"""Immutable credentials and declarations, with explicit static and environment providers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Generic, Literal, Protocol, TypeAlias, TypeVar, final

from typing_extensions import TypeIs

from ..model_codecs.unset import UNSET, Unset
from .errors import AuthConfigurationError, ConfigurationError, SigningConfigurationError
from .responses import HeadersView  # noqa: TC001 - Public annotations support get_type_hints().
from .scopes import scope_tuple
from .timing import CancelToken, Deadline

__all__ = (
    "AccessToken",
    "ApiKeyCredential",
    "AsyncCloseableCredentialProvider",
    "AsyncCredentialProvider",
    "AsyncEnvironmentCredentialProvider",
    "AsyncRefreshableTokenProvider",
    "AsyncRequestSigner",
    "AsyncStaticCredentialProvider",
    "AsyncStaticTokenProvider",
    "AuthConfig",
    "BasicCredential",
    "BearerCredential",
    "CloseableCredentialProvider",
    "CredentialContext",
    "CredentialMaterial",
    "CredentialProvider",
    "CredentialProviderInput",
    "EnvironmentCredentialProvider",
    "OwnedCredentialProvider",
    "RefreshableTokenProvider",
    "RequestSigner",
    "SignatureFields",
    "SignerCapabilities",
    "SigningInput",
    "StaticCredentialProvider",
    "StaticTokenProvider",
    "TokenVersion",
)


def _typed(value: object, types: tuple[type[object], ...], path: tuple[str, ...]) -> None:
    if not isinstance(value, types):
        raise AuthConfigurationError(field_path=path, condition="invalid_type")


def _scopes(value: object, name: str) -> tuple[str, ...]:
    try:
        return scope_tuple(value)
    except ValueError:
        raise AuthConfigurationError(field_path=(name,), condition="invalid_scope") from None


def _sequence(value: object) -> TypeIs[tuple[object, ...] | list[object]]:
    return isinstance(value, (tuple, list))


def _strings(
    value: object, path: tuple[str, ...], error_type: type[ConfigurationError] = AuthConfigurationError
) -> tuple[str, ...]:
    if not _sequence(value):
        raise error_type(field_path=path, condition="invalid_type")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise error_type(field_path=path, condition="invalid_type")
        result.append(item)
    return tuple(result)


@final
@dataclass(frozen=True, slots=True, eq=False)
class TokenVersion:
    """An identity belonging to one provider's published material, never a persistent revision."""


@dataclass(frozen=True, slots=True)
class AccessToken:
    """A token with declared expiry and either unknown or confirmed granted scopes."""

    value: str = field(repr=False)
    token_type: str = field(default="Bearer", repr=False)
    expires_at: datetime | None = field(default=None, repr=False)
    scopes: tuple[str, ...] | None = field(default=None, repr=False)
    audience: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Validate scalar shapes and freeze confirmed scope information without inferring grants."""
        _typed(self.value, (str,), ("value",))
        _typed(self.token_type, (str,), ("token_type",))
        _typed(self.expires_at, (datetime, type(None)), ("expires_at",))
        _typed(self.audience, (str, type(None)), ("audience",))
        if self.scopes is not None:
            object.__setattr__(self, "scopes", _scopes(self.scopes, "scopes"))


@dataclass(frozen=True, slots=True)
class ApiKeyCredential:
    """An API key whose wire name and position belong to its security scheme."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        """Retain the string unchanged for the selected scheme's wire validation."""
        _typed(self.value, (str,), ("value",))


@dataclass(frozen=True, slots=True)
class BasicCredential:
    """A username and password for explicitly selected UTF-8 HTTP Basic authentication."""

    username: str = field(repr=False)
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        """Keep both strings unchanged until Basic encoding."""
        _typed(self.username, (str,), ("username",))
        _typed(self.password, (str,), ("password",))


@dataclass(frozen=True, slots=True)
class BearerCredential:
    """Bearer material paired with the exact version that an attempt may invalidate."""

    token: AccessToken = field(repr=False)
    version: TokenVersion = field(repr=False)

    def __post_init__(self) -> None:
        """Require the public token and nominal version records."""
        _typed(self.token, (AccessToken,), ("token",))
        _typed(self.version, (TokenVersion,), ("version",))


CredentialMaterial: TypeAlias = ApiKeyCredential | BasicCredential | BearerCredential


