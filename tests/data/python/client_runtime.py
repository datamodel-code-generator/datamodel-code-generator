"""Generate client packages, call them through HTTPX2 mock transports, and report every exchange and failure."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import httpx2

from datamodel_code_generator import DataModelType, GenerateConfig
from datamodel_code_generator._api_generation import generate_target
from datamodel_code_generator._client.target import ClientTarget
from datamodel_code_generator.enums import OpenAPIScope
from datamodel_code_generator.format import Formatter
from tests.data.python.client_generation import SOURCE, client_config
from tests.data.python.generated_packages import forget_generated, import_generated

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
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
    "error_decoded",
    "error_data",
    "error_decode_error",
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
    generate_target(
        shutil.copy2(SOURCE / case["input"], root / case["input"]),
        model_config=GenerateConfig(
            output=root / f"{package}_models.py",
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
            output_model_type=DataModelType(backend),
            disable_timestamp=True,
            formatters=[Formatter.BUILTIN],
        ),
        config=client_config(
            {"output": package, "package": package, "model_package": f"{package}_models", **case.get("config", {})},
            root,
        ),
        generator=ClientTarget(),
    )


class Exchange:
    """Answer each request through a queue of responders, recording the request line, headers, and body."""

    def __init__(self, lines: list[str]) -> None:
        """Start with no queued responder."""
        self.lines = lines
        self.responders: list[Callable[[httpx2.Request], httpx2.Response]] = []

    def respond(self, *responders: Callable[[httpx2.Request], httpx2.Response]) -> None:
        """Queue the responders of the next requests."""
        self.responders.extend(responders)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Record a request and answer it with the next queued responder."""
        headers = ", ".join(f"{name}: {value}" for name, value in request.headers.multi_items())
        self.lines.extend((f"  > {request.method} {request.url}", f"    [{headers}] {request.content!r}"))
        return self.responders.pop(0)(request)

    async def ahandle(self, request: httpx2.Request) -> httpx2.Response:
        """Record an async request, reading its body first, and answer it with the next responder."""
        await request.aread()
        return self.handle(request)


def describe(value: object) -> str:
    """Describe a call outcome: an error with its safe fields, a response with its metadata, or a value."""
    match value:
        case BaseException():
            details = ", ".join(
                f"{name}={getattr(value, name)!r}"
                for name in _ERROR_FIELDS
                if getattr(value, name, None) not in (None, ())
            )
            return f"{type(value).__name__}: {value} [{details}] {value.reason_code if hasattr(value, 'reason_code') else ''}"
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


async def arecord(lines: list[str], label: str, call: Callable[[], Any]) -> object:
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {describe(error)}")
        return None
    lines.append(f"  {label} = {describe(result)}")
    return result


def json_response(status: int, payload: object, **headers: str) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of a JSON body."""
    return lambda _: httpx2.Response(status, json=payload, headers=headers)


def raw_response(
    status: int, content: bytes = b"", content_type: str | None = None, **headers: str
) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of raw bytes with an optional Content-Type."""
    fields = {**headers, **({} if content_type is None else {"content-type": content_type})}
    return lambda _: httpx2.Response(status, content=content, headers=fields)


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
    """Return a responder that fails with a transport error before any response."""

    def fail(request: httpx2.Request) -> httpx2.Response:
        raise error("failed", request=request)

    return fail


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


def broken(request: httpx2.Request) -> httpx2.Response:
    """Answer with headers, then fail while the body streams."""
    del request
    return httpx2.Response(200, headers={"content-type": "application/json"}, stream=_BrokenStream())


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
        scenario(import_generated(package), lines)
    finally:
        del sys.path[: len(paths)]
        forget_generated(package)
    return _CALL_ID.sub("<call>", "\n".join(lines)) + "\n"


def run(coroutine: Callable[[], Any]) -> None:
    """Run an async scenario to completion."""
    asyncio.run(coroutine())
