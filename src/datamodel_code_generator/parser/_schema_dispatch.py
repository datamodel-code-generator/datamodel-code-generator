"""Choose the parse method of a schema position from one ordered table.

A position is a place where the parser meets a schema object: a definition (a schema that becomes a model of its own), a
field, item or map value, or a body, response or parameter of the legacy OpenAPI scopes. Its table holds, in order, the
steps that rewrite the site before the rows after them read it (``Step``: a ``$ref`` merged with its sibling keywords)
and the rows that choose the parse method (``Row``); the first row that applies builds the result, else the table's
``Default``.
The parser keeps the predicates and builders: rows call its public hooks through the parser, so overriding subclasses
keep working.

A table also declares what its sites type (``TypedChild``): the child schemas and the models their types name. A row
that types other children than the rest of its table declares its own (``Row.typed``); readers that must follow
what a parse types, such as the request/response split, ask the table (``DispatchTable.typed_children``) instead of
walking the schema themselves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Generic, Literal, TypeAlias, TypeVar

from typing_extensions import final

from datamodel_code_generator._records import record

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from contextlib import AbstractContextManager

__all__ = ["ChildPosition", "Choice", "Default", "DispatchTable", "Row", "SchemaSite", "Step", "TypedChild"]

ParserT_contra = TypeVar("ParserT_contra", contravariant=True)
SchemaT = TypeVar("SchemaT")
ResultT_co = TypeVar("ResultT_co", covariant=True)
ChildPosition: TypeAlias = Literal["model", "member", "fields", "ref", "key"]
"""How a site types a child: see ``TypedChild``."""


@final
@record(eq=False)
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
@record(eq=False)
class TypedChild(Generic[SchemaT]):
    """A child a site types, or a model its type names.

    ``model``: a ``schema`` typed as a model of its own (a property, item, map value or branch). ``member``: an allOf
    member ``schema`` merged into the model built from ``into``. ``fields``: the ``schema`` whose properties are the
    fields of the site's model. ``ref``: a ``$ref`` (``reference``) the type names. ``key``: the resolved key
    (``reference``) of a model the type names without typing it here.
    """

    position: ChildPosition
    schema: SchemaT | None = None
    reference: str | None = None
    into: SchemaT | None = None


@final
@record
class Step(Generic[ParserT_contra, SchemaT]):
    """A rewrite of the site that the entries after it read."""

    name: str
    applies: Callable[[ParserT_contra, SchemaSite[SchemaT]], bool]
    rewrite: Callable[[ParserT_contra, SchemaSite[SchemaT]], SchemaSite[SchemaT]]


@final
@record
class Row(Generic[ParserT_contra, SchemaT, ResultT_co]):
    """A parse method and the sites it applies to."""

    name: str
    applies: Callable[[ParserT_contra, SchemaSite[SchemaT]], bool]
    build: Callable[[ParserT_contra, SchemaSite[SchemaT]], ResultT_co]
    typed: Callable[[ParserT_contra, SchemaSite[SchemaT]], Iterable[TypedChild[SchemaT]]] | None = None
    """What the row types at a site where it types other children than its table declares."""


@final
@record
class Default(Generic[ParserT_contra, SchemaT, ResultT_co]):
    """The parse method of the sites no row of a table applies to."""

    name: str
    build: Callable[[ParserT_contra, SchemaSite[SchemaT]], ResultT_co]


@final
@record
class Choice(Generic[ParserT_contra, SchemaT, ResultT_co]):
    """What a table chooses for a site: the entries taken, their parse method, the rewritten site and what it types."""

    path: tuple[str, ...]
    build: Callable[[ParserT_contra, SchemaSite[SchemaT]], ResultT_co]
    site: SchemaSite[SchemaT]
    typed: Callable[[ParserT_contra, SchemaSite[SchemaT]], Iterable[TypedChild[SchemaT]]] | None


@final
@record
class DispatchTable(Generic[ParserT_contra, SchemaT, ResultT_co]):
    """The ordered entries of a position and the parse method of the sites none of its rows applies to."""

    entries: tuple[Step[ParserT_contra, SchemaT] | Row[ParserT_contra, SchemaT, ResultT_co], ...]
    default: Default[ParserT_contra, SchemaT, ResultT_co]
    typed: Callable[[ParserT_contra, SchemaSite[SchemaT]], Iterable[TypedChild[SchemaT]]] | None = None
    """What a site types where its row declares nothing of its own; ``None``: nothing is declared."""

    def choose(
        self, parser: ParserT_contra, site: SchemaSite[SchemaT], *, upto: int | None = None
    ) -> Choice[ParserT_contra, SchemaT, ResultT_co]:
        """Return the first row that applies to the site after the steps before it, else the default.

        ``upto`` reads only the entries before that index; the default answers where none of them applies.
        """
        steps: list[str] = []
        for entry in self.entries[:upto]:
            if not entry.applies(parser, site):
                continue
            if isinstance(entry, Row):
                return Choice((*steps, entry.name), entry.build, site, entry.typed or self.typed)
            site = entry.rewrite(parser, site)
            steps.append(entry.name)
        return Choice((*steps, self.default.name), self.default.build, site, self.typed)

    def typed_children(
        self, parser: ParserT_contra, site: SchemaSite[SchemaT], *, as_parsed: SchemaSite[SchemaT] | None = None
    ) -> Iterable[TypedChild[SchemaT]]:
        """Return what the row chosen for the site types there: its own declaration, else the table's.

        ``as_parsed`` is the site as its position reads it before the table chooses (a ``$ref`` merged with its
        siblings) where the caller reads another. The entries after the last row with a declaration of its own answer
        with the table's, so they are not read.
        """
        last = max(
            (index for index, entry in enumerate(self.entries) if isinstance(entry, Row) and entry.typed is not None),
            default=-1,
        )
        typed = self.choose(parser, site if as_parsed is None else as_parsed, upto=last + 1).typed
        return () if typed is None else typed(parser, site)
