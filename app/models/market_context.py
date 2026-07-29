"""
Data model representing the broader market context for a pair.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from app.models.candle import Candle
from app.models.trade_zone import TradeZone
from app.models.validation_result import ValidationResult


class MarketContext(BaseModel):
    """
    Shared, read-only context passed between validation layers during
    the evaluation of a single symbol.

    This model only stores data collected or produced by other
    components; it does not calculate indicators or make trading
    decisions itself.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    detection_time_utc: datetime
    candles_by_timeframe: dict[str, list[Candle]]
    btc_candles_by_timeframe: dict[str, list[Candle]]
    direction: Optional[str] = None
    market_regime: Optional[str] = None
    higher_timeframe_bias: Optional[str] = None
    detected_liquidity: Optional[list[dict[str, Any]]] = None
    structure_shift: Optional[dict[str, Any]] = None
    selected_entry_zone: Optional[TradeZone] = None
    indicator_values: Optional[dict[str, Any]] = None
    validation_results: Optional[list[ValidationResult]] = None
    metadata: Optional[dict[str, Any]] = None

    # Shared pipeline fields (Step 13). These only ever store data
    # produced by other calculators/validators; this model still does
    # not calculate anything itself.
    entry_timeframe: Optional[str] = None
    expected_direction: Optional[str] = None
    indicators_by_timeframe: Optional[dict[str, Any]] = None
    structures_by_timeframe: Optional[dict[str, Any]] = None
    htf_bias_result: Optional[Any] = None
    liquidity_detection_result: Optional[Any] = None
    liquidity_sweeps: Optional[list[Any]] = None
    selected_liquidity_sweep: Optional[Any] = None
    structure_shift_result: Optional[Any] = None
    selected_structure_break: Optional[Any] = None
    zone_detection_result: Optional[Any] = None
    dealing_range_result: Optional[Any] = None
    retest_result: Optional[Any] = None
    pre_risk_validation_result: Optional[Any] = None
    risk_plan: Optional[Any] = None
    confidence_result: Optional[Any] = None
    pipeline_metadata: Optional[dict[str, Any]] = None

    def with_updates(self, **updates: Any) -> "MarketContext":
        """
        Return a new MarketContext with the given fields replaced.

        MarketContext is frozen; this is the only supported way to
        derive an updated context, and it never mutates the original
        instance or its nested collections in place.
        """
        return self.model_copy(update=updates)
