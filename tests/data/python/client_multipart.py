"""Send multipart/form-data bodies from generated clients: an object's members as parts, or parts given one by one."""

from __future__ import annotations

import importlib
import io
import re
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_bodies import Chunks, async_attempt_factory, attempt_factory
from tests.data.python.client_runtime import Exchange, aoutcome, arecord, outcome, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_BOUNDARY: Final = re.compile(r"dcg[0-9a-f]{32}")
_PROFILE: Final = {
    "name": "Ada",
    "age": 36,
    "active": True,
    "tags": ["a", "b"],
    "address": {"city": "London", "codes": [1, 2]},
    "nickname": None,
}


def _modules(package: ModuleType) -> tuple[ModuleType, ModuleType, ModuleType]:
    bodies, options, types = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("bodies", "options", "types.forms")
    )
    return bodies, options, types


def multipart(package: ModuleType, lines: list[str]) -> None:
    """Send form-data bodies synchronously and with asyncio, and refuse parts that cannot be sent."""
    exchange = Exchange(lines)
    http = httpx2.Client(transport=httpx2.MockTransport(exchange.handle))
    with package.Client(http_client=http) as api:
        _profiles(package, api, exchange, lines)
        _parts(package, api, exchange, lines)
    http.close()
    run(lambda: _async_multipart(package, lines))
    lines[:] = [_BOUNDARY.sub("<boundary>", line) for line in lines]


