"""Call the pets server through the tools of its client: over MCP with asyncio, through the tool table synchronously.

An MCP client calls the generated MCP server, whose tools call the generated asyncio client, which calls the generated
FastAPI application; the synchronous run calls the same tools through the client's tool table. Each call sends JSON
arguments that the models validate before any request, and returns the server's answer as JSON or an error value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp import Client as MCPClient

from tests.api_generation.scenarios.cross_pets import services

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.api_generation.support.cross_targets import Calls, Kit

DOCUMENT = "fastapi/pets.yaml"
CLIENT = {"default_base_url": "https://pets.example.com", "tools": "mcp"}
__all__ = ["CLIENT", "DOCUMENT", "calls", "services"]


def calls(kit: Kit) -> Calls:
    """Return the calls of each tool: valid arguments, refused ones, a typed error, and the tool listing."""
    if kit.asynchronous:
        server = kit.module("mcp_server").server(kit.api)

        async def listed() -> object:
            async with MCPClient(server) as session:
                tools = (await session.list_tools()).tools
            return [(tool.name, tool.annotations.read_only_hint, tool.annotations.idempotent_hint) for tool in tools]

        async def called(name: str, arguments: dict[str, object]) -> object:
            async with MCPClient(server) as session:
                result = await session.call_tool(name, arguments)
            return {"error": result.is_error, "structured": result.structured_content, "text": result.content[0].text}

        def call(name: str, arguments: dict[str, object]) -> Callable[[], object]:
            return lambda: called(name, arguments)

    else:
        table = kit.module("tools").Toolset(kit.api)

        def listed() -> object:
            return [entry["name"] for entry in table.search(limit=20)]

        def call(name: str, arguments: dict[str, object]) -> Callable[[], object]:
            return lambda: table.call(name, arguments)

    return [
        ("tools", listed),
        (
            "pets_list_pets every argument",
            call(
                "pets_list_pets",
                {
                    "limit": 5,
                    "tags": ["a b", "c,d"],
                    "kind": "cat",
                    "X_Request_Id": "12345678-1234-5678-1234-567812345678",
                    "since": "2026-10-01T12:30:00Z",
                    "session": "s1",
                },
            ),
        ),
        ("pets_list_pets default response", call("pets_list_pets", {"limit": 99})),
        ("pets_list_pets refused limit", call("pets_list_pets", {"limit": 0})),
        ("pets_create_pet", call("pets_create_pet", {"body": {"name": "Tama", "tag": None}})),
        ("pets_create_pet refused body", call("pets_create_pet", {"body": {"name": ""}})),
        ("pets_get_pet", call("pets_get_pet", {"petId": 7})),
        ("pets_get_pet typed error", call("pets_get_pet", {"petId": 404})),
        ("pets_delete_pet", call("pets_delete_pet", {"petId": 7})),
        ("store_get_inventory", call("store_get_inventory", {})),
    ]
