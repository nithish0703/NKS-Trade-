"""
Unit tests for app.risk.correlation_filter.CorrelationFilter.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.candle import Candle
from app.risk.correlation_filter import CorrelationFilter
from app.risk.results import CorrelationStatus
from app.risk.stop_loss import RiskCalculationError

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candles(closes: list[float], symbol="BTC-USDT") -> list[Candle]:
    candles = []
    for i, close in enumerate(closes):
        candles.append(
            Candle(
                timestamp=UTC_NOW + timedelta(minutes=i),
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=100.0,
                symbol=symbol,
                timeframe="15m",
            )
        )
    return candles


def _filter(max_corr=0.80, min_obs=5) -> CorrelationFilter:
    return CorrelationFilter(maximum_allowed_correlation=max_corr, minimum_observations=min_obs)


def _rising_closes(n: int, start=100.0, step=1.0) -> list[float]:
    return [start + i * step for i in range(n)]


class TestCorrelationFilter:
    def test_no_active_positions_passes(self):
        candidate = _candles(_rising_closes(10))
        result = _filter().evaluate("ETH-USDT", candidate, {})
        assert result.acceptable is True
        assert result.status == CorrelationStatus.ACCEPTABLE

    def test_low_correlation_passes(self):
        candidate_closes = [100, 102, 99, 103, 98, 104, 97, 105, 96, 106]
        active_closes = [50, 51, 50.5, 52, 51.5, 50, 52.5, 51, 50.2, 52.8]
        candidate = _candles(candidate_closes, "ETH-USDT")
        active = {"SOL-USDT": _candles(active_closes, "SOL-USDT")}
        result = _filter(min_obs=5).evaluate("ETH-USDT", candidate, active)
        assert result.acceptable is True

    def test_high_positive_correlation_fails(self):
        closes = _rising_closes(10)
        candidate = _candles(closes, "ETH-USDT")
        active = {"BTC-USDT": _candles(closes, "BTC-USDT")}  # identical series
        result = _filter(min_obs=5).evaluate("ETH-USDT", candidate, active)
        assert result.acceptable is False
        assert result.status == CorrelationStatus.TOO_HIGH

    def test_high_negative_absolute_correlation_fails(self):
        rising = _rising_closes(10)
        falling = list(reversed(rising))
        candidate = _candles(rising, "ETH-USDT")
        active = {"BTC-USDT": _candles(falling, "BTC-USDT")}
        result = _filter(min_obs=5).evaluate("ETH-USDT", candidate, active)
        assert result.acceptable is False
        assert result.status == CorrelationStatus.TOO_HIGH

    def test_insufficient_observations_fails(self):
        candidate = _candles(_rising_closes(3), "ETH-USDT")
        active = {"BTC-USDT": _candles(_rising_closes(3), "BTC-USDT")}
        result = _filter(min_obs=5).evaluate("ETH-USDT", candidate, active)
        assert result.acceptable is False
        assert result.status == CorrelationStatus.DATA_MISSING

    def test_zero_variance_series_fails_gracefully(self):
        candidate = _candles([100.0] * 10, "ETH-USDT")  # zero variance
        active = {"BTC-USDT": _candles(_rising_closes(10), "BTC-USDT")}
        result = _filter(min_obs=5).evaluate("ETH-USDT", candidate, active)
        assert result.acceptable is False
        assert result.status == CorrelationStatus.DATA_MISSING

    def test_mismatched_observations_handled(self):
        candidate = _candles(_rising_closes(20), "ETH-USDT")
        active = {"BTC-USDT": _candles(_rising_closes(10), "BTC-USDT")}
        result = _filter(min_obs=5).evaluate("ETH-USDT", candidate, active)
        # Should align to the overlapping window and still produce a result.
        assert result.observed_correlations["BTC-USDT"] is not None

    def test_multiple_active_symbols(self):
        candidate = _candles(_rising_closes(10), "ETH-USDT")
        active = {
            "BTC-USDT": _candles(_rising_closes(10), "BTC-USDT"),
            "SOL-USDT": _candles([50, 49, 51, 48, 52, 47, 53, 46, 54, 45], "SOL-USDT"),
        }
        result = _filter(min_obs=5).evaluate("ETH-USDT", candidate, active)
        assert set(result.active_symbols) == {"BTC-USDT", "SOL-USDT"}

    def test_one_excessive_correlation_rejects(self):
        candidate_closes = _rising_closes(10)
        candidate = _candles(candidate_closes, "ETH-USDT")
        active = {
            "BTC-USDT": _candles(candidate_closes, "BTC-USDT"),  # identical -> too high
            "SOL-USDT": _candles([50, 49, 51, 48, 52, 47, 53, 46, 54, 45], "SOL-USDT"),  # low
        }
        result = _filter(min_obs=5).evaluate("ETH-USDT", candidate, active)
        assert result.acceptable is False
        assert result.status == CorrelationStatus.TOO_HIGH

    def test_inputs_not_mutated(self):
        candidate = _candles(_rising_closes(10), "ETH-USDT")
        active = {"BTC-USDT": _candles(_rising_closes(10), "BTC-USDT")}
        candidate_snapshot = [c.model_copy() for c in candidate]
        active_snapshot = {k: [c.model_copy() for c in v] for k, v in active.items()}

        _filter(min_obs=5).evaluate("ETH-USDT", candidate, active)

        assert candidate == candidate_snapshot
        assert active == active_snapshot

    def test_constructor_validation(self):
        with pytest.raises(RiskCalculationError):
            CorrelationFilter(maximum_allowed_correlation=1.5, minimum_observations=5)
        with pytest.raises(RiskCalculationError):
            CorrelationFilter(maximum_allowed_correlation=0.8, minimum_observations=0)
