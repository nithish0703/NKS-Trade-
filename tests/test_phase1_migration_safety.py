"""
Regression coverage for the "safe on write, unsafe on read" migration
class of bug: a pre-existing row can have any Phase-1-and-later
NULLable column left NULL (order_flow_confidence, entry_grade,
stop_loss_source, outcome_source, and -- in the most extreme case --
even passive_exit_price/passive_closed_at_utc), and every reader
downstream of it (funnel, performance, baseline) must parse that row
without raising and without silently miscounting it into a bucket it
doesn't belong to.

Seeds rows directly via the ORM (bypassing SignalRepository.save()/
close_passive(), which always populate every current-schema column
together) to reproduce exactly the column combinations an older build
of this code could have left on disk.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from app.analytics.baseline import save_baseline
from app.analytics.funnel_report import format_funnel_report, generate_funnel_report
from app.analytics.performance_report import format_performance_report, generate_performance_report
from app.storage.analytics_repository import AnalyticsRepository
from app.storage.database import DatabaseManager
from app.storage.models import SignalRecord
from app.storage.signal_repository import PASSIVE_OUTCOME_WIN, SignalRepository

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def database_manager(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}"
    manager = DatabaseManager(database_url)
    await manager.initialize()
    yield manager
    await manager.dispose()


async def _insert_legacy_row(database_manager: DatabaseManager, *, trade_id: str, **overrides) -> None:
    """Insert a SignalRecord directly, bypassing every current-code write path."""
    defaults = dict(
        trade_id=trade_id,
        setup_key=f"setup-{trade_id}",
        coin="BTC-USDT",
        direction="BUY",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_reward_ratio=3.0,
        status="CONFIRMED",
        liquidity_type="EQUAL_HIGH",
        entry_zone_type="ORDER_BLOCK",
        structure_confirmation="BOS",
        detection_time_utc=UTC_NOW,
        institutional_reason="legacy row",
        liquidity_sweep_id="sweep-1",
        structure_break_id="break-1",
        entry_zone_id="zone-1",
        created_at_utc=UTC_NOW,
        dashboard_status="NEW",
        # Every column added after the original schema, left exactly as
        # a pre-existing row would have them: NULL.
        outcome=None,
        exit_price=None,
        closed_at_utc=None,
        order_flow_confidence=None,
        entry_grade=None,
        stop_loss_source=None,
        passive_outcome=None,
        passive_exit_price=None,
        passive_closed_at_utc=None,
        outcome_source=None,
    )
    defaults.update(overrides)
    async with database_manager.session_scope() as session:
        session.add(SignalRecord(**defaults))
        await session.commit()


class TestPreExistingOpenSignal:
    """A row from before ANY Phase 1 column existed: every new column NULL, never closed."""

    async def test_funnel_report_does_not_raise(self, database_manager):
        await _insert_legacy_row(database_manager, trade_id="SMC-LEGACY-OPEN")
        analytics_repository = AnalyticsRepository(database_manager, enabled=True)

        report = await generate_funnel_report(analytics_repository, now=UTC_NOW)
        text = format_funnel_report(report)

        assert isinstance(text, str)

    async def test_performance_report_does_not_raise(self, database_manager):
        await _insert_legacy_row(database_manager, trade_id="SMC-LEGACY-OPEN")
        signal_repository = SignalRepository(database_manager)

        report = await generate_performance_report(signal_repository, now=UTC_NOW)
        text = format_performance_report(report)

        assert report.still_open_count == 1
        assert report.overall.trade_count == 0
        assert isinstance(text, str)

    async def test_baseline_save_does_not_raise(self, database_manager, tmp_path):
        await _insert_legacy_row(database_manager, trade_id="SMC-LEGACY-OPEN")
        signal_repository = SignalRepository(database_manager)
        analytics_repository = AnalyticsRepository(database_manager, enabled=True)

        path = await save_baseline(
            name="legacy-safety",
            analytics_repository=analytics_repository,
            signal_repository=signal_repository,
            output_dir=tmp_path / "baselines",
            now=UTC_NOW,
        )

        assert path.exists()


class TestPreOutcomeSourceClosedSignal:
    """
    A row closed by a build of close_passive() that predates the
    outcome_source column: passive_outcome/passive_exit_price/
    passive_closed_at_utc are all populated (the old code always wrote
    them together), but outcome_source is NULL. This is the exact shape
    that crashed `python main.py performance`.
    """

    async def test_performance_report_counts_it_as_unknown_not_as_a_bucket(self, database_manager):
        await _insert_legacy_row(
            database_manager,
            trade_id="SMC-LEGACY-CLOSED",
            passive_outcome=PASSIVE_OUTCOME_WIN,
            passive_exit_price=110.0,
            passive_closed_at_utc=UTC_NOW,
            outcome_source=None,
        )
        signal_repository = SignalRepository(database_manager)

        report = await generate_performance_report(signal_repository, now=UTC_NOW)
        text = format_performance_report(report)

        # A real, computable R -- counted in stats -- but never
        # miscounted into MANUAL_ACTIVATION or PASSIVE_TRACKING.
        assert report.overall.trade_count == 1
        assert report.manual_activation_count == 0
        assert report.passive_tracking_count == 0
        assert report.unknown_outcome_source_count == 1
        assert "UNKNOWN" in report.by_outcome_source
        assert "UNKNOWN" in text

    async def test_baseline_save_does_not_raise(self, database_manager, tmp_path):
        await _insert_legacy_row(
            database_manager,
            trade_id="SMC-LEGACY-CLOSED",
            passive_outcome=PASSIVE_OUTCOME_WIN,
            passive_exit_price=110.0,
            passive_closed_at_utc=UTC_NOW,
            outcome_source=None,
        )
        signal_repository = SignalRepository(database_manager)
        analytics_repository = AnalyticsRepository(database_manager, enabled=True)

        path = await save_baseline(
            name="legacy-closed-safety",
            analytics_repository=analytics_repository,
            signal_repository=signal_repository,
            output_dir=tmp_path / "baselines",
            now=UTC_NOW,
        )

        assert path.exists()


class TestExtremeLegacyIncompleteRow:
    """
    Worst case: passive_outcome is set but passive_exit_price/
    passive_closed_at_utc are somehow NULL too (no known current write
    path produces this, but nothing at the DB schema level forbids it).
    Must be excluded from R/expectancy math, never crash, never
    fabricate a value.
    """

    async def test_performance_report_excludes_it_without_raising(self, database_manager):
        await _insert_legacy_row(
            database_manager,
            trade_id="SMC-LEGACY-INCOMPLETE",
            passive_outcome=PASSIVE_OUTCOME_WIN,
            passive_exit_price=None,
            passive_closed_at_utc=None,
            outcome_source=None,
        )
        signal_repository = SignalRepository(database_manager)

        report = await generate_performance_report(signal_repository, now=UTC_NOW)
        format_performance_report(report)  # must not raise

        assert report.overall.trade_count == 0
        assert report.unresolved_count == 1
