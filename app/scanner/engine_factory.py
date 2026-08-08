"""
Constructs the fully wired scanner service, using the 5-stage
PipelineStrategyEngine (HTF Bias -> Liquidity Sweep -> BOS -> IFVG ->
Order Flow -> Signal) as the sole strategy engine.
"""

import asyncio
from typing import Optional

from app.config import thresholds
from app.config.pairs import get_configured_pairs, set_pair_source
from app.config.settings import get_settings
from app.data.binance_market_data_provider import (
    BinanceFuturesMarketDataProvider,
    create_shared_rate_limiter,
)
from app.data.provider_base import MarketDataProvider
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


def build_strategy_engine(*, market_data_provider: Optional[MarketDataProvider] = None) -> PipelineStrategyEngine:
    """
    Construct a fully wired PipelineStrategyEngine using production
    dependency instances, centralized Settings and thresholds.

    Does not make any API call, start scanning, or create global
    mutable singleton state; each call returns an independent engine
    instance with its own market-data provider, unless `market_data_provider`
    is supplied (see build_scanner_service, which injects one sharing its
    rate limiter with the dynamic-pair-discovery provider).
    """
    return build_pipeline_strategy_engine(market_data_provider=market_data_provider)


def build_scanner_service(
    *,
    on_event=None,
    on_cycle_result=None,
    on_pair_result=None,
    apply_scan_lease: bool = True,
    scan_lease_holder_id: str = "scan-cli",
) -> ScannerService:
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

    `apply_scan_lease` (default True) wires the DB-backed scan-cycle
    lease (see thresholds.SCANNER_LEASE_DURATION_MULTIPLIER) into the
    returned scheduler when persistence is enabled -- appropriate for
    the two long-running, recurring loops the lease exists to
    deduplicate (`python main.py scan` and the dashboard API's
    background scanner). Every `build_scanner_service()` call
    constructs a lease guard with a brand-new random holder id, so a
    one-off caller with no persistent identity across invocations (the
    `scan-once` CLI mode) must pass `apply_scan_lease=False`: otherwise
    a *previous* `scan-once` invocation's still-unexpired lease
    (duration = scanner_interval_seconds * SCANNER_LEASE_DURATION_MULTIPLIER,
    15 minutes at current defaults) silently locks out every
    `scan-once` run after the first one, each with total_pairs=0.

    `scan_lease_holder_id` (default "scan-cli", matching `python
    main.py scan`) must be stable across restarts of the same
    long-running caller so a process killed by Ctrl+C or a crash
    reclaims its own lease immediately on restart instead of waiting
    out the full lease duration (see MonitorLeaseGuard's holder_id
    behaviour) -- but must differ between genuinely different
    long-running callers (the dashboard API passes "dashboard-api") so
    the two still exclude each other correctly.
    """
    settings = get_settings()

    # One rate limiter shared by every BinanceFuturesMarketDataProvider
    # this function constructs (scan provider and, when enabled, the
    # dynamic-pair-discovery provider): Binance enforces request weight
    # and 418/429 bans per IP, not per provider object, so without
    # sharing, a ban signalled to one provider (e.g. discovery's Open
    # Interest fetches) would never slow down the other's candle
    # fetches against the same already-banned IP.
    shared_rate_limiter = create_shared_rate_limiter()

    strategy_engine = build_strategy_engine(
        market_data_provider=BinanceFuturesMarketDataProvider(
            base_url=settings.exchange_base_url,
            request_timeout_seconds=settings.request_timeout_seconds,
            rate_limiter=shared_rate_limiter,
        )
    )
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
            rate_limiter=shared_rate_limiter,
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

    # Built before the scheduler (rather than after, as in an earlier
    # version of this function) so a DB-backed scan lease can be wired
    # into MultiPairScanScheduler below whenever persistence is enabled.
    signal_storage_service = None
    scan_lease_guard = None
    if settings.enable_signal_persistence:
        from app.storage.analytics_repository import AnalyticsRepository
        from app.storage.database import DatabaseManager
        from app.storage.monitor_lease import MonitorLeaseGuard
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
        # So `python main.py scan` and the dashboard API's uvicorn
        # process -- each building a fully independent ScannerService
        # via this same function -- never both fetch candles and scan
        # the same cycle against the same database (see
        # thresholds.SCANNER_LEASE_DURATION_MULTIPLIER). Skipped for a
        # one-off caller (apply_scan_lease=False, e.g. `scan-once`):
        # such a caller has no persistent identity across invocations,
        # so it would otherwise be locked out by its own previous run's
        # still-unexpired lease.
        if apply_scan_lease:
            scan_lease_guard = MonitorLeaseGuard(
                database_manager,
                lease_name="scanner_cycle",
                lease_duration_seconds=(
                    settings.scanner_interval_seconds * thresholds.SCANNER_LEASE_DURATION_MULTIPLIER
                ),
                holder_id=scan_lease_holder_id,
            )

    scheduler = MultiPairScanScheduler(
        pair_scanner=pair_scanner,
        configured_pair_provider=configured_pair_provider,
        scanner_interval_seconds=settings.scanner_interval_seconds,
        maximum_concurrent_scans=settings.max_concurrent_scans,
        retry_count_provider=lambda: strategy_engine._market_data_provider.total_retry_count,
        lease_guard=scan_lease_guard,
    )
    candidate_buffer = ValidSignalCandidateBuffer(
        maximum_size=settings.candidate_buffer_maximum_size
    )
    active_state_provider = EmptyActiveTradingStateProvider()

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
