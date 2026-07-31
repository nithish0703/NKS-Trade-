"""
Data model representing a single historical Open Interest data point,
used for Open Interest Confirmation (Check 4 of the new strategy).
"""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_validator


class OpenInterestPoint(BaseModel):
    """
    A single point in a symbol's historical Open Interest series, as
    returned by an exchange's public OI-history endpoint.

    This model only represents market data; it does not filter, rank,
    or make any trading decisions.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timestamp: datetime
    open_interest: float

    @field_validator("open_interest")
    @classmethod
    def _open_interest_cannot_be_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("open_interest cannot be negative.")
        return value

    @field_validator("timestamp")
    @classmethod
    def _timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("timestamp must be timezone-aware UTC.")
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("timestamp must be in UTC.")
        return value
