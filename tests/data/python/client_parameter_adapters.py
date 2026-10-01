"""Send parameters through registered parameter adapters: every location, sync and async, and paged helpers."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from tests.data.python.client_pagination import adrained, drained
from tests.data.python.client_runtime import Exchange, arecord, json_response, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    import httpx2

_SEGMENTS = (".", "..", "%2e", ".%2E", "%2E.", "%2e%2E", "...", "a.b")
_HEADERS = (" padded", "padded\t", "naïve", "a\tb")


def _arguments(package: ModuleType) -> dict[str, Any]:
    """Return the snapshot of each adapted value, built by its request codec from a wire value."""
    codecs = importlib.import_module(f"{package.__name__}.types.default")
    tags, item = codecs.ListTagsRequestCodecs.parameter, codecs.GetItemRequestCodecs.parameter
    segment, search = codecs.GetSegmentRequestCodecs.parameter, codecs.SearchRequestCodecs.parameter
    criteria = codecs.FindRequestCodecs.parameter(location="querystring", name="criteria")
    return {
        "tags": tags(location="cookie", name="tags").from_wire(["a,b", "c"]),
        "none": tags(location="cookie", name="tags").from_wire([]),
        "session": tags(location="cookie", name="session").from_wire("s"),
        "where": tags(location="query", name="where").from_wire({"range": {"min": 1}, "name": "x y"}),
        "labels": tags(location="header", name="X-Labels").from_wire(["l1", "l2"]),
        "unlabeled": tags(location="header", name="X-Labels").from_wire([]),
        "key": item(location="path", name="key").from_wire({"a": 1}),
        "criteria": criteria.from_wire({"term": "a", "start": 4}),
        "opts": search(location="query", name="opts").from_wire({"a": "x"}),
        "a": search(location="query", name="a").from_wire("y"),
        "near": search(location="query", name="near").from_wire({"b": "x y"}),
        "members": codecs.SpreadRequestCodecs.parameter(location="query", name="opts").from_wire({"b": "1"}),
        "segments": {text: segment(location="path", name="segment").from_wire(text) for text in _SEGMENTS},
        "headers": {text: segment(location="header", name="X-Raw").from_wire(text) for text in _HEADERS},
    }


def _page(*ids: str, more: bool) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of one page of found items."""
    return json_response(200, {"data": [{"id": value} for value in ids], "has_more": more})


def parameter_adapters(package: ModuleType, lines: list[str]) -> None:
    """Encode each adapted parameter through its adapter, next to builtin ones, and refuse what the boundary must."""
    values = _arguments(package)
    exchange = Exchange(lines)
    http = exchange.client()
    with package.Client(http_client=http) as api:
        exchange.respond(*(raw_response(204) for _ in range(4)))
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
        record(
            lines,
            "adapted objects beside a member's name",
            lambda: api.default.search(opts=values["opts"], a=values["a"], near=values["near"]),
        )
        _boundaries(api, values, exchange, lines)
        exchange.respond(_page("1", more=False))
        record(lines, "adapted querystring", lambda: api.default.find(criteria=values["criteria"]).data)
        exchange.respond(_page("1", "2", more=True), _page("3", more=False))
        drained(lines, "pages of an adapted querystring", api.protocols.finds.all.iterate(criteria=values["criteria"]))
        exchange.respond(_page("1", more=False))
        drained(lines, "pages without a querystring", api.protocols.finds.all.iterate())
    http.close()
    run(lambda: _async_parameter_adapters(package, values, lines))


def _boundaries(api: Any, values: dict[str, Any], exchange: Exchange, lines: list[str]) -> None:
    """Send path segments, header values, and query names as an adapter gives them, refusing the ones others own.

    Only the last two segments and the last header value are sent.
    """
    exchange.respond(raw_response(204), raw_response(204), raw_response(204))
    for text, segment in values["segments"].items():
        record(lines, f"path segment {text!r}", lambda segment=segment: api.default.get_segment(segment=segment))
    for text, header in values["headers"].items():
        record(
            lines,
            f"header value {text!r}",
            lambda header=header: api.default.get_segment(segment=values["segments"]["a.b"], x_raw=header),
        )
    record(lines, "query member names", lambda: api.default.spread(opts=values["members"]))


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
        await arecord(lines, "async dot segment", lambda: api.default.get_segment(segment=values["segments"][".."]))
        exchange.respond(_page("1", "2", more=True), _page("3", more=False))
        await adrained(
            lines,
            "async pages of an adapted querystring",
            api.protocols.finds.all.iterate(criteria=values["criteria"]),
        )
    await http.aclose()
