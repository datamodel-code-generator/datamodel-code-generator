"""Send native binary inputs through real retry and multipart exchanges."""

from __future__ import annotations

import importlib
import io
import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx2

from tests.data.python.client_runtime import Exchange, arecord, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from types import ModuleType

_DATA = Path(__file__).parents[1] / "generation_platform/client/native-bodies.json"


class _File(io.BytesIO):
    """Observe conventional binary reads and caller-owned lifetime."""

    def __init__(self, content: bytes, *, seekable: bool = True) -> None:
        super().__init__(content)
        self.closes = 0
        self.reads: list[int | None] = []
        self.can_seek = seekable

    def seek(self, offset: int, whence: int = 0, /) -> int:
        if not self.can_seek:
            raise io.UnsupportedOperation("not seekable")
        return super().seek(offset, whence)

    def read(self, size: int | None = -1, /) -> bytes:
        self.reads.append(size)
        return super().read(size)

    def write(self, content: bytes, /, *, at_end: bool = False) -> int:
        position = self.tell()
        if at_end:
            super().seek(0, io.SEEK_END)
        written = super().write(content)
        super().seek(position)
        return written

    def close(self) -> None:
        self.closes += 1
        super().close()


class _Chunks:
    """Observe when a caller-owned iterable is consumed."""

    def __init__(self, payload: bytes = b"one-shot") -> None:
        self.payload = payload
        self.begins = 0
        self.closes = 0

    def __iter__(self) -> Iterator[bytes]:
        self.begins += 1
        yield self.payload

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for item in self:
            yield item

    def close(self) -> None:
        self.closes += 1


class _Move:
    """Move the file after entry to exercise its retained initial offset."""

    def __init__(self, file: _File) -> None:
        self.file = file

    def on_event(self, event: Any) -> None:
        if event.name == "call_start":
            self.file.seek(0)


class _Replies:
    """Keep compact observations of real TLS requests and supply the requested status sequence."""

    def __init__(self, exchange: Exchange) -> None:
        self.exchange = exchange
        self.requests: list[tuple[str, bytes, str | None, bytes]] = []

    def reset(self, *statuses: int, change: Callable[[], None] | None = None) -> None:
        self.requests.clear()
        self.exchange.responders.clear()
        for index, status in enumerate(statuses):

            def respond(request: httpx2.Request, status: int = status, index: int = index) -> httpx2.Response:
                self.requests.append(
                    (
                        request.method,
                        request.url.raw_path,
                        request.headers.get("content-type"),
                        request.content,
                    )
                )
                if change is not None and index == 0:
                    change()
                fields = {"Content-Type": "text/plain"}
                if status in {303, 307, 308}:
                    fields["Location"] = f"/hop-{index}"
                return httpx2.Response(status, headers=fields, stream=httpx2.ByteStream(b"ok"))

            self.exchange.respond(respond)

    def report(self, lines: list[str]) -> None:
        lines.append(
            f"    wire={[(method, path, payload) for method, path, _, payload in self.requests]!r}"
            f" pending={len(self.exchange.responders)}"
        )


def body_replay(package: ModuleType, lines: list[str]) -> None:
    """Replay bytes, seekable files and paths without factories or ownership adapters."""
    options = importlib.import_module(f"{package.__name__}.options")
    data = json.loads(_DATA.read_text())
    exchange = Exchange([])
    replies = _Replies(exchange)
    config = options.ClientOptions(
        retry=options.RetryOptions(initial_delay=0),
        redirects=options.RedirectOptions(enabled=True, allow_303_to_get=True),
    )
    with (
        exchange.client() as native,
        package.Client(http_client=native, options=config) as api,
        tempfile.TemporaryDirectory() as directory,
    ):
        replies.reset(*data["retry"])
        record(lines, "bytes retained", lambda: api.retry.post_idempotent(body=data["payload"].encode()))
        replies.report(lines)
        file = _File(data["file"].encode())
        file.seek(data["offset"])
        replies.reset(*data["hops"])
        record(
            lines,
            "borrowed entry offset",
            lambda: api.retry.post_idempotent(body=file, options=options.RequestOptions(hooks=(_Move(file),))),
        )
        replies.report(lines)
        lines.append(f"    caller file open={not file.closed} offset={file.tell()} reads={file.reads}")
        file.seek(data["next_offset"])
        replies.reset(200)
        record(lines, "next call offset", lambda: api.retry.post_idempotent(body=file))
        replies.report(lines)
        file.close()
        grown = _File(data["payload"].encode())
        replies.reset(*data["retry"], change=lambda: grown.write(b"-grown", at_end=True))
        record(lines, "file grown between attempts", lambda: api.retry.post_idempotent(body=grown))
        replies.report(lines)
        lines.append(f"    grown file size={len(grown.getvalue())} reads={grown.reads}")
        grown.close()
        gone = _File(data["payload"].encode())
        replies.reset(*data["retry"], change=gone.close)
        record(lines, "file closed between attempts", lambda: api.retry.post_idempotent(body=gone))
        replies.report(lines)
        path = Path(directory) / "body.bin"
        path.write_bytes(data["payload"].encode())
        replies.reset(*data["hops"])
        record(lines, "native path replay", lambda: api.retry.post_idempotent(body=path))
        replies.report(lines)
        path.unlink()
        for label, body in (
            ("nonseekable file", _File(data["payload"].encode(), seekable=False)),
            ("native iterator", iter((data["payload"].encode(),))),
        ):
            replies.reset(*data["retry"])
            record(lines, label, lambda body=body: api.retry.post_idempotent(body=body))
            replies.report(lines)
        replies.reset(303, 200)
        record(
            lines, "303 drops consumed body", lambda: api.retry.post_idempotent(body=iter((data["payload"].encode(),)))
        )
        replies.report(lines)
    run(lambda: _async_replay(package, options, data, lines))


