"""Tests for selvedge.importers — SQL DDL and Alembic migration parsers."""

import pytest
from pathlib import Path
from selvedge.importers import parse_sql_file, parse_alembic_file, import_path


# ---------------------------------------------------------------------------
# SQL parser — CREATE / DROP TABLE
# ---------------------------------------------------------------------------


def test_sql_create_table(tmp_path):
    """CREATE TABLE emits one event for the table and one per column."""
    f = tmp_path / "001.sql"
    f.write_text("CREATE TABLE users (id INTEGER PRIMARY KEY);")
    events = parse_sql_file(f)
    # 1 table event + 1 column event for `id`
    assert len(events) == 2
    assert events[0].entity_path == "users"
    assert events[0].entity_type == "table"
    assert events[0].change_type == "create"
    assert events[1].entity_path == "users.id"
    assert events[1].entity_type == "column"
    assert events[1].change_type == "add"


def test_sql_create_table_if_not_exists(tmp_path):
    f = tmp_path / "001.sql"
    f.write_text("CREATE TABLE IF NOT EXISTS orders (id INTEGER);")
    events = parse_sql_file(f)
    assert len(events) == 2
    assert events[0].entity_path == "orders"
    assert events[0].change_type == "create"
    assert events[1].entity_path == "orders.id"
    assert events[1].change_type == "add"


def test_sql_create_table_multi_column(tmp_path):
    """Multi-column CREATE TABLE emits a column event per column,
    skipping table-level constraints like PRIMARY KEY (id)."""
    f = tmp_path / "001.sql"
    f.write_text("""
        CREATE TABLE users (
            id INTEGER NOT NULL,
            stripe_customer_id VARCHAR(255),
            user_tier_v2 TEXT DEFAULT 'free',
            amount DECIMAL(10, 2) DEFAULT 0,
            PRIMARY KEY (id)
        );
    """)
    events = parse_sql_file(f)
    # 1 table + 4 columns (PRIMARY KEY clause is skipped, not treated as a column)
    assert len(events) == 5
    paths = [e.entity_path for e in events]
    assert paths == [
        "users",
        "users.id",
        "users.stripe_customer_id",
        "users.user_tier_v2",
        "users.amount",
    ]
    # The DECIMAL(10, 2) inner comma should NOT split the column —
    # `users.amount` is one entry, not two.
    assert "DECIMAL(10, 2)" in events[4].diff or "DECIMAL(10," in events[4].diff


def test_sql_create_table_blame_works_for_inline_columns(tmp_path):
    """A blame query against a column defined only in CREATE TABLE
    should return the create-time event — this was the headline gap
    in the import story before the per-column fix."""
    f = tmp_path / "0001_initial.sql"
    f.write_text("CREATE TABLE users (id INTEGER, email TEXT);")
    events = parse_sql_file(f)
    paths = {e.entity_path for e in events}
    assert "users.email" in paths


def test_sql_drop_table(tmp_path):
    f = tmp_path / "001.sql"
    f.write_text("DROP TABLE IF EXISTS old_sessions;")
    events = parse_sql_file(f)
    assert len(events) == 1
    assert events[0].entity_path == "old_sessions"
    assert events[0].change_type == "delete"


# ---------------------------------------------------------------------------
# SQL parser — ADD / DROP / RENAME / ALTER COLUMN
# ---------------------------------------------------------------------------


def test_sql_add_column(tmp_path):
    f = tmp_path / "001.sql"
    f.write_text("ALTER TABLE users ADD COLUMN stripe_id VARCHAR(255);")
    events = parse_sql_file(f)
    assert len(events) == 1
    assert events[0].entity_path == "users.stripe_id"
    assert events[0].entity_type == "column"
    assert events[0].change_type == "add"
    assert "stripe_id" in events[0].diff


def test_sql_add_column_without_column_keyword(tmp_path):
    f = tmp_path / "001.sql"
    f.write_text("ALTER TABLE payments ADD amount DECIMAL(10,2) NOT NULL DEFAULT 0;")
    events = parse_sql_file(f)
    assert len(events) == 1
    assert events[0].entity_path == "payments.amount"
    assert events[0].change_type == "add"


def test_sql_drop_column(tmp_path):
    f = tmp_path / "001.sql"
    f.write_text("ALTER TABLE users DROP COLUMN legacy_flag;")
    events = parse_sql_file(f)
    assert len(events) == 1
    assert events[0].entity_path == "users.legacy_flag"
    assert events[0].change_type == "remove"


