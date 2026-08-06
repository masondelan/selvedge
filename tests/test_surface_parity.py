"""CLI `--json` and the MCP tools must return identical structures.

Both surfaces wrap the same storage layer, and each had grown its own
defaulting, error-shaping and result assembly. The tell is that the drifted
code *asserts* parity in its comments — `cli.py` reads "Mirror the MCP blame
tool ... so the two surfaces can't diverge" directly above a divergence.

These tests compare the two surfaces directly rather than asserting each one
against a fixture, so a change to either that isn't mirrored fails here.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from selvedge.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def _reset_server_storage():
    import selvedge.server as srv
    srv._storage = None
    yield
    srv._storage = None


def _cli_json(runner, args: list[str]) -> object:
    """Parse the CLI's STDOUT only.

    `result.output` merges stderr, and `prior-attempts --fuzzy` legitimately
    writes its fallback notice there — reading `.output` would make this
    helper fail on exactly the case it exists to check.
    """
    result = runner.invoke(cli, args)
    assert result.stdout, f"no stdout from {args}: stderr={result.stderr!r}"
    return json.loads(result.stdout)


def _seed_rename():
    from selvedge.server import log_change

    log_change(
        entity_path="src/auth/session.py::login",
        change_type="rename",
        rename_from="src/auth.py::login",
        entity_type="function",
        reasoning="Split auth.py into a package; login moved to session.py.",
    )


# ---------------------------------------------------------------------------
# metadata is a dict on every read surface
# ---------------------------------------------------------------------------


def test_metadata_is_a_dict_on_every_mcp_read_surface():
    """`BlameResult` declares `metadata: dict` and only `blame` honoured it.

    The documented rename-following idiom `event["metadata"]["renamed_from"]`
    worked on `blame` and raised `TypeError: string indices must be integers`
    on diff, history, search, changeset and prior_attempts.
    """
    from selvedge.server import blame, changeset, diff, history, search

    _seed_rename()
    path = "src/auth/session.py::login"

    assert isinstance(blame(path)["metadata"], dict)
    for row in diff(path) + history() + search("login"):
        assert isinstance(row["metadata"], dict), (
            f"metadata came back as {type(row['metadata']).__name__} on a "
            f"read surface: {row}"
        )

    # The idiom itself, on a non-blame surface.
    renamed = [r for r in diff(path) if r["metadata"].get("renamed_from")]
    assert renamed and renamed[0]["metadata"]["renamed_from"] == "src/auth.py::login"

    log_id = history()[0]["changeset_id"]
    if log_id:
        for row in changeset(log_id):
            assert isinstance(row["metadata"], dict)


def test_cli_blame_json_matches_the_mcp_tool(runner):
    """Key-for-key, including `metadata`'s type and the guaranteed `error`."""
    from selvedge.server import blame

    _seed_rename()
    path = "src/auth/session.py::login"

    assert _cli_json(runner, ["blame", path, "--json"]) == blame(path)


def test_cli_blame_json_matches_the_mcp_tool_on_a_miss(runner):
    """The miss is the common case, and it diverged hardest.

    The CLI emitted only `{"error": ...}` while the tool returns the full
    `BlameResult` shape — so a client written against the TypedDict and
    pointed at the CLI got a KeyError on every field.
    """
    from selvedge.server import blame

    cli_payload = _cli_json(runner, ["blame", "nope.missing", "--json"])
    tool_payload = blame("nope.missing")

    assert cli_payload == tool_payload
    assert cli_payload["error"]
    assert cli_payload["metadata"] == {}
    assert cli_payload["status"] == ""


# ---------------------------------------------------------------------------
# prior_attempts --fuzzy
# ---------------------------------------------------------------------------


def test_fuzzy_fallback_rows_are_well_formed():
    """The fallback note row had only a `note` key.

    So `for r in results: r["entity_path"]` — the obvious loop, and the one
    the tool's own description implies — raised KeyError on the first element
    whenever the semantic extra wasn't installed, which is the default
    install.
    """
    from selvedge.server import log_change, prior_attempts

    log_change(entity_path="users.card_token", change_type="add",
               reasoning="Tried storing card tokens on the user row.")
    log_change(entity_path="users.card_token", change_type="remove",
               reasoning="Reverted: storing card tokens put us in PCI scope.")

    rows = prior_attempts(entity_path="users.card_token",
                          fuzzy="tokenized payment credentials")
    assert rows, "expected at least the fallback note"
    for r in rows:
        assert "entity_path" in r, f"row is missing entity_path: {r}"
        assert "note" in r, f"row is missing the always-present note key: {r}"


def test_cli_fuzzy_json_matches_the_mcp_tool(runner):
    """Same list, same length. The CLI docstring claims exactly this."""
    from selvedge.server import log_change, prior_attempts

    log_change(entity_path="users.card_token", change_type="add",
               reasoning="Tried storing card tokens on the user row.")
    log_change(entity_path="users.card_token", change_type="remove",
               reasoning="Reverted: storing card tokens put us in PCI scope.")

    tool_rows = prior_attempts(entity_path="users.card_token",
                               fuzzy="tokenized payment credentials")
    cli_rows = _cli_json(runner, [
        "prior-attempts", "users.card_token",
        "--fuzzy", "tokenized payment credentials", "--json",
    ])
    assert cli_rows == tool_rows


# ---------------------------------------------------------------------------
# empty changeset
# ---------------------------------------------------------------------------


def test_cli_changeset_json_matches_the_mcp_tool_when_empty(runner):
    """CLI returned `[]`, the tool returned `[{"error": ...}]`."""
    from selvedge.server import changeset

    assert _cli_json(runner, ["changeset", "no-such-changeset", "--json"]) == \
        changeset("no-such-changeset")
