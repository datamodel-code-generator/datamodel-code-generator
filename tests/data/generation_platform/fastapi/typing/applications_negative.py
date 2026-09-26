"""Connections to the generated secured server that type checkers must reject: one error on each line marked error."""

from __future__ import annotations

from applications import Admin, Pets, Public, Untagged, User, authorize, store
from fastapi import FastAPI, Request
from secured_models import FieldPetsGetResponse

from secured import FastAPIOptions, OperationDependencies, Unset, create_app, install_openapi
from secured.auth_types import AuthContext, Authorizer, Credential, CredentialExtractor, CustomSecret
from secured.services import PetsService, UntaggedService


def authorize_text(context: AuthContext[str]) -> str:
    return context.operation_key


def wrong_scheme(request: Request) -> Credential[str] | None:
    return None


def raw_digest(request: Request) -> Credential[bytes] | None:
    return None


class Settings:
    pass


class Counted(PetsService[User]):
    def list_pets(self, *, principal: User) -> int:  # error
        return len(principal.name)


class Required(UntaggedService[User]):
    async def get_maybe(self, *, principal: User, query_principal: str | Unset) -> None:  # error
        del principal, query_principal


class Blocking(UntaggedService[User]):
    def get_maybe(self, *, principal: User | None, query_principal: str | Unset) -> None:  # error
        del principal, query_principal


class Hurried(UntaggedService[User]):
    async def put_pet(self, *, principal: User, pet_id: int) -> None:  # error
        del principal, pet_id


class Anonymous(UntaggedService[User]):
    def put_pet(self, *, principal: User) -> None:  # error
        del principal


class Admins(PetsService[Admin]):
    def list_pets(self, *, principal: Admin) -> FieldPetsGetResponse:
        return store[principal.name]


class Incomplete(PetsService[User]):
    pass


class Wrong:
    def list_pets(self, *, principal: User) -> int:
        return len(principal.name)


unknown: FastAPIOptions = {"lifespan": None}  # error
classes: FastAPIOptions = {"contact": {"model": Settings}}  # error
response_class: FastAPIOptions = {"default_response_class": dict}  # error
flag: FastAPIOptions = {"debug": "yes"}  # error
dependencies: OperationDependencies = {"/paths/~1nope/get": []}  # error
method_key: OperationDependencies = {"list_pets": []}  # error
scheme = Credential[str](scheme_name="nope", payload=CustomSecret(value="x"))  # error
narrow: Authorizer[int, str] = authorize_text  # error
extractor: CredentialExtractor[bytes] = wrong_scheme  # error
incomplete = Incomplete()  # error
structural: PetsService[User] = Wrong()  # error
admins: FastAPI = create_app(pets=Admins(), public=Public(), untagged=Untagged(), authorizer=authorize)  # error
create_app(public=Public(), untagged=Untagged(), authorizer=authorize)  # error
create_app(pets=Pets(), public=Public(), untagged=Untagged())  # error
secret: FastAPI = create_app(pets=Pets(), public=Public(), untagged=Untagged(), authorizer=authorize, credential_extractors={"digest": raw_digest})  # error
install_openapi(FastAPI(), operation_keys=("/paths/~1nope/get",))  # error
