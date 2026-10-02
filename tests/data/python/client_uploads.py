"""Upload content in chunks through generated resumable upload helpers, against an independent upload server.

The server keeps the bytes each upload received and answers as a tus server would; the scenarios compare what it
stored with the original content, and break appends, probes, and completions to drive every recovery path.
"""

from __future__ import annotations

import asyncio
import io
import json
import string
import tempfile
import threading
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_pagination import Harness
from tests.data.python.client_runtime import (
    Exchange,
    aoutcome,
    arecord,
    describe,
    failing,
    injected,
    json_response,
    outcome,
    raw_response,
    record,
    run,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from types import ModuleType

_CONTENT: Final = b"0123456789"
_PAST: Final = "Wed, 21 Oct 2015 07:28:00 GMT"
_FUTURE: Final = "2999-01-01T00:00:00Z"
_EXPIRED: Final = datetime(2015, 10, 21, 7, 28, tzinfo=timezone.utc)


class _Server:
    """Store each upload's bytes and answer its create, probe, append, and complete calls."""

    def __init__(self) -> None:
        """Start without uploads."""
        self.uploads: dict[str, bytearray] = {}
        self.expires: str | None = _FUTURE

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        """Answer a request by its method and path."""
        parts = request.url.path.strip("/").split("/")
        upload = parts[1] if len(parts) > 1 else ""
        routes: dict[tuple[str, int, str], Callable[[], httpx2.Response]] = {
            ("POST", 1, ""): lambda: self.create(request),
            ("HEAD", 2, ""): lambda: raw_response(200, b"", None, **{"Upload-Offset": self.offset(upload)})(request),
            ("GET", 3, "status"): lambda: json_response(200, {"offset": len(self.uploads[upload])})(request),
            ("PATCH", 2, ""): lambda: self.append(request, upload),
            ("PUT", 2, ""): lambda: self.put(request, upload),
            ("POST", 3, "complete"): lambda: self.complete(request, upload),
        }
        return routes[request.method, len(parts), parts[2] if len(parts) > 2 else ""]()

    def offset(self, upload: str) -> str:
        """Return the offset an upload holds, as a header spells it."""
        return str(len(self.uploads[upload]))

    def complete(self, request: httpx2.Request, upload: str) -> httpx2.Response:
        """Answer a completion with what the upload stored: its size and digest."""
        stored = bytes(self.uploads[upload])
        return json_response(200, {"id": upload, "size": len(stored), "sha256": sha256(stored).hexdigest()})(request)

    def create(self, request: httpx2.Request) -> httpx2.Response:
        """Create an upload of the declared length."""
        upload = f"u{len(self.uploads) + 1}"
        self.uploads[upload] = bytearray()
        headers = {} if self.expires is None else {"Upload-Expires": self.expires}
        return json_response(201, {"id": upload, "expires": self.expires}, **headers)(request)

    def append(self, request: httpx2.Request, upload: str, keep: int | None = None) -> httpx2.Response:
        """Append a chunk at the offset the upload holds, keeping only its first `keep` bytes when given."""
        stored = self.uploads[upload]
        if int(request.headers["Upload-Offset"]) != len(stored):
            return raw_response(409)(request)
        stored.extend(request.content[:keep])
        return raw_response(204, b"", None, **{"Upload-Offset": str(len(stored))})(request)

    def put(self, request: httpx2.Request, upload: str) -> httpx2.Response:
        """Store a chunk at the offset its query names, which must be the offset the upload holds."""
        stored = self.uploads[upload]
        if int(request.url.params["offset"]) != len(stored):
            return raw_response(409)(request)
        stored.extend(request.content)
        return raw_response(204)(request)

    def refused(self, request: httpx2.Request) -> httpx2.Response:
        """Store an append, and answer with an error status as if it had failed."""
        self.append(request, request.url.path.strip("/").split("/")[1])
        return raw_response(503)(request)

    def lost(self, keep: int | None = None) -> Callable[[httpx2.Request], httpx2.Response]:
        """Return a responder that stores an append, or its first bytes, and then loses the connection."""

        def respond(request: httpx2.Request) -> httpx2.Response:
            request.read()
            self.append(request, request.url.path.strip("/").split("/")[1], keep)
            msg = "connection reset"
            raise httpx2.ReadError(msg, request=request)

        return injected(respond)

    def stored(self, upload: str) -> str:
        """Describe what an upload holds: its size and whether its digest is the original's."""
        content = bytes(self.uploads[upload])
        return f"stored {upload}: {len(content)} bytes, original={content == _CONTENT}"

    def offered(self, offset: int) -> Callable[[httpx2.Request], httpx2.Response]:
        """Return a responder of a probe that gives an offset of the server's choice."""
        return raw_response(200, b"", None, **{"Upload-Offset": str(offset)})


def _held(arrived: asyncio.Event, released: threading.Event) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder that tells the client's loop a request arrived and answers only once released."""
    loop = asyncio.get_running_loop()

    def respond(request: httpx2.Request) -> httpx2.Response:
        loop.call_soon_threadsafe(arrived.set)
        released.wait(10)
        return raw_response(503)(request)

    return respond


async def _cancelled(lines: list[str], label: str, exchange: Exchange, call: Callable[[], Any], *prior: Any) -> None:
    """Cancel a step's task while the server holds its last request, then release the server."""
    arrived, released = asyncio.Event(), threading.Event()
    exchange.respond(*prior, _held(arrived, released))
    task = asyncio.ensure_future(call())
    await arrived.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        lines.append(f"  {label} cancelled")
    finally:
        released.set()


class _Counting:
    """A source that counts the readers it opened and closed, and the most open at once."""

    def __init__(self, source: Any) -> None:
        """Wrap a builtin source."""
        self.source = source
        self.identity = source.identity
        self.max_parallel_ranges = 1
        self.opened = self.closed = self.most = 0

    def open_range(self, offset: int, length: int) -> Any:
        """Open a range of the wrapped source, counting it."""
        from contextlib import contextmanager

        @contextmanager
        def counted() -> Iterator[Any]:
            self.opened += 1
            self.most = max(self.most, self.opened - self.closed)
            try:
                with self.source.open_range(offset, length) as reader:
                    yield reader
            finally:
                self.closed += 1

        return counted()


class _Changing:
    """A source whose bytes change after the scan read them, from the second reader on."""

    def __init__(self, protocols: ModuleType, content: bytes, changed: bytes) -> None:
        """Keep the content the identity names and the bytes later readers read."""
        self.identity = protocols.BytesUploadSource.from_bytes(content).identity
        self.max_parallel_ranges = 1
        self.sources = iter((protocols.BytesUploadSource.from_bytes(content),))
        self.changed = protocols.BytesUploadSource.from_bytes(changed)

    def open_range(self, offset: int, length: int) -> Any:
        """Open a range of the original content once, then of the changed bytes."""
        return next(self.sources, self.changed).open_range(offset, length)


class _Claiming:
    """A source whose identity names other content than its readers read."""

    def __init__(self, protocols: ModuleType, claimed: bytes, content: bytes, kind: str = "BytesUploadSource") -> None:
        """Keep the claimed identity and the content read, by a builtin source of a kind."""
        self.identity = protocols.BytesUploadSource.from_bytes(claimed).identity
        self.max_parallel_ranges = 1
        self.source = getattr(protocols, kind).from_bytes(content)

    def open_range(self, offset: int, length: int) -> Any:
        """Open a reader of all the content read, whatever range is asked for."""
        del offset, length
        return self.source.open_range(0, self.source.identity.size)


class _Reentrant:
    """A source whose reader calls back into the handle while a step reads it."""

    def __init__(self, protocols: ModuleType, lines: list[str]) -> None:
        """Wrap the original content."""
        self.source = protocols.BytesUploadSource.from_bytes(_CONTENT)
        self.identity = self.source.identity
        self.max_parallel_ranges = 1
        self.lines = lines
        self.handle: Any = None

    def open_range(self, offset: int, length: int) -> Any:
        """Open a range whose first read steps the handle again, checkpoints it, and closes it, once."""
        handle, lines = self.handle, self.lines
        self.handle = None
        if handle is not None:
            record(lines, "advance during a step", handle.advance)
            lines.append(f"  checkpoint during a step: {type(handle.checkpoint()).__name__}")
            record(lines, "close during a step", handle.close)
            record(
                lines,
                "leave a block with an error during a step",
                lambda: handle.__exit__(ValueError, ValueError(), None),
            )
        return self.source.open_range(offset, length)


class _Uploads(Harness):
    """A generated upload package's public modules and the create arguments its helpers take."""

    def __init__(self, package: ModuleType) -> None:
        """Import the modules and the create arguments."""
        super().__init__(package)
        self.tus = self.argument("files", "CreateFile", "header", "Tus-Resumable", "1.0.0")

    def source(self, content: bytes = _CONTENT) -> Any:
        """Return a builtin bytes source."""
        return self.protocols.BytesUploadSource.from_bytes(content)

    def uploads(self, **settings: Any) -> Any:
        """Return upload options."""
        return self.protocols.UploadOptions(**settings)

    def session(self, **settings: Any) -> Any:
        """Return session options."""
        return self.options.SessionOptions(**settings)


def _progress(value: object, *, uncertain_delivery: bool = False) -> str:
    """Describe an upload step's outcome: progress, an upload error with its own fields, or anything else."""
    if hasattr(value, "confirmed_bytes") and hasattr(value, "total_bytes"):
        progress: Any = value
        return f"progress {progress.confirmed_bytes}/{progress.total_bytes} complete={progress.complete}"
    if isinstance(value, BaseException):
        if uncertain_delivery and getattr(value, "phase", None) == "complete":
            delivery = getattr(getattr(value, "delivery_state", None), "value", None)
            unknown = delivery in {"MAYBE_SENT", "RESPONSE_STARTED"}
            return (
                f"{type(value).__name__}(phase='complete', uncertain_delivery={unknown}, "
                f"resume_state={getattr(value, 'resume_state', None) is not None})"
            )
        fields = ", ".join(
            f"{name}={getattr(value, name)!r}"
            for name in ("phase", "confirmed_offset", "expected_offset", "remote_offset", "size", "offset")
            if getattr(value, name, None) is not None
        )
        state = getattr(value, "resume_state", None)
        kept = "" if not hasattr(value, "resume_state") else f" resume_state={state is not None}"
        identities = "".join(
            f" {name}={getattr(value, name).size}"
            for name in ("expected", "actual")
            if getattr(value, name, None) is not None
        )
        return f"{describe(value)}{f' [{fields}]' if fields else ''}{identities}{kept}"
    return describe(value)


def step(lines: list[str], label: str, call: Callable[[], object], *, uncertain_delivery: bool = False) -> object:
    """Report a step's result or failure."""
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {_progress(error, uncertain_delivery=uncertain_delivery)}")
        return None
    lines.append(f"  {label} = {_progress(result)}")
    return result


async def astep(lines: list[str], label: str, call: Callable[[], Any], *, uncertain_delivery: bool = False) -> object:
    """Report an asyncio step's result or failure."""
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {_progress(error, uncertain_delivery=uncertain_delivery)}")
        return None
    lines.append(f"  {label} = {_progress(result)}")
    return result


def uploads(package: ModuleType, lines: list[str]) -> None:
    """Upload, recover, resume, and limit uploads through the synchronous and asyncio clients."""
    harness = _Uploads(package)
    _records(harness, lines)
    server = _Server()
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native, options=harness.client_options()) as api:
        sections = (_runs, _recoveries, _offsets, _completions, _sources, _resumes, _expiry, _clock, _limits, _steps)
        for section in sections:
            section(harness, api, server, exchange, lines)
            _drained(exchange, lines)
    run(lambda: _async_uploads(harness, server, lines))
    parts_uploads(package, lines)


