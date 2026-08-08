"""
Entry point for the Institutional Smart Money Concepts trading engine.

This module provides manual, development-facing CLI modes:

    python main.py analyze BTC-USDT                   # one-symbol pipeline analysis
    python main.py scan-once                          # one full multi-pair scan cycle
    python main.py scan                                # continuous scanner (see SCANNER_INTERVAL_SECONDS)
    python main.py scan --force-release-lease          # also clear stuck scan/monitor leases before starting
    python main.py signals --symbol BTC-USDT           # list locally stored signals
    python main.py telegram-test                       # send one Telegram test message to every configured chat
    python main.py funnel [--days N]                   # rejection-funnel report (default: last 7 days)
    python main.py performance [--days N]              # win-rate/expectancy report (default: last 7 days)
    python main.py baseline --save <name> [--days N]   # save a timestamped baseline snapshot to data/baselines/

None of these modes implement a dashboard or WebSocket broadcasting.
`scan-once` and `scan` persist CONFIRMED signals to local SQLite
storage only when persistence is enabled, and send a Telegram
notification for each newly stored, non-duplicate signal to every chat
ID configured in TELEGRAM_CHAT_IDS, only when Telegram is enabled in
settings. `scan` additionally runs a standalone SignalOutcomeMonitor
(see app.monitoring.signal_outcome_monitor) whenever persistence and
SIGNAL_OUTCOME_MONITOR_ENABLED are both on, so a `python main.py scan`
run tracks WIN/LOSS/TIMEOUT outcomes for every CONFIRMED signal on its
own -- no dashboard API process is required for outcome tracking to
work. If the dashboard API is also run against the same database, a
DB-backed lease (app.storage.monitor_lease) ensures only one of the two
processes' monitors -- and, separately, only one of the two processes'
scan/candle-fetch loops -- does that work in any given cycle. Both
leases use a stable per-mode holder_id ("scan-cli"/"scan-cli-monitor"
here, "dashboard-api"/"dashboard-api-monitor" for the dashboard API),
so restarting `scan` after Ctrl+C or a crash reclaims its own lease
immediately rather than waiting out the full lease duration;
`--force-release-lease` is only needed to hand a lease to a genuinely
different holder on demand, or to clear one left by an older version
of this app. `scan-once` never touches the scan-cycle lease at all
(it has no persistent identity across separate invocations, so it
would otherwise be locked out by its own previous run).
`funnel`, `performance`, and `baseline` are read-only analysis modes:
they never modify strategy behaviour and never write anything except
(for `baseline`) the snapshot JSON file itself.
"""

import asyncio
import signal
import sys
from typing import Optional

from app.config.pairs import BTC_SYMBOL
from app.config.thresholds import SIGNAL_OUTCOME_MONITOR_LEASE_DURATION_MULTIPLIER
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
from app.storage.monitor_lease import MonitorLeaseGuard
from app.storage.signal_repository import SignalRepository

_SIGNAL_OUTCOME_MONITOR_LEASE_NAME = "signal_outcome_monitor"

_DEFAULT_DEVELOPMENT_BALANCE = 10_000.0
_DEFAULT_MODE = "analyze"
_VALID_MODES = (
    "analyze",
    "scan-once",
    "scan",
    "signals",
    "telegram-test",
    "funnel",
    "performance",
    "baseline",
)
_DEFAULT_REPORT_WINDOW_DAYS = 7


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


_FORCE_RELEASE_LEASE_FLAG = "--force-release-lease"


def _parse_scan_args(argv: list[str]) -> tuple[float, bool]:
    """
    Parse an optional account balance and the `--force-release-lease`
    flag for the `scan` CLI mode. The flag is stripped out before the
    positional balance argument is parsed, so its position relative to
    a balance argument doesn't matter.
    """
    force_release_lease = _FORCE_RELEASE_LEASE_FLAG in argv
    filtered = [arg for arg in argv if arg != _FORCE_RELEASE_LEASE_FLAG]
    _, account_balance = _parse_args(filtered)
    return account_balance, force_release_lease


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
    if cycle_result.metadata and cycle_result.metadata.get("lease_skipped"):
        print(
            "WARNING: cycle SKIPPED -- scan lease held by another process; 0 pairs scanned. "
            "See app.storage.monitor_lease / logs for detail."
        )
    elif cycle_result.total_pairs == 0:
        print(
            "WARNING: 0 configured pairs this cycle -- nothing was scanned. Check "
            "DYNAMIC_PAIR_DISCOVERY_ENABLED and the pair-discovery service's last result."
        )
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
        print()

    for pair_result in cycle_result.error_results:
        print(
            f"  error: symbol={pair_result.symbol}"
            f" error_type={pair_result.error_type}"
            f" reason={pair_result.reason}"
        )


