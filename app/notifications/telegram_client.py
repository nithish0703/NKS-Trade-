"""
Async Telegram Bot API client with safe retry behaviour, supporting
delivery to multiple configured chat IDs.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional, Sequence

import httpx

from app.notifications.chat_ids import mask_chat_id, parse_chat_ids
from app.notifications.results import NotificationStatus, TelegramNotificationResult

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_SECRET_PLACEHOLDER = "***"


class TelegramNotificationError(Exception):
    """Base exception for Telegram notification failures. Never carries the bot token."""


class TelegramConfigurationError(TelegramNotificationError):
    """Raised when the Telegram client is misconfigured (missing token/chat IDs)."""


class TelegramRequestError(TelegramNotificationError):
    """Raised when a network-level request to the Telegram API fails."""


class TelegramResponseError(TelegramNotificationError):
    """Raised when the Telegram API responds with an error or malformed payload."""


class TelegramBotClient:
    """
    Thin async wrapper over the Telegram Bot API `sendMessage` endpoint,
    with bounded retries for transient failures only. Sends the same
    message to every configured chat ID independently, so a failure
    delivering to one chat never prevents delivery to the others. Never
    logs or raises the bot token or a full chat ID in exception text.
    """

    def __init__(
        self,
        *,
        bot_token: str,
        chat_ids: Sequence[str],
        api_base_url: str = "https://api.telegram.org",
        request_timeout_seconds: float = 10.0,
        max_retries: int = 2,
        retry_delay_seconds: float = 1.0,
        disable_web_page_preview: bool = True,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        if not bot_token or not bot_token.strip():
            raise TelegramConfigurationError("bot_token is required.")

        try:
            resolved_chat_ids = self._resolve_chat_ids(chat_ids)
        except ValueError as exc:
            raise TelegramConfigurationError(str(exc)) from exc
        if not resolved_chat_ids:
            raise TelegramConfigurationError("At least one chat_id is required.")

        self._bot_token = bot_token.strip()
        self._chat_ids = resolved_chat_ids
        self._api_base_url = api_base_url.rstrip("/")
        self._request_timeout_seconds = request_timeout_seconds
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._disable_web_page_preview = disable_web_page_preview

        self._injected_client = http_client is not None
        self._http_client = http_client or httpx.AsyncClient(timeout=request_timeout_seconds)

    @staticmethod
    def _resolve_chat_ids(chat_ids: Sequence[str]) -> list[str]:
        # Accept either an already-parsed sequence of IDs or a single
        # comma-separated string, trimming whitespace and rejecting
        # empty/invalid entries either way.
        if isinstance(chat_ids, str):
            return parse_chat_ids(chat_ids)
        return parse_chat_ids(",".join(str(chat_id) for chat_id in chat_ids))

    @property
    def chat_ids(self) -> list[str]:
        return list(self._chat_ids)

    @property
    def _send_message_url(self) -> str:
        return f"{self._api_base_url}/bot{self._bot_token}/sendMessage"

    async def send_message(self, text: str) -> list[TelegramNotificationResult]:
        """
        Send `text` to every configured chat ID. Each chat is sent to
        independently (one chat's failure does not prevent delivery to
        the others), and one TelegramNotificationResult is returned per
        chat ID, in configured order.
        """
        if not text or not text.strip():
            raise ValueError("text must not be empty.")

        results = await asyncio.gather(
            *(self._send_to_chat(chat_id, text) for chat_id in self._chat_ids)
        )
        return list(results)

    async def _send_to_chat(self, chat_id: str, text: str) -> TelegramNotificationResult:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": self._disable_web_page_preview,
        }

        attempt = 0
        last_reason = "Unknown Telegram delivery failure."

        while attempt <= self._max_retries:
            attempt += 1
            try:
                response = await self._http_client.post(self._send_message_url, json=payload)
            except httpx.TimeoutException:
                last_reason = "Telegram request timed out."
                if attempt > self._max_retries:
                    break
                await asyncio.sleep(self._retry_delay_seconds)
                continue
            except httpx.HTTPError as exc:
                last_reason = f"Telegram network error: {type(exc).__name__}."
                if attempt > self._max_retries:
                    break
                await asyncio.sleep(self._retry_delay_seconds)
                continue

            if response.status_code == 200:
                return self._parse_success_response(response, attempt, chat_id)

            if response.status_code in _RETRYABLE_STATUS_CODES:
                last_reason = f"Telegram returned retryable HTTP {response.status_code}."
                if attempt > self._max_retries:
                    break
                delay = self._resolve_retry_after(response) or self._retry_delay_seconds
                await asyncio.sleep(delay)
                continue

            # Permanent failures (400, 401, 403, and any other non-retryable
            # status) are never retried.
            return TelegramNotificationResult(
                status=NotificationStatus.FAILED,
                chat_id_suffix=mask_chat_id(chat_id),
                reason=self._safe_failure_reason(response),
                attempt_count=attempt,
            )

        return TelegramNotificationResult(
            status=NotificationStatus.FAILED,
            chat_id_suffix=mask_chat_id(chat_id),
            reason=last_reason,
            attempt_count=attempt,
        )

    def _parse_success_response(
        self, response: httpx.Response, attempt: int, chat_id: str
    ) -> TelegramNotificationResult:
        try:
            payload = response.json()
        except ValueError as exc:
            raise TelegramResponseError("Telegram response body was not valid JSON.") from exc

        if not isinstance(payload, dict) or not payload.get("ok"):
            return TelegramNotificationResult(
                status=NotificationStatus.FAILED,
                chat_id_suffix=mask_chat_id(chat_id),
                reason="Telegram response did not indicate success (ok=false).",
                attempt_count=attempt,
            )

        result = payload.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if message_id is None:
            return TelegramNotificationResult(
                status=NotificationStatus.FAILED,
                chat_id_suffix=mask_chat_id(chat_id),
                reason="Telegram response was missing a message_id.",
                attempt_count=attempt,
            )

        return TelegramNotificationResult(
            status=NotificationStatus.SENT,
            telegram_message_id=message_id,
            chat_id_suffix=mask_chat_id(chat_id),
            sent_at_utc=datetime.now(timezone.utc),
            attempt_count=attempt,
        )

    @staticmethod
    def _resolve_retry_after(response: httpx.Response) -> Optional[float]:
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if isinstance(payload, dict):
            parameters = payload.get("parameters")
            if isinstance(parameters, dict):
                retry_after = parameters.get("retry_after")
                if isinstance(retry_after, (int, float)):
                    return float(retry_after)

        header_value = response.headers.get("Retry-After")
        if header_value is not None:
            try:
                return float(header_value)
            except ValueError:
                return None
        return None

    def _safe_failure_reason(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"Telegram returned HTTP {response.status_code}."

        description = payload.get("description") if isinstance(payload, dict) else None
        if description:
            return self._redact_token(f"Telegram returned HTTP {response.status_code}: {description}")
        return f"Telegram returned HTTP {response.status_code}."

    def _redact_token(self, message: str) -> str:
        if self._bot_token and self._bot_token in message:
            return message.replace(self._bot_token, _SECRET_PLACEHOLDER)
        return message

    async def test_connection(self) -> list[TelegramNotificationResult]:
        from app.notifications.telegram_formatter import TelegramSignalFormatter

        return await self.send_message(TelegramSignalFormatter().format_test_message())

    async def close(self) -> None:
        """Close the underlying HTTP client, unless it was externally injected."""
        if not self._injected_client:
            await self._http_client.aclose()

    async def __aenter__(self) -> "TelegramBotClient":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.close()
