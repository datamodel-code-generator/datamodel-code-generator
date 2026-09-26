"""Services of the server that hooks renamed and made asynchronous, rendered from custom templates."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def services(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return asynchronous services under the names the hooks gave the operations."""

    class Pets(server.services.PetsService):
        async def handle_list_pets(self) -> list[str]:
            calls.append("handle_list_pets()")
            return ["rex"]

        async def handle_delete_pet(self, *, pet_id: int) -> None:
            calls.append(f"handle_delete_pet(pet_id={pet_id!r})")

        async def handle_move_pet(self, *, pet_id: int) -> object:
            calls.append(f"handle_move_pet(pet_id={pet_id!r})")
            return server.HTTPResult(status_code=201, headers=(("Location", f"/pets/{pet_id}"),))

    class Untagged(server.services.UntaggedService):
        async def handle_get_health(self) -> None:
            calls.append("handle_get_health()")

    return {"default": {"pets": Pets(), "untagged": Untagged()}}
