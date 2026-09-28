"""Drive generated clients through custom transport adapters, views, and closing, and report every exchange."""

from __future__ import annotations

import asyncio
import importlib
import threading
import time
from typing import TYPE_CHECKING, Any

import httpx2

from tests.data.python.client_runtime import Exchange, arecord, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from types import ModuleType

_PET = b'{"id":3,"name":"fox"}'


class Stop(BaseException):
    """An interruption that is not an Exception, as KeyboardInterrupt is."""


def _modules(package: ModuleType) -> tuple[ModuleType, ModuleType, ModuleType, ModuleType]:
    transports, errors, options, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("transports", "errors", "options", "responses")
    )
    return transports, errors, options, responses


def _pet(package: ModuleType, operation: str = "GetPet") -> object:
    types = importlib.import_module(f"{package.__name__}.types.pets")
    return getattr(types, f"{operation}RequestCodecs").parameter(location="path", name="petId").from_wire(3)


class Response:
    """A canned response that yields its chunks, raising any exception among them, and records its close."""

    def __init__(
        self, lines: list[str], status: object, headers: object, chunks: tuple[object, ...], *, close_error: bool = False
    ) -> None:
        self.lines = lines
        self.status_code = status
        self.headers = headers
        self.chunks = chunks
        self.close_error = close_error

    def iter_raw_bytes(self) -> Iterator[object]:
        for chunk in self.chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk

    def close(self) -> None:
        self.lines.append("  < closed")
        if self.close_error:
            msg = "close failed"
            raise RuntimeError(msg)


class AsyncResponse(Response):
    """The async form of a canned response."""

    async def iter_raw_bytes(self) -> AsyncIterator[object]:
        for chunk in self.chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk

    async def aclose(self) -> None:
        self.close()


def _request(lines: list[str], request: Any, context: Any, body: bytes | None) -> None:
    headers = ", ".join(f"{name}: {value}" for name, value in request.headers)
    length = None if request.body is None else request.body.content_length
    media = None if request.body is None else request.body.content_type
    timeout = context.timeout
    lines.append(f"  > {request.method} {request.url} [{headers}] {body!r} {length} {media}")
    lines.append(f"    {context.phase} {timeout.connect}/{timeout.read}/{timeout.write}/{timeout.pool}")


class Adapter:
    """A synchronous adapter answering from a queue of replies, each a callable of the request and context."""

    def __init__(self, transports: ModuleType, lines: list[str], *, evidence: bool = False) -> None:
        self.capabilities = transports.TransportCapabilities(
            internal_retry_limit=0, delivery_evidence=evidence, http_versions=("HTTP/1.1",)
        )
        self.lines = lines
        self.replies: list[Callable[[Any, Any], Any]] = []
        self.close_error = False

    def send(self, request: Any, context: Any) -> Any:
        body = None if request.body is None else b"".join(request.body.iter_bytes())
        _request(self.lines, request, context, body)
        return self.replies.pop(0)(request, context)

    def close(self) -> None:
        self.lines.append("  adapter closed")
        if self.close_error:
            msg = "adapter close failed"
            raise RuntimeError(msg)


class AsyncAdapter(Adapter):
    """The async form of the queued adapter."""

    async def send(self, request: Any, context: Any) -> Any:
        body = None if request.body is None else b"".join([chunk async for chunk in request.body.aiter_bytes()])
        _request(self.lines, request, context, body)
        return self.replies.pop(0)(request, context)

    async def aclose(self) -> None:
        self.close()


def _ok(lines: list[str], responses: ModuleType, response: type[Response] = Response, **options: Any) -> Callable[[Any, Any], Any]:
    headers = responses.HeadersView([("content-type", "application/json")])
    return lambda request, context: response(lines, 200, headers, (b"", _PET[:5], _PET[5:]), **options)


def _created(lines: list[str], responses: ModuleType, response: type[Response]) -> Callable[[Any, Any], Any]:
    headers = responses.HeadersView([("content-type", "application/json")])
    return lambda request, context: response(lines, 201, headers, (_PET,))


