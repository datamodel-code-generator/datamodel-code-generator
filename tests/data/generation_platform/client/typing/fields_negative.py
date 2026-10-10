"""Give fields no overload takes: with a body, without a required one, of another media, or of a wrong type."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets_models import (
    FieldPetsPetIdOwnerPutPathPetIdParameter,
    FieldPetsPetIdPatchPathPetIdParameter,
    FieldPetsPetIdVisitsPostPathPetIdParameter,
    Kind,
    NewPet,
)


def refuse(
    client: Client,
    kind: Kind,
    body: NewPet,
    pet: FieldPetsPetIdPatchPathPetIdParameter,
    visit: FieldPetsPetIdVisitsPostPathPetIdParameter,
    owner_id: FieldPetsPetIdOwnerPutPathPetIdParameter,
) -> None:
    pets = client.default
    pets.create_pet(body=body, name="Mimi", media_type="application/json")  # error
    pets.create_pet(name="Mimi", media_type="application/json")  # error
    pets.create_pet(name="Mimi", kind=kind, media_type="application/x-www-form-urlencoded")  # error
    pets.create_pet(name=5, kind=kind, media_type="application/json")  # error
    pets.create_pet(name="Mimi", pet_tag=None, media_type="application/x-www-form-urlencoded")  # error
    pets.update_pet(petId=pet, media_type="application/json")  # error
    pets.update_pet(petId=pet, name=None)  # error
    pets.log_visit(petId=visit, note="n", media_type="text/plain")  # error
    pets.set_owner(petId=owner_id, email="e")  # error
    pets.create_owner()  # error


async def refuse_async(client: AsyncClient, body: NewPet) -> None:
    await client.default.create_pet(body=body, name="Mimi", media_type="application/json")  # error
