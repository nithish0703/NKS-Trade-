"""
Combines Premium/Discount dealing-range validation and retest
confirmation into a single zone-setup confirmation result.
"""

from datetime import datetime
from typing import Any, Optional, Sequence

from pydantic import BaseModel, ConfigDict

from app.market_structure.results import SwingPoint
from app.models.candle import Candle
from app.models.trade_zone import TradeZone
from app.models.validation_result import ValidationResult
from app.validators.premium_discount import PremiumDiscountValidator
from app.validators.retest_confirmation import RetestConfirmationValidator

from app.zones.dealing_range import DealingRangeCalculator, DealingRangeResult
from app.zones.retest_confirmation import RetestConfirmationDetector
from app.zones.retest_results import RetestResult


class ZoneSetupConfirmationResult(BaseModel):
    """
    Aggregated result of Premium/Discount and retest-confirmation
    validation for a single selected entry zone.

    This model does not calculate entry price, stop loss, take profit,
    risk-reward, confidence score, or signal fields.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    expected_direction: str
    selected_zone: TradeZone
    dealing_range: DealingRangeResult
    premium_discount_validation: ValidationResult
    retest: Optional[RetestResult] = None
    retest_validation: Optional[ValidationResult] = None
    confirmed: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None


class ZoneSetupConfirmationCalculator:
    """
    Coordinates dealing-range calculation, Premium/Discount validation,
    retest detection, and retest validation into a single confirmed/not
    confirmed setup result.
    """

    def __init__(
        self,
        dealing_range_calculator: DealingRangeCalculator,
        premium_discount_validator: PremiumDiscountValidator,
        retest_confirmation_detector: RetestConfirmationDetector,
        retest_confirmation_validator: RetestConfirmationValidator,
    ) -> None:
        self._dealing_range_calculator = dealing_range_calculator
        self._premium_discount_validator = premium_discount_validator
        self._retest_confirmation_detector = retest_confirmation_detector
        self._retest_confirmation_validator = retest_confirmation_validator

    def calculate(
        self,
        candles: Sequence[Candle],
        swings: Sequence[SwingPoint],
        selected_zone: TradeZone,
        expected_direction: str,
        volume_ema_values: Sequence[Optional[float]],
        current_price: float,
        current_time_utc: datetime,
    ) -> ZoneSetupConfirmationResult:
        """
        Calculate the full Premium/Discount + retest confirmation result
        for `selected_zone`.

        Premium/Discount is evaluated first; if it fails, retest
        detection is skipped entirely and `confirmed=False` is returned
        immediately (never entering immediately after BOS/MSS/CHoCH
        without first satisfying both a valid dealing-range position and
        a confirmed retest).
        """
        symbol = selected_zone.symbol
        timeframe = selected_zone.timeframe

        dealing_range = self._dealing_range_calculator.calculate(
            swings, current_price, expected_direction
        )
        premium_discount_validation = self._premium_discount_validator.validate(
            dealing_range, expected_direction
        )

        if not premium_discount_validation.passed:
            return ZoneSetupConfirmationResult(
                symbol=symbol,
                timeframe=timeframe,
                expected_direction=expected_direction,
                selected_zone=selected_zone,
                dealing_range=dealing_range,
                premium_discount_validation=premium_discount_validation,
                retest=None,
                retest_validation=None,
                confirmed=False,
                reason="Premium/Discount validation failed; retest was not evaluated.",
            )

        retest = self._retest_confirmation_detector.detect_latest_confirmed(
            candles, selected_zone, volume_ema_values, current_time_utc
        )
        retest_validation = self._retest_confirmation_validator.validate(
            retest, expected_direction, selected_zone
        )

        confirmed = premium_discount_validation.passed and retest_validation.passed

        reason = (
            "Setup confirmed: valid dealing-range position and confirmed retest."
            if confirmed
            else "Retest validation failed."
        )

        return ZoneSetupConfirmationResult(
            symbol=symbol,
            timeframe=timeframe,
            expected_direction=expected_direction,
            selected_zone=selected_zone,
            dealing_range=dealing_range,
            premium_discount_validation=premium_discount_validation,
            retest=retest,
            retest_validation=retest_validation,
            confirmed=confirmed,
            reason=reason,
        )
