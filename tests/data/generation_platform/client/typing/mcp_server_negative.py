"""Reject a server over the synchronous client and a factory that is not one of asyncio clients."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.mcp_server import serve, server


def wrong_servers(client: Client, aclient: AsyncClient) -> None:
    """Reject each mistyped server or factory."""
    server(client)  # error
    serve(aclient)  # error
    serve(Client)  # error
    server(aclient, read_only="yes")  # error