def test_sql_rename_column(tmp_path):
    """Renaming a column emits two events so blame works under both names."""
    f = tmp_path / "001.sql"
    f.write_text("ALTER TABLE users RENAME COLUMN user_tier TO subscription_tier;")
    events = parse_sql_file(f)
    assert len(events) == 2
    assert events[0].entity_path == "users.user_tier"
    assert events[0].change_type == "rename"
    assert events[1].entity_path == "users.subscription_tier"
    assert events[1].change_type == "add"
    assert "renamed from" in events[1].reasoning


def test_sql_rename_table(tmp_path):
    """Renaming a table emits two events so blame works under both names."""
    f = tmp_path / "001.sql"
    f.write_text("ALTER TABLE old_name RENAME TO new_name;")
    events = parse_sql_file(f)
    assert len(events) == 2
    assert events[0].entity_path == "old_name"
    assert events[0].entity_type == "table"
    assert events[0].change_type == "rename"
    assert events[1].entity_path == "new_name"
    assert events[1].entity_type == "table"
    assert events[1].change_type == "create"
    assert "rename" in events[1].reasoning
    assert "old_name" in events[1].reasoning


# ---------------------------------------------------------------------------
# SQL parser — indexes
# ---------------------------------------------------------------------------


def test_sql_create_index(tmp_path):
    f = tmp_path / "001.sql"
    f.write_text("CREATE INDEX idx_users_email ON users (email);")
    events = parse_sql_file(f)
    assert len(events) == 1
    assert events[0].entity_type == "index"
    assert events[0].change_type == "index_add"


def test_sql_drop_index(tmp_path):
    f = tmp_path / "001.sql"
    f.write_text("DROP INDEX IF EXISTS idx_users_email;")
    events = parse_sql_file(f)
    assert len(events) == 1
    assert events[0].change_type == "index_remove"


# ---------------------------------------------------------------------------
# SQL parser — multiple statements, project tagging, reasoning
# ---------------------------------------------------------------------------


def test_sql_multiple_statements(tmp_path):
    f = tmp_path / "001.sql"
    f.write_text("""
    CREATE TABLE users (id INTEGER);
    ALTER TABLE users ADD COLUMN email VARCHAR(255);
    ALTER TABLE users ADD COLUMN stripe_id TEXT;
    DROP TABLE old_users;
    """)
    events = parse_sql_file(f)
    # CREATE TABLE users (id) → 2 events (table + id column)
    # ALTER ADD email           → 1 event
    # ALTER ADD stripe_id       → 1 event
    # DROP TABLE old_users      → 1 event
    assert len(events) == 5
    assert [e.change_type for e in events] == ["create", "add", "add", "add", "delete"]
    assert [e.entity_path for e in events] == [
        "users", "users.id", "users.email", "users.stripe_id", "old_users"
    ]


def test_sql_project_tagged(tmp_path):
    f = tmp_path / "001.sql"
    f.write_text("CREATE TABLE users (id INTEGER);")
    events = parse_sql_file(f, project="my-api")
    assert events[0].project == "my-api"


def test_sql_reasoning_contains_filename(tmp_path):
    f = tmp_path / "0023_add_stripe.sql"
    f.write_text("ALTER TABLE users ADD COLUMN stripe_id TEXT;")
    events = parse_sql_file(f)
    assert "0023_add_stripe.sql" in events[0].reasoning


def test_sql_empty_file(tmp_path):
    f = tmp_path / "empty.sql"
    f.write_text("")
    assert parse_sql_file(f) == []


def test_sql_no_ddl(tmp_path):
    f = tmp_path / "select.sql"
    f.write_text("SELECT * FROM users WHERE id = 1;")
    assert parse_sql_file(f) == []


# ---------------------------------------------------------------------------
# Alembic parser
# ---------------------------------------------------------------------------


def test_alembic_add_column(tmp_path):
    f = tmp_path / "001_add_col.py"
    f.write_text("""
def upgrade():
    op.add_column('users', sa.Column('stripe_id', sa.String(255), nullable=True))
""")
    events = parse_alembic_file(f)
    assert len(events) == 1
    assert events[0].entity_path == "users.stripe_id"
    assert events[0].change_type == "add"


def test_alembic_drop_column(tmp_path):
    f = tmp_path / "002_drop.py"
    f.write_text("""
def upgrade():
    op.drop_column('users', 'legacy_flag')
""")
    events = parse_alembic_file(f)
    assert len(events) == 1
    assert events[0].entity_path == "users.legacy_flag"
    assert events[0].change_type == "remove"


def test_alembic_create_table(tmp_path):
    f = tmp_path / "003_create.py"
    f.write_text("""
def upgrade():
    op.create_table('payments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
    )
""")
    events = parse_alembic_file(f)
    assert any(e.change_type == "create" and e.entity_path == "payments" for e in events)


