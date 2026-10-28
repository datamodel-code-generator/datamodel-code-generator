"""Implement StoreService with a decorator that supplies an argument of the method it wraps."""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

from app import models
from app.server.services import StoreService

if TYPE_CHECKING:
    from collections.abc import Callable


def counted(method: Callable[..., models.StoreInventoryGetResponse]) -> Callable[..., object]:
    """Pass the method a count of its own."""

    @functools.wraps(method)
    def wrapper(self: object) -> models.StoreInventoryGetResponse:
        return method(self, count=3)

    return wrapper


class Store(StoreService):
    """Count the pets in stock."""

    @counted
    def get_inventory(self, count: int) -> models.StoreInventoryGetResponse:  # type: ignore[override]
        """Return the inventory."""
        return models.StoreInventoryGetResponse({"cats": count})
