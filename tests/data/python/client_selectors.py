"""Select concrete request and response media for declared media types and ranges, and refuse selectors misused."""

from __future__ import annotations

import importlib
import re
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_runtime import Exchange, arecord, json_response, raw_response, record, run

if TYPE_CHECKING:
    from types import ModuleType

_BOUNDARY: Final = re.compile(r"dcg[0-9a-f]{32}")


def selectors(package: ModuleType, lines: list[str]) -> None:
    """Send and accept concrete media through selectors, synchronously and with asyncio, refusing misused ones."""
    files = importlib.import_module(f"{package.__name__}.types.files")
    codecs = importlib.import_module(f"{package.__name__}.model_codecs")
    store = files.StoreFileRequestCodecs
    exchange = Exchange(lines)
    http = exchange.client()
    with package.Client(http_client=http) as api:
        _requests(api, exchange, lines, package)
        _responses(api, exchange, lines, store)
        _misuse(api, lines, store, files, codecs)
    http.close()
    run(lambda: _async_selectors(package, lines, store))
    lines[:] = [_BOUNDARY.sub("<boundary>", line) for line in lines]


def _requests(api: Any, exchange: Exchange, lines: list[str], package: ModuleType) -> None:
    """Send a body of each declared media type through its selector, with the selector's concrete media type.

    A text body takes the charset its concrete type names, or else its declared type's, and a multipart body names the
    boundary of its call.
    """
    files, documents, forms, bodies = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("types.files", "types.documents", "types.forms", "bodies")
    )
    store = files.StoreFileRequestCodecs
    address = store.body(media_type="application/json").from_wire({"city": "Oslo", "codes": [7]})
    csv = store.body(media_type="text/*").from_wire("a,b")
    for label, declared, concrete, body in (
        ("json", "application/json", "application/json", address),
        ("json with a charset", "application/json", "application/json; charset=utf-8", address),
        ("pdf", "application/*", "application/pdf", b"%PDF"),
        ("png", "image/*", "image/png", b"\x89PNG"),
        ("csv", "text/*", "text/csv; charset=utf-8", csv),
        ("csv in UTF-16", "text/*", "text/csv; charset=utf-16", csv),
    ):
        selector = store.select_request_media(declared_media=declared, concrete_media=concrete)
        exchange.respond(raw_response(204))
        record(lines, f"{label} sent", lambda body=body, selector=selector: api.files.store_file(body=body, media_type=selector))
    ascii_csv = store.select_request_media(declared_media="text/*", concrete_media="text/csv; charset=us-ascii")
    record(lines, "csv its charset cannot represent", lambda: api.files.store_file(body=store.body(media_type="text/*").from_wire("caf\xe9"), media_type=ascii_csv))
    anything = files.ReplaceFileRequestCodecs.select_request_media(declared_media="*/*", concrete_media="font/woff2")
    exchange.respond(raw_response(204))
    record(lines, "font sent", lambda: api.files.replace_file(body=b"wOF2", media_type=anything))
    text = documents.StoreDocumentRequestCodecs.select_request_media(
        declared_media="text/plain; charset=utf-16", concrete_media="text/plain"
    )
    exchange.respond(json_response(200, {"stored": True}))
    record(lines, "text sent with its declared charset", lambda: api.documents.store_document(body="note", media_type=text))
    bounded = forms.SubmitPartsRequestCodecs.select_request_media(
        declared_media="multipart/form-data", concrete_media="multipart/form-data; boundary=mine"
    )
    exchange.respond(raw_response(204))
    record(lines, "parts sent with a boundary of their own", lambda: api.forms.submit_parts(body=bodies.MultipartBody((bodies.FieldPart("a", "1"),)), media_type=bounded))
    record(lines, "json codec of its selector", lambda: store.body(media_type=store.select_request_media(declared_media="application/json", concrete_media="application/json")).from_wire({"city": "c"}))


