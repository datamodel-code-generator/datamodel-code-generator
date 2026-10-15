"""Call the pets server through the pets client: every operation, its parameters, statuses, and error responses."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "fastapi/pets.yaml"
CLIENT = {"default_base_url": "https://pets.example.com"}
SOCKET = True


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return services that record their typed arguments and answer from them."""

    class Pets(server.services.PetsService):
        def list_pets(self, **arguments: object) -> object:
            record("list_pets", arguments)
            if arguments["limit"] == 99:
                return server.HTTPResult(409, models.Error(code=409, message="paused"))
            return [models.Pet(id=1, name="Mimi", tag=None), models.Pet(id=2, name="Rex", tag="dog")]

        def create_pet(self, **arguments: object) -> object:
            record("create_pet", arguments)
            body = arguments["body"]
            return server.HTTPResult(201, models.Pet(id=3, name=body.name, tag=body.tag))

        def list_my_pets(self, **arguments: object) -> object:
            record("list_my_pets", arguments)
            return []

        def get_pet(self, **arguments: object) -> object:
            record("get_pet", arguments)
            if arguments["petId"] == 404:
                return server.HTTPResult(404, models.Error(code=404, message="no such pet"))
            return models.Pet(id=arguments["petId"], name="Mimi", tag=None)

        def delete_pet(self, **arguments: object) -> None:
            record("delete_pet", arguments)

    class Store(server.services.StoreService):
        def get_inventory(self, **arguments: object) -> object:
            record("get_inventory", arguments)
            return {"available": 2, "sold": 0}

    return {"pets": Pets(), "store": Store()}


def calls(kit: Kit) -> Calls:
    """Return every operation's calls: each argument set, a typed error, a declared default, and a refused body."""
    api, models = kit.api, kit.models
    kind = models.PetsGetQueryKindParameter.cat
    since = datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc)
    request_id = UUID("12345678-1234-5678-1234-567812345678")
    return [
        (
            "list_pets every parameter",
            lambda: api.pets.list_pets(
                limit=5, tags=["a b", "c,d"], kind=kind, X_Request_Id=request_id, since=since, session="s1"
            ),
        ),
        ("list_pets defaults", api.pets.list_pets),
        ("list_pets default response", lambda: api.pets.list_pets(limit=99)),
        ("create_pet", lambda: api.pets.create_pet(body=models.NewPet(name="Tama", tag=None))),
        ("create_pet refused body", lambda: api.pets.create_pet(body=models.NewPet.model_construct(name="", tag=None))),
        ("list_my_pets", api.pets.list_my_pets),
        ("get_pet", lambda: api.pets.get_pet(petId=7)),
        ("get_pet typed error", lambda: api.pets.get_pet(petId=404)),
        ("delete_pet", lambda: api.pets.delete_pet(petId=7)),
        ("get_inventory", api.store.get_inventory),
    ]
