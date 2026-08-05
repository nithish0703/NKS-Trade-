"""
Tests for the `signals` CLI mode in main.py.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

import main
from app.models.signal import Direction, MarketRegime, Signal, SignalStatus
from app.storage.database import DatabaseManager
from app.storage.signal_repository import SignalRepository

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _signal(trade_id="SMC-1", coin="BTC-USDT") -> Signal:
    return Signal(
        trade_id=trade_id,
        coin=coin,
        direction=Direction.BUY,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_reward_ratio=3.0,
        status=SignalStatus.CONFIRMED,
        market_regime=MarketRegime.TRENDING,
        higher_timeframe_bias="BULLISH",
        liquidity_type="EQUAL_HIGH",
        entry_zone_type="ORDER_BLOCK",
        structure_confirmation="BOS",
        volume_confirmation=True,
        atr_status="EXPANDING",
        trading_session="LONDON",
        btc_market_alignment=True,
        detection_time_utc=UTC_NOW,
        institutional_reason="Confirmed setup facts only. Not a guarantee of outcome.",
        setup_key=f"setup-{trade_id}",
        liquidity_sweep_id="sweep-1",
        structure_break_id="break-1",
        entry_zone_id="zone-1",
        retest_id="retest-1",
        created_at_utc=UTC_NOW,
    )


@pytest_asyncio.fixture
async def seeded_database_url(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'signals.db'}"
    manager = DatabaseManager(database_url)
    await manager.initialize()
    repository = SignalRepository(manager)
    await repository.save(_signal(trade_id="SMC-BTC", coin="BTC-USDT"))
    await repository.save(_signal(trade_id="SMC-ETH", coin="ETH-USDT"))
    await manager.dispose()
    return database_url


class TestSignalsMode:
    async def test_signals_command_lists_stored_signals(self, seeded_database_url, capsys):
        settings = MagicMock()
        settings.database_url = seeded_database_url
        with patch("app.config.settings.get_settings", return_value=settings):
            await main._run_signals(None, 20)
        output = capsys.readouterr().out
        assert "SMC-BTC" in output
        assert "SMC-ETH" in output

    async def test_symbol_filter(self, seeded_database_url, capsys):
        settings = MagicMock()
        settings.database_url = seeded_database_url
        with patch("app.config.settings.get_settings", return_value=settings):
            await main._run_signals("BTC-USDT", 20)
        output = capsys.readouterr().out
        assert "SMC-BTC" in output
        assert "SMC-ETH" not in output

    async def test_limit(self, tmp_path, capsys):
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'limit.db'}"
        manager = DatabaseManager(database_url)
        await manager.initialize()
        repository = SignalRepository(manager)
        for index in range(5):
            await repository.save(_signal(trade_id=f"SMC-{index}"))
        await manager.dispose()

        settings = MagicMock()
        settings.database_url = database_url
        with patch("app.config.settings.get_settings", return_value=settings):
            await main._run_signals(None, 2)
        output = capsys.readouterr().out
        assert output.count("Trade ID:") == 2

    async def test_concise_output(self, seeded_database_url, capsys):
        settings = MagicMock()
        settings.database_url = seeded_database_url
        with patch("app.config.settings.get_settings", return_value=settings):
            await main._run_signals("BTC-USDT", 20)
        output = capsys.readouterr().out
        assert "Trade ID:" in output
        assert "Coin:" in output
        assert "Direction:" in output
        assert "Entry:" in output
        assert "Stop Loss:" in output
        assert "Take Profit:" in output
        assert "RR:" in output
        assert "Status:" in output
        assert "Detection Time:" in output

    async def test_no_secrets_printed(self, seeded_database_url, capsys):
        settings = MagicMock()
        settings.database_url = seeded_database_url
        with patch("app.config.settings.get_settings", return_value=settings):
            await main._run_signals(None, 20)
        output = capsys.readouterr().out
        assert "api_key" not in output.lower()
        assert "secret" not in output.lower()

    async def test_no_raw_candles_printed(self, seeded_database_url, capsys):
        settings = MagicMock()
        settings.database_url = seeded_database_url
        with patch("app.config.settings.get_settings", return_value=settings):
            await main._run_signals(None, 20)
        output = capsys.readouterr().out
        assert "candles_by_timeframe" not in output
        assert "OHLCV" not in output

    async def test_no_telegram_call(self, seeded_database_url, capsys):
        settings = MagicMock()
        settings.database_url = seeded_database_url
        with patch("app.config.settings.get_settings", return_value=settings) as mock_get_settings, patch(
            "main.DatabaseManager"
        ) as mock_manager_cls:
            mock_manager_cls.side_effect = lambda url: DatabaseManager(url)
            await main._run_signals(None, 20)
        output = capsys.readouterr().out
        assert "telegram" not in output.lower()

    async def test_database_disposed_cleanly(self, seeded_database_url):
        settings = MagicMock()
        settings.database_url = seeded_database_url
        disposed = False
        original_dispose = DatabaseManager.dispose

        async def _tracking_dispose(self):
            nonlocal disposed
            disposed = True
            await original_dispose(self)

        with patch("app.config.settings.get_settings", return_value=settings), patch.object(
            DatabaseManager, "dispose", _tracking_dispose
        ):
            await main._run_signals(None, 20)
        assert disposed is True

    def test_signals_args_parsed(self):
        symbol, limit = main._parse_signals_args(["main.py", "--symbol", "BTC-USDT", "--limit", "5"])
        assert symbol == "BTC-USDT"
        assert limit == 5

    def test_signals_args_default(self):
        symbol, limit = main._parse_signals_args(["main.py"])
        assert symbol is None
        assert limit == 20
