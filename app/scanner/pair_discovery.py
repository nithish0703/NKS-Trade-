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
from typing import Awaitable, Callable, Dict, List, Optional, Sequence, Set

from app.config.pairs import BTC_SYMBOL, DEFAULT_PAIRS
from app.data.provider_base import MarketDataProvider
from app.data.ticker_snapshot import TickerSnapshot

ClockProvider = Callable[[], float]


def _default_clock() -> float:
    return asyncio.get_event_loop().time()


class PairWarmUpTracker:
    """
    Gates newly discovered pairs behind a background warm-up fetch
    before they are exposed to the scanner, so a brand-new symbol never
    appears in the scan rotation until its first full candle-history
    fetch has actually succeeded.

    A symbol's readiness is tracked here, independent of any single
    `DynamicPairDiscoveryService` refresh: once warm, it stays warm
    across subsequent refreshes and is never re-warmed. A failed
    warm-up is logged and retried in the background (driven by
    `reconcile()` being called again on the next discovery refresh)
    until it succeeds or the symbol stops qualifying, at which point
    `reconcile()` drops it from tracking entirely.

    Uses one background asyncio.Task per in-flight warm-up, tracked in
    `_pending_tasks` so a symbol is never warmed twice concurrently.
    All shared state (`_ready`, `_pending_tasks`) is only ever mutated
    from within the event loop's single thread, consistent with the
    rest of this module's asyncio-only concurrency model -- no lock is
    needed because `reconcile()` and warm-up completion callbacks never
    await between a state check and its corresponding mutation.
    """

    def __init__(
        self,
        *,
        market_data_provider: MarketDataProvider,
        always_ready: Sequence[str] = (BTC_SYMBOL,),
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._market_data_provider = market_data_provider
        self._logger = logger or logging.getLogger(__name__)

        self._always_ready: Set[str] = set(always_ready)
        self._ready: Set[str] = set(always_ready)
        self._pending_tasks: Dict[str, "asyncio.Task[None]"] = {}

    def reconcile(self, candidate_pairs: Sequence[str]) -> List[str]:
        """
        Reconcile the latest ranked candidate list against known-ready
        and in-flight-warm-up symbols:
          - Symbols already ready (or always-ready, e.g. BTC) are kept.
          - Symbols newly seen (not ready, not already warming) get a
            background warm-up task started, and are excluded from the
            returned list until that task succeeds.
          - Symbols previously tracked (ready or pending) that no
            longer appear in `candidate_pairs` are dropped from
            tracking entirely -- including cancelling any in-flight
            warm-up task for them.

        Returns the subset of `candidate_pairs` that is ready right
        now, preserving `candidate_pairs`' order. Never raises and
        never blocks on any warm-up fetch.
        """
        candidate_set = set(candidate_pairs)

        self._drop_stale_tracking(candidate_set)

        for symbol in candidate_pairs:
            if symbol in self._ready or symbol in self._pending_tasks:
                continue
            self._start_warm_up(symbol)

        return [symbol for symbol in candidate_pairs if symbol in self._ready]

    def _drop_stale_tracking(self, candidate_set: Set[str]) -> None:
        stale_ready = [
            symbol
            for symbol in self._ready
            if symbol not in candidate_set and symbol not in self._always_ready
        ]
        for symbol in stale_ready:
            self._ready.discard(symbol)

        stale_pending = [symbol for symbol in self._pending_tasks if symbol not in candidate_set]
        for symbol in stale_pending:
            task = self._pending_tasks.pop(symbol)
            task.cancel()

    def _start_warm_up(self, symbol: str) -> None:
        task = asyncio.ensure_future(self._warm_up(symbol))
        self._pending_tasks[symbol] = task

    async def _warm_up(self, symbol: str) -> None:
        try:
            await self._market_data_provider.fetch_symbol_market_data(symbol)
        except Exception as exc:  # noqa: BLE001 - a warm-up failure must never crash the scanner
            self._logger.warning(
                "Warm-up fetch failed for newly discovered pair %s: %s. "
                "Will retry on the next discovery refresh.",
                symbol,
                exc,
            )
            self._pending_tasks.pop(symbol, None)
            return

        self._pending_tasks.pop(symbol, None)
        self._ready.add(symbol)
        self._logger.info("Warm-up succeeded for newly discovered pair %s; now scan-eligible.", symbol)

    @property
    def ready_symbols(self) -> Set[str]:
        return set(self._ready)

    @property
    def pending_symbols(self) -> Set[str]:
        return set(self._pending_tasks.keys())


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
        warm_up_tracker: Optional[PairWarmUpTracker] = None,
        clock: ClockProvider = _default_clock,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._market_data_provider = market_data_provider
        self._minimum_open_interest_usdt = minimum_open_interest_usdt
        self._minimum_turnover_24h_usdt = minimum_turnover_24h_usdt
        self._refresh_interval_seconds = refresh_interval_seconds
        self._maximum_pairs = maximum_pairs
        self._on_refresh = on_refresh
        self._warm_up_tracker = warm_up_tracker
        self._clock = clock
        self._logger = logger or logging.getLogger(__name__)

        self._current_pairs: List[str] = list(DEFAULT_PAIRS)
        self._last_ranked_pairs: Optional[List[str]] = None
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
        `DEFAULT_PAIRS` fallback. After that, when a warm-up tracker is
        configured, this re-derives the ready subset of the most
        recently ranked candidate list on every call -- rather than
        only the snapshot taken at the last `refresh_once()` -- so a
        symbol that finishes its background warm-up mid-interval
        appears immediately instead of waiting for the next discovery
        refresh (which may be up to `refresh_interval_seconds` away).
        """
        if self._warm_up_tracker is not None and self._last_ranked_pairs is not None:
            ready = self._warm_up_tracker.ready_symbols
            return [symbol for symbol in self._last_ranked_pairs if symbol in ready]
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

        if self._warm_up_tracker is not None:
            # reconcile() is synchronous and never blocks on a warm-up
            # fetch: newly seen symbols get a background warm-up task
            # started and are excluded from the returned list until
            # that task succeeds; already-ready symbols (including BTC)
            # pass through unchanged. Symbols warmed *after* this call
            # (mid-interval, before the next refresh_once()) still show
            # up immediately via get_current_pairs() re-deriving from
            # _last_ranked_pairs + the tracker's live ready set.
            ready_pairs = self._warm_up_tracker.reconcile(ranked_pairs)
            self._last_ranked_pairs = ranked_pairs
        else:
            ready_pairs = ranked_pairs

        self._current_pairs = ready_pairs
        self._has_refreshed_successfully = True
        self._last_error = None
        self._last_refresh_pair_count = len(ready_pairs)
        self._logger.info(
            "Dynamic pair discovery refreshed: %d of %d qualifying pairs are scan-ready.",
            len(ready_pairs),
            len(ranked_pairs),
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
