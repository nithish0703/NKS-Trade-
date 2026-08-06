"""
Formats final Signal objects into concise Telegram HTML messages.
"""

import html

from app.models.signal import Signal

_TELEGRAM_MESSAGE_LIMIT = 4096
_TRUNCATION_SUFFIX = "..."


class TelegramSignalFormatter:
    """
    Builds a deterministic, concise HTML-formatted Telegram message for a
    final CONFIRMED Signal. Never includes secrets, raw metadata,
    guaranteed-outcome language, or any score/confidence/percentage.
    """

    def format_signal(self, signal: Signal) -> str:
        direction_emoji = "\U0001F4C8" if signal.direction.value == "BUY" else "\U0001F4C9"

        header = f"<b>NKS {html.escape(signal.status.value)} SIGNAL</b>"
        divider = "─" * 20

        lines = [
            header,
            divider,
            f"\U0001F4B0 Coin: <b>{html.escape(signal.coin)}</b>",
            f"{direction_emoji} Direction: <b>{html.escape(signal.direction.value)}</b>",
            "",
            f"\U000027A1 Entry: <b>{self._format_price(signal.entry_price)}</b>",
            f"\U0001F6D1 Stop Loss: <b>{self._format_price(signal.stop_loss)}</b>",
            f"\U0001F3AF Take Profit: <b>{self._format_price(signal.take_profit)}</b>",
            f"\U00002696 Risk Reward: 1:{signal.risk_reward_ratio:.2f}",
            divider,
            f"\U0001F4C5 Detected: {signal.detection_time_utc.isoformat()} UTC",
        ]

        message = "\n".join(lines)
        return self._safe_truncate(message, _TELEGRAM_MESSAGE_LIMIT)

    def format_test_message(self) -> str:
        return "✅ Telegram test successful. NKS Trade bot is connected."

    @staticmethod
    def _format_price(price: float) -> str:
        formatted = f"{price:.8f}".rstrip("0").rstrip(".")
        return formatted or "0"

    @staticmethod
    def _safe_truncate(text: str, max_length: int) -> str:
        if max_length <= 0:
            return ""
        if len(text) <= max_length:
            return text
        suffix = _TRUNCATION_SUFFIX
        if max_length <= len(suffix):
            return text[:max_length]
        return text[: max_length - len(suffix)] + suffix
