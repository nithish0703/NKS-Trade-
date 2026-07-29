"""
Dashboard live-update WebSocket endpoint.
"""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.websocket_manager import DashboardWebSocketManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

_HEARTBEAT_INTERVAL_SECONDS = 20.0


@router.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket) -> None:
    manager: DashboardWebSocketManager = websocket.app.state.websocket_manager
    await manager.connect(websocket)

    heartbeat_task = asyncio.create_task(_heartbeat_loop(websocket, manager))
    try:
        while True:
            # The dashboard client does not need to send anything; this
            # simply waits for a disconnect or an incoming ping/pong.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - a client-side failure must not crash the server
        logger.debug("Dashboard WebSocket connection ended unexpectedly.")
    finally:
        heartbeat_task.cancel()
        await manager.disconnect(websocket)


async def _heartbeat_loop(websocket: WebSocket, manager: DashboardWebSocketManager) -> None:
    try:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
            try:
                await websocket.send_json({"event": "PING"})
            except Exception:  # noqa: BLE001 - disconnect will be handled by the main receive loop
                break
    except asyncio.CancelledError:
        pass
