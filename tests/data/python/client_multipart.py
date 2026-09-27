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
    """Send form-data bodies and read multipart responses, synchronously and with asyncio, refusing broken ones."""
    exchange = Exchange(lines)
    http = httpx2.Client(transport=httpx2.MockTransport(exchange.handle))
    with package.Client(http_client=http) as api:
        _profiles(package, api, exchange, lines)
        _parts(package, api, exchange, lines)
        _responses(api, exchange, lines)
    http.close()
    run(lambda: _async_multipart(package, lines))
    lines[:] = [_BOUNDARY.sub("<boundary>", line) for line in lines]


def _form(*parts: tuple[str, bytes], boundary: str = "b1") -> bytes:
    """Return a multipart body of raw part heads and contents."""
    return b"".join(b"--%s\r\n%s\r\n\r\n%s\r\n" % (boundary.encode(), head, content) for head, content in parts) + (
        b"--%s--\r\n" % boundary.encode()
    )


_NAMED: Final = b'Content-Disposition: form-data; name="%s"'
_PROFILE_PARTS: Final = (
    (_NAMED % b"name", b"Ada"),
    (_NAMED % b"age", b"36"),
    (_NAMED % b"score", b"1.5"),
    (_NAMED % b"ratio", b"2"),
    (_NAMED % b"active", b"true"),
    (_NAMED % b"tags", b"a"),
    (_NAMED % b"tags", b"b"),
    (_NAMED % b"address" + b"\r\nContent-Type: application/json", b'{"city":"Oslo","codes":[7]}'),
    (_NAMED % b"bonus", b"7"),
)


def _parts_of(data: Any) -> str:
    """Describe multipart data: each part's name, filename, media type, headers, and value."""
    return f"{data!r} " + "; ".join(
        f"{part!r} {part.name!r} {part.filename!r} {part.content_type!r} {list(part.headers)} {part.value!r}"
        for part in data.parts
    )