def _traced(lines: list[str], responses: ModuleType, response: type[Response] = Response) -> Callable[[Any, Any], Any]:
    def reply(request: Any, context: Any) -> Any:
        trace = context.trace
        trace.phase_started("connect")
        trace.request_headers_started()
        trace.wire_send()
        trace.phase_started("read")
        headers = responses.HeadersView([("content-type", "application/json")])
        trace.response_headers_received(http_version="HTTP/1.1", status_code=200, headers=headers)
        lines.append(f"    traced {context.phase}")
        return response(lines, 200, headers, (_PET,))

    return reply


def _answered_then_failing(headers: object) -> Callable[[Any, Any], Any]:
    def reply(request: Any, context: Any) -> Any:
        context.trace.response_headers_received(http_version="HTTP/1.1", status_code=200, headers=headers)
        msg = "adapter bug after headers"
        raise ValueError(msg)

    return reply


def _raising(error: BaseException) -> Callable[[Any, Any], Any]:
    def reply(request: Any, context: Any) -> Any:
        raise error

    return reply


def _adapter_calls(
    package: ModuleType, api: Any, adapter: Adapter, lines: list[str], response: type[Response]
) -> tuple[Callable[[], Any], object]:
    """Queue the replies of the shared adapter cases; return the call that consumes each, and a request body."""
    transports, errors, _, responses = _modules(package)
    json = responses.HeadersView([("content-type", "application/json")])
    pet = _pet(package)
    adapter.replies.extend((
        _ok(lines, responses, response),
        _traced(lines, responses, response),
        _raising(errors.TransportError(delivery_state=errors.DeliveryState.NOT_SENT, phase="connect")),
        _raising(ValueError("adapter bug")),
        lambda request, context: response(lines, "200", json, ()),
        lambda request, context: response(lines, 99, json, ()),
        lambda request, context: response(lines, 200, [("content-type", "application/json")], ()),
        lambda request, context: context.trace.phase_started("bogus") or response(lines, 200, json, (_PET,)),
        lambda request, context: response(lines, 200, json, ("text",)),
        lambda request, context: response(lines, 200, json, (b"{", RuntimeError("read bug"))),
        lambda request, context: response(lines, 200, json, (_PET,), close_error=True),
        lambda request, context: response(lines, 200, json, (b"{", RuntimeError("read bug")), close_error=True),
        _answered_then_failing(json),
    ))
    body = importlib.import_module(f"{package.__name__}.types.pets").CreatePetRequestCodecs.body(
        media_type="application/json"
    ).from_wire({"name": "cat"})
    return lambda: api.pets.get_pet(pet_id=pet), body


_LABELS = (
    "adapter ok",
    "adapter traced",
    "adapter transport error",
    "adapter bug",
    "adapter status type",
    "adapter status range",
    "adapter headers type",
    "adapter phase",
    "adapter chunk type",
    "adapter read bug",
    "adapter close bug",
    "adapter read and close bugs",
    "adapter bug after headers",
)


def _secondary(call: Callable[[], object]) -> Callable[[], str]:
    """Report the class and secondary errors of a failed call."""

    def called() -> str:
        try:
            call()
        except Exception as error:  # noqa: BLE001
            return f"{type(error).__name__} secondary {[type(item).__name__ for item in error.secondary_errors]}"
        return "returned"

    return called


