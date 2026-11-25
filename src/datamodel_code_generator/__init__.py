"""Main module for datamodel-code-generator.

Provides the main `generate()` function and related enums/exceptions for generating
Python data models (Pydantic, dataclasses, TypedDict, msgspec) from various schema formats.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator._caches import cached_path_exists as cached_path_exists
from datamodel_code_generator._debug import DEFAULT_MAX_VARIABLE_LENGTH as DEFAULT_MAX_VARIABLE_LENGTH
from datamodel_code_generator._debug import T as T
from datamodel_code_generator._debug import enable_debug_message as enable_debug_message
from datamodel_code_generator._debug import snooper_to_methods as snooper_to_methods
from datamodel_code_generator._facade import generate as generate
from datamodel_code_generator._generation import (
    _COMMENT_ONLY_HEADER_FAST_PATH_LIMIT as _COMMENT_ONLY_HEADER_FAST_PATH_LIMIT,
)
from datamodel_code_generator._generation import _GC_YOUNG_THRESHOLD as _GC_YOUNG_THRESHOLD
from datamodel_code_generator._generation import _INPUT_TYPES as _INPUT_TYPES
from datamodel_code_generator._generation import JSON_SCHEMA_URLS as JSON_SCHEMA_URLS
from datamodel_code_generator._generation import RAW_DATA_TYPES as RAW_DATA_TYPES
from datamodel_code_generator._generation import ParserConfigT as ParserConfigT
from datamodel_code_generator._generation import _build_parser as _build_parser
from datamodel_code_generator._generation import _create_parser_config as _create_parser_config
from datamodel_code_generator._generation import _create_typed_parser_config as _create_typed_parser_config
from datamodel_code_generator._generation import (
    _find_future_import_insertion_point as _find_future_import_insertion_point,
)
from datamodel_code_generator._generation import _generate_config_values as _generate_config_values
from datamodel_code_generator._generation import _looks_like_csv_text as _looks_like_csv_text
from datamodel_code_generator._generation import _prepare_generate_facade_config as _prepare_generate_facade_config
from datamodel_code_generator._generation import _run_generation as _run_generation
from datamodel_code_generator._generation import _tuned_gc as _tuned_gc
from datamodel_code_generator._generation import chdir as chdir
from datamodel_code_generator._generation import detect_xmlschema_version as detect_xmlschema_version
from datamodel_code_generator._generation import get_first_file as get_first_file
from datamodel_code_generator._generation import get_version as get_version
from datamodel_code_generator._generation import infer_input_type as infer_input_type
from datamodel_code_generator._generation import inferred_message as inferred_message
from datamodel_code_generator._generation import inferred_swagger_2_message as inferred_swagger_2_message
from datamodel_code_generator._generation import is_schema as is_schema
from datamodel_code_generator._generation_timestamp import current_timestamp as current_timestamp
from datamodel_code_generator._lazy_exports import module_hooks as _module_hooks
from datamodel_code_generator._process_state import PROCESS_STATE_LOCK as PROCESS_STATE_LOCK
from datamodel_code_generator._shared_types import DanglingRefWarning as DanglingRefWarning
from datamodel_code_generator._shared_types import (
    DefaultPutDict,
    LiteralType,
    _CollapseRootModelsRecursionError,
)
from datamodel_code_generator._shared_types import DefaultValueTypeWarning as DefaultValueTypeWarning
from datamodel_code_generator._shared_types import DocumentationAnnotationWarning as DocumentationAnnotationWarning
from datamodel_code_generator._shared_types import Error as Error
from datamodel_code_generator._shared_types import GeneratedModules as GeneratedModules
from datamodel_code_generator._shared_types import InvalidClassNameError as InvalidClassNameError
from datamodel_code_generator._shared_types import InvalidFileFormatError as InvalidFileFormatError
from datamodel_code_generator._shared_types import LeftoverFilesWarning as LeftoverFilesWarning
from datamodel_code_generator._shared_types import NamingHintWarning as NamingHintWarning
from datamodel_code_generator._shared_types import SchemaFetchError as SchemaFetchError
from datamodel_code_generator._shared_types import SchemaParseError as SchemaParseError
from datamodel_code_generator._shared_types import SchemaResourceRefWarning as SchemaResourceRefWarning
from datamodel_code_generator._shared_types import YamlScalar as YamlScalar
from datamodel_code_generator._shared_types import YamlValue as YamlValue
from datamodel_code_generator._shared_types import _InvalidConfigTomlError as _InvalidConfigTomlError
from datamodel_code_generator._shared_types import (
    _StructuredOutputUnavailableError as _StructuredOutputUnavailableError,
)
from datamodel_code_generator._source import (
    _clear_parser_source_data_cache as _clear_parser_source_data_cache,
)
from datamodel_code_generator._source import _is_json_text as _is_json_text
from datamodel_code_generator._source import (
    _is_parsed_source_cache_enabled as _is_parsed_source_cache_enabled,
)
from datamodel_code_generator._source import _is_xml_text as _is_xml_text
from datamodel_code_generator._source import (
    _load_parser_source_data_from_path_bytes as _load_parser_source_data_from_path_bytes,
)
from datamodel_code_generator._source import (
    _parser_source_data_cache as _parser_source_data_cache,
)
from datamodel_code_generator._source import (
    _read_parser_source_data_from_path as _read_parser_source_data_from_path,
)
from datamodel_code_generator._source import enable_parsed_source_cache
from datamodel_code_generator._source import is_asyncapi as is_asyncapi
from datamodel_code_generator._source import is_openapi as is_openapi
from datamodel_code_generator._source import is_swagger_2 as is_swagger_2
from datamodel_code_generator._source import load_data as load_data
from datamodel_code_generator._source import load_data_from_path as load_data_from_path
from datamodel_code_generator._source import load_text_data as load_text_data
from datamodel_code_generator._source import load_yaml as load_yaml
from datamodel_code_generator._source import load_yaml_dict as load_yaml_dict
from datamodel_code_generator._source import load_yaml_dict_from_path as load_yaml_dict_from_path
from datamodel_code_generator._url_redaction import redact_path as redact_path
from datamodel_code_generator._url_redaction import redact_url as redact_url
from datamodel_code_generator._warning_control import held as held
from datamodel_code_generator._warning_control import quiet as quiet
from datamodel_code_generator.enums import (
    DEFAULT_SHARED_MODULE_NAME,
    MAX_VERSION,
    MIN_VERSION,
    AliasGenerator,
    AllExportsCollisionStrategy,
    AllExportsScope,
    AllOfClassHierarchy,
    AllOfMergeMode,
    AsyncAPIVersion,
    ClassNameAffixScope,
    ClientAsyncAPIPerspective,
    ClientBodyArguments,
    ClientSignatureStyle,
    ClientTools,
    ClientType,
    CollapseRootModelsNameStrategy,
    ConstructorFieldOrder,
    CustomFileHeaderMode,
    DefaultValueType,
    FieldTypeCollisionStrategy,
    GraphQLScope,
    HTTPBackend,
    InputFileType,
    InputModelRefStrategy,
    JsonSchemaVersion,
    ModuleSplitMode,
    NamingStrategy,
    OpenAPIAsDefinitions,
    OpenAPIScope,
    OpenAPIVersion,
    OpenEnums,
    ProtobufVersion,
    ReadOnlyWriteOnlyModelType,
    ReuseScope,
    SchemaValidatorType,
    ServerBodyMode,
    ServerHandlerMode,
    ServerLayout,
    ServerType,
    TargetPydanticVersion,
    VersionMode,
    XMLSchemaVersion,
)
from datamodel_code_generator.enums import DataclassArguments as DataclassArguments
from datamodel_code_generator.enums import DataModelType as DataModelType

if TYPE_CHECKING:
    from datamodel_code_generator._format_types import (
        DEFAULT_FORMATTERS,
        DateClassType,
        DatetimeClassType,
        PythonVersion,
        PythonVersionMin,
    )
    from datamodel_code_generator._project_config import load_pyproject_config
    from datamodel_code_generator.config import GenerateConfig

# Preserve the historical private facade identity for repr and pickling compatibility.
_CollapseRootModelsRecursionError.__module__ = __name__

for _public_source_export in (
    enable_parsed_source_cache,
    is_asyncapi,
    is_openapi,
    is_swagger_2,
    load_data,
    load_data_from_path,
    load_yaml,
    load_yaml_dict,
    load_yaml_dict_from_path,
):
    _public_source_export.__module__ = __name__
del _public_source_export

DEFAULT_BASE_CLASS: str = "pydantic.BaseModel"


_LAZY_EXPORTS = {
    "load_pyproject_config": "datamodel_code_generator._project_config",
    "clear_dynamic_models_cache": "datamodel_code_generator.dynamic",
    "detect_jsonschema_version": "datamodel_code_generator.parser.schema_version",
    "detect_openapi_version": "datamodel_code_generator.parser.schema_version",
    "generate_dynamic_models": "datamodel_code_generator.dynamic",
    "generate_dynamic_module": "datamodel_code_generator.dynamic",
    "DynamicModule": "datamodel_code_generator.dynamic",
    "GenerateConfig": "datamodel_code_generator.config",
    "UnionMode": "datamodel_code_generator.enums",
    "CodeFormatter": "datamodel_code_generator.format",
    "DateClassType": "datamodel_code_generator._format_types",
    "DatetimeClassType": "datamodel_code_generator._format_types",
    "DEFAULT_FORMATTERS": "datamodel_code_generator._format_types",
    "Formatter": "datamodel_code_generator._format_types",
    "PythonVersion": "datamodel_code_generator._format_types",
    "PythonVersionMin": "datamodel_code_generator._format_types",
    "resolve_use_type_checking_imports": "datamodel_code_generator.format",
}


__getattr__, __dir__ = _module_hooks(__name__, _LAZY_EXPORTS, cache=True)


__all__ = [
    "DEFAULT_FORMATTERS",
    "DEFAULT_SHARED_MODULE_NAME",
    "MAX_VERSION",
    "MIN_VERSION",
    "AliasGenerator",
    "AllExportsCollisionStrategy",
    "AllExportsScope",
    "AllOfClassHierarchy",
    "AllOfMergeMode",
    "AsyncAPIVersion",
    "ClassNameAffixScope",
    "ClientAsyncAPIPerspective",
    "ClientBodyArguments",
    "ClientSignatureStyle",
    "ClientTools",
    "ClientType",
    "CollapseRootModelsNameStrategy",
    "ConstructorFieldOrder",
    "CustomFileHeaderMode",
    "DanglingRefWarning",
    "DateClassType",
    "DatetimeClassType",
    "DefaultPutDict",
    "DefaultValueType",
    "DefaultValueTypeWarning",
    "DocumentationAnnotationWarning",
    "DynamicModule",  # noqa: F822
    "Error",
    "FieldTypeCollisionStrategy",
    "GeneratedModules",
    "GraphQLScope",
    "HTTPBackend",
    "InputFileType",
    "InputModelRefStrategy",
    "InvalidClassNameError",
    "InvalidFileFormatError",
    "JsonSchemaVersion",
    "LeftoverFilesWarning",
    "LiteralType",
    "ModuleSplitMode",
    "NamingHintWarning",
    "NamingStrategy",
    "OpenAPIAsDefinitions",
    "OpenAPIScope",
    "OpenAPIVersion",
    "OpenEnums",
    "ProtobufVersion",
    "PythonVersion",
    "PythonVersionMin",
    "ReadOnlyWriteOnlyModelType",
    "ReuseScope",
    "SchemaParseError",
    "SchemaResourceRefWarning",
    "SchemaValidatorType",
    "ServerBodyMode",
    "ServerHandlerMode",
    "ServerLayout",
    "ServerType",
    "TargetPydanticVersion",
    "VersionMode",
    "XMLSchemaVersion",
    "clear_dynamic_models_cache",  # noqa: F822
    "detect_jsonschema_version",  # noqa: F822
    "detect_openapi_version",  # noqa: F822
    "detect_xmlschema_version",
    "enable_parsed_source_cache",
    "generate",
    "generate_dynamic_models",  # noqa: F822
    "generate_dynamic_module",  # noqa: F822
]

__all__ += ["GenerateConfig"]
__all__ += ["load_pyproject_config"]
