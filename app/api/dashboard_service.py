"""
Dashboard aggregation service: maps real backend/runtime/storage data
into dashboard API response models. Contains no trading strategy logic
and never fabricates values.
"""

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from app.api.runtime_store import DashboardRuntimeStore
from app.api.schemas import (
    ActiveSignal,
    ComparisonPercentages,
    DashboardHealth,
    DashboardSummary,
    PremiumSignal,
    RejectionItem,
    ScanningCoin,
    ScannerStatusItem,
    SignalDetails,
    StrongSignal,
)
from app.config.pairs import get_configured_pairs
from app.config.timeframes import ENTRY_TIMEFRAME
from app.config.thresholds import (
    PREMIUM_SIGNAL_MIN_SCORE,
    SCORE_BTC_ALIGNMENT,
    SCORE_ENTRY_ZONE,
    SCORE_FAKE_BREAKOUT,
    SCORE_HTF_BIAS,
    SCORE_LIQUIDITY_SWEEP,
    SCORE_MARKET_REGIME,
    SCORE_MAXIMUM_RAW,
    SCORE_PREMIUM_DISCOUNT,
    SCORE_RETEST_CONFIRMATION,
    SCORE_SESSION,
    SCORE_STRUCTURE_SHIFT,
    SCORE_VOLUME_CONFIRMATION,
    STRONG_SIGNAL_MIN_SCORE,
)
from app.data.provider_base import MarketDataProvider
from app.models.signal import Direction, Signal, SignalType
from app.scanner.pipeline_results import PipelineStageResult, PipelineStatus
from app.scanner.scan_results import PairScanResult, PairScanStatus, ScanCycleResult
from app.scanner.scanner_events import ScannerEvent, ScannerEventType
from app.storage.analytics_repository import AnalyticsRepository
from app.storage.signal_repository import (
    DASHBOARD_STATUS_ACTIVE,
    DASHBOARD_STATUS_NEW,
    SignalNotFoundError,
    SignalRepository,
)

# Dashboard-only scan-progress weights. These mirror the exact layer
# weights ConfidenceScoringEngine already uses (app/config/thresholds.py)
# so the displayed progress reflects the real strategy weighting, but
# this mapping is never read by the scoring engine itself and never
# feeds back into signal classification, storage, or notification.
_VALIDATION_PROGRESS_LAYER_WEIGHTS: dict[str, float] = {
    "MARKET_REGIME": SCORE_MARKET_REGIME,
    "HTF_BIAS": SCORE_HTF_BIAS,
    "LIQUIDITY_SWEEP": SCORE_LIQUIDITY_SWEEP,
    "STRUCTURE_SHIFT": SCORE_STRUCTURE_SHIFT,
    "VOLUME_CONFIRMATION": SCORE_VOLUME_CONFIRMATION,
    "ENTRY_ZONE": SCORE_ENTRY_ZONE,
    "PREMIUM_DISCOUNT": SCORE_PREMIUM_DISCOUNT,
    "RETEST_CONFIRMATION": SCORE_RETEST_CONFIRMATION,
    "SESSION_FILTER": SCORE_SESSION,
    "BTC_ALIGNMENT": SCORE_BTC_ALIGNMENT,
    "FAKE_BREAKOUT_FILTER": SCORE_FAKE_BREAKOUT,
}
_VALIDATION_PROGRESS_MAX_SCORE: float = float(SCORE_MAXIMUM_RAW)


