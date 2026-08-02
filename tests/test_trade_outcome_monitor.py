"""
Tests for app.monitoring.trade_outcome_monitor.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.signal import Direction, MarketRegime, Signal, SignalType
from app.monitoring.trade_outcome_monitor import TradeOutcomeMonitor, evaluate_outcome
from app.storage.signal_repository import (
    DASHBOARD_STATUS_ACTIVE,
    DASHBOARD_STATUS_CLOSED_LOSS,
    DASHBOARD_STATUS_CLOSED_WIN,
    SignalNotFoundError,
    SignalWithStatus,
)

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _signal(*, trade_id="SMC-1", coin="BTC-USDT", direction=Direction.BUY) -> Signal:
    return Signal(
        trade_id=trade_id,
        coin=coin,
        direction=direction,
        entry_price=100.0,
        stop_loss=95.0 if direction == Direction.BUY else 105.0,
        take_profit=110.0 if direction == Direction.BUY else 90.0,
        risk_reward_ratio=3.0,
        confidence_score=95.0,
        signal_type=SignalType.PREMIUM,
        market_regime=MarketRegime.TRENDING,
        higher_timeframe_bias="BULLISH",
        liquidity_type="EQUAL_HIGH",
        entry_zone_type="ORDER_BLOCK",
        structure_confirmation="BOS",
        volume_confirmation=True,
        atr_status="EXPANDING",
        trading_session="LONDON",
        btc_market_alignment=True,
        detection_time_utc=UTC_NOW,
        institutional_reason="Confirmed setup facts only.",
        setup_key=f"setup-{trade_id}",
        liquidity_sweep_id="sweep-1",
        structure_break_id="break-1",
        entry_zone_id="zone-1",
        retest_id="retest-1",
        created_at_utc=UTC_NOW,
    )


class TestEvaluateOutcome:
    def test_buy_take_profit_touched_is_win(self):
        signal = _signal(direction=Direction.BUY)
        assert evaluate_outcome(signal, current_price=110.0) == DASHBOARD_STATUS_CLOSED_WIN

    def test_buy_take_profit_exceeded_is_win(self):
        signal = _signal(direction=Direction.BUY)
        assert evaluate_outcome(signal, current_price=115.0) == DASHBOARD_STATUS_CLOSED_WIN

    def test_buy_stop_loss_touched_is_loss(self):
        signal = _signal(direction=Direction.BUY)
        assert evaluate_outcome(signal, current_price=95.0) == DASHBOARD_STATUS_CLOSED_LOSS

    def test_buy_stop_loss_breached_is_loss(self):
        signal = _signal(direction=Direction.BUY)
        assert evaluate_outcome(signal, current_price=90.0) == DASHBOARD_STATUS_CLOSED_LOSS

    def test_buy_price_between_levels_is_none(self):
        signal = _signal(direction=Direction.BUY)
        assert evaluate_outcome(signal, current_price=105.0) is None

    def test_sell_take_profit_touched_is_win(self):
        signal = _signal(direction=Direction.SELL)
        assert evaluate_outcome(signal, current_price=90.0) == DASHBOARD_STATUS_CLOSED_WIN

    def test_sell_take_profit_exceeded_is_win(self):
        signal = _signal(direction=Direction.SELL)
        assert evaluate_outcome(signal, current_price=85.0) == DASHBOARD_STATUS_CLOSED_WIN

    def test_sell_stop_loss_touched_is_loss(self):
        signal = _signal(direction=Direction.SELL)
        assert evaluate_outcome(signal, current_price=105.0) == DASHBOARD_STATUS_CLOSED_LOSS

    def test_sell_stop_loss_breached_is_loss(self):
        signal = _signal(direction=Direction.SELL)
        assert evaluate_outcome(signal, current_price=110.0) == DASHBOARD_STATUS_CLOSED_LOSS

    def test_sell_price_between_levels_is_none(self):
        signal = _signal(direction=Direction.SELL)
        assert evaluate_outcome(signal, current_price=95.0) is None


class TestTradeOutcomeMonitorConstruction:
    def test_non_positive_interval_rejected(self):
        with pytest.raises(ValueError):
            TradeOutcomeMonitor(
                signal_repository=MagicMock(),
                market_data_provider=MagicMock(),
                interval_seconds=0,
            )


class TestCheckActiveSignals:
    async def test_closes_signal_whose_take_profit_was_touched(self):
        signal = _signal(trade_id="SMC-WIN", direction=Direction.BUY)
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(
            return_value=[SignalWithStatus(signal=signal, dashboard_status=DASHBOARD_STATUS_ACTIVE)]
        )
        repository.close_signal = AsyncMock(
            return_value=SignalWithStatus(signal=signal, dashboard_status=DASHBOARD_STATUS_CLOSED_WIN)
        )
        market_data_provider = MagicMock()
        market_data_provider.fetch_ticker_price = AsyncMock(return_value=111.0)
        on_closed = AsyncMock()

        monitor = TradeOutcomeMonitor(
            signal_repository=repository,
            market_data_provider=market_data_provider,
            interval_seconds=60,
            on_signal_closed=on_closed,
        )

        await monitor.check_active_signals()

        repository.close_signal.assert_awaited_once()
        _, kwargs = repository.close_signal.call_args
        assert repository.close_signal.call_args.args[0] == "SMC-WIN"
        assert kwargs["outcome"] == DASHBOARD_STATUS_CLOSED_WIN
        assert kwargs["exit_price"] == 111.0
        on_closed.assert_awaited_once()

    async def test_leaves_signal_open_when_neither_level_touched(self):
        signal = _signal(trade_id="SMC-OPEN", direction=Direction.BUY)
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(
            return_value=[SignalWithStatus(signal=signal, dashboard_status=DASHBOARD_STATUS_ACTIVE)]
        )
        repository.close_signal = AsyncMock()
        market_data_provider = MagicMock()
        market_data_provider.fetch_ticker_price = AsyncMock(return_value=102.0)

        monitor = TradeOutcomeMonitor(
            signal_repository=repository,
            market_data_provider=market_data_provider,
            interval_seconds=60,
        )

        await monitor.check_active_signals()

        repository.close_signal.assert_not_awaited()

    async def test_ticker_fetch_failure_for_one_signal_does_not_block_others(self):
        failing_signal = _signal(trade_id="SMC-FAIL", coin="AAA-USDT", direction=Direction.BUY)
        winning_signal = _signal(trade_id="SMC-WIN", coin="BBB-USDT", direction=Direction.BUY)
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(
            return_value=[
                SignalWithStatus(signal=failing_signal, dashboard_status=DASHBOARD_STATUS_ACTIVE),
                SignalWithStatus(signal=winning_signal, dashboard_status=DASHBOARD_STATUS_ACTIVE),
            ]
        )
        repository.close_signal = AsyncMock(
            return_value=SignalWithStatus(
                signal=winning_signal, dashboard_status=DASHBOARD_STATUS_CLOSED_WIN
            )
        )

        async def _fetch_ticker_price(coin):
            if coin == "AAA-USDT":
                raise RuntimeError("exchange timeout")
            return 110.0

        market_data_provider = MagicMock()
        market_data_provider.fetch_ticker_price = AsyncMock(side_effect=_fetch_ticker_price)

        monitor = TradeOutcomeMonitor(
            signal_repository=repository,
            market_data_provider=market_data_provider,
            interval_seconds=60,
        )

        await monitor.check_active_signals()

        repository.close_signal.assert_awaited_once()
        assert repository.close_signal.call_args.args[0] == "SMC-WIN"

    async def test_missing_signal_during_close_is_skipped_gracefully(self):
        signal = _signal(trade_id="SMC-GONE", direction=Direction.BUY)
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(
            return_value=[SignalWithStatus(signal=signal, dashboard_status=DASHBOARD_STATUS_ACTIVE)]
        )
        repository.close_signal = AsyncMock(side_effect=SignalNotFoundError("gone"))
        market_data_provider = MagicMock()
        market_data_provider.fetch_ticker_price = AsyncMock(return_value=111.0)
        on_closed = AsyncMock()

        monitor = TradeOutcomeMonitor(
            signal_repository=repository,
            market_data_provider=market_data_provider,
            interval_seconds=60,
            on_signal_closed=on_closed,
        )

        # Must not raise.
        await monitor.check_active_signals()
        on_closed.assert_not_awaited()

    async def test_none_ticker_price_leaves_signal_untouched(self):
        signal = _signal(trade_id="SMC-NOPRICE", direction=Direction.BUY)
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(
            return_value=[SignalWithStatus(signal=signal, dashboard_status=DASHBOARD_STATUS_ACTIVE)]
        )
        repository.close_signal = AsyncMock()
        market_data_provider = MagicMock()
        market_data_provider.fetch_ticker_price = AsyncMock(return_value=None)

        monitor = TradeOutcomeMonitor(
            signal_repository=repository,
            market_data_provider=market_data_provider,
            interval_seconds=60,
        )

        await monitor.check_active_signals()

        repository.close_signal.assert_not_awaited()


class TestRunForeverShutdown:
    async def test_request_shutdown_stops_the_loop_promptly(self):
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(return_value=[])
        market_data_provider = MagicMock()

        monitor = TradeOutcomeMonitor(
            signal_repository=repository,
            market_data_provider=market_data_provider,
            interval_seconds=3600,
        )

        import asyncio

        task = asyncio.create_task(monitor.run_forever())
        await asyncio.sleep(0)  # let it run the first check_active_signals()
        monitor.request_shutdown()
        await asyncio.wait_for(task, timeout=2.0)

        assert repository.list_recent_with_status.await_count >= 1
