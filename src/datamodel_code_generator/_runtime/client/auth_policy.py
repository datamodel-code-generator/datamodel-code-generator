"""Bound credential selection, validated callback results, and per-hop wire ownership."""

from __future__ import annotations

import base64
import inspect
import re
from dataclasses import dataclass
from time import monotonic, time
from typing import TYPE_CHECKING, Final, Literal, TypeVar
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from typing_extensions import TypeIs

from ..model_codecs.unset import Unset
from .auth import (
    ApiKeyCredential,
    BasicCredential,
    BearerCredential,
    OwnedCredentialProvider,
    SignatureFields,
    SignerCapabilities,
)
from .auth_challenges import invalid_token
from .errors import (
    AuthConfigurationError,
    AuthProviderExecutionError,
    AuthRefreshError,
    DeliveryState,
    InsufficientScopeError,
    SigningConfigurationError,
    SigningExecutionError,
    TokenExpiredError,
)
from .responses import HeadersView
from .security import SecurityRequirement, SecurityScheme, UnavailableSecurityScheme
from .transports import PreparedRequest
from .urls import URLValidationError, canonical_origin

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from .auth import (
        AccessToken,
        AsyncCloseableCredentialProvider,
        AsyncCredentialProvider,
        AsyncRefreshableTokenProvider,
        AsyncRequestSigner,
        AuthConfig,
        CloseableCredentialProvider,
        CredentialContext,
        CredentialMaterial,
        CredentialProvider,
        CredentialProviderInput,
        RefreshableTokenProvider,
        RequestSigner,
        SigningInput,
        TokenVersion,
    )
    from .bodies import AsyncBodyAttempt, BodyAttempt
    from .options import HeaderPatch, QueryPatch
    from .security import SecurityBinding, SecuritySchemeEntry
    from .urls import Origin

__all__ = ("invalid_token",)

BodyT = TypeVar("BodyT", bound="BodyAttempt | AsyncBodyAttempt")
_NAME: Final = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_ORIGIN: Final = re.compile(r"https?://[^\s/?#\\\x00-\x1f\x7f]+", re.IGNORECASE)
_HEADER_VALUE: Final = re.compile(r"[^\x00-\x08\x0a-\x1f\x7f\ud800-\udfff]*")
_COOKIE_VALUE: Final = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*")
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")
_RESERVED: Final = frozenset({"host", "content-length", "transfer-encoding"})


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class BoundCredential:
    """One selected synchronous provider and its fixed material requirement."""

    scheme: SecurityScheme
    required_scopes: tuple[str, ...]
    provider: CredentialProvider
    refreshable: RefreshableTokenProvider | None


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class AsyncBoundCredential:
    """One selected asynchronous provider and its fixed material requirement."""

    scheme: SecurityScheme
    required_scopes: tuple[str, ...]
    provider: AsyncCredentialProvider
    refreshable: AsyncRefreshableTokenProvider | None


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class BoundSigner:
    """A synchronous signer with capabilities observed once at binding."""

    signer: RequestSigner
    capabilities: SignerCapabilities
    allowed_origins: frozenset[Origin]
    index: int


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class AsyncBoundSigner:
    """An asynchronous signer with capabilities observed once at binding."""

    signer: AsyncRequestSigner
    capabilities: SignerCapabilities
    allowed_origins: frozenset[Origin]
    index: int


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class _BoundAuth:
    allowed_origins: frozenset[Origin]
    managed_headers: frozenset[str]
    managed_query: frozenset[str]
    managed_cookies: frozenset[str]
    requires_body_digest: bool


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class BoundAuth(_BoundAuth):
    """A call's synchronous auth selection, without acquired material."""

    credentials: tuple[BoundCredential, ...]
    signers: tuple[BoundSigner, ...]


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class AsyncBoundAuth(_BoundAuth):
    """A call's asynchronous auth selection, without acquired material."""

    credentials: tuple[AsyncBoundCredential, ...]
    signers: tuple[AsyncBoundSigner, ...]


