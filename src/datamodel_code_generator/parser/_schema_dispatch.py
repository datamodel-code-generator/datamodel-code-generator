"""Choose the parse method of a schema position from one ordered table.

A position is a place where the parser meets a schema object (``Position``). Its table holds, in order, the steps that
rewrite the site before the rows after them read it (``Step``: a ``$ref`` merged with its sibling keywords) and the
rows that choose the parse method (``Row``); the first row that applies builds the result, else the table's
``Default``.
The parser keeps the predicates and builders: rows call its public hooks through the parser, so overriding subclasses
keep working.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Generic, TypeVar

from typing_extensions import final

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

__all__ = ["Choice", "Default", "DispatchTable", "Position", "Row", "SchemaSite", "Step"]

ParserT_contra = TypeVar("ParserT_contra", contravariant=True)
SchemaT = TypeVar("SchemaT")
ResultT_co = TypeVar("ResultT_co", covariant=True)


class Position(Enum):
    """A place where the parser meets a schema object; each has one table."""

    DEFINITION = "definition"
    """A schema that becomes a model of its own: a definition, a root, a reserved target, a payload."""
    FIELD = "field"
    """A schema whose $ref siblings are merged that types a field, an item or a map value."""
    OPENAPI_OPERATION = "openapi-operation"
    """A request body, response or parameter schema of the legacy OpenAPI scopes."""


@final
@dataclass(frozen=True, slots=True, eq=False)
class SchemaSite(Generic[SchemaT]):
    """A schema object at a position, with what its rows read beside it."""

    name: str
    obj: SchemaT
    path: list[str]
    singular_name: bool = False
    parent: SchemaT | None = None
    scope: AbstractContextManager[None] | None = None
    """The dynamic scope of a merged ``$ref`` that stands for another schema resource."""


@final
@dataclass(frozen=True, slots=True)
class Step(Generic[ParserT_contra, SchemaT]):
    """A rewrite of the site that the entries after it read."""

    name: str
    applies: Callable[[ParserT_contra, SchemaSite[SchemaT]], bool]
    rewrite: Callable[[ParserT_contra, SchemaSite[SchemaT]], SchemaSite[SchemaT]]


@final
@dataclass(frozen=True, slots=True)
class Row(Generic[ParserT_contra, SchemaT, ResultT_co]):
    """A parse method and the sites it applies to."""

    name: str
    applies: Callable[[ParserT_contra, SchemaSite[SchemaT]], bool]
    build: Callable[[ParserT_contra, SchemaSite[SchemaT]], ResultT_co]


@final
@dataclass(frozen=True, slots=True)
class Default(Generic[ParserT_contra, SchemaT, ResultT_co]):
    """The parse method of the sites no row of a table applies to."""

    name: str
    build: Callable[[ParserT_contra, SchemaSite[SchemaT]], ResultT_co]


@final
@dataclass(frozen=True, slots=True)
class Choice(Generic[ParserT_contra, SchemaT, ResultT_co]):
    """What a table chooses for a site: the names of the entries taken, their parse method and the rewritten site."""

    path: tuple[str, ...]
    build: Callable[[ParserT_contra, SchemaSite[SchemaT]], ResultT_co]
    site: SchemaSite[SchemaT]


@final
@dataclass(frozen=True, slots=True)
class DispatchTable(Generic[ParserT_contra, SchemaT, ResultT_co]):
    """The ordered entries of a position and the parse method of the sites none of its rows applies to."""

    entries: tuple[Step[ParserT_contra, SchemaT] | Row[ParserT_contra, SchemaT, ResultT_co], ...]
    default: Default[ParserT_contra, SchemaT, ResultT_co]

    def choose(self, parser: ParserT_contra, site: SchemaSite[SchemaT]) -> Choice[ParserT_contra, SchemaT, ResultT_co]:
        """Return the first row that applies to the site after the steps before it, else the default."""
        steps: list[str] = []
        for entry in self.entries:
            if not entry.applies(parser, site):
                continue
            if isinstance(entry, Row):
                return Choice((*steps, entry.name), entry.build, site)
            site = entry.rewrite(parser, site)
            steps.append(entry.name)
        return Choice((*steps, self.default.name), self.default.build, site)
