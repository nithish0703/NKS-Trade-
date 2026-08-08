"""
SQLAlchemy ORM models for locally persisted signals and analytics.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all storage ORM models."""


class SignalRecord(Base):
    """Persisted, CONFIRMED institutional trade signal (binary decision only, no score)."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    setup_key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    coin: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    risk_reward_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True, default="CONFIRMED")
    liquidity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entry_zone_type: Mapped[str] = mapped_column(String(32), nullable=False)
    structure_confirmation: Mapped[str] = mapped_column(String(16), nullable=False)
    detection_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    institutional_reason: Mapped[str] = mapped_column(Text, nullable=False)
    liquidity_sweep_id: Mapped[str] = mapped_column(String(128), nullable=False)
    structure_break_id: Mapped[str] = mapped_column(String(128), nullable=False)
    entry_zone_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Dashboard-only lifecycle status ("NEW", "ACTIVE", "CLOSED_WIN", or
    # "CLOSED_LOSS"). This is a pure UI state transition set by the
    # dashboard "Trade" action (NEW -> ACTIVE) and mirrored by
    # SignalOutcomeMonitor (ACTIVE -> CLOSED_WIN/CLOSED_LOSS, in the same
    # pass as the passive_* columns below) once price touches
    # take_profit or stop_loss. It is never read or written by strategy,
    # scoring, or risk code, and never affects the persisted signal's
    # original trading fields (entry_price, stop_loss, take_profit,
    # etc. above are never mutated after insert).
    dashboard_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="NEW", server_default="NEW"
    )

    # Trade outcome tracking, mirrored by SignalOutcomeMonitor only for
    # a signal that is dashboard-ACTIVE at the moment it closes (see
    # outcome_source below). All three remain NULL for signals that are
    # still NEW/ACTIVE (never activated, activated but not yet closed,
    # or only ever resolved via passive tracking while never activated).
    outcome: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default=None)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=None)
    closed_at_utc: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    # Analytics-only fields, populated at signal-build time from data the
    # pipeline already computed in memory (never recalculated here).
    # Never read by strategy, scoring, risk, or notification code, and
    # never shown on the Telegram/dashboard signal itself -- purely for
    # the Phase 1 performance-report slices (by confidence tier, entry
    # grade, and stop-loss source).
    order_flow_confidence: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default=None)
    entry_grade: Mapped[Optional[str]] = mapped_column(String(4), nullable=True, default=None)
    stop_loss_source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default=None)

    # Passive outcome tracking: an analytics-only WIN/LOSS/TIMEOUT ground
    # truth for EVERY CONFIRMED signal, checked automatically by
    # SignalOutcomeMonitor regardless of dashboard_status -- this is the
    # single source of truth the Phase 1 performance report reads (never
    # outcome/exit_price/closed_at_utc above, which only exist for the
    # subset of signals the dashboard "Trade" button ever activated).
    # All NULL for a pre-existing row from before this column existed,
    # or for a signal not yet resolved -- never fabricated as a loss.
    passive_outcome: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default=None)
    passive_exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=None)
    passive_closed_at_utc: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    # Which path produced this signal's outcome, set in the same write
    # as passive_outcome above:
    #   - MANUAL_ACTIVATION: dashboard_status was ACTIVE at close time
    #     (the user clicked "Trade"); outcome/exit_price/closed_at_utc
    #     were also mirrored in the same pass.
    #   - PASSIVE_TRACKING: the signal was never activated on the
    #     dashboard; only passive_outcome is populated.
    # NULL until the signal closes.
    outcome_source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default=None)


class ScanStageAnalyticsRecord(Base):
    """
    One row per executed pipeline stage per scan (VALID, REJECTED, or
    DUPLICATE -- any scan that actually ran the pipeline; ERROR/SKIPPED
    scans have no meaningful stage data and are never recorded here).

    Presence of a row for (symbol, layer_name, scan_time_utc) means that
    stage was *reached*; `passed` records whether it passed. This is
    the source for the Phase 1 rejection-funnel report. Never stores
    candle data or secrets.
    """

    __tablename__ = "scan_stage_analytics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    stage_order: Mapped[int] = mapped_column(Integer, nullable=False)
    layer_name: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    scan_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MonitorLeaseRecord(Base):
    """
    Mutual-exclusion lease so at most one process's SignalOutcomeMonitor
    (e.g. the dashboard API and a concurrently running `python main.py
    scan`) does outcome-tracking work at a time against this database.
    One row per lease name; see app.storage.monitor_lease.MonitorLeaseGuard.
    """

    __tablename__ = "monitor_leases"

    lease_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    holder_id: Mapped[str] = mapped_column(String(36), nullable=False)
    expires_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RejectedAnalyticsRecord(Base):
    """
    Optional internal analytics record for a REJECTED strategy pipeline
    result. Never stores full candle payloads or secrets.
    """

    __tablename__ = "rejected_analytics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    failed_layer: Mapped[str] = mapped_column(String(64), nullable=False)
    rejection_reason: Mapped[str] = mapped_column(Text, nullable=False)
    detection_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
