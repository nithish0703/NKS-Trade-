"""
Tests for app.scanner.duplicate_guard.DuplicateSignalGuard.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.risk.results import RiskPlan, RiskPlanStatus
from app.scanner.duplicate_guard import DuplicateGuardError, DuplicateSignalGuard
from app.scanner.pipeline_results import PipelineStatus, StrategyPipelineResult

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _risk_plan():
    risk_plan = MagicMock(spec=RiskPlan)
    risk_plan.status = RiskPlanStatus.VALID
    return risk_plan


def _valid_pipeline_result(
    *,
    symbol: str = "BTC-USDT",
    direction: str = "BUY",
    sweep_id: str = "sweep-1",
    zone_id: str = "zone-1",
    break_id: str = "break-1",
) -> StrategyPipelineResult:
    sweep = MagicMock()
    sweep.sweep_id = sweep_id
    zone = MagicMock()
    zone.zone_id = zone_id
    structure_break = MagicMock()
    structure_break.break_id = break_id

    return StrategyPipelineResult(
        symbol=symbol,
        expected_direction=direction,
        detection_time_utc=UTC_NOW,
        status=PipelineStatus.VALID,
        passed=True,
        stages=[],
        liquidity_sweep=sweep,
        selected_entry_zone=zone,
        selected_structure_break=structure_break,
        risk_plan=_risk_plan(),
    )


def _guard(retention_seconds: int = 3600, maximum_entries: int = 10000) -> DuplicateSignalGuard:
    return DuplicateSignalGuard(retention_seconds=retention_seconds, maximum_entries=maximum_entries)


class TestBuildSetupKey:
    def test_stable_setup_key(self):
        result = _valid_pipeline_result()
        key_one = DuplicateSignalGuard.build_setup_key(result)
        key_two = DuplicateSignalGuard.build_setup_key(result)
        assert key_one == key_two

    def test_different_symbol_changes_key(self):
        result_a = _valid_pipeline_result(symbol="BTC-USDT")
        result_b = _valid_pipeline_result(symbol="ETH-USDT")
        assert DuplicateSignalGuard.build_setup_key(
            result_a
        ) != DuplicateSignalGuard.build_setup_key(result_b)

    def test_different_direction_changes_key(self):
        result_a = _valid_pipeline_result(direction="BUY")
        result_b = _valid_pipeline_result(direction="SELL")
        assert DuplicateSignalGuard.build_setup_key(
            result_a
        ) != DuplicateSignalGuard.build_setup_key(result_b)

    def test_different_sweep_id_changes_key(self):
        result_a = _valid_pipeline_result(sweep_id="sweep-1")
        result_b = _valid_pipeline_result(sweep_id="sweep-2")
        assert DuplicateSignalGuard.build_setup_key(
            result_a
        ) != DuplicateSignalGuard.build_setup_key(result_b)

    def test_different_zone_id_changes_key(self):
        result_a = _valid_pipeline_result(zone_id="zone-1")
        result_b = _valid_pipeline_result(zone_id="zone-2")
        assert DuplicateSignalGuard.build_setup_key(
            result_a
        ) != DuplicateSignalGuard.build_setup_key(result_b)

    def test_different_break_id_changes_key(self):
        result_a = _valid_pipeline_result(break_id="break-1")
        result_b = _valid_pipeline_result(break_id="break-2")
        assert DuplicateSignalGuard.build_setup_key(
            result_a
        ) != DuplicateSignalGuard.build_setup_key(result_b)

    def test_entry_price_fluctuation_does_not_change_setup_key(self):
        # entry price lives on risk_plan, which is not part of the setup identity.
        result_a = _valid_pipeline_result()
        alternate_risk_plan = MagicMock(spec=RiskPlan)
        alternate_risk_plan.status = RiskPlanStatus.VALID
        alternate_risk_plan.entry_price = 99999.0
        result_b = result_a.model_copy(update={"risk_plan": alternate_risk_plan})
        assert DuplicateSignalGuard.build_setup_key(
            result_a
        ) == DuplicateSignalGuard.build_setup_key(result_b)

    def test_invalid_pipeline_result_rejected(self):
        result = _valid_pipeline_result()
        rejected = result.model_copy(
            update={
                "status": PipelineStatus.REJECTED,
                "passed": False,
                "failed_layer": "MARKET_REGIME",
                "rejection_reason": "not trending",
            }
        )
        with pytest.raises(DuplicateGuardError):
            DuplicateSignalGuard.build_setup_key(rejected)

    def test_missing_setup_identity_rejected(self):
        result = _valid_pipeline_result()
        incomplete = result.model_copy(update={"liquidity_sweep": None})
        with pytest.raises(DuplicateGuardError):
            DuplicateSignalGuard.build_setup_key(incomplete)


class TestDuplicateDetection:
    pytestmark = pytest.mark.asyncio

    async def test_first_setup_is_not_duplicate(self):
        guard = _guard()
        result = _valid_pipeline_result()
        duplicate, _ = await guard.check_and_register(result, UTC_NOW)
        assert duplicate is False

    async def test_second_identical_setup_is_duplicate(self):
        guard = _guard()
        result = _valid_pipeline_result()
        await guard.check_and_register(result, UTC_NOW)
        duplicate, _ = await guard.check_and_register(result, UTC_NOW + timedelta(seconds=5))
        assert duplicate is True

    async def test_expired_key_becomes_valid_again(self):
        guard = _guard(retention_seconds=60)
        result = _valid_pipeline_result()
        await guard.check_and_register(result, UTC_NOW)
        later = UTC_NOW + timedelta(seconds=120)
        duplicate, _ = await guard.check_and_register(result, later)
        assert duplicate is False

    async def test_cleanup_removes_expired_keys(self):
        guard = _guard(retention_seconds=60)
        result = _valid_pipeline_result()
        await guard.register(DuplicateSignalGuard.build_setup_key(result), UTC_NOW)
        removed = await guard.cleanup(UTC_NOW + timedelta(seconds=120))
        assert removed == 1
        assert len(guard._entries) == 0

    async def test_maximum_entries_enforced(self):
        guard = _guard(retention_seconds=3600, maximum_entries=2)
        for index in range(5):
            await guard.register(f"key-{index}", UTC_NOW + timedelta(seconds=index))
        assert len(guard._entries) == 2
        # Only the most recently registered entries survive.
        assert "key-4" in guard._entries
        assert "key-3" in guard._entries

    async def test_concurrent_access_is_safe(self):
        guard = _guard()
        result = _valid_pipeline_result()

        outcomes = await asyncio.gather(
            *[guard.check_and_register(result, UTC_NOW) for _ in range(20)]
        )
        duplicate_flags = [outcome[0] for outcome in outcomes]
        # Exactly one caller should have registered the setup as new.
        assert duplicate_flags.count(False) == 1
        assert duplicate_flags.count(True) == 19

    async def test_inputs_not_mutated(self):
        guard = _guard()
        result = _valid_pipeline_result()
        original_symbol = result.symbol
        await guard.check_and_register(result, UTC_NOW)
        assert result.symbol == original_symbol


class TestConstructorValidation:
    def test_non_positive_retention_seconds_rejected(self):
        with pytest.raises(ValueError):
            DuplicateSignalGuard(retention_seconds=0, maximum_entries=10)

    def test_non_positive_maximum_entries_rejected(self):
        with pytest.raises(ValueError):
            DuplicateSignalGuard(retention_seconds=60, maximum_entries=0)