@dataclass(frozen=True, slots=True, repr=False)
class AcquiredCredential:
    """Validated material with its one monotonic expiry conversion."""

    material: CredentialMaterial
    expires_at: float | None


@dataclass(frozen=True, slots=True, repr=False)
class HopCredentials:
    """Synchronous hop material in the selected binding order."""

    values: tuple[AcquiredCredential, ...]


@dataclass(frozen=True, slots=True, repr=False)
class AsyncHopCredentials:
    """Asynchronous hop material in the selected binding order."""

    values: tuple[AcquiredCredential, ...]


def _method(value: object, name: str, *, asynchronous: bool) -> bool:
    callback = getattr(value, name, None)
    return callable(callback) and inspect.iscoroutinefunction(callback) == asynchronous


def _sync_provider(value: object) -> TypeIs[CredentialProvider]:
    return _method(value, "get", asynchronous=False)


def _async_provider(value: object) -> TypeIs[AsyncCredentialProvider]:
    return _method(value, "get", asynchronous=True)


def is_sync_closeable(value: object) -> TypeIs[CloseableCredentialProvider]:
    """Validate synchronous ownership capability only at an adoption boundary."""
    return _sync_provider(value) and _method(value, "close", asynchronous=False)


def is_async_closeable(value: object) -> TypeIs[AsyncCloseableCredentialProvider]:
    """Validate asynchronous ownership capability only at an adoption boundary."""
    return _async_provider(value) and _method(value, "aclose", asynchronous=True)


def _sync_refreshable(value: object) -> TypeIs[RefreshableTokenProvider]:
    return all(_method(value, name, asynchronous=False) for name in ("get", "invalidate", "refresh"))


def _async_refreshable(value: object) -> TypeIs[AsyncRefreshableTokenProvider]:
    return all(_method(value, name, asynchronous=True) for name in ("get", "invalidate", "refresh"))


def _sync_signer(value: object) -> TypeIs[RequestSigner]:
    return _method(value, "sign", asynchronous=False)


def _async_signer(value: object) -> TypeIs[AsyncRequestSigner]:
    return _method(value, "sign", asynchronous=True)


def _provider(value: CredentialProviderInput) -> CredentialProvider | AsyncCredentialProvider:
    return value.provider if isinstance(value, OwnedCredentialProvider) else value


def validate_auth_mode(config: AuthConfig, *, asynchronous: bool) -> None:
    """Refuse incompatible callback and ownership modes before executing user code."""
    for value in config.credentials.values():
        provider = _provider(value)
        if isinstance(value, OwnedCredentialProvider):
            valid = is_async_closeable(provider) if asynchronous else is_sync_closeable(provider)
        else:
            valid = _async_provider(provider) if asynchronous else _sync_provider(provider)
        if not valid:
            raise AuthConfigurationError(field_path=("auth", "credentials"), condition="invalid_mode")
        if any(getattr(provider, name, None) is not None for name in ("invalidate", "refresh")):
            valid = _async_refreshable(provider) if asynchronous else _sync_refreshable(provider)
            if not valid:
                raise AuthConfigurationError(field_path=("auth", "credentials"), condition="invalid_refresh_capability")
    for signer in config.signers:
        if not (_async_signer(signer) if asynchronous else _sync_signer(signer)):
            raise SigningConfigurationError(field_path=("auth", "signers"), condition="invalid_mode")


def _origins(
    values: tuple[str, ...], error: type[AuthConfigurationError | SigningConfigurationError]
) -> frozenset[Origin]:
    try:
        if not all(_ORIGIN.fullmatch(value) for value in values):
            raise error(field_path=("auth", "allowed_origins"), condition="invalid_origin")
        return frozenset(canonical_origin(value) for value in values)
    except URLValidationError as cause:
        raise error(field_path=("auth", "allowed_origins"), condition="invalid_origin", cause=cause) from None


