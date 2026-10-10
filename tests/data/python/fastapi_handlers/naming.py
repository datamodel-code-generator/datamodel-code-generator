"""Services of the server whose names follow the model's naming options: each records the arguments it receives."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


def services(server: ModuleType, _models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return one recording service for each group `create_app` takes, implementing every method of its Protocol."""

    def recorder(name: str) -> Callable[..., None]:
        def method(_self: object, **arguments: object) -> None:
            calls.append(f"{name}({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

        return method

    groups: dict[str, object] = {}
    for name, parameter in inspect.signature(server.create_app).parameters.items():
        protocol = parameter.annotation
        if isinstance(protocol, type) and protocol.__module__ == server.services.__name__:
            methods = {method: recorder(method) for method in sorted(protocol.__abstractmethods__)}
            groups[name] = type(protocol.__name__.removesuffix("Service") or "Operations", (protocol,), methods)()
    return {"default": groups}
