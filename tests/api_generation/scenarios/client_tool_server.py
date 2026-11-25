"""List and call the tools of a generated client over MCP, in process and over stdin and stdout.

An MCP client lists each tool with the JSON schemas of the models and the annotations of its HTTP method, and calls
it: a result is structured content that matches its output schema, a refused argument or a failed call is a result
marked as an error, and an unknown tool is a protocol error. No message the server sends holds a credential the client
placed, even where the API echoes one, a result whose model refuses the text that replaced one is a result marked as an
error too, and document text reaches tool descriptions as the description options say.
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
from functools import partial
from typing import TYPE_CHECKING

import httpx2
import pytest
from mcp import Client as MCPClient
from mcp import MCPError

from tests.api_generation.support.client_runtime import Exchange, failing, json_response, raw_response, run
from tests.api_generation.support.generated_packages import import_generated

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_CANARY = "canary-7f3e"
_PET = {"id": 3, "name": "Rex", "species": "dog", "born": "2020-02-29"}
_OWNER = "8c2d1f0e-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
_CREDENTIALS: tuple[dict[str, object], ...] = (
    {"api_key": _CANARY},
    {"header_key": _CANARY},
    {"bearer": _CANARY},
    {"basic": (_CANARY, _CANARY)},
)


def _shown(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _echo(request: httpx2.Request) -> httpx2.Response:
    """Answer 401 with a body that echoes the request's query, Authorization and API key, as some APIs do."""
    echoed = {
        "query": request.url.query.decode(),
        "authorization": request.headers.get("authorization"),
        "api_key": request.headers.get("x-api-key"),
    }
    return json_response(401, echoed)(request)


def _echoed_pet(request: httpx2.Request) -> httpx2.Response:
    """Answer 200 with a pet whose name echoes the request's URL and credential headers, as a `next` link would."""
    sent = (str(request.url), request.headers.get("authorization"), request.headers.get("x-api-key"))
    return json_response(200, {**_PET, "name": " ".join(filter(None, sent))})(request)


def tool_server(package: ModuleType, lines: list[str]) -> None:
    """List and call the tools in process, then serve them over stdin and stdout, also from the command line."""
    module = import_generated(f"{package.__name__}.mcp_server", copied=True)
    run(lambda: _listed_and_called(package, module, lines))
    _canary(package, module, lines)
    exchange = Exchange(lines)
    exchange.respond(json_response(200, _PET))
    messages: list[dict[str, object]] = [
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "pets_get_pet", "arguments": {"petId": 3}},
        },
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope"}},
    ]
    sent = _stdio(lambda: module.serve(_clients(package, exchange, api_key="k")), messages)
    lines.extend(f"  stdio {line}" for line in _summary(sent))
    read_only = [
        messages[0],
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "pets_get_pet", "arguments": {"petId": 3}},
        },
    ]
    sent = _stdio(lambda: module.main(["--base-url", "https://elsewhere.example/v2", "--read-only"]), read_only)
    lines.extend(f"  main {line}" for line in _summary(sent))


