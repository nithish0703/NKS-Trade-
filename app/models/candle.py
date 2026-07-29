"""
Data model representing a single OHLCV candle.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class Candle(BaseModel):
    """
    Immutable OHLCV candle for a single symbol and timeframe.

    This model only represents market data; it does not make any
    trading decisions.
    """

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    symbol: str
    timeframe: str

    @field_validator("open", "high", "low", "close")
    @classmethod
    def _prices_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Price fields must be positive.")
        return value

    @field_validator("volume")
    @classmethod
    def _volume_cannot_be_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("Volume cannot be negative.")
        return value

    @field_validator("timestamp")
    @classmethod
    def _timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("timestamp must be timezone-aware UTC.")
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("timestamp must be in UTC.")
        return value

    @model_validator(mode="after")
    def _validate_ohlc_relationships(self) -> "Candle":
        if self.high < self.open or self.high < self.close or self.high < self.low:
            raise ValueError(
                "high must be greater than or equal to open, close and low."
            )
        if self.low > self.open or self.low > self.close or self.low > self.high:
            raise ValueError(
                "low must be less than or equal to open, close and high."
            )
        return self

    @property
    def body_size(self) -> float:
        """Absolute size of the candle body."""
        return abs(self.close - self.open)

    @property
    def candle_range(self) -> float:
        """Total range of the candle from high to low."""
        return self.high - self.low

    @property
    def body_ratio(self) -> float:
        """Ratio of body size to total candle range."""
        if self.candle_range == 0:
            return 0.0
        return self.body_size / self.candle_range

    @property
    def upper_wick(self) -> float:
        """Size of the upper wick."""
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        """Size of the lower wick."""
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        """Whether the candle closed above its open."""
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        """Whether the candle closed below its open."""
        return self.close < self.open
