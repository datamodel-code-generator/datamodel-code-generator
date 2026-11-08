"""Schema version features and detection utilities.

Provides SchemaFeatures classes for version-dependent feature flags,
format registries for schema-specific data formats,
and utility functions for detecting schema versions.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias, TypeVar

from typing_extensions import NotRequired, TypedDict

from datamodel_code_generator.enums import AsyncAPIVersion, JsonSchemaVersion, OpenAPIVersion

if TYPE_CHECKING:
    from collections.abc import Callable

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
    """Implementation status of model generation: supported, partial, or not_supported."""
    client_status: NotRequired[Literal["supported", "partial", "not_supported"]]
    """Status in the Python client (`--generate-client`), for a feature the client plans itself.

    An operation that uses a feature the client does not support, or a part of a partial one it does not, is left
    out of the client with a warning that names the feature.
    """


_DRAFT_FLAG_CHANGES: tuple[tuple[JsonSchemaVersion, dict[str, Any]], ...] = (
    (
        JsonSchemaVersion.Draft4,
        {
            "null_in_type_array": True,
            "id_field": "id",
            "definitions_key": "definitions",
            "const_support": False,
            "property_names": False,
        },
    ),
    (
        JsonSchemaVersion.Draft6,
        {
            "boolean_schemas": True,
            "id_field": "$id",
            "exclusive_as_number": True,
            "const_support": True,
            "property_names": True,
        },
    ),
    (JsonSchemaVersion.Draft7, {"read_only_write_only": True}),
    (
        JsonSchemaVersion.Draft201909,
        {
            "defs_not_definitions": True,
            "definitions_key": "$defs",
            "anchor": True,
            "recursive_ref": True,
            "ref_sibling_keywords": True,
        },
    ),
    (JsonSchemaVersion.Draft202012, {"prefix_items": True, "dynamic_ref": True}),
)


def _draft_flags(version: JsonSchemaVersion) -> dict[str, Any]:
    """Return the flags that differ from the defaults up to ``version``; every other version reads as 2020-12."""
    flags: dict[str, Any] = {}
    for draft, changes in _DRAFT_FLAG_CHANGES:
        flags |= changes
        if draft == version:
            break
    return flags


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
    ref_sibling_keywords: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="2019-09",
            doc_name="$ref with sibling keywords",
            description="Keywords beside `$ref` apply with the referenced schema (ignored in Draft 4-7 and OAS 3.0)",
            status="partial",
        ),
    )

    @classmethod
    def from_version(cls, version: JsonSchemaVersion) -> JsonSchemaFeatures:
        """Create JsonSchemaFeatures from a JSON Schema version."""
        return cls(**_draft_flags(version))


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
            description="Top-level webhooks object for incoming events; the client generates no methods for webhook "
            "operations, only helpers that verify and decode deliveries",
            status="supported",
            client_status="partial",
        ),
    )
    # --- Partially supported features ---
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
            description="Query string parameter object without a parameter name; the client ignores the `encoding` "
            "declarations of the members of a nameless form querystring",
            status="supported",
            client_status="partial",
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
    # --- Features the Python client plans itself (client_status) ---
    http_auth_schemes: bool = field(
        default=True,
        metadata=FeatureMetadata(
            introduced="OAS 3.0",
            doc_name="http security schemes",
            description="HTTP authentication schemes; the client sends `basic` and `bearer` only",
            status="not_supported",
            client_status="partial",
        ),
    )
    mutual_tls: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="OAS 3.1",
            doc_name="mutualTLS",
            description="Mutual TLS security scheme",
            status="not_supported",
            client_status="not_supported",
        ),
    )
    cookie_style: bool = field(
        default=False,
        metadata=FeatureMetadata(
            introduced="OAS 3.2",
            doc_name="style: cookie",
            description="Cookie parameter style; earlier versions do not declare it",
            status="supported",
            client_status="supported",
        ),
    )
    deep_object_style: bool = field(
        default=True,
        metadata=FeatureMetadata(
            introduced="OAS 3.0",
            doc_name="style: deepObject",
            description="Query parameters written as `name[key]=value`; the client writes objects, and arrays and "
            "scalars, which OpenAPI leaves undefined, of query parameters and URL-encoded form members as "
            "`name[]=value` and `name=value` only with `--client-deep-object-brackets`",
            status="supported",
            client_status="partial",
        ),
    )
    structured_parameter_values: bool = field(
        default=True,
        metadata=FeatureMetadata(
            introduced="OAS 3.0",
            doc_name="Structured style values",
            description="Parameter and form member values that are no single scalar kind, such as nested objects, "
            "unions and `allOf`, written with a style; the client writes only a parameter outside a header, or a "
            "URL-encoded form member, that takes one item or an array of them",
            status="supported",
            client_status="partial",
        ),
    )
    binary_in_text_media: bool = field(
        default=True,
        metadata=FeatureMetadata(
            introduced="OAS 3.0",
            doc_name="Binary strings in text media",
            description="`format: binary` strings inside a JSON, URL-encoded or text body, a parameter or a header",
            status="supported",
            client_status="not_supported",
        ),
    )
    multipart_media: bool = field(
        default=True,
        metadata=FeatureMetadata(
            introduced="OAS 3.0",
            doc_name="multipart media types",
            description="Multipart bodies; the client maps the parts of `multipart/form-data` with an object schema, "
            "or a union of objects that agree on the parts of each property, only, such as not `multipart/related`",
            status="supported",
            client_status="partial",
        ),
    )
    part_content_type: bool = field(
        default=True,
        metadata=FeatureMetadata(
            introduced="OAS 3.0",
            doc_name="Encoding contentType",
            description="Media types of form-data parts; the client writes a part holding no files as JSON, or its "
            "scalars as text in a media type other than a form or multipart media type",
            status="supported",
            client_status="partial",
        ),
    )

    @classmethod
    def from_openapi_version(cls, version: OpenAPIVersion) -> OpenAPISchemaFeatures:
        """Create OpenAPISchemaFeatures from an OpenAPI version."""
        if version == OpenAPIVersion.V30:
            return cls(definitions_key="definitions", read_only_write_only=True, nullable_keyword=True)
        flags: dict[str, Any] = {**_draft_flags(JsonSchemaVersion.Draft202012), "webhooks": True, "mutual_tls": True}
        if version == OpenAPIVersion.V32:
            flags |= {
                "media_item_schema": True,
                "openapi_self": True,
                "querystring_parameter": True,
                "cookie_style": True,
            }
        return cls(**flags)

    @classmethod
    def from_asyncapi_version(cls, version: AsyncAPIVersion) -> OpenAPISchemaFeatures:
        """Create schema features for AsyncAPI Schema Objects."""
        if version == AsyncAPIVersion.V2:
            return cls.from_openapi_version(OpenAPIVersion.V30)
        return cls.from_openapi_version(OpenAPIVersion.V31)


def feature_label(feature: str) -> str:
    """Return how messages name a feature: its documented name and the version that introduced it."""
    metadata = next(item.metadata for item in fields(OpenAPISchemaFeatures) if item.name == feature)
    return f"{metadata['doc_name']} ({metadata['introduced']})"


SchemaFeaturesT = TypeVar("SchemaFeaturesT", bound=JsonSchemaFeatures)


def _detect_declared_jsonschema_version(data: dict[str, Any]) -> JsonSchemaVersion | None:
    """Detect a recognized declaration without applying undeclared-schema heuristics."""
    if isinstance(schema_url := data.get("$schema", ""), str):
        for version, _ in _DRAFT_FLAG_CHANGES:
            if version.value in schema_url:
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
    if isinstance(version := data.get("asyncapi", ""), str) and version.startswith("2."):
        return AsyncAPIVersion.V2
    return AsyncAPIVersion.V3


SchemaKind: TypeAlias = Literal["jsonschema", "openapi", "asyncapi"]
SchemaVersion: TypeAlias = JsonSchemaVersion | OpenAPIVersion | AsyncAPIVersion


@dataclass(frozen=True)
class DocumentDialect:
    """The schema version a document is read by, and the features of that version."""

    version: SchemaVersion
    features: JsonSchemaFeatures


@dataclass(frozen=True)
class _KindReading:
    """How a kind of document declares its version: the config field, ``auto``, the declaration and the features."""

    config_field: str
    auto: SchemaVersion
    declares: Callable[[dict[str, Any]], bool]
    detect: Callable[[dict[str, Any]], Any]
    features: Callable[[Any], JsonSchemaFeatures]


_KIND_READINGS: Final[dict[SchemaKind, _KindReading]] = {
    "jsonschema": _KindReading(
        "jsonschema_version",
        JsonSchemaVersion.Auto,
        lambda raw: _detect_declared_jsonschema_version(raw) is not None,
        detect_jsonschema_version,
        JsonSchemaFeatures.from_version,
    ),
    "openapi": _KindReading(
        "openapi_version",
        OpenAPIVersion.Auto,
        lambda raw: "openapi" in raw,
        detect_openapi_version,
        OpenAPISchemaFeatures.from_openapi_version,
    ),
    "asyncapi": _KindReading(
        "asyncapi_version",
        AsyncAPIVersion.Auto,
        lambda raw: "asyncapi" in raw,
        detect_asyncapi_version,
        OpenAPISchemaFeatures.from_asyncapi_version,
    ),
}


def document_dialect(
    raw: dict[str, Any],
    kind: SchemaKind,
    *,
    configured: SchemaVersion | None = None,
    inherited: DocumentDialect | None = None,
) -> DocumentDialect:
    """Return the schema version a document of ``kind`` is read by, and its features.

    The configured version unless it is ``auto``; else the version the document declares (`$schema`, or the
    ``openapi`` or ``asyncapi`` field of a specification); else ``inherited``, the dialect of the document whose
    reference loads it; without one, the document is read by itself (no `$schema`: `$defs` reads as 2020-12, else
    draft 7; an empty document as ``auto``). A JSON Schema document that a specification loads reads its keywords by
    its own `$schema` and the specification's own fields as the loading document does.
    """
    reading = _KIND_READINGS[kind]
    if configured is not None and configured != reading.auto:
        version: Any = configured
    elif inherited is None or reading.declares(raw):
        version = reading.detect(raw) if raw else reading.auto
    elif (version := _detect_declared_jsonschema_version(raw)) is None:
        return inherited
    else:
        own = JsonSchemaFeatures.from_version(version)
        return DocumentDialect(
            version, replace(inherited.features, **{item.name: getattr(own, item.name) for item in fields(own)})
        )
    return DocumentDialect(version, reading.features(version))


DataFormatMapping: TypeAlias = "dict[str, dict[str, Types]]"


def get_data_formats(*, is_openapi: bool = False) -> DataFormatMapping:
    """Get merged data formats based on schema type.

    Args:
        is_openapi: If True, includes OpenAPI-specific formats.

    Returns:
        Merged dictionary of data formats.
    """
    from datamodel_code_generator.types import Types  # noqa: PLC0415

    formats: DataFormatMapping = {
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
    if is_openapi:
        formats["string"] |= {"binary": Types.binary, "password": Types.password}
    return formats


__all__ = [
    "DataFormatMapping",
    "DocumentDialect",
    "FeatureMetadata",
    "JsonSchemaFeatures",
    "OpenAPISchemaFeatures",
    "SchemaFeaturesT",
    "SchemaKind",
    "detect_asyncapi_version",
    "detect_jsonschema_version",
    "detect_openapi_version",
    "document_dialect",
    "get_data_formats",
]
