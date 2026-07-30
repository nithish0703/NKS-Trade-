"""
Top-level confidence-calculation entry point combining the input
adapter and the scoring engine.
"""

from app.models.validation_result import ValidationResult

from app.scoring.input_adapter import ConfidenceInputAdapter
from app.scoring.results import ConfidenceScoreResult
from app.scoring.score_engine import ConfidenceScoringEngine


class ConfidenceCalculator:
    """
    Coordinates building the scoring-layer validation map and running
    the confidence scoring engine. Does not modify validation results,
    generate a signal, or persist/publish anything.
    """

    def __init__(
        self,
        input_adapter: ConfidenceInputAdapter,
        scoring_engine: ConfidenceScoringEngine,
    ) -> None:
        self._input_adapter = input_adapter
        self._scoring_engine = scoring_engine

    def calculate(
        self,
        *,
        market_regime: ValidationResult,
        htf_bias: ValidationResult,
        liquidity_sweep: ValidationResult,
        structure_shift: ValidationResult,
        volume_confirmation: ValidationResult,
        entry_zone: ValidationResult,
        premium_discount: ValidationResult,
        retest_confirmation: ValidationResult,
        session_filter: ValidationResult,
        btc_alignment: ValidationResult,
        fake_breakout_filter: ValidationResult,
    ) -> ConfidenceScoreResult:
        """Build the validation map and calculate the confidence score."""
        validation_map = self._input_adapter.build_validation_map(
            market_regime=market_regime,
            htf_bias=htf_bias,
            liquidity_sweep=liquidity_sweep,
            structure_shift=structure_shift,
            volume_confirmation=volume_confirmation,
            entry_zone=entry_zone,
            premium_discount=premium_discount,
            retest_confirmation=retest_confirmation,
            session_filter=session_filter,
            btc_alignment=btc_alignment,
            fake_breakout_filter=fake_breakout_filter,
        )
        return self._scoring_engine.calculate(validation_map)
