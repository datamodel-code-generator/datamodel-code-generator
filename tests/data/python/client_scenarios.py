"""Call generated clients through HTTPX2: request encoding, response dispatch, and injected failure paths."""

from __future__ import annotations

import gzip
import importlib
import json
import zlib
from functools import partial
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_allowreserved import reserved_paths
from tests.data.python.client_auth_challenges import auth_challenges
from tests.data.python.client_auth_errors import auth_errors
from tests.data.python.client_auth_flows import auth_flows
from tests.data.python.client_auth_options import auth_options
from tests.data.python.client_auth_values import auth_values
from tests.data.python.client_bodies import bodies
from tests.data.python.client_body_digest import body_digest
from tests.data.python.client_body_replay import body_replay, multipart_replay
from tests.data.python.client_body_replay_faults import body_replay_faults
from tests.data.python.client_caching import cache_backends, cache_stores, caching
from tests.data.python.client_compression import compression
from tests.data.python.client_deadline_cleanup import deadline_cleanup
from tests.data.python.client_deadline_files import deadline_files
from tests.data.python.client_deadline_options import deadline_options
from tests.data.python.client_deadline_races import deadline_races
from tests.data.python.client_deadline_streams import deadline_streams
from tests.data.python.client_evolution import evolution
from tests.data.python.client_fields import fields, optional_models
from tests.data.python.client_headers import headers
from tests.data.python.client_hooks import hooks
from tests.data.python.client_limiter_faults import limiter_faults
from tests.data.python.client_limiters import limiters
from tests.data.python.client_multipart import multipart, split_parts
from tests.data.python.client_native import native_faults, native_wire
from tests.data.python.client_native_signing import native_signing
from tests.data.python.client_oauth_client_credentials import oauth_client_credentials
from tests.data.python.client_oauth_refresh import oauth_refresh
from tests.data.python.client_pagination import pagination, pagination_backends, pagination_limits
from tests.data.python.client_pagination_count_values import pagination_count_defaults, pagination_count_values
from tests.data.python.client_pagination_counts import pagination_counts
from tests.data.python.client_pagination_links import pagination_links
from tests.data.python.client_pagination_resume import pagination_resume
from tests.data.python.client_pagination_sessions import pagination_auth, pagination_sessions
from tests.data.python.client_pagination_targets import (
    pagination_paths,
    pagination_querystring,
    pagination_targets,
    path_arguments,
)
from tests.data.python.client_polling import polling
from tests.data.python.client_polling_resume import polling_resume
from tests.data.python.client_protocol_contracts import protocol_contracts
from tests.data.python.client_protocol_errors import protocol_errors
from tests.data.python.client_query import query
from tests.data.python.client_raw import raw
from tests.data.python.client_redirects import head_redirects, redirects
from tests.data.python.client_regressions import json_decode_errors, no_success
from tests.data.python.client_retry_boundaries import retry_boundaries
from tests.data.python.client_retry_calls import retry_calls
from tests.data.python.client_retry_errors import retry_errors
from tests.data.python.client_retry_options import retry_options
from tests.data.python.client_retry_policy import retry_policy
from tests.data.python.client_runtime import (
    Exchange,
    abroken,
    arecord,
    argument,
    broken,
    chunked_response,
    failing,
    generated,
    injected,
    json_response,
    raw_response,
    record,
    request_body,
    run,
)
from tests.data.python.client_signatures import keywords, signatures
from tests.data.python.client_socket_connectors import socket_connector_outcomes, socket_connectors
from tests.data.python.client_sockets import sockets
from tests.data.python.client_stream_lifetimes import stream_lifetimes
from tests.data.python.client_stream_resume import stream_resume
from tests.data.python.client_streams import ndjson, ndjson_backends, ndjson_split, stream_backends, streams
from tests.data.python.client_streams import stream_lifetimes as event_stream_lifetimes
from tests.data.python.client_unions import split_unions, unions
from tests.data.python.client_uploads import upload_compression, uploads, uploads_oauth
from tests.data.python.client_webhook_adapters import (
    webhook_adapter_imports,
    webhook_adapters,
    webhook_mapped_backends,
    webhook_unsigned,
)
from tests.data.python.client_webhook_contracts import webhook_contracts
from tests.data.python.client_webhook_errors import webhook_errors
from tests.data.python.client_webhook_public_keys import webhook_public_keys
from tests.data.python.client_webhooks import webhook_backends, webhook_verification

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import ModuleType


def _modules(package: ModuleType, *names: str) -> list[ModuleType]:
    return [importlib.import_module(f"{package.__name__}.{name}") for name in names]


def _pet(package: ModuleType, operation: str) -> object:
    return argument(package, operation, "path", "petId", 3)


def _trace(package: ModuleType, value: str = "t") -> object:
    return argument(package, "listPets", "header", "X-Trace", value)


def _options(package: ModuleType, lines: list[str]) -> None:
    (options,) = _modules(package, "options")
    selection = options.ServerSelection
    for label, build in (
        ("server and base_url", lambda: options.ClientOptions(server=selection(), base_url="https://a.example.com")),
        ("server type", lambda: options.RequestOptions(server="eu")),
        ("base_url type", lambda: options.RequestOptions(base_url=5)),
        ("base_url scheme", lambda: options.RequestOptions(base_url="ftp://a.example.com")),
        ("base_url port", lambda: options.RequestOptions(base_url="https://a.example.com:port")),
        ("base_url userinfo", lambda: options.RequestOptions(base_url="https://user@a.example.com")),
        ("base_url query", lambda: options.RequestOptions(base_url="https://a.example.com/v1?key=1")),
        ("base_url fragment", lambda: options.RequestOptions(base_url="https://a.example.com/#top")),
        ("base_url port zero", lambda: options.RequestOptions(base_url="https://a.example.com:0")),
        ("max_response_bytes bool", lambda: options.RequestOptions(max_response_bytes=True)),
        ("max_response_bytes negative", lambda: options.RequestOptions(max_response_bytes=-1)),
        ("max_error_body_bytes zero", lambda: options.RequestOptions(max_error_body_bytes=0)),
        ("max_error_body_bytes large", lambda: options.RequestOptions(max_error_body_bytes=2 * 1024 * 1024)),
        ("server index", lambda: selection(index=-1)),
        ("server variables", lambda: selection(variables={"region": 1})),
        ("server", lambda: selection(index=0, variables={"region": "eu"})),
        ("limits", lambda: options.RequestOptions(max_response_bytes=None, max_error_body_bytes=10)),
    ):
        record(lines, f"options {label}", build)