def transports(package: ModuleType, lines: list[str]) -> None:
    """Send through custom sync and async adapters: their requests, evidence, failures, and ownership."""
    transport_module, errors, options, responses = _modules(package)
    adapter = Adapter(transport_module, lines, evidence=True)
    with package.Client(transport_adapter=adapter) as api:
        call, body = _adapter_calls(package, api, adapter, lines, Response)
        for label in _LABELS:
            record(lines, label, _secondary(call) if label == "adapter read and close bugs" else call)
        adapter.replies.append(_created(lines, responses, Response))
        record(lines, "adapter body", lambda: api.pets.create_pet(body=body, media_type="application/json"))
        adapter.replies.append(_raising(Stop()))
        try:
            api.pets.get_pet(pet_id=_pet(package))
        except Stop:
            lines.append("  adapter stop propagated")
        adapter.replies.append(lambda request, context: Response(lines, 200, responses.HeadersView([]), (b"{", Stop())))
        try:
            api.pets.get_pet(pet_id=_pet(package))
        except Stop:
            lines.append("  adapter stop while reading propagated")
        adapter.replies.append(
            lambda request, context: Response(lines, 200, responses.HeadersView([]), (b"{", Stop()), close_error=True)
        )
        try:
            api.pets.get_pet(pet_id=_pet(package))
        except Stop:
            lines.append("  adapter stop propagated over a close failure")
    lines.append("  borrowed adapter left open")
    owned = Adapter(transport_module, lines)
    package.Client(transport_adapter=transport_module.OwnedTransportAdapter(owned)).close()
    failing = Adapter(transport_module, lines)
    failing.close_error = True
    record(lines, "owned adapter close bug", package.Client(transport_adapter=transport_module.OwnedTransportAdapter(failing)).close)
    http = httpx2.Client()
    for label, build in (
        ("adapter and http_client", lambda: package.Client(transport_adapter=adapter, http_client=http)),
        ("adapter owned by ownership", lambda: package.Client(transport_adapter=adapter, http_client_ownership="owned")),
        ("adapter type", lambda: package.Client(transport_adapter=object())),
        ("async adapter to sync client", lambda: package.Client(transport_adapter=AsyncAdapter(transport_module, lines))),
        ("adapter capabilities", lambda: package.Client(transport_adapter=_Undeclared())),
        ("sync adapter to async client", lambda: package.AsyncClient(transport_adapter=adapter)),
        ("async ownership", lambda: package.AsyncClient(http_client_ownership="shared")),
        ("async http_client", lambda: package.AsyncClient(http_client=http)),
    ):
        record(lines, label, build)
    http.close()
    run(lambda: _async_transports(package, lines))


class _Undeclared:
    capabilities = None

    def send(self, request: Any, context: Any) -> Any:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


async def _async_transports(package: ModuleType, lines: list[str]) -> None:
    transport_module, _, _, responses = _modules(package)
    adapter = AsyncAdapter(transport_module, lines)
    async with package.AsyncClient(transport_adapter=adapter) as api:
        call, body = _adapter_calls(package, api, adapter, lines, AsyncResponse)
        for label in _LABELS:
            if label == "adapter read and close bugs":
                await arecord(lines, f"async {label}", _asecondary(call))
            else:
                await arecord(lines, f"async {label}", call)
        adapter.replies.append(_created(lines, responses, AsyncResponse))
        await arecord(lines, "async adapter body", lambda: api.pets.create_pet(body=body, media_type="application/json"))
        adapter.replies.append(lambda request, context: AsyncResponse(lines, 200, responses.HeadersView([]), (b"{", Stop())))
        try:
            await api.pets.get_pet(pet_id=_pet(package))
        except Stop:
            lines.append("  async adapter stop while reading propagated")
    owned = AsyncAdapter(transport_module, lines)
    owned.close_error = True
    client = package.AsyncClient(transport_adapter=transport_module.OwnedTransportAdapter(owned))
    await arecord(lines, "async owned adapter close bug", client.aclose)
    sent: list[object] = []
    echo = Exchange(lines)
    echo.respond(lambda request: _header_echo(request, sent))
    unicode = echo.client()
    with package.Client(http_client=unicode) as api:
        trace = importlib.import_module(f"{package.__name__}.types.pets").ListPetsRequestCodecs.parameter(
            location="header", name="X-Trace"
        ).from_wire("café")
        record(lines, "unicode header", lambda: api.pets.with_response.list_pets(x_trace=trace).info.status_code)
    unicode.close()
    lines.append(f"  unicode header bytes {sent}")
    native = httpx2.AsyncClient(transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json={"id": 3})))
    async with package.AsyncClient(http_client=native) as api:
        await arecord(lines, "async pre-read response", lambda: api.pets.get_pet(pet_id=_pet(package)))
    await native.aclose()