def _drained(exchange: Exchange, lines: list[str]) -> None:
    """Report responders a section queued but never used, so a section never answers the next one's requests."""
    if left := len(exchange.responders):
        lines.append(f"  unused responders: {left}")
        exchange.responders.clear()


def _records(harness: _Uploads, lines: list[str]) -> None:
    """Validate upload records and builtin sources, and report the upload errors' fields and safe representations."""
    protocols, errors = harness.protocols, harness.errors
    lines.append("upload records")
    digest = sha256(_CONTENT).digest()
    identity = protocols.UploadIdentity(size=10, sha256=digest)
    receipt = protocols.PartReceipt(index=1, receipt="etag-1")
    lines.append(f"  {identity!r} {receipt!r} {protocols.UploadProgress(confirmed_bytes=4, total_bytes=10)!r}")
    for label, create in (
        ("an identity of a negative size", lambda: protocols.UploadIdentity(size=-1, sha256=digest)),
        ("an identity of a boolean size", lambda: protocols.UploadIdentity(size=True, sha256=digest)),
        ("an identity of a short digest", lambda: protocols.UploadIdentity(size=1, sha256=b"1")),
        ("an identity of a text digest", lambda: protocols.UploadIdentity(size=1, sha256=digest.hex())),
        ("a receipt of index 0", lambda: protocols.PartReceipt(index=0, receipt="etag")),
        ("a receipt of a number", lambda: protocols.PartReceipt(index=1, receipt=1)),
        ("progress past the total", lambda: protocols.UploadProgress(confirmed_bytes=11, total_bytes=10)),
        (
            "progress of a receipt list",
            lambda: protocols.UploadProgress(confirmed_bytes=0, total_bytes=0, confirmed_parts=[receipt]),
        ),
        (
            "progress of other receipts",
            lambda: protocols.UploadProgress(confirmed_bytes=0, total_bytes=0, confirmed_parts=("etag",)),
        ),
        ("progress of a numeric flag", lambda: protocols.UploadProgress(confirmed_bytes=0, total_bytes=0, complete=1)),
    ):
        record(lines, label, create)
    source, streamed = harness.source(), protocols.AsyncBytesUploadSource.from_bytes(bytearray(_CONTENT))
    lines.append(
        f"  sources {source!r} {streamed!r} identities equal: {source.identity == streamed.identity == identity}"
    )
    with source.open_range(0, 4) as reader:
        record(lines, "read zero bytes", lambda: reader.read(0))
    lines.append("upload errors")
    progress = protocols.UploadProgress(confirmed_bytes=4, total_bytes=10)
    state = protocols.ResumeState(helper_fingerprint="h", security_fingerprint="s", state={})
    delivery = harness.package.errors.DeliveryState
    for name, fields in (
        ("DeliveryUnknownError", {"delivery_state": delivery.MAYBE_SENT, "resume_state": state, "message_id": "m-1"}),
        (
            "UploadDeliveryUnknownError",
            {"phase": "part", "progress": progress, "delivery_state": delivery.RESPONSE_STARTED},
        ),
        ("UploadSourceChangedError", {"expected": identity, "actual": None, "offset": 4}),
        (
            "UploadOffsetError",
            {"confirmed_offset": 4, "expected_offset": 8, "remote_offset": 2, "size": 10, "resume_state": state},
        ),
        ("UploadExpiredError", {"expires_at": _EXPIRED}),
        ("NonResumableSourceError", {"source_kind": "reader"}),
    ):
        error_type = getattr(errors, name)
        error = error_type(**fields)
        chain = [item.__name__ for item in error_type.__mro__ if issubclass(item, errors.SDKError)]
        kept = all(getattr(error, key) is value or getattr(error, key) == value for key, value in fields.items())
        lines.append(f"  {name}: chain={chain} reason={error.reason_code} kept={kept} {error!r}")
    for label, create in (
        ("delivery not sent", lambda: errors.DeliveryUnknownError(delivery_state=delivery.NOT_SENT)),
        (
            "delivery message number",
            lambda: errors.DeliveryUnknownError(delivery_state=delivery.MAYBE_SENT, message_id=1),
        ),
        (
            "upload phase",
            lambda: errors.UploadDeliveryUnknownError(
                phase="probe", progress=progress, delivery_state=delivery.MAYBE_SENT
            ),
        ),
        (
            "upload progress",
            lambda: errors.UploadDeliveryUnknownError(phase="append", progress={}, delivery_state=delivery.MAYBE_SENT),
        ),
        ("changed identity", lambda: errors.UploadSourceChangedError(expected=None, actual=None)),
        ("changed actual", lambda: errors.UploadSourceChangedError(expected=identity, actual=digest)),
        ("changed offset", lambda: errors.UploadSourceChangedError(expected=identity, actual=identity, offset=-1)),
        (
            "offset negative",
            lambda: errors.UploadOffsetError(confirmed_offset=-1, expected_offset=0, remote_offset=0, size=0),
        ),
        (
            "offset state",
            lambda: errors.UploadOffsetError(
                confirmed_offset=0, expected_offset=0, remote_offset=0, size=0, resume_state={}
            ),
        ),
        ("expired naive", lambda: errors.UploadExpiredError(expires_at=_EXPIRED.replace(tzinfo=None))),
        ("expired condition", lambda: errors.UploadExpiredError(expires_at=_EXPIRED, condition="expired")),
        ("source kind", lambda: errors.NonResumableSourceError(source_kind="bytes")),
    ):
        record(lines, f"refuse {label}", create)


def _runs(harness: _Uploads, api: Any, server: _Server, exchange: Exchange, lines: list[str]) -> None:
    """Upload by length and with a completion operation, one chunk per advance, and keep the result."""
    helper = api.protocols.files.upload
    lines.append("advance one chunk at a time, completing by length")
    exchange.respond(*[server] * 4)
    handle = step(lines, "start", lambda: helper.start(harness.source(), tus_resumable=harness.tus))
    for _ in range(3):
        step(lines, "advance", handle.advance)
    step(lines, "advance after completion", handle.advance)
    step(lines, "run after completion", handle.run)
    lines.extend((f"  {server.stored('u1')}", "run with a completion operation"))
    exchange.respond(*[server] * 4)
    with api.protocols.files.finish.start(harness.source(), tus_resumable=harness.tus) as finished:
        result = step(lines, "run", finished.run)
        step(lines, "run again", finished.run)
    stored = getattr(result, "sha256", None)
    lines.extend((
        f"  completion digest is the original's: {stored == sha256(_CONTENT).hexdigest()}",
        "append with PUT, the size given by the caller",
    ))
    exchange.respond(*[server] * 4)
    put = api.protocols.files.put
    length = harness.argument("files", "CreateFile", "header", "Upload-Length", 10)
    trace = harness.options.RequestOptions(headers=(("X-Trace", "kept"),), query=(("trace", "kept"),))
    handle = step(
        lines,
        "start",
        lambda: put.start(harness.source(), upload_length=length, tus_resumable=harness.tus, options=trace),
    )
    step(lines, "run", handle.run)
    lines.extend((f"  {server.stored(f'u{len(server.uploads)}')}", "upload empty content"))
    exchange.respond(server)
    empty = step(lines, "start", lambda: helper.start(harness.source(b""), tus_resumable=harness.tus))
    step(lines, "run", empty.run)
    lines.append("upload a file")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory, "content.bin")
        path.write_bytes(_CONTENT)
        source = _Counting(harness.protocols.FileUploadSource.from_path(path))
        exchange.respond(*[server] * 4)
        handle = step(lines, "start", lambda: helper.start(source, tus_resumable=harness.tus))
        step(lines, "run", handle.run)
        lines.append(f"  readers opened {source.opened}, closed {source.closed}, most at once {source.most}")
    lines.append(f"  {server.stored('u4')}")


