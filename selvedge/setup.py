"""
Selvedge first-run wizard — collapse the install funnel to one command.

The current funnel is six manual steps with three documentation
lookups: ``pip install selvedge``, add the MCP server (project
``.mcp.json`` for Claude Code, ``~/.cursor/mcp.json`` for Cursor, …),
restart agent, ``selvedge init``, paste system prompt into the
project's ``CLAUDE.md``, ``selvedge install-hook``. ``selvedge setup``
detects the AI tooling already installed on the user's machine and
walks through the remaining five steps in one interactive pass.

Robustness conventions (see CLAUDE.md "code conventions"):

  - **Always back up before modifying.** Every file the wizard touches
    gets a ``<file>.bak`` written next to it via ``prompt._write_backup``
    *before* any modification reaches disk. The summary at the end
    surfaces every backup path so the user can ``mv`` to recover.
  - **Idempotent.** Re-running setup on a project that's already set up
    is a no-op. The MCP-config installers do dictionary-merge (not
    overwrite). The prompt installer uses sentinel-bracketed blocks.
    Existing-but-different MCP entries trigger an explicit prompt, never
    silent overwrite.
  - **Non-destructive on errors.** Malformed JSON / TOML in target
    config files is surfaced and the wizard exits non-zero rather than
    overwriting.
  - **--non-interactive escape hatch.** For CI and devcontainers the
    wizard can run unattended with ``--non-interactive --yes``; without
    ``--yes`` it lists what *would* be done and exits 0.

The detector logic, the writer logic, and the wizard orchestration are
deliberately separated so the test suite (``tests/test_setup.py``) can
exercise each layer with ``tmp_path``-fixtured filesystems and never
touches the real ``~/.claude/`` or ``~/.cursor/``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from .prompt import _write_backup, install_to_file, render_block

# ---------------------------------------------------------------------------
# Detected agents — what we look for on disk
# ---------------------------------------------------------------------------


AgentName = Literal["claude-code", "cursor", "copilot", "codex", "gemini", "windsurf"]


@dataclass(frozen=True)
class AgentTarget:
    """One AI tool that Selvedge knows how to install itself into.

    Each agent has:
      - a human-friendly name (rendered in prompts and the summary),
      - a *config* path — the JSON file we add the MCP entry to, OR
        ``None`` if the tool doesn't have a JSON MCP registry,
      - a *prompt* path — the file we drop the agent-instructions
        block into so the agent knows how to use Selvedge,
      - an optional *detect* path — when set, its existence is the
        install signal (used when ``config_path`` is a project file we
        write regardless of whether the tool is installed, e.g. Claude
        Code's project ``.mcp.json``).

    The wizard treats "agent is installed" as "its detect_path (or,
    absent that, its config_path / prompt_path) already exists on
    disk". That's the minimum signal that the user uses this tool.
    """

    name: AgentName
    label: str
    config_path: Path | None
    prompt_path: Path
    detect_path: Path | None = None

    def is_installed(self) -> bool:
        """Best-effort check: does the user have this tool on this machine?"""
        # An explicit detect_path is the authoritative install signal. It's
        # needed when the MCP config isn't written somewhere we can detect
        # from — e.g. Claude Code, whose entry we write to a project-level
        # .mcp.json but which is "installed" iff ~/.claude/ exists.
        if self.detect_path is not None:
            return self.detect_path.exists() or self.prompt_path.exists()
        if self.config_path is not None and self.config_path.exists():
            return True
        if self.prompt_path.exists():
            return True
        # Fall back to "their config dir at least exists" so a fresh
        # install of, say, Cursor with no project file yet is still
        # detected.
        if self.config_path is not None and self.config_path.parent.exists():
            return True
        return False


def agent_targets(
    *,
    home: Path | None = None,
    project: Path | None = None,
) -> list[AgentTarget]:
    """Return supported agent configurations, including tools not yet detected.

    ``home`` and ``project`` are exposed so the test suite can point
    them at ``tmp_path`` and never touch real ``~/.claude/`` or the
    real CWD. Production callers should leave them as None.

    The order is stable so interactive prompts appear in the same sequence.
    """
    home = home or Path.home()
    project = project or Path.cwd()

    candidates = [
        AgentTarget(
            name="claude-code",
            label="Claude Code",
            # Modern Claude Code reads project-scoped MCP servers from a
            # committed .mcp.json (or `claude mcp add`); it does NOT read
            # ~/.claude/config.json. Detection still keys on ~/.claude/.
            config_path=project / ".mcp.json",
            prompt_path=project / "CLAUDE.md",
            detect_path=home / ".claude",
        ),
        AgentTarget(
            name="cursor",
            label="Cursor",
            config_path=home / ".cursor" / "mcp.json",
            prompt_path=project / ".cursorrules",
        ),
        AgentTarget(
            name="copilot",
            label="GitHub Copilot",
            config_path=project / ".vscode" / "mcp.json",
            prompt_path=project / ".github" / "copilot-instructions.md",
            detect_path=project / ".github" / "copilot-instructions.md",
        ),
        AgentTarget(
            name="codex",
            label="Codex",
            config_path=project / ".codex" / "config.toml",
            prompt_path=project / "AGENTS.md",
            detect_path=home / ".codex",
        ),
        AgentTarget(
            name="gemini",
            label="Gemini CLI",
            config_path=project / ".gemini" / "settings.json",
            prompt_path=project / "GEMINI.md",
            detect_path=home / ".gemini",
        ),
        AgentTarget(
            name="windsurf",
            label="Windsurf",
            config_path=home / ".codeium" / "windsurf" / "mcp_config.json",
            prompt_path=project / ".windsurfrules",
        ),
    ]
    return candidates


def detect_agents(
    *, home: Path | None = None, project: Path | None = None,
) -> list[AgentTarget]:
    """Return supported agents whose configuration or instructions exist."""
    return [c for c in agent_targets(home=home, project=project) if c.is_installed()]


def has_mcp_entry(agent: AgentTarget) -> bool:
    """Check the agent's native registry without modifying its configuration."""
    if agent.config_path is None or not agent.config_path.exists():
        return False
    try:
        raw = agent.config_path.read_text(encoding="utf-8")
        data = tomllib.loads(raw) if agent.name == "codex" else json.loads(raw or "{}")
    except (ValueError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    key = "mcp_servers" if agent.name == "codex" else "servers" if agent.name == "copilot" else "mcpServers"
    servers = data.get(key)
    return isinstance(servers, dict) and isinstance(servers.get("selvedge"), dict)


# ---------------------------------------------------------------------------
# MCP-config installer — adds the selvedge entry to a tool's mcpServers
# ---------------------------------------------------------------------------


@dataclass
class ConfigWriteResult:
    """What happened when we tried to install the MCP entry."""

    action: Literal["created", "added", "updated", "unchanged", "conflict", "error"]
    path: Path
    backup_path: Path | None = None
    detail: str = ""


def install_mcp_entry(
    config_path: Path,
    *,
    server_name: str = "selvedge",
    command: str = "selvedge-server",
    write_backup: bool = True,
    overwrite_existing: bool = False,
    config_key: str = "mcpServers",
    server_type: str | None = None,
) -> ConfigWriteResult:
    """Idempotently merge a Selvedge MCP entry into ``config_path``.

    Behavior matrix:

      - File doesn't exist → create with just our entry  → ``"created"``
      - File exists, no ``mcpServers`` key → add ours    → ``"added"``
      - File exists, no ``selvedge`` under it → add ours → ``"added"``
      - File exists, ``selvedge`` matches ours → no-op   → ``"unchanged"``
      - File exists, ``selvedge`` differs:
          * ``overwrite_existing=False`` → ``"conflict"`` (no write)
          * ``overwrite_existing=True``  → replace, ``"updated"``
      - File exists, JSON is malformed → ``"error"`` (no write)

    A ``.bak`` is written before any modification when
    ``write_backup=True``. ``ConfigWriteResult.backup_path`` reflects
    where it landed (``None`` when no backup was needed).
    """
    desired = {"command": command}
    if server_type is not None:
        desired["type"] = server_type

    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps({config_key: {server_name: desired}}, indent=2) + "\n"
        )
        return ConfigWriteResult("created", config_path)

    raw = config_path.read_text()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        return ConfigWriteResult(
            "error",
            config_path,
            detail=(
                f"existing config is not valid JSON ({e.msg} at line "
                f"{e.lineno}); fix it first or remove and rerun"
            ),
        )

    if not isinstance(data, dict):
        return ConfigWriteResult(
            "error",
            config_path,
            detail="existing config is JSON but the top level is not an object",
        )

    servers = data.get(config_key)
    if not isinstance(servers, dict):
        # Either missing or wrong type — replace with a fresh dict.
        # Replacing a wrong-type value is intentional; leaving a
        # malformed ``mcpServers`` in place would break the user's
        # other agents.
        servers = {}

    existing_entry = servers.get(server_name)

    if existing_entry == desired:
        return ConfigWriteResult("unchanged", config_path)

    if existing_entry is not None and not overwrite_existing:
        return ConfigWriteResult(
            "conflict",
            config_path,
            detail=(
                f"existing '{config_key}.{server_name}' differs from what "
                "Selvedge wants to write; rerun with --force or update "
                "manually"
            ),
        )

    backup_path = _write_backup(config_path, raw) if write_backup else None
    servers[server_name] = desired
    data[config_key] = servers
    config_path.write_text(json.dumps(data, indent=2) + "\n")
    action: Literal["added", "updated"] = (
        "updated" if existing_entry is not None else "added"
    )
    return ConfigWriteResult(action, config_path, backup_path)


