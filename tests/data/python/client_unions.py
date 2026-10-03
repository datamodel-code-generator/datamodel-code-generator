"""Decode each member of a union: by its required properties, its discriminator, its whole schema, or its variant's."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_runtime import Exchange, arecord, json_response, record, run

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
    with package.Client(http_client=exchange.client(), http_client_ownership="owned") as api:
        for operation, label, payload in _MEMBERS:
            if (method := getattr(api.default, operation, None)) is not None:
                exchange.respond(json_response(200, payload))
                record(lines, label, method)


def _split(exchange: Exchange, api: Any, codecs: Any, payload: dict[str, object]) -> Callable[[], Any]:
    """Queue the shape the server returns without its write-only property, and return the call that sends it."""
    exchange.respond(json_response(200, {key: value for key, value in payload.items() if key != "secret"}))
    return lambda: api.default.shape(body=codecs.body().from_wire(payload))


def split_unions(package: ModuleType, lines: list[str]) -> None:
    """Send and read each member of a union whose members split into request and response models.

    Each member is told apart by the schema of its own variant, in both execution modes.
    """
    codecs = importlib.import_module(f"{package.__name__}.types.default").ShapeRequestCodecs
    exchange = Exchange(lines)
    with package.Client(http_client=exchange.client(), http_client_ownership="owned") as api:
        for payload in _SPLIT:
            record(lines, f"split {payload}", _split(exchange, api, codecs, payload))
    run(lambda: _async_split_unions(package, codecs, lines))


async def _async_split_unions(package: ModuleType, codecs: Any, lines: list[str]) -> None:
    exchange = Exchange(lines)
    async with package.AsyncClient(http_client=exchange.async_client(), http_client_ownership="owned") as api:
        for payload in _SPLIT:
            await arecord(lines, f"async split {payload}", _split(exchange, api, codecs, payload))
