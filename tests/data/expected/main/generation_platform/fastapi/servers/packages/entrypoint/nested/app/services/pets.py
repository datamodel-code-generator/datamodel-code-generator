"""Implement PetsService of app.server.services; app.server.main imports Pets from this module.

datamodel-code-generator wrote this module once, with a stub for each method, and never overwrites it. Generating the
server again updates the Protocol, so type checkers point at the methods to change.
"""

from __future__ import annotations

from uuid import UUID

from app import models
from app.server import HTTPResult
from app.server.services import PetsService
from fastapi.responses import Response
from pydantic import AwareDatetime


class Pets(PetsService):
    """Implement the pets operations."""

    def list_pets(
        self,
        *,
        limit: int,
        tags: list[str] | None,
        kind: models.PetsGetQueryKindParameter | None,
        X_Request_Id: UUID | None,
        since: AwareDatetime | None,
        session: str | None,
    ) -> models.PetsGetResponse | HTTPResult[models.PetsGetResponse | models.Error] | Response:
        """
        Handle GET /pets.
        """
        raise NotImplementedError

    def create_pet(
        self,
        *,
        body: models.NewPet,
    ) -> models.Pet | HTTPResult[models.Pet] | Response:
        """
        Handle POST /pets.
        """
        raise NotImplementedError

    def list_my_pets(
        self,
    ) -> models.PetsMineGetResponse | HTTPResult[models.PetsMineGetResponse] | Response:
        """
        Handle GET /pets/mine.
        """
        raise NotImplementedError

    def get_pet(
        self,
        *,
        petId: int,
    ) -> models.Pet | HTTPResult[models.Pet | models.Error] | Response:
        """
        Handle GET /pets/{petId}.
        """
        raise NotImplementedError

    def delete_pet(
        self,
        *,
        petId: int,
    ) -> None | HTTPResult[None] | Response:
        """
        Handle DELETE /pets/{petId}.
        """
        raise NotImplementedError
