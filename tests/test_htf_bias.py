"""
Unit tests for app.market_structure.htf_bias.
"""

from app.indicators.results import IndicatorSnapshot
from app.market_structure.htf_bias import HigherTimeframeBiasAnalyzer
from app.market_structure.results import (
    HigherTimeframeBias,
    MarketStructureResult,
    TrendDirection,
)


def _structure(trend: TrendDirection, timeframe: str) -> MarketStructureResult:
    return MarketStructureResult(
        symbol="BTC-USDT",
        timeframe=timeframe,
        swings=[],
        classified_swings=[],
        latest_swing_high=None,
        latest_swing_low=None,
        trend_direction=trend,
        higher_high_count=0,
        higher_low_count=0,
        lower_high_count=0,
        lower_low_count=0,
        equal_high_count=0,
        equal_low_count=0,
        confidence_notes=None,
    )


def _indicators(ema_direction: str) -> IndicatorSnapshot:
    return IndicatorSnapshot(ema200_slope_direction=ema_direction)


class TestHigherTimeframeBiasAnalyzer:
    def test_bullish_4h_and_1h_produces_bullish_bias(self):
        structures = {
            "4h": _structure(TrendDirection.BULLISH, "4h"),
            "1h": _structure(TrendDirection.BULLISH, "1h"),
        }
        indicators = {"4h": _indicators("BULLISH"), "1h": _indicators("BULLISH")}
        result = HigherTimeframeBiasAnalyzer().analyze(structures, indicators)
        assert result.final_bias == HigherTimeframeBias.BULLISH
        assert result.aligned is True

    def test_bearish_4h_and_1h_produces_bearish_bias(self):
        structures = {
            "4h": _structure(TrendDirection.BEARISH, "4h"),
            "1h": _structure(TrendDirection.BEARISH, "1h"),
        }
        indicators = {"4h": _indicators("BEARISH"), "1h": _indicators("BEARISH")}
        result = HigherTimeframeBiasAnalyzer().analyze(structures, indicators)
        assert result.final_bias == HigherTimeframeBias.BEARISH
        assert result.aligned is True

    def test_bullish_and_bearish_structures_produce_mixed(self):
        structures = {
            "4h": _structure(TrendDirection.BULLISH, "4h"),
            "1h": _structure(TrendDirection.BEARISH, "1h"),
        }
        indicators = {"4h": _indicators("BULLISH"), "1h": _indicators("BEARISH")}
        result = HigherTimeframeBiasAnalyzer().analyze(structures, indicators)
        assert result.final_bias == HigherTimeframeBias.MIXED
        assert result.aligned is False

    def test_missing_4h_produces_unknown(self):
        structures = {"1h": _structure(TrendDirection.BULLISH, "1h")}
        indicators = {"1h": _indicators("BULLISH")}
        result = HigherTimeframeBiasAnalyzer().analyze(structures, indicators)
        assert result.final_bias == HigherTimeframeBias.UNKNOWN
        assert result.aligned is False

    def test_missing_1h_produces_unknown(self):
        structures = {"4h": _structure(TrendDirection.BULLISH, "4h")}
        indicators = {"4h": _indicators("BULLISH")}
        result = HigherTimeframeBiasAnalyzer().analyze(structures, indicators)
        assert result.final_bias == HigherTimeframeBias.UNKNOWN

    def test_unknown_structure_produces_unknown(self):
        structures = {
            "4h": _structure(TrendDirection.UNKNOWN, "4h"),
            "1h": _structure(TrendDirection.BULLISH, "1h"),
        }
        indicators = {"4h": _indicators("BULLISH"), "1h": _indicators("BULLISH")}
        result = HigherTimeframeBiasAnalyzer().analyze(structures, indicators)
        assert result.final_bias == HigherTimeframeBias.UNKNOWN

    def test_bullish_structures_with_bearish_ema_conflict_produce_mixed(self):
        structures = {
            "4h": _structure(TrendDirection.BULLISH, "4h"),
            "1h": _structure(TrendDirection.BULLISH, "1h"),
        }
        indicators = {"4h": _indicators("BEARISH"), "1h": _indicators("BULLISH")}
        result = HigherTimeframeBiasAnalyzer().analyze(structures, indicators)
        assert result.final_bias == HigherTimeframeBias.MIXED

    def test_bearish_structures_with_bullish_ema_conflict_produce_mixed(self):
        structures = {
            "4h": _structure(TrendDirection.BEARISH, "4h"),
            "1h": _structure(TrendDirection.BEARISH, "1h"),
        }
        indicators = {"4h": _indicators("BULLISH"), "1h": _indicators("BEARISH")}
        result = HigherTimeframeBiasAnalyzer().analyze(structures, indicators)
        assert result.final_bias == HigherTimeframeBias.MIXED

    def test_bullish_structures_with_flat_ema_remain_bullish(self):
        structures = {
            "4h": _structure(TrendDirection.BULLISH, "4h"),
            "1h": _structure(TrendDirection.BULLISH, "1h"),
        }
        indicators = {"4h": _indicators("FLAT"), "1h": _indicators("FLAT")}
        result = HigherTimeframeBiasAnalyzer().analyze(structures, indicators)
        assert result.final_bias == HigherTimeframeBias.BULLISH

    def test_bearish_structures_with_flat_ema_remain_bearish(self):
        structures = {
            "4h": _structure(TrendDirection.BEARISH, "4h"),
            "1h": _structure(TrendDirection.BEARISH, "1h"),
        }
        indicators = {"4h": _indicators("FLAT"), "1h": _indicators("FLAT")}
        result = HigherTimeframeBiasAnalyzer().analyze(structures, indicators)
        assert result.final_bias == HigherTimeframeBias.BEARISH

    def test_aligned_true_only_for_bullish_or_bearish(self):
        mixed_structures = {
            "4h": _structure(TrendDirection.BULLISH, "4h"),
            "1h": _structure(TrendDirection.RANGE, "1h"),
        }
        indicators = {"4h": _indicators("BULLISH"), "1h": _indicators("FLAT")}
        result = HigherTimeframeBiasAnalyzer().analyze(mixed_structures, indicators)
        assert result.final_bias not in (
            HigherTimeframeBias.BULLISH,
            HigherTimeframeBias.BEARISH,
        )
        assert result.aligned is False

    def test_no_buy_sell_or_signal_fields_in_result(self):
        structures = {
            "4h": _structure(TrendDirection.BULLISH, "4h"),
            "1h": _structure(TrendDirection.BULLISH, "1h"),
        }
        indicators = {"4h": _indicators("BULLISH"), "1h": _indicators("BULLISH")}
        result = HigherTimeframeBiasAnalyzer().analyze(structures, indicators)
        result_fields = set(type(result).model_fields.keys())
        forbidden = {
            "direction",
            "entry_price",
            "stop_loss",
            "take_profit",
            "confidence_score",
            "signal_type",
        }
        assert result_fields.isdisjoint(forbidden)
