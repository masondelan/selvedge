"""
Adversarial-input tests covering the issues called out in the v0.3.0 review.

These tests exist to lock in behavior that broke in subtle ways before:

  - search() treating '_' as a wildcard
  - --since '5m' interpreted as 5 months instead of 5 minutes
  - --since 'yesterday' silently returning empty results
  - CREATE TABLE columns that were never queryable via blame
  - mixed-timezone timestamps sorting wrong lexicographically
"""

import pytest
from pathlib import Path

from selvedge.importers import parse_sql_file
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage
from selvedge.timeutil import normalize_timestamp, parse_time_string


# ---------------------------------------------------------------------------
# search() and prefix matching must not treat '_' or '%' as wildcards
# ---------------------------------------------------------------------------


@pytest.fixture
def storage(tmp_path: Path) -> SelvedgeStorage:
    return SelvedgeStorage(tmp_path / "test.db")


def test_search_underscore_is_literal_not_wildcard(storage):
    """`stripe_customer_id` must not match `stripeXcustomerXid`."""
    storage.log_event(ChangeEvent(
        entity_path="users.stripe_customer_id", change_type="add",
        reasoning="Stripe billing"))
    storage.log_event(ChangeEvent(
        entity_path="users.stripeXcustomerXid", change_type="add",
        reasoning="unrelated"))

    rows = storage.search("stripe_customer_id")
    assert len(rows) == 1
    assert rows[0]["entity_path"] == "users.stripe_customer_id"


def test_search_percent_is_literal_not_wildcard(storage):
    """`100% off` shouldn't match every row in the DB."""
    storage.log_event(ChangeEvent(
        entity_path="config.discount_pct", change_type="add",
        reasoning="initial 100% off promo"))
    storage.log_event(ChangeEvent(
        entity_path="users.email", change_type="add",
        reasoning="auth feature"))

    rows = storage.search("100% off")
    assert len(rows) == 1
    assert rows[0]["entity_path"] == "config.discount_pct"


def test_prefix_query_underscore_isolation(storage):
    """`get_entity_history('user_tier_v2')` must not return rows for
    `userXtierXv2` etc. (the README's example column name)."""
    storage.log_event(ChangeEvent(
        entity_path="user_tier_v2", change_type="add",
        reasoning="real entity"))
    storage.log_event(ChangeEvent(
        entity_path="userXtierXv2", change_type="add",
        reasoning="should not match"))
    storage.log_event(ChangeEvent(
        entity_path="user_tier_v2.subfield", change_type="add",
        reasoning="prefix match — should match"))

    rows = storage.get_entity_history("user_tier_v2")
    paths = {r["entity_path"] for r in rows}
    assert paths == {"user_tier_v2", "user_tier_v2.subfield"}


def test_history_entity_filter_underscore_isolation(storage):
    """The entity_path filter on get_history must escape underscores too."""
    storage.log_event(ChangeEvent(entity_path="user_id", change_type="add"))
    storage.log_event(ChangeEvent(entity_path="userXid", change_type="add"))
    storage.log_event(ChangeEvent(entity_path="user_id.col", change_type="add"))

    rows = storage.get_history(entity_path="user_id")
    paths = {r["entity_path"] for r in rows}
    assert paths == {"user_id", "user_id.col"}


# ---------------------------------------------------------------------------
# Time parsing — 'm' is minutes, 'mo' is months
# ---------------------------------------------------------------------------


def test_parse_time_5m_is_minutes_not_months():
    """'5m' must mean 5 minutes ago (matching every other CLI convention).
    The earlier behavior interpreted '5m' as 5 months and silently
    returned five months of data when the user expected five minutes."""
    from datetime import datetime, timedelta, timezone
    result = parse_time_string("5m")
    parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    delta = datetime.now(timezone.utc) - parsed
    # Should be ~5 minutes, definitely not ~150 days
    assert delta < timedelta(minutes=10)
    assert delta > timedelta(seconds=30)  # not less than 5 minutes either


def test_parse_time_5mo_is_months():
    """'5mo' explicitly means months."""
    from datetime import datetime, timedelta, timezone
    result = parse_time_string("5mo")
    parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    delta = datetime.now(timezone.utc) - parsed
    # ~150 days
    assert timedelta(days=140) < delta < timedelta(days=160)


def test_parse_time_5mon_alias():
    from datetime import datetime, timedelta, timezone
    five_mo = parse_time_string("5mo")
    five_mon = parse_time_string("5mon")
    a = datetime.fromisoformat(five_mo.replace("Z", "+00:00"))
    b = datetime.fromisoformat(five_mon.replace("Z", "+00:00"))
    assert abs((a - b).total_seconds()) < 2


