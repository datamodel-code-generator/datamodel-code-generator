"""Patch the headers of generated clients' calls: the client's, a view's, and a call's, over the generated ones."""

from __future__ import annotations

import importlib
import re
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_runtime import Exchange, arecord, json_response, raw_response, record, run

if TYPE_CHECKING:
    from types import ModuleType

_RAW: Final = "https://raw.example.com/items"
_BOUNDARY: Final = re.compile(r"dcg[0-9a-f]{32}")


def _modules(package: ModuleType) -> tuple[ModuleType, ModuleType, ModuleType]:
    options, bodies, types = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "bodies", "types.pets")
    )
    return options, bodies, types


def headers(package: ModuleType, lines: list[str]) -> None:
    """Patch headers in layers, and refuse patches that relabel a body or a narrowed response, or are malformed."""
    options, bodies, types = _modules(package)
    exchange = Exchange(lines)
    http = exchange.client()
    patch = (("User-Agent", "custom/1"), ("X-Client", "c"), ("Accept-Encoding", "gzip;q=0.5, identity"))
    with package.Client(http_client=http, options=options.ClientOptions(headers=patch)) as api:
        _layers(api, exchange, lines, options, types)
        _framing(api, exchange, lines, options, bodies, types)
    http.close()
    _malformed(lines, options)
    run(lambda: _async_headers(package, lines))
    lines[:] = [_BOUNDARY.sub("<boundary>", line) for line in lines]


def _layers(api: Any, exchange: Exchange, lines: list[str], options: ModuleType, types: ModuleType) -> None:
    """Replace each name's values where they first came, remove them with None, and append new names in order."""
    codecs = types.ListPetsRequestCodecs
    trace = codecs.parameter(location="header", name="X-Trace").from_wire("t1").value
    session = codecs.parameter(location="cookie", name="session").from_wire("s1")
    pets = json_response(200, [], **{"X-Rate": "1"})
    exchange.respond(pets)
    record(lines, "client headers", lambda: api.pets.list_pets(x_trace=trace))
    view = api.with_options(
        options.RequestOptions(headers=(("x-client", None), ("X-View", "v1"), ("X-View", "v2"), ("X-Trace", "view")))
    )
    exchange.respond(pets)
    record(lines, "view headers under the parameters", lambda: view.pets.list_pets(x_trace=trace, session=session))
    call = options.RequestOptions(
        headers=(("x-trace", "call"), ("Cookie", None), ("X-View", None), ("Accept", "application/json"))
    )
    exchange.respond(pets)
    record(
        lines,
        "call headers over the parameters",
        lambda: view.pets.list_pets(x_trace=trace, session=session, options=call),
    )


