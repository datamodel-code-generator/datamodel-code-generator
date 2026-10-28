"""Read each response union whole, as one value of its generated alias or root model."""

from __future__ import annotations

from typing_extensions import assert_type

from pets import AsyncClient, Client
from pets.responses import Response
from pets.types.default import GetPetResponse, GetShapeResponse


def read(client: Client) -> None:
    assert_type(client.default.get_shape(), GetShapeResponse)
    assert_type(client.default.get_pet(), GetPetResponse)
    assert_type(client.default.with_response.get_shape(), Response[GetShapeResponse])


async def read_async(client: AsyncClient) -> None:
    assert_type(await client.default.get_shape(), GetShapeResponse)
    assert_type(await client.default.get_pet(), GetPetResponse)
