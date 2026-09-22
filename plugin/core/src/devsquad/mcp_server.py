"""Optional local MCP transport for the durable DevSquad service.

This module must remain importable without the MCP SDK.  Ordinary CLI and
worker processes never pay for or depend on the optional transport package.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

MCP_SDK_REQUIREMENT = "mcp==2.2.0"


class MCPDependencyUnavailable(RuntimeError):
    """Raised when the explicitly requested MCP transport is not installed."""


def _server_type() -> Any:
    try:
        from mcp.server import MCPServer
    except ModuleNotFoundError as exc:
        if exc.name != "mcp":
            raise
        raise MCPDependencyUnavailable(
            "DevSquad MCP support is not installed; install the optional "
            f"dependency with: python3 -m pip install 'devsquad-core[mcp]' "
            f"(requires {MCP_SDK_REQUIREMENT})"
        ) from exc
    return MCPServer


def build_server(runtime: Path) -> Any:
    """Build the stdio server without starting it.

    Tools are registered by later M4 slices.  Keeping construction separate
    makes the optional dependency and installed-wheel boundary testable.
    """

    del runtime
    server_type = _server_type()
    return server_type("DevSquad")


def serve_stdio(runtime: Path) -> None:
    """Run the local MCP server; stdout is owned exclusively by the SDK."""

    build_server(runtime).run()
