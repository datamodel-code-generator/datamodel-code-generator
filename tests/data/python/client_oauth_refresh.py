"""Refresh token sets through generated clients: on rejection and on expiry, reporting each refreshed set."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_oauth import failure_line, issued
from tests.data.python.client_runtime import Exchange, arecord, json_response, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_OK: Final = raw_response(200, b"ok", "application/octet-stream")
_INVALID: Final = raw_response(401, b"", None, **{"www-authenticate": 'Bearer error="invalid_token"'})
_EPOCH: Final = 1_700_000_000


def _outcome(lines: list[str], label: str, call: Callable[[], object]) -> None:
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {failure_line(error)}")
    else:
        lines.append(f"  {label} = {result!r}")


def _set(tokens: Any) -> str:
    expires = None if tokens.expires_at is None else round(tokens.expires_at.timestamp() - _EPOCH)
    return f"{tokens.access_token}/{tokens.refresh_token} expires={expires}"


class _Clock:
    """A clock a scenario moves forward, for the provider's token expiry."""

    def __init__(self, options: ModuleType) -> None:
        self.now = 0.0
        self.clock = options.Clock(monotonic=lambda: self.now, time=lambda: _EPOCH + self.now)


def _expiring(auth: ModuleType, access: str, refresh: str | None, seconds: float) -> Any:
    return auth.TokenSet(access, refresh, datetime.fromtimestamp(_EPOCH + seconds, timezone.utc))


