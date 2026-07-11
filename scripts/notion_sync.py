#!/usr/bin/env python3
"""Mirror Selvedge's repo source-of-truth into the Notion internal hub.

This is the **Plus-compatible** replacement for Notion Workers (which require a
Business/Enterprise workspace). It runs in the repo's own CI: a deterministic,
standard-library-only upsert from the two canonical repo files into the two
*mirror* databases of the Notion "Selvedge — Internal" hub.

- ``CHANGELOG.md``        -> 🚀 Releases   (one row per version)
- ``docs/architecture.md`` -> 🗺️ Roadmap   (one row per phase heading)

Design constraints (match the Selvedge core rules):

- **No external dependencies.** ``urllib`` / ``re`` / ``json`` only.
- **No LLM calls.** Templated, deterministic parsing and assembly.
- **Idempotent.** A second run with no repo change is a no-op; rows are keyed
  by their title (version / phase) and patched only when a field actually
  differs.

The repo stays canonical for *what shipped* (``CHANGELOG.md`` /
``docs/architecture.md``); these databases are mirrors and are never the place
to decide what shipped.

Environment:

- ``NOTION_TOKEN``     — internal-integration token (the integration must be
                         shared with both databases).
- ``RELEASES_DB_ID``   — the 🚀 Releases database id.
- ``ROADMAP_DB_ID``    — the 🗺️ Roadmap database id.

Usage::

    python scripts/notion_sync.py --dry-run    # parse + show planned writes
    python scripts/notion_sync.py              # apply the upserts
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"
CHANGELOG_URL = "https://github.com/masondelan/selvedge/blob/main/CHANGELOG.md"

# A version at or below this is treated as shipped; at or above the next patch
# it is planned. Kept in lockstep with the §7 status mapping in the build spec.
# BUMP THIS ON EVERY RELEASE — it is part of the version-bump checklist; a stale
# value silently marks a shipped phase as "Planned" in the Notion Roadmap mirror.
LATEST_SHIPPED = (0, 3, 9, 1)
CONDITIONAL_VERSIONS = {"v0.3.15"}


# --------------------------------------------------------------------------- #
# Parsed records                                                              #
# --------------------------------------------------------------------------- #
@dataclass
class Release:
    """One row of the 🚀 Releases mirror."""

    version: str
    date: str  # ISO-8601 date or "" for planned
    kind: str  # "Shipped" | "Planned"
    highlights: str

    def properties(self) -> dict:
        """Render as a Notion ``properties`` payload."""
        props: dict = {
            "Version": _title(self.version),
            "Kind": _select(self.kind),
            "Highlights": _rich_text(self.highlights),
            "Changelog": {"url": CHANGELOG_URL},
        }
        props["Date"] = {"date": {"start": self.date} if self.date else None}
        return props


@dataclass
class Phase:
    """One row of the 🗺️ Roadmap mirror."""

    phase: str
    version: str  # "vX.Y.Z" or "" for the unversioned platform phase
    status: str  # "Done" | "Planned" | "Conditional"
    ship_date: str  # ISO-8601 date or ""
    summary: str

    def properties(self) -> dict:
        """Render as a Notion ``properties`` payload."""
        props: dict = {
            "Phase": _title(self.phase),
            "Version": _rich_text(self.version),
            "Status": _select(self.status),
            "Summary": _rich_text(self.summary),
            "Origin": _rich_text("docs/architecture.md"),
        }
        props["Ship date"] = {
            "date": {"start": self.ship_date} if self.ship_date else None
        }
        return props


# --------------------------------------------------------------------------- #
# Notion property helpers                                                      #
# --------------------------------------------------------------------------- #
def _title(text: str) -> dict:
    return {"title": [{"text": {"content": text}}]}


def _rich_text(text: str) -> dict:
    return {"rich_text": [{"text": {"content": text}}] if text else []}


def _select(name: str) -> dict:
    return {"select": {"name": name} if name else None}


def _plain(prop: dict | None) -> str:
    """Read a title/rich_text/select/url/date property back to a plain string."""
    if prop is None:
        return ""
    kind = prop.get("type")
    if kind in ("title", "rich_text"):
        return "".join(part.get("plain_text", "") for part in prop[kind])
    if kind == "select":
        return prop["select"]["name"] if prop["select"] else ""
    if kind == "url":
        return prop["url"] or ""
    if kind == "date":
        return prop["date"]["start"] if prop["date"] else ""
    return ""


# --------------------------------------------------------------------------- #
# Parsers (repo -> records)                                                    #
# --------------------------------------------------------------------------- #
# Version group accepts an optional fourth segment so four-part patch
# versions (e.g. 0.3.9.1, PEP 440) aren't silently skipped — the header
# would otherwise never match and the release would vanish from the Notion
# mirror despite a green sync run.
_SHIPPED_RE = re.compile(r"^## \[(\d+\.\d+\.\d+(?:\.\d+)?)\] [—-] (\d{4}-\d{2}-\d{2})")
_PLANNED_RE = re.compile(r"^### \[(\d+\.\d+\.\d+(?:\.\d+)?)\] [—-] planned", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*-\s+(.*)")


def _clean(text: str) -> str:
    """Strip the most common markdown decorations for a one-line summary."""
    text = re.sub(r"^\[[ xX]\]\s*", "", text)  # task-list checkbox marker
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)  # bold
    text = re.sub(r"\*([^*]+)\*", r"\1", text)  # italic
    text = re.sub(r"`([^`]+)`", r"\1", text)  # inline code
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # links
    return text.strip()


def parse_changelog(path: Path) -> list[Release]:
    """Parse ``CHANGELOG.md`` into Releases, newest first.

    Picks up both shipped headers (``## [x.y.z] — YYYY-MM-DD``) and the planned
    entries under the ``## Roadmap`` section (``### [x.y.z] — planned``). The
    highlight is the first descriptive line (prose lead or first bullet) after
    the version header, decoration-stripped and length-capped.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    releases: list[Release] = []
    for i, line in enumerate(lines):
        shipped = _SHIPPED_RE.match(line)
        planned = _PLANNED_RE.match(line)
        header = shipped or planned
        if header is None:
            continue
        version = header.group(1)
        date = shipped.group(2) if shipped else ""
        kind = "Shipped" if shipped else "Planned"
        releases.append(
            Release(
                version=version,
                date=date,
                kind=kind,
                highlights=_first_summary(lines, i + 1, stop_at_subheading=False),
            )
        )
    return releases


def _first_summary(
    lines: list[str], start: int, cap: int = 300, stop_at_subheading: bool = True
) -> str:
    """Return the first descriptive line at/after ``start`` (capped).

    Prefers the thematic blockquote line a heading is usually followed by (the
    leading ``>`` is stripped); falls back to the first bullet or prose line.

    Always stops at the next ``## `` section. ``stop_at_subheading`` controls
    ``### ``: roadmap phases stop there (the next phase), but changelog entries
    skip past ``### Added``/``Changed``/``Fixed`` to reach the first bullet.
    """
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith(">"):
            stripped = stripped.lstrip(">").strip()
        if not stripped:
            continue
        if stripped.startswith("## "):
            break
        if stripped.startswith("### "):
            if stop_at_subheading:
                break
            continue
        if stripped.startswith("#"):
            break
        if stripped.startswith("---"):
            continue
        bullet = _BULLET_RE.match(line)
        text = _clean(bullet.group(1) if bullet else stripped)
        if text:
            return text[:cap].rstrip()
    return ""


_PHASE_RE = re.compile(r"^### (Phase [^\n(]+?)(?:\s*\(([^)]*)\))?\s*$")
_VERSION_IN_PAREN = re.compile(r"v(\d+\.\d+\.\d+(?:\.\d+)?)")


def parse_roadmap(path: Path, ship_dates: dict[str, str]) -> list[Phase]:
    """Parse the ``## Phase plan`` section of ``docs/architecture.md``.

    Status mapping mirrors the build spec: a ``DONE ✓`` marker (or a version at
    or below the latest shipped) is ``Done``; ``v0.3.15`` is ``Conditional``;
    everything else is ``Planned``. ``ship_dates`` maps ``vX.Y.Z`` -> ISO date
    (from the changelog) so shipped phases carry their release date.
    """
    text = path.read_text(encoding="utf-8")
    section = text.split("## Phase plan", 1)
    if len(section) < 2:
        return []
    body = section[1].split("\n## ", 1)[0]
    lines = body.splitlines()

    phases: list[Phase] = []
    for i, line in enumerate(lines):
        match = _PHASE_RE.match(line)
        if not match:
            continue
        # Strip inline-code backticks so the title matches the seeded row
        # (e.g. "Phase 2.13 — prior_attempts wedge …").
        phase_name = match.group(1).strip().replace("`", "")
        paren = (match.group(2) or "").strip()
        vmatch = _VERSION_IN_PAREN.search(paren)
        version = f"v{vmatch.group(1)}" if vmatch else ""
        status = _phase_status(version, paren)
        phases.append(
            Phase(
                phase=phase_name,
                version=version,
                status=status,
                ship_date=ship_dates.get(version, "") if status == "Done" else "",
                summary=_first_summary(lines, i + 1),
            )
        )
    return phases


def _phase_status(version: str, paren: str) -> str:
    """Map a phase heading to Done / Conditional / Planned."""
    if "DONE" in paren.upper():
        return "Done"
    if version in CONDITIONAL_VERSIONS:
        return "Conditional"
    if version:
        parts = tuple(int(n) for n in version[1:].split("."))
        if parts <= LATEST_SHIPPED:
            return "Done"
    return "Planned"


# --------------------------------------------------------------------------- #
# Notion REST helpers                                                          #
# --------------------------------------------------------------------------- #
class Notion:
    """Minimal Notion REST client over ``urllib`` (no SDK dependency)."""

    def __init__(self, token: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{NOTION_API}{path}", data=data, headers=self._headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # surface the API error body
            body = exc.read().decode("utf-8", "replace")
            raise SystemExit(
                f"Notion API {exc.code} on {method} {path}: {body}"
            ) from exc

    def query_all(self, database_id: str) -> list[dict]:
        """Return every page in a database, following pagination."""
        results: list[dict] = []
        cursor: str | None = None
        while True:
            payload: dict = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            page = self._request("POST", f"/databases/{database_id}/query", payload)
            results.extend(page["results"])
            if not page.get("has_more"):
                return results
            cursor = page["next_cursor"]

    def create_page(self, database_id: str, properties: dict) -> dict:
        return self._request(
            "POST",
            "/pages",
            {"parent": {"database_id": database_id}, "properties": properties},
        )

    def update_page(self, page_id: str, properties: dict) -> dict:
        return self._request("PATCH", f"/pages/{page_id}", {"properties": properties})


# --------------------------------------------------------------------------- #
# Upsert                                                                       #
# --------------------------------------------------------------------------- #
@dataclass
class Plan:
    """What an upsert would do — used for both dry-run output and apply."""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def _index_by_title(pages: list[dict], title_prop: str) -> dict[str, dict]:
    return {_plain(p["properties"].get(title_prop)): p for p in pages}


def _diff(existing: dict, desired: dict, fields: dict[str, str]) -> bool:
    """True if any tracked field differs (plain-string comparison)."""
    props = existing["properties"]
    for name in fields:
        if _plain(props.get(name)) != fields[name]:
            return True
    return False


def upsert(
    notion: Notion,
    database_id: str,
    title_prop: str,
    records: list,
    compare_fields,
    dry_run: bool,
) -> Plan:
    """Create-or-patch ``records`` keyed by their title property.

    ``compare_fields`` maps a record to a ``{prop_name: plain_value}`` dict used
    to decide whether an existing row needs patching (idempotency).
    """
    plan = Plan()
    existing = _index_by_title(notion.query_all(database_id), title_prop)
    for record in records:
        key = record.properties()[title_prop]["title"][0]["text"]["content"]
        desired = compare_fields(record)
        if key not in existing:
            plan.created.append(key)
            if not dry_run:
                notion.create_page(database_id, record.properties())
        elif _diff(existing[key], desired, desired):
            plan.updated.append(key)
            if not dry_run:
                notion.update_page(existing[key]["id"], record.properties())
        else:
            plan.unchanged.append(key)
    return plan


def _report(label: str, plan: Plan, dry_run: bool) -> None:
    verb = "would" if dry_run else "did"
    print(f"\n{label}:")
    print(f"  create ({verb}): {len(plan.created)}  {plan.created}")
    print(f"  update ({verb}): {len(plan.updated)}  {plan.updated}")
    print(f"  unchanged: {len(plan.unchanged)}")


# --------------------------------------------------------------------------- #
# Entrypoint                                                                   #
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and report planned writes without mutating Notion",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("NOTION_TOKEN", "")
    releases_db = os.environ.get("RELEASES_DB_ID", "")
    roadmap_db = os.environ.get("ROADMAP_DB_ID", "")
    have_creds = bool(token and releases_db and roadmap_db)

    releases = parse_changelog(CHANGELOG)
    ship_dates = {f"v{r.version}": r.date for r in releases if r.date}
    phases = parse_roadmap(ARCHITECTURE, ship_dates)

    if not releases:
        raise SystemExit("No releases parsed from CHANGELOG.md — refusing to run.")
    if not phases:
        raise SystemExit("No phases parsed from docs/architecture.md — refusing to run.")

    if not have_creds:
        print("Parse-only (set NOTION_TOKEN + DB ids to compute diffs / apply).\n")
        print(f"🚀 Releases — {len(releases)} parsed:")
        for r in releases:
            print(f"  · {r.version:8} [{r.kind:8}] {r.date or '(no date)':12} {r.highlights[:90]}")
        print(f"\n🗺️ Roadmap — {len(phases)} parsed:")
        for p in phases:
            ver = p.version or "—"
            print(f"  · {p.phase}")
            print(f"      {ver:9} [{p.status:11}] ship={p.ship_date or '—':11} {p.summary[:80]}")
        return 0

    notion = Notion(token)

    releases_plan = upsert(
        notion,
        releases_db,
        "Version",
        releases,
        lambda r: {
            "Version": r.version,
            "Date": r.date,
            "Kind": r.kind,
            "Highlights": r.highlights,
        },
        args.dry_run,
    )
    roadmap_plan = upsert(
        notion,
        roadmap_db,
        "Phase",
        phases,
        lambda p: {
            "Phase": p.phase,
            "Version": p.version,
            "Status": p.status,
            "Ship date": p.ship_date,
            "Summary": p.summary,
        },
        args.dry_run,
    )

    _report("🚀 Releases", releases_plan, args.dry_run)
    _report("🗺️ Roadmap", roadmap_plan, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