@dataclass(frozen=True, slots=True, kw_only=True)
class CredentialContext:
    """The selected scheme and constraints of the current caller's resource hop."""

    scheme: str = field(repr=False)
    required_scopes: tuple[str, ...] = field(repr=False)
    audience: str | None = field(repr=False)
    origin: str = field(repr=False)
    deadline: Deadline | None = field(repr=False)
    cancel_token: CancelToken | None = field(repr=False)

    def __post_init__(self) -> None:
        """Freeze requirements and retain the caller's exact deadline and cancellation references."""
        _typed(self.scheme, (str,), ("scheme",))
        _typed(self.audience, (str, type(None)), ("audience",))
        _typed(self.origin, (str,), ("origin",))
        _typed(self.deadline, (Deadline, type(None)), ("deadline",))
        _typed(self.cancel_token, (CancelToken, type(None)), ("cancel_token",))
        object.__setattr__(self, "required_scopes", _scopes(self.required_scopes, "required_scopes"))


class CredentialProvider(Protocol):
    """A synchronous source of material for a selected security scheme."""

    def get(self, context: CredentialContext) -> CredentialMaterial:
        """Return material under this caller's constraints."""
        ...


class AsyncCredentialProvider(Protocol):
    """An asynchronous source of material for a selected security scheme."""

    async def get(self, context: CredentialContext) -> CredentialMaterial:
        """Return material after one await under this caller's constraints."""
        ...


class RefreshableTokenProvider(CredentialProvider, Protocol):
    """A bearer provider with explicit version invalidation and refresh capabilities."""

    def get(self, context: CredentialContext) -> BearerCredential:
        """Return the currently usable bearer material."""
        ...

    def invalidate(self, version: TokenVersion) -> None:
        """Invalidate only this provider's currently published matching version."""
        ...

    def refresh(self, context: CredentialContext) -> BearerCredential:
        """Return refreshed material under this caller's constraints."""
        ...


class AsyncRefreshableTokenProvider(AsyncCredentialProvider, Protocol):
    """An asynchronous bearer provider with explicit invalidation and refresh capabilities."""

    async def get(self, context: CredentialContext) -> BearerCredential:
        """Return the currently usable bearer material."""
        ...

    async def invalidate(self, version: TokenVersion) -> None:
        """Invalidate only this provider's currently published matching version."""
        ...

    async def refresh(self, context: CredentialContext) -> BearerCredential:
        """Return refreshed material under this caller's constraints."""
        ...


class CloseableCredentialProvider(CredentialProvider, Protocol):
    """A synchronous provider whose close capability permits explicit ownership transfer."""

    def close(self) -> None:
        """Close the provider and its owned resources."""
        ...


class AsyncCloseableCredentialProvider(AsyncCredentialProvider, Protocol):
    """An asynchronous provider whose close capability permits explicit ownership transfer."""

    async def aclose(self) -> None:
        """Close the provider and its owned resources."""
        ...


_CloseableProvider: TypeAlias = CloseableCredentialProvider | AsyncCloseableCredentialProvider
ProviderT_co = TypeVar("ProviderT_co", bound=_CloseableProvider, covariant=True)


@dataclass(frozen=True, slots=True)
class OwnedCredentialProvider(Generic[ProviderT_co]):
    """A readonly provider reference whose ownership is transferred to the root client."""

    provider: ProviderT_co = field(repr=False)


CredentialProviderInput: TypeAlias = (
    CredentialProvider
    | AsyncCredentialProvider
    | OwnedCredentialProvider[CloseableCredentialProvider]
    | OwnedCredentialProvider[AsyncCloseableCredentialProvider]
)


@dataclass(frozen=True, slots=True)
class SignerCapabilities:
    """The destinations, wire fields and body digest needed by a signer."""

    allowed_origins: tuple[str, ...] = field(repr=False)
    managed_headers: tuple[str, ...] = field(repr=False)
    managed_query: tuple[str, ...] = field(repr=False)
    requires_body_digest: bool = field(repr=False)

    def __post_init__(self) -> None:
        """Copy declared collections without choosing destinations or invoking a signer."""
        for name in ("allowed_origins", "managed_headers", "managed_query"):
            object.__setattr__(self, name, _strings(getattr(self, name), (name,), SigningConfigurationError))
        if type(self.requires_body_digest) is not bool:
            raise SigningConfigurationError(field_path=("requires_body_digest",), condition="invalid_type")


