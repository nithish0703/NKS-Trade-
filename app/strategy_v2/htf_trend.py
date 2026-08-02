"""
Check 1: Higher Timeframe Trend (1H).

Trade only with the 1H trend: 1H bullish permits BUY only, 1H bearish
permits SELL only. Unlike the legacy HigherTimeframeBiasAnalyzer (which
requires 4H+1H agreement), this check reads the 1H timeframe alone.

This check is the strategy's direction-setter: it runs first and its
`permitted_direction` becomes the only trade direction every later
check (Liquidity Sweep, CHoCH+BOS, Open Interest, CVD) is evaluated
against. A RANGE/UNKNOWN 1H trend permits no direction at all, so the
symbol is skipped for this scan.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.market_structure.results import MarketStructureResult, TrendDirection

HTF_TREND_TIMEFRAME = "1h"


class HtfTrendDirection(str, Enum):
    """Trade direction permitted by the 1H trend check."""

    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"


class HtfTrendResult(BaseModel):
    """
    Result of the 1H Higher Timeframe Trend check.

    This model only reports the 1H trend classification and the
    resulting permitted trade direction; it never contains a trade
    decision beyond direction-gating (entry price, stop loss, take
    profit are never included here).
    """

    model_config = ConfigDict(frozen=True)

    timeframe: str
    trend_direction: TrendDirection
    permitted_direction: HtfTrendDirection
    passed: bool
    reason: str


def evaluate_htf_trend(structure: Optional[MarketStructureResult]) -> HtfTrendResult:
    """
    Determine the trade direction permitted by the 1H trend.

    Passes only when 1H market structure trend is strictly BULLISH
    (permits BUY) or BEARISH (permits SELL). RANGE, UNKNOWN, or missing
    1H structure never permits a direction and never fabricates one.
    """
    if structure is None:
        return HtfTrendResult(
            timeframe=HTF_TREND_TIMEFRAME,
            trend_direction=TrendDirection.UNKNOWN,
            permitted_direction=HtfTrendDirection.NONE,
            passed=False,
            reason="1H market structure is unavailable.",
        )

    trend = structure.trend_direction

    if trend == TrendDirection.BULLISH:
        return HtfTrendResult(
            timeframe=HTF_TREND_TIMEFRAME,
            trend_direction=trend,
            permitted_direction=HtfTrendDirection.BUY,
            passed=True,
            reason="1H trend is BULLISH, permitting BUY only.",
        )

    if trend == TrendDirection.BEARISH:
        return HtfTrendResult(
            timeframe=HTF_TREND_TIMEFRAME,
            trend_direction=trend,
            permitted_direction=HtfTrendDirection.SELL,
            passed=True,
            reason="1H trend is BEARISH, permitting SELL only.",
        )

    return HtfTrendResult(
        timeframe=HTF_TREND_TIMEFRAME,
        trend_direction=trend,
        permitted_direction=HtfTrendDirection.NONE,
        passed=False,
        reason=f"1H trend is {trend.value}; no trend-aligned trade is permitted.",
    )
