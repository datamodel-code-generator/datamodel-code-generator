"""Capture actual OpenAPI generation calls only for explicit contract consumers."""

from __future__ import annotations

from contextlib import contextmanager, suppress
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, ClassVar, Literal, cast

from typing_extensions import TypedDict, Unpack, override

from datamodel_code_generator._generation_contract import (
    AttemptId,
    BindingCaptureError,
    ModuleResultBinding,
    ReferenceResolution,
    SourceLocation,
)
from datamodel_code_generator._openapi_generation import SourceLease, borrow_source_member
from datamodel_code_generator.enums import AllOfMergeMode
from datamodel_code_generator.model.base import DataModel
from datamodel_code_generator.parser._api_reference import ApiDeclarationId, ApiModelResolver
from datamodel_code_generator.parser.base import (
    _expand_result_module_path,  # pyright: ignore[reportPrivateUsage]
    _normalize_result_module_path,  # pyright: ignore[reportPrivateUsage]
)
from datamodel_code_generator.parser.jsonschema import (
    JsonSchemaObject,
    _get_model_by_path_or_missing,  # pyright: ignore[reportPrivateUsage]
    split_json_pointer,
)
from datamodel_code_generator.parser.openapi import OpenAPIParser, ParameterObject
from datamodel_code_generator.parser.openapi_contract_origins import SchemaOrigin, ValidatedSchemaOriginIndex
from datamodel_code_generator.parser.openapi_contract_store import (
    BindingLedger,
    ContractGenerationStore,
    FieldCopy,
    TypeCopy,
    Variant,
    capture_errors,
)
from datamodel_code_generator.parser.openapi_scope import ApiDeclarationFrame, ApiOpenAPIParser
from datamodel_code_generator.reference import (
    SPECIAL_PATH_MARKER,
    FieldNameResolver,
    ModelResolver,
    ModelType,
    Reference,
)


class ResolverOptions(TypedDict, total=False):
    """Preserve fixed resolver keyword types at the capture-only factory boundary."""

    exclude_names: set[str] | None
    duplicate_name_suffix: str | None
    base_url: str | None
    singular_name_suffix: str | None
    aliases: Mapping[str, str | list[str]] | None
    snake_case_field: bool
    empty_field_name: str | None
    custom_class_name_generator: Callable[[str], str] | None
    base_path: Path | None
    field_name_resolver_classes: dict[ModelType, type[FieldNameResolver]] | None
    original_field_name_delimiter: str | None
    special_field_name_prefix: str | None
    remove_special_field_name_prefix: bool
    capitalise_enum_members: bool
    no_alias: bool
    use_subclass_enum: bool
    target_python_version: PythonVersion | None
    remove_suffix_number: bool
    parent_scoped_naming: bool
    treat_dot_as_module: bool | None
    strict_dotted_module_names: bool
    naming_strategy: NamingStrategy | None
    duplicate_name_suffix_map: dict[str, str] | None
    class_name_prefix: str | None
    class_name_suffix: str | None
    class_name_affix_scope: ClassNameAffixScope | None
    skip_affix_for_root: bool
    model_name_map: Mapping[str, str] | None
    default_value_overrides: Mapping[str, object] | None
    http_backend: HTTPBackend


@dataclass(frozen=True, slots=True)
class DefaultResolution:
    """Keep actual resolver inputs/returns and explicit override producer identity."""

    field_name: str
    class_name: str | None
    original: object
    had_default: bool
    result: tuple[object, bool]
    producer: Literal["original", "override", "opaque"]


@dataclass(slots=True)
class ReferenceProducerFrame:
    """Own direct resolver returns separately from nested helpers and add_ref calls."""

    resolutions: list[ReferenceResolution]


class BindingResolverMixin(ModelResolver):
    """Keep actual resolver arguments/results, including recursive call ownership."""

    def __init__(self, ledger: BindingLedger, **options: Unpack[ResolverOptions]) -> None:
        """Establish attempt ownership before the original resolver constructor."""
        self.binding_ledger = ledger
        self.resolution_owner: ReferenceProducerFrame | None = None
        self.resolutions: list[ReferenceResolution] = []
        self.default_resolutions: list[DefaultResolution] = []
        self._resolution_stack: list[int] = []
        self._next_resolution = 0
        super().__init__(**options)

    def resolve_ref(self, path: Sequence[str] | str) -> str:
        """Call the selected resolver once and record its completed return."""
        sequence = self._next_resolution
        self._next_resolution += 1
        parent = self._resolution_stack[-1] if self._resolution_stack else None
        original = path if isinstance(path, str) else tuple(path)
        self._resolution_stack.append(sequence)
        try:
            result = super().resolve_ref(path)
        finally:
            self._resolution_stack.pop()
        self._record_resolution(sequence, original, result, parent)
        return result

    def add_ref(self, ref: str, resolved: bool = False) -> Reference:  # ruff: ignore[boolean-type-hint-positional-argument, boolean-default-value-positional-argument]
        """Retain the actual registered Reference without another lookup or call."""
        sequence = self._next_resolution
        self._next_resolution += 1
        parent = self._resolution_stack[-1] if self._resolution_stack else None
        self._resolution_stack.append(sequence)
        try:
            result = super().add_ref(ref, resolved=resolved)
        finally:
            self._resolution_stack.pop()
        self._record_reference(sequence, ref, result, parent, resolved=resolved)
        return result

    @capture_errors
    def _record_resolution(
        self, sequence: int, original: str | tuple[str, ...], result: str, parent: int | None
    ) -> None:
        """Record a completed resolve call without touching resolver state."""
        event = ReferenceResolution(sequence, original, result, parent, "resolve_ref")
        self.resolutions.append(event)
        if parent is None and self.resolution_owner is not None:
            self.resolution_owner.resolutions.append(event)

    @capture_errors
    def _record_reference(
        self, sequence: int, original: str, result: Reference, parent: int | None, *, resolved: bool
    ) -> None:
        """Assign an ID to the actual reference under the capture failure boundary."""
        self.resolutions.append(
            ReferenceResolution(
                sequence, original, result.path, parent, "add_ref", self.binding_ledger.identity(result), resolved
            )
        )

    @override
    def resolve_default_value(
        self, field_name: str, original_default: object, has_default: bool, class_name: str | None
    ) -> tuple[object, bool]:
        """Call the existing override owner once, containing arbitrary values as object."""
        result = super().resolve_default_value(field_name, original_default, has_default, class_name)
        return self._record_default_resolution(
            field_name, original_default, class_name, result, had_default=has_default
        )

    @capture_errors
    def _record_default_resolution(
        self,
        field_name: str,
        original: object,
        class_name: str | None,
        result: tuple[object, bool],
        *,
        had_default: bool,
    ) -> tuple[object, bool]:
        overrides: Mapping[str, object] = self.default_value_overrides
        override_type = type(overrides)
        producer: Literal["original", "override", "opaque"] = "opaque"
        if override_type is dict and all(type(key) is str for key in overrides):
            scoped_key = f"{class_name}.{field_name}" if class_name else None
            producer = (
                "override"
                if (scoped_key is not None and scoped_key in overrides) or field_name in overrides
                else "original"
            )
        self.default_resolutions.append(
            DefaultResolution(field_name, class_name, original, had_default, result, producer)
        )
        return result

    def close_capture(self) -> None:
        """Release completed resolver observations after projection or failure."""
        self.resolutions.clear()
        self.default_resolutions.clear()
        self.resolution_owner = None
        self._resolution_stack.clear()


class ContractModelResolver(BindingResolverMixin):
    """Observe ordinary scope resolution without changing its canonicalization."""


class ContractApiModelResolver(BindingResolverMixin, ApiModelResolver):
    """Observe the actual Api canonicalization owner through the same mixin."""


@dataclass(frozen=True, slots=True)
class OperationObservation:
    """Borrow an actual operation frame and its actual callback parent until freeze."""

    frame: ApiDeclarationFrame
    parent: ApiDeclarationFrame | None


@dataclass(frozen=True, slots=True)
class TypeObservation:
    """Retain an actual returned type and declaration frame without constructing types."""

    data_type: DataType
    path: tuple[str, ...] | None
    ref: str | None
    declaration: ApiDeclarationFrame | None
    operation: LegacyOperationObservation | None
    resolved_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SchemaTypeObservation:
    """Borrow an actual schema producer's returned type for nested helper demands."""

    schema: JsonSchemaObject | None
    data_type: DataType
    locations: tuple[SourceLocation, ...] = ()
    root_value: GraphObjectId | None = None


@dataclass(frozen=True, slots=True)
class SchemaReferenceObservation:
    """Connect an actual parsed declaration to its existing resolver identity."""

    schema: JsonSchemaObject
    reference: GraphObjectId


@dataclass(frozen=True, slots=True)
class VariantFieldsObservation:
    """Retain the actual fields omitted by one directional model producer."""

    base: GraphObjectId
    suffix: Literal["Request", "Response"]
    fields: tuple[GraphObjectId, ...]
    excluded: tuple[tuple[GraphObjectId, Literal["read_only", "write_only"]], ...]


@dataclass(frozen=True, slots=True)
class ObjectUseObservation:
    """Keep actual object-reference use and resolved declaration independently."""

    use: ApiDeclarationId
    target: _ApiObject
    original_use: ApiDeclarationId


@dataclass(frozen=True, slots=True)
class SchemaUseObservation:
    """Keep original use paths separate from declaration paths and naming paths."""

    frame: ApiDeclarationFrame
    use: ApiDeclarationId
    object_use: ObjectUseObservation | None
    operation: ApiDeclarationFrame | None


class _ObservedDeclarationFrames(list[ApiDeclarationFrame]):  # ruff: ignore[subclass-builtin] -- Preserve the engine list interface; only append observes capture frames.
    """Observe actual Api frame insertion exclusively on capture parser instances."""

    def __init__(
        self,
        lease: SourceLease,
        record_schema: Callable[[ApiDeclarationFrame, SourceDocumentId], None],
        ledger: BindingLedger,
    ) -> None:
        """Retain finite operation and schema entry observations, not a mutation log."""
        super().__init__()
        self.lease = lease
        self.binding_ledger = ledger
        self.record_schema: Callable[[ApiDeclarationFrame, SourceDocumentId], None] | None = record_schema
        self.operations: list[OperationObservation] = []
        self.schemas: list[ApiDeclarationFrame] = []

    @capture_errors
    def append(self, frame: ApiDeclarationFrame) -> None:
        """Borrow the engine's actual frame without recomputing its effective values."""
        if (record_schema := self.record_schema) is None:
            msg = "Declaration capture is closed"
            raise BindingCaptureError(msg)
        document = self.lease.register(frame.declaration.document, frame.raw_document)
        match frame.phase:
            case "operation":
                parent = next((entry for entry in reversed(self) if entry.phase == "operation"), None)
                self.operations.append(OperationObservation(frame, parent))
            case _:  # The remaining Api-owned frame phases are schema and file.
                self.schemas.append(frame)
                record_schema(frame, document)
        super().append(frame)

    def close(self) -> None:
        """Release borrowed frames after freezing without altering the source nodes."""
        self.record_schema = None
        self.clear()
        self.operations.clear()
        self.schemas.clear()


@dataclass(frozen=True, slots=True)
class LegacyOperationCandidate:
    """Retain a direct legacy declaration before the engine makes effective copies."""

    declaration: ApiDeclarationId
    method: str
    raw: dict[str, YamlValue]


@dataclass(frozen=True, slots=True)
class LegacyOperationObservation:
    """Keep unresolved or ambiguous legacy origins explicit instead of guessing."""

    effective: dict[str, YamlValue]
    candidates: tuple[LegacyOperationCandidate, ...]
    origin_state: Literal["known", "unavailable", "ambiguous"]
    engine_path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LegacyPathItemsFrame:
    """Borrow original scope inputs, including parameter sequences and security."""

    items: dict[str, dict[str, YamlValue]]
    base_path: tuple[str, ...]
    scope: str
    global_parameters: list[dict[str, YamlValue]]
    security: list[dict[str, list[str]]] | None
    candidates: tuple[LegacyOperationCandidate, ...]


@dataclass(frozen=True, slots=True)
class RequestTypesObservation:
    """Borrow every media return from the existing request-body parser."""

    operation: LegacyOperationObservation | None
    path: tuple[str, ...]
    types: dict[str, DataType]


@dataclass(frozen=True, slots=True)
class ResponseTypesObservation:
    """Borrow all status/media returns, including actual contentless response types."""

    operation: LegacyOperationObservation | None
    path: tuple[str, ...]
    types: dict[str | int, dict[str, DataType]]


@dataclass(slots=True)
class LegacyParameterFrame:
    """Join one real validated parameter sequence to the effective raw sequence."""

    operation: LegacyOperationObservation
    inputs: tuple[tuple[ReferenceObject | ParameterObject, YamlValue], ...]
    path: tuple[str, ...]
    current: ParameterObject | None = None


@dataclass(frozen=True, slots=True)
class ParameterFieldObservation:
    """Retain the actual top-level parameter field even without an emitted parameter model."""

    operation: LegacyOperationObservation
    parameter: ParameterObject
    field: DataModelFieldBase


def _same_legacy_operation(original: dict[str, YamlValue], effective: dict[str, YamlValue]) -> bool:
    """Recognize only identity-preserving copies with original parameter prefixes."""
    if original is effective:
        return True
    if any(key not in effective for key in original):
        return False
    if any(key not in original and key not in {"parameters", "security"} for key in effective):
        return False
    if any(effective.get(key) is not value for key, value in original.items() if key != "parameters"):
        return False
    original_parameters, effective_parameters = original.get("parameters", []), effective.get("parameters", [])
    if not isinstance(original_parameters, list) or not isinstance(effective_parameters, list):
        return False
    return len(original_parameters) <= len(effective_parameters) and all(
        original is effective for original, effective in zip(original_parameters, effective_parameters, strict=False)
    )


