"""Send authenticated calls through generated clients: placement, tokens, 401 recovery, permits, hooks and ownership."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import time
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_runtime import Exchange, argument, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_INVALID: Final = {"WWW-Authenticate": 'Bearer error="invalid_token"'}
_ORIGIN: Final = "https://api.example.com"
_OTHER: Final = "https://other.example.com"
_LIFETIME: Final = 0.3


def _ok() -> Callable[[Any], Any]:
    return raw_response(200, b"ok", "application/octet-stream")


def _rejected() -> Callable[[Any], Any]:
    return raw_response(401, b"rejected", "application/octet-stream", **_INVALID)


def _bearer(auth: ModuleType, value: str = "token", **fields: object) -> object:
    return auth.BearerCredential(auth.AccessToken(value, **fields), auth.TokenVersion())


def _failure(error: BaseException) -> tuple[object, ...]:
    return (
        type(error).__name__,
        getattr(error, "reason", None),
        type(getattr(error, "cause", None)).__name__,
        tuple(getattr(error, "__notes__", ())),
        getattr(error, "attempt_count", None),
        len(getattr(error, "body_bytes", b"") or b""),
    )


def _outcome(call: Callable[[], object]) -> tuple[object, ...]:
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        return _failure(error)
    return ("returned", getattr(getattr(result, "info", None), "status_code", None))


async def _aoutcome(call: Callable[[], Any]) -> tuple[object, ...]:
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        return _failure(error)
    return ("returned", getattr(getattr(result, "info", None), "status_code", None))


class _Provider:
    """A refreshable provider whose callbacks can fail, recording every callback it served."""

    def __init__(
        self, material: object, *, refreshed: object = None, failures: dict[str, Exception] | None = None
    ) -> None:
        self.material = material
        self.refreshed = material if refreshed is None else refreshed
        self.failures = failures or {}
        self.calls: list[str] = []

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if (failure := self.failures.get(name)) is not None:
            raise failure

    def get(self, context: object) -> object:
        del context
        self._call("get")
        return self.material

    def invalidate(self, version: object) -> None:
        del version
        self._call("invalidate")

    def refresh(self, context: object) -> object:
        del context
        self._call("refresh")
        self.material = self.refreshed
        return self.refreshed


class _AsyncProvider(_Provider):
    async def get(self, context: object) -> object:  # ty: ignore[invalid-method-override]
        return _Provider.get(self, context)

    async def invalidate(self, version: object) -> None:  # ty: ignore[invalid-method-override]
        _Provider.invalidate(self, version)

    async def refresh(self, context: object) -> object:  # ty: ignore[invalid-method-override]
        return _Provider.refresh(self, context)


class _Renewing(_Provider):
    """A provider whose first token is the one it was given, and whose later tokens are its refreshed one."""

    def get(self, context: object) -> object:
        material = _Provider.get(self, context)
        self.material = self.refreshed
        return material


class _AsyncRenewing(_AsyncProvider):
    async def get(self, context: object) -> object:  # ty: ignore[invalid-method-override]
        return _Renewing.get(self, context)  # ty: ignore[invalid-argument-type]


async def _later(value: object) -> object:
    return value


class _Deferring:
    """A synchronous provider that returns a coroutine instead of material."""

    def get(self, context: object) -> object:
        del context
        return _later(None)


class _Signer:
    def __init__(
        self,
        auth: ModuleType,
        result: object = None,
        *,
        failure: Exception | None = None,
        origins: tuple[str, ...] = (),
        managed: tuple[str, ...] = ("X-Sig",),
    ) -> None:
        self.capabilities = auth.SignerCapabilities(origins, managed, ("sig",))
        self.result = auth.SignatureFields((("X-Sig", "signed"),), ()) if result is None else result
        self.failure = failure
        self.calls = 0

    def sign(self, request: object) -> object:
        del request
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return self.result


class _AsyncSigner(_Signer):
    async def sign(self, request: object) -> object:  # ty: ignore[invalid-method-override]
        return _Signer.sign(self, request)


class _Late(_Signer):
    """A signer that answers only once the given monotonic time has passed."""

    def __init__(self, auth: ModuleType, until: float) -> None:
        super().__init__(auth)
        self.until = until

    def sign(self, request: object) -> object:
        time.sleep(max(0.0, self.until - monotonic()))
        return _Signer.sign(self, request)


class _AsyncLate(_Late):
    async def sign(self, request: object) -> object:  # ty: ignore[invalid-method-override]
        await asyncio.sleep(max(0.0, self.until - monotonic()))
        return _Signer.sign(self, request)


class _Permit:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def release(self) -> None:
        self.log.append("release")


class _AsyncPermit(_Permit):
    async def release(self) -> None:  # ty: ignore[invalid-method-override]
        _Permit.release(self)


class _Limiter:
    """Grant permits, holding the first one back until the given monotonic time."""

    def __init__(self, until: float) -> None:
        self.until = until
        self.log: list[str] = []

    def _wait(self) -> float:
        self.log.append("acquire")
        return max(0.0, self.until - monotonic()) if self.log.count("acquire") == 1 else 0.0

    def acquire(self, context: object) -> object:
        del context
        time.sleep(self._wait())
        return _Permit(self.log)


class _AsyncLimiter(_Limiter):
    async def acquire(self, context: object) -> object:  # ty: ignore[invalid-method-override]
        del context
        await asyncio.sleep(self._wait())
        return _AsyncPermit(self.log)


class _Lagging(_Limiter):
    """Grant every permit only after a token minted when it was requested has expired."""

    def _wait(self) -> float:
        self.log.append("acquire")
        return _LIFETIME + 0.05


class _AsyncLagging(_AsyncLimiter):
    _wait = _Lagging._wait


class _Minting:
    """A provider whose every token expires before the next permit arrives."""

    def __init__(self, auth: ModuleType) -> None:
        self.auth = auth
        self.calls: list[str] = []

    def get(self, context: object) -> object:
        del context
        self.calls.append("get")
        token, _ = _soon(self.auth)
        return self.auth.BearerCredential(token, self.auth.TokenVersion())


class _AsyncMinting(_Minting):
    async def get(self, context: object) -> object:  # ty: ignore[invalid-method-override]
        return _Minting.get(self, context)


class _Hook:
    """Record the names of a call's events, failing on the named one once it has passed it `spared` times."""

    def __init__(self, failing: str | None = None, *, spared: int = 0) -> None:
        self.failing = failing
        self.spared = spared
        self.names: list[str] = []

    def on_event(self, event: Any) -> None:
        self.names.append(event.name)
        if event.name == self.failing and self.names.count(event.name) > self.spared:
            msg = f"{event.name} hook failed"
            raise RuntimeError(msg)