def _responses(api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Read form-data parts into a schema's object and any multipart parts as they arrived."""
    form = "multipart/form-data; boundary=b1"
    for label, content, media in (
        ("profile read", _form(*_PROFILE_PARTS), form),
        (
            "profile read with a quoted boundary",
            _form(*_PROFILE_PARTS[:1], boundary="q 1"),
            'multipart/form-data; boundary="q 1"',
        ),
        ("profile read without a boundary", _form(*_PROFILE_PARTS[:1]), "multipart/form-data"),
        ("profile read of another boundary", _form(*_PROFILE_PARTS[:1]), "multipart/form-data; boundary=b2"),
        (
            "profile read with a preamble, padding, and an epilogue",
            b"preamble\r\n" + _form(*_PROFILE_PARTS[:1]).replace(b"--b1\r\n", b"--b1 \t\r\n", 1) + b"epilogue",
            form,
        ),
        ("profile read of a boundary mid-line", b"x--b1\r\n" + _form(*_PROFILE_PARTS[:1]), form),
        ("profile read of a part off its line", b"--b1 x\r\n\r\n--b1--\r\n", form),
        ("profile read that ends at its boundary", b"--b1", form),
        ("profile read of an unterminated part", b"--b1\r\n" + _NAMED % b"name" + b"\r\n\r\nAda", form),
        ("profile read of a header without a colon", _form((b"Broken", b"x")), form),
        ("profile read of a part without a name", _form((b"Content-Type: text/plain", b"x")), form),
        ("profile read of a name twice", _form(*_PROFILE_PARTS[:1], *_PROFILE_PARTS[:1]), form),
        (
            "profile read of a null address twice",
            _form(
                *_PROFILE_PARTS[:1], *(((_NAMED % b"address" + b"\r\nContent-Type: application/json", b"null"),) * 2)
            ),
            form,
        ),
        ("profile read of an extra that is no integer", _form(*_PROFILE_PARTS[:1], (_NAMED % b"bonus", b"x")), form),
        ("profile read of broken JSON", _form(*_PROFILE_PARTS[:1], (_NAMED % b"address", b"{")), form),
        ("profile read of text that is not UTF-8", _form((_NAMED % b"name", b"\xff")), form),
        ("profile read without its name", _form(*_PROFILE_PARTS[1:2]), form),
    ):
        exchange.respond(raw_response(200, content, media))
        record(lines, label, api.forms.read_profile)
    mixed = (
        b'--b1\r\nContent-Disposition: attachment; filename="a\\"b.txt"\r\nContent-Type: text/plain\r\nX-Trace: t\r\n\r\none'
        b"\r\n--b1\r\n\r\n\x00two\r\n\r\n"
        b'\r\n--b1\r\nContent-Disposition: form-data; filename="x; name=y"; name=third; filename=plain.bin\r\n\r\n3'
        b"\r\n--b1\r\nX-Only: 1\r\n"
        b'\r\n--b1\r\nContent-Disposition: attachment; filename="caf\xe9.txt"\r\n\r\n4'
        b"\r\n--b1\r\nX-Bare: 2"
        b"\r\n--b1\r\n"
        b"\r\n--b1--"
    )
    for _ in range(2):
        exchange.respond(raw_response(200, mixed, "multipart/mixed; boundary=b1"))
    data, again = api.forms.read_parts(), api.forms.read_parts()
    lines.append(f"  parts read {_parts_of(data)}")
    first, second = data.parts[:2]
    lines.append(
        f"  parts read again: {data == again} {hash(data) == hash(again)} {first == again.parts[0]} {hash(first) == hash(again.parts[0])} "
        f"{first == second} {data == data.parts} {first == data}"
    )
    exchange.respond(raw_response(200, b"not multipart", "multipart/mixed; boundary=b1"))
    record(lines, "parts read of a body without its boundary", api.forms.read_parts)
    for label, parts in (
        ("stored id read", ((_NAMED % b"id", b"4"),)),
        ("stored id read of an extra part", ((_NAMED % b"id", b"4"), (_NAMED % b"x", b"1"))),
    ):
        exchange.respond(raw_response(202, _form(*parts), form))
        record(lines, label, api.forms.read_parts)


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
        record(
            lines,
            label,
            lambda value=value: (
                api.forms.submit_profile(body=codec.from_wire(value))
                if "name" in value
                else api.forms.submit_profile(body=value)
            ),
        )
    anything = types.SubmitAnythingRequestCodecs.body()
    exchange.respond(raw_response(204))
    record(lines, "free-form object", lambda: api.forms.submit_anything(body=anything.from_wire({"k": [1]})))
    record(
        lines, "free-form value that is no object", lambda: api.forms.submit_anything(body=anything.from_wire("text"))
    )


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
    closing = body((
        file("first", failing),
        file("second", bodies.BodyFactory(attempt_factory(lines, b"y", close_error=True))),
    ))
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
        await arecord(
            lines,
            "async profile",
            lambda: api.forms.submit_profile(body=types.SubmitProfileRequestCodecs.body().from_wire(_PROFILE)),
        )
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
        begun = body((
            file("first", bodies.AsyncBodyFactory(async_attempt_factory(lines, b"x"))),
            file("second", spent),
        ))
        await arecord(lines, "async file part refused after another began", lambda: api.forms.submit_parts(body=begun))
        closing = body((
            file("first", bodies.AsyncBodyFactory(async_attempt_factory(lines, b"x", close_error=True))),
            file("second", bodies.AsyncBodyFactory(async_attempt_factory(lines, b"y", close_error=True))),
        ))
        exchange.respond(raw_response(204))
        lines.append(
            f"  async file parts failing to close: {await aoutcome(lambda: api.forms.submit_parts(body=closing))}"
        )
        exchange.respond(raw_response(200, b"ok", "text/plain"))
        raw = body((field("meta", {"k": 1}), file("f", b"raw")))

        async def raw_call() -> bytes:
            return (await api.request_raw("POST", "https://forms.example.com/raw", body=raw)).body_bytes

        await arecord(lines, "async raw parts", raw_call)
        exchange.respond(raw_response(200, _form(*_PROFILE_PARTS), "multipart/form-data; boundary=b1"))
        await arecord(lines, "async profile read", api.forms.read_profile)
        exchange.respond(raw_response(200, _form((_NAMED % b"x", b"1")), "multipart/mixed; boundary=b1"))
        lines.append(f"  async parts read {_parts_of(await api.forms.read_parts())}")
    await http.aclose()
