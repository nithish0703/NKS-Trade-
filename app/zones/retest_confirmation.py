"""
Detects confirmed retests of a selected trade zone.
"""

import hashlib
from datetime import datetime
from typing import Optional, Sequence

from app.indicators.candle_metrics import calculate_candle_metrics
from app.models.candle import Candle
from app.models.trade_zone import TradeZone, ZoneStatus

from app.zones.retest_results import RejectionCandleDirection, RetestResult, RetestStatus


class RetestCalculationError(Exception):
    """Raised when retest-confirmation calculations cannot be performed."""


def _make_retest_id(symbol: str, timeframe: str, zone_id: str, candle_index: int) -> str:
    """Generate a stable, deterministic retest ID."""
    raw = f"{symbol}|{timeframe}|{zone_id}|{candle_index}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class RetestConfirmationDetector:
    """
    Detects a confirmed retest of a selected FRESH trade zone: price must
    interact with the zone, produce a strong rejection candle, and be
    supported by volume confirmation.
    """

    def __init__(
        self,
        minimum_rejection_body_ratio: float,
        bullish_close_location_minimum: float,
        bearish_close_location_maximum: float,
        minimum_rejection_wick_ratio: float,
        maximum_confirmation_candles: int,
    ) -> None:
        for name, value in (
            ("minimum_rejection_body_ratio", minimum_rejection_body_ratio),
            ("bullish_close_location_minimum", bullish_close_location_minimum),
            ("bearish_close_location_maximum", bearish_close_location_maximum),
            ("minimum_rejection_wick_ratio", minimum_rejection_wick_ratio),
        ):
            if not (0 <= value <= 1):
                raise RetestCalculationError(f"{name} must be between 0 and 1, got {value}.")
        if maximum_confirmation_candles < 1:
            raise RetestCalculationError(
                f"maximum_confirmation_candles must be at least 1, got {maximum_confirmation_candles}."
            )

        self._minimum_rejection_body_ratio = minimum_rejection_body_ratio
        self._bullish_close_location_minimum = bullish_close_location_minimum
        self._bearish_close_location_maximum = bearish_close_location_maximum
        self._minimum_rejection_wick_ratio = minimum_rejection_wick_ratio
        self._maximum_confirmation_candles = maximum_confirmation_candles

    def detect(
        self,
        candles: Sequence[Candle],
        zone: TradeZone,
        volume_ema_values: Sequence[Optional[float]],
        evaluation_time_utc: Optional[datetime] = None,
    ) -> list[RetestResult]:
        """
        Detect retest candidates against `zone`.

        Only candles strictly after `zone.created_at` are ever inspected
        (the creation candle cannot confirm its own retest), and none at
        or after `evaluation_time_utc` are inspected. Returns REJECTED
        results with explicit reasons for failed candidates, and at most
        one CONFIRMED result — the earliest valid confirmation for a
        given zone interaction.

        Raises:
            RetestCalculationError: If volume_ema_values length does not
                match candles length.
        """
        if len(volume_ema_values) != len(candles):
            raise RetestCalculationError(
                f"volume_ema_values length ({len(volume_ema_values)}) must match "
                f"candles length ({len(candles)})."
            )

        results: list[RetestResult] = []

        if zone.mitigation_timestamp is not None or zone.status == ZoneStatus.MITIGATED:
            return [self._rejected(zone, "ZONE_ALREADY_MITIGATED", "Zone has already been mitigated.")]
        if zone.invalidation_timestamp is not None or zone.status == ZoneStatus.INVALIDATED:
            return [self._rejected(zone, "ZONE_INVALIDATED", "Zone has been invalidated.")]
        if zone.status != ZoneStatus.FRESH:
            return [self._rejected(zone, "ZONE_NOT_FRESH", "Zone is not FRESH.")]

        eligible = [
            (index, candle)
            for index, candle in enumerate(candles)
            if candle.symbol == zone.symbol
            and candle.timeframe == zone.timeframe
            and candle.timestamp > zone.created_at
            and (evaluation_time_utc is None or candle.timestamp <= evaluation_time_utc)
        ]

        if not eligible:
            return [self._rejected(zone, "ZONE_NOT_TOUCHED", "No eligible candles interact with the zone.")]

        first_interaction_position: Optional[int] = None
        for position, (_, candle) in enumerate(eligible):
            overlaps = candle.low <= zone.upper_price and candle.high >= zone.lower_price
            if overlaps:
                first_interaction_position = position
                break

        if first_interaction_position is None:
            return [self._rejected(zone, "ZONE_NOT_TOUCHED", "No candle overlapped the zone.")]

        window = eligible[
            first_interaction_position : first_interaction_position
            + 1
            + self._maximum_confirmation_candles
        ]
        outcome = self._evaluate_window(zone, window, volume_ema_values)
        return [outcome]

    def _evaluate_window(
        self,
        zone: TradeZone,
        window: list[tuple[int, Candle]],
        volume_ema_values: Sequence[Optional[float]],
    ) -> RetestResult:
        interaction_index, interaction_candle = window[0]

        if zone.direction not in ("BUY", "SELL"):
            return self._rejected(
                zone,
                "RETEST_DIRECTION_MISMATCH",
                "Zone direction is neither BUY nor SELL.",
                retest_candle=interaction_candle,
                retest_candle_index=interaction_index,
            )

        for candidate_index, candidate_candle in window:
            volume_ema = volume_ema_values[candidate_index]
            outcome = self._evaluate_rejection_candidate(
                zone, candidate_candle, candidate_index, volume_ema
            )
            if outcome.status == RetestStatus.CONFIRMED:
                return outcome

        # No candle in the window produced a confirmed rejection; report
        # the last evaluated candidate's rejection details, or a generic
        # window-expired rejection if the window only ever failed on the
        # rejection-candle check.
        last_index, last_candle = window[-1]
        return self._evaluate_rejection_candidate(
            zone, last_candle, last_index, volume_ema_values[last_index]
        )

    def _evaluate_rejection_candidate(
        self,
        zone: TradeZone,
        candle: Candle,
        candle_index: int,
        volume_ema: Optional[float],
    ) -> RetestResult:
        metrics = calculate_candle_metrics(candle)
        zone_midpoint = (zone.lower_price + zone.upper_price) / 2

        if zone.direction == "BUY":
            direction_ok = candle.is_bullish
            rejection_direction = RejectionCandleDirection.BULLISH if candle.is_bullish else None
            close_beyond_boundary = candle.close > zone.upper_price
            close_beyond_midpoint = candle.close > zone_midpoint
            close_location_ok = metrics.close_location_value >= self._bullish_close_location_minimum
            wick_ratio = metrics.lower_wick_ratio
        else:
            direction_ok = candle.is_bearish
            rejection_direction = RejectionCandleDirection.BEARISH if candle.is_bearish else None
            close_beyond_boundary = candle.close < zone.lower_price
            close_beyond_midpoint = candle.close < zone_midpoint
            close_location_ok = metrics.close_location_value <= self._bearish_close_location_maximum
            wick_ratio = metrics.upper_wick_ratio

        body_ratio_ok = metrics.body_ratio >= self._minimum_rejection_body_ratio
        wick_ok = wick_ratio >= self._minimum_rejection_wick_ratio or close_beyond_boundary

        volume_confirmed = volume_ema is not None and candle.volume > volume_ema
        volume_ratio = candle.volume / volume_ema if volume_ema else None

        rejection_candle_confirmed = (
            direction_ok
            and close_beyond_midpoint
            and body_ratio_ok
            and close_location_ok
            and wick_ok
        )

        common_kwargs = dict(
            symbol=zone.symbol,
            timeframe=zone.timeframe,
            zone=zone,
            retest_candle_timestamp=candle.timestamp,
            retest_candle_index=candle_index,
            interaction_price=candle.close,
            rejection_direction=rejection_direction,
            body_ratio=metrics.body_ratio,
            upper_wick_ratio=metrics.upper_wick_ratio,
            lower_wick_ratio=metrics.lower_wick_ratio,
            close_location_value=metrics.close_location_value,
            rejection_strength=metrics.body_ratio,
            volume=candle.volume,
            volume_ema=volume_ema,
            volume_ratio=volume_ratio,
            volume_confirmed=volume_confirmed,
            zone_interaction_confirmed=True,
        )

        if not direction_ok:
            return RetestResult(
                retest_id=_make_retest_id(zone.symbol, zone.timeframe, zone.zone_id, candle_index),
                rejection_candle_confirmed=False,
                status=RetestStatus.REJECTED,
                reason="Rejection candle direction does not oppose the zone side.",
                metadata={"rejection_code": "REJECTION_CANDLE_MISSING"},
                **common_kwargs,
            )

        if not body_ratio_ok:
            return RetestResult(
                retest_id=_make_retest_id(zone.symbol, zone.timeframe, zone.zone_id, candle_index),
                rejection_candle_confirmed=False,
                status=RetestStatus.REJECTED,
                reason="Rejection candle body ratio is below the configured minimum.",
                metadata={"rejection_code": "WEAK_REJECTION_BODY"},
                **common_kwargs,
            )

        if not close_location_ok or not close_beyond_midpoint:
            return RetestResult(
                retest_id=_make_retest_id(zone.symbol, zone.timeframe, zone.zone_id, candle_index),
                rejection_candle_confirmed=False,
                status=RetestStatus.REJECTED,
                reason="Rejection candle close location or midpoint break is too weak.",
                metadata={"rejection_code": "WEAK_REJECTION_CLOSE"},
                **common_kwargs,
            )

        if not wick_ok:
            return RetestResult(
                retest_id=_make_retest_id(zone.symbol, zone.timeframe, zone.zone_id, candle_index),
                rejection_candle_confirmed=False,
                status=RetestStatus.REJECTED,
                reason="Rejection candle lacks a meaningful rejection wick.",
                metadata={"rejection_code": "REJECTION_WICK_MISSING"},
                **common_kwargs,
            )

        if volume_ema is None:
            return RetestResult(
                retest_id=_make_retest_id(zone.symbol, zone.timeframe, zone.zone_id, candle_index),
                rejection_candle_confirmed=True,
                status=RetestStatus.REJECTED,
                reason="Volume EMA is unavailable; volume confirmation cannot be established.",
                metadata={"rejection_code": "RETEST_VOLUME_MISSING"},
                **common_kwargs,
            )

        if not volume_confirmed:
            return RetestResult(
                retest_id=_make_retest_id(zone.symbol, zone.timeframe, zone.zone_id, candle_index),
                rejection_candle_confirmed=True,
                status=RetestStatus.REJECTED,
                reason="Candle volume is not strictly greater than its volume EMA.",
                metadata={"rejection_code": "RETEST_VOLUME_NOT_CONFIRMED"},
                **common_kwargs,
            )

        return RetestResult(
            retest_id=_make_retest_id(zone.symbol, zone.timeframe, zone.zone_id, candle_index),
            rejection_candle_confirmed=True,
            status=RetestStatus.CONFIRMED,
            reason=f"Confirmed {zone.direction} retest with rejection candle and volume confirmation.",
            **common_kwargs,
        )

    @staticmethod
    def _rejected(
        zone: TradeZone,
        rejection_code: str,
        reason: str,
        retest_candle: Optional[Candle] = None,
        retest_candle_index: int = 0,
    ) -> RetestResult:
        timestamp = retest_candle.timestamp if retest_candle else zone.created_at
        interaction_price = retest_candle.close if retest_candle else zone.lower_price
        return RetestResult(
            retest_id=_make_retest_id(zone.symbol, zone.timeframe, zone.zone_id, retest_candle_index),
            symbol=zone.symbol,
            timeframe=zone.timeframe,
            zone=zone,
            retest_candle_timestamp=timestamp,
            retest_candle_index=retest_candle_index,
            interaction_price=interaction_price,
            rejection_direction=None,
            body_ratio=0.0,
            upper_wick_ratio=0.0,
            lower_wick_ratio=0.0,
            close_location_value=0.0,
            rejection_strength=0.0,
            volume=0.0,
            volume_ema=None,
            volume_ratio=None,
            volume_confirmed=False,
            zone_interaction_confirmed=False,
            rejection_candle_confirmed=False,
            status=RetestStatus.REJECTED,
            reason=reason,
            metadata={"rejection_code": rejection_code},
        )

    def detect_latest_confirmed(
        self,
        candles: Sequence[Candle],
        zone: TradeZone,
        volume_ema_values: Sequence[Optional[float]],
        evaluation_time_utc: Optional[datetime] = None,
    ) -> Optional[RetestResult]:
        """Return the latest CONFIRMED retest, or None if none exists."""
        results = self.detect(candles, zone, volume_ema_values, evaluation_time_utc)
        confirmed = [r for r in results if r.status == RetestStatus.CONFIRMED]
        if not confirmed:
            return None
        return max(confirmed, key=lambda r: r.retest_candle_timestamp)
