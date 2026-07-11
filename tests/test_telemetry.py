"""
Tests for :mod:`selvedge.telemetry` — the opt-in anonymous heartbeat.

The full contract under test:

* **Off by default.** No consent file, no env var → nothing is gated in,
  nothing is sent, ``build_payload`` is never reached from ``ping_async``.
* **Kill switches beat consent.** ``SELVEDGE_TELEMETRY=0`` and
  ``SELVEDGE_NO_TELEMETRY=1`` win over a recorded ``enabled: true``.
* **CI and dev installs never send**, even with consent.
* **The payload is exactly the documented schema** — fixed key set, no
  paths, no repo names, nothing machine-derived.
* **enable/disable lifecycle:** enable mints a UUID and is idempotent;
  disable deletes the id so consent periods can't be linked.
* **24h interval** gating via ``last_ping_epoch``, stamped before the send.
* **Never raises**, even when the endpoint is unreachable or ``$HOME``
  is read-only.

Crucially: **no test in this module hits the network.** ``urlopen`` is
monkeypatched everywhere it could be reached.
"""

from __future__ import annotations

import json
import time
import urllib.error
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner

from selvedge import telemetry
from selvedge.cli import cli

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Each test starts with the one-shot ping guard cleared."""
    telemetry._reset_for_testing()
    yield
    telemetry._reset_for_testing()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No telemetry-relevant env vars leak in from the invoking shell."""
    for var in ("SELVEDGE_TELEMETRY", "SELVEDGE_NO_TELEMETRY", "SELVEDGE_TELEMETRY_URL", "CI"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """
    Redirect ``Path.home()`` so consent-file writes go to ``tmp_path``.

    The module reads ``Path.home()`` lazily on every call, so patching it
    here is enough — same pattern as tests/test_update_check.py.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def no_network(monkeypatch):
    """Any urlopen call in this test is a bug — fail loudly."""

    def _boom(*args, **kwargs):  # pragma: no cover - reaching this IS the failure
        raise AssertionError("telemetry attempted a network call while gated off")

    monkeypatch.setattr(telemetry.urllib.request, "urlopen", _boom)


@pytest.fixture
def capture_send(monkeypatch):
    """Record outgoing payloads instead of touching the network."""
    sent: list[dict] = []

    class _FakeResp:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return _FakeResp()

    monkeypatch.setattr(telemetry.urllib.request, "urlopen", _fake_urlopen)
    return sent


def _consent_file(home: Path) -> Path:
    return home / ".selvedge" / telemetry.CONSENT_FILENAME


# --------------------------------------------------------------------------- #
# Default-off + gating
# --------------------------------------------------------------------------- #


def test_disabled_by_default(fake_home):
    assert telemetry.is_enabled() is False
    assert telemetry.should_send() is False


def test_ping_async_sends_nothing_by_default(fake_home, no_network):
    telemetry.ping_async("cli")
    # The daemon thread gates out before any network call; give it a beat.
    time.sleep(0.2)
    assert not _consent_file(fake_home).exists()


def test_env_opt_in_enables(fake_home, monkeypatch):
    monkeypatch.setenv("SELVEDGE_TELEMETRY", "1")
    assert telemetry.is_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_env_kill_switch_beats_file_consent(fake_home, monkeypatch, value):
    telemetry.enable()
    monkeypatch.setenv("SELVEDGE_TELEMETRY", value)
    assert telemetry.is_enabled() is False
    assert telemetry.should_send() is False


def test_no_telemetry_var_beats_everything(fake_home, monkeypatch):
    telemetry.enable()
    monkeypatch.setenv("SELVEDGE_TELEMETRY", "1")
    monkeypatch.setenv("SELVEDGE_NO_TELEMETRY", "1")
    assert telemetry.is_enabled() is False


def test_ci_suppresses_send_but_not_consent(fake_home, monkeypatch):
    telemetry.enable()
    monkeypatch.setenv("CI", "true")
    assert telemetry.is_enabled() is True
    assert telemetry.should_send() is False


def test_dev_install_suppresses_send(fake_home, monkeypatch):
    telemetry.enable()
    monkeypatch.setattr(telemetry, "_is_dev_install", lambda v: True)
    assert telemetry.should_send() is False


def test_interval_gate(fake_home):
    telemetry.enable()
    now = time.time()
    state = telemetry._read_state()
    state["last_ping_epoch"] = now - 60
    telemetry._write_state(state)
    assert telemetry.should_send(now=now) is False
    assert telemetry.should_send(now=now + telemetry.PING_INTERVAL_SECONDS + 1) is True


# --------------------------------------------------------------------------- #
# Payload anonymity
# --------------------------------------------------------------------------- #


def test_payload_is_exactly_the_documented_schema(fake_home):
    telemetry.enable()
    payload = telemetry.build_payload("cli")
    assert payload is not None
    assert set(payload) == {"schema", "install_id", "version", "python", "os", "arch", "source"}
    assert payload["schema"] == telemetry.PAYLOAD_SCHEMA_VERSION
    assert payload["source"] == "cli"
    # install_id is a valid UUID and not derived from anything local
    uuid.UUID(str(payload["install_id"]))
    # Nothing path-like or repo-like leaks into any value
    for value in payload.values():
        assert "/" not in str(value).replace("N/A", "")
        assert str(fake_home) not in str(value)


def test_payload_stable_install_id(fake_home):
    telemetry.enable()
    first = telemetry.build_payload("cli")
    second = telemetry.build_payload("server")
    assert first is not None and second is not None
    assert first["install_id"] == second["install_id"]
    assert second["source"] == "server"


def test_env_only_consent_mints_persistent_id(fake_home, monkeypatch):
    monkeypatch.setenv("SELVEDGE_TELEMETRY", "1")
    payload = telemetry.build_payload("cli")
    assert payload is not None
    on_disk = json.loads(_consent_file(fake_home).read_text())
    assert on_disk["install_id"] == payload["install_id"]


def test_unwritable_state_means_no_send(fake_home, monkeypatch):
    """No stable id → don't send. An unstable id would corrupt the count."""
    monkeypatch.setenv("SELVEDGE_TELEMETRY", "1")
    monkeypatch.setattr(telemetry, "_write_state", lambda state: False)
    assert telemetry.build_payload("cli") is None


# --------------------------------------------------------------------------- #
# enable / disable lifecycle
# --------------------------------------------------------------------------- #


def test_enable_disable_roundtrip(fake_home):
    state = telemetry.enable()
    assert state["enabled"] is True
    first_id = state["install_id"]

    # Idempotent: re-enable keeps the id (no double counting)
    assert telemetry.enable()["install_id"] == first_id

    after = telemetry.disable()
    assert after["enabled"] is False
    assert "install_id" not in after
    assert "last_ping_epoch" not in after

    # Fresh consent period → fresh id
    assert telemetry.enable()["install_id"] != first_id


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #


def test_run_ping_sends_and_stamps(fake_home, capture_send):
    telemetry.enable()
    telemetry._run_ping("cli")
    assert len(capture_send) == 1
    assert capture_send[0]["source"] == "cli"
    assert telemetry._read_state()["last_ping_epoch"] > 0


def test_stamp_written_even_when_send_fails(fake_home, monkeypatch):
    """A dead endpoint must not turn every invocation into a network attempt."""
    telemetry.enable()

    def _fail(req, timeout=None):
        raise urllib.error.URLError("unreachable")

    monkeypatch.setattr(telemetry.urllib.request, "urlopen", _fail)
    telemetry._run_ping("cli")  # must not raise
    assert telemetry._read_state()["last_ping_epoch"] > 0
    assert telemetry.should_send() is False


def test_send_honors_endpoint_override(fake_home, capture_send, monkeypatch):
    monkeypatch.setenv("SELVEDGE_TELEMETRY_URL", "https://example.invalid/ping")
    assert telemetry._endpoint() == "https://example.invalid/ping"


def test_send_never_raises_on_exotic_errors(fake_home, monkeypatch):
    telemetry.enable()

    def _boom(req, timeout=None):
        raise RuntimeError("exotic")

    monkeypatch.setattr(telemetry.urllib.request, "urlopen", _boom)
    assert telemetry._send(telemetry.build_payload("cli") or {}) is False


def test_ping_async_one_shot_per_process(fake_home, capture_send):
    telemetry.enable()
    telemetry.ping_async("cli")
    telemetry.ping_async("cli")
    deadline = time.time() + 5
    while not capture_send and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.2)  # would-be second thread gets a chance to misfire
    assert len(capture_send) == 1


