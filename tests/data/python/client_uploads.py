"""Upload content in chunks through generated resumable upload helpers, against an independent upload server.

The server keeps the bytes each upload received and answers as a tus server would; the scenarios compare what it
stored with the original content, and break appends, probes, and completions to drive every recovery path.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import threading
from contextlib import suppress
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_pagination import Harness
from tests.data.python.client_runtime import (
    Exchange,
    arecord,
    describe,
    failing,
    injected,
    json_response,
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
    """Keep each upload's bytes and answer creates, probes, appends, and completes independently."""

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


def _progress(value: object) -> str:
    """Describe an upload step's outcome: progress, an upload error with its own fields, or anything else."""
    if hasattr(value, "confirmed_bytes") and hasattr(value, "total_bytes"):
        progress: Any = value
        return f"progress {progress.confirmed_bytes}/{progress.total_bytes} complete={progress.complete}"
    if isinstance(value, BaseException):
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


def step(lines: list[str], label: str, call: Callable[[], object]) -> object:
    """Report a step's result or failure."""
    try:
        result = call()
    except Exception as error:  # noqa: BLE001 - Report failures from public calls.
        lines.append(f"  {label} ! {_progress(error)}")
        return None
    lines.append(f"  {label} = {_progress(result)}")
    return result


async def astep(lines: list[str], label: str, call: Callable[[], Any]) -> object:
    """Report an asyncio step's result or failure."""
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001 - Report failures from public calls.
        lines.append(f"  {label} ! {_progress(error)}")
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
    run(lambda: _fifo_ranges(harness, lines))


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
    lines.append(f"  {server.stored(f'u{len(server.uploads)}')}")


def _recoveries(harness: _Uploads, api: Any, server: _Server, exchange: Exchange, lines: list[str]) -> None:
    """Settle appends whose outcome is unknown by probing, and probe before the next append after any failure."""
    helper = api.protocols.files.upload
    lines.append("an append stored before the connection was lost")
    exchange.respond(server, server.lost(), server, server, server)
    handle = helper.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "advance", handle.advance)
    step(lines, "run", handle.run)
    lines.extend((
        f"  {server.stored(f'u{len(server.uploads)}')}",
        "an append lost before it was stored, then part of one stored",
    ))
    exchange.respond(server, failing(httpx2.ReadError), server, server, server.lost(2), server, server, server)
    handle = helper.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "advance", handle.advance)
    step(lines, "advance", handle.advance)
    step(lines, "run", handle.run)
    lines.extend((f"  {server.stored(f'u{len(server.uploads)}')}", "an append refused while connecting"))
    exchange.respond(server, failing(httpx2.ConnectError), server, server, server, server)
    handle = helper.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "advance", handle.advance)
    step(lines, "run", handle.run)
    lines.extend((f"  {server.stored(f'u{len(server.uploads)}')}", "probes that never answer"))
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
    lines.extend((f"  {server.stored(f'u{len(server.uploads)}')}", "the last append stored but answered with an error"))
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
        ("repeated", lambda _: httpx2.Response(200, headers=[("Upload-Offset", "4"), ("Upload-Offset", "4")])),
    ):
        exchange.respond(server, failing(httpx2.ReadError), responder)
        handle = upload.start(harness.source(), tus_resumable=harness.tus)
        step(lines, f"advance with a {label} offset", handle.advance)
    for label, body in (("string", {"offset": "4"}), ("null", {"offset": None}), ("negative", {"offset": -1})):
        exchange.respond(server, failing(httpx2.ReadError), json_response(200, body))
        handle = finish.start(harness.source(), tus_resumable=harness.tus)
        step(lines, f"advance with a {label} offset", handle.advance)


