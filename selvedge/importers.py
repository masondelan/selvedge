"""
Migration file importers for ``selvedge import``.

Parses schema migration files and converts DDL operations into ChangeEvents
so you can backfill Selvedge history for a project that existed before
Selvedge was installed.

Supported formats:
  - Raw SQL DDL files (.sql) — CREATE TABLE, ALTER TABLE ADD/DROP/RENAME COLUMN,
    DROP TABLE, CREATE/DROP INDEX
  - Alembic Python migration files (.py) — op.create_table, op.drop_table,
    op.add_column, op.drop_column, op.alter_column, op.create_index,
    op.drop_index, op.rename_table

Usage::

    from selvedge.importers import import_path
    events = import_path(Path("migrations/"), fmt="auto", project="my-api")
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .models import ChangeEvent


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def import_path(
    target: Path,
    fmt: str = "auto",
    project: str = "",
) -> list[ChangeEvent]:
    """
    Parse ``target`` (file or directory) and return a list of ChangeEvents.

    Args:
        target:  A single migration file or a directory of migration files.
                 Directories are walked recursively; files are sorted by name
                 so Alembic-style numeric prefixes give chronological order.
        fmt:     "auto" (detect by extension/content), "sql", or "alembic".
        project: Optional project name to tag all generated events.

    Returns:
        List of ChangeEvents, in the order the migrations were discovered.
    """
    files = _collect_files(target, fmt)
    events: list[ChangeEvent] = []
    for f in files:
        parser = _pick_parser(f, fmt)
        events.extend(parser(f, project=project))
    return events


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------


def _collect_files(target: Path, fmt: str) -> list[Path]:
    """Return sorted list of migration files under target."""
    if target.is_file():
        return [target]

    sql_exts = {".sql"}
    py_exts = {".py"}

    results: list[Path] = []
    for f in sorted(target.rglob("*")):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if fmt == "sql" and ext in sql_exts:
            results.append(f)
        elif fmt == "alembic" and ext in py_exts:
            results.append(f)
        elif fmt == "auto" and ext in sql_exts | py_exts:
            results.append(f)
    return results


def _pick_parser(f: Path, fmt: str) -> Callable[[Path, str], list[ChangeEvent]]:
    """Choose the right parser for a file."""
    if fmt == "alembic":
        return parse_alembic_file
    if fmt == "sql":
        return parse_sql_file
    # auto-detect
    if f.suffix.lower() == ".py":
        return parse_alembic_file
    return parse_sql_file


# ---------------------------------------------------------------------------
# SQL DDL parser
# ---------------------------------------------------------------------------

# Patterns match the most common DDL forms found in raw SQL migration files.
# Case-insensitive, whitespace-tolerant.

_SQL_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`'\"]?(\w+)[`'\"]?",
    re.IGNORECASE,
)
_SQL_DROP_TABLE = re.compile(
    r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?[`'\"]?(\w+)[`'\"]?",
    re.IGNORECASE,
)
_SQL_ADD_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+[`'\"]?(\w+)[`'\"]?\s+ADD\s+(?:COLUMN\s+)?[`'\"]?(\w+)[`'\"]?\s+([^,;]+)",
    re.IGNORECASE,
)
_SQL_DROP_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+[`'\"]?(\w+)[`'\"]?\s+DROP\s+(?:COLUMN\s+)?[`'\"]?(\w+)[`'\"]?",
    re.IGNORECASE,
)
_SQL_RENAME_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+[`'\"]?(\w+)[`'\"]?\s+RENAME\s+(?:COLUMN\s+)?[`'\"]?(\w+)[`'\"]?"
    r"\s+TO\s+[`'\"]?(\w+)[`'\"]?",
    re.IGNORECASE,
)
_SQL_ALTER_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+[`'\"]?(\w+)[`'\"]?\s+(?:ALTER|MODIFY)\s+(?:COLUMN\s+)?[`'\"]?(\w+)[`'\"]?",
    re.IGNORECASE,
)
_SQL_CREATE_INDEX = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+ON\s+[`'\"]?(\w+)[`'\"]?",
    re.IGNORECASE,
)
_SQL_DROP_INDEX = re.compile(
    r"DROP\s+INDEX\s+(?:IF\s+EXISTS\s+)?[`'\"]?(\w+)[`'\"]?",
    re.IGNORECASE,
)
_SQL_RENAME_TABLE = re.compile(
    r"ALTER\s+TABLE\s+[`'\"]?(\w+)[`'\"]?\s+RENAME\s+(?:TO\s+)?[`'\"]?(\w+)[`'\"]?",
    re.IGNORECASE,
)


def parse_sql_file(path: Path, project: str = "") -> list[ChangeEvent]:
    """
    Parse a raw SQL DDL file and return ChangeEvents for each schema operation.

    Handles: CREATE/DROP TABLE, ADD/DROP/RENAME/ALTER COLUMN,
    CREATE/DROP INDEX, RENAME TABLE.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    events: list[ChangeEvent] = []
    source = f"imported from {path.name}"

    # Walk statement by statement (split on semicolons)
    for raw_stmt in text.split(";"):
        stmt = raw_stmt.strip()
        if not stmt:
            continue

        # One-line representation of the statement for the diff field
        diff_line = " ".join(stmt.split()) + ";"

        # RENAME COLUMN — must check before ADD/DROP to avoid mismatching
        m = _SQL_RENAME_COLUMN.search(stmt)
        if m:
            table, old_col, new_col = m.group(1), m.group(2), m.group(3)
            events.append(ChangeEvent(
                entity_path=f"{table}.{old_col}",
                entity_type="column",
                change_type="rename",
                diff=diff_line,
                reasoning=source,
                project=project,
            ))
            continue

        # RENAME TABLE — must check before ALTER COLUMN
        m = _SQL_RENAME_TABLE.search(stmt)
        if m and "COLUMN" not in stmt.upper():
            table = m.group(1)
            events.append(ChangeEvent(
                entity_path=table,
                entity_type="table",
                change_type="rename",
                diff=diff_line,
                reasoning=source,
                project=project,
            ))
            continue

        # ALTER / MODIFY COLUMN
        m = _SQL_ALTER_COLUMN.search(stmt)
        if m and "ADD" not in stmt.upper()[:stmt.upper().find("ALTER") + 20]:
            table, col = m.group(1), m.group(2)
            events.append(ChangeEvent(
                entity_path=f"{table}.{col}",
                entity_type="column",
                change_type="modify",
                diff=diff_line,
                reasoning=source,
                project=project,
            ))
            continue

        # ADD COLUMN
        m = _SQL_ADD_COLUMN.search(stmt)
        if m:
            table, col, col_def = m.group(1), m.group(2), m.group(3).strip()
            events.append(ChangeEvent(
                entity_path=f"{table}.{col}",
                entity_type="column",
                change_type="add",
                diff=f"+ {col} {col_def}".strip(),
                reasoning=source,
                project=project,
            ))
            continue

        # DROP COLUMN
        m = _SQL_DROP_COLUMN.search(stmt)
        if m:
            table, col = m.group(1), m.group(2)
            events.append(ChangeEvent(
                entity_path=f"{table}.{col}",
                entity_type="column",
                change_type="remove",
                diff=diff_line,
                reasoning=source,
                project=project,
            ))
            continue

        # CREATE TABLE
        m = _SQL_CREATE_TABLE.search(stmt)
        if m:
            table = m.group(1)
            events.append(ChangeEvent(
                entity_path=table,
                entity_type="table",
                change_type="create",
                diff=diff_line[:200],
                reasoning=source,
                project=project,
            ))
            continue

        # DROP TABLE
        m = _SQL_DROP_TABLE.search(stmt)
        if m:
            table = m.group(1)
            events.append(ChangeEvent(
                entity_path=table,
                entity_type="table",
                change_type="delete",
                diff=diff_line,
                reasoning=source,
                project=project,
            ))
            continue

        # CREATE INDEX
        m = _SQL_CREATE_INDEX.search(stmt)
        if m:
            table = m.group(1)
            events.append(ChangeEvent(
                entity_path=table,
                entity_type="index",
                change_type="index_add",
                diff=diff_line,
                reasoning=source,
                project=project,
            ))
            continue

        # DROP INDEX
        m = _SQL_DROP_INDEX.search(stmt)
        if m:
            index_name = m.group(1)
            events.append(ChangeEvent(
                entity_path=index_name,
                entity_type="index",
                change_type="index_remove",
                diff=diff_line,
                reasoning=source,
                project=project,
            ))
            continue

    return events


