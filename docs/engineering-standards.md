# Engineering standards

Lightweight standard reviewed October 17, 2026; upstream support/security baseline
last reviewed October 15, 2026. These standards apply to design, code, documentation,
examples, the website, reviews, tests, dependencies and releases.

## Product values

Preserve the three [product values](architecture.md#product-values):

- **Easy to use:** make the common path short, defaults useful, errors actionable
  and documentation discoverable. Prefer fewer concepts and dependencies.
- **Robust:** protect data integrity, make failures observable and test recovery,
  concurrency and compatibility at the boundary affected by a change.
- **Developer focused:** fit existing developer workflows, with useful CLI and
  MCP interfaces, machine-readable output and explicit local data controls.

Every substantial change should explain which value it serves and any tradeoff.
New technology must solve a concrete problem; novelty alone is not a reason to
add a dependency, service or abstraction.

**Selvedge is agent-agnostic.** Any compatible agent may use the same core.
Agent/model labels identify contributors; they do not select storage semantics
or gate core features. Put client-specific behavior in optional adapters. Keep
named examples and truthful compatibility guides; never equate setup presets
with the full set of compatible clients.

## Lightweight and powerful

**Does this make Selvedge more useful without making everyone's experience heavier?**

Ask this for every product and engineering decision. Borrow
[DuckDB's emphasis on simple installation, embedded use, portability and optional
extensions](https://duckdb.org/why_duckdb); this is a design reference, not a claim
of certification, equal performance or identical dependencies.

- Keep everyday storage and retrieval local, deterministic and usable without
  an account, model call, network connection or running MCP server. The Python
  library is a first-class path. Preserve explicit data ownership and exports.
- Keep GitHub, editor and MCP integrations thin: reuse the same decisions and
  retrieval behavior. Put their network, authentication and UI needs at the
  integration boundary. Add abstractions only when an actual second use needs them.
- Make added capabilities optional and bounded: limit background work, output,
  notifications and new dependencies. Quiet no-match output must still preserve
  diagnostics and distinguish unknown history from approval.
- Evaluate the whole installation as well as runtime imports. The current
  package requires MCP, Click and Rich even for library users; lazy imports do
  not make those installation dependencies optional. A smaller install needs a
  separate compatibility plan, not a silent change to existing installs.

For a substantial change, explain the user task, expected or observed benefit,
added default versus optional cost, and the smallest approach that serves it.
Measure the costs the change affects: dependency/install size, startup time,
memory, query time, stored bytes, context/output size, setup steps or notification
volume. Record the command, environment, baseline and result; do not invent a
universal performance target or call a single-machine sample a benchmark win.
Existing tests and CI checks remain required. A typo or routine documentation
correction does not need a new measurement exercise.

The core isolation test runs a real save/reopen/retrieve flow without installed
third-party packages and with Python socket activity blocked. Preserve that boundary as
integrations grow. Interactive CLI update checks and opt-in telemetry are
separate documented behavior; this rule does not claim all entry points are
network-free. See the [current audit](design/lightweight-core-audit.md) and
[implementation phases](architecture.md#phase-225--lightweight-shared-core).

## Implementation and validation

- Follow the type, deterministic-core, storage and output conventions in
  [CLAUDE.md](../CLAUDE.md). Validate inputs at boundaries and preserve existing
  data and documented behavior when changing schemas or interfaces.
- Reproduce behavior bugs and add meaningful regression coverage. Test MCP
  through the protocol, CLI through commands, and persistence with temporary
  databases. Cross-client coverage must include identities outside setup presets.
- Run the relevant tests, Ruff, mypy and MCP schema budget before merging core
  changes. Keep the full CI suite and coverage gate. For site changes, use the
  lockfile (`npm ci`), tests, a production build and link/metadata checks; inspect
  affected pages and controls at narrow and wide widths.
- Review the exact final diff, including generated assets and package contents.
  Changed code invalidates review of that portion. Record checks actually run,
  skipped checks and known limitations; do not present inspection as execution.

## Supported technology and dependencies

Use an upstream-supported stable runtime for new development and deployments.
Check [Python's support table](https://devguide.python.org/versions/) and
[Node's release policy](https://nodejs.org/en/about/previous-releases) before a
release or runtime change. Test supported runtime additions before claiming
compatibility. Use current security patches within the chosen supported line.

The current package still permits Python 3.10 for compatibility. Upstream ended
3.10 support on October 1, 2026; passing compatibility tests does not restore
security support. Use Python 3.11–3.14 for new installations. The 0.3.17 patch
release retains the 3.10 compatibility floor; its release notes document that
decision. Revisit the floor before the next versioned release rather than
silently changing it. Python 3.15 is released upstream but has not yet been
added to Selvedge's tested runtime matrix.

Review dependency updates and advisories regularly; core's weekly Dependabot
configuration covers Python and Actions. Evaluate major upgrades against actual
API and protocol changes. Keep runtime dependencies small and site lockfiles
committed. A version pin is not a substitute for maintaining it.

## Security, CI and releases

Follow [GitHub's workflow security guidance](https://docs.github.com/en/actions/reference/security/secure-use):
pin external actions to upstream full commit SHAs with readable version comments,
grant only necessary token permissions, and keep untrusted PR code away from
privileged jobs. Pass variable inputs through quoted environment variables,
not by interpolating them into shell source. Verify downloaded build executables
and source archives against recorded checksums before executing them.

Use [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/) and
retain/verify [publication attestations](https://docs.pypi.org/attestations/).
Follow the [release checklist](releasing.md): inspect package allowlists, record
the source revision and artifact hashes, verify each distribution channel and
verify the live website separately. Preserve rollback targets. Do not call a
build reproducible unless an independent rebuild comparison demonstrates it.

Treat local MCP processes as installed software with the permissions of their
execution environment. [The MCP trust model](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/SECURITY.md)
does not make stdio a sandbox. Document actual data flows and boundaries; do not
equate local storage with an agent provider never receiving retrieved content.
Review transport changes against the current [MCP specification](https://modelcontextprotocol.io/specification/latest)
and SDK behavior. Claim only the versions, transports and capabilities validated.

## Documentation and keeping these rules current

Lead with the user's task and a working example. Keep general onboarding
agent-neutral, show optional integration details where useful, and state
compatibility limits. Synchronize generated prompts, manifests, site examples
and CLI help when their source changes. Separate shipped behavior from plans,
synthetic experiments from real-world evidence, and verified facts from claims.

The maintainer reviews this baseline at releases and when an upstream support,
security or protocol change affects Selvedge. Update the reviewed date, primary
source links and applicable checks together. Record unresolved gaps with an
owner and next checkpoint; a standards document is not proof that a control ran.
