"""
FastAPI application entry point for the dashboard API.

Runs the dashboard API and the scanner in the same process/event loop
so dashboard routes and the WebSocket feed can read live runtime state
directly. When DASHBOARD_API_ENABLED is False, the app still starts
(so `uvicorn app.api.main:app` always works) but the scanner background
task is not started; dashboard routes then report empty/"waiting" data.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import dashboard, health, scanner, signals, websocket
from app.api.dashboard_service import (
    DashboardService,
    build_pair_scan_updated_event,
    build_pair_scan_updated_events,
    build_trade_outcome_updated_event,
)
from app.api.runtime_store import DashboardRuntimeStore
from app.api.websocket_manager import DashboardWebSocketManager
from app.config.settings import get_settings
from app.config.thresholds import SIGNAL_OUTCOME_MONITOR_LEASE_DURATION_MULTIPLIER
from app.data.binance_market_data_provider import BinanceFuturesMarketDataProvider
from app.monitoring.signal_outcome_monitor import SignalOutcomeMonitor
from app.scanner.engine_factory import (
    build_scanner_service,
    dispose_scanner_notifications,
    initialize_scanner_storage,
)
from app.storage.analytics_repository import AnalyticsRepository
from app.storage.database import DatabaseManager
from app.storage.monitor_lease import MonitorLeaseGuard
from app.storage.signal_repository import (
    OUTCOME_SOURCE_MANUAL_ACTIVATION,
    SignalRepository,
    SignalWithStatus,
)
from app.utils.logger import configure_logging

logger = logging.getLogger(__name__)

_DASHBOARD_ACCOUNT_BALANCE = 10_000.0
# Must match main.py's _SIGNAL_OUTCOME_MONITOR_LEASE_NAME exactly -- the
# same lease row coordinates both processes.
_SIGNAL_OUTCOME_MONITOR_LEASE_NAME = "signal_outcome_monitor"

# Configured at import time (before the lifespan context runs, and
# before any scanner/discovery log call can happen) so every module's
# logging.getLogger(__name__) call is visible from the very first line
# of startup, not just after the app object is constructed.
configure_logging(get_settings().log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    database_manager = DatabaseManager(settings.database_url)
    await database_manager.initialize()
    signal_repository = SignalRepository(database_manager)
    analytics_repository = (
        AnalyticsRepository(database_manager, enabled=settings.enable_rejection_analytics)
        if settings.enable_rejection_analytics
        else None
    )

    market_data_provider = BinanceFuturesMarketDataProvider(
        base_url=settings.exchange_base_url,
        request_timeout_seconds=settings.request_timeout_seconds,
    )

    runtime_store = DashboardRuntimeStore()
    websocket_manager = DashboardWebSocketManager()

    dashboard_service = DashboardService(
        signal_repository=signal_repository,
        analytics_repository=analytics_repository,
        runtime_store=runtime_store,
        market_data_provider=market_data_provider,
        telegram_enabled=settings.telegram_enabled,
        websocket_enabled=settings.dashboard_websocket_enabled,
    )

    app.state.settings = settings
    app.state.database_manager = database_manager
    app.state.signal_repository = signal_repository
    app.state.analytics_repository = analytics_repository
    app.state.market_data_provider = market_data_provider
    app.state.runtime_store = runtime_store
    app.state.websocket_manager = websocket_manager
    app.state.dashboard_service = dashboard_service
    app.state.scanner_service = None
    app.state.signal_outcome_monitor = None

    scanner_task = None
    if settings.dashboard_api_enabled:
        async def _on_scanner_event(event) -> None:
            await runtime_store.record_event(event)
            if settings.dashboard_websocket_enabled:
                await websocket_manager.broadcast_event(event)

        async def _on_cycle_result(cycle_result) -> None:
            # Per-pair results have already been recorded/broadcast in
            # real time via _on_pair_result as each pair's scan finished;
            # this only stores the final cycle-level aggregate (rejection
            # breakdown, counts, ranking) that isn't known until every
            # pair in the cycle has completed.
            await runtime_store.record_cycle_result(cycle_result)

        async def _on_pair_result(pair_result) -> None:
            await runtime_store.record_pair_result(pair_result)
            if settings.dashboard_websocket_enabled:
                await websocket_manager.broadcast_event(build_pair_scan_updated_event(pair_result))

        scanner_service = build_scanner_service(
            on_event=_on_scanner_event,
            on_cycle_result=_on_cycle_result,
            on_pair_result=_on_pair_result,
            # Stable across restarts of this same process/mode (see
            # MonitorLeaseGuard's holder_id docs) so a crashed or
            # Ctrl+C'd dashboard API reclaims its own scan lease
            # immediately instead of waiting out the full lease
            # duration; must differ from main.py's "scan-cli" so the
            # two still exclude each other correctly.
            scan_lease_holder_id="dashboard-api",
        )
        await initialize_scanner_storage(scanner_service)
        app.state.scanner_service = scanner_service

        async def _run_scanner() -> None:
            runtime_store.set_scanner_running(True)
            try:
                await scanner_service.run_forever(_DASHBOARD_ACCOUNT_BALANCE)
            except Exception as exc:  # noqa: BLE001 - keep the API process alive
                logger.error("Scanner background task stopped unexpectedly: %s", exc)
            finally:
                runtime_store.set_scanner_running(False)

        scanner_task = asyncio.create_task(_run_scanner())

    signal_outcome_monitor = None
    signal_outcome_monitor_task = None
    if settings.dashboard_api_enabled and settings.signal_outcome_monitor_enabled:

        async def _on_signal_closed(result) -> None:
            # Only broadcast to the dashboard WebSocket when the close
            # was also mirrored onto dashboard_status (the signal was
            # ACTIVE) -- a pure background PASSIVE_TRACKING close (the
            # vast majority of CONFIRMED signals, since most are never
            # activated) has no dashboard-visible state change and must
            # not spam the feed with closes the UI never showed as open.
            if result.outcome_source == OUTCOME_SOURCE_MANUAL_ACTIVATION and settings.dashboard_websocket_enabled:
                await websocket_manager.broadcast_event(
                    build_trade_outcome_updated_event(
                        SignalWithStatus(signal=result.signal, dashboard_status=result.dashboard_status)
                    )
                )

        lease_guard = MonitorLeaseGuard(
            database_manager,
            lease_name=_SIGNAL_OUTCOME_MONITOR_LEASE_NAME,
            lease_duration_seconds=(
                settings.signal_outcome_monitor_interval_seconds
                * SIGNAL_OUTCOME_MONITOR_LEASE_DURATION_MULTIPLIER
            ),
            holder_id="dashboard-api-monitor",
        )
        signal_outcome_monitor = SignalOutcomeMonitor(
            signal_repository=signal_repository,
            market_data_provider=market_data_provider,
            interval_seconds=settings.signal_outcome_monitor_interval_seconds,
            on_signal_closed=_on_signal_closed,
            lease_guard=lease_guard,
        )
        app.state.signal_outcome_monitor = signal_outcome_monitor

        async def _run_signal_outcome_monitor() -> None:
            try:
                await signal_outcome_monitor.run_forever()
            except Exception as exc:  # noqa: BLE001 - keep the API process alive
                logger.error("Signal outcome monitor background task stopped unexpectedly: %s", exc)

        signal_outcome_monitor_task = asyncio.create_task(_run_signal_outcome_monitor())

    try:
        yield
    finally:
        if scanner_task is not None:
            scanner_service = app.state.scanner_service
            scanner_service.request_shutdown()
            await asyncio.wait_for(scanner_task, timeout=30.0)
            await dispose_scanner_notifications(scanner_service)
            if scanner_service.signal_storage_service is not None:
                await scanner_service.signal_storage_service.database_manager.dispose()

        if signal_outcome_monitor_task is not None:
            signal_outcome_monitor.request_shutdown()
            await asyncio.wait_for(signal_outcome_monitor_task, timeout=30.0)

        await database_manager.dispose()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="NexTrade SMC Trading Engine Dashboard API",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.dashboard_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(dashboard.router)
    app.include_router(signals.router)
    app.include_router(scanner.router)
    app.include_router(websocket.router)

    return app


app = create_app()
