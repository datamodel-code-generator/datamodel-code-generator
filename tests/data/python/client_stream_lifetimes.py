"""Keep connections and downloads only as long as their streams: borrowed pools, passed clients, and disk work."""

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
from contextlib import AsyncExitStack
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Final

import httpx2
import pytest

from tests.data.python.client_runtime import (
    Exchange,
    aoutcome,
    argument,
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


class _Written(io.FileIO):
    """A download file that tells the scenario once a write of the body reached it."""

    def __init__(self, handle: int, mode: str, *, written: Callable[[], object]) -> None:
        super().__init__(handle, mode)
        self.written = written

    def write(self, data: Any) -> int:
        size = super().write(data)
        self.written()
        return size


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


def _denied(path: Path, *, missing_ok: bool = False) -> None:
    raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))


class _Owned(httpx2.Client):
    """Count how often this HTTPX2 client is closed."""

    closes = 0

    def close(self) -> None:
        self.closes += 1
        super().close()


class _AsyncOwned(httpx2.AsyncClient):
    """Count how often this asyncio HTTPX2 client is closed."""

    closes = 0

    async def aclose(self) -> None:
        self.closes += 1
        await super().aclose()


def _modules(package: ModuleType) -> tuple[ModuleType, ModuleType]:
    return importlib.import_module(f"{package.__name__}.options"), importlib.import_module(
        f"{package.__name__}.types.pets"
    )


def _pet(package: ModuleType) -> object:
    return argument(package, "getPet", "path", "petId", 3)


def _body(size: int = 3 * _CHUNK) -> Callable[[httpx2.Request], httpx2.Response]:
    return raw_response(200, _DATA[:size], "application/octet-stream")


def _gzipped() -> Callable[[httpx2.Request], httpx2.Response]:
    return raw_response(200, gzip.compress(_DATA, mtime=0), "application/octet-stream", **{"content-encoding": "gzip"})


def _stalled(gate: threading.Event) -> Callable[[httpx2.Request], httpx2.Response]:
    return lambda _: httpx2.Response(200, headers={"content-type": "application/octet-stream"}, stream=_Stalled(gate))


def _files(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def _sent(requests: list[str]) -> int:
    return sum(line.startswith("  > ") for line in requests)


def _report(lines: list[str], label: str, results: dict[str, list[str]]) -> None:
    lines.extend(f"  {label} {name} x{len(seen)} {sorted(set(seen))}" for name, seen in results.items())


def stream_lifetimes(package: ModuleType, lines: list[str]) -> None:
    """Reuse one borrowed connection after every way a stream ends, never close a passed client, and write files."""
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

    def read_timeout() -> str:
        gate = threading.Event()
        exchange.respond(_stalled(gate))
        try:
            with streaming.request_raw("GET", _URL, options=_read_timeout(options)) as raw:
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
        "read timeout": read_timeout,
        "stream limit": limit,
    }


def _read_timeout(options: ModuleType) -> object:
    """Return options whose native read timeout ends a stalled body."""
    return options.RequestOptions(timeout=options.TimeoutOptions(read=0.5))


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
    with package.Client(http_client=http) as api:
        exchange.respond(_body())
        with api.with_streaming_response.request_raw("GET", _URL) as response:
            next(response.iter_bytes())
    api.close()
    lines.append(f"  sync passed client kept open {not http.is_closed} closes {http.closes}")
    http.close()


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
            lines.append(
                f"  sync borrowed file open {not file.closed} at {end} {file.read() == b'head' + _DATA[: _CHUNK + 5]}"
            )
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


