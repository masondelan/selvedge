"""
Optional semantic recall over reasoning text (v0.3.9.1).

Entity-keyed lookup misses renames — ``payment_token`` vs ``card_token`` is
exactly the case where the agent has forgotten the canonical name, and
substring FTS only helps if the words happen to match (dev.to launch-thread
feedback: Dipankar Sarkar, Raffaele Zarrelli). This module adds an optional
local-embeddings index over event reasoning, consumed by
``prior_attempts --fuzzy``.

**Deliberately outside the core path.** Zero-LLM, zero-network core is a
stated design promise, so:

  - the embedding backend ships as an extra: ``pip install "selvedge[semantic]"``;
  - nothing here is imported by the core write/read paths — the CLI and MCP
    layers call in only when a fuzzy query is requested, and fall back to
    substring matching (with a one-line pointer) when the extra is absent;
  - the one network touch is the model download on the FIRST ``selvedge
    index`` run (cached under the Hugging Face cache dir after that);
    querying and re-indexing are fully local.

**Backend choice: model2vec static embeddings** (``potion-base-8M``). The
plan's criterion was "size and cold-start matter more than quality":
model2vec is a pure-Python lookup over pre-distilled static vectors — no
torch, no transformers, ~30 MB of weights, cold start well under a second —
versus sentence-transformers/MiniLM pulling a multi-GB torch stack with a
multi-second import. Static embeddings are weaker on long text, but the
corpus here is one-to-three-sentence reasoning strings, where they hold up.

Storage: an ``embeddings`` table created lazily in the same SQLite file
(``CREATE TABLE IF NOT EXISTS`` — a brand-new table, not an alteration, the
same posture ``path_migrations`` took in v0.3.7). Vectors are L2-normalized
float32 blobs, so similarity search is a dot product; the math below is
stdlib-only (``array``) on purpose — this module must import cleanly, and
fail politely, when the extra is missing.
"""

from __future__ import annotations

import hashlib
import sqlite3
from array import array
from collections.abc import Callable, Sequence
from math import sqrt
from pathlib import Path

from .storage import SelvedgeStorage

#: The default model2vec model. Distilled static embeddings, ~30 MB.
DEFAULT_MODEL = "minishlab/potion-base-8M"

#: Cosine-similarity floor for a fuzzy hit. Static embeddings put related
#: sentences comfortably above this; unrelated text lands well below.
DEFAULT_THRESHOLD = 0.35

#: One-line pointer shown by every fallback path.
INSTALL_HINT = (
    'semantic recall is an optional extra — pip install "selvedge[semantic]" '
    "and run `selvedge index` to enable fuzzy matching"
)

#: Signature for an embedding backend: texts in, one vector per text out.
#: The seam that lets the test suite (and any future backend) run without
#: model2vec installed.
EmbedFn = Callable[[list[str]], Sequence[Sequence[float]]]

CREATE_EMBEDDINGS_SQL = """
CREATE TABLE IF NOT EXISTS embeddings (
    event_id  TEXT PRIMARY KEY,
    model     TEXT NOT NULL,
    dim       INTEGER NOT NULL,
    text_hash TEXT NOT NULL,
    vector    BLOB NOT NULL
);
"""


class SemanticUnavailable(RuntimeError):
    """The semantic extra (model2vec) is not installed."""


class IndexMissing(RuntimeError):
    """No embeddings exist yet for the requested model — run ``selvedge index``."""


def is_available() -> bool:
    """True when the optional embedding backend can be imported."""
    try:
        import model2vec  # noqa: F401
    except ImportError:
        return False
    return True


def get_embedder(model_name: str = DEFAULT_MODEL) -> EmbedFn:
    """Return the model2vec embedding callable, or raise SemanticUnavailable.

    Split out as a module function so tests (and alternative backends) can
    monkeypatch it; everything downstream only sees the EmbedFn signature.
    """
    try:
        from model2vec import StaticModel
    except ImportError as e:
        raise SemanticUnavailable(INSTALL_HINT) from e
    try:
        model = StaticModel.from_pretrained(model_name)
    except Exception as e:  # noqa: BLE001 — loading can fail many ways
        # Offline machine, bad model name, corrupt cache — for callers this
        # is the same situation as "no backend": fall back, don't crash.
        raise SemanticUnavailable(
            f"embedding model {model_name!r} failed to load ({e}); "
            "fuzzy matching is unavailable"
        ) from e

    def embed(texts: list[str]) -> Sequence[Sequence[float]]:
        return model.encode(texts).tolist()

    return embed


