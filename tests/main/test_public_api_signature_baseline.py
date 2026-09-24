"""Public API signature baselines from origin/main."""

from __future__ import annotations

import ast
import importlib
import inspect
import types
from typing import TYPE_CHECKING, Annotated, Any, ForwardRef, Union, get_args, get_origin

import pytest
from typing_extensions import NotRequired

from datamodel_code_generator import DEFAULT_SHARED_MODULE_NAME, generate
from datamodel_code_generator.enums import (
    AliasGenerator,
    AllExportsCollisionStrategy,
    AllExportsScope,
    AllOfClassHierarchy,
    AllOfMergeMode,
    ClassNameAffixScope,
    CollapseRootModelsNameStrategy,
    CustomFileHeaderMode,
    DataModelType,
    DefaultValueType,
    FieldTypeCollisionStrategy,
    GraphQLScope,
    HTTPBackend,
    InputFileType,
    ModuleSplitMode,
    NamingStrategy,
    OpenAPIScope,
    ReadOnlyWriteOnlyModelType,
    ReuseScope,
    SchemaValidatorType,
    TargetPydanticVersion,
    VersionMode,
)
from datamodel_code_generator.format import PythonVersionMin
from datamodel_code_generator.model.pydantic_v2 import BaseModel, DataModelField, DataTypeManager, RootModel
from datamodel_code_generator.parser.base import YamlValue, title_to_class_name

if TYPE_CHECKING:
    from collections import defaultdict
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from pathlib import Path
    from typing import Unpack
    from urllib.parse import ParseResult

    from datamodel_code_generator import GeneratedModules
    from datamodel_code_generator._types import GenerateConfigDict
    from datamodel_code_generator.config import GenerateConfig
    from datamodel_code_generator.format import DateClassType, DatetimeClassType, Formatter, PythonVersion
    from datamodel_code_generator.model import DataModel, DataModelFieldBase
    from datamodel_code_generator.model.dataclass import DataclassArguments
    from datamodel_code_generator.model.pydantic_v2 import UnionMode
    from datamodel_code_generator.parser import DefaultPutDict, LiteralType
    from datamodel_code_generator.parser.base import ModuleContext, ModulePath, ParseConfig, Result
    from datamodel_code_generator.preset_names import PresetName
    from datamodel_code_generator.types import StrictTypes
    from datamodel_code_generator.validators import ModelValidators


_PUBLIC_MODULE_EXPORTS: dict[str, frozenset[str]] = {
    "datamodel_code_generator": frozenset({
        "DEFAULT_FORMATTERS",
        "DEFAULT_SHARED_MODULE_NAME",
        "MAX_VERSION",
        "MIN_VERSION",
        "AllExportsCollisionStrategy",
        "AllExportsScope",
        "AllOfClassHierarchy",
        "AllOfMergeMode",
        "AliasGenerator",
        "AsyncAPIVersion",
        "ClassNameAffixScope",
        "CollapseRootModelsNameStrategy",
        "CustomFileHeaderMode",
        "DanglingRefWarning",
        "DateClassType",
        "DatetimeClassType",
        "DefaultValueType",
        "DefaultValueTypeWarning",
        "DefaultPutDict",
        "Error",
        "FieldTypeCollisionStrategy",
        "GenerateConfig",
        "GeneratedModules",
        "GraphQLScope",
        "HTTPBackend",
        "InputFileType",
        "InputModelRefStrategy",
        "InvalidClassNameError",
        "InvalidFileFormatError",
        "JsonSchemaVersion",
        "LiteralType",
        "ModuleSplitMode",
        "NamingStrategy",
        "OpenAPIScope",
        "OpenAPIVersion",
        "ProtobufVersion",
        "PythonVersion",
        "PythonVersionMin",
        "ReadOnlyWriteOnlyModelType",
        "ReuseScope",
        "SchemaValidatorType",
        "SchemaParseError",
        "SchemaResourceRefWarning",
        "TargetPydanticVersion",
        "VersionMode",
        "XMLSchemaVersion",
        "clear_dynamic_models_cache",
        "detect_jsonschema_version",
        "detect_openapi_version",
        "detect_xmlschema_version",
        "enable_parsed_source_cache",
        "generate",
        "generate_dynamic_models",
    }),
    "datamodel_code_generator._types": frozenset({
        "AsyncAPIParserConfigDict",
        "AvroParserConfigDict",
        "GenerateConfigDict",
        "GraphQLParserConfigDict",
        "JSONSchemaParserConfigDict",
        "ModelDict",
        "OpenAPIParserConfigDict",
        "ParseConfigDict",
        "ParserConfigDict",
        "ProtobufParserConfigDict",
        "XMLSchemaParserConfigDict",
    }),
    "datamodel_code_generator.arguments": frozenset({"DEFAULT_ENCODING", "arg_parser", "namespace"}),
    "datamodel_code_generator.enums": frozenset({
        "DEFAULT_SHARED_MODULE_NAME",
        "MAX_VERSION",
        "MIN_VERSION",
        "AllExportsCollisionStrategy",
        "AllExportsScope",
        "AllOfClassHierarchy",
        "AllOfMergeMode",
        "AliasGenerator",
        "AsyncAPIVersion",
        "ClassNameAffixScope",
        "CollapseRootModelsNameStrategy",
        "CustomFileHeaderMode",
        "DataModelType",
        "DataclassArguments",
        "DefaultValueType",
        "FieldTypeCollisionStrategy",
        "GraphQLScope",
        "HTTPBackend",
        "InputFileType",
        "InputModelRefStrategy",
        "JsonSchemaVersion",
        "ModuleSplitMode",
        "NamingStrategy",
        "OpenAPIScope",
        "OpenAPIVersion",
        "ProtobufVersion",
        "ReadOnlyWriteOnlyModelType",
        "ReuseScope",
        "SchemaValidatorType",
        "StrictTypes",
        "TargetPydanticVersion",
        "UnionMode",
        "VersionMode",
        "XMLSchemaVersion",
    }),
    "datamodel_code_generator.model": frozenset({
        "DEFAULT_TARGET_PYTHON_VERSION",
        "UNDEFINED",
        "ConstraintsBase",
        "DataModel",
        "DataModelFieldBase",
        "DataModelSet",
        "_rebuild_model_with_datamodel_namespace",
        "get_data_model_types",
    }),
    "datamodel_code_generator.model.pydantic_v2": frozenset({
        "BaseModel",
        "DataModelField",
        "DataTypeManager",
        "RootModel",
        "RootModelTypeAlias",
        "UnionMode",
        "dump_resolve_reference_action",
    }),
    "datamodel_code_generator.parser": frozenset({"DefaultPutDict", "LiteralType"}),
    "datamodel_code_generator.parser.avro": frozenset({
        "AvroParser",
        "convert_avro_schema_data",
        "is_avro_schema_data",
    }),
    "datamodel_code_generator.parser.protobuf": frozenset({"ProtobufParser", "convert_protobuf_schema_data"}),
    "datamodel_code_generator.parser.schema_version": frozenset({
        "DataFormatMapping",
        "FeatureMetadata",
        "JsonSchemaFeatures",
        "OpenAPISchemaFeatures",
        "SchemaFeaturesT",
        "detect_asyncapi_version",
        "detect_jsonschema_version",
        "detect_openapi_version",
        "get_data_formats",
    }),
    "datamodel_code_generator.parser.xmlschema": frozenset({
        "XMLSchemaParser",
        "convert_xml_schema_data",
        "detect_xmlschema_version",
        "is_xml_schema_text",
    }),
}


