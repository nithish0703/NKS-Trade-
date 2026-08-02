"""
Tests for the (removed) warm-up-tracker wiring in
app.scanner.engine_factory.build_scanner_service.

Dynamic pair discovery no longer gates newly discovered symbols behind a
warm-up fetch: PairScanner.scan_pair() -> InstitutionalSMCStrategyEngine
.analyze_symbol() already independently fetches its own candle/market
data for every symbol on every scan cycle regardless of warm-up state,
so the warm-up pre-fetch only added delay (missed entries for brand-new
symbols) without adding safety. PairWarmUpTracker itself is untouched
and still covered directly in tests/test_pair_discovery.py; it is just
no longer wired into production discovery.
"""

import pytest

from app.config.settings import get_settings
from app.scanner.engine_factory import build_scanner_service
from app.scanner.pair_discovery import DynamicPairDiscoveryService


@pytest.fixture(autouse=True)
def _clear_settings_cache(monkeypatch):
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


class TestPairWarmUpWiring:
    def test_no_pair_discovery_service_when_dynamic_discovery_disabled(self, monkeypatch):
        monkeypatch.setenv("DYNAMIC_PAIR_DISCOVERY_ENABLED", "false")
        get_settings.cache_clear()

        service = build_scanner_service()
        assert service.pair_discovery_service is None

    def test_pair_discovery_service_wired_without_a_warm_up_tracker_when_enabled(self, monkeypatch):
        monkeypatch.setenv("DYNAMIC_PAIR_DISCOVERY_ENABLED", "true")
        get_settings.cache_clear()

        service = build_scanner_service()
        discovery_service = service.pair_discovery_service
        assert isinstance(discovery_service, DynamicPairDiscoveryService)
        assert discovery_service._warm_up_tracker is None

    def test_live_scan_provider_keeps_the_full_allow_list_check(self, monkeypatch):
        from app.config.pairs import validate_pair_symbol

        monkeypatch.setenv("DYNAMIC_PAIR_DISCOVERY_ENABLED", "true")
        get_settings.cache_clear()

        service = build_scanner_service()
        live_provider = service._scheduler._pair_scanner._strategy_engine._market_data_provider

        assert live_provider._validate_symbol is validate_pair_symbol

    def test_live_scan_provider_retry_schedule_unaffected(self, monkeypatch):
        # The strategy engine's own market-data provider (used for
        # symbols already in rotation) must keep using the unmodified,
        # shared defaults regardless of dynamic discovery being enabled.
        from app.data.binance_market_data_provider import (
            MAX_REQUEST_ATTEMPTS,
            RETRY_BACKOFF_SCHEDULE_SECONDS,
        )

        monkeypatch.setenv("DYNAMIC_PAIR_DISCOVERY_ENABLED", "true")
        get_settings.cache_clear()

        service = build_scanner_service()
        live_provider = service._scheduler._pair_scanner._strategy_engine._market_data_provider

        assert live_provider._max_request_attempts == MAX_REQUEST_ATTEMPTS
        assert live_provider._retry_backoff_schedule_seconds == RETRY_BACKOFF_SCHEDULE_SECONDS

    def test_discovery_provider_itself_uses_unmodified_defaults(self, monkeypatch):
        # The bulk tickers discovery-refresh provider is unrelated to the
        # (now-removed) warm-up path and remains untouched.
        from app.data.binance_market_data_provider import (
            MAX_REQUEST_ATTEMPTS,
            RETRY_BACKOFF_SCHEDULE_SECONDS,
        )

        monkeypatch.setenv("DYNAMIC_PAIR_DISCOVERY_ENABLED", "true")
        get_settings.cache_clear()

        service = build_scanner_service()
        discovery_provider = service.pair_discovery_service._market_data_provider

        assert discovery_provider._max_request_attempts == MAX_REQUEST_ATTEMPTS
        assert discovery_provider._retry_backoff_schedule_seconds == RETRY_BACKOFF_SCHEDULE_SECONDS
