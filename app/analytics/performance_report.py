"""
Performance / expectancy report: win rate, average win/loss in R,
expectancy per trade, max consecutive losses, max drawdown, and profit
factor -- overall and sliced by symbol, order_flow_confidence tier,
entry_grade, stop_loss_source, and outcome_source.

Reads passively-closed signals (every CONFIRMED signal
SignalOutcomeMonitor has resolved to WIN/LOSS/TIMEOUT, independent of
dashboard_status), so this is the full-sample source, not just
dashboard-activated trades. `outcome_source` (MANUAL_ACTIVATION vs
PASSIVE_TRACKING) tells you how much of that sample was also visible
on the dashboard "Trade" workflow vs pure background tracking -- the
header always prints both counts so a report can never be misread as
"every signal was actively traded". Analysis only -- never
recalculates or influences any strategy or risk decision.

R = (exit_price - entry_price) / (entry_price - stop_loss). Correctly
signed for both directions without extra direction handling: for a
SELL, (entry_price - stop_loss) is itself negative (the stop sits
above entry), which flips the sign of every ratio computed against it.
"""

from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict

from app.storage.signal_repository import (
    OUTCOME_SOURCE_MANUAL_ACTIVATION,
    OUTCOME_SOURCE_PASSIVE_TRACKING,
    PASSIVE_OUTCOME_UNRESOLVED,
    PASSIVE_OUTCOME_WIN,
    ClosedTradeRecord,
    SignalRepository,
)

DEFAULT_WINDOW_DAYS = 7

_UNKNOWN_SLICE_KEY = "UNKNOWN"


def compute_r_multiple(entry_price: float, stop_loss: float, exit_price: float) -> float:
    """
    R = (exit_price - entry_price) / (entry_price - stop_loss).

    Raises:
        ValueError: If entry_price equals stop_loss (division by zero;
            never a valid RiskPlan in practice, but this function must
            not silently return a fabricated value for it).
    """
    risk = entry_price - stop_loss
    if risk == 0:
        raise ValueError("entry_price and stop_loss cannot be equal (division by zero).")
    return (exit_price - entry_price) / risk


class TradeR(BaseModel):
    """A single closed trade's outcome expressed in R, with its analytics slice keys."""

    model_config = ConfigDict(frozen=True)

    trade_id: str
    symbol: str
    order_flow_confidence: Optional[str]
    entry_grade: Optional[str]
    stop_loss_source: Optional[str]
    outcome_source: Optional[str]
    r_multiple: float
    is_win: bool
    closed_at_utc: datetime


def has_complete_outcome_data(record: ClosedTradeRecord) -> bool:
    """
    True only for a record with everything needed to compute a real R
    (never UNRESOLVED, and never missing passive_exit_price/
    passive_closed_at_utc). A pre-existing row is never guaranteed to
    have every column populated -- e.g. exit_price/closed_at_utc are
    NULLable columns -- so this must be checked before to_trade_r(),
    never assumed. generate_performance_report() excludes every record
    that fails this check from R/win-rate/expectancy math entirely,
    counted separately as `unresolved_count` rather than raising or
    fabricating a value.
    """
    return (
        record.passive_outcome != PASSIVE_OUTCOME_UNRESOLVED
        and record.passive_exit_price is not None
        and record.passive_closed_at_utc is not None
    )


def to_trade_r(record: ClosedTradeRecord) -> TradeR:
    """
    Convert a persisted ClosedTradeRecord into its R-multiple
    representation. Caller must have already confirmed
    has_complete_outcome_data(record) -- this never fabricates a
    missing exit_price/closed_at_utc.
    """
    r_multiple = compute_r_multiple(
        record.signal.entry_price, record.signal.stop_loss, record.passive_exit_price
    )
    return TradeR(
        trade_id=record.signal.trade_id,
        symbol=record.signal.coin,
        order_flow_confidence=record.order_flow_confidence,
        entry_grade=record.entry_grade,
        stop_loss_source=record.stop_loss_source,
        outcome_source=record.outcome_source,
        r_multiple=r_multiple,
        is_win=record.passive_outcome == PASSIVE_OUTCOME_WIN,
        closed_at_utc=record.passive_closed_at_utc,
    )


class PerformanceStats(BaseModel):
    """Aggregated performance statistics over a set of closed trades."""

    model_config = ConfigDict(frozen=True)

    trade_count: int
    win_rate_percentage: Optional[float]
    average_win_r: Optional[float]
    average_loss_r: Optional[float]
    expectancy_r: Optional[float]
    max_consecutive_losses: int
    max_drawdown_r: Optional[float]
    profit_factor: Optional[float]


