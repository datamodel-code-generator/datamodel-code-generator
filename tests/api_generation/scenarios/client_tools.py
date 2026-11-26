"""Search, describe, and call the tools of a generated client with JSON values, in both execution modes.

A call sends what the client's method sends: the tool takes the operation's parameters and body, never the parameter at
the API key's position or the request options, so the credential the client holds is the one sent. A refused argument,
a failed call, and a tool the table has not each return or raise what the tool table documents. An error value shows no
credential the client placed where the API echoes one, nor does a result that echoes one; such a result whose model
refuses the text that replaced the credential is an error value. A result longer than the table's budget is an error
value with its size, and the catalog lists each tool as a search does, with what its method promises. Strict models take
the JSON values their schemas describe.
"""

from __future__ import annotations

import json
from datetime import date
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
_OWNER = "8c2d1f0e-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
_ERROR_BODY_LIMIT = 64 * 1024


def _shown(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def _echo(status: int, **headers: str) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of an error body that echoes the request's query, Authorization and API key, as some do."""
    return lambda request: json_response(
        status,
        {
            "query": request.url.query.decode(),
            "authorization": request.headers.get("authorization"),
            "api_key": request.headers.get("x-api-key"),
        },
        **headers,
    )(request)


def _echoed_pet(request: httpx2.Request) -> httpx2.Response:
    """Answer 200 with a pet whose name echoes the request's URL and credential headers, as a `next` link would."""
    sent = (str(request.url), request.headers.get("authorization"), request.headers.get("x-api-key"))
    return json_response(200, {**_PET, "name": " ".join(filter(None, sent))})(request)


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
        catalog = table.catalog()
        lines.extend((
            f"  catalog entry = {_shown(catalog[0])}",
            f"  catalog = {[(entry['name'], entry['safe'], entry['idempotent']) for entry in catalog]}",
            f"  read-only catalog = {[entry['name'] for entry in safe.catalog()]}",
        ))
        _budgets(module, api, exchange, lines)
    for credentials in (
        {"api_key": "secret/key+1"},
        {"header_key": "header-key-1"},
        {"bearer": "token-1"},
        {"basic": ("user-1", "password-1")},
    ):
        with exchange.client() as http, package.Client(http_client=http, max_retries=0, **credentials) as api:
            table = module.Toolset(api)
            exchange.respond(_echo(401))
            lines.append(f"  {next(iter(credentials))} echoed = {_shown(table.call('pets_get_pet', {'petId': 3}))}")
            exchange.respond(_echoed_pet)
            echoed = table.call("pets_get_pet", {"petId": 3})
            lines.append(f"  {next(iter(credentials))} echoed result = {_shown(echoed)}")
    _spelled_credentials(package, module, exchange, lines)
    run(lambda: _async_tools(package, module, lines))


def _budgets(module: ModuleType, api: object, exchange: Exchange, lines: list[str]) -> None:
    """Call a tool under result budgets: a result as long as the budget is returned, a longer one is an error value.

    The budget counts the UTF-8 bytes of the result's compact JSON text; a table has none unless it is given one.
    """
    pets = [{**_PET, "name": "Rex 猫"}] * 3
    exchange.respond(json_response(200, pets))
    value = module.Toolset(api, max_result_bytes=None).call("pets_list_pets", {})
    size = len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())
    for label, budget in (("at the budget", size), ("over the budget", size - 1)):
        exchange.respond(json_response(200, pets))
        result = module.Toolset(api, max_result_bytes=budget).call("pets_list_pets", {})
        lines.append(f"  {label} {budget} = {_shown(result)}")
    exchange.respond(json_response(200, [_PET] * 1500))
    lines.append(f"  no budget by default = {len(module.Toolset(api).call('pets_list_pets', {}))} pets")
    for budget in (0, True, 1.5, "1"):
        record(lines, f"budget {budget!r}", lambda budget=budget: module.Toolset(api, max_result_bytes=budget))


