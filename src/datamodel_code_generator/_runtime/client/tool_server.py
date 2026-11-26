"""Serve the tools of a client package over MCP, through the low-level server of the official SDK.

`tools/list` lists each tool of an asyncio tool table with the JSON schemas of its arguments and result, which the
table derives from the models, and with annotations from what its HTTP method promises (RFC 9110): a safe method is
read-only, and PUT and DELETE are idempotent. The server only advertises the schemas; a call validates its arguments
through the models, as the table's `call` does. A refusal of the arguments, a failure of the client, and a result that
its model refuses once the credentials placed in it are replaced are each a result marked as an error, whose text no
output schema checks, and a name the table holds no tool of is a protocol error. A result that is no JSON object is the
`result` member of one, since revisions of the protocol before 2026-07-28 take only an object as structured content.

In the meta-tool mode the server lists three tools instead, for a client that cannot hold one tool per operation:
`search_tools` searches the table, `get_tool_schema` returns a tool's schemas, and `call_tool` calls a tool by name
through the same call as the tool would, so read-only serving, the result budget, the error values, and the redaction
are those of the table. A name of `call_tool` or `get_tool_schema` that the table holds no tool of is a result marked
as an error, and only a name that is none of the three is a protocol error.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Final, TypeVar, cast

from mcp import MCPError
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import INVALID_PARAMS, CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations

from ..model_codecs.media import json_text
from .tools import ToolOutcome, UnknownToolError, problem, refused, schema_patterns, unknown_tool

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from contextlib import AbstractAsyncContextManager

    from mcp.server import ServerRequestContext
    from mcp.types import CallToolRequestParams, PaginatedRequestParams

    from ..model_codecs.media import JSONValue
    from .tools import AsyncToolTable, ToolPlan

TableClientT = TypeVar("TableClientT")
ClientT = TypeVar("ClientT", bound="AbstractAsyncContextManager[object]")
_RESULT: Final = "result"
_SEARCH: Final = "search_tools"
_SCHEMA: Final = "get_tool_schema"
_CALL: Final = "call_tool"


@dataclass(frozen=True, slots=True)
class ServeCommand:
    """The settings of the command line that serves the tools.

    They are the API's base URL and the result budget in bytes, each if given, read-only serving, and the meta-tool
    mode.
    """

    base_url: str | None
    read_only: bool
    max_result_bytes: int | None
    meta_tools: bool


@dataclass(frozen=True, slots=True)
class _MetaArgument:
    """One argument of a meta tool: its name, its JSON schema, the check of a value, and the problem of another."""

    name: str
    schema: dict[str, JSONValue]
    accepts: Callable[[object], bool]
    kind: str
    message: str
    default: object = None
    required: bool = False


def _text(value: object) -> bool:
    return isinstance(value, str)


_NAME_ARGUMENT: Final = _MetaArgument(
    "name", {"type": "string"}, _text, "string_type", "Input should be a valid string", required=True
)
_META_ARGUMENTS: Final[dict[str, tuple[_MetaArgument, ...]]] = {
    _SEARCH: (
        _MetaArgument(
            "query",
            {"type": "string", "default": ""},
            _text,
            "string_type",
            "Input should be a valid string",
            default="",
        ),
        _MetaArgument(
            "tag",
            {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
            lambda value: value is None or isinstance(value, str),
            "string_type",
            "Input should be a valid string or null",
        ),
        _MetaArgument(
            "limit",
            {"type": "integer", "minimum": 1, "default": 10},
            lambda value: type(value) is int and value >= 1,
            "int_type",
            "Input should be an integer of at least 1",
            default=10,
        ),
    ),
    _SCHEMA: (_NAME_ARGUMENT,),
    _CALL: (
        _NAME_ARGUMENT,
        _MetaArgument(
            "arguments",
            {"type": "object", "default": {}},
            lambda value: isinstance(value, dict),
            "dict_type",
            "Input should be a valid object",
            default={},
        ),
    ),
}
_META_DESCRIPTIONS: Final = {
    _SEARCH: (
        "Find the tools of this API that match a query best, at most `limit`, each with its name, route, tags, "
        "description, and Python signature; an empty query lists every tool, and `tag` keeps the tools that carry it."
    ),
    _SCHEMA: "Return a tool's description and the JSON schemas of its arguments object and of its result.",
    _CALL: "Call a tool by its name with its arguments object; its JSON result is the `result` member.",
}
_META_RESULTS: Final[dict[str, dict[str, JSONValue]]] = {
    _SEARCH: {"type": "array", "items": {"type": "object"}},
    _SCHEMA: {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        },
        "required": ["name", "description", "input_schema", "output_schema"],
    },
    _CALL: {},
}


def tool_server(table: AsyncToolTable[TableClientT], name: str, *, meta_tools: bool = False) -> Server[object]:
    """Return a server named `name` that lists and calls the tools of an asyncio tool table.

    The tools are described once, when they are first listed or called; with `meta_tools` the server lists the three
    meta tools instead, which search, describe, and call the table's tools.
    """

    @cache
    def described() -> dict[str, tuple[Tool, bool]]:
        if meta_tools:
            return _meta_tools(table)
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
        arguments = params.arguments or {}
        try:
            if meta_tools:
                outcome = await _meta_called(table, params.name, arguments)
            else:
                outcome = await table.outcome(params.name, arguments)
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
    parser.add_argument(
        "--max-result-bytes",
        type=_positive,
        help="The budget of a result's JSON text in bytes, over which a call returns an error value; none by default.",
    )
    parser.add_argument(
        "--meta-tools",
        action="store_true",
        help="Serve the tools through search_tools, get_tool_schema, and call_tool instead of one tool each.",
    )
    namespace = parser.parse_args(argv)
    return ServeCommand(
        base_url=namespace.base_url,
        read_only=namespace.read_only,
        max_result_bytes=namespace.max_result_bytes,
        meta_tools=namespace.meta_tools,
    )


def _positive(text: str) -> int:
    """Return the positive integer a command-line value spells, or refuse it."""
    if not (text.isascii() and text.isdigit()) or (value := int(text)) < 1:
        msg = f"not a positive integer: {text!r}"
        raise argparse.ArgumentTypeError(msg)
    return value


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


def _meta_tools(table: AsyncToolTable[TableClientT]) -> dict[str, tuple[Tool, bool]]:
    """Return how the server lists the meta tools, each with whether its result is the `result` member.

    `call_tool` is read-only or idempotent only when every tool it can call is.
    """
    available = table.available
    hints = {
        _SEARCH: (True, True),
        _SCHEMA: (True, True),
        _CALL: (all(plan.safe for plan in available), all(plan.idempotent for plan in available)),
    }
    tools: dict[str, tuple[Tool, bool]] = {}
    for name, arguments in _META_ARGUMENTS.items():
        properties: dict[str, JSONValue] = {argument.name: argument.schema for argument in arguments}
        schema: dict[str, JSONValue] = {"type": "object", "properties": properties, "additionalProperties": False}
        if required := [argument.name for argument in arguments if argument.required]:
            schema["required"] = [*required]
        result = _META_RESULTS[name]
        wrapped = result.get("type") != "object"
        output = {"type": "object", "properties": {_RESULT: result}, "required": [_RESULT]} if wrapped else result
        read_only, idempotent = hints[name]
        annotations = ToolAnnotations(read_only_hint=read_only, idempotent_hint=idempotent)
        tool = Tool(
            name=name,
            description=_META_DESCRIPTIONS[name],
            input_schema=schema,
            output_schema=output,
            annotations=annotations,
        )
        tools[name] = (tool, wrapped)
    return tools


async def _meta_called(table: AsyncToolTable[TableClientT], name: str, given: Mapping[str, object]) -> ToolOutcome:
    """Call a meta tool with its arguments: search the table, describe a tool, or call one through the table's call.

    A name that is none of the meta tools raises UnknownToolError; one the table holds no tool of is an error value.
    """
    if (arguments := _META_ARGUMENTS.get(name)) is None:
        raise UnknownToolError(name)
    known = {argument.name for argument in arguments}
    problems = [problem(str(key), "Unknown argument", "extra_forbidden") for key in given if key not in known]
    values: dict[str, object] = {}
    for argument in arguments:
        if argument.name not in given:
            if argument.required:
                problems.append(problem(argument.name, "Field required", "missing"))
            values[argument.name] = argument.default
        elif argument.accepts(value := given[argument.name]):
            values[argument.name] = value
        else:
            problems.append(problem(argument.name, argument.message, argument.kind))
    if problems:
        return ToolOutcome(refused(problems), error=True)
    if name == _SEARCH:
        found = table.search(
            cast("str", values["query"]), tag=cast("str | None", values["tag"]), limit=cast("int", values["limit"])
        )
        return ToolOutcome([*found])
    tool = cast("str", values["name"])
    try:
        if name == _SCHEMA:
            return ToolOutcome(table.schema(tool))
        return await table.outcome(tool, cast("Mapping[str, object]", values["arguments"]))
    except UnknownToolError:
        return ToolOutcome(unknown_tool(tool), error=True)