def _framing(  # noqa: PLR0913, PLR0917
    api: Any, exchange: Exchange, lines: list[str], options: ModuleType, bodies: ModuleType, types: ModuleType
) -> None:
    """Keep the body's media type and a narrowed Accept, which a patch may only repeat, and label raw bytes freely."""
    fox = types.CreatePetRequestCodecs.body(media_type="application/json").from_wire({"name": "fox"})
    pet = types.GetPetRequestCodecs.parameter(location="path", name="petId").from_wire(3)
    exchange.respond(json_response(201, {"id": 1, "name": "fox"}))
    record(
        lines,
        "body media type repeated",
        lambda: api.pets.create_pet(body=fox, media_type="application/json", options=options.RequestOptions(headers=(("Content-Type", "Application/JSON"),))),
    )
    for label, call in (
        ("body relabeled", lambda: api.pets.create_pet(body=fox, media_type="application/json", options=options.RequestOptions(headers=(("Content-Type", "text/plain"),)))),
        ("body media type removed", lambda: api.pets.create_pet(body=fox, media_type="application/json", options=options.RequestOptions(headers=(("content-type", None),)))),
        ("media type of no body", lambda: api.pets.get_pet(pet_id=pet, options=options.RequestOptions(headers=(("Content-Type", "text/plain"),)))),
        (
            "narrowed Accept replaced",
            lambda: api.pets.get_pet(pet_id=pet, response_media_type="application/json", options=options.RequestOptions(headers=(("Accept", "*/*"),))),
        ),
        (
            "narrowed Accept removed",
            lambda: api.pets.get_pet(pet_id=pet, response_media_type="application/json", options=options.RequestOptions(headers=(("Accept", None),))),
        ),
        (
            "raw parts relabeled",
            lambda: api.request_raw("POST", _RAW, body=bodies.MultipartBody((bodies.FieldPart("a", "1"),)), options=options.RequestOptions(headers=(("Content-Type", "multipart/form-data"),))),
        ),
    ):
        record(lines, label, call)
    exchange.respond(json_response(200, {"id": 3, "name": "fox"}))
    record(
        lines,
        "no body without a media type, narrowed Accept repeated",
        lambda: api.pets.get_pet(pet_id=pet, response_media_type="application/json", options=options.RequestOptions(headers=(("Content-Type", None), ("Accept", "Application/JSON")))),
    )
    exchange.respond(raw_response(200, b"ok", "text/plain"), raw_response(200, b"ok", "text/plain"))
    record(
        lines,
        "raw bytes labeled",
        lambda: api.request_raw("POST", _RAW, body=b"<a/>", options=options.RequestOptions(headers=(("Content-Type", "application/xml"),))).body_bytes,
    )
    record(
        lines,
        "raw parts with their media type repeated",
        lambda: api.request_raw("POST", _RAW, body=bodies.MultipartBody(()), options=options.RequestOptions(headers=(("X-Raw", "1"),))).body_bytes,
    )


def _malformed(lines: list[str], options: ModuleType) -> None:
    """Refuse reserved, malformed, contradictory, and undecodable header patches when the options are made."""
    for label, patch in (
        ("reserved header", (("Host", "a"),)),
        ("header name that is no token", (("Bad Name", "v"),)),
        ("header value on two lines", (("X-A", "a\r\nb"),)),
        ("header set and removed", (("X-A", "a"), ("x-a", None))),
        ("header that is no pair", (("X-A",),)),
        ("header value that is no text", (("X-A", 1),)),
        ("headers that are no sequence", "X-A: a"),
        ("coding without a decoder", (("Accept-Encoding", "br"),)),
        ("coding named twice", (("Accept-Encoding", "gzip, GZIP"),)),
        ("coding of a weight out of range", (("Accept-Encoding", "gzip;q=2"),)),
        ("coding of two weights", (("Accept-Encoding", "deflate;q=1;q=0"),)),
    ):
        record(lines, label, lambda patch=patch: options.RequestOptions(headers=patch))
    record(lines, "headers listed", lambda: options.ClientOptions(headers=[["X-A", "a"], ("X-B", None)]).headers)


async def _async_headers(package: ModuleType, lines: list[str]) -> None:
    options, _, types = _modules(package)
    exchange = Exchange(lines)
    http = exchange.async_client()
    pet = types.GetPetRequestCodecs.parameter(location="path", name="petId").from_wire(3)
    async with package.AsyncClient(http_client=http, options=options.ClientOptions(headers=(("X-Client", "c"),))) as api:
        exchange.respond(json_response(200, {"id": 3, "name": "fox"}))
        await arecord(
            lines,
            "async call headers",
            lambda: api.pets.get_pet(pet_id=pet, options=options.RequestOptions(headers=(("X-Call", "1"),))),
        )
        exchange.respond(raw_response(200, b"ok", "text/plain"))

        async def raw_call() -> bytes:
            response = await api.request_raw("GET", _RAW, options=options.RequestOptions(headers=(("X-Raw", "1"),)))
            return response.body_bytes

        await arecord(lines, "async raw headers", raw_call)
    await http.aclose()
