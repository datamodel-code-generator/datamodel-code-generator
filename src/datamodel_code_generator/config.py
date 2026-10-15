"""Configuration models for datamodel-code-generator."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from keyword import iskeyword
from pathlib import Path  # noqa: TC003 - used at runtime by Pydantic
from threading import RLock
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from datamodel_code_generator._format_types import (
    DateClassType,
    DatetimeClassType,
    Formatter,
    PythonVersion,
    PythonVersionMin,
)
from datamodel_code_generator._shared_types import DefaultPutDict, LiteralType
from datamodel_code_generator.base_config import BaseGenerateConfig, _validate_additional_import_paths
from datamodel_code_generator.enums import (
    DEFAULT_SHARED_MODULE_NAME,
    AliasGenerator,
    AllExportsCollisionStrategy,
    AllExportsScope,
    AllOfClassHierarchy,
    AllOfMergeMode,
    AsyncAPIVersion,
    ClassNameAffixScope,
    CollapseRootModelsNameStrategy,
    DataclassArguments,
    DefaultValueType,
    FieldTypeCollisionStrategy,
    GraphQLScope,
    HTTPBackend,
    JsonSchemaVersion,
    ModuleSplitMode,
    NamingStrategy,
    OpenAPIScope,
    OpenAPIVersion,
    ProtobufVersion,
    ReadOnlyWriteOnlyModelType,
    ReuseScope,
    TargetPydanticVersion,
    UnionMode,
    VersionMode,
    XMLSchemaVersion,
)
from datamodel_code_generator.model import pydantic_v2
from datamodel_code_generator.model.base import (  # noqa: TC001 - used by Pydantic at runtime
    DataModel,
    DataModelFieldBase,
)
from datamodel_code_generator.model.scalar import DataTypeScalar
from datamodel_code_generator.model.union import DataTypeUnion
from datamodel_code_generator.types import DataTypeManager, StrictTypes
from datamodel_code_generator.validators import ModelValidators  # noqa: TC001 - used by Pydantic at runtime

CallableSchema = Callable[[str], str]
DumpResolveReferenceAction = Callable[[Iterable[str]], str]
_CONFIG_REBUILD_LOCK = RLock()
DefaultPutDictSchema = DefaultPutDict[str, str]
if TYPE_CHECKING:
    from datamodel_code_generator._parser_context import ParserSourceContext

    ExtraTemplateDataType = defaultdict[str, dict[str, Any]]
else:
    ExtraTemplateDataType = defaultdict[str, Annotated[dict[str, Any], Field(default_factory=dict)]]


def validate_schema_validator_base_class_name(value: str | None) -> str | None:
    """Validate the generated shared schema validator base class name."""
    if value is None:
        return value
    if not value.isidentifier() or iskeyword(value):
        msg = f"--schema-validator-base-class-name '{value}' is not a valid Python identifier"
        raise ValueError(msg)
    return value


class GenerateConfig(BaseGenerateConfig):
    """Configuration model for generate()."""

    input_filename: str | None = None
    extra_template_data: ExtraTemplateDataType | None = None
    validators: Mapping[str, ModelValidators] | None = None
    aliases: Mapping[str, str | list[str]] | None = None
    serialization_aliases: Mapping[str, str] | None = None
    command_line: str | None = None
    apply_default_values_for_required_fields: bool = False
    force_optional_for_required_fields: bool = False
    strict_types: Sequence[StrictTypes] | None = None
    custom_class_name_generator: CallableSchema | None = None
    openapi_scopes: list[OpenAPIScope] | None = None
    graphql_scopes: list[GraphQLScope] | None = None
    custom_formatters_kwargs: dict[str, Any] | None = None
    settings_path: Path | None = None
    default_value_overrides: Mapping[str, Any] | None = None

    _validate_schema_validator_base_class_name = field_validator("schema_validator_base_class_name")(
        validate_schema_validator_base_class_name
    )


def _rebuild_config_model(model_type: type[BaseModel], types_namespace: dict[str, Any]) -> None:
    """Build one deferred config model without racing shared Pydantic state."""
    if model_type.__pydantic_complete__:
        return
    with _CONFIG_REBUILD_LOCK:
        if model_type.__pydantic_complete__:
            return
        model_type.model_rebuild(_types_namespace=types_namespace)


def _rebuild_generate_config() -> None:
    _rebuild_config_model(
        GenerateConfig,
        {"DefaultValueType": DefaultValueType, "StrictTypes": StrictTypes, "UnionMode": UnionMode},
    )


class ParserConfig(BaseModel):
    """Configuration model for Parser.__init__()."""

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=True,
        protected_namespaces=(),
        defer_build=True,
    )

    _source_context: ParserSourceContext | None = PrivateAttr(default=None)
    # These CLI-only flags are kept private while the stdout repair flow is
    # migrated out of the parser configuration in a later boundary change.
    _repair_invalid_dotted_stdout: bool = PrivateAttr(default=False)
    _forced_invalid_dotted_stdout_repair_modules: tuple[tuple[str, ...], ...] = PrivateAttr(default=())

    @property
    def repair_invalid_dotted_stdout(self) -> bool:
        """Return the internal stdout repair switch used by generation retries."""
        return self._repair_invalid_dotted_stdout

    @property
    def forced_invalid_dotted_stdout_repair_modules(self) -> tuple[tuple[str, ...], ...]:
        """Return modules explicitly selected by the stdout repair retry."""
        return self._forced_invalid_dotted_stdout_repair_modules

    data_model_type: type[DataModel] = pydantic_v2.BaseModel
    data_model_root_type: type[DataModel] = pydantic_v2.RootModel
    data_type_manager_type: type[DataTypeManager] = pydantic_v2.DataTypeManager
    data_model_field_type: type[DataModelFieldBase] = pydantic_v2.DataModelField
    base_class: str | None = None
    base_class_map: dict[str, str | list[str]] | None = None
    model_name_map: dict[str, str] | None = None
    additional_imports: list[str] | None = None
    class_decorators: list[str] | None = None
    custom_template_dir: Path | None = None
    extra_template_data: ExtraTemplateDataType | None = None
    validators: Mapping[str, ModelValidators] | None = None
    generate_schema_validators: bool = False
    schema_validator_base_class_name: str | None = None
    target_python_version: PythonVersion = PythonVersionMin
    dump_resolve_reference_action: DumpResolveReferenceAction | None = None
    validation: bool = False
    field_constraints: bool = False
    alias_generator: AliasGenerator | None = None
    snake_case_field: bool = False
    strip_default_none: bool = False
    aliases: Mapping[str, str | list[str]] | None = None
    serialization_aliases: Mapping[str, str] | None = None
    allow_population_by_field_name: bool = False
    apply_default_values_for_required_fields: bool = False
    allow_extra_fields: bool = False
    extra_fields: str | None = None
    use_generic_base_class: bool = False
    force_optional_for_required_fields: bool = False
    class_name: str | None = None
    allow_leading_underscore_class_name: bool = False
    class_name_prefix: str | None = None
    class_name_suffix: str | None = None
    class_name_affix_scope: ClassNameAffixScope = ClassNameAffixScope.All
    use_standard_collections: bool = False
    base_path: Path | None = None
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
    reuse_scope: ReuseScope | None = None
    shared_module_name: str = DEFAULT_SHARED_MODULE_NAME
    encoding: str = "utf-8"
    enum_field_as_literal: LiteralType | None = None
    enum_field_as_literal_map: dict[str, str] | None = None
    ignore_enum_constraints: bool = False
    set_default_enum_member: bool = False
    use_subclass_enum: bool = False
    use_specialized_enum: bool = True
    strict_nullable: bool = False
    use_generic_container_types: bool = False
    enable_faux_immutability: bool = False
    remote_text_cache: DefaultPutDictSchema | None = None
    disable_appending_item_suffix: bool = False
    strict_types: Sequence[StrictTypes] | None = None
    empty_enum_field_name: str | None = None
    custom_class_name_generator: CallableSchema | None = None
    field_extra_keys: set[str] | None = None
    field_include_all_keys: bool = False
    field_extra_keys_without_x_prefix: set[str] | None = None
    model_extra_keys: set[str] | None = None
    model_extra_keys_without_x_prefix: set[str] | None = None
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
    use_annotated: bool = False
    use_serialize_as_any: bool = False
    use_non_positive_negative_number_constrained_types: bool = False
    use_decimal_for_multiple_of: bool = False
    original_field_name_delimiter: str | None = None
    use_double_quotes: bool = False
    use_union_operator: bool = False
    allow_responses_without_content: bool = False
    collapse_root_models: bool = False
    collapse_root_models_name_strategy: CollapseRootModelsNameStrategy | None = None
    collapse_reuse_models: bool = False
    skip_root_model: bool = False
    use_root_model_sequence_interface: bool = False
    use_type_alias: bool = False
    special_field_name_prefix: str | None = None
    remove_special_field_name_prefix: bool = False
    capitalise_enum_members: bool = False
    keep_model_order: bool = False
    use_one_literal_as_default: bool = False
    use_enum_values_in_discriminator: bool = False
    use_msgspec_tagged_unions: bool = False
    use_msgspec_nullable_enum_alias: bool = False
    use_typed_nullable_map_values: bool = False
    known_third_party: list[str] | None = None
    custom_formatters: list[str] | None = None
    custom_formatters_kwargs: dict[str, Any] | None = None
    use_pendulum: bool = False
    use_standard_primitive_types: bool = False
    use_object_type: bool = False
    http_query_parameters: Sequence[tuple[str, str]] | None = None
    treat_dot_as_module: bool | None = None
    strict_dotted_module_names: bool = False
    use_exact_imports: bool = False
    use_type_checking_imports: bool | None = None
    default_field_extras: dict[str, Any] | None = None
    target_datetime_class: DatetimeClassType | None = None
    target_date_class: DateClassType | None = None
    keyword_only: bool = False
    frozen_dataclasses: bool = False
    no_alias: bool = False
    use_serialization_alias: bool = False
    use_frozen_field: bool = False
    use_default_factory_for_optional_nested_models: bool = False
    formatters: list[Formatter] | None = None
    builtin_format_line_length: int | None = None
    defer_formatting: bool = False
    parent_scoped_naming: bool = False
    naming_strategy: NamingStrategy | None = None
    duplicate_name_suffix: dict[str, str] | None = None
    dataclass_arguments: DataclassArguments | None = None
    import_overrides: dict[str, str] | None = None
    type_mappings: list[str] | None = None
    type_overrides: dict[str, str] | None = None
    read_only_write_only_model_type: ReadOnlyWriteOnlyModelType | None = None
    field_type_collision_strategy: FieldTypeCollisionStrategy | None = None
    target_pydantic_version: TargetPydanticVersion | None = None
    default_value_overrides: Mapping[str, Any] | None = None
    external_ref_mapping: dict[str, str] | None = None

    @field_validator("additional_imports")
    @classmethod
    def validate_additional_imports(cls, value: list[str] | None) -> list[str] | None:
        """Require additional imports to be safe Python import paths."""
        return _validate_additional_import_paths(value)

    _validate_schema_validator_base_class_name = field_validator("schema_validator_base_class_name")(
        validate_schema_validator_base_class_name
    )


class GraphQLParserConfig(ParserConfig):
    """Configuration model for GraphQLParser.__init__()."""

    data_model_scalar_type: type[DataModel] = DataTypeScalar
    data_model_union_type: type[DataModel] = DataTypeUnion
    graphql_no_typename: bool = False


class JSONSchemaParserConfig(ParserConfig):
    """Configuration model for JsonSchemaParser.__init__()."""

    jsonschema_version: JsonSchemaVersion | None = None
    schema_version_mode: VersionMode | None = None


class AvroParserConfig(JSONSchemaParserConfig):
    """Configuration model for AvroParser.__init__()."""


class OpenAPIParserConfig(JSONSchemaParserConfig):
    """Configuration model for OpenAPIParser.__init__()."""

    openapi_scopes: list[OpenAPIScope] | None = None
    include_path_parameters: bool = False
    use_status_code_in_response_name: bool = False
    openapi_include_paths: list[str] | None = None
    openapi_include_info_version: bool = False
    openapi_version: OpenAPIVersion | None = None


class AsyncAPIParserConfig(JSONSchemaParserConfig):
    """Configuration model for AsyncAPIParser.__init__()."""

    openapi_scopes: list[OpenAPIScope] | None = None
    include_path_parameters: bool = False
    use_status_code_in_response_name: bool = False
    openapi_include_paths: list[str] | None = None
    openapi_include_info_version: bool = False
    openapi_version: OpenAPIVersion | None = None
    asyncapi_version: AsyncAPIVersion | None = None


class XMLSchemaParserConfig(JSONSchemaParserConfig):
    """Configuration model for XMLSchemaParser.__init__()."""

    xmlschema_version: XMLSchemaVersion | None = None


class ProtobufParserConfig(JSONSchemaParserConfig):
    """Configuration model for ProtobufParser.__init__()."""

    protobuf_version: ProtobufVersion | None = None


class ParseConfig(BaseModel):
    """Configuration model for Parser.parse()."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True, defer_build=True)

    with_import: bool | None = True
    format_: bool | None = True
    settings_path: Path | None = None
    disable_future_imports: bool = False
    all_exports_scope: AllExportsScope | None = None
    all_exports_collision_strategy: AllExportsCollisionStrategy | None = None
    module_split_mode: ModuleSplitMode | None = None
    collect_model_metadata: bool = False
