"""
Tests for app.notifications.telegram_client.TelegramBotClient, using
httpx.MockTransport. No real Telegram requests are made.
"""

import json

import httpx
import pytest

from app.notifications.results import NotificationStatus
from app.notifications.telegram_client import TelegramBotClient, TelegramConfigurationError

pytestmark = pytest.mark.asyncio


def _client(handler, **kwargs) -> TelegramBotClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return TelegramBotClient(
        bot_token=kwargs.pop("bot_token", "TEST_TOKEN"),
        chat_ids=kwargs.pop("chat_ids", ["12345"]),
        http_client=http_client,
        retry_delay_seconds=kwargs.pop("retry_delay_seconds", 0.001),
        **kwargs,
    )


class TestSuccessfulSend:
    async def test_successful_send(self):
        def handler(request):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

        client = _client(handler)
        results = await client.send_message("hello")
        assert len(results) == 1
        assert results[0].status == NotificationStatus.SENT
        assert results[0].telegram_message_id == 42
        await client.close()

    async def test_correct_endpoint(self):
        captured = {}

        def handler(request):
            captured["path"] = request.url.path
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        client = _client(handler, bot_token="ABC123")
        await client.send_message("hi")
        assert captured["path"] == "/botABC123/sendMessage"
        await client.close()

    async def test_correct_chat_id(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        client = _client(handler, chat_ids=["99887766"])
        await client.send_message("hi")
        assert captured["body"]["chat_id"] == "99887766"
        await client.close()

    async def test_html_parse_mode(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        client = _client(handler)
        await client.send_message("hi")
        assert captured["body"]["parse_mode"] == "HTML"
        await client.close()

    async def test_web_preview_disabled(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        client = _client(handler, disable_web_page_preview=True)
        await client.send_message("hi")
        assert captured["body"]["disable_web_page_preview"] is True
        await client.close()

    async def test_message_id_extracted(self):
        def handler(request):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 987}})

        client = _client(handler)
        results = await client.send_message("hi")
        assert results[0].telegram_message_id == 987
        await client.close()


class TestMultipleChatIds:
    async def test_sends_to_every_configured_chat_id(self):
        seen_chat_ids = []

        def handler(request):
            body = json.loads(request.content)
            seen_chat_ids.append(body["chat_id"])
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(seen_chat_ids)}})

        client = _client(handler, chat_ids=["111", "222", "333"])
        results = await client.send_message("hi")
        assert sorted(seen_chat_ids) == ["111", "222", "333"]
        assert len(results) == 3
        assert all(r.status == NotificationStatus.SENT for r in results)
        await client.close()

    async def test_returns_one_result_per_chat_id_in_order(self):
        def handler(request):
            body = json.loads(request.content)
            message_id = {"111": 10, "222": 20}[body["chat_id"]]
            return httpx.Response(200, json={"ok": True, "result": {"message_id": message_id}})

        client = _client(handler, chat_ids=["111", "222"])
        results = await client.send_message("hi")
        assert [r.telegram_message_id for r in results] == [10, 20]
        await client.close()

    async def test_one_chat_failure_does_not_block_other_chats(self):
        def handler(request):
            body = json.loads(request.content)
            if body["chat_id"] == "111":
                return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

        client = _client(handler, chat_ids=["111", "222"], max_retries=0)
        results = await client.send_message("hi")
        statuses = {r.chat_id_suffix: r.status for r in results}
        assert results[0].status == NotificationStatus.FAILED
        assert results[1].status == NotificationStatus.SENT
        await client.close()

    async def test_no_duplicate_sends_to_same_chat_id(self):
        call_count = {"n": 0}

        def handler(request):
            call_count["n"] += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        # Even if the same ID were passed twice, TelegramBotClient dedupes
        # via parse_chat_ids before ever making a request.
        client = _client(handler, chat_ids=["111", "111", "222"])
        results = await client.send_message("hi")
        assert call_count["n"] == 2
        assert len(results) == 2
        await client.close()

    async def test_chat_id_suffix_never_exposes_full_id(self):
        def handler(request):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        client = _client(handler, chat_ids=["8886680874"])
        results = await client.send_message("hi")
        assert "8886680874" not in (results[0].chat_id_suffix or "")
        await client.close()