async def _run_scan_once(account_balance: float) -> None:
    """
    Build the scanner service and run exactly one multi-pair scan cycle.

    Skips the scan-cycle lease (apply_scan_lease=False): this is a
    one-off manual invocation with no persistent identity across
    separate runs, so it must never be locked out by a previous
    `scan-once` run's own still-unexpired lease -- unlike `scan` and
    the dashboard API, which run as one long-lived process each and
    genuinely need the lease to avoid duplicating candle fetches.
    """
    service = build_scanner_service(apply_scan_lease=False)
    await initialize_scanner_storage(service)
    try:
        cycle_result = await service.run_single_cycle(account_balance)
        _print_cycle_summary(cycle_result)
    finally:
        await _dispose_scanner_storage(service)


def _build_signal_outcome_monitor(service):
    """
    Build a SignalOutcomeMonitor standalone for `python main.py scan`,
    when signal persistence and the monitor are both enabled. Returns
    None otherwise (nothing to track, or the operator opted out).

    Shares the same DB-backed lease name/duration as the dashboard
    API's monitor (see app.api.main.lifespan) so at most one of the two
    processes does outcome-tracking work per cycle if both are ever run
    against the same database at once.
    """
    signal_storage_service = service.signal_storage_service
    if signal_storage_service is None:
        return None

    from app.config.settings import get_settings
    from app.data.binance_market_data_provider import BinanceFuturesMarketDataProvider
    from app.monitoring.signal_outcome_monitor import SignalOutcomeMonitor

    settings = get_settings()
    if not settings.signal_outcome_monitor_enabled:
        return None

    market_data_provider = BinanceFuturesMarketDataProvider(
        base_url=settings.exchange_base_url,
        request_timeout_seconds=settings.request_timeout_seconds,
    )
    lease_guard = MonitorLeaseGuard(
        signal_storage_service.database_manager,
        lease_name=_SIGNAL_OUTCOME_MONITOR_LEASE_NAME,
        lease_duration_seconds=(
            settings.signal_outcome_monitor_interval_seconds
            * SIGNAL_OUTCOME_MONITOR_LEASE_DURATION_MULTIPLIER
        ),
        holder_id="scan-cli-monitor",
    )
    return SignalOutcomeMonitor(
        signal_repository=signal_storage_service.signal_repository,
        market_data_provider=market_data_provider,
        interval_seconds=settings.signal_outcome_monitor_interval_seconds,
        lease_guard=lease_guard,
    )


async def _force_release_scan_leases() -> None:
    """
    Release the `scanner_cycle` and `signal_outcome_monitor` lease rows,
    if present, regardless of current holder -- the
    `--force-release-lease` escape hatch. The leases live in the same
    SQLite database signal storage uses; a no-op with a printed note
    when persistence is disabled, since there is then nothing to
    release.
    """
    from app.config.settings import get_settings
    from app.storage.monitor_lease import release_lease

    settings = get_settings()
    if not settings.enable_signal_persistence:
        print("force_release_lease: signal persistence is disabled; no lease to release.")
        return

    database_manager = DatabaseManager(settings.database_url)
    await database_manager.initialize()
    try:
        scan_released = await release_lease(database_manager, lease_name="scanner_cycle")
        monitor_released = await release_lease(
            database_manager, lease_name=_SIGNAL_OUTCOME_MONITOR_LEASE_NAME
        )
        print(
            f"force_release_lease: scanner_cycle_released={scan_released} "
            f"signal_outcome_monitor_released={monitor_released}"
        )
    finally:
        await database_manager.dispose()


async def _run_scan_forever(account_balance: float, *, force_release_lease: bool = False) -> None:
    """
    Start the continuous multi-pair scanner (see SCANNER_INTERVAL_SECONDS,
    currently 5 minutes) until Ctrl+C.

    Both the scan-cycle lease and the signal-outcome-monitor lease now
    use a stable holder_id ("scan-cli" / "scan-cli-monitor"), so a
    restart of this same command after Ctrl+C or a crash reclaims its
    own lease immediately rather than waiting out the full lease
    duration -- `force_release_lease` (from `--force-release-lease`) is
    only needed to manually hand the lease to a *different* holder
    (e.g. if the dashboard API should take over) or to clear a lease
    left by a version of this app that predates the stable holder_id
    fix.
    """
    if force_release_lease:
        await _force_release_scan_leases()

    service = build_scanner_service()
    await initialize_scanner_storage(service)
    loop = asyncio.get_running_loop()

    signal_outcome_monitor = _build_signal_outcome_monitor(service)
    signal_outcome_monitor_task = None
    if signal_outcome_monitor is not None:
        signal_outcome_monitor_task = asyncio.create_task(signal_outcome_monitor.run_forever())

    def _handle_shutdown_signal() -> None:
        print("Shutdown requested; finishing current scan cycle...")
        service.request_shutdown()
        if signal_outcome_monitor is not None:
            signal_outcome_monitor.request_shutdown()

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
        if signal_outcome_monitor_task is not None:
            signal_outcome_monitor.request_shutdown()
            await asyncio.wait_for(signal_outcome_monitor_task, timeout=30.0)
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