def _async_cases(
    package: ModuleType, api: Any, exchange: Exchange, iterators: AsyncExitStack
) -> dict[str, Callable[[], Awaitable[str]]]:
    options = _modules(package)[0]
    pet, streaming = _pet(package), api.with_streaming_response

    async def first_chunk() -> str:
        exchange.respond(_body())
        async with streaming.request_raw("GET", _URL) as response:
            chunks = response.iter_bytes()
            iterators.push_async_callback(chunks.aclose)
            return f"read {bool(await anext(chunks))}"

    async def typed_decode() -> str:
        exchange.respond(json_response(200, {"id": "x", "name": 1}))
        return await aoutcome(lambda: api.pets.get_pet(pet_id=pet))

    async def retried() -> str:
        exchange.respond(raw_response(503, b"down", "text/plain"), json_response(200, _PET))
        retry = options.RetryOptions(max_retries=1, initial_delay=0, jitter="none")
        return await aoutcome(lambda: api.pets.get_pet(pet_id=pet, options=options.RequestOptions(retry=retry)))

    async def cancelled() -> str:
        gate, started = threading.Event(), asyncio.Event()

        async def read() -> int:
            async with streaming.request_raw("GET", _URL) as raw:
                chunks = raw.iter_bytes()
                await anext(chunks)
                started.set()
                return await _drained(chunks)

        exchange.respond(_stalled(gate))
        reader = asyncio.create_task(read())
        try:
            await started.wait()
            reader.cancel("reader cancelled")
            await reader
        except asyncio.CancelledError as error:
            return f"CancelledError {error.args}"
        finally:
            gate.set()
        return "not cancelled"

    async def read_timeout() -> str:
        gate = threading.Event()
        exchange.respond(_stalled(gate))
        try:
            async with streaming.request_raw("GET", _URL, options=_read_timeout(options)) as response:
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
        "cancelled": cancelled,
        "read timeout": read_timeout,
        "stream limit": limit,
    }


async def _drained(chunks: AsyncIterator[bytes]) -> int:
    return sum([len(chunk) async for chunk in chunks])


async def _async(package: ModuleType, lines: list[str]) -> None:
    requests: list[str] = []
    exchange = Exchange(requests)
    http = exchange.async_client(1)
    results: dict[str, list[str]] = defaultdict(list)
    async with AsyncExitStack() as iterators:
        async with package.AsyncClient(http_client=http) as api:
            cases = list(_async_cases(package, api, exchange, iterators).items())
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
    async with AsyncExitStack() as iterators:
        async with package.AsyncClient(http_client=http) as api:
            exchange.respond(_body())
            async with api.with_streaming_response.request_raw("GET", _URL) as response:
                chunks = response.iter_bytes()
                iterators.push_async_callback(chunks.aclose)
                await anext(chunks)
        await api.aclose()
        lines.append(f"  async passed client kept open {not http.is_closed} closes {http.closes}")
    await http.aclose()


