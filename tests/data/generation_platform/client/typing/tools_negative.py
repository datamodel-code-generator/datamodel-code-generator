"""Reject a tool table over the other client kind, and calls that are not a name with JSON arguments."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.tools import AsyncToolset, Toolset


def wrong_tables(client: Client, aclient: AsyncClient) -> None:
    """Reject each mistyped table or call."""
    Toolset(aclient)  # error
    AsyncToolset(client)  # error
    Toolset(client).call(3, {})  # error
    Toolset(client).call("pets_get_pet", [3])  # error
    Toolset(client).search(limit="3")  # error
