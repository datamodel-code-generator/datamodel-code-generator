"""Plan model codec bindings for the directional type uses of an accepted batch."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import cached_property
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from datamodel_code_generator._codec_declarations import CodecDeclarations
from datamodel_code_generator._codec_type_source import type_reason
from datamodel_code_generator._openapi_codec_adapters import (
    AdapterPlan,
    AdapterSelection,
    plan_adapters,
    select_adapters,
    suppressed,
    type_nodes,
)
from datamodel_code_generator._openapi_wire_plan import CodecDiagnostic, CodecReason, WirePlan
from datamodel_code_generator._python_type_annotation import render_python_type_expr
from datamodel_code_generator._runtime.model_codecs.bindings import (
    ArrayNode,
    ConverterStrategy,
    ExtraPolicy,
    FieldBinding,
    LeafNode,
    MapNode,
    ModelBinding,
    ModelNode,
    NativeKind,
    Representation,
    TupleNode,
    TypeNode,
    UnionNode,
    UseBinding,
)
from datamodel_code_generator._target_contract import (
    AnnotatedType,
    BoundType,
    BuiltinType,
    ConstructorType,
    FieldSlot,
    FieldUseBinding,
    FinalModelSymbol,
    FinalPythonType,
    GeneratedSymbolType,
    GeneratedTypeContractBatch,
    GenericType,
    ImportedType,
    KnownBackendValue,
    LiteralScalar,
    LiteralType,
    ModelArtifactAddress,
    ModelFieldFacts,
    NoneType,
    OperationId,
    SourceLocation,
    SymbolId,
    TypeUseBinding,
    TypeUseId,
    UnionType,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator._openapi_generation import SourceLease

PydanticBackend: TypeAlias = Literal["pydantic_v2.BaseModel", "pydantic_v2.dataclass"]
CodecBackend: TypeAlias = Literal[
    "pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"
]
ArrayKind: TypeAlias = Literal["list", "set", "frozenset"]

_SYMBOL_BACKENDS: Final[dict[CodecBackend, str]] = {
    "pydantic_v2.BaseModel": "pydantic",
    "pydantic_v2.dataclass": "pydantic_dataclass",
    "dataclasses.dataclass": "dataclass",
    "typing.TypedDict": "typeddict",
    "msgspec.Struct": "msgspec",
}
_STRATEGIES: Final[dict[CodecBackend, ConverterStrategy]] = {
    "pydantic_v2.BaseModel": "pydantic_type_adapter",
    "pydantic_v2.dataclass": "pydantic_type_adapter",
    "dataclasses.dataclass": "dataclass_structural",
    "typing.TypedDict": "typeddict_structural",
    "msgspec.Struct": "msgspec_convert",
}
_NATIVE_KINDS: Final[dict[object, Literal["model", "dataclass", "typed_dict", "struct"]]] = {
    "pydantic": "model",
    "pydantic_dataclass": "dataclass",
    "dataclass": "dataclass",
    "typeddict": "typed_dict",
    "msgspec": "struct",
}
_MSGSPEC_KINDS: Final = ("str", "int", "array", "object")
_MSGSPEC_ENUMS: Final = (frozenset({"str"}), frozenset({"int"}))
_MSGSPEC_STRINGS: Final = frozenset({
    "str",
    "bytes",
    "datetime.datetime",
    "datetime.date",
    "datetime.time",
    "datetime.timedelta",
    "decimal.Decimal",
    "uuid.UUID",
})
_MSGSPEC_UNSUPPORTED: Final = frozenset({
    "ipaddress.IPv4Address",
    "ipaddress.IPv6Address",
    "ipaddress.IPv4Network",
    "ipaddress.IPv6Network",
    "pathlib.Path",
})
_MAPPINGS: Final = frozenset({"dict", "typing.Mapping", "collections.abc.Mapping"})
_STRUCTURAL_KEYS: Final = frozenset({"str", "object", "typing.Any"})
_STRUCTURAL_LEAVES: Final = _STRUCTURAL_KEYS | {
    "bool",
    "int",
    "float",
    "decimal.Decimal",
    "datetime.date",
    "datetime.datetime",
    "datetime.time",
    "datetime.timedelta",
    "uuid.UUID",
    "ipaddress.IPv4Address",
    "ipaddress.IPv6Address",
    "ipaddress.IPv4Network",
    "ipaddress.IPv6Network",
    "pathlib.Path",
}
_ARRAY_KINDS: Final[dict[str, ArrayKind]] = {"set": "set", "frozenset": "frozenset"}
_OPTIONAL_FALLBACK: Final = ("none", "synthesized_optional_fallback", "optional_fallback")
_EXTRA_POLICIES: Final[dict[object, ExtraPolicy]] = {"ignore": "ignore", "allow": "allow", "forbid": "forbid"}
_TYPED_EXTRAS: Final = "__pydantic_extra__"
_NO_DECLARATIONS: Final = CodecDeclarations()


@dataclass(frozen=True, slots=True)
class CodecPlan:
    """Keep every planned directional use binding, the selected adapters, and every blocking diagnostic."""

    bindings: tuple[tuple[TypeUseId, UseBinding], ...]
    diagnostics: tuple[CodecDiagnostic, ...]
    adapters: tuple[AdapterPlan, ...] = ()
    imports: tuple[tuple[int, str], ...] = ()


def _setting(symbol: FinalModelSymbol, name: str, *, parameter: bool = False) -> object:
    for setting in (symbol.facts.parameters if parameter else symbol.facts.configuration) if symbol.facts else ():
        if setting.name == name and setting.present:
            match setting.value:
                case KnownBackendValue(value=LiteralScalar(value=value)):
                    return value
                case value:
                    return value
    return None


def _is_decimal(value: FinalPythonType) -> bool:
    match value:
        case ImportedType(import_=imported):
            return (imported.from_, imported.import_) == ("decimal", "Decimal")
        case ConstructorType(callable=ImportedType(import_=imported)):
            return imported.import_ == "condecimal"
        case _:
            return False


def _array_kind(base: FinalPythonType) -> ArrayKind:
    return _ARRAY_KINDS.get(base.name, "list") if isinstance(base, BuiltinType) else "list"


def _models(node: TypeNode) -> Iterator[str]:
    return (item.symbol for item in type_nodes(node) if isinstance(item, ModelNode))


def _at(location: SourceLocation, *tokens: str | int) -> SourceLocation:
    return replace(location, pointer=location.pointer + "".join(f"/{token}" for token in tokens))


def _name(value: FinalPythonType) -> str | None:
    match value:
        case BuiltinType():
            return value.name
        case ImportedType():
            return f"{value.import_.from_}.{value.import_.import_}"
        case _:
            pass
    return None


def _literal_kind(value: LiteralType) -> str | None:
    """Return the one kind msgspec reads a literal's values as, or None for values or mixtures it cannot describe."""
    kinds = {item.kind if isinstance(item, LiteralScalar) else "enum" for item in value.values}
    return next(iter(kinds)) if len(kinds) == 1 and kinds <= {"str", "int", "none"} else None


