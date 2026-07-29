"""
Unit tests for app.risk.take_profit.SingleTakeProfitCalculator.
"""

from datetime import datetime, timedelta, timezone

from app.liquidity.results import LiquidityLevel, LiquiditySide, LiquidityStrength, LiquidityType
from app.market_structure.results import SwingPoint, SwingType
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType
from app.risk.results import TakeProfitSource
from app.risk.take_profit import SingleTakeProfitCalculator

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _level(
    price: float,
    side: LiquiditySide,
    liquidity_type=LiquidityType.PREVIOUS_DAY_HIGH,
    strength=LiquidityStrength.INSTITUTIONAL,
    active=True,
    liquidity_id=None,
) -> LiquidityLevel:
    return LiquidityLevel(
        liquidity_id=liquidity_id or f"level-{price}-{side.value}",
        symbol="BTC-USDT",
        timeframe="15m",
        liquidity_type=liquidity_type,
        liquidity_side=side,
        price=price,
        start_timestamp=UTC_NOW,
        end_timestamp=UTC_NOW,
        source_timestamps=[UTC_NOW],
        touch_count=2,
        strength=strength,
        active=active,
    )


def _swing(price: float, swing_type: SwingType, confirmed=True) -> SwingPoint:
    return SwingPoint(
        swing_id=f"swing-{price}-{swing_type.value}",
        symbol="BTC-USDT",
        timeframe="15m",
        timestamp=UTC_NOW,
        candle_index=3,
        swing_type=swing_type,
        price=price,
        left_strength=3,
        right_strength=3,
        confirmed=confirmed,
    )


def _fvg(direction: str, lower: float, upper: float, zone_id=None) -> TradeZone:
    return TradeZone(
        zone_id=zone_id or f"fvg-{direction}-{lower}-{upper}",
        symbol="BTC-USDT",
        timeframe="15m",
        zone_type=ZoneType.FAIR_VALUE_GAP,
        direction=direction,
        lower_price=lower,
        upper_price=upper,
        created_at=UTC_NOW,
        source_candle_timestamp=UTC_NOW,
        source_candle_index=0,
        status=ZoneStatus.FRESH,
        touch_count=0,
    )


def _calculator() -> SingleTakeProfitCalculator:
    return SingleTakeProfitCalculator()


class TestInstitutionalLiquidityTarget:
    def test_buy_nearest_institutional_liquidity_target(self):
        level = _level(120.0, LiquiditySide.BUY_SIDE)  # RR = 20/10 = 2.0
        result = _calculator().calculate("BUY", 100.0, 90.0, [level], [], [])
        assert result.valid is True
        assert result.selected_source == TakeProfitSource.MAJOR_INSTITUTIONAL_LIQUIDITY
        assert result.selected_take_profit == 120.0

    def test_sell_nearest_institutional_liquidity_target(self):
        level = _level(80.0, LiquiditySide.SELL_SIDE, liquidity_type=LiquidityType.PREVIOUS_DAY_LOW)  # RR = 20/10 = 2.0
        result = _calculator().calculate("SELL", 100.0, 110.0, [level], [], [])
        assert result.valid is True
        assert result.selected_source == TakeProfitSource.MAJOR_INSTITUTIONAL_LIQUIDITY
        assert result.selected_take_profit == 80.0


class TestSwingTargets:
    def test_buy_strong_swing_high_target(self):
        swing = _swing(130.0, SwingType.HIGH)  # RR = 30/10 = 3.0
        result = _calculator().calculate("BUY", 100.0, 90.0, [], [swing], [])
        assert result.valid is True
        assert result.selected_source == TakeProfitSource.STRONG_SWING_HIGH

    def test_sell_strong_swing_low_target(self):
        swing = _swing(70.0, SwingType.LOW)  # RR = 30/10 = 3.0
        result = _calculator().calculate("SELL", 100.0, 110.0, [], [swing], [])
        assert result.valid is True
        assert result.selected_source == TakeProfitSource.STRONG_SWING_LOW