def _headers(package: ModuleType, lines: list[str]) -> None:
    (responses,) = _modules(package, "responses")
    view = responses.HeadersView([("Set-Cookie", "a=1"), ("X-Id", "7"), ("set-cookie", "b=2")])
    lines.extend((
        f"  headers {view!r} {len(view)} {list(view)} {view.items()}",
        f"  headers get {view.get('SET-COOKIE')} {view.get('x-missing')} {view.get_all('set-cookie')}",
        f"  headers contains {'x-id' in view} {'missing' in view} {5 in view}",
        f"  headers equal {view == responses.HeadersView(view.items())} {view == 5}",
        f"  headers hash {hash(view) == hash(responses.HeadersView(view.items()))}",
    ))


def _errors(package: ModuleType, lines: list[str]) -> None:
    (errors,) = _modules(package, "errors")
    record(lines, "error condition", lambda: errors.ConfigurationError(reason="invalid_value"))
    error = errors.SDKError()
    lines.append(
        f"  error bare {error} {error.reason_code} "
        f"{errors.APIConnectionError(delivery_state=errors.DeliveryState.NOT_SENT)}"
    )


def _lifecycle(package: ModuleType, lines: list[str]) -> None:
    owned = package.Client()
    owned.close()
    owned.close()
    borrowed_http = httpx2.Client()
    with package.Client(http_client=borrowed_http):
        pass
    lines.append(f"  lifecycle borrowed closed {borrowed_http.is_closed}")
    borrowed_http.close()
    record(lines, "client http_client", lambda: package.Client(http_client="http"))
    record(lines, "client options", lambda: package.Client(options="fast"))


