"""Models with mixed types for reuse-foreign same-family tests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import msgspec
from pydantic import BaseModel, ConfigDict
from typing_extensions import TypedDict


class Category(Enum):
    """Enum that should always be reused."""

    A = "a"
    B = "b"


class NestedTypedDict(TypedDict):
    """TypedDict that should be reused when output is TypedDict."""

    key: str
    value: int


class NestedPydantic(BaseModel):
    """Pydantic model that should be regenerated when output is TypedDict."""

    name: str
    age: int


@dataclass
class NestedDataclass:
    """Dataclass that should be reused when output is dataclass."""

    title: str
    count: int


class ModelWithTypedDict(BaseModel):
    """Pydantic model containing a TypedDict field."""

    data: NestedTypedDict
    category: Category


class ModelWithPydantic(BaseModel):
    """Pydantic model containing another Pydantic model."""

    nested: NestedPydantic
    category: Category


class ModelWithDataclass(BaseModel):
    """Pydantic model containing a dataclass field."""

    info: NestedDataclass
    category: Category


class ModelWithMixed(BaseModel):
    """Pydantic model with TypedDict, Pydantic, and dataclass nested types."""

    typed_dict_field: NestedTypedDict
    pydantic_field: NestedPydantic
    dataclass_field: NestedDataclass
    category: Category


class NestedStruct(msgspec.Struct):
    """msgspec Struct, for which Pydantic has no schema."""

    name: str


class ModelWithStruct(BaseModel):
    """Pydantic model containing a msgspec Struct field."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    info: NestedStruct
    category: Category


@dataclass
class ExportOptions:
    """Dataclass with a field named like the fields attribute of a Pydantic model."""

    name: str
    model_fields: tuple[str, ...] = ()


class ModelWithExportOptions(BaseModel):
    """Pydantic model containing a dataclass that has a field named ``model_fields``."""

    options: ExportOptions
