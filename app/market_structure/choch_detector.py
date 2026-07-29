"""
Detects Change of Character (CHoCH) events.
"""

from typing import Optional, Sequence

from app.liquidity.results import LiquiditySweepResult, SweepDirection
from app.models.candle import Candle

from app.market_structure._ids import make_break_id
from app.market_structure.results import MarketStructureResult, SwingPoint, SwingType, TrendDirection
from app.market_structure.shift_results import (
    BreakConfirmation,
    BreakDirection,
    DisplacementResult,
    StructureBreakResult,
    StructureBreakType,
)


class CHOCHDetector:
    """
    Detects confirmed Change of Character (CHoCH) events: a confirmed
    displacement candle that closes beyond the most relevant recent
    opposing-type swing, reversing a previously established BULLISH or
    BEARISH trend, and supported by a confirmed liquidity sweep that
    occurred before the break candle.
    """

    def detect(
        self,
        candles: Sequence[Candle],
        structure: MarketStructureResult,
        swings: Sequence[SwingPoint],
        displacement_results: Sequence[DisplacementResult],
        liquidity_sweeps: Sequence[LiquiditySweepResult],
    ) -> list[StructureBreakResult]:
        """
        Detect confirmed CHoCH events.

        A bullish CHoCH requires the previous market structure to be
        BEARISH, a confirmed bullish liquidity sweep before the break,
        and a confirmed bullish displacement candle closing strictly
        above the most relevant recent LOWER_HIGH swing. Bearish CHoCH
        is the symmetric rule: previous structure BULLISH, confirmed
        bearish sweep, and a close strictly below the most relevant
        recent HIGHER_LOW swing. RANGE or UNKNOWN prior structure never
        produces a CHoCH.
        """
        if structure.trend_direction not in (TrendDirection.BULLISH, TrendDirection.BEARISH):
            return []
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

            if (
                structure.trend_direction == TrendDirection.BEARISH
                and displacement.direction == BreakDirection.BULLISH
            ):
                broken_swing = self._find_opposing_swing(
                    confirmed_highs, candle, index, is_high=True
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
                    self._build_result(candle, index, displacement, broken_swing, sweep, BreakDirection.BULLISH)
                )

            elif (
                structure.trend_direction == TrendDirection.BULLISH
                and displacement.direction == BreakDirection.BEARISH
            ):
                broken_swing = self._find_opposing_swing(
                    confirmed_lows, candle, index, is_high=False
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
                    self._build_result(candle, index, displacement, broken_swing, sweep, BreakDirection.BEARISH)
                )

        results.sort(key=lambda r: (r.break_candle_timestamp, r.break_id))
        return results

    @staticmethod
    def _find_opposing_swing(
        candidate_swings: list[SwingPoint], candle: Candle, candle_index: int, is_high: bool
    ) -> Optional[SwingPoint]:
        """Find the nearest confirmed swing (before this candle) whose price this candle's close breaks."""
        eligible = [
            s
            for s in candidate_swings
            if s.timestamp < candle.timestamp and s.candle_index < candle_index
        ]
        if not eligible:
            return None

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
            candle.symbol, candle.timeframe, StructureBreakType.CHOCH, broken_swing.swing_id, index
        )

        return StructureBreakResult(
            break_id=break_id,
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            break_type=StructureBreakType.CHOCH,
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
                f"Confirmed {direction.value.lower()} CHoCH: close reversed prior "
                f"trend beyond swing {broken_swing.swing_id} with supporting liquidity sweep."
            ),
        )