def _struct_tag(symbol: FinalModelSymbol) -> tuple[str, str | int] | None:
    """Return the tag field and value msgspec derives from a Struct's tag parameters, or None for an untagged Struct.

    As in msgspec, ``tag=True`` or a ``tag_field`` alone tags a Struct with its class name, and the field defaults to
    ``type``; a tag function, whose value only running it tells, is no known tag.
    """
    field = _setting(symbol, "tag_field", parameter=True)
    name = field if isinstance(field, str) else "type"
    match tag := _setting(symbol, "tag", parameter=True):
        case True | None if tag is True or isinstance(field, str):
            return name, symbol.name
        case str() | int() if not isinstance(tag, bool):
            return name, tag
        case _:
            pass
    return None


def _functional(symbol: FinalModelSymbol) -> bool:
    """Return whether a TypedDict uses the functional syntax, whose keys are the wire names rather than field names."""
    return symbol.facts is not None and symbol.facts.functional_typeddict


def _encoded_as_wire(member: FieldUseBinding, facts: ModelFieldFacts) -> bool:
    """Return whether a Struct encodes a field under its wire name, its alias or else its name, as msgspec reads it."""
    return (
        (slot := member.slot) is None
        or member.wire_name is None
        or member.exclusion == "tag"
        or (facts.alias or slot.name) == member.wire_name
    )


