"""Replay public body inputs through generated clients while retaining their call-owned resources."""

from __future__ import annotations

import importlib
import io
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx2

from tests.data.python.client_runtime import Exchange, arecord, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from types import ModuleType


class _File(io.BytesIO):
    """A real in-memory binary file that counts its owned close and bounded reads."""

    def __init__(self, content: bytes, *, seekable: bool = True) -> None:
        super().__init__(content)
        self.closes = 0
        self.reads: list[int | None] = []
        self.can_seek = seekable

    def seekable(self) -> bool:
        return self.can_seek

    def read(self, size: int | None = -1, /) -> bytes:
        self.reads.append(size)
        return super().read(size)

    def close(self) -> None:
        self.closes += 1
        super().close()


class _Chunks:
    """An owned or borrowed iterable recording its consumption and final cleanup."""

    def __init__(self) -> None:
        self.begins = 0
        self.closes = 0

    def __iter__(self) -> Iterator[bytes]:
        self.begins += 1
        yield b"one-shot"

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for item in self:
            yield item

    def close(self) -> None:
        self.closes += 1

    async def aclose(self) -> None:
        self.close()


class _Attempt:
    """A slotted factory result whose identity must not require hashing or weak references."""

    __slots__ = ("closes", "failure", "length", "payload")

    def __init__(
        self, payload: bytes = b"factory", *, length: int | None = None, failure: BaseException | None = None
    ) -> None:
        self.payload = payload
        self.length = length
        self.failure = failure
        self.closes = 0

    @property
    def content_length(self) -> int | None:
        return self.length

    @property
    def content_type(self) -> None:
        return None

    def iter_bytes(self) -> Iterator[bytes]:
        yield self.payload

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        yield self.payload

    def close(self) -> None:
        self.closes += 1
        if self.failure is not None:
            raise self.failure

    async def aclose(self) -> None:
        self.close()


class _Factory:
    """Return supplied attempt identities or fresh results and record the immutable callback context."""

    def __init__(self, attempts: tuple[_Attempt, ...] = ()) -> None:
        self.supplied = iter(attempts)
        self.attempts: list[_Attempt] = []
        self.contexts: list[tuple[int, int, bool]] = []
        self.ids: list[str] = []

    def __call__(self, context: Any) -> _Attempt:
        self.contexts.append((context.attempt_index, context.hop_index, context.remaining_timeout is not None))
        self.ids.append(context.call_id)
        attempt = next(self.supplied, None)
        if attempt is None:
            attempt = _Attempt(length=7)
        self.attempts.append(attempt)
        return attempt

    async def async_call(self, context: Any) -> _Attempt:
        return self(context)


class _Move:
    """Move a borrowed file after call entry, proving that each send uses its retained entry offset."""

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
                self.requests.append((
                    request.method,
                    request.url.raw_path,
                    request.headers.get("content-type"),
                    request.content,
                ))
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
    """Retain bytes, file offsets, and owned sources through retries and redirect hops."""
    bodies, options = (importlib.import_module(f"{package.__name__}.{name}") for name in ("bodies", "options"))
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
        replies.reset(503, 200)
        record(lines, "bytes retained", lambda: api.retry.post_idempotent(body=b"retained"))
        replies.report(lines)
        file = _File(b"prefix-payload")
        file.seek(7)
        body = bodies.FileBody(file)
        replies.reset(307, 503, 200)
        record(
            lines,
            "borrowed entry offset",
            lambda body=body, file=file: api.retry.post_idempotent(
                body=body, options=options.RequestOptions(hooks=(_Move(file),))
            ),
        )
        replies.report(lines)
        lines.append(f"    file closed={file.closed} offset={file.tell()} reads={file.reads}")
        file.seek(3)
        replies.reset(200)
        record(lines, "borrowed next call offset", lambda body=body: api.retry.post_idempotent(body=body))
        replies.report(lines)
        file.close()
        owned = _File(b"owned")
        body = bodies.FileBody(owned, ownership="owned")
        replies.reset(503, 308, 200)
        record(lines, "owned file final close", lambda body=body: api.retry.post_idempotent(body=body))
        replies.report(lines)
        record(lines, "owned reuse refused", lambda body=body: api.retry.post_idempotent(body=body))
        lines.append(f"    owned closed={owned.closed} closes={owned.closes}")
        for ownership in ("borrowed", "owned"):
            file = _File(b"unseekable", seekable=False)
            body = bodies.FileBody(file, ownership=ownership)
            replies.reset(503, 200)
            record(lines, f"{ownership} unseekable not retried", lambda body=body: api.retry.post_idempotent(body=body))
            replies.report(lines)
            record(lines, f"{ownership} unseekable reuse", lambda body=body: api.retry.post_idempotent(body=body))
            lines.append(f"    file closes={file.closes} reads={file.reads}")
            if not file.closed:
                file.close()
        path = Path(directory) / "body.bin"
        path.write_bytes(b"path body")
        replies.reset(307, 503, 200)
        record(lines, "path reopened", lambda: api.retry.post_idempotent(body=bodies.FileBody.from_path(path)))
        replies.report(lines)
        body = bodies.FileBody.from_path(path)
        replies.reset(503, 200, change=lambda: path.write_bytes(b"changed length"))
        record(lines, "path changed between attempts", lambda body=body: api.retry.post_idempotent(body=body))
        replies.report(lines)
        _streams(api, bodies, replies, lines)
        _factories(api, bodies, replies, lines)
    run(lambda: _async_body_replay(package, bodies, options, lines))


