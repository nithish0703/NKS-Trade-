"""
Unit tests for app.validators.context_validator.PreRiskValidator.
"""

from datetime import datetime, timedelta, timezone

from app.indicators.results import IndicatorSnapshot
from app.liquidity.results import (
    LiquidityLevel,
    LiquiditySide,
    LiquiditySweepResult,
    LiquidityStrength,
    LiquidityType,
    SweepDirection,
)
from app.market_structure.results import HigherTimeframeBias, HigherTimeframeBiasResult, MarketStructureResult, SwingPoint, SwingType, TrendDirection
from app.market_structure.shift_results import (
    BreakConfirmation,
    BreakDirection,
    DisplacementResult,
    StructureBreakResult,
    StructureBreakType,
)
from app.models.candle import Candle
from app.models.market_context import MarketContext
from app.validators.btc_alignment import BTCAlignmentValidator
from app.validators.candle_quality import CandleQualityValidator
from app.validators.context_validator import PreRiskValidator
from app.validators.fake_breakout_filter import FakeBreakoutFilter
from app.validators.market_regime import MarketRegimeValidator
from app.validators.session_filter import SessionFilter

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)  # London session


def _candle(index: int, open_: float, high: float, low: float, close: float, volume=100.0) -> Candle:
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        symbol="ETH-USDT",
        timeframe="15m",
    )


def _normal_candles(count: int) -> list[Candle]:
    return [_candle(i, 100, 110, 100, 105, volume=100.0) for i in range(count)]


def _snapshot(**overrides) -> IndicatorSnapshot:
    fields = dict(
        adx=30.0,
        ema200_slope_direction="BULLISH",
        atr=10.0,
        atr_expansion_ratio=1.5,
        ema20=110.0,
        ema50=100.0,
        volume_ratio=1.5,
    )
    fields.update(overrides)
    return IndicatorSnapshot(**fields)


def _btc_structure(trend=TrendDirection.BULLISH) -> MarketStructureResult:
    return MarketStructureResult(
        symbol="BTC-USDT",
        timeframe="4h",
        swings=[],
        classified_swings=[],
        trend_direction=trend,
        higher_high_count=0,
        higher_low_count=0,
        lower_high_count=0,
        lower_low_count=0,
        equal_high_count=0,
        equal_low_count=0,
    )


def _btc_bias(final_bias=HigherTimeframeBias.BULLISH) -> HigherTimeframeBiasResult:
    return HigherTimeframeBiasResult(
        primary_timeframe="4h",
        secondary_timeframe="1h",
        primary_trend=TrendDirection.UNKNOWN,
        secondary_trend=TrendDirection.UNKNOWN,
        final_bias=final_bias,
        aligned=True,
        reason="test",
    )


def _swing(price: float) -> SwingPoint:
    return SwingPoint(
        swing_id=f"swing-{price}",
        symbol="ETH-USDT",
        timeframe="15m",
        timestamp=UTC_NOW - timedelta(minutes=30),
        candle_index=3,
        swing_type=SwingType.HIGH,
        price=price,
        left_strength=3,
        right_strength=3,
        confirmed=True,
    )


def _sweep(timestamp) -> LiquiditySweepResult:
    level = LiquidityLevel(
        liquidity_id="level-1",
        symbol="ETH-USDT",
        timeframe="15m",
        liquidity_type=LiquidityType.EQUAL_LOW,
        liquidity_side=LiquiditySide.SELL_SIDE,
        price=90.0,
        start_timestamp=timestamp - timedelta(minutes=20),
        end_timestamp=timestamp - timedelta(minutes=20),
        source_timestamps=[timestamp - timedelta(minutes=20)],
        touch_count=2,
        strength=LiquidityStrength.STRONG,
        active=True,
    )
    return LiquiditySweepResult(
        sweep_id="sweep-1",
        symbol="ETH-USDT",
        timeframe="15m",
        direction=SweepDirection.BULLISH,
        liquidity_level=level,
        sweep_candle_timestamp=timestamp,
        sweep_candle_index=0,
        sweep_price=89.0,
        close_price=91.0,
        penetration_distance=1.0,
        penetration_ratio=0.01,
        reclaimed_level=True,
        confirmed=True,
        reason="test sweep",
    )


def _structure_break(sweep) -> StructureBreakResult:
    displacement = DisplacementResult(
        symbol="ETH-USDT",
        timeframe="15m",
        candle_timestamp=UTC_NOW - timedelta(minutes=10),
        candle_index=5,
        direction=BreakDirection.BULLISH,
        body_ratio=0.8,
        candle_range=10.0,
        body_size=8.0,
        close_location_value=0.9,
        volume_confirmed=True,
        strong_close=True,
        confirmed=True,
        reason="test",
    )
    return StructureBreakResult(
        break_id="break-1",
        symbol="ETH-USDT",
        timeframe="15m",
        break_type=StructureBreakType.BOS,
        direction=BreakDirection.BULLISH,
        broken_swing=_swing(100.0),
        break_candle_timestamp=UTC_NOW - timedelta(minutes=10),
        break_candle_index=5,
        break_price=100.0,
        close_price=105.0,
        displacement=displacement,
        preceding_liquidity_sweep=sweep,
        strong_close_beyond_structure=True,
        wick_only_break=False,
        confirmation=BreakConfirmation.CONFIRMED,
        reason="test break",
    )


