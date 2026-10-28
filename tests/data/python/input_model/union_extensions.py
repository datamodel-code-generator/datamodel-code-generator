"""Caller-owned union extensions alongside generated branch provenance."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from pydantic import BaseModel, Field

Callback = Callable[[int], int]


class Serializable(BaseModel):
    """A serializable union must keep caller schema extensions unchanged."""

    value: int | str = Field(
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "x-python-union-branch": 17},
                {"type": "string", "x-python-union-branch": {"label": "metadata"}},
            ]
        }
    )


class Mixed(BaseModel):
    """Both serializable and unsupported choices may carry caller extensions."""

    value: (
        Annotated[int, Field(json_schema_extra={"x-python-union-branch": 99})]
        | Annotated[Callback, Field(json_schema_extra={"x-python-union-branch": 0})]
        | None
    )


class Container(BaseModel):
    """Restoring complete containers must retain their extension metadata."""

    value: (
        int
        | Annotated[
            list[Annotated[Callback, Field(json_schema_extra={"x-python-union-branch": "item"})]],
            Field(json_schema_extra={"x-python-union-branch": ["container", 0]}),
        ]
    )


class Nested(BaseModel):
    """Flattened outer unions must retain the original inner extension."""

    value: (
        str
        | Annotated[
            int | Annotated[Callback, Field(json_schema_extra={"x-python-union-branch": False})],
            "flattenable union metadata",
        ]
    )
