"""Merge the query of generated clients' calls: the client's, a view's, and a call's, over the parameters'."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_runtime import Exchange, arecord, argument, json_response, raw_response, record, run

if TYPE_CHECKING:
    from types import ModuleType

_RAW: Final = "https://raw.example.com/items?a=1&limit=2&trace+id=old"


def query(package: ModuleType, lines: list[str]) -> None:
    """Merge the query in layers by case-sensitive name, percent-encoding each merged name and value once."""
    options = importlib.import_module(f"{package.__name__}.options")
    exchange = Exchange(lines)
    http = exchange.client()
    trace = argument(package, "listPets", "header", "X-Trace", "t1")
    limit = argument(package, "listPets", "query", "limit", 5)
    labels = argument(package, "listPets", "query", "tags", ["a", "b"])
    pets = json_response(200, [], **{"X-Rate": "1"})
    with package.Client(http_client=http, default_query={"api-version": "1", "limit": "9"}) as api:
        exchange.respond(pets, pets, pets, pets)
        record(lines, "client query under the parameters", lambda: api.pets.list_pets(X_Trace=trace, limit=limit))
        view = api.with_options(default_query={"api-version": None, "trace id": "a b/c"})
        record(lines, "view query", lambda: view.pets.list_pets(X_Trace=trace, labels=labels))
        nested = view.with_options(default_query={"trace id": "nested", "API-VERSION": "2"})
        record(lines, "nested view query by case", lambda: nested.pets.list_pets(X_Trace=trace))
        call = options.RequestOptions(extra_query={"limit": "7", "tags": None, "x": "1", "Limit": "8"})
        record(
            lines,
            "call query over the parameters",
            lambda: view.pets.list_pets(X_Trace=trace, limit=limit, labels=labels, options=call),
        )
        exchange.respond(raw_response(200, b"ok", "text/plain"))
        raw = options.RequestOptions(extra_query={"a": None, "trace id": "new"})
        record(lines, "raw query", lambda: api.request_raw("GET", _RAW, options=raw).body_bytes)
    http.close()
    run(lambda: _async_query(package, lines))


async def _async_query(package: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    http = exchange.async_client()
    trace = argument(package, "listPets", "header", "X-Trace", "t1")
    api: Any
    async with package.AsyncClient(http_client=http, default_query={"api-version": "1"}) as api:
        exchange.respond(json_response(200, [], **{"X-Rate": "1"}), raw_response(200, b"ok", "text/plain"))
        await arecord(lines, "async client query", lambda: api.pets.list_pets(X_Trace=trace))

        async def raw_call() -> bytes:
            return (await api.request_raw("GET", "https://raw.example.com/items")).body_bytes

        await arecord(lines, "async raw query", raw_call)
    await http.aclose()
