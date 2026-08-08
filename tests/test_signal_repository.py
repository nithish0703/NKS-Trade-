"""
Tests for app.storage.signal_repository.SignalRepository.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.models.signal import Direction, Signal, SignalStatus
from app.storage.database import DatabaseManager
from app.storage.signal_repository import (
    DASHBOARD_STATUS_ACTIVE,
    DASHBOARD_STATUS_CLOSED_LOSS,
    DASHBOARD_STATUS_CLOSED_WIN,
    DASHBOARD_STATUS_NEW,
    OUTCOME_SOURCE_MANUAL_ACTIVATION,
    OUTCOME_SOURCE_PASSIVE_TRACKING,
    PASSIVE_OUTCOME_LOSS,
    PASSIVE_OUTCOME_TIMEOUT,
    PASSIVE_OUTCOME_UNRESOLVED,
    PASSIVE_OUTCOME_WIN,
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
        status=SignalStatus.CONFIRMED,
        liquidity_type="EQUAL_HIGH",
        entry_zone_type="ORDER_BLOCK",
        structure_confirmation="BOS",
        detection_time_utc=detection_time_utc,
        institutional_reason="Confirmed setup facts only.",
        setup_key=setup_key,
        liquidity_sweep_id="sweep-1",
        structure_break_id="break-1",
        entry_zone_id="zone-1",
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
    async def test_save_confirmed_signal(self, repository):
        signal = _signal()
        saved = await repository.save(signal)
        assert saved.trade_id == signal.trade_id

    async def test_reject_non_confirmed_signal(self, repository):
        # A Signal model itself refuses REJECTED at construction, so this
        # confirms the repository's own defensive check via model_construct.
        signal = Signal.model_construct(
            trade_id="SMC-REJECTED",
            coin="BTC-USDT",
            direction=Direction.BUY,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            risk_reward_ratio=3.0,
            status=SignalStatus.REJECTED,
            liquidity_type="EQUAL_HIGH",
            entry_zone_type="ORDER_BLOCK",
            structure_confirmation="BOS",
            detection_time_utc=UTC_NOW,
            institutional_reason="reason",
            setup_key="setup-rejected",
            liquidity_sweep_id="sweep-1",
            structure_break_id="break-1",
            entry_zone_id="zone-1",
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
        assert result.signal.status == signal.status
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


class TestAnalyticsFieldsOnSave:
    async def test_analytics_fields_persisted(self, repository):
        signal = _signal(trade_id="SMC-ANALYTICS", setup_key="setup-analytics")
        await repository.save(
            signal,
            order_flow_confidence="HIGH",
            entry_grade="A",
            stop_loss_source="LIQUIDITY_SWEEP",
        )
        closed = await repository.close_passive(
            "SMC-ANALYTICS", outcome=PASSIVE_OUTCOME_WIN, exit_price=110.0, closed_at_utc=UTC_NOW
        )
        assert closed.signal.trade_id == "SMC-ANALYTICS"
        records = await repository.list_passively_closed()
        record = next(r for r in records if r.signal.trade_id == "SMC-ANALYTICS")
        assert record.order_flow_confidence == "HIGH"
        assert record.entry_grade == "A"
        assert record.stop_loss_source == "LIQUIDITY_SWEEP"

    async def test_analytics_fields_default_to_none(self, repository):
        signal = _signal(trade_id="SMC-NOANALYTICS", setup_key="setup-noanalytics")
        await repository.save(signal)
        await repository.close_passive(
            "SMC-NOANALYTICS", outcome=PASSIVE_OUTCOME_LOSS, exit_price=95.0, closed_at_utc=UTC_NOW
        )
        records = await repository.list_passively_closed()
        record = next(r for r in records if r.signal.trade_id == "SMC-NOANALYTICS")
        assert record.order_flow_confidence is None
        assert record.entry_grade is None
        assert record.stop_loss_source is None


class TestPassiveOutcomeTracking:
    async def test_new_signal_is_not_passively_closed(self, repository):
        await repository.save(_signal(trade_id="SMC-OPEN", setup_key="setup-open"))
        open_signals = await repository.list_not_passively_closed()
        assert any(s.trade_id == "SMC-OPEN" for s in open_signals)

    async def test_close_passive_removes_from_not_closed_list(self, repository):
        await repository.save(_signal(trade_id="SMC-CLOSE", setup_key="setup-close"))
        await repository.close_passive(
            "SMC-CLOSE", outcome=PASSIVE_OUTCOME_WIN, exit_price=110.0, closed_at_utc=UTC_NOW
        )
        open_signals = await repository.list_not_passively_closed()
        assert all(s.trade_id != "SMC-CLOSE" for s in open_signals)

    async def test_close_passive_leaves_never_activated_signal_as_new(self, repository):
        # A signal that was never dashboard-ACTIVE must not be mirrored
        # onto dashboard_status at all -- closing it passively leaves
        # dashboard_status exactly as it was (NEW).
        await repository.save(_signal(trade_id="SMC-INDEPENDENT", setup_key="setup-independent"))
        result = await repository.close_passive(
            "SMC-INDEPENDENT", outcome=PASSIVE_OUTCOME_WIN, exit_price=110.0, closed_at_utc=UTC_NOW
        )
        with_status = await repository.get_by_trade_id_with_status("SMC-INDEPENDENT")
        assert with_status.dashboard_status == DASHBOARD_STATUS_NEW
        assert result.outcome_source == OUTCOME_SOURCE_PASSIVE_TRACKING

    async def test_close_passive_unknown_trade_id_raises(self, repository):
        with pytest.raises(SignalNotFoundError):
            await repository.close_passive(
                "does-not-exist", outcome=PASSIVE_OUTCOME_WIN, exit_price=110.0, closed_at_utc=UTC_NOW
            )

    async def test_close_passive_rejects_invalid_outcome(self, repository):
        await repository.save(_signal(trade_id="SMC-BADOUTCOME", setup_key="setup-badoutcome"))
        with pytest.raises(ValueError):
            await repository.close_passive(
                "SMC-BADOUTCOME", outcome="MAYBE", exit_price=110.0, closed_at_utc=UTC_NOW
            )

    async def test_list_passively_closed_filters_by_since(self, repository):
        early = UTC_NOW
        late = UTC_NOW + timedelta(days=2)

        await repository.save(_signal(trade_id="SMC-EARLY", setup_key="setup-early"))
        await repository.close_passive(
            "SMC-EARLY", outcome=PASSIVE_OUTCOME_WIN, exit_price=110.0, closed_at_utc=early
        )
        await repository.save(_signal(trade_id="SMC-LATE", setup_key="setup-late"))
        await repository.close_passive(
            "SMC-LATE", outcome=PASSIVE_OUTCOME_LOSS, exit_price=95.0, closed_at_utc=late
        )

        recent_only = await repository.list_passively_closed(since=UTC_NOW + timedelta(days=1))
        assert {r.signal.trade_id for r in recent_only} == {"SMC-LATE"}

        everything = await repository.list_passively_closed()
        assert {r.signal.trade_id for r in everything} == {"SMC-EARLY", "SMC-LATE"}

    async def test_active_signal_close_mirrors_onto_dashboard_status(self, repository):
        # The merged-monitor contract: a signal that IS dashboard-ACTIVE
        # at close time gets its WIN/LOSS mirrored onto dashboard_status/
        # outcome/exit_price/closed_at_utc in the SAME write, and is
        # tagged outcome_source=MANUAL_ACTIVATION -- there is no second,
        # separately-scheduled close for the dashboard "Trade" workflow.
        await repository.save(_signal(trade_id="SMC-ACTIVEANDPASSIVE", setup_key="setup-active-passive"))
        await repository.mark_active("SMC-ACTIVEANDPASSIVE")
        result = await repository.close_passive(
            "SMC-ACTIVEANDPASSIVE", outcome=PASSIVE_OUTCOME_WIN, exit_price=110.0, closed_at_utc=UTC_NOW
        )

        assert result.outcome_source == OUTCOME_SOURCE_MANUAL_ACTIVATION
        assert result.dashboard_status == DASHBOARD_STATUS_CLOSED_WIN

        with_status = await repository.get_by_trade_id_with_status("SMC-ACTIVEANDPASSIVE")
        assert with_status.dashboard_status == DASHBOARD_STATUS_CLOSED_WIN

    async def test_active_signal_loss_mirrors_as_closed_loss(self, repository):
        await repository.save(_signal(trade_id="SMC-ACTIVELOSS", setup_key="setup-active-loss"))
        await repository.mark_active("SMC-ACTIVELOSS")
        result = await repository.close_passive(
            "SMC-ACTIVELOSS", outcome=PASSIVE_OUTCOME_LOSS, exit_price=95.0, closed_at_utc=UTC_NOW
        )
        assert result.dashboard_status == DASHBOARD_STATUS_CLOSED_LOSS

    async def test_active_signal_timeout_is_never_mirrored_to_dashboard(self, repository):
        # TIMEOUT has no dashboard_status equivalent: an ACTIVE signal
        # that times out stays ACTIVE on the dashboard (still resolved
        # for analytics via passive_outcome, just not reflected in the
        # dashboard UI, which has no TIMEOUT concept).
        await repository.save(_signal(trade_id="SMC-ACTIVETIMEOUT", setup_key="setup-active-timeout"))
        await repository.mark_active("SMC-ACTIVETIMEOUT")
        result = await repository.close_passive(
            "SMC-ACTIVETIMEOUT", outcome=PASSIVE_OUTCOME_TIMEOUT, exit_price=100.0, closed_at_utc=UTC_NOW
        )

        assert result.dashboard_status == DASHBOARD_STATUS_ACTIVE
        assert result.outcome_source == OUTCOME_SOURCE_MANUAL_ACTIVATION

        with_status = await repository.get_by_trade_id_with_status("SMC-ACTIVETIMEOUT")
        assert with_status.dashboard_status == DASHBOARD_STATUS_ACTIVE

    async def test_close_passive_accepts_timeout_outcome(self, repository):
        await repository.save(_signal(trade_id="SMC-TIMEOUT", setup_key="setup-timeout"))
        result = await repository.close_passive(
            "SMC-TIMEOUT", outcome=PASSIVE_OUTCOME_TIMEOUT, exit_price=100.0, closed_at_utc=UTC_NOW
        )
        assert result.passive_outcome == PASSIVE_OUTCOME_TIMEOUT

    async def test_close_passive_accepts_unresolved_with_null_exit_price(self, repository):
        await repository.save(_signal(trade_id="SMC-UNRESOLVED", setup_key="setup-unresolved"))
        result = await repository.close_passive(
            "SMC-UNRESOLVED", outcome=PASSIVE_OUTCOME_UNRESOLVED, exit_price=None, closed_at_utc=UTC_NOW
        )
        assert result.passive_outcome == PASSIVE_OUTCOME_UNRESOLVED

        records = await repository.list_passively_closed()
        record = next(r for r in records if r.signal.trade_id == "SMC-UNRESOLVED")
        assert record.passive_exit_price is None

    async def test_close_passive_requires_exit_price_for_non_unresolved_outcomes(self, repository):
        await repository.save(_signal(trade_id="SMC-NOEXIT", setup_key="setup-noexit"))
        with pytest.raises(ValueError):
            await repository.close_passive(
                "SMC-NOEXIT", outcome=PASSIVE_OUTCOME_WIN, exit_price=None, closed_at_utc=UTC_NOW
            )

    async def test_active_signal_unresolved_is_never_mirrored_to_dashboard(self, repository):
        await repository.save(_signal(trade_id="SMC-ACTIVEUNRESOLVED", setup_key="setup-active-unresolved"))
        await repository.mark_active("SMC-ACTIVEUNRESOLVED")
        result = await repository.close_passive(
            "SMC-ACTIVEUNRESOLVED",
            outcome=PASSIVE_OUTCOME_UNRESOLVED,
            exit_price=None,
            closed_at_utc=UTC_NOW,
        )
        assert result.dashboard_status == DASHBOARD_STATUS_ACTIVE

    async def test_list_not_passively_closed_orders_oldest_detected_first(self, repository):
        # On overflow past `limit`, the oldest (closest to timing out)
        # signals must be the ones guaranteed to be returned -- never an
        # arbitrary DB-order subset.
        newer = _signal(
            trade_id="SMC-NEWER", setup_key="setup-newer", detection_time_utc=UTC_NOW + timedelta(hours=1)
        )
        older = _signal(trade_id="SMC-OLDER", setup_key="setup-older", detection_time_utc=UTC_NOW)
        await repository.save(newer)
        await repository.save(older)

        limited = await repository.list_not_passively_closed(limit=1)

        assert len(limited) == 1
        assert limited[0].trade_id == "SMC-OLDER"
