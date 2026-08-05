"""
Entry point for the Institutional Smart Money Concepts trading engine.

This module provides five manual, development-facing CLI modes:

    python main.py analyze BTC-USDT               # one-symbol pipeline analysis
    python main.py scan-once                      # one full multi-pair scan cycle
    python main.py scan                           # continuous 15-second scanner
    python main.py signals --symbol BTC-USDT       # list locally stored signals
    python main.py telegram-test                  # send one Telegram test message to every configured chat

None of these modes implement a dashboard, WebSocket broadcasting, live
trading, or TradingView integration. `scan-once` and `scan` persist
PREMIUM/STRONG signals to local SQLite storage only when persistence is
enabled, and send a Telegram notification for each newly stored,
non-duplicate signal to every chat ID configured in TELEGRAM_CHAT_IDS,
only when Telegram is enabled in settings.
"""

import asyncio
import signal
import sys
from typing import Optional

from app.config.pairs import BTC_SYMBOL
from app.data.market_data_errors import MarketDataError
from app.scanner.engine_factory import (
    build_scanner_service,
    build_strategy_engine,
    dispose_scanner_notifications,
    initialize_scanner_storage,
)
from app.scanner.pipeline_exceptions import StrategyPipelineError
from app.scanner.pipeline_results import PipelineStatus
from app.scanner.scan_results import PairScanStatus, ScanCycleResult
from app.storage.database import DatabaseManager
from app.storage.signal_repository import SignalRepository

_DEFAULT_DEVELOPMENT_BALANCE = 10_000.0
_DEFAULT_MODE = "analyze"
_VALID_MODES = ("analyze", "scan-once", "scan", "signals", "telegram-test")


def _parse_args(argv: list[str]) -> tuple[str, float]:
    """Parse an optional symbol and account balance from CLI arguments."""
    symbol = argv[1] if len(argv) > 1 else BTC_SYMBOL
    if len(argv) > 2:
        try:
            balance = float(argv[2])
        except ValueError:
            balance = _DEFAULT_DEVELOPMENT_BALANCE
    else:
        balance = _DEFAULT_DEVELOPMENT_BALANCE
    return symbol, balance


def _parse_mode_and_args(argv: list[str]) -> tuple[str, list[str]]:
    """Determine the CLI mode and the remaining arguments for that mode."""
    if len(argv) > 1 and argv[1] in _VALID_MODES:
        return argv[1], [argv[0]] + argv[2:]
    return _DEFAULT_MODE, argv


def _print_result(result) -> None:
    """Print a concise summary of a StrategyPipelineResult. Never prints candle data or secrets."""
    print(f"symbol={result.symbol}")
    print(f"status={result.status.value}")

    if result.expected_direction:
        print(f"direction={result.expected_direction}")

    if result.status == PipelineStatus.REJECTED:
        print(f"failed_layer={result.failed_layer}")
        print(f"rejection_reason={result.rejection_reason}")
        return

    if result.status == PipelineStatus.VALID and result.risk_plan is not None:
        print(f"entry={result.risk_plan.entry_price}")
        print(f"stop_loss={result.risk_plan.stop_loss_result.selected_stop_loss}")
        print(f"take_profit={result.risk_plan.take_profit_result.selected_take_profit}")
        print(f"risk_reward_ratio={result.risk_plan.risk_reward_ratio}")

    if result.confidence_result is not None:
        print(f"confidence={result.confidence_result.normalized_score}")
        print(f"classification={result.confidence_result.classification.value}")


async def _run_manual_analysis(symbol: str, account_balance: float) -> None:
    """Build the strategy engine and run exactly one analysis for `symbol`."""
    engine = build_strategy_engine()

    try:
        result = await engine.analyze_symbol(
            symbol=symbol,
            account_balance=account_balance,
            active_trade_count=0,
            active_positions=[],
            active_position_candles={},
        )
    except MarketDataError as exc:
        print(f"symbol={symbol}")
        print("status=ERROR")
        print(f"rejection_reason=Market data unavailable: {exc}")
        return
    except StrategyPipelineError as exc:
        print(f"symbol={exc.symbol}")
        print("status=ERROR")
        print(f"failed_layer={exc.layer_name}")
        print(f"rejection_reason={exc.reason}")
        return

    _print_result(result)


