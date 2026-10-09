"""Send multipart/form-data bodies from generated clients: an object's members as parts, or parts given one by one."""

from __future__ import annotations

import importlib
import io
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_runtime import (
    Exchange,
    arecord,
    form_part,
    outcome,
    raw_response,
    record,
    request_body,
    run,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


class _AsyncFile:
    """An async file object: its `read` is a coroutine function."""

    async def read(self, size: int = -1) -> bytes:
        return b""


async def _chunks() -> Any:
    for chunk in (b"s1", b"s2"):
        yield chunk


_BOUNDARY: Final = re.compile(r"\b[0-9a-f]{32}\b")
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
    http = exchange.client()
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


def _nested(depth: int) -> bytes:
    """Return a multipart body whose each part opens the next level of nested multipart."""
    return b"".join(
        b"--b%d\r\nContent-Type: multipart/mixed; boundary=b%d\r\n\r\n" % (level + 1, level + 2)
        for level in range(depth)
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
    exchange.respond(raw_response(200, _nested(3000), form))
    lines.append(f"  profile read of deeply nested parts {outcome(api.forms.read_profile)}")
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
        (
            "stored id read of a repeated extra part",
            ((_NAMED % b"id", b"4"), (_NAMED % b"x", b"1"), (_NAMED % b"x", b"2")),
        ),
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
    (_NAMED % b"photo" + b'; filename="a.png"\r\nContent-Type: image/png', b"\x89PNG"),
    (_NAMED % b"pages", b"1"),
    (_NAMED % b"pages", b"2"),
    (_NAMED % b"bonus", b"7"),
)


def _uploads_read(api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Read form-data responses with file parts: files as bytes, other parts through their members' codecs."""
    form = "multipart/form-data; boundary=b1"
    title, photo = _UPLOAD_PARTS[0], _UPLOAD_PARTS[5]
    for label, status, parts in (
        ("upload read", 200, _UPLOAD_PARTS),
        ("upload read of its required parts", 200, (photo, title)),
        (
            "upload read of scans",
            201,
            ((_NAMED % b"note", b"n"), (_NAMED % b"scan-1", b"1"), (_NAMED % b"scan-2", b"2")),
        ),
        ("upload read of a photo and parts of any name", 202, (photo, (_NAMED % b"x", b"1"), (_NAMED % b"x", b"{}"))),
        ("upload read of a photo alone", 203, (photo,)),
    ):
        exchange.respond(raw_response(status, _form(*parts), form))
        lines.append(f"  {label} {_parts_of(api.forms.read_upload())}")
    for label, status, parts in (
        ("upload read without its photo", 200, (title,)),
        ("upload read of a title twice", 200, (title, title, photo)),
        ("upload read of title text that is not UTF-8", 200, ((_NAMED % b"title", b"\xff"), photo)),
        ("upload read of a count that is no integer", 200, (title, photo, (_NAMED % b"count", b"x"))),
        ("upload read of an extra that is no integer", 200, (title, photo, (_NAMED % b"bonus", b"x"))),
        ("upload read of broken JSON", 200, (title, photo, (_NAMED % b"meta" + _JSON, b"{"))),
        (
            "upload read of an address with other codes",
            200,
            (title, photo, (_NAMED % b"meta" + _JSON, b'{"codes":["a"]}')),
        ),
        ("upload read of its write-only secret", 200, (title, photo, (_NAMED % b"secret", b"s"))),
        (
            "upload read of a draft with its write-only secret",
            200,
            (title, photo, (_NAMED % b"draft" + _JSON, b'{"id":1,"title":"t","secret":"s"}')),
        ),
        ("upload read of a part without a name", 200, (title, photo, (b"Content-Type: text/plain", b"x"))),
        ("upload read of a photo and another part", 203, (photo, (_NAMED % b"x", b"1"))),
    ):
        exchange.respond(raw_response(status, _form(*parts), form))
        record(lines, label, api.forms.read_upload)


def _profiles(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send a schema's object as its members' parts: text for scalars, JSON for the rest, repeated for arrays."""
    for label, value in (
        ("profile", _PROFILE),
        ("profile with its name only", {"name": "Bo"}),
        ("profile with no tags", {"name": "Bo", "tags": []}),
    ):
        exchange.respond(raw_response(204))
        record(
            lines,
            label,
            lambda value=value: api.forms.submit_profile(body=request_body(package, "submitProfile", None, value)),
        )
    exchange.respond(raw_response(204))
    record(
        lines,
        "free-form object",
        lambda: api.forms.submit_anything(body=request_body(package, "submitAnything", None, {"k": [1]})),
    )
    record(
        lines,
        "free-form value that is no object",
        lambda: api.forms.submit_anything(body=request_body(package, "submitAnything", None, "text")),
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
        file("doc", io.BytesIO(b"doc"), filename="doc.txt"),
        file("stream", [b"s1", b"s2"]),
    ))
    sized = body((field("a", "1"), file("f", b"x")))
    for label, value in (("streamed parts", streamed), ("sized parts", sized), ("no parts", body(()))):
        exchange.respond(raw_response(204))
        record(lines, label, lambda value=value: api.forms.submit_parts(body=value))
    record(lines, "no body", lambda: _answered(exchange, lambda: api.forms.submit_parts()))
    for label, value in (
        ("part header naming the media type", body((field("a", "1", headers=(("Content-Type", "text/plain"),)),))),
        ("part media type as given", body((field("a", "1", content_type="not a media type"),))),
        ("body of the other mode", bodies.AsyncMultipartBody((field("a", "1"),))),
        ("file of an empty chunk", body((file("f", iter([b"aa", b"", b"bb"])),))),
        ("part headers of one name", body((field("a", "1", headers=(("X-T", "1"), ("x-t", "2"))),))),
        (
            "file part header naming a media type in its name",
            body((file("f", b"x", content_type="image/png", headers=(("X-Content-Type-Options", "nosniff"),)),)),
        ),
    ):
        record(lines, label, lambda value=value: _answered(exchange, lambda: api.forms.submit_parts(body=value)))
    for label, value in (
        ("part header on two lines", body((field("a", "1", headers=(("X-Trace", "a\r\nb"),)),))),
        ("part header naming the disposition", body((field("a", "1", headers=(("content-disposition", "inline"),)),))),
        ("file part header that is no token", body((file("f", b"x", headers=(("Bad Header", "v"),)),))),
        ("part that is no part", body(("text",))),
        ("field of an object", body((field("a", object()),))),
        ("file with invalid native input", body((file("f", object()),))),
        ("file of an async file", body((file("f", _AsyncFile()),))),
        ("file of an async iterable", body((file("f", _chunks()),))),
        ("body that is no multipart body", b"a=1"),
    ):
        record(lines, label, lambda value=value: api.forms.submit_parts(body=value))
    exchange.respond(raw_response(200, b"ok", "text/plain"))
    raw = body((field("meta", {"k": [1, "x"]}), field("count", 5), file("f", b"raw")))
    record(lines, "raw parts", lambda: api.request_raw("POST", "https://forms.example.com/raw", body=raw).body_bytes)


def _uploads(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send bodies with file parts, avatars, scans, labels, and photos, refusing parts the schema forbids."""
    bodies, options, _ = _modules(package)
    body, field, file = bodies.MultipartBody, bodies.FieldPart, bodies.FilePart
    meta = form_part(package, "submitUpload", "meta", {"city": "Oslo", "codes": [7]})
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
                file("pages", [b"2"]),
                field("bonus", 7),
            ),
        ),
        ("upload of its required parts", (photo, title)),
        ("upload of no tags", (title, photo, field("tags", []))),
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
        ("upload of a read-only id", (title, photo, field("id", 1))),
    ):
        record(lines, label, lambda parts=parts: _answered(exchange, lambda: api.forms.submit_upload(body=body(parts))))
    record(
        lines,
        "upload of a part without a name",
        lambda: api.forms.submit_upload(body=body((title, photo, field(1, "x")))),
    )
    exchange.respond(raw_response(204))
    avatar = (field("caption", "me"), file("avatar", b"a"), field("style", {"k": [1]}))
    record(lines, "avatar", lambda: api.forms.submit_avatar(body=body(avatar), media_type="multipart/form-data"))
    exchange.respond(raw_response(204))
    scans = (field("note", "n"), file("scan-1", b"1"), file("scan-2", b"2"))
    record(lines, "scans", lambda: api.forms.submit_scans(body=body(scans)))
    record(
        lines,
        "scans of an extra field",
        lambda: _answered(exchange, lambda: api.forms.submit_scans(body=body((field("x", "1"),)))),
    )
    exchange.respond(raw_response(204))
    color = form_part(package, "submitLabels", "color", "red")
    record(lines, "labels", lambda: api.forms.submit_labels(body=body((file("sheet", b"s"), field("color", color)))))
    exchange.respond(raw_response(204))
    record(lines, "photos", lambda: api.forms.submit_photos(body=body((photo,))))
    record(
        lines,
        "photos of an extra part",
        lambda: _answered(exchange, lambda: api.forms.submit_photos(body=body((photo, file("x", b""))))),
    )


