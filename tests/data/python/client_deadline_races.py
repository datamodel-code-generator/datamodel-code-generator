"""Report generated-client deadline, cancellation, closing, and late-cleanup fault races."""

from __future__ import annotations

import asyncio
import importlib
from contextlib import ExitStack
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx2

from tests.data.python.client_runtime import arecord, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterator
    from types import ModuleType


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _clock(package: ModuleType, clock: Callable[[], float]) -> ExitStack:
    stack = ExitStack()
    for name in ("logical", "timing"):
        module = importlib.import_module(f"{package.__name__}._runtime.client.{name}")
        stack.enter_context(patch.object(module, "monotonic", clock))
    return stack


def _snapshot(error: BaseException) -> tuple[object, ...]:
    return (
        type(error).__name__,
        getattr(error, "resource_attempt_count", None),
        getattr(error, "network_send_count", None),
        getattr(error, "network_send_budget_used", None),
        getattr(error, "phase", None),
        getattr(error, "source", None),
        type(getattr(error, "cause", None)).__name__,
        tuple(type(failure).__name__ for failure in getattr(error, "secondary_errors", ())),
        getattr(error, "pending_calls", None),
        getattr(error, "pending_leases", None),
    )


def _captured(call: Callable[[], object]) -> tuple[object, ...]:
    try:
        call()
    except BaseException as error:
        return _snapshot(error)
    return ("returned",)


async def _acaptured(
    call: Callable[[], Awaitable[object]], errors: list[BaseException] | None = None
) -> tuple[object, ...]:
    try:
        await call()
    except BaseException as error:
        if errors is not None:
            errors.append(error)
        return _snapshot(error)
    return ("returned",)


class _ResponseState:
    status_code = 200

    def __init__(self, responses: ModuleType) -> None:
        self.headers = responses.HeadersView((("content-type", "application/octet-stream"),))
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class _Response(_ResponseState):
    def iter_raw_bytes(self) -> Iterator[bytes]:
        yield b"response"


class _FailedCloseResponse(_Response):
    def __init__(self, responses: ModuleType, failure: BaseException) -> None:
        super().__init__(responses)
        self.failure = failure

    def close(self) -> None:
        super().close()
        raise self.failure


class _AsyncResponse(_ResponseState):
    async def iter_raw_bytes(self) -> AsyncIterator[bytes]:
        yield b"response"

    async def aclose(self) -> None:
        self.close()


class _UnwindingStream(_AsyncResponse):
    def __init__(self, responses: ModuleType) -> None:
        super().__init__(responses)
        self.unwinding = asyncio.Event()
        self.release = asyncio.Event()
        self.proceed = asyncio.Event()

    async def iter_raw_bytes(self) -> AsyncIterator[bytes]:
        yield b"first"
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.unwinding.set()
            await self.release.wait()
            raise

    async def aclose(self) -> None:
        self.close()
        await self.proceed.wait()


class _ExpiringResponse(_AsyncResponse):
    def __init__(self, responses: ModuleType, clock: _Clock) -> None:
        super().__init__(responses)
        self.clock = clock

    async def aclose(self) -> None:
        self.close()
        self.clock.value = 102.0


class _Fault:
    def __init__(self, transports: ModuleType, responses: ModuleType, action: Callable[[], None]) -> None:
        self.capabilities = transports.TransportCapabilities(
            internal_retry_limit=0, delivery_evidence=False, http_versions=("HTTP/1.1",)
        )
        self.response = _Response(responses)
        self.action = action
        self.sent = 0

    def send(self, request: object, context: object) -> _Response:
        self.sent += 1
        self.action()
        return self.response

    def close(self) -> None:
        pass


class _AsyncFault:
    def __init__(self, transports: ModuleType, responses: ModuleType, action: Callable[[], Awaitable[None]]) -> None:
        self.capabilities = transports.TransportCapabilities(
            internal_retry_limit=0, delivery_evidence=False, http_versions=("HTTP/1.1",)
        )
        self.response = _AsyncResponse(responses)
        self.action = action
        self.sent = 0

    async def send(self, request: object, context: object) -> _AsyncResponse:
        self.sent += 1
        await self.action()
        return self.response

    async def aclose(self) -> None:
        pass


