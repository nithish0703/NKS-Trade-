"""
Filters setups based on volatility conditions.
"""

import math
from typing import Optional, Sequence

from app.indicators.results import IndicatorSnapshot
from app.models.candle import Candle
from app.models.validation_result import ValidationResult

from app.validators.results import VolatilityResult, VolatilityStatus

_LAYER_NAME = "VOLATILITY_FILTER"


def _is_finite(value: Optional[float]) -> bool:
    return value is not None and math.isfinite(value)


class VolatilityFilter:
    """
    Classifies current market volatility from candle ranges and an
    IndicatorSnapshot's ATR data, and validates that volatility is
    acceptable (EXPANDING or NORMAL) for a valid SMC setup.
    """

    def __init__(
        self,
        minimum_atr_value: float,
        minimum_atr_expansion_ratio: float,
        compression_lookback: int,
        minimum_candle_range_ratio: float,
    ) -> None:
        self._minimum_atr_value = minimum_atr_value
        self._minimum_atr_expansion_ratio = minimum_atr_expansion_ratio
        self._compression_lookback = compression_lookback
        self._minimum_candle_range_ratio = minimum_candle_range_ratio

    def evaluate(self, candles: Sequence[Candle], snapshot: IndicatorSnapshot) -> VolatilityResult:
        """
        Classify current volatility.

        Uses the current candle's range against the average range of the
        preceding `compression_lookback` candles to compute a
        compression ratio, combined with ATR and ATR-expansion data from
        `snapshot`. Never fabricates ATR or candle data; when required
        data is unavailable the result is UNKNOWN.
        """
        atr = snapshot.atr
        atr_expansion_ratio = snapshot.atr_expansion_ratio

        if not candles:
            return VolatilityResult(
                status=VolatilityStatus.UNKNOWN,
                atr=atr,
                atr_expansion_ratio=atr_expansion_ratio,
                current_candle_range=None,
                average_candle_range=None,
                compression_ratio=None,
                acceptable=False,
                reason="No candles were provided.",
            )

        current_candle = candles[-1]
        current_range = current_candle.candle_range

        lookback_candles = candles[-1 - self._compression_lookback : -1]
        if lookback_candles:
            average_range = sum(c.candle_range for c in lookback_candles) / len(lookback_candles)
        else:
            average_range = None

        compression_ratio = None
        if average_range is not None and average_range > 0:
            compression_ratio = current_range / average_range

        atr_valid = _is_finite(atr) and atr > 0
        atr_expansion_valid = (
            _is_finite(atr_expansion_ratio) and atr_expansion_ratio >= self._minimum_atr_expansion_ratio
        )

        if not atr_valid:
            return VolatilityResult(
                status=VolatilityStatus.DEAD,
                atr=atr,
                atr_expansion_ratio=atr_expansion_ratio,
                current_candle_range=current_range,
                average_candle_range=average_range,
                compression_ratio=compression_ratio,
                acceptable=False,
                reason="ATR is zero, missing, or invalid.",
            )

        if average_range is not None and average_range == 0:
            return VolatilityResult(
                status=VolatilityStatus.DEAD,
                atr=atr,
                atr_expansion_ratio=atr_expansion_ratio,
                current_candle_range=current_range,
                average_candle_range=average_range,
                compression_ratio=compression_ratio,
                acceptable=False,
                reason="Average candle range is zero; no defensible price movement.",
            )

        tiny_candle = (
            compression_ratio is not None and compression_ratio < self._minimum_candle_range_ratio
        )

        if atr < self._minimum_atr_value:
            return VolatilityResult(
                status=VolatilityStatus.LOW,
                atr=atr,
                atr_expansion_ratio=atr_expansion_ratio,
                current_candle_range=current_range,
                average_candle_range=average_range,
                compression_ratio=compression_ratio,
                acceptable=False,
                reason="ATR is positive but below the configured minimum ATR value.",
            )

        if not atr_expansion_valid or tiny_candle:
            return VolatilityResult(
                status=VolatilityStatus.COMPRESSED,
                atr=atr,
                atr_expansion_ratio=atr_expansion_ratio,
                current_candle_range=current_range,
                average_candle_range=average_range,
                compression_ratio=compression_ratio,
                acceptable=False,
                reason="ATR expansion ratio is below threshold or recent candle ranges are contracted.",
            )

        if atr_expansion_ratio > self._minimum_atr_expansion_ratio and (
            compression_ratio is None or compression_ratio >= self._minimum_candle_range_ratio
        ):
            return VolatilityResult(
                status=VolatilityStatus.EXPANDING,
                atr=atr,
                atr_expansion_ratio=atr_expansion_ratio,
                current_candle_range=current_range,
                average_candle_range=average_range,
                compression_ratio=compression_ratio,
                acceptable=True,
                reason="ATR is expanding and current candle range is not tiny relative to recent average.",
            )

        return VolatilityResult(
            status=VolatilityStatus.NORMAL,
            atr=atr,
            atr_expansion_ratio=atr_expansion_ratio,
            current_candle_range=current_range,
            average_candle_range=average_range,
            compression_ratio=compression_ratio,
            acceptable=True,
            reason="ATR is valid with no compression or dead-market condition.",
        )

    def validate(self, candles: Sequence[Candle], snapshot: IndicatorSnapshot) -> ValidationResult:
        """
        Validate that volatility is acceptable (EXPANDING or NORMAL).

        Failure codes:
            - VOLATILITY_DATA_MISSING
            - DEAD_MARKET
            - LOW_ATR
            - ATR_COMPRESSION
            - TINY_CANDLE
        """
        result = self.evaluate(candles, snapshot)

        if result.status == VolatilityStatus.UNKNOWN:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="VOLATILITY_DATA_MISSING",
                score=0.0,
            )

        if result.status == VolatilityStatus.DEAD:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="DEAD_MARKET",
                score=0.0,
            )

        if result.status == VolatilityStatus.LOW:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="LOW_ATR",
                score=0.0,
            )

        if (
            result.compression_ratio is not None
            and result.compression_ratio < self._minimum_candle_range_ratio
        ):
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Current candle range is tiny relative to recent average.",
                rejection_code="TINY_CANDLE",
                score=0.0,
            )

        if result.status == VolatilityStatus.COMPRESSED:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="ATR_COMPRESSION",
                score=0.0,
            )

        return ValidationResult.success(
            layer_name=_LAYER_NAME,
            reason=result.reason,
            score=0.0,
        )
