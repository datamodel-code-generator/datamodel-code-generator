"""Decode and send enums whose values include null: null, each member, and an unknown value."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.api_generation.support.client_runtime import Exchange, arecord, json_response, record, request_body, run

if TYPE_CHECKING:
    from types import ModuleType

_PETS = [
    {"name": "Mimi", "color": None},
    {"name": "Rex", "color": "red", "size": 2},
    {"name": "Tom", "color": "green", "size": None},
]


def nullable_enums(package: ModuleType, lines: list[str]) -> None:
    """Decode null and members of nullable enums, send them back, and refuse values outside the enum."""
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        exchange.respond(
            json_response(200, _PETS),
            json_response(200, [{"name": "Bad", "color": "blue"}]),
            json_response(200, [{"name": "Bad", "color": None, "size": 3}]),
        )
        record(lines, "decode null and members", api.default.list_pets)
        record(lines, "refuse an unknown string member", api.default.list_pets)
        record(lines, "refuse an unknown integer member", api.default.list_pets)
        for pet in _PETS:
            exchange.respond(json_response(201, pet))
            record(
                lines,
                f"send {pet['name']}",
                lambda pet=pet: api.default.create_pet(body=request_body(package, "createPet", None, pet)),
            )
    run(lambda: _async_nullable_enums(package, lines))


async def _async_nullable_enums(package: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    http = exchange.async_client()
    api: Any
    async with package.AsyncClient(http_client=http) as api:
        exchange.respond(json_response(200, _PETS), json_response(200, [{"name": "Bad", "color": "blue"}]))
        await arecord(lines, "async decode null and members", api.default.list_pets)
        await arecord(lines, "async refuse an unknown member", api.default.list_pets)
    await http.aclose()
