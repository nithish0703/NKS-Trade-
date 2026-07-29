"""
Tests for ScannerService's optional on_event / on_cycle_result runtime
observer hooks (used by the dashboard). These hooks are purely
observational and must never affect scanner/strategy behaviour.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.scanner.scanner_events import ScannerEventType
from app.scanner.scanner_service import ScannerService
from tests.test_scanner_service import UTC_NOW, _active_state, _cycle_result, _pair_result

pytestmark = pytest.mark.asyncio


def _build_service_with_hooks(scheduler=None, on_event=None, on_cycle_result=None):
    return ScannerService(
        scheduler=scheduler or MagicMock(),
        candidate_buffer=MagicMock(add=AsyncMock()),
        active_state_provider=MagicMock(get_state=AsyncMock(return_value=_active_state())),
        on_event=on_event,
        on_cycle_result=on_cycle_result,
    )


class TestOnEventHook:
    async def test_scan_cycle_started_and_completed_emitted(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))
        events = []

        async def _on_event(event):
            events.append(event)

        service = _build_service_with_hooks(scheduler=scheduler, on_event=_on_event)
        await service.run_single_cycle(10000.0)

        event_types = [event.event for event in events]
        assert ScannerEventType.SCAN_CYCLE_STARTED in event_types
        assert ScannerEventType.SCAN_CYCLE_COMPLETED in event_types

    async def test_signal_stored_event_emitted(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))
        signal_storage_service = MagicMock()
        signal = MagicMock()
        signal.trade_id = "SMC-1"
        signal.coin = "BTC-USDT"
        signal.direction.value = "BUY"
        signal.signal_type.value = "PREMIUM"
        storage_result = MagicMock(signal=signal, stored=True, duplicate=False)
        signal_storage_service.process_cycle = AsyncMock(return_value=[storage_result])

        events = []

        async def _on_event(event):
            events.append(event)

        service = ScannerService(
            scheduler=scheduler,
            candidate_buffer=MagicMock(add=AsyncMock()),
            active_state_provider=MagicMock(get_state=AsyncMock(return_value=_active_state())),
            signal_storage_service=signal_storage_service,
            on_event=_on_event,
        )
        await service.run_single_cycle(10000.0)

        stored_events = [e for e in events if e.event == ScannerEventType.SIGNAL_STORED]
        assert len(stored_events) == 1
        assert stored_events[0].data["trade_id"] == "SMC-1"

    async def test_event_data_never_contains_secrets(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))
        events = []

        async def _on_event(event):
            events.append(event)

        service = _build_service_with_hooks(scheduler=scheduler, on_event=_on_event)
        await service.run_single_cycle(10000.0)

        for event in events:
            data_text = str(event.data).lower()
            assert "token" not in data_text
            assert "bot_token" not in data_text

    async def test_event_broadcast_failure_does_not_crash_scanner(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))

        async def _failing_on_event(event):
            raise RuntimeError("websocket broadcast failed")

        service = _build_service_with_hooks(scheduler=scheduler, on_event=_failing_on_event)
        # Must not raise.
        result = await service.run_single_cycle(10000.0)
        assert result is not None

    async def test_no_event_callback_is_a_no_op(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))
        service = _build_service_with_hooks(scheduler=scheduler, on_event=None)
        # Must not raise even with no observer attached.
        result = await service.run_single_cycle(10000.0)
        assert result is not None


class TestOnCycleResultHook:
    async def test_on_cycle_result_receives_full_cycle_result(self):
        scheduler = MagicMock()
        cycle_result = _cycle_result([_pair_result()])
        scheduler.run_cycle = AsyncMock(return_value=cycle_result)

        received = []

        async def _on_cycle_result(result):
            received.append(result)

        service = _build_service_with_hooks(scheduler=scheduler, on_cycle_result=_on_cycle_result)
        await service.run_single_cycle(10000.0)

        assert len(received) == 1
        assert received[0] is cycle_result

    async def test_on_cycle_result_failure_does_not_crash_scanner(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))

        async def _failing_on_cycle_result(result):
            raise RuntimeError("store failed")

        service = _build_service_with_hooks(scheduler=scheduler, on_cycle_result=_failing_on_cycle_result)
        result = await service.run_single_cycle(10000.0)
        assert result is not None
