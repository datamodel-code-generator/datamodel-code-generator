"""Services of the parameter adapter server: they report the values the registered adapters decode."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def services(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:  # noqa: ARG001
    """Return a service that records its keywords and sends no body, one method synchronous and one async."""

    def record(name: str, arguments: dict[str, object]) -> None:
        calls.append(f"{name}({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

    class Untagged(server.services.UntaggedService):
        def list_tags(self, **arguments: object) -> None:
            record("list_tags", arguments)

        async def get_item(self, **arguments: object) -> None:
            record("get_item", arguments)

        def get_segment(self, **arguments: object) -> None:
            record("get_segment", arguments)

        def find(self, **arguments: object) -> None:
            record("find", arguments)

        def search(self, **arguments: object) -> None:
            record("search", arguments)

    return {"default": {"untagged": Untagged()}}
