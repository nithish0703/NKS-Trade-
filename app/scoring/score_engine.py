"""
Aggregates validator outcomes into a composite signal score.
"""

from typing import Mapping

from app.config.thresholds import (
    SCORE_BTC_ALIGNMENT,
    SCORE_ENTRY_ZONE,
    SCORE_FAKE_BREAKOUT,
    SCORE_HTF_BIAS,
    SCORE_LIQUIDITY_SWEEP,
    SCORE_MARKET_REGIME,
    SCORE_MAXIMUM_RAW,
    SCORE_PREMIUM_DISCOUNT,
    SCORE_RETEST_CONFIRMATION,
    SCORE_SESSION,
    SCORE_STRUCTURE_SHIFT,
    SCORE_VOLUME_CONFIRMATION,
)
from app.models.validation_result import ValidationResult

from app.scoring.classification import classify_confidence, is_publishable
from app.scoring.results import ConfidenceClassification, ConfidenceScoreResult, LayerScore


class ConfidenceScoringError(Exception):
    """Raised when confidence scoring cannot be performed."""


# Hard-mandatory layers: these are pipeline gates in strategy_engine.py.
# A signal only reaches scoring at all once every one of these has
# already passed, so they are always `passed=True` here in practice --
# but they still participate in the raw score like any other layer.
_HARD_LAYER_WEIGHTS: Mapping[str, float] = {
    "MARKET_REGIME": SCORE_MARKET_REGIME,
    "HTF_BIAS": SCORE_HTF_BIAS,
    "LIQUIDITY_SWEEP": SCORE_LIQUIDITY_SWEEP,
    "STRUCTURE_SHIFT": SCORE_STRUCTURE_SHIFT,
    "VOLUME_CONFIRMATION": SCORE_VOLUME_CONFIRMATION,
    "ENTRY_ZONE": SCORE_ENTRY_ZONE,
}

# Soft-scoring layers: failure never rejects a signal. A failure here
# only costs that layer's points -- it does not force IGNORE.
_SOFT_LAYER_WEIGHTS: Mapping[str, float] = {
    "PREMIUM_DISCOUNT": SCORE_PREMIUM_DISCOUNT,
    "RETEST_CONFIRMATION": SCORE_RETEST_CONFIRMATION,
    "SESSION_FILTER": SCORE_SESSION,
    "BTC_ALIGNMENT": SCORE_BTC_ALIGNMENT,
    "FAKE_BREAKOUT_FILTER": SCORE_FAKE_BREAKOUT,
}

_LAYER_WEIGHTS: Mapping[str, float] = {**_HARD_LAYER_WEIGHTS, **_SOFT_LAYER_WEIGHTS}

_HARD_LAYERS: tuple[str, ...] = tuple(_HARD_LAYER_WEIGHTS.keys())
_SOFT_LAYERS: tuple[str, ...] = tuple(_SOFT_LAYER_WEIGHTS.keys())
_ALL_LAYERS: tuple[str, ...] = _HARD_LAYERS + _SOFT_LAYERS

if sum(_LAYER_WEIGHTS.values()) != SCORE_MAXIMUM_RAW:
    raise ConfidenceScoringError(
        f"Configured scoring layer weights must sum to {SCORE_MAXIMUM_RAW}, "
        f"got {sum(_LAYER_WEIGHTS.values())}."
    )


class ConfidenceScoringEngine:
    """
    Aggregates already-computed ValidationResult objects into a raw
    score out of 120, normalizes it to 100, and classifies the setup.

    Does not recalculate any validation or strategy logic; it only
    reads `passed` from each supplied ValidationResult and awards the
    full configured points for that layer, or zero.
    """

    def calculate(self, validation_results: Mapping[str, ValidationResult]) -> ConfidenceScoreResult:
        """
        Calculate the confidence score from a mapping of layer name to
        ValidationResult.

        The 6 hard layers (MARKET_REGIME, HTF_BIAS, LIQUIDITY_SWEEP,
        STRUCTURE_SHIFT, VOLUME_CONFIRMATION, ENTRY_ZONE) are pipeline
        gates: by the time scoring runs, the calling pipeline has
        already rejected any setup where one of them failed, so they
        are expected to always be `passed=True` here. If one is
        somehow still failed, classification is forced to IGNORE.

        The 5 soft layers (PREMIUM_DISCOUNT, RETEST_CONFIRMATION,
        SESSION_FILTER, BTC_ALIGNMENT, FAKE_BREAKOUT_FILTER) never
        force IGNORE on failure -- a failure only costs that layer's
        points, and classification is driven purely by the resulting
        normalized score.

        Raises:
            ConfidenceScoringError: If any required scoring layer is
                missing from `validation_results`, or a supplied result's
                `layer_name` does not match its mapping key.
        """
        missing_layers = [layer for layer in _ALL_LAYERS if layer not in validation_results]
        if missing_layers:
            raise ConfidenceScoringError(
                f"Missing required scoring layer(s): {', '.join(missing_layers)}."
            )

        layer_scores: list[LayerScore] = []
        failed_mandatory_layers: list[str] = []
        raw_score = 0.0

        for layer_name in _ALL_LAYERS:
            result = validation_results[layer_name]
            if result.layer_name != layer_name:
                raise ConfidenceScoringError(
                    f"ValidationResult for '{layer_name}' has mismatched layer_name "
                    f"'{result.layer_name}'."
                )

            is_hard_layer = layer_name in _HARD_LAYERS
            maximum_points = _LAYER_WEIGHTS[layer_name]
            awarded_points = maximum_points if result.passed else 0.0
            raw_score += awarded_points

            if is_hard_layer and not result.passed:
                failed_mandatory_layers.append(layer_name)

            layer_scores.append(
                LayerScore(
                    layer_name=layer_name,
                    maximum_points=maximum_points,
                    awarded_points=awarded_points,
                    passed=result.passed,
                    mandatory=is_hard_layer,
                    reason=result.reason,
                )
            )

        mandatory_layers_passed = not failed_mandatory_layers
        normalized_score = round((raw_score / SCORE_MAXIMUM_RAW) * 100, 2)

        if not mandatory_layers_passed:
            classification = ConfidenceClassification.IGNORE
            publishable = False
            reason = "MANDATORY_LAYER_FAILED"
        else:
            classification = classify_confidence(normalized_score)
            publishable = is_publishable(classification)
            if classification == ConfidenceClassification.PREMIUM:
                reason = "PREMIUM_CONFIDENCE"
            elif classification == ConfidenceClassification.STRONG:
                reason = "STRONG_CONFIDENCE"
            elif classification == ConfidenceClassification.MEDIUM:
                reason = "MEDIUM_INTERNAL_ONLY"
            else:
                reason = "IGNORE_LOW_CONFIDENCE"

        return ConfidenceScoreResult(
            raw_score=raw_score,
            maximum_raw_score=SCORE_MAXIMUM_RAW,
            normalized_score=normalized_score,
            classification=classification,
            publishable=publishable,
            mandatory_layers_passed=mandatory_layers_passed,
            layer_scores=layer_scores,
            failed_mandatory_layers=failed_mandatory_layers,
            reason=reason,
        )
