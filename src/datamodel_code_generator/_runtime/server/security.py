"""Authorize secured operations from the credentials of FastAPI's security dependencies, one per scheme."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import TypeAlias

from fastapi import HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials, OAuth2, OpenIdConnect
from fastapi.security.utils import get_authorization_scheme_param
from typing_extensions import TypeVar

PrincipalT = TypeVar("PrincipalT")
Requirement: TypeAlias = tuple[tuple[str, tuple[str, ...]], ...]
RequirementSets: TypeAlias = tuple[Requirement, ...]
Credentials: TypeAlias = Mapping[str, object]
Authorize: TypeAlias = Callable[[RequirementSets, Credentials], PrincipalT]
AsyncAuthorize: TypeAlias = Callable[[RequirementSets, Credentials], Awaitable[PrincipalT]]


def bearer_token(authorization: str | None) -> str | None:
    """Return the token of a `Bearer` `Authorization` header, matching the scheme case-insensitively, or None."""
    scheme, token = get_authorization_scheme_param(authorization)
    return token if scheme.lower() == "bearer" and token else None


class OptionalHTTPBasic(HTTPBasic):
    """FastAPI's `HTTPBasic`, reading a malformed Basic credential as none, as it reads a missing one."""

    async def __call__(self, request: Request) -> HTTPBasicCredentials | None:  # type: ignore[override]
        """Return the credentials of a `Basic` `Authorization` header, or None without a well-formed one."""
        try:
            return await super().__call__(request)
        except HTTPException:
            return None


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
