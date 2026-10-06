"""Send file, stream, and factory bodies from generated clients, and refuse the ones that cannot be sent again."""

from __future__ import annotations

import importlib
import io
import os
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx2

from tests.data.python.client_runtime import (
    Exchange,
    Stop,
    aoutcome,
    arecord,
    argument,
    outcome,
    raw_response,
    record,
    run,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from types import ModuleType

_PNG = b"\x89PNG"


def _photo(package: ModuleType) -> object:
    return argument(package, "uploadPhoto", "path", "petId", 1)


class _Unseekable(io.BytesIO):
    """A file that cannot seek, so it is read once."""

    def seekable(self) -> bool:
        return False


class _Unreadable(io.BytesIO):
    """A file whose reads fail."""

    def read(self, size: int | None = -1, /) -> bytes:
        msg = "disk failed"
        raise OSError(msg)


class _Untellable(io.BytesIO):
    """A file whose position cannot be read."""

    def tell(self) -> int:
        msg = "tell failed"
        raise OSError(msg)


class _Interrupting(io.BytesIO):
    """A file interrupted while it is checked."""

    def seekable(self) -> bool:
        raise Stop


class _Sized(io.BytesIO):
    """A file whose end is reported elsewhere than where its bytes end."""

    def __init__(self, content: bytes, end: int) -> None:
        super().__init__(content)
        self.end = end

    def seek(self, offset: int, whence: int = 0, /) -> int:
        position = super().seek(offset, whence)
        return self.end if whence == os.SEEK_END else position


def _chunks(chunks: tuple[object, ...]) -> Iterator[object]:
    for chunk in chunks:
        if isinstance(chunk, BaseException):
            raise chunk
        yield chunk


class Chunks:
    """Chunks that raise any exception among them and record their close."""

    def __init__(self, lines: list[str], chunks: tuple[object, ...]) -> None:
        self.lines = lines
        self.chunks = chunks

    def __iter__(self) -> Iterator[object]:
        return _chunks(self.chunks)

    async def __aiter__(self) -> AsyncIterator[object]:
        for chunk in _chunks(self.chunks):
            yield chunk

    def close(self) -> None:
        self.lines.append("  chunks closed")

    async def aclose(self) -> None:
        self.lines.append("  async chunks closed")


class Attempt:
    """A factory's attempt of canned chunks, which records its close and may fail to iterate or to close."""

    def __init__(
        self,
        lines: list[str],
        chunks: tuple[object, ...],
        length: int | None = None,
        *,
        close_error: bool = False,
        iter_error: bool = False,
        interrupt: bool = False,
    ) -> None:
        self.lines = lines
        self.chunks = chunks
        self.length = length
        self.close_error = close_error
        self.iter_error = iter_error
        self.interrupt = interrupt

    @property
    def content_length(self) -> int | None:
        return self.length

    @property
    def content_type(self) -> str | None:
        return "application/x-attempt"

    def iter_bytes(self) -> Iterator[object]:
        if self.iter_error:
            msg = "cannot iterate"
            raise RuntimeError(msg)
        return _chunks(self.chunks)

    def aiter_bytes(self) -> Any:
        if self.iter_error:
            msg = "cannot iterate"
            raise RuntimeError(msg)
        return Chunks(self.lines, self.chunks).__aiter__()

    def close(self) -> None:
        self.lines.append("  attempt closed")
        if self.interrupt:
            raise Stop
        if self.close_error:
            msg = "close failed"
            raise RuntimeError(msg)

    async def aclose(self) -> None:
        self.close()


def attempt_factory(
    lines: list[str], *chunks: object, length: int | None = None, **options: bool
) -> Callable[[Any], Any]:
    def build(context: Any) -> Attempt:
        remaining = context.remaining_timeout
        bounded = None if remaining is None else 0 < remaining <= 60
        lines.append(f"  factory {context.call_id} {context.attempt_index} {context.hop_index} {bounded}")
        return Attempt(lines, chunks, length, **options)

    return build


def async_attempt_factory(
    lines: list[str], *chunks: object, length: int | None = None, **options: bool
) -> Callable[[Any], Any]:
    build = attempt_factory(lines, *chunks, length=length, **options)

    async def abuild(context: Any) -> Attempt:
        return build(context)

    return abuild


def _failing(context: Any) -> Any:
    msg = "factory failed"
    raise RuntimeError(msg)


async def _afailing(context: Any) -> Any:
    return _failing(context)


def _blocked(entered: threading.Event, proceed: threading.Event) -> Callable[[httpx2.Request], httpx2.Response]:
    def respond(request: httpx2.Request) -> httpx2.Response:
        entered.set()
        proceed.wait()
        return httpx2.Response(200, headers={"content-type": "image/png"}, stream=httpx2.ByteStream(_PNG))

    return respond


def bodies(package: ModuleType, lines: list[str]) -> None:
    """Upload bytes, files, streams, and factories, synchronously and with asyncio, and refuse the rest."""
    exchange = Exchange(lines)
    http = exchange.client()
    with tempfile.TemporaryDirectory() as directory, package.Client(http_client=http) as api:
        _files(package, api, exchange, lines, Path(directory))
        _streams(package, api, exchange, lines)
        _factories(package, api, exchange, lines)
    http.close()
    _interrupted_close(package, lines)
    run(lambda: _async_bodies(package, lines))


def _interrupted_close(package: ModuleType, lines: list[str]) -> None:
    """Preserve a body's close interruption after a real native response."""
    bodies_module = importlib.import_module(f"{package.__name__}.bodies")
    exchange = Exchange(lines)
    exchange.respond(raw_response(200, _PNG, "image/png"))
    with exchange.client() as native, package.Client(http_client=native) as api:
        body = bodies_module.BodyFactory(attempt_factory(lines, b"x", interrupt=True))
        try:
            api.pets.photos.upload(pet_id=_photo(package), body=body)
        except Stop:
            lines.append("  interrupted attempt close propagated")
    run(lambda: _async_interrupted_close(package, lines))


async def _async_interrupted_close(package: ModuleType, lines: list[str]) -> None:
    """Preserve a body's close interruption after a real native response."""
    bodies_module = importlib.import_module(f"{package.__name__}.bodies")
    exchange = Exchange(lines)
    exchange.respond(raw_response(200, _PNG, "image/png"))
    async with exchange.async_client() as native, package.AsyncClient(http_client=native) as api:
        body = bodies_module.AsyncBodyFactory(async_attempt_factory(lines, b"x", interrupt=True))
        try:
            await api.pets.photos.upload(pet_id=_photo(package), body=body)
        except Stop:
            lines.append("  async interrupted attempt close propagated")


def _uploader(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> Callable[..., None]:
    """Return a step that uploads a body, answering it only when a request is expected to arrive."""
    photo = _photo(package)

    def upload(label: str, body: object, *, answered: bool = False, secondaries: bool = False) -> None:
        if answered:
            exchange.respond(raw_response(200, _PNG, "image/png"))
        call = lambda: api.pets.photos.upload(pet_id=photo, body=body)  # noqa: E731
        if secondaries:
            lines.append(f"  {label}: {outcome(call)}")
        else:
            record(lines, label, call)

    return upload


def _files(package: ModuleType, api: Any, exchange: Exchange, lines: list[str], directory: Path) -> None:
    """Read files from their position by one call at a time, or from their path while it names the same file."""
    bodies_module = importlib.import_module(f"{package.__name__}.bodies")
    file_body = bodies_module.FileBody
    upload = _uploader(package, api, exchange, lines)
    borrowed = io.BytesIO(b"abcdef")
    borrowed.seek(2)
    body = file_body(borrowed)
    upload("file from its position", body, answered=True)
    lines.append(f"    borrowed file left open at {borrowed.tell()} {not borrowed.closed}")
    upload("file again from where it stopped", body, answered=True)
    (directory / "owned.bin").write_bytes(b"owned")
    handle = (directory / "owned.bin").open("rb")
    owned = file_body(handle, ownership="owned")
    upload("owned file", owned, answered=True)
    lines.append(f"    owned file closed {handle.closed}")
    upload("owned file again", owned)
    once = file_body(_Unseekable(b"once"))
    upload("file that cannot seek", once, answered=True)
    upload("file that cannot seek again", once)
    upload("unreadable file", file_body(_Unreadable(b"x")))
    upload("file without a position", file_body(_Untellable(b"x")))
    upload("file longer than its end", file_body(_Sized(b"abcdef", 3)))
    upload("file shorter than its end", file_body(_Sized(b"abc", 6)))
    interrupted = file_body(_Interrupting(b"x"))
    try:
        api.pets.photos.upload(pet_id=_photo(package), body=interrupted)
    except Stop:
        lines.append("  interrupted file propagated")
    shared = file_body(io.BytesIO(b"shared"))
    entered, proceed = threading.Event(), threading.Event()
    exchange.respond(_blocked(entered, proceed))
    first: list[str] = []
    reader = threading.Thread(
        target=lambda: record(
            first, "first reader", lambda: api.pets.photos.upload(pet_id=_photo(package), body=shared)
        )
    )
    reader.start()
    entered.wait()
    upload("file another call reads", shared)
    proceed.set()
    reader.join()
    lines.extend(first)
    path = directory / "photo.png"
    path.write_bytes(_PNG)
    by_path = file_body.from_path(path)
    upload("path", by_path, answered=True)
    upload("path again", by_path, answered=True)
    path.write_bytes(b"changed!")
    upload("changed path", by_path)
    removed = file_body.from_path(path)
    path.unlink()
    upload("removed path", removed)
    lines.append(f"  missing path: {outcome(lambda: file_body.from_path(directory / 'missing.png'))}")
    upload("async file to the sync client", bodies_module.AsyncFileBody(io.BytesIO(b"x")))
    exchange.respond(raw_response(200, b"ok", "text/plain"))
    record(
        lines,
        "raw file",
        lambda: api.request_raw("PUT", "https://hooks.example.com/file", body=file_body(io.BytesIO(b"raw"))).body_bytes,
    )


def _streams(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send streams once, closing owned ones, and refuse chunks that are not bytes."""
    stream_body = importlib.import_module(f"{package.__name__}.bodies").StreamBody
    upload = _uploader(package, api, exchange, lines)
    stream = stream_body(Chunks(lines, (b"ab", b"", b"cd")))
    upload("stream", stream, answered=True)
    upload("stream again", stream)
    upload("owned stream", stream_body(Chunks(lines, (b"x",)), ownership="owned"), answered=True)
    upload("stream of text", stream_body(["text"]))
    upload("failing stream", stream_body(Chunks(lines, (b"x", RuntimeError("stream failed")))))
    upload("stream that is not iterable", stream_body(5))
    try:
        api.pets.photos.upload(
            pet_id=_photo(package), body=stream_body(Chunks(lines, (b"x", Stop())), ownership="owned")
        )
    except Stop:
        lines.append("  interrupted owned stream propagated")


def _factories(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Build a new attempt for each call, within the length the factory declares, and close each attempt."""
    body_factory = importlib.import_module(f"{package.__name__}.bodies").BodyFactory
    upload = _uploader(package, api, exchange, lines)
    made = body_factory(
        attempt_factory(lines, b"made", length=4), content_length=4, content_type="image/png", fingerprint=b"f"
    )
    lines.append(f"  factory declares {made.content_length} {made.content_type} {made.fingerprint!r}")
    upload("factory", made, answered=True)
    upload("factory again", made, answered=True)
    constant = Attempt(lines, (b"c",))
    same = body_factory(lambda context: constant)
    upload("factory once", same, answered=True)
    upload("factory returning the same attempt", same)
    upload("failing factory", body_factory(_failing))
    upload("attempt of another length", body_factory(attempt_factory(lines, b"xy", length=2), content_length=3))
    upload(
        "attempt of another length that fails to close",
        body_factory(attempt_factory(lines, b"xy", length=2, close_error=True), content_length=3),
        secondaries=True,
    )
    upload("attempt longer than declared", body_factory(attempt_factory(lines, b"xyz"), content_length=2))
    upload("attempt shorter than declared", body_factory(attempt_factory(lines, b"x", length=2)))
    upload("attempt failing to close", body_factory(attempt_factory(lines, b"ok", close_error=True)), answered=True)
    upload(
        "attempt failing to read and to close",
        body_factory(attempt_factory(lines, RuntimeError("read failed"), close_error=True)),
        secondaries=True,
    )
    upload("attempt that cannot iterate", body_factory(attempt_factory(lines, iter_error=True)))
    exchange.respond(raw_response(200, b"ok", "text/plain"))
    raw = body_factory(attempt_factory(lines, b"raw", length=3), content_type="text/plain")
    record(
        lines, "raw factory", lambda: api.request_raw("POST", "https://hooks.example.com/upload", body=raw).body_bytes
    )
    bodies_module = importlib.import_module(f"{package.__name__}.bodies")
    context = bodies_module.BodyAttemptContext(attempt_index=2, hop_index=1, call_id="own", remaining_timeout=9.5)
    for label, body in (
        ("file", bodies_module.FileBody(io.BytesIO(b"direct"))),
        ("factory", body_factory(attempt_factory(lines, b"direct"))),
    ):
        attempt = body(context)
        lines.append(
            f"  {label} attempt built directly {attempt.content_length} {attempt.content_type} {b''.join(attempt.iter_bytes())!r}"
        )
        attempt.close()
        attempt.close()


async def _async_bodies(package: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    http = exchange.async_client()
    with tempfile.TemporaryDirectory() as directory:
        async with package.AsyncClient(http_client=http) as api:
            await _async_files(package, api, exchange, lines, Path(directory))
            await _async_streams(package, api, exchange, lines)
    await http.aclose()


def _auploader(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> Callable[..., Any]:
    photo = _photo(package)

    async def upload(label: str, body: object, *, answered: bool = False, secondaries: bool = False) -> None:
        if answered:
            exchange.respond(raw_response(200, _PNG, "image/png"))
        call = lambda: api.pets.photos.upload(pet_id=photo, body=body)  # noqa: E731
        if secondaries:
            lines.append(f"  async {label}: {await aoutcome(call)}")
        else:
            await arecord(lines, f"async {label}", call)

    return upload


async def _async_files(package: ModuleType, api: Any, exchange: Exchange, lines: list[str], directory: Path) -> None:
    bodies_module = importlib.import_module(f"{package.__name__}.bodies")
    file_body = bodies_module.AsyncFileBody
    upload = _auploader(package, api, exchange, lines)
    borrowed = io.BytesIO(b"abcdef")
    borrowed.seek(2)
    body = file_body(borrowed)
    await upload("file from its position", body, answered=True)
    lines.append(f"    async borrowed file left open at {borrowed.tell()} {not borrowed.closed}")
    await body.aclose()
    await upload("closed file body", body)
    (directory / "owned.bin").write_bytes(b"owned")
    handle = (directory / "owned.bin").open("rb")
    owned = file_body(handle, ownership="owned")
    await upload("owned file", owned, answered=True)
    lines.append(f"    async owned file closed {handle.closed}")
    await upload("owned file again", owned)
    owned.close()
    once = file_body(_Unseekable(b"once"))
    await upload("file that cannot seek", once, answered=True)
    await upload("file that cannot seek again", once)
    for label, file in (
        ("unreadable file", _Unreadable(b"x")),
        ("file without a position", _Untellable(b"x")),
        ("file longer than its end", _Sized(b"abcdef", 3)),
    ):
        broken = file_body(file)
        await upload(label, broken)
        broken.close()
    interrupted = file_body(_Interrupting(b"x"))
    try:
        await api.pets.photos.upload(pet_id=_photo(package), body=interrupted)
    except Stop:
        lines.append("  async interrupted file propagated")
    interrupted.close()
    path = directory / "photo.png"
    path.write_bytes(_PNG)
    by_path = file_body.from_path(path)
    await upload("path", by_path, answered=True)
    path.write_bytes(b"changed!")
    await upload("changed path", by_path)
    removed = file_body.from_path(path)
    path.unlink()
    await upload("removed path", removed)
    for closing in (by_path, removed):
        await closing.aclose()
    file_body(io.BytesIO(b"never sent")).close()
    await upload("sync file to the async client", bodies_module.FileBody(io.BytesIO(b"x")))


async def _async_streams(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    bodies_module = importlib.import_module(f"{package.__name__}.bodies")
    stream_body, body_factory = bodies_module.AsyncStreamBody, bodies_module.AsyncBodyFactory
    upload = _auploader(package, api, exchange, lines)
    stream = stream_body(Chunks(lines, (b"ab", b"", b"cd")))
    await upload("stream", stream, answered=True)
    await upload("stream again", stream)
    await upload("owned stream", stream_body(Chunks(lines, (b"x",)), ownership="owned"), answered=True)
    await upload("stream of text", stream_body(Chunks(lines, ("text",))))
    await upload("failing stream", stream_body(Chunks(lines, (b"x", RuntimeError("stream failed")))))
    await upload("stream that is not async iterable", stream_body(5))
    made = body_factory(
        async_attempt_factory(lines, b"made", length=4), content_length=4, content_type="image/png", fingerprint=b"f"
    )
    lines.append(f"  async factory declares {made.content_length} {made.content_type} {made.fingerprint!r}")
    await upload("factory", made, answered=True)
    constant = Attempt(lines, (b"c",))

    async def same_attempt(context: Any) -> Attempt:
        return constant

    same = body_factory(same_attempt)
    await upload("factory once", same, answered=True)
    await upload("factory returning the same attempt", same)
    await upload("failing factory", body_factory(_afailing))
    await upload(
        "attempt of another length that fails to close",
        body_factory(async_attempt_factory(lines, b"xy", length=2, close_error=True), content_length=3),
        secondaries=True,
    )
    await upload(
        "attempt of another length", body_factory(async_attempt_factory(lines, b"xy", length=2), content_length=3)
    )
    await upload(
        "attempt failing to close", body_factory(async_attempt_factory(lines, b"ok", close_error=True)), answered=True
    )
    await upload(
        "attempt failing to read and to close",
        body_factory(async_attempt_factory(lines, RuntimeError("read failed"), close_error=True)),
        secondaries=True,
    )
    await upload("attempt that cannot iterate", body_factory(async_attempt_factory(lines, iter_error=True)))
    exchange.respond(raw_response(200, b"ok", "text/plain"))
    raw = body_factory(async_attempt_factory(lines, b"raw", length=3), content_type="text/plain")

    async def raw_call() -> bytes:
        return (await api.request_raw("POST", "https://hooks.example.com/upload", body=raw)).body_bytes

    await arecord(lines, "async raw factory", raw_call)
    try:
        await api.pets.photos.upload(
            pet_id=_photo(package), body=stream_body(Chunks(lines, (b"x", Stop())), ownership="owned")
        )
    except Stop:
        lines.append("  async interrupted owned stream propagated")
    context = bodies_module.BodyAttemptContext(attempt_index=2, hop_index=1, call_id="own", remaining_timeout=None)
    direct = bodies_module.AsyncFileBody(io.BytesIO(b"direct"))
    for label, body in (("file", direct), ("factory", body_factory(async_attempt_factory(lines, b"direct")))):
        attempt = await body(context)
        chunks = [chunk async for chunk in attempt.aiter_bytes()]
        lines.append(f"  async {label} attempt built directly {attempt.content_length} {attempt.content_type} {chunks}")
        await attempt.aclose()
        await attempt.aclose()
    direct.close()
