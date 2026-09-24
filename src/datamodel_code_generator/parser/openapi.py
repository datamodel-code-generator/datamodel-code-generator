"""OpenAPI and Swagger specification parser.

Extends JsonSchemaParser to handle OpenAPI 2.0 (Swagger), 3.0, 3.1, and 3.2
specifications, including paths, operations, parameters, and request/response bodies.
"""

from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from pathlib import Path
from re import Pattern
from typing import TYPE_CHECKING, Any, ClassVar, Optional, TypeVar, Union, cast
from warnings import warn

from pydantic import Field, StrictBool, ValidationError
from typing_extensions import Unpack

from datamodel_code_generator import (
    Error,
    InputFileType,
    OpenAPIScope,
    snooper_to_methods,
)
from datamodel_code_generator._source import YamlValue
from datamodel_code_generator.deprecations import warn_deprecated
from datamodel_code_generator.enums import OpenAPIVersion, VersionMode
from datamodel_code_generator.parser.base import Result, get_special_path
from datamodel_code_generator.parser.jsonschema import (
    JsonSchemaObject,
    JsonSchemaParser,
    get_model_by_path,
)
from datamodel_code_generator.reference import FieldNameResolver, is_url, snake_to_upper_camel
from datamodel_code_generator.types import (
    DataType,
    EmptyDataType,
)
from datamodel_code_generator.util import BaseModel

if TYPE_CHECKING:
    from collections.abc import Generator
    from urllib.parse import ParseResult

    from datamodel_code_generator._types import OpenAPIParserConfigDict
    from datamodel_code_generator.config import OpenAPIParserConfig
    from datamodel_code_generator.model import DataModelFieldBase
    from datamodel_code_generator.parser.schema_version import OpenAPISchemaFeatures


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


MediaSchema = JsonSchemaObject | ReferenceObject | bool
RawSchema = dict[str, YamlValue] | StrictBool


@dataclass(frozen=True)
class MediaSchemaSource:
    """Schema extracted from a parsed OpenAPI media type object."""

    schema: MediaSchema
    from_item_schema: bool = False


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


_FIELD_NAME_RESOLVER = FieldNameResolver()