@dataclass(frozen=True, slots=True)
class RawValidationFrame:
    """Connect one raw validation call to the actual parse_obj invocation it makes."""

    name: str
    raw: YamlValue
    path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FieldOriginObservation:
    """Keep source properties separate from final field names and model policies."""

    field: GraphObjectId
    wire_name: str
    origins: tuple[SchemaOrigin, ...]
    required_by_node: bool


@dataclass(frozen=True, slots=True)
class EffectiveDefaultObservation:
    """Preserve actual default-policy inputs and the single completed return."""

    field_name: str
    class_name: str | None
    original: object
    has_default: bool
    required: bool
    result: tuple[object, bool, bool]
    resolution: DefaultResolution


@dataclass(frozen=True, slots=True)
class InheritedDefaultObservation:
    """Associate a derived-scope resolver return with its actual field pair."""

    field: GraphObjectId
    inherited: GraphObjectId
    resolution: DefaultResolution


@dataclass(frozen=True, slots=True)
class PreexistingNullObservation:
    """Retain only top-level null evidence and unresolved reference identities."""

    explicit: bool
    references: tuple[GraphObjectId, ...]
    opaque: bool


@dataclass(frozen=True, slots=True)
class FieldConstructionObservation:
    """Borrow real field construction inputs without invoking final field getters."""

    field: GraphObjectId
    schema: JsonSchemaObject | None
    required: bool
    effective_default: object
    effective_has_default: bool | None
    use_default_with_required: bool
    original_name: str | None
    class_name: str | None
    default_policy: EffectiveDefaultObservation | None
    preexisting_null: PreexistingNullObservation
    data_type: DataType


@dataclass(frozen=True, slots=True)
class ModuleOutputObservation:
    """Borrow the actual completed module output and its declaring models until freeze."""

    module: tuple[str, ...]
    models: tuple[DataModel, ...]
    result: Result
    imports: tuple[Imports, Imports]


@dataclass(slots=True)
class ConditionalMergeFrame:
    """Observe only branch reads made for the current original merge call."""

    parent: JsonSchemaObject
    branches: list[JsonSchemaObject]


@dataclass(frozen=True, slots=True)
class AdditionalTypeFrame:
    """Identify the actual metadata path whose type the engine is about to stringify."""

    path: str
    schema: JsonSchemaObject
    suffix: Literal["Request", "Response"] | None


@dataclass(frozen=True, slots=True)
class AdditionalTypeObservation:
    """Retain the original type return before backend metadata stores rendered text."""

    frame: AdditionalTypeFrame
    data_type: DataType


@dataclass(frozen=True, slots=True)
class PatternTypeObservation:
    """Keep actual schema-valued pattern declarations and the original type return."""

    patterns: tuple[tuple[str, JsonSchemaObject | bool], ...]
    data_type: DataType


@dataclass(frozen=True, slots=True)
class PatternValidatorObservation:
    """Borrow the existing validator producer's structured type returns."""

    schema: JsonSchemaObject
    patterns: tuple[tuple[str, DataType], ...]
    rejected: tuple[str, ...]
    additional: DataType | None
    allow_unmatched: bool


@dataclass(frozen=True, slots=True)
class DiscriminatorTypeObservation:
    """Retain the actual enum source, member fields and returned discriminator type."""

    enum: GraphObjectId | None
    members: tuple[tuple[str | None, GraphObjectId], ...]
    model: GraphObjectId
    data_type: DataType
    reference: GraphObjectId | None


@dataclass(frozen=True, slots=True)
class SyntheticFieldObservation:
    """Keep non-property field occurrences distinct from ordinary wire properties."""

    field: GraphObjectId
    kind: Literal["required_only", "additional_properties", "root_value"]
    locations: tuple[SourceLocation, ...]
    name: str | None


@dataclass(frozen=True, slots=True)
class RootValueObservation:
    """Borrow actual root-value helper inputs and outputs until projection."""

    sources: tuple[JsonSchemaObject | bool, ...]
    result: dict[str, object] | bool
    producer: Literal["nodes", "children", "validation_keywords"]


@dataclass(slots=True)
class RootSchemaFrame:
    """Retain value-helper results associated with one actual root materialization."""

    source: JsonSchemaObject
    references: ReferenceProducerFrame
    values: list[RootValueObservation]


@dataclass(slots=True)
class RootValueChildrenFrame:
    """Own the actual ref expansion feeding one root-value child merge."""

    sources: tuple[JsonSchemaObject | bool, ...]
    references: ReferenceProducerFrame
    bound: bool = False


@dataclass(slots=True)
class AllOfRefFrame:
    """Match direct allOf ref occurrences to their actual loader resolutions."""

    name: str
    obj: JsonSchemaObject
    path: tuple[str, ...]
    direct_refs: tuple[tuple[int, JsonSchemaObject], ...]
    producer: ReferenceProducerFrame
    materialized_parents: dict[int, JsonSchemaObject]


@dataclass(slots=True)
class CombinedBranchFrame:
    """Keep original occurrence order separate from the engine's filtered sequence."""

    name: str
    parent: JsonSchemaObject
    path: tuple[str, ...]
    keyword: str
    originals: tuple[JsonSchemaObject | bool, ...]
    edges: list[tuple[JsonSchemaObject, JsonSchemaObject]]
    false_decisions: list[tuple[str, bool]]
    merged_inputs: dict[int, JsonSchemaObject]
    collecting: bool = True


@dataclass(slots=True)
class InheritedMergeFrame:
    """Retain the effective map returned inside one actual parent-constraint merge."""

    child: JsonSchemaObject
    parents: dict[str, tuple[JsonSchemaObject | bool, str]] | None


