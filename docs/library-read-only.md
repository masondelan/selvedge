# Read-only library access

The Python storage API supports an opt-in SQL read-only mode. Default construction remains writable.

```python
from pathlib import Path
from selvedge import SelvedgeStorage

reader = SelvedgeStorage(Path(".selvedge/selvedge.db"), read_only=True)
records = reader.get_history(entity_path="schema.sql")
```

Use an existing database created or migrated by the compatible Selvedge version. Construction checks the current migration ledger and required table-column shapes independently of physical column order; missing, older, newer, or incompatible stores fail without initialization or migration. This is not comprehensive verification of arbitrary or malicious SQLite schemas.

Known mutation methods raise `PermissionError`, including empty write batches. SQLite connections use `mode=ro` and `query_only`. The constructor accepts only a boolean for `read_only`. Returned rows retain existing dictionary semantics; they are not immutable result objects.

Committed WAL data is included. Each storage session uses a transaction whose snapshot starts at its first read. Separate calls can observe different committed versions; calling `get_history` followed by `get_prior_attempts` does not provide one shared snapshot.

SQLite may create or update WAL/SHM sidecars. This mode is not a guarantee of zero filesystem writes, an authorization boundary, or a sandbox. It does not prevent another process from changing/replacing the database after construction, and it does not revalidate schema or bind file identity on every call. Do not use immutable-mode or main-file-only copying as a substitute for reading live WAL.

No new runtime dependency, MCP tool, automatic capture, telemetry, network service, or dashboard is introduced.
