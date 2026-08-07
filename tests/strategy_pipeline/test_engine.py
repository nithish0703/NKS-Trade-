"""
Tests for app.strategy_pipeline.engine.PipelineStrategyEngine, covering
input validation and the market-data-unavailable error path with
mocked dependencies. Full end-to-end success/rejection paths against
real chained detectors are exercised separately via a live smoke run
against real Binance market data (not part of the unit test suite,
since that requires network access), except for the targeted Grade B
integration test below, which mocks each stage's calculator/detector
seam directly rather than chaining real detectors end-to-end, so it
stays fast and deterministic like the rest of this file.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.pairs import BTC_SYMBOL
from app.config.timeframes import ENTRY_TIMEFRAME, HTF_PRIMARY, HTF_SECONDARY
from app.config.thresholds import (
    BOS_ZONE_RETEST_VALIDITY_WINDOW_CANDLES,
    IFVG_VALIDITY_WINDOW_CANDLES,
)
from app.data.market_data_errors import MarketDataRequestError
from app.liquidity.results import LiquidityDetectionResult
from app.market_structure.displacement import DisplacementResult
from app.market_structure.results import MarketStructureResult, SwingPoint, SwingType, TrendDirection
from app.market_structure.shift_results import (
    BreakConfirmation,
    BreakDirection,
    StructureBreakResult,
)
from app.models.candle import Candle
from app.risk.results import (
    CorrelationResult,
    CorrelationStatus,
    PositionRiskResult,
    RiskPlan,
    RiskPlanStatus,
    StopLossResult,
    TakeProfitResult,
)
from app.scanner.pipeline_exceptions import PipelineDataUnavailableError, PipelineInputError
from app.scanner.pipeline_results import PipelineStatus
from app.strategy_pipeline.engine import PipelineStrategyEngine

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _build_engine(**overrides) -> PipelineStrategyEngine:
    from app.data.candle_repository import CandleRepository

    defaults = dict(
        market_data_provider=MagicMock(),
        candle_repository=CandleRepository(),
        indicator_calculator=MagicMock(),
        market_structure_calculator=MagicMock(),
        liquidity_calculator=MagicMock(),
        displacement_detector=MagicMock(),
        bos_detector=MagicMock(),
        fair_value_gap_detector=MagicMock(),
        risk_management_calculator=MagicMock(),
    )
    defaults.update(overrides)
    return PipelineStrategyEngine(**defaults)


async def _analyze(engine: PipelineStrategyEngine, symbol: str = "BTC-USDT"):
    return await engine.analyze_symbol(
        symbol=symbol,
        account_balance=10_000.0,
        active_trade_count=0,
        active_positions=[],
        active_position_candles={},
        detection_time_utc=UTC_NOW,
    )


class TestInputValidation:
    async def test_invalid_symbol_raises_pipeline_input_error(self):
        engine = _build_engine()
        with pytest.raises(PipelineInputError):
            await _analyze(engine, symbol="not-a-real-symbol")

    async def test_non_positive_account_balance_raises_pipeline_input_error(self):
        engine = _build_engine()
        with pytest.raises(PipelineInputError):
            await engine.analyze_symbol(
                symbol="BTC-USDT",
                account_balance=0.0,
                active_trade_count=0,
                active_positions=[],
                active_position_candles={},
                detection_time_utc=UTC_NOW,
            )

    async def test_negative_active_trade_count_raises_pipeline_input_error(self):
        engine = _build_engine()
        with pytest.raises(PipelineInputError):
            await engine.analyze_symbol(
                symbol="BTC-USDT",
                account_balance=10_000.0,
                active_trade_count=-1,
                active_positions=[],
                active_position_candles={},
                detection_time_utc=UTC_NOW,
            )


class TestMarketDataUnavailable:
    async def test_market_data_error_raises_pipeline_data_unavailable_error(self):
        provider = MagicMock()
        provider.fetch_symbol_market_data = AsyncMock(
            side_effect=MarketDataRequestError("network failure")
        )
        engine = _build_engine(market_data_provider=provider)

        with pytest.raises(PipelineDataUnavailableError):
            await _analyze(engine)

    async def test_missing_required_timeframe_raises_pipeline_data_unavailable_error(self):
        provider = MagicMock()
        # Missing "1h" and "4h" -- only entry timeframe present.
        provider.fetch_symbol_market_data = AsyncMock(return_value={"15m": [MagicMock()]})
        engine = _build_engine(market_data_provider=provider)

        with pytest.raises(PipelineDataUnavailableError):
            await _analyze(engine)


def _entry_candle(index: int, open_: float, high: float, low: float, close: float, volume: float = 10.0) -> Candle:
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        symbol=BTC_SYMBOL,
        timeframe=ENTRY_TIMEFRAME,
    )


def _real_risk_plan(*, direction: str, entry_price: float) -> RiskPlan:
    stop_loss = entry_price - 5.0 if direction == "BUY" else entry_price + 5.0
    stop_loss_result = StopLossResult(
        direction=direction,
        entry_price=entry_price,
        selected_stop_loss=stop_loss,
        candidates=[],
        valid=True,
        reason="test stop loss ok",
    )
    take_profit = entry_price + 15.0 if direction == "BUY" else entry_price - 15.0
    take_profit_result = TakeProfitResult(
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        selected_take_profit=take_profit,
        risk_reward_ratio=3.0,
        candidates=[],
        valid=True,
        reason="test take profit ok",
    )
    position_risk = PositionRiskResult(
        account_balance=10_000.0,
        risk_percentage=1.0,
        entry_price=entry_price,
        stop_loss=stop_loss,
        valid=True,
        reason="test position risk ok",
    )
    correlation_result = CorrelationResult(
        candidate_symbol=BTC_SYMBOL,
        active_symbols=[],
        maximum_allowed_correlation=0.7,
        observed_correlations={},
        status=CorrelationStatus.ACCEPTABLE,
        acceptable=True,
        reason="test correlation ok",
    )
    return RiskPlan(
        direction=direction,
        entry_price=entry_price,
        stop_loss_result=stop_loss_result,
        take_profit_result=take_profit_result,
        position_risk=position_risk,
        correlation_result=correlation_result,
        active_trade_count=0,
        maximum_active_trades=5,
        risk_reward_ratio=3.0,
        status=RiskPlanStatus.VALID,
        valid=True,
        reason="test risk plan ok",
    )


def _htf_structure(trend: TrendDirection) -> MarketStructureResult:
    return MarketStructureResult(
        symbol=BTC_SYMBOL,
        timeframe=HTF_SECONDARY,
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


class TestGradeBIntegration:
    """
    End-to-end (within-engine) integration test proving a Grade B IFVG
    pass reaches Stage 6 with a real entry zone and the pipeline reaches
    VALID -- the exact bug this fix closes: before `selected_zone`, a
    Grade B pass always read `ifvg_zone` (None for Grade B) at Stage 6
    and was wrongly rejected with "Entry zone or ATR is unavailable".

    Each stage's calculator/detector seam is mocked directly (matching
    this file's existing pattern) rather than chaining real detectors
    end-to-end, since assembling a real candle series that survives
    every real detector's own internal thresholds is exactly the
    fragile, slow style of test this file's docstring says belongs to
    the separate live smoke run instead.
    """

    async def test_grade_b_pass_reaches_valid_with_bos_zone_as_entry_zone(self):
        entry_candles = [_entry_candle(i, 100.0, 101.0, 99.0, 100.0) for i in range(3)]
        # Sweep candle at index 3: long lower wick (rejection), closes
        # back inside the prior [99, 101] range -- satisfies
        # validate_liquidity_sweep's wick>body and close-inside-range rules.
        sweep_candle = _entry_candle(3, 100.0, 100.5, 95.0, 100.2)
        entry_candles.append(sweep_candle)

        break_candle_index = 4
        break_candle = _entry_candle(break_candle_index, 100.2, 106.0, 100.0, 105.0)
        entry_candles.append(break_candle)

        # Grade A never confirms (no FVGs at all, mocked below). Fill
        # the IFVG window with candles that never retest anything, then
        # retest the BOS zone [swing_price, close_price] = [102, 105]
        # within BOS_ZONE_RETEST_VALIDITY_WINDOW_CANDLES of the break.
        for i in range(break_candle_index + 1, break_candle_index + 1 + IFVG_VALIDITY_WINDOW_CANDLES + 2):
            entry_candles.append(_entry_candle(i, 110.0, 111.0, 109.0, 110.0))
        bos_zone_retest_index = break_candle_index + BOS_ZONE_RETEST_VALIDITY_WINDOW_CANDLES
        while len(entry_candles) <= bos_zone_retest_index:
            entry_candles.append(_entry_candle(len(entry_candles), 110.0, 111.0, 109.0, 110.0))
        entry_candles[bos_zone_retest_index] = _entry_candle(bos_zone_retest_index, 104.0, 104.5, 103.0, 104.0)

        # A few extra trailing candles for Stage 5's CVD/Volume Profile
        # sub-checks, engineered so both directional checks agree with
        # BUY: a CVD higher-low swing pattern (two clearly separated dip
        # cycles, spaced for evaluate_order_flow's default
        # left_strength=3/right_strength=3 swing detection -- unlike
        # test_cvd.py/test_order_flow.py's own fixtures, this engine
        # test cannot override those strengths since engine.py always
        # calls evaluate_order_flow with its defaults) via the
        # bullish/bearish volume-delta proxy (see app.strategy_pipeline.cvd),
        # and a price level (108.0) that lands the resulting Volume
        # Profile's dominant HVN directly under the final close, so the
        # default-config Volume Profile sub-check also confirms BUY.
        cvd_start = len(entry_candles)
        deltas = [6, 6, 6, -10, -10, -10, -10, -10, 6, 6, 6, 6, 6, 6, -5, -5, -5, -5, -5, 6, 6, 6]
        price = 108.0
        for offset, delta in enumerate(deltas):
            index = cvd_start + offset
            if delta > 0:
                entry_candles.append(_entry_candle(index, price, price + 1.0, price - 0.5, price + 1.0, volume=delta))
            else:
                entry_candles.append(_entry_candle(index, price + 1.0, price + 1.5, price - 0.5, price, volume=-delta))
            price += 0.1

        broken_swing = SwingPoint(
            swing_id="swing-bullish",
            symbol=BTC_SYMBOL,
            timeframe=ENTRY_TIMEFRAME,
            timestamp=UTC_NOW - timedelta(minutes=20),
            candle_index=3,
            swing_type=SwingType.HIGH,
            price=102.0,
            left_strength=3,
            right_strength=3,
            confirmed=True,
        )
        displacement = DisplacementResult(
            symbol=BTC_SYMBOL,
            timeframe=ENTRY_TIMEFRAME,
            candle_timestamp=break_candle.timestamp,
            candle_index=break_candle_index,
            direction=BreakDirection.BULLISH,
            body_ratio=0.8,
            candle_range=break_candle.candle_range,
            body_size=break_candle.body_size,
            close_location_value=0.9,
            volume_confirmed=True,
            strong_close=True,
            confirmed=True,
            reason="test displacement",
        )

        from app.liquidity.results import (
            LiquidityLevel,
            LiquiditySide,
            LiquidityStrength,
            LiquiditySweepResult,
            LiquidityType,
            SweepDirection,
        )

        liquidity_level = LiquidityLevel(
            liquidity_id="level-99",
            symbol=BTC_SYMBOL,
            timeframe=ENTRY_TIMEFRAME,
            liquidity_type=LiquidityType.EQUAL_LOW,
            liquidity_side=LiquiditySide.SELL_SIDE,
            price=99.0,
            start_timestamp=UTC_NOW - timedelta(minutes=30),
            end_timestamp=UTC_NOW - timedelta(minutes=30),
            source_timestamps=[UTC_NOW - timedelta(minutes=30)],
            touch_count=2,
            strength=LiquidityStrength.STRONG,
            active=True,
        )
        confirmed_sweep = LiquiditySweepResult(
            sweep_id="sweep-bullish",
            symbol=BTC_SYMBOL,
            timeframe=ENTRY_TIMEFRAME,
            direction=SweepDirection.BULLISH,
            liquidity_level=liquidity_level,
            sweep_candle_timestamp=sweep_candle.timestamp,
            sweep_candle_index=3,
            sweep_price=95.0,
            close_price=100.2,
            penetration_distance=1.0,
            penetration_ratio=0.01,
            reclaimed_level=True,
            confirmed=True,
            reason="test sweep",
        )

        structure_break = StructureBreakResult(
            break_id="break-bullish-0",
            symbol=BTC_SYMBOL,
            timeframe=ENTRY_TIMEFRAME,
            break_type="BOS",
            direction=BreakDirection.BULLISH,
            broken_swing=broken_swing,
            break_candle_timestamp=break_candle.timestamp,
            break_candle_index=break_candle_index,
            break_price=102.0,
            close_price=105.0,
            displacement=displacement,
            preceding_liquidity_sweep=confirmed_sweep,
            strong_close_beyond_structure=True,
            wick_only_break=False,
            confirmation=BreakConfirmation.CONFIRMED,
            reason="test BOS",
        )

        liquidity_detection_result = LiquidityDetectionResult(
            symbol=BTC_SYMBOL,
            timeframe=ENTRY_TIMEFRAME,
            equal_highs=[],
            equal_lows=[liquidity_level],
            previous_day_levels=[],
            previous_week_levels=[],
            major_swing_levels=[],
            session_levels=[],
            all_levels=[liquidity_level],
            active_levels=[liquidity_level],
        )

        entry_snapshot = MagicMock(volume_ema20=5.0, atr=1.0, ema200_slope_direction="BULLISH")
        indicator_calculator = MagicMock()
        indicator_calculator.calculate_multiple_timeframes = MagicMock(
            return_value={
                ENTRY_TIMEFRAME: entry_snapshot,
                HTF_SECONDARY: MagicMock(),
                HTF_PRIMARY: MagicMock(),
            }
        )

        market_structure_calculator = MagicMock()
        discount_zone_swing_high = SwingPoint(
            swing_id="swing-range-high",
            symbol=BTC_SYMBOL,
            timeframe=ENTRY_TIMEFRAME,
            timestamp=UTC_NOW - timedelta(minutes=40),
            candle_index=10,
            swing_type=SwingType.HIGH,
            price=150.0,
            left_strength=3,
            right_strength=3,
            confirmed=True,
        )
        discount_zone_swing_low = SwingPoint(
            swing_id="swing-range-low",
            symbol=BTC_SYMBOL,
            timeframe=ENTRY_TIMEFRAME,
            timestamp=UTC_NOW - timedelta(minutes=41),
            candle_index=10,
            swing_type=SwingType.LOW,
            price=100.0,
            left_strength=3,
            right_strength=3,
            confirmed=True,
        )
        entry_structure = MagicMock(
            swings=[broken_swing],
            latest_swing_high=discount_zone_swing_high,
            latest_swing_low=discount_zone_swing_low,
        )
        market_structure_calculator.calculate_multiple_timeframes = MagicMock(
            return_value={
                ENTRY_TIMEFRAME: entry_structure,
                HTF_SECONDARY: _htf_structure(TrendDirection.BULLISH),
                HTF_PRIMARY: _htf_structure(TrendDirection.BULLISH),
            }
        )

        liquidity_calculator = MagicMock()
        liquidity_calculator.detect_levels = MagicMock(return_value=liquidity_detection_result)
        liquidity_calculator.detect_sweeps = MagicMock(return_value=[confirmed_sweep])

        bos_detector = MagicMock()
        bos_detector.detect = MagicMock(return_value=[structure_break])

        fair_value_gap_detector = MagicMock()
        fair_value_gap_detector.detect = MagicMock(return_value=[])  # Grade A can never confirm

        risk_management_calculator = MagicMock()
        risk_plan = _real_risk_plan(direction="BUY", entry_price=entry_candles[-1].close)
        risk_management_calculator.calculate = MagicMock(return_value=risk_plan)

        htf_candle = _entry_candle(0, 100.0, 101.0, 99.0, 100.0)
        provider = MagicMock()
        provider.fetch_symbol_market_data = AsyncMock(
            return_value={
                ENTRY_TIMEFRAME: entry_candles,
                HTF_SECONDARY: [htf_candle],
                HTF_PRIMARY: [htf_candle],
            }
        )

        engine = _build_engine(
            market_data_provider=provider,
            indicator_calculator=indicator_calculator,
            market_structure_calculator=market_structure_calculator,
            liquidity_calculator=liquidity_calculator,
            bos_detector=bos_detector,
            fair_value_gap_detector=fair_value_gap_detector,
            risk_management_calculator=risk_management_calculator,
        )

        # A detection time after every entry candle's timestamp, so
        # `_select_latest_valid_sweep`'s "sweep occurred before
        # detection time" filter accepts the sweep candle built above.
        detection_time_utc = entry_candles[-1].timestamp + timedelta(minutes=1)
        result = await engine.analyze_symbol(
            symbol="BTC-USDT",
            account_balance=10_000.0,
            active_trade_count=0,
            active_positions=[],
            active_position_candles={},
            detection_time_utc=detection_time_utc,
        )

        ifvg_stage = next(s for s in result.stages if s.layer_name == "IFVG")
        assert ifvg_stage.passed is True

        risk_stage = next(s for s in result.stages if s.layer_name == "RISK_MANAGEMENT")
        assert risk_stage.passed is True
        assert "unavailable" not in (risk_stage.reason or "").lower()

        assert result.status == PipelineStatus.VALID
        assert result.selected_entry_zone is not None
        assert result.selected_entry_zone.lower_price == 102.0
        assert result.selected_entry_zone.upper_price == 105.0
        assert result.market_context.selected_entry_zone == result.selected_entry_zone
