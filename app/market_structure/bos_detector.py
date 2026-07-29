"""
Detects Break of Structure (BOS) events.
"""

from typing import Optional, Sequence

from app.liquidity.results import LiquiditySweepResult, SweepDirection
from app.models.candle import Candle

from app.market_structure._ids import make_break_id
from app.market_structure.displacement import StructureShiftCalculationError
from app.market_structure.results import SwingPoint, SwingType
from app.market_structure.shift_results import (
    BreakConfirmation,
    BreakDirection,
    DisplacementResult,
    StructureBreakResult,
    StructureBreakType,
)


class BOSDetector:
    """
    Detects confirmed Break of Structure (BOS) events: a confirmed
    displacement candle closing strictly beyond a prior confirmed swing,
    supported by a confirmed liquidity sweep that occurred before the
    break candle.
    """

    def detect(
        self,
        candles: Sequence[Candle],
        swings: Sequence[SwingPoint],
        displacement_results: Sequence[DisplacementResult],
        liquidity_sweeps: Sequence[LiquiditySweepResult],
    ) -> list[StructureBreakResult]:
        """
        Detect confirmed BOS events.

        A bullish BOS requires a confirmed bullish displacement candle to
        close strictly above a prior confirmed swing high, with a
        confirmed bullish liquidity sweep occurring before the break
        candle. Bearish BOS is the symmetric rule against swing lows
        with a confirmed bearish sweep. A close that does not exceed the
        swing price (wick-only movement) is never confirmed, and a
        missing supporting sweep means no confirmed BOS.

        Returns:
            Confirmed BOS results in ascending break-candle timestamp
            order. No duplicate result is produced for the same broken
            swing and break candle.
        """
        if not candles:
            return []

        displacement_by_index = {d.candle_index: d for d in displacement_results}
        confirmed_sweeps = [s for s in liquidity_sweeps if s.confirmed]

        confirmed_highs = sorted(
            (s for s in swings if s.swing_type == SwingType.HIGH and s.confirmed),
            key=lambda s: s.timestamp,
        )
        confirmed_lows = sorted(
            (s for s in swings if s.swing_type == SwingType.LOW and s.confirmed),
            key=lambda s: s.timestamp,
        )

        results: list[StructureBreakResult] = []
        seen_keys: set[tuple[str, int]] = set()

        for index, candle in enumerate(candles):
            displacement = displacement_by_index.get(index)
            if displacement is None or not displacement.confirmed:
                continue

            if displacement.direction == BreakDirection.BULLISH:
                broken_swing = self._find_broken_swing(
                    confirmed_highs, candle, index, candles, is_high=True
                )
                if broken_swing is None:
                    continue
                sweep = self._find_supporting_sweep(
                    confirmed_sweeps, candle, SweepDirection.BULLISH
                )
                if sweep is None:
                    continue

                key = (broken_swing.swing_id, index)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                results.append(
                    self._build_result(
                        candle,
                        index,
                        displacement,
                        broken_swing,
                        sweep,
                        BreakDirection.BULLISH,
                    )
                )

            elif displacement.direction == BreakDirection.BEARISH:
                broken_swing = self._find_broken_swing(
                    confirmed_lows, candle, index, candles, is_high=False
                )
                if broken_swing is None:
                    continue
                sweep = self._find_supporting_sweep(
                    confirmed_sweeps, candle, SweepDirection.BEARISH
                )
                if sweep is None:
                    continue

                key = (broken_swing.swing_id, index)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                results.append(
                    self._build_result(
                        candle,
                        index,
                        displacement,
                        broken_swing,
                        sweep,
                        BreakDirection.BEARISH,
                    )
                )

        results.sort(key=lambda r: (r.break_candle_timestamp, r.break_id))
        return results

    @staticmethod
    def _find_broken_swing(
        candidate_swings: list[SwingPoint],
        candle: Candle,
        candle_index: int,
        candles: Sequence[Candle],
        is_high: bool,
    ) -> Optional[SwingPoint]:
        """Find the most recent confirmed swing (before this candle) that this candle's close breaks."""
        eligible = [
            s
            for s in candidate_swings
            if s.timestamp < candle.timestamp and s.candle_index < candle_index
        ]
        if not eligible:
            return None

        # Use the most recent qualifying swing whose price the close breaks.
        for swing in reversed(eligible):
            if is_high and candle.close > swing.price:
                return swing
            if not is_high and candle.close < swing.price:
                return swing
        return None

    @staticmethod
    def _find_supporting_sweep(
        confirmed_sweeps: list[LiquiditySweepResult],
        candle: Candle,
        required_direction: SweepDirection,
    ) -> Optional[LiquiditySweepResult]:
        """Find the latest confirmed sweep of the required direction before the break candle."""
        eligible = [
            s
            for s in confirmed_sweeps
            if s.direction == required_direction and s.sweep_candle_timestamp < candle.timestamp
        ]
        if not eligible:
            return None
        return max(eligible, key=lambda s: s.sweep_candle_timestamp)

    @staticmethod
    def _build_result(
        candle: Candle,
        index: int,
        displacement: DisplacementResult,
        broken_swing: SwingPoint,
        sweep: LiquiditySweepResult,
        direction: BreakDirection,
    ) -> StructureBreakResult:
        if direction == BreakDirection.BULLISH:
            wick_only = candle.high > broken_swing.price and candle.close <= broken_swing.price
        else:
            wick_only = candle.low < broken_swing.price and candle.close >= broken_swing.price

        strong_close_beyond_structure = not wick_only

        break_id = make_break_id(
            candle.symbol, candle.timeframe, StructureBreakType.BOS, broken_swing.swing_id, index
        )

        return StructureBreakResult(
            break_id=break_id,
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            break_type=StructureBreakType.BOS,
            direction=direction,
            broken_swing=broken_swing,
            break_candle_timestamp=candle.timestamp,
            break_candle_index=index,
            break_price=broken_swing.price,
            close_price=candle.close,
            displacement=displacement,
            preceding_liquidity_sweep=sweep,
            strong_close_beyond_structure=strong_close_beyond_structure,
            wick_only_break=wick_only,
            confirmation=BreakConfirmation.CONFIRMED,
            reason=(
                f"Confirmed {direction.value.lower()} BOS: close broke beyond swing "
                f"{broken_swing.swing_id} with supporting liquidity sweep."
            ),
        )