def _completions(harness: _Uploads, api: Any, server: _Server, exchange: Exchange, lines: list[str]) -> None:
    """Never send a completion of unknown outcome, a 502 or 504 included, again; send one an error answered again."""
    helper = api.protocols.files.finish
    lines.append("a completion of unknown outcome")
    exchange.respond(server, server, server, failing(httpx2.ReadError))
    handle = helper.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "run", handle.run)
    step(lines, "run again", handle.run)
    step(lines, "advance again", handle.advance)
    state = handle.checkpoint()
    step(lines, "resume", lambda state=state: helper.resume(harness.source(), state))
    lines.append("a completion an error answered")
    exchange.respond(server, server, server, raw_response(500), server)
    handle = helper.start(harness.source(), tus_resumable=harness.tus)
    step(lines, "run", handle.run)
    step(lines, "run again", handle.run)
    for status in (502, 504):
        lines.append(f"a completion a gateway answered with {status}")
        exchange.respond(server, server, server, raw_response(status))
        handle = helper.start(harness.source(), tus_resumable=harness.tus)
        step(lines, "run", handle.run)
        step(lines, "run again", handle.run)
        step(lines, "advance again", handle.advance)
        state = handle.checkpoint()
        step(lines, "resume", lambda state=state: helper.resume(harness.source(), state))
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

    async def stream() -> AsyncIterator[bytes]:  # noqa: RUF029 - The public adapter requires an async callback.
        yield _CONTENT

    class Ranges:
        identity = harness.source().identity
        max_parallel_ranges = 0

        def open_range(self, offset: int, length: int) -> object:  # noqa: ARG002 - Keep the public range-source method signature.
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

        def open_range(self, offset: int, length: int) -> Any:  # noqa: ARG002 - Keep the public range-source method signature.
            from contextlib import nullcontext

            return nullcontext(io.StringIO(_CONTENT.decode()))

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
        ("past the end", lambda: source.open_range(5, 10).__enter__()),  # noqa: PLC2801 - Exercise errors raised on context entry.
        ("a negative offset", lambda: source.open_range(-1, 1).__enter__()),  # noqa: PLC2801 - Exercise errors raised on context entry.
    ):
        try:
            call()
        except ValueError:  # noqa: PERF203 - Each step must report failures and continue to sibling outcomes.
            found.append(f"{label} refused")
    return ", ".join(found)


def _resumes(harness: _Uploads, api: Any, server: _Server, exchange: Exchange, lines: list[str]) -> None:  # noqa: PLR0914 - Exercise the upload lifecycle in one scenario.
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
    with exchange.client() as native, harness.package.Client(http_client=native, options=secured) as secured_api:
        record(lines, "resume", lambda: secured_api.protocols.files.upload.resume(harness.source(), state))


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


async def _async_uploads(harness: _Uploads, server: _Server, lines: list[str]) -> None:  # noqa: PLR0914 - Exercise the upload lifecycle in one scenario.
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
            await asyncio.to_thread(path.write_bytes, _CONTENT)
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
                await asyncio.to_thread(path.write_bytes, b"0123456789abc")
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


async def _fifo_ranges(harness: _Uploads, lines: list[str]) -> None:
    """Refuse ranges after FIFO replacement without a writer; use a changed file where FIFOs are unsupported."""
    lines.append("file ranges after FIFO replacement (where supported)")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory, "content.bin")
        await asyncio.to_thread(path.write_bytes, _CONTENT)
        source = harness.protocols.FileUploadSource.from_path(path)

        def opened() -> bytes:
            with source.open_range(2, 3) as synchronous:
                return synchronous.read(3)

        async with (
            await harness.protocols.AsyncFileUploadSource.from_path(path) as asynchronous,
            asynchronous.open_range(2, 3) as reader,
        ):
            if mkfifo := getattr(os, "mkfifo", None):
                await asyncio.to_thread(path.unlink)
                await asyncio.to_thread(mkfifo, path)
            else:
                await asyncio.to_thread(path.write_bytes, _CONTENT + b"!")
            step(lines, "sync open_range", opened)
            await astep(lines, "async open_range", lambda: _async_ranges(asynchronous))
            lines.append(f"  existing async range {await reader.read(3)!r}")
            await reader.aclose()
            lines.append(f"  existing async range after close {await reader.read(1)!r}")
        lines.append("  async source closed")


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


class _CompletionResponse:
    """An independently authored response for token/create/probe/completion resource controls."""

    def __init__(self, headers: Any, status: int, content: bytes) -> None:
        self.status_code = status
        self.headers = headers((("content-type", "application/json"),))
        self.content = content
        self.closed = False

    def iter_raw_bytes(self) -> Iterator[bytes]:
        yield self.content

    def close(self) -> None:
        self.closed = True


