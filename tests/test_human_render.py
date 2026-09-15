"""Human-only rendering for ``blame`` and ``prior-attempts`` (UX-1A).

JSON payloads, exits, query semantics and changeset output stay frozen.
These cases cover the two presentation changes: an explicit stand-in for
empty recorded reasoning, and hanging wrap of explanation text.
"""

from __future__ import annotations

import json
from io import StringIO

import pytest
from click.testing import CliRunner
from rich.console import Console
from rich.text import Text

from selvedge.cli import (
    _REASON_NOT_RECORDED,
    _TRAIL_PREFIXES,
    _print_explanation,
    _reason_display,
    cli,
)
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SELVEDGE_DB", str(tmp_path / "selvedge.db"))
    import selvedge.server as srv

    srv._storage = None
    yield
    srv._storage = None


def _storage() -> SelvedgeStorage:
    from selvedge.config import get_db_path

    return SelvedgeStorage(get_db_path())


def _log(path, change_type, ts, reasoning="", **kwargs) -> ChangeEvent:
    return _storage().log_event(
        ChangeEvent(
            entity_path=path,
            change_type=change_type,
            timestamp=ts,
            reasoning=reasoning,
            **kwargs,
        )
    )


def _console(width: int) -> tuple[Console, StringIO]:
    buf = StringIO()
    return (
        Console(
            file=buf,
            width=width,
            color_system=None,
            force_terminal=False,
            highlight=False,
            legacy_windows=False,
        ),
        buf,
    )


def _hang_column(prefix: str, left: int = 4) -> int:
    return left + Text.from_markup(prefix).cell_len


def _joined_body(text: str, hang: int) -> str:
    """Reassemble hanging-wrapped body text, ignoring display-only wrap spaces."""
    lines = text.splitlines()
    if not lines:
        return ""
    first = lines[0][hang:] if len(lines[0]) >= hang else lines[0].lstrip()
    rest = [line[hang:] if len(line) >= hang else line.lstrip() for line in lines[1:]]
    return first + "".join(rest)


# ---------------------------------------------------------------------------
# Helper: empty vs recorded, wrap, markup
# ---------------------------------------------------------------------------


def test_reason_display_empty_is_valid_absence():
    assert _reason_display("") == (_REASON_NOT_RECORDED, False)
    assert _reason_display("   \n") == (_REASON_NOT_RECORDED, False)
    assert _reason_display(None) == (_REASON_NOT_RECORDED, False)


def test_reason_display_keeps_recorded_text():
    text, recorded = _reason_display("Added for login flow")
    assert recorded is True
    assert text == "Added for login flow"


@pytest.mark.parametrize("width", [40, 80, 120])
def test_explanation_wraps_with_hanging_indent(width):
    body = (
        "Store a per-user auth token so the mobile app can stay signed in "
        "across restarts and we can revoke it without a write."
    )
    console, buf = _console(width)
    prefix = _TRAIL_PREFIXES["tried"]
    hang = _hang_column(prefix)
    _print_explanation(body, left=4, prefix=prefix, out=console)
    lines = buf.getvalue().splitlines()
    assert lines, "expected wrapped output"
    assert lines[0].startswith("    tried:")
    for line in lines:
        assert len(line) <= width, (width, repr(line), len(line))
    for line in lines[1:]:
        assert line.startswith(" " * hang), repr(line)
        assert not line[hang:].startswith(" "), repr(line)
    reconstructed = _joined_body(buf.getvalue(), hang).replace(" ", "")
    assert reconstructed == body.replace(" ", "")
    assert body.split()[0] in buf.getvalue()
    assert body.split()[-1] in buf.getvalue()


@pytest.mark.parametrize("width", [40, 80, 120])
def test_explanation_folds_long_unbreakable_path(width):
    body = "src/very/long/path/to/auth/session.py::handle_login_and_refresh_token"
    console, buf = _console(width)
    prefix = _TRAIL_PREFIXES["tried"]
    hang = _hang_column(prefix)
    _print_explanation(body, left=4, prefix=prefix, out=console)
    assert _joined_body(buf.getvalue(), hang) == body
    for line in buf.getvalue().splitlines():
        assert len(line) <= width, (width, repr(line))


