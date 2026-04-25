#!/usr/bin/env bash
# Update GitHub repo description, homepage, and topics for SEO.
#
# Why a script instead of API call from the maintainer's tooling: api.github.com
# is blocked from the Cowork sandbox where most of these edits get drafted, so
# we keep the metadata as code and run it locally with the gh CLI when changed.
#
# Usage:
#   gh auth login   # if you haven't
#   bash scripts/update-repo-metadata.sh
#
# Idempotent — safe to re-run.

set -euo pipefail

REPO="masondelan/selvedge"

DESCRIPTION="Long-term memory for AI-coded codebases. A git blame for AI agents — but for the why. MCP server that captures the agent's reasoning live, in context, as each change is made. Local SQLite, zero deps."

HOMEPAGE="https://masondelan.github.io/selvedge"

# GitHub topics: max 20, lowercase, hyphens only, ≤50 chars each.
# Curated for the keyword strategy in launch/engagement/digests/2026-04-24-friday.md:
#   - own "AI agent reasoning capture" (uncontested)
#   - rank for "git blame for AI" / "AI code provenance" (contested but worth fighting for)
#   - capture related-software discovery surface (mcp / claude-code / cursor / agent-trace)
TOPICS=(
  "mcp"
  "mcp-server"
  "model-context-protocol"
  "ai-coding"
  "ai-agents"
  "claude-code"
  "cursor"
  "ai-code-provenance"
  "git-blame"
  "git-blame-for-ai"
  "ai-agent-reasoning"
  "codebase-change-tracking"
  "schema-change-history"
  "developer-tools"
  "devtools"
  "python"
  "sqlite"
  "agent-trace"
  "ai-codebase-memory"
  "long-term-memory"
)

if ! command -v gh >/dev/null 2>&1; then
  echo "error: gh CLI not installed. Install with: brew install gh" >&2
  exit 1
fi

echo "→ Setting description and homepage on $REPO"
gh repo edit "$REPO" \
  --description "$DESCRIPTION" \
  --homepage "$HOMEPAGE"

echo "→ Replacing topics on $REPO with curated set (${#TOPICS[@]} topics)"
# gh repo edit --add-topic only adds; we want a deterministic set.
# Clear first by reading current and removing each, then add the curated set.
CURRENT_TOPICS=$(gh api "repos/$REPO/topics" --jq '.names[]' 2>/dev/null || echo "")
if [[ -n "$CURRENT_TOPICS" ]]; then
  REMOVE_ARGS=()
  while IFS= read -r topic; do
    [[ -z "$topic" ]] && continue
    REMOVE_ARGS+=(--remove-topic "$topic")
  done <<< "$CURRENT_TOPICS"
  if [[ ${#REMOVE_ARGS[@]} -gt 0 ]]; then
    gh repo edit "$REPO" "${REMOVE_ARGS[@]}"
  fi
fi

ADD_ARGS=()
for topic in "${TOPICS[@]}"; do
  ADD_ARGS+=(--add-topic "$topic")
done
gh repo edit "$REPO" "${ADD_ARGS[@]}"

echo
echo "✓ Done. Verify at https://github.com/$REPO"
gh api "repos/$REPO/topics" --jq '.names[]' | sed 's/^/  · /'
