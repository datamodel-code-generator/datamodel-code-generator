"""Search, describe, and call the tools of a generated client with JSON values, in both execution modes.

A call sends what the client's method sends: the tool takes the operation's parameters and body, never the parameter at
the API key's position or the request options, so the credential the client holds is the one sent. A refused argument,
a failed call, and a tool the table has not each return or raise what the tool table documents.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx2

from tests.api_generation.support.client_runtime import (
    Exchange,
    arecord,
    failing,
    json_response,
    raw_response,
    record,
    run,
)
from tests.api_generation.support.generated_packages import import_generated

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_PET = {"id": 3, "name": "Rex", "species": "dog", "born": "2020-02-29"}


def _shown(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def tools(package: ModuleType, lines: list[str]) -> None:
    """Search the tools, read their schemas, and call each kind of operation through the synchronous table."""
    module = import_generated(f"{package.__name__}.tools", copied=True)
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http, api_key="secret-key", max_retries=0) as api:
        table = module.Toolset(api)
        for query, options in (
            ("", {}),
            ("pet", {"limit": 3}),
            ("owner", {}),
            ("listPets", {}),
            ("delete", {}),
            ("store", {}),
            ("猫", {}),
            ("", {"tag": "owners"}),
        ):
            names = [entry["name"] for entry in table.search(query, **options)]
            lines.append(f"  search {query!r} {options} = {names}")
        record(lines, "search with no limit", lambda: table.search("pet", limit=0))
        lines.append(f"  search entry = {_shown(table.search('owner')[0])}")
        described = ("pets_list_pets", "pets_create_pet", "pets_delete_pet", "owners_get_owner")
        lines.extend(f"  schema {name} = {_shown(table.schema(name))}" for name in described)
        _calls(module, api, exchange, lines)
        record(lines, "unknown tool", lambda: table.call("pets_check_pet", {}))
        record(lines, "unknown schema", lambda: table.schema("nope"))
        safe = module.Toolset(api, read_only=True)
        lines.append(f"  read-only search = {[entry['name'] for entry in safe.search(limit=10)]}")
        record(lines, "read-only create", lambda: safe.call("pets_create_pet", {"body": {"name": "a"}}))
    run(lambda: _async_tools(package, module, lines))


def _calls(module: ModuleType, api: object, exchange: Exchange, lines: list[str]) -> None:
    """Call the tools once for each outcome: a result, a refusal of the arguments, and a failure of the client."""
    table = module.Toolset(api)
    calls: list[tuple[str, str, dict[str, object], Callable[[httpx2.Request], httpx2.Response]]] = [
        ("list with defaults", "pets_list_pets", {}, json_response(200, [_PET])),
        ("list", "pets_list_pets", {"limit": "5", "species": "cat", "X_Trace": "t-1"}, json_response(200, [])),
        ("create", "pets_create_pet", {"body": {"name": "Rex", "species": "dog"}}, json_response(201, _PET)),
        (
            "create refused",
            "pets_create_pet",
            {"body": {"name": "Rex", "species": "dog"}},
            json_response(422, {"code": 1}),
        ),
        ("get", "pets_get_pet", {"petId": 3}, json_response(200, _PET)),
        ("get text", "pets_get_pet", {"petId": 3}, raw_response(200, b"Rex", "text/plain")),
        ("get missing", "pets_get_pet", {"petId": 4}, raw_response(404, b"gone", "text/plain")),
        ("replace without a body", "pets_replace_pet", {"petId": 3}, json_response(200, _PET)),
        ("delete", "pets_delete_pet", {"petId": 3}, raw_response(204)),
        ("delete missing", "pets_delete_pet", {"petId": 4}, raw_response(404)),
        (
            "owner",
            "owners_get_owner",
            {"ownerId": "8c2d1f0e-4a5b-4c6d-8e9f-0a1b2c3d4e5f"},
            json_response(
                200,
                {
                    "id": "8c2d1f0e-4a5b-4c6d-8e9f-0a1b2c3d4e5f",
                    "pets": [_PET],
                },
            ),
        ),
        ("connection failure", "pets_get_pet", {"petId": 3}, failing(httpx2.ConnectError)),
    ]
    for label, name, arguments, responder in calls:
        exchange.respond(responder)
        lines.append(f"  {label} = {_shown(table.call(name, arguments))}")
    exchange.respond(raw_response(500, b'{"code": 5, "message": "' + b"x" * 70_000 + b'"}', "application/json"))
    failure = table.call("pets_list_pets", {})["error"]
    body = failure.pop("body")
    lines.append(f"  long error body = {_shown(failure)}, body {type(body).__name__} of {len(body)} characters")
    for label, name, arguments in (
        ("limit out of range", "pets_list_pets", {"limit": 1000}),
        ("unknown species", "pets_list_pets", {"species": "fish"}),
        ("api key", "pets_list_pets", {"key": "other-key"}),
        ("request options", "pets_get_pet", {"petId": 3, "options": {"base_url": "https://elsewhere.example"}}),
        ("missing path parameter", "pets_get_pet", {}),
        ("invalid body", "pets_create_pet", {"body": {"name": "", "species": "dog", "born": "soon"}}),
        ("no arguments object", "pets_get_pet", [3]),
    ):
        lines.append(f"  {label} = {_shown(table.call(name, arguments))}")


async def _async_tools(package: ModuleType, module: ModuleType, lines: list[str]) -> None:
    """Call tools through the asyncio table: a result and a failure, which it returns as the synchronous one does."""
    exchange = Exchange(lines)
    async with (
        exchange.async_client() as http,
        package.AsyncClient(http_client=http, api_key="secret-key", max_retries=0) as api,
    ):
        table = module.AsyncToolset(api)
        exchange.respond(json_response(200, _PET), raw_response(404, b'{"code": 4}', "application/json"))
        await arecord(lines, "async get", lambda: table.call("pets_get_pet", {"petId": 3}))
        await arecord(lines, "async get missing", lambda: table.call("pets_get_pet", {"petId": 4}))
        await arecord(lines, "async refused", lambda: table.call("pets_get_pet", {"petId": "x"}))
        await arecord(lines, "async unknown tool", lambda: table.call("nope", {}))


def tool_extras(package: ModuleType, lines: list[str]) -> None:
    """Return the members a model that allows extra members holds, as its codec writes them, in a tool's result."""
    module = import_generated(f"{package.__name__}.tools", copied=True)
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http, api_key="secret-key", max_retries=0) as api:
        table = module.Toolset(api)
        exchange.respond(json_response(200, {**_PET, "colour": "brown"}))
        lines.append(f"  get = {_shown(table.call('pets_get_pet', {'petId': 3}))}")
        exchange.respond(json_response(201, {**_PET, "colour": "brown"}))
        created = table.call("pets_create_pet", {"body": {"name": "Rex", "species": "dog", "colour": "brown"}})
        lines.append(f"  create = {_shown(created)}")
