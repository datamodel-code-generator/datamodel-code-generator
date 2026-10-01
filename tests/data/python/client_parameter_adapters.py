"""Send parameters through registered parameter adapters: cookie, query, header, and path, sync and async."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from tests.data.python.client_runtime import Exchange, arecord, raw_response, record, run

if TYPE_CHECKING:
    from types import ModuleType


def _arguments(package: ModuleType) -> dict[str, Any]:
    """Return the snapshot of each adapted value, built by its request codec from a wire value."""
    codecs = importlib.import_module(f"{package.__name__}.types.default")
    tags, item = codecs.ListTagsRequestCodecs.parameter, codecs.GetItemRequestCodecs.parameter
    return {
        "tags": tags(location="cookie", name="tags").from_wire(["a,b", "c"]),
        "none": tags(location="cookie", name="tags").from_wire([]),
        "session": tags(location="cookie", name="session").from_wire("s"),
        "where": tags(location="query", name="where").from_wire({"range": {"min": 1}, "name": "x y"}),
        "labels": tags(location="header", name="X-Labels").from_wire(["l1", "l2"]),
        "unlabeled": tags(location="header", name="X-Labels").from_wire([]),
        "key": item(location="path", name="key").from_wire({"a": 1}),
    }


def parameter_adapters(package: ModuleType, lines: list[str]) -> None:
    """Encode each adapted parameter through its adapter, next to a builtin one, and refuse what the adapter must."""
    values = _arguments(package)
    exchange = Exchange(lines)
    http = exchange.client()
    with package.Client(http_client=http) as api:
        exchange.respond(raw_response(204), raw_response(204), raw_response(204))
        record(
            lines,
            "adapted cookie, query, and header",
            lambda: api.default.list_tags(
                tags=values["tags"], session=values["session"], where=values["where"], x_labels=values["labels"]
            ),
        )
        record(lines, "adapted empty cookie array", lambda: api.default.list_tags(tags=values["none"]))
        record(lines, "adapted path", lambda: api.default.get_item(key=values["key"]))
        record(lines, "empty header array", lambda: api.default.list_tags(x_labels=values["unlabeled"]))
    http.close()
    run(lambda: _async_parameter_adapters(package, values, lines))


async def _async_parameter_adapters(package: ModuleType, values: dict[str, Any], lines: list[str]) -> None:
    exchange = Exchange(lines)
    http = exchange.async_client()
    async with package.AsyncClient(http_client=http) as api:
        exchange.respond(raw_response(204), raw_response(204))
        await arecord(
            lines,
            "async adapted cookie, query, and header",
            lambda: api.default.list_tags(tags=values["tags"], where=values["where"], x_labels=values["labels"]),
        )
        await arecord(lines, "async adapted path", lambda: api.default.get_item(key=values["key"]))
        await arecord(lines, "async empty header array", lambda: api.default.list_tags(x_labels=values["unlabeled"]))
    await http.aclose()
