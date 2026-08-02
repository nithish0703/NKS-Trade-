"""
Tests for app.utils.logger.configure_logging.
"""

import logging

from app.utils.logger import configure_logging


def _clear_root_handlers():
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)


class TestConfigureLogging:
    def test_installs_a_stream_handler_on_the_root_logger(self):
        _clear_root_handlers()
        configure_logging("INFO")
        root_logger = logging.getLogger()
        assert any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers)
        _clear_root_handlers()

    def test_sets_the_root_logger_level(self):
        _clear_root_handlers()
        configure_logging("DEBUG")
        assert logging.getLogger().level == logging.DEBUG
        _clear_root_handlers()

    def test_lowercase_level_is_accepted(self):
        _clear_root_handlers()
        configure_logging("warning")
        assert logging.getLogger().level == logging.WARNING
        _clear_root_handlers()

    def test_idempotent_does_not_install_duplicate_handlers(self):
        _clear_root_handlers()
        configure_logging("INFO")
        handler_count_after_first_call = len(logging.getLogger().handlers)
        configure_logging("INFO")
        handler_count_after_second_call = len(logging.getLogger().handlers)
        assert handler_count_after_first_call == handler_count_after_second_call
        _clear_root_handlers()

    def test_a_module_logger_actually_emits_at_the_configured_level(self, capsys):
        _clear_root_handlers()
        configure_logging("INFO")
        module_logger = logging.getLogger("some.module.under.test")
        module_logger.info("hello from a module logger")
        captured = capsys.readouterr()
        assert "hello from a module logger" in captured.err
        _clear_root_handlers()

    def test_default_level_is_info(self):
        _clear_root_handlers()
        configure_logging()
        assert logging.getLogger().level == logging.INFO
        _clear_root_handlers()