def _rejection(package: ModuleType, auth: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    clock = _Clock(importlib.import_module(f"{package.__name__}.options"))
    saved: list[str] = []
    provider = auth.OauthRefreshToken(
        _expiring(auth, "access-0", "refresh-0", 3600),
        client_id="pets-app",
        on_token_refreshed=lambda tokens: saved.append(_set(tokens)),
        clock=clock.clock,
    )
    lines.append(f"  declared refresh url {auth.OauthRefreshToken.token_url}")
    with exchange.client() as http, package.Client(http_client=http, oauth=provider) as api:
        exchange.respond(_OK)
        record(lines, "initial access token", api.auth.oauth_read)
        exchange.respond(_INVALID, issued("access-1", refresh_token="refresh-1"), _OK)
        record(lines, "rejection refreshes", api.auth.oauth_read)
        exchange.respond(_INVALID, issued("access-2", expires_in=None), _OK)
        record(lines, "refresh without a new refresh token", api.auth.oauth_read)
        exchange.respond(_INVALID, json_response(400, {"error": "invalid_grant"}))
        _outcome(lines, "invalid grant", api.auth.oauth_read)
    lines.append(f"  saved {saved}")


def _expiry(package: ModuleType, auth: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    clock = _Clock(importlib.import_module(f"{package.__name__}.options"))
    saved: list[str] = []
    provider = auth.RefreshToken(
        _expiring(auth, "access-0", "refresh-0", 100),
        client_id="pets-app",
        client_secret="se:cr et",
        client_auth_method="client_secret_basic",
        token_url="https://id.example.com/token",
        on_token_refreshed=lambda tokens: saved.append(_set(tokens)),
        clock=clock.clock,
    )
    with exchange.client() as http, package.Client(http_client=http, openid=provider) as api:
        exchange.respond(_OK)
        record(lines, "token before its renewal", api.auth.openid_read)
        clock.now = 95.0
        exchange.respond(issued("access-1", expires_in=100, refresh_token="refresh-1"), _OK)
        record(lines, "due token refreshes first", api.auth.openid_read)
    lines.append(f"  saved {saved}")
    unrenewable = auth.RefreshToken(
        _expiring(auth, "access-0", None, 300),
        client_id="pets-app",
        token_url="https://id.example.com/token",
        clock=clock.clock,
    )
    with exchange.client() as http, package.Client(http_client=http, openid=unrenewable) as api:
        clock.now = 150.0
        exchange.respond(_OK)
        record(lines, "unrenewable token until its expiry", api.auth.openid_read)
        clock.now = 301.0
        _outcome(lines, "unrenewable token after its expiry", api.auth.openid_read)
    with exchange.client() as http:
        forever = auth.RefreshToken(
            auth.TokenSet("access-0"), client_id="pets-app", token_url="https://id.example.com/token"
        )
        with package.Client(http_client=http, openid=forever) as api:
            exchange.respond(_INVALID)
            _outcome(lines, "rejected token without a refresh token", api.auth.openid_read)


def _callbacks(package: ModuleType, auth: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    def failed(tokens: object) -> None:
        msg = "store unavailable"
        raise OSError(msg)

    async def pending(tokens: object) -> None:
        del tokens

    clock = _Clock(importlib.import_module(f"{package.__name__}.options"))
    for label, callback in (("failing callback", failed), ("coroutine callback in a synchronous call", pending)):
        provider = auth.OauthRefreshToken(
            _expiring(auth, "access-0", "refresh-0", 3600),
            client_id="pets-app",
            on_token_refreshed=callback,
            clock=clock.clock,
        )
        with exchange.client() as http, package.Client(http_client=http, oauth=provider) as api:
            exchange.respond(_INVALID, issued("access-1"))
            _outcome(lines, label, api.auth.oauth_read)
            exchange.respond(_OK)
            record(lines, f"{label} keeps the refreshed token", api.auth.oauth_read)
    for label, build in (
        ("naive expiry", lambda: auth.TokenSet("access-0", "refresh-0", datetime(2026, 1, 1))),  # noqa: DTZ001
        ("empty access token", lambda: auth.TokenSet("")),
        ("empty refresh token", lambda: auth.TokenSet("access-0", "")),
        ("token set of another type", lambda: auth.OauthRefreshToken("access-0", client_id="pets-app")),
        (
            "callback of another type",
            lambda: auth.OauthRefreshToken(auth.TokenSet("a"), client_id="c", on_token_refreshed=1),
        ),
        (
            "secret without a method",
            lambda: auth.OauthRefreshToken(auth.TokenSet("a"), client_id="c", client_secret="s"),
        ),
        (
            "method without a secret",
            lambda: auth.OauthRefreshToken(auth.TokenSet("a"), client_id="c", client_auth_method="client_secret_post"),
        ),
        ("empty client id", lambda: auth.OauthRefreshToken(auth.TokenSet("a"), client_id="")),
        ("unknown method", lambda: auth.OauthRefreshToken(auth.TokenSet("a"), client_id="c", client_auth_method="tls")),
        ("clock of another type", lambda: auth.OauthRefreshToken(auth.TokenSet("a"), client_id="c", clock=object())),
    ):
        _outcome(lines, label, lambda build=build: type(build()).__name__)
    lines.append(f"  token set repr {auth.TokenSet('access-x', 'refresh-x')!r}")


async def _async_refresh(package: ModuleType, auth: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    saved: list[str] = []

    async def save(tokens: Any) -> None:
        saved.append(_set(tokens))

    clock = _Clock(importlib.import_module(f"{package.__name__}.options"))
    provider = auth.OauthRefreshToken(
        _expiring(auth, "access-0", "refresh-0", 3600), client_id="pets-app", on_token_refreshed=save, clock=clock.clock
    )
    async with exchange.async_client() as http, package.AsyncClient(http_client=http, oauth=provider) as api:
        exchange.respond(_INVALID, issued("access-1", refresh_token="refresh-1"), _OK)
        await arecord(lines, "async rejection refreshes", api.auth.oauth_read)
        exchange.respond(_INVALID, json_response(400, {"error": "invalid_grant"}))
        try:
            await api.auth.oauth_read()
        except Exception as error:  # noqa: BLE001
            lines.append(f"  async invalid grant ! {failure_line(error)}")
    lines.append(f"  async saved {saved}")

    async def failed(tokens: Any) -> None:
        msg = "store unavailable"
        raise OSError(msg)

    provider = auth.OauthRefreshToken(
        _expiring(auth, "access-0", "refresh-0", 3600),
        client_id="pets-app",
        on_token_refreshed=failed,
        clock=clock.clock,
    )
    async with exchange.async_client() as http, package.AsyncClient(http_client=http, oauth=provider) as api:
        exchange.respond(_INVALID, issued("access-1"))
        try:
            await api.auth.oauth_read()
        except Exception as error:  # noqa: BLE001
            lines.append(f"  async failing callback ! {failure_line(error)}")


def oauth_refresh(package: ModuleType, lines: list[str]) -> None:
    """Refresh a token set when a resource rejects it or it is due, and refuse invalid token sets and grants."""
    auth = importlib.import_module(f"{package.__name__}.auth")
    exchange = Exchange(lines)
    for title, part in (("rejection", _rejection), ("expiry", _expiry), ("callbacks", _callbacks)):
        lines.append(title)
        part(package, auth, exchange, lines)
    lines.append("async")
    run(lambda: _async_refresh(package, auth, lines))
