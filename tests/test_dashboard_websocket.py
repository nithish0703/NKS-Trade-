"""
Tests for app.api.websocket_manager.DashboardWebSocketManager and the
scanner->dashboard event broadcast payload contract.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.websocket_manager import DashboardWebSocketManager
from app.scanner.scanner_events import ScannerEvent, ScannerEventType

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _event(event_type=ScannerEventType.SIGNAL_STORED, data=None) -> ScannerEvent:
    return ScannerEvent(event=event_type, timestamp_utc=UTC_NOW, data=data or {})


class TestBroadcastPayload:
    async def test_broadcast_event_payload_shape(self):
        manager = DashboardWebSocketManager()
        websocket = MagicMock()
        websocket.accept = AsyncMock()
        websocket.send_json = AsyncMock()
        await manager.connect(websocket)

        await manager.broadcast_event(
            _event(ScannerEventType.SIGNAL_STORED, {"trade_id": "SMC-1", "coin": "BTC-USDT"})
        )

        websocket.send_json.assert_awaited_once()
        payload = websocket.send_json.call_args.args[0]
        assert payload["event"] == "SIGNAL_STORED"
        assert payload["data"] == {"trade_id": "SMC-1", "coin": "BTC-USDT"}
        assert "timestamp_utc" in payload

    async def test_broadcast_never_includes_secrets(self):
        manager = DashboardWebSocketManager()
        websocket = MagicMock()
        websocket.accept = AsyncMock()
        websocket.send_json = AsyncMock()
        await manager.connect(websocket)

        await manager.broadcast_event(
            _event(ScannerEventType.TELEGRAM_SENT, {"trade_id": "SMC-1", "chat_id_suffix": "...1234"})
        )

        payload = websocket.send_json.call_args.args[0]
        payload_text = str(payload)
        assert "bot_token" not in payload_text
        assert "8886680874" not in payload_text  # never a full chat ID

    async def test_broadcast_to_multiple_clients(self):
        manager = DashboardWebSocketManager()
        clients = []
        for _ in range(3):
            websocket = MagicMock()
            websocket.accept = AsyncMock()
            websocket.send_json = AsyncMock()
            await manager.connect(websocket)
            clients.append(websocket)

        await manager.broadcast_event(_event())

        for websocket in clients:
            websocket.send_json.assert_awaited_once()


class TestDisconnectIsolation:
    async def test_one_client_failure_does_not_prevent_others(self):
        manager = DashboardWebSocketManager()

        failing_client = MagicMock()
        failing_client.accept = AsyncMock()
        failing_client.send_json = AsyncMock(side_effect=RuntimeError("connection reset"))
        await manager.connect(failing_client)

        healthy_client = MagicMock()
        healthy_client.accept = AsyncMock()
        healthy_client.send_json = AsyncMock()
        await manager.connect(healthy_client)

        await manager.broadcast_event(_event())

        healthy_client.send_json.assert_awaited_once()

    async def test_failing_client_is_disconnected(self):
        manager = DashboardWebSocketManager()
        failing_client = MagicMock()
        failing_client.accept = AsyncMock()
        failing_client.send_json = AsyncMock(side_effect=RuntimeError("connection reset"))
        await manager.connect(failing_client)

        assert manager.connection_count == 1
        await manager.broadcast_event(_event())
        assert manager.connection_count == 0

    async def test_explicit_disconnect_removes_client(self):
        manager = DashboardWebSocketManager()
        websocket = MagicMock()
        websocket.accept = AsyncMock()
        await manager.connect(websocket)
        assert manager.connection_count == 1

        await manager.disconnect(websocket)
        assert manager.connection_count == 0

    async def test_broadcast_with_no_clients_does_not_raise(self):
        manager = DashboardWebSocketManager()
        await manager.broadcast_event(_event())  # must not raise


class TestScannerEventModel:
    def test_event_timestamp_must_be_utc(self):
        with pytest.raises(Exception):
            ScannerEvent(
                event=ScannerEventType.SCAN_CYCLE_STARTED,
                timestamp_utc=datetime(2026, 1, 1, 10, 0),  # naive
                data={},
            )

    def test_all_required_event_types_exist(self):
        expected = {
            "SCAN_CYCLE_STARTED",
            "PAIR_SCAN_UPDATED",
            "SCAN_CYCLE_COMPLETED",
            "SIGNAL_STORED",
            "SIGNAL_DUPLICATE",
            "TELEGRAM_SENT",
            "TELEGRAM_FAILED",
            "SCANNER_STATUS_CHANGED",
            "PAIR_LIST_REFRESHED",
            "TRADE_OUTCOME_UPDATED",
        }
        actual = {member.value for member in ScannerEventType}
        assert expected == actual