def _print_cycle_summary(cycle_result: ScanCycleResult) -> None:
    """Print a concise scan-cycle summary. Never prints candle data or secrets."""
    print(f"cycle_id={cycle_result.cycle_id}")
    print(f"total_pairs={cycle_result.total_pairs}")
    print(f"valid={cycle_result.valid_count}")
    print(f"rejected={cycle_result.rejected_count}")
    print(f"duplicates={cycle_result.duplicate_count}")
    print(f"errors={cycle_result.error_count}")
    print(f"duration_ms={cycle_result.duration_ms:.1f}")

    for pair_result in cycle_result.valid_results:
        pipeline_result = pair_result.pipeline_result
        print(f"  valid: symbol={pair_result.symbol}", end="")
        if pipeline_result is not None:
            print(f" direction={pipeline_result.expected_direction}", end="")
            if pipeline_result.confidence_result is not None:
                print(
                    f" classification={pipeline_result.confidence_result.classification.value}"
                    f" confidence={pipeline_result.confidence_result.normalized_score}",
                    end="",
                )
        print()

    for pair_result in cycle_result.error_results:
        print(
            f"  error: symbol={pair_result.symbol}"
            f" error_type={pair_result.error_type}"
            f" reason={pair_result.reason}"
        )


async def _run_scan_once(account_balance: float) -> None:
    """Build the scanner service and run exactly one multi-pair scan cycle."""
    service = build_scanner_service()
    await initialize_scanner_storage(service)
    try:
        cycle_result = await service.run_single_cycle(account_balance)
        _print_cycle_summary(cycle_result)
    finally:
        await _dispose_scanner_storage(service)


async def _run_scan_forever(account_balance: float) -> None:
    """Start the continuous 15-second multi-pair scanner until Ctrl+C."""
    service = build_scanner_service()
    await initialize_scanner_storage(service)
    loop = asyncio.get_running_loop()

    def _handle_shutdown_signal() -> None:
        print("Shutdown requested; finishing current scan cycle...")
        service.request_shutdown()

    registered_signals: list[int] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_shutdown_signal)
            registered_signals.append(sig)
        except (NotImplementedError, RuntimeError):
            # add_signal_handler is unavailable on some platforms (e.g. Windows);
            # KeyboardInterrupt is handled as a fallback below.
            pass

    try:
        try:
            await service.run_forever(account_balance)
        except KeyboardInterrupt:
            print("Shutdown requested; finishing current scan cycle...")
            service.request_shutdown()
    finally:
        for sig in registered_signals:
            loop.remove_signal_handler(sig)
        await _dispose_scanner_storage(service)

    status = service.get_runtime_status()
    print(f"cycles_completed={status.cycles_completed}")
    print(f"last_cycle_id={status.last_cycle_id}")
    print(f"last_error={status.last_error}")


async def _dispose_scanner_storage(service) -> None:
    """Cleanly dispose of a scanner service's database engine and Telegram client, when present."""
    signal_storage_service = service.signal_storage_service
    if signal_storage_service is not None:
        await signal_storage_service.database_manager.dispose()
    await dispose_scanner_notifications(service)


def _parse_signals_args(argv: list[str]) -> tuple[Optional[str], int]:
    """Parse optional --symbol and --limit flags for the signals CLI mode."""
    symbol: Optional[str] = None
    limit = 20
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg == "--symbol" and index + 1 < len(argv):
            symbol = argv[index + 1]
            index += 2
        elif arg == "--limit" and index + 1 < len(argv):
            try:
                limit = int(argv[index + 1])
            except ValueError:
                limit = 20
            index += 2
        else:
            index += 1
    return symbol, limit


