"""What the client and server targets read from a model type's frozen view: its None, its kind.

The binder writes into each view the None the model renders (``model/_decisions/nullability.py``), as a `NoneType`
member; these pure functions and the readers of ``TypeTables`` are the one reading of it, so no target decides by its
own walk whether a type takes None. The kind of JSON value or text a type holds (``TypeTables.json_kinds``), the
container slot of a generic class and the kind of a Pydantic scalar type are read here too, so no target keeps a kind
table of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, cast, get_args

from datamodel_code_generator._all_or_none import all_or_none
from datamodel_code_generator._container_kinds import CONTAINER_CLASSES, ITEM_CONTAINERS, MAPPINGS
from datamodel_code_generator._runtime.model_codecs.media import JSONKind, LexicalKind
from datamodel_code_generator._target_contract import (
    WRAPPER_KINDS,
    BoundType,
    BuiltinType,
    ConstructorType,
    GeneratedEnumMember,
    GeneratedSymbolType,
    GenericType,
    ImportedType,
    LiteralScalar,
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
    from collections.abc import Callable, Container, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator._container_kinds import ContainerKind, ContainerSlot
    from datamodel_code_generator._runtime.model_codecs.parameters import ParameterPlan
    from datamodel_code_generator._target_contract import (
        FieldUseBinding,
        FinalModelSymbol,
        GeneratedTypeContractBatch,
        LeafStep,
        ModelFieldFacts,
        SymbolId,
        TypeView,
    )

__all__ = [
    "CONSTRAINED_SCALARS",
    "NULL_KINDS",
    "JSONKinds",
    "TypeTables",
    "admits_none",
    "container_slot",
    "is_alias",
    "is_none",
    "is_wrapper",
    "opened",
    "present",
    "sole_present",
    "styled_without_none",
    "type_identity",
    "without_none",
]

CONSTRAINED_SCALARS: Final[Mapping[tuple[str, str], LexicalKind]] = MappingProxyType({
    ("pydantic", name): kind for name, kind in PYDANTIC_SCALAR_KINDS.items()
})
"""The kind of each strict or constrained Pydantic scalar type, by its module and name: a bool's, a float's or an
int's, as its text and its JSON value both read."""
JSONVocabulary: TypeAlias = Literal["schema", "codec", "lexical"]
"""The names a reader gives the kinds of a type's values (``TypeTables.json_kinds``): the JSON types of a schema, the
JSON kinds a stdlib codec tells union members apart by, or the lexical kinds of text."""
JSONKinds: TypeAlias = "frozenset[str] | None"
_STRING: Final = frozenset({"string"})
NULL_KINDS: Final = frozenset({"null"})
_INTEGER_NUMBER: Final = frozenset({"integer", "number"})
_SCALAR_KINDS: Final[Mapping[str, str]] = MappingProxyType({
    "none": "null",
    "bool": "boolean",
    "int": "integer",
    "float": "number",
    "decimal": "decimal",
    "str": "string",
    "bytes": "string",
})
"""The kind of a literal or enum value by the kind of its scalar; a Decimal's is its own, each vocabulary names it."""


def _container_kind(slot: str | None) -> str:
    """Return the kind of the values a container holds by its slot: a mapping's are objects, any other's arrays."""
    return "object" if slot in MAPPINGS.slots else "array"


_CONTAINER_CLASSES: Final = {**CONTAINER_CLASSES, (None, "tuple"): "tuple", ("typing", "List"): "list"}
_LEAF_KINDS: Final[Mapping[tuple[str | None, str], str]] = MappingProxyType({
    **{(None, name): _SCALAR_KINDS[name] for name in ("bool", "int", "float", "str", "bytes")},
    ("decimal", "Decimal"): "decimal",
    ("types", "NoneType"): "null",
    (None, "object"): "any",
    ("typing", "Any"): "any",
    **{key: _container_kind(slot) for key, slot in _CONTAINER_CLASSES.items()},
    **CONSTRAINED_SCALARS,
})
"""The kind of each class by its module and name (``type_identity``): another imported class holds strings."""
_IMPORTED_CONTAINERS: Final = frozenset(key for key in _CONTAINER_CLASSES if key[0] is not None)


@dataclass(frozen=True, slots=True)
class _Vocabulary:
    """How one reader names the kinds of a type's values, a row of ``TypeTables.json_kinds``."""

    kinds: frozenset[str]
    """The kinds it names: a leaf of another kind holds strings, and None and a null value hold null where it names
    null, else no kind."""
    renamed: Mapping[str, JSONKinds]
    """The leaf kinds it reads as others, or as None: any value for a schema or codec, no kind for text."""
    written: JSONKinds
    """The kinds of a type that names no class it knows: another builtin, or an annotation the document writes."""
    values: Literal["all", "one", "native"]
    """How an enum or literal takes kinds: those of all its values (an enum its ``value_types``), one kind of them
    (an int beside a float is a number), or the one of the codec (``legacy-codec-enum-kinds``): the kinds of its str
    and int values alone, of a literal only where all its values are of one kind."""
    text: frozenset[tuple[str | None, str]]
    """The classes it reads as strings whatever their kind: the containers imported from a module, an abstract class
    or a ``typing`` alias, that a schema or text names alone (``legacy-imported-container-text``)."""

    classes: Mapping[tuple[str | None, str], JSONKinds] = field(init=False)
    """The kinds of each class ``_LEAF_KINDS`` names, as it names them."""

    def __post_init__(self) -> None:
        """Name the kinds of the classes ``_LEAF_KINDS`` names."""
        classes = {key: _STRING if key in self.text else self.leaf(kind) for key, kind in _LEAF_KINDS.items()}
        object.__setattr__(self, "classes", MappingProxyType(classes))

    def leaf(self, kind: str) -> JSONKinds:
        """Return the kinds a leaf of one kind holds in this vocabulary."""
        return self.renamed[kind] if kind in self.renamed else frozenset({kind}) if kind in self.kinds else _STRING


