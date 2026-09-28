"""Read raw and streaming responses of generated clients: saved bodies, one-time streams, limits, files, and closing."""

from __future__ import annotations

import asyncio
import errno
import gzip
import importlib
import io
import os
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NoReturn

import httpx2
import pytest

from tests.data.python.client_runtime import Exchange, arecord, failing, record, run
from tests.data.python.client_transports import Adapter, AsyncAdapter, AsyncResponse, Response, Stop

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from types import ModuleType

_PET: Final = b'{"id":3,"name":"fox"}'
_ERROR: Final = b'{"code":7,"message":"boom"}'
_JSON: Final = "application/json"


def _modules(package: ModuleType) -> tuple[ModuleType, ModuleType, ModuleType, ModuleType, ModuleType]:
    transports, errors, options, responses, types = (
        importlib.import_module(f"{package.__name__}.{name}")
        for name in ("transports", "errors", "options", "responses", "types.pets")
    )
    return transports, errors, options, responses, types


def _pet(package: ModuleType) -> object:
    return _modules(package)[4].GetPetRequestCodecs.parameter(location="path", name="petId").from_wire(3)


def _saved(response: Any) -> str:
    """Describe a response whose body is in memory: its status, media type, and both forms of its body."""
    info = response.info
    decoded, coded = response.body_bytes, response.raw_body_bytes
    return f"{info.status_code} {info.content_type!r} {decoded!r} {coded!r} shared {decoded is coded}"


def _outcome(call: Callable[[], object]) -> str:
    """Report the class of a call's failure with the classes of its secondary errors, or its result."""
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        return f"{type(error).__name__} secondary {[type(item).__name__ for item in getattr(error, 'secondary_errors', ())]}"
    return f"returned {result!r}"


async def _aoutcome(call: Callable[[], Any]) -> str:
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        return f"{type(error).__name__} secondary {[type(item).__name__ for item in getattr(error, 'secondary_errors', ())]}"
    return f"returned {result!r}"


