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
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType
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
from app.strategy_pipeline.engine import PipelineStrategyEngine, _resolve_entry_price
from app.strategy_pipeline.premium_discount import PremiumDiscountResult

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _build_engine(**overrides) -> PipelineStrategyEngine:
    defaults = dict(
        market_data_provider=MagicMock(),
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

        # atr is deliberately large here: these fixtures' trailing candles
        # were engineered purely to drive Volume Profile/CVD confidence far
        # from the [102, 105] BOS zone, which would otherwise trip the new
        # ENTRY_ZONE_MAX_DISTANCE_ATR freshness gate (see
        # TestEntryPriceAnchoring below for dedicated staleness coverage) --
        # a large atr keeps that unrelated distance well under the gate so
        # this fixture still tests what it was written to test.
        entry_snapshot = MagicMock(volume_ema20=5.0, atr=50.0, ema200_slope_direction="BULLISH")
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

        # ORDER_FLOW is a soft confidence layer: it always ran (Stages
        # 1-4 passed) and reports HIGH here since this fixture's
        # trailing candles were engineered so both Volume Profile and
        # CVD agree, but it never contributed to the VALID/REJECTED
        # decision above -- see test_low_order_flow_confidence_still_
        # reaches_valid below for the case where it disagrees entirely.
        order_flow_stage = next(s for s in result.stages if s.layer_name == "ORDER_FLOW")
        assert order_flow_stage.passed is True
        assert order_flow_stage.mandatory is False
        assert result.order_flow_confidence == "HIGH"

    async def test_low_order_flow_confidence_still_reaches_valid(self):
        """
        The exact scenario this refactor exists to prove: Volume
        Profile and CVD disagreeing entirely (LOW confidence) must
        NOT reject an otherwise-valid setup -- the pipeline still
        reaches Stage 6 and VALID, because Stages 1-4 (the real
        hard-mandatory gate) all passed. Identical fixture to
        test_grade_b_pass_reaches_valid_with_bos_zone_as_entry_zone
        except for the trailing candles, which are engineered so
        neither Volume Profile nor CVD confirms BUY.
        """
        entry_candles = [_entry_candle(i, 100.0, 101.0, 99.0, 100.0) for i in range(3)]
        sweep_candle = _entry_candle(3, 100.0, 100.5, 95.0, 100.2)
        entry_candles.append(sweep_candle)

        break_candle_index = 4
        break_candle = _entry_candle(break_candle_index, 100.2, 106.0, 100.0, 105.0)
        entry_candles.append(break_candle)

        for i in range(break_candle_index + 1, break_candle_index + 1 + IFVG_VALIDITY_WINDOW_CANDLES + 2):
            entry_candles.append(_entry_candle(i, 110.0, 111.0, 109.0, 110.0))
        bos_zone_retest_index = break_candle_index + BOS_ZONE_RETEST_VALIDITY_WINDOW_CANDLES
        while len(entry_candles) <= bos_zone_retest_index:
            entry_candles.append(_entry_candle(len(entry_candles), 110.0, 111.0, 109.0, 110.0))
        entry_candles[bos_zone_retest_index] = _entry_candle(bos_zone_retest_index, 104.0, 104.5, 103.0, 104.0)

        # Trailing candles engineered for the opposite outcome of the
        # HIGH-confidence fixture above: a CVD lower-low pattern (a
        # genuine directional disagreement for BUY) at a price level
        # (110.0) that also leaves Volume Profile's dominant HVN away
        # from the final close -- neither sub-check confirms BUY.
        cvd_start = len(entry_candles)
        deltas = [6, 6, 6, -10, -10, -10, -10, -10, 6, 6, 6, 6, 6, 6, -12, -12, -12, -12, -12, 6, 6, 6]
        price = 110.0
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

        # atr is deliberately large here: these fixtures' trailing candles
        # were engineered purely to drive Volume Profile/CVD confidence far
        # from the [102, 105] BOS zone, which would otherwise trip the new
        # ENTRY_ZONE_MAX_DISTANCE_ATR freshness gate (see
        # TestEntryPriceAnchoring below for dedicated staleness coverage) --
        # a large atr keeps that unrelated distance well under the gate so
        # this fixture still tests what it was written to test.
        entry_snapshot = MagicMock(volume_ema20=5.0, atr=50.0, ema200_slope_direction="BULLISH")
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

        detection_time_utc = entry_candles[-1].timestamp + timedelta(minutes=1)
        result = await engine.analyze_symbol(
            symbol="BTC-USDT",
            account_balance=10_000.0,
            active_trade_count=0,
            active_positions=[],
            active_position_candles={},
            detection_time_utc=detection_time_utc,
        )

        # The headline assertion: LOW order-flow confidence did NOT
        # reject this otherwise-valid setup.
        assert result.status == PipelineStatus.VALID
        assert result.selected_entry_zone is not None
        assert result.failed_layer is None
        assert result.rejection_reason is None

        risk_stage = next(s for s in result.stages if s.layer_name == "RISK_MANAGEMENT")
        assert risk_stage.passed is True

        order_flow_stage = next(s for s in result.stages if s.layer_name == "ORDER_FLOW")
        assert order_flow_stage.passed is True  # never blocks, regardless of confidence
        assert order_flow_stage.mandatory is False
        assert order_flow_stage.metadata is not None
        assert order_flow_stage.metadata["confidence"] == "LOW"
        assert order_flow_stage.metadata["volume_profile_passed"] is False
        assert order_flow_stage.metadata["cvd_passed"] is False

        assert result.order_flow_confidence == "LOW"
        assert result.order_flow_reason is not None
        assert "LOW_CONFIDENCE" in result.order_flow_reason


class TestEntryPriceAnchoring:
    """
    Covers the 2A fix: Stage 6 (and the Premium/Discount check folded
    into Stage 4) must anchor entry price to the Stage 4-selected zone
    (via `_resolve_entry_price`/`IfvgResult.selected_zone`) instead of
    the latest closed candle's close, and must reject a stale setup
    once the latest close has drifted more than
    ENTRY_ZONE_MAX_DISTANCE_ATR away from that anchor.
    """

    def test_resolve_entry_price_zone_midpoint(self):
        zone = TradeZone(
            zone_id="z1", symbol=BTC_SYMBOL, timeframe=ENTRY_TIMEFRAME, zone_type=ZoneType.ORDER_BLOCK,
            direction="BUY", lower_price=100.0, upper_price=110.0, created_at=UTC_NOW,
            source_candle_timestamp=UTC_NOW, status=ZoneStatus.FRESH,
        )
        assert _resolve_entry_price(zone, "BUY", last_close=999.0) == 105.0

    def test_resolve_entry_price_zone_edge_buy_uses_lower_price(self):
        zone = TradeZone(
            zone_id="z1", symbol=BTC_SYMBOL, timeframe=ENTRY_TIMEFRAME, zone_type=ZoneType.ORDER_BLOCK,
            direction="BUY", lower_price=100.0, upper_price=110.0, created_at=UTC_NOW,
            source_candle_timestamp=UTC_NOW, status=ZoneStatus.FRESH,
        )
        import app.strategy_pipeline.engine as engine_module

        original = engine_module.ENTRY_PRICE_ANCHOR
        engine_module.ENTRY_PRICE_ANCHOR = "ZONE_EDGE"
        try:
            assert _resolve_entry_price(zone, "BUY", last_close=999.0) == 100.0
            assert _resolve_entry_price(zone, "SELL", last_close=999.0) == 110.0
        finally:
            engine_module.ENTRY_PRICE_ANCHOR = original

    def test_resolve_entry_price_last_close_mode(self):
        zone = TradeZone(
            zone_id="z1", symbol=BTC_SYMBOL, timeframe=ENTRY_TIMEFRAME, zone_type=ZoneType.ORDER_BLOCK,
            direction="BUY", lower_price=100.0, upper_price=110.0, created_at=UTC_NOW,
            source_candle_timestamp=UTC_NOW, status=ZoneStatus.FRESH,
        )
        import app.strategy_pipeline.engine as engine_module

        original = engine_module.ENTRY_PRICE_ANCHOR
        engine_module.ENTRY_PRICE_ANCHOR = "LAST_CLOSE"
        try:
            assert _resolve_entry_price(zone, "BUY", last_close=123.45) == 123.45
        finally:
            engine_module.ENTRY_PRICE_ANCHOR = original


def _build_grade_b_fixture(*, final_close: float, atr: float = 1.0):
    """
    A Grade B (BOS-zone retest) fixture, deliberately leaner than
    TestGradeBIntegration's: since ORDER_FLOW (Stage 5) never blocks the
    pipeline, the trailing candles only need to avoid crashing Volume
    Profile/CVD, not engineer a specific confidence tier. `final_close`
    is the caller-controlled last candle's close, letting each test
    dial in exactly how far price has drifted from the resulting
    bos_zone [102.0, 105.0] (midpoint 103.5) to exercise the
    ENTRY_PRICE_ANCHOR / ENTRY_ZONE_MAX_DISTANCE_ATR behaviour.

    Returns (engine, risk_management_calculator_mock, premium_discount_mock).
    """
    entry_candles = [_entry_candle(i, 100.0, 101.0, 99.0, 100.0) for i in range(3)]
    sweep_candle = _entry_candle(3, 100.0, 100.5, 95.0, 100.2)
    entry_candles.append(sweep_candle)

    break_candle_index = 4
    break_candle = _entry_candle(break_candle_index, 100.2, 106.0, 100.0, 105.0)
    entry_candles.append(break_candle)

    for i in range(break_candle_index + 1, break_candle_index + 1 + IFVG_VALIDITY_WINDOW_CANDLES + 2):
        entry_candles.append(_entry_candle(i, 110.0, 111.0, 109.0, 110.0))
    bos_zone_retest_index = break_candle_index + BOS_ZONE_RETEST_VALIDITY_WINDOW_CANDLES
    while len(entry_candles) <= bos_zone_retest_index:
        entry_candles.append(_entry_candle(len(entry_candles), 110.0, 111.0, 109.0, 110.0))
    entry_candles[bos_zone_retest_index] = _entry_candle(bos_zone_retest_index, 104.0, 104.5, 103.0, 104.0)

    # A few flat trailing candles (harmless for the non-blocking ORDER_FLOW
    # stage), then one final candle whose close is the caller-controlled
    # `final_close` used to exercise anchoring/staleness.
    for i in range(bos_zone_retest_index + 1, bos_zone_retest_index + 6):
        entry_candles.append(_entry_candle(i, 104.0, 104.5, 103.5, 104.0, volume=5.0))
    last_index = len(entry_candles)
    entry_candles.append(
        _entry_candle(
            last_index, 104.0, max(104.0, final_close) + 0.5, min(104.0, final_close) - 0.5, final_close, volume=5.0
        )
    )

    broken_swing = SwingPoint(
        swing_id="swing-bullish", symbol=BTC_SYMBOL, timeframe=ENTRY_TIMEFRAME,
        timestamp=UTC_NOW - timedelta(minutes=20), candle_index=3, swing_type=SwingType.HIGH,
        price=102.0, left_strength=3, right_strength=3, confirmed=True,
    )
    displacement = DisplacementResult(
        symbol=BTC_SYMBOL, timeframe=ENTRY_TIMEFRAME, candle_timestamp=break_candle.timestamp,
        candle_index=break_candle_index, direction=BreakDirection.BULLISH, body_ratio=0.8,
        candle_range=break_candle.candle_range, body_size=break_candle.body_size,
        close_location_value=0.9, volume_confirmed=True, strong_close=True, confirmed=True,
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
        liquidity_id="level-99", symbol=BTC_SYMBOL, timeframe=ENTRY_TIMEFRAME,
        liquidity_type=LiquidityType.EQUAL_LOW, liquidity_side=LiquiditySide.SELL_SIDE, price=99.0,
        start_timestamp=UTC_NOW - timedelta(minutes=30), end_timestamp=UTC_NOW - timedelta(minutes=30),
        source_timestamps=[UTC_NOW - timedelta(minutes=30)], touch_count=2,
        strength=LiquidityStrength.STRONG, active=True,
    )
    confirmed_sweep = LiquiditySweepResult(
        sweep_id="sweep-bullish", symbol=BTC_SYMBOL, timeframe=ENTRY_TIMEFRAME,
        direction=SweepDirection.BULLISH, liquidity_level=liquidity_level,
        sweep_candle_timestamp=sweep_candle.timestamp, sweep_candle_index=3, sweep_price=95.0,
        close_price=100.2, penetration_distance=1.0, penetration_ratio=0.01, reclaimed_level=True,
        confirmed=True, reason="test sweep",
    )
    structure_break = StructureBreakResult(
        break_id="break-bullish-0", symbol=BTC_SYMBOL, timeframe=ENTRY_TIMEFRAME, break_type="BOS",
        direction=BreakDirection.BULLISH, broken_swing=broken_swing,
        break_candle_timestamp=break_candle.timestamp, break_candle_index=break_candle_index,
        break_price=102.0, close_price=105.0, displacement=displacement,
        preceding_liquidity_sweep=confirmed_sweep, strong_close_beyond_structure=True,
        wick_only_break=False, confirmation=BreakConfirmation.CONFIRMED, reason="test BOS",
    )
    liquidity_detection_result = LiquidityDetectionResult(
        symbol=BTC_SYMBOL, timeframe=ENTRY_TIMEFRAME, equal_highs=[], equal_lows=[liquidity_level],
        previous_day_levels=[], previous_week_levels=[], major_swing_levels=[], session_levels=[],
        all_levels=[liquidity_level], active_levels=[liquidity_level],
    )

    entry_snapshot = MagicMock(volume_ema20=5.0, atr=atr, ema200_slope_direction="BULLISH")
    indicator_calculator = MagicMock()
    indicator_calculator.calculate_multiple_timeframes = MagicMock(
        return_value={ENTRY_TIMEFRAME: entry_snapshot, HTF_SECONDARY: MagicMock(), HTF_PRIMARY: MagicMock()}
    )

    market_structure_calculator = MagicMock()
    discount_zone_swing_high = SwingPoint(
        swing_id="swing-range-high", symbol=BTC_SYMBOL, timeframe=ENTRY_TIMEFRAME,
        timestamp=UTC_NOW - timedelta(minutes=40), candle_index=10, swing_type=SwingType.HIGH,
        price=150.0, left_strength=3, right_strength=3, confirmed=True,
    )
    discount_zone_swing_low = SwingPoint(
        swing_id="swing-range-low", symbol=BTC_SYMBOL, timeframe=ENTRY_TIMEFRAME,
        timestamp=UTC_NOW - timedelta(minutes=41), candle_index=10, swing_type=SwingType.LOW,
        price=100.0, left_strength=3, right_strength=3, confirmed=True,
    )
    entry_structure = MagicMock(
        swings=[broken_swing], latest_swing_high=discount_zone_swing_high, latest_swing_low=discount_zone_swing_low,
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
    risk_plan = _real_risk_plan(direction="BUY", entry_price=103.5)
    risk_management_calculator.calculate = MagicMock(return_value=risk_plan)

    htf_candle = _entry_candle(0, 100.0, 101.0, 99.0, 100.0)
    provider = MagicMock()
    provider.fetch_symbol_market_data = AsyncMock(
        return_value={ENTRY_TIMEFRAME: entry_candles, HTF_SECONDARY: [htf_candle], HTF_PRIMARY: [htf_candle]}
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
    detection_time_utc = entry_candles[-1].timestamp + timedelta(minutes=1)
    return engine, risk_management_calculator, entry_candles, detection_time_utc


class TestEntryPriceAnchoringIntegration:
    """
    Full pipeline runs against the Grade B fixture above, proving the
    anchored price (not the latest close) is what actually reaches
    Premium/Discount and Stage 6's risk calculation, and that the
    staleness gate fires/doesn't fire at the right boundary.
    """

    async def test_zone_midpoint_anchor_reaches_valid_and_uses_midpoint(self):
        # final_close close enough to the 103.5 midpoint to stay under
        # the 0.5 ATR freshness gate (atr=1.0 here).
        engine, risk_calc, entry_candles, detection_time_utc = _build_grade_b_fixture(
            final_close=103.6, atr=1.0
        )
        result = await engine.analyze_symbol(
            symbol="BTC-USDT", account_balance=10_000.0, active_trade_count=0,
            active_positions=[], active_position_candles={}, detection_time_utc=detection_time_utc,
        )
        assert result.status == PipelineStatus.VALID
        assert risk_calc.calculate.call_args.kwargs["entry_price"] == 103.5

    async def test_zone_edge_anchor_uses_lower_price_for_buy(self):
        import app.strategy_pipeline.engine as engine_module

        original = engine_module.ENTRY_PRICE_ANCHOR
        engine_module.ENTRY_PRICE_ANCHOR = "ZONE_EDGE"
        try:
            # 102.2 is within 0.5 ATR of the lower edge (102.0), the
            # anchor this BUY setup should resolve to.
            engine, risk_calc, entry_candles, detection_time_utc = _build_grade_b_fixture(
                final_close=102.2, atr=1.0
            )
            result = await engine.analyze_symbol(
                symbol="BTC-USDT", account_balance=10_000.0, active_trade_count=0,
                active_positions=[], active_position_candles={}, detection_time_utc=detection_time_utc,
            )
        finally:
            engine_module.ENTRY_PRICE_ANCHOR = original

        assert result.status == PipelineStatus.VALID
        assert risk_calc.calculate.call_args.kwargs["entry_price"] == 102.0

    async def test_premium_discount_receives_the_anchored_price(self):
        import app.strategy_pipeline.engine as engine_module

        spy = MagicMock(
            wraps=lambda structure, entry_price, expected_direction: PremiumDiscountResult(
                passed=True, reason="test override: always passes"
            )
        )
        engine, risk_calc, entry_candles, detection_time_utc = _build_grade_b_fixture(
            final_close=103.6, atr=1.0
        )
        with_original = engine_module.evaluate_premium_discount_zone
        engine_module.evaluate_premium_discount_zone = spy
        try:
            result = await engine.analyze_symbol(
                symbol="BTC-USDT", account_balance=10_000.0, active_trade_count=0,
                active_positions=[], active_position_candles={}, detection_time_utc=detection_time_utc,
            )
        finally:
            engine_module.evaluate_premium_discount_zone = with_original

        assert result.status == PipelineStatus.VALID
        spy.assert_called_once()
        assert spy.call_args.args[1] == 103.5  # anchored midpoint, not the latest close (103.6)

    async def test_stale_setup_rejected_above_half_atr(self):
        # atr=1.0, midpoint=103.5: a final close of 110.0 is 6.5 ATR
        # away -- well above ENTRY_ZONE_MAX_DISTANCE_ATR (0.5).
        engine, risk_calc, entry_candles, detection_time_utc = _build_grade_b_fixture(
            final_close=110.0, atr=1.0
        )
        result = await engine.analyze_symbol(
            symbol="BTC-USDT", account_balance=10_000.0, active_trade_count=0,
            active_positions=[], active_position_candles={}, detection_time_utc=detection_time_utc,
        )
        assert result.status == PipelineStatus.REJECTED
        assert result.failed_layer == "RISK_MANAGEMENT"
        assert "stale" in (result.rejection_reason or "").lower()
        risk_calc.calculate.assert_not_called()

    async def test_fresh_setup_not_rejected_below_half_atr(self):
        # 103.9 is 0.4 ATR from the 103.5 midpoint -- under the 0.5 gate.
        engine, risk_calc, entry_candles, detection_time_utc = _build_grade_b_fixture(
            final_close=103.9, atr=1.0
        )
        result = await engine.analyze_symbol(
            symbol="BTC-USDT", account_balance=10_000.0, active_trade_count=0,
            active_positions=[], active_position_candles={}, detection_time_utc=detection_time_utc,
        )
        assert result.status == PipelineStatus.VALID
        risk_calc.calculate.assert_called_once()