def _list_pets(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    types, errors = _modules(package, "types.pets", "errors")
    limit = argument(package, "listPets", "query", "limit", 5)
    labels = argument(package, "listPets", "query", "tags", ["a b", "c"])
    trace = argument(package, "listPets", "header", "X-Trace", "t1")
    session = argument(package, "listPets", "cookie", "session", "s1")
    headers = {"X-Rate": "10", "X-Next": "abc", "X-Request-Id": "req-1"}
    pets = [{"id": 1, "name": "cat", "tag": None}]
    exchange.respond(json_response(200, pets, **headers))
    record(lines, "list", lambda: api.pets.list_pets(limit=limit, labels=labels, x_trace=trace, session=session))
    exchange.respond(json_response(200, pets, **{"X-Rate": "10"}))
    response = record(lines, "list response", lambda: api.pets.with_response.list_pets(x_trace=trace))
    info = response.info
    lines.append(f"  info {info.status_code} {info.content_type} {info.request_id} {info.headers!r}")
    for name in ("X-Next", "x-rate", "X-Other"):
        record(lines, f"header {name}", lambda name=name: types.decode_list_pets_header(info, name=name))
    for label, extra in (("missing", {}), ("invalid", {"X-Rate": "many"})):
        exchange.respond(json_response(200, pets, **extra))
        response = api.pets.with_response.list_pets(x_trace=trace)
        record(
            lines,
            f"header {label}",
            lambda response=response: types.decode_list_pets_header(response.info, name="X-Rate"),
        )
    for label, responder, attempts in (
        ("error", json_response(500, {"code": 7, "message": "boom"}), 3),
        ("error syntax", raw_response(500, b"{", "application/json"), 1),
        ("error media", raw_response(503, b"down", "text/plain"), 3),
        ("error bare", raw_response(502, b"down"), 3),
        ("redirect", raw_response(302, b"", Location="https://elsewhere.example.com"), 1),
        ("success default", json_response(201, {"code": 1}), 1),
        ("success media", raw_response(200, b"[]"), 1),
        ("success syntax", raw_response(200, b"[", "application/json"), 1),
        ("success value", json_response(200, [{"id": "one"}]), 1),
        ("success empty", raw_response(204), 1),
    ):
        exchange.respond(*((responder,) * attempts))
        error = record(lines, f"list {label}", lambda: api.pets.list_pets(x_trace=trace))
        if error is None and label == "redirect":
            exchange.respond(raw_response(302, b"", Location="https://elsewhere.example.com"))
            try:
                api.pets.list_pets(x_trace=trace)
            except Exception as failure:  # ruff: ignore[blind-except]
                lines.append(f"  redirect info {failure.status_code} {failure.headers!r}")
        if error is None and label == "error":
            exchange.respond(*((json_response(500, {"code": 7}),) * 3))
            try:
                api.pets.list_pets(x_trace=trace)
            except errors.InternalServerError as failure:
                lines.append(
                    f"  error info {failure.status_code} {failure.headers!r} {failure.request_id} "
                    f"{failure.reason_code} {failure.body!r}"
                )
                record(
                    lines,
                    "header error",
                    lambda failure=failure: types.decode_list_pets_header(failure.info, name="X-Next"),
                )
    exchange.respond(raw_response(500, b"x" * 20, "application/json"))
    options = importlib.import_module(f"{package.__name__}.options")
    record(
        lines,
        "list truncated",
        lambda: api.pets.list_pets(x_trace=trace, options=options.RequestOptions(max_error_body_bytes=4)),
    )
    record(lines, "list missing", lambda: api.pets.list_pets(x_trace=options.UNSET))
    record(lines, "list invalid", lambda: api.pets.list_pets(x_trace=_trace(package, "line\nbreak")))
    record(lines, "list options", lambda: api.pets.list_pets(x_trace=trace, options="fast"))


def _create_pet(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    text = request_body(package, "createPet", "text/plain", "bird")
    created = {"id": 2, "name": "dog"}
    exchange.respond(json_response(201, created), json_response(201, created), json_response(201, created))
    dog = request_body(package, "createPet", "application/json", {"name": "dog"})
    record(lines, "create", lambda: api.pets.create_pet(body=dog, media_type="application/json"))
    native = request_body(package, "createPet", "application/json", {"name": "dog", "tag": "x"})
    record(lines, "create native", lambda: api.pets.create_pet(body=native, media_type="Application/JSON"))
    record(lines, "create text", lambda: api.pets.create_pet(body=text, media_type="text/plain"))
    record(lines, "create missing media", lambda: api.pets.create_pet(body=native))
    unset = importlib.import_module(f"{package.__name__}.options").UNSET
    record(lines, "create without body", lambda: api.pets.create_pet(body=unset))
    record(lines, "create undeclared media", lambda: api.pets.create_pet(body=native, media_type="text/csv"))
    record(lines, "create invalid media", lambda: api.pets.create_pet(body=native, media_type="not media"))
    record(lines, "create invalid body", lambda: api.pets.create_pet(body=object(), media_type="application/json"))
    exchange.respond(json_response(422, {"code": 22}))
    record(lines, "create rejected", lambda: api.pets.create_pet(body=native, media_type="application/json"))


def _get_pet(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    pet = {"id": 3, "name": "fox"}
    for label, responder, media in (
        ("get", json_response(200, pet), None),
        ("get again", json_response(200, pet), None),
        ("get text", raw_response(200, b"fox", "text/plain; charset=latin-1"), "text/plain"),
        ("get flowed", raw_response(200, b"fox", "text/plain; format=flowed"), "text/plain"),
        ("get text mismatch", json_response(200, pet), "text/plain"),
        ("get missing", raw_response(404), None),
        ("get missing body", raw_response(404, b"gone", "text/plain"), None),
        ("get created", json_response(201, pet), None),
    ):
        exchange.respond(responder)
        record(
            lines,
            label,
            lambda media=media: api.pets.get_pet(pet_id=_pet(package, "getPet"), response_media_type=media),
        )
    pet = _pet(package, "getPet")
    record(lines, "get undeclared", lambda: api.pets.get_pet(pet_id=pet, response_media_type="image/png"))
    record(lines, "get invalid", lambda: api.pets.get_pet(pet_id=pet, response_media_type="png"))
    exchange.respond(raw_response(204), injected(raw_response(204, b"x", "text/plain")), raw_response(200))
    deleted = _pet(package, "DELETE /pets/{petId}")
    record(lines, "delete", lambda: api.pets.delete_pets_by_pet_id(pet_id=deleted))
    record(lines, "delete body", lambda: api.pets.delete_pets_by_pet_id(pet_id=deleted))
    record(lines, "delete ok", lambda: api.pets.delete_pets_by_pet_id(pet_id=deleted))
    exchange.respond(raw_response(200, ETag='"v1"'))
    response = record(lines, "head", lambda: api.pets.with_response.head_pet(pet_id=_pet(package, "headPet")))
    types = importlib.import_module(f"{package.__name__}.types.pets")
    record(lines, "head etag", lambda: types.decode_head_pet_header(response.info, name="ETag"))


def _upload(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    exchange.respond(raw_response(200, b"\x89PNG", "image/png"), raw_response(200, b"{}", "application/json"))
    pet = _pet(package, "uploadPhoto")
    record(lines, "upload", lambda: api.pets.photos.upload(pet_id=pet, body=b"\x00\x01"))
    record(lines, "upload empty", lambda: api.pets.photos.upload(pet_id=pet))
    record(lines, "upload text", lambda: api.pets.photos.upload(pet_id=pet, body="text"))
    record(lines, "upload media", lambda: api.pets.photos.upload(pet_id=pet, media_type="application/octet-stream"))
    _range_responses(api.pets.photos, exchange, lines, "upload", {"pet_id": pet})


def _servers(package: ModuleType, api_options: Any, exchange: Exchange, lines: list[str]) -> None:
    (options,) = _modules(package, "options")
    selection = options.ServerSelection
    http = exchange.client()
    override = options.ClientOptions(base_url="https://override.example.com/root/")
    pet = _pet(package, "DELETE /pets/{petId}")
    with package.Client(http_client=http, options=override) as api:
        exchange.respond(raw_response(204), raw_response(204))
        record(lines, "base_url", lambda: api.pets.delete_pets_by_pet_id(pet_id=pet))
        eu = options.RequestOptions(server=selection(variables={"region": "eu"}))
        record(lines, "server eu", lambda: api.pets.delete_pets_by_pet_id(pet_id=pet, options=eu))
        for label, chosen in (
            ("server moon", selection(variables={"region": "moon"})),
            ("server zone", selection(variables={"zone": "west"})),
            ("server index", selection(index=3)),
        ):
            record(
                lines,
                label,
                lambda chosen=chosen: api.pets.delete_pets_by_pet_id(
                    pet_id=pet, options=options.RequestOptions(server=chosen)
                ),
            )
    http.close()
    del api_options


def _limits(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    (options,) = _modules(package, "options")
    pets = [{"id": index, "name": "x" * 20} for index in range(20)]
    exchange.respond(json_response(200, pets, **{"X-Rate": "1"}), json_response(200, pets, **{"X-Rate": "1"}))
    trace = _trace(package)
    record(
        lines,
        "too large",
        lambda: api.pets.list_pets(x_trace=trace, options=options.RequestOptions(max_response_bytes=10)),
    )
    unlimited = options.RequestOptions(max_response_bytes=None)
    record(
        lines, "unlimited", lambda: api.pets.with_response.list_pets(x_trace=trace, options=unlimited).info.status_code
    )


def _transports(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    for error, attempts in (
        (httpx2.ConnectError, 1),
        (httpx2.ConnectTimeout, 3),
        (httpx2.PoolTimeout, 1),
        (httpx2.WriteError, 1),
        (httpx2.WriteTimeout, 1),
        (httpx2.ReadTimeout, 1),
        (httpx2.ReadError, 1),
        (httpx2.RemoteProtocolError, 1),
    ):
        exchange.respond(*(failing(error) for _ in range(attempts)))
        record(lines, f"transport {error.__name__}", lambda: api.pets.get_pet(pet_id=_pet(package, "getPet")))
    exchange.respond(broken)
    record(lines, "transport broken", lambda: api.pets.get_pet(pet_id=_pet(package, "getPet")))


def pets(package: ModuleType, lines: list[str]) -> None:
    """Call every pets operation synchronously, then its async client, covering each success and failure."""
    exchange = Exchange(lines)
    (options,) = _modules(package, "options")
    with (
        exchange.client() as native_client_381,
        package.Client(
            http_client=native_client_381, options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
        ) as api,
    ):
        for step in (_list_pets, _create_pet, _get_pet, _upload, _limits, _transports):
            step(package, api, exchange, lines)
    _servers(package, None, exchange, lines)
    _options(package, lines)
    _headers(package, lines)
    _errors(package, lines)
    _lifecycle(package, lines)
    run(lambda: _async_pets(package, exchange, lines))
    run(lambda: _async_range_responses(package, lines, "pets"))


async def _async_pets(package: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    (options,) = _modules(package, "options")
    http = exchange.async_client()
    trace, pet = _trace(package), _pet(package, "getPet")
    (_types,) = _modules(package, "types.pets")
    text = request_body(package, "createPet", "text/plain", "dog")
    async with package.AsyncClient(
        http_client=http, options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
    ) as api:
        exchange.respond(json_response(200, [{"id": 1, "name": "cat"}], **{"X-Rate": "1"}))
        await arecord(lines, "async list", lambda: api.pets.list_pets(x_trace=trace))
        exchange.respond(json_response(201, {"id": 2, "name": "dog"}))
        await arecord(
            lines, "async create", lambda: api.pets.with_response.create_pet(body=text, media_type="text/plain")
        )
        exchange.respond(failing(httpx2.ConnectError))
        await arecord(lines, "async connect", lambda: api.pets.get_pet(pet_id=pet))
        exchange.respond(abroken)
        await arecord(lines, "async broken", lambda: api.pets.get_pet(pet_id=pet))
        exchange.respond(raw_response(200, b"[" * 40, "application/json"))
        limited = options.RequestOptions(max_response_bytes=10)
        await arecord(lines, "async too large", lambda: api.pets.list_pets(x_trace=trace, options=limited))
        exchange.respond(raw_response(500, b"x" * 40, "application/json"))
        truncated = options.RequestOptions(max_error_body_bytes=4)
        await arecord(lines, "async truncated", lambda: api.pets.list_pets(x_trace=trace, options=truncated))
    lines.append(f"  async borrowed closed {http.is_closed}")
    await http.aclose()
    owned = package.AsyncClient()
    await owned.aclose()
    await owned.aclose()
    transferred = exchange.async_client()
    async with package.AsyncClient(http_client=transferred):
        pass
    lines.append(f"  async borrowed second closed {transferred.is_closed}")
    await transferred.aclose()
    await arecord(lines, "async http_client", _async_invalid(package))


def _async_invalid(package: ModuleType) -> Callable[[], Any]:
    async def build() -> object:  # ruff: ignore[unused-async] - The scenario calls this factory through the async entry point.
        return package.AsyncClient(http_client=httpx2.Client())

    return build


def media(package: ModuleType, lines: list[str]) -> None:
    """Send forms, pairs, documents, and notes, and decode forms, texts, documents, and object headers."""
    _, documents = _modules(package, "types.forms", "types.documents")
    exchange = Exchange(lines)
    with exchange.client() as native_client_440, package.Client(http_client=native_client_440) as api:
        form = request_body(package, "submitForm", None, {"name": "a b", "count": 2, "labels": ["x", "y"]})
        exchange.respond(
            raw_response(200, b"name=a+b&count=2", "application/x-www-form-urlencoded"),
            raw_response(200, b"count=many", "application/x-www-form-urlencoded"),
        )
        record(lines, "form", lambda: api.forms.submit_form(body=form))
        record(lines, "form invalid", lambda: api.forms.submit_form(body=form))
        exchange.respond(
            raw_response(200, b"a=1&a=2&b=%20", "application/x-www-form-urlencoded"),
            raw_response(200, b"a=%zz", "application/x-www-form-urlencoded"),
        )
        record(lines, "pairs", lambda: api.forms.submit_pairs(body=(("a", "1"), ("a", "2"), ("b", " "))))
        record(lines, "pairs invalid", api.forms.submit_pairs)
        record(lines, "pairs body", lambda: api.forms.submit_pairs(body=[("a", "1")]))
        search = request_body(
            package,
            "submitSearch",
            None,
            {
                "term": "a b",
                "filter": {"name": "x y", "min": 2},
                "tags": ["a", "b"],
                "ids": [1, 2],
                "meta": {"city": "Oslo"},
                "path": "/a?b",
                "extra": {"page": "2"},
            },
        )
        exchange.respond(raw_response(204))
        record(lines, "search", lambda: api.forms.submit_search(body=search))
        clash = request_body(package, "submitSearch", None, {"term": "a", "extra": {"term": "b"}})
        record(lines, "search of an extra named as another member", lambda: api.forms.submit_search(body=clash))
        _documents(package, api, exchange, lines, documents)
        _files(package, api, exchange, lines)
        _range_responses(api.files, exchange, lines, "store_file", {"body": b"image", "media_type": "image/jpeg"})
        exchange.respond(raw_response(204))
        record(
            lines,
            "range bodyless",
            lambda: api.files.store_file(body=b"image", media_type="image/jpeg", response_media_type="image/jpeg"),
        )
    run(lambda: _async_range_responses(package, lines, "media"))


def _files(package: ModuleType, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send concrete media types within declared ranges, never a range, and narrow a response to one concrete type.

    A concrete type goes to its most specific declaration, whose encoding it takes, and text takes its named charset.
    """
    address = request_body(package, "storeFile", "application/json", {"city": "Oslo"})
    record(lines, "store file of a media range", lambda: api.files.store_file(body=address, media_type="image/*"))
    csv = request_body(package, "storeFile", "text/*", "a,b")
    exchange.respond(raw_response(204), raw_response(204), raw_response(204), raw_response(204))
    record(lines, "store png", lambda: api.files.store_file(body=b"\x89PNG", media_type="image/png"))
    record(lines, "store pdf", lambda: api.files.store_file(body=b"%PDF", media_type="application/pdf"))
    record(lines, "store csv in UTF-16", lambda: api.files.store_file(body=csv, media_type="text/csv; charset=utf-16"))
    record(
        lines, "replace with octets", lambda: api.files.replace_file(body=b"x", media_type="application/octet-stream")
    )
    record(lines, "store video outside the ranges", lambda: api.files.store_file(body=b"x", media_type="video/mp4"))
    exchange.respond(raw_response(201, b"png", "image/png"), raw_response(200, b"jpeg", "image/jpeg"))
    for label in ("store file narrowed", "store file narrowed to another image"):
        record(
            lines,
            label,
            lambda: api.files.store_file(body=address, media_type="application/json", response_media_type="image/png"),
        )
    exchange.respond(raw_response(204))
    record(lines, "replace file without a body", api.files.replace_file)


def _range_responses(resource: Any, exchange: Exchange, lines: list[str], name: str, arguments: dict[str, Any]) -> None:
    """Select a concrete image through a range declaration in every synchronous view."""
    for view in (resource, resource.with_response):
        exchange.respond(raw_response(200, b"jpeg", "image/jpeg"))
        record(
            lines,
            f"range {name} {type(view).__name__}",
            lambda view=view: getattr(view, name)(**arguments, response_media_type="image/jpeg"),
        )
    exchange.respond(raw_response(200, b"jpeg", "image/jpeg"))
    record(
        lines,
        f"range {name} raw",
        lambda: getattr(resource.with_raw_response, name)(**arguments, response_media_type="image/jpeg").read(),
    )
    exchange.respond(raw_response(200, b"jpeg", "image/jpeg"))
    with getattr(resource.with_streaming_response, name)(**arguments, response_media_type="image/jpeg") as response:
        record(lines, f"range {name} streaming", response.read)
    record(
        lines,
        f"response wildcard rejected {name}",
        lambda: getattr(resource, name)(**arguments, response_media_type="image/*"),
    )


async def _async_range_responses(package: ModuleType, lines: list[str], case: str) -> None:
    """Select concrete image responses through each asyncio view using the existing HTTPS recipes."""
    exchange = Exchange(lines)
    async with package.AsyncClient(http_client=exchange.async_client(), http_client_ownership="owned") as api:
        if case == "pets":
            resource, name, arguments = api.pets.photos, "upload", {"pet_id": _pet(package, "uploadPhoto")}
        else:
            resource, name, arguments = api.files, "store_file", {"body": b"image", "media_type": "image/jpeg"}
        for view in (resource, resource.with_response):
            exchange.respond(raw_response(200, b"jpeg", "image/jpeg"))
            await arecord(
                lines,
                f"async range {name} {type(view).__name__}",
                lambda view=view: getattr(view, name)(**arguments, response_media_type="image/jpeg"),
            )

        async def raw() -> bytes:
            response = await getattr(resource.with_raw_response, name)(**arguments, response_media_type="image/jpeg")
            return await response.read()

        exchange.respond(raw_response(200, b"jpeg", "image/jpeg"))
        await arecord(lines, f"async range {name} raw", raw)
        exchange.respond(raw_response(200, b"jpeg", "image/jpeg"))
        async with getattr(resource.with_streaming_response, name)(
            **arguments, response_media_type="image/jpeg"
        ) as response:
            await arecord(lines, f"async range {name} streaming", response.read)
        await arecord(
            lines,
            f"async response wildcard rejected {name}",
            lambda: getattr(resource, name)(**arguments, response_media_type="image/*"),
        )


def _documents(package: ModuleType, api: Any, exchange: Exchange, lines: list[str], documents: ModuleType) -> None:
    for label, responder, call in (
        ("store", json_response(200, {"stored": True}), lambda: api.documents.store_document(body={"x": [1, 2]})),
        (
            "store text",
            raw_response(200, "caf\xe9".encode("latin-1"), "text/plain; charset=latin-1"),
            lambda: api.documents.store_document(body="note", media_type="text/plain; charset=utf-16"),
        ),
        (
            "store text of a charset named in capitals",
            raw_response(200, "caf\xe9".encode("latin-1"), "text/plain; CHARSET=latin-1"),
            lambda: api.documents.store_document(body="note", media_type="text/plain; charset=utf-16"),
        ),
        ("store moved", json_response(302, {"id": 1, "title": "t"}), lambda: api.documents.store_document(body=1)),
        ("store moved invalid", json_response(302, {"title": 5}), lambda: api.documents.store_document(body=1)),
        (
            "store failed",
            raw_response(500, b"down", "text/plain; charset=nope"),
            lambda: api.documents.store_document(body=1),
        ),
        ("store syntax", raw_response(200, b"\xff", "text/plain"), lambda: api.documents.store_document(body=1)),
        (
            "store narrowed",
            json_response(302, {"id": 1, "title": "t"}),
            lambda: api.documents.store_document(body=1, response_media_type="text/plain"),
        ),
    ):
        exchange.respond(responder)
        record(lines, label, call)
    record(lines, "store value", lambda: api.documents.store_document(body={1, 2}))
    read = argument(package, "readDocument", "path", "id", {"key": "a/b"})
    draft = raw_response(200, b'{"id":1,"title":"t"}', "application/vnd.api+json", **{"X-Draft": "id,1,title,h"})
    exchange.respond(draft, draft)
    record(lines, "read", lambda: api.documents.read_document(id=read, filter={"q": [1]}, x_mode="fast"))
    info = api.documents.with_raw_response.read_document(id=read, filter={"q": [1]}, x_mode="fast").info
    record(lines, "read header", lambda: documents.decode_read_document_header(info, name="X-Draft"))
    exchange.respond(raw_response(204), raw_response(204), raw_response(204))
    record(lines, "note", lambda: api.documents.store_note(body=[1], media_type="application/vnd.note+json"))
    record(lines, "note replace", api.documents.replace_note)
    record(lines, "note replace text", lambda: api.documents.replace_note(body="n", media_type="text/plain"))
    record(lines, "note replace value", lambda: api.documents.replace_note(body=5, media_type="text/plain"))
    del package


def querystring(package: ModuleType, lines: list[str]) -> None:
    """Send a whole query through one querystring parameter, which no query patch may add to."""
    _types, options = _modules(package, "types.default", "options")
    exchange = Exchange(lines)
    criteria = argument(
        package,
        "search",
        "querystring",
        "criteria",
        {
            "term": "a b",
            "page": 2,
        },
    )
    with exchange.client() as native_client_551, package.Client(http_client=native_client_551) as api:
        exchange.respond(json_response(200, ["a"]), json_response(200, []))
        record(lines, "search", lambda: api.default.search(criteria=criteria))
        record(lines, "search all", api.default.search)
        patched = options.RequestOptions(query=(("page", "3"),))
        record(lines, "search with a query patch", lambda: api.default.search(criteria=criteria, options=patched))


def servers(package: ModuleType, lines: list[str]) -> None:
    """Resolve relative servers against their base, default server variables, and later servers by position."""
    (options,) = _modules(package, "options")
    exchange = Exchange(lines)
    with exchange.client() as native_client_563, package.Client(http_client=native_client_563) as api:
        exchange.respond(raw_response(204), raw_response(204), raw_response(204))
        record(lines, "status", api.default.get_status)
        record(lines, "regional", api.default.get_regional)
        backup = options.RequestOptions(server=options.ServerSelection(index=1))
        record(lines, "regional backup", lambda: api.default.get_regional(options=backup))


def default_server(package: ModuleType, lines: list[str]) -> None:
    """Send the generated User-Agent of a distribution to its default base URL."""
    exchange = Exchange(lines)
    with exchange.client() as native_client_574, package.Client(http_client=native_client_574) as api:
        exchange.respond(raw_response(204))
        record(lines, "status", api.default.get_status)


_DOTS: Final = (".", "..", "...", ".a", "%2e", "")
_PATHS: Final = (
    *(("get_archive", {"name": value}) for value in (*_DOTS, "%2E%2E", "a.b", "a/b", "../a/b?c#d")),
    *(("get_file", {"name": value}) for value in (".", "")),
    *(
        ("get_pair", {"first": first, "second": second})
        for first, second in ((".", "."), ("", ".."), (".", "a"), ("", "."), ("..", "."))
    ),
    *(("get_reserved", {"name": value}) for value in (*_DOTS, "%2E.", "%2e%2f", "a/..")),
    *(("get_list", {"names": value}) for value in (["."], [".."], [".", "."], ["..", ""])),
    *(("get_label", {"name": value}) for value in _DOTS),
    *(("get_label_list", {"names": value}) for value in (["", ""], ["a", ""], ["."], [""])),
    *(("get_matrix", {"name": value}) for value in _DOTS),
    *(("get_matrix_list", {"names": value}) for value in ([".", ".."], [""])),
    *(("get_reserved_label", {"name": value}) for value in (*_DOTS, "%2E.")),
    *(("get_reserved_label_list", {"names": value}) for value in (["%2e"], ["%2e", "a"], ["", "%2E"])),
    *(
        ("get_dotted", {"first": first, "second": second})
        for first, second in (("", ""), ("a", ""), ("", "."), (".", ""), ("%2e", ""))
    ),
    *(("get_static", {"name": value}) for value in ("a", "..")),
)


def _arguments(package: ModuleType, method: str, values: dict[str, object]) -> dict[str, object]:
    first, *rest = method.split("_")
    return path_arguments(package, first + "".join(part.title() for part in rest), values)


def paths(package: ModuleType, lines: list[str]) -> None:
    """Keep each call on its operation: a path value making its segment `.` or `..` is refused before sending.

    URL normalization would remove such a segment, and `%2E` is the `.` it is equivalent to, so no encoding of these
    values reaches the operation; every other value, `...` and `.a` among them, is sent as data.
    """
    exchange = Exchange(lines)
    with exchange.client() as native_client_615, package.Client(http_client=native_client_615) as api:
        for method, values in _PATHS:
            call = partial(getattr(api.default, method), **_arguments(package, method, values))
            exchange.responders[:] = [raw_response(204)]
            record(lines, f"{method} {values}", call)
    run(lambda: _async_paths(package, lines))


async def _async_paths(package: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    api: Any
    async with exchange.async_client() as native_client_626, package.AsyncClient(http_client=native_client_626) as api:
        for method, values in _PATHS:
            call = partial(getattr(api.default, method), **_arguments(package, method, values))
            exchange.responders[:] = [raw_response(204)]
            await arecord(lines, f"async {method} {values}", call)


_PET: Final = json.dumps({"id": 3, "name": "fox"}).encode()
_ERROR: Final = json.dumps({"code": 7, "message": "boom"}).encode()
_BOMB: Final = gzip.compress(bytes(2 * 1024 * 1024), mtime=0)


def _coded(coding: str, content: bytes, status: int = 200) -> Callable[[Any], Any]:
    return chunked_response(status, content, len(content), "application/json", **{"content-encoding": coding})


def _corrupt(content: bytes) -> bytes:
    return content[:-8] + bytes([content[-8] ^ 1]) + content[-7:]


def _limit(call: Callable[[], object]) -> Callable[[], str]:
    """Report an expansion failure by its fixed facts, leaving out the byte counts the compressor decides."""

    def limited() -> str:
        try:
            call()
        except Exception as error:  # ruff: ignore[blind-except]
            over = error.observed > error.limit >= 1024 * 1024
            return f"{type(error).__name__} layer={error.layer} ratio={error.max_ratio} over={over}"
        return "decoded"

    return limited


def codings(package: ModuleType, lines: list[str]) -> None:
    """Remove gzip, deflate, and stacked codings exactly once, and refuse unknown, broken, and expanding ones."""
    exchange = Exchange(lines)
    pets = json.dumps([{"id": index, "name": "cat"} for index in range(4000)]).encode()
    (options,) = _modules(package, "options")
    with (
        exchange.client() as native_client_665,
        package.Client(
            http_client=native_client_665, options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
        ) as api,
    ):
        pet, trace = _pet(package, "getPet"), _trace(package)
        for label, responder in (
            ("gzip", _coded("gzip", gzip.compress(_PET, mtime=0))),
            ("x-gzip", _coded("X-Gzip", gzip.compress(_PET, mtime=0))),
            ("deflate", _coded("deflate", zlib.compress(_PET))),
            ("stacked", _coded("gzip, deflate", zlib.compress(gzip.compress(_PET, mtime=0)))),
            ("identity", _coded("identity", _PET)),
            ("members", _coded("gzip", gzip.compress(_PET[:5], mtime=0) + gzip.compress(_PET[5:], mtime=0))),
            ("layers", _coded("gzip, deflate, gzip", _PET)),
            ("unknown", _coded("gzip, br", _PET)),
            ("truncated", _coded("gzip", gzip.compress(_PET, mtime=0)[:-4])),
            ("corrupt", _coded("gzip", _corrupt(gzip.compress(_PET, mtime=0)))),
            ("deflate trailing", _coded("deflate", zlib.compress(_PET) + b"x")),
            ("gzip trailing", _coded("gzip", gzip.compress(_PET, mtime=0) + b"garbage")),
        ):
            exchange.respond(responder)
            record(lines, f"coding {label}", lambda: api.pets.get_pet(pet_id=pet))
        exchange.respond(_coded("gzip", _BOMB))
        record(lines, "coding bomb", _limit(lambda: api.pets.get_pet(pet_id=pet)))
        exchange.respond(injected(lambda _: httpx2.Response(200, json={"id": 3, "name": "fox"})))
        record(lines, "coding pre-read", lambda: api.pets.get_pet(pet_id=pet))
        exchange.respond(
            chunked_response(
                200,
                gzip.compress(pets, mtime=0),
                100,
                "application/json",
                **{"content-encoding": "gzip", "X-Rate": "1"},
            )
        )
        record(lines, "coding chunked", lambda: len(api.pets.list_pets(x_trace=trace).root))
        exchange.respond(
            *((_coded("gzip", gzip.compress(_ERROR, mtime=0), 500),) * 3),
            *((_coded("gzip", gzip.compress(_ERROR, mtime=0)[:-4], 500),) * 3),
            chunked_response(302, b"moved", 5, "text/plain", **{"content-encoding": "br"}),
            chunked_response(204, b"", 1, "text/plain", **{"content-encoding": "br"}),
        )
        record(lines, "coding error", lambda: api.pets.list_pets(x_trace=trace))
        record(lines, "coding error truncated", lambda: api.pets.list_pets(x_trace=trace))
        record(lines, "coding redirect", lambda: api.pets.list_pets(x_trace=trace))
        record(
            lines,
            "coding bodyless",
            lambda: api.pets.delete_pets_by_pet_id(pet_id=_pet(package, "DELETE /pets/{petId}")),
        )
    run(lambda: _async_codings(package, exchange, lines))


async def _async_codings(package: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    pet = _pet(package, "getPet")
    async with exchange.async_client() as native_client_714, package.AsyncClient(http_client=native_client_714) as api:
        exchange.respond(
            _coded("gzip, deflate", zlib.compress(gzip.compress(_PET, mtime=0))),
            _coded("identity", _PET),
            _coded("gzip", gzip.compress(_PET, mtime=0)[:-4]),
        )
        await arecord(lines, "async coding stacked", lambda: api.pets.get_pet(pet_id=pet))
        await arecord(lines, "async coding identity", lambda: api.pets.get_pet(pet_id=pet))
        await arecord(lines, "async coding truncated", lambda: api.pets.get_pet(pet_id=pet))


BACKENDS: Final = (
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    "typing.TypedDict",
    "msgspec.Struct",
)
ALL_BUT_MSGSPEC: Final = BACKENDS[:-1]
STRUCTURAL: Final = BACKENDS[2:]
SCENARIOS: Final[dict[str, tuple[str, tuple[str, ...], Callable[[ModuleType, list[str]], None]]]] = {
    "allowreserved-path-30": ("allowreserved-path-30", BACKENDS, reserved_paths),
    "allowreserved-path-31": ("allowreserved-path-31", BACKENDS, reserved_paths),
    "allowreserved-path-32": ("allowreserved-path-32", BACKENDS, reserved_paths),
    "no-success": ("no-success", BACKENDS, no_success),
    "no-success-unpack": ("no-success-unpack", BACKENDS, no_success),
    "json-decode-errors-sse": ("streams", ("pydantic_v2.BaseModel",), json_decode_errors),
    "json-decode-errors-ndjson": ("ndjson", ("pydantic_v2.BaseModel",), json_decode_errors),
    "pets": ("pets", ("pydantic_v2.BaseModel", "typing.TypedDict"), pets),
    "auth-errors": ("pets", ("pydantic_v2.BaseModel",), auth_errors),
    "auth-values": ("auth", BACKENDS, auth_values),
    "auth-challenges": ("auth", ("pydantic_v2.BaseModel",), auth_challenges),
    "auth-flows": ("auth", ("pydantic_v2.BaseModel",), auth_flows),
    "auth-options": ("auth", ("pydantic_v2.BaseModel",), auth_options),
    "body-digest": ("auth", ("pydantic_v2.BaseModel",), body_digest),
    "deadline-options": ("pets", ("pydantic_v2.BaseModel",), deadline_options),
    "deadline-cleanup": ("pets", ("pydantic_v2.BaseModel",), deadline_cleanup),
    "deadline-files": ("pets", ("pydantic_v2.BaseModel",), deadline_files),
    "deadline-races": ("pets", ("pydantic_v2.BaseModel",), deadline_races),
    "deadline-streams": ("pets", ("pydantic_v2.BaseModel",), deadline_streams),
    "retry-errors": ("pets", ("pydantic_v2.BaseModel",), retry_errors),
    "retry-calls": ("retries", ("pydantic_v2.BaseModel",), retry_calls),
    "retry-boundaries": ("retries", ("pydantic_v2.BaseModel",), retry_boundaries),
    "retry-options": ("retries", ("pydantic_v2.BaseModel",), retry_options),
    "retry-policy": ("retries", ("pydantic_v2.BaseModel",), retry_policy),
    "redirects": ("retries", ("pydantic_v2.BaseModel",), redirects),
    "redirect-head": ("pets", ("pydantic_v2.BaseModel",), head_redirects),
    "native-wire": ("retries", ("pydantic_v2.BaseModel",), native_wire),
    "native-faults": ("retries", ("pydantic_v2.BaseModel",), native_faults),
    "native-signing": ("auth", ("pydantic_v2.BaseModel",), native_signing),
    "oauth-client-credentials": ("auth", ("pydantic_v2.BaseModel",), oauth_client_credentials),
    "oauth-refresh": ("auth", ("pydantic_v2.BaseModel",), oauth_refresh),
    "body-replay": ("retries", ("pydantic_v2.BaseModel",), body_replay),
    "body-replay-faults": ("media", ("pydantic_v2.BaseModel",), body_replay_faults),
    "multipart-replay": ("media", ("pydantic_v2.BaseModel",), multipart_replay),
    "media": ("media", ("pydantic_v2.BaseModel", "dataclasses.dataclass"), media),
    "querystring": ("querystring", ("pydantic_v2.BaseModel",), querystring),
    "servers": ("servers", ("pydantic_v2.BaseModel",), servers),
    "default-server": ("default-server", ("pydantic_v2.BaseModel",), default_server),
    "paths": ("paths", ("pydantic_v2.BaseModel",), paths),
    "codings": ("pets", ("pydantic_v2.BaseModel",), codings),
    "raw": ("pets", ("pydantic_v2.BaseModel",), raw),
    "stream-lifetimes": ("pets", ("pydantic_v2.BaseModel",), stream_lifetimes),
    "bodies": ("pets", ("pydantic_v2.BaseModel",), bodies),
    "multipart": ("media", ("pydantic_v2.BaseModel", "typing.TypedDict"), multipart),
    "multipart-split": ("multipart-split", STRUCTURAL, split_parts),
    "headers": ("pets", ("pydantic_v2.BaseModel",), headers),
    "query": ("pets", ("pydantic_v2.BaseModel",), query),
    "signatures": ("pets", BACKENDS, signatures),
    "signatures-unpack": ("pets-unpack", BACKENDS, signatures),
    "keywords": ("keywords", ("pydantic_v2.BaseModel",), keywords),
    "hooks": ("pets", ("pydantic_v2.BaseModel",), hooks),
    "limiters": ("pets", ("pydantic_v2.BaseModel",), limiters),
    "limiter-faults": ("pets", ("pydantic_v2.BaseModel",), limiter_faults),
    "webhook-contracts": ("pets", BACKENDS, webhook_contracts),
    "webhook-errors": ("pets", ("pydantic_v2.BaseModel",), webhook_errors),
    "protocol-contracts": ("pets", BACKENDS, protocol_contracts),
    "pagination": ("pagination", ("pydantic_v2.BaseModel",), pagination),
    "pagination-backends": ("pagination", BACKENDS, pagination_backends),
    "pagination-sessions": ("pagination", ("pydantic_v2.BaseModel",), pagination_sessions),
    "pagination-auth": ("pagination", ("pydantic_v2.BaseModel",), pagination_auth),
    "pagination-limits": ("pagination-limits", ("pydantic_v2.BaseModel",), pagination_limits),
    "pagination-targets": ("pagination-targets", ("pydantic_v2.BaseModel",), pagination_targets),
    "pagination-paths": ("pagination-paths", ("pydantic_v2.BaseModel",), pagination_paths),
    "pagination-querystring": ("pagination-querystring", ("pydantic_v2.BaseModel",), pagination_querystring),
    "pagination-count-values": ("pagination-counts", ALL_BUT_MSGSPEC, pagination_count_values),
    "pagination-count-defaults": ("pagination-counts", BACKENDS, pagination_count_defaults),
    "pagination-counts": ("pagination-counts", ("pydantic_v2.BaseModel",), pagination_counts),
    "pagination-links": ("pagination-links", ("pydantic_v2.BaseModel",), pagination_links),
    "pagination-resume": ("pagination-resume", ("pydantic_v2.BaseModel",), pagination_resume),
    "polling": ("polling", ("pydantic_v2.BaseModel",), polling),
    "uploads": ("uploads", ("pydantic_v2.BaseModel",), uploads),
    "upload-compression-off": ("uploads", ("pydantic_v2.BaseModel",), upload_compression),
    "upload-compression": ("uploads-compression", ("pydantic_v2.BaseModel",), upload_compression),
    "uploads-oauth": ("uploads-oauth", ("pydantic_v2.BaseModel",), uploads_oauth),
    "polling-resume": ("polling", ("pydantic_v2.BaseModel",), polling_resume),
    "compression": ("compression", ("pydantic_v2.BaseModel",), compression),
    "streams": ("streams", ("pydantic_v2.BaseModel",), streams),
    "stream-events": ("streams", ("pydantic_v2.BaseModel",), event_stream_lifetimes),
    "stream-backends": ("streams", BACKENDS, stream_backends),
    "ndjson": ("ndjson", ("pydantic_v2.BaseModel",), ndjson),
    "ndjson-backends": ("ndjson", BACKENDS, ndjson_backends),
    "stream-resume": ("stream-resume", ("pydantic_v2.BaseModel",), stream_resume),
    "ndjson-split": ("ndjson-split", STRUCTURAL, ndjson_split),
    "sockets": ("sockets", ("pydantic_v2.BaseModel",), sockets),
    "socket-connectors": ("sockets", ("pydantic_v2.BaseModel",), socket_connectors),
    "socket-connector-outcomes": ("sockets", ("pydantic_v2.BaseModel",), socket_connector_outcomes),
    "protocol-errors": ("pets", ("pydantic_v2.BaseModel",), protocol_errors),
    "cache": ("caching", ("pydantic_v2.BaseModel",), caching),
    "cache-stores": ("caching", ("pydantic_v2.BaseModel",), cache_stores),
    "cache-backends": ("caching-backends", BACKENDS, cache_backends),
    "evolution": ("evolution", ("pydantic_v2.BaseModel", "pydantic_v2.dataclass", "msgspec.Struct"), evolution),
    "evolution-forbid": ("evolution-forbid", ("pydantic_v2.BaseModel", "msgspec.Struct"), evolution),
    "evolution-allow": ("evolution-allow", ("pydantic_v2.BaseModel",), evolution),
    "fields": ("fields", ("pydantic_v2.BaseModel", "pydantic_v2.dataclass", "msgspec.Struct"), fields),
    "fields-structural": ("fields-structural", ("dataclasses.dataclass", "typing.TypedDict"), fields),
    "fields-unpack": ("fields-unpack", ("pydantic_v2.BaseModel",), fields),
    "fields-optional-models": (
        "fields-optional-models",
        ("pydantic_v2.BaseModel", "dataclasses.dataclass", "msgspec.Struct"),
        optional_models,
    ),
    "webhook-verification": ("webhooks", ("pydantic_v2.BaseModel",), webhook_verification),
    "webhook-backends": ("webhooks", BACKENDS, webhook_backends),
    "webhook-public-keys": ("webhooks-public-keys", ("pydantic_v2.BaseModel",), webhook_public_keys),
    "webhook-adapters": ("webhooks-adapters", ("pydantic_v2.BaseModel",), webhook_adapters),
    "webhook-unsigned": ("webhooks-adapters", ("pydantic_v2.BaseModel",), webhook_unsigned),
    "webhook-mapped-backends": ("webhooks-adapters", BACKENDS, webhook_mapped_backends),
    "webhook-adapter-imports": ("webhooks-unsigned", ("pydantic_v2.BaseModel",), webhook_adapter_imports),
    "unions": ("unions", ("pydantic_v2.BaseModel", "pydantic_v2.dataclass"), unions),
    "unions-tagged": ("unions-tagged", ("msgspec.Struct",), unions),
    "unions-legacy": ("unions-legacy", ("pydantic_v2.BaseModel", "pydantic_v2.dataclass"), unions),
    "unions-split": ("unions-split", ("pydantic_v2.BaseModel", "pydantic_v2.dataclass"), split_unions),
}


def client_runtime_report(name: str, root: Path) -> str:
    """Generate one scenario's package for each backend, run its calls, and report every exchange and outcome."""
    case, backends, scenario = SCENARIOS[name]
    return "".join(generated(case, backend, root / backend.replace(".", "_"), scenario) for backend in backends)
