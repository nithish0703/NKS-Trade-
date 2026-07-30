"""
Tests for the dashboard FastAPI routes, using dependency overrides so no
real database, scanner, or Bybit network calls occur.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api import dependencies
from app.api.main import create_app
from app.config.settings import get_settings


@pytest.fixture(autouse=True)
def _disable_dashboard_scanner(monkeypatch):
    # The dashboard API's lifespan starts a real background scanner (with
    # real Bybit network calls) whenever DASHBOARD_API_ENABLED is true.
    # Unit tests must never make real network calls, so force it off
    # here, regardless of the developer's local .env.
    monkeypatch.setenv("DASHBOARD_API_ENABLED", "false")
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()
from app.api.schemas import (
    ActiveSignal,
    ComparisonPercentages,
    DashboardHealth,
    DashboardSummary,
    PremiumSignal,
    RejectionItem,
    ScanningCoin,
    SignalDetails,
    StrongSignal,
)

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _summary(**overrides) -> DashboardSummary:
    fields = dict(
        total_signals=0,
        wins=0,
        losses=0,
        open_signals=0,
        win_rate=None,
        average_rr=None,
        premium_count=0,
        strong_count=0,
        scanner_running=False,
        last_scan_time_utc=None,
        server_time_utc=UTC_NOW,
        comparison=ComparisonPercentages(),
    )
    fields.update(overrides)
    return DashboardSummary(**fields)


@pytest.fixture
def app_with_mocks():
    app = create_app()
    dashboard_service = MagicMock()
    dashboard_service.get_summary = AsyncMock(return_value=_summary())
    dashboard_service.get_scanning_coins = AsyncMock(return_value=[])
    dashboard_service.get_active_signals = AsyncMock(return_value=[])
    dashboard_service.get_premium_signals = AsyncMock(return_value=[])
    dashboard_service.get_strong_signals = AsyncMock(return_value=[])
    dashboard_service.get_recent_rejections = AsyncMock(return_value=[])
    dashboard_service.get_health = AsyncMock(
        return_value=DashboardHealth(
            scanner_running=False,
            database_reachable=True,
            telegram_enabled=False,
            websocket_enabled=False,
            server_time_utc=UTC_NOW,
        )
    )
    dashboard_service.get_signal_details = AsyncMock(return_value=None)
    dashboard_service.activate_signal = AsyncMock(return_value=None)

    signal_repository = MagicMock()
    signal_repository.list_recent = AsyncMock(return_value=[])

    app.dependency_overrides[dependencies.get_dashboard_service] = lambda: dashboard_service
    app.dependency_overrides[dependencies.get_signal_repository] = lambda: signal_repository
    app.dependency_overrides[dependencies.get_scanner_service] = lambda: None

    return app, dashboard_service, signal_repository


class TestHealthEndpoint:
    def test_liveness_endpoint(self):
        app = create_app()
        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestDashboardSummaryEndpoint:
    def test_summary_returns_service_data(self, app_with_mocks):
        app, dashboard_service, _ = app_with_mocks
        dashboard_service.get_summary = AsyncMock(
            return_value=_summary(total_signals=10, premium_count=3)
        )
        with TestClient(app) as client:
            response = client.get("/api/dashboard/summary")
        assert response.status_code == 200
        body = response.json()
        assert body["total_signals"] == 10
        assert body["premium_count"] == 3

    def test_null_win_rate_serialized_as_null(self, app_with_mocks):
        app, dashboard_service, _ = app_with_mocks
        dashboard_service.get_summary = AsyncMock(return_value=_summary(win_rate=None))
        with TestClient(app) as client:
            response = client.get("/api/dashboard/summary")
        assert response.json()["win_rate"] is None


class TestScanningCoinsEndpoint:
    def test_scanning_coins_returns_service_data(self, app_with_mocks):
        app, dashboard_service, _ = app_with_mocks
        dashboard_service.get_scanning_coins = AsyncMock(
            return_value=[
                ScanningCoin(coin="BTC-USDT", direction="BUY", score=95.0, status="READY")
            ]
        )
        with TestClient(app) as client:
            response = client.get("/api/dashboard/scanning-coins")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["coin"] == "BTC-USDT"


class TestActiveSignalsEndpoint:
    def test_active_signals_returns_service_data(self, app_with_mocks):
        app, dashboard_service, _ = app_with_mocks
        dashboard_service.get_active_signals = AsyncMock(
            return_value=[
                ActiveSignal(
                    trade_id="SMC-1",
                    coin="BTC-USDT",
                    direction="BUY",
                    current_price=105.0,
                    entry_price=100.0,
                    take_profit=110.0,
                    stop_loss=95.0,
                    distance_to_take_profit_percentage=4.76,
                    confidence_score=95.0,
                    signal_type="PREMIUM",
                    detection_time_utc=UTC_NOW,
                )
            ]
        )
        with TestClient(app) as client:
            response = client.get("/api/dashboard/active-signals")
        assert response.status_code == 200
        assert response.json()[0]["trade_id"] == "SMC-1"


class TestActivateSignalEndpoint:
    def test_activate_signal_returns_active_signal(self, app_with_mocks):
        app, dashboard_service, _ = app_with_mocks
        dashboard_service.activate_signal = AsyncMock(
            return_value=ActiveSignal(
                trade_id="SMC-1",
                coin="BTC-USDT",
                direction="BUY",
                current_price=None,
                entry_price=100.0,
                take_profit=110.0,
                stop_loss=95.0,
                distance_to_take_profit_percentage=None,
                confidence_score=95.0,
                signal_type="PREMIUM",
                detection_time_utc=UTC_NOW,
                dashboard_status="ACTIVE",
            )
        )
        with TestClient(app) as client:
            response = client.post("/api/dashboard/signals/SMC-1/activate")
        assert response.status_code == 200
        assert response.json()["trade_id"] == "SMC-1"
        assert response.json()["dashboard_status"] == "ACTIVE"
        dashboard_service.activate_signal.assert_awaited_once_with("SMC-1")

    def test_activate_signal_not_found_returns_404(self, app_with_mocks):
        app, dashboard_service, _ = app_with_mocks
        dashboard_service.activate_signal = AsyncMock(return_value=None)
        with TestClient(app) as client:
            response = client.post("/api/dashboard/signals/does-not-exist/activate")
        assert response.status_code == 404

    def test_activate_signal_route_is_post_only(self, app_with_mocks):
        app, _, _ = app_with_mocks
        with TestClient(app) as client:
            response = client.get("/api/dashboard/signals/SMC-1/activate")
        assert response.status_code == 405


class TestPremiumStrongEndpoints:
    def test_premium_signals_endpoint(self, app_with_mocks):
        app, dashboard_service, _ = app_with_mocks
        dashboard_service.get_premium_signals = AsyncMock(
            return_value=[
                PremiumSignal(
                    trade_id="SMC-1",
                    coin="BTC-USDT",
                    direction="BUY",
                    signal_type="PREMIUM",
                    entry_price=100.0,
                    take_profit=110.0,
                    stop_loss=95.0,
                    confidence_score=95.0,
                    detection_time_utc=UTC_NOW,
                )
            ]
        )
        with TestClient(app) as client:
            response = client.get("/api/dashboard/premium-signals")
        assert response.status_code == 200
        assert response.json()[0]["signal_type"] == "PREMIUM"

    def test_strong_signals_endpoint(self, app_with_mocks):
        app, dashboard_service, _ = app_with_mocks
        dashboard_service.get_strong_signals = AsyncMock(
            return_value=[
                StrongSignal(
                    trade_id="SMC-2",
                    coin="ETH-USDT",
                    direction="SELL",
                    confidence_score=85.0,
                    higher_timeframe_bias="BEARISH",
                    normalized_score=85.0,
                    detection_time_utc=UTC_NOW,
                )
            ]
        )
        with TestClient(app) as client:
            response = client.get("/api/dashboard/strong-signals")
        assert response.status_code == 200
        assert response.json()[0]["coin"] == "ETH-USDT"

    def test_medium_signals_never_exposed(self, app_with_mocks):
        # There is no dashboard endpoint that can return MEDIUM signals at
        # all; confirm neither premium nor strong routes ever surface one.
        app, dashboard_service, _ = app_with_mocks
        with TestClient(app) as client:
            premium_response = client.get("/api/dashboard/premium-signals")
            strong_response = client.get("/api/dashboard/strong-signals")
        for body in (premium_response.json(), strong_response.json()):
            assert all(item.get("signal_type") != "MEDIUM" for item in body)


class TestRejectionsEndpoint:
    def test_recent_rejections_endpoint(self, app_with_mocks):
        app, dashboard_service, _ = app_with_mocks
        dashboard_service.get_recent_rejections = AsyncMock(
            return_value=[
                RejectionItem(
                    coin="BTC-USDT",
                    failed_layer="MARKET_REGIME",
                    reason="not trending",
                    detection_time_utc=UTC_NOW,
                )
            ]
        )
        with TestClient(app) as client:
            response = client.get("/api/dashboard/recent-rejections")
        assert response.status_code == 200
        assert response.json()[0]["failed_layer"] == "MARKET_REGIME"

    def test_rejections_do_not_include_candle_arrays(self, app_with_mocks):
        app, dashboard_service, _ = app_with_mocks
        dashboard_service.get_recent_rejections = AsyncMock(
            return_value=[
                RejectionItem(
                    coin="BTC-USDT",
                    failed_layer="MARKET_REGIME",
                    reason="not trending",
                    detection_time_utc=UTC_NOW,
                )
            ]
        )
        with TestClient(app) as client:
            response = client.get("/api/dashboard/recent-rejections")
        body_text = response.text.lower()
        assert "candle" not in body_text
        assert "open" not in body_text and "close" not in body_text


class TestSignalDetailsEndpoint:
    def test_signal_details_found(self, app_with_mocks):
        app, dashboard_service, _ = app_with_mocks
        dashboard_service.get_signal_details = AsyncMock(
            return_value=SignalDetails(
                trade_id="SMC-1",
                coin="BTC-USDT",
                direction="BUY",
                signal_type="PREMIUM",
                entry_price=100.0,
                stop_loss=95.0,
                take_profit=110.0,
                risk_reward_ratio=3.0,
                confidence_score=95.0,
                market_regime="TRENDING",
                higher_timeframe_bias="BULLISH",
                liquidity_type="EQUAL_HIGH",
                entry_zone_type="ORDER_BLOCK",
                structure_confirmation="BOS",
                volume_confirmation=True,
                atr_status="EXPANDING",
                trading_session="LONDON",
                btc_market_alignment=True,
                detection_time_utc=UTC_NOW,
                institutional_reason="Confirmed setup facts only.",
            )
        )
        with TestClient(app) as client:
            response = client.get("/api/signals/SMC-1")
        assert response.status_code == 200
        assert response.json()["trade_id"] == "SMC-1"

    def test_signal_details_not_found_returns_404(self, app_with_mocks):
        app, dashboard_service, _ = app_with_mocks
        dashboard_service.get_signal_details = AsyncMock(return_value=None)
        with TestClient(app) as client:
            response = client.get("/api/signals/does-not-exist")
        assert response.status_code == 404

    def test_signal_details_no_tp2_tp3_fields(self, app_with_mocks):
        app, dashboard_service, _ = app_with_mocks
        dashboard_service.get_signal_details = AsyncMock(
            return_value=SignalDetails(
                trade_id="SMC-1",
                coin="BTC-USDT",
                direction="BUY",
                signal_type="PREMIUM",
                entry_price=100.0,
                stop_loss=95.0,
                take_profit=110.0,
                risk_reward_ratio=3.0,
                confidence_score=95.0,
                market_regime="TRENDING",
                higher_timeframe_bias="BULLISH",
                liquidity_type="EQUAL_HIGH",
                entry_zone_type="ORDER_BLOCK",
                structure_confirmation="BOS",
                volume_confirmation=True,
                atr_status="EXPANDING",
                trading_session="LONDON",
                btc_market_alignment=True,
                detection_time_utc=UTC_NOW,
                institutional_reason="Confirmed setup facts only.",
            )
        )
        with TestClient(app) as client:
            response = client.get("/api/signals/SMC-1")
        body = response.json()
        assert "take_profit_2" not in body
        assert "take_profit_3" not in body
        assert "trailing_stop" not in body


class TestScannerStatusEndpoint:
    def test_scanner_status_waiting_when_no_scanner(self, app_with_mocks):
        app, _, _ = app_with_mocks
        with TestClient(app) as client:
            response = client.get("/api/scanner/status")
        assert response.status_code == 200
        body = response.json()
        assert body["running"] is False
        assert body["last_error"] == "Waiting for scanner"

    def test_scanner_status_reports_real_runtime(self, app_with_mocks):
        app, _, _ = app_with_mocks
        scanner_service = MagicMock()
        scanner_service.get_runtime_status = MagicMock(
            return_value=MagicMock(
                running=True,
                shutdown_requested=False,
                cycles_completed=5,
                last_cycle_started_at=UTC_NOW,
                last_cycle_completed_at=UTC_NOW,
                last_cycle_id="cycle-5",
                last_error=None,
            )
        )
        app.dependency_overrides[dependencies.get_scanner_service] = lambda: scanner_service
        with TestClient(app) as client:
            response = client.get("/api/scanner/status")
        body = response.json()
        assert body["running"] is True
        assert body["cycles_completed"] == 5


class TestSignalsListEndpoint:
    def test_list_signals_endpoint(self, app_with_mocks):
        from app.models.signal import Direction, MarketRegime, Signal, SignalType

        app, _, signal_repository = app_with_mocks
        signal_repository.list_recent = AsyncMock(
            return_value=[
                Signal(
                    trade_id="SMC-1",
                    coin="BTC-USDT",
                    direction=Direction.BUY,
                    entry_price=100.0,
                    stop_loss=95.0,
                    take_profit=110.0,
                    risk_reward_ratio=3.0,
                    confidence_score=95.0,
                    signal_type=SignalType.PREMIUM,
                    market_regime=MarketRegime.TRENDING,
                    higher_timeframe_bias="BULLISH",
                    liquidity_type="EQUAL_HIGH",
                    entry_zone_type="ORDER_BLOCK",
                    structure_confirmation="BOS",
                    volume_confirmation=True,
                    atr_status="EXPANDING",
                    trading_session="LONDON",
                    btc_market_alignment=True,
                    detection_time_utc=UTC_NOW,
                    institutional_reason="reason",
                    setup_key="setup-1",
                    liquidity_sweep_id="sweep-1",
                    structure_break_id="break-1",
                    entry_zone_id="zone-1",
                    retest_id="retest-1",
                    created_at_utc=UTC_NOW,
                )
            ]
        )
        with TestClient(app) as client:
            response = client.get("/api/signals")
        assert response.status_code == 200
        assert response.json()[0]["trade_id"] == "SMC-1"

    def test_list_signals_rejects_non_positive_limit(self, app_with_mocks):
        app, _, _ = app_with_mocks
        with TestClient(app) as client:
            response = client.get("/api/signals?limit=0")
        assert response.status_code == 422


class TestNoSecretsInPayloads:
    def test_no_secrets_in_health_payload(self, app_with_mocks):
        app, _, _ = app_with_mocks
        with TestClient(app) as client:
            response = client.get("/api/dashboard/health")
        body_text = response.text.lower()
        assert "token" not in body_text
        assert "chat_id" not in body_text
        assert "api_key" not in body_text

    def test_no_secrets_in_summary_payload(self, app_with_mocks):
        app, _, _ = app_with_mocks
        with TestClient(app) as client:
            response = client.get("/api/dashboard/summary")
        body_text = response.text.lower()
        assert "token" not in body_text
        assert "secret" not in body_text


class TestNoLiveTradeEndpoint:
    def test_no_order_or_trade_execution_route_exists(self):
        app = create_app()
        paths = set()
        for route in app.routes:
            path = getattr(route, "path", None)
            if path:
                paths.add(path)
            routes_attr = getattr(route, "routes", None)
            if routes_attr:
                for sub_route in routes_attr:
                    sub_path = getattr(sub_route, "path", None)
                    if sub_path:
                        paths.add(sub_path)

        forbidden_fragments = ("order", "trade/execute", "buy", "sell", "position")
        for path in paths:
            lowered = path.lower()
            assert not any(fragment in lowered for fragment in forbidden_fragments), path