async def _listed_and_called(package: ModuleType, module: ModuleType, lines: list[str]) -> None:
    """List the tools with their schemas and annotations, and call each kind of outcome through the asyncio client."""
    exchange = Exchange(lines)
    async with (
        exchange.async_client() as http,
        package.AsyncClient(http_client=http, api_key="secret-key", max_retries=0) as api,
        MCPClient(module.server(api)) as session,
    ):
        lines.extend(
            f"  tool {tool.name} {tool.annotations!r} {tool.description!r}\n"
            f"    input {_shown(tool.input_schema)}\n    output {_shown(tool.output_schema)}"
            for tool in (await session.list_tools()).tools
        )
        calls: list[tuple[str, str, dict[str, object] | None, Callable[[httpx2.Request], httpx2.Response] | None]] = [
            ("list", "pets_list_pets", {"limit": 5}, json_response(200, [_PET])),
            ("create", "pets_create_pet", {"body": {"name": "Rex", "species": "dog"}}, json_response(201, _PET)),
            ("get", "pets_get_pet", {"petId": 3}, json_response(200, _PET)),
            ("get text", "pets_get_pet", {"petId": 3}, raw_response(200, b"Rex", "text/plain")),
            ("delete", "pets_delete_pet", {"petId": 3}, raw_response(204)),
            ("owner", "owners_get_owner", {"ownerId": _OWNER}, json_response(200, {"id": _OWNER, "pets": [_PET]})),
            ("get missing", "pets_get_pet", {"petId": 4}, raw_response(404, b'{"code": 4}', "application/json")),
            ("connection failure", "pets_get_pet", {"petId": 3}, failing(httpx2.ConnectError)),
            ("refused", "pets_get_pet", {"petId": "x", "options": {}}, None),
            ("no arguments", "pets_get_pet", None, None),
        ]
        for label, name, arguments, responder in calls:
            if responder is not None:
                exchange.respond(responder)
            result = await session.call_tool(name, arguments)
            structured = _shown(result.structured_content)
            lines.append(f"  {label} error={result.is_error} {structured} {[item.text for item in result.content]}")
        try:
            await session.call_tool("nope", {})
        except MCPError as error:
            lines.append(f"  unknown tool ! {error.code} {error.message}")
    async with (
        exchange.async_client() as http,
        package.AsyncClient(http_client=http, api_key="secret-key", max_retries=0) as api,
        MCPClient(module.server(api, read_only=True)) as session,
    ):
        lines.append(f"  read-only tools = {[tool.name for tool in (await session.list_tools()).tools]}")
        try:
            await session.call_tool("pets_create_pet", {"body": {"name": "Rex", "species": "dog"}})
        except MCPError as error:
            lines.append(f"  read-only create ! {error.code} {error.message}")
    await _redacted_results(package, module, exchange, lines)


async def _redacted_results(package: ModuleType, module: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    """Call a tool whose result echoes the API key: in a name it is structured content, as an integer id an error.

    The MCP client checks the structured content against the output schema, which `[redacted]` in an integer fails.
    """
    async with (
        exchange.async_client() as http,
        package.AsyncClient(http_client=http, api_key="123456", max_retries=0) as api,
        MCPClient(module.server(api)) as session,
    ):
        for label, pet in (
            ("credential in a name", {**_PET, "name": "Rex 123456"}),
            ("credential id", {**_PET, "id": 123456}),
        ):
            exchange.respond(json_response(200, pet))
            result = await session.call_tool("pets_get_pet", {"petId": 3})
            structured = _shown(result.structured_content)
            lines.append(f"  {label} error={result.is_error} {structured} {[item.text for item in result.content]}")


def _canary(package: ModuleType, module: ModuleType, lines: list[str]) -> None:
    """Call a tool with each kind of credential set to a marker the API echoes; no message holds the marker.

    The API echoes it in a refusal and in a result; a result that echoes nothing is the API's data.
    """
    exchange = Exchange(lines)
    messages = [
        {
            "jsonrpc": "2.0",
            "id": index,
            "method": "tools/call",
            "params": {"name": "pets_get_pet", "arguments": {"petId": 3}},
        }
        for index in (2, 3, 4)
    ]
    for credentials in _CREDENTIALS:
        exchange.respond(_echo, _echoed_pet, json_response(200, _PET))
        sent = _stdio(partial(module.serve, _clients(package, exchange, max_retries=0, **credentials)), messages)
        lines.append(f"  canary {next(iter(credentials))} sent the marker: {any(_CANARY in line for line in sent)}")
        lines.extend(f"    {line}" for line in _summary(sent)[1:])


def _clients(package: ModuleType, exchange: Exchange, **options: object) -> Callable[[], object]:
    """Return a factory of asyncio clients that send through the exchange and close its HTTPX2 client when closed.

    The server builds and closes the client inside its own event loop, which a borrowed HTTPX2 client must close in.
    """

    def build() -> object:
        http = exchange.async_client()

        class Owning(package.AsyncClient):
            async def __aexit__(self, *exc_info: object) -> None:
                try:
                    await super().__aexit__(*exc_info)
                finally:
                    await http.aclose()

        return Owning(http_client=http, **options)

    return build


@pytest.mark.abnormal_path(
    "The stdio transport serves the process's own stdin and stdout, so a test runs it over pipes."
)
def _stdio(serve: Callable[[], None], messages: list[dict[str, object]]) -> list[str]:
    """Serve over pipes in place of stdin and stdout: send the handshake and each message, then end stdin.

    Each request is sent once the answer of the one before has arrived, and the server's lines are returned in order.
    """
    handshake = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]
    stdin, requests = os.pipe()
    responses, stdout = os.pipe()
    sent: list[str] = []

    def drive() -> None:
        with os.fdopen(requests, "wb", buffering=0) as writer, os.fdopen(responses, "rb") as reader:
            for message in (*handshake, *messages):
                writer.write(json.dumps(message).encode() + b"\n")
                if "id" in message:
                    sent.append(reader.readline().decode().rstrip("\n"))

    given = sys.stdin, sys.stdout
    sys.stdin = io.TextIOWrapper(os.fdopen(stdin, "rb"), encoding="utf-8")
    sys.stdout = io.TextIOWrapper(os.fdopen(stdout, "wb"), encoding="utf-8")
    thread = threading.Thread(target=drive)
    thread.start()
    try:
        serve()
    finally:
        sys.stdin, sys.stdout = given
        thread.join()
    return sent


