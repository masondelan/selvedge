# Lightweight core audit

Reviewed October 17, 2026 against v0.3.17, base commit
`3b67b8f35b82206eacd8f85d9cab66284da5dd08`. Changes described as new below are
unreleased. This is a focused audit of core use, installation boundaries and
review output, not an exhaustive security or performance assessment.

**Does this make Selvedge more useful without making everyone's experience heavier?**

[DuckDB's design](https://duckdb.org/why_duckdb) is the reference: useful locally,
simple to embed, easy to start using, with added capabilities kept optional.
Selvedge does not inherit DuckDB's performance, testing depth or dependency
profile by adopting these principles. This does not call for replacing SQLite.

## Findings and decisions

| Area | Current evidence | Decision |
| --- | --- | --- |
| Local core | `SelvedgeStorage` saves and retrieves records through SQLite without MCP, Click, Rich or a model call. Top-level exports are lazy. | Preserve the existing library; no new engine or service is needed. |
| Installation | `pyproject.toml` requires MCP, Click and Rich and their dependencies for every normal install. Python 3.10 also needs Tomli for TOML settings. | This is the main packaging gap. Plan a smaller install with an explicit compatibility path; do not silently break current CLI/server installs. |
| Shared behavior | The CLI and GitHub Action already use `read_ledger` and `render_ledger`. The Action reads approved history at the trusted base commit. | Improve this shared path before building more interfaces. |
| Offline boundary | The new isolation test saves a rejection, restarts Python and retrieves the identical record with site packages unavailable and socket activity blocked. | Keep this as a regression test. It proves that flow, not every command or optional feature. |
| Network use | Interactive CLI commands can check PyPI for updates; telemetry is separately opt-in. | Describe these costs honestly. Local storage does not mean every entry point is network-free. |
| Review noise | Previously, a healthy run with no matching history could create a PR comment. | New behavior leaves the full job summary but creates no new comment. It refreshes an existing Selvedge comment so old context does not remain current. A broken chain still reports and fails. |
| Review usefulness | Existing output provides history, attribution labels and explicit revisions, but can be lengthy. Actor labels are self-reported; recorded reasons may be stale. | Next, test a compact explanation of relevant reasons and constraints with links to evidence. Missing history is unknown, never approval. |
| Optional features | Local semantic recall is an optional extra; the core makes no model calls. | Keep optional models, editor UI and remote services outside the basic storage/retrieval path. |

## Local startup sample

One machine, Python 3.12.14, SQLite 3.53.4, macOS 26.6.2 arm64; MCP 1.30.0,
Click 8.5.0, Rich 15.0.0. These are the installed audit versions, not new pins
or a compatibility claim. Each row measures complete fresh-process wall time,
including interpreter startup: 11 samples after one discarded warmup, warm OS
caches and no CPU isolation.

| Process work | Median |
| --- | ---: |
| Python only | 19 ms |
| `import selvedge` | 19 ms |
| Import public storage and event classes | 33 ms |
| Import CLI module | 65 ms |
| Import MCP server module | 230 ms |

These are import samples, not server readiness, query latency, memory usage or
end-to-end review timings. They support retaining the existing lazy boundary;
they do not establish a universal speed target or a comparison with DuckDB.
The audit did not measure peak memory, large repositories or editor overhead.
Measure those when work affects them.

To repeat the import sample from the repository root, use a development
environment with the declared dependencies installed, and record its Python,
platform and dependency versions alongside the output:

```sh
python - <<'PY'
import pathlib
import statistics
import subprocess
import sys
import time

root = str(pathlib.Path.cwd())
cases = {
    "python": "pass",
    "package": "import selvedge",
    "storage": "from selvedge import SelvedgeStorage, ChangeEvent",
    "cli": "import selvedge.cli",
    "mcp": "import selvedge.server",
}
for name, statement in cases.items():
    code = "import sys; sys.path.insert(0, sys.argv[1]); " + statement
    samples = []
    for index in range(12):
        start = time.perf_counter()
        subprocess.run([sys.executable, "-I", "-c", code, root],
                       check=True, capture_output=True, timeout=30)
        if index:
            samples.append(1000 * (time.perf_counter() - start))
    print(name, "median_ms=", round(statistics.median(samples), 3),
          "samples_ms=", [round(value, 3) for value in samples])
PY
```

Run the behavioral boundary checks with:

```sh
python -m pytest tests/test_public_api.py tests/test_ledger.py -q
```

The GitHub behavior is covered with a fake API and real temporary SQLite
databases. Native GitHub execution of the new behavior remains unverified.

## Implementation order

The [phase plan](../architecture.md#phase-225--lightweight-shared-core) owns the
remaining work and completion conditions. First protect the small shared core
and assess install costs; then make GitHub change review useful and quiet; then
prove one on-demand editor integration. Keep MCP as another way to use the same
records. Remote collaboration needs demonstrated demand and its own design.

The standing [engineering rule](../engineering-standards.md#lightweight-and-powerful)
requires substantial changes to explain their user benefit and added cost. A
plan, setup preset or passing unit test is not proof of a working integration.
