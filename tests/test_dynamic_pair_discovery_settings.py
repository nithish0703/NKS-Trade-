"""
Tests for the dynamic pair discovery fields on app.config.settings.Settings.
"""

import pytest

from app.config.settings import Settings


class TestDynamicPairDiscoverySettings:
    def test_disabled_by_default(self):
        settings = Settings(_env_file=None)
        assert settings.dynamic_pair_discovery_enabled is False

    def test_default_interval_is_15_minutes(self):
        settings = Settings(_env_file=None)
        assert settings.pair_discovery_interval_seconds == 900

    def test_default_maximum_pairs_is_none(self):
        settings = Settings(_env_file=None)
        assert settings.pair_discovery_maximum_pairs is None

    def test_can_enable_via_env_style_kwargs(self):
        settings = Settings(_env_file=None, DYNAMIC_PAIR_DISCOVERY_ENABLED="true")
        assert settings.dynamic_pair_discovery_enabled is True

    def test_custom_interval_respected(self):
        settings = Settings(_env_file=None, PAIR_DISCOVERY_INTERVAL_SECONDS="600")
        assert settings.pair_discovery_interval_seconds == 600

    def test_custom_thresholds_respected(self):
        settings = Settings(
            _env_file=None,
            PAIR_DISCOVERY_MINIMUM_OPEN_INTEREST_USDT="1000000",
            PAIR_DISCOVERY_MINIMUM_TURNOVER_24H_USDT="2000000",
        )
        assert settings.pair_discovery_minimum_open_interest_usdt == 1_000_000.0
        assert settings.pair_discovery_minimum_turnover_24h_usdt == 2_000_000.0

    def test_maximum_pairs_can_be_set_to_reenable_a_cap(self):
        settings = Settings(_env_file=None, PAIR_DISCOVERY_MAXIMUM_PAIRS="25")
        assert settings.pair_discovery_maximum_pairs == 25

    def test_blank_maximum_pairs_means_no_limit(self):
        settings = Settings(_env_file=None, PAIR_DISCOVERY_MAXIMUM_PAIRS="")
        assert settings.pair_discovery_maximum_pairs is None

    def test_zero_maximum_pairs_rejected(self):
        with pytest.raises(ValueError):
            Settings(_env_file=None, PAIR_DISCOVERY_MAXIMUM_PAIRS="0")

    def test_negative_maximum_pairs_rejected(self):
        with pytest.raises(ValueError):
            Settings(_env_file=None, PAIR_DISCOVERY_MAXIMUM_PAIRS="-5")
