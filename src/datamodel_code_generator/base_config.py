"""Lightweight shared generation configuration.

This module is intentionally kept smaller than ``config.py`` so the CLI can
validate command-line options without importing parser/model configuration
classes that are only needed once generation starts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence  # noqa: TC003 - used at runtime by Pydantic
from pathlib import Path  # noqa: TC003 - used at runtime by Pydantic
from typing import TYPE_CHECKING, Any, Final, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, PrivateAttr, ValidationInfo, field_validator, model_validator
from typing_extensions import Self

from datamodel_code_generator._config_fields import DEFAULT_ENCODING, TARGET_SELECTORS
from datamodel_code_generator._format_types import (
    NEWEST_TARGET,
    DateClassType,
    DatetimeClassType,
    Formatter,
    PythonVersion,
    PythonVersionMin,
)
from datamodel_code_generator._message_text import english_list
from datamodel_code_generator._option_names import option_flag
from datamodel_code_generator._option_rules import enable_implied_options, implied_options
from datamodel_code_generator._shared_types import LiteralType  # noqa: TC001 - used at runtime by Pydantic
from datamodel_code_generator.enums import (
    DEFAULT_SHARED_MODULE_NAME,
    AliasGenerator,
    AllExportsCollisionStrategy,
    AllExportsScope,
    AllOfClassHierarchy,
    AllOfMergeMode,
    ClassNameAffixScope,
    ClientAsyncAPIPerspective,
    ClientBodyArguments,
    ClientSignatureStyle,
    ClientType,
    CollapseRootModelsNameStrategy,
    ConstructorFieldOrder,
    CustomFileHeaderMode,
    DataclassArguments,
    DataModelType,
    DefaultValueType,
    FieldTypeCollisionStrategy,
    HTTPBackend,
    InputFileType,
    ModuleSplitMode,
    NamingStrategy,
    OpenAPIAsDefinitions,
    ReadOnlyWriteOnlyModelType,
    ReuseScope,
    SchemaValidatorType,
    ServerBodyMode,
    ServerHandlerMode,
    ServerLayout,
    ServerType,
    TargetPydanticVersion,
    UnionMode,
    VersionMode,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from collections.abc import Set as AbstractSet

    from datamodel_code_generator._source import DirectoryInputFilter
    from datamodel_code_generator.preset_names import PresetName as PresetNameValue
else:
    PresetNameValue: TypeAlias = str


def _import_paths(field: str, paths: Iterable[object], target: PythonVersion, *, relative: bool = False) -> list[str]:
    """Return the import paths a setting gives as plain strings, raising its error for one that is not identifiers.

    Each path is read by ``validators._validate_python_import_path``: names of the target Python joined by dots, after
    the dots of a relative import with ``relative``.
    """
    from datamodel_code_generator import Error  # noqa: PLC0415
    from datamodel_code_generator.validators import _validate_python_import_path  # noqa: PLC0415

    try:
        return [_validate_python_import_path(path, target, relative=relative) for path in paths]
    except ValueError as exc:
        msg = f"{field} {exc}"
        raise Error(msg) from None


def _validate_additional_import_paths(value: list[str] | None, target: PythonVersion) -> list[str] | None:
    """Validate user-provided imports before they reach generated source, their names as names of the target Python.

    The configs check them for the newest target, and the parser again for the run's.
    """
    return None if value is None else _import_paths("additional_imports", value, target)


def _given(values: Iterable[str]) -> list[str]:
    """Return the values that give a path: those with text other than whitespace, which names none."""
    return [value for value in values if value.strip()]


def _decorator_paths(decorators: Iterable[str]) -> list[str]:
    """Return the paths the class decorators name, read from their tokens by ``python_decorator_path``."""
    from datamodel_code_generator._python_decorator import python_decorator_path  # noqa: PLC0415

    return [python_decorator_path(decorator) for decorator in _given(decorators)]


def _type_override_class_paths(type_overrides: Mapping[str, str]) -> Iterable[str]:
    """Return the ``--type-overrides`` values that name a class to import, which have no brackets.

    Such a value is names of a target joined by dots, or no annotation spells it; which values these are does not
    depend on the Python that runs the generator, whose parser may not know a name of a newer target. A value with
    brackets is an annotation or the parser's error (``parser/_type_overrides.py:_override_expression``).
    """
    from datamodel_code_generator._python_type_annotation_codec import parse_python_type_annotation  # noqa: PLC0415
    from datamodel_code_generator._target_unicode import is_dotted_python_name  # noqa: PLC0415

    return [
        value
        for value in _given(type_overrides.values())
        if "[" not in value
        and (is_dotted_python_name(value, NEWEST_TARGET) or parse_python_type_annotation(value) is None)
    ]


_NAMED_PATHS: Final[dict[str, Callable[[Any], Iterable[str]]]] = {
    "base_class": lambda base_class: _given((base_class,)),
    "base_class_map": lambda base_class_map: _given(
        base for bases in base_class_map.values() for base in ((bases,) if isinstance(bases, str) else bases)
    ),
    "class_decorators": _decorator_paths,
    "type_overrides": _type_override_class_paths,
}
"""The paths each setting that names Python objects by path names; a value or entry of only whitespace names none."""

_RELATIVE_NAMED_PATHS: Final = frozenset({"base_class", "base_class_map", "type_overrides"})
"""The settings of ``_NAMED_PATHS`` whose paths the output imports, so a path may be relative (``...base.BaseClass``).

