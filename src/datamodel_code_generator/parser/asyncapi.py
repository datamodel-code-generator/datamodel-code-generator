"""AsyncAPI specification parser.

Extracts AsyncAPI message schemas and delegates Schema Object handling to the
OpenAPI/JSON Schema parser stack.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pydantic import Field, StrictStr, ValidationError
from typing_extensions import Unpack, override

from datamodel_code_generator import Error, InputFileType, SchemaParseError, snooper_to_methods
from datamodel_code_generator._document_reader import as_mapping, string_keys
from datamodel_code_generator._json_pointer import escape_token
from datamodel_code_generator._name_words import (
    ASCII_WORD as NAME_PART_PATTERN,  # noqa: F401  # Keep the public asyncapi name.
)
from datamodel_code_generator._name_words import pascal_words
from datamodel_code_generator._records import record
from datamodel_code_generator._url_redaction import redact_path
from datamodel_code_generator.deprecations import warn_deprecated
from datamodel_code_generator.enums import AsyncAPIVersion
from datamodel_code_generator.parser.jsonschema import (
    JsonSchemaParser,
    _named_path,
    _VersionSource,
    get_model_by_path,
    unescape_json_pointer_segment,
)
from datamodel_code_generator.parser.openapi import OpenAPIParser
from datamodel_code_generator.parser.schema_version import OpenAPISchemaFeatures, detect_asyncapi_version
from datamodel_code_generator.reference import is_url
from datamodel_code_generator.util import BaseModel, create_module_getattr

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator
    from urllib.parse import ParseResult

    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator._types import AsyncAPIParserConfigDict
    from datamodel_code_generator.config import AsyncAPIParserConfig
    from datamodel_code_generator.reference import SchemaLocation


OPERATION_NAMES: tuple[str, ...] = ("publish", "subscribe")
BINDING_SCHEMA_FIELD_NAMES: frozenset[str] = frozenset({"headers", "query", "key", "groupId", "clientId"})
ASYNCAPI_SCHEMA_FORMATS: frozenset[str] = frozenset({
    "application/vnd.aai.asyncapi",
    "application/vnd.aai.asyncapi+json",
    "application/vnd.aai.asyncapi+yaml",
})
JSON_SCHEMA_FORMATS: frozenset[str] = frozenset({
    "application/schema+json",
    "application/schema+yaml",
})
OPENAPI_SCHEMA_FORMATS: frozenset[str] = frozenset({
    "application/vnd.oai.openapi",
    "application/vnd.oai.openapi+json",
    "application/vnd.oai.openapi+yaml",
})
AVRO_SCHEMA_FORMATS: frozenset[str] = frozenset({
    "application/vnd.apache.avro",
    "application/vnd.apache.avro+json",
    "application/vnd.apache.avro+yaml",
})
PROTOBUF_SCHEMA_FORMATS: frozenset[str] = frozenset({"application/vnd.google.protobuf"})
XML_SCHEMA_FORMATS: frozenset[str] = frozenset({
    "application/xml",
    "application/xml+schema",
    "application/xml-schema",
    "application/xsd+xml",
    "text/xml",
})
RAML_SCHEMA_FORMATS: frozenset[str] = frozenset({"application/raml+yaml"})
SCHEMA_FORMAT_KIND_BY_MEDIA_TYPE: dict[str, str] = {
    **dict.fromkeys(ASYNCAPI_SCHEMA_FORMATS, "asyncapi"),
    **dict.fromkeys(JSON_SCHEMA_FORMATS, "jsonschema"),
    **dict.fromkeys(OPENAPI_SCHEMA_FORMATS, "openapi"),
    **dict.fromkeys(AVRO_SCHEMA_FORMATS, "avro"),
    **dict.fromkeys(PROTOBUF_SCHEMA_FORMATS, "protobuf"),
    **dict.fromkeys(XML_SCHEMA_FORMATS, "xmlschema"),
}
INTERNAL_SCHEMA_CONTAINER = "x-datamodel-code-generator-asyncapi-schemas"
__getattr__ = create_module_getattr(
    __name__,
    {
        "convert_avro_schema_data": ("datamodel_code_generator.parser.avro", "convert_avro_schema_data"),
        "convert_protobuf_schema_data": (
            "datamodel_code_generator.parser.protobuf",
            "convert_protobuf_schema_data",
        ),
        "convert_xml_schema_data": ("datamodel_code_generator.parser.xmlschema", "convert_xml_schema_data"),
    },
)


@record(slots=False)
class AsyncAPIContext:
    """Resolver context for an AsyncAPI document."""

    raw_obj: dict[str, Any]
    root_parts: list[str]
    base_path: Path | None
    base_url: str | None


@record(slots=False)
class AsyncAPIResolvedRef:
    """Resolved AsyncAPI reference with parser path and document context."""

    value: YamlValue
    path: list[str]
    context: AsyncAPIContext


@record(slots=False)
class AsyncAPISchema:
    """AsyncAPI schema candidate with the context needed to parse it."""

    name: str
    raw_schema: YamlValue
    path: list[str]
    context: AsyncAPIContext
    parse_as_file: bool = False
    location: SchemaLocation | None = None


class MultiFormatSchemaObject(BaseModel):
    """AsyncAPI Multi Format Schema Object fields used by the parser."""

    schema_: Any = Field(alias="schema")
    schema_format: StrictStr | None = Field(default=None, alias="schemaFormat")


def _iter_mapping_items(value: Any) -> list[tuple[str, Any]]:
    return list(as_mapping(value).items())


def _iter_trait_items(traits: Any, path: list[str]) -> Iterator[tuple[dict[str, Any], list[str]]]:
    is_trait_list = isinstance(traits, list)
    trait_items = traits if is_trait_list else [traits]
    for index, trait in enumerate(trait_items):
        if not isinstance(trait, dict):
            continue
        yield trait, [*path, str(index)] if is_trait_list else path


def _path_to_string(path: list[str]) -> str:
    """Return ``path`` as a message names it; a key on it may be no string, such as a YAML key ``1``."""
    return redact_path(map(str, path))


def _make_model_name(*parts: object) -> str:
    return pascal_words(*map(str, parts)) or "AsyncAPIModel"


def _member_schemas(
    holder: dict[str, Any],
    key: str,
    walker: Callable[..., list[AsyncAPISchema]],
    name: str,
    path: list[str],
    *rest: Any,
) -> list[AsyncAPISchema]:
    """Return the schemas ``walker`` finds in member ``key`` of ``holder`` at ``path``; none where it lacks one."""
    return walker(holder[key], name, [*path, key], *rest) if key in holder else []


def _map_schemas(
    entries: Any, walker: Callable[..., list[AsyncAPISchema]], name: str, path: list[str], *rest: Any, suffix: str = ""
) -> list[AsyncAPISchema]:
    """Return the schemas ``walker`` finds in each entry of the map ``entries`` at ``path``, named after its key."""
    return [
        schema
        for key, value in _iter_mapping_items(entries)
        for schema in walker(value, _make_model_name(name, key, suffix), [*path, key], *rest)
    ]


def _message_list_schemas(
    messages: list[Any], walker: Callable[..., list[AsyncAPISchema]], name: str, path: list[str], *rest: Any
) -> list[AsyncAPISchema]:
    """Return the schemas ``walker`` finds in each message of the list ``messages`` at ``path``, named by number."""
    return [
        schema
        for index, message in enumerate(messages, start=1)
        for schema in walker(message, _make_model_name(name, "Message", index), [*path, str(index - 1)], *rest)
    ]


def _escape_context_path_part(part: str) -> str:
    return escape_token(part.removeprefix("#/"))


def _schema_format_media_type(schema_format: str) -> str:
    return schema_format.split(";", maxsplit=1)[0].strip().lower()


def _is_multi_format_schema_object_candidate(raw_schema: YamlValue) -> bool:
    if not isinstance(raw_schema, dict):
        return False
    if "schemaFormat" in raw_schema:
        return True
    fixed_keys = {key for key in raw_schema if isinstance(key, str) and not key.startswith("x-")}
    return fixed_keys == {"schema"}


def _parse_multi_format_schema_object(
    raw_schema: YamlValue,
    path: list[str],
) -> MultiFormatSchemaObject | None:
    if not _is_multi_format_schema_object_candidate(raw_schema):
        return None
    try:
        return MultiFormatSchemaObject.model_validate(raw_schema)
    except ValidationError as error:
        error_types_by_location = {
            tuple(str(part) for part in detail.get("loc", ())): detail.get("type") for detail in error.errors()
        }
        if error_types_by_location.get(("schema",)) == "missing":
            msg = f"AsyncAPI Multi Format Schema Object requires a schema field at {_path_to_string(path)}"
            raise Error(msg) from error
        msg = f"AsyncAPI schemaFormat must be a string at {_path_to_string(path)}"
        raise Error(msg) from error


def _unsupported_schema_format_error(schema_format: str, path: list[str]) -> Error:
    msg = (
        f"Unsupported AsyncAPI schemaFormat {schema_format!r} at {_path_to_string(path)}. "
        "Supported embedded schema formats are AsyncAPI Schema Object, JSON Schema, "
        "OpenAPI Schema Object, Avro, Protocol Buffers, and XML Schema. RAML and custom schema "
        "formats are not supported inside AsyncAPI documents."
    )
    return Error(msg)


def _schema_format_kind(schema_format: str | None, path: list[str]) -> str:
    if schema_format is None:
        return "asyncapi"
    media_type = _schema_format_media_type(schema_format)
    if kind := SCHEMA_FORMAT_KIND_BY_MEDIA_TYPE.get(media_type):
        return kind
    raise _unsupported_schema_format_error(schema_format, path)


def _move_json_schema_definitions_to_components(
    schema: dict[str, YamlValue],
    *,
    ref_prefix: str = "#/components/schemas",
) -> dict[str, YamlValue]:
    if isinstance(definitions := schema.pop("definitions", None), dict):
        schema["components"] = {"schemas": definitions}

    def update_refs(value: YamlValue) -> None:
        match value:
            case dict():
                if (ref := value.get("$ref")) and isinstance(ref, str) and ref.startswith("#/definitions/"):
                    value["$ref"] = f"{ref_prefix}/{ref.removeprefix('#/definitions/')}"
                else:
                    for item in value.values():
                        update_refs(item)
            case list():
                for item in value:
                    update_refs(item)

    update_refs(schema)
    return schema


@snooper_to_methods()
class AsyncAPIParser(OpenAPIParser):
    """Parser for AsyncAPI 2.x and 3.x documents."""

    _config_class_name: ClassVar[str] = "AsyncAPIParserConfig"
    _input_file_type: ClassVar[InputFileType] = InputFileType.AsyncAPI
    _api_scope_error: ClassVar[str] = (
        "The 'api' scope of --openapi-scopes is not available for AsyncAPI input, "
        "which generates models from every location that holds a schema"
    )
    _named_entry_maps: ClassVar[frozenset[str]] = JsonSchemaParser._named_entry_maps  # ruff: ignore[private-member-access]

    def __init__(
        self,
        source: str | Path | list[Path] | ParseResult,
        *,
        config: AsyncAPIParserConfig | None = None,
        **options: Unpack[AsyncAPIParserConfigDict],
    ) -> None:
        """Initialize the AsyncAPI parser."""
        super().__init__(source=source, config=config, **options)

    _version_source: ClassVar[_VersionSource] = _VersionSource(
        "asyncapi_version", AsyncAPIVersion.Auto, detect_asyncapi_version, OpenAPISchemaFeatures.from_asyncapi_version
    )

    @property
    def schema_features(self) -> OpenAPISchemaFeatures:
        """Get Schema Object features based on config or detected AsyncAPI version."""
        # Keep this uncached because AsyncAPI reference parsing swaps raw_obj between document contexts.
        return cast("OpenAPISchemaFeatures", self._schema_features_for(self.raw_obj))

    def _collect_discriminator_schemas(self) -> None:
        """Collect discriminator metadata from component schemas."""
        self._discriminator_documents.clear()
        self._discriminator_schemas.clear()
        self._discriminator_subtypes.clear()
        self._collect_discriminator_document(self.raw_obj)

    def _collect_discriminator_document(self, raw: dict[str, Any]) -> None:
        """Retain AsyncAPI's component-only policy when reference loading indexes documents."""
        self._discriminator_documents.add(f"{'/'.join(self.model_resolver.current_root)}#")
        components = raw.get("components", {})
        if not isinstance(components, dict):
            msg = "`components` must be an object"
            raise SchemaParseError(
                msg,
                _named_path(
                    self._member_location(raw, "components"),
                    [*self.model_resolver.current_root, "components"],
                ),
            )
        schemas = components.get("schemas", {})
        if not isinstance(schemas, dict):
            return
        for schema_name, schema in schemas.items():
            if not isinstance(schema, dict):
                continue
            self._register_discriminator_schema(f"#/components/schemas/{schema_name}", schema)

    @override
    def _index_document(self, raw: dict[str, Any], path_parts: list[str]) -> dict[str, Any]:
        """Return a loaded document as a located tree whose root the schema graph owns, for converted payloads."""
        return self._graph.mount_root(super()._index_document(raw, path_parts))

    def _current_asyncapi_context(self) -> AsyncAPIContext:
        return AsyncAPIContext(
            raw_obj=self.raw_obj,
            root_parts=list(self.model_resolver.current_root),
            base_path=self.model_resolver.current_base_path,
            base_url=self.model_resolver.base_url,
        )

    @contextmanager
    def _asyncapi_context(self, context: AsyncAPIContext) -> Generator[None]:
        previous_raw_obj = self.raw_obj
        self.raw_obj = context.raw_obj
        try:
            with (
                self.model_resolver.current_base_path_context(context.base_path),
                self.model_resolver.base_url_context(context.base_url),
                self.model_resolver.current_root_context(context.root_parts),
            ):
                yield
        finally:
            self.raw_obj = previous_raw_obj

    def _schema(  # noqa: PLR0913
        self,
        name: str,
        raw_schema: YamlValue,
        path: list[str],
        context: AsyncAPIContext | None = None,
        *,
        parse_as_file: bool = False,
        location: SchemaLocation | None = None,
    ) -> AsyncAPISchema:
        return AsyncAPISchema(
            name=name,
            raw_schema=raw_schema,
            path=path,
            context=context or self._current_asyncapi_context(),
            parse_as_file=parse_as_file,
            location=location,
        )

    def _converted_schema_root_parts(self, path: list[str]) -> list[str]:
        """Return the root parts of the document of its own in which a converted schema at ``path`` is parsed."""
        current_root_parts = self._current_asyncapi_context().root_parts
        return [
            *current_root_parts,
            "__asyncapi_schemas__",
            *(_escape_context_path_part(part) for part in path[len(current_root_parts) :]),
        ]

    def _iter_converted_schema_schemas(
        self,
        name: str,
        converted_schema: dict[str, YamlValue],
        path: list[str],
        *,
        include_root_schema: bool = False,
        location: SchemaLocation | None = None,
    ) -> list[AsyncAPISchema]:
        """Mount a converted payload schema in the internal container of its document, located where the payload is.

        The container is under the root the schema graph owns for the document, so the document is not written.
        """
        context = self._current_asyncapi_context()
        converted_schema_key = "_".join(_escape_context_path_part(part) for part in path) or name
        ref_prefix = f"#/{INTERNAL_SCHEMA_CONTAINER}/{converted_schema_key}/components/schemas"
        converted_schema = _move_json_schema_definitions_to_components(converted_schema, ref_prefix=ref_prefix)
        converted_schema.setdefault("title", name)
        converted_schema = self._mount_converted_schema(
            converted_schema, location, f"/{INTERNAL_SCHEMA_CONTAINER}/{converted_schema_key}"
        )
        self._graph.mount_in(context.raw_obj, INTERNAL_SCHEMA_CONTAINER, converted_schema_key, converted_schema)
        root_path = [*context.root_parts, f"#/{INTERNAL_SCHEMA_CONTAINER}", converted_schema_key]
        schemas = [self._schema(name, converted_schema, root_path, context)] if include_root_schema else []
        schemas.extend(
            self._schema(
                schema_name,
                component_schema,
                [*root_path, "components", "schemas", schema_name],
                context,
                location=location and location.child("components", "schemas", schema_name),
            )
            for schema_name, component_schema in _iter_mapping_items(
                cast("dict[str, YamlValue]", converted_schema.get("components", {})).get("schemas", {})
            )
            if isinstance(component_schema, (dict, bool))
        )
        return schemas

    def _iter_schema_format_schemas(
        self,
        name: str,
        raw_schema: YamlValue,
        path: list[str],
        inherited_schema_format: str | None = None,
        *,
        location: SchemaLocation | None = None,
    ) -> list[AsyncAPISchema]:
        """Return the schemas of a payload, header, parameter or binding value located at ``location``.

        A schema in another format is converted to JSON Schema, which is mounted at the value's location. A key on
        ``path`` that is no string, such as a YAML key ``1``, is an error.
        """
        string_keys(path, "name", lambda _: _named_path(location, list(map(str, path))))
        schema_format = inherited_schema_format
        if multi_format_schema := _parse_multi_format_schema_object(raw_schema, path):
            schema_format = multi_format_schema.schema_format or schema_format
            raw_schema = multi_format_schema.schema_
            path = [*path, "schema"]
            location = location and location.child("schema")

        match _schema_format_kind(schema_format, path):
            case "asyncapi" | "jsonschema" | "openapi":
                if not isinstance(raw_schema, (dict, bool)):
                    msg = (
                        f"AsyncAPI schemaFormat {schema_format or 'default AsyncAPI schema'!r} "
                        f"requires a schema object at {_path_to_string(path)}"
                    )
                    raise Error(msg)
                return [self._schema(name, raw_schema, path, location=location)]
            case "avro":
                from datamodel_code_generator.parser.avro import convert_avro_schema_data  # noqa: PLC0415

                converted_schema = convert_avro_schema_data(raw_schema)
                converted_schema.setdefault("title", name)
                home = self.model_resolver.join_path(tuple(self._converted_schema_root_parts(path))).partition("#")[0]
                converted_schema = self._mount_converted_schema(converted_schema, location, home=home)
                context = replace(
                    self._current_asyncapi_context(),
                    raw_obj=converted_schema,
                    root_parts=self._converted_schema_root_parts(path),
                )
                self.remote_object_cache[str((self.base_path / home).resolve())] = converted_schema
                return [self._schema(name, converted_schema, context.root_parts, context, parse_as_file=True)]
            case "protobuf":
                from datamodel_code_generator.parser.protobuf import _convert_protobuf_schema_data  # noqa: PLC0415

                context = self._current_asyncapi_context()
                return self._iter_converted_schema_schemas(
                    name,
                    _convert_protobuf_schema_data(
                        raw_schema,
                        base_path=context.base_path,
                        encoding=self.encoding,
                        source_safe_non_finite=True,
                    ),
                    path,
                    location=location,
                )
            case "xmlschema":
                from datamodel_code_generator.parser.xmlschema import _convert_xml_schema_data  # noqa: PLC0415

                context = self._current_asyncapi_context()
                return self._iter_converted_schema_schemas(
                    name,
                    _convert_xml_schema_data(
                        raw_schema,
                        base_path=context.base_path,
                        encoding=self.encoding,
                        source_safe_non_finite=True,
                    ),
                    path,
                    include_root_schema=True,
                    location=location,
                )
        raise _unsupported_schema_format_error(schema_format or "", path)  # pragma: no cover

    def _resolve_ref_object(
        self,
        value: Any,
        seen_paths: set[tuple[str, ...]],
    ) -> AsyncAPIResolvedRef | None:
        if not isinstance(value, dict) or not isinstance(ref := value.get("$ref"), str):
            return None
        resolved = self._resolve_asyncapi_ref(self._derived_ref(ref, self._raw_location(value), "$ref", ref))
        return None if tuple(resolved.path) in seen_paths else resolved

    def _recurse_into_ref(
        self,
        value: Any,
        name: str,
        seen_paths: set[tuple[str, ...]] | None,
        recurse: Callable[[Any, str, list[str], set[tuple[str, ...]]], list[AsyncAPISchema]],
        *,
        rename: bool = True,
    ) -> list[AsyncAPISchema] | None:
        """Return ref recursion results; None means no ref or an already-seen ref."""
        seen_paths = seen_paths or set()
        if (resolved := self._resolve_ref_object(value, seen_paths)) is None:
            return None
        resolved_path = resolved.path
        ref_name = resolved_path[-1] if resolved_path else name
        with self._asyncapi_context(resolved.context):
            return recurse(
                resolved.value,
                _make_model_name(ref_name) if rename else name,
                resolved_path,
                seen_paths | {tuple(resolved_path)},
            )

    def _follow(
        self,
        value: Any,
        name: str,
        seen_paths: set[tuple[str, ...]] | None,
        walker: Callable[[Any, str, list[str], set[tuple[str, ...]]], list[AsyncAPISchema]],
        *,
        rename: bool = True,
    ) -> list[AsyncAPISchema] | None:
        """Return no schemas for a value that is no object, those of the target of a new reference, else None."""
        if not isinstance(value, dict):
            return []
        return self._recurse_into_ref(value, name, seen_paths, walker, rename=rename)

    def _member_format_schemas(
        self,
        holder: dict[str, Any],
        key: str,
        name: str,
        path: list[str],
        schema_format: str | None = None,
    ) -> list[AsyncAPISchema]:
        """Return the schemas of the schema in member ``key`` of ``holder`` at ``path``; none where it lacks one."""
        if key not in holder:
            return []
        location = self._member_location(holder, key)
        return self._iter_schema_format_schemas(name, holder[key], [*path, key], schema_format, location=location)

    def _resolve_asyncapi_ref(self, ref: str) -> AsyncAPIResolvedRef:
        """Resolve an AsyncAPI Reference Object to raw data and a parser path."""
        resolved_ref = self._ref_path(ref)
        file_part, fragment = ([*resolved_ref.split("#", 1), ""])[:2]
        if file_part:
            remote = is_url(file_part)
            context = AsyncAPIContext(
                self._get_ref_body(file_part),
                [file_part] if remote else file_part.split("/"),
                None if remote else Path(file_part).parent,
                file_part if remote else None,
            )
        else:
            context = self._current_asyncapi_context()
        raw_doc, root_parts = context.raw_obj, context.root_parts
        if not fragment:
            return AsyncAPIResolvedRef(raw_doc, root_parts or [resolved_ref], context)
        pointer = [p for p in fragment.split("/") if p]
        path_pointer = [unescape_json_pointer_segment(p) for p in pointer]
        if not path_pointer:
            return AsyncAPIResolvedRef(raw_doc, [*root_parts, "#/"], context)
        target = get_model_by_path(raw_doc, pointer)
        return AsyncAPIResolvedRef(target, [*root_parts, f"#/{path_pointer[0]}", *path_pointer[1:]], context)

    def _iter_message_payload_schemas(
        self,
        message: Any,
        name: str,
        path: list[str],
        seen_paths: set[tuple[str, ...]] | None = None,
    ) -> list[AsyncAPISchema]:
        if (schemas := self._follow(message, name, seen_paths, self._iter_message_payload_schemas)) is not None:
            return schemas
        if isinstance(one_of := message.get("oneOf"), list):
            return _message_list_schemas(one_of, self._iter_message_payload_schemas, name, [*path, "oneOf"], seen_paths)
        payload_schema_format = message.get("schemaFormat")
        if payload_schema_format is not None and not isinstance(payload_schema_format, str):
            msg = f"AsyncAPI message schemaFormat must be a string at {_path_to_string(path)}"
            raise Error(msg)
        schemas = self._member_format_schemas(
            message, "payload", _make_model_name(name, "Payload"), path, payload_schema_format
        )
        headers_name = None if "headers" in message else _make_model_name(name, "Headers")
        schemas.extend(self._member_format_schemas(message, "headers", _make_model_name(name, "Headers"), path))
        if "traits" in message:
            schemas.extend(
                self._iter_message_trait_schemas(message["traits"], headers_name, name, [*path, "traits"], seen_paths)
            )
        schemas.extend(_member_schemas(message, "bindings", self._iter_binding_schemas, name, path, seen_paths))
        return schemas

    def _iter_binding_schemas(
        self,
        bindings: Any,
        name: str,
        path: list[str],
        seen_paths: set[tuple[str, ...]] | None = None,
    ) -> list[AsyncAPISchema]:
        if (schemas := self._follow(bindings, name, seen_paths, self._iter_binding_schemas)) is not None:
            return schemas
        schemas: list[AsyncAPISchema] = []
        for protocol_name, binding in _iter_mapping_items(bindings):
            if (
                followed := self._recurse_into_ref(
                    binding,
                    name,
                    seen_paths,
                    lambda value, _, ref_path, seen, protocol=protocol_name: self._iter_binding_schemas(
                        {protocol: value}, name, ref_path, seen
                    ),
                    rename=False,
                )
            ) is not None:
                schemas.extend(followed)
            elif isinstance(binding, dict):
                binding_name = _make_model_name(name, protocol_name)
                schemas.extend(
                    schema
                    for field_name, _ in _iter_mapping_items(binding)
                    if field_name in BINDING_SCHEMA_FIELD_NAMES
                    for schema in self._member_format_schemas(
                        binding, field_name, _make_model_name(binding_name, field_name), [*path, protocol_name]
                    )
                )
        return schemas

    def _iter_parameter_schemas(
        self,
        parameter: Any,
        name: str,
        path: list[str],
        seen_paths: set[tuple[str, ...]] | None = None,
    ) -> list[AsyncAPISchema]:
        if (schemas := self._follow(parameter, name, seen_paths, self._iter_parameter_schemas)) is not None:
            return schemas
        return self._member_format_schemas(parameter, "schema", name, path)

    def _iter_message_trait_schemas(
        self,
        traits: Any,
        headers_name: str | None,
        binding_name: str,
        path: list[str],
        seen_paths: set[tuple[str, ...]] | None = None,
    ) -> list[AsyncAPISchema]:
        schemas: list[AsyncAPISchema] = []
        for trait, trait_path in _iter_trait_items(traits, path):
            if (
                followed := self._recurse_into_ref(
                    trait,
                    binding_name,
                    seen_paths,
                    lambda value, _, ref_path, seen: self._iter_message_trait_schemas(
                        value, headers_name, binding_name, ref_path, seen
                    ),
                    rename=False,
                )
            ) is not None:
                schemas.extend(followed)
                continue
            if "headers" in trait and headers_name is not None:
                if any(schema.name == headers_name for schema in schemas):
                    msg = f"Multiple AsyncAPI message traits define headers for {_path_to_string(path)}"
                    raise Error(msg)
                schemas.extend(self._member_format_schemas(trait, "headers", headers_name, trait_path))
            schemas.extend(
                _member_schemas(
                    trait,
                    "bindings",
                    self._iter_binding_schemas,
                    _make_model_name(binding_name, "Trait"),
                    trait_path,
                    seen_paths,
                )
            )
        return schemas

    def _iter_operation_trait_schemas(
        self,
        traits: Any,
        name: str,
        path: list[str],
        seen_paths: set[tuple[str, ...]] | None = None,
    ) -> list[AsyncAPISchema]:
        schemas: list[AsyncAPISchema] = []
        for trait, trait_path in _iter_trait_items(traits, path):
            followed = self._recurse_into_ref(trait, name, seen_paths, self._iter_operation_trait_schemas, rename=False)
            schemas.extend(
                _member_schemas(
                    trait,
                    "bindings",
                    self._iter_binding_schemas,
                    _make_model_name(name, "Trait"),
                    trait_path,
                    seen_paths,
                )
                if followed is None
                else followed
            )
        return schemas

    def _iter_operation_message_schemas(
        self,
        message: Any,
        name: str,
        path: list[str],
    ) -> list[AsyncAPISchema]:
        if isinstance(message, list):
            return _message_list_schemas(message, self._iter_operation_message_schemas, name, path)
        return self._iter_message_payload_schemas(message, name, path)

    def _iter_operation_reply_schemas(
        self,
        reply: Any,
        name: str,
        path: list[str],
        seen_paths: set[tuple[str, ...]] | None = None,
    ) -> list[AsyncAPISchema]:
        if (
            schemas := self._follow(reply, name, seen_paths, self._iter_operation_reply_schemas, rename=False)
        ) is not None:
            return schemas
        return _member_schemas(
            reply, "messages", self._iter_operation_message_schemas, _make_model_name(name, "Reply"), path
        )

    def _iter_operation_schemas(
        self,
        operation: Any,
        name: str,
        path: list[str],
        seen_paths: set[tuple[str, ...]] | None = None,
    ) -> list[AsyncAPISchema]:
        if (schemas := self._follow(operation, name, seen_paths, self._iter_operation_schemas)) is not None:
            return schemas
        return [
            *_member_schemas(operation, "traits", self._iter_operation_trait_schemas, name, path, seen_paths),
            *_member_schemas(operation, "bindings", self._iter_binding_schemas, name, path, seen_paths),
            *_member_schemas(operation, "messages", self._iter_operation_message_schemas, name, path),
            *_member_schemas(operation, "reply", self._iter_operation_reply_schemas, name, path),
        ]

    def _iter_channel_schemas(
        self,
        channel: Any,
        name: str,
        path: list[str],
        seen_paths: set[tuple[str, ...]] | None = None,
    ) -> list[AsyncAPISchema]:
        if (schemas := self._follow(channel, name, seen_paths, self._iter_channel_schemas)) is not None:
            return schemas
        schemas = _map_schemas(
            channel.get("parameters"), self._iter_parameter_schemas, name, [*path, "parameters"], seen_paths
        )
        schemas.extend(_member_schemas(channel, "bindings", self._iter_binding_schemas, name, path, seen_paths))
        for operation_name in OPERATION_NAMES:
            if isinstance(operation := channel.get(operation_name), dict) and "message" in operation:
                schemas.extend(
                    self._iter_operation_message_schemas(
                        operation["message"], _make_model_name(name, operation_name), [*path, operation_name, "message"]
                    )
                )
        schemas.extend(
            _map_schemas(channel.get("messages"), self._iter_message_payload_schemas, name, [*path, "messages"])
        )
        return schemas

    def _iter_asyncapi_schemas(
        self,
        specification: dict[str, Any],
        path_parts: list[str],
    ) -> list[AsyncAPISchema]:
        """Return the schemas of a document: its component schemas, then those of each of its maps, in that order."""
        components = specification.get("components", {})
        components = components if isinstance(components, dict) else {}
        component_schemas = components.get("schemas")
        schemas = [
            schema
            for schema_name, raw_schema in _iter_mapping_items(component_schemas)
            if isinstance(raw_schema, (dict, bool))
            for schema in self._iter_schema_format_schemas(
                schema_name,
                raw_schema,
                [*path_parts, "#/components", "schemas", schema_name],
                location=self._member_location(component_schemas, str(schema_name)),
            )
        ]
        component_maps: tuple[tuple[str, Callable[..., list[AsyncAPISchema]], str], ...] = (
            ("messages", self._iter_message_payload_schemas, ""),
            (
                "messageTraits",
                lambda trait, name, path: self._iter_message_trait_schemas(
                    trait, name, name.removesuffix("Headers") or name, path
                ),
                "Headers",
            ),
            ("parameters", self._iter_parameter_schemas, ""),
            ("channels", self._iter_channel_schemas, ""),
            ("replies", self._iter_operation_reply_schemas, ""),
            ("operations", self._iter_operation_schemas, ""),
            ("operationTraits", self._iter_operation_trait_schemas, ""),
        )
        for key, walker, suffix in component_maps:
            path = [*path_parts, "#/components", key]
            schemas.extend(_map_schemas(components.get(key), walker, "", path, suffix=suffix))
        for key, walker in (("channels", self._iter_channel_schemas), ("operations", self._iter_operation_schemas)):
            schemas.extend(_map_schemas(specification.get(key), walker, "", [*path_parts, f"#/{key}"]))
        return schemas

    def _resolve_asyncapi_unparsed_json_pointer(
        self,
        context_sources: dict[tuple[str, ...], AsyncAPIContext],
    ) -> None:
        """Parse the targets each AsyncAPI context reserves, in key order, until a round adds no model.

        A target is read from the document of its context.
        """
        while True:
            model_count = len(self.results)
            for root_key, context in context_sources.items():
                if not self.reserved_refs.get(root_key):
                    continue
                with self._asyncapi_context(context):
                    for target in self._reserved_targets(list(root_key), context.raw_obj, self._reserved_ref_loaded):
                        self.parse_json_pointer(context.raw_obj, target["key"], list(root_key))
            if model_count == len(self.results):
                return

    def parse_raw(self) -> None:
        """Parse AsyncAPI component schemas and message payload/header schemas."""
        parsed_paths: set[tuple[str, ...]] = set()
        context_sources: dict[tuple[str, ...], AsyncAPIContext] = {}
        with self._parsing_session(lambda: self._resolve_asyncapi_unparsed_json_pointer(context_sources)):
            for source, path_parts in self._get_context_source_path_parts():
                if self.validation:
                    warn_deprecated("cli.validation", stacklevel=2)

                specification = self._index_document(self._load_source_dict(source), path_parts)
                self.raw_obj = specification
                context_sources[tuple(path_parts)] = self._current_asyncapi_context()
                self._collect_discriminator_schemas()
                for schema in self._iter_asyncapi_schemas(specification, path_parts):
                    context_sources[tuple(schema.context.root_parts)] = schema.context
                    raw_schema = schema.raw_schema
                    path_key = tuple(schema.path)
                    if path_key in parsed_paths:
                        continue
                    parsed_paths.add(path_key)
                    with self._asyncapi_context(schema.context):
                        if schema.parse_as_file:
                            self._parse_file(raw_schema, schema.name, schema.path)
                        else:
                            self._parse_raw_at(schema.name, raw_schema, schema.path, schema.location)
