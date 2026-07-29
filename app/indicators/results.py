"""
Immutable typed result models for indicator calculations.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class IndicatorSnapshot(BaseModel):
    """
    Latest calculated indicator values for a single symbol and timeframe.

    All fields may be None when insufficient candle history exists for a
    given timeframe. This model only stores calculated values; it does
    not include trade direction, confidence scores, acceptance status,
    or rejection reasons.
    """

    model_config = ConfigDict(frozen=True)

    ema20: Optional[float] = None
    ema50: Optional[float] = None
    ema200: Optional[float] = None
    ema200_slope: Optional[float] = None
    ema200_slope_direction: Optional[str] = None
    atr: Optional[float] = None
    atr_expansion_ratio: Optional[float] = None
    adx: Optional[float] = None
    plus_di: Optional[float] = None
    minus_di: Optional[float] = None
    current_volume: Optional[float] = None
    volume_ema20: Optional[float] = None
    volume_ratio: Optional[float] = None
