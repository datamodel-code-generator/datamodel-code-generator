"""Observe challenge parsing and version-aware recovery through generated public clients."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Final

import httpx2

from tests.data.python.client_runtime import Exchange, arecord, raw_response, record, run

if TYPE_CHECKING:
    from types import ModuleType

_CHALLENGES: Final = (
    ("quoted", ('Bearer error="invalid_token"',)),
    ("token", ("Bearer error=invalid_token",)),
    ("mixed case", ('bEaReR ErRoR="invalid_token"',)),
    ("multiple fields", ('Basic realm="ordinary"', 'Bearer error="invalid_token"')),
    ("quoted comma", ('Basic realm="one,two", Bearer error="invalid_token"',)),
    ("escaped quote", ('Basic realm="one\\\",two", Bearer error="invalid_token"',)),
    ("escaped error", ('Bearer error="invalid\\_token"',)),
    ("token68", ('Basic YTpi==, Bearer error="invalid_token"',)),
    ("bare scheme", ('Basic, Bearer error="invalid_token"',)),
    ("empty members", (', ,Bearer realm="x",,error="invalid_token",,',)),
    ("wrong error case", ('Bearer error="INVALID_TOKEN"',)),
    ("quoted padding", ('Bearer error=" invalid_token "',)),
    ("description only", ('Bearer error_description="invalid_token"',)),
    ("utf-8 description", ('Bearer error="invalid_token", error_description="期限切れ"'.encode(),)),
    ("scope", ('Bearer error="insufficient_scope"',)),
    ("scope veto", ('Bearer error="invalid_token", Bearer error="insufficient_scope"',)),
    ("scope field veto", ('Bearer error="invalid_token"', 'Bearer error="insufficient_scope"')),
    ("duplicate parameter", ('Bearer error="invalid_token", ERROR="invalid_token"',)),
    ("unclosed quote", ('Basic realm="prefix, Bearer error=invalid_token',)),
    ("unclosed escape", ('Bearer error="invalid_token\\',)),
    ("missing scheme", ('error="invalid_token"',)),
    ("invalid scheme", ('Bad:scheme, Bearer error="invalid_token"',)),
    ("invalid parameter", ('Bearer error="invalid_token" junk',)),
    ("token68 continuation", ('Basic YTpi==, error="invalid_token"',)),
    ("independent valid field", ('Bearer realm="unclosed', 'Bearer error="invalid_token"')),
    ("empty field", ("",)),
    ("absent", ()),
)


class _RefreshState:
    def __init__(self, auth: ModuleType) -> None:
        self.first = auth.BearerCredential(auth.AccessToken("first-material"), auth.TokenVersion())
        self.second = auth.BearerCredential(auth.AccessToken("second-material"), auth.TokenVersion())
        self.current = self.first
        self.calls: list[tuple[object, ...]] = []
        self.failure: BaseException | None = None
        self.failed_callback = ""

    def get_value(self, context: object) -> object:
        self.calls.append(("get", getattr(context, "scheme", None), getattr(context, "required_scopes", None)))
        if self.failed_callback == "get" and self.failure is not None:
            raise self.failure
        return self.current

    def invalidate_value(self, version: object) -> None:
        self.calls.append(("invalidate", version is self.first.version, version is self.current.version))
        if self.failed_callback == "invalidate" and self.failure is not None:
            raise self.failure

    def refresh_value(self, context: object) -> object:
        self.calls.append(("refresh", getattr(context, "scheme", None)))
        if self.failed_callback == "refresh" and self.failure is not None:
            raise self.failure
        self.current = self.second
        return self.current


class _Refreshing(_RefreshState):
    def get(self, context: object) -> object:
        return self.get_value(context)

    def invalidate(self, version: object) -> None:
        self.invalidate_value(version)

    def refresh(self, context: object) -> object:
        return self.refresh_value(context)


class _AsyncRefreshing(_RefreshState):
    async def get(self, context: object) -> object:
        return self.get_value(context)

    async def invalidate(self, version: object) -> None:
        self.invalidate_value(version)

    async def refresh(self, context: object) -> object:
        return self.refresh_value(context)


async def _async_challenges(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    async with exchange.async_client() as native:
        for label, values in _CHALLENGES:
            provider = _AsyncRefreshing(auth)
            exchange.responders.clear()
            fields = tuple(("WWW-Authenticate", value) for value in values)
            exchange.respond(
                lambda _, fields=fields: httpx2.Response(
                    401, headers=(("Content-Type", "application/octet-stream"), *fields), content=b"rejected"
                ),
                raw_response(200, b"accepted", "application/octet-stream"),
            )
            async with package.AsyncClient(
                http_client=native,
                options=options.ClientOptions(
                    auth=auth.AuthConfig({"bearer": provider}),
                    retry=options.RetryOptions(max_retries=1, initial_delay=0),
                ),
            ) as api:
                await arecord(lines, f"async challenge {label}", api.auth.with_response.bearer)
            lines.append(f"    callbacks={provider.calls!r} unused={len(exchange.responders)}")


def auth_challenges(package: ModuleType, lines: list[str]) -> None:
    """Compare independent challenge grammar oracles against actual 401 recovery."""
    auth = importlib.import_module(f"{package.__name__}.auth")
    options = importlib.import_module(f"{package.__name__}.options")
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    exchange = Exchange(lines)
    with exchange.client() as native:
        for label, values in _CHALLENGES:
            provider = _Refreshing(auth)
            exchange.responders.clear()
            fields = tuple(("WWW-Authenticate", value) for value in values)
            exchange.respond(
                lambda _, fields=fields: httpx2.Response(
                    401, headers=(("Content-Type", "application/octet-stream"), *fields), content=b"rejected"
                ),
                raw_response(200, b"accepted", "application/octet-stream"),
            )
            with package.Client(
                http_client=native,
                options=options.ClientOptions(
                    auth=auth.AuthConfig({"bearer": provider}),
                    retry=options.RetryOptions(max_retries=1, initial_delay=0),
                ),
            ) as api:
                record(lines, f"challenge {label}", api.auth.with_response.bearer)
            lines.append(f"    callbacks={provider.calls!r} unused={len(exchange.responders)}")
        for label, method, status, fields, request, body in (
            ("challenge-less permitted", "challenge_less", 401, {}, options.RequestOptions(), options.UNSET),
            (
                "challenge-less empty forbidden", "challenge_less", 401, {"WWW-Authenticate": ""},
                options.RequestOptions(), options.UNSET,
            ),
            ("403 no recovery", "bearer", 403, {}, options.RequestOptions(), options.UNSET),
            ("407 no recovery", "bearer", 407, {}, options.RequestOptions(), options.UNSET),
            (
                "disabled before unsafe", "unsafe_auth", 401, {},
                options.RequestOptions(retry=options.RetryOptions(max_retries=0)), b"request",
            ),
            ("unsafe no recovery", "unsafe_auth", 401, {}, options.RequestOptions(), b"request"),
            (
                "body before safety", "idempotent_auth", 401, {}, options.RequestOptions(),
                bodies.StreamBody(iter((b"request",))),
            ),
            (
                "never before disabled", "never_auth", 401, {},
                options.RequestOptions(retry=options.RetryOptions(max_retries=0)), options.UNSET,
            ),
            (
                "server false before disabled", "vendor_auth", 401, {"X-Retry-Permitted": "false"},
                options.RequestOptions(retry=options.RetryOptions(max_retries=0)), options.UNSET,
            ),
            ("network unavailable", "bearer", 401, {}, options.RequestOptions(max_network_sends=1), options.UNSET),
            ("server delay capped", "bearer", 401, {"Retry-After": "61"}, options.RequestOptions(), options.UNSET),
        ):
            provider = _Refreshing(auth)
            exchange.responders.clear()
            headers = {"WWW-Authenticate": 'Bearer error="invalid_token"', **fields}
            if label == "challenge-less permitted":
                headers.clear()
            exchange.respond(
                raw_response(status, b"rejected", "application/octet-stream", **headers),
                raw_response(200, b"accepted", "application/octet-stream"),
            )
            with package.Client(
                http_client=native,
                options=options.ClientOptions(
                    auth=auth.AuthConfig({"bearer": provider}), retry=options.RetryOptions(initial_delay=0),
                ),
            ) as api:
                arguments = {} if body is options.UNSET else {"body": body}
                record(
                    lines, f"auth gate {label}",
                    lambda method=method, request=request, arguments=arguments: getattr(api.auth.with_response, method)(
                        options=request, **arguments
                    ),
                )
            lines.append(f"    callbacks={provider.calls!r} unused={len(exchange.responders)}")
        provider = _Refreshing(auth)

        def published(_: httpx2.Request) -> httpx2.Response:
            provider.current = provider.second
            return httpx2.Response(
                401,
                headers={"Content-Type": "application/octet-stream", "WWW-Authenticate": 'Bearer error="invalid_token"'},
                content=b"rejected old version",
            )

        exchange.responders.clear()
        exchange.respond(published, raw_response(200, b"adopted", "application/octet-stream"))
        with package.Client(
            http_client=native,
            options=options.ClientOptions(
                auth=auth.AuthConfig({"bearer": provider}), retry=options.RetryOptions(initial_delay=0),
            ),
        ) as api:
            record(lines, "newer publication skips refresh", api.auth.with_response.bearer)
        lines.append(f"    callbacks={provider.calls!r} unused={len(exchange.responders)}")
    run(lambda: _async_challenges(package, auth, options, lines))
