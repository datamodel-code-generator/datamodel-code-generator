"""Services of the default parameter server: they report the values and defaults they receive."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def services(server: ModuleType, _models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return a service that records its keywords and sends no body."""

    class Untagged(server.services.UntaggedService):
        def get_values(self, **arguments: object) -> None:
            calls.append(f"get_values({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

    return {"default": {"untagged": Untagged()}}