def _covers(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send members in the media types their encodings name, and refuse a part of a media type outside them."""
    bodies, _, _ = _modules(package)
    body, field, file = bodies.MultipartBody, bodies.FieldPart, bodies.FilePart
    meta = form_part(package, "submitCover", "meta", {"city": "Oslo"})
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
        ("cover of a scan without its media type", (cover, file("scans", b"s"))),
        ("cover of an object as text", (cover, field("extra", {"k": 1}))),
        (
            "cover of a note its charset cannot represent",
            (cover, field("note", "h\xe9llo", content_type="text/plain; charset=us-ascii")),
        ),
    ):
        record(lines, label, lambda parts=parts: api.forms.submit_cover(body=body(parts)))
    exchange.respond(raw_response(204))
    record(
        lines,
        "cover of an extra in UTF-16 text",
        lambda: api.forms.submit_cover(
            body=body((cover, field("extra", 7, content_type="text/plain; charset=utf-16")))
        ),
    )
    card = request_body(package, "submitCard", None, {"title": "h\xe9llo", "count": 2, "tags": ["a", "b"]})
    exchange.respond(raw_response(204))
    record(lines, "card", lambda: api.forms.submit_card(body=card))
    record(
        lines,
        "card of a tag its charset cannot represent",
        lambda: api.forms.submit_card(body=request_body(package, "submitCard", None, {"tags": ["\xe9"]})),
    )


def _styles(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send members in the parts their query styles give, without percent-encoding, and refuse names two members write."""
    bodies, options, _ = _modules(package)
    body, field, file = bodies.MultipartBody, bodies.FieldPart, bodies.FilePart
    sticker = {
        "tags": ["a&b", "c"],
        "words": ["x", "y"],
        "sizes": [1, 2],
        "filter": {"name": "n m", "min": 2},
        "point": {"x": 1, "z": 3},
        "label": "l/1",
        "y": 4,
    }
    exchange.respond(raw_response(204), raw_response(204))
    for label, value in (("stickers", sticker), ("stickers of no tags and no filter", {"tags": [], "filter": {}})):
        record(
            lines,
            label,
            lambda value=value: api.forms.submit_stickers(body=request_body(package, "submitStickers", None, value)),
        )
    for label, value in (
        ("stickers of a point whose extra is named as another member", {"point": {"y": 1}, "y": 2}),
        ("stickers of another member named as a point's extra", {"y": 2, "point": {"y": 1}}),
        ("stickers of a point's extra named as a later styled member", {"point": {"label": 3}, "label": "l"}),
        ("stickers of a tag holding its delimiter", {"tags": ["a|b"]}),
    ):
        record(
            lines,
            label,
            lambda value=value: api.forms.submit_stickers(body=request_body(package, "submitStickers", None, value)),
        )
    photo = file("photo", b"p")
    exchange.respond(raw_response(204), raw_response(204))
    record(
        lines,
        "album of UTF-16 tags",
        lambda: api.forms.submit_album(
            body=body((photo, field("tags", ["x", "y"], content_type="text/plain; charset=utf-16")))
        ),
    )
    record(
        lines,
        "album",
        lambda: api.forms.submit_album(
            body=body((
                photo,
                field("tags", ["a", "b"]),
                field("bounds", form_part(package, "submitAlbum", "bounds", {"w": 1, "h": 2})),
                field("title", "t"),
            ))
        ),
    )
    clash = field("bounds", form_part(package, "submitAlbum", "bounds", {"title": 1}))
    exchange.respond(raw_response(204))
    record(
        lines,
        "album of an omitted title beside a bound's extra of its name",
        lambda: api.forms.submit_album(body=body((photo, field("title", options.UNSET), clash))),
    )
    for label, parts in (
        ("album of bounds whose extra is named as another member", (photo, clash, field("title", "t"))),
        ("album of another member named as a bound's extra", (photo, field("title", "t"), clash)),
    ):
        record(lines, label, lambda parts=parts: _answered(exchange, lambda: api.forms.submit_album(body=body(parts))))
    for label, parts in (
        ("album of a tag holding its delimiter", (photo, field("tags", ["a b"]))),
        (
            "album of tags their named charset cannot represent",
            (photo, field("tags", ["\xe9"], content_type="text/plain; charset=us-ascii")),
        ),
    ):
        record(lines, label, lambda parts=parts: api.forms.submit_album(body=body(parts)))


def _answered(exchange: Exchange, call: Callable[[], object]) -> object:
    exchange.respond(raw_response(204))
    return call()


async def _async_multipart(package: ModuleType, lines: list[str]) -> None:
    bodies, _, types = _modules(package)
    exchange = Exchange(lines)
    http = exchange.async_client()
    body, field, file = bodies.AsyncMultipartBody, bodies.FieldPart, bodies.FilePart

    async with package.AsyncClient(http_client=http) as api:
        exchange.respond(raw_response(204))
        await arecord(
            lines,
            "async profile",
            lambda: api.forms.submit_profile(body=request_body(package, "submitProfile", None, _PROFILE)),
        )
        file_body = io.BytesIO(b"doc")
        parts = body((
            field("title", "Notes"),
            file("doc", file_body, filename="doc.txt"),
            file("stream", [b"s1", b"s2"]),
            file("image", b"\x00", content_type="image/png"),
        ))
        exchange.respond(raw_response(204))
        await arecord(lines, "async parts", lambda: api.forms.submit_parts(body=parts))
        file_body.close()
        exchange.respond(raw_response(204))
        await arecord(
            lines,
            "async body of the other mode",
            lambda: api.forms.submit_parts(body=bodies.MultipartBody((field("a", "1"),))),
        )
        await arecord(
            lines,
            "async file with invalid native input",
            lambda: api.forms.submit_parts(body=body((file("f", object()),))),
        )
        for label, content in (("async file", _AsyncFile()), ("async iterable", _chunks())):
            await arecord(
                lines,
                f"async file part of an {label}",
                lambda content=content: api.forms.submit_parts(body=body((file("f", content),))),
            )
        exchange.respond(raw_response(200, b"ok", "text/plain"))
        raw = body((field("meta", {"k": 1}), file("f", b"raw")))

        async def raw_call() -> bytes:
            return (await api.request_raw("POST", "https://forms.example.com/raw", body=raw)).body_bytes

        await arecord(lines, "async raw parts", raw_call)
        upload = body((
            field("title", "Notes"),
            file("photo", io.BytesIO(b"png"), filename="a.png"),
            file("pages", [b"p1", b"p2"]),
        ))
        exchange.respond(raw_response(204))
        await arecord(lines, "async upload", lambda: api.forms.submit_upload(body=upload))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "photo.png"
            path.write_bytes(b"png" * 3)
            exchange.respond(raw_response(204))
            await arecord(
                lines,
                "async upload of a path",
                lambda: api.forms.submit_upload(body=body((field("title", "Notes"), file("photo", path)))),
            )
            path.unlink()
        missing = body((field("title", "Notes"),))
        exchange.respond(raw_response(204))
        await arecord(lines, "async upload without its photo", lambda: api.forms.submit_upload(body=missing))
        exchange.respond(raw_response(200, _form(*_PROFILE_PARTS), "multipart/form-data; boundary=b1"))
        await arecord(lines, "async profile read", api.forms.read_profile)
        exchange.respond(raw_response(200, _form(*_UPLOAD_PARTS), "multipart/form-data; boundary=b1"))
        lines.append(f"  async upload read {_parts_of(await api.forms.read_upload())}")
        exchange.respond(raw_response(200, _form((_NAMED % b"x", b"1")), "multipart/mixed; boundary=b1"))
        lines.append(f"  async parts read {_parts_of(await api.forms.read_parts())}")
    await http.aclose()


_SPLIT_PARTS: Final = (
    (_NAMED % b"photo" + b'; filename="a.png"\r\nContent-Type: image/png', b"\x89PNG"),
    (_NAMED % b"note" + _JSON, b'{"text":"t","id":1}'),
    (_NAMED % b"spare" + _JSON, b'{"text":"s","id":2}'),
)


def split_parts(package: ModuleType, lines: list[str]) -> None:
    """Send and read form-data parts whose schema splits into request and response models.

    A member or extra part sent goes through the request model, and one read through the response model.
    """
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        parts = [bodies.FilePart("photo", b"\x89PNG")]
        for name in ("note", "spare"):
            value = record(
                lines,
                f"split {name} part",
                lambda name=name: form_part(package, "sendUpload", name, {"text": name[0], "secret": "s"}),
            )
            parts.append(bodies.FieldPart(name, value))
        exchange.respond(raw_response(204))
        record(lines, "split parts sent", lambda: api.default.send_upload(body=bodies.MultipartBody(tuple(parts))))
        exchange.respond(raw_response(200, _form(*_SPLIT_PARTS), "multipart/form-data; boundary=b1"))
        record(lines, "split parts read", lambda: [(part.name, part.value) for part in api.default.read_upload().parts])
    lines[:] = [_BOUNDARY.sub("<boundary>", line) for line in lines]
