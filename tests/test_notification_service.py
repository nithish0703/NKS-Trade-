"""
Tests for app.notifications.notification_service.SignalNotificationService.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.signal import Direction, Signal, SignalStatus
from app.notifications.notification_service import SignalNotificationService
from app.notifications.results import NotificationStatus, TelegramNotificationResult
from app.storage.signal_service import SignalStorageResult

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _signal(trade_id="SMC-1") -> Signal:
    return Signal(
        trade_id=trade_id,
        coin="BTC-USDT",
        direction=Direction.BUY,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_reward_ratio=3.0,
        status=SignalStatus.CONFIRMED,
        liquidity_type="EQUAL_HIGH",
        entry_zone_type="ORDER_BLOCK",
        structure_confirmation="BOS",
        detection_time_utc=UTC_NOW,
        institutional_reason="Confirmed setup facts only.",
        setup_key=f"setup-{trade_id}",
        liquidity_sweep_id="sweep-1",
        structure_break_id="break-1",
        entry_zone_id="zone-1",
        created_at_utc=UTC_NOW,
    )


def _storage_result(*, stored=True, duplicate=False, signal=None, reason=None) -> SignalStorageResult:
    return SignalStorageResult(
        signal=signal if signal is not None else (_signal() if stored else None),
        stored=stored,
        duplicate=duplicate,
        reason=reason,
    )


def _build_service(send_result=None):
    telegram_notifier = MagicMock()
    telegram_notifier.notify = AsyncMock(
        return_value=send_result
        or [
            TelegramNotificationResult(
                status=NotificationStatus.SENT,
                telegram_message_id=1,
                sent_at_utc=UTC_NOW,
                attempt_count=1,
            )
        ]
    )
    return SignalNotificationService(telegram_notifier=telegram_notifier), telegram_notifier


class TestProcessStorageResult:
    async def test_newly_stored_confirmed_sends(self):
        service, telegram_notifier = _build_service()
        results = await service.process_storage_result(
            _storage_result(signal=_signal())
        )
        assert results[0].status == NotificationStatus.SENT
        telegram_notifier.notify.assert_awaited_once()

    async def test_duplicate_storage_does_not_send(self):
        service, telegram_notifier = _build_service()
        results = await service.process_storage_result(
            _storage_result(stored=False, duplicate=True, signal=_signal(), reason="duplicate")
        )
        assert results is None
        telegram_notifier.notify.assert_not_called()

    async def test_failed_storage_does_not_send(self):
        service, telegram_notifier = _build_service()
        results = await service.process_storage_result(
            _storage_result(stored=False, duplicate=False, signal=None, reason="build failed")
        )
        assert results is None
        telegram_notifier.notify.assert_not_called()

    async def test_missing_signal_does_not_send(self):
        service, telegram_notifier = _build_service()
        results = await service.process_storage_result(
            SignalStorageResult(signal=None, stored=True, duplicate=False)
        )
        assert results is None
        telegram_notifier.notify.assert_not_called()

    async def test_none_storage_result_does_not_send(self):
        service, telegram_notifier = _build_service()
        results = await service.process_storage_result(None)
        assert results is None
        telegram_notifier.notify.assert_not_called()

    async def test_telegram_disabled_skips(self):
        telegram_notifier = MagicMock()
        telegram_notifier.notify = AsyncMock(
            return_value=[
                TelegramNotificationResult(
                    status=NotificationStatus.SKIPPED, reason="Telegram notifications are disabled."
                )
            ]
        )
        service = SignalNotificationService(telegram_notifier=telegram_notifier)
        results = await service.process_storage_result(_storage_result())
        assert results[0].status == NotificationStatus.SKIPPED

    async def test_send_failure_does_not_alter_stored_signal(self):
        service, telegram_notifier = _build_service(
            send_result=[
                TelegramNotificationResult(
                    status=NotificationStatus.FAILED, reason="network error", attempt_count=3
                )
            ]
        )
        storage_result = _storage_result()
        original_signal = storage_result.signal
        results = await service.process_storage_result(storage_result)
        assert results[0].status == NotificationStatus.FAILED
        assert storage_result.signal == original_signal
        assert storage_result.stored is True

    async def test_multiple_chat_results_all_returned(self):
        service, telegram_notifier = _build_service(
            send_result=[
                TelegramNotificationResult(
                    status=NotificationStatus.SENT,
                    telegram_message_id=1,
                    chat_id_suffix="...0001",
                    sent_at_utc=UTC_NOW,
                    attempt_count=1,
                ),
                TelegramNotificationResult(
                    status=NotificationStatus.SENT,
                    telegram_message_id=2,
                    chat_id_suffix="...0002",
                    sent_at_utc=UTC_NOW,
                    attempt_count=1,
                ),
            ]
        )
        results = await service.process_storage_result(_storage_result())
        assert len(results) == 2

    async def test_no_persistence_side_effect(self):
        service, telegram_notifier = _build_service()
        # SignalNotificationService should have no repository/database attributes at all.
        assert not hasattr(service, "_signal_repository")
        assert not hasattr(service, "_database_manager")
        await service.process_storage_result(_storage_result())


class TestProcessStorageResults:
    async def test_ordered_batch_processing(self):
        service, telegram_notifier = _build_service()
        results = [
            _storage_result(signal=_signal(trade_id="SMC-1")),
            _storage_result(stored=False, duplicate=True, signal=_signal(trade_id="SMC-2")),
            _storage_result(signal=_signal(trade_id="SMC-3")),
        ]
        notification_results = await service.process_storage_results(results)
        assert len(notification_results) == 2
        assert telegram_notifier.notify.await_count == 2

    async def test_flattens_multiple_chat_results_across_signals(self):
        service, telegram_notifier = _build_service(
            send_result=[
                TelegramNotificationResult(
                    status=NotificationStatus.SENT,
                    telegram_message_id=1,
                    chat_id_suffix="...0001",
                    sent_at_utc=UTC_NOW,
                    attempt_count=1,
                ),
                TelegramNotificationResult(
                    status=NotificationStatus.SENT,
                    telegram_message_id=2,
                    chat_id_suffix="...0002",
                    sent_at_utc=UTC_NOW,
                    attempt_count=1,
                ),
            ]
        )
        results = [
            _storage_result(signal=_signal(trade_id="SMC-1")),
            _storage_result(signal=_signal(trade_id="SMC-2")),
        ]
        notification_results = await service.process_storage_results(results)
        # 2 stored signals x 2 chats each = 4 flattened results.
        assert len(notification_results) == 4
