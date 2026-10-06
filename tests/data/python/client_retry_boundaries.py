"""Observe retry policy at preparation boundaries and deterministic cancellation races."""

from __future__ import annotations

import asyncio
import importlib
import sys
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx2

from tests.data.python.client_body_replay import _Attempt, _Factory
from tests.data.python.client_limiters import _AsyncSemaphoreLimiter, _SemaphoreLimiter
from tests.data.python.client_retry_calls import _Broken, _capture, _error, _Events, _report, _secondary, _Stop
from tests.data.python.client_runtime import Exchange, arecord, failing, injected, raw_response, record, run

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
    options, bodies, errors = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "bodies", "errors")
    )
    _configuration(package, options, lines)
    _presend(package, options, errors, lines)
    _uncapped(package, options, lines)
    _terminal(package, options, lines)
    _failed_streams(package, options, lines)
    _hook_interruption(package, options, lines)
    run(lambda: _async(package, options, bodies, errors, lines))


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
            observed = type(error).__name__, error is primary, getattr(error, "__notes__", ()), _secondary(error)
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
            observed = type(error).__name__, error is primary, getattr(error, "__notes__", ()), _secondary(error)
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
        failure = errors.APITimeoutError(
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


def _uncapped(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Classify native timeouts after the send started when no phase timeout or deadline bounds the attempt."""
    exchange = Exchange([])
    config = options.ClientOptions(
        total_timeout=None,
        timeout=options.TimeoutOptions(read=None, write=None),
        retry=options.RetryOptions(max_retries=1, initial_delay=0),
    )
    with exchange.client() as native, package.Client(http_client=native, options=config) as api:
        for error in (httpx2.ReadTimeout, httpx2.WriteTimeout):
            exchange.respond(failing(error), raw_response(200, b"resent", "text/plain"))
            record(lines, f"uncapped native {error.__name__}", lambda: _capture(api.retry.get_safe))
            lines.append(f"    queued={len(exchange.responders)}")
            exchange.responders.clear()


async def _async(
    package: ModuleType, options: ModuleType, bodies: ModuleType, errors: ModuleType, lines: list[str]
) -> None:
    await _async_presend(package, options, errors, lines)
    await _late_native(package, options, bodies, lines)
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
        failure = errors.APITimeoutError(
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


class _Blocking(httpx2.AsyncBaseTransport):
    """Hold each request until its caller is cancelled, then let the cancellation propagate."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cancelled = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        del request
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return httpx2.Response(200)


async def _late_native(package: ModuleType, options: ModuleType, bodies: ModuleType, lines: list[str]) -> None:
    transport = _Blocking()
    attempt = _Attempt(failure=_Stop("late body cleanup interruption"))
    limiter = _AsyncSemaphoreLimiter()
    async with (
        httpx2.AsyncClient(transport=transport) as native,
        package.AsyncClient(http_client=native, options=options.ClientOptions(limiter=limiter)) as api,
    ):

        async def request() -> tuple[object, ...]:
            try:
                await api.retry.post_idempotent(body=bodies.AsyncBodyFactory(_Factory((attempt,)).async_call))
            except BaseException as error:  # noqa: BLE001
                return type(error).__name__, error.args, getattr(error, "__notes__", ())
            return ("returned",)

        caller = asyncio.create_task(request())
        await transport.entered.wait()
        caller.cancel("original caller interruption")
        await arecord(lines, "send cancellation with a failing body cleanup", lambda: caller)
    record(
        lines, "cancelled send resources released", lambda: (attempt.closes, transport.cancelled, limiter.usage.active)
    )


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


def _caller_block(failure: str) -> None:
    if failure != "block":
        return
    message = "caller block failure"
    raise ValueError(message)


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
                    _caller_block(failure)
                    response.read()
            except BaseException as error:  # noqa: BLE001
                observed = type(error).__name__, error is primary, getattr(error, "__notes__", ()), _secondary(error)
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
                    _caller_block(failure)
                    await response.read()
            except BaseException as error:  # noqa: BLE001
                observed = type(error).__name__, error is primary, getattr(error, "__notes__", ()), _secondary(error)
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
