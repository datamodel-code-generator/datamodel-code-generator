"""Patch the query of generated clients' calls: the client's, a view's, and a call's, over the parameters'."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_runtime import Exchange, arecord, json_response, raw_response, record, run

if TYPE_CHECKING:
    from types import ModuleType

_RAW: Final = "https://raw.example.com/items?a=1&limit=2"


def query(package: ModuleType, lines: list[str]) -> None:
    """Patch the query in layers, percent-encoding each patched name and value once, and refuse malformed patches."""
    options, types = (importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "types.pets"))
    exchange = Exchange(lines)
    http = httpx2.Client(transport=httpx2.MockTransport(exchange.handle))
    codecs = types.ListPetsRequestCodecs
    trace = codecs.parameter(location="header", name="X-Trace").from_wire("t1").value
    limit = codecs.parameter(location="query", name="limit").from_wire(5).value
    labels = codecs.parameter(location="query", name="tags").from_wire(["a", "b"])
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
        record(lines, "raw query", lambda: api.request_raw("GET", _RAW, options=options.RequestOptions(query=(("a", None),))).body_bytes)
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
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(exchange.ahandle))
    trace = types.ListPetsRequestCodecs.parameter(location="header", name="X-Trace").from_wire("t1").value
    api: Any
    async with package.AsyncClient(http_client=http, options=options.ClientOptions(query=(("api-version", "1"),))) as api:
        exchange.respond(json_response(200, [], **{"X-Rate": "1"}), raw_response(200, b"ok", "text/plain"))
        await arecord(lines, "async client query", lambda: api.pets.list_pets(x_trace=trace))

        async def raw_call() -> bytes:
            return (await api.request_raw("GET", "https://raw.example.com/items")).body_bytes

        await arecord(lines, "async raw query", raw_call)
    await http.aclose()
