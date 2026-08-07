"""
Computes stop loss levels for a trade setup.
"""

import logging
from typing import Optional

from app.liquidity.results import LiquiditySweepResult, SweepDirection
from app.models.trade_zone import TradeZone

from app.risk.results import StopLossCandidate, StopLossResult, StopLossSource

_logger = logging.getLogger(__name__)

# Safety priority among structurally valid, volatility-approved
# candidates: lower number = safer / more structurally certain.
# LIQUIDITY_SWEEP is the level price already proved by sweeping it, so
# a re-break there is the most credible invalidation signal.
# ENTRY_ZONE is the boundary of the zone the trade depends on holding.
# ATR carries no structural meaning at all -- it is purely a volatility
# fallback -- so it is used only when no structural candidate survives
# both structure validation and the ATR volatility band.
_SOURCE_SAFETY_PRIORITY = {
    StopLossSource.LIQUIDITY_SWEEP: 1,
    StopLossSource.ENTRY_ZONE: 2,
    StopLossSource.ATR: 3,
}


class RiskCalculationError(Exception):
    """Raised when risk-management calculations cannot be performed."""


class DynamicStopLossCalculator:
    """
    Generates and selects a dynamic stop-loss level from exactly three
    candidate sources: ATR distance, the selected entry zone boundary,
    and the supporting liquidity-sweep extreme. Never uses a fixed
    percentage stop.

    Selection does NOT simply pick the candidate closest to entry.
    Instead it selects the safest structural stop: among candidates
    that pass BOTH structure validation (correct side of entry) AND an
    ATR-based volatility requirement (distance is between
    min_atr_multiplier and max_atr_multiplier times ATR), it prefers
    the more structurally significant source first (liquidity sweep >
    entry zone > raw ATR), then the widest (safest, most breathing
    room) candidate within that source tier. A candidate tighter than
    the ATR volatility floor is exactly the kind of stop that gets hit
    by ordinary noise/wicks and produces a "stopped out, then price
    went to target anyway" false signal, so it is excluded rather than
    picked just because it is nearest to entry.
    """

    def __init__(
        self,
        atr_multiplier: float,
        structural_buffer_ratio: float,
        min_atr_multiplier: Optional[float] = None,
        max_atr_multiplier: Optional[float] = None,
    ) -> None:
        if atr_multiplier <= 0:
            raise RiskCalculationError(
                f"atr_multiplier must be positive, got {atr_multiplier}."
            )
        if structural_buffer_ratio < 0:
            raise RiskCalculationError(
                f"structural_buffer_ratio cannot be negative, got {structural_buffer_ratio}."
            )

        # Imported here (rather than at module scope) so this module's
        # only hard compile-time dependency on app.config is a default
        # fallback -- callers can still fully override the volatility
        # band via constructor arguments (e.g. in tests).
        from app.config.thresholds import MAX_STOP_ATR_MULTIPLIER, MIN_STOP_ATR_MULTIPLIER

        resolved_min = MIN_STOP_ATR_MULTIPLIER if min_atr_multiplier is None else min_atr_multiplier
        resolved_max = MAX_STOP_ATR_MULTIPLIER if max_atr_multiplier is None else max_atr_multiplier

        if resolved_min < 0:
            raise RiskCalculationError(
                f"min_atr_multiplier cannot be negative, got {resolved_min}."
            )
        if resolved_max <= resolved_min:
            raise RiskCalculationError(
                f"max_atr_multiplier ({resolved_max}) must be greater than "
                f"min_atr_multiplier ({resolved_min})."
            )

        self._atr_multiplier = atr_multiplier
        self._structural_buffer_ratio = structural_buffer_ratio
        self._min_atr_multiplier = resolved_min
        self._max_atr_multiplier = resolved_max

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
        rejects any candidate on the wrong side of entry, then narrows
        to candidates whose distance from entry also falls within the
        ATR volatility band [min_atr_multiplier, max_atr_multiplier] *
        atr. Among those, selects the safest structural stop (see class
        docstring). Returns an invalid result (never a fabricated stop)
        when no candidate satisfies both structure and volatility
        requirements.
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
        structurally_valid = [c for c in candidates if c.valid_for_direction]

        if not structurally_valid:
            self._log_analysis(direction, entry_price, atr, candidates, selected=None)
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

        atr_floor = atr * self._min_atr_multiplier
        atr_ceiling = atr * self._max_atr_multiplier

        volatility_valid = [
            c for c in structurally_valid if atr_floor <= c.distance_from_entry <= atr_ceiling
        ]

        if not volatility_valid:
            self._log_analysis(direction, entry_price, atr, candidates, selected=None)
            all_too_tight = all(c.distance_from_entry < atr_floor for c in structurally_valid)
            all_too_wide = all(c.distance_from_entry > atr_ceiling for c in structurally_valid)
            if all_too_tight:
                reason = (
                    "Every structurally valid stop-loss candidate is tighter than the "
                    f"minimum ATR volatility floor ({atr_floor:.8f}); rejecting to avoid a "
                    "stop that ordinary noise would hit before the setup is actually invalidated."
                )
                rejection_code = "STOP_TOO_TIGHT_FOR_VOLATILITY"
            elif all_too_wide:
                reason = (
                    "Every structurally valid stop-loss candidate is wider than the "
                    f"maximum ATR volatility ceiling ({atr_ceiling:.8f}); rejecting an "
                    "unreasonably wide stop."
                )
                rejection_code = "STOP_TOO_WIDE_FOR_VOLATILITY"
            else:
                reason = (
                    "No structurally valid stop-loss candidate falls within the ATR "
                    f"volatility band [{atr_floor:.8f}, {atr_ceiling:.8f}]."
                )
                rejection_code = "NO_STOP_WITHIN_VOLATILITY_BAND"
            return StopLossResult(
                direction=direction,
                entry_price=entry_price,
                selected_stop_loss=None,
                selected_source=None,
                candidates=candidates,
                risk_distance=None,
                valid=False,
                reason=reason,
                metadata={
                    "rejection_code": rejection_code,
                    "atr_floor": atr_floor,
                    "atr_ceiling": atr_ceiling,
                },
            )

        # Safest structural stop: prefer the more structurally
        # significant source first (sweep > zone > ATR fallback), then
        # within the same source tier prefer the widest (safest)
        # distance from entry rather than the closest one. `c.price` is
        # the final tie-breaker for full determinism.
        selected = min(
            volatility_valid,
            key=lambda c: (
                _SOURCE_SAFETY_PRIORITY[c.source],
                -c.distance_from_entry,
                c.price,
            ),
        )

        self._log_analysis(direction, entry_price, atr, candidates, selected=selected)

        return StopLossResult(
            direction=direction,
            entry_price=entry_price,
            selected_stop_loss=selected.price,
            selected_source=selected.source,
            candidates=candidates,
            risk_distance=selected.distance_from_entry,
            valid=True,
            reason="VALID_DYNAMIC_STOP",
            metadata={"atr_floor": atr_floor, "atr_ceiling": atr_ceiling},
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
    def _log_analysis(
        direction: str,
        entry_price: float,
        atr: float,
        candidates: list[StopLossCandidate],
        *,
        selected: Optional[StopLossCandidate],
    ) -> None:
        """
        DEBUG-only audit trail of every candidate considered, why it was
        kept or dropped, and which one (if any) was selected. Purely
        observational -- never affects control flow -- and guarded so it
        costs nothing when DEBUG logging is disabled (the default).
        """
        if not _logger.isEnabledFor(logging.DEBUG):
            return

        lines = [
            "Stop Loss Analysis",
            f"Direction: {direction}",
            f"Entry: {entry_price}",
            f"ATR: {atr}",
        ]
        for candidate in candidates:
            lines.append(f"Source: {candidate.source.value}")
            lines.append(f"Price: {candidate.price}")
            lines.append(f"Distance: {candidate.distance_from_entry}")
            lines.append("SIDE_OK" if candidate.valid_for_direction else "WRONG_SIDE")

        if selected is not None:
            lines.append(f"Selected: {selected.source.value} @ {selected.price}")
            lines.append(
                "Reason: safest structural candidate within the ATR volatility band."
            )
        else:
            lines.append("Rejected: no candidate satisfies both structure and volatility requirements.")

        _logger.debug("\n".join(lines))

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
