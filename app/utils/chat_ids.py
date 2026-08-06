"""
Shared parsing/validation for comma-separated Telegram chat ID lists.

A dependency-free leaf module (no imports from app.config or
app.notifications) so both packages can import it without risking a
circular import at package-init time.
"""

from typing import Optional


def parse_chat_ids(raw_value: Optional[str]) -> list[str]:
    """
    Parse a comma-separated string of Telegram chat IDs into a
    deduplicated, order-preserving list of trimmed, non-empty IDs.

    Example: "8886680874, 736782230" -> ["8886680874", "736782230"]

    Raises ValueError if any individual entry is empty or not a valid
    Telegram chat ID (an optional leading '-' followed by digits only,
    since supergroup/channel chat IDs are negative integers).
    """
    if raw_value is None or not raw_value.strip():
        return []

    chat_ids: list[str] = []
    seen: set[str] = set()
    for raw_entry in raw_value.split(","):
        entry = raw_entry.strip()
        if not entry:
            raise ValueError("Telegram chat IDs must not contain empty entries.")
        if not _is_valid_chat_id(entry):
            raise ValueError(f"'{entry}' is not a valid Telegram chat ID.")
        if entry not in seen:
            seen.add(entry)
            chat_ids.append(entry)

    return chat_ids


def _is_valid_chat_id(value: str) -> bool:
    candidate = value[1:] if value.startswith("-") else value
    return candidate.isdigit()
