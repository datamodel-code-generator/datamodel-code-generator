"""Services and applications of the error server: every way to serve it, with and without the package's handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from types import ModuleType


def services(server: ModuleType, _models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return one recording service for each application."""

    class Untagged(server.services.UntaggedService):
        def update_account(self, **arguments: object) -> None:
            calls.append(f"update_account({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

        def list_codes(self, **arguments: object) -> None:
            calls.append(f"list_codes({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

    default = {"untagged": Untagged()}
    return dict.fromkeys(("default", "override", "router", "router-handler"), default)


def settings(_server: ModuleType, _models: ModuleType, _calls: list[str]) -> dict[str, dict[str, object]]:
    """Return the caller's own validation error handler for the override application."""

    async def counted(_: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": f"{len(error.errors())} invalid values"}, status_code=400)

    return {"override": {"exception_handlers": {RequestValidationError: counted}}}


def applications(server: ModuleType, sets: dict[str, dict[str, object]]) -> dict[str, FastAPI]:
    """Include the router in the caller's applications: FastAPI's default handler, then the package's handler."""
    plain, handled = FastAPI(), FastAPI()
    plain.include_router(server.build_router(**sets["router"]))
    handled.include_router(server.build_router(**sets["router-handler"]))
    handled.add_exception_handler(RequestValidationError, server.validation_error_handler)
    return {"router": plain, "router-handler": handled}
