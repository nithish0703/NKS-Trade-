"""
Tests for app.scanner.active_state.
"""

from datetime import datetime, timezone

import pytest

from app.scanner.active_state import ActiveTradingState, EmptyActiveTradingStateProvider


class TestEmptyActiveTradingStateProvider:
    pytestmark = pytest.mark.asyncio

    async def test_zero_active_trades(self):
        provider = EmptyActiveTradingStateProvider()
        state = await provider.get_state()
        assert state.active_trade_count == 0

    async def test_active_positions_empty(self):
        provider = EmptyActiveTradingStateProvider()
        state = await provider.get_state()
        assert state.active_positions == []

    async def test_candle_mapping_empty(self):
        provider = EmptyActiveTradingStateProvider()
        state = await provider.get_state()
        assert state.active_position_candles == {}

    async def test_timestamp_utc_aware(self):
        provider = EmptyActiveTradingStateProvider()
        state = await provider.get_state()
        assert state.captured_at_utc.tzinfo is not None
        assert state.captured_at_utc.utcoffset() == timezone.utc.utcoffset(state.captured_at_utc)


class TestActiveTradingStateValidation:
    def test_negative_active_count_rejected(self):
        with pytest.raises(ValueError):
            ActiveTradingState(
                active_trade_count=-1,
                active_positions=[],
                active_position_candles={},
                captured_at_utc=datetime.now(timezone.utc),
            )

    def test_naive_timestamp_rejected(self):
        with pytest.raises(ValueError):
            ActiveTradingState(
                active_trade_count=0,
                active_positions=[],
                active_position_candles={},
                captured_at_utc=datetime(2026, 1, 1),
            )

    def test_defensive_immutable_state(self):
        state = ActiveTradingState(
            active_trade_count=0,
            active_positions=[],
            active_position_candles={},
            captured_at_utc=datetime.now(timezone.utc),
        )
        with pytest.raises(Exception):
            state.active_trade_count = 5
