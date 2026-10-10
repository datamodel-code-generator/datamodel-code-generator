"""Report generated-client deadline, task cancellation, closing, and late-cleanup races over native transports."""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

import httpx2

from tests.data.python.client_runtime import arecord, argument, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
    from types import ModuleType


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _snapshot(error: BaseException) -> tuple[object, ...]:
    return (
        type(error).__name__,
        getattr(error, "attempt_count", None),
        getattr(error, "reason", None),
        type(getattr(error, "cause", None)).__name__,
        tuple(getattr(error, "__notes__", ())),
    )


def _captured(
    call: Callable[[], object], snapshot: Callable[[BaseException], tuple[object, ...]] = _snapshot
) -> tuple[object, ...]:
    """Record the SDK error, or the KeyboardInterrupt or SystemExit a race delivers, that ends a call."""
    try:
        call()
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        return snapshot(error)
    return ("returned",)


async def _acaptured(
    call: Callable[[], Awaitable[object]],
    errors: list[BaseException] | None = None,
    snapshot: Callable[[BaseException], tuple[object, ...]] = _snapshot,
) -> tuple[object, ...]:
    """Record the SDK error, or the task cancellation, that ends an awaited call."""
    try:
        await call()
    except (Exception, asyncio.CancelledError) as error:
        if errors is not None:
            errors.append(error)
        return snapshot(error)
    return ("returned",)


