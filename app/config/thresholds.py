"""
Configuration of numerical thresholds used by validators and scoring.
"""

from typing import Final

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

# Signal outcome monitor: how often EVERY CONFIRMED signal (regardless
# of dashboard_status) is re-checked, via a single bulk ticker-price
# fetch, against its take_profit/stop_loss to see if either has been
# touched. Feeds the Phase 1 performance report's full-sample outcome
# tracking; when a signal also happens to be dashboard-ACTIVE (the
# "Trade" button workflow) at close time, the same pass also mirrors
# the outcome onto dashboard_status. One shared schedule -- there is no
# separate dashboard-only polling loop.
SIGNAL_OUTCOME_MONITOR_INTERVAL_SECONDS: Final[int] = 60

# A CONFIRMED signal that never touches take_profit or stop_loss is
# force-closed as TIMEOUT after this many entry-timeframe (15m) candles
# have elapsed since detection, matching the Phase 4 backtest's
# MAX_TRADE_DURATION_CANDLES convention. 96 candles * 15m = 24 hours.
# Without this, open signals accumulate forever and the performance
# report's win-rate denominator excludes every signal that never
# resolves, silently inflating the win rate.
MAX_TRACKED_SIGNAL_DURATION_CANDLES: Final[int] = 96

# If a cycle's bulk ticker fetch has no price at all for a signal's
# symbol (delisted, temporarily absent from the response, etc.), the
# signal is left open and retried on the next cycle rather than
# force-closed immediately -- a single missing price must never be
# recorded as a fabricated 0R break-even. Only after this many
# *consecutive* cycles with no price for that symbol is it force-closed
# as UNRESOLVED (a distinct outcome, excluded from performance-report
# expectancy math, never counted as WIN/LOSS/TIMEOUT).
MAX_CONSECUTIVE_MISSING_PRICE_CYCLES: Final[int] = 10

# SignalOutcomeMonitor renews a DB-backed lease every cycle so at most
# one process (e.g. `python main.py scan` and the dashboard API) does
# outcome-tracking work at a time even when both point at the same
# SQLite database. The lease is granted for this many multiples of the
# monitor's poll interval, so a crashed holder's lease expires and the
# other process can take over within a bounded number of missed cycles,
# while a slow-but-alive single holder never loses its own lease
# between renewals.
SIGNAL_OUTCOME_MONITOR_LEASE_DURATION_MULTIPLIER: Final[int] = 3

# Same lease mechanism (see app.storage.monitor_lease.MonitorLeaseGuard),
# applied to the scan/candle-fetch loop itself rather than just the
# SignalOutcomeMonitor: `python main.py scan` and the dashboard API's
# uvicorn process each build a fully independent ScannerService (see
# app.scanner.engine_factory.build_scanner_service), so running both
# against the same database previously meant every candle fetch was
# duplicated between the two processes with no coordination at all.
SCANNER_LEASE_DURATION_MULTIPLIER: Final[int] = 3

# Binance Futures IP rate-limit safety (Phase 2 hardening after a live
# 418 IP ban during unattended `python main.py scan` operation).
# REQUEST_WEIGHT is capped at 2400/min per IP across all /fapi/v1
# endpoints combined; the app has no weight accounting of its own today
# beyond a fixed concurrency+spacing gate, which throttles request
# *rate* but not actual Binance-reported weight usage. SOFT_THROTTLE_RATIO
# triggers proactive slowdown once X-MBX-USED-WEIGHT-1M crosses this
# fraction of the hard cap, before Binance itself has to enforce it via
# 429/418.
BINANCE_WEIGHT_LIMIT_PER_MINUTE: Final[int] = 2400
BINANCE_WEIGHT_SOFT_THROTTLE_RATIO: Final[float] = 0.75
# Extra delay inserted before each request while used weight is at or
# above the soft-throttle threshold, on top of the existing
# MIN_REQUEST_INTERVAL_SECONDS gate -- deliberately coarse (not a
# precise token-bucket refill) since X-MBX-USED-WEIGHT-1M is only
# refreshed once per response, not continuously.
BINANCE_WEIGHT_SOFT_THROTTLE_EXTRA_DELAY_SECONDS: Final[float] = 1.0

# Fallback global cooldown when a 418/429 response carries no
# Retry-After header at all. A real IP ban (418) typically lasts
# minutes, not the few seconds of the ordinary per-request retry
# backoff schedule, so this is deliberately much longer than
# RETRY_BACKOFF_SCHEDULE_SECONDS in app.data.binance_market_data_provider.
GLOBAL_RATE_LIMIT_COOLDOWN_FALLBACK_SECONDS: Final[float] = 60.0

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

# Premium/Discount dealing-range configuration
DEALING_RANGE_EQUILIBRIUM_TOLERANCE_RATIO: Final[float] = 0.001
DEALING_RANGE_MIDDLE_TOLERANCE_RATIO: Final[float] = 0.05  # reserved: used by Phase 3/5

# Market regime / volatility validation configuration
MARKET_REGIME_MINIMUM_ATR_EXPANSION_RATIO: Final[float] = 1.0  # reserved: used by Phase 3/5
VOLATILITY_COMPRESSION_LOOKBACK: Final[int] = 10  # reserved: used by Phase 3/5

# Risk-management calculation configuration
STOP_LOSS_STRUCTURAL_BUFFER_RATIO: Final[float] = 0.0005
MAXIMUM_ALLOWED_POSITION_CORRELATION: Final[float] = 0.80
CORRELATION_MINIMUM_OBSERVATIONS: Final[int] = 30

