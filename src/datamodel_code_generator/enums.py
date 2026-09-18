"""Enum definitions for datamodel-code-generator.

This module contains all enum types used by the CLI and code generation,
separated from the main module to allow fast CLI startup without loading pydantic.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

from typing_extensions import TypedDict


class DataclassArguments(TypedDict, total=False):
    """Arguments for @dataclass decorator."""

    init: bool
    repr: bool
    eq: bool
    order: bool
    unsafe_hash: bool
    frozen: bool
    match_args: bool
    kw_only: bool
    slots: bool
    weakref_slot: bool


MIN_VERSION: Final[int] = 10
MAX_VERSION: Final[int] = 13
DEFAULT_SHARED_MODULE_NAME: Final[str] = "shared"


class InputFileType(Enum):
    """Supported input file types for schema parsing."""

    Auto = "auto"
    OpenAPI = "openapi"
    AsyncAPI = "asyncapi"
    JsonSchema = "jsonschema"
    MCPTools = "mcp-tools"
    XMLSchema = "xmlschema"
    Protobuf = "protobuf"
    Avro = "avro"
    Json = "json"
    Yaml = "yaml"
    Dict = "dict"
    CSV = "csv"
    GraphQL = "graphql"


class DataModelType(Enum):
    """Supported output data model types."""

    PydanticV2BaseModel = "pydantic_v2.BaseModel"
    PydanticV2Dataclass = "pydantic_v2.dataclass"
    DataclassesDataclass = "dataclasses.dataclass"
    TypingTypedDict = "typing.TypedDict"
    MsgspecStruct = "msgspec.Struct"


class _OutputModelFamily(str, Enum):
    """Compatibility family used when preserving input-model references."""

    PYDANTIC = "pydantic"
    DATACLASS = "dataclass"
    TYPEDDICT = "typeddict"
    MSGSPEC = "msgspec"


def _get_output_model_family(data_model_type: DataModelType | None) -> _OutputModelFamily:
    """Resolve the reference-compatible family for an output model selection."""
    match data_model_type:
        case None | DataModelType.PydanticV2BaseModel | DataModelType.PydanticV2Dataclass:
            return _OutputModelFamily.PYDANTIC
        case DataModelType.DataclassesDataclass:
            return _OutputModelFamily.DATACLASS
        case DataModelType.TypingTypedDict:
            return _OutputModelFamily.TYPEDDICT
        case DataModelType.MsgspecStruct:
            return _OutputModelFamily.MSGSPEC
    msg = f"{data_model_type} is unsupported data model type"  # pragma: no cover
    raise ValueError(msg)  # pragma: no cover


class ReuseScope(Enum):
    """Scope for model reuse deduplication.

    module: Deduplicate identical models within each module (default).
    tree: Deduplicate identical models across all modules, placing shared models in shared.py.
    """

    Module = "module"
    Tree = "tree"


class OpenAPIScope(Enum):
    """Scopes for OpenAPI model generation."""

    Schemas = "schemas"
    Paths = "paths"
    Tags = "tags"
    Parameters = "parameters"
    Webhooks = "webhooks"
    RequestBodies = "requestbodies"
    Api = "api"


class AllExportsScope(Enum):
    """Scope for __all__ exports in __init__.py.

    children: Export models from direct child modules only.
    recursive: Export models from all descendant modules recursively.
    """

    Children = "children"
    Recursive = "recursive"


class AllExportsCollisionStrategy(Enum):
    """Strategy for handling name collisions in recursive exports.

    error: Raise an error when name collision is detected.
    minimal_prefix: Add module prefix only to colliding names.
    full_prefix: Add full module path prefix to all colliding names.
    """

    Error = "error"
    MinimalPrefix = "minimal-prefix"
    FullPrefix = "full-prefix"


class FieldTypeCollisionStrategy(Enum):
    """Strategy for handling field name and type name collisions.

    rename_field: Rename the field with a suffix and add alias (default).
    rename_type: Rename the type class with a suffix to preserve field name.
    """

    RenameField = "rename-field"
    RenameType = "rename-type"


class ExtraFields(str, Enum):
    """Generated model behavior for unknown input fields."""

    Allow = "allow"
    Ignore = "ignore"
    Forbid = "forbid"


class SchemaValidatorType(Enum):
    """Schema-derived runtime validator backend."""

    PydanticV2 = "pydantic-v2"


class HTTPBackend(Enum):
    """HTTP client backend selection policy."""

    AUTO = "auto"
    HTTPX = "httpx"
    HTTPX2 = "httpx2"


class NamingStrategy(Enum):
    """Strategy for generating unique model names when duplicates occur.

    numbered: Append numeric suffix (Address1, Address2) [default].
    parent_prefixed: Prefix with parent model name (CustomerAddress, UserAddress).
    full_path: Use full schema path for unique names (OrdersItemsAddress).
    primary_first: Prioritize primary schema definitions, others get suffix.
    """

    Numbered = "numbered"
    ParentPrefixed = "parent-prefixed"
    FullPath = "full-path"
    PrimaryFirst = "primary-first"


class ClassNameAffixScope(Enum):
    """Scope for applying class name prefix/suffix.

    All: Apply to all classes including enums (default).
    Models: Apply only to model classes (BaseModel, TypedDict, dataclass, msgspec).
    Enums: Apply only to enum classes.
    """

    All = "all"
    Models = "models"
    Enums = "enums"


class CollapseRootModelsNameStrategy(Enum):
    """Strategy for naming when collapsing root models with object references.

    child: Keep the inner (child) model's name, remove the wrapper.
    parent: Rename inner model to wrapper's name, remove the wrapper.
    """

    Child = "child"
    Parent = "parent"


class AllOfMergeMode(Enum):
    """Mode for field merging in allOf schemas.

    constraints: Merge only constraint fields (minItems, maxItems, pattern, etc.) from parent.
    all: Merge constraints plus annotation fields (default, examples) from parent.
    none: Do not merge any fields from parent properties.
    """

    Constraints = "constraints"
    All = "all"
    NoMerge = "none"


class AllOfClassHierarchy(Enum):
    """How to map allOf references to class hierarchies."""

    IfNoConflict = "if-no-conflict"
    Always = "always"


class GraphQLScope(Enum):
    """Scopes for GraphQL model generation."""

    Schema = "schema"


class ReadOnlyWriteOnlyModelType(Enum):
    """Model generation strategy for readOnly/writeOnly fields.

    RequestResponse: Generate only Request/Response model variants (no base model).
    All: Generate Base, Request, and Response models.
    """

    RequestResponse = "request-response"
    All = "all"


class ModuleSplitMode(Enum):
    """Mode for splitting generated models into separate files.

    Single: Generate one file per model class.
    """

    Single = "single"


class TargetPydanticVersion(Enum):
    """Target Pydantic version for generated code.

    V2: Generate code compatible with Pydantic 2.0+ (uses populate_by_name).
    V2_11: Generate code for Pydantic 2.11+ (uses validate_by_name).
    V2_12: Generate code for Pydantic 2.12+ (supports MISSING sentinel).
    """

    V2 = "2"
    V2_11 = "2.11"
    V2_12 = "2.12"


def _pydantic_version_key(target_version: TargetPydanticVersion | str) -> tuple[int, ...]:
    """Convert a target Pydantic version into comparable integer parts."""
    value = target_version.value if isinstance(target_version, TargetPydanticVersion) else target_version
    return tuple(int(part) for part in value.split("."))


def _is_pydantic_version_at_least(
    target_version: TargetPydanticVersion | str,
    minimum: TargetPydanticVersion | str,
) -> bool:
    """Return whether the target Pydantic version is greater than or equal to the minimum."""
    return _pydantic_version_key(target_version) >= _pydantic_version_key(minimum)


class AliasGenerator(Enum):
    """Supported Pydantic v2 alias generator helpers."""

    ToCamel = "to_camel"
    ToPascal = "to_pascal"
    ToSnake = "to_snake"


class UnionMode(Enum):
    """Union discriminator mode for Pydantic v2."""

    smart = "smart"
    left_to_right = "left_to_right"


class CustomFileHeaderMode(Enum):
    """Strategy for combining custom and generated file headers."""

    Replace = "replace"
    Prepend = "prepend"


class InputModelRefStrategy(Enum):
    """Strategy for handling referenced types in --input-model.

    RegenerateAll: Regenerate all referenced types into target output type.
    ReuseForeign: Reuse types from different model families via import,
                  regenerate same-family types into target output type.
    ReuseAll: Reuse all referenced types via import, no regeneration.
    """

    RegenerateAll = "regenerate-all"
    ReuseForeign = "reuse-foreign"
    ReuseAll = "reuse-all"


class StrictTypes(Enum):
    """Strict type options for generated models."""

    str = "str"
    bytes = "bytes"
    int = "int"
    float = "float"
    bool = "bool"


class DefaultValueType(Enum):
    """Schema default value types that can be deserialized in generated code."""

    Decimal = "decimal"
    Enum = "enum"


class JsonSchemaVersion(Enum):
    """JSON Schema draft versions.

    Auto: Auto-detect from $schema field or heuristics (default).
    """

    Draft4 = "draft-04"
    Draft6 = "draft-06"
    Draft7 = "draft-07"
    Draft201909 = "2019-09"
    Draft202012 = "2020-12"
    Auto = "auto"


class OpenAPIVersion(Enum):
    """OpenAPI specification versions.

    Auto: Auto-detect from openapi field (default).
    """

    V30 = "3.0"
    V31 = "3.1"
    V32 = "3.2"
    Auto = "auto"


class AsyncAPIVersion(Enum):
    """AsyncAPI specification versions.

    Auto: Auto-detect from asyncapi field (default).
    """

    V2 = "2.0"
    V3 = "3.0"
    Auto = "auto"


class XMLSchemaVersion(Enum):
    """XML Schema specification versions.

    Auto: Auto-detect from XML Schema versioning attributes and XSD 1.1 constructs (default).
    """

    V10 = "1.0"
    V11 = "1.1"
    Auto = "auto"


class ProtobufVersion(Enum):
    """Protocol Buffers schema syntax versions.

    Auto: Auto-detect from each file's syntax or edition declaration. Files without a syntax declaration are proto2.
    """

    Proto2 = "proto2"
    Proto3 = "proto3"
    Edition2023 = "2023"
    Auto = "auto"


class VersionMode(Enum):
    """Schema version validation mode.

    Lenient: Accept all features regardless of declared version (default).
    Strict: Warn on features outside declared/detected version.
    """

    Lenient = "lenient"
    Strict = "strict"


__all__ = [
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
]
