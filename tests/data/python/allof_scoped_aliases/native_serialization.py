"""Native Pydantic field serialization with an explicitly selected wire key."""

from __future__ import annotations

from pydantic import BaseModel, Field, create_model


def root_model(serialization_alias: str | None) -> type[BaseModel]:
    """Create a native model using Pydantic's public alias API."""
    child = create_model(
        "NativeChild",
        base=(int, ...),
        renamed=(str, Field(alias="x", serialization_alias=serialization_alias)),
        y=(int, 2),
        z=(bool, True),
    )
    return create_model("NativeRoot", child=(child, ...))
