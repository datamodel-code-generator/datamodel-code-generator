"""Implement StoreService with an annotation whose name only type checkers import."""

from typing import TYPE_CHECKING

from app import models
from app.server.services import StoreService

if TYPE_CHECKING:
    from decimal import Decimal


class Store(StoreService):
    """Count the pets in stock."""

    def get_inventory(self, *, scale: Decimal | None = None) -> models.StoreInventoryGetResponse:
        """Return the inventory."""
        return models.StoreInventoryGetResponse({"cats": 2 if scale is None else int(scale)})
