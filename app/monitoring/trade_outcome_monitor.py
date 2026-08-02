"""
Background monitor that periodically re-checks every dashboard-ACTIVE
signal's current exchange price against its stored take_profit/
stop_loss, and records a WIN/LOSS outcome once one is touched.

This is purely dashboard/analytics bookkeeping for signals the user
explicitly moved to "Active" via the dashboard "Trade" action:

  - It never places, modifies, or cancels an exchange order.
  - It never reads or writes anything used by the strategy pipeline,
    confidence scoring, risk management, or Telegram notifications.
  - It never mutates a signal's original trading fields (entry_price,
    stop_loss, take_profit, etc.) -- it only sets the dashboard-only
    outcome/exit_price/closed_at_utc columns and dashboard_status.

A signal is closed the first time price touches either level; if a
fast move touches both within one polling interval, that is
structurally impossible to observe as ambiguous here because for a
BUY, take_profit is always above entry and stop_loss always below (and
the reverse for SELL) -- a single ticker price can only ever satisfy
one side at a time.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from app.data.provider_base import MarketDataProvider
from app.models.signal import Direction, Signal
from app.storage.signal_repository import (
    DASHBOARD_STATUS_ACTIVE,
    DASHBOARD_STATUS_CLOSED_LOSS,
    DASHBOARD_STATUS_CLOSED_WIN,
    SignalNotFoundError,
    SignalRepository,
    SignalWithStatus,
)

logger = logging.getLogger(__name__)

_ACTIVE_SIGNALS_CHECK_LIMIT = 10_000

OnSignalClosed = Callable[[SignalWithStatus], Awaitable[None]]


def evaluate_outcome(signal: Signal, current_price: float) -> Optional[str]:
    """
    Return DASHBOARD_STATUS_CLOSED_WIN / DASHBOARD_STATUS_CLOSED_LOSS if
    `current_price` has touched `signal`'s take_profit or stop_loss,
    else None (still open). Pure function; no I/O, no side effects.
    """
    if signal.direction == Direction.BUY:
        if current_price >= signal.take_profit:
            return DASHBOARD_STATUS_CLOSED_WIN
        if current_price <= signal.stop_loss:
            return DASHBOARD_STATUS_CLOSED_LOSS
        return None

    # Direction.SELL
    if current_price <= signal.take_profit:
        return DASHBOARD_STATUS_CLOSED_WIN
    if current_price >= signal.stop_loss:
        return DASHBOARD_STATUS_CLOSED_LOSS
    return None


class TradeOutcomeMonitor:
    """
    Polls dashboard-ACTIVE signals on a fixed interval and closes each
    one out (WIN/LOSS) the first time its take_profit or stop_loss is
    touched by the live ticker price.
    """

    def __init__(
        self,
        *,
        signal_repository: SignalRepository,
        market_data_provider: MarketDataProvider,
        interval_seconds: float,
        on_signal_closed: Optional[OnSignalClosed] = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive.")
        self._signal_repository = signal_repository
        self._market_data_provider = market_data_provider
        self._interval_seconds = interval_seconds
        self._on_signal_closed = on_signal_closed
        self._shutdown_event = asyncio.Event()

    def request_shutdown(self) -> None:
        """Signal run_forever() to stop after the current check completes."""
        self._shutdown_event.set()

    async def run_forever(self) -> None:
        """
        Run checks on a fixed interval until request_shutdown() is
        called. A failure while checking one signal (e.g. a transient
        ticker-fetch error) is logged and never stops the loop or
        affects any other signal.
        """
        while not self._shutdown_event.is_set():
            try:
                await self.check_active_signals()
            except Exception as exc:  # noqa: BLE001 - keep monitoring alive
                logger.error("Trade outcome monitor check cycle failed: %s", exc)

            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(), timeout=self._interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def check_active_signals(self) -> None:
        """Check every currently-ACTIVE signal once against its live price."""
        active_results = await self._signal_repository.list_recent_with_status(
            limit=_ACTIVE_SIGNALS_CHECK_LIMIT, dashboard_status=DASHBOARD_STATUS_ACTIVE
        )

        for result in active_results:
            await self._check_one_signal(result.signal)

    async def _check_one_signal(self, signal: Signal) -> None:
        try:
            current_price = await self._market_data_provider.fetch_ticker_price(signal.coin)
        except Exception as exc:  # noqa: BLE001 - one bad fetch must not block others
            logger.warning(
                "Trade outcome monitor: failed to fetch ticker price for %s: %s", signal.coin, exc
            )
            return

        if current_price is None or current_price <= 0:
            return

        outcome = evaluate_outcome(signal, current_price)
        if outcome is None:
            return

        try:
            closed = await self._signal_repository.close_signal(
                signal.trade_id,
                outcome=outcome,
                exit_price=current_price,
                closed_at_utc=datetime.now(timezone.utc),
            )
        except SignalNotFoundError:
            # Signal was removed/changed between the list and the close
            # call; nothing more to do for it this cycle.
            return

        logger.info(
            "Trade outcome monitor: %s %s closed as %s at price %s (tp=%s, sl=%s).",
            signal.coin,
            signal.direction.value,
            outcome,
            current_price,
            signal.take_profit,
            signal.stop_loss,
        )

        if self._on_signal_closed is not None:
            await self._on_signal_closed(closed)