def test_explanation_keeps_markup_like_input_literal():
    body = "Rejected [bold]not markup[/bold] and [red]still literal[/red]."
    console, buf = _console(80)
    _print_explanation(body, left=4, prefix=_TRAIL_PREFIXES["rejected"], out=console)
    out = buf.getvalue()
    assert "[bold]not markup[/bold]" in out
    assert "[red]still literal[/red]" in out
    assert "\x1b[" not in out


# ---------------------------------------------------------------------------
# blame human + JSON/exit parity
# ---------------------------------------------------------------------------


def test_blame_recorded_reason_vs_empty_reason(runner):
    _log("users.email", "add", "2026-01-01T00:00:00Z", "Added for login flow")
    _log("users.token", "add", "2026-01-01T00:00:00Z", "")

    recorded = runner.invoke(cli, ["blame", "users.email"])
    assert recorded.exit_code == 0, recorded.output
    assert "Added for login flow" in recorded.output
    assert _REASON_NOT_RECORDED not in recorded.output

    empty = runner.invoke(cli, ["blame", "users.token"])
    assert empty.exit_code == 0, empty.output
    assert "Reasoning:" in empty.output
    assert _REASON_NOT_RECORDED in empty.output
    assert "invalid" not in empty.output.lower()

    payload = json.loads(runner.invoke(cli, ["blame", "users.token", "--json"]).stdout)
    assert payload["reasoning"] == ""
    assert payload["error"] == ""
    assert payload["entity_path"] == "users.token"


def test_blame_no_hit_unchanged(runner):
    human = runner.invoke(cli, ["blame", "ghost.entity"])
    assert human.exit_code == 1
    assert "No history found for 'ghost.entity'" in human.output
    assert _REASON_NOT_RECORDED not in human.output

    raw = runner.invoke(cli, ["blame", "ghost.entity", "--json"])
    assert raw.exit_code == 1
    payload = json.loads(raw.stdout)
    from selvedge.server import blame as mcp_blame

    assert payload == mcp_blame("ghost.entity")
    assert payload["reasoning"] == ""
    assert payload["error"].startswith("No history found")


def test_blame_json_hit_matches_mcp_and_keeps_long_evidence(runner):
    long_reason = "Kept the refresh-token table. " * 20
    long_path = "src/auth/session.py::handle_login_and_refresh_token"
    _log(long_path, "add", "2026-01-01T00:00:00Z", long_reason, diff="--- a\n+++ b\n")

    raw = runner.invoke(cli, ["blame", long_path, "--json"])
    assert raw.exit_code == 0
    payload = json.loads(raw.stdout)
    from selvedge.server import blame as mcp_blame

    assert payload == mcp_blame(long_path)
    assert payload["reasoning"] == long_reason
    assert payload["diff"] == "--- a\n+++ b\n"

    human = runner.invoke(cli, ["blame", long_path])
    assert long_path in human.output
    assert "Kept the refresh-token table." in human.output
    # Folded wrap may split a word; the stored sentence is still present.
    joined = "".join(human.output.split())
    assert "".join(long_reason.split()) in joined


def test_blame_markup_like_reasoning_is_literal(runner):
    _log(
        "users.flag",
        "add",
        "2026-01-01T00:00:00Z",
        "Do not parse [red]this[/red] as color.",
    )
    result = runner.invoke(cli, ["blame", "users.flag"])
    assert result.exit_code == 0
    assert "[red]this[/red]" in result.output


# ---------------------------------------------------------------------------
# prior-attempts human + JSON/exit parity
# ---------------------------------------------------------------------------


