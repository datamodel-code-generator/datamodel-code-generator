"""Leave optional members of Pydantic dataclasses unset as MISSING, send null where it is allowed, and read both."""

from __future__ import annotations

from pydantic import MISSING
from typing_extensions import assert_type

from pets import AsyncClient, Client
from pets_models import LoginPostRequest, PetsGetQueryFilterParameter, Pet


def send(client: Client) -> None:
    client.default.create_pet(body=Pet(name="a", owner=None))
    client.default.create_pet(body=Pet(name="a", owner=None, tag=MISSING, nick=None))
    client.default.login(body=LoginPostRequest(user="ann", note=None))
    client.default.list_pets(filter=PetsGetQueryFilterParameter(owner=None))


def read(client: Client) -> None:
    for pet in client.default.list_pets():
        if pet.tag is not MISSING:
            assert_type(pet.tag, str)
        if pet.nick is not MISSING and pet.nick is not None:
            assert_type(pet.nick, str)


async def read_async(client: AsyncClient) -> None:
    pet = await client.default.create_pet(body=Pet(name="a", owner=None))
    assert_type(pet.tag, str | MISSING)