def _header_echo(request: httpx2.Request, sent: list[object]) -> httpx2.Response:
    sent.extend(value for name, value in request.headers.raw if name.lower() == b"x-trace")
    content = b'[{"id":1,"name":"cat"}]'
    headers = {"content-type": "application/json", "x-rate": "1"}
    return httpx2.Response(200, headers=headers, stream=httpx2.ByteStream(content))


def _asecondary(call: Callable[[], Any]) -> Callable[[], Any]:
    async def called() -> str:
        try:
            await call()
        except Exception as error:  # noqa: BLE001
            return f"{type(error).__name__} secondary {[type(item).__name__ for item in error.secondary_errors]}"
        return "returned"

    return called


class _Blocking(Adapter):
    """An adapter whose response yields one chunk, then waits for an event before the rest or a failure."""

    def __init__(self, transports: ModuleType, lines: list[str], responses: ModuleType) -> None:
        super().__init__(transports, lines)
        self.started = threading.Event()
        self.proceed = threading.Event()
        self.responses = responses
        self.failure: BaseException | None = None

    def send(self, request: Any, context: Any) -> Any:
        adapter = self

        class Blocked(Response):
            def iter_raw_bytes(self) -> Iterator[object]:
                yield _PET[:5]
                adapter.started.set()
                adapter.proceed.wait()
                if adapter.failure is not None:
                    raise adapter.failure
                yield _PET[5:]

        return Blocked(self.lines, 200, self.responses.HeadersView([("content-type", "application/json")]), ())


def _closing(package: ModuleType, lines: list[str], label: str, cleanup: float, failure: object | None) -> None:
    """Close a client while one call is reading its body, then let the call go on, and report both outcomes."""
    transport_module, errors, options, responses = _modules(package)
    adapter = _Blocking(transport_module, lines, responses)
    api = package.Client(transport_adapter=adapter, options=options.ClientOptions(cleanup_timeout=cleanup))
    adapter.failure = failure
    outcome: list[str] = []
    call = threading.Thread(target=lambda: record(outcome, "call", lambda: api.pets.get_pet(pet_id=_pet(package))))
    call.start()
    adapter.started.wait()
    record(outcome, "probe", lambda: api.pets.get_pet(pet_id=_pet(package), options="refused only once closing"))
    closer = threading.Thread(target=lambda: record(outcome, "close", api.close))
    closer.start()
    deadline = time.monotonic() + 30
    while True:
        try:
            api.pets.get_pet(pet_id=_pet(package), options="refused only once closing")
        except errors.ClientClosedError:
            break
        except errors.ConfigurationError:
            if time.monotonic() > deadline:
                msg = "The client never started closing"
                raise TimeoutError(msg) from None
    if cleanup < 1:
        closer.join()
    adapter.proceed.set()
    call.join()
    closer.join()
    lines.append(f"  {label}")
    lines.extend(sorted(outcome))
    record(lines, f"{label} close again", api.close)


