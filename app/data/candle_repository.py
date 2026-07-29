"""
Repository for storing and retrieving candle data.

This is an in-memory repository only; no database persistence is
implemented at this stage.
"""

from typing import Optional

from app.data.data_validator import validate_candle_sequence
from app.models.candle import Candle

DEFAULT_MAX_CANDLES_PER_KEY = 1000


class CandleRepository:
    """
    In-memory store of Candle objects keyed by (symbol, timeframe).

    Candles are always kept deduplicated by timestamp and sorted in
    ascending chronological order.
    """

    def __init__(self, max_candles_per_key: int = DEFAULT_MAX_CANDLES_PER_KEY) -> None:
        if max_candles_per_key <= 0:
            raise ValueError(
                f"max_candles_per_key must be greater than zero, "
                f"got {max_candles_per_key}."
            )
        self._max_candles_per_key = max_candles_per_key
        self._store: dict[tuple[str, str], list[Candle]] = {}

    def save_candles(self, symbol: str, timeframe: str, candles: list[Candle]) -> None:
        """
        Merge new candles into the repository for (symbol, timeframe).

        New candles replace existing candles that share the same
        timestamp. The resulting sequence is deduplicated, sorted in
        ascending order, and truncated to the configured maximum size,
        keeping only the most recent candles.
        """
        validate_candle_sequence(candles)

        key = (symbol, timeframe)
        existing = self._store.get(key, [])

        merged_by_timestamp: dict = {candle.timestamp: candle for candle in existing}
        for candle in candles:
            merged_by_timestamp[candle.timestamp] = candle

        merged = sorted(merged_by_timestamp.values(), key=lambda c: c.timestamp)

        if len(merged) > self._max_candles_per_key:
            merged = merged[-self._max_candles_per_key :]

        self._store[key] = merged

    def get_candles(
        self, symbol: str, timeframe: str, limit: Optional[int] = None
    ) -> list[Candle]:
        """
        Return a copy of stored candles for (symbol, timeframe) in
        ascending chronological order.

        If `limit` is provided, only the latest `limit` candles are
        returned.

        Raises:
            ValueError: If limit is provided and is not positive.
        """
        if limit is not None and limit <= 0:
            raise ValueError(f"limit must be greater than zero, got {limit}.")

        candles = self._store.get((symbol, timeframe), [])
        if limit is not None:
            candles = candles[-limit:]
        return list(candles)

    def get_latest_candle(self, symbol: str, timeframe: str) -> Optional[Candle]:
        """Return the most recent candle for (symbol, timeframe), or None."""
        candles = self._store.get((symbol, timeframe))
        if not candles:
            return None
        return candles[-1]

    def has_data(self, symbol: str, timeframe: str) -> bool:
        """Return whether any candles are stored for (symbol, timeframe)."""
        return bool(self._store.get((symbol, timeframe)))

    def clear(
        self, symbol: Optional[str] = None, timeframe: Optional[str] = None
    ) -> None:
        """
        Clear stored candle data.

        Behavior:
            - No arguments: clear the entire repository.
            - symbol only: clear all timeframes stored for that symbol.
            - symbol and timeframe: clear that exact dataset.
            - timeframe only (without symbol): rejected as ambiguous.

        Raises:
            ValueError: If timeframe is provided without a symbol.
        """
        if timeframe is not None and symbol is None:
            raise ValueError(
                "Clearing by timeframe alone is ambiguous; a symbol must be provided."
            )

        if symbol is None and timeframe is None:
            self._store.clear()
            return

        if symbol is not None and timeframe is not None:
            self._store.pop((symbol, timeframe), None)
            return

        # symbol is not None and timeframe is None: clear all timeframes for symbol.
        keys_to_remove = [key for key in self._store if key[0] == symbol]
        for key in keys_to_remove:
            del self._store[key]
