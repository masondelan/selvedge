> **Mirror of the Notion [growth master plan](https://app.notion.com/p/387e4352eff381aa92b8f815752d5abb); Notion is the working copy.**
> This repo file is the versioned snapshot — edit the Notion page, then re-mirror here. The codebase repo is canonical for *what shipped* ([`CHANGELOG.md`](../CHANGELOG.md)); this plan is the *growth surface* layered on top.

# 📈 Growth master plan — humans + agents

## ✅ Progress log — updated 2026-06-27 (reconciled vs. Distribution DB)

Status re-checked against the 📡 Distribution DB (source of truth) on 2026-06-27. No new code shipped since v0.3.9; this pass corrects two drift items and re-baselines the open network surfaces. **A ready-to-run Claude Code prompt for the next batch lives at `launch/listings/CLAUDE-CODE-PROMPT-2026-06-27-network.md`.**

**Corrections to the 2026-06-22 log:**

- **One-click install badges (Pillar 2B #4) — ⚠️ REGRESSED, not live.** Commit `56d0615` (2026-06-25) removed both editor badges from the README: the `cursor://` deeplink dead-ends from GitHub's web README. Re-do is queued in the next batch — put the buttons on selvedge.sh (where deeplinks resolve in-browser) and link to them from the README.
- **`SECURITY.md` — ✅ DONE.** It exists in-repo (added 2026-06-11). The Anthropic plugin-directory row's "`SECURITY.md` follow-up" note is stale; only the directory *review* itself is still pending.

**Distribution DB snapshot (2026-06-27): 9 Live · 1 Merged · 2 Submitted · 5 Open PR · 3 Staged · 2 Not started.**

- **Live (9):** Official MCP Registry, Smithery (98), Glama, mcpservers.org, PyPI, Self-marketplace, GitHub Actions Marketplace (`selvedge-coverage-check`) — all v0.3.9-synced.
- **Merged (1):** punkpeye/awesome-mcp-servers (v0.3.7 update PR #7657 still open).
- **Submitted, awaiting review (2):** Cline MCP Marketplace (#1851), Anthropic plugins-community.
- **Open PR (5):** the awesome-list PRs — hesreallyhim, rohitg00, jqueryscript, MCPHubCloud, jamesmurdza — open since ~2026-05-26.
- **Staged (3):** PulseMCP, LobeHub, mcp.so — drafts exist but are **stale at v0.3.4 / no Agent Trace**; refresh-then-submit is queued.
- **Not started (2):** Docker MCP Catalog (high — Claude Code drafts the PR) and MCPMarket.com.

**Next batch (the Claude Code prompt above — network expansion):** Docker MCP Catalog PR · refresh the 3 staged drafts to v0.3.9 + Agent Trace · MCPMarket draft · fix the regressed install badges (site-side) · per-client config recipes (Cline/Continue/Windsurf/Cursor) · refresh the 5 awesome-list PR drafts · mirror this plan to `docs/growth-master-plan.md`. **Mason-only tail:** submit the forms/PRs, named outreach, newsletter pitches, Reddit karma earn. **Cowork-task tail:** stand up the AI mention-share probe + extend the registry freshness sweep.

---

## ✅ Progress log — updated 2026-06-22 (post v0.3.9)

Reconciling the plan against shipped reality. Current listing status lives in the 📡 Distribution DB (source of truth, refreshed 2026-06-22); this log captures the growth-relevant work now **done** so the pillars below read against reality.

**Shipped / live:**

- **Agent Trace producer (Pillar 2B #1)** — `selvedge export` / `import --format agent-trace` shipped in **v0.3.9** (Agent Trace v0.1.0), pulled forward from Phase 3.2. The `/compare/agent-trace` page is corrected to the real v0.1.0 shape and live; `cursor/agent-trace#32` raised. The single biggest ecosystem move is now real, not just claimed in the README.
- **GitHub Actions Marketplace (Pillar 2B #2)** — `selvedge-coverage-check` Action **published** (`marketplace/actions/selvedge-coverage-check`). New Distribution DB row added (Live).
- **One-click install badges (Pillar 2B #4)** — ⚠️ **regressed 2026-06-25** (removed from README; `cursor://` dead-ends from GitHub web). See the 2026-06-27 log above — re-do queued (site-side deeplinks).
- **`/llms.txt` + `/llms-full.txt` + `/prompt-block` (Pillar 1B / 3C)** — all live at selvedge.sh.
- **Programmatic SEO (Pillar 3B)** — `/mcp/<client>` and `/compare/selvedge-vs-<tool>` pages live.
- **Expanded JSON-LD (Pillar 3C)** — `TechArticle` + `HowTo` schema shipped.
- **Cline MCP Marketplace (Pillar 2A)** — **Submitted** (`cline/mcp-marketplace#1851`, awaiting review) — was "Net-new" in the 2A table below.
- **Smithery** — republished at **v0.3.9**; listing description now names the Agent Trace export.

**v0.3.9 channel parity (verified 2026-06-22):** PyPI 0.3.9 · Official MCP Registry 0.3.9 (`isLatest`) · Smithery 0.3.9 · site changelog/roadmap/cli synced · Notion 🗺️ Roadmap + 🚀 Releases auto-mirrored (notion-sync Action green).

**Still net-new / open** (unchanged below): Docker MCP Catalog, mcp.so, LobeHub, MCPMarket, PulseMCP claim; the AI mention-share probe; the staged/open awesome-list PRs; the newsletter pitches.

> Note: `sourcegraph/awesome-code-ai` (a held-back awesome-list target) was found **archived / read-only** on 2026-06-22 — dropped, not pursued. It was never a Distribution DB row.

---

> **What this is.** The net-new growth surface layered on top of the existing strategy stack. It does **not** replace the [engagement strategy](https://app.notion.com/p/385e4352eff381a5befcf79d84c73bb6) (steady-state loops + weekly cadence), [strategy 2026 q3](https://app.notion.com/p/385e4352eff381e3bf4bf1e3c8af3531) (quarter plan), or [top-priority actions](https://app.notion.com/p/385e4352eff381f3a304da4aee192ece) (launch-window checklist). It **adds** four things those docs don't cover in depth: (1) agent/LLM discovery — making coding agents *recommend and deploy* Selvedge; (2) ecosystem + integration engineering; (3) a technical-SEO content engine; (4) a 30/60/90 wired to agentic execution. Everything new here is meant to be *worked*, not shelved.

**North star.** Selvedge wins when, asked *"what MCP server tracks why my AI agent changed code,"* both a developer **and** their coding agent name Selvedge — and the agent can install it in one step without leaving the IDE.

**Resourcing assumption (locked).** $0 external spend. No ads, no paid SEO/GEO tools, no paid directory tiers. Execution is agentic: 🤖 Cowork scheduled tasks, ⌨️ Claude Code PRs, 🎨 Claude design, with 🧑 Mason as human-in-the-loop for merges, form submissions, and named outreach. Every action below is tagged with its executor.

*Drafted 2026-06-22. Sibling of the reference docs under Selvedge — Internal. Canonical copy to land at `docs/growth-master-plan.md` if/when mirrored.*

---

## 0 · How this layers on what already exists

| Layer | Doc | Role | This plan's relationship |
|-------|-----|------|--------------------------|
| Strategy | long-term thesis | Why Selvedge exists | Inherits it |
| Quarter | strategy 2026 q3 | This quarter's bets | Extends the distribution pillar |
| Cadence | engagement strategy | Weekly loops + channels | Unchanged; feeds it new plays |
| Launch ops | top-priority actions | Daily checklist | Picks up its open threads |
| Growth surface | this page | Agent discovery + ecosystem + SEO + 30/60/90 | Net-new |

The engagement strategy already nails the *human cadence* (awareness/conversion/retention loops, dev.to arc, X build-in-public, weekly dashboard). This plan assumes all of that keeps running and concentrates on the surfaces it under-serves — above all, the **agent/LLM discovery layer**, which is where an MCP server lives or dies in 2026.

---

## Pillar 1 · The dual-core adoption strategy (humans vs. agents)

Two audiences discover Selvedge through completely different mechanics. A human reads a blog post and stars a repo. An agent reads a registry, a config file, or its own context window. Optimize both cores separately.

### 1A · Human adoption — beyond the current channels

The current channels (MCP directories, dev.to, X, one Show HN) are correct but thin on **third-party voices** and **high-intent niches**. The reframe: the bottleneck isn't *more posting by Mason* — it's getting Selvedge in front of people already feeling the exact pain, in venues where someone *other than the founder* vouches for it.

**Niche spaces to penetrate (free, high-intent):**

- **MCP / agent-tooling inner circles.** The Latent Space Discord (most active AI-engineer community), Anthropic Discord `#mcp` / `#claude-code`, and the Cursor / Cline / Windsurf community servers. These are where "which MCP server for X" gets asked daily. Value-first only — answer the pain, link Selvedge only when it's the literal answer.
- **Curated dev-tool newsletters (earned placement, $0).** They accept tips/submissions and reach the exact audience: **TLDR AI** (~1.1M devs, surfaces OSS releases), **Latent Space / AINews** (open-source roundups), **Future Tools** by Matt Wolfe (free directory submission at futuretools.io), **Console.dev** (curated dev tools, submission form), **The Changelog** (news + podcast pitch), **Cooperpress** titles (Node Weekly, etc.), **Pointer.io**, **daily.dev** (submit selvedge.sh as a source — devs upvote). Pitch the *wedge* (`prior_attempts`: agents that check before repeating a mistake), never the version bump.
- **Developer syndicates / aggregators.** daily.dev source + squad; **IndieHackers** build-in-public; **Hashnode** (cross-post the dev.to flagship, canonical-tagged); **Lobsters** (invite-only — ask a contact in MCP circles; high-signal audience); a *second* **Show HN** built around the demo video and the wedge, not the project as a whole.

**Bypassing the Reddit karma bottleneck — organically, no farming:**

1. **Earn comment karma by being useful, ~15 min/day.** Answer real questions in r/ExperiencedDevs, r/ChatGPTCoding, r/ClaudeAI, r/LocalLLaMA, r/mcp, r/devtools with zero links. Comment karma compounds fast and clears most gates within ~2 weeks.
2. **Start where the gates are low.** r/mcp and r/ClaudeAI are lenient and on-topic; lead with a teardown ("how I track *why* my agent changed a column"), not a launch.
3. **Let someone else post the link.** For a karma-limited founder this is the strongest play: make the artifact (demo gif, flagship post) good enough that a third party submits it. Seed that by being the most useful voice in threads about AI-code maintainability.
4. **Win the Reddit SERP without posting.** Reddit threads rank in Google *and* in LLM retrieval. A great value-first *comment* on an already-ranking thread captures the same intent as a post you can't make yet.

### 1B · Agent discovery & recommendation (LLMO) — the core net-new bet

There are exactly **three** ways a coding agent or LLM comes to use or suggest Selvedge. Each has a different optimization.

**Mechanism 1 — In-session tool availability (Selvedge's unfair advantage).** The agent already has Selvedge wired (setup wizard / Claude Code plugin / `mcp.json`) and the canonical instruction block in `CLAUDE.md` / `.cursorrules` tells it to *call `prior_attempts` before editing an entity with history*. **No other category player ships its own usage instructions into the repo.** Treat that prompt-block as a first-class growth artifact, not a config detail:

- Lead with the imperative and the payoff in the first line; keep it token-cheap (agents drop bloated blocks, and users delete what costs context).
- A/B the wording on dogfood repos; measure whether agents actually call the tool (coverage via `selvedge stats`).
- Publish it as a standalone, copy-pasteable, indexable page: [**selvedge.sh/prompt-block**](https://selvedge.sh/prompt-block) (already floated in q3 as the "Trojan-horse" page — elevate it here). It is simultaneously a human conversion asset, an SEO page, and an LLM-retrieval target.

**Mechanism 2 — Client-side discovery surfaces (registries the agents read).** Agents and their IDEs increasingly read registries/marketplaces directly: the official MCP Registry (clients consume its feed), Smithery (CLI install), the Cline in-IDE marketplace, the Windsurf marketplace, the Docker Desktop MCP Toolkit, and Cursor's directory + deeplink install. Presence + a clean description + *one-step install* means the agent (or its user) adds Selvedge without leaving the editor. Listing mechanics are Pillar 2.

**Mechanism 3 — Model world-knowledge + retrieval (true LLMO).** When a dev asks ChatGPT / Claude / Perplexity / Gemini *"what MCP server tracks why my AI changed code / git blame for AI,"* the model answers from (a) its training corpus and (b) live retrieval. Win both:

- **Corpus presence.** LLMs are fact-extractors. Maximize the count of high-signal, durable pages that state the *same clean fact* — *Selvedge is the MCP server that captures why AI agents change code, entity-level, captured live* — across GitHub (stars + README), awesome-lists, dev.to/Hashnode, the comparison page, and third-party blogs. Repetition + consistency across indexed sources is the ranking signal.
- **Retrieval readiness (B2A).** Ship **/llms.txt** and **/llms-full.txt** at selvedge.sh — confirmed absent/empty as of 2026-06-22, so this is a clean ~1hr win. Keep docs server-side-rendered (Astro/Starlight already are) and AI crawlers unblocked (resolved per the SEO notes). Expand structured data beyond the shipped `SoftwareApplication` schema: `TechArticle`, `HowTo`, and a machine-readable **facts block** (what it is / install / when to use / alternatives) that's trivial to lift.
- **Be the answer, not an entry.** Build the single page that *directly answers* the high-intent question in dense, comparison-first, fact-first format — the structure GEO research shows models quote verbatim.
- **Measure it (the $0 GEO proxy).** Monthly, prompt 4 models with the target questions and log whether Selvedge is named vs. competitors. Paid tools (Profound, Scrunch) automate this; we do it by hand — see Pillar 4's *AI mention-share probe*.

**Documentation architecture for agents (build order):** `/llms.txt` (curated link map) → `/llms-full.txt` (concatenated docs) → the **prompt-block page** (written to be pasted into an agent) → one canonical one-liner repeated *identically* on README, site meta, and every registry. Optional later: serve the docs themselves as an llms.txt-backed MCP (mcpdoc / Mintlify-style) so agents can pull setup steps mid-task.

---

## Pillar 2 · Ecosystem & integration engineering

### 2A · Registry / marketplace coverage map

Statuses are from project memory as of 2026-06-22 — **reconcile against the 📡 Distribution DB before acting** (it's the source of truth; this table is the gap-finder). Net-new targets in **bold**.

| Surface | Type | Why it matters | Status / action |
|---------|------|----------------|-----------------|
| Official MCP Registry | MCP registry | The feed clients read to discover servers | Live (CI publishes `server.json`) — keep current |
| Smithery | Marketplace | Docker-Hub of MCP; CLI one-step install | Live (98/100) — maintain |
| Glama | MCP registry | Verified/Claimed tiers, large index | Live — maintain `glama.json` |
| mcpservers.org | MCP registry | Community directory | Live |
| PyPI | Package index | `pip install selvedge` | Live |
| Anthropic plugins-community | Plugin directory | Highest-authority MCP slot | Submitted/pending |
| punkpeye/awesome-mcp-servers | Awesome list | Canonical awesome-list | Merged |
| **Cline MCP Marketplace** | Marketplace | In-IDE install for millions of Cline users | ✅ **Submitted** — `cline/mcp-marketplace#1851`, awaiting review (repo link + 400×400 PNG + `llms-install.md` provided 2026-06-22). |
| **Docker MCP Catalog** | Marketplace | Lands in Docker Desktop MCP Toolkit ~24h after merge | **Net-new** — PR to `docker/mcp-registry`. **High.** |
| **PulseMCP** | MCP registry | Hand-reviewed; quality-filtered discovery | **Net-new** — likely already crawled; *claim* • fix description |
| **mcp.so** | MCP registry | Largest third-party directory (~20k) | **Net-new** — submit GitHub issue |
| **LobeHub MCP** | Marketplace | Separate audience | **Net-new** — verify/submit |
| **MCPMarket.com** | Marketplace | 10k+ servers, dev-tools category | **Net-new** — verify/submit |
| Staged awesome-list PRs | Awesome list | rohitg00/awesome-claude-code-toolkit, hesreallyhim + jqueryscript/awesome-claude-code, MCPHubCloud/awesome-mcp, jamesmurdza/awesome-ai-devtools | Push to merge (from top-priority-actions) |

**Listing hygiene (the rule that makes listings compound):** one canonical one-liner everywhere; keep version + tool count synced on every surface; every listing links back to selvedge.sh. Inbound links feed *both* SEO and the LLM corpus — a listing is a discovery surface **and** a fact-repetition for Mechanism 3.

### 2B · Upstream integration engineering — bake Selvedge into existing workflows

The highest-leverage adoption isn't a listing someone has to find — it's Selvedge already present in a toolchain a developer uses. Ranked by leverage:

1. **Agent Trace producer (Cursor + Cognition standard).** Ship `selvedge export --format agent-trace` and open a PR / reference example to the Agent Trace repo as a compatible *producer*. Surfaces Selvedge to the whole alliance (Cloudflare, Vercel, Google Jules, Amp, OpenCode, git-ai). The README already *claims* interop — making it real and visible is the single biggest ecosystem move. (Decision-gated in q3 open questions; this plan recommends pulling it in.) **[Shipped v0.3.9.]**
2. **GitHub Actions Marketplace — `selvedge-coverage-check`.** Wrap `scripts/coverage_check.py` as a published Action. Teams add it to CI; every workflow file that references it is a durable, indexed mention — and it's perfectly on-thesis (provenance in CI). Cheap, net-new. **[Shipped.]**
3. **Per-client config recipes as upstream docs/PRs.** Contribute "add Selvedge" examples to where Cline / Continue / Windsurf / Cursor community MCP configs live. Each is a durable inbound link + a discovery surface inside the client's own docs.
4. **One-click install badges.** Add "Add to Cursor" and "Install in VS Code" deeplink buttons to the README and site. Both clients support deeplink MCP install — converts a looker into an installer in one click (closes the conversion gap the engagement strategy flags).
5. **Homebrew formula + `npx` shim.** `brew install selvedge` and an `npx selvedge` path widen reach beyond pip and are *themselves* indexed discovery surfaces. (Already in q3 "later" — keep.)
6. **pre-commit hook + devcontainer feature.** Publish a pre-commit hook and a devcontainers "feature" so Selvedge drops into any repo's standard tooling config. (`selvedge setup` already supports devcontainer `postCreateCommand`.)
7. **docs-as-MCP (optional, later).** Expose selvedge.sh docs via an llms.txt-backed MCP so agents pull setup steps mid-task — turns the docs into an agent-queryable surface.

---

## Pillar 3 · High-impact content & technical SEO

### 3A · Keyword / intent map

Deliberately high-intent and low-competition. Crucially, **the same phrases are the LLMO prompts from Pillar 1B** — what a developer types into Google *is* what they type into ChatGPT. One content asset, two engines.

- **Problem-aware:** "why did my AI agent change this code", "track AI code changes", "git blame for AI / for AI agents", "AI code attribution", "agent context lost after session", "who wrote this code AI or me".
- **Solution / category-aware:** "MCP server for codebase memory", "MCP server change tracking", "AI coding agent memory", "agent reasoning audit trail", "codebase provenance AI", "structured vs freeform agent memory".
- **Comparison / eval:** "best MCP servers for Claude Code", "Selvedge vs AgentDiff", "Agent Trace tools", "MCP server to track decisions".
- **Branded long-tail:** "selvedge mcp", "selvedge prior_attempts", "selvedge setup".

### 3B · Five high-velocity content concepts

Each is drafted by ⌨️ Claude Code, edited + shipped by 🧑 Mason. Format is engineered for organic search **and** verbatim LLM extraction (dense, fact-first, comparison tables).

1. **"git blame can't tell you *why* your AI wrote that. Here's what can."** — dev.to flagship + basis for a second Show HN. Target: *git blame for AI*. Hook: the `user_tier_v2` cold-open from the README, expanded into a real reproduction, closing on the `prior_attempts` demo. **The canonical corpus-anchor** — the page you most want LLMs to quote. Cross-post Hashnode (canonical → selvedge.sh).
2. **"I gave my coding agent long-term memory with one MCP server (and a 4-line `CLAUDE.md` block)."** — tutorial. Target: *AI coding agent memory / MCP server memory*. Hook: copy-paste setup + the prompt-block. Doubles as the narrative for the prompt-block Trojan page. Highest install-intent of the five.
3. **"Structured vs. freeform agent memory: Selvedge vs. Obsidian + Claude Code."** — comparison deep-dive (already staged in memory as the Obsidian dev.to piece). Positions *complementary, not adversarial*. Comparison tables are the most LLM-liftable format there is.
4. **"What a production-ready MCP server actually has to handle."** — hardening field-notes (concurrency, schema migrations, the `^fixed?$` regex bug, the SQLite version matrix). Target: *build / production MCP server*. Audience = people **building or evaluating** MCP servers → earns credibility + backlinks from *other MCP authors* (whose pages LLMs read). Already in the engagement backlog — elevate it.
5. **"We tried to add this column before: teaching agents to check `prior_attempts`."** — case-study on the wedge, built on the reverted-`auth_token` story. Target: *avoid AI agent repeating mistakes*. Pair with the demo video. The single best "why this is novel" artifact for humans **and** models.

**Programmatic SEO seam (net-new, fully agentic).** Generate small, genuinely-useful template pages from one data file: `selvedge.sh/mcp/<client>` ("Set up Selvedge with Cursor / Cline / Windsurf / Claude Code / Continue") and `selvedge.sh/compare/selvedge-vs-<tool>`. Each targets a real long-tail query and an LLM-retrieval hit. ⌨️ Claude Code generates and maintains them from the data file. Keep them substantive (real per-client steps) — not doorway spam. **[Shipped.]**

### 3C · Technical SEO / crawl + retrieval readiness

**Already shipped** (per q3 / SEO notes): comparison page, FAQ JSON-LD, sitemap, robots.txt, `SoftwareApplication` schema, GSC verified (Bing pending), AI crawlers unblocked.

**Net-new:**

- **/llms.txt + /llms-full.txt** — the B2A surface (confirmed absent as of 2026-06-22). **[Shipped.]**
- **Expand JSON-LD:** `TechArticle` per article, `HowTo` on tutorials, `BreadcrumbList`. Keep `FAQPage` for LLM extraction even though Google sunset FAQ rich results (2026-05-07) — the SERP payoff is gone but the machine-readable facts still help retrieval.
- **Consistency as a ranking signal:** one identical one-liner on README, site meta description, every registry, and social. LLMO rewards repetition of the same fact.
- **Canonical tags** on all cross-posts (dev.to / Hashnode / Medium → canonical to selvedge.sh) so authority consolidates on the owned domain.
- **Internal linking + OG images:** every article links to the comparison page + install with consistent anchor text; per-article OG images for shareability.

---

## Pillar 4 · Execution roadmap & logistical playbook

Executor legend: 🤖 Cowork scheduled task · ⌨️ Claude Code PR · 🎨 Claude design · 🧑 Mason (merge / submit / named outreach).

### 4A · 30 / 60 / 90

**Days 0–30 — instrument + take the easy ground.**

- Ship `/llms.txt` + `/llms-full.txt` ⌨️→🧑
- Publish [**selvedge.sh/prompt-block**](https://selvedge.sh/prompt-block) Trojan page ⌨️🎨→🧑
- **Cline marketplace** submission (`llms-install.md` + 400×400 PNG) ⌨️🎨→🧑
- **Docker MCP Catalog** PR ⌨️→🧑
- Claim **PulseMCP**; submit **mcp.so**, **LobeHub**, **MCPMarket** 🤖 draft → 🧑 submit
- Push the staged awesome-list PRs to merge 🧑
- Stand up the **AI mention-share probe** (monthly) 🤖
- Ship content #1 (git-blame flagship) + #5 (`prior_attempts` case study) ⌨️→🧑
- Begin the Reddit value-first karma earn (~15 min/day) 🧑

**Days 31–60 — integrations + content engine.**

- **Agent Trace** `export` + upstream PR/example (decision-gated) ⌨️→🧑
- **GitHub Actions Marketplace:** `selvedge-coverage-check` ⌨️→🧑
- Programmatic SEO: `/mcp/<client>` + `/compare` pages from a data file ⌨️🎨→🧑
- Content #2 (memory tutorial) + #3 (Obsidian comparison) ⌨️→🧑
- "Add to Cursor / VS Code" deeplink badges ⌨️→🧑
- Newsletter pitches: TLDR AI, Future Tools directory, Console.dev, Changelog 🤖 draft → 🧑 send
- Homebrew formula + `npx` shim if bandwidth ⌨️

**Days 61–90 — compound + measure.**

- Content #4 (production-MCP hardening) → seed backlinks from MCP-author community ⌨️→🧑
- Second **Show HN**, built on the demo + wedge (not the whole project) 🧑
- pre-commit hook + devcontainer feature ⌨️
- Re-run the AI mention-share probe; compare to day-30 baseline 🤖
- Distribution DB audit: every surface Live or with a logged reason 🤖
- Pitch **plugins-official** + Agent Trace alliance contacts now that proof exists 🧑
- Fold what worked into the engagement strategy steady-state cadence 🧑

### 4B · New recurring agentic tasks

These fit the existing single-dashboard architecture — add sections, don't spawn many tasks.

- **AI mention-share probe** — monthly. Prompt ChatGPT / Claude / Perplexity / Gemini with the 6 target questions; log whether Selvedge is named + which competitors are. Appends to a scoreboard. The LLMO KPI engine, $0.
- **Registry/listing freshness sweep** — extend the existing monthly directory audit to cover the net-new surfaces and verify the canonical one-liner + version are synced everywhere.
- **llms.txt / schema drift check** — when README/CHANGELOG change, flag whether `/llms.txt` + JSON-LD facts need re-sync (mirrors the existing notion-sync discipline).

### 4C · KPIs — what actually signals conversion

Additive to the engagement-strategy KPIs. North star: **weekly active installs** (once telemetry lands). Everything else is a proxy.

| Funnel | Metric | Healthy signal |
|--------|--------|----------------|
| Human | Stars/week · install-to-star ratio · dev.to+Hashnode cumulative views · external issues/PRs | Up week-over-week; first external PR = first real user |
| Agent / LLMO | **AI mention-share** (% of target prompts across 4 models naming Selvedge) | The headline new KPI — trending up |
| Agent / LLMO | **Registry presence** (Live listings ÷ target surfaces) | Approaching 100% |
| Agent / LLMO | **One-step installability** (# surfaces with one-click/command install) | Cline + Smithery + Docker + Cursor deeplink all green |
| Agent / LLMO | **llms.txt fetches / AI-crawler hits** (GPTBot, ClaudeBot, PerplexityBot, Google-Extended) | Non-zero and rising |
| Agent / LLMO | **prompt-block retention** — `selvedge stats` coverage on dogfood repos | Agents actually calling `prior_attempts` |

**Diagnostic pairings (more useful than any single number):**

- Registry presence high **but** installs flat → discovery works, conversion (README/demo) fails.
- AI mention-share rising **but** stars flat → corpus presence growing, human funnel lagging — push human channels.
- Installs up **but** coverage low → agents install but don't call the tool — fix the prompt-block.

**Anti-metrics (keep ignoring):** raw follower count, total stars (vanity once stars/week is tracked), impression sums on cross-posts.

---

## Appendix · Landscape snapshot (June 2026)

Context the plan rests on, so it's sanity-checkable and ages legibly:

- **MCP registry scale:** official MCP Registry ~9,650 servers (API frozen at v0.1); mcp.so ~20k; Glama ~37k (Official/Claimed tiers); PulseMCP ~11.8k (hand-reviewed); Smithery ~7k+ (hosted-remote + CLI). The registry is the *source*; Smithery / Glama / PulseMCP are *storefronts* that read from it.
- **Agent discovery is real:** Cursor, Windsurf, Claude Code, Copilot, Cline, and Aider all fetch `/llms.txt` and `/llms-full.txt` when pointed at a docs site; Cline, Windsurf, and Docker Desktop have in-client MCP marketplaces with one-click/one-command install.
- **llms.txt adoption** ~10% of domains after ~18 months, but it's the de-facto standard among AI-native companies (Anthropic, Cursor, Vercel). A B2A play, not an SEO play.
- **GEO/LLMO consensus:** models are fact-extractors; reward information density, structured data (JSON-LD), server-side rendering, unblocked AI crawlers, a distinctive consistent value prop, and authority content (original research / comparisons). Mention-share is the tracked outcome.

*Source threads (2026-06-22): TrueFoundry / RoxyAPI / Tallyfy registry surveys, the official MCP Registry, Cline `mcp-marketplace` + `docker/mcp-registry` submission docs, the llms.txt 2026 guides, and the GEO/AEO 2026 tool surveys.*