class _AsyncHook(_Hook):
    async def on_event(self, event: Any) -> None:  # ty: ignore[invalid-method-override]
        _Hook.on_event(self, event)


class _Offset(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta:
        del dt
        msg = "offset lookup failed"
        raise ValueError(msg)

    def dst(self, dt: datetime | None) -> timedelta | None:
        del dt
        return None


def _cookie_arguments(package: ModuleType) -> dict[str, object]:
    return {
        name: argument(package, "cookie_parameters", location, wire, value)
        for name, (location, wire, value) in {
            "theme": ("cookie", "theme", "dark"),
            "page": ("query", "page", 2),
            "x_trace": ("header", "X-Trace", "trace"),
        }.items()
    }


def _placement(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    static = auth.StaticCredentialProvider
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    cases: tuple[tuple[str, dict[str, object], str, dict[str, object]], ...] = (
        ("basic utf-8", {"basic": static(auth.BasicCredential("usér", "pä:ss"))}, "basic", {}),
        ("basic colon user", {"basic": static(auth.BasicCredential("us:er", "p"))}, "basic", {}),
        ("basic control user", {"basic": static(auth.BasicCredential("us\x00er", "p"))}, "basic", {}),
        ("basic lone surrogate", {"basic": static(auth.BasicCredential("user", "\ud800"))}, "basic", {}),
        ("query key", {"query_key": static(auth.ApiKeyCredential("query-secret"))}, "api_key_query", {}),
        ("query key lone surrogate", {"query_key": static(auth.ApiKeyCredential("\ud800"))}, "api_key_query", {}),
        ("cookie key", {"cookie_key": static(auth.ApiKeyCredential("cookie-secret"))}, "api_key_cookie", {}),
        ("cookie key separator", {"cookie_key": static(auth.ApiKeyCredential("a;b"))}, "api_key_cookie", {}),
        ("header key CRLF", {"header_key": static(auth.ApiKeyCredential("k\r\nX-Evil: 1"))}, "api_key_header", {}),
        ("header key inner space", {"header_key": static(auth.ApiKeyCredential("in ner"))}, "api_key_header", {}),
        (
            "bearer token trailing space",
            {"bearer": auth.StaticTokenProvider(auth.AccessToken("secret "))},
            "bearer",
            {},
        ),
        (
            "and alternative",
            {
                "header_key": static(auth.ApiKeyCredential("and-key")),
                "bearer": auth.StaticTokenProvider(auth.AccessToken("and-token")),
            },
            "and_auth",
            {},
        ),
        (
            "cookie key beside cookie parameter",
            {"cookie_key": static(auth.ApiKeyCredential("cookie-secret"))},
            "cookie_parameters",
            _cookie_arguments(package),
        ),
        ("token type mac", {"bearer": auth.StaticTokenProvider(auth.AccessToken("t", token_type="MAC"))}, "bearer", {}),
        ("api key material for bearer", {"bearer": static(auth.ApiKeyCredential("k"))}, "bearer", {}),
        (
            "past expiry",
            {
                "bearer": auth.StaticTokenProvider(
                    auth.AccessToken("t", expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
                )
            },
            "bearer",
            {},
        ),
        (
            "naive expiry",
            {"bearer": auth.StaticTokenProvider(auth.AccessToken("t", expires_at=datetime(2026, 1, 1)))},
            "bearer",
            {},
        ),
        (
            "failing offset expiry",
            {
                "bearer": auth.StaticTokenProvider(
                    auth.AccessToken("t", expires_at=datetime(2026, 1, 1, tzinfo=_Offset()))
                )
            },
            "bearer",
            {},
        ),
        ("future expiry", {"bearer": auth.StaticTokenProvider(auth.AccessToken("t", expires_at=future))}, "bearer", {}),
        ("provider get raises", {"bearer": _Provider(None, failures={"get": ValueError("get failed")})}, "bearer", {}),
        ("provider returns a coroutine", {"bearer": _Deferring()}, "bearer", {}),
        (
            "environment missing",
            {"bearer": auth.EnvironmentCredentialProvider("DCG_AUTH_FLOWS_MISSING", kind="bearer")},
            "bearer",
            {},
        ),
    )
    for label, credentials, method, arguments in cases:
        exchange = Exchange(lines)
        exchange.respond(_ok())
        with (
            exchange.client() as native,
            package.Client(http_client=native, options=options.ClientOptions(auth=auth.AuthConfig(credentials))) as api,
        ):
            call = getattr(api.auth.with_response, method)
            record(lines, label, lambda call=call, arguments=arguments: _outcome(lambda: call(**arguments)))

    for case in json.loads((Path(__file__).parents[1] / "generation_platform/client/scope-grants.json").read_text()):
        for status in (200, 403):
            grants = None if case["grants"] is None else tuple(case["grants"])
            provider = _Provider(_bearer(auth, scopes=grants))
            exchange = Exchange([])
            exchange.respond(
                raw_response(
                    status,
                    b"result",
                    "application/octet-stream",
                    **{"WWW-Authenticate": 'Bearer error="insufficient_scope"'},
                )
            )
            with (
                exchange.client() as native,
                package.Client(
                    http_client=native,
                    options=options.ClientOptions(
                        auth=auth.AuthConfig({"oauth": provider}),
                        clock=options.Clock(monotonic=lambda: 100.0, time=lambda: 1800000000.0),
                    ),
                ) as api,
            ):
                try:
                    result = api.auth.with_response.oauth_scopes()
                    actual = result.info.status_code
                except Exception as error:  # noqa: BLE001
                    actual = error.status_code
            arrivals = sum(line.startswith("  > GET https://api.example.com/oauth/scopes") for line in exchange.lines)
            lines.append(
                f"  {case['label']} grants={grants} status={actual}"
                f" callbacks={provider.calls} provider_calls={len(provider.calls)} resource_arrivals={arrivals}"
            )


def _environment(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    provider = auth.EnvironmentCredentialProvider("DCG_AUTH_FLOWS_TOKEN", kind="bearer")
    context = auth.CredentialContext(scheme="bearer", required_scopes=(), audience=None, origin=_ORIGIN, deadline=None)
    os.environ["DCG_AUTH_FLOWS_TOKEN"] = "first"
    try:
        first, again = provider.get(context), provider.get(context)
        os.environ["DCG_AUTH_FLOWS_TOKEN"] = "second"
        changed = provider.get(context)
    finally:
        del os.environ["DCG_AUTH_FLOWS_TOKEN"]
    lines.append(
        f"  environment bearer version same value={first.version == again.version}"
        f" changed value={first.version != changed.version}"
    )
    del package, options


def _configuration(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    token = auth.StaticTokenProvider(auth.AccessToken("token"))
    key = auth.StaticCredentialProvider(auth.ApiKeyCredential("key"))

    class _Partial:
        def get(self, context: object) -> object:
            del context
            return _bearer(auth)

        def refresh(self, context: object) -> object:
            del context
            return _bearer(auth)

    mapped = _Signer(auth)
    mapped.capabilities = {"not": "capabilities"}

    class _Unknowable(_Signer):
        @property
        def capabilities(self) -> object:  # ty: ignore[invalid-method-override]
            msg = "capabilities unavailable"
            raise RuntimeError(msg)

        @capabilities.setter
        def capabilities(self, value: object) -> None:
            del value

    cases: tuple[tuple[str, Callable[[], object], str], ...] = (
        (
            "async provider on sync client",
            lambda: auth.AuthConfig({"bearer": auth.AsyncStaticTokenProvider(auth.AccessToken("t"))}),
            "bearer",
        ),
        ("partial refresh capability", lambda: auth.AuthConfig({"bearer": _Partial()}), "bearer"),
        (
            "async signer on sync client",
            lambda: auth.AuthConfig({}, send_on_anonymous=True, signers=(_AsyncSigner(auth),)),
            "anonymous",
        ),
        ("origin with path", lambda: auth.AuthConfig({"bearer": token}, allowed_origins=(f"{_ORIGIN}/",)), "bearer"),
        (
            "origin with user",
            lambda: auth.AuthConfig({"bearer": token}, allowed_origins=("https://user@a.example.com",)),
            "bearer",
        ),
        (
            "origin port overflow",
            lambda: auth.AuthConfig({"bearer": token}, allowed_origins=("https://a.example.com:99999",)),
            "bearer",
        ),
        ("unknown scheme", lambda: auth.AuthConfig({"nope": token}), "bearer"),
        ("unavailable scheme", lambda: auth.AuthConfig({"unused_digest": token}), "bearer"),
        (
            "selection out of range",
            lambda: auth.AuthConfig({"bearer": token, "header_key": key}, selection=5),
            "or_auth",
        ),
        ("selected alternative incomplete", lambda: auth.AuthConfig({"header_key": key}, selection=1), "or_auth"),
        ("selection on anonymous operation", lambda: auth.AuthConfig({"bearer": token}, selection=1), "anonymous"),
        ("selection on single alternative", lambda: auth.AuthConfig({"bearer": token}, selection=1), "bearer"),
        ("anonymous without opt-in", lambda: auth.AuthConfig({"bearer": token}), "empty_security"),
        (
            "anonymous scheme without credential",
            lambda: auth.AuthConfig({"header_key": key}, send_on_anonymous=True, anonymous_schemes=("bearer",)),
            "anonymous",
        ),
        ("anonymous without schemes or signers", lambda: auth.AuthConfig({}, send_on_anonymous=True), "anonymous"),
        (
            "signer capabilities mapping",
            lambda: auth.AuthConfig({}, send_on_anonymous=True, signers=(mapped,)),
            "anonymous",
        ),
        (
            "signer capabilities raising",
            lambda: auth.AuthConfig({}, send_on_anonymous=True, signers=(_Unknowable(auth),)),
            "anonymous",
        ),
        (
            "two credentials one header",
            lambda: auth.AuthConfig(
                {"bearer": token, "basic": auth.StaticCredentialProvider(auth.BasicCredential("u", "p"))},
                send_on_anonymous=True,
                anonymous_schemes=("bearer", "basic"),
            ),
            "anonymous",
        ),
        (
            "signer header beside credential header",
            lambda: auth.AuthConfig({"bearer": token}, signers=(_ManagingSigner(auth, ("Authorization",), ()),)),
            "bearer",
        ),
        (
            "two signers one query name",
            lambda: auth.AuthConfig(
                {},
                send_on_anonymous=True,
                signers=(_ManagingSigner(auth, (), ("sig",)), _ManagingSigner(auth, (), ("sig",))),
            ),
            "anonymous",
        ),
        (
            "signer managing cookie beside cookie key",
            lambda: auth.AuthConfig({"cookie_key": key}, signers=(_ManagingSigner(auth, ("Cookie",), ()),)),
            "api_key_cookie",
        ),
        (
            "invalid managed header",
            lambda: auth.AuthConfig({}, send_on_anonymous=True, signers=(_ManagingSigner(auth, ("Bad Name",), ()),)),
            "anonymous",
        ),
        (
            "header credential named Cookie beside cookie key",
            lambda: auth.AuthConfig(
                {"cookie_key": key, "cookie_header": key},
                send_on_anonymous=True,
                anonymous_schemes=("cookie_key", "cookie_header"),
            ),
            "anonymous",
        ),
    )
    for label, config, method in cases:
        exchange = Exchange(lines)
        exchange.respond(_ok())
        with exchange.client() as native:
            try:
                api = package.Client(http_client=native, options=options.ClientOptions(auth=config()))
            except Exception as error:  # noqa: BLE001
                lines.append(f"  {label} construction = {_failure(error)}")
                continue
            with api:
                record(lines, label, lambda api=api, method=method: _outcome(getattr(api.auth.with_response, method)))
    exchange = Exchange(lines)
    exchange.respond(_ok(), _ok(), _ok(), _ok())
    with (
        exchange.client() as native,
        package.Client(
            http_client=native, options=options.ClientOptions(auth=auth.AuthConfig({"bearer": token}))
        ) as api,
    ):
        patch = options.RequestOptions(headers=(("Authorization", "generic"),))
        record(
            lines,
            "generic managed header patch",
            lambda: _outcome(lambda: api.auth.with_response.bearer(options=patch)),
        )
    exchange = Exchange(lines)
    exchange.respond(_ok(), _ok())
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(auth=auth.AuthConfig({"query_key": key, "cookie_key": key})),
        ) as api,
    ):
        query = options.RequestOptions(query=(("api_key", "generic"),))
        record(
            lines,
            "generic managed query patch",
            lambda: _outcome(lambda: api.auth.with_response.api_key_query(options=query)),
        )
        cookie = options.RequestOptions(headers=(("Cookie", "theme=light; session_key=generic"),))
        record(
            lines,
            "generic managed cookie patch",
            lambda: _outcome(lambda: api.auth.with_response.api_key_cookie(options=cookie)),
        )
        plain = options.RequestOptions(headers=(("Cookie", "theme=light"),))
        record(
            lines,
            "generic ordinary cookie patch",
            lambda: _outcome(lambda: api.auth.with_response.api_key_cookie(options=plain)),
        )
        padded = options.RequestOptions(headers=(("Cookie", " theme=light\t"),))
        exchange.respond(_ok())
        record(
            lines,
            "padded ordinary cookie patch",
            lambda: _outcome(lambda: api.auth.with_response.api_key_cookie(options=padded)),
        )
        arguments = _cookie_arguments(package)
        for label, signer in (
            ("signer claiming a parameter header", _ManagingSigner(auth, ("X-Trace",), ())),
            ("signer claiming an exploded query field", _ManagingSigner(auth, (), ("kind",))),
        ):
            signing = options.RequestOptions(auth=auth.AuthConfig({"cookie_key": key}, signers=(signer,)))
            record(
                lines,
                label,
                lambda signing=signing: _outcome(
                    lambda: api.auth.with_response.cookie_parameters(**arguments, options=signing)
                ),
            )


class _Publishing(_Provider):
    """A provider that publishes a newer token when told its current one was rejected."""

    def invalidate(self, version: object) -> None:
        _Provider.invalidate(self, version)
        self.material = self.refreshed


class _AsyncPublishing(_AsyncProvider):
    async def invalidate(self, version: object) -> None:  # ty: ignore[invalid-method-override]
        _Publishing.invalidate(self, version)  # ty: ignore[invalid-argument-type]


def _gate_cases(
    auth: ModuleType, options: ModuleType, static: type, provider: type[_Provider], publishing: type[_Provider]
) -> tuple[tuple[str, str, Callable[[], object], object, tuple[Callable[[Any], Any], ...]], ...]:
    retry = options.RetryOptions(initial_delay=0)
    once = options.RetryOptions(max_retries=0)
    return (
        ("static token rejected", "bearer", lambda: static(auth.AccessToken("static")), retry, (_rejected(),)),
        (
            "refreshable recovered",
            "bearer",
            lambda: provider(_bearer(auth, "old"), refreshed=_bearer(auth, "new")),
            retry,
            (_rejected(), _ok()),
        ),
        (
            "refreshable rejected twice",
            "bearer",
            lambda: provider(_bearer(auth, "old"), refreshed=_bearer(auth, "new")),
            retry,
            (_rejected(), _rejected()),
        ),
        (
            "newer publication skips refresh",
            "bearer",
            lambda: publishing(_bearer(auth, "old"), refreshed=_bearer(auth, "published")),
            retry,
            (_rejected(), _ok()),
        ),
        (
            "invalidate fails while recovering",
            "bearer",
            lambda: provider(_bearer(auth), failures={"invalidate": RuntimeError("invalidate failed")}),
            retry,
            (_rejected(),),
        ),
        (
            "invalidate fails locally",
            "bearer",
            lambda: provider(_bearer(auth), failures={"invalidate": RuntimeError("invalidate failed")}),
            once,
            (_rejected(),),
        ),
        (
            "refresh fails",
            "bearer",
            lambda: provider(_bearer(auth), failures={"refresh": RuntimeError("refresh failed")}),
            retry,
            (_rejected(),),
        ),
        ("unsafe operation rejected", "unsafe_auth", lambda: provider(_bearer(auth)), retry, (_rejected(),)),
        ("never operation rejected", "never_auth", lambda: provider(_bearer(auth)), retry, (_rejected(),)),
    )


def _gates(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for label, method, provider, retry, replies in _gate_cases(
        auth, options, auth.StaticTokenProvider, _Provider, _Publishing
    ):
        exchange = Exchange(lines)
        exchange.respond(*replies)
        credentials = provider()
        with (
            exchange.client() as native,
            package.Client(
                http_client=native,
                options=options.ClientOptions(auth=auth.AuthConfig({"bearer": credentials}), retry=retry),
            ) as api,
        ):
            record(lines, label, lambda api=api, method=method: _outcome(getattr(api.auth.with_response, method)))
        lines.append(f"    callbacks={getattr(credentials, 'calls', ())}")
    exchange = Exchange(lines)
    exchange.respond(_rejected())
    failing = _Provider(_bearer(auth), failures={"invalidate": RuntimeError("invalidate failed")})
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(
                auth=auth.AuthConfig({"bearer": failing}), retry=options.RetryOptions(max_retries=0)
            ),
        ) as api,
    ):
        record(lines, "raw invalidate failure", lambda: _outcome(api.auth.with_raw_response.bearer))

        def streamed() -> None:
            with api.auth.with_streaming_response.bearer() as response:
                del response

        exchange.respond(_rejected())
        record(lines, "streamed invalidate failure", lambda: _outcome(streamed))
        exchange.respond(_rejected())
        record(
            lines,
            "request_raw invalidate failure",
            lambda: _outcome(
                lambda: api.request_raw("GET", f"{_ORIGIN}/bearer", options=_raw_auth(auth, options, failing))
            ),
        )
    exchange = Exchange(lines)
    exchange.respond(_rejected())
    refreshable, hook = _Provider(_bearer(auth)), _Hook("auth_start", spared=1)
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(
                auth=auth.AuthConfig({"bearer": refreshable}),
                retry=options.RetryOptions(initial_delay=0),
                hooks=(hook,),
            ),
        ) as api,
    ):
        record(lines, "hook fails on the invalidation span", lambda: _outcome(api.auth.with_raw_response.bearer))
    lines.append(f"    callbacks={refreshable.calls} events={hook.names}")
    signer = _Signer(auth)
    exchange = Exchange(lines)
    exchange.respond(_rejected())
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(
                auth=auth.AuthConfig({}, send_on_anonymous=True, signers=(signer,)),
                retry=options.RetryOptions(initial_delay=0),
            ),
        ) as api,
    ):
        record(lines, "signed anonymous rejected", lambda: _outcome(api.auth.with_response.anonymous))
    _after_send(package, auth, options, lines)


