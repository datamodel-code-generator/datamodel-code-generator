"""What the client and server targets read from a model type's frozen view: its None, its kinds.

The binder writes into each view the None the model renders (``model/_decisions/nullability.py``), as a `NoneType`
member; these pure functions are the one reading of it, so no target decides by its own walk whether a type takes
None. The JSON kinds of a type's values, the lexical kind of a text leaf, the container slot of a generic class and the
kind of a Pydantic scalar type are read here too, so no target keeps a kind table of its own.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._container_kinds import BUILTIN_CONTAINERS, CONTAINER_CLASSES, ITEM_CONTAINERS, MAPPINGS
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
    wrapped_facts,
    wrapped_type,
)
from datamodel_code_generator.model._decisions.nullability import imported_name_null, written_annotation_null

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from datamodel_code_generator._container_kinds import ContainerKind, ContainerSlot
    from datamodel_code_generator._runtime.model_codecs.media import LexicalKind
    from datamodel_code_generator._target_contract import (
        FieldUseBinding,
        FinalModelSymbol,
        LeafStep,
        LiteralScalar,
        SymbolId,
        TypeView,
    )

__all__ = [
    "ANY_TYPES",
    "CONSTRAINED_SCALARS",
    "admits_none",
    "container_slot",
    "is_none",
    "json_kinds",
    "lexical_kind",
    "present",
    "stepped",
    "takes_null",
    "type_identity",
    "unwrapped",
    "without_none",
]

CONSTRAINED_SCALARS: Final[Mapping[tuple[str, str], LexicalKind]] = MappingProxyType({
    ("pydantic", "StrictBool"): "boolean",
    ("pydantic", "NegativeFloat"): "number",
    ("pydantic", "NonNegativeFloat"): "number",
    ("pydantic", "NonPositiveFloat"): "number",
    ("pydantic", "PositiveFloat"): "number",
    ("pydantic", "StrictFloat"): "number",
    ("pydantic", "NegativeInt"): "integer",
    ("pydantic", "NonNegativeInt"): "integer",
    ("pydantic", "NonPositiveInt"): "integer",
    ("pydantic", "PositiveInt"): "integer",
    ("pydantic", "StrictInt"): "integer",
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
_NULL: Final = frozenset({"null"})
_SCALAR_KINDS: Final = MappingProxyType({
    "none": "null",
    "bool": "boolean",
    "int": "integer",
    "float": "number",
    "decimal": "number",
    "str": "string",
    "bytes": "string",
})
_JSON_KINDS: Final[Mapping[tuple[str | None, str], str]] = MappingProxyType({
    (None, "str"): "string",
    (None, "bytes"): "string",
    (None, "int"): "integer",
    (None, "float"): "number",
    (None, "bool"): "boolean",
    (None, "dict"): "object",
    (None, "list"): "array",
    (None, "tuple"): "array",
    (None, "set"): "array",
    (None, "frozenset"): "array",
    ("types", "NoneType"): "null",
    ("datetime", "datetime"): "string",
    ("datetime", "date"): "string",
    ("datetime", "time"): "string",
    ("datetime", "timedelta"): "string",
    ("uuid", "UUID"): "string",
    ("decimal", "Decimal"): "number",
    **CONSTRAINED_SCALARS,
})
_STEPS: Final[Mapping[LeafStep, tuple[int, ContainerKind]]] = MappingProxyType({
    "items": (1, ITEM_CONTAINERS),
    "values": (2, MAPPINGS),
})
ANY_TYPES: Final = frozenset({("typing", "Any"), ("typing_extensions", "Any")})
"""The module and name of each type that takes any value: the Any of the typing module and of typing_extensions."""
_SERIALIZE_AS_ANY: Final = ("pydantic", "SerializeAsAny")


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


def without_none(value: TypeView) -> TypeView:
    """Return a union without its None: its one other member, or the union of the others; any other type itself.

    The union of the others keeps the discriminator its schema declares and the one its annotation validates by.
    """
    if not isinstance(value, UnionType) or not admits_none(value):
        return value
    members = present(value)
    return members[0] if len(members) == 1 else replace(value, members=members, hint=None)


def takes_null(
    value: TypeView | None,
    symbols: Mapping[SymbolId, FinalModelSymbol],
    members: Mapping[SymbolId, Sequence[FieldUseBinding]],
    seen: frozenset[SymbolId] = frozenset(),
) -> bool:
    """Return whether a type validates JSON null, as the generated models annotate it, which both targets send by.

    Any value does without a type, as Any, or as object; so does None, an enum with a null value, a union with such a
    member, a root model or alias whose annotation accepts None or whose type does, and an annotation the document
    writes that accepts None (``written_annotation_null``).
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
            null = any(takes_null(member, symbols, members, seen) for member in value.members)
        case GeneratedSymbolType() if (declared := symbols[value.symbol]).kind == "enum":
            null = "null" in declared.value_types
        case GeneratedSymbolType() if (
            value.symbol not in seen
            and symbols[value.symbol].kind in WRAPPER_KINDS
            and (facts := wrapped_facts(members, value.symbol)) is not None
        ):
            null = facts.annotation_null or takes_null(facts.type, symbols, members, seen | {value.symbol})
        case _:
            pass
    return null


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
    """Return the container slot whose generic class a type is, or None for a fixed tuple or any other type."""
    return CONTAINER_CLASSES.get(type_identity(value.base)) if isinstance(value, GenericType) else None


