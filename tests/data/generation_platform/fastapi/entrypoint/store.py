"""Implement StoreService of app.server.services: the inventory by pet kind."""

from __future__ import annotations

from app import models
from app.server.services import StoreService


class Store(StoreService):
    """Count the pets in stock by kind."""

    def get_inventory(self) -> models.StoreInventoryGetResponse:
        """Return the inventory."""
        return models.StoreInventoryGetResponse({"cats": 2, "dogs": 1})