def _scheme(name: str, catalogue: tuple[SecuritySchemeEntry, ...]) -> SecurityScheme:
    for scheme in catalogue:
        if scheme.name == name:
            if isinstance(scheme, UnavailableSecurityScheme):
                raise AuthConfigurationError(field_path=("auth", "credentials", name), condition="unavailable_scheme")
            return scheme
    raise AuthConfigurationError(field_path=("auth", "credentials", name), condition="unknown_scheme")


def _requirements(
    config: AuthConfig, security: SecurityBinding | None, catalogue: tuple[SecuritySchemeEntry, ...]
) -> tuple[SecurityRequirement, ...] | None:
    schemes = security.schemes if security is not None else catalogue
    for name in (*config.credentials, *config.anonymous_schemes):
        _scheme(name, schemes)
    alternatives = () if security is None else security.alternatives
    selected: tuple[SecurityRequirement, ...] | None = ()
    if not isinstance(config.selection, Unset) and len(alternatives) > 1:
        if config.selection >= len(alternatives):
            raise AuthConfigurationError(field_path=("auth", "selection"), condition="out_of_range")
        selected = alternatives[config.selection]
        if not all(item.scheme.name in config.credentials for item in selected):
            raise AuthConfigurationError(field_path=("auth", "credentials"), condition="missing_credentials")
    elif alternatives:
        selected = next(
            (group for group in alternatives if all(item.scheme.name in config.credentials for item in group)), None
        )
        if selected is None:
            raise AuthConfigurationError(field_path=("auth", "credentials"), condition="missing_credentials")
    if selected:
        return selected
    if not config.send_on_anonymous:
        return None
    selected = tuple(
        SecurityRequirement(scheme=_scheme(name, schemes), required_scopes=()) for name in config.anonymous_schemes
    )
    if not all(item.scheme.name in config.credentials for item in selected):
        raise AuthConfigurationError(field_path=("auth", "anonymous_schemes"), condition="missing_credentials")
    if not selected and not config.signers:
        raise AuthConfigurationError(field_path=("auth", "send_on_anonymous"), condition="empty_selection")
    return selected


def _capabilities(value: object) -> SignerCapabilities:
    if not isinstance(value, SignerCapabilities):
        raise SigningConfigurationError(field_path=("auth", "signers"), condition="invalid_capabilities")
    if any(_NAME.fullmatch(name) is None for name in value.managed_headers) or any(
        not name for name in value.managed_query
    ):
        raise SigningConfigurationError(field_path=("auth", "signers"), condition="invalid_name")
    return value


