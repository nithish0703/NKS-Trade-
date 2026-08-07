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
    confirmed_count: int
    scanner_running: bool
    last_scan_time_utc: Optional[datetime] = None
    server_time_utc: datetime
    comparison: ComparisonPercentages


class ScanningCoin(BaseModel):
    model_config = ConfigDict(frozen=True)

    coin: str
    # Dashboard-only "live price" display: the latest exchange traded
    # price for this symbol, from a single bulk ticker call. Never used
    # by the strategy pipeline, scoring, or risk logic, and independent
    # of scan status -- populated even while a coin is still
    # "SCANNING" (not yet evaluated this cycle). None if the bulk
    # ticker fetch failed or this symbol wasn't in the response.
    price: Optional[float] = None
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

    # Stage 5 (Volume Profile + CVD) confidence tier ("HIGH"/"MEDIUM"/
    # "LOW") and its human-readable reasoning. A soft confidence signal
    # only -- `failed_layer` can never be "ORDER_FLOW" (it is not a
    # gate), and these fields are None until Stage 5 has run (i.e. once
    # HTF Bias, Liquidity Sweep, BOS, and IFVG have all passed).
    order_flow_confidence: Optional[str] = None
    order_flow_reason: Optional[str] = None

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
    # Binary signal status (CONFIRMED/REJECTED). Present only for Premium
    # access tier callers; omitted (None) for Free. No score, percentage,
    # or confidence value is ever included anywhere on this model.
    status: Optional[str] = None
    detection_time_utc: datetime
    # Dashboard-only lifecycle status; always "ACTIVE" for a signal that
    # appears in this list. Never reflects a real exchange position.
    dashboard_status: str = "ACTIVE"


class PremiumSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_id: str
    coin: str
    direction: str
    # Binary signal status (CONFIRMED/REJECTED). Present only for Premium
    # access tier callers; omitted (None) for Free.
    status: Optional[str] = None
    entry_price: float
    take_profit: float
    stop_loss: float
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
    started_at_utc: datetime


class SignalDetails(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_id: str
    coin: str
    direction: str
    # Binary signal status (CONFIRMED/REJECTED). Present only for Premium
    # access tier callers; omitted (None) for Free.
    status: Optional[str] = None
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float
    liquidity_type: str
    entry_zone_type: str
    structure_confirmation: str
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
