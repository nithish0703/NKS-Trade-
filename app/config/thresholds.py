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
MIN_RISK_REWARD_RATIO: Final[float] = 2.0
MAX_ACTIVE_TRADES: Final[int] = 5
ATR_STOP_LOSS_MULTIPLIER: Final[float] = 1.5

# Scanner
SCANNER_INTERVAL_SECONDS: Final[int] = 15

# Signal scoring
MIN_PUBLISHABLE_CONFIDENCE_SCORE: Final[float] = 80.0
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
SCORE_MARKET_REGIME: Final[int] = 15
SCORE_HTF_BIAS: Final[int] = 25
SCORE_LIQUIDITY_SWEEP: Final[int] = 15
SCORE_STRUCTURE_SHIFT: Final[int] = 15
SCORE_VOLUME_CONFIRMATION: Final[int] = 10
SCORE_ENTRY_ZONE: Final[int] = 10
SCORE_PREMIUM_DISCOUNT: Final[int] = 5
SCORE_RETEST_CONFIRMATION: Final[int] = 5
SCORE_ATR: Final[int] = 5
SCORE_SESSION: Final[int] = 5
SCORE_BTC_ALIGNMENT: Final[int] = 5
SCORE_FAKE_BREAKOUT: Final[int] = 5
SCORE_MAXIMUM_RAW: Final[int] = 120

# Confidence classification thresholds
PREMIUM_SIGNAL_MINIMUM_SCORE: Final[float] = 90.0
STRONG_SIGNAL_MINIMUM_SCORE: Final[float] = 80.0
MEDIUM_SIGNAL_MINIMUM_SCORE: Final[float] = 70.0
MINIMUM_PUBLISHABLE_CONFIDENCE_SCORE: Final[float] = 80.0
