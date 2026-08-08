"""
Tests for app.analytics.baseline.save_baseline.
"""

import json
from datetime import datetime, timezone

import pytest

from app.analytics.baseline import BASELINE_SCHEMA_VERSION, save_baseline
from app.storage.analytics_repository import AnalyticsRepository
from app.storage.database import DatabaseManager
from app.storage.signal_repository import SignalRepository

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


class TestSaveBaseline:
    async def test_writes_a_json_file_under_output_dir(self, tmp_path):
        manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'db.db'}")
        await manager.initialize()
        try:
            analytics_repository = AnalyticsRepository(manager, enabled=True)
            signal_repository = SignalRepository(manager)
            output_dir = tmp_path / "baselines"

            file_path = await save_baseline(
                name="pre_changes",
                analytics_repository=analytics_repository,
                signal_repository=signal_repository,
                window_days=7,
                output_dir=output_dir,
                now=UTC_NOW,
            )

            assert file_path.exists()
            assert file_path.parent == output_dir
            assert "pre_changes" in file_path.name
        finally:
            await manager.dispose()

    async def test_file_contains_expected_schema(self, tmp_path):
        manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'db.db'}")
        await manager.initialize()
        try:
            analytics_repository = AnalyticsRepository(manager, enabled=True)
            signal_repository = SignalRepository(manager)

            file_path = await save_baseline(
                name="my_baseline",
                analytics_repository=analytics_repository,
                signal_repository=signal_repository,
                window_days=7,
                output_dir=tmp_path / "baselines",
                now=UTC_NOW,
            )

            payload = json.loads(file_path.read_text(encoding="utf-8"))
            assert payload["schema_version"] == BASELINE_SCHEMA_VERSION
            assert payload["name"] == "my_baseline"
            assert payload["window_days"] == 7
            assert "funnel" in payload
            assert "performance" in payload
            assert payload["funnel"]["confirmed_count"] == 0
            assert payload["performance"]["overall"]["trade_count"] == 0
        finally:
            await manager.dispose()

    async def test_two_saves_under_the_same_name_produce_different_files(self, tmp_path):
        manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'db.db'}")
        await manager.initialize()
        try:
            analytics_repository = AnalyticsRepository(manager, enabled=True)
            signal_repository = SignalRepository(manager)
            output_dir = tmp_path / "baselines"

            first = await save_baseline(
                name="repeat",
                analytics_repository=analytics_repository,
                signal_repository=signal_repository,
                output_dir=output_dir,
                now=UTC_NOW,
            )
            second = await save_baseline(
                name="repeat",
                analytics_repository=analytics_repository,
                signal_repository=signal_repository,
                output_dir=output_dir,
                now=datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
            )

            assert first != second
            assert first.exists()
            assert second.exists()
        finally:
            await manager.dispose()

    async def test_empty_name_rejected(self, tmp_path):
        manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'db.db'}")
        await manager.initialize()
        try:
            analytics_repository = AnalyticsRepository(manager, enabled=True)
            signal_repository = SignalRepository(manager)
            with pytest.raises(ValueError):
                await save_baseline(
                    name="",
                    analytics_repository=analytics_repository,
                    signal_repository=signal_repository,
                    output_dir=tmp_path / "baselines",
                    now=UTC_NOW,
                )
        finally:
            await manager.dispose()
