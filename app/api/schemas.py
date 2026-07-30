"""
Pydantic response models for the dashboard REST API and WebSocket feed.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class ComparisonPercentages(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_signals_percentage: Optional[float] = None
    wins_percentage: Optional[float] = None
    losses_percentage: Optional[float] = None
    win_rate_percentage: Optional[float] = None
    average_rr_percentage: Optional[float] = None


class DashboardSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_signals: int
    wins: int
    losses: int
    open_signals: int
    win_rate: Optional[float] = None
    average_rr: Optional[float] = None
    premium_count: int
    strong_count: int
    scanner_running: bool
    last_scan_time_utc: Optional[datetime] = None
    server_time_utc: datetime
    comparison: ComparisonPercentages


class ScanningCoin(BaseModel):
    model_config = ConfigDict(frozen=True)

    coin: str
    direction: Optional[str] = None
    score: Optional[float] = None
    status: str
    failed_layer: Optional[str] = None
    reason: Optional[str] = None
    updated_at_utc: Optional[datetime] = None

    # Dashboard-only scan-progress visibility. Never the final confidence
    # score, never used for signal publishing/storage/notification, and
    # never a substitute for ConfidenceScoringEngine's output.
    validation_progress_raw_score: Optional[float] = None
    validation_progress_max_score: Optional[float] = None
    validation_progress_percentage: Optional[float] = None
    last_executed_layer: Optional[str] = None

    # Dashboard-only, non-trading scan PREVIEW. Independently
    # re-evaluated from the same market data, continuing past the point
    # the real fail-fast pipeline stopped. Never a signal, never
    # persisted, never notified, never used for risk/trade decisions,
    # and never a substitute for `direction`/`validation_progress_*`
    # above (which reflect the real, fail-fast pipeline outcome).
    preview_direction: Optional[str] = None
    preview_progress_raw_score: Optional[float] = None
    preview_progress_max_score: Optional[float] = None
    preview_progress_percentage: Optional[float] = None
    preview_completed_layers: Optional[list[str]] = None
    preview_failed_layers: Optional[list[str]] = None
    preview_data_availability: Optional[dict[str, str]] = None

    # Dashboard-only visual cue: raw price direction of the most recent
    # completed entry-timeframe candle vs. the one before it. A pure
    # chart observation -- never HTF-bias-derived, never a substitute
    # for `direction` or `preview_direction`, and never used for any
    # trade or risk decision.
    chart_trend: Optional[str] = None


class ActiveSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_id: str
    coin: str
    direction: str
    current_price: Optional[float] = None
    entry_price: float
    take_profit: float
    stop_loss: float
    distance_to_take_profit_percentage: Optional[float] = None
    confidence_score: float
    signal_type: str
    detection_time_utc: datetime
    # Dashboard-only lifecycle status; always "ACTIVE" for a signal that
    # appears in this list. Never reflects a real exchange position.
    dashboard_status: str = "ACTIVE"


class PremiumSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_id: str
    coin: str
    direction: str
    signal_type: str
    entry_price: float
    take_profit: float
    stop_loss: float
    confidence_score: float
    detection_time_utc: datetime


class StrongSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_id: str
    coin: str
    direction: str
    confidence_score: float
    higher_timeframe_bias: str
    normalized_score: float
    detection_time_utc: datetime


class RejectionItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    coin: str
    failed_layer: str
    reason: str
    detection_time_utc: datetime


class ScannerStatusItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    pair: str
    stage: Optional[str] = None
    status: str
    last_scan_time_utc: Optional[datetime] = None


class DashboardHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    scanner_running: bool
    database_reachable: bool
    telegram_enabled: bool
    websocket_enabled: bool
    server_time_utc: datetime


class SignalDetails(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_id: str
    coin: str
    direction: str
    signal_type: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float
    confidence_score: float
    market_regime: str
    higher_timeframe_bias: str
    liquidity_type: str
    entry_zone_type: str
    structure_confirmation: str
    volume_confirmation: bool
    atr_status: str
    trading_session: str
    btc_market_alignment: bool
    detection_time_utc: datetime
    institutional_reason: str
    # Dashboard-only lifecycle status ("NEW" or "ACTIVE"). Drives whether
    # the modal's "Trade" button is enabled; never a real trading field.
    dashboard_status: str = "NEW"


class ScannerStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    running: bool
    shutdown_requested: bool
    cycles_completed: int
    last_cycle_started_at: Optional[datetime] = None
    last_cycle_completed_at: Optional[datetime] = None
    last_cycle_id: Optional[str] = None
    last_error: Optional[str] = None


class DashboardWebSocketEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event: str
    timestamp_utc: datetime
    data: dict[str, Any]


class ApiError(BaseModel):
    model_config = ConfigDict(frozen=True)

    detail: str
