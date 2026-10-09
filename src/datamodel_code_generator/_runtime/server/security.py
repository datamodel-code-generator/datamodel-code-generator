"""Authorize secured operations from the credentials of FastAPI's security dependencies, one per scheme."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import TypeAlias, TypeGuard

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from typing_extensions import TypeVar

PrincipalT = TypeVar("PrincipalT")
Requirement: TypeAlias = tuple[tuple[str, tuple[str, ...]], ...]
RequirementSets: TypeAlias = tuple[Requirement, ...]
Credentials: TypeAlias = Mapping[str, object]
Authorize: TypeAlias = Callable[[RequirementSets, Credentials], PrincipalT]
AsyncAuthorize: TypeAlias = Callable[[RequirementSets, Credentials], Awaitable[PrincipalT]]


def coroutine_function(value: object) -> TypeGuard[Callable[..., Awaitable[object]]]:
    """Return whether calling a value, a function or an object with an async __call__, returns a coroutine."""
    return inspect.iscoroutinefunction(value) or inspect.iscoroutinefunction(getattr(value, "__call__", None))  # noqa: B004


def awaitable(authorize: Callable[..., object]) -> Callable[..., Awaitable[object]]:
    """Return an authorize callback to await: a coroutine function as it is, any other run in the threadpool.

    An awaitable that the other returns is awaited too, so its result or its rejection is the callback's.
    """
    if coroutine_function(authorize):
        return authorize

    async def threaded(requirements: RequirementSets, credentials: Credentials) -> object:
        result = await run_in_threadpool(authorize, requirements, credentials)
        return await result if inspect.isawaitable(result) else result

    return threaded


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
