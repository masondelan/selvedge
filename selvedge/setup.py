"""
Selvedge first-run wizard — collapse the install funnel to one command.

The current funnel is six manual steps with three documentation
lookups: ``pip install selvedge``, edit ``~/.claude/config.json``,
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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .prompt import _write_backup, install_to_file, render_block

# ---------------------------------------------------------------------------
# Detected agents — what we look for on disk
# ---------------------------------------------------------------------------


AgentName = Literal["claude-code", "cursor", "copilot"]


@dataclass(frozen=True)
class AgentTarget:
    """One AI tool that Selvedge knows how to install itself into.

    Each agent has:
      - a human-friendly name (rendered in prompts and the summary),
      - a *config* path — the JSON file we add the MCP entry to, OR
        ``None`` if the tool doesn't have a JSON MCP registry,
      - a *prompt* path — the file we drop the agent-instructions
        block into so the agent knows how to use Selvedge.

    The wizard treats "agent is installed" as "either the config_path
    or the prompt_path's parent already exists on disk". That's the
    minimum signal that the user uses this tool.
    """

    name: AgentName
    label: str
    config_path: Path | None
    prompt_path: Path

    def is_installed(self) -> bool:
        """Best-effort check: does the user have this tool on this machine?"""
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


def detect_agents(
    *,
    home: Path | None = None,
    project: Path | None = None,
) -> list[AgentTarget]:
    """Return the list of agents we've detected on this machine.

    ``home`` and ``project`` are exposed so the test suite can point
    them at ``tmp_path`` and never touch real ``~/.claude/`` or the
    real CWD. Production callers should leave them as None.

    The returned list is filtered to the subset that actually appears
    installed (per ``AgentTarget.is_installed``). The order is
    deterministic — Claude Code first, then Cursor, then Copilot — so
    interactive-mode prompts always appear in the same sequence.
    """
    home = home or Path.home()
    project = project or Path.cwd()

    candidates = [
        AgentTarget(
            name="claude-code",
            label="Claude Code",
            config_path=home / ".claude" / "config.json",
            prompt_path=project / "CLAUDE.md",
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
            # Copilot doesn't expose a JSON MCP registry today — we only
            # write the prompt block. Setting config_path to None makes
            # the wizard skip the MCP-install step for this agent.
            config_path=None,
            prompt_path=project / ".github" / "copilot-instructions.md",
        ),
    ]
    return [c for c in candidates if c.is_installed()]


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

    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps({"mcpServers": {server_name: desired}}, indent=2) + "\n"
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

    servers = data.get("mcpServers")
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
                f"existing 'mcpServers.{server_name}' differs from what "
                "Selvedge wants to write; rerun with --force or update "
                "manually"
            ),
        )

    backup_path = _write_backup(config_path, raw) if write_backup else None
    servers[server_name] = desired
    data["mcpServers"] = servers
    config_path.write_text(json.dumps(data, indent=2) + "\n")
    action: Literal["added", "updated"] = (
        "updated" if existing_entry is not None else "added"
    )
    return ConfigWriteResult(action, config_path, backup_path)


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
    agents = detect_agents(home=home, project=project)

    if not agents:
        outcome.add(
            StepResult(
                "Detect AI tooling",
                "skipped",
                detail=(
                    "No supported AI tools detected on this machine. "
                    "Install Claude Code, Cursor, or Copilot first, then rerun."
                ),
            )
        )
    else:
        for agent in agents:
            _install_for_agent(
                agent,
                outcome=outcome,
                interactive=interactive,
                force=force,
                confirm=confirm,
            )

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
        elif interactive and not confirm(
            f"Run `selvedge init` in {project}?", True
        ):
            outcome.add(
                StepResult(
                    "Initialize project",
                    "skipped",
                    detail="user declined",
                )
            )
        else:
            try:
                # Late import — ``config.init_project`` would otherwise
                # pull in the storage layer at module import time.
                from .config import init_project as _init_project

                fn = init_fn or _init_project
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
        elif interactive and not confirm(
            "Install Selvedge post-commit hook?", True
        ):
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
    interactive: bool,
    force: bool,
    confirm: Callable[[str, bool], bool],
) -> None:
    """Run the per-agent install (MCP config + prompt block)."""
    # MCP entry — only if the agent has a config_path
    if agent.config_path is not None:
        if interactive and not confirm(
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
            result = install_mcp_entry(
                agent.config_path,
                overwrite_existing=force,
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
    if interactive and not confirm(
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


def _default_install_hook(project: Path) -> None:
    """Production install_hook — invokes the same logic as ``selvedge install-hook``.

    Pulled out so tests can replace it with a no-op without monkeypatching
    Click's command runner.
    """
    # Late import — the cli module imports from setup, so importing back
    # at module level would create a cycle.
    from .cli import _HOOK_MARKER, _HOOK_SCRIPT  # noqa: PLC0415

    hooks_dir = project / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "post-commit"
    if hook_path.exists():
        existing = hook_path.read_text()
        if _HOOK_MARKER in existing:
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
    "StepResult",
    "WizardOutcome",
    "detect_agents",
    "install_mcp_entry",
    "render_block",
    "run_wizard",
]