def _files(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def _broken_link(source: object, target: object) -> NoReturn:
    """Fail to give a completed download its name, as a full disk or a lost volume would."""
    del source, target
    raise OSError(errno.EIO, "The link failed")


class _Stream(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    """Yield chunks, running an action before the chunk at its position, and fail at the end when asked."""

    def __init__(self, chunks: tuple[bytes, ...], *, fail: bool = False, action: Callable[[], object] | None = None) -> None:
        self.chunks = chunks
        self.fail = fail
        self.action = action

    def __iter__(self) -> Iterator[bytes]:
        for index, chunk in enumerate(self.chunks):
            if index and self.action is not None:
                self.action()
            yield chunk
        if self.fail:
            msg = "connection reset"
            raise httpx2.ReadError(msg)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.__iter__():
            yield chunk


def _streamed(status: int, chunks: tuple[bytes, ...], media: str = _JSON, **options: Any) -> Callable[[httpx2.Request], httpx2.Response]:
    headers = {"content-type": media, **options.pop("headers", {})}
    return lambda request: httpx2.Response(status, headers=headers, stream=_Stream(chunks, **options))


def _gzip(status: int, content: bytes, size: int, media: str = _JSON) -> Callable[[httpx2.Request], httpx2.Response]:
    coded = gzip.compress(content, mtime=0)
    chunks = tuple(coded[start : start + size] for start in range(0, len(coded), size))
    return _streamed(status, chunks, media, headers={"content-encoding": "gzip"})


def _large(count: int) -> Callable[[httpx2.Request], httpx2.Response]:
    return _streamed(200, (bytes(65536),) * count, "application/octet-stream")


def raw(package: ModuleType, lines: list[str]) -> None:
    """Read raw responses saved and streaming, synchronously and with asyncio, through HTTPX2 and adapters."""
    _, _, options, _, _ = _modules(package)
    exchange = Exchange(lines)
    http = httpx2.Client(transport=httpx2.MockTransport(exchange.handle))
    with tempfile.TemporaryDirectory() as directory, package.Client(http_client=http) as api:
        _saved_responses(package, api, exchange, lines)
        _streaming(package, api, exchange, lines)
        _status(package, api, exchange, lines)
        _download(package, api, exchange, lines, Path(directory))
        _requests(package, api, exchange, lines)
        record(lines, "streaming view options", lambda: api.with_options(options.RequestOptions(max_stream_bytes=-1)))
    http.close()
    _handles(package, lines)
    _adapters(package, lines)
    run(lambda: _async_raw(package, lines))


def _saved_responses(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Keep the body of a raw response in memory, both decoded and as it arrived, within the buffer limit."""
    _, _, options, _, types = _modules(package)
    pet, raw = _pet(package), api.pets.with_raw_response
    exchange.respond(_streamed(200, (_PET[:8], _PET[8:])))
    kept = raw.get_pet(pet_id=pet)
    lines.append(f"  saved {_saved(kept)}")
    pieces, coded = list(kept.iter_bytes()), list(kept.iter_raw_bytes())
    lines.append(f"    read {kept.read()!r} text {kept.text()!r} json {kept.json()!r} pieces {pieces} {coded}")
    kept.close()
    with kept as same:
        lines.append(f"    closed twice {same.read() == _PET} {same.raise_for_status()} {same is kept}")
    exchange.respond(_gzip(200, _PET, 7))
    lines.append(f"  saved gzip {_saved(raw.get_pet(pet_id=pet))}")
    exchange.respond(_streamed(200, (bytes(70000),), "application/octet-stream"))
    large = raw.get_pet(pet_id=pet, options=options.RequestOptions(max_response_bytes=None))
    lines.append(f"  saved pieces {[len(piece) for piece in large.iter_bytes()]}")
    trace = types.ListPetsRequestCodecs.parameter(location="header", name="X-Trace").from_wire("t")
    exchange.respond(_streamed(418, (_ERROR,), headers={"x-request-id": "r-1"}))
    listed = raw.list_pets(x_trace=trace)
    lines.append(f"  saved error {_saved(listed)} {listed.info.request_id}")
    record(lines, "saved error status", listed.raise_for_status)
    record(lines, "saved error status again", listed.raise_for_status)
    exchange.respond(_streamed(500, (b"0123456789",), "text/plain"))
    record(lines, "saved truncated status", raw.get_pet(pet_id=pet, options=options.RequestOptions(max_error_body_bytes=4)).raise_for_status)
    exchange.respond(_streamed(302, (), "text/plain", headers={"location": "https://api.example.com/v1/pets/4"}))
    record(lines, "saved redirect status", raw.get_pet(pet_id=pet).raise_for_status)
    for label, content, media in (
        ("undecodable text", b"\xff\xfe", "text/plain; charset=utf-8"),
        ("latin-1 text", "café".encode("latin-1"), "text/plain; charset=latin-1"),
        ("invalid json", b"{", _JSON),
    ):
        exchange.respond(_streamed(200, (content,), media))
        received = raw.get_pet(pet_id=pet)
        record(lines, f"saved {label} text", received.text)
        record(lines, f"saved {label} json", received.json)
    small = options.RequestOptions(max_response_bytes=8)
    for label, responder, limit in (
        ("identity", _streamed(200, (_PET[:8], _PET[8:])), small),
        ("coded", _gzip(200, bytes(100), 100), small),
        ("expanded", _gzip(200, bytes(100), 100), options.RequestOptions(max_response_bytes=50)),
        ("broken", _streamed(200, (_PET[:8],), fail=True), None),
        ("connect", failing(httpx2.ConnectError), None),
    ):
        exchange.respond(responder)
        record(lines, f"saved {label}", lambda limit=limit: raw.get_pet(pet_id=pet, options=limit))
    record(lines, "saved options", lambda: raw.get_pet(pet_id=pet, options="fast"))


def _streaming(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Read a streaming body once: by iterating it, or into memory first, and refuse any second reading."""
    _, _, options, _, _ = _modules(package)
    pet, streaming = _pet(package), api.pets.with_streaming_response
    exchange.respond(_streamed(200, (_PET[:8], _PET[8:])))
    with streaming.get_pet(pet_id=pet) as response:
        lines.append(f"  streamed {list(response.iter_bytes())} status {response.raise_for_status()}")
        for label, action in (
            ("read", response.read),
            ("text", response.text),
            ("json", response.json),
            ("iter_bytes", response.iter_bytes),
            ("iter_raw_bytes", response.iter_raw_bytes),
            ("body_bytes", lambda: response.body_bytes),
            ("raw_body_bytes", lambda: response.raw_body_bytes),
        ):
            record(lines, f"consumed {label}", action)
    exchange.respond(_gzip(200, _PET, 7))
    with streaming.get_pet(pet_id=pet) as response:
        record(lines, "unread body_bytes", lambda: response.body_bytes)
        lines.append(f"  read first {response.read()!r} {_saved(response)} {list(response.iter_raw_bytes())}")
    exchange.respond(_gzip(200, _PET, 7))
    with streaming.get_pet(pet_id=pet) as response:
        lines.append(f"  streamed coded {list(response.iter_raw_bytes())}")
    exchange.respond(_streamed(200, (_PET[:8], _PET[8:15], _PET[15:])))
    with streaming.get_pet(pet_id=pet) as response:
        chunks = response.iter_bytes()
        lines.append(f"  streamed first {next(chunks)!r}")
        record(lines, "streaming read", response.read)
        response.close()
        record(lines, "closed read", response.read)
    exchange.respond(_large(1600))
    with streaming.get_pet(pet_id=pet) as response:
        lines.append(f"  streamed 100 MiB {sum(len(chunk) for chunk in response.iter_bytes())}")
    capped = api.with_options(options.RequestOptions(max_stream_bytes=10)).pets.with_streaming_response
    for label, responder, decoded in (
        ("identity", _streamed(200, (_PET[:8], _PET[8:])), True),
        ("gzip decoded", _gzip(200, _PET, 8), True),
        ("gzip coded", _gzip(200, _PET, 8), False),
        ("broken", _streamed(200, (_PET[:8],), fail=True), True),
    ):
        exchange.respond(responder)
        with capped.get_pet(pet_id=pet) as response:
            received: list[bytes] = []
            record(lines, f"capped {label}", lambda response=response, decoded=decoded, received=received: received.extend(response.iter_bytes() if decoded else response.iter_raw_bytes()))
            lines.append(f"    passed {received}")
            record(lines, f"capped {label} read", response.read)
    exchange.respond(_streamed(200, (_PET,)))
    try:
        with streaming.get_pet(pet_id=pet) as response:
            raise Stop
    except Stop:
        record(lines, "stopped block read", response.read)
    exchange.respond(failing(httpx2.ConnectError))
    record(lines, "streaming connect", lambda: _entered(streaming.get_pet(pet_id=pet)))
    record(lines, "streaming options", lambda: _entered(streaming.get_pet(pet_id=pet, options="fast")))


def _entered(manager: Any) -> object:
    with manager as response:
        return response.info.status_code


def _status(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Raise the typed failure of a streaming response from the prefix of its error body, then close it."""
    _, _, options, _, _ = _modules(package)
    pet, streaming = _pet(package), api.pets.with_streaming_response
    short = api.with_options(options.RequestOptions(max_error_body_bytes=4)).pets.with_streaming_response
    for label, view, responder in (
        ("error", streaming, _streamed(404, (b"miss", b"ing"), "text/plain")),
        ("truncated", short, _streamed(500, (b"012", b"345", b"678"), "text/plain")),
        ("broken coding", streaming, _streamed(500, (b"not gzip",), "text/plain", headers={"content-encoding": "gzip"})),
        ("broken read", streaming, _streamed(500, (b"012",), "text/plain", fail=True)),
    ):
        exchange.respond(responder)
        with view.get_pet(pet_id=pet) as response:
            record(lines, f"streaming {label} status", response.raise_for_status)
            record(lines, f"streaming {label} read", response.read)
    exchange.respond(_streamed(404, (b"miss", b"ing"), "text/plain"))
    with streaming.get_pet(pet_id=pet) as response:
        next(response.iter_raw_bytes())
        record(lines, "partly read status", response.raise_for_status)


def _download(package: ModuleType, api: Any, exchange: Exchange, lines: list[str], directory: Path) -> None:
    """Write a body to a borrowed file object, or to a path through a temporary file that success moves there."""
    pet, streaming = _pet(package), api.pets.with_streaming_response
    target, raced = directory / "pet.json", directory / "raced.json"
    sink = io.BytesIO()
    exchange.respond(_streamed(200, (_PET[:8], _PET[8:])), _streamed(200, (_PET[:8], _PET[8:])))
    with streaming.get_pet(pet_id=pet) as response:
        response.stream_to(sink)
    with streaming.get_pet(pet_id=pet) as response:
        response.stream_to(target)
    plain = directory / "plain.json"
    plain.write_bytes(b"")
    permissions = stat.S_IMODE(target.stat().st_mode) == stat.S_IMODE(plain.stat().st_mode)
    plain.unlink()
    lines.append(f"  downloaded {sink.getvalue()!r} open {not sink.closed} {target.read_bytes()!r} {_files(directory)}")
    lines.append(f"  downloaded with the permissions of a new file {permissions}")
    exchange.respond(_streamed(200, (b"{}",)), _streamed(200, (b"[]",)), _streamed(200, (b"{", b"}"), fail=True))
    with streaming.get_pet(pet_id=pet) as response:
        lines.append(f"  existing {_outcome(lambda: response.stream_to(target))} then read {response.read()!r}")
    with streaming.get_pet(pet_id=pet) as response:
        response.stream_to(str(target), overwrite=True)
    with streaming.get_pet(pet_id=pet) as response:
        lines.append(f"  overwrite broken {_outcome(lambda: response.stream_to(target, overwrite=True))}")
    lines.append(f"  kept {target.read_bytes()!r} {_files(directory)}")
    exchange.respond(_streamed(200, (b"{", b"}"), action=lambda: raced.write_bytes(b"first")))
    with streaming.get_pet(pet_id=pet) as response:
        lines.append(f"  raced {_outcome(lambda: response.stream_to(raced))}")
    lines.append(f"  raced kept {raced.read_bytes()!r} {_files(directory)}")
    failed = directory / "failed.json"
    exchange.respond(_streamed(200, (b"{}",)), _streamed(200, (b"{}",)))
    with streaming.get_pet(pet_id=pet) as response, pytest.MonkeyPatch.context() as fault:
        fault.setattr(os, "link", _broken_link)
        lines.append(f"  move failed {_outcome(lambda: response.stream_to(failed))} {_files(directory)}")
    with streaming.get_pet(pet_id=pet) as response:
        response.stream_to(failed)
    lines.append(f"  moved again {failed.read_bytes()!r} {_files(directory)}")


def _requests(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send raw requests to any absolute URL, saved or streaming, and refuse invalid ones before sending."""
    _, _, options, _, _ = _modules(package)
    exchange.respond(
        _streamed(200, (b"pong",), "text/plain"), _streamed(503, (b"down",), "text/plain"), _streamed(200, (b"str", b"eam"))
    )
    lines.append(f"  request {_saved(api.request_raw('POST', 'https://hooks.example.com/ping?x=1', body=b'ping'))}")
    record(lines, "request status", api.request_raw("GET", "https://hooks.example.com/down").raise_for_status)
    with api.with_streaming_response.request_raw("GET", "https://hooks.example.com/stream") as response:
        lines.append(f"  request streamed {list(response.iter_bytes())}")
    for label, method, url, body, settings in (
        ("method", "GE T", "https://x.example.com", b"", None),
        ("method type", 1, "https://x.example.com", b"", None),
        ("relative url", "GET", "/pets", b"", None),
        ("url scheme", "GET", "ftp://x.example.com", b"", None),
        ("url fragment", "GET", "https://x.example.com/#top", b"", None),
        ("query fragment", "GET", "https://x.example.com/?a=1#top", b"", None),
        ("url userinfo", "GET", "https://user@x.example.com", b"", None),
        ("url port", "GET", "https://x.example.com:99999", b"", None),
        ("url type", "GET", b"https://x.example.com", b"", None),
        ("body type", "POST", "https://x.example.com", "text", None),
        ("options type", "GET", "https://x.example.com", b"", "fast"),
    ):
        record(lines, f"request {label}", lambda method=method, url=url, body=body, settings=settings: api.request_raw(method, url, body=body, options=settings))
    view = api.with_options(options.RequestOptions(max_stream_bytes=3)).with_streaming_response
    exchange.respond(_streamed(200, (b"str", b"eam")))
    with view.request_raw("GET", "https://hooks.example.com/stream") as response:
        record(lines, "request capped", lambda: list(response.iter_bytes()))
    record(lines, "request streaming options", lambda: _entered(view.request_raw("GET", "https://x.example.com", options="fast")))


def _until_closing(api: Any, pet: object, errors: ModuleType) -> None:
    """Wait until the client refuses calls, probing with a call that only fails before sending while it is open."""
    deadline = time.monotonic() + 30
    while True:
        try:
            api.pets.get_pet(pet_id=pet, options="refused only once closing")
        except errors.ClientClosedError:
            return
        except errors.ConfigurationError:
            if time.monotonic() > deadline:
                msg = "The client never started closing"
                raise TimeoutError(msg) from None


def _handles(package: ModuleType, lines: list[str]) -> None:
    """Count streaming handles as leases that closing waits for, then closes, and keep saved responses readable."""
    transports, errors, options, responses, _ = _modules(package)
    pet, json = _pet(package), responses.HeadersView([("content-type", _JSON)])
    adapter = Adapter(transports, lines)
    api = package.Client(transport_adapter=adapter, options=options.ClientOptions(cleanup_timeout=0.05))
    adapter.replies.extend(
        lambda request, context: Response(lines, 200, json, (_PET[:5], _PET[5:])) for _ in range(4)
    )
    saved = api.pets.with_raw_response.get_pet(pet_id=pet)
    view = api.with_options(options.RequestOptions())
    with view.pets.with_streaming_response.get_pet(pet_id=pet) as held:
        record(lines, "view close with a handle", view.close)
        record(lines, "handle after view close", held.read)
    with api.pets.with_streaming_response.get_pet(pet_id=pet) as held:
        chunks = held.iter_bytes()
        lines.append(f"  handle first chunk {next(chunks)!r}")
        record(lines, "close with a handle", api.close)
        record(lines, "handle after close next chunk", lambda: next(chunks))
        for label, action in (("read", held.read), ("status", held.raise_for_status), ("iterate", held.iter_bytes)):
            record(lines, f"handle after close {label}", action)
        record(lines, "handle close after close", held.close)
    lines.append(f"  saved after close {saved.read()!r}")
    record(lines, "close again", api.close)
    record(lines, "handle after closed", held.read)
    waiting = package.Client(transport_adapter=adapter, options=options.ClientOptions(cleanup_timeout=30.0))
    outcome: list[str] = []
    with waiting.pets.with_streaming_response.get_pet(pet_id=pet) as held:
        closer = threading.Thread(target=lambda: record(outcome, "close", waiting.close))
        closer.start()
        _until_closing(waiting, pet, errors)
        record(lines, "live handle while closing", lambda: list(held.iter_bytes()))
        record(lines, "live handle status while closing", held.raise_for_status)
    closer.join()
    lines.extend(outcome)
    failing_close = Adapter(transports, lines)
    api = package.Client(transport_adapter=failing_close, options=options.ClientOptions(cleanup_timeout=0.05))
    failing_close.replies.extend((
        lambda request, context: Response(lines, 200, json, (_PET,), close_error=True),
        lambda request, context: Response(lines, 200, json, (_PET,), close_error=True),
        lambda request, context: Response(lines, 200, json, (b"{", RuntimeError("read bug")), close_error=True),
        lambda request, context: Response(lines, 200, json, (_PET[:5], _PET[5:]), close_error=True),
        lambda request, context: Response(lines, 200, json, (_PET,), close_error=True),
    ))
    record(lines, "handle close failure", lambda: _entered(api.pets.with_streaming_response.get_pet(pet_id=pet)))
    record(lines, "saved close failure", lambda: api.pets.with_raw_response.get_pet(pet_id=pet))
    lines.append(f"  read and close failures {_outcome(lambda: _read_in_block(api, pet))}")
    lines.append(f"  refused read and close failure {_outcome(lambda: _switch_in_block(api, pet))}")
    with api.pets.with_streaming_response.get_pet(pet_id=pet) as held:
        lines.append(f"  close with a failing handle {_outcome(api.close)}")


def _read_in_block(api: Any, pet: object) -> object:
    with api.pets.with_streaming_response.get_pet(pet_id=pet) as response:
        return response.read()


def _switch_in_block(api: Any, pet: object) -> object:
    with api.pets.with_streaming_response.get_pet(pet_id=pet) as response:
        next(response.iter_bytes())
        return response.read()


def _adapters(package: ModuleType, lines: list[str]) -> None:
    """Check what an adapter returns for a raw call: its status, headers, and chunks, and interruptions."""
    transports, _, _, responses, _ = _modules(package)
    pet, json = _pet(package), responses.HeadersView([("content-type", _JSON)])
    adapter = Adapter(transports, lines)
    with package.Client(transport_adapter=adapter) as api:
        adapter.replies.extend((
            lambda request, context: Response(lines, 200, json, (b"", _PET)),
            lambda request, context: Response(lines, 200, json, (_PET[:5], "text")),
            lambda request, context: Response(lines, "200", json, ()),
            lambda request, context: _Interrupted(),
            lambda request, context: Response(lines, 200, json, (b"{", Stop())),
        ))
        lines.append(f"  adapter saved {_saved(api.pets.with_raw_response.get_pet(pet_id=pet))}")
        with api.pets.with_streaming_response.get_pet(pet_id=pet) as response:
            record(lines, "adapter chunk type", lambda: list(response.iter_bytes()))
        record(lines, "adapter status type", lambda: api.pets.with_raw_response.get_pet(pet_id=pet))
        for label in ("before the headers", "while reading a saved body"):
            try:
                api.pets.with_raw_response.get_pet(pet_id=pet)
            except Stop:
                lines.append(f"  adapter stop {label} propagated")
        adapter.replies.append(_raise(Stop()))
        try:
            api.request_raw("GET", "https://x.example.com")
        except Stop:
            lines.append("  adapter stop while sending propagated")


class _Interrupted:
    """A response interrupted while its status is read."""

    headers = None

    @property
    def status_code(self) -> int:
        raise Stop

    def close(self) -> None:
        pass

    async def aclose(self) -> None:
        pass


def _raise(error: BaseException) -> Callable[[Any, Any], Any]:
    def reply(request: Any, context: Any) -> Any:
        raise error

    return reply


async def _async_raw(package: ModuleType, lines: list[str]) -> None:
    """Read raw responses of an asyncio client: the same saved, streaming, and closing behaviour."""
    _, _, options, _, _ = _modules(package)
    exchange = Exchange(lines)
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(exchange.ahandle))
    with tempfile.TemporaryDirectory() as directory:
        async with package.AsyncClient(http_client=http) as api:
            await _async_saved(package, api, exchange, lines)
            await _async_streaming(package, api, exchange, lines, Path(directory))
            await _async_requests(api, exchange, lines, options)
    await http.aclose()
    await _async_handles(package, lines)


async def _async_saved(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    _, _, options, _, _ = _modules(package)
    pet, raw = _pet(package), api.pets.with_raw_response
    exchange.respond(_streamed(200, (_PET[:8], _PET[8:])))
    kept = await raw.get_pet(pet_id=pet)
    pieces = [piece async for piece in kept.iter_bytes()]
    coded = [piece async for piece in kept.iter_raw_bytes()]
    lines.append(f"  async saved {_saved(kept)} {await kept.read()!r} {await kept.text()!r} {await kept.json()!r} {pieces} {coded}")
    await kept.aclose()
    async with kept as same:
        lines.append(f"    closed twice {await same.raise_for_status()} {same is kept}")
    exchange.respond(_gzip(200, _PET, 7))
    lines.append(f"  async saved gzip {_saved(await raw.get_pet(pet_id=pet))}")
    exchange.respond(_streamed(500, (b"0123456789",), "text/plain"))
    failed = await raw.get_pet(pet_id=pet, options=options.RequestOptions(max_error_body_bytes=4))
    await arecord(lines, "async saved status", failed.raise_for_status)
    for label, responder, limit in (
        ("coded", _gzip(200, bytes(100), 100), options.RequestOptions(max_response_bytes=8)),
        ("expanded", _gzip(200, bytes(100), 100), options.RequestOptions(max_response_bytes=50)),
        ("broken", _streamed(200, (_PET[:8],), fail=True), None),
        ("connect", failing(httpx2.ConnectError), None),
    ):
        exchange.respond(responder)
        await arecord(lines, f"async saved {label}", lambda limit=limit: raw.get_pet(pet_id=pet, options=limit))
    await arecord(lines, "async saved options", lambda: raw.get_pet(pet_id=pet, options="fast"))


async def _async_streaming(package: ModuleType, api: Any, exchange: Exchange, lines: list[str], directory: Path) -> None:
    _, _, options, _, _ = _modules(package)
    pet, streaming = _pet(package), api.pets.with_streaming_response
    exchange.respond(_streamed(200, (_PET[:8], _PET[8:])))
    async with streaming.get_pet(pet_id=pet) as response:
        lines.append(f"  async streamed {[chunk async for chunk in response.iter_bytes()]}")
        for label, action in (("read", response.read), ("text", response.text), ("json", response.json)):
            await arecord(lines, f"async consumed {label}", action)
        record(lines, "async consumed iter_raw_bytes", response.iter_raw_bytes)
    exchange.respond(_gzip(200, _PET, 7), _gzip(200, _PET, 7))
    async with streaming.get_pet(pet_id=pet) as response:
        lines.append(f"  async read first {await response.read()!r} {_saved(response)}")
    async with streaming.get_pet(pet_id=pet) as response:
        lines.append(f"  async streamed coded {[chunk async for chunk in response.iter_raw_bytes()]}")
    capped = api.with_options(options.RequestOptions(max_stream_bytes=10)).pets.with_streaming_response
    for label, responder in (("identity", _streamed(200, (_PET[:8], _PET[8:]))), ("broken", _streamed(200, (_PET[:8],), fail=True))):
        exchange.respond(responder)
        async with capped.get_pet(pet_id=pet) as response:
            await arecord(lines, f"async capped {label}", lambda response=response: _alist(response.iter_bytes()))
    short = api.with_options(options.RequestOptions(max_error_body_bytes=4)).pets.with_streaming_response
    for label, view, responder in (
        ("error", streaming, _streamed(404, (b"miss", b"ing"), "text/plain")),
        ("truncated", short, _streamed(500, (b"012", b"345", b"678"), "text/plain")),
        ("broken coding", streaming, _streamed(500, (b"not gzip",), "text/plain", headers={"content-encoding": "gzip"})),
        ("broken read", streaming, _streamed(500, (b"012",), "text/plain", fail=True)),
    ):
        exchange.respond(responder)
        async with view.get_pet(pet_id=pet) as response:
            await arecord(lines, f"async streaming {label} status", response.raise_for_status)
    exchange.respond(_streamed(404, (b"miss", b"ing"), "text/plain"))
    async with streaming.get_pet(pet_id=pet) as response:
        await anext(response.iter_raw_bytes())
        await arecord(lines, "async partly read status", response.raise_for_status)
    exchange.respond(_streamed(200, (_PET,)))
    try:
        async with streaming.get_pet(pet_id=pet) as response:
            raise Stop
    except Stop:
        await arecord(lines, "async stopped block read", response.read)
    await _async_download(streaming, pet, exchange, lines, directory)


async def _alist(chunks: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in chunks]


async def _async_download(streaming: Any, pet: object, exchange: Exchange, lines: list[str], directory: Path) -> None:
    target, raced, sink = directory / "pet.json", directory / "raced.json", io.BytesIO()
    exchange.respond(
        _streamed(200, (_PET[:8], _PET[8:])),
        _streamed(200, (_PET[:8], _PET[8:])),
        _streamed(200, (b"{}",)),
        _streamed(200, (b"{", b"}"), fail=True),
        _streamed(200, (b"{", b"}"), action=lambda: raced.write_bytes(b"first")),
    )
    async with streaming.get_pet(pet_id=pet) as response:
        await response.stream_to(sink)
    async with streaming.get_pet(pet_id=pet) as response:
        await response.stream_to(target)
    async with streaming.get_pet(pet_id=pet) as response:
        lines.append(f"  async existing {await _aoutcome(lambda: response.stream_to(target))} {await response.read()!r}")
    async with streaming.get_pet(pet_id=pet) as response:
        lines.append(f"  async overwrite broken {await _aoutcome(lambda: response.stream_to(target, overwrite=True))}")
    async with streaming.get_pet(pet_id=pet) as response:
        lines.append(f"  async raced {await _aoutcome(lambda: response.stream_to(raced))}")
    lines.append(f"  async downloaded {sink.getvalue()!r} {target.read_bytes()!r} {raced.read_bytes()!r} {_files(directory)}")
    failed = directory / "failed.json"
    exchange.respond(_streamed(200, (b"{}",)), _streamed(200, (b"{}",)))
    async with streaming.get_pet(pet_id=pet) as response:
        with pytest.MonkeyPatch.context() as fault:
            fault.setattr(os, "link", _broken_link)
            lines.append(f"  async move failed {await _aoutcome(lambda: response.stream_to(failed))} {_files(directory)}")
    async with streaming.get_pet(pet_id=pet) as response:
        await response.stream_to(failed)
    lines.append(f"  async moved again {failed.read_bytes()!r} {_files(directory)}")


async def _async_requests(api: Any, exchange: Exchange, lines: list[str], options: ModuleType) -> None:
    exchange.respond(_streamed(503, (b"down",), "text/plain"), _streamed(200, (b"str", b"eam")))
    failed = await api.request_raw("POST", "https://hooks.example.com/ping", body=b"ping")
    await arecord(lines, "async request status", failed.raise_for_status)
    async with api.with_streaming_response.request_raw("GET", "https://hooks.example.com/stream") as response:
        lines.append(f"  async request streamed {await _alist(response.iter_bytes())}")
    await arecord(lines, "async request url", lambda: api.request_raw("GET", "/pets"))
    view = api.with_options(options.RequestOptions()).with_streaming_response
    await arecord(lines, "async request streaming options", lambda: _aentered(view.request_raw("GET", "https://x.example.com", options="fast")))


async def _aentered(manager: Any) -> object:
    async with manager as response:
        return response.info.status_code


async def _async_handles(package: ModuleType, lines: list[str]) -> None:
    transports, _, options, responses, _ = _modules(package)
    pet, json = _pet(package), responses.HeadersView([("content-type", _JSON)])
    adapter = AsyncAdapter(transports, lines)
    api = package.AsyncClient(transport_adapter=adapter, options=options.ClientOptions(cleanup_timeout=30.0))
    adapter.replies.extend((
        lambda request, context: AsyncResponse(lines, 200, json, (_PET[:5], _PET[5:])),
        lambda request, context: AsyncResponse(lines, 200, json, (_PET[:5], "text")),
        lambda request, context: AsyncResponse(lines, "200", json, ()),
        lambda request, context: _Interrupted(),
        lambda request, context: AsyncResponse(lines, 200, json, (b"{", Stop())),
        lambda request, context: AsyncResponse(lines, 200, json, (_PET,), close_error=True),
        lambda request, context: AsyncResponse(lines, 200, json, (b"{", RuntimeError("read bug")), close_error=True),
        lambda request, context: AsyncResponse(lines, 200, json, (_PET[:5], _PET[5:]), close_error=True),
        lambda request, context: AsyncResponse(lines, 200, json, (_PET[:5], _PET[5:])),
        lambda request, context: AsyncResponse(lines, 200, json, (_PET[:5], _PET[5:]), close_error=True),
    ))
    saved = await api.pets.with_raw_response.get_pet(pet_id=pet)
    async with api.pets.with_streaming_response.get_pet(pet_id=pet) as held:
        await arecord(lines, "async adapter chunk type", lambda: _alist(held.iter_bytes()))
    await arecord(lines, "async adapter status type", lambda: api.pets.with_raw_response.get_pet(pet_id=pet))
    for label in ("before the headers", "while reading a saved body"):
        try:
            await api.pets.with_raw_response.get_pet(pet_id=pet)
        except Stop:
            lines.append(f"  async adapter stop {label} propagated")
    await arecord(lines, "async handle close failure", lambda: _aentered(api.pets.with_streaming_response.get_pet(pet_id=pet)))
    lines.append(f"  async read and close failures {await _aoutcome(lambda: _aread_in_block(api, pet))}")
    lines.append(f"  async refused read and close failure {await _aoutcome(lambda: _aswitch_in_block(api, pet))}")
    async with api.pets.with_streaming_response.get_pet(pet_id=pet) as held:
        closer = asyncio.ensure_future(_aoutcome(api.aclose))
        await asyncio.sleep(0)
        await arecord(lines, "async live handle while closing", held.read)
        await arecord(lines, "async live handle status while closing", held.raise_for_status)
    lines.append(f"  async close with a handle {await closer}")
    lines.append(f"  async saved after close {await saved.read()!r} {await _aoutcome(api.aclose)}")
    forced = package.AsyncClient(transport_adapter=adapter, options=options.ClientOptions(cleanup_timeout=0.05))
    async with forced.pets.with_streaming_response.get_pet(pet_id=pet) as held:
        chunks = held.iter_bytes()
        lines.append(f"  async handle first chunk {await anext(chunks)!r}")
        lines.append(f"  async close with a failing handle {await _aoutcome(forced.aclose)}")
        await arecord(lines, "async handle after forced close next chunk", lambda: anext(chunks))
        await arecord(lines, "async handle after forced close", held.read)


async def _aread_in_block(api: Any, pet: object) -> object:
    async with api.pets.with_streaming_response.get_pet(pet_id=pet) as response:
        return await response.read()


async def _aswitch_in_block(api: Any, pet: object) -> object:
    async with api.pets.with_streaming_response.get_pet(pet_id=pet) as response:
        await anext(response.iter_bytes())
        return await response.read()