def _managed(
    credentials: tuple[SecurityRequirement, ...], capabilities: tuple[SignerCapabilities, ...]
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    headers: set[str] = set()
    query: set[str] = set()
    cookies: set[str] = set()
    for requirement in credentials:
        scheme = requirement.scheme
        names = {"header": headers, "query": query, "cookie": cookies}[scheme.location]
        name = scheme.wire_name.lower() if scheme.location == "header" else scheme.wire_name
        if name in names or (scheme.location == "header" and name in _RESERVED):
            raise AuthConfigurationError(field_path=("auth", "credentials"), condition="name_collision")
        names.add(name)
    if cookies and "cookie" in headers:
        raise AuthConfigurationError(field_path=("auth", "credentials"), condition="name_collision")
    for signer in capabilities:
        for name in signer.managed_headers:
            key = name.lower()
            if key in headers or key in _RESERVED or (cookies and key == "cookie"):
                raise SigningConfigurationError(field_path=("auth", "signers"), condition="name_collision")
            headers.add(key)
        for name in signer.managed_query:
            if name in query:
                raise SigningConfigurationError(field_path=("auth", "signers"), condition="name_collision")
            query.add(name)
    return frozenset(headers), frozenset(query), frozenset(cookies)


def bind_auth(
    config: AuthConfig, security: SecurityBinding | None, catalogue: tuple[SecuritySchemeEntry, ...]
) -> BoundAuth | None:
    """Select and snapshot synchronous authentication without invoking callbacks."""
    if (requirements := _requirements(config, security, catalogue)) is None:
        return None
    origins = _origins(config.allowed_origins, AuthConfigurationError)
    signers: list[BoundSigner] = []
    for index, signer in enumerate(config.signers):
        assert _sync_signer(signer)
        capabilities = _capabilities(signer.capabilities)
        signers.append(
            BoundSigner(
                signer=signer,
                capabilities=capabilities,
                allowed_origins=_origins(capabilities.allowed_origins, SigningConfigurationError),
                index=index,
            )
        )
    headers, query, cookies = _managed(requirements, tuple(signer.capabilities for signer in signers))
    credentials: list[BoundCredential] = []
    for requirement in requirements:
        provider = _provider(config.credentials[requirement.scheme.name])
        assert _sync_provider(provider)
        credentials.append(
            BoundCredential(
                scheme=requirement.scheme,
                required_scopes=requirement.required_scopes,
                provider=provider,
                refreshable=provider if _sync_refreshable(provider) else None,
            )
        )
    return BoundAuth(
        credentials=tuple(credentials),
        signers=tuple(signers),
        allowed_origins=origins,
        managed_headers=headers,
        managed_query=query,
        managed_cookies=cookies,
        requires_body_digest=any(signer.capabilities.requires_body_digest for signer in signers),
    )


def bind_async_auth(
    config: AuthConfig, security: SecurityBinding | None, catalogue: tuple[SecuritySchemeEntry, ...]
) -> AsyncBoundAuth | None:
    """Select and snapshot asynchronous authentication without invoking callbacks."""
    if (requirements := _requirements(config, security, catalogue)) is None:
        return None
    origins = _origins(config.allowed_origins, AuthConfigurationError)
    signers: list[AsyncBoundSigner] = []
    for index, signer in enumerate(config.signers):
        assert _async_signer(signer)
        capabilities = _capabilities(signer.capabilities)
        signers.append(
            AsyncBoundSigner(
                signer=signer,
                capabilities=capabilities,
                allowed_origins=_origins(capabilities.allowed_origins, SigningConfigurationError),
                index=index,
            )
        )
    headers, query, cookies = _managed(requirements, tuple(signer.capabilities for signer in signers))
    credentials: list[AsyncBoundCredential] = []
    for requirement in requirements:
        provider = _provider(config.credentials[requirement.scheme.name])
        assert _async_provider(provider)
        credentials.append(
            AsyncBoundCredential(
                scheme=requirement.scheme,
                required_scopes=requirement.required_scopes,
                provider=provider,
                refreshable=provider if _async_refreshable(provider) else None,
            )
        )
    return AsyncBoundAuth(
        credentials=tuple(credentials),
        signers=tuple(signers),
        allowed_origins=origins,
        managed_headers=headers,
        managed_query=query,
        managed_cookies=cookies,
        requires_body_digest=any(signer.capabilities.requires_body_digest for signer in signers),
    )


def authorize_hop(
    bound: BoundAuth | AsyncBoundAuth, *, origin: Origin, server_origin: Origin | None, raw: bool
) -> None:
    """Require independent credential and signer authority for the actual destination."""
    allowed = origin in bound.allowed_origins if bound.allowed_origins else not raw and origin == server_origin
    if not allowed:
        raise AuthConfigurationError(field_path=("auth", "allowed_origins"), condition="origin_denied")
    for signer in bound.signers:
        allowed = origin in signer.allowed_origins if signer.allowed_origins else not raw and origin == server_origin
        if not allowed:
            raise SigningConfigurationError(field_path=("auth", "signers"), condition="origin_denied")


def validate_patches(bound: BoundAuth | AsyncBoundAuth, headers: HeaderPatch, query: QueryPatch) -> None:
    """Reject generic replacement or deletion of active authentication fields, including a managed cookie."""
    if (
        any(name.lower() in bound.managed_headers for name, _ in headers)
        or any(name in bound.managed_query for name, _ in query)
        or any(
            part.partition("=")[0].strip() in bound.managed_cookies
            for name, value in headers
            if value is not None and name.lower() == "cookie"
            for part in value.split(";")
        )
    ):
        raise AuthConfigurationError(field_path=("auth",), condition="managed_field")


def validate_ownership(
    bound: BoundAuth | AsyncBoundAuth,
    *,
    headers: Iterable[str] = (),
    query: Iterable[str] = (),
    cookies: Iterable[str] = (),
) -> None:
    """Refuse active owners that overlap ordinary operation or SDK-owned positions."""
    if (
        any(name.lower() in bound.managed_headers for name in headers)
        or any(name in bound.managed_query for name in query)
        or any(name in bound.managed_cookies for name in cookies)
    ):
        raise AuthConfigurationError(field_path=("auth",), condition="name_collision")


def _expiry(token: AccessToken) -> float | None:
    if (expires := token.expires_at) is None:
        return None
    try:
        timestamp = None if expires.utcoffset() is None else expires.timestamp()
    except (TypeError, ValueError) as cause:
        raise TokenExpiredError(
            condition="invalid_expiry", delivery_state=DeliveryState.NOT_SENT, cause=cause
        ) from None
    if timestamp is None:
        raise TokenExpiredError(condition="invalid_expiry", delivery_state=DeliveryState.NOT_SENT)
    if (remaining := timestamp - time()) <= 0:
        raise TokenExpiredError(condition="expired", expires_at=expires, delivery_state=DeliveryState.NOT_SENT)
    return monotonic() + remaining


def _material(value: object, scheme: SecurityScheme, context: CredentialContext) -> AcquiredCredential:
    if isinstance(value, ApiKeyCredential) and scheme.kind == "api_key":
        return AcquiredCredential(value, None)
    if isinstance(value, BasicCredential) and scheme.kind == "basic":
        return AcquiredCredential(value, None)
    if isinstance(value, BearerCredential) and scheme.kind == "bearer":
        token = value.token
        if token.token_type.lower() != "bearer":
            raise AuthConfigurationError(field_path=("auth", "token_type"), condition="unsupported_token_type")
        if token.scopes is not None and not set(context.required_scopes).issubset(token.scopes):
            raise InsufficientScopeError(
                required_scopes=context.required_scopes,
                granted_scopes=token.scopes,
                delivery_state=DeliveryState.NOT_SENT,
            )
        return AcquiredCredential(value, _expiry(token))
    if inspect.iscoroutine(value):
        value.close()
    raise AuthConfigurationError(field_path=("auth", "credentials", scheme.name), condition="invalid_material")


class _Wrapped:
    """Let classified failures of a callback through and wrap any other exception it raised."""

    __slots__ = ("_kept", "_wrap")

    def __init__(self, kept: tuple[type[Exception], ...], wrap: Callable[[Exception], Exception]) -> None:
        self._kept = kept
        self._wrap = wrap

    def __enter__(self) -> None:
        """Run the callback."""

    def __exit__(self, kind: object, error: BaseException | None, traceback: object) -> Literal[False]:
        """Wrap an unclassified exception, leaving interruptions and classified failures unchanged."""
        if isinstance(error, Exception) and not isinstance(error, self._kept):
            raise self._wrap(error) from None
        return False


_PROVIDER_KEPT: Final = (AuthConfigurationError, AuthRefreshError)
_GET: Final = _Wrapped(
    _PROVIDER_KEPT,
    lambda cause: AuthProviderExecutionError(callback="get", delivery_state=DeliveryState.NOT_SENT, cause=cause),
)
_INVALIDATE: Final = _Wrapped(
    _PROVIDER_KEPT,
    lambda cause: AuthProviderExecutionError(
        callback="invalidate", delivery_state=DeliveryState.RESPONSE_STARTED, cause=cause
    ),
)
_REFRESH: Final = _Wrapped(
    _PROVIDER_KEPT,
    lambda cause: AuthProviderExecutionError(callback="refresh", delivery_state=DeliveryState.NOT_SENT, cause=cause),
)


def get_credential(binding: BoundCredential, context: CredentialContext) -> AcquiredCredential:
    """Acquire one synchronous credential, retaining classified auth failures."""
    with _GET:
        value = binding.provider.get(context)
    return _material(value, binding.scheme, context)


async def aget_credential(binding: AsyncBoundCredential, context: CredentialContext) -> AcquiredCredential:
    """Acquire one asynchronous credential in the existing caller-owned operation."""
    with _GET:
        value = await binding.provider.get(context)
    return _material(value, binding.scheme, context)


def invalidate_credential(binding: BoundCredential, version: TokenVersion) -> None:
    """Invalidate only the version retained from the actual sent request."""
    assert binding.refreshable is not None
    with _INVALIDATE:
        binding.refreshable.invalidate(version)


async def ainvalidate_credential(binding: AsyncBoundCredential, version: TokenVersion) -> None:
    """Asynchronously invalidate the saved version without initiating acquisition."""
    assert binding.refreshable is not None
    with _INVALIDATE:
        await binding.refreshable.invalidate(version)


def refresh_credential(binding: BoundCredential, context: CredentialContext) -> AcquiredCredential:
    """Execute one explicitly admitted synchronous refresh callback."""
    assert binding.refreshable is not None
    with _REFRESH:
        value = binding.refreshable.refresh(context)
    return _material(value, binding.scheme, context)


async def arefresh_credential(binding: AsyncBoundCredential, context: CredentialContext) -> AcquiredCredential:
    """Execute one explicitly admitted asynchronous refresh callback."""
    assert binding.refreshable is not None
    with _REFRESH:
        value = await binding.refreshable.refresh(context)
    return _material(value, binding.scheme, context)


def credentials_expired(acquired: HopCredentials | AsyncHopCredentials, *, now: float) -> bool:
    """Check the already converted monotonic expiration boundaries of one hop."""
    return any(value.expires_at is not None and now >= value.expires_at for value in acquired.values)


def _wire_value(material: CredentialMaterial) -> str:
    if isinstance(material, ApiKeyCredential):
        return material.value
    if isinstance(material, BearerCredential):
        return f"Bearer {material.token.value}"
    if ":" in material.username or _CONTROL.search(material.username + material.password) is not None:
        raise AuthConfigurationError(field_path=("auth", "credentials"), condition="invalid_basic")
    try:
        encoded = f"{material.username}:{material.password}".encode()
    except UnicodeEncodeError as cause:
        raise AuthConfigurationError(
            field_path=("auth", "credentials"), condition="invalid_basic", cause=cause
        ) from None
    return "Basic " + base64.b64encode(encoded).decode("ascii")


def _query_url(url: str, added: tuple[tuple[str, str], ...], removed: frozenset[str] = frozenset()) -> str:
    if not added and not removed:
        return url
    parsed = urlsplit(url)
    parts = (
        [part for part in parsed.query.split("&") if unquote(part.partition("=")[0]) not in removed]
        if parsed.query
        else []
    )
    parts.extend(f"{quote(name, safe='-._~')}={quote(value, safe='-._~')}" for name, value in added)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "&".join(parts), parsed.fragment))


