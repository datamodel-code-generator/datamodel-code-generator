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
        _uploads(package, api, exchange, lines)
        _covers(package, api, exchange, lines)
        _styles(package, api, exchange, lines)
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
        (
            "profile read of Latin-1 text",
            _form((_NAMED % b"name" + b"\r\nContent-Type: text/plain; charset=iso-8859-1", "café".encode("latin-1"))),
            form,
        ),
        (
            "profile read of UTF-16 text",
            _form((_NAMED % b"name" + b"\r\nContent-Type: text/plain; charset=utf-16", "café".encode("utf-16"))),
            form,
        ),
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
    _uploads_read(api, exchange, lines)


_JSON: Final = b"\r\nContent-Type: application/json"
_UPLOAD_PARTS: Final = (
    (_NAMED % b"title", b"Notes"),
    (_NAMED % b"count", b"3"),
    (_NAMED % b"tags", b"a"),
    (_NAMED % b"tags", b"b"),
    (_NAMED % b"meta" + _JSON, b'{"city":"Oslo","codes":[7]}'),
    (_NAMED % b"draft" + _JSON, b'{"id":1,"title":"t"}'),
    (_NAMED % b"photo" + b'; filename="a.png"\r\nContent-Type: image/png', b"\x89PNG"),
    (_NAMED % b"pages", b"1"),
    (_NAMED % b"pages", b"2"),
    (_NAMED % b"bonus", b"7"),
)


