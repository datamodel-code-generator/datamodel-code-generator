"""Send literal route text without changing legal path characters or encoded parameter values."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.client_runtime import Exchange, arecord, argument, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

    import httpx2


def _calls(package: ModuleType) -> Iterator[tuple[str, dict[str, object]]]:
    """Yield illegal and legal literal text, then a parameter and query encoding control."""
    yield "get_literal", {}
    yield "get_legal", {}
    yield (
        "get_parameter",
        {
            "note": argument(package, "getParameter", "path", "note", "c\\d /%2f"),
            "q": argument(package, "getParameter", "query", "q", "a b"),
        },
    )


def _response(lines: list[str], request: httpx2.Request) -> httpx2.Response:
    """Record the actual request target and return the declared bodyless response."""
    lines.append(f"  raw target {request.url.raw_path!r}")
    return raw_response(204)(request)


def literal_paths(package: ModuleType, lines: list[str]) -> None:
    """Send each route through both generated client modes and the existing TLS fixture server."""
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        for method, arguments in _calls(package):
            exchange.respond(lambda request: _response(lines, request))
            record(
                lines,
                f"sync {method}",
                lambda method=method, arguments=arguments: getattr(api.notes, method)(**arguments),
            )
    run(lambda: _async_paths(package, exchange, lines))


async def _async_paths(package: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    """Send the same literal and parameter controls asynchronously."""
    async with exchange.async_client() as http, package.AsyncClient(http_client=http) as api:
        for method, arguments in _calls(package):
            exchange.respond(lambda request: _response(lines, request))
            await arecord(
                lines,
                f"async {method}",
                lambda method=method, arguments=arguments: getattr(api.notes, method)(**arguments),
            )
