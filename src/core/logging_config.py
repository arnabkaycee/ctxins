"""Centralized, adaptive logging configuration for ctxins."""

from __future__ import annotations

import collections
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Deque, List, Optional, Union

# Defaults
DEFAULT_LOG_LEVEL = "WARNING"
DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)-8s] [%(name)s:%(lineno)d] %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LOG_DIR = Path("~/.ctxins").expanduser()
DEFAULT_LOG_FILE = DEFAULT_LOG_DIR / "ctxins.log"
MAX_LOG_BYTES = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 3

NOISY_EXTERNAL_LOGGERS = [
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
    "mitmproxy",
    "urllib3",
    "asyncio",
    "websockets",
    "hpack",
    "httpcore",
    "httpx",
]


class InMemoryLogHandler(logging.Handler):
    """Thread-safe in-memory ring buffer holding recent formatted log records."""

    def __init__(self, capacity: int = 1000) -> None:
        super().__init__()
        self.capacity = capacity
        self.buffer: Deque[str] = collections.deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.buffer.append(msg)
        except Exception:
            self.handleError(record)

    def get_recent_logs(self, limit: int = 100) -> List[str]:
        """Return the most recent log entries up to limit."""
        items = list(self.buffer)
        if limit <= 0 or limit >= len(items):
            return items
        return items[-limit:]

    def clear(self) -> None:
        """Clear the in-memory buffer."""
        self.buffer.clear()


# Global in-memory handler singleton
_MEMORY_HANDLER: Optional[InMemoryLogHandler] = None
_CONFIGURED_MODE: Optional[str] = None


def get_memory_handler(capacity: int = 1000) -> InMemoryLogHandler:
    """Get or create the global in-memory log handler."""
    global _MEMORY_HANDLER
    if _MEMORY_HANDLER is None:
        _MEMORY_HANDLER = InMemoryLogHandler(capacity=capacity)
        formatter = logging.Formatter(fmt=DEFAULT_LOG_FORMAT, datefmt=DEFAULT_DATE_FORMAT)
        _MEMORY_HANDLER.setFormatter(formatter)
    return _MEMORY_HANDLER


def resolve_log_level(
    level: Optional[Union[str, int]] = None,
    debug: bool = False,
) -> int:
    """Resolve effective logging level from arguments and environment variables.

    Precedence:
    1. debug=True flag -> logging.DEBUG
    2. Explicit level argument (str or int)
    3. CTXINS_DEBUG environment variable ("1", "true", "yes") -> logging.DEBUG
    4. CTXINS_LOG_LEVEL environment variable
    5. Default: logging.WARNING
    """
    if debug:
        return logging.DEBUG

    if level is not None:
        if isinstance(level, int):
            return level
        lvl_str = str(level).strip().upper()
        if hasattr(logging, lvl_str):
            return int(getattr(logging, lvl_str))

    env_debug = os.environ.get("CTXINS_DEBUG", "").strip().lower()
    if env_debug in ("1", "true", "yes", "on"):
        return logging.DEBUG

    env_level = os.environ.get("CTXINS_LOG_LEVEL", "").strip().upper()
    if env_level and hasattr(logging, env_level):
        return int(getattr(logging, env_level))

    return getattr(logging, DEFAULT_LOG_LEVEL)


def resolve_log_file(
    log_file: Optional[Union[str, Path]] = None,
    debug: bool = False,
    mode: str = "tui",
) -> Optional[Path]:
    """Resolve log file destination path.

    Precedence:
    1. Explicit log_file argument
    2. CTXINS_LOG_FILE environment variable
    3. If debug=True or mode in ("tui", "run") when debug level is active: default ~/.ctxins/ctxins.log
    4. None (no file output)
    """
    if log_file is not None and str(log_file).strip():
        return Path(log_file).expanduser().resolve()

    env_file = os.environ.get("CTXINS_LOG_FILE", "").strip()
    if env_file:
        return Path(env_file).expanduser().resolve()

    if debug or mode in ("tui", "run"):
        return DEFAULT_LOG_FILE.resolve()

    return None


