"""
WebSocket connection manager for the dashboard live-update feed.

Broadcasting is best-effort: a disconnected or slow dashboard client can
never affect scanner/strategy processing, and one client's failure never
prevents delivery to other connected clients.
"""

import asyncio
import logging
from typing import Optional

from fastapi import WebSocket

from app.api.schemas import DashboardWebSocketEvent
from app.scanner.scanner_events import ScannerEvent

logger = logging.getLogger(__name__)

_HEARTBEAT_EVENT = "HEARTBEAT"


class DashboardWebSocketManager:
    """Tracks connected dashboard WebSocket clients and broadcasts safe events to all of them."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def broadcast_event(self, event: ScannerEvent) -> None:
        payload = DashboardWebSocketEvent(
            event=event.event.value, timestamp_utc=event.timestamp_utc, data=event.data
        )
        await self._broadcast_json(payload.model_dump(mode="json"))

    async def broadcast_heartbeat(self) -> None:
        from datetime import datetime, timezone

        payload = DashboardWebSocketEvent(
            event=_HEARTBEAT_EVENT, timestamp_utc=datetime.now(timezone.utc), data={}
        )
        await self._broadcast_json(payload.model_dump(mode="json"))

    async def _broadcast_json(self, payload: dict) -> None:
        async with self._lock:
            connections = list(self._connections)

        for websocket in connections:
            try:
                await websocket.send_json(payload)
            except Exception:  # noqa: BLE001 - one client's failure must not affect others
                logger.debug("Dropping unresponsive dashboard WebSocket client.")
                await self.disconnect(websocket)


async def on_scanner_event(manager: DashboardWebSocketManager, event: ScannerEvent) -> None:
    """Adapter used as ScannerService's on_event callback."""
    await manager.broadcast_event(event)
