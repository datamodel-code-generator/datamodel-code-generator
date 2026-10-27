"""OpenAPI and Swagger specification parser.

Extends JsonSchemaParser to handle OpenAPI 2.0 (Swagger), 3.0, 3.1, and 3.2
specifications, including paths, operations, parameters, and request/response bodies.
"""

from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from enum import Enum
from functools import cache, cached_property, partial
from pathlib import Path
from re import Pattern
from typing import TYPE_CHECKING, Any, ClassVar, Optional, TypeVar, Union, cast
from warnings import warn

from pydantic import Field, StrictBool, ValidationError, field_validator
from typing_extensions import Unpack, override

from datamodel_code_generator import (
    Error,
    InputFileType,
    OpenAPIScope,
    SchemaParseError,
    snooper_to_methods,
)
from datamodel_code_generator._source import YamlValue
from datamodel_code_generator.deprecations import warn_deprecated
from datamodel_code_generator.enums import OpenAPIAsDefinitions, OpenAPIVersion, VersionMode
from datamodel_code_generator.parser._schema_dispatch import Default, DispatchTable, Row, SchemaSite
from datamodel_code_generator.parser._schema_graph import RefOrigin, keep_resolved_ref
from datamodel_code_generator.parser.base import Result, get_special_path
from datamodel_code_generator.parser.jsonschema import (
    JsonSchemaObject,
    JsonSchemaParser,
    _locate,
    _located,
    _located_model_schema,
    _named_path,
    get_model_by_path,
)
from datamodel_code_generator.reference import SCHEMA_LOCATION_KEY, FieldNameResolver, is_url, snake_to_upper_camel
from datamodel_code_generator.types import (
    DataType,
    EmptyDataType,
    build_union,
)
from datamodel_code_generator.util import BaseModel

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator
    from contextlib import AbstractContextManager
    from urllib.parse import ParseResult

    from pydantic import GetCoreSchemaHandler, ValidatorFunctionWrapHandler
    from pydantic_core import CoreSchema

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator._types import OpenAPIParserConfigDict
    from datamodel_code_generator.config import OpenAPIParserConfig
    from datamodel_code_generator.model import DataModel, DataModelFieldBase
    from datamodel_code_generator.parser._null_facts import NullMode
    from datamodel_code_generator.parser.schema_version import OpenAPISchemaFeatures
    from datamodel_code_generator.reference import SchemaLocation


RE_APPLICATION_JSON_PATTERN: Pattern[str] = re.compile(r"^application/.*json$")

OPERATION_NAMES: list[str] = [
    "get",
    "put",
    "post",
    "delete",
    "patch",
    "head",
    "options",
    "trace",
]


class ParameterLocation(Enum):
    """Represent OpenAPI parameter locations."""

    query = "query"
    querystring = "querystring"
    header = "header"
    path = "path"
    cookie = "cookie"


BaseModelT = TypeVar("BaseModelT", bound=BaseModel)


class ReferenceObject(BaseModel):
    """Represent an OpenAPI reference object ($ref)."""

    ref: str = Field(..., alias="$ref")

    @field_validator("ref", mode="wrap")
    @classmethod
    def _keep_resolved_ref(cls, value: Any, handler: ValidatorFunctionWrapHandler) -> Any:
        """Keep the edge of a reference of a loaded document."""
        return keep_resolved_ref(value, handler(value))


class ExampleObject(BaseModel):
    """Represent an OpenAPI example object."""

    summary: Optional[str] = None  # noqa: UP045
    description: Optional[str] = None  # noqa: UP045
    value: YamlValue = None
    externalValue: Optional[str] = None  # noqa: N815, UP045


class MediaObject(BaseModel):
    """Represent an OpenAPI media type object."""

    schema_: Optional[Union[ReferenceObject, JsonSchemaObject, bool]] = Field(None, alias="schema")  # noqa: UP007, UP045
    itemSchema: Optional[Union[ReferenceObject, JsonSchemaObject, bool]] = None  # noqa: N815, UP007, UP045
    example: YamlValue = None
    examples: Optional[Union[str, ReferenceObject, ExampleObject]] = None  # noqa: UP007, UP045

    @classmethod
    @override
    def __get_pydantic_core_schema__(cls, source: Any, handler: GetCoreSchemaHandler, /) -> CoreSchema:
        """Validate media type objects so that each takes the location of the document object it is validated from."""
        return _located_model_schema(source, handler)

    @property
    def schema_location(self) -> SchemaLocation | None:
        """Return the location of the media type object in its document."""
        return self.__dict__.get(SCHEMA_LOCATION_KEY)


MediaSchema = JsonSchemaObject | ReferenceObject | bool
RawSchema = dict[str, YamlValue] | StrictBool


@dataclass(frozen=True)
class MediaSchemaSource:
    """Schema extracted from a parsed OpenAPI media type object.

    ``location`` locates a boolean schema, whose value cannot carry its location.
    """

    schema: MediaSchema
    from_item_schema: bool = False
    location: SchemaLocation | None = None


@dataclass(frozen=True)
class RawMediaSchemaSource:
    """Schema extracted from a raw OpenAPI media type object."""

    schema: RawSchema
    from_item_schema: bool = False


class RawMediaObject(BaseModel):
    """Raw OpenAPI media type object fields used by the parser."""

    schema_: RawSchema | None = Field(None, alias="schema")
    itemSchema: RawSchema | None = None  # noqa: N815


class ParameterObject(BaseModel):
    """Represent an OpenAPI parameter object."""

    name: Optional[str] = None  # noqa: UP045
    in_: Optional[ParameterLocation] = Field(None, alias="in")  # noqa: UP045
    description: Optional[str] = None  # noqa: UP045
    required: bool = False
    deprecated: bool = False
    schema_: Optional[JsonSchemaObject] = Field(None, alias="schema")  # noqa: UP045
    example: YamlValue = None
    examples: Optional[Union[str, ReferenceObject, ExampleObject]] = None  # noqa: UP007, UP045
    content: dict[str, MediaObject] = Field(default_factory=dict)


class HeaderObject(BaseModel):
    """Represent an OpenAPI header object."""

    description: Optional[str] = None  # noqa: UP045
    required: bool = False
    deprecated: bool = False
    schema_: Optional[JsonSchemaObject] = Field(None, alias="schema")  # noqa: UP045
    example: YamlValue = None
    examples: Optional[Union[str, ReferenceObject, ExampleObject]] = None  # noqa: UP007, UP045
    content: dict[str, MediaObject] = Field(default_factory=dict)


class RequestBodyObject(BaseModel):
    """Represent an OpenAPI request body object."""

    description: Optional[str] = None  # noqa: UP045
    content: dict[str, MediaObject] = Field(default_factory=dict)
    required: bool = False


class ResponseObject(BaseModel):
    """Represent an OpenAPI response object."""

    description: Optional[str] = None  # noqa: UP045
    headers: dict[str, ParameterObject] = Field(default_factory=dict)
    content: dict[Union[str, int], MediaObject] = Field(default_factory=dict)  # noqa: UP007