class TestOtherSources:
    def test_unmitigated_liquidity_pool_target(self):
        # No institutional-liquidity candidate collides in priority since
        # both pull from the same levels list; institutional liquidity
        # always wins priority when present, so to reach the pool tier we
        # exclude that source by using only swing-less/level-only input
        # where the priority-1 source still applies. This test instead
        # verifies the pool candidate exists and is valid when selected
        # directly via priority tie-break absence of higher tiers.
        level = _level(120.0, LiquiditySide.BUY_SIDE, strength=LiquidityStrength.STRONG)
        result = _calculator().calculate("BUY", 100.0, 90.0, [level], [], [])
        pool_candidates = [c for c in result.candidates if c.source == TakeProfitSource.UNMITIGATED_LIQUIDITY_POOL]
        assert len(pool_candidates) == 1
        assert pool_candidates[0].valid is True

    def test_fvg_completion_target(self):
        fvg = _fvg("SELL", lower=115.0, upper=125.0)  # BUY targets upper=125 -> RR=25/10=2.5
        result = _calculator().calculate("BUY", 100.0, 90.0, [], [], [fvg])
        assert result.valid is True
        assert result.selected_source == TakeProfitSource.FAIR_VALUE_GAP_COMPLETION
        assert result.selected_take_profit == 125.0


class TestRiskRewardFiltering:
    def test_nearest_target_below_rr_2_skipped(self):
        weak_level = _level(105.0, LiquiditySide.BUY_SIDE, liquidity_id="weak")  # RR = 5/10 = 0.5
        strong_level = _level(125.0, LiquiditySide.BUY_SIDE, liquidity_id="strong")  # RR = 25/10 = 2.5
        result = _calculator().calculate("BUY", 100.0, 90.0, [weak_level, strong_level], [], [])
        assert result.valid is True
        assert result.selected_take_profit == 125.0

    def test_next_valid_target_selected(self):
        weak_level = _level(102.0, LiquiditySide.BUY_SIDE, liquidity_id="weak")
        valid_level = _level(130.0, LiquiditySide.BUY_SIDE, liquidity_id="valid")
        result = _calculator().calculate("BUY", 100.0, 90.0, [weak_level, valid_level], [], [])
        assert result.selected_take_profit == 130.0

    def test_no_target_with_rr_2_returns_invalid(self):
        weak_level = _level(101.0, LiquiditySide.BUY_SIDE)  # RR = 1/10 = 0.1
        result = _calculator().calculate("BUY", 100.0, 90.0, [weak_level], [], [])
        assert result.valid is False
        assert result.metadata["rejection_code"] == "NO_TARGET_WITH_MINIMUM_RR"

    def test_target_on_wrong_side_rejected(self):
        wrong_side_level = _level(80.0, LiquiditySide.BUY_SIDE)  # below entry for BUY
        result = _calculator().calculate("BUY", 100.0, 90.0, [wrong_side_level], [], [])
        assert result.valid is False


class TestSingleTarget:
    def test_exactly_one_final_tp(self):
        level_a = _level(120.0, LiquiditySide.BUY_SIDE, liquidity_id="a")
        level_b = _level(130.0, LiquiditySide.BUY_SIDE, liquidity_id="b")
        result = _calculator().calculate("BUY", 100.0, 90.0, [level_a, level_b], [], [])
        assert result.selected_take_profit is not None
        # only one selected_take_profit value; not a list of multiple TPs
        assert isinstance(result.selected_take_profit, float)

    def test_no_tp2_or_tp3_fields(self):
        from app.risk.results import TakeProfitResult

        result_fields = set(TakeProfitResult.model_fields.keys())
        assert "take_profit_2" not in result_fields
        assert "take_profit_3" not in result_fields

    def test_deterministic_ranking(self):
        level = _level(120.0, LiquiditySide.BUY_SIDE)
        result_one = _calculator().calculate("BUY", 100.0, 90.0, [level], [], [])
        result_two = _calculator().calculate("BUY", 100.0, 90.0, [level], [], [])
        assert result_one.selected_target_id == result_two.selected_target_id

    def test_inputs_not_mutated(self):
        level = _level(120.0, LiquiditySide.BUY_SIDE)
        swing = _swing(130.0, SwingType.HIGH)
        fvg = _fvg("SELL", 115.0, 125.0)
        level_snapshot = level.model_copy()
        swing_snapshot = swing.model_copy()
        fvg_snapshot = fvg.model_copy()

        _calculator().calculate("BUY", 100.0, 90.0, [level], [swing], [fvg])

        assert level == level_snapshot
        assert swing == swing_snapshot
        assert fvg == fvg_snapshot
