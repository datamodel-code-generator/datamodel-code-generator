"""Handlers of the pets server: native values, HTTP results, codec snapshots, and handler mapping mistakes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


def handlers(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, Callable[..., object]]]:
    """Return the default handlers and the broken mappings the router builder rejects."""
    pets = {1: models.Pet(id=1, name="Mimi", tag="cat"), 2: models.Pet(id=2, name="Rex", tag=None)}

    def record(name: str, **arguments: object) -> None:
        calls.append(f"{name}({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

    def list_pets(*, limit: object, tags: object, kind: object, x_request_id: object, since: object, session: object) -> object:
        record("list_pets", limit=limit, tags=tags, kind=kind, x_request_id=x_request_id, since=since, session=session)
        if limit == 99:
            return server.HTTPResult(status_code=500, body=models.Error(code=1, message="Too many"))
        return list(pets.values())[: limit if isinstance(limit, int) else None]

    def create_pet(*, body: object) -> object:
        record("create_pet", body=body)
        return models.Pet(id=3, name=body.name, tag=body.tag)

    def list_my_pets() -> object:
        record("list_my_pets")
        return server.responses.ListMyPetsResponseCodecs.body(status_code=200).from_wire([{"id": 1, "name": "Mimi", "tag": None}])

    def get_pet(*, pet_id: int) -> object:
        record("get_pet", pet_id=pet_id)
        if (pet := pets.get(pet_id)) is None:
            return server.HTTPResult(status_code=404, body=models.Error(code=404, message="Not found"))
        return pet

    def delete_pet(*, pet_id: int) -> object:
        record("delete_pet", pet_id=pet_id)
        return None if pet_id in pets else server.HTTPResult(status_code=404, body=models.Error(code=404, message="Gone"))

    def get_inventory() -> object:
        record("get_inventory")
        return {"available": 2}

    default = {
        "list_pets": list_pets,
        "create_pet": create_pet,
        "list_my_pets": list_my_pets,
        "get_pet": get_pet,
        "delete_pet": delete_pet,
        "get_inventory": get_inventory,
    }

    async def get_inventory_async() -> object:
        return {}

    class Awaitable:
        async def __call__(self) -> object:
            return {}

    def get_pet_wrongly(*, pet: int) -> object:
        return pet

    return {
        "default": default,
        "not-a-mapping": list(default.items()),
        "unknown": {**default, "get_pets": get_pet},
        "missing": {key: value for key, value in default.items() if key != "get_inventory"},
        "not-callable": {**default, "get_inventory": "inventory"},
        "async": {**default, "get_inventory": get_inventory_async},
        "async-object": {**default, "get_inventory": Awaitable()},
        "signature": {**default, "get_pet": get_pet_wrongly},
    }