_DE_FACTO_PUBLIC_SYMBOLS: dict[str, frozenset[str]] = {
    "datamodel_code_generator.format": frozenset({
        "CodeFormatter",
        "Formatter",
        "PythonVersion",
        "PythonVersionMin",
    }),
    "datamodel_code_generator.imports": frozenset({
        "IMPORT_ANNOTATIONS",
        "Import",
        "Imports",
    }),
    "datamodel_code_generator.parser.base": frozenset({
        "Parser",
        "SPECIAL_PATH_FORMAT",
        "Source",
        "YamlValue",
        "title_to_class_name",
    }),
    "datamodel_code_generator.parser.graphql": frozenset({"GraphQLParser"}),
    "datamodel_code_generator.parser.jsonschema": frozenset({
        "JsonSchemaObject",
        "JsonSchemaParser",
        "get_model_by_path",
        "get_special_path",
    }),
    "datamodel_code_generator.parser.openapi": frozenset({
        "MediaObject",
        "OpenAPIParser",
        "Operation",
        "ParameterObject",
        "RequestBodyObject",
        "ResponseObject",
    }),
    "datamodel_code_generator.reference": frozenset({
        "ModelResolver",
        "Reference",
        "snake_to_upper_camel",
    }),
    "datamodel_code_generator.types": frozenset({
        "DataType",
        "DataTypeManager",
        "StrictTypes",
        "Types",
    }),
}


