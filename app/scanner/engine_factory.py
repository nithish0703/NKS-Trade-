"""
Constructs the fully wired scanner service, using the 5-stage
PipelineStrategyEngine (HTF Bias -> Liquidity Sweep -> BOS -> IFVG ->
Order Flow -> Signal) as the sole strategy engine.
"""

import asyncio

from app.config.pairs import get_configured_pairs, set_pair_source
from app.config.settings import get_settings
from app.data.binance_market_data_provider import BinanceFuturesMarketDataProvider
from app.scanner.active_state import EmptyActiveTradingStateProvider
from app.scanner.candidate_buffer import ValidSignalCandidateBuffer
from app.scanner.duplicate_guard import DuplicateSignalGuard
from app.scanner.pair_discovery import DynamicPairDiscoveryService
from app.scanner.pair_scanner import PairScanner
from app.scanner.scan_scheduler import MultiPairScanScheduler
from app.scanner.scanner_service import ScannerService
from app.scanner.signal_builder import InstitutionalSignalBuilder
from app.strategy_pipeline.engine import PipelineStrategyEngine
from app.strategy_pipeline.factory import build_pipeline_strategy_engine

# Storage submodules are imported lazily inside build_scanner_service() and
# initialize_scanner_storage() to avoid a module-level circular import:
# app.storage's package __init__ imports app.scanner.pipeline_results, which
# (via app.scanner's package __init__) re-enters this module.


def build_strategy_engine() -> PipelineStrategyEngine:
    """
    Construct a fully wired PipelineStrategyEngine using production
    dependency instances, centralized Settings and thresholds.

    Does not make any API call, start scanning, or create global
    mutable singleton state; each call returns an independent engine
    instance with its own CandleRepository and market-data provider.
    """
    return build_pipeline_strategy_engine()


def build_scanner_service(*, on_event=None, on_cycle_result=None, on_pair_result=None) -> ScannerService:
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

    `on_pair_result`, when provided, is passed through to PairScanner and
    invoked the instant each individual pair's scan finishes -- not
    batched at cycle-end -- so a dashboard observer can reflect each
    coin's result in real time as it lands, independent of how long
    other pairs in the same cycle take. Never affects strategy behaviour.
    """
    settings = get_settings()

    strategy_engine = build_strategy_engine()
    semaphore = asyncio.Semaphore(settings.max_concurrent_scans)
    duplicate_guard = DuplicateSignalGuard(
        retention_seconds=settings.duplicate_signal_retention_seconds,
        maximum_entries=settings.duplicate_signal_maximum_entries,
    )
    pair_scanner = PairScanner(
        strategy_engine=strategy_engine,
        duplicate_guard=duplicate_guard,
        semaphore=semaphore,
        on_pair_result=on_pair_result,
    )

    pair_discovery_service = None
    configured_pair_provider = get_configured_pairs
    if settings.dynamic_pair_discovery_enabled:
        discovery_market_data_provider = BinanceFuturesMarketDataProvider(
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
        retry_count_provider=lambda: strategy_engine._market_data_provider.total_retry_count,
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
            # Same DuplicateSignalGuard instance used by PairScanner to
            # check for duplicates during scanning; SignalStorageService
            # only marks a setup as seen in it after a successful save.
            duplicate_guard=duplicate_guard,
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
