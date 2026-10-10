"""Forward known keywords to generated methods, which both signature styles type alike."""

from __future__ import annotations

from typing_extensions import NotRequired, TypedDict, assert_type

from pets import AsyncClient, Client
from pets.options import RequestOptions
from pets.responses import Response
from pets.types.pets import ListPetsResponse
from pets_models import FieldPetsGetHeaderXTraceParameter


class ListArguments(TypedDict):
    """Keywords of list_pets that a caller declares for itself."""

    X_Trace: FieldPetsGetHeaderXTraceParameter
    options: NotRequired[RequestOptions | None]


def forward(client: Client, arguments: ListArguments) -> None:
    assert_type(client.pets.list_pets(**arguments), ListPetsResponse)
    assert_type(client.pets.with_response.list_pets(**arguments), Response[ListPetsResponse])


async def forward_async(client: AsyncClient, arguments: ListArguments) -> None:
    assert_type(await client.pets.list_pets(**arguments), ListPetsResponse)
