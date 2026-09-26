"""Authenticate secured operations: builtin credential helpers, explicit extractors, and one authorizer per router."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Generic, Literal, Protocol, TypeAlias

from fastapi import HTTPException
from fastapi.security import (
    APIKeyCookie,
    APIKeyHeader,
    APIKeyQuery,
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from typing_extensions import TypeIs, TypeVar

from ..model_codecs.parameters import split_cookies
from .errors import malformed_request

SecretT = TypeVar("SecretT")
SecretT_co = TypeVar("SecretT_co", covariant=True)
SecretT_contra = TypeVar("SecretT_contra", contravariant=True)
PrincipalT = TypeVar("PrincipalT")
PrincipalT_co = TypeVar("PrincipalT_co", covariant=True)
SchemeT = TypeVar("SchemeT", bound=str)
SchemeT_co = TypeVar("SchemeT_co", bound=str, covariant=True)
OperationT = TypeVar("OperationT", bound=str)
SchemeKind: TypeAlias = Literal["api_key", "basic", "bearer", "custom"]
ApiKeyLocation: TypeAlias = Literal["header", "query", "cookie"]
Authenticator: TypeAlias = Callable[[Request], Awaitable[object]]

_CHALLENGES: Final = {"api_key": "APIKey", "basic": "Basic", "bearer": "Bearer"}


class AuthConfigurationError(TypeError):
    """Reject an authorizer, credential extractors, or security plan that cannot authenticate every operation."""


def _redacted(name: str) -> str:
    return f"{name}(<redacted>)"


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class ApiKeySecret:
    """An API key read from its declared header, query parameter, or cookie."""

    value: str

    def __repr__(self) -> str:
        """Name the record without its secret."""
        return _redacted("ApiKeySecret")


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class BasicSecret:
    """An HTTP Basic username and password, decoded as ASCII by FastAPI's HTTPBasic."""

    username: str
    password: str

    def __repr__(self) -> str:
        """Name the record without its secret."""
        return _redacted("BasicSecret")


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class BearerSecret:
    """An HTTP Bearer token, also used for OAuth 2.0 and OpenID Connect schemes."""

    token: str

    def __repr__(self) -> str:
        """Name the record without its secret."""
        return _redacted("BearerSecret")


@dataclass(frozen=True, kw_only=True, repr=False)
class CustomSecret(Generic[SecretT_co]):
    """A credential a custom extractor read, kept as the extractor's own opaque value."""

    value: SecretT_co  # type: ignore[misc, unused-ignore]

    def __repr__(self) -> str:
        """Name the record without its value."""
        return _redacted("CustomSecret")


@dataclass(frozen=True, kw_only=True, repr=False)
class Credential(Generic[SecretT_co, SchemeT_co]):
    """The credential of one security scheme, named exactly as the source declares the scheme."""

    scheme_name: SchemeT_co  # type: ignore[misc, unused-ignore]
    payload: ApiKeySecret | BasicSecret | BearerSecret | CustomSecret[SecretT_co]

    @property
    def kind(self) -> SchemeKind:
        """Return the kind of secret the credential carries."""
        match self.payload:
            case ApiKeySecret():
                return "api_key"
            case BasicSecret():
                return "basic"
            case BearerSecret():
                return "bearer"
            case _:
                pass
        return "custom"

    def __repr__(self) -> str:
        """Name the scheme and kind without the secret."""
        return f"Credential(scheme_name={self.scheme_name!r}, kind={self.kind!r}, payload=<redacted>)"


@dataclass(frozen=True, kw_only=True)
class SchemeRequirement(Generic[SchemeT_co]):
    """One scheme an alternative requires, with its declared scopes in order."""

    scheme_name: SchemeT_co  # type: ignore[misc, unused-ignore]
    scopes: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class RequirementCandidate(Generic[SecretT_co, SchemeT]):
    """One declared alternative whose every scheme presented a credential, in the alternative's scheme order."""

    requirement_index: int
    credentials: Mapping[SchemeT, Credential[SecretT_co, SchemeT]]

    def __post_init__(self) -> None:
        """Freeze the credentials and reject indexes or names that do not describe a declared alternative."""
        if type(self.requirement_index) is not int:
            msg = "requirement_index must be an integer"
            raise AuthConfigurationError(msg)
        if any(credential.scheme_name != name for name, credential in self.credentials.items()):
            msg = "A candidate credential is filed under another scheme's name"
            raise AuthConfigurationError(msg)
        object.__setattr__(self, "credentials", MappingProxyType(dict(self.credentials)))


@dataclass(frozen=True, kw_only=True)
class AuthContext(Generic[SecretT_co, SchemeT, OperationT]):
    """What an authorizer decides from: the operation, the request, its alternatives, and the complete candidates."""

    operation_key: OperationT
    request: Request = field(repr=False)
    requirements: tuple[tuple[SchemeRequirement[SchemeT], ...], ...]
    candidates: tuple[RequirementCandidate[SecretT_co, SchemeT], ...]


