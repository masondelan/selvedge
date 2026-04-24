"""
Migration file importers for ``selvedge import``.

Parses schema migration files and converts DDL operations into ChangeEvents
so you can backfill Selvedge history for a project that existed before
Selvedge was installed.

Supported formats:
  - Raw SQL DDL files (.sql) — CREATE TABLE (with per-column events),
    ALTER TABLE ADD/DROP/RENAME COLUMN, DROP TABLE, CREATE/DROP INDEX,
    RENAME TABLE
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

_SQL_CREATE_TABLE_HEAD = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`'\"]?(\w+)[`'\"]?\s*\(",
    re.IGNORECASE,
)

# Top-level constraint keywords inside a CREATE TABLE body — entries that
# start with one of these are NOT column definitions.
_TABLE_CONSTRAINT_KEYWORDS = frozenset(
    {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"}
)


def _extract_create_table_body(stmt: str) -> tuple[str, str] | None:
    """
    Extract the (table_name, column_body) from a CREATE TABLE statement.

    Walks the parentheses with a depth counter so types like ``DECIMAL(10, 2)``
    don't confuse the parser. Returns None if the statement isn't a complete
    CREATE TABLE with a balanced body.
    """
    head = _SQL_CREATE_TABLE_HEAD.search(stmt)
    if not head:
        return None
    table = head.group(1)
    start = head.end()  # right after the opening '('
    depth = 1
    end = start
    while end < len(stmt) and depth > 0:
        ch = stmt[end]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        end += 1
    if depth != 0:
        return None
    body = stmt[start : end - 1]
    return table, body


def _split_top_level_commas(body: str) -> list[str]:
    """Split a comma-separated list, ignoring commas inside parentheses."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current and "".join(current).strip():
        parts.append("".join(current).strip())
    return parts


def _is_column_definition(part: str) -> bool:
    """
    Return True if ``part`` looks like a column definition rather than a
    table-level constraint clause.
    """
    upper = part.upper().lstrip()
    if not upper:
        return False
    first_word = upper.split(maxsplit=1)[0].rstrip(",(")
    return first_word not in _TABLE_CONSTRAINT_KEYWORDS


_COLUMN_NAME_RE = re.compile(r"\s*[`'\"]?(\w+)[`'\"]?")


def _extract_column_name(part: str) -> str | None:
    m = _COLUMN_NAME_RE.match(part)
    return m.group(1) if m else None


