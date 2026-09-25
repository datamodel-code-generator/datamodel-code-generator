"""Typed connections to the generated secured server: options, authorizers, extractors, and dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.routing import APIRoute
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware

from secured import (
    AsyncAuthorizer,
    Authorizer,
    CredentialExtractors,
    Dependency,
    FastAPIOptions,
    OperationDependencies,
    OperationKey,
    SchemeKey,
    build_router,
    create_app,
)
from secured.auth_types import AuthContext, Credential, CredentialExtractor, CustomSecret


@dataclass(frozen=True)
class Certificate:
    subject: str


def authorize(context: AuthContext[Certificate | str]) -> str:
    operation: OperationKey = context.operation_key
    for candidate in context.candidates:
        for name, credential in candidate.credentials.items():
            scheme: SchemeKey = name
            payload = credential.payload
            if isinstance(payload, CustomSecret) and isinstance(payload.value, Certificate):
                return f"{operation} {scheme} {payload.value.subject}"
    return operation


async def authorize_async(context: AuthContext[Certificate | str]) -> str:
    return context.operation_key


def digest(request: Request) -> Credential[str] | None:
    value = request.headers.get("x-digest")
    return None if value is None else Credential(scheme_name="digest", payload=CustomSecret(value=value))


async def certificate(request: Request) -> Credential[Certificate] | None:
    subject = request.headers.get("x-client-cert")
    return None if subject is None else Credential(scheme_name="mtls", payload=CustomSecret(value=Certificate(subject)))


def unique_id(route: APIRoute) -> str:
    return route.name


def record(request: Request) -> None:
    request.state.recorded = True


extractors: CredentialExtractors[Certificate | str] = {"digest": digest, "mtls": certificate}
digest_only: CredentialExtractor[str] = digest
authorizer: Authorizer[Certificate | str, str] = authorize
async_authorizer: AsyncAuthorizer[Certificate | str, str] = authorize_async
dependencies: list[Dependency] = [Depends(record)]
operation_dependencies: OperationDependencies = {"/paths/~1pets/get": dependencies}
options: FastAPIOptions = {
    "title": "Pets",
    "summary": None,
    "openapi_tags": [{"name": "pets", "description": "Pets."}],
    "servers": [{"url": "https://pets.example.com"}],
    "responses": {404: {"description": "Missing."}, "default": {"description": "Anything."}},
    "dependencies": dependencies,
    "middleware": [Middleware(GZipMiddleware)],
    "routes": [],
    "webhooks": APIRouter(),
    "default_response_class": PlainTextResponse,
    "generate_unique_id_function": unique_id,
    "strict_content_type": False,
}
handlers = {"list_pets": lambda **_: ["rex"]}
app: FastAPI = create_app(
    handlers=handlers,
    authorizer=authorizer,
    credential_extractors=extractors,
    dependencies=dependencies,
    operation_dependencies=operation_dependencies,
    prefix="/api",
    fastapi_options=options,
)
router: APIRouter = build_router(handlers=handlers, authorizer=async_authorizer, credential_extractors=extractors)