class BindingCaptureMixin(OpenAPIParser):
    """Create attempt ownership before ordinary parser construction begins."""

    _binding_resolver_type: ClassVar[type[BindingResolverMixin]] = ContractModelResolver

    def __init__(
        self,
        source: str | Path | list[Path] | ParseResult | dict[str, YamlValue],
        *,
        attempt_id: AttemptId,
        binding_ledger: BindingLedger | None = None,
        source_lease: SourceLease | None = None,
        config: OpenAPIParserConfig | None = None,
        **options: Unpack[OpenAPIParserConfigDict],
    ) -> None:
        """Keep state on capture subclasses without adding fields to default parsers."""
        self.binding_ledger = binding_ledger if binding_ledger is not None else BindingLedger(attempt_id)
        self.source_lease = source_lease if source_lease is not None else SourceLease()
        if self.binding_ledger.attempt_id != attempt_id:
            msg = "BND_ATTEMPT_MISMATCH: parser and ledger identities differ"
            raise BindingCaptureError(msg)
        self.schema_origins = ValidatedSchemaOriginIndex(self.binding_ledger)
        self.field_origins: dict[GraphObjectId, FieldOriginObservation] = {}
        self.synthetic_fields: list[SyntheticFieldObservation] = []
        self._additional_root_fields: set[GraphObjectId] = set()
        self.schema_types: list[SchemaTypeObservation] = []
        self.schema_references: list[SchemaReferenceObservation] = []
        self.root_documents: list[str] = []
        self.unresolved_references: set[str] = set()
        self.variant_fields: list[VariantFieldsObservation] = []
        self._variant_references: dict[GraphObjectId, dict[GraphObjectId, Reference]] = {}
        self._array_sources: list[JsonSchemaObject] = []
        self._additional_type_frames: list[AdditionalTypeFrame] = []
        self.additional_types: list[AdditionalTypeObservation] = []
        self.pattern_types: list[PatternTypeObservation] = []
        self.pattern_validators: list[PatternValidatorObservation] = []
        self.discriminator_types: list[DiscriminatorTypeObservation] = []
        self._required_field_lists: list[tuple[list[str], ...]] = []
        self.field_constructions: dict[GraphObjectId, FieldConstructionObservation] = {}
        self.effective_defaults: list[EffectiveDefaultObservation] = []
        self.inherited_defaults: dict[GraphObjectId, InheritedDefaultObservation] = {}
        self.module_outputs: list[ModuleOutputObservation] = []
        self._conditional_merges: list[ConditionalMergeFrame] = []
        self._inherited_merges: list[InheritedMergeFrame] = []
        self._combined_branches: list[CombinedBranchFrame] = []
        self._allof_refs: list[AllOfRefFrame] = []
        self._root_schema_frames: list[RootSchemaFrame] = []
        self._root_value_children: list[RootValueChildrenFrame] = []
        self._pending_field_default: EffectiveDefaultObservation | None = None
        self.root_materializations: list[tuple[RootSchemaFrame, JsonSchemaObject]] = []
        self._raw_validation_frames: list[RawValidationFrame] = []
        self.type_observations: list[TypeObservation] = []
        self.legacy_operations: list[LegacyOperationObservation] = []
        self.legacy_scopes: list[LegacyPathItemsFrame] = []
        self._legacy_scopes: list[LegacyPathItemsFrame] = []
        self._legacy_operations: list[LegacyOperationObservation] = []
        self.legacy_ref_objects: dict[tuple[int, str], SchemaOrigin] = {}
        self._media_paths: dict[tuple[str, ...], tuple[tuple[str, ...], bool]] = {}
        self.request_types: list[RequestTypesObservation] = []
        self.response_types: list[ResponseTypesObservation] = []
        self.parameter_fields: list[ParameterFieldObservation] = []
        self._parameter_frames: list[LegacyParameterFrame] = []
        self._model_resolver_factory = partial(self._create_binding_resolver)
        self._generation_store_factory = partial(self._create_binding_store)
        # The existing parser accepts mappings; its constructor annotation omits them.
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            source,  # type: ignore[arg-type]
            config=config,
            **options,
        )

    @contextmanager
    def _resolution_producer(self, frame: ReferenceProducerFrame | None = None) -> Generator[None, None, None]:
        """Give nested engine work a distinct resolver owner without extra resolution."""
        previous = self.binding_resolver.resolution_owner
        self.binding_resolver.resolution_owner = frame
        try:
            yield
        finally:
            self.binding_resolver.resolution_owner = previous

    def _create_binding_store(self) -> tuple[ContractGenerationStore, list[DataModel]]:
        """Create the capture store through the original constructor's factory call."""
        store = ContractGenerationStore(self.binding_ledger)
        return store, store.models

    def _copy_model_field(
        self, field: DataModelFieldBase, *, data_type: DataType | None = None, register_references: bool = True
    ) -> DataModelFieldBase:
        """Retain actual copy origins without intercepting the helper's recursion."""
        result: DataModelFieldBase = super()._copy_model_field(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            field, data_type=data_type, register_references=register_references
        )
        if data_type is None and self.discriminator_types:
            self._record_enum_copies(field.data_type, result.data_type)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        return self._record_field_copy((field,), result, None)  # pyright: ignore[reportUnknownArgumentType]

    def _copy_model_type(self, data_type: DataType, *, register_references: bool = True) -> DataType:
        """Observe one original helper return with unchanged reference registration."""
        result: DataType = super()._copy_model_type(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            data_type, register_references=register_references
        )
        return self._record_type_copy(data_type, result)  # pyright: ignore[reportUnknownArgumentType]

    def _copy_inherited_field(  # ruff: ignore[too-many-arguments] -- Preserve the existing copy hook signature.
        self,
        field: DataModelFieldBase,
        inherited_field: DataModelFieldBase,
        *,
        force_optional: bool = False,
        partial_merge_mode: AllOfMergeMode = AllOfMergeMode.All,
        register_references: bool = True,
        reserved_names: set[str] | None = None,
    ) -> DataModelFieldBase | None:
        """Preserve both source fields and the actual inherited merge mode."""
        result: DataModelFieldBase | None = super()._copy_inherited_field(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            field,
            inherited_field,
            force_optional=force_optional,
            partial_merge_mode=partial_merge_mode,
            register_references=register_references,
            reserved_names=reserved_names,
        )
        if result is None:
            return None
        if self.discriminator_types:
            self._record_enum_copies(inherited_field.data_type, result.data_type)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        return self._record_field_copy(
            (field, inherited_field),
            result,  # pyright: ignore[reportUnknownArgumentType]
            partial_merge_mode,
        )

    @capture_errors
    def _record_field_copy(
        self, sources: tuple[DataModelFieldBase, ...], target: DataModelFieldBase, mode: AllOfMergeMode | None
    ) -> DataModelFieldBase:
        identity = self.binding_ledger.identity
        self.binding_ledger.copies.append(
            FieldCopy(tuple(identity(source) for source in sources), identity(target), mode)
        )
        return target

    @capture_errors
    def _resolve_field_sources(self) -> dict[GraphObjectId, tuple[GraphObjectId, ...]]:
        """Follow completed copies in producer order, preserving both inherited sources.

        Original observations can be registered after an inner copy returns. Resolve
        after parsing so those observations remain available without copying schemas
        or conflating sources whose generated fields happen to look the same.
        """
        sources: dict[GraphObjectId, tuple[GraphObjectId, ...]] = {}
        for copy in self.binding_ledger.copies:
            if not isinstance(copy, FieldCopy):
                continue
            original_ids = dict.fromkeys(
                original for source in copy.sources for original in sources.get(source, (source,))
            )
            if (
                copy.target in self.field_origins
                or copy.target in self.field_constructions
                or copy.target in self.inherited_defaults
            ):
                original_ids[copy.target] = None
            sources[copy.target] = tuple(original_ids)
        return sources

    def _apply_inherited_field_default(
        self, field: DataModelFieldBase, inherited_field: DataModelFieldBase, *, class_name: str
    ) -> None:
        """Bind only resolver calls actually made by the existing inherited-default hook."""
        start = len(self.binding_resolver.default_resolutions)
        super()._apply_inherited_field_default(  # pyright: ignore[reportUnknownMemberType]
            field, inherited_field, class_name=class_name
        )
        self._record_inherited_default(field, inherited_field, start)

    @capture_errors
    def _record_inherited_default(
        self, field: DataModelFieldBase, inherited_field: DataModelFieldBase, start: int
    ) -> None:
        resolutions = self.binding_resolver.default_resolutions
        if len(resolutions) == start:
            return
        if len(resolutions) != start + 1:
            msg = "An inherited default has multiple unmatched resolver calls"
            raise BindingCaptureError(msg)
        identity = self.binding_ledger.identity
        field_id = identity(field)
        self.inherited_defaults[field_id] = InheritedDefaultObservation(
            field_id, identity(inherited_field), resolutions[start]
        )

    @capture_errors
    def _record_type_copy(self, source: DataType, target: DataType) -> DataType:
        identity = self.binding_ledger.identity
        self.binding_ledger.copies.append(TypeCopy(identity(source), identity(target)))
        if self.discriminator_types:
            self._record_enum_copies(source, target)
        return target

    @capture_errors
    def _record_enum_copies(self, source: DataType, target: DataType) -> None:
        """Preserve enum producer identity through actual helper copies, without recopying."""
        pending = [(source, target)]
        identity = self.binding_ledger.identity
        while pending:
            original, copied = pending.pop()
            if original.enum_member_literals and copied.enum_member_literals == original.enum_member_literals:
                self.binding_ledger.enum_copies[identity(copied)] = identity(original)
            if len(original.data_types) == len(copied.data_types):
                pending.extend(zip(original.data_types, copied.data_types, strict=True))
            if original.dict_key is not None and copied.dict_key is not None:
                pending.append((original.dict_key, copied.dict_key))

    def _get_rw_model_variant_reference(
        self, base_reference: Reference, suffix: Literal["Request", "Response"], *, loaded: bool = False
    ) -> Reference:
        """Retain variants before the engine releases its per-parse caches."""
        result: Reference = super()._get_rw_model_variant_reference(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            base_reference, suffix, loaded=loaded
        )
        return self._record_variant(base_reference, suffix, result)  # pyright: ignore[reportUnknownArgumentType]

    def _create_variant_model(  # ruff: ignore[too-many-arguments] -- Preserve the existing producer signature.
        self,
        base_reference: Reference,
        suffix: Literal["Request", "Response"],
        model_fields: list[DataModelFieldBase],
        obj: JsonSchemaObject,
        data_model_type_class: type[DataModel],
        *,
        source_fields: Sequence[DataModelFieldBase] = (),
    ) -> None:
        """Capture actual exclusions after the ordinary producer has created its variant."""
        super()._create_variant_model(  # pyright: ignore[reportUnknownMemberType]
            base_reference, suffix, model_fields, obj, data_model_type_class, source_fields=source_fields
        )
        self._record_variant_fields(base_reference, suffix, model_fields, source_fields)

    @capture_errors
    def _record_variant_fields(
        self,
        base: Reference,
        suffix: Literal["Request", "Response"],
        included: list[DataModelFieldBase],
        source: Sequence[DataModelFieldBase],
    ) -> None:
        identity = self.binding_ledger.identity
        selected = {identity(field) for field in included}
        excluded: list[tuple[GraphObjectId, Literal["read_only", "write_only"]]] = []
        for field in source:
            if (field_id := identity(field)) in selected:
                continue
            if suffix == "Request" and field.read_only:
                excluded.append((field_id, "read_only"))
            elif suffix == "Response" and field.write_only:
                excluded.append((field_id, "write_only"))
        self.variant_fields.append(
            VariantFieldsObservation(
                identity(base), suffix, tuple(identity(field) for field in source), tuple(excluded)
            )
        )

    @capture_errors
    def _record_variant(self, base: Reference, suffix: Literal["Request", "Response"], result: Reference) -> Reference:
        identity = self.binding_ledger.identity
        self.binding_ledger.variants.append(Variant(identity(base), suffix, identity(result)))
        self._variant_references.setdefault(identity(base), {})[identity(result)] = result
        return result

    def _create_binding_resolver(self, **options: Unpack[ResolverOptions]) -> BindingResolverMixin:
        """Construct the owned resolver once with unchanged parser keyword values."""
        self.binding_resolver = self._binding_resolver_type(self.binding_ledger, **options)
        return self.binding_resolver

    def _parse_specification(self, specification: dict[str, YamlValue], path_parts: list[str]) -> None:
        """Borrow the actual root mapping before the existing engine visits it."""
        self._borrow_source("/".join(path_parts), specification)
        self.root_documents.append("/".join(path_parts))
        super()._parse_specification(specification, path_parts)  # pyright: ignore[reportUnknownMemberType]

    def _get_ref_body(self, resolved_ref: str) -> dict[str, YamlValue]:
        """Borrow the actual loader return without requesting another document."""
        with self._resolution_producer():
            raw: dict[str, YamlValue] = super()._get_ref_body(resolved_ref)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        self._borrow_source(resolved_ref, raw)  # pyright: ignore[reportUnknownArgumentType]
        return raw  # pyright: ignore[reportUnknownVariableType]

    def _get_model_by_json_pointer(self, raw: dict[str, YamlValue], object_paths: list[str], ref: str) -> YamlValue:
        """Retain actual missing-pointer decisions without repeating lookup or diagnostics."""
        before = len(self._dangling_refs)
        result: YamlValue = super()._get_model_by_json_pointer(raw, object_paths, ref)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        self._record_unresolved_reference(ref, before)
        return result  # pyright: ignore[reportUnknownVariableType]

    @capture_errors
    def _record_unresolved_reference(self, ref: str, before: int) -> None:
        if len(self._dangling_refs) > before:
            self.unresolved_references.add(ref)

    @capture_errors
    def _borrow_source(self, uri: str, raw: dict[str, YamlValue]) -> None:
        """Borrow one actual loader result under the recording failure boundary."""
        document = self.source_lease.register(uri, raw)
        self.schema_origins.borrow_document(document, raw)

    def _parse_raw_or_validated_obj(
        self, name: str, raw: YamlValue, path: list[str], validated_obj: JsonSchemaObject | None = None
    ) -> None:
        """Observe actual validator input without overriding the guarded validator."""
        self._raw_validation_frames.append(RawValidationFrame(name, raw, tuple(path)))
        try:
            super()._parse_raw_or_validated_obj(  # pyright: ignore[reportUnknownMemberType]
                name, raw, path, validated_obj
            )
        finally:
            self._raw_validation_frames.pop()

    def parse_obj(self, name: str, obj: JsonSchemaObject, path: list[str]) -> None:
        """Connect the actual validated object before ordinary dispatch transforms it."""
        self._pair_validated_source(name, obj, path)
        super().parse_obj(name, obj, path)  # pyright: ignore[reportUnknownMemberType]
        self._record_declared_root(obj, path)

    @capture_errors
    def _record_declared_root(self, obj: JsonSchemaObject, path: list[str]) -> None:
        reference = self.model_resolver.references.get(self.model_resolver.join_path(tuple(path)))
        if reference is None:
            return
        identity = self.binding_ledger.identity(reference)
        self.schema_references.append(SchemaReferenceObservation(obj, identity))
        for candidate in (reference, *self._variant_references.get(identity, {}).values()):
            if isinstance(model := candidate.source, DataModel) and (model.IS_ROOT_MODEL or model.IS_ALIAS):
                self._record_root_fields(obj, model.fields, model.reference.name)

    @capture_errors
    def _pair_validated_source(self, name: str, obj: JsonSchemaObject, path: list[str]) -> None:
        if not self._raw_validation_frames:
            if (
                reference := self.model_resolver.references.get(self.model_resolver.join_path(tuple(path)))
            ) is not None:
                self._pair_inherited_declaration(reference.path, obj, relation="validated_child")
            return
        frame = self._raw_validation_frames[-1]
        if frame.name != name or frame.path != tuple(path) or not isinstance(frame.raw, (dict, bool)):
            return
        locations = self._validation_locations(frame)
        if (
            not locations
            and (reference := self.model_resolver.references.get(self.model_resolver.join_path(tuple(path))))
            is not None
        ):
            self._pair_inherited_declaration(reference.path, obj, relation="validated_child")
        for location in locations:
            self.schema_origins.pair(raw=frame.raw, obj=obj, location=location)

    def _validation_locations(self, frame: RawValidationFrame) -> tuple[SourceLocation, ...]:
        """Retain all raw occurrences when legacy processing supplies no declaration frame."""
        return self.schema_origins.raw_locations(raw=frame.raw)

    def _get_conditional_schema(
        self, obj: JsonSchemaObject, keyword: Literal["if", "then", "else", "not"]
    ) -> JsonSchemaObject | bool | None:
        """Retain the actual validated conditional node without another validation."""
        result: JsonSchemaObject | bool | None = super()._get_conditional_schema(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            obj, keyword
        )
        return self._record_conditional(obj, keyword, result=result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_conditional(
        self,
        parent: JsonSchemaObject,
        keyword: Literal["if", "then", "else", "not"],
        *,
        result: JsonSchemaObject | bool | None,
    ) -> JsonSchemaObject | bool | None:
        self.schema_origins.pair_keyword(parent, keyword, result=result)
        if (
            self._conditional_merges
            and (frame := self._conditional_merges[-1]).parent is parent
            and keyword in {"then", "else"}
            and isinstance(result, JsonSchemaObject)
        ):
            frame.branches.append(result)
        return result

    def _merge_conditional_properties(self, obj: JsonSchemaObject) -> JsonSchemaObject:
        """Follow actual branch calls made by this existing conditional merge."""
        frame = ConditionalMergeFrame(obj, [])
        self._conditional_merges.append(frame)
        try:
            result: JsonSchemaObject = super()._merge_conditional_properties(obj)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        finally:
            self._conditional_merges.pop()
        return self._record_conditional_merge(frame, result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_conditional_merge(self, frame: ConditionalMergeFrame, result: JsonSchemaObject) -> JsonSchemaObject:
        if result is not frame.parent:
            self.schema_origins.derive(frame.parent, result, "conditional_merge")
            for branch in frame.branches:
                self.schema_origins.derive(branch, result, "conditional_merge")
        return result

    def _get_deferred_inherited_parse_object(
        self, source_obj: JsonSchemaObject, deferred_property_names: frozenset[str]
    ) -> JsonSchemaObject:
        """Connect actual stripped child nodes to their unmodified source schema."""
        with self._resolution_producer():
            result: JsonSchemaObject = super()._get_deferred_inherited_parse_object(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                source_obj, deferred_property_names
            )
        return self._record_schema_result(source_obj, result, "deferred_shape")  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_schema_result(
        self, source: JsonSchemaObject, result: JsonSchemaObject, relation: SchemaRelation
    ) -> JsonSchemaObject:
        self.schema_origins.derive(source, result, relation, merge_mode=self.allof_merge_mode)
        return result

    def _normalize_inherited_constraint_compositions(self, schema: JsonSchemaObject) -> JsonSchemaObject:
        """Retain the actual constraint-normalization result, including nested calls."""
        result: JsonSchemaObject = super()._normalize_inherited_constraint_compositions(schema)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return self._record_schema_result(schema, result, "inherited_constraint")  # pyright: ignore[reportUnknownArgumentType]

    def _sanitize_untyped_boolean_inherited_property(self, child: JsonSchemaObject) -> JsonSchemaObject | None:
        """Record a completed sanitization only when the original producer returned one."""
        result: JsonSchemaObject | None = super()._sanitize_untyped_boolean_inherited_property(child)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if result is None:
            return None
        return self._record_schema_result(child, result, "inherited_constraint")  # pyright: ignore[reportUnknownArgumentType]

    def _merge_no_merge_inherited_property(
        self, parent: JsonSchemaObject, child: JsonSchemaObject, parent_ref: str
    ) -> JsonSchemaObject:
        """Retain parent and child inputs of the real no-merge materialization."""
        result: JsonSchemaObject = super()._merge_no_merge_inherited_property(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            parent, child, parent_ref
        )
        return self._record_inherited_property(parent, child, result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_inherited_property(
        self, parent: JsonSchemaObject, child: JsonSchemaObject, result: JsonSchemaObject
    ) -> JsonSchemaObject:
        if result is not child:
            self.schema_origins.derive(parent, result, "inherited_materialization", merge_mode=self.allof_merge_mode)
            self.schema_origins.derive(child, result, "inherited_constraint", merge_mode=self.allof_merge_mode)
        return result

    def parse_combined_schema(
        self, name: str, obj: JsonSchemaObject, path: list[str], target_attribute_name: str
    ) -> list[DataType]:
        """Follow one actual collect phase before the engine parses its filtered items."""
        self._pair_allof_union(name, obj, path)
        self._begin_combined_branch(name, obj, path, target_attribute_name)
        try:
            with self._resolution_producer():
                result: list[DataType] = super().parse_combined_schema(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                    name, obj, path, target_attribute_name
                )
        finally:
            self._combined_branches.pop()
        return result  # pyright: ignore[reportUnknownVariableType]

    @capture_errors
    def _begin_combined_branch(
        self, name: str, obj: JsonSchemaObject, path: list[str], keyword: str
    ) -> CombinedBranchFrame:
        match keyword:
            case "anyOf":
                originals = tuple(obj.anyOf)
            case "oneOf":
                originals = tuple(obj.oneOf)
            case "allOf":
                originals = tuple(obj.allOf)
            case _:
                msg = "An unobserved combined-schema keyword was supplied"
                raise BindingCaptureError(msg)
        frame = CombinedBranchFrame(name, obj, tuple(path), keyword, originals, [], [], {})
        self._combined_branches.append(frame)
        return frame

    def _preserve_inherited_materialized_type_shape(
        self, source: JsonSchemaObject, target: JsonSchemaObject
    ) -> JsonSchemaObject:
        """Observe the actual source/target pair without touching materialization markers."""
        result: JsonSchemaObject = super()._preserve_inherited_materialized_type_shape(source, target)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return self._record_materialized_shape(source, result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_materialized_shape(self, source: JsonSchemaObject, result: JsonSchemaObject) -> JsonSchemaObject:
        if self._combined_branches and (frame := self._combined_branches[-1]).collecting:
            frame.edges.append((source, result))
            self.schema_origins.derive(source, result, "combined_materialization")
            if (merged := frame.merged_inputs.get(id(source))) is not None and merged is not result:
                self.schema_origins.derive(merged, result, "combined_materialization")
        else:
            self.schema_origins.derive(source, result, "inherited_materialization")
        return result

    def _is_local_ref_false_schema(self, ref: str, *, use_builtin_facts: bool) -> bool:
        """Keep only false decisions the original combined loop actually requested."""
        result: bool = super()._is_local_ref_false_schema(ref, use_builtin_facts=use_builtin_facts)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return self._record_false_decision(ref, result=result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_false_decision(self, ref: str, *, result: bool) -> bool:
        if self._combined_branches and (frame := self._combined_branches[-1]).collecting:
            frame.false_decisions.append((ref, result))
        return result

    def _parse_combined_schema_items(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        combined_schemas: Sequence[JsonSchemaObject],
        variant_names: Sequence[str | None] | None,
    ) -> list[DataType]:
        """Verify occurrence correspondence before nested generated-item processing."""
        self._complete_combined_branch(name, obj, path, combined_schemas)
        registrations = len(self.binding_ledger.registrations)
        result: list[DataType] = super()._parse_combined_schema_items(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            name, obj, path, combined_schemas, variant_names
        )
        self._record_list_types(combined_schemas, result, registrations)  # pyright: ignore[reportUnknownArgumentType]
        return result  # pyright: ignore[reportUnknownVariableType]

    @capture_errors
    def _complete_combined_branch(
        self, name: str, obj: JsonSchemaObject, path: list[str], combined_schemas: Sequence[JsonSchemaObject]
    ) -> None:
        if not self._combined_branches:
            msg = "Combined items have no owning producer"
            raise BindingCaptureError(msg)
        frame = self._combined_branches[-1]
        if not frame.collecting or frame.parent is not obj or frame.name != name or frame.path != tuple(path):
            msg = "Combined items do not match their owning producer"
            raise BindingCaptureError(msg)
        frame.collecting = False
        completed_index = 0
        decision_index = 0
        for ordinal, original in enumerate(frame.originals):
            if original is False or (isinstance(original, JsonSchemaObject) and original.is_boolean_schema_false):
                continue
            if isinstance(original, JsonSchemaObject) and original.ref and original.ref.startswith("#"):
                if decision_index >= len(frame.false_decisions):
                    msg = "A combined reference has no observed false decision"
                    raise BindingCaptureError(msg)
                ref, excluded = frame.false_decisions[decision_index]
                decision_index += 1
                if ref != original.ref:
                    msg = "Combined reference decisions are out of occurrence order"
                    raise BindingCaptureError(msg)
                if excluded:
                    continue
            if completed_index >= len(combined_schemas):
                msg = "The completed combined sequence is missing an occurrence"
                raise BindingCaptureError(msg)
            target = combined_schemas[completed_index]
            completed_index += 1
            self._connect_combined_occurrence(frame, ordinal, original=original, target=target)
        if completed_index != len(combined_schemas) or decision_index != len(frame.false_decisions):
            msg = "Combined occurrence and producer event counts disagree"
            raise BindingCaptureError(msg)

    @capture_errors
    def _connect_combined_occurrence(
        self, frame: CombinedBranchFrame, ordinal: int, *, original: JsonSchemaObject | bool, target: JsonSchemaObject
    ) -> None:
        if original is True:
            self.schema_origins.derive_combined_common(frame.parent, target, frame.keyword, branch=original)
            self.schema_origins.pair_true_branch(frame.parent, frame.keyword, ordinal, target)
            self.schema_origins.derive(frame.parent, target, "combined_materialization")
            return
        if not isinstance(original, JsonSchemaObject):
            msg = "An unobserved combined node was supplied"
            raise BindingCaptureError(msg)
        if target is original:
            return
        reachable = {id(original)}
        for source, result in frame.edges:
            if id(source) in reachable:
                reachable.add(id(result))
        if id(target) not in reachable:
            msg = "A combined occurrence has no actual materialization edge"
            raise BindingCaptureError(msg)
        merged = frame.merged_inputs.get(id(original), original)
        if merged is not target:
            self.schema_origins.derive_combined_common(frame.parent, target, frame.keyword, branch=merged)
        self.schema_origins.derive(frame.parent, target, "combined_materialization")

    def _get_allof_parent_references(
        self, schema: JsonSchemaObject, *, inherited: Iterable[Reference] = (), defining_ref: str | None = None
    ) -> list[Reference]:
        """Exclude parent-map discovery from the direct allOf loader's resolver frame."""
        with self._resolution_producer():
            result: list[Reference] = super()._get_allof_parent_references(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                schema, inherited=inherited, defining_ref=defining_ref
            )
        return result  # pyright: ignore[reportUnknownVariableType]

    def _resolve_inherited_child_ref(self, ref: str, parent_ref: str) -> str:
        """Separate copied child-ref normalization from the containing loader's resolution."""
        with self._resolution_producer():
            result: str = super()._resolve_inherited_child_ref(ref, parent_ref)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return result  # pyright: ignore[reportUnknownVariableType]

    def _get_deferred_inherited_property_names(
        self, source_obj: JsonSchemaObject, parent_properties: dict[str, tuple[JsonSchemaObject | bool, str]]
    ) -> frozenset[str]:
        """Keep deferred-parent resolution separate from direct allOf occurrences."""
        with self._resolution_producer():
            result: frozenset[str] = super()._get_deferred_inherited_property_names(source_obj, parent_properties)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return result  # pyright: ignore[reportUnknownVariableType]

    def _append_missing_required_fields(
        self,
        *,
        required_names: list[str],
        fields: list[DataModelFieldBase],
        base_classes: list[Reference],
        class_name: str,
        declared_property_names: frozenset[str],
    ) -> None:
        """Keep required-field materialization out of the enclosing loader frame."""
        self._required_field_lists.append((required_names,))
        try:
            with self._resolution_producer():
                super()._append_missing_required_fields(  # pyright: ignore[reportUnknownMemberType]
                    required_names=required_names,
                    fields=fields,
                    base_classes=base_classes,
                    class_name=class_name,
                    declared_property_names=declared_property_names,
                )

        finally:
            self._required_field_lists.pop()

    def _parse_all_of_item(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- Preserve the existing producer signature.
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        fields: list[DataModelFieldBase],
        base_classes: list[Reference],
        required: list[str],
        union_models: list[Reference],
        declared_property_names: frozenset[str],
        inherited_parent_refs: Iterable[Reference] = (),
    ) -> None:
        """Own only direct loader resolutions and actual ref-union materializations."""
        frame = AllOfRefFrame(
            name,
            obj,
            tuple(path),
            tuple(
                (ordinal, node)
                for ordinal, node in enumerate(obj.allOf)
                if isinstance(node, JsonSchemaObject) and node.ref
            ),
            ReferenceProducerFrame([]),
            {},
        )
        self._allof_refs.append(frame)
        try:
            with self._resolution_producer(frame.producer):
                super()._parse_all_of_item(  # pyright: ignore[reportUnknownMemberType]
                    name,
                    obj,
                    path,
                    fields,
                    base_classes,
                    required,
                    union_models,
                    declared_property_names,
                    inherited_parent_refs,
                )
            self._verify_allof_resolutions(frame)
            self.schema_origins.register_required_composition(required, obj)
        finally:
            self._allof_refs.pop()

    @capture_errors
    def _verify_allof_resolutions(self, frame: AllOfRefFrame) -> None:
        if not self._allof_refs or self._allof_refs[-1] is not frame:
            msg = "The completed allOf producer is not the active frame"
            raise BindingCaptureError(msg)
        if len(frame.direct_refs) != len(frame.producer.resolutions):
            msg = "Direct allOf references and loader resolution counts disagree"
            raise BindingCaptureError(msg)
        for index, (_, node) in enumerate(frame.direct_refs):
            if frame.producer.resolutions[index].input != node.ref:
                msg = "Direct allOf loader resolutions are out of occurrence order"
                raise BindingCaptureError(msg)

    @capture_errors
    def _pair_allof_union(self, name: str, obj: JsonSchemaObject, path: list[str]) -> None:
        if not self._allof_refs:
            return
        frame = self._allof_refs[-1]
        if self.binding_resolver.resolution_owner is not frame.producer:
            return
        if any(obj is inline for inline in frame.obj.allOf):
            return
        if frame.name != name or frame.path != tuple(path) or not frame.producer.resolutions:
            msg = "An allOf union does not match its direct loader frame"
            raise BindingCaptureError(msg)
        occurrence = len(frame.producer.resolutions) - 1
        if occurrence >= len(frame.direct_refs):
            msg = "An allOf union has no matching reference occurrence"
            raise BindingCaptureError(msg)
        _, node = frame.direct_refs[occurrence]
        event = frame.producer.resolutions[occurrence]
        if event.input != node.ref:
            msg = "An allOf union is not the observed referenced declaration"
            raise BindingCaptureError(msg)
        if (prior := frame.materialized_parents.get(occurrence)) is not None and prior is not obj:
            msg = "Repeated allOf union processing changed the actual referenced schema"
            raise BindingCaptureError(msg)
        frame.materialized_parents[occurrence] = obj
        self._pair_inherited_declaration(event.output, obj, relation="ref_union_materialization")

    @capture_errors
    def _borrow_resolved_schema(self, resolved_ref: str, relation: SchemaRelation) -> SchemaOrigin | None:
        """Decode an actual resolved key against a document the engine already borrowed."""
        if resolved_ref in self.unresolved_references:
            return None
        document_uri, marker, fragment = resolved_ref.partition("#")
        if not marker or SPECIAL_PATH_MARKER in resolved_ref:
            return None
        if (document := self.source_lease.document_id(document_uri)) is None:
            return None
        root = self.source_lease.borrow(SourceLocation(document, "", "schema"))
        if not isinstance(root, dict):
            return None
        tokens = split_json_pointer(root, fragment)
        pointer = "/" + "/".join(token.replace("~", "~0").replace("/", "~1") for token in tokens) if tokens else ""
        location = SourceLocation(document, pointer, "schema")
        raw = _get_model_by_path_or_missing(root, tokens) if tokens else root
        return (
            SchemaOrigin(location, cast("dict[str, YamlValue] | bool", raw), relation)
            if isinstance(raw, (dict, bool))
            else None
        )

    @capture_errors
    def _pair_inherited_declaration(
        self, resolved_ref: str, schema: JsonSchemaObject, *, relation: SchemaRelation = "inherited_materialization"
    ) -> None:
        """Connect a supplied/cached schema to a declaration already read by the engine."""
        if (origin := self._borrow_resolved_schema(resolved_ref, relation)) is not None:
            self.schema_origins.pair(raw=origin.raw, obj=schema, location=origin.location, relation=relation)

    def _is_ref_circular(self, resolved_ref: str) -> bool:
        """Keep cycle-probe resolutions separate from the real reference loader."""
        with self._resolution_producer():
            result: bool = super()._is_ref_circular(resolved_ref)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return result  # pyright: ignore[reportUnknownVariableType]

    def _merge_ref_with_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject:
        """Borrow the actual referenced declaration and the real sibling merge return."""
        frame = ReferenceProducerFrame([])
        with self._resolution_producer(frame):
            result: JsonSchemaObject = super()._merge_ref_with_schema(obj)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return self._record_ref_merge(obj, result, frame)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_ref_merge(
        self, obj: JsonSchemaObject, result: JsonSchemaObject, frame: ReferenceProducerFrame
    ) -> JsonSchemaObject:
        if result is obj:
            return result
        expected_calls = 2  # The original cycle-check key and the actual loader key.
        if len(frame.resolutions) != expected_calls or any(event.input != obj.ref for event in frame.resolutions):
            msg = "A ref sibling merge has no matching direct loader resolution"
            raise BindingCaptureError(msg)
        if (origin := self._borrow_resolved_schema(frame.resolutions[1].output, "ref_sibling")) is not None:
            self.schema_origins.pair_merged_properties(origin, result)
        self.schema_origins.derive(obj, result, "ref_sibling")
        if self._combined_branches and (combined := self._combined_branches[-1]).collecting:
            combined.merged_inputs[id(obj)] = result
        return result

    def _merge_all_of_object(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Record source occurrences only when the original object merge materialized one."""
        frame = ReferenceProducerFrame([])
        with self._resolution_producer(frame):
            result: JsonSchemaObject | None = super()._merge_all_of_object(obj)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return self._record_allof_object_merge(obj, result, frame)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_allof_object_merge(
        self, obj: JsonSchemaObject, result: JsonSchemaObject | None, frame: ReferenceProducerFrame
    ) -> JsonSchemaObject | None:
        if result is None:
            return None
        self.schema_origins.derive(obj, result, "allof_merge", merge_mode=self.allof_merge_mode)
        reference_index = 0
        for item in obj.allOf:
            if not isinstance(item, JsonSchemaObject):
                continue
            if not item.ref:
                self.schema_origins.derive(item, result, "allof_merge", merge_mode=self.allof_merge_mode)
                continue
            if (
                reference_index >= len(frame.resolutions)
                or (event := frame.resolutions[reference_index]).input != item.ref
            ):
                msg = "An allOf object merge has no matching reference occurrence"
                raise BindingCaptureError(msg)
            reference_index += 1
            if (origin := self._borrow_resolved_schema(event.output, "allof_merge")) is not None:
                self.schema_origins.pair_merged_properties(origin, result)
        if reference_index != len(frame.resolutions):
            msg = "An allOf object merge has additional unowned resolutions"
            raise BindingCaptureError(msg)
        return result

    def _merge_all_of_root_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Retain the existing root producer and the helper events it actually issued."""
        frame = RootSchemaFrame(obj, ReferenceProducerFrame([]), [])
        self._root_schema_frames.append(frame)
        try:
            with self._resolution_producer(frame.references):
                result: JsonSchemaObject | None = super()._merge_all_of_root_schema(obj)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        finally:
            self._root_schema_frames.pop()
        return self._record_root_materialization(frame, result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_root_materialization(
        self, frame: RootSchemaFrame, result: JsonSchemaObject | None
    ) -> JsonSchemaObject | None:
        if result is None:
            return None
        self.root_materializations.append((frame, result))
        self.schema_origins.derive(frame.source, result, "allof_root_materialization", merge_mode=self.allof_merge_mode)
        if (
            len(frame.source.allOf) == 1
            and isinstance(source := frame.source.allOf[0], JsonSchemaObject)
            and not source.ref
        ):
            self.schema_origins.derive_preserved_shape(source, result, "allof_root_materialization")
        self._record_root_value_materializations(frame, result)
        return result

    def _record_root_value_materializations(self, frame: RootSchemaFrame, result: JsonSchemaObject) -> None:
        """Connect the actual merged mapping before ordinary array/root parsing starts."""
        roots = [
            event
            for event in frame.values
            if event.producer == "validation_keywords" and any(source is frame.source for source in event.sources)
        ]
        if not roots:
            return
        if len(roots) != 1 or not isinstance(raw := roots[0].result, dict):
            msg = "A root materialization has ambiguous validation producers"
            raise BindingCaptureError(msg)
        match frame.source.allOf, roots[0].sources, frame.references.resolutions:
            case (
                [JsonSchemaObject(ref=str() as ref) as branch],
                (JsonSchemaObject() as referenced, sibling, parent),
                [loader, context],
            ) if sibling is branch and parent is frame.source and loader.input == ref and context.input == ref:
                if (origin := self._borrow_resolved_schema(loader.output, "allof_root_materialization")) is not None:
                    self.schema_origins.pair(
                        raw=origin.raw, obj=referenced, location=origin.location, relation="allof_root_materialization"
                    )
            case _:
                msg = "A root materialization has no matching reference producer"
                raise BindingCaptureError(msg)
        producers: dict[int, dict[int, JsonSchemaObject | bool]] = {}
        for event in frame.values:
            if isinstance(event.result, dict):
                producers.setdefault(id(event.result), {}).update((id(source), source) for source in event.sources)
        self.schema_origins.pair_root_materialization(
            raw, result, {identity: tuple(sources.values()) for identity, sources in producers.items()}
        )

    def _merge_all_of_root_value_nodes(self, nodes: list[JsonSchemaObject | bool]) -> dict[str, object] | bool:
        """Capture the real raw-value return without reconstructing its intersection."""
        with self._resolution_producer():
            result: dict[str, object] | bool = super()._merge_all_of_root_value_nodes(nodes)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return self._record_root_value(nodes, "nodes", result=result)  # pyright: ignore[reportUnknownArgumentType]

    def _merge_all_of_root_value_children(self, nodes: list[JsonSchemaObject | bool]) -> dict[str, object] | bool:
        """Keep actual child-source order and actual raw output from the engine."""
        frame = RootValueChildrenFrame(tuple(nodes), ReferenceProducerFrame([]))
        self._root_value_children.append(frame)
        try:
            with self._resolution_producer(frame.references):
                result: dict[str, object] | bool = super()._merge_all_of_root_value_children(nodes)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        finally:
            self._root_value_children.pop()
        return self._record_root_value(nodes, "children", result=result)  # pyright: ignore[reportUnknownArgumentType]

    def _merge_all_of_root_validation_keywords(
        self, merged: dict[str, object], sources: list[JsonSchemaObject]
    ) -> None:
        """Observe the actual in-place mapping after the existing validation merge."""
        self._record_root_child_inputs(sources)
        with self._resolution_producer():
            super()._merge_all_of_root_validation_keywords(merged, sources)  # pyright: ignore[reportUnknownMemberType]
        self._record_root_value(sources, "validation_keywords", result=merged)

    @capture_errors
    def _record_root_child_inputs(self, children: list[JsonSchemaObject]) -> None:
        if not self._root_value_children or (frame := self._root_value_children[-1]).bound:
            return
        frame.bound = True
        completed = iter(children)
        resolutions = iter(frame.references.resolutions)
        for source in frame.sources:
            if isinstance(source, bool):
                continue
            if (child := next(completed, None)) is None:
                msg = "A root-value child merge lost an input occurrence"
                raise BindingCaptureError(msg)
            if not source.ref:
                if child is not source:
                    msg = "A root-value inline child changed identity before merging"
                    raise BindingCaptureError(msg)
                continue
            loader, context = next(resolutions, None), next(resolutions, None)
            sibling = next(completed, None)
            if loader is None or context is None or loader.input != source.ref or context.input != source.ref:
                msg = "A root-value child has no matching loader resolution"
                raise BindingCaptureError(msg)
            if sibling is None or sibling is source or sibling.ref is not None:
                msg = "A root-value reference has no actual sibling copy"
                raise BindingCaptureError(msg)
            if (origin := self._borrow_resolved_schema(loader.output, "allof_root_materialization")) is not None:
                self.schema_origins.pair(
                    raw=origin.raw, obj=child, location=origin.location, relation="allof_root_materialization"
                )
            self.schema_origins.derive_preserved_shape(source, sibling, "allof_root_materialization")
        if next(completed, None) is not None or next(resolutions, None) is not None:
            msg = "A root-value child merge has unowned inputs or resolutions"
            raise BindingCaptureError(msg)

    @capture_errors
    def _record_root_value(
        self,
        sources: Sequence[JsonSchemaObject | bool],
        producer: Literal["nodes", "children", "validation_keywords"],
        *,
        result: dict[str, object] | bool,
    ) -> dict[str, object] | bool:
        if self._root_schema_frames:
            self._root_schema_frames[-1].values.append(RootValueObservation(tuple(sources), result, producer))
        return result

    def _parse_object_common_part(  # ruff: ignore[too-many-arguments] -- Preserve the existing producer signature.
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        *,
        ignore_duplicate_model: bool,
        fields: list[DataModelFieldBase],
        base_classes: list[Reference],
        required: list[str],
    ) -> DataType:
        """Retain both actual required inputs of the final object field producer."""
        self._required_field_lists.append((required, obj.required))
        try:
            with self._resolution_producer():
                result: DataType = super()._parse_object_common_part(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                    name,
                    obj,
                    path,
                    ignore_duplicate_model=ignore_duplicate_model,
                    fields=fields,
                    base_classes=base_classes,
                    required=required,
                )
        finally:
            self._required_field_lists.pop()
        return result  # pyright: ignore[reportUnknownVariableType]

    def _build_missing_required_field(
        self,
        required_field_name: str,
        excludes: set[str],
        base_classes: list[Reference],
        class_name: str,
        inherited_fields: dict[str, DataModelFieldBase] | None = None,
    ) -> DataModelFieldBase:
        """Bind required-only fields to the list occurrences that actually produced them."""
        result: DataModelFieldBase = super()._build_missing_required_field(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            required_field_name, excludes, base_classes, class_name, inherited_fields
        )
        return self._record_required_field(required_field_name, result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_required_field(self, name: str, result: DataModelFieldBase) -> DataModelFieldBase:
        locations = tuple(
            dict.fromkeys(
                location
                for names in (self._required_field_lists[-1] if self._required_field_lists else ())
                for location in self.schema_origins.required_locations(names, name)
            )
        )
        self.synthetic_fields.append(
            SyntheticFieldObservation(self.binding_ledger.identity(result), "required_only", locations, name)
        )
        return result

    def _get_typed_additional_properties_field(
        self, class_name: str, obj: JsonSchemaObject, path: list[str]
    ) -> DataModelFieldBase | None:
        """Record only the typed-extra field actually returned by the backend producer."""
        result: DataModelFieldBase | None = super()._get_typed_additional_properties_field(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            class_name, obj, path
        )
        if result is None:
            return None
        return self._record_additional_field(obj, result, "additional_properties")  # pyright: ignore[reportUnknownArgumentType]

    def _get_additional_properties_root_field(
        self, name: str, obj: JsonSchemaObject, path: list[str]
    ) -> DataModelFieldBase:
        """Keep dictionary root provenance on additionalProperties rather than a fake property."""
        result: DataModelFieldBase = super()._get_additional_properties_root_field(name, obj, path)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return self._record_additional_field(obj, result, "root_value")  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_root_fields(self, obj: JsonSchemaObject, fields: list[DataModelFieldBase], class_name: str) -> None:
        locations = tuple(origin.location for origin in self.schema_origins.origins(obj))
        for field in fields:
            identity = self.binding_ledger.identity(field)
            if identity not in self._additional_root_fields:
                self.synthetic_fields.append(SyntheticFieldObservation(identity, "root_value", locations, None))
            if identity not in self.field_constructions:
                self.field_constructions[identity] = FieldConstructionObservation(
                    identity,
                    obj,
                    field.required,
                    field.default,
                    field.has_default,
                    field.use_default_with_required,
                    field.original_name,
                    class_name,
                    None,
                    self._observe_preexisting_null(field.data_type),
                    field.data_type,
                )

    @capture_errors
    def _record_additional_field(
        self, obj: JsonSchemaObject, result: DataModelFieldBase, kind: Literal["additional_properties", "root_value"]
    ) -> DataModelFieldBase:
        identity = self.binding_ledger.identity(result)
        if kind == "root_value":
            self._additional_root_fields.add(identity)
        self.synthetic_fields.append(
            SyntheticFieldObservation(
                identity,
                kind,
                self.schema_origins.keyword_locations(obj, "additionalProperties"),
                None,
            )
        )
        return result

    def parse_list_item(
        self,
        name: str,
        target_items: Sequence[JsonSchemaObject | bool],
        path: list[str],
        parent: JsonSchemaObject,
        singular_name: bool = True,  # ruff: ignore[boolean-type-hint-positional-argument, boolean-default-value-positional-argument] -- Preserve the existing producer signature.
    ) -> list[DataType]:
        """Retain actual element and union-branch returns without changing guarded item parsing."""
        registrations = len(self.binding_ledger.registrations)
        result: list[DataType] = super().parse_list_item(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            name, target_items, path, parent, singular_name
        )
        self._record_list_types(target_items, result, registrations)  # pyright: ignore[reportUnknownArgumentType]
        return result  # pyright: ignore[reportUnknownVariableType]

    @capture_errors
    def _record_list_types(
        self, schemas: Sequence[JsonSchemaObject | bool], results: list[DataType], start: int
    ) -> None:
        references = {registration.reference for registration in self.binding_ledger.registrations[start:]}
        for schema, result in zip((schema for schema in schemas if schema is not False), results, strict=True):
            if isinstance(schema, JsonSchemaObject):
                root_value = None
                if (
                    result.reference is not None
                    and self.binding_ledger.identity(result.reference) in references
                    and isinstance(model := result.reference.source, DataModel)
                    and (model.IS_ROOT_MODEL or model.IS_ALIAS)
                ):
                    self._record_root_fields(schema, model.fields, model.reference.name)
                    if len(model.fields) == 1:
                        root_value = self.binding_ledger.identity(model.fields[0].data_type)
                self.schema_types.append(SchemaTypeObservation(schema, result, root_value=root_value))

    def get_data_type(self, obj: JsonSchemaObject) -> DataType:
        """Retain a primitive producer's actual return for nested schema occurrences."""
        result: DataType = super().get_data_type(obj)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return self._record_schema_type(obj, result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_schema_type(self, schema: JsonSchemaObject, result: DataType) -> DataType:
        self.schema_types.append(SchemaTypeObservation(schema, result))
        return result

    def _parse_additional_properties_value(
        self,
        name: str,
        path: list[str],
        parent: JsonSchemaObject,
        *,
        additional_properties: JsonSchemaObject,
        constrained_name: str | None = None,
    ) -> DataType:
        """Observe the original additional-property value without overriding its guarded helpers."""
        result: DataType = super()._parse_additional_properties_value(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            name, path, parent, additional_properties=additional_properties, constrained_name=constrained_name
        )
        return self._record_schema_type(additional_properties, result)  # pyright: ignore[reportUnknownArgumentType]

    def parse_array_fields(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        singular_name: bool = True,  # ruff: ignore[boolean-type-hint-positional-argument, boolean-default-value-positional-argument] -- Preserve the producer signature.
        use_annotated: bool | None = None,  # ruff: ignore[boolean-type-hint-positional-argument] -- Preserve the producer signature.
    ) -> DataModelFieldBase:
        """Keep the current array source only while its ordinary fallback producer runs."""
        self._array_sources.append(obj)
        try:
            return super().parse_array_fields(name, obj, path, singular_name, use_annotated)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        finally:
            self._array_sources.pop()

    def _fallback_array_item_data_types(self) -> list[DataType]:
        """Bind an explicit true items occurrence to the real fallback Any return."""
        result: list[DataType] = super()._fallback_array_item_data_types()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        self._record_array_fallback(result)  # pyright: ignore[reportUnknownArgumentType]
        return result  # pyright: ignore[reportUnknownVariableType]

    @capture_errors
    def _record_array_fallback(self, result: list[DataType]) -> None:
        if not self._array_sources or len(result) != 1:
            return
        obj = self._array_sources[-1]
        if obj.items is True:
            self.schema_types.append(
                SchemaTypeObservation(None, result[0], self.schema_origins.keyword_locations(obj, "items"))
            )

    def _create_synthetic_enum_obj(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- Preserve the original producer signature.
        self,
        original: JsonSchemaObject,
        enum_values: list[object],
        varnames: list[str],
        descriptions: list[str],
        enum_type: str | None,
        nullable: bool,  # ruff: ignore[boolean-type-hint-positional-argument] -- Preserve the existing producer signature.
    ) -> JsonSchemaObject:
        """Retain the actual enum producer relation without reevaluating const branches."""
        result: JsonSchemaObject = super()._create_synthetic_enum_obj(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            original, enum_values, varnames, descriptions, enum_type, nullable
        )
        return self._record_schema_result(original, result, "synthetic_value")  # pyright: ignore[reportUnknownArgumentType]

    def set_additional_properties(self, path: str, obj: JsonSchemaObject) -> None:
        """Own the actual type producer used for TypedDict extra-items metadata."""
        self._additional_type_frames.append(AdditionalTypeFrame(path, obj, None))
        try:
            super().set_additional_properties(path, obj)  # pyright: ignore[reportUnknownMemberType]
        finally:
            self._additional_type_frames.pop()

    def _update_variant_additional_properties_metadata(
        self, reference_path: str, obj: JsonSchemaObject, suffix: Literal["Request", "Response"]
    ) -> None:
        """Keep each directional metadata producer attached to its real owner path."""
        self._additional_type_frames.append(AdditionalTypeFrame(reference_path, obj, suffix))
        try:
            super()._update_variant_additional_properties_metadata(reference_path, obj, suffix)  # pyright: ignore[reportUnknownMemberType]
        finally:
            self._additional_type_frames.pop()

    def _build_lightweight_type(
        self,
        schema: JsonSchemaObject,
        depth: int = 0,
        visited: frozenset[int] | None = None,
        max_depth: int = 3,
        max_union_elements: int = 5,
    ) -> DataType | None:
        """Capture an already requested type before the original caller renders it."""
        result: DataType | None = super()._build_lightweight_type(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            schema, depth, visited, max_depth, max_union_elements
        )
        return self._record_additional_type(schema, result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_additional_type(self, schema: JsonSchemaObject, result: DataType | None) -> DataType | None:
        if result is not None and self._additional_type_frames:
            frame = self._additional_type_frames[-1]
            if frame.schema.additionalProperties is schema:
                self.additional_types.append(AdditionalTypeObservation(frame, result))
        return result

    def parse_pattern_properties(
        self,
        name: str,
        pattern_properties: dict[str, JsonSchemaObject | bool],
        path: list[str],
        *,
        property_names: JsonSchemaObject | bool | None = None,
    ) -> DataType:
        """Keep the exact pattern input and completed dictionary type without another getter."""
        result: DataType = super().parse_pattern_properties(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            name, pattern_properties, path, property_names=property_names
        )
        return self._record_pattern_type(pattern_properties, result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_pattern_type(self, patterns: dict[str, JsonSchemaObject | bool], result: DataType) -> DataType:
        self.pattern_types.append(PatternTypeObservation(tuple(patterns.items()), result))
        return result

    def _collect_pattern_property_validators(
        self, name: str, obj: JsonSchemaObject, path: list[str]
    ) -> tuple[list[tuple[str, DataType]], list[str], DataType | None, bool]:
        """Retain already constructed runtime-validation types before string conversion."""
        result: tuple[list[tuple[str, DataType]], list[str], DataType | None, bool] = (  # pyright: ignore[reportUnknownVariableType]
            super()._collect_pattern_property_validators(  # pyright: ignore[reportUnknownMemberType]
                name, obj, path
            )
        )
        return self._record_pattern_validators(obj, result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_pattern_validators(
        self, obj: JsonSchemaObject, result: tuple[list[tuple[str, DataType]], list[str], DataType | None, bool]
    ) -> tuple[list[tuple[str, DataType]], list[str], DataType | None, bool]:
        patterns, rejected, additional, allow_unmatched = result
        self.pattern_validators.append(
            PatternValidatorObservation(obj, tuple(patterns), tuple(rejected), additional, allow_unmatched)
        )
        return result

    def _create_discriminator_data_type(
        self,
        enum_source: Enum | None,
        discriminator_values: list[DiscriminatorValue],
        discriminator_model: DataModel,
        imports: Imports,
    ) -> DataType:
        """Observe enum/member identity without calling the member finder again."""
        result: DataType = super()._create_discriminator_data_type(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            enum_source, discriminator_values, discriminator_model, imports
        )
        return self._record_discriminator_type(enum_source, discriminator_model, result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_discriminator_type(self, enum: Enum | None, model: DataModel, result: DataType) -> DataType:
        self.discriminator_types.append(
            DiscriminatorTypeObservation(
                self.binding_ledger.identity(enum) if enum is not None else None,
                tuple((field.name, self.binding_ledger.identity(field)) for field in enum.fields)
                if enum is not None
                else (),
                self.binding_ledger.identity(model),
                result,
                self.binding_ledger.identity(enum.reference) if enum is not None else None,
            )
        )
        return result

    def _parse_inherited_schema_fields(
        self,
        reference: Reference,
        schema: JsonSchemaObject,
        parent_refs: list[Reference],
        parent_fields: list[DataModelFieldBase],
    ) -> list[DataModelFieldBase]:
        """Establish forward-parent origins before title changes or path-free field parsing."""
        self._pair_inherited_declaration(reference.path, schema)
        result: list[DataModelFieldBase] = super()._parse_inherited_schema_fields(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            reference, schema, parent_refs, parent_fields
        )
        return result  # pyright: ignore[reportUnknownVariableType]

    def _get_inherited_property_map(
        self, base_classes: list[Reference]
    ) -> dict[str, tuple[JsonSchemaObject | bool, str]]:
        """Retain actual cached validated parents without loading or linearizing again."""
        with self._resolution_producer():
            result: dict[str, tuple[JsonSchemaObject | bool, str]] = super()._get_inherited_property_map(base_classes)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return self._record_inherited_map(result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_inherited_map(
        self, result: dict[str, tuple[JsonSchemaObject | bool, str]]
    ) -> dict[str, tuple[JsonSchemaObject | bool, str]]:
        for parent_ref in dict.fromkeys(parent_ref for _, parent_ref in result.values()):
            if (schema := self._inherited_schema_cache.get(parent_ref)) is not None:
                self._pair_inherited_declaration(parent_ref, schema)
        if self._inherited_merges:
            self._inherited_merges[-1].parents = result
        return result

    def _merge_properties_with_parent_constraints(
        self,
        child_obj: JsonSchemaObject,
        base_classes: list[Reference],
        parent_properties: dict[str, tuple[JsonSchemaObject | bool, str]] | None = None,
        deferred_property_names: frozenset[str] | None = None,
    ) -> JsonSchemaObject:
        """Observe the real effective parent map and result without repeating a merge."""
        frame = InheritedMergeFrame(child_obj, parent_properties)
        self._inherited_merges.append(frame)
        try:
            with self._resolution_producer():
                result: JsonSchemaObject = super()._merge_properties_with_parent_constraints(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                    child_obj, base_classes, parent_properties, deferred_property_names
                )
        finally:
            self._inherited_merges.pop()
        return self._record_parent_constraint_merge(frame, result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_parent_constraint_merge(self, frame: InheritedMergeFrame, result: JsonSchemaObject) -> JsonSchemaObject:
        if result is frame.child:
            return result
        self.schema_origins.derive(frame.child, result, "inherited_constraint", merge_mode=self.allof_merge_mode)
        for name, (parent, _) in (frame.parents or {}).items():
            target = (result.properties or {}).get(name)
            if isinstance(parent, JsonSchemaObject) and isinstance(target, JsonSchemaObject):
                self.schema_origins.derive(
                    parent, target, "inherited_materialization", merge_mode=self.allof_merge_mode
                )
        return result

    def _effective_default_state(
        self, field_name: str, default: object, *, has_default: bool, required: bool, class_name: str | None
    ) -> tuple[object, bool, bool]:
        """Observe the original default-policy call without executing another resolver."""
        result: tuple[object, bool, bool] = super()._effective_default_state(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            field_name, default, has_default=has_default, required=required, class_name=class_name
        )
        return self._record_effective_default(
            field_name,
            class_name,
            default,
            has_default=has_default,
            required=required,
            result=result,  # pyright: ignore[reportUnknownArgumentType]
        )

    @capture_errors
    def _record_effective_default(  # ruff: ignore[too-many-arguments] -- Preserve the actual default-policy inputs and return.
        self,
        field_name: str,
        class_name: str | None,
        default: object,
        *,
        has_default: bool,
        required: bool,
        result: tuple[object, bool, bool],
    ) -> tuple[object, bool, bool]:
        if not self.binding_resolver.default_resolutions:
            msg = "An effective field default has no actual resolver result"
            raise BindingCaptureError(msg)
        resolution = self.binding_resolver.default_resolutions[-1]
        if (
            resolution.field_name != field_name
            or resolution.class_name != class_name
            or resolution.original is not default
        ):
            msg = "An effective field default does not match its actual resolver call"
            raise BindingCaptureError(msg)
        observation = EffectiveDefaultObservation(
            field_name, class_name, default, has_default, required, result, resolution
        )
        self.effective_defaults.append(observation)
        self._pending_field_default = observation
        return observation.result

    @capture_errors
    def _field_construction_inputs(
        self,
        field_type: DataType,
        original_name: str | None,
        class_name: str | None,
    ) -> tuple[EffectiveDefaultObservation | None, PreexistingNullObservation]:
        policy, self._pending_field_default = self._pending_field_default, None
        if policy is not None and (policy.field_name != original_name or policy.class_name != class_name):
            msg = "A field construction does not match its pending default producer"
            raise BindingCaptureError(msg)
        return policy, self._observe_preexisting_null(field_type)

    def _observe_preexisting_null(self, field_type: DataType) -> PreexistingNullObservation:
        """Read stored type flags before ordinary rendering can introduce optionality."""
        pending = [field_type]
        seen: set[int] = set()
        references: dict[GraphObjectId, None] = {}
        opaque = False
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            if current.is_optional or current.type == "None":
                return PreexistingNullObservation(explicit=True, references=(), opaque=False)
            if any((
                current.is_list,
                current.is_dict,
                current.is_set,
                current.is_frozen_set,
                current.is_mapping,
                current.is_sequence,
                current.is_tuple,
            )):
                continue
            if current.reference is not None:
                references[self.binding_ledger.identity(current.reference)] = None
            opaque |= current.python_type is not None
            pending.extend(current.data_types)
        return PreexistingNullObservation(explicit=False, references=tuple(references), opaque=opaque)

    def get_object_field(  # ruff: ignore[too-many-arguments] -- Preserve the existing field producer signature.
        self,
        *,
        field_name: str | None,
        field: JsonSchemaObject | None,
        required: bool,
        field_type: DataType,
        alias: str | list[str] | None,
        original_field_name: str | None,
        effective_default: object = None,
        effective_has_default: bool | None = None,
        use_default_with_required: bool = False,
        class_name: str | None = None,
    ) -> DataModelFieldBase:
        """Retain the real field producer before copy/inheritance and rendering."""
        default_policy, preexisting_null = self._field_construction_inputs(field_type, original_field_name, class_name)
        result: DataModelFieldBase = super().get_object_field(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            field_name=field_name,
            field=field,
            required=required,
            field_type=field_type,
            alias=alias,
            original_field_name=original_field_name,
            effective_default=effective_default,
            effective_has_default=effective_has_default,
            use_default_with_required=use_default_with_required,
            class_name=class_name,
        )
        return self._record_field_construction(
            result,  # pyright: ignore[reportUnknownArgumentType]
            schema=field,
            effective_default=effective_default,
            effective_has_default=effective_has_default,
            original_name=original_field_name,
            class_name=class_name,
            required=required,
            use_default_with_required=use_default_with_required,
            default_policy=default_policy,
            preexisting_null=preexisting_null,
        )

    @capture_errors
    def _record_field_construction(  # ruff: ignore[too-many-arguments] -- Retain actual constructor arguments at one failure boundary.
        self,
        field: DataModelFieldBase,
        *,
        schema: JsonSchemaObject | None,
        effective_default: object,
        effective_has_default: bool | None,
        original_name: str | None,
        class_name: str | None,
        required: bool,
        use_default_with_required: bool,
        default_policy: EffectiveDefaultObservation | None,
        preexisting_null: PreexistingNullObservation,
    ) -> DataModelFieldBase:
        identity = self.binding_ledger.identity(field)
        if schema is not None:
            self.schema_types.append(SchemaTypeObservation(schema, field.data_type))
        self.field_constructions[identity] = FieldConstructionObservation(
            identity,
            schema,
            required,
            effective_default,
            effective_has_default,
            use_default_with_required,
            original_name,
            class_name,
            default_policy,
            preexisting_null,
            field.data_type,
        )
        if self._parameter_frames:
            frame = self._parameter_frames[-1]
            reference = self.model_resolver.references.get(self.model_resolver.join_path(frame.path))
            if frame.current is not None and reference is not None and class_name == reference.name:
                self.parameter_fields.append(ParameterFieldObservation(frame.operation, frame.current, field))
                if schema is not None:
                    self.field_origins[identity] = FieldOriginObservation(
                        identity, original_name or "", self.schema_origins.origins(schema), required
                    )
        return field

    def parse_object_fields(
        self, obj: JsonSchemaObject, path: list[str], module_name: str | None = None, class_name: str | None = None
    ) -> list[DataModelFieldBase]:
        """Observe outer returned fields, including existing discriminator replacements."""
        self._required_field_lists.append((obj.required,))
        try:
            with self._resolution_producer():
                result: list[DataModelFieldBase] = super().parse_object_fields(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                    obj, path, module_name, class_name
                )
        finally:
            self._required_field_lists.pop()
        return self._record_object_fields(obj, result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_object_fields(
        self, obj: JsonSchemaObject, fields: list[DataModelFieldBase]
    ) -> list[DataModelFieldBase]:
        if obj.properties is None:
            return fields
        for field in fields:
            if (wire_name := field.original_name) is None or wire_name not in obj.properties:
                continue
            identity = self.binding_ledger.identity(field)
            origins = self.schema_origins.property_origins(obj, wire_name)
            self.field_origins[identity] = FieldOriginObservation(
                identity, wire_name, origins, wire_name in obj.required
            )
            if isinstance(obj.properties[wire_name], bool):
                self.schema_types.append(
                    SchemaTypeObservation(None, field.data_type, tuple(origin.location for origin in origins))
                )
        return fields

    def _process_path_items(  # ruff: ignore[too-many-arguments] -- Preserve the existing hook signature.
        self,
        items: dict[str, dict[str, YamlValue]],
        base_path: list[str],
        scope_name: str,
        global_parameters: list[dict[str, YamlValue]],
        security: list[dict[str, list[str]]] | None,
        *,
        strip_leading_slash: bool = True,
        apply_path_filter: bool = True,
    ) -> None:
        """Borrow actual legacy inputs and preserve the original loop and late lookup."""
        self._begin_legacy_scope(items, base_path, scope_name, global_parameters, security)
        try:
            super()._process_path_items(  # pyright: ignore[reportUnknownMemberType]
                items,
                base_path,
                scope_name,
                global_parameters,
                security,
                strip_leading_slash=strip_leading_slash,
                apply_path_filter=apply_path_filter,
            )
        finally:
            self._legacy_scopes.pop()

    @capture_errors
    def _begin_legacy_scope(
        self,
        items: dict[str, dict[str, YamlValue]],
        base_path: list[str],
        scope_name: str,
        global_parameters: list[dict[str, YamlValue]],
        security: list[dict[str, list[str]]] | None,
    ) -> None:
        """Retain only observed scope inputs before the original traversal begins."""
        candidates = tuple(
            LegacyOperationCandidate(ApiDeclarationId("/".join(base_path), (scope_name, key, method)), method, raw)
            for key, item in items.items()
            if "$ref" not in item
            for method, raw in item.items()
            if isinstance(raw, dict)
        )
        frame = LegacyPathItemsFrame(items, tuple(base_path), scope_name, global_parameters, security, candidates)
        self.legacy_scopes.append(frame)
        self._legacy_scopes.append(frame)

    def parse_operation(self, raw_operation: dict[str, YamlValue], path: list[str]) -> None:
        """Retain every matching declaration; ambiguity never selects the first one."""
        self._begin_legacy_operation(raw_operation, path)
        try:
            super().parse_operation(raw_operation, path)  # pyright: ignore[reportUnknownMemberType]
        finally:
            self._legacy_operations.pop()

    @capture_errors
    def _begin_legacy_operation(self, raw_operation: dict[str, YamlValue], path: list[str]) -> None:
        """Latch candidate-correlation failures without catching engine failures."""
        candidates = (
            tuple(
                candidate
                for candidate in self._legacy_scopes[-1].candidates
                if candidate.method == path[-1] and _same_legacy_operation(candidate.raw, raw_operation)
            )
            if self._legacy_scopes
            else ()
        )
        state: Literal["known", "unavailable", "ambiguous"] = (
            "known" if len(candidates) == 1 else "ambiguous" if candidates else "unavailable"
        )
        observation = LegacyOperationObservation(raw_operation, candidates, state, tuple(path))
        self.legacy_operations.append(observation)
        self._legacy_operations.append(observation)

    @capture_errors
    def _observe_type(
        self,
        data_type: DataType,
        *,
        path: list[str] | None = None,
        ref: str | None = None,
        resolutions: tuple[str, ...] = (),
    ) -> None:
        """Attach only an existing return to its active declaration occurrence."""
        frame = self.declaration_frames[-1] if isinstance(self, ApiOpenAPIParser) and self.declaration_frames else None
        self.binding_ledger.identity(data_type)
        self.type_observations.append(
            TypeObservation(
                data_type,
                tuple(path) if path is not None else None,
                ref,
                frame,
                self._legacy_operations[-1] if self._legacy_operations else None,
                resolutions,
            )
        )

    def _parse_schema_or_ref(self, name: str, schema: MediaSchema, path: list[str]) -> DataType:
        """Keep the actual schema-use return, including discriminator-expanded types."""
        self._pair_legacy_media_schema(schema, path)
        registrations = len(self.binding_ledger.registrations)
        result: DataType = super()._parse_schema_or_ref(name, schema, path)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        self._record_media_root(schema, result, registrations)  # pyright: ignore[reportUnknownArgumentType]
        self._observe_type(result, path=path)  # pyright: ignore[reportUnknownArgumentType]
        return result  # pyright: ignore[reportUnknownVariableType]

    def get_ref_model(self, ref: str) -> dict[str, YamlValue]:
        """Borrow the actual legacy object-resolution return without another lookup."""
        start = len(self.binding_resolver.resolutions)
        result: dict[str, YamlValue] = super().get_ref_model(ref)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return self._record_legacy_ref_object(ref, result, start)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_legacy_ref_object(self, ref: str, result: dict[str, YamlValue], start: int) -> dict[str, YamlValue]:
        if not self._legacy_operations:
            return result
        for event in self.binding_resolver.resolutions[start:]:
            if (
                event.input == ref
                and event.operation == "resolve_ref"
                and (origin := self._borrow_resolved_schema(event.output, "validated_child")) is not None
                and origin.raw is result
            ):
                self.legacy_ref_objects[id(self._legacy_operations[-1]), ref] = origin
                break
        return result

    def _media_schema_path(self, path: list[str], *, from_item_schema: bool) -> list[str]:
        """Retain the actual media path before the engine adds its synthetic suffix."""
        result: list[str] = super()._media_schema_path(path, from_item_schema=from_item_schema)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return self._record_media_path(path, result, from_item_schema=from_item_schema)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_media_path(self, path: list[str], result: list[str], *, from_item_schema: bool) -> list[str]:
        if self._legacy_operations:
            self._media_paths[tuple(result)] = (tuple(path), from_item_schema)
        return result

    @capture_errors
    def _pair_legacy_media_schema(self, schema: MediaSchema, path: list[str]) -> None:
        if (
            not self._legacy_operations
            or not isinstance(schema, JsonSchemaObject)
            or (media_path := self._media_paths.get(tuple(path))) is None
            or (operation := self._legacy_operations[-1]).origin_state != "known"
        ):
            return
        original_path, projected = media_path
        keyword = "itemSchema" if projected else "schema"
        if (origin := self._legacy_media_source(operation, original_path, keyword)) is None:
            return
        if projected:
            self.schema_origins.pair_item_projection(
                raw=origin.raw, obj=schema, location=origin.location, reference=False
            )
        else:
            self.schema_origins.pair(raw=origin.raw, obj=schema, location=origin.location)

    def _legacy_media_source(
        self, operation: LegacyOperationObservation, path: tuple[str, ...], keyword: str
    ) -> SchemaOrigin | None:
        """Join the original use to its direct or actually resolved media occurrence."""
        declaration = operation.candidates[0]
        raw: YamlValue
        match path[len(operation.engine_path) :]:
            case ("requestBody", media):
                raw = declaration.raw.get("requestBody")
                owner = (*declaration.declaration.tokens, "requestBody")
            case ("responses", status, media):
                responses = declaration.raw.get("responses")
                raw = borrow_source_member(responses, status) if isinstance(responses, dict) else None
                owner = (*declaration.declaration.tokens, "responses", status)
            case _:
                return None
        if not isinstance(raw, dict):
            return None
        if isinstance(ref := raw.get("$ref"), str):
            if (origin := self.legacy_ref_objects.get((id(operation), ref))) is None:
                return None
            raw, parent = origin.raw, origin.location
        else:
            if (document := self.source_lease.document_id(declaration.declaration.document)) is None:
                return None
            parent = SourceLocation(
                document, "/" + "/".join(token.replace("~", "~0").replace("/", "~1") for token in owner), "schema"
            )
        if (
            not isinstance(raw, dict)
            or not isinstance(content := raw.get("content"), dict)
            or not isinstance(medium := content.get(media), dict)
            or not isinstance(original := medium.get(keyword), (dict, bool))
        ):
            return None
        location = SourceLocation(
            parent.document,
            parent.pointer + "/content/" + media.replace("~", "~0").replace("/", "~1") + "/" + keyword,
            "schema",
        )
        return SchemaOrigin(location, original, "validated_child")

    @capture_errors
    def _record_media_root(self, schema: MediaSchema, result: DataType, start: int) -> None:
        if not isinstance(schema, JsonSchemaObject) or result.reference is None:
            return
        if (
            self.binding_ledger.identity(result.reference)
            in {registration.reference for registration in self.binding_ledger.registrations[start:]}
            and isinstance(model := result.reference.source, DataModel)
            and (model.IS_ROOT_MODEL or model.IS_ALIAS)
        ):
            self._record_root_fields(schema, model.fields, model.reference.name)

    def parse_all_parameters(
        self, name: str, parameters: list[ReferenceObject | ParameterObject], path: list[str]
    ) -> DataType | None:
        """Observe the real parameter producer without adding a Parameters scope."""
        if not self._legacy_operations:
            return super().parse_all_parameters(name, parameters, path)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        operation = self._legacy_operations[-1]
        raw = operation.effective.get("parameters", [])
        inputs = (
            tuple(zip(parameters, raw, strict=True)) if isinstance(raw, list) and len(raw) == len(parameters) else ()
        )
        self._parameter_frames.append(LegacyParameterFrame(operation, inputs, tuple(path)))
        try:
            return super().parse_all_parameters(name, parameters, path)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        finally:
            self._parameter_frames.pop()

    def resolve_object(self, obj: ReferenceObject | BaseModelT, object_type: type[BaseModelT]) -> BaseModelT:
        """Keep the actual resolved parameter schema and original declaration together."""
        if object_type is not ParameterObject or not self._parameter_frames:
            return super().resolve_object(obj, object_type)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        producer = ReferenceProducerFrame([])
        with self._resolution_producer(producer):
            result = super().resolve_object(obj, object_type)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        self._record_resolved_parameter(obj, result, producer)  # pyright: ignore[reportUnknownArgumentType]
        return result  # pyright: ignore[reportUnknownVariableType]

    @capture_errors
    def _record_resolved_parameter(self, original: object, result: object, producer: ReferenceProducerFrame) -> None:
        if isinstance(result, ParameterObject):
            self._record_parameter_schema(original, result, producer)

    @capture_errors
    def _record_parameter_schema(
        self, original: object, parameter: ParameterObject, producer: ReferenceProducerFrame
    ) -> None:
        frame = self._parameter_frames[-1]
        frame.current = parameter
        raws = tuple(raw for validated, raw in frame.inputs if validated is original)
        if len(raws) != 1 or not isinstance(raw := raws[0], dict):
            return
        origins: list[SchemaOrigin] = []
        if isinstance(ref := raw.get("$ref"), str):
            origins.extend(
                origin
                for event in producer.resolutions
                if event.input == ref
                and (origin := self._borrow_resolved_schema(event.output, "validated_child")) is not None
            )
        else:
            origins.extend(self._direct_parameter_origins(frame, raw))
        for origin in dict.fromkeys(origin.location for origin in origins):
            raw_parameter = self.source_lease.borrow(origin)
            if not isinstance(raw_parameter, dict):
                continue
            if parameter.schema_ is not None and isinstance(schema := raw_parameter.get("schema"), (dict, bool)):
                self.schema_origins.pair(
                    raw=schema,
                    obj=parameter.schema_,
                    location=SourceLocation(origin.document, origin.pointer + "/schema", "schema"),
                    relation="validated_child",
                )
            content = raw_parameter.get("content")
            if not isinstance(content, dict):
                continue
            for media, value in parameter.content.items():
                raw_media = content.get(media)
                if not isinstance(raw_media, dict):
                    continue
                if isinstance(value.schema_, JsonSchemaObject) and isinstance(
                    schema := raw_media.get("schema"), (dict, bool)
                ):
                    self.schema_origins.pair(
                        raw=schema,
                        obj=value.schema_,
                        location=SourceLocation(
                            origin.document,
                            origin.pointer + "/content/" + media.replace("~", "~0").replace("/", "~1") + "/schema",
                            "schema",
                        ),
                        relation="validated_child",
                    )

    def _direct_parameter_origins(
        self, frame: LegacyParameterFrame, raw: dict[str, YamlValue]
    ) -> tuple[SchemaOrigin, ...]:
        """Find original occurrences by identity inside the actual legacy operation frame."""
        if frame.operation.origin_state != "known":
            return ()
        origins: list[SchemaOrigin] = []
        declaration = frame.operation.candidates[0].declaration
        document = self.source_lease.document_id(declaration.document)
        if document is None:
            return ()
        root = self.source_lease.borrow(SourceLocation(document, "", "schema"))
        if not isinstance(root, dict):
            return ()
        scope = root.get(declaration.tokens[0])
        if not isinstance(scope, dict):
            return ()
        path_item = scope.get(declaration.tokens[1])
        groups = (
            (declaration.tokens, frame.operation.candidates[0].raw.get("parameters")),
            (declaration.tokens[:-1], path_item.get("parameters") if isinstance(path_item, dict) else None),
            (declaration.tokens[:1], scope.get("parameters")),
        )
        for parent, values in groups:
            if not isinstance(values, list):
                continue
            for index, candidate in enumerate(values):
                if candidate is raw:
                    pointer = "/" + "/".join(
                        token.replace("~", "~0").replace("/", "~1") for token in (*parent, "parameters", str(index))
                    )
                    origins.append(SchemaOrigin(SourceLocation(document, pointer, "schema"), raw, "validated_child"))
        return tuple(origins)

    def get_ref_data_type(self, ref: str) -> DataType:
        """Observe actual reference type creation without calling a resolver again."""
        producer = ReferenceProducerFrame([])
        with self._resolution_producer(producer):
            result: DataType = super().get_ref_data_type(ref)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        self._observe_type(
            result,  # pyright: ignore[reportUnknownArgumentType]
            ref=ref,
            resolutions=tuple(dict.fromkeys(event.output for event in producer.resolutions if event.input == ref)),
        )
        return result  # pyright: ignore[reportUnknownVariableType]

    def parse_request_body(self, name: str, request_body: RequestBodyObject, path: list[str]) -> dict[str, DataType]:
        """Retain the original media dictionary without another schema parse."""
        return self._record_request_types(
            super().parse_request_body(name, request_body, path),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            path,
        )

    @capture_errors
    def _record_request_types(self, types: dict[str, DataType], path: list[str]) -> dict[str, DataType]:
        self.request_types.append(
            RequestTypesObservation(
                self._legacy_operations[-1] if self._legacy_operations else None, tuple(path), types
            )
        )
        return types

    def parse_responses(
        self, name: str, responses: dict[str | int, ReferenceObject | ResponseObject], path: list[str]
    ) -> dict[str | int, dict[str, DataType]]:
        """Retain every actual status and media, with no primary-response reduction."""
        return self._record_response_types(
            super().parse_responses(name, responses, path),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            path,
        )

    @capture_errors
    def _record_response_types(
        self, types: dict[str | int, dict[str, DataType]], path: list[str]
    ) -> dict[str | int, dict[str, DataType]]:
        self.response_types.append(
            ResponseTypesObservation(
                self._legacy_operations[-1] if self._legacy_operations else None, tuple(path), types
            )
        )
        return types

    def _generate_module_output(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- Preserve the existing module producer signature.
        self,
        ctx: ModuleContext,
        config: ParseConfig,
        contexts: list[ModuleContext],
        forwarder_map: ForwarderMap,
        require_update_action_models: list[str],
        future_imports_str: str,
    ) -> Result | None:
        """Observe the single ordinary render return without freezing inside parse."""
        result: Result | None = super()._generate_module_output(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            ctx, config, contexts, forwarder_map, require_update_action_models, future_imports_str
        )
        return self._record_module_output(ctx, result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_module_output(self, ctx: ModuleContext, result: Result | None) -> Result | None:
        if result is not None:
            self.module_outputs.append(
                ModuleOutputObservation(ctx.module, tuple(ctx.models), result, (self.imports, ctx.imports))
            )
        return result

    @capture_errors
    def resolve_module_results(self, results: str | dict[tuple[str, ...], Result]) -> tuple[ModuleResultBinding, ...]:
        """Match original contexts using the same path helpers as the ordinary renderer."""
        outputs = [output for output in self.module_outputs if output.models]
        if isinstance(results, str):
            return tuple(
                ModuleResultBinding(
                    tuple(self.binding_ledger.identity(model) for model in output.models),
                    "single" if len(outputs) == 1 and output.module == ("__init__.py",) else None,
                    reason=None
                    if len(outputs) == 1 and output.module == ("__init__.py",)
                    else "BND_ARTIFACT_AMBIGUOUS",
                )
                for output in outputs
            )

        keys_by_result: dict[int, list[tuple[str, ...]]] = {}
        for key, result in results.items():
            keys_by_result.setdefault(id(result), []).append(key)
        primary_keys: list[tuple[str, ...]] = []
        claims: dict[tuple[str, ...], int] = {}
        for output in outputs:
            key = _normalize_result_module_path(output.module, treat_dot_as_module=self.treat_dot_as_module)
            if self.treat_dot_as_module:
                key = _expand_result_module_path(key)
            primary_keys.append(key)
            claims[key] = claims.get(key, 0) + 1

        bindings: list[ModuleResultBinding] = []
        for output, key in zip(outputs, primary_keys, strict=True):
            models = tuple(self.binding_ledger.identity(model) for model in output.models)
            if claims[key] > 1:
                bindings.append(ModuleResultBinding(models, None, reason="BND_ARTIFACT_AMBIGUOUS"))
                continue
            if results.get(key) is not output.result:
                bindings.append(ModuleResultBinding(models, None, reason="BND_SYMBOL_NOT_EMITTED"))
                continue
            bindings.append(
                ModuleResultBinding(
                    models, key, tuple(other for other in keys_by_result[id(output.result)] if other != key)
                )
            )
        return tuple(bindings)

    def dispose(self) -> None:
        """Release graph anchors without replacing an ordinary disposal failure."""
        try:
            super().dispose()  # pyright: ignore[reportUnknownMemberType]
        except BaseException:
            with suppress(BaseException):
                self._release_capture()
            raise
        self._release_capture()

    def _release_capture(self) -> None:
        """Release every capture owner even when an earlier owner fails to close."""
        self.__dict__.pop("_model_resolver_factory", None)
        self.__dict__.pop("_generation_store_factory", None)
        self.request_types.clear()
        self.response_types.clear()
        self.parameter_fields.clear()
        self._parameter_frames.clear()
        for observations in (
            self._legacy_operations,
            self.legacy_ref_objects,
            self._media_paths,
            self.legacy_operations,
            self.legacy_scopes,
            self._legacy_scopes,
        ):
            observations.clear()
        self.type_observations.clear()
        self._raw_validation_frames.clear()
        self.schema_origins.close()
        self.field_origins.clear()
        self.synthetic_fields.clear()
        self._additional_root_fields.clear()
        self.schema_types.clear()
        self.schema_references.clear()
        self.root_documents.clear()
        self.unresolved_references.clear()
        self.variant_fields.clear()
        self._variant_references.clear()
        self._array_sources.clear()
        self._additional_type_frames.clear()
        self.additional_types.clear()
        self.pattern_types.clear()
        self.pattern_validators.clear()
        self.discriminator_types.clear()
        self._required_field_lists.clear()
        self.field_constructions.clear()
        self.effective_defaults.clear()
        self.inherited_defaults.clear()
        self.module_outputs.clear()
        self._conditional_merges.clear()
        self._inherited_merges.clear()
        self._combined_branches.clear()
        self._allof_refs.clear()
        self._root_schema_frames.clear()
        self._root_value_children.clear()
        self._pending_field_default = None
        self.root_materializations.clear()
        try:
            self.binding_ledger.close()
        except BaseException:
            with suppress(BaseException):
                self.binding_resolver.close_capture()
            raise
        self.binding_resolver.close_capture()


class ContractOpenAPIParser(BindingCaptureMixin, OpenAPIParser):
    """Capture ordinary explicitly configured scopes without substituting Api."""


class ContractApiOpenAPIParser(BindingCaptureMixin, ApiOpenAPIParser):
    """Capture the same declaration-aware model engine as ordinary Api generation."""

    _binding_resolver_type = ContractApiModelResolver

    def __init__(
        self,
        source: str | Path | list[Path] | ParseResult | dict[str, YamlValue],
        *,
        attempt_id: AttemptId,
        binding_ledger: BindingLedger | None = None,
        source_lease: SourceLease | None = None,
        config: OpenAPIParserConfig | None = None,
        **options: Unpack[OpenAPIParserConfigDict],
    ) -> None:
        """Observe the existing frame list only after original initialization."""
        self.object_observations: list[ObjectUseObservation] = []
        self.schema_observations: list[SchemaUseObservation] = []
        self._object_uses: dict[int, ObjectUseObservation] = {}
        self._object_contexts: list[ObjectUseObservation] = []
        super().__init__(
            source,
            attempt_id=attempt_id,
            binding_ledger=binding_ledger,
            source_lease=source_lease,
            config=config,
            **options,
        )
        self.binding_frames = _ObservedDeclarationFrames(
            self.source_lease, self._record_schema_use, self.binding_ledger
        )
        self.declaration_frames = self.binding_frames

    def _resolve_api_object(self, value: YamlValue, path: list[str]) -> _ApiObject:
        """Retain the existing object's declaration without another resolution."""
        return self._record_object_use(
            super()._resolve_api_object(value, path),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            path,
        )

    @capture_errors
    def _record_object_use(self, target: _ApiObject, path: list[str]) -> _ApiObject:
        """Record a completed typed return after the decorated engine boundary."""
        self.source_lease.register(target.declaration.document, target.document)
        use = self._declaration_id(path)
        observation = ObjectUseObservation(use, target, self._original_use(use))
        self.object_observations.append(observation)
        self._object_uses[id(target)] = observation
        return target

    def _original_use(self, declaration: ApiDeclarationId) -> ApiDeclarationId:
        """Project raw declaration tokens through actual enclosing object-use edges."""
        for context in reversed(self._object_contexts):
            target = context.target.declaration
            if declaration.document == target.document and declaration.tokens[: len(target.tokens)] == target.tokens:
                return ApiDeclarationId(
                    context.original_use.document,
                    (*context.original_use.tokens, *declaration.tokens[len(target.tokens) :]),
                )
        return declaration

    @contextmanager
    def _api_object_context(self, target: _ApiObject) -> Generator[None, None, None]:
        """Follow actual use contexts while the Api owner retains document switching."""
        observation = self._object_uses.get(id(target))
        if observation is not None:
            self._object_contexts.append(observation)
        try:
            with super()._api_object_context(target):  # pyright: ignore[reportUnknownMemberType]
                yield
        finally:
            if observation is not None:
                self._object_contexts.pop()

    def _declaration_source_location(self, frame: ApiDeclarationFrame) -> SourceLocation:
        document = self.source_lease.register(frame.declaration.document, frame.raw_document)
        tokens = frame.declaration.tokens
        pointer = "/" + "/".join(token.replace("~", "~0").replace("/", "~1") for token in tokens) if tokens else ""
        return SourceLocation(document, pointer, "schema")

    @capture_errors
    def _pair_validated_source(self, name: str, obj: JsonSchemaObject, path: list[str]) -> None:
        if self._raw_validation_frames:
            frame = self._raw_validation_frames[-1]
            if frame.name == name and frame.path == tuple(path):
                for declaration in reversed(self.binding_frames):
                    if (
                        declaration.raw_schema is frame.raw
                        and declaration.engine_path == frame.path
                        and declaration.projection == "item_stream_array"
                    ):
                        location = self._declaration_source_location(declaration)
                        raw = self.source_lease.borrow(location)
                        if not isinstance(raw, (dict, bool)):
                            msg = "An item-stream source is not an accepted schema declaration"
                            raise BindingCaptureError(msg)
                        self.schema_origins.pair_item_projection(raw=raw, obj=obj, location=location)
                        return
        super()._pair_validated_source(name, obj, path)

    def _validation_locations(self, frame: RawValidationFrame) -> tuple[SourceLocation, ...]:
        """Prefer the exact Api declaration frame over other occurrences of a YAML alias."""
        for declaration in reversed(self.binding_frames):
            if (
                declaration.raw_schema is frame.raw
                and declaration.engine_path == frame.path
                and declaration.projection == "value"
            ):
                return (self._declaration_source_location(declaration),)
        return super()._validation_locations(frame)

    @capture_errors
    def _record_schema_use(self, frame: ApiDeclarationFrame, document: SourceDocumentId) -> None:
        """Retain occurrence provenance before the existing schema engine runs."""
        self.schema_origins.borrow_document(document, frame.raw_document)
        operation = next((entry for entry in reversed(self.binding_frames) if entry.phase == "operation"), None)
        self.schema_observations.append(
            SchemaUseObservation(
                frame,
                self._original_use(frame.declaration),
                self._object_contexts[-1] if self._object_contexts else None,
                operation,
            )
        )

    def dispose(self) -> None:
        """Release frame observations even if ordinary parser disposal fails."""
        try:
            super().dispose()
        finally:
            self.binding_frames.close()
            self.object_observations.clear()
            self.schema_observations.clear()
            self._object_uses.clear()
            self._object_contexts.clear()


if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
    from pathlib import Path
    from urllib.parse import ParseResult

    from datamodel_code_generator._generation_contract import GraphObjectId, SourceDocumentId
    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator._types import OpenAPIParserConfigDict
    from datamodel_code_generator.config import OpenAPIParserConfig
    from datamodel_code_generator.enums import ClassNameAffixScope, HTTPBackend, NamingStrategy
    from datamodel_code_generator.format import PythonVersion
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.model.enum import Enum
    from datamodel_code_generator.parser.base import (
        DiscriminatorValue,
        ForwarderMap,
        ModuleContext,
        ParseConfig,
        Result,
    )
    from datamodel_code_generator.parser.openapi import (
        BaseModelT,
        MediaSchema,
        ReferenceObject,
        RequestBodyObject,
        ResponseObject,
    )
    from datamodel_code_generator.parser.openapi_contract_origins import SchemaRelation
    from datamodel_code_generator.parser.openapi_scope import _ApiObject  # pyright: ignore[reportPrivateUsage]
    from datamodel_code_generator.types import DataType
