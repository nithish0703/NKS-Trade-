"""
Aggregates individual indicator calculations into per-timeframe snapshots.

This module orchestrates existing indicator calculations only. It does
not make trade decisions and does not classify the overall market regime.
"""

from typing import Mapping, Sequence

from app.config.thresholds import (
    ADX_PERIOD,
    ATR_EXPANSION_LOOKBACK,
    ATR_PERIOD,
    EMA_FAST_PERIOD,
    EMA_FLAT_THRESHOLD,
    EMA_SLOPE_LOOKBACK,
    EMA_SLOW_PERIOD,
    EMA_TREND_PERIOD,
    VOLUME_EMA_PERIOD,
)
from app.indicators.adx import calculate_adx
from app.indicators.atr import calculate_atr, calculate_atr_expansion
from app.indicators.ema import (
    IndicatorCalculationError,
    calculate_candle_close_ema,
    calculate_ema_slope,
    classify_ema_slope,
)
from app.indicators.results import IndicatorSnapshot
from app.indicators.volume import calculate_volume_ema, calculate_volume_ratio
from app.models.candle import Candle


class IndicatorCalculator:
    """
    Calculates a full IndicatorSnapshot from a chronologically ordered
    sequence of candles, using centralized configuration constants.
    """

    def __init__(
        self,
        ema_fast_period: int = EMA_FAST_PERIOD,
        ema_slow_period: int = EMA_SLOW_PERIOD,
        ema_trend_period: int = EMA_TREND_PERIOD,
        volume_ema_period: int = VOLUME_EMA_PERIOD,
        atr_period: int = ATR_PERIOD,
        adx_period: int = ADX_PERIOD,
        ema_slope_lookback: int = EMA_SLOPE_LOOKBACK,
        ema_flat_threshold: float = EMA_FLAT_THRESHOLD,
        atr_expansion_lookback: int = ATR_EXPANSION_LOOKBACK,
    ) -> None:
        self._ema_fast_period = ema_fast_period
        self._ema_slow_period = ema_slow_period
        self._ema_trend_period = ema_trend_period
        self._volume_ema_period = volume_ema_period
        self._atr_period = atr_period
        self._adx_period = adx_period
        self._ema_slope_lookback = ema_slope_lookback
        self._ema_flat_threshold = ema_flat_threshold
        self._atr_expansion_lookback = atr_expansion_lookback

    def calculate_snapshot(self, candles: Sequence[Candle]) -> IndicatorSnapshot:
        """
        Calculate the latest IndicatorSnapshot from a candle sequence.

        Raises:
            IndicatorCalculationError: If candles are empty, out of
                chronological order, or required indicator values cannot
                be calculated from the available history.
        """
        if not candles:
            raise IndicatorCalculationError("candles cannot be empty.")

        for index in range(1, len(candles)):
            if candles[index].timestamp <= candles[index - 1].timestamp:
                raise IndicatorCalculationError(
                    "candles must be in strictly ascending chronological order."
                )

        ema20_values = calculate_candle_close_ema(candles, self._ema_fast_period)
        ema50_values = calculate_candle_close_ema(candles, self._ema_slow_period)
        ema200_values = calculate_candle_close_ema(candles, self._ema_trend_period)

        if ema20_values[-1] is None:
            raise IndicatorCalculationError(
                f"Insufficient candles to calculate a {self._ema_fast_period}-period "
                "EMA20."
            )
        if ema50_values[-1] is None:
            raise IndicatorCalculationError(
                f"Insufficient candles to calculate a {self._ema_slow_period}-period "
                "EMA50."
            )
        if ema200_values[-1] is None:
            raise IndicatorCalculationError(
                f"Insufficient candles to calculate a {self._ema_trend_period}-period "
                "EMA200."
            )

        ema200_slope = calculate_ema_slope(ema200_values, self._ema_slope_lookback)
        ema200_slope_direction = classify_ema_slope(
            ema200_slope, self._ema_flat_threshold
        )

        atr_values = calculate_atr(candles, self._atr_period)
        if atr_values[-1] is None:
            raise IndicatorCalculationError(
                f"Insufficient candles to calculate a {self._atr_period}-period ATR."
            )
        atr = atr_values[-1]
        atr_expansion_ratio = calculate_atr_expansion(
            atr_values, self._atr_expansion_lookback
        )

        adx_result = calculate_adx(candles, self._adx_period)
        if adx_result.adx[-1] is None:
            raise IndicatorCalculationError(
                f"Insufficient candles to calculate a {self._adx_period}-period ADX."
            )
        adx = adx_result.adx[-1]
        plus_di = adx_result.plus_di[-1]
        minus_di = adx_result.minus_di[-1]

        volume_ema_values = calculate_volume_ema(candles, self._volume_ema_period)
        if volume_ema_values[-1] is None:
            raise IndicatorCalculationError(
                f"Insufficient candles to calculate a {self._volume_ema_period}-period "
                "volume EMA."
            )
        volume_ema20 = volume_ema_values[-1]
        current_volume = candles[-1].volume
        volume_ratio = calculate_volume_ratio(current_volume, volume_ema20)

        return IndicatorSnapshot(
            ema20=ema20_values[-1],
            ema50=ema50_values[-1],
            ema200=ema200_values[-1],
            ema200_slope=ema200_slope,
            ema200_slope_direction=ema200_slope_direction,
            atr=atr,
            atr_expansion_ratio=atr_expansion_ratio,
            adx=adx,
            plus_di=plus_di,
            minus_di=minus_di,
            current_volume=current_volume,
            volume_ema20=volume_ema20,
            volume_ratio=volume_ratio,
        )

    def calculate_multiple_timeframes(
        self, candles_by_timeframe: Mapping[str, Sequence[Candle]]
    ) -> dict[str, IndicatorSnapshot]:
        """
        Calculate an IndicatorSnapshot independently for each timeframe.

        Raises:
            IndicatorCalculationError: An aggregated error if any
                timeframe's snapshot calculation fails. No partial result
                is returned.
        """
        errors: list[str] = []
        snapshots: dict[str, IndicatorSnapshot] = {}

        for timeframe, candles in candles_by_timeframe.items():
            try:
                snapshots[timeframe] = self.calculate_snapshot(candles)
            except IndicatorCalculationError as exc:
                errors.append(f"{timeframe}: {exc}")

        if errors:
            raise IndicatorCalculationError(
                f"Failed to calculate indicators for {len(errors)} timeframe(s): "
                f"{'; '.join(errors)}"
            )

        return snapshots
