"""Selvedge ↔ Agent Trace v0.1.0 interop.

`Agent Trace <https://agent-trace.dev/>`_ is an open AI code-attribution wire
format published by Cursor (RFC, Jan 2026). Its original GitHub home
(``github.com/cursor/agent-trace``) went 404 as of 2026-08-10; the spec text and
schema survive at agent-trace.dev, frozen at v0.1.0. Selvedge targets that
frozen format: it captures everything an Agent Trace record needs (file/line
attribution tied to an AI contributor) plus reasoning and entity-level
provenance, so it can emit and read Agent Trace records without any runtime
dependency on the upstream project.

This module is a pure, deterministic, dependency-free converter — no LLM, no
I/O. The CLI (``selvedge export --format agent-trace`` /
``selvedge import --format agent-trace``) is a thin shell over it.

Mapping summary (``ChangeEvent`` dict → Agent Trace v0.1.0 record):

============================  =========================================
ChangeEvent field             Agent Trace target
============================  =========================================
``id``                        top-level ``id`` (already a UUID)
``timestamp``                 top-level ``timestamp``
``git_commit``                ``vcs.revision`` (``vcs.type = "git"``)
*(producer identity)*         ``tool = {name: "selvedge", version}``
``agent``                     ``files[].conversations[].contributor.type``
                              (``ai``/``unknown``); name kept in metadata
``entity_path`` *(file)*      ``files[].path`` (``path::symbol`` → ``path``)
``diff``                      ``files[].conversations[].ranges`` (hunks)
``entity_path`` *(non-file)*  ``metadata["dev.selvedge"].entity`` only
everything else               ``metadata["dev.selvedge"].*``
============================  =========================================

Fidelity note: column / env-var / dependency events and migration-imported
events have no line range, so the converter emits ``range_unknown: true`` in
``metadata["dev.selvedge"]`` rather than fabricating a ``[1, 1]`` placeholder.
See :func:`export_preamble`.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from ..models import VALID_CHANGE_TYPES

# Pinned to the Agent Trace spec version this module targets.
AGENT_TRACE_VERSION = "0.1.0"

# Reverse-domain namespace for Selvedge-specific data carried inside an Agent
# Trace record's ``metadata`` object (the spec's recommended convention).
SELVEDGE_NS = "dev.selvedge"

# Stable namespace for deriving record IDs from non-UUID inputs.
_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://selvedge.sh/agent-trace")

# Entity types whose ``entity_path`` denotes (or contains) a real file path.
_FILE_ENTITY_TYPES = frozenset({"file", "function", "class"})

# Unified-diff hunk header: capture the new-file start line and (optional) count.
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)

# Agent Trace contributor types.
_CONTRIBUTOR_TYPES = frozenset({"human", "ai", "mixed", "unknown"})


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _selvedge_version() -> str:
    """Return the installed Selvedge version (lazy import avoids a cycle)."""
    try:
        from selvedge import __version__

        return __version__
    except Exception:  # pragma: no cover - defensive
        return "0"


def _metadata_value(raw: Any) -> Any:
    """Parse a ChangeEvent ``metadata`` value (JSON string or dict) to its value.

    Preserves the original JSON type (dict, list, scalar) so a round-trip never
    rewraps a list as ``{"value": [...]}``. Returns ``None`` for empty or
    unparseable input so the caller can simply omit the key.
    """
    if isinstance(raw, dict):
        return raw
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _record_id(raw: Any) -> str:
    """Coerce a ChangeEvent id to a UUID string (deterministic if not already one)."""
    try:
        return str(uuid.UUID(str(raw)))
    except (ValueError, AttributeError, TypeError):
        return str(uuid.uuid5(_ID_NAMESPACE, str(raw)))


def _is_file_entity(entity_type: str, entity_path: str) -> bool:
    """Whether the entity maps onto a repo file path."""
    if entity_type in _FILE_ENTITY_TYPES:
        return True
    # Heuristic fallback for events whose entity_type is "other" but whose path
    # is clearly a file (contains a path separator and a dotted extension).
    return "::" in entity_path or bool(re.search(r"/[^/]+\.[A-Za-z0-9]+$", entity_path))


def _file_path(entity_path: str) -> str:
    """Strip a ``::symbol`` suffix to get the bare file path."""
    return entity_path.split("::", 1)[0]


def extract_line_ranges(diff: str) -> list[dict]:
    """Extract new-file line ranges from a unified diff's hunk headers.

    Returns a list of ``{"start_line": int, "end_line": int}`` (both >= 1), one
    per ``@@ -a,b +c,d @@`` hunk. Returns ``[]`` when the diff has no parseable
    hunks (e.g. raw SQL DDL from ``selvedge import``).
    """
    ranges: list[dict] = []
    for match in _HUNK_RE.finditer(diff or ""):
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        if start < 1 or count < 1:
            continue
        ranges.append({"start_line": start, "end_line": start + count - 1})
    return ranges


def _contributor(agent: str) -> dict:
    """Build an Agent Trace contributor object from a Selvedge agent name.

    Selvedge stores the agent/tool name (``claude-code``, ``cursor``), not a
    models.dev model id, so ``model_id`` is intentionally omitted rather than
    fabricated. An empty agent is reported as ``unknown``.
    """
    return {"type": "ai" if agent else "unknown"}


def _selvedge_meta(event: dict) -> dict:
    """The ``metadata["dev.selvedge"]`` payload for one event (lossless extras)."""
    extra = _metadata_value(event.get("metadata"))
    meta: dict[str, Any] = {
        "entity": event.get("entity_path", ""),
        "entity_type": event.get("entity_type", ""),
        "change_type": event.get("change_type", ""),
        "reasoning": event.get("reasoning", ""),
        "agent": event.get("agent", ""),
        "session_id": event.get("session_id", ""),
        "changeset_id": event.get("changeset_id", ""),
        "project": event.get("project", ""),
    }
    # Preserve the original metadata value (any JSON type) but skip empties.
    if extra is not None and extra != {}:
        meta["metadata"] = extra
    return meta


def _conversation(event: dict, ranges: list[dict]) -> dict:
    """Build a conversation object linking the contributor to its line ranges."""
    session_id = event.get("session_id", "")
    ref = (
        f"urn:selvedge:session:{session_id}"
        if session_id
        else f"urn:selvedge:event:{_record_id(event.get('id'))}"
    )
    conv: dict[str, Any] = {
        "url": ref,
        "contributor": _contributor(event.get("agent", "")),
        "ranges": ranges,
    }
    changeset_id = event.get("changeset_id", "")
    if changeset_id:
        conv["related"] = [
            {"type": "changeset", "url": f"urn:selvedge:changeset:{changeset_id}"}
        ]
    return conv


# ---------------------------------------------------------------------------
# ChangeEvent -> Agent Trace
# ---------------------------------------------------------------------------


def event_to_trace_record(event: dict, *, tool_version: str | None = None) -> dict:
    """Convert one ChangeEvent dict to an Agent Trace v0.1.0 trace record.

    ``event`` is a dict shaped like :meth:`selvedge.ChangeEvent.to_dict`.
    """
    tool_version = tool_version or _selvedge_version()
    entity_path = event.get("entity_path", "")
    entity_type = event.get("entity_type", "")
    diff = event.get("diff", "")

    record: dict[str, Any] = {
        "version": AGENT_TRACE_VERSION,
        "id": _record_id(event.get("id")),
        "timestamp": event.get("timestamp", ""),
        "tool": {"name": "selvedge", "version": tool_version},
        "files": [],
    }

    git_commit = event.get("git_commit", "")
    if git_commit:
        record["vcs"] = {"type": "git", "revision": git_commit}

    selvedge_meta = _selvedge_meta(event)

    if _is_file_entity(entity_type, entity_path):
        ranges = extract_line_ranges(diff)
        record["files"] = [
            {"path": _file_path(entity_path), "conversations": [_conversation(event, ranges)]}
        ]
        selvedge_meta["range_unknown"] = not ranges
    else:
        # Non-file entities (column, env var, dependency, route, …) have no path
        # Agent Trace can represent. Keep them lossless in metadata; AT-only
        # readers see an empty files[] rather than a fabricated path.
        selvedge_meta["range_unknown"] = True

    record["metadata"] = {SELVEDGE_NS: selvedge_meta}
    return record


def _collapsed_record(events: list[dict], tool_version: str) -> dict:
    """Merge events sharing a session into one record (``--collapse-by-session``)."""
    first = events[0]
    session_id = first.get("session_id", "")
    record: dict[str, Any] = {
        "version": AGENT_TRACE_VERSION,
        "id": str(uuid.uuid5(_ID_NAMESPACE, f"session:{session_id}")),
        "timestamp": min(e.get("timestamp", "") for e in events),
        "tool": {"name": "selvedge", "version": tool_version},
        "files": [],
    }
    for event in events:
        commit = event.get("git_commit", "")
        if commit:
            record["vcs"] = {"type": "git", "revision": commit}
            break

    files_by_path: dict[str, dict] = {}
    per_event_meta: list[dict] = []
    for event in events:
        per_event_meta.append(_selvedge_meta(event))
        entity_path = event.get("entity_path", "")
        if _is_file_entity(event.get("entity_type", ""), entity_path):
            path = _file_path(entity_path)
            ranges = extract_line_ranges(event.get("diff", ""))
            entry = files_by_path.setdefault(path, {"path": path, "conversations": []})
            entry["conversations"].append(_conversation(event, ranges))

    record["files"] = list(files_by_path.values())
    record["metadata"] = {
        SELVEDGE_NS: {
            "session_id": session_id,
            "collapsed": True,
            "event_count": len(events),
            "events": per_event_meta,
        }
    }
    return record


def events_to_trace_records(
    events: list[dict],
    *,
    collapse_by_session: bool = False,
    tool_version: str | None = None,
) -> list[dict]:
    """Convert a list of ChangeEvent dicts to Agent Trace records.

    Default is 1:1 (one record per event). With ``collapse_by_session=True``,
    events sharing a non-empty ``session_id`` are merged into a single record
    (emitted at the position of the session's first event); events without a
    session stay 1:1.
    """
    tool_version = tool_version or _selvedge_version()
    if not collapse_by_session:
        return [event_to_trace_record(e, tool_version=tool_version) for e in events]

    sessions: dict[str, list[dict]] = {}
    order: list[tuple[str, str]] = []  # (kind, key) preserving first-seen order
    singles: dict[str, dict] = {}
    for idx, event in enumerate(events):
        sid = event.get("session_id") or ""
        if sid:
            if sid not in sessions:
                sessions[sid] = []
                order.append(("session", sid))
            sessions[sid].append(event)
        else:
            key = f"single:{idx}"
            singles[key] = event
            order.append(("single", key))

    records: list[dict] = []
    for kind, key in order:
        if kind == "session":
            records.append(_collapsed_record(sessions[key], tool_version))
        else:
            records.append(event_to_trace_record(singles[key], tool_version=tool_version))
    return records


def export_preamble(tool_version: str | None = None) -> dict:
    """A non-record header describing the producer and its fidelity profile.

    Emitted as the first element of the JSON-array export so Agent Trace
    consumers know that ``range_unknown: true`` records are a truthful
    representation of entity-level / migration-imported events, not a
    low-fidelity producer.
    """
    tool_version = tool_version or _selvedge_version()
    return {
        SELVEDGE_NS: {
            "producer": f"selvedge {tool_version}",
            "agent_trace_version": AGENT_TRACE_VERSION,
            "note": (
                "Selvedge emits one Agent Trace record per change event. Records "
                "with metadata.dev.selvedge.range_unknown=true are entity-level "
                "(DB column, env var, dependency) or migration-imported events "
                "that genuinely have no line range — not low-fidelity output."
            ),
        }
    }


# ---------------------------------------------------------------------------
# Agent Trace -> ChangeEvent  (best-effort import)
# ---------------------------------------------------------------------------


def load_trace_records(text: str) -> list[dict]:
    """Parse Agent Trace records from any shape Selvedge's export can emit.

    Accepts: a Selvedge bundle object (``{"records": [...]}``), a bare JSON
    array of records, a single record object (has ``version`` + ``files``), or
    NDJSON (one record per line). Returns the list of record dicts.
    """
    stripped = text.strip()
    if not stripped:
        return []
    try:
        parsed = json.loads(stripped)
    except ValueError:
        # NDJSON fallback: one JSON record per non-empty line.
        records = []
        for line in stripped.splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
        return records
    if isinstance(parsed, dict):
        if isinstance(parsed.get("records"), list):
            return parsed["records"]
        return [parsed]
    if isinstance(parsed, list):
        return parsed
    return []


def _coerce_change_type(raw: Any) -> str:
    """Map a change_type to a valid one, defaulting unknown values to ``modify``.

    Mirrors ChangeEvent's tolerance: a foreign/hand-edited record with an
    unknown verb (``"edit"``) imports as ``modify`` instead of being silently
    dropped when ChangeEvent rejects it.
    """
    ct = raw or "modify"
    return ct if ct in VALID_CHANGE_TYPES else "modify"


def _event_from_selvedge_meta(record: dict, sv: dict, idx: int | None = None) -> dict | None:
    """Reconstruct one ChangeEvent dict from a ``dev.selvedge`` per-event block."""
    entity_path = sv.get("entity", "")
    if not entity_path:
        return None
    base_id = record.get("id")
    rid = _record_id(f"{base_id}:{idx}") if idx is not None else _record_id(base_id)
    mv = sv.get("metadata")
    return {
        "id": rid,
        "timestamp": record.get("timestamp", ""),
        "entity_type": sv.get("entity_type") or "other",
        "entity_path": entity_path,
        "change_type": _coerce_change_type(sv.get("change_type")),
        "diff": "",
        "reasoning": sv.get("reasoning", ""),
        "agent": sv.get("agent", ""),
        "session_id": sv.get("session_id", ""),
        "git_commit": (record.get("vcs") or {}).get("revision", ""),
        "project": sv.get("project", ""),
        "changeset_id": sv.get("changeset_id", ""),
        "metadata": json.dumps(mv) if mv is not None else "{}",
    }


def trace_record_to_events(record: dict) -> list[dict]:
    """Convert one Agent Trace record to zero or more ChangeEvent dicts.

    - A Selvedge **collapsed** record (`--collapse-by-session` export, marked
      ``metadata.dev.selvedge.collapsed``) fans back out to one event per entry
      in its ``events`` list — so a collapsed export round-trips losslessly.
    - A single Selvedge record (``metadata.dev.selvedge.entity``) yields one
      lossless event.
    - A **foreign** record yields one event per file in ``files[]`` (Agent Trace
      is a file-array format), each ``change_type="modify"`` with empty
      reasoning and an ``ai``/empty agent from that file's contributor.

    Every reconstructed event gets a deterministic id derived from the record id
    and the entity/file, so an id-less or multi-file record can't collide on the
    primary key when imported. Returns ``[]`` for a record with no usable entity.
    """
    meta = record.get("metadata") or {}
    sv = meta.get(SELVEDGE_NS) if isinstance(meta, dict) else None

    # Collapsed Selvedge record → fan out to its per-event metadata.
    if isinstance(sv, dict) and sv.get("collapsed") and isinstance(sv.get("events"), list):
        events: list[dict] = []
        for i, em in enumerate(sv["events"]):
            if not isinstance(em, dict):
                continue
            ev = _event_from_selvedge_meta(record, em, idx=i)
            if ev is not None:
                ev["session_id"] = ev["session_id"] or sv.get("session_id", "")
                events.append(ev)
        return events

    # Single Selvedge record → one lossless event.
    if isinstance(sv, dict) and sv.get("entity"):
        ev = _event_from_selvedge_meta(record, sv)
        return [ev] if ev is not None else []

    # Foreign record → one event per file (file-array format).
    foreign: list[dict] = []
    base_id = record.get("id")
    commit = (record.get("vcs") or {}).get("revision", "")
    for f in record.get("files") or []:
        if not (isinstance(f, dict) and f.get("path")):
            continue
        path = f["path"]
        contributor_type = ""
        for conv in f.get("conversations") or []:
            ctype = (conv.get("contributor") or {}).get("type")
            if ctype:
                contributor_type = ctype
                break
        foreign.append({
            "id": _record_id(f"{base_id}::{path}"),
            "timestamp": record.get("timestamp", ""),
            "entity_type": "file",
            "entity_path": path,
            "change_type": "modify",
            "diff": "",
            "reasoning": "",
            "agent": "ai" if contributor_type == "ai" else "",
            "session_id": "",
            "git_commit": commit,
            "project": "",
            "changeset_id": "",
            "metadata": "{}",
        })
    return foreign


def trace_record_to_event(record: dict) -> dict | None:
    """Single-event view of :func:`trace_record_to_events` (first event or ``None``).

    Prefer :func:`trace_record_to_events`, which correctly expands collapsed and
    multi-file records into all of their events.
    """
    events = trace_record_to_events(record)
    return events[0] if events else None


# ---------------------------------------------------------------------------
# validation (no third-party jsonschema dependency)
# ---------------------------------------------------------------------------


def validate_trace_record(record: Any) -> list[str]:
    """Structurally validate a record against Agent Trace v0.1.0.

    Returns a list of human-readable problems; an empty list means valid. This
    is a hand-rolled checker (Selvedge takes no jsonschema dependency) covering
    the spec's required fields, types, and enums. ``content_hash`` of a range is
    treated as optional, matching the spec.
    """
    problems: list[str] = []
    if not isinstance(record, dict):
        return ["record is not an object"]

    if record.get("version") != AGENT_TRACE_VERSION:
        problems.append(f"version must be {AGENT_TRACE_VERSION!r}")
    if not isinstance(record.get("id"), str) or not record.get("id"):
        problems.append("id must be a non-empty string")
    else:
        try:
            uuid.UUID(record["id"])
        except ValueError:
            problems.append("id must be a UUID")
    if not isinstance(record.get("timestamp"), str) or not record.get("timestamp"):
        problems.append("timestamp must be a non-empty string")

    vcs = record.get("vcs")
    if vcs is not None:
        if not isinstance(vcs, dict):
            problems.append("vcs must be an object")
        else:
            if vcs.get("type") not in {"git", "jj", "hg", "svn"}:
                problems.append("vcs.type must be one of git/jj/hg/svn")
            if not isinstance(vcs.get("revision"), str) or not vcs.get("revision"):
                problems.append("vcs.revision must be a non-empty string")

    tool = record.get("tool")
    if tool is not None and not isinstance(tool, dict):
        problems.append("tool must be an object")

    files = record.get("files")
    if not isinstance(files, list):
        problems.append("files must be an array")
    else:
        for i, f in enumerate(files):
            problems.extend(_validate_file(f, i))

    if "metadata" in record and not isinstance(record["metadata"], dict):
        problems.append("metadata must be an object")

    return problems


def _validate_file(f: Any, i: int) -> list[str]:
    """Validate one ``files[]`` entry (and its conversations/ranges)."""
    problems: list[str] = []
    if not isinstance(f, dict):
        return [f"files[{i}] must be an object"]
    if not isinstance(f.get("path"), str) or not f.get("path"):
        problems.append(f"files[{i}].path must be a non-empty string")
    convs = f.get("conversations", [])
    if not isinstance(convs, list):
        problems.append(f"files[{i}].conversations must be an array")
        return problems
    for j, conv in enumerate(convs):
        loc = f"files[{i}].conversations[{j}]"
        if not isinstance(conv, dict):
            problems.append(f"{loc} must be an object")
            continue
        # Per spec, `contributor` is OPTIONAL on a conversation (it can be set
        # per-range instead); validate its shape only when present.
        contributor = conv.get("contributor")
        if contributor is not None:
            if not isinstance(contributor, dict):
                problems.append(f"{loc}.contributor must be an object")
            elif contributor.get("type") not in _CONTRIBUTOR_TYPES:
                problems.append(f"{loc}.contributor.type must be one of {sorted(_CONTRIBUTOR_TYPES)}")
        # `ranges` is REQUIRED on a conversation per the spec.
        if "ranges" not in conv:
            problems.append(f"{loc}.ranges is required")
            continue
        ranges = conv.get("ranges")
        if not isinstance(ranges, list):
            problems.append(f"{loc}.ranges must be an array")
            continue
        for k, rng in enumerate(ranges):
            if not isinstance(rng, dict):
                problems.append(f"{loc}.ranges[{k}] must be an object")
                continue
            range_contributor = rng.get("contributor")
            if range_contributor is not None:
                if not isinstance(range_contributor, dict):
                    problems.append(f"{loc}.ranges[{k}].contributor must be an object")
                elif range_contributor.get("type") not in _CONTRIBUTOR_TYPES:
                    problems.append(
                        f"{loc}.ranges[{k}].contributor.type must be one of {sorted(_CONTRIBUTOR_TYPES)}"
                    )
            for bound in ("start_line", "end_line"):
                val = rng.get(bound)
                if not isinstance(val, int) or isinstance(val, bool) or val < 1:
                    problems.append(f"{loc}.ranges[{k}].{bound} must be an integer >= 1")
    return problems


def content_hash(text: str) -> str:
    """A stable ``algo:hex`` content hash, for callers that want one on a range."""
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()