def _baseline_generate(
    input_: Path | str | ParseResult | Mapping[str, Any] | list[Any],
    *,
    config: GenerateConfig | None = None,
    input_filename: str | None = None,
    input_file_type: InputFileType = InputFileType.Auto,
    output: Path | None = None,
    emit_model_metadata: Path | None = None,
    output_model_type: DataModelType = DataModelType.PydanticV2BaseModel,
    preset: PresetName | None = None,
    target_python_version: PythonVersion = PythonVersionMin,
    target_pydantic_version: TargetPydanticVersion | None = None,
    base_class: str = "",
    base_class_map: dict[str, str | list[str]] | None = None,
    model_name_map: dict[str, str] | None = None,
    additional_imports: list[str] | None = None,
    class_decorators: list[str] | None = None,
    custom_template_dir: Path | None = None,
    schema_validator_type: SchemaValidatorType | None = None,
    extra_template_data: defaultdict[str, dict[str, Any]] | None = None,
    validators: Mapping[str, ModelValidators] | None = None,
    generate_schema_validators: bool = False,
    schema_validator_base_class_name: str | None = None,
    validation: bool = False,
    field_constraints: bool = False,
    alias_generator: AliasGenerator | None = None,
    snake_case_field: bool = False,
    strip_default_none: bool = False,
    aliases: Mapping[str, str | list[str]] | None = None,
    serialization_aliases: Mapping[str, str] | None = None,
    default_value_overrides: Mapping[str, Any] | None = None,
    disable_timestamp: bool = False,
    enable_version_header: bool = False,
    enable_command_header: bool = False,
    enable_generated_header_marker: bool = False,
    command_line: str | None = None,
    allow_population_by_field_name: bool = False,
    allow_extra_fields: bool = False,
    extra_fields: str | None = None,
    use_generic_base_class: bool = False,
    apply_default_values_for_required_fields: bool = False,
    force_optional_for_required_fields: bool = False,
    class_name: str | None = None,
    allow_leading_underscore_class_name: bool = False,
    class_name_prefix: str | None = None,
    class_name_suffix: str | None = None,
    class_name_affix_scope: ClassNameAffixScope = ClassNameAffixScope.All,
    use_standard_collections: bool = True,
    use_schema_description: bool = False,
    use_field_description: bool = False,
    use_field_description_example: bool = False,
    use_attribute_docstrings: bool = False,
    use_inline_field_description: bool = False,
    use_single_line_docstring: bool = False,
    use_default_kwarg: bool = False,
    deserialize_default_values: Sequence[DefaultValueType] = (),
    use_missing_sentinel: bool = False,
    reuse_model: bool = False,
    reuse_scope: ReuseScope = ReuseScope.Module,
    shared_module_name: str = DEFAULT_SHARED_MODULE_NAME,
    encoding: str = "utf-8",
    enum_field_as_literal: LiteralType | None = None,
    enum_field_as_literal_map: dict[str, str] | None = None,
    ignore_enum_constraints: bool = False,
    use_one_literal_as_default: bool = False,
    use_enum_values_in_discriminator: bool = False,
    set_default_enum_member: bool = False,
    use_subclass_enum: bool = False,
    use_specialized_enum: bool = True,
    strict_nullable: bool = False,
    use_generic_container_types: bool = False,
    enable_faux_immutability: bool = False,
    disable_appending_item_suffix: bool = False,
    strict_types: Sequence[StrictTypes] | None = None,
    empty_enum_field_name: str | None = None,
    custom_class_name_generator: Callable[[str], str] | None = None,
    field_extra_keys: set[str] | None = None,
    field_include_all_keys: bool = False,
    field_extra_keys_without_x_prefix: set[str] | None = None,
    model_extra_keys: set[str] | None = None,
    model_extra_keys_without_x_prefix: set[str] | None = None,
    openapi_scopes: list[OpenAPIScope] | None = None,
    include_path_parameters: bool = False,
    openapi_include_paths: list[str] | None = None,
    openapi_include_info_version: bool = False,
    graphql_scopes: list[GraphQLScope] | None = None,
    graphql_no_typename: bool = False,
    wrap_string_literal: bool | None = None,
    use_title_as_name: bool = False,
    infer_union_variant_names: bool = False,
    use_operation_id_as_name: bool = False,
    use_unique_items_as_set: bool = False,
    use_tuple_for_fixed_items: bool = False,
    use_tuple_for_fixed_length_arrays: bool = False,
    use_total_false_for_typed_dict: bool = False,
    use_closed_typed_dict: bool = True,
    allof_merge_mode: AllOfMergeMode = AllOfMergeMode.Constraints,
    allof_class_hierarchy: AllOfClassHierarchy = AllOfClassHierarchy.IfNoConflict,
    allow_remote_refs: bool | None = None,
    strict_refs: bool = False,
    allow_private_network: bool = False,
    http_backend: HTTPBackend = HTTPBackend.AUTO,
    http_headers: Sequence[tuple[str, str]] | None = None,
    http_local_ref_path: Path | None = None,
    http_ignore_tls: bool = False,
    http_timeout: float | None = None,
    lockfile: Path | None = None,
    update_lock: bool = False,
    locked: bool = False,
    use_annotated: bool = False,
    use_serialize_as_any: bool = False,
    use_non_positive_negative_number_constrained_types: bool = False,
    use_decimal_for_multiple_of: bool = False,
    original_field_name_delimiter: str | None = None,
    use_double_quotes: bool = False,
    use_union_operator: bool = True,
    collapse_root_models: bool = False,
    collapse_root_models_name_strategy: CollapseRootModelsNameStrategy | None = None,
    collapse_reuse_models: bool = False,
    skip_root_model: bool = False,
    use_root_model_sequence_interface: bool = False,
    use_type_alias: bool = False,
    use_type_alias_type: bool = False,
    use_root_model_type_alias: bool = False,
    special_field_name_prefix: str | None = None,
    remove_special_field_name_prefix: bool = False,
    capitalise_enum_members: bool = False,
    keep_model_order: bool = False,
    custom_file_header: str | None = None,
    custom_file_header_path: Path | None = None,
    custom_file_header_mode: CustomFileHeaderMode = CustomFileHeaderMode.Replace,
    custom_formatters: list[str] | None = None,
    custom_formatters_kwargs: dict[str, Any] | None = None,
    use_pendulum: bool = False,
    use_standard_primitive_types: bool = False,
    use_object_type: bool = False,
    http_query_parameters: Sequence[tuple[str, str]] | None = None,
    treat_dot_as_module: bool | None = None,
    strict_dotted_module_names: bool = False,
    use_exact_imports: bool = False,
    use_type_checking_imports: bool | None = None,
    union_mode: UnionMode | None = None,
    output_datetime_class: DatetimeClassType | None = None,
    output_date_class: DateClassType | None = None,
    keyword_only: bool = False,
    frozen_dataclasses: bool = False,
    no_alias: bool = False,
    use_serialization_alias: bool = False,
    use_frozen_field: bool = False,
    use_default_factory_for_optional_nested_models: bool = False,
    formatters: list[Formatter] | None = None,
    builtin_format_line_length: int | None = None,
    settings_path: Path | None = None,
    parent_scoped_naming: bool = False,
    naming_strategy: NamingStrategy | None = None,
    duplicate_name_suffix: dict[str, str] | None = None,
    dataclass_arguments: DataclassArguments | None = None,
    disable_future_imports: bool = False,
    import_overrides: dict[str, str] | None = None,
    type_mappings: list[str] | None = None,
    type_overrides: dict[str, str] | None = None,
    read_only_write_only_model_type: ReadOnlyWriteOnlyModelType | None = None,
    use_status_code_in_response_name: bool = False,
    all_exports_scope: AllExportsScope | None = None,
    all_exports_collision_strategy: AllExportsCollisionStrategy | None = None,
    field_type_collision_strategy: FieldTypeCollisionStrategy | None = None,
    module_split_mode: ModuleSplitMode | None = None,
    schema_version: str | None = None,
    schema_version_mode: VersionMode | None = None,
    external_ref_mapping: dict[str, str] | None = None,
) -> str | object | None:
    raise NotImplementedError


def _baseline_generate_runtime_signature(
    input_: Path | str | ParseResult | Mapping[str, Any] | list[Any],
    *,
    config: GenerateConfig | None = None,
    **options: Unpack[GenerateConfigDict],
) -> str | GeneratedModules | None:
    raise NotImplementedError


def _baseline_parser_parse_runtime_signature(
    self,  # noqa: ANN001
    with_import: bool | None = True,
    format_: bool | None = True,
    settings_path: Path | None = None,
    disable_future_imports: bool = False,
    all_exports_scope: AllExportsScope | None = None,
    all_exports_collision_strategy: AllExportsCollisionStrategy | None = None,
    module_split_mode: ModuleSplitMode | None = None,
    collect_model_metadata: bool = False,
) -> str | dict[tuple[str, ...], Result]:
    raise NotImplementedError


def _baseline_process_single_module_runtime_signature(
    self,  # noqa: ANN001
    module_: ModulePath,
    models: list[DataModel],
    results: dict[ModulePath, Result],
    config: ParseConfig,
    internal_modules: set[ModulePath],
    model_path_to_module_name: dict[str, str],
    require_update_action_models: list[str],
    unused_models: list[DataModel],
) -> ModuleContext:
    raise NotImplementedError


def _baseline_openapi_parser_parse_runtime_signature(
    self,  # noqa: ANN001
    *args: Any,
    **kwargs: Any,
) -> str | dict[tuple[str, ...], Result]:
    raise NotImplementedError


