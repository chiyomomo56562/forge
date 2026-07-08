"""Logging utilities for the Forge agent framework.

Initialises Python ``logging`` from ``config/logging.yml`` and provides
a convenience :func:`get_logger` factory used across all modules.
"""

from __future__ import annotations

import logging
import logging.config
from pathlib import Path
from typing import Any

import yaml

from .serialization import read_yaml

_INITIALISED = False


def setup_logging(
    config_path: str | Path = "config/logging.yml",
    *,
    force: bool = False,
) -> None:
    """Initialise logging from a YAML config file.

    This is idempotent — calling it multiple times has no effect unless
    *force* is ``True``.

    Args:
        config_path: Path to the logging YAML file.
        force: If ``True``, re-initialise even if already set up.
    """
    global _INITIALISED
    if _INITIALISED and not force:
        return

    config_path = Path(config_path)
    if not config_path.exists():
        # Fallback: basic console logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        _INITIALISED = True
        return

    config: dict[str, Any] = read_yaml(config_path)

    # Ensure log file directories exist
    handlers = config.get("handlers", {})
    for handler in handlers.values():
        filename = handler.get("filename")
        if filename:
            Path(filename).parent.mkdir(parents=True, exist_ok=True)

    logging.config.dictConfig(config)
    _INITIALISED = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger with *name*, ensuring logging is initialised.

    If :func:`setup_logging` has not been called yet, it will be called
    automatically with the default config path.
    """
    if not _INITIALISED:
        setup_logging()
    return logging.getLogger(name)


def reset_logging() -> None:
    """Reset the initialisation flag (mainly for testing)."""
    global _INITIALISED
    _INITIALISED = False