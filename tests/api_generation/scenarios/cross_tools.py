"""Call the pets server through the tools of its client: over MCP with asyncio, through the tool table synchronously.

An MCP client calls the generated MCP server, whose tools call the generated asyncio client, which calls the generated
FastAPI application; the synchronous run calls the same tools through the client's tool table. Each call sends JSON
arguments that the models validate before any request, and returns the server's answer as JSON or an error value. The
asyncio run also calls them through the meta tools of the MCP server, and the synchronous one under a result budget.
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
    return [*_tool_calls(kit), *(_meta_calls(kit) if kit.asynchronous else _budget_calls(kit))]


def _meta_calls(kit: Kit) -> Calls:
    """Return the calls through the meta tools of the MCP server: the listing, a search, a schema, and calls."""
    server = kit.module("mcp_server").server(kit.api, meta_tools=True)

    async def meta(name: str, arguments: dict[str, object]) -> object:
        async with MCPClient(server) as session:
            result = await session.call_tool(name, arguments)
        return {"error": result.is_error, "structured": result.structured_content, "text": result.content[0].text}

    async def listed() -> object:
        async with MCPClient(server) as session:
            return [tool.name for tool in (await session.list_tools()).tools]

    def call(name: str, arguments: dict[str, object]) -> Callable[[], object]:
        return lambda: meta(name, arguments)

    return [
        ("meta tools", listed),
        ("meta search", call("search_tools", {"query": "pet", "limit": 2})),
        ("meta schema", call("get_tool_schema", {"name": "pets_get_pet"})),
        ("meta pets_get_pet", call("call_tool", {"name": "pets_get_pet", "arguments": {"petId": 7}})),
        ("meta pets_get_pet typed error", call("call_tool", {"name": "pets_get_pet", "arguments": {"petId": 404}})),
        (
            "meta pets_create_pet refused body",
            call("call_tool", {"name": "pets_create_pet", "arguments": {"body": {}}}),
        ),
        ("meta unknown tool", call("call_tool", {"name": "nope"})),
    ]


def _budget_calls(kit: Kit) -> Calls:
    """Return the calls of the tool table under a result budget: a short result, and one over it."""
    table = kit.module("tools").Toolset(kit.api, max_result_bytes=64)
    return [
        ("budget pets_get_pet", lambda: table.call("pets_get_pet", {"petId": 7})),
        ("budget pets_list_pets", lambda: table.call("pets_list_pets", {"limit": 5})),
        ("catalog", lambda: [(entry["name"], entry["safe"], entry["idempotent"]) for entry in table.catalog()]),
    ]


def _tool_calls(kit: Kit) -> Calls:
    """Return the calls of each tool, over MCP for the asyncio client and through the table for the synchronous one."""
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