Its module is written after ``from`` as given, as 0.88.0 wrote it; a decorator path is written as an expression, which
no dots can start.
"""


def _check_named_paths(field: str, value: Any, target: PythonVersion) -> None:
    """Refuse a value of a setting ``_NAMED_PATHS`` lists that names a path other than target names joined by dots.

    A setting ``_RELATIVE_NAMED_PATHS`` lists may start a path with the dots of a relative import.
    """
    if value:
        _import_paths(field, _NAMED_PATHS[field](value), target, relative=field in _RELATIVE_NAMED_PATHS)


def validate_named_paths(value: Any, info: ValidationInfo) -> Any:
    """Refuse a setting whose value names a Python object by a path that is not names of the target joined by dots.

    The command line, ``generate()`` and the parser configs accept the same paths, read by ``_import_paths`` for the
    newest target, and the parser refuses the names its target does not bind.
    """
    _check_named_paths(cast("str", info.field_name), value, NEWEST_TARGET)
    return value


def _check_config_named_paths(config: object, target: PythonVersion) -> None:
    """Refuse a config whose settings name a path other than names of the target joined by dots, as its validators do.

    The parsers check the config they read for the run's target, so that a config changed after validation is refused
    as well.
    """
    for field in _NAMED_PATHS:
        _check_named_paths(field, getattr(config, field), target)


def _schema_validator_base_class_name_error(value: str | None, target: PythonVersion) -> str | None:
    """Return why a name cannot name the shared schema validator base class, or None when it can or none is given.

    A name of the target Python can. The command line and `generate()` accept the same names for the newest target,
    and the parser refuses the names its target does not bind; each raises the message as its own error.
    """
    if value is None:
        return None

    from datamodel_code_generator._target_unicode import is_python_name  # noqa: PLC0415

    if is_python_name(value, target):
        return None
    return f"--schema-validator-base-class-name '{value}' is not a valid Python identifier"


_TARGET_REQUIRED: tuple[str, ...] = ("output", "package", "model_package")
_TARGET_INPUTS: dict[str, tuple[InputFileType, ...]] = {
    "generate_server": (InputFileType.OpenAPI,),
    "generate_client": (InputFileType.OpenAPI, InputFileType.AsyncAPI),
}
"""The input kinds each target reads operations from, by the field that selects it."""


def _selected_target(config: Any) -> tuple[str, str] | None:
    """Return the selector and the settings prefix of the target a config selects, or None for a model-only config."""
    return next((item for item in TARGET_SELECTORS if getattr(config, item[0]) is not None), None)


def _target_options_error(config: Any, *, output: bool = True) -> str | None:
    """Return why the target a config selects cannot run with its options, or None when it can.

    A target needs its required options and an input explicitly typed as one it reads: OpenAPI, or AsyncAPI for the
    client. Without `output`, the package directory is not required: a run that writes nothing does not use it.
    """
    if (selected := _selected_target(config)) is None:
        return None
    selector, prefix = selected
    required = _TARGET_REQUIRED if output else _TARGET_REQUIRED[1:]
    if missing := [option_flag(field) for name in required if getattr(config, field := f"{prefix}{name}") is None]:
        return f"{option_flag(selector)} requires {english_list(missing, 'and')}"
    readable = _TARGET_INPUTS[selector]
    if (input_file_type := InputFileType(config.input_file_type)) not in readable:
        types = english_list([kind.value for kind in readable], "or")
        return f"{option_flag(selector)} requires --input-file-type {types}, not {input_file_type.value}"
    asyncapi = input_file_type is InputFileType.AsyncAPI
    if asyncapi and config.client_protocols is not None:
        return "--client-protocols cannot be used with --input-file-type asyncapi, whose channels the client opens"
    if not asyncapi and config.client_asyncapi_perspective is not None:
        return "--client-asyncapi-perspective requires --input-file-type asyncapi"
    return None


def implied_model_settings(config: Any, explicit: AbstractSet[str]) -> dict[str, bool]:
    """Return the constraint settings a config leaves unset, with the values its other settings imply.

    A setting is unset unless `explicit` names it. msgspec models use `Annotated` constraints, `use_annotated` writes
    the constraints as field constraints, and an unset `field_constraints` follows `use_annotated`, so the command
    line and `generate()` keep the same constraints. `intersect_combined_schema_keywords` turns on
    `intersect_allof_keywords`, also where a preset sets it.
    """
    use_annotated = config.use_annotated
    implied: dict[str, bool] = {}
    if "use_annotated" not in explicit and DataModelType(config.output_model_type) is DataModelType.MsgspecStruct:
        implied["use_annotated"] = use_annotated = True
    if "field_constraints" not in explicit:
        implied["field_constraints"] = use_annotated
    implied.update(
        implied_options({
            "use_annotated": use_annotated,
            "intersect_combined_schema_keywords": config.intersect_combined_schema_keywords,
        })
    )
    return implied


def implied_target_settings(config: Any, explicit: AbstractSet[str]) -> dict[str, Any]:
    """Return the model settings a server or client target needs that a config leaves unset, with the values it implies.

    A setting is unset unless `explicit` names it, the fields an option, a configuration key, a `generate()` argument,
    or a preset set. The target needs the `api` scope of an input other than AsyncAPI, Python 3.11, and its backend's
    settings: the MISSING sentinel with pydantic 2.14 for Pydantic dataclasses, pydantic 2.14 for any sentinel, and the
    decodable unions and enums of msgspec. A setting that was set keeps its value, which the target refuses when it
    cannot use it.
    """
    from datamodel_code_generator._requirements import REQUIREMENTS, python_target_meets  # noqa: PLC0415
    from datamodel_code_generator.enums import (  # noqa: PLC0415
        _PYDANTIC_MODEL_TYPES,
        DataModelType,
        OpenAPIScope,
        TargetPydanticVersion,
    )

    implied: dict[str, Any] = {}
    scopes = list(config.openapi_scopes or (OpenAPIScope.Schemas,))
    if (
        "openapi_scopes" not in explicit
        and OpenAPIScope.Api not in scopes
        and InputFileType(config.input_file_type) is not InputFileType.AsyncAPI
    ):
        implied["openapi_scopes"] = [*scopes, OpenAPIScope.Api]
    if "target_python_version" not in explicit and not python_target_meets(
        "python.strenum", config.target_python_version.version_key
    ):
        implied["target_python_version"] = PythonVersion(REQUIREMENTS["python.strenum"].minimum)
    backend = DataModelType(config.output_model_type)
    switches = {
        DataModelType.PydanticV2Dataclass: ("use_missing_sentinel",),
        DataModelType.MsgspecStruct: ("use_msgspec_tagged_unions", "use_msgspec_nullable_enum_alias"),
    }.get(backend, ())
    implied.update((name, True) for name in switches if name not in explicit and not getattr(config, name))
    if (
        implied.get("use_missing_sentinel", config.use_missing_sentinel)
        and backend in _PYDANTIC_MODEL_TYPES
        and "target_pydantic_version" not in explicit
    ):
        implied["target_pydantic_version"] = TargetPydanticVersion.V2_14
    return implied


class BaseGenerateConfig(BaseModel):
    """Shared generation configuration fields."""

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=True,
        protected_namespaces=(),
        defer_build=True,
    )

    _generation_timestamp: str | None = PrivateAttr(default=None)
    # ``--diff-against`` writes each generated revision to a temporary path while
    # retaining the caller-selected output path as the formatter/process context.
    # Keeping this private preserves the public configuration schema.
    _logical_output: Path | None = PrivateAttr(default=None)
    _logical_model_metadata: Path | None = PrivateAttr(default=None)
    _directory_input_filter: DirectoryInputFilter | None = PrivateAttr(default=None)
    _target_document_bases: dict[str, Path] | None = PrivateAttr(default=None)
    # The pyproject.toml ``load_pyproject_config()`` read: the default lockfile lies beside it, as on the command line.
    _pyproject_path: Path | None = PrivateAttr(default=None)

    input_file_type: InputFileType = InputFileType.Auto
    output: Path | None = None
    emit_model_metadata: Path | None = None
    output_model_type: DataModelType = DataModelType.PydanticV2BaseModel
    preset: PresetNameValue | None = None
    target_python_version: PythonVersion = PythonVersionMin
    target_pydantic_version: TargetPydanticVersion | None = None
    base_class: str = ""
    base_class_map: dict[str, str | list[str]] | None = None
    model_name_map: dict[str, str] | None = None
    additional_imports: list[str] | None = None
    class_decorators: list[str] | None = None
    custom_template_dir: Path | None = None
    schema_validator_type: SchemaValidatorType | None = None
    generate_schema_validators: bool = False
    check_pattern_maps_strictly: bool = False
    schema_validator_base_class_name: str | None = None
    validation: bool = False
    field_constraints: bool = False
    alias_generator: AliasGenerator | None = None
    pin_aliases_with_alias_generator: bool = False
    snake_case_field: bool = False
    strip_default_none: bool = False
    disable_timestamp: bool = False
    enable_version_header: bool = False
    enable_command_header: bool = False
    enable_generated_header_marker: bool = False
    allow_population_by_field_name: bool = False
    allow_extra_fields: bool = False
    extra_fields: str | None = None
    use_generic_base_class: bool = False
    class_name: str | None = None
    allow_leading_underscore_class_name: bool = False
    class_name_prefix: str | None = None
    class_name_suffix: str | None = None
    class_name_affix_scope: ClassNameAffixScope = ClassNameAffixScope.All
    use_standard_collections: bool = True
    use_schema_description: bool = False
    use_field_description: bool = False
    use_field_description_example: bool = False
    use_attribute_docstrings: bool = False
    use_inline_field_description: bool = False
    use_single_line_docstring: bool = False
    use_default_kwarg: bool = False
    apply_field_options_to_every_field: bool = False
    deserialize_default_values: Sequence[DefaultValueType] = ()
    use_missing_sentinel: bool = False
    reuse_model: bool = False
    reuse_scope: ReuseScope = ReuseScope.Module
    shared_module_name: str = DEFAULT_SHARED_MODULE_NAME
    encoding: str = DEFAULT_ENCODING
    enum_field_as_literal: LiteralType | None = None
    enum_field_as_literal_map: dict[str, str] | None = None
    ignore_enum_constraints: bool = False
    use_one_literal_as_default: bool = False
    use_enum_values_in_discriminator: bool = False
    merge_shared_variant_tags: bool = False
    read_schema_version_per_document: bool = False
    read_escaped_ref_pointers: bool = False
    read_dynamic_anchors_in_document_order: bool = False
    ignore_ref_siblings_by_schema_version: bool = False
    use_msgspec_tagged_unions: bool = False
    tag_msgspec_by_payload_name: bool = False
    use_msgspec_nullable_enum_alias: bool = False
    use_msgspec_forbid_unknown_fields: bool = False
    use_typed_nullable_map_values: bool = False
    use_typed_map_values_beside_other_types: bool = False
    use_single_dict_for_object_maps: bool = False
    read_unevaluated_properties: bool = False
    use_single_value_enum_as_const: bool = False
    read_const_beside_enum: bool = False
    filter_literal_values: bool = False
    intersect_allof_keywords: bool = False
    intersect_combined_schema_keywords: bool = False
    distribute_allof_over_unions: bool = False
    read_allof_parents_in_mro_order: bool = False
    read_nullable_beside_type_list: bool = False
    keep_field_defaults_off_branch_roots: bool = False
    keep_enum_member_values: bool = False
    set_default_enum_member: bool = False
    use_subclass_enum: bool = False
    use_specialized_enum: bool = True
    strict_nullable: bool = False
    omit_redundant_reference_null: bool = False
    keep_reference_null_on_merged_models: bool = False
    use_generic_container_types: bool = False
    enable_faux_immutability: bool = False
    disable_appending_item_suffix: bool = False
    singularize_last_word_of_item_names: bool = False
    empty_enum_field_name: str | None = None
    field_extra_keys: set[str] | None = None
    field_include_all_keys: bool = False
    field_extra_keys_without_x_prefix: set[str] | None = None
    model_extra_keys: set[str] | None = None
    model_extra_keys_without_x_prefix: set[str] | None = None
    include_path_parameters: bool = False
    openapi_include_paths: list[str] | None = None
    openapi_include_info_version: bool = False
    openapi_as_definitions: Sequence[OpenAPIAsDefinitions] = ()
    graphql_no_typename: bool = False
    wrap_string_literal: bool | None = None
    use_title_as_name: bool = False
    infer_union_variant_names: bool = False
    use_tag_as_union_variant_name: bool = False
    name_allof_union_members_after_branches: bool = False
    use_operation_id_as_name: bool = False
    use_unique_items_as_set: bool = False
    use_unique_items_as_set_by_value: bool = False
    use_tuple_for_fixed_items: bool = False
    use_tuple_for_closed_arrays: bool = False
    use_tuple_for_fixed_length_arrays: bool = False
    use_total_false_for_typed_dict: bool = False
    use_closed_typed_dict: bool = True
    allof_merge_mode: AllOfMergeMode = AllOfMergeMode.Constraints
    allof_class_hierarchy: AllOfClassHierarchy = AllOfClassHierarchy.IfNoConflict
    allow_remote_refs: bool | None = None
    strict_refs: bool = False
    untrusted_input: bool = False
    allow_private_network: bool = False
    http_backend: HTTPBackend = HTTPBackend.AUTO
    http_headers: Sequence[tuple[str, str]] | None = None
    http_local_ref_path: Path | None = None
    http_ignore_tls: bool = False
    http_timeout: float | None = None
    lockfile: Path | None = None
    update_lock: bool = False
    locked: bool = False
    _remote_lock: Any | None = PrivateAttr(default=None)
    _remote_lock_resolved: bool = PrivateAttr(default=False)

    @property
    def remote_lock(self) -> Any | None:
        """Return the generation-scoped remote lock collector, if resolved."""
        return self._remote_lock

    @property
    def remote_lock_resolved(self) -> bool:
        """Return whether the CLI or API has resolved remote lock policy."""
        return self._remote_lock_resolved

    def resolve_remote_lock(self, remote_lock: Any | None) -> None:
        """Record a generation-scoped remote lock decision on this config instance."""
        self._remote_lock = remote_lock
        self._remote_lock_resolved = True

    use_annotated: bool = False
    use_serialize_as_any: bool = False
    use_serialize_as_any_for_every_subclass: bool = False
    use_non_positive_negative_number_constrained_types: bool = False
    use_decimal_for_multiple_of: bool = False
    use_decimal_in_field_forms: bool = False
    use_encoded_str_for_constrained_base64: bool = False
    use_strict_str_for_base64: bool = False
    use_msgspec_meta_for_map_keys: bool = False
    omit_redundant_constraints: bool = False
    read_length_keywords_by_type: bool = False
    read_object_keywords_by_type: bool = False
    read_msgspec_union_bounds_exactly: bool = False
    read_input_model_dataclass_fields_by_alias: bool = False
    reuse_every_reached_input_model_type: bool = False
    use_site_models_for_constrained_refs: bool = False
    use_one_model_per_ref_site: bool = False
    original_field_name_delimiter: str | None = None
    use_double_quotes: bool = False
    use_union_operator: bool = True
    collapse_root_models: bool = False
    collapse_root_models_name_strategy: CollapseRootModelsNameStrategy | None = None
    collapse_shared_parent_wrappers: bool = False
    keep_constrained_root_models_in_every_container: bool = False
    keep_enum_literal_aliases: bool = False
    keep_reference_null_on_collapsed_root_models: bool = False
    keep_shared_members_on_collapsed_root_models: bool = False
    collapse_constrained_root_models_as_annotated: bool = False
    remove_unused_models_beside_type_overrides: bool = False
    keep_self_referencing_array_items: bool = False
    keep_defaults_of_inherited_required_fields: bool = False
    omit_incomplete_model_defaults: bool = False
    collapse_reuse_models: bool = False
    skip_root_model: bool = False
    use_root_model_sequence_interface: bool = False
    use_type_alias: bool = False
    use_type_alias_type: bool = False
    use_root_model_type_alias: bool = False
    use_plain_alias_for_recursive_models: bool = False
    special_field_name_prefix: str | None = None
    remove_special_field_name_prefix: bool = False
    capitalise_enum_members: bool = False
    capitalise_numbered_enum_members: bool = False
    keep_model_order: bool = False
    order_constructor_fields_by: Sequence[ConstructorFieldOrder] = ()
    custom_file_header: str | None = None
    custom_file_header_path: Path | None = None
    custom_file_header_mode: CustomFileHeaderMode = CustomFileHeaderMode.Replace
    custom_formatters: list[str] | None = None
    use_pendulum: bool = False
    use_standard_primitive_types: bool = False
    use_object_type: bool = False
    http_query_parameters: Sequence[tuple[str, str]] | None = None
    treat_dot_as_module: bool | None = None
    strict_dotted_module_names: bool = False
    rename_submodules_hidden_by_classes: bool = False
    use_exact_imports: bool = False
    use_type_checking_imports: bool | None = None
    union_mode: UnionMode | None = None
    output_datetime_class: DatetimeClassType | None = None
    output_date_class: DateClassType | None = None
    keyword_only: bool = False
    frozen_dataclasses: bool = False
    no_alias: bool = False
    use_serialization_alias: bool = False
    use_frozen_field: bool = False
    use_default_factory_for_optional_nested_models: bool = False
    formatters: list[Formatter] | None = None
    builtin_format_line_length: int | None = None
    parent_scoped_naming: bool = False
    naming_strategy: NamingStrategy | None = None
    duplicate_name_suffix: dict[str, str] | None = None
    use_duplicate_name_suffix_for_every_collision: bool = False
    dataclass_arguments: DataclassArguments | None = None
    keep_false_dataclass_arguments: bool = False
    disable_future_imports: bool = False
    import_overrides: dict[str, str] | None = None
    type_mappings: list[str] | None = None
    type_overrides: dict[str, str] | None = None
    type_overrides_replace_whole_type: bool = False
    use_x_python_type_as_written: bool = False
    use_x_python_import_for_every_ref: bool = False
    use_bytes_for_content_media_type: bool = False
    use_bounds_for_float_const: bool = False
    read_only_write_only_model_type: ReadOnlyWriteOnlyModelType | None = None
    dedupe_request_response_variants: bool = False
    dedupe_numbered_request_response_variants: bool = False
    use_status_code_in_response_name: bool = False
    keep_first_character_of_path_keys: bool = False
    keep_content_parameter_refs: bool = False
    skip_path_extension_keys: bool = False
    use_enum_field_as_literal_in_operations: bool = False
    name_converted_payloads_by_message: bool = False
    merge_message_trait_headers: bool = False
    all_exports_scope: AllExportsScope | None = None
    all_exports_collision_strategy: AllExportsCollisionStrategy | None = None
    field_type_collision_strategy: FieldTypeCollisionStrategy | None = None
    module_split_mode: ModuleSplitMode | None = None
    schema_version: str | None = None
    schema_version_mode: VersionMode | None = None
    external_ref_mapping: dict[str, str] | None = None
    generate_server: ServerType | None = None
    server_output: Path | None = None
    server_package: str | None = None
    server_model_package: str | None = None
    server_layout: ServerLayout | None = None
    server_handler_mode: ServerHandlerMode | None = None
    server_handler_modes: Mapping[str, ServerHandlerMode] | None = None
    server_include_request: bool | None = None
    server_body_mode: ServerBodyMode | None = None
    server_body_modes: Mapping[str, ServerBodyMode] | None = None
    server_primary_responses: Mapping[str, Any] | None = None
    server_operation_names: Mapping[str, str] | None = None
    server_router_names: Mapping[str, str] | None = None
    server_parameter_names: Mapping[str, Mapping[str, str]] | None = None
    generate_client: ClientType | None = None
    client_output: Path | None = None
    client_package: str | None = None
    client_model_package: str | None = None
    client_signature_style: ClientSignatureStyle | None = None
    client_body_arguments: ClientBodyArguments | None = None
    client_resource_names: Mapping[str, str] | None = None
    client_operations: Mapping[str, Mapping[str, Any]] | None = None
    client_default_base_url: str | None = None
    client_server_base_url: str | None = None
    client_deep_object_brackets: bool | None = None
    client_asyncapi_perspective: ClientAsyncAPIPerspective | None = None
    client_protocols: Mapping[str, Any] | None = None

    @field_validator("additional_imports")
    @classmethod
    def validate_additional_imports(cls, value: list[str] | None) -> list[str] | None:
        """Require additional imports to be safe Python import paths."""
        return _validate_additional_import_paths(value, NEWEST_TARGET)

    _validate_named_paths = field_validator(*_NAMED_PATHS)(validate_named_paths)

    @model_validator(mode="after")
    def normalize_schema_validator_type(self) -> Self:
        """Keep the legacy boolean flag and explicit backend selection in sync."""
        if self.generate_schema_validators and self.schema_validator_type is None:
            self.schema_validator_type = SchemaValidatorType.PydanticV2
        if self.schema_validator_type is not None:
            self.generate_schema_validators = True
        return self

    @model_validator(mode="after")
    def validate_remote_lock_mode(self) -> Self:
        """Keep lock update and verification modes mutually exclusive."""
        if self.update_lock and self.locked:
            msg = "--update-lock and --locked cannot be used together"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def validate_target_selection(self) -> Self:
        """Validate that one run selects at most one generation target."""
        if self.generate_server is not None and self.generate_client is not None:
            from datamodel_code_generator import Error  # noqa: PLC0415

            msg = "--generate-server and --generate-client cannot be used together"
            raise Error(msg)
        return self

    @model_validator(mode="after")
    def normalize_use_type_alias_type(self) -> Self:
        """Enable type aliases when their implementation is explicitly selected."""
        enable_implied_options(self, "use_type_alias_type")
        return self


__all__ = ["BaseGenerateConfig"]
