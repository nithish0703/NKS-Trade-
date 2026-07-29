"""
Immutable typed result models for Telegram signal notifications.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


def _is_utc(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() == timezone.utc.utcoffset(value)


class NotificationStatus(str, Enum):
    """Outcome status of attempting to deliver a Telegram notification."""

    SENT = "SENT"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class TelegramNotificationResult(BaseModel):
    """
    Result of attempting to deliver a Telegram notification to a single
    chat for a single trade. Never carries the bot token or full chat
    ID; `chat_id_suffix` (when present) exposes only the last 4
    characters of the destination chat ID, for log/display purposes.
    """

    model_config = ConfigDict(frozen=True)

    trade_id: Optional[str] = None
    status: NotificationStatus
    telegram_message_id: Optional[int] = None
    chat_id_suffix: Optional[str] = None
    sent_at_utc: Optional[datetime] = None
    reason: Optional[str] = None
    attempt_count: int = 0
    metadata: Optional[dict[str, Any]] = None

    @field_validator("sent_at_utc")
    @classmethod
    def _sent_at_must_be_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and not _is_utc(value):
            raise ValueError("sent_at_utc must be timezone-aware UTC.")
        return value

    @field_validator("chat_id_suffix")
    @classmethod
    def _chat_id_suffix_must_be_masked(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and len(value) > 8:
            raise ValueError("chat_id_suffix must be a short masked value, not a full chat ID.")
        return value

    @field_validator("attempt_count")
    @classmethod
    def _attempt_count_cannot_be_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("attempt_count cannot be negative.")
        return value

    @model_validator(mode="after")
    def _sent_requires_message_id_and_timestamp(self) -> "TelegramNotificationResult":
        if self.status == NotificationStatus.SENT:
            if self.telegram_message_id is None or self.sent_at_utc is None:
                raise ValueError(
                    "A SENT TelegramNotificationResult requires telegram_message_id and sent_at_utc."
                )
        return self

    @model_validator(mode="after")
    def _failed_requires_reason(self) -> "TelegramNotificationResult":
        if self.status == NotificationStatus.FAILED and not self.reason:
            raise ValueError("A FAILED TelegramNotificationResult requires a reason.")
        return self