def strip_managed(request: PreparedRequest[BodyT], bound: BoundAuth | AsyncBoundAuth) -> PreparedRequest[BodyT]:
    """Remove managed query fields a redirect target carries, retaining unrelated encoded query atoms.

    An unsigned request never carries a managed header or cookie: patches, parameters, and raw requests naming one are
    refused before the first hop, and credentials and signatures are placed only on each outgoing copy.
    """
    return PreparedRequest(
        method=request.method,
        url=_query_url(request.url, (), bound.managed_query),
        headers=request.headers,
        body=request.body,
    )


def _cookie_fields(headers: list[tuple[str, str]], cookies: list[tuple[str, str]]) -> list[tuple[str, str]]:
    if not cookies:
        return headers
    values = [value for name, value in headers if name.lower() == "cookie"]
    values.extend(f"{name}={value}" for name, value in cookies)
    return [(name, value) for name, value in headers if name.lower() != "cookie"] + [("Cookie", "; ".join(values))]


def place_credentials(
    request: PreparedRequest[BodyT], bound: BoundAuth | AsyncBoundAuth, acquired: HopCredentials | AsyncHopCredentials
) -> PreparedRequest[BodyT]:
    """Place validated material only at the compiled scheme's exact outgoing position."""
    request = strip_managed(request, bound)
    headers = list(request.headers.items())
    query: list[tuple[str, str]] = []
    cookies: list[tuple[str, str]] = []
    for scheme, value in zip((binding.scheme for binding in bound.credentials), acquired.values, strict=True):
        text = _wire_value(value.material)
        if scheme.location == "header":
            if _NAME.fullmatch(scheme.wire_name) is None or _HEADER_VALUE.fullmatch(text) is None:
                raise AuthConfigurationError(
                    field_path=("auth", "credentials", scheme.name), condition="invalid_header"
                )
            headers.append((scheme.wire_name, text))
        elif scheme.location == "cookie":
            if _NAME.fullmatch(scheme.wire_name) is None or _COOKIE_VALUE.fullmatch(text) is None:
                raise AuthConfigurationError(
                    field_path=("auth", "credentials", scheme.name), condition="invalid_cookie"
                )
            cookies.append((scheme.wire_name, text))
        else:
            query.append((scheme.wire_name, text))
    try:
        url = _query_url(request.url, tuple(query))
    except UnicodeEncodeError as cause:
        raise AuthConfigurationError(
            field_path=("auth", "credentials"), condition="invalid_query", cause=cause
        ) from None
    return PreparedRequest(
        method=request.method, url=url, headers=HeadersView(_cookie_fields(headers, cookies)), body=request.body
    )


