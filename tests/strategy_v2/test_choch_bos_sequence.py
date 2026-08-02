"""
Unit tests for app.strategy_v2.choch_bos_sequence.

These tests drive the real DisplacementDetector, CHOCHDetector and
BOSDetector over hand-built candle/swing/sweep fixtures: no detector is
mocked and no StructureBreakResult is hand-constructed, so the
Sweep -> CHoCH -> BOS -> Entry sequencing is proven against genuinely
detected structural breaks.
"""

from datetime import datetime, timedelta, timezone

from app.liquidity.results import (
    LiquidityLevel,
    LiquiditySide,
    LiquidityStrength,
    LiquiditySweepResult,
    LiquidityType,
    SweepDirection,
)
from app.market_structure.bos_detector import BOSDetector
from app.market_structure.choch_detector import CHOCHDetector
from app.market_structure.displacement import DisplacementDetector
from app.market_structure.results import (
    MarketStructureResult,
    SwingPoint,
    SwingType,
    TrendDirection,
)
from app.market_structure.shift_results import BreakDirection, StructureBreakType
from app.strategy_v2.choch_bos_sequence import evaluate_choch_bos_sequence
from app.models.candle import Candle

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYMBOL = "BTC-USDT"
TIMEFRAME = "15m"

VOLUME_EMA = 100.0
QUIET_VOLUME = 50.0
DISPLACEMENT_VOLUME = 300.0


def _candle(
    index: int, open_: float, high: float, low: float, close: float, volume: float
) -> Candle:
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
    )


def _swing(
    swing_id: str, swing_type: SwingType, price: float, candle_index: int
) -> SwingPoint:
    return SwingPoint(
        swing_id=swing_id,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        timestamp=UTC_NOW + timedelta(minutes=candle_index),
        candle_index=candle_index,
        swing_type=swing_type,
        price=price,
        left_strength=3,
        right_strength=3,
        confirmed=True,
    )


def _sweep(
    direction: SweepDirection, candle_index: int, price: float = 110.0
) -> LiquiditySweepResult:
    timestamp = UTC_NOW + timedelta(minutes=candle_index)
    level = LiquidityLevel(
        liquidity_id=f"level-{direction.value}-{candle_index}",
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        liquidity_type=(
            LiquidityType.EQUAL_HIGH
            if direction == SweepDirection.BEARISH
            else LiquidityType.EQUAL_LOW
        ),
        liquidity_side=(
            LiquiditySide.BUY_SIDE
            if direction == SweepDirection.BEARISH
            else LiquiditySide.SELL_SIDE
        ),
        price=price,
        start_timestamp=timestamp - timedelta(minutes=10),
        end_timestamp=timestamp - timedelta(minutes=10),
        source_timestamps=[timestamp - timedelta(minutes=10)],
        touch_count=2,
        strength=LiquidityStrength.STRONG,
        active=True,
    )
    return LiquiditySweepResult(
        sweep_id=f"sweep-{direction.value}-{candle_index}",
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        direction=direction,
        liquidity_level=level,
        sweep_candle_timestamp=timestamp,
        sweep_candle_index=candle_index,
        sweep_price=price + 1 if direction == SweepDirection.BEARISH else price - 1,
        close_price=price - 1 if direction == SweepDirection.BEARISH else price + 1,
        penetration_distance=1.0,
        penetration_ratio=0.01,
        reclaimed_level=True,
        confirmed=True,
        reason="test sweep",
    )


def _structure(trend: TrendDirection) -> MarketStructureResult:
    return MarketStructureResult(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        swings=[],
        classified_swings=[],
        latest_swing_high=None,
        latest_swing_low=None,
        trend_direction=trend,
        higher_high_count=0,
        higher_low_count=0,
        lower_high_count=0,
        lower_low_count=0,
        equal_high_count=0,
        equal_low_count=0,
    )


