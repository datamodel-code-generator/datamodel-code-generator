"""Model codecs for the Pydantic v2 BaseModel and pydantic dataclass backends."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from decimal import Decimal
from enum import Enum
from itertools import chain
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Generic, TypeVar, overload

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
from .errors import (
    CodecBindingError,
    CodecConfigurationError,
    CodecResourceLimitError,
    ModelProjectionError,
    NativeIssue,
    NativeValidationError,
    WireValidationError,
)
from .media import encode_json
from .patterns import MatchBudget
from .values import DecodedValue, ModelInput, ModelValue, ProjectionIssue
from .wire import (
    JSONValue,
    PresenceTree,
    WireValue,
    check_array_presence,
    check_object_presence,
    checked_key,
    checked_scalar,
    escape_pointer_token,
    freeze_wire,
    snapshot_presence,
    thaw_wire,
    without_pointers,
)

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo
    from typing_extensions import TypeIs

    from .context import CodecContext
    from .schema import SchemaBundle, WireSchemaValidator, WireValidator

T = TypeVar("T")

_EMPTY: Final[Mapping[str, WireValue]] = MappingProxyType({})
_NO_KEYS: Final = frozenset[str]()
_BACKENDS: Final = frozenset({"pydantic_v2.BaseModel", "pydantic_v2.dataclass"})


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_sequence(value: object) -> TypeIs[list[object] | tuple[object, ...]]:
    return isinstance(value, (list, tuple))


def _is_set(value: object) -> TypeIs[set[object] | frozenset[object]]:
    return isinstance(value, (set, frozenset))


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


def _selected(presence: PresenceTree | None, names: Iterable[str]) -> list[tuple[str, PresenceTree | None]]:
    if presence is None:
        return [(name, None) for name in names]
    return [(name, child) for name in names if (child := presence.child(name)) is not None]


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


def _at(pointer: str, token: str | int) -> str:
    return f"{pointer}/{escape_pointer_token(token)}"


def _scalar(value: object, pointer: str) -> JSONValue:
    try:
        return checked_scalar(value)
    except (TypeError, ValueError) as error:
        msg = f"{error} at {pointer or '/'}"
        raise ModelProjectionError(msg) from None


def _key(key: object, pointer: str) -> str:
    try:
        return checked_key(key.value if isinstance(key, Enum) else key)
    except (TypeError, ValueError) as error:
        msg = f"{error} at {pointer or '/'}"
        raise ModelProjectionError(msg) from None


def _sorted_items(items: list[JSONValue]) -> list[JSONValue]:
    return sorted(items, key=encode_json)


def _child(wire: WireValue, token: str | int) -> WireValue:
    if isinstance(wire, tuple) and isinstance(token, int):
        return wire[token]
    return wire.get(str(token)) if isinstance(wire, Mapping) else None


def _has_models(node: TypeNode | None) -> bool:
    match node:
        case ModelNode():
            return True
        case ArrayNode():
            return _has_models(node.item)
        case MapNode():
            return _has_models(node.value)
        case TupleNode():
            return any(_has_models(item) for item in node.items)
        case UnionNode():
            return any(_has_models(item) for item in node.members)
        case _:
            return False


def _model_unions(node: TypeNode | None) -> bool:
    match node:
        case UnionNode():
            members = node.members
            return (len(members) > 1 and any(isinstance(item, ModelNode) for item in members)) or any(
                _model_unions(item) for item in members
            )
        case TupleNode():
            return any(_model_unions(item) for item in node.items)
        case ArrayNode():
            return _model_unions(node.item)
        case MapNode():
            return _model_unions(node.value)
        case _:
            return False


def _item(node: ArrayNode | TupleNode, index: int) -> TypeNode | None:
    if isinstance(node, ArrayNode):
        return node.item
    return node.items[index] if index < len(node.items) else None


def _shape(pointer: str) -> ModelProjectionError:
    return ModelProjectionError(f"The value at {pointer or '/'} does not have its bound shape")


@dataclasses.dataclass(slots=True)
class _Walk:
    budget: MatchBudget
    extras: dict[str, WireValue] = dataclasses.field(default_factory=dict[str, WireValue])
    issues: list[ProjectionIssue] = dataclasses.field(default_factory=list[ProjectionIssue])
    forbidden: list[NativeIssue] = dataclasses.field(default_factory=list[NativeIssue])


class PydanticModelCodec(Generic[T]):
    """Validate, construct, snapshot, and encode one bound use of a Pydantic v2 model type."""

    __slots__ = (
        "_adapter",
        "_binding",
        "_bundle",
        "_inspect",
        "_keys",
        "_leaves",
        "_members",
        "_models",
        "_nested",
        "_reserved",
        "_types",
        "_unstored",
        "_validator",
        "_walk",
        "_wire_fields",
    )

    @overload
    def __init__(
        self,
        binding: UseBinding,
        native_type: type[T],
        models: Mapping[str, type],
        bundle: SchemaBundle,
        *,
        validator: WireValidator | None = None,
    ) -> None: ...
    @overload
    def __init__(
        self,
        binding: UseBinding,
        native_type: object,
        models: Mapping[str, type],
        bundle: SchemaBundle,
        *,
        validator: WireValidator | None = None,
    ) -> None: ...
    def __init__(
        self,
        binding: UseBinding,
        native_type: object,
        models: Mapping[str, type],
        bundle: SchemaBundle,
        *,
        validator: WireValidator | None = None,
    ) -> None:
        """Check the binding against the native types and bundle once, before any value is processed.

        A schema adapter's validator replaces the bundle's builtin validator of the use's schema.
        """
        if binding.converter_strategy != "pydantic_type_adapter" or binding.backend not in _BACKENDS:
            msg = "The binding does not select a Pydantic v2 type adapter"
            raise CodecConfigurationError(msg)
        if bundle.direction != binding.direction:
            msg = "The schema bundle does not validate the binding's direction"
            raise CodecConfigurationError(msg)
        self._binding = binding
        self._bundle = bundle
        self._models = {model.symbol: model for model in binding.models}
        natives = {model.symbol: _native_type(model, models) for model in binding.models}
        self._types = {symbol: native for symbol, (native, _) in natives.items()}
        self._reserved = {
            model.symbol: reserved
            for model in binding.models
            if (reserved := natives[model.symbol][1] - {field.wire_name for field in model.fields})
        }
        self._wire_fields = {
            model.symbol: {field.wire_name: field for field in model.fields} for model in binding.models
        }
        self._nested = {
            model.symbol: {field.wire_name: field for field in model.fields if _has_models(field.type)}
            for model in binding.models
        }
        self._keys = {
            model.symbol: {key: field for field in model.fields for key in (field.native_name, field.validation_key)}
            for model in binding.models
        }
        self._members: dict[str, WireSchemaValidator] = {}
        self._leaves: dict[type, TypeAdapter[object]] = {}
        self._validator: WireValidator = validator if validator is not None else bundle.validator(binding.schema_id)
        self._adapter: TypeAdapter[T] = TypeAdapter(native_type)
        self._walk = (
            binding.projection_mode == "envelope"
            or bool(self._reserved)
            or any(field.validation_key != field.wire_name for model in binding.models for field in model.fields)
        )
        self._unstored = frozenset(
            model.symbol
            for model in binding.models
            if model.open
            and model.native_kind != "root"
            and (model.extra == "ignore" or (model.extra == "allow" and model.native_kind == "dataclass"))
        )
        self._inspect = any(
            _model_unions(node)
            for node in (
                binding.type,
                *(model.root for model in binding.models),
                *(field.type for model in binding.models for field in model.fields),
            )
        ) or bool(self._unstored or self._reserved)

    @property
    def binding(self) -> UseBinding:
        """Return the static binding this codec was built from."""
        return self._binding

    def decode(self, wire: WireValue, context: CodecContext) -> DecodedValue[T]:
        """Validate a received wire value in its direction, then construct T or a known-gap envelope."""
        self._require(context, inbound=True)
        budget = MatchBudget()
        try:
            snapshot = freeze_wire(wire)
            if issues := self._validator.validate(snapshot, budget=budget, context=context):
                raise WireValidationError(issues)
            return self._project(snapshot, budget)
        except RecursionError:
            raise self._nesting() from None

    def from_wire(self, wire: JSONValue | WireValue, context: CodecContext) -> DecodedValue[T]:
        """Copy a value to send, drop members excluded in its direction, validate it, and project it."""
        self._require(context, inbound=False)
        budget = MatchBudget()
        try:
            return self._project(self._outbound(freeze_wire(wire), budget, context), budget)
        except RecursionError:
            raise self._nesting() from None

    def snapshot(self, value: T, context: CodecContext, *, presence: PresenceTree | None = None) -> ModelValue[T]:
        """Capture a native value's wire form for sending, using explicit presence when given."""
        self._require(context, inbound=False)
        try:
            wire = self._outbound(self._native_wire(value, presence), MatchBudget(), context)
        except RecursionError:
            raise self._nesting() from None
        return ModelValue(
            value=value, binding_id=self._binding.binding_id, wire=wire, presence=snapshot_presence(wire), extras=_EMPTY
        )

    def encode(self, value: object, context: CodecContext) -> WireValue:
        """Return the validated wire value to send for a native value or a snapshot of this binding.

        The value may come from a dynamic boundary such as a server handler, so its shape is checked here.
        """
        self._require(context, inbound=False)
        try:
            match value:
                case ModelValue() | ModelInput():
                    if value.binding_id != self._binding.binding_id:
                        msg = "The value was captured for a different binding"
                        raise CodecBindingError(msg)
                    return self._outbound(freeze_wire(value.wire), MatchBudget(), context)
                case _:
                    pass
            return self._outbound(self._native_wire(value, None), MatchBudget(), context)
        except RecursionError:
            raise self._nesting() from None

    @staticmethod
    def _nesting() -> CodecResourceLimitError:
        return CodecResourceLimitError("The value is nested beyond the interpreter recursion limit")

    def _require(self, context: CodecContext, *, inbound: bool) -> None:
        binding = self._binding
        if (context.direction, context.schema_id, context.operation_id, context.media_type, context.inbound) != (
            binding.direction,
            binding.schema_id,
            binding.operation_id,
            binding.media_type,
            inbound,
        ):
            msg = "The codec context does not match the bound use"
            raise CodecBindingError(msg)

    def _outbound(self, wire: WireValue, budget: MatchBudget, context: CodecContext) -> WireValue:
        if excluded := self._validator.excluded(wire, budget=budget):
            wire = without_pointers(wire, excluded)
        if issues := self._validator.validate(wire, budget=budget, context=context):
            raise WireValidationError(issues)
        return wire

    def _project(self, wire: WireValue, budget: MatchBudget) -> DecodedValue[T]:
        presence = snapshot_presence(wire)
        binding_id = self._binding.binding_id
        walk = _Walk(budget)
        keyed = self._keyed(wire, self._binding.type, "", (), walk) if self._walk else wire
        if not walk.issues:
            if walk.forbidden:
                raise NativeValidationError(tuple(walk.forbidden))
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

    def _native(self, keyed: JSONValue | WireValue, wire: WireValue, budget: MatchBudget) -> T:
        try:
            return self._adapter.validate_json(encode_json(keyed), by_alias=True, by_name=False)
        except ValidationError as error:
            raise NativeValidationError(
                tuple(
                    NativeIssue(
                        code=f"native.{item['type']}",
                        pointer=self._native_pointer(tuple(item["loc"]), wire, budget),
                        native_path=tuple(item["loc"]),
                    )
                    for item in error.errors(include_url=False, include_context=False, include_input=False)
                )
            ) from None

    def _keyed(
        self, wire: WireValue, node: TypeNode | None, pointer: str, path: tuple[str | int, ...], walk: _Walk
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
                    self._keyed(entry, _item(node, index), _at(pointer, index), (*path, index), walk)
                    for index, entry in enumerate(wire)
                ]
            case MapNode(value=value) if isinstance(wire, Mapping):
                return {
                    name: self._keyed(entry, value, _at(pointer, name), (*path, name), walk)
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
        walk: _Walk,
    ) -> JSONValue:
        fields = self._wire_fields[model.symbol]
        reserved = self._reserved.get(model.symbol, _NO_KEYS)
        keyed: dict[str, JSONValue] = {}
        for name, entry in wire.items():
            if (field := fields.get(name)) is not None:
                keyed[field.validation_key] = self._keyed(
                    entry, field.type, _at(pointer, name), (*path, field.validation_key), walk
                )
            elif name not in reserved:
                if model.symbol in self._unstored:
                    walk.extras[_at(pointer, name)] = entry
                keyed[name] = thaw_wire(entry)
            elif model.extra == "forbid":
                walk.forbidden.append(
                    NativeIssue(code="native.extra_forbidden", pointer=_at(pointer, name), native_path=(*path, name))
                )
            else:
                walk.extras[_at(pointer, name)] = entry
        walk.issues.extend(
            ProjectionIssue(
                code="DIRECTIONAL_REQUIRED",
                pointer=_at(pointer, field.wire_name),
                schema_location=model.schema_id or "",
                field_id=field.field_id,
                message="The native type requires a property that this direction excludes",
            )
            for field in model.fields
            if field.required and field.wire_name not in wire and self._excluded(field)
        )
        return keyed

    def _native_pointer(self, loc: tuple[str | int, ...], wire: WireValue, budget: MatchBudget) -> str:
        node: TypeNode | None = self._binding.type
        tokens: list[str | int] = []
        for element in loc:
            if (node := self._unwrapped(node)) is None:
                break
            node, token = self._pointer_step(node, element, wire, budget)
            if token is not None:
                tokens.append(token)
                wire = _child(wire, token)
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
                return _item(node, element), element
            case MapNode(value=value):
                return value, element
            case _:
                return None, None

    def _excluded(self, field: FieldBinding) -> bool:
        return field.read_only if self._binding.direction == "request" else field.write_only

    def _matches(self, symbol: str, wire: WireValue, budget: MatchBudget) -> bool:
        if (schema_id := (model := self._models[symbol]).schema_id) is None:
            return model.native_kind == "root" or isinstance(wire, Mapping)
        if (validator := self._members.get(symbol)) is None:
            validator = self._members[symbol] = self._bundle.validator(schema_id)
        return not validator.validate(wire, budget=budget)

    def _wire_member(self, wire: WireValue, members: tuple[TypeNode, ...], budget: MatchBudget) -> TypeNode | None:
        for member in members:
            match member:
                case ModelNode(symbol=symbol) if self._matches(symbol, wire, budget):
                    return member
                case ArrayNode() | TupleNode() if isinstance(wire, tuple):
                    return member
                case MapNode() if isinstance(wire, Mapping):
                    return member
                case _:
                    continue
        return None

    def _native_member(self, native: object, members: tuple[TypeNode, ...]) -> TypeNode | None:
        for member in members:
            match member:
                case ModelNode(symbol=symbol) if isinstance(native, self._types[symbol]):
                    return member
                case ArrayNode() | TupleNode() if _is_sequence(native) or _is_set(native):
                    return member
                case MapNode() if _is_mapping(native):
                    return member
                case _:
                    continue
        return next((member for member in members if isinstance(member, LeafNode)), None)

    def _native_extras(self, wire: WireValue, value: object, budget: MatchBudget) -> Mapping[str, WireValue]:
        if not self._inspect:
            return _EMPTY
        walk = _Walk(budget)
        self._collect(wire, value, self._binding.type, "", walk)
        return MappingProxyType(walk.extras)

    def _collect(self, wire: WireValue, native: object, node: TypeNode | None, pointer: str, walk: _Walk) -> None:
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
            case ArrayNode() | TupleNode() if isinstance(wire, tuple) and _is_sequence(native):
                for index, (entry, native_entry) in enumerate(zip(wire, native, strict=False)):
                    self._collect(entry, native_entry, _item(node, index), _at(pointer, index), walk)
            case MapNode(value=value) if isinstance(wire, Mapping) and _is_mapping(native):
                entries = {_key(key, pointer): entry for key, entry in native.items()}
                for name, entry in wire.items():
                    self._collect(entry, entries.get(name), value, _at(pointer, name), walk)
            case _:
                return

    def _collect_model(
        self, wire: Mapping[str, WireValue], native: object, model: ModelBinding, pointer: str, walk: _Walk
    ) -> None:
        fields = self._wire_fields[model.symbol]
        nested = self._nested[model.symbol]
        unstored = model.symbol in self._unstored
        reserved = self._reserved.get(model.symbol, _NO_KEYS)
        for name, entry in wire.items():
            if (field := nested.get(name)) is not None:
                self._collect(entry, getattr(native, field.native_name), field.type, _at(pointer, name), walk)
            elif name not in fields and (unstored or name in reserved):
                walk.extras[_at(pointer, name)] = entry

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
            case ArrayNode() | TupleNode() if _is_sequence(dumped) or _is_set(dumped):
                return self._sequence_wire(dumped, native, node, presence, pointer)
            case MapNode(value=value) if _is_mapping(dumped) and _is_mapping(native):
                entries = {_key(key, pointer): (entry, native[key]) for key, entry in dumped.items()}
                check_object_presence(presence, set(entries), pointer)
                return {
                    name: self._wire(entries[name][0], entries[name][1], value, child, _at(pointer, name))
                    for name, child in _selected(presence, entries)
                }
            case LeafNode(representation=representation):
                return self._leaf(dumped, representation, presence, pointer)
            case _:
                raise _shape(pointer)

    def _sequence_wire(
        self,
        dumped: list[object] | tuple[object, ...] | set[object] | frozenset[object],
        native: object,
        node: ArrayNode | TupleNode,
        presence: PresenceTree | None,
        pointer: str,
    ) -> JSONValue:
        check_array_presence(presence, len(dumped), pointer)
        if isinstance(node, ArrayNode) and _is_set(dumped):
            return _sorted_items([self._wire(entry, entry, node.item, None, pointer) for entry in dumped])
        items = (node.item,) * len(dumped) if isinstance(node, ArrayNode) else node.items
        if not (_is_sequence(dumped) and _is_sequence(native) and len(native) == len(dumped) == len(items)):
            raise _shape(pointer)
        return [
            self._wire(entry, native_entry, item, presence and presence.child(index), _at(pointer, index))
            for index, (entry, native_entry, item) in enumerate(zip(dumped, native, items, strict=True))
        ]

    def _model_wire(
        self, dumped: object, native: object, model: ModelBinding, presence: PresenceTree | None, pointer: str
    ) -> JSONValue:
        if model.root is not None and _is_root(native):
            return self._wire(dumped, native.root, model.root, presence, pointer)
        fields: Mapping[object, object] = dumped if _is_mapping(dumped) else {}
        extra = (native.model_extra if isinstance(native, BaseModel) else None) or {}
        check_object_presence(presence, {field.wire_name for field in model.fields} | set(extra), pointer)
        fields_set = native.model_fields_set if isinstance(native, BaseModel) else None
        members: dict[str, JSONValue] = {}
        for field in model.fields:
            child = None if presence is None else presence.child(field.wire_name)
            if not self._present(field, native, presence, child, fields_set) or field.native_name not in fields:
                continue
            members[field.wire_name] = self._wire(
                fields[field.native_name],
                getattr(native, field.native_name),
                field.type,
                child,
                _at(pointer, field.wire_name),
            )
        for name, child in _selected(presence, extra):
            members[name] = self._leaf(fields[name], "value", child, _at(pointer, name))
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
            case _ if _is_mapping(value) or _is_sequence(value) or _is_set(value):
                return self._json_container(value, presence, pointer)
            case None | bool() | int() | float() | str() | Decimal():
                return (
                    str(value)
                    if representation == "decimal_string" and isinstance(value, Decimal)
                    else _scalar(value, pointer)
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
        if _is_mapping(value):
            entries = {_key(key, pointer): entry for key, entry in value.items()}
            check_object_presence(presence, set(entries), pointer)
            return {
                name: self._leaf(entries[name], "value", child, _at(pointer, name))
                for name, child in _selected(presence, entries)
            }
        check_array_presence(presence, len(value), pointer)
        if _is_set(value):
            return _sorted_items([self._leaf(entry, "value", None, pointer) for entry in value])
        return [
            self._leaf(entry, "value", presence and presence.child(index), _at(pointer, index))
            for index, entry in enumerate(value)
        ]