def _uploads_read(api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Read form-data responses with file parts: files as bytes, other parts through their members' codecs."""
    form = "multipart/form-data; boundary=b1"
    title, photo = _UPLOAD_PARTS[0], _UPLOAD_PARTS[6]
    for label, status, parts in (
        ("upload read", 200, _UPLOAD_PARTS),
        ("upload read of its required parts", 200, (photo, title)),
        ("upload read of scans", 201, ((_NAMED % b"note", b"n"), (_NAMED % b"scan-1", b"1"), (_NAMED % b"scan-2", b"2"))),
        ("upload read of a photo and parts of any name", 202, (photo, (_NAMED % b"x", b"1"), (_NAMED % b"x", b"{}"))),
        ("upload read of a photo alone", 203, (photo,)),
    ):
        exchange.respond(raw_response(status, _form(*parts), form))
        lines.append(f"  {label} {_parts_of(api.forms.read_upload())}")
    for label, status, parts in (
        ("upload read without its photo", 200, (title,)),
        ("upload read of a title twice", 200, (title, title, photo)),
        ("upload read of a count that is no integer", 200, (title, photo, (_NAMED % b"count", b"x"))),
        ("upload read of an extra that is no integer", 200, (title, photo, (_NAMED % b"bonus", b"x"))),
        ("upload read of broken JSON", 200, (title, photo, (_NAMED % b"meta" + _JSON, b"{"))),
        ("upload read of an address with other codes", 200, (title, photo, (_NAMED % b"meta" + _JSON, b'{"codes":["a"]}'))),
        ("upload read of a part without a name", 200, (title, photo, (b"Content-Type: text/plain", b"x"))),
        ("upload read of a photo and another part", 203, (photo, (_NAMED % b"x", b"1"))),
    ):
        exchange.respond(raw_response(status, _form(*parts), form))
        record(lines, label, api.forms.read_upload)


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
        ("body that is no multipart body", b"a=1"),
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


def _uploads(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send bodies with file parts: each member's values through its part codec, refusing parts the schema forbids."""
    bodies, options, types = _modules(package)
    body, field, file = bodies.MultipartBody, bodies.FieldPart, bodies.FilePart
    codecs = types.SubmitUploadRequestCodecs
    meta = codecs.part(name="meta").from_wire({"city": "Oslo", "codes": [7]})
    title, photo = field("title", "Notes"), file("photo", b"\x89PNG", filename="a.png", content_type="image/png")
    for label, parts in (
        (
            "upload",
            (
                title,
                field("count", options.UNSET),
                field("tags", ["a", "b"]),
                field("meta", meta),
                photo,
                file("pages", b"1"),
                file("pages", bodies.StreamBody([b"2"])),
                field("bonus", 7),
            ),
        ),
        ("upload of its required parts", (photo, title)),
    ):
        exchange.respond(raw_response(204))
        record(lines, label, lambda parts=parts: api.forms.submit_upload(body=body(parts)))
    for label, parts in (
        ("upload without its title", (photo,)),
        ("upload of an unset title", (field("title", options.UNSET), photo)),
        ("upload without its photo", (title,)),
        ("upload of a photo as a field", (title, field("photo", "x"))),
        ("upload of a title as a file", (file("title", b"x"), photo)),
        ("upload of a title twice", (title, title, photo)),
        ("upload of a photo twice", (title, photo, photo)),
        ("upload of a count that is no integer", (title, photo, field("count", "x"))),
        ("upload of an extra that is no integer", (title, photo, field("bonus", "x"))),
        ("upload of no tags", (title, photo, field("tags", []))),
        ("upload of a part without a name", (title, photo, field(1, "x"))),
        ("upload of a read-only id", (title, photo, field("id", 1))),
    ):
        record(lines, label, lambda parts=parts: api.forms.submit_upload(body=body(parts)))
    exchange.respond(raw_response(204))
    avatar = (field("caption", "me"), file("avatar", b"a"), field("style", {"k": [1]}))
    record(lines, "avatar", lambda: api.forms.submit_avatar(body=body(avatar), media_type="multipart/form-data"))
    exchange.respond(raw_response(204))
    scans = (field("note", "n"), file("scan-1", b"1"), file("scan-2", b"2"))
    record(lines, "scans", lambda: api.forms.submit_scans(body=body(scans)))
    record(lines, "scans of an extra field", lambda: api.forms.submit_scans(body=body((field("x", "1"),))))
    exchange.respond(raw_response(204))
    color = types.SubmitLabelsRequestCodecs.part(name="color").from_wire("red")
    record(lines, "labels", lambda: api.forms.submit_labels(body=body((file("sheet", b"s"), field("color", color)))))
    exchange.respond(raw_response(204))
    record(lines, "photos", lambda: api.forms.submit_photos(body=body((photo,))))
    record(lines, "photos of an extra part", lambda: api.forms.submit_photos(body=body((photo, file("x", b"")))))
    for label, select in (
        ("title codec", lambda: codecs.part(name="title").from_wire("t")),
        ("extra codec", lambda: codecs.part(name="bonus", media_type="multipart/form-data").from_wire(3)),
        ("photo codec", lambda: codecs.part(name="photo")),
        ("title codec of another media type", lambda: codecs.part(name="title", media_type="application/json")),
        ("caption codec without a media type", lambda: types.SubmitAvatarRequestCodecs.part(name="caption")),
        (
            "caption codec",
            lambda: types.SubmitAvatarRequestCodecs.part(name="caption", media_type="multipart/form-data").from_wire(
                "c"
            ),
        ),
        (
            "untyped extra codec",
            lambda: types.SubmitAvatarRequestCodecs.part(name="style", media_type="multipart/form-data"),
        ),
        ("file extra codec", lambda: types.SubmitScansRequestCodecs.part(name="scan-1")),
    ):
        record(lines, label, select)


def _covers(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send members in the media types their encodings name, and refuse a part of a media type outside them."""
    bodies, _, types = _modules(package)
    body, field, file = bodies.MultipartBody, bodies.FieldPart, bodies.FilePart
    meta = types.SubmitCoverRequestCodecs.part(name="meta").from_wire({"city": "Oslo"})
    limit = (("X-Rate-Limit", "5"),)
    cover = file("cover", b"\x89PNG", headers=limit)
    exchange.respond(raw_response(204), raw_response(204))
    record(
        lines,
        "cover",
        lambda: api.forms.submit_cover(
            body=body((
                field("note", "h\xe9llo", headers=(("X-Trace", "t1"),)),
                field("size", 3),
                field("meta", meta, headers=(("X-Meta", "1"),)),
                field("extra", 7),
                cover,
                file("scans", b"g", filename="g.gif", content_type="image/gif"),
            ))
        ),
    )
    record(
        lines,
        "cover of a note in plain text",
        lambda: api.forms.submit_cover(
            body=body((
                field("note", "hi", content_type="Text/Plain"),
                file("cover", b"j", content_type="image/jpeg", headers=limit),
            ))
        ),
    )
    for label, parts in (
        ("cover of a note in JSON", (field("note", "hi", content_type="application/json"), cover)),
        ("cover in text", (file("cover", b"x", content_type="text/plain", headers=limit),)),
        ("cover without its rate limit", (file("cover", b"x"),)),
        ("cover of a rate limit that is no integer", (file("cover", b"x", headers=(("X-Rate-Limit", "x"),)),)),
        ("cover of a note traced out of pattern", (cover, field("note", "hi", headers=(("X-Trace", "u"),)))),
        ("cover of a scan without its media type", (cover, file("scans", b"s"))),
        ("cover of a scan without its filename", (cover, file("scans", b"s", content_type="image/gif"))),
        ("cover of a meta header that is no JSON integer", (cover, field("meta", meta, headers=(("X-Meta", '"1"'),)))),
        ("cover of an object as text", (cover, field("extra", {"k": 1}))),
        ("cover of a note its charset cannot represent", (cover, field("note", "h\xe9llo", content_type="text/plain; charset=us-ascii"))),
    ):
        record(lines, label, lambda parts=parts: api.forms.submit_cover(body=body(parts)))
    exchange.respond(raw_response(204))
    record(lines, "cover of an extra in UTF-16 text", lambda: api.forms.submit_cover(body=body((cover, field("extra", 7, content_type="text/plain; charset=utf-16")))))
    codec = types.SubmitCardRequestCodecs.body()
    card = codec.from_wire({"title": "h\xe9llo", "count": 2, "tags": ["a", "b"]})
    exchange.respond(raw_response(204))
    record(lines, "card", lambda: api.forms.submit_card(body=card))
    record(lines, "card of a tag its charset cannot represent", lambda: api.forms.submit_card(body=codec.from_wire({"tags": ["\xe9"]})))


def _styles(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send members in the parts their query styles give, without percent-encoding, and refuse names two members write."""
    bodies, options, types = _modules(package)
    body, field, file = bodies.MultipartBody, bodies.FieldPart, bodies.FilePart
    stickers = types.SubmitStickersRequestCodecs.body()
    sticker = {
        "tags": ["a&b", "c"],
        "words": ["x", "y"],
        "sizes": [1, 2],
        "filter": {"name": "n m", "min": 2},
        "point": {"x": 1, "z": 3},
        "label": "l/1",
        "y": 4,
    }
    exchange.respond(raw_response(204))
    record(lines, "stickers", lambda: api.forms.submit_stickers(body=stickers.from_wire(sticker)))
    for label, value in (
        ("stickers of a point whose extra is named as another member", {"point": {"y": 1}, "y": 2}),
        ("stickers of another member named as a point's extra", {"y": 2, "point": {"y": 1}}),
        ("stickers of a tag holding its delimiter", {"tags": ["a|b"]}),
    ):
        record(lines, label, lambda value=value: api.forms.submit_stickers(body=stickers.from_wire(value)))
    photo = file("photo", b"p")
    bounds = types.SubmitAlbumRequestCodecs.part(name="bounds")
    exchange.respond(raw_response(204))
    record(
        lines,
        "album",
        lambda: api.forms.submit_album(
            body=body((
                photo,
                field("tags", ["a", "b"]),
                field("bounds", bounds.from_wire({"w": 1, "h": 2})),
                field("title", "t"),
            ))
        ),
    )
    clash = field("bounds", bounds.from_wire({"title": 1}))
    exchange.respond(raw_response(204))
    record(
        lines,
        "album of an omitted title beside a bound's extra of its name",
        lambda: api.forms.submit_album(body=body((photo, field("title", options.UNSET), clash))),
    )
    for label, parts in (
        ("album of bounds whose extra is named as another member", (photo, clash, field("title", "t"))),
        ("album of another member named as a bound's extra", (photo, field("title", "t"), clash)),
        ("album of a bound's extra its disposition pattern refuses", (photo, field("bounds", bounds.from_wire({"b2": 1})))),
        ("album of a tag holding its delimiter", (photo, field("tags", ["a b"]))),
    ):
        record(lines, label, lambda parts=parts: api.forms.submit_album(body=body(parts)))


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
        upload = body((
            field("title", "Notes"),
            file("photo", bodies.AsyncFileBody(io.BytesIO(b"png")), filename="a.png"),
            file("pages", bodies.AsyncStreamBody(chunks())),
        ))
        exchange.respond(raw_response(204))
        await arecord(lines, "async upload", lambda: api.forms.submit_upload(body=upload))
        missing = body((field("title", "Notes"),))
        await arecord(lines, "async upload without its photo", lambda: api.forms.submit_upload(body=missing))
        exchange.respond(raw_response(200, _form(*_PROFILE_PARTS), "multipart/form-data; boundary=b1"))
        await arecord(lines, "async profile read", api.forms.read_profile)
        exchange.respond(raw_response(200, _form(*_UPLOAD_PARTS), "multipart/form-data; boundary=b1"))
        lines.append(f"  async upload read {_parts_of(await api.forms.read_upload())}")
        exchange.respond(raw_response(200, _form((_NAMED % b"x", b"1")), "multipart/mixed; boundary=b1"))
        lines.append(f"  async parts read {_parts_of(await api.forms.read_parts())}")
    await http.aclose()