class _AsyncCompletionResponse(_CompletionResponse):
    async def iter_raw_bytes(self) -> AsyncIterator[bytes]:
        yield self.content

    async def aclose(self) -> None:
        self.close()


class _CompletionTransport:
    """Count actual token and resource sends separately, holding token traffic before resource admission."""

    def __init__(self, harness: _Uploads, *, token: bool = False, asynchronous: bool = False) -> None:
        import importlib

        transports = importlib.import_module(f"{harness.package.__name__}.transports")
        responses = importlib.import_module(f"{harness.package.__name__}.responses")
        self.capabilities = transports.TransportCapabilities(
            internal_retry_limit=0, delivery_evidence=True, http_versions=("HTTP/1.1",)
        )
        self.headers = responses.HeadersView
        self.token = token
        event = asyncio.Event if asynchronous else threading.Event
        self.started, self.release = event(), event()
        self.methods: list[str] = []
        self.responses: list[Any] = []
        self.kind = _AsyncCompletionResponse if asynchronous else _CompletionResponse
        self.server = _Server()

    def _sent(self, request: Any, context: Any) -> None:
        self.methods.append(f"{request.method} {request.url.split('example.com')[-1]}")
        context.trace.request_headers_started()
        context.trace.wire_send()
        self.started.set()

    def _reply(self, request: Any, context: Any) -> Any:
        if self.token:
            content = b'{"access_token":"upload-control","token_type":"Bearer","expires_in":3600}'
            status = 200
        else:
            wire = self.server(httpx2.Request(request.method, request.url, content=b""))
            status, content = wire.status_code, wire.read()
        reply = self.kind(self.headers, status, content)
        self.responses.append(reply)
        context.trace.response_headers_received(http_version="HTTP/1.1", status_code=status, headers=reply.headers)
        return reply

    def send(self, request: Any, context: Any) -> Any:
        self._sent(request, context)
        if self.token and not self.release.wait(30):
            msg = "token watchdog"
            raise RuntimeError(msg)
        return self._reply(request, context)

    def close(self) -> None:
        """Leave this borrowed adapter with its caller."""


class _AsyncCompletionTransport(_CompletionTransport):
    async def send(self, request: Any, context: Any) -> Any:
        self._sent(request, context)
        if self.token:
            await self.release.wait()
        return self._reply(request, context)

    async def aclose(self) -> None:
        """Leave this borrowed adapter with its caller."""


def _completion_provider(harness: _Uploads, token: Any, *, asynchronous: bool = False) -> Any:
    import importlib

    auth = importlib.import_module(f"{harness.package.__name__}.auth")
    kind = auth.AsyncClientCredentialsProvider if asynchronous else auth.ClientCredentialsProvider
    secret = auth.AsyncStaticCredentialProvider if asynchronous else auth.StaticCredentialProvider
    provider = kind(
        "https://auth.example.com/token",
        client_id="upload-control",
        client_secret=secret(auth.ApiKeyCredential("control")),
        token_transport=token,
    )
    return provider, harness.options.ClientOptions(auth=auth.AuthConfig({"oauth": provider}), cleanup_timeout=0.05)


