"""Exercise error-only operations and JSON failure contexts through generated clients and real HTTP exchanges."""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any

from tests.data.python.client_generation import SOURCE
from tests.data.python.client_runtime import Exchange, arecord, json_response, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType


def no_success(package: ModuleType, lines: list[str]) -> None:
    """Keep error-only aliases returnable while declared errors and undeclared successes still raise."""
    types = importlib.import_module(f"{package.__name__}.types.archives")
    lines.extend(
        f"  {name}Response = {getattr(types, f'{name}Response')!r}"
        for name in ("CreateArchive", "ReadArchive", "CheckArchive", "EmptyArchive")
    )
    exchange = Exchange(lines)
    with package.Client(http_client=exchange.client(), http_client_ownership="owned") as api:
        for label, name, response in _responses():
            exchange.respond(response)
            record(lines, label, getattr(api.archives, name))
        exchange.respond(raw_response(409))
        record(lines, "metadata error", api.archives.with_response.create_archive)
    run(lambda: _async_no_success(package, exchange, lines))


def _responses() -> Iterator[tuple[str, str, Any]]:
    """Yield declared errors and undeclared successes for the error-only operations."""
    yield "bodyless error", "create_archive", raw_response(409)
    yield "JSON error", "read_archive", json_response(404, {"detail": "missing"})
    yield "range error", "check_archive", raw_response(403)
    yield "undeclared success", "create_archive", raw_response(200)
    yield "empty responses", "empty_archive", raw_response(204)


async def _async_no_success(package: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    """Exercise the asynchronous error-only operations and their metadata view."""
    async with package.AsyncClient(http_client=exchange.async_client(), http_client_ownership="owned") as api:
        for label, name, response in _responses():
            exchange.respond(response)
            await arecord(lines, f"async {label}", getattr(api.archives, name))
        exchange.respond(raw_response(409))
        await arecord(lines, "async metadata error", api.archives.with_response.create_archive)


def _bodies() -> Iterator[tuple[str, bytes]]:
    """Build malformed JSON bodies from the external regression cases."""
    for case in json.loads((SOURCE / "json-decode-errors.json").read_text(encoding="utf-8")):
        if "hex" in case:
            yield case["name"], bytes.fromhex(case["hex"])
        else:
            text = (
                case.get("prefix", "")
                + case["text"] * case.get("repeat", 1)
                + case.get("suffix", "") * case.get("suffix_repeat", 1)
            )
            yield case["name"], text.encode()


def _failure(error: Any) -> str:
    """Report the decode cause's classification, retained exception links, and body prefix."""
    cause = error.cause
    codes = ",".join(issue.code for issue in getattr(cause, "issues", ()))
    prefix = error.raw_prefix if hasattr(error, "raw_prefix") else error.body_bytes
    return (
        f"{type(error).__name__} {type(cause).__name__} {codes} "
        f"context={cause.__context__!r} cause={cause.__cause__!r} traceback={cause.__traceback__ is not None} "
        f"prefix={len(prefix)} truncated={error.truncated} retained={retained_body(error, len(prefix))}"
    )


def json_error_body(name: str) -> bytes:
    """Return one malformed JSON body from the external regression cases."""
    return next(body for label, body in _bodies() if label == name)


def retained_body(error: BaseException, limit: int) -> bool:
    """Report whether an SDK traceback owns a payload larger than the publicly retained bytes."""
    runtime = type(error).__module__.rsplit(".", 2)[0] + "."
    trace = error.__traceback__
    while trace is not None:
        frame = trace.tb_frame
        if frame.f_globals.get("__name__", "").startswith(runtime):
            for value in frame.f_locals.values():
                payload = value if isinstance(value, bytes) else getattr(value, "body", getattr(value, "data", None))
                if isinstance(payload, bytes) and len(payload) > limit:
                    return True
        trace = trace.tb_next
    return False


def _stream(api: Any, body: bytes) -> tuple[Any, bytes, str]:
    """Frame a malformed JSON record for the generated SSE or NDJSON helper."""
    if hasattr(api.protocols, "events"):
        return api.protocols.events.messages, b"data: " + body + b"\n\n", "text/event-stream"
    return api.protocols.records.all, body + b"\n", "application/x-ndjson"


def json_decode_errors(package: ModuleType, lines: list[str]) -> None:
    """Inspect every JSON parse failure and large malformed stream records without retaining their original errors."""
    exchange = Exchange(lines)
    errors = importlib.import_module(f"{package.__name__}.errors")
    with package.Client(http_client=exchange.client(), http_client_ownership="owned") as api:
        for label, body in _bodies():
            exchange.respond(raw_response(200, body, "application/json"))
            try:
                api.status.get_status()
            except errors.DecodeError as error:
                lines.append(f"  {label}: {_failure(error)}")
        for label, body in _bodies():
            if label in {"syntax", "large syntax"}:
                helper, content, media = _stream(api, body)
                exchange.respond(raw_response(200, content, media))
                with helper.open() as stream:
                    try:
                        next(stream)
                    except errors.StreamDecodeError as error:
                        lines.append(f"  stream {label}: {_failure(error)}")
    run(lambda: _async_json_decode_errors(package, exchange, lines))


async def _async_json_decode_errors(package: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    """Inspect asynchronous JSON response and stream failures through real HTTP exchanges."""
    errors = importlib.import_module(f"{package.__name__}.errors")
    async with package.AsyncClient(http_client=exchange.async_client(), http_client_ownership="owned") as api:
        for label, body in _bodies():
            exchange.respond(raw_response(200, body, "application/json"))
            try:
                await api.status.get_status()
            except errors.DecodeError as error:
                lines.append(f"  async {label}: {_failure(error)}")
        for label, body in _bodies():
            if label in {"syntax", "large syntax"}:
                helper, content, media = _stream(api, body)
                exchange.respond(raw_response(200, content, media))
                async with await helper.open() as stream:
                    try:
                        await anext(stream)
                    except errors.StreamDecodeError as error:
                        lines.append(f"  async stream {label}: {_failure(error)}")