def install_codex_entry(config_path: Path) -> ConfigWriteResult:
    """Append a project MCP entry without rewriting unrelated TOML or comments.

    Existing custom Selvedge entries require manual reconciliation, even with
    --force. A lossy TOML rewrite could discard settings or credentials.
    """
    existed = config_path.exists()
    raw = config_path.read_text(encoding="utf-8") if existed else ""
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        return ConfigWriteResult("error", config_path, detail=f"Invalid TOML: {exc}")
    servers = data.get("mcp_servers", {})
    if not isinstance(servers, dict):
        return ConfigWriteResult("error", config_path, detail="mcp_servers must be a TOML table")
    desired = {"command": "selvedge-server"}
    if "selvedge" in servers:
        if servers["selvedge"] == desired:
            return ConfigWriteResult("unchanged", config_path)
        return ConfigWriteResult(
            "conflict", config_path,
            detail="Existing mcp_servers.selvedge differs; reconcile this TOML entry manually. --force does not rewrite TOML.",
        )
    updated = raw.rstrip() + '\n\n[mcp_servers.selvedge]\ncommand = "selvedge-server"\n'
    try:
        parsed = tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        return ConfigWriteResult("error", config_path, detail=f"Cannot append MCP table safely: {exc}")
    expected = {**data, "mcp_servers": {**servers, "selvedge": desired}}
    if parsed != expected:
        return ConfigWriteResult("error", config_path, detail="Appending would alter other TOML settings; update manually")
    backup = _write_backup(config_path, raw) if existed else None
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(updated.lstrip("\n") if not existed else updated, encoding="utf-8")
    return ConfigWriteResult("added" if existed else "created", config_path, backup)


