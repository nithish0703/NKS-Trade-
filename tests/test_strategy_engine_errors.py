"""
Tests for app.scanner.strategy_engine.InstitutionalSMCStrategyEngine
error handling.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.data.market_data_provider import MarketDataRequestError
from app.indicators.ema import IndicatorCalculationError
from app.scanner.pipeline_exceptions import (
    PipelineDataUnavailableError,
    PipelineInputError,
    PipelineStageError,
)

from tests.test_strategy_engine_rejections import UTC_NOW, _build_engine, _market_data

pytestmark = pytest.mark.asyncio


class TestStrategyEngineErrors:
    async def test_invalid_symbol(self):
        engine = _build_engine()
        with pytest.raises(PipelineInputError) as exc_info:
            await engine.analyze_symbol(
                symbol="NOT-A-REAL-PAIR",
                account_balance=10000.0,
                active_trade_count=0,
                active_positions=[],
                active_position_candles={},
                detection_time_utc=UTC_NOW,
            )
        assert exc_info.value.layer_name == "INPUT_VALIDATION"

    async def test_invalid_account_balance(self):
        engine = _build_engine()
        with pytest.raises(PipelineInputError) as exc_info:
            await engine.analyze_symbol(
                symbol="BTC-USDT",
                account_balance=0.0,
                active_trade_count=0,
                active_positions=[],
                active_position_candles={},
                detection_time_utc=UTC_NOW,
            )
        assert "account_balance" in exc_info.value.reason

    async def test_negative_active_trade_count(self):
        engine = _build_engine()
        with pytest.raises(PipelineInputError) as exc_info:
            await engine.analyze_symbol(
                symbol="BTC-USDT",
                account_balance=10000.0,
                active_trade_count=-1,
                active_positions=[],
                active_position_candles={},
                detection_time_utc=UTC_NOW,
            )
        assert "active_trade_count" in exc_info.value.reason

    async def test_market_data_failure(self):
        market_data_provider = AsyncMock()
        market_data_provider.fetch_symbol_market_data = AsyncMock(
            side_effect=MarketDataRequestError("connection refused")
        )
        engine = _build_engine(market_data_provider=market_data_provider)
        with pytest.raises(PipelineDataUnavailableError) as exc_info:
            await engine.analyze_symbol(
                symbol="BTC-USDT",
                account_balance=10000.0,
                active_trade_count=0,
                active_positions=[],
                active_position_candles={},
                detection_time_utc=UTC_NOW,
            )
        assert exc_info.value.layer_name == "MARKET_DATA_PREPARATION"

    async def test_missing_timeframe_data(self):
        market_data_provider = AsyncMock()
        market_data_provider.fetch_symbol_market_data = AsyncMock(
            return_value={"15m": [], "1h": [], "4h": []}
        )
        engine = _build_engine(market_data_provider=market_data_provider)
        with pytest.raises(PipelineDataUnavailableError):
            await engine.analyze_symbol(
                symbol="BTC-USDT",
                account_balance=10000.0,
                active_trade_count=0,
                active_positions=[],
                active_position_candles={},
                detection_time_utc=UTC_NOW,
            )

    async def test_indicator_calculation_failure(self):
        market_data_provider = AsyncMock()
        market_data_provider.fetch_symbol_market_data = AsyncMock(side_effect=lambda s: _market_data(s))
        indicator_calculator = MagicMock()
        indicator_calculator.calculate_multiple_timeframes = MagicMock(
            side_effect=IndicatorCalculationError("insufficient candles")
        )
        engine = _build_engine(
            market_data_provider=market_data_provider, indicator_calculator=indicator_calculator
        )
        with pytest.raises(PipelineDataUnavailableError):
            await engine.analyze_symbol(
                symbol="BTC-USDT",
                account_balance=10000.0,
                active_trade_count=0,
                active_positions=[],
                active_position_candles={},
                detection_time_utc=UTC_NOW,
            )

    async def test_unexpected_validator_exception_raises_pipeline_stage_error(self):
        risk_management_calculator = MagicMock()
        risk_management_calculator.calculate = MagicMock(side_effect=RuntimeError("boom"))
        engine = _build_engine(risk_management_calculator=risk_management_calculator)
        with pytest.raises(PipelineStageError) as exc_info:
            await engine.analyze_symbol(
                symbol="BTC-USDT",
                account_balance=10000.0,
                active_trade_count=0,
                active_positions=[],
                active_position_candles={},
                detection_time_utc=UTC_NOW,
            )
        assert exc_info.value.layer_name == "RISK_MANAGEMENT"
        assert exc_info.value.original_exception is not None

    async def test_error_distinguished_from_rejected(self):
        # A PipelineStageError is raised (ERROR-equivalent), not returned
        # as a normal REJECTED StrategyPipelineResult.
        risk_management_calculator = MagicMock()
        risk_management_calculator.calculate = MagicMock(side_effect=RuntimeError("boom"))
        engine = _build_engine(risk_management_calculator=risk_management_calculator)
        with pytest.raises(PipelineStageError):
            await engine.analyze_symbol(
                symbol="BTC-USDT",
                account_balance=10000.0,
                active_trade_count=0,
                active_positions=[],
                active_position_candles={},
                detection_time_utc=UTC_NOW,
            )

    async def test_exception_does_not_expose_full_payload(self):
        engine = _build_engine()
        try:
            await engine.analyze_symbol(
                symbol="NOT-A-REAL-PAIR",
                account_balance=10000.0,
                active_trade_count=0,
                active_positions=[],
                active_position_candles={},
                detection_time_utc=UTC_NOW,
            )
        except PipelineInputError as exc:
            message = str(exc)
            assert "api_key" not in message.lower()
            assert "secret" not in message.lower()
            assert "token" not in message.lower()

    async def test_partial_context_preserved_safely_on_rejection(self):
        from app.models.validation_result import ValidationResult

        market_regime_validator = MagicMock()
        market_regime_validator.validate = MagicMock(
            return_value=ValidationResult.failure(layer_name="MARKET_REGIME", reason="not trending")
        )
        engine = _build_engine(market_regime_validator=market_regime_validator)
        result = await engine.analyze_symbol(
            symbol="BTC-USDT",
            account_balance=10000.0,
            active_trade_count=0,
            active_positions=[],
            active_position_candles={},
            detection_time_utc=UTC_NOW,
        )
        assert result.market_context is not None
        assert result.market_context.symbol == "BTC-USDT"
