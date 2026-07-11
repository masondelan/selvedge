"""
Tests for the optional semantic recall layer (v0.3.9.1).

Everything here runs WITHOUT the selvedge[semantic] extra installed — that
is itself the headline contract ("core tests must pass without the extra").
A deterministic bag-of-words embedder stands in for model2vec through the
``embed_fn`` seam / ``get_embedder`` monkeypatch, so the index and search
mechanics are exercised for real while the backend stays out of the loop.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from selvedge import semantic
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage

# A tiny fixed vocabulary makes cosine similarity fully predictable:
# reasoning strings sharing words score high, disjoint ones score 0.
_VOCAB = (
    "token", "tokens", "card", "payment", "payments", "credentials",
    "vault", "email", "auth", "login", "index", "cache", "schema",
)


def fake_embed(texts: list[str]) -> list[list[float]]:
    return [
        [float(text.lower().count(word)) for word in _VOCAB] for text in texts
    ]


@pytest.fixture
def storage(tmp_path: Path) -> SelvedgeStorage:
    return SelvedgeStorage(tmp_path / "semantic.db")


def _ev(storage, path, change_type, ts, reasoning=""):
    return storage.log_event(
        ChangeEvent(
            entity_path=path, change_type=change_type,
            timestamp=ts, reasoning=reasoning,
        )
    )


def _seed_card_token_revert(storage):
    _ev(
        storage, "users.card_token", "add", "2026-01-01T00:00:00Z",
        "Store tokenized card credentials for faster payment flows.",
    )
    _ev(
        storage, "users.card_token", "remove", "2026-01-02T00:00:00Z",
        "Reverted: card tokens belong in the provider vault, not our DB.",
    )


# ---------------------------------------------------------------------------
# build_index
# ---------------------------------------------------------------------------


def test_build_index_embeds_reasoning_rows(storage):
    _seed_card_token_revert(storage)
    _ev(storage, "misc.thing", "add", "2026-01-03T00:00:00Z", "")  # no reasoning

    report = semantic.build_index(storage, embed_fn=fake_embed)
    assert report["indexed"] == 2  # the two reasoning-carrying events
    assert report["skipped"] == 0
    assert report["total_indexed"] == 2
    assert report["model"] == semantic.DEFAULT_MODEL


def test_build_index_is_incremental(storage):
    _seed_card_token_revert(storage)
    semantic.build_index(storage, embed_fn=fake_embed)

    second = semantic.build_index(storage, embed_fn=fake_embed)
    assert second["indexed"] == 0
    assert second["skipped"] == 2

    _ev(storage, "users.email", "add", "2026-01-05T00:00:00Z", "Email for auth login.")
    third = semantic.build_index(storage, embed_fn=fake_embed)
    assert third["indexed"] == 1
    assert third["total_indexed"] == 3


# ---------------------------------------------------------------------------
# fuzzy_events / fuzzy_prior_attempts
# ---------------------------------------------------------------------------


def test_fuzzy_events_ranks_similar_reasoning(storage):
    _seed_card_token_revert(storage)
    _ev(storage, "cache.layer", "add", "2026-01-04T00:00:00Z",
        "Added an index cache for the schema registry.")
    semantic.build_index(storage, embed_fn=fake_embed)

    hits = semantic.fuzzy_events(
        storage, "tokenized payment credentials", embed_fn=fake_embed
    )
    assert hits, "expected at least one fuzzy hit"
    assert hits[0]["entity_path"] == "users.card_token"
    assert hits[0]["match_type"] == "fuzzy"
    assert 0.0 < hits[0]["similarity"] <= 1.0
    # The unrelated cache event shares no vocabulary — below threshold.
    assert all(h["entity_path"] != "cache.layer" for h in hits)


def test_fuzzy_events_raises_index_missing(storage):
    _seed_card_token_revert(storage)
    with pytest.raises(semantic.IndexMissing):
        semantic.fuzzy_events(storage, "anything", embed_fn=fake_embed)


def test_fuzzy_prior_attempts_finds_renamed_entity(storage):
    """The rename-recall scenario: the agent asks about 'payment tokens'
    while history lives under users.card_token."""
    _seed_card_token_revert(storage)
    semantic.build_index(storage, embed_fn=fake_embed)

    rows = semantic.fuzzy_prior_attempts(
        storage, "tokenized payment credentials", embed_fn=fake_embed
    )
    assert rows
    attempt = rows[0]
    assert attempt["entity_path"] == "users.card_token"
    # Full outcome pipeline applies to fuzzy hits too.
    assert attempt["outcome"] == "reverted"
    assert "provider vault" in attempt["outcome_reasoning"]
    assert attempt["match_type"] == "fuzzy"
    assert attempt["similarity"] > 0.0


def test_fuzzy_prior_attempts_respects_exclude_paths(storage):
    _seed_card_token_revert(storage)
    semantic.build_index(storage, embed_fn=fake_embed)
    rows = semantic.fuzzy_prior_attempts(
        storage,
        "tokenized payment credentials",
        embed_fn=fake_embed,
        exclude_paths={"users.card_token"},
    )
    assert rows == []


def test_get_embedder_raises_without_extra(monkeypatch):
    # Simulate the extra being absent even if it happens to be installed.
    monkeypatch.setitem(sys.modules, "model2vec", None)
    assert semantic.is_available() is False
    with pytest.raises(semantic.SemanticUnavailable, match="selvedge\\[semantic\\]"):
        semantic.get_embedder()


# ---------------------------------------------------------------------------
# CLI — `selvedge index` + `prior-attempts --fuzzy`
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SELVEDGE_DB", str(tmp_path / "cli-semantic.db"))
    import selvedge.server as srv
    srv._storage = None
    yield
    srv._storage = None


def _cli_storage():
    from selvedge.config import get_db_path

    return SelvedgeStorage(get_db_path())


def test_cli_index_points_at_extra_when_missing(runner, monkeypatch):
    from selvedge.cli import cli

    monkeypatch.setattr(semantic, "is_available", lambda: False)
    result = runner.invoke(cli, ["index"])
    assert result.exit_code == 1
    assert 'selvedge[semantic]' in result.output


def test_cli_index_builds_with_backend(runner, monkeypatch):
    from selvedge.cli import cli

    _seed_card_token_revert(_cli_storage())
    monkeypatch.setattr(semantic, "is_available", lambda: True)
    monkeypatch.setattr(semantic, "get_embedder", lambda model_name: fake_embed)
    result = runner.invoke(cli, ["index", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["indexed"] == 2


def test_cli_fuzzy_labels_matches(runner, monkeypatch):
    from selvedge.cli import cli

    storage = _cli_storage()
    _seed_card_token_revert(storage)
    monkeypatch.setattr(semantic, "get_embedder", lambda model_name: fake_embed)
    semantic.build_index(storage, embed_fn=fake_embed)

    result = runner.invoke(
        cli, ["prior-attempts", "--fuzzy", "tokenized payment credentials", "--json"]
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert rows and rows[0]["match_type"] == "fuzzy"
    assert rows[0]["entity_path"] == "users.card_token"


def test_cli_fuzzy_falls_back_to_substring_with_pointer(runner, monkeypatch):
    from selvedge.cli import cli

    storage = _cli_storage()
    _seed_card_token_revert(storage)
    monkeypatch.setitem(sys.modules, "model2vec", None)

    result = runner.invoke(
        cli, ["prior-attempts", "--fuzzy", "card tokens"]
    )
    assert result.exit_code == 0, result.output
    assert "selvedge[semantic]" in result.output  # the one-line pointer
    # Substring fallback still finds the record ("card tokens" appears in
    # the revert reasoning).
    assert "users.card_token" in result.output


def test_cli_fuzzy_index_missing_falls_back(runner, monkeypatch):
    """Extra present but `selvedge index` never ran — same graceful path."""
    from selvedge.cli import cli

    _seed_card_token_revert(_cli_storage())
    monkeypatch.setattr(semantic, "get_embedder", lambda model_name: fake_embed)
    result = runner.invoke(cli, ["prior-attempts", "--fuzzy", "card tokens"])
    assert result.exit_code == 0, result.output
    assert "selvedge index" in result.output
    assert "users.card_token" in result.output


# ---------------------------------------------------------------------------
# MCP tool — fuzzy param + fallback note row
# ---------------------------------------------------------------------------


def test_mcp_prior_attempts_fuzzy(monkeypatch):
    from selvedge.server import log_change, prior_attempts

    log_change(
        entity_path="users.card_token",
        change_type="add",
        reasoning="Store tokenized card credentials for faster payment flows.",
    )
    log_change(
        entity_path="users.card_token",
        change_type="remove",
        reasoning="Reverted: card tokens belong in the provider vault, not our DB.",
    )
    monkeypatch.setattr(semantic, "get_embedder", lambda model_name: fake_embed)
    from selvedge.config import get_db_path

    semantic.build_index(SelvedgeStorage(get_db_path()), embed_fn=fake_embed)

    rows = prior_attempts(fuzzy="tokenized payment credentials")
    assert rows and rows[0]["match_type"] == "fuzzy"
    assert rows[0]["outcome"] == "reverted"


def test_mcp_prior_attempts_fuzzy_fallback_note(monkeypatch):
    from selvedge.server import log_change, prior_attempts

    log_change(
        entity_path="users.card_token",
        change_type="add",
        reasoning="Store tokenized card credentials for faster payment flows.",
    )
    log_change(
        entity_path="users.card_token",
        change_type="remove",
        reasoning="Reverted: card tokens belong in the provider vault, not our DB.",
    )
    monkeypatch.setitem(sys.modules, "model2vec", None)

    rows = prior_attempts(fuzzy="card tokens")
    assert "note" in rows[0]
    assert "substring" in rows[0]["note"]
    assert any(r.get("entity_path") == "users.card_token" for r in rows[1:])


def test_mcp_prior_attempts_requires_some_input():
    from selvedge.server import prior_attempts

    rows = prior_attempts()
    assert "error" in rows[0]


# ---------------------------------------------------------------------------
# Core-without-extra guarantee
# ---------------------------------------------------------------------------


def test_core_imports_never_pull_the_semantic_backend():
    """`import selvedge` + cli + server must not import model2vec (or even
    selvedge.semantic) — the extra is opt-in at the moment a fuzzy query or
    `selvedge index` actually runs."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, selvedge, selvedge.cli, selvedge.server; "
            "print('model2vec' in sys.modules, 'selvedge.semantic' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False False", result.stdout


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
