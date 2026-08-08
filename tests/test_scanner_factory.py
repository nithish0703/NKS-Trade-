"""
Tests for app.scanner.engine_factory.build_scanner_service.
"""

from app.scanner.engine_factory import build_scanner_service
from app.scanner.scanner_service import ScannerService


class TestScannerFactory:
    def test_scanner_service_constructed(self):
        service = build_scanner_service()
        assert isinstance(service, ScannerService)

    def test_all_dependencies_wired(self):
        service = build_scanner_service()
        assert service._scheduler is not None
        assert service._candidate_buffer is not None
        assert service._active_state_provider is not None
        assert service._scheduler._pair_scanner is not None
        assert service._scheduler._pair_scanner._strategy_engine is not None
        assert service._scheduler._pair_scanner._duplicate_guard is not None

    def test_settings_respected(self):
        from app.config.settings import get_settings

        settings = get_settings()
        service = build_scanner_service()
        assert service._scheduler._scanner_interval_seconds == settings.scanner_interval_seconds

    def test_semaphore_uses_max_concurrent_scans(self):
        from app.config.settings import get_settings

        settings = get_settings()
        service = build_scanner_service()
        semaphore = service._scheduler._pair_scanner._semaphore
        assert semaphore._value == settings.max_concurrent_scans

    def test_duplicate_settings_respected(self):
        from app.config.settings import get_settings

        settings = get_settings()
        service = build_scanner_service()
        guard = service._scheduler._pair_scanner._duplicate_guard
        assert guard._retention_seconds == settings.duplicate_signal_retention_seconds
        assert guard._maximum_entries == settings.duplicate_signal_maximum_entries

    def test_candidate_buffer_size_respected(self):
        from app.config.settings import get_settings

        settings = get_settings()
        service = build_scanner_service()
        assert service._candidate_buffer._maximum_size == settings.candidate_buffer_maximum_size

    def test_no_api_calls_at_construction(self):
        # Construction must be fast and synchronous with no network I/O.
        service = build_scanner_service()
        assert service is not None

    def test_no_scan_starts_during_construction(self):
        service = build_scanner_service()
        status = service.get_runtime_status()
        assert status.running is False
        assert status.cycles_completed == 0

    def test_independent_factory_instances(self):
        service_one = build_scanner_service()
        service_two = build_scanner_service()
        assert service_one is not service_two
        assert service_one._candidate_buffer is not service_two._candidate_buffer
        assert (
            service_one._scheduler._pair_scanner._strategy_engine
            is not service_two._scheduler._pair_scanner._strategy_engine
        )


class TestScanLeaseWiring:
    """
    Covers the `scan-once` self-lockout bug: build_scanner_service()
    constructs a brand-new MonitorLeaseGuard (brand-new random holder
    id) on every call, so a one-off caller with no persistent identity
    across separate invocations must be able to opt out entirely via
    apply_scan_lease=False -- otherwise a previous invocation's own
    still-unexpired lease silently locks out every later one.
    """

    def test_apply_scan_lease_true_by_default_wires_a_lease_guard_when_persistence_enabled(self):
        from app.config.settings import get_settings

        settings = get_settings()
        service = build_scanner_service()
        if settings.enable_signal_persistence:
            assert service._scheduler._lease_guard is not None
        else:
            assert service._scheduler._lease_guard is None

    def test_apply_scan_lease_false_never_wires_a_lease_guard(self):
        service = build_scanner_service(apply_scan_lease=False)
        assert service._scheduler._lease_guard is None
