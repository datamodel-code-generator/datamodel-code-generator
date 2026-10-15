"""Implement StoreService of app.server.services; app.server.main imports Store from this module.

datamodel-code-generator wrote this module once, with a stub for each method, and never overwrites it. Generating the
server again updates the Protocol, so type checkers point at the methods to change.
"""

from __future__ import annotations

from app import models
from app.server import HTTPResult
from app.server.services import StoreService
from fastapi.responses import Response


class Store(StoreService):
    """Implement the store operations."""

    def get_inventory(
        self,
    ) -> models.StoreInventoryGetResponse | HTTPResult[models.StoreInventoryGetResponse] | Response:
        """
        Handle GET /store/inventory.
        """
        raise NotImplementedError