def _market_context() -> MarketContext:
    return MarketContext(
        symbol="ETH-USDT",
        detection_time_utc=UTC_NOW,
        candles_by_timeframe={},
        btc_candles_by_timeframe={},
    )


def _confirmation_candle() -> Candle:
    return _candle(20, open_=100, high=110, low=100, close=108, volume=150)


def _make_validator(
    adx_trending_min=25.0,
    adx_rejection_max=20.0,
) -> PreRiskValidator:
    return PreRiskValidator(
        market_regime_validator=MarketRegimeValidator(
            adx_trending_minimum=adx_trending_min,
            adx_rejection_maximum=adx_rejection_max,
            ema_flat_threshold=0.0005,
            minimum_atr_expansion_ratio=1.0,
            compression_lookback=10,
            minimum_candle_range_ratio=0.50,
        ),
        session_filter=SessionFilter(),
        btc_alignment_validator=BTCAlignmentValidator(),
        fake_breakout_filter=FakeBreakoutFilter(
            maximum_reversal_candles=3, return_inside_tolerance_ratio=0.0001
        ),
        candle_quality_validator=CandleQualityValidator(
            minimum_body_ratio=0.60,
            doji_maximum_body_ratio=0.10,
            spinning_top_maximum_body_ratio=0.30,
            maximum_opposite_wick_ratio=0.35,
            bullish_close_location_minimum=0.75,
            bearish_close_location_maximum=0.25,
        ),
    )


