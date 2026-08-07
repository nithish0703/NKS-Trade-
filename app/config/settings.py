"""
Global application settings for the SMC trading engine.
"""

from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config.thresholds import (
    PAIR_DISCOVERY_INTERVAL_SECONDS,
    PAIR_DISCOVERY_MINIMUM_OPEN_INTEREST_USDT,
    PAIR_DISCOVERY_MINIMUM_TURNOVER_24H_USDT,
    SCANNER_INTERVAL_SECONDS,
    TRADE_OUTCOME_MONITOR_INTERVAL_SECONDS,
)
from app.utils.chat_ids import parse_chat_ids as _parse_chat_ids

load_dotenv()


class Settings(BaseSettings):
    """
    Typed application settings sourced from environment variables.

    No exchange API keys are included since only public market data
    is used initially.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    exchange_base_url: str = Field(
        default="https://fapi.binance.com", alias="EXCHANGE_BASE_URL"
    )
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/smc_engine.db",
        alias="DATABASE_URL",
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    scanner_interval_seconds: int = Field(
        default=SCANNER_INTERVAL_SECONDS, alias="SCANNER_INTERVAL_SECONDS"
    )
    request_timeout_seconds: int = Field(default=10, alias="REQUEST_TIMEOUT_SECONDS")
    max_concurrent_scans: int = Field(default=5, alias="MAX_CONCURRENT_SCANS")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    enable_signal_persistence: bool = Field(
        default=True, alias="ENABLE_SIGNAL_PERSISTENCE"
    )
    enable_rejection_analytics: bool = Field(
        default=True, alias="ENABLE_REJECTION_ANALYTICS"
    )
    duplicate_signal_retention_seconds: int = Field(
        default=3600, alias="DUPLICATE_SIGNAL_RETENTION_SECONDS"
    )
    duplicate_signal_maximum_entries: int = Field(
        default=10000, alias="DUPLICATE_SIGNAL_MAXIMUM_ENTRIES"
    )
    candidate_buffer_maximum_size: int = Field(
        default=1000, alias="CANDIDATE_BUFFER_MAXIMUM_SIZE"
    )
    storage_failure_is_fatal: bool = Field(
        default=False, alias="STORAGE_FAILURE_IS_FATAL"
    )

    # Defaults to enabled: the scanner should cover every qualifying
    # USDT perpetual by default rather than the static 10-pair
    # DEFAULT_PAIRS fallback. Set to false to pin the scanner to
    # DEFAULT_PAIRS only.
    dynamic_pair_discovery_enabled: bool = Field(
        default=True, alias="DYNAMIC_PAIR_DISCOVERY_ENABLED"
    )
    pair_discovery_interval_seconds: int = Field(
        default=PAIR_DISCOVERY_INTERVAL_SECONDS, alias="PAIR_DISCOVERY_INTERVAL_SECONDS"
    )
    pair_discovery_minimum_open_interest_usdt: float = Field(
        default=PAIR_DISCOVERY_MINIMUM_OPEN_INTEREST_USDT,
        alias="PAIR_DISCOVERY_MINIMUM_OPEN_INTEREST_USDT",
    )
    pair_discovery_minimum_turnover_24h_usdt: float = Field(
        default=PAIR_DISCOVERY_MINIMUM_TURNOVER_24H_USDT,
        alias="PAIR_DISCOVERY_MINIMUM_TURNOVER_24H_USDT",
    )
    # Caps the dynamic pair-discovery list to the top N coins by 24h
    # turnover (after the OI/turnover filters already applied) so the
    # scanner only ever watches the most liquid, most reliably-tradable
    # names. Set to a different positive integer, or leave unset/blank
    # via the PAIR_DISCOVERY_MAXIMUM_PAIRS env var, to change or remove
    # the cap without a code change.
    pair_discovery_maximum_pairs: Optional[int] = Field(
        default=100, alias="PAIR_DISCOVERY_MAXIMUM_PAIRS"
    )

    telegram_enabled: bool = Field(default=False, alias="TELEGRAM_ENABLED")
    telegram_bot_token: Optional[str] = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_ids_raw: str = Field(default="", alias="TELEGRAM_CHAT_IDS")
    telegram_api_base_url: str = Field(
        default="https://api.telegram.org", alias="TELEGRAM_API_BASE_URL"
    )
    telegram_request_timeout_seconds: float = Field(
        default=10.0, alias="TELEGRAM_REQUEST_TIMEOUT_SECONDS"
    )
    telegram_max_retries: int = Field(default=2, alias="TELEGRAM_MAX_RETRIES")
    telegram_retry_delay_seconds: float = Field(
        default=1.0, alias="TELEGRAM_RETRY_DELAY_SECONDS"
    )
    telegram_disable_web_page_preview: bool = Field(
        default=True, alias="TELEGRAM_DISABLE_WEB_PAGE_PREVIEW"
    )

    dashboard_api_enabled: bool = Field(default=False, alias="DASHBOARD_API_ENABLED")
    dashboard_allowed_origins_raw: str = Field(
        default="http://localhost:5173", alias="DASHBOARD_ALLOWED_ORIGINS"
    )
    dashboard_websocket_enabled: bool = Field(
        default=False, alias="DASHBOARD_WEBSOCKET_ENABLED"
    )

    # Trade outcome monitor: periodically re-checks every ACTIVE
    # (dashboard "Trade" button) signal's current price against its
    # take_profit/stop_loss and records a WIN/LOSS outcome once one is
    # touched. Runs only alongside the dashboard API (see
    # dashboard_api_enabled above); has no effect on strategy, scoring,
    # risk, storage, or notification behaviour.
    trade_outcome_monitor_enabled: bool = Field(
        default=True, alias="TRADE_OUTCOME_MONITOR_ENABLED"
    )
    trade_outcome_monitor_interval_seconds: int = Field(
        default=TRADE_OUTCOME_MONITOR_INTERVAL_SECONDS,
        alias="TRADE_OUTCOME_MONITOR_INTERVAL_SECONDS",
    )

    @property
    def dashboard_allowed_origins(self) -> list[str]:
        """Parsed, trimmed, order-preserving list of allowed CORS origins."""
        if not self.dashboard_allowed_origins_raw.strip():
            return []
        origins: list[str] = []
        seen: set[str] = set()
        for raw_entry in self.dashboard_allowed_origins_raw.split(","):
            origin = raw_entry.strip()
            if origin and origin not in seen:
                seen.add(origin)
                origins.append(origin)
        return origins

    @field_validator("telegram_bot_token", mode="before")
    @classmethod
    def _trim_telegram_secrets(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None

    @field_validator("telegram_chat_ids_raw", mode="before")
    @classmethod
    def _coerce_telegram_chat_ids_raw(cls, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            # Accept an already-parsed list (e.g. constructed directly in
            # tests) by re-joining it back into the canonical raw form.
            return ",".join(str(item) for item in value)
        return str(value)

    @property
    def telegram_chat_ids(self) -> list[str]:
        """Parsed, deduplicated, order-preserving list of configured Telegram chat IDs."""
        return _parse_chat_ids(self.telegram_chat_ids_raw)

    @model_validator(mode="after")
    def _telegram_chat_ids_raw_must_be_valid(self) -> "Settings":
        # Eagerly validate at construction time so invalid entries fail
        # fast, matching the previous field_validator's behaviour.
        _parse_chat_ids(self.telegram_chat_ids_raw)
        return self

    @field_validator("pair_discovery_maximum_pairs", mode="before")
    @classmethod
    def _blank_pair_discovery_maximum_pairs_means_no_limit(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("pair_discovery_maximum_pairs")
    @classmethod
    def _pair_discovery_maximum_pairs_must_be_positive(
        cls, value: Optional[int]
    ) -> Optional[int]:
        if value is not None and value <= 0:
            raise ValueError("pair_discovery_maximum_pairs must be a positive integer, or unset for no limit.")
        return value

    @model_validator(mode="after")
    def _telegram_credentials_required_when_enabled(self) -> "Settings":
        if self.telegram_enabled:
            if not self.telegram_bot_token:
                raise ValueError("telegram_bot_token is required when telegram_enabled=True.")
            if not self.telegram_chat_ids:
                raise ValueError("telegram_chat_ids is required when telegram_enabled=True.")
        return self

    def __repr__(self) -> str:
        return "Settings(...)"

    def __str__(self) -> str:
        return "Settings(...)"


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton instance of the application settings."""
    return Settings()
