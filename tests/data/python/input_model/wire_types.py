"""Python types whose validation schema uses wire property names."""

from __future__ import annotations

import typing
from collections.abc import Callable
from typing import Any

from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field

Callback = Callable[[int], int]


class Plain(BaseModel):
    """Plain validation property naming fixture."""

    first: int
    values: frozenset[int]
    callback: Callback
    last: str


class Aliased(BaseModel):
    """Aliased validation property naming fixture."""

    first: int
    values: frozenset[int] = Field(alias="wire_values")
    callback: Callback = Field(alias="wire_callback")
    last: str


class Validation(BaseModel):
    """Validation validation property naming fixture."""

    first: int
    values: frozenset[int] = Field(alias="ignored_values", validation_alias="wire_values")
    callback: Callback = Field(alias="ignored_callback", validation_alias="wire_callback")
    last: str


class Choices(BaseModel):
    """Choices validation property naming fixture."""

    first: int
    values: frozenset[int] = Field(validation_alias=AliasChoices(AliasPath("data", "values"), "wire_values", "other"))
    callback: Callback = Field(validation_alias=AliasChoices("wire_callback", "other_callback"))
    last: str


class Paths(BaseModel):
    """Paths validation property naming fixture."""

    first: int
    values: frozenset[int] = Field(validation_alias=AliasPath("data", "values"))
    callback: Callback = Field(validation_alias=AliasPath("callback"))
    last: str


class Serialization(BaseModel):
    """Serialization validation property naming fixture."""

    first: int
    values: frozenset[int] = Field(serialization_alias="wire_values")
    callback: Callback = Field(serialization_alias="wire_callback")
    last: str


class Swapped(BaseModel):
    """Swapped validation property naming fixture."""

    first: int
    values: frozenset[int] = Field(alias="callback")
    callback: Callback = Field(alias="values")
    last: str


NestedCallback = typing.Callable[[int], int]


class NestedItem(BaseModel):
    """Nested wire types also supported by Python 3.10 introspection."""

    first: int
    values: int = Field(alias="wire_values")
    callback: NestedCallback = Field(alias="wire_callback")
    last: str


class Nested(BaseModel):
    """Nested validation property naming fixture."""

    item: NestedItem


def hide_optional_property(schema: dict[str, Any]) -> None:
    """Omit an internal defaulted field from the published schema."""
    schema["properties"].pop("hidden")


class Hidden(Plain):
    """Hidden validation property naming fixture."""

    model_config = ConfigDict(json_schema_extra=hide_optional_property)
    hidden: frozenset[int] = frozenset()