def test_parse_time_unparseable_raises():
    """`--since yesterday` (or any nonsense) must raise, not silently
    produce empty results from a string-vs-string lexicographic compare."""
    with pytest.raises(ValueError, match="could not parse"):
        parse_time_string("yesterday")
    with pytest.raises(ValueError):
        parse_time_string("last week")
    with pytest.raises(ValueError):
        parse_time_string("not a time")


def test_parse_time_iso_passes_through_normalized():
    """An ISO timestamp is parsed and normalized to UTC Z-suffix form."""
    result = parse_time_string("2025-06-01T10:00:00+05:00")
    # 10:00 +05:00 == 05:00 UTC
    assert result == "2025-06-01T05:00:00Z"


def test_parse_time_empty_raises():
    with pytest.raises(ValueError):
        parse_time_string("")
    with pytest.raises(ValueError):
        parse_time_string("   ")


def test_history_since_unparseable_returns_error_dict():
    """The MCP history tool returns an error rather than empty results."""
    import os
    os.environ["SELVEDGE_DB"] = "/tmp/__test_unparseable.db"
    Path("/tmp/__test_unparseable.db").unlink(missing_ok=True)
    import selvedge.server as srv
    srv._storage = None
    try:
        result = srv.history(since="yesterday")
        assert isinstance(result, list)
        assert len(result) == 1
        assert "error" in result[0]
        assert "yesterday" in result[0]["error"]
    finally:
        srv._storage = None
        Path("/tmp/__test_unparseable.db").unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# CREATE TABLE per-column events — blame works on inline columns
# ---------------------------------------------------------------------------


def test_create_table_blame_finds_inline_columns(tmp_path, storage):
    """The headline regression: import a CREATE TABLE, then blame a
    column defined only there. Before this fix the answer was always
    'No history found.'"""
    f = tmp_path / "0001_initial.sql"
    f.write_text("""
        CREATE TABLE users (
            id INTEGER NOT NULL,
            email VARCHAR(255) NOT NULL,
            stripe_customer_id VARCHAR(255)
        );
    """)
    events = parse_sql_file(f)
    storage.log_event_batch(events)

    blame = storage.get_blame("users.email")
    assert blame is not None
    assert blame["change_type"] == "add"
    assert blame["entity_type"] == "column"
    # The reasoning should point back to the source migration
    assert "users" in blame["reasoning"]
    assert "0001_initial.sql" in blame["reasoning"]


# ---------------------------------------------------------------------------
# Mixed-timezone timestamps — UTC normalization keeps order correct
# ---------------------------------------------------------------------------


def test_mixed_timezone_timestamps_sort_chronologically(storage):
    """Without UTC normalization, '...-08:00' sorts before '...+00:00'
    because '-' < '+' in ASCII, even when the +00:00 time is later
    in real life. After normalization both are stored as ...Z and
    chronological order matches lexicographic order."""
    # 09:00 PST = 17:00 UTC
    e1 = ChangeEvent(entity_path="users.email", change_type="add", reasoning="pst")
    e1.timestamp = "2025-01-01T09:00:00-08:00"
    storage.log_event(e1)

    # 10:00 UTC — earlier than e1's real time but lexicographically later
    # before normalization (because '+' > '-' in ASCII).
    e2 = ChangeEvent(entity_path="users.email", change_type="modify", reasoning="utc")
    e2.timestamp = "2025-01-01T10:00:00+00:00"
    storage.log_event(e2)

    rows = storage.get_entity_history("users.email")
    # Newest first: e1 (17:00 UTC) > e2 (10:00 UTC)
    assert rows[0]["reasoning"] == "pst"
    assert rows[1]["reasoning"] == "utc"


def test_normalize_timestamp_z_suffix():
    """All stored timestamps end with 'Z' for clean string ordering."""
    assert normalize_timestamp("2025-01-01T10:00:00+00:00") == "2025-01-01T10:00:00Z"
    assert normalize_timestamp("2025-01-01T10:00:00Z") == "2025-01-01T10:00:00Z"
    # Naive timestamps treated as UTC
    assert normalize_timestamp("2025-01-01T10:00:00") == "2025-01-01T10:00:00Z"
    # Offset is converted
    assert normalize_timestamp("2025-01-01T10:00:00-08:00") == "2025-01-01T18:00:00Z"


# ---------------------------------------------------------------------------
# ChangeEvent validation — reject empty entity_path, invalid change_type
# ---------------------------------------------------------------------------


def test_empty_entity_path_rejected():
    with pytest.raises(ValueError, match="entity_path"):
        ChangeEvent(entity_path="", change_type="add")
    with pytest.raises(ValueError, match="entity_path"):
        ChangeEvent(entity_path="   ", change_type="add")


def test_invalid_change_type_rejected():
    """Hallucinated change types and typos must not silently insert."""
    with pytest.raises(ValueError, match="invalid change_type"):
        ChangeEvent(entity_path="users.email", change_type="banana")
    with pytest.raises(ValueError, match="invalid change_type"):
        ChangeEvent(entity_path="users.email", change_type="modifyed")  # typo
    with pytest.raises(ValueError, match="invalid change_type"):
        ChangeEvent(entity_path="users.email", change_type="")


