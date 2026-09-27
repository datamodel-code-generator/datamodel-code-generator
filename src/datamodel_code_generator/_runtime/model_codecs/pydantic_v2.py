"""Model codecs for the Pydantic v2 BaseModel and pydantic dataclass backends."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from decimal import Decimal
from enum import Enum
from itertools import chain
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, TypeVar, overload

from pydantic import AliasChoices, BaseModel, RootModel, SecretBytes, SecretStr, TypeAdapter, ValidationError
from pydantic.dataclasses import is_pydantic_dataclass
from pydantic.errors import PydanticSchemaGenerationError
from pydantic_core import PydanticSerializationError, to_jsonable_python

from .bindings import (
    ArrayNode,
    FieldBinding,
    LeafNode,
    MapNode,
    ModelBinding,
    ModelNode,
    Representation,
    TupleNode,
    TypeNode,
    UnionNode,
    UseBinding,
)
from .codec import (
    EMPTY,
    BuiltinModelCodec,
    Walk,
    at,
    child_value,
    directional_gap,
    directs,
    has_models,
    is_mapping,
    is_sequence,
    is_set,
    item_node,
    json_key,
    json_scalar,
    model_unions,
    selected,
    shape_error,
    sorted_items,
    walks,
)
from .errors import CodecConfigurationError, ModelProjectionError, NativeIssue, NativeValidationError
from .media import encode_json
from .patterns import MatchBudget
from .values import DecodedValue, ModelInput, ModelValue
from .wire import (
    JSONValue,
    PresenceTree,
    WireValue,
    check_array_presence,
    check_object_presence,
    escape_pointer_token,
    freeze_wire,
    snapshot_presence,
    thaw_wire,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic.fields import FieldInfo
    from typing_extensions import TypeIs

    from .schema import SchemaBundle, WireValidator

T = TypeVar("T")

_NO_KEYS: Final = frozenset[str]()
_BACKENDS: Final = frozenset({"pydantic_v2.BaseModel", "pydantic_v2.dataclass"})


def _is_root(value: object) -> TypeIs[RootModel[object]]:
    return isinstance(value, RootModel)


def _aliases(info: FieldInfo) -> frozenset[str]:
    match info.validation_alias:
        case str() as alias:
            return frozenset({alias})
        case AliasChoices(choices=choices):
            return frozenset(choice for choice in choices if isinstance(choice, str))
        case _:
            return frozenset({info.alias} if info.alias else ())


def _validation_keys(info: FieldInfo, name: str) -> frozenset[str]:
    return _aliases(info) or frozenset({name})


def _read_keys(binding: ModelBinding, native: type) -> Mapping[str, frozenset[str]] | None:
    names = {field.native_name for field in binding.fields}
    match binding.native_kind:
        case "root":
            return {} if issubclass(native, RootModel) and not names else None
        case "model" if issubclass(native, BaseModel) and set(native.model_fields) == names:
            return {name: _validation_keys(info, name) for name, info in native.model_fields.items()}
        case "dataclass" if is_pydantic_dataclass(native) and names <= (
            declared := {field.name for field in dataclasses.fields(native)}
        ):
            planned = {field.native_name: frozenset(field.validation_keys) for field in binding.fields}
            return {name: planned.get(name, frozenset({name})) for name in declared}
        case _:
            return None


def _native_type(binding: ModelBinding, models: Mapping[str, type]) -> tuple[type, frozenset[str]]:
    if (
        (native := models.get(binding.symbol)) is not None
        and (keys := _read_keys(binding, native)) is not None
        and all(field.validation_key in keys[field.native_name] for field in binding.fields)
    ):
        return native, frozenset(chain.from_iterable(keys.values()))
    msg = f"The native type of {binding.symbol} does not match its binding"
    raise CodecConfigurationError(msg)


class PydanticModelCodec(BuiltinModelCodec[T]):
    """Validate, construct, snapshot, and encode one bound use of a Pydantic v2 model type."""

    __slots__ = ("_adapter", "_directed", "_inspect", "_keys", "_leaves", "_nested", "_reserved", "_unstored", "_walk")

    @overload
    def __init__(
        self,
        binding: UseBinding,
        native_type: type[T],
        models: Mapping[str, type],
        bundle: Callable[[], SchemaBundle],
        *,
        validator: WireValidator | None = None,
    ) -> None: ...
    @overload
    def __init__(
        self,
        binding: UseBinding,
        native_type: object,
        models: Mapping[str, type],
        bundle: Callable[[], SchemaBundle],
        *,
        validator: WireValidator | None = None,
    ) -> None: ...
    def __init__(
        self,
        binding: UseBinding,
        native_type: object,
        models: Mapping[str, type],
        bundle: Callable[[], SchemaBundle],
        *,
        validator: WireValidator | None = None,
    ) -> None:
        """Check the binding against the native types once, before any value is processed.

        A schema adapter's validator replaces the bundle's builtin validator of the use's schema.
        """
        super().__init__(
            binding,
            bundle,
            validator,
            selects=binding.converter_strategy == "pydantic_type_adapter" and binding.backend in _BACKENDS,
            converter="a Pydantic v2 type adapter",
        )
        natives = {model.symbol: _native_type(model, models) for model in binding.models}
        self._types = {symbol: native for symbol, (native, _) in natives.items()}
        self._reserved = {
            model.symbol: reserved
            for model in binding.models
            if (reserved := natives[model.symbol][1] - {field.wire_name for field in model.fields})
        }
        self._nested = {
            model.symbol: {field.wire_name: field for field in model.fields if has_models(field.type)}
            for model in binding.models
        }
        self._keys = {
            model.symbol: {key: field for field in model.fields for key in (field.native_name, field.validation_key)}
            for model in binding.models
        }
        self._leaves: dict[type, TypeAdapter[object]] = {}
        self._adapter: TypeAdapter[T] = TypeAdapter(native_type)
        self._walk = walks(binding) or bool(self._reserved)
        self._directed = self._walk or directs(binding)
        self._unstored = frozenset(
            model.symbol
            for model in binding.models
            if model.open
            and model.native_kind != "root"
            and (model.extra == "ignore" or (model.extra == "allow" and model.native_kind == "dataclass"))
        )
        self._inspect = any(
            model_unions(node)
            for node in (
                binding.type,
                *(model.root for model in binding.models),
                *(field.type for model in binding.models for field in model.fields),
            )
        ) or bool(self._unstored or self._reserved)

    def _project(self, wire: WireValue, budget: MatchBudget) -> DecodedValue[T]:
        presence = snapshot_presence(wire)
        binding_id = self._binding.binding_id
        walk = Walk(budget)
        keyed = self._keyed(wire, self._binding.type, "", (), walk) if self._walk else wire
        if not walk.issues:
            if walk.native:
                raise NativeValidationError(tuple(walk.native))
            value = self._native(keyed, wire, budget)
            return ModelValue(
                value=value,
                binding_id=binding_id,
                wire=wire,
                presence=presence,
                extras=self._native_extras(wire, value, budget),
            )
        if self._binding.projection_mode == "native":
            msg = f"The native use has an unplanned projection gap at {walk.issues[0].pointer or '/'}"
            raise ModelProjectionError(msg)
        return ModelInput(
            binding_id=binding_id,
            wire=wire,
            presence=presence,
            extras=MappingProxyType(walk.extras),
            issues=tuple(walk.issues),
        )

    def _native(self, keyed: JSONValue | WireValue, wire: WireValue, budget: MatchBudget, *, schema: bool = True) -> T:
        try:
            return self._adapter.validate_json(encode_json(keyed), by_alias=True, by_name=False)
        except ValidationError as error:
            raise self._invalid(error, wire, budget, schema=schema) from None

    def _invalid(
        self, error: ValidationError, wire: WireValue, budget: MatchBudget, *, schema: bool
    ) -> NativeValidationError:
        """Return the native issues of a validation error located in the wire; without schemas, a union ends a path."""
        return NativeValidationError(
            tuple(
                NativeIssue(
                    code=f"native.{item['type']}",
                    pointer=self._native_pointer(tuple(item["loc"]), wire, budget, schema=schema),
                    native_path=tuple(item["loc"]),
                )
                for item in error.errors(include_url=False, include_context=False, include_input=False)
            )
        )

    def _validated(self, value: object) -> object:
        try:
            return self._adapter.validate_python(value)
        except ValidationError as error:
            raise self._invalid(error, None, MatchBudget(), schema=False) from None

    def _converted(self, wire: WireValue, budget: MatchBudget) -> T:
        walk = Walk(budget, converting=True)
        keyed = self._keyed(wire, self._binding.type, "", (), walk) if self._directed else wire
        if walk.native:
            raise NativeValidationError(tuple(walk.native))
        if walk.issues:
            msg = f"The native use has an unplanned projection gap at {walk.issues[0].pointer or '/'}"
            raise ModelProjectionError(msg)
        return self._native(keyed, wire, budget, schema=False)

    def _keyed(
        self, wire: WireValue, node: TypeNode | None, pointer: str, path: tuple[str | int, ...], walk: Walk
    ) -> JSONValue:
        match node:
            case ModelNode(symbol=symbol) if (model := self._models[symbol]).native_kind == "root":
                return self._keyed(wire, model.root, pointer, path, walk)
            case ModelNode(symbol=symbol) if isinstance(wire, Mapping):
                return self._keyed_model(wire, self._models[symbol], pointer, path, walk)
            case UnionNode(members=members) if wire is not None:
                return self._keyed(wire, self._wire_member(wire, members, walk.budget), pointer, path, walk)
            case ArrayNode() | TupleNode() if isinstance(wire, tuple):
                return [
                    self._keyed(entry, item_node(node, index), at(pointer, index), (*path, index), walk)
                    for index, entry in enumerate(wire)
                ]
            case MapNode(value=value) if isinstance(wire, Mapping):
                return {
                    name: self._keyed(entry, value, at(pointer, name), (*path, name), walk)
                    for name, entry in wire.items()
                }
            case _:
                return thaw_wire(wire)

    def _keyed_model(
        self,
        wire: Mapping[str, WireValue],
        model: ModelBinding,
        pointer: str,
        path: tuple[str | int, ...],
        walk: Walk,
    ) -> JSONValue:
        fields = self._wire_fields[model.symbol]
        reserved = self._reserved.get(model.symbol, _NO_KEYS)
        keyed: dict[str, JSONValue] = {}
        for name, entry in wire.items():
            if (field := fields.get(name)) is not None and walk.converting and self._excluded(field):
                walk.native.append(self._carried(at(pointer, name), (*path, field.validation_key)))
            elif field is not None:
                keyed[field.validation_key] = self._keyed(
                    entry, field.type, at(pointer, name), (*path, field.validation_key), walk
                )
            elif name not in reserved:
                if model.symbol in self._unstored:
                    walk.extras[at(pointer, name)] = entry
                keyed[name] = thaw_wire(entry)
            elif model.extra == "forbid":
                walk.native.append(
                    NativeIssue(code="native.extra_forbidden", pointer=at(pointer, name), native_path=(*path, name))
                )
            else:
                walk.extras[at(pointer, name)] = entry
        walk.issues.extend(
            directional_gap(model, field, pointer)
            for field in model.fields
            if field.required and field.wire_name not in wire and self._excluded(field)
        )
        return keyed

    def _native_pointer(
        self, loc: tuple[str | int, ...], wire: WireValue, budget: MatchBudget, *, schema: bool = True
    ) -> str:
        node: TypeNode | None = self._binding.type
        tokens: list[str | int] = []
        for element in loc:
            if (node := self._unwrapped(node)) is None or (not schema and isinstance(node, UnionNode)):
                break
            node, token = self._pointer_step(node, element, wire, budget)
            if token is not None:
                tokens.append(token)
                wire = child_value(wire, token)
        return "".join(f"/{escape_pointer_token(token)}" for token in tokens)

    def _unwrapped(self, node: TypeNode | None) -> TypeNode | None:
        match node:
            case ModelNode(symbol=symbol) if (model := self._models[symbol]).native_kind == "root":
                return self._unwrapped(model.root)
            case UnionNode(members=(member,), nullable=True):
                return self._unwrapped(member)
            case _:
                return node

    def _pointer_step(
        self, node: TypeNode, element: str | int, wire: WireValue, budget: MatchBudget
    ) -> tuple[TypeNode | None, str | int | None]:
        match node:
            case ModelNode(symbol=symbol) if isinstance(element, str):
                field = self._keys[symbol].get(element)
                return (None, element) if field is None else (field.type, field.wire_name)
            case UnionNode(members=members):
                return self._wire_member(wire, members, budget), None
            case ArrayNode() | TupleNode() if isinstance(element, int):
                return item_node(node, element), element
            case MapNode(value=value):
                return value, element
            case _:
                return None, None

    def _native_extras(self, wire: WireValue, value: object, budget: MatchBudget) -> Mapping[str, WireValue]:
        if not self._inspect:
            return EMPTY
        walk = Walk(budget)
        self._collect(wire, value, self._binding.type, "", walk)
        return MappingProxyType(walk.extras)

    def _collect(self, wire: WireValue, native: object, node: TypeNode | None, pointer: str, walk: Walk) -> None:
        match node:
            case ModelNode(symbol=symbol) if (model := self._models[symbol]).native_kind == "root" and _is_root(native):
                self._collect(wire, native.root, model.root, pointer, walk)
            case ModelNode(symbol=symbol) if isinstance(wire, Mapping):
                self._collect_model(wire, native, self._models[symbol], pointer, walk)
            case UnionNode(members=members):
                member = self._native_member(native, members)
                if (
                    len(members) > 1
                    and isinstance(member, ModelNode)
                    and not self._matches(member.symbol, wire, walk.budget)
                ):
                    msg = f"The native union member at {pointer or '/'} does not match the wire schema"
                    raise ModelProjectionError(msg)
                self._collect(wire, native, member, pointer, walk)
            case ArrayNode() | TupleNode() if isinstance(wire, tuple) and is_sequence(native):
                for index, (entry, native_entry) in enumerate(zip(wire, native, strict=False)):
                    self._collect(entry, native_entry, item_node(node, index), at(pointer, index), walk)
            case MapNode(value=value) if isinstance(wire, Mapping) and is_mapping(native):
                entries = {json_key(key, pointer): entry for key, entry in native.items()}
                for name, entry in wire.items():
                    self._collect(entry, entries.get(name), value, at(pointer, name), walk)
            case _:
                return

    def _collect_model(
        self, wire: Mapping[str, WireValue], native: object, model: ModelBinding, pointer: str, walk: Walk
    ) -> None:
        fields = self._wire_fields[model.symbol]
        nested = self._nested[model.symbol]
        unstored = model.symbol in self._unstored
        reserved = self._reserved.get(model.symbol, _NO_KEYS)
        for name, entry in wire.items():
            if (field := nested.get(name)) is not None:
                self._collect(entry, getattr(native, field.native_name), field.type, at(pointer, name), walk)
            elif name not in fields and (unstored or name in reserved):
                walk.extras[at(pointer, name)] = entry

    def _native_wire(self, value: object, presence: PresenceTree | None) -> WireValue:
        dumped = self._adapter.serializer.to_python(
            value, mode="python", by_alias=False, round_trip=True, warnings=False
        )
        return freeze_wire(self._wire(dumped, value, self._binding.type, presence, ""))

    def _wire(
        self, dumped: object, native: object, node: TypeNode, presence: PresenceTree | None, pointer: str
    ) -> JSONValue:
        match node:
            case _ if native is None:
                return None
            case ModelNode(symbol=symbol) if isinstance(native, self._types[symbol]):
                return self._model_wire(dumped, native, self._models[symbol], presence, pointer)
            case UnionNode(members=members) if (member := self._native_member(native, members)) is not None:
                return self._wire(dumped, native, member, presence, pointer)
            case ArrayNode() | TupleNode() if is_sequence(dumped) or is_set(dumped):
                return self._sequence_wire(dumped, native, node, presence, pointer)
            case MapNode(value=value) if is_mapping(dumped) and is_mapping(native):
                entries = {json_key(key, pointer): (entry, native[key]) for key, entry in dumped.items()}
                check_object_presence(presence, entries.keys(), pointer)
                return {
                    name: self._wire(entries[name][0], entries[name][1], value, child, at(pointer, name))
                    for name, child in selected(presence, entries)
                }
            case LeafNode(representation=representation):
                return self._leaf(dumped, representation, presence, pointer)
            case _:
                raise shape_error(pointer)

    def _sequence_wire(
        self,
        dumped: list[object] | tuple[object, ...] | set[object] | frozenset[object],
        native: object,
        node: ArrayNode | TupleNode,
        presence: PresenceTree | None,
        pointer: str,
    ) -> JSONValue:
        check_array_presence(presence, len(dumped), pointer)
        if isinstance(node, ArrayNode) and is_set(dumped):
            return sorted_items([self._wire(entry, entry, node.item, None, pointer) for entry in dumped])
        items = (node.item,) * len(dumped) if isinstance(node, ArrayNode) else node.items
        if not (is_sequence(dumped) and is_sequence(native) and len(native) == len(dumped) == len(items)):
            raise shape_error(pointer)
        return [
            self._wire(entry, native_entry, item, presence and presence.child(index), at(pointer, index))
            for index, (entry, native_entry, item) in enumerate(zip(dumped, native, items, strict=True))
        ]

    def _model_wire(
        self, dumped: object, native: object, model: ModelBinding, presence: PresenceTree | None, pointer: str
    ) -> JSONValue:
        if model.root is not None and _is_root(native):
            return self._wire(dumped, native.root, model.root, presence, pointer)
        fields: Mapping[object, object] = dumped if is_mapping(dumped) else {}
        extra = (native.model_extra if isinstance(native, BaseModel) else None) or {}
        check_object_presence(presence, {field.wire_name for field in model.fields} | set(extra), pointer)
        fields_set = native.model_fields_set if isinstance(native, BaseModel) else None
        members: dict[str, JSONValue] = {}
        for field in model.fields:
            child = None if presence is None else presence.child(field.wire_name)
            if (
                self._excluded(field)
                or not self._present(field, native, presence, child, fields_set)
                or field.native_name not in fields
            ):
                continue
            members[field.wire_name] = self._wire(
                fields[field.native_name],
                getattr(native, field.native_name),
                field.type,
                child,
                at(pointer, field.wire_name),
            )
        for name, child in selected(presence, extra):
            members[name] = self._leaf(fields[name], "value", child, at(pointer, name))
        return members

    @staticmethod
    def _present(
        field: FieldBinding,
        native: object,
        presence: PresenceTree | None,
        child: PresenceTree | None,
        fields_set: set[str] | None,
    ) -> bool:
        if presence is not None:
            return child is not None
        if fields_set is not None:
            return field.native_name in fields_set
        return not (field.omit_none and getattr(native, field.native_name) is None)

    def _leaf(
        self, value: object, representation: Representation, presence: PresenceTree | None, pointer: str
    ) -> JSONValue:
        match value:
            case Enum():
                return self._leaf(value.value, representation, presence, pointer)
            case SecretStr() | SecretBytes():
                return self._leaf(value.get_secret_value(), representation, presence, pointer)
            case _ if is_mapping(value) or is_sequence(value) or is_set(value):
                return self._json_container(value, presence, pointer)
            case None | bool() | int() | float() | str() | Decimal():
                return (
                    str(value)
                    if representation == "decimal_string" and isinstance(value, Decimal)
                    else json_scalar(value, pointer)
                )
            case _:
                return self._leaf(self._jsonable(value, pointer), representation, presence, pointer)

    def _jsonable(self, value: object, pointer: str) -> object:
        """Convert a leaf through Pydantic's own JSON serialization, or through its type's serializer if it has one."""
        try:
            return to_jsonable_python(value)
        except PydanticSerializationError:
            pass
        kind = type(value)
        try:
            if (adapter := self._leaves.get(kind)) is None:
                adapter = self._leaves[kind] = TypeAdapter(kind)
            return adapter.dump_python(value, mode="json")
        except (PydanticSchemaGenerationError, PydanticSerializationError):
            msg = f"The value at {pointer or '/'} has no JSON representation"
            raise ModelProjectionError(msg) from None

    def _json_container(
        self,
        value: Mapping[object, object] | list[object] | tuple[object, ...] | set[object] | frozenset[object],
        presence: PresenceTree | None,
        pointer: str,
    ) -> JSONValue:
        if is_mapping(value):
            entries = {json_key(key, pointer): entry for key, entry in value.items()}
            check_object_presence(presence, entries.keys(), pointer)
            return {
                name: self._leaf(entries[name], "value", child, at(pointer, name))
                for name, child in selected(presence, entries)
            }
        check_array_presence(presence, len(value), pointer)
        if is_set(value):
            return sorted_items([self._leaf(entry, "value", None, pointer) for entry in value])
        return [
            self._leaf(entry, "value", presence and presence.child(index), at(pointer, index))
            for index, entry in enumerate(value)
        ]
