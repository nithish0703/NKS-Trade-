"""
Unit tests for app.strategy_pipeline.ifvg (Stage 4: IFVG).
"""

from datetime import datetime, timedelta, timezone

from app.models.candle import Candle
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType
from app.strategy_pipeline.ifvg import evaluate_ifvg

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYMBOL = "BTC-USDT"
TIMEFRAME = "15m"


def _candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
    )


def _fvg_zone(
    *,
    direction: str,
    lower_price: float,
    upper_price: float,
    source_candle_index: int,
) -> TradeZone:
    ts = UTC_NOW + timedelta(minutes=source_candle_index)
    return TradeZone(
        zone_id=f"fvg-{direction}-{source_candle_index}",
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        zone_type=ZoneType.FAIR_VALUE_GAP,
        direction=direction,
        lower_price=lower_price,
        upper_price=upper_price,
        created_at=ts,
        source_candle_timestamp=ts,
        source_candle_index=source_candle_index,
        status=ZoneStatus.FRESH,
        touch_count=0,
    )


class TestEvaluateIfvgBuy:
    def test_bearish_fvg_flips_and_is_retested_for_a_buy(self):
        # A bearish FVG (resistance) sits at [100, 105], created at
        # index 2. Candles 0-4 stay at/below the zone (close=103, never
        # closes through 105). Candle 5 closes at 110 (fully above 105
        # -> flips it into bullish support). Candle 8 trades back down
        # into [100, 105] (low=102, high=104) -> retest confirmed.
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=2)
        candles = [_candle(i, 102, 104, 101, 103) for i in range(0, 5)]  # indices 0-4, close=103 <= 105
        candles.append(_candle(5, 108, 111, 108, 110))  # flip candle: close=110 > 105
        candles += [_candle(i, 112, 113, 111, 112) for i in range(6, 8)]  # indices 6-7, still above
        candles.append(_candle(8, 104, 104, 102, 103))  # retest: low=102 <= 105, high=104 >= 100

        result = evaluate_ifvg(candles, [zone], expected_direction="BUY")

        assert result.passed is True
        assert result.source_fvg.zone_id == zone.zone_id
        assert result.ifvg_zone.direction == "BUY"
        assert result.flip_candle_index == 5
        assert result.retest_candle_index == 8

    def test_bullish_fvg_never_flips_a_buy(self):
        # A bullish (BUY) FVG is not an obstacle for a BUY setup -- only
        # an opposing (SELL) FVG can flip into a BUY IFVG.
        zone = _fvg_zone(direction="BUY", lower_price=100.0, upper_price=105.0, source_candle_index=2)
        candles = [_candle(i, 108, 109, 107, 108) for i in range(0, 10)]

        result = evaluate_ifvg(candles, [zone], expected_direction="BUY")

        assert result.passed is False
        assert result.ifvg_zone is None

    def test_no_flip_yet_fails_with_a_clear_reason(self):
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=2)
        # Price never closes above 105.
        candles = [_candle(i, 102, 104, 101, 103) for i in range(0, 10)]

        result = evaluate_ifvg(candles, [zone], expected_direction="BUY")

        assert result.passed is False
        assert result.flip_candle_index is None
        assert "closed through" in result.reason.lower()

    def test_flip_without_retest_fails(self):
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=2)
        candles = [_candle(i, 102, 104, 101, 103) for i in range(0, 5)]  # close=103 <= 105
        candles.append(_candle(5, 108, 111, 108, 110))  # flip candle
        # No later candle ever trades back down into [100, 105].
        candles += [_candle(i, 112, 113, 111, 112) for i in range(6, 10)]

        result = evaluate_ifvg(candles, [zone], expected_direction="BUY")

        assert result.passed is False
        assert result.flip_candle_index is not None
        assert result.retest_candle_index is None
        assert "not retested" in result.reason.lower()

    def test_flip_must_occur_strictly_after_the_source_candle(self):
        # A candle at or before the zone's own source candle can never
        # count as the flip, even if its close numerically qualifies.
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=5)
        candles = [_candle(i, 108, 109, 107, 108) for i in range(0, 5)]  # indices 0-4, close=108 > 105
        candles.append(_candle(5, 103, 104, 102, 103))  # the zone's own source candle
        candles += [_candle(i, 103, 104, 102, 103) for i in range(6, 10)]

        result = evaluate_ifvg(candles, [zone], expected_direction="BUY")

        assert result.passed is False
        assert result.flip_candle_index is None


class TestEvaluateIfvgSell:
    def test_bullish_fvg_flips_and_is_retested_for_a_sell(self):
        # A bullish FVG (support) sits at [100, 105], created at index
        # 2. Candle 5 closes at 95 (fully below 100 -> flips it into
        # bearish resistance). Candle 8 retests [100, 105].
        zone = _fvg_zone(direction="BUY", lower_price=100.0, upper_price=105.0, source_candle_index=2)
        candles = [_candle(i, 92, 93, 91, 92) for i in range(0, 5)]
        candles.append(_candle(5, 92, 96, 90, 95))  # flip candle: close=95 < 100
        candles += [_candle(i, 90, 91, 89, 90) for i in range(6, 8)]
        candles.append(_candle(8, 102, 104, 101, 102))  # retest

        result = evaluate_ifvg(candles, [zone], expected_direction="SELL")

        assert result.passed is True
        assert result.ifvg_zone.direction == "SELL"

    def test_bearish_fvg_never_flips_a_sell(self):
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=2)
        candles = [_candle(i, 92, 93, 91, 92) for i in range(0, 10)]

        result = evaluate_ifvg(candles, [zone], expected_direction="SELL")

        assert result.passed is False


class TestEvaluateIfvgSelection:
    def test_most_recently_created_qualifying_zone_is_preferred(self):
        older_zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=1)
        newer_zone = _fvg_zone(direction="SELL", lower_price=200.0, upper_price=205.0, source_candle_index=6)

        candles = [_candle(i, 108, 109, 107, 108) for i in range(0, 8)]
        candles.append(_candle(8, 208, 211, 208, 210))  # flips the NEWER zone (closes > 205)
        candles.append(_candle(9, 203, 204, 202, 203))  # retests the NEWER zone

        result = evaluate_ifvg(candles, [older_zone, newer_zone], expected_direction="BUY")

        assert result.passed is True
        assert result.source_fvg.zone_id == newer_zone.zone_id


class TestEvaluateIfvgEmptyInputs:
    def test_no_candles_fails(self):
        zone = _fvg_zone(direction="SELL", lower_price=100.0, upper_price=105.0, source_candle_index=2)
        result = evaluate_ifvg([], [zone], expected_direction="BUY")
        assert result.passed is False

    def test_no_fvg_zones_fails(self):
        candles = [_candle(i, 100, 101, 99, 100) for i in range(5)]
        result = evaluate_ifvg(candles, [], expected_direction="BUY")
        assert result.passed is False
        assert result.ifvg_zone is None
