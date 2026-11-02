"""Read response keys and media types through generated clients as the server reads them."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.api_generation.support.client_runtime import Exchange, json_response, raw_response, record

if TYPE_CHECKING:
    from types import ModuleType


def _fetched(result: Any) -> str:
    """Summarize a cache result: where it came from, its status and its data."""
    return f"{result.source} {result.response.status_code} {result.data!r}"


def response_keys(package: ModuleType, lines: list[str]) -> None:
    """Cache a range or default response, pick a media type by the server's order, and ignore keys naming no status."""
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        cached = {"etag": '"v1"', "cache-control": "max-age=60"}
        exchange.respond(json_response(200, {"name": "range"}, **cached))
        record(lines, "range fetch", lambda: _fetched(api.protocols.range.fetch()))
        record(lines, "range fetch again", lambda: _fetched(api.protocols.range.fetch()))
        exchange.respond(json_response(200, {"name": "fallback"}, **cached))
        record(lines, "fallback fetch", lambda: _fetched(api.protocols.fallback.fetch()))
        exchange.respond(raw_response(200, b'{"plain":"x"}', content_type="application/json; charset=utf-8"))
        record(lines, "media without parameters first", api.default.get_media)
        exchange.respond(json_response(404, {"plain": "x"}))
        record(lines, "range written with a lower-case x", api.default.get_lowercase)
        exchange.respond(json_response(200, 7))
        record(lines, "status key", api.default.get_invalid)
        for status in (201, 204):
            exchange.respond(raw_response(status))
            record(lines, f"key naming no status {status}", api.default.get_invalid)
        exchange.respond(json_response(200, {"name": "final"}))
        record(lines, "informational keys", api.default.get_informational)