def _named(value: TypeView) -> tuple[str | None, str] | None:
    """Return the module and name of a builtin or imported type, None for any other type or a module import."""
    named = isinstance(value, BuiltinType) or (isinstance(value, ImportedType) and bool(value.import_.from_))
    return type_identity(value) if named else None


def _serialized(value: TypeView | None) -> TypeView | None:
    """Return the type a ``SerializeAsAny`` wrapper marks for serialization, or None for any other type."""
    return (
        value.arguments[0]
        if isinstance(value, GenericType) and _named(value.base) == _SERIALIZE_AS_ANY and len(value.arguments) == 1
        else None
    )


def unwrapped(
    value: TypeView | None,
    symbols: Mapping[SymbolId, FinalModelSymbol],
    members: Mapping[SymbolId, Sequence[FieldUseBinding]],
) -> TypeView | None:
    """Return the type a type stands for, through aliases, root models, SerializeAsAny and a union with None alone."""
    seen: set[SymbolId] = set()
    while True:
        if isinstance(value, UnionType) and len(found := present(value)) == 1:
            value = found[0]
        elif (inner := _serialized(value)) is not None:
            value = inner
        elif (
            isinstance(value, GeneratedSymbolType)
            and value.symbol not in seen
            and symbols[value.symbol].kind in WRAPPER_KINDS
        ):
            seen.add(value.symbol)
            value = wrapped_type(members, value.symbol)
        else:
            return value


def stepped(
    value: TypeView | None,
    steps: Sequence[LeafStep],
    symbols: Mapping[SymbolId, FinalModelSymbol],
    members: Mapping[SymbolId, Sequence[FieldUseBinding]],
) -> TypeView | None:
    """Return the type the steps from a type lead to: a list's items, a mapping's values, None where a step finds none.

    Each step reads the last argument of a container type, through aliases, root models and a union with None alone.
    """
    for step in steps:
        count, kind = _STEPS[step]
        value = unwrapped(value, symbols, members)
        value = (
            value.arguments[-1]
            if isinstance(value, GenericType) and len(value.arguments) == count and container_slot(value) in kind.slots
            else None
        )
    return value


