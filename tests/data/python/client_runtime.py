"""Generate client packages, call them through a local HTTPS server, and report every exchange and failure."""

from __future__ import annotations

import asyncio
import gzip
import importlib
import json
import os
import re
import shutil
import sys
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_generation import SOURCE, copy_references, generate_client
from tests.data.python.fixture_server import AsyncLocalTransport, FixtureServer, Injected, LocalTransport
from tests.data.python.generated_packages import forget_generated, import_generated

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from pathlib import Path
    from types import ModuleType

_CALL_ID: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_ERROR_FIELDS: Final = (
    "operation_id",
    "field_path",
    "condition",
    "location",
    "delivery_state",
    "phase",
    "status_code",
    "body",
    "body_bytes",
    "truncated",
    "actual_media_type",
    "expected_media_types",
    "representation",
    "kind",
    "unit",
    "limit",
    "observed_bytes",
    "coding",
    "cause",
)


def _generate(case: dict[str, Any], backend: str, root: Path, package: str) -> None:
    copy_references(case, root)
    generate_client(
        shutil.copy2(SOURCE / case["input"], root / case["input"]),
        root,
        package,
        backend,
        case.get("model"),
        case.get("config"),
    )


class Exchange:
    """Answer each request through a queue of responders, recording the request line, headers, and body.

    Its clients send through HTTPX2's transports to a local HTTPS server that answers with the queued responses;
    a responder marked as injected answers in-process instead, for failures no server can produce.
    """

    def __init__(self, lines: list[str]) -> None:
        """Start with no queued responder and no server."""
        self.lines = lines
        self.responders: list[Callable[[httpx2.Request], httpx2.Response]] = []
        self.server: FixtureServer | None = None
        self.transports = 0
        self.gzipped: bytes | None = None

    def respond(self, *responders: Callable[[httpx2.Request], httpx2.Response]) -> None:
        """Queue the responders of the next requests."""
        self.responders.extend(responders)

    def client(self, connections: int = 10, kind: type[httpx2.Client] = httpx2.Client, **options: Any) -> httpx2.Client:
        """Return an HTTPX2 client of a kind that sends through this exchange's server over at most `connections`."""
        self.transports += 1
        return kind(transport=LocalTransport(self, connections), **options)

    def async_client(
        self, connections: int = 10, kind: type[httpx2.AsyncClient] = httpx2.AsyncClient, **options: Any
    ) -> httpx2.AsyncClient:
        """Return an asyncio HTTPX2 client of a kind that sends through this exchange's server over `connections`."""
        self.transports += 1
        return kind(transport=AsyncLocalTransport(self, connections), **options)

    def port(self) -> int:
        """Return the port of the server, starting it first."""
        if self.server is None:
            self.server = FixtureServer(self.handle)
        return self.server.server_port

    def injected(self) -> bool:
        """Return whether the next responder answers in-process."""
        return bool(self.responders) and isinstance(self.responders[0], Injected)

    def release(self) -> None:
        """Stop the server once every transport of this exchange is closed."""
        self.transports -= 1
        if not self.transports and (server := self.server) is not None:
            self.server = None
            server.stop()

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Record a request and answer it with the next queued responder.

        A gzip body is recorded decompressed, with its compressed length masked, which zlib builds may change, and
        marked when its compressed bytes repeat the previous gzip body's.
        """
        request.read()
        content, coded = request.content, request.headers.get("content-encoding") == "gzip"
        headers = ", ".join(
            f"{name}: {'<gzip>' if coded and name == 'content-length' else value}"
            for name, value in request.headers.multi_items()
        )
        body = repr(content)
        if coded:
            body = f"gzip{' again' if content == self.gzipped else ''} {gzip.decompress(content)!r}"
            self.gzipped = content
        self.lines.extend((f"  > {request.method} {request.url}", f"    [{headers}] {body}"))
        return self.responders.pop(0)(request)

    async def ahandle(self, request: httpx2.Request) -> httpx2.Response:
        """Record an async request, reading its body first, and answer it with the next responder."""
        await request.aread()
        return self.handle(request)


def _operation(package: ModuleType, operation: str) -> Any:
    """Return the plan of a generated package's operation by its operation id, or by `METHOD /path` without one."""
    plans = importlib.import_module(f"{package.__name__}._runtime.client.operations").OperationPlan
    return next(
        plan
        for plan in vars(importlib.import_module(f"{package.__name__}._operations")).values()
        if isinstance(plan, plans) and operation in {plan.operation_id, f"{plan.method} {plan.path}"}
    )


def argument(package: ModuleType, operation_id: str, location: str, name: str, wire: object) -> object:
    """Return the native argument a parameter's wire value builds, as a resumed call builds a saved one."""
    specs = _operation(package, operation_id).parameters
    return next(spec for spec in specs if (spec.plan.location, spec.plan.name) == (location, name)).restored(wire)


def request_body(package: ModuleType, operation_id: str, media_type: str | None, wire: object) -> object:
    """Return the native body a declared media type's wire value builds, as a resumed call builds a saved one."""
    return _operation(package, operation_id).body.select(operation_id, media_type).restored(wire)


def form_part(
    package: ModuleType, operation_id: str, name: str, wire: object, media_type: str | None = None
) -> object:
    """Return the native value a form-data member's wire value builds: a declared member's, or another part's."""
    media = _operation(package, operation_id).body.select(operation_id, media_type)
    return next((plan for plan in media.parts if plan.name == name), media.additional_part).encoder.restored(wire)


