"""Capture actual OpenAPI generation calls only for explicit contract consumers."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, ClassVar, Literal

from typing_extensions import TypedDict, Unpack

from datamodel_code_generator._generation_contract import AttemptId, BindingCaptureError, ReferenceResolution
from datamodel_code_generator._openapi_generation import SourceLease
from datamodel_code_generator.enums import AllOfMergeMode
from datamodel_code_generator.parser._api_reference import ApiDeclarationId, ApiModelResolver
from datamodel_code_generator.parser.openapi import OpenAPIParser
from datamodel_code_generator.parser.openapi_contract_store import (
    BindingLedger,
    ContractGenerationStore,
    FieldCopy,
    TypeCopy,
    Variant,
    capture_errors,
)
from datamodel_code_generator.parser.openapi_scope import ApiDeclarationFrame, ApiOpenAPIParser
from datamodel_code_generator.reference import FieldNameResolver, ModelResolver, ModelType, Reference

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping, Sequence
    from pathlib import Path
    from urllib.parse import ParseResult

    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator._types import OpenAPIParserConfigDict
    from datamodel_code_generator.config import OpenAPIParserConfig
    from datamodel_code_generator.enums import ClassNameAffixScope, HTTPBackend, NamingStrategy
    from datamodel_code_generator.format import PythonVersion
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.parser.openapi import MediaSchema, ReferenceObject, RequestBodyObject, ResponseObject
    from datamodel_code_generator.parser.openapi_scope import _ApiObject  # pyright: ignore[reportPrivateUsage]
    from datamodel_code_generator.types import DataType


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


class BindingResolverMixin(ModelResolver):
    """Keep actual resolver arguments/results, including recursive call ownership."""

    def __init__(self, ledger: BindingLedger, **options: Unpack[ResolverOptions]) -> None:
        """Establish attempt ownership before the original resolver constructor."""
        self.binding_ledger = ledger
        self.resolutions: list[ReferenceResolution] = []
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
        self.resolutions.append(ReferenceResolution(sequence, original, result, parent, "resolve_ref"))

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

    def close_capture(self) -> None:
        """Release completed resolver observations after projection or failure."""
        self.resolutions.clear()
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


class _ObservedDeclarationFrames(list[ApiDeclarationFrame]):  # ruff: ignore[subclass-builtin]
    """Observe actual Api frame insertion exclusively on capture parser instances."""

    def __init__(
        self, lease: SourceLease, record_schema: Callable[[ApiDeclarationFrame], None], ledger: BindingLedger
    ) -> None:
        """Retain finite operation and schema entry observations, not a mutation log."""
        super().__init__()
        self.lease = lease
        self.binding_ledger = ledger
        self.record_schema: Callable[[ApiDeclarationFrame], None] | None = record_schema
        self.operations: list[OperationObservation] = []
        self.schemas: list[ApiDeclarationFrame] = []

    @capture_errors
    def append(self, frame: ApiDeclarationFrame) -> None:
        """Borrow the engine's actual frame without recomputing its effective values.

        The remaining Api-owned frame phases after operations are schema and file.
        """
        if (record_schema := self.record_schema) is None:
            msg = "Declaration capture is closed"
            raise BindingCaptureError(msg)
        self.lease.register(frame.declaration.document, frame.raw_document)
        match frame.phase:
            case "operation":
                parent = next((entry for entry in reversed(self) if entry.phase == "operation"), None)
                self.operations.append(OperationObservation(frame, parent))
            case _:
                self.schemas.append(frame)
                record_schema(frame)
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


class BindingCaptureMixin(OpenAPIParser):
    """Create attempt ownership before ordinary parser construction begins."""

    _binding_resolver_type: ClassVar[type[BindingResolverMixin]] = ContractModelResolver

    def __init__(
        self,
        source: str | Path | list[Path] | ParseResult | dict[str, YamlValue],
        *,
        attempt_id: AttemptId,
        config: OpenAPIParserConfig | None = None,
        **options: Unpack[OpenAPIParserConfigDict],
    ) -> None:
        """Keep state on capture subclasses without adding fields to default parsers.

        The existing parser accepts mappings; its constructor annotation omits them.
        """
        self.binding_ledger = BindingLedger(attempt_id)
        self.source_lease = SourceLease()
        self.type_observations: list[TypeObservation] = []
        self.legacy_operations: list[LegacyOperationObservation] = []
        self.legacy_scopes: list[LegacyPathItemsFrame] = []
        self._legacy_scopes: list[LegacyPathItemsFrame] = []
        self._legacy_operations: list[LegacyOperationObservation] = []
        self.request_types: list[RequestTypesObservation] = []
        self.response_types: list[ResponseTypesObservation] = []
        self._model_resolver_factory = partial(self._create_binding_resolver)
        self._generation_store_factory = partial(self._create_binding_store)
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            source,  # type: ignore[arg-type]
            config=config,
            **options,
        )

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
        return self._record_field_copy((field,), result, None)  # pyright: ignore[reportUnknownArgumentType]

    def _copy_model_type(self, data_type: DataType, *, register_references: bool = True) -> DataType:
        """Observe one original helper return with unchanged reference registration."""
        result: DataType = super()._copy_model_type(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            data_type, register_references=register_references
        )
        return self._record_type_copy(data_type, result)  # pyright: ignore[reportUnknownArgumentType]

    def _copy_inherited_field(  # ruff: ignore[too-many-arguments]
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
    def _record_type_copy(self, source: DataType, target: DataType) -> DataType:
        identity = self.binding_ledger.identity
        self.binding_ledger.copies.append(TypeCopy(identity(source), identity(target)))
        return target

    def _get_rw_model_variant_reference(
        self, base_reference: Reference, suffix: Literal["Request", "Response"], *, loaded: bool = False
    ) -> Reference:
        """Retain variants before the engine releases its per-parse caches."""
        result: Reference = super()._get_rw_model_variant_reference(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            base_reference, suffix, loaded=loaded
        )
        return self._record_variant(base_reference, suffix, result)  # pyright: ignore[reportUnknownArgumentType]

    @capture_errors
    def _record_variant(self, base: Reference, suffix: Literal["Request", "Response"], result: Reference) -> Reference:
        identity = self.binding_ledger.identity
        self.binding_ledger.variants.append(Variant(identity(base), suffix, identity(result)))
        return result

    def _create_binding_resolver(self, **options: Unpack[ResolverOptions]) -> BindingResolverMixin:
        """Construct the owned resolver once with unchanged parser keyword values."""
        self.binding_resolver = self._binding_resolver_type(self.binding_ledger, **options)
        return self.binding_resolver

    def _parse_specification(self, specification: dict[str, YamlValue], path_parts: list[str]) -> None:
        """Borrow the actual root mapping before the existing engine visits it."""
        self._borrow_source("/".join(path_parts), specification)
        super()._parse_specification(specification, path_parts)  # pyright: ignore[reportUnknownMemberType]

    def _get_ref_body(self, resolved_ref: str) -> dict[str, YamlValue]:
        """Borrow the actual loader return without requesting another document."""
        raw: dict[str, YamlValue] = super()._get_ref_body(resolved_ref)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        self._borrow_source(resolved_ref, raw)  # pyright: ignore[reportUnknownArgumentType]
        return raw  # pyright: ignore[reportUnknownVariableType]

    @capture_errors
    def _borrow_source(self, uri: str, raw: dict[str, YamlValue]) -> None:
        """Borrow one actual loader result under the recording failure boundary."""
        self.source_lease.register(uri, raw)

    def _process_path_items(  # ruff: ignore[too-many-arguments]
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
        observation = LegacyOperationObservation(raw_operation, candidates, state)
        self.legacy_operations.append(observation)
        self._legacy_operations.append(observation)

    @capture_errors
    def _observe_type(self, data_type: DataType, *, path: list[str] | None = None, ref: str | None = None) -> None:
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
            )
        )

    def _parse_schema_or_ref(self, name: str, schema: MediaSchema, path: list[str]) -> DataType:
        """Keep the actual schema-use return, including discriminator-expanded types."""
        result: DataType = super()._parse_schema_or_ref(name, schema, path)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        self._observe_type(result, path=path)  # pyright: ignore[reportUnknownArgumentType]
        return result  # pyright: ignore[reportUnknownVariableType]

    def get_ref_data_type(self, ref: str) -> DataType:
        """Observe actual reference type creation without calling a resolver again."""
        result: DataType = super().get_ref_data_type(ref)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        self._observe_type(result, ref=ref)  # pyright: ignore[reportUnknownArgumentType]
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

    def dispose(self) -> None:
        """Release graph anchors even when ordinary disposal raises."""
        try:
            super().dispose()  # pyright: ignore[reportUnknownMemberType]
        finally:
            self.__dict__.pop("_model_resolver_factory", None)
            self.__dict__.pop("_generation_store_factory", None)
            self.request_types.clear()
            self.response_types.clear()
            self._legacy_operations.clear()
            self.legacy_operations.clear()
            self.legacy_scopes.clear()
            self._legacy_scopes.clear()
            self.type_observations.clear()
            self.binding_ledger.close()
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
        config: OpenAPIParserConfig | None = None,
        **options: Unpack[OpenAPIParserConfigDict],
    ) -> None:
        """Observe the existing frame list only after original initialization."""
        self.object_observations: list[ObjectUseObservation] = []
        self.schema_observations: list[SchemaUseObservation] = []
        self._object_uses: dict[int, ObjectUseObservation] = {}
        self._object_contexts: list[ObjectUseObservation] = []
        super().__init__(source, attempt_id=attempt_id, config=config, **options)
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

    @capture_errors
    def _record_schema_use(self, frame: ApiDeclarationFrame) -> None:
        """Retain occurrence provenance before the existing schema engine runs."""
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
