"""Observe generated clients' calls through hooks: their events in order, and how a failing hook ends a call."""

from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_runtime import (
    Exchange,
    Injected,
    abroken,
    aoutcome,
    arecord,
    argument,
    broken,
    json_response,
    outcome,
    raw_response,
    record,
    run,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
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


class Recorder:
    """A hook that writes each event's safe facts to the report, and raises on the events it is told to."""

    def __init__(self, lines: list[str], label: str, failing: tuple[str, ...] = ()) -> None:
        """Keep the report, the hook's label, and the names of the events it raises on."""
        self.lines = lines
        self.label = label
        self.failing = failing

    def on_event(self, event: Any) -> None:
        """Report the event, then raise when its name is one this hook fails on."""
        self.lines.append(f"    {self.label}: {_described(event)}")
        if event.name in self.failing:
            msg = f"{self.label} failed on {event.name}"
            raise RuntimeError(msg)


class AsyncRecorder(Recorder):
    """An asynchronous hook that reports each event and raises on the events it is told to."""

    async def on_event(self, event: Any) -> None:
        """Report the event, then raise when its name is one this hook fails on."""
        super().on_event(event)


def _described(event: Any) -> str:
    facts = [
        event.name,
        f"attempt={event.attempt_count}",
        f"sent={event.sent}",
        f"status={event.status}",
        f"outcome={event.outcome}",
        f"phase={event.phase}",
        f"path={event.path}",
        f"origin={event.origin}",
        f"attempts={event.attempt_count}",
        f"request_id={event.request_id}",
        f"timed={event.duration is not None}",
        f"context={dict(event.context)}",
    ]
    if event.options is not None:
        facts.append(f"options={dict(event.options)}")
    return " ".join(facts)


def _modules(package: ModuleType) -> tuple[ModuleType, ModuleType, ModuleType]:
    options, errors, types = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "errors", "types.pets")
    )
    return options, errors, types


def hooks(package: ModuleType, lines: list[str]) -> None:
    """Report the events of calls that succeed, fail, and are refused, and the errors of hooks that fail on them."""
    options, errors, types = _modules(package)
    exchange = Exchange(lines)
    http = exchange.client()
    watched = options.ClientOptions(hooks=(Recorder(lines, "a"),), context={"tenant": "t1", "retry": 0})
    pet = argument(package, "getPet", "path", "petId", 3)
    trace = argument(package, "listPets", "header", "X-Trace", "t")
    with package.Client(http_client=http, options=watched) as api:
        exchange.respond(json_response(200, _PET))
        record(lines, "get", lambda: api.pets.get_pet(pet_id=pet))
        exchange.respond(json_response(200, [], **{"X-Rate": "1", "X-Request-Id": "req-7"}))
        call = options.RequestOptions(context={"retry": 1, "user": None})
        record(
            lines,
            "list with its request ID and a call context",
            lambda: api.pets.list_pets(x_trace=trace, options=call),
        )
        exchange.respond(json_response(404, {"code": 7, "message": "gone"}))
        record(lines, "get missing", lambda: api.pets.get_pet(pet_id=pet))
        record(lines, "list of a trace its codec refuses", lambda: api.pets.list_pets(x_trace=object()))
        exchange.respond(_refusing)
        record(lines, "get of a refused connection", lambda: api.pets.get_pet(pet_id=pet))
        exchange.respond(_interrupting)
        try:
            api.pets.get_pet(pet_id=pet)
        except KeyboardInterrupt:
            lines.append("  get interrupted: KeyboardInterrupt")
        _failing(api, exchange, lines, options, errors, pet)
        _raw(api, exchange, lines, options, pet)
    http.close()
    _refused(package, lines, options)
    run(lambda: _async_hooks(package, lines))


