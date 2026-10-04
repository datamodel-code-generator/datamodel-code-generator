"""A custom Pydantic base of the queued order whose validation runs controlled restoration work."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, model_validator

if TYPE_CHECKING:
    from collections.abc import Callable

restoring: Callable[[], None] | None = None


class RestoredOrder(BaseModel):
    """Run the controlled restoration work whenever Pydantic validates an order."""

    @model_validator(mode="before")
    @classmethod
    def restore(cls, data: Any) -> Any:
        """Consume the controlled restoration work before Pydantic validates the order."""
        if restoring is not None:
            restoring()
        return data
