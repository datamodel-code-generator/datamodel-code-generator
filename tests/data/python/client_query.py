"""Patch the query of generated clients' calls: the client's, a view's, and a call's, over the parameters'."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_runtime import Exchange, arecord, argument, json_response, raw_response, record, run

if TYPE_CHECKING:
    from types import ModuleType

_RAW: Final = "https://raw.example.com/items?a=1&limit=2&trace+id=old"


def query(package: ModuleType, lines: list[str]) -> None:
    """Patch the query in layers, percent-encoding each patched name and value once, and refuse malformed patches."""
    options, types = (importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "types.pets"))
    exchange = Exchange(lines)
    http = exchange.client()
    trace = argument(package, "listPets", "header", "X-Trace", "t1")
    limit = argument(package, "listPets", "query", "limit", 5)
    labels = argument(package, "listPets", "query", "tags", ["a", "b"])
    pets = json_response(200, [], **{"X-Rate": "1"})
    client = options.ClientOptions(query=(("api-version", "1"), ("limit", "9")))
    with package.Client(http_client=http, options=client) as api:
        exchange.respond(pets, pets, pets)
        record(lines, "client query under the parameters", lambda: api.pets.list_pets(x_trace=trace, limit=limit))
        view = api.with_options(options.RequestOptions(query=(("api-version", None), ("trace id", "a b/c"))))
        record(lines, "view query", lambda: view.pets.list_pets(x_trace=trace, labels=labels))
        call = options.RequestOptions(query=(("limit", "7"), ("tags", None), ("x", "1"), ("x", "2")))
        record(lines, "call query over the parameters", lambda: view.pets.list_pets(x_trace=trace, limit=limit, labels=labels, options=call))
        exchange.respond(raw_response(200, b"ok", "text/plain"))
        raw = options.RequestOptions(query=(("a", None), ("trace id", "new")))
        record(lines, "raw query", lambda: api.request_raw("GET", _RAW, options=raw).body_bytes)
    http.close()
    for label, patch in (
        ("query name that is empty", (("", "v"),)),
        ("query name set and removed", (("a", "1"), ("a", None))),
        ("query that is no sequence", "a=1"),
    ):
        record(lines, label, lambda patch=patch: options.RequestOptions(query=patch))
    record(lines, "query names by case", lambda: options.RequestOptions(query=(("a", "1"), ("A", None))).query)
    run(lambda: _async_query(package, lines))


async def _async_query(package: ModuleType, lines: list[str]) -> None:
    options, types = (importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "types.pets"))
    exchange = Exchange(lines)
    http = exchange.async_client()
    trace = argument(package, "listPets", "header", "X-Trace", "t1")
    api: Any
    async with package.AsyncClient(http_client=http, options=options.ClientOptions(query=(("api-version", "1"),))) as api:
        exchange.respond(json_response(200, [], **{"X-Rate": "1"}), raw_response(200, b"ok", "text/plain"))
        await arecord(lines, "async client query", lambda: api.pets.list_pets(x_trace=trace))

        async def raw_call() -> bytes:
            return (await api.request_raw("GET", "https://raw.example.com/items")).body_bytes

        await arecord(lines, "async raw query", raw_call)
    await http.aclose()