def _failing(  # noqa: PLR0913, PLR0917
    api: Any, exchange: Exchange, lines: list[str], options: ModuleType, errors: ModuleType, pet: object
) -> None:
    """Stop a call at the event a hook fails on, end it still, and keep a success it completed."""
    for label, failing, respond in (
        ("hook failing to start", ("call_start",), False),
        ("hook failing before sending", ("attempt_start",), False),
        ("hooks failing on headers and every end", ("response_headers", "attempt_end", "call_end"), True),
    ):
        if respond:
            exchange.respond(json_response(200, _PET))
        view = api.with_options(
            options.RequestOptions(hooks=(Recorder(lines, "b", failing), Recorder(lines, "c", failing)))
        )
        lines.append(f"  {label}: {_outcome(lambda view=view: view.pets.get_pet(pet_id=pet), errors)}")
    exchange.respond(json_response(200, _PET), json_response(200, _PET))
    ended = api.with_options(options.RequestOptions(hooks=(Recorder(lines, "d", ("call_end",)),)))
    lines.append(f"  success ending in a failing hook: {_outcome(lambda: ended.pets.get_pet(pet_id=pet), errors)}")
    both = api.with_options(options.RequestOptions(hooks=(Recorder(lines, "l", ("attempt_end", "call_end")),)))
    lines.append(f"  success whose every end fails: {_outcome(lambda: both.pets.get_pet(pet_id=pet), errors)}")
    failed = api.with_options(options.RequestOptions(hooks=(Recorder(lines, "e", ("attempt_start",)),)))
    lines.append(f"  failure before sending: {_outcome(lambda: failed.pets.get_pet(pet_id=pet), errors)}")
    exchange.respond(json_response(200, _PET))
    record(
        lines,
        "hooks replaced by none",
        lambda: api.with_options(options.RequestOptions(hooks=())).pets.get_pet(pet_id=pet),
    )


def _outcome(call: Callable[[], object], errors: ModuleType) -> str:
    """Keep a successful native result beside a terminal hook failure."""
    try:
        call()
    except errors.SDKError as error:
        completed = error.completed_result
        result = getattr(completed, "data", None)
        causes = [type(item).__name__ for item in error.secondary_errors]
        return (
            f"{error} delivery={error.delivery_state.value} cause={error.cause} secondary={causes} completed={result!r}"
        )
    return "no error"


def _raw(api: Any, exchange: Exchange, lines: list[str], options: ModuleType, pet: object) -> None:
    """End raw calls once their responses are buffered or handed over, and close a handle a failing hook keeps."""
    exchange.respond(json_response(200, _PET), raw_response(200, b"ok", "text/plain"), json_response(200, _PET))
    record(lines, "raw get", lambda: api.pets.with_raw_response.get_pet(pet_id=pet).info.status_code)
    record(lines, "raw request", lambda: api.request_raw("GET", _RAW).body_bytes)
    with api.pets.with_streaming_response.get_pet(pet_id=pet) as streamed:
        record(lines, "streamed get", streamed.read)
    exchange.respond(json_response(200, _PET))
    kept = api.with_options(options.RequestOptions(hooks=(Recorder(lines, "f", ("call_end",)),)))
    record(
        lines,
        "streamed get whose hook fails",
        lambda: kept.pets.with_streaming_response.get_pet(pet_id=pet).__enter__(),
    )
    exchange.respond(json_response(200, _PET))
    record(lines, "raw get after it", lambda: api.pets.with_raw_response.get_pet(pet_id=pet).info.status_code)
    _streams(api, exchange, lines, options, pet)
    trace = object()
    record(lines, "raw list of a trace its codec refuses", lambda: api.pets.with_raw_response.list_pets(x_trace=trace))
    record(lines, "raw request to no URL", lambda: api.request_raw("GET", "not a url"))
    exchange.respond(_refusing)
    record(lines, "raw get of a refused connection", lambda: api.pets.with_raw_response.get_pet(pet_id=pet))