def _displacement_detector() -> DisplacementDetector:
    """Thresholds the bullish/bearish displacement fixtures below are built to clear."""
    return DisplacementDetector(
        minimum_body_ratio=0.6,
        bullish_close_location_minimum=0.75,
        bearish_close_location_maximum=0.25,
    )


def _bullish_displacement_candle(index: int, open_: float, close: float) -> Candle:
    """A wide-bodied bullish candle closing near its high, on above-EMA volume."""
    return _candle(
        index,
        open_=open_,
        high=close + 1,
        low=open_ - 1,
        close=close,
        volume=DISPLACEMENT_VOLUME,
    )


def _bearish_displacement_candle(index: int, open_: float, close: float) -> Candle:
    """A wide-bodied bearish candle closing near its low, on above-EMA volume."""
    return _candle(
        index,
        open_=open_,
        high=open_ + 1,
        low=close - 1,
        close=close,
        volume=DISPLACEMENT_VOLUME,
    )


def _quiet_candle(index: int, price: float) -> Candle:
    """A narrow, below-EMA-volume candle that can never be a displacement."""
    return _candle(
        index,
        open_=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=QUIET_VOLUME,
    )


def _downtrend_prefix(count: int) -> list[Candle]:
    return [_quiet_candle(i, 100 - i) for i in range(count)]


def _evaluate(
    candles,
    structure,
    swings,
    sweeps,
    expected_direction: str,
):
    return evaluate_choch_bos_sequence(
        candles=candles,
        structure=structure,
        swings=swings,
        liquidity_sweeps=sweeps,
        displacement_detector=_displacement_detector(),
        choch_detector=CHOCHDetector(),
        bos_detector=BOSDetector(),
        expected_direction=expected_direction,
        volume_ema_values=[VOLUME_EMA] * len(candles),
    )


def _bullish_sequence_candles() -> list[Candle]:
    """
    Downtrend, a bullish CHoCH break at index 10, a bullish continuation
    break at index 14, and a further bullish break at index 18.
    """
    candles = _downtrend_prefix(10)
    candles.append(_bullish_displacement_candle(10, open_=104, close=111))
    candles += [_quiet_candle(i, 111) for i in range(11, 14)]
    candles.append(_bullish_displacement_candle(14, open_=112, close=125))
    candles += [_quiet_candle(i, 125) for i in range(15, 18)]
    candles.append(_bullish_displacement_candle(18, open_=126, close=139))
    return candles


def _bullish_sequence_swings() -> list[SwingPoint]:
    """
    Swing highs staggered so each successive displacement candle breaks a
    different, progressively higher swing.
    """
    return [
        _swing("swing-HIGH-choch", SwingType.HIGH, 105.0, candle_index=3),
        _swing("swing-HIGH-bos", SwingType.HIGH, 120.0, candle_index=12),
        _swing("swing-HIGH-late", SwingType.HIGH, 130.0, candle_index=16),
    ]


