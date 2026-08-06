"""
Unit tests for app.strategy_pipeline.ifvg (Stage 4: IFVG).
"""

from datetime import datetime, timedelta, timezone

from app.config.thresholds import (
    BOS_ZONE_RETEST_VALIDITY_WINDOW_CANDLES,
    IFVG_VALIDITY_WINDOW_CANDLES,
)
from app.market_structure.displacement import DisplacementResult
from app.market_structure.results import SwingPoint, SwingType
from app.market_structure.shift_results import BreakConfirmation, BreakDirection, StructureBreakResult
from app.liquidity.results import LiquidityLevel, LiquiditySide, LiquidityStrength, LiquidityType, SweepDirection, LiquiditySweepResult
from app.models.candle import Candle
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType
from app.strategy_pipeline.ifvg import evaluate_ifvg

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYMBOL = "BTC-USDT"
TIMEFRAME = "15m"


def _candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
    )


def _fvg_zone(
    *,
    direction: str,
    lower_price: float,
    upper_price: float,
    source_candle_index: int,
) -> TradeZone:
    ts = UTC_NOW + timedelta(minutes=source_candle_index)
    return TradeZone(
        zone_id=f"fvg-{direction}-{source_candle_index}",
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        zone_type=ZoneType.FAIR_VALUE_GAP,
        direction=direction,
        lower_price=lower_price,
        upper_price=upper_price,
        created_at=ts,
        source_candle_timestamp=ts,
        source_candle_index=source_candle_index,
        status=ZoneStatus.FRESH,
        touch_count=0,
    )


def _sweep(direction: SweepDirection, price: float = 90.0) -> LiquiditySweepResult:
    ts = UTC_NOW - timedelta(minutes=10)
    level = LiquidityLevel(
        liquidity_id=f"level-{price}",
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        liquidity_type=(
            LiquidityType.EQUAL_HIGH if direction == SweepDirection.BEARISH else LiquidityType.EQUAL_LOW
        ),
        liquidity_side=(
            LiquiditySide.BUY_SIDE if direction == SweepDirection.BEARISH else LiquiditySide.SELL_SIDE
        ),
        price=price,
        start_timestamp=ts,
        end_timestamp=ts,
        source_timestamps=[ts],
        touch_count=2,
        strength=LiquidityStrength.STRONG,
        active=True,
    )
    return LiquiditySweepResult(
        sweep_id=f"sweep-{direction.value}",
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        direction=direction,
        liquidity_level=level,
        sweep_candle_timestamp=ts,
        sweep_candle_index=0,
        sweep_price=price + 1 if direction == SweepDirection.BEARISH else price - 1,
        close_price=price - 1 if direction == SweepDirection.BEARISH else price + 1,
        penetration_distance=1.0,
        penetration_ratio=0.01,
        reclaimed_level=True,
        confirmed=True,
        reason="test sweep",
    )


def _structure_break(
    *,
    direction: BreakDirection,
    swing_price: float,
    break_price: float,
    break_candle_index: int,
) -> StructureBreakResult:
    """
    A confirmed BOS whose broken-swing/close prices derive the Grade B
    fallback zone: swing_price is the structural boundary that was
    broken, break_price is the break candle's close (the same value
    _build_bos_zone reads as close_price).
    """
    break_ts = UTC_NOW + timedelta(minutes=break_candle_index)
    swing_type = SwingType.HIGH if direction == BreakDirection.BULLISH else SwingType.LOW
    broken_swing = SwingPoint(
        swing_id=f"swing-{direction.value}",
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        timestamp=UTC_NOW - timedelta(minutes=20),
        candle_index=3,
        swing_type=swing_type,
        price=swing_price,
        left_strength=3,
        right_strength=3,
        confirmed=True,
    )
    displacement = DisplacementResult(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        candle_timestamp=break_ts,
        candle_index=break_candle_index,
        direction=direction,
        body_ratio=0.8,
        candle_range=5.0,
        body_size=4.0,
        close_location_value=0.9 if direction == BreakDirection.BULLISH else 0.1,
        volume_confirmed=True,
        strong_close=True,
        confirmed=True,
        reason="test displacement",
    )
    sweep_direction = SweepDirection.BULLISH if direction == BreakDirection.BULLISH else SweepDirection.BEARISH
    return StructureBreakResult(
        break_id=f"break-{direction.value}-{break_candle_index}",
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        break_type="BOS",
        direction=direction,
        broken_swing=broken_swing,
        break_candle_timestamp=break_ts,
        break_candle_index=break_candle_index,
        break_price=swing_price,
        close_price=break_price,
        displacement=displacement,
        preceding_liquidity_sweep=_sweep(sweep_direction),
        strong_close_beyond_structure=True,
        wick_only_break=False,
        confirmation=BreakConfirmation.CONFIRMED,
        reason="test BOS",
    )


