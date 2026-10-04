"""Exercise malformed limiter callbacks and compound cleanup failures through generated public clients."""

from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING, NoReturn, Protocol

import httpx2

from tests.data.python.client_bodies import _photo
from tests.data.python.client_limiters import (
    _AsyncBody,
    _AsyncEvents,
    _AsyncSemaphoreLimiter,
    _Body,
    _Events,
    _Factory,
    _SemaphoreLimiter,
    _modules,
)
from tests.data.python.client_runtime import Exchange, aoutcome, arecord, argument, outcome, raw_response, record, run
from tests.data.python.client_transports import Adapter, AsyncAdapter, AsyncResponse, Response

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from types import ModuleType

_PET = b'{"id":3,"name":"fox"}'
_PNG = b"\x89PNG"


class _Returned:
    """State for a limiter that violates its return contract."""

    def __init__(self, value: object) -> None:
        self.value = value
        self.entered = 0


class _ReturnedLimiter(_Returned):
    def acquire(self, context: object) -> object:
        del context
        self.entered += 1
        return self.value


class _AsyncReturnedLimiter(_Returned):
    async def acquire(self, context: object) -> object:
        del context
        self.entered += 1
        return self.value


class _ForeignPermit:
    def __init__(self) -> None:
        self.released = 0

    def release(self) -> None:
        self.released += 1


class _ForeignAsyncPermit:
    def __init__(self) -> None:
        self.released = 0

    async def release(self) -> None:
        self.released += 1


class _BodyToClose(Protocol):
    def iter_bytes(self) -> Iterator[bytes]: ...

    def close(self) -> None: ...


class _AsyncBodyToClose(Protocol):
    def aiter_bytes(self) -> AsyncIterator[bytes]: ...

    async def aclose(self) -> None: ...


class _Request(Protocol):
    @property
    def body(self) -> _BodyToClose | None: ...


class _AsyncRequest(Protocol):
    @property
    def body(self) -> _AsyncBodyToClose | None: ...


class _AdapterState:
    """A faulty adapter that fails after cleaning up its request body."""

    def __init__(self, transports: ModuleType) -> None:
        self.capabilities = transports.TransportCapabilities(
            internal_retry_limit=0, delivery_evidence=False, http_versions=("HTTP/1.1",)
        )
        self.sent = 0
        self.content = b""


class _ClosingAdapter(_AdapterState):
    def send(self, request: _Request, context: object) -> NoReturn:
        del context
        self.sent += 1
        if (body := request.body) is not None:
            self.content = b"".join(body.iter_bytes())
            body.close()
            body.close()
        raise RuntimeError("Adapter failed after body cleanup")

    def close(self) -> None:
        pass


class _AsyncClosingAdapter(_AdapterState):
    async def send(self, request: _AsyncRequest, context: object) -> NoReturn:
        del context
        self.sent += 1
        if (body := request.body) is not None:
            self.content = b"".join([chunk async for chunk in body.aiter_bytes()])
            await body.aclose()
            await body.aclose()
        raise RuntimeError("Adapter failed after body cleanup")

    async def aclose(self) -> None:
        pass


class _FaultBody(_Body):
    def close(self) -> None:
        super().close()
        raise RuntimeError("Body close failed")


class _AsyncFaultBody(_AsyncBody):
    async def aclose(self) -> None:
        self.close()
        raise RuntimeError("Body close failed")


class _FaultFactory(_Factory):
    def open(self, context: object) -> _FaultBody:
        self._open(context)
        return _FaultBody(self)

    async def aopen(self, context: object) -> _AsyncFaultBody:
        self._open(context)
        return _AsyncFaultBody(self)