def _spelled_credentials(package: ModuleType, module: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    """Echo a credential as a JSON number, in escapes of a JSON body, and slash-escaped in a body that is not JSON.

    A result whose integer or UUID was the credential is an error value, since its model refuses `[redacted]` there.
    """
    with exchange.client() as http, package.Client(http_client=http, api_key="123456", max_retries=0) as api:
        table = module.Toolset(api)
        exchange.respond(json_response(200, {**_PET, "id": 123456}))
        lines.append(f"  numeric credential result = {_shown(table.call('pets_get_pet', {'petId': 3}))}")
        exchange.respond(json_response(200, {**_PET, "name": "Rex 123456"}))
        lines.append(f"  credential in a name = {_shown(table.call('pets_get_pet', {'petId': 3}))}")
        spelled = b'{"number": 123456, "escaped": "\\u0031\\u0032\\u0033\\u0034\\u0035\\u0036", "other": 1234}'
        exchange.respond(raw_response(401, spelled, "application/json"))
        lines.append(f"  numeric credential body = {_shown(table.call('pets_get_pet', {'petId': 3})['error']['body'])}")
    with exchange.client() as http, package.Client(http_client=http, api_key="secret/key+1", max_retries=0) as api:
        table = module.Toolset(api)
        exchange.respond(raw_response(401, b'{"detail": "key secret\\/key+1 refused"', "application/json"))
        lines.append(f"  slash-escaped body = {_shown(table.call('pets_get_pet', {'petId': 3})['error']['body'])}")
    with exchange.client() as http, package.Client(http_client=http, api_key=_OWNER, max_retries=0) as api:
        table = module.Toolset(api)
        exchange.respond(json_response(200, {"id": _OWNER, "pets": [_PET]}))
        lines.append(f"  owner credential result = {_shown(table.call('owners_get_owner', {'ownerId': _OWNER}))}")


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
    escaped = b'{"errors": [{"secret\\u002dkey": "key secret\\u002dkey"}], "code": 1}'
    for label, responder in (
        ("echoed credential", _echo(401)),
        ("escaped credential", raw_response(401, escaped, "application/json")),
        ("echoed credential text", raw_response(401, b"key secret-key refused", "text/plain")),
        ("cut credential", raw_response(500, b"x" * (_ERROR_BODY_LIMIT - 6) + b"secret-key", "text/plain")),
    ):
        exchange.respond(responder)
        lines.append(f"  {label} = {_shown(table.call('pets_list_pets', {})['error']['body'])[-80:]}")
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
    """Call tools through the asyncio table: a result and a failure, which it returns as the synchronous one does.

    A credential that is an enum value of the result is an error value, and one that is a name stays a result.
    """
    exchange = Exchange(lines)
    async with (
        exchange.async_client() as http,
        package.AsyncClient(http_client=http, api_key="secret-key", max_retries=0) as api,
    ):
        table = module.AsyncToolset(api)
        exchange.respond(json_response(200, _PET), _echoed_pet, raw_response(404, b'{"code": 4}', "application/json"))
        await arecord(lines, "async get", lambda: table.call("pets_get_pet", {"petId": 3}))
        await arecord(lines, "async echoed result", lambda: table.call("pets_get_pet", {"petId": 3}))
        await arecord(lines, "async get missing", lambda: table.call("pets_get_pet", {"petId": 4}))
        await arecord(lines, "async refused", lambda: table.call("pets_get_pet", {"petId": "x"}))
        await arecord(lines, "async unknown tool", lambda: table.call("nope", {}))
        exchange.respond(json_response(200, _PET))
        small = module.AsyncToolset(api, max_result_bytes=10)
        await arecord(lines, "async over the budget", lambda: small.call("pets_get_pet", {"petId": 3}))
        lines.append(f"  async catalog = {[entry['name'] for entry in small.catalog()]}")
    async with (
        exchange.async_client() as http,
        package.AsyncClient(http_client=http, api_key="dog", max_retries=0) as api,
    ):
        table = module.AsyncToolset(api)
        exchange.respond(json_response(200, _PET), json_response(200, {**_PET, "species": "cat", "name": "dog"}))
        await arecord(lines, "async species credential", lambda: table.call("pets_get_pet", {"petId": 3}))
        await arecord(lines, "async name credential", lambda: table.call("pets_get_pet", {"petId": 3}))


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


def tool_strict(package: ModuleType, lines: list[str]) -> None:
    """Call the tools of strict models with JSON values, which they validate as JSON, the mode the schemas describe.

    A strict dataclass takes a JSON object for its body and a date as text; a value that is no JSON value is refused,
    and the error value of a failure shows the API key neither as given nor as its query field encodes it.
    """
    module = import_generated(f"{package.__name__}.tools", copied=True)
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http, api_key="secret/key+1", max_retries=0) as api:
        table = module.Toolset(api)
        born = {"name": "Rex", "species": "dog", "born": "2020-02-29"}
        for label, name, arguments, responder in (
            ("create", "pets_create_pet", {"body": born}, json_response(201, _PET)),
            ("replace", "pets_replace_pet", {"petId": 3, "body": {**born, "species": "cat"}}, json_response(200, _PET)),
            ("list", "pets_list_pets", {"limit": 5, "species": "cat", "X_Trace": "t-1"}, json_response(200, [_PET])),
            ("owner", "owners_get_owner", {"ownerId": _OWNER}, json_response(200, {"id": _OWNER, "pets": [_PET]})),
            ("echoed credential", "pets_get_pet", {"petId": 3}, _echo(404, **{"x-request-id": "req-secret/key+1"})),
        ):
            exchange.respond(responder)
            lines.append(f"  {label} = {_shown(table.call(name, arguments))}")
        for label, name, arguments in (
            ("invalid date", "pets_create_pet", {"body": {**born, "born": "soon"}}),
            ("not a JSON number", "pets_list_pets", {"limit": float("nan")}),
            ("not a JSON value", "pets_create_pet", {"body": {**born, "born": date(2020, 2, 29)}}),
        ):
            lines.append(f"  {label} = {_shown(table.call(name, arguments))}")