def _parse_window_days_arg(argv: list[str], default: int = _DEFAULT_REPORT_WINDOW_DAYS) -> int:
    """Parse an optional --days N flag, defaulting to `default` on absence or a bad value."""
    days = default
    index = 1
    while index < len(argv):
        if argv[index] == "--days" and index + 1 < len(argv):
            try:
                days = int(argv[index + 1])
            except ValueError:
                days = default
            index += 2
        else:
            index += 1
    return days


async def _run_funnel(window_days: int) -> None:
    """Print the rejection-funnel report for the last `window_days` days."""
    from app.analytics.funnel_report import format_funnel_report, generate_funnel_report
    from app.config.settings import get_settings
    from app.storage.analytics_repository import AnalyticsRepository

    settings = get_settings()
    database_manager = DatabaseManager(settings.database_url)
    await database_manager.initialize()
    try:
        analytics_repository = AnalyticsRepository(database_manager, enabled=settings.enable_rejection_analytics)
        report = await generate_funnel_report(analytics_repository, window_days=window_days)
        print(format_funnel_report(report))
    finally:
        await database_manager.dispose()


async def _run_performance(window_days: int) -> None:
    """Print the performance/expectancy report for the last `window_days` days."""
    from app.analytics.performance_report import format_performance_report, generate_performance_report
    from app.config.settings import get_settings

    settings = get_settings()
    database_manager = DatabaseManager(settings.database_url)
    await database_manager.initialize()
    try:
        signal_repository = SignalRepository(database_manager)
        report = await generate_performance_report(signal_repository, window_days=window_days)
        print(format_performance_report(report))
    finally:
        await database_manager.dispose()


def _parse_baseline_args(argv: list[str]) -> tuple[Optional[str], int]:
    """Parse --save <name> and an optional --days N flag for the baseline CLI mode."""
    name: Optional[str] = None
    days = _DEFAULT_REPORT_WINDOW_DAYS
    index = 1
    while index < len(argv):
        if argv[index] == "--save" and index + 1 < len(argv):
            name = argv[index + 1]
            index += 2
        elif argv[index] == "--days" and index + 1 < len(argv):
            try:
                days = int(argv[index + 1])
            except ValueError:
                pass
            index += 2
        else:
            index += 1
    return name, days


async def _run_baseline(name: Optional[str], window_days: int) -> None:
    """Save a timestamped baseline snapshot (funnel + performance reports) to data/baselines/."""
    from app.analytics.baseline import save_baseline
    from app.config.settings import get_settings
    from app.storage.analytics_repository import AnalyticsRepository

    if not name:
        print("status=ERROR")
        print("reason=--save <name> is required, e.g. `python main.py baseline --save pre_changes`.")
        return

    settings = get_settings()
    database_manager = DatabaseManager(settings.database_url)
    await database_manager.initialize()
    try:
        analytics_repository = AnalyticsRepository(database_manager, enabled=settings.enable_rejection_analytics)
        signal_repository = SignalRepository(database_manager)
        file_path = await save_baseline(
            name=name,
            analytics_repository=analytics_repository,
            signal_repository=signal_repository,
            window_days=window_days,
        )
        print(f"baseline_saved={file_path}")
    finally:
        await database_manager.dispose()


def main() -> None:
    """Manual CLI entry point supporting analyze, scan-once, scan, signals, telegram-test, funnel, performance, and baseline modes."""
    mode, remaining_argv = _parse_mode_and_args(sys.argv)

    if mode == "analyze":
        symbol, account_balance = _parse_args(remaining_argv)
        asyncio.run(_run_manual_analysis(symbol, account_balance))
    elif mode == "scan-once":
        _, account_balance = _parse_args(remaining_argv)
        asyncio.run(_run_scan_once(account_balance))
    elif mode == "scan":
        account_balance, force_release_lease = _parse_scan_args(remaining_argv)
        asyncio.run(_run_scan_forever(account_balance, force_release_lease=force_release_lease))
    elif mode == "signals":
        symbol, limit = _parse_signals_args(remaining_argv)
        asyncio.run(_run_signals(symbol, limit))
    elif mode == "telegram-test":
        asyncio.run(_run_telegram_test())
    elif mode == "funnel":
        window_days = _parse_window_days_arg(remaining_argv)
        asyncio.run(_run_funnel(window_days))
    elif mode == "performance":
        window_days = _parse_window_days_arg(remaining_argv)
        asyncio.run(_run_performance(window_days))
    elif mode == "baseline":
        name, window_days = _parse_baseline_args(remaining_argv)
        asyncio.run(_run_baseline(name, window_days))


if __name__ == "__main__":
    main()
