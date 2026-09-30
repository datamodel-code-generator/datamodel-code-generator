"""Sign generated requests with exact wire-body digests and observe their replay and ownership."""

from __future__ import annotations

import errno
import hashlib
import importlib
import io
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx2

from tests.data.python.client_body_replay import _Chunks, _File
from tests.data.python.client_runtime import Exchange, arecord, describe, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
    from types import ModuleType


class _Payload:
    """A caller-owned declaration whose returned attempt records any premature payload read."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.begins = 0
        self.closes = 0

    @property
    def content_length(self) -> int:
        return len(self.payload)

    @property
    def content_type(self) -> None:
        return None

    def iter_bytes(self) -> Iterator[bytes]:
        self.begins += 1
        yield self.payload

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        self.begins += 1
        yield self.payload

    def close(self) -> None:
        self.closes += 1

    async def aclose(self) -> None:
        self.close()


class _Factory:
    """Provide fresh payload attempts while retaining their externally visible observations."""

    def __init__(self, payload: bytes = b"factory") -> None:
        self.payload = payload
        self.attempts: list[_Payload] = []
        self.contexts: list[tuple[int, int]] = []

    def __call__(self, context: Any) -> _Payload:
        self.contexts.append((context.attempt_index, context.hop_index))
        attempt = _Payload(self.payload)
        self.attempts.append(attempt)
        return attempt

    async def async_call(self, context: Any) -> _Payload:
        return self(context)


class _Signing:
    """Observe restored offsets and unread factory attempts, returning explicit signature headers."""

    def __init__(self, auth: ModuleType, *, required: bool = True, suffix: str = "") -> None:
        self.auth = auth
        self.header = f"x-body-digest{suffix}"
        self.nonce = f"x-body-nonce{suffix}"
        self._capabilities = auth.SignerCapabilities(
            allowed_origins=("https://api.example.com",),
            managed_headers=(self.header, self.nonce),
            managed_query=(),
            requires_body_digest=required,
        )
        self.calls: list[tuple[object, ...]] = []
        self.file: _File | None = None
        self.offset = 0
        self.factory: _Factory | None = None

    @property
    def capabilities(self) -> Any:
        return self._capabilities

    def reset(self, *, file: _File | None = None, offset: int = 0, factory: _Factory | None = None) -> None:
        self.calls.clear()
        self.file, self.offset, self.factory = file, offset, factory

    def observe(self, request: Any) -> Any:
        digest = None if request.body_digest is None else request.body_digest.hex()
        restored = self.file is None or self.file.tell() == self.offset
        unread = self.factory is None or self.factory.attempts[-1].begins == 0
        self.calls.append((request.method, request.attempt_index, request.hop_index, digest, restored, unread))
        return self.auth.SignatureFields(
            headers=((self.header, "none" if digest is None else digest), (self.nonce, str(len(self.calls)))),
            query=(),
        )


class _Signer(_Signing):
    def sign(self, request: Any) -> Any:
        return self.observe(request)


class _AsyncSigner(_Signing):
    async def sign(self, request: Any) -> Any:
        return self.observe(request)


class _Faulty(_File):
    """A borrowed file whose descriptor, reads or seeks fail, or whose reads stall past a time, once hashing reads it."""

    def __init__(self, *faults: str) -> None:
        super().__init__(b"prefix-" + b"f" * 65549)
        self.faults = faults
        self.until = 0.0
        self.hashing = False

    def fileno(self) -> int:
        if "fileno" in self.faults:
            msg = "descriptor lost"
            raise OSError(msg)
        return super().fileno()

    def read(self, size: int | None = -1, /) -> bytes:
        self.hashing = True
        if "stall" in self.faults:
            time.sleep(max(0.0, self.until - time.monotonic()))
        if "read" in self.faults:
            msg = "read failed"
            raise OSError(msg)
        return super().read(size)

    def seek(self, offset: int, whence: int = 0, /) -> int:
        if self.hashing and "seek" in self.faults:
            msg = "seek failed"
            raise OSError(msg)
        return super().seek(offset, whence)


class _Vanishing(io.FileIO):
    """A real file whose descriptor stops resolving once hashing has read it."""

    def __init__(self, path: Path) -> None:
        super().__init__(path, "rb")
        self.hashing = False

    def read(self, size: int = -1, /) -> bytes:
        self.hashing = True
        return super().read(size)

    def fileno(self) -> int:
        if self.hashing:
            raise OSError(errno.EBADF, "descriptor lost")
        return super().fileno()


def _faulted(error: BaseException | None) -> str:
    if error is None:
        return "returned"
    return f"! {describe(error)} secondary={[repr(item) for item in getattr(error, 'secondary_errors', ())]}"


def _failure(call: Callable[[], object]) -> str:
    try:
        call()
    except Exception as error:  # noqa: BLE001
        return _faulted(error)
    return _faulted(None)


async def _afailure(call: Callable[[], Awaitable[object]]) -> str:
    try:
        await call()
    except Exception as error:  # noqa: BLE001
        return _faulted(error)
    return _faulted(None)


def _faults() -> tuple[tuple[str, _Faulty, float], ...]:
    return (
        ("descriptor lookup fails before hashing", _Faulty("fileno"), 60.0),
        ("seek back fails after hashing", _Faulty("seek"), 60.0),
        ("read and seek back fail while hashing", _Faulty("read", "seek"), 60.0),
        ("deadline while hashing", _Faulty("stall"), 0.5),
    )


class _Replies:
    """Hash the bytes a real TLS server receives, independently of the signing input."""

    def __init__(self, exchange: Exchange) -> None:
        self.exchange = exchange
        self.requests: list[httpx2.Request] = []

    def reset(self, *statuses: int) -> None:
        self.requests.clear()
        self.exchange.responders.clear()
        for index, status in enumerate(statuses):

            def respond(request: httpx2.Request, *, status: int = status, index: int = index) -> httpx2.Response:
                self.requests.append(request)
                headers = {"Content-Type": "application/octet-stream"}
                if status in {303, 307, 308}:
                    headers["Location"] = f"/hop-{index}"
                return httpx2.Response(status, headers=headers, content=b"ok")

            self.exchange.respond(respond)

    def report(self, lines: list[str], signer: _Signing) -> None:
        wire = []
        for request in self.requests:
            expected = hashlib.sha256(request.content).hexdigest()
            supplied = request.headers.get(signer.header)
            matched = None if supplied in {None, "none"} else supplied == expected
            wire.append((request.method, request.url.raw_path, len(request.content), matched))
        labels: dict[object, str] = {}
        signed = [
            (*call[:3], None if call[3] is None else labels.setdefault(call[3], f"digest{len(labels)}"), *call[4:])
            for call in signer.calls
        ]
        lines.append(f"    signed={signed}")
        lines.append(f"    wire={wire} pending={len(self.exchange.responders)}")


def _configuration(auth: ModuleType, options: ModuleType, signer: _Signing) -> Any:
    return options.ClientOptions(
        auth=auth.AuthConfig(
            {}, send_on_anonymous=True, allowed_origins=("https://api.example.com",), signers=(signer,)
        ),
        retry=options.RetryOptions(initial_delay=0),
        redirects=options.RedirectOptions(enabled=True, allow_303_to_get=True),
    )


def body_digest(package: ModuleType, lines: list[str]) -> None:
    """Check digest vectors, exact multipart wire bytes, replay, declared factories, and unsupported sources."""
    auth, bodies, options = (importlib.import_module(f"{package.__name__}.{name}") for name in ("auth", "bodies", "options"))
    exchange = Exchange([])
    replies = _Replies(exchange)
    signer = _Signer(auth)
    with (
        exchange.client() as native,
        package.Client(http_client=native, options=_configuration(auth, options, signer)) as api,
        tempfile.TemporaryDirectory() as directory,
    ):
        replies.reset(200)
        record(lines, "bodyless", api.auth.signed_body)
        replies.report(lines, signer)
        for label, payload in (("empty", b""), ("abc", b"abc"), ("large bytes", b"x" * 131089)):
            signer.reset()
            replies.reset(200)
            record(lines, label, lambda payload=payload: api.auth.signed_body(body=payload))
            replies.report(lines, signer)
        signer.reset()
        replies.reset(303, 503, 200)
        record(lines, "bodyless hop then original replay", lambda: api.auth.signed_body(body=b"abc"))
        replies.report(lines, signer)
        for ownership in ("borrowed", "owned"):
            file = _File(b"prefix-" + b"f" * 65549)
            file.seek(7)
            signer.reset(file=file, offset=7)
            replies.reset(503, 307, 200)
            record(
                lines,
                f"{ownership} file digest replay",
                lambda file=file, ownership=ownership: api.auth.signed_body(
                    body=bodies.FileBody(file, ownership=ownership)
                ),
            )
            replies.report(lines, signer)
            lines.append(f"    closed={file.closed} closes={file.closes} reads={file.reads}")
            if not file.closed:
                file.close()
        file = _File(b"x")
        file.seek(2)
        signer.reset(file=file, offset=2)
        replies.reset(200)
        record(lines, "past EOF digest", lambda: api.auth.signed_body(body=bodies.FileBody(file)))
        replies.report(lines, signer)
        lines.append(f"    final offset={file.tell()} borrowed_open={not file.closed}")
        file.close()
        path = Path(directory) / "signed.bin"
        path.write_bytes(b"path payload" * 6000)
        signer.reset()
        replies.reset(503, 200)
        record(lines, "path digest replay", lambda: api.auth.signed_body(body=bodies.FileBody.from_path(path)))
        replies.report(lines, signer)
        _sync_factories(api, auth, bodies, options, replies, signer, lines)
        _sync_multipart(api, auth, bodies, options, replies, signer, path, lines)
        _sync_unsigned(api, auth, bodies, options, replies, signer, lines)
        _sync_faults(api, bodies, options, replies, signer, path, lines)
    run(lambda: _async_digest(package, auth, bodies, options, lines))


def _sync_faults(
    api: Any, bodies: ModuleType, options: ModuleType, replies: _Replies, signer: _Signing, path: Path, lines: list[str]
) -> None:
    for label, file, seconds in _faults():
        file.seek(7)
        signer.reset(file=file, offset=7)
        replies.reset(200)
        deadline = options.Deadline.after(seconds)
        file.until = deadline.at + 0.05
        outcome = _failure(
            lambda file=file, deadline=deadline: api.auth.signed_body(
                body=bodies.FileBody(file), options=options.RequestOptions(deadline=deadline)
            )
        )
        lines.append(f"  {label} {outcome}")
        replies.report(lines, signer)
        lines.append(f"    offset={file.tell()} reads={len(file.reads)}")
        file.close()
    with _Vanishing(path) as vanishing:
        signer.reset()
        replies.reset(200)
        outcome = _failure(lambda: api.auth.signed_body(body=bodies.FileBody(vanishing)))
        lines.append(f"  descriptor lost while hashing {outcome}")
        replies.report(lines, signer)
        lines.append(f"    offset={vanishing.tell()}")


def _sync_factories(
    api: Any, auth: ModuleType, bodies: ModuleType, options: ModuleType, replies: _Replies, signer: _Signing, lines: list[str]
) -> None:
    factory = _Factory()
    signer.reset(factory=factory)
    replies.reset(503, 308, 200)
    record(
        lines,
        "declared factory digest",
        lambda: api.auth.signed_body(body=bodies.BodyFactory(factory, sha256=hashlib.sha256(factory.payload).digest())),
    )
    replies.report(lines, signer)
    lines.append(f"    contexts={factory.contexts} attempts={[(item.begins, item.closes) for item in factory.attempts]}")
    factory = _Factory()
    signer.reset(factory=factory)
    replies.reset(200)
    record(
        lines,
        "fingerprint is not a digest",
        lambda: api.auth.signed_body(body=bodies.BodyFactory(factory, fingerprint=hashlib.sha256(factory.payload).digest())),
    )
    replies.report(lines, signer)
    lines.append(f"    factories={len(factory.attempts)}")
    for value in (b"", b"x" * 31, b"x" * 33, "x" * 32, bytearray(32)):
        record(lines, f"invalid digest {type(value).__name__} {len(value)}", lambda value=value: bodies.BodyFactory(factory, sha256=value))
    opaque = _Signer(auth, required=False)
    replies.reset(200)
    record(
        lines,
        "factory without required digest",
        lambda: api.auth.signed_body(
            body=bodies.BodyFactory(factory), options=options.RequestOptions(auth=_configuration(auth, options, opaque).auth)
        ),
    )
    replies.report(lines, opaque)
    lines.append(f"    attempts={[(item.begins, item.closes) for item in factory.attempts]}")


def _sync_multipart(
    api: Any, auth: ModuleType, bodies: ModuleType, options: ModuleType, replies: _Replies, signer: _Signing,
    path: Path, lines: list[str],
) -> None:
    for kind in ("bytes", "file", "path"):
        file = _File(b"prefix-part")
        file.seek(7)
        content = b"part" if kind == "bytes" else bodies.FileBody(file)
        if kind == "path":
            content = bodies.FileBody.from_path(path)
        signer.reset(file=file if kind == "file" else None, offset=7)
        body = bodies.MultipartBody((
            bodies.FieldPart("note", "日本語 \"note\""),
            bodies.FilePart("file", content, filename="résumé.txt"),
        ))
        replies.reset(503, 307, 200)
        record(lines, f"multipart {kind} exact wire", lambda body=body: api.auth.signed_multipart(body=body))
        replies.report(lines, signer)
        lines.append(
            f"    stable_body={len({request.content for request in replies.requests}) == 1}"
            f" stable_type={len({request.headers['content-type'] for request in replies.requests}) == 1}"
        )
        file.close()
    factory = _Factory()
    signer.reset(factory=factory)
    body = bodies.MultipartBody((bodies.FilePart("file", bodies.BodyFactory(factory, sha256=hashlib.sha256(factory.payload).digest())),))
    replies.reset(200)
    record(lines, "part digest is not multipart digest", lambda: api.auth.signed_multipart(body=body))
    replies.report(lines, signer)
    lines.append(f"    factories={len(factory.attempts)}")
    opaque = _Signer(auth, required=False)
    record(
        lines,
        "multipart factory without required digest",
        lambda: api.auth.signed_multipart(body=body, options=options.RequestOptions(auth=_configuration(auth, options, opaque).auth)),
    )
    replies.report(lines, opaque)
    for composite in (False, True):
        for kind in ("stream", "file"):
            chunks, file = _Chunks(), _File(b"one-shot", seekable=False)
            content = bodies.StreamBody(chunks, ownership="owned") if kind == "stream" else bodies.FileBody(file, ownership="owned")
            body = bodies.MultipartBody((bodies.FilePart("file", content),)) if composite else content
            signer.reset()
            replies.reset(200)
            call = api.auth.signed_multipart if composite else api.auth.signed_body
            record(lines, f"unsupported {'multipart' if composite else 'body'} {kind}", lambda body=body, call=call: call(body=body))
            replies.report(lines, signer)
            lines.append(f"    begins={chunks.begins} stream_closes={chunks.closes} file_reads={file.reads} file_closes={file.closes}")
            if not file.closed:
                file.close()


def _sync_unsigned(
    api: Any, auth: ModuleType, bodies: ModuleType, options: ModuleType, replies: _Replies, signer: _Signing, lines: list[str]
) -> None:
    for signing in (False, True):
        file = _File(b"abc")
        observer = _Signer(auth, required=False)
        signer.reset()
        replies.reset(200)
        record(
            lines,
            f"no hash {'signer' if signing else 'anonymous'}",
            lambda file=file, observer=observer, signing=signing: api.auth.signed_body(
                body=bodies.FileBody(file),
                options=options.RequestOptions(auth=_configuration(auth, options, observer).auth if signing else None),
            ),
        )
        replies.report(lines, observer)
        lines.append(f"    reads={file.reads} borrowed_open={not file.closed}")
        file.close()


async def _async_digest(package: ModuleType, auth: ModuleType, bodies: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange([])
    replies = _Replies(exchange)
    signer = _AsyncSigner(auth)
    with tempfile.TemporaryDirectory() as directory:
        async with exchange.async_client() as native, package.AsyncClient(http_client=native, options=_configuration(auth, options, signer)) as api:
            replies.reset(200)
            await arecord(lines, "async bodyless", api.auth.signed_body)
            replies.report(lines, signer)
            for label, payload in (("empty", b""), ("abc", b"abc"), ("large bytes", b"x" * 131089)):
                signer.reset()
                replies.reset(200)
                await arecord(lines, f"async {label}", lambda payload=payload: api.auth.signed_body(body=payload))
                replies.report(lines, signer)
            signer.reset()
            replies.reset(303, 503, 200)
            await arecord(lines, "async bodyless hop then original replay", lambda: api.auth.signed_body(body=b"abc"))
            replies.report(lines, signer)
            for ownership in ("borrowed", "owned"):
                file = _File(b"prefix-" + b"f" * 65549)
                file.seek(7)
                body = bodies.AsyncFileBody(file, ownership=ownership)
                signer.reset(file=file, offset=7)
                replies.reset(503, 307, 200)
                await arecord(lines, f"async {ownership} file digest replay", lambda body=body: api.auth.signed_body(body=body))
                replies.report(lines, signer)
                lines.append(f"    closed={file.closed} closes={file.closes} reads={file.reads}")
                await body.aclose()
                if not file.closed:
                    file.close()
            file = _File(b"x")
            file.seek(2)
            body = bodies.AsyncFileBody(file)
            signer.reset(file=file, offset=2)
            replies.reset(200)
            await arecord(lines, "async past EOF digest", lambda: api.auth.signed_body(body=body))
            replies.report(lines, signer)
            lines.append(f"    final offset={file.tell()} borrowed_open={not file.closed}")
            await body.aclose()
            file.close()
            path = Path(directory) / "signed.bin"
            path.write_bytes(b"path payload" * 6000)
            body = bodies.AsyncFileBody.from_path(path)
            signer.reset()
            replies.reset(503, 200)
            await arecord(lines, "async path digest replay", lambda: api.auth.signed_body(body=body))
            replies.report(lines, signer)
            await body.aclose()
            await _async_factories(api, auth, bodies, options, replies, signer, lines)
            await _async_multipart(api, auth, bodies, options, replies, signer, path, lines)
            for signing in (False, True):
                file = _File(b"abc")
                body = bodies.AsyncFileBody(file)
                observer = _AsyncSigner(auth, required=False)
                replies.reset(200)
                await arecord(
                    lines,
                    f"async no hash {'signer' if signing else 'anonymous'}",
                    lambda body=body, observer=observer, signing=signing: api.auth.signed_body(
                        body=body, options=options.RequestOptions(auth=_configuration(auth, options, observer).auth if signing else None)
                    ),
                )
                replies.report(lines, observer)
                lines.append(f"    reads={file.reads} borrowed_open={not file.closed}")
                await body.aclose()
                file.close()
            await _async_faults(api, bodies, options, replies, signer, path, lines)


async def _async_faults(
    api: Any, bodies: ModuleType, options: ModuleType, replies: _Replies, signer: _Signing, path: Path, lines: list[str]
) -> None:
    for label, file, seconds in _faults():
        file.seek(7)
        body = bodies.AsyncFileBody(file)
        signer.reset(file=file, offset=7)
        replies.reset(200)
        deadline = options.Deadline.after(seconds)
        file.until = deadline.at + 0.05
        outcome = await _afailure(
            lambda body=body, deadline=deadline: api.auth.signed_body(
                body=body, options=options.RequestOptions(deadline=deadline)
            )
        )
        lines.append(f"  async {label} {outcome}")
        await body.aclose()
        replies.report(lines, signer)
        lines.append(f"    offset={file.tell()} reads={len(file.reads)}")
        file.close()
    with _Vanishing(path) as vanishing:
        body = bodies.AsyncFileBody(vanishing)
        signer.reset()
        replies.reset(200)
        outcome = await _afailure(lambda: api.auth.signed_body(body=body))
        lines.append(f"  async descriptor lost while hashing {outcome}")
        await body.aclose()
        replies.report(lines, signer)
        lines.append(f"    offset={vanishing.tell()}")


async def _async_factories(
    api: Any, auth: ModuleType, bodies: ModuleType, options: ModuleType, replies: _Replies, signer: _Signing, lines: list[str]
) -> None:
    factory = _Factory()
    signer.reset(factory=factory)
    replies.reset(503, 308, 200)
    await arecord(
        lines,
        "async declared factory digest",
        lambda: api.auth.signed_body(body=bodies.AsyncBodyFactory(factory.async_call, sha256=hashlib.sha256(factory.payload).digest())),
    )
    replies.report(lines, signer)
    lines.append(f"    contexts={factory.contexts} attempts={[(item.begins, item.closes) for item in factory.attempts]}")
    factory = _Factory()
    signer.reset(factory=factory)
    replies.reset(200)
    await arecord(lines, "async missing factory digest", lambda: api.auth.signed_body(body=bodies.AsyncBodyFactory(factory.async_call)))
    replies.report(lines, signer)
    lines.append(f"    factories={len(factory.attempts)}")
    record(lines, "async invalid digest", lambda: bodies.AsyncBodyFactory(factory.async_call, sha256=b"short"))
    opaque = _AsyncSigner(auth, required=False)
    await arecord(
        lines,
        "async factory without required digest",
        lambda: api.auth.signed_body(
            body=bodies.AsyncBodyFactory(factory.async_call), options=options.RequestOptions(auth=_configuration(auth, options, opaque).auth)
        ),
    )
    replies.report(lines, opaque)


async def _async_multipart(
    api: Any, auth: ModuleType, bodies: ModuleType, options: ModuleType, replies: _Replies, signer: _Signing,
    path: Path, lines: list[str],
) -> None:
    for kind in ("bytes", "file", "path"):
        file = _File(b"prefix-part")
        file.seek(7)
        adapter = bodies.AsyncFileBody.from_path(path) if kind == "path" else bodies.AsyncFileBody(file)
        content = b"part" if kind == "bytes" else adapter
        signer.reset(file=file if kind == "file" else None, offset=7)
        body = bodies.AsyncMultipartBody((
            bodies.FieldPart("note", "日本語 \"note\""),
            bodies.FilePart("file", content, filename="résumé.txt"),
        ))
        replies.reset(503, 307, 200)
        await arecord(lines, f"async multipart {kind} exact wire", lambda body=body: api.auth.signed_multipart(body=body))
        replies.report(lines, signer)
        lines.append(
            f"    stable_body={len({request.content for request in replies.requests}) == 1}"
            f" stable_type={len({request.headers['content-type'] for request in replies.requests}) == 1}"
        )
        await adapter.aclose()
        file.close()
    factory = _Factory()
    signer.reset(factory=factory)
    body = bodies.AsyncMultipartBody((bodies.FilePart("file", bodies.AsyncBodyFactory(factory.async_call, sha256=hashlib.sha256(factory.payload).digest())),))
    replies.reset(200)
    await arecord(lines, "async part digest is not multipart digest", lambda: api.auth.signed_multipart(body=body))
    replies.report(lines, signer)
    lines.append(f"    factories={len(factory.attempts)}")
    opaque = _AsyncSigner(auth, required=False)
    await arecord(
        lines,
        "async multipart factory without required digest",
        lambda: api.auth.signed_multipart(body=body, options=options.RequestOptions(auth=_configuration(auth, options, opaque).auth)),
    )
    replies.report(lines, opaque)
    for composite in (False, True):
        for kind in ("stream", "file"):
            chunks, file = _Chunks(), _File(b"one-shot", seekable=False)
            adapter = bodies.AsyncFileBody(file, ownership="owned")
            content = bodies.AsyncStreamBody(chunks, ownership="owned") if kind == "stream" else adapter
            body = bodies.AsyncMultipartBody((bodies.FilePart("file", content),)) if composite else content
            signer.reset()
            replies.reset(200)
            call = api.auth.signed_multipart if composite else api.auth.signed_body
            await arecord(lines, f"async unsupported {'multipart' if composite else 'body'} {kind}", lambda body=body, call=call: call(body=body))
            replies.report(lines, signer)
            lines.append(f"    begins={chunks.begins} stream_closes={chunks.closes} file_reads={file.reads} file_closes={file.closes}")
            await adapter.aclose()
            if not file.closed:
                file.close()
