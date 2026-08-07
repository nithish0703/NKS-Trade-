"""
Dashboard aggregation service: maps real backend/runtime/storage data
into dashboard API response models. Contains no trading strategy logic
and never fabricates values.
"""

from datetime import datetime, timezone
from typing import Optional

from app.api.access_tier import AccessTier
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
)
from app.config.pairs import get_configured_pairs
from app.config.timeframes import ENTRY_TIMEFRAME
from app.data.provider_base import MarketDataProvider
from app.models.signal import Direction, Signal
from app.scanner.pipeline_results import PipelineStageResult
from app.scanner.scan_results import PairScanResult, PairScanStatus, ScanCycleResult
from app.scanner.scanner_events import ScannerEvent, ScannerEventType
from app.storage.analytics_repository import AnalyticsRepository
from app.storage.signal_repository import (
    DASHBOARD_STATUS_ACTIVE,
    DASHBOARD_STATUS_CLOSED_LOSS,
    DASHBOARD_STATUS_CLOSED_WIN,
    DASHBOARD_STATUS_NEW,
    SignalNotFoundError,
    SignalRepository,
    SignalWithStatus,
)

# Dashboard-only "Scanning Coins" ranking score: how many of the
# 5-stage pipeline's mandatory stages a symbol has cleared so far this
# cycle, out of the total stage count. This is a pure ranking/display
# aid for the Scanning Coins panel only -- it is never read back into
# the strategy pipeline and never influences whether a signal is
# generated (that decision is purely binary CONFIRMED/REJECTED, per
# calculate_pipeline_decision in app.strategy_pipeline.scoring).
def calculate_validation_progress(
    stages: Optional[list[PipelineStageResult]],
) -> tuple[int, int, Optional[str]]:
    """
    Compute a dashboard-only "how many stages did this scan clear"
    progress count from a StrategyPipelineResult's stage audit trail.

    Returns (stages_passed, stages_total, last_executed_layer).
    """
    if not stages:
        return 0, 0, None

    stages_passed = 0
    last_executed_layer: Optional[str] = None

    for stage in sorted(stages, key=lambda s: s.stage_order):
        if stage.executed:
            last_executed_layer = stage.layer_name
            if stage.passed:
                stages_passed += 1

    return stages_passed, len(stages), last_executed_layer


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
    last/failed layer, reason, order-flow confidence/reason). Never
    includes candles, secrets, tokens, or chat IDs, and never affects
    strategy, storage, or notification behaviour — this is a pure,
    read-only projection of one pair result.
    """
    pipeline_result = pair_result.pipeline_result
    direction = pipeline_result.expected_direction if pipeline_result is not None else None
    stages = pipeline_result.stages if pipeline_result is not None else None
    stages_passed, stages_total, last_executed_layer = calculate_validation_progress(stages)
    percentage = round((stages_passed / stages_total) * 100) if stages_total else None
    failed_layer = pipeline_result.failed_layer if pipeline_result is not None else None
    order_flow_confidence = pipeline_result.order_flow_confidence if pipeline_result is not None else None
    order_flow_reason = pipeline_result.order_flow_reason if pipeline_result is not None else None

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
            "order_flow_confidence": order_flow_confidence,
            "order_flow_reason": order_flow_reason,
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


def build_trade_outcome_updated_event(closed_result: SignalWithStatus) -> ScannerEvent:
    """
    Build a single TRADE_OUTCOME_UPDATED ScannerEvent for a signal
    TradeOutcomeMonitor just closed out (WIN or LOSS), for dashboard
    WebSocket visibility only. Carries only fields already exposed by
    the REST active-signals/summary responses; never candles, secrets,
    tokens, or chat IDs. Never affects strategy, storage, or
    notification behaviour -- a pure, read-only projection.
    """
    signal = closed_result.signal
    return ScannerEvent(
        event=ScannerEventType.TRADE_OUTCOME_UPDATED,
        timestamp_utc=datetime.now(timezone.utc),
        data={
            "trade_id": signal.trade_id,
            "coin": signal.coin,
            "direction": signal.direction.value,
            "outcome": closed_result.dashboard_status,
        },
    )


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
        confirmed_signals = await self._signal_repository.list_recent(limit=100000)
        confirmed_count = len(confirmed_signals)

        average_rr: Optional[float] = None
        if confirmed_signals:
            average_rr = sum(signal.risk_reward_ratio for signal in confirmed_signals) / len(
                confirmed_signals
            )

        cycle_result = await self._runtime_store.get_latest_cycle_result()
        last_scan_time_utc = cycle_result.completed_at_utc if cycle_result is not None else None

        # Wins/losses/open_signals are real counts from TradeOutcomeMonitor,
        # which closes an ACTIVE (dashboard "Trade" button) signal out as
        # CLOSED_WIN/CLOSED_LOSS once its take_profit/stop_loss is
        # touched by the live price. A signal that was never activated,
        # or is activated but not yet closed, contributes to neither
        # count. win_rate is None (never 0) until at least one signal
        # has closed, so an empty "0%" is never shown as if it were a
        # measured result.
        wins = await self._signal_repository.count_by_dashboard_status(DASHBOARD_STATUS_CLOSED_WIN)
        losses = await self._signal_repository.count_by_dashboard_status(DASHBOARD_STATUS_CLOSED_LOSS)
        open_signals = await self._signal_repository.count_by_dashboard_status(DASHBOARD_STATUS_ACTIVE)
        closed_total = wins + losses
        win_rate = (wins / closed_total * 100) if closed_total > 0 else None

        return DashboardSummary(
            total_signals=total_signals,
            wins=wins,
            losses=losses,
            open_signals=open_signals,
            win_rate=win_rate,
            average_rr=average_rr,
            confirmed_count=confirmed_count,
            scanner_running=self._runtime_store.scanner_running,
            last_scan_time_utc=last_scan_time_utc,
            server_time_utc=datetime.now(timezone.utc),
            comparison=ComparisonPercentages(),
        )

    async def get_scanning_coins(self) -> list[ScanningCoin]:
        pair_results = await self._runtime_store.get_latest_pair_results()
        configured_pairs = get_configured_pairs()
        # Single bulk call for every symbol's live price, rather than one
        # request per coin -- dashboard-display only, never used by the
        # strategy pipeline. Falls back to price=None per-coin (rendered
        # as "--") if the exchange call fails; never blocks the rest of
        # the scanning-coins response.
        price_by_symbol = await self._market_data_provider.fetch_all_ticker_prices()

        items: list[ScanningCoin] = []
        for symbol in configured_pairs:
            pair_result = pair_results.get(symbol)
            if pair_result is None:
                items.append(
                    ScanningCoin(
                        coin=symbol,
                        price=price_by_symbol.get(symbol),
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
                        chart_trend=None,
                        order_flow_confidence=None,
                        order_flow_reason=None,
                    )
                )
                continue
            items.append(self._to_scanning_coin(pair_result, price=price_by_symbol.get(symbol)))
        return items

    @staticmethod
    def _to_scanning_coin(pair_result: PairScanResult, *, price: Optional[float] = None) -> ScanningCoin:
        pipeline_result = pair_result.pipeline_result
        direction = pipeline_result.expected_direction if pipeline_result is not None else None

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
        order_flow_confidence = pipeline_result.order_flow_confidence if pipeline_result is not None else None
        order_flow_reason = pipeline_result.order_flow_reason if pipeline_result is not None else None

        stages = pipeline_result.stages if pipeline_result is not None else None
        stages_passed, stages_total, last_executed_layer = calculate_validation_progress(stages)
        percentage = round((stages_passed / stages_total) * 100) if stages_total else None

        # Scanning Coins ranking-only score: how many pipeline stages
        # this symbol has cleared so far, out of the total stage count.
        # Never used for signal generation -- see calculate_pipeline_decision.
        score: Optional[float] = float(stages_passed) if stages else None

        market_context = pipeline_result.market_context if pipeline_result is not None else None
        chart_trend = calculate_chart_trend(market_context)

        return ScanningCoin(
            coin=pair_result.symbol,
            price=price,
            direction=direction,
            score=score,
            status=status,
            failed_layer=failed_layer,
            reason=reason,
            updated_at_utc=pair_result.completed_at_utc,
            validation_progress_raw_score=float(stages_passed) if stages else None,
            validation_progress_max_score=float(stages_total) if stages else None,
            validation_progress_percentage=percentage,
            last_executed_layer=last_executed_layer,
            chart_trend=chart_trend,
            order_flow_confidence=order_flow_confidence,
            order_flow_reason=order_flow_reason,
        )

    async def get_active_signals(
        self, limit: int = 50, *, tier: AccessTier = AccessTier.FREE
    ) -> list[ActiveSignal]:
        # Active Signals shows only signals the user has explicitly moved
        # here via the dashboard "Trade" action (dashboard_status ==
        # ACTIVE). This is a pure UI state transition, never a real
        # order/position: no exchange call is made to activate a signal.
        results = await self._signal_repository.list_recent_with_status(
            limit=limit, dashboard_status=DASHBOARD_STATUS_ACTIVE
        )
        active_signals = [result.signal for result in results]
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
                    status=signal.status.value if tier == AccessTier.PREMIUM else None,
                    detection_time_utc=signal.detection_time_utc,
                    dashboard_status=DASHBOARD_STATUS_ACTIVE,
                )
            )
        return items

    async def get_premium_signals(
        self, limit: int = 50, *, tier: AccessTier = AccessTier.FREE
    ) -> list[PremiumSignal]:
        # Once a signal is activated (moved to Active Signals via the
        # dashboard "Trade" action), it is excluded from this list so it
        # appears in exactly one list at a time.
        results = await self._signal_repository.list_recent_with_status(
            limit=limit, dashboard_status=DASHBOARD_STATUS_NEW
        )
        return [
            PremiumSignal(
                trade_id=r.signal.trade_id,
                coin=r.signal.coin,
                direction=r.signal.direction.value,
                status=r.signal.status.value if tier == AccessTier.PREMIUM else None,
                entry_price=r.signal.entry_price,
                take_profit=r.signal.take_profit,
                stop_loss=r.signal.stop_loss,
                detection_time_utc=r.signal.detection_time_utc,
            )
            for r in results
        ]

    async def activate_signal(
        self, trade_id: str, *, tier: AccessTier = AccessTier.FREE
    ) -> Optional[ActiveSignal]:
        """
        Mark a stored CONFIRMED signal as ACTIVE (dashboard-only state
        transition). Returns the resulting ActiveSignal, or None if no
        signal exists with `trade_id`.

        This never places an order, never calls any exchange API, never
        recalculates risk, and never touches Telegram. It only updates
        the signal's dashboard_status so it is included in
        get_active_signals and excluded from get_premium_signals from
        this point on.
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
            status=signal.status.value if tier == AccessTier.PREMIUM else None,
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
            started_at_utc=self._runtime_store.started_at_utc,
        )

    async def get_signal_details(
        self, trade_id: str, *, tier: AccessTier = AccessTier.FREE
    ) -> Optional[SignalDetails]:
        result = await self._signal_repository.get_by_trade_id_with_status(trade_id)
        if result is None:
            return None
        signal = result.signal
        return SignalDetails(
            trade_id=signal.trade_id,
            coin=signal.coin,
            direction=signal.direction.value,
            status=signal.status.value if tier == AccessTier.PREMIUM else None,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            risk_reward_ratio=signal.risk_reward_ratio,
            liquidity_type=signal.liquidity_type,
            entry_zone_type=signal.entry_zone_type,
            structure_confirmation=signal.structure_confirmation,
            detection_time_utc=signal.detection_time_utc,
            institutional_reason=signal.institutional_reason,
            dashboard_status=result.dashboard_status,
        )