def _recoveries(harness: _Uploads, api: Any, server: _Server, exchange: Exchange, lines: list[str]) -> None:
    """Settle appends whose outcome is unknown by probing, and probe before the next append after any failure."""
    helper = api.protocols.files.upload
    lines.append("an append stored before the connection was lost")
    exchange.respond(server, server.lost(), server, server, server)
    handle = helper.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "advance", handle.advance)
    step(lines, "run", handle.run)
    lines.extend((f"  {server.stored('u5')}", "an append lost before it was stored, then part of one stored"))
    exchange.respond(server, failing(httpx2.ReadError), server, server, server.lost(2), server, server, server)
    handle = helper.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "advance", handle.advance)
    step(lines, "advance", handle.advance)
    step(lines, "run", handle.run)
    lines.extend((f"  {server.stored('u6')}", "an append refused while connecting"))
    exchange.respond(server, failing(httpx2.ConnectError), server, server, server, server)
    handle = helper.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "advance", handle.advance)
    step(lines, "run", handle.run)
    lines.extend((f"  {server.stored('u7')}", "probes that never answer"))
    unretried = harness.options.RequestOptions(retry=harness.options.RetryOptions(max_retries=0))
    exchange.respond(server, server.lost(), failing(httpx2.ConnectError), failing(httpx2.ConnectError))
    handle = helper.start(
        harness.source(),
        tus_resumable=harness.tus,
        upload_options=harness.uploads(max_uncertain_probes=2),
        options=unretried,
    )
    step(lines, "advance", handle.advance)
    exchange.respond(server, server, server)
    step(lines, "run probes first", handle.run)
    lines.extend((f"  {server.stored('u8')}", "the last append stored but answered with an error"))
    exchange.respond(server, server, server, server.refused, server)
    handle = helper.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "advance", handle.advance)
    step(lines, "advance", handle.advance)
    step(lines, "advance", handle.advance)
    step(lines, "advance probes first", handle.advance)
    lines.append("no probe for an unknown append")
    exchange.respond(server, failing(httpx2.ReadError))
    handle = helper.start(
        harness.source(), tus_resumable=harness.tus, upload_options=harness.uploads(max_uncertain_probes=0)
    )
    step(lines, "advance", handle.advance)


def _offsets(harness: _Uploads, api: Any, server: _Server, exchange: Exchange, lines: list[str]) -> None:
    """Refuse remote offsets that regress, pass the content, or commit part of a chunk where none may be."""
    upload, finish = api.protocols.files.upload, api.protocols.files.finish
    for label, offset in (("regressed", 0), ("past the content", 11), ("past the chunk", 9)):
        lines.append(f"a remote offset {label}")
        exchange.respond(server, server, failing(httpx2.ReadError), server.offered(offset))
        handle = upload.start(harness.source(), tus_resumable=harness.tus)
        step(lines, "advance", handle.advance)
        step(lines, "advance", handle.advance)
    lines.append("a probe after an error answer that claims bytes never sent")
    exchange.respond(server, raw_response(500), server.offered(10))
    handle = upload.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "advance", handle.advance)
    step(lines, "advance", handle.advance)
    lines.append("part of a chunk where partial commits are forbidden")
    exchange.respond(server, server.lost(3), server)
    handle = finish.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "advance", handle.advance)
    lines.append("probe responses without a usable offset")
    for label, responder in (
        ("missing", raw_response(200)),
        ("not digits", raw_response(200, b"", None, **{"Upload-Offset": "+4"})),
        ("too many digits", raw_response(200, b"", None, **{"Upload-Offset": "9" * 5000})),
        ("repeated", lambda _request: httpx2.Response(200, headers=[("Upload-Offset", "4"), ("Upload-Offset", "4")])),
    ):
        exchange.respond(server, failing(httpx2.ReadError), responder)
        handle = upload.start(harness.source(), tus_resumable=harness.tus)
        step(lines, f"advance with a {label} offset", handle.advance)
    for label, body in (("string", {"offset": "4"}), ("null", {"offset": None}), ("negative", {"offset": -1})):
        exchange.respond(server, failing(httpx2.ReadError), json_response(200, body))
        handle = finish.start(harness.source(), tus_resumable=harness.tus)
        step(lines, f"advance with a {label} offset", handle.advance)


def _completions(harness: _Uploads, api: Any, server: _Server, exchange: Exchange, lines: list[str]) -> None:
    """Never send a completion of unknown outcome again; send one an error answered again."""
    helper = api.protocols.files.finish
    lines.append("a completion of unknown outcome")
    exchange.respond(server, server, server, failing(httpx2.ReadError))
    handle = helper.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "run", handle.run)
    step(lines, "run again", handle.run)
    step(lines, "advance again", handle.advance)
    state = handle.checkpoint()
    step(lines, "resume", lambda: helper.resume(harness.source(), state))
    lines.append("a completion an error answered")
    exchange.respond(server, server, server, raw_response(500), server)
    handle = helper.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "run", handle.run)
    step(lines, "run again", handle.run)
    lines.append("a completion the server answered with a body that does not decode")
    exchange.respond(server, server, server, json_response(200, {"id": 5}))
    handle = helper.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "run", handle.run)
    step(lines, "run again", handle.run)
    step(lines, "resume", lambda: helper.resume(harness.source(), handle.checkpoint()))
    lines.append("a completion cancelled while the server answers it")
    token = harness.options.CancelToken()

    def cancelled(request: httpx2.Request) -> httpx2.Response:
        token.cancel()
        return server(request)

    exchange.respond(server, server, server, cancelled)
    handle = helper.start(
        harness.source(), tus_resumable=harness.tus, options=harness.options.RequestOptions(cancel_token=token)
    )
    step(lines, "run", handle.run)
    step(lines, "resume", lambda: helper.resume(harness.source(), handle.checkpoint()))
    lines.append("a checkpoint taken while the completion is in flight")
    taken: list[Any] = []

    def checkpointed(request: httpx2.Request) -> httpx2.Response:
        taken.append(handle.checkpoint())
        return server(request)

    exchange.respond(server, server, server, checkpointed)
    handle = helper.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "run", handle.run)
    step(lines, "resume the checkpoint taken in flight", lambda: helper.resume(harness.source(), taken[0]))


def _sources(harness: _Uploads, api: Any, server: _Server, exchange: Exchange, lines: list[str]) -> None:
    """Refuse one-shot inputs and invalid sources before sending, and stop at content that changed."""
    helper, protocols = api.protocols.files.upload, harness.protocols

    async def stream() -> AsyncIterator[bytes]:  # ruff: ignore[unused-async]
        yield _CONTENT

    class Ranges:
        identity = harness.source().identity
        max_parallel_ranges = 0

        def open_range(self, offset: int, length: int) -> object:
            del offset, length
            return self

    class Unidentified(Ranges):
        identity = "content"
        max_parallel_ranges = 1

    class Asynchronous(Ranges):
        max_parallel_ranges = 1

        def open_range(self, offset: int, length: int) -> object:
            return protocols.AsyncBytesUploadSource.from_bytes(_CONTENT).open_range(offset, length)

    class Coroutines(Ranges):
        max_parallel_ranges = 1

        async def open_range(self, offset: int, length: int) -> object:
            return protocols.AsyncBytesUploadSource.from_bytes(_CONTENT).open_range(offset, length)

    class Texts(Ranges):
        max_parallel_ranges = 1

        def open_range(self, offset: int, length: int) -> Any:
            del offset, length
            from contextlib import nullcontext

            return nullcontext(io.StringIO(string.digits))

    lines.append("sources that cannot resume or are invalid")
    stream_source = stream()
    for label, source in (
        ("bytes", _CONTENT),
        ("an iterator", iter((_CONTENT,))),
        ("a reader", io.BytesIO(_CONTENT)),
        ("an async iterator", stream_source),
        ("a number", 5),
        ("no parallel ranges", Ranges()),
        ("another identity", Unidentified()),
        ("an asyncio source", Asynchronous()),
        ("a coroutine for each range", Coroutines()),
        ("a text reader", Texts()),
    ):
        record(lines, f"start with {label}", lambda source=source: helper.start(source, tus_resumable=harness.tus))
    run(stream_source.aclose)
    lines.append("content that changed after the scan")
    exchange.respond(server)
    handle = helper.start(_Changing(protocols, _CONTENT, b"x123456789"), tus_resumable=harness.tus)
    step(lines, "advance", handle.advance)
    step(lines, "advance again", handle.advance)
    lines.append(f"  checkpoint kept: {type(handle.checkpoint()).__name__}")
    for label, changed in (("shorter", b"012"), ("longer", b"0123456789!"), ("other", b"9876543210")):
        step(
            lines,
            f"start with {label} content than its identity",
            lambda changed=changed: helper.start(_Claiming(protocols, _CONTENT, changed), tus_resumable=harness.tus),
        )
    lines.append("a file that changed after it was hashed")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory, "content.bin")
        path.write_bytes(_CONTENT)
        source = protocols.FileUploadSource.from_path(path)
        path.write_bytes(b"0123456789abc")
        step(lines, "start", lambda: helper.start(source, tus_resumable=harness.tus))
        lines.append(f"  source {source!r} reads ranges: {_ranges(source)}")
        step(lines, "hash a directory", lambda: protocols.FileUploadSource.from_path(directory))


def _ranges(source: Any) -> str:
    """Describe what out-of-range and invalid reads of a builtin source raise."""
    found = []
    for label, call in (
        ("past the end", lambda: source.open_range(5, 10).__enter__()),  # ruff: ignore[unnecessary-dunder-call]
        ("a negative offset", lambda: source.open_range(-1, 1).__enter__()),  # ruff: ignore[unnecessary-dunder-call]
    ):
        try:
            call()
        except ValueError:  # ruff: ignore[try-except-in-loop]
            found.append(f"{label} refused")
    return ", ".join(found)