def _signature(value: object) -> SignatureFields:
    if isinstance(value, SignatureFields):
        return value
    if inspect.iscoroutine(value):
        value.close()
    raise SigningConfigurationError(field_path=("auth", "signers"), condition="invalid_result")


def _signing(signer_index: int) -> _Wrapped:
    return _Wrapped(
        (SigningConfigurationError, SigningExecutionError),
        lambda cause: SigningExecutionError(signer_index=signer_index, cause=cause),
    )


def sign_request(signer: RequestSigner, request: SigningInput, *, signer_index: int) -> SignatureFields:
    """Run a synchronous signer without converting native interruptions."""
    with _signing(signer_index):
        value = signer.sign(request)
    return _signature(value)


async def asign_request(signer: AsyncRequestSigner, request: SigningInput, *, signer_index: int) -> SignatureFields:
    """Run an asynchronous signer in the existing bounded operation task."""
    with _signing(signer_index):
        value = await signer.sign(request)
    return _signature(value)


def apply_signature(
    request: PreparedRequest[BodyT], fields: SignatureFields, capabilities: SignerCapabilities
) -> PreparedRequest[BodyT]:
    """Apply only declared ordered signature fields, encoding new query atoms once."""
    names = frozenset(name.lower() for name in capabilities.managed_headers)
    for name, value in fields.headers:
        if name.lower() not in names:
            raise SigningConfigurationError(field_path=("auth", "signers"), condition="undeclared_header")
        if _HEADER_VALUE.fullmatch(value) is None:
            raise SigningConfigurationError(field_path=("auth", "signers"), condition="invalid_header")
    if any(name not in capabilities.managed_query for name, _ in fields.query):
        raise SigningConfigurationError(field_path=("auth", "signers"), condition="undeclared_query")
    try:
        url = _query_url(request.url, fields.query)
    except UnicodeEncodeError as cause:
        raise SigningConfigurationError(
            field_path=("auth", "signers"), condition="invalid_query", cause=cause
        ) from None
    return PreparedRequest(
        method=request.method,
        url=url,
        headers=HeadersView((*request.headers.items(), *fields.headers)),
        body=request.body,
    )