def suppress_external_loggers(min_level: int = logging.WARNING) -> None:
    """Suppress excessively chatty third-party loggers."""
    for logger_name in NOISY_EXTERNAL_LOGGERS:
        ext_logger = logging.getLogger(logger_name)
        ext_logger.setLevel(min_level)


def configure_logging(
    level: Optional[Union[str, int]] = None,
    debug: bool = False,
    log_file: Optional[Union[str, Path]] = None,
    stream: Optional[bool] = None,
    mode: str = "tui",
    enable_memory_buffer: bool = True,
    force: bool = False,
) -> logging.Logger:
    """Configure unified logging for ctxins.

    Args:
        level: Explicit log level name or int.
        debug: If True, forces DEBUG level and enables file logging if not specified.
        log_file: Optional destination log file path.
        stream: Whether to log to stderr. In 'tui', 'run', or 'env' mode, defaults to False
                to prevent screen corruption. In 'web' or 'headless' mode, defaults to True
                unless a log_file is explicitly provided.
        mode: Operational mode: 'tui', 'run', 'web', 'headless', 'env'.
        enable_memory_buffer: If True, attaches the global in-memory ring buffer handler.
        force: If True, forces reconfiguration even if an active UI mode is already set.

    Returns:
        The configured root logger.
    """
    global _CONFIGURED_MODE
    # Prevent headless mode imports from clobbering active interactive modes
    if not force and _CONFIGURED_MODE in ("tui", "run", "web", "env") and mode == "headless":
        return logging.getLogger()
    _CONFIGURED_MODE = mode
    effective_level = resolve_log_level(level=level, debug=debug)
    is_debug_active = effective_level <= logging.DEBUG

    # Determine default stream behavior based on mode
    if stream is None:
        if mode in ("tui", "run", "env"):
            stream = False
        else:
            stream = True

    effective_log_file = resolve_log_file(
        log_file=log_file,
        debug=is_debug_active,
        mode=mode,
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(effective_level)

    # Remove existing Ctxins-managed handlers to guarantee idempotency
    handlers_to_keep: List[logging.Handler] = []
    for h in root_logger.handlers:
        if getattr(h, "_ctxins_managed", False):
            try:
                h.close()
            except Exception:
                pass
        else:
            handlers_to_keep.append(h)
    root_logger.handlers = handlers_to_keep

    formatter = logging.Formatter(fmt=DEFAULT_LOG_FORMAT, datefmt=DEFAULT_DATE_FORMAT)

    # 1. Rotating File Handler
    if effective_log_file is not None:
        try:
            effective_log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                filename=str(effective_log_file),
                maxBytes=MAX_LOG_BYTES,
                backupCount=BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setLevel(effective_level)
            file_handler.setFormatter(formatter)
            setattr(file_handler, "_ctxins_managed", True)
            root_logger.addHandler(file_handler)
        except Exception as e:
            # If unable to open log file, fallback to stderr only if not in TUI mode
            if stream or mode not in ("tui", "run"):
                sys.stderr.write(f"Warning: Failed to initialize log file at {effective_log_file}: {e}\n")

    # 2. Console/Stream Handler (stderr only, never stdout)
    if stream and mode not in ("tui", "run"):
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(effective_level)
        console_handler.setFormatter(formatter)
        setattr(console_handler, "_ctxins_managed", True)
        root_logger.addHandler(console_handler)

    # 3. In-memory buffer
    if enable_memory_buffer:
        mem_handler = get_memory_handler()
        mem_handler.setLevel(effective_level)
        mem_handler.setFormatter(formatter)
        setattr(mem_handler, "_ctxins_managed", True)
        if mem_handler not in root_logger.handlers:
            root_logger.addHandler(mem_handler)

    # Suppress chatty external loggers unless effective level is lower than DEBUG
    suppress_external_loggers(min_level=logging.WARNING)

    return root_logger
