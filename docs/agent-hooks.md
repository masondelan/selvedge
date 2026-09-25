# Agent lifecycle integrations

Selvedge's native hook adapters connect supported client events to the same
local decision store. Run setup in the project, then review and enable the
resulting hook definitions in that client:

```bash
selvedge setup --agent codex --agent cursor --agent copilot --agent gemini --agent windsurf
```

Choose only the clients you use. Existing MCP configuration and unrelated hook
entries are preserved. Modified files receive backups. Malformed hook structures
and customized Selvedge commands produce a visible error without overwriting the
hook file; reconcile them manually. `--force` does not replace customized hooks.
Use `--skip-enforcement-hook` to install MCP and instructions without lifecycle
hooks, or `SELVEDGE_HOOK_DISABLE=1` to bypass Selvedge hook behavior.

## Capabilities differ by harness

| Setup target | Project hook file | Startup context | Watched-edit gate | Before compaction |
| --- | --- | --- | --- | --- |
| Claude Code | `.claude/settings.json` | Existing digest | Existing Edit/Write/Bash checks | Existing Claude adapter |
| Codex | `.codex/hooks.json` | Digest, including `source: compact` | `Bash` and `apply_patch` | User notification |
| Cursor | `.cursor/hooks.json` | Digest | `Write` and `Shell` | User notification |
| Copilot, VS Code **Local** harness | `.github/hooks/selvedge.json` | Digest | File creation, replacements, `apply_patch`, terminal commands | User notification |
| Gemini CLI | `.gemini/settings.json` | Digest | `write_file`, `replace`, `run_shell_command` | User notification via `PreCompress` |
| Windsurf / Cascade | `.windsurf/hooks.json`, or existing `.devin/hooks.json` | Not provided | `pre_write_code`, `pre_run_command` | Not provided |

This table specifies the implemented adapters. It does not establish that every
client version has executed them. Tests exercise native payload normalization,
configuration merging, real SQLite decisions and subprocess exit/output
contracts. End-to-end behavior must also be checked in the actual client and
version you use. Other tools, inline completions and client harnesses are outside
this coverage. Copilot CLI, cloud and Agent Host use a different hook contract;
the `copilot` setup target here remains VS Code Local, matching its existing
`.vscode/mcp.json` setup.

In Codex CLI 0.154.0-alpha.6.2, a disposable-project test also observed a real
`apply_patch` denial before lookup, a successful CLI `prior-attempts` retrieval,
and an allowed retry with the final file checked. The test used the normal hook
trust review and an absolute executable path with a local payload recorder.
Startup and compaction remain contract-tested; the other four new clients have
not yet been tested end to end. Isolated Codex runs that ignored user config did
not invoke project hooks and were excluded from the successful native test.

The gate is deliberately narrow. It checks configured watched paths, known
mutating tool schemas, explicit rejected/reverted decisions and recorded recent
`prior_attempts` queries. It is not a security boundary or a guarantee that every
edit consults history. Shell detection is heuristic. Read operations, unknown
payloads, absent project databases and internal evaluation errors fail open.
Existing query-window behavior is retained: an earlier query in the recent
window, including another session, may satisfy the check. A successful check does
not establish that an agent correctly applied the retrieved decision.

Startup digests are bounded and quiet when there are no relevant records.
Compaction notifications never block compaction. A **user notification is not
model context injection** and does not cause a model to save a decision before
compaction. Hook observations refer to attempted edits, not proof that an edit
succeeded. Neither adapters nor reminders inspect a model's hidden reasoning or
read chat transcripts.

## Client-specific activation

- **Codex:** trust the project and use `/hooks` to review and trust the generated
  hook definitions. New or changed untrusted hooks are skipped. Setup does not
  alter hook trust or approval policy. The current official runtime uses
  `.codex/hooks.json`, not Cursor's `.cursor/hooks.json`.
- **Cursor:** open a trusted project. Check its Hooks output and settings for
  the generated definitions. A multi-root payload without an unambiguous current
  directory is ignored rather than applying another repository's decisions.
- **VS Code:** select the **Local** session target, keep `chat.useHooks` enabled,
  and review **Chat: Configure Hooks**. The provider harness selected in VS Code
  determines its actual hook protocol; changing only the model is insufficient.
- **Gemini CLI:** review the hooks in project settings and the client's hook
  status. `PreCompress` only displays an advisory message.
- **Windsurf/Cascade:** trusted workspaces load project hooks. Current Devin
  Desktop uses `.devin/hooks.json` preferentially; when that file exists setup
  writes there. Otherwise it uses the legacy `.windsurf/hooks.json`, supported
  by Windsurf and the documented fallback in current Cascade. Review both files
  if migrating, because a preferred file can shadow legacy hooks. Setup reports
  a conflict if populating an empty preferred file would disable legacy hooks.
  The edit gate
  writes its reason to stderr and exits 2; `show_output` surfaces it to the user.

The `selvedge-hook` executable must be on the hook process's PATH. If it is not,
replace the command with its installed absolute path after reviewing your client
configuration. A working MCP server alone does not prove that hooks are active.

## Verify in a disposable project

1. Use a disposable project and initialize its own Selvedge database. Keep the
   fixture out of real project history.
2. Record a synthetic rejection on `schema.sql`, with a concrete reason.
3. Run setup for your chosen client and review the generated hooks.
4. Start a fresh session. Verify that its hook log shows the startup event and
   that the digest contains the record, if startup delivery is supported.
5. Ask for an edit to `schema.sql` without first retrieving history. Inspect the
   real tool result: the gate should deny the attempt and identify the decision.
6. Query `prior_attempts` for `schema.sql`, then retry. The check should allow
   the edit; inspect the final file and run its tests independently.
7. Check an ordinary source edit and a read command; they should remain allowed.
   Trigger manual compaction where supported and inspect the notification.

For direct protocol diagnosis, pipe a saved native JSON payload to
`selvedge-hook pretooluse --agent codex --dry-run`, substituting the client.
A dry run reports what would be emitted and always exits 0. It evaluates the
normal gate and may update its local session bookkeeping; use a fixture database.

## Protocol sources

Checked September 24, 2026:

- [Codex hooks](https://learn.chatgpt.com/docs/hooks)
- [Cursor hooks](https://cursor.com/docs/hooks)
- [Gemini CLI hook reference](https://geminicli.com/docs/hooks/reference/)
- [VS Code hook selection](https://code.visualstudio.com/docs/copilot/customization/hooks)
  and [Local hook reference](https://code.visualstudio.com/docs/agents/reference/hooks-reference)
- [VS Code Local file-tool schemas](https://github.com/microsoft/vscode-copilot-chat/tree/main/src/extension/tools/node)
- [Cascade hooks](https://docs.devin.ai/desktop/cascade/hooks)

These are client APIs, so recheck their current contracts before expanding tool
coverage. Preserve explicit distinctions between configured, protocol-tested,
and observed in a real client.
