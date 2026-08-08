"""
Tests for app.monitoring.signal_outcome_monitor.SignalOutcomeMonitor.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.thresholds import (
    MAX_CONSECUTIVE_MISSING_PRICE_CYCLES,
    MAX_TRACKED_SIGNAL_DURATION_CANDLES,
)
from app.config.timeframes import ENTRY_TIMEFRAME, get_timeframe_duration_seconds
from app.models.signal import Direction, Signal, SignalStatus
from app.monitoring.signal_outcome_monitor import SignalOutcomeMonitor, evaluate_outcome
from app.storage.signal_repository import (
    OUTCOME_SOURCE_MANUAL_ACTIVATION,
    PASSIVE_OUTCOME_LOSS,
    PASSIVE_OUTCOME_TIMEOUT,
    PASSIVE_OUTCOME_UNRESOLVED,
    PASSIVE_OUTCOME_WIN,
    SignalNotFoundError,
    SignalOutcomeResult,
)

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

_MAX_DURATION_SECONDS = MAX_TRACKED_SIGNAL_DURATION_CANDLES * get_timeframe_duration_seconds(ENTRY_TIMEFRAME)


def _fixed_clock() -> datetime:
    return UTC_NOW


def _signal(
    *, trade_id="SMC-1", coin="BTC-USDT", direction=Direction.BUY, detection_time_utc=UTC_NOW
) -> Signal:
    return Signal(
        trade_id=trade_id,
        coin=coin,
        direction=direction,
        entry_price=100.0,
        stop_loss=95.0 if direction == Direction.BUY else 105.0,
        take_profit=110.0 if direction == Direction.BUY else 90.0,
        risk_reward_ratio=3.0,
        status=SignalStatus.CONFIRMED,
        liquidity_type="EQUAL_HIGH",
        entry_zone_type="ORDER_BLOCK",
        structure_confirmation="BOS",
        detection_time_utc=detection_time_utc,
        institutional_reason="Confirmed setup facts only.",
        setup_key=f"setup-{trade_id}",
        liquidity_sweep_id="sweep-1",
        structure_break_id="break-1",
        entry_zone_id="zone-1",
        created_at_utc=detection_time_utc,
    )


def _outcome_result(signal: Signal, *, outcome: str, source=OUTCOME_SOURCE_MANUAL_ACTIVATION) -> SignalOutcomeResult:
    return SignalOutcomeResult(
        signal=signal, dashboard_status="CLOSED_WIN", outcome_source=source, passive_outcome=outcome
    )


class TestEvaluateOutcome:
    def test_buy_take_profit_touched_is_win(self):
        signal = _signal(direction=Direction.BUY)
        assert evaluate_outcome(signal, current_price=110.0) == PASSIVE_OUTCOME_WIN

    def test_buy_take_profit_exceeded_is_win(self):
        signal = _signal(direction=Direction.BUY)
        assert evaluate_outcome(signal, current_price=115.0) == PASSIVE_OUTCOME_WIN

    def test_buy_stop_loss_touched_is_loss(self):
        signal = _signal(direction=Direction.BUY)
        assert evaluate_outcome(signal, current_price=95.0) == PASSIVE_OUTCOME_LOSS

    def test_buy_stop_loss_breached_is_loss(self):
        signal = _signal(direction=Direction.BUY)
        assert evaluate_outcome(signal, current_price=90.0) == PASSIVE_OUTCOME_LOSS

    def test_buy_price_between_levels_is_none(self):
        signal = _signal(direction=Direction.BUY)
        assert evaluate_outcome(signal, current_price=105.0) is None

    def test_sell_take_profit_touched_is_win(self):
        signal = _signal(direction=Direction.SELL)
        assert evaluate_outcome(signal, current_price=90.0) == PASSIVE_OUTCOME_WIN

    def test_sell_stop_loss_touched_is_loss(self):
        signal = _signal(direction=Direction.SELL)
        assert evaluate_outcome(signal, current_price=105.0) == PASSIVE_OUTCOME_LOSS

    def test_sell_price_between_levels_is_none(self):
        signal = _signal(direction=Direction.SELL)
        assert evaluate_outcome(signal, current_price=95.0) is None

    def test_checks_stop_loss_before_take_profit(self):
        # Defensive ordering: SL is checked first. Verified indirectly
        # here since a real signal can never satisfy both at once (see
        # the module docstring) -- this documents the source-code
        # ordering intent rather than a behavioural difference.
        import inspect

        source = inspect.getsource(evaluate_outcome)
        stop_loss_check_index = source.index("stop_loss")
        take_profit_check_index = source.index("take_profit")
        assert stop_loss_check_index < take_profit_check_index


class TestSignalOutcomeMonitorConstruction:
    def test_non_positive_interval_rejected(self):
        with pytest.raises(ValueError):
            SignalOutcomeMonitor(
                signal_repository=MagicMock(),
                market_data_provider=MagicMock(),
                interval_seconds=0,
            )


class TestBulkTickerFetch:
    async def test_uses_bulk_fetch_not_per_symbol_loop(self):
        # The whole point of the merge: ONE fetch_all_ticker_prices()
        # call per cycle covering every open signal, never
        # fetch_ticker_price() called once per symbol (which would not
        # scale once DYNAMIC_PAIR_DISCOVERY_ENABLED=true widens the
        # scanned universe).
        signals = [_signal(trade_id=f"SMC-{i}", coin=f"COIN{i}-USDT") for i in range(5)]
        repository = MagicMock()
        repository.list_not_passively_closed = AsyncMock(return_value=signals)
        repository.close_passive = AsyncMock(
            side_effect=lambda trade_id, **kwargs: _outcome_result(
                next(s for s in signals if s.trade_id == trade_id), outcome=kwargs["outcome"]
            )
        )
        market_data_provider = MagicMock()
        market_data_provider.fetch_all_ticker_prices = AsyncMock(return_value={})
        market_data_provider.fetch_ticker_price = AsyncMock()

        monitor = SignalOutcomeMonitor(
            signal_repository=repository,
            market_data_provider=market_data_provider,
            interval_seconds=60,
            clock=_fixed_clock,
        )

        await monitor.check_open_signals()

        market_data_provider.fetch_all_ticker_prices.assert_awaited_once()
        market_data_provider.fetch_ticker_price.assert_not_called()

    async def test_no_open_signals_skips_the_fetch_entirely(self):
        repository = MagicMock()
        repository.list_not_passively_closed = AsyncMock(return_value=[])
        market_data_provider = MagicMock()
        market_data_provider.fetch_all_ticker_prices = AsyncMock(return_value={})

        monitor = SignalOutcomeMonitor(
            signal_repository=repository, market_data_provider=market_data_provider, interval_seconds=60, clock=_fixed_clock
        )

        await monitor.check_open_signals()

        market_data_provider.fetch_all_ticker_prices.assert_not_awaited()

    async def test_bulk_fetch_failure_does_not_raise(self):
        repository = MagicMock()
        repository.list_not_passively_closed = AsyncMock(return_value=[_signal()])
        market_data_provider = MagicMock()
        market_data_provider.fetch_all_ticker_prices = AsyncMock(side_effect=RuntimeError("exchange down"))

        monitor = SignalOutcomeMonitor(
            signal_repository=repository, market_data_provider=market_data_provider, interval_seconds=60, clock=_fixed_clock
        )

        # Must not raise.
        await monitor.check_open_signals()


class TestCheckOpenSignals:
    async def test_closes_signal_whose_take_profit_was_touched(self):
        signal = _signal(trade_id="SMC-WIN", direction=Direction.BUY)
        repository = MagicMock()
        repository.list_not_passively_closed = AsyncMock(return_value=[signal])
        repository.close_passive = AsyncMock(return_value=_outcome_result(signal, outcome=PASSIVE_OUTCOME_WIN))
        market_data_provider = MagicMock()
        market_data_provider.fetch_all_ticker_prices = AsyncMock(return_value={"BTC-USDT": 111.0})
        on_closed = AsyncMock()

        monitor = SignalOutcomeMonitor(
            signal_repository=repository,
            market_data_provider=market_data_provider,
            interval_seconds=60,
            on_signal_closed=on_closed,
            clock=_fixed_clock,
        )

        await monitor.check_open_signals()

        repository.close_passive.assert_awaited_once()
        assert repository.close_passive.call_args.args[0] == "SMC-WIN"
        assert repository.close_passive.call_args.kwargs["outcome"] == PASSIVE_OUTCOME_WIN
        assert repository.close_passive.call_args.kwargs["exit_price"] == 111.0
        on_closed.assert_awaited_once()

    async def test_leaves_signal_open_when_neither_level_touched(self):
        signal = _signal(trade_id="SMC-OPEN", direction=Direction.BUY)
        repository = MagicMock()
        repository.list_not_passively_closed = AsyncMock(return_value=[signal])
        repository.close_passive = AsyncMock()
        market_data_provider = MagicMock()
        market_data_provider.fetch_all_ticker_prices = AsyncMock(return_value={"BTC-USDT": 102.0})

        monitor = SignalOutcomeMonitor(
            signal_repository=repository, market_data_provider=market_data_provider, interval_seconds=60, clock=_fixed_clock
        )

        await monitor.check_open_signals()

        repository.close_passive.assert_not_awaited()

    async def test_missing_price_for_one_symbol_does_not_block_others(self):
        no_price_signal = _signal(trade_id="SMC-NOPRICE", coin="AAA-USDT", direction=Direction.BUY)
        winning_signal = _signal(trade_id="SMC-WIN", coin="BBB-USDT", direction=Direction.BUY)
        repository = MagicMock()
        repository.list_not_passively_closed = AsyncMock(return_value=[no_price_signal, winning_signal])
        repository.close_passive = AsyncMock(
            return_value=_outcome_result(winning_signal, outcome=PASSIVE_OUTCOME_WIN)
        )
        market_data_provider = MagicMock()
        # AAA-USDT is simply absent from the bulk response.
        market_data_provider.fetch_all_ticker_prices = AsyncMock(return_value={"BBB-USDT": 111.0})

        monitor = SignalOutcomeMonitor(
            signal_repository=repository, market_data_provider=market_data_provider, interval_seconds=60, clock=_fixed_clock
        )

        await monitor.check_open_signals()

        repository.close_passive.assert_awaited_once()
        assert repository.close_passive.call_args.args[0] == "SMC-WIN"

    async def test_missing_signal_during_close_is_skipped_gracefully(self):
        signal = _signal(trade_id="SMC-GONE", direction=Direction.BUY)
        repository = MagicMock()
        repository.list_not_passively_closed = AsyncMock(return_value=[signal])
        repository.close_passive = AsyncMock(side_effect=SignalNotFoundError("gone"))
        market_data_provider = MagicMock()
        market_data_provider.fetch_all_ticker_prices = AsyncMock(return_value={"BTC-USDT": 111.0})
        on_closed = AsyncMock()

        monitor = SignalOutcomeMonitor(
            signal_repository=repository,
            market_data_provider=market_data_provider,
            interval_seconds=60,
            on_signal_closed=on_closed,
            clock=_fixed_clock,
        )

        # Must not raise.
        await monitor.check_open_signals()
        on_closed.assert_not_awaited()


class TestTimeout:
    async def test_signal_within_duration_is_not_timed_out(self):
        signal = _signal(trade_id="SMC-YOUNG", detection_time_utc=UTC_NOW)
        repository = MagicMock()
        repository.list_not_passively_closed = AsyncMock(return_value=[signal])
        repository.close_passive = AsyncMock()
        market_data_provider = MagicMock()
        # No price for this symbol at all -- only the timeout path could close it.
        market_data_provider.fetch_all_ticker_prices = AsyncMock(return_value={})

        monitor = SignalOutcomeMonitor(
            signal_repository=repository,
            market_data_provider=market_data_provider,
            interval_seconds=60,
            clock=_fixed_clock,
        )
        # clock=_fixed_clock pins "now" to UTC_NOW, matching this
        # signal's detection_time_utc exactly -- elapsed time is ~0, so
        # it must not time out.
        await monitor.check_open_signals()

        repository.close_passive.assert_not_awaited()

    async def test_signal_past_max_duration_closes_as_timeout(self):
        old_detection_time = UTC_NOW - timedelta(seconds=_MAX_DURATION_SECONDS + 1)
        signal = _signal(trade_id="SMC-OLD", detection_time_utc=old_detection_time)
        repository = MagicMock()
        repository.list_not_passively_closed = AsyncMock(return_value=[signal])
        repository.close_passive = AsyncMock(
            return_value=_outcome_result(signal, outcome=PASSIVE_OUTCOME_TIMEOUT)
        )
        market_data_provider = MagicMock()
        # Still open (price between levels) -- only elapsed time forces the close.
        market_data_provider.fetch_all_ticker_prices = AsyncMock(return_value={"BTC-USDT": 102.0})

        monitor = SignalOutcomeMonitor(
            signal_repository=repository, market_data_provider=market_data_provider, interval_seconds=60, clock=_fixed_clock
        )

        await monitor.check_open_signals()

        repository.close_passive.assert_awaited_once()
        assert repository.close_passive.call_args.kwargs["outcome"] == PASSIVE_OUTCOME_TIMEOUT
        assert repository.close_passive.call_args.kwargs["exit_price"] == 102.0

    async def test_timed_out_signal_with_no_price_at_all_is_left_open_not_closed(self):
        # A missing price must never be recorded as a fabricated 0R
        # timeout close, even if the signal is otherwise past its
        # MAX_TRACKED_SIGNAL_DURATION_CANDLES window -- it is retried,
        # governed only by the consecutive-missing-price counter (see
        # TestMissingPriceUnresolved below), never by elapsed time.
        old_detection_time = UTC_NOW - timedelta(seconds=_MAX_DURATION_SECONDS + 1)
        signal = _signal(trade_id="SMC-OLDNOPRICE", detection_time_utc=old_detection_time)
        repository = MagicMock()
        repository.list_not_passively_closed = AsyncMock(return_value=[signal])
        repository.close_passive = AsyncMock()
        market_data_provider = MagicMock()
        market_data_provider.fetch_all_ticker_prices = AsyncMock(return_value={})

        monitor = SignalOutcomeMonitor(
            signal_repository=repository, market_data_provider=market_data_provider, interval_seconds=60, clock=_fixed_clock
        )

        await monitor.check_open_signals()

        repository.close_passive.assert_not_awaited()


class TestMissingPriceUnresolved:
    async def test_single_missing_price_cycle_is_retried_not_closed(self):
        signal = _signal(trade_id="SMC-NOPRICE")
        repository = MagicMock()
        repository.list_not_passively_closed = AsyncMock(return_value=[signal])
        repository.close_passive = AsyncMock()
        market_data_provider = MagicMock()
        market_data_provider.fetch_all_ticker_prices = AsyncMock(return_value={})

        monitor = SignalOutcomeMonitor(
            signal_repository=repository, market_data_provider=market_data_provider, interval_seconds=60, clock=_fixed_clock
        )

        await monitor.check_open_signals()

        repository.close_passive.assert_not_awaited()

    async def test_closes_as_unresolved_with_null_exit_price_after_max_consecutive_misses(self):
        signal = _signal(trade_id="SMC-NOPRICE")
        repository = MagicMock()
        repository.list_not_passively_closed = AsyncMock(return_value=[signal])
        repository.close_passive = AsyncMock(
            return_value=_outcome_result(signal, outcome=PASSIVE_OUTCOME_UNRESOLVED)
        )
        market_data_provider = MagicMock()
        market_data_provider.fetch_all_ticker_prices = AsyncMock(return_value={})

        monitor = SignalOutcomeMonitor(
            signal_repository=repository, market_data_provider=market_data_provider, interval_seconds=60, clock=_fixed_clock
        )

        for _ in range(MAX_CONSECUTIVE_MISSING_PRICE_CYCLES):
            await monitor.check_open_signals()

        repository.close_passive.assert_awaited_once()
        assert repository.close_passive.call_args.kwargs["outcome"] == PASSIVE_OUTCOME_UNRESOLVED
        assert repository.close_passive.call_args.kwargs["exit_price"] is None

    async def test_price_reappearing_resets_the_missing_streak(self):
        signal = _signal(trade_id="SMC-FLAKY")
        repository = MagicMock()
        repository.list_not_passively_closed = AsyncMock(return_value=[signal])
        repository.close_passive = AsyncMock()
        market_data_provider = MagicMock()
        # Miss almost up to the threshold, then a good price appears
        # (still no WIN/LOSS/timeout), then it goes missing again --
        # the streak from before the reset must not carry over.
        responses = [{}] * (MAX_CONSECUTIVE_MISSING_PRICE_CYCLES - 1) + [{"BTC-USDT": 102.0}] + [{}] * (
            MAX_CONSECUTIVE_MISSING_PRICE_CYCLES - 1
        )
        market_data_provider.fetch_all_ticker_prices = AsyncMock(side_effect=responses)

        monitor = SignalOutcomeMonitor(
            signal_repository=repository, market_data_provider=market_data_provider, interval_seconds=60, clock=_fixed_clock
        )

        for _ in responses:
            await monitor.check_open_signals()

        repository.close_passive.assert_not_awaited()


class TestLeaseGuard:
    async def test_no_work_done_when_lease_not_acquired(self):
        repository = MagicMock()
        repository.list_not_passively_closed = AsyncMock(return_value=[_signal()])
        market_data_provider = MagicMock()
        market_data_provider.fetch_all_ticker_prices = AsyncMock(return_value={})
        lease_guard = MagicMock()
        lease_guard.try_acquire = AsyncMock(return_value=False)

        monitor = SignalOutcomeMonitor(
            signal_repository=repository,
            market_data_provider=market_data_provider,
            interval_seconds=60,
            clock=_fixed_clock,
            lease_guard=lease_guard,
        )

        await monitor.check_open_signals()

        lease_guard.try_acquire.assert_awaited_once_with(UTC_NOW)
        repository.list_not_passively_closed.assert_not_awaited()
        market_data_provider.fetch_all_ticker_prices.assert_not_awaited()

    async def test_work_proceeds_when_lease_acquired(self):
        signal = _signal(trade_id="SMC-WIN")
        repository = MagicMock()
        repository.list_not_passively_closed = AsyncMock(return_value=[signal])
        repository.close_passive = AsyncMock(return_value=_outcome_result(signal, outcome=PASSIVE_OUTCOME_WIN))
        market_data_provider = MagicMock()
        market_data_provider.fetch_all_ticker_prices = AsyncMock(return_value={"BTC-USDT": 111.0})
        lease_guard = MagicMock()
        lease_guard.try_acquire = AsyncMock(return_value=True)

        monitor = SignalOutcomeMonitor(
            signal_repository=repository,
            market_data_provider=market_data_provider,
            interval_seconds=60,
            clock=_fixed_clock,
            lease_guard=lease_guard,
        )

        await monitor.check_open_signals()

        repository.close_passive.assert_awaited_once()


class TestRunForeverShutdown:
    async def test_request_shutdown_stops_the_loop_promptly(self):
        repository = MagicMock()
        repository.list_not_passively_closed = AsyncMock(return_value=[])
        market_data_provider = MagicMock()

        monitor = SignalOutcomeMonitor(
            signal_repository=repository,
            market_data_provider=market_data_provider,
            interval_seconds=3600,
        )

        task = asyncio.create_task(monitor.run_forever())
        await asyncio.sleep(0)  # let it run the first check_open_signals()
        monitor.request_shutdown()
        await asyncio.wait_for(task, timeout=2.0)

        assert repository.list_not_passively_closed.await_count >= 1