def _meta_pattern(facts: ModelFieldFacts) -> bool:
    return any(name == "pattern" for layer in facts.backend.emitted.meta_layers for name, _ in layer.keywords)


def _known_false(value: object) -> bool:
    return isinstance(value, KnownBackendValue) and value.value == LiteralScalar(kind="bool", value=False)


def artifact_module(artifact: ModelArtifactAddress) -> str:
    """Return the dotted module path of a model artifact below its model package."""
    *parents, name = artifact.relative_path
    modules = () if artifact.result_key == "single" else (*parents, name.removesuffix(".py"))
    return ".".join((artifact.model_package, *modules)).removesuffix(".__init__")


def _symbol_key(symbol: FinalModelSymbol) -> str:
    return f"{artifact_module(symbol.artifact) if symbol.artifact else ''}:{symbol.name}"


def _accepted(facts: ModelFieldFacts, slot: FieldSlot, wire_name: str, *, generated: bool) -> frozenset[str]:
    if facts.validation_aliases:
        return frozenset(facts.validation_aliases)
    if facts.alias and not facts.use_serialization_alias:
        return frozenset({facts.alias})
    return frozenset({wire_name if generated else slot.name})


@dataclass(frozen=True, slots=True)
class _Member:
    facts: ModelFieldFacts
    slot: FieldSlot
    wire_name: str
    schema: SourceLocation


