"""Register a generated router's operations on one APIRouter, with the services that implement them."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeAlias, TypeVar

from fastapi import APIRouter
from fastapi.params import Depends
from typing_extensions import TypeIs

from .security import awaitable, coroutine_function

MethodT = TypeVar("MethodT")


@dataclass(frozen=True, slots=True, kw_only=True)
class Wiring:
    """What route adders connect: the services by group, the authorize callback, and operation dependencies."""

    services: Mapping[str, Any]
    authorize: object
    dependencies: Mapping[str, Sequence[Depends]]

    def authorizer(self) -> Callable[..., Awaitable[object]]:
        """Return the authorize callback to await, which a secured operation needs before it is registered."""
        if not callable(authorize := self.authorize):
            msg = "An authorize callback is required because the selected operations use security"
            raise TypeError(msg)
        return awaitable(authorize)


Route: TypeAlias = tuple[str, Callable[[APIRouter, Wiring], None]]


def checked(method: MethodT, label: str, *, asynchronous: bool = False) -> MethodT:
    """Return a service method for its operation, refusing a coroutine function where a plain one runs or the reverse.

    A mismatch would skip the method body or fail after it ran, so it stops the registration.
    """
    if coroutine_function(method) is asynchronous:
        return method
    kinds = ("a coroutine function", "a plain method")
    found, needed = reversed(kinds) if asynchronous else kinds
    msg = f"{label} is {found}, but the operation's handler mode needs {needed}"
    raise TypeError(msg)


def _is_sequence(value: object) -> TypeIs[Sequence[object]]:
    return isinstance(value, Sequence)


def build(  # noqa: PLR0913
    routes: tuple[Route, ...],
    *,
    services: Mapping[str, Any],
    authorize: object = None,
    dependencies: Sequence[Depends],
    operation_dependencies: Mapping[str, Any] | None,
    prefix: str,
) -> APIRouter:
    """Register the routes in order on a new router; each looks its service's method up as it registers.

    The service Protocols and FastAPI check the services and settings. Refused here is what would otherwise drop a
    dependency or an authorization without an error: a dependency for a method the router does not have, or one
    that is no sequence of Depends, and, by the secured routes, a missing authorize callback.
    """
    own = operation_dependencies or {}
    if unknown := sorted(own.keys() - {name for name, _ in routes}):
        msg = f"operation_dependencies names no operation of this router: {', '.join(unknown)}"
        raise ValueError(msg)
    for name, items in own.items():
        if not (_is_sequence(items) and all(isinstance(item, Depends) for item in items)):
            msg = f"operation_dependencies[{name!r}] must be a sequence of Depends(...) values"
            raise TypeError(msg)
    wiring = Wiring(services=services, authorize=authorize, dependencies=own)
    router = APIRouter(prefix=prefix, dependencies=dependencies)
    for _, add in routes:
        add(router, wiring)
    return router
