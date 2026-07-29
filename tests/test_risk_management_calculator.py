"""
Unit tests for app.risk.calculator.RiskManagementCalculator.
"""

from datetime import datetime, timedelta, timezone

from app.liquidity.results import (
    LiquidityLevel,
    LiquiditySide,
    LiquiditySweepResult,
    LiquidityStrength,
    LiquidityType,
    SweepDirection,
)
from app.models.candle import Candle
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType
from app.risk.active_trade_guard import ActiveTradeGuard
from app.risk.averaging_guard import AveragingGuard
from app.risk.calculator import RiskManagementCalculator
from app.risk.correlation_filter import CorrelationFilter
from app.risk.position_risk import PositionRiskCalculator
from app.risk.results import RiskPlanStatus
from app.risk.stop_loss import DynamicStopLossCalculator
from app.risk.take_profit import SingleTakeProfitCalculator

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _zone(direction="BUY", lower=90.0, upper=95.0) -> TradeZone:
    return TradeZone(
        zone_id="zone-1",
        symbol="ETH-USDT",
        timeframe="15m",
        zone_type=ZoneType.ORDER_BLOCK,
        direction=direction,
        lower_price=lower,
        upper_price=upper,
        created_at=UTC_NOW,
        source_candle_timestamp=UTC_NOW,
        source_candle_index=0,
        status=ZoneStatus.FRESH,
        touch_count=0,
    )


def _sweep(direction=SweepDirection.BULLISH, sweep_price=85.0) -> LiquiditySweepResult:
    level = LiquidityLevel(
        liquidity_id="level-1",
        symbol="ETH-USDT",
        timeframe="15m",
        liquidity_type=(
            LiquidityType.EQUAL_LOW if direction == SweepDirection.BULLISH else LiquidityType.EQUAL_HIGH
        ),
        liquidity_side=(
            LiquiditySide.SELL_SIDE if direction == SweepDirection.BULLISH else LiquiditySide.BUY_SIDE
        ),
        price=90.0,
        start_timestamp=UTC_NOW - timedelta(minutes=20),
        end_timestamp=UTC_NOW - timedelta(minutes=20),
        source_timestamps=[UTC_NOW - timedelta(minutes=20)],
        touch_count=2,
        strength=LiquidityStrength.STRONG,
        active=True,
    )
    return LiquiditySweepResult(
        sweep_id="sweep-1",
        symbol="ETH-USDT",
        timeframe="15m",
        direction=direction,
        liquidity_level=level,
        sweep_candle_timestamp=UTC_NOW - timedelta(minutes=10),
        sweep_candle_index=0,
        sweep_price=sweep_price,
        close_price=91.0,
        penetration_distance=1.0,
        penetration_ratio=0.01,
        reclaimed_level=True,
        confirmed=True,
        reason="test sweep",
    )


def _liquidity_level(price: float, side=LiquiditySide.BUY_SIDE) -> LiquidityLevel:
    return LiquidityLevel(
        liquidity_id=f"level-{price}",
        symbol="ETH-USDT",
        timeframe="15m",
        liquidity_type=LiquidityType.PREVIOUS_DAY_HIGH if side == LiquiditySide.BUY_SIDE else LiquidityType.PREVIOUS_DAY_LOW,
        liquidity_side=side,
        price=price,
        start_timestamp=UTC_NOW,
        end_timestamp=UTC_NOW,
        source_timestamps=[UTC_NOW],
        touch_count=2,
        strength=LiquidityStrength.INSTITUTIONAL,
        active=True,
    )


def _candles(closes: list[float], symbol="ETH-USDT") -> list[Candle]:
    return [
        Candle(
            timestamp=UTC_NOW + timedelta(minutes=i),
            open=c,
            high=c + 1,
            low=c - 1,
            close=c,
            volume=100.0,
            symbol=symbol,
            timeframe="15m",
        )
        for i, c in enumerate(closes)
    ]


def _make_calculator(max_active=5, max_correlation=0.80) -> RiskManagementCalculator:
    return RiskManagementCalculator(
        stop_loss_calculator=DynamicStopLossCalculator(atr_multiplier=1.5, structural_buffer_ratio=0.0005),
        take_profit_calculator=SingleTakeProfitCalculator(),
        position_risk_calculator=PositionRiskCalculator(maximum_risk_percentage=1.0),
        correlation_filter=CorrelationFilter(maximum_allowed_correlation=max_correlation, minimum_observations=5),
        active_trade_guard=ActiveTradeGuard(maximum_active_trades=max_active),
        averaging_guard=AveragingGuard(),
    )