_VOCABULARIES: Final[Mapping[JSONVocabulary, _Vocabulary]] = MappingProxyType({
    "schema": _Vocabulary(
        frozenset({*_SCALAR_KINDS.values(), "array", "object"}) - {"decimal"},
        {"decimal": frozenset({"number"}), "any": None},
        None,
        "all",
        _IMPORTED_CONTAINERS,
    ),
    "codec": _Vocabulary(
        frozenset(get_args(JSONKind)),
        {"integer": frozenset({"number"}), "decimal": frozenset({"string", "number"}), "any": None},
        _STRING,
        "native",
        frozenset(),
    ),
    "lexical": _Vocabulary(
        frozenset(get_args(LexicalKind)),
        {"array": None, "object": None},
        _STRING,
        "one",
        _IMPORTED_CONTAINERS,
    ),
})
_SERIALIZE_AS_ANY: Final = ("pydantic", "SerializeAsAny")
_ANY: Final = frozenset({("typing", "Any"), ("typing_extensions", "Any")})
_ARGUMENTS: Final[dict[LeafStep, tuple[int, ContainerKind]]] = {"items": (1, ITEM_CONTAINERS), "values": (2, MAPPINGS)}


def is_wrapper(symbol: FinalModelSymbol) -> bool:
    """Return whether a symbol only stands for another type, as an alias or a root model does."""
    return symbol.kind in WRAPPER_KINDS


def is_alias(symbol: FinalModelSymbol) -> bool:
    """Return whether a symbol is an alias of another type, which a root model is not."""
    return symbol.kind == "alias"


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


def _leaves(value: TypeView) -> Iterator[TypeView]:
    if isinstance(value, UnionType):
        for member in value.members:
            yield from _leaves(member)
    else:
        yield value


def opened(value: TypeView) -> TypeView:
    """Return a union with the unions among its members opened and no hint, or any other type itself.

    A root model of a nullable type that a target reads through is a union of that union and None.
    """
    if not isinstance(value, UnionType) or not any(isinstance(member, UnionType) for member in value.members):
        return value
    return replace(value, members=tuple(_leaves(value)), hint=None)


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


