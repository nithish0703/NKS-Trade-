"""
Sends a Telegram notification for a final PREMIUM/STRONG Signal to
every configured chat ID.
"""

from app.models.signal import Signal, SignalType
from app.notifications.results import NotificationStatus, TelegramNotificationResult
from app.notifications.telegram_client import TelegramBotClient
from app.notifications.telegram_formatter import TelegramSignalFormatter

_PUBLISHABLE_SIGNAL_TYPES = (SignalType.PREMIUM, SignalType.STRONG)


class TelegramSignalNotifier:
    """
    Formats and sends exactly one Telegram message per PREMIUM/STRONG
    Signal to every configured chat ID (one delivery attempt, and one
    returned result, per chat). Performs no persistence and no
    duplicate-suppression of its own (that responsibility belongs to
    the caller).
    """

    def __init__(
        self,
        *,
        telegram_client: TelegramBotClient,
        formatter: TelegramSignalFormatter,
        enabled: bool,
    ) -> None:
        self._telegram_client = telegram_client
        self._formatter = formatter
        self._enabled = enabled

    @property
    def telegram_client(self) -> TelegramBotClient:
        return self._telegram_client

    async def notify(self, signal: Signal) -> list[TelegramNotificationResult]:
        if not self._enabled:
            return [
                TelegramNotificationResult(
                    trade_id=signal.trade_id,
                    status=NotificationStatus.SKIPPED,
                    reason="Telegram notifications are disabled.",
                )
            ]

        if signal.signal_type not in _PUBLISHABLE_SIGNAL_TYPES:
            return [
                TelegramNotificationResult(
                    trade_id=signal.trade_id,
                    status=NotificationStatus.SKIPPED,
                    reason=f"Signal type {signal.signal_type.value} is not publishable.",
                )
            ]

        message = self._formatter.format_signal(signal)
        results = await self._telegram_client.send_message(message)
        return [result.model_copy(update={"trade_id": signal.trade_id}) for result in results]

    async def send_test_message(self) -> list[TelegramNotificationResult]:
        """
        Send a single explicit test message to every configured chat ID.
        Intended only for manual CLI testing; never invoked automatically
        during startup or factory construction.
        """
        message = self._formatter.format_test_message()
        return await self._telegram_client.send_message(message)