class _InterruptedStream(httpx2.AsyncByteStream):
    """A response that reports a read failure after its reader is cancelled."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.stopped = asyncio.Event()
        self.closed = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"{"
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.stopped.set()
            raise httpx2.ReadError("Response failed after cancellation") from None

    async def aclose(self) -> None:
        self.closed += 1


def limiter_faults(package: ModuleType, lines: list[str]) -> None:
    """Record callback contract failures, preserved primary failures, and owned-resource cleanup."""
    _sync_callbacks(package, lines)
    _sync_response_cleanup(package, lines)
    _sync_body_cleanup(package, lines)
    run(lambda: _async(package, lines))


def _sync_callbacks(package: ModuleType, lines: list[str]) -> None:
    options, bodies, types = _modules(package)
    exchange = Exchange(lines)
    exchange.respond(raw_response(200, _PNG, "image/png"))
    http = exchange.client()
    foreign = _ForeignAsyncPermit()
    pet = argument(package, "getPet", "path", "petId", 3)
    with package.Client(http_client=http) as api:
        for label, value in (("missing permit", None), ("async permit from sync limiter", foreign)):
            limiter = _ReturnedLimiter(value)
            record(
                lines,
                label,
                lambda: api.pets.get_pet(pet_id=pet, options=options.RequestOptions(limiter=limiter)),
            )
            lines.append(
                f"    entered={limiter.entered} wrong_mode_released={foreign.released} queued={len(exchange.responders)}"
            )
        record(lines, "raw preparation before limiter", lambda: api.pets.with_raw_response.get_pet(pet_id=object()))
        semaphore = _SemaphoreLimiter()
        factory = _Factory(semaphore.usage)
        starting, ending = _Events(semaphore.usage, "call_start"), _Events(semaphore.usage, "call_end")
        stopped = options.RequestOptions(total_timeout=0, limiter=semaphore, hooks=(starting, ending))
        lines.append(
            f"  stopped call with failing hooks: {outcome(lambda: api.pets.photos.upload(pet_id=_photo(package), body=bodies.BodyFactory(factory.open), options=stopped))}"
        )
        lines.append(
            f"    {semaphore.usage.report} factory={factory.opened}/{factory.closed} "
            f"events={starting.names}/{ending.names} counters={ending.ends} queued={len(exchange.responders)}"
        )
    http.close()


def _sync_response_cleanup(package: ModuleType, lines: list[str]) -> None:
    options, _, types = _modules(package)
    transports = importlib.import_module(f"{package.__name__}.transports")
    responses = importlib.import_module(f"{package.__name__}.responses")
    pet = argument(package, "getPet", "path", "petId", 3)
    for failure in (None, RuntimeError("Permit close failed")):
        limiter = _SemaphoreLimiter(release_failure=failure)
        adapter = Adapter(transports, lines)
        response = Response(
            lines, 200, responses.HeadersView((("content-type", "application/json"),)), (_PET,), close_error=True
        )
        adapter.replies.append(lambda request, context: response)
        with package.Client(transport_adapter=adapter, options=options.ClientOptions(limiter=limiter)) as api:
            try:
                api.pets.get_pet(pet_id=pet)
            except Exception as error:
                cause = getattr(error, "cause", None)
                record(
                    lines,
                    f"response close failure with permit failure={failure is not None}",
                    lambda: (
                        type(error).__name__,
                        type(cause).__name__,
                        tuple(type(item).__name__ for item in getattr(error, "secondary_errors", ())),
                        not hasattr(cause, "add_note")
                        or bool(getattr(cause, "__notes__", ())) == (failure is not None),
                    ),
                )
        lines.append(f"    {limiter.usage.report}")


def _sync_body_cleanup(package: ModuleType, lines: list[str]) -> None:
    options, bodies, _ = _modules(package)
    transports = importlib.import_module(f"{package.__name__}.transports")
    limiter = _SemaphoreLimiter()
    factory = _Factory(limiter.usage)
    adapter = _ClosingAdapter(transports)
    with package.Client(transport_adapter=adapter, options=options.ClientOptions(limiter=limiter)) as api:
        record(
            lines,
            "adapter closes body before failure",
            lambda: api.pets.photos.upload(pet_id=_photo(package), body=bodies.BodyFactory(factory.open)),
        )
    lines.append(
        f"    sent={adapter.sent} content={adapter.content!r} factory={factory.opened}/{factory.closed} {limiter.usage.report}"
    )
    exchange = Exchange(lines)
    exchange.respond(raw_response(200, _PNG, "image/png"))
    http = exchange.client()
    limiter = _SemaphoreLimiter(release_failure=RuntimeError("Permit close failed"))
    factory = _FaultFactory(limiter.usage)
    with package.Client(http_client=http, options=options.ClientOptions(limiter=limiter)) as api:
        lines.append(
            f"  body and permit close failure: {outcome(lambda: api.pets.photos.upload(pet_id=_photo(package), body=bodies.BodyFactory(factory.open)))}"
        )
    lines.append(f"    factory={factory.opened}/{factory.closed} {limiter.usage.report}")
    http.close()


async def _async(package: ModuleType, lines: list[str]) -> None:
    await _async_callbacks(package, lines)
    await _async_response_cleanup(package, lines)
    await _async_body_cleanup(package, lines)
    await _native_priority(package, lines)


async def _async_callbacks(package: ModuleType, lines: list[str]) -> None:
    options, bodies, types = _modules(package)
    exchange = Exchange(lines)
    exchange.respond(raw_response(200, _PNG, "image/png"))
    http = exchange.async_client()
    foreign = _ForeignPermit()
    pet = argument(package, "getPet", "path", "petId", 3)
    async with package.AsyncClient(http_client=http) as api:
        for label, value in (("async missing permit", None), ("sync permit from async limiter", foreign)):
            limiter = _AsyncReturnedLimiter(value)
            await arecord(
                lines,
                label,
                lambda: api.pets.get_pet(pet_id=pet, options=options.RequestOptions(limiter=limiter)),
            )
            lines.append(
                f"    entered={limiter.entered} wrong_mode_released={foreign.released} queued={len(exchange.responders)}"
            )
        await arecord(
            lines, "async raw preparation before limiter", lambda: api.pets.with_raw_response.get_pet(pet_id=object())
        )
        semaphore = _AsyncSemaphoreLimiter()
        factory = _Factory(semaphore.usage)
        starting, ending = _Events(semaphore.usage, "call_start"), _Events(semaphore.usage, "call_end")
        stopped = options.RequestOptions(
            total_timeout=0, limiter=semaphore, hooks=(_AsyncEvents(starting), _AsyncEvents(ending))
        )
        lines.append(
            f"  async stopped call with failing hooks: {await aoutcome(lambda: api.pets.photos.upload(pet_id=_photo(package), body=bodies.AsyncBodyFactory(factory.aopen), options=stopped))}"
        )
        lines.append(
            f"    {semaphore.usage.report} factory={factory.opened}/{factory.closed} "
            f"events={starting.names}/{ending.names} counters={ending.ends} queued={len(exchange.responders)}"
        )
    await http.aclose()


async def _async_response_cleanup(package: ModuleType, lines: list[str]) -> None:
    options, _, types = _modules(package)
    transports = importlib.import_module(f"{package.__name__}.transports")
    responses = importlib.import_module(f"{package.__name__}.responses")
    pet = argument(package, "getPet", "path", "petId", 3)
    for failure in (None, RuntimeError("Permit close failed")):
        limiter = _AsyncSemaphoreLimiter(release_failure=failure)
        adapter = AsyncAdapter(transports, lines)
        response = AsyncResponse(
            lines, 200, responses.HeadersView((("content-type", "application/json"),)), (_PET,), close_error=True
        )
        adapter.replies.append(lambda request, context: response)
        async with package.AsyncClient(
            transport_adapter=adapter, options=options.ClientOptions(limiter=limiter)
        ) as api:
            try:
                await api.pets.get_pet(pet_id=pet)
            except Exception as error:
                cause = getattr(error, "cause", None)
                record(
                    lines,
                    f"async response close failure with permit failure={failure is not None}",
                    lambda: (
                        type(error).__name__,
                        type(cause).__name__,
                        tuple(type(item).__name__ for item in getattr(error, "secondary_errors", ())),
                        not hasattr(cause, "add_note")
                        or bool(getattr(cause, "__notes__", ())) == (failure is not None),
                    ),
                )
        lines.append(f"    {limiter.usage.report}")


async def _async_body_cleanup(package: ModuleType, lines: list[str]) -> None:
    options, bodies, _ = _modules(package)
    transports = importlib.import_module(f"{package.__name__}.transports")
    limiter = _AsyncSemaphoreLimiter()
    factory = _Factory(limiter.usage)
    adapter = _AsyncClosingAdapter(transports)
    async with package.AsyncClient(transport_adapter=adapter, options=options.ClientOptions(limiter=limiter)) as api:
        await arecord(
            lines,
            "async adapter closes body before failure",
            lambda: api.pets.photos.upload(pet_id=_photo(package), body=bodies.AsyncBodyFactory(factory.aopen)),
        )
    lines.append(
        f"    sent={adapter.sent} content={adapter.content!r} factory={factory.opened}/{factory.closed} {limiter.usage.report}"
    )
    exchange = Exchange(lines)
    exchange.respond(raw_response(200, _PNG, "image/png"))
    http = exchange.async_client()
    limiter = _AsyncSemaphoreLimiter(release_failure=RuntimeError("Permit close failed"))
    factory = _FaultFactory(limiter.usage)
    async with package.AsyncClient(http_client=http, options=options.ClientOptions(limiter=limiter)) as api:
        lines.append(
            f"  async body and permit close failure: {await aoutcome(lambda: api.pets.photos.upload(pet_id=_photo(package), body=bodies.AsyncBodyFactory(factory.aopen)))}"
        )
    lines.append(f"    factory={factory.opened}/{factory.closed} {limiter.usage.report}")
    await http.aclose()


async def _native_priority(package: ModuleType, lines: list[str]) -> None:
    options, _, types = _modules(package)
    for mode in ("typed-send", "raw-send", "typed-read", "raw-read", "stream-read"):
        body = _InterruptedStream()
        limiter = _AsyncSemaphoreLimiter()
        events = _Events(limiter.usage)

        async def failing(request: httpx2.Request) -> httpx2.Response:
            if mode.endswith("send"):
                body.entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    body.stopped.set()
                    raise httpx2.ReadError("Adapter failed after cancellation", request=request) from None
            return httpx2.Response(200, headers={"content-type": "application/json"}, stream=body)

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(failing)) as http:
            async with package.AsyncClient(
                http_client=http, options=options.ClientOptions(limiter=limiter, hooks=(_AsyncEvents(events),))
            ) as api:
                pet = argument(package, "getPet", "path", "petId", 3)
                match mode:
                    case "typed-send" | "typed-read":
                        caller = asyncio.create_task(api.pets.get_pet(pet_id=pet))
                    case "raw-send" | "raw-read":
                        caller = asyncio.create_task(api.pets.with_raw_response.get_pet(pet_id=pet))
                    case _:
                        manager = api.pets.with_streaming_response.get_pet(pet_id=pet)
                        handle = await manager.__aenter__()
                        caller = asyncio.create_task(handle.read())
                await body.entered.wait()
                caller.cancel()
                try:
                    await caller
                except BaseException as error:
                    lines.append(
                        f"  {mode} native cancellation before adapter failure={type(error).__name__} stopped={body.stopped.is_set()}"
                    )
                if mode == "stream-read":
                    await manager.__aexit__(None, None, None)
                    await api.aclose()
                    try:
                        await handle.read()
                    except BaseException as error:
                        lines.append(f"    closed interrupted stream reread={type(error).__name__}")
            lines.append(
                f"    {limiter.usage.report} response_closed={body.closed} events={' '.join(events.names)} counters={events.ends}"
            )