def _streams(api: Any, exchange: Exchange, lines: list[str], options: ModuleType, pet: object) -> None:
    """End each handed-over stream once: read to its end, iterated, closed early, or failed, whichever hook fails."""
    exchange.respond(json_response(200, _PET), json_response(200, _PET), json_response(200, _PET))
    with api.pets.with_streaming_response.get_pet(pet_id=pet) as early:
        lines.append(f"  stream closed early: {early.info.status_code}")
    with api.pets.with_streaming_response.get_pet(pet_id=pet) as interrupted:
        interrupted.discard(KeyboardInterrupt())
        lines.append("  stream discarded for an interruption")
    with api.pets.with_streaming_response.get_pet(pet_id=pet) as iterated:
        record(lines, "stream iterated", lambda: b"".join(iterated.iter_bytes()))
    exchange.respond(_interrupted_close)
    with api.pets.with_streaming_response.get_pet(pet_id=pet) as interrupting:
        try:
            interrupting.close()
        except KeyboardInterrupt:
            lines.append("  stream whose close is interrupted: KeyboardInterrupt")
    ending = api.with_options(options.RequestOptions(hooks=(Recorder(lines, "n", ("stream_end",)),)))
    exchange.respond(json_response(200, _PET), json_response(200, _PET), broken)
    with ending.pets.with_streaming_response.get_pet(pet_id=pet) as read:
        record(lines, "stream whose end fails on reading it", read.read)
    with ending.pets.with_streaming_response.get_pet(pet_id=pet) as closing:
        record(lines, "stream whose end fails on closing it", closing.close)
    with ending.pets.with_streaming_response.get_pet(pet_id=pet) as failed:
        record(lines, "stream failing whose end fails too", failed.read)
    exchange.respond(raw_response(404))
    with ending.pets.with_streaming_response.get_pet(pet_id=pet) as missing:
        lines.append(f"  stream of an error whose end fails: {outcome(missing.raise_for_status)}")


def _refused(package: ModuleType, lines: list[str], options: ModuleType) -> None:
    """Refuse hooks that are none, contexts of other values or over 8 KiB, and an async hook on a sync client."""
    for label, arguments in (
        ("hooks that are no sequence", {"hooks": Recorder(lines, "x")}),
        ("hook without on_event", {"hooks": (object(),)}),
        ("context that is no mapping", {"context": [("a", 1)]}),
        ("context of a value that is no scalar", {"context": {"a": [1]}}),
        ("context of a name that is no string", {"context": {1: "a"}}),
        ("context of an infinite number", {"context": {"a": float("inf")}}),
        ("context over 8 KiB", {"context": {"a": "x" * 8192}}),
    ):
        record(lines, label, lambda arguments=arguments: options.RequestOptions(**arguments))
    merged = options.ClientOptions(context={"a": "x" * 4096})
    http = Exchange(lines).client()
    with package.Client(http_client=http, options=merged) as api:
        record(
            lines,
            "context merged over 8 KiB",
            lambda: api.with_options(options.RequestOptions(context={"b": "y" * 4096})),
        )
        asynchronous = api.with_options(options.RequestOptions(hooks=(AsyncRecorder(lines, "z"),)))
        record(lines, "async hook on a sync client", lambda: asynchronous.request_raw("GET", _RAW))
    http.close()


