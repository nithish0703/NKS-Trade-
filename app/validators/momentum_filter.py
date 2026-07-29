"""
Filters setups based on momentum conditions.

Momentum is a confidence helper only: this module never rejects an
otherwise valid SMC setup, regardless of EMA alignment or missing data.
"""

from app.indicators.results import IndicatorSnapshot
from app.models.validation_result import ValidationResult

from app.validators.results import MomentumAlignment, MomentumResult

_LAYER_NAME = "MOMENTUM_FILTER"


class MomentumFilter:
    """
    Evaluates short-term EMA20/EMA50 momentum alignment with the expected
    trade direction. This is a confidence helper only; `validate()`
    always passes.
    """

    def evaluate(self, snapshot: IndicatorSnapshot, expected_direction: str) -> MomentumResult:
        """
        Evaluate momentum alignment.

        BUY: EMA20 > EMA50 is BUY_ALIGNED (confidence_boost_eligible=True);
        otherwise NEUTRAL/CONFLICT (confidence_boost_eligible=False).
        SELL: EMA20 < EMA50 is SELL_ALIGNED (confidence_boost_eligible=True);
        otherwise NEUTRAL/CONFLICT (confidence_boost_eligible=False).
        Missing EMA data always yields NEUTRAL with no confidence boost.
        """
        ema20 = snapshot.ema20
        ema50 = snapshot.ema50

        if ema20 is None or ema50 is None:
            return MomentumResult(
                alignment=MomentumAlignment.NEUTRAL,
                ema20=ema20,
                ema50=ema50,
                expected_direction=expected_direction,
                confidence_boost_eligible=False,
                reason="EMA20 or EMA50 is unavailable.",
            )

        if expected_direction == "BUY":
            if ema20 > ema50:
                return MomentumResult(
                    alignment=MomentumAlignment.BUY_ALIGNED,
                    ema20=ema20,
                    ema50=ema50,
                    expected_direction=expected_direction,
                    confidence_boost_eligible=True,
                    reason="EMA20 is above EMA50, aligned with the expected BUY direction.",
                )
            if ema20 < ema50:
                return MomentumResult(
                    alignment=MomentumAlignment.CONFLICT,
                    ema20=ema20,
                    ema50=ema50,
                    expected_direction=expected_direction,
                    confidence_boost_eligible=False,
                    reason="EMA20 is below EMA50, conflicting with the expected BUY direction.",
                )
            return MomentumResult(
                alignment=MomentumAlignment.NEUTRAL,
                ema20=ema20,
                ema50=ema50,
                expected_direction=expected_direction,
                confidence_boost_eligible=False,
                reason="EMA20 equals EMA50; momentum is neutral.",
            )

        if expected_direction == "SELL":
            if ema20 < ema50:
                return MomentumResult(
                    alignment=MomentumAlignment.SELL_ALIGNED,
                    ema20=ema20,
                    ema50=ema50,
                    expected_direction=expected_direction,
                    confidence_boost_eligible=True,
                    reason="EMA20 is below EMA50, aligned with the expected SELL direction.",
                )
            if ema20 > ema50:
                return MomentumResult(
                    alignment=MomentumAlignment.CONFLICT,
                    ema20=ema20,
                    ema50=ema50,
                    expected_direction=expected_direction,
                    confidence_boost_eligible=False,
                    reason="EMA20 is above EMA50, conflicting with the expected SELL direction.",
                )
            return MomentumResult(
                alignment=MomentumAlignment.NEUTRAL,
                ema20=ema20,
                ema50=ema50,
                expected_direction=expected_direction,
                confidence_boost_eligible=False,
                reason="EMA20 equals EMA50; momentum is neutral.",
            )

        return MomentumResult(
            alignment=MomentumAlignment.NEUTRAL,
            ema20=ema20,
            ema50=ema50,
            expected_direction=expected_direction,
            confidence_boost_eligible=False,
            reason=f"Unrecognized expected_direction '{expected_direction}'.",
        )

    def validate(self, snapshot: IndicatorSnapshot, expected_direction: str) -> ValidationResult:
        """
        Always returns passed=True. Momentum alignment (or lack thereof)
        and missing EMA data are recorded in `data` for later confidence
        scoring, but never reject the setup.
        """
        result = self.evaluate(snapshot, expected_direction)

        if result.ema20 is None or result.ema50 is None:
            reason = "MOMENTUM_DATA_MISSING"
        elif result.alignment in (MomentumAlignment.BUY_ALIGNED, MomentumAlignment.SELL_ALIGNED):
            reason = "MOMENTUM_ALIGNED"
        elif result.alignment == MomentumAlignment.CONFLICT:
            reason = "MOMENTUM_CONFLICT"
        else:
            reason = "MOMENTUM_NEUTRAL"

        return ValidationResult.success(
            layer_name=_LAYER_NAME,
            reason=reason,
            score=0.0,
            data={
                "alignment": result.alignment.value,
                "confidence_boost_eligible": result.confidence_boost_eligible,
                "ema20": result.ema20,
                "ema50": result.ema50,
            },
        )
