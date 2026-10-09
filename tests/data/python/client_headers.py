"""Merge the headers of generated clients' calls: the client's, a view's, and a call's, over the generated ones."""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlencode

import httpx2

from tests.data.python.client_runtime import (
    Exchange,
    arecord,
    argument,
    json_response,
    raw_response,
    record,
    request_body,
    run,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_RAW: Final = "https://raw.example.com/items"
_BOUNDARY: Final = re.compile(r"\b[0-9a-f]{32}\b")
_PART: Final = '--b\r\nContent-Disposition: form-data; name="{}"\r\n\r\n{}\r\n'


def _modules(package: ModuleType) -> tuple[ModuleType, ModuleType, ModuleType]:
    options, bodies, types = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "bodies", "types.pets")
    )
    return options, bodies, types


def headers(package: ModuleType, lines: list[str]) -> None:
    """Merge headers in layers, sending explicit ones over a body's media type or a narrowed Accept, as given."""
    options, bodies, _ = _modules(package)
    exchange = Exchange(lines)
    http = exchange.client()
    defaults = {"User-Agent": "custom/1", "X-Client": "c", "Accept-Encoding": "gzip;q=0.5, identity"}
    with package.Client(http_client=http, default_headers=defaults) as api:
        _layers(api, exchange, lines, options, package)
        _framing(api, exchange, lines, options, bodies, package)
        with package.Client(http_client=http, default_headers={"Content-Type": "application/json"}) as labeled:
            _parts(labeled, api, exchange, lines, options, bodies)
    http.close()
    _unsendable(package, lines, options)
    run(lambda: _async_headers(package, lines))
    lines[:] = [_BOUNDARY.sub("<boundary>", line) for line in lines]


def _layers(api: Any, exchange: Exchange, lines: list[str], options: ModuleType, package: ModuleType) -> None:
    """Replace each name's value where it first came, case-insensitively, remove it with None, and append new names."""
    trace = argument(package, "listPets", "header", "X-Trace", "t1")
    session = argument(package, "listPets", "cookie", "session", "s1")
    pets = json_response(200, [], **{"X-Rate": "1"})
    exchange.respond(pets)
    record(lines, "client headers", lambda: api.pets.list_pets(x_trace=trace))
    view = api.with_options(default_headers={"x-client": None, "X-View": "v1", "X-Trace": "view"})
    exchange.respond(pets)
    record(lines, "view headers under the parameters", lambda: view.pets.list_pets(x_trace=trace, session=session))
    nested = view.with_options(default_headers={"x-view": "v2", "USER-AGENT": "nested/1"})
    exchange.respond(pets)
    record(lines, "nested view headers", lambda: nested.pets.list_pets(x_trace=trace))
    call = options.RequestOptions(
        extra_headers={"x-trace": "call", "Cookie": None, "X-View": None, "Accept": "application/json"}
    )
    exchange.respond(pets)
    record(
        lines,
        "call headers over the parameters",
        lambda: view.pets.list_pets(x_trace=trace, session=session, options=call),
    )