def _resumes(harness: _Uploads, api: Any, server: _Server, exchange: Exchange, lines: list[str]) -> None:  # ruff: ignore[too-many-locals]
    """Resume a checkpoint with zero creates after checking its source, and refuse checkpoints of another kind."""
    helper, finish, protocols = api.protocols.files.upload, api.protocols.files.finish, harness.protocols
    lines.append("checkpoint, export, import, and resume")
    exchange.respond(server, server)
    handle = helper.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "advance", handle.advance)
    exported = handle.checkpoint().export()
    handle.close()
    state = protocols.import_state(exported)
    exchange.respond(server, server, server)
    resumed = step(lines, "resume", lambda: helper.resume(harness.source(), state))
    step(lines, "run", resumed.run)
    lines.extend((
        f"  {server.stored(f'u{len(server.uploads)}')}",
        "resume a checkpoint whose remote upload went further",
    ))
    exchange.respond(server, server)
    handle = helper.start(harness.source(), tus_resumable=harness.tus)
    state = handle.checkpoint()
    step(lines, "advance", handle.advance)
    exchange.respond(server, server)
    resumed = step(lines, "resume", lambda: helper.resume(harness.source(), state))
    step(lines, "advance", resumed.advance)
    lines.append("resume a complete upload")
    exchange.respond(*[server] * 4)
    finished = finish.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "run", finished.run)
    complete = protocols.import_state(finished.checkpoint().export())
    restored = step(lines, "resume", lambda: finish.resume(harness.source(), complete))
    step(lines, "run", restored.run)
    finished_envelope = json.loads(complete.export())
    unreadable = protocols.ResumeState(
        **{name: finished_envelope[name] for name in ("helper_fingerprint", "security_fingerprint")},
        state=finished_envelope["state"],
        payload=state_payload(complete.export())[:64] + b"not json",
    )
    record(
        lines,
        "resume a complete upload whose result does not decode",
        lambda: finish.resume(harness.source(), unreadable),
    )
    lines.append("resume a checkpoint whose remote offset regressed")
    exchange.respond(server.offered(0))
    record(lines, "resume", lambda: helper.resume(harness.source(), protocols.import_state(exported)))
    lines.append("refused checkpoints and sources")
    for label, call in (
        ("another source", lambda: helper.resume(harness.source(b"9876543210"), state)),
        ("another helper", lambda: finish.resume(harness.source(), state)),
        ("not a state", lambda: helper.resume(harness.source(), "state")),
        ("bytes", lambda: helper.resume(_CONTENT, state)),
        (
            "a smaller chunk size",
            lambda: helper.resume(harness.source(), state, upload_options=harness.uploads(chunk_bytes=2)),
        ),
        (
            "fewer chunks allowed",
            lambda: helper.resume(harness.source(), state, upload_options=harness.uploads(max_parts=2)),
        ),
    ):
        record(lines, f"resume with {label}", call)
    envelope = json.loads(exported)
    fingerprints = {name: envelope[name] for name in ("helper_fingerprint", "security_fingerprint")}
    saved = envelope["state"]
    upload = saved["bound"][1][0]
    for label, change in (
        ("an unknown member", {"other": 1}),
        ("a confirmed offset past the content", {"confirmed": 11}),
        ("a chunk over the server maximum", {"chunk": 5}),
        ("an unknown phase", {"phase": "paused"}),
        ("an unknown delivery", {"phase": "unknown", "delivery": "NOT_SENT"}),
        ("a result without completion", {"phase": "complete", "confirmed": 10, "result": [200, None]}),
        ("a digest of another form", {"sha256": "ABC"}),
        ("missing bound values", {"bound": [[], [], []]}),
        ("a path value of dots", {"bound": [["..", "1.0.0"], [upload, "1.0.0"], []]}),
        ("a path value of another shape", {"bound": [[{"id": upload}, "1.0.0"], [upload, "1.0.0"], []]}),
    ):
        broken = protocols.ResumeState(**fingerprints, state={**saved, **change}, payload=state_payload(exported))
        record(lines, f"resume with {label}", lambda broken=broken: helper.resume(harness.source(), broken))
    dots = {**saved, "bound": [["..", "1.0.0"], [upload, "1.0.0"], []]}
    dotted = protocols.ResumeState(**fingerprints, state=dots, payload=state_payload(exported))
    record(lines, "resume with a path value of dots and bytes for a source", lambda: helper.resume(_CONTENT, dotted))
    digests = state_payload(exported)
    other = protocols.ResumeState(**fingerprints, state=saved, payload=digests[:32] + bytes(32) + digests[64:])
    step(lines, "resume with digests of other content", lambda: helper.resume(harness.source(), other))
    lines.append("a checkpoint of another security partition")
    secured = harness.client_options(
        protocols=harness.options.ProtocolClientOptions(
            security=protocols.ProtocolSecurityContext(credential_partition="tenant-a")
        )
    )
    with exchange.client() as native, harness.package.Client(http_client=native, options=secured) as secured_client:
        record(lines, "resume", lambda: secured_client.protocols.files.upload.resume(harness.source(), state))


def state_payload(exported: bytes) -> bytes:
    """Return the payload of an exported checkpoint."""
    from base64 import b64decode

    return b64decode(json.loads(exported)["payload"])


def _expiry(harness: _Uploads, api: Any, server: _Server, exchange: Exchange, lines: list[str]) -> None:
    """Keep the server's expiry in checkpoints, refuse expired ones, and refuse expiry values that are no dates."""
    helper, protocols = api.protocols.files.upload, harness.protocols
    lines.append("server expiries")
    for label, value, probes in (("an HTTP date in the past", _PAST, 0), ("an RFC 3339 date-time to come", _FUTURE, 1)):
        server.expires = value
        exchange.respond(*[server] * (1 + probes))
        handle = helper.start(harness.source(), tus_resumable=harness.tus)
        state = handle.checkpoint()
        record(lines, f"resume a checkpoint with {label}", lambda state=state: helper.resume(harness.source(), state))
        record(lines, "import its export", lambda state=state: protocols.import_state(state.export()))
    for label, value in (
        ("no date", "tomorrow"),
        ("no day", "2999-02-30T00:00:00Z"),
        ("a leap second", "2999-12-31T23:59:60Z"),
        ("a fraction", "2999-01-01t00:00:00.123456789z"),
    ):
        server.expires = value
        exchange.respond(server)
        record(lines, f"start with {label}", lambda: helper.start(harness.source(), tus_resumable=harness.tus))
    finish = api.protocols.files.finish
    for label, body in (("a null", {"id": "x", "expires": None}), ("a number as", {"id": "x", "expires": 1})):
        exchange.respond(json_response(201, body))
        record(
            lines, f"start with {label} body expiry", lambda: finish.start(harness.source(), tus_resumable=harness.tus)
        )
    server.expires = _FUTURE
    exchange.respond(json_response(201, {"id": "x"}, **{"Upload-Expires": ""}))
    record(lines, "start with an empty expiry", lambda: helper.start(harness.source(), tus_resumable=harness.tus))


def _clock(harness: _Uploads, api: Any, server: _Server, exchange: Exchange, lines: list[str]) -> None:
    """Measure expiry and the session's total timeout on the client's clock, not the system's."""
    del api
    lines.append("the client's clock")
    ticks = [1000.0]
    wall = datetime(3000, 1, 1, tzinfo=timezone.utc).timestamp()
    clock = harness.options.Clock(monotonic=lambda: ticks[0], time=lambda: wall)
    with (
        exchange.client() as native,
        harness.package.Client(http_client=native, options=harness.client_options(clock=clock)) as timed,
    ):
        helper = timed.protocols.files.upload
        exchange.respond(server, server)
        handle = helper.start(harness.source(), tus_resumable=harness.tus)
        step(lines, "advance", handle.advance)
        state = handle.checkpoint()
        record(
            lines, "resume a checkpoint whose expiry the clock passed", lambda: helper.resume(harness.source(), state)
        )
        ticks[0] += 601
        step(lines, "advance once the clock passed the session's total timeout", handle.advance)


def _limits(harness: _Uploads, api: Any, server: _Server, exchange: Exchange, lines: list[str]) -> None:
    """Refuse options before reading or sending, and stop at a session's send limit with a checkpoint."""
    helper, options = api.protocols.files.upload, harness.options
    lines.append("refused options")
    for label, call in (
        (
            "too many chunks",
            lambda: helper.start(
                harness.source(), tus_resumable=harness.tus, upload_options=harness.uploads(max_parts=2)
            ),
        ),
        (
            "a digest list over the checkpoint limit",
            lambda: helper.start(
                harness.source(bytes(400000)),
                tus_resumable=harness.tus,
                upload_options=harness.uploads(chunk_bytes=1, max_parts=None),
            ),
        ),
        (
            "options of another type",
            lambda: helper.start(harness.source(), tus_resumable=harness.tus, upload_options=harness.session()),
        ),
        (
            "a fixed idempotency key",
            lambda: helper.start(
                harness.source(),
                tus_resumable=harness.tus,
                options=options.RequestOptions(idempotency_key=options.IdempotencyKey.new()),
            ),
        ),
        (
            "a patch of the written offset",
            lambda: helper.start(
                harness.source(),
                tus_resumable=harness.tus,
                options=options.RequestOptions(headers=(("upload-offset", "1"),)),
            ),
        ),
        (
            "a patch of the written size",
            lambda: helper.start(
                harness.source(),
                tus_resumable=harness.tus,
                options=options.RequestOptions(headers=(("Upload-Length", "1"),)),
            ),
        ),
    ):
        record(lines, f"start with {label}", call)
    record(
        lines,
        "start with a patch of the written query offset",
        lambda: api.protocols.files.put.start(
            harness.source(),
            upload_length=harness.argument("files", "CreateFile", "header", "Upload-Length", 10),
            tus_resumable=harness.tus,
            options=options.RequestOptions(query=(("offset", "1"),)),
        ),
    )
    for label, value in (("zero chunk bytes", {"chunk_bytes": 0}), ("negative probes", {"max_uncertain_probes": -1})):
        record(lines, f"upload options with {label}", lambda value=value: harness.uploads(**value))
    lines.append("a session's send limit")
    exchange.respond(server, server)
    handle = helper.start(
        harness.source(), tus_resumable=harness.tus, session_options=harness.session(max_network_sends=2)
    )
    step(lines, "advance", handle.advance)
    step(lines, "advance", handle.advance)
    record(
        lines,
        "start without a send slot",
        lambda: helper.start(
            harness.source(), tus_resumable=harness.tus, session_options=harness.session(max_network_sends=0)
        ),
    )
    lines.append("defaults from the client")
    defaults = options.ProtocolClientOptions(
        defaults={"files.upload": harness.protocols.ProtocolDefaults(options=harness.uploads(chunk_bytes=2))}
    )
    with (
        exchange.client() as native,
        harness.package.Client(http_client=native, options=harness.client_options(protocols=defaults)) as other,
    ):
        exchange.respond(server, server)
        handle = other.protocols.files.upload.start(harness.source(), tus_resumable=harness.tus)
        step(lines, "advance two bytes", handle.advance)