class TestPreRiskValidator:
    def test_complete_passing_validation_flow(self):
        candles = _normal_candles(11)
        sweep = _sweep(UTC_NOW - timedelta(minutes=20))
        structure_break = _structure_break(sweep)

        result = _make_validator().validate(
            _market_context(),
            "BUY",
            candles,
            _snapshot(),
            _btc_structure(),
            _btc_bias(),
            sweep,
            structure_break,
            _confirmation_candle(),
        )
        assert result.passed is True
        assert result.failed_layer is None
        assert len(result.validation_results) == 5

    def test_market_regime_failure_stops_later_validators(self):
        candles = _normal_candles(11)
        sweep = _sweep(UTC_NOW - timedelta(minutes=20))
        structure_break = _structure_break(sweep)

        result = _make_validator().validate(
            _market_context(),
            "BUY",
            candles,
            _snapshot(adx=None),
            _btc_structure(),
            _btc_bias(),
            sweep,
            structure_break,
            _confirmation_candle(),
        )
        assert result.passed is False
        assert result.failed_layer == "MARKET_REGIME"
        assert len(result.validation_results) == 1
        assert result.session is None

    def test_tiny_candle_range_fails_market_regime(self):
        # Valid ATR/ADX, but a tiny final candle range relative to the
        # preceding average triggers TINY_CANDLE -- now folded into
        # MARKET_REGIME itself (formerly a separate VOLATILITY_FILTER stage).
        candles = _normal_candles(10) + [_candle(10, 100, 100.5, 100, 100.2)]
        sweep = _sweep(UTC_NOW - timedelta(minutes=20))
        structure_break = _structure_break(sweep)

        result = _make_validator().validate(
            _market_context(),
            "BUY",
            candles,
            _snapshot(),
            _btc_structure(),
            _btc_bias(),
            sweep,
            structure_break,
            _confirmation_candle(),
        )
        assert result.passed is False
        assert result.failed_layer == "MARKET_REGIME"
        assert result.session is None

    def test_session_failure_stops_flow(self):
        candles = _normal_candles(11)
        sweep = _sweep(UTC_NOW - timedelta(minutes=20))
        structure_break = _structure_break(sweep)
        outside_session_context = MarketContext(
            symbol="ETH-USDT",
            detection_time_utc=UTC_NOW.replace(hour=22),
            candles_by_timeframe={},
            btc_candles_by_timeframe={},
        )

        result = _make_validator().validate(
            outside_session_context,
            "BUY",
            candles,
            _snapshot(),
            _btc_structure(),
            _btc_bias(),
            sweep,
            structure_break,
            _confirmation_candle(),
        )
        assert result.passed is False
        assert result.failed_layer == "SESSION_FILTER"
        assert result.btc_alignment is None

    def test_btc_alignment_failure_stops_flow(self):
        candles = _normal_candles(11)
        sweep = _sweep(UTC_NOW - timedelta(minutes=20))
        structure_break = _structure_break(sweep)

        result = _make_validator().validate(
            _market_context(),
            "BUY",
            candles,
            _snapshot(),
            _btc_structure(trend=TrendDirection.BEARISH),
            _btc_bias(final_bias=HigherTimeframeBias.BEARISH),
            sweep,
            structure_break,
            _confirmation_candle(),
        )
        assert result.passed is False
        assert result.failed_layer == "BTC_ALIGNMENT"
        assert result.fake_breakout is None

    def test_fake_breakout_failure_stops_flow(self):
        # structure_break's break candle timestamp is UTC_NOW - 10 minutes,
        # so every candle in `candles` (all at/after UTC_NOW) occurs after
        # it; the fake-breakout detector only inspects the first
        # `maximum_reversal_candles` (3) of those, so the reversal must be
        # among candles[0:3].
        candles = [
            _candle(0, open_=104, high=105, low=90, close=95),  # closes back below broken swing (100)
        ] + _normal_candles(10)[1:]
        sweep = _sweep(UTC_NOW - timedelta(minutes=20))
        structure_break = _structure_break(sweep)

        result = _make_validator().validate(
            _market_context(),
            "BUY",
            candles,
            _snapshot(),
            _btc_structure(),
            _btc_bias(),
            sweep,
            structure_break,
            _confirmation_candle(),
        )
        assert result.passed is False
        assert result.failed_layer == "FAKE_BREAKOUT_FILTER"
        assert result.candle_quality is None

    def test_candle_quality_failure_stops_flow(self):
        candles = _normal_candles(11)
        sweep = _sweep(UTC_NOW - timedelta(minutes=20))
        structure_break = _structure_break(sweep)
        weak_candle = _candle(20, open_=105, high=110, low=100, close=101)  # bearish, wrong for BUY

        result = _make_validator().validate(
            _market_context(),
            "BUY",
            candles,
            _snapshot(),
            _btc_structure(),
            _btc_bias(),
            sweep,
            structure_break,
            weak_candle,
        )
        assert result.passed is False
        assert result.failed_layer == "CANDLE_QUALITY"

    def test_executed_validation_results_preserved(self):
        candles = _normal_candles(11)
        sweep = _sweep(UTC_NOW - timedelta(minutes=20))
        structure_break = _structure_break(sweep)

        result = _make_validator().validate(
            _market_context(),
            "BUY",
            candles,
            _snapshot(),
            _btc_structure(),
            _btc_bias(),
            sweep,
            structure_break,
            _confirmation_candle(),
        )
        layer_names = [r.layer_name for r in result.validation_results]
        assert layer_names == [
            "MARKET_REGIME",
            "SESSION_FILTER",
            "BTC_ALIGNMENT",
            "FAKE_BREAKOUT_FILTER",
            "CANDLE_QUALITY",
        ]

    def test_failed_layer_recorded(self):
        candles = _normal_candles(11)
        sweep = _sweep(UTC_NOW - timedelta(minutes=20))
        structure_break = _structure_break(sweep)

        result = _make_validator().validate(
            _market_context(),
            "BUY",
            candles,
            _snapshot(adx=None),
            _btc_structure(),
            _btc_bias(),
            sweep,
            structure_break,
            _confirmation_candle(),
        )
        assert result.failed_layer == "MARKET_REGIME"

    def test_no_risk_score_or_signal_fields(self):
        result_fields = set(
            type(
                _make_validator().validate(
                    _market_context(),
                    "BUY",
                    _normal_candles(11),
                    _snapshot(),
                    _btc_structure(),
                    _btc_bias(),
                    _sweep(UTC_NOW - timedelta(minutes=20)),
                    _structure_break(_sweep(UTC_NOW - timedelta(minutes=20))),
                    _confirmation_candle(),
                )
            ).model_fields.keys()
        )
        forbidden = {
            "stop_loss",
            "take_profit",
            "risk_reward_ratio",
            "confidence_score",
            "signal_type",
        }
        assert result_fields.isdisjoint(forbidden)

    def test_inputs_not_mutated(self):
        candles = _normal_candles(11)
        snapshot = _snapshot()
        sweep = _sweep(UTC_NOW - timedelta(minutes=20))
        structure_break = _structure_break(sweep)
        confirmation_candle = _confirmation_candle()

        candles_snapshot = [c.model_copy() for c in candles]
        snapshot_copy = snapshot.model_copy()
        sweep_snapshot = sweep.model_copy()
        break_snapshot = structure_break.model_copy()
        candle_snapshot = confirmation_candle.model_copy()

        _make_validator().validate(
            _market_context(),
            "BUY",
            candles,
            snapshot,
            _btc_structure(),
            _btc_bias(),
            sweep,
            structure_break,
            confirmation_candle,
        )

        assert candles == candles_snapshot
        assert snapshot == snapshot_copy
        assert sweep == sweep_snapshot
        assert structure_break == break_snapshot
        assert confirmation_candle == candle_snapshot