def _framing(  # noqa: PLR0913, PLR0917
    api: Any, exchange: Exchange, lines: list[str], options: ModuleType, bodies: ModuleType, package: ModuleType
) -> None:
    """Send an explicit Content-Type or Accept over the body's media type and a narrowed Accept, or remove them."""
    fox = request_body(package, "createPet", "application/json", {"name": "fox"})
    pet = argument(package, "getPet", "path", "petId", 3)
    created, found = json_response(201, {"id": 1, "name": "fox"}), json_response(200, {"id": 3, "name": "fox"})
    exchange.respond(created, created, found, found, found)
    for label, call in (
        (
            "body relabeled",
            lambda: api.pets.create_pet(
                body=fox,
                media_type="application/json",
                options=options.RequestOptions(extra_headers={"Content-Type": "text/plain"}),
            ),
        ),
        (
            "body media type removed",
            lambda: api.pets.create_pet(
                body=fox,
                media_type="application/json",
                options=options.RequestOptions(extra_headers={"content-type": None}),
            ),
        ),
        (
            "media type of no body",
            lambda: api.pets.get_pet(
                pet_id=pet, options=options.RequestOptions(extra_headers={"Content-Type": "text/plain"})
            ),
        ),
        (
            "narrowed Accept replaced",
            lambda: api.pets.get_pet(
                pet_id=pet,
                response_media_type="application/json",
                options=options.RequestOptions(extra_headers={"Accept": "*/*"}),
            ),
        ),
        (
            "narrowed Accept removed",
            lambda: api.pets.get_pet(
                pet_id=pet,
                response_media_type="application/json",
                options=options.RequestOptions(extra_headers={"Accept": None}),
            ),
        ),
    ):
        record(lines, label, call)
    exchange.respond(created)
    record(
        lines,
        "view Content-Type under the body's media type",
        lambda: api.with_options(default_headers={"Content-Type": "text/plain"}).pets.create_pet(
            body=fox, media_type="application/json"
        ),
    )
    exchange.respond(raw_response(200, b"ok", "text/plain"), raw_response(200, b"ok", "text/plain"))
    record(
        lines,
        "raw bytes labeled",
        lambda: (
            api.request_raw(
                "POST",
                _RAW,
                body=b"<a/>",
                options=options.RequestOptions(extra_headers={"Content-Type": "application/xml"}),
            ).body_bytes
        ),
    )
    record(
        lines,
        "raw parts relabeled",
        lambda: (
            api.request_raw(
                "POST",
                _RAW,
                body=bodies.MultipartBody((bodies.FieldPart("a", "1"),)),
                options=options.RequestOptions(extra_headers={"Content-Type": "multipart/mixed"}),
            ).body_bytes
        ),
    )


def _parts(  # noqa: PLR0913, PLR0917
    labeled: Any, api: Any, exchange: Exchange, lines: list[str], options: ModuleType, bodies: ModuleType
) -> None:
    """Keep the parts' media type and boundary over a client's or a view's Content-Type, which only a call replaces."""
    parts = bodies.MultipartBody((bodies.FieldPart("a", "1"),))
    for label, layer, call in (
        ("raw parts under a client Content-Type", labeled, None),
        ("raw parts under a view Content-Type", api.with_options(default_headers={"Content-Type": "text/plain"}), None),
        ("raw parts under a view without Content-Type", api.with_options(default_headers={"Content-Type": None}), None),
        (
            "raw parts relabeled without a boundary",
            labeled,
            options.RequestOptions(extra_headers={"Content-Type": "multipart/mixed"}),
        ),
        ("raw parts unlabeled by the call", labeled, options.RequestOptions(extra_headers={"Content-Type": None})),
    ):
        exchange.respond(raw_response(200, b"ok", "text/plain"))
        record(
            lines,
            label,
            lambda layer=layer, call=call: layer.request_raw("POST", _RAW, body=parts, options=call).body_bytes,
        )


def _unsendable(package: ModuleType, lines: list[str], options: ModuleType) -> None:
    """Fail natively when HTTPX2 cannot send a header value, without waiting to retry the call."""
    pet = argument(package, "getPet", "path", "petId", 3)
    exchange, waits = Exchange(lines), []
    split = options.RequestOptions(extra_headers={"X-A": "a\r\nb"})
    with exchange.client() as http, package.Client(http_client=http, clock=options.Clock(sleep=waits.append)) as api:
        record(lines, "header value on two lines", lambda: api.pets.get_pet(pet_id=pet, options=split))
    lines.append(f"  header value on two lines retry waits {waits}")


async def _async_headers(package: ModuleType, lines: list[str]) -> None:
    options, _, _ = _modules(package)
    exchange = Exchange(lines)
    http = exchange.async_client()
    pet = argument(package, "getPet", "path", "petId", 3)
    async with package.AsyncClient(http_client=http, default_headers={"X-Client": "c"}) as api:
        exchange.respond(json_response(200, {"id": 3, "name": "fox"}))
        await arecord(
            lines,
            "async call headers",
            lambda: api.pets.get_pet(
                pet_id=pet, options=options.RequestOptions(extra_headers={"X-Call": "1", "x-client": None})
            ),
        )
        exchange.respond(raw_response(200, b"ok", "text/plain"))

        async def raw_call() -> bytes:
            response = await api.request_raw("GET", _RAW, options=options.RequestOptions(extra_headers={"X-Raw": "1"}))
            return response.body_bytes

        await arecord(lines, "async raw headers", raw_call)
    await http.aclose()