class Authorizer(Protocol[SecretT_contra, PrincipalT_co, SchemeT, OperationT]):
    """Authorize a request from its complete candidates and return the principal the handler receives."""

    def __call__(self, context: AuthContext[SecretT_contra, SchemeT, OperationT], /) -> PrincipalT_co:
        """Return the principal, or raise HTTPException to reject the request."""
        raise NotImplementedError


class AsyncAuthorizer(Protocol[SecretT_contra, PrincipalT_co, SchemeT, OperationT]):
    """Authorize a request asynchronously and return the principal the handler receives."""

    async def __call__(self, context: AuthContext[SecretT_contra, SchemeT, OperationT], /) -> PrincipalT_co:
        """Return the principal, or raise HTTPException to reject the request."""
        raise NotImplementedError


class CredentialExtractor(Protocol[SecretT_co, SchemeT_co]):
    """Read one scheme's credential from a request, replacing the builtin helper for that scheme."""

    def __call__(self, request: Request, /) -> Credential[SecretT_co, SchemeT_co] | None:
        """Return the credential, None when the request presents none, or raise HTTPException for an invalid one."""


class AsyncCredentialExtractor(Protocol[SecretT_co, SchemeT_co]):
    """Read one scheme's credential from a request asynchronously."""

    async def __call__(self, request: Request, /) -> Credential[SecretT_co, SchemeT_co] | None:
        """Return the credential, None when the request presents none, or raise HTTPException for an invalid one."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemePlan:
    """One declared security scheme the selected operations use, and where the builtin helper reads its credential."""

    name: str
    kind: SchemeKind
    location: ApiKeyLocation | None = None
    parameter: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SecurityPlan:
    """A secured operation's alternatives in source order: each names the schemes and scopes it needs, or none."""

    requirements: tuple[tuple[tuple[str, tuple[str, ...]], ...], ...]
    anonymous: bool = field(init=False, repr=False, compare=False)
    schemes: tuple[str, ...] = field(init=False, repr=False, compare=False)
    alternatives: tuple[tuple[SchemeRequirement[str], ...], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Note an anonymous alternative, order the schemes by first occurrence, and build the public records once."""
        object.__setattr__(self, "anonymous", not all(self.requirements))
        names = tuple(dict.fromkeys(name for requirement in self.requirements for name, _ in requirement))
        object.__setattr__(self, "schemes", names)
        object.__setattr__(
            self,
            "alternatives",
            tuple(
                tuple(SchemeRequirement(scheme_name=name, scopes=scopes) for name, scopes in requirement)
                for requirement in self.requirements
            ),
        )


def coroutine_function(value: object) -> TypeIs[Callable[..., Awaitable[object]]]:
    """Return whether calling a value, a function or an object with an async __call__, returns a coroutine."""
    return inspect.iscoroutinefunction(value) or inspect.iscoroutinefunction(getattr(value, "__call__", None))  # noqa: B004


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_credential(value: object) -> TypeIs[Credential[object, str]]:
    return isinstance(value, Credential)


def asynchronous(value: object) -> Callable[..., Awaitable[object]] | None:
    """Return a callable whose calls return coroutines as such, or None for any other value."""
    return value if coroutine_function(value) else None


def _awaited(function: Callable[..., object]) -> Callable[..., Awaitable[object]]:
    if (awaited := asynchronous(function)) is not None:
        return awaited

    async def call(*arguments: object) -> object:
        return await run_in_threadpool(function, *arguments)

    return call


def _not_authenticated(challenges: Iterable[str]) -> HTTPException:
    headers = {"WWW-Authenticate": value} if (value := ", ".join(dict.fromkeys(filter(None, challenges)))) else None
    return HTTPException(status_code=401, detail="Not authenticated", headers=headers)


def _helper(scheme: SchemePlan) -> Callable[[Request], Awaitable[object]]:
    name = scheme.parameter or ""
    match scheme.kind, scheme.location:
        case "api_key", "header":
            return APIKeyHeader(name=name, auto_error=False)
        case "api_key", "query":
            return APIKeyQuery(name=name, auto_error=False)
        case "api_key", _:
            return APIKeyCookie(name=name, auto_error=False)
        case "basic", _:
            return HTTPBasic(auto_error=False)
        case _:
            pass
    return HTTPBearer(auto_error=False)


def _presented(request: Request, scheme: SchemePlan) -> tuple[str, ...]:
    name = scheme.parameter or ""
    match scheme.kind, scheme.location:
        case "api_key", "header":
            return tuple(request.headers.getlist(name))
        case "api_key", "query":
            return tuple(request.query_params.getlist(name))
        case "api_key", _:
            key = name.encode("latin-1")
            cookies = split_cookies(tuple(request.scope.get("headers", ())))
            return tuple(item.value.decode("latin-1") for item in cookies if item.name == key)
        case _:
            pass
    values = tuple(request.headers.getlist("authorization"))
    if len(set(values)) > 1:
        return values
    return tuple(value for value in values if value.partition(" ")[0].lower() == scheme.kind)


class Security:
    """Authenticate one router's secured operations: an extractor or builtin helper per scheme, then the authorizer."""

    __slots__ = ("_authorize", "_extractors", "_helpers", "_schemes")

    def __init__(
        self, schemes: Mapping[str, SchemePlan], used: Iterable[str], authorizer: object, extractors: object
    ) -> None:
        """Check the authorizer and the extractors against the schemes the secured operations use."""
        if not callable(authorizer):
            msg = "An authorizer is required because the selected operations use security"
            raise AuthConfigurationError(msg)
        custom = checked_extractors(extractors, schemes)
        needed = tuple(schemes[name] for name in used)
        if missing := [scheme.name for scheme in needed if scheme.kind == "custom" and scheme.name not in custom]:
            msg = f"The schemes {', '.join(missing)} have no builtin helper and need credential_extractors"
            raise AuthConfigurationError(msg)
        self._schemes = schemes
        self._extractors = {name: _awaited(extractor) for name, extractor in custom.items()}
        self._helpers = {scheme.name: _helper(scheme) for scheme in needed if scheme.name not in custom}
        self._authorize = _awaited(authorizer)

    def dependency(self, operation: str, plan: SecurityPlan) -> Authenticator:
        """Return the FastAPI dependency that authenticates one operation and returns its principal."""

        async def authenticate(request: Request) -> object:
            return await self.authenticate(request, operation, plan)

        return authenticate

    async def authenticate(self, request: Request, operation: str, plan: SecurityPlan) -> object:
        """Return the principal of a request, None for an anonymous one, or raise 401 without complete credentials."""
        credentials: dict[str, Credential[object, str]] = {}
        for name in plan.schemes:
            if (credential := await self.extract(request, name)) is not None:
                credentials[name] = credential
        candidates = tuple(
            RequirementCandidate(
                requirement_index=index, credentials={name: credentials[name] for name, _ in requirement}
            )
            for index, requirement in enumerate(plan.requirements)
            if requirement and all(name in credentials for name, _ in requirement)
        )
        if candidates:
            context = AuthContext(
                operation_key=operation, request=request, requirements=plan.alternatives, candidates=candidates
            )
            return await self._authorize(context)
        if plan.anonymous:
            return None
        raise _not_authenticated(_CHALLENGES.get(self._schemes[name].kind, "") for name in plan.schemes)

    async def extract(self, request: Request, name: str) -> Credential[object, str] | None:
        """Return one scheme's credential from its extractor or builtin helper, rejecting presented invalid ones."""
        if (extractor := self._extractors.get(name)) is not None:
            if (found := await extractor(request)) is None:
                return None
            if not _is_credential(found) or found.scheme_name != name:
                msg = f"The {name} credential extractor returned something other than a {name} Credential"
                raise AuthConfigurationError(msg)
            return found
        scheme = self._schemes[name]
        if not (presented := _presented(request, scheme)):
            return None
        if len(set(presented)) > 1:
            raise malformed_request()
        value = await self._helpers[name](request)
        match value:
            case str():
                return Credential(scheme_name=name, payload=ApiKeySecret(value=value))
            case HTTPBasicCredentials():
                return Credential(
                    scheme_name=name, payload=BasicSecret(username=value.username, password=value.password)
                )
            case HTTPAuthorizationCredentials():
                return Credential(scheme_name=name, payload=BearerSecret(token=value.credentials))
            case _:
                pass
        raise _not_authenticated((_CHALLENGES[scheme.kind],))


def checked_extractors(extractors: object, schemes: Mapping[str, SchemePlan]) -> Mapping[str, Callable[..., object]]:
    """Return the credential extractors once each one is callable and names a scheme the operations use."""
    if extractors is None:
        return MappingProxyType({})
    if not _is_mapping(extractors):
        msg = "credential_extractors must map scheme names to callables"
        raise AuthConfigurationError(msg)
    checked = {str(key): value for key, value in extractors.items() if callable(value)}
    if len(checked) != len(extractors):
        msg = "credential_extractors must map scheme names to callables"
        raise AuthConfigurationError(msg)
    if unknown := sorted(key for key in checked if key not in schemes):
        msg = f"credential_extractors names schemes no selected operation uses: {', '.join(unknown)}"
        raise AuthConfigurationError(msg)
    return MappingProxyType(checked)


def authenticators(
    schemes: tuple[SchemePlan, ...],
    secured: tuple[tuple[str, SecurityPlan], ...],
    authorizer: object,
    extractors: object,
) -> Mapping[str, Authenticator]:
    """Return each secured operation's authentication dependency by operation key, checking the router's settings.

    A router without secured operations takes no authorizer or credential extractors.
    """
    if not secured:
        return MappingProxyType({})
    declared = MappingProxyType({scheme.name: scheme for scheme in schemes})
    used = dict.fromkeys(name for _, plan in secured for name in plan.schemes)
    security = Security(declared, used, authorizer, extractors)
    return MappingProxyType({operation: security.dependency(operation, plan) for operation, plan in secured})