# ---------------------------------------------------------------------------
# PreToolUse enforcement hook installer (v0.3.9.1)
# ---------------------------------------------------------------------------

#: The command Claude Code runs on each gated tool call. Ships as a console
#: script with the package, so it's on PATH wherever `selvedge` is.
HOOK_COMMAND = "selvedge-hook pretooluse"

#: Tools the hook intercepts. Must stay in sync with
#: ``selvedge.hooks.pretooluse._WATCHED_TOOLS``.
HOOK_MATCHER = "Edit|Write|MultiEdit|NotebookEdit|Bash"


def _desired_hook_entry() -> dict:
    return {
        "matcher": HOOK_MATCHER,
        "hooks": [{"type": "command", "command": HOOK_COMMAND}],
    }


#: The delivery hooks (v0.3.10). Unlike the gate these are not tool-scoped,
#: so they carry no matcher — the harness fires them once per event.
SESSION_START_COMMAND = "selvedge-hook sessionstart"
PRE_COMPACT_COMMAND = "selvedge-hook precompact"

#: event name -> (command, matcher or None). One table so the installer, the
#: wizard and the plugin manifest can't drift on which hooks exist.
SELVEDGE_HOOKS: dict[str, tuple[str, str | None]] = {
    "PreToolUse": (HOOK_COMMAND, HOOK_MATCHER),
    "SessionStart": (SESSION_START_COMMAND, None),
    "PreCompact": (PRE_COMPACT_COMMAND, None),
}


