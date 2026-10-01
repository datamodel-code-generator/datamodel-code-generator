"""Keep connections and downloads only as long as their streams: borrowed pools, owned clients, and disk threads."""

from __future__ import annotations

import asyncio
import errno
import gzip
import importlib
import io
import os
import tempfile
import threading
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Final

import httpx2
import pytest

from tests.data.python.client_runtime import (
    Exchange,
    aoutcome,
    injected,
    json_response,
    outcome,
    raw_response,
    run,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
    from types import ModuleType

_CHUNK: Final = 65536
_DATA: Final = bytes(range(256)) * 4096
_ROUNDS: Final = 20
_URL: Final = "https://files.example.com/body"
_PET: Final = {"id": 3, "name": "fox"}
_WORKER: Final = "AsyncRawResponse"


class _Stalled(httpx2.SyncByteStream):
    """Send a first chunk, then hold the rest of the body until the scenario releases it."""

    def __init__(self, gate: threading.Event) -> None:
        self.gate = gate

    def __iter__(self) -> Iterator[bytes]:
        yield _DATA[:_CHUNK]
        self.gate.wait(10)
        yield _DATA[_CHUNK : 2 * _CHUNK]


class _Broken(httpx2.AsyncByteStream):
    """Send two chunks, then lose the connection; a slow disk's write is let go only after the second chunk is read."""

    def __init__(self, written: threading.Event) -> None:
        self.written = written

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield _DATA[:_CHUNK]
        asyncio.get_running_loop().call_soon(self.written.set)
        yield _DATA[_CHUNK : 2 * _CHUNK]
        msg = "connection reset"
        raise httpx2.ReadError(msg)


class _FullDisk(io.FileIO):
    """A slow download file on a full disk: it takes a number of writes once let go, then fails every later one."""

    def __init__(self, handle: int, room: int, written: threading.Event) -> None:
        super().__init__(handle, "wb")
        self.room = room
        self.written = written

    def write(self, data: Any) -> int:
        if not self.room:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        self.room -= 1
        self.written.wait(5)
        return super().write(data)


def _free() -> threading.Event:
    written = threading.Event()
    written.set()
    return written


class _Opening:
    """Open a download's file after an action, as a slow disk or an observer that cancels the call would."""

    def __init__(self, action: Callable[[], object]) -> None:
        self.action = action
        self.opened: list[BinaryIO] = []
        self.fdopen = os.fdopen

    def __call__(self, handle: int, mode: str) -> BinaryIO:
        self.action()
        self.opened.append(file := self.fdopen(handle, mode))
        return file


class _Gate:
    """Hold the disk thread inside an operation until the scenario lets it go."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.released = threading.Event()

    def hold(self) -> None:
        self.entered.set()
        self.released.wait(5)

    async def reached(self) -> None:
        for _ in range(500):
            if self.entered.is_set():
                return
            await asyncio.sleep(0.01)


def _denied(path: Path, *, missing_ok: bool = False) -> None:
    raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))


class _Suspending:
    """Suspend in the stream's end event, between releasing its connection and its later cleanup."""

    async def on_event(self, event: Any) -> None:
        if event.name == "stream_end":
            await asyncio.sleep(0.05)


class _Watched(httpx2.AsyncByteStream):
    """Pass a body through, setting an event once its reader has taken the first chunk and asks for more."""

    def __init__(self, stream: httpx2.AsyncByteStream, reading: asyncio.Event) -> None:
        self.stream = stream
        self.reading = reading

    async def __aiter__(self) -> AsyncIterator[bytes]:
        taken, chunks = 0, aiter(self.stream)
        try:
            async for chunk in chunks:
                yield chunk
                taken += len(chunk)
                if taken >= _CHUNK:
                    self.reading.set()
        finally:
            await chunks.aclose()

    async def aclose(self) -> None:
        await self.stream.aclose()


class _Reading(httpx2.AsyncClient):
    """Watch the body of each response, so a scenario stops a download only once it is past its first chunk."""

    reading: asyncio.Event

    async def send(self, request: httpx2.Request, **options: Any) -> httpx2.Response:
        response = await super().send(request, **options)
        self.reading = asyncio.Event()
        response.stream = _Watched(response.stream, self.reading)
        return response


class _Owned(httpx2.Client):
    """Count how often the client that owns this HTTPX2 client closes it."""

    closes = 0

    def close(self) -> None:
        self.closes += 1
        super().close()


class _AsyncOwned(httpx2.AsyncClient):
    """Count how often the asyncio client that owns this HTTPX2 client closes it."""

    closes = 0

    async def aclose(self) -> None:
        self.closes += 1
        await super().aclose()


def _modules(package: ModuleType) -> tuple[ModuleType, ModuleType]:
    return importlib.import_module(f"{package.__name__}.options"), importlib.import_module(
        f"{package.__name__}.types.pets"
    )


def _pet(package: ModuleType) -> object:
    return _modules(package)[1].GetPetRequestCodecs.parameter(location="path", name="petId").from_wire(3)


def _body(size: int = 3 * _CHUNK) -> Callable[[httpx2.Request], httpx2.Response]:
    return raw_response(200, _DATA[:size], "application/octet-stream")


def _gzipped() -> Callable[[httpx2.Request], httpx2.Response]:
    return raw_response(
        200, gzip.compress(_DATA, mtime=0), "application/octet-stream", **{"content-encoding": "gzip"}
    )


def _stalled(gate: threading.Event) -> Callable[[httpx2.Request], httpx2.Response]:
    return lambda _: httpx2.Response(200, headers={"content-type": "application/octet-stream"}, stream=_Stalled(gate))


def _files(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def _sent(requests: list[str]) -> int:
    return sum(line.startswith("  > ") for line in requests)


def _report(lines: list[str], label: str, results: dict[str, list[str]]) -> None:
    lines.extend(f"  {label} {name} x{len(seen)} {sorted(set(seen))}" for name, seen in results.items())


def stream_lifetimes(package: ModuleType, lines: list[str]) -> None:
    """Reuse one borrowed connection after every way a stream ends, close owned clients once, and write files."""
    _borrowed(package, lines)
    _owned(package, lines)
    with tempfile.TemporaryDirectory() as directory:
        _files_sync(package, lines, Path(directory))
    run(lambda: _async(package, lines))


def _sync_cases(package: ModuleType, api: Any, exchange: Exchange) -> dict[str, Callable[[], str]]:
    options = _modules(package)[0]
    pet, streaming = _pet(package), api.with_streaming_response

    def first_chunk() -> str:
        exchange.respond(_body())
        with streaming.request_raw("GET", _URL) as response:
            return f"read {bool(next(response.iter_bytes()))}"

    def typed_decode() -> str:
        exchange.respond(json_response(200, {"id": "x", "name": 1}))
        return outcome(lambda: api.pets.get_pet(pet_id=pet))

    def retried() -> str:
        exchange.respond(raw_response(503, b"down", "text/plain"), json_response(200, _PET))
        retry = options.RetryOptions(max_retries=1, initial_delay=0, jitter="none")
        return outcome(lambda: api.pets.get_pet(pet_id=pet, options=options.RequestOptions(retry=retry)))

    def token() -> str:
        cancel = options.CancelToken()
        exchange.respond(_body())
        with streaming.request_raw("GET", _URL, options=options.RequestOptions(cancel_token=cancel)) as response:
            chunks = response.iter_bytes()
            next(chunks)
            cancel.cancel()
            return outcome(lambda: list(chunks))

    def idle() -> str:
        gate = threading.Event()
        exchange.respond(_stalled(gate))
        try:
            with streaming.request_raw("GET", _URL, options=options.RequestOptions(stream_idle_timeout=0.5)) as raw:
                return outcome(raw.read)
        finally:
            gate.set()

    def limit() -> str:
        exchange.respond(_body())
        with streaming.request_raw("GET", _URL, options=options.RequestOptions(max_stream_bytes=1000)) as response:
            return outcome(lambda: list(response.iter_bytes()))

    return {
        "first chunk": first_chunk,
        "typed decode": typed_decode,
        "retried 503": retried,
        "token": token,
        "idle": idle,
        "stream limit": limit,
    }


def _borrowed(package: ModuleType, lines: list[str]) -> None:
    requests: list[str] = []
    exchange = Exchange(requests)
    http = exchange.client(1)
    results: dict[str, list[str]] = defaultdict(list)
    with package.Client(http_client=http) as api:
        cases = list(_sync_cases(package, api, exchange).items())
        for index in range(_ROUNDS):
            name, case = cases[index % len(cases)]
            results[name].append(case())
        exchange.respond(raw_response(200, b"pong", "text/plain"))
        lines.append(f"  sync borrowed after rounds {api.request_raw('GET', _URL).read()!r}")
    _report(lines, "sync borrowed", results)
    exchange.respond(raw_response(200, b"pong", "text/plain"))
    lines.append(f"  sync borrowed kept open {not http.is_closed} {http.get(_URL).content!r} sent {_sent(requests)}")
    http.close()


def _owned(package: ModuleType, lines: list[str]) -> None:
    exchange = Exchange([])
    http = exchange.client(1, kind=_Owned)
    with package.Client(http_client=http, http_client_ownership="owned") as api:
        exchange.respond(_body())
        with api.with_streaming_response.request_raw("GET", _URL) as response:
            next(response.iter_bytes())
    api.close()
    lines.append(f"  sync owned closed {http.is_closed} {http.closes}")


def _files_sync(package: ModuleType, lines: list[str], directory: Path) -> None:
    options = _modules(package)[0]
    exchange = Exchange([])
    http = exchange.client(1)
    target = directory / "full.bin"
    with package.Client(http_client=http) as api:
        streaming = api.with_streaming_response
        exchange.respond(_body(), raw_response(200, b"pong", "text/plain"))
        with streaming.request_raw("GET", _URL) as response, pytest.MonkeyPatch.context() as fault:
            fault.setattr(os, "fdopen", lambda handle, mode: _FullDisk(handle, 0, _free()))
            failed = outcome(lambda: response.stream_to(target))
            lines.append(f"  sync full disk {failed} then {api.request_raw('GET', _URL).read()!r} {_files(directory)}")
        exchange.respond(_body())
        with streaming.request_raw("GET", _URL) as response:
            with pytest.MonkeyPatch.context() as fault:
                fault.setattr(os, "fdopen", _unopenable)
                failed = outcome(lambda: response.stream_to(target))
            lines.append(f"  sync unopenable {failed} {_files(directory)} then read {len(response.read())}")
        exchange.respond(_body())
        with streaming.request_raw("GET", _URL) as response:
            chunks = response.iter_bytes()
            first = len(next(chunks))
            failed = outcome(lambda: response.stream_to(io.BytesIO()))
            lines.append(f"  sync file object while iterating {failed} then read {first + sum(map(len, chunks))}")
        (directory / "kept.bin").write_bytes(b"kept")
        exchange.respond(_body())
        with streaming.request_raw("GET", _URL) as response:
            drained = sum(map(len, response.iter_bytes()))
            failed = outcome(lambda: response.stream_to(directory / "kept.bin"))
            lines.append(f"  sync consumed to existing {drained} {failed} {_files(directory)}")
        with tempfile.TemporaryFile() as file:
            file.write(b"head")
            exchange.respond(_body(_CHUNK + 5))
            with streaming.request_raw("GET", _URL) as response:
                response.stream_to(file)
            end = file.tell()
            file.seek(0)
            lines.append(f"  sync borrowed file open {not file.closed} at {end} {file.read() == b'head' + _DATA[: _CHUNK + 5]}")
        exchange.respond(_body(), raw_response(200, b"pong", "text/plain"))
        with streaming.request_raw("GET", _URL) as response:
            failed = outcome(lambda: response.stream_to(_Sink()))
            lines.append(f"  sync file object write {failed} then {api.request_raw('GET', _URL).read()!r}")
        exchange.respond(_body())
        with streaming.request_raw("GET", _URL, options=options.RequestOptions(max_stream_bytes=1000)) as response:
            lines.append(f"  sync limit to path {outcome(lambda: response.stream_to(target))} {_files(directory)}")
    http.close()


def _unopenable(handle: int, mode: str) -> BinaryIO:
    raise OSError(errno.EMFILE, os.strerror(errno.EMFILE))


class _Sink(io.RawIOBase):
    """A borrowed file whose device fails every write."""

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        raise OSError(errno.EIO, os.strerror(errno.EIO))


def _async_cases(package: ModuleType, api: Any, exchange: Exchange) -> dict[str, Callable[[], Awaitable[str]]]:
    options = _modules(package)[0]
    pet, streaming = _pet(package), api.with_streaming_response

    async def first_chunk() -> str:
        exchange.respond(_body())
        async with streaming.request_raw("GET", _URL) as response:
            return f"read {bool(await anext(response.iter_bytes()))}"

    async def typed_decode() -> str:
        exchange.respond(json_response(200, {"id": "x", "name": 1}))
        return await aoutcome(lambda: api.pets.get_pet(pet_id=pet))

    async def retried() -> str:
        exchange.respond(raw_response(503, b"down", "text/plain"), json_response(200, _PET))
        retry = options.RetryOptions(max_retries=1, initial_delay=0, jitter="none")
        return await aoutcome(lambda: api.pets.get_pet(pet_id=pet, options=options.RequestOptions(retry=retry)))

    async def token() -> str:
        cancel = options.CancelToken()
        exchange.respond(_body())
        async with streaming.request_raw("GET", _URL, options=options.RequestOptions(cancel_token=cancel)) as raw:
            chunks = raw.iter_bytes()
            await anext(chunks)
            cancel.cancel()
            return await aoutcome(lambda: _drained(chunks))

    async def idle() -> str:
        gate = threading.Event()
        exchange.respond(_stalled(gate))
        try:
            async with streaming.request_raw(
                "GET", _URL, options=options.RequestOptions(stream_idle_timeout=0.5)
            ) as response:
                return await aoutcome(response.read)
        finally:
            gate.set()

    async def limit() -> str:
        exchange.respond(_body())
        async with streaming.request_raw("GET", _URL, options=options.RequestOptions(max_stream_bytes=1000)) as raw:
            return await aoutcome(lambda: _drained(raw.iter_bytes()))

    return {
        "first chunk": first_chunk,
        "typed decode": typed_decode,
        "retried 503": retried,
        "token": token,
        "idle": idle,
        "stream limit": limit,
    }


async def _drained(chunks: AsyncIterator[bytes]) -> int:
    return sum([len(chunk) async for chunk in chunks])


async def _async(package: ModuleType, lines: list[str]) -> None:
    requests: list[str] = []
    exchange = Exchange(requests)
    http = exchange.async_client(1)
    results: dict[str, list[str]] = defaultdict(list)
    async with package.AsyncClient(http_client=http) as api:
        cases = list(_async_cases(package, api, exchange).items())
        for index in range(_ROUNDS):
            name, case = cases[index % len(cases)]
            results[name].append(await case())
        exchange.respond(raw_response(200, b"pong", "text/plain"))
        lines.append(f"  async borrowed after rounds {await (await api.request_raw('GET', _URL)).read()!r}")
    _report(lines, "async borrowed", results)
    exchange.respond(raw_response(200, b"pong", "text/plain"))
    lines.append(
        f"  async borrowed kept open {not http.is_closed} {(await http.get(_URL)).content!r} sent {_sent(requests)}"
    )
    await http.aclose()
    await _async_owned(package, lines)
    with tempfile.TemporaryDirectory() as directory:
        await _downloads(package, lines, Path(directory))
    with tempfile.TemporaryDirectory() as directory:
        await _stopped_downloads(package, lines, Path(directory))


async def _async_owned(package: ModuleType, lines: list[str]) -> None:
    exchange = Exchange([])
    http = exchange.async_client(1, kind=_AsyncOwned)
    async with package.AsyncClient(http_client=http, http_client_ownership="owned") as api:
        exchange.respond(_body())
        async with api.with_streaming_response.request_raw("GET", _URL) as response:
            await anext(response.iter_bytes())
    await api.aclose()
    lines.append(f"  async owned closed {http.is_closed} {http.closes}")


def _workers() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name.startswith(_WORKER)]


async def _downloads(package: ModuleType, lines: list[str], directory: Path) -> None:
    """Download whole bodies on the handle's disk thread, then show the thread gone."""
    options = _modules(package)[0]
    exchange = Exchange([])
    http = exchange.async_client(1, kind=_Reading)
    async with package.AsyncClient(http_client=http) as api:
        streaming = api.with_streaming_response
        exchange.respond(raw_response(200, b"warm", "text/plain"))
        await (await api.request_raw("GET", _URL)).read()
        before = threading.enumerate()
        held, gate = directory / "held.bin", threading.Event()
        exchange.respond(_stalled(gate))
        async with streaming.request_raw("GET", _URL) as response:
            task = asyncio.create_task(response.stream_to(held))
            await _mid_body(http, task)
            writing = [thread.name for thread in _workers()]
            gate.set()
            await task
        lines.append(f"  async download mid-body disk threads {writing} wrote {held.read_bytes() == _DATA[: 2 * _CHUNK]}")
        identity, coded = directory / "identity.bin", directory / "coded.bin"
        exchange.respond(_body(len(_DATA)), _gzipped())
        async with streaming.request_raw("GET", _URL) as response:
            await response.stream_to(identity)
            again = await aoutcome(lambda: response.stream_to(directory / "again.bin"))
        async with streaming.request_raw("GET", _URL) as response:
            await response.stream_to(str(coded))
        lines.append(
            f"  async download identity {identity.read_bytes() == _DATA} gzip {coded.read_bytes() == _DATA} again {again}"
        )
        for worker in _workers():
            worker.join(5)
        lines.append(
            f"  async download threads left {[thread.name for thread in threading.enumerate() if thread not in before]}"
        )
        exchange.respond(_gzipped())
        limit = options.RequestOptions(max_stream_bytes=len(_DATA) // 2)
        async with streaming.request_raw("GET", _URL, options=limit) as response:
            try:
                await response.stream_to(directory / "limited.bin")
            except Exception as error:  # noqa: BLE001
                limited = f"{type(error).__name__} {error.representation} {error.limit}"
        lines.append(f"  async download gzip past its decoded limit {limited} {_files(directory)}")
        exchange.respond(raw_response(200))
        async with streaming.request_raw("GET", _URL) as response:
            await response.stream_to(directory / "empty.bin")
        exchange.respond(_body(_CHUNK + 5))
        saved = await api.request_raw("GET", _URL)
        with tempfile.TemporaryFile() as file:
            file.write(b"head")
            exchange.respond(_body(_CHUNK + 5))
            async with streaming.request_raw("GET", _URL) as response:
                await response.stream_to(file)
            end = file.tell()
            file.seek(0)
            kept = file.read() == b"head" + _DATA[: _CHUNK + 5]
            lines.append(f"  async borrowed file open {not file.closed} at {end} {kept}")
        cancel = options.CancelToken()
        exchange.respond(_body())
        async with streaming.request_raw("GET", _URL, options=options.RequestOptions(cancel_token=cancel)) as raw:
            cancel.cancel()
            lines.append(f"  async cancelled before download {await aoutcome(lambda: raw.stream_to(directory / 'no.bin'))}")
        exchange.respond(_body(), _body())
        async with streaming.request_raw("GET", _URL) as response:
            chunks = response.iter_bytes()
            first = len(await anext(chunks))
            failed = await aoutcome(lambda: response.stream_to(io.BytesIO()))
            lines.append(f"  async file object while iterating {failed} then read {first + await _drained(chunks)}")
        async with streaming.request_raw("GET", _URL) as response:
            drained = await _drained(response.iter_bytes())
            failed = await aoutcome(lambda: response.stream_to(identity))
            lines.append(f"  async consumed to existing {drained} {failed} {_files(directory)}")
        await _opening_faults(api, exchange, lines, directory, options)
        await _disk_failures(api, exchange, lines, directory)
    await saved.stream_to(directory / "saved.bin")
    lines.append(
        f"  async saved after close {(directory / 'saved.bin').read_bytes() == _DATA[: _CHUNK + 5]} {_files(directory)}"
    )
    await http.aclose()


async def _opening_faults(
    api: Any, exchange: Exchange, lines: list[str], directory: Path, options: ModuleType
) -> None:
    """Stop downloads while or right after the disk thread creates their file, and keep nothing it created."""
    streaming, target = api.with_streaming_response, directory / "opening.bin"
    cancel = options.CancelToken()
    opening = _Opening(cancel.cancel)
    exchange.respond(_body())
    async with streaming.request_raw("GET", _URL, options=options.RequestOptions(cancel_token=cancel)) as response:
        with pytest.MonkeyPatch.context() as fault:
            fault.setattr(os, "fdopen", opening)
            failed = await aoutcome(lambda: response.stream_to(target))
    closed = [file.closed for file in opening.opened]
    lines.append(f"  async cancelled once opened {failed} closed {closed} {_files(directory)}")
    for label in ("cancelled while opening", "removal denied", "read while opening"):
        gate = _Gate()
        exchange.respond(_body())
        async with streaming.request_raw("GET", _URL) as response:
            with pytest.MonkeyPatch.context() as fault:
                fault.setattr(os, "fdopen", _Opening(gate.hold))
                if label == "removal denied":
                    fault.setattr(Path, "unlink", _denied)
                task = asyncio.create_task(response.stream_to(target))
                await gate.reached()
                chunks = response.iter_bytes() if label == "read while opening" else None
                if chunks is None:
                    task.cancel()
                gate.released.set()
                try:
                    result = await aoutcome(lambda: task)
                except asyncio.CancelledError as error:
                    result = f"CancelledError notes {error.__dict__.get('__notes__', [])}"
                left = [path.name.endswith(".part") for path in directory.iterdir()]
            if chunks is not None:
                result += f" then read {await _drained(chunks)}"
        for path in directory.glob("*.part"):
            path.unlink()
        lines.append(f"  async {label} {result} partial files {left.count(True)}")


async def _disk_failures(api: Any, exchange: Exchange, lines: list[str], directory: Path) -> None:
    """Fail disk writes while a borrowed connection is held, ending the stream before the failure propagates."""
    streaming, target = api.with_streaming_response, directory / "full.bin"
    written = threading.Event()
    broken = injected(lambda _: httpx2.Response(200, stream=_Broken(written)))
    for room, responder, slow in ((0, _body(), _free()), (1, broken, written)):
        exchange.respond(responder, raw_response(200, b"pong", "text/plain"))
        async with streaming.request_raw("GET", _URL) as response:
            with pytest.MonkeyPatch.context() as fault:
                fault.setattr(os, "fdopen", lambda handle, mode, room=room, slow=slow: _FullDisk(handle, room, slow))
                failed = await aoutcome(lambda: response.stream_to(target))
            after = await (await api.request_raw("GET", _URL)).read()
            lines.append(f"  async full disk room {room} {failed} then {after!r} {_files(directory)}")
    exchange.respond(_body(), raw_response(200, b"pong", "text/plain"))
    async with streaming.request_raw("GET", _URL) as response:
        failed = await aoutcome(lambda: response.stream_to(_Sink()))
        lines.append(f"  async file object write {failed} then {await (await api.request_raw('GET', _URL)).read()!r}")
    exchange.respond(injected(lambda _: httpx2.Response(200, stream=_Broken(threading.Event()))))
    async with streaming.request_raw("GET", _URL) as response:
        lines.append(f"  async file object broken read {await aoutcome(lambda: response.stream_to(io.BytesIO()))}")


async def _mid_body(http: _Reading, task: asyncio.Task[None]) -> None:
    """Wait until the download has taken the body's first chunk and asks for the held rest, or until it ended."""
    reading = asyncio.create_task(http.reading.wait())
    await asyncio.wait((reading, task), return_when=asyncio.FIRST_COMPLETED)
    reading.cancel()


async def _stopped_downloads(package: ModuleType, lines: list[str], directory: Path) -> None:
    """Stop downloads mid-body by cancellation, token, limits, and closing; keep the target and remove the rest."""
    options = _modules(package)[0]
    exchange = Exchange([])
    http = exchange.async_client(1, kind=_Reading)
    target = directory / "target.bin"
    target.write_bytes(b"old")
    for label, settings in (
        ("cancelled", None),
        ("token", options.RequestOptions(cancel_token=options.CancelToken())),
        ("idle", options.RequestOptions(stream_idle_timeout=0.5)),
        ("deadline", options.RequestOptions(stream_total_timeout=0.5)),
        ("closing", options.RequestOptions(hooks=(_Suspending(),))),
    ):
        gate = threading.Event()
        exchange.respond(_stalled(gate))
        api = package.AsyncClient(http_client=http)
        async with api.with_streaming_response.request_raw("GET", _URL, options=settings) as response:
            task = asyncio.create_task(response.stream_to(target, overwrite=True))
            await _mid_body(http, task)
            closed = ""
            match label:
                case "cancelled":
                    task.cancel()
                case "token":
                    settings.cancel_token.cancel()
                case "closing":
                    closed = f" aclose {await aoutcome(api.aclose)} then {_files(directory)}"
                case _:
                    pass
            try:
                result = await aoutcome(lambda: task)
            except asyncio.CancelledError:
                result = "CancelledError"
            gate.set()
        await api.aclose()
        lines.append(f"  async stopped {label} {result}{closed} {_files(directory)} {target.read_bytes()!r}")
    exchange.respond(raw_response(200, b"pong", "text/plain"))
    lines.append(f"  async stopped borrowed reused {(await http.get(_URL)).content!r}")
    await http.aclose()
