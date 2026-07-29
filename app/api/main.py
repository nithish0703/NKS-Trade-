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
from app.api.dashboard_service import DashboardService, build_pair_scan_updated_events
from app.api.runtime_store import DashboardRuntimeStore
from app.api.websocket_manager import DashboardWebSocketManager
from app.config.settings import get_settings
from app.data.market_data_provider import OKXMarketDataProvider
from app.scanner.engine_factory import (
    build_scanner_service,
    dispose_scanner_notifications,
    initialize_scanner_storage,
)
from app.storage.analytics_repository import AnalyticsRepository
from app.storage.database import DatabaseManager
from app.storage.signal_repository import SignalRepository

logger = logging.getLogger(__name__)

_DASHBOARD_ACCOUNT_BALANCE = 10_000.0


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

    market_data_provider = OKXMarketDataProvider(
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

    scanner_task = None
    if settings.dashboard_api_enabled:
        async def _on_scanner_event(event) -> None:
            await runtime_store.record_event(event)
            if settings.dashboard_websocket_enabled:
                await websocket_manager.broadcast_event(event)

        async def _on_cycle_result(cycle_result) -> None:
            await runtime_store.record_cycle_result(cycle_result)
            for pair_event in build_pair_scan_updated_events(cycle_result):
                await runtime_store.record_event(pair_event)
                if settings.dashboard_websocket_enabled:
                    await websocket_manager.broadcast_event(pair_event)

        scanner_service = build_scanner_service(
            on_event=_on_scanner_event, on_cycle_result=_on_cycle_result
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
