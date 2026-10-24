"""Read-only, deterministic decision attribution for agents and human reviewers.

Actor names are reported identities, not authenticated signatures. Explicit
supersession is evidence of a revision, not proof of interpersonal disagreement.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import chain
from .storage import canonicalize_entity_path

FIELDS = (
    "id",
    "timestamp",
    "entity_path",
    "change_type",
    "reasoning",
    "agent",
    "session_id",
    "changeset_id",
    "supersedes",
    "constraint",
    "stale_when",
    "expires_when",
    "revisit_after",
    "git_commit",
)


def read_ledger(db: Path, entities: list[str], limit: int = 100) -> dict:
    """Read one SQLite snapshot without migrations, writes, or implicit creation.

    Match exact paths and dotted subentities, using the existing entity grammar.
    Empty entities selects the whole ledger for private local reporting. Counts
    and supersession links cover the full matching population before limiting.
    """
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    paths = sorted({canonicalize_entity_path(p) for p in entities})
    if any(not p for p in paths):
        raise ValueError("entity paths must not be empty")
    conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        # An ephemeral CTE avoids LIKE wildcards and SQLite's expression depth
        # limit for large PRs. Only parameters carry user-controlled paths.
        if paths:
            values = ",".join("(?)" for _ in paths)
            prefix = f"WITH requested(path) AS (VALUES {values}) "
            where = "EXISTS (SELECT 1 FROM requested r WHERE e.entity_path=r.path OR substr(e.entity_path,1,length(r.path)+1)=r.path||'.')"
        else:
            prefix, where = "", "1=1"
        columns = ",".join('e."' + f + '"' for f in FIELDS)
        query = (
            prefix
            + f"SELECT {columns} FROM events e WHERE {where} ORDER BY e.timestamp DESC,e.id DESC"
        )
        all_rows = conn.execute(query, paths)
        # Ids explicitly superseded anywhere in the store, to classify hidden rows.
        superseded = {
            row[0]
            for row in conn.execute(
                "SELECT supersedes FROM events WHERE change_type='supersede' AND supersedes IS NOT NULL"
            )
        }
        events: list[dict] = []
        total = 0
        evidence_omitted = 0
        actors: dict[str, int] = {}
        for row in all_rows:
            total += 1
            actor = row["agent"] or ""
            actors[actor] = actors.get(actor, 0) + 1
            if len(events) < limit:
                events.append({key: "" if row[key] is None else row[key] for key in FIELDS})
            elif _is_evidence(
                {key: row[key] for key in _EVIDENCE_FIELDS}
                | {"superseded_by": row["id"] in superseded}
            ):
                evidence_omitted += 1
        for event in events:
            # Resolve against the complete store, including older hidden rows.
            event["superseded_by"] = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM events WHERE change_type='supersede' AND supersedes=? ORDER BY timestamp,id",
                    (event["id"],),
                )
            ]
            event["revises_agent"] = ""
            event["cross_agent_revision"] = False
            if event["change_type"] == "supersede" and event["supersedes"]:
                old = conn.execute(
                    "SELECT agent FROM events WHERE id=?", (event["supersedes"],)
                ).fetchone()
                if old:
                    event["revises_agent"] = old[0] or ""
                    event["cross_agent_revision"] = bool(
                        old[0] and event["agent"] and old[0] != event["agent"]
                    )
        integrity = chain.verify_chain(conn)
        coverage = chain.chain_coverage(conn)
        head_seq, head_hash = chain.read_head(conn) if integrity["table_present"] else (0, "")
        return {
            "entities": paths,
            "total_matching": total,
            "shown": len(events),
            "omitted": total - len(events),
            "evidence_omitted": evidence_omitted,
            "agents": dict(sorted(actors.items())),
            "events": events,
            "chain": integrity,
            "chain_coverage": coverage,
            "chain_head": {"seq": head_seq, "hash": head_hash},
            "identity": "self-reported",
            "conflicts": "Only explicit cross-agent supersessions are identified; semantic conflicts are not inferred.",
        }
    finally:
        conn.close()


def _text(value: object, cap: int = 1200) -> str:
    """Render stored text literally without HTML, Markdown, or mention effects."""
    text = " ".join(str(value).split())
    if len(text) > cap:
        text = text[:cap] + " [truncated]"
    # Numeric entities also neutralize Markdown punctuation and @ mentions.
    return "".join(c if c.isalnum() or c == " " else f"&#{ord(c)};" for c in text)


def render_ledger(report: dict, *, max_chars: int = 50000) -> str:
    """Render a bounded PR-safe report of recorded reasons, not private thinking."""
    status = "PASS" if report["chain"]["intact"] else "FAIL"
    if not report["chain"]["table_present"]:
        status = "UNAVAILABLE"
    lines = [
        "## Selvedge decision context",
        "",
        f"{report['shown']} of {report['total_matching']} matching events selected; {report['omitted']} omitted by event limit.",
        f"Store chain: **{status}**; {report['chain_coverage']['unchained']} unchained rows in the full store.",
        "Actor/session labels are self-reported. Integrity checks detect changes to recorded data; they do not prove identities, completeness, or correctness. A local writer can recompute the chain.",
        "Explicit cross-agent revisions are shown below; unrecorded or semantic conflicts remain unknown.",
        "",
    ]
    if report["chain_head"]["hash"]:
        lines += [
            f"Chain head: {_text(report['chain_head']['seq'])} / {_text(report['chain_head']['hash'])}.",
            "",
        ]
    if not report["events"]:
        lines += ["No matching decisions recorded. Missing history is not approval.", ""]
    rendered = 0
    for event in report["events"]:
        block = [
            f"### {_text(event['entity_path'])}",
            f"**{_text(event['change_type'])}** · {_text(event['timestamp'])} · {_text(event['agent'] or 'actor not recorded')}",
            f"Event: {_text(event['id'])}; session: {_text(event['session_id'] or 'not recorded')}; changeset: {_text(event['changeset_id'] or 'not recorded')}.",
            f"Reason: {_text(event['reasoning'] or 'Reason not recorded')}",
        ]
        for field, label in (
            ("constraint", "Constraint"),
            ("stale_when", "Revisit when"),
            ("expires_when", "Expiry condition (not evaluated here)"),
            ("revisit_after", "Revisit after"),
            ("git_commit", "Recorded commit (outside chain)"),
        ):
            if event[field]:
                block.append(f"{label}: {_text(event[field])}")
        if event["supersedes"]:
            block.append(f"Explicitly supersedes: {_text(event['supersedes'])}.")
        if event["superseded_by"]:
            block.append(f"Superseded by: {_text(', '.join(event['superseded_by']))}.")
        if event["cross_agent_revision"]:
            block.append(
                f"Cross-agent revision: {_text(event['revises_agent'])} → {_text(event['agent'])}. Review both recorded reasons."
            )
        block.append("")
        if len("\n".join(lines + block)) > max_chars - 150:
            break
        lines.extend(block)
        rendered += 1
    if rendered < len(report["events"]):
        lines.append(
            f"Report size limit omitted {len(report['events']) - rendered} additional selected events. Use the local JSON report for full selected text."
        )
    return "\n".join(lines).rstrip() + "\n"


# Change types whose recorded verdict is itself decision evidence: a path
# considered and declined, tried and rolled back, or explicitly re-opened.
_EVIDENCE_TYPES = {
    "reject": "Rejected approach",
    "revert": "Reverted approach",
    "supersede": "Explicit revision",
}


_EVIDENCE_FIELDS = ("change_type", "constraint", "stale_when", "expires_when", "revisit_after")


def _is_evidence(event: dict) -> bool:
    """Whether an event carries decision evidence beyond routine change metadata.

    Every condition the compact block renders counts, including a bare
    ``revisit_after``, so the grouping matches the documented wording.
    """
    return bool(
        event["change_type"] in _EVIDENCE_TYPES
        or event["superseded_by"]
        or event["constraint"]
        or event["stale_when"]
        or event["expires_when"]
        or event["revisit_after"]
    )


def _compact_block(event: dict, field_cap: int) -> list[str]:
    """One decision: recorded content first, routine metadata last."""
    kind = _EVIDENCE_TYPES.get(event["change_type"], "Recorded change")
    lines = [
        f"- **{kind}** on {_text(event['entity_path'], field_cap)}: "
        + _text(event["reasoning"] or "Reason not recorded", field_cap)
    ]
    for field, label in (
        ("constraint", "Constraint"),
        ("stale_when", "Revisit when"),
        ("expires_when", "Expiry condition (not evaluated here)"),
        ("revisit_after", "Revisit after (not evaluated here)"),
    ):
        if event[field]:
            lines.append(f"  {label}: {_text(event[field], field_cap)}")
    if event["supersedes"]:
        lines.append(f"  Explicitly supersedes event {_text(event['supersedes'], field_cap)}.")
    if event["superseded_by"]:
        lines.append(
            "  Status: superseded by later explicit revision "
            + _text(", ".join(event["superseded_by"]), field_cap)
            + "."
        )
    if event["cross_agent_revision"]:
        lines.append(
            f"  Cross-agent revision: {_text(event['revises_agent'], field_cap)} → {_text(event['agent'], field_cap)}."
        )
    lines.append(
        f"  Recorded {_text(event['timestamp'], field_cap)} · {_text(event['change_type'], field_cap)}"
        f" · actor {_text(event['agent'] or 'not recorded', field_cap)}"
        f" · event {_text(event['id'], field_cap)}"
    )
    return lines


def render_review_context(
    report: dict,
    *,
    budget: int = 6000,
    source: str = "",
    details: str = "",
    field_cap: int = 300,
    prefix_chars: int = 0,
) -> str:
    """Render a compact, bounded PR comment from an existing ledger report.

    Decision evidence (rejected, reverted or explicitly revised approaches and
    recorded constraints/conditions) is listed before other recorded changes;
    each group keeps the report's newest-first order. Output never exceeds
    ``budget`` characters, less ``prefix_chars`` already used by a caller's
    preamble: an over-long ``source`` is replaced by a fixed note, and a
    header that still cannot fit raises ``ValueError``. It states how many
    matching events were omitted and why, including how many events beyond
    the event limit are decision evidence.
    ``source`` and ``details`` are caller-built Markdown links from validated
    values; every stored value is rendered literally via ``_text``.
    Nothing is inferred: correctness, semantic conflicts and completeness of
    capture stay explicitly unevaluated.
    """
    limit = budget - prefix_chars
    if limit < 1000:
        raise ValueError("budget must leave at least 1000 characters for the report")
    status = "PASS" if report["chain"]["intact"] else "FAIL"
    if not report["chain"]["table_present"]:
        status = "UNAVAILABLE"
    events = report["events"]
    evidence = [e for e in events if _is_evidence(e)]
    routine = [e for e in events if not _is_evidence(e)]
    see = f"; see {details}" if details else ""
    # Reserve room for the closing count/omission lines so the budget holds.
    reserve = 500 + len(see)
    if len(source) + len(see) + 1200 > limit:
        # A caller-built link too long for the budget (e.g. a very long
        # db-path) is replaced by a fixed note rather than overflowing.
        source = "link omitted (too long for the output budget)"
    head = [
        "## Selvedge decision context",
        "",
        (f"Source: {source}. " if source else "")
        + "Recorded text is self-reported by the listed actors and shown literally; Selvedge does not verify it.",
        f"Store chain: **{status}**; {report['chain_coverage']['unchained']} unchained rows in the full store.",
        "Not evaluated: correctness, semantic conflicts, or whether every decision was recorded. Missing history is not approval.",
        "",
    ]
    if not events:
        head.append("No matching decisions recorded.")
    if len("\n".join(head)) + reserve > limit:
        raise ValueError("budget too small for the report header and closing lines")
    body: list[str] = []
    shown = 0
    full = False
    for title, group in (("Decision evidence", evidence), ("Other recorded changes", routine)):
        heading = ([""] if body else []) + [f"### {title}"]
        for event in group:
            if full:
                break
            block = heading + _compact_block(event, field_cap)
            if len("\n".join(head + body + block)) + reserve > limit:
                full = True
                break
            body.extend(block)
            heading = []
            shown += 1
    by_budget = len(events) - shown
    omitted = report["omitted"] + by_budget
    tail = [
        "",
        f"Showing {shown} of {report['total_matching']} matching events within a {budget}-character budget.",
    ]
    if omitted:
        tail.append(
            f"{omitted} more matching events omitted ({report['omitted']} by event limit,"
            f" {by_budget} by output budget){see}."
        )
    if report["omitted"]:
        tail.append(
            f"{report.get('evidence_omitted', 0)} of the {report['omitted']} events beyond the"
            " event limit are decision evidence (rejected, reverted, revised or conditioned)."
        )
        tail.append(
            "Events beyond the event limit appear in neither report; raise `limit`"
            " or run `selvedge ledger --json` locally."
        )
    return "\n".join(head + body + tail).rstrip() + "\n"
