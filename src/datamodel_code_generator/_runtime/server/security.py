"""Authorize secured operations from the credentials of FastAPI's security dependencies, one per scheme.

A credential that a header or a cookie carries is ASCII, as RFC 9110 writes header text and RFC 6265 cookie octets:
one with another byte is no credential, as a missing one is not.
"""

from __future__ import annotations

from base64 import b64decode
from collections.abc import Awaitable, Callable, Mapping
from typing import TypeAlias

from fastapi import HTTPException, Request
from fastapi.security import (
    APIKeyCookie,
    APIKeyHeader,
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
    HTTPDigest,
    OAuth2,
    OpenIdConnect,
)
from fastapi.security.utils import get_authorization_scheme_param
from typing_extensions import TypeVar

PrincipalT = TypeVar("PrincipalT")
Requirement: TypeAlias = tuple[tuple[str, tuple[str, ...]], ...]
RequirementSets: TypeAlias = tuple[Requirement, ...]
Credentials: TypeAlias = Mapping[str, object]
Authorize: TypeAlias = Callable[[RequirementSets, Credentials], PrincipalT]
AsyncAuthorize: TypeAlias = Callable[[RequirementSets, Credentials], Awaitable[PrincipalT]]


def bearer_token(authorization: str | None) -> str | None:
    """Return the ASCII token of a `Bearer` `Authorization` header, matching the scheme case-insensitively, or None."""
    scheme, token = get_authorization_scheme_param(authorization)
    return token if scheme.lower() == "bearer" and token and token.isascii() else None


def _text(credential: str | None) -> str | None:
    return credential if credential is not None and credential.isascii() else None


def _authorization(credentials: HTTPAuthorizationCredentials | None) -> HTTPAuthorizationCredentials | None:
    return credentials if credentials is not None and credentials.credentials.isascii() else None


class ASCIIAPIKeyHeader(APIKeyHeader):
    """FastAPI's `APIKeyHeader`, reading a key that is not ASCII as none."""

    async def __call__(self, request: Request) -> str | None:
        """Return the API key of the header, or None without an ASCII one."""
        return _text(await super().__call__(request))


class ASCIIAPIKeyCookie(APIKeyCookie):
    """FastAPI's `APIKeyCookie`, reading a key that is not ASCII as none."""

    async def __call__(self, request: Request) -> str | None:
        """Return the API key of the cookie, or None without an ASCII one."""
        return _text(await super().__call__(request))


class ASCIIHTTPBearer(HTTPBearer):
    """FastAPI's `HTTPBearer`, reading a token that is not ASCII as none."""

    async def __call__(self, request: Request) -> HTTPAuthorizationCredentials | None:
        """Return the credentials of a `Bearer` `Authorization` header, or None without an ASCII token."""
        return _authorization(await super().__call__(request))


class ASCIIHTTPDigest(HTTPDigest):
    """FastAPI's `HTTPDigest`, reading credentials that are not ASCII as none."""

    async def __call__(self, request: Request) -> HTTPAuthorizationCredentials | None:
        """Return the credentials of a `Digest` `Authorization` header, or None without ASCII ones."""
        return _authorization(await super().__call__(request))


class OptionalHTTPBasic(HTTPBasic):
    """FastAPI's `HTTPBasic`, reading a malformed Basic credential as none, as it reads a missing one.

    The credential is UTF-8 (RFC 7617), as the generated client sends it, where `HTTPBasic` reads ASCII only.
    """

    async def __call__(self, request: Request) -> HTTPBasicCredentials | None:  # type: ignore[override]
        """Return the credentials of a `Basic` `Authorization` header, or None without a well-formed one."""
        scheme, credential = get_authorization_scheme_param(request.headers.get("authorization"))
        if scheme.lower() != "basic":
            return None
        try:
            username, separator, password = b64decode(credential, validate=True).decode().partition(":")
        except ValueError:
            return None
        return HTTPBasicCredentials(username=username, password=password) if separator else None


class OAuth2Bearer(OAuth2):
    """FastAPI's `OAuth2` with the declared flows, reading only a bearer token as `OAuth2PasswordBearer` does."""

    async def __call__(self, request: Request) -> str | None:
        """Return the bearer token of the `Authorization` header, or None for another scheme or no header."""
        return bearer_token(await super().__call__(request))


class OpenIdConnectBearer(OpenIdConnect):
    """FastAPI's `OpenIdConnect`, reading only a bearer token from the `Authorization` header."""

    async def __call__(self, request: Request) -> str | None:
        """Return the bearer token of the `Authorization` header, or None for another scheme or no header."""
        return bearer_token(await super().__call__(request))


async def authenticate(
    requirements: RequirementSets,
    credentials: Credentials,
    authorize: Callable[..., Awaitable[object]],
    challenge: str,
) -> object:
    """Return the principal of the requirement sets whose every scheme presented a credential.

    The awaited authorize callback receives those sets in declaration order with their credentials, once. Without
    one, an operation that also accepts no credentials receives None, and any other answers 401.
    """
    eligible = tuple(
        requirement
        for requirement in requirements
        if requirement and all(credentials.get(name) is not None for name, _ in requirement)
    )
    if eligible:
        return await authorize(
            eligible, {name: credentials[name] for requirement in eligible for name, _ in requirement}
        )
    if not all(requirements):
        return None
    raise HTTPException(status_code=401, detail="Not authenticated", headers={"WWW-Authenticate": challenge})