class Operation(BaseModel):
    """Represent an OpenAPI operation object."""

    tags: list[str] = Field(default_factory=list)
    summary: Optional[str] = None  # noqa: UP045
    description: Optional[str] = None  # noqa: UP045
    operationId: Optional[str] = None  # noqa: N815, UP045
    parameters: list[Union[ReferenceObject, ParameterObject]] = Field(default_factory=list)  # noqa: UP007
    requestBody: Optional[Union[ReferenceObject, RequestBodyObject]] = None  # noqa: N815, UP007, UP045
    responses: dict[Union[str, int], Union[ReferenceObject, ResponseObject]] = Field(default_factory=dict)  # noqa: UP007
    deprecated: bool = False


class ComponentsObject(BaseModel):
    """Represent an OpenAPI components object."""

    schemas: dict[str, Union[ReferenceObject, JsonSchemaObject]] = Field(default_factory=dict)  # noqa: UP007
    responses: dict[str, Union[ReferenceObject, ResponseObject]] = Field(default_factory=dict)  # noqa: UP007
    examples: dict[str, Union[ReferenceObject, ExampleObject]] = Field(default_factory=dict)  # noqa: UP007
    requestBodies: dict[str, Union[ReferenceObject, RequestBodyObject]] = Field(default_factory=dict)  # noqa: N815, UP007
    headers: dict[str, Union[ReferenceObject, HeaderObject]] = Field(default_factory=dict)  # noqa: UP007


_HELD_OBJECTS: dict[type[BaseModel], tuple[tuple[str, type[BaseModel], bool], ...]] = {
    Operation: (
        ("parameters", ParameterObject, True),
        ("requestBody", RequestBodyObject, False),
        ("responses", ResponseObject, True),
    ),
    ParameterObject: (("schema", JsonSchemaObject, False), ("content", MediaObject, True)),
    RequestBodyObject: (("content", MediaObject, True),),
    ResponseObject: (("headers", ParameterObject, True), ("content", MediaObject, True)),
    MediaObject: (("schema", JsonSchemaObject, False), ("itemSchema", JsonSchemaObject, False)),
}
"""The fields of OpenAPI object models that hold schemas or such objects: name, model, and whether it holds many."""


def _held_schemas(
    raw: object, kind: type[BaseModel], tokens: tuple[str, ...] = ()
) -> Iterator[tuple[dict[str, YamlValue], str, tuple[str, ...]]]:
    """Yield the holder, key and tokens below ``raw`` of each schema that ``raw``, an OpenAPI ``kind``, holds.

    A reference object holds none, and a field set to null holds nothing, as the models read it.
    """
    if not isinstance(raw, dict) or "$ref" in raw:
        return
    for field, member, many in _HELD_OBJECTS.get(kind, ()):
        if (value := raw.get(field)) is None:
            continue
        at = (*tokens, field)
        if member is JsonSchemaObject:
            yield raw, field, at
        elif not many:
            yield from _held_schemas(value, member, at)
        else:
            entries = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
            for key, entry in entries:
                yield from _held_schemas(entry, member, (*at, str(key)))


def is_inline_object_body(obj: JsonSchemaObject) -> bool:
    """Return whether the legacy paths and webhooks dispatch types an object body inline that a definition models.

    Such a body admits objects by its type array, or by its map keywords without a type; a definition of the same
    schema gets a model, and so does the body with ``--openapi-as-definitions paths-bodies``. Bodies that are
    ``is_object``, arrays, combinations, enums, or references get the definitions' model kind either way.
    """
    if obj.ref or obj.is_array or obj.is_object or obj.enum or any((obj.allOf, obj.oneOf, obj.anyOf)):
        return False
    if obj.type is not None:
        return isinstance(obj.type, list) and "object" in obj.type
    return any(keyword is not None for keyword in (obj.additionalProperties, obj.patternProperties, obj.propertyNames))


@cache
def _operation_name_resolver(target_python_version: PythonVersion) -> FieldNameResolver:
    """Return the resolver that names operation models by the rules of the target Python version."""
    return FieldNameResolver(target_python_version=target_python_version)


