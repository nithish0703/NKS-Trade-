"""
Filters setups based on active trading session.
"""

from datetime import datetime, time, timezone

from app.models.validation_result import ValidationResult

from app.validators.results import SessionValidationResult, TradingSession

_LAYER_NAME = "SESSION_FILTER"

_ASIA_START = time(0, 0)
_ASIA_END = time(8, 0)
_LONDON_START = time(8, 0)
_OVERLAP_START = time(13, 0)
_OVERLAP_END = time(16, 0)
_NEW_YORK_END = time(21, 0)


def _is_utc(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() == timezone.utc.utcoffset(value)


class SessionFilter:
    """
    Detects the active UTC trading session and validates whether trading
    is permitted, including the Asian-session exception requiring a
    strong BTC trend, a high ATR, and high volume.
    """

    def detect_session(self, detection_time_utc: datetime) -> TradingSession:
        """
        Detect the named UTC trading session for `detection_time_utc`
        using start-inclusive, end-exclusive boundaries.
        """
        current_time = detection_time_utc.timetz().replace(tzinfo=None)

        if _OVERLAP_START <= current_time < _OVERLAP_END:
            return TradingSession.LONDON_NEW_YORK_OVERLAP
        if _LONDON_START <= current_time < _OVERLAP_START:
            return TradingSession.LONDON
        if _OVERLAP_END <= current_time < _NEW_YORK_END:
            return TradingSession.NEW_YORK
        if _ASIA_START <= current_time < _ASIA_END:
            return TradingSession.ASIA
        return TradingSession.OUTSIDE_SUPPORTED_SESSION

    def evaluate(
        self,
        detection_time_utc: datetime,
        btc_trend_strong: bool,
        atr_high: bool,
        volume_high: bool,
    ) -> SessionValidationResult:
        """
        Evaluate whether trading is permitted for the detected session.

        London, New York, and the London/New York overlap are always
        allowed. Asia is allowed only when `btc_trend_strong`,
        `atr_high`, and `volume_high` are all True. All other times are
        not permitted.
        """
        if not _is_utc(detection_time_utc):
            raise ValueError("detection_time_utc must be timezone-aware UTC.")

        session = self.detect_session(detection_time_utc)

        london_active = session in (TradingSession.LONDON, TradingSession.LONDON_NEW_YORK_OVERLAP)
        new_york_active = session in (TradingSession.NEW_YORK, TradingSession.LONDON_NEW_YORK_OVERLAP)
        overlap_active = session == TradingSession.LONDON_NEW_YORK_OVERLAP

        asian_exception_required = session == TradingSession.ASIA
        asian_exception_passed = bool(btc_trend_strong and atr_high and volume_high)

        if session in (
            TradingSession.LONDON,
            TradingSession.NEW_YORK,
            TradingSession.LONDON_NEW_YORK_OVERLAP,
        ):
            allowed = True
            reason = f"{session.value} session is always permitted."
        elif session == TradingSession.ASIA:
            allowed = asian_exception_passed
            reason = (
                "Asian session permitted: BTC trend strong, ATR high, and volume high."
                if allowed
                else "Asian session not permitted: exception conditions not all satisfied."
            )
        else:
            allowed = False
            reason = "Outside all supported trading sessions."

        return SessionValidationResult(
            session=session,
            session_start_utc=None,
            session_end_utc=None,
            london_active=london_active,
            new_york_active=new_york_active,
            overlap_active=overlap_active,
            asian_exception_required=asian_exception_required,
            asian_exception_passed=asian_exception_passed,
            allowed=allowed,
            reason=reason,
        )

    def validate(
        self,
        detection_time_utc: datetime,
        btc_trend_strong: bool,
        atr_high: bool,
        volume_high: bool,
    ) -> ValidationResult:
        """
        Validate whether trading is permitted for the current session.

        Failure codes:
            - INVALID_SESSION_TIMESTAMP
            - ASIAN_SESSION_CONDITIONS_FAILED
            - UNSUPPORTED_TRADING_SESSION
        """
        if not _is_utc(detection_time_utc):
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="detection_time_utc must be timezone-aware UTC.",
                rejection_code="INVALID_SESSION_TIMESTAMP",
                score=0.0,
            )

        result = self.evaluate(detection_time_utc, btc_trend_strong, atr_high, volume_high)

        if result.allowed:
            return ValidationResult.success(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                score=0.0,
            )

        if result.session == TradingSession.ASIA:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="ASIAN_SESSION_CONDITIONS_FAILED",
                score=0.0,
            )

        return ValidationResult.failure(
            layer_name=_LAYER_NAME,
            reason=result.reason,
            rejection_code="UNSUPPORTED_TRADING_SESSION",
            score=0.0,
        )
