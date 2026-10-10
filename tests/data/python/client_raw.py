"""Read raw and streaming responses of generated clients: saved bodies, one-time streams, files, and closing."""

from __future__ import annotations

import errno
import gzip
import importlib
import io
import os
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NoReturn

import httpx2
import pytest

from tests.data.python.client_regressions import json_error_body
from tests.data.python.client_runtime import (
    Exchange,
    Injected,
    Stop,
    aoutcome,
    arecord,
    argument,
    failing,
    outcome,
    record,
    run,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from types import ModuleType

_PET: Final = b'{"id":3,"name":"fox"}'
_ERROR: Final = b'{"code":7,"message":"boom"}'
_JSON: Final = "application/json"
_LONG_ERROR: Final = (b"0123" * 16384, b"tail")


def _kept(error: Any) -> str:
    """Describe the error a long error body raised by its status and the length and last bytes of its kept prefix."""
    kept = error.body_bytes
    return f"! {type(error).__name__} {error.status_code} kept {len(kept)} {kept[-4:]!r} truncated={error.truncated}"


def _prefix(call: Callable[[], object]) -> str:
    """Describe the error a call raises from a long error body."""
    try:
        call()
    except Exception as error:  # noqa: BLE001
        return _kept(error)
    return "= no error"


async def _aprefix(call: Callable[[], Any]) -> str:
    """Describe the error an asyncio call raises from a long error body."""
    try:
        await call()
    except Exception as error:  # noqa: BLE001
        return _kept(error)
    return "= no error"


def _modules(package: ModuleType) -> tuple[ModuleType, ModuleType, ModuleType, ModuleType]:
    errors, options, responses, types = (
        importlib.import_module(f"{package.__name__}.{name}")
        for name in ("errors", "options", "responses", "types.pets")
    )
    return errors, options, responses, types


def _pet(package: ModuleType) -> object:
    return argument(package, "getPet", "path", "petId", 3)


def _saved(response: Any) -> str:
    """Describe a response whose body is in memory: its status, media type, and its decoded body."""
    info = response.info
    return f"{info.status_code} {info.content_type!r} {response.body_bytes!r}"


def _files(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def _modes(directory: Path) -> list[int]:
    """Return the permissions of the downloads being written in a directory."""
    return [stat.S_IMODE(path.stat().st_mode) for path in directory.glob(".*.part")]


def _private(mode: int) -> bool:
    """Return whether only a file's owner may read it, which Windows permissions cannot tell."""
    return os.name == "nt" or not mode & 0o077


def _broken_link(source: object, target: object) -> NoReturn:
    """Fail to give a completed download its name, as a full disk or a lost volume would."""
    del source, target
    raise OSError(errno.EIO, "The link failed")


class _Stream(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    """Yield chunks, running an action before the chunk at its position, and fail at the end when asked."""

    def __init__(
        self, chunks: tuple[bytes, ...], *, fail: bool = False, action: Callable[[], object] | None = None
    ) -> None:
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


def _streamed(
    status: int, chunks: tuple[bytes, ...], media: str = _JSON, **options: Any
) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of a chunked body; one that fails or acts mid-body runs in-process, as the client reads it."""
    headers = {"content-type": media, **options.pop("headers", {})}
    responder = lambda request: httpx2.Response(status, headers=headers, stream=_Stream(chunks, **options))  # noqa: E731
    return Injected(responder) if options.get("fail") or options.get("action") else responder


def _gzip(status: int, content: bytes, size: int, media: str = _JSON) -> Callable[[httpx2.Request], httpx2.Response]:
    with io.BytesIO() as buffer:
        with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as compressed:
            compressed.write(content)
        coded = buffer.getvalue()
    chunks = tuple(coded[start : start + size] for start in range(0, len(coded), size))
    return _streamed(status, chunks, media, headers={"content-encoding": "gzip"})


def _large(count: int) -> Callable[[httpx2.Request], httpx2.Response]:
    return _streamed(200, (bytes(65536),) * count, "application/octet-stream")


def raw(package: ModuleType, lines: list[str]) -> None:
    """Read raw responses saved and streaming through native HTTPX2 clients."""
    exchange = Exchange(lines)
    http = exchange.client()
    with tempfile.TemporaryDirectory() as directory, package.Client(http_client=http) as api:
        _saved_responses(package, api, exchange, lines)
        _streaming(package, api, exchange, lines)
        _status(package, api, exchange, lines)
        _download(package, api, exchange, lines, Path(directory))
        _requests(api, exchange, lines)
    http.close()
    _handles(package, lines)
    run(lambda: _async_raw(package, lines))


def _saved_responses(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Keep a raw response's whole decoded body in memory, and an error body's prefix; raw bytes are not kept."""
    errors, options, _, types = _modules(package)
    pet, raw = _pet(package), api.pets.with_raw_response
    exchange.respond(_streamed(200, (_PET[:8], _PET[8:])))
    kept = raw.get_pet(petId=pet)
    lines.append(f"  saved {_saved(kept)}")
    pieces = list(kept.iter_bytes())
    lines.append(f"    read {kept.read()!r} text {kept.text()!r} json {kept.json()!r} pieces {pieces}")
    record(lines, "saved raw bytes", kept.iter_raw_bytes)
    kept.close()
    with kept as same:
        lines.append(f"    closed twice {same.read() == _PET} {same.raise_for_status()} {same is kept}")
    exchange.respond(_gzip(200, _PET, 7))
    lines.append(f"  saved gzip {_saved(raw.get_pet(petId=pet))}")
    exchange.respond(_streamed(200, (bytes(70000),), "application/octet-stream"))
    large = raw.get_pet(petId=pet)
    lines.append(f"  saved pieces {[len(piece) for piece in large.iter_bytes()]}")
    trace = argument(package, "listPets", "header", "X-Trace", "t")
    exchange.respond(_streamed(418, (_ERROR,), headers={"x-request-id": "r-1"}))
    listed = raw.list_pets(X_Trace=trace)
    lines.append(f"  saved error {_saved(listed)} {listed.info.request_id}")
    record(lines, "saved error status", listed.raise_for_status)
    record(lines, "saved error status again", listed.raise_for_status)
    exchange.respond(_streamed(500, _LONG_ERROR, "text/plain"))
    once = options.RequestOptions(max_retries=0)
    lines.append(f"  saved truncated status {_prefix(raw.get_pet(petId=pet, options=once).raise_for_status)}")
    exchange.respond(_streamed(302, (), "text/plain", headers={"location": "https://api.example.com/v1/pets/4"}))
    record(lines, "saved redirect status", raw.get_pet(petId=pet).raise_for_status)
    for label, content, media in (
        ("undecodable text", b"\xff\xfe", "text/plain; charset=utf-8"),
        ("latin-1 text", "café".encode("latin-1"), "text/plain; charset=latin-1"),
        ("invalid json", b"{", _JSON),
    ):
        exchange.respond(_streamed(200, (content,), media))
        received = raw.get_pet(petId=pet)
        record(lines, f"saved {label} text", received.text)
        record(lines, f"saved {label} json", received.json)
    body = json_error_body("integer limit")
    exchange.respond(_streamed(200, (body,)))
    received = raw.get_pet(petId=pet)
    try:
        received.json()
    except errors.DecodeError as error:
        lines.append(
            f"  saved integer limit {type(error).__name__} cause={type(error.cause).__name__} "
            f"body={error.body_bytes == body} info={error.info is received.info} "
            f"operation={error.operation_id!r}"
        )
    else:
        lines.append("  saved integer limit decoded")
    for label, responder in (
        ("broken", _streamed(200, (_PET[:8],), fail=True)),
        ("connect", failing(httpx2.ConnectError)),
    ):
        exchange.respond(responder)
        record(lines, f"saved {label}", lambda: raw.get_pet(petId=pet, options=once))


def _streaming(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Read a streaming body once: by iterating it, or into memory first, and refuse any second reading."""
    pet, streaming = _pet(package), api.pets.with_streaming_response
    exchange.respond(_streamed(200, (_PET[:8], _PET[8:])))
    with streaming.get_pet(petId=pet) as response:
        lines.append(f"  streamed {list(response.iter_bytes())} status {response.raise_for_status()}")
        for label, action in (
            ("read", response.read),
            ("text", response.text),
            ("json", response.json),
            ("iter_bytes", response.iter_bytes),
            ("iter_raw_bytes", response.iter_raw_bytes),
            ("body_bytes", lambda: response.body_bytes),
        ):
            record(lines, f"consumed {label}", action)
    exchange.respond(_gzip(200, _PET, 7))
    with streaming.get_pet(petId=pet) as response:
        record(lines, "unread body_bytes", lambda: response.body_bytes)
        lines.append(f"  read first {response.read()!r} {_saved(response)}")
        record(lines, "raw bytes after a read", lambda: list(response.iter_raw_bytes()))
    exchange.respond(_gzip(200, _PET, 7))
    with streaming.get_pet(petId=pet) as response:
        lines.append(f"  streamed coded {list(response.iter_raw_bytes())}")
    exchange.respond(_streamed(200, (_PET[:8], _PET[8:15], _PET[15:])))
    with streaming.get_pet(petId=pet) as response:
        chunks = response.iter_bytes()
        lines.append(f"  streamed first {next(chunks)!r}")
        record(lines, "streaming read", response.read)
        response.close()
        record(lines, "closed read", response.read)
    exchange.respond(_large(1600))
    with streaming.get_pet(petId=pet) as response:
        lines.append(f"  streamed 100 MiB {sum(len(chunk) for chunk in response.iter_bytes())}")
    exchange.respond(_streamed(200, (_PET[:8],), fail=True))
    with streaming.get_pet(petId=pet) as response:
        received: list[bytes] = []
        record(lines, "streamed broken", lambda: received.extend(response.iter_bytes()))
        lines.append(f"    passed {received}")
        record(lines, "streamed broken read", response.read)
    exchange.respond(_streamed(200, (_PET,)))
    try:
        with streaming.get_pet(petId=pet) as response:
            raise Stop
    except Stop:
        record(lines, "stopped block read", response.read)
    exchange.respond(*(failing(httpx2.ConnectError) for _ in range(3)))
    record(lines, "streaming connect", lambda: _entered(streaming.get_pet(petId=pet)))


def _entered(manager: Any) -> object:
    with manager as response:
        return response.info.status_code


def _status(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Raise the typed failure of a streaming response from the 64 KiB prefix of its error body, then close it."""
    errors, options, _, _ = _modules(package)
    pet, streaming = _pet(package), api.pets.with_streaming_response
    once = options.RequestOptions(max_retries=0)
    exchange.respond(_streamed(500, _LONG_ERROR, "text/plain"))
    with streaming.get_pet(petId=pet, options=once) as response:
        lines.append(f"  streaming truncated status {_prefix(response.raise_for_status)}")
        record(lines, "streaming truncated read", response.read)
    for label, responder in (
        ("error", _streamed(404, (b"miss", b"ing"), "text/plain")),
        ("broken coding", _streamed(500, (b"not gzip",), "text/plain", headers={"content-encoding": "gzip"})),
        ("broken read", _streamed(500, (b"012",), "text/plain", fail=True)),
    ):
        exchange.respond(responder)
        try:
            with streaming.get_pet(petId=pet, options=once) as response:
                record(lines, f"streaming {label} status", response.raise_for_status)
                record(lines, f"streaming {label} read", response.read)
        except errors.APIConnectionError as error:
            lines.append(f"  streaming {label} acquisition {type(error).__name__}")
    exchange.respond(_streamed(404, (b"miss", b"ing"), "text/plain"))
    with streaming.get_pet(petId=pet) as response:
        next(response.iter_raw_bytes())
        record(lines, "partly read status", response.raise_for_status)


def _download(package: ModuleType, api: Any, exchange: Exchange, lines: list[str], directory: Path) -> None:
    """Write a body to a borrowed file object, or to a path through a temporary file that success moves there."""
    pet, streaming = _pet(package), api.pets.with_streaming_response
    target, raced = directory / "pet.json", directory / "raced.json"
    sink = io.BytesIO()
    exchange.respond(_streamed(200, (_PET[:8], _PET[8:])), _streamed(200, (_PET[:8], _PET[8:])))
    with streaming.get_pet(petId=pet) as response:
        response.stream_to(sink)
    with streaming.get_pet(petId=pet) as response:
        response.stream_to(target)
    plain = directory / "plain.json"
    plain.write_bytes(b"")
    permissions = stat.S_IMODE(target.stat().st_mode) == stat.S_IMODE(plain.stat().st_mode)
    plain.unlink()
    lines.append(f"  downloaded {sink.getvalue()!r} open {not sink.closed} {target.read_bytes()!r} {_files(directory)}")
    lines.append(f"  downloaded with the permissions of a new file {permissions}")
    exchange.respond(_streamed(200, (b"{}",)), _streamed(200, (b"[]",)), _streamed(200, (b"{", b"}"), fail=True))
    with streaming.get_pet(petId=pet) as response:
        lines.append(f"  existing {outcome(lambda: response.stream_to(target))} then read {response.read()!r}")
    with streaming.get_pet(petId=pet) as response:
        response.stream_to(str(target), overwrite=True)
    with streaming.get_pet(petId=pet) as response:
        lines.append(f"  overwrite broken {outcome(lambda: response.stream_to(target, overwrite=True))}")
    lines.append(f"  kept {target.read_bytes()!r} {_files(directory)}")
    exchange.respond(_streamed(200, (b"{", b"}"), action=lambda: raced.write_bytes(b"first")))
    with streaming.get_pet(petId=pet) as response:
        lines.append(f"  raced {outcome(lambda: response.stream_to(raced))}")
    lines.append(f"  raced kept {raced.read_bytes()!r} {_files(directory)}")
    writing: list[int] = []
    exchange.respond(_streamed(200, (b"{", b"}"), action=lambda: writing.extend(_modes(directory))))
    with streaming.get_pet(petId=pet) as response:
        response.stream_to(directory / "private.json")
    lines.append(f"  downloading readable by its owner alone {bool(writing) and all(map(_private, writing))}")
    failed = directory / "failed.json"
    exchange.respond(_streamed(200, (b"{}",)), _streamed(200, (b"{}",)))
    with streaming.get_pet(petId=pet) as response, pytest.MonkeyPatch.context() as fault:
        fault.setattr(os, "link", _broken_link)
        lines.append(f"  move failed {outcome(lambda: response.stream_to(failed))} {_files(directory)}")
    with streaming.get_pet(petId=pet) as response:
        response.stream_to(failed)
    lines.append(f"  moved again {failed.read_bytes()!r} {_files(directory)}")


def _requests(api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send raw requests to any absolute URL, saved or streaming, and refuse invalid ones before sending."""
    exchange.respond(
        _streamed(200, (b"pong",), "text/plain"),
        *(_streamed(503, (b"down",), "text/plain") for _ in range(3)),
        _streamed(200, (b"str", b"eam")),
    )
    lines.append(f"  request {_saved(api.request_raw('POST', 'https://hooks.example.com/ping?x=1', body=b'ping'))}")
    record(lines, "request status", api.request_raw("GET", "https://hooks.example.com/down").raise_for_status)
    with api.with_streaming_response.request_raw("GET", "https://hooks.example.com/stream") as response:
        lines.append(f"  request streamed {list(response.iter_bytes())}")
    for label, method, url, body in (
        ("method", "GE T", "https://x.example.com", b""),
        ("method type", 1, "https://x.example.com", b""),
        ("relative url", "GET", "/pets", b""),
        ("url scheme", "GET", "ftp://x.example.com", b""),
        ("url userinfo", "GET", "https://user@x.example.com", b""),
        ("url port", "GET", "https://x.example.com:99999", b""),
        ("url type", "GET", b"https://x.example.com", b""),
        ("body type", "POST", "https://x.example.com", "text"),
    ):
        record(
            lines,
            f"request {label}",
            lambda method=method, url=url, body=body: api.request_raw(method, url, body=body),
        )
    for label, url in (
        ("url fragment", "https://x.example.com/#top"),
        ("query fragment", "https://x.example.com/?a=1#top"),
    ):
        exchange.respond(_streamed(200, (b"fragment omitted",), "text/plain"))
        record(lines, f"request {label}", lambda url=url: _saved(api.request_raw("get", url)))


def _handles(package: ModuleType, lines: list[str]) -> None:
    """A root closes only its created client; a borrowed stream owns its own response."""
    exchange = Exchange(lines)
    exchange.respond(_streamed(200, (_PET,), _JSON), _streamed(200, (_PET[:5], _PET[5:]), _JSON))
    with exchange.client() as native, package.Client(http_client=native) as api:
        pet = _pet(package)
        saved = api.pets.with_raw_response.get_pet(petId=pet)
        view = api.with_options()
        with view.pets.with_streaming_response.get_pet(petId=pet) as held:
            api.close()
            lines.append(f"  borrowed native closed={native.is_closed}")
            record(lines, "held response after SDK close", held.read)
            record(lines, "closed root refuses new calls", lambda: api.pets.get_pet(petId=pet))
        lines.append(f"  saved after close={saved.read()!r}")
        api.close()


def _read_in_block(api: Any, pet: object) -> object:
    with api.pets.with_streaming_response.get_pet(petId=pet) as response:
        return response.read()


def _switch_in_block(api: Any, pet: object) -> object:
    with api.pets.with_streaming_response.get_pet(petId=pet) as response:
        next(response.iter_bytes())
        return response.read()


async def _async_raw(package: ModuleType, lines: list[str]) -> None:
    """Read raw responses of an asyncio client: the same saved, streaming, and closing behaviour."""
    exchange = Exchange(lines)
    http = exchange.async_client()
    with tempfile.TemporaryDirectory() as directory:
        async with package.AsyncClient(http_client=http) as api:
            await _async_saved(package, api, exchange, lines)
            await _async_streaming(package, api, exchange, lines, Path(directory))
            await _async_requests(api, exchange, lines)
    await http.aclose()
    await _async_handles(package, lines)


async def _async_saved(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    errors, options, _, _ = _modules(package)
    pet, raw = _pet(package), api.pets.with_raw_response
    exchange.respond(_streamed(200, (_PET[:8], _PET[8:])))
    kept = await raw.get_pet(petId=pet)
    pieces = [piece async for piece in kept.iter_bytes()]
    lines.append(
        f"  async saved {_saved(kept)} {await kept.read()!r} {await kept.text()!r} {await kept.json()!r} {pieces}"
    )
    record(lines, "async saved raw bytes", kept.iter_raw_bytes)
    await kept.aclose()
    async with kept as same:
        lines.append(f"    closed twice {await same.raise_for_status()} {same is kept}")
    exchange.respond(_gzip(200, _PET, 7))
    lines.append(f"  async saved gzip {_saved(await raw.get_pet(petId=pet))}")
    exchange.respond(_streamed(500, _LONG_ERROR, "text/plain"))
    once = options.RequestOptions(max_retries=0)
    failed = await raw.get_pet(petId=pet, options=once)
    lines.append(f"  async saved status {await _aprefix(failed.raise_for_status)}")
    body = json_error_body("integer limit")
    exchange.respond(_streamed(200, (body,)))
    received = await raw.get_pet(petId=pet)
    try:
        await received.json()
    except errors.DecodeError as error:
        lines.append(
            f"  async saved integer limit {type(error).__name__} cause={type(error.cause).__name__} "
            f"body={error.body_bytes == body} info={error.info is received.info} "
            f"operation={error.operation_id!r}"
        )
    else:
        lines.append("  async saved integer limit decoded")
    for label, responder in (
        ("broken", _streamed(200, (_PET[:8],), fail=True)),
        ("connect", failing(httpx2.ConnectError)),
    ):
        exchange.respond(responder)
        await arecord(lines, f"async saved {label}", lambda: raw.get_pet(petId=pet, options=once))


async def _async_streaming(
    package: ModuleType, api: Any, exchange: Exchange, lines: list[str], directory: Path
) -> None:
    errors, options, _, _ = _modules(package)
    pet, streaming = _pet(package), api.pets.with_streaming_response
    exchange.respond(_streamed(200, (_PET[:8], _PET[8:])))
    async with streaming.get_pet(petId=pet) as response:
        lines.append(f"  async streamed {[chunk async for chunk in response.iter_bytes()]}")
        for label, action in (("read", response.read), ("text", response.text), ("json", response.json)):
            await arecord(lines, f"async consumed {label}", action)
        record(lines, "async consumed iter_raw_bytes", response.iter_raw_bytes)
    exchange.respond(_gzip(200, _PET, 7), _gzip(200, _PET, 7))
    async with streaming.get_pet(petId=pet) as response:
        lines.append(f"  async read first {await response.read()!r} {_saved(response)}")
    async with streaming.get_pet(petId=pet) as response:
        lines.append(f"  async streamed coded {[chunk async for chunk in response.iter_raw_bytes()]}")
    exchange.respond(_streamed(200, (_PET[:8],), fail=True))
    async with streaming.get_pet(petId=pet) as response:
        await arecord(lines, "async streamed broken", lambda: _alist(response.iter_bytes()))
    once = options.RequestOptions(max_retries=0)
    exchange.respond(_streamed(500, _LONG_ERROR, "text/plain"))
    async with streaming.get_pet(petId=pet, options=once) as response:
        lines.append(f"  async streaming truncated status {await _aprefix(response.raise_for_status)}")
    for label, responder in (
        ("error", _streamed(404, (b"miss", b"ing"), "text/plain")),
        ("broken coding", _streamed(500, (b"not gzip",), "text/plain", headers={"content-encoding": "gzip"})),
        ("broken read", _streamed(500, (b"012",), "text/plain", fail=True)),
    ):
        exchange.respond(responder)
        try:
            async with streaming.get_pet(petId=pet, options=once) as response:
                await arecord(lines, f"async streaming {label} status", response.raise_for_status)
        except errors.APIConnectionError as error:
            lines.append(f"  async streaming {label} acquisition {type(error).__name__}")
    exchange.respond(_streamed(404, (b"miss", b"ing"), "text/plain"))
    async with streaming.get_pet(petId=pet) as response:
        await anext(response.iter_raw_bytes())
        await arecord(lines, "async partly read status", response.raise_for_status)
    exchange.respond(_streamed(200, (_PET,)))
    try:
        async with streaming.get_pet(petId=pet) as response:
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
    async with streaming.get_pet(petId=pet) as response:
        await response.stream_to(sink)
    async with streaming.get_pet(petId=pet) as response:
        await response.stream_to(target)
    async with streaming.get_pet(petId=pet) as response:
        lines.append(f"  async existing {await aoutcome(lambda: response.stream_to(target))} {await response.read()!r}")
    async with streaming.get_pet(petId=pet) as response:
        lines.append(f"  async overwrite broken {await aoutcome(lambda: response.stream_to(target, overwrite=True))}")
    async with streaming.get_pet(petId=pet) as response:
        lines.append(f"  async raced {await aoutcome(lambda: response.stream_to(raced))}")
    lines.append(
        f"  async downloaded {sink.getvalue()!r} {target.read_bytes()!r} {raced.read_bytes()!r} {_files(directory)}"
    )
    failed = directory / "failed.json"
    exchange.respond(_streamed(200, (b"{}",)), _streamed(200, (b"{}",)))
    async with streaming.get_pet(petId=pet) as response:
        with pytest.MonkeyPatch.context() as fault:
            fault.setattr(os, "link", _broken_link)
            lines.append(
                f"  async move failed {await aoutcome(lambda: response.stream_to(failed))} {_files(directory)}"
            )
    async with streaming.get_pet(petId=pet) as response:
        await response.stream_to(failed)
    lines.append(f"  async moved again {failed.read_bytes()!r} {_files(directory)}")


async def _async_requests(api: Any, exchange: Exchange, lines: list[str]) -> None:
    exchange.respond(_streamed(503, (b"down",), "text/plain"), _streamed(200, (b"str", b"eam")))
    failed = await api.request_raw("POST", "https://hooks.example.com/ping", body=b"ping")
    await arecord(lines, "async request status", failed.raise_for_status)
    async with api.with_streaming_response.request_raw("GET", "https://hooks.example.com/stream") as response:
        lines.append(f"  async request streamed {await _alist(response.iter_bytes())}")
    await arecord(lines, "async request url", lambda: api.request_raw("GET", "/pets"))


async def _async_handles(package: ModuleType, lines: list[str]) -> None:
    """A root closes only its created client; a borrowed stream owns its own response."""
    exchange = Exchange(lines)
    exchange.respond(_streamed(200, (_PET,), _JSON), _streamed(200, (_PET[:5], _PET[5:]), _JSON))
    async with exchange.async_client() as native, package.AsyncClient(http_client=native) as api:
        pet = _pet(package)
        saved = await api.pets.with_raw_response.get_pet(petId=pet)
        view = api.with_options()
        async with view.pets.with_streaming_response.get_pet(petId=pet) as held:
            await api.aclose()
            lines.append(f"  async borrowed native closed={native.is_closed}")
            await arecord(lines, "async held response after SDK close", held.read)
            await arecord(lines, "async closed root refuses new calls", lambda: api.pets.get_pet(petId=pet))
        lines.append(f"  async saved after close={await saved.read()!r}")
        await api.aclose()


async def _aread_in_block(api: Any, pet: object) -> object:
    async with api.pets.with_streaming_response.get_pet(petId=pet) as response:
        return await response.read()


async def _aswitch_in_block(api: Any, pet: object) -> object:
    async with api.pets.with_streaming_response.get_pet(petId=pet) as response:
        await anext(response.iter_bytes())
        return await response.read()
