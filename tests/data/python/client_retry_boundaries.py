"""Observe retry policy at preparation boundaries and deterministic cancellation races."""

from __future__ import annotations

import asyncio
import importlib
import sys
from time import monotonic
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx2

from tests.data.python.client_body_replay import _Attempt, _Factory
from tests.data.python.client_limiters import _AsyncSemaphoreLimiter, _SemaphoreLimiter
from tests.data.python.client_retry_calls import _Broken, _capture, _error, _Events, _report, _Stop
from tests.data.python.client_runtime import Exchange, arecord, injected, raw_response, record, run
from tests.data.python.client_transports import Adapter, AsyncAdapter, AsyncResponse

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType


class _ShapeLimiter:
    """Inject a late descriptor failure after the application limiter passed option validation."""

    def __init__(self, inner: _SemaphoreLimiter | _AsyncSemaphoreLimiter, failure: BaseException) -> None:
        self.inner, self.failure = inner, failure
        self.armed = False
        self.calls = 0

    @property
    def acquire(self) -> object:
        if self.armed:
            self.calls += 1
            if self.calls <= 2:
                raise self.failure
            message = "finite guard against retrying a pre-send failure"
            raise RuntimeError(message)
        return self.inner.acquire


class _ArmLimiter:
    def __init__(self, limiter: _ShapeLimiter, event: str) -> None:
        self.limiter, self.event = limiter, event

    def on_event(self, event: object) -> None:
        if getattr(event, "name", None) == self.event:
            self.limiter.armed = True


class _TerminalHook:
    def __init__(self, failures: dict[str, BaseException]) -> None:
        self.failures = failures
        self.events: list[tuple[object, object]] = []

    def on_event(self, event: object) -> None:
        name = getattr(event, "name", "")
        self.events.append((name, getattr(event, "outcome", None)))
        if (failure := self.failures.get(name)) is not None:
            raise failure


class _NativeBody(_Broken):
    def __init__(self, failure: BaseException) -> None:
        super().__init__(read=False)
        self.failure = failure

    def __iter__(self) -> Iterator[bytes]:
        yield b"before interruption"
        raise self.failure


def retry_boundaries(package: ModuleType, lines: list[str]) -> None:
    """Keep keys, permits, and error attribution correct when preparation or waiting changes the call state."""
    options, bodies, errors, transports = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "bodies", "errors", "transports")
    )
    _configuration(package, options, lines)
    _presend(package, options, errors, lines)
    _uncapped(package, options, errors, transports, lines)
    _closing_wait(package, options, errors, lines)
    _terminal(package, options, lines)
    _failed_streams(package, options, lines)
    _hook_interruption(package, options, lines)
    run(lambda: _async(package, options, bodies, errors, transports, lines))


def _hook_interruption(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    primary = _Stop("intermediate attempt hook interruption")
    hook = _TerminalHook({"attempt_end": primary})
    exchange = Exchange([])
    exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"done", "text/plain"))
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(hooks=(hook,), retry=options.RetryOptions(initial_delay=0)),
        ) as api,
    ):
        try:
            api.retry.get_safe()
        except BaseException as error:  # noqa: BLE001
            observed = type(error).__name__, error is primary, getattr(error, "__notes__", ())
        else:
            observed = ("returned",)
    record(lines, "intermediate hook interruption", lambda: observed)
    record(lines, "intermediate hook interruption events", lambda: hook.events)


async def _ahook_interruption(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    primary = _Stop("intermediate attempt hook interruption")
    hook = _TerminalHook({"attempt_end": primary})
    exchange = Exchange([])
    exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"done", "text/plain"))
    async with (
        exchange.async_client() as native,
        package.AsyncClient(
            http_client=native,
            options=options.ClientOptions(hooks=(hook,), retry=options.RetryOptions(initial_delay=0)),
        ) as api,
    ):
        try:
            await api.retry.get_safe()
        except BaseException as error:  # noqa: BLE001
            observed = type(error).__name__, error is primary, getattr(error, "__notes__", ())
        else:
            observed = ("returned",)
    record(lines, "async intermediate hook interruption", lambda: observed)
    record(lines, "async intermediate hook interruption events", lambda: hook.events)


