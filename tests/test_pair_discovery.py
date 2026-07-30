"""
Tests for app.scanner.pair_discovery: dynamic Liquidity + Open Interest
based coin discovery filtering/ranking, its background refresh loop, and
the newly-discovered-pair warm-up gate.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.config.pairs import BTC_SYMBOL, DEFAULT_PAIRS
from app.data.ticker_snapshot import TickerSnapshot
from app.scanner.pair_discovery import (
    DynamicPairDiscoveryService,
    PairWarmUpTracker,
    filter_and_rank_pairs,
)

pytestmark = pytest.mark.asyncio


def _ticker(symbol: str, oi: float, turnover: float) -> TickerSnapshot:
    return TickerSnapshot(symbol=symbol, open_interest_usdt=oi, turnover_24h_usdt=turnover)


class _FakeWarmUpMarketDataProvider:
    """
    A controllable fake for warm-up fetches: per-symbol outcomes can be
    set to succeed, raise, or hang (never resolve) so tests can exercise
    each path deterministically without real network I/O.
    """

    def __init__(self) -> None:
        self.failing_symbols: set[str] = set()
        self.hanging_symbols: set[str] = set()
        self.fetch_calls: list[str] = []
        self._hang_events: dict[str, asyncio.Event] = {}

    async def fetch_symbol_market_data(self, symbol: str) -> dict:
        self.fetch_calls.append(symbol)
        if symbol in self.hanging_symbols:
            event = self._hang_events.setdefault(symbol, asyncio.Event())
            await event.wait()
        if symbol in self.failing_symbols:
            raise RuntimeError(f"transient failure fetching {symbol}")
        return {"15m": [], "1h": [], "4h": []}

    def release_hang(self, symbol: str) -> None:
        self._hang_events.setdefault(symbol, asyncio.Event()).set()


class TestFilterAndRankPairs:
    def test_passes_both_conditions(self):
        tickers = [_ticker("BTC-USDT", 10_000_000, 20_000_000)]
        result = filter_and_rank_pairs(
            tickers, minimum_open_interest_usdt=5_000_000, minimum_turnover_24h_usdt=10_000_000
        )
        assert result == ["BTC-USDT"]

    def test_fails_open_interest_only(self):
        tickers = [_ticker("ETH-USDT", 1_000_000, 20_000_000)]
        result = filter_and_rank_pairs(
            tickers, minimum_open_interest_usdt=5_000_000, minimum_turnover_24h_usdt=10_000_000
        )
        assert result == []

    def test_fails_turnover_only(self):
        tickers = [_ticker("ETH-USDT", 10_000_000, 1_000_000)]
        result = filter_and_rank_pairs(
            tickers, minimum_open_interest_usdt=5_000_000, minimum_turnover_24h_usdt=10_000_000
        )
        assert result == []

    def test_no_coins_pass_returns_empty_list(self):
        tickers = [
            _ticker("ETH-USDT", 1_000, 1_000),
            _ticker("SOL-USDT", 2_000, 2_000),
        ]
        result = filter_and_rank_pairs(
            tickers, minimum_open_interest_usdt=5_000_000, minimum_turnover_24h_usdt=10_000_000
        )
        assert result == []

    def test_empty_input_returns_empty_list(self):
        result = filter_and_rank_pairs(
            [], minimum_open_interest_usdt=5_000_000, minimum_turnover_24h_usdt=10_000_000
        )
        assert result == []

    def test_missing_open_interest_field_excluded(self):
        tickers = [TickerSnapshot(symbol="ETH-USDT", open_interest_usdt=None, turnover_24h_usdt=20_000_000)]
        result = filter_and_rank_pairs(
            tickers, minimum_open_interest_usdt=5_000_000, minimum_turnover_24h_usdt=10_000_000
        )
        assert result == []

    def test_missing_turnover_field_excluded(self):
        tickers = [TickerSnapshot(symbol="ETH-USDT", open_interest_usdt=20_000_000, turnover_24h_usdt=None)]
        result = filter_and_rank_pairs(
            tickers, minimum_open_interest_usdt=5_000_000, minimum_turnover_24h_usdt=10_000_000
        )
        assert result == []

    def test_missing_data_never_treated_as_zero_or_passing(self):
        # Both fields missing: still excluded, not fabricated as passing.
        tickers = [TickerSnapshot(symbol="ETH-USDT", open_interest_usdt=None, turnover_24h_usdt=None)]
        result = filter_and_rank_pairs(
            tickers, minimum_open_interest_usdt=0.0, minimum_turnover_24h_usdt=0.0
        )
        assert result == []

    def test_ranked_by_turnover_descending(self):
        tickers = [
            _ticker("ETH-USDT", 10_000_000, 15_000_000),
            _ticker("BTC-USDT", 10_000_000, 50_000_000),
            _ticker("SOL-USDT", 10_000_000, 30_000_000),
        ]
        result = filter_and_rank_pairs(
            tickers, minimum_open_interest_usdt=5_000_000, minimum_turnover_24h_usdt=10_000_000
        )
        assert result == ["BTC-USDT", "SOL-USDT", "ETH-USDT"]

    def test_no_cap_includes_every_qualifying_coin(self):
        tickers = [_ticker(f"COIN{i}-USDT", 10_000_000, 10_000_000 + i) for i in range(37)]
        result = filter_and_rank_pairs(
            tickers,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            maximum_pairs=None,
        )
        assert len(result) == 37

    def test_maximum_pairs_cap_applied_to_highest_turnover(self):
        tickers = [_ticker(f"COIN{i}-USDT", 10_000_000, 10_000_000 + i) for i in range(10)]
        result = filter_and_rank_pairs(
            tickers,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            maximum_pairs=3,
        )
        assert result == ["COIN9-USDT", "COIN8-USDT", "COIN7-USDT"]

    def test_boundary_values_are_inclusive(self):
        tickers = [_ticker("ETH-USDT", 5_000_000, 10_000_000)]
        result = filter_and_rank_pairs(
            tickers, minimum_open_interest_usdt=5_000_000, minimum_turnover_24h_usdt=10_000_000
        )
        assert result == ["ETH-USDT"]


def _provider(tickers=None, raises: Exception = None) -> AsyncMock:
    provider = AsyncMock()
    if raises is not None:
        provider.fetch_all_linear_tickers.side_effect = raises
    else:
        provider.fetch_all_linear_tickers.return_value = tickers or []
    return provider


class TestDynamicPairDiscoveryServiceRefreshOnce:
    async def test_successful_refresh_updates_pair_list(self):
        provider = _provider([_ticker("SOL-USDT", 10_000_000, 20_000_000)])
        service = DynamicPairDiscoveryService(
            market_data_provider=provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
        )
        updated = await service.refresh_once()
        assert updated is True
        assert "SOL-USDT" in service.get_current_pairs()
        assert service.has_refreshed_successfully is True
        assert service.last_error is None

    async def test_btc_always_included(self):
        provider = _provider([_ticker("SOL-USDT", 10_000_000, 20_000_000)])
        service = DynamicPairDiscoveryService(
            market_data_provider=provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
        )
        await service.refresh_once()
        assert BTC_SYMBOL in service.get_current_pairs()

    async def test_before_first_refresh_falls_back_to_static_default_list(self):
        provider = _provider([])
        service = DynamicPairDiscoveryService(
            market_data_provider=provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
        )
        assert service.get_current_pairs() == list(DEFAULT_PAIRS)
        assert service.has_refreshed_successfully is False

    async def test_failed_refresh_keeps_old_list(self):
        provider = _provider([_ticker("SOL-USDT", 10_000_000, 20_000_000)])
        service = DynamicPairDiscoveryService(
            market_data_provider=provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
        )
        await service.refresh_once()
        pairs_after_success = service.get_current_pairs()

        provider.fetch_all_linear_tickers.side_effect = RuntimeError("network error")
        updated = await service.refresh_once()

        assert updated is False
        assert service.get_current_pairs() == pairs_after_success
        assert service.last_error == "network error"

    async def test_api_request_failure_does_not_raise(self):
        provider = _provider(raises=RuntimeError("boom"))
        service = DynamicPairDiscoveryService(
            market_data_provider=provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
        )
        updated = await service.refresh_once()
        assert updated is False
        assert service.get_current_pairs() == list(DEFAULT_PAIRS)

    async def test_empty_result_keeps_old_list(self):
        provider = _provider([_ticker("SOL-USDT", 10_000_000, 20_000_000)])
        service = DynamicPairDiscoveryService(
            market_data_provider=provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
        )
        await service.refresh_once()
        pairs_after_success = service.get_current_pairs()

        provider.fetch_all_linear_tickers.return_value = []
        updated = await service.refresh_once()

        assert updated is False
        assert service.get_current_pairs() == pairs_after_success

    async def test_no_qualifying_coins_keeps_old_list_not_empty(self):
        provider = _provider([_ticker("SOL-USDT", 1, 1)])  # fails both thresholds
        service = DynamicPairDiscoveryService(
            market_data_provider=provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
        )
        updated = await service.refresh_once()
        assert updated is False
        assert service.get_current_pairs() == list(DEFAULT_PAIRS)

    async def test_on_refresh_callback_invoked_on_success(self):
        provider = _provider([_ticker("SOL-USDT", 10_000_000, 20_000_000)])
        on_refresh = AsyncMock()
        service = DynamicPairDiscoveryService(
            market_data_provider=provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
            on_refresh=on_refresh,
        )
        await service.refresh_once()
        on_refresh.assert_awaited_once()
        args = on_refresh.await_args.args
        assert args[0] is True
        assert "SOL-USDT" in args[1]

    async def test_on_refresh_callback_invoked_on_failure(self):
        provider = _provider(raises=RuntimeError("boom"))
        on_refresh = AsyncMock()
        service = DynamicPairDiscoveryService(
            market_data_provider=provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
            on_refresh=on_refresh,
        )
        await service.refresh_once()
        on_refresh.assert_awaited_once()
        assert on_refresh.await_args.args[0] is False

    async def test_on_refresh_exception_does_not_propagate(self):
        provider = _provider([_ticker("SOL-USDT", 10_000_000, 20_000_000)])
        on_refresh = AsyncMock(side_effect=RuntimeError("observer broke"))
        service = DynamicPairDiscoveryService(
            market_data_provider=provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
            on_refresh=on_refresh,
        )
        updated = await service.refresh_once()
        assert updated is True  # the refresh itself still succeeded


class TestDynamicPairDiscoveryServiceRunForever:
    async def test_first_refresh_happens_immediately_not_after_first_interval(self):
        provider = _provider([_ticker("SOL-USDT", 10_000_000, 20_000_000)])
        service = DynamicPairDiscoveryService(
            market_data_provider=provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=3600,  # long interval; must not need to wait for it
        )

        task = asyncio.create_task(service.run_forever())
        await asyncio.sleep(0.05)
        service.request_shutdown()
        await asyncio.wait_for(task, timeout=2.0)

        assert service.has_refreshed_successfully is True
        assert "SOL-USDT" in service.get_current_pairs()

    async def test_shutdown_stops_the_loop(self):
        provider = _provider([_ticker("SOL-USDT", 10_000_000, 20_000_000)])
        service = DynamicPairDiscoveryService(
            market_data_provider=provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=0.01,
        )

        task = asyncio.create_task(service.run_forever())
        await asyncio.sleep(0.05)
        service.request_shutdown()
        await asyncio.wait_for(task, timeout=2.0)

        assert task.done()

    async def test_repeated_refreshes_on_short_interval(self):
        provider = _provider([_ticker("SOL-USDT", 10_000_000, 20_000_000)])
        service = DynamicPairDiscoveryService(
            market_data_provider=provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=0.01,
        )

        task = asyncio.create_task(service.run_forever())
        await asyncio.sleep(0.1)
        service.request_shutdown()
        await asyncio.wait_for(task, timeout=2.0)

        assert provider.fetch_all_linear_tickers.await_count >= 2


class TestGetConfiguredPairsIntegration:
    async def test_get_current_pairs_usable_as_scheduler_provider(self):
        # Confirms the discovery service's bound method matches the
        # MultiPairScanScheduler's expected zero-arg synchronous
        # configured_pair_provider signature.
        provider = _provider([_ticker("SOL-USDT", 10_000_000, 20_000_000)])
        service = DynamicPairDiscoveryService(
            market_data_provider=provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
        )
        await service.refresh_once()
        pairs = service.get_current_pairs()
        assert isinstance(pairs, list)
        assert all(isinstance(p, str) for p in pairs)


class TestPairWarmUpTracker:
    async def test_new_symbol_not_ready_until_warm_up_succeeds(self):
        provider = _FakeWarmUpMarketDataProvider()
        tracker = PairWarmUpTracker(market_data_provider=provider)

        ready = tracker.reconcile([BTC_SYMBOL, "SKHYNIX-USDT"])
        assert "SKHYNIX-USDT" not in ready
        assert BTC_SYMBOL in ready

        # Let the background warm-up task actually run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert "SKHYNIX-USDT" in tracker.ready_symbols

    async def test_symbol_appears_after_warm_up_succeeds_and_stays_ready(self):
        provider = _FakeWarmUpMarketDataProvider()
        tracker = PairWarmUpTracker(market_data_provider=provider)

        tracker.reconcile([BTC_SYMBOL, "SOL-USDT"])
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        first_ready = tracker.reconcile([BTC_SYMBOL, "SOL-USDT"])
        assert "SOL-USDT" in first_ready

        second_ready = tracker.reconcile([BTC_SYMBOL, "SOL-USDT"])
        assert "SOL-USDT" in second_ready
        # Warmed exactly once -- not re-warmed on subsequent reconciles.
        assert provider.fetch_calls.count("SOL-USDT") == 1

    async def test_failed_warm_up_does_not_crash_and_is_retried(self):
        provider = _FakeWarmUpMarketDataProvider()
        provider.failing_symbols.add("ETH-USDT")
        tracker = PairWarmUpTracker(market_data_provider=provider)

        ready = tracker.reconcile([BTC_SYMBOL, "ETH-USDT"])
        assert "ETH-USDT" not in ready

        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Still not ready after a failed attempt, but not crashed either.
        assert "ETH-USDT" not in tracker.ready_symbols

        # Next reconcile (e.g. the following discovery refresh tick)
        # retries the warm-up.
        provider.failing_symbols.discard("ETH-USDT")
        tracker.reconcile([BTC_SYMBOL, "ETH-USDT"])
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert "ETH-USDT" in tracker.ready_symbols
        assert provider.fetch_calls.count("ETH-USDT") == 2

    async def test_failed_warm_up_never_removes_an_already_ready_pair(self):
        provider = _FakeWarmUpMarketDataProvider()
        tracker = PairWarmUpTracker(market_data_provider=provider)

        tracker.reconcile([BTC_SYMBOL, "SOL-USDT"])
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert "SOL-USDT" in tracker.ready_symbols

        provider.failing_symbols.add("ETH-USDT")
        ready = tracker.reconcile([BTC_SYMBOL, "SOL-USDT", "ETH-USDT"])
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert "SOL-USDT" in tracker.ready_symbols
        assert "SOL-USDT" in ready
        assert BTC_SYMBOL in ready

    async def test_btc_symbol_always_ready_immediately(self):
        provider = _FakeWarmUpMarketDataProvider()
        tracker = PairWarmUpTracker(market_data_provider=provider)

        ready = tracker.reconcile([BTC_SYMBOL])
        assert ready == [BTC_SYMBOL]
        assert BTC_SYMBOL not in provider.fetch_calls  # never warmed, always ready

    async def test_symbol_dropped_from_tracking_when_no_longer_qualifying(self):
        provider = _FakeWarmUpMarketDataProvider()
        tracker = PairWarmUpTracker(market_data_provider=provider)

        tracker.reconcile([BTC_SYMBOL, "SOL-USDT"])
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert "SOL-USDT" in tracker.ready_symbols

        tracker.reconcile([BTC_SYMBOL])  # SOL-USDT no longer qualifies
        assert "SOL-USDT" not in tracker.ready_symbols

        # Re-appearing later triggers a fresh warm-up, not instant readiness.
        ready = tracker.reconcile([BTC_SYMBOL, "SOL-USDT"])
        assert "SOL-USDT" not in ready

    async def test_pending_symbol_dropped_cancels_in_flight_task(self):
        provider = _FakeWarmUpMarketDataProvider()
        provider.hanging_symbols.add("SOL-USDT")
        tracker = PairWarmUpTracker(market_data_provider=provider)

        tracker.reconcile([BTC_SYMBOL, "SOL-USDT"])
        await asyncio.sleep(0)
        assert "SOL-USDT" in tracker.pending_symbols

        tracker.reconcile([BTC_SYMBOL])  # drops SOL-USDT while still pending
        assert "SOL-USDT" not in tracker.pending_symbols
        provider.release_hang("SOL-USDT")

    async def test_never_warms_the_same_symbol_twice_concurrently(self):
        provider = _FakeWarmUpMarketDataProvider()
        provider.hanging_symbols.add("SOL-USDT")
        tracker = PairWarmUpTracker(market_data_provider=provider)

        tracker.reconcile([BTC_SYMBOL, "SOL-USDT"])
        await asyncio.sleep(0)
        tracker.reconcile([BTC_SYMBOL, "SOL-USDT"])  # still pending; must not re-trigger

        assert provider.fetch_calls.count("SOL-USDT") == 1
        provider.release_hang("SOL-USDT")
        await asyncio.sleep(0)
        await asyncio.sleep(0)


class TestDynamicPairDiscoveryServiceWarmUp:
    async def test_new_symbol_excluded_from_current_pairs_until_warm(self):
        discovery_provider = _provider(
            [_ticker("SOL-USDT", 10_000_000, 20_000_000), _ticker("ETH-USDT", 10_000_000, 15_000_000)]
        )
        warmup_provider = _FakeWarmUpMarketDataProvider()
        warmup_provider.hanging_symbols.add("SOL-USDT")
        tracker = PairWarmUpTracker(market_data_provider=warmup_provider)

        service = DynamicPairDiscoveryService(
            market_data_provider=discovery_provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
            warm_up_tracker=tracker,
        )

        await service.refresh_once()
        await asyncio.sleep(0)

        pairs = service.get_current_pairs()
        assert "SOL-USDT" not in pairs
        assert BTC_SYMBOL in pairs
        warmup_provider.release_hang("SOL-USDT")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def test_symbol_appears_mid_interval_without_a_second_refresh(self):
        # Regression test: a symbol that finishes warming up in the
        # background must appear via get_current_pairs() as soon as it's
        # ready -- it must NOT require waiting for the next refresh_once()
        # (which could be up to refresh_interval_seconds, e.g. 15 minutes,
        # away). get_current_pairs() re-derives live from the warm-up
        # tracker's ready set on every call.
        discovery_provider = _provider([_ticker("SOL-USDT", 10_000_000, 20_000_000)])
        warmup_provider = _FakeWarmUpMarketDataProvider()
        tracker = PairWarmUpTracker(market_data_provider=warmup_provider)

        service = DynamicPairDiscoveryService(
            market_data_provider=discovery_provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
            warm_up_tracker=tracker,
        )

        await service.refresh_once()
        assert "SOL-USDT" not in service.get_current_pairs()

        # Let the background warm-up task actually complete, without
        # calling refresh_once() again.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert "SOL-USDT" in service.get_current_pairs()

    async def test_symbol_appears_in_current_pairs_after_warm_up_succeeds(self):
        discovery_provider = _provider([_ticker("SOL-USDT", 10_000_000, 20_000_000)])
        warmup_provider = _FakeWarmUpMarketDataProvider()
        tracker = PairWarmUpTracker(market_data_provider=warmup_provider)

        service = DynamicPairDiscoveryService(
            market_data_provider=discovery_provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
            warm_up_tracker=tracker,
        )

        await service.refresh_once()
        assert "SOL-USDT" not in service.get_current_pairs()

        await asyncio.sleep(0)
        await asyncio.sleep(0)

        await service.refresh_once()
        assert "SOL-USDT" in service.get_current_pairs()

    async def test_btc_never_gated_behind_warm_up(self):
        discovery_provider = _provider([_ticker("SOL-USDT", 10_000_000, 20_000_000)])
        warmup_provider = _FakeWarmUpMarketDataProvider()
        warmup_provider.hanging_symbols.add("SOL-USDT")
        tracker = PairWarmUpTracker(market_data_provider=warmup_provider)

        service = DynamicPairDiscoveryService(
            market_data_provider=discovery_provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
            warm_up_tracker=tracker,
        )

        await service.refresh_once()
        assert BTC_SYMBOL in service.get_current_pairs()
        warmup_provider.release_hang("SOL-USDT")
        await asyncio.sleep(0)

    async def test_never_leaves_an_empty_scan_universe_while_new_pairs_warm(self):
        discovery_provider = _provider([_ticker("SOL-USDT", 10_000_000, 20_000_000)])
        warmup_provider = _FakeWarmUpMarketDataProvider()
        warmup_provider.hanging_symbols.add("SOL-USDT")
        tracker = PairWarmUpTracker(market_data_provider=warmup_provider)

        service = DynamicPairDiscoveryService(
            market_data_provider=discovery_provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
            warm_up_tracker=tracker,
        )

        await service.refresh_once()
        assert len(service.get_current_pairs()) > 0
        warmup_provider.release_hang("SOL-USDT")
        await asyncio.sleep(0)

    async def test_warm_up_failure_does_not_raise_out_of_refresh_once(self):
        discovery_provider = _provider([_ticker("SOL-USDT", 10_000_000, 20_000_000)])
        warmup_provider = _FakeWarmUpMarketDataProvider()
        warmup_provider.failing_symbols.add("SOL-USDT")
        tracker = PairWarmUpTracker(market_data_provider=warmup_provider)

        service = DynamicPairDiscoveryService(
            market_data_provider=discovery_provider,
            minimum_open_interest_usdt=5_000_000,
            minimum_turnover_24h_usdt=10_000_000,
            refresh_interval_seconds=900,
            warm_up_tracker=tracker,
        )

        updated = await service.refresh_once()  # must not raise
        assert updated is True
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert "SOL-USDT" not in service.get_current_pairs()
