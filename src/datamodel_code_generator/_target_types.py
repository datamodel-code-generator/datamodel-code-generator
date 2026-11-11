"""What the client and server targets read from a model type's frozen view: its None, its kind.

The binder writes into each view the None the model renders (``model/_decisions/nullability.py``), as a `NoneType`
member; these pure functions and the readers of ``TypeTables`` are the one reading of it, so no target decides by its
own walk whether a type takes None. The lexical kind of a text leaf, the container slot of a generic class and the
kind of a Pydantic scalar type are read here too, so no target keeps a kind table of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._container_kinds import BUILTIN_CONTAINERS, ITEM_CONTAINERS, MAPPINGS
from datamodel_code_generator._target_contract import (
    WRAPPER_KINDS,
    BoundType,
    BuiltinType,
    ConstructorType,
    GeneratedEnumMember,
    GeneratedSymbolType,
    GenericType,
    ImportedType,
    LiteralType,
    NoneType,
    UnionType,
    members_by_consumer,
    symbols_by_id,
    wrapped_facts,
    wrapped_type,
)
from datamodel_code_generator.model._decisions.nullability import imported_name_null, written_annotation_null
from datamodel_code_generator.model._decisions.tag_value import PYDANTIC_SCALAR_KINDS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator._container_kinds import ContainerKind, ContainerSlot
    from datamodel_code_generator._runtime.model_codecs.media import LexicalKind
    from datamodel_code_generator._target_contract import (
        FieldUseBinding,
        FinalModelSymbol,
        GeneratedTypeContractBatch,
        LeafStep,
        LiteralScalar,
        SymbolId,
        TypeView,
    )

__all__ = [
    "CONSTRAINED_SCALARS",
    "TypeTables",
    "admits_none",
    "container_slot",
    "is_none",
    "present",
    "sole_present",
    "type_identity",
    "without_none",
]

CONSTRAINED_SCALARS: Final[Mapping[tuple[str, str], LexicalKind]] = MappingProxyType({
    ("pydantic", name): kind for name, kind in PYDANTIC_SCALAR_KINDS.items()
})
"""The kind of each strict or constrained Pydantic scalar type, by its module and name: a bool's, a float's or an
int's, as its text and its JSON value both read."""
_LITERAL_KINDS: Final[dict[str, LexicalKind]] = {"bool": "boolean", "float": "number", "int": "integer"}
_LEXICAL_KINDS: Final[dict[tuple[str | None, str], LexicalKind | None]] = {
    (None, "bool"): "boolean",
    (None, "float"): "number",
    (None, "int"): "integer",
    **dict.fromkeys(((None, name) for name in BUILTIN_CONTAINERS), None),
    **CONSTRAINED_SCALARS,
}
_INTEGER_NUMBER: Final = frozenset({"integer", "number"})
_ANY: Final = frozenset({("typing", "Any"), ("typing_extensions", "Any")})
_ARGUMENTS: Final[dict[LeafStep, tuple[int, ContainerKind]]] = {"items": (1, ITEM_CONTAINERS), "values": (2, MAPPINGS)}


def _wraps(symbol: FinalModelSymbol) -> bool:
    """Return whether a symbol only stands for another type, as an alias or a root model does."""
    return symbol.kind in WRAPPER_KINDS


def is_none(value: TypeView) -> bool:
    """Return whether a type is None itself."""
    return isinstance(value, NoneType)


def _members(value: TypeView) -> tuple[TypeView, ...]:
    return value.members if isinstance(value, UnionType) else (value,)


def admits_none(value: TypeView | None) -> bool:
    """Return whether a type's annotation includes None: it is None or a union with a None member."""
    return value is not None and any(map(is_none, _members(value)))


def present(value: TypeView) -> tuple[TypeView, ...]:
    """Return the members of a union besides None, or the type itself when it is neither a union nor None."""
    return tuple(member for member in _members(value) if not is_none(member))


def sole_present(value: TypeView | None) -> TypeView | None:
    """Return the one member of a union besides None, or None for any other type and a union with several others."""
    return members[0] if isinstance(value, UnionType) and len(members := present(value)) == 1 else None


def without_none(value: TypeView) -> TypeView:
    """Return a union without its None: its one other member, or the union of the others; any other type itself.

    The union of the others keeps the discriminator its schema declares and the one its annotation validates by.
    """
    if not isinstance(value, UnionType) or not admits_none(value):
        return value
    members = present(value)
    return members[0] if len(members) == 1 else replace(value, members=members, hint=None)


