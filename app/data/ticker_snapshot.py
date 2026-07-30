"""
Data model representing a single symbol's bulk ticker snapshot, used for
dynamic Liquidity + Open Interest based coin discovery.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator


class TickerSnapshot(BaseModel):
    """
    Immutable snapshot of a single symbol's Open Interest and 24h
    turnover, as returned by an exchange's bulk public ticker endpoint.

    This model only represents market data; it does not filter, rank,
    or make any trading decisions.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    open_interest_usdt: Optional[float] = None
    turnover_24h_usdt: Optional[float] = None

    @field_validator("open_interest_usdt", "turnover_24h_usdt")
    @classmethod
    def _values_cannot_be_negative(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and value < 0:
            raise ValueError("open_interest_usdt and turnover_24h_usdt cannot be negative.")
        return value