# ---------------------------------------------------------------------------
# Vector plumbing — stdlib only
# ---------------------------------------------------------------------------


def _normalize(vec: Sequence[float]) -> array:
    """L2-normalize into a float32 array; zero vectors stay zero."""
    norm = sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return array("f", vec)
    return array("f", (x / norm for x in vec))


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(CREATE_EMBEDDINGS_SQL)
    return conn


# ---------------------------------------------------------------------------
# Index build
# ---------------------------------------------------------------------------


def build_index(
    storage: SelvedgeStorage,
    *,
    model_name: str = DEFAULT_MODEL,
    embed_fn: EmbedFn | None = None,
) -> dict:
    """Build or update the embeddings index from event reasoning text.

    Incremental: events whose reasoning already has an embedding under the
    same model and text hash are skipped, so re-running after new events is
    cheap. Events with empty reasoning are never indexed (there is nothing
    semantic to match). Switching models re-embeds on the next run.

    Returns a report dict — ``indexed`` (new/updated), ``skipped``
    (unchanged), ``total_indexed`` (rows now in the index), ``model``.
    Every field always populated.
    """
    embed = embed_fn or get_embedder(model_name)

    conn = _connect(storage.db_path)
    try:
        events = conn.execute(
            "SELECT id, reasoning FROM events WHERE reasoning != ''"
        ).fetchall()
        existing = {
            r["event_id"]: r["text_hash"]
            for r in conn.execute(
                "SELECT event_id, text_hash FROM embeddings WHERE model = ?",
                (model_name,),
            ).fetchall()
        }

        pending = [
            (row["id"], row["reasoning"])
            for row in events
            if existing.get(row["id"]) != _text_hash(row["reasoning"])
        ]

        if pending:
            vectors = embed([reasoning for _, reasoning in pending])
            upserts = []
            for (event_id, reasoning), vec in zip(pending, vectors, strict=True):
                normalized = _normalize(vec)
                upserts.append(
                    (
                        event_id,
                        model_name,
                        len(normalized),
                        _text_hash(reasoning),
                        normalized.tobytes(),
                    )
                )
            conn.executemany(
                "INSERT INTO embeddings (event_id, model, dim, text_hash, vector) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(event_id) DO UPDATE SET model=excluded.model, "
                "dim=excluded.dim, text_hash=excluded.text_hash, "
                "vector=excluded.vector",
                upserts,
            )
            conn.commit()

        total = conn.execute(
            "SELECT COUNT(*) FROM embeddings WHERE model = ?", (model_name,)
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "model": model_name,
        "indexed": len(pending),
        "skipped": len(events) - len(pending),
        "total_indexed": total,
    }


# ---------------------------------------------------------------------------
# Fuzzy search
# ---------------------------------------------------------------------------


