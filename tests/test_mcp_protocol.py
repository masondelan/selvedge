"""
End-to-end MCP protocol smoke tests.

Boots the real ``selvedge-server`` subprocess and talks to it over stdio
using the official MCP client SDK. This catches contract drift that the
in-process tool tests in ``test_server.py`` miss — wrong tool registration,
wrong return shapes, broken initialization, etc.

These tests are inherently slower (each spawns a subprocess) but they're
the only way to know the published binary actually responds to a real
agent's MCP traffic.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_EXPECTED_TOOL_NAMES = frozenset({
    "log_change",
    "diff",
    "blame",
    "history",
    "changeset",
    "search",
    "prior_attempts",
})


@pytest.fixture
def server_params(tmp_path: Path) -> StdioServerParameters:
    """Spawn ``selvedge-server`` against a clean per-test database."""
    db_path = tmp_path / "smoke.db"
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "selvedge.server"],
        env={
            **os.environ,
            "SELVEDGE_DB": str(db_path),
            "SELVEDGE_QUIET": "1",
            # Keep test output clean; structured logs would interleave with
            # JSON-RPC traffic on stderr otherwise.
            "SELVEDGE_LOG_LEVEL": "ERROR",
        },
    )


def _payload(result):
    """
    Extract the typed return value from a CallToolResult.

    FastMCP returns a tool's value in three shapes depending on the
    return annotation:
      - ``list[dict]`` returns → ``structuredContent={"result": [...]}``
        and one TextContent block per element under ``content``.
      - ``TypedDict`` returns (v0.3.3+ on ``log_change`` and ``blame``)
        → ``structuredContent`` IS the dict itself, no ``result`` wrap.
      - Plain ``dict`` returns (no annotated return type) →
        ``structuredContent`` is ``None``; the canonical value lives in
        ``content[0].text`` as a JSON string.

    This helper handles all three so callers don't have to know which
    tool returned which shape.
    """
    if result.structuredContent is not None:
        sc = result.structuredContent
        # List shape: {"result": [...]}
        if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
            return sc["result"]
        # TypedDict shape: the dict itself.
        return sc
    # No structuredContent — fall back to TextContent JSON.
    assert result.content, "tool returned no content"
    return json.loads(result.content[0].text)


# ---------------------------------------------------------------------------
# Initialization + tool discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_initializes_and_lists_seven_tools(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.serverInfo.name == "selvedge"

            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert names == _EXPECTED_TOOL_NAMES, (
                f"missing or extra tools — expected {sorted(_EXPECTED_TOOL_NAMES)}, "
                f"got {sorted(names)}"
            )


# ---------------------------------------------------------------------------
# Tool round-trips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_change_round_trip(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "log_change",
                arguments={
                    "entity_path": "users.email",
                    "change_type": "add",
                    "reasoning": "Smoke-test event from the MCP protocol suite",
                    "agent": "mcp-smoke",
                },
            )
            assert not result.isError
            payload = _payload(result)
            assert payload["status"] == "logged"
            assert "id" in payload
            # As of v0.3.3 every LogChangeResult key is always present
            # (so the schema validator sees a complete object). Empty list
            # means "reasoning passed the quality validator."
            assert payload["warnings"] == []


@pytest.mark.asyncio
async def test_diff_returns_logged_event(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("log_change", arguments={
                "entity_path": "users.email",
                "change_type": "add",
                "reasoning": "Seed for the diff smoke test",
            })
            await session.call_tool("log_change", arguments={
                "entity_path": "users.email",
                "change_type": "modify",
                "reasoning": "Second event so diff has multiple rows",
            })

            result = await session.call_tool("diff", arguments={"entity_path": "users.email"})
            payload = _payload(result)
            assert isinstance(payload, list)
            assert len(payload) == 2


@pytest.mark.asyncio
async def test_blame_returns_most_recent(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("log_change", arguments={
                "entity_path": "payments.amount",
                "change_type": "add",
                "reasoning": "Initial event for blame smoke test",
            })
            result = await session.call_tool("blame", arguments={"entity_path": "payments.amount"})
            payload = _payload(result)
            assert payload["entity_path"] == "payments.amount"
            assert payload["change_type"] == "add"


@pytest.mark.asyncio
async def test_history_filters_by_entity(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("log_change", arguments={
                "entity_path": "users.email",
                "change_type": "add",
                "reasoning": "Event for the users entity in history smoke test",
            })
            await session.call_tool("log_change", arguments={
                "entity_path": "orders.total",
                "change_type": "add",
                "reasoning": "Event for the orders entity in history smoke test",
            })

            result = await session.call_tool("history", arguments={"entity_path": "users"})
            payload = _payload(result)
            paths = {row["entity_path"] for row in payload if "entity_path" in row}
            assert paths == {"users.email"}


@pytest.mark.asyncio
async def test_changeset_groups_related_events(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            cs_id = "smoke-stripe"
            for path in ("payments", "payments.amount", "payments.currency"):
                await session.call_tool("log_change", arguments={
                    "entity_path": path,
                    "change_type": "add",
                    "reasoning": f"Adding {path} as part of stripe-billing smoke test",
                    "changeset_id": cs_id,
                })

            result = await session.call_tool("changeset", arguments={"changeset_id": cs_id})
            payload = _payload(result)
            assert {row["entity_path"] for row in payload} == {
                "payments", "payments.amount", "payments.currency",
            }


@pytest.mark.asyncio
async def test_search_finds_by_reasoning(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("log_change", arguments={
                "entity_path": "users.stripe_customer_id",
                "change_type": "add",
                "reasoning": "Stripe billing integration for paid users",
            })
            await session.call_tool("log_change", arguments={
                "entity_path": "users.email",
                "change_type": "add",
                "reasoning": "Standard email field for auth flow",
            })

            result = await session.call_tool("search", arguments={"query": "stripe"})
            payload = _payload(result)
            paths = {row["entity_path"] for row in payload}
            assert paths == {"users.stripe_customer_id"}


# ---------------------------------------------------------------------------
# Reasoning-quality warnings surface in the response payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_change_returns_warnings_for_short_reasoning(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("log_change", arguments={
                "entity_path": "users.email",
                "change_type": "add",
                "reasoning": "n/a",
            })
            payload = _payload(result)
            assert payload["status"] == "logged"
            assert "warnings" in payload
            assert any("generic" in w for w in payload["warnings"])


# ---------------------------------------------------------------------------
# v0.3.7 — prior_attempts + the log_change rename extension over real stdio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prior_attempts_round_trip(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("log_change", arguments={
                "entity_path": "users.token",
                "change_type": "add",
                "reasoning": "Tried a per-user token column for SSO.",
            })
            await session.call_tool("log_change", arguments={
                "entity_path": "users.token",
                "change_type": "remove",
                "reasoning": "Reverted: SSO moved to short-lived JWTs.",
            })

            result = await session.call_tool(
                "prior_attempts", arguments={"entity_path": "users.token"}
            )
            payload = _payload(result)
            assert isinstance(payload, list)
            assert len(payload) == 1
            assert payload[0]["outcome"] == "reverted"
            assert payload[0]["confidence"] == "proximity_high"
            assert "JWTs" in payload[0]["outcome_reasoning"]


@pytest.mark.asyncio
async def test_log_change_rename_dual_event_round_trip(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("log_change", arguments={
                "entity_path": "src/auth/session.py::login",
                "change_type": "rename",
                "rename_from": "src/auth.py::login",
                "entity_type": "function",
                "reasoning": "Split auth.py into a package; login moved.",
            })
            assert not result.isError
            assert _payload(result)["status"] == "logged"

            # New path carries the create event with metadata.renamed_from.
            new = _payload(
                await session.call_tool(
                    "blame", arguments={"entity_path": "src/auth/session.py::login"}
                )
            )
            assert new["change_type"] == "create"
            assert new["metadata"]["renamed_from"] == "src/auth.py::login"
