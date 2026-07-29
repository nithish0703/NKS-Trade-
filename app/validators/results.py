"""
Immutable typed result models for market-context validation layers:
market regime, momentum, volatility, session, BTC alignment, fake
breakout, and candle quality.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from app.liquidity.results import LiquiditySweepResult
from app.market_structure.shift_results import StructureBreakResult


class MarketRegimeStatus(str, Enum):
    """Classification of the current market regime."""

    TRENDING = "TRENDING"
    RANGING = "RANGING"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    DEAD_MARKET = "DEAD_MARKET"
    UNKNOWN = "UNKNOWN"


class MomentumAlignment(str, Enum):
    """Alignment of short-term momentum with the expected trade direction."""

    BUY_ALIGNED = "BUY_ALIGNED"
    SELL_ALIGNED = "SELL_ALIGNED"
    NEUTRAL = "NEUTRAL"
    CONFLICT = "CONFLICT"


class VolatilityStatus(str, Enum):
    """Classification of current market volatility."""

    EXPANDING = "EXPANDING"
    NORMAL = "NORMAL"
    COMPRESSED = "COMPRESSED"
    LOW = "LOW"
    DEAD = "DEAD"
    UNKNOWN = "UNKNOWN"


class TradingSession(str, Enum):
    """Named UTC trading session window."""

    ASIA = "ASIA"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"
    LONDON_NEW_YORK_OVERLAP = "LONDON_NEW_YORK_OVERLAP"
    OUTSIDE_SUPPORTED_SESSION = "OUTSIDE_SUPPORTED_SESSION"


class BTCAlignmentStatus(str, Enum):
    """Alignment status between an altcoin setup and BTC market direction."""

    ALIGNED = "ALIGNED"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


class FakeBreakoutStatus(str, Enum):
    """Outcome of fake-breakout analysis for a structural break."""

    PASSED = "PASSED"
    DETECTED = "DETECTED"
    UNKNOWN = "UNKNOWN"


class CandleQualityStatus(str, Enum):
    """Classification of a candle's shape quality for entry confirmation."""

    STRONG = "STRONG"
    WEAK = "WEAK"
    DOJI = "DOJI"
    TINY_BODY = "TINY_BODY"
    LONG_WICK = "LONG_WICK"
    SPINNING_TOP = "SPINNING_TOP"
    UNKNOWN = "UNKNOWN"


class MarketRegimeResult(BaseModel):
    """Result of market-regime classification from an IndicatorSnapshot."""

    model_config = ConfigDict(frozen=True)

    status: MarketRegimeStatus
    adx: Optional[float] = None
    ema200_slope: Optional[float] = None
    ema200_direction: Optional[str] = None
    atr: Optional[float] = None
    atr_expansion_ratio: Optional[float] = None
    trending: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None


class MomentumResult(BaseModel):
    """Result of short-term momentum alignment evaluation. Confidence helper only."""

    model_config = ConfigDict(frozen=True)

    alignment: MomentumAlignment
    ema20: Optional[float] = None
    ema50: Optional[float] = None
    expected_direction: str
    confidence_boost_eligible: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None


class VolatilityResult(BaseModel):
    """Result of volatility classification from candles and an IndicatorSnapshot."""

    model_config = ConfigDict(frozen=True)

    status: VolatilityStatus
    atr: Optional[float] = None
    atr_expansion_ratio: Optional[float] = None
    current_candle_range: Optional[float] = None
    average_candle_range: Optional[float] = None
    compression_ratio: Optional[float] = None
    acceptable: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None


class SessionValidationResult(BaseModel):
    """Result of UTC trading-session detection and Asian-session exception evaluation."""

    model_config = ConfigDict(frozen=True)

    session: TradingSession
    session_start_utc: Optional[datetime] = None
    session_end_utc: Optional[datetime] = None
    london_active: bool
    new_york_active: bool
    overlap_active: bool
    asian_exception_required: bool
    asian_exception_passed: bool
    allowed: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None

    @field_validator("session_start_utc", "session_end_utc")
    @classmethod
    def _timestamps_must_be_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return value
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("timestamps must be timezone-aware UTC.")
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("timestamps must be in UTC.")
        return value


class BTCAlignmentResult(BaseModel):
    """Result of validating an altcoin setup's direction against BTC market direction."""

    model_config = ConfigDict(frozen=True)

    altcoin_symbol: str
    expected_direction: str
    btc_trend: Optional[str] = None
    btc_htf_bias: Optional[str] = None
    status: BTCAlignmentStatus
    aligned: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None


class FakeBreakoutResult(BaseModel):
    """Result of fake-breakout analysis for a confirmed structural break."""

    model_config = ConfigDict(frozen=True)

    status: FakeBreakoutStatus
    liquidity_sweep: Optional[LiquiditySweepResult] = None
    structure_break: Optional[StructureBreakResult] = None
    reversal_candle_timestamp: Optional[datetime] = None
    returned_inside_structure: bool
    immediate_reversal: bool
    passed: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None

    @field_validator("reversal_candle_timestamp")
    @classmethod
    def _timestamp_must_be_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return value
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("reversal_candle_timestamp must be timezone-aware UTC.")
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("reversal_candle_timestamp must be in UTC.")
        return value


class CandleQualityResult(BaseModel):
    """Result of candle-shape quality evaluation for entry confirmation."""

    model_config = ConfigDict(frozen=True)

    status: CandleQualityStatus
    body_ratio: float
    upper_wick_ratio: float
    lower_wick_ratio: float
    close_location_value: float
    displacement_direction: Optional[str] = None
    acceptable: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None
