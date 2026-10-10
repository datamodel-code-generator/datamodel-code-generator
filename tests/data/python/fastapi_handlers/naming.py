"""Services of the server whose arguments take the model's field names and their suffixes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def services(server: ModuleType, _models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return one recording service for each group, which names every argument it receives."""

    def record(name: str, arguments: dict[str, object]) -> None:
        calls.append(f"{name}({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

    class ItemStore(server.services.ItemStoreService):
        def get_item(self, **arguments: object) -> None:
            record("get_item", arguments)

        def get_item_1(self, **arguments: object) -> None:
            record("get_item_1", arguments)

    class ItemStore1(server.services.ItemStore1Service):
        def delete_items_by_item_id_tags(self, **arguments: object) -> None:
            record("delete_items_by_item_id_tags", arguments)

    class Untagged(server.services.UntaggedService):
        def x1(self, **arguments: object) -> None:
            record("x1", arguments)

        def x_1(self, **arguments: object) -> None:
            record("x_1", arguments)

    class Pets(server.services.PetsService):
        def get_pets(self, **arguments: object) -> None:
            record("get_pets", arguments)

    class Close(server.services.CloseService):
        def get_health(self, **arguments: object) -> None:
            record("get_health", arguments)

    return {
        "default": {
            "item_store": ItemStore(),
            "item_store_1": ItemStore1(),
            "untagged": Untagged(),
            "pets": Pets(),
            "close": Close(),
        }
    }