def test_prior_attempts_recorded_reason_vs_empty_reason(runner):
    _log("users.auth_token", "add", "2026-01-01T00:00:00Z", "Tried a per-user token column.")
    _log("users.auth_token", "remove", "2026-01-02T00:00:00Z", "Reverted: moved to JWTs.")
    _log("users.empty", "add", "2026-01-01T00:00:00Z", "")
    _log("users.empty", "remove", "2026-01-02T00:00:00Z", "")

    recorded = runner.invoke(cli, ["prior-attempts", "users.auth_token"])
    assert recorded.exit_code == 0, recorded.output
    assert "tried:" in recorded.output
    assert "Tried a per-user token column." in recorded.output
    assert "reverted:" in recorded.output
    assert "moved to JWTs" in recorded.output
    assert _REASON_NOT_RECORDED not in recorded.output

    empty = runner.invoke(cli, ["prior-attempts", "users.empty"])
    assert empty.exit_code == 0, empty.output
    assert "tried:" in empty.output
    assert "reverted:" in empty.output
    assert empty.output.count(_REASON_NOT_RECORDED) == 2
    assert "invalid" not in empty.output.lower()

    payload = json.loads(runner.invoke(cli, ["prior-attempts", "users.empty", "--json"]).stdout)
    assert len(payload) == 1
    assert payload[0]["reasoning"] == ""
    assert payload[0]["outcome_reasoning"] == ""
    assert payload[0]["outcome"] == "reverted"


def test_prior_attempts_no_hit_exits_zero(runner):
    result = runner.invoke(cli, ["prior-attempts", "ghost.entity"])
    assert result.exit_code == 0
    assert "No prior attempts found for ghost.entity." in result.output
    assert _REASON_NOT_RECORDED not in result.output

    raw = runner.invoke(cli, ["prior-attempts", "ghost.entity", "--json"])
    assert raw.exit_code == 0
    assert json.loads(raw.stdout) == []


def test_prior_attempts_json_matches_mcp_on_hit(runner):
    _log("users.auth_token", "add", "2026-01-01T00:00:00Z", "Tried a per-user token column.")
    _log("users.auth_token", "remove", "2026-01-02T00:00:00Z", "Reverted: moved to JWTs.")
    raw = runner.invoke(cli, ["prior-attempts", "users.auth_token", "--json"])
    assert raw.exit_code == 0
    from selvedge.server import prior_attempts

    assert json.loads(raw.stdout) == prior_attempts(entity_path="users.auth_token")


def test_prior_attempts_rejection_still_uses_rejected_not_tried(runner):
    _log(
        "users.card_pan",
        "reject",
        "2026-01-01T00:00:00Z",
        "Rejected storing raw PANs; chose tokenization — PCI scope.",
    )
    result = runner.invoke(cli, ["prior-attempts", "users.card_pan"])
    assert result.exit_code == 0
    assert "rejected:" in result.output
    assert "tried:" not in result.output
    assert _REASON_NOT_RECORDED not in result.output


def test_prior_attempts_superseded_target_and_active_sibling(runner):
    """Path-level ``current_status``: a sibling may read reopened while active.

    That is existing query semantics — do not treat it as event-level
    supersession. Human output still shows the sibling as ``active`` with no
    re-opened trail of its own.
    """
    _log(
        "payments.card_token",
        "add",
        "2026-01-01T00:00:00Z",
        "Store card tokens locally for faster checkout.",
    )
    _log(
        "payments.card_token",
        "remove",
        "2026-01-02T00:00:00Z",
        "Reverted: card data in our own DB puts us in PCI scope.",
    )
    sibling = _log(
        "payments.card_token",
        "modify",
        "2026-01-03T00:00:00Z",
        "Later sibling still in play on the same path.",
    )
    _storage().log_supersede(
        "payments.card_token",
        reasoning="Provider now vaults card data — PCI constraint gone.",
    )

    raw = json.loads(
        runner.invoke(cli, ["prior-attempts", "payments.card_token", "--all", "--json"]).stdout
    )
    by_id = {row["id"]: row for row in raw}
    assert by_id[sibling.id]["outcome"] == "active"
    assert by_id[sibling.id]["superseded_by"] == ""
    assert by_id[sibling.id]["current_status"] == "reopened"
    reopened = [row for row in raw if row["outcome"] == "reopened"]
    assert reopened, raw
    assert reopened[0]["current_status"] == "reopened"

    human = runner.invoke(cli, ["prior-attempts", "payments.card_token", "--all"])
    assert human.exit_code == 0, human.output
    assert "re-opened:" in human.output
    assert "Later sibling still in play" in human.output
    sibling_blocks = [
        block for block in human.output.split("\n\n") if "Later sibling still in play" in block
    ]
    assert sibling_blocks
    assert "re-opened:" not in sibling_blocks[0]
    assert "active" in sibling_blocks[0]
    assert "current status" in human.output
    assert "reopened" in human.output


