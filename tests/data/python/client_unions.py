"""Decode each member of a response union: by its required properties, its discriminator, or its whole schema."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Final

from tests.data.python.client_runtime import Exchange, json_response, record

if TYPE_CHECKING:
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


def _decode(package: ModuleType, lines: list[str], modes: tuple[str, ...]) -> None:
    """Read every member of each selected operation's union under each response validation mode."""
    options = importlib.import_module(f"{package.__name__}.options")
    exchange = Exchange(lines)
    with package.Client(http_client=exchange.client(), http_client_ownership="owned") as api:
        for mode in modes:
            call = options.RequestOptions(validation=options.ValidationOptions(response=mode))
            for operation, label, payload in _MEMBERS:
                if (method := getattr(api.default, operation, None)) is not None:
                    exchange.respond(json_response(200, payload))
                    record(lines, f"{label} {mode}", lambda method=method, call=call: method(options=call))


def unions(package: ModuleType, lines: list[str]) -> None:
    """Read each member natively, as the backend's converter tells them apart, and then by their schemas."""
    _decode(package, lines, ("native", "schema"))


def schema_unions(package: ModuleType, lines: list[str]) -> None:
    """Read each member by matching it against the schemas of the union's members."""
    _decode(package, lines, ("schema",))
