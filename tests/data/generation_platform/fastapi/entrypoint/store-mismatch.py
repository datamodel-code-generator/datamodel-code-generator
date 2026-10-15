"""Implement StoreService with a keyword argument its route does not pass."""

from __future__ import annotations

from app import models
from app.server.services import StoreService


class Store(StoreService):
    """Count the pets of one warehouse."""

    def get_inventory(self, warehouse: str) -> models.StoreInventoryGetResponse:  # type: ignore[override]
        """Return the inventory of a warehouse."""
        return models.StoreInventoryGetResponse({warehouse: 0})
