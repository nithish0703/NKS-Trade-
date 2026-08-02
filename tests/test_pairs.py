"""
Tests for app.config.pairs: static pair list, the swappable dynamic
pair-source registry, and symbol validation.
"""

import pytest

from app.config.pairs import (
    BTC_SYMBOL,
    DEFAULT_PAIRS,
    get_configured_pairs,
    set_pair_source,
    validate_pair_symbol,
    validate_pair_symbol_format,
)


@pytest.fixture(autouse=True)
def _reset_pair_source():
    # Every test starts and ends with the static fallback active, so no
    # test can leak a dynamic pair source into another test.
    set_pair_source(None)
    yield
    set_pair_source(None)


class TestStaticDefault:
    def test_returns_default_pairs_when_no_source_installed(self):
        assert get_configured_pairs() == DEFAULT_PAIRS

    def test_btc_always_present(self):
        assert BTC_SYMBOL in get_configured_pairs()

    def test_returns_a_copy_not_the_original_list(self):
        pairs = get_configured_pairs()
        pairs.append("FAKE-USDT")
        assert "FAKE-USDT" not in get_configured_pairs()


class TestDynamicPairSource:
    def test_installed_source_is_used(self):
        set_pair_source(lambda: ["SOL-USDT", "AVAX-USDT"])
        pairs = get_configured_pairs()
        assert "SOL-USDT" in pairs
        assert "AVAX-USDT" in pairs

    def test_btc_inserted_if_source_omits_it(self):
        set_pair_source(lambda: ["SOL-USDT"])
        pairs = get_configured_pairs()
        assert pairs[0] == BTC_SYMBOL

    def test_btc_not_duplicated_if_source_includes_it(self):
        set_pair_source(lambda: [BTC_SYMBOL, "SOL-USDT"])
        pairs = get_configured_pairs()
        assert pairs.count(BTC_SYMBOL) == 1

    def test_clearing_source_restores_static_default(self):
        set_pair_source(lambda: ["SOL-USDT"])
        assert get_configured_pairs() != DEFAULT_PAIRS
        set_pair_source(None)
        assert get_configured_pairs() == DEFAULT_PAIRS

    def test_dynamic_symbol_passes_validate_pair_symbol(self):
        # Confirms a dynamically discovered coin (not in the static
        # DEFAULT_PAIRS list) flows through the same validator every
        # other pair does, once its source is installed.
        set_pair_source(lambda: ["PEPE-USDT"])
        assert validate_pair_symbol("PEPE-USDT") == "PEPE-USDT"

    def test_symbol_outside_dynamic_source_still_rejected(self):
        set_pair_source(lambda: ["PEPE-USDT"])
        with pytest.raises(ValueError):
            validate_pair_symbol("SHIB-USDT")

    def test_source_is_re_invoked_each_call_not_cached(self):
        calls = {"count": 0}

        def source():
            calls["count"] += 1
            return ["SOL-USDT"]

        set_pair_source(source)
        get_configured_pairs()
        get_configured_pairs()
        assert calls["count"] == 2


class TestValidatePairSymbolFormat:
    def test_valid_format_normalized(self):
        assert validate_pair_symbol_format(" btc-usdt ") == "BTC-USDT"

    def test_invalid_format_rejected(self):
        with pytest.raises(ValueError):
            validate_pair_symbol_format("not-a-symbol-format!!")

    def test_does_not_check_allow_list(self):
        # A well-formed symbol not in any configured list still passes
        # format-only validation -- this is what lets dynamic discovery
        # determine which symbols to allow without a circular check.
        set_pair_source(lambda: [])
        assert validate_pair_symbol_format("ZZZZ-USDT") == "ZZZZ-USDT"