def _configuration(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    with package.Client() as api:
        record(
            lines,
            "declared key header stays managed",
            lambda: api.retry.post_keyed(
                body=b"payload", options=options.RequestOptions(headers=(("Idempotency-Key", "manual"),))
            ),
        )
    with patch.dict(sys.modules, {"h2": None}):
        record(
            lines,
            "missing optional HTTP2 dependency",
            lambda: package.Client(options=options.ClientOptions(transport=options.TransportOptions(http2=True))),
        )


def _presend(package: ModuleType, options: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    for stage in ("call_start", "retry_scheduled"):
        failure = errors.PhaseTimeoutError(
            effective_timeout=1.0, phase="read", delivery_state=errors.DeliveryState.NOT_SENT
        )
        limiter, events, exchange = _ShapeLimiter(_SemaphoreLimiter(), failure), _Events(), Exchange([])
        exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"unused", "text/plain"))
        with (
            exchange.client() as native,
            package.Client(
                http_client=native,
                options=options.ClientOptions(
                    total_timeout=None,
                    retry=options.RetryOptions(initial_delay=0),
                    limiter=limiter,
                    hooks=(_ArmLimiter(limiter, stage), events),
                ),
            ) as api,
        ):
            try:
                api.retry.get_safe()
            except BaseException as error:  # noqa: BLE001
                observed = _error(error), error is failure, limiter.calls
            else:
                observed = ("returned",)
        record(lines, f"pre-send descriptor {stage}", lambda observed=observed: observed)
        _report(lines, events, exchange)


def _uncapped(
    package: ModuleType, options: ModuleType, errors: ModuleType, transports: ModuleType, lines: list[str]
) -> None:
    for phase in ("read", "unknown"):
        failure = errors.TransportError(
            delivery_state=errors.DeliveryState.MAYBE_SENT,
            phase=phase,
            cause=httpx2.ReadTimeout("adapter timeout"),
        )
        adapter = Adapter(transports, [])

        def failed(_request: object, _context: object, failure: Exception = failure) -> object:
            raise failure

        adapter.replies.append(failed)
        with package.Client(
            transport_adapter=adapter,
            options=options.ClientOptions(
                total_timeout=None, timeout=options.TimeoutOptions(read=None), retry=options.RetryOptions(max_retries=0)
            ),
        ) as api:
            record(lines, f"uncapped classified timeout {phase}", lambda api=api: _capture(api.retry.get_safe))


def _closing_wait(package: ModuleType, options: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    """Close the client after the retry sleep checked the call and read what is left, just before it waits.

    Once the retry is scheduled, the sleep reads the clock to bound its real time, then again after its check.
    """
    exchange = Exchange([])
    reads = 0
    close_failures: list[str] = []

    def scheduled() -> None:
        nonlocal reads
        reads = 2

    def clock() -> float:
        nonlocal reads
        if reads:
            reads -= 1
            if not reads:
                try:
                    api.close()
                except errors.CleanupError as error:
                    close_failures.append(type(error).__name__)
        return monotonic()

    events = _Events(scheduled=scheduled)
    exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"unused", "text/plain"))
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(
                total_timeout=None,
                cleanup_timeout=0.001,
                retry=options.RetryOptions(initial_delay=0.1, jitter="none"),
                hooks=(events,),
                clock=options.Clock(monotonic=clock),
            ),
        ) as api,
    ):
        record(lines, "close wins immediately before wait", lambda: _capture(api.retry.get_safe))
    record(lines, "close race cleanup", lambda: close_failures)
    _report(lines, events, exchange)


async def _async(
    package: ModuleType,
    options: ModuleType,
    bodies: ModuleType,
    errors: ModuleType,
    transports: ModuleType,
    lines: list[str],
) -> None:
    await _async_presend(package, options, errors, lines)
    await _late_native(package, options, bodies, transports, lines)
    await _async_terminal(package, options, lines)
    await _async_failed_streams(package, options, lines)
    await _ahook_interruption(package, options, lines)
    exchange = Exchange([])
    exchange.respond(raw_response(200, b"bounded stream", "text/plain"))
    async with (
        exchange.async_client() as native,
        package.AsyncClient(http_client=native) as api,
        api.retry.with_streaming_response.get_safe(options=options.RequestOptions(stream_total_timeout=2)) as response,
    ):
        await arecord(lines, "stream reuses its deadline through EOF", response.read)
    with patch.dict(sys.modules, {"h2": None}):
        record(
            lines,
            "async missing optional HTTP2 dependency",
            lambda: package.AsyncClient(options=options.ClientOptions(transport=options.TransportOptions(http2=True))),
        )


