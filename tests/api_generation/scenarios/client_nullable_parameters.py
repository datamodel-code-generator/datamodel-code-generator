"""Send None for nullable parameters: an optional style parameter is left out, and a required one is refused."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.api_generation.support.client_runtime import Exchange, arecord, raw_response, record, run

if TYPE_CHECKING:
    from types import ModuleType


def nullable_parameters(package: ModuleType, lines: list[str]) -> None:
    """Leave out None of optional query, header, and cookie parameters, send JSON null as content, refuse the rest."""
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        exchange.respond(*(raw_response(204) for _ in range(4)))
        record(
            lines,
            "omit null optional parameters",
            lambda: api.default.get_items(cap=None, tags=None, X_Trace=None, session=None),
        )
        record(
            lines,
            "send optional values",
            lambda: api.default.get_items(cap=0, tags=[1, 2], X_Trace="t", session="s"),
        )
        record(lines, "send null content", lambda: api.default.get_items(blob=None))
        record(lines, "omit an empty array", lambda: api.default.get_items(tags=[]))
        record(lines, "refuse a null array item", lambda: api.default.get_items(levels=[1, None]))
        record(lines, "refuse a null required query", lambda: api.default.get_needed(key="k", need=None))
        record(lines, "refuse a null path", lambda: api.default.get_needed(key=None, need=1))
    run(lambda: _async_nullable_parameters(package, lines))


async def _async_nullable_parameters(package: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    http = exchange.async_client()
    api: Any
    async with package.AsyncClient(http_client=http) as api:
        exchange.respond(raw_response(204))
        await arecord(
            lines,
            "async omit null optional parameters",
            lambda: api.default.get_items(cap=None, tags=None, X_Trace=None, session=None),
        )
        await arecord(lines, "async refuse a null required query", lambda: api.default.get_needed(key="k", need=None))
    await http.aclose()
