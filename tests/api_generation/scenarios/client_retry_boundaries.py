"""Observe retry policy at preparation boundaries and deterministic cancellation races."""

from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING

import httpx2

from tests.api_generation.scenarios.client_retry_calls import (
    _Broken,
    _capture,
    _error,
    _Events,
    _report,
    _secondary,
    _Stop,
)
from tests.api_generation.support.client_runtime import Exchange, arecord, failing, injected, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType


class _Fault:
    """Raise a failure from the injected client's request hook once the call reaches a chosen send."""

    def __init__(self, failure: BaseException, send: int) -> None:
        self.failure, self.send = failure, send
        self.calls = 0

    def __call__(self, request: httpx2.Request) -> None:
        del request
        self.calls += 1
        if self.calls == self.send:
            raise self.failure
        if self.calls > self.send:
            message = "finite guard against resending a failed send"
            raise RuntimeError(message)

    async def asynchronous(self, request: httpx2.Request) -> None:
        self(request)


class _Interrupting:
    """Interrupt each response from the injected client's response hook, before the SDK receives it."""

    def __init__(self, failure: BaseException) -> None:
        self.failure = failure

    def __call__(self, response: httpx2.Response) -> None:
        del response
        raise self.failure

    async def asynchronous(self, response: httpx2.Response) -> None:
        self(response)


class _NativeBody(_Broken):
    """A body interrupted while it streams, whose close raises the same interruption again."""

    def __init__(self, failure: BaseException) -> None:
        super().__init__(read=False, close=failure)
        self.failure = failure

    def __iter__(self) -> Iterator[bytes]:
        yield b"before interruption"
        raise self.failure


def retry_boundaries(package: ModuleType, lines: list[str]) -> None:
    """Keep keys and error attribution correct when a native send fails or an interruption ends the call."""
    options, errors = (importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "errors"))
    _configuration(package, options, lines)
    _presend(package, options, errors, lines)
    _uncapped(package, options, lines)
    _terminal(package, lines)
    _failed_streams(package, lines)
    run(lambda: _async(package, options, errors, lines))


def _configuration(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Send a caller header of the declared key name as the call's key, retained through a retry."""
    exchange = Exchange(lines)
    exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"accepted", "text/plain"))
    with (
        exchange.client() as native,
        package.Client(http_client=native, retry=options.RetryOptions(initial_delay=0)) as api,
    ):
        record(
            lines,
            "declared key header becomes the key",
            lambda: api.retry.post_keyed(
                body=b"payload", options=options.RequestOptions(extra_headers={"Idempotency-Key": "manual"})
            ),
        )
    lines.append(f"    queued={len(exchange.responders)}")


def _presend(package: ModuleType, options: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    for stage, send in (("first", 1), ("retried", 2)):
        failure = errors.APITimeoutError(reason="phase_timeout")
        fault, events, exchange = _Fault(failure, send), _Events(), Exchange([])
        exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"unused", "text/plain"))
        with (
            exchange.client(event_hooks={"request": [fault, events.request], "response": [events.response]}) as native,
            package.Client(http_client=native, retry=options.RetryOptions(initial_delay=0)) as api,
        ):
            try:
                api.retry.get_safe()
            except errors.SDKError as error:
                observed = _error(error), getattr(error, "cause", None) is failure, fault.calls
            else:
                observed = ("returned",)
        record(lines, f"request hook failure {stage} send", lambda observed=observed: observed)
        _report(lines, events, exchange)


