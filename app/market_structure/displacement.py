"""
Detects candidate and confirmed displacement candles.
"""

from typing import Optional, Sequence

from app.models.candle import Candle

from app.market_structure.shift_results import BreakDirection, DisplacementResult


class StructureShiftCalculationError(Exception):
    """Raised when displacement or structure-shift calculations cannot be performed."""


class DisplacementDetector:
    """
    Evaluates candles as candidate bullish or bearish displacement
    candles based on body ratio, close location, and (optionally)
    volume confirmation.
    """

    def __init__(
        self,
        minimum_body_ratio: float,
        bullish_close_location_minimum: float,
        bearish_close_location_maximum: float,
    ) -> None:
        if not (0 <= minimum_body_ratio <= 1):
            raise StructureShiftCalculationError(
                f"minimum_body_ratio must be between 0 and 1, got {minimum_body_ratio}."
            )
        if not (0 <= bullish_close_location_minimum <= 1):
            raise StructureShiftCalculationError(
                "bullish_close_location_minimum must be between 0 and 1, got "
                f"{bullish_close_location_minimum}."
            )
        if not (0 <= bearish_close_location_maximum <= 1):
            raise StructureShiftCalculationError(
                "bearish_close_location_maximum must be between 0 and 1, got "
                f"{bearish_close_location_maximum}."
            )

        self._minimum_body_ratio = minimum_body_ratio
        self._bullish_close_location_minimum = bullish_close_location_minimum
        self._bearish_close_location_maximum = bearish_close_location_maximum

    def _validate_candles(
        self, candles: Sequence[Candle], volume_ema_values: Optional[Sequence]
    ) -> None:
        if not candles:
            raise StructureShiftCalculationError("candles cannot be empty.")
        if volume_ema_values is not None and len(volume_ema_values) != len(candles):
            raise StructureShiftCalculationError(
                f"volume_ema_values length ({len(volume_ema_values)}) must match "
                f"candles length ({len(candles)})."
            )

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        previous_timestamp = None
        for candle in candles:
            if candle.symbol != symbol:
                raise StructureShiftCalculationError(
                    "All candles must share the same symbol."
                )
            if candle.timeframe != timeframe:
                raise StructureShiftCalculationError(
                    "All candles must share the same timeframe."
                )
            if previous_timestamp is not None and candle.timestamp <= previous_timestamp:
                raise StructureShiftCalculationError(
                    "candles must be in strictly ascending chronological order."
                )
            previous_timestamp = candle.timestamp

    def detect(
        self,
        candles: Sequence[Candle],
        volume_ema_values: Optional[Sequence[Optional[float]]] = None,
    ) -> list[DisplacementResult]:
        """
        Evaluate every candle in `candles` as a candidate displacement
        candle.

        Raises:
            StructureShiftCalculationError: If candles are empty, mixed
                symbol/timeframe, not ascending, or volume_ema_values
                length does not match candles length.
        """
        self._validate_candles(candles, volume_ema_values)

        results: list[DisplacementResult] = []
        for index in range(len(candles)):
            volume_ema = volume_ema_values[index] if volume_ema_values is not None else None
            results.append(self._evaluate_candle(candles, index, volume_ema))

        return results

    def detect_at_index(
        self,
        candles: Sequence[Candle],
        candle_index: int,
        volume_ema_values: Optional[Sequence[Optional[float]]] = None,
    ) -> DisplacementResult:
        """
        Evaluate a single candle at `candle_index` as a candidate
        displacement candle.
        """
        self._validate_candles(candles, volume_ema_values)
        if candle_index < 0 or candle_index >= len(candles):
            raise StructureShiftCalculationError(
                f"candle_index {candle_index} is out of range for {len(candles)} candles."
            )
        volume_ema = (
            volume_ema_values[candle_index] if volume_ema_values is not None else None
        )
        return self._evaluate_candle(candles, candle_index, volume_ema)

    def _evaluate_candle(
        self, candles: Sequence[Candle], index: int, volume_ema: Optional[float]
    ) -> DisplacementResult:
        candle = candles[index]
        candle_range = candle.candle_range
        body_ratio = candle.body_ratio
        body_size = candle.body_size

        if candle_range == 0:
            close_location_value = 0.0
        else:
            close_location_value = (candle.close - candle.low) / candle_range

        if volume_ema is None:
            volume_confirmed = False
        else:
            volume_confirmed = candle.volume > volume_ema

        is_bullish_direction = candle.close > candle.open
        is_bearish_direction = candle.close < candle.open
        direction = BreakDirection.BULLISH if is_bullish_direction else BreakDirection.BEARISH

        body_ratio_passes = body_ratio >= self._minimum_body_ratio

        if is_bullish_direction:
            strong_close = close_location_value >= self._bullish_close_location_minimum
        elif is_bearish_direction:
            strong_close = close_location_value <= self._bearish_close_location_maximum
        else:
            strong_close = False

        directional_rule_passes = is_bullish_direction or is_bearish_direction

        confirmed = (
            directional_rule_passes
            and body_ratio_passes
            and strong_close
            and volume_confirmed
        )

        reasons = []
        if not directional_rule_passes:
            reasons.append("Candle has no directional body (close equals open).")
        if not body_ratio_passes:
            reasons.append(
                f"Body ratio {body_ratio:.4f} is below minimum {self._minimum_body_ratio:.4f}."
            )
        if not strong_close:
            reasons.append("Close location does not indicate a strong directional close.")
        if not volume_confirmed:
            reasons.append("Volume confirmation unavailable or volume not above EMA.")
        reason = "; ".join(reasons) if reasons else "Confirmed displacement candle."

        return DisplacementResult(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            candle_timestamp=candle.timestamp,
            candle_index=index,
            direction=direction,
            body_ratio=body_ratio,
            candle_range=candle_range,
            body_size=body_size,
            close_location_value=close_location_value,
            volume_confirmed=volume_confirmed,
            strong_close=strong_close,
            confirmed=confirmed,
            reason=reason,
        )
