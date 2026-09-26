"""Check the services a generated router connects, then register its operations on one APIRouter."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import TypeAlias

from fastapi import APIRouter

Handlers: TypeAlias = Mapping[str, Callable[..., object]]
Operation: TypeAlias = tuple[str, str, tuple[str, ...]]
Route: TypeAlias = Callable[[APIRouter, Handlers], None]


class HandlerConfigurationError(TypeError):
    """Reject a service that does not provide one fitting method per operation of its router group."""


def build(routes: tuple[Route, ...], operations: tuple[Operation, ...], services: Mapping[str, object]) -> APIRouter:
    """Check the services' methods, then register the routes on a new router in order."""
    handlers = checked_services(services, operations)
    router = APIRouter()
    for add in routes:
        add(router, handlers)
    return router


def checked_services(services: Mapping[str, object], operations: tuple[Operation, ...]) -> Handlers:
    """Return each operation's handler: its service's method, once it is synchronous and accepts the keywords.

    Each operation names its service, its method, and the keywords the endpoint passes. The check reads
    signatures only; it never evaluates annotations, so it proves shapes, not argument or return types.
    """
    checked: dict[str, Callable[..., object]] = {}
    for service, name, keywords in operations:
        if not callable(handler := getattr(services[service], name, None)):
            msg = f"The {service} service needs a callable {name} method"
            raise HandlerConfigurationError(msg)
        if inspect.iscoroutinefunction(handler) or inspect.iscoroutinefunction(getattr(handler, "__call__", None)):  # noqa: B004
            msg = f"The {service}.{name} method is asynchronous, but its operation calls it synchronously"
            raise HandlerConfigurationError(msg)
        try:
            inspect.signature(handler).bind(**dict.fromkeys(keywords))
        except (TypeError, ValueError) as error:
            msg = f"The {service}.{name} method does not accept exactly the keywords {', '.join(keywords) or 'none'}"
            raise HandlerConfigurationError(msg) from error
        checked[name] = handler
    return MappingProxyType(checked)
