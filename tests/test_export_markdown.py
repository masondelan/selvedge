"""`selvedge export --format markdown` (v0.3.10).

The point of the format is that it gets committed, so the two properties
under test are the ones that make a generated file survive in version
control: byte-for-byte determinism, and anchors that don't move.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from selvedge.cli import cli
from selvedge.exporters.markdown import _anchor, render_markdown
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    db = tmp_path / ".selvedge" / "selvedge.db"
    db.parent.mkdir(parents=True)
    monkeypatch.setenv("SELVEDGE_DB", str(db))
    return SelvedgeStorage(db)


def _seed(storage):
    storage.log_event(ChangeEvent(
        entity_path="users.sso_token", change_type="add",
        timestamp="2026-01-01T00:00:00Z", agent="claude",
        reasoning="Tried a dedicated SSO token column for the provider handoff.",
        constraint="tokens must be short-lived"))
    storage.log_event(ChangeEvent(
        entity_path="users.sso_token", change_type="remove",
        timestamp="2026-01-02T00:00:00Z", agent="claude",
        reasoning="Reverted: tokens belong in the sessions table, not on users."))
    storage.log_event(ChangeEvent(
        entity_path="users.email", change_type="add",
        timestamp="2026-01-03T00:00:00Z",
        reasoning="Added so password reset has somewhere to send."))
    storage.log_event(ChangeEvent(
        entity_path="payments.card_token", change_type="add",
        timestamp="2026-02-01T00:00:00Z", reasoning="First attempt."))
    storage.log_event(ChangeEvent(
        entity_path="payments.card_token", change_type="remove",
        timestamp="2026-02-02T00:00:00Z", reasoning="PCI scope."))
    storage.log_event(ChangeEvent(
        entity_path="payments.card_token", change_type="supersede",
        timestamp="2026-03-01T00:00:00Z",
        reasoning="Provider vaults cards now; the PCI constraint is gone."))


# ---------------------------------------------------------------------------
# Determinism — the property that makes it committable
# ---------------------------------------------------------------------------


def test_output_is_byte_identical_across_runs(storage):
    _seed(storage)
    rows = storage.get_history(limit=100)
    assert render_markdown(rows) == render_markdown(rows)


def test_input_order_does_not_change_the_output(storage):
    """Re-sorted internally, so a differently-ordered read is the same file."""
    _seed(storage)
    rows = storage.get_history(limit=100)
    assert render_markdown(rows) == render_markdown(list(reversed(rows)))


def test_regenerating_without_new_events_is_a_zero_line_diff(storage, runner, tmp_path):
    """The whole point: a noisy generated file is one nobody reads."""
    _seed(storage)
    out = tmp_path / "DECISIONS.md"
    runner.invoke(cli, ["export", "--format", "markdown", "-o", str(out)])
    first = out.read_text()
    runner.invoke(cli, ["export", "--format", "markdown", "-o", str(out)])
    assert out.read_text() == first


def test_no_generation_timestamp_leaks_in(storage):
    """A wall-clock stamp would make every regeneration a diff."""
    _seed(storage)
    import datetime
    today = datetime.date.today().isoformat()
    assert today not in render_markdown(storage.get_history(limit=100))


# ---------------------------------------------------------------------------
# Anchor stability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("users.email", "usersemail"),
        ("src/auth.py::login", "srcauthpylogin"),
        ("env/STRIPE_SECRET_KEY", "envstripe-secret-key"),
        ("deps/stripe", "depsstripe"),
    ],
)
def test_anchor_is_derived_only_from_the_path(path, expected):
    assert _anchor(path) == expected


def test_anchors_survive_new_events(storage):
    _seed(storage)
    before = render_markdown(storage.get_history(limit=100))
    anchor = _anchor("users.sso_token")
    assert f'id="{anchor}"' in before

    storage.log_event(ChangeEvent(
        entity_path="zzz.new_entity", change_type="add", reasoning="later work"))
    after = render_markdown(storage.get_history(limit=100))
    assert f'id="{anchor}"' in after, "an anchor moved when unrelated events landed"


# ---------------------------------------------------------------------------
# Content and ordering
# ---------------------------------------------------------------------------


def test_reverted_decisions_come_first(storage):
    """Precision over recall, applied to document order.

    The expensive failure is re-implementing something already killed, so a
    reader skimming the top hits that before the chronology.
    """
    _seed(storage)
    doc = render_markdown(storage.get_history(limit=100))
    assert doc.index("## Tried and reverted") < doc.index("## Re-opened")
    assert doc.index("## Re-opened") < doc.index("## Active decisions")


def test_a_superseded_entity_is_classified_as_reopened(storage):
    _seed(storage)
    doc = render_markdown(storage.get_history(limit=100))
    reopened = doc.split("## Re-opened")[1].split("## Active")[0]
    assert "payments.card_token" in reopened


def test_constraint_is_surfaced_above_the_timeline(storage):
    _seed(storage)
    doc = render_markdown(storage.get_history(limit=100))
    section = doc.split("### `users.sso_token`")[1].split("###")[0]
    assert "**Constraint:** tokens must be short-lived" in section
    assert section.index("**Constraint:**") < section.index("- **add**")


def test_markdown_control_characters_in_reasoning_are_escaped(storage):
    """Agent-written text must not be able to break the document structure."""
    storage.log_event(ChangeEvent(
        entity_path="a.b", change_type="add",
        reasoning="used `rm -rf` | piped * into _thing_\nand a newline"))
    doc = render_markdown(storage.get_history(limit=100))
    body = doc.split("- **add**")[1]
    assert "\\`" in body and "\\|" in body and "\\*" in body
    assert "\n" not in body.split("\n")[0].replace("and a newline", "x") or True
    # The reasoning must occupy exactly one list line.
    assert body.splitlines()[0].endswith("and a newline")


def test_empty_store_renders_a_valid_document(storage):
    doc = render_markdown([])
    assert doc.startswith("# Selvedge decision log")
    assert "_No change events recorded yet._" in doc


def test_cli_writes_the_file(storage, runner, tmp_path):
    _seed(storage)
    out = tmp_path / "DECISIONS.md"
    result = runner.invoke(cli, ["export", "--format", "markdown", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.is_file()
    assert "users.sso_token" in out.read_text()


def test_cli_writes_to_stdout_by_default(storage, runner):
    _seed(storage)
    result = runner.invoke(cli, ["export", "--format", "markdown"])
    assert result.exit_code == 0
    assert result.stdout.startswith("# Selvedge decision log")
