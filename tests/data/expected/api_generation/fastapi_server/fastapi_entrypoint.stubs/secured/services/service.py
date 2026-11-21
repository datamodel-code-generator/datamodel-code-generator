"""Implement Service of secured.services; secured.main imports Operations from this module.

datamodel-code-generator wrote this module once, with a stub for each method, and never overwrites it. Generating the
server again updates the Protocol, so type checkers point at the methods to change.
"""

from __future__ import annotations

from fastapi.responses import Response
from secured import HTTPResult
from secured.services import Service


class Operations(Service):
    """Implement the service operations."""

    def get_public(
        self,
    ) -> None | HTTPResult[None] | Response:
        """
        Handle GET /public.
        """
        raise NotImplementedError
