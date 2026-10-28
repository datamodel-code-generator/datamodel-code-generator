"""Alias ownership must remain local to affected schema-generation passes."""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, Field
from pydantic_core import core_schema


class Plain(BaseModel):
    """An owner-free ordinary root."""

    first: int
    second: int
    third: int


class Duplicate(BaseModel):
    """The later list field owns the shared validation property."""

    frozen: frozenset[int] = Field(alias="wire")
    items: list[int] = Field(alias="wire")


class Container(BaseModel):
    """An ordinary outer root containing the affected nested model."""

    item: Duplicate


class NestedDuplicate(BaseModel):
    """Nested duplicate fields must restore their outer ownership delegate."""

    old: Duplicate = Field(alias="item")
    new: Duplicate = Field(alias="item")


class InlineValues:
    """A native inline schema with colliding field aliases and no reference wrapper."""

    @classmethod
    def __get_pydantic_core_schema__(  # noqa: PLW3201 - Pydantic custom-type schema protocol.
        cls, _source: object, _handler: object
    ) -> core_schema.CoreSchema:
        """Supply an inline validation schema through Pydantic's native protocol."""
        return core_schema.typed_dict_schema({
            "old": core_schema.typed_dict_field(
                core_schema.frozenset_schema(core_schema.int_schema()),
                validation_alias="wire",
                serialization_alias="wire",
            ),
            "new": core_schema.typed_dict_field(
                core_schema.list_schema(core_schema.int_schema()),
                validation_alias="wire",
                serialization_alias="wire",
            ),
        })


class InlineDuplicate(BaseModel):
    """Nested inline ownership temporarily overlays the outer owner delegate."""

    old: InlineValues = Field(alias="item")
    new: InlineValues = Field(alias="item")


owner_calls: Counter[str] = Counter()


def record_owner_calls(frame: Any, event: str, _arg: Any) -> None:
    """Count real owner work during public conversion of these external models."""
    if (
        event == "call"
        and frame.f_code.co_filename.replace("\\", "/").endswith("/datamodel_code_generator/input_model.py")
        and frame.f_code.co_name in {"generate_owned_field", "_clear_field_schema_names"}
    ):
        owner_calls[frame.f_code.co_name] += 1
