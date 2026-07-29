"""
Unit tests for app.liquidity.level_selector.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.liquidity.equal_high_low import LiquidityCalculationError
from app.liquidity.level_selector import InstitutionalLiquiditySelector
from app.liquidity.results import LiquidityLevel, LiquiditySide, LiquidityStrength, LiquidityType

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _level(
    liquidity_type: LiquidityType,
    side: LiquiditySide,
    price: float,
    strength: LiquidityStrength,
    active: bool = True,
    touch_count: int = 2,
    liquidity_id: str = None,
    timestamp_offset: int = 0,
    metadata: dict = None,
) -> LiquidityLevel:
    ts = UTC_NOW + timedelta(minutes=timestamp_offset)
    return LiquidityLevel(
        liquidity_id=liquidity_id or f"{liquidity_type.value}-{price}-{timestamp_offset}",
        symbol="BTC-USDT",
        timeframe="15m",
        liquidity_type=liquidity_type,
        liquidity_side=side,
        price=price,
        start_timestamp=ts,
        end_timestamp=ts,
        source_timestamps=[ts],
        touch_count=touch_count,
        strength=strength,
        active=active,
        metadata=metadata,
    )


class TestInstitutionalLiquiditySelector:
    def test_weak_levels_excluded(self):
        levels = [
            _level(LiquidityType.EQUAL_HIGH, LiquiditySide.BUY_SIDE, 110, LiquidityStrength.WEAK)
        ]
        selected = InstitutionalLiquiditySelector().select_active_institutional_levels(
            levels, 100.0, UTC_NOW
        )
        assert selected == []

    def test_inactive_levels_excluded(self):
        levels = [
            _level(
                LiquidityType.EQUAL_HIGH,
                LiquiditySide.BUY_SIDE,
                110,
                LiquidityStrength.STRONG,
                active=False,
            )
        ]
        selected = InstitutionalLiquiditySelector().select_active_institutional_levels(
            levels, 100.0, UTC_NOW
        )
        assert selected == []

    def test_institutional_levels_included(self):
        levels = [
            _level(
                LiquidityType.MAJOR_SWING_HIGH,
                LiquiditySide.BUY_SIDE,
                110,
                LiquidityStrength.INSTITUTIONAL,
            )
        ]
        selected = InstitutionalLiquiditySelector().select_active_institutional_levels(
            levels, 100.0, UTC_NOW
        )
        assert len(selected) == 1

    def test_strong_levels_included(self):
        levels = [
            _level(
                LiquidityType.SESSION_HIGH, LiquiditySide.BUY_SIDE, 110, LiquidityStrength.STRONG
            )
        ]
        selected = InstitutionalLiquiditySelector().select_active_institutional_levels(
            levels, 100.0, UTC_NOW
        )
        assert len(selected) == 1

    def test_duplicate_removal(self):
        level = _level(
            LiquidityType.MAJOR_SWING_HIGH,
            LiquiditySide.BUY_SIDE,
            110,
            LiquidityStrength.INSTITUTIONAL,
            liquidity_id="dup-1",
        )
        selected = InstitutionalLiquiditySelector().select_active_institutional_levels(
            [level, level], 100.0, UTC_NOW
        )
        assert len(selected) == 1

    def test_priority_ordering(self):
        levels = [
            _level(
                LiquidityType.SESSION_HIGH,
                LiquiditySide.BUY_SIDE,
                105,
                LiquidityStrength.STRONG,
                liquidity_id="session",
            ),
            _level(
                LiquidityType.PREVIOUS_WEEK_HIGH,
                LiquiditySide.BUY_SIDE,
                106,
                LiquidityStrength.INSTITUTIONAL,
                liquidity_id="week",
            ),
            _level(
                LiquidityType.PREVIOUS_DAY_HIGH,
                LiquiditySide.BUY_SIDE,
                107,
                LiquidityStrength.INSTITUTIONAL,
                liquidity_id="day",
            ),
        ]
        selected = InstitutionalLiquiditySelector().select_active_institutional_levels(
            levels, 100.0, UTC_NOW
        )
        ids_in_order = [level.liquidity_id for level in selected]
        assert ids_in_order == ["week", "day", "session"]

    def test_distance_ordering_within_same_priority(self):
        levels = [
            _level(
                LiquidityType.SESSION_HIGH,
                LiquiditySide.BUY_SIDE,
                120,
                LiquidityStrength.STRONG,
                liquidity_id="far",
            ),
            _level(
                LiquidityType.SESSION_LOW,
                LiquiditySide.SELL_SIDE,
                95,
                LiquidityStrength.STRONG,
                liquidity_id="near",
            ),
        ]
        selected = InstitutionalLiquiditySelector().select_active_institutional_levels(
            levels, 100.0, UTC_NOW
        )
        ids_in_order = [level.liquidity_id for level in selected]
        assert ids_in_order == ["near", "far"]

    def test_buy_side_above_current_price(self):
        levels = [
            _level(
                LiquidityType.MAJOR_SWING_HIGH,
                LiquiditySide.BUY_SIDE,
                110,
                LiquidityStrength.INSTITUTIONAL,
            )
        ]
        selected = InstitutionalLiquiditySelector().select_active_institutional_levels(
            levels, 100.0, UTC_NOW
        )
        assert selected[0].price > 100.0
        assert selected[0].liquidity_side == LiquiditySide.BUY_SIDE

    def test_sell_side_below_current_price(self):
        levels = [
            _level(
                LiquidityType.MAJOR_SWING_LOW,
                LiquiditySide.SELL_SIDE,
                90,
                LiquidityStrength.INSTITUTIONAL,
            )
        ]
        selected = InstitutionalLiquiditySelector().select_active_institutional_levels(
            levels, 100.0, UTC_NOW
        )
        assert selected[0].price < 100.0
        assert selected[0].liquidity_side == LiquiditySide.SELL_SIDE

    def test_no_take_profit_field_exists(self):
        levels = [
            _level(
                LiquidityType.MAJOR_SWING_HIGH,
                LiquiditySide.BUY_SIDE,
                110,
                LiquidityStrength.INSTITUTIONAL,
            )
        ]
        selected = InstitutionalLiquiditySelector().select_active_institutional_levels(
            levels, 100.0, UTC_NOW
        )
        level_fields = set(type(selected[0]).model_fields.keys())
        assert "take_profit" not in level_fields
        assert "is_take_profit_target" not in level_fields

    def test_inputs_not_mutated(self):
        levels = [
            _level(
                LiquidityType.MAJOR_SWING_HIGH,
                LiquiditySide.BUY_SIDE,
                110,
                LiquidityStrength.INSTITUTIONAL,
            )
        ]
        snapshot = [l.model_copy() for l in levels]
        InstitutionalLiquiditySelector().select_active_institutional_levels(
            levels, 100.0, UTC_NOW
        )
        assert levels == snapshot

    def test_invalid_current_price(self):
        with pytest.raises(LiquidityCalculationError):
            InstitutionalLiquiditySelector().select_active_institutional_levels(
                [], 0.0, UTC_NOW
            )

    def test_naive_current_time_rejected(self):
        with pytest.raises(LiquidityCalculationError):
            InstitutionalLiquiditySelector().select_active_institutional_levels(
                [], 100.0, datetime(2026, 1, 1)
            )

    def test_explicitly_invalidated_level_excluded(self):
        levels = [
            _level(
                LiquidityType.MAJOR_SWING_HIGH,
                LiquiditySide.BUY_SIDE,
                110,
                LiquidityStrength.INSTITUTIONAL,
                metadata={"invalidated": True},
            )
        ]
        selected = InstitutionalLiquiditySelector().select_active_institutional_levels(
            levels, 100.0, UTC_NOW
        )
        assert selected == []
