"""Services of the adapter parameter server: they report the decoded values they receive."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, FastAPI, Request
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

if TYPE_CHECKING:
    from types import ModuleType


def services(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return a service that records its keywords and sends no body."""

    def record(name: str, arguments: dict[str, object]) -> None:
        calls.append(f"{name}({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

    class Default(server.services.DefaultService):
        def search(self, **arguments: object) -> None:
            record("search", arguments)

        def get_ratios(self, **arguments: object) -> None:
            record("get_ratios", arguments)

        def get_sizes(self, **arguments: object) -> None:
            record("get_sizes", arguments)

        def get_codes(self, **arguments: object) -> None:
            record("get_codes", arguments)

        def repeat(self, **arguments: object) -> None:
            record("repeat", arguments)

        def get_note(self, **arguments: object) -> None:
            record("get_note", arguments)

        def get_file(self, **arguments: object) -> None:
            record("get_file", arguments)

        def get_pair(self, **arguments: object) -> None:
            record("get_pair", arguments)

        def get_menu(self, **arguments: object) -> None:
            record("get_menu", arguments)

        def get_item(self, **arguments: object) -> None:
            record("get_item", arguments)

        def get_serving(self, **arguments: object) -> None:
            record("get_serving", arguments)

    return {"default": {"default": Default()}}


def _echo(request: Request) -> JSONResponse:
    """Answer with the path and path parameters a route the user added receives."""
    return JSONResponse({"path": request.url.path, "params": request.path_params})


def applications(server: ModuleType, sets: dict[str, dict[str, object]]) -> dict[str, FastAPI]:
    """Mount the application below /a/b, and add the user's own routes, router, and mount to the generated router."""
    mounted = FastAPI()
    mounted.mount("/a/b", server.create_app(**sets["default"]))
    router = server.build_router(**sets["default"])
    router.add_api_route("/user/{name}", _echo)
    router.add_route("/echo/{rest:path}", _echo)
    included = APIRouter()
    included.add_api_route("/included/{rest:path}", _echo)
    router.include_router(included)
    router.mount("/mounted", Starlette(routes=[Route("/{rest:path}", _echo)]))
    extended = FastAPI()
    extended.include_router(router)
    return {"mounted": mounted, "extended": extended}
