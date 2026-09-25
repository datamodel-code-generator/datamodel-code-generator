"""Services, authorizers, credential extractors, and settings of the secured server: they report what they receive."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
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


def _secret(payload: Any) -> str:  # noqa: ANN401
    value = getattr(payload, "value", None) or getattr(payload, "token", None) or getattr(payload, "username", "")
    return f"{payload!r}={value}"


class _Variant:
    """Stand in for a service that replaces some of another service's methods."""

    def __init__(self, service: object, **methods: object) -> None:
        self.service = service
        self.methods = methods

    def __getattr__(self, name: str) -> object:
        return self.methods[name] if name in self.methods else getattr(self.service, name)


def services(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return service sets: recording services of each mode, and a set whose methods return awaitables."""

    def record(name: str, arguments: dict[str, object]) -> None:
        calls.append(f"{name}({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

    class Pets:
        def list_pets(self, **arguments: object) -> object:
            record("list_pets", arguments)
            return ["rex"]

    class Public:
        def get_public(self, **arguments: object) -> None:
            record("get_public", arguments)

    class Untagged:
        async def get_maybe(self, **arguments: object) -> None:
            record("get_maybe", arguments)

        def put_pet(self, **arguments: object) -> None:
            record("put_pet", arguments)

        def get_session(self, **arguments: object) -> None:
            record("get_session", arguments)

        async def get_custom(self, **arguments: object) -> None:
            record("get_custom", arguments)

    async def later() -> list[str]:
        return ["late"]

    default = {"pets": Pets(), "public": Public(), "untagged": Untagged()}
    awaitables = {
        "pets": _Variant(default["pets"], list_pets=lambda **_: later()),
        "public": _Variant(default["public"], get_public=lambda **_: _Pending()),
    }
    return {"default": default, "async": default, "awaitables": {**default, **awaitables}}


def settings(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return the builder settings of each application: authorizers, extractors, dependencies, and options."""
    auth = server.auth_types

    def authorize(context: Any) -> str:  # noqa: ANN401
        calls.append(f"authorize {context!r}")
        credentials = [credential for candidate in context.candidates for credential in candidate.credentials.values()]
        calls.append(f"  secrets {[_secret(credential.payload) for credential in credentials]}")
        if any(getattr(credential.payload, "value", None) == "denied" for credential in credentials):
            raise HTTPException(status_code=403, detail="Forbidden")
        return f"user-{context.candidates[0].requirement_index}"

    class AsyncAuthorize:
        async def __call__(self, context: Any) -> str:  # noqa: ANN401
            calls.append(f"async authorize {context.operation_key} {[c.requirement_index for c in context.candidates]}")
            return "async-user"

    def digest(request: Request) -> object:
        match request.headers.get("x-digest"):
            case None:
                return None
            case "wrong-type":
                return "not a credential"
            case "wrong-scheme":
                return auth.Credential(scheme_name="mtls", payload=auth.CustomSecret(value="digest"))
            case value:
                return auth.Credential(scheme_name="digest", payload=auth.CustomSecret(value=value))

    class Certificate:
        async def __call__(self, request: Request) -> object:
            if (subject := request.headers.get("x-client-cert")) is None:
                return None
            return auth.Credential(scheme_name="mtls", payload=auth.CustomSecret(value=subject))

    def dependency(label: str) -> Callable[..., None]:
        def record(request: Request) -> None:
            calls.append(f"{label} {request.url.path}")

        return record

    def teapot() -> None:
        raise HTTPException(status_code=418, detail="I'm a teapot")

    extractors = {"digest": digest, "mtls": Certificate()}
    return {
        "default": {
            "authorizer": authorize,
            "credential_extractors": extractors,
            "dependencies": [Depends(dependency("global"))],
            "operation_dependencies": {"/paths/~1public/get": [Depends(dependency("public"))]},
        },
        "async": {
            "authorizer": AsyncAuthorize(),
            "credential_extractors": extractors,
            "operation_dependencies": {"/paths/~1public/get": (Depends(teapot),)},
            "prefix": "/api",
            "fastapi_options": {
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
        },
        "awaitables": {"authorizer": authorize, "credential_extractors": extractors},
    }


def builds(server: ModuleType, models: ModuleType, calls: list[str]) -> Iterator[tuple[str, Callable[[], object]]]:
    """Yield builders and record constructors that the generated package must reject or accept."""
    auth = server.auth_types
    default = services(server, models, calls)["default"]
    configured = settings(server, models, calls)["default"]
    authorize, extractors = configured["authorizer"], configured["credential_extractors"]
    secured = {"authorizer": authorize, "credential_extractors": extractors}
    build = partial(server.build_router, **default)
    create = partial(server.create_app, **default, **secured)
    untagged = default["untagged"]
    yield "no-authorizer", partial(build, authorizer=None)
    yield "no-extractors", partial(build, authorizer=authorize)
    yield "unknown-extractor", partial(build, authorizer=authorize, credential_extractors={**extractors, "nope": print})
    yield "extractor-list", partial(build, authorizer=authorize, credential_extractors=[print])
    yield "extractor-value", partial(build, authorizer=authorize, credential_extractors={**extractors, "digest": "d"})
    for label, methods in (
        ("missing-method", {"put_pet": None}),
        ("sync-in-async", {"get_maybe": untagged.put_pet}),
        ("async-in-sync", {"put_pet": untagged.get_maybe}),
        ("keywords", {"put_pet": lambda *, pet_id: None}),
    ):
        yield label, partial(build, untagged=_Variant(untagged, **methods), **secured)
    for prefix in ("api", "/api/", "/api?x=1", "/{api}", 7):
        yield f"prefix {prefix!r}", partial(build, prefix=prefix, **secured)
    yield "dependencies-object", partial(build, dependencies=[object()], **secured)
    yield "dependencies-text", partial(build, dependencies="global", **secured)
    for label, value in (
        ("unknown", {"/paths/~1nope/get": []}),
        ("list", [("/paths/~1pets/get", [])]),
        ("items", {"/paths/~1pets/get": [object()]}),
    ):
        yield f"operation-dependencies-{label}", partial(build, operation_dependencies=value, **secured)
    routers = server.routers
    yield "group public", partial(routers.public.build_router, public=default["public"])
    yield "group pets", partial(routers.pets.build_router, pets=default["pets"], **secured)
    for label, options in (
        ("options-list", [("title", "x")]),
        ("options-unknown", {"lifespan": None}),
        ("options-flag", {"debug": "yes"}),
        ("options-text", {"title": None}),
        ("options-json", {"contact": {"weight": float("nan")}}),
        ("options-object", {"license_info": ["MIT"]}),
        ("options-objects", {"servers": {"url": "https://example.com"}}),
        ("options-object-items", {"openapi_tags": ["pets"]}),
        ("options-responses", {"responses": [418]}),
        ("options-response-code", {"responses": {True: {}}}),
        ("options-dependencies", {"dependencies": [object()]}),
        ("options-response-class", {"default_response_class": dict}),
        ("options-unique-id-type", {"generate_unique_id_function": "route"}),
    ):
        yield label, partial(create, fastapi_options=options)
    for label, generate in (("unique-id", lambda route: f"{route.name}-id"), ("unique-id-result", lambda route: 7)):
        app = create(fastapi_options={"generate_unique_id_function": generate})
        yield f"options-{label}", partial(app.add_api_route, "/extra", lambda: None)
    yield "candidate-index", partial(auth.RequirementCandidate, requirement_index=True, credentials={})
    credential = auth.Credential(scheme_name="digest", payload=auth.CustomSecret(value="x"))
    yield "candidate-names", partial(auth.RequirementCandidate, requirement_index=0, credentials={"mtls": credential})
    yield "candidate", partial(auth.RequirementCandidate, requirement_index=0, credentials={"digest": credential})