@dataclass(frozen=True, slots=True)
class SigningInput:
    """The final unsigned request observed by a signer, without mutable request access."""

    method: str = field(repr=False)
    url: str = field(repr=False)
    origin: str = field(repr=False)
    query: bytes = field(repr=False)
    headers: HeadersView = field(repr=False)
    body_digest: bytes | None = field(repr=False)
    attempt_index: int = field(repr=False)
    hop_index: int = field(repr=False)


def _signature_pairs(value: object, name: str) -> tuple[tuple[str, str], ...]:
    if not _sequence(value):
        raise SigningConfigurationError(field_path=(name,), condition="invalid_type")
    pairs: list[tuple[str, str]] = []
    for item in value:
        match _strings(item, (name,), SigningConfigurationError):
            case first, second:
                pairs.append((first, second))
            case _:
                raise SigningConfigurationError(field_path=(name,), condition="invalid_value")
    return tuple(pairs)


@dataclass(frozen=True, slots=True)
class SignatureFields:
    """Ordered signature additions whose names must be declared by the signer."""

    headers: tuple[tuple[str, str], ...] = field(repr=False)
    query: tuple[tuple[str, str], ...] = field(repr=False)

    def __post_init__(self) -> None:
        """Freeze signature pairs; the binding validates their declared names and wire safety."""
        object.__setattr__(self, "headers", _signature_pairs(self.headers, "headers"))
        object.__setattr__(self, "query", _signature_pairs(self.query, "query"))


class RequestSigner(Protocol):
    """A synchronous signer of a finalized unsigned request."""

    @property
    def capabilities(self) -> SignerCapabilities:
        """Declare immutable destinations, managed names and digest requirements."""
        ...

    def sign(self, request: SigningInput) -> SignatureFields:
        """Return fresh signature additions for this request."""
        ...


class AsyncRequestSigner(Protocol):
    """An asynchronous signer of a finalized unsigned request."""

    @property
    def capabilities(self) -> SignerCapabilities:
        """Declare immutable destinations, managed names and digest requirements."""
        ...

    async def sign(self, request: SigningInput) -> SignatureFields:
        """Return fresh signature additions after one await."""
        ...


def _mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _owned(value: object) -> TypeIs[OwnedCredentialProvider[_CloseableProvider]]:
    return isinstance(value, OwnedCredentialProvider)


def _provider(value: object) -> TypeIs[CredentialProviderInput]:
    return _owned(value) or callable(getattr(value, "get", None))


def _signer(value: object) -> TypeIs[RequestSigner | AsyncRequestSigner]:
    return callable(getattr(value, "sign", None))


def _credentials(value: object) -> Mapping[str, CredentialProviderInput]:
    path = ("auth", "credentials")
    if not _mapping(value):
        raise AuthConfigurationError(field_path=path, condition="invalid_type")
    result: dict[str, CredentialProviderInput] = {}
    for name, provider in value.items():
        if not isinstance(name, str) or not _provider(provider):
            raise AuthConfigurationError(field_path=path, condition="invalid_type")
        result[name] = provider
    return MappingProxyType(result)


def _signers(value: object) -> tuple[RequestSigner | AsyncRequestSigner, ...]:
    path = ("auth", "signers")
    if not _sequence(value):
        raise AuthConfigurationError(field_path=path, condition="invalid_type")
    result: list[RequestSigner | AsyncRequestSigner] = []
    for signer in value:
        if not _signer(signer):
            raise AuthConfigurationError(field_path=path, condition="invalid_type")
        result.append(signer)
    return tuple(result)


