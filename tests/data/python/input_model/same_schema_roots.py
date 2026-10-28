"""Single-root type identity collisions and ordinary reuse controls."""

from __future__ import annotations

from pydantic import BaseModel
from typing_extensions import TypeAliasType

from tests.data.python.input_model.same_schema_a import Alias as AliasA
from tests.data.python.input_model.same_schema_a import Marker as MarkerA
from tests.data.python.input_model.same_schema_a import Node as NodeA
from tests.data.python.input_model.same_schema_a import Recursive as RecursiveA
from tests.data.python.input_model.same_schema_b import Alias as AliasB
from tests.data.python.input_model.same_schema_b import Marker as MarkerB
from tests.data.python.input_model.same_schema_b import Node as NodeB
from tests.data.python.input_model.same_schema_b import Recursive as RecursiveB


class Both(BaseModel):
    """A root containing native field types."""

    left: NodeA
    right: NodeB


class Reversed(BaseModel):
    """A root containing native field types."""

    right: NodeB
    left: NodeA


class Shared(BaseModel):
    """A root containing native field types."""

    left: NodeA
    right: NodeA


Number = TypeAliasType("Number", int)


class Child(Both):
    """A root containing native field types."""

    count: Number = 0


class UnionRoot(BaseModel):
    """A root containing native field types."""

    item: NodeA | NodeB


class RecursiveRoot(BaseModel):
    """A root containing native field types."""

    left: RecursiveA
    right: RecursiveB


class EnumRoot(BaseModel):
    """A root containing native field types."""

    left: MarkerA
    right: MarkerB


class AliasRoot(BaseModel):
    """A root using re-exported native classes with preserved qualnames."""

    left: AliasA
    right: AliasB


TYPE_PAIRS = ((NodeA, NodeB), (RecursiveA, RecursiveB), (MarkerA, MarkerB), (AliasA, AliasB))
