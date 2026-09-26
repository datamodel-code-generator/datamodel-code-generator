"""Services of the adapter parameter server: they report the decoded values they receive."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def services(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return a service that records its keywords and sends no body."""

    def record(name: str, arguments: dict[str, object]) -> None:
        calls.append(f"{name}({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

    class Untagged(server.services.UntaggedService):
        def search(self, **arguments: object) -> None:
            record("search", arguments)

        def get_ratios(self, **arguments: object) -> None:
            record("get_ratios", arguments)

        def repeat(self, **arguments: object) -> None:
            record("repeat", arguments)

        def get_note(self, **arguments: object) -> None:
            record("get_note", arguments)

        def get_file(self, **arguments: object) -> None:
            record("get_file", arguments)

        def get_menu(self, **arguments: object) -> None:
            record("get_menu", arguments)

        def get_item(self, **arguments: object) -> None:
            record("get_item", arguments)

    return {"default": {"untagged": Untagged()}}