def _count(value: object, path: tuple[str, ...]) -> None:
    if type(value) is not int or value < 0:
        raise AuthConfigurationError(field_path=path, condition="out_of_range")


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """Explicit provider and signer configuration that replaces an inherited auth choice as a whole."""

    credentials: Mapping[str, CredentialProviderInput] = field(repr=False)
    selection: int | Unset = field(default=UNSET, kw_only=True, repr=False)
    allowed_origins: tuple[str, ...] = field(default=(), kw_only=True, repr=False)
    max_token_exchanges: int = field(default=2, kw_only=True)
    send_on_anonymous: bool = field(default=False, kw_only=True)
    anonymous_schemes: tuple[str, ...] = field(default=(), kw_only=True, repr=False)
    signers: tuple[RequestSigner | AsyncRequestSigner, ...] = field(default=(), kw_only=True, repr=False)
    _owned_providers: tuple[_CloseableProvider, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Freeze configuration and collect owned identities without callbacks or mode conversion."""
        credentials = _credentials(self.credentials)
        object.__setattr__(self, "credentials", credentials)
        object.__setattr__(self, "allowed_origins", _strings(self.allowed_origins, ("auth", "allowed_origins")))
        object.__setattr__(self, "anonymous_schemes", _strings(self.anonymous_schemes, ("auth", "anonymous_schemes")))
        object.__setattr__(self, "signers", _signers(self.signers))
        if not isinstance(self.selection, Unset):
            _count(self.selection, ("auth", "selection"))
        _count(self.max_token_exchanges, ("auth", "max_token_exchanges"))
        _typed(self.send_on_anonymous, (bool,), ("auth", "send_on_anonymous"))
        owned: list[_CloseableProvider] = []
        identities: set[int] = set()
        for configured in credentials.values():
            if _owned(configured) and (identity := id(provider := configured.provider)) not in identities:
                identities.add(identity)
                owned.append(provider)
        object.__setattr__(self, "_owned_providers", tuple(owned))


def owned_providers(config: AuthConfig) -> tuple[_CloseableProvider, ...]:
    """Return the configuration's precomputed owned providers in declaration order."""
    return config._owned_providers  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001


def _material(value: object) -> CredentialMaterial:
    match value:
        case ApiKeyCredential() | BasicCredential() | BearerCredential():
            return value
        case _:
            raise AuthConfigurationError(field_path=("material",), condition="invalid_type")


class _StaticCredentials:
    __slots__ = ("_material",)

    def __init__(self, material: CredentialMaterial) -> None:
        self._material = _material(material)


class StaticCredentialProvider(_StaticCredentials):
    """A synchronous provider returning one fixed material object."""

    __slots__ = ()

    def get(self, context: CredentialContext) -> CredentialMaterial:
        """Return the configured material without environment or network access."""
        del context
        return self._material


class AsyncStaticCredentialProvider(_StaticCredentials):
    """An asynchronous provider returning one fixed material object without worker delegation."""

    __slots__ = ()

    async def get(self, context: CredentialContext) -> CredentialMaterial:
        """Return the configured material without environment or network access."""
        del context
        return self._material


class _StaticTokens:
    __slots__ = ("_material",)

    def __init__(self, token: AccessToken) -> None:
        self._material = BearerCredential(token, TokenVersion())


class StaticTokenProvider(_StaticTokens):
    """A fixed synchronous bearer token with one stable version and no refresh capability."""

    __slots__ = ()

    def get(self, context: CredentialContext) -> BearerCredential:
        """Return the original token and version without changing scope knowledge."""
        del context
        return self._material


class AsyncStaticTokenProvider(_StaticTokens):
    """A fixed asynchronous bearer token with one stable version and no refresh capability."""

    __slots__ = ()

    async def get(self, context: CredentialContext) -> BearerCredential:
        """Return the original token and version without changing scope knowledge."""
        del context
        return self._material


class _EnvironmentCredentials:
    __slots__ = ("_kind", "_last", "_variable_name")

    def __init__(self, variable_name: str, *, kind: Literal["api_key", "bearer"] = "api_key") -> None:
        _typed(variable_name, (str,), ("variable_name",))
        if not variable_name or "=" in variable_name or "\0" in variable_name:
            raise AuthConfigurationError(field_path=("variable_name",), condition="invalid_value")
        match kind:
            case "api_key" | "bearer":
                self._kind = kind
            case _:
                raise AuthConfigurationError(field_path=("kind",), condition="invalid_value")
        self._variable_name = variable_name
        self._last: tuple[str, TokenVersion] | None = None

    def _read(self) -> CredentialMaterial:
        if (value := os.environ.get(self._variable_name)) is None:
            raise AuthConfigurationError(field_path=("variable_name",), condition="missing_value")
        if self._kind == "api_key":
            return ApiKeyCredential(value)
        if (last := self._last) is None or last[0] != value:
            last = self._last = (value, TokenVersion())
        return BearerCredential(AccessToken(value), last[1])


class EnvironmentCredentialProvider(_EnvironmentCredentials):
    """A synchronous provider reading one explicitly named environment variable per get."""

    __slots__ = ()

    def get(self, context: CredentialContext) -> CredentialMaterial:
        """Read the current value without inferring Basic credentials or bearer grants."""
        del context
        return self._read()


class AsyncEnvironmentCredentialProvider(_EnvironmentCredentials):
    """An asynchronous provider reading one explicitly named environment variable per get."""

    __slots__ = ()

    async def get(self, context: CredentialContext) -> CredentialMaterial:
        """Read the current value without worker delegation or inferred bearer grants."""
        del context
        return self._read()
