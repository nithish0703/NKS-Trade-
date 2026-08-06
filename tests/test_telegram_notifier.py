"""
Tests for app.notifications.telegram_notifier.TelegramSignalNotifier.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.signal import Direction, Signal, SignalStatus
from app.notifications.results import NotificationStatus, TelegramNotificationResult
from app.notifications.telegram_formatter import TelegramSignalFormatter
from app.notifications.telegram_notifier import TelegramSignalNotifier

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _signal(status=SignalStatus.CONFIRMED) -> Signal:
    fields = dict(
        trade_id="SMC-BTC-USDT-BUY-abc123",
        coin="BTC-USDT",
        direction=Direction.BUY,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_reward_ratio=3.0,
        status=status,
        liquidity_type="EQUAL_HIGH",
        entry_zone_type="ORDER_BLOCK",
        structure_confirmation="BOS",
        detection_time_utc=UTC_NOW,
        institutional_reason="Confirmed setup facts only.",
        setup_key="setup-1",
        liquidity_sweep_id="sweep-1",
        structure_break_id="break-1",
        entry_zone_id="zone-1",
        created_at_utc=UTC_NOW,
    )
    if status == SignalStatus.CONFIRMED:
        return Signal(**fields)
    # REJECTED can never pass Signal's own construction-time
    # _signal_status_must_be_confirmed validator, so build via
    # model_construct to exercise TelegramSignalNotifier's own
    # defensive status check.
    return Signal.model_construct(**fields)


def _sent_results(count=1) -> list[TelegramNotificationResult]:
    return [
        TelegramNotificationResult(
            status=NotificationStatus.SENT,
            telegram_message_id=index + 1,
            chat_id_suffix=f"...{1000 + index}",
            sent_at_utc=UTC_NOW,
            attempt_count=1,
        )
        for index in range(count)
    ]


def _build_notifier(enabled=True, send_result=None):
    telegram_client = MagicMock()
    telegram_client.send_message = AsyncMock(return_value=send_result or _sent_results())
    return TelegramSignalNotifier(
        telegram_client=telegram_client,
        formatter=TelegramSignalFormatter(),
        enabled=enabled,
    ), telegram_client


class TestNotify:
    async def test_disabled_notifier_skips(self):
        notifier, telegram_client = _build_notifier(enabled=False)
        results = await notifier.notify(_signal())
        assert len(results) == 1
        assert results[0].status == NotificationStatus.SKIPPED
        telegram_client.send_message.assert_not_called()

    async def test_confirmed_signal_sends(self):
        notifier, telegram_client = _build_notifier()
        results = await notifier.notify(_signal(status=SignalStatus.CONFIRMED))
        assert results[0].status == NotificationStatus.SENT
        telegram_client.send_message.assert_awaited_once()

    async def test_rejected_signal_rejected(self):
        notifier, telegram_client = _build_notifier()
        results = await notifier.notify(_signal(status=SignalStatus.REJECTED))
        assert len(results) == 1
        assert results[0].status == NotificationStatus.SKIPPED
        telegram_client.send_message.assert_not_called()

    async def test_exactly_one_send_call_regardless_of_chat_count(self):
        # The notifier calls send_message exactly once per signal; fanning
        # out to multiple chats is TelegramBotClient's responsibility.
        notifier, telegram_client = _build_notifier(send_result=_sent_results(count=3))
        await notifier.notify(_signal())
        assert telegram_client.send_message.await_count == 1

    async def test_returns_one_result_per_configured_chat(self):
        notifier, _ = _build_notifier(send_result=_sent_results(count=3))
        results = await notifier.notify(_signal())
        assert len(results) == 3

    async def test_input_signal_not_mutated(self):
        notifier, _ = _build_notifier()
        signal = _signal()
        original_trade_id = signal.trade_id
        await notifier.notify(signal)
        assert signal.trade_id == original_trade_id

    async def test_every_result_carries_trade_id(self):
        notifier, _ = _build_notifier(send_result=_sent_results(count=2))
        signal = _signal()
        results = await notifier.notify(signal)
        assert all(result.trade_id == signal.trade_id for result in results)


class TestSendTestMessage:
    async def test_test_message_only_on_explicit_call(self):
        notifier, telegram_client = _build_notifier()
        # Constructing the notifier and calling notify() must never trigger
        # a test message; only an explicit send_test_message() call does.
        await notifier.notify(_signal())
        telegram_client.send_message.assert_called_once()
        call_args = telegram_client.send_message.call_args
        assert "test successful" not in call_args.args[0].lower()

        await notifier.send_test_message()
        assert telegram_client.send_message.await_count == 2
