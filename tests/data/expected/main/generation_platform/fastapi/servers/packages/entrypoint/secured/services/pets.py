"""Implement PetsService of secured.services; secured.main imports Pets from this module.

datamodel-code-generator wrote this module once, with a stub for each method, and never overwrites it. Generating the
server again updates the Protocol, so type checkers point at the methods to change.
"""

from __future__ import annotations

import secured_models
from fastapi.responses import Response
from secured import HTTPResult
from secured.services import PetsService


class Pets(PetsService[object]):
    """Implement the pets operations."""

    def list_pets(
        self,
        *,
        principal: object,
    ) -> secured_models.PetsGetResponse | HTTPResult[secured_models.PetsGetResponse] | Response:
        """
        Handle GET /pets.
        """
        raise NotImplementedError