def describe(value: object) -> str:
    """Describe a call outcome: an error with its safe fields, a response with its metadata, or a value."""
    match value:
        case BaseException():
            details = ", ".join(
                f"{name}={getattr(value, name)!r}"
                for name in _ERROR_FIELDS
                if getattr(value, name, None) not in (None, ())
            )
            code = f" {value.reason_code}" if hasattr(value, "reason_code") else ""
            return f"{type(value).__name__}: {value} [{details}]{code}"
        case _ if hasattr(value, "info") and hasattr(value, "data"):
            info = value.info
            headers = list(info.headers)
            return (
                f"Response({value.data!r}, {info.status_code}, {info.content_type!r}, {info.request_id!r}, {headers})"
            )
        case _:
            pass
    return repr(value)


def record(lines: list[str], label: str, call: Callable[[], object]) -> object:
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {describe(error)}")
        return None
    lines.append(f"  {label} = {describe(result)}")
    return result


def outcome(call: Callable[[], object]) -> str:
    """Report the class of a call's failure with the classes of its secondary errors, or its result."""
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        return f"{type(error).__name__} secondary {[type(item).__name__ for item in getattr(error, 'secondary_errors', ())]}"
    return f"returned {result!r}"


async def aoutcome(call: Callable[[], Any]) -> str:
    """Report the class of an async call's failure with the classes of its secondary errors, or its result."""
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        return f"{type(error).__name__} secondary {[type(item).__name__ for item in getattr(error, 'secondary_errors', ())]}"
    return f"returned {result!r}"


async def arecord(lines: list[str], label: str, call: Callable[[], Any]) -> object:
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {describe(error)}")
        return None
    lines.append(f"  {label} = {describe(result)}")
    return result


def _streamed(status: int, content: bytes, headers: dict[str, str]) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder that streams its body as a server does, with the Content-Length HTTPX2 would add."""
    fields = {**headers, **({"content-length": str(len(content))} if content else {})}
    return lambda _: httpx2.Response(status, headers=fields, stream=httpx2.ByteStream(content))


def json_response(status: int, payload: object, **headers: str) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of a JSON body, encoded as HTTPX2 encodes one."""
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    return _streamed(
        status, content, {**headers, "content-length": str(len(content)), "content-type": "application/json"}
    )


def raw_response(
    status: int, content: bytes = b"", content_type: str | None = None, **headers: str
) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of raw bytes with an optional Content-Type."""
    return _streamed(status, content, {**headers, **({} if content_type is None else {"content-type": content_type})})


class _Chunks(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        yield from self.chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


def chunked_response(
    status: int, content: bytes, size: int, content_type: str, **headers: str
) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder that streams raw bytes in chunks of a size."""
    chunks = tuple(content[start : start + size] for start in range(0, len(content), size))
    fields = {**headers, "content-type": content_type}
    return lambda _: httpx2.Response(status, headers=fields, stream=_Chunks(chunks))


def failing(error: type[httpx2.TransportError]) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return an injected responder that fails with a transport error before any response."""

    def fail(request: httpx2.Request) -> httpx2.Response:
        raise error("failed", request=request)

    return Injected(fail)


def injected(responder: Callable[[httpx2.Request], httpx2.Response]) -> Callable[[httpx2.Request], httpx2.Response]:
    """Mark a responder that injects a failure no server can produce, so it answers in-process."""
    return Injected(responder)


class _BrokenStream(httpx2.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b'{"partial"'
        msg = "connection reset"
        raise httpx2.ReadError(msg)


class _AsyncBrokenStream(httpx2.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'{"partial"'
        msg = "connection reset"
        raise httpx2.ReadError(msg)


@Injected
def broken(request: httpx2.Request) -> httpx2.Response:
    """Answer with headers, then fail while the body streams."""
    del request
    return httpx2.Response(200, headers={"content-type": "application/json"}, stream=_BrokenStream())


@Injected
def abroken(request: httpx2.Request) -> httpx2.Response:
    """Answer an async request with headers, then fail while the body streams."""
    del request
    return httpx2.Response(200, headers={"content-type": "application/json"}, stream=_AsyncBrokenStream())


def generated(case_name: str, backend: str, root: Path, scenario: Callable[[ModuleType, list[str]], None]) -> str:
    """Generate one fixture's package for a backend, run a scenario against it, and return the cleaned report."""
    case = json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))[case_name]
    package = f"{case_name.replace('-', '_')}_{backend.replace('.', '_').lower()}"
    root.mkdir(parents=True, exist_ok=True)
    _generate(case, backend, root, package)
    paths = [str(root), str(root / package / "src")]
    sys.path[:0] = paths
    lines = [f"# {case_name} {backend}"]
    try:
        copied = os.environ.get("DATAMODEL_CODE_GENERATOR_CLIENT_COPIED_RUNTIME_E2E") == "1"
        scenario(importlib.import_module(package) if copied else import_generated(package), lines)
    finally:
        del sys.path[: len(paths)]
        forget_generated(package)
    return _CALL_ID.sub("<call>", "\n".join(lines)) + "\n"


def run(coroutine: Callable[[], Any]) -> None:
    """Run an async scenario to completion."""
    asyncio.run(coroutine())
