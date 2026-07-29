"""
Unit tests for app.data.candle_repository.CandleRepository.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.data.candle_repository import CandleRepository
from app.models.candle import Candle

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_candle(offset_minutes: int, **overrides) -> Candle:
    fields = {
        "timestamp": UTC_NOW + timedelta(minutes=offset_minutes),
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 1000.0,
        "symbol": "BTC-USDT",
        "timeframe": "15m",
    }
    fields.update(overrides)
    return Candle(**fields)


def test_save_and_retrieve_candles():
    repo = CandleRepository()
    candles = [_make_candle(0), _make_candle(15)]
    repo.save_candles("BTC-USDT", "15m", candles)

    result = repo.get_candles("BTC-USDT", "15m")
    assert len(result) == 2


def test_candles_remain_ascending():
    repo = CandleRepository()
    repo.save_candles("BTC-USDT", "15m", [_make_candle(15)])
    repo.save_candles("BTC-USDT", "15m", [_make_candle(0)])

    result = repo.get_candles("BTC-USDT", "15m")
    timestamps = [c.timestamp for c in result]
    assert timestamps == sorted(timestamps)


def test_duplicate_timestamps_are_replaced():
    repo = CandleRepository()
    original = _make_candle(0, close=105.0)
    repo.save_candles("BTC-USDT", "15m", [original])

    updated = _make_candle(0, close=108.0, high=120.0)
    repo.save_candles("BTC-USDT", "15m", [updated])

    result = repo.get_candles("BTC-USDT", "15m")
    assert len(result) == 1
    assert result[0].close == 108.0


def test_latest_candle_retrieval():
    repo = CandleRepository()
    repo.save_candles("BTC-USDT", "15m", [_make_candle(0), _make_candle(15)])

    latest = repo.get_latest_candle("BTC-USDT", "15m")
    assert latest is not None
    assert latest.timestamp == UTC_NOW + timedelta(minutes=15)


def test_latest_candle_none_when_empty():
    repo = CandleRepository()
    assert repo.get_latest_candle("BTC-USDT", "15m") is None


def test_limit_returns_latest_candles():
    repo = CandleRepository()
    candles = [_make_candle(offset) for offset in range(0, 60, 15)]
    repo.save_candles("BTC-USDT", "15m", candles)

    result = repo.get_candles("BTC-USDT", "15m", limit=2)
    assert len(result) == 2
    assert result[-1].timestamp == candles[-1].timestamp


def test_get_candles_rejects_non_positive_limit():
    repo = CandleRepository()
    repo.save_candles("BTC-USDT", "15m", [_make_candle(0)])

    with pytest.raises(ValueError):
        repo.get_candles("BTC-USDT", "15m", limit=0)


def test_returned_list_cannot_mutate_internal_state():
    repo = CandleRepository()
    repo.save_candles("BTC-USDT", "15m", [_make_candle(0)])

    result = repo.get_candles("BTC-USDT", "15m")
    result.append(_make_candle(999))

    assert len(repo.get_candles("BTC-USDT", "15m")) == 1


def test_maximum_storage_size_is_enforced():
    repo = CandleRepository(max_candles_per_key=3)
    candles = [_make_candle(offset) for offset in range(0, 60, 15)]
    repo.save_candles("BTC-USDT", "15m", candles)

    result = repo.get_candles("BTC-USDT", "15m")
    assert len(result) == 3
    assert result[-1].timestamp == candles[-1].timestamp


def test_has_data():
    repo = CandleRepository()
    assert repo.has_data("BTC-USDT", "15m") is False

    repo.save_candles("BTC-USDT", "15m", [_make_candle(0)])
    assert repo.has_data("BTC-USDT", "15m") is True


def test_clear_one_symbol_and_timeframe():
    repo = CandleRepository()
    repo.save_candles("BTC-USDT", "15m", [_make_candle(0)])
    repo.save_candles("BTC-USDT", "1h", [_make_candle(0)])

    repo.clear(symbol="BTC-USDT", timeframe="15m")

    assert repo.has_data("BTC-USDT", "15m") is False
    assert repo.has_data("BTC-USDT", "1h") is True


def test_clear_all_timeframes_for_one_symbol():
    repo = CandleRepository()
    repo.save_candles("BTC-USDT", "15m", [_make_candle(0)])
    repo.save_candles("BTC-USDT", "1h", [_make_candle(0)])
    repo.save_candles("ETH-USDT", "15m", [_make_candle(0, symbol="ETH-USDT")])

    repo.clear(symbol="BTC-USDT")

    assert repo.has_data("BTC-USDT", "15m") is False
    assert repo.has_data("BTC-USDT", "1h") is False
    assert repo.has_data("ETH-USDT", "15m") is True


def test_clear_entire_repository():
    repo = CandleRepository()
    repo.save_candles("BTC-USDT", "15m", [_make_candle(0)])
    repo.save_candles("ETH-USDT", "1h", [_make_candle(0, symbol="ETH-USDT")])

    repo.clear()

    assert repo.has_data("BTC-USDT", "15m") is False
    assert repo.has_data("ETH-USDT", "1h") is False


def test_timeframe_only_clear_raises_value_error():
    repo = CandleRepository()
    repo.save_candles("BTC-USDT", "15m", [_make_candle(0)])

    with pytest.raises(ValueError):
        repo.clear(timeframe="15m")