def install_hook_entry(
    settings_path: Path,
    event: str,
    *,
    write_backup: bool = True,
) -> ConfigWriteResult:
    """Idempotently merge one Selvedge hook into ``settings_path``.

    Targets the project's ``.claude/settings.json`` (Claude Code's checked-in
    project settings). Behavior matrix mirrors :func:`install_mcp_entry`:

      - File doesn't exist → create with just our hook       → ``"created"``
      - No matching selvedge entry → append ours             → ``"added"``
      - An entry already runs our command → no-op            → ``"unchanged"``
      - Malformed JSON / non-object shapes → ``"error"`` (no write)

    A ``.bak`` is written before any modification when ``write_backup=True``.
    Existing non-Selvedge hooks are never touched — we only ever append.
    """
    command, matcher = SELVEDGE_HOOKS[event]
    desired: dict = {"hooks": [{"type": "command", "command": command}]}
    if matcher is not None:
        desired = {"matcher": matcher, **desired}

    if not settings_path.exists():
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps({"hooks": {event: [desired]}}, indent=2) + "\n"
        )
        return ConfigWriteResult("created", settings_path)

    raw = settings_path.read_text()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        return ConfigWriteResult(
            "error",
            settings_path,
            detail=(
                f"existing settings file is not valid JSON ({e.msg} at line "
                f"{e.lineno}); fix it first and rerun"
            ),
        )
    if not isinstance(data, dict):
        return ConfigWriteResult(
            "error",
            settings_path,
            detail="existing settings file is JSON but the top level is not an object",
        )

    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    existing = hooks.get(event)
    if not isinstance(existing, list):
        existing = []

    for entry in existing:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks", []) or []:
            if isinstance(hook, dict) and command in str(hook.get("command", "")):
                return ConfigWriteResult("unchanged", settings_path)

    backup_path = _write_backup(settings_path, raw) if write_backup else None
    existing.append(desired)
    hooks[event] = existing
    data["hooks"] = hooks
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    return ConfigWriteResult("added", settings_path, backup_path)


def install_pretooluse_hook(
    settings_path: Path, *, write_backup: bool = True
) -> ConfigWriteResult:
    """Install the PreToolUse gate. Thin wrapper kept for its callers."""
    return install_hook_entry(settings_path, "PreToolUse", write_backup=write_backup)


def install_delivery_hooks(
    settings_path: Path, *, write_backup: bool = True
) -> list[tuple[str, ConfigWriteResult]]:
    """Install the SessionStart and PreCompact delivery hooks (v0.3.10).

    Returns one ``(event, result)`` pair per hook so the wizard can report
    each independently — one of them failing should not obscure the other
    succeeding.
    """
    results = []
    for event in ("SessionStart", "PreCompact"):
        results.append(
            (event, install_hook_entry(settings_path, event, write_backup=write_backup))
        )
        # Only the first write needs a backup; a second would overwrite the
        # pre-modification copy with an already-modified one.
        write_backup = False
    return results


# ---------------------------------------------------------------------------
# Wizard orchestration
# ---------------------------------------------------------------------------


@dataclass
class StepResult:
    """One row of the wizard's end-of-run summary."""

    label: str
    status: Literal["ok", "skipped", "noop", "error"]
    detail: str = ""
    backup_path: Path | None = None


@dataclass
class WizardOutcome:
    """All step results, plus an exit code derived from them."""

    steps: list[StepResult] = field(default_factory=list)
    exit_code: int = 0

    def add(self, step: StepResult) -> None:
        self.steps.append(step)
        if step.status == "error":
            self.exit_code = 1


