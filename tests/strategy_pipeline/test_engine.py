"""
Tests for app.strategy_pipeline.engine.PipelineStrategyEngine, covering
input validation and the market-data-unavailable error path with
mocked dependencies. Full end-to-end success/rejection paths against
real chained detectors are exercised separately via a live smoke run
against real Binance market data (not part of the unit test suite,
since that requires network access).
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.data.market_data_errors import MarketDataRequestError
from app.scanner.pipeline_exceptions import PipelineDataUnavailableError, PipelineInputError
from app.strategy_pipeline.engine import PipelineStrategyEngine

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _build_engine(**overrides) -> PipelineStrategyEngine:
    from app.data.candle_repository import CandleRepository

    defaults = dict(
        market_data_provider=MagicMock(),
        candle_repository=CandleRepository(),
        indicator_calculator=MagicMock(),
        market_structure_calculator=MagicMock(),
        liquidity_calculator=MagicMock(),
        displacement_detector=MagicMock(),
        bos_detector=MagicMock(),
        fair_value_gap_detector=MagicMock(),
        risk_management_calculator=MagicMock(),
    )
    defaults.update(overrides)
    return PipelineStrategyEngine(**defaults)


async def _analyze(engine: PipelineStrategyEngine, symbol: str = "BTC-USDT"):
    return await engine.analyze_symbol(
        symbol=symbol,
        account_balance=10_000.0,
        active_trade_count=0,
        active_positions=[],
        active_position_candles={},
        detection_time_utc=UTC_NOW,
    )


class TestInputValidation:
    async def test_invalid_symbol_raises_pipeline_input_error(self):
        engine = _build_engine()
        with pytest.raises(PipelineInputError):
            await _analyze(engine, symbol="not-a-real-symbol")

    async def test_non_positive_account_balance_raises_pipeline_input_error(self):
        engine = _build_engine()
        with pytest.raises(PipelineInputError):
            await engine.analyze_symbol(
                symbol="BTC-USDT",
                account_balance=0.0,
                active_trade_count=0,
                active_positions=[],
                active_position_candles={},
                detection_time_utc=UTC_NOW,
            )

    async def test_negative_active_trade_count_raises_pipeline_input_error(self):
        engine = _build_engine()
        with pytest.raises(PipelineInputError):
            await engine.analyze_symbol(
                symbol="BTC-USDT",
                account_balance=10_000.0,
                active_trade_count=-1,
                active_positions=[],
                active_position_candles={},
                detection_time_utc=UTC_NOW,
            )


class TestMarketDataUnavailable:
    async def test_market_data_error_raises_pipeline_data_unavailable_error(self):
        provider = MagicMock()
        provider.fetch_symbol_market_data = AsyncMock(
            side_effect=MarketDataRequestError("network failure")
        )
        engine = _build_engine(market_data_provider=provider)

        with pytest.raises(PipelineDataUnavailableError):
            await _analyze(engine)

    async def test_missing_required_timeframe_raises_pipeline_data_unavailable_error(self):
        provider = MagicMock()
        # Missing "1h" and "4h" -- only entry timeframe present.
        provider.fetch_symbol_market_data = AsyncMock(return_value={"15m": [MagicMock()]})
        engine = _build_engine(market_data_provider=provider)

        with pytest.raises(PipelineDataUnavailableError):
            await _analyze(engine)
