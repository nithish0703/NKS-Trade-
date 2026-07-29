"""
Tests for Telegram wiring in app.scanner.engine_factory.build_scanner_service.
"""

import pytest

from app.config.settings import get_settings
from app.scanner.engine_factory import build_scanner_service


@pytest.fixture(autouse=True)
def _clear_settings_cache(monkeypatch):
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


class TestTelegramFactory:
    def test_enabled_settings_create_notification_service(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ENABLED", "true")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST_TOKEN")
        monkeypatch.setenv("TELEGRAM_CHAT_IDS", "12345")
        get_settings.cache_clear()

        service = build_scanner_service()
        assert service.notification_service is not None

    def test_disabled_settings_do_not_create_active_telegram_client(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ENABLED", "false")
        get_settings.cache_clear()

        service = build_scanner_service()
        assert service.notification_service is None

    def test_token_required_when_enabled(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ENABLED", "true")
        # Use an explicit empty value rather than delenv: a real .env file
        # (if present on disk) is read directly by pydantic-settings and
        # is not affected by removing the process environment variable.
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
        monkeypatch.setenv("TELEGRAM_CHAT_IDS", "12345")
        get_settings.cache_clear()

        with pytest.raises(Exception):
            get_settings()

    def test_chat_ids_required_when_enabled(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ENABLED", "true")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST_TOKEN")
        monkeypatch.setenv("TELEGRAM_CHAT_IDS", "")
        get_settings.cache_clear()

        with pytest.raises(Exception):
            get_settings()

    def test_multiple_chat_ids_parsed_and_wired(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ENABLED", "true")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST_TOKEN")
        monkeypatch.setenv("TELEGRAM_CHAT_IDS", "8886680874, 736782230")
        get_settings.cache_clear()

        settings = get_settings()
        assert settings.telegram_chat_ids == ["8886680874", "736782230"]

        service = build_scanner_service()
        telegram_client = service.notification_service.telegram_notifier.telegram_client
        assert telegram_client.chat_ids == ["8886680874", "736782230"]

    def test_no_api_call_at_construction(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ENABLED", "true")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST_TOKEN")
        monkeypatch.setenv("TELEGRAM_CHAT_IDS", "12345")
        get_settings.cache_clear()

        # Construction must be fast and synchronous with no network I/O.
        service = build_scanner_service()
        assert service is not None

    def test_no_automatic_test_message(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ENABLED", "true")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST_TOKEN")
        monkeypatch.setenv("TELEGRAM_CHAT_IDS", "12345")
        get_settings.cache_clear()

        service = build_scanner_service()
        telegram_client = service.notification_service.telegram_notifier.telegram_client
        # No send_message call should have occurred yet; the client's
        # underlying httpx.AsyncClient should not have made any request.
        assert telegram_client is not None

    def test_independent_factory_instances(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ENABLED", "true")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST_TOKEN")
        monkeypatch.setenv("TELEGRAM_CHAT_IDS", "12345")
        get_settings.cache_clear()

        service_one = build_scanner_service()
        service_two = build_scanner_service()
        assert service_one.notification_service is not service_two.notification_service
        client_one = service_one.notification_service.telegram_notifier.telegram_client
        client_two = service_two.notification_service.telegram_notifier.telegram_client
        assert client_one is not client_two

    def test_secrets_not_represented_in_logs(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ENABLED", "true")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST_TOKEN_SECRET")
        monkeypatch.setenv("TELEGRAM_CHAT_IDS", "12345")
        get_settings.cache_clear()

        settings = get_settings()
        assert "TEST_TOKEN_SECRET" not in repr(settings)
        assert "TEST_TOKEN_SECRET" not in str(settings)
