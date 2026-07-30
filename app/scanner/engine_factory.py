"""
Constructs a fully wired InstitutionalSMCStrategyEngine using centralized
application settings and thresholds.
"""

from app.config import thresholds
from app.config.settings import get_settings
from app.data.candle_repository import CandleRepository
from app.data.bybit_market_data_provider import BybitMarketDataProvider
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
from app.market_structure.choch_detector import CHOCHDetector
from app.market_structure.displacement import DisplacementDetector
from app.market_structure.htf_bias import HigherTimeframeBiasAnalyzer
from app.market_structure.mss_detector import MSSDetector
from app.market_structure.shift_calculator import StructureShiftCalculator
from app.market_structure.swing_detector import SwingDetector
from app.market_structure.trend_structure import TrendStructureAnalyzer
from app.risk.active_trade_guard import ActiveTradeGuard
from app.risk.averaging_guard import AveragingGuard
from app.risk.calculator import RiskManagementCalculator
from app.risk.correlation_filter import CorrelationFilter
from app.risk.position_risk import PositionRiskCalculator
from app.risk.stop_loss import DynamicStopLossCalculator
from app.risk.take_profit import SingleTakeProfitCalculator
from app.scoring.calculator import ConfidenceCalculator
from app.scoring.input_adapter import ConfidenceInputAdapter
from app.scoring.score_engine import ConfidenceScoringEngine
from app.validators.btc_alignment import BTCAlignmentValidator
from app.validators.candle_quality import CandleQualityValidator
from app.validators.entry_zone import EntryZoneValidator
from app.validators.fake_breakout_filter import FakeBreakoutFilter
from app.validators.htf_bias import HigherTimeframeBiasValidator
from app.validators.liquidity_sweep import LiquiditySweepValidator
from app.validators.market_regime import MarketRegimeValidator
from app.validators.premium_discount import PremiumDiscountValidator
from app.validators.retest_confirmation import RetestConfirmationValidator
from app.validators.risk_management import RiskManagementValidator
from app.validators.session_filter import SessionFilter
from app.validators.structure_shift import StructureShiftValidator
from app.validators.volume_confirmation import VolumeConfirmationValidator
from app.zones.breaker_block import BreakerBlockDetector
from app.zones.calculator import ZoneCalculator
from app.zones.dealing_range import DealingRangeCalculator
from app.zones.fair_value_gap import FairValueGapDetector
from app.zones.invalidation_checker import ZoneInvalidationChecker
from app.zones.mitigation_checker import ZoneMitigationChecker
from app.zones.order_block import OrderBlockDetector
from app.zones.retest_confirmation import RetestConfirmationDetector
from app.zones.setup_confirmation import ZoneSetupConfirmationCalculator
from app.zones.zone_selector import EntryZoneSelector

from app.scanner.preview_analyzer import PreviewAnalyzer
from app.scanner.strategy_engine import InstitutionalSMCStrategyEngine

import asyncio

from app.config.pairs import get_configured_pairs, set_pair_source
from app.scanner.active_state import EmptyActiveTradingStateProvider
from app.scanner.candidate_buffer import ValidSignalCandidateBuffer
from app.scanner.duplicate_guard import DuplicateSignalGuard
from app.scanner.pair_discovery import DynamicPairDiscoveryService
from app.scanner.pair_scanner import PairScanner
from app.scanner.scan_scheduler import MultiPairScanScheduler
from app.scanner.scanner_service import ScannerService
from app.scanner.signal_builder import InstitutionalSignalBuilder

# Storage submodules are imported lazily inside build_scanner_service() and
# initialize_scanner_storage() to avoid a module-level circular import:
# app.storage's package __init__ imports app.scanner.pipeline_results, which
# (via app.scanner's package __init__) re-enters this module.