def parse_sql_file(path: Path, project: str = "") -> list[ChangeEvent]:
    """
    Parse a raw SQL DDL file and return ChangeEvents for each schema operation.

    Handles: CREATE TABLE (with per-column events), CREATE/DROP TABLE,
    ADD/DROP/RENAME/ALTER COLUMN, CREATE/DROP INDEX, RENAME TABLE.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return _parse_sql_text(text, source=f"imported from {path.name}", project=project)


def _parse_sql_text(sql: str, source: str = "", project: str = "") -> list[ChangeEvent]:
    """
    Parse raw SQL text and return ChangeEvents.

    Used by parse_sql_file() and by the Alembic parser when it encounters
    op.execute() blocks containing inline DDL.
    """
    events: list[ChangeEvent] = []

    # Walk statement by statement (split on semicolons)
    for raw_stmt in sql.split(";"):
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
            # Also emit an "add" event for the new column name so future
            # `selvedge blame {table}.{new_col}` calls find the rename
            # context rather than returning empty.
            events.append(ChangeEvent(
                entity_path=f"{table}.{new_col}",
                entity_type="column",
                change_type="add",
                diff=diff_line,
                reasoning=f"renamed from {table}.{old_col} ({source})",
                project=project,
            ))
            continue

        # RENAME TABLE — must check before ALTER COLUMN
        m = _SQL_RENAME_TABLE.search(stmt)
        if m and "COLUMN" not in stmt.upper():
            old_name, new_name = m.group(1), m.group(2)
            events.append(ChangeEvent(
                entity_path=old_name,
                entity_type="table",
                change_type="rename",
                diff=diff_line,
                reasoning=f"renamed to {new_name} ({source})",
                project=project,
            ))
            # Emit the new name as a create event so `selvedge blame {new_name}`
            # surfaces the rename history.
            events.append(ChangeEvent(
                entity_path=new_name,
                entity_type="table",
                change_type="create",
                diff=diff_line,
                reasoning=f"created via rename from {old_name} ({source})",
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

        # CREATE TABLE — emit one event for the table AND one per column.
        # Without per-column events, asking `selvedge blame users.email` for
        # a column that's only ever defined in the original CREATE TABLE
        # returns "no history found", which defeats the whole point of the
        # importer.
        m = _SQL_CREATE_TABLE.search(stmt)
        if m:
            parsed = _extract_create_table_body(stmt)
            if parsed:
                table, body = parsed
            else:
                table, body = m.group(1), ""

            events.append(ChangeEvent(
                entity_path=table,
                entity_type="table",
                change_type="create",
                diff=diff_line[:200],
                reasoning=source,
                project=project,
            ))

            if body:
                for part in _split_top_level_commas(body):
                    if not _is_column_definition(part):
                        continue
                    col_name = _extract_column_name(part)
                    if not col_name:
                        continue
                    col_def = " ".join(part.split())
                    events.append(ChangeEvent(
                        entity_path=f"{table}.{col_name}",
                        entity_type="column",
                        change_type="add",
                        diff=f"+ {col_def}"[:200],
                        reasoning=f"defined in CREATE TABLE {table} ({source})",
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

# Matches the columns inside an op.create_table(...) block. We can't fully
# parse Python here, but a regex over sa.Column(...) lines covers the
# overwhelmingly common Alembic usage.
_ALB_CREATE_TABLE_BLOCK = re.compile(
    r"op\.create_table\(\s*['\"](\w+)['\"](?P<body>.*?)\)\s*\n",
    re.IGNORECASE | re.DOTALL,
)
_ALB_SA_COLUMN = re.compile(
    r"sa\.Column\(\s*['\"](\w+)['\"]",
    re.IGNORECASE,
)


def parse_alembic_file(path: Path, project: str = "") -> list[ChangeEvent]:
    """
    Parse an Alembic Python migration file and return ChangeEvents.

    Handles: create_table (with per-column events), drop_table,
    add_column, drop_column, alter_column, rename_table, create_index,
    drop_index, and raw op.execute() SQL.
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

    # Track the (start, end) spans of create_table blocks so we don't
    # double-count their inner sa.Column() calls as add_column events.
    create_table_spans: list[tuple[int, int]] = []
    for m in _ALB_CREATE_TABLE_BLOCK.finditer(upgrade_text):
        table = m.group(1)
        body = m.group("body")
        create_table_spans.append((m.start(), m.end()))
        events.append(ChangeEvent(
            entity_path=table,
            entity_type="table",
            change_type="create",
            diff=m.group(0)[:200],
            reasoning=source,
            project=project,
        ))
        # Emit per-column events for sa.Column(...) entries inside the block
        for col_match in _ALB_SA_COLUMN.finditer(body):
            col_name = col_match.group(1)
            events.append(ChangeEvent(
                entity_path=f"{table}.{col_name}",
                entity_type="column",
                change_type="add",
                diff=col_match.group(0),
                reasoning=f"defined in op.create_table('{table}') ({source})",
                project=project,
            ))

    def _inside_create_table(pos: int) -> bool:
        return any(start <= pos < end for start, end in create_table_spans)

    for m in _ALB_ADD_COLUMN.finditer(upgrade_text):
        # Skip sa.Column(...) calls that are part of an op.create_table block
        # — those are already covered above.
        if _inside_create_table(m.start()):
            continue
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

    # Plain create_table fallback — only fires for create_table calls that
    # didn't match the block pattern above (e.g. one-liners with no trailing
    # newline). Avoid double-counting by tracking already-seen tables.
    seen_create_tables = {ev.entity_path for ev in events if ev.change_type == "create"}
    for m in _ALB_CREATE_TABLE.finditer(upgrade_text):
        table = m.group(1)
        if table in seen_create_tables:
            continue
        seen_create_tables.add(table)
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

    # rename_table — emit BOTH the rename of the old name AND a create
    # event for the new name, so `selvedge blame {new_name}` after a
    # rename returns the rename context instead of "no history found".
    for m in _ALB_RENAME_TABLE.finditer(upgrade_text):
        old_name, new_name = m.group(1), m.group(2)
        events.append(ChangeEvent(
            entity_path=old_name,
            entity_type="table",
            change_type="rename",
            diff=m.group(0),
            reasoning=f"renamed to {new_name} ({source})",
            project=project,
        ))
        events.append(ChangeEvent(
            entity_path=new_name,
            entity_type="table",
            change_type="create",
            diff=m.group(0),
            reasoning=f"created via rename from {old_name} ({source})",
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
        events.extend(_parse_sql_text(raw_sql, source=source, project=project))

    return events


def _extract_upgrade_block(text: str) -> str:
    """
    Extract the body of the upgrade() function from an Alembic migration.
    Returns empty string if not found.
    """
    m = re.search(r"def upgrade\(\)[^:]*:(.*?)(?=\ndef |\Z)", text, re.DOTALL)
    return m.group(1) if m else ""