class TestEvaluateIfvgBuy:
    def test_bearish_fvg_flips_and_is_retested_for_a_buy(self):
        # A bearish FVG (resistance) sits at [100, 105], created at
        # index 2. Candles 0-4 stay at/below the zone (close=103, never
        # closes through 105). Candle 5 closes at 110 (fully above 105
        # -> flips it into bullish support). Candle 8 trades back down
        # into [100, 105] (low=102, high=104) -> retest confirmed.
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=2)
        candles = [_candle(i, 102, 104, 101, 103) for i in range(0, 5)]  # indices 0-4, close=103 <= 105
        candles.append(_candle(5, 108, 111, 108, 110))  # flip candle: close=110 > 105
        candles += [_candle(i, 112, 113, 111, 112) for i in range(6, 8)]  # indices 6-7, still above
        candles.append(_candle(8, 104, 104, 102, 103))  # retest: low=102 <= 105, high=104 >= 100

        result = evaluate_ifvg(candles, [zone], expected_direction="BUY")

        assert result.passed is True
        assert result.source_fvg.zone_id == zone.zone_id
        assert result.ifvg_zone.direction == "BUY"
        assert result.flip_candle_index == 5
        assert result.retest_candle_index == 8

    def test_bullish_fvg_never_flips_a_buy(self):
        # A bullish (BUY) FVG is not an obstacle for a BUY setup -- only
        # an opposing (SELL) FVG can flip into a BUY IFVG.
        zone = _fvg_zone(direction="BUY", lower_price=100.0, upper_price=105.0, source_candle_index=2)
        candles = [_candle(i, 108, 109, 107, 108) for i in range(0, 10)]

        result = evaluate_ifvg(candles, [zone], expected_direction="BUY")

        assert result.passed is False
        assert result.ifvg_zone is None

    def test_no_flip_yet_fails_with_a_clear_reason(self):
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=2)
        # Price never closes above 105.
        candles = [_candle(i, 102, 104, 101, 103) for i in range(0, 10)]

        result = evaluate_ifvg(candles, [zone], expected_direction="BUY")

        assert result.passed is False
        assert result.flip_candle_index is None
        assert "closed through" in result.reason.lower()

    def test_flip_without_retest_fails(self):
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=2)
        candles = [_candle(i, 102, 104, 101, 103) for i in range(0, 5)]  # close=103 <= 105
        candles.append(_candle(5, 108, 111, 108, 110))  # flip candle
        # No later candle ever trades back down into [100, 105].
        candles += [_candle(i, 112, 113, 111, 112) for i in range(6, 10)]

        result = evaluate_ifvg(candles, [zone], expected_direction="BUY")

        assert result.passed is False
        assert result.flip_candle_index is not None
        assert result.retest_candle_index is None
        assert "not retested" in result.reason.lower()

    def test_flip_must_occur_strictly_after_the_source_candle(self):
        # A candle at or before the zone's own source candle can never
        # count as the flip, even if its close numerically qualifies.
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=5)
        candles = [_candle(i, 108, 109, 107, 108) for i in range(0, 5)]  # indices 0-4, close=108 > 105
        candles.append(_candle(5, 103, 104, 102, 103))  # the zone's own source candle
        candles += [_candle(i, 103, 104, 102, 103) for i in range(6, 10)]

        result = evaluate_ifvg(candles, [zone], expected_direction="BUY")

        assert result.passed is False
        assert result.flip_candle_index is None


class TestEvaluateIfvgSell:
    def test_bullish_fvg_flips_and_is_retested_for_a_sell(self):
        # A bullish FVG (support) sits at [100, 105], created at index
        # 2. Candle 5 closes at 95 (fully below 100 -> flips it into
        # bearish resistance). Candle 8 retests [100, 105].
        zone = _fvg_zone(direction="BUY", lower_price=100.0, upper_price=105.0, source_candle_index=2)
        candles = [_candle(i, 92, 93, 91, 92) for i in range(0, 5)]
        candles.append(_candle(5, 92, 96, 90, 95))  # flip candle: close=95 < 100
        candles += [_candle(i, 90, 91, 89, 90) for i in range(6, 8)]
        candles.append(_candle(8, 102, 104, 101, 102))  # retest

        result = evaluate_ifvg(candles, [zone], expected_direction="SELL")

        assert result.passed is True
        assert result.ifvg_zone.direction == "SELL"

    def test_bearish_fvg_never_flips_a_sell(self):
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=2)
        candles = [_candle(i, 92, 93, 91, 92) for i in range(0, 10)]

        result = evaluate_ifvg(candles, [zone], expected_direction="SELL")

        assert result.passed is False