def test_invalid_entity_type_coerced_to_other():
    """Unknown entity_type is coerced rather than rejected — it's
    descriptive metadata, not load-bearing for queries."""
    e = ChangeEvent(entity_path="x", change_type="add", entity_type="totally_made_up")
    assert e.entity_type == "other"


def test_valid_change_type_enum_value_accepted():
    from selvedge.models import ChangeType, EntityType
    e = ChangeEvent(
        entity_path="x", change_type=ChangeType.ADD, entity_type=EntityType.COLUMN
    )
    assert e.change_type == "add"
    assert e.entity_type == "column"


# ---------------------------------------------------------------------------
# log_event_batch — atomic and faster than per-row inserts
# ---------------------------------------------------------------------------


def test_log_event_batch_persists_all(storage):
    events = [
        ChangeEvent(entity_path=f"t.col{i}", change_type="add",
                    reasoning=f"col {i}")
        for i in range(50)
    ]
    storage.log_event_batch(events)
    assert storage.count() == 50


def test_log_event_batch_empty_is_noop(storage):
    storage.log_event_batch([])
    assert storage.count() == 0


# ---------------------------------------------------------------------------
# count_missing_git_commit — surfaces unstamped events
# ---------------------------------------------------------------------------


def test_count_missing_git_commit(storage):
    storage.log_event(ChangeEvent(
        entity_path="a.x", change_type="add", git_commit="abc123"))
    storage.log_event(ChangeEvent(
        entity_path="b.y", change_type="add"))  # no commit
    storage.log_event(ChangeEvent(
        entity_path="c.z", change_type="add"))  # no commit

    assert storage.count_missing_git_commit() == 2


# ---------------------------------------------------------------------------
# get_db_path requires DB file, not just directory
# ---------------------------------------------------------------------------


def test_get_db_path_skips_empty_selvedge_dir(tmp_path, monkeypatch):
    """A stray empty .selvedge/ dir upstream must not hijack resolution.
    Walk-up should require the actual DB file to exist."""
    # Set up a directory tree: /tmp_path/proj/sub/, with an EMPTY
    # .selvedge dir at /tmp_path/ (no DB inside).
    (tmp_path / ".selvedge").mkdir()
    sub = tmp_path / "proj" / "sub"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    monkeypatch.delenv("SELVEDGE_DB", raising=False)
    monkeypatch.setenv("SELVEDGE_QUIET", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))

    # Reset the warned-fallback flag
    import selvedge.config as cfg
    cfg._warned_fallback = False

    resolved = cfg.get_db_path()
    # Should fall through to the global default, NOT the empty .selvedge/
    assert resolved == tmp_path / "fakehome" / ".selvedge" / "selvedge.db"


def test_get_db_path_finds_real_project_db(tmp_path, monkeypatch):
    """When a real project DB file exists upstream, walk-up finds it."""
    proj = tmp_path / "proj"
    sub = proj / "sub"
    sub.mkdir(parents=True)
    selvedge_dir = proj / ".selvedge"
    selvedge_dir.mkdir()
    db_file = selvedge_dir / "selvedge.db"
    db_file.write_bytes(b"")  # touch the file

    monkeypatch.chdir(sub)
    monkeypatch.delenv("SELVEDGE_DB", raising=False)

    import selvedge.config as cfg
    cfg._warned_fallback = False

    assert cfg.get_db_path() == db_file


# ---------------------------------------------------------------------------
# CLI: --since 'yesterday' exits with an error rather than empty results
# ---------------------------------------------------------------------------


def test_cli_history_since_unparseable_exits_error(tmp_path, monkeypatch):
    """`selvedge history --since yesterday` exits non-zero with an error
    message, not silently returning empty results."""
    from click.testing import CliRunner
    from selvedge.cli import cli

    monkeypatch.setenv("SELVEDGE_DB", str(tmp_path / "test.db"))
    runner = CliRunner()
    result = runner.invoke(cli, ["history", "--since", "yesterday"])
    assert result.exit_code != 0
    # Click's CliRunner merges stderr into result.output by default in
    # newer versions; check both.
    output = result.output + (result.stderr_bytes.decode() if result.stderr_bytes else "")
    assert "yesterday" in output or "could not parse" in output


# ---------------------------------------------------------------------------
# CLI: log rejects invalid change_type via click.Choice
# ---------------------------------------------------------------------------


def test_cli_log_rejects_invalid_change_type(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from selvedge.cli import cli

    monkeypatch.setenv("SELVEDGE_DB", str(tmp_path / "test.db"))
    runner = CliRunner()
    result = runner.invoke(cli, ["log", "users.email", "banana"])
    assert result.exit_code != 0
