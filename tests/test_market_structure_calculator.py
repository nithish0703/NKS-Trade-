"""
Unit tests for app.market_structure.calculator.MarketStructureCalculator.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.market_structure.calculator import MarketStructureCalculator
from app.market_structure.results import MarketStructureResult
from app.market_structure.swing_detector import (
    MarketStructureCalculationError,
    SwingDetector,
)
from app.market_structure.trend_structure import TrendStructureAnalyzer
from app.models.candle import Candle

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_zigzag_candles(count: int, timeframe: str, symbol="BTC-USDT") -> list[Candle]:
    candles = []
    for i in range(count):
        cycle_position = i % 20
        base = 100 + (cycle_position if cycle_position <= 10 else 20 - cycle_position) * 2
        base += i * 0.1
        high = base + 3
        low = base - 3
        close = base
        candles.append(
            Candle(
                timestamp=UTC_NOW + timedelta(hours=i),
                open=close,
                high=high,
                low=low,
                close=close,
                volume=100.0,
                symbol=symbol,
                timeframe=timeframe,
            )
        )
    return candles


def _make_calculator() -> MarketStructureCalculator:
    return MarketStructureCalculator(
        swing_detector=SwingDetector(
            left_strength=3, right_strength=3, equality_tolerance=0.001
        ),
        trend_structure_analyzer=TrendStructureAnalyzer(
            equality_tolerance=0.001, minimum_confirmed_swings=4
        ),
    )


class TestCalculateTimeframe:
    def test_complete_single_timeframe_structure_calculation(self):
        candles = _make_zigzag_candles(60, "4h")
        result = _make_calculator().calculate_timeframe(candles)
        assert isinstance(result, MarketStructureResult)

    def test_empty_candles_rejected(self):
        with pytest.raises(MarketStructureCalculationError):
            _make_calculator().calculate_timeframe([])

    def test_candles_not_mutated(self):
        candles = _make_zigzag_candles(60, "4h")
        snapshot = [c.model_copy() for c in candles]
        _make_calculator().calculate_timeframe(candles)
        assert candles == snapshot

    def test_no_liquidity_bos_signal_risk_or_score_fields(self):
        candles = _make_zigzag_candles(60, "4h")
        result = _make_calculator().calculate_timeframe(candles)
        result_fields = set(type(result).model_fields.keys())
        forbidden = {
            "liquidity_sweep",
            "bos",
            "mss",
            "choch",
            "signal",
            "risk_reward_ratio",
            "confidence_score",
            "entry_price",
            "stop_loss",
            "take_profit",
        }
        assert result_fields.isdisjoint(forbidden)


class TestCalculateMultipleTimeframes:
    def test_multiple_timeframe_calculation_preserves_keys(self):
        candles_by_timeframe = {
            "4h": _make_zigzag_candles(60, "4h"),
            "1h": _make_zigzag_candles(60, "1h"),
        }
        results = _make_calculator().calculate_multiple_timeframes(candles_by_timeframe)
        assert set(results.keys()) == {"4h", "1h"}
        for result in results.values():
            assert isinstance(result, MarketStructureResult)

    def test_one_timeframe_failure_rejects_complete_calculation(self):
        candles_by_timeframe = {
            "4h": _make_zigzag_candles(60, "4h"),
            "1h": [],
        }
        with pytest.raises(MarketStructureCalculationError):
            _make_calculator().calculate_multiple_timeframes(candles_by_timeframe)
