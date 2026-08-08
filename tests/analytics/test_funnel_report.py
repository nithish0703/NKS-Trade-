"""
Unit tests for app.analytics.funnel_report's pure aggregation functions
and orchestration.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.analytics.funnel_report import (
    RejectionReasonCount,
    StageFunnelRow,
    compute_confirmed_count,
    compute_stage_funnel,
    compute_top_rejection_reasons,
    format_funnel_report,
    generate_funnel_report,
)
from app.storage.analytics_repository import RejectionRecord, StageAnalyticsRecord

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _stage_record(layer_name: str, *, passed: bool, duration_ms: float = 1.0, symbol="BTC-USDT") -> StageAnalyticsRecord:
    return StageAnalyticsRecord(
        symbol=symbol,
        stage_order={"HTF_BIAS": 1, "LIQUIDITY_SWEEP": 2, "BOS": 3, "IFVG": 4, "ORDER_FLOW": 5, "RISK_MANAGEMENT": 6}[
            layer_name
        ],
        layer_name=layer_name,
        passed=passed,
        duration_ms=duration_ms,
        scan_time_utc=UTC_NOW,
    )


def _rejection_record(failed_layer: str, reason: str, symbol="BTC-USDT") -> RejectionRecord:
    return RejectionRecord(
        symbol=symbol,
        failed_layer=failed_layer,
        rejection_reason=reason,
        detection_time_utc=UTC_NOW,
        created_at_utc=UTC_NOW,
    )


class TestComputeStageFunnel:
    def test_empty_input_gives_empty_rows(self):
        assert compute_stage_funnel([]) == []

    def test_reached_passed_and_pass_percentage(self):
        records = [
            _stage_record("HTF_BIAS", passed=True),
            _stage_record("HTF_BIAS", passed=True),
            _stage_record("HTF_BIAS", passed=False),
            _stage_record("HTF_BIAS", passed=False),
        ]
        rows = compute_stage_funnel(records)
        assert len(rows) == 1
        row = rows[0]
        assert row.layer_name == "HTF_BIAS"
        assert row.reached == 4
        assert row.passed == 2
        assert row.pass_percentage == 50.0

    def test_average_duration(self):
        records = [
            _stage_record("BOS", passed=True, duration_ms=2.0),
            _stage_record("BOS", passed=True, duration_ms=4.0),
        ]
        rows = compute_stage_funnel(records)
        assert rows[0].average_duration_ms == 3.0

    def test_rows_ordered_by_canonical_stage_order_not_insertion_order(self):
        records = [
            _stage_record("BOS", passed=True),
            _stage_record("HTF_BIAS", passed=True),
            _stage_record("LIQUIDITY_SWEEP", passed=True),
        ]
        rows = compute_stage_funnel(records)
        assert [r.layer_name for r in rows] == ["HTF_BIAS", "LIQUIDITY_SWEEP", "BOS"]

    def test_a_stage_with_zero_reached_is_omitted_not_fabricated(self):
        records = [_stage_record("HTF_BIAS", passed=True)]
        rows = compute_stage_funnel(records)
        assert [r.layer_name for r in rows] == ["HTF_BIAS"]
        assert "RISK_MANAGEMENT" not in [r.layer_name for r in rows]


class TestComputeConfirmedCount:
    def test_confirmed_equals_risk_management_passed_count(self):
        rows = [
            StageFunnelRow(layer_name="HTF_BIAS", reached=10, passed=4, pass_percentage=40.0, average_duration_ms=1.0),
            StageFunnelRow(
                layer_name="RISK_MANAGEMENT", reached=4, passed=3, pass_percentage=75.0, average_duration_ms=1.0
            ),
        ]
        assert compute_confirmed_count(rows) == 3

    def test_no_risk_management_row_gives_zero(self):
        rows = [
            StageFunnelRow(layer_name="HTF_BIAS", reached=10, passed=4, pass_percentage=40.0, average_duration_ms=1.0)
        ]
        assert compute_confirmed_count(rows) == 0

    def test_empty_rows_gives_zero(self):
        assert compute_confirmed_count([]) == 0


class TestComputeTopRejectionReasons:
    def test_groups_by_failed_layer(self):
        records = [
            _rejection_record("HTF_BIAS", "EMA200 slope is FLAT."),
            _rejection_record("LIQUIDITY_SWEEP", "No sweep found."),
        ]
        result = compute_top_rejection_reasons(records)
        assert set(result.keys()) == {"HTF_BIAS", "LIQUIDITY_SWEEP"}

    def test_counts_and_orders_by_frequency(self):
        records = [
            _rejection_record("HTF_BIAS", "reason A"),
            _rejection_record("HTF_BIAS", "reason A"),
            _rejection_record("HTF_BIAS", "reason A"),
            _rejection_record("HTF_BIAS", "reason B"),
        ]
        result = compute_top_rejection_reasons(records)
        reasons = result["HTF_BIAS"]
        assert reasons[0] == RejectionReasonCount(reason="reason A", count=3)
        assert reasons[1] == RejectionReasonCount(reason="reason B", count=1)

    def test_respects_top_n(self):
        records = [_rejection_record("HTF_BIAS", f"reason {i}") for i in range(15)]
        result = compute_top_rejection_reasons(records, top_n=10)
        assert len(result["HTF_BIAS"]) == 10

    def test_empty_input_gives_empty_dict(self):
        assert compute_top_rejection_reasons([]) == {}


class TestGenerateFunnelReport:
    async def test_empty_repository_gives_no_stages_and_zero_confirmed(self):
        repository = AsyncMock()
        repository.list_stage_results_since = AsyncMock(return_value=[])
        repository.list_rejections_since = AsyncMock(return_value=[])

        report = await generate_funnel_report(repository, window_days=7, now=UTC_NOW)

        assert report.stages == []
        assert report.confirmed_count == 0
        assert report.window_start_utc == UTC_NOW - timedelta(days=7)
        assert report.window_end_utc == UTC_NOW

    async def test_window_is_passed_through_to_repository_calls(self):
        repository = AsyncMock()
        repository.list_stage_results_since = AsyncMock(return_value=[])
        repository.list_rejections_since = AsyncMock(return_value=[])

        await generate_funnel_report(repository, window_days=3, now=UTC_NOW)

        expected_since = UTC_NOW - timedelta(days=3)
        repository.list_stage_results_since.assert_awaited_once_with(expected_since)
        repository.list_rejections_since.assert_awaited_once_with(expected_since)

    async def test_full_report_reconciles_confirmed_with_stage_data(self):
        repository = AsyncMock()
        repository.list_stage_results_since = AsyncMock(
            return_value=[
                _stage_record("HTF_BIAS", passed=True),
                _stage_record("RISK_MANAGEMENT", passed=True),
            ]
        )
        repository.list_rejections_since = AsyncMock(return_value=[])

        report = await generate_funnel_report(repository, window_days=7, now=UTC_NOW)

        assert report.confirmed_count == 1


class TestFormatFunnelReport:
    async def test_empty_report_prints_no_data_yet(self):
        repository = AsyncMock()
        repository.list_stage_results_since = AsyncMock(return_value=[])
        repository.list_rejections_since = AsyncMock(return_value=[])
        report = await generate_funnel_report(repository, now=UTC_NOW)

        text = format_funnel_report(report)

        assert "no data yet" in text

    def test_non_empty_report_includes_stage_names_and_confirmed(self):
        rows = [
            StageFunnelRow(layer_name="HTF_BIAS", reached=10, passed=4, pass_percentage=40.0, average_duration_ms=1.0)
        ]
        from app.analytics.funnel_report import FunnelReport

        report = FunnelReport(
            window_start_utc=UTC_NOW - timedelta(days=7),
            window_end_utc=UTC_NOW,
            stages=rows,
            confirmed_count=2,
            top_rejection_reasons_by_stage={"HTF_BIAS": [RejectionReasonCount(reason="flat", count=5)]},
        )

        text = format_funnel_report(report)

        assert "HTF_BIAS" in text
        assert "CONFIRMED" in text
        assert "flat" in text