class _BaselineParser:
    def __init__(
        self,
        source: str | Path | list[Path] | ParseResult | dict[str, YamlValue],
        *,
        data_model_type: type[DataModel] = BaseModel,
        data_model_root_type: type[DataModel] = RootModel,
        data_type_manager_type: type[DataTypeManager] = DataTypeManager,
        data_model_field_type: type[DataModelFieldBase] = DataModelField,
        base_class: str | None = None,
        base_class_map: dict[str, str | list[str]] | None = None,
        model_name_map: dict[str, str] | None = None,
        additional_imports: list[str] | None = None,
        class_decorators: list[str] | None = None,
        custom_template_dir: Path | None = None,
        extra_template_data: defaultdict[str, dict[str, Any]] | None = None,
        validators: Mapping[str, ModelValidators] | None = None,
        generate_schema_validators: bool = False,
        schema_validator_base_class_name: str | None = None,
        target_python_version: PythonVersion = PythonVersionMin,
        dump_resolve_reference_action: Callable[[Iterable[str]], str] | None = None,
        validation: bool = False,
        field_constraints: bool = False,
        alias_generator: AliasGenerator | None = None,
        snake_case_field: bool = False,
        strip_default_none: bool = False,
        aliases: Mapping[str, str | list[str]] | None = None,
        serialization_aliases: Mapping[str, str] | None = None,
        default_value_overrides: Mapping[str, Any] | None = None,
        allow_population_by_field_name: bool = False,
        apply_default_values_for_required_fields: bool = False,
        allow_extra_fields: bool = False,
        extra_fields: str | None = None,
        use_generic_base_class: bool = False,
        force_optional_for_required_fields: bool = False,
        class_name: str | None = None,
        allow_leading_underscore_class_name: bool = False,
        class_name_prefix: str | None = None,
        class_name_suffix: str | None = None,
        class_name_affix_scope: ClassNameAffixScope = ClassNameAffixScope.All,
        use_standard_collections: bool = False,
        base_path: Path | None = None,
        use_schema_description: bool = False,
        use_field_description: bool = False,
        use_field_description_example: bool = False,
        use_attribute_docstrings: bool = False,
        use_inline_field_description: bool = False,
        use_single_line_docstring: bool = False,
        use_default_kwarg: bool = False,
        deserialize_default_values: Sequence[DefaultValueType] = (),
        use_missing_sentinel: bool = False,
        reuse_model: bool = False,
        reuse_scope: ReuseScope | None = None,
        shared_module_name: str = DEFAULT_SHARED_MODULE_NAME,
        encoding: str = "utf-8",
        enum_field_as_literal: LiteralType | None = None,
        enum_field_as_literal_map: dict[str, str] | None = None,
        ignore_enum_constraints: bool = False,
        set_default_enum_member: bool = False,
        use_subclass_enum: bool = False,
        use_specialized_enum: bool = True,
        strict_nullable: bool = False,
        use_generic_container_types: bool = False,
        enable_faux_immutability: bool = False,
        remote_text_cache: DefaultPutDict[str, str] | None = None,
        disable_appending_item_suffix: bool = False,
        strict_types: Sequence[StrictTypes] | None = None,
        empty_enum_field_name: str | None = None,
        custom_class_name_generator: Callable[[str], str] | None = title_to_class_name,
        field_extra_keys: set[str] | None = None,
        field_include_all_keys: bool = False,
        field_extra_keys_without_x_prefix: set[str] | None = None,
        model_extra_keys: set[str] | None = None,
        model_extra_keys_without_x_prefix: set[str] | None = None,
        wrap_string_literal: bool | None = None,
        use_title_as_name: bool = False,
        infer_union_variant_names: bool = False,
        use_operation_id_as_name: bool = False,
        use_unique_items_as_set: bool = False,
        use_tuple_for_fixed_items: bool = False,
        use_tuple_for_fixed_length_arrays: bool = False,
        use_total_false_for_typed_dict: bool = False,
        use_closed_typed_dict: bool = True,
        allof_merge_mode: AllOfMergeMode = AllOfMergeMode.Constraints,
        allof_class_hierarchy: AllOfClassHierarchy = AllOfClassHierarchy.IfNoConflict,
        allow_remote_refs: bool | None = None,
        strict_refs: bool = False,
        allow_private_network: bool = False,
        http_backend: HTTPBackend = HTTPBackend.AUTO,
        http_headers: Sequence[tuple[str, str]] | None = None,
        http_local_ref_path: Path | None = None,
        http_ignore_tls: bool = False,
        http_timeout: float | None = None,
        use_annotated: bool = False,
        use_serialize_as_any: bool = False,
        use_non_positive_negative_number_constrained_types: bool = False,
        use_decimal_for_multiple_of: bool = False,
        original_field_name_delimiter: str | None = None,
        use_double_quotes: bool = False,
        use_union_operator: bool = False,
        allow_responses_without_content: bool = False,
        collapse_root_models: bool = False,
        collapse_root_models_name_strategy: CollapseRootModelsNameStrategy | None = None,
        collapse_reuse_models: bool = False,
        skip_root_model: bool = False,
        use_root_model_sequence_interface: bool = False,
        use_type_alias: bool = False,
        special_field_name_prefix: str | None = None,
        remove_special_field_name_prefix: bool = False,
        capitalise_enum_members: bool = False,
        keep_model_order: bool = False,
        use_one_literal_as_default: bool = False,
        use_enum_values_in_discriminator: bool = False,
        known_third_party: list[str] | None = None,
        custom_formatters: list[str] | None = None,
        custom_formatters_kwargs: dict[str, Any] | None = None,
        use_pendulum: bool = False,
        use_standard_primitive_types: bool = False,
        use_object_type: bool = False,
        http_query_parameters: Sequence[tuple[str, str]] | None = None,
        treat_dot_as_module: bool | None = None,
        strict_dotted_module_names: bool = False,
        use_exact_imports: bool = False,
        use_type_checking_imports: bool | None = None,
        default_field_extras: dict[str, Any] | None = None,
        target_datetime_class: DatetimeClassType | None = None,
        target_date_class: DateClassType | None = None,
        keyword_only: bool = False,
        frozen_dataclasses: bool = False,
        no_alias: bool = False,
        use_serialization_alias: bool = False,
        use_frozen_field: bool = False,
        use_default_factory_for_optional_nested_models: bool = False,
        formatters: list[Formatter] | None = None,
        builtin_format_line_length: int | None = None,
        defer_formatting: bool = False,
        parent_scoped_naming: bool = False,
        naming_strategy: NamingStrategy | None = None,
        duplicate_name_suffix: dict[str, str] | None = None,
        dataclass_arguments: DataclassArguments | None = None,
        import_overrides: dict[str, str] | None = None,
        type_mappings: list[str] | None = None,
        type_overrides: dict[str, str] | None = None,
        read_only_write_only_model_type: ReadOnlyWriteOnlyModelType | None = None,
        field_type_collision_strategy: FieldTypeCollisionStrategy | None = None,
        target_pydantic_version: TargetPydanticVersion | None = None,
        external_ref_mapping: dict[str, str] | None = None,
    ) -> None:
        raise NotImplementedError

    def parse(
        self,
        with_import: bool | None = True,
        format_: bool | None = True,
        settings_path: Path | None = None,
        disable_future_imports: bool = False,
        all_exports_scope: AllExportsScope | None = None,
        all_exports_collision_strategy: AllExportsCollisionStrategy | None = None,
        module_split_mode: ModuleSplitMode | None = None,
        collect_model_metadata: bool = False,
    ) -> str | dict[tuple[str, ...], Any]:
        raise NotImplementedError


