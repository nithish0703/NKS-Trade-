"""
Manual connectivity check against the real Bybit public market-data API.

This script is not run automatically as part of the pytest suite. It
performs real network requests to verify that the BybitMarketDataProvider
can successfully reach Bybit and fetch completed candles.

Usage:
    python scripts/check_bybit_connection.py
"""

import asyncio
import sys

from app.config.pairs import BTC_SYMBOL
from app.config.settings import get_settings
from app.data.bybit_market_data_provider import BybitMarketDataProvider
from app.data.market_data_errors import MarketDataError

TIMEFRAMES = ["15m", "1h", "4h"]


async def main() -> None:
    """Fetch BTC-USDT candles for 15m, 1h, and 4h and print a brief summary."""
    settings = get_settings()

    try:
        async with BybitMarketDataProvider(
            base_url=settings.exchange_base_url,
            request_timeout_seconds=settings.request_timeout_seconds,
        ) as provider:
            results = await provider.fetch_multiple_timeframes(BTC_SYMBOL, TIMEFRAMES)
    except MarketDataError as exc:
        print("Bybit connectivity check failed.", file=sys.stderr)
        # str(exc) already carries the safe, structured diagnostic
        # (exception class, timeout/connect/DNS/SSL classification, and
        # endpoint host) built by market_data_provider's error handling;
        # it never includes credentials or full response bodies.
        print(f"reason={exc}", file=sys.stderr)
        sys.exit(1)

    for timeframe, candles in results.items():
        if candles:
            oldest = candles[0].timestamp.isoformat()
            newest = candles[-1].timestamp.isoformat()
        else:
            oldest = "N/A"
            newest = "N/A"

        print(f"symbol={BTC_SYMBOL}")
        print(f"timeframe={timeframe}")
        print(f"completed_candles={len(candles)}")
        print(f"oldest_timestamp={oldest}")
        print(f"newest_timestamp={newest}")
        print("-")


if __name__ == "__main__":
    asyncio.run(main())
