"""
Immutable scanner runtime event model, broadcast to optional observers
(e.g. a dashboard WebSocket) without any strategy or trading logic
depending on whether an observer is attached.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator


class ScannerEventType(str, Enum):
    """Kind of safe, secret-free scanner runtime event."""

    SCAN_CYCLE_STARTED = "SCAN_CYCLE_STARTED"
    PAIR_SCAN_UPDATED = "PAIR_SCAN_UPDATED"
    SCAN_CYCLE_COMPLETED = "SCAN_CYCLE_COMPLETED"
    SIGNAL_STORED = "SIGNAL_STORED"
    SIGNAL_DUPLICATE = "SIGNAL_DUPLICATE"
    TELEGRAM_SENT = "TELEGRAM_SENT"
    TELEGRAM_FAILED = "TELEGRAM_FAILED"
    SCANNER_STATUS_CHANGED = "SCANNER_STATUS_CHANGED"


class ScannerEvent(BaseModel):
    """
    A single scanner runtime event. `data` must never contain secrets,
    tokens, chat IDs, full raw candle arrays, or account details.
    """

    model_config = ConfigDict(frozen=True)

    event: ScannerEventType
    timestamp_utc: datetime
    data: dict[str, Any]

    @field_validator("timestamp_utc")
    @classmethod
    def _timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("timestamp_utc must be timezone-aware UTC.")
        return value