def _text_outcome(call: Callable[[], object]) -> str:
    """Report a value, or a failure by its reason, location and cause without a validating backend's own wording."""
    try:
        return f"= {call()!r}"
    except Exception as error:  # noqa: BLE001
        cause = getattr(error, "cause", None)
        if hasattr(cause, "errors"):
            detail = f"{type(cause).__name__}{[item['type'] for item in cause.errors()]}"
        elif type(cause).__module__ in {"builtins", "decimal"}:
            detail = repr(cause)
        else:
            detail = type(cause).__name__
        place = ".".join(getattr(error, "location", None) or ())
        return f"! {type(error).__name__} {getattr(error, 'reason', None)} {place} cause={detail}".replace("  ", " ")


def native_boundaries(package: ModuleType, lines: list[str]) -> None:
    """Decode content headers natively and assemble a nullable model body from its fields."""
    types = importlib.import_module(f"{package.__name__}.types.native_headers")
    codecs = importlib.import_module(f"{package.__name__}.model_codecs")
    lines.append(f"  public codec exports {codecs.__all__}")
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        for label, header in (
            ("duplicate JSON keys", '{"value":"first","value":"last"}'),
            ("malformed JSON", "{broken"),
            ("missing required header", None),
        ):
            exchange.respond(raw_response(204, **({} if header is None else {"X-Value": header})))
            info = api.native_headers.with_raw_response.get_values().info
            record(lines, label, lambda: types.decode_get_values_header(info, name="X-Value"))
            record(
                lines, "missing optional JSON header", lambda: types.decode_get_values_header(info, name="X-Optional")
            )
        for name, value in (
            ("X-Count", "05"),
            ("X-Count", "+1"),
            ("X-Count", "1.0"),
            ("X-Count", "many"),
            ("X-Flag", "TRUE"),
            ("X-Flag", "false"),
            ("X-Counts", "05,+1,1.0"),
            ("X-Entry", "value=known,unknown=extra"),
            ("X-Entry", "value=known,unknown=first,unknown=last"),
            ("X-Closed", "value=known,unknown=extra"),
        ):
            exchange.respond(raw_response(204, **{name: value}))
            info = api.native_headers.with_raw_response.get_values().info
            record(lines, f"native {name} {value}", lambda: types.decode_get_values_header(info, name=name))
        source = Path(__file__).parents[1] / "generation_platform/client/native-text.json"
        cases = json.loads(source.read_text(encoding="utf-8"))
        for name, value in cases["headers"]:
            content = ((name.encode(), value.encode()),)
            exchange.respond(lambda _, content=content: httpx2.Response(204, headers=content))
            info = api.native_headers.with_raw_response.get_values().info
            read = _text_outcome(lambda: types.decode_get_values_header(info, name=name))
            lines.append(f"  native {name} {value} {read}")
        for fields in cases["forms"]:
            exchange.respond(raw_response(200, urlencode(fields).encode(), "application/x-www-form-urlencoded"))
            lines.append(f"  native form {fields} {_text_outcome(api.native_headers.get_form)}")
            parts = "".join(_PART.format(name, value) for name, value in fields.items())
            exchange.respond(raw_response(200, f"{parts}--b--\r\n".encode(), "multipart/form-data; boundary=b"))
            lines.append(f"  native parts {fields} {_text_outcome(api.native_headers.get_parts)}")
        run(lambda: _async_native_headers(package, types, lines))
        exchange.respond(raw_response(204))
        record(lines, "nullable model from fields", lambda: api.native_headers.save_value(value="saved"))
        exchange.responders.clear()


async def _async_native_headers(package: ModuleType, types: ModuleType, lines: list[str]) -> None:
    """Convert header text through the same model codecs on the async client."""
    exchange = Exchange(lines)
    async with exchange.async_client() as http, package.AsyncClient(http_client=http) as api:
        exchange.respond(raw_response(204, **{"X-Count": "+1", "X-Entry": "value=known,unknown=extra"}))
        info = (await api.native_headers.with_raw_response.get_values()).info
        for name in ("X-Count", "X-Entry"):
            record(lines, f"async native {name}", lambda: types.decode_get_values_header(info, name=name))
