"""
Data model representing the broader market context for a pair.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from app.models.candle import Candle
from app.models.trade_zone import TradeZone


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
    direction: Optional[str] = None
    selected_entry_zone: Optional[TradeZone] = None

    # Shared pipeline fields. These only ever store data produced by
    # other calculators/validators; this model still does not
    # calculate anything itself.
    entry_timeframe: Optional[str] = None
    expected_direction: Optional[str] = None
    indicators_by_timeframe: Optional[dict[str, Any]] = None
    structures_by_timeframe: Optional[dict[str, Any]] = None
    liquidity_detection_result: Optional[Any] = None
    liquidity_sweeps: Optional[list[Any]] = None
    selected_liquidity_sweep: Optional[Any] = None
    selected_structure_break: Optional[Any] = None
    risk_plan: Optional[Any] = None

    def with_updates(self, **updates: Any) -> "MarketContext":
        """
        Return a new MarketContext with the given fields replaced.

        MarketContext is frozen; this is the only supported way to
        derive an updated context, and it never mutates the original
        instance or its nested collections in place.
        """
        return self.model_copy(update=updates)