def calculate_validation_progress(
    stages: Optional[list[PipelineStageResult]],
) -> tuple[float, float, int, Optional[str]]:
    """
    Compute a dashboard-only "how far did this scan get" progress score
    from a StrategyPipelineResult's stage audit trail.

    This never recalculates any validator, never touches
    ConfidenceScoringEngine, and never affects PREMIUM/STRONG/MEDIUM/
    IGNORE classification. It only reads `executed`/`passed` off stages
    that already ran and awards each scoring layer's existing fixed
    weight (or zero), purely for scanner visibility.

    Returns (raw_score, max_score, percentage, last_executed_layer).
    percentage is rounded to the nearest whole number. Non-scoring
    stages (CANDLE_QUALITY, RISK_MANAGEMENT, CONFIDENCE_SCORING) are
    ignored entirely.
    """
    if not stages:
        return 0.0, _VALIDATION_PROGRESS_MAX_SCORE, 0, None

    raw_score = 0.0
    last_executed_layer: Optional[str] = None

    for stage in sorted(stages, key=lambda s: s.stage_order):
        if stage.executed:
            last_executed_layer = stage.layer_name

        weight = _VALIDATION_PROGRESS_LAYER_WEIGHTS.get(stage.layer_name)
        if weight is None:
            continue
        if stage.executed and stage.passed:
            raw_score += weight

    raw_percentage = Decimal(str(raw_score)) / Decimal(str(_VALIDATION_PROGRESS_MAX_SCORE)) * 100
    percentage = int(raw_percentage.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return raw_score, _VALIDATION_PROGRESS_MAX_SCORE, percentage, last_executed_layer


def calculate_chart_trend(market_context) -> Optional[str]:
    """
    Dashboard-only visual cue: whether the most recently completed
    entry-timeframe candle closed higher or lower than the one before
    it. This is a raw price observation only -- it never reads HTF
    bias, never runs a validator, and is never used as `direction` or
    `preview_direction` (which remain HTF-bias-derived and never
    inferred from candles alone, per the strategy's no-fabrication
    rule). Purely a "which way is price moving right now" chart cue.

    Returns "UP", "DOWN", or None when fewer than two entry-timeframe
    candles are available.
    """
    if market_context is None or not market_context.candles_by_timeframe:
        return None
    candles = market_context.candles_by_timeframe.get(ENTRY_TIMEFRAME)
    if not candles or len(candles) < 2:
        return None
    latest, previous = candles[-1], candles[-2]
    if latest.close > previous.close:
        return "UP"
    if latest.close < previous.close:
        return "DOWN"
    return None


def build_pair_scan_updated_event(pair_result: PairScanResult) -> ScannerEvent:
    """
    Build a single PAIR_SCAN_UPDATED ScannerEvent for one pair's scan
    result, for dashboard WebSocket visibility only.

    Carries only the same safe fields the scanning-coins REST response
    already exposes (coin, direction, validation-progress percentage,
    last/failed layer, reason). Never includes candles, secrets, tokens,
    or chat IDs, and never affects strategy, storage, or notification
    behaviour — this is a pure, read-only projection of one pair result.
    """
    pipeline_result = pair_result.pipeline_result
    direction = pipeline_result.expected_direction if pipeline_result is not None else None
    stages = pipeline_result.stages if pipeline_result is not None else None
    _, _, percentage, last_executed_layer = calculate_validation_progress(stages)
    failed_layer = pipeline_result.failed_layer if pipeline_result is not None else None

    return ScannerEvent(
        event=ScannerEventType.PAIR_SCAN_UPDATED,
        timestamp_utc=pair_result.completed_at_utc,
        data={
            "coin": pair_result.symbol,
            "direction": direction,
            "validation_progress_percentage": percentage if stages else None,
            "last_executed_layer": last_executed_layer,
            "failed_layer": failed_layer,
            "reason": pair_result.reason,
        },
    )


def build_pair_scan_updated_events(cycle_result: ScanCycleResult) -> list[ScannerEvent]:
    """
    Build one PAIR_SCAN_UPDATED ScannerEvent per pair in a completed scan
    cycle, for dashboard WebSocket visibility only. Retained for any
    consumer that still wants a full-cycle batch (e.g. backfilling a
    freshly connected WebSocket client with the latest known state);
    real-time per-pair delivery during a running cycle instead uses
    `build_pair_scan_updated_event` directly from PairScanner's
    `on_pair_result` callback as each pair finishes.
    """
    return [build_pair_scan_updated_event(pair_result) for pair_result in cycle_result.pair_results]


def _distance_to_take_profit_percentage(
    *, direction: Direction, current_price: Optional[float], take_profit: float
) -> Optional[float]:
    if current_price is None or current_price <= 0:
        return None
    if direction == Direction.BUY:
        return ((take_profit - current_price) / current_price) * 100
    return ((current_price - take_profit) / current_price) * 100


class DashboardService:
    """
    Builds dashboard API responses from the signal repository, analytics
    repository, runtime store, and market-data provider. Never invents
    scores, prices, or outcome statistics.
    """

    def __init__(
        self,
        *,
        signal_repository: SignalRepository,
        analytics_repository: Optional[AnalyticsRepository],
        runtime_store: DashboardRuntimeStore,
        market_data_provider: MarketDataProvider,
        telegram_enabled: bool,
        websocket_enabled: bool,
    ) -> None:
        self._signal_repository = signal_repository
        self._analytics_repository = analytics_repository
        self._runtime_store = runtime_store
        self._market_data_provider = market_data_provider
        self._telegram_enabled = telegram_enabled
        self._websocket_enabled = websocket_enabled

    async def get_summary(self) -> DashboardSummary:
        total_signals = await self._signal_repository.count()
        premium_signals = await self._signal_repository.list_recent(
            limit=100000, signal_type=SignalType.PREMIUM.value
        )
        strong_signals = await self._signal_repository.list_recent(
            limit=100000, signal_type=SignalType.STRONG.value
        )
        premium_count = len(premium_signals)
        strong_count = len(strong_signals)

        all_signals = premium_signals + strong_signals
        average_rr: Optional[float] = None
        if all_signals:
            average_rr = sum(signal.risk_reward_ratio for signal in all_signals) / len(all_signals)

        cycle_result = await self._runtime_store.get_latest_cycle_result()
        last_scan_time_utc = cycle_result.completed_at_utc if cycle_result is not None else None

        # No trade-outcome tracking exists yet: wins/losses/win_rate and
        # open_signals are honestly reported as zero/None rather than
        # inferred from current price or fabricated.
        return DashboardSummary(
            total_signals=total_signals,
            wins=0,
            losses=0,
            open_signals=0,
            win_rate=None,
            average_rr=average_rr,
            premium_count=premium_count,
            strong_count=strong_count,
            scanner_running=self._runtime_store.scanner_running,
            last_scan_time_utc=last_scan_time_utc,
            server_time_utc=datetime.now(timezone.utc),
            comparison=ComparisonPercentages(),
        )

    async def get_scanning_coins(self) -> list[ScanningCoin]:
        pair_results = await self._runtime_store.get_latest_pair_results()
        configured_pairs = get_configured_pairs()

        items: list[ScanningCoin] = []
        for symbol in configured_pairs:
            pair_result = pair_results.get(symbol)
            if pair_result is None:
                items.append(
                    ScanningCoin(
                        coin=symbol,
                        direction=None,
                        score=None,
                        status="SCANNING",
                        failed_layer=None,
                        reason=None,
                        updated_at_utc=None,
                        validation_progress_raw_score=None,
                        validation_progress_max_score=None,
                        validation_progress_percentage=None,
                        last_executed_layer=None,
                        preview_direction=None,
                        preview_progress_raw_score=None,
                        preview_progress_max_score=None,
                        preview_progress_percentage=None,
                        preview_completed_layers=None,
                        preview_failed_layers=None,
                        preview_data_availability=None,
                        chart_trend=None,
                    )
                )
                continue
            items.append(self._to_scanning_coin(pair_result))
        return items

    @staticmethod
    def _to_scanning_coin(pair_result: PairScanResult) -> ScanningCoin:
        pipeline_result = pair_result.pipeline_result
        direction = pipeline_result.expected_direction if pipeline_result is not None else None

        score: Optional[float] = None
        if (
            pipeline_result is not None
            and pipeline_result.confidence_result is not None
            and pipeline_result.status == PipelineStatus.VALID
        ):
            score = pipeline_result.confidence_result.normalized_score

        status_map = {
            PairScanStatus.VALID: "READY",
            PairScanStatus.REJECTED: "REJECTED",
            PairScanStatus.ERROR: "ERROR",
            PairScanStatus.DUPLICATE: "DUPLICATE",
            PairScanStatus.SKIPPED: "SCANNING",
        }
        status = status_map[pair_result.status]

        failed_layer = pipeline_result.failed_layer if pipeline_result is not None else None
        reason = pair_result.reason

        stages = pipeline_result.stages if pipeline_result is not None else None
        raw_score, max_score, percentage, last_executed_layer = calculate_validation_progress(stages)

        preview = pair_result.preview_result
        preview_data_availability = (
            {layer: status.value for layer, status in preview.preview_data_availability.items()}
            if preview is not None
            else None
        )

        market_context = pipeline_result.market_context if pipeline_result is not None else None
        chart_trend = calculate_chart_trend(market_context)

        return ScanningCoin(
            coin=pair_result.symbol,
            direction=direction,
            score=score,
            status=status,
            failed_layer=failed_layer,
            reason=reason,
            updated_at_utc=pair_result.completed_at_utc,
            validation_progress_raw_score=raw_score if stages else None,
            validation_progress_max_score=max_score if stages else None,
            validation_progress_percentage=percentage if stages else None,
            last_executed_layer=last_executed_layer,
            preview_direction=preview.preview_direction if preview is not None else None,
            preview_progress_raw_score=preview.preview_progress_raw_score if preview is not None else None,
            preview_progress_max_score=preview.preview_progress_max_score if preview is not None else None,
            preview_progress_percentage=(
                preview.preview_progress_percentage if preview is not None else None
            ),
            preview_completed_layers=preview.preview_completed_layers if preview is not None else None,
            preview_failed_layers=preview.preview_failed_layers if preview is not None else None,
            preview_data_availability=preview_data_availability,
            chart_trend=chart_trend,
        )

    async def get_active_signals(self, limit: int = 50) -> list[ActiveSignal]:
        # Active Signals shows only signals the user has explicitly moved
        # here via the dashboard "Trade" action (dashboard_status ==
        # ACTIVE). This is a pure UI state transition, never a real
        # order/position: no exchange call is made to activate a signal.
        active_signals: list[Signal] = []
        for signal_type in (SignalType.PREMIUM, SignalType.STRONG):
            results = await self._signal_repository.list_recent_with_status(
                limit=limit, signal_type=signal_type.value, dashboard_status=DASHBOARD_STATUS_ACTIVE
            )
            active_signals.extend(result.signal for result in results)
        active_signals.sort(key=lambda signal: signal.created_at_utc, reverse=True)
        active_signals = active_signals[:limit]

        items: list[ActiveSignal] = []
        for signal in active_signals:
            current_price = await self._market_data_provider.fetch_ticker_price(signal.coin)
            distance = _distance_to_take_profit_percentage(
                direction=signal.direction,
                current_price=current_price,
                take_profit=signal.take_profit,
            )
            items.append(
                ActiveSignal(
                    trade_id=signal.trade_id,
                    coin=signal.coin,
                    direction=signal.direction.value,
                    current_price=current_price,
                    entry_price=signal.entry_price,
                    take_profit=signal.take_profit,
                    stop_loss=signal.stop_loss,
                    distance_to_take_profit_percentage=distance,
                    confidence_score=signal.confidence_score,
                    signal_type=signal.signal_type.value,
                    detection_time_utc=signal.detection_time_utc,
                    dashboard_status=DASHBOARD_STATUS_ACTIVE,
                )
            )
        return items

    async def get_premium_signals(self, limit: int = 50) -> list[PremiumSignal]:
        # Once a signal is activated (moved to Active Signals via the
        # dashboard "Trade" action), it is excluded from Premium/Strong
        # so it appears in exactly one list at a time.
        results = await self._signal_repository.list_recent_with_status(
            limit=limit, signal_type=SignalType.PREMIUM.value, dashboard_status=DASHBOARD_STATUS_NEW
        )
        signals = [r.signal for r in results if r.signal.confidence_score >= PREMIUM_SIGNAL_MIN_SCORE]
        return [
            PremiumSignal(
                trade_id=signal.trade_id,
                coin=signal.coin,
                direction=signal.direction.value,
                signal_type=signal.signal_type.value,
                entry_price=signal.entry_price,
                take_profit=signal.take_profit,
                stop_loss=signal.stop_loss,
                confidence_score=signal.confidence_score,
                detection_time_utc=signal.detection_time_utc,
            )
            for signal in signals
        ]

    async def get_strong_signals(self, limit: int = 50) -> list[StrongSignal]:
        results = await self._signal_repository.list_recent_with_status(
            limit=limit, signal_type=SignalType.STRONG.value, dashboard_status=DASHBOARD_STATUS_NEW
        )
        signals = [
            r.signal
            for r in results
            if STRONG_SIGNAL_MIN_SCORE <= r.signal.confidence_score < PREMIUM_SIGNAL_MIN_SCORE
        ]
        return [
            StrongSignal(
                trade_id=signal.trade_id,
                coin=signal.coin,
                direction=signal.direction.value,
                confidence_score=signal.confidence_score,
                higher_timeframe_bias=signal.higher_timeframe_bias,
                normalized_score=signal.confidence_score,
                detection_time_utc=signal.detection_time_utc,
            )
            for signal in signals
        ]

    async def activate_signal(self, trade_id: str) -> Optional[ActiveSignal]:
        """
        Mark a stored PREMIUM/STRONG signal as ACTIVE (dashboard-only
        state transition). Returns the resulting ActiveSignal, or None
        if no signal exists with `trade_id`.

        This never places an order, never calls any exchange API, never
        recalculates confidence/risk, and never touches Telegram. It
        only updates the signal's dashboard_status so it is included in
        get_active_signals and excluded from get_premium_signals /
        get_strong_signals from this point on.
        """
        try:
            result = await self._signal_repository.mark_active(trade_id)
        except SignalNotFoundError:
            return None

        signal = result.signal
        current_price = await self._market_data_provider.fetch_ticker_price(signal.coin)
        distance = _distance_to_take_profit_percentage(
            direction=signal.direction,
            current_price=current_price,
            take_profit=signal.take_profit,
        )
        return ActiveSignal(
            trade_id=signal.trade_id,
            coin=signal.coin,
            direction=signal.direction.value,
            current_price=current_price,
            entry_price=signal.entry_price,
            take_profit=signal.take_profit,
            stop_loss=signal.stop_loss,
            distance_to_take_profit_percentage=distance,
            confidence_score=signal.confidence_score,
            signal_type=signal.signal_type.value,
            detection_time_utc=signal.detection_time_utc,
            dashboard_status=DASHBOARD_STATUS_ACTIVE,
        )

    async def get_recent_rejections(self, limit: int = 50) -> list[RejectionItem]:
        if self._analytics_repository is None:
            return []
        records = await self._analytics_repository.list_recent(limit=limit)
        return [
            RejectionItem(
                coin=record.symbol,
                failed_layer=record.failed_layer,
                reason=record.rejection_reason,
                detection_time_utc=record.detection_time_utc,
            )
            for record in records
        ]

    async def get_scanner_status_items(self) -> list[ScannerStatusItem]:
        pair_results = await self._runtime_store.get_latest_pair_results()
        configured_pairs = get_configured_pairs()

        items: list[ScannerStatusItem] = []
        for symbol in configured_pairs:
            pair_result = pair_results.get(symbol)
            if pair_result is None:
                items.append(
                    ScannerStatusItem(
                        pair=symbol, stage=None, status="SCANNING", last_scan_time_utc=None
                    )
                )
                continue

            status_map = {
                PairScanStatus.VALID: "READY",
                PairScanStatus.REJECTED: "REJECTED",
                PairScanStatus.ERROR: "ERROR",
                PairScanStatus.DUPLICATE: "DUPLICATE",
                PairScanStatus.SKIPPED: "SCANNING",
            }
            stage = (
                pair_result.pipeline_result.failed_layer
                if pair_result.pipeline_result is not None
                else None
            )
            items.append(
                ScannerStatusItem(
                    pair=symbol,
                    stage=stage,
                    status=status_map[pair_result.status],
                    last_scan_time_utc=pair_result.completed_at_utc,
                )
            )
        return items

    async def get_health(self) -> DashboardHealth:
        database_reachable = True
        try:
            await self._signal_repository.count()
        except Exception:  # noqa: BLE001 - health check must report, not raise
            database_reachable = False

        return DashboardHealth(
            scanner_running=self._runtime_store.scanner_running,
            database_reachable=database_reachable,
            telegram_enabled=self._telegram_enabled,
            websocket_enabled=self._websocket_enabled,
            server_time_utc=datetime.now(timezone.utc),
        )

    async def get_signal_details(self, trade_id: str) -> Optional[SignalDetails]:
        result = await self._signal_repository.get_by_trade_id_with_status(trade_id)
        if result is None:
            return None
        signal = result.signal
        return SignalDetails(
            trade_id=signal.trade_id,
            coin=signal.coin,
            direction=signal.direction.value,
            signal_type=signal.signal_type.value,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            risk_reward_ratio=signal.risk_reward_ratio,
            confidence_score=signal.confidence_score,
            market_regime=signal.market_regime.value,
            higher_timeframe_bias=signal.higher_timeframe_bias,
            liquidity_type=signal.liquidity_type,
            entry_zone_type=signal.entry_zone_type,
            structure_confirmation=signal.structure_confirmation,
            volume_confirmation=signal.volume_confirmation,
            atr_status=signal.atr_status,
            trading_session=signal.trading_session,
            btc_market_alignment=signal.btc_market_alignment,
            detection_time_utc=signal.detection_time_utc,
            institutional_reason=signal.institutional_reason,
            dashboard_status=result.dashboard_status,
        )