def fuzzy_events(
    storage: SelvedgeStorage,
    query: str,
    *,
    model_name: str = DEFAULT_MODEL,
    threshold: float = DEFAULT_THRESHOLD,
    limit: int = 20,
    embed_fn: EmbedFn | None = None,
) -> list[dict]:
    """Events whose reasoning is semantically similar to ``query``.

    Returns event dicts (nullable columns coalesced) with two extra
    always-present keys: ``similarity`` (cosine, rounded to 3 places) and
    ``match_type="fuzzy"``. Sorted best-match first, floored at
    ``threshold``, capped at ``limit``. Raises :class:`IndexMissing` when no
    embeddings exist for the model.
    """
    conn = _connect(storage.db_path)
    try:
        rows = conn.execute(
            "SELECT event_id, dim, vector FROM embeddings WHERE model = ?",
            (model_name,),
        ).fetchall()
        if not rows:
            # Checked BEFORE touching the embedding backend: loading the
            # model can hit the network (first-run download), and a query
            # against a machine with no index must stay fully local.
            raise IndexMissing(
                "no embeddings index yet — run `selvedge index` first"
            )
        embed = embed_fn or get_embedder(model_name)
        query_vec = _normalize(embed([query])[0])

        scored: list[tuple[float, str]] = []
        for r in rows:
            vec = array("f")
            vec.frombytes(r["vector"])
            if len(vec) != len(query_vec):
                continue  # stale row from a different-dim model; reindex fixes
            sim = sum(a * b for a, b in zip(query_vec, vec, strict=True))
            if sim >= threshold:
                scored.append((sim, r["event_id"]))
        scored.sort(reverse=True)
        scored = scored[:limit]
        if not scored:
            return []

        placeholders = ",".join("?" for _ in scored)
        event_rows = {
            r["id"]: dict(r)
            for r in conn.execute(
                f"SELECT * FROM events WHERE id IN ({placeholders})",
                [event_id for _, event_id in scored],
            ).fetchall()
        }
    finally:
        conn.close()

    from .storage import _coalesce_event_nullables

    results: list[dict] = []
    for sim, event_id in scored:
        event = event_rows.get(event_id)
        if event is None:
            continue  # embedding outlived its event (defensive; no delete path yet)
        annotated = _coalesce_event_nullables(dict(event))
        annotated["similarity"] = round(sim, 3)
        annotated["match_type"] = "fuzzy"
        results.append(annotated)
    return results


def fuzzy_prior_attempts(
    storage: SelvedgeStorage,
    query: str,
    *,
    min_confidence: str = "proximity_high",
    window_minutes: int = 10080,
    limit: int = 20,
    exclude_paths: set[str] | None = None,
    exclude_ids: set[str] | None = None,
    model_name: str = DEFAULT_MODEL,
    threshold: float = DEFAULT_THRESHOLD,
    embed_fn: EmbedFn | None = None,
) -> list[dict]:
    """Prior attempts on entities found via semantic similarity to ``query``.

    The rename-recall path: a fuzzy sweep finds events whose reasoning is
    similar to the description, then each DISTINCT entity path among the
    hits is run through the normal ``get_prior_attempts`` outcome pipeline —
    so the caller gets the same annotated attempt rows (outcome /
    confidence / trail fields), each additionally labeled
    ``match_type="fuzzy"`` with the entity's best ``similarity``.

    Dedupe happens at the EVENT level, not just the path level:
    ``get_prior_attempts`` expands dotted prefixes (a ``users`` lookup also
    returns ``users.card_token`` attempts), so path-only exclusion would
    let the same event appear once as exact and once as fuzzy, or twice
    across overlapping hit paths. ``exclude_ids`` drops events the caller
    already holds; hits are also deduped against each other by event id.
    ``exclude_paths`` remains as a cheap pre-filter. Raises
    :class:`SemanticUnavailable` / :class:`IndexMissing` like
    :func:`fuzzy_events` — callers fall back to substring matching.
    """
    hits = fuzzy_events(
        storage,
        query,
        model_name=model_name,
        threshold=threshold,
        limit=max(limit * 3, 30),  # hits collapse into fewer distinct paths
        embed_fn=embed_fn,
    )
    exclude = exclude_paths or set()
    seen_ids = set(exclude_ids or set())
    best_similarity: dict[str, float] = {}
    for hit in hits:
        path = hit["entity_path"]
        if path in exclude:
            continue
        best_similarity.setdefault(path, hit["similarity"])

    results: list[dict] = []
    for path, similarity in list(best_similarity.items())[:limit]:
        for attempt in storage.get_prior_attempts(
            entity_path=path,
            min_confidence=min_confidence,
            window_minutes=window_minutes,
            limit=limit,
        ):
            if attempt["id"] in seen_ids:
                continue
            seen_ids.add(attempt["id"])
            attempt["match_type"] = "fuzzy"
            attempt["similarity"] = similarity
            results.append(attempt)

    results.sort(key=lambda r: (-r["similarity"], r["timestamp"]))
    return results[:limit]