def _rejected_after(until: float) -> Callable[[Any], Any]:
    """Answer 401 invalid_token only once the given monotonic time has passed, as a slow server would."""
    rejected = _rejected()

    def reply(request: Any) -> Any:
        time.sleep(max(0.0, until - monotonic()))
        return rejected(request)

    return reply


def _in_flight(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Refresh a cached token that expired while its request was in flight, instead of refusing it as expired."""
    exchange = Exchange(lines)
    with (
        exchange.client() as native,
        package.Client(
            http_client=native, options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
        ) as api,
    ):
        token, until = _soon(auth)
        provider = _Provider(auth.BearerCredential(token, auth.TokenVersion()), refreshed=_bearer(auth, "lasting"))
        exchange.respond(_rejected_after(until), _ok())
        expiring = options.RequestOptions(auth=auth.AuthConfig({"bearer": provider}))
        record(
            lines, "token expired in flight", lambda: _outcome(lambda: api.auth.with_response.bearer(options=expiring))
        )
    lines.append(f"    callbacks={provider.calls}")


async def _ain_flight(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    async with (
        exchange.async_client() as native,
        package.AsyncClient(
            http_client=native, options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
        ) as api,
    ):
        token, until = _soon(auth)
        provider = _AsyncProvider(auth.BearerCredential(token, auth.TokenVersion()), refreshed=_bearer(auth, "lasting"))
        exchange.respond(_rejected_after(until), _ok())
        expiring = options.RequestOptions(auth=auth.AuthConfig({"bearer": provider}))
        outcome = await _aoutcome(lambda: api.auth.with_response.bearer(options=expiring))
    lines.append(f"  async token expired in flight = {outcome}")
    lines.append(f"    callbacks={provider.calls}")


def _after_send(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Report auth failures that follow a sent request with the delivery the call reached, never as unsent."""
    lapsed = _bearer(auth, "lapsed", expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    retry = options.RetryOptions(initial_delay=0)
    for label, scheme, method, provider, reply, redirects in (
        (
            "retry token already expired",
            "bearer",
            "bearer",
            _Renewing(_bearer(auth), refreshed=lapsed),
            raw_response(503, b"busy", "application/octet-stream"),
            False,
        ),
        (
            "redirect hop token already expired",
            "bearer",
            "bearer",
            _Renewing(_bearer(auth), refreshed=lapsed),
            _moved(f"{_ORIGIN}/bearer?hop=1"),
            True,
        ),
        (
            "refresh grants known empty scopes",
            "oauth",
            "oauth_read",
            _Provider(_bearer(auth), refreshed=_bearer(auth, "narrowed", scopes=())),
            _rejected(),
            False,
        ),
    ):
        first_line = len(lines)
        exchange = Exchange(lines)
        exchange.respond(
            reply,
            raw_response(
                403,
                b"forbidden",
                "application/octet-stream",
                **{"WWW-Authenticate": 'Bearer error="insufficient_scope"'},
            )
            if scheme == "oauth"
            else _ok(),
        )
        with (
            exchange.client() as native,
            package.Client(
                http_client=native,
                options=options.ClientOptions(
                    auth=auth.AuthConfig({scheme: provider}), retry=retry, follow_redirects=redirects
                ),
            ) as api,
        ):
            record(lines, label, lambda api=api, method=method: _outcome(getattr(api.auth.with_response, method)))
        lines.append(
            f"    callbacks={provider.calls}"
            f" resource_arrivals={sum(line.startswith('  > GET') for line in exchange.lines[first_line:])}"
        )


async def _agates(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for case in json.loads((Path(__file__).parents[1] / "generation_platform/client/scope-grants.json").read_text()):
        for status in (200, 403):
            grants = None if case["grants"] is None else tuple(case["grants"])
            provider = _AsyncProvider(_bearer(auth, scopes=grants))
            exchange = Exchange([])
            exchange.respond(
                raw_response(
                    status,
                    b"result",
                    "application/octet-stream",
                    **{"WWW-Authenticate": 'Bearer error="insufficient_scope"'},
                )
            )
            async with (
                exchange.async_client() as native,
                package.AsyncClient(
                    http_client=native,
                    options=options.ClientOptions(
                        auth=auth.AuthConfig({"oauth": provider}),
                        clock=options.Clock(monotonic=lambda: 100.0, time=lambda: 1800000000.0),
                    ),
                ) as api,
            ):
                try:
                    result = await api.auth.with_response.oauth_scopes()
                    actual = result.info.status_code
                except Exception as error:  # noqa: BLE001
                    actual = error.status_code
            arrivals = sum(line.startswith("  > GET https://api.example.com/oauth/scopes") for line in exchange.lines)
            lines.append(
                f"  async {case['label']} grants={grants} status={actual}"
                f" callbacks={provider.calls} provider_calls={len(provider.calls)} resource_arrivals={arrivals}"
            )
    for label, method, provider, retry, replies in _gate_cases(
        auth, options, auth.AsyncStaticTokenProvider, _AsyncProvider, _AsyncPublishing
    ):
        exchange = Exchange(lines)
        exchange.respond(*replies)
        credentials = provider()
        async with (
            exchange.async_client() as native,
            package.AsyncClient(
                http_client=native,
                options=options.ClientOptions(auth=auth.AuthConfig({"bearer": credentials}), retry=retry),
            ) as api,
        ):
            outcome = await _aoutcome(getattr(api.auth.with_response, method))
        lines.append(f"  async {label} = {outcome}")
        lines.append(f"    callbacks={getattr(credentials, 'calls', ())}")
    exchange = Exchange(lines)
    exchange.respond(_rejected())
    failing = _AsyncProvider(_bearer(auth), failures={"invalidate": RuntimeError("invalidate failed")})
    async with (
        exchange.async_client() as native,
        package.AsyncClient(
            http_client=native,
            options=options.ClientOptions(
                auth=auth.AuthConfig({"bearer": failing}), retry=options.RetryOptions(max_retries=0)
            ),
        ) as api,
    ):

        async def raised() -> None:
            await api.auth.with_raw_response.bearer()

        async def streamed() -> None:
            async with api.auth.with_streaming_response.bearer() as response:
                del response

        lines.append(f"  async raw invalidate failure = {await _aoutcome(raised)}")
        exchange.respond(_rejected())
        lines.append(f"  async streamed invalidate failure = {await _aoutcome(streamed)}")
        exchange.respond(_rejected())
        raw = await _aoutcome(
            lambda: api.request_raw("GET", f"{_ORIGIN}/bearer", options=_raw_auth(auth, options, failing))
        )
        lines.append(f"  async request_raw invalidate failure = {raw}")
        exchange.respond(_ok())
        lines.append(f"  async anonymous without opt-in = {await _aoutcome(api.auth.with_response.empty_security)}")
    lines.append(f"    callbacks={failing.calls}")


def _raw_auth(auth: ModuleType, options: ModuleType, provider: object) -> object:
    """Send the bearer credential on raw requests to the server origin."""
    return options.RequestOptions(
        auth=auth.AuthConfig(
            {"bearer": provider}, send_on_anonymous=True, anonymous_schemes=("bearer",), allowed_origins=(_ORIGIN,)
        )
    )


def _soon(auth: ModuleType) -> tuple[object, float]:
    """Return a token that expires shortly, with the monotonic time by which it certainly has."""
    until = monotonic() + _LIFETIME + 0.05
    return auth.AccessToken("soon", expires_at=datetime.now(timezone.utc) + timedelta(seconds=_LIFETIME)), until


def _expiring(
    auth: ModuleType, static: type, renewing: type[_Provider], limiter: type[_Limiter], late: type[_Late]
) -> tuple[tuple[str, Callable[[], tuple[Any, _Limiter, tuple[object, ...]]]], ...]:
    def renewed() -> tuple[Any, _Limiter, tuple[object, ...]]:
        token, until = _soon(auth)
        provider = renewing(auth.BearerCredential(token, auth.TokenVersion()), refreshed=_bearer(auth, "lasting"))
        return provider, limiter(until), ()

    def expired() -> tuple[Any, _Limiter, tuple[object, ...]]:
        token, until = _soon(auth)
        return static(token), limiter(until), ()

    def outlived() -> tuple[Any, _Limiter, tuple[object, ...]]:
        token, until = _soon(auth)
        return static(token), limiter(0.0), (late(auth, until),)

    def minting() -> tuple[Any, _Limiter, tuple[object, ...]]:
        lagging = _Lagging if limiter is _Limiter else _AsyncLagging
        return (_Minting if limiter is _Limiter else _AsyncMinting)(auth), lagging(0.0), ()

    return (
        ("token expires while waiting for a permit", renewed),
        ("static token expires while waiting for a permit", expired),
        ("signer outlives the token", outlived),
        ("every token expires while waiting for a permit", minting),
    )


def _permits(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for label, setup in _expiring(auth, auth.StaticTokenProvider, _Renewing, _Limiter, _Late):
        exchange = Exchange(lines)
        exchange.respond(_ok())
        hook = _Hook()
        with (
            exchange.client() as native,
            package.Client(http_client=native, options=options.ClientOptions(hooks=(hook,))) as api,
        ):
            credentials, limiter, signers = setup()
            expiring = options.RequestOptions(
                auth=auth.AuthConfig({"bearer": credentials}, signers=signers), limiter=limiter
            )
            record(
                lines,
                label,
                lambda api=api, expiring=expiring: _outcome(lambda: api.auth.with_response.bearer(options=expiring)),
            )
        lines.append(f"    limiter={limiter.log} callbacks={getattr(credentials, 'calls', ())} events={hook.names}")


async def _apermits(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for label, setup in _expiring(auth, auth.AsyncStaticTokenProvider, _AsyncRenewing, _AsyncLimiter, _AsyncLate):
        exchange = Exchange(lines)
        exchange.respond(_ok())
        hook = _AsyncHook()
        async with (
            exchange.async_client() as native,
            package.AsyncClient(http_client=native, options=options.ClientOptions(hooks=(hook,))) as api,
        ):
            credentials, limiter, signers = setup()
            expiring = options.RequestOptions(
                auth=auth.AuthConfig({"bearer": credentials}, signers=signers), limiter=limiter
            )
            outcome = await _aoutcome(
                lambda api=api, expiring=expiring: api.auth.with_response.bearer(options=expiring)
            )
        lines.append(f"  async {label} = {outcome}")
        lines.append(f"    limiter={limiter.log} callbacks={getattr(credentials, 'calls', ())} events={hook.names}")


def _hooks(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for label, credentials, hook in (
        (
            "provider and auth_end hook fail",
            _Provider(None, failures={"get": ValueError("get failed")}),
            _Hook("auth_end"),
        ),
        ("auth_start hook fails", _Provider(_bearer(auth)), _Hook("auth_start")),
    ):
        exchange = Exchange(lines)
        exchange.respond(_ok())
        with (
            exchange.client() as native,
            package.Client(
                http_client=native,
                options=options.ClientOptions(auth=auth.AuthConfig({"bearer": credentials}), hooks=(hook,)),
            ) as api,
        ):
            record(lines, label, lambda api=api: _outcome(api.auth.with_response.bearer))
        lines.append(f"    callbacks={credentials.calls} events={hook.names}")


async def _ahooks(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for label, credentials, hook in (
        (
            "provider and auth_end hook fail",
            _AsyncProvider(None, failures={"get": ValueError("get failed")}),
            _AsyncHook("auth_end"),
        ),
        ("auth_start hook fails", _AsyncProvider(_bearer(auth)), _AsyncHook("auth_start")),
    ):
        exchange = Exchange(lines)
        exchange.respond(_ok())
        async with (
            exchange.async_client() as native,
            package.AsyncClient(
                http_client=native,
                options=options.ClientOptions(auth=auth.AuthConfig({"bearer": credentials}), hooks=(hook,)),
            ) as api,
        ):
            outcome = await _aoutcome(api.auth.with_response.bearer)
        lines.append(f"  async {label} = {outcome}")
        lines.append(f"    callbacks={credentials.calls} events={hook.names}")


class _Lifetime:
    """A provider with a close method, which only its caller may call."""

    def __init__(self, auth: ModuleType) -> None:
        self.auth = auth
        self.closes = 0

    def get(self, context: object) -> object:
        del context
        return _bearer(self.auth)

    def close(self) -> None:
        self.closes += 1


class _AsyncLifetime(_Lifetime):
    async def get(self, context: object) -> object:  # ty: ignore[invalid-method-override]
        return _Lifetime.get(self, context)

    async def aclose(self) -> None:
        self.closes += 1


def _ownership(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    provider = _Lifetime(auth)
    exchange = Exchange(lines)
    exchange.respond(_ok(), _ok())
    with exchange.client() as native:
        api = package.Client(
            http_client=native, options=options.ClientOptions(auth=auth.AuthConfig({"bearer": provider}))
        )
        view = api.with_options(options.RequestOptions(auth=auth.AuthConfig({"bearer_alias": provider})))
        lines.append(f"  root call = {_outcome(api.auth.with_response.bearer)}")
        api.close()
        api.close()
        lines.append(f"  call after close = {_outcome(view.auth.with_response.bearer)}")
        lines.append(
            f"    provider closes={provider.closes} borrowed closed={native.is_closed} view close={hasattr(view, 'close')}"
        )


async def _aownership(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    provider = _AsyncLifetime(auth)
    exchange = Exchange(lines)
    exchange.respond(_ok())
    async with exchange.async_client() as native:
        api = package.AsyncClient(
            http_client=native, options=options.ClientOptions(auth=auth.AuthConfig({"bearer": provider}))
        )
        view = api.with_options(options.RequestOptions(auth=auth.AuthConfig({"bearer_alias": provider})))
        lines.append(f"  async root call = {await _aoutcome(api.auth.with_response.bearer)}")
        await api.aclose()
        await api.aclose()
        lines.append(f"  async call after close = {await _aoutcome(view.auth.with_response.bearer)}")
        lines.append(
            f"    provider closes={provider.closes} borrowed closed={native.is_closed} view close={hasattr(view, 'aclose')}"
        )


def _moved(location: str) -> Callable[[Any], Any]:
    return lambda _: httpx2.Response(307, headers={"Location": location, "Content-Length": "0"})


class _CallerKey(httpx2.Auth):
    """A caller's own native Auth on the HTTP client, which places its key on every request HTTPX2 sends."""

    def auth_flow(self, request: httpx2.Request) -> Any:
        request.headers["X-Caller-Key"] = "caller-secret"
        yield request


_REJECTED_ELSEWHERE: Final = "bearer rejected by another origin"


def _answer(label: str) -> Callable[[Any], Any]:
    """Return the reply after the redirect: the other origin rejects the token it never received on one row."""
    return _rejected() if label == _REJECTED_ELSEWHERE else _ok()


def _redirect(status: int, location: str) -> Callable[[Any], Any]:
    return lambda _: httpx2.Response(status, headers={"Location": location, "Content-Length": "0"})


def _redirect_cases(
    auth: ModuleType, options: ModuleType, *, asynchronous: bool
) -> tuple[tuple[str, dict[str, Any], object, str, int, str], ...]:
    """Return the redirect rows: label, native client settings, client options, operation, status, and Location.

    The HTTP client follows redirects unless a row says otherwise; a request carrying a key at a declared scheme
    position other than Authorization, or a signature, is never redirected, whatever the setting.
    """
    prefix = "Async" if asynchronous else ""
    token = getattr(auth, f"{prefix}StaticTokenProvider")(auth.AccessToken("token-secret"))
    key = getattr(auth, f"{prefix}StaticCredentialProvider")(auth.ApiKeyCredential("key-secret"))
    refreshable = (_AsyncProvider if asynchronous else _Provider)(
        _bearer(auth, "token-secret"), refreshed=_bearer(auth, "fresh-secret")
    )
    signer = (_AsyncSigner if asynchronous else _Signer)(auth)
    follows = {"follow_redirects": True}
    keyed = options.ClientOptions(auth=auth.AuthConfig({"header_key": key}))
    return (
        ("header key not redirected", follows, keyed, "api_key_header", 302, f"{_OTHER}/api-key/header"),
        (
            "query key not redirected",
            follows,
            options.ClientOptions(auth=auth.AuthConfig({"query_key": key})),
            "api_key_query",
            307,
            f"{_OTHER}/api-key/query",
        ),
        (
            "cookie key not redirected",
            follows,
            options.ClientOptions(auth=auth.AuthConfig({"cookie_key": key})),
            "api_key_cookie",
            308,
            f"{_OTHER}/api-key/cookie",
        ),
        (
            "patched scheme header not redirected",
            follows,
            options.ClientOptions(headers=(("X-API-Key", "patched-secret"),)),
            "anonymous",
            302,
            f"{_OTHER}/anonymous",
        ),
        (
            "signed request not redirected",
            follows,
            options.ClientOptions(auth=auth.AuthConfig({"bearer": token}, signers=(signer,))),
            "bearer",
            302,
            f"{_OTHER}/bearer",
        ),
        ("header key at its own origin not redirected", follows, keyed, "api_key_header", 302, f"{_ORIGIN}/moved"),
        (
            "SDK choice cannot redirect a header key",
            follows,
            options.ClientOptions(auth=auth.AuthConfig({"header_key": key}), follow_redirects=True),
            "api_key_header",
            307,
            f"{_OTHER}/api-key/header",
        ),
        (
            "bearer redirected without Authorization",
            follows,
            options.ClientOptions(auth=auth.AuthConfig({"bearer": token})),
            "bearer",
            302,
            f"{_OTHER}/bearer",
        ),
        (
            _REJECTED_ELSEWHERE,
            follows,
            options.ClientOptions(auth=auth.AuthConfig({"bearer": refreshable})),
            "bearer",
            302,
            f"{_OTHER}/bearer",
        ),
        (
            "plain request inherits the client's redirects",
            follows,
            options.ClientOptions(),
            "anonymous",
            303,
            f"{_OTHER}/anonymous",
        ),
        (
            "plain request on a client that follows none",
            {},
            options.ClientOptions(),
            "anonymous",
            302,
            f"{_OTHER}/anonymous",
        ),
        (
            "plain request the SDK redirects",
            {},
            options.ClientOptions(follow_redirects=True),
            "anonymous",
            307,
            f"{_OTHER}/anonymous",
        ),
        (
            "plain request the SDK keeps",
            follows,
            options.ClientOptions(follow_redirects=False),
            "anonymous",
            302,
            f"{_OTHER}/anonymous",
        ),
        (
            "caller's own Auth applied and redirected",
            {**follows, "auth": _CallerKey()},
            options.ClientOptions(),
            "anonymous",
            302,
            f"{_OTHER}/anonymous",
        ),
    )


_REDIRECT_MODES: Final = ("typed", "raw", "stream")


def _redirected(response: Any) -> tuple[object, ...]:
    info = response.info
    return ("returned", info.status_code, info.headers.get("location"))


def _redirects(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Send keyed, signed, bearer, and plain calls to a redirect across origins through a following HTTP client."""
    for label, native_settings, settings, method, status, location in _redirect_cases(
        auth, options, asynchronous=False
    ):
        for mode in _REDIRECT_MODES if label.startswith(("header key not", "plain request inherits")) else ("typed",):
            exchange = Exchange(lines)
            exchange.respond(_redirect(status, location), _answer(label))
            with (
                exchange.client(**native_settings) as native,
                package.Client(http_client=native, options=settings) as api,
            ):

                def streamed(api: Any = api, method: str = method) -> tuple[object, ...]:
                    with getattr(api.auth.with_streaming_response, method)() as response:
                        return _redirected(response)

                calls = {
                    "typed": lambda api=api, method=method: _outcome(getattr(api.auth.with_response, method)),
                    "raw": lambda api=api, method=method: _redirected(getattr(api.auth.with_raw_response, method)()),
                    "stream": streamed,
                }
                record(lines, f"{label} {mode}", calls[mode])
            lines.append(f"    unused={len(exchange.responders)}")


async def _aredirects(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Send the redirect rows with asyncio."""
    for label, native_settings, settings, method, status, location in _redirect_cases(auth, options, asynchronous=True):
        for mode in _REDIRECT_MODES if label.startswith(("header key not", "plain request inherits")) else ("typed",):
            exchange = Exchange(lines)
            exchange.respond(_redirect(status, location), _answer(label))
            async with (
                exchange.async_client(**native_settings) as native,
                package.AsyncClient(http_client=native, options=settings) as api,
            ):
                if mode == "typed":
                    outcome = await _aoutcome(getattr(api.auth.with_response, method))
                elif mode == "raw":
                    outcome = _redirected(await getattr(api.auth.with_raw_response, method)())
                else:
                    async with getattr(api.auth.with_streaming_response, method)() as response:
                        outcome = _redirected(response)
            lines.append(f"  async {label} {mode} = {outcome}")
            lines.append(f"    unused={len(exchange.responders)}")


def _signatures(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    fields = auth.SignatureFields
    for label, signer in (
        ("signature query placed", _Signer(auth, fields((("X-Sig", "signed"),), (("sig", "v"),)))),
        ("signer raises", _Signer(auth, failure=RuntimeError("sign failed"))),
        ("signature header undeclared", _Signer(auth, fields((("X-Other", "v"),), ()))),
        ("signature header CRLF", _Signer(auth, fields((("X-Sig", "a\r\nInjected: 1"),), ()))),
        ("signature header leading space", _Signer(auth, fields((("X-Sig", " signed"),), ()))),
        (
            "signature header name folding to a token",
            _Signer(auth, fields((("\u212a-Sig", "v"),), ()), managed=("K-Sig",)),
        ),
        ("signature query undeclared", _Signer(auth, fields((), (("other", "v"),)))),
        ("signature query lone surrogate", _Signer(auth, fields((), (("sig", "\ud800"),)))),
        ("signer returns a mapping", _Signer(auth, {"X-Sig": "v"})),
        ("signer returns a coroutine", _Signer(auth, _later(None))),
        ("signer of another origin", _Signer(auth, origins=(_OTHER,))),
    ):
        exchange = Exchange(lines)
        exchange.respond(_ok())
        with (
            exchange.client() as native,
            package.Client(
                http_client=native,
                options=options.ClientOptions(auth=auth.AuthConfig({}, send_on_anonymous=True, signers=(signer,))),
            ) as api,
        ):
            record(lines, label, lambda api=api: _outcome(api.auth.with_response.anonymous))
    signer = _AsyncSigner(auth, failure=RuntimeError("sign failed"))

    async def signed() -> tuple[object, ...]:
        exchange = Exchange(lines)
        exchange.respond(_ok())
        async with (
            exchange.async_client() as native,
            package.AsyncClient(
                http_client=native,
                options=options.ClientOptions(auth=auth.AuthConfig({}, send_on_anonymous=True, signers=(signer,))),
            ) as api,
        ):
            return await _aoutcome(api.auth.with_response.anonymous)

    lines.append(f"  async signer raises = {asyncio.run(signed())}")


class _ManagingSigner(_Signer):
    def __init__(self, auth: ModuleType, headers: tuple[str, ...], query: tuple[str, ...]) -> None:
        super().__init__(auth)
        self.capabilities = auth.SignerCapabilities((), headers, query)


def auth_flows(package: ModuleType, lines: list[str]) -> None:
    """Send authenticated generated calls and report what reached the wire and how failures were classified."""
    auth, options = (importlib.import_module(f"{package.__name__}.{name}") for name in ("auth", "options"))
    _placement(package, auth, options, lines)
    _environment(package, auth, options, lines)
    _configuration(package, auth, options, lines)
    _gates(package, auth, options, lines)
    run(lambda: _agates(package, auth, options, lines))
    _in_flight(package, auth, options, lines)
    run(lambda: _ain_flight(package, auth, options, lines))
    _permits(package, auth, options, lines)
    run(lambda: _apermits(package, auth, options, lines))
    _hooks(package, auth, options, lines)
    run(lambda: _ahooks(package, auth, options, lines))
    _ownership(package, auth, options, lines)
    run(lambda: _aownership(package, auth, options, lines))
    _redirects(package, auth, options, lines)
    run(lambda: _aredirects(package, auth, options, lines))
    _signatures(package, auth, options, lines)
