"""Use a generated client as typed code: calls, media overloads, results, errors, and header accessors."""

from __future__ import annotations

from typing_extensions import assert_type

from pets import AsyncClient, Client
from pets.bodies import AsyncBodyAttempt, BodyAttempt
from pets.model_codecs import ModelValue
from pets.options import UNSET, RequestOptions
from pets.responses import Response
from pets.transports import (
    AsyncTransportResponse,
    AttemptIOContext,
    OwnedTransportAdapter,
    PreparedRequest,
    TransportCapabilities,
    TransportResponse,
)
from pets.types.pets.photos import UploadRequestCodecs
from pets.types.pets import (
    CreatePetRequestCodecs,
    CreatePetResponse,
    GetPetRequestCodecs,
    ListPetsErrorData,
    ListPetsHTTPError,
    ListPetsRequestCodecs,
    ListPetsResponse,
    decode_list_pets_header,
)


def call(client: Client) -> None:
    trace = ListPetsRequestCodecs.parameter(location="header", name="X-Trace").from_wire("t")
    pet = GetPetRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    assert_type(client.pets.list_pets(x_trace=trace, limit=UNSET), ListPetsResponse)
    response = client.pets.with_response.list_pets(x_trace=trace, options=RequestOptions(max_response_bytes=None))
    assert_type(response, Response[ListPetsResponse])
    decode_list_pets_header(response.info, name="X-Next")
    decode_list_pets_header(response.info, name="X-Rate")
    body = CreatePetRequestCodecs.body(media_type="application/json").from_wire({"name": "dog"})
    assert_type(client.pets.create_pet(body=body, media_type="application/json"), CreatePetResponse)
    text = CreatePetRequestCodecs.body(media_type="text/plain").from_wire("dog")
    client.pets.create_pet(body=text, media_type="text/plain")
    client.pets.get_pet(pet_id=pet, response_media_type="text/plain")
    photo = UploadRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    client.pets.photos.upload(pet_id=photo, body=b"\x00")
    try:
        client.pets.list_pets(x_trace=trace)
    except ListPetsHTTPError as error:
        assert_type(error.error_data, ListPetsErrorData | None)


async def call_async(client: AsyncClient) -> None:
    trace = ListPetsRequestCodecs.parameter(location="header", name="X-Trace").from_wire("t")
    assert_type(await client.pets.list_pets(x_trace=trace), ListPetsResponse)
    response = await client.pets.with_response.list_pets(x_trace=trace)
    assert_type(response, Response[ListPetsResponse])
    value: ModelValue[object] | None = None
    del value
    await client.aclose()


class Adapter:
    """A synchronous transport adapter, structurally."""

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(internal_retry_limit=0, delivery_evidence=False, http_versions=("HTTP/1.1",))

    def send(self, request: PreparedRequest[BodyAttempt], context: AttemptIOContext) -> TransportResponse:
        raise NotImplementedError

    def close(self) -> None:
        pass


class AsyncAdapter:
    """An async transport adapter, structurally."""

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(internal_retry_limit=None, delivery_evidence=True, http_versions=("HTTP/2",))

    async def send(self, request: PreparedRequest[AsyncBodyAttempt], context: AttemptIOContext) -> AsyncTransportResponse:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


def transports() -> None:
    borrowed = Client(transport_adapter=Adapter())
    assert_type(borrowed.with_options(RequestOptions(cleanup_timeout=1.0)), Client)
    owned = OwnedTransportAdapter(Adapter())
    assert_type(owned, OwnedTransportAdapter[Adapter])
    Client(transport_adapter=owned)
    assert_type(AsyncClient(transport_adapter=OwnedTransportAdapter(AsyncAdapter())).with_options(RequestOptions()), AsyncClient)
