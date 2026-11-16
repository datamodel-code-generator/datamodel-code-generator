"""Send and read objects whose classes take other members, or none, by the configuration they apply."""

from __future__ import annotations

import importlib
import re
from typing import TYPE_CHECKING, Final

from tests.api_generation.support.client_runtime import (
    Exchange,
    argument,
    form_part,
    raw_response,
    record,
    request_body,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_BOUNDARY: Final = re.compile(r"\b[0-9a-f]{32}\b")
_DOWNLOAD: Final = (
    b'--b1\r\nContent-Disposition: form-data; name="photo"; filename="a.png"\r\nContent-Type: image/png\r\n\r\n\x89PNG'
    b'\r\n--b1\r\nContent-Disposition: form-data; name="x"\r\n\r\n1\r\n--b1--\r\n'
)


def _built(lines: list[str], label: str, build: Callable[[object], object], wires: tuple[object, ...]) -> object:
    """Return the value the first wire form builds that its model takes (the last always), recording each attempt."""
    *tried, last = wires
    for wire in tried:
        if (value := record(lines, f"{label} {wire!r}", lambda wire=wire: build(wire))) is not None:
            return value
    return record(lines, f"{label} {last!r}", lambda: build(last))


def held_extras(package: ModuleType, lines: list[str]) -> None:
    """Send query and cookie objects and form bodies with a member their schema leaves out, and read form-data parts.

    Each value is built from its wire form with that member, or without it where its model refuses it. A response's
    parts are read as the schema gives them, whatever the model of their object takes.
    """
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        for name in ("plain", "closed", "open"):
            obj = _built(
                lines,
                f"{name} object",
                lambda wire, name=name: argument(package, f"get{name.title()}", "query", "obj", wire),
                ({"size": 2, "foo": "bar"}, {"size": 2}),
            )
            exchange.respond(raw_response(204))
            record(lines, f"{name} query", lambda name=name, obj=obj: getattr(api.default, f"get_{name}")(obj=obj))
        prefs = argument(package, "getCookie", "cookie", "prefs", {"size": 3})
        a, b = (
            argument(package, "getTwo", "query", "a", {"size": 1}),
            argument(package, "getTwo", "query", "b", {"page": 2}),
        )
        exchange.respond(raw_response(204), raw_response(204))
        record(lines, "cookie", lambda: api.default.get_cookie(session="s", prefs=prefs))
        record(lines, "two objects", lambda: api.default.get_two(a=a, b=b))
        for operation, method, wires in (
            ("postPlainParts", "post_plain_parts", ({"size": 2, "foo": 1}, {"size": 2})),
            ("postClosedParts", "post_closed_parts", ({"size": 2, "foo": 1}, {"size": 2})),
            ("postOpenParts", "post_open_parts", ({"size": 2, "foo": 1}, {"size": 2})),
            ("postTyped", "post_typed", ({"size": 1, "x": 5}, {"size": 1})),
            ("postTypedParts", "post_typed_parts", ({"size": 1, "x": 5}, {"size": 1})),
            ("postLocatedParts", "post_located_parts", ({"size": 1, "x": {"lat": 5}}, {"size": 1})),
        ):
            body = _built(
                lines, method, lambda wire, operation=operation: request_body(package, operation, None, wire), wires
            )
            exchange.respond(raw_response(204))
            record(lines, method, lambda method=method, body=body: getattr(api.default, method)(body=body))
        note = form_part(package, "sendUpload", "note", "kept")
        upload = bodies.MultipartBody((bodies.FilePart("photo", b"\x89PNG"), bodies.FieldPart("note", note)))
        exchange.respond(raw_response(204))
        record(lines, "upload", lambda: api.default.send_upload(body=upload))
        for method in ("read_download", "read_typed_download"):
            exchange.respond(raw_response(200, _DOWNLOAD, "multipart/form-data; boundary=b1"))
            data = record(lines, method, getattr(api.default, method))
            lines.append(f"  {method} parts {[(part.name, part.value) for part in getattr(data, 'parts', ())]!r}")
    lines[:] = [_BOUNDARY.sub("<boundary>", line) for line in lines]