# ---------------------------------------------------------------------------
# Alembic migration parser
# ---------------------------------------------------------------------------

_ALB_ADD_COLUMN = re.compile(
    r"op\.add_column\(\s*['\"](\w+)['\"].*?sa\.Column\(\s*['\"](\w+)['\"].*?\)",
    re.IGNORECASE | re.DOTALL,
)
_ALB_DROP_COLUMN = re.compile(
    r"op\.drop_column\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]",
    re.IGNORECASE,
)
_ALB_CREATE_TABLE = re.compile(
    r"op\.create_table\(\s*['\"](\w+)['\"]",
    re.IGNORECASE,
)
_ALB_DROP_TABLE = re.compile(
    r"op\.drop_table\(\s*['\"](\w+)['\"]",
    re.IGNORECASE,
)
_ALB_ALTER_COLUMN = re.compile(
    r"op\.alter_column\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]",
    re.IGNORECASE,
)
_ALB_RENAME_TABLE = re.compile(
    r"op\.rename_table\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]",
    re.IGNORECASE,
)
_ALB_CREATE_INDEX = re.compile(
    r"op\.create_index\(.*?['\"](\w+)['\"]",
    re.IGNORECASE,
)
_ALB_DROP_INDEX = re.compile(
    r"op\.drop_index\(\s*['\"](\w+)['\"]",
    re.IGNORECASE,
)
_ALB_EXECUTE = re.compile(
    r"op\.execute\(\s*['\"](.{10,200})['\"]",
    re.IGNORECASE | re.DOTALL,
)


