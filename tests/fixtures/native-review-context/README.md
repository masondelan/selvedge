# Synthetic native review-context fixture

Every record in `history.db` is invented test data from `records.json`.
The fixture contains no imported project history, credentials or user data.
Do not substitute another database. The native workflow names this exact path;
it must never fall back to the repository's ordinary `.selvedge` store.

Build from core `af5964ef5cdd31c47a72f463c862c9aaff626ac9`:

```sh
python -I tests/fixtures/native-review-context/build_fixture.py /tmp/new-history.db
```

The output must not already exist. IDs and clocks are fixed. Byte-for-byte
repeatability is tested with the same core/Python/SQLite runtime; it is not a
claim about cross-version SQLite output. Only frozen, reviewed fixture bytes
are used in the native workflow. Runtime generation is deliberately absent.

Frozen `history.db`: 81,920 bytes, SHA-256
`7eec1c117c5825c35306e48726ab229c0b6f54222cd541d5ed0f2e13ecccba52`,
generated with Python 3.12.14 and SQLite 3.53.4. It has three events, an intact
four-record chain, no tool-call records and no path-migration records.

The database is intentionally committed as one synthetic test fixture, although
the repository ignores ordinary scratch `*.db` files. It is outside the wheel,
sdist and Smithery bundle allowlists. `probe.py`, `empty.py` and `unrelated.py`
do not need to exist in the base: they name synthetic paths for the probe PRs.

Native validation requires a separate review before this temporary workflow
and fixture reach `main`. Until actual runs are recorded, none is native evidence:

1. Create same-repository branch `release/native-review-context-probe` from the
   fixture-bearing `main`, adding only `tests/fixtures/native-review-context/probe.py`
   with a harmless comment. Open a PR to `main`; never merge the probe PR.
2. The read-only job must print `dry-run` and render two matching events in its
   summary. The comment job uses only `contents: read` and `pull-requests: write`.
   Its first Action invocation should print `created`, and the immediate second
   should print `unchanged`. Record the run URL, exact base/head and bot-comment
   URL; verify one comment and a body within 2,000 characters. Re-running an
   unchanged event should print `unchanged` twice.
3. On that same probe branch, replace the added probe file with only
   `tests/fixtures/native-review-context/empty.py`. The PR's full diff must no
   longer include `probe.py`. The next run should print `updated`, then
   `unchanged`, refreshing the previous comment to unknown/no matching history.
4. Create branch `release/native-review-context-empty` from the same base,
   adding only `empty.py`. On its fresh PR, the two write-capable invocations
   must both print `no-matching-history` and create no comment; the read-only
   summary remains available. This checks suppression independently of refresh.
5. Close both probe PRs and remove the temporary workflow, fixture directory
   and `tests/test_native_review_fixture.py` together after retaining the
   receipts, before tagging the release. Keep the composite dependency pin.
   Do not call these runs human-usefulness evidence or claim that client
   activation was validated.

Only two explicitly named same-repository branches are eligible. Forks and
ordinary PRs are skipped, there is no schedule or dispatch trigger, and the
write-capable job only runs after the read-only job succeeds. Workflows run the
trusted base checkout and its local Selvedge Action; no PR-head code runs.
The exact base SHA pins both the fixture and Action source. The composite
changes only its Python setup reference to the already-reviewed upstream
`6d4fd22b20465ccc0f7354476588e8df7c9ca0e0` (v7.1.0). The action runtime major,
Python selection and reporting behavior are unchanged. No credentials or
persistent repository settings are changed by this dependency pin.
