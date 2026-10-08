"""Register a generated router's operations on one APIRouter, with the services that implement them."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, TypeAlias, TypeVar, cast

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.params import Depends
from starlette.responses import JSONResponse
from typing_extensions import TypeIs

from .security import awaitable, coroutine_function

if TYPE_CHECKING:
    from starlette.requests import Request

MethodT = TypeVar("MethodT")
_REQUEST_VALUES: Final = frozenset({"input", "ctx", "url"})


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


async def validation_error_handler(  # noqa: RUF029 - Starlette runs a synchronous handler in the threadpool.
    _: Request, error: Exception
) -> JSONResponse:
    """Answer 422 with the type, location, and message of each of FastAPI's records, without its input and context.

    The error is the RequestValidationError the handler is registered for; Starlette types every handler's as Exception.
    """
    errors = cast("RequestValidationError", error).errors()
    records = [{key: value for key, value in record.items() if key not in _REQUEST_VALUES} for record in errors]
    return JSONResponse({"detail": jsonable_encoder(records)}, status_code=422)


def error_handlers(exception_handlers: Mapping[Any, Any] | None) -> dict[Any, Any]:
    """Return an application's exception handlers: validation_error_handler, under the caller's own handlers."""
    return {RequestValidationError: validation_error_handler, **(exception_handlers or {})}


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