def parse_alembic_file(path: Path, project: str = "") -> list[ChangeEvent]:
    """
    Parse an Alembic Python migration file and return ChangeEvents.

    Handles: create_table, drop_table, add_column, drop_column, alter_column,
    rename_table, create_index, drop_index, and raw op.execute() SQL.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    # Only parse upgrade() functions — skip downgrade() to avoid duplicates
    upgrade_text = _extract_upgrade_block(text)
    if not upgrade_text:
        upgrade_text = text  # fall back to full file if we can't isolate upgrade()

    events: list[ChangeEvent] = []
    source = f"imported from {path.name}"

    for m in _ALB_ADD_COLUMN.finditer(upgrade_text):
        table, col = m.group(1), m.group(2)
        events.append(ChangeEvent(
            entity_path=f"{table}.{col}",
            entity_type="column",
            change_type="add",
            diff=m.group(0)[:200],
            reasoning=source,
            project=project,
        ))

    for m in _ALB_DROP_COLUMN.finditer(upgrade_text):
        table, col = m.group(1), m.group(2)
        events.append(ChangeEvent(
            entity_path=f"{table}.{col}",
            entity_type="column",
            change_type="remove",
            diff=m.group(0),
            reasoning=source,
            project=project,
        ))

    for m in _ALB_ALTER_COLUMN.finditer(upgrade_text):
        table, col = m.group(1), m.group(2)
        events.append(ChangeEvent(
            entity_path=f"{table}.{col}",
            entity_type="column",
            change_type="modify",
            diff=m.group(0)[:200],
            reasoning=source,
            project=project,
        ))

    for m in _ALB_CREATE_TABLE.finditer(upgrade_text):
        table = m.group(1)
        events.append(ChangeEvent(
            entity_path=table,
            entity_type="table",
            change_type="create",
            diff=m.group(0),
            reasoning=source,
            project=project,
        ))

    for m in _ALB_DROP_TABLE.finditer(upgrade_text):
        table = m.group(1)
        events.append(ChangeEvent(
            entity_path=table,
            entity_type="table",
            change_type="delete",
            diff=m.group(0),
            reasoning=source,
            project=project,
        ))

    for m in _ALB_RENAME_TABLE.finditer(upgrade_text):
        old_name = m.group(1)
        events.append(ChangeEvent(
            entity_path=old_name,
            entity_type="table",
            change_type="rename",
            diff=m.group(0),
            reasoning=source,
            project=project,
        ))

    for m in _ALB_CREATE_INDEX.finditer(upgrade_text):
        index_name = m.group(1)
        events.append(ChangeEvent(
            entity_path=index_name,
            entity_type="index",
            change_type="index_add",
            diff=m.group(0)[:200],
            reasoning=source,
            project=project,
        ))

    for m in _ALB_DROP_INDEX.finditer(upgrade_text):
        index_name = m.group(1)
        events.append(ChangeEvent(
            entity_path=index_name,
            entity_type="index",
            change_type="index_remove",
            diff=m.group(0),
            reasoning=source,
            project=project,
        ))

    # op.execute() with raw SQL — parse it too
    for m in _ALB_EXECUTE.finditer(upgrade_text):
        raw_sql = m.group(1).strip()
        sql_events = parse_sql_file.__wrapped__(raw_sql, project=project) \
            if hasattr(parse_sql_file, "__wrapped__") \
            else _parse_sql_text(raw_sql, source=source, project=project)
        events.extend(sql_events)

    return events


def _extract_upgrade_block(text: str) -> str:
    """
    Extract the body of the upgrade() function from an Alembic migration.
    Returns empty string if not found.
    """
    m = re.search(r"def upgrade\(\)[^:]*:(.*?)(?=\ndef |\Z)", text, re.DOTALL)
    return m.group(1) if m else ""


def _parse_sql_text(sql: str, source: str = "", project: str = "") -> list[ChangeEvent]:
    """Parse raw SQL text (not a file) — used internally for op.execute() blocks."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".sql", mode="w", delete=False) as f:
        f.write(sql)
        tmp = Path(f.name)
    try:
        events = parse_sql_file(tmp, project=project)
        for e in events:
            if source:
                e.reasoning = source
        return events
    finally:
        tmp.unlink(missing_ok=True)