def _phase_sources(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    clock = _Clock()
    with _clock(package, clock):
        for phase, failure in (
            ("connect", httpx2.ConnectTimeout),
            ("read", httpx2.ReadTimeout),
            ("write", httpx2.WriteTimeout),
            ("pool", httpx2.PoolTimeout),
        ):

            def failed(request: httpx2.Request) -> httpx2.Response:
                raise failure("injected timeout", request=request)

            for label, configured, total in (
                ("tie", 1.0, 1.0),
                ("phase", 0.5, 1.0),
                ("none", None, 1.0),
                ("unlimited", None, None),
                ("absolute", 1.0, 2.0),
            ):
                timeout = options.TimeoutOptions(**{phase: configured})
                with httpx2.Client(transport=httpx2.MockTransport(failed)) as native:
                    with package.Client(
                        http_client=native,
                        options=options.ClientOptions(
                            timeout=timeout,
                            retry=options.RetryOptions(max_retries=0),
                            total_timeout=total,
                            deadline=options.Deadline.after(0.5) if label == "absolute" else None,
                        ),
                    ) as api:
                        record(
                            lines,
                            f"phase source {phase} {label}",
                            lambda: _captured(lambda: api.request_raw("GET", "https://race.example/timeout")),
                        )

        def unknown(request: httpx2.Request) -> httpx2.Response:
            raise httpx2.TimeoutException("unclassified timeout", request=request)

        with httpx2.Client(transport=httpx2.MockTransport(unknown)) as native:
            with package.Client(http_client=native) as api:
                record(
                    lines,
                    "native timeout unknown phase",
                    lambda: _captured(lambda: api.request_raw("GET", "https://race.example/timeout")),
                )


def _admission_race(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    ticks = 0

    def at() -> float:
        nonlocal ticks
        ticks += 1
        if ticks == 2:
            api.close()
        return 100.0

    adapter = _Fault(transports, responses, lambda: None)
    api = package.Client(transport_adapter=adapter)
    with _clock(package, at):
        record(
            lines,
            "closing during admission",
            lambda: _captured(lambda: api.request_raw("GET", "https://race.example/admit")),
        )
    record(lines, "closing during admission sends", lambda: adapter.sent)
    api.close()


def _sync_races(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    for interrupt in (None, KeyboardInterrupt, SystemExit):
        clock, token = _Clock(), options.CancelToken()

        def action() -> None:
            token.cancel()
            clock.value = 102.0
            if interrupt is not None:
                raise interrupt()

        adapter = _Fault(transports, responses, action)
        with _clock(package, clock):
            with package.Client(
                transport_adapter=adapter,
                options=options.ClientOptions(total_timeout=1.0, cancel_token=token),
            ) as api:
                record(
                    lines,
                    f"sync native/token/deadline {None if interrupt is None else interrupt.__name__}",
                    lambda: _captured(lambda: api.request_raw("GET", "https://race.example/precedence")),
                )
        record(lines, "sync race resources", lambda: (adapter.sent, adapter.response.closed))


def _compound_cleanup(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    errors = importlib.import_module(f"{package.__name__}.errors")
    token = options.CancelToken()
    adapter = _Fault(transports, responses, token.cancel)
    adapter.response = _FailedCloseResponse(
        responses,
        errors.CleanupError(cause=RuntimeError(), secondary_errors=(ValueError(), KeyboardInterrupt())),
    )
    with package.Client(
        transport_adapter=adapter,
        options=options.ClientOptions(cancel_token=token),
    ) as api:
        record(
            lines,
            "compound sync cleanup",
            lambda: _captured(lambda: api.request_raw("GET", "https://race.example/compound")),
        )
    record(lines, "compound sync cleanup resources", lambda: (adapter.sent, adapter.response.closed))


async def _async_races(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    for label, cancelled, closing, expired, native in (
        ("token/result", True, False, False, False),
        ("closing/result", False, True, False, False),
        ("deadline/result", False, False, True, False),
        ("token/closing/deadline/result", True, True, True, False),
        ("native/token/closing/deadline/result", True, True, True, True),
    ):
        clock, token = _Clock(), options.CancelToken()
        closers: list[asyncio.Task[None]] = []

        async def action() -> None:
            if cancelled:
                token.cancel()
            if closing:
                closers.append(asyncio.create_task(api.aclose()))
            if expired:
                clock.value = 102.0
            if native:
                caller.cancel()

        adapter = _AsyncFault(transports, responses, action)
        with _clock(package, clock):
            api = package.AsyncClient(
                transport_adapter=adapter,
                options=options.ClientOptions(total_timeout=1.0, cancel_token=token if cancelled else None),
            )
            caller = asyncio.create_task(_acaptured(lambda: api.request_raw("GET", "https://race.example/race")))
            await arecord(lines, f"async {label}", lambda: caller)
            for closer in closers:
                await closer
            await api.aclose()
        record(lines, "async race resources", lambda: (adapter.sent, adapter.response.closed))


async def _capped_timeout(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    errors = importlib.import_module(f"{package.__name__}.errors")
    clock = _Clock()

    async def timed_out() -> None:
        raise errors.TransportError(
            delivery_state=errors.DeliveryState.MAYBE_SENT, phase="read", cause=httpx2.ReadTimeout("capped read")
        )

    adapter = _AsyncFault(transports, responses, timed_out)
    chunks = _GatedChunks()
    with _clock(package, clock):
        api = package.AsyncClient(
            transport_adapter=adapter, options=options.ClientOptions(total_timeout=0.05, cleanup_timeout=1.0)
        )
        body = bodies.AsyncStreamBody(chunks, ownership="owned")
        await arecord(
            lines,
            "deadline timer during the release of a capped timeout",
            lambda: _acaptured(lambda: api.request_raw("POST", "https://race.example/capped", body=body)),
        )
        chunks.proceed.set()
        await api.aclose()


async def _foreign_cancellation(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    async def sent() -> None:
        pass

    adapter = _AsyncFault(transports, responses, sent)
    stream = _UnwindingStream(responses)
    adapter.response = stream
    api = package.AsyncClient(
        transport_adapter=adapter,
        options=options.ClientOptions(total_timeout=None, stream_idle_timeout=0.05, cleanup_timeout=1.0),
    )
    async with api.with_streaming_response.request_raw("GET", "https://race.example/stream") as handle:

        async def read() -> None:
            async for _chunk in handle.iter_raw_bytes():
                pass

        reader = asyncio.create_task(_acaptured(read))
        await stream.unwinding.wait()
        closer = asyncio.create_task(_acaptured(handle.aclose))
        await asyncio.sleep(0)
        closer.cancel("closer cancelled")
        await arecord(lines, "idle stop of another task's read leaves a native close cancellation", lambda: closer)
        stream.release.set()
        stream.proceed.set()
        await arecord(lines, "idle stop of another task's read", lambda: reader)
    await api.aclose()
    record(lines, "idle stop of another task's read resources", lambda: (adapter.sent, stream.closed))


async def _early_timer(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    clock = _Clock()

    async def blocked() -> None:
        await asyncio.Event().wait()

    adapter = _AsyncFault(transports, responses, blocked)
    with _clock(package, clock):
        async with package.AsyncClient(
            transport_adapter=adapter, options=options.ClientOptions(total_timeout=0.05)
        ) as api:
            await arecord(
                lines,
                "deadline timer ahead of the clock",
                lambda: _acaptured(lambda: api.request_raw("GET", "https://race.example/early")),
            )


async def _expired_read(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    clock = _Clock()

    async def sent() -> None:
        pass

    adapter = _AsyncFault(transports, responses, sent)
    adapter.response = _ExpiringResponse(responses, clock)
    with _clock(package, clock):
        async with package.AsyncClient(
            transport_adapter=adapter, options=options.ClientOptions(total_timeout=1.0)
        ) as api:
            await arecord(
                lines,
                "deadline after buffered read",
                lambda: _acaptured(lambda: api.request_raw("GET", "https://race.example/expired")),
            )
    record(lines, "deadline after buffered read resources", lambda: (adapter.sent, adapter.response.closed))


class _LatePermit:
    def __init__(self, *, failure: bool = False) -> None:
        self.released = 0
        self.failure = failure

    async def release(self) -> None:
        self.released += 1
        if self.failure:
            raise RuntimeError("late permit release failed")


class _WaitingHook:
    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def on_event(self, event: object) -> None:
        if getattr(event, "name") == "attempt_start":
            self.entered.set()
            await asyncio.Event().wait()


async def _nested_waits(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    for reason in ("deadline", "token", "closing", "native"):
        token, hook = options.CancelToken(), _WaitingHook()

        async def unexpected_send() -> None:
            raise RuntimeError("send after a stopped hook")

        adapter = _AsyncFault(transports, responses, unexpected_send)
        api = package.AsyncClient(
            transport_adapter=adapter,
            options=options.ClientOptions(
                hooks=(hook,),
                total_timeout=0.2 if reason == "deadline" else None,
                cancel_token=token if reason == "token" else None,
            ),
        )
        caller = asyncio.create_task(_acaptured(lambda: api.request_raw("GET", "https://race.example/nested")))
        await hook.entered.wait()
        closer: asyncio.Task[None] | None = None
        if reason == "token":
            await asyncio.sleep(0.075)
            token.cancel()
        elif reason == "closing":
            closer = asyncio.create_task(api.aclose())
        elif reason == "native":
            caller.cancel()
        await arecord(lines, f"nested hook {reason}", lambda: caller)
        if closer is not None:
            await closer
        await api.aclose()
        record(lines, f"nested hook {reason} sends", lambda: adapter.sent)


class _GatedResponse(_AsyncResponse):
    def __init__(self, responses: ModuleType, *, failure: bool | BaseException) -> None:
        super().__init__(responses)
        self.entered = asyncio.Event()
        self.proceed = asyncio.Event()
        self.failure = failure

    async def aclose(self) -> None:
        self.closed += 1
        self.entered.set()
        await self.proceed.wait()
        if isinstance(self.failure, BaseException):
            raise self.failure
        if self.failure:
            raise RuntimeError("late response close failed")


async def _retained_responses(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    public_errors = importlib.import_module(f"{package.__name__}.errors")
    for native, failure in ((False, False), (False, True), (True, False), (True, True), (True, "compound")):

        async def sent() -> None:
            pass

        adapter = _AsyncFault(transports, responses, sent)
        close_failure = (
            public_errors.CleanupError(cause=RuntimeError(), secondary_errors=(ValueError(), KeyboardInterrupt()))
            if failure == "compound"
            else bool(failure)
        )
        response = _GatedResponse(responses, failure=close_failure)
        adapter.response = response
        api = package.AsyncClient(
            transport_adapter=adapter,
            options=options.ClientOptions(total_timeout=None, cleanup_timeout=0.01),
        )
        errors: list[BaseException] = []
        caller = asyncio.create_task(_acaptured(lambda: api.request_raw("GET", "https://race.example/cleanup"), errors))
        await response.entered.wait()
        if native:
            caller.cancel()
        label = f"retained response native={native} failure={failure}"
        await arecord(lines, label, lambda: caller)
        await arecord(lines, f"{label} first close", lambda: _acaptured(api.aclose))
        response.proceed.set()
        await api.aclose()
        record(
            lines,
            f"{label} resources",
            lambda: (
                adapter.sent,
                response.closed,
                tuple(type(error).__name__ for error in getattr(errors[0], "secondary_errors", ())),
                not native
                or not failure
                or not hasattr(errors[0], "add_note")
                or bool(getattr(errors[0], "__notes__", ())),
            ),
        )
        if failure == "compound":
            record(lines, f"{label} notes", lambda: tuple(getattr(errors[0], "__notes__", ())))


async def _acompound_cleanup(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    errors = importlib.import_module(f"{package.__name__}.errors")
    token = options.CancelToken()

    async def cancel() -> None:
        token.cancel()

    adapter = _AsyncFault(transports, responses, cancel)
    response = _GatedResponse(
        responses,
        failure=errors.CleanupError(cause=RuntimeError(), secondary_errors=(ValueError(), KeyboardInterrupt())),
    )
    response.proceed.set()
    adapter.response = response
    async with package.AsyncClient(
        transport_adapter=adapter,
        options=options.ClientOptions(cancel_token=token),
    ) as api:
        await arecord(
            lines,
            "compound async cleanup",
            lambda: _acaptured(lambda: api.request_raw("GET", "https://race.example/compound")),
        )
    record(lines, "compound async cleanup resources", lambda: (adapter.sent, adapter.response.closed))


async def _stopped_releases(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    for reason in ("token", "deadline"):
        token = options.CancelToken()

        async def sent() -> None:
            if reason == "token":
                token.cancel()

        adapter = _AsyncFault(transports, responses, sent)
        response = _GatedResponse(responses, failure=True)
        adapter.response = response
        api = package.AsyncClient(
            transport_adapter=adapter,
            options=options.ClientOptions(
                total_timeout=0.1 if reason == "deadline" else None,
                cancel_token=token if reason == "token" else None,
                cleanup_timeout=1.0,
            ),
        )
        errors: list[BaseException] = []
        label = f"{reason} stops response release"
        await arecord(
            lines, label, lambda: _acaptured(lambda: api.request_raw("GET", "https://race.example/release"), errors)
        )
        response.proceed.set()
        await arecord(lines, f"{label} close", lambda: _acaptured(api.aclose))
        record(
            lines,
            f"{label} late failure",
            lambda: (response.closed, tuple(type(error).__name__ for error in errors[0].secondary_errors)),
        )


class _Interrupted(BaseException):
    pass


class _GatedChunks:
    def __init__(self, *, failure: bool = False) -> None:
        self.proceed = asyncio.Event()
        self.closed = False
        self.failure = failure

    def __aiter__(self) -> _GatedChunks:
        return self

    async def __anext__(self) -> bytes:
        raise StopAsyncIteration

    async def aclose(self) -> None:
        await self.proceed.wait()
        self.closed = True
        if self.failure:
            raise RuntimeError("late body close failed")


class _Limiter:
    def __init__(self) -> None:
        self.permit = _LatePermit()

    async def acquire(self, context: object) -> _LatePermit:
        return self.permit


async def _stopped_attempts(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    for failure, late in (
        (RuntimeError("send failed"), False),
        (RuntimeError("send failed"), True),
        (_Interrupted("send interrupted"), False),
    ):

        async def failed() -> None:
            raise failure

        adapter = _AsyncFault(transports, responses, failed)
        chunks, limiter = _GatedChunks(failure=late), _Limiter()
        api = package.AsyncClient(
            transport_adapter=adapter,
            options=options.ClientOptions(total_timeout=0.1, limiter=limiter, cleanup_timeout=1.0),
        )
        label = f"deadline stops body release after {type(failure).__name__}{' and a late close failure' * late}"
        body = bodies.AsyncStreamBody(chunks, ownership="owned")
        errors: list[BaseException] = []
        await arecord(
            lines,
            label,
            lambda: _acaptured(lambda: api.request_raw("POST", "https://race.example/body", body=body), errors),
        )
        record(lines, f"{label} permit", lambda: (limiter.permit.released, chunks.closed))
        chunks.proceed.set()
        await api.aclose()
        record(
            lines,
            f"{label} resources",
            lambda: (
                adapter.sent,
                limiter.permit.released,
                chunks.closed,
                tuple(type(error).__name__ for error in getattr(errors[0], "secondary_errors", ())),
            ),
        )


class _LateLimiter:
    def __init__(self, *, deferred: bool, failure: bool, release_failure: bool) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.proceed = asyncio.Event()
        self.permit = _LatePermit(failure=release_failure)
        self.deferred = deferred
        self.failure = failure

    async def acquire(self, context: object) -> _LatePermit:
        self.started.set()
        try:
            await self.proceed.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            if self.deferred:
                await self.proceed.wait()
        if self.failure:
            raise RuntimeError("late acquire failed")
        return self.permit


async def _late_permits(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    for label, deferred, failure, release_failure, native in (
        ("late permit", False, False, False, False),
        ("late release failure", False, False, True, False),
        ("retained permit", True, False, False, False),
        ("retained acquire failure", True, True, False, False),
        ("retained release failure", True, False, True, False),
        ("late native permit", False, False, False, True),
        ("retained native permit", True, False, False, True),
    ):
        token = options.CancelToken()
        limiter = _LateLimiter(deferred=deferred, failure=failure, release_failure=release_failure)

        async def unexpected_send() -> None:
            raise RuntimeError("send after cancellation")

        adapter = _AsyncFault(transports, responses, unexpected_send)
        api = package.AsyncClient(
            transport_adapter=adapter,
            options=options.ClientOptions(
                total_timeout=None, cancel_token=None if native else token, limiter=limiter, cleanup_timeout=0.01
            ),
        )
        errors: list[BaseException] = []
        caller = asyncio.create_task(_acaptured(lambda: api.request_raw("GET", "https://race.example/permit"), errors))
        await limiter.started.wait()
        if native:
            caller.cancel()
        else:
            token.cancel()
        if deferred:
            await limiter.cancelled.wait()
            await arecord(lines, f"{label} first close", lambda: _acaptured(api.aclose))
            limiter.proceed.set()
        await arecord(lines, label, lambda: caller)
        await api.aclose()
        record(
            lines,
            f"{label} resources",
            lambda: (
                adapter.sent,
                limiter.cancelled.is_set(),
                limiter.permit.released,
                tuple(type(error).__name__ for error in getattr(errors[0], "secondary_errors", ())),
            ),
        )


async def _startup_cancellation(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    loop = asyncio.get_running_loop()
    for label in ("rejected cleanup", "client close"):
        rejected = label == "rejected cleanup"
        created = 0

        def cancelled_before_start(
            loop: asyncio.AbstractEventLoop,
            coroutine: Coroutine[object, object, object],
            **kwargs: object,
        ) -> asyncio.Task[object]:
            nonlocal created
            del kwargs
            task = asyncio.Task(coroutine, loop=loop)
            created += 1
            if created == 1:
                task.cancel("cancelled before owned coroutine starts")
            return task

        async def unexpected_send() -> None:
            raise RuntimeError("send after startup cancellation")

        adapter = _AsyncFault(transports, responses, unexpected_send)
        api = package.AsyncClient(
            transport_adapter=adapter,
            options=options.ClientOptions(
                total_timeout=0.0 if rejected else None,
                hooks=(_WaitingHook(),) if rejected else (),
            ),
        )
        previous = loop.get_task_factory()
        loop.set_task_factory(cancelled_before_start)
        operation = (
            api.aclose if label == "client close" else lambda: api.request_raw("GET", "https://race.example/startup")
        )
        try:
            await arecord(
                lines,
                f"startup cancellation {label}",
                lambda: _acaptured(operation),
            )
        finally:
            loop.set_task_factory(previous)
        await api.aclose()
        record(
            lines,
            f"startup cancellation {label} resources",
            lambda: (created, adapter.sent, adapter.response.closed),
        )


async def _async(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    await _async_races(package, options, transports, responses, lines)
    await _early_timer(package, options, transports, responses, lines)
    await _capped_timeout(package, options, transports, responses, lines)
    await _foreign_cancellation(package, options, transports, responses, lines)
    await _expired_read(package, options, transports, responses, lines)
    await _nested_waits(package, options, transports, responses, lines)
    await _late_permits(package, options, transports, responses, lines)
    await _retained_responses(package, options, transports, responses, lines)
    await _acompound_cleanup(package, options, transports, responses, lines)
    await _stopped_releases(package, options, transports, responses, lines)
    await _stopped_attempts(package, options, transports, responses, lines)
    await _startup_cancellation(package, options, transports, responses, lines)


def deadline_races(package: ModuleType, lines: list[str]) -> None:
    """Inject only failures and deterministic races, exercising public generated client calls throughout."""
    options, transports, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "transports", "responses")
    )
    _phase_sources(package, options, lines)
    _admission_race(package, options, transports, responses, lines)
    _sync_races(package, options, transports, responses, lines)
    _compound_cleanup(package, options, transports, responses, lines)
    run(lambda: _async(package, options, transports, responses, lines))
