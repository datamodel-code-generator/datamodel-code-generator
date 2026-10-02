"""Services of the unbound server: report converted parameters and return a Value model."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def services(_server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return the service for the unbound parameter operation."""

    class Untagged:
        def get_value(self, *, id: int, at: object, tags: object) -> object:  # ruff: ignore[builtin-argument-shadowing]
            """Record the typed path and query values and return their Value response."""
            calls.append(f"get_value(id={id!r}, at={at!r}, tags={tags!r})")
            return models.Value(id=id)

    return {"default": {"untagged": Untagged()}}