class TestRetryBehaviour:
    async def test_timeout_retry(self):
        call_count = {"n": 0}

        def handler(request):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise httpx.TimeoutException("timed out")
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        client = _client(handler, max_retries=2)
        results = await client.send_message("hi")
        assert results[0].status == NotificationStatus.SENT
        assert call_count["n"] == 2
        await client.close()

    async def test_http_429_retry_after(self):
        call_count = {"n": 0}

        def handler(request):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return httpx.Response(
                    429, json={"ok": False, "parameters": {"retry_after": 0.01}}
                )
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        client = _client(handler, max_retries=2)
        results = await client.send_message("hi")
        assert results[0].status == NotificationStatus.SENT
        assert call_count["n"] == 2
        await client.close()

    async def test_http_500_retry(self):
        call_count = {"n": 0}

        def handler(request):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return httpx.Response(500, json={"ok": False, "description": "server error"})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        client = _client(handler, max_retries=2)
        results = await client.send_message("hi")
        assert results[0].status == NotificationStatus.SENT
        assert call_count["n"] == 2
        await client.close()

    async def test_permanent_400_not_retried(self):
        call_count = {"n": 0}

        def handler(request):
            call_count["n"] += 1
            return httpx.Response(400, json={"ok": False, "description": "Bad Request"})

        client = _client(handler, max_retries=2)
        results = await client.send_message("hi")
        assert results[0].status == NotificationStatus.FAILED
        assert call_count["n"] == 1
        await client.close()

    async def test_invalid_token_response(self):
        def handler(request):
            return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

        client = _client(handler, max_retries=2)
        results = await client.send_message("hi")
        assert results[0].status == NotificationStatus.FAILED
        await client.close()

    async def test_invalid_chat_response(self):
        def handler(request):
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})

        client = _client(handler, max_retries=2)
        results = await client.send_message("hi")
        assert results[0].status == NotificationStatus.FAILED
        assert "chat not found" in results[0].reason
        await client.close()

    async def test_malformed_json(self):
        def handler(request):
            return httpx.Response(200, content=b"not json")

        client = _client(handler)
        from app.notifications.telegram_client import TelegramResponseError

        with pytest.raises(TelegramResponseError):
            await client.send_message("hi")
        await client.close()

    async def test_ok_false_response(self):
        def handler(request):
            return httpx.Response(200, json={"ok": False, "description": "something went wrong"})

        client = _client(handler)
        results = await client.send_message("hi")
        assert results[0].status == NotificationStatus.FAILED
        await client.close()

    async def test_missing_message_id(self):
        def handler(request):
            return httpx.Response(200, json={"ok": True, "result": {}})

        client = _client(handler)
        results = await client.send_message("hi")
        assert results[0].status == NotificationStatus.FAILED
        await client.close()

    async def test_maximum_retries_enforced(self):
        call_count = {"n": 0}

        def handler(request):
            call_count["n"] += 1
            return httpx.Response(500, json={"ok": False, "description": "server error"})

        client = _client(handler, max_retries=2)
        results = await client.send_message("hi")
        assert results[0].status == NotificationStatus.FAILED
        assert call_count["n"] == 3  # initial attempt + 2 retries
        await client.close()


class TestSecurity:
    async def test_token_not_exposed_in_errors(self):
        def handler(request):
            return httpx.Response(
                400, json={"ok": False, "description": "Bad Request: token SUPERSECRETTOKEN invalid"}
            )

        client = _client(handler, bot_token="SUPERSECRETTOKEN", max_retries=0)
        results = await client.send_message("hi")
        assert "SUPERSECRETTOKEN" not in results[0].reason
        await client.close()

    async def test_injected_client_not_auto_closed(self):
        def handler(request):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        transport = httpx.MockTransport(handler)
        http_client = httpx.AsyncClient(transport=transport)
        client = TelegramBotClient(
            bot_token="T", chat_ids=["1"], http_client=http_client, retry_delay_seconds=0.001
        )
        await client.send_message("hi")
        await client.close()
        assert http_client.is_closed is False
        await http_client.aclose()


class TestConfiguration:
    def test_missing_bot_token_rejected(self):
        with pytest.raises(TelegramConfigurationError):
            TelegramBotClient(bot_token="", chat_ids=["123"])

    def test_missing_chat_ids_rejected(self):
        with pytest.raises(TelegramConfigurationError):
            TelegramBotClient(bot_token="abc", chat_ids=[])

    def test_empty_chat_id_string_rejected(self):
        with pytest.raises(TelegramConfigurationError):
            TelegramBotClient(bot_token="abc", chat_ids=[""])

    def test_invalid_chat_id_rejected(self):
        with pytest.raises(TelegramConfigurationError):
            TelegramBotClient(bot_token="abc", chat_ids=["not-a-number"])

    def test_comma_separated_string_accepted(self):
        client = TelegramBotClient(bot_token="abc", chat_ids="111,222")
        assert client.chat_ids == ["111", "222"]

    async def test_empty_text_rejected(self):
        def handler(request):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        client = _client(handler)
        with pytest.raises(ValueError):
            await client.send_message("")
        await client.close()


class TestContextManager:
    async def test_async_context_manager_closes_owned_client(self):
        def handler(request):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        transport = httpx.MockTransport(handler)
        async with TelegramBotClient(
            bot_token="T",
            chat_ids=["1"],
            http_client=httpx.AsyncClient(transport=transport),
            retry_delay_seconds=0.001,
        ) as client:
            await client.send_message("hi")
