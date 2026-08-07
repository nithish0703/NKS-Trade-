"""
Immutable typed result models for risk management: stop loss, take
profit, position sizing, correlation, and the combined risk plan.
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator


class StopLossSource(str, Enum):
    """Source of a candidate or selected stop-loss level."""

    ATR = "ATR"
    ENTRY_ZONE = "ENTRY_ZONE"
    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"


class TakeProfitSource(str, Enum):
    """Source of a candidate or selected take-profit target."""

    MAJOR_INSTITUTIONAL_LIQUIDITY = "MAJOR_INSTITUTIONAL_LIQUIDITY"
    STRONG_SWING_HIGH = "STRONG_SWING_HIGH"
    STRONG_SWING_LOW = "STRONG_SWING_LOW"
    UNMITIGATED_LIQUIDITY_POOL = "UNMITIGATED_LIQUIDITY_POOL"
    FAIR_VALUE_GAP_COMPLETION = "FAIR_VALUE_GAP_COMPLETION"


class RiskPlanStatus(str, Enum):
    """Overall status of a calculated risk plan."""

    VALID = "VALID"
    INVALID = "INVALID"
    REJECTED = "REJECTED"


class CorrelationStatus(str, Enum):
    """Outcome of a correlation check against active positions."""

    ACCEPTABLE = "ACCEPTABLE"
    TOO_HIGH = "TOO_HIGH"
    DATA_MISSING = "DATA_MISSING"


class StopLossCandidate(BaseModel):
    """A single candidate stop-loss level generated for audit and selection."""

    model_config = ConfigDict(frozen=True)

    source: StopLossSource
    price: float
    distance_from_entry: float
    valid_for_direction: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None

    @field_validator("price")
    @classmethod
    def _price_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("price must be positive.")
        return value

    @field_validator("distance_from_entry")
    @classmethod
    def _distance_cannot_be_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("distance_from_entry cannot be negative.")
        return value


class StopLossResult(BaseModel):
    """Result of dynamic stop-loss calculation, retaining all candidates for audit."""

    model_config = ConfigDict(frozen=True)

    direction: str
    entry_price: float
    selected_stop_loss: Optional[float] = None
    selected_source: Optional[StopLossSource] = None
    candidates: list[StopLossCandidate]
    risk_distance: Optional[float] = None
    valid: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None

    @field_validator("entry_price")
    @classmethod
    def _entry_price_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("entry_price must be positive.")
        return value


class TakeProfitCandidate(BaseModel):
    """A single candidate take-profit target generated for audit and selection."""

    model_config = ConfigDict(frozen=True)

    source: TakeProfitSource
    target_id: str
    price: float
    distance_from_entry: float
    risk_reward_ratio: float
    probability_rank: int
    valid: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None

    @field_validator("price")
    @classmethod
    def _price_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("price must be positive.")
        return value

    @field_validator("distance_from_entry")
    @classmethod
    def _distance_cannot_be_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("distance_from_entry cannot be negative.")
        return value

    @field_validator("risk_reward_ratio")
    @classmethod
    def _risk_reward_cannot_be_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("risk_reward_ratio cannot be negative.")
        return value


class TakeProfitResult(BaseModel):
    """
    Result of take-profit selection, retaining all candidates for audit.

    `selected_take_profit` remains the full/final target (== `tp2_price`
    when valid) for backward compatibility. `tp1_price` is an earlier,
    fixed-RR partial-exit level for locking in a conservative slice of
    the position; `tp2_price` is the structurally selected, volatility-
    aware final target for the remainder.
    """

    model_config = ConfigDict(frozen=True)

    direction: str
    entry_price: float
    stop_loss: float
    selected_take_profit: Optional[float] = None
    selected_source: Optional[TakeProfitSource] = None
    selected_target_id: Optional[str] = None
    risk_reward_ratio: Optional[float] = None
    required_risk_reward_ratio: Optional[float] = None
    tp1_price: Optional[float] = None
    tp1_risk_reward_ratio: Optional[float] = None
    tp1_exit_percentage: Optional[float] = None
    tp2_price: Optional[float] = None
    tp2_risk_reward_ratio: Optional[float] = None
    tp2_exit_percentage: Optional[float] = None
    candidates: list[TakeProfitCandidate]
    valid: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None

    @field_validator("entry_price", "stop_loss")
    @classmethod
    def _prices_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("entry_price and stop_loss must be positive.")
        return value


class PositionRiskResult(BaseModel):
    """Result of position-sizing calculation from account balance and stop distance."""

    model_config = ConfigDict(frozen=True)

    account_balance: float
    risk_percentage: float
    risk_amount: Optional[float] = None
    entry_price: float
    stop_loss: float
    stop_distance: Optional[float] = None
    position_size: Optional[float] = None
    valid: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None

    @field_validator("entry_price", "stop_loss")
    @classmethod
    def _prices_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("entry_price and stop_loss must be positive.")
        return value


class CorrelationResult(BaseModel):
    """Result of evaluating a candidate symbol's return correlation against active positions."""

    model_config = ConfigDict(frozen=True)

    candidate_symbol: str
    active_symbols: list[str]
    maximum_allowed_correlation: float
    observed_correlations: dict[str, Optional[float]]
    status: CorrelationStatus
    acceptable: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None


class RiskPlan(BaseModel):
    """
    Fully aggregated risk-management plan combining stop loss, take
    profit, position sizing, correlation, and active-trade checks.

    This model does not include confidence score, signal type, trade ID,
    or Telegram/dashboard fields.
    """

    model_config = ConfigDict(frozen=True)

    direction: str
    entry_price: float
    stop_loss_result: StopLossResult
    take_profit_result: TakeProfitResult
    position_risk: PositionRiskResult
    correlation_result: CorrelationResult
    active_trade_count: int
    maximum_active_trades: int
    risk_reward_ratio: Optional[float] = None
    status: RiskPlanStatus
    valid: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None

    @field_validator("entry_price")
    @classmethod
    def _entry_price_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("entry_price must be positive.")
        return value

    @field_validator("active_trade_count")
    @classmethod
    def _active_trade_count_cannot_be_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("active_trade_count cannot be negative.")
        return value