def _profiles(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send a schema's object as its members' parts: text for scalars, JSON for the rest, repeated for arrays."""
    _, _, types = _modules(package)
    codec = types.SubmitProfileRequestCodecs.body()
    for label, value in (
        ("profile", _PROFILE),
        ("profile with its name only", {"name": "Bo"}),
        ("profile with no tags", {"name": "Bo", "tags": []}),
        ("profile without a name", {"age": 3}),
    ):
        if label in {"profile", "profile with its name only"}:
            exchange.respond(raw_response(204))
        record(lines, label, lambda value=value: api.forms.submit_profile(body=codec.from_wire(value)) if "name" in value else api.forms.submit_profile(body=value))
    anything = types.SubmitAnythingRequestCodecs.body()
    exchange.respond(raw_response(204))
    record(lines, "free-form object", lambda: api.forms.submit_anything(body=anything.from_wire({"k": [1]})))
    record(lines, "free-form value that is no object", lambda: api.forms.submit_anything(body=anything.from_wire("text")))


def _parts(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send parts one by one: fields, bytes, files, streams, and factories, refusing any a form cannot carry."""
    bodies, options, _ = _modules(package)
    body, field, file = bodies.MultipartBody, bodies.FieldPart, bodies.FilePart
    unset = options.UNSET
    streamed = body((
        field("title", "Notes"),
        field("skipped", unset),
        field("summary", "# Hi", content_type="text/markdown", headers=(("X-Trace", "t"),)),
        file("image", b"\x00\x01", filename='a"b\r\n.png', content_type="image/png"),
        file("doc", bodies.FileBody(io.BytesIO(b"doc")), filename="doc.txt"),
        file("stream", bodies.StreamBody([b"s1", b"s2"])),
    ))
    sized = body((field("a", "1"), file("f", b"x")))
    for label, value in (("streamed parts", streamed), ("sized parts", sized), ("no parts", body(()))):
        exchange.respond(raw_response(204))
        record(lines, label, lambda value=value: api.forms.submit_parts(body=value))
    record(lines, "no body", lambda: _answered(exchange, lambda: api.forms.submit_parts()))
    for label, value in (
        ("part header on two lines", body((field("a", "1", headers=(("X-Trace", "a\r\nb"),)),))),
        ("part header naming the media type", body((field("a", "1", headers=(("Content-Type", "text/plain"),)),))),
        ("part media type that is none", body((field("a", "1", content_type="not a media type"),))),
        ("file part header that is no token", body((file("f", b"x", headers=(("Bad Header", "v"),)),))),
        ("part that is no part", body(("text",))),
        ("field of an object", body((field("a", object()),))),
        ("file of the other mode", body((file("f", bodies.AsyncFileBody(io.BytesIO(b"x"))),))),
        ("body of the other mode", bodies.AsyncMultipartBody((field("a", "1"),))),
    ):
        record(lines, label, lambda value=value: api.forms.submit_parts(body=value))
    spent = bodies.StreamBody([b"once"])
    spent(bodies.BodyAttemptContext(call_id="spent", attempt_index=0, hop_index=0, remaining_timeout=None))
    begun = body((file("first", bodies.BodyFactory(attempt_factory(lines, b"x"))), file("second", spent)))
    record(lines, "file part refused after another began", lambda: api.forms.submit_parts(body=begun))
    failing = bodies.BodyFactory(attempt_factory(lines, b"x", close_error=True))
    closing = body((file("first", failing), file("second", bodies.BodyFactory(attempt_factory(lines, b"y", close_error=True)))))
    exchange.respond(raw_response(204))
    lines.append(f"  file parts failing to close: {outcome(lambda: api.forms.submit_parts(body=closing))}")
    exchange.respond(raw_response(200, b"ok", "text/plain"))
    raw = body((field("meta", {"k": [1, "x"]}), field("count", 5), file("f", b"raw")))
    record(lines, "raw parts", lambda: api.request_raw("POST", "https://forms.example.com/raw", body=raw).body_bytes)


def _answered(exchange: Exchange, call: Callable[[], object]) -> object:
    exchange.respond(raw_response(204))
    return call()


async def _async_multipart(package: ModuleType, lines: list[str]) -> None:
    bodies, _, types = _modules(package)
    exchange = Exchange(lines)
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(exchange.ahandle))
    body, field, file = bodies.AsyncMultipartBody, bodies.FieldPart, bodies.FilePart

    async def chunks() -> Any:
        for chunk in (b"s1", b"s2"):
            yield chunk

    async with package.AsyncClient(http_client=http) as api:
        exchange.respond(raw_response(204))
        await arecord(lines, "async profile", lambda: api.forms.submit_profile(body=types.SubmitProfileRequestCodecs.body().from_wire(_PROFILE)))
        file_body = bodies.AsyncFileBody(io.BytesIO(b"doc"))
        parts = body((
            field("title", "Notes"),
            file("doc", file_body, filename="doc.txt"),
            file("stream", bodies.AsyncStreamBody(chunks())),
            file("image", b"\x00", content_type="image/png"),
        ))
        exchange.respond(raw_response(204))
        await arecord(lines, "async parts", lambda: api.forms.submit_parts(body=parts))
        file_body.close()
        for label, value in (
            ("async body of the other mode", bodies.MultipartBody((field("a", "1"),))),
            ("async file of the other mode", body((file("f", bodies.FileBody(io.BytesIO(b"x"))),))),
        ):
            await arecord(lines, label, lambda value=value: api.forms.submit_parts(body=value))
        spent = bodies.AsyncStreamBody(Chunks(lines, (b"once",)))
        await spent(bodies.BodyAttemptContext(call_id="spent", attempt_index=0, hop_index=0, remaining_timeout=None))
        begun = body((file("first", bodies.AsyncBodyFactory(async_attempt_factory(lines, b"x"))), file("second", spent)))
        await arecord(lines, "async file part refused after another began", lambda: api.forms.submit_parts(body=begun))
        closing = body((
            file("first", bodies.AsyncBodyFactory(async_attempt_factory(lines, b"x", close_error=True))),
            file("second", bodies.AsyncBodyFactory(async_attempt_factory(lines, b"y", close_error=True))),
        ))
        exchange.respond(raw_response(204))
        lines.append(f"  async file parts failing to close: {await aoutcome(lambda: api.forms.submit_parts(body=closing))}")
        exchange.respond(raw_response(200, b"ok", "text/plain"))
        raw = body((field("meta", {"k": 1}), file("f", b"raw")))

        async def raw_call() -> bytes:
            return (await api.request_raw("POST", "https://forms.example.com/raw", body=raw)).body_bytes

        await arecord(lines, "async raw parts", raw_call)
    await http.aclose()
