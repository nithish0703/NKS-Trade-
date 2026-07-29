"""
Tests for app.notifications.chat_ids.parse_chat_ids / mask_chat_id.
"""

import pytest

from app.notifications.chat_ids import mask_chat_id, parse_chat_ids


class TestParseChatIds:
    def test_single_chat_id(self):
        assert parse_chat_ids("8886680874") == ["8886680874"]

    def test_multiple_chat_ids(self):
        assert parse_chat_ids("8886680874,736782230") == ["8886680874", "736782230"]

    def test_whitespace_trimmed(self):
        assert parse_chat_ids(" 8886680874 , 736782230 ") == ["8886680874", "736782230"]

    def test_none_returns_empty_list(self):
        assert parse_chat_ids(None) == []

    def test_empty_string_returns_empty_list(self):
        assert parse_chat_ids("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert parse_chat_ids("   ") == []

    def test_negative_group_chat_id_accepted(self):
        assert parse_chat_ids("-1001234567890") == ["-1001234567890"]

    def test_duplicate_ids_deduplicated(self):
        assert parse_chat_ids("8886680874,8886680874") == ["8886680874"]

    def test_order_preserved(self):
        assert parse_chat_ids("111,222,333") == ["111", "222", "333"]

    def test_empty_entry_rejected(self):
        with pytest.raises(ValueError):
            parse_chat_ids("123,,456")

    def test_trailing_comma_rejected(self):
        with pytest.raises(ValueError):
            parse_chat_ids("123,")

    def test_non_numeric_entry_rejected(self):
        with pytest.raises(ValueError):
            parse_chat_ids("abc")

    def test_mixed_valid_and_invalid_rejected(self):
        with pytest.raises(ValueError):
            parse_chat_ids("123,abc")


class TestMaskChatId:
    def test_masks_long_chat_id(self):
        assert mask_chat_id("8886680874") == "...0874"

    def test_masks_short_chat_id(self):
        assert mask_chat_id("123") == "***"

    def test_never_returns_full_id_for_long_input(self):
        masked = mask_chat_id("8886680874")
        assert "8886680874" not in masked