async def _async_presend(package: ModuleType, options: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    for stage in ("call_start", "retry_scheduled"):
        failure = errors.PhaseTimeoutError(
            effective_timeout=1.0, phase="read", delivery_state=errors.DeliveryState.NOT_SENT
        )
        limiter, events, exchange = _ShapeLimiter(_AsyncSemaphoreLimiter(), failure), _Events(), Exchange([])
        exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"unused", "text/plain"))
        async with (
            exchange.async_client() as native,
            package.AsyncClient(
                http_client=native,
                options=options.ClientOptions(
                    total_timeout=None,
                    retry=options.RetryOptions(initial_delay=0),
                    limiter=limiter,
                    hooks=(_ArmLimiter(limiter, stage), events),
                ),
            ) as api,
        ):
            try:
                await api.retry.get_safe()
            except BaseException as error:  # noqa: BLE001
                observed = _error(error), error is failure, limiter.calls
            else:
                observed = ("returned",)
        record(lines, f"async pre-send descriptor {stage}", lambda observed=observed: observed)
        _report(lines, events, exchange)


async def _late_native(
    package: ModuleType, options: ModuleType, bodies: ModuleType, transports: ModuleType, lines: list[str]
) -> None:
    responses = importlib.import_module(f"{package.__name__}.responses")
    entered = asyncio.Event()
    adapter = AsyncAdapter(transports, [])
    response = AsyncResponse([], 200, responses.HeadersView((("content-type", "text/plain"),)), (b"late",))

    async def late(_request: object, _context: object) -> AsyncResponse:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return response

    adapter.send = late
    attempt = _Attempt(failure=_Stop("late body cleanup interruption"))
    limiter = _AsyncSemaphoreLimiter()
    async with package.AsyncClient(transport_adapter=adapter, options=options.ClientOptions(limiter=limiter)) as api:

        async def request() -> tuple[object, ...]:
            try:
                await api.retry.post_idempotent(body=bodies.AsyncBodyFactory(_Factory((attempt,)).async_call))
            except asyncio.CancelledError as error:
                return type(error).__name__, error.args, getattr(error, "__notes__", ())
            return ("returned",)

        caller = asyncio.create_task(request())
        await entered.wait()
        caller.cancel("original caller interruption")
        await arecord(lines, "late cleanup preserves caller interruption", lambda: caller)
    record(lines, "late resources released", lambda: (attempt.closes, response.lines, limiter.usage.active))


