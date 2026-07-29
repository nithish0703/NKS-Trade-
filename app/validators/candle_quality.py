"""
Validates candle quality (body/wick ratios, noise, etc.).
"""

from app.indicators.candle_metrics import calculate_candle_metrics
from app.models.candle import Candle
from app.models.validation_result import ValidationResult

from app.validators.results import CandleQualityResult, CandleQualityStatus

_LAYER_NAME = "CANDLE_QUALITY"


class CandleQualityValidator:
    """
    Validates that a candle's shape (body ratio, wick ratios, close
    location) is strong enough to support a BUY or SELL entry, rejecting
    doji, tiny-body, spinning-top, weak-close, and excessive-opposite-
    wick candles.
    """

    def __init__(
        self,
        minimum_body_ratio: float,
        doji_maximum_body_ratio: float,
        spinning_top_maximum_body_ratio: float,
        maximum_opposite_wick_ratio: float,
        bullish_close_location_minimum: float,
        bearish_close_location_maximum: float,
    ) -> None:
        self._minimum_body_ratio = minimum_body_ratio
        self._doji_maximum_body_ratio = doji_maximum_body_ratio
        self._spinning_top_maximum_body_ratio = spinning_top_maximum_body_ratio
        self._maximum_opposite_wick_ratio = maximum_opposite_wick_ratio
        self._bullish_close_location_minimum = bullish_close_location_minimum
        self._bearish_close_location_maximum = bearish_close_location_maximum

    def evaluate(self, candle: Candle, expected_direction: str) -> CandleQualityResult:
        """
        Evaluate candle shape quality for `expected_direction`.

        A candle is only STRONG (acceptable) when it is directionally
        correct, has a body ratio at or above the configured minimum,
        a strong close near the relevant extreme, and does not show an
        excessive opposite-side wick. Doji, tiny-body, and spinning-top
        shapes are always rejected regardless of direction.
        """
        metrics = calculate_candle_metrics(candle)

        if candle.candle_range == 0:
            return CandleQualityResult(
                status=CandleQualityStatus.UNKNOWN,
                body_ratio=metrics.body_ratio,
                upper_wick_ratio=metrics.upper_wick_ratio,
                lower_wick_ratio=metrics.lower_wick_ratio,
                close_location_value=metrics.close_location_value,
                displacement_direction=None,
                acceptable=False,
                reason="Candle has zero range; quality cannot be evaluated.",
            )

        if metrics.body_ratio <= self._doji_maximum_body_ratio:
            return CandleQualityResult(
                status=CandleQualityStatus.DOJI,
                body_ratio=metrics.body_ratio,
                upper_wick_ratio=metrics.upper_wick_ratio,
                lower_wick_ratio=metrics.lower_wick_ratio,
                close_location_value=metrics.close_location_value,
                displacement_direction=None,
                acceptable=False,
                reason="Candle body ratio indicates a doji.",
            )

        if metrics.body_ratio < self._minimum_body_ratio:
            if metrics.body_ratio <= self._spinning_top_maximum_body_ratio:
                status = CandleQualityStatus.SPINNING_TOP
                reason = "Candle body ratio indicates a spinning top."
            else:
                status = CandleQualityStatus.TINY_BODY
                reason = "Candle body ratio is below the configured minimum."
            return CandleQualityResult(
                status=status,
                body_ratio=metrics.body_ratio,
                upper_wick_ratio=metrics.upper_wick_ratio,
                lower_wick_ratio=metrics.lower_wick_ratio,
                close_location_value=metrics.close_location_value,
                displacement_direction=None,
                acceptable=False,
                reason=reason,
            )

        if expected_direction == "BUY":
            if not candle.is_bullish:
                return CandleQualityResult(
                    status=CandleQualityStatus.WEAK,
                    body_ratio=metrics.body_ratio,
                    upper_wick_ratio=metrics.upper_wick_ratio,
                    lower_wick_ratio=metrics.lower_wick_ratio,
                    close_location_value=metrics.close_location_value,
                    displacement_direction="BEARISH" if candle.is_bearish else None,
                    acceptable=False,
                    reason="Candle is not bullish; direction mismatch for BUY.",
                )
            if metrics.upper_wick_ratio > self._maximum_opposite_wick_ratio:
                return CandleQualityResult(
                    status=CandleQualityStatus.LONG_WICK,
                    body_ratio=metrics.body_ratio,
                    upper_wick_ratio=metrics.upper_wick_ratio,
                    lower_wick_ratio=metrics.lower_wick_ratio,
                    close_location_value=metrics.close_location_value,
                    displacement_direction="BULLISH",
                    acceptable=False,
                    reason="Upper wick is excessively long for a BUY candle.",
                )
            if metrics.close_location_value < self._bullish_close_location_minimum:
                return CandleQualityResult(
                    status=CandleQualityStatus.WEAK,
                    body_ratio=metrics.body_ratio,
                    upper_wick_ratio=metrics.upper_wick_ratio,
                    lower_wick_ratio=metrics.lower_wick_ratio,
                    close_location_value=metrics.close_location_value,
                    displacement_direction="BULLISH",
                    acceptable=False,
                    reason="Close location is too weak for a strong BUY candle.",
                )
            return CandleQualityResult(
                status=CandleQualityStatus.STRONG,
                body_ratio=metrics.body_ratio,
                upper_wick_ratio=metrics.upper_wick_ratio,
                lower_wick_ratio=metrics.lower_wick_ratio,
                close_location_value=metrics.close_location_value,
                displacement_direction="BULLISH",
                acceptable=True,
                reason="Strong bullish candle with a close near the high and no excessive upper wick.",
            )

        if expected_direction == "SELL":
            if not candle.is_bearish:
                return CandleQualityResult(
                    status=CandleQualityStatus.WEAK,
                    body_ratio=metrics.body_ratio,
                    upper_wick_ratio=metrics.upper_wick_ratio,
                    lower_wick_ratio=metrics.lower_wick_ratio,
                    close_location_value=metrics.close_location_value,
                    displacement_direction="BULLISH" if candle.is_bullish else None,
                    acceptable=False,
                    reason="Candle is not bearish; direction mismatch for SELL.",
                )
            if metrics.lower_wick_ratio > self._maximum_opposite_wick_ratio:
                return CandleQualityResult(
                    status=CandleQualityStatus.LONG_WICK,
                    body_ratio=metrics.body_ratio,
                    upper_wick_ratio=metrics.upper_wick_ratio,
                    lower_wick_ratio=metrics.lower_wick_ratio,
                    close_location_value=metrics.close_location_value,
                    displacement_direction="BEARISH",
                    acceptable=False,
                    reason="Lower wick is excessively long for a SELL candle.",
                )
            if metrics.close_location_value > self._bearish_close_location_maximum:
                return CandleQualityResult(
                    status=CandleQualityStatus.WEAK,
                    body_ratio=metrics.body_ratio,
                    upper_wick_ratio=metrics.upper_wick_ratio,
                    lower_wick_ratio=metrics.lower_wick_ratio,
                    close_location_value=metrics.close_location_value,
                    displacement_direction="BEARISH",
                    acceptable=False,
                    reason="Close location is too weak for a strong SELL candle.",
                )
            return CandleQualityResult(
                status=CandleQualityStatus.STRONG,
                body_ratio=metrics.body_ratio,
                upper_wick_ratio=metrics.upper_wick_ratio,
                lower_wick_ratio=metrics.lower_wick_ratio,
                close_location_value=metrics.close_location_value,
                displacement_direction="BEARISH",
                acceptable=True,
                reason="Strong bearish candle with a close near the low and no excessive lower wick.",
            )

        return CandleQualityResult(
            status=CandleQualityStatus.UNKNOWN,
            body_ratio=metrics.body_ratio,
            upper_wick_ratio=metrics.upper_wick_ratio,
            lower_wick_ratio=metrics.lower_wick_ratio,
            close_location_value=metrics.close_location_value,
            displacement_direction=None,
            acceptable=False,
            reason=f"Unrecognized expected_direction '{expected_direction}'.",
        )

    def validate(self, candle: Candle, expected_direction: str) -> ValidationResult:
        """
        Validate candle quality for `expected_direction`.

        Failure codes:
            - CANDLE_QUALITY_DATA_INVALID
            - DOJI_CANDLE
            - TINY_CANDLE_BODY
            - SPINNING_TOP
            - CANDLE_DIRECTION_MISMATCH
            - EXCESSIVE_OPPOSITE_WICK
            - WEAK_CANDLE_CLOSE
        """
        result = self.evaluate(candle, expected_direction)

        if result.acceptable:
            return ValidationResult.success(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                score=0.0,
            )

        if result.status == CandleQualityStatus.UNKNOWN:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="CANDLE_QUALITY_DATA_INVALID",
                score=0.0,
            )

        if result.status == CandleQualityStatus.DOJI:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="DOJI_CANDLE",
                score=0.0,
            )

        if result.status == CandleQualityStatus.TINY_BODY:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="TINY_CANDLE_BODY",
                score=0.0,
            )

        if result.status == CandleQualityStatus.SPINNING_TOP:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="SPINNING_TOP",
                score=0.0,
            )

        if result.status == CandleQualityStatus.LONG_WICK:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="EXCESSIVE_OPPOSITE_WICK",
                score=0.0,
            )

        if "direction mismatch" in result.reason.lower():
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="CANDLE_DIRECTION_MISMATCH",
                score=0.0,
            )

        return ValidationResult.failure(
            layer_name=_LAYER_NAME,
            reason=result.reason,
            rejection_code="WEAK_CANDLE_CLOSE",
            score=0.0,
        )
