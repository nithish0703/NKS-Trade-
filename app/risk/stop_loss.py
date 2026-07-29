"""
Computes stop loss levels for a trade setup.
"""

from typing import Optional

from app.liquidity.results import LiquiditySweepResult, SweepDirection
from app.models.trade_zone import TradeZone

from app.risk.results import StopLossCandidate, StopLossResult, StopLossSource


class RiskCalculationError(Exception):
    """Raised when risk-management calculations cannot be performed."""


class DynamicStopLossCalculator:
    """
    Generates and selects a dynamic stop-loss level from exactly three
    candidate sources: ATR distance, the selected entry zone boundary,
    and the supporting liquidity-sweep extreme. Never uses a fixed
    percentage stop.
    """

    def __init__(self, atr_multiplier: float, structural_buffer_ratio: float) -> None:
        if atr_multiplier <= 0:
            raise RiskCalculationError(
                f"atr_multiplier must be positive, got {atr_multiplier}."
            )
        if structural_buffer_ratio < 0:
            raise RiskCalculationError(
                f"structural_buffer_ratio cannot be negative, got {structural_buffer_ratio}."
            )

        self._atr_multiplier = atr_multiplier
        self._structural_buffer_ratio = structural_buffer_ratio

    def calculate(
        self,
        direction: str,
        entry_price: float,
        atr: float,
        selected_zone: TradeZone,
        liquidity_sweep: LiquiditySweepResult,
    ) -> StopLossResult:
        """
        Calculate the dynamic stop loss for `direction` at `entry_price`.

        Generates exactly three candidates (ATR, zone, liquidity sweep),
        rejects any candidate on the wrong side of entry, and selects
        the safest valid candidate: the lowest for BUY, the highest for
        SELL. Returns an invalid result (never a fabricated stop) when
        no valid candidate exists.
        """
        if entry_price <= 0:
            return self._invalid_result(
                direction, entry_price, [], "Entry price must be positive.", "INVALID_ENTRY_PRICE"
            )
        if direction not in ("BUY", "SELL"):
            return self._invalid_result(
                direction, entry_price, [], f"Unrecognized direction '{direction}'.", "INVALID_ENTRY_PRICE"
            )

        if atr is None or atr <= 0:
            return self._invalid_result(
                direction, entry_price, [], "ATR must be positive.", "INVALID_ATR"
            )

        expected_zone_direction = "BUY" if direction == "BUY" else "SELL"
        if selected_zone.direction != expected_zone_direction:
            return self._invalid_result(
                direction,
                entry_price,
                [],
                "Selected zone direction does not match trade direction.",
                "ZONE_DIRECTION_MISMATCH",
            )

        expected_sweep_direction = (
            SweepDirection.BULLISH if direction == "BUY" else SweepDirection.BEARISH
        )
        if liquidity_sweep.direction != expected_sweep_direction:
            return self._invalid_result(
                direction,
                entry_price,
                [],
                "Liquidity sweep direction does not support the trade direction.",
                "SWEEP_DIRECTION_MISMATCH",
            )

        raw_candidates: list[Optional[StopLossCandidate]] = []

        if direction == "BUY":
            atr_price = entry_price - (atr * self._atr_multiplier)
            raw_candidates.append(self._make_candidate(StopLossSource.ATR, atr_price, entry_price, direction))

            zone_reference = selected_zone.lower_price
            zone_price = zone_reference - (zone_reference * self._structural_buffer_ratio)
            raw_candidates.append(
                self._make_candidate(StopLossSource.ENTRY_ZONE, zone_price, entry_price, direction)
            )

            sweep_reference = liquidity_sweep.sweep_price
            sweep_price = sweep_reference - (sweep_reference * self._structural_buffer_ratio)
            raw_candidates.append(
                self._make_candidate(StopLossSource.LIQUIDITY_SWEEP, sweep_price, entry_price, direction)
            )
        else:
            atr_price = entry_price + (atr * self._atr_multiplier)
            raw_candidates.append(self._make_candidate(StopLossSource.ATR, atr_price, entry_price, direction))

            zone_reference = selected_zone.upper_price
            zone_price = zone_reference + (zone_reference * self._structural_buffer_ratio)
            raw_candidates.append(
                self._make_candidate(StopLossSource.ENTRY_ZONE, zone_price, entry_price, direction)
            )

            sweep_reference = liquidity_sweep.sweep_price
            sweep_price = sweep_reference + (sweep_reference * self._structural_buffer_ratio)
            raw_candidates.append(
                self._make_candidate(StopLossSource.LIQUIDITY_SWEEP, sweep_price, entry_price, direction)
            )

        candidates: list[StopLossCandidate] = [c for c in raw_candidates if c is not None]
        valid_candidates = [c for c in candidates if c.valid_for_direction]

        if not valid_candidates:
            return StopLossResult(
                direction=direction,
                entry_price=entry_price,
                selected_stop_loss=None,
                selected_source=None,
                candidates=candidates,
                risk_distance=None,
                valid=False,
                reason="No valid stop-loss candidate exists on the correct side of entry.",
                metadata={"rejection_code": "NO_VALID_STOP_CANDIDATE"},
            )

        if direction == "BUY":
            selected = min(valid_candidates, key=lambda c: c.price)
        else:
            selected = max(valid_candidates, key=lambda c: c.price)

        return StopLossResult(
            direction=direction,
            entry_price=entry_price,
            selected_stop_loss=selected.price,
            selected_source=selected.source,
            candidates=candidates,
            risk_distance=selected.distance_from_entry,
            valid=True,
            reason="VALID_DYNAMIC_STOP",
        )

    @staticmethod
    def _make_candidate(
        source: StopLossSource, price: float, entry_price: float, direction: str
    ) -> Optional[StopLossCandidate]:
        # A non-positive candidate price cannot be represented by the
        # (positive-price) StopLossCandidate model and can never be a
        # valid stop; such a candidate is simply omitted from the
        # candidate list rather than fabricated.
        if price <= 0:
            return None

        if direction == "BUY":
            valid = price < entry_price
        else:
            valid = price > entry_price

        distance = abs(entry_price - price)
        reason = (
            f"{source.value} candidate is valid for {direction}."
            if valid
            else f"{source.value} candidate is on the wrong side of entry for {direction}."
        )
        return StopLossCandidate(
            source=source,
            price=price,
            distance_from_entry=distance,
            valid_for_direction=valid,
            reason=reason,
        )

    @staticmethod
    def _invalid_result(
        direction: str,
        entry_price: float,
        candidates: list[StopLossCandidate],
        reason: str,
        rejection_code: str,
    ) -> StopLossResult:
        safe_entry_price = entry_price if entry_price > 0 else 1.0
        return StopLossResult(
            direction=direction,
            entry_price=safe_entry_price,
            selected_stop_loss=None,
            selected_source=None,
            candidates=candidates,
            risk_distance=None,
            valid=False,
            reason=reason,
            metadata={"rejection_code": rejection_code},
        )
