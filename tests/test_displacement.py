"""
Unit tests for app.market_structure.displacement.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.market_structure.displacement import DisplacementDetector, StructureShiftCalculationError
from app.market_structure.shift_results import BreakDirection
from app.models.candle import Candle

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(
    index: int, open_: float, high: float, low: float, close: float, volume: float = 100.0
) -> Candle:
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        symbol="BTC-USDT",
        timeframe="15m",
    )


def _detector(min_body=0.60, bull_min=0.75, bear_max=0.25) -> DisplacementDetector:
    return DisplacementDetector(
        minimum_body_ratio=min_body,
        bullish_close_location_minimum=bull_min,
        bearish_close_location_maximum=bear_max,
    )


class TestDetect:
    def test_confirmed_bullish_displacement(self):
        # range 10, body 8 (80%), close near high (CLV high)
        candle = _candle(0, open_=100, high=110, low=100, close=108, volume=200)
        volume_ema = [100.0]
        results = _detector().detect([candle], volume_ema)
        assert results[0].direction == BreakDirection.BULLISH
        assert results[0].confirmed is True

    def test_confirmed_bearish_displacement(self):
        candle = _candle(0, open_=108, high=108, low=98, close=100, volume=200)
        volume_ema = [100.0]
        results = _detector().detect([candle], volume_ema)
        assert results[0].direction == BreakDirection.BEARISH
        assert results[0].confirmed is True

    def test_body_ratio_exactly_60_percent_passes(self):
        # range 10 (100-110), body 6 (104-110) -> ratio 0.60 exactly;
        # close at the candle high for CLV=1.0
        candle = _candle(0, open_=104, high=110, low=100, close=110, volume=200)
        results = _detector(min_body=0.60).detect([candle], [100.0])
        assert results[0].body_ratio == pytest.approx(0.6)
        assert results[0].confirmed is True

    def test_body_ratio_below_60_percent_fails(self):
        candle = _candle(0, open_=100, high=110, low=95, close=103, volume=200)
        results = _detector(min_body=0.60).detect([candle], [100.0])
        assert results[0].confirmed is False

    def test_bullish_weak_close_fails(self):
        # Large bullish body but close far from the high (weak CLV).
        candle = _candle(0, open_=100, high=120, low=99, close=110, volume=200)
        results = _detector(min_body=0.10, bull_min=0.90).detect([candle], [100.0])
        assert results[0].direction == BreakDirection.BULLISH
        assert results[0].strong_close is False
        assert results[0].confirmed is False

    def test_bearish_weak_close_fails(self):
        candle = _candle(0, open_=110, high=111, low=90, close=100, volume=200)
        results = _detector(min_body=0.10, bear_max=0.05).detect([candle], [100.0])
        assert results[0].direction == BreakDirection.BEARISH
        assert results[0].strong_close is False
        assert results[0].confirmed is False

    def test_volume_above_ema_confirms(self):
        candle = _candle(0, open_=100, high=110, low=100, close=109, volume=150)
        results = _detector().detect([candle], [100.0])
        assert results[0].volume_confirmed is True

    def test_volume_equal_to_ema_does_not_confirm(self):
        candle = _candle(0, open_=100, high=110, low=100, close=109, volume=100)
        results = _detector().detect([candle], [100.0])
        assert results[0].volume_confirmed is False
        assert results[0].confirmed is False

    def test_missing_volume_ema_gives_volume_confirmed_false(self):
        candle = _candle(0, open_=100, high=110, low=100, close=109, volume=150)
        results = _detector().detect([candle], [None])
        assert results[0].volume_confirmed is False
        assert results[0].confirmed is False

    def test_no_volume_ema_list_gives_volume_confirmed_false(self):
        candle = _candle(0, open_=100, high=110, low=100, close=109, volume=150)
        results = _detector().detect([candle])
        assert results[0].volume_confirmed is False

    def test_mismatched_ema_list_length_rejection(self):
        candle = _candle(0, open_=100, high=110, low=100, close=109, volume=150)
        with pytest.raises(StructureShiftCalculationError):
            _detector().detect([candle], [100.0, 101.0])

    def test_zero_range_candle_handling(self):
        candle = _candle(0, open_=100, high=100, low=100, close=100, volume=150)
        results = _detector().detect([candle], [100.0])
        assert results[0].close_location_value == 0.0
        assert results[0].confirmed is False

    def test_mixed_symbol_rejection(self):
        c1 = _candle(0, 100, 110, 100, 109)
        c2 = Candle(
            timestamp=UTC_NOW + timedelta(minutes=1),
            open=100,
            high=110,
            low=100,
            close=109,
            volume=100.0,
            symbol="ETH-USDT",
            timeframe="15m",
        )
        with pytest.raises(StructureShiftCalculationError):
            _detector().detect([c1, c2])

    def test_non_ascending_candles_rejection(self):
        c1 = _candle(0, 100, 110, 100, 109)
        c2 = _candle(1, 100, 110, 100, 109)
        with pytest.raises(StructureShiftCalculationError):
            _detector().detect([c2, c1])

    def test_input_data_not_mutated(self):
        candles = [_candle(0, 100, 110, 100, 109), _candle(1, 105, 115, 104, 114)]
        snapshot = [c.model_copy() for c in candles]
        _detector().detect(candles, [100.0, 100.0])
        assert candles == snapshot

    def test_constructor_validation(self):
        with pytest.raises(StructureShiftCalculationError):
            DisplacementDetector(
                minimum_body_ratio=1.5,
                bullish_close_location_minimum=0.75,
                bearish_close_location_maximum=0.25,
            )
        with pytest.raises(StructureShiftCalculationError):
            DisplacementDetector(
                minimum_body_ratio=0.6,
                bullish_close_location_minimum=-0.1,
                bearish_close_location_maximum=0.25,
            )
        with pytest.raises(StructureShiftCalculationError):
            DisplacementDetector(
                minimum_body_ratio=0.6,
                bullish_close_location_minimum=0.75,
                bearish_close_location_maximum=1.5,
            )


class TestDetectAtIndex:
    def test_detect_at_index_matches_detect(self):
        candles = [_candle(0, 100, 110, 100, 109), _candle(1, 105, 115, 104, 114)]
        volume_ema = [100.0, 100.0]
        full_results = _detector().detect(candles, volume_ema)
        single_result = _detector().detect_at_index(candles, 1, volume_ema)
        assert single_result == full_results[1]

    def test_detect_at_index_out_of_range(self):
        candles = [_candle(0, 100, 110, 100, 109)]
        with pytest.raises(StructureShiftCalculationError):
            _detector().detect_at_index(candles, 5, [100.0])
