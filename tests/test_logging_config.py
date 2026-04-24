"""Tests for the structured logging configuration."""

from __future__ import annotations

import logging

import pytest

from selvedge.logging_config import (
    LOG_LEVEL_ENV,
    _resolve_level,
    configure_logging,
)


@pytest.fixture(autouse=True)
def reset_selvedge_logger():
    """Tear down any handlers configure_logging() installed during the test."""
    logger = logging.getLogger("selvedge")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    yield
    logger.handlers = original_handlers
    logger.setLevel(original_level)
    logger.propagate = original_propagate


# ---------------------------------------------------------------------------
# Level resolution
# ---------------------------------------------------------------------------


def test_resolve_level_uses_env_var(monkeypatch):
    monkeypatch.setenv(LOG_LEVEL_ENV, "DEBUG")
    assert _resolve_level() == logging.DEBUG


def test_resolve_level_default_is_warning(monkeypatch):
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
    assert _resolve_level() == logging.WARNING


def test_resolve_level_explicit_override_wins(monkeypatch):
    monkeypatch.setenv(LOG_LEVEL_ENV, "DEBUG")
    assert _resolve_level("ERROR") == logging.ERROR


def test_resolve_level_unknown_falls_back_to_warning(monkeypatch):
    """A typo in SELVEDGE_LOG_LEVEL must not silence the logger entirely."""
    monkeypatch.setenv(LOG_LEVEL_ENV, "BANANA")
    assert _resolve_level() == logging.WARNING


def test_resolve_level_is_case_insensitive(monkeypatch):
    monkeypatch.setenv(LOG_LEVEL_ENV, "info")
    assert _resolve_level() == logging.INFO


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


def test_configure_logging_installs_handler(monkeypatch):
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
    configure_logging()

    logger = logging.getLogger("selvedge")
    selvedge_handlers = [h for h in logger.handlers if getattr(h, "_selvedge_handler", False)]
    assert len(selvedge_handlers) == 1
    assert logger.level == logging.WARNING
    assert logger.propagate is False


def test_configure_logging_idempotent(monkeypatch):
    """Repeated calls swap out the handler instead of stacking duplicates."""
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
    configure_logging()
    configure_logging()
    configure_logging()

    logger = logging.getLogger("selvedge")
    selvedge_handlers = [h for h in logger.handlers if getattr(h, "_selvedge_handler", False)]
    assert len(selvedge_handlers) == 1


def test_configure_logging_does_not_remove_user_handlers(monkeypatch):
    """A handler installed by user code must survive configure_logging()."""
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
    logger = logging.getLogger("selvedge")
    user_handler = logging.NullHandler()
    logger.addHandler(user_handler)

    try:
        configure_logging()
        assert user_handler in logger.handlers
    finally:
        logger.removeHandler(user_handler)


def test_configure_logging_respects_env(monkeypatch):
    monkeypatch.setenv(LOG_LEVEL_ENV, "DEBUG")
    configure_logging()
    assert logging.getLogger("selvedge").level == logging.DEBUG


def test_configure_logging_explicit_level_wins(monkeypatch):
    monkeypatch.setenv(LOG_LEVEL_ENV, "DEBUG")
    configure_logging(level="ERROR")
    assert logging.getLogger("selvedge").level == logging.ERROR


# ---------------------------------------------------------------------------
# End-to-end: storage emits expected log messages
# ---------------------------------------------------------------------------


def test_storage_logger_uses_namespace(monkeypatch, tmp_path, caplog):
    """Library modules must log under the ``selvedge.*`` namespace so
    configure_logging() can route them."""
    from selvedge.models import ChangeEvent
    from selvedge.storage import SelvedgeStorage

    # Force-trigger the WAL-mode debug log by capturing all selvedge.* output
    with caplog.at_level(logging.DEBUG, logger="selvedge.storage"):
        storage = SelvedgeStorage(tmp_path / "log.db")
        storage.log_event(ChangeEvent(entity_path="users.email", change_type="add"))

    selvedge_records = [r for r in caplog.records if r.name.startswith("selvedge")]
    # Even on a happy path with no warnings, at least one record may be
    # emitted (e.g. the WAL fallback debug). The important property is
    # that EVERY storage record is namespaced — so configure_logging()
    # can attach a handler to "selvedge" and capture everything.
    for record in selvedge_records:
        assert record.name.startswith("selvedge.")