def _kwonly_params(signature: inspect.Signature) -> list[inspect.Parameter]:
    return [param for param in signature.parameters.values() if param.kind is inspect.Parameter.KEYWORD_ONLY]


def _kwonly_by_name(signature: inspect.Signature) -> dict[str, inspect.Parameter]:
    return {param.name: param for param in _kwonly_params(signature)}


def _params_by_name(signature: inspect.Signature) -> dict[str, inspect.Parameter]:
    return {
        name: param
        for name, param in signature.parameters.items()
        if name != "self" and param.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
    }


def _type_to_str(tp: Any) -> str:  # pragma: no cover
    """Convert type to normalized string."""
    if tp is type(None):
        return "None"
    if isinstance(tp, type):
        return tp.__name__
    if isinstance(tp, str):
        return tp
    return (
        str(tp)
        .replace("collections.abc.", "")
        .replace("collections.", "")
        .replace("typing.", "")
        .replace("pathlib.", "")
    )


def _normalize_union_str(type_str: str) -> str:
    """Normalize a union type string by sorting its components recursively."""
    try:
        tree = ast.parse(type_str, mode="eval")
    except SyntaxError:  # pragma: no cover
        return type_str

    def normalize_node(node: ast.expr) -> str:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):

            def collect_union_parts(n: ast.expr) -> list[str]:
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr):
                    return collect_union_parts(n.left) + collect_union_parts(n.right)
                return [normalize_node(n)]

            parts = collect_union_parts(node)
            return " | ".join(sorted(parts))
        if isinstance(node, ast.Subscript):
            value = ast.unparse(node.value)
            if isinstance(node.slice, ast.Tuple):
                args = [normalize_node(elt) for elt in node.slice.elts]
                return f"{value}[{', '.join(args)}]"
            return f"{value}[{normalize_node(node.slice)}]"
        return ast.unparse(node)

    return normalize_node(tree.body)


@pytest.mark.parametrize(
    ("input_str", "expected"),
    [
        ("str | int", "int | str"),
        ("str | int | None", "None | int | str"),
        ("int", "int"),
        ("Mapping[str, str]", "Mapping[str, str]"),
        ("Mapping[str, str | list[str]]", "Mapping[str, list[str] | str]"),
        ("Mapping[str, list[str] | str]", "Mapping[str, list[str] | str]"),
        ("Mapping[str, str | list[str]] | None", "Mapping[str, list[str] | str] | None"),
        ("dict[str, int | str | None]", "dict[str, None | int | str]"),
        ("list[str | int]", "list[int | str]"),
        ("tuple[str | int, bool | None]", "tuple[int | str, None | bool]"),
    ],
)
def test_normalize_union_str(input_str: str, expected: str) -> None:
    """Test _normalize_union_str correctly normalizes union types recursively."""
    assert _normalize_union_str(input_str) == expected


@pytest.mark.parametrize(
    ("type_a", "type_b"),
    [
        ("Mapping[str, str | list[str]]", "Mapping[str, list[str] | str]"),
        ("str | int | None", "None | str | int"),
        ("dict[str, int | str]", "dict[str, str | int]"),
    ],
)
def test_normalize_union_str_equivalence(type_a: str, type_b: str) -> None:
    """Test that different orderings of the same union type normalize to the same string."""
    assert _normalize_union_str(type_a) == _normalize_union_str(type_b)


def _normalize_type(tp: Any) -> str:  # noqa: PLR0911  # pragma: no cover
    """Normalize type for comparison between Config and TypedDict."""
    if tp is None or tp is type(None):
        return "None"

    if isinstance(tp, str):
        return _normalize_union_str(tp)

    if isinstance(tp, ForwardRef):
        arg = tp.__forward_arg__
        if arg.startswith("NotRequired[") and arg.endswith("]"):
            arg = arg[12:-1]
        return _normalize_union_str(arg)

    if isinstance(tp, list):
        return f"[{', '.join(_normalize_type(t) for t in tp)}]"

    origin = get_origin(tp)
    args = get_args(tp)

    if origin in {Annotated, NotRequired}:
        return _normalize_type(args[0]) if args else _type_to_str(tp)

    if origin is Union or isinstance(tp, types.UnionType):
        if isinstance(tp, types.UnionType):
            args = get_args(tp)
        normalized_args = sorted(_normalize_type(a) for a in args)
        return _type_to_str(" | ".join(normalized_args))

    if origin is not None:
        if args:
            normalized_args = [_normalize_type(a) for a in args]
            origin_name = getattr(origin, "__name__", str(origin))
            return _type_to_str(f"{origin_name}[{', '.join(normalized_args)}]")
        return _type_to_str(origin)

    return _type_to_str(tp)


def _types_match(config_type: Any, dict_type: Any) -> bool:
    """Check if Config type and TypedDict type are equivalent."""
    return _normalize_type(config_type) == _normalize_type(dict_type)


@pytest.mark.parametrize(("module_name", "expected_exports"), _PUBLIC_MODULE_EXPORTS.items())
def test_public_module_all_exports_match_baseline(module_name: str, expected_exports: frozenset[str]) -> None:
    """Pin focused public module export surfaces used by compatibility shims."""
    module = importlib.import_module(module_name)
    module_exports = list(module.__all__)
    actual_exports = set(module_exports)
    missing_exports = [name for name in module_exports if not hasattr(module, name)]

    assert len(module_exports) == len(actual_exports), f"{module_name}.__all__ contains duplicate names"
    assert actual_exports == expected_exports, (
        f"{module_name}.__all__ changed:\n"
        f"  removed: {sorted(expected_exports - actual_exports)}\n"
        f"  added: {sorted(actual_exports - expected_exports)}"
    )
    assert missing_exports == [], f"{module_name}.__all__ contains non-importable names: {missing_exports}"


@pytest.mark.parametrize(("module_name", "expected_symbols"), _DE_FACTO_PUBLIC_SYMBOLS.items())
def test_de_facto_public_symbols_remain_importable(module_name: str, expected_symbols: frozenset[str]) -> None:
    """Pin importable symbols used downstream without requiring module __all__ changes."""
    module = importlib.import_module(module_name)
    missing_symbols = sorted(name for name in expected_symbols if not hasattr(module, name))

    assert missing_symbols == [], f"{module_name} no longer exposes de facto public symbols: {missing_symbols}"


