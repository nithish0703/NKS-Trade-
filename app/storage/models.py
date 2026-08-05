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
    market_regime: Mapped[str] = mapped_column(String(32), nullable=False)
    higher_timeframe_bias: Mapped[str] = mapped_column(String(16), nullable=False)
    liquidity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entry_zone_type: Mapped[str] = mapped_column(String(32), nullable=False)
    structure_confirmation: Mapped[str] = mapped_column(String(16), nullable=False)
    volume_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    atr_status: Mapped[str] = mapped_column(String(16), nullable=False)
    trading_session: Mapped[str] = mapped_column(String(32), nullable=False)
    btc_market_alignment: Mapped[bool] = mapped_column(Boolean, nullable=False)
    detection_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    institutional_reason: Mapped[str] = mapped_column(Text, nullable=False)
    liquidity_sweep_id: Mapped[str] = mapped_column(String(128), nullable=False)
    structure_break_id: Mapped[str] = mapped_column(String(128), nullable=False)
    entry_zone_id: Mapped[str] = mapped_column(String(128), nullable=False)
    retest_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Dashboard-only lifecycle status ("NEW", "ACTIVE", "CLOSED_WIN", or
    # "CLOSED_LOSS"). This is a pure UI state transition set by the
    # dashboard "Trade" action (NEW -> ACTIVE) and by TradeOutcomeMonitor
    # (ACTIVE -> CLOSED_WIN/CLOSED_LOSS once price touches take_profit or
    # stop_loss). It is never read or written by strategy, scoring, or
    # risk code, and never affects the persisted signal's original
    # trading fields (entry_price, stop_loss, take_profit, etc. above
    # are never mutated after insert).
    dashboard_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="NEW", server_default="NEW"
    )

    # Trade outcome tracking, populated only by TradeOutcomeMonitor once
    # an ACTIVE signal's take_profit or stop_loss price is touched.
    # All three remain NULL for signals that are still NEW/ACTIVE (i.e.
    # never activated, or activated but not yet closed).
    outcome: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default=None)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=None)
    closed_at_utc: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


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