def json_kinds(
    value: TypeView,
    symbols: Mapping[SymbolId, FinalModelSymbol],
    members: Mapping[SymbolId, Sequence[FieldUseBinding]],
    seen: frozenset[SymbolId] = frozenset(),
) -> frozenset[str] | None:
    """Return the JSON kinds the values of a type are, or None when they can be any value.

    An enum's values give its kinds, an alias or root model those of the type it stands for, and an object model is an
    object.
    """
    leaf = (value.callable if value.base is None else value.base) if isinstance(value, ConstructorType) else value
    name = _named(leaf)
    named = None if name is None else _JSON_KINDS.get(name)
    if named is None and isinstance(value, ImportedType | ConstructorType) and name not in ANY_TYPES:
        named = "string"
    kinds = None if named is None else frozenset({named})
    match value:
        case UnionType():
            found = [json_kinds(member, symbols, members, seen) for member in value.members]
            kinds = None if None in found else frozenset[str]().union(*(item for item in found if item is not None))
        case LiteralType():
            kinds = frozenset[str]().union(
                *(
                    json_kinds(GeneratedSymbolType(item.symbol), symbols, members, seen) or ()
                    if isinstance(item, GeneratedEnumMember)
                    else {_SCALAR_KINDS[item.kind]}
                    for item in value.values
                )
            )
        case GeneratedSymbolType() if (symbol := symbols[value.symbol]).kind == "enum":
            kinds = frozenset(symbol.value_types)
        case GeneratedSymbolType() if (
            symbols[value.symbol].kind in WRAPPER_KINDS
            and value.symbol not in seen
            and (root := wrapped_type(members, value.symbol)) is not None
        ):
            kinds = json_kinds(root, symbols, members, seen | {value.symbol})
        case GeneratedSymbolType() if symbols[value.symbol].kind == "model":
            kinds = frozenset({"object"})
        case GenericType() if (inner := _serialized(value)) is not None:
            kinds = json_kinds(inner, symbols, members, seen)
        case GenericType():
            kinds = frozenset({"object" if container_slot(value) in MAPPINGS.slots else "array"})
        case NoneType():
            kinds = _NULL
        case _:
            pass
    return kinds


def lexical_kind(
    value: TypeView | None,
    symbols: Mapping[SymbolId, FinalModelSymbol],
    members: Mapping[SymbolId, Sequence[FieldUseBinding]],
) -> LexicalKind | None:
    """Return the lexical kind of a text leaf's type, or None when the leaf has none.

    An int, float or bool leaf has its own kind: as the builtin, a constrained or strict form of it, or the member
    type of an enum or literal. The text of any other scalar leaf is the model's to read, and so is that of a union
    with such a leaf. A model, a container, null alone, an enum or literal of several kinds, and a union of several
    kinds none of which reads text as it is, have no kind.
    """
    return _one_kind(set(_leaves(value, symbols, members, frozenset())), mixed=True)


def _leaves(
    value: TypeView | None,
    symbols: Mapping[SymbolId, FinalModelSymbol],
    members: Mapping[SymbolId, Sequence[FieldUseBinding]],
    seen: frozenset[SymbolId],
) -> Iterator[LexicalKind | None]:
    match value:
        case NoneType():
            pass
        case ConstructorType():
            yield from _leaves(value.callable if value.base is None else value.base, symbols, members, seen)
        case UnionType():
            for member in value.members:
                yield from _leaves(member, symbols, members, seen)
        case LiteralType():
            yield _one_kind(
                {
                    kind
                    for item in value.values
                    for kind in (
                        _leaves(GeneratedSymbolType(item.symbol), symbols, members, seen)
                        if isinstance(item, GeneratedEnumMember)
                        else _literal_kinds(item)
                    )
                },
                mixed=False,
            )
        case GeneratedSymbolType():
            yield from _symbol(symbols[value.symbol], symbols, members, seen)
        case BuiltinType() | ImportedType():
            yield _LEXICAL_KINDS.get(type_identity(value), "string")
        case None | GenericType():
            yield None
        case _:
            yield "string"


def _symbol(
    symbol: FinalModelSymbol,
    symbols: Mapping[SymbolId, FinalModelSymbol],
    members: Mapping[SymbolId, Sequence[FieldUseBinding]],
    seen: frozenset[SymbolId],
) -> Iterator[LexicalKind | None]:
    if symbol.kind == "enum":
        yield _one_kind({kind for item in symbol.values for kind in _literal_kinds(item)}, mixed=False)
    elif symbol.kind in WRAPPER_KINDS and symbol.id not in seen:
        yield from _leaves(wrapped_type(members, symbol.id), symbols, members, seen | {symbol.id})
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