def test_data_model_set_tuple_abi_matches_baseline() -> None:
    """Keep DataModelSet compatible with downstream tuple construction and unpacking."""
    from datamodel_code_generator.model import DataModelSet, get_data_model_types

    expected_fields = (
        "data_model",
        "root_model",
        "field_model",
        "data_type_manager",
        "dump_resolve_reference_action",
        "scalar_model",
        "union_model",
        "known_third_party",
    )
    model_types = get_data_model_types(DataModelType.PydanticV2BaseModel)
    (
        data_model,
        root_model,
        field_model,
        data_type_manager,
        dump_resolve_reference_action,
        scalar_model,
        union_model,
        known_third_party,
    ) = model_types

    defaulted_model_types = DataModelSet(
        data_model,
        root_model,
        field_model,
        data_type_manager,
        dump_resolve_reference_action,
        scalar_model,
        union_model,
    )

    assert issubclass(DataModelSet, tuple)
    assert DataModelSet._fields == expected_fields
    assert DataModelSet._field_defaults == {"known_third_party": None}
    assert len(model_types) == len(expected_fields)
    assert defaulted_model_types.known_third_party is None
    assert (
        DataModelSet(
            data_model,
            root_model,
            field_model,
            data_type_manager,
            dump_resolve_reference_action,
            scalar_model,
            union_model,
            known_third_party,
        )
        == model_types
    )


@pytest.mark.parametrize(
    ("context_type", "expected_fields"),
    [
        pytest.param(
            "ModuleContext",
            ("module", "module_key", "models", "is_init", "imports", "scoped_model_resolver"),
            id="module-context",
        ),
        pytest.param(
            "ParseConfig",
            (
                "with_import",
                "use_deferred_annotations",
                "code_formatter",
                "module_split_mode",
                "all_exports_scope",
                "all_exports_collision_strategy",
            ),
            id="parse-config",
        ),
    ],
)
def test_parser_named_tuple_context_abi_matches_baseline(
    context_type: str,
    expected_fields: tuple[str, ...],
) -> None:
    """Keep parser processing contexts compatible with tuple consumers."""
    from datamodel_code_generator.parser import base

    context_class = getattr(base, context_type)
    values = tuple(object() for _ in expected_fields)
    context = context_class(*values)

    assert context_class.__module__ == "datamodel_code_generator.parser.base"
    assert issubclass(context_class, tuple)
    assert context_class._fields == expected_fields
    assert context_class._field_defaults == {}
    assert tuple(context) == values
    assert tuple(getattr(context, field) for field in expected_fields) == values
    if context_type == "ParseConfig":
        replacement = object()
        updated_context = context._replace(with_import=replacement)

        assert updated_context.with_import is replacement
        assert tuple(updated_context)[1:] == values[1:]


def test_json_schema_parser_extension_method_signatures_match_baseline() -> None:
    """Keep parser subclass hooks compatible with their established signatures."""
    from datamodel_code_generator.model.base import UNDEFINED
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parse_item = inspect.signature(JsonSchemaParser.parse_item).parameters
    parse_root_type = inspect.signature(JsonSchemaParser.parse_root_type).parameters
    register_root_model = inspect.signature(JsonSchemaParser._register_root_model).parameters

    assert tuple(parse_item) == ("self", "name", "item", "path", "singular_name", "parent")
    assert all(parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for parameter in parse_item.values())
    assert parse_item["singular_name"].default is False
    assert parse_item["parent"].default is None

    assert tuple(parse_root_type) == ("self", "name", "obj", "path")
    assert all(parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for parameter in parse_root_type.values())

    assert tuple(register_root_model) == (
        "self",
        "reference",
        "fields",
        "obj",
        "custom_base_class_name",
        "description",
        "default",
    )
    assert register_root_model["self"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for name, parameter in register_root_model.items()
        if name != "self"
    )
    assert register_root_model["description"].default is None
    assert register_root_model["default"].default is UNDEFINED


def test_generate_signature_matches_baseline() -> None:
    """Ensure generate keeps backward compatibility via GenerateConfigDict.

    The new signature uses **options: Unpack[GenerateConfigDict], so we verify
    that GenerateConfigDict has all the same keys as the baseline function's
    keyword-only arguments (except 'config'), matching types and default values.
    """
    from datamodel_code_generator._types import GenerateConfigDict

    expected = inspect.signature(_baseline_generate)
    baseline_params = {k: v for k, v in _kwonly_by_name(expected).items() if k != "config"}
    dict_annotations = GenerateConfigDict.__annotations__

    # 1. Verify all baseline kwargs are in GenerateConfigDict (key names)
    baseline_kwargs = set(baseline_params.keys())
    dict_keys = set(dict_annotations.keys())
    assert baseline_kwargs == dict_keys, (
        f"Mismatch between baseline args and GenerateConfigDict keys:\n"
        f"  In baseline but not in dict: {baseline_kwargs - dict_keys}\n"
        f"  In dict but not in baseline: {dict_keys - baseline_kwargs}"
    )

    # 2. Verify types match between baseline and GenerateConfigDict
    for name, param in baseline_params.items():
        baseline_type = param.annotation
        dict_type = dict_annotations[name]
        assert _types_match(baseline_type, dict_type), (
            f"Type mismatch for '{name}':\n"
            f"  Baseline: {_normalize_type(baseline_type)}\n"
            f"  TypedDict: {_normalize_type(dict_type)}"
        )

    # 3. Verify default values match between baseline and GenerateConfig
    from datamodel_code_generator.config import GenerateConfig
    from datamodel_code_generator.model.pydantic_v2 import UnionMode
    from datamodel_code_generator.types import StrictTypes

    GenerateConfig.model_rebuild(
        _types_namespace={"DefaultValueType": DefaultValueType, "StrictTypes": StrictTypes, "UnionMode": UnionMode}
    )

    for name, param in baseline_params.items():
        config_default = GenerateConfig.model_fields[name].default
        assert config_default == param.default, (
            f"Default mismatch for '{name}':\n  Baseline: {param.default!r}\n  GenerateConfig: {config_default!r}"
        )


def test_generate_runtime_signature_matches_baseline() -> None:
    """Keep generate()'s introspected public callable shape stable."""
    assert generate.__module__ == "datamodel_code_generator"
    assert inspect.signature(generate) == inspect.signature(_baseline_generate_runtime_signature)


