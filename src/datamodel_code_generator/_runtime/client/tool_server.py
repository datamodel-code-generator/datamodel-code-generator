"""Serve the tools of a client package over MCP, through the low-level server of the official SDK.

`tools/list` lists each tool of an asyncio tool table with the JSON schemas of its arguments and result, which the
table derives from the models, and with annotations from what its HTTP method promises (RFC 9110): a safe method is
read-only, and PUT and DELETE are idempotent. The server only advertises the schemas; a call validates its arguments
through the models, as the table's `call` does. A refusal of the arguments, a failure of the client, and a result that
its model refuses once the credentials placed in it are replaced are each a result marked as an error, whose text no
output schema checks, and a name the table holds no tool of is a protocol error. A result that is no JSON object is the
`result` member of one, since revisions of the protocol before 2026-07-28 take only an object as structured content.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Final, TypeVar

from mcp import MCPError
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import INVALID_PARAMS, CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations

from ..model_codecs.media import json_text
from .tools import UnknownToolError, schema_patterns

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractAsyncContextManager

    from mcp.server import ServerRequestContext
    from mcp.types import CallToolRequestParams, PaginatedRequestParams

    from ..model_codecs.media import JSONValue
    from .tools import AsyncToolTable, ToolOutcome, ToolPlan

TableClientT = TypeVar("TableClientT")
ClientT = TypeVar("ClientT", bound="AbstractAsyncContextManager[object]")
_RESULT: Final = "result"


@dataclass(frozen=True, slots=True)
class ServeCommand:
    """The settings of the command line that serves the tools: the API's base URL, if given, and read-only serving."""

    base_url: str | None
    read_only: bool


def tool_server(table: AsyncToolTable[TableClientT], name: str) -> Server[object]:
    """Return a server named `name` that lists and calls the tools of an asyncio tool table.

    The tools are described once, when they are first listed or called.
    """

    @cache
    def described() -> dict[str, tuple[Tool, bool]]:
        return {plan.name: _described(table, plan) for plan in table.available}

    async def list_tools(  # noqa: RUF029 - the server awaits its handlers.
        context: ServerRequestContext[object], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        """List every tool the table holds, on one page."""
        del context, params
        return ListToolsResult(tools=[tool for tool, _ in described().values()])

    async def call_tool(context: ServerRequestContext[object], params: CallToolRequestParams) -> CallToolResult:
        """Call a tool, returning its JSON result or its error value; a name the table holds no tool of is refused."""
        del context
        try:
            outcome = await table.outcome(params.name, params.arguments or {})
        except UnknownToolError as error:
            raise MCPError(INVALID_PARAMS, str(error)) from None
        return _result(outcome, wrapped=described()[params.name][1])

    return Server(name, on_list_tools=list_tools, on_call_tool=call_tool)


def serve_stdio(make_client: Callable[[], ClientT], server: Callable[[ClientT], Server[object]]) -> None:
    """Serve over the process's stdin and stdout until stdin ends, through a client `make_client` builds and closes."""
    asyncio.run(_served(make_client, server))


async def _served(make_client: Callable[[], ClientT], server: Callable[[ClientT], Server[object]]) -> None:
    client = make_client()
    async with client:
        served = server(client)
        async with stdio_server() as (read, write):
            await served.run(read, write, served.create_initialization_options())


def command_line(argv: Sequence[str] | None, prog: str) -> ServeCommand:
    """Return the settings of the command line that serves the tools, from its arguments or the process's."""
    parser = argparse.ArgumentParser(
        prog=prog, description="Serve the tools of the client over MCP on stdin and stdout."
    )
    parser.add_argument("--base-url", help="The base URL of the API, instead of the client's default server.")
    parser.add_argument("--read-only", action="store_true", help="Serve only the tools of safe HTTP methods.")
    namespace = parser.parse_args(argv)
    return ServeCommand(base_url=namespace.base_url, read_only=namespace.read_only)


def _result(outcome: ToolOutcome, *, wrapped: bool) -> CallToolResult:
    """Return a call's MCP result: its error value as text, or its structured content and the JSON text of it."""
    if outcome.error:
        return CallToolResult(content=[TextContent(text=json_text(outcome.value))], is_error=True)
    structured = {_RESULT: outcome.value} if wrapped else outcome.value
    return CallToolResult(content=[TextContent(text=json_text(structured))], structured_content=structured)


def _described(table: AsyncToolTable[TableClientT], plan: ToolPlan) -> tuple[Tool, bool]:
    """Return how the server lists a tool, and whether its result is the `result` member of its structured content.

    A result schema that holds a pattern Python's `re` rejects (`rejected_patterns` of the table) is not listed, as an
    MCP client checks structured content against the output schema with it; the result is the same.
    """
    arguments, result = table.schemas(plan.name)
    checkable = table.rejected_patterns.isdisjoint(schema_patterns(result))
    wrapped = result.get("type") != "object"
    if wrapped:
        properties: dict[str, JSONValue] = {_RESULT: {key: value for key, value in result.items() if key != "$defs"}}
        definitions = {"$defs": result["$defs"]} if "$defs" in result else {}
        result = {"type": "object", "properties": properties, "required": [_RESULT], **definitions}
    annotations = ToolAnnotations(read_only_hint=plan.safe, idempotent_hint=plan.idempotent)
    tool = Tool(
        name=plan.name,
        description=plan.description,
        input_schema=arguments,
        output_schema=result if checkable else None,
        annotations=annotations,
    )
    return tool, wrapped
