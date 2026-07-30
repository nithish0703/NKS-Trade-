"""
Dynamic Liquidity + Open Interest based coin discovery.

Replaces the static configured-pair list with every USDT perpetual
futures pair that clears a configurable minimum Open Interest and
minimum 24h turnover, refreshed automatically on a background timer.
This module only discovers and filters *which* symbols the scanner
should scan; it never fetches candles, calculates indicators, or makes
any trading decision -- discovered pairs flow through the exact same
strategy pipeline as any statically configured pair.
"""

import asyncio
import logging
from typing import Awaitable, Callable, List, Optional, Sequence

from app.config.pairs import BTC_SYMBOL, DEFAULT_PAIRS
from app.data.provider_base import MarketDataProvider
from app.data.ticker_snapshot import TickerSnapshot

ClockProvider = Callable[[], float]


def _default_clock() -> float:
    return asyncio.get_event_loop().time()


def filter_and_rank_pairs(
    tickers: Sequence[TickerSnapshot],
    *,
    minimum_open_interest_usdt: float,
    minimum_turnover_24h_usdt: float,
    maximum_pairs: Optional[int] = None,
) -> List[str]:
    """
    Filter ticker snapshots down to symbols whose Open Interest AND 24h
    turnover both meet their configured minimums, ranked by turnover
    (highest first), and optionally capped to the first `maximum_pairs`.

    A ticker with a missing (None) Open Interest or turnover value never
    passes the filter -- missing data is never treated as if it were
    zero or fabricated as passing.

    `maximum_pairs=None` (the default) applies no cap: every symbol that
    passes both conditions is included, however many that is.
    """
    passing = [
        ticker
        for ticker in tickers
        if ticker.open_interest_usdt is not None
        and ticker.turnover_24h_usdt is not None
        and ticker.open_interest_usdt >= minimum_open_interest_usdt
        and ticker.turnover_24h_usdt >= minimum_turnover_24h_usdt
    ]
    passing.sort(key=lambda t: t.turnover_24h_usdt, reverse=True)

    ranked_symbols = [ticker.symbol for ticker in passing]
    if maximum_pairs is not None:
        ranked_symbols = ranked_symbols[:maximum_pairs]
    return ranked_symbols


class DynamicPairDiscoveryService:
    """
    Periodically refreshes the dynamic Liquidity+OI-filtered pair list
    in the background, and exposes the current list synchronously via
    `get_current_pairs()` for use as a `configured_pair_provider`.

    Refresh interval defaults to 15 minutes (see
    `app.config.thresholds.PAIR_DISCOVERY_INTERVAL_SECONDS`): Bybit's
    bulk tickers endpoint returns Open Interest and turnover for every
    USDT perpetual in a single call, so there is no meaningful
    rate-limit cost to refreshing this often.

    A failed or empty refresh always keeps the previous list rather
    than ever leaving the scanner with an empty scan universe. Before
    the first successful refresh, `get_current_pairs()` falls back to
    the static `DEFAULT_PAIRS` list, so the scanner always has
    something to work with immediately at startup.
    """

    def __init__(
        self,
        *,
        market_data_provider: MarketDataProvider,
        minimum_open_interest_usdt: float,
        minimum_turnover_24h_usdt: float,
        refresh_interval_seconds: float,
        maximum_pairs: Optional[int] = None,
        on_refresh: Optional[Callable[[bool, List[str]], Awaitable[None]]] = None,
        clock: ClockProvider = _default_clock,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._market_data_provider = market_data_provider
        self._minimum_open_interest_usdt = minimum_open_interest_usdt
        self._minimum_turnover_24h_usdt = minimum_turnover_24h_usdt
        self._refresh_interval_seconds = refresh_interval_seconds
        self._maximum_pairs = maximum_pairs
        self._on_refresh = on_refresh
        self._clock = clock
        self._logger = logger or logging.getLogger(__name__)

        self._current_pairs: List[str] = list(DEFAULT_PAIRS)
        self._has_refreshed_successfully = False
        self._last_error: Optional[str] = None
        self._last_refresh_pair_count: Optional[int] = None
        self._running = False
        self._shutdown_event = asyncio.Event()

    def get_current_pairs(self) -> List[str]:
        """
        Return the current dynamic pair list (a copy), suitable for use
        as a `MultiPairScanScheduler` `configured_pair_provider`.

        Before the first successful refresh this returns the static
        `DEFAULT_PAIRS` fallback; after that it returns whatever the
        most recent successful refresh produced.
        """
        return list(self._current_pairs)

    async def refresh_once(self) -> bool:
        """
        Perform a single refresh attempt.

        Returns True if the refresh produced a non-empty pair list and
        the current list was updated; False if the fetch failed, was
        empty, or was otherwise unusable -- in which case the previous
        list is left untouched. Never raises.
        """
        try:
            tickers = await self._market_data_provider.fetch_all_linear_tickers()
        except Exception as exc:  # noqa: BLE001 - a discovery failure must never crash the scanner
            self._last_error = str(exc)
            self._logger.warning("Dynamic pair discovery refresh failed: %s", exc)
            await self._notify_refresh(updated=False)
            return False

        ranked_pairs = filter_and_rank_pairs(
            tickers,
            minimum_open_interest_usdt=self._minimum_open_interest_usdt,
            minimum_turnover_24h_usdt=self._minimum_turnover_24h_usdt,
            maximum_pairs=self._maximum_pairs,
        )

        if not ranked_pairs:
            self._last_error = "Dynamic pair discovery returned no qualifying pairs."
            self._logger.warning(
                "Dynamic pair discovery refresh returned no qualifying pairs; "
                "keeping the previous pair list."
            )
            await self._notify_refresh(updated=False)
            return False

        if BTC_SYMBOL not in ranked_pairs:
            ranked_pairs.insert(0, BTC_SYMBOL)

        self._current_pairs = ranked_pairs
        self._has_refreshed_successfully = True
        self._last_error = None
        self._last_refresh_pair_count = len(ranked_pairs)
        self._logger.info(
            "Dynamic pair discovery refreshed: %d pairs qualify.", len(ranked_pairs)
        )
        await self._notify_refresh(updated=True)
        return True

    async def _notify_refresh(self, *, updated: bool) -> None:
        if self._on_refresh is None:
            return
        try:
            await self._on_refresh(updated, self.get_current_pairs())
        except Exception as exc:  # noqa: BLE001 - an observer failure must never affect discovery
            self._logger.warning("Dynamic pair discovery refresh observer failed: %s", exc)

    async def run_forever(self) -> None:
        """
        Refresh immediately, then continue refreshing on the configured
        interval until shutdown is requested. The first refresh happens
        immediately on startup rather than waiting for the first
        interval to elapse.
        """
        self._running = True
        try:
            while not self._shutdown_event.is_set():
                cycle_start_clock = self._clock()

                await self.refresh_once()

                if self._shutdown_event.is_set():
                    break

                elapsed_seconds = self._clock() - cycle_start_clock
                remaining_seconds = max(0.0, self._refresh_interval_seconds - elapsed_seconds)

                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(), timeout=remaining_seconds
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            self._running = False

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    @property
    def has_refreshed_successfully(self) -> bool:
        return self._has_refreshed_successfully

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def last_refresh_pair_count(self) -> Optional[int]:
        return self._last_refresh_pair_count