def test_prior_attempts_wraps_long_reason_at_narrow_width(runner, monkeypatch):
    prose = (
        "Store a per-user auth token so the mobile app can stay signed in "
        "across restarts and we can revoke it without a write."
    )
    _log("users.auth_token", "add", "2026-01-01T00:00:00Z", prose)
    _log("users.auth_token", "remove", "2026-01-02T00:00:00Z", "Reverted: moved to JWTs.")

    import selvedge.cli as cli_mod

    # Set both so Rich does not ignore a lone `_width` on a dumb/non-TTY stream.
    monkeypatch.setattr(cli_mod.console, "_width", 40)
    monkeypatch.setattr(cli_mod.console, "_height", 25)
    result = runner.invoke(cli, ["prior-attempts", "users.auth_token"])
    assert result.exit_code == 0, result.output
    hang = _hang_column(_TRAIL_PREFIXES["tried"])
    tried_lines = []
    in_tried = False
    for line in result.output.splitlines():
        if line.startswith("    tried:"):
            in_tried = True
            tried_lines.append(line)
            continue
        if in_tried:
            if line.startswith(" " * hang) and line[hang : hang + 1] != " ":
                tried_lines.append(line)
            else:
                break
    assert len(tried_lines) > 1
    for line in tried_lines:
        assert len(line) <= 40, repr(line)
    joined = _joined_body("\n".join(tried_lines), hang).replace(" ", "")
    assert joined == prose.replace(" ", "")


# ---------------------------------------------------------------------------
# changeset regression — human rendering of changeset is untouched
# ---------------------------------------------------------------------------


def test_changeset_human_and_json_unchanged_for_empty_reason(runner):
    _log(
        "payments.col0",
        "add",
        "2026-01-01T00:00:00Z",
        "",
        changeset_id="ux1a-cs",
    )
    _log(
        "payments.col1",
        "add",
        "2026-01-01T00:01:00Z",
        "Adding payments column 1 for Stripe integration",
        changeset_id="ux1a-cs",
    )
    human = runner.invoke(cli, ["changeset", "ux1a-cs"])
    assert human.exit_code == 0, human.output
    assert "payments.col0" in human.output
    assert "payments.col1" in human.output
    assert _REASON_NOT_RECORDED not in human.output

    raw = runner.invoke(cli, ["changeset", "ux1a-cs", "--json"])
    assert raw.exit_code == 0
    data = json.loads(raw.stdout)
    from selvedge.server import changeset

    assert data == changeset("ux1a-cs")
    assert data[0]["reasoning"] == ""


# ---------------------------------------------------------------------------
# Unforced pipe / no-color: CliRunner is a non-TTY capture
# ---------------------------------------------------------------------------


def test_unforced_cli_capture_has_no_ansi_on_empty_reason(runner):
    """Ordinary non-TTY capture (the test runner's pipe analogue).

    Does not set FORCE_COLOR. Interactive TTY, Windows, and live MCP were
    not executed in this suite.
    """
    _log("users.token", "add", "2026-01-01T00:00:00Z", "")
    result = runner.invoke(cli, ["blame", "users.token"])
    assert result.exit_code == 0
    assert _REASON_NOT_RECORDED in result.output
    assert "\x1b[" not in result.output
    assert "\x1b[" not in result.stdout
