"""
Dedicated exceptions for the final strategy pipeline.
"""

from typing import Optional


class StrategyPipelineError(Exception):
    """
    Base exception for strategy-pipeline failures.

    Carries structured context (layer_name, symbol, reason) instead of
    exposing raw internal exception text, so pipeline failures never
    leak full HTTP payloads, secrets, tokens, or account data.
    """

    def __init__(
        self,
        layer_name: str,
        symbol: str,
        reason: str,
        original_exception: Optional[BaseException] = None,
    ) -> None:
        self.layer_name = layer_name
        self.symbol = symbol
        self.reason = reason
        self.original_exception = original_exception
        super().__init__(f"[{layer_name}] {symbol}: {reason}")


class PipelineInputError(StrategyPipelineError):
    """Raised when pipeline input parameters (symbol, balance, counts) are invalid."""


class PipelineStageError(StrategyPipelineError):
    """Raised when a pipeline stage encounters an unexpected technical failure."""


class PipelineDataUnavailableError(StrategyPipelineError):
    """Raised when required market data or calculated data is unavailable."""