def _responses(api: Any, exchange: Exchange, lines: list[str], store: Any) -> None:
    """Accept only a selector's concrete media type, each status reading the branch that type dispatches to."""
    png = store.select_response_media(declared_media="image/*", concrete_media="image/png")
    anything = store.select_response_media(declared_media="*/*", concrete_media="text/csv")
    for label, selector, responder in (
        ("png read", png, raw_response(200, b"\x89PNG", "image/png")),
        ("png copy read", png, raw_response(201, b"\x89PNG", "image/png")),
        ("png stored without a body", png, raw_response(204)),
        ("jpeg read where png is accepted", png, raw_response(200, b"\xff\xd8", "image/jpeg")),
        ("csv read", anything, raw_response(200, b"a,b", "text/csv")),
        ("json read where csv is accepted", anything, json_response(200, {"city": "Oslo"})),
    ):
        exchange.respond(responder)
        record(lines, label, lambda selector=selector: api.files.store_file(body=b"x", media_type=store.select_request_media(declared_media="image/*", concrete_media="image/gif"), response_media_type=selector))
    exchange.respond(json_response(200, {"city": "Oslo"}), raw_response(201, b"\x89PNG", "image/png"))
    address = store.body(media_type="application/json").from_wire({"city": "c"})
    for label in ("json read by its literal", "copy read by the json literal"):
        record(lines, label, lambda: api.files.store_file(body=address, media_type="application/json", response_media_type="application/json"))


def _misuse(api: Any, lines: list[str], store: Any, files: ModuleType, codecs: ModuleType) -> None:
    """Refuse selectors of undeclared, ranged, or other media, of other operations or directions, and made by hand."""
    replace = files.ReplaceFileRequestCodecs.select_request_media(declared_media="*/*", concrete_media="image/png")
    png = store.select_response_media(declared_media="image/*", concrete_media="image/png")
    for label, call in (
        ("selector of an undeclared type", lambda: store.select_request_media(declared_media="video/*", concrete_media="video/mp4")),
        ("selector of a range", lambda: store.select_request_media(declared_media="image/*", concrete_media="image/*")),
        ("selector of a type outside its range", lambda: store.select_request_media(declared_media="image/*", concrete_media="text/plain")),
        ("selector of a type a narrower declaration wins", lambda: store.select_request_media(declared_media="application/*", concrete_media="application/json")),
        ("selector of no media type", lambda: store.select_request_media(declared_media="image/*", concrete_media="not a media type")),
        ("response selector of a type outside its range", lambda: store.select_response_media(declared_media="image/*", concrete_media="text/plain")),
        ("selector of another operation", lambda: api.files.store_file(body=b"x", media_type=replace)),
        ("response selector as a request's", lambda: api.files.store_file(body=b"x", media_type=png)),
        ("request selector as a response's", lambda: api.files.store_file(body=b"x", media_type=store.select_request_media(declared_media="image/*", concrete_media="image/png"), response_media_type=store.select_request_media(declared_media="image/*", concrete_media="image/png"))),
        ("range named without a selector", lambda: api.files.store_file(body=b"x", media_type="image/*")),
        ("binary codec of its selector", lambda: store.body(media_type=store.select_request_media(declared_media="image/*", concrete_media="image/png"))),
        ("codec of another operation's selector", lambda: store.body(media_type=replace)),
        ("selector made by hand", lambda: codecs.RequestMedia("image/*", "image/png", store, None)),
    ):
        record(lines, label, call)
    request = store.select_request_media(declared_media="image/*", concrete_media="image/png")
    lines.append(f"  selector {request!r} {png!r} {request.declared_media} {request.concrete_media}")


async def _async_selectors(package: ModuleType, lines: list[str], store: Any) -> None:
    exchange = Exchange(lines)
    http = exchange.async_client()
    async with package.AsyncClient(http_client=http) as api:
        png = store.select_request_media(declared_media="image/*", concrete_media="image/png")
        accepted = store.select_response_media(declared_media="image/*", concrete_media="image/png")
        exchange.respond(raw_response(200, b"\x89PNG", "image/png"))
        await arecord(lines, "async png", lambda: api.files.store_file(body=b"\x89PNG", media_type=png, response_media_type=accepted))
    await http.aclose()
