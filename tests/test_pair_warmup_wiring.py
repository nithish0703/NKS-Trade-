"""
Tests for the warm-up-provider wiring in
app.scanner.engine_factory.build_scanner_service, exercised only in the
branch where dynamic pair discovery is enabled.
"""

import pytest

from app.config import thresholds
from app.config.settings import get_settings
from app.scanner.engine_factory import build_scanner_service
from app.scanner.pair_discovery import DynamicPairDiscoveryService, PairWarmUpTracker


@pytest.fixture(autouse=True)
def _clear_settings_cache(monkeypatch):
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


class TestPairWarmUpWiring:
    def test_no_warm_up_tracker_when_dynamic_discovery_disabled(self, monkeypatch):
        monkeypatch.setenv("DYNAMIC_PAIR_DISCOVERY_ENABLED", "false")
        get_settings.cache_clear()

        service = build_scanner_service()
        assert service.pair_discovery_service is None

    def test_pair_discovery_service_wired_with_a_warm_up_tracker_when_enabled(self, monkeypatch):
        monkeypatch.setenv("DYNAMIC_PAIR_DISCOVERY_ENABLED", "true")
        get_settings.cache_clear()

        service = build_scanner_service()
        discovery_service = service.pair_discovery_service
        assert isinstance(discovery_service, DynamicPairDiscoveryService)
        assert isinstance(discovery_service._warm_up_tracker, PairWarmUpTracker)

    def test_warm_up_provider_uses_more_patient_retry_settings(self, monkeypatch):
        monkeypatch.setenv("DYNAMIC_PAIR_DISCOVERY_ENABLED", "true")
        get_settings.cache_clear()

        service = build_scanner_service()
        warmup_provider = service.pair_discovery_service._warm_up_tracker._market_data_provider

        assert warmup_provider._max_request_attempts == thresholds.PAIR_WARMUP_MAX_REQUEST_ATTEMPTS
        assert (
            warmup_provider._retry_backoff_schedule_seconds
            == thresholds.PAIR_WARMUP_RETRY_BACKOFF_SCHEDULE_SECONDS
        )

    def test_warm_up_provider_bypasses_the_configured_pair_allow_list(self, monkeypatch):
        # Regression test: a warm-up fetch's whole purpose is to test a
        # symbol *before* it is added to get_configured_pairs(), so the
        # warm-up provider must use format-only validation. Using the
        # default allow-list validator here would make every warm-up
        # fetch fail immediately with "Unsupported trading pair", since
        # the symbol being warmed up is by definition not yet configured.
        from app.config.pairs import validate_pair_symbol, validate_pair_symbol_format

        monkeypatch.setenv("DYNAMIC_PAIR_DISCOVERY_ENABLED", "true")
        get_settings.cache_clear()

        service = build_scanner_service()
        warmup_provider = service.pair_discovery_service._warm_up_tracker._market_data_provider

        assert warmup_provider._validate_symbol is validate_pair_symbol_format
        assert warmup_provider._validate_symbol is not validate_pair_symbol

    def test_live_scan_provider_keeps_the_full_allow_list_check(self, monkeypatch):
        from app.config.pairs import validate_pair_symbol

        monkeypatch.setenv("DYNAMIC_PAIR_DISCOVERY_ENABLED", "true")
        get_settings.cache_clear()

        service = build_scanner_service()
        live_provider = service._scheduler._pair_scanner._strategy_engine._market_data_provider

        assert live_provider._validate_symbol is validate_pair_symbol

    def test_warm_up_retry_settings_are_more_patient_than_the_live_scan_defaults(self):
        # A structural guarantee that the constants themselves encode a
        # more patient schedule, independent of any wiring.
        from app.data.bybit_market_data_provider import (
            MAX_REQUEST_ATTEMPTS,
            RETRY_BACKOFF_SCHEDULE_SECONDS,
        )

        assert thresholds.PAIR_WARMUP_MAX_REQUEST_ATTEMPTS > MAX_REQUEST_ATTEMPTS
        assert sum(thresholds.PAIR_WARMUP_RETRY_BACKOFF_SCHEDULE_SECONDS) > sum(
            RETRY_BACKOFF_SCHEDULE_SECONDS
        )

    def test_live_scan_provider_retry_schedule_unaffected(self, monkeypatch):
        # The strategy engine's own market-data provider (used for
        # symbols already in rotation) must keep using the unmodified,
        # shared defaults regardless of dynamic discovery being enabled.
        from app.data.bybit_market_data_provider import (
            MAX_REQUEST_ATTEMPTS,
            RETRY_BACKOFF_SCHEDULE_SECONDS,
        )

        monkeypatch.setenv("DYNAMIC_PAIR_DISCOVERY_ENABLED", "true")
        get_settings.cache_clear()

        service = build_scanner_service()
        live_provider = service._scheduler._pair_scanner._strategy_engine._market_data_provider

        assert live_provider._max_request_attempts == MAX_REQUEST_ATTEMPTS
        assert live_provider._retry_backoff_schedule_seconds == RETRY_BACKOFF_SCHEDULE_SECONDS

    def test_discovery_provider_itself_also_uses_unmodified_defaults(self, monkeypatch):
        # Only the warm-up provider gets the patient schedule; the bulk
        # tickers discovery-refresh provider is unrelated and untouched.
        from app.data.bybit_market_data_provider import (
            MAX_REQUEST_ATTEMPTS,
            RETRY_BACKOFF_SCHEDULE_SECONDS,
        )

        monkeypatch.setenv("DYNAMIC_PAIR_DISCOVERY_ENABLED", "true")
        get_settings.cache_clear()

        service = build_scanner_service()
        discovery_provider = service.pair_discovery_service._market_data_provider

        assert discovery_provider._max_request_attempts == MAX_REQUEST_ATTEMPTS
        assert discovery_provider._retry_backoff_schedule_seconds == RETRY_BACKOFF_SCHEDULE_SECONDS

    def test_warm_up_provider_and_discovery_provider_are_distinct_instances(self, monkeypatch):
        monkeypatch.setenv("DYNAMIC_PAIR_DISCOVERY_ENABLED", "true")
        get_settings.cache_clear()

        service = build_scanner_service()
        discovery_service = service.pair_discovery_service
        assert (
            discovery_service._warm_up_tracker._market_data_provider
            is not discovery_service._market_data_provider
        )
