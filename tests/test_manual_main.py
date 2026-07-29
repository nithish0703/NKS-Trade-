"""
Tests for the manual, one-symbol main.py entry point.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main
from app.data.market_data_provider import MarketDataRequestError
from app.scanner.pipeline_exceptions import PipelineInputError
from app.scanner.pipeline_results import PipelineStatus, StrategyPipelineResult
from app.scoring.results import ConfidenceClassification


def _valid_result() -> StrategyPipelineResult:
    risk_plan = MagicMock()
    risk_plan.entry_price = 100.0
    risk_plan.stop_loss_result.selected_stop_loss = 95.0
    risk_plan.take_profit_result.selected_take_profit = 110.0
    risk_plan.risk_reward_ratio = 2.0

    confidence_result = MagicMock()
    confidence_result.normalized_score = 92.5
    confidence_result.classification = ConfidenceClassification.PREMIUM

    result = MagicMock(spec=StrategyPipelineResult)
    result.symbol = "BTC-USDT"
    result.status = PipelineStatus.VALID
    result.expected_direction = "BUY"
    result.failed_layer = None
    result.rejection_reason = None
    result.risk_plan = risk_plan
    result.confidence_result = confidence_result
    return result


def _rejected_result() -> StrategyPipelineResult:
    result = MagicMock(spec=StrategyPipelineResult)
    result.symbol = "BTC-USDT"
    result.status = PipelineStatus.REJECTED
    result.expected_direction = None
    result.failed_layer = "MARKET_REGIME"
    result.rejection_reason = "ADX below trending threshold."
    result.risk_plan = None
    result.confidence_result = None
    return result


class TestParseArgs:
    def test_default_symbol_and_balance(self):
        symbol, balance = main._parse_args(["main.py"])
        assert symbol == "BTC-USDT"
        assert balance == main._DEFAULT_DEVELOPMENT_BALANCE

    def test_custom_symbol_and_balance(self):
        symbol, balance = main._parse_args(["main.py", "ETH-USDT", "5000"])
        assert symbol == "ETH-USDT"
        assert balance == 5000.0


class TestPrintResult:
    def test_concise_success_output(self, capsys):
        main._print_result(_valid_result())
        output = capsys.readouterr().out
        assert "symbol=BTC-USDT" in output
        assert "status=VALID" in output
        assert "direction=BUY" in output
        assert "entry=100.0" in output
        assert "stop_loss=95.0" in output
        assert "take_profit=110.0" in output
        assert "confidence=92.5" in output
        assert "classification=PREMIUM" in output

    def test_concise_rejected_output(self, capsys):
        main._print_result(_rejected_result())
        output = capsys.readouterr().out
        assert "status=REJECTED" in output
        assert "failed_layer=MARKET_REGIME" in output
        assert "rejection_reason=ADX below trending threshold." in output

    def test_no_full_candle_payload_printed(self, capsys):
        main._print_result(_valid_result())
        output = capsys.readouterr().out
        assert "candles_by_timeframe" not in output
        assert "OHLCV" not in output


class TestRunManualAnalysis:
    @pytest.mark.asyncio
    async def test_one_symbol_execution_only(self, capsys):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_result())
        with patch("main.build_strategy_engine", return_value=engine):
            await main._run_manual_analysis("BTC-USDT", 10000.0)
        engine.analyze_symbol.assert_called_once()
        output = capsys.readouterr().out
        assert "symbol=BTC-USDT" in output

    @pytest.mark.asyncio
    async def test_concise_technical_error_output_on_market_data_failure(self, capsys):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(side_effect=MarketDataRequestError("connection blocked"))
        with patch("main.build_strategy_engine", return_value=engine):
            await main._run_manual_analysis("BTC-USDT", 10000.0)
        output = capsys.readouterr().out
        assert "status=ERROR" in output
        assert "api_key" not in output.lower()
        assert "secret" not in output.lower()

    @pytest.mark.asyncio
    async def test_concise_technical_error_output_on_pipeline_error(self, capsys):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(
            side_effect=PipelineInputError(layer_name="INPUT_VALIDATION", symbol="XX", reason="bad symbol")
        )
        with patch("main.build_strategy_engine", return_value=engine):
            await main._run_manual_analysis("XX", 10000.0)
        output = capsys.readouterr().out
        assert "status=ERROR" in output
        assert "failed_layer=INPUT_VALIDATION" in output

    @pytest.mark.asyncio
    async def test_no_secrets_printed(self, capsys):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_result())
        with patch("main.build_strategy_engine", return_value=engine):
            await main._run_manual_analysis("BTC-USDT", 10000.0)
        output = capsys.readouterr().out
        assert "api_key" not in output.lower()
        assert "password" not in output.lower()

    def test_no_infinite_loop(self):
        # main.main() must call asyncio.run exactly once and return.
        with patch("main.asyncio.run") as mock_run:
            with patch("main.sys.argv", ["main.py"]):
                main.main()
            mock_run.assert_called_once()
            # Close the un-awaited coroutine passed to the mocked asyncio.run
            # to avoid a RuntimeWarning; it is never actually executed here.
            mock_run.call_args.args[0].close()