def test_parser_parse_runtime_signature_matches_baseline() -> None:
    """Keep Parser.parse()'s introspected public callable shape stable."""
    from datamodel_code_generator.parser.base import Parser

    assert Parser.parse.__module__ == "datamodel_code_generator.parser.base"
    assert inspect.signature(Parser.parse) == inspect.signature(_baseline_parser_parse_runtime_signature)


def test_parser_process_single_module_runtime_signature_matches_baseline() -> None:
    """Keep Parser._process_single_module()'s extension hook stable."""
    from datamodel_code_generator.parser.base import Parser

    assert Parser._process_single_module.__module__ == "datamodel_code_generator.parser.base"
    assert inspect.signature(Parser._process_single_module) == inspect.signature(
        _baseline_process_single_module_runtime_signature
    )


def test_openapi_and_asyncapi_parser_parse_runtime_signatures_match_baseline() -> None:
    """Keep OpenAPI's forwarding parse hook and AsyncAPI inheritance intact."""
    from datamodel_code_generator.parser.asyncapi import AsyncAPIParser
    from datamodel_code_generator.parser.openapi import OpenAPIParser

    expected = inspect.signature(_baseline_openapi_parser_parse_runtime_signature)

    assert OpenAPIParser.parse.__module__ == "datamodel_code_generator.parser.openapi"
    assert inspect.signature(OpenAPIParser.parse) == expected
    assert AsyncAPIParser.parse is OpenAPIParser.parse
    assert AsyncAPIParser.parse.__module__ == "datamodel_code_generator.parser.openapi"
    assert inspect.signature(AsyncAPIParser.parse) == expected


def test_parser_signature_matches_baseline() -> None:
    """Ensure Parser.__init__ keeps backward compatibility via ParserConfigDict.

    The new signature uses **options: Unpack[ParserConfigDict], so we verify
    that ParserConfigDict has all the same keys as the baseline function's
    keyword-only arguments (except 'config'), matching types and default values.
    """
    from datamodel_code_generator._types import ParserConfigDict

    expected = inspect.signature(_BaselineParser.__init__)
    baseline_params = {k: v for k, v in _kwonly_by_name(expected).items() if k != "config"}
    dict_annotations = ParserConfigDict.__annotations__

    baseline_kwargs = set(baseline_params.keys())
    dict_keys = set(dict_annotations.keys())
    assert baseline_kwargs == dict_keys, (
        f"Mismatch between baseline args and ParserConfigDict keys:\n"
        f"  In baseline but not in dict: {baseline_kwargs - dict_keys}\n"
        f"  In dict but not in baseline: {dict_keys - baseline_kwargs}"
    )

    for name, param in baseline_params.items():
        baseline_type = param.annotation
        dict_type = dict_annotations[name]
        assert _types_match(baseline_type, dict_type), (
            f"Type mismatch for '{name}':\n"
            f"  Baseline: {_normalize_type(baseline_type)}\n"
            f"  TypedDict: {_normalize_type(dict_type)}"
        )

    from datamodel_code_generator.config import ParserConfig
    from datamodel_code_generator.enums import DefaultValueType
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.model.pydantic_v2 import UnionMode
    from datamodel_code_generator.types import DataTypeManager, StrictTypes

    ParserConfig.model_rebuild(
        _types_namespace={
            "DefaultValueType": DefaultValueType,
            "StrictTypes": StrictTypes,
            "UnionMode": UnionMode,
            "DataModel": DataModel,
            "DataModelFieldBase": DataModelFieldBase,
            "DataTypeManager": DataTypeManager,
        }
    )

    for name, param in baseline_params.items():
        config_default = ParserConfig.model_fields[name].default
        if callable(param.default) and config_default is None:
            continue
        assert config_default == param.default, (
            f"Default mismatch for '{name}':\n  Baseline: {param.default!r}\n  ParserConfig: {config_default!r}"
        )


def test_pydantic_v2_compatibility_reexports_remain_importable() -> None:
    """Keep compatibility import paths for pydantic v2 model internals."""
    from datamodel_code_generator.model.pydantic_base import DataModelField as PydanticBaseDataModelField
    from datamodel_code_generator.model.pydantic_v2.base_model import (
        Constraints as PydanticV2BaseConstraints,
    )
    from datamodel_code_generator.model.pydantic_v2.base_model import (
        DataModelField as PydanticV2DataModelField,
    )
    from datamodel_code_generator.model.pydantic_v2.base_model import (
        DataModelFieldV1,
    )
    from datamodel_code_generator.model.pydantic_v2.dataclass import Constraints as PydanticV2DataclassConstraints

    assert DataModelFieldV1 is PydanticBaseDataModelField
    assert issubclass(PydanticV2DataModelField, DataModelFieldV1)
    assert PydanticV2DataclassConstraints is PydanticV2BaseConstraints


def test_config_models_force_rebuild_after_defer_build() -> None:
    """Ensure deferred config model schemas can still be rebuilt at import-test time."""
    from pydantic import BaseModel as PydanticBaseModel

    from datamodel_code_generator.enums import DefaultValueType
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.model.pydantic_v2 import UnionMode
    from datamodel_code_generator.types import DataTypeManager, StrictTypes

    config_module = importlib.import_module("datamodel_code_generator.config")
    expected_model_names = {
        "AsyncAPIParserConfig",
        "AvroParserConfig",
        "BaseGenerateConfig",
        "GenerateConfig",
        "GraphQLParserConfig",
        "JSONSchemaParserConfig",
        "OpenAPIParserConfig",
        "ParseConfig",
        "ParserConfig",
        "ProtobufParserConfig",
        "XMLSchemaParserConfig",
    }
    config_model_types = {
        name: model_type
        for name, model_type in inspect.getmembers(config_module, inspect.isclass)
        if issubclass(model_type, PydanticBaseModel)
        and model_type.__module__ in {"datamodel_code_generator.base_config", config_module.__name__}
    }
    rebuild_namespace = {
        "DataModel": DataModel,
        "DataModelFieldBase": DataModelFieldBase,
        "DataTypeManager": DataTypeManager,
        "DefaultValueType": DefaultValueType,
        "StrictTypes": StrictTypes,
        "UnionMode": UnionMode,
    }

    assert set(config_model_types) == expected_model_names
    for model_type in config_model_types.values():
        assert model_type.model_rebuild(force=True, _types_namespace=rebuild_namespace)


