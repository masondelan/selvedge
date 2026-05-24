"""
Tests for :mod:`selvedge.update_check` — the background PyPI version check
shipped in v0.3.6.

The full contract under test:

* The check is gated by env vars (``SELVEDGE_NO_UPDATE_CHECK``,
  ``SELVEDGE_QUIET``, ``CI``), by TTY status, by dev-install detection,
  and by a 24h cache TTL.
* The HTTP fetch is soft-failing — any network error returns ``None`` and
  the cache is left untouched.
* Version comparison handles bare ``X.Y.Z`` cleanly with or without
  ``packaging`` installed.
* The notice is rendered once per process, on stderr, only when the
  cached latest is strictly greater than ``__version__``.

Crucially: **no test in this module hits the network.** ``urlopen`` is
monkeypatched everywhere it could be reached. Tests that fail to do
that would slow the suite and flake on network outages.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest
from rich.console import Console

from selvedge import update_check

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Each test starts with the one-shot guards cleared."""
    update_check._reset_for_testing()
    yield
    update_check._reset_for_testing()


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """
    Redirect ``Path.home()`` so the cache writes go to ``tmp_path``.

    The module reads ``Path.home()`` lazily on every call, so patching it
    here is enough — no need to also patch ``HOME`` in the environment.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def force_tty(monkeypatch):
    """
    Pretend stderr is a TTY so the gating doesn't bail out immediately.

    We patch the module-level ``_stderr_is_tty`` helper rather than
    ``sys.stderr`` directly — pytest's capture machinery rewraps
    ``sys.stderr`` after fixtures run, so attribute patching doesn't
    stick. The helper is just ``sys.stderr.isatty()`` in production.
    """
    monkeypatch.setattr(update_check, "_stderr_is_tty", lambda: True)


@pytest.fixture
def clear_suppression_env(monkeypatch):
    """Make sure no inherited env var poisons the gating."""
    for var in update_check.SUPPRESSION_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------------- #
# Gating — should_check()
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("var", list(update_check.SUPPRESSION_ENV_VARS))
def test_should_check_blocked_by_each_suppression_env(
    var, monkeypatch, fake_home, force_tty, clear_suppression_env
):
    """Any of the three suppression env vars individually disables the check."""
    monkeypatch.setenv(var, "1")
    assert update_check.should_check() is False


def test_should_check_blocked_when_stderr_not_tty(monkeypatch, fake_home, clear_suppression_env):
    """Piping output / running in agent stdio disables the check."""
    monkeypatch.setattr(update_check, "_stderr_is_tty", lambda: False)
    assert update_check.should_check() is False


def test_should_check_blocked_on_dev_install(monkeypatch, fake_home, force_tty, clear_suppression_env):
    """``pip install -e .`` users see a version like 0.3.6.dev0+g..."""
    monkeypatch.setattr(update_check, "__version__", "0.3.6.dev0")
    assert update_check.should_check() is False


def test_should_check_allows_fresh_run(fake_home, force_tty, clear_suppression_env):
    """No cache, no suppression, TTY present → check runs."""
    assert update_check.should_check() is True


def test_should_check_blocked_within_ttl(fake_home, force_tty, clear_suppression_env):
    """A recent cache write blocks the next check."""
    now = 1_000_000.0
    update_check._write_cache("0.99.0", now=now)
    assert update_check.should_check(now=now + 60) is False


def test_should_check_allows_after_ttl(fake_home, force_tty, clear_suppression_env):
    """A stale cache (>24h) re-opens the gate."""
    now = 1_000_000.0
    update_check._write_cache("0.99.0", now=now)
    later = now + update_check.CHECK_INTERVAL_SECONDS + 1
    assert update_check.should_check(now=later) is True


def test_should_check_ignores_malformed_cache(fake_home, force_tty, clear_suppression_env):
    """A corrupt cache file doesn't block; we just re-check."""
    path = update_check._cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    assert update_check.should_check() is True


# --------------------------------------------------------------------------- #
# Fetch — _fetch_latest()
# --------------------------------------------------------------------------- #


def _fake_urlopen_response(payload: dict):
    """Build a context-manager response that json.load can consume."""

    class _Resp:
        def __init__(self, body: bytes):
            self._buf = io.BytesIO(body)

        def __enter__(self):
            return self._buf

        def __exit__(self, *exc):
            return False

        def read(self, *args, **kwargs):
            return self._buf.read(*args, **kwargs)

    return _Resp(json.dumps(payload).encode("utf-8"))


def test_fetch_latest_happy_path(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _fake_urlopen_response({"info": {"version": "1.2.3"}}),
    )
    assert update_check._fetch_latest() == "1.2.3"


def test_fetch_latest_returns_none_on_network_error(monkeypatch):
    def _raise(req, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    assert update_check._fetch_latest() is None


def test_fetch_latest_returns_none_on_timeout(monkeypatch):
    def _raise(req, timeout=None):
        raise TimeoutError("slow PyPI")

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    assert update_check._fetch_latest() is None


def test_fetch_latest_returns_none_on_malformed_payload(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _fake_urlopen_response({"unexpected": "shape"}),
    )
    assert update_check._fetch_latest() is None


def test_fetch_latest_returns_none_on_unexpected_exception(monkeypatch):
    """Belt-and-suspenders: even an unknown exception type must be swallowed."""

    def _raise(req, timeout=None):
        raise RuntimeError("urlopen blew up in a weird way")

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    assert update_check._fetch_latest() is None


# --------------------------------------------------------------------------- #
# Version comparison — _is_newer()
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "latest,current,expected",
    [
        ("0.3.6", "0.3.5", True),
        ("0.3.5", "0.3.5", False),
        ("0.3.5", "0.3.6", False),
        ("1.0.0", "0.99.0", True),
        ("0.10.0", "0.9.0", True),  # tuple-of-ints must not lex-compare strings
        ("0.3.6", "0.3.6.dev0", True),
    ],
)
def test_is_newer(latest, current, expected):
    assert update_check._is_newer(latest, current) is expected