class _Body(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    """A response body that counts its closes."""

    def __init__(self, content: bytes = b"response", headers: dict[str, str] | None = None) -> None:
        self.closed = 0
        self.content = content
        self.headers = {"content-type": "application/octet-stream"} if headers is None else headers

    def __iter__(self) -> Iterator[bytes]:
        yield self.content

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.content

    def close(self) -> None:
        self.closed += 1

    async def aclose(self) -> None:
        self.close()


class _FailedClose(_Body):
    def __init__(self, failure: BaseException) -> None:
        super().__init__()
        self.failure = failure

    def close(self) -> None:
        super().close()
        raise self.failure


class _CompletedBody(_Body):
    """Advance the public clock at native EOF, after every response byte arrived."""

    def __init__(self, clock: _Clock, vector: dict[str, object]) -> None:
        super().__init__(json.dumps(vector["response"]).encode(), vector["headers"])
        self.clock = clock
        self.completed = vector["completed"]

    def __iter__(self) -> Iterator[bytes]:
        yield from super().__iter__()
        self.clock.value = self.completed

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in super().__aiter__():
            yield chunk
        self.clock.value = self.completed


class _ExpiringClose(_Body):
    """A body whose close moves the call's clock past its deadline."""

    def __init__(self, clock: _Clock) -> None:
        super().__init__()
        self.clock = clock

    async def aclose(self) -> None:
        self.close()
        self.clock.value = 200.0


class _GatedClose(_Body):
    """A body whose close waits until released, then notes its completion and optionally fails."""

    def __init__(self, *, failure: bool) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.proceed = asyncio.Event()
        self.failure = failure
        self.completed = False

    async def aclose(self) -> None:
        self.closed += 1
        self.entered.set()
        await self.proceed.wait()
        self.completed = True
        if self.failure:
            msg = "late response close failed"
            raise RuntimeError(msg)


def _answer(body: _Body) -> httpx2.Response:
    return httpx2.Response(200, headers=body.headers, stream=body)


class _Fault(httpx2.BaseTransport):
    """Run an action at each send, then answer with the body."""

    def __init__(self, action: Callable[[], None], body: _Body | None = None) -> None:
        self.action = action
        self.body = _Body() if body is None else body
        self.sent = 0

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        del request
        self.sent += 1
        self.action()
        return _answer(self.body)


class _AsyncFault(httpx2.AsyncBaseTransport):
    """Await an action at each send, then answer with the body."""

    def __init__(self, action: Callable[[], Awaitable[None]], body: _Body | None = None) -> None:
        self.action = action
        self.body = _Body() if body is None else body
        self.sent = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        del request
        self.sent += 1
        await self.action()
        return _answer(self.body)


async def _unexpected_send() -> None:
    msg = "a stopped call reached the transport"
    raise RuntimeError(msg)


_NATIVE_TIMEOUTS = (
    ("connect", httpx2.ConnectTimeout),
    ("read", httpx2.ReadTimeout),
    ("write", httpx2.WriteTimeout),
    ("pool", httpx2.PoolTimeout),
)


def _phase_sources(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    clock = options.Clock(monotonic=_Clock())
    for phase, failure in _NATIVE_TIMEOUTS:

        def failed(request: httpx2.Request) -> httpx2.Response:
            raise failure("injected timeout", request=request)

        for label, configured, total in (
            ("tie", 1.0, 1.0),
            ("phase", 0.5, 1.0),
            ("none", None, 1.0),
            ("unlimited", None, None),
        ):
            timeout = httpx2.Timeout(5.0, **{phase: configured})
            with httpx2.Client(transport=httpx2.MockTransport(failed)) as native:
                with package.Client(
                    http_client=native, timeout=timeout, max_retries=0, total_timeout=total, clock=clock
                ) as api:
                    record(
                        lines,
                        f"phase source {phase} {label}",
                        lambda: _captured(lambda: api.request_raw("GET", "https://race.example/timeout")),
                    )

    def unknown(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.TimeoutException("unclassified timeout", request=request)

    with httpx2.Client(transport=httpx2.MockTransport(unknown)) as native:
        with package.Client(http_client=native, clock=clock) as api:
            record(
                lines,
                "native timeout unknown phase",
                lambda: _captured(lambda: api.request_raw("GET", "https://race.example/timeout")),
            )


def _expired_snapshot(error: BaseException) -> tuple[object, ...]:
    return (*_snapshot(error)[:4], getattr(error, "reason", None))


def _expiring(
    options: ModuleType, failure: type[httpx2.TimeoutException]
) -> tuple[httpx2.MockTransport, dict[str, object]]:
    clock = _Clock()

    def capped(request: httpx2.Request) -> httpx2.Response:
        clock.value += 1.0
        msg = "capped timeout"
        raise failure(msg, request=request)

    settings = {
        "max_retries": 2,
        "retry": options.RetryOptions(initial_delay=0),
        "total_timeout": 1.0,
        "clock": options.Clock(monotonic=clock),
    }
    return httpx2.MockTransport(capped), settings


def _expired_sources(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for phase, failure in _NATIVE_TIMEOUTS:
        transport, settings = _expiring(options, failure)
        with (
            httpx2.Client(transport=transport) as native,
            package.Client(http_client=native, **settings) as api,
        ):
            record(
                lines,
                f"capped native timeout {phase}",
                lambda: _captured(lambda: api.request_raw("GET", "https://race.example/capped"), _expired_snapshot),
            )


async def _aexpired_sources(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for phase, failure in _NATIVE_TIMEOUTS:
        transport, settings = _expiring(options, failure)
        async with (
            httpx2.AsyncClient(transport=transport) as native,
            package.AsyncClient(http_client=native, **settings) as api,
        ):
            await arecord(
                lines,
                f"async capped native timeout {phase}",
                lambda: _acaptured(
                    lambda: api.request_raw("GET", "https://race.example/capped"), snapshot=_expired_snapshot
                ),
            )


def _admission_race(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    ticks = 0

    def at() -> float:
        nonlocal ticks
        ticks += 1
        if ticks == 2:
            api.close()
        return 100.0

    transport = _Fault(lambda: None)
    with httpx2.Client(transport=transport) as native:
        api = package.Client(http_client=native, clock=options.Clock(monotonic=at))
        record(
            lines,
            "closing during admission",
            lambda: _captured(lambda: api.request_raw("GET", "https://race.example/admit")),
        )
        record(lines, "closing during admission sends", lambda: transport.sent)
        api.close()


def _sync_races(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Expire the deadline during a send, which may also be interrupted or end in a failing response close."""
    for label, interrupt, failure in (
        ("deadline", None, None),
        ("deadline KeyboardInterrupt", KeyboardInterrupt, None),
        ("deadline SystemExit", SystemExit, None),
        ("deadline close failure", None, RuntimeError("response close failed")),
    ):
        clock = _Clock()

        def action(interrupt: type[BaseException] | None = interrupt, clock: _Clock = clock) -> None:
            clock.value = 102.0
            if interrupt is not None:
                raise interrupt

        transport = _Fault(action, None if failure is None else _FailedClose(failure))
        with (
            httpx2.Client(transport=transport) as native,
            package.Client(http_client=native, total_timeout=1.0, clock=options.Clock(monotonic=clock)) as api,
        ):
            record(
                lines,
                f"sync {label}",
                lambda api=api: _captured(lambda: api.request_raw("GET", "https://race.example/precedence")),
            )
        record(lines, f"sync {label} resources", lambda transport=transport: (transport.sent, transport.body.closed))


async def _async_races(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Close the client, expire the deadline, or cancel the caller's task while its request is being sent."""
    for label, closing, expired, cancelled in (
        ("closing/result", True, False, False),
        ("deadline/result", False, True, False),
        ("closing/deadline/result", True, True, False),
        ("native/closing/deadline/result", True, True, True),
    ):
        clock = _Clock()
        closers: list[asyncio.Task[None]] = []

        async def action(
            closing: bool = closing, expired: bool = expired, cancelled: bool = cancelled, clock: _Clock = clock
        ) -> None:
            if closing:
                closers.append(asyncio.create_task(api.aclose()))
            if expired:
                clock.value = 102.0
            if cancelled:
                caller.cancel()
                await asyncio.sleep(0)

        transport = _AsyncFault(action)
        async with httpx2.AsyncClient(transport=transport) as native:
            api = package.AsyncClient(http_client=native, total_timeout=1.0, clock=options.Clock(monotonic=clock))
            caller = asyncio.create_task(_acaptured(lambda: api.request_raw("GET", "https://race.example/race")))
            await arecord(lines, f"async {label}", lambda: caller)
            for closer in closers:
                await closer
            await api.aclose()
        record(lines, f"async {label} resources", lambda: (transport.sent, transport.body.closed))


async def _expired_read(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    clock = _Clock()

    async def sent() -> None:
        pass

    transport = _AsyncFault(sent, _ExpiringClose(clock))
    async with (
        httpx2.AsyncClient(transport=transport) as native,
        package.AsyncClient(http_client=native, total_timeout=60.0, clock=options.Clock(monotonic=clock)) as api,
    ):
        await arecord(
            lines,
            "deadline after buffered read",
            lambda: _acaptured(lambda: api.request_raw("GET", "https://race.example/expired")),
        )
    record(lines, "deadline after buffered read resources", lambda: (transport.sent, transport.body.closed))


async def _nested_wait(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Cancel the caller's task while a native request hook waits before the send; nothing is sent."""
    entered = asyncio.Event()

    async def waiting(request: httpx2.Request) -> None:
        del request
        entered.set()
        await asyncio.Event().wait()

    transport = _AsyncFault(_unexpected_send)
    async with httpx2.AsyncClient(transport=transport, event_hooks={"request": [waiting]}) as native:
        api = package.AsyncClient(http_client=native, clock=options.Clock(monotonic=_Clock()))
        caller = asyncio.create_task(_acaptured(lambda: api.request_raw("GET", "https://race.example/nested")))
        await entered.wait()
        caller.cancel()
        await arecord(lines, "nested request hook native", lambda: caller)
        await api.aclose()
    record(lines, "nested request hook native sends", lambda: transport.sent)


async def _released_responses(package: ModuleType, lines: list[str]) -> None:
    """Cancel the caller's task while its response closes: the cancellation reaches the close and propagates."""
    for cancelled, failure in ((False, False), (False, True), (True, False), (True, True)):

        async def sent() -> None:
            pass

        body = _GatedClose(failure=failure)
        transport = _AsyncFault(sent, body)
        errors: list[BaseException] = []
        label = f"released response cancelled={cancelled} failure={failure}"
        async with httpx2.AsyncClient(transport=transport) as native:
            api = package.AsyncClient(http_client=native, total_timeout=None)
            caller = asyncio.create_task(
                _acaptured(lambda api=api: api.request_raw("GET", "https://race.example/cleanup"), errors)
            )
            await body.entered.wait()
            if cancelled:
                caller.cancel()
                await asyncio.sleep(0)
            body.proceed.set()
            await arecord(lines, label, lambda caller=caller: caller)
            await api.aclose()
        record(
            lines,
            f"{label} resources",
            lambda transport=transport, body=body, errors=errors: (
                transport.sent,
                body.closed,
                body.completed,
                tuple(getattr(next(iter(errors), None), "__notes__", ())),
                tuple(getattr(next(iter(errors), None), "__notes__", ())),
            ),
        )


async def _async(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    await _aexpired_sources(package, options, lines)
    await _acompleted_response(package, options, lines)
    await _async_races(package, options, lines)
    await _expired_read(package, options, lines)
    await _nested_wait(package, options, lines)
    await _released_responses(package, lines)


def deadline_races(package: ModuleType, lines: list[str]) -> None:
    """Inject only failures and deterministic races, exercising public generated client calls throughout."""
    options = importlib.import_module(f"{package.__name__}.options")
    _phase_sources(package, options, lines)
    _expired_sources(package, options, lines)
    _admission_race(package, options, lines)
    _sync_races(package, options, lines)
    _completed_response(package, options, lines)
    run(lambda: _async(package, options, lines))


def _completed_input(options: ModuleType) -> tuple[_CompletedBody, dict[str, object]]:
    vector = json.loads((Path(__file__).parents[1] / "generation_platform/client/deadline-completed.json").read_text())
    clock = _Clock()
    clock.value = vector["started"]
    settings = {"total_timeout": vector["total_timeout"], "clock": options.Clock(monotonic=clock)}
    return _CompletedBody(clock, vector), settings


def _completed_response(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    body, settings = _completed_input(options)
    transport = _Fault(lambda: None, body)
    with httpx2.Client(transport=transport) as native:
        with package.Client(
            http_client=native,
            **settings,
        ) as api:
            record(
                lines,
                "completed typed response after expiry",
                lambda: (
                    api.pets.with_response
                    .list_pets(X_Trace=argument(package, "listPets", "header", "X-Trace", "t"))
                    .data.root[0]
                    .name
                ),
            )
        record(lines, "completed response resources", lambda: (transport.sent, body.closed, native.is_closed))


async def _acompleted_response(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    body, settings = _completed_input(options)

    async def unchanged() -> None:
        await asyncio.sleep(0)

    transport = _AsyncFault(unchanged, body)
    async with httpx2.AsyncClient(transport=transport) as native:
        async with package.AsyncClient(
            http_client=native,
            **settings,
        ) as api:

            async def completed() -> object:
                response = await api.pets.with_response.list_pets(
                    X_Trace=argument(package, "listPets", "header", "X-Trace", "t")
                )
                return response.data.root[0].name

            await arecord(lines, "async completed typed response after expiry", completed)
        record(lines, "async completed response resources", lambda: (transport.sent, body.closed, native.is_closed))