def styled_without_none(value: TypeView, plan: ParameterPlan | None) -> TypeView | None:
    """Return the type of a required parameter its plan writes by its style without None, or None where None stays.

    A query string, header, cookie or path has no spelling for None, so such a parameter never receives it; one with a
    content media type, an optional one and a type without None keep their type.
    """
    if plan is None or not plan.required or plan.content_media_type is not None:
        return None
    return without_none(members) if admits_none(members := opened(value)) else None


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
            case GeneratedSymbolType() if (facts := self.wrapped(value, seen)) is not None:
                null = facts.annotation_null or self.takes_null(facts.type, seen | {value.symbol})
            case _:
                pass
        return null

    def wrapper_chain(
        self,
        value: TypeView | None,
        *,
        through: Callable[[FinalModelSymbol], bool] = is_wrapper,
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

    def wrapped(
        self,
        value: TypeView | None,
        seen: Container[SymbolId] = frozenset(),
        through: Callable[[FinalModelSymbol], bool] = is_wrapper,
    ) -> ModelFieldFacts | None:
        """Return the facts of the value an alias or root model stands for, one step, or None for any other type.

        It steps through the symbols ``through`` names, as ``wrapper_chain`` does, but not through one in ``seen`` or
        one the batch binds no type to, which ``wrapper_chain`` ends at None.
        """
        if isinstance(value, GeneratedSymbolType) and value.symbol not in seen and through(self.symbols[value.symbol]):
            return wrapped_facts(self.members, value.symbol)
        return None

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
        return cast("LexicalKind | None", _one_kind(self.json_kinds(value, "lexical"), mixed=True))

    def json_kinds(self, value: TypeView | None, vocabulary: JSONVocabulary) -> JSONKinds:
        """Return the kinds of the JSON values or text a type holds, as a vocabulary names them, or None.

        None is any value in a schema or codec, and no kind for text. A class has the kind ``_LEAF_KINDS`` gives it,
        a container that of its slot and an object model an object's; an enum and a literal take those of their
        values, and an alias, a root model, a union and a Pydantic wrapper those of the types they stand for.
        """
        return self._kinds(value, _VOCABULARIES[vocabulary], frozenset())

    def fixed(self, value: TypeView) -> tuple[object, ...] | None:
        """Return the values a type admits where it admits literal values alone, None aside, else None.

        A literal admits the values its items stand for (``scalar``), and an enum the values of its members, through
        aliases and root models (``wrapper_chain``), and a union they stand for the values of its members, read the
        same way; any other type, such as a scalar or a model, admits values of its own, and so does an alias or root
        model the union's members reach again.
        """
        return self._fixed(value, frozenset())

    def _fixed(self, value: TypeView, seen: frozenset[SymbolId]) -> tuple[object, ...] | None:
        found: list[object] = []
        for present_member in present(value):
            member, named = self.wrapper_chain(present_member)
            if isinstance(member, LiteralType):
                found.extend(None if (item := self.scalar(entry)) is None else item.value for entry in member.values)
            elif isinstance(member, GeneratedSymbolType) and (symbol := self.symbols[member.symbol]).kind == "enum":
                found.extend(None if item is None else item.value for item in symbol.values)
            elif (
                isinstance(member, UnionType)
                and seen.isdisjoint(named)
                and (values := self._fixed(member, seen.union(named))) is not None
            ):
                found.extend(values)
            else:
                return None
        return tuple(found)

    def scalar(self, item: LiteralScalar | GeneratedEnumMember) -> LiteralScalar | None:
        """Return the value a literal's item stands for: its own, or that of the enum member it names (None aside)."""
        if not isinstance(item, GeneratedEnumMember):
            return item
        symbol = self.symbols[item.symbol]
        return next(value for slot, value in zip(symbol.fields, symbol.values, strict=True) if slot.field == item.field)

    def _kinds(self, value: TypeView | None, row: _Vocabulary, seen: frozenset[SymbolId]) -> JSONKinds:  # ruff: ignore[too-many-branches]
        kinds: JSONKinds = row.written
        match value:
            case None:
                kinds = None
            case NoneType():
                kinds = row.kinds & NULL_KINDS
            case UnionType():
                kinds = _joined(self._kinds(member, row, seen) for member in value.members)
            case ConstructorType():
                kinds = self._kinds(value.callable if value.base is None else value.base, row, seen)
            case LiteralType():
                kinds = self._values(value, row, seen)
            case GeneratedSymbolType() if (facts := self.wrapped(value, seen)) is not None:
                kinds = self._kinds(facts.type, row, seen | {value.symbol})
            case GeneratedSymbolType() if (symbol := self.symbols[value.symbol]).kind == "enum":
                kinds = self._values(symbol, row, seen)
            case GeneratedSymbolType():
                kinds = row.leaf("object") if self.symbols[value.symbol].kind == "model" else None
            case GenericType() if type_identity(value.base) == _SERIALIZE_AS_ANY and len(value.arguments) == 1:
                kinds = self._kinds(value.arguments[0], row, seen)
            case GenericType():
                kinds = row.leaf(_container_kind(container_slot(value)))
            case ImportedType():
                kinds = row.classes.get(type_identity(value), _STRING)
            case BuiltinType():
                kinds = row.classes.get(type_identity(value), row.written)
            case _:
                pass
        return kinds

    def _values(self, value: LiteralType | FinalModelSymbol, row: _Vocabulary, seen: frozenset[SymbolId]) -> JSONKinds:
        """Return the kinds of a literal's or an enum's values as ``row.values`` says."""
        literal = isinstance(value, LiteralType)
        if row.values == "all" and not literal:
            return frozenset(value.value_types)
        if row.values == "native":
            natives = {item.kind if isinstance(item, LiteralScalar) else "other" for item in value.values}
            if literal and len(natives) > 1:
                return frozenset()
            return _joined(row.leaf(_SCALAR_KINDS[kind]) for kind in natives & {"str", "int"})
        kinds = _joined(
            self._kinds(GeneratedSymbolType(item.symbol), row, seen)
            if isinstance(item, GeneratedEnumMember)
            else None
            if item is None
            else row.kinds & NULL_KINDS
            if item.kind == "none"
            else row.leaf(_SCALAR_KINDS[item.kind])
            for item in value.values
        )
        if row.values == "all":
            return kinds
        return None if (kind := _one_kind(kinds, mixed=False)) is None else frozenset({kind})


def _joined(kinds: Iterable[JSONKinds]) -> JSONKinds:
    """Return the kinds of several types together, or None when one of them has None."""
    return None if (found := all_or_none(kinds)) is None else frozenset().union(*found)


def _one_kind(kinds: JSONKinds, *, mixed: bool) -> str | None:
    """Return the one lexical kind of a leaf's kinds, taking an int beside a float as a number.

    Several other kinds are the string kind when `mixed` admits them and one of them is the string kind, whose leaf
    reads the text as it is; the members of one enum or literal admit none. A leaf of null alone has no kind.
    """
    if kinds is None:
        return None
    if kinds == _INTEGER_NUMBER:
        return "number"
    if len(kinds) == 1:
        return next(iter(kinds))
    return "string" if mixed and "string" in kinds else None
