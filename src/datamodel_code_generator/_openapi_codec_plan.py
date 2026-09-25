"""Plan Pydantic v2 model codec bindings for the directional type uses of an accepted batch."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from datamodel_code_generator._codec_declarations import (
    BuiltinCodecCompatibility,
    CodecDeclarations,
    ModelExportBinding,
    SchemaRef,
)
from datamodel_code_generator._codec_type_source import type_reason
from datamodel_code_generator._generation_contract import (
    AnnotatedType,
    BindingCaptureError,
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
    LiteralScalar,
    ModelArtifactAddress,
    ModelFieldFacts,
    NoneType,
    OperationId,
    SourceLocation,
    TypeUseBinding,
    TypeUseId,
    UnionType,
)
from datamodel_code_generator._openapi_codec_adapters import (
    AdapterPlan,
    AdapterSelection,
    declared_document,
    plan_adapters,
    select_adapters,
    suppressed,
    type_nodes,
)
from datamodel_code_generator._openapi_wire_plan import CodecDiagnostic, CodecReason, WirePlan
from datamodel_code_generator._runtime.model_codecs.bindings import (
    ArrayNode,
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
from datamodel_code_generator.model.binding import (
    FrozenImportBindings,
    KnownBackendValue,
    OpaqueBackendValue,
    index_builtin_field_declarations,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from datamodel_code_generator._openapi_generation import SourceLease
    from datamodel_code_generator._runtime.model_codecs.context import Surface

PydanticBackend: TypeAlias = Literal["pydantic_v2.BaseModel", "pydantic_v2.dataclass"]
ArrayKind: TypeAlias = Literal["list", "set", "frozenset"]

_SYMBOL_BACKENDS: Final[dict[PydanticBackend, str]] = {
    "pydantic_v2.BaseModel": "pydantic",
    "pydantic_v2.dataclass": "pydantic_dataclass",
}
_ARRAY_KINDS: Final[dict[str, ArrayKind]] = {"set": "set", "frozenset": "frozenset"}
_OPTIONAL_FALLBACK: Final = ("none", "synthesized_optional_fallback", "optional_fallback")
_EXTRA_POLICIES: Final[dict[object, ExtraPolicy]] = {"ignore": "ignore", "allow": "allow", "forbid": "forbid"}
_NO_DECLARATIONS: Final = CodecDeclarations()


@dataclass(frozen=True, slots=True)
class CodecPlan:
    """Keep every planned directional use binding, the selected adapters, and every blocking diagnostic."""

    bindings: tuple[tuple[TypeUseId, UseBinding], ...]
    diagnostics: tuple[CodecDiagnostic, ...]
    adapters: tuple[AdapterPlan, ...] = ()
    imports: tuple[tuple[int, str], ...] = ()


def _setting(symbol: FinalModelSymbol, name: str) -> object:
    for setting in symbol.facts.configuration if symbol.facts else ():
        if setting.name == name and setting.present:
            match setting.value:
                case KnownBackendValue(value=LiteralScalar(value=value)):
                    return value
                case value:
                    return value
    return None


def _builtin(symbol: FinalModelSymbol) -> bool:
    return (
        symbol.facts is not None
        and not symbol.facts.custom_base
        and not any(
            isinstance(setting.value, OpaqueBackendValue) and setting.value.reason == "custom_origin"
            for setting in (*symbol.facts.parameters, *symbol.facts.configuration)
        )
    )


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


def _module(artifact: ModelArtifactAddress) -> str:
    *parents, name = artifact.relative_path
    modules = () if artifact.result_key == "single" else (*parents, name.removesuffix(".py"))
    return ".".join((artifact.model_package, *modules)).removesuffix(".__init__")


def _symbol_key(symbol: FinalModelSymbol) -> str:
    return f"{_module(symbol.artifact) if symbol.artifact else ''}:{symbol.name}"


def _defined(source: str) -> frozenset[str]:
    try:
        index = index_builtin_field_declarations(source, expected=(), imports=FrozenImportBindings(()))
    except BindingCaptureError:
        return frozenset()
    return frozenset(definition.name for definition in index.definitions)


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
        backend: PydanticBackend,
        declarations: CodecDeclarations,
        adapted: frozenset[TypeUseId],
    ) -> None:
        self.batch = batch
        self.wire = wire
        self.backend: PydanticBackend = backend
        self.adapted = adapted
        self.quiet = False
        self.exports: dict[int, str] = {}
        self.imports: dict[int, str] = {}
        self.symbols = {symbol.id: symbol for symbol in batch.symbols}
        self.schema_ids = dict(wire.schema_ids)
        self.locations: dict[str, SourceLocation] = {}
        self.symbol_schemas: dict[int, str] = {}
        uses = {use.id: use for use in batch.type_uses}
        for use_id, schema_id in wire.schema_ids:
            use = uses[use_id]
            self.locations.setdefault(schema_id, use.schema or use_id.schema_site)
            if use_id.role == "schema" and isinstance(use.type, GeneratedSymbolType):
                self.symbol_schemas.setdefault(use.type.symbol, schema_id)
        self.members: dict[int, list[FieldUseBinding]] = {}
        for member in batch.fields:
            self.members.setdefault(member.consumer, []).append(member)
        self.caches: dict[bool, dict[str, ModelBinding]] = {False: {}, True: {}}
        self.building: set[tuple[bool, str]] = set()
        self.aliases: set[int] = set()
        self.diagnostics: list[CodecDiagnostic] = []
        self.compatible = frozenset(
            symbol
            for declaration in declarations.compatibility
            if declaration.backend.value == backend
            for symbol in self.declared(declaration)
        )

    def declared(self, declaration: BuiltinCodecCompatibility) -> Iterable[int]:
        if not declaration.schemas:
            return self.symbols
        referenced = self.referenced(declaration.schemas)
        return (symbol for symbol, schema_id in self.symbol_schemas.items() if schema_id in referenced)

    def referenced(self, references: tuple[SchemaRef, ...]) -> frozenset[str]:
        return frozenset(
            self.wire.schema_id(SourceLocation(document, reference.pointer, "schema"))
            for reference in references
            if (document := declared_document(self.batch, reference.document)) is not None
        )

    def export(self, binding: ModelExportBinding, sources: Mapping[str, str]) -> None:
        document = declared_document(self.batch, binding.schema.document)
        symbols = {
            use.type.symbol
            for use in self.batch.type_uses
            if isinstance(use.type, GeneratedSymbolType)
            and use.schema is not None
            and ((resolved := self.wire.schema(use.schema)[0]).document, resolved.pointer)
            == (document, binding.schema.pointer)
            and (use.id.role == "schema" if binding.direction == "neutral" else use.id.direction == binding.direction)
        }
        source = SourceLocation(document or self.batch.documents[0].id, binding.schema.pointer, "schema")
        if len(symbols) != 1:
            code: CodecReason = "MC_ADAPTER_CONTRACT" if symbols else "BND_MODEL_SCOPE_REQUIRED"
            self.report(code, source, "The export binding selects no single generated model variant")
            return
        symbol = self.symbols[next(iter(symbols))]
        package = symbol.artifact.model_package if symbol.artifact else ""
        if binding.symbol not in _defined(sources.get(binding.module, "")) or not (
            binding.module == package or binding.module.startswith(f"{package}.")
        ):
            self.report(
                "BND_SYMBOL_NOT_EMITTED", source, f"{binding.module} does not define or export {binding.symbol}"
            )
            return
        self.exports[symbol.id] = f"{binding.module}:{binding.symbol}"

    def report(self, code: CodecReason, source: SourceLocation, message: str) -> None:
        if (diagnostic := CodecDiagnostic(code, source, message)) not in self.diagnostics:
            self.diagnostics.append(diagnostic)

    def locate(self) -> None:
        self.imports = {
            symbol.id: _symbol_key(symbol) for symbol in self.batch.symbols if symbol.artifact is not None
        } | self.exports

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
                present = [member for member in members if not isinstance(member, NoneType)]
                return UnionNode(
                    tuple(self.node(member, schema, source) for member in present), len(present) != len(members)
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
            case GenericType(arguments=(_, item)):
                return MapNode(self.node(item, self.child(schema, "additionalProperties"), source))
            case _:
                return LeafNode(self.representation(value, schema))

    def symbol_node(self, symbol: FinalModelSymbol, source: SourceLocation) -> TypeNode:
        match symbol.kind:
            case "model" | "root" | "custom" if self.quiet or (
                symbol.kind != "custom"
                and symbol.backend == _SYMBOL_BACKENDS[self.backend]
                and (_builtin(symbol) or (symbol.facts is not None and symbol.id in self.compatible))
            ):
                self.model(symbol, source)
                return ModelNode(_symbol_key(symbol))
            case "model" | "root" if symbol.id in self.compatible and symbol.backend == _SYMBOL_BACKENDS[self.backend]:
                self.report(
                    "MC_ADAPTER_REQUIRED",
                    source,
                    f"The {symbol.name} model has no captured field facts, so its builtin compatibility cannot bind it",
                )
                return LeafNode()
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
            case "enum" | "alias":
                return LeafNode()
            case _:
                self.report(
                    "MC_ADAPTER_REQUIRED",
                    source,
                    f"The {symbol.name} model needs a builtin compatibility declaration or a model adapter",
                )
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
        planned = [
            (
                _Member(facts, slot, wire_name, member.schema or source),
                _accepted(facts, slot, wire_name, generated=generated),
            )
            for member in members
            if (facts := member.model_facts) is not None
            and (slot := member.slot) is not None
            and (wire_name := member.wire_name) is not None
        ]
        fields = tuple(self.field(key, member, accepted) for member, accepted in planned)
        readers: dict[str, list[str]] = {}
        for member, accepted in planned:
            for accepted_key in accepted:
                readers.setdefault(accepted_key, []).append(member.slot.name)
        for field, (member, _) in zip(fields, planned, strict=True):
            if other := next((name for name in readers[field.validation_key] if name != field.native_name), None):
                self.report(
                    "MC_ALIAS_COLLISION",
                    member.schema,
                    f"The {field.wire_name} property of {symbol.name} is also read by the {other} field",
                )
        self.models[key] = ModelBinding(
            symbol=key,
            native_kind="dataclass" if symbol.backend == "pydantic_dataclass" else "model",
            schema_id=schema_id,
            fields=fields,
            extra=_EXTRA_POLICIES.get(_setting(symbol, "extra"), "ignore"),
            open=self.open(schema_id),
        )

    def open(self, schema_id: str | None) -> bool:
        if schema_id is None or (location := self.locations.get(schema_id)) is None:
            return True
        schema = self.wire.schema(location)[1]
        return schema.get("additionalProperties") is not False and schema.get("unevaluatedProperties") is not False

    def field(self, key: str, member: _Member, accepted: frozenset[str]) -> FieldBinding:
        facts, name, wire_name = member.facts, member.slot.name, member.wire_name
        provenance = facts.none_default_provenance
        return FieldBinding(
            field_id=f"{key}.{name}",
            native_name=name,
            wire_name=wire_name,
            validation_key=wire_name if wire_name in accepted else min(accepted),
            validation_keys=tuple(sorted(accepted)),
            required=facts.required and not facts.has_default and not facts.explicit_default_factory,
            read_only=facts.read_only,
            write_only=facts.write_only,
            omit_none=(provenance.emitted_default, provenance.origin, provenance.annotation_null_origin)
            == _OPTIONAL_FALLBACK,
            type=self.node(facts.type, member.schema, member.schema),
        )

    def reachable(self, node: TypeNode) -> tuple[ModelBinding, ...]:
        pending, found = list(_models(node)), dict[str, ModelBinding]()
        while pending:
            if (symbol := pending.pop()) in found:
                continue
            model = found[symbol] = self.models[symbol]
            if model.root is not None:
                pending.extend(_models(model.root))
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
        envelope = any(field.required and getattr(field, excluded) for model in models for field in model.fields)
        return UseBinding(
            binding_id=f"{self.wire.schema_id(self.wire.schema(use.schema)[0])}|{node!r}",
            direction=direction,
            schema_id=self.schema_ids[use.id],
            operation_id=use.id.owner.use_site.pointer if isinstance(use.id.owner, OperationId) else None,
            media_type=use.id.media,
            backend=self.backend,
            native_kind=self.native_kind(use.type, node, models),
            native_export=self.exports.get(use.type.symbol, _symbol_key(self.symbols[use.type.symbol]))
            if isinstance(use.type, GeneratedSymbolType)
            else None,
            projection_mode="envelope" if envelope else "native",
            converter_strategy="registered_adapter" if use.id in self.adapted else "pydantic_type_adapter",
            type=node,
            models=models,
        )

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


def plan_model_codecs(  # noqa: PLR0913
    batch: GeneratedTypeContractBatch,
    wire: WirePlan,
    backend: PydanticBackend,
    *,
    declarations: CodecDeclarations = _NO_DECLARATIONS,
    surface: Surface = "server",
    lease: SourceLease | None = None,
    sources: Mapping[str, str] | None = None,
) -> CodecPlan:
    """Bind every directional use to its native type graph, projection mode, and any registered adapter."""
    selection: AdapterSelection = select_adapters(batch, wire, declarations, surface)
    planner = _CodecPlanner(batch, wire, backend, declarations, selection.uses("model"))
    for export in declarations.exports:
        planner.export(export, sources or {})
    planner.locate()
    bindings = tuple((use.id, binding) for use in batch.type_uses if (binding := planner.use(use)) is not None)
    adapters, adapter_diagnostics = plan_adapters(selection, batch, wire, dict(bindings), lease)
    return CodecPlan(
        bindings,
        (
            *suppressed(wire.diagnostics, wire, adapters),
            *selection.diagnostics,
            *planner.diagnostics,
            *adapter_diagnostics,
        ),
        adapters,
        tuple(planner.imports.items()),
    )
