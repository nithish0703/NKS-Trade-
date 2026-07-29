"""
Coordinates Telegram notifications for newly stored signal-storage
results. Never persists and never notifies before successful storage.
"""

import logging
from typing import Optional, Sequence

from app.notifications.results import NotificationStatus, TelegramNotificationResult
from app.notifications.telegram_notifier import TelegramSignalNotifier
from app.storage.signal_service import SignalStorageResult


class SignalNotificationService:
    """
    Sends exactly one Telegram notification per newly stored, non-duplicate
    PREMIUM/STRONG signal. A failed Telegram delivery never rolls back or
    invalidates the already-stored signal.
    """

    def __init__(
        self,
        *,
        telegram_notifier: TelegramSignalNotifier,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._telegram_notifier = telegram_notifier
        self._logger = logger or logging.getLogger(__name__)

    @property
    def telegram_notifier(self) -> TelegramSignalNotifier:
        return self._telegram_notifier

    async def process_storage_result(
        self, storage_result: Optional[SignalStorageResult]
    ) -> Optional[list[TelegramNotificationResult]]:
        """
        Notify every configured Telegram chat for one newly stored,
        non-duplicate signal. Returns one TelegramNotificationResult per
        configured chat ID, or None when there is nothing to notify.
        """
        if storage_result is None:
            return None
        if not storage_result.stored or storage_result.duplicate:
            return None
        if storage_result.signal is None:
            return None

        results = await self._telegram_notifier.notify(storage_result.signal)

        for result in results:
            if result.status == NotificationStatus.FAILED:
                self._logger.warning(
                    "Telegram notification failed for trade_id=%s (chat ...%s): %s",
                    storage_result.signal.trade_id,
                    result.chat_id_suffix,
                    result.reason,
                )

        return results

    async def process_storage_results(
        self, results: Sequence[SignalStorageResult]
    ) -> list[TelegramNotificationResult]:
        """
        Process results in order, preserving that order in the return
        list. Each stored, non-duplicate signal contributes one
        TelegramNotificationResult per configured chat ID.
        """
        notification_results: list[TelegramNotificationResult] = []
        for storage_result in results:
            per_chat_results = await self.process_storage_result(storage_result)
            if per_chat_results is not None:
                notification_results.extend(per_chat_results)
        return notification_results
