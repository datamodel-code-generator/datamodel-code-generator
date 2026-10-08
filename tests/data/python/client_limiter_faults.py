"""Exercise malformed limiter callbacks and compound cleanup failures through generated public clients."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx2

from tests.data.python.client_bodies import _photo
from tests.data.python.client_limiters import (
    _AsyncEvents,
    _AsyncSemaphoreLimiter,
    _Events,
    _Payload,
    _SemaphoreLimiter,
    _modules,
)
from tests.data.python.client_runtime import (
    Exchange,
    aoutcome,
    arecord,
    argument,
    injected,
    outcome,
    raw_response,
    record,
    run,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
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


class _ClosingStream(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    """A response body whose native close fails after it counts the close."""

    def __init__(self) -> None:
        self.closes = 0

    def __iter__(self) -> Iterator[bytes]:
        yield _PET

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield _PET

    def close(self) -> None:
        self.closes += 1
        msg = "close failed"
        raise RuntimeError(msg)

    async def aclose(self) -> None:
        self.close()


def _closing(stream: _ClosingStream) -> object:
    return injected(lambda _: httpx2.Response(200, headers={"content-type": "application/json"}, stream=stream))


def _sent_then_failed(request: httpx2.Request) -> httpx2.Response:
    """Fail in the transport after the whole request body was read from the SDK."""
    del request
    msg = "Transport failed after reading the body"
    raise RuntimeError(msg)


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
        payload = _Payload(semaphore.usage)
        starting, ending = _Events(semaphore.usage, "call_start"), _Events(semaphore.usage, "call_end")
        stopped = options.RequestOptions(total_timeout=0, limiter=semaphore, hooks=(starting, ending))
        lines.append(
            f"  stopped call with failing hooks: {outcome(lambda: api.pets.photos.upload(pet_id=_photo(package), body=payload, options=stopped))}"
        )
        lines.append(
            f"    {semaphore.usage.report} payload={payload.opened} "
            f"events={starting.names}/{ending.names} counters={ending.ends} queued={len(exchange.responders)}"
        )
    http.close()


def _sync_response_cleanup(package: ModuleType, lines: list[str]) -> None:
    options, _, _ = _modules(package)
    pet = argument(package, "getPet", "path", "petId", 3)
    for failure in (None, RuntimeError("Permit close failed")):
        limiter = _SemaphoreLimiter(release_failure=failure)
        exchange, stream = Exchange(lines), _ClosingStream()
        exchange.respond(_closing(stream))
        with (
            exchange.client() as native,
            package.Client(http_client=native, options=options.ClientOptions(limiter=limiter)) as api,
        ):
            record(
                lines,
                f"response close failure with permit failure={failure is not None}",
                lambda api=api: _close_failure(_raised(lambda: api.pets.get_pet(pet_id=pet)), failure),
            )
        lines.append(f"    closes={stream.closes} {limiter.usage.report}")


def _raised(call: Callable[[], object]) -> Exception | None:
    try:
        call()
    except Exception as error:  # noqa: BLE001
        return error
    return None


async def _araised(call: Callable[[], Awaitable[object]]) -> Exception | None:
    try:
        await call()
    except Exception as error:  # noqa: BLE001
        return error
    return None


def _close_failure(error: Exception | None, failure: BaseException | None) -> tuple[object, ...]:
    """Describe a failed response close, the classes of its cause and secondaries, and where the permit failure went."""
    if error is None:
        return ("returned",)
    cause = getattr(error, "cause", None)
    return (
        type(error).__name__,
        type(cause).__name__,
        tuple(type(item).__name__ for item in getattr(error, "secondary_errors", ())),
        not hasattr(cause, "add_note") or bool(getattr(cause, "__notes__", ())) == (failure is not None),
    )


async def _async(package: ModuleType, lines: list[str]) -> None:
    await _async_callbacks(package, lines)
    await _async_response_cleanup(package, lines)
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
        payload = _Payload(semaphore.usage)
        starting, ending = _Events(semaphore.usage, "call_start"), _Events(semaphore.usage, "call_end")
        stopped = options.RequestOptions(
            total_timeout=0, limiter=semaphore, hooks=(_AsyncEvents(starting), _AsyncEvents(ending))
        )
        lines.append(
            f"  async stopped call with failing hooks: {await aoutcome(lambda: api.pets.photos.upload(pet_id=_photo(package), body=payload, options=stopped))}"
        )
        lines.append(
            f"    {semaphore.usage.report} payload={payload.opened} "
            f"events={starting.names}/{ending.names} counters={ending.ends} queued={len(exchange.responders)}"
        )
    await http.aclose()


async def _async_response_cleanup(package: ModuleType, lines: list[str]) -> None:
    options, _, _ = _modules(package)
    pet = argument(package, "getPet", "path", "petId", 3)
    for failure in (None, RuntimeError("Permit close failed")):
        limiter = _AsyncSemaphoreLimiter(release_failure=failure)
        exchange, stream = Exchange(lines), _ClosingStream()
        exchange.respond(_closing(stream))
        async with (
            exchange.async_client() as native,
            package.AsyncClient(http_client=native, options=options.ClientOptions(limiter=limiter)) as api,
        ):
            observed = _close_failure(await _araised(lambda api=api: api.pets.get_pet(pet_id=pet)), failure)
        record(lines, f"async response close failure with permit failure={failure is not None}", lambda: observed)
        lines.append(f"    closes={stream.closes} {limiter.usage.report}")


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
                    raise httpx2.ReadError("Transport failed after cancellation", request=request) from None
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
                        f"  {mode} transport failure after cancellation={type(error).__name__} stopped={body.stopped.is_set()}"
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
