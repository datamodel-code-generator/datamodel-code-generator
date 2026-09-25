"""Handlers of the adapter parameter server: they report the decoded values they receive."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


def handlers(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, Callable[..., object]]]:
    """Return handlers that record their keywords and send no body."""

    def recorder(name: str) -> Callable[..., object]:
        def handle(**arguments: object) -> None:
            calls.append(f"{name}({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

        return handle

    return {"default": {name: recorder(name) for name in ("search", "repeat", "get_file", "get_menu")}}
