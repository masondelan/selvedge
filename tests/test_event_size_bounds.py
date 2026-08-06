"""Event-size bounds at log time (v0.3.10).

`diff_bytes` and `reasoning_bytes` cap what one event can carry. The trade
being made: an unbounded store grows without limit and gets committed to a
repo, but silently clipping the reasoning behind a decision is exactly the
loss this tool exists to prevent. So truncation is *loud* — a marker in the
stored text, a warning at write time, and a running count in `selvedge stats`.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from selvedge.cli import cli
from selvedge.storage import SelvedgeStorage, truncate_field


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def _fresh_server_storage():
    import selvedge.server as srv
    srv._storage = None
    yield
    srv._storage = None


# ---------------------------------------------------------------------------
# truncate_field
# ---------------------------------------------------------------------------


def test_short_text_is_returned_untouched():
    assert truncate_field("hello", 100) == ("hello", 0)


def test_zero_means_no_limit():
    big = "x" * 10_000
    assert truncate_field(big, 0) == (big, 0)


def test_truncation_marks_how_much_was_dropped():
    text, dropped = truncate_field("y" * 3000, 1000)
    assert dropped == 2000
    assert text.startswith("y" * 1000)
    assert "[truncated 2KB]" in text


def test_truncation_counts_bytes_not_characters():
    """A multi-byte string must be measured the way it is stored."""
    text = "é" * 100  # 200 UTF-8 bytes
    out, dropped = truncate_field(text, 100)
    assert dropped == 100
    assert len(out.encode("utf-8")) <= 100 + len("…[truncated 100B]".encode())


def test_split_multibyte_character_is_dropped_not_mangled():
    """Cutting mid-character must not store a replacement character."""
    out, _ = truncate_field("aaa" + "€" * 10, 5)  # cut lands inside a 3-byte char
    assert "�" not in out


# ---------------------------------------------------------------------------
# Applied at write time, on both surfaces
# ---------------------------------------------------------------------------


def test_mcp_log_change_truncates_and_warns(monkeypatch):
    from selvedge.server import blame, log_change

    monkeypatch.setenv("SELVEDGE_REASONING_BYTES", "200")
    result = log_change(
        entity_path="users.email", change_type="add", reasoning="z" * 5000,
    )

    assert result["status"] == "logged", "truncation must warn, never reject"
    assert any("reasoning truncated" in w for w in result["warnings"])
    stored = blame("users.email")["reasoning"]
    assert "[truncated" in stored
    assert len(stored.encode("utf-8")) < 5000


def test_cli_log_truncates_and_warns(runner, monkeypatch):
    monkeypatch.setenv("SELVEDGE_DIFF_BYTES", "100")
    result = runner.invoke(
        cli, ["log", "users.email", "add", "--diff", "q" * 4000, "-r", "why"]
    )
    assert result.exit_code == 0
    assert "diff truncated" in result.stderr

    blamed = json.loads(runner.invoke(cli, ["blame", "users.email", "--json"]).stdout)
    assert "[truncated" in blamed["diff"]


def test_the_event_is_still_logged_when_truncated(monkeypatch):
    """Warn, never reject — the whole posture of the write path."""
    from selvedge.server import log_change

    monkeypatch.setenv("SELVEDGE_REASONING_BYTES", "50")
    result = log_change(
        entity_path="users.email", change_type="add", reasoning="w" * 900,
    )
    assert result["status"] == "logged"
    assert result["id"]


def test_default_limits_leave_ordinary_reasoning_alone(monkeypatch):
    """A normal event must never trip the bounds."""
    from selvedge.server import log_change

    for spec in ("SELVEDGE_REASONING_BYTES", "SELVEDGE_DIFF_BYTES"):
        monkeypatch.delenv(spec, raising=False)
    reasoning = (
        "Switched the session store from cookies to Redis because the "
        "cookie payload passed 4KB once we added workspace scopes."
    )
    result = log_change(
        entity_path="src/session.py", change_type="modify", reasoning=reasoning
    )
    assert not [w for w in result["warnings"] if "truncated" in w]


# ---------------------------------------------------------------------------
# Visibility after the fact
# ---------------------------------------------------------------------------


def test_stats_counts_truncated_events(runner, monkeypatch, tmp_path):
    monkeypatch.setenv("SELVEDGE_REASONING_BYTES", "60")
    runner.invoke(cli, ["log", "a.b", "add", "-r", "m" * 900])
    runner.invoke(cli, ["log", "c.d", "add", "-r", "short and clean"])

    payload = json.loads(runner.invoke(cli, ["stats", "--json"]).stdout)
    assert payload["truncated_events"] == 1


def test_count_truncated_is_zero_on_a_clean_store(tmp_path):
    storage = SelvedgeStorage(tmp_path / "t.db")
    assert storage.count_truncated() == 0