class TestFullValidFlow:
    def test_sweep_choch_bos_sequence_passes(self):
        candles = _bullish_sequence_candles()
        swings = _bullish_sequence_swings()
        sweeps = [_sweep(SweepDirection.BULLISH, candle_index=5)]

        result = _evaluate(
            candles, _structure(TrendDirection.BEARISH), swings, sweeps, "BUY"
        )

        assert result.passed is True
        assert result.choch is not None
        assert result.entry_break is not None

    def test_entry_break_is_the_bos_not_the_choch(self):
        candles = _bullish_sequence_candles()
        swings = _bullish_sequence_swings()
        sweeps = [_sweep(SweepDirection.BULLISH, candle_index=5)]

        result = _evaluate(
            candles, _structure(TrendDirection.BEARISH), swings, sweeps, "BUY"
        )

        assert result.entry_break.break_type == StructureBreakType.BOS
        assert result.choch.break_type == StructureBreakType.CHOCH
        assert result.entry_break.break_candle_index == 14
        assert result.choch.break_candle_index == 10

    def test_entry_bos_occurs_strictly_after_the_choch(self):
        candles = _bullish_sequence_candles()
        swings = _bullish_sequence_swings()
        sweeps = [_sweep(SweepDirection.BULLISH, candle_index=5)]

        result = _evaluate(
            candles, _structure(TrendDirection.BEARISH), swings, sweeps, "BUY"
        )

        assert (
            result.entry_break.break_candle_timestamp
            > result.choch.break_candle_timestamp
        )

    def test_both_breaks_are_bullish_for_a_buy(self):
        candles = _bullish_sequence_candles()
        swings = _bullish_sequence_swings()
        sweeps = [_sweep(SweepDirection.BULLISH, candle_index=5)]

        result = _evaluate(
            candles, _structure(TrendDirection.BEARISH), swings, sweeps, "BUY"
        )

        assert result.choch.direction == BreakDirection.BULLISH
        assert result.entry_break.direction == BreakDirection.BULLISH

    def test_breaks_carry_their_own_preceding_sweep(self):
        candles = _bullish_sequence_candles()
        swings = _bullish_sequence_swings()
        sweeps = [_sweep(SweepDirection.BULLISH, candle_index=5)]

        result = _evaluate(
            candles, _structure(TrendDirection.BEARISH), swings, sweeps, "BUY"
        )

        for break_result in (result.choch, result.entry_break):
            sweep = break_result.preceding_liquidity_sweep
            assert sweep is not None
            assert sweep.confirmed is True
            assert sweep.direction == SweepDirection.BULLISH
            assert sweep.sweep_candle_timestamp < break_result.break_candle_timestamp


class TestNoChoch:
    def test_lone_bos_without_choch_is_ignored(self):
        """
        RANGE structure suppresses CHoCH entirely while leaving the same
        candles detectable as a BOS, isolating the "no CHoCH" rule.
        """
        candles = _bullish_sequence_candles()
        swings = _bullish_sequence_swings()
        sweeps = [_sweep(SweepDirection.BULLISH, candle_index=5)]

        bos_results = BOSDetector().detect(
            candles,
            swings,
            _displacement_detector().detect(candles, [VOLUME_EMA] * len(candles)),
            sweeps,
        )
        assert bos_results, "fixture must genuinely produce a BOS for this test"

        result = _evaluate(
            candles, _structure(TrendDirection.RANGE), swings, sweeps, "BUY"
        )

        assert result.passed is False
        assert result.choch is None
        assert result.entry_break is None
        assert "no CHoCH" in result.reason

    def test_unknown_structure_also_ignores_a_lone_bos(self):
        candles = _bullish_sequence_candles()
        swings = _bullish_sequence_swings()
        sweeps = [_sweep(SweepDirection.BULLISH, candle_index=5)]

        result = _evaluate(
            candles, _structure(TrendDirection.UNKNOWN), swings, sweeps, "BUY"
        )

        assert result.passed is False
        assert result.entry_break is None

    def test_missing_sweep_produces_no_breaks_at_all(self):
        candles = _bullish_sequence_candles()
        swings = _bullish_sequence_swings()

        result = _evaluate(candles, _structure(TrendDirection.BEARISH), swings, [], "BUY")

        assert result.passed is False
        assert result.choch is None
        assert result.entry_break is None


class TestChochWithoutFollowingBos:
    def test_choch_with_no_later_bos_fails(self):
        """Only one displacement candle exists, so the CHoCH has no continuation break."""
        candles = _downtrend_prefix(10)
        candles.append(_bullish_displacement_candle(10, open_=104, close=111))
        candles += [_quiet_candle(i, 111) for i in range(11, 15)]
        swings = [_swing("swing-HIGH-choch", SwingType.HIGH, 105.0, candle_index=3)]
        sweeps = [_sweep(SweepDirection.BULLISH, candle_index=5)]

        result = _evaluate(
            candles, _structure(TrendDirection.BEARISH), swings, sweeps, "BUY"
        )

        assert result.passed is False
        assert result.choch is not None
        assert result.choch.break_type == StructureBreakType.CHOCH
        assert result.entry_break is None
        assert "no confirmed BOS" in result.reason


