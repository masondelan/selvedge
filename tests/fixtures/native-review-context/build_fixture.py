"""Build the publication-safe synthetic fixture without consulting another store.

Run at the reviewed core revision with ``python -I build_fixture.py OUTPUT``.
The output must not exist. Fixed IDs and clocks make repeated builds identical
for the same core revision and Python/SQLite runtime; cross-version SQLite byte
reproducibility is not claimed. The committed bytes are the native test input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from selvedge.models import ChangeEvent  # noqa: E402
from selvedge.storage import SelvedgeStorage  # noqa: E402

FIXTURE_TIME = "2026-01-01T00:00:00Z"


def build_fixture(output: Path) -> str:
    """Create a new synthetic-only database and return its SHA-256 digest."""
    # Exclusive reservation also rejects a broken symlink. Never overwrite an
    # existing store or delete its sidecars to make this fixture fit.
    sidecars = [Path(str(output) + suffix) for suffix in ("-wal", "-shm", "-journal")]
    if any(path.exists() or path.is_symlink() for path in sidecars):
        raise ValueError("Fixture output has an existing SQLite sidecar")
    with output.open("xb"):
        pass
    records = json.loads(Path(__file__).with_name("records.json").read_text())
    with patch("selvedge.storage.utc_now_iso", return_value=FIXTURE_TIME), patch(
        "selvedge.migrations.utc_now_iso", return_value=FIXTURE_TIME
    ):
        store = SelvedgeStorage(output)
        store.log_event_batch(ChangeEvent(**row) for row in records)
    connection = sqlite3.connect(output)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("VACUUM")
    finally:
        connection.close()
    assert not any(Path(str(output) + suffix).exists() for suffix in ("-wal", "-shm"))
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main() -> None:
    """Create the explicitly named fixture, refusing an existing output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(build_fixture(args.output))


if __name__ == "__main__":
    main()