@snooper_to_methods()
class OpenAPIParser(JsonSchemaParser):
    """Parser for OpenAPI 2.0/3.0/3.1/3.2 and Swagger specifications."""

    SCHEMA_PATHS: ClassVar[list[str]] = ["#/components/schemas"]
    _input_file_type: ClassVar[InputFileType] = InputFileType.OpenAPI
    _non_dict_source_is_invalid: ClassVar[bool] = True
    _component_schemas_are_resources: ClassVar[bool] = True
    config: OpenAPIParserConfig

    @cached_property
    def schema_features(self) -> OpenAPISchemaFeatures:
        """Get schema features based on config or detected OpenAPI version."""
        # OpenAPI parses a single document context, so caching is intentional.
        # AsyncAPI overrides this with a live property because it swaps raw_obj while resolving refs.
        return self._schema_features_for(self.raw_obj)

    def _schema_features_for(self, root: dict[str, YamlValue]) -> OpenAPISchemaFeatures:
        """Get the Schema Object features of a root document based on config or its detected OpenAPI version."""
        from datamodel_code_generator.parser.schema_version import (  # noqa: PLC0415
            OpenAPISchemaFeatures,
            detect_openapi_version,
        )

        config_version = getattr(self.config, "openapi_version", None)
        if config_version is not None and config_version != OpenAPIVersion.Auto:
            return OpenAPISchemaFeatures.from_openapi_version(config_version)
        version = detect_openapi_version(root) if root else OpenAPIVersion.Auto
        return OpenAPISchemaFeatures.from_openapi_version(version)

    @property
    @override
    def _ref_siblings_are_parts(self) -> bool:
        """Whether the document's OpenAPI or AsyncAPI version reads the keywords beside a ``$ref`` as parts."""
        return self.schema_features.ref_sibling_keywords

    @override
    def _definition_entry_prefixes(self) -> tuple[tuple[str, ...], ...]:
        """Return the definitions containers, and under --openapi-as-definitions ref-components the components too.

        A specification parses its components under ``#/components``, ``schemas``, which then name a container as
        ``#/components/schemas`` does where another document's components are parsed.
        """
        prefixes = super()._definition_entry_prefixes()
        if OpenAPIAsDefinitions.RefComponents not in self.config.openapi_as_definitions:
            return prefixes
        return (*prefixes, *((f"#/{first}", *rest) for _, (first, *rest) in self.schema_paths))

    _config_class_name: ClassVar[str] = "OpenAPIParserConfig"
    _supports_api_scope: ClassVar[bool] = False
    _api_scope_error: ClassVar[str] = "OpenAPIScope.Api requires ApiOpenAPIParser"
    _named_entry_maps: ClassVar[frozenset[str]] = JsonSchemaParser._named_entry_maps | {"headers"}  # ruff: ignore[private-member-access]

    def __init__(
        self,
        source: str | Path | list[Path] | ParseResult,
        *,
        config: OpenAPIParserConfig | None = None,
        **options: Unpack[OpenAPIParserConfigDict],
    ) -> None:
        """Initialize the OpenAPI parser with extensive configuration options."""
        if config is None and options.get("wrap_string_literal") is None:
            options["wrap_string_literal"] = False
        super().__init__(source=source, config=config, **options)
        self.open_api_scopes: list[OpenAPIScope] = self.config.openapi_scopes or [OpenAPIScope.Schemas]
        if OpenAPIScope.Api in self.open_api_scopes and not self._supports_api_scope:
            raise Error(self._api_scope_error)
        self.include_path_parameters: bool = self.config.include_path_parameters
        self.use_status_code_in_response_name: bool = self.config.use_status_code_in_response_name
        self.openapi_include_paths: list[str] | None = self.config.openapi_include_paths
        self.openapi_include_info_version: bool = self.config.openapi_include_info_version
        self.openapi_info_version: str | None = None
        self.inline_object_bodies = False
        if (
            self.openapi_include_paths
            and OpenAPIScope.Paths not in self.open_api_scopes
            and OpenAPIScope.Api not in self.open_api_scopes
        ):
            warn(
                "--openapi-include-paths has no effect without --openapi-scopes paths",
                stacklevel=2,
            )
        self._discriminator_documents: set[str] = set()
        self._discriminator_schemas: dict[str, dict[str, Any]] = {}
        self._discriminator_subtypes: dict[str, list[str]] = defaultdict(list)
        self._parameter_models: list[tuple[DataModel, tuple[str, ...]]] = []
        self._specification_documents: set[str] = set()

    def get_ref_model(self, ref: str) -> dict[str, Any]:
        """Resolve a reference to its model definition."""
        ref_file, ref_path = self._ref_path(ref).split("#", 1)
        ref_body = self._get_ref_body(ref_file) if ref_file else self.raw_obj
        model = cast("dict[str, Any]", get_model_by_path(ref_body, ref_path.split("/")[1:]))
        self._check_ref_target("get_ref_model", ref, self._raw_location(model))
        return model

    @contextmanager
    def openapi_self_context(self, specification: dict[str, Any]) -> Generator[None, None, None]:
        """Temporarily use OpenAPI 3.2 $self as the document root identifier."""
        if not isinstance(openapi_self := specification.get("$self"), str):
            yield
            return
        if not self.schema_features.openapi_self:
            yield
            return

        previous_root_id = self.root_id
        self.root_id = openapi_self
        try:
            yield
        finally:
            self.root_id = previous_root_id

    @override
    def _document_self_context(self, document: str, raw: dict[str, Any]) -> AbstractContextManager[None]:
        """Return the context in which an input specification names itself by its OpenAPI 3.2 `$self`.

        A document loaded through a reference answers to its `$self` only for references spelled with it, and keeps the
        location it was loaded from as the base of every other.
        """
        if document in self._specification_documents:
            return self.openapi_self_context(raw)
        if "openapi" in raw and isinstance(openapi_self := raw.get("$self"), str) and self.schema_features.openapi_self:
            scope = "/".join(self.model_resolver.current_root)
            self.model_resolver.ids[scope].setdefault(openapi_self, f"{scope}#")
        return nullcontext()

    def _key_error(self, key: object, kind: str, path: list[str]) -> SchemaParseError:  # noqa: PLR6301
        """Return the error at ``path`` for ``key``, which names a ``kind`` and is no string.

        A YAML key such as ``1`` loads as a number.
        """
        return SchemaParseError(f"{kind} {key!r} is not a string", path=path)

    def _key_name(self, holder: object, key: object, path: list[str], kind: str) -> str:
        """Return ``key``, which names a ``kind`` in ``holder`` at ``path``; a key that is no string is an error."""
        if isinstance(key, str):
            return key
        raise self._key_error(key, kind, _named_path(self._member_location(holder, str(key)), [*path, str(key)]))

    def _named_components(self, components: dict[Any, YamlValue], path: list[str]) -> dict[str, YamlValue]:
        """Return ``components`` once each of its keys names a component (``_key_name``)."""
        for key in components:
            self._key_name(components, key, path, "component name")
        return components

    def _parse_specification(self, specification: dict[str, Any], path_parts: list[str]) -> None:
        """Parse the loaded OpenAPI specification."""
        if self.openapi_include_info_version:
            self._update_openapi_info_version(specification)
        self._collect_discriminator_schemas()
        schemas: dict[str, Any] = specification.get("components", {}).get("schemas", {})
        paths: dict[str, Any] = specification.get("paths", {})
        security: list[dict[str, list[str]]] | None = specification.get("security")
        # Warn if schemas is empty but paths exist and only Schemas scope is used
        if not schemas and self.open_api_scopes == [OpenAPIScope.Schemas] and paths:
            warn(
                "No schemas found in components/schemas. If your schemas are defined in "
                "external files referenced from paths, consider using --openapi-scopes paths",
                stacklevel=2,
            )
        if OpenAPIScope.Schemas in self.open_api_scopes:
            for obj_name in self._named_components(schemas, [*path_parts, "#/components", "schemas"]):
                self._parse_raw_member(obj_name, schemas, obj_name, [*path_parts, "#/components", "schemas", obj_name])
        if OpenAPIScope.Paths in self.open_api_scopes:
            # Resolve $ref in global parameter list
            global_parameters = [
                self._get_ref_body(p["$ref"]) if isinstance(p, dict) and "$ref" in p else p
                for p in paths.get("parameters", [])
                if isinstance(p, dict)
            ]
            self._process_path_items(paths, path_parts, "paths", global_parameters, security)

        if OpenAPIScope.Webhooks in self.open_api_scopes:
            webhooks: dict[str, dict[str, Any]] = specification.get("webhooks", {})
            self._process_path_items(
                webhooks,
                path_parts,
                "webhooks",
                [],
                security,
                strip_leading_slash=False,
                apply_path_filter=False,
            )

        if OpenAPIScope.RequestBodies in self.open_api_scopes:
            request_bodies: dict[str, Any] = specification.get("components", {}).get("requestBodies", {})
            for body_name, raw_body in request_bodies.items():
                resolved_body = self.get_ref_model(raw_body["$ref"]) if "$ref" in raw_body else raw_body
                content = resolved_body.get("content", {})
                for media_type, media_obj in content.items():
                    media_schema = self._get_raw_media_schema(media_obj)
                    if media_schema is None:
                        continue
                    name = self._key_name(
                        request_bodies, body_name, [*path_parts, "#/components", "requestBodies"], "component name"
                    )
                    media_path = [
                        *path_parts,
                        "#/components",
                        "requestBodies",
                        name,
                        "content",
                        media_type,
                    ]
                    schema_path = self._media_schema_path(
                        media_path if media_schema.from_item_schema else [*media_path, "schema"],
                        from_item_schema=media_schema.from_item_schema,
                    )
                    if media_schema.from_item_schema:
                        self.parse_raw_obj(name, media_schema.schema, schema_path)
                    else:
                        self._parse_raw_member(name, media_obj, "schema", schema_path)

    def _insert_info_version_constant(self, body: str, info_version: str) -> str:  # noqa: PLR6301
        constant = f"OPENAPI_INFO_VERSION = {info_version!r}"
        if not body:
            return constant

        lines = body.splitlines()
        insert_line = 0
        while insert_line < len(lines):
            stripped = lines[insert_line].strip()
            if not stripped:
                insert_line += 1
                continue
            if not stripped.startswith(("import ", "from ")):
                break

            paren_balance = stripped.count("(") - stripped.count(")")
            insert_line += 1
            while paren_balance > 0 and insert_line < len(lines):
                stripped = lines[insert_line].strip()
                paren_balance += stripped.count("(") - stripped.count(")")
                insert_line += 1

        prefix = lines[:insert_line]
        while prefix and not prefix[-1].strip():
            prefix.pop()
        return "\n".join([*prefix, "", constant, "", "", *lines[insert_line:]])

    def _update_openapi_info_version(self, specification: dict[str, Any]) -> None:
        info = specification.get("info")
        if isinstance(info, dict) and info.get("version") is not None:
            self.openapi_info_version = str(info["version"])
            return

        warn(
            "--openapi-include-info-version was specified, but info.version was not found",
            stacklevel=2,
        )

    def parse(self, *args: Any, **kwargs: Any) -> str | dict[tuple[str, ...], Result]:
        """Parse OpenAPI schema and optionally emit the info.version constant."""
        result = super().parse(*args, **kwargs)
        if not self.openapi_include_info_version or self.openapi_info_version is None:
            return result

        if isinstance(result, str):
            return self._insert_info_version_constant(result, self.openapi_info_version)

        root_init = ("__init__.py",)
        root_result = result.get(root_init)
        if root_result is None:
            result[root_init] = Result(body=self._insert_info_version_constant("", self.openapi_info_version))
        else:
            root_result.body = self._insert_info_version_constant(root_result.body, self.openapi_info_version)
        return result

    @cached_property
    @override
    def _null_mode(self) -> NullMode:
        """The null mode of JSON Schema, reading ``nullable`` beside a scalar type as a type list holding null."""
        return replace(super()._null_mode, nullable_as_type_list=True)

    def _get_data_type(self, obj: JsonSchemaObject, *, localize_constraints: bool) -> DataType:
        """Get data type from JSON schema object, warning on ``nullable`` where the OpenAPI version deprecates it.

        OpenAPI 3.0 writes ``nullable: true``; 3.1 and later write ``type: [..., "null"]`` and still read ``nullable``,
        with a warning in Strict mode (``schema_features.nullable_keyword``).
        """
        if (
            obj.nullable
            and not self.schema_features.nullable_keyword
            and (self.config.schema_version_mode == VersionMode.Strict)
        ):
            warn_deprecated("schema.openapi-nullable", stacklevel=2)
        return super()._get_data_type(obj, localize_constraints=localize_constraints)

    def _normalize_discriminator_mapping_ref(self, mapping_value: str) -> str:  # noqa: PLR6301
        """Normalize a discriminator mapping value to a full $ref path.

        Per OpenAPI spec, mapping values can be either:
        - Full refs: "#/components/schemas/Pet" or "./other.yaml#/components/schemas/Pet"
        - Short names: "Pet" or "Pet.V1" (relative to #/components/schemas/)
        - Relative paths: "schemas/Pet" or "./other.yaml"

        Values containing "/" or "#" are treated as paths/refs and passed through.
        All other values (including those with dots like "Pet.V1") are treated as
        short schema names and normalized to full refs.

        Note: Bare file references without path separators (e.g., "other.yaml") will be
        treated as schema names. Use "./other.yaml" format for file references.

        Note: This could be a staticmethod, but @snooper_to_methods() decorator
        converts staticmethods to regular functions when pysnooper is installed.
        """
        if "/" in mapping_value or "#" in mapping_value:
            return mapping_value
        return f"#/components/schemas/{mapping_value}"

    def _normalize_discriminator(self, discriminator: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of the discriminator dict with normalized mapping refs."""
        result = discriminator.copy()
        mapping = discriminator.get("mapping")
        if mapping:
            result["mapping"] = {
                k: self._normalize_discriminator_mapping_ref(v) for k, v in mapping.items() if isinstance(v, str)
            }
        return result

    def _discriminator_mapping_ref(self, name: str, value: str, mapping: SchemaLocation | None) -> str:
        """Return a discriminator mapping value as a reference, with the edge of the mapping located at ``mapping``."""
        normalized = self._normalize_discriminator_mapping_ref(value)
        return self._derived_ref(normalized, mapping, name, normalized, RefOrigin.DISCRIMINATOR_MAPPING)

    def _get_discriminator_subtype_data_type(self, ref: str) -> DataType:
        """Resolve one subtype without recursively expanding its discriminator."""
        self.resolve_ref(ref)
        return super().get_ref_data_type(ref)

    def _get_discriminator_union_type(self, ref: str) -> DataType | None:
        """Create a union type for discriminator subtypes if available.

        First tries to use allOf subtypes. If none found, falls back to using
        the discriminator mapping to create the union type. This handles cases
        where schemas don't use allOf inheritance but have explicit discriminator mappings.
        """
        current_document = "/".join(self.model_resolver.current_root)
        subtypes = self._discriminator_subtypes.get(ref, [])
        if not subtypes:
            discriminator = self._discriminator_schemas[ref]
            mapping = discriminator.get("mapping", {})
            if mapping:
                subtypes = [
                    self._discriminator_mapping_ref(name, value, self._raw_location(mapping))
                    for name, value in mapping.items()
                    if isinstance(value, str)
                ]
                if subtypes and ref.partition("#")[0] == current_document:
                    return build_union(self.data_type, [self._get_discriminator_subtype_data_type(v) for v in subtypes])
                subtypes = [keep_resolved_ref(v, self._ref_path(v)) for v in subtypes]
        if not subtypes:
            return None
        data_types: list[DataType] = []
        for subtype in subtypes:
            document, _, fragment = subtype.partition("#")
            if document != current_document:
                with (
                    self.model_resolver.current_base_path_context(self.base_path),
                    self.model_resolver.base_url_context(None),
                    self.root_id_context({}),
                ):
                    data_types.append(self._get_discriminator_subtype_data_type(subtype))
            else:
                data_types.append(self._get_discriminator_subtype_data_type(keep_resolved_ref(subtype, f"#{fragment}")))
        return build_union(self.data_type, data_types)

    def get_ref_data_type(self, ref: str) -> DataType:
        """Get data type for a reference, handling discriminator polymorphism."""
        if self._discriminator_schemas and (resolved_ref := self._ref_path(ref)) in self._discriminator_schemas:
            return self._get_discriminator_union_type(resolved_ref) or super().get_ref_data_type(ref)
        document_count = len(self._discriminator_documents)
        data_type = super().get_ref_data_type(ref)
        if (
            len(self._discriminator_documents) != document_count
            and self._discriminator_schemas
            and (resolved_ref := self._ref_path(ref)) in self._discriminator_schemas
        ):
            return self._get_discriminator_union_type(resolved_ref) or data_type
        return data_type

    def parse_object_fields(
        self,
        obj: JsonSchemaObject,
        path: list[str],
        module_name: Optional[str] = None,  # noqa: UP045
        class_name: Optional[str] = None,  # noqa: UP045
    ) -> list[DataModelFieldBase]:
        """Parse object fields, adding discriminator info for allOf polymorphism."""
        fields = super().parse_object_fields(obj, path, module_name, class_name=class_name)
        properties = obj.properties or {}

        result_fields: list[DataModelFieldBase] = []
        for field_obj in fields:
            field = properties.get(field_obj.original_name)

            if (
                isinstance(field, JsonSchemaObject)
                and field.ref
                and self._discriminator_schemas
                and (discriminator := self._discriminator_schemas.get(resolved_ref := self._ref_path(field.ref)))
            ):
                new_field_type = self._get_discriminator_union_type(resolved_ref)
                if new_field_type is None:
                    result_fields.append(field_obj)
                    continue
                normalized_discriminator = self._normalize_discriminator(discriminator)
                field_obj = self.data_model_field_type(**{  # noqa: PLW2901
                    **field_obj.__dict__,
                    "data_type": new_field_type,
                    "extras": {**field_obj.extras, "discriminator": normalized_discriminator},
                })
            result_fields.append(field_obj)

        return result_fields

    def resolve_object(self, obj: ReferenceObject | BaseModelT, object_type: type[BaseModelT]) -> BaseModelT:
        """Resolve a reference object to its actual type or return the object as-is."""
        if isinstance(obj, ReferenceObject):
            return self._validated_target(self.get_ref_model(obj.ref), object_type)
        return obj

    def _validated_target(self, raw: object, object_type: type[BaseModelT]) -> BaseModelT:
        """Return the ``object_type`` a value of a loaded document validates to, validated once and shared."""
        return self._shared_object(
            raw, object_type, partial(self._validated_object, raw, object_type, [], self._raw_location(raw))
        )

    def _validated_object(
        self, raw: object, object_type: type[BaseModelT], path: list[str], location: SchemaLocation | None
    ) -> BaseModelT:
        """Validate ``raw``, an OpenAPI object at ``location`` (``path`` when unknown), as ``object_type``.

        One that does not validate is an error at the first schema in it that is no schema object, with the message of
        such a schema in ``components/schemas``, or else at the object itself.
        """
        try:
            return object_type.model_validate(raw, context=self._schema_validation_context)
        except ValidationError as error:
            for holder, key, tokens in _held_schemas(raw, object_type):
                self._validate_member(holder[key], [*path, *tokens], self._member_location(holder, key))
            message = f"{type(error).__name__}: {error}"
            raise SchemaParseError(message, _named_path(location, path), error) from error

    def _parse_schema_or_ref(
        self,
        name: str,
        schema: MediaSchema,
        path: list[str],
        *,
        location: SchemaLocation | None = None,
    ) -> DataType:
        """Parse a schema object or resolve a reference to get DataType; ``location`` locates a boolean schema."""
        if isinstance(schema, bool):
            return self.parse_schema(name, _locate(self._validate_schema_object(schema, path), location), path)
        if isinstance(schema, JsonSchemaObject):
            return self.parse_schema(name, schema, path)
        self.resolve_ref(schema.ref)
        return self.get_ref_data_type(schema.ref)

    @classmethod
    def _array_schema_from_media_item_schema(cls, item_schema: MediaSchema) -> JsonSchemaObject:
        """Represent OpenAPI 3.2 itemSchema as an array schema."""
        if isinstance(item_schema, bool):
            raw_item_schema: RawSchema = item_schema
        else:
            raw_item_schema = cast("dict[str, YamlValue]", item_schema.model_dump(by_alias=True, exclude_none=True))
        return JsonSchemaObject.model_validate({"type": "array", "items": raw_item_schema})

    def _get_media_schema(self, media_obj: MediaObject) -> MediaSchemaSource | None:
        """Return the schema represented by an OpenAPI media type object.

        The array that represents an ``itemSchema`` stands for the media type object that holds it.
        """
        location = media_obj.schema_location
        if media_obj.schema_ is not None:
            return MediaSchemaSource(media_obj.schema_, location=location and location.child("schema"))
        if media_obj.itemSchema is None or not self.schema_features.media_item_schema:
            return None
        return MediaSchemaSource(
            _locate(self._array_schema_from_media_item_schema(media_obj.itemSchema), location), from_item_schema=True
        )

    def _get_raw_media_schema(self, media_obj: object) -> RawMediaSchemaSource | None:
        """Return raw schema data from an OpenAPI media type object.

        Note: This could be a staticmethod, but @snooper_to_methods() decorator
        converts staticmethods to regular functions when pysnooper is installed.
        """
        try:
            raw_media_obj = RawMediaObject.model_validate(media_obj)
        except ValidationError:  # pragma: no cover
            return None

        if "schema_" in raw_media_obj.model_fields_set:
            if raw_media_obj.schema_ is None:
                return None
            return RawMediaSchemaSource(cast("dict[str, RawSchema]", media_obj)["schema"])
        if "itemSchema" not in raw_media_obj.model_fields_set or not self.schema_features.media_item_schema:
            return None
        if raw_media_obj.itemSchema is None:
            return None
        return RawMediaSchemaSource(
            _located(
                {"type": "array", "items": cast("dict[str, RawSchema]", media_obj)["itemSchema"]},
                self._raw_location(media_obj),
            ),
            from_item_schema=True,
        )

    def _media_schema_path(self, path: list[str], *, from_item_schema: bool) -> list[str]:  # noqa: PLR6301
        """Return the resolver path for a media schema."""
        if from_item_schema:
            return get_special_path("itemSchema", path)
        return path

    def _normalize_path(self, path: str) -> str:  # noqa: PLR6301
        """Normalize path for consistent matching.

        Note: This is an instance method (not static) due to the snooper_to_methods
        class decorator which does not preserve staticmethod descriptors.
        """
        if not path.startswith("/"):
            path = f"/{path}"
        return path

    def _matches_path_pattern(self, path: str) -> bool:
        """Check if path matches any of the include patterns."""
        if not self.openapi_include_paths:
            return True
        normalized_path = self._normalize_path(path)
        return any(
            fnmatch.fnmatch(normalized_path, self._normalize_path(pattern)) for pattern in self.openapi_include_paths
        )

    def _process_path_items(  # noqa: PLR0913
        self,
        items: dict[str, dict[str, Any]],
        base_path: list[str],
        scope_name: str,
        global_parameters: list[dict[str, Any]],
        security: list[dict[str, list[str]]] | None,
        *,
        strip_leading_slash: bool = True,
        apply_path_filter: bool = True,
    ) -> None:
        """Process path or webhook items with operations."""
        scope_path = [*base_path, f"#/{scope_name}"]
        kind = "path" if scope_name == "paths" else "webhook name"
        for item_name, methods_ in items.items():
            self._key_name(items, item_name, scope_path, kind)
            if apply_path_filter and not self._matches_path_pattern(item_name):
                continue
            item_ref = methods_.get("$ref")
            if item_ref:
                methods = self.get_ref_model(item_ref)
                # Extract base path from reference for external file resolution
                resolved_ref = self._ref_path(item_ref)
                ref_file = resolved_ref.split("#")[0] if "#" in resolved_ref else resolved_ref
                ref_base_path = Path(ref_file).parent if ref_file and not is_url(ref_file) else None
            else:
                methods = methods_
                ref_base_path = None

            item_parameters = global_parameters.copy()
            if "parameters" in methods:
                item_parameters.extend(methods["parameters"])

            relative_name = item_name[1:] if strip_leading_slash else item_name.removeprefix("/")
            path = [*scope_path, relative_name] if relative_name else get_special_path("root", scope_path)

            base_path_context = (
                self.model_resolver.current_base_path_context(ref_base_path) if ref_base_path else nullcontext()
            )
            with base_path_context:
                for operation_name, raw_operation in methods.items():
                    if operation_name not in OPERATION_NAMES:
                        continue
                    operation = raw_operation
                    if item_parameters:
                        operation = operation.copy()
                        if "parameters" in raw_operation:
                            operation_parameters = raw_operation["parameters"]
                            overridden_parameters = {
                                key
                                for parameter in operation_parameters
                                if (key := self._parameter_key(parameter)) is not None
                            }
                            operation["parameters"] = [
                                *operation_parameters,
                                *(
                                    parameter
                                    for parameter in item_parameters
                                    if self._parameter_key(parameter) not in overridden_parameters
                                ),
                            ]
                        else:
                            operation["parameters"] = item_parameters.copy()
                    if security is not None and "security" not in operation:
                        # fastapi-code-generator depends on inherited global security being materialized here.
                        if operation is raw_operation:
                            operation = operation.copy()
                        operation["security"] = security
                    self.parse_operation(operation, [*path, operation_name], location=self._raw_location(raw_operation))

    def _parameter_key(self, parameter: Any) -> tuple[str, str] | None:
        """Return the OpenAPI identity used for operation-level parameter overrides."""
        if not isinstance(parameter, dict):
            return None  # pragma: no cover - OpenAPI validation rejects malformed parameters
        if isinstance(ref := parameter.get("$ref"), str):
            parameter = self.get_ref_model(ref)
        if not isinstance(name := parameter.get("name"), str) or not isinstance(location := parameter.get("in"), str):
            return None  # pragma: no cover - OpenAPI validation rejects malformed parameters
        return name, location

    def parse_schema(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> DataType:
        """Parse a JSON schema object into a data type (``_OPENAPI_OPERATION_TABLE``)."""
        site = SchemaSite(name, self._read_false_all_of_values(self._array_all_of_site_schema(obj)), path)
        choice = self._OPENAPI_OPERATION_TABLE.choose(self, site)
        data_type = choice.build(self, choice.site)
        self.parse_ref(site.obj, path)
        return data_type

    def _parse_combined_operation_schema(self, site: SchemaSite[JsonSchemaObject]) -> DataType:
        obj, name, path = site.obj, site.name, site.path
        data_type = self.parse_root_type(name, obj, path)
        if isinstance(data_type, EmptyDataType) and obj.properties:
            self.parse_object(name, obj, path)
        return data_type

    def _parse_unmodeled_operation_schema(self, site: SchemaSite[JsonSchemaObject]) -> DataType:
        self.inline_object_bodies |= is_inline_object_body(site.obj)
        return self.get_data_type(site.obj)

    def _operation_array_applies(self, site: SchemaSite[JsonSchemaObject]) -> bool:  # noqa: PLR6301
        return site.obj.is_array

    def _build_operation_array(self, site: SchemaSite[JsonSchemaObject]) -> DataType:
        return self.parse_array(site.name, site.obj, [*site.path, site.name])

    def _operation_all_of_applies(self, site: SchemaSite[JsonSchemaObject]) -> bool:  # noqa: PLR6301
        return bool(site.obj.allOf)

    def _build_operation_all_of(self, site: SchemaSite[JsonSchemaObject]) -> DataType:
        return self.parse_all_of(site.name, site.obj, site.path)

    def _operation_one_of_any_of_applies(self, site: SchemaSite[JsonSchemaObject]) -> bool:  # noqa: PLR6301
        return bool(site.obj.oneOf or site.obj.anyOf)

    def _operation_object_applies(self, site: SchemaSite[JsonSchemaObject]) -> bool:  # noqa: PLR6301
        return site.obj.is_object

    def _build_operation_object(self, site: SchemaSite[JsonSchemaObject]) -> DataType:
        return self._parse_object_kind_schema(site.name, site.obj, site.path) or self.parse_object(
            site.name, site.obj, site.path
        )

    def _operation_inline_object_body_applies(self, site: SchemaSite[JsonSchemaObject]) -> bool:
        return (
            is_inline_object_body(site.obj) and OpenAPIAsDefinitions.PathsBodies in self.config.openapi_as_definitions
        )

    def _build_operation_inline_object_body(self, site: SchemaSite[JsonSchemaObject]) -> DataType:
        return self._parse_object_kind_schema(site.name, site.obj, site.path) or self.parse_root_type(
            site.name, site.obj, site.path
        )

    def _operation_enum_applies(self, site: SchemaSite[JsonSchemaObject]) -> bool:
        return bool(site.obj.enum and not self.ignore_enum_constraints and not self._reads_enum_as_const(site.obj))

    def _build_operation_enum(self, site: SchemaSite[JsonSchemaObject]) -> DataType:
        return self.parse_enum(site.name, self._definition_enum(site.obj), site.path)

    def _operation_ref_applies(self, site: SchemaSite[JsonSchemaObject]) -> bool:  # noqa: PLR6301
        return bool(site.obj.ref)

    def _build_operation_ref(self, site: SchemaSite[JsonSchemaObject]) -> DataType:
        return self.get_ref_data_type(cast("str", site.obj.ref))

    _OPENAPI_OPERATION_TABLE: ClassVar[DispatchTable[OpenAPIParser, JsonSchemaObject, DataType]] = DispatchTable(
        (
            Row("array", _operation_array_applies, _build_operation_array),
            Row("all-of", _operation_all_of_applies, _build_operation_all_of),
            Row("one-of-any-of", _operation_one_of_any_of_applies, _parse_combined_operation_schema),
            Row("object", _operation_object_applies, _build_operation_object),
            Row("inline-object-body", _operation_inline_object_body_applies, _build_operation_inline_object_body),
            Row("enum", _operation_enum_applies, _build_operation_enum),
            Row("ref", _operation_ref_applies, _build_operation_ref),
        ),
        Default("data-type", _parse_unmodeled_operation_schema),
    )

    def parse_request_body(
        self,
        name: str,
        request_body: RequestBodyObject,
        path: list[str],
    ) -> dict[str, DataType]:
        """Parse request body content into data types by media type."""
        data_types: dict[str, DataType] = {}
        for media_type, media_obj in request_body.content.items():
            media_schema = self._get_media_schema(media_obj)
            if media_schema is None:
                continue
            schema_path = self._media_schema_path([*path, media_type], from_item_schema=media_schema.from_item_schema)
            data_type = self._parse_schema_or_ref(
                name, media_schema.schema, schema_path, location=media_schema.location
            )
            data_types[media_type] = data_type
        return data_types

    def parse_responses(
        self,
        name: str,
        responses: dict[str | int, ReferenceObject | ResponseObject],
        path: list[str],
    ) -> dict[str | int, dict[str, DataType]]:
        """Parse response objects into data types by status code and content type."""
        data_types: defaultdict[str | int, dict[str, DataType]] = defaultdict(dict)
        for status_code, detail in responses.items():
            response_name = f"{name}{str(status_code).capitalize()}" if self.use_status_code_in_response_name else name

            if isinstance(detail, ReferenceObject):
                if not detail.ref:  # pragma: no cover
                    continue
                ref_model = self.get_ref_model(detail.ref)
                content = {k: self._validated_target(v, MediaObject) for k, v in ref_model.get("content", {}).items()}
            else:
                content = detail.content

            if self.allow_responses_without_content and not content:
                data_types[status_code]["application/json"] = DataType(type="None")

            for content_type, obj in content.items():
                response_path: list[str] = [*path, str(status_code), str(content_type)]
                media_schema = self._get_media_schema(obj)
                if media_schema is None:
                    continue
                schema_path = self._media_schema_path(response_path, from_item_schema=media_schema.from_item_schema)
                data_type = self._parse_schema_or_ref(
                    response_name, media_schema.schema, schema_path, location=media_schema.location
                )
                data_types[status_code][content_type] = data_type  # ty: ignore[invalid-assignment]

        return data_types

    @classmethod
    def parse_tags(
        cls,
        name: str,  # noqa: ARG003
        tags: list[str],
        path: list[str],  # noqa: ARG003
    ) -> list[str]:
        """Parse operation tags."""
        return tags

    def _get_model_name(self, path_name: str, method: str, suffix: str) -> str:
        target = self.target_python_version
        normalized = _operation_name_resolver(target).get_valid_name(path_name, ignore_snake_case_field=True)
        camel_path_name = snake_to_upper_camel(normalized, target_python_version=target)
        return f"{camel_path_name}{method.capitalize()}{suffix}"

    def parse_all_parameters(  # noqa: PLR0912, PLR0914, PLR0915
        self,
        name: str,
        parameters: list[ReferenceObject | ParameterObject],
        path: list[str],
        *,
        location: SchemaLocation | None = None,
    ) -> DataType | None:
        """Parse all operation parameters into a data model located at the operation object."""
        fields: list[DataModelFieldBase] = []
        field_schemas: list[tuple[JsonSchemaObject, list[str]]] = []
        exclude_field_names: set[str] = set()
        seen_parameter_names: set[str] = set()
        reference = self.model_resolver.add(path, name, class_name=True, unique=True)
        for parameter_ in parameters:
            parameter = self.resolve_object(parameter_, ParameterObject)
            match parameter.in_:
                case ParameterLocation.querystring if self.schema_features.querystring_parameter:
                    parameter_name = parameter.name or "querystring"
                case ParameterLocation.query | ParameterLocation.path:
                    if not (parameter_name := parameter.name):
                        continue
                case _:
                    continue

            if parameter.in_ == ParameterLocation.path and not self.include_path_parameters:
                continue

            if parameter_name in seen_parameter_names:
                msg = f"Parameter name '{parameter_name}' is used more than once."
                raise Exception(msg)  # noqa: TRY002
            seen_parameter_names.add(parameter_name)

            field_name, alias = self.model_resolver.get_valid_field_name_and_alias(
                field_name=parameter_name,
                excludes=exclude_field_names,
                model_type=self.field_name_model_type,
                class_name=name,
            )
            if parameter.schema_:
                param_schema = self._array_all_of_site_schema(parameter.schema_)
                if param_schema.has_ref_with_schema_keywords and not param_schema.is_ref_with_nullable_only:
                    param_schema = self._merge_ref_with_schema(param_schema)
                param_schema = self._read_false_all_of_values(param_schema)
                field_schemas.append((param_schema, [*path, name, parameter_name]))
                effective_required = parameter.required
                effective_default, effective_has_default, use_default_with_required = self._effective_default_state(
                    parameter_name,
                    param_schema.default,
                    has_default=param_schema.has_default,
                    required=effective_required,
                    class_name=reference.name,
                )
                fields.append(
                    self.get_object_field(
                        field_name=field_name,
                        field=param_schema,
                        field_type=self.parse_item(field_name, param_schema, [*path, name, parameter_name]),
                        original_field_name=parameter_name,
                        required=effective_required,
                        alias=alias,
                        effective_default=effective_default,
                        effective_has_default=effective_has_default,
                        use_default_with_required=use_default_with_required,
                        class_name=reference.name,
                    )
                )
            else:
                data_types: list[DataType] = []
                object_schema: JsonSchemaObject | None = None
                for (
                    media_type,
                    media_obj,
                ) in parameter.content.items():
                    schema_result = self._get_media_schema(media_obj)
                    if schema_result is None:
                        continue
                    schema_path = self._media_schema_path(
                        [*path, name, parameter_name, media_type],
                        from_item_schema=schema_result.from_item_schema,
                    )
                    object_schema = self._read_false_all_of_values(
                        self._array_all_of_site_schema(
                            _locate(
                                self._validate_schema_object(schema_result.schema, schema_path), schema_result.location
                            )
                            if isinstance(schema_result.schema, bool)
                            else self.resolve_object(schema_result.schema, JsonSchemaObject)
                        )
                    )
                    data_types.append(
                        self.parse_item(
                            field_name,
                            object_schema,
                            schema_path,
                        )
                    )
                    field_schemas.append((object_schema, schema_path))

                if not data_types:
                    continue
                if len(data_types) == 1:
                    data_type = data_types[0]
                else:
                    data_type = build_union(self.data_type, data_types)
                    # multiple data_type parse as non-constraints field
                    object_schema = None
                original_default = object_schema.default if object_schema else None
                original_has_default = object_schema.has_default if object_schema else False
                effective_required = parameter.required
                effective_default, effective_has_default, use_default_with_required = self._effective_default_state(
                    parameter_name,
                    original_default,
                    has_default=original_has_default,
                    required=effective_required,
                    class_name=reference.name,
                )
                fields.append(
                    self.get_object_field(
                        field_name=field_name,
                        field=object_schema,
                        required=effective_required,
                        field_type=data_type,
                        alias=alias,
                        original_field_name=parameter_name,
                        effective_default=effective_default,
                        effective_has_default=effective_has_default,
                        use_default_with_required=use_default_with_required,
                        class_name=reference.name,
                    )
                )

        if OpenAPIScope.Parameters in self.open_api_scopes and fields:
            if self._types_nullable_map_values:
                for field_schema, schema_path in field_schemas:
                    self.parse_ref(field_schema, schema_path)
            # Using _create_data_model from parent class JsonSchemaParser
            # This method automatically adds frozen=True for DataClass types
            model = self._create_data_model(
                fields=fields,
                reference=reference,
                custom_base_class=self._resolve_base_class(name),
                custom_template_dir=self.custom_template_dir,
                extra_template_data=self.extra_template_data,
                keyword_only=self.keyword_only,
                treat_dot_as_module=self.treat_dot_as_module,
                dataclass_arguments=self.dataclass_arguments,
            )
            self._graph_builder.register_model(model)
            self._record_schema_location(reference, location)
            if not self._types_nullable_map_values:
                self._parameter_models.append((model, tuple(self.model_resolver.current_root)))
            return self.data_type(reference=reference)

        return None

    def parse_operation(
        self,
        raw_operation: dict[str, Any],
        path: list[str],
        *,
        location: SchemaLocation | None = None,
    ) -> None:
        """Parse an OpenAPI operation including parameters, request body, and responses.

        ``location`` is the operation object in its document; the operation may be a copy that carries path-level
        parameters or the global security requirement.
        """
        operation = self._validated_object(raw_operation, Operation, path, location)
        path_name, method = path[-2:]
        if self.use_operation_id_as_name:
            if not operation.operationId:
                msg = (
                    f"All operations must have an operationId when --use_operation_id_as_name is set."
                    f"The following path was missing an operationId: {path_name}"
                )
                raise Error(msg)
            path_name = operation.operationId
            method = ""
        self.parse_all_parameters(
            self._get_model_name(
                path_name, method, suffix="Parameters" if self.include_path_parameters else "ParametersQuery"
            ),
            operation.parameters,
            [*path, "parameters"],
            location=location,
        )
        if operation.requestBody:
            if isinstance(operation.requestBody, ReferenceObject):
                ref_model = self.get_ref_model(operation.requestBody.ref)
                request_body = self._validated_target(ref_model, RequestBodyObject)
            else:
                request_body = operation.requestBody
            self.parse_request_body(
                name=self._get_model_name(path_name, method, suffix="Request"),
                request_body=request_body,
                path=[*path, "requestBody"],
            )
        self.parse_responses(
            name=self._get_model_name(path_name, method, suffix="Response"),
            responses=operation.responses,
            path=[*path, "responses"],
        )
        if OpenAPIScope.Tags in self.open_api_scopes:
            self.parse_tags(
                name=self._get_model_name(path_name, method, suffix="Tags"),
                tags=operation.tags,
                path=[*path, "tags"],
            )

    def parse_raw(self) -> None:
        """Parse OpenAPI specification including schemas, paths, and operations.

        Operations resolve references outside schema parsing, so models specialized for a dynamic scope
        there are parsed once every specification has been read.
        """
        self._discriminator_documents.clear()
        self._discriminator_schemas.clear()
        self._discriminator_subtypes.clear()
        self._parameter_models.clear()
        try:
            for source, path_parts in self._get_context_source_path_parts():
                if self.validation:
                    warn_deprecated("cli.validation", stacklevel=2)

                    if source.raw_data is not None and not source.text:
                        warn(
                            "Warning: Validation was skipped for dict input. "
                            "The --validation option only works with file or text input.\n",
                            stacklevel=2,
                        )
                    else:
                        try:
                            from prance import BaseParser  # noqa: PLC0415

                            BaseParser(
                                spec_string=source.text,
                                backend="openapi-spec-validator",
                                encoding=self.encoding,
                            )
                        except ImportError:  # pragma: no cover
                            warn(
                                "Warning: Validation was skipped for OpenAPI. "
                                "`prance` or `openapi-spec-validator` are not installed.\n"
                                "To use --validation option after datamodel-code-generator 0.24.0, "
                                "Please run `$pip install 'datamodel-code-generator[validation]'`.\n",
                                stacklevel=2,
                            )

                specification = self._load_source_dict(source)
                self._cache_source_ref_body(source, specification)
                self.raw_obj = specification
                self.raw_obj = specification = self._prepare_schema_resources(specification, path_parts)
                self._specification_documents.add(self._schema_resource_document(path_parts))
                with self.openapi_self_context(specification):
                    self._parse_specification(specification, path_parts)

            self._parse_dynamic_specializations()
            self._resolve_unparsed_json_pointer()
            if self._parameter_models:
                self._load_references_of_parameter_models()
            self._end_schema_parsing()
        finally:
            self._clear_inherited_field_caches()
            self._reset_local_source_cache()

    def _load_references_of_parameter_models(self) -> None:
        """Load the models parameter models use that nothing else loaded, following only the references types use.

        Without --use-typed-nullable-map-values, parameter schemas load no references, as in 0.83.0, which failed
        when a parameter used a model nothing else loaded.
        """
        roots = {"/".join(root): root for _, root in self._parameter_models}
        attempted: set[str] = set()
        while missing := self._missing_references_of_parameter_models() - attempted:
            attempted |= missing
            self._loads_only_used_references = True
            try:
                with self.model_resolver.current_base_path_context(Path()):
                    for path in sorted(missing):
                        if (root := roots.get(path.split("#", 1)[0])) is None:
                            self.resolve_ref(path)
                        else:
                            self.reserved_refs[root].add(path)
                self._resolve_unparsed_json_pointer()
            finally:
                self._loads_only_used_references = False

    def _missing_references_of_parameter_models(self) -> set[str]:
        """Return the paths of the models that parameter models use, directly or through others, but lack."""
        models = {model.path: model for model in self.results}
        pending = [model for model, _ in self._parameter_models]
        seen = {model.path for model in pending}
        missing: set[str] = set()
        while pending:
            model = pending.pop()
            for path in model.reference_classes.union(model.additional_properties_reference_classes) - seen:
                seen.add(path)
                if (used := models.get(path)) is None:
                    missing.add(path)
                else:
                    pending.append(used)
        return missing

    def _get_ref_body(self, resolved_ref: str) -> dict[str, YamlValue]:
        """Index discriminator metadata when the existing loader reads an external document."""
        raw = super()._get_ref_body(resolved_ref)
        if f"{resolved_ref}#" not in self._discriminator_documents:
            with self._inherited_ref_context(resolved_ref):
                self._collect_discriminator_document(raw)
        return raw

    def _collect_discriminator_schemas(self) -> None:
        """Collect discriminator metadata for the current input specification."""
        self._collect_discriminator_document(self.raw_obj)

    def _collect_discriminator_document(self, raw: dict[str, Any]) -> None:
        """Collect discriminator definitions once per document, preserving subtype order."""
        document = f"{'/'.join(self.model_resolver.current_root)}#"
        if document in self._discriminator_documents:
            return
        self._discriminator_documents.add(document)
        self._register_discriminator_schema("#", raw)
        schemas: dict[str, Any] = raw.get("components", {}).get("schemas", {})
        potential_subtypes: dict[str, list[str]] = {}

        for schema_name, schema in schemas.items():
            match schema:
                case bool():
                    continue
                case dict():
                    pass
                case _:
                    self._validate_member(
                        schema,
                        [*self.model_resolver.current_root, "#/components", "schemas", schema_name],
                        self._member_location(schemas, schema_name),
                    )
                    continue  # pragma: no cover - validation always raises for non-schema values
            self._register_discriminator_schema(f"#/components/schemas/{schema_name}", schema)

            all_of = schema.get("allOf")
            if all_of:
                refs = [ref for item in all_of if isinstance(item, dict) and (ref := item.get("$ref"))]
                if refs:
                    potential_subtypes[schema_name] = refs

        for schema_name, refs in potential_subtypes.items():
            for ref_in_allof in refs:
                resolved_ref = self._ref_path(ref_in_allof)
                # External parents may be registered after this child document is read.
                if resolved_ref in self._discriminator_schemas or resolved_ref.partition("#")[0] != document[:-1]:
                    self._discriminator_subtypes[resolved_ref].append(
                        self._schema_key(
                            schemas[schema_name],
                            f"#/components/schemas/{schema_name}",
                            "allOf",
                            RefOrigin.DISCRIMINATOR_SUBTYPE,
                        )
                    )

    def _register_discriminator_schema(self, ref: str, schema: dict[str, Any]) -> None:
        """Register the discriminator of a schema of a loaded document under its key, if no oneOf or anyOf lists types.

        ``ref`` names the schema from its own document.
        """
        discriminator = schema.get("discriminator")
        if discriminator and not schema.get("oneOf") and not schema.get("anyOf"):
            self._discriminator_schemas[
                self._schema_key(schema, ref, "discriminator", RefOrigin.DISCRIMINATOR_SCHEMA)
            ] = discriminator

    def _schema_key(self, schema: object, ref: str, keyword: str, origin: RefOrigin) -> str:
        """Return the key of a schema of a loaded document, with the edge of ``ref``, which names it from its document.

        The edge's source is the schema, so the key is the one ``ref`` resolves to in the schema's own document (its
        location, root `$id` and `$self`), the key the references to the schema get; a value the schema graph does
        not locate resolves ``ref`` in the current context.
        """
        derived = self._derived_ref(ref, self._raw_location(schema), keyword, ref, origin)
        return keep_resolved_ref(derived, self._ref_path(derived))