async def _downloads(package: ModuleType, lines: list[str], directory: Path) -> None:
    """Download whole bodies on the handle's disk thread, then show the thread gone."""
    options = _modules(package)[0]
    exchange = Exchange([])
    http = exchange.async_client(1)
    async with package.AsyncClient(http_client=http) as api:
        streaming = api.with_streaming_response
        exchange.respond(raw_response(200, b"warm", "text/plain"))
        await (await api.request_raw("GET", _URL)).read()
        held, gate = directory / "held.bin", threading.Event()
        exchange.respond(_stalled(gate))
        async with streaming.request_raw("GET", _URL) as response:
            task = asyncio.create_task(response.stream_to(held))
            await _mid_body(task)
            gate.set()
            await task
        lines.append(f"  async download mid-body wrote {held.read_bytes() == _DATA[: 2 * _CHUNK]}")
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
        exchange.respond(_gzipped())
        limit = options.RequestOptions(max_stream_bytes=len(_DATA) // 2)
        async with streaming.request_raw("GET", _URL, options=limit) as response:
            try:
                await response.stream_to(directory / "limited.bin")
            except Exception as error:  # noqa: BLE001
                limited = f"{type(error).__name__} {error.reason} {error.limit}"
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
        await _opening_faults(api, exchange, lines, directory)
        await _disk_failures(api, exchange, lines, directory)
    await _late_download(package, http, exchange, lines, directory)
    await saved.stream_to(directory / "saved.bin")
    lines.append(
        f"  async saved after close {(directory / 'saved.bin').read_bytes() == _DATA[: _CHUNK + 5]} {_files(directory)}"
    )
    await http.aclose()


@pytest.mark.abnormal_path(
    "Portable external file-opening and unlink failures exercise cancellation cleanup."
)
async def _opening_faults(api: Any, exchange: Exchange, lines: list[str], directory: Path) -> None:
    """Cancel after conventional file creation and report an abnormal unlink failure."""
    streaming, target = api.with_streaming_response, directory / "opening.bin"
    loop = asyncio.get_running_loop()
    for label in ("cancelled once opened", "removal denied"):
        downloads: list[asyncio.Task[None]] = []
        opening = _Opening(lambda: loop.call_soon(downloads[0].cancel, "cancelled once opened"))
        exchange.respond(_body())
        async with streaming.request_raw("GET", _URL) as response:
            with pytest.MonkeyPatch.context() as fault:
                fault.setattr(os, "fdopen", opening)
                if label == "removal denied":
                    fault.setattr(Path, "unlink", _denied)
                downloads.append(asyncio.create_task(response.stream_to(target)))
                try:
                    result = await aoutcome(lambda: downloads[0])
                except asyncio.CancelledError as error:
                    result = f"CancelledError {error.args} notes {error.__dict__.get('__notes__', [])}"
                left = sum(path.name.endswith(".part") for path in directory.iterdir())
        for path in directory.glob("*.part"):
            path.unlink()
        closed = [file.closed for file in opening.opened]
        lines.append(f"  async {label} {result} closed {closed} partial files {left}")


async def _disk_failures(api: Any, exchange: Exchange, lines: list[str], directory: Path) -> None:
    """Fail disk writes while a borrowed connection is held, ending the stream before the failure propagates."""
    streaming, target = api.with_streaming_response, directory / "full.bin"
    broken = injected(lambda _: httpx2.Response(200, stream=_Broken(threading.Event())))
    for room, responder, slow in ((0, _body(), _free()), (1, broken, _free())):
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


async def _late_download(
    package: ModuleType, http: httpx2.AsyncClient, exchange: Exchange, lines: list[str], directory: Path
) -> None:
    """Refuse a download to a path whose stream's total time passed on the client's clock, before any disk work."""
    options, now = _modules(package)[0], [0.0]
    settings = options.ClientOptions(clock=options.Clock(monotonic=lambda: now[0]))
    async with package.AsyncClient(http_client=http, options=settings) as api:
        exchange.respond(_body())
        limit = options.RequestOptions(total_timeout=5)
        async with api.with_streaming_response.request_raw("GET", _URL, options=limit) as response:
            now[0] = 10.0
            late = "returned"
            try:
                await response.stream_to(directory / "late.bin")
            except Exception as error:  # noqa: BLE001
                late = f"{type(error).__name__} {error.reason} {error.phase} status {error.info.status_code}"
        lines.append(f"  async download after its acquisition budget {late} {_files(directory)}")


async def _mid_body(task: asyncio.Task[None]) -> None:
    """Wait until the download wrote the body's first chunk to its temporary file, or until it ended."""
    loop, written = asyncio.get_running_loop(), asyncio.Event()
    with pytest.MonkeyPatch.context() as disk:
        disk.setattr(os, "fdopen", partial(_Written, written=partial(loop.call_soon_threadsafe, written.set)))
        waiting = asyncio.create_task(written.wait())
        await asyncio.wait((waiting, task), return_when=asyncio.FIRST_COMPLETED)
        waiting.cancel()


async def _stopped_downloads(package: ModuleType, lines: list[str], directory: Path) -> None:
    """Stop downloads mid-body by cancellation and timeouts; keep the target and remove the rest."""
    options = _modules(package)[0]
    exchange = Exchange([])
    http = exchange.async_client(1)
    target = directory / "target.bin"
    target.write_bytes(b"old")
    for label, settings in (
        ("cancelled", None),
        ("read timeout", _read_timeout(options)),
        ("deadline", options.RequestOptions(total_timeout=3)),
    ):
        gate = threading.Event()
        exchange.respond(_stalled(gate))
        api = package.AsyncClient(http_client=http)
        async with api.with_streaming_response.request_raw("GET", _URL, options=settings) as response:
            task = asyncio.create_task(response.stream_to(target, overwrite=True))
            await _mid_body(task)
            if label == "cancelled":
                task.cancel()
            try:
                result = await aoutcome(lambda: task)
            except asyncio.CancelledError:
                result = "CancelledError"
            gate.set()
        await api.aclose()
        lines.append(f"  async stopped {label} {result} {_files(directory)} {target.read_bytes()!r}")
    exchange.respond(raw_response(200, b"pong", "text/plain"))
    lines.append(f"  async stopped borrowed reused {(await http.get(_URL)).content!r}")
    await http.aclose()
