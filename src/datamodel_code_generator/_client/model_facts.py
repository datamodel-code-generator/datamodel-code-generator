"""Read the generated models' fields and types from an accepted batch, as the helper planners walk them.

A helper reads a body through the fields its model declares, by attribute or key, so its planner needs what the model
backend gives each field: its name, its type, and whether the constructor requires it. These come from the batch's
symbols and field bindings alone, never from a codec.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import cached_property
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from datamodel_code_generator._openapi_codec_plan import artifact_module
from datamodel_code_generator._target_contract import (
    AnnotatedType,
    BuiltinType,
    ConstructorType,
    GeneratedEnumMember,
    GeneratedSymbolType,
    GenericType,
    ImportedType,
    KnownBackendValue,
    LiteralScalar,
    LiteralType,
    NoneType,
    UnionType,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator._target_contract import (
        FieldUseBinding,
        FinalModelSymbol,
        FinalPythonType,
        GeneratedTypeContractBatch,
        ModelFieldFacts,
        SymbolId,
    )

__all__ = ("ALIASES", "WRAPPERS", "ItemStep", "JSONTypes", "ModelFacts", "ModelField", "StepKind")

StepKind: TypeAlias = Literal["attr", "key", "get", "root"]
JSONTypes: TypeAlias = frozenset[str] | None
_NULL: Final = frozenset({"null"})
_SCALARS: Final = MappingProxyType({
    "none": "null",
    "bool": "boolean",
    "int": "integer",
    "float": "number",
    "decimal": "number",
    "str": "string",
    "bytes": "string",
})
_NAMED: Final = MappingProxyType({
    "str": "string",
    "bytes": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "dict": "object",
    "list": "array",
    "tuple": "array",
    "set": "array",
    "frozenset": "array",
    "types.NoneType": "null",
    "datetime.datetime": "string",
    "datetime.date": "string",
    "datetime.time": "string",
    "datetime.timedelta": "string",
    "uuid.UUID": "string",
    "decimal.Decimal": "number",
    "pydantic.conint": "integer",
    "pydantic.confloat": "number",
    "pydantic.condecimal": "number",
    "pydantic.constr": "string",
    "pydantic.conbytes": "string",
})
_MAPPINGS: Final = frozenset({"dict", "typing.Mapping", "collections.abc.Mapping", "typing.Dict"})
_EXTRAS: Final = "__pydantic_extra__"
_FALSE: Final = KnownBackendValue(LiteralScalar(kind="bool", value=False))
_MODELS: Final = frozenset({"model", "root"})
WRAPPERS: Final = frozenset({"alias", "root"})
ALIASES: Final = frozenset({"alias"})


@dataclass(frozen=True, slots=True)
class ItemStep:
    """One step of an items accessor: an attribute, a required or optional TypedDict key, or a root model's root.

    `none` says the value it reads may be None, and `unset` that it may be msgspec's UNSET, as for an optional member
    of a Struct.
    """

    kind: StepKind
    name: str
    none: bool = False
    unset: bool = False


@dataclass(frozen=True, slots=True)
class ModelField:
    """A field of a generated model by its wire name: its attribute, or key, its type, and what its model gives it.

    `required` says the model's constructor needs the field; `member` is the field's binding in its consumer.
    """

    wire_name: str
    name: str
    type: FinalPythonType
    required: bool
    read_only: bool
    write_only: bool
    member: FieldUseBinding


def _partial(symbol: FinalModelSymbol) -> bool:
    """Return whether a TypedDict class declares `total=False`."""
    parameters = () if symbol.facts is None else symbol.facts.parameters
    return next((item.value for item in parameters if item.name == "total" and item.present), None) == _FALSE


def _required(consumer: FinalModelSymbol, owner: FinalModelSymbol, facts: ModelFieldFacts) -> bool:
    """Return whether a model's constructor requires a field, by the rules of the consumer's backend.

    Pydantic requires a required field without a default, a dataclass or Struct an initialized field without an emitted
    default, and a TypedDict a key its qualifiers or its declaring class's totality require.
    """
    backend = facts.backend
    match consumer.backend:
        case "pydantic" | "pydantic_dataclass":
            required = facts.required and backend.emitted.emitted_default_kind == "absent"
        case "typeddict":
            qualifiers = backend.emitted.qualifiers
            required = "Required" in qualifiers or (not _partial(owner) and "NotRequired" not in qualifiers)
        case _:
            required = backend.constructor_init != _FALSE and backend.emitted.emitted_default_kind == "absent"
    return required


class ModelFacts:
    """The generated models of a batch: their symbols, their fields by consumer, and the types they stand for."""

    def __init__(self, batch: GeneratedTypeContractBatch) -> None:
        """Index the batch's symbols by identity and its field bindings by consuming symbol."""
        self.symbols = {symbol.id: symbol for symbol in batch.symbols}
        self.members: dict[SymbolId, list[FieldUseBinding]] = {}
        for member in batch.fields:
            self.members.setdefault(member.consumer, []).append(member)
        self.planned: dict[SymbolId, tuple[ModelField, ...]] = {}

    @cached_property
    def imports(self) -> dict[int, str]:
        """Return where each emitted symbol is imported from, as `module:Name` by symbol identity."""
        return {
            symbol.id: f"{artifact_module(symbol.artifact)}:{symbol.name}"
            for symbol in self.symbols.values()
            if symbol.artifact is not None
        }

    def symbol(self, value: FinalPythonType) -> FinalModelSymbol | None:
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
                required=_required(consumer, owner, facts),
                read_only=facts.read_only,
                write_only=facts.write_only,
                member=member,
            )

    def root(self, symbol: SymbolId) -> FinalPythonType | None:
        """Return the type an alias or root symbol stands for, or None when the batch binds none."""
        return next(
            (facts.type for member in self.members.get(symbol, ()) if (facts := member.model_facts) is not None), None
        )

    def argument(
        self, value: FinalPythonType, kinds: frozenset[str] = WRAPPERS, seen: frozenset[SymbolId] = frozenset()
    ) -> FinalPythonType:
        """Return the type an argument of a model type takes: each symbol of the kinds by the type it stands for."""
        match value:
            case GeneratedSymbolType() if (
                value.symbol not in seen
                and self.symbols[value.symbol].kind in kinds
                and (root := self.root(value.symbol)) is not None
            ):
                return self.argument(root, kinds, seen | {value.symbol})
            case GenericType():
                return replace(value, arguments=tuple(self.argument(item, kinds, seen) for item in value.arguments))
            case UnionType():
                return replace(value, members=tuple(self.argument(item, kinds, seen) for item in value.members))
            case _:
                pass
        return value

    def plain(self, value: FinalPythonType) -> FinalPythonType:
        """Return the type an alias stands for, through aliases of aliases, or any other type itself."""
        while (
            isinstance(value, GeneratedSymbolType)
            and self.symbols[value.symbol].kind == "alias"
            and (aliased := self.root(value.symbol)) is not None
        ):
            value = aliased
        return value

    def unwrapped(self, value: FinalPythonType, steps: list[ItemStep]) -> FinalPythonType:
        """Return the type a root model or a union of one type and None stands for, adding each root step taken."""
        while True:
            value = self.plain(value)
            if isinstance(value, UnionType) and len(present := _present(value)) == 1:
                value = present[0]
            elif (
                (symbol := self.symbol(value)) is not None
                and symbol.kind == "root"
                and (root := self.root(symbol.id)) is not None
            ):
                steps.append(ItemStep("root", "root"))
                value = root
            else:
                return value

    def model(self, value: FinalPythonType) -> FinalModelSymbol | None:
        """Return the object model a body stands for, alone or with null, or None when it is anything else.

        A root model whose root is such a model, as a Pydantic body of an object or null is, stands for that model.
        """
        value = self.plain(value)
        if (
            (symbol := self.symbol(value)) is not None
            and symbol.kind == "root"
            and (root := self.root(symbol.id)) is not None
        ):
            value = self.plain(root)
        if isinstance(value, UnionType) and len(present := _present(value)) == 1:
            value = self.plain(present[0])
        return symbol if (symbol := self.symbol(value)) is not None and symbol.kind == "model" else None

    def json_types(self, value: FinalPythonType, seen: frozenset[SymbolId] = frozenset()) -> JSONTypes:
        """Return the JSON types the values of a type are, or None when they can be any value.

        An enum's values give its types, an alias or root model those of the type it stands for, and an object model
        is an object.
        """
        value = value.base if isinstance(value, AnnotatedType) else value
        name = _name(value.callable if isinstance(value, ConstructorType) else value)
        named = _NAMED.get(name or "")
        if named is None and isinstance(value, ImportedType | ConstructorType) and name != "typing.Any":
            named = "string"
        types: JSONTypes = None if named is None else frozenset({named})
        match value:
            case UnionType():
                found = [self.json_types(member, seen) for member in value.members]
                types = None if None in found else frozenset().union(*(item for item in found if item is not None))
            case LiteralType():
                types = frozenset().union(
                    *(
                        self.json_types(GeneratedSymbolType(item.symbol), seen) or ()
                        if isinstance(item, GeneratedEnumMember)
                        else {_SCALARS[item.kind]}
                        for item in value.values
                    )
                )
            case GeneratedSymbolType() if (symbol := self.symbols[value.symbol]).kind == "enum":
                types = frozenset(symbol.value_types)
            case GeneratedSymbolType() if (
                self.symbols[value.symbol].kind in WRAPPERS
                and value.symbol not in seen
                and (root := self.root(value.symbol)) is not None
            ):
                types = self.json_types(root, seen | {value.symbol})
            case GeneratedSymbolType() if self.symbols[value.symbol].kind == "model":
                types = frozenset({"object"})
            case GenericType():
                types = frozenset({"object" if _name(value.base) in _MAPPINGS else "array"})
            case NoneType():
                types = _NULL
            case _:
                pass
        return types

    def field_types(self, field: ModelFieldFacts) -> JSONTypes:
        """Return the JSON types of a field's wire values: those of its type, null only when its schema admits it."""
        present = self.json_types(_without_none(field.type))
        return present if present is None or not (field.nullable or field.type_has_null) else present | _NULL

    def nullable(self, value: FinalPythonType) -> bool:
        """Return whether a type admits None, through its aliases."""
        value = self.plain(value)
        return isinstance(value, UnionType) and len(_present(value)) != len(value.members)


def _name(value: FinalPythonType) -> str | None:
    """Return the dotted name of a builtin or imported type, or None for any other type."""
    if isinstance(value, BuiltinType):
        return value.name
    return f"{value.import_.from_}.{value.import_.import_}" if isinstance(value, ImportedType) else None


def _without_none(value: FinalPythonType) -> FinalPythonType:
    """Return a type without its None member, or the type itself when it is no union."""
    if not isinstance(value, UnionType):
        return value
    present = _present(value)
    return present[0] if len(present) == 1 else replace(value, members=tuple(present))


def _present(value: UnionType) -> list[FinalPythonType]:
    return [member for member in value.members if not isinstance(member, NoneType)]
