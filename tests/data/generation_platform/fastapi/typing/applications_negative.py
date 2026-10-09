"""Connections to the generated secured server that type checkers must reject: one error on each line marked error."""

from __future__ import annotations

from applications import Admin, Pets, Public, Untagged, User, authorize, store
from fastapi import FastAPI
from secured_models import FieldPetsGetResponse

from secured import Authorize, OperationDependencies, RequirementSets, create_app
from secured.services import PetsService, UntaggedService


def authorize_text(requirement_sets: RequirementSets) -> str:
    return str(requirement_sets)


class Counted(PetsService[User]):
    def list_pets(self, *, principal: User) -> int:  # error
        return len(principal.name)


class Required(UntaggedService[User]):
    async def get_maybe(self, *, principal: User, query_principal: str | None) -> None:  # error
        del principal, query_principal


class Blocking(UntaggedService[User]):
    def get_maybe(self, *, principal: User | None, query_principal: str | None) -> None:  # error
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


dependencies: OperationDependencies = {"/paths/~1nope/get": []}  # error
method_key: OperationDependencies = {"list_pets": []}  # error
narrow: Authorize[str] = authorize_text  # error
incomplete = Incomplete()  # error
structural: PetsService[User] = Wrong()  # error
admins: FastAPI = create_app(pets=Admins(), public=Public(), untagged=Untagged(), authorize=authorize)  # error
create_app(public=Public(), untagged=Untagged(), authorize=authorize)  # error
create_app(pets=Pets(), public=Public(), untagged=Untagged())  # error
