"""Register a generated router's operations on one APIRouter, with the services that implement them."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

from fastapi import APIRouter

from .security import awaitable

if TYPE_CHECKING:
    from fastapi.params import Depends


@dataclass(frozen=True, slots=True, kw_only=True)
class Wiring:
    """What route adders connect: the services by group, the awaited authorize callback, and operation dependencies."""

    services: Mapping[str, Any]
    authorize: Callable[..., Awaitable[object]]
    dependencies: Mapping[str, Sequence[Depends]]


Route: TypeAlias = tuple[str, Callable[[APIRouter, Wiring], None]]


def _unsecured(*_: object) -> None:
    """Stand in for the authorize callback of a router without secured operations, which never calls it."""


def build(  # noqa: PLR0913
    routes: tuple[Route, ...],
    *,
    services: Mapping[str, Any],
    authorize: Callable[..., object] = _unsecured,
    dependencies: Sequence[Depends],
    operation_dependencies: Mapping[str, Any] | None,
    prefix: str,
) -> APIRouter:
    """Register the routes in order on a new router; each looks its service's method up as it registers.

    The service Protocols and FastAPI check the services and settings. Only a dependency for a method the router
    does not have is refused here, since nothing else would report it.
    """
    wiring = Wiring(services=services, authorize=awaitable(authorize), dependencies=operation_dependencies or {})
    if unknown := sorted(wiring.dependencies.keys() - {name for name, _ in routes}):
        msg = f"operation_dependencies names no operation of this router: {', '.join(unknown)}"
        raise ValueError(msg)
    router = APIRouter(prefix=prefix, dependencies=dependencies)
    for _, add in routes:
        add(router, wiring)
    return router
