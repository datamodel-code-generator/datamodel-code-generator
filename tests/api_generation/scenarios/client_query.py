"""Merge the query of generated clients' calls: the client's, a view's, and a call's, over the parameters'."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Final

from tests.api_generation.support.client_runtime import (
    Exchange,
    arecord,
    argument,
    json_response,
    raw_response,
    record,
    run,
)

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


def item_or_array(package: ModuleType, lines: list[str]) -> None:
    """Write a parameter that takes one item or an array of them as its style writes each: an item as a scalar."""
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        for label, values in (
            ("items", {"ids": 7, "cwes": "79,284", "has": "patch"}),
            ("arrays", {"ids": [7, 8], "cwes": ["79", "284"], "has": ["patch", "deployment"]}),
            ("one-item arrays", {"ids": [7], "cwes": ["79"], "has": ["patch"]}),
            ("empty arrays", {"ids": [7], "cwes": [], "has": []}),
        ):
            arguments = {
                name: argument(package, "listAdvisories", location, wire_name, values[wire_name])
                for name, location, wire_name in (
                    ("ids", "path", "ids"),
                    ("cwes", "query", "cwes"),
                    ("has", "query", "has"),
                )
            }
            exchange.respond(raw_response(204))
            record(lines, label, lambda arguments=arguments: api.default.list_advisories(**arguments))
        record(lines, "empty path array", lambda: api.default.list_advisories(ids=[]))


def free_objects(package: ModuleType, lines: list[str]) -> None:
    """Write several exploded query objects that take other properties, each as the pairs of its members."""
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        arguments = {
            name: argument(package, "listApps", "query", name, wire)
            for name, wire in (
                ("per_page", 2),
                ("sort_by", {"name": "asc", "created": "desc"}),
                ("filter", {"app_type": "sso", "owner": "files"}),
            )
        }
        exchange.respond(raw_response(204), raw_response(204))
        record(lines, "objects", lambda: api.default.list_apps(**arguments))
        record(lines, "one object", lambda: api.default.list_apps(sort_by=arguments["sort_by"]))


def bracketed(package: ModuleType, lines: list[str]) -> None:
    """Write deepObject arrays as `name[]=value` for each item and scalars as `name=value`, objects as `name[key]`.

    A value that is one item or an array of them is written as either, by the value a call gives.
    """
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        created = argument(package, "GetChargesCharge", "query", "created", {"gt": 5})
        exchange.respond(*(raw_response(204) for _ in range(5)))
        record(
            lines,
            "charge",
            lambda: api.charges.get_charges_charge(charge="ch_1", expand=["a", "b"], limit=3, created=created),
        )
        record(lines, "one item", lambda: api.customers.get_customers(expand=["data.source"]))
        record(lines, "no items", lambda: api.customers.get_customers(expand=[]))
        record(lines, "one id", lambda: api.customers.get_customers(ids="cus_1"))
        record(lines, "ids", lambda: api.customers.get_customers(ids=["cus_1", "cus_2"]))
