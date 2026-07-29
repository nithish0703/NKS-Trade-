"""
Higher-timeframe (4H/1H) bias analysis combining market structure trend
direction with EMA200 slope direction.
"""

from typing import Mapping

from app.indicators.results import IndicatorSnapshot

from app.market_structure.results import (
    HigherTimeframeBias,
    HigherTimeframeBiasResult,
    MarketStructureResult,
    TrendDirection,
)

PRIMARY_TIMEFRAME = "4h"
SECONDARY_TIMEFRAME = "1h"


class HigherTimeframeBiasAnalyzer:
    """
    Determines the final higher-timeframe bias using 4H and 1H market
    structure trend direction and EMA200 slope direction.

    This class only reports alignment; it does not generate BUY/SELL
    signals, does not reject signals, and does not calculate confidence
    scores. Market structure is never overridden by EMA200 slope.
    """

    def analyze(
        self,
        structures_by_timeframe: Mapping[str, MarketStructureResult],
        indicators_by_timeframe: Mapping[str, IndicatorSnapshot],
    ) -> HigherTimeframeBiasResult:
        """
        Combine 4H/1H market structure and EMA200 slope direction into a
        final higher-timeframe bias.
        """
        primary_structure = structures_by_timeframe.get(PRIMARY_TIMEFRAME)
        secondary_structure = structures_by_timeframe.get(SECONDARY_TIMEFRAME)
        primary_indicators = indicators_by_timeframe.get(PRIMARY_TIMEFRAME)
        secondary_indicators = indicators_by_timeframe.get(SECONDARY_TIMEFRAME)

        if primary_structure is None or secondary_structure is None:
            return HigherTimeframeBiasResult(
                primary_timeframe=PRIMARY_TIMEFRAME,
                secondary_timeframe=SECONDARY_TIMEFRAME,
                primary_trend=(
                    primary_structure.trend_direction
                    if primary_structure
                    else TrendDirection.UNKNOWN
                ),
                secondary_trend=(
                    secondary_structure.trend_direction
                    if secondary_structure
                    else TrendDirection.UNKNOWN
                ),
                primary_ema200_direction=None,
                secondary_ema200_direction=None,
                final_bias=HigherTimeframeBias.UNKNOWN,
                aligned=False,
                reason="Required 4H or 1H market structure is missing.",
            )

        primary_trend = primary_structure.trend_direction
        secondary_trend = secondary_structure.trend_direction

        if primary_indicators is None or secondary_indicators is None:
            return HigherTimeframeBiasResult(
                primary_timeframe=PRIMARY_TIMEFRAME,
                secondary_timeframe=SECONDARY_TIMEFRAME,
                primary_trend=primary_trend,
                secondary_trend=secondary_trend,
                primary_ema200_direction=None,
                secondary_ema200_direction=None,
                final_bias=HigherTimeframeBias.UNKNOWN,
                aligned=False,
                reason="Required 4H or 1H indicator snapshot is missing.",
            )

        primary_ema_direction = primary_indicators.ema200_slope_direction
        secondary_ema_direction = secondary_indicators.ema200_slope_direction

        if primary_trend == TrendDirection.UNKNOWN or secondary_trend == TrendDirection.UNKNOWN:
            return HigherTimeframeBiasResult(
                primary_timeframe=PRIMARY_TIMEFRAME,
                secondary_timeframe=SECONDARY_TIMEFRAME,
                primary_trend=primary_trend,
                secondary_trend=secondary_trend,
                primary_ema200_direction=primary_ema_direction,
                secondary_ema200_direction=secondary_ema_direction,
                final_bias=HigherTimeframeBias.UNKNOWN,
                aligned=False,
                reason="4H or 1H market structure trend is UNKNOWN.",
            )

        if primary_ema_direction is None or secondary_ema_direction is None:
            return HigherTimeframeBiasResult(
                primary_timeframe=PRIMARY_TIMEFRAME,
                secondary_timeframe=SECONDARY_TIMEFRAME,
                primary_trend=primary_trend,
                secondary_trend=secondary_trend,
                primary_ema200_direction=primary_ema_direction,
                secondary_ema200_direction=secondary_ema_direction,
                final_bias=HigherTimeframeBias.UNKNOWN,
                aligned=False,
                reason="4H or 1H EMA200 slope direction is unavailable.",
            )

        if primary_trend == TrendDirection.BULLISH and secondary_trend == TrendDirection.BULLISH:
            if primary_ema_direction == "BEARISH" or secondary_ema_direction == "BEARISH":
                return HigherTimeframeBiasResult(
                    primary_timeframe=PRIMARY_TIMEFRAME,
                    secondary_timeframe=SECONDARY_TIMEFRAME,
                    primary_trend=primary_trend,
                    secondary_trend=secondary_trend,
                    primary_ema200_direction=primary_ema_direction,
                    secondary_ema200_direction=secondary_ema_direction,
                    final_bias=HigherTimeframeBias.MIXED,
                    aligned=False,
                    reason=(
                        "Bullish structure conflicts with a bearish EMA200 slope "
                        "direction."
                    ),
                )
            return HigherTimeframeBiasResult(
                primary_timeframe=PRIMARY_TIMEFRAME,
                secondary_timeframe=SECONDARY_TIMEFRAME,
                primary_trend=primary_trend,
                secondary_trend=secondary_trend,
                primary_ema200_direction=primary_ema_direction,
                secondary_ema200_direction=secondary_ema_direction,
                final_bias=HigherTimeframeBias.BULLISH,
                aligned=True,
                reason="4H and 1H structure are both BULLISH with no bearish EMA200 conflict.",
            )

        if primary_trend == TrendDirection.BEARISH and secondary_trend == TrendDirection.BEARISH:
            if primary_ema_direction == "BULLISH" or secondary_ema_direction == "BULLISH":
                return HigherTimeframeBiasResult(
                    primary_timeframe=PRIMARY_TIMEFRAME,
                    secondary_timeframe=SECONDARY_TIMEFRAME,
                    primary_trend=primary_trend,
                    secondary_trend=secondary_trend,
                    primary_ema200_direction=primary_ema_direction,
                    secondary_ema200_direction=secondary_ema_direction,
                    final_bias=HigherTimeframeBias.MIXED,
                    aligned=False,
                    reason=(
                        "Bearish structure conflicts with a bullish EMA200 slope "
                        "direction."
                    ),
                )
            return HigherTimeframeBiasResult(
                primary_timeframe=PRIMARY_TIMEFRAME,
                secondary_timeframe=SECONDARY_TIMEFRAME,
                primary_trend=primary_trend,
                secondary_trend=secondary_trend,
                primary_ema200_direction=primary_ema_direction,
                secondary_ema200_direction=secondary_ema_direction,
                final_bias=HigherTimeframeBias.BEARISH,
                aligned=True,
                reason="4H and 1H structure are both BEARISH with no bullish EMA200 conflict.",
            )

        return HigherTimeframeBiasResult(
            primary_timeframe=PRIMARY_TIMEFRAME,
            secondary_timeframe=SECONDARY_TIMEFRAME,
            primary_trend=primary_trend,
            secondary_trend=secondary_trend,
            primary_ema200_direction=primary_ema_direction,
            secondary_ema200_direction=secondary_ema_direction,
            final_bias=HigherTimeframeBias.MIXED,
            aligned=False,
            reason="4H and 1H market structure trends disagree or are non-directional.",
        )
