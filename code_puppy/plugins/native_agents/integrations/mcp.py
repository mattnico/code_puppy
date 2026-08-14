"""MCP boundary: use the normal manager and builder, never a second client."""

from __future__ import annotations

from typing import Any

from code_puppy.agents._builder import load_mcp_servers


def bound_servers(agent_name: str) -> list[Any]:
    """Return the same filtered MCP server set ordinary agents receive."""

    try:
        return list(load_mcp_servers(agent_name=agent_name))
    except Exception:
        return []