# --------------------------------------------------------------------------- #
# CLI surface
# --------------------------------------------------------------------------- #


def test_cli_status_json_default_off(fake_home, no_network):
    runner = CliRunner()
    result = runner.invoke(cli, ["telemetry", "status", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["enabled"] is False
    assert data["consent_source"] == "default"
    assert data["install_id"] == ""
    assert data["payload_preview"] == {}


def test_cli_enable_status_disable(fake_home, no_network):
    runner = CliRunner()
    assert runner.invoke(cli, ["telemetry", "enable"]).exit_code == 0

    result = runner.invoke(cli, ["telemetry", "status", "--json"])
    data = json.loads(result.output)
    assert data["enabled"] is True
    assert data["consent_source"] == "file"
    assert data["payload_preview"]["source"] == "cli"

    assert runner.invoke(cli, ["telemetry", "disable"]).exit_code == 0
    result = runner.invoke(cli, ["telemetry", "status", "--json"])
    assert json.loads(result.output)["enabled"] is False


def test_cli_bare_telemetry_shows_status(fake_home, no_network):
    runner = CliRunner()
    result = runner.invoke(cli, ["telemetry"])
    assert result.exit_code == 0
    assert "disabled" in result.output


# --------------------------------------------------------------------------- #
# State-file robustness
# --------------------------------------------------------------------------- #


def test_malformed_state_file_is_treated_as_empty(fake_home):
    path = _consent_file(fake_home)
    path.parent.mkdir(parents=True)
    path.write_text("not json{{{")
    assert telemetry._read_state() == {}
    assert telemetry.is_enabled() is False


def test_state_file_is_json_not_a_list(fake_home):
    path = _consent_file(fake_home)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(["enabled"]))
    assert telemetry._read_state() == {}
