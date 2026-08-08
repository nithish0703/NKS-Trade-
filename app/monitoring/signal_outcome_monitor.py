"""
Single background monitor that resolves an outcome (WIN, LOSS, TIMEOUT,
or UNRESOLVED) for every CONFIRMED signal not yet closed, via ONE bulk
ticker-price fetch per cycle -- never a per-symbol request loop (see
MarketDataProvider.fetch_all_ticker_prices; a per-symbol loop breaks
down once DYNAMIC_PAIR_DISCOVERY_ENABLED=true widens the scanned
universe well past a handful of symbols).

This is the SOLE source of truth for `passive_outcome`/
passive_exit_price/passive_closed_at_utc (used by the Phase 1
performance report for every CONFIRMED signal, independent of the
dashboard "Trade" button) and, in the same pass, mirrors the outcome
onto dashboard_status/outcome/exit_price/closed_at_utc whenever a
signal happens to also be dashboard-ACTIVE at close time -- there is
only ever one ticker-polling schedule, never two independently
drifting ones (see app.storage.signal_repository.SignalRepository
.close_passive). It runs standalone from both `python main.py scan`
(app.scanner.scanner_service) and the dashboard API
(app.api.main.lifespan); an optional `lease_guard` (see
app.storage.monitor_lease.MonitorLeaseGuard) ensures only one of those
two processes does the actual work in any given cycle when both are up
against the same database.

Known limitation: this checks a single current price per cycle, not an
OHLC candle range. If price wicks through both take_profit and
stop_loss between two polls, only whichever side the price sits on at
the next check is observed -- the same-candle SL-over-TP tie-break
below only protects against the (structurally impossible for a
well-formed signal) case of a single price satisfying both conditions
at once. Phase 4's candle-based backtest is the authoritative source
for genuine same-candle resolution; this live monitor is an
approximation of it, not a replacement.

A signal whose symbol has no price at all in a cycle's bulk fetch is
left open and retried, never force-closed on the spot -- a single
missing price must never be recorded as a fabricated 0R break-even.
Only after MAX_CONSECUTIVE_MISSING_PRICE_CYCLES consecutive misses is
it force-closed as UNRESOLVED, with passive_exit_price left NULL; the
Phase 1 performance report excludes UNRESOLVED from its win-rate/
expectancy math entirely (see app.analytics.performance_report).

  - It never places, modifies, or cancels an exchange order.
  - It never reads or writes anything used by the strategy pipeline,
    confidence scoring, or risk management.
  - It never mutates a signal's original trading fields.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from app.config.thresholds import (
    MAX_CONSECUTIVE_MISSING_PRICE_CYCLES,
    MAX_TRACKED_SIGNAL_DURATION_CANDLES,
)
from app.config.timeframes import ENTRY_TIMEFRAME, get_timeframe_duration_seconds
from app.data.provider_base import MarketDataProvider
from app.models.signal import Direction, Signal
from app.storage.monitor_lease import MonitorLeaseGuard
from app.storage.signal_repository import (
    PASSIVE_OUTCOME_LOSS,
    PASSIVE_OUTCOME_TIMEOUT,
    PASSIVE_OUTCOME_UNRESOLVED,
    PASSIVE_OUTCOME_WIN,
    SignalNotFoundError,
    SignalOutcomeResult,
    SignalRepository,
)

logger = logging.getLogger(__name__)

_NOT_CLOSED_SIGNALS_CHECK_LIMIT = 10_000

_MAX_TRACKED_SIGNAL_DURATION_SECONDS = (
    MAX_TRACKED_SIGNAL_DURATION_CANDLES * get_timeframe_duration_seconds(ENTRY_TIMEFRAME)
)

OnSignalClosed = Callable[[SignalOutcomeResult], Awaitable[None]]
ClockProvider = Callable[[], datetime]


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def evaluate_outcome(signal: Signal, current_price: float) -> Optional[str]:
    """
    Return PASSIVE_OUTCOME_LOSS / PASSIVE_OUTCOME_WIN if `current_price`
    has touched `signal`'s stop_loss or take_profit, else None (still
    open). Pure function; no I/O, no side effects.

    Checks the stop-loss side FIRST, deliberately: for a well-formed
    signal take_profit and stop_loss sit on opposite sides of
    entry_price, so a single current_price can only ever satisfy one of
    the two conditions below and this ordering never changes the
    outcome in practice. It exists so that if that invariant were ever
    violated, the conservative (loss) outcome wins -- matching the
    Phase 4 backtest's convention that an ambiguous same-candle
    SL+TP touch resolves to SL, never the more favourable TP.
    """
    if signal.direction == Direction.BUY:
        if current_price <= signal.stop_loss:
            return PASSIVE_OUTCOME_LOSS
        if current_price >= signal.take_profit:
            return PASSIVE_OUTCOME_WIN
        return None

    # Direction.SELL
    if current_price >= signal.stop_loss:
        return PASSIVE_OUTCOME_LOSS
    if current_price <= signal.take_profit:
        return PASSIVE_OUTCOME_WIN
    return None


def _is_timed_out(signal: Signal, now: datetime) -> bool:
    elapsed_seconds = (now - signal.detection_time_utc).total_seconds()
    return elapsed_seconds >= _MAX_TRACKED_SIGNAL_DURATION_SECONDS


class SignalOutcomeMonitor:
    """
    Polls every not-yet-closed CONFIRMED signal on a fixed interval,
    via one bulk ticker-price fetch per cycle, and closes each one out
    (WIN/LOSS/TIMEOUT) the first time its take_profit or stop_loss is
    touched -- or after MAX_TRACKED_SIGNAL_DURATION_CANDLES with
    neither touched.
    """

    def __init__(
        self,
        *,
        signal_repository: SignalRepository,
        market_data_provider: MarketDataProvider,
        interval_seconds: float,
        on_signal_closed: Optional[OnSignalClosed] = None,
        clock: ClockProvider = _default_clock,
        lease_guard: Optional[MonitorLeaseGuard] = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive.")
        self._signal_repository = signal_repository
        self._market_data_provider = market_data_provider
        self._interval_seconds = interval_seconds
        self._on_signal_closed = on_signal_closed
        self._clock = clock
        self._lease_guard = lease_guard
        self._shutdown_event = asyncio.Event()
        # trade_id -> count of consecutive cycles with no price observed
        # for that signal's symbol; pruned to only currently open
        # signals at the start of every cycle.
        self._consecutive_missing_price_cycles: dict[str, int] = {}

    def request_shutdown(self) -> None:
        """Signal run_forever() to stop after the current check completes."""
        self._shutdown_event.set()

    async def run_forever(self) -> None:
        """
        Run checks on a fixed interval until request_shutdown() is
        called. A failure during one check cycle is logged and never
        stops the loop.
        """
        while not self._shutdown_event.is_set():
            try:
                await self.check_open_signals()
            except Exception as exc:  # noqa: BLE001 - keep monitoring alive
                logger.error("Signal outcome monitor check cycle failed: %s", exc)

            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(), timeout=self._interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def check_open_signals(self) -> None:
        """
        Check every not-yet-closed signal once, via a single bulk
        ticker-price fetch shared across all of them -- never one
        ticker request per symbol. A no-op (no DB or ticker call at
        all) when `lease_guard` is set and another process currently
        holds the lease.
        """
        now = self._clock()

        if self._lease_guard is not None and not await self._lease_guard.try_acquire(now):
            # INFO, not debug: an unattended run must be able to tell
            # "another process legitimately holds this lease" apart
            # from "this monitor died silently" without changing the
            # log level just to check.
            logger.info(
                "Signal outcome monitor: lease held by another process this cycle; skipping."
            )
            return

        open_signals = await self._signal_repository.list_not_passively_closed(
            limit=_NOT_CLOSED_SIGNALS_CHECK_LIMIT
        )
        logger.info(
            "Signal outcome monitor: lease acquired; %d open signal(s) to check this cycle.",
            len(open_signals),
        )
        if not open_signals:
            self._consecutive_missing_price_cycles.clear()
            return

        open_trade_ids = {signal.trade_id for signal in open_signals}
        self._consecutive_missing_price_cycles = {
            trade_id: count
            for trade_id, count in self._consecutive_missing_price_cycles.items()
            if trade_id in open_trade_ids
        }

        try:
            prices = await self._market_data_provider.fetch_all_ticker_prices()
        except Exception as exc:  # noqa: BLE001 - a bad fetch must not crash the monitor
            logger.warning("Signal outcome monitor: bulk ticker-price fetch failed: %s", exc)
            return

        logger.info(
            "Signal outcome monitor: bulk ticker fetch returned %d price(s).", len(prices)
        )

        for signal in open_signals:
            await self._check_one_signal(signal, prices, now)

    async def _check_one_signal(self, signal: Signal, prices: dict[str, float], now: datetime) -> None:
        current_price = prices.get(signal.coin)
        has_price = current_price is not None and current_price > 0

        if not has_price:
            missed = self._consecutive_missing_price_cycles.get(signal.trade_id, 0) + 1
            if missed < MAX_CONSECUTIVE_MISSING_PRICE_CYCLES:
                self._consecutive_missing_price_cycles[signal.trade_id] = missed
                return
            self._consecutive_missing_price_cycles.pop(signal.trade_id, None)
            await self._close_signal(
                signal, outcome=PASSIVE_OUTCOME_UNRESOLVED, exit_price=None, now=now
            )
            return

        self._consecutive_missing_price_cycles.pop(signal.trade_id, None)

        outcome = evaluate_outcome(signal, current_price)
        if outcome is not None:
            await self._close_signal(signal, outcome=outcome, exit_price=current_price, now=now)
            return

        if _is_timed_out(signal, now):
            await self._close_signal(
                signal, outcome=PASSIVE_OUTCOME_TIMEOUT, exit_price=current_price, now=now
            )

    async def _close_signal(
        self, signal: Signal, *, outcome: str, exit_price: Optional[float], now: datetime
    ) -> None:
        try:
            result = await self._signal_repository.close_passive(
                signal.trade_id,
                outcome=outcome,
                exit_price=exit_price,
                closed_at_utc=now,
            )
        except SignalNotFoundError:
            # Signal was removed between the list and the close call;
            # nothing more to do for it this cycle.
            return

        logger.info(
            "Signal outcome monitor: %s %s closed as %s (source=%s) at price %s (tp=%s, sl=%s).",
            signal.coin,
            signal.direction.value,
            outcome,
            result.outcome_source,
            exit_price,
            signal.take_profit,
            signal.stop_loss,
        )

        if self._on_signal_closed is not None:
            await self._on_signal_closed(result)
