"""Services of the pets server: native values, HTTP results, codec snapshots, and service mistakes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def services(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return the default services and the broken ones the router builder rejects."""
    pets = {1: models.Pet(id=1, name="Mimi", tag="cat"), 2: models.Pet(id=2, name="Rex", tag=None)}

    def record(name: str, **arguments: object) -> None:
        calls.append(f"{name}({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

    class Pets(server.services.PetsService):
        def list_pets(
            self, *, limit: object, tags: object, kind: object, x_request_id: object, since: object, session: object
        ) -> object:
            record("list_pets", limit=limit, tags=tags, kind=kind, x_request_id=x_request_id, since=since, session=session)
            if limit == 99:
                return server.HTTPResult(status_code=500, body=models.Error(code=1, message="Too many"))
            return list(pets.values())[: limit if isinstance(limit, int) else None]

        def create_pet(self, *, body: object) -> object:
            record("create_pet", body=body)
            return models.Pet(id=3, name=body.name, tag=body.tag)

        def list_my_pets(self) -> object:
            record("list_my_pets")
            return server.responses.ListMyPetsResponseCodecs.body(status_code=200).from_wire([{"id": 1, "name": "Mimi", "tag": None}])

        def get_pet(self, *, pet_id: int) -> object:
            record("get_pet", pet_id=pet_id)
            if (pet := pets.get(pet_id)) is None:
                return server.HTTPResult(status_code=404, body=models.Error(code=404, message="Not found"))
            return pet

        def delete_pet(self, *, pet_id: int) -> object:
            record("delete_pet", pet_id=pet_id)
            return None if pet_id in pets else server.HTTPResult(status_code=404, body=models.Error(code=404, message="Gone"))

    class Store(server.services.StoreService):
        def get_inventory(self) -> object:
            record("get_inventory")
            return {"available": 2}

    class AsyncStore:
        async def get_inventory(self) -> object:
            return {}

    class Awaitable:
        async def __call__(self) -> object:
            return {}

    class AwaitableStore:
        get_inventory = Awaitable()

    class NoStore:
        get_inventory = "inventory"

    class WrongPets(Pets):
        def get_pet(self, *, pet: int) -> object:
            return pet

    default = {"pets": Pets(), "store": Store()}
    return {
        "default": default,
        "missing": {**default, "store": object()},
        "not-callable": {**default, "store": NoStore()},
        "async": {**default, "store": AsyncStore()},
        "async-object": {**default, "store": AwaitableStore()},
        "signature": {**default, "pets": WrongPets()},
    }
