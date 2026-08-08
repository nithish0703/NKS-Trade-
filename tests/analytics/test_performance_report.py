"""
Unit tests for app.analytics.performance_report's pure aggregation
functions and orchestration.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.analytics.performance_report import (
    PerformanceStats,
    TradeR,
    compute_performance_stats,
    compute_r_multiple,
    format_performance_report,
    generate_performance_report,
    slice_by,
    to_trade_r,
)
from app.models.signal import Direction, Signal, SignalStatus
from app.storage.signal_repository import (
    OUTCOME_SOURCE_MANUAL_ACTIVATION,
    OUTCOME_SOURCE_PASSIVE_TRACKING,
    PASSIVE_OUTCOME_LOSS,
    PASSIVE_OUTCOME_UNRESOLVED,
    PASSIVE_OUTCOME_WIN,
    ClosedTradeRecord,
)

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _signal(trade_id="SMC-1", coin="BTC-USDT", entry_price=100.0, stop_loss=95.0, take_profit=110.0) -> Signal:
    return Signal(
        trade_id=trade_id,
        coin=coin,
        direction=Direction.BUY,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward_ratio=2.0,
        status=SignalStatus.CONFIRMED,
        liquidity_type="EQUAL_HIGH",
        entry_zone_type="ORDER_BLOCK",
        structure_confirmation="BOS",
        detection_time_utc=UTC_NOW,
        institutional_reason="test",
        setup_key=f"setup-{trade_id}",
        liquidity_sweep_id="sweep-1",
        structure_break_id="break-1",
        entry_zone_id="zone-1",
        created_at_utc=UTC_NOW,
    )


def _trade_r(
    *,
    trade_id="SMC-1",
    symbol="BTC-USDT",
    r_multiple: float,
    is_win: bool,
    order_flow_confidence=None,
    entry_grade=None,
    stop_loss_source=None,
    outcome_source=OUTCOME_SOURCE_PASSIVE_TRACKING,
    offset_minutes: int = 0,
) -> TradeR:
    return TradeR(
        trade_id=trade_id,
        symbol=symbol,
        order_flow_confidence=order_flow_confidence,
        entry_grade=entry_grade,
        stop_loss_source=stop_loss_source,
        outcome_source=outcome_source,
        r_multiple=r_multiple,
        is_win=is_win,
        closed_at_utc=UTC_NOW + timedelta(minutes=offset_minutes),
    )


class TestComputeRMultiple:
    def test_buy_full_take_profit_gives_positive_r(self):
        # entry=100, stop=95 (risk=5), exit=110 -> R = 10/5 = 2.0
        assert compute_r_multiple(100.0, 95.0, 110.0) == 2.0

    def test_buy_stop_loss_touch_gives_minus_one_r(self):
        assert compute_r_multiple(100.0, 95.0, 95.0) == -1.0

    def test_sell_full_take_profit_gives_positive_r(self):
        # entry=100, stop=105 (risk=-5), exit=90 -> R = -10/-5 = 2.0
        assert compute_r_multiple(100.0, 105.0, 90.0) == 2.0

    def test_sell_stop_loss_touch_gives_minus_one_r(self):
        assert compute_r_multiple(100.0, 105.0, 105.0) == -1.0

    def test_equal_entry_and_stop_raises(self):
        with pytest.raises(ValueError):
            compute_r_multiple(100.0, 100.0, 105.0)


class TestToTradeR:
    def test_win_flag_derived_from_passive_outcome(self):
        record = ClosedTradeRecord(
            signal=_signal(),
            order_flow_confidence="HIGH",
            entry_grade="A",
            stop_loss_source="LIQUIDITY_SWEEP",
            outcome_source=OUTCOME_SOURCE_MANUAL_ACTIVATION,
            passive_outcome=PASSIVE_OUTCOME_WIN,
            passive_exit_price=110.0,
            passive_closed_at_utc=UTC_NOW,
        )
        trade = to_trade_r(record)
        assert trade.is_win is True
        assert trade.r_multiple == 2.0
        assert trade.order_flow_confidence == "HIGH"
        assert trade.entry_grade == "A"
        assert trade.stop_loss_source == "LIQUIDITY_SWEEP"
        assert trade.outcome_source == OUTCOME_SOURCE_MANUAL_ACTIVATION

    def test_loss_flag_derived_from_passive_outcome(self):
        record = ClosedTradeRecord(
            signal=_signal(),
            order_flow_confidence=None,
            entry_grade=None,
            stop_loss_source=None,
            outcome_source=OUTCOME_SOURCE_PASSIVE_TRACKING,
            passive_outcome=PASSIVE_OUTCOME_LOSS,
            passive_exit_price=95.0,
            passive_closed_at_utc=UTC_NOW,
        )
        trade = to_trade_r(record)
        assert trade.is_win is False
        assert trade.r_multiple == -1.0


class TestComputePerformanceStats:
    def test_empty_trades_gives_zero_stats(self):
        stats = compute_performance_stats([])
        assert stats == PerformanceStats(
            trade_count=0,
            win_rate_percentage=None,
            average_win_r=None,
            average_loss_r=None,
            expectancy_r=None,
            max_consecutive_losses=0,
            max_drawdown_r=None,
            profit_factor=None,
        )

    def test_all_wins(self):
        trades = [_trade_r(r_multiple=2.0, is_win=True), _trade_r(r_multiple=3.0, is_win=True)]
        stats = compute_performance_stats(trades)
        assert stats.trade_count == 2
        assert stats.win_rate_percentage == 100.0
        assert stats.average_win_r == 2.5
        assert stats.average_loss_r is None
        assert stats.max_consecutive_losses == 0
        assert stats.profit_factor is None  # gross_losses_r == 0, division avoided

    def test_all_losses(self):
        trades = [_trade_r(r_multiple=-1.0, is_win=False), _trade_r(r_multiple=-1.0, is_win=False)]
        stats = compute_performance_stats(trades)
        assert stats.trade_count == 2
        assert stats.win_rate_percentage == 0.0
        assert stats.average_win_r is None
        assert stats.average_loss_r == 1.0
        assert stats.max_consecutive_losses == 2
        assert stats.profit_factor == 0.0

    def test_mixed_win_rate_and_expectancy(self):
        # 2 wins @ +2R, 2 losses @ -1R -> win_rate=50%, expectancy = 0.5*2 - 0.5*1 = 0.5
        trades = [
            _trade_r(r_multiple=2.0, is_win=True, offset_minutes=0),
            _trade_r(r_multiple=-1.0, is_win=False, offset_minutes=1),
            _trade_r(r_multiple=2.0, is_win=True, offset_minutes=2),
            _trade_r(r_multiple=-1.0, is_win=False, offset_minutes=3),
        ]
        stats = compute_performance_stats(trades)
        assert stats.trade_count == 4
        assert stats.win_rate_percentage == 50.0
        assert stats.average_win_r == 2.0
        assert stats.average_loss_r == 1.0
        assert stats.expectancy_r == pytest.approx(0.5)
        assert stats.profit_factor == pytest.approx(2.0)  # gross gains 4 / gross losses 2

    def test_max_consecutive_losses_across_a_gap(self):
        trades = [
            _trade_r(r_multiple=-1.0, is_win=False, offset_minutes=0),
            _trade_r(r_multiple=-1.0, is_win=False, offset_minutes=1),
            _trade_r(r_multiple=2.0, is_win=True, offset_minutes=2),
            _trade_r(r_multiple=-1.0, is_win=False, offset_minutes=3),
        ]
        stats = compute_performance_stats(trades)
        assert stats.max_consecutive_losses == 2

    def test_max_drawdown_r(self):
        # cumulative: +2 (peak 2), -1 (dd 1), -1 (dd 2), +2 (peak restored, dd 0)
        trades = [
            _trade_r(r_multiple=2.0, is_win=True, offset_minutes=0),
            _trade_r(r_multiple=-1.0, is_win=False, offset_minutes=1),
            _trade_r(r_multiple=-1.0, is_win=False, offset_minutes=2),
            _trade_r(r_multiple=2.0, is_win=True, offset_minutes=3),
        ]
        stats = compute_performance_stats(trades)
        assert stats.max_drawdown_r == pytest.approx(2.0)

    def test_sorts_internally_by_closed_at_utc_regardless_of_input_order(self):
        # Passed out of order; streak/drawdown must still reflect
        # chronological order, not list order.
        later_loss = _trade_r(r_multiple=-1.0, is_win=False, offset_minutes=5)
        earlier_win = _trade_r(r_multiple=2.0, is_win=True, offset_minutes=0)
        stats = compute_performance_stats([later_loss, earlier_win])
        assert stats.max_consecutive_losses == 1
        assert stats.max_drawdown_r == pytest.approx(1.0)


class TestSliceBy:
    def test_groups_by_key(self):
        trades = [
            _trade_r(trade_id="a", r_multiple=2.0, is_win=True, order_flow_confidence="HIGH"),
            _trade_r(trade_id="b", r_multiple=-1.0, is_win=False, order_flow_confidence="LOW"),
            _trade_r(trade_id="c", r_multiple=2.0, is_win=True, order_flow_confidence="HIGH"),
        ]
        result = slice_by(trades, lambda t: t.order_flow_confidence)
        assert set(result.keys()) == {"HIGH", "LOW"}
        assert result["HIGH"].trade_count == 2
        assert result["LOW"].trade_count == 1

    def test_none_values_grouped_under_unknown(self):
        trades = [_trade_r(r_multiple=2.0, is_win=True, order_flow_confidence=None)]
        result = slice_by(trades, lambda t: t.order_flow_confidence)
        assert set(result.keys()) == {"UNKNOWN"}


class TestGeneratePerformanceReport:
    async def test_empty_repository_gives_zero_trades(self):
        repository = AsyncMock()
        repository.list_passively_closed = AsyncMock(return_value=[])
        repository.list_not_passively_closed = AsyncMock(return_value=[])

        report = await generate_performance_report(repository, window_days=7, now=UTC_NOW)

        assert report.overall.trade_count == 0
        assert report.still_open_count == 0

    async def test_still_open_count_reflects_unresolved_signals(self):
        repository = AsyncMock()
        repository.list_passively_closed = AsyncMock(return_value=[])
        repository.list_not_passively_closed = AsyncMock(return_value=[object(), object(), object()])

        report = await generate_performance_report(repository, window_days=7, now=UTC_NOW)

        assert report.still_open_count == 3

    async def test_closed_trades_populate_overall_and_slices(self):
        repository = AsyncMock()
        repository.list_passively_closed = AsyncMock(
            return_value=[
                ClosedTradeRecord(
                    signal=_signal(trade_id="SMC-A", coin="BTC-USDT"),
                    order_flow_confidence="HIGH",
                    entry_grade="A",
                    stop_loss_source="LIQUIDITY_SWEEP",
                    outcome_source=OUTCOME_SOURCE_MANUAL_ACTIVATION,
                    passive_outcome=PASSIVE_OUTCOME_WIN,
                    passive_exit_price=110.0,
                    passive_closed_at_utc=UTC_NOW,
                ),
                ClosedTradeRecord(
                    signal=_signal(trade_id="SMC-B", coin="ETH-USDT"),
                    order_flow_confidence="LOW",
                    entry_grade="B",
                    stop_loss_source="ATR",
                    outcome_source=OUTCOME_SOURCE_PASSIVE_TRACKING,
                    passive_outcome=PASSIVE_OUTCOME_LOSS,
                    passive_exit_price=95.0,
                    passive_closed_at_utc=UTC_NOW,
                ),
            ]
        )
        repository.list_not_passively_closed = AsyncMock(return_value=[])

        report = await generate_performance_report(repository, window_days=7, now=UTC_NOW)

        assert report.overall.trade_count == 2
        assert set(report.by_symbol.keys()) == {"BTC-USDT", "ETH-USDT"}
        assert set(report.by_order_flow_confidence.keys()) == {"HIGH", "LOW"}
        assert set(report.by_entry_grade.keys()) == {"A", "B"}
        assert set(report.by_stop_loss_source.keys()) == {"LIQUIDITY_SWEEP", "ATR"}
        assert set(report.by_outcome_source.keys()) == {
            OUTCOME_SOURCE_MANUAL_ACTIVATION,
            OUTCOME_SOURCE_PASSIVE_TRACKING,
        }
        assert report.manual_activation_count == 1
        assert report.passive_tracking_count == 1

    async def test_unresolved_trades_excluded_from_stats_but_counted(self):
        repository = AsyncMock()
        repository.list_passively_closed = AsyncMock(
            return_value=[
                ClosedTradeRecord(
                    signal=_signal(trade_id="SMC-WIN", coin="BTC-USDT"),
                    order_flow_confidence=None,
                    entry_grade=None,
                    stop_loss_source=None,
                    outcome_source=OUTCOME_SOURCE_PASSIVE_TRACKING,
                    passive_outcome=PASSIVE_OUTCOME_WIN,
                    passive_exit_price=110.0,
                    passive_closed_at_utc=UTC_NOW,
                ),
                ClosedTradeRecord(
                    signal=_signal(trade_id="SMC-UNRESOLVED", coin="ETH-USDT"),
                    order_flow_confidence=None,
                    entry_grade=None,
                    stop_loss_source=None,
                    outcome_source=OUTCOME_SOURCE_PASSIVE_TRACKING,
                    passive_outcome=PASSIVE_OUTCOME_UNRESOLVED,
                    passive_exit_price=None,
                    passive_closed_at_utc=UTC_NOW,
                ),
            ]
        )
        repository.list_not_passively_closed = AsyncMock(return_value=[])

        report = await generate_performance_report(repository, window_days=7, now=UTC_NOW)

        # Only the WIN contributes to trade_count/expectancy; the
        # UNRESOLVED trade (no price ever observed) must never be
        # counted as a fabricated 0R trade.
        assert report.overall.trade_count == 1
        assert "ETH-USDT" not in report.by_symbol
        assert report.unresolved_count == 1


class TestFormatPerformanceReport:
    async def test_empty_report_prints_no_data_yet(self):
        repository = AsyncMock()
        repository.list_passively_closed = AsyncMock(return_value=[])
        repository.list_not_passively_closed = AsyncMock(return_value=[])
        report = await generate_performance_report(repository, now=UTC_NOW)

        text = format_performance_report(report)

        assert "no data yet" in text

    async def test_non_empty_report_includes_overall_and_slices(self):
        repository = AsyncMock()
        repository.list_passively_closed = AsyncMock(
            return_value=[
                ClosedTradeRecord(
                    signal=_signal(trade_id="SMC-A"),
                    order_flow_confidence="HIGH",
                    entry_grade="A",
                    stop_loss_source="LIQUIDITY_SWEEP",
                    outcome_source=OUTCOME_SOURCE_MANUAL_ACTIVATION,
                    passive_outcome=PASSIVE_OUTCOME_WIN,
                    passive_exit_price=110.0,
                    passive_closed_at_utc=UTC_NOW,
                )
            ]
        )
        repository.list_not_passively_closed = AsyncMock(return_value=[])
        report = await generate_performance_report(repository, now=UTC_NOW)

        text = format_performance_report(report)

        assert "OVERALL" in text
        assert "By symbol" in text
        assert "By order_flow_confidence" in text
        assert "By outcome_source" in text
        assert "MANUAL_ACTIVATION" in text
        assert "unresolved" in text
        assert "still open" in text
