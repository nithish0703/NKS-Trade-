"""
Rejection-funnel report: how many scans reached and passed each
pipeline stage over a time window, plus the most frequent rejection
reasons per stage. Analysis only -- reads persisted analytics data,
never recalculates or influences any strategy decision.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.storage.analytics_repository import AnalyticsRepository, RejectionRecord, StageAnalyticsRecord

DEFAULT_WINDOW_DAYS = 7
DEFAULT_TOP_REASONS_PER_STAGE = 10

# Canonical stage order for report display, matching
# app.strategy_pipeline.engine._STAGE_DEFINITIONS.
STAGE_DISPLAY_ORDER = (
    "HTF_BIAS",
    "LIQUIDITY_SWEEP",
    "BOS",
    "IFVG",
    "ORDER_FLOW",
    "RISK_MANAGEMENT",
)


class StageFunnelRow(BaseModel):
    """Reached/passed/pass%/average-duration for a single pipeline stage."""

    model_config = ConfigDict(frozen=True)

    layer_name: str
    reached: int
    passed: int
    pass_percentage: Optional[float]
    average_duration_ms: Optional[float]


class RejectionReasonCount(BaseModel):
    """A single rejection_reason string and how many times it occurred."""

    model_config = ConfigDict(frozen=True)

    reason: str
    count: int


class FunnelReport(BaseModel):
    """Complete rejection-funnel report for a time window."""

    model_config = ConfigDict(frozen=True)

    window_start_utc: datetime
    window_end_utc: datetime
    stages: list[StageFunnelRow]
    confirmed_count: int
    top_rejection_reasons_by_stage: dict[str, list[RejectionReasonCount]]


def compute_stage_funnel(stage_records: list[StageAnalyticsRecord]) -> list[StageFunnelRow]:
    """
    Pure aggregation: reached/passed/pass%/average-duration per stage,
    ordered per STAGE_DISPLAY_ORDER. A stage with zero reached rows in
    this window is omitted entirely -- never fabricates a zero-row
    entry for a stage that simply never ran.
    """
    reached: dict[str, int] = {}
    passed: dict[str, int] = {}
    duration_sum: dict[str, float] = {}

    for record in stage_records:
        reached[record.layer_name] = reached.get(record.layer_name, 0) + 1
        if record.passed:
            passed[record.layer_name] = passed.get(record.layer_name, 0) + 1
        duration_sum[record.layer_name] = duration_sum.get(record.layer_name, 0.0) + record.duration_ms

    rows: list[StageFunnelRow] = []
    for layer_name in STAGE_DISPLAY_ORDER:
        if layer_name not in reached:
            continue
        reached_count = reached[layer_name]
        passed_count = passed.get(layer_name, 0)
        rows.append(
            StageFunnelRow(
                layer_name=layer_name,
                reached=reached_count,
                passed=passed_count,
                pass_percentage=(passed_count / reached_count * 100.0) if reached_count else None,
                average_duration_ms=(duration_sum[layer_name] / reached_count) if reached_count else None,
            )
        )
    return rows


def compute_confirmed_count(stage_rows: list[StageFunnelRow]) -> int:
    """
    CONFIRMED count = the final hard-mandatory gate's (RISK_MANAGEMENT)
    passed count -- reaching and passing RISK_MANAGEMENT is exactly
    what PipelineStatus.VALID means (see
    app.strategy_pipeline.engine._run_pipeline).
    """
    for row in stage_rows:
        if row.layer_name == "RISK_MANAGEMENT":
            return row.passed
    return 0


def compute_top_rejection_reasons(
    rejection_records: list[RejectionRecord], top_n: int = DEFAULT_TOP_REASONS_PER_STAGE
) -> dict[str, list[RejectionReasonCount]]:
    """Pure aggregation: top-N most frequent rejection_reason strings, grouped by failed_layer."""
    reasons_by_stage: dict[str, Counter] = {}
    for record in rejection_records:
        reasons_by_stage.setdefault(record.failed_layer, Counter())[record.rejection_reason] += 1

    return {
        layer_name: [
            RejectionReasonCount(reason=reason, count=count)
            for reason, count in counter.most_common(top_n)
        ]
        for layer_name, counter in reasons_by_stage.items()
    }


async def generate_funnel_report(
    analytics_repository: AnalyticsRepository,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: Optional[datetime] = None,
) -> FunnelReport:
    """
    Build a FunnelReport for the last `window_days` days, ending at
    `now` (default: the current UTC time). Never raises on an empty
    database -- an empty window simply produces zero stage rows and an
    empty rejection-reason breakdown.
    """
    window_end = now or datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=window_days)

    stage_records = await analytics_repository.list_stage_results_since(window_start)
    rejection_records = await analytics_repository.list_rejections_since(window_start)

    stage_rows = compute_stage_funnel(stage_records)
    confirmed_count = compute_confirmed_count(stage_rows)
    top_reasons = compute_top_rejection_reasons(rejection_records)

    return FunnelReport(
        window_start_utc=window_start,
        window_end_utc=window_end,
        stages=stage_rows,
        confirmed_count=confirmed_count,
        top_rejection_reasons_by_stage=top_reasons,
    )


def format_funnel_report(report: FunnelReport) -> str:
    """Render a FunnelReport as a plain-text table for CLI output."""
    lines: list[str] = [
        f"Rejection funnel: {report.window_start_utc.isoformat()} -> {report.window_end_utc.isoformat()}",
        "",
    ]

    if not report.stages:
        lines.append("no data yet")
        return "\n".join(lines)

    lines.append(f"{'Stage':<20}{'Reached':>10}{'Passed':>10}{'Pass%':>9}{'AvgMs':>9}")
    for row in report.stages:
        pass_pct = f"{row.pass_percentage:.1f}%" if row.pass_percentage is not None else "-"
        avg_ms = f"{row.average_duration_ms:.1f}" if row.average_duration_ms is not None else "-"
        lines.append(f"{row.layer_name:<20}{row.reached:>10}{row.passed:>10}{pass_pct:>9}{avg_ms:>9}")
    lines.append("-" * 58)
    lines.append(f"{'CONFIRMED':<20}{'':>10}{report.confirmed_count:>10}")

    lines.append("")
    lines.append(f"Top rejection reasons per stage (up to {DEFAULT_TOP_REASONS_PER_STAGE} each):")
    if not report.top_rejection_reasons_by_stage:
        lines.append("  (no rejections in this window)")
    else:
        for layer_name in STAGE_DISPLAY_ORDER:
            reasons = report.top_rejection_reasons_by_stage.get(layer_name)
            if not reasons:
                continue
            lines.append(f"  {layer_name}:")
            for reason_count in reasons:
                lines.append(f"    {reason_count.count:>5}  {reason_count.reason}")

    return "\n".join(lines)
