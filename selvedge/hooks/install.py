"""Project-scoped hook configuration for non-Claude coding clients.

Formats are intentionally explicit: similar event names do not make hook
protocols interchangeable. Existing configuration is validated before any write.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..setup import ConfigWriteResult


def hook_config(agent: str) -> dict:
    """Return the hooks Selvedge implements for this client, in its native schema."""

    def command(event: str) -> str:
        return f"selvedge-hook {event} --agent {agent}"

    events = {
        "PreToolUse": "pretooluse",
        "SessionStart": "sessionstart",
        "PreCompact": "precompact",
    }
    if agent == "cursor":
        return {
            "version": 1,
            "hooks": {
                name: [{"command": command(event)}]
                for name, event in [
                    ("preToolUse", "pretooluse"),
                    ("sessionStart", "sessionstart"),
                    ("preCompact", "precompact"),
                ]
            },
        }
    if agent == "windsurf":
        return {
            "hooks": {
                name: [{"command": command("pretooluse"), "show_output": True}]
                for name in ("pre_write_code", "pre_run_command")
            }
        }
    if agent == "gemini":
        events = {
            "BeforeTool": "pretooluse",
            "SessionStart": "sessionstart",
            "PreCompress": "precompact",
        }
    if agent not in ("codex", "copilot", "gemini"):
        raise ValueError(f"Unsupported hook client: {agent}")
    hooks = {}
    for name, event in events.items():
        handler = {"type": "command", "command": command(event)}
        hooks[name] = [handler if agent == "copilot" else {"hooks": [handler]}]
    return {"hooks": hooks}


def hook_path(agent: str, project: Path) -> Path:
    """Select a client project file, respecting Cascade's newer path precedence."""
    paths = {
        "codex": ".codex/hooks.json",
        "cursor": ".cursor/hooks.json",
        "copilot": ".github/hooks/selvedge.json",
        "gemini": ".gemini/settings.json",
        "windsurf": ".windsurf/hooks.json",
    }
    if agent == "windsurf":
        preferred = project / ".devin/hooks.json"
        if preferred.exists():
            # Use the preferred file whenever present; don't silently add hooks
            # to a legacy file that the current client can ignore.
            return preferred
    return project / paths[agent]


def install_agent_hooks(agent: str, project: Path) -> ConfigWriteResult:
    """Append native entries with backup, idempotence, and fail-without-write validation."""
    from ..setup import ConfigWriteResult, _write_backup

    path = hook_path(agent, project)
    desired = hook_config(agent)
    existed = path.exists()
    try:
        raw = path.read_text() if existed else ""
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            raise ValueError("top level must be an object")
        if (
            "version" in desired
            and "version" in data
            and (type(data["version"]) is not int or data["version"] != 1)
        ):
            raise ValueError("unsupported hooks schema version")
        hooks = data.get("hooks", {})
        if not isinstance(hooks, dict):
            raise ValueError("hooks must be an object")
        if agent == "windsurf" and path == project / ".devin/hooks.json" and not hooks:
            legacy = project / ".windsurf/hooks.json"
            if legacy.exists():
                legacy_data = json.loads(legacy.read_text())
                if not isinstance(legacy_data, dict):
                    raise ValueError("legacy Cascade hooks must be an object")
                if legacy_data.get("hooks"):
                    # Populating the preferred file would deactivate the
                    # currently effective legacy hooks, including other tools.
                    return ConfigWriteResult(
                        "conflict",
                        path,
                        detail="Legacy .windsurf hooks are active; reconcile them into .devin/hooks.json first",
                    )
        # Validate all touched events before modifying anything, including backup.
        for name in desired["hooks"]:
            existing = hooks.get(name, [])
            if not isinstance(existing, list) or any(
                not isinstance(item, dict) for item in existing
            ):
                raise ValueError(f"{name} must be an array of hook objects")
            for item in existing:
                if "hooks" in item and (
                    not isinstance(item["hooks"], list)
                    or any(not isinstance(h, dict) for h in item["hooks"])
                ):
                    raise ValueError(f"{name} has malformed nested hooks")
        changed = False
        for name, entries in desired["hooks"].items():
            existing = hooks.get(name, [])
            entry = entries[0]
            if entry in existing:
                continue
            expected = entry.get("command") or entry["hooks"][0]["command"]
            for item in existing:
                handlers = item.get("hooks", [item])
                for handler in handlers:
                    cmd = handler.get("command", "")
                    if isinstance(cmd, str) and expected in cmd:
                        # A customized matcher/env/timeout may change semantics;
                        # never silently duplicate or overwrite it.
                        return ConfigWriteResult(
                            "conflict",
                            path,
                            detail=f"Existing customized {name} Selvedge hook; reconcile manually",
                        )
            hooks[name] = [*existing, entry]
            changed = True
        if not changed:
            return ConfigWriteResult("unchanged", path)
        if "version" in desired:
            data["version"] = desired["version"]
        data["hooks"] = hooks
        backup = _write_backup(path, raw) if existed else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
        return ConfigWriteResult("added" if existed else "created", path, backup)
    except (ValueError, OSError) as error:
        return ConfigWriteResult("error", path, detail=str(error))
