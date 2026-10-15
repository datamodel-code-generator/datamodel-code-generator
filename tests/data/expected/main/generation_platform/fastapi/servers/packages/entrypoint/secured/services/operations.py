"""Implement OperationsService of secured.services; secured.main imports Operations from this module.

datamodel-code-generator wrote this module once, with a stub for each method, and never overwrites it. Generating the
server again updates the Protocol, so type checkers point at the methods to change.
"""

from __future__ import annotations

from fastapi.responses import Response
from secured import HTTPResult
from secured.services import OperationsService


class Operations(OperationsService[object]):
    """Implement the operations operations."""

    def get_maybe(
        self,
        *,
        principal: object | None,
        principal_1: str | None,
    ) -> None | HTTPResult[None] | Response:
        """
        Handle GET /maybe.
        """
        raise NotImplementedError

    def put_pet(
        self,
        *,
        principal: object,
        petId: int,
    ) -> None | HTTPResult[None] | Response:
        """
        Handle PUT /pets/{petId}.
        """
        raise NotImplementedError

    def get_session(
        self,
        *,
        principal: object,
    ) -> None | HTTPResult[None] | Response:
        """
        Handle GET /session.
        """
        raise NotImplementedError

    def get_custom(
        self,
        *,
        principal: object,
    ) -> None | HTTPResult[None] | Response:
        """
        Handle GET /custom.
        """
        raise NotImplementedError
