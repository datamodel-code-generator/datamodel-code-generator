"""Send authenticated calls through generated clients: placement, tokens, 401 recovery, permits, hooks and ownership."""

from __future__ import annotations

import asyncio
import importlib
import os
import time
from datetime import datetime, timedelta, timezone, tzinfo
from time import monotonic
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_runtime import Exchange, raw_response, record, run

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
        getattr(error, "condition", None),
        getattr(error, "callback", None),
        getattr(error, "retry_stop_reason", None),
        type(getattr(error, "cause", None)).__name__,
        tuple(type(item).__name__ for item in getattr(error, "secondary_errors", ())),
        getattr(error, "network_send_count", None),
        getattr(getattr(error, "delivery_state", None), "value", None),
        len(getattr(error, "body_bytes", b"") or b""),
    )


def _cleanup(error: BaseException) -> tuple[object, ...]:
    return (
        type(error).__name__,
        getattr(error, "pending_calls", None),
        getattr(error, "pending_providers", None),
        type(getattr(error, "cause", None)).__name__,
    )


def _closed(close: Callable[[], object]) -> tuple[object, ...]:
    try:
        close()
    except Exception as error:  # noqa: BLE001
        return _cleanup(error)
    return ("closed",)


async def _aclosed(close: Callable[[], Any]) -> tuple[object, ...]:
    try:
        await close()
    except Exception as error:  # noqa: BLE001
        return _cleanup(error)
    return ("closed",)


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
        self.capabilities = auth.SignerCapabilities(origins, managed, ("sig",), False)
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


class _Owned:
    """A closeable provider that counts its closes and fails them when told to."""

    def __init__(self, auth: ModuleType, failure: Exception | None = None) -> None:
        self.auth = auth
        self.failure = failure
        self.closes = 0

    def get(self, context: object) -> object:
        del context
        return _bearer(self.auth)

    def close(self) -> None:
        self.closes += 1
        if self.failure is not None:
            raise self.failure


class _AsyncOwned:
    """An asynchronous closeable provider whose token request, outlasting a cancellation, and close wait for the test."""

    def __init__(
        self,
        auth: ModuleType,
        failure: Exception | None = None,
        *,
        getting: asyncio.Event | None = None,
        closing: asyncio.Event | None = None,
    ) -> None:
        self.auth = auth
        self.failure = failure
        self.getting = getting
        self.closing = closing
        self.gets = 0
        self.closes = 0

    async def get(self, context: object) -> object:
        del context
        self.gets += 1
        if (getting := self.getting) is not None:
            try:
                await getting.wait()
            except asyncio.CancelledError:
                await getting.wait()
                raise
        return _bearer(self.auth)

    async def aclose(self) -> None:
        self.closes += 1
        if self.closing is not None:
            await self.closing.wait()
        if self.failure is not None:
            raise self.failure