def test_alembic_drop_table(tmp_path):
    f = tmp_path / "004_drop_table.py"
    f.write_text("""
def upgrade():
    op.drop_table('old_sessions')
""")
    events = parse_alembic_file(f)
    assert len(events) == 1
    assert events[0].entity_path == "old_sessions"
    assert events[0].change_type == "delete"


def test_alembic_alter_column(tmp_path):
    f = tmp_path / "005_alter.py"
    f.write_text("""
def upgrade():
    op.alter_column('users', 'email', existing_type=sa.String(100), type_=sa.String(255))
""")
    events = parse_alembic_file(f)
    assert len(events) == 1
    assert events[0].entity_path == "users.email"
    assert events[0].change_type == "modify"


def test_alembic_rename_table(tmp_path):
    """Renaming a table emits two events so the new name is queryable."""
    f = tmp_path / "006_rename.py"
    f.write_text("""
def upgrade():
    op.rename_table('user_profiles', 'profiles')
""")
    events = parse_alembic_file(f)
    assert len(events) == 2
    assert events[0].entity_path == "user_profiles"
    assert events[0].change_type == "rename"
    assert events[1].entity_path == "profiles"
    assert events[1].change_type == "create"
    assert "rename" in events[1].reasoning
    assert "user_profiles" in events[1].reasoning


def test_alembic_skips_downgrade(tmp_path):
    """Events in downgrade() should not be imported."""
    f = tmp_path / "007_migration.py"
    f.write_text("""
def upgrade():
    op.add_column('users', sa.Column('new_col', sa.String()))

def downgrade():
    op.drop_column('users', 'new_col')
""")
    events = parse_alembic_file(f)
    # Only the upgrade add_column should appear, not the downgrade drop_column
    assert all(e.change_type == "add" for e in events)
    assert len(events) == 1


def test_alembic_multiple_ops(tmp_path):
    f = tmp_path / "008_multi.py"
    f.write_text("""
def upgrade():
    op.create_table('invoices', sa.Column('id', sa.Integer()))
    op.add_column('users', sa.Column('invoice_id', sa.Integer()))
    op.add_column('users', sa.Column('billing_email', sa.String()))
""")
    events = parse_alembic_file(f)
    # create_table('invoices') → table event + invoices.id column event
    # 2x op.add_column         → 2 column events
    assert len(events) == 4
    paths = [e.entity_path for e in events]
    assert "invoices" in paths
    assert "invoices.id" in paths
    assert "users.invoice_id" in paths
    assert "users.billing_email" in paths


def test_alembic_project_tagged(tmp_path):
    f = tmp_path / "009.py"
    f.write_text("""
def upgrade():
    op.add_column('users', sa.Column('x', sa.String()))
""")
    events = parse_alembic_file(f, project="billing-service")
    assert events[0].project == "billing-service"


# ---------------------------------------------------------------------------
# import_path — directory walking, auto-detect
# ---------------------------------------------------------------------------


def test_import_path_single_sql_file(tmp_path):
    f = tmp_path / "schema.sql"
    f.write_text("CREATE TABLE users (id INTEGER); ALTER TABLE users ADD COLUMN email TEXT;")
    events = import_path(f)
    # CREATE TABLE → 2 events (table + id column); ADD COLUMN email → 1
    assert len(events) == 3


def test_import_path_directory_mixed(tmp_path):
    (tmp_path / "001.sql").write_text("CREATE TABLE users (id INTEGER);")
    (tmp_path / "002.py").write_text("""
def upgrade():
    op.add_column('users', sa.Column('email', sa.String()))
""")
    events = import_path(tmp_path, fmt="auto")
    # SQL CREATE TABLE → 2 events; alembic add_column → 1 event
    assert len(events) == 3


def test_import_path_sorted_by_name(tmp_path):
    (tmp_path / "002_second.sql").write_text("ALTER TABLE users ADD COLUMN b TEXT;")
    (tmp_path / "001_first.sql").write_text("CREATE TABLE users (id INTEGER);")
    events = import_path(tmp_path, fmt="sql")
    # 001 should come first
    assert events[0].entity_path == "users"
    assert events[0].change_type == "create"


def test_import_path_empty_directory(tmp_path):
    assert import_path(tmp_path) == []


def test_import_path_fmt_sql_skips_py(tmp_path):
    (tmp_path / "mig.sql").write_text("CREATE TABLE t (id INTEGER);")
    (tmp_path / "mig.py").write_text("""
def upgrade():
    op.add_column('t', sa.Column('x', sa.String()))
""")
    events = import_path(tmp_path, fmt="sql")
    # SQL CREATE TABLE → 2 events (table + id column); .py file is skipped
    assert len(events) == 2
    assert events[0].change_type == "create"
    assert events[1].entity_path == "t.id"
