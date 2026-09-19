"""Unit tests for centralized logging configuration."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Generator

import pytest

from src.core.logging_config import (
    DEFAULT_LOG_FILE,
    InMemoryLogHandler,
    configure_logging,
    resolve_log_file,
    resolve_log_level,
    suppress_external_loggers,
)


@pytest.fixture(autouse=True)
def clean_env() -> Generator[None, None, None]:
    """Clean up logging environment variables before and after each test."""
    orig_env = {
        k: os.environ.get(k)
        for k in ["CTXINS_LOG_LEVEL", "CTXINS_LOG_FILE", "CTXINS_DEBUG"]
    }
    import src.core.logging_config as lc
    lc._CONFIGURED_MODE = None
    for k in orig_env:
        os.environ.pop(k, None)

    yield

    lc._CONFIGURED_MODE = None
    for k, v in orig_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_resolve_log_level_default() -> None:
    assert resolve_log_level() == logging.WARNING


def test_resolve_log_level_explicit() -> None:
    assert resolve_log_level("DEBUG") == logging.DEBUG
    assert resolve_log_level("info") == logging.INFO
    assert resolve_log_level("error") == logging.ERROR
    assert resolve_log_level(logging.INFO) == logging.INFO


def test_resolve_log_level_debug_flag() -> None:
    assert resolve_log_level(debug=True) == logging.DEBUG
    assert resolve_log_level(level="ERROR", debug=True) == logging.DEBUG


def test_resolve_log_level_env_vars() -> None:
    os.environ["CTXINS_LOG_LEVEL"] = "DEBUG"
    assert resolve_log_level() == logging.DEBUG

    os.environ.pop("CTXINS_LOG_LEVEL")
    os.environ["CTXINS_DEBUG"] = "1"
    assert resolve_log_level() == logging.DEBUG

    os.environ["CTXINS_DEBUG"] = "true"
    assert resolve_log_level() == logging.DEBUG


def test_resolve_log_file(tmp_path: Path) -> None:
    explicit = tmp_path / "custom.log"
    assert resolve_log_file(log_file=explicit) == explicit.resolve()

    os.environ["CTXINS_LOG_FILE"] = str(tmp_path / "env.log")
    assert resolve_log_file() == (tmp_path / "env.log").resolve()
    os.environ.pop("CTXINS_LOG_FILE")

    # In TUI or debug mode without explicit file, falls back to default ~/.ctxins/ctxins.log
    assert resolve_log_file(debug=True) == DEFAULT_LOG_FILE.resolve()
    assert resolve_log_file(mode="tui") == DEFAULT_LOG_FILE.resolve()
    assert resolve_log_file(mode="headless", debug=False) is None


def test_in_memory_log_handler() -> None:
    handler = InMemoryLogHandler(capacity=3)
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    test_logger = logging.getLogger("test.memory.handler")
    test_logger.setLevel(logging.DEBUG)
    test_logger.addHandler(handler)

    test_logger.info("msg 1")
    test_logger.info("msg 2")
    test_logger.info("msg 3")
    test_logger.info("msg 4")

    logs = handler.get_recent_logs()
    assert len(logs) == 3
    assert logs == ["msg 2", "msg 3", "msg 4"]

    assert handler.get_recent_logs(limit=2) == ["msg 3", "msg 4"]
    handler.clear()
    assert handler.get_recent_logs() == []
    test_logger.removeHandler(handler)


def test_configure_logging_tui_mode_safety(tmp_path: Path) -> None:
    log_file = tmp_path / "tui_test.log"
    root = configure_logging(
        level="DEBUG",
        log_file=log_file,
        mode="tui",
    )

    # In TUI mode, StreamHandler writing to stderr/stdout must NOT be added by ctxins
    stream_handlers = [
        h for h in root.handlers
        if getattr(h, "_ctxins_managed", False)
        and isinstance(h, logging.StreamHandler)
        and not isinstance(h, (RotatingFileHandler, logging.FileHandler, InMemoryLogHandler))
    ]
    assert len(stream_handlers) == 0

    # RotatingFileHandler should be installed
    file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
    assert len(file_handlers) >= 1
    assert str(log_file) in file_handlers[0].baseFilename


def test_configure_logging_file_output(tmp_path: Path) -> None:
    log_file = tmp_path / "output.log"
    configure_logging(level="INFO", log_file=log_file, mode="headless")

    test_logger = logging.getLogger("ctxins.test")
    test_logger.info("Test log line written to file")

    # Flush handlers
    for h in logging.getLogger().handlers:
        h.flush()

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "Test log line written to file" in content
    assert "[INFO    ]" in content
    assert "ctxins.test" in content


def test_suppress_external_loggers() -> None:
    suppress_external_loggers(min_level=logging.WARNING)
    assert logging.getLogger("uvicorn").level >= logging.WARNING
    assert logging.getLogger("mitmproxy").level >= logging.WARNING
    assert logging.getLogger("httpx").level >= logging.WARNING


def test_headless_mode_inherits_env_log_file(tmp_path: Path) -> None:
    target_log = tmp_path / "inherited.log"
    os.environ["CTXINS_LOG_FILE"] = str(target_log)
    os.environ["CTXINS_LOG_LEVEL"] = "INFO"

    resolved = resolve_log_file(mode="headless", debug=False)
    assert resolved == target_log.resolve()

    configure_logging(mode="headless", enable_memory_buffer=False)
    test_logger = logging.getLogger("ctxins.headless")
    test_logger.info("Headless log entry via env var")

    for h in logging.getLogger().handlers:
        h.flush()

    assert target_log.exists()
    content = target_log.read_text(encoding="utf-8")
    assert "Headless log entry via env var" in content