def type_identity(value: TypeView) -> tuple[str | None, str]:
    """Return the module and name of a builtin or imported type, or no name for any other type."""
    return (
        (None, value.name)
        if isinstance(value, BuiltinType)
        else (value.import_.from_, value.import_.import_)
        if isinstance(value, ImportedType)
        else (None, "")
    )


def container_slot(value: TypeView) -> ContainerSlot | None:
    """Return the container slot whose generic class a type is, or None for a fixed tuple or any other type.

    The slot is the one the container's kind decided, wherever an import override imports its class from.
    """
    return value.slot if isinstance(value, GenericType) else None


@dataclass(frozen=True, slots=True)
class TypeTables:
    """A batch's symbols by identity and its field bindings by consuming symbol, and the readers of types through them.

    A planner builds it once per batch (``of``) and the binder once over the tables it fills, so no reader indexes a
    batch of its own.
    """

    symbols: Mapping[SymbolId, FinalModelSymbol]
    members: Mapping[SymbolId, Sequence[FieldUseBinding]]

    @classmethod
    def of(cls, batch: GeneratedTypeContractBatch) -> TypeTables:
        """Index a batch's symbols by identity and its field bindings by consuming symbol."""
        return cls(symbols_by_id(batch), members_by_consumer(batch))

    def takes_null(self, value: TypeView | None, seen: frozenset[SymbolId] = frozenset()) -> bool:
        """Return whether a type validates JSON null, as the generated models annotate it, which both targets send by.

        Any value does without a type, as Any, or as object; so does None, an enum with a null value, a union with such
        a member, a root model or alias whose annotation accepts None or whose type does, and an annotation the
        document writes that accepts None (``written_annotation_null``).
        """
        null = False
        match value:
            case None | NoneType():
                null = True
            case BuiltinType():
                null = value.name == "object"
            case ImportedType():
                null = imported_name_null(module=value.import_.from_, name=value.import_.import_)
            case BoundType():
                null = written_annotation_null(value.binding.expression)
            case UnionType():
                null = any(self.takes_null(member, seen) for member in value.members)
            case GeneratedSymbolType() if (declared := self.symbols[value.symbol]).kind == "enum":
                null = "null" in declared.value_types
            case GeneratedSymbolType() if (
                value.symbol not in seen
                and self.symbols[value.symbol].kind in WRAPPER_KINDS
                and (facts := wrapped_facts(self.members, value.symbol)) is not None
            ):
                null = facts.annotation_null or self.takes_null(facts.type, seen | {value.symbol})
            case _:
                pass
        return null

    def wrapper_chain(
        self,
        value: TypeView | None,
        *,
        through: Callable[[FinalModelSymbol], bool] = _wraps,
        nullable: bool = True,
    ) -> tuple[TypeView | None, list[SymbolId]]:
        """Return the type a type stands for, through aliases, root models and a union with None alone, and the symbols.

        The symbols are the aliases and root models it steps through, in order, then the symbol it stands for; an alias
        or root model it reaches again ends the walk where it is reached, and one the batch binds no type to ends it at
        None. ``through`` says which symbols it steps through, aliases and root models unless given, and without
        ``nullable`` a union with None ends it.
        """
        named: list[SymbolId] = []
        while True:
            if nullable and (member := sole_present(value)) is not None:
                value = member
            elif isinstance(value, GeneratedSymbolType) and value.symbol not in named:
                named.append(value.symbol)
                if not through(self.symbols[value.symbol]):
                    return value, named
                value = wrapped_type(self.members, value.symbol)
            else:
                return value, named

    def unwrapped(self, value: TypeView | None) -> TypeView | None:
        """Return the type a type stands for, through aliases, root models and a union with None alone."""
        return self.wrapper_chain(value)[0]

    def contained(self, value: TypeView | None, step: LeafStep) -> TypeView | None:
        """Return the items type of the item container, or the values type of the mapping, a type stands for.

        The type stands for it through aliases, root models and a union with None alone (``unwrapped``).
        """
        count, kind = _ARGUMENTS[step]
        value = self.unwrapped(value)
        return (
            value.arguments[-1]
            if isinstance(value, GenericType) and len(value.arguments) == count and container_slot(value) in kind.slots
            else None
        )

    def kept_values(self, members: Iterable[FieldUseBinding]) -> Iterator[tuple[FieldUseBinding, TypeView]]:
        """Yield each member that keeps a model's other properties, with the type of their values, in order.

        It is a field of the model's other properties whose type stands for a mapping (``contained``).
        """
        for member in members:
            if (
                member.member_kind == "additional_properties"
                and (facts := member.model_facts) is not None
                and (found := self.contained(facts.type, "values")) is not None
            ):
                yield member, found

    def untyped(self, value: TypeView | None) -> bool:
        """Return whether a type takes any value, as the type of an untyped schema does, through ``unwrapped``.

        A form-data part carries such a value as JSON, as it does one without one kind, while its kind is a string's.
        """
        return (found := self.unwrapped(value)) is not None and type_identity(found) in _ANY

    def member_kind(self, value: TypeView) -> tuple[LexicalKind | None, TypeView, bool]:
        """Return how a form member of a type is written: the kind of its text, each value's type, and its repeats.

        A list type repeats, each of its values one of its items. A value type without one kind, such as a model, a
        mapping, or a union of text and an object, has no text of its own: a form-data part carries such a value as
        JSON, and a URL-encoded form cannot carry it.
        """
        item = self.contained(value, "items")
        leaf = value if item is None else item
        return self.lexical_kind(leaf), leaf, item is not None

    def lexical_kind(self, value: TypeView | None) -> LexicalKind | None:
        """Return the lexical kind of a text leaf's type, or None when the leaf has none.

        An int, float or bool leaf has its own kind: as the builtin, a constrained or strict form of it, or the member
        type of an enum or literal. The text of any other scalar leaf is the model's to read, and so is that of a union
        with such a leaf. A model, a container, null alone, an enum or literal of several kinds, and a union of several
        kinds none of which reads text as it is, have no kind.
        """
        return _one_kind(set(self._leaves(value, frozenset())), mixed=True)

    def _leaves(self, value: TypeView | None, seen: frozenset[SymbolId]) -> Iterator[LexicalKind | None]:
        match value:
            case NoneType():
                pass
            case ConstructorType():
                yield from self._leaves(value.callable if value.base is None else value.base, seen)
            case UnionType():
                for member in value.members:
                    yield from self._leaves(member, seen)
            case LiteralType():
                yield _one_kind(
                    {
                        kind
                        for item in value.values
                        for kind in (
                            self._leaves(GeneratedSymbolType(item.symbol), seen)
                            if isinstance(item, GeneratedEnumMember)
                            else _literal_kinds(item)
                        )
                    },
                    mixed=False,
                )
            case GeneratedSymbolType():
                yield from self._symbol(self.symbols[value.symbol], seen)
            case BuiltinType() | ImportedType():
                yield _LEXICAL_KINDS.get(type_identity(value), "string")
            case None | GenericType():
                yield None
            case _:
                yield "string"

    def _symbol(self, symbol: FinalModelSymbol, seen: frozenset[SymbolId]) -> Iterator[LexicalKind | None]:
        if symbol.kind == "enum":
            yield _one_kind({kind for item in symbol.values for kind in _literal_kinds(item)}, mixed=False)
        elif symbol.kind in WRAPPER_KINDS and symbol.id not in seen:
            yield from self._leaves(wrapped_type(self.members, symbol.id), seen | {symbol.id})
        else:
            yield None


def _literal_kinds(value: LiteralScalar | None) -> Iterator[LexicalKind | None]:
    """Yield the kind of one literal or enum member value: none for null, and None for one that is no scalar."""
    if value is None or value.kind != "none":
        yield None if value is None else _LITERAL_KINDS.get(value.kind, "string")


def _one_kind(kinds: set[LexicalKind | None], *, mixed: bool) -> LexicalKind | None:
    """Return the one kind of a leaf's kinds, taking an int beside a float as a number.

    Several other kinds are the string kind when `mixed` admits them and one of them is the string kind, whose leaf
    reads the text as it is; the members of one enum or literal admit none. A leaf of null alone has no kind.
    """
    if kinds == _INTEGER_NUMBER:
        return "number"
    if len(kinds) == 1:
        return kinds.pop()
    return "string" if mixed and "string" in kinds and None not in kinds else None
