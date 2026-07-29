"""
Validates alignment with BTC market direction.
"""

from app.market_structure.results import HigherTimeframeBias, HigherTimeframeBiasResult, MarketStructureResult, TrendDirection
from app.models.validation_result import ValidationResult

from app.validators.results import BTCAlignmentResult, BTCAlignmentStatus

_LAYER_NAME = "BTC_ALIGNMENT"

_BUY_TREND = TrendDirection.BULLISH
_SELL_TREND = TrendDirection.BEARISH
_BUY_BIAS = HigherTimeframeBias.BULLISH
_SELL_BIAS = HigherTimeframeBias.BEARISH


class BTCAlignmentValidator:
    """
    Validates that an altcoin's expected trade direction is aligned with
    BTC's own market structure trend and higher-timeframe bias.

    This validator is always applied, including when the symbol under
    evaluation is BTC-USDT itself; alignment is never skipped silently.
    """

    def evaluate(
        self,
        altcoin_symbol: str,
        expected_direction: str,
        btc_structure: MarketStructureResult,
        btc_htf_bias: HigherTimeframeBiasResult,
    ) -> BTCAlignmentResult:
        """
        Evaluate whether BTC's trend and higher-timeframe bias support
        `expected_direction`.

        Mixed, RANGE, or UNKNOWN BTC structure/bias always yields
        status=UNKNOWN, aligned=False; a clear opposite-direction trend
        or bias yields status=CONFLICT, aligned=False.
        """
        btc_trend = btc_structure.trend_direction
        btc_bias = btc_htf_bias.final_bias

        if expected_direction == "BUY":
            required_trend = _BUY_TREND
            required_bias = _BUY_BIAS
            opposite_trend = _SELL_TREND
            opposite_bias = _SELL_BIAS
        elif expected_direction == "SELL":
            required_trend = _SELL_TREND
            required_bias = _SELL_BIAS
            opposite_trend = _BUY_TREND
            opposite_bias = _BUY_BIAS
        else:
            return BTCAlignmentResult(
                altcoin_symbol=altcoin_symbol,
                expected_direction=expected_direction,
                btc_trend=btc_trend.value,
                btc_htf_bias=btc_bias.value,
                status=BTCAlignmentStatus.UNKNOWN,
                aligned=False,
                reason=f"Unrecognized expected_direction '{expected_direction}'.",
            )

        if btc_trend not in (TrendDirection.BULLISH, TrendDirection.BEARISH) or btc_bias not in (
            HigherTimeframeBias.BULLISH,
            HigherTimeframeBias.BEARISH,
        ):
            return BTCAlignmentResult(
                altcoin_symbol=altcoin_symbol,
                expected_direction=expected_direction,
                btc_trend=btc_trend.value,
                btc_htf_bias=btc_bias.value,
                status=BTCAlignmentStatus.UNKNOWN,
                aligned=False,
                reason="BTC structure trend or higher-timeframe bias is not clearly directional.",
            )

        if btc_trend == opposite_trend or btc_bias == opposite_bias:
            return BTCAlignmentResult(
                altcoin_symbol=altcoin_symbol,
                expected_direction=expected_direction,
                btc_trend=btc_trend.value,
                btc_htf_bias=btc_bias.value,
                status=BTCAlignmentStatus.CONFLICT,
                aligned=False,
                reason="BTC trend or higher-timeframe bias conflicts with the expected direction.",
            )

        if btc_trend == required_trend and btc_bias == required_bias:
            return BTCAlignmentResult(
                altcoin_symbol=altcoin_symbol,
                expected_direction=expected_direction,
                btc_trend=btc_trend.value,
                btc_htf_bias=btc_bias.value,
                status=BTCAlignmentStatus.ALIGNED,
                aligned=True,
                reason="BTC trend and higher-timeframe bias both support the expected direction.",
            )

        return BTCAlignmentResult(
            altcoin_symbol=altcoin_symbol,
            expected_direction=expected_direction,
            btc_trend=btc_trend.value,
            btc_htf_bias=btc_bias.value,
            status=BTCAlignmentStatus.UNKNOWN,
            aligned=False,
            reason="BTC trend and higher-timeframe bias disagree with each other.",
        )

    def validate(
        self,
        altcoin_symbol: str,
        expected_direction: str,
        btc_structure: MarketStructureResult,
        btc_htf_bias: HigherTimeframeBiasResult,
    ) -> ValidationResult:
        """
        Validate BTC alignment; passes only when `aligned` is True.

        Failure codes:
            - BTC_ALIGNMENT_DATA_MISSING
            - BTC_DIRECTION_UNKNOWN
            - BTC_DIRECTION_CONFLICT
            - BTC_HTF_BIAS_CONFLICT
        """
        result = self.evaluate(altcoin_symbol, expected_direction, btc_structure, btc_htf_bias)

        if result.aligned:
            return ValidationResult.success(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                score=0.0,
            )

        if result.btc_trend is None or result.btc_htf_bias is None:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="BTC structure or higher-timeframe bias data is missing.",
                rejection_code="BTC_ALIGNMENT_DATA_MISSING",
                score=0.0,
            )

        if result.status == BTCAlignmentStatus.UNKNOWN:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="BTC_DIRECTION_UNKNOWN",
                score=0.0,
            )

        btc_trend_conflicts = (
            expected_direction == "BUY" and result.btc_trend == _SELL_TREND.value
        ) or (expected_direction == "SELL" and result.btc_trend == _BUY_TREND.value)
        if btc_trend_conflicts:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="BTC_DIRECTION_CONFLICT",
                score=0.0,
            )

        return ValidationResult.failure(
            layer_name=_LAYER_NAME,
            reason=result.reason,
            rejection_code="BTC_HTF_BIAS_CONFLICT",
            score=0.0,
        )
