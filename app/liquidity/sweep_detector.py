"""
Detects liquidity sweep events.
"""

from typing import Optional, Sequence

from app.models.candle import Candle

from app.liquidity._ids import make_sweep_id
from app.liquidity.equal_high_low import LiquidityCalculationError
from app.liquidity.results import (
    LiquidityLevel,
    LiquiditySide,
    LiquidityStrength,
    LiquiditySweepResult,
    SweepDirection,
)


class LiquiditySweepDetector:
    """
    Detects confirmed liquidity sweeps: a decisive penetration of a
    liquidity level followed by a reclaim (close back on the origin side)
    within a configured number of candles.
    """

    def __init__(self, minimum_penetration_ratio: float, maximum_reclaim_candles: int) -> None:
        if minimum_penetration_ratio < 0:
            raise LiquidityCalculationError(
                f"minimum_penetration_ratio cannot be negative, got "
                f"{minimum_penetration_ratio}."
            )
        if maximum_reclaim_candles < 1:
            raise LiquidityCalculationError(
                f"maximum_reclaim_candles must be at least 1, got {maximum_reclaim_candles}."
            )

        self._minimum_penetration_ratio = minimum_penetration_ratio
        self._maximum_reclaim_candles = maximum_reclaim_candles

    def _validate_candles(self, candles: Sequence[Candle]) -> None:
        if not candles:
            return
        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        for candle in candles:
            if candle.symbol != symbol:
                raise LiquidityCalculationError("All candles must share the same symbol.")
            if candle.timeframe != timeframe:
                raise LiquidityCalculationError("All candles must share the same timeframe.")

    def detect_sweeps(
        self, candles: Sequence[Candle], levels: Sequence[LiquidityLevel]
    ) -> list[LiquiditySweepResult]:
        """
        Detect all confirmed liquidity sweeps of active STRONG or
        INSTITUTIONAL liquidity levels within `candles`.

        A sweep requires: (1) a candle whose high/low penetrates beyond
        the level's price by at least `minimum_penetration_ratio`, and
        (2) a close back on the origin side of the level, on the sweep
        candle itself or within `maximum_reclaim_candles` candles after
        it. A simple touch (no penetration) or an unreclaimed breakout is
        never confirmed.

        Only candles at or after the level's `end_timestamp` are
        considered, since earlier candles predate the level's existence.

        Returns:
            Confirmed sweeps in ascending sweep-timestamp order. Each
            (level, sweep candle) pair produces at most one result.

        Raises:
            LiquidityCalculationError: If candles have mixed
                symbol/timeframe.
        """
        self._validate_candles(candles)

        eligible_levels = [
            level
            for level in levels
            if level.active and level.strength != LiquidityStrength.WEAK
        ]

        results: list[LiquiditySweepResult] = []
        seen_sweep_ids: set[str] = set()

        for level in eligible_levels:
            usable_candles = [
                (index, candle)
                for index, candle in enumerate(candles)
                if candle.timestamp >= level.end_timestamp
            ]

            position = 0
            while position < len(usable_candles):
                sweep = self._evaluate_candle_for_sweep(level, usable_candles, position)
                if sweep is None:
                    position += 1
                    continue

                if sweep.sweep_id not in seen_sweep_ids:
                    seen_sweep_ids.add(sweep.sweep_id)
                    results.append(sweep)
                # Advance past the entire penetration/reclaim episode so the
                # same level/candle sweep is never detected more than once.
                episode_end_position = min(
                    position + self._maximum_reclaim_candles,
                    len(usable_candles) - 1,
                )
                position = episode_end_position + 1

        results.sort(key=lambda r: (r.sweep_candle_timestamp, r.sweep_id))
        return results

    def _evaluate_candle_for_sweep(
        self,
        level: LiquidityLevel,
        usable_candles: list[tuple[int, Candle]],
        position: int,
    ) -> Optional[LiquiditySweepResult]:
        index, candle = usable_candles[position]

        if level.liquidity_side == LiquiditySide.BUY_SIDE:
            return self._evaluate_bearish_sweep(level, usable_candles, position)
        return self._evaluate_bullish_sweep(level, usable_candles, position)

    def _evaluate_bearish_sweep(
        self,
        level: LiquidityLevel,
        usable_candles: list[tuple[int, Candle]],
        position: int,
    ) -> Optional[LiquiditySweepResult]:
        index, candle = usable_candles[position]

        if candle.high <= level.price:
            return None  # no penetration; at most a touch

        penetration_distance = candle.high - level.price
        penetration_ratio = penetration_distance / level.price
        if penetration_ratio < self._minimum_penetration_ratio:
            return None

        reclaim_window = usable_candles[position : position + 1 + self._maximum_reclaim_candles]

        reclaimed = False
        sweep_price = candle.high
        close_price = candle.close
        reclaim_timestamp = candle.timestamp
        reclaim_index = index

        for window_index, window_candle in reclaim_window:
            sweep_price = max(sweep_price, window_candle.high)
            if window_candle.close < level.price:
                reclaimed = True
                close_price = window_candle.close
                reclaim_timestamp = window_candle.timestamp
                reclaim_index = window_index
                break

        if not reclaimed:
            return LiquiditySweepResult(
                sweep_id=make_sweep_id(level.symbol, level.timeframe, level.liquidity_id, index),
                symbol=level.symbol,
                timeframe=level.timeframe,
                direction=SweepDirection.BEARISH,
                liquidity_level=level,
                sweep_candle_timestamp=candle.timestamp,
                sweep_candle_index=index,
                sweep_price=sweep_price,
                close_price=candle.close,
                penetration_distance=penetration_distance,
                penetration_ratio=penetration_ratio,
                reclaimed_level=False,
                confirmed=False,
                reason="Level was penetrated but not reclaimed within the configured window.",
            )

        return LiquiditySweepResult(
            sweep_id=make_sweep_id(level.symbol, level.timeframe, level.liquidity_id, index),
            symbol=level.symbol,
            timeframe=level.timeframe,
            direction=SweepDirection.BEARISH,
            liquidity_level=level,
            sweep_candle_timestamp=reclaim_timestamp,
            sweep_candle_index=reclaim_index,
            sweep_price=sweep_price,
            close_price=close_price,
            penetration_distance=penetration_distance,
            penetration_ratio=penetration_ratio,
            reclaimed_level=True,
            confirmed=True,
            reason="Buy-side liquidity was penetrated and price reclaimed below the level.",
        )

    def _evaluate_bullish_sweep(
        self,
        level: LiquidityLevel,
        usable_candles: list[tuple[int, Candle]],
        position: int,
    ) -> Optional[LiquiditySweepResult]:
        index, candle = usable_candles[position]

        if candle.low >= level.price:
            return None  # no penetration; at most a touch

        penetration_distance = level.price - candle.low
        penetration_ratio = penetration_distance / level.price
        if penetration_ratio < self._minimum_penetration_ratio:
            return None

        reclaim_window = usable_candles[position : position + 1 + self._maximum_reclaim_candles]

        reclaimed = False
        sweep_price = candle.low
        close_price = candle.close
        reclaim_timestamp = candle.timestamp
        reclaim_index = index

        for window_index, window_candle in reclaim_window:
            sweep_price = min(sweep_price, window_candle.low)
            if window_candle.close > level.price:
                reclaimed = True
                close_price = window_candle.close
                reclaim_timestamp = window_candle.timestamp
                reclaim_index = window_index
                break

        if not reclaimed:
            return LiquiditySweepResult(
                sweep_id=make_sweep_id(level.symbol, level.timeframe, level.liquidity_id, index),
                symbol=level.symbol,
                timeframe=level.timeframe,
                direction=SweepDirection.BULLISH,
                liquidity_level=level,
                sweep_candle_timestamp=candle.timestamp,
                sweep_candle_index=index,
                sweep_price=sweep_price,
                close_price=candle.close,
                penetration_distance=penetration_distance,
                penetration_ratio=penetration_ratio,
                reclaimed_level=False,
                confirmed=False,
                reason="Level was penetrated but not reclaimed within the configured window.",
            )

        return LiquiditySweepResult(
            sweep_id=make_sweep_id(level.symbol, level.timeframe, level.liquidity_id, index),
            symbol=level.symbol,
            timeframe=level.timeframe,
            direction=SweepDirection.BULLISH,
            liquidity_level=level,
            sweep_candle_timestamp=reclaim_timestamp,
            sweep_candle_index=reclaim_index,
            sweep_price=sweep_price,
            close_price=close_price,
            penetration_distance=penetration_distance,
            penetration_ratio=penetration_ratio,
            reclaimed_level=True,
            confirmed=True,
            reason="Sell-side liquidity was penetrated and price reclaimed above the level.",
        )

    def detect_latest_confirmed_sweep(
        self, candles: Sequence[Candle], levels: Sequence[LiquidityLevel]
    ) -> Optional[LiquiditySweepResult]:
        """Return the most recent confirmed liquidity sweep, or None."""
        sweeps = self.detect_sweeps(candles, levels)
        confirmed = [s for s in sweeps if s.confirmed]
        if not confirmed:
            return None
        return confirmed[-1]