def _steps(harness: _Uploads, api: Any, server: _Server, exchange: Exchange, lines: list[str]) -> None:
    """Refuse a step during a step, allow a checkpoint, and refuse every step once closed."""
    helper = api.protocols.files.upload
    lines.append("steps during a step")
    source = _Reentrant(harness.protocols, lines)
    exchange.respond(server, server)
    handle = helper.start(source, tus_resumable=harness.tus)
    source.handle = handle
    step(lines, "advance", handle.advance)
    handle.close()
    step(lines, "advance after close", handle.advance)
    step(lines, "close again", handle.close)
    lines.append(f"  handle {handle!r}")


async def _async_uploads(harness: _Uploads, server: _Server, lines: list[str]) -> None:  # ruff: ignore[too-many-locals]
    """Upload, recover, and resume with asyncio, from bytes and from a file read on its own worker."""
    package, protocols = harness.package, harness.protocols
    exchange = Exchange(lines)
    async with (
        exchange.async_client() as native,
        package.AsyncClient(http_client=native, options=harness.client_options()) as api,
    ):
        helper, finish = api.protocols.files.upload, api.protocols.files.finish
        content = protocols.AsyncBytesUploadSource.from_bytes(_CONTENT)
        lines.append("async uploads")
        exchange.respond(server, server, server.lost(2), server, server, server)
        handle = await helper.start(content, tus_resumable=harness.tus)
        await astep(lines, "advance", handle.advance)
        await astep(lines, "advance", handle.advance)
        await astep(lines, "run", handle.run)
        await astep(lines, "run again", handle.run)
        await astep(lines, "advance after completion", handle.advance)
        lines.append(f"  {server.stored(f'u{len(server.uploads)}')}")
        exchange.respond(server, server.lost(), server, server, server.refused, server)
        handle = await helper.start(content, tus_resumable=harness.tus)
        await astep(lines, "advance over a lost acknowledgement", handle.advance)
        await astep(lines, "advance", handle.advance)
        await astep(lines, "advance stored but refused", handle.advance)
        await astep(lines, "advance probes first", handle.advance)
        exchange.respond(server, server.refused, server, server, server)
        handle = await helper.start(content, tus_resumable=harness.tus)
        await astep(lines, "advance stored but refused", handle.advance)
        await astep(lines, "run probes first", handle.run)
        exchange.respond(*[server] * 4)
        async with await finish.start(content, tus_resumable=harness.tus) as finished:
            result = await astep(lines, "run with a completion", finished.run)
        lines.append(
            f"  completion digest is the original's: {getattr(result, 'sha256', None) == sha256(_CONTENT).hexdigest()}"
        )
        restored = await astep(
            lines, "resume the complete upload", lambda: finish.resume(content, finished.checkpoint())
        )
        await astep(lines, "run it", restored.run)
        longer = _Claiming(protocols, _CONTENT, _CONTENT + b"!", "AsyncBytesUploadSource")
        await astep(
            lines,
            "start with longer content than its identity",
            lambda: helper.start(longer, tus_resumable=harness.tus),
        )
        _drained(exchange, lines)
        lines.append("async resume and refusals")
        exchange.respond(server, server)
        handle = await helper.start(content, tus_resumable=harness.tus)
        await astep(lines, "advance", handle.advance)
        state = handle.checkpoint()
        await handle.aclose()
        await astep(lines, "advance after aclose", handle.advance)
        exchange.respond(server, server, server)
        resumed = await astep(lines, "resume", lambda: helper.resume(content, state))
        await astep(lines, "run", resumed.run)
        await arecord(
            lines, "start with a synchronous source", lambda: helper.start(harness.source(), tus_resumable=harness.tus)
        )
        exchange.respond(server, server, server, failing(httpx2.ReadError))
        unknown = await finish.start(content, tus_resumable=harness.tus)
        await astep(lines, "run with a completion of unknown outcome", unknown.run)
        await astep(lines, "resume it", lambda: finish.resume(content, unknown.checkpoint()))
        exchange.respond(server, failing(httpx2.ReadError), failing(httpx2.ConnectError))
        unretried = harness.options.RequestOptions(retry=harness.options.RetryOptions(max_retries=0))
        lost = await helper.start(
            content,
            tus_resumable=harness.tus,
            upload_options=harness.uploads(max_uncertain_probes=1),
            options=unretried,
        )
        await astep(lines, "advance with probes that never answer", lost.advance)
        exchange.respond(server, server, failing(httpx2.ReadError), server.offered(0))
        regressed = await helper.start(content, tus_resumable=harness.tus)
        await astep(lines, "advance", regressed.advance)
        await astep(lines, "advance to a regressed offset", regressed.advance)
        exchange.respond(server, server.lost(5), server)
        partial = await finish.start(content, tus_resumable=harness.tus)
        await astep(lines, "advance with a forbidden partial commit", partial.advance)
        exchange.respond(server, server)
        async with await helper.start(
            content, tus_resumable=harness.tus, session_options=harness.session(max_network_sends=2)
        ) as limited:
            await astep(lines, "advance", limited.advance)
            await astep(lines, "advance past the send limit", limited.advance)
        exchange.respond(server)
        changing = _Changing(protocols, _CONTENT, b"x123456789")
        changing.sources = iter((protocols.AsyncBytesUploadSource.from_bytes(_CONTENT),))
        changing.changed = protocols.AsyncBytesUploadSource.from_bytes(b"x123456789")
        changed = await helper.start(changing, tus_resumable=harness.tus)
        await astep(lines, "advance over changed content", changed.advance)
        lines.append("async append cancelled in flight")
        exchange.respond(server)
        interrupted = await helper.start(content, tus_resumable=harness.tus)
        await _cancelled(lines, "advance", exchange, interrupted.advance)
        exchange.respond(server, server, server, server)
        await astep(lines, "run probes first", interrupted.run)
        lines.append("async completion cancelled in flight")
        exchange.respond(server, server, server)
        cancelled = await finish.start(content, tus_resumable=harness.tus)
        await astep(lines, "advance", cancelled.advance)
        await astep(lines, "advance", cancelled.advance)
        await _cancelled(lines, "run", exchange, cancelled.run)
        await astep(lines, "run again", cancelled.run)
        await astep(lines, "resume", lambda: finish.resume(content, cancelled.checkpoint()))
        _drained(exchange, lines)
        lines.append("async file source")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "content.bin")
            path.write_bytes(_CONTENT)  # ruff: ignore[blocking-path-method-in-async-function]
            async with await protocols.AsyncFileUploadSource.from_path(path) as source:
                lines.append(f"  source {source!r}")
                exchange.respond(*[server] * 4)
                handle = await helper.start(source, tus_resumable=harness.tus)
                await astep(lines, "run", handle.run)
                lines.append(f"  {server.stored(f'u{len(server.uploads)}')}")
                async with source.open_range(2, 4) as reader:
                    first = await reader.read(3)
                    await reader.aclose()
                    lines.append(f"  file range {first!r}, after close {await reader.read(1)!r}")
                path.write_bytes(b"0123456789abc")  # ruff: ignore[blocking-path-method-in-async-function]
                await arecord(
                    lines, "start after the file changed", lambda: helper.start(source, tus_resumable=harness.tus)
                )
            await arecord(
                lines, "start after the source closed", lambda: helper.start(source, tus_resumable=harness.tus)
            )
            await astep(lines, "hash a directory", lambda: protocols.AsyncFileUploadSource.from_path(directory))
            try:
                await protocols.AsyncFileUploadSource.from_path(Path(directory, "missing"))
            except OSError as error:
                lines.append(f"  hash a missing file ! {type(error).__name__}")
        lines.append(f"  async ranges: {await _async_ranges(content)}")
        _drained(exchange, lines)


async def _async_ranges(source: Any) -> str:
    """Describe what an asyncio reader of a builtin source reads, and an out-of-range open."""
    async with source.open_range(2, 3) as reader:
        first, rest, end = await reader.read(2), await reader.read(5), await reader.read(1)
        await reader.aclose()
        closed = await reader.read(1)
    try:
        async with source.open_range(8, 3):
            pass
    except ValueError:
        refused = "refused"
    return f"{first!r} {rest!r} {end!r} after close {closed!r}, past the end {refused}"


_PART_CONTENT = b"AAAABBBBCCCCDDDD"