def tool_untrusted(package: ModuleType, lines: list[str]) -> None:
    """Read the schemas of a table generated from untrusted input: no schema has a description, title, or examples.

    The property named `description` stays, and the models keep their descriptions, titles and examples. No search or
    catalog entry shows the operation's tags, though the `tag` filter still finds the tools that carry one.
    """
    module = import_generated(f"{package.__name__}.tools", copied=True)
    with package.Client(api_key="secret-key") as api:
        table = module.Toolset(api)
        lines.extend(
            f"  schema {name} = {_shown(table.schema(name))}" for name in ("pets_create_pet", "owners_get_owner")
        )
        lines.extend((
            f"  search entry = {_shown(table.search('owner')[0])}",
            f"  search by tag = {[entry['name'] for entry in table.search(tag='owners')]}",
            f"  catalog entry = {_shown(table.catalog()[-1])}",
        ))
    models = import_generated(f"{package.__name__}_models", copied=True)
    owner = models.Owner.model_json_schema()["properties"]["description"]
    lines.extend((
        f"  model Pet description = {models.Pet.model_json_schema()['description']!r}",
        f"  model Owner.description = {_shown(owner)}",
        f"  model Pet.born = {_shown(models.Pet.model_json_schema()['properties']['born'])}",
        f"  async schema = {_shown(module.AsyncToolset(object()).schema('pets_get_pet'))}",
    ))


def tool_not_tools(package: ModuleType, lines: list[str]) -> None:
    """Leave operations out of the tools by their `tool` setting: they have no tool and keep their client methods."""
    module = import_generated(f"{package.__name__}.tools", copied=True)
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http, api_key="secret-key", max_retries=0) as api:
        table = module.Toolset(api)
        lines.append(f"  tools = {[entry['name'] for entry in table.catalog()]}")
        record(lines, "tool of a left-out operation", lambda: table.call("pets_delete_pet", {"petId": 3}))
        exchange.respond(raw_response(204))
        record(lines, "method of a left-out operation", lambda: api.pets.delete_pet(petId=3))