def compute_performance_stats(trades: list[TradeR]) -> PerformanceStats:
    """
    Pure aggregation over a list of closed trades. Sorts internally by
    closed_at_utc ascending for the streak/drawdown calculations -- the
    caller need not pre-sort.
    """
    if not trades:
        return PerformanceStats(
            trade_count=0,
            win_rate_percentage=None,
            average_win_r=None,
            average_loss_r=None,
            expectancy_r=None,
            max_consecutive_losses=0,
            max_drawdown_r=None,
            profit_factor=None,
        )

    ordered = sorted(trades, key=lambda t: t.closed_at_utc)
    wins = [t for t in ordered if t.is_win]
    losses = [t for t in ordered if not t.is_win]

    trade_count = len(ordered)
    win_rate = len(wins) / trade_count
    average_win_r = (sum(t.r_multiple for t in wins) / len(wins)) if wins else None
    average_loss_r = (sum(abs(t.r_multiple) for t in losses) / len(losses)) if losses else None
    expectancy_r = (win_rate * average_win_r if average_win_r is not None else 0.0) - (
        (1.0 - win_rate) * average_loss_r if average_loss_r is not None else 0.0
    )

    max_consecutive_losses = 0
    current_streak = 0
    for trade in ordered:
        if trade.is_win:
            current_streak = 0
            continue
        current_streak += 1
        max_consecutive_losses = max(max_consecutive_losses, current_streak)

    cumulative_r = 0.0
    peak_r = 0.0
    max_drawdown_r = 0.0
    for trade in ordered:
        cumulative_r += trade.r_multiple
        peak_r = max(peak_r, cumulative_r)
        max_drawdown_r = max(max_drawdown_r, peak_r - cumulative_r)

    gross_gains_r = sum(t.r_multiple for t in wins)
    gross_losses_r = sum(abs(t.r_multiple) for t in losses)
    profit_factor = (gross_gains_r / gross_losses_r) if gross_losses_r > 0 else None

    return PerformanceStats(
        trade_count=trade_count,
        win_rate_percentage=win_rate * 100.0,
        average_win_r=average_win_r,
        average_loss_r=average_loss_r,
        expectancy_r=expectancy_r,
        max_consecutive_losses=max_consecutive_losses,
        max_drawdown_r=max_drawdown_r,
        profit_factor=profit_factor,
    )


def slice_by(trades: list[TradeR], key_fn: Callable[[TradeR], Optional[str]]) -> dict[str, PerformanceStats]:
    """
    Group `trades` by `key_fn`'s result (None grouped under "UNKNOWN",
    e.g. a signal persisted before this field existed) and compute
    PerformanceStats independently for each group.
    """
    groups: dict[str, list[TradeR]] = {}
    for trade in trades:
        key = key_fn(trade) or _UNKNOWN_SLICE_KEY
        groups.setdefault(key, []).append(trade)
    return {key: compute_performance_stats(group) for key, group in groups.items()}


class PerformanceReport(BaseModel):
    """Complete performance report for a time window: overall plus every slice."""

    model_config = ConfigDict(frozen=True)

    window_start_utc: datetime
    window_end_utc: datetime
    overall: PerformanceStats
    by_symbol: dict[str, PerformanceStats]
    by_order_flow_confidence: dict[str, PerformanceStats]
    by_entry_grade: dict[str, PerformanceStats]
    by_stop_loss_source: dict[str, PerformanceStats]
    by_outcome_source: dict[str, PerformanceStats]
    still_open_count: int
    # Header-level counts so a reader can never mistake this report for
    # "every signal was actively traded": how many of the closed trades
    # above were dashboard-ACTIVE at close time (MANUAL_ACTIVATION) vs
    # pure background tracking (PASSIVE_TRACKING).
    manual_activation_count: int
    passive_tracking_count: int
    # Records excluded entirely from `overall`/every slice above (never
    # counted as a fabricated 0R trade): force-closed as UNRESOLVED (no
    # price ever observed), or missing exit_price/closed_at_utc outright
    # -- both mean "no real R can be computed for this row", reported
    # here so the report can never quietly make them disappear.
    unresolved_count: int
    # Among the trades that DO count toward `overall`/every slice above,
    # how many have outcome_source=NULL -- rows closed by a build of
    # close_passive that predates the outcome_source column. Not the
    # same as PASSIVE_TRACKING; already broken out as its own "UNKNOWN"
    # key in by_outcome_source, mirrored here so it always shows in the
    # header even when by_outcome_source is buried further down.
    unknown_outcome_source_count: int