def run_wizard(
    *,
    project: Path,
    home: Path | None = None,
    interactive: bool = True,
    force: bool = False,
    install_hook: bool = True,
    init_project_dir: bool = True,
    install_enforcement_hook: bool = True,
    selected_agents: tuple[str, ...] = (),
    confirm: Callable[[str, bool], bool] | None = None,
    init_fn: Callable[[Path], None] | None = None,
    install_hook_fn: Callable[[Path], None] | None = None,
) -> WizardOutcome:
    """Execute every wizard step end-to-end.

    The wizard is a thin orchestrator over the building blocks in
    this module + ``selvedge.prompt`` + ``selvedge.config``. Each
    step is recorded in ``WizardOutcome.steps`` so the CLI layer can
    render the summary and any caller (tests, future automation) can
    introspect what happened.

    Test seams:

      - ``confirm`` lets tests answer interactive prompts
        deterministically. Production CLI passes a ``click.confirm``
        wrapper.
      - ``init_fn`` and ``install_hook_fn`` let tests stub out the
        side-effects that touch the real DB / git directory. Production
        defaults to ``selvedge.config.init_project`` and the
        ``selvedge install-hook`` command body.
    """
    home = home or Path.home()
    confirm = confirm or _default_confirm
    outcome = WizardOutcome()

    # --- Step 1: detect agents and install MCP entries ---
    if selected_agents:
        available = agent_targets(home=home, project=project)
        unknown = set(selected_agents) - {a.name for a in available}
        if unknown:
            raise ValueError(f"Unsupported agent(s): {', '.join(sorted(unknown))}")
        agents = [a for a in available if a.name in selected_agents]
    else:
        agents = detect_agents(home=home, project=project)

    if not agents:
        outcome.add(
            StepResult(
                "Detect AI tooling",
                "skipped",
                detail=(
                    "No supported AI tools detected on this machine. "
                    "Choose your tool explicitly, e.g. selvedge setup --agent codex."
                ),
            )
        )
    else:
        for agent in agents:
            _install_for_agent(
                agent,
                outcome=outcome,
                force=force,
                confirm=confirm,
            )

    # --- Step 1b: PreToolUse enforcement hook (Claude Code only, v0.3.9.1) ---
    # Default-on: the CLAUDE.md "check prior_attempts first" instruction is
    # probabilistic; the hook makes it deterministic. Offered only when
    # Claude Code was detected — the hook protocol is Claude Code's.
    if install_enforcement_hook and any(a.name == "claude-code" for a in agents):
        settings_path = project / ".claude" / "settings.json"
        if not confirm(
            f"Install the Selvedge PreToolUse enforcement hook into "
            f"{settings_path}? (blocks schema/migration edits until "
            "prior_attempts is checked)",
            True,
        ):
            outcome.add(
                StepResult(
                    "PreToolUse enforcement hook",
                    "skipped",
                    detail="user declined",
                )
            )
        else:
            result = install_pretooluse_hook(settings_path)
            hook_status: dict[str, Literal["ok", "noop", "error"]] = {
                "created": "ok",
                "added": "ok",
                "unchanged": "noop",
                "error": "error",
            }
            outcome.add(
                StepResult(
                    "PreToolUse enforcement hook",
                    hook_status.get(result.action, "error"),
                    detail=result.detail or str(result.path),
                    backup_path=result.backup_path,
                )
            )

    # --- Step 1c: delivery hooks (Claude Code only, v0.3.10) ---
    # The counterpart to the gate. The gate covers the case where there is
    # something to veto; these cover the case where there isn't — a session
    # starting with no idea the store exists, and a compaction about to
    # destroy reasoning that was never written down. Both are read-only,
    # quiet when they have nothing to say, and neither can block anything.
    if install_enforcement_hook and any(a.name == "claude-code" for a in agents):
        settings_path = project / ".claude" / "settings.json"
        if not confirm(
            f"Install the Selvedge delivery hooks into {settings_path}? "
            "(session-start digest of reverted/due decisions, and a "
            "pre-compaction reminder to log unrecorded changes)",
            True,
        ):
            outcome.add(
                StepResult("Delivery hooks", "skipped", detail="user declined")
            )
        else:
            delivery_status: dict[str, Literal["ok", "noop", "error"]] = {
                "created": "ok",
                "added": "ok",
                "unchanged": "noop",
                "error": "error",
            }
            for event, res in install_delivery_hooks(settings_path):
                outcome.add(
                    StepResult(
                        f"{event} hook",
                        delivery_status.get(res.action, "error"),
                        detail=res.detail or str(res.path),
                        backup_path=res.backup_path,
                    )
                )

    # Native adapters keep each client's event and response schema separate.
    if install_enforcement_hook:
        from .hooks.install import hook_path, install_agent_hooks

        for agent in agents:
            if agent.name == "claude-code":
                continue
            target = hook_path(agent.name, project)
            label = f"{agent.label} lifecycle hooks"
            if not confirm(
                f"Install Selvedge hooks into {target}? "
                "(watched-edit checks and supported context/notification events; "
                "review and enable hooks in your client)", True,
            ):
                outcome.add(StepResult(label, "skipped", detail="user declined"))
                continue
            native = install_agent_hooks(agent.name, project)
            native_status: dict[str, Literal["ok", "noop", "error"]] = {
                "created": "ok", "added": "ok", "unchanged": "noop",
                "conflict": "error", "error": "error",
            }
            outcome.add(StepResult(
                label, native_status.get(native.action, "error"),
                detail=native.detail or str(native.path), backup_path=native.backup_path,
            ))

    # --- Step 2: selvedge init in the project ---
    if init_project_dir:
        if (project / ".selvedge").exists():
            outcome.add(
                StepResult(
                    "Initialize project",
                    "noop",
                    detail=str(project / ".selvedge"),
                )
            )
        elif not confirm(f"Run `selvedge init` in {project}?", True):
            # ``confirm`` returning False is the dry-run signal (set by
            # the CLI's ``--non-interactive`` without ``--yes``). Skip
            # the step rather than silently doing it.
            outcome.add(
                StepResult(
                    "Initialize project",
                    "skipped",
                    detail="user declined",
                )
            )
        else:
            try:
                fn = init_fn or _default_init_project
                fn(project)
                outcome.add(
                    StepResult(
                        "Initialize project",
                        "ok",
                        detail=str(project / ".selvedge"),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                outcome.add(
                    StepResult(
                        "Initialize project",
                        "error",
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )

    # --- Step 3: install post-commit hook ---
    if install_hook:
        git_dir = project / ".git"
        if not git_dir.exists():
            outcome.add(
                StepResult(
                    "Install git hook",
                    "skipped",
                    detail="not a git repository",
                )
            )
        elif not confirm("Install Selvedge post-commit hook?", True):
            outcome.add(
                StepResult(
                    "Install git hook",
                    "skipped",
                    detail="user declined",
                )
            )
        else:
            try:
                fn = install_hook_fn or _default_install_hook
                fn(project)
                outcome.add(
                    StepResult(
                        "Install git hook",
                        "ok",
                        detail=str(git_dir / "hooks" / "post-commit"),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                outcome.add(
                    StepResult(
                        "Install git hook",
                        "error",
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )

    return outcome


def _install_for_agent(
    agent: AgentTarget,
    *,
    outcome: WizardOutcome,
    force: bool,
    confirm: Callable[[str, bool], bool],
) -> None:
    """Run the per-agent install (MCP config + prompt block).

    The ``confirm`` callback decides whether to proceed at every
    user-facing decision point. Interactive vs non-interactive mode is
    encoded entirely in what ``confirm`` returns: production
    interactive runs use ``click.confirm``; ``--non-interactive --yes``
    passes a constant-True lambda; ``--non-interactive`` alone passes
    a constant-False lambda (so the wizard becomes a dry-run preview).
    """
    # MCP entry — only if the agent has a config_path
    if agent.config_path is not None:
        if not confirm(
            f"Install Selvedge MCP entry into {agent.label} "
            f"({agent.config_path})?",
            True,
        ):
            outcome.add(
                StepResult(
                    f"{agent.label} MCP entry",
                    "skipped",
                    detail="user declined",
                )
            )
        else:
            if agent.name == "codex":
                result = install_codex_entry(agent.config_path)
            else:
                result = install_mcp_entry(
                    agent.config_path,
                    overwrite_existing=force,
                    config_key="servers" if agent.name == "copilot" else "mcpServers",
                    server_type="stdio" if agent.name == "copilot" else None,
                )
            status_for: dict[str, Literal["ok", "noop", "error", "skipped"]] = {
                "created": "ok",
                "added": "ok",
                "updated": "ok",
                "unchanged": "noop",
                "conflict": "error",
                "error": "error",
            }
            outcome.add(
                StepResult(
                    f"{agent.label} MCP entry",
                    status_for[result.action],
                    detail=result.detail or str(result.path),
                    backup_path=result.backup_path,
                )
            )

    # Prompt block — every agent gets one
    if not confirm(
        f"Install Selvedge prompt block into {agent.prompt_path}?",
        True,
    ):
        outcome.add(
            StepResult(
                f"{agent.label} prompt block",
                "skipped",
                detail="user declined",
            )
        )
    else:
        action, backup = install_to_file(agent.prompt_path)
        status: Literal["ok", "noop"] = "noop" if action == "unchanged" else "ok"
        detail = f"{action}: {agent.prompt_path}"
        outcome.add(
            StepResult(
                f"{agent.label} prompt block",
                status,
                detail=detail,
                backup_path=backup,
            )
        )


# ---------------------------------------------------------------------------
# Default delegates — split out so tests can stub
# ---------------------------------------------------------------------------


def _default_confirm(message: str, default: bool) -> bool:
    """Production confirm — defers to Click for the actual prompt."""
    import click

    return click.confirm(message, default=default)


def _default_init_project(project: Path) -> None:
    """Production init step — creates ``.selvedge/`` AND bootstraps the DB.

    Mirrors what ``selvedge init`` does at the CLI level: ``init_project``
    creates the directory, then ``SelvedgeStorage`` opens (and creates if
    missing) the SQLite file inside it. Without the second step the
    directory exists but the DB doesn't materialize until the first
    ``log_change`` call — which means ``selvedge status`` immediately
    after setup would see "DB file does not exist yet" and confuse the
    user about whether the wizard worked.
    """
    # Late imports keep ``selvedge.setup`` cheap to import standalone
    # (the wizard module is imported by tests, the CLI, and any future
    # automation; pulling storage at import time is wasteful).
    from .config import init_project
    from .storage import SelvedgeStorage

    selvedge_dir = init_project(project)
    SelvedgeStorage(selvedge_dir / "selvedge.db")


def _default_install_hook(project: Path) -> None:
    """Production install_hook — invokes the same logic as ``selvedge install-hook``.

    Pulled out so tests can replace it with a no-op without monkeypatching
    Click's command runner.
    """
    # Late import — the cli module imports from setup, so importing back
    # at module level would create a cycle.
    from .cli import _HOOK_SCRIPT  # noqa: PLC0415
    from .diagnostics import HOOK_MARKER  # noqa: PLC0415

    hooks_dir = project / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "post-commit"
    if hook_path.exists():
        existing = hook_path.read_text()
        if HOOK_MARKER in existing:
            return  # already installed
        hook_path.write_text(existing.rstrip("\n") + "\n\n" + _HOOK_SCRIPT)
    else:
        hook_path.write_text(_HOOK_SCRIPT)
    hook_path.chmod(0o755)


# Re-exported so external callers (tests, future automation) don't have
# to know about the underscore-prefixed helper. ``render_block`` is
# what the wizard surfaces in its summary as "what got written."
__all__ = [
    "AgentName",
    "AgentTarget",
    "ConfigWriteResult",
    "HOOK_COMMAND",
    "HOOK_MATCHER",
    "StepResult",
    "WizardOutcome",
    "detect_agents",
    "install_mcp_entry",
    "install_pretooluse_hook",
    "render_block",
    "run_wizard",
]
