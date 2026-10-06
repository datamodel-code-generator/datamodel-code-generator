"""Decode each member of a union: by its required properties, its discriminator, its whole schema, or its variant's."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_runtime import Exchange, arecord, json_response, record, request_body, run

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
_SPLIT: Final = ({"radius": 1}, {"side": 2}, {"radius": 1, "id": 4}, {"side": 2, "secret": "s"})


def unions(package: ModuleType, lines: list[str]) -> None:
    """Read every member of each selected operation's union natively, as the backend's converter tells them apart."""
    exchange = Exchange(lines)
    with exchange.client() as native_client_31, package.Client(http_client=native_client_31) as api:
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
    with exchange.client() as native_client_50, package.Client(http_client=native_client_50) as api:
        for payload in _SPLIT:
            record(lines, f"split {payload}", _split(exchange, api, package, payload))
    run(lambda: _async_split_unions(package, lines))


async def _async_split_unions(package: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    async with exchange.async_client() as native_client_58, package.AsyncClient(http_client=native_client_58) as api:
        for payload in _SPLIT:
            await arecord(lines, f"async split {payload}", _split(exchange, api, package, payload))