def lifecycle(package: ModuleType, lines: list[str]) -> None:
    """Refuse calls once closing, stop active calls at their next step, and keep views apart from their owner."""
    transport_module, errors, options, responses = _modules(package)
    adapter = Adapter(transport_module, lines)
    api = package.Client(transport_adapter=adapter)
    view = api.with_options(options.RequestOptions(base_url="https://view.example.com/v2"))
    other = api.with_options(options.RequestOptions())
    adapter.replies.extend((_ok(lines, responses), _ok(lines, responses), _ok(lines, responses)))
    pet = _pet(package)
    record(lines, "view call", lambda: view.pets.get_pet(pet_id=pet))
    view.close()
    view.close()
    record(lines, "closed view call", lambda: view.pets.get_pet(pet_id=pet))
    record(lines, "other view call", lambda: other.pets.get_pet(pet_id=pet))
    record(lines, "owner call", lambda: api.pets.get_pet(pet_id=pet))
    record(lines, "view options", lambda: api.with_options(None))
    api.close()
    record(lines, "closed owner call", lambda: api.pets.get_pet(pet_id=pet))
    record(lines, "view of closed owner call", lambda: other.pets.get_pet(pet_id=pet))
    lines.append(f"  borrowed adapter closes {'adapter closed' in lines}")
    for label, value in (("cleanup bool", True), ("cleanup zero", 0), ("cleanup infinite", float("inf")), ("cleanup text", "1")):
        record(lines, f"options {label}", lambda value=value: options.ClientOptions(cleanup_timeout=value))
    _closing(package, lines, "closing stops the call", 5.0, None)
    _closing(package, lines, "closing outlasts the cleanup time", 0.05, None)
    _closing(package, lines, "closing interrupts the read", 5.0, errors.TransportError(delivery_state=errors.DeliveryState.RESPONSE_STARTED, phase="read"))
    run(lambda: _async_lifecycle(package, lines))
    _backends(package, lines)


class _AsyncBlocking(AsyncAdapter):
    def __init__(self, transports: ModuleType, lines: list[str], responses: ModuleType) -> None:
        super().__init__(transports, lines)
        self.started = asyncio.Event()
        self.proceed = asyncio.Event()
        self.responses = responses

    async def send(self, request: Any, context: Any) -> Any:
        adapter = self

        class Blocked(AsyncResponse):
            async def iter_raw_bytes(self) -> AsyncIterator[object]:
                yield _PET[:5]
                adapter.started.set()
                await adapter.proceed.wait()
                yield _PET[5:]

        return Blocked(self.lines, 200, self.responses.HeadersView([("content-type", "application/json")]), ())


async def _async_lifecycle(package: ModuleType, lines: list[str]) -> None:
    transport_module, _, options, responses = _modules(package)
    for label, cleanup in (("async closing stops the call", 5.0), ("async closing outlasts the cleanup time", 0.05)):
        adapter = _AsyncBlocking(transport_module, lines, responses)
        api = package.AsyncClient(transport_adapter=adapter, options=options.ClientOptions(cleanup_timeout=cleanup))
        view = api.with_options(options.RequestOptions())
        outcome: list[str] = []
        call = asyncio.ensure_future(arecord(outcome, "call", lambda: view.pets.get_pet(pet_id=_pet(package))))
        await adapter.started.wait()
        await arecord(outcome, "probe", lambda: api.pets.get_pet(pet_id=_pet(package), options="invalid"))
        closer = asyncio.ensure_future(arecord(outcome, "close", api.aclose))
        await asyncio.sleep(0)
        await arecord(outcome, "refused", lambda: api.pets.get_pet(pet_id=_pet(package)))
        if cleanup < 1:
            await closer
        adapter.proceed.set()
        await call
        await closer
        lines.append(f"  {label}")
        lines.extend(sorted(outcome))
        await arecord(lines, f"{label} close again", api.aclose)


def _backends(package: ModuleType, lines: list[str]) -> None:
    """Refuse an async call outside asyncio and on a second event loop."""
    transport_module, _, _, responses = _modules(package)
    adapter = AsyncAdapter(transport_module, lines)
    api = package.AsyncClient(transport_adapter=adapter)
    coroutine = api.pets.get_pet(pet_id=_pet(package))
    record(lines, "no event loop", lambda: coroutine.send(None))
    coroutine.close()
    adapter.replies.append(_ok(lines, responses, AsyncResponse))
    asyncio.run(arecord(lines, "first loop", lambda: api.pets.get_pet(pet_id=_pet(package))))
    asyncio.run(arecord(lines, "second loop", lambda: api.pets.get_pet(pet_id=_pet(package))))

    async def inside() -> None:
        bound = package.AsyncClient(transport_adapter=AsyncAdapter(transport_module, lines))
        await bound.aclose()

    asyncio.run(inside())
    lines.append("  client created in a loop closes in it")