class _CodecPlanner:
    def __init__(
        self,
        batch: GeneratedTypeContractBatch,
        wire: WirePlan,
        backend: CodecBackend,
        adapted: frozenset[TypeUseId],
    ) -> None:
        self.batch = batch
        self.wire = wire
        self.backend: CodecBackend = backend
        self.structural = _STRATEGIES[backend] != "pydantic_type_adapter"
        self.gaps: set[str] = set()
        self.adapted = adapted
        self.quiet = False
        self.imports: dict[int, str] = {
            symbol.id: _symbol_key(symbol) for symbol in batch.symbols if symbol.artifact is not None
        }
        self.symbols = {symbol.id: symbol for symbol in batch.symbols}
        self.schema_ids = dict(wire.schema_ids)
        self.locations: dict[str, SourceLocation] = {}
        self.symbol_schemas: dict[int, str] = {}
        uses = {use.id: use for use in batch.type_uses}
        for use_id, schema_id in wire.schema_ids:
            self.locations.setdefault(schema_id, uses[use_id].schema or use_id.schema_site)
        declared = [
            (value.symbol, location)
            for use in batch.type_uses
            if use.id.role == "schema"
            and isinstance(value := use.type, GeneratedSymbolType)
            and (location := use.schema) is not None
        ]
        resolved = wire.resolved(location for _, location in declared)
        for symbol, location in declared:
            if symbol not in self.symbol_schemas and (target := resolved.get(location)) is not None:
                self.symbol_schemas[symbol] = schema_id = wire.schema_id(target)
                self.locations.setdefault(schema_id, target)
        self.members: dict[int, list[FieldUseBinding]] = {}
        for member in batch.fields:
            self.members.setdefault(member.consumer, []).append(member)
        self.caches: dict[bool, dict[str, ModelBinding]] = {False: {}, True: {}}
        self.building: set[tuple[bool, str]] = set()
        self.aliases: set[int] = set()
        self.diagnostics: list[CodecDiagnostic] = []
        self.msgspec = _MsgspecTypes(self)

    def report(self, code: CodecReason, source: SourceLocation, message: str) -> None:
        if (diagnostic := CodecDiagnostic(code, source, message)) not in self.diagnostics:
            self.diagnostics.append(diagnostic)

    def spelled(self, use: TypeUseBinding, value: FinalPythonType) -> bool:
        types = [value]
        if use.id in self.adapted and isinstance(value, GeneratedSymbolType):
            types.extend(facts.type for member in self.members.get(value.symbol, []) if (facts := member.model_facts))
        reasons: list[CodecReason] = [
            reason for item in types if (reason := type_reason(item, self.imports)) is not None
        ]
        if reasons:
            self.report(
                reasons[0], use.id.use_site, "The use's final type has no expression a generated module can import"
            )
        return not reasons

    def child(self, schema: SourceLocation | None, *tokens: str | int) -> SourceLocation | None:
        return None if schema is None else _at(self.wire.schema(schema)[0], *tokens)

    def representation(self, value: FinalPythonType, schema: SourceLocation | None) -> Representation:
        if schema is None or not _is_decimal(value):
            return "value"
        match self.wire.schema(schema)[1].get("type"):
            case "string":
                return "decimal_string"
            case tuple() as kinds if "string" in kinds and "number" not in kinds:
                return "decimal_string"
            case _:
                return "value"

    def node(self, value: FinalPythonType, schema: SourceLocation | None, source: SourceLocation) -> TypeNode:  # noqa: PLR0911
        match value:
            case GeneratedSymbolType():
                return self.symbol_node(self.symbols[value.symbol], source)
            case AnnotatedType(base=base):
                return self.node(base, schema, source)
            case UnionType(members=members):
                flat = tuple(dict.fromkeys(_union_members(self, members, frozenset())))
                present = [member for member in flat if not isinstance(member, NoneType)]
                return UnionNode(
                    tuple(self.node(member, schema, source) for member in present), len(present) != len(flat)
                )
            case GenericType(arguments=items, tuple_form="fixed"):
                return TupleNode(
                    tuple(
                        self.node(item, self.child(schema, "prefixItems", index), source)
                        for index, item in enumerate(items)
                    )
                )
            case GenericType(base=base, arguments=(item,)):
                return ArrayNode(self.node(item, self.child(schema, "items"), source), _array_kind(base))
            case GenericType(arguments=(key, item)):
                if self.structural and not self.quiet and (name := self.unstructured(key, _STRUCTURAL_KEYS)):
                    self.report(
                        "MC_ADAPTER_REQUIRED",
                        source,
                        f"The {name} key type has no structural conversion; register a model adapter",
                    )
                return MapNode(self.node(item, self.child(schema, "additionalProperties"), source))
            case _:
                if self.structural and not self.quiet and (name := self.unstructured(value, _STRUCTURAL_LEAVES)):
                    self.report(
                        "MC_ADAPTER_REQUIRED",
                        source,
                        f"The {name} type has no structural conversion; register a model adapter",
                    )
                return LeafNode(self.representation(value, schema))

    def unstructured(self, value: FinalPythonType, supported: frozenset[str]) -> str | None:
        """Return the name of a type that the structural converters do not read, or None when they do."""
        match value:
            case GeneratedSymbolType() if (declared := self.symbols[value.symbol]).kind == "alias":
                return next(
                    (
                        self.unstructured(facts.type, supported)
                        for member in self.members.get(value.symbol, [])
                        if (facts := member.model_facts) is not None
                    ),
                    declared.name,
                )
            case GeneratedSymbolType():
                return None if (declared := self.symbols[value.symbol]).kind == "enum" else declared.name
            case BuiltinType() | ImportedType():
                return None if (name := _name(value)) in supported else name
            case BoundType(binding=binding):
                return render_python_type_expr(binding.expression)
            case _:
                pass
        return None

    def symbol_node(self, symbol: FinalModelSymbol, source: SourceLocation) -> TypeNode:
        match symbol.kind:
            case "model" | "root" | "custom" if self.quiet or (
                symbol.kind != "custom"
                and symbol.backend == _SYMBOL_BACKENDS[self.backend]
                and symbol.facts is not None
            ):
                self.model(symbol, source)
                return ModelNode(_symbol_key(symbol))
            case "alias" if symbol.id not in self.aliases:
                self.aliases.add(symbol.id)
                node = next(
                    (
                        self.node(facts.type, member.schema, source)
                        for member in self.members.get(symbol.id, [])
                        if (facts := member.model_facts) is not None
                    ),
                    LeafNode(),
                )
                self.aliases.discard(symbol.id)
                return node
            case "alias" if self.structural and not self.quiet:
                self.report(
                    "MC_ADAPTER_REQUIRED",
                    source,
                    f"The recursive {symbol.name} alias has no structural conversion; register a model adapter",
                )
                return LeafNode()
            case "enum" | "alias":
                return LeafNode()
            case _:
                self.report("MC_ADAPTER_REQUIRED", source, f"The {symbol.name} model needs a model adapter")
                return LeafNode()

    @property
    def models(self) -> dict[str, ModelBinding]:
        return self.caches[self.quiet]

    def model(self, symbol: FinalModelSymbol, source: SourceLocation) -> None:
        if (key := _symbol_key(symbol)) in self.models or (self.quiet, key) in self.building:
            return
        self.building.add((self.quiet, key))
        members = self.members.get(symbol.id, [])
        schema_id = self.symbol_schemas.get(symbol.id)
        if symbol.kind == "root":
            self.models[key] = ModelBinding(
                symbol=key,
                native_kind="root",
                schema_id=schema_id,
                root=next(
                    (
                        self.node(facts.type, member.schema, source)
                        for member in members
                        if (facts := member.model_facts) is not None
                    ),
                    LeafNode(),
                ),
            )
            return
        generated = _setting(symbol, "alias_generator") is not None
        record = symbol.backend == "typeddict"
        planned = [
            (
                _Member(facts, slot, wire_name, member.schema or source),
                frozenset({wire_name if record and _functional(self.symbols[slot.symbol]) else slot.name})
                if self.structural
                else _accepted(facts, slot, wire_name, generated=generated),
            )
            for member in members
            if (facts := member.model_facts) is not None
            and (slot := member.slot) is not None
            and slot.name != _TYPED_EXTRAS
            and member.exclusion != "tag"
            and (wire_name := member.wire_name) is not None
        ]
        fields = tuple(
            self.field(
                key,
                member,
                accepted,
                total=_setting(self.symbols[member.slot.symbol], "total", parameter=True) is not False
                if record
                else None,
            )
            for member, accepted in planned
        )
        readers: dict[str, list[str]] = {}
        for field, (_, accepted) in zip(fields, planned, strict=True):
            for accepted_key in accepted:
                readers.setdefault(accepted_key, []).append(field.native_name)
        for field, (member, _) in zip(fields, planned, strict=True):
            if other := next((name for name in readers[field.validation_key] if name != field.native_name), None):
                self.report(
                    "MC_ALIAS_COLLISION",
                    member.schema,
                    f"The {field.wire_name} property of {symbol.name} is also read by the {other} field",
                )
        extra_items = (
            self.node(items, self.child(self.locations.get(schema_id or ""), "additionalProperties"), source)
            if symbol.facts is not None and (items := symbol.facts.extra_items) is not None
            else None
        )
        self.models[key] = ModelBinding(
            symbol=key,
            native_kind=_NATIVE_KINDS[symbol.backend],
            schema_id=schema_id,
            fields=fields,
            extra="forbid"
            if (record and _setting(symbol, "closed", parameter=True) is True)
            or _setting(symbol, "forbid_unknown_fields", parameter=True) is True
            else "allow"
            if extra_items is not None
            else _EXTRA_POLICIES.get(_setting(symbol, "extra"), "ignore"),
            open=self.open(schema_id),
            extra_items=extra_items,
            tag=_struct_tag(symbol),
        )

    def open(self, schema_id: str | None) -> bool:
        if schema_id is None or (location := self.locations.get(schema_id)) is None:
            return True
        schema = self.wire.schema(location)[1]
        return schema.get("additionalProperties") is not False and schema.get("unevaluatedProperties") is not False

    def field(self, key: str, member: _Member, accepted: frozenset[str], *, total: bool | None) -> FieldBinding:
        """Bind one field; ``total`` is the totality of the TypedDict that declares it, and None for other models."""
        facts, name, wire_name = member.facts, member.slot.name, member.wire_name
        provenance = facts.none_default_provenance
        constructible = not _known_false(facts.backend.constructor_init)
        qualifiers = facts.backend.emitted.qualifiers
        match total:
            case _ if not self.structural:
                required = facts.required and not facts.has_default and not facts.explicit_default_factory
            case None:
                required = constructible and provenance.emitted_default == "absent"
            case _:
                required = "Required" in qualifiers or (total and "NotRequired" not in qualifiers)
        if required and not facts.required:
            self.gaps.add(f"{key}.{name}")
        return FieldBinding(
            field_id=f"{key}.{name}",
            native_name=min(accepted) if self.structural else name,
            wire_name=wire_name,
            validation_key=wire_name if wire_name in accepted else min(accepted),
            validation_keys=tuple(sorted(accepted)),
            required=required,
            read_only=facts.read_only,
            write_only=facts.write_only,
            omit_none=(provenance.emitted_default, provenance.origin, provenance.annotation_null_origin)
            == _OPTIONAL_FALLBACK,
            type=self.node(facts.type, member.schema, member.schema),
            constructible=constructible,
        )

    def reachable(self, node: TypeNode) -> tuple[ModelBinding, ...]:
        pending, found = list(_models(node)), dict[str, ModelBinding]()
        while pending:
            if (symbol := pending.pop()) in found:
                continue
            model = found[symbol] = self.models[symbol]
            if model.root is not None:
                pending.extend(_models(model.root))
            if model.extra_items is not None:
                pending.extend(_models(model.extra_items))
            pending.extend(symbol for field in model.fields for symbol in _models(field.type))
        return tuple(found[symbol] for symbol in sorted(found))

    def use(self, use: TypeUseBinding) -> UseBinding | None:
        if (direction := use.id.direction) == "neutral" or use.schema is None or use.id not in self.schema_ids:
            return None
        source = use.id.use_site
        if use.state != "bound" or use.type is None:
            self.report(
                "BND_MODEL_SCOPE_REQUIRED" if use.state == "not_generated" else use.reason or "MC_BINDING_MISSING",
                source,
                "The use has no generated native type",
            )
            return None
        if not self.spelled(use, use.type):
            return None
        self.quiet = use.id in self.adapted
        node = self.node(use.type, use.schema, source)
        models = self.reachable(node)
        self.quiet = False
        excluded = "read_only" if direction == "request" else "write_only"
        envelope = any(
            (field.required and (getattr(field, excluded) or field.field_id in self.gaps))
            or (not field.constructible and not getattr(field, excluded))
            for model in models
            for field in model.fields
        )
        return UseBinding(
            binding_id=f"{self.wire.schema_id(self.wire.schema(use.schema)[0])}|{node!r}",
            direction=direction,
            schema_id=self.schema_ids[use.id],
            operation_id=use.id.owner.use_site.pointer if isinstance(use.id.owner, OperationId) else None,
            media_type=use.id.media,
            backend=self.backend,
            native_kind=self.native_kind(use.type, node, models),
            native_export=_symbol_key(self.symbols[use.type.symbol])
            if isinstance(use.type, GeneratedSymbolType)
            else None,
            projection_mode="envelope" if envelope else "native",
            converter_strategy="registered_adapter" if use.id in self.adapted else self.strategy(use.type),
            type=node,
            models=models,
        )

    def strategy(self, value: FinalPythonType) -> ConverterStrategy:
        """Return the use's converter; a msgspec type that msgspec.convert refuses takes the structural converter."""
        if (strategy := _STRATEGIES[self.backend]) == "msgspec_convert" and not self.msgspec.convertible(value, set()):
            return "msgspec_structural"
        return strategy

    def native_kind(self, value: FinalPythonType, node: TypeNode, models: tuple[ModelBinding, ...]) -> NativeKind:
        match value, node:
            case GeneratedSymbolType(symbol=symbol), _ if (kind := self.symbols[symbol].kind) in {"alias", "enum"}:
                return "alias" if kind == "alias" else "enum"
            case _, ModelNode(symbol=symbol):
                return next(model.native_kind for model in models if model.symbol == symbol)
            case _, UnionNode(members=members) if len(members) > 1:
                return "union"
            case _, ArrayNode() | TupleNode():
                return "array"
            case _, MapNode():
                return "map"
            case _:
                return "scalar"


