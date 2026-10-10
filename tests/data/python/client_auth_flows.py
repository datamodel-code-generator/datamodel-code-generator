"""Send authenticated generated calls across redirects and sign them with a caller's own HTTPX2 Auth."""

from __future__ import annotations

import hashlib
import hmac
import importlib
import io
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_runtime import Exchange, arecord, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Generator, Iterator
    from types import ModuleType

_ORIGIN: Final = "https://api.example.com"
_OTHER: Final = "https://other.example.com"
_OK: Final = raw_response(200, b"ok", "application/octet-stream")
_REJECTED: Final = raw_response(401, b"", None, **{"www-authenticate": 'Bearer error="invalid_token"'})


class _CallerKey(httpx2.Auth):
    """A caller's own native Auth on the HTTP client, which places its key on the request it authenticates."""

    def auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, None]:
        request.headers["X-Caller-Key"] = "caller-secret"
        yield request


class _Signer(httpx2.Auth):
    """A caller's body signer: an HMAC of the method, the path, and the body it reads natively."""

    requires_request_body = True

    def __init__(self) -> None:
        self.signed: list[str] = []

    def _sign(self, request: httpx2.Request) -> None:
        message = b"\n".join((request.method.encode(), request.url.raw_path, request.content))
        digest = hmac.digest(b"signing-key", message, hashlib.sha256).hex()[:16]
        self.signed.append(f"{request.headers.get('content-length') or 'chunked'}:{digest}")
        request.headers["X-Signature"] = digest

    def sync_auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, None]:
        request.read()
        self._sign(request)
        yield request

    async def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        await request.aread()
        self._sign(request)
        yield request


def _redirect(status: int, location: str) -> Callable[[httpx2.Request], httpx2.Response]:
    return raw_response(status, b"", None, location=location)


def _rows() -> tuple[tuple[str, dict[str, Any], dict[str, Any], str, int, str], ...]:
    """Return the redirect rows: label, HTTP client settings, client arguments, operation, status, and Location.

    The HTTP client follows redirects unless a row says otherwise; a request carrying a key at a declared scheme
    position other than Authorization is never redirected, whatever the setting.
    """
    follows = {"follow_redirects": True}
    key = {"header_key": "key-secret"}
    return (
        ("header key not redirected", follows, key, "api_key_header", 302, f"{_OTHER}/api-key/header"),
        ("query key not redirected", follows, {"query_key": "key-secret"}, "api_key_query", 307, f"{_OTHER}/q"),
        ("cookie key not redirected", follows, {"cookie_key": "key-secret"}, "api_key_cookie", 308, f"{_OTHER}/c"),
        (
            "patched scheme header not redirected",
            follows,
            {"default_headers": {"X-API-Key": "patched-secret"}},
            "anonymous",
            302,
            f"{_OTHER}/anonymous",
        ),
        ("header key at its own origin not redirected", follows, key, "api_key_header", 302, f"{_ORIGIN}/moved"),
        (
            "SDK choice cannot redirect a header key",
            follows,
            {**key, "follow_redirects": True},
            "api_key_header",
            307,
            f"{_OTHER}/api-key/header",
        ),
        ("bearer redirected without Authorization", follows, {"bearer": "token-secret"}, "bearer", 302, f"{_OTHER}/b"),
        ("bearer redirected within its origin", follows, {"bearer": "token-secret"}, "bearer", 307, f"{_ORIGIN}/b2"),
        ("plain request inherits the client's redirects", follows, {}, "anonymous", 303, f"{_OTHER}/anonymous"),
        ("plain request on a client that follows none", {}, {}, "anonymous", 302, f"{_OTHER}/anonymous"),
        (
            "plain request the SDK redirects",
            {},
            {"follow_redirects": True},
            "anonymous",
            307,
            f"{_OTHER}/anonymous",
        ),
        ("caller's own Auth applied and redirected", {**follows, "auth": _CallerKey()}, {}, "anonymous", 302, _OTHER),
    )


def _returned(response: Any) -> tuple[object, ...]:
    info = response.info
    return ("returned", info.status_code, info.headers.get("location"))


def _redirects(package: ModuleType, lines: list[str]) -> None:
    """Send keyed, bearer, and plain calls to a redirect through a following HTTP client."""
    for label, native, arguments, method, status, location in _rows():
        exchange = Exchange(lines)
        exchange.respond(_redirect(status, location), _OK)
        with exchange.client(**native) as http, package.Client(http_client=http, **arguments) as api:
            record(
                lines,
                f"{label} raw",
                lambda api=api, method=method: _returned(getattr(api.auth.with_raw_response, method)()),
            )
        lines.append(f"    unused={len(exchange.responders)}")
    exchange = Exchange(lines)
    exchange.respond(_redirect(302, f"{_OTHER}/bearer"), _REJECTED)
    with exchange.client(follow_redirects=True) as http, package.Client(http_client=http, bearer="token-secret") as api:
        record(lines, "bearer rejected by another origin", api.auth.bearer)


async def _aredirects(package: ModuleType, lines: list[str]) -> None:
    """Send the redirect rows with asyncio."""
    for label, native, arguments, method, status, location in _rows():
        exchange = Exchange(lines)
        exchange.respond(_redirect(status, location), _OK)
        async with exchange.async_client(**native) as http, package.AsyncClient(http_client=http, **arguments) as api:
            response = await getattr(api.auth.with_raw_response, method)()
            lines.append(f"  async {label} = {_returned(response)}")
        lines.append(f"    unused={len(exchange.responders)}")


def _chunks() -> Iterator[bytes]:
    yield from (b"chunk-", b"body")


async def _achunks() -> AsyncIterator[bytes]:
    for chunk in (b"chunk-", b"body"):
        yield chunk


def _signing(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Sign bytes, file, and chunked bodies with a caller's Auth, again on every retry."""
    signer = _Signer()
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http, bearer="token-secret") as api:
        exchange.respond(_OK, _OK, _OK, raw_response(503, b"", None), _OK, _OK)
        view = api.with_options(auth=signer)
        record(lines, "signed bytes", lambda: view.auth.signed_body(body=b"payload"))
        record(lines, "signed file", lambda: view.auth.signed_body(body=io.BytesIO(b"file-body")))
        record(lines, "signed chunks", lambda: view.auth.signed_body(body=_chunks()))
        retried = options.RequestOptions(auth=signer, retry=options.RetryOptions(statuses={503}, initial_delay=0))
        record(lines, "signed retry", lambda: api.auth.signed_body(body=b"payload", options=retried))
        record(lines, "signature replaces the credentials", lambda: view.auth.bearer())
    lines.append(f"  signatures {signer.signed}")

    async def asigned() -> None:
        async with exchange.async_client() as http, package.AsyncClient(http_client=http) as api:
            exchange.respond(_OK, _OK)
            view = api.with_options(auth=signer)
            await arecord(lines, "async signed bytes", lambda: view.auth.signed_body(body=b"payload"))
            await arecord(lines, "async signed chunks", lambda: view.auth.signed_body(body=_achunks()))

    run(asigned)
    lines.append(f"  async signatures {signer.signed[-2:]}")


def auth_flows(package: ModuleType, lines: list[str]) -> None:
    """Report what authenticated calls send across redirects, and what a caller's signing Auth signs."""
    options = importlib.import_module(f"{package.__name__}.options")
    lines.append("redirects")
    _redirects(package, lines)
    run(lambda: _aredirects(package, lines))
    lines.append("signing")
    _signing(package, options, lines)
