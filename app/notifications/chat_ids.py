"""
Telegram chat ID parsing/masking, re-exported for app.notifications consumers.
"""

from app.utils.chat_ids import parse_chat_ids

__all__ = ["parse_chat_ids", "mask_chat_id"]


def mask_chat_id(chat_id: str) -> str:
    """Return a masked representation of a chat ID, exposing only its last 4 characters."""
    if len(chat_id) <= 4:
        return "*" * len(chat_id)
    return f"...{chat_id[-4:]}"
