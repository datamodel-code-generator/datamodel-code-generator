"""Plan registered codec adapters for one accepted batch: selection, capability coverage, and data-only views."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from typing import TYPE_CHECKING, Final, Literal, TypeAlias
from urllib.parse import unquote, urldefrag

from datamodel_code_generator._codec_declarations import (
    CodecAdapterRegistration,
    CodecDeclarations,
    OperationRef,
    RegistrationCapabilities,
    SchemaDirectionalUse,
    SchemaRef,
    TypeUseRef,
)
from datamodel_code_generator._generation_contract import (
    GeneratedSymbolType,
    OperationId,
    SourceDocumentId,
    SourceLocation,
)
from datamodel_code_generator._openapi_wire_plan import (
    LOGICAL_ROOT,
    CodecDiagnostic,
    CodecReason,
    ParameterViewPlan,
    parameter_view,
)
from datamodel_code_generator._runtime.model_codecs.bindings import (
    ArrayNode,
    MapNode,
    ModelNode,
    TupleNode,
    TypeNode,
    UnionNode,
)
from datamodel_code_generator._runtime.model_codecs.capabilities import (
    ClientMediaCodecCapabilities,
    CodecCapabilities,
    ParameterCodecCapabilities,
    SchemaCodecCapabilities,
    ServerMediaCodecCapabilities,
)
from datamodel_code_generator._runtime.model_codecs.registry import SchemaSource
from datamodel_code_generator._runtime.model_codecs.schema import (
    SCHEMA_ARRAY_KEYWORDS,
    SCHEMA_MAP_KEYWORDS,
    SCHEMA_VALUE_KEYWORDS,
)
from datamodel_code_generator._runtime.model_codecs.views import (
    CodecFieldView,
    CodecSourceRef,
    CodecUseView,
    ModelExportView,
)
from datamodel_code_generator._runtime.model_codecs.wire import JSONValue, WireValue, escape_pointer_token, freeze_wire

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from datamodel_code_generator._generation_contract import (
        FieldUseBinding,
        FinalPythonType,
        GeneratedTypeContractBatch,
        OperationContract,
        TypeUseBinding,
        TypeUseId,
        WireDeclaration,
    )
    from datamodel_code_generator._openapi_generation import SourceLease
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.bindings import (
        BackendId,
        ConverterStrategy,
        NativeKind,
        ProjectionMode,
        UseBinding,
    )
    from datamodel_code_generator._runtime.model_codecs.context import Direction, Surface
    from datamodel_code_generator._source import YamlValue

Priority: TypeAlias = Literal[0, 1]

DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ADAPTER_CODES: Final = frozenset({"MC_SCHEMA_DIALECT", "MC_PATTERN_DIALECT", "MC_PATTERN_RESOURCE_LIMIT"})
_KEYWORDS: Final = frozenset({
    "$dynamicRef",
    "$ref",
    "additionalProperties",
    "allOf",
    "anyOf",
    "const",
    "contains",
    "dependentRequired",
    "dependentSchemas",
    "else",
    "enum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "if",
    "items",
    "maxContains",
    "maxItems",
    "maxLength",
    "maxProperties",
    "maximum",
    "minContains",
    "minItems",
    "minLength",
    "minProperties",
    "minimum",
    "multipleOf",
    "not",
    "oneOf",
    "pattern",
    "patternProperties",
    "prefixItems",
    "properties",
    "propertyNames",
    "required",
    "then",
    "type",
    "unevaluatedItems",
    "unevaluatedProperties",
    "uniqueItems",
})
_GROUPED: Final = frozenset({"$ref", "allOf"})
_DEFINITIONS: Final = frozenset({"$defs", "definitions"})
_ALL_KINDS: Final = ("null", "boolean", "integer", "number", "string", "array", "object")
_EXACT: Final[Priority] = 0
_SCHEMA_DEFAULT: Final[Priority] = 1


@dataclass(frozen=True, slots=True)
class BindingViewPlan:
    """A CodecBindingView whose SchemaView the runtime reads from the offline registry of its direction."""

    binding_id: str
    use: CodecUseView
    source: SchemaSource
    backend: BackendId | None
    native_kind: NativeKind | None
    native_export: ModelExportView | None
    fields: tuple[CodecFieldView, ...]
    projection_mode: ProjectionMode
    converter_strategy: ConverterStrategy | None


@dataclass(frozen=True, slots=True)
class SchemaAdapterPlan:
    """The SchemaPlanView data of one schema adapter use, and the patterns its closure uses."""

    source: SchemaSource
    direction: Direction
    required_vocabularies: tuple[str, ...]
    required_keywords: tuple[str, ...]
    pattern_dialects: tuple[str, ...]
    patterns: frozenset[str]
    excluded: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AdapterPlan:
    """One adapter selected for one use, with the data-only views its runtime wrapper receives."""

    registration: CodecAdapterRegistration
    use: TypeUseId
    binding: BindingViewPlan
    parameter: ParameterViewPlan | None = None
    schema: SchemaAdapterPlan | None = None


@dataclass(frozen=True, slots=True)
class AdapterSelection:
    """The adapter each (kind, use) selects for one surface, and the diagnostics of selecting them."""

    chosen: Mapping[tuple[str, TypeUseId], CodecAdapterRegistration]
    diagnostics: tuple[CodecDiagnostic, ...]

    def uses(self, kind: str) -> frozenset[TypeUseId]:
        """Return the uses an adapter of one kind was selected for."""
        return frozenset(use for chosen_kind, use in self.chosen if chosen_kind == kind)


@dataclass(slots=True)
class _Closure:
    documents: Mapping[str, WireValue]
    skipped: frozenset[str] = frozenset()
    visited: set[tuple[str, str]] = field(default_factory=set[tuple[str, str]])
    keywords: set[str] = field(default_factory=set[str])
    patterns: set[str] = field(default_factory=set[str])


def _resolve(documents: Mapping[str, WireValue], uri: str, pointer: str) -> WireValue:
    value = documents.get(uri)
    for token in (part.replace("~1", "/").replace("~0", "~") for part in pointer.split("/")[1:]):
        value = (
            value.get(token)
            if isinstance(value, Mapping)
            else value[int(token)]
            if isinstance(value, tuple) and token.isdigit() and int(token) < len(value)
            else None
        )
    return value


def _children(uri: str, pointer: str, schema: Mapping[str, WireValue]) -> Iterator[tuple[str, tuple[str, str]]]:
    if isinstance(reference := schema.get("$ref"), str):
        target, fragment = urldefrag(reference)
        yield "$ref", (target or uri, unquote(fragment))
    for keyword, value in schema.items():
        at = f"{pointer}/{escape_pointer_token(keyword)}"
        match value:
            case Mapping() if keyword in SCHEMA_MAP_KEYWORDS:
                yield from ((keyword, (uri, f"{at}/{escape_pointer_token(name)}")) for name in value)
            case tuple() if keyword in SCHEMA_ARRAY_KEYWORDS:
                yield from ((keyword, (uri, f"{at}/{index}")) for index in range(len(value)))
            case _ if keyword in SCHEMA_VALUE_KEYWORDS:
                yield keyword, (uri, at)
            case _:
                continue


def _walk(closure: _Closure, uri: str, pointer: str) -> None:
    if (uri, pointer) in closure.visited or not isinstance(
        schema := _resolve(closure.documents, uri, pointer), Mapping
    ):
        return
    closure.visited.add((uri, pointer))
    closure.keywords.update(key for key in schema if key in _KEYWORDS)
    if isinstance(pattern := schema.get("pattern"), str):
        closure.patterns.add(pattern)
    if isinstance(patterns := schema.get("patternProperties"), Mapping):
        closure.patterns.update(patterns)
    for keyword, at in _children(uri, pointer, schema):
        if keyword not in closure.skipped:
            _walk(closure, *at)


@dataclass(frozen=True, slots=True)
class SchemaClosure:
    """The reachable normalized schema objects of one use, with their keywords and patterns."""

    locations: frozenset[tuple[str, str]]
    keywords: tuple[str, ...]
    patterns: frozenset[str]

    def contains(self, uri: str, pointer: str) -> bool:
        """Return whether a schema or keyword location lies inside a reachable schema object."""
        parts = pointer.split("/")
        return any((uri, "/".join(parts[:length])) in self.locations for length in range(len(parts), 0, -1))


def _group(
    documents: Mapping[str, WireValue], location: tuple[str, str]
) -> dict[tuple[str, str], Mapping[str, WireValue]]:
    group: dict[tuple[str, str], Mapping[str, WireValue]] = {}
    pending = [location]
    while pending:
        if (current := pending.pop()) in group or not isinstance(schema := _resolve(documents, *current), Mapping):
            continue
        group[current] = schema
        pending.extend(at for keyword, at in _children(*current, schema) if keyword in _GROUPED)
    return group


def _beneath(
    group: Mapping[tuple[str, str], Mapping[str, WireValue]], owned: frozenset[str]
) -> Iterator[tuple[str, str]]:
    return (
        at
        for (uri, pointer), schema in group.items()
        for keyword, at in _children(uri, pointer, schema)
        if keyword not in owned and keyword not in _DEFINITIONS
    )


def _reached(
    documents: Mapping[str, WireValue],
    root: Mapping[tuple[str, str], Mapping[str, WireValue]],
    members: Iterable[Mapping[tuple[str, str], Mapping[str, WireValue]]],
) -> set[tuple[str, str]]:
    walk = _Closure(documents, skipped=_DEFINITIONS)
    for at in (
        *_beneath(root, _GROUPED | {"properties"}),
        *(at for group in members for at in _beneath(group, _GROUPED)),
    ):
        _walk(walk, *at)
    return walk.visited


def schema_closure(documents: Mapping[str, WireValue], schema_id: str) -> SchemaClosure:
    """Return every normalized schema object reachable from one bundled schema identifier."""
    walk = _Closure(documents)
    uri, fragment = urldefrag(schema_id)
    _walk(walk, uri, unquote(fragment))
    return SchemaClosure(frozenset(walk.visited), tuple(sorted(walk.keywords)), frozenset(walk.patterns))


def type_nodes(node: TypeNode) -> Iterator[TypeNode]:
    """Yield a type graph node and every node beneath it, stopping at model references."""
    yield node
    match node:
        case ArrayNode():
            yield from type_nodes(node.item)
        case MapNode():
            yield from type_nodes(node.value)
        case TupleNode():
            for item in node.items:
                yield from type_nodes(item)
        case UnionNode():
            for item in node.members:
                yield from type_nodes(item)
        case _:
            return


def _model_unions(binding: UseBinding) -> bool:
    roots = (
        binding.type,
        *(model.root for model in binding.models if model.root is not None),
        *(field.type for model in binding.models for field in model.fields),
    )
    return any(
        isinstance(node, UnionNode) and any(isinstance(member, ModelNode) for member in node.members)
        for root in roots
        for node in type_nodes(root)
    )


def _raw(value: YamlValue) -> JSONValue:
    match value:
        case dict():
            return {str(key): _raw(item) for key, item in value.items()}
        case list():
            return [_raw(item) for item in value]
        case float() if not isfinite(value):
            return None
        case _:
            return value


def _kinds(schema: WireValue) -> tuple[str, ...]:
    match schema:
        case Mapping() if isinstance(kinds := schema.get("type"), str):
            return (kinds,)
        case Mapping() if isinstance(kinds := schema.get("type"), tuple):
            return tuple(str(kind) for kind in kinds)
        case _:
            return _ALL_KINDS


def declared_document(batch: GeneratedTypeContractBatch, uri: str | None) -> SourceDocumentId | None:
    """Return the document a declaration names, or the root document when it names none."""
    if uri is None:
        return batch.documents[0].id
    return next((document.id for document in batch.documents if document.uri == uri), None)


class _Selector:
    def __init__(self, batch: GeneratedTypeContractBatch, wire: WirePlan) -> None:
        self.batch = batch
        self.wire = wire
        self.uses = {use.id: use for use in batch.type_uses}
        self.diagnostics: list[CodecDiagnostic] = []
        self.chosen: dict[tuple[str, TypeUseId], tuple[Priority, CodecAdapterRegistration]] = {}

    def report(self, code: CodecReason, source: SourceLocation, message: str) -> None:
        if (diagnostic := CodecDiagnostic(code, source, message)) not in self.diagnostics:
            self.diagnostics.append(diagnostic)

    def location(self, reference: SchemaRef | OperationRef) -> tuple[SourceDocumentId | None, str]:
        return declared_document(self.batch, reference.document), reference.pointer

    def root(self) -> SourceLocation:
        return SourceLocation(self.batch.documents[0].id, "", "declaration")

    def register(self, registration: CodecAdapterRegistration, selector: TypeUseRef | SchemaDirectionalUse) -> None:
        priority, found = self.selected(selector)
        if not found:
            self.report(
                "BND_MODEL_SCOPE_REQUIRED" if priority == _SCHEMA_DEFAULT else "MC_ADAPTER_CONTRACT",
                self.root(),
                f"The {registration.name} adapter selects no processed use",
            )
        elif priority == _EXACT and len(found) > 1:
            self.report(
                "MC_ADAPTER_CONTRACT",
                found[0].use_site,
                f"The {registration.name} adapter selector matches several uses; select a schema direction instead",
            )
        else:
            for use in found:
                self.choose(registration, priority, use)

    def selected(self, selector: TypeUseRef | SchemaDirectionalUse) -> tuple[Priority, list[TypeUseId]]:
        match selector:
            case TypeUseRef():
                return _EXACT, [use for use in self.uses if self.exact(selector, use)]
            case _:
                target = self.location(selector.schema)
                return _SCHEMA_DEFAULT, [
                    use.id
                    for use in self.uses.values()
                    if use.id.direction == selector.direction
                    and use.schema is not None
                    and ((resolved := self.wire.schema(use.schema)[0]).document, resolved.pointer) == target
                ]

    def exact(self, selector: TypeUseRef, use: TypeUseId) -> bool:
        operation = (
            OperationRef(pointer=selector.operation) if isinstance(selector.operation, str) else selector.operation
        )
        return (
            use.role == selector.role
            and isinstance(use.owner, OperationId)
            and (use.owner.use_site.document, use.owner.use_site.pointer) == self.location(operation)
            and (use.location, use.name, use.status, use.media)
            == (selector.location, selector.name, selector.status, selector.media_type)
            and (
                selector.encoding_property is None
                or f"/encoding/{escape_pointer_token(selector.encoding_property)}/headers/" in use.use_site.pointer
            )
        )

    def choose(self, registration: CodecAdapterRegistration, priority: Priority, use: TypeUseId) -> None:
        key = (registration.kind, use)
        match self.chosen.get(key):
            case (existing, other) if existing == priority:
                self.report(
                    "MC_ADAPTER_CONTRACT",
                    use.use_site,
                    f"The {other.name} and {registration.name} adapters both select this use",
                )
            case (existing, _) if existing < priority:
                return
            case _:
                self.chosen[key] = (priority, registration)


def select_adapters(
    batch: GeneratedTypeContractBatch, wire: WirePlan, declarations: CodecDeclarations, surface: Surface
) -> AdapterSelection:
    """Choose one adapter per (kind, use) for one surface, exact uses before schema defaults.

    A schema default applies to every use whose schema is, or wholly references, the selected schema.
    """
    selector = _Selector(batch, wire)
    names: set[str] = set()
    for registration in declarations.adapters:
        if registration.name in names:
            selector.report(
                "MC_ADAPTER_CONTRACT", selector.root(), f"The {registration.name} adapter is registered twice"
            )
            continue
        names.add(registration.name)
        if surface in registration.capabilities.surfaces:
            for use_selector in registration.uses:
                selector.register(registration, use_selector)
    return AdapterSelection(
        {key: registration for key, (_, registration) in selector.chosen.items()}, tuple(selector.diagnostics)
    )


class _AdapterPlanner:
    def __init__(
        self,
        batch: GeneratedTypeContractBatch,
        wire: WirePlan,
        bindings: Mapping[TypeUseId, UseBinding],
        lease: SourceLease | None,
    ) -> None:
        self.batch = batch
        self.wire = wire
        self.bindings = bindings
        self.lease = lease
        self.uses: dict[TypeUseId, TypeUseBinding] = {use.id: use for use in batch.type_uses}
        self.logical = dict(wire.documents)
        self.documents = {resource.uri: resource.contents for resource in wire.resources}
        self.diagnostics: list[CodecDiagnostic] = []
        self.members: dict[int, list[FieldUseBinding]] = {}
        for member in batch.fields:
            self.members.setdefault(member.consumer, []).append(member)
        self.native_types: dict[str, FinalPythonType | None] = {}

    def report(self, registration: CodecAdapterRegistration, use: TypeUseId, gap: str) -> None:
        self.diagnostics.append(
            CodecDiagnostic("MC_ADAPTER_CONTRACT", use.use_site, f"The {registration.name} adapter {gap}")
        )

    def ref(self, location: SourceLocation) -> CodecSourceRef:
        return CodecSourceRef(
            document=self.logical.get(location.document, "").replace(LOGICAL_ROOT, "/inputs/", 1),
            pointer=location.pointer,
        )

    def use_view(self, use: TypeUseId) -> CodecUseView:
        binding = self.uses[use]
        operation = isinstance(use.owner, OperationId)
        return CodecUseView(
            owner_kind="operation" if operation else "schema",
            owner=self.ref(use.owner.use_site if isinstance(use.owner, OperationId) else use.owner),
            role=use.role,
            use_site=self.ref(use.use_site),
            schema=None if binding.schema is None else self.ref(binding.schema),
            declaration=self.ref(use.declaration.location) if operation else None,
            direction=use.direction,
            projection=use.projection,
            location=next(
                (kind for kind in ("path", "query", "querystring", "header", "cookie") if kind == use.location), None
            ),
            name=use.name,
            status=use.status,
            media=use.media,
        )

    def fields(self, use: TypeUseId, binding: UseBinding) -> tuple[CodecFieldView, ...]:
        if not isinstance(bound := self.uses[use].type, GeneratedSymbolType):
            return ()
        return tuple(
            CodecFieldView(
                field_id=f"{binding.native_export}.{slot.name}",
                native_name=slot.name,
                wire_name=wire_name,
                schema_id=None if member.schema is None else self.wire.schema_id(member.schema),
                validation_paths=tuple(
                    (key,)
                    for key in facts.validation_aliases
                    or ((facts.alias,) if facts.alias and not facts.use_serialization_alias else (slot.name,))
                ),
                serialization_alias=facts.serialization_alias
                or (facts.alias if facts.use_serialization_alias else None),
                required=facts.required and not facts.has_default and not facts.explicit_default_factory,
                read_only=facts.read_only,
                write_only=facts.write_only,
                default_kind=facts.none_default_provenance.emitted_default,
                excluded=None,
            )
            for member in self.members.get(bound.symbol, [])
            if (facts := member.model_facts) is not None
            and (slot := member.slot) is not None
            and (wire_name := member.wire_name) is not None
        )

    def resolved(self, use: TypeUseId) -> tuple[SourceLocation, Mapping[str, WireValue]]:
        return self.wire.schema(self.uses[use].schema or use.schema_site)

    def source(self, location: SourceLocation, schema_id: str) -> SchemaSource:
        return SchemaSource(
            schema_id=schema_id,
            source=self.ref(location),
            dialect=DIALECT,
            raw=freeze_wire(_raw(self.lease.borrow(location))) if self.lease is not None else None,
        )

    def view(self, use: TypeUseId, kind: str) -> BindingViewPlan | None:
        if (binding := self.bindings.get(use)) is None:
            return None
        module, _, symbol = (binding.native_export or "").partition(":")
        return BindingViewPlan(
            binding_id=binding.binding_id,
            use=self.use_view(use),
            source=self.source(self.uses[use].schema or use.schema_site, binding.schema_id),
            backend=binding.backend,
            native_kind=binding.native_kind,
            native_export=ModelExportView(module=module, symbol=symbol) if symbol else None,
            fields=self.fields(use, binding),
            projection_mode=binding.projection_mode,
            converter_strategy="registered_adapter" if kind == "model" else binding.converter_strategy,
        )

    def parameter(
        self, use: TypeUseId, registration: CodecAdapterRegistration, capabilities: ParameterCodecCapabilities
    ) -> ParameterViewPlan | None:
        found = next(
            (
                (operation, declaration)
                for operation in self.batch.operations
                for declaration in operation.parameters
                if use in declaration.schemas or any(use in child.schemas for child in declaration.children)
            ),
            None,
        )
        if found is None:
            self.report(registration, use, "selects no parameter")
            return None
        operation, declaration = found
        return self.covered_parameter(registration, capabilities, use, operation, declaration)

    def covered_parameter(
        self,
        registration: CodecAdapterRegistration,
        capabilities: ParameterCodecCapabilities,
        use: TypeUseId,
        operation: OperationContract,
        declaration: WireDeclaration,
    ) -> ParameterViewPlan:
        plan = parameter_view(self.batch, self.wire.version, operation, declaration)
        kinds = _kinds(self.resolved(use)[1])
        gaps = (
            plan.location not in capabilities.locations,
            plan.style is not None and plan.style not in capabilities.styles,
            plan.explode is not None and plan.explode not in capabilities.explode_values,
            plan.content_media_type is not None and plan.content_media_type not in capabilities.media_types,
            not set(kinds) <= set(capabilities.value_kinds),
            use.direction not in capabilities.directions,
        )
        if any(gaps):
            self.report(registration, use, "does not cover this parameter's location, style, media, or wire kinds")
        return plan

    def schema(
        self, use: TypeUseId, registration: CodecAdapterRegistration, capabilities: SchemaCodecCapabilities
    ) -> SchemaAdapterPlan | None:
        if _model_unions(self.bindings[use]):
            self.report(registration, use, "cannot own a schema whose model unions the codec verifies per member")
            return None
        location = self.resolved(use)[0]
        schema_id = self.wire.schema_id(location)
        closure = schema_closure(self.documents, schema_id)
        flag = "readOnly" if self.bindings[use].direction == "request" else "writeOnly"
        root = _group(self.documents, (self.logical[location.document], location.pointer))
        members: dict[str, dict[tuple[str, str], Mapping[str, WireValue]]] = {}
        for (uri, pointer), schema in root.items():
            if isinstance(properties := schema.get("properties"), Mapping):
                for name in properties:
                    at = (uri, f"{pointer}/properties/{escape_pointer_token(name)}")
                    members.setdefault(name, {}).update(_group(self.documents, at))
        flagged = {
            at
            for at in closure.locations
            if isinstance(node := _resolve(self.documents, *at), Mapping) and node.get(flag) is True
        }
        if flagged and flagged & (root.keys() | _reached(self.documents, root, members.values())):
            self.report(
                registration, use, "cannot own a schema whose nested or conditional members its direction excludes"
            )
            return None
        pattern_dialects = ("ecma262-u",) if closure.patterns else ()
        if (
            DIALECT not in capabilities.dialects
            or not set(closure.keywords) <= set(capabilities.keywords)
            or not set(pattern_dialects) <= set(capabilities.pattern_dialects)
            or use.direction not in capabilities.directions
        ):
            self.report(registration, use, "does not cover every keyword or pattern dialect of this schema")
        return SchemaAdapterPlan(
            source=self.source(location, schema_id),
            direction=self.bindings[use].direction,
            required_vocabularies=(),
            required_keywords=closure.keywords,
            pattern_dialects=pattern_dialects,
            patterns=closure.patterns,
            excluded=tuple(sorted(name for name, group in members.items() if group.keys() & flagged)),
        )

    def covers(self, registration: CodecAdapterRegistration, use: TypeUseId) -> bool:
        if not (covered := self.covered(registration.capabilities, use)):
            self.report(registration, use, "does not cover this use's backend, native kind, media, or direction")
        return covered

    def covered(self, capabilities: RegistrationCapabilities, use: TypeUseId) -> bool:
        match capabilities:
            case CodecCapabilities():
                return (
                    (binding := self.bindings.get(use)) is not None
                    and binding.backend in capabilities.backends
                    and binding.native_kind in capabilities.native_kinds
                    and use.direction in capabilities.directions
                )
            case ClientMediaCodecCapabilities() | ServerMediaCodecCapabilities():
                return use.media in capabilities.media_types and use.direction in capabilities.directions
            case _:
                return True

    def plan(self, kind: str, use: TypeUseId, registration: CodecAdapterRegistration) -> AdapterPlan | None:
        if not self.covers(registration, use) or (view := self.view(use, kind)) is None:
            return None
        match registration.capabilities:
            case ParameterCodecCapabilities() as capabilities:
                parameter = self.parameter(use, registration, capabilities)
                return None if parameter is None else AdapterPlan(registration, use, view, parameter=parameter)
            case SchemaCodecCapabilities() as capabilities:
                schema = self.schema(use, registration, capabilities)
                return None if schema is None else AdapterPlan(registration, use, view, schema=schema)
            case CodecCapabilities() if (
                self.native_types.setdefault(registration.name, native := self.uses[use].type) != native
            ):
                self.report(registration, use, "serves uses of different native types")
                return None
            case _:
                return AdapterPlan(registration, use, view)


def plan_adapters(
    selection: AdapterSelection,
    batch: GeneratedTypeContractBatch,
    wire: WirePlan,
    bindings: Mapping[TypeUseId, UseBinding],
    lease: SourceLease | None,
) -> tuple[tuple[AdapterPlan, ...], tuple[CodecDiagnostic, ...]]:
    """Check each selected adapter's capabilities against its use and build its data-only views."""
    planner = _AdapterPlanner(batch, wire, bindings, lease)
    plans = tuple(
        plan
        for (kind, use), registration in selection.chosen.items()
        if (plan := planner.plan(kind, use, registration)) is not None
    )
    return plans, tuple(planner.diagnostics)


def suppressed(
    diagnostics: Iterable[CodecDiagnostic], wire: WirePlan, plans: tuple[AdapterPlan, ...]
) -> tuple[CodecDiagnostic, ...]:
    """Drop the diagnostics that selected parameter and schema adapters take over."""
    parameter_uses = {plan.use for plan in plans if plan.parameter is not None}
    owned = {plan.use for plan in plans if plan.schema is not None}
    documents = {resource.uri: resource.contents for resource in wire.resources}
    closures = (
        {use: schema_closure(documents, schema_id) for use, schema_id in wire.schema_ids if use.direction != "neutral"}
        if owned
        else {}
    )
    logical = dict(wire.documents)
    kept: list[CodecDiagnostic] = []
    for diagnostic in diagnostics:
        if diagnostic.code == "MC_PARAMETER_ENCODING" and diagnostic.uses and set(diagnostic.uses) <= parameter_uses:
            continue
        if diagnostic.code in SCHEMA_ADAPTER_CODES and owned:
            uri, pointer = logical.get(diagnostic.source.document, ""), diagnostic.source.pointer
            reached = {use for use, closure in closures.items() if closure.contains(uri, pointer)}
            if reached and reached <= owned:
                continue
        kept.append(diagnostic)
    return tuple(kept)
