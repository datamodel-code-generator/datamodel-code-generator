"""Implement StoreService of models.server.services; models.server.main imports Store from this module.

datamodel-code-generator wrote this module once, with a stub for each method, and never overwrites it. Generating the
server again updates the Protocol, so type checkers point at the methods to change.
"""

from __future__ import annotations

import models
from fastapi.responses import Response
from models.server import HTTPResult
from models.server.services import StoreService


class Store(StoreService):
    """Implement the store operations."""

    def list_orders(
        self,
    ) -> models.OrdersGetResponse | HTTPResult[models.OrdersGetResponse] | Response:
        """
        Handle GET /orders.
        """
        raise NotImplementedError
