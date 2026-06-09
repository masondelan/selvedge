"""
Tests for the `selvedge.aggregates` library helper (v0.3.7).

``summary()`` is a pure, schema-versioned, templated roll-up — no MCP tool,
no CLI command, no LLM. These tests pin its shape and aggregation so the
v0.3.9 ``audit`` / ``digest`` consumers can rely on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from selvedge import aggregates
from selvedge.aggregates import SUMMARY_VERSION, Summary, summary
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage


@pytest.fixture
def storage(tmp_path: Path) -> SelvedgeStorage:
    s = SelvedgeStorage(tmp_path / "agg.db")
    s.log_event(ChangeEvent(entity_path="users.email", change_type="add", agent="claude-code", changeset_id="auth", project="api"))
    s.log_event(ChangeEvent(entity_path="users.email", change_type="modify", agent="claude-code", changeset_id="auth", project="api"))
    s.log_event(ChangeEvent(entity_path="users.phone", change_type="add", agent="cursor", changeset_id="2fa", project="api"))
    s.log_event(ChangeEvent(entity_path="payments.amount", change_type="add", agent="", project="web"))
    return s


def test_summary_is_schema_versioned(storage):
    result = summary(storage)
    assert isinstance(result, Summary)
    assert result.summary_version == SUMMARY_VERSION == 1


def test_total_events_counts_all(storage):
    assert summary(storage).total_events == 4


def test_changesets_and_agents_are_distinct_and_nonempty(storage):
    result = summary(storage)
    assert result.changesets == ["2fa", "auth"]
    # The empty-agent event is excluded; the rest are distinct + sorted.
    assert result.agents == ["claude-code", "cursor"]


def test_top_entities_ordered_by_activity(storage):
    result = summary(storage)
    top = result.top_entities
    assert top[0].entity_path == "users.email"
    assert top[0].event_count == 2
    # All three distinct entities are represented.
    assert {e.entity_path for e in top} == {"users.email", "users.phone", "payments.amount"}


def test_project_filter_scopes_the_window(storage):
    result = summary(storage, project="web")
    assert result.total_events == 1
    assert result.project == "web"
    assert {e.entity_path for e in result.top_entities} == {"payments.amount"}


def test_to_dict_is_json_shaped(storage):
    d = summary(storage).to_dict()
    assert d["summary_version"] == 1
    assert d["total_events"] == 4
    assert isinstance(d["top_entities"], list)
    assert d["top_entities"][0] == {"entity_path": "users.email", "event_count": 2}


def test_summary_is_exported_from_package():
    # Reachable as a library surface for the v0.3.9 CLI consumers.
    assert hasattr(aggregates, "summary")


def test_summary_empty_db_is_all_zero_never_null(tmp_path):
    # The edge a fresh-DB v0.3.9 consumer hits first: every field populated
    # with its empty value (0 / []), never None.
    empty = SelvedgeStorage(tmp_path / "empty.db")
    result = summary(empty)
    assert result.total_events == 0
    assert result.changesets == []
    assert result.agents == []
    assert result.top_entities == []
    d = result.to_dict()
    assert all(d[k] is not None for k in d)


def test_summary_since_filter_scopes_the_window(tmp_path):
    s = SelvedgeStorage(tmp_path / "since.db")
    s.log_event(ChangeEvent(entity_path="old.col", change_type="add", timestamp="2026-01-01T00:00:00Z"))
    s.log_event(ChangeEvent(entity_path="new.col", change_type="add", timestamp="2026-06-01T00:00:00Z"))
    scoped = summary(s, since="2026-03-01T00:00:00Z")
    assert scoped.total_events == 1
    assert {e.entity_path for e in scoped.top_entities} == {"new.col"}
    assert scoped.since == "2026-03-01T00:00:00Z"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