def build_strategy_engine() -> InstitutionalSMCStrategyEngine:
    """
    Construct a fully wired InstitutionalSMCStrategyEngine using
    production dependency instances, centralized Settings and
    thresholds.

    Does not make any API call, start scanning, or create global
    mutable singleton state; each call returns an independent engine
    instance with its own CandleRepository and market-data provider.
    """
    settings = get_settings()

    market_data_provider = BybitMarketDataProvider(
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

    structure_shift_calculator = StructureShiftCalculator(
        displacement_detector=DisplacementDetector(
            minimum_body_ratio=thresholds.DISPLACEMENT_CANDLE_MIN_BODY_RATIO,
            bullish_close_location_minimum=thresholds.BULLISH_DISPLACEMENT_CLOSE_LOCATION_MIN,
            bearish_close_location_maximum=thresholds.BEARISH_DISPLACEMENT_CLOSE_LOCATION_MAX,
        ),
        bos_detector=BOSDetector(),
        choch_detector=CHOCHDetector(),
        mss_detector=MSSDetector(),
    )

    zone_calculator = ZoneCalculator(
        order_block_detector=OrderBlockDetector(),
        fair_value_gap_detector=FairValueGapDetector(),
        breaker_block_detector=BreakerBlockDetector(),
        mitigation_checker=ZoneMitigationChecker(
            full_mitigation_required=thresholds.FULL_ZONE_MITIGATION_REQUIRED,
            touch_tolerance_ratio=thresholds.ZONE_TOUCH_TOLERANCE_RATIO,
        ),
        invalidation_checker=ZoneInvalidationChecker(),
        entry_zone_selector=EntryZoneSelector(),
    )

    dealing_range_calculator = DealingRangeCalculator(
        equilibrium_tolerance_ratio=thresholds.DEALING_RANGE_EQUILIBRIUM_TOLERANCE_RATIO,
        middle_zone_tolerance_ratio=thresholds.DEALING_RANGE_MIDDLE_TOLERANCE_RATIO,
    )
    retest_confirmation_detector = RetestConfirmationDetector(
        minimum_rejection_body_ratio=thresholds.RETEST_MINIMUM_REJECTION_BODY_RATIO,
        bullish_close_location_minimum=thresholds.RETEST_BULLISH_CLOSE_LOCATION_MINIMUM,
        bearish_close_location_maximum=thresholds.RETEST_BEARISH_CLOSE_LOCATION_MAXIMUM,
        minimum_rejection_wick_ratio=thresholds.RETEST_MINIMUM_REJECTION_WICK_RATIO,
        maximum_confirmation_candles=thresholds.RETEST_MAXIMUM_CONFIRMATION_CANDLES,
    )
    zone_setup_confirmation_calculator = ZoneSetupConfirmationCalculator(
        dealing_range_calculator=dealing_range_calculator,
        premium_discount_validator=PremiumDiscountValidator(),
        retest_confirmation_detector=retest_confirmation_detector,
        retest_confirmation_validator=RetestConfirmationValidator(),
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

    confidence_calculator = ConfidenceCalculator(
        input_adapter=ConfidenceInputAdapter(),
        scoring_engine=ConfidenceScoringEngine(),
    )

    return InstitutionalSMCStrategyEngine(
        market_data_provider=market_data_provider,
        candle_repository=candle_repository,
        indicator_calculator=indicator_calculator,
        market_structure_calculator=market_structure_calculator,
        liquidity_calculator=liquidity_calculator,
        structure_shift_calculator=structure_shift_calculator,
        zone_calculator=zone_calculator,
        zone_setup_confirmation_calculator=zone_setup_confirmation_calculator,
        market_regime_validator=MarketRegimeValidator(
            adx_trending_minimum=thresholds.ADX_TRENDING_MIN,
            adx_rejection_maximum=thresholds.ADX_REJECTION_MAX,
            ema_flat_threshold=thresholds.EMA_FLAT_THRESHOLD,
            minimum_atr_expansion_ratio=thresholds.MARKET_REGIME_MINIMUM_ATR_EXPANSION_RATIO,
            compression_lookback=thresholds.VOLATILITY_COMPRESSION_LOOKBACK,
            minimum_candle_range_ratio=thresholds.VOLATILITY_MINIMUM_CANDLE_RANGE_RATIO,
        ),
        htf_bias_validator=HigherTimeframeBiasValidator(),
        liquidity_sweep_validator=LiquiditySweepValidator(),
        structure_shift_validator=StructureShiftValidator(),
        volume_confirmation_validator=VolumeConfirmationValidator(),
        entry_zone_validator=EntryZoneValidator(),
        session_filter=SessionFilter(),
        btc_alignment_validator=BTCAlignmentValidator(),
        fake_breakout_filter=FakeBreakoutFilter(
            maximum_reversal_candles=thresholds.FAKE_BREAKOUT_MAXIMUM_REVERSAL_CANDLES,
            return_inside_tolerance_ratio=thresholds.FAKE_BREAKOUT_RETURN_INSIDE_TOLERANCE_RATIO,
        ),
        candle_quality_validator=CandleQualityValidator(
            minimum_body_ratio=thresholds.DISPLACEMENT_CANDLE_MIN_BODY_RATIO,
            doji_maximum_body_ratio=thresholds.DOJI_MAXIMUM_BODY_RATIO,
            spinning_top_maximum_body_ratio=thresholds.SPINNING_TOP_MAXIMUM_BODY_RATIO,
            maximum_opposite_wick_ratio=thresholds.CANDLE_MAXIMUM_OPPOSITE_WICK_RATIO,
            bullish_close_location_minimum=thresholds.CANDLE_QUALITY_BULLISH_CLOSE_LOCATION_MINIMUM,
            bearish_close_location_maximum=thresholds.CANDLE_QUALITY_BEARISH_CLOSE_LOCATION_MAXIMUM,
        ),
        risk_management_calculator=risk_management_calculator,
        risk_management_validator=RiskManagementValidator(),
        confidence_calculator=confidence_calculator,
    )


def build_preview_analyzer() -> PreviewAnalyzer:
    """
    Construct a dashboard-only PreviewAnalyzer using the exact same
    validator/calculator instances and configured thresholds as
    build_strategy_engine(), so preview evaluation never diverges from
    production strategy logic -- only the fail-fast orchestration
    differs.

    Does not make any API call, start scanning, or create global
    mutable singleton state.
    """
    layer_weights: dict[str, float] = {
        "MARKET_REGIME": thresholds.SCORE_MARKET_REGIME,
        "HTF_BIAS": thresholds.SCORE_HTF_BIAS,
        "LIQUIDITY_SWEEP": thresholds.SCORE_LIQUIDITY_SWEEP,
        "STRUCTURE_SHIFT": thresholds.SCORE_STRUCTURE_SHIFT,
        "VOLUME_CONFIRMATION": thresholds.SCORE_VOLUME_CONFIRMATION,
        "ENTRY_ZONE": thresholds.SCORE_ENTRY_ZONE,
        "PREMIUM_DISCOUNT": thresholds.SCORE_PREMIUM_DISCOUNT,
        "RETEST_CONFIRMATION": thresholds.SCORE_RETEST_CONFIRMATION,
        "SESSION_FILTER": thresholds.SCORE_SESSION,
        "BTC_ALIGNMENT": thresholds.SCORE_BTC_ALIGNMENT,
        "FAKE_BREAKOUT_FILTER": thresholds.SCORE_FAKE_BREAKOUT,
    }

    return PreviewAnalyzer(
        liquidity_calculator=LiquidityCalculator(
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
        ),
        structure_shift_calculator=StructureShiftCalculator(
            displacement_detector=DisplacementDetector(
                minimum_body_ratio=thresholds.DISPLACEMENT_CANDLE_MIN_BODY_RATIO,
                bullish_close_location_minimum=thresholds.BULLISH_DISPLACEMENT_CLOSE_LOCATION_MIN,
                bearish_close_location_maximum=thresholds.BEARISH_DISPLACEMENT_CLOSE_LOCATION_MAX,
            ),
            bos_detector=BOSDetector(),
            choch_detector=CHOCHDetector(),
            mss_detector=MSSDetector(),
        ),
        zone_calculator=ZoneCalculator(
            order_block_detector=OrderBlockDetector(),
            fair_value_gap_detector=FairValueGapDetector(),
            breaker_block_detector=BreakerBlockDetector(),
            mitigation_checker=ZoneMitigationChecker(
                full_mitigation_required=thresholds.FULL_ZONE_MITIGATION_REQUIRED,
                touch_tolerance_ratio=thresholds.ZONE_TOUCH_TOLERANCE_RATIO,
            ),
            invalidation_checker=ZoneInvalidationChecker(),
            entry_zone_selector=EntryZoneSelector(),
        ),
        dealing_range_calculator=DealingRangeCalculator(
            equilibrium_tolerance_ratio=thresholds.DEALING_RANGE_EQUILIBRIUM_TOLERANCE_RATIO,
            middle_zone_tolerance_ratio=thresholds.DEALING_RANGE_MIDDLE_TOLERANCE_RATIO,
        ),
        retest_confirmation_detector=RetestConfirmationDetector(
            minimum_rejection_body_ratio=thresholds.RETEST_MINIMUM_REJECTION_BODY_RATIO,
            bullish_close_location_minimum=thresholds.RETEST_BULLISH_CLOSE_LOCATION_MINIMUM,
            bearish_close_location_maximum=thresholds.RETEST_BEARISH_CLOSE_LOCATION_MAXIMUM,
            minimum_rejection_wick_ratio=thresholds.RETEST_MINIMUM_REJECTION_WICK_RATIO,
            maximum_confirmation_candles=thresholds.RETEST_MAXIMUM_CONFIRMATION_CANDLES,
        ),
        market_regime_validator=MarketRegimeValidator(
            adx_trending_minimum=thresholds.ADX_TRENDING_MIN,
            adx_rejection_maximum=thresholds.ADX_REJECTION_MAX,
            ema_flat_threshold=thresholds.EMA_FLAT_THRESHOLD,
            minimum_atr_expansion_ratio=thresholds.MARKET_REGIME_MINIMUM_ATR_EXPANSION_RATIO,
            compression_lookback=thresholds.VOLATILITY_COMPRESSION_LOOKBACK,
            minimum_candle_range_ratio=thresholds.VOLATILITY_MINIMUM_CANDLE_RANGE_RATIO,
        ),
        htf_bias_validator=HigherTimeframeBiasValidator(),
        liquidity_sweep_validator=LiquiditySweepValidator(),
        structure_shift_validator=StructureShiftValidator(),
        volume_confirmation_validator=VolumeConfirmationValidator(),
        entry_zone_validator=EntryZoneValidator(),
        premium_discount_validator=PremiumDiscountValidator(),
        retest_confirmation_validator=RetestConfirmationValidator(),
        session_filter=SessionFilter(),
        btc_alignment_validator=BTCAlignmentValidator(),
        fake_breakout_filter=FakeBreakoutFilter(
            maximum_reversal_candles=thresholds.FAKE_BREAKOUT_MAXIMUM_REVERSAL_CANDLES,
            return_inside_tolerance_ratio=thresholds.FAKE_BREAKOUT_RETURN_INSIDE_TOLERANCE_RATIO,
        ),
        layer_weights=layer_weights,
        max_score=float(thresholds.SCORE_MAXIMUM_RAW),
    )


def build_scanner_service(*, on_event=None, on_cycle_result=None) -> ScannerService:
    """
    Construct a fully wired ScannerService using centralized Settings
    and thresholds.

    Does not make any API call, does not start scanning, and does not
    create global mutable singleton state; each call returns an
    independent ScannerService with its own strategy engine, semaphore,
    duplicate guard, and candidate buffer.

    `on_event`, when provided, is passed straight through to
    ScannerService as its optional runtime-event observer (e.g. for a
    dashboard WebSocket broadcast). It never affects strategy behaviour.
    """
    settings = get_settings()

    strategy_engine = build_strategy_engine()
    preview_analyzer = build_preview_analyzer()
    semaphore = asyncio.Semaphore(settings.max_concurrent_scans)
    duplicate_guard = DuplicateSignalGuard(
        retention_seconds=settings.duplicate_signal_retention_seconds,
        maximum_entries=settings.duplicate_signal_maximum_entries,
    )
    pair_scanner = PairScanner(
        strategy_engine=strategy_engine,
        duplicate_guard=duplicate_guard,
        semaphore=semaphore,
        preview_analyzer=preview_analyzer,
    )

    pair_discovery_service = None
    configured_pair_provider = get_configured_pairs
    if settings.dynamic_pair_discovery_enabled:
        discovery_market_data_provider = BybitMarketDataProvider(
            base_url=settings.exchange_base_url,
            request_timeout_seconds=settings.request_timeout_seconds,
        )

        async def _on_pair_list_refreshed(updated: bool, current_pairs: list) -> None:
            if on_event is None:
                return
            from datetime import datetime, timezone

            from app.scanner.scanner_events import ScannerEvent, ScannerEventType

            await on_event(
                ScannerEvent(
                    event=ScannerEventType.PAIR_LIST_REFRESHED,
                    timestamp_utc=datetime.now(timezone.utc),
                    data={"updated": updated, "pair_count": len(current_pairs)},
                )
            )

        pair_discovery_service = DynamicPairDiscoveryService(
            market_data_provider=discovery_market_data_provider,
            minimum_open_interest_usdt=settings.pair_discovery_minimum_open_interest_usdt,
            minimum_turnover_24h_usdt=settings.pair_discovery_minimum_turnover_24h_usdt,
            refresh_interval_seconds=settings.pair_discovery_interval_seconds,
            maximum_pairs=settings.pair_discovery_maximum_pairs,
            on_refresh=_on_pair_list_refreshed,
        )
        set_pair_source(pair_discovery_service.get_current_pairs)
        configured_pair_provider = pair_discovery_service.get_current_pairs
    else:
        set_pair_source(None)

    scheduler = MultiPairScanScheduler(
        pair_scanner=pair_scanner,
        configured_pair_provider=configured_pair_provider,
        scanner_interval_seconds=settings.scanner_interval_seconds,
        maximum_concurrent_scans=settings.max_concurrent_scans,
    )
    candidate_buffer = ValidSignalCandidateBuffer(
        maximum_size=settings.candidate_buffer_maximum_size
    )
    active_state_provider = EmptyActiveTradingStateProvider()

    signal_storage_service = None
    if settings.enable_signal_persistence:
        from app.storage.analytics_repository import AnalyticsRepository
        from app.storage.database import DatabaseManager
        from app.storage.signal_repository import SignalRepository
        from app.storage.signal_service import SignalStorageService

        database_manager = DatabaseManager(settings.database_url)
        signal_storage_service = SignalStorageService(
            signal_builder=InstitutionalSignalBuilder(),
            signal_repository=SignalRepository(database_manager),
            analytics_repository=AnalyticsRepository(
                database_manager, enabled=settings.enable_rejection_analytics
            ),
            settings=settings,
        )

    notification_service = None
    if settings.telegram_enabled:
        from app.notifications.notification_service import SignalNotificationService
        from app.notifications.telegram_client import TelegramBotClient
        from app.notifications.telegram_formatter import TelegramSignalFormatter
        from app.notifications.telegram_notifier import TelegramSignalNotifier

        telegram_client = TelegramBotClient(
            bot_token=settings.telegram_bot_token,
            chat_ids=settings.telegram_chat_ids,
            api_base_url=settings.telegram_api_base_url,
            request_timeout_seconds=settings.telegram_request_timeout_seconds,
            max_retries=settings.telegram_max_retries,
            retry_delay_seconds=settings.telegram_retry_delay_seconds,
            disable_web_page_preview=settings.telegram_disable_web_page_preview,
        )
        telegram_notifier = TelegramSignalNotifier(
            telegram_client=telegram_client,
            formatter=TelegramSignalFormatter(),
            enabled=True,
        )
        notification_service = SignalNotificationService(telegram_notifier=telegram_notifier)

    return ScannerService(
        scheduler=scheduler,
        candidate_buffer=candidate_buffer,
        active_state_provider=active_state_provider,
        signal_storage_service=signal_storage_service,
        storage_failure_is_fatal=settings.storage_failure_is_fatal,
        notification_service=notification_service,
        pair_discovery_service=pair_discovery_service,
        on_event=on_event,
        on_cycle_result=on_cycle_result,
    )


async def initialize_scanner_storage(scanner_service: ScannerService) -> None:
    """
    Initialize the local SQLite schema for a ScannerService's signal
    storage, when persistence is enabled. A no-op when persistence is
    disabled. Does not perform any other API call.
    """
    signal_storage_service = scanner_service.signal_storage_service
    if signal_storage_service is None:
        return
    await signal_storage_service.database_manager.initialize()


async def dispose_scanner_notifications(scanner_service: ScannerService) -> None:
    """
    Close the Telegram HTTP client owned by a ScannerService's
    notification service, when Telegram is enabled. A no-op when
    notifications are disabled.
    """
    notification_service = scanner_service.notification_service
    if notification_service is None:
        return
    await notification_service.telegram_notifier.telegram_client.close()
