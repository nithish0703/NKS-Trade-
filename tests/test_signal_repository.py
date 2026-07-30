"""
Tests for app.storage.signal_repository.SignalRepository.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.models.signal import Direction, MarketRegime, Signal, SignalType
from app.storage.database import DatabaseManager
from app.storage.signal_repository import (
    DASHBOARD_STATUS_ACTIVE,
    DASHBOARD_STATUS_NEW,
    DuplicateSignalStorageError,
    SignalNotFoundError,
    SignalRepository,
)

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _signal(
    *,
    trade_id="SMC-BTC-USDT-BUY-abc123",
    setup_key="setup-key-1",
    coin="BTC-USDT",
    signal_type=SignalType.PREMIUM,
    detection_time_utc=UTC_NOW,
    created_at_utc=UTC_NOW,
) -> Signal:
    return Signal(
        trade_id=trade_id,
        coin=coin,
        direction=Direction.BUY,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_reward_ratio=3.0,
        confidence_score=90.0,
        signal_type=signal_type,
        market_regime=MarketRegime.TRENDING,
        higher_timeframe_bias="BULLISH",
        liquidity_type="EQUAL_HIGH",
        entry_zone_type="ORDER_BLOCK",
        structure_confirmation="BOS",
        volume_confirmation=True,
        atr_status="EXPANDING",
        trading_session="LONDON",
        btc_market_alignment=True,
        detection_time_utc=detection_time_utc,
        institutional_reason="Confirmed setup facts only.",
        setup_key=setup_key,
        liquidity_sweep_id="sweep-1",
        structure_break_id="break-1",
        entry_zone_id="zone-1",
        retest_id="retest-1",
        created_at_utc=created_at_utc,
    )


@pytest_asyncio.fixture
async def repository(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'signals.db'}"
    manager = DatabaseManager(database_url)
    await manager.initialize()
    yield SignalRepository(manager)
    await manager.dispose()


class TestSignalRepositorySave:
    async def test_save_premium_signal(self, repository):
        signal = _signal(signal_type=SignalType.PREMIUM)
        saved = await repository.save(signal)
        assert saved.trade_id == signal.trade_id

    async def test_save_strong_signal(self, repository):
        signal = _signal(signal_type=SignalType.STRONG, trade_id="SMC-STRONG", setup_key="setup-strong")
        saved = await repository.save(signal)
        assert saved.signal_type == SignalType.STRONG

    async def test_reject_medium_signal(self, repository):
        # A Signal model itself refuses MEDIUM/IGNORE at construction, so this
        # confirms the repository's own defensive check via model_construct.
        signal = Signal.model_construct(
            trade_id="SMC-MEDIUM",
            coin="BTC-USDT",
            direction=Direction.BUY,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            risk_reward_ratio=3.0,
            confidence_score=75.0,
            signal_type=SignalType.MEDIUM,
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
            institutional_reason="reason",
            setup_key="setup-medium",
            liquidity_sweep_id="sweep-1",
            structure_break_id="break-1",
            entry_zone_id="zone-1",
            retest_id="retest-1",
            created_at_utc=UTC_NOW,
        )
        with pytest.raises(ValueError):
            await repository.save(signal)

    async def test_duplicate_trade_id_rejected(self, repository):
        signal_one = _signal(trade_id="SMC-DUP", setup_key="setup-one")
        signal_two = _signal(trade_id="SMC-DUP", setup_key="setup-two")
        await repository.save(signal_one)
        with pytest.raises(DuplicateSignalStorageError):
            await repository.save(signal_two)

    async def test_duplicate_setup_key_rejected(self, repository):
        signal_one = _signal(trade_id="SMC-A", setup_key="setup-shared")
        signal_two = _signal(trade_id="SMC-B", setup_key="setup-shared")
        await repository.save(signal_one)
        with pytest.raises(DuplicateSignalStorageError):
            await repository.save(signal_two)

    async def test_values_preserved_exactly(self, repository):
        signal = _signal()
        await repository.save(signal)
        retrieved = await repository.get_by_trade_id(signal.trade_id)
        assert retrieved.entry_price == signal.entry_price
        assert retrieved.stop_loss == signal.stop_loss
        assert retrieved.take_profit == signal.take_profit
        assert retrieved.risk_reward_ratio == signal.risk_reward_ratio
        assert retrieved.institutional_reason == signal.institutional_reason


class TestSignalRepositoryRetrieval:
    async def test_retrieve_by_trade_id(self, repository):
        signal = _signal(trade_id="SMC-TID")
        await repository.save(signal)
        retrieved = await repository.get_by_trade_id("SMC-TID")
        assert retrieved is not None
        assert retrieved.trade_id == "SMC-TID"

    async def test_retrieve_by_trade_id_not_found(self, repository):
        retrieved = await repository.get_by_trade_id("does-not-exist")
        assert retrieved is None

    async def test_retrieve_by_setup_key(self, repository):
        signal = _signal(setup_key="setup-lookup")
        await repository.save(signal)
        retrieved = await repository.get_by_setup_key("setup-lookup")
        assert retrieved is not None
        assert retrieved.setup_key == "setup-lookup"

    async def test_recent_list_newest_first(self, repository):
        older = _signal(trade_id="SMC-OLD", setup_key="setup-old", created_at_utc=UTC_NOW)
        newer = _signal(
            trade_id="SMC-NEW", setup_key="setup-new", created_at_utc=UTC_NOW + timedelta(minutes=5)
        )
        await repository.save(older)
        await repository.save(newer)
        results = await repository.list_recent(limit=10)
        assert results[0].trade_id == "SMC-NEW"
        assert results[1].trade_id == "SMC-OLD"

    async def test_filter_by_symbol(self, repository):
        btc_signal = _signal(trade_id="SMC-BTC", setup_key="setup-btc", coin="BTC-USDT")
        eth_signal = _signal(trade_id="SMC-ETH", setup_key="setup-eth", coin="ETH-USDT")
        await repository.save(btc_signal)
        await repository.save(eth_signal)
        results = await repository.list_recent(limit=10, symbol="ETH-USDT")
        assert len(results) == 1
        assert results[0].coin == "ETH-USDT"

    async def test_filter_by_signal_type(self, repository):
        premium_signal = _signal(trade_id="SMC-P", setup_key="setup-p", signal_type=SignalType.PREMIUM)
        strong_signal = _signal(trade_id="SMC-S", setup_key="setup-s", signal_type=SignalType.STRONG)
        await repository.save(premium_signal)
        await repository.save(strong_signal)
        results = await repository.list_recent(limit=10, signal_type="STRONG")
        assert len(results) == 1
        assert results[0].signal_type == SignalType.STRONG

    async def test_positive_limit_validation(self, repository):
        with pytest.raises(ValueError):
            await repository.list_recent(limit=0)
        with pytest.raises(ValueError):
            await repository.list_recent(limit=-5)

    async def test_count(self, repository):
        assert await repository.count() == 0
        await repository.save(_signal(trade_id="SMC-1", setup_key="setup-1"))
        await repository.save(_signal(trade_id="SMC-2", setup_key="setup-2"))
        assert await repository.count() == 2


class TestSignalRepositoryTransactions:
    async def test_transaction_rollback_on_duplicate(self, repository):
        signal = _signal(trade_id="SMC-ROLLBACK", setup_key="setup-rollback")
        await repository.save(signal)
        with pytest.raises(DuplicateSignalStorageError):
            await repository.save(signal)
        # Count must remain 1, not 2, confirming the failed insert rolled back.
        assert await repository.count() == 1


class TestDashboardStatus:
    async def test_new_signal_defaults_to_new_status(self, repository):
        signal = _signal(trade_id="SMC-DEFAULT", setup_key="setup-default")
        await repository.save(signal)
        results = await repository.list_recent_with_status(limit=10)
        assert results[0].dashboard_status == DASHBOARD_STATUS_NEW

    async def test_mark_active_sets_active_status(self, repository):
        signal = _signal(trade_id="SMC-ACTIVATE", setup_key="setup-activate")
        await repository.save(signal)

        result = await repository.mark_active("SMC-ACTIVATE")

        assert result.dashboard_status == DASHBOARD_STATUS_ACTIVE
        assert result.signal.trade_id == "SMC-ACTIVATE"

    async def test_mark_active_preserves_every_signal_value_exactly(self, repository):
        signal = _signal(trade_id="SMC-PRESERVE", setup_key="setup-preserve")
        await repository.save(signal)

        result = await repository.mark_active("SMC-PRESERVE")

        assert result.signal.coin == signal.coin
        assert result.signal.direction == signal.direction
        assert result.signal.entry_price == signal.entry_price
        assert result.signal.stop_loss == signal.stop_loss
        assert result.signal.take_profit == signal.take_profit
        assert result.signal.risk_reward_ratio == signal.risk_reward_ratio
        assert result.signal.confidence_score == signal.confidence_score
        assert result.signal.signal_type == signal.signal_type
        assert result.signal.institutional_reason == signal.institutional_reason
        assert result.signal.detection_time_utc == signal.detection_time_utc

    async def test_mark_active_unknown_trade_id_raises(self, repository):
        with pytest.raises(SignalNotFoundError):
            await repository.mark_active("does-not-exist")

    async def test_mark_active_persists_across_new_queries(self, repository):
        # Confirms the status transition is a real, committed DB write --
        # not held only in the in-memory ORM object -- so it survives a
        # fresh query (simulating a dashboard refresh).
        signal = _signal(trade_id="SMC-PERSIST", setup_key="setup-persist")
        await repository.save(signal)
        await repository.mark_active("SMC-PERSIST")

        active_results = await repository.list_recent_with_status(
            limit=10, dashboard_status=DASHBOARD_STATUS_ACTIVE
        )
        new_results = await repository.list_recent_with_status(
            limit=10, dashboard_status=DASHBOARD_STATUS_NEW
        )
        assert len(active_results) == 1
        assert active_results[0].signal.trade_id == "SMC-PERSIST"
        assert new_results == []

    async def test_list_recent_with_status_filters_correctly(self, repository):
        active_signal = _signal(trade_id="SMC-A", setup_key="setup-a")
        new_signal = _signal(trade_id="SMC-B", setup_key="setup-b")
        await repository.save(active_signal)
        await repository.save(new_signal)
        await repository.mark_active("SMC-A")

        active_only = await repository.list_recent_with_status(
            limit=10, dashboard_status=DASHBOARD_STATUS_ACTIVE
        )
        assert len(active_only) == 1
        assert active_only[0].signal.trade_id == "SMC-A"

    async def test_get_by_trade_id_with_status_returns_current_status(self, repository):
        signal = _signal(trade_id="SMC-LOOKUP", setup_key="setup-lookup")
        await repository.save(signal)

        before = await repository.get_by_trade_id_with_status("SMC-LOOKUP")
        assert before.dashboard_status == DASHBOARD_STATUS_NEW

        await repository.mark_active("SMC-LOOKUP")

        after = await repository.get_by_trade_id_with_status("SMC-LOOKUP")
        assert after.dashboard_status == DASHBOARD_STATUS_ACTIVE

    async def test_get_by_trade_id_with_status_not_found(self, repository):
        result = await repository.get_by_trade_id_with_status("does-not-exist")
        assert result is None
