"""
Validates the current market regime (trending/ranging/volatile).
"""

import math
from typing import Optional

from app.indicators.results import IndicatorSnapshot
from app.models.validation_result import ValidationResult

from app.validators.results import MarketRegimeResult, MarketRegimeStatus

_LAYER_NAME = "MARKET_REGIME"


def _is_finite(value: Optional[float]) -> bool:
    return value is not None and math.isfinite(value)


class MarketRegimeValidator:
    """
    Classifies the current market regime from an IndicatorSnapshot and
    validates that it is strictly TRENDING for a valid SMC setup.
    """

    def __init__(
        self,
        adx_trending_minimum: float,
        adx_rejection_maximum: float,
        ema_flat_threshold: float,
        minimum_atr_expansion_ratio: float,
    ) -> None:
        self._adx_trending_minimum = adx_trending_minimum
        self._adx_rejection_maximum = adx_rejection_maximum
        self._ema_flat_threshold = ema_flat_threshold
        self._minimum_atr_expansion_ratio = minimum_atr_expansion_ratio

    def evaluate(self, snapshot: IndicatorSnapshot) -> MarketRegimeResult:
        """
        Classify the current market regime.

        TRENDING requires ADX strictly above the trending minimum, a
        non-flat EMA200 slope direction (BULLISH or BEARISH), a valid
        positive ATR, and an ATR expansion ratio at or above the
        configured minimum. Missing or non-finite required data never
        fabricates a classification; it produces UNKNOWN.
        """
        adx = snapshot.adx
        ema_direction = snapshot.ema200_slope_direction
        ema_slope = snapshot.ema200_slope
        atr = snapshot.atr
        atr_expansion_ratio = snapshot.atr_expansion_ratio

        atr_valid = _is_finite(atr) and atr > 0
        atr_expansion_valid = (
            _is_finite(atr_expansion_ratio) and atr_expansion_ratio >= self._minimum_atr_expansion_ratio
        )
        ema_direction_known = ema_direction in ("BULLISH", "BEARISH", "FLAT")

        if not _is_finite(adx) or not ema_direction_known:
            return MarketRegimeResult(
                status=MarketRegimeStatus.UNKNOWN,
                adx=adx,
                ema200_slope=ema_slope,
                ema200_direction=ema_direction,
                atr=atr,
                atr_expansion_ratio=atr_expansion_ratio,
                trending=False,
                reason="Required ADX or EMA200 slope direction data is unavailable.",
            )

        if not atr_valid:
            return MarketRegimeResult(
                status=MarketRegimeStatus.LOW_VOLATILITY,
                adx=adx,
                ema200_slope=ema_slope,
                ema200_direction=ema_direction,
                atr=atr,
                atr_expansion_ratio=atr_expansion_ratio,
                trending=False,
                reason="ATR is missing, zero, or invalid.",
            )

        is_trend_direction = ema_direction in ("BULLISH", "BEARISH")

        if adx > self._adx_trending_minimum and is_trend_direction and atr_expansion_valid:
            return MarketRegimeResult(
                status=MarketRegimeStatus.TRENDING,
                adx=adx,
                ema200_slope=ema_slope,
                ema200_direction=ema_direction,
                atr=atr,
                atr_expansion_ratio=atr_expansion_ratio,
                trending=True,
                reason="ADX above trending threshold with a sloped EMA200 and sufficient ATR expansion.",
            )

        if not atr_expansion_valid:
            return MarketRegimeResult(
                status=MarketRegimeStatus.LOW_VOLATILITY,
                adx=adx,
                ema200_slope=ema_slope,
                ema200_direction=ema_direction,
                atr=atr,
                atr_expansion_ratio=atr_expansion_ratio,
                trending=False,
                reason="ATR expansion ratio is below the configured minimum.",
            )

        if adx < self._adx_rejection_maximum or ema_direction == "FLAT":
            return MarketRegimeResult(
                status=MarketRegimeStatus.RANGING,
                adx=adx,
                ema200_slope=ema_slope,
                ema200_direction=ema_direction,
                atr=atr,
                atr_expansion_ratio=atr_expansion_ratio,
                trending=False,
                reason="ADX below rejection threshold or EMA200 slope is flat.",
            )

        # ADX sits between the rejection maximum and trending minimum
        # (an unconfirmed zone): never classify as TRENDING.
        return MarketRegimeResult(
            status=MarketRegimeStatus.RANGING,
            adx=adx,
            ema200_slope=ema_slope,
            ema200_direction=ema_direction,
            atr=atr,
            atr_expansion_ratio=atr_expansion_ratio,
            trending=False,
            reason="ADX is in the unconfirmed zone between rejection and trending thresholds.",
        )

    def validate(self, snapshot: IndicatorSnapshot) -> ValidationResult:
        """
        Validate that the market regime is strictly TRENDING.

        Failure codes:
            - MARKET_REGIME_DATA_MISSING
            - DEAD_MARKET
            - ATR_INVALID
            - ATR_EXPANSION_INSUFFICIENT
            - EMA200_FLAT
            - ADX_BELOW_TRENDING_THRESHOLD
            - MARKET_NOT_TRENDING
        """
        result = self.evaluate(snapshot)

        if result.status == MarketRegimeStatus.UNKNOWN:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="MARKET_REGIME_DATA_MISSING",
                score=0.0,
            )

        if result.status == MarketRegimeStatus.DEAD_MARKET:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="DEAD_MARKET",
                score=0.0,
            )

        if not (_is_finite(result.atr) and result.atr > 0):
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="ATR is invalid.",
                rejection_code="ATR_INVALID",
                score=0.0,
            )

        if not (
            _is_finite(result.atr_expansion_ratio)
            and result.atr_expansion_ratio >= self._minimum_atr_expansion_ratio
        ):
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="ATR expansion ratio is insufficient.",
                rejection_code="ATR_EXPANSION_INSUFFICIENT",
                score=0.0,
            )

        if result.ema200_direction == "FLAT":
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="EMA200 slope is flat.",
                rejection_code="EMA200_FLAT",
                score=0.0,
            )

        if not _is_finite(result.adx) or result.adx <= self._adx_trending_minimum:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="ADX is at or below the trending threshold.",
                rejection_code="ADX_BELOW_TRENDING_THRESHOLD",
                score=0.0,
            )

        if result.status != MarketRegimeStatus.TRENDING:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Market regime is not classified as TRENDING.",
                rejection_code="MARKET_NOT_TRENDING",
                score=0.0,
            )

        return ValidationResult.success(
            layer_name=_LAYER_NAME,
            reason="Market regime is TRENDING with valid ADX, EMA200 slope, and ATR expansion.",
            score=0.0,
        )
