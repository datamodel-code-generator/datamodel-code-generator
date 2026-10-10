"""Observe generated clients' calls through their HTTP client's own event hooks, and end calls native faults stop."""

from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.api_generation.support.client_runtime import (
    Exchange,
    Injected,
    abroken,
    aoutcome,
    arecord,
    argument,
    broken,
    describe,
    json_response,
    outcome,
    raw_response,
    record,
    run,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
    from types import ModuleType

_PET: Final = {"id": 3, "name": "fox"}
_RAW: Final = "https://raw.example.com/items?a=1"


@Injected
def _refusing(request: httpx2.Request) -> httpx2.Response:
    msg = "refused"
    raise httpx2.ConnectError(msg, request=request)


@Injected
def _interrupting(request: httpx2.Request) -> httpx2.Response:
    raise KeyboardInterrupt


@Injected
def _cancelling(request: httpx2.Request) -> httpx2.Response:
    raise asyncio.CancelledError


class _InterruptedClose(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    """A body whose close is interrupted: by a signal when read synchronously, by a cancellation with asyncio."""

    def __iter__(self) -> Iterator[bytes]:
        yield b'{"id":3,"name":"fox"}'

    def close(self) -> None:
        raise KeyboardInterrupt

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'{"id":3,"name":"fox"}'

    async def aclose(self) -> None:
        raise asyncio.CancelledError


@Injected
def _interrupted_close(request: httpx2.Request) -> httpx2.Response:
    del request
    return httpx2.Response(200, headers={"content-type": "application/json"}, stream=_InterruptedClose())


class _FailingClose(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    """A body read in full whose close then fails."""

    def __iter__(self) -> Iterator[bytes]:
        yield b'{"id":3,"name":"fox"}'

    def close(self) -> None:
        msg = "close failed"
        raise RuntimeError(msg)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'{"id":3,"name":"fox"}'

    async def aclose(self) -> None:
        self.close()


@Injected
def _failing_close(request: httpx2.Request) -> httpx2.Response:
    del request
    return httpx2.Response(200, headers={"content-type": "application/json"}, stream=_FailingClose())


class _BrokenFailingClose(_FailingClose):
    """A body whose read breaks off, and whose close then fails too."""

    def __iter__(self) -> Iterator[bytes]:
        yield b'{"id":3,'
        msg = "body broke off"
        raise httpx2.ReadError(msg)


@Injected
def _broken_failing_close(request: httpx2.Request) -> httpx2.Response:
    del request
    return httpx2.Response(200, headers={"content-type": "application/json"}, stream=_BrokenFailingClose())


class _Hooks:
    """The HTTP client's own request and response hooks: report each one HTTPX2 runs, and raise when armed to."""

    def __init__(self, lines: list[str]) -> None:
        """Keep the report, armed for no failure."""
        self.lines = lines
        self.failing: str | None = None
        self.error: type[Exception] = RuntimeError

    def request(self, request: httpx2.Request) -> None:
        """Report a request HTTPX2 is about to send: every attempt and every redirect it follows."""
        self.lines.append(f"    request hook: {request.method} {request.url.path}")
        self._raise("request")

    def response(self, response: httpx2.Response) -> None:
        """Report a response HTTPX2 received, before its body is read."""
        self.lines.append(
            f"    response hook: {response.request.method} {response.request.url.path} {response.status_code}"
        )
        self._raise("response")

    def _raise(self, event: str) -> None:
        if event == self.failing:
            msg = f"{event} hook failed"
            raise self.error(msg)

    async def arequest(self, request: httpx2.Request) -> None:
        """Report a request an asyncio client is about to send."""
        self.request(request)

    async def aresponse(self, response: httpx2.Response) -> None:
        """Report a response an asyncio client received."""
        self.response(response)


def _measured(value: Any) -> str:
    """Describe a call outcome with the attempts its result or error counts."""
    info = getattr(value, "info", None)
    attempts = getattr(value, "attempt_count", None) if info is None else info.attempt_count
    return f"{describe(value)} attempts={attempts}"


def _status(raw: Any) -> tuple[int, int]:
    """Return a raw response's status and the attempts its call made."""
    return raw.info.status_code, raw.info.attempt_count


def _observed(lines: list[str], label: str, call: Callable[[], object]) -> None:
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {_measured(error)}")
        return
    lines.append(f"  {label} = {_measured(result)}")


async def _aobserved(lines: list[str], label: str, call: Callable[[], Awaitable[object]]) -> None:
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {_measured(error)}")
        return
    lines.append(f"  {label} = {_measured(result)}")


def _settings(package: ModuleType) -> dict[str, Any]:
    """Retry at once and follow redirects, so a call's every attempt and hop reaches the hooks."""
    options = importlib.import_module(f"{package.__name__}.options")
    return {"retry": options.RetryOptions(initial_delay=0), "follow_redirects": True}


def hooks(package: ModuleType, lines: list[str]) -> None:
    """Report the native hooks of typed, retried, redirected, raw, and streamed calls, and calls they or faults end."""
    exchange = Exchange(lines)
    observer = _Hooks(lines)
    http = exchange.client(event_hooks={"request": [observer.request], "response": [observer.response]})
    pet = argument(package, "getPet", "path", "petId", 3)
    with package.Client(http_client=http, **_settings(package)) as api:
        _calls(api, exchange, lines, pet)
        _failing_hooks(api, exchange, lines, observer, pet)
        _faults(api, exchange, lines, pet)
        _streams(api, exchange, lines, pet)
        _raw(api, exchange, lines, pet)
    http.close()
    _unread_close(package, lines, pet)
    run(lambda: _async_hooks(package, lines))


def _calls(api: Any, exchange: Exchange, lines: list[str], pet: object) -> None:
    """Run the request hook for every attempt and redirect hop, and the response hook for every response."""
    exchange.respond(json_response(200, _PET))
    _observed(lines, "typed get", lambda: api.pets.with_response.get_pet(petId=pet))
    exchange.respond(_refusing, json_response(200, _PET))
    _observed(lines, "get retried after a refused connection", lambda: api.pets.with_response.get_pet(petId=pet))
    exchange.respond(raw_response(503), json_response(200, _PET))
    _observed(lines, "get retried after a 503", lambda: api.pets.with_response.get_pet(petId=pet))
    exchange.respond(raw_response(302, Location="/v1/pets/3"), json_response(200, _PET))
    _observed(lines, "get following a redirect", lambda: api.pets.with_response.get_pet(petId=pet))
    exchange.respond(json_response(200, _PET), raw_response(200, b"ok", "text/plain"), json_response(200, _PET))
    record(lines, "raw get", lambda: _status(api.pets.with_raw_response.get_pet(petId=pet)))
    record(lines, "raw request", lambda: api.request_raw("GET", _RAW).body_bytes)
    with api.pets.with_streaming_response.get_pet(petId=pet) as streamed:
        record(lines, "streamed get", streamed.read)


def _failing_hooks(api: Any, exchange: Exchange, lines: list[str], observer: _Hooks, pet: object) -> None:
    """Fail a call as HTTPX2 raises a hook's error: before sending, or once a response arrived, never retried."""
    observer.failing = "request"
    _observed(lines, "get whose request hook fails", lambda: api.pets.get_pet(petId=pet))
    _observed(lines, "raw request whose request hook fails", lambda: api.request_raw("GET", _RAW))
    observer.error = httpx2.ConnectError
    _observed(lines, "get whose request hook refuses the connection", lambda: api.pets.get_pet(petId=pet))
    observer.error = RuntimeError
    observer.failing = "response"
    exchange.respond(raw_response(503), json_response(200, _PET))
    _observed(lines, "get whose response hook fails on a 503", lambda: api.pets.get_pet(petId=pet))
    lines.append(f"    unanswered after it: {len(exchange.responders)}")
    exchange.responders.clear()
    exchange.respond(json_response(200, _PET))
    _observed(lines, "stream whose response hook fails", api.pets.with_streaming_response.get_pet(petId=pet).__enter__)
    observer.failing = None


def _faults(api: Any, exchange: Exchange, lines: list[str], pet: object) -> None:
    """End calls a codec, a refused connection, an interruption, or a failing close stops."""
    record(lines, "list of a trace its codec refuses", lambda: api.pets.list_pets(X_Trace=object()))
    exchange.respond(_refusing, _refusing, _refusing)
    _observed(lines, "get of a refused connection", lambda: api.pets.get_pet(petId=pet))
    exchange.respond(_interrupting)
    try:
        api.pets.get_pet(petId=pet)
    except KeyboardInterrupt:
        lines.append("  get interrupted: KeyboardInterrupt")
    exchange.respond(_failing_close, _failing_close)
    record(lines, "get whose response close fails", lambda: api.pets.get_pet(petId=pet))
    record(lines, "raw get whose response close fails", lambda: api.pets.with_raw_response.get_pet(petId=pet))
    exchange.respond(_broken_failing_close)
    record(lines, "get whose read breaks off and close fails", lambda: api.pets.get_pet(petId=pet))


def _unread_close(package: ModuleType, lines: list[str], pet: object) -> None:
    """Close a stream before reading it, as the HTTP client hands it over, while its body's close fails."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        del request
        return httpx2.Response(200, headers={"content-type": "application/json"}, stream=_FailingClose())

    with httpx2.Client(transport=httpx2.MockTransport(answer)) as http, package.Client(http_client=http) as api:
        manager = api.pets.with_streaming_response.get_pet(petId=pet)
        closing = manager.__enter__()
        record(lines, "stream closed unread whose close fails", closing.close)
        record(lines, "its block left after it", lambda: manager.__exit__(None, None, None))


def _streams(api: Any, exchange: Exchange, lines: list[str], pet: object) -> None:
    """End each handed-over stream once: closed early, discarded, iterated, interrupted, failed, or an error's."""
    exchange.respond(json_response(200, _PET), json_response(200, _PET), json_response(200, _PET))
    with api.pets.with_streaming_response.get_pet(petId=pet) as early:
        lines.append(f"  stream closed early: {early.info.status_code}")
    with api.pets.with_streaming_response.get_pet(petId=pet) as interrupted:
        interrupted.discard(KeyboardInterrupt())
        lines.append("  stream discarded for an interruption")
    with api.pets.with_streaming_response.get_pet(petId=pet) as iterated:
        record(lines, "stream iterated", lambda: b"".join(iterated.iter_bytes()))
    exchange.respond(_interrupted_close)
    with api.pets.with_streaming_response.get_pet(petId=pet) as interrupting:
        try:
            interrupting.close()
        except KeyboardInterrupt:
            lines.append("  stream whose close is interrupted: KeyboardInterrupt")
    exchange.respond(broken)
    with api.pets.with_streaming_response.get_pet(petId=pet) as failed:
        record(lines, "stream failing on reading it", failed.read)
    exchange.respond(raw_response(404))
    with api.pets.with_streaming_response.get_pet(petId=pet) as missing:
        lines.append(f"  stream of an error: {outcome(missing.raise_for_status)}")


def _raw(api: Any, exchange: Exchange, lines: list[str], pet: object) -> None:
    """Refuse raw calls before sending, and end one a refused connection fails."""
    record(lines, "raw get of a pet its codec refuses", lambda: api.pets.with_raw_response.get_pet(petId=object()))
    trace = object()
    record(lines, "raw list of a trace its codec refuses", lambda: api.pets.with_raw_response.list_pets(X_Trace=trace))
    record(lines, "raw request to no URL", lambda: api.request_raw("GET", "not a url"))
    exchange.respond(_refusing, _refusing, _refusing)
    record(lines, "raw get of a refused connection", lambda: api.pets.with_raw_response.get_pet(petId=pet))


async def _async_hooks(package: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    observer = _Hooks(lines)
    http = exchange.async_client(event_hooks={"request": [observer.arequest], "response": [observer.aresponse]})
    pet = argument(package, "getPet", "path", "petId", 3)
    async with package.AsyncClient(http_client=http, **_settings(package)) as api:
        await _async_calls(api, exchange, lines, pet)
        await _async_failing_hooks(api, exchange, lines, observer, pet)
        await _async_faults(api, exchange, lines, pet)
        await _async_streams(api, exchange, lines, pet)
    await http.aclose()
    await _cancelled_failures(package, lines)


async def _async_calls(api: Any, exchange: Exchange, lines: list[str], pet: object) -> None:
    exchange.respond(json_response(200, _PET))
    await _aobserved(lines, "async typed get", lambda: api.pets.with_response.get_pet(petId=pet))
    exchange.respond(_refusing, json_response(200, _PET))
    await _aobserved(
        lines, "async get retried after a refused connection", lambda: api.pets.with_response.get_pet(petId=pet)
    )
    exchange.respond(raw_response(503), json_response(200, _PET))
    await _aobserved(lines, "async get retried after a 503", lambda: api.pets.with_response.get_pet(petId=pet))
    exchange.respond(raw_response(302, Location="/v1/pets/3"), json_response(200, _PET))
    await _aobserved(lines, "async get following a redirect", lambda: api.pets.with_response.get_pet(petId=pet))
    exchange.respond(json_response(200, _PET), raw_response(200, b"ok", "text/plain"))

    async def raw_get() -> tuple[int, int]:
        return _status(await api.pets.with_raw_response.get_pet(petId=pet))

    await arecord(lines, "async raw get", raw_get)

    async def raw_call() -> bytes:
        return (await api.request_raw("GET", _RAW)).body_bytes

    await arecord(lines, "async raw request", raw_call)


async def _async_failing_hooks(api: Any, exchange: Exchange, lines: list[str], observer: _Hooks, pet: object) -> None:
    observer.failing = "request"
    await _aobserved(lines, "async get whose request hook fails", lambda: api.pets.get_pet(petId=pet))
    await _aobserved(lines, "async raw request whose request hook fails", lambda: api.request_raw("GET", _RAW))
    observer.error = httpx2.ConnectError
    await _aobserved(lines, "async get whose request hook refuses the connection", lambda: api.pets.get_pet(petId=pet))
    observer.error = RuntimeError
    observer.failing = "response"
    exchange.respond(raw_response(503), json_response(200, _PET))
    await _aobserved(lines, "async get whose response hook fails on a 503", lambda: api.pets.get_pet(petId=pet))
    lines.append(f"    unanswered after it: {len(exchange.responders)}")
    exchange.responders.clear()
    exchange.respond(json_response(200, _PET))
    manager = api.pets.with_streaming_response.get_pet(petId=pet)
    await _aobserved(lines, "async stream whose response hook fails", manager.__aenter__)
    observer.failing = None


async def _async_faults(api: Any, exchange: Exchange, lines: list[str], pet: object) -> None:
    await arecord(
        lines, "async raw get of a pet its codec refuses", lambda: api.pets.with_raw_response.get_pet(petId=object())
    )
    await arecord(
        lines,
        "async raw list of a trace its codec refuses",
        lambda: api.pets.with_raw_response.list_pets(X_Trace=object()),
    )
    await arecord(lines, "async raw request to no URL", lambda: api.request_raw("GET", "not a url"))
    exchange.respond(_refusing, _refusing, _refusing)
    await arecord(
        lines, "async raw get of a refused connection", lambda: api.pets.with_raw_response.get_pet(petId=pet)
    )
    exchange.respond(_cancelling)
    try:
        await api.pets.get_pet(petId=pet)
    except asyncio.CancelledError:
        lines.append("  async get cancelled: CancelledError")
    exchange.respond(_failing_close, _failing_close)
    await arecord(lines, "async get whose response close fails", lambda: api.pets.get_pet(petId=pet))
    await arecord(
        lines, "async raw get whose response close fails", lambda: api.pets.with_raw_response.get_pet(petId=pet)
    )


async def _async_streams(api: Any, exchange: Exchange, lines: list[str], pet: object) -> None:
    exchange.respond(json_response(200, _PET), json_response(200, _PET), abroken)
    async with api.pets.with_streaming_response.get_pet(petId=pet) as early:
        lines.append(f"  async stream closed early: {early.info.status_code}")
    async with api.pets.with_streaming_response.get_pet(petId=pet) as read:
        await arecord(lines, "async stream read", read.read)
    async with api.pets.with_streaming_response.get_pet(petId=pet) as failed:
        await arecord(lines, "async stream failing on reading it", failed.read)
    exchange.respond(raw_response(404))
    async with api.pets.with_streaming_response.get_pet(petId=pet) as missing:
        lines.append(f"  async stream of an error: {await aoutcome(missing.raise_for_status)}")
    exchange.respond(_interrupted_close)
    async with api.pets.with_streaming_response.get_pet(petId=pet) as cancelling:
        try:
            await cancelling.aclose()
        except asyncio.CancelledError:
            lines.append("  async stream whose close is cancelled: CancelledError")
    exchange.respond(json_response(200, _PET))

    async def raw_get() -> int:
        return (await api.pets.with_raw_response.get_pet(petId=pet)).info.status_code

    await arecord(lines, "async raw get after it", raw_get)


class _CancelledRead(httpx2.AsyncByteStream):
    """A body that starts, then waits until its reader is cancelled and fails with a read error as it stops."""

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
            msg = "Response failed after cancellation"
            raise httpx2.ReadError(msg) from None

    async def aclose(self) -> None:
        self.closed += 1


async def _cancelled_failures(package: ModuleType, lines: list[str]) -> None:
    """Raise the native failure a send or read raises as it is cancelled, as HTTPX2 hands it over."""
    pet = argument(package, "getPet", "path", "petId", 3)
    for mode in ("typed-send", "raw-send", "typed-read", "raw-read", "stream-read"):
        body = _CancelledRead()

        async def answer(request: httpx2.Request, body: _CancelledRead = body, mode: str = mode) -> httpx2.Response:
            if mode.endswith("send"):
                body.entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    body.stopped.set()
                    msg = "Transport failed after cancellation"
                    raise httpx2.ReadError(msg, request=request) from None
            return httpx2.Response(200, headers={"content-type": "application/json"}, stream=body)

        async with (
            httpx2.AsyncClient(transport=httpx2.MockTransport(answer)) as http,
            package.AsyncClient(http_client=http) as api,
        ):
            manager = api.pets.with_streaming_response.get_pet(petId=pet) if mode == "stream-read" else None
            handle = None if manager is None else await manager.__aenter__()
            match mode:
                case "typed-send" | "typed-read":
                    caller = asyncio.create_task(api.pets.get_pet(petId=pet))
                case "raw-send" | "raw-read":
                    caller = asyncio.create_task(api.pets.with_raw_response.get_pet(petId=pet))
                case _:
                    caller = asyncio.create_task(handle.read())
            await body.entered.wait()
            caller.cancel()
            try:
                await caller
            except (asyncio.CancelledError, Exception) as error:
                lines.append(
                    f"  {mode} failing as it is cancelled={type(error).__name__} stopped={body.stopped.is_set()}"
                )
            if manager is not None:
                await manager.__aexit__(None, None, None)
                await api.aclose()
                await arecord(lines, "reread of the closed stream", handle.read)
        lines.append(f"    response closed={body.closed}")
