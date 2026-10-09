"""Services, authorize callbacks, dependency overrides, and settings of the secured server: they report what they get."""

from __future__ import annotations

from functools import partial, wraps
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator
    from types import ModuleType


class _Pending:
    """Stand in for an awaitable result that is not a coroutine."""

    def __await__(self) -> Generator[None, None, None]:
        yield


class _Variant:
    """Stand in for a service that replaces some of another service's methods."""

    def __init__(self, service: object, **methods: object) -> None:
        self.service = service
        self.methods = methods

    def __getattr__(self, name: str) -> object:
        return self.methods[name] if name in self.methods else getattr(self.service, name)


def _secret(credential: Any) -> str:  # noqa: ANN401
    return str(getattr(credential, "credentials", None) or getattr(credential, "username", None) or credential)


def services(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return the service sets: recording services of each mode, and a set whose plain methods return awaitables."""

    def record(name: str, arguments: dict[str, object]) -> None:
        calls.append(f"{name}({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

    class Pets(server.services.PetsService):
        def list_pets(self, **arguments: object) -> object:
            record("list_pets", arguments)
            return ["rex"]

    class Public(server.services.PublicService):
        def get_public(self, **arguments: object) -> None:
            record("get_public", arguments)

    class Untagged(server.services.UntaggedService):
        async def get_maybe(self, **arguments: object) -> None:
            record("get_maybe", arguments)

        def put_pet(self, **arguments: object) -> None:
            record("put_pet", arguments)

        def get_session(self, **arguments: object) -> None:
            record("get_session", arguments)

        async def get_custom(self, **arguments: object) -> None:
            record("get_custom", arguments)

    async def later() -> None:
        record("later", {})

    default = {"pets": Pets(), "public": Public(), "untagged": Untagged()}
    awaitables = {
        "public": _Variant(default["public"], get_public=lambda **_: _Pending()),
        "untagged": _Variant(default["untagged"], put_pet=lambda **_: later()),
    }
    shapes = dict.fromkeys(("authorize-lambda", "authorize-wrapped", "authorize-partial"), default)
    return {"default": default, "async": default, "awaitables": {**default, **awaitables}, **shapes}


def settings(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return the builder settings of each application: authorize callbacks, overrides, dependencies, and options."""

    def authorize(requirement_sets: tuple[Any, ...], credentials: dict[str, Any]) -> str:
        calls.append(f"authorize {list(requirement_sets)}")
        calls.append(f"  credentials {[f'{name}={_secret(value)}' for name, value in sorted(credentials.items())]}")
        if "denied" in credentials.values():
            raise HTTPException(status_code=403, detail="Forbidden")
        return "user-" + "+".join(name for name, _ in requirement_sets[0])

    class AsyncAuthorize:
        async def __call__(self, requirement_sets: tuple[Any, ...], credentials: dict[str, Any]) -> str:
            calls.append(f"async authorize {list(requirement_sets)} {sorted(credentials)}")
            return "async-user"

    async def checked(requirement_sets: tuple[Any, ...], credentials: dict[str, Any]) -> str:
        calls.append(f"awaited authorize {sorted(credentials)}")
        if "denied" in credentials.values():
            raise HTTPException(status_code=403, detail="Forbidden")
        return "awaited-user"

    @wraps(checked)
    def wrapped(requirement_sets: tuple[Any, ...], credentials: dict[str, Any]) -> object:
        return checked(requirement_sets, credentials)

    class Checked:
        async def __call__(self, requirement_sets: tuple[Any, ...], credentials: dict[str, Any]) -> str:
            return await checked(requirement_sets, credentials)

    def certificate(x_client_cert: Annotated[str | None, Header()] = None) -> str | None:
        return x_client_cert

    def dependency(label: str) -> Callable[..., None]:
        def record(request: Request) -> None:
            calls.append(f"{label} {request.url.path}")

        return record

    def teapot() -> None:
        raise HTTPException(status_code=418, detail="I'm a teapot")

    return {
        "default": {
            "authorize": authorize,
            "dependencies": [Depends(dependency("global"))],
            "operation_dependencies": {"get_public": [Depends(dependency("public"))]},
            "dependency_overrides": {server.security.mtls: certificate},
        },
        "async": {
            "authorize": AsyncAuthorize(),
            "operation_dependencies": {"get_public": (Depends(teapot),)},
            "prefix": "/api",
            "debug": False,
            "title": "Renamed pets",
            "summary": None,
            "openapi_tags": [{"name": "pets", "description": "Renamed."}],
            "servers": None,
            "swagger_ui_parameters": {"deepLinking": False},
            "contact": {"name": "Other team"},
            "responses": {418: {"description": "Teapot."}, "default": {"description": "Anything."}},
            "dependencies": [Depends(dependency("app"))],
            "middleware": [Middleware(GZipMiddleware)],
            "routes": [],
            "callbacks": None,
            "webhooks": APIRouter(),
            "default_response_class": JSONResponse,
            "deprecated": None,
            "generate_unique_id_function": lambda route: f"{route.name}-id",
        },
        "awaitables": {"authorize": authorize},
        "authorize-lambda": {"authorize": lambda requirement_sets, credentials: checked(requirement_sets, credentials)},
        "authorize-wrapped": {"authorize": wrapped},
        "authorize-partial": {"authorize": partial(Checked())},
    }


def builds(server: ModuleType, models: ModuleType, calls: list[str]) -> Iterator[tuple[str, Callable[[], object]]]:
    """Yield builders that fail as they register a missing or mismatched method or setting, and builders that succeed."""
    default = services(server, models, calls)["default"]
    secured = {"authorize": settings(server, models, calls)["default"]["authorize"]}
    build = partial(server.build_router, **default)
    create = partial(server.create_app, **default, **secured)
    untagged = default["untagged"]
    yield "no-authorize", partial(build, authorize=None)
    yield "missing-method", partial(build, untagged=object(), **secured)
    yield "sync-in-async", partial(build, untagged=_Variant(untagged, get_maybe=untagged.put_pet), **secured)
    yield "async-in-sync", partial(build, untagged=_Variant(untagged, put_pet=untagged.get_maybe), **secured)
    for label, value in (
        ("unknown", {"/paths/~1pets/get": []}),
        ("none", {"list_pets": None}),
        ("items", {"list_pets": [object()]}),
    ):
        yield f"operation-dependencies-{label}", partial(build, operation_dependencies=value, **secured)
    routers = server.routers
    yield "group public", partial(routers.public.build_router, public=default["public"])
    yield "group pets", partial(routers.pets.build_router, pets=default["pets"], **secured)
    for label, generate in (("unique-id", lambda route: f"{route.name}-id"), ("unique-id-result", lambda route: 7)):
        app = create(generate_unique_id_function=generate)
        yield f"options-{label}", partial(app.add_api_route, "/extra", lambda: None)