def uploads_oauth(package: ModuleType, lines: list[str]) -> None:
    """Close during completion OAuth, then explicitly resume the proven-unsent resource on a fresh client."""
    from concurrent.futures import ThreadPoolExecutor

    harness = _Uploads(package)
    token, resource = _CompletionTransport(harness, token=True), _CompletionTransport(harness)
    provider, settings = _completion_provider(harness, token)
    api = package.Client(transport_adapter=resource, options=settings)
    source = harness.protocols.BytesUploadSource.from_bytes(b"")
    handle = api.protocols.files.finish.start(source, tus_resumable=harness.tus)
    with ThreadPoolExecutor(max_workers=1) as executor:
        work = executor.submit(handle.run)
        if not token.started.wait(30):
            msg = "token admission watchdog"
            raise RuntimeError(msg)
        with suppress(harness.errors.CleanupError):
            api.close()
        token.release.set()
        try:
            work.result(timeout=30)
        except harness.errors.ClientClosedError as error:
            lines.append(f"sync original failure: {type(error).__name__} cause={error.cause}")
    state = handle.checkpoint()
    lines.append(f"  handle {handle!r} token={len(token.methods)} resource={resource.methods}")
    with package.Client(transport_adapter=resource, options=settings) as fresh:
        resumed = fresh.protocols.files.finish.resume(source, state)
        result = resumed.run()
        resumed.run()
        lines.append(f"  resumed size={result.size} token={len(token.methods)} resource={resource.methods}")
        _completion_end_control(harness, fresh, resource, lines)
    lines.append(f"  responses closed={all(reply.closed for reply in token.responses + resource.responses)}")
    handle.close()
    api.close()
    provider.close()
    run(lambda: _async_completion_oauth(harness, lines))


async def _async_completion_oauth(harness: _Uploads, lines: list[str]) -> None:
    token = _AsyncCompletionTransport(harness, token=True, asynchronous=True)
    resource = _AsyncCompletionTransport(harness, asynchronous=True)
    provider, settings = _completion_provider(harness, token, asynchronous=True)
    api = harness.package.AsyncClient(transport_adapter=resource, options=settings)
    source = harness.protocols.AsyncBytesUploadSource.from_bytes(b"")
    handle = await api.protocols.files.finish.start(source, tus_resumable=harness.tus)
    work = asyncio.create_task(handle.run())
    await asyncio.wait_for(token.started.wait(), timeout=30)
    with suppress(harness.errors.CleanupError):
        await api.aclose()
    token.release.set()
    try:
        await asyncio.wait_for(work, timeout=30)
    except harness.errors.ClientClosedError as error:
        lines.append(f"async original failure: {type(error).__name__} cause={error.cause}")
    state = handle.checkpoint()
    lines.append(f"  handle {handle!r} token={len(token.methods)} resource={resource.methods}")
    async with harness.package.AsyncClient(transport_adapter=resource, options=settings) as fresh:
        resumed = await fresh.protocols.files.finish.resume(source, state)
        result = await resumed.run()
        await resumed.run()
        lines.append(f"  resumed size={result.size} token={len(token.methods)} resource={resource.methods}")
        await _async_completion_end_control(harness, fresh, resource, lines)
    lines.append(f"  responses closed={all(reply.closed for reply in token.responses + resource.responses)}")
    await handle.aclose()
    await api.aclose()
    await provider.aclose()


class _CancelCompletionEnd:
    """Cancel after the completion response has been decoded, retaining its already acquired result."""

    def __init__(self, token: Any) -> None:
        self.token = token

    def on_event(self, event: Any) -> None:
        if event.name == "call_end" and event.operation_id == "completeFile":
            self.token.cancel()


def _completion_end_control(harness: _Uploads, api: Any, resource: Any, lines: list[str]) -> None:
    token = harness.options.CancelToken()
    source = harness.protocols.BytesUploadSource.from_bytes(b"")
    handle = api.protocols.files.finish.start(
        source,
        tus_resumable=harness.tus,
        options=harness.options.RequestOptions(cancel_token=token, hooks=(_CancelCompletionEnd(token),)),
    )
    try:
        handle.run()
    except harness.errors.RequestCancelledError:
        lines.append(f"  cancelled after decoded completion: retained={handle.run().size} resource={resource.methods}")
    handle.close()


async def _async_completion_end_control(harness: _Uploads, api: Any, resource: Any, lines: list[str]) -> None:
    token = harness.options.CancelToken()
    source = harness.protocols.AsyncBytesUploadSource.from_bytes(b"")
    handle = await api.protocols.files.finish.start(
        source,
        tus_resumable=harness.tus,
        options=harness.options.RequestOptions(cancel_token=token, hooks=(_CancelCompletionEnd(token),)),
    )
    try:
        await handle.run()
    except harness.errors.RequestCancelledError:
        result = await handle.run()
        lines.append(f"  cancelled after decoded completion: retained={result.size} resource={resource.methods}")
    await handle.aclose()
