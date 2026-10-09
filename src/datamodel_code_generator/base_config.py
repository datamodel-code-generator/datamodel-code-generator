"""Lightweight shared generation configuration.

This module is intentionally kept smaller than ``config.py`` so the CLI can
validate command-line options without importing parser/model configuration
classes that are only needed once generation starts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence  # noqa: TC003 - used at runtime by Pydantic
from pathlib import Path  # noqa: TC003 - used at runtime by Pydantic
from typing import TYPE_CHECKING, Any, TypeAlias

from pydantic import BaseModel, ConfigDict, PrivateAttr, field_validator, model_validator
from typing_extensions import Self

from datamodel_code_generator._format_types import (
    DateClassType,
    DatetimeClassType,
    Formatter,
    PythonVersion,
    PythonVersionMin,
)
from datamodel_code_generator._shared_types import LiteralType  # noqa: TC001 - used at runtime by Pydantic
from datamodel_code_generator.enums import (
    DEFAULT_SHARED_MODULE_NAME,
    AliasGenerator,
    AllExportsCollisionStrategy,
    AllExportsScope,
    AllOfClassHierarchy,
    AllOfMergeMode,
    ClassNameAffixScope,
    ClientBodyArguments,
    ClientSignatureStyle,
    ClientType,
    CollapseRootModelsNameStrategy,
    CustomFileHeaderMode,
    DataclassArguments,
    DataModelType,
    DefaultValueType,
    FieldTypeCollisionStrategy,
    HTTPBackend,
    InputFileType,
    ModuleSplitMode,
    NamingStrategy,
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
    from datamodel_code_generator._source import DirectoryInputFilter
    from datamodel_code_generator.preset_names import PresetName as PresetNameValue
else:
    PresetNameValue: TypeAlias = str


def _validate_additional_import_paths(value: list[str] | None) -> list[str] | None:
    """Validate user-provided imports before they reach generated source."""
    if value is None:
        return None

    from datamodel_code_generator import Error  # noqa: PLC0415
    from datamodel_code_generator.validators import _validate_python_import_path  # noqa: PLC0415

    try:
        return [_validate_python_import_path(import_path) for import_path in value]
    except ValueError as exc:
        msg = f"additional_imports {exc}"
        raise Error(msg) from None


_TARGET_SELECTORS: tuple[tuple[str, str], ...] = (("generate_server", "server_"), ("generate_client", "client_"))
_TARGET_REQUIRED: tuple[str, ...] = ("output", "package", "model_package")


def _option_list(flags: Sequence[str]) -> str:
    """Name options as an English list."""
    return flags[0] if len(flags) == 1 else f"{', '.join(flags[:-1])} and {flags[-1]}"


def _flag(field: str, *, negative: bool = False) -> str:
    """Return the option of a config field, or its --no- form."""
    return f"--{'no-' if negative else ''}{field.replace('_', '-')}"


def _selected_target(config: Any) -> tuple[str, str] | None:
    """Return the selector and the settings prefix of the target a config selects, or None for a model-only config."""
    return next((item for item in _TARGET_SELECTORS if getattr(config, item[0]) is not None), None)


def _missing_target_options(config: Any, *, output: bool = True) -> str | None:
    """Return why the target a config selects cannot run without the options it requires, or None when it can.

    Without `output`, the package directory is not required: a run that writes nothing does not use it.
    """
    if (selected := _selected_target(config)) is None:
        return None
    selector, prefix = selected
    required = _TARGET_REQUIRED if output else _TARGET_REQUIRED[1:]
    if missing := [_flag(field) for name in required if getattr(config, field := f"{prefix}{name}") is None]:
        return f"{_flag(selector)} requires {_option_list(missing)}"
    return None


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
    schema_validator_base_class_name: str | None = None
    validation: bool = False
    field_constraints: bool = False
    alias_generator: AliasGenerator | None = None
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
    deserialize_default_values: Sequence[DefaultValueType] = ()
    use_missing_sentinel: bool = False
    reuse_model: bool = False
    reuse_scope: ReuseScope = ReuseScope.Module
    shared_module_name: str = DEFAULT_SHARED_MODULE_NAME
    encoding: str = "utf-8"
    enum_field_as_literal: LiteralType | None = None
    enum_field_as_literal_map: dict[str, str] | None = None
    ignore_enum_constraints: bool = False
    use_one_literal_as_default: bool = False
    use_enum_values_in_discriminator: bool = False
    set_default_enum_member: bool = False
    use_subclass_enum: bool = False
    use_specialized_enum: bool = True
    strict_nullable: bool = False
    use_generic_container_types: bool = False
    enable_faux_immutability: bool = False
    disable_appending_item_suffix: bool = False
    empty_enum_field_name: str | None = None
    field_extra_keys: set[str] | None = None
    field_include_all_keys: bool = False
    field_extra_keys_without_x_prefix: set[str] | None = None
    model_extra_keys: set[str] | None = None
    model_extra_keys_without_x_prefix: set[str] | None = None
    include_path_parameters: bool = False
    openapi_include_paths: list[str] | None = None
    openapi_include_info_version: bool = False
    graphql_no_typename: bool = False
    wrap_string_literal: bool | None = None
    use_title_as_name: bool = False
    infer_union_variant_names: bool = False
    use_operation_id_as_name: bool = False
    use_unique_items_as_set: bool = False
    use_tuple_for_fixed_items: bool = False
    use_tuple_for_fixed_length_arrays: bool = False
    use_total_false_for_typed_dict: bool = False
    use_closed_typed_dict: bool = True
    allof_merge_mode: AllOfMergeMode = AllOfMergeMode.Constraints
    allof_class_hierarchy: AllOfClassHierarchy = AllOfClassHierarchy.IfNoConflict
    allow_remote_refs: bool | None = None
    strict_refs: bool = False
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
    use_non_positive_negative_number_constrained_types: bool = False
    use_decimal_for_multiple_of: bool = False
    original_field_name_delimiter: str | None = None
    use_double_quotes: bool = False
    use_union_operator: bool = True
    collapse_root_models: bool = False
    collapse_root_models_name_strategy: CollapseRootModelsNameStrategy | None = None
    collapse_reuse_models: bool = False
    skip_root_model: bool = False
    use_root_model_sequence_interface: bool = False
    use_type_alias: bool = False
    use_type_alias_type: bool = False
    use_root_model_type_alias: bool = False
    special_field_name_prefix: str | None = None
    remove_special_field_name_prefix: bool = False
    capitalise_enum_members: bool = False
    keep_model_order: bool = False
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
    dataclass_arguments: DataclassArguments | None = None
    disable_future_imports: bool = False
    import_overrides: dict[str, str] | None = None
    type_mappings: list[str] | None = None
    type_overrides: dict[str, str] | None = None
    read_only_write_only_model_type: ReadOnlyWriteOnlyModelType | None = None
    use_status_code_in_response_name: bool = False
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
    client_protocols: Mapping[str, Any] | None = None

    @field_validator("additional_imports")
    @classmethod
    def validate_additional_imports(cls, value: list[str] | None) -> list[str] | None:
        """Require additional imports to be safe Python import paths."""
        return _validate_additional_import_paths(value)

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
        if self.use_type_alias_type:
            self.use_type_alias = True
        return self


__all__ = ["BaseGenerateConfig"]
