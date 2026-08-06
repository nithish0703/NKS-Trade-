"""
Configuration of numerical thresholds used by validators and scoring.
"""

from typing import Final

# Trend strength (ADX)
ADX_TRENDING_MIN: Final[float] = 25.0
ADX_REJECTION_MAX: Final[float] = 20.0

# Candle quality
DISPLACEMENT_CANDLE_MIN_BODY_RATIO: Final[float] = 0.60

# Indicator periods
VOLUME_EMA_PERIOD: Final[int] = 20
EMA_FAST_PERIOD: Final[int] = 20
EMA_SLOW_PERIOD: Final[int] = 50
EMA_TREND_PERIOD: Final[int] = 200

# Risk management
MAX_RISK_PER_TRADE: Final[float] = 0.01
MIN_RISK_REWARD_RATIO: Final[float] = 1.80
MAX_ACTIVE_TRADES: Final[int] = 5
ATR_STOP_LOSS_MULTIPLIER: Final[float] = 1.5

# Scanner
SCANNER_INTERVAL_SECONDS: Final[int] = 300

# Trade outcome monitor: how often ACTIVE (dashboard "Trade" button)
# signals are re-checked against the latest exchange ticker price to
# see if take_profit or stop_loss has been touched.
TRADE_OUTCOME_MONITOR_INTERVAL_SECONDS: Final[int] = 60

# Dynamic Liquidity + Open Interest coin-discovery configuration.
# The refresh interval defaults to 15 minutes. Binance Futures' public
# 24hr ticker endpoint returns turnover for every USDT-M perpetual in a
# single call; Open Interest still requires one request per
# turnover-qualifying candidate (Binance has no bulk OI-value endpoint),
# so unlike turnover this refresh's cost does scale with the number of
# candidates -- kept at 15 minutes as a reasonable balance.
PAIR_DISCOVERY_INTERVAL_SECONDS: Final[int] = 900
PAIR_DISCOVERY_MINIMUM_OPEN_INTEREST_USDT: Final[float] = 5_000_000.0
PAIR_DISCOVERY_MINIMUM_TURNOVER_24H_USDT: Final[float] = 10_000_000.0

# Warm-up fetch for newly discovered pairs (see app.scanner.pair_discovery).
# A brand-new symbol's first full candle-history fetch has no cached
# fallback and no safety margin, so it gets a more patient retry
# schedule than the routine per-cycle fetch used once a symbol is
# already in rotation -- this only delays a new symbol's first
# appearance on a transient failure; it never changes retry behaviour
# for symbols already being scanned.
PAIR_WARMUP_MAX_REQUEST_ATTEMPTS: Final[int] = 5
PAIR_WARMUP_RETRY_BACKOFF_SCHEDULE_SECONDS: Final[tuple[float, ...]] = (2.0, 4.0, 8.0, 15.0)

# Signal scoring
PREMIUM_SIGNAL_MIN_SCORE: Final[float] = 90.0
STRONG_SIGNAL_MIN_SCORE: Final[float] = 80.0
MEDIUM_SIGNAL_MIN_SCORE: Final[float] = 70.0

# Indicator calculation configuration
ATR_PERIOD: Final[int] = 14
ADX_PERIOD: Final[int] = 14
EMA_SLOPE_LOOKBACK: Final[int] = 5
EMA_FLAT_THRESHOLD: Final[float] = 0.0005
ATR_EXPANSION_LOOKBACK: Final[int] = 20

# Market structure detection configuration
SWING_LEFT_STRENGTH: Final[int] = 3
SWING_RIGHT_STRENGTH: Final[int] = 3
SWING_EQUALITY_TOLERANCE: Final[float] = 0.001
MINIMUM_CONFIRMED_SWINGS: Final[int] = 4

# Liquidity detection configuration
LIQUIDITY_EQUALITY_TOLERANCE: Final[float] = 0.001
EQUAL_LEVEL_MINIMUM_TOUCHES: Final[int] = 2
EQUAL_LEVEL_MAXIMUM_GROUP_SPAN: Final[int] = 100
MAJOR_SWING_MINIMUM_STRENGTH: Final[int] = 3
LIQUIDITY_MINIMUM_PENETRATION_RATIO: Final[float] = 0.0001
LIQUIDITY_MAXIMUM_RECLAIM_CANDLES: Final[int] = 2

# Displacement / structure shift detection configuration
BULLISH_DISPLACEMENT_CLOSE_LOCATION_MIN: Final[float] = 0.75
BEARISH_DISPLACEMENT_CLOSE_LOCATION_MAX: Final[float] = 0.25

# Zone lifecycle detection configuration
ZONE_TOUCH_TOLERANCE_RATIO: Final[float] = 0.0001
FULL_ZONE_MITIGATION_REQUIRED: Final[bool] = False

# Premium/Discount dealing-range configuration
DEALING_RANGE_EQUILIBRIUM_TOLERANCE_RATIO: Final[float] = 0.001
DEALING_RANGE_MIDDLE_TOLERANCE_RATIO: Final[float] = 0.05

