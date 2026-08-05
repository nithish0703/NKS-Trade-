"""
Constructs a fully wired PipelineStrategyEngine using centralized
application settings and thresholds.

Isolated from app.scanner.engine_factory during staged development:
nothing here is imported by the live scanner/dashboard/Telegram wiring
until the final, separately-approved cutover.
"""

from app.config import thresholds
from app.config.settings import get_settings
from app.data.binance_market_data_provider import BinanceFuturesMarketDataProvider
from app.data.candle_repository import CandleRepository
from app.indicators.calculator import IndicatorCalculator
from app.liquidity.calculator import LiquidityCalculator
from app.liquidity.equal_high_low import EqualHighLowDetector
from app.liquidity.level_selector import InstitutionalLiquiditySelector
from app.liquidity.previous_levels import PreviousPeriodLevelDetector
from app.liquidity.session_levels import SessionLiquidityDetector
from app.liquidity.sweep_detector import LiquiditySweepDetector
from app.liquidity.swing_liquidity import SwingLiquidityDetector
from app.market_structure.bos_detector import BOSDetector
from app.market_structure.calculator import MarketStructureCalculator
from app.market_structure.displacement import DisplacementDetector
from app.market_structure.htf_bias import HigherTimeframeBiasAnalyzer
from app.market_structure.swing_detector import SwingDetector
from app.market_structure.trend_structure import TrendStructureAnalyzer
from app.risk.active_trade_guard import ActiveTradeGuard
from app.risk.averaging_guard import AveragingGuard
from app.risk.calculator import RiskManagementCalculator
from app.risk.correlation_filter import CorrelationFilter
from app.risk.position_risk import PositionRiskCalculator
from app.risk.stop_loss import DynamicStopLossCalculator
from app.risk.take_profit import SingleTakeProfitCalculator
from app.zones.fair_value_gap import FairValueGapDetector

from app.strategy_pipeline.engine import PipelineStrategyEngine


def build_pipeline_strategy_engine() -> PipelineStrategyEngine:
    """
    Construct a fully wired PipelineStrategyEngine using production
    dependency instances, centralized Settings and thresholds.

    Does not make any API call, start scanning, or create global
    mutable singleton state; each call returns an independent engine
    instance with its own CandleRepository and market-data provider.
    """
    settings = get_settings()

    market_data_provider = BinanceFuturesMarketDataProvider(
        base_url=settings.exchange_base_url,
        request_timeout_seconds=settings.request_timeout_seconds,
    )
    candle_repository = CandleRepository()

    indicator_calculator = IndicatorCalculator(
        ema_fast_period=thresholds.EMA_FAST_PERIOD,
        ema_slow_period=thresholds.EMA_SLOW_PERIOD,
        ema_trend_period=thresholds.EMA_TREND_PERIOD,
        volume_ema_period=thresholds.VOLUME_EMA_PERIOD,
        atr_period=thresholds.ATR_PERIOD,
        adx_period=thresholds.ADX_PERIOD,
        ema_slope_lookback=thresholds.EMA_SLOPE_LOOKBACK,
        ema_flat_threshold=thresholds.EMA_FLAT_THRESHOLD,
        atr_expansion_lookback=thresholds.ATR_EXPANSION_LOOKBACK,
    )

    swing_detector = SwingDetector(
        left_strength=thresholds.SWING_LEFT_STRENGTH,
        right_strength=thresholds.SWING_RIGHT_STRENGTH,
        equality_tolerance=thresholds.SWING_EQUALITY_TOLERANCE,
    )
    trend_structure_analyzer = TrendStructureAnalyzer(
        equality_tolerance=thresholds.SWING_EQUALITY_TOLERANCE,
        minimum_confirmed_swings=thresholds.MINIMUM_CONFIRMED_SWINGS,
    )
    market_structure_calculator = MarketStructureCalculator(
        swing_detector=swing_detector,
        trend_structure_analyzer=trend_structure_analyzer,
        higher_timeframe_bias_analyzer=HigherTimeframeBiasAnalyzer(),
    )

    liquidity_calculator = LiquidityCalculator(
        equal_high_low_detector=EqualHighLowDetector(
            equality_tolerance=thresholds.LIQUIDITY_EQUALITY_TOLERANCE,
            minimum_touches=thresholds.EQUAL_LEVEL_MINIMUM_TOUCHES,
            maximum_group_span=thresholds.EQUAL_LEVEL_MAXIMUM_GROUP_SPAN,
        ),
        previous_period_level_detector=PreviousPeriodLevelDetector(),
        swing_liquidity_detector=SwingLiquidityDetector(
            minimum_swing_strength=thresholds.MAJOR_SWING_MINIMUM_STRENGTH
        ),
        session_liquidity_detector=SessionLiquidityDetector(),
        institutional_liquidity_selector=InstitutionalLiquiditySelector(),
        liquidity_sweep_detector=LiquiditySweepDetector(
            minimum_penetration_ratio=thresholds.LIQUIDITY_MINIMUM_PENETRATION_RATIO,
            maximum_reclaim_candles=thresholds.LIQUIDITY_MAXIMUM_RECLAIM_CANDLES,
        ),
    )

    displacement_detector = DisplacementDetector(
        minimum_body_ratio=thresholds.DISPLACEMENT_CANDLE_MIN_BODY_RATIO,
        bullish_close_location_minimum=thresholds.BULLISH_DISPLACEMENT_CLOSE_LOCATION_MIN,
        bearish_close_location_maximum=thresholds.BEARISH_DISPLACEMENT_CLOSE_LOCATION_MAX,
    )

    risk_management_calculator = RiskManagementCalculator(
        stop_loss_calculator=DynamicStopLossCalculator(
            atr_multiplier=thresholds.ATR_STOP_LOSS_MULTIPLIER,
            structural_buffer_ratio=thresholds.STOP_LOSS_STRUCTURAL_BUFFER_RATIO,
        ),
        take_profit_calculator=SingleTakeProfitCalculator(),
        position_risk_calculator=PositionRiskCalculator(
            maximum_risk_percentage=thresholds.MAX_RISK_PER_TRADE * 100
        ),
        correlation_filter=CorrelationFilter(
            maximum_allowed_correlation=thresholds.MAXIMUM_ALLOWED_POSITION_CORRELATION,
            minimum_observations=thresholds.CORRELATION_MINIMUM_OBSERVATIONS,
        ),
        active_trade_guard=ActiveTradeGuard(maximum_active_trades=thresholds.MAX_ACTIVE_TRADES),
        averaging_guard=AveragingGuard(),
    )

    return PipelineStrategyEngine(
        market_data_provider=market_data_provider,
        candle_repository=candle_repository,
        indicator_calculator=indicator_calculator,
        market_structure_calculator=market_structure_calculator,
        liquidity_calculator=liquidity_calculator,
        displacement_detector=displacement_detector,
        bos_detector=BOSDetector(),
        fair_value_gap_detector=FairValueGapDetector(),
        risk_management_calculator=risk_management_calculator,
    )