class TestRiskManagementCalculator:
    def test_complete_valid_risk_plan(self):
        zone = _zone()
        sweep = _sweep()
        # Selected stop is the sweep-based candidate (~84.96), so the
        # target must be far enough above entry to clear RR >= 2.0
        # against that ~15-point risk distance.
        level = _liquidity_level(160.0)
        candidate_candles = _candles([100 + i for i in range(10)])

        plan = _make_calculator().calculate(
            "BUY",
            entry_price=100.0,
            atr=2.0,
            selected_zone=zone,
            liquidity_sweep=sweep,
            institutional_liquidity_levels=[level],
            major_swings=[],
            fair_value_gaps=[],
            account_balance=10000.0,
            active_trade_count=0,
            candidate_symbol="ETH-USDT",
            candidate_candles=candidate_candles,
            active_position_candles={},
            active_positions=[],
        )
        assert plan.valid is True
        assert plan.status == RiskPlanStatus.VALID

    def test_active_trade_limit_failure_stops_calculation(self):
        zone = _zone()
        sweep = _sweep()
        plan = _make_calculator(max_active=5).calculate(
            "BUY",
            entry_price=100.0,
            atr=2.0,
            selected_zone=zone,
            liquidity_sweep=sweep,
            institutional_liquidity_levels=[],
            major_swings=[],
            fair_value_gaps=[],
            account_balance=10000.0,
            active_trade_count=5,
            candidate_symbol="ETH-USDT",
            candidate_candles=[],
            active_position_candles={},
            active_positions=[],
        )
        assert plan.valid is False
        assert plan.status == RiskPlanStatus.REJECTED

    def test_averaging_failure_stops_calculation(self):
        zone = _zone()
        sweep = _sweep()
        losing_position = {
            "symbol": "ETH-USDT",
            "direction": "BUY",
            "entry_price": 100.0,
            "current_price": 95.0,
            "status": "OPEN",
        }
        plan = _make_calculator().calculate(
            "BUY",
            entry_price=100.0,
            atr=2.0,
            selected_zone=zone,
            liquidity_sweep=sweep,
            institutional_liquidity_levels=[],
            major_swings=[],
            fair_value_gaps=[],
            account_balance=10000.0,
            active_trade_count=1,
            candidate_symbol="ETH-USDT",
            candidate_candles=[],
            active_position_candles={},
            active_positions=[losing_position],
        )
        assert plan.valid is False
        assert plan.status == RiskPlanStatus.REJECTED

    def test_invalid_stop_loss_stops_calculation(self):
        zone = _zone()
        sweep = _sweep()
        plan = _make_calculator().calculate(
            "BUY",
            entry_price=100.0,
            atr=0.0,  # invalid ATR -> no valid stop loss
            selected_zone=zone,
            liquidity_sweep=sweep,
            institutional_liquidity_levels=[],
            major_swings=[],
            fair_value_gaps=[],
            account_balance=10000.0,
            active_trade_count=0,
            candidate_symbol="ETH-USDT",
            candidate_candles=[],
            active_position_candles={},
            active_positions=[],
        )
        assert plan.valid is False
        assert plan.status == RiskPlanStatus.INVALID
        assert plan.stop_loss_result.valid is False

    def test_invalid_take_profit_stops_calculation(self):
        zone = _zone()
        sweep = _sweep()
        # No institutional levels/swings/FVGs -> no take-profit candidates.
        plan = _make_calculator().calculate(
            "BUY",
            entry_price=100.0,
            atr=2.0,
            selected_zone=zone,
            liquidity_sweep=sweep,
            institutional_liquidity_levels=[],
            major_swings=[],
            fair_value_gaps=[],
            account_balance=10000.0,
            active_trade_count=0,
            candidate_symbol="ETH-USDT",
            candidate_candles=[],
            active_position_candles={},
            active_positions=[],
        )
        assert plan.valid is False
        assert plan.take_profit_result.valid is False

    def test_rr_below_2_stops_calculation(self):
        zone = _zone()
        sweep = _sweep()
        weak_level = _liquidity_level(101.0)  # RR far below 2.0
        plan = _make_calculator().calculate(
            "BUY",
            entry_price=100.0,
            atr=2.0,
            selected_zone=zone,
            liquidity_sweep=sweep,
            institutional_liquidity_levels=[weak_level],
            major_swings=[],
            fair_value_gaps=[],
            account_balance=10000.0,
            active_trade_count=0,
            candidate_symbol="ETH-USDT",
            candidate_candles=[],
            active_position_candles={},
            active_positions=[],
        )
        assert plan.valid is False

    def test_invalid_position_risk_stops_calculation(self):
        zone = _zone()
        sweep = _sweep()
        level = _liquidity_level(120.0)
        plan = _make_calculator().calculate(
            "BUY",
            entry_price=100.0,
            atr=2.0,
            selected_zone=zone,
            liquidity_sweep=sweep,
            institutional_liquidity_levels=[level],
            major_swings=[],
            fair_value_gaps=[],
            account_balance=0.0,  # invalid balance
            active_trade_count=0,
            candidate_symbol="ETH-USDT",
            candidate_candles=[],
            active_position_candles={},
            active_positions=[],
        )
        assert plan.valid is False
        assert plan.position_risk.valid is False

    def test_high_correlation_rejects(self):
        zone = _zone()
        sweep = _sweep()
        level = _liquidity_level(120.0)
        candidate_closes = [100 + i for i in range(10)]
        candidate_candles = _candles(candidate_closes, "ETH-USDT")
        active_candles = {"BTC-USDT": _candles(candidate_closes, "BTC-USDT")}  # identical -> too high

        plan = _make_calculator().calculate(
            "BUY",
            entry_price=100.0,
            atr=2.0,
            selected_zone=zone,
            liquidity_sweep=sweep,
            institutional_liquidity_levels=[level],
            major_swings=[],
            fair_value_gaps=[],
            account_balance=10000.0,
            active_trade_count=0,
            candidate_symbol="ETH-USDT",
            candidate_candles=candidate_candles,
            active_position_candles=active_candles,
            active_positions=[],
        )
        assert plan.valid is False
        assert plan.correlation_result.acceptable is False

    def test_exactly_one_tp_retained(self):
        zone = _zone()
        sweep = _sweep()
        level_a = _liquidity_level(160.0)
        level_b = _liquidity_level(170.0)
        plan = _make_calculator().calculate(
            "BUY",
            entry_price=100.0,
            atr=2.0,
            selected_zone=zone,
            liquidity_sweep=sweep,
            institutional_liquidity_levels=[level_a, level_b],
            major_swings=[],
            fair_value_gaps=[],
            account_balance=10000.0,
            active_trade_count=0,
            candidate_symbol="ETH-USDT",
            candidate_candles=_candles([100 + i for i in range(10)]),
            active_position_candles={},
            active_positions=[],
        )
        assert plan.valid is True
        assert isinstance(plan.take_profit_result.selected_take_profit, float)

    def test_all_component_results_preserved(self):
        zone = _zone()
        sweep = _sweep()
        level = _liquidity_level(120.0)
        plan = _make_calculator().calculate(
            "BUY",
            entry_price=100.0,
            atr=2.0,
            selected_zone=zone,
            liquidity_sweep=sweep,
            institutional_liquidity_levels=[level],
            major_swings=[],
            fair_value_gaps=[],
            account_balance=10000.0,
            active_trade_count=0,
            candidate_symbol="ETH-USDT",
            candidate_candles=_candles([100 + i for i in range(10)]),
            active_position_candles={},
            active_positions=[],
        )
        assert plan.stop_loss_result is not None
        assert plan.take_profit_result is not None
        assert plan.position_risk is not None
        assert plan.correlation_result is not None

    def test_no_confidence_score_or_signal_generated(self):
        zone = _zone()
        sweep = _sweep()
        level = _liquidity_level(120.0)
        plan = _make_calculator().calculate(
            "BUY",
            entry_price=100.0,
            atr=2.0,
            selected_zone=zone,
            liquidity_sweep=sweep,
            institutional_liquidity_levels=[level],
            major_swings=[],
            fair_value_gaps=[],
            account_balance=10000.0,
            active_trade_count=0,
            candidate_symbol="ETH-USDT",
            candidate_candles=_candles([100 + i for i in range(10)]),
            active_position_candles={},
            active_positions=[],
        )
        plan_fields = set(type(plan).model_fields.keys())
        forbidden = {"confidence_score", "signal_type", "trade_id"}
        assert plan_fields.isdisjoint(forbidden)

    def test_inputs_not_mutated(self):
        zone = _zone()
        sweep = _sweep()
        level = _liquidity_level(120.0)
        candidate_candles = _candles([100 + i for i in range(10)])

        zone_snapshot = zone.model_copy()
        sweep_snapshot = sweep.model_copy()
        level_snapshot = level.model_copy()
        candles_snapshot = [c.model_copy() for c in candidate_candles]

        _make_calculator().calculate(
            "BUY",
            entry_price=100.0,
            atr=2.0,
            selected_zone=zone,
            liquidity_sweep=sweep,
            institutional_liquidity_levels=[level],
            major_swings=[],
            fair_value_gaps=[],
            account_balance=10000.0,
            active_trade_count=0,
            candidate_symbol="ETH-USDT",
            candidate_candles=candidate_candles,
            active_position_candles={},
            active_positions=[],
        )

        assert zone == zone_snapshot
        assert sweep == sweep_snapshot
        assert level == level_snapshot
        assert candidate_candles == candles_snapshot