class TestWrongDirectionChoch:
    def test_bearish_choch_does_not_satisfy_a_buy(self):
        """An uptrend that breaks down gives a bearish CHoCH; a BUY must still fail."""
        candles = [_quiet_candle(i, 100 + i) for i in range(10)]
        candles.append(_bearish_displacement_candle(10, open_=106, close=95))
        candles += [_quiet_candle(i, 95) for i in range(11, 14)]
        candles.append(_bearish_displacement_candle(14, open_=94, close=81))
        swings = [
            _swing("swing-LOW-choch", SwingType.LOW, 99.0, candle_index=3),
            _swing("swing-LOW-bos", SwingType.LOW, 85.0, candle_index=12),
        ]
        sweeps = [_sweep(SweepDirection.BEARISH, candle_index=5)]
        structure = _structure(TrendDirection.BULLISH)

        sell_result = _evaluate(candles, structure, swings, sweeps, "SELL")
        assert sell_result.passed is True
        assert sell_result.choch.direction == BreakDirection.BEARISH

        buy_result = _evaluate(candles, structure, swings, sweeps, "BUY")

        assert buy_result.passed is False
        assert buy_result.choch is None
        assert buy_result.entry_break is None
        assert "no CHoCH" in buy_result.reason

    def test_bullish_choch_does_not_satisfy_a_sell(self):
        candles = _bullish_sequence_candles()
        swings = _bullish_sequence_swings()
        sweeps = [_sweep(SweepDirection.BULLISH, candle_index=5)]

        result = _evaluate(
            candles, _structure(TrendDirection.BEARISH), swings, sweeps, "SELL"
        )

        assert result.passed is False
        assert result.choch is None
        assert result.entry_break is None


class TestEmptyCandles:
    def test_empty_candles_fails_without_crashing(self):
        result = evaluate_choch_bos_sequence(
            candles=[],
            structure=_structure(TrendDirection.BEARISH),
            swings=[],
            liquidity_sweeps=[],
            displacement_detector=_displacement_detector(),
            choch_detector=CHOCHDetector(),
            bos_detector=BOSDetector(),
            expected_direction="BUY",
            volume_ema_values=[],
        )

        assert result.passed is False
        assert result.choch is None
        assert result.entry_break is None
        assert "No candles" in result.reason


class TestBosBeforeChochOrdering:
    def test_bos_before_choch_does_not_satisfy_the_sequence(self):
        """
        A bearish BOS at index 6 precedes the bullish CHoCH at index 10,
        and no bullish break follows it, so ordering alone must reject.
        """
        candles = _downtrend_prefix(6)
        candles.append(_bearish_displacement_candle(6, open_=95, close=84))
        candles += [_quiet_candle(i, 84) for i in range(7, 10)]
        candles.append(_bullish_displacement_candle(10, open_=104, close=111))
        candles += [_quiet_candle(i, 111) for i in range(11, 14)]

        swings = [
            _swing("swing-LOW-early", SwingType.LOW, 90.0, candle_index=3),
            _swing("swing-HIGH-choch", SwingType.HIGH, 105.0, candle_index=8),
        ]
        sweeps = [
            _sweep(SweepDirection.BEARISH, candle_index=4),
            _sweep(SweepDirection.BULLISH, candle_index=9),
        ]
        structure = _structure(TrendDirection.BEARISH)

        displacements = _displacement_detector().detect(
            candles, [VOLUME_EMA] * len(candles)
        )
        bos_results = BOSDetector().detect(candles, swings, displacements, sweeps)
        choch_results = CHOCHDetector().detect(
            candles, structure, swings, displacements, sweeps
        )
        assert [b.break_candle_index for b in bos_results] == [6, 10]
        assert [c.break_candle_index for c in choch_results] == [10]

        result = _evaluate(candles, structure, swings, sweeps, "BUY")

        assert result.passed is False
        assert result.choch is not None
        assert result.choch.break_candle_index == 10
        assert result.entry_break is None
