"""Native nested types whose owners are classes, not modules."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel


class Parent(BaseModel):
    inherited: int = 1


class Outer:
    class Inner(Parent):
        value: int

    class Deeper:
        class Leaf(BaseModel):
            label: str

    class Kind(str, Enum):
        FIRST = "first"
        SECOND = "second"

    @dataclass
    class Record:
        number: int

    class Node(BaseModel):
        value: int
        child: Outer.Node | None = None


class NestedRoot(BaseModel):
    child: Outer.Inner
    leaf: Outer.Deeper.Leaf
    kind: Outer.Kind
    record: Outer.Record
    node: Outer.Node
    tests: str | None = None


Outer.Node.model_rebuild()


def __getattr__(name: str):
    if name in {"Inner", "Leaf", "Kind", "Record", "Node"}:
        raise RuntimeError("Nested types must be reached through their owners")
    raise AttributeError(name)


class ContainerRoot(BaseModel):
    items: deque[Outer.Inner]
    tests: str | None = None
