"""Union branch provenance and unchanged ordered controls."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Union

from pydantic import BaseModel, Field


class IntFirst(BaseModel):
    value: Union[int, Callable[[int], int]]


class CallableFirst(BaseModel):
    value: Union[Callable[[int], int], int]


class PipeIntFirst(BaseModel):
    value: int | Callable[[int], int]


class PipeCallableFirst(BaseModel):
    value: Callable[[int], int] | int


class NoneFirst(BaseModel):
    value: Union[None, int, Callable[[int], int]]


class NoneMiddle(BaseModel):
    value: Union[int, None, Callable[[int], int]]


class NoneLast(BaseModel):
    value: Union[int, Callable[[int], int], None]


class OptionalCallableFirst(BaseModel):
    value: Union[Callable[[int], int], int, None]


class MultipleCallables(BaseModel):
    value: Union[int, Callable[[int], int], Callable[[str], str]]


class CustomValue:
    """A second unsupported type distinct from Callable."""


class ArbitraryBase(BaseModel):
    model_config = {"arbitrary_types_allowed": True}


class MultipleUnsupported(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    value: Union[int, CustomValue, Callable[[int], int]]


class ListUnion(BaseModel):
    value: list[Union[int, Callable[[int], int]]]


class UnionList(BaseModel):
    value: Union[int, list[Callable[[int], int]]]


class NestedContainer(BaseModel):
    value: dict[str, list[Union[int, Callable[[int], int]]]]


class AnnotatedUnion(BaseModel):
    value: Union[Annotated[Union[int, Callable[[int], int]], Field(title="Inner choice")], str]


class OrdinaryUnion(BaseModel):
    value: Union[int, str, None]


class NestedModels(BaseModel):
    value: IntFirst


class DuplicateSerializable(BaseModel):
    value: Union[Annotated[int, "duplicate primitive annotation"], int, Callable[[int], int]]


class BooleanSchemaContainer(BaseModel):
    value: Union[int, Annotated[dict[str, Callable[[int], int]], Field(json_schema_extra={"items": False})]]


class DocumentationControl(BaseModel):
    value: str = Field(
        json_schema_extra={"examples": [{"x-python-unserializable": True, "x-python-union-branch": 0}]}
    )