def _terminal(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for event in ("attempt_end", "call_end", "call_start", "stream_end", "body"):
        primary = KeyboardInterrupt("first native terminal interruption")
        secondary = SystemExit("later native terminal interruption")
        first = _TerminalHook({"stream_end" if event == "body" else event: secondary if event == "body" else primary})
        second = _TerminalHook({"stream_end" if event == "body" else event: RuntimeError("ordinary terminal failure")})
        third = _TerminalHook({
            "call_end" if event == "call_start" else "stream_end" if event == "body" else event: secondary
        })
        if event == "call_start":
            first.failures["call_end"] = primary
        elif event == "attempt_end":
            third.failures["call_end"] = _Stop("separate call_end interruption")
        hooks = (second, first, third) if event == "call_start" else (first, second, third)
        exchange = Exchange([])
        if event == "body":
            body = _NativeBody(primary)
            exchange.respond(
                injected(lambda _, body=body: httpx2.Response(200, headers={"Content-Type": "text/plain"}, stream=body))
            )
        else:
            exchange.respond(raw_response(200, b"done", "text/plain"))
        with (
            exchange.client() as native,
            package.Client(http_client=native, options=options.ClientOptions(hooks=hooks)) as api,
        ):
            try:
                if event in {"stream_end", "body"}:
                    with api.retry.with_streaming_response.get_safe() as response:
                        response.read()
                else:
                    api.retry.get_safe()
            except BaseException as error:  # noqa: BLE001
                observed = type(error).__name__, error is primary, error.args, getattr(error, "__notes__", ())
            else:
                observed = ("returned",)
        record(lines, f"terminal native drain {event}", lambda observed=observed: observed)
        record(
            lines,
            "terminal hook order",
            lambda first=first, second=second, third=third: (first.events, second.events, third.events),
        )
    exchange = Exchange([])
    token = options.CancelToken()
    events = _Events(ended=token.cancel, fail="attempt_end")
    exchange.respond(raw_response(200, b"discarded", "text/plain"))
    with (
        exchange.client() as native,
        package.Client(http_client=native, options=options.ClientOptions(cancel_token=token, hooks=(events,))) as api,
    ):
        record(
            lines,
            "raw terminal cancellation retains hook failure",
            lambda: _capture(lambda: api.request_raw("GET", "https://api.example.com/safe")),
        )
    _report(lines, events, exchange)


def _failed_streams(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for failure in ("body", "block"):
        primary = KeyboardInterrupt("stream_end interruption")
        hook = _TerminalHook({"stream_end": primary})
        exchange = Exchange([])
        body = _Broken(read=True)
        exchange.respond(
            injected(lambda _, body=body: httpx2.Response(200, headers={"Content-Type": "text/plain"}, stream=body))
        )
        with (
            exchange.client() as native,
            package.Client(http_client=native, options=options.ClientOptions(hooks=(hook,))) as api,
        ):
            try:
                with api.retry.with_streaming_response.get_safe() as response:
                    if failure == "block":
                        message = "caller block failure"
                        raise ValueError(message)
                    response.read()
            except BaseException as error:  # noqa: BLE001
                observed = type(error).__name__, error is primary, getattr(error, "__notes__", ())
        record(lines, f"stream_end interruption after a failed {failure}", lambda observed=observed: observed)


async def _async_failed_streams(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for failure in ("body", "block"):
        primary = _Stop("stream_end interruption")
        hook = _TerminalHook({"stream_end": primary})
        exchange = Exchange([])
        body = _Broken(read=True)
        exchange.respond(
            injected(lambda _, body=body: httpx2.Response(200, headers={"Content-Type": "text/plain"}, stream=body))
        )
        async with (
            exchange.async_client() as native,
            package.AsyncClient(http_client=native, options=options.ClientOptions(hooks=(hook,))) as api,
        ):
            try:
                async with api.retry.with_streaming_response.get_safe() as response:
                    if failure == "block":
                        message = "caller block failure"
                        raise ValueError(message)
                    await response.read()
            except BaseException as error:  # noqa: BLE001
                observed = type(error).__name__, error is primary, getattr(error, "__notes__", ())
        record(lines, f"async stream_end interruption after a failed {failure}", lambda observed=observed: observed)


async def _async_terminal(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for event in ("attempt_end", "call_end", "call_start", "stream_end", "body"):
        primary = asyncio.CancelledError("first native terminal interruption")
        secondary = _Stop("later native terminal interruption")
        first = _TerminalHook({"stream_end" if event == "body" else event: secondary if event == "body" else primary})
        second = _TerminalHook({"stream_end" if event == "body" else event: RuntimeError("ordinary terminal failure")})
        third = _TerminalHook({
            "call_end" if event == "call_start" else "stream_end" if event == "body" else event: secondary
        })
        if event == "call_start":
            first.failures["call_end"] = primary
        elif event == "attempt_end":
            third.failures["call_end"] = _Stop("separate call_end interruption")
        hooks = (second, first, third) if event == "call_start" else (first, second, third)
        exchange = Exchange([])
        if event == "body":
            body = _NativeBody(primary)
            exchange.respond(
                injected(lambda _, body=body: httpx2.Response(200, headers={"Content-Type": "text/plain"}, stream=body))
            )
        else:
            exchange.respond(raw_response(200, b"done", "text/plain"))
        async with (
            exchange.async_client() as native,
            package.AsyncClient(http_client=native, options=options.ClientOptions(hooks=hooks)) as api,
        ):
            try:
                if event in {"stream_end", "body"}:
                    async with api.retry.with_streaming_response.get_safe() as response:
                        await response.read()
                else:
                    await api.retry.get_safe()
            except BaseException as error:  # noqa: BLE001
                observed = type(error).__name__, error is primary, error.args, getattr(error, "__notes__", ())
            else:
                observed = ("returned",)
        record(lines, f"async terminal native drain {event}", lambda observed=observed: observed)
        record(
            lines,
            "async terminal hook order",
            lambda first=first, second=second, third=third: (first.events, second.events, third.events),
        )
