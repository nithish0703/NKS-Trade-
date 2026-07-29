"""
Tests for the `telegram-test` CLI mode in main.py.
"""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main
from app.notifications.results import NotificationStatus, TelegramNotificationResult

pytestmark = pytest.mark.asyncio


def _settings(enabled=True, bot_token="TEST_TOKEN", chat_ids=None):
    settings = MagicMock()
    settings.telegram_enabled = enabled
    settings.telegram_bot_token = bot_token
    settings.telegram_chat_ids = chat_ids if chat_ids is not None else ["12345"]
    settings.telegram_api_base_url = "https://api.telegram.org"
    settings.telegram_request_timeout_seconds = 10.0
    settings.telegram_max_retries = 2
    settings.telegram_retry_delay_seconds = 1.0
    settings.telegram_disable_web_page_preview = True
    return settings


def _sent_result(message_id=1, chat_id_suffix="...2345"):
    return TelegramNotificationResult(
        status=NotificationStatus.SENT,
        telegram_message_id=message_id,
        chat_id_suffix=chat_id_suffix,
        sent_at_utc=datetime.datetime.now(datetime.timezone.utc),
        attempt_count=1,
    )


def _failed_result(reason="boom", chat_id_suffix="...2345"):
    return TelegramNotificationResult(
        status=NotificationStatus.FAILED,
        reason=reason,
        chat_id_suffix=chat_id_suffix,
        attempt_count=1,
    )


