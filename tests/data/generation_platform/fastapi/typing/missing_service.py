"""Serve Pydantic dataclasses whose unset members hold MISSING: read a member a request left out, leave one out."""

from __future__ import annotations

from fastapi import FastAPI
from pets_models import (
    LoginPostRequest,
    PetsGetQueryFilterParameter,
    PetsGetResponse,
    ProfilePutRequest,
    UploadPostRequest,
    Pet,
)
from pydantic import MISSING
from typing_extensions import assert_type

from pets import create_app
from pets.services import DefaultService


class Default(DefaultService):
    def list_pets(self, *, filter: PetsGetQueryFilterParameter | None) -> PetsGetResponse:
        owner = None if filter is None or filter.owner is MISSING else filter.owner
        return [Pet(name="a", owner=owner)]

    def create_pet(self, *, body: Pet) -> Pet:
        if body.tag is MISSING:
            return Pet(name=body.name, owner=body.owner, nick=None)
        assert_type(body.tag, str)
        return body

    def login(self, *, body: LoginPostRequest) -> None:
        assert_type(body.note, str | None | MISSING)

    def upload(self, *, body: UploadPostRequest) -> None:
        assert_type(body.file, bytes)

    def update_profile(self, *, body: ProfilePutRequest) -> None:
        assert_type(body.note, str | None | MISSING)


app: FastAPI = create_app(default=Default())