def _uncapped(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Classify native timeouts after the send started when no phase timeout or deadline bounds the attempt."""
    exchange = Exchange([])
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            timeout=httpx2.Timeout(600.0, connect=5.0, read=None, write=None),
            max_retries=1,
            retry=options.RetryOptions(initial_delay=0),
        ) as api,
    ):
        for error in (httpx2.ReadTimeout, httpx2.WriteTimeout):
            exchange.respond(failing(error), raw_response(200, b"resent", "text/plain"))
            record(lines, f"uncapped native {error.__name__}", lambda: _capture(api.retry.get_safe))
            lines.append(f"    queued={len(exchange.responders)}")
            exchange.responders.clear()


async def _async(package: ModuleType, options: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    await _async_presend(package, options, errors, lines)
    await _late_native(package, lines)
    await _async_terminal(package, lines)
    await _async_failed_streams(package, lines)
    exchange = Exchange([])
    exchange.respond(raw_response(200, b"bounded stream", "text/plain"))
    async with (
        exchange.async_client() as native,
        package.AsyncClient(http_client=native) as api,
        api.retry.with_streaming_response.get_safe(
            options=options.RequestOptions(timeout=httpx2.Timeout(600.0, connect=5.0, read=2.0))
        ) as response,
    ):
        await arecord(lines, "stream reuses its deadline through EOF", response.read)


async def _async_presend(package: ModuleType, options: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    for stage, send in (("first", 1), ("retried", 2)):
        failure = errors.APITimeoutError(reason="phase_timeout")
        fault, events, exchange = _Fault(failure, send), _Events(), Exchange([])
        exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"unused", "text/plain"))
        hooks = {"request": [fault.asynchronous, events.arequest], "response": [events.aresponse]}
        async with (
            exchange.async_client(event_hooks=hooks) as native,
            package.AsyncClient(http_client=native, retry=options.RetryOptions(initial_delay=0)) as api,
        ):
            try:
                await api.retry.get_safe()
            except errors.SDKError as error:
                observed = _error(error), getattr(error, "cause", None) is failure, fault.calls
            else:
                observed = ("returned",)
        record(lines, f"async request hook failure {stage} send", lambda observed=observed: observed)
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


async def _late_native(package: ModuleType, lines: list[str]) -> None:
    transport = _Blocking()
    async with (
        httpx2.AsyncClient(transport=transport) as native,
        package.AsyncClient(http_client=native) as api,
    ):

        async def request() -> tuple[object, ...]:
            try:
                await api.retry.post_idempotent(body=b"request")
            except asyncio.CancelledError as error:
                return type(error).__name__, error.args, getattr(error, "__notes__", ())
            return ("returned",)

        caller = asyncio.create_task(request())
        await transport.entered.wait()
        caller.cancel("original caller interruption")
        await arecord(lines, "native send cancellation", lambda: caller)
    record(lines, "cancelled native sends", lambda: transport.cancelled)


def _terminal(package: ModuleType, lines: list[str]) -> None:
    for point in ("response hook", "body"):
        primary = KeyboardInterrupt("first native terminal interruption")
        exchange = Exchange([])
        hooks = {"response": [_Interrupting(primary)]} if point == "response hook" else {}
        if point == "body":
            body = _NativeBody(primary)
            exchange.respond(
                injected(lambda _, body=body: httpx2.Response(200, headers={"Content-Type": "text/plain"}, stream=body))
            )
        else:
            exchange.respond(raw_response(200, b"done", "text/plain"))
        with (
            exchange.client(event_hooks=hooks) as native,
            package.Client(http_client=native) as api,
        ):
            try:
                if point == "body":
                    with api.retry.with_streaming_response.get_safe() as response:
                        response.read()
                else:
                    api.retry.get_safe()
            except KeyboardInterrupt as error:
                observed = type(error).__name__, error is primary, error.args, getattr(error, "__notes__", ())
            else:
                observed = ("returned",)
        record(lines, f"terminal native interruption {point}", lambda observed=observed: observed)


def _caller_block(failure: str) -> None:
    if failure != "block":
        return
    message = "caller block failure"
    raise ValueError(message)


def _failed_streams(package: ModuleType, lines: list[str]) -> None:
    for failure in ("body", "block"):
        primary = KeyboardInterrupt("stream close interruption")
        exchange = Exchange([])
        body = _Broken(read=True, close=primary)
        exchange.respond(
            injected(lambda _, body=body: httpx2.Response(200, headers={"Content-Type": "text/plain"}, stream=body))
        )
        with exchange.client() as native, package.Client(http_client=native) as api:
            try:
                with api.retry.with_streaming_response.get_safe() as response:
                    _caller_block(failure)
                    response.read()
            except KeyboardInterrupt as error:
                observed = type(error).__name__, error is primary, getattr(error, "__notes__", ()), _secondary(error)
        record(lines, f"stream close interruption after a failed {failure}", lambda observed=observed: observed)


async def _async_failed_streams(package: ModuleType, lines: list[str]) -> None:
    for failure in ("body", "block"):
        primary = _Stop("stream close interruption")
        exchange = Exchange([])
        body = _Broken(read=True, close=primary)
        exchange.respond(
            injected(lambda _, body=body: httpx2.Response(200, headers={"Content-Type": "text/plain"}, stream=body))
        )
        async with exchange.async_client() as native, package.AsyncClient(http_client=native) as api:
            try:
                async with api.retry.with_streaming_response.get_safe() as response:
                    _caller_block(failure)
                    await response.read()
            except _Stop as error:
                observed = type(error).__name__, error is primary, getattr(error, "__notes__", ()), _secondary(error)
        record(lines, f"async stream close interruption after a failed {failure}", lambda observed=observed: observed)


async def _async_terminal(package: ModuleType, lines: list[str]) -> None:
    for point in ("response hook", "body"):
        primary = asyncio.CancelledError("first native terminal interruption")
        exchange = Exchange([])
        hooks = {"response": [_Interrupting(primary).asynchronous]} if point == "response hook" else {}
        if point == "body":
            body = _NativeBody(primary)
            exchange.respond(
                injected(lambda _, body=body: httpx2.Response(200, headers={"Content-Type": "text/plain"}, stream=body))
            )
        else:
            exchange.respond(raw_response(200, b"done", "text/plain"))
        async with (
            exchange.async_client(event_hooks=hooks) as native,
            package.AsyncClient(http_client=native) as api,
        ):
            try:
                if point == "body":
                    async with api.retry.with_streaming_response.get_safe() as response:
                        await response.read()
                else:
                    await api.retry.get_safe()
            except asyncio.CancelledError as error:
                observed = type(error).__name__, error is primary, error.args, getattr(error, "__notes__", ())
            else:
                observed = ("returned",)
        record(lines, f"async terminal native interruption {point}", lambda observed=observed: observed)
