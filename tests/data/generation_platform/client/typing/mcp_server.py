"""Keep the MCP server typed: built over an asyncio client, served by a factory of them, and run from arguments."""

from __future__ import annotations

from mcp.server import Server
from pets import AsyncClient
from pets.mcp_server import main, serve, server
from typing_extensions import assert_type


def served(client: AsyncClient) -> None:
    """Build the server over a client, and serve one that a factory builds."""
    assert_type(server(client, read_only=True), Server[object])
    assert_type(server(client, max_result_bytes=65536, meta_tools=True), Server[object])
    serve(lambda: AsyncClient(base_url="https://api.example.com"), read_only=True)
    serve(lambda: AsyncClient(base_url="https://api.example.com"), max_result_bytes=None, meta_tools=True)
    main(["--read-only", "--meta-tools", "--max-result-bytes", "65536"])