def test_generate_config_dict_fields_match_generate_config() -> None:
    """Ensure GenerateConfigDict has same field names as GenerateConfig."""
    from datamodel_code_generator._types import GenerateConfigDict
    from datamodel_code_generator.config import GenerateConfig

    config_fields = set(GenerateConfig.model_fields.keys())
    dict_fields = set(GenerateConfigDict.__annotations__.keys())
    assert config_fields == dict_fields, f"Mismatch: {config_fields ^ dict_fields}"


def test_generate_config_dict_types_match_generate_config() -> None:
    """Ensure GenerateConfigDict field types match GenerateConfig."""
    from datamodel_code_generator._types import GenerateConfigDict
    from datamodel_code_generator.config import GenerateConfig

    for field_name, field_info in GenerateConfig.model_fields.items():
        config_type = field_info.annotation
        dict_type = GenerateConfigDict.__annotations__[field_name]
        if field_name == "preset":
            assert _normalize_type(config_type) == "None | str"
            assert _normalize_type(dict_type) == "None | PresetName"
            continue
        assert _types_match(config_type, dict_type), (
            f"Type mismatch for {field_name}: Config={_normalize_type(config_type)}, Dict={_normalize_type(dict_type)}"
        )


def test_parser_config_dict_fields_match_parser_config() -> None:
    """Ensure ParserConfigDict has same field names as ParserConfig."""
    from datamodel_code_generator._types import ParserConfigDict
    from datamodel_code_generator.config import ParserConfig

    config_fields = set(ParserConfig.model_fields.keys())
    dict_fields = set(ParserConfigDict.__annotations__.keys())
    assert config_fields == dict_fields, f"Mismatch: {config_fields ^ dict_fields}"


def test_parser_config_dump_fields_match_public_typed_dict() -> None:
    """Keep internal parser context state out of the serialized public configuration."""
    from datamodel_code_generator._types import ParserConfigDict
    from datamodel_code_generator.config import ParserConfig

    assert set(ParserConfig().model_dump()) == set(ParserConfigDict.__annotations__)


def test_parse_config_dict_fields_match_parse_config() -> None:
    """Ensure ParseConfigDict has same field names as ParseConfig."""
    from datamodel_code_generator._types import ParseConfigDict
    from datamodel_code_generator.config import ParseConfig

    config_fields = set(ParseConfig.model_fields.keys())
    dict_fields = set(ParseConfigDict.__annotations__.keys())
    assert config_fields == dict_fields, f"Mismatch: {config_fields ^ dict_fields}"


def test_parser_config_dict_types_match_parser_config() -> None:
    """Ensure ParserConfigDict field types match ParserConfig."""
    from datamodel_code_generator._types import ParserConfigDict
    from datamodel_code_generator.config import ParserConfig

    for field_name, field_info in ParserConfig.model_fields.items():
        config_type = field_info.annotation
        dict_type = ParserConfigDict.__annotations__[field_name]
        assert _types_match(config_type, dict_type), (
            f"Type mismatch for {field_name}: Config={_normalize_type(config_type)}, Dict={_normalize_type(dict_type)}"
        )


def test_parse_config_dict_types_match_parse_config() -> None:
    """Ensure ParseConfigDict field types match ParseConfig."""
    from datamodel_code_generator._types import ParseConfigDict
    from datamodel_code_generator.config import ParseConfig

    for field_name, field_info in ParseConfig.model_fields.items():
        config_type = field_info.annotation
        dict_type = ParseConfigDict.__annotations__[field_name]
        assert _types_match(config_type, dict_type), (
            f"Type mismatch for {field_name}: Config={_normalize_type(config_type)}, Dict={_normalize_type(dict_type)}"
        )


def test_generate_config_defaults_match_generate_signature() -> None:
    """Ensure GenerateConfig default values match generate() signature defaults."""
    from datamodel_code_generator.config import GenerateConfig

    expected_sig = inspect.signature(_baseline_generate)
    expected_params = _kwonly_by_name(expected_sig)

    for field_name, field_info in GenerateConfig.model_fields.items():
        param = expected_params[field_name]
        config_default = field_info.default

        assert config_default == param.default, (
            f"Default mismatch for {field_name}: Config={config_default}, generate()={param.default}"
        )


def test_parser_config_defaults_match_parser_signature() -> None:
    """Ensure ParserConfig default values match Parser.__init__ signature defaults."""
    from datamodel_code_generator.config import ParserConfig

    expected_sig = inspect.signature(_BaselineParser.__init__)
    expected_params = _kwonly_by_name(expected_sig)

    for field_name, field_info in ParserConfig.model_fields.items():
        param = expected_params[field_name]
        config_default = field_info.default

        if callable(param.default) and config_default is None:
            continue

        assert config_default == param.default, (
            f"Default mismatch for {field_name}: Config={config_default}, Parser.__init__()={param.default}"
        )


def test_parse_config_defaults_match_parse_signature() -> None:
    """Ensure ParseConfig default values match Parser.parse() signature defaults."""
    from datamodel_code_generator.config import ParseConfig

    expected_sig = inspect.signature(_BaselineParser.parse)
    expected_params = _params_by_name(expected_sig)

    for field_name, field_info in ParseConfig.model_fields.items():
        param = expected_params[field_name]
        config_default = field_info.default

        assert config_default == param.default, (
            f"Default mismatch for {field_name}: Config={config_default}, Parser.parse()={param.default}"
        )


def test_generate_with_config_produces_same_result_as_kwargs(tmp_path: Path) -> None:
    """Ensure generate() with GenerateConfig produces same result as kwargs."""
    from datamodel_code_generator.config import GenerateConfig
    from datamodel_code_generator.enums import DataModelType, DefaultValueType
    from datamodel_code_generator.model.pydantic_v2 import UnionMode
    from datamodel_code_generator.types import StrictTypes

    GenerateConfig.model_rebuild(
        _types_namespace={"DefaultValueType": DefaultValueType, "StrictTypes": StrictTypes, "UnionMode": UnionMode}
    )

    schema = '{"type": "object", "properties": {"name": {"type": "string"}}}'
    output_kwargs = tmp_path / "output_kwargs.py"
    output_config = tmp_path / "output_config.py"

    # Generate with kwargs
    generate(
        input_=schema,
        output=output_kwargs,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )

    # Generate with config
    config = GenerateConfig(
        output=output_config,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    generate(
        input_=schema,
        config=config,
    )

    # Compare results
    kwargs_content = output_kwargs.read_text(encoding="utf-8")
    config_content = output_config.read_text(encoding="utf-8")
    assert kwargs_content == config_content, "Output differs between kwargs and config"
