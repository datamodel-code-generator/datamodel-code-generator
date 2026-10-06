"""Use a generated client as typed code: calls, media overloads, results, errors, and header accessors."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Set
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from pathlib import Path
from ssl import SSLContext, create_default_context
from typing import BinaryIO, Literal

from typing_extensions import assert_type

from pets import AsyncClient, Client
from pets.auth import OAuthProviderOptions
from pets.bodies import (
    AsyncBinaryBody,
    AsyncBodyAttempt,
    AsyncBodyFactory,
    AsyncFileBody,
    AsyncMultipartBody,
    AsyncStreamBody,
    BodyAttempt,
    BodyAttemptContext,
    BodyFactory,
    DecodedPart,
    FieldPart,
    FileBody,
    FilePart,
    MultipartBody,
    MultipartData,
    StreamBody,
    SyncBinaryBody,
)
from pets.model_codecs import JSONValue, ModelValue, NativeOutboundCodec, RequestMedia, ResponseMedia, WireValue
from pets.errors import (
    BudgetExceededError,
    DeadlineExceededError,
    DeliveryState,
    HookExecutionError,
    LimiterExecutionError,
    PhaseTimeoutError,
    RedirectPolicyError,
    RequestCancelledError,
    SDKError,
)
from pets.hooks import AsyncLimiter, AsyncPermit, CallEvent, Limiter, LimiterContext, Permit
from pets.options import (
    UNSET,
    CancelToken,
    ClientOptions,
    Clock,
    Deadline,
    IdempotencyKey,
    RedirectOptions,
    RequestOptions,
    RetryOptions,
    TimeoutOptions,
    TransportOptions,
    Unset,
    ValidationOptions,
)
from pets.responses import AsyncRawResponse, HeadersView, RawResponse, Response, ResponseInfo
from pets.transports import (
    AsyncTransportResponse,
    AttemptIOContext,
    OwnedTransportAdapter,
    PreparedRequest,
    TransportCapabilities,
    TransportResponse,
)
from pets.types.pets.photos import UploadRequestCodecs, UploadResponse
from pets.types.pets import (
    AttachFilesRequestCodecs,
    CreatePetRequestCodecs,
    CreatePetResponse,
    GetPetRequestCodecs,
    ListPetsErrorData,
    ListPetsHTTPError,
    ListPetsRequestCodecs,
    ListPetsResponse,
    ReadFilesRequestCodecs,
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


def raw(client: Client, sink: BinaryIO) -> None:
    trace = ListPetsRequestCodecs.parameter(location="header", name="X-Trace").from_wire("t")
    pet = GetPetRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    saved = client.pets.with_raw_response.list_pets(x_trace=trace)
    assert_type(saved, RawResponse)
    assert_type(saved.info, ResponseInfo)
    assert_type(saved.body_bytes, bytes)
    assert_type(saved.read(), bytes)
    assert_type(saved.text(), str)
    assert_type(saved.json(), JSONValue)
    saved.raise_for_status()
    body = CreatePetRequestCodecs.body(media_type="application/json").from_wire({"name": "dog"})
    manager = client.pets.with_streaming_response.create_pet(body=body, media_type="application/json")
    assert_type(manager, AbstractContextManager[RawResponse])
    with client.pets.with_streaming_response.get_pet(pet_id=pet, response_media_type="text/plain") as streamed:
        for chunk in streamed.iter_bytes():
            assert_type(chunk, bytes)
        streamed.stream_to(sink)
    assert_type(client.request_raw("POST", "https://example.com/hooks", body=b"{}"), RawResponse)
    with client.with_streaming_response.request_raw("GET", "https://example.com/file") as download:
        download.stream_to("file.bin", overwrite=True)


async def raw_async(client: AsyncClient) -> None:
    trace = ListPetsRequestCodecs.parameter(location="header", name="X-Trace").from_wire("t")
    pet = GetPetRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    saved = await client.pets.with_raw_response.list_pets(x_trace=trace)
    assert_type(saved, AsyncRawResponse)
    assert_type(await saved.read(), bytes)
    assert_type(await saved.json(), JSONValue)
    await saved.raise_for_status()
    manager = client.pets.with_streaming_response.get_pet(pet_id=pet)
    assert_type(manager, AbstractAsyncContextManager[AsyncRawResponse])
    async with manager as streamed:
        async for chunk in streamed.iter_raw_bytes():
            assert_type(chunk, bytes)
    assert_type(await client.request_raw("GET", "https://example.com"), AsyncRawResponse)
    async with client.with_streaming_response.request_raw("GET", "https://example.com") as download:
        await download.stream_to("file.bin")


def build(context: BodyAttemptContext) -> BodyAttempt:
    raise NotImplementedError(context.call_id)


async def abuild(context: BodyAttemptContext) -> AsyncBodyAttempt:
    raise NotImplementedError(context.attempt_index)


def bodies(client: Client, file: BinaryIO) -> None:
    photo = UploadRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    inputs: tuple[SyncBinaryBody, ...] = (
        b"\x00",
        FileBody(file),
        FileBody(file, ownership="owned"),
        FileBody.from_path("photo.png"),
        StreamBody([b"a", b"b"]),
        StreamBody(iter([b"a"]), ownership="owned"),
        BodyFactory(build, content_length=1, content_type="image/png", fingerprint=b"f"),
    )
    for body in inputs:
        client.pets.photos.upload(pet_id=photo, body=body)
    assert_type(BodyFactory(build).content_type, str | None)
    client.request_raw("PUT", "https://example.com/file", body=FileBody.from_path("photo.png"))


async def bodies_async(client: AsyncClient, file: BinaryIO) -> None:
    photo = UploadRequestCodecs.parameter(location="path", name="petId").from_wire(1)

    async def chunks() -> AsyncIterator[bytes]:
        yield b"a"

    body = AsyncFileBody.from_path("photo.png")
    inputs: tuple[AsyncBinaryBody, ...] = (
        b"\x00",
        AsyncFileBody(file),
        body,
        AsyncStreamBody(chunks()),
        AsyncBodyFactory(abuild),
    )
    for each in inputs:
        await client.pets.photos.upload(pet_id=photo, body=each)
    await client.request_raw("PUT", "https://example.com/file", body=AsyncStreamBody(chunks(), ownership="owned"))
    await body.aclose()
    body.close()


def multipart(client: Client, file: BinaryIO) -> None:
    name: FieldPart[str] = FieldPart("name", "Ada")
    count = FieldPart("count", 3, content_type="text/plain", headers=(("X-Trace", "t"),))
    photo = FilePart("photo", FileBody(file), filename="a.png", content_type="image/png")
    assert_type(photo.content, FileBody)
    parts: MultipartBody[str | int] = MultipartBody((name, count, photo))
    assert_type(parts.parts, tuple[FilePart[SyncBinaryBody] | FieldPart[str | int], ...])
    body = MultipartBody[WireValue]((FieldPart("meta", {"k": 1}), FieldPart("skipped", UNSET), FilePart("f", b"x")))
    client.request_raw("POST", "https://example.com/forms", body=body)


def file_parts(client: Client, file: BinaryIO) -> None:
    note = FieldPart("note", "hello")
    labels = FieldPart("labels", AttachFilesRequestCodecs.part(name="labels").from_wire(["a", "b"]))
    assert_type(labels, FieldPart[ModelValue[list[str]]])
    pet = AttachFilesRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    client.pets.attach_files(pet_id=pet, body=MultipartBody((note, labels, FilePart("file", FileBody(file)))))
    codec = AttachFilesRequestCodecs.part(name="note", media_type="multipart/form-data")
    assert_type(codec, NativeOutboundCodec[str])
    assert_type(AttachFilesRequestCodecs.part(name="file"), NativeOutboundCodec[str] | NativeOutboundCodec[list[str]])
    files = client.pets.read_files(pet_id=ReadFilesRequestCodecs.parameter(location="path", name="petId").from_wire(1))
    assert_type(files, MultipartData[str | bytes])
    for part in files.parts:
        assert_type(part.value, str | bytes)


def multipart_data(data: MultipartData[bytes]) -> None:
    for part in data.parts:
        assert_type(part, DecodedPart[bytes])
        assert_type(part.value, bytes)
        assert_type(part.name, str | None)
        assert_type(part.filename, str | None)
        assert_type(part.content_type, str | None)
        assert_type(part.headers, HeadersView)
    widened: MultipartData[bytes | str] = data
    del widened


def media_selectors(client: Client, file: BinaryIO) -> None:
    photo = UploadRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    sent = UploadRequestCodecs.select_request_media(
        declared_media="application/octet-stream", concrete_media="application/octet-stream"
    )
    assert_type(sent, RequestMedia[SyncBinaryBody, AsyncBinaryBody])
    accepted = UploadRequestCodecs.select_response_media(declared_media="image/*", concrete_media="image/png")
    assert_type(accepted, ResponseMedia[UploadResponse])
    assert_type(accepted.concrete_media, str)
    stored = client.pets.photos.upload(
        pet_id=photo, body=FileBody(file), media_type=sent, response_media_type=accepted
    )
    assert_type(stored, UploadResponse)


async def media_selectors_async(client: AsyncClient) -> None:
    photo = UploadRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    sent = UploadRequestCodecs.select_request_media(
        declared_media="application/octet-stream", concrete_media="application/octet-stream"
    )
    await client.pets.photos.upload(pet_id=photo, body=AsyncFileBody.from_path("a.png"), media_type=sent)


async def multipart_async(client: AsyncClient) -> None:
    body = AsyncMultipartBody[WireValue]((FieldPart("a", "x"), FilePart("f", AsyncFileBody.from_path("a.bin"))))
    await client.request_raw("POST", "https://example.com/forms", body=body)


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

    async def send(
        self, request: PreparedRequest[AsyncBodyAttempt], context: AttemptIOContext
    ) -> AsyncTransportResponse:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


def transports() -> None:
    borrowed = Client(transport_adapter=Adapter())
    assert_type(borrowed.with_options(RequestOptions(cleanup_timeout=1.0)), Client)
    owned = OwnedTransportAdapter(Adapter())
    assert_type(owned, OwnedTransportAdapter[Adapter])
    Client(transport_adapter=owned)
    assert_type(
        AsyncClient(transport_adapter=OwnedTransportAdapter(AsyncAdapter())).with_options(RequestOptions()),
        AsyncClient,
    )


class Tracer:
    def on_event(self, event: CallEvent) -> None:
        del event


class AsyncTracer:
    async def on_event(self, event: CallEvent) -> None:
        del event


def hooks(client: Client) -> None:
    pet = GetPetRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    options = ClientOptions(hooks=(Tracer(), AsyncTracer()), context={"tenant": "t", "retry": 1, "user": None})
    Client(options=options)
    try:
        client.pets.get_pet(pet_id=pet, options=RequestOptions(hooks=(), context={"retry": 2}))
    except HookExecutionError as error:
        assert_type(error.require_result().info.status_code, int)
        assert_type(error.has_completed_result, bool)


def validation(client: Client) -> None:
    pet = GetPetRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    strict = Client(options=ClientOptions(validation=ValidationOptions(request="schema", response="schema")))
    view = strict.with_options(RequestOptions(validation=ValidationOptions(response="native")))
    view.pets.get_pet(pet_id=pet, options=RequestOptions(validation=ValidationOptions(request="none")))
    inherited = ValidationOptions()
    assert_type(inherited.request, Literal["none", "native", "schema"] | Unset)
    assert_type(inherited.response, Literal["native", "schema"] | Unset)
    assert_type(ValidationOptions(request=UNSET).request, Literal["none", "native", "schema"] | Unset)


def timing_options(client: Client, context: AttemptIOContext) -> None:
    deadline = Deadline.after(60)
    token = CancelToken()
    assert_type(deadline.at, float)
    assert_type(deadline.remaining(), float)
    assert_type(deadline.clock, Clock)
    clock = Clock(monotonic=lambda: 0.0, random=lambda: 0.5)
    assert_type(Deadline.after(1, clock=clock), Deadline)
    assert_type(OAuthProviderOptions(clock=clock).clock, Clock)
    assert_type(token.cancelled, bool)
    assert_type(context.deadline, Deadline | None)
    assert_type(context.cancel_token, CancelToken | None)
    phases = TimeoutOptions(connect=1, read=None, write=UNSET, pool=0)
    assert_type(phases.read, float | Unset | None)
    configured = ClientOptions(
        timeout=phases,
        total_timeout=None,
        deadline=deadline,
        cancel_token=token,
        max_network_sends=1,
        stream_idle_timeout=60,
        stream_total_timeout=None,
        cleanup_timeout=5,
        clock=clock,
    )
    assert_type(configured.clock, Clock | Unset)
    assert_type(configured.timeout, TimeoutOptions | Unset | None)
    assert_type(configured.total_timeout, float | Unset | None)
    assert_type(configured.deadline, Deadline | Unset | None)
    assert_type(configured.cancel_token, CancelToken | Unset | None)
    assert_type(configured.limiter, Limiter | AsyncLimiter | Unset | None)
    assert_type(configured.max_network_sends, int | Unset | None)
    assert_type(configured.stream_idle_timeout, float | Unset | None)
    assert_type(configured.stream_total_timeout, float | Unset | None)
    Client(options=configured)
    assert_type(client.with_options(RequestOptions(timeout=None, total_timeout=0, max_network_sends=None)), Client)
    client.with_options(RequestOptions(deadline=None, cancel_token=None, limiter=None))
    token.cancel()


def read_with_budget(client: Client, url: str) -> bytes:
    deadline = Deadline.after(10)
    options = RequestOptions(total_timeout=20, deadline=deadline, timeout=TimeoutOptions(read=3))
    with client.with_options(options) as view:
        response = view.request_raw("GET", url, options=RequestOptions(timeout=TimeoutOptions(connect=2)))
        return response.read()


class SteppedClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def on_event(self, event: CallEvent) -> None:
        if event.name == "retry_scheduled" and event.duration is not None:
            self.now += event.duration


def instant_retries(url: str) -> Client:
    stepped = SteppedClock()
    clock = Clock(monotonic=stepped, random=lambda: 0.5)
    return Client(options=ClientOptions(base_url=url, retry=RetryOptions(), hooks=(stepped,), clock=clock))


def cancel_before_send(client: Client, url: str) -> int:
    token = CancelToken()
    token.cancel()
    try:
        client.request_raw("GET", url, options=RequestOptions(cancel_token=token))
    except RequestCancelledError as error:
        return error.network_send_count
    raise RuntimeError("The cancelled call unexpectedly completed")


def download(client: Client, url: str, destination: BinaryIO) -> None:
    options = RequestOptions(total_timeout=10, stream_idle_timeout=60, stream_total_timeout=300)
    with client.with_streaming_response.request_raw("GET", url, options=options) as response:
        response.stream_to(destination)


class SemaphorePermit:
    """Release one asyncio semaphore slot exactly once."""

    def __init__(self, semaphore: asyncio.Semaphore) -> None:
        self._semaphore = semaphore
        self._released = False

    async def release(self) -> None:
        if not self._released:
            self._released = True
            self._semaphore.release()


class SemaphoreLimiter:
    """Share one concurrency limit among calls made through the configured client or view."""

    def __init__(self, limit: int) -> None:
        self._semaphore = asyncio.Semaphore(limit)

    async def acquire(self, context: LimiterContext) -> AsyncPermit:
        await self._semaphore.acquire()
        return SemaphorePermit(self._semaphore)


async def read_limited(client: AsyncClient, urls: tuple[str, ...]) -> tuple[bytes, ...]:
    limiter: AsyncLimiter = SemaphoreLimiter(4)
    async with client.with_options(RequestOptions(limiter=limiter)) as view:

        async def read(url: str) -> bytes:
            response = await view.request_raw("GET", url)
            return await response.read()

        return tuple(await asyncio.gather(*(read(url) for url in urls)))


def limiter_contract(limiter: Limiter, context: LimiterContext) -> None:
    assert_type(context.operation_id, str | None)
    assert_type(context.origin, str)
    assert_type(context.call_id, str)
    assert_type(context.parent_session_id, str | None)
    assert_type(context.remaining_timeout, float | None)
    assert_type(context.cancel_token, CancelToken | None)
    permit = limiter.acquire(context)
    assert_type(permit, Permit)
    permit.release()
    Client(options=ClientOptions(limiter=limiter))


async def async_limiter_contract(limiter: AsyncLimiter, context: LimiterContext) -> None:
    permit = await limiter.acquire(context)
    assert_type(permit, AsyncPermit)
    await permit.release()
    AsyncClient(options=ClientOptions(limiter=limiter))


def error_counters(error: SDKError, event: CallEvent, info: ResponseInfo) -> None:
    assert_type(error.resource_attempt_count, int)
    assert_type(error.redirect_count, int)
    assert_type(error.auth_exchange_count, int)
    assert_type(error.network_send_count, int)
    assert_type(error.network_send_budget_used, int)
    assert_type(error.auth_exchange_budget_used, int)
    assert_type(error.auth_refresh_ids, tuple[str, ...])
    assert_type(error.auth_refresh_pending, int)
    assert_type(error.wire_send_count, int | None)
    assert_type(event.resource_attempt_count, int)
    assert_type(event.network_send_count, int)
    assert_type(event.network_send_budget_used, int)
    assert_type(event.auth_refresh_ids, tuple[str, ...])
    assert_type(info.resource_attempt_count, int)
    assert_type(info.network_send_count, int)
    assert_type(info.network_send_budget_used, int)
    assert_type(info.wire_send_count, int | None)
    phase = PhaseTimeoutError(effective_timeout=1, phase="connect", delivery_state=DeliveryState.NOT_SENT)
    assert_type(phase.effective_timeout, float)
    deadline = DeadlineExceededError(deadline_at=0, elapsed=1, delivery_state=DeliveryState.NOT_SENT)
    assert_type(deadline.deadline_at, float)
    assert_type(deadline.elapsed, float)
    cancelled = RequestCancelledError(source="cancel_token", delivery_state=DeliveryState.NOT_SENT)
    assert_type(cancelled.source, Literal["cancel_token", "parent_cancel_token"])
    budget = BudgetExceededError(budget_kind="network", limit=0, used=0)
    assert_type(budget.limit, int)
    assert_type(budget.used, int)
    limiter = LimiterExecutionError(action="release")
    assert_type(limiter.action, Literal["acquire", "release"])
    PhaseTimeoutError(
        effective_timeout=0.5,
        phase="read",
        delivery_state=DeliveryState.RESPONSE_STARTED,
        resource_attempt_count=1,
        network_send_count=1,
        network_send_budget_used=1,
        auth_refresh_ids=(),
        wire_send_count=None,
    )
    redirect = RedirectPolicyError(delivery_state=DeliveryState.RESPONSE_STARTED, info=info, body_available=False)
    assert_type(redirect.body_available, Literal[False])
    assert_type(redirect.delivery_state, DeliveryState)
    assert_type(redirect.info, ResponseInfo | None)


def retry_options(client: Client) -> None:
    retry = RetryOptions(
        max_retries=3,
        initial_delay=0.25,
        max_delay=2,
        jitter="none",
        statuses={429, 503},
        max_retry_after=None,
        respect_retry_after=True,
        retry_after_ms_header=None,
        should_retry_header=UNSET,
        retry_on_pool_timeout=False,
    )
    assert_type(retry.statuses, Set[int] | Unset)
    assert_type(retry.jitter, Literal["full", "none"] | Unset)
    assert_type(retry.max_retry_after, float | Unset | None)
    client.with_options(RequestOptions(retry=retry, redirects=RedirectOptions(enabled=True, max_redirects=2)))
    Client(options=ClientOptions(retry=RetryOptions(max_retries=0), redirects=RedirectOptions()))
    redirects = RedirectOptions(
        allow_303_to_get=True,
        allowed_origins=("https://download.example.com",),
        allow_https_downgrade=False,
    )
    assert_type(redirects.allowed_origins, tuple[str, ...] | Unset)


def fetch_with_retries(client: Client, url: str) -> bytes:
    options = RequestOptions(
        retry=RetryOptions(max_retries=2, max_retry_after=20),
        redirects=RedirectOptions(enabled=True, max_redirects=2),
        total_timeout=30,
    )
    return client.request_raw("GET", url, options=options).read()


def keyed_view(client: Client, value: str) -> Client:
    key = IdempotencyKey(value)
    return client.with_options(RequestOptions(idempotency_key=key))


def upload_file(client: Client, url: str, path: Path) -> bytes:
    response = client.request_raw(
        "PUT", url, body=FileBody.from_path(path), options=RequestOptions(retry=RetryOptions(max_retries=2))
    )
    return response.read()


def configured_client(ca_file: str) -> Client:
    context = create_default_context(cafile=ca_file)
    transport = TransportOptions(ssl_context=context, max_connections=50, max_keepalive_connections=10)
    return Client(options=ClientOptions(transport=transport))


def idempotency_input(client: Client) -> None:
    key = IdempotencyKey("stored-key")
    assert_type(key.value, str)
    assert_type(IdempotencyKey.new(), IdempotencyKey)
    client.with_options(RequestOptions(idempotency_key=key))
    Client(options=ClientOptions(idempotency_key=IdempotencyKey.new()))
    client.with_options(RequestOptions(idempotency_key=None))


def transport_options(context: SSLContext) -> None:
    transport = TransportOptions(
        ssl_context=context,
        proxy="http://proxy.example.com:8080",
        trust_env=False,
        http2=False,
        max_connections=50,
        max_keepalive_connections=10,
        keepalive_expiry=5,
        retry_owner="sdk",
    )
    assert_type(transport.verify, bool | Unset)
    assert_type(transport.ssl_context, SSLContext | None)
    assert_type(transport.retry_owner, Literal["sdk", "transport"])
    Client(options=ClientOptions(transport=transport))
    AsyncClient(options=ClientOptions(transport=TransportOptions(verify=True)))