async def generate_performance_report(
    signal_repository: SignalRepository,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: Optional[datetime] = None,
) -> PerformanceReport:
    """
    Build a PerformanceReport for signals passively closed in the last
    `window_days` days, ending at `now` (default: the current UTC
    time). Never raises on an empty database.
    """
    window_end = now or datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=window_days)

    closed_records = await signal_repository.list_passively_closed(since=window_start)
    # Excluded from every R-multiple/win-rate/expectancy computation
    # below, never treated as a fabricated 0R trade: UNRESOLVED (no
    # price ever observed) and any record missing exit_price/
    # closed_at_utc outright (a pre-existing row is never guaranteed to
    # have every nullable column populated).
    resolved_records = [r for r in closed_records if has_complete_outcome_data(r)]
    unresolved_records = [r for r in closed_records if not has_complete_outcome_data(r)]
    trades = [to_trade_r(record) for record in resolved_records]

    still_open = await signal_repository.list_not_passively_closed()

    manual_activation_count = sum(1 for t in trades if t.outcome_source == OUTCOME_SOURCE_MANUAL_ACTIVATION)
    passive_tracking_count = sum(1 for t in trades if t.outcome_source == OUTCOME_SOURCE_PASSIVE_TRACKING)
    unknown_outcome_source_count = sum(1 for t in trades if t.outcome_source is None)

    return PerformanceReport(
        window_start_utc=window_start,
        window_end_utc=window_end,
        overall=compute_performance_stats(trades),
        by_symbol=slice_by(trades, lambda t: t.symbol),
        by_order_flow_confidence=slice_by(trades, lambda t: t.order_flow_confidence),
        by_entry_grade=slice_by(trades, lambda t: t.entry_grade),
        by_stop_loss_source=slice_by(trades, lambda t: t.stop_loss_source),
        by_outcome_source=slice_by(trades, lambda t: t.outcome_source),
        still_open_count=len(still_open),
        manual_activation_count=manual_activation_count,
        passive_tracking_count=passive_tracking_count,
        unresolved_count=len(unresolved_records),
        unknown_outcome_source_count=unknown_outcome_source_count,
    )


def _format_stats_row(label: str, stats: PerformanceStats) -> str:
    if stats.trade_count == 0:
        return f"  {label:<20} no closed trades yet"
    win_rate = f"{stats.win_rate_percentage:.1f}%"
    avg_win = f"{stats.average_win_r:.2f}R" if stats.average_win_r is not None else "-"
    avg_loss = f"{stats.average_loss_r:.2f}R" if stats.average_loss_r is not None else "-"
    expectancy = f"{stats.expectancy_r:.3f}R"
    profit_factor = f"{stats.profit_factor:.2f}" if stats.profit_factor is not None else "inf"
    max_drawdown = f"{stats.max_drawdown_r:.2f}R" if stats.max_drawdown_r is not None else "-"
    return (
        f"  {label:<20} n={stats.trade_count:<5} win%={win_rate:<8} avgWin={avg_win:<8} "
        f"avgLoss={avg_loss:<8} expectancy={expectancy:<10} "
        f"maxConsecLoss={stats.max_consecutive_losses:<4} maxDD={max_drawdown:<8} PF={profit_factor}"
    )


def format_performance_report(report: PerformanceReport) -> str:
    """Render a PerformanceReport as plain text for CLI output."""
    lines: list[str] = [
        f"Performance: {report.window_start_utc.isoformat()} -> {report.window_end_utc.isoformat()}",
        f"outcome source: {report.manual_activation_count} MANUAL_ACTIVATION, "
        f"{report.passive_tracking_count} PASSIVE_TRACKING, "
        f"{report.unknown_outcome_source_count} UNKNOWN (legacy, pre-dates outcome_source)",
        f"unresolved (no price ever observed or incomplete record, excluded from expectancy): "
        f"{report.unresolved_count}",
        "",
    ]

    if report.overall.trade_count == 0:
        lines.append("no data yet")
        lines.append(f"still open (not yet closed): {report.still_open_count}")
        return "\n".join(lines)

    lines.append(_format_stats_row("OVERALL", report.overall))
    lines.append("")

    for title, slice_stats in (
        ("By symbol", report.by_symbol),
        ("By order_flow_confidence", report.by_order_flow_confidence),
        ("By entry_grade", report.by_entry_grade),
        ("By stop_loss_source", report.by_stop_loss_source),
        ("By outcome_source", report.by_outcome_source),
    ):
        lines.append(f"{title}:")
        for key in sorted(slice_stats):
            lines.append(_format_stats_row(key, slice_stats[key]))
        lines.append("")

    lines.append(f"still open (not yet closed): {report.still_open_count}")
    return "\n".join(lines)