class TestTelegramTestMode:
    async def test_sends_one_test_message_per_chat(self, capsys):
        settings = _settings()
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_notifier = MagicMock()
        mock_notifier.send_test_message = AsyncMock(return_value=[_sent_result(message_id=555)])

        with patch("app.config.settings.get_settings", return_value=settings), patch(
            "app.notifications.telegram_client.TelegramBotClient", return_value=mock_client
        ), patch("app.notifications.telegram_notifier.TelegramSignalNotifier", return_value=mock_notifier):
            await main._run_telegram_test()

        mock_notifier.send_test_message.assert_awaited_once()
        output = capsys.readouterr().out
        assert "status=SENT" in output
        assert "message_id=555" in output

    async def test_sends_to_multiple_configured_chats(self, capsys):
        settings = _settings(chat_ids=["111", "222"])
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_notifier = MagicMock()
        mock_notifier.send_test_message = AsyncMock(
            return_value=[
                _sent_result(message_id=1, chat_id_suffix="...0111"),
                _sent_result(message_id=2, chat_id_suffix="...0222"),
            ]
        )
        with patch("app.config.settings.get_settings", return_value=settings), patch(
            "app.notifications.telegram_client.TelegramBotClient", return_value=mock_client
        ), patch("app.notifications.telegram_notifier.TelegramSignalNotifier", return_value=mock_notifier):
            await main._run_telegram_test()
        output = capsys.readouterr().out
        assert output.count("status=SENT") == 2
        assert "message_id=1" in output
        assert "message_id=2" in output

    async def test_one_chat_failure_does_not_block_others(self, capsys):
        settings = _settings(chat_ids=["111", "222"])
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_notifier = MagicMock()
        mock_notifier.send_test_message = AsyncMock(
            return_value=[
                _failed_result(reason="chat not found", chat_id_suffix="...0111"),
                _sent_result(message_id=2, chat_id_suffix="...0222"),
            ]
        )
        with patch("app.config.settings.get_settings", return_value=settings), patch(
            "app.notifications.telegram_client.TelegramBotClient", return_value=mock_client
        ), patch("app.notifications.telegram_notifier.TelegramSignalNotifier", return_value=mock_notifier):
            await main._run_telegram_test()
        output = capsys.readouterr().out
        assert "status=FAILED" in output
        assert "status=SENT" in output
        assert "message_id=2" in output

    async def test_disabled_telegram_exits_clearly(self, capsys):
        settings = _settings(enabled=False)
        with patch("app.config.settings.get_settings", return_value=settings):
            await main._run_telegram_test()
        output = capsys.readouterr().out
        assert "status=SKIPPED" in output
        assert "TELEGRAM_ENABLED" in output

    async def test_missing_configuration_exits_clearly(self, capsys):
        settings = _settings(enabled=True, bot_token=None, chat_ids=[])
        with patch("app.config.settings.get_settings", return_value=settings):
            await main._run_telegram_test()
        output = capsys.readouterr().out
        assert "status=SKIPPED" in output
        assert "reason=" in output

    async def test_successful_result_prints_message_id(self, capsys):
        settings = _settings()
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_notifier = MagicMock()
        mock_notifier.send_test_message = AsyncMock(return_value=[_sent_result(message_id=777)])
        with patch("app.config.settings.get_settings", return_value=settings), patch(
            "app.notifications.telegram_client.TelegramBotClient", return_value=mock_client
        ), patch("app.notifications.telegram_notifier.TelegramSignalNotifier", return_value=mock_notifier):
            await main._run_telegram_test()
        output = capsys.readouterr().out
        assert "message_id=777" in output

    async def test_failure_result_prints_safe_reason(self, capsys):
        settings = _settings()
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_notifier = MagicMock()
        mock_notifier.send_test_message = AsyncMock(
            return_value=[_failed_result(reason="Telegram returned HTTP 401: Unauthorized")]
        )
        with patch("app.config.settings.get_settings", return_value=settings), patch(
            "app.notifications.telegram_client.TelegramBotClient", return_value=mock_client
        ), patch("app.notifications.telegram_notifier.TelegramSignalNotifier", return_value=mock_notifier):
            await main._run_telegram_test()
        output = capsys.readouterr().out
        assert "status=FAILED" in output
        assert "reason=" in output

    async def test_token_not_printed(self, capsys):
        settings = _settings(bot_token="SUPERSECRETTOKEN12345")
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_notifier = MagicMock()
        mock_notifier.send_test_message = AsyncMock(return_value=[_sent_result()])
        with patch("app.config.settings.get_settings", return_value=settings), patch(
            "app.notifications.telegram_client.TelegramBotClient", return_value=mock_client
        ), patch("app.notifications.telegram_notifier.TelegramSignalNotifier", return_value=mock_notifier):
            await main._run_telegram_test()
        output = capsys.readouterr().out
        assert "SUPERSECRETTOKEN12345" not in output

    async def test_chat_id_not_printed_in_full(self, capsys):
        settings = _settings(chat_ids=["999888777"])
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_notifier = MagicMock()
        mock_notifier.send_test_message = AsyncMock(
            return_value=[_sent_result(chat_id_suffix="...8777")]
        )
        with patch("app.config.settings.get_settings", return_value=settings), patch(
            "app.notifications.telegram_client.TelegramBotClient", return_value=mock_client
        ), patch("app.notifications.telegram_notifier.TelegramSignalNotifier", return_value=mock_notifier):
            await main._run_telegram_test()
        output = capsys.readouterr().out
        assert "999888777" not in output
        assert "...8777" in output

    async def test_client_closes_cleanly(self):
        settings = _settings()
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_notifier = MagicMock()
        mock_notifier.send_test_message = AsyncMock(return_value=[_sent_result()])
        with patch("app.config.settings.get_settings", return_value=settings), patch(
            "app.notifications.telegram_client.TelegramBotClient", return_value=mock_client
        ), patch("app.notifications.telegram_notifier.TelegramSignalNotifier", return_value=mock_notifier):
            await main._run_telegram_test()
        mock_client.close.assert_awaited_once()

    async def test_client_closes_cleanly_on_failure(self):
        settings = _settings()
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_notifier = MagicMock()
        mock_notifier.send_test_message = AsyncMock(return_value=[_failed_result()])
        with patch("app.config.settings.get_settings", return_value=settings), patch(
            "app.notifications.telegram_client.TelegramBotClient", return_value=mock_client
        ), patch("app.notifications.telegram_notifier.TelegramSignalNotifier", return_value=mock_notifier):
            await main._run_telegram_test()
        mock_client.close.assert_awaited_once()