class _PartsServer:
    """Store each indexed part, then assemble only the receipt list a completion supplies."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.parts: dict[int, bytes] = {}
        self.creates = self.completes = self.aborts = self.lists = 0
        self.sent: list[int] = []
        self.order: list[int] = []
        self.stored = b""
        self.failure: str | None = None
        self.bad_digest = False
        self.expiry = _FUTURE
        self.url: str | None = None
        self.complete_token: Any = None
        self.encoding = "hex"
        self.pending = False
        self.part_token: Any = None

    def __call__(self, request: httpx2.Request) -> httpx2.Response:  # ruff: ignore[too-many-return-statements, too-many-branches]
        """Answer creates, part sends, reverse-order listings, completion, and abort."""
        path = request.url.path
        with self.lock:
            if request.method == "POST" and path == "/files":
                self.creates += 1
                headers = {"Upload-Expires": self.expiry}
                if self.url is not None:
                    headers["Location"] = self.url
                return json_response(201, {"id": "parts", "expires": self.expiry, "url": self.url}, **headers)(request)
            if request.method == "PUT":
                index = int(
                    request.headers["X-Part-Index"] if "X-Part-Index" in request.headers else path.rsplit("/", 1)[1]
                )
                self.sent.append(index)
                mode = self.failure if index == 2 else None
                if mode in {"before", "during", "after", "fail"}:
                    self.failure = None
                if mode == "before":
                    msg = "lost before part"
                    raise httpx2.WriteError(msg, request=request)
                if mode == "fail":
                    return raw_response(503)(request)
                content = request.content
                if "Digest" in request.headers and request.headers["Digest"] != self.digest(content):
                    return raw_response(400)(request)
                if mode == "during":
                    msg = "lost during part"
                    raise httpx2.WriteError(msg, request=request)
                self.parts[index] = content
                if self.part_token is not None and index == 2:
                    self.part_token.cancel()
                if mode == "after":
                    msg = "lost after part ACK"
                    raise httpx2.ReadError(msg, request=request)
                return raw_response(204)(request)
            if request.method == "GET" and (path.endswith("/parts") or path == "/sessions/parts"):
                self.lists += 1
                if self.failure == "list":
                    msg = "list unavailable"
                    raise httpx2.ReadError(msg, request=request)
                values = [
                    {
                        "index": index,
                        "receipt": f"part-{index}",
                        "digest": "wrong" if self.bad_digest else self.digest(content),
                    }
                    for index, content in sorted(self.parts.items(), reverse=True)
                ]
                return json_response(200, {"parts": values})(request)
            if request.method == "POST":
                self.completes += 1
                parts = json.loads(request.content)["parts"]
                self.order = [item["index"] for item in parts]
                self.stored = b"".join(
                    self.parts[item["index"]] for item in parts if item["receipt"] == f"part-{item['index']}"
                )
                if self.complete_token is not None:
                    self.complete_token.cancel()
                if self.failure == "complete":
                    msg = "completion applied"
                    raise httpx2.ReadError(msg, request=request)
                if self.failure == "decode":
                    return json_response(200, {"id": "parts"})(request)
                if self.failure == "gateway":
                    return raw_response(502)(request)
                return self.result(request)
            if request.method == "DELETE":
                self.aborts += 1
                return json_response(200, {"aborted": True})(request)
            return json_response(
                200,
                {
                    "state": "complete" if self.completes and not self.pending else "pending",
                    "result": self.result_value(),
                },
            )(request)

    def digest(self, content: bytes) -> str:
        """Spell the stored digest in the advertised encoding."""
        from base64 import b64encode

        digest = sha256(content).digest()
        return digest.hex() if self.encoding == "hex" else b64encode(digest).decode("ascii")

    def result_value(self) -> dict[str, object]:
        return {"id": "parts", "size": len(self.stored), "sha256": sha256(self.stored).hexdigest()}

    def result(self, request: httpx2.Request) -> httpx2.Response:
        return json_response(200, self.result_value())(request)

    def report(self, lines: list[str], label: str) -> None:
        lines.append(
            f"  {label}: creates={self.creates} lists={self.lists} sends={sorted(self.sent)} "
            f"completes={self.completes} aborts={self.aborts} ordered={self.order} "
            f"original={sha256(self.stored).digest() == sha256(_PART_CONTENT).digest()}"
        )


class _Ranges:
    """Independent readers with a four-reader barrier and observable bounds."""

    def __init__(self, protocols: Any, *, capacity: int = 4, shared: bool = False) -> None:
        self.identity = protocols.BytesUploadSource.from_bytes(_PART_CONTENT).identity
        self.max_parallel_ranges = capacity
        self.barrier = threading.Barrier(capacity)
        self.lock = threading.Lock()
        self.shared = shared
        self.extra = False
        self.position = 0
        self.opened = self.closed = self.most = self.largest = self.read_limit = 0

    @contextmanager
    def open_range(self, offset: int, length: int) -> Iterator[Any]:
        self.position = offset
        with self.lock:
            self.opened += 1
            self.most = max(self.most, self.opened - self.closed)
            self.largest = max(self.largest, length if length != len(_PART_CONTENT) else 0)
        source = self

        class Reader:
            def __init__(self) -> None:
                self.position = offset
                self.first = True

            def read(self, max_bytes: int) -> bytes:
                source.read_limit = max(source.read_limit, max_bytes)
                if self.first and length != len(_PART_CONTENT):
                    self.first = False
                    source.barrier.wait(10)
                position = source.position if source.shared and length != len(_PART_CONTENT) else self.position
                limit = min(max_bytes, 4) if source.extra and length != len(_PART_CONTENT) else max_bytes
                stop = offset + length + int(source.extra and length != len(_PART_CONTENT))
                value = (_PART_CONTENT + b"!")[position : min(position + limit, stop)]
                self.position += len(value)
                if source.shared and length != len(_PART_CONTENT):
                    source.position += len(value)
                return value

            def close(self) -> None:
                pass

        try:
            yield Reader()
        finally:
            with self.lock:
                self.closed += 1

    def report(self, lines: list[str]) -> None:
        lines.append(
            f"  ranges: most={self.most} opened={self.opened} closed={self.closed} "
            f"part_limit={self.largest} read_limit={self.read_limit}"
        )


class _AsyncRanges:
    """Native async readers whose first part reads meet through an event."""

    def __init__(self, protocols: Any, *, capacity: int = 4, shared: bool = False) -> None:
        self.identity = protocols.BytesUploadSource.from_bytes(_PART_CONTENT).identity
        self.max_parallel_ranges = capacity
        self.shared = shared
        self.position = self.waiting = 0
        self.barrier = asyncio.Event()
        self.opened = self.closed = self.most = self.largest = self.read_limit = 0

    @asynccontextmanager
    async def open_range(self, offset: int, length: int) -> AsyncIterator[Any]:
        self.position = offset
        self.opened += 1
        self.most = max(self.most, self.opened - self.closed)
        self.largest = max(self.largest, length if length != len(_PART_CONTENT) else 0)
        source = self

        class Reader:
            def __init__(self) -> None:
                self.position = offset
                self.first = True

            async def read(self, max_bytes: int) -> bytes:
                source.read_limit = max(source.read_limit, max_bytes)
                if self.first and length != len(_PART_CONTENT):
                    self.first = False
                    source.waiting += 1
                    if source.waiting == source.max_parallel_ranges:
                        source.barrier.set()
                    await source.barrier.wait()
                position = source.position if source.shared and length != len(_PART_CONTENT) else self.position
                value = _PART_CONTENT[position : min(position + max_bytes, offset + length)]
                self.position += len(value)
                if source.shared and length != len(_PART_CONTENT):
                    source.position += len(value)
                return value

            async def aclose(self) -> None:
                pass

        try:
            yield Reader()
        finally:
            self.closed += 1

    def report(self, lines: list[str]) -> None:
        lines.append(
            f"  ranges: most={self.most} opened={self.opened} closed={self.closed} "
            f"part_limit={self.largest} read_limit={self.read_limit}"
        )


def _respond(exchange: Exchange, server: _PartsServer) -> None:
    exchange.respond(*[server] * 40)


def parts_uploads(package: ModuleType, lines: list[str]) -> None:
    """Exercise parts waves, resumption, uncertainty, abort, and source limits in both modes."""
    harness = _Uploads(package)
    quiet: list[str] = []
    exchange = Exchange(quiet)
    with exchange.client() as native, package.Client(http_client=native, options=harness.client_options()) as api:
        helper = api.protocols.files.parts
        for capacity, shared in ((4, False), (1, False), (4, True)):
            lines.append(f"parts ranges capacity={capacity} shared={shared}")
            server = _PartsServer()
            _respond(exchange, server)
            source = _Ranges(harness.protocols, capacity=capacity, shared=shared)
            with helper.start(source, tus_resumable=harness.tus) as handle:
                if shared:
                    lines.append(f"  run: {outcome(handle.run)}")
                else:
                    step(lines, "run", handle.run)
                source.report(lines)
                server.report(lines, "server")
            exchange.responders.clear()
        for mode in ("before", "during", "after", "fail", "list"):
            lines.append(f"parts failure {mode}")
            server = _PartsServer()
            _respond(exchange, server)
            source = harness.source(_PART_CONTENT)
            with helper.start(
                source,
                tus_resumable=harness.tus,
                upload_options=harness.uploads(parallelism=1),
                options=harness.options.RequestOptions(retry=harness.options.RetryOptions(max_retries=0)),
            ) as handle:
                step(lines, "first part", handle.advance)
                server.failure = mode
                step(lines, "failed wave", handle.advance)
                state = handle.checkpoint()
            server.failure = None
            before = server.creates
            with helper.resume(source, state) as resumed:
                step(lines, "run resumed", resumed.run)
                lines.append(
                    f"  zero creates on resume={server.creates == before} "
                    f"receipts={[part.index for part in resumed.advance().confirmed_parts]}"
                )
            server.report(lines, "server")
            exchange.responders.clear()
        for helper_name, mode in (
            ("parts", "complete"),
            ("parts", "decode"),
            ("parts", "token"),
            ("parts", "gateway"),
            ("parts_probe", "complete"),
            ("parts_probe", "decode"),
        ):
            lines.append(f"parts completion {helper_name} {mode}")
            server = _PartsServer()
            _respond(exchange, server)
            token = harness.options.CancelToken()
            selected = getattr(api.protocols.files, helper_name)
            with selected.start(
                harness.source(_PART_CONTENT),
                tus_resumable=harness.tus,
                options=harness.options.RequestOptions(cancel_token=token),
            ) as handle:
                step(lines, "wave", handle.advance)
                server.failure = mode
                server.complete_token = token if mode == "token" else None
                step(lines, "complete", handle.run, uncertain_delivery=mode == "token")
                step(lines, "complete again", handle.run, uncertain_delivery=mode == "token")
                state = handle.checkpoint()
            restored = step(
                lines,
                "resume completion",
                lambda selected=selected, state=state: selected.resume(harness.source(_PART_CONTENT), state),
                uncertain_delivery=mode == "token",
            )
            if restored is not None:
                restored.close()
            server.report(lines, "server")
            exchange.responders.clear()
        lines.append("parts changed source and layout")
        server = _PartsServer()
        _respond(exchange, server)
        source = _Changing(harness.protocols, _PART_CONTENT, b"x" + _PART_CONTENT[1:])
        with helper.start(source, tus_resumable=harness.tus) as handle:
            step(lines, "changed part", handle.advance)
            step(lines, "changed again", handle.run)
        server.report(lines, "changed")
        for settings in ({"chunk_bytes": 2}, {"chunk_bytes": 4, "max_parts": 3}):
            record(
                lines,
                "impossible layout",
                lambda settings=settings: helper.start(
                    harness.source(_PART_CONTENT), tus_resumable=harness.tus, upload_options=harness.uploads(**settings)
                ),
            )
        server.expiry = "2015-10-21T07:28:00Z"
        with helper.start(harness.source(_PART_CONTENT), tus_resumable=harness.tus) as handle:
            record(lines, "expired resume", lambda: helper.resume(harness.source(_PART_CONTENT), handle.checkpoint()))
        exchange.responders.clear()
        _parts_variants(harness, api, exchange, lines)
        _abort_rows(harness, api, exchange, lines)
    _session_urls(harness, lines)
    run(lambda: _async_parts(harness, lines))
    run(lambda: _async_session_urls(harness, lines))


def _abort_rows(harness: _Uploads, api: Any, exchange: Exchange, lines: list[str]) -> None:
    lines.append("remote abort")
    server = _PartsServer()
    _respond(exchange, server)
    source = harness.source(_PART_CONTENT)
    for name in ("parts_abort", "abort"):
        helper = getattr(api.protocols.files, name)
        with helper.start(source, tus_resumable=harness.tus) as handle:
            step(lines, name, handle.abort_remote)
            step(lines, "run aborted", handle.run)
            state = handle.checkpoint()
            record(lines, "resume aborted", lambda helper=helper, state=state: helper.resume(source, state))
        step(lines, "abort closed", handle.abort_remote)
    helper = api.protocols.files.parts_abort
    with helper.start(source, tus_resumable=harness.tus) as handle:
        step(lines, "finish", handle.run)
        step(lines, "abort complete", handle.abort_remote)
    with api.protocols.files.parts.start(source, tus_resumable=harness.tus) as handle:
        lines.append(f"  undeclared abort absent={not hasattr(handle, 'abort_remote')}")
    exchange.responders.clear()


async def _async_parts(harness: _Uploads, lines: list[str]) -> None:
    exchange = Exchange([])
    async with (
        exchange.async_client() as native,
        harness.package.AsyncClient(http_client=native, options=harness.client_options()) as api,
    ):
        helper = api.protocols.files.parts
        for capacity, shared in ((4, False), (1, False), (4, True)):
            lines.append(f"async parts ranges capacity={capacity} shared={shared}")
            server = _PartsServer()
            _respond(exchange, server)
            source = _AsyncRanges(harness.protocols, capacity=capacity, shared=shared)
            async with await helper.start(source, tus_resumable=harness.tus) as handle:
                if shared:
                    lines.append(f"  run: {await aoutcome(handle.run)}")
                else:
                    await astep(lines, "run", handle.run)
                source.report(lines)
                server.report(lines, "server")
            exchange.responders.clear()
        source = harness.protocols.AsyncBytesUploadSource.from_bytes(_PART_CONTENT)
        for mode in ("before", "during", "after", "fail", "list"):
            lines.append(f"async parts failure {mode}")
            server = _PartsServer()
            _respond(exchange, server)
            async with await helper.start(
                source,
                tus_resumable=harness.tus,
                upload_options=harness.uploads(parallelism=1),
                options=harness.options.RequestOptions(retry=harness.options.RetryOptions(max_retries=0)),
            ) as handle:
                await astep(lines, "first part", handle.advance)
                server.failure = mode
                await astep(lines, "failed wave", handle.advance)
                state = handle.checkpoint()
            server.failure = None
            before = server.creates
            async with await helper.resume(source, state) as resumed:
                await astep(lines, "run resumed", resumed.run)
                lines.append(f"  zero creates on resume={server.creates == before}")
            server.report(lines, "server")
            exchange.responders.clear()
        for helper_name, mode in (
            ("parts", "complete"),
            ("parts", "decode"),
            ("parts", "token"),
            ("parts", "gateway"),
            ("parts_probe", "complete"),
            ("parts_probe", "decode"),
        ):
            lines.append(f"async parts completion {helper_name} {mode}")
            server = _PartsServer()
            _respond(exchange, server)
            token = harness.options.CancelToken()
            selected = getattr(api.protocols.files, helper_name)
            async with await selected.start(
                source,
                tus_resumable=harness.tus,
                options=harness.options.RequestOptions(cancel_token=token),
            ) as handle:
                await astep(lines, "wave", handle.advance)
                server.failure = mode
                server.complete_token = token if mode == "token" else None
                await astep(lines, "complete", handle.run, uncertain_delivery=mode == "token")
                await astep(lines, "complete again", handle.run, uncertain_delivery=mode == "token")
                state = handle.checkpoint()
            restored = await astep(
                lines,
                "resume completion",
                lambda selected=selected, state=state: selected.resume(source, state),
                uncertain_delivery=mode == "token",
            )
            if restored is not None:
                await restored.aclose()
            server.report(lines, "server")
            exchange.responders.clear()
        for phase in ("part", "complete"):
            lines.append(f"async parts task cancellation {phase}")
            server = _PartsServer()
            _respond(exchange, server)
            async with await helper.start(
                source, tus_resumable=harness.tus, upload_options=harness.uploads(parallelism=1)
            ) as handle:
                if phase == "complete":
                    for _ in range(4):
                        await astep(lines, "wave", handle.advance)
                exchange.responders.clear()
                await _cancelled(lines, phase, exchange, handle.advance)
                _respond(exchange, server)
                state = handle.checkpoint()
                await astep(lines, "next step", handle.advance)
                resumed = await astep(lines, "resume", lambda state=state: helper.resume(source, state))
                if resumed is not None:
                    async with resumed:
                        await astep(lines, "run resumed", resumed.run)
            server.report(lines, "server")
            exchange.responders.clear()
        lines.append("async part cancelled by its responder")
        server = _PartsServer()
        token = harness.options.CancelToken()
        server.part_token = token
        _respond(exchange, server)
        async with await helper.start(
            source,
            tus_resumable=harness.tus,
            upload_options=harness.uploads(parallelism=1, max_uncertain_probes=0),
            options=harness.options.RequestOptions(cancel_token=token),
        ) as handle:
            await astep(lines, "first part", handle.advance)
            await astep(lines, "cancelled part", handle.advance)
            state = handle.checkpoint()
        async with await helper.resume(source, state) as resumed:
            await astep(lines, "run resumed", resumed.run)
        server.report(lines, "server")
        exchange.responders.clear()
        lines.append("async parts source change and expiration")
        server = _PartsServer()
        _respond(exchange, server)
        changing = _Changing(harness.protocols, _PART_CONTENT, b"x" + _PART_CONTENT[1:])
        changing.sources = iter((source,))
        changing.changed = harness.protocols.AsyncBytesUploadSource.from_bytes(b"x" + _PART_CONTENT[1:])
        async with await helper.start(changing, tus_resumable=harness.tus) as handle:
            await astep(lines, "changed", handle.advance)
            await astep(lines, "changed again", handle.run)
        server.report(lines, "server")
        server.expiry = "2015-10-21T07:28:00Z"
        async with await helper.start(source, tus_resumable=harness.tus) as handle:
            await astep(lines, "expired resume", lambda: helper.resume(source, handle.checkpoint()))
        exchange.responders.clear()
        for name in ("parts_abort", "abort"):
            lines.append(f"async remote abort {name}")
            server = _PartsServer()
            _respond(exchange, server)
            selected = getattr(api.protocols.files, name)
            async with await selected.start(source, tus_resumable=harness.tus) as handle:
                await astep(lines, "abort", handle.abort_remote)
                await astep(lines, "run aborted", handle.run)
                await astep(
                    lines, "resume aborted", lambda selected=selected: selected.resume(source, handle.checkpoint())
                )
            await astep(lines, "abort closed", handle.abort_remote)
            if name == "parts_abort":
                async with await selected.start(source, tus_resumable=harness.tus) as handle:
                    await astep(lines, "finish", handle.run)
                    await astep(lines, "abort complete", handle.abort_remote)
            server.report(lines, "server")
            exchange.responders.clear()


def _session_urls(harness: _Uploads, lines: list[str]) -> None:
    """Follow relative session URLs and reject unsafe or unapproved destinations before later sends."""
    for reference in (
        "/sessions/u1",
        "https://other.example.com/u1",
        "https://user:secret@api.example.com/u1",
        "/sessions/u1#part",
        "bad space",
        "x" * 8193,
    ):
        lines.append(f"session URL {reference if len(reference) < 100 else 'oversized'}")
        exchange = Exchange([])
        server = _Server()
        paths: list[str] = []

        def respond(
            request: httpx2.Request, paths: list[str] = paths, reference: str = reference, server: _Server = server
        ) -> httpx2.Response:
            paths.append(request.url.path)
            if request.method == "POST":
                result = server.create(request)
                result.headers["Location"] = reference
                return result
            translated = httpx2.Request(
                request.method, "https://api.example.com/files/u1", headers=request.headers, content=request.content
            )
            return server(translated)

        exchange.respond(*[injected(respond)] * 10)
        with (
            exchange.client() as native,
            harness.package.Client(http_client=native, options=harness.client_options()) as api,
        ):
            helper = api.protocols.files.url
            handle = step(
                lines, "start", lambda helper=helper: helper.start(harness.source(), tus_resumable=harness.tus)
            )
            if handle is not None:
                with handle:
                    step(lines, "advance", handle.advance)
                    state = handle.checkpoint()
                with helper.resume(harness.source(), state) as resumed:
                    step(lines, "run resumed", resumed.run)
                envelope = json.loads(state.export())
                for changed in (
                    "https://other.example.com/u1",
                    "https://user:secret@api.example.com/u1",
                    "/sessions/u1#part",
                    None,
                ):
                    broken = harness.protocols.ResumeState(
                        **{name: envelope[name] for name in ("helper_fingerprint", "security_fingerprint")},
                        state={**envelope["state"], "url": changed},
                        payload=state_payload(state.export()),
                    )
                    record(
                        lines,
                        "resume unsafe saved URL",
                        lambda broken=broken, helper=helper: helper.resume(harness.source(), broken),
                    )
            lines.append(f"  paths={paths}")
        exchange.responders.clear()
    lines.append("allowed cross-origin session URL strips credentials")
    exchange = Exchange([])
    server = _Server()
    received: list[tuple[str, bool, bool]] = []

    def followed(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            result = server.create(request)
            result.headers["Location"] = "https://other.example.com/session"
            return result
        received.append((request.url.host, "Authorization" in request.headers, "Cookie" in request.headers))
        translated = httpx2.Request(
            request.method, "https://api.example.com/files/u1", headers=request.headers, content=request.content
        )
        return server(translated)

    exchange.respond(*[injected(followed)] * 10)
    security = harness.protocols.ProtocolSecurityContext(
        credential_partition="tenant",
        allowed_origins=(harness.protocols.Origin(scheme="https", host="other.example.com", port=443),),
    )
    options = harness.client_options(
        headers=(("Authorization", "Bearer secret"), ("Cookie", "secret=value")),
        protocols=harness.options.ProtocolClientOptions(security=security),
    )
    with (
        exchange.client() as native,
        harness.package.Client(http_client=native, options=options) as api,
        api.protocols.files.url.start(harness.source(), tus_resumable=harness.tus) as handle,
    ):
        step(lines, "run", handle.run)
    lines.append(f"  cross-origin headers={received}")
    exchange.responders.clear()


async def _async_session_urls(harness: _Uploads, lines: list[str]) -> None:
    """Follow and resume the saved session URL with asyncio, rechecking unsafe references."""
    source = harness.protocols.AsyncBytesUploadSource.from_bytes(_CONTENT)
    for reference in (
        "/sessions/u1",
        "https://other.example.com/u1",
        "https://user:secret@api.example.com/u1",
        "/sessions/u1#part",
    ):
        lines.append(f"async session URL {reference}")
        exchange = Exchange([])
        server = _Server()
        paths: list[str] = []

        def respond(
            request: httpx2.Request, paths: list[str] = paths, reference: str = reference, server: _Server = server
        ) -> httpx2.Response:
            paths.append(request.url.path)
            if request.method == "POST":
                result = server.create(request)
                result.headers["Location"] = reference
                return result
            translated = httpx2.Request(
                request.method, "https://api.example.com/files/u1", headers=request.headers, content=request.content
            )
            return server(translated)

        exchange.respond(*[injected(respond)] * 10)
        async with (
            exchange.async_client() as native,
            harness.package.AsyncClient(http_client=native, options=harness.client_options()) as api,
        ):
            helper = api.protocols.files.url
            handle = await astep(lines, "start", lambda helper=helper: helper.start(source, tus_resumable=harness.tus))
            if handle is not None:
                async with handle:
                    await astep(lines, "advance", handle.advance)
                    state = handle.checkpoint()
                async with await helper.resume(source, state) as resumed:
                    await astep(lines, "run resumed", resumed.run)
            lines.append(f"  paths={paths}")
        exchange.responders.clear()


def _parts_variants(harness: _Uploads, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Exercise digest encodings, optional digests, layout bounds, and receipt checks."""
    for name, content in (
        ("parts_base64", _PART_CONTENT),
        ("parts_bare", _PART_CONTENT),
        ("parts_strict", _PART_CONTENT),
        ("parts_unlimited", _PART_CONTENT),
        ("parts_url", _PART_CONTENT),
        ("parts", b""),
        ("parts", b"AAAAB"),
    ):
        lines.append(f"parts variant {name} size={len(content)}")
        server = _PartsServer()
        server.encoding = "base64" if name == "parts_base64" else "hex"
        server.url = "/sessions/parts" if name == "parts_url" else None
        _respond(exchange, server)
        selected = getattr(api.protocols.files, name)
        source = harness.source(content)
        with selected.start(source, tus_resumable=harness.tus, upload_options=harness.uploads(chunk_bytes=4)) as handle:
            step(lines, "run", handle.run)
            state = handle.checkpoint()
        with selected.resume(source, state) as handle:
            step(lines, "complete resume", handle.run)
        server.report(lines, "server")
        exchange.responders.clear()
    for name, content in (("parts_strict", b"AAAAB"), ("parts", bytes(41))):
        record(
            lines,
            "API layout refused before create",
            lambda content=content, name=name: getattr(api.protocols.files, name).start(
                harness.source(content), tus_resumable=harness.tus
            ),
        )
    for label in (
        "digest",
        "missing",
        "receipt",
        "receipt type",
        "array type",
        "duplicate",
        "index",
        "malformed checkpoint",
    ):
        lines.append(f"parts resume invalid {label}")
        server = _PartsServer()
        _respond(exchange, server)
        helper = api.protocols.files.parts
        source = harness.source(_PART_CONTENT)
        with helper.start(source, tus_resumable=harness.tus) as handle:
            step(lines, "wave", handle.advance)
            state = handle.checkpoint()
        exchange.responders.clear()
        if label == "digest":
            server.bad_digest = True
            _respond(exchange, server)
        elif label == "malformed checkpoint":
            envelope = json.loads(state.export())
            state = harness.protocols.ResumeState(
                **{name: envelope[name] for name in ("helper_fingerprint", "security_fingerprint")},
                state={**envelope["state"], "parts": [[0, "receipt"]]},
                payload=state_payload(state.export()),
            )
        else:
            values = [
                {"index": index, "receipt": f"part-{index}", "digest": server.digest(content)}
                for index, content in sorted(server.parts.items())
            ]
            if label == "missing":
                values.pop()
            elif label == "receipt":
                values[0]["receipt"] = "changed"
            elif label == "receipt type":
                values[0]["receipt"] = 7
            elif label == "duplicate":
                values.append(values[0])
            elif label == "index":
                values[0]["index"] = 9
            exchange.respond(json_response(200, {"parts": 7 if label == "array type" else values}))
        record(lines, "resume", lambda helper=helper, source=source, state=state: helper.resume(source, state))
        server.report(lines, "server")
        exchange.responders.clear()

    lines.append("parts failed step retries its listing before sends")
    server = _PartsServer()
    _respond(exchange, server)
    with api.protocols.files.parts.start(
        harness.source(_PART_CONTENT), tus_resumable=harness.tus, upload_options=harness.uploads(parallelism=1)
    ) as handle:
        step(lines, "first part", handle.advance)
        server.failure = "fail"
        step(lines, "failed part", handle.advance)
        step(lines, "next step", handle.advance)
    handle.close()
    step(lines, "close again", handle.close)
    server.report(lines, "server")
    exchange.responders.clear()
    lines.append("parts explicit close releases ranges")
    server = _PartsServer()
    _respond(exchange, server)
    source = _Ranges(harness.protocols)
    handle = api.protocols.files.parts.start(source, tus_resumable=harness.tus)
    step(lines, "wave", handle.advance)
    handle.close()
    step(lines, "closed run", handle.run)
    source.report(lines)
    exchange.responders.clear()
    lines.append("parts completion probe stays pending")
    server = _PartsServer()
    server.failure, server.pending = "complete", True
    _respond(exchange, server)
    selected = api.protocols.files.parts_probe
    with selected.start(harness.source(_PART_CONTENT), tus_resumable=harness.tus) as handle:
        step(lines, "run", handle.run)
        state = handle.checkpoint()
    record(lines, "resume pending", lambda: selected.resume(harness.source(_PART_CONTENT), state))
    server.pending = False
    with selected.resume(harness.source(_PART_CONTENT), state) as resumed:
        step(lines, "confirmed completion", resumed.run)
    server.report(lines, "server")
    exchange.responders.clear()
    lines.append("parts body session URL has the wrong type")
    exchange.respond(json_response(201, {"id": "parts", "url": 7, "expires": _FUTURE}))
    record(
        lines,
        "start",
        lambda: api.protocols.files.parts_url.start(harness.source(_PART_CONTENT), tus_resumable=harness.tus),
    )

    lines.append("parts reader gives an extra byte")
    server = _PartsServer()
    _respond(exchange, server)
    source = _Ranges(harness.protocols, capacity=1)
    source.extra = True
    with api.protocols.files.parts.start(source, tus_resumable=harness.tus) as handle:
        step(lines, "advance", handle.advance)
    source.report(lines)
    server.report(lines, "server")
    exchange.responders.clear()

    lines.append("part cancelled by its responder")
    server = _PartsServer()
    token = harness.options.CancelToken()
    server.part_token = token
    _respond(exchange, server)
    helper = api.protocols.files.parts
    source = harness.source(_PART_CONTENT)
    with helper.start(
        source,
        tus_resumable=harness.tus,
        upload_options=harness.uploads(parallelism=1, max_uncertain_probes=0),
        options=harness.options.RequestOptions(cancel_token=token),
    ) as handle:
        step(lines, "first part", handle.advance)
        step(lines, "cancelled part", handle.advance)
        state = handle.checkpoint()
    with helper.resume(source, state) as resumed:
        step(lines, "run resumed", resumed.run)
    server.report(lines, "server")
    exchange.responders.clear()
