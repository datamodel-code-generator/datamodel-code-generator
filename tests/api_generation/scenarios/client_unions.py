"""Decode each member of a union: by its required properties, its discriminator, its whole schema, or its variant's."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from tests.api_generation.support.client_runtime import (
    Exchange,
    arecord,
    json_response,
    raw_response,
    record,
    request_body,
    run,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_MEMBERS: Final = (
    ("get_shape", "circle", {"radius": 1}),
    ("get_shape", "square", {"side": 2}),
    ("get_pet", "cat", {"kind": "cat", "lives": 9}),
    ("get_pet", "dog", {"kind": "dog", "good": True}),
    ("get_overlap", "paired", {"x": "s", "y": "t"}),
    ("get_overlap", "single, which the paired schema refuses", {"x": "s"}),
    ("get_holder", "holder circle", {"b": 1, "shape": {"radius": 1}}),
    ("get_holder", "holder square", {"b": 1, "shape": {"side": 2}}),
    ("get_inline", "inline circle", {"b": 1, "shape": {"radius": 1}}),
    ("get_inline", "inline square", {"b": 1, "shape": {"side": 2}}),
)
_COPIES: Final = (
    ("get_child", "inherited pet c", {"pet": {"kind": "c", "mane": True}, "pets_by_name": {"a": {"kind": "d"}}}),
    ("get_child", "inherited pet d", {"pet": {"kind": "d"}, "pets_by_name": {"b": {"kind": "c", "mane": False}}}),
    ("get_child", "inherited pet of an unknown tag", {"pet": {"kind": "Lion"}, "pets_by_name": {}}),
    ("get_holder", "held Fish", {"pet": {"kind": "Fish", "fins": 2}}),
    ("get_holder", "held Bird", {"pet": {"kind": "Bird"}}),
    ("get_holder", "held null", {"pet": None}),
    ("list_pets", "listed pets", [{"kind": "Cat"}, {"kind": "Dog", "bark": True}]),
)
_SPLIT: Final = ({"radius": 1}, {"side": 2}, {"radius": 1, "id": 4}, {"side": 2, "secret": "s"})


def unions(package: ModuleType, lines: list[str]) -> None:
    """Read every member of each selected operation's union natively, as the backend's converter tells them apart."""
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        for operation, label, payload in _MEMBERS:
            if (method := getattr(api.default, operation, None)) is not None:
                exchange.respond(json_response(200, payload))
                record(lines, label, method)


def _split(exchange: Exchange, api: Any, package: ModuleType, payload: dict[str, object]) -> Callable[[], Any]:
    """Queue the shape the server returns without its write-only property, and return the call that sends it."""
    exchange.respond(json_response(200, {key: value for key, value in payload.items() if key != "secret"}))
    return lambda: api.default.shape(body=request_body(package, "shape", None, payload))


def split_unions(package: ModuleType, lines: list[str]) -> None:
    """Send and read each member of a union whose members split into request and response models.

    Each member is told apart by the schema of its own variant, in both execution modes.
    """
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        for payload in _SPLIT:
            record(lines, f"split {payload}", _split(exchange, api, package, payload))
    run(lambda: _async_split_unions(package, lines))


async def _async_split_unions(package: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    async with exchange.async_client() as http, package.AsyncClient(http_client=http) as api:
        for payload in _SPLIT:
            await arecord(lines, f"async split {payload}", _split(exchange, api, package, payload))


def copied_unions(package: ModuleType, lines: list[str]) -> None:
    """Read and send the unions that inherited fields, request and response variants, and null members copy.

    Each is told apart by the discriminator its schema declares, a mapped schema name included, and a body field
    whose model requires it despite a schema default is a required argument. Each call's answer is dropped after it.
    """
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        calls = [
            (label, json_response(200, payload), getattr(api.default, operation))
            for operation, label, payload in _COPIES
        ]
        calls.extend(
            (
                f"pet {payload['kind']}",
                json_response(200, payload),
                lambda payload=payload: api.default.create_pet(body=request_body(package, "createPet", None, payload)),
            )
            for payload in ({"kind": "kitty"}, {"kind": "doggo", "bark": False})
        )
        calls.extend((
            ("item without its count", raw_response(204), lambda: api.default.create_item(name="n")),
            ("item with its count", raw_response(204), lambda: api.default.create_item(name="n", count=1)),
        ))
        for label, answer, call in calls:
            exchange.respond(answer)
            record(lines, label, call)
            exchange.responders.clear()