def test_is_newer_fallback_handles_unparseable(monkeypatch):
    """Even with packaging unavailable, garbage input must not raise."""
    import sys

    monkeypatch.setitem(sys.modules, "packaging.version", None)
    # Fallback path tolerates trailing junk by stripping at the first non-digit.
    assert update_check._is_newer("0.3.6rc1", "0.3.5") is True


# --------------------------------------------------------------------------- #
# Cache I/O — _write_cache / _read_cache
# --------------------------------------------------------------------------- #


def test_write_then_read_roundtrip(fake_home):
    update_check._write_cache("0.3.6", now=1234567.0)
    data = update_check._read_cache()
    assert data is not None
    assert data["latest"] == "0.3.6"
    assert data["checked_at_epoch"] == 1234567.0


def test_write_cache_swallows_unwritable_home(fake_home, monkeypatch):
    """A read-only $HOME must not raise — we just lose the cache."""

    def _raise_mkdir(self, *args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "mkdir", _raise_mkdir)
    update_check._write_cache("0.3.6")  # must not raise
    assert update_check._read_cache() is None


# --------------------------------------------------------------------------- #
# print_notice_if_due()
# --------------------------------------------------------------------------- #


def _capture_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, width=120, no_color=True), buf


def test_notice_prints_when_newer_available(fake_home, force_tty, clear_suppression_env, monkeypatch):
    monkeypatch.setattr(update_check, "__version__", "0.3.5")
    update_check._write_cache("0.3.6")

    console, buf = _capture_console()
    update_check.print_notice_if_due(console=console)

    out = buf.getvalue()
    assert "0.3.6" in out
    assert "0.3.5" in out
    assert update_check.UPGRADE_URL in out


def test_notice_silent_when_up_to_date(fake_home, force_tty, clear_suppression_env, monkeypatch):
    monkeypatch.setattr(update_check, "__version__", "0.3.6")
    update_check._write_cache("0.3.6")

    console, buf = _capture_console()
    update_check.print_notice_if_due(console=console)

    assert buf.getvalue() == ""


def test_notice_silent_with_no_cache(fake_home, force_tty, clear_suppression_env):
    console, buf = _capture_console()
    update_check.print_notice_if_due(console=console)
    assert buf.getvalue() == ""


def test_notice_silent_when_suppression_env_set(
    fake_home, force_tty, clear_suppression_env, monkeypatch
):
    """Even with a valid cache, suppression at print-time wins."""
    monkeypatch.setattr(update_check, "__version__", "0.3.5")
    update_check._write_cache("0.3.6")
    monkeypatch.setenv("SELVEDGE_NO_UPDATE_CHECK", "1")

    console, buf = _capture_console()
    update_check.print_notice_if_due(console=console)
    assert buf.getvalue() == ""


def test_notice_silent_when_stderr_not_tty(
    fake_home, clear_suppression_env, monkeypatch
):
    """If stderr was redirected after the check started, don't pollute it."""
    monkeypatch.setattr(update_check, "__version__", "0.3.5")
    update_check._write_cache("0.3.6")
    monkeypatch.setattr(update_check, "_stderr_is_tty", lambda: False)

    console, buf = _capture_console()
    update_check.print_notice_if_due(console=console)
    assert buf.getvalue() == ""


def test_notice_prints_at_most_once_per_process(
    fake_home, force_tty, clear_suppression_env, monkeypatch
):
    monkeypatch.setattr(update_check, "__version__", "0.3.5")
    update_check._write_cache("0.3.6")

    console, buf = _capture_console()
    update_check.print_notice_if_due(console=console)
    first = buf.getvalue()
    update_check.print_notice_if_due(console=console)
    second = buf.getvalue()
    assert first == second  # second call is a no-op
    assert first.count("selvedge:") == 1


# --------------------------------------------------------------------------- #
# Threading entry point — check_for_update_async()
# --------------------------------------------------------------------------- #


def test_check_for_update_async_only_spawns_one_thread(
    fake_home, force_tty, clear_suppression_env, monkeypatch
):
    """Calling the entry point twice in one process spawns one worker."""
    calls = []

    def _fake_thread_target(*args, **kwargs):
        calls.append("started")

        class _Stub:
            def start(self):
                calls.append("start")

        return _Stub()

    monkeypatch.setattr("threading.Thread", _fake_thread_target)
    update_check.check_for_update_async()
    update_check.check_for_update_async()
    update_check.check_for_update_async()
    assert calls.count("start") == 1


def test_run_check_writes_cache_on_success(
    fake_home, force_tty, clear_suppression_env, monkeypatch
):
    """End-to-end: gate → fetch → cache. No threads, no network."""
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _fake_urlopen_response({"info": {"version": "9.9.9"}}),
    )
    update_check._run_check()
    cached = update_check._read_cache()
    assert cached is not None
    assert cached["latest"] == "9.9.9"


def test_run_check_does_not_write_cache_on_network_error(
    fake_home, force_tty, clear_suppression_env, monkeypatch
):
    """A failed fetch must not poison the cache with a stale or bogus entry."""

    def _raise(req, timeout=None):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    update_check._run_check()
    assert update_check._read_cache() is None
