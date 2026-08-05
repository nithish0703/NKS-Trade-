"""
Stage 1: HTF Bias (1H).

Trade only with the 1H trend: 1H bullish permits BUY only, 1H bearish
permits SELL only. Unlike the legacy HigherTimeframeBiasAnalyzer (which
requires 4H+1H agreement), this stage reads the 1H timeframe alone.

This stage is the pipeline's direction-setter: it runs first and its
`permitted_direction` becomes the only trade direction every later
stage (Liquidity Sweep, BOS, IFVG, OI+CVD) is evaluated against. A
RANGE/UNKNOWN 1H trend permits no direction at all, so the symbol is
skipped for this scan.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.market_structure.results import MarketStructureResult, TrendDirection

HTF_BIAS_TIMEFRAME = "1h"


class HtfBiasDirection(str, Enum):
    """Trade direction permitted by the 1H HTF Bias stage."""

    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"


class HtfBiasResult(BaseModel):
    """
    Result of the 1H HTF Bias stage.

    This model only reports the 1H trend classification and the
    resulting permitted trade direction; it never contains a trade
    decision beyond direction-gating (entry price, stop loss, take
    profit are never included here).
    """

    model_config = ConfigDict(frozen=True)

    timeframe: str
    trend_direction: TrendDirection
    permitted_direction: HtfBiasDirection
    passed: bool
    reason: str


def evaluate_htf_bias(structure: Optional[MarketStructureResult]) -> HtfBiasResult:
    """
    Determine the trade direction permitted by the 1H HTF Bias.

    Passes only when 1H market structure trend is strictly BULLISH
    (permits BUY) or BEARISH (permits SELL). RANGE, UNKNOWN, or missing
    1H structure never permits a direction and never fabricates one.
    """
    if structure is None:
        return HtfBiasResult(
            timeframe=HTF_BIAS_TIMEFRAME,
            trend_direction=TrendDirection.UNKNOWN,
            permitted_direction=HtfBiasDirection.NONE,
            passed=False,
            reason="1H market structure is unavailable.",
        )

    trend = structure.trend_direction

    if trend == TrendDirection.BULLISH:
        return HtfBiasResult(
            timeframe=HTF_BIAS_TIMEFRAME,
            trend_direction=trend,
            permitted_direction=HtfBiasDirection.BUY,
            passed=True,
            reason="1H trend is BULLISH, permitting BUY only.",
        )

    if trend == TrendDirection.BEARISH:
        return HtfBiasResult(
            timeframe=HTF_BIAS_TIMEFRAME,
            trend_direction=trend,
            permitted_direction=HtfBiasDirection.SELL,
            passed=True,
            reason="1H trend is BEARISH, permitting SELL only.",
        )

    return HtfBiasResult(
        timeframe=HTF_BIAS_TIMEFRAME,
        trend_direction=trend,
        permitted_direction=HtfBiasDirection.NONE,
        passed=False,
        reason=f"1H trend is {trend.value}; no trend-aligned trade is permitted.",
    )