class _Offset(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta:
        del dt
        msg = "offset lookup failed"
        raise ValueError(msg)

    def dst(self, dt: datetime | None) -> timedelta | None:
        del dt
        return None


def _cookie_arguments(package: ModuleType) -> dict[str, object]:
    codecs = importlib.import_module(f"{package.__name__}.types.auth").CookieParametersRequestCodecs
    return {
        name: codecs.parameter(location=location, name=wire).from_wire(value).value
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
        ("bearer token trailing space", {"bearer": auth.StaticTokenProvider(auth.AccessToken("secret "))}, "bearer",
         {}),
        (
            "and alternative",
            {"header_key": static(auth.ApiKeyCredential("and-key")), "bearer": auth.StaticTokenProvider(
                auth.AccessToken("and-token"))},
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
            {"bearer": auth.StaticTokenProvider(
                auth.AccessToken("t", expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))},
            "bearer",
            {},
        ),
        ("naive expiry", {"bearer": auth.StaticTokenProvider(auth.AccessToken("t", expires_at=datetime(2026, 1, 1)))},
         "bearer", {}),
        (
            "failing offset expiry",
            {"bearer": auth.StaticTokenProvider(auth.AccessToken("t", expires_at=datetime(2026, 1, 1, tzinfo=_Offset())))},
            "bearer",
            {},
        ),
        ("future expiry", {"bearer": auth.StaticTokenProvider(auth.AccessToken("t", expires_at=future))}, "bearer", {}),
        ("known read scope", {"oauth": auth.StaticTokenProvider(auth.AccessToken("t", scopes=("read",)))},
         "oauth_read", {}),
        ("known empty scopes", {"oauth": auth.StaticTokenProvider(auth.AccessToken("t", scopes=()))}, "oauth_read", {}),
        ("known write scope", {"oauth": auth.StaticTokenProvider(auth.AccessToken("t", scopes=("write",)))},
         "oauth_read", {}),
        ("unknown scopes", {"oauth": auth.StaticTokenProvider(auth.AccessToken("t"))}, "oauth_read", {}),
        ("provider get raises", {"bearer": _Provider(None, failures={"get": ValueError("get failed")})}, "bearer", {}),
        ("provider returns a coroutine", {"bearer": _Deferring()}, "bearer", {}),
        ("environment missing", {"bearer": auth.EnvironmentCredentialProvider("DCG_AUTH_FLOWS_MISSING", kind="bearer")},
         "bearer", {}),
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


def _environment(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    provider = auth.EnvironmentCredentialProvider("DCG_AUTH_FLOWS_TOKEN", kind="bearer")
    context = auth.CredentialContext(
        scheme="bearer", required_scopes=(), audience=None, origin=_ORIGIN, deadline=None, cancel_token=None
    )
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
        ("async provider on sync client", lambda: auth.AuthConfig({"bearer": auth.AsyncStaticTokenProvider(
            auth.AccessToken("t"))}), "bearer"),
        ("partial refresh capability", lambda: auth.AuthConfig({"bearer": _Partial()}), "bearer"),
        ("async signer on sync client", lambda: auth.AuthConfig({}, send_on_anonymous=True,
                                                                  signers=(_AsyncSigner(auth),)), "anonymous"),
        ("origin with path", lambda: auth.AuthConfig({"bearer": token}, allowed_origins=(f"{_ORIGIN}/",)), "bearer"),
        ("origin with user", lambda: auth.AuthConfig({"bearer": token}, allowed_origins=("https://user@a.example.com",)),
         "bearer"),
        ("origin port overflow", lambda: auth.AuthConfig({"bearer": token}, allowed_origins=("https://a.example.com:99999",)),
         "bearer"),
        ("unknown scheme", lambda: auth.AuthConfig({"nope": token}), "bearer"),
        ("unavailable scheme", lambda: auth.AuthConfig({"unused_digest": token}), "bearer"),
        ("selection out of range", lambda: auth.AuthConfig({"bearer": token, "header_key": key}, selection=5), "or_auth"),
        ("selected alternative incomplete", lambda: auth.AuthConfig({"header_key": key}, selection=1), "or_auth"),
        ("selection on anonymous operation", lambda: auth.AuthConfig({"bearer": token}, selection=1), "anonymous"),
        ("selection on single alternative", lambda: auth.AuthConfig({"bearer": token}, selection=1), "bearer"),
        ("anonymous without opt-in", lambda: auth.AuthConfig({"bearer": token}), "empty_security"),
        ("anonymous scheme without credential", lambda: auth.AuthConfig({"header_key": key}, send_on_anonymous=True,
                                                                         anonymous_schemes=("bearer",)), "anonymous"),
        ("anonymous without schemes or signers", lambda: auth.AuthConfig({}, send_on_anonymous=True), "anonymous"),
        ("signer capabilities mapping", lambda: auth.AuthConfig({}, send_on_anonymous=True, signers=(mapped,)),
         "anonymous"),
        ("signer capabilities raising", lambda: auth.AuthConfig({}, send_on_anonymous=True,
                                                                  signers=(_Unknowable(auth),)), "anonymous"),
        ("two credentials one header", lambda: auth.AuthConfig(
            {"bearer": token, "basic": auth.StaticCredentialProvider(auth.BasicCredential("u", "p"))},
            send_on_anonymous=True, anonymous_schemes=("bearer", "basic")), "anonymous"),
        ("signer header beside credential header", lambda: auth.AuthConfig(
            {"bearer": token}, signers=(_ManagingSigner(auth, ("Authorization",), ()),)), "bearer"),
        ("two signers one query name", lambda: auth.AuthConfig(
            {}, send_on_anonymous=True,
            signers=(_ManagingSigner(auth, (), ("sig",)), _ManagingSigner(auth, (), ("sig",)))), "anonymous"),
        ("signer managing cookie beside cookie key", lambda: auth.AuthConfig(
            {"cookie_key": key}, signers=(_ManagingSigner(auth, ("Cookie",), ()),)), "api_key_cookie"),
        ("invalid managed header", lambda: auth.AuthConfig(
            {}, send_on_anonymous=True, signers=(_ManagingSigner(auth, ("Bad Name",), ()),)), "anonymous"),
        ("header credential named Cookie beside cookie key", lambda: auth.AuthConfig(
            {"cookie_key": key, "cookie_header": key}, send_on_anonymous=True,
            anonymous_schemes=("cookie_key", "cookie_header")), "anonymous"),
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
        package.Client(http_client=native, options=options.ClientOptions(auth=auth.AuthConfig({"bearer": token}))) as api,
    ):
        patch = options.RequestOptions(headers=(("Authorization", "generic"),))
        record(lines, "generic managed header patch", lambda: _outcome(lambda: api.auth.with_response.bearer(options=patch)))
    exchange = Exchange(lines)
    exchange.respond(_ok(), _ok())
    with (
        exchange.client() as native,
        package.Client(
            http_client=native, options=options.ClientOptions(auth=auth.AuthConfig({"query_key": key, "cookie_key": key}))
        ) as api,
    ):
        query = options.RequestOptions(query=(("api_key", "generic"),))
        record(lines, "generic managed query patch", lambda: _outcome(lambda: api.auth.with_response.api_key_query(
            options=query)))
        cookie = options.RequestOptions(headers=(("Cookie", "theme=light; session_key=generic"),))
        record(lines, "generic managed cookie patch", lambda: _outcome(lambda: api.auth.with_response.api_key_cookie(
            options=cookie)))
        plain = options.RequestOptions(headers=(("Cookie", "theme=light"),))
        record(lines, "generic ordinary cookie patch", lambda: _outcome(lambda: api.auth.with_response.api_key_cookie(
            options=plain)))
        arguments = _cookie_arguments(package)
        for label, signer in (
            ("signer claiming a parameter header", _ManagingSigner(auth, ("X-Trace",), ())),
            ("signer claiming an exploded query field", _ManagingSigner(auth, (), ("kind",))),
        ):
            signing = options.RequestOptions(auth=auth.AuthConfig({"cookie_key": key}, signers=(signer,)))
            record(lines, label, lambda signing=signing: _outcome(lambda: api.auth.with_response.cookie_parameters(
                **arguments, options=signing)))


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
        ("refreshable recovered", "bearer", lambda: provider(_bearer(auth, "old"), refreshed=_bearer(auth, "new")), retry,
         (_rejected(), _ok())),
        ("refreshable rejected twice", "bearer", lambda: provider(_bearer(auth, "old"), refreshed=_bearer(auth, "new")),
         retry, (_rejected(), _rejected())),
        ("newer publication skips refresh", "bearer",
         lambda: publishing(_bearer(auth, "old"), refreshed=_bearer(auth, "published")), retry, (_rejected(), _ok())),
        ("invalidate fails while recovering", "bearer",
         lambda: provider(_bearer(auth), failures={"invalidate": RuntimeError("invalidate failed")}), retry,
         (_rejected(),)),
        ("invalidate fails locally", "bearer",
         lambda: provider(_bearer(auth), failures={"invalidate": RuntimeError("invalidate failed")}), once,
         (_rejected(),)),
        ("refresh fails", "bearer", lambda: provider(_bearer(auth), failures={"refresh": RuntimeError("refresh failed")}),
         retry, (_rejected(),)),
        ("unsafe operation rejected", "unsafe_auth", lambda: provider(_bearer(auth)), retry, (_rejected(),)),
        ("never operation rejected", "never_auth", lambda: provider(_bearer(auth)), retry, (_rejected(),)),
    )


def _gates(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for label, method, provider, retry, replies in _gate_cases(auth, options, auth.StaticTokenProvider, _Provider, _Publishing):
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
        record(lines, "request_raw invalidate failure", lambda: _outcome(lambda: api.request_raw(
            "GET", f"{_ORIGIN}/bearer", options=_raw_auth(auth, options, failing))))
    exchange = Exchange(lines)
    exchange.respond(_rejected())
    refreshable, hook = _Provider(_bearer(auth)), _Hook("auth_start", spared=1)
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(
                auth=auth.AuthConfig({"bearer": refreshable}), retry=options.RetryOptions(initial_delay=0), hooks=(hook,)
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
        record(lines, "token expired in flight", lambda: _outcome(lambda: api.auth.with_response.bearer(options=expiring)))
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
        ("retry token already expired", "bearer", "bearer", _Renewing(_bearer(auth), refreshed=lapsed),
         raw_response(503, b"busy", "application/octet-stream"), options.RedirectOptions()),
        ("redirect hop token already expired", "bearer", "bearer", _Renewing(_bearer(auth), refreshed=lapsed),
         _moved(f"{_ORIGIN}/bearer?hop=1"), options.RedirectOptions(enabled=True)),
        ("refresh grants known empty scopes", "oauth", "oauth_read",
         _Provider(_bearer(auth), refreshed=_bearer(auth, "narrowed", scopes=())), _rejected(), options.RedirectOptions()),
    ):
        exchange = Exchange(lines)
        exchange.respond(reply, _ok())
        with (
            exchange.client() as native,
            package.Client(
                http_client=native,
                options=options.ClientOptions(
                    auth=auth.AuthConfig({scheme: provider}), retry=retry, redirects=redirects
                ),
            ) as api,
        ):
            record(lines, label, lambda api=api, method=method: _outcome(getattr(api.auth.with_response, method)))


async def _agates(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
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
        raw = await _aoutcome(lambda: api.request_raw("GET", f"{_ORIGIN}/bearer", options=_raw_auth(auth, options, failing)))
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
            record(lines, label, lambda api=api, expiring=expiring: _outcome(
                lambda: api.auth.with_response.bearer(options=expiring)))
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
            outcome = await _aoutcome(lambda api=api, expiring=expiring: api.auth.with_response.bearer(options=expiring))
        lines.append(f"  async {label} = {outcome}")
        lines.append(f"    limiter={limiter.log} callbacks={getattr(credentials, 'calls', ())} events={hook.names}")


def _hooks(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for label, credentials, hook in (
        ("provider and auth_end hook fail", _Provider(None, failures={"get": ValueError("get failed")}),
         _Hook("auth_end")),
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
        ("provider and auth_end hook fail", _AsyncProvider(None, failures={"get": ValueError("get failed")}),
         _AsyncHook("auth_end")),
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


def _ownership(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    owned = auth.OwnedCredentialProvider
    failing, closing, late = _Owned(auth, RuntimeError("close failed")), _Owned(auth), _Owned(auth)
    exchange = Exchange(lines)
    with exchange.client() as native:
        api = package.Client(
            http_client=native,
            options=options.ClientOptions(auth=auth.AuthConfig({"bearer": owned(failing), "bearer_alias": owned(closing)})),
        )
        lines.append(f"  close with a failing owned provider = {_closed(api.close)}")
        adopting = options.RequestOptions(auth=auth.AuthConfig({"bearer": owned(late)}))
        record(lines, "adopt after close", lambda: _outcome(lambda: api.with_options(adopting)))
        lines.append(f"  close again = {_closed(api.close)}")
    lines.append(f"    closes={failing.closes, closing.closes, late.closes}")
    adopted = _Owned(auth)
    with exchange.client() as native:
        api = package.Client(http_client=native)
        view = api.with_options(options.RequestOptions())
        view.close()
        adopting = options.RequestOptions(auth=auth.AuthConfig({"bearer": owned(adopted)}))
        record(lines, "adopt through a closed view", lambda: _outcome(lambda: view.with_options(adopting)))
        lines.append(f"  close root = {_closed(api.close)}")
    lines.append(f"    adopted closes={adopted.closes}")


async def _aownership(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    owned = auth.OwnedCredentialProvider
    failing = _AsyncOwned(auth, RuntimeError("aclose failed"))
    closing = asyncio.Event()
    slow = _AsyncOwned(auth, closing=closing)
    getting = asyncio.Event()
    held = _AsyncOwned(auth, getting=getting)
    exchange = Exchange(lines)
    exchange.respond(_ok())
    async with exchange.async_client() as native:

        def client(provider: _AsyncOwned) -> Any:
            return package.AsyncClient(
                http_client=native,
                options=options.ClientOptions(auth=auth.AuthConfig({"bearer": owned(provider)}), cleanup_timeout=0.05),
            )

        lines.append(f"  async aclose with a failing owned provider = {await _aclosed(client(failing).aclose)}")
        api = client(slow)
        lines.append(f"  async aclose outlived by an owned provider = {await _aclosed(api.aclose)}")
        closing.set()
        lines.append(f"  async aclose once the provider closed = {await _aclosed(api.aclose)}")
        api = client(held)
        call = asyncio.create_task(_aoutcome(api.auth.with_response.bearer))
        while not held.gets:
            await asyncio.sleep(0)
        lines.append(f"  async aclose during a call outlasting it = {await _aclosed(api.aclose)}")
        getting.set()
        lines.append(f"  async outlasting call = {await call}")
        lines.append(f"  async aclose once the call ended = {await _aclosed(api.aclose)}")
    lines.append(f"    closes={failing.closes, slow.closes, held.closes}")


def _moved(location: str) -> Callable[[Any], Any]:
    return lambda _: httpx2.Response(307, headers={"Location": location, "Content-Length": "0"})


def _redirects(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    token = auth.StaticTokenProvider(auth.AccessToken("token"))
    key = auth.StaticCredentialProvider(auth.ApiKeyCredential("query-secret"))
    both = (_ORIGIN, _OTHER)
    for label, config, method, location in (
        ("credentials kept to their origin", auth.AuthConfig({"bearer": token}), "bearer", f"{_OTHER}/bearer"),
        (
            "signer kept to its origin",
            auth.AuthConfig({"bearer": token}, allowed_origins=both, signers=(_Signer(auth, origins=(_ORIGIN,)),)),
            "bearer",
            f"{_OTHER}/bearer",
        ),
        (
            "query key placed again after a redirect",
            auth.AuthConfig({"query_key": key}, allowed_origins=both),
            "api_key_query",
            f"{_OTHER}/api-key/query?api_key=planted&page=1",
        ),
        (
            "self redirect planting the query key",
            auth.AuthConfig({"query_key": key}),
            "api_key_query",
            f"{_ORIGIN}/api-key/query?api_key=planted",
        ),
        (
            "form-encoded planted query key",
            auth.AuthConfig(
                {"spaced_query": key}, send_on_anonymous=True, anonymous_schemes=("spaced_query",), allowed_origins=both
            ),
            "anonymous",
            f"{_OTHER}/anonymous?api+key=planted",
        ),
    ):
        exchange = Exchange(lines)
        exchange.respond(_moved(location), _ok())
        with (
            exchange.client() as native,
            package.Client(
                http_client=native,
                options=options.ClientOptions(
                    auth=config, redirects=options.RedirectOptions(enabled=True, allowed_origins=(_OTHER,))
                ),
            ) as api,
        ):
            record(lines, label, lambda api=api, method=method: _outcome(getattr(api.auth.with_response, method)))


def _signatures(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    fields = auth.SignatureFields
    for label, signer in (
        ("signature query placed", _Signer(auth, fields((("X-Sig", "signed"),), (("sig", "v"),)))),
        ("signer raises", _Signer(auth, failure=RuntimeError("sign failed"))),
        ("signature header undeclared", _Signer(auth, fields((("X-Other", "v"),), ()))),
        ("signature header CRLF", _Signer(auth, fields((("X-Sig", "a\r\nInjected: 1"),), ()))),
        ("signature header leading space", _Signer(auth, fields((("X-Sig", " signed"),), ()))),
        ("signature header name folding to a token", _Signer(auth, fields((("\u212a-Sig", "v"),), ()), managed=("K-Sig",))),
        ("signature query undeclared", _Signer(auth, fields((), (("other", "v"),)))),
        ("signature query lone surrogate", _Signer(auth, fields((), (("sig", "\ud800"),)))),
        ("signer returns a mapping", _Signer(auth, {"X-Sig": "v"})),
        ("signer returns a coroutine", _Signer(auth, _later(None))),
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
        self.capabilities = auth.SignerCapabilities((), headers, query, False)


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
    _signatures(package, auth, options, lines)
