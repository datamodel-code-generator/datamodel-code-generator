"""Read the generated models' fields and types from an accepted batch, as the helper planners walk them.

A helper reads a body through the fields its model declares, by attribute or key, so its planner needs what the model
backend gives each field: its name, its type, and whether the constructor requires it. These come from the batch's
symbols and field bindings alone, never from a codec.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import cached_property
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, cast

from datamodel_code_generator._target_contract import (
    BuiltinType,
    GeneratedSymbolType,
    GenericType,
    ImportedType,
    UnionType,
    schema_uses,
    wrapped_type,
)
from datamodel_code_generator._target_types import (
    NULL_KINDS,
    JSONKinds,
    TypeTables,
    is_alias,
    is_wrapper,
    sole_present,
    without_none,
)
from datamodel_code_generator.imports import _import_path

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from datamodel_code_generator._target_contract import (
        Direction,
        FieldUseBinding,
        FinalModelSymbol,
        ModelFieldFacts,
        SourceDocumentId,
        SymbolId,
        TypeUseBinding,
        TypeView,
    )

__all__ = ("ItemStep", "ModelFacts", "ModelField", "StepKind")

StepKind: TypeAlias = Literal["attr", "key", "get", "root"]
_EXTRAS: Final = "__pydantic_extra__"
_MODELS: Final = frozenset({"model", "root"})


@dataclass(frozen=True, slots=True)
class ItemStep:
    """One step of an items accessor: an attribute, a required or optional TypedDict key, or a root model's root.

    `none` says the value it reads may be None, `unset` that it may be msgspec's UNSET, as for an optional member of a
    Struct, and `missing` that it may be Pydantic's MISSING, as for a field that defaults to it.
    """

    kind: StepKind
    name: str
    none: bool = False
    unset: bool = False
    missing: bool = False


@dataclass(frozen=True, slots=True)
class ModelField:
    """A field of a generated model by its wire name: its attribute, or key, its type, and what its model gives it.

    `required` says the model's constructor needs the field, `missing` that it defaults to Pydantic's MISSING and
    `annotation_null` that its annotation accepts None; `explicit` that an `--aliases` entry of its class gave its
    name; `member` is the field's binding in its consumer.
    """

    wire_name: str
    name: str
    type: TypeView
    required: bool
    read_only: bool
    write_only: bool
    member: FieldUseBinding
    missing: bool = False
    explicit: bool = False
    annotation_null: bool = False


def _declared_root(symbol: FinalModelSymbol) -> bool:
    """Return whether a symbol is a root model its backend declares."""
    return symbol.kind == "root" and symbol.facts is not None


class ModelFacts:
    """The generated models of a batch: their symbols, their fields by consumer, and the types they stand for.

    `schemas` holds the value use of each schema occurrence among the batch's type uses (``schema_uses``).
    """

    def __init__(self, tables: TypeTables, uses: Sequence[TypeUseBinding]) -> None:
        """Read the batch's symbols by identity and its field bindings by consuming symbol from its tables."""
        self.tables = tables
        self.symbols = tables.symbols
        self.members = tables.members
        self._uses = uses
        self.planned: dict[SymbolId, tuple[ModelField, ...]] = {}

    @cached_property
    def schemas(self) -> dict[tuple[SourceDocumentId, str, Direction], TypeUseBinding]:
        """Return the value use of each schema occurrence by its location and direction, indexed when first read."""
        return schema_uses(self._uses)

    def symbol(self, value: TypeView) -> FinalModelSymbol | None:
        """Return the model or root model a type names when its backend declares it, or None for any other type."""
        if not isinstance(value, GeneratedSymbolType):
            return None
        symbol = self.symbols[value.symbol]
        return symbol if symbol.kind in _MODELS and symbol.facts is not None else None

    def fields(self, symbol: SymbolId) -> tuple[ModelField, ...]:
        """Return the fields a model declares by wire name, its own and inherited, leaving out tags and extra items."""
        if (found := self.planned.get(symbol)) is None:
            found = self.planned[symbol] = tuple(self._fields(self.symbols[symbol]))
        return found

    def _fields(self, consumer: FinalModelSymbol) -> Iterator[ModelField]:
        for member in self.members.get(consumer.id, ()):
            if (
                (facts := member.model_facts) is None
                or (slot := member.slot) is None
                or slot.name == _EXTRAS
                or member.exclusion == "tag"
                or (wire_name := member.wire_name) is None
            ):
                continue
            owner = self.symbols[slot.symbol]
            functional = (
                consumer.backend == "typeddict" and owner.facts is not None and owner.facts.functional_typeddict
            )
            yield ModelField(
                wire_name=wire_name,
                name=wire_name if functional else slot.name,
                type=facts.type,
                required=facts.constructor_required,
                read_only=facts.read_only,
                write_only=facts.write_only,
                member=member,
                missing=facts.backend.emitted.emitted_default_kind == "pydantic_missing",
                explicit=facts.explicit_name,
                annotation_null=facts.annotation_null,
            )

    def root(self, symbol: SymbolId) -> TypeView | None:
        """Return the type an alias or root symbol stands for, or None when the batch binds none."""
        return wrapped_type(self.members, symbol)

    def argument(
        self,
        value: TypeView,
        through: Callable[[FinalModelSymbol], bool] = is_wrapper,
        seen: frozenset[SymbolId] = frozenset(),
    ) -> TypeView:
        """Return the type an argument of a model type takes: each symbol `through` names by the type it stands for."""
        match value:
            case GeneratedSymbolType() if (facts := self.tables.wrapped(value, seen, through)) is not None:
                return self.argument(facts.type, through, seen | {value.symbol})
            case GenericType():
                return replace(
                    value, arguments=tuple(self.argument(item, through, seen) for item in value.arguments), hint=None
                )
            case UnionType():
                return replace(
                    value, members=tuple(self.argument(item, through, seen) for item in value.members), hint=None
                )
            case _:
                pass
        return value

    def _aliased(self, symbol: FinalModelSymbol) -> bool:
        """Return whether a symbol is an alias the batch binds a type to."""
        return is_alias(symbol) and self.root(symbol.id) is not None

    def _stepped(self, symbol: FinalModelSymbol) -> bool:
        """Return whether a symbol is such an alias, or a root model its backend declares and the batch binds."""
        return (is_alias(symbol) or _declared_root(symbol)) and self.root(symbol.id) is not None

    def plain(self, value: TypeView) -> TypeView:
        """Return the type an alias stands for, through aliases of aliases, or any other type itself."""
        return cast("TypeView", self.tables.wrapper_chain(value, through=self._aliased, nullable=False)[0])

    def unwrapped(self, value: TypeView, steps: list[ItemStep]) -> TypeView:
        """Return the type a root model or a union of one type and None stands for, adding each root step taken."""
        found, named = self.tables.wrapper_chain(value, through=self._stepped)
        steps.extend(
            ItemStep("root", "root")
            for symbol in map(self.symbols.__getitem__, named)
            if symbol.kind == "root" and self._stepped(symbol)
        )
        return cast("TypeView", found)

    def model(self, value: TypeView) -> FinalModelSymbol | None:
        """Return the object model a body stands for, alone or with null, or None when it is anything else.

        A root model whose root is such a model, as a Pydantic body of an object or null is, stands for that model.
        """
        value = self.plain(value)
        if (facts := self.tables.wrapped(value, through=_declared_root)) is not None:
            value = facts.type
        return symbol if (symbol := self.symbol(self.present(value))) is not None and symbol.kind == "model" else None

    def present(self, value: TypeView) -> TypeView:
        """Return a type without None, through its aliases (``plain``)."""
        value = self.plain(value)
        return value if (member := sole_present(value)) is None else self.plain(member)

    def field_types(self, field: ModelFieldFacts) -> JSONKinds:
        """Return the JSON types of a field's wire values: those of its type, null only when its schema admits it.

        A schema admits null by its own nullability, or by a type it refers to that does, such as a nullable model; an
        annotation that only makes an omitted field optional does not (``ModelFieldFacts.wire_null``).
        """
        kinds = self.tables.json_kinds(without_none(field.type), "schema")
        return kinds if kinds is None or not field.wire_null else kinds | NULL_KINDS


def _name(value: TypeView) -> str | None:
    """Return the dotted name of a builtin or imported type, or None for any other type, a module import included."""
    if isinstance(value, BuiltinType):
        return value.name
    return _import_path(value.import_) if isinstance(value, ImportedType) and value.import_.from_ else None