def _streams(api: Any, bodies: ModuleType, replies: _Replies, lines: list[str]) -> None:
    for ownership in ("borrowed", "owned"):
        chunks = _Chunks()
        body = bodies.StreamBody(chunks, ownership=ownership)
        replies.reset(503, 200)
        record(lines, f"{ownership} stream not retried", lambda body=body: api.retry.post_idempotent(body=body))
        replies.report(lines)
        record(lines, f"{ownership} stream reuse", lambda body=body: api.retry.post_idempotent(body=body))
        lines.append(f"    stream begins={chunks.begins} closes={chunks.closes}")
    chunks = _Chunks()
    replies.reset(303, 200)
    record(
        lines,
        "303 drops consumed body",
        lambda: api.retry.post_idempotent(body=bodies.StreamBody(chunks, ownership="owned")),
    )
    replies.report(lines)
    lines.append(f"    stream begins={chunks.begins} closes={chunks.closes}")


def _factories(api: Any, bodies: ModuleType, replies: _Replies, lines: list[str]) -> None:
    factory = _Factory()
    replies.reset(307, 503, 200)
    record(lines, "factory per candidate hop", lambda: api.retry.post_idempotent(body=bodies.BodyFactory(factory)))
    replies.report(lines)
    lines.append(
        f"    contexts={factory.contexts} one_call={len(set(factory.ids)) == 1}"
        f" closes={[item.closes for item in factory.attempts]}"
    )
    a, b = _Attempt(), _Attempt()
    factory = _Factory((a, b, a, a))
    body = bodies.BodyFactory(factory)
    replies.reset(503, 503, 200)
    record(lines, "factory A B A refused", lambda body=body: api.retry.post_idempotent(body=body))
    replies.report(lines)
    record(lines, "factory immediate next call refused", lambda body=body: api.retry.post_idempotent(body=body))
    lines.append(f"    contexts={factory.contexts} closes={(a.closes, b.closes)}")
    a, b = _Attempt(length=7), _Attempt(b"changed", length=8)
    factory = _Factory((a, b))
    replies.reset(503, 200)
    record(
        lines, "factory observed length changed", lambda: api.retry.post_idempotent(body=bodies.BodyFactory(factory))
    )
    replies.report(lines)
    lines.append(f"    closes={(a.closes, b.closes)}")
    factory = _Factory()
    replies.reset(303, 503, 200)
    record(
        lines,
        "bodyless redirect retry restores source",
        lambda: api.retry.post_idempotent(body=bodies.BodyFactory(factory)),
    )
    replies.report(lines)
    lines.append(f"    contexts={factory.contexts} closes={[item.closes for item in factory.attempts]}")


