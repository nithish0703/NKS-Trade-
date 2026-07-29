"""
Tests for app.notifications.telegram_formatter.TelegramSignalFormatter.
"""

from datetime import datetime, timezone

from app.models.signal import Direction, MarketRegime, Signal, SignalType
from app.notifications.telegram_formatter import TelegramSignalFormatter

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _signal(
    *,
    direction=Direction.BUY,
    signal_type=SignalType.PREMIUM,
    entry_price=100.0,
    stop_loss=95.0,
    take_profit=110.0,
    institutional_reason="Confirmed setup facts only. Not a guarantee of outcome.",
    coin="BTC-USDT",
) -> Signal:
    return Signal(
        trade_id="SMC-BTC-USDT-BUY-abc123",
        coin=coin,
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward_ratio=3.0,
        confidence_score=95.8,
        signal_type=signal_type,
        market_regime=MarketRegime.TRENDING,
        higher_timeframe_bias="BULLISH" if direction == Direction.BUY else "BEARISH",
        liquidity_type="EQUAL_HIGH",
        entry_zone_type="ORDER_BLOCK",
        structure_confirmation="BOS",
        volume_confirmation=True,
        atr_status="EXPANDING",
        trading_session="LONDON",
        btc_market_alignment=True,
        detection_time_utc=UTC_NOW,
        institutional_reason=institutional_reason,
        setup_key="setup-1",
        liquidity_sweep_id="sweep-1",
        structure_break_id="break-1",
        entry_zone_id="zone-1",
        retest_id="retest-1",
        created_at_utc=UTC_NOW,
    )


class TestFormatSignal:
    def test_premium_buy_formatting(self):
        formatter = TelegramSignalFormatter()
        message = formatter.format_signal(_signal(signal_type=SignalType.PREMIUM, direction=Direction.BUY))
        assert "NKS PREMIUM SIGNAL" in message
        assert "Direction: <b>BUY</b>" in message

    def test_strong_sell_formatting(self):
        formatter = TelegramSignalFormatter()
        message = formatter.format_signal(
            _signal(
                signal_type=SignalType.STRONG,
                direction=Direction.SELL,
                entry_price=100.0,
                stop_loss=105.0,
                take_profit=90.0,
            )
        )
        assert "NKS STRONG SIGNAL" in message
        assert "Direction: <b>SELL</b>" in message

    def test_exactly_one_take_profit(self):
        formatter = TelegramSignalFormatter()
        message = formatter.format_signal(_signal())
        assert message.count("Take Profit:") == 1

    def test_no_tp2_tp3(self):
        formatter = TelegramSignalFormatter()
        message = formatter.format_signal(_signal())
        assert "TP2" not in message
        assert "TP3" not in message
        assert "Take Profit 2" not in message

    def test_all_required_fields_included(self):
        formatter = TelegramSignalFormatter()
        message = formatter.format_signal(_signal())
        for field_label in (
            "Coin:",
            "Direction:",
            "Entry:",
            "Stop Loss:",
            "Take Profit:",
            "Risk Reward:",
            "Confidence:",
            "Session:",
            "Detected:",
        ):
            assert field_label in message

    def test_extended_context_fields_removed(self):
        formatter = TelegramSignalFormatter()
        message = formatter.format_signal(_signal())
        for removed_label in (
            "Trade ID:",
            "Market Regime:",
            "HTF Bias:",
            "Liquidity:",
            "Entry Zone:",
            "Structure:",
            "Volume:",
            "ATR:",
            "BTC Alignment:",
        ):
            assert removed_label not in message

    def test_institutional_reason_not_included(self):
        formatter = TelegramSignalFormatter()
        message = formatter.format_signal(
            _signal(institutional_reason="A distinctive unique reason marker XYZ123.")
        )
        assert "XYZ123" not in message
        assert "Institutional Reason:" not in message

    def test_html_characters_escaped(self):
        formatter = TelegramSignalFormatter()
        message = formatter.format_signal(
            _signal(coin="<script>alert('x')</script> & <b>bold</b>")
        )
        assert "<script>" not in message
        assert "&lt;script&gt;" in message
        assert "&amp;" in message

    def test_message_length_safely_limited(self):
        formatter = TelegramSignalFormatter()
        message = formatter.format_signal(_signal())
        assert len(message) <= 4096

    def test_deterministic_output(self):
        formatter = TelegramSignalFormatter()
        signal = _signal()
        message_one = formatter.format_signal(signal)
        message_two = formatter.format_signal(signal)
        assert message_one == message_two

    def test_no_token_or_chat_id(self):
        formatter = TelegramSignalFormatter()
        message = formatter.format_signal(_signal())
        assert "bot_token" not in message.lower()
        assert "chat_id" not in message.lower()
        assert "token" not in message.lower()

    def test_coin_direction_entry_sl_tp_highlighted(self):
        formatter = TelegramSignalFormatter()
        message = formatter.format_signal(_signal())
        assert "Coin: <b>BTC-USDT</b>" in message
        assert "Direction: <b>BUY</b>" in message
        assert "Entry: <b>100</b>" in message
        assert "Stop Loss: <b>95</b>" in message
        assert "Take Profit: <b>110</b>" in message

    def test_no_guaranteed_win_language(self):
        # "not a guarantee of outcome" is a permitted disclaimer; only an
        # affirmative guaranteed-win claim would be forbidden.
        formatter = TelegramSignalFormatter()
        message = formatter.format_signal(_signal())
        assert "guaranteed win" not in message.lower()
        assert "100% win" not in message.lower()
        assert "sure thing" not in message.lower()
        assert "risk-free" not in message.lower()


class TestFormatTestMessage:
    def test_test_message_content(self):
        formatter = TelegramSignalFormatter()
        message = formatter.format_test_message()
        assert message == "✅ Telegram test successful. NKS Trade bot is connected."
