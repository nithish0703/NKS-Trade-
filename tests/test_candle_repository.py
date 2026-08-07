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


def _stored(repo: CandleRepository, symbol: str, timeframe: str) -> list[Candle]:
    """Read back what save_candles()/clear() left behind, via the internal store."""
    return repo._store.get((symbol, timeframe), [])


def test_save_and_retrieve_candles():
    repo = CandleRepository()
    candles = [_make_candle(0), _make_candle(15)]
    repo.save_candles("BTC-USDT", "15m", candles)

    assert len(_stored(repo, "BTC-USDT", "15m")) == 2


def test_candles_remain_ascending():
    repo = CandleRepository()
    repo.save_candles("BTC-USDT", "15m", [_make_candle(15)])
    repo.save_candles("BTC-USDT", "15m", [_make_candle(0)])

    timestamps = [c.timestamp for c in _stored(repo, "BTC-USDT", "15m")]
    assert timestamps == sorted(timestamps)


def test_duplicate_timestamps_are_replaced():
    repo = CandleRepository()
    original = _make_candle(0, close=105.0)
    repo.save_candles("BTC-USDT", "15m", [original])

    updated = _make_candle(0, close=108.0, high=120.0)
    repo.save_candles("BTC-USDT", "15m", [updated])

    result = _stored(repo, "BTC-USDT", "15m")
    assert len(result) == 1
    assert result[0].close == 108.0


def test_maximum_storage_size_is_enforced():
    repo = CandleRepository(max_candles_per_key=3)
    candles = [_make_candle(offset) for offset in range(0, 60, 15)]
    repo.save_candles("BTC-USDT", "15m", candles)

    result = _stored(repo, "BTC-USDT", "15m")
    assert len(result) == 3
    assert result[-1].timestamp == candles[-1].timestamp


def test_clear_one_symbol_and_timeframe():
    repo = CandleRepository()
    repo.save_candles("BTC-USDT", "15m", [_make_candle(0)])
    repo.save_candles("BTC-USDT", "1h", [_make_candle(0)])

    repo.clear(symbol="BTC-USDT", timeframe="15m")

    assert _stored(repo, "BTC-USDT", "15m") == []
    assert len(_stored(repo, "BTC-USDT", "1h")) == 1


def test_clear_all_timeframes_for_one_symbol():
    repo = CandleRepository()
    repo.save_candles("BTC-USDT", "15m", [_make_candle(0)])
    repo.save_candles("BTC-USDT", "1h", [_make_candle(0)])
    repo.save_candles("ETH-USDT", "15m", [_make_candle(0, symbol="ETH-USDT")])

    repo.clear(symbol="BTC-USDT")

    assert _stored(repo, "BTC-USDT", "15m") == []
    assert _stored(repo, "BTC-USDT", "1h") == []
    assert len(_stored(repo, "ETH-USDT", "15m")) == 1


def test_clear_entire_repository():
    repo = CandleRepository()
    repo.save_candles("BTC-USDT", "15m", [_make_candle(0)])
    repo.save_candles("ETH-USDT", "1h", [_make_candle(0, symbol="ETH-USDT")])

    repo.clear()

    assert _stored(repo, "BTC-USDT", "15m") == []
    assert _stored(repo, "ETH-USDT", "1h") == []


def test_timeframe_only_clear_raises_value_error():
    repo = CandleRepository()
    repo.save_candles("BTC-USDT", "15m", [_make_candle(0)])

    with pytest.raises(ValueError):
        repo.clear(timeframe="15m")
