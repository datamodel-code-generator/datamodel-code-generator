"""Authenticate generated clients with constructor credentials, a native Auth, and the HTTP client's own Auth."""

from __future__ import annotations

import importlib
import inspect
from typing import TYPE_CHECKING, Final

import httpx2

from tests.data.python.client_runtime import Exchange, arecord, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from types import ModuleType

_OK: Final = raw_response(200, b"ok", "application/octet-stream")
_SECRETS: Final = ("token-secret", "key-secret", "pass word", "q secret", "cookie-secret", "vault-secret")


class _Marked(httpx2.Auth):
    """A native Auth marking the requests it authenticates."""

    def __init__(self, value: str) -> None:
        self.value = value

    def auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, None]:
        request.headers["X-Native"] = self.value
        yield request


class _Rotating:
    """A credential callable returning a new value each time it is called."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return f"{self.prefix}-{self.calls}"


def _failing() -> str:
    msg = "vault-secret unavailable"
    raise OSError(msg)


def _leaks(error: object) -> list[str]:
    text = f"{error!r} {error}"
    return [secret for secret in _SECRETS if secret in text]


def _failure(lines: list[str], label: str, call: Callable[[], object]) -> None:
    try:
        call()
    except Exception as error:  # noqa: BLE001
        cause = getattr(error, "cause", None)
        lines.append(
            f"  {label} ! {type(error).__name__} reason={getattr(error, 'reason', None)}"
            f" field_path={getattr(error, 'field_path', None)} cause={type(cause).__name__} leaked={_leaks(error)}"
        )
    else:
        lines.append(f"  {label} = sent")


def _credentials(package: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    rotating = _Rotating("key-secret")
    lines.append(f"  arguments {tuple(inspect.signature(package.Client).parameters)}")
    with (
        exchange.client() as http,
        package.Client(
            http_client=http,
            bearer="token-secret",
            header_key=rotating,
            basic=("user", "pass word"),
            query_key="q secret&",
            cookie_key=lambda: "cookie-secret",
        ) as api,
    ):
        exchange.respond(*(_OK for _ in range(15)))
        record(lines, "bearer", api.auth.bearer)
        record(lines, "inherited", api.auth.inherited_auth)
        record(lines, "header key", api.auth.api_key_header)
        record(lines, "header key again", api.auth.api_key_header)
        record(lines, "query key", api.auth.api_key_query)
        record(lines, "cookie key", api.auth.api_key_cookie)
        record(
            lines,
            "cookie beside parameters",
            lambda: api.auth.cookie_parameters(theme="dark", page=1, x_trace="trace"),
        )
        record(lines, "basic", api.auth.basic)
        record(lines, "and", api.auth.and_auth)
        record(lines, "or picks its first alternative", api.auth.or_auth)
        record(lines, "basic or bearer", api.auth.authorization_or)
        record(lines, "anonymous", api.auth.anonymous)
        record(lines, "empty security", api.auth.empty_security)
        record(lines, "anonymous first", api.auth.optional_auth)
        record(lines, "token first", api.auth.optional_token_first)
        lines.append(f"  callable calls={rotating.calls}")
        exchange.respond(_OK, _OK)
        record(lines, "raw request", lambda: api.request_raw("GET", "https://api.example.com/bearer").info.status_code)
        patched = api.with_options(default_headers={"X-API-Key": "patched", "Authorization": "Bearer patched"})
        record(lines, "credentials replace patches", patched.auth.and_auth)


def _selection(package: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    with exchange.client() as http, package.Client(http_client=http, bearer="token-secret") as api:
        exchange.respond(_OK, _OK)
        record(lines, "or falls to bearer", api.auth.or_auth)
        record(lines, "alias needs its own credential", api.auth.alias_auth)
        record(lines, "and missing key", api.auth.and_auth)
        record(lines, "optional sends the credential", api.auth.optional_auth)
    with exchange.client() as http, package.Client(http_client=http) as api:
        exchange.respond(_OK, _OK)
        record(lines, "required without credentials", api.auth.bearer)
        record(lines, "anonymous without credentials", api.auth.anonymous)
        record(lines, "optional without credentials", api.auth.optional_auth)


def _native(package: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    options = importlib.import_module(f"{package.__name__}.options")
    with exchange.client() as http, package.Client(http_client=http, bearer="token-secret") as api:
        exchange.respond(_OK, _OK, _OK, _OK, _OK)
        native = api.with_options(auth=_Marked("view"))
        record(lines, "view auth replaces credentials", native.auth.bearer)
        record(lines, "view auth none", api.with_options(auth=None).auth.optional_auth)
        record(
            lines,
            "call auth over view auth",
            lambda: native.auth.bearer(options=options.RequestOptions(auth=_Marked("call over view"))),
        )
        record(lines, "call auth", lambda: api.auth.and_auth(options=options.RequestOptions(auth=_Marked("call"))))
        record(
            lines, "auth none on anonymous", lambda: api.auth.optional_auth(options=options.RequestOptions(auth=None))
        )
        record(lines, "auth none on required", lambda: api.auth.bearer(options=options.RequestOptions(auth=None)))
    with exchange.client(auth=_Marked("client")) as http, package.Client(http_client=http) as api:
        exchange.respond(_OK, _OK)
        record(lines, "client auth on required", api.auth.bearer)
        record(lines, "client auth on anonymous", api.auth.anonymous)
    with exchange.client(auth=_Marked("client")) as http, package.Client(http_client=http, auth=None) as api:
        exchange.respond(_OK)
        record(lines, "root auth none over client auth", api.auth.optional_auth)
    with exchange.client() as http, package.Client(http_client=http, auth=_Marked("root")) as api:
        exchange.respond(_OK, _OK)
        record(lines, "root auth", api.auth.bearer)
        record(lines, "view inherits root auth", api.with_options(max_retries=0).auth.bearer)


def _refusals(package: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    with exchange.client(auth=_Marked("client")) as http:
        _failure(
            lines, "credentials beside client auth", lambda: package.Client(http_client=http, bearer="token-secret")
        )
    _failure(lines, "credentials beside auth none", lambda: package.Client(auth=None, bearer="token-secret"))
    _failure(
        lines, "credentials beside native auth", lambda: package.Client(auth=_Marked("root"), bearer="token-secret")
    )
    _failure(lines, "credential of no credential type", lambda: package.Client(bearer=b"token-secret"))
    with exchange.client() as http:
        for label, credentials, call in (
            ("callable failure", {"bearer": _failing}, "bearer"),
            ("callable of another type", {"bearer": lambda: 7}, "bearer"),
            ("basic of another shape", {"basic": "user:pass word"}, "basic"),
            ("basic of a pair of another size", {"basic": ("user", "pass", "word")}, "basic"),
            ("header line break", {"header_key": "key-secret\r\nX-Injected: 1"}, "api_key_header"),
            ("cookie separator", {"cookie_key": "cookie-secret; admin=1"}, "api_key_cookie"),
            ("non-ascii header", {"header_key": "key-secreté"}, "api_key_header"),
        ):
            with package.Client(http_client=http, **credentials) as api:
                _failure(lines, label, getattr(api.auth, call))


async def _async_values(package: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    options = importlib.import_module(f"{package.__name__}.options")
    async with (
        exchange.async_client() as http,
        package.AsyncClient(http_client=http, bearer=_Rotating("token-secret"), query_key="q secret&") as api,
    ):
        exchange.respond(_OK, _OK, _OK, _OK)
        await arecord(lines, "async bearer", api.auth.bearer)
        await arecord(lines, "async bearer again", api.auth.bearer)
        await arecord(lines, "async query key", api.auth.api_key_query)
        await arecord(
            lines, "async call auth", lambda: api.auth.bearer(options=options.RequestOptions(auth=_Marked("async")))
        )
        await arecord(lines, "async required without credentials", api.auth.basic)


def auth_values(package: ModuleType, lines: list[str]) -> None:
    """Place every declared scheme's credential, select alternatives, and let a native Auth replace them."""
    exchange = Exchange(lines)
    for title, part in (
        ("credentials", _credentials),
        ("selection", _selection),
        ("native auth", _native),
        ("refusals", _refusals),
    ):
        lines.append(title)
        part(package, exchange, lines)
    lines.append("async")
    run(lambda: _async_values(package, exchange, lines))