async def _async_body_replay(package: ModuleType, bodies: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange([])
    replies = _Replies(exchange)
    config = options.ClientOptions(
        retry=options.RetryOptions(initial_delay=0),
        redirects=options.RedirectOptions(enabled=True, allow_303_to_get=True),
    )
    with tempfile.TemporaryDirectory() as directory:
        async with exchange.async_client() as native, package.AsyncClient(http_client=native, options=config) as api:
            replies.reset(503, 200)
            await arecord(lines, "async bytes retained", lambda: api.retry.post_idempotent(body=b"retained"))
            replies.report(lines)
            for ownership in ("borrowed", "owned"):
                file = _File(b"prefix-payload")
                file.seek(7)
                body = bodies.AsyncFileBody(file, ownership=ownership)
                replies.reset(307, 503, 200)
                await arecord(
                    lines,
                    f"async {ownership} entry offset",
                    lambda body=body, file=file: api.retry.post_idempotent(
                        body=body, options=options.RequestOptions(hooks=(_Move(file),))
                    ),
                )
                replies.report(lines)
                lines.append(f"    file closed={file.closed} closes={file.closes} reads={file.reads}")
                if ownership == "owned":
                    await arecord(
                        lines, "async owned reuse refused", lambda body=body: api.retry.post_idempotent(body=body)
                    )
                    lines.append(f"    owned closes={file.closes}")
                await body.aclose()
                if not file.closed:
                    file.close()
            file = _File(b"unseekable", seekable=False)
            body = bodies.AsyncFileBody(file, ownership="owned")
            replies.reset(503, 200)
            await arecord(lines, "async unseekable not retried", lambda body=body: api.retry.post_idempotent(body=body))
            replies.report(lines)
            lines.append(f"    file closes={file.closes} reads={file.reads}")
            await body.aclose()
            path = Path(directory) / "async.bin"
            path.write_bytes(b"path body")
            body = bodies.AsyncFileBody.from_path(path)
            replies.reset(503, 308, 200)
            await arecord(lines, "async path reopened", lambda body=body: api.retry.post_idempotent(body=body))
            replies.report(lines)
            replies.reset(503, 200, change=lambda: path.write_bytes(b"changed length"))
            await arecord(lines, "async path changed", lambda body=body: api.retry.post_idempotent(body=body))
            replies.report(lines)
            await body.aclose()
            for ownership in ("borrowed", "owned"):
                chunks = _Chunks()
                body = bodies.AsyncStreamBody(chunks, ownership=ownership)
                replies.reset(503, 200)
                await arecord(
                    lines,
                    f"async {ownership} stream not retried",
                    lambda body=body: api.retry.post_idempotent(body=body),
                )
                replies.report(lines)
                await arecord(
                    lines, f"async {ownership} stream reuse", lambda body=body: api.retry.post_idempotent(body=body)
                )
                lines.append(f"    stream begins={chunks.begins} closes={chunks.closes}")
            await _async_factories(api, bodies, replies, lines)


async def _async_factories(api: Any, bodies: ModuleType, replies: _Replies, lines: list[str]) -> None:
    factory = _Factory()
    replies.reset(307, 503, 200)
    await arecord(
        lines,
        "async factory per candidate hop",
        lambda: api.retry.post_idempotent(body=bodies.AsyncBodyFactory(factory.async_call)),
    )
    replies.report(lines)
    lines.append(
        f"    contexts={factory.contexts} one_call={len(set(factory.ids)) == 1}"
        f" closes={[item.closes for item in factory.attempts]}"
    )
    a, b = _Attempt(), _Attempt()
    factory = _Factory((a, b, a, a))
    body = bodies.AsyncBodyFactory(factory.async_call)
    replies.reset(503, 503, 200)
    await arecord(lines, "async factory A B A refused", lambda body=body: api.retry.post_idempotent(body=body))
    replies.report(lines)
    await arecord(
        lines, "async factory immediate next call refused", lambda body=body: api.retry.post_idempotent(body=body)
    )
    lines.append(f"    contexts={factory.contexts} closes={(a.closes, b.closes)}")
    a, b = _Attempt(length=7), _Attempt(b"changed", length=8)
    factory = _Factory((a, b))
    replies.reset(503, 200)
    await arecord(
        lines,
        "async factory observed length changed",
        lambda: api.retry.post_idempotent(body=bodies.AsyncBodyFactory(factory.async_call)),
    )
    replies.report(lines)
    lines.append(f"    closes={(a.closes, b.closes)}")


def multipart_replay(package: ModuleType, lines: list[str]) -> None:
    """Keep one boundary and encoded layout, and share attempt identities between multipart factories."""
    bodies, options = (importlib.import_module(f"{package.__name__}.{name}") for name in ("bodies", "options"))
    exchange = Exchange([])
    replies = _Replies(exchange)
    config = options.ClientOptions(redirects=options.RedirectOptions(enabled=True))
    with exchange.client() as native, package.Client(http_client=native, options=config) as api:
        file = _File(b"prefix-file")
        file.seek(7)
        factory = _Factory()
        body = bodies.MultipartBody((
            bodies.FieldPart("title", "frozen"),
            bodies.FilePart("file", bodies.FileBody(file, ownership="owned")),
            bodies.FilePart("factory", bodies.BodyFactory(factory)),
            bodies.FilePart("bytes", b"bytes"),
        ))
        replies.reset(307, 308, 200)
        record(
            lines,
            "multipart reopens",
            lambda: api.request_raw("PUT", "https://forms.example.com/parts", body=body).body_bytes,
        )
        lines.append(
            f"    requests={len(replies.requests)} stable_body={len({item[3] for item in replies.requests}) == 1}"
            f" stable_type={len({item[2] for item in replies.requests}) == 1}"
            f" closes={file.closes} factories={factory.contexts}"
        )
        chunks = _Chunks()
        body = bodies.MultipartBody((
            bodies.FieldPart("title", "one shot"),
            bodies.FilePart("stream", bodies.StreamBody(chunks, ownership="owned")),
        ))
        replies.reset(307, 200)
        record(
            lines,
            "multipart one shot blocks redirect",
            lambda: api.request_raw("PUT", "https://forms.example.com/parts", body=body).body_bytes,
        )
        lines.append(
            f"    requests={len(replies.requests)} pending={len(exchange.responders)}"
            f" begins={chunks.begins} closes={chunks.closes}"
        )
        attempt = _Attempt()
        first, second = _Factory((attempt,)), _Factory((attempt,))
        body = bodies.MultipartBody((
            bodies.FilePart("first", bodies.BodyFactory(first)),
            bodies.FilePart("second", bodies.BodyFactory(second)),
        ))
        replies.reset(200)
        record(lines, "multipart shared raw attempt refused", lambda: api.forms.submit_parts(body=body))
        lines.append(
            f"    requests={len(replies.requests)} closes={attempt.closes} contexts={(first.contexts, second.contexts)}"
        )
    run(lambda: _async_multipart_replay(package, bodies, config, lines))


async def _async_multipart_replay(package: ModuleType, bodies: ModuleType, config: object, lines: list[str]) -> None:
    exchange = Exchange([])
    replies = _Replies(exchange)
    async with exchange.async_client() as native, package.AsyncClient(http_client=native, options=config) as api:
        file = _File(b"prefix-file")
        file.seek(7)
        source = bodies.AsyncFileBody(file, ownership="owned")
        factory = _Factory()
        body = bodies.AsyncMultipartBody((
            bodies.FieldPart("title", "frozen"),
            bodies.FilePart("file", source),
            bodies.FilePart("factory", bodies.AsyncBodyFactory(factory.async_call)),
            bodies.FilePart("bytes", b"bytes"),
        ))
        replies.reset(307, 308, 200)
        await arecord(lines, "async multipart reopens", lambda: _async_raw_body(api, body))
        lines.append(
            f"    requests={len(replies.requests)} stable_body={len({item[3] for item in replies.requests}) == 1}"
            f" stable_type={len({item[2] for item in replies.requests}) == 1}"
            f" closes={file.closes} factories={factory.contexts}"
        )
        await source.aclose()
        chunks = _Chunks()
        body = bodies.AsyncMultipartBody((
            bodies.FieldPart("title", "one shot"),
            bodies.FilePart("stream", bodies.AsyncStreamBody(chunks, ownership="owned")),
        ))
        replies.reset(307, 200)
        await arecord(lines, "async multipart one shot blocks redirect", lambda: _async_raw_body(api, body))
        lines.append(
            f"    requests={len(replies.requests)} pending={len(exchange.responders)}"
            f" begins={chunks.begins} closes={chunks.closes}"
        )
        attempt = _Attempt()
        first, second = _Factory((attempt,)), _Factory((attempt,))
        body = bodies.AsyncMultipartBody((
            bodies.FilePart("first", bodies.AsyncBodyFactory(first.async_call)),
            bodies.FilePart("second", bodies.AsyncBodyFactory(second.async_call)),
        ))
        replies.reset(200)
        await arecord(lines, "async multipart shared raw attempt refused", lambda: api.forms.submit_parts(body=body))
        lines.append(
            f"    requests={len(replies.requests)} closes={attempt.closes} contexts={(first.contexts, second.contexts)}"
        )


async def _async_raw_body(api: Any, body: object) -> bytes:
    return (await api.request_raw("PUT", "https://forms.example.com/parts", body=body)).body_bytes
