"""
FastAPI dependency providers for the dashboard API.

All shared runtime objects (database manager, scanner service, runtime
store, WebSocket manager) are constructed once at application startup
and attached to `app.state`; these dependencies simply read them back.
"""

from typing import Optional

from fastapi import Request

from app.api.dashboard_service import DashboardService
from app.api.runtime_store import DashboardRuntimeStore
from app.api.websocket_manager import DashboardWebSocketManager
from app.storage.signal_repository import SignalRepository


def get_dashboard_service(request: Request) -> DashboardService:
    return request.app.state.dashboard_service


def get_signal_repository(request: Request) -> SignalRepository:
    return request.app.state.signal_repository


def get_runtime_store(request: Request) -> DashboardRuntimeStore:
    return request.app.state.runtime_store


def get_websocket_manager(request: Request) -> DashboardWebSocketManager:
    return request.app.state.websocket_manager


def get_scanner_service(request: Request) -> Optional[object]:
    return getattr(request.app.state, "scanner_service", None)
