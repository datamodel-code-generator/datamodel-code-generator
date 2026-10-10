"""Give a body's fields instead of the body: each media's fields, an optional body's witnesses, and every view."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, AbstractContextManager

from typing_extensions import assert_type

from pets import AsyncClient, Client
from pets.options import UNSET
from pets.responses import AsyncRawResponse, RawResponse, Response
from pets.types.default import CreatePetResponse
from pets_models import (
    FieldPetsPetIdOwnerPutPathPetIdParameter,
    FieldPetsPetIdPatchPathPetIdParameter,
    FieldPetsPetIdVisitsPostPathPetIdParameter,
    Kind,
    NewPet,
    Owner,
)


def create(client: Client, kind: Kind, owner: Owner, body: NewPet) -> None:
    pets = client.default
    assert_type(pets.create_pet(name="Mimi", kind=kind, media_type="application/json"), CreatePetResponse)
    assert_type(
        pets.create_pet(name="Mimi", kind=kind, pet_tag=None, owner=owner, secret="s", media_type="application/json"),
        CreatePetResponse,
    )
    assert_type(pets.create_pet(name="Mimi", pet_tag="t", media_type="application/x-www-form-urlencoded"), CreatePetResponse)
    assert_type(pets.create_pet(body=body, media_type="application/json"), CreatePetResponse)
    assert_type(pets.create_pet(body=UNSET, name="Mimi", kind=kind, media_type="application/json"), CreatePetResponse)
    assert_type(pets.create_pet(body=body, name=UNSET, media_type="application/json"), CreatePetResponse)
    assert_type(
        pets.with_response.create_pet(name="Mimi", kind=kind, media_type="application/json"), Response[CreatePetResponse]
    )
    assert_type(pets.with_raw_response.create_pet(name="Mimi", kind=kind, media_type="application/json"), RawResponse)
    assert_type(
        pets.with_streaming_response.create_pet(name="Mimi", kind=kind, media_type="application/json"),
        AbstractContextManager[RawResponse],
    )


def update(
    client: Client,
    pet: FieldPetsPetIdPatchPathPetIdParameter,
    visit: FieldPetsPetIdVisitsPostPathPetIdParameter,
    owner_id: FieldPetsPetIdOwnerPutPathPetIdParameter,
    owner: Owner,
) -> None:
    pets = client.default
    pets.update_pet(petId=pet)
    pets.update_pet(petId=pet, name="n")
    pets.update_pet(petId=pet, tag=None)
    pets.update_pet(petId=pet, name="n", tag="t", media_type="application/json")
    pets.log_visit(petId=visit, media_type="application/json")
    pets.log_visit(petId=visit, note="n", visit_options=["a"], media_type="application/json")
    pets.log_visit(petId=visit, body="n", media_type="text/plain")
    pets.set_owner(petId=owner_id, body=owner)
    pets.create_owner(email="e", nickName="n")


async def create_async(client: AsyncClient, kind: Kind) -> None:
    pets = client.default
    assert_type(await pets.create_pet(name="Mimi", kind=kind, media_type="application/json"), CreatePetResponse)
    assert_type(
        pets.with_streaming_response.create_pet(name="Mimi", kind=kind, media_type="application/json"),
        AbstractAsyncContextManager[AsyncRawResponse],
    )
