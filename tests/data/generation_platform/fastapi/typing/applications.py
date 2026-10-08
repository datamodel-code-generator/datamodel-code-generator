"""Typed connections to the generated secured server: services, FastAPI arguments, authorize callbacks, and dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, FastAPI, Request, params
from fastapi.responses import PlainTextResponse
from fastapi.routing import APIRoute
from secured_models import FieldPetsGetResponse
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware

from secured import (
    AsyncAuthorize,
    Authorize,
    Credentials,
    HTTPResult,
    OperationDependencies,
    RequirementSets,
    build_router,
    create_app,
)
from secured.routers import pets, public
from secured.services import PetsService, UntaggedService


@dataclass(frozen=True)
class Certificate:
    subject: str


@dataclass(frozen=True)
class User:
    name: str


@dataclass(frozen=True)
class Admin(User):
    pass


def authorize(requirement_sets: RequirementSets, credentials: Credentials) -> User:
    names = [name for requirement in requirement_sets for name, _ in requirement]
    certificate = credentials.get("mtls")
    subject = certificate.subject if isinstance(certificate, Certificate) else ""
    return User(f"{names} {subject}")


async def authorize_async(requirement_sets: RequirementSets, credentials: Credentials) -> Admin:
    return Admin(f"{len(requirement_sets)} {len(credentials)}")


def unique_id(route: APIRoute) -> str:
    return route.name


def record(request: Request) -> None:
    request.state.recorded = True


class Pets(PetsService[User]):
    def list_pets(self, *, principal: User) -> FieldPetsGetResponse:
        return store[principal.name]


class Public:
    def get_public(self) -> None:
        return None


class Untagged(UntaggedService[User]):
    async def get_maybe(self, *, principal: User | None, query_principal: str | None) -> PlainTextResponse:
        name = "anonymous" if principal is None else principal.name
        return PlainTextResponse(name if query_principal is None else query_principal)

    def put_pet(self, *, principal: object, pet_id: int) -> HTTPResult[None]:
        return HTTPResult(204, headers={"x-pet": f"{principal} {pet_id}"})

    def get_session(self, *, principal: User) -> None:
        del principal

    async def get_custom(self, *, principal: User) -> None:
        del principal


store: dict[str, FieldPetsGetResponse] = {}
admin_pets: PetsService[Admin] = Pets()
authorizer: Authorize[User] = authorize
async_authorizer: AsyncAuthorize[Admin] = authorize_async
dependencies: list[params.Depends] = [Depends(record)]
operation_dependencies: OperationDependencies = {"list_pets": dependencies}
app: FastAPI = create_app(
    pets=Pets(),
    public=Public(),
    untagged=Untagged(),
    authorize=authorizer,
    operation_dependencies=operation_dependencies,
    prefix="/api",
    title="Pets",
    summary=None,
    openapi_tags=[{"name": "pets", "description": "Pets."}],
    servers=[{"url": "https://pets.example.com"}],
    responses={404: {"description": "Missing."}, "default": {"description": "Anything."}},
    dependencies=dependencies,
    middleware=[Middleware(GZipMiddleware)],
    routes=[],
    webhooks=APIRouter(),
    default_response_class=PlainTextResponse,
    generate_unique_id_function=unique_id,
    strict_content_type=False,
)
router: APIRouter = build_router(
    pets=Pets(),  # ty: ignore[invalid-argument-type]
    public=Public(),
    untagged=Untagged(),  # ty: ignore[invalid-argument-type]
    authorize=async_authorizer,
)
pets_router: APIRouter = pets.build_router(pets=Pets(), authorize=authorizer)
public_router: APIRouter = public.build_router(public=Public())
