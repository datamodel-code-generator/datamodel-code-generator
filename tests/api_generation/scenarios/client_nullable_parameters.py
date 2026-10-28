"""Send None for nullable parameters and bodies.

An optional style parameter is left out, and a required one is refused. An inline object schema that admits null sends
and decodes None as its body and its result, its properties declared directly or through allOf, and still checks an
object against its schema. A form or multipart body has no null to send, so such a schema's declared properties still
travel as its fields and parts. None for an optional body whose schema rejects null is left out, as UNSET is, on
every backend.
"""

from __future__ import annotations

import importlib
import re
from typing import TYPE_CHECKING, Any, Final

from tests.api_generation.support.client_runtime import (
    Exchange,
    arecord,
    form_part,
    json_response,
    raw_response,
    record,
    request_body,
    run,
)

if TYPE_CHECKING:
    from types import ModuleType

_BOUNDARY: Final = re.compile(r"\b[0-9a-f]{32}\b")
_JSON: Final = "application/json"
_NOTE: Final = "application/vnd.note+json"


def nullable_parameters(package: ModuleType, lines: list[str]) -> None:
    """Leave out None of optional query, header, and cookie parameters, send JSON null as content, refuse the rest."""
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        exchange.respond(*(raw_response(204) for _ in range(4)))
        record(
            lines,
            "omit null optional parameters",
            lambda: api.default.get_items(cap=None, tags=None, X_Trace=None, session=None),
        )
        record(
            lines,
            "send optional values",
            lambda: api.default.get_items(cap=0, tags=[1, 2], X_Trace="t", session="s"),
        )
        record(lines, "send null content", lambda: api.default.get_items(blob=None))
        record(lines, "omit an empty array", lambda: api.default.get_items(tags=[]))
        record(lines, "refuse a null array item", lambda: api.default.get_items(levels=[1, None]))
        record(lines, "refuse a null required query", lambda: api.default.get_needed(key="k", need=None))
        record(lines, "refuse a null path", lambda: api.default.get_needed(key=None, need=1))
    run(lambda: _async_nullable_parameters(package, lines))


async def _async_nullable_parameters(package: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    http = exchange.async_client()
    api: Any
    async with package.AsyncClient(http_client=http) as api:
        exchange.respond(raw_response(204))
        await arecord(
            lines,
            "async omit null optional parameters",
            lambda: api.default.get_items(cap=None, tags=None, X_Trace=None, session=None),
        )
        await arecord(lines, "async refuse a null required query", lambda: api.default.get_needed(key="k", need=None))
    await http.aclose()


def nullable_bodies(package: ModuleType, lines: list[str]) -> None:
    """Send and decode null as an inline object schema's body and result, then send and check objects."""
    exchange = Exchange(lines)
    note = request_body(package, "postNote", None, {"text": "a"})
    with exchange.client() as http, package.Client(http_client=http) as api:
        exchange.respond(json_response(200, None), json_response(200, {"text": "b"}), json_response(200, {}))
        record(lines, "send and decode null", lambda: api.default.post_note(body=None))
        record(lines, "send and decode an object", lambda: api.default.post_note(body=note))
        record(lines, "decode an object without its required member", lambda: api.default.post_note(body=None))
        extended = request_body(package, "postExtended", None, {"text": "a"})
        exchange.respond(
            json_response(200, None), json_response(200, {"text": "b", "tag": "t"}), json_response(200, {})
        )
        record(lines, "send and decode null through allOf", lambda: api.default.post_extended(body=None))
        record(lines, "send and decode an object through allOf", lambda: api.default.post_extended(body=extended))
        record(
            lines, "decode an allOf object without its required member", lambda: api.default.post_extended(body=None)
        )
        _forms(package, api, exchange, lines)
        _optional(api, exchange, lines)
    run(lambda: _async_nullable_bodies(package, lines))
    lines[:] = [_BOUNDARY.sub("<boundary>", line) for line in lines]


def _forms(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send the declared properties of form and multipart schemas that admit null as fields and parts."""
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    form = request_body(package, "postForm", None, {"name": "n", "tags": ["a", "b"], "count": 2})
    upload = bodies.MultipartBody((
        bodies.FilePart("file", b"data", filename="a.txt", content_type="text/plain"),
        bodies.FieldPart("note", "n"),
        bodies.FieldPart("meta", form_part(package, "upload", "meta", {"size": 3})),
    ))
    exchange.respond(raw_response(204), raw_response(204))
    record(lines, "send a form's declared fields", lambda: api.default.post_form(body=form))
    record(lines, "send declared parts", lambda: api.default.upload(body=upload))


def _optional(api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Leave out None of an optional body whose schema rejects null, as UNSET is, and send null where it takes null."""
    for label, call in (
        ("omit a null optional body", lambda: api.default.post_draft(body=None)),
        (
            "refuse a media type beside an omitted null body",
            lambda: api.default.post_draft(body=None, media_type=_JSON),
        ),
        ("omit a null optional body of several media", lambda: api.default.post_version(body=None)),
        ("refuse null without the media type of a media that takes it", lambda: api.default.post_variant(body=None)),
        ("refuse a named media that rejects null", lambda: api.default.post_variant(body=None, media_type=_JSON)),
        ("send null in a named media that takes null", lambda: api.default.post_variant(body=None, media_type=_NOTE)),
        ("omit a null optional text body", lambda: api.default.post_text(body=None)),
        ("send null as a body without a schema", lambda: api.default.post_value(body=None)),
    ):
        exchange.respond(raw_response(204))
        record(lines, label, call)
        exchange.responders.clear()


async def _async_nullable_bodies(package: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    http = exchange.async_client()
    api: Any
    async with package.AsyncClient(http_client=http) as api:
        exchange.respond(json_response(200, None), raw_response(204))
        await arecord(lines, "async send and decode null", lambda: api.default.post_note(body=None))
        await arecord(lines, "async omit a null optional body", lambda: api.default.post_draft(body=None))
    await http.aclose()