def _aliased(planner: _CodecPlanner, symbol: SymbolId) -> FinalPythonType | None:
    """Return the final type an alias names, or None for a symbol that is no alias."""
    if planner.symbols[symbol].kind != "alias":
        return None
    return next(
        (facts.type for member in planner.members.get(symbol, []) if (facts := member.model_facts) is not None),
        None,
    )


def _union_members(
    planner: _CodecPlanner, members: tuple[FinalPythonType, ...], seen: frozenset[SymbolId]
) -> Iterator[FinalPythonType]:
    """Yield a union's members with the members of aliased unions in place of the aliases, as Python does.

    ``seen`` holds the aliases expanded on the way to these members only, so a recursive alias stops while another
    member that names an alias already expanded elsewhere is expanded as well.
    """
    for member in members:
        if (
            isinstance(member, GeneratedSymbolType)
            and member.symbol not in seen
            and isinstance(aliased := _aliased(planner, member.symbol), UnionType)
        ):
            yield from _union_members(planner, aliased.members, seen | {member.symbol})
        else:
            yield member


class _MsgspecTypes:
    """Decide at generation time whether msgspec.convert reads a final type, as msgspec itself decides at runtime."""

    def __init__(self, planner: _CodecPlanner) -> None:
        self.planner = planner

    def convertible(self, value: FinalPythonType, seen: set[int]) -> bool:  # noqa: PLR0911
        """Return whether msgspec.convert reads a final type, which no refused union, leaf, enum or pattern reaches."""
        symbols, members = self.planner.symbols, self.planner.members
        match value:
            case GeneratedSymbolType() if value.symbol in seen:
                return True
            case GeneratedSymbolType() if symbols[value.symbol].kind == "enum":
                return self.enum_kinds(value.symbol) in _MSGSPEC_ENUMS
            case GeneratedSymbolType():
                seen.add(value.symbol)
                return _setting(symbols[value.symbol], "array_like", parameter=True) is not True and all(
                    not _meta_pattern(facts) and _encoded_as_wire(member, facts) and self.convertible(facts.type, seen)
                    for member in members.get(value.symbol, [])
                    if (facts := member.model_facts) is not None
                )
            case UnionType(members=items):
                kinds = self.kinds(items)
                return (
                    all(kinds.count(kind) <= 1 for kind in _MSGSPEC_KINDS)
                    and not ("tagged" in kinds and "object" in kinds)
                    and all(self.convertible(item, seen) for item in items)
                )
            case GenericType(arguments=arguments):
                return all(self.convertible(argument, seen) for argument in arguments)
            case LiteralType():
                return _literal_kind(value) is not None
            case _:
                pass
        return _name(value) not in _MSGSPEC_UNSUPPORTED

    def flattened(self, items: tuple[FinalPythonType, ...], seen: frozenset[int]) -> Iterator[FinalPythonType]:
        """Yield union members with the members of aliased types in place of their aliases, as msgspec reads them."""
        for item in items:
            if (
                isinstance(item, GeneratedSymbolType)
                and item.symbol not in seen
                and (aliased := _aliased(self.planner, item.symbol)) is not None
            ):
                members = aliased.members if isinstance(aliased, UnionType) else (aliased,)
                yield from self.flattened(members, seen | {item.symbol})
            else:
                yield item

    def kinds(self, items: tuple[FinalPythonType, ...]) -> list[str]:
        """Return the msgspec categories of a union's members; literals merge with each other and a plain str or int."""
        flat = tuple(dict.fromkeys(self.flattened(items, frozenset())))
        names = {_name(item) for item in flat}
        literals = {
            kind
            for item in flat
            if isinstance(item, LiteralType) and (kind := _literal_kind(item)) is not None and kind not in names
        }
        return [
            *(kind for item in flat if not isinstance(item, LiteralType) and (kind := self.kind(item)) is not None),
            *literals,
        ]

    def kind(self, value: FinalPythonType) -> str | None:
        """Return the msgspec union category of a member, or None for a type any union may hold."""
        match value:
            case GeneratedSymbolType() if self.planner.symbols[value.symbol].kind == "enum":
                kinds = self.enum_kinds(value.symbol)
                return next(iter(kinds)) if len(kinds) == 1 else None
            case GeneratedSymbolType():
                return "tagged" if _struct_tag(self.planner.symbols[value.symbol]) is not None else "object"
            case GenericType(base=base):
                return "object" if _name(base) in _MAPPINGS else "array"
            case BuiltinType(name="int"):
                return "int"
            case _:
                pass
        return "str" if _name(value) in _MSGSPEC_STRINGS else None

    @cached_property
    def enums(self) -> dict[int, frozenset[str]]:
        """Return the JSON kinds of each enum's values, read from the first schema it was generated from the wire holds.

        An enum is generated from every schema it replaces, and a schema the wire never reaches is not held.
        """
        planner = self.planner
        kinds: dict[int, frozenset[str]] = {}
        for use in planner.batch.type_uses:
            if (
                use.id.role == "schema"
                and isinstance(value := use.type, GeneratedSymbolType)
                and value.symbol not in kinds
                and planner.symbols[value.symbol].kind == "enum"
                and isinstance(values := planner.wire.schema(use.schema or use.id.schema_site)[1].get("enum"), tuple)
            ):
                kinds[value.symbol] = frozenset(
                    "int" if type(item) is int else "str" if type(item) is str else "other"
                    for item in values
                    if item is not None
                )
        return kinds

    def enum_kinds(self, symbol: int) -> frozenset[str]:
        """Return the JSON kinds of an enum's values; one that no schema the wire holds declares has none."""
        return self.enums.get(symbol, frozenset())


def plan_model_codecs(  # noqa: PLR0913
    batch: GeneratedTypeContractBatch,
    wire: WirePlan,
    backend: CodecBackend,
    *,
    declarations: CodecDeclarations = _NO_DECLARATIONS,
    lease: SourceLease | None = None,
    selection: AdapterSelection | None = None,
) -> CodecPlan:
    """Bind every directional use to its native type graph, projection mode, and any registered adapter.

    A target that already selected the adapters of the same batch passes its `selection`.
    """
    if selection is None:
        selection = select_adapters(batch, wire, declarations)
    planner = _CodecPlanner(batch, wire, backend, selection.uses("model"))
    bindings = tuple((use.id, binding) for use in batch.type_uses if (binding := planner.use(use)) is not None)
    adapters, adapter_diagnostics = plan_adapters(selection, batch, wire, dict(bindings), lease)
    return CodecPlan(
        bindings,
        (
            *suppressed(wire.diagnostics, wire, adapters, batch),
            *selection.diagnostics,
            *planner.diagnostics,
            *adapter_diagnostics,
        ),
        adapters,
        tuple(planner.imports.items()),
    )
