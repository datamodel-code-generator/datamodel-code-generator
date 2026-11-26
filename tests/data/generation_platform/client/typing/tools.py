"""Keep the tool tables typed: built over their own client, searched, described, and called with JSON values."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.tools import AsyncToolset, Toolset, UnknownToolError
from pets.types import JSONValue
from typing_extensions import assert_type


def calls(client: Client) -> None:
    """Search, describe, and call the synchronous client's tools."""
    tools = Toolset(client, read_only=True, max_result_bytes=65536)
    found = tools.search("pet", tag="pets", limit=3)
    assert_type(tools.catalog(), list[dict[str, JSONValue]])
    assert_type(found, list[dict[str, JSONValue]])
    schema = tools.schema("pets_get_pet")
    assert_type(schema, dict[str, JSONValue])
    try:
        result = tools.call("pets_get_pet", {"petId": 3})
    except UnknownToolError as error:
        assert_type(error.name, str)
    else:
        assert_type(result, JSONValue)


async def acalls(client: AsyncClient) -> None:
    """Call the asyncio client's tools."""
    tools = AsyncToolset(client, max_result_bytes=None)
    assert_type(tools.catalog(), list[dict[str, JSONValue]])
    assert_type(await tools.call("pets_list_pets", {}), JSONValue)