async def _run_signals(symbol: Optional[str], limit: int) -> None:
    """List recently stored CONFIRMED signals from local SQLite storage."""
    from app.config.settings import get_settings

    settings = get_settings()
    database_manager = DatabaseManager(settings.database_url)
    await database_manager.initialize()
    try:
        repository = SignalRepository(database_manager)
        signals = await repository.list_recent(limit=limit, symbol=symbol)
        for stored_signal in signals:
            print(f"Trade ID: {stored_signal.trade_id}")
            print(f"Coin: {stored_signal.coin}")
            print(f"Direction: {stored_signal.direction.value}")
            print(f"Entry: {stored_signal.entry_price}")
            print(f"Stop Loss: {stored_signal.stop_loss}")
            print(f"Take Profit: {stored_signal.take_profit}")
            print(f"RR: {stored_signal.risk_reward_ratio}")
            print(f"Status: {stored_signal.status.value}")
            print(f"Detection Time: {stored_signal.detection_time_utc.isoformat()}")
            print("-")
    finally:
        await database_manager.dispose()


async def _run_telegram_test() -> None:
    """Send one Telegram test message to every configured chat ID and print a concise per-chat result."""
    from app.config.settings import get_settings
    from app.notifications.results import NotificationStatus
    from app.notifications.telegram_client import TelegramBotClient, TelegramConfigurationError
    from app.notifications.telegram_formatter import TelegramSignalFormatter
    from app.notifications.telegram_notifier import TelegramSignalNotifier

    settings = get_settings()

    if not settings.telegram_enabled:
        print("status=SKIPPED")
        print("reason=Telegram is disabled (set TELEGRAM_ENABLED=true).")
        return

    try:
        telegram_client = TelegramBotClient(
            bot_token=settings.telegram_bot_token,
            chat_ids=settings.telegram_chat_ids,
            api_base_url=settings.telegram_api_base_url,
            request_timeout_seconds=settings.telegram_request_timeout_seconds,
            max_retries=settings.telegram_max_retries,
            retry_delay_seconds=settings.telegram_retry_delay_seconds,
            disable_web_page_preview=settings.telegram_disable_web_page_preview,
        )
    except TelegramConfigurationError as exc:
        print("status=SKIPPED")
        print(f"reason=Telegram configuration is incomplete: {exc}")
        return

    notifier = TelegramSignalNotifier(
        telegram_client=telegram_client,
        formatter=TelegramSignalFormatter(),
        enabled=True,
    )

    try:
        results = await notifier.send_test_message()
        for result in results:
            print(f"chat={result.chat_id_suffix}")
            print(f"status={result.status.value}")
            if result.status == NotificationStatus.SENT:
                print(f"message_id={result.telegram_message_id}")
            elif result.status == NotificationStatus.FAILED:
                print(f"reason={result.reason}")
            print("-")
    finally:
        await telegram_client.close()


def main() -> None:
    """Manual CLI entry point supporting analyze, scan-once, scan, signals, and telegram-test modes."""
    mode, remaining_argv = _parse_mode_and_args(sys.argv)

    if mode == "analyze":
        symbol, account_balance = _parse_args(remaining_argv)
        asyncio.run(_run_manual_analysis(symbol, account_balance))
    elif mode == "scan-once":
        _, account_balance = _parse_args(remaining_argv)
        asyncio.run(_run_scan_once(account_balance))
    elif mode == "scan":
        _, account_balance = _parse_args(remaining_argv)
        asyncio.run(_run_scan_forever(account_balance))
    elif mode == "signals":
        symbol, limit = _parse_signals_args(remaining_argv)
        asyncio.run(_run_signals(symbol, limit))
    elif mode == "telegram-test":
        asyncio.run(_run_telegram_test())


if __name__ == "__main__":
    main()