# Stop-loss volatility gating. A candidate closer to entry than
# MIN_STOP_ATR_MULTIPLIER * ATR sits inside normal market noise and is
# routinely swept by wicks that never actually invalidate the setup --
# this is what produces "stopped out then went straight to target"
# false signals. A candidate farther than MAX_STOP_ATR_MULTIPLIER * ATR
# is rejected as unreasonably wide (bad position sizing / RR math).
# ATR_STOP_LOSS_MULTIPLIER (1.5) is kept strictly between these two so
# the raw-ATR candidate itself always lands inside the accepted band.
MIN_STOP_ATR_MULTIPLIER: Final[float] = 0.75
MAX_STOP_ATR_MULTIPLIER: Final[float] = 3.0

# Take-profit volatility ceiling. A target farther than this many ATRs
# from entry is not realistically reachable within the setup's expected
# holding period and is rejected as "unrealistically far" rather than
# silently selected just because its RR number looks attractive.
MAX_TAKE_PROFIT_ATR_MULTIPLIER: Final[float] = 8.0

# Dynamic minimum risk-reward, keyed by which stop-loss source was
# selected. A stop anchored to the swept liquidity extreme is the most
# structurally certain invalidation point (price already proved it by
# sweeping there), so it keeps the base minimum. A stop anchored only
# to the entry-zone boundary is less certain and is required to earn a
# bit more reward. A stop that fell back to raw ATR (no structural
# confirmation at all) is required to earn the most, since there is no
# structural reason to believe that level actually invalidates the idea.
MIN_RISK_REWARD_BY_STOP_SOURCE: Final[dict] = {
    "LIQUIDITY_SWEEP": 1.80,
    "ENTRY_ZONE": 2.00,
    "ATR": 2.50,
}

# Partial exits. TP1 locks in a conservative slice of the position at a
# fixed, modest risk-reward so a trade that reverses after moving in
# favor still books a partial win; TP2 is the full, structurally
# selected target for the remainder of the position.
PARTIAL_EXIT_TP1_RISK_REWARD_RATIO: Final[float] = 1.0
PARTIAL_EXIT_TP1_PERCENTAGE: Final[float] = 0.50
PARTIAL_EXIT_TP2_PERCENTAGE: Final[float] = 0.50

# Stage 5 (Order Flow) Volume Profile confirmation configuration. The
# profile is built entirely from already-fetched entry-timeframe
# candles (no additional market-data fetch), over the most recent
# `lookback` candles, split into `bins` equal-width price buckets.
# `value_area` is the percentage of total volume the Value Area
# (VAH/VAL) is expanded to hold, expanding outward from the POC bin.
VOLUME_PROFILE_ENABLED: Final[bool] = True
VOLUME_PROFILE_LOOKBACK: Final[int] = 200
VOLUME_PROFILE_BINS: Final[int] = 100
VOLUME_PROFILE_VALUE_AREA_PERCENT: Final[float] = 70.0

# A bin is classified a High Volume Node when its volume is at least
# this fraction of the profile's busiest (POC) bin, and a Low Volume
# Node when at or below this fraction -- both only among bins that
# carried any volume at all, so an untraded bin is never mistaken for
# a genuine low-volume node.
VOLUME_PROFILE_HVN_THRESHOLD_RATIO: Final[float] = 0.70
VOLUME_PROFILE_LVN_THRESHOLD_RATIO: Final[float] = 0.15

# How close current price must be to a node/POC/VAH/VAL to count as
# "at" it, expressed as a fraction of current price.
VOLUME_PROFILE_PROXIMITY_RATIO: Final[float] = 0.002

# Stage 6 (Risk Management) entry-price anchoring. The historical bug
# was using the latest closed candle's close as the entry price even
# though Stage 4 (IFVG) already selected a specific entry zone -- if
# the zone's retest happened several candles ago, the latest close can
# sit far away from where the trade would actually be entered, which
# silently corrupts stop distance, risk:reward, and position sizing.
# ZONE_MIDPOINT anchors to the selected zone's center; ZONE_EDGE anchors
# to the boundary a retracement into the zone touches first (the lower
# edge for a BUY, the upper edge for a SELL); LAST_CLOSE preserves the
# old (buggy) behaviour as an explicit, named opt-out rather than
# silently removing the option. Phase 4's backtest will determine which
# anchor produces better realized R:R; kept configurable rather than
# hardcoded for that reason.
ENTRY_PRICE_ANCHOR: Final[str] = "ZONE_MIDPOINT"  # ZONE_MIDPOINT | ZONE_EDGE | LAST_CLOSE

# Freshness gate paired with ENTRY_PRICE_ANCHOR: if the latest closed
# candle's close has already moved more than this many ATRs away from
# the anchored entry price, the setup is stale -- price has left the
# zone the trade idea was actually built around, so entering there no
# longer reflects the entry-location analysis that passed Stage 4.
# Rejected explicitly (naming the ATR distance) rather than silently
# entered at a price the strategy never actually validated.
ENTRY_ZONE_MAX_DISTANCE_ATR: Final[float] = 0.5

# Stage 4 (IFVG) validity windows, in candles after a confirmed BOS's
# break_candle_index. N1 bounds how long the tighter IFVG flip+retest
# path (Grade A) is looked for before falling back to the wider,
# coarser BOS-zone retest path (Grade B), which gets its own,
# independent allowance N2 -- a coarser zone can reasonably be granted
# a different (typically longer) window than the tighter IFVG zone
# without the two being coupled to the same constant.
IFVG_VALIDITY_WINDOW_CANDLES: Final[int] = 12
BOS_ZONE_RETEST_VALIDITY_WINDOW_CANDLES: Final[int] = 20