class TestEvaluateIfvgSelection:
    def test_most_recently_created_qualifying_zone_is_preferred(self):
        older_zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=1)
        newer_zone = _fvg_zone(direction="SELL", lower_price=200.0, upper_price=205.0, source_candle_index=6)

        candles = [_candle(i, 108, 109, 107, 108) for i in range(0, 8)]
        candles.append(_candle(8, 208, 211, 208, 210))  # flips the NEWER zone (closes > 205)
        candles.append(_candle(9, 203, 204, 202, 203))  # retests the NEWER zone

        result = evaluate_ifvg(candles, [older_zone, newer_zone], expected_direction="BUY")

        assert result.passed is True
        assert result.source_fvg.zone_id == newer_zone.zone_id


class TestEvaluateIfvgEmptyInputs:
    def test_no_candles_fails(self):
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=2)
        result = evaluate_ifvg([], [zone], expected_direction="BUY")
        assert result.passed is False

    def test_no_fvg_zones_fails(self):
        candles = [_candle(i, 100, 101, 99, 100) for i in range(5)]
        result = evaluate_ifvg(candles, [], expected_direction="BUY")
        assert result.passed is False
        assert result.ifvg_zone is None


class TestEvaluateIfvgWithStructureBreak:
    """
    Behavior specific to supplying `structure_break`: the Grade A
    validity window, the Grade B BOS-zone retest fallback, and grading.
    """

    def test_grade_a_within_window_is_graded_a(self):
        # BOS break at index 0 (swing_price=90 broken, close=95). The
        # IFVG flip+retest confirms well within IFVG_VALIDITY_WINDOW_CANDLES.
        structure_break = _structure_break(
            direction=BreakDirection.BULLISH, swing_price=90.0, break_price=95.0, break_candle_index=0
        )
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=1)
        candles = [_candle(0, 94, 96, 93, 95)]  # the break candle itself
        candles += [_candle(i, 102, 104, 101, 103) for i in range(1, 4)]  # close=103 <= 105
        candles.append(_candle(4, 108, 111, 108, 110))  # flip: close=110 > 105
        candles.append(_candle(5, 104, 104, 102, 103))  # retest

        result = evaluate_ifvg(
            candles, [zone], expected_direction="BUY", structure_break=structure_break
        )

        assert result.passed is True
        assert result.entry_grade == "A"
        assert result.ifvg_zone is not None
        assert result.bos_zone is None

    def test_grade_a_window_expires_falls_back_to_grade_b(self):
        # No opposing FVG at all -- Grade A can never confirm -- but a
        # later candle retests the BOS-derived zone [90, 95] within
        # BOS_ZONE_RETEST_VALIDITY_WINDOW_CANDLES, so Grade B passes.
        structure_break = _structure_break(
            direction=BreakDirection.BULLISH, swing_price=90.0, break_price=95.0, break_candle_index=0
        )
        candles = [_candle(0, 94, 96, 93, 95)]  # break candle
        candles += [_candle(i, 110, 112, 109, 111) for i in range(1, 10)]  # away from the zone
        candles.append(_candle(10, 93, 94, 91, 92))  # retests [90, 95]: low=91<=95, high=94>=90

        result = evaluate_ifvg(candles, [], expected_direction="BUY", structure_break=structure_break)

        assert result.passed is True
        assert result.entry_grade == "B"
        assert result.bos_zone is not None
        assert result.bos_zone.lower_price == 90.0
        assert result.bos_zone.upper_price == 95.0
        assert result.bos_zone_retest_candle_index == 10
        assert result.ifvg_zone is None

    def test_grade_a_confirms_outside_window_does_not_count_falls_back_to_grade_b(self):
        # The IFVG flip+retest would eventually confirm, but only after
        # IFVG_VALIDITY_WINDOW_CANDLES has elapsed -- Grade A must not
        # accept it, so the result should fall back to Grade B (or
        # reject) instead of silently ignoring the window.
        structure_break = _structure_break(
            direction=BreakDirection.BULLISH, swing_price=90.0, break_price=95.0, break_candle_index=0
        )
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=1)
        late_index = IFVG_VALIDITY_WINDOW_CANDLES + 5
        candles = [_candle(0, 94, 96, 93, 95)]
        candles += [_candle(i, 102, 104, 101, 103) for i in range(1, late_index)]
        candles.append(_candle(late_index, 108, 111, 108, 110))  # flip, but outside the window
        candles.append(_candle(late_index + 1, 104, 104, 102, 103))  # retest, also outside the window

        result = evaluate_ifvg(
            candles, [zone], expected_direction="BUY", structure_break=structure_break
        )

        # Grade A never confirmed inside its window; the BOS-zone [90, 95]
        # was also never retested in this fixture (price stayed at/above
        # 101-110 throughout) -- so this is a clean reject, not a
        # window-boundary false pass.
        assert result.passed is False
        assert result.entry_grade is None
        assert "window expired" in result.reason.lower()

    def test_neither_path_confirms_rejects_with_a_specific_reason(self):
        structure_break = _structure_break(
            direction=BreakDirection.BULLISH, swing_price=90.0, break_price=95.0, break_candle_index=0
        )
        candles = [_candle(0, 94, 96, 93, 95)]
        # Price drifts far away and never retests [90, 95], and no FVG exists.
        candles += [
            _candle(i, 200 + i, 201 + i, 199 + i, 200 + i)
            for i in range(1, BOS_ZONE_RETEST_VALIDITY_WINDOW_CANDLES + 5)
        ]

        result = evaluate_ifvg(candles, [], expected_direction="BUY", structure_break=structure_break)

        assert result.passed is False
        assert result.entry_grade is None
        assert result.bos_zone is None
        assert "window expired" in result.reason.lower()
        assert "fallback also did not confirm" in result.reason.lower()

    def test_grade_b_zone_and_index_are_none_when_grade_a_passes(self):
        structure_break = _structure_break(
            direction=BreakDirection.BULLISH, swing_price=90.0, break_price=95.0, break_candle_index=0
        )
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=1)
        candles = [_candle(0, 94, 96, 93, 95)]
        candles += [_candle(i, 102, 104, 101, 103) for i in range(1, 4)]
        candles.append(_candle(4, 108, 111, 108, 110))
        candles.append(_candle(5, 104, 104, 102, 103))

        result = evaluate_ifvg(
            candles, [zone], expected_direction="BUY", structure_break=structure_break
        )

        assert result.passed is True
        assert result.entry_grade == "A"
        assert result.bos_zone is None
        assert result.bos_zone_retest_candle_index is None

    def test_bearish_break_bos_zone_orders_prices_correctly(self):
        # For a bearish break the swing (resistance broken) sits ABOVE
        # the close, so the zone's lower/upper must still order
        # correctly (lower < upper) regardless of break direction.
        structure_break = _structure_break(
            direction=BreakDirection.BEARISH, swing_price=110.0, break_price=105.0, break_candle_index=0
        )
        candles = [_candle(0, 108, 109, 104, 105)]
        candles += [_candle(i, 90, 91, 89, 90) for i in range(1, 10)]
        candles.append(_candle(10, 107, 109, 106, 108))  # retests [105, 110]

        result = evaluate_ifvg(candles, [], expected_direction="SELL", structure_break=structure_break)

        assert result.passed is True
        assert result.entry_grade == "B"
        assert result.bos_zone.lower_price == 105.0
        assert result.bos_zone.upper_price == 110.0
        assert result.bos_zone.direction == "SELL"

    def test_structure_invalidated_before_grade_a_retest_falls_through_to_grade_b(self):
        # BOS breaks swing_price=90 (bullish, close=95 at index 0). The
        # IFVG flip+retest would confirm at index 5, but index 3 closes
        # back below 90 first -- the structural premise is negated
        # before Grade A can confirm, so it must not pass. A later
        # candle (index 8) then retests the BOS zone [90, 95] itself,
        # after the invalidation -- Grade B's own check runs
        # independently against the invalidation window ending at ITS
        # retest index (8), and 3 <= 8, so Grade B is invalidated too:
        # overall rejection with a reason naming invalidation.
        structure_break = _structure_break(
            direction=BreakDirection.BULLISH, swing_price=90.0, break_price=95.0, break_candle_index=0
        )
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=1)
        candles = [_candle(0, 94, 96, 93, 95)]  # break candle
        candles.append(_candle(1, 102, 104, 101, 103))  # close=103 <= 105
        candles.append(_candle(2, 102, 104, 101, 103))
        candles.append(_candle(3, 85, 86, 84, 85))  # closes below 90 -> invalidation
        candles.append(_candle(4, 108, 111, 108, 110))  # flip: close=110 > 105
        candles.append(_candle(5, 104, 104, 102, 103))  # would-be Grade A retest

        result = evaluate_ifvg(
            candles, [zone], expected_direction="BUY", structure_break=structure_break
        )

        assert result.passed is False
        assert result.entry_grade is None
        assert "structure invalidated" in result.reason.lower()

    def test_structure_invalidated_before_grade_b_retest_rejects_outright(self):
        # No opposing FVG at all (Grade A can never confirm). A later
        # candle retests the BOS zone [90, 95], but an earlier candle
        # already closed back below 90 -- invalidating the structure
        # before that retest -- so Grade B must not pass either.
        structure_break = _structure_break(
            direction=BreakDirection.BULLISH, swing_price=90.0, break_price=95.0, break_candle_index=0
        )
        candles = [_candle(0, 94, 96, 93, 95)]  # break candle
        candles += [_candle(i, 110, 112, 109, 111) for i in range(1, 5)]  # away from the zone
        candles.append(_candle(5, 85, 86, 84, 85))  # closes below 90 -> invalidation
        candles += [_candle(i, 110, 112, 109, 111) for i in range(6, 10)]
        candles.append(_candle(10, 93, 94, 91, 92))  # retests [90, 95] AFTER invalidation

        result = evaluate_ifvg(candles, [], expected_direction="BUY", structure_break=structure_break)

        assert result.passed is False
        assert result.entry_grade is None
        assert result.bos_zone is None
        assert "structure invalidated" in result.reason.lower()

    def test_invalidation_after_grade_a_retest_does_not_block_grade_a(self):
        # Structure is invalidated on a candle AFTER the Grade A retest
        # already confirmed -- invalidation only matters if it happens
        # before confirmation, never after, so Grade A must still pass.
        structure_break = _structure_break(
            direction=BreakDirection.BULLISH, swing_price=90.0, break_price=95.0, break_candle_index=0
        )
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=1)
        candles = [_candle(0, 94, 96, 93, 95)]  # break candle
        candles += [_candle(i, 102, 104, 101, 103) for i in range(1, 4)]  # close=103 <= 105
        candles.append(_candle(4, 108, 111, 108, 110))  # flip: close=110 > 105
        candles.append(_candle(5, 104, 104, 102, 103))  # retest confirms Grade A
        candles.append(_candle(6, 85, 86, 84, 85))  # closes below 90, but AFTER the retest

        result = evaluate_ifvg(
            candles, [zone], expected_direction="BUY", structure_break=structure_break
        )

        assert result.passed is True
        assert result.entry_grade == "A"
        assert result.retest_candle_index == 5

    def test_bearish_structure_invalidated_before_retest_falls_through(self):
        # Mirror of the bullish case: a bearish break's structure is
        # invalidated by a close back ABOVE the broken swing price.
        structure_break = _structure_break(
            direction=BreakDirection.BEARISH, swing_price=110.0, break_price=105.0, break_candle_index=0
        )
        zone = _fvg_zone(direction="BUY", lower_price=100.0, upper_price=105.0, source_candle_index=1)
        candles = [_candle(0, 108, 109, 104, 105)]  # break candle
        candles.append(_candle(1, 92, 93, 91, 92))
        candles.append(_candle(2, 92, 93, 91, 92))
        candles.append(_candle(3, 112, 113, 111, 112))  # closes above 110 -> invalidation
        candles.append(_candle(4, 92, 96, 90, 91))  # flip: close=91 < 100
        candles.append(_candle(5, 102, 104, 101, 102))  # would-be Grade A retest

        result = evaluate_ifvg(
            candles, [zone], expected_direction="SELL", structure_break=structure_break
        )

        assert result.passed is False
        assert result.entry_grade is None
        assert "structure invalidated" in result.reason.lower()

    def test_no_structure_break_preserves_unbounded_legacy_behavior(self):
        # When structure_break is not supplied at all, Grade A runs with
        # no validity window (matching the function's pre-fallback
        # behaviour) and no grade is ever attached.
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=2)
        late_index = IFVG_VALIDITY_WINDOW_CANDLES + 10
        candles = [_candle(i, 102, 104, 101, 103) for i in range(0, late_index)]
        candles.append(_candle(late_index, 108, 111, 108, 110))  # flip, far beyond IFVG_VALIDITY_WINDOW_CANDLES
        candles.append(_candle(late_index + 1, 104, 104, 102, 103))  # retest

        result = evaluate_ifvg(candles, [zone], expected_direction="BUY")

        assert result.passed is True
        assert result.entry_grade is None
