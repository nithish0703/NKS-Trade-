"""
Orchestrates liquidity-level detection and liquidity-sweep detection.
"""

from datetime import datetime
from typing import Optional, Sequence

from app.market_structure.results import SwingPoint
from app.models.candle import Candle

from app.liquidity.equal_high_low import EqualHighLowDetector
from app.liquidity.level_selector import InstitutionalLiquiditySelector
from app.liquidity.previous_levels import PreviousPeriodLevelDetector
from app.liquidity.results import LiquidityDetectionResult, LiquiditySweepResult
from app.liquidity.session_levels import SessionLiquidityDetector
from app.liquidity.sweep_detector import LiquiditySweepDetector
from app.liquidity.swing_liquidity import SwingLiquidityDetector


class LiquidityCalculator:
    """
    Coordinates all liquidity-level detectors and the liquidity-sweep
    detector into a single detection and sweep-evaluation workflow.
    """

    def __init__(
        self,
        equal_high_low_detector: EqualHighLowDetector,
        previous_period_level_detector: PreviousPeriodLevelDetector,
        swing_liquidity_detector: SwingLiquidityDetector,
        session_liquidity_detector: SessionLiquidityDetector,
        institutional_liquidity_selector: InstitutionalLiquiditySelector,
        liquidity_sweep_detector: LiquiditySweepDetector,
    ) -> None:
        self._equal_high_low_detector = equal_high_low_detector
        self._previous_period_level_detector = previous_period_level_detector
        self._swing_liquidity_detector = swing_liquidity_detector
        self._session_liquidity_detector = session_liquidity_detector
        self._institutional_liquidity_selector = institutional_liquidity_selector
        self._liquidity_sweep_detector = liquidity_sweep_detector

    def detect_levels(
        self,
        candles: Sequence[Candle],
        swings: Sequence[SwingPoint],
        reference_time_utc: datetime,
    ) -> LiquidityDetectionResult:
        """
        Detect all supported institutional liquidity levels and select
        the active STRONG/INSTITUTIONAL subset.

        When previous-day or previous-week history is unavailable, that
        condition is recorded in `metadata` rather than fabricated; all
        other valid liquidity types are still returned.
        """
        symbol = candles[0].symbol if candles else (swings[0].symbol if swings else "")
        timeframe = (
            candles[0].timeframe if candles else (swings[0].timeframe if swings else "")
        )

        equal_highs = self._equal_high_low_detector.detect_equal_highs(swings)
        equal_lows = self._equal_high_low_detector.detect_equal_lows(swings)

        previous_day_levels = self._previous_period_level_detector.detect_previous_day_levels(
            candles, reference_time_utc
        )
        previous_week_levels = self._previous_period_level_detector.detect_previous_week_levels(
            candles, reference_time_utc
        )
        major_swing_levels = self._swing_liquidity_detector.detect_major_swing_levels(swings)
        session_levels = self._session_liquidity_detector.detect_completed_session_levels(
            candles, reference_time_utc
        )

        all_levels = (
            equal_highs
            + equal_lows
            + previous_day_levels
            + previous_week_levels
            + major_swing_levels
            + session_levels
        )

        current_price = candles[-1].close if candles else None
        if current_price is not None:
            active_levels = self._institutional_liquidity_selector.select_active_institutional_levels(
                all_levels, current_price, reference_time_utc
            )
        else:
            active_levels = []

        metadata: dict[str, object] = {}
        if not previous_day_levels:
            metadata["previous_day_levels_unavailable"] = True
        if not previous_week_levels:
            metadata["previous_week_levels_unavailable"] = True

        return LiquidityDetectionResult(
            symbol=symbol,
            timeframe=timeframe,
            equal_highs=equal_highs,
            equal_lows=equal_lows,
            previous_day_levels=previous_day_levels,
            previous_week_levels=previous_week_levels,
            major_swing_levels=major_swing_levels,
            session_levels=session_levels,
            all_levels=all_levels,
            active_levels=active_levels,
            metadata=metadata or None,
        )

    def detect_sweeps(
        self, candles: Sequence[Candle], detection_result: LiquidityDetectionResult
    ) -> list[LiquiditySweepResult]:
        """Detect confirmed liquidity sweeps against the active liquidity levels."""
        return self._liquidity_sweep_detector.detect_sweeps(
            candles, detection_result.active_levels
        )

    def detect_latest_sweep(
        self, candles: Sequence[Candle], detection_result: LiquidityDetectionResult
    ) -> Optional[LiquiditySweepResult]:
        """Return the most recent confirmed liquidity sweep, or None."""
        return self._liquidity_sweep_detector.detect_latest_confirmed_sweep(
            candles, detection_result.active_levels
        )