async def _async_hooks(package: ModuleType, lines: list[str]) -> None:
    options, errors, types = _modules(package)
    exchange = Exchange(lines)
    http = exchange.async_client()
    pet = argument(package, "getPet", "path", "petId", 3)
    watched = options.ClientOptions(hooks=(AsyncRecorder(lines, "g"), Recorder(lines, "h")))
    async with package.AsyncClient(http_client=http, options=watched) as api:
        exchange.respond(json_response(200, _PET), raw_response(200, b"ok", "text/plain"))
        await arecord(lines, "async get", lambda: api.pets.get_pet(pet_id=pet))

        async def raw_call() -> bytes:
            return (await api.request_raw("GET", _RAW)).body_bytes

        await arecord(lines, "async raw request", raw_call)
        failing = api.with_options(options.RequestOptions(hooks=(AsyncRecorder(lines, "i", ("attempt_start",)),)))
        await arecord(lines, "async hook failing before sending", lambda: failing.pets.get_pet(pet_id=pet))
        exchange.respond(json_response(200, _PET))
        every = ("response_headers", "attempt_end", "call_end")
        noisy = api.with_options(options.RequestOptions(hooks=(AsyncRecorder(lines, "m", every),)))
        await arecord(lines, "async hook failing on headers and every end", lambda: noisy.pets.get_pet(pet_id=pet))
        await arecord(
            lines,
            "async raw list of a trace its codec refuses",
            lambda: api.pets.with_raw_response.list_pets(x_trace=object()),
        )
        await arecord(lines, "async raw request to no URL", lambda: api.request_raw("GET", "not a url"))
        exchange.respond(_refusing)
        await arecord(
            lines, "async raw get of a refused connection", lambda: api.pets.with_raw_response.get_pet(pet_id=pet)
        )
        exchange.respond(_cancelling)
        try:
            await api.pets.get_pet(pet_id=pet)
        except asyncio.CancelledError:
            lines.append("  async get cancelled: CancelledError")
        exchange.respond(json_response(200, _PET), json_response(200, _PET))
        for label, failing_ends in (
            ("a failing hook", ("call_end",)),
            ("every end failing", ("attempt_end", "call_end")),
        ):
            ended = api.with_options(options.RequestOptions(hooks=(AsyncRecorder(lines, "j", failing_ends),)))
            try:
                await ended.pets.get_pet(pet_id=pet)
            except errors.SDKError as error:
                causes = [type(item).__name__ for item in error.secondary_errors]
                completed = error.completed_result
                data = None if completed is None else completed.data
                lines.append(f"  async success ending in {label}: {error} {causes} {data!r}")
        exchange.respond(json_response(200, _PET))
        kept = api.with_options(options.RequestOptions(hooks=(AsyncRecorder(lines, "k", ("call_end",)),)))
        await arecord(lines, "async raw get whose hook fails", lambda: kept.pets.with_raw_response.get_pet(pet_id=pet))
        exchange.respond(json_response(200, _PET), json_response(200, _PET), json_response(200, _PET), abroken)
        async with api.pets.with_streaming_response.get_pet(pet_id=pet) as early:
            lines.append(f"  async stream closed early: {early.info.status_code}")
        async with api.pets.with_streaming_response.get_pet(pet_id=pet) as read:
            await arecord(lines, "async stream read", read.read)
        ending = api.with_options(options.RequestOptions(hooks=(AsyncRecorder(lines, "o", ("stream_end",)),)))
        async with ending.pets.with_streaming_response.get_pet(pet_id=pet) as last:
            await arecord(lines, "async stream whose end fails on reading it", last.read)
        async with ending.pets.with_streaming_response.get_pet(pet_id=pet) as failed:
            await arecord(lines, "async stream failing whose end fails too", failed.read)
        exchange.respond(raw_response(404))
        async with ending.pets.with_streaming_response.get_pet(pet_id=pet) as missing:
            lines.append(f"  async stream of an error whose end fails: {await aoutcome(missing.raise_for_status)}")
        exchange.respond(_interrupted_close)
        async with api.pets.with_streaming_response.get_pet(pet_id=pet) as cancelling:
            try:
                await cancelling.aclose()
            except asyncio.CancelledError:
                lines.append("  async stream whose close is cancelled: CancelledError")
        exchange.respond(json_response(200, _PET))

        async def raw_get() -> int:
            return (await api.pets.with_raw_response.get_pet(pet_id=pet)).info.status_code

        await arecord(lines, "async raw get after it", raw_get)
    await http.aclose()