def _summary(sent: list[str]) -> list[str]:
    """Describe each answer of the server: its id, then its error, or the names of its tools, or its call result."""
    found: list[str] = []
    for line in sent:
        message = json.loads(line)
        result = message.get("result", {})
        if "error" in message:
            shown = f"error {message['error']['code']} {message['error']['message']}"
        elif "tools" in result:
            shown = f"tools {[tool['name'] for tool in result['tools']]}"
        elif "content" in result:
            shown = (
                f"isError={result['isError']} {_shown(result.get('structuredContent'))} {result['content'][0]['text']}"
            )
        else:
            shown = f"server {result['serverInfo']['name']} {result['protocolVersion']}"
        found.append(f"{message['id']} {shown}")
    return found


def tool_descriptions(package: ModuleType, lines: list[str]) -> None:
    """List the tools of a document with hostile text: each description and schema as the server sends them.

    Under --untrusted-input no schema the server sends has a description, title, or examples.
    """
    module = import_generated(f"{package.__name__}.mcp_server", copied=True)
    messages: list[dict[str, object]] = [{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    sent = _stdio(lambda: module.serve(_clients(package, Exchange(lines))), messages)
    lines.extend(
        f"  tool {tool['name']} {tool['description']!r}\n    input {_shown(tool['inputSchema'])}\n"
        f"    output {_shown(tool['outputSchema'])}"
        for tool in json.loads(sent[1])["result"]["tools"]
    )


def tool_patterns(package: ModuleType, lines: list[str]) -> None:
    """List and call tools whose results hold patterns; one Python's `re` rejects leaves out the tool's output schema.

    The MCP client validates structured content against an output schema with Python's `re`, so it can call such a tool
    only when the server lists none; its result is the same either way, and a plain pattern keeps the output schema.
    """
    module = import_generated(f"{package.__name__}.mcp_server", copied=True)
    run(lambda: _patterns_called(package, module, lines))


async def _patterns_called(package: ModuleType, module: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    async with (
        exchange.async_client() as http,
        package.AsyncClient(http_client=http, max_retries=0) as api,
        MCPClient(module.server(api)) as session,
    ):
        tools = (await session.list_tools()).tools
        lines.extend(f"  tool {tool.name} output {_shown(tool.output_schema)}" for tool in tools)
        bodies = ({"name": "Ωmega"}, {"Ärzte": 2}, {"code": "abc"})
        for name, body in zip((tool.name for tool in tools), bodies, strict=True):
            exchange.respond(json_response(200, body))
            result = await session.call_tool(name, {})
            lines.append(f"  {name} error={result.is_error} {_shown(result.structured_content)}")
