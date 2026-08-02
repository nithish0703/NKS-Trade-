"""
Application-wide logging configuration and helpers.
"""

import logging


def configure_logging(level: str = "INFO") -> None:
    """
    Configure the root logger with a single stream handler and a
    consistent format, so every module's `logging.getLogger(__name__)`
    call (scan-cycle summaries, retry warnings, pair-discovery refreshes,
    etc.) actually produces visible output.

    Idempotent: safe to call more than once (e.g. across repeated
    ASGI app construction in tests) without installing duplicate
    handlers on the root logger.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level.upper())

    if any(isinstance(handler, logging.StreamHandler) for handler in root_logger.handlers):
        return

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root_logger.addHandler(handler)
