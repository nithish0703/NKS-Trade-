"""
Active-trading-state provider abstraction for the multi-pair scanner.
"""

from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.candle import Candle


class ActiveTradingState(BaseModel):
    """A snapshot of currently active trades, captured at a point in time."""

    model_config = ConfigDict(frozen=True)

    active_trade_count: int
    active_positions: list[Any]
    active_position_candles: dict[str, list[Candle]]
    captured_at_utc: datetime
    metadata: Optional[dict[str, Any]] = None

    @field_validator("active_trade_count")
    @classmethod
    def _active_trade_count_cannot_be_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("active_trade_count cannot be negative.")
        return value

    @field_validator("captured_at_utc")
    @classmethod
    def _captured_at_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("captured_at_utc must be timezone-aware UTC.")
        return value


@runtime_checkable
class ActiveTradingStateProvider(Protocol):
    """Supplies a fresh snapshot of currently active trading state."""

    async def get_state(self) -> ActiveTradingState:
        ...


class EmptyActiveTradingStateProvider:
    """
    Development implementation that always reports zero active trades.

    Does not connect to an exchange account and does not simulate positions.
    """

    async def get_state(self) -> ActiveTradingState:
        return ActiveTradingState(
            active_trade_count=0,
            active_positions=[],
            active_position_candles={},
            captured_at_utc=datetime.now(timezone.utc),
        )
