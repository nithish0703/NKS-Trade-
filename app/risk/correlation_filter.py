"""
Filters trades based on cross-pair correlation exposure.
"""

import math
from typing import Mapping, Sequence

from app.models.candle import Candle

from app.risk.results import CorrelationResult, CorrelationStatus
from app.risk.stop_loss import RiskCalculationError


class CorrelationFilter:
    """
    Evaluates whether a candidate symbol's price returns are too highly
    correlated with any currently active position, to avoid opening
    highly correlated simultaneous positions.
    """

    def __init__(self, maximum_allowed_correlation: float, minimum_observations: int) -> None:
        if not (0 <= maximum_allowed_correlation <= 1):
            raise RiskCalculationError(
                f"maximum_allowed_correlation must be between 0 and 1, got "
                f"{maximum_allowed_correlation}."
            )
        if minimum_observations <= 0:
            raise RiskCalculationError(
                f"minimum_observations must be positive, got {minimum_observations}."
            )

        self._maximum_allowed_correlation = maximum_allowed_correlation
        self._minimum_observations = minimum_observations

    def calculate_return_series(self, candles: Sequence[Candle]) -> list[float]:
        """Calculate close-to-close percentage returns from a candle sequence."""
        returns: list[float] = []
        for previous, current in zip(candles, candles[1:]):
            if previous.close == 0:
                continue
            returns.append((current.close - previous.close) / previous.close)
        return returns

    def calculate_correlation(
        self, first_returns: Sequence[float], second_returns: Sequence[float]
    ) -> float:
        """
        Calculate the Pearson correlation coefficient between two return
        series.

        Raises:
            RiskCalculationError: If lengths differ, there are too few
                observations, or either series has zero variance.
        """
        if len(first_returns) != len(second_returns):
            raise RiskCalculationError("Return series lengths must match.")
        if len(first_returns) < self._minimum_observations:
            raise RiskCalculationError(
                f"At least {self._minimum_observations} observations are required, "
                f"got {len(first_returns)}."
            )

        n = len(first_returns)
        mean_x = sum(first_returns) / n
        mean_y = sum(second_returns) / n

        covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(first_returns, second_returns))
        variance_x = sum((x - mean_x) ** 2 for x in first_returns)
        variance_y = sum((y - mean_y) ** 2 for y in second_returns)

        if variance_x == 0 or variance_y == 0:
            raise RiskCalculationError("Cannot calculate correlation for a zero-variance series.")

        correlation = covariance / math.sqrt(variance_x * variance_y)
        return max(-1.0, min(1.0, correlation))

    def evaluate(
        self,
        candidate_symbol: str,
        candidate_candles: Sequence[Candle],
        active_position_candles: Mapping[str, Sequence[Candle]],
    ) -> CorrelationResult:
        """
        Evaluate correlation between the candidate symbol and every
        active position's symbol.

        Returns acceptable=True with no active symbols when there are no
        active positions. Returns status=DATA_MISSING when overlapping
        observations are insufficient for any comparison and no
        acceptable comparison could be made. Never fabricates a
        correlation value.
        """
        active_symbols = list(active_position_candles.keys())

        if not active_symbols:
            return CorrelationResult(
                candidate_symbol=candidate_symbol,
                active_symbols=[],
                maximum_allowed_correlation=self._maximum_allowed_correlation,
                observed_correlations={},
                status=CorrelationStatus.ACCEPTABLE,
                acceptable=True,
                reason="CORRELATION_ACCEPTABLE",
            )

        candidate_returns = self.calculate_return_series(candidate_candles)

        observed: dict[str, float | None] = {}
        any_data_available = False
        max_abs_correlation = 0.0
        offending_symbol = None

        for symbol, candles in active_position_candles.items():
            other_returns = self.calculate_return_series(candles)
            aligned_length = min(len(candidate_returns), len(other_returns))

            if aligned_length < self._minimum_observations:
                observed[symbol] = None
                continue

            aligned_candidate = candidate_returns[-aligned_length:]
            aligned_other = other_returns[-aligned_length:]

            try:
                correlation = self.calculate_correlation(aligned_candidate, aligned_other)
            except RiskCalculationError:
                observed[symbol] = None
                continue

            observed[symbol] = correlation
            any_data_available = True

            if abs(correlation) > max_abs_correlation:
                max_abs_correlation = abs(correlation)
                offending_symbol = symbol

        if not any_data_available:
            return CorrelationResult(
                candidate_symbol=candidate_symbol,
                active_symbols=active_symbols,
                maximum_allowed_correlation=self._maximum_allowed_correlation,
                observed_correlations=observed,
                status=CorrelationStatus.DATA_MISSING,
                acceptable=False,
                reason="CORRELATION_DATA_MISSING",
            )

        if max_abs_correlation > self._maximum_allowed_correlation:
            return CorrelationResult(
                candidate_symbol=candidate_symbol,
                active_symbols=active_symbols,
                maximum_allowed_correlation=self._maximum_allowed_correlation,
                observed_correlations=observed,
                status=CorrelationStatus.TOO_HIGH,
                acceptable=False,
                reason=f"CORRELATION_TOO_HIGH: {offending_symbol} exceeds the maximum allowed correlation.",
            )

        return CorrelationResult(
            candidate_symbol=candidate_symbol,
            active_symbols=active_symbols,
            maximum_allowed_correlation=self._maximum_allowed_correlation,
            observed_correlations=observed,
            status=CorrelationStatus.ACCEPTABLE,
            acceptable=True,
            reason="CORRELATION_ACCEPTABLE",
        )