async def _async_replay(package: ModuleType, options: ModuleType, data: dict[str, Any], lines: list[str]) -> None:
    exchange = Exchange([])
    replies = _Replies(exchange)
    config = options.ClientOptions(
        retry=options.RetryOptions(initial_delay=0), redirects=options.RedirectOptions(enabled=True)
    )
    with tempfile.TemporaryDirectory() as directory:
        async with exchange.async_client() as native, package.AsyncClient(http_client=native, options=config) as api:
            file = _File(data["file"].encode())
            file.seek(data["offset"])
            path = Path(directory) / "async.bin"
            path.write_bytes(data["payload"].encode())
            for label, body in (
                ("async bytes", data["payload"].encode()),
                ("async file", file),
                ("async path", path),
                ("async native iterator", iter((data["payload"].encode(),))),
                ("async native async iterator", _Chunks(data["payload"].encode())),
            ):
                replies.reset(*data["retry"])
                await arecord(lines, label, lambda body=body: api.retry.post_idempotent(body=body))
                replies.report(lines)
            lines.append(f"    async caller file open={not file.closed} offset={file.tell()} reads={file.reads}")
            file.close()
            path.unlink()


def multipart_replay(package: ModuleType, lines: list[str]) -> None:
    """Use the same caller file twice and rewind each part at actual consumption."""
    bodies, options = (importlib.import_module(f"{package.__name__}.{name}") for name in ("bodies", "options"))
    data = json.loads(_DATA.read_text())
    exchange = Exchange([])
    config = options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
    file = _File(data["file"].encode())
    file.seek(data["offset"])
    parts = (bodies.FilePart("first", file), bodies.FilePart("second", file))

    def received(request: httpx2.Request) -> httpx2.Response:
        lines.append(f"    multipart payload count={request.content.count(data['file'][data['offset'] :].encode())}")
        return httpx2.Response(200, headers={"content-type": "text/plain"}, stream=httpx2.ByteStream(b"ok"))

    with exchange.client() as native, package.Client(http_client=native, options=config) as api:
        exchange.respond(received, received)
        record(
            lines,
            "shared file parts",
            lambda: api.request_raw("POST", data["url"], body=bodies.MultipartBody(parts)).read(),
        )
        file.seek(data["offset"])
        record(
            lines,
            "caller file in another call",
            lambda: api.request_raw("POST", data["url"], body=bodies.MultipartBody(parts)).read(),
        )
        lines.append(f"    multipart caller file open={not file.closed}")
    file.close()
    run(lambda: _async_multipart(package, bodies, config, data, lines))


async def _async_multipart(
    package: ModuleType, bodies: ModuleType, config: Any, data: dict[str, Any], lines: list[str]
) -> None:
    exchange = Exchange([])
    file = _File(data["file"].encode())
    file.seek(data["offset"])

    def received(request: httpx2.Request) -> httpx2.Response:
        lines.append(
            f"    async multipart payload count={request.content.count(data['file'][data['offset'] :].encode())}"
        )
        return httpx2.Response(200, headers={"content-type": "text/plain"}, stream=httpx2.ByteStream(b"ok"))

    async with exchange.async_client() as native, package.AsyncClient(http_client=native, options=config) as api:
        exchange.respond(received)
        body = bodies.AsyncMultipartBody((bodies.FilePart("first", file), bodies.FilePart("second", file)))

        async def call() -> object:
            response = await api.request_raw("POST", data["url"], body=body)
            return await response.read()

        await arecord(lines, "async shared file parts", call)
        lines.append(f"    async multipart caller file open={not file.closed}")
    file.close()