@snooper_to_methods()
class OpenAPIParser(JsonSchemaParser):
    """Parser for OpenAPI 2.0/3.0/3.1/3.2 and Swagger specifications."""

    SCHEMA_PATHS: ClassVar[list[str]] = ["#/components/schemas"]
    _input_file_type: ClassVar[InputFileType] = InputFileType.OpenAPI
    _non_dict_source_is_invalid: ClassVar[bool] = True
    config: OpenAPIParserConfig

    @cached_property
    def schema_features(self) -> OpenAPISchemaFeatures:
        """Get schema features based on config or detected OpenAPI version."""
        # OpenAPI parses a single document context, so caching is intentional.
        # AsyncAPI overrides this with a live property because it swaps raw_obj while resolving refs.
        from datamodel_code_generator.parser.schema_version import (  # noqa: PLC0415
            OpenAPISchemaFeatures,
            detect_openapi_version,
        )

        config_version = getattr(self.config, "openapi_version", None)
        if config_version is not None and config_version != OpenAPIVersion.Auto:
            return OpenAPISchemaFeatures.from_openapi_version(config_version)
        version = detect_openapi_version(self.raw_obj) if self.raw_obj else OpenAPIVersion.Auto
        return OpenAPISchemaFeatures.from_openapi_version(version)

    _config_class_name: ClassVar[str] = "OpenAPIParserConfig"
    _supports_api_scope: ClassVar[bool] = False

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
            msg = "OpenAPIScope.Api requires ApiOpenAPIParser"
            raise Error(msg)
        self.include_path_parameters: bool = self.config.include_path_parameters
        self.use_status_code_in_response_name: bool = self.config.use_status_code_in_response_name
        self.openapi_include_paths: list[str] | None = self.config.openapi_include_paths
        self.openapi_include_info_version: bool = self.config.openapi_include_info_version
        self.openapi_info_version: str | None = None
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

    def get_ref_model(self, ref: str) -> dict[str, Any]:
        """Resolve a reference to its model definition."""
        ref_file, ref_path = self.model_resolver.resolve_ref(ref).split("#", 1)
        ref_body = self._get_ref_body(ref_file) if ref_file else self.raw_obj
        return get_model_by_path(ref_body, ref_path.split("/")[1:])

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
            for obj_name, raw_obj in schemas.items():
                self.parse_raw_obj(
                    obj_name,
                    raw_obj,
                    [*path_parts, "#/components", "schemas", obj_name],
                )
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
                    media_path = [
                        *path_parts,
                        "#/components",
                        "requestBodies",
                        body_name,
                        "content",
                        media_type,
                    ]
                    self.parse_raw_obj(
                        body_name,
                        media_schema.schema,
                        self._media_schema_path(
                            media_path if media_schema.from_item_schema else [*media_path, "schema"],
                            from_item_schema=media_schema.from_item_schema,
                        ),
                    )

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

    def _get_data_type(self, obj: JsonSchemaObject, *, localize_constraints: bool) -> DataType:
        """Get data type from JSON schema object, handling OpenAPI nullable semantics.

        Uses schema_features.nullable_keyword to handle version differences:
        - OpenAPI 3.0: nullable: true is valid, convert to type array when strict_nullable
        - OpenAPI 3.1+: nullable is deprecated, use type: ["string", "null"] instead
        """
        if obj.nullable:
            if self.schema_features.nullable_keyword:
                # OpenAPI 3.0: nullable: true is the standard way
                if self.strict_nullable and isinstance(obj.type, str):
                    obj.type = [obj.type, "null"]
            else:
                # OpenAPI 3.1+: nullable is deprecated, still process but warn in Strict mode
                if self.config.schema_version_mode == VersionMode.Strict:
                    warn_deprecated("schema.openapi-nullable", stacklevel=2)
                # Still convert to type array for compatibility
                if self.strict_nullable and isinstance(obj.type, str):
                    obj.type = [obj.type, "null"]

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
                    self._normalize_discriminator_mapping_ref(v) for v in mapping.values() if isinstance(v, str)
                ]
                if subtypes and ref.partition("#")[0] == current_document:
                    return self.data_type(data_types=[self._get_discriminator_subtype_data_type(v) for v in subtypes])
                subtypes = [self._resolve_inherited_child_ref(v, ref) for v in subtypes]
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
                local_ref = f"#{fragment}"
                data_types.append(self._get_discriminator_subtype_data_type(local_ref))
        return self.data_type(data_types=data_types)

    def get_ref_data_type(self, ref: str) -> DataType:
        """Get data type for a reference, handling discriminator polymorphism."""
        if (
            self._discriminator_schemas
            and (resolved_ref := self.model_resolver.resolve_ref(ref)) in self._discriminator_schemas
        ):
            return self._get_discriminator_union_type(resolved_ref) or super().get_ref_data_type(ref)
        document_count = len(self._discriminator_documents)
        data_type = super().get_ref_data_type(ref)
        if (
            len(self._discriminator_documents) != document_count
            and self._discriminator_schemas
            and (resolved_ref := self.model_resolver.resolve_ref(ref)) in self._discriminator_schemas
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
            field = properties.get(field_obj.original_name)  # ty: ignore[invalid-argument-type]

            if (
                isinstance(field, JsonSchemaObject)
                and field.ref
                and self._discriminator_schemas
                and (
                    discriminator := self._discriminator_schemas.get(
                        resolved_ref := self.model_resolver.resolve_ref(field.ref)
                    )
                )
            ):
                new_field_type = self._get_discriminator_union_type(resolved_ref)
                if new_field_type is None:
                    result_fields.append(field_obj)
                    continue
                normalized_discriminator = self._normalize_discriminator(discriminator)
                field_obj = self.data_model_field_type(**{  # noqa: PLW2901  # ty: ignore[invalid-argument-type]
                    **field_obj.__dict__,
                    "data_type": new_field_type,
                    "extras": {**field_obj.extras, "discriminator": normalized_discriminator},
                })
            result_fields.append(field_obj)

        return result_fields

    def resolve_object(self, obj: ReferenceObject | BaseModelT, object_type: type[BaseModelT]) -> BaseModelT:
        """Resolve a reference object to its actual type or return the object as-is."""
        if isinstance(obj, ReferenceObject):
            ref_obj = self.get_ref_model(obj.ref)
            return object_type.model_validate(ref_obj)
        return obj

    def _parse_schema_or_ref(
        self,
        name: str,
        schema: MediaSchema,
        path: list[str],
    ) -> DataType:
        """Parse a schema object or resolve a reference to get DataType."""
        if isinstance(schema, bool):
            return self.parse_schema(name, self._validate_schema_object(schema, path), path)
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
        """Return the schema represented by an OpenAPI media type object."""
        if media_obj.schema_ is not None:
            return MediaSchemaSource(media_obj.schema_)
        if media_obj.itemSchema is None or not self.schema_features.media_item_schema:
            return None
        return MediaSchemaSource(self._array_schema_from_media_item_schema(media_obj.itemSchema), from_item_schema=True)

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
            return RawMediaSchemaSource(raw_media_obj.schema_)
        if "itemSchema" not in raw_media_obj.model_fields_set or not self.schema_features.media_item_schema:
            return None
        if raw_media_obj.itemSchema is None:
            return None
        return RawMediaSchemaSource({"type": "array", "items": raw_media_obj.itemSchema}, from_item_schema=True)

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
        for item_name, methods_ in items.items():
            if apply_path_filter and not self._matches_path_pattern(item_name):
                continue
            item_ref = methods_.get("$ref")
            if item_ref:
                methods = self.get_ref_model(item_ref)
                # Extract base path from reference for external file resolution
                resolved_ref = self.model_resolver.resolve_ref(item_ref)
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
                    self.parse_operation(operation, [*path, operation_name])

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
        """Parse a JSON schema object into a data type."""
        if obj.is_array:
            data_type = self.parse_array(name, obj, [*path, name])
        elif obj.allOf:  # pragma: no cover
            data_type = self.parse_all_of(name, obj, path)
        elif obj.oneOf or obj.anyOf:  # pragma: no cover
            data_type = self.parse_root_type(name, obj, path)
            if isinstance(data_type, EmptyDataType) and obj.properties:
                self.parse_object(name, obj, path)
        elif obj.is_object:
            data_type = self.parse_object(name, obj, path)
        elif obj.enum and not self.ignore_enum_constraints:  # pragma: no cover
            data_type = self.parse_enum(name, obj, path)
        elif obj.ref:  # pragma: no cover
            data_type = self.get_ref_data_type(obj.ref)
        else:
            data_type = self.get_data_type(obj)
        self.parse_ref(obj, path)
        return data_type

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
            data_type = self._parse_schema_or_ref(name, media_schema.schema, schema_path)
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
                content = {k: MediaObject.model_validate(v) for k, v in ref_model.get("content", {}).items()}
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
                data_type = self._parse_schema_or_ref(response_name, media_schema.schema, schema_path)
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

    _field_name_resolver: FieldNameResolver = _FIELD_NAME_RESOLVER

    @classmethod
    def _get_model_name(cls, path_name: str, method: str, suffix: str) -> str:
        normalized = cls._field_name_resolver.get_valid_name(path_name, ignore_snake_case_field=True)
        camel_path_name = snake_to_upper_camel(normalized)
        return f"{camel_path_name}{method.capitalize()}{suffix}"

    def parse_all_parameters(  # noqa: PLR0912, PLR0914, PLR0915
        self,
        name: str,
        parameters: list[ReferenceObject | ParameterObject],
        path: list[str],
    ) -> DataType | None:
        """Parse all operation parameters into a data model."""
        fields: list[DataModelFieldBase] = []
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
                param_schema = parameter.schema_
                if param_schema.has_ref_with_schema_keywords and not param_schema.is_ref_with_nullable_only:
                    param_schema = self._merge_ref_with_schema(param_schema)
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
                    object_schema = (
                        self._validate_schema_object(schema_result.schema, schema_path)
                        if isinstance(schema_result.schema, bool)
                        else self.resolve_object(schema_result.schema, JsonSchemaObject)
                    )
                    data_types.append(
                        self.parse_item(
                            field_name,
                            object_schema,
                            schema_path,
                        )
                    )

                if not data_types:
                    continue
                if len(data_types) == 1:
                    data_type = data_types[0]
                else:
                    data_type = self.data_type(data_types=data_types)
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
            # Using _create_data_model from parent class JsonSchemaParser
            # This method automatically adds frozen=True for DataClass types
            self.generation_store.register_model(
                self._create_data_model(
                    fields=fields,
                    reference=reference,
                    custom_base_class=self._resolve_base_class(name),
                    custom_template_dir=self.custom_template_dir,
                    extra_template_data=self.extra_template_data,
                    keyword_only=self.keyword_only,
                    treat_dot_as_module=self.treat_dot_as_module,
                    dataclass_arguments=self.dataclass_arguments,
                )
            )
            return self.data_type(reference=reference)

        return None

    def parse_operation(
        self,
        raw_operation: dict[str, Any],
        path: list[str],
    ) -> None:
        """Parse an OpenAPI operation including parameters, request body, and responses."""
        operation = Operation.model_validate(raw_operation)
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
        )
        if operation.requestBody:
            if isinstance(operation.requestBody, ReferenceObject):
                ref_model = self.get_ref_model(operation.requestBody.ref)
                request_body = RequestBodyObject.model_validate(ref_model)
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
                with self.openapi_self_context(specification):
                    self._parse_specification(specification, path_parts)

            self._parse_dynamic_specializations()
            self._resolve_unparsed_json_pointer()
            self._generate_forced_base_models()
        finally:
            self._clear_inherited_field_caches()
            self._reset_local_source_cache()

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
                    self._validate_schema_object(
                        schema,
                        [*self.model_resolver.current_root, "#/components", "schemas", schema_name],
                    )
                    continue  # pragma: no cover - validation always raises for non-schema values
            self._register_discriminator_schema(f"#/components/schemas/{schema_name}", schema)

            all_of = schema.get("allOf")
            if all_of:
                refs = [item.get("$ref") for item in all_of if item.get("$ref")]
                if refs:
                    potential_subtypes[schema_name] = refs

        for schema_name, refs in potential_subtypes.items():
            for ref_in_allof in refs:
                resolved_ref = (
                    f"{document}{ref_in_allof[1:]}"
                    if ref_in_allof.startswith("#")
                    else self.model_resolver.resolve_ref(ref_in_allof)
                )
                # External parents may be registered after this child document is read.
                if resolved_ref in self._discriminator_schemas or resolved_ref.partition("#")[0] != document[:-1]:
                    subtype_ref = self.model_resolver.resolve_ref(f"#/components/schemas/{schema_name}")
                    self._discriminator_subtypes[resolved_ref].append(subtype_ref)

    def _register_discriminator_schema(self, ref: str, schema: dict[str, Any]) -> None:
        discriminator = schema.get("discriminator")
        if discriminator and not schema.get("oneOf") and not schema.get("anyOf"):
            self._discriminator_schemas[self.model_resolver.resolve_ref(ref)] = discriminator