# Retest confirmation configuration
RETEST_MINIMUM_REJECTION_BODY_RATIO: Final[float] = 0.50
RETEST_BULLISH_CLOSE_LOCATION_MINIMUM: Final[float] = 0.65
RETEST_BEARISH_CLOSE_LOCATION_MAXIMUM: Final[float] = 0.35
RETEST_MINIMUM_REJECTION_WICK_RATIO: Final[float] = 0.15
RETEST_MAXIMUM_CONFIRMATION_CANDLES: Final[int] = 3

# Market regime / volatility / candle-quality validation configuration
MARKET_REGIME_MINIMUM_ATR_EXPANSION_RATIO: Final[float] = 1.0
VOLATILITY_MINIMUM_ATR_VALUE: Final[float] = 0.00000001
VOLATILITY_MINIMUM_ATR_EXPANSION_RATIO: Final[float] = 1.0
VOLATILITY_COMPRESSION_LOOKBACK: Final[int] = 10
VOLATILITY_MINIMUM_CANDLE_RANGE_RATIO: Final[float] = 0.50
FAKE_BREAKOUT_MAXIMUM_REVERSAL_CANDLES: Final[int] = 3
FAKE_BREAKOUT_RETURN_INSIDE_TOLERANCE_RATIO: Final[float] = 0.0001
DOJI_MAXIMUM_BODY_RATIO: Final[float] = 0.10
SPINNING_TOP_MAXIMUM_BODY_RATIO: Final[float] = 0.30
CANDLE_MAXIMUM_OPPOSITE_WICK_RATIO: Final[float] = 0.35
CANDLE_QUALITY_BULLISH_CLOSE_LOCATION_MINIMUM: Final[float] = 0.75
CANDLE_QUALITY_BEARISH_CLOSE_LOCATION_MAXIMUM: Final[float] = 0.25

# Risk-management calculation configuration
STOP_LOSS_STRUCTURAL_BUFFER_RATIO: Final[float] = 0.0005
MAXIMUM_ALLOWED_POSITION_CORRELATION: Final[float] = 0.80
CORRELATION_MINIMUM_OBSERVATIONS: Final[int] = 30

# Confidence scoring layer weights (must sum to SCORE_MAXIMUM_RAW)
# Hard-mandatory layers (pipeline gates; failure rejects before scoring runs):
SCORE_MARKET_REGIME: Final[int] = 15
SCORE_HTF_BIAS: Final[int] = 25
SCORE_LIQUIDITY_SWEEP: Final[int] = 15
SCORE_STRUCTURE_SHIFT: Final[int] = 15
SCORE_VOLUME_CONFIRMATION: Final[int] = 10
SCORE_ENTRY_ZONE: Final[int] = 10
# Soft-scoring layers (never reject; failure awards zero points only):
SCORE_PREMIUM_DISCOUNT: Final[int] = 5
SCORE_RETEST_CONFIRMATION: Final[int] = 5
SCORE_SESSION: Final[int] = 5
SCORE_BTC_ALIGNMENT: Final[int] = 5
SCORE_FAKE_BREAKOUT: Final[int] = 5
SCORE_MAXIMUM_RAW: Final[int] = 115

# Confidence classification thresholds
PREMIUM_SIGNAL_MINIMUM_SCORE: Final[float] = 90.0
MEDIUM_SIGNAL_MINIMUM_SCORE: Final[float] = 70.0
# STRONG_SIGNAL_MINIMUM_SCORE (the publishable-signal cutoff) moved to
# app.config.settings.Settings.min_publishable_confidence_score so
# signal frequency can be tuned via the MIN_PUBLISHABLE_CONFIDENCE_SCORE
# env var without a code change/redeploy. Default is unchanged (80.0).

# Stage 5 (Order Flow) Open Interest fetch retry configuration. The
# provider itself never raises (it swallows every failure into an
# empty list), so an empty result -- not an exception -- is the
# retryable signal: API delay is often transient, so it's worth a
# short retry before treating OI confirmation as UNAVAILABLE for this
# scan. Kept small (well under the scan cycle interval) so a
# persistently unavailable OI feed never meaningfully slows scanning.
OPEN_INTEREST_FETCH_MAX_ATTEMPTS: Final[int] = 2
OPEN_INTEREST_FETCH_RETRY_BACKOFF_SECONDS: Final[float] = 0.5

# Stage 4 (IFVG) validity windows, in candles after a confirmed BOS's
# break_candle_index. N1 bounds how long the tighter IFVG flip+retest
# path (Grade A) is looked for before falling back to the wider,
# coarser BOS-zone retest path (Grade B), which gets its own,
# independent allowance N2 -- a coarser zone can reasonably be granted
# a different (typically longer) window than the tighter IFVG zone
# without the two being coupled to the same constant.
IFVG_VALIDITY_WINDOW_CANDLES: Final[int] = 12
BOS_ZONE_RETEST_VALIDITY_WINDOW_CANDLES: Final[int] = 20
