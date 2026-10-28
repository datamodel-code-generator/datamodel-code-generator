"""Schema version features and detection utilities.

Provides SchemaFeatures classes for version-dependent feature flags,
format registries for schema-specific data formats,
and utility functions for detecting schema versions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, TypeVar

from typing_extensions import TypedDict

from datamodel_code_generator.enums import AsyncAPIVersion, JsonSchemaVersion, OpenAPIVersion

if TYPE_CHECKING:
    from datamodel_code_generator.types import Types


class FeatureMetadata(TypedDict):
    """Metadata for schema feature documentation.

    This metadata is used by scripts/build_schema_docs.py to generate
    the feature compatibility matrix in docs/supported_formats.md.
    """

    introduced: str
    """Version when feature was introduced (e.g., "Draft 6", "2020-12", "OAS 3.0")."""
    doc_name: str
    """Display name for documentation (e.g., "prefixItems", "Null in type array")."""
    description: str
    """User-facing description of the feature."""
    status: Literal["supported", "partial", "not_supported"]
    """Implementation status: supported, partial, or not_supported."""


@dataclass(frozen=True)
class JsonSchemaFeatures:
    """Feature flags for JSON Schema versions.

    This is the base class for schema features. OpenAPISchemaFeatures
    extends this to add OpenAPI-specific features.

    Each field includes metadata for documentation generation.
    Use `dataclasses.fields(JsonSchemaFeatures)` to access metadata.
    """

    null_in_type_array: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="Draft 4 or earlier",
            doc_name="Null in type array",
            description="Allows `type: ['string', 'null']` syntax for nullable types",
            status="supported",
        ),
    )
    defs_not_definitions: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="2019-09",
            doc_name="$defs",
            description="Uses `$defs` instead of `definitions` for schema definitions",
            status="supported",
        ),
    )
    prefix_items: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="2020-12",
            doc_name="prefixItems",
            description="Tuple validation using `prefixItems` keyword",
            status="supported",
        ),
    )
    boolean_schemas: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="Draft 6",
            doc_name="Boolean schemas",
            description="Allows `true` and `false` as valid schemas",
            status="supported",
        ),
    )
    id_field: str = field(
        default="$id",
        metadata=FeatureMetadata(
            introduced="Draft 6",
            doc_name="$id",
            description="Schema identifier field (`id` in Draft 4, `$id` in Draft 6+)",
            status="supported",
        ),
    )
    definitions_key: str = field(
        default="$defs",
        metadata=FeatureMetadata(
            introduced="Draft 4",
            doc_name="definitions/$defs",
            description="Key for reusable schema definitions",
            status="supported",
        ),
    )
    exclusive_as_number: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="Draft 6",
            doc_name="exclusiveMinimum/Maximum as number",
            description="Numeric `exclusiveMinimum`/`exclusiveMaximum` (boolean in Draft 4)",
            status="supported",
        ),
    )
    read_only_write_only: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="Draft 7",
            doc_name="readOnly/writeOnly",
            description="Field visibility hints for read-only and write-only properties",
            status="supported",
        ),
    )
    # --- Partially supported features ---
    const_support: bool = field(
        default=True,
        metadata=FeatureMetadata(
            introduced="Draft 6",
            doc_name="const",
            description="Single constant value constraint",
            status="supported",
        ),
    )
    property_names: bool = field(
        default=True,
        metadata=FeatureMetadata(
            introduced="Draft 6",
            doc_name="propertyNames",
            description="Dict key type constraints via pattern, enum, or $ref",
            status="supported",
        ),
    )
    contains: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="Draft 6",
            doc_name="contains",
            description=(
                "Count constraints are modeled when contains matches every item; "
                "general schema-valued contains is not supported"
            ),
            status="partial",
        ),
    )
    deprecated_keyword: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="2019-09",
            doc_name="deprecated",
            description="Marks schema elements as deprecated",
            status="partial",
        ),
    )
    # --- Unsupported features ---
    if_then_else: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="Draft 7",
            doc_name="if/then/else",
            description="Conditional schema validation",
            status="not_supported",
        ),
    )
    content_encoding: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="Draft 7",
            doc_name="contentMediaType/contentEncoding",
            description="Content type and encoding hints for strings",
            status="partial",
        ),
    )
    content_schema: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="2019-09",
            doc_name="contentSchema",
            description="Schema for decoded string content",
            status="partial",
        ),
    )
    anchor: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="2019-09",
            doc_name="$anchor",
            description="Location-independent schema references",
            status="supported",
        ),
    )
    vocabulary: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="2019-09",
            doc_name="$vocabulary",
            description="Vocabulary declarations for meta-schemas",
            status="not_supported",
        ),
    )
    unevaluated_properties: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="2019-09",
            doc_name="unevaluatedProperties",
            description="Additional properties not evaluated by subschemas",
            status="partial",
        ),
    )
    unevaluated_items: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="2019-09",
            doc_name="unevaluatedItems",
            description="Additional items not evaluated by subschemas",
            status="partial",
        ),
    )
    dependent_required: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="2019-09",
            doc_name="dependentRequired",
            description="Conditional property requirements",
            status="not_supported",
        ),
    )
    dependent_schemas: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="2019-09",
            doc_name="dependentSchemas",
            description="Conditional schema application based on property presence",
            status="not_supported",
        ),
    )
    recursive_ref: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="2019-09",
            doc_name="$recursiveRef/$recursiveAnchor",
            description="Recursive reference resolution via anchors",
            status="supported",
        ),
    )
    dynamic_ref: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="2020-12",
            doc_name="$dynamicRef/$dynamicAnchor",
            description="Dynamic reference resolution across schemas",
            status="supported",
        ),
    )

    @classmethod
    def from_version(cls, version: JsonSchemaVersion) -> JsonSchemaFeatures:
        """Create JsonSchemaFeatures from a JSON Schema version."""
        match version:
            case JsonSchemaVersion.Draft4:
                return cls(
                    null_in_type_array=True,
                    defs_not_definitions=False,
                    prefix_items=False,
                    boolean_schemas=False,
                    id_field="id",
                    definitions_key="definitions",
                    exclusive_as_number=False,
                    read_only_write_only=False,
                    const_support=False,
                    property_names=False,
                )
            case JsonSchemaVersion.Draft6:
                return cls(
                    null_in_type_array=True,
                    defs_not_definitions=False,
                    prefix_items=False,
                    boolean_schemas=True,
                    id_field="$id",
                    definitions_key="definitions",
                    exclusive_as_number=True,
                    read_only_write_only=False,
                )
            case JsonSchemaVersion.Draft7:
                return cls(
                    null_in_type_array=True,
                    defs_not_definitions=False,
                    prefix_items=False,
                    boolean_schemas=True,
                    id_field="$id",
                    definitions_key="definitions",
                    exclusive_as_number=True,
                    read_only_write_only=True,
                )
            case JsonSchemaVersion.Draft201909:
                return cls(
                    null_in_type_array=True,
                    defs_not_definitions=True,
                    prefix_items=False,
                    boolean_schemas=True,
                    id_field="$id",
                    definitions_key="$defs",
                    exclusive_as_number=True,
                    read_only_write_only=True,
                    anchor=True,
                    recursive_ref=True,
                )
            case _:
                return cls(
                    null_in_type_array=True,
                    defs_not_definitions=True,
                    prefix_items=True,
                    boolean_schemas=True,
                    id_field="$id",
                    definitions_key="$defs",
                    exclusive_as_number=True,
                    read_only_write_only=True,
                    anchor=True,
                    recursive_ref=True,
                    dynamic_ref=True,
                )


@dataclass(frozen=True)
class OpenAPISchemaFeatures(JsonSchemaFeatures):
    """Feature flags for OpenAPI versions.

    Extends JsonSchemaFeatures with OpenAPI-specific features.

    Each field includes metadata for documentation generation.
    Use `dataclasses.fields(OpenAPISchemaFeatures)` to access metadata.
    """

    nullable_keyword: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="OAS 3.0",
            doc_name="nullable",
            description="Uses `nullable: true` for nullable types (deprecated in 3.1)",
            status="supported",
        ),
    )
    discriminator_support: bool = field(
        default=True,
        metadata=FeatureMetadata(
            introduced="OAS 3.0",
            doc_name="discriminator",
            description="Polymorphism support via `discriminator` keyword",
            status="supported",
        ),
    )
    webhooks: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="OAS 3.1",
            doc_name="webhooks",
            description="Top-level webhooks object for incoming events",
            status="supported",
        ),
    )
    # --- Partially supported features ---
    ref_sibling_keywords: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="OAS 3.1",
            doc_name="$ref with sibling keywords",
            description="$ref can coexist with description, summary (no allOf workaround)",
            status="partial",
        ),
    )
    media_item_schema: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="OAS 3.2",
            doc_name="itemSchema",
            description="Media Type Object item schema for sequential media",
            status="supported",
        ),
    )
    openapi_self: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="OAS 3.2",
            doc_name="$self",
            description="Root document URI for relative and absolute reference resolution",
            status="supported",
        ),
    )
    querystring_parameter: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="OAS 3.2",
            doc_name="querystring",
            description="Query string parameter object without a parameter name",
            status="supported",
        ),
    )
    # --- Unsupported features ---
    xml_support: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="OAS 3.0",
            doc_name="xml",
            description="XML serialization metadata (name, namespace, prefix)",
            status="partial",
        ),
    )
    external_docs: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="OAS 3.0",
            doc_name="externalDocs",
            description="Reference to external documentation",
            status="partial",
        ),
    )
    links: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="OAS 3.0",
            doc_name="links",
            description="Links between operations",
            status="not_supported",
        ),
    )
    callbacks: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="OAS 3.0",
            doc_name="callbacks",
            description="Callback definitions for webhooks",
            status="not_supported",
        ),
    )
    security_schemes: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="OAS 3.0",
            doc_name="securitySchemes",
            description="API security mechanism definitions",
            status="not_supported",
        ),
    )

    @classmethod
    def from_openapi_version(cls, version: OpenAPIVersion) -> OpenAPISchemaFeatures:
        """Create OpenAPISchemaFeatures from an OpenAPI version."""
        match version:
            case OpenAPIVersion.V30:
                return cls(
                    null_in_type_array=False,
                    defs_not_definitions=False,
                    prefix_items=False,
                    boolean_schemas=False,
                    id_field="$id",
                    definitions_key="definitions",
                    exclusive_as_number=False,
                    read_only_write_only=True,
                    nullable_keyword=True,
                    discriminator_support=True,
                )
            case OpenAPIVersion.V32:
                return cls(
                    null_in_type_array=True,
                    defs_not_definitions=True,
                    prefix_items=True,
                    boolean_schemas=True,
                    id_field="$id",
                    definitions_key="$defs",
                    exclusive_as_number=True,
                    read_only_write_only=True,
                    anchor=True,
                    recursive_ref=True,
                    dynamic_ref=True,
                    nullable_keyword=False,
                    discriminator_support=True,
                    webhooks=True,
                    ref_sibling_keywords=True,
                    media_item_schema=True,
                    openapi_self=True,
                    querystring_parameter=True,
                )
            case _:
                return cls(
                    null_in_type_array=True,
                    defs_not_definitions=True,
                    prefix_items=True,
                    boolean_schemas=True,
                    id_field="$id",
                    definitions_key="$defs",
                    exclusive_as_number=True,
                    read_only_write_only=True,
                    anchor=True,
                    recursive_ref=True,
                    dynamic_ref=True,
                    nullable_keyword=False,
                    discriminator_support=True,
                    webhooks=True,
                    ref_sibling_keywords=True,
                )

    @classmethod
    def from_asyncapi_version(cls, version: AsyncAPIVersion) -> OpenAPISchemaFeatures:
        """Create schema features for AsyncAPI Schema Objects."""
        if version == AsyncAPIVersion.V2:
            return cls.from_openapi_version(OpenAPIVersion.V30)
        return cls.from_openapi_version(OpenAPIVersion.V31)


SchemaFeaturesT = TypeVar("SchemaFeaturesT", bound=JsonSchemaFeatures)

_JsonSchemaVersionPatterns: TypeAlias = dict[str, JsonSchemaVersion]

_JSONSCHEMA_VERSION_PATTERNS: _JsonSchemaVersionPatterns = {
    "draft-04": JsonSchemaVersion.Draft4,
    "draft-06": JsonSchemaVersion.Draft6,
    "draft-07": JsonSchemaVersion.Draft7,
    "2019-09": JsonSchemaVersion.Draft201909,
    "2020-12": JsonSchemaVersion.Draft202012,
}


def _detect_declared_jsonschema_version(data: dict[str, Any]) -> JsonSchemaVersion | None:
    """Detect a recognized declaration without applying undeclared-schema heuristics."""
    if isinstance(schema_url := data.get("$schema", ""), str):
        for pattern, version in _JSONSCHEMA_VERSION_PATTERNS.items():
            if pattern in schema_url:
                return version
    return None


def detect_jsonschema_version(data: dict[str, Any]) -> JsonSchemaVersion:
    """Detect JSON Schema version from $schema field or heuristics.

    Detection priority:
    1. $schema field explicit declaration
    2. Heuristics ($defs vs definitions, etc.)
    3. Fallback: Draft 7 (backward-compatible default)

    Note: In Lenient mode, detection result is only used for optimization hints.
          In Strict mode, detection result is used to warn on version violations.

    Args:
        data: The schema dictionary to analyze.

    Returns:
        The detected JSON Schema version.
    """
    if (version := _detect_declared_jsonschema_version(data)) is not None:
        return version

    # Heuristic detection based on keywords
    # $defs was introduced in Draft 2019-09, but Draft 2020-12 also uses it.
    # Since 2020-12 is a superset of 2019-09, default to 2020-12 when $defs is present
    # to avoid false warnings in Strict mode for features valid in both versions.
    if "$defs" in data:
        return JsonSchemaVersion.Draft202012
    return JsonSchemaVersion.Draft7


def detect_openapi_version(data: dict[str, Any]) -> OpenAPIVersion:
    """Detect OpenAPI version from openapi field.

    Args:
        data: The schema dictionary to analyze.

    Returns:
        The detected OpenAPI version.
    """
    if not isinstance(version := data.get("openapi", ""), str):
        return OpenAPIVersion.V31

    match version.split(".", maxsplit=2)[:2]:
        case ["3", "0"]:
            return OpenAPIVersion.V30
        case ["3", "1"]:
            return OpenAPIVersion.V31
        case ["3", "2"]:
            return OpenAPIVersion.V32
        case _:
            return OpenAPIVersion.V31


def detect_asyncapi_version(data: dict[str, Any]) -> AsyncAPIVersion:
    """Detect AsyncAPI major version from asyncapi field."""
    if isinstance(version := data.get("asyncapi", ""), str):
        if version.startswith("2."):
            return AsyncAPIVersion.V2
        if version.startswith("3."):
            return AsyncAPIVersion.V3
    return AsyncAPIVersion.V3


DataFormatMapping: TypeAlias = "dict[str, dict[str, Types]]"


def _get_common_data_formats() -> DataFormatMapping:
    """Get common data formats valid for both JsonSchema and OpenAPI."""
    from datamodel_code_generator.types import Types  # noqa: PLC0415

    return {
        "integer": {
            "int32": Types.int32,
            "int64": Types.int64,
            "default": Types.integer,
            "date-time": Types.date_time,
            "unix-time": Types.int64,
            "unixtime": Types.int64,
        },
        "number": {
            "float": Types.float,
            "double": Types.double,
            "decimal": Types.decimal,
            "date-time": Types.date_time,
            "time": Types.time,
            "time-delta": Types.timedelta,
            "default": Types.number,
            "unixtime": Types.int64,
        },
        "string": {
            "default": Types.string,
            "byte": Types.byte,
            "date": Types.date,
            "date-time": Types.date_time,
            "timestamp with time zone": Types.date_time,
            "date-time-local": Types.date_time_local,
            "duration": Types.timedelta,
            "time": Types.time,
            "time-local": Types.time_local,
            "path": Types.path,
            "email": Types.email,
            "idn-email": Types.email,
            "idn-hostname": Types.string,
            "iri": Types.string,
            "iri-reference": Types.string,
            "uuid": Types.uuid,
            "uuid1": Types.uuid1,
            "uuid2": Types.uuid2,
            "uuid3": Types.uuid3,
            "uuid4": Types.uuid4,
            "uuid5": Types.uuid5,
            "uri": Types.uri,
            "uri-reference": Types.string,
            "uri-template": Types.string,
            "json-pointer": Types.string,
            "relative-json-pointer": Types.string,
            "regex": Types.string,
            "hostname": Types.hostname,
            "ipv4": Types.ipv4,
            "ipv4-network": Types.ipv4_network,
            "ipv6": Types.ipv6,
            "ipv6-network": Types.ipv6_network,
            "decimal": Types.decimal,
            "integer": Types.integer,
            "unixtime": Types.int64,
            "ulid": Types.ulid,
        },
        "boolean": {"default": Types.boolean},
        "object": {"default": Types.object},
        "null": {"default": Types.null},
        "array": {"default": Types.array},
    }


def _get_openapi_only_formats() -> DataFormatMapping:
    """Get formats specific to OpenAPI."""
    from datamodel_code_generator.types import Types  # noqa: PLC0415

    return {
        "string": {
            "binary": Types.binary,
            "password": Types.password,
        },
    }


def _get_openapi_data_formats() -> DataFormatMapping:
    """Get common data formats extended with OpenAPI-specific formats."""
    formats = _get_common_data_formats()
    for type_key, type_formats in _get_openapi_only_formats().items():
        if current_type_formats := formats.get(type_key):
            formats[type_key] = {**current_type_formats, **type_formats}
            continue
        formats[type_key] = type_formats  # pragma: no cover
    return formats


def get_data_formats(*, is_openapi: bool = False) -> DataFormatMapping:
    """Get merged data formats based on schema type.

    Args:
        is_openapi: If True, includes OpenAPI-specific formats.

    Returns:
        Merged dictionary of data formats.
    """
    if is_openapi:
        return _get_openapi_data_formats()
    return _get_common_data_formats()


__all__ = [
    "DataFormatMapping",
    "FeatureMetadata",
    "JsonSchemaFeatures",
    "OpenAPISchemaFeatures",
    "SchemaFeaturesT",
    "detect_asyncapi_version",
    "detect_jsonschema_version",
    "detect_openapi_version",
    "get_data_formats",
]
