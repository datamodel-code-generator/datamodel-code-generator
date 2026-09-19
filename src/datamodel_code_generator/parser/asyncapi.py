"""AsyncAPI specification parser.

Extracts AsyncAPI message schemas and delegates Schema Object handling to the
OpenAPI/JSON Schema parser stack.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pydantic import Field, StrictStr, ValidationError
from typing_extensions import Unpack

from datamodel_code_generator import Error, InputFileType, snooper_to_methods
from datamodel_code_generator.deprecations import warn_deprecated
from datamodel_code_generator.enums import AsyncAPIVersion
from datamodel_code_generator.parser.jsonschema import get_model_by_path, unescape_json_pointer_segment
from datamodel_code_generator.parser.openapi import OpenAPIParser
from datamodel_code_generator.reference import is_url
from datamodel_code_generator.util import BaseModel, create_module_getattr

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from urllib.parse import ParseResult

    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator._types import AsyncAPIParserConfigDict
    from datamodel_code_generator.config import AsyncAPIParserConfig
    from datamodel_code_generator.parser.schema_version import OpenAPISchemaFeatures


OPERATION_NAMES: tuple[str, ...] = ("publish", "subscribe")
NAME_PART_PATTERN = re.compile(r"[A-Za-z0-9]+")
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


@dataclass(frozen=True)
class AsyncAPIContext:
    """Resolver context for an AsyncAPI document."""

    raw_obj: dict[str, Any]
    root_parts: list[str]
    base_path: Path | None
    base_url: str | None


@dataclass(frozen=True)
class AsyncAPIResolvedRef:
    """Resolved AsyncAPI reference with parser path and document context."""

    value: YamlValue
    path: list[str]
    context: AsyncAPIContext


@dataclass(frozen=True)
class AsyncAPISchema:
    """AsyncAPI schema candidate with the context needed to parse it."""

    name: str
    raw_schema: YamlValue
    path: list[str]
    context: AsyncAPIContext
    parse_as_file: bool = False


class MultiFormatSchemaObject(BaseModel):
    """AsyncAPI Multi Format Schema Object fields used by the parser."""

    schema_: Any = Field(alias="schema")
    schema_format: StrictStr | None = Field(default=None, alias="schemaFormat")


def _iter_mapping_items(value: Any) -> list[tuple[str, Any]]:
    return list(value.items()) if isinstance(value, dict) else []


def _iter_trait_items(traits: Any, path: list[str]) -> Iterator[tuple[dict[str, Any], list[str]]]:
    is_trait_list = isinstance(traits, list)
    trait_items = traits if is_trait_list else [traits]
    for index, trait in enumerate(trait_items):
        if not isinstance(trait, dict):
            continue
        yield trait, [*path, str(index)] if is_trait_list else path


def _path_to_string(path: list[str]) -> str:
    return "/".join(path)


def _make_model_name(*parts: object) -> str:
    words: list[str] = []
    for part in parts:
        words.extend(word[:1].upper() + word[1:] for word in NAME_PART_PATTERN.findall(str(part)))
    return "".join(words) or "AsyncAPIModel"


def _escape_context_path_part(part: str) -> str:
    return part.removeprefix("#/").replace("~", "~0").replace("/", "~1")


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

    def __init__(
        self,
        source: str | Path | list[Path] | ParseResult,
        *,
        config: AsyncAPIParserConfig | None = None,
        **options: Unpack[AsyncAPIParserConfigDict],
    ) -> None:
        """Initialize the AsyncAPI parser."""
        super().__init__(source=source, config=config, **options)

    @property
    def schema_features(self) -> OpenAPISchemaFeatures:
        """Get Schema Object features based on config or detected AsyncAPI version."""
        # Keep this uncached because AsyncAPI reference parsing swaps raw_obj between document contexts.
        from datamodel_code_generator.parser.schema_version import (  # noqa: PLC0415
            OpenAPISchemaFeatures,
            detect_asyncapi_version,
        )

        config_version = getattr(self.config, "asyncapi_version", None)
        if config_version is not None and config_version != AsyncAPIVersion.Auto:
            return OpenAPISchemaFeatures.from_asyncapi_version(config_version)
        version = detect_asyncapi_version(self.raw_obj) if self.raw_obj else AsyncAPIVersion.Auto
        return OpenAPISchemaFeatures.from_asyncapi_version(version)

    def _collect_discriminator_schemas(self) -> None:
        """Collect discriminator metadata from component schemas."""
        self._discriminator_documents.clear()
        self._discriminator_schemas.clear()
        self._discriminator_subtypes.clear()
        self._collect_discriminator_document(self.raw_obj)

    def _collect_discriminator_document(self, raw: dict[str, Any]) -> None:
        """Retain AsyncAPI's component-only policy when reference loading indexes documents."""
        self._discriminator_documents.add(f"{'/'.join(self.model_resolver.current_root)}#")
        schemas = raw.get("components", {}).get("schemas", {})
        if not isinstance(schemas, dict):
            return
        for schema_name, schema in schemas.items():
            if not isinstance(schema, dict):
                continue
            self._register_discriminator_schema(f"#/components/schemas/{schema_name}", schema)

    def _current_asyncapi_context(self) -> AsyncAPIContext:
        return AsyncAPIContext(
            raw_obj=self.raw_obj,
            root_parts=list(self.model_resolver.current_root),
            base_path=self.model_resolver.current_base_path,
            base_url=self.model_resolver.base_url,
        )

    @contextmanager
    def _asyncapi_context(self, context: AsyncAPIContext) -> Iterator[None]:
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

    def _schema(
        self,
        name: str,
        raw_schema: YamlValue,
        path: list[str],
        context: AsyncAPIContext | None = None,
        *,
        parse_as_file: bool = False,
    ) -> AsyncAPISchema:
        return AsyncAPISchema(
            name=name,
            raw_schema=raw_schema,
            path=path,
            context=context or self._current_asyncapi_context(),
            parse_as_file=parse_as_file,
        )

    def _schema_context_for_converted_schema(
        self,
        converted_schema: dict[str, YamlValue],
        path: list[str],
    ) -> AsyncAPIContext:
        current_context = self._current_asyncapi_context()
        context_path = path[len(current_context.root_parts) :]
        return AsyncAPIContext(
            raw_obj=converted_schema,
            root_parts=[
                *current_context.root_parts,
                "__asyncapi_schemas__",
                *(_escape_context_path_part(part) for part in context_path),
            ],
            base_path=current_context.base_path,
            base_url=current_context.base_url,
        )

    def _iter_converted_schema_schemas(
        self,
        name: str,
        converted_schema: dict[str, YamlValue],
        path: list[str],
        *,
        include_root_schema: bool = False,
    ) -> list[AsyncAPISchema]:
        context = self._current_asyncapi_context()
        converted_schema_key = "_".join(_escape_context_path_part(part) for part in path) or name
        ref_prefix = f"#/{INTERNAL_SCHEMA_CONTAINER}/{converted_schema_key}/components/schemas"
        converted_schema = _move_json_schema_definitions_to_components(converted_schema, ref_prefix=ref_prefix)
        converted_schema.setdefault("title", name)
        context.raw_obj.setdefault(INTERNAL_SCHEMA_CONTAINER, {})[converted_schema_key] = converted_schema
        root_path = [*context.root_parts, f"#/{INTERNAL_SCHEMA_CONTAINER}", converted_schema_key]
        schemas = [self._schema(name, converted_schema, root_path, context)] if include_root_schema else []
        schemas.extend(
            self._schema(
                schema_name,
                component_schema,
                [*root_path, "components", "schemas", schema_name],
                context,
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
    ) -> list[AsyncAPISchema]:
        schema_format = inherited_schema_format
        if multi_format_schema := _parse_multi_format_schema_object(raw_schema, path):
            schema_format = multi_format_schema.schema_format or schema_format
            raw_schema = multi_format_schema.schema_
            path = [*path, "schema"]

        match _schema_format_kind(schema_format, path):
            case "asyncapi" | "jsonschema" | "openapi":
                if not isinstance(raw_schema, (dict, bool)):
                    msg = (
                        f"AsyncAPI schemaFormat {schema_format or 'default AsyncAPI schema'!r} "
                        f"requires a schema object at {_path_to_string(path)}"
                    )
                    raise Error(msg)
                return [self._schema(name, raw_schema, path)]
            case "avro":
                from datamodel_code_generator.parser.avro import convert_avro_schema_data  # noqa: PLC0415

                converted_schema = convert_avro_schema_data(raw_schema)
                converted_schema.setdefault("title", name)
                context = self._schema_context_for_converted_schema(converted_schema, path)
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
                )
            case _:  # pragma: no cover
                raise _unsupported_schema_format_error(schema_format or "", path)

    def _resolve_ref_object(
        self,
        value: Any,
        seen_paths: set[tuple[str, ...]],
    ) -> AsyncAPIResolvedRef | None:
        if not isinstance(value, dict) or not isinstance(ref := value.get("$ref"), str):
            return None
        resolved = self._resolve_asyncapi_ref(ref)
        return None if tuple(resolved.path) in seen_paths else resolved

    def _recurse_into_ref(
        self,
        value: Any,
        name: str,
        seen_paths: set[tuple[str, ...]],
        recurse: Callable[[Any, str, list[str], set[tuple[str, ...]]], list[AsyncAPISchema]],
        *,
        rename: bool = True,
    ) -> list[AsyncAPISchema] | None:
        """Return ref recursion results; None means no ref or an already-seen ref."""
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

    def _resolve_asyncapi_ref(self, ref: str) -> AsyncAPIResolvedRef:
        """Resolve an AsyncAPI Reference Object to raw data and a parser path."""
        resolved_ref = self.model_resolver.resolve_ref(ref)
        file_part, fragment = ([*resolved_ref.split("#", 1), ""])[:2]
        raw_doc = self._get_ref_body(file_part) if file_part else self.raw_obj
        if file_part:
            root_parts = [file_part] if is_url(file_part) else file_part.split("/")
            base_path = None if is_url(file_part) else Path(file_part).parent
            base_url = file_part if is_url(file_part) else None
        else:
            root_parts = list(self.model_resolver.current_root)
            base_path = self.model_resolver.current_base_path
            base_url = self.model_resolver.base_url
        context = AsyncAPIContext(
            raw_obj=raw_doc,
            root_parts=root_parts,
            base_path=base_path,
            base_url=base_url,
        )
        if not fragment:
            return AsyncAPIResolvedRef(raw_doc, root_parts or [resolved_ref], context)
        pointer = [p for p in fragment.split("/") if p]
        path_pointer = [unescape_json_pointer_segment(p) for p in pointer]
        if not path_pointer:
            return AsyncAPIResolvedRef(raw_doc, [*root_parts, "#/"], context)
        return AsyncAPIResolvedRef(
            get_model_by_path(raw_doc, pointer),
            [*root_parts, f"#/{path_pointer[0]}", *path_pointer[1:]],
            context,
        )

    def _iter_message_payload_schemas(
        self,
        message: Any,
        name: str,
        path: list[str],
        seen_paths: set[tuple[str, ...]] | None = None,
    ) -> list[AsyncAPISchema]:
        seen_paths = seen_paths or set()
        if not isinstance(message, dict):
            return []
        if (
            schemas := self._recurse_into_ref(message, name, seen_paths, self._iter_message_payload_schemas)
        ) is not None:
            return schemas
        if isinstance(one_of := message.get("oneOf"), list):
            schemas: list[AsyncAPISchema] = []
            for index, item in enumerate(one_of, start=1):
                schemas.extend(
                    self._iter_message_payload_schemas(
                        item,
                        _make_model_name(name, "Message", index),
                        [*path, "oneOf", str(index - 1)],
                        seen_paths,
                    )
                )
            return schemas

        schemas = []
        payload_schema_format = message.get("schemaFormat")
        if payload_schema_format is not None and not isinstance(payload_schema_format, str):
            msg = f"AsyncAPI message schemaFormat must be a string at {_path_to_string(path)}"
            raise Error(msg)
        if "payload" in message:
            schemas.extend(
                self._iter_schema_format_schemas(
                    _make_model_name(name, "Payload"),
                    message["payload"],
                    [*path, "payload"],
                    payload_schema_format,
                )
            )
        has_headers = "headers" in message
        if has_headers:
            schemas.extend(
                self._iter_schema_format_schemas(
                    _make_model_name(name, "Headers"),
                    message["headers"],
                    [*path, "headers"],
                )
            )
        if "traits" in message:
            schemas.extend(
                self._iter_message_trait_schemas(
                    message["traits"],
                    None if has_headers else _make_model_name(name, "Headers"),
                    name,
                    [*path, "traits"],
                    seen_paths,
                )
            )
        if "bindings" in message:
            schemas.extend(
                self._iter_binding_schemas(
                    message["bindings"],
                    name,
                    [*path, "bindings"],
                    seen_paths,
                )
            )
        return schemas

    def _iter_binding_schemas(
        self,
        bindings: Any,
        name: str,
        path: list[str],
        seen_paths: set[tuple[str, ...]] | None = None,
    ) -> list[AsyncAPISchema]:
        seen_paths = seen_paths or set()
        if not isinstance(bindings, dict):
            return []
        if (schemas := self._recurse_into_ref(bindings, name, seen_paths, self._iter_binding_schemas)) is not None:
            return schemas

        schemas: list[AsyncAPISchema] = []
        for protocol_name, binding in _iter_mapping_items(bindings):
            binding_path = [*path, protocol_name]
            binding_name = _make_model_name(name, protocol_name)
            if resolved := self._resolve_ref_object(binding, seen_paths):
                with self._asyncapi_context(resolved.context):
                    schemas.extend(
                        self._iter_binding_schemas(
                            {protocol_name: resolved.value},
                            name,
                            resolved.path,
                            seen_paths | {tuple(resolved.path)},
                        )
                    )
                continue
            if not isinstance(binding, dict):
                continue
            for field_name, field_schema in _iter_mapping_items(binding):
                if field_name not in BINDING_SCHEMA_FIELD_NAMES:
                    continue
                schemas.extend(
                    self._iter_schema_format_schemas(
                        _make_model_name(binding_name, field_name),
                        field_schema,
                        [*binding_path, field_name],
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
        seen_paths = seen_paths or set()
        if not isinstance(parameter, dict):
            return []
        if (schemas := self._recurse_into_ref(parameter, name, seen_paths, self._iter_parameter_schemas)) is not None:
            return schemas
        if "schema" not in parameter:
            return []
        return self._iter_schema_format_schemas(
            name,
            parameter["schema"],
            [*path, "schema"],
        )

    def _iter_message_trait_schemas(
        self,
        traits: Any,
        headers_name: str | None,
        binding_name: str,
        path: list[str],
        seen_paths: set[tuple[str, ...]] | None = None,
    ) -> list[AsyncAPISchema]:
        seen_paths = seen_paths or set()
        schemas: list[AsyncAPISchema] = []
        for trait, trait_path in _iter_trait_items(traits, path):
            if resolved := self._resolve_ref_object(trait, seen_paths):
                with self._asyncapi_context(resolved.context):
                    schemas.extend(
                        self._iter_message_trait_schemas(
                            resolved.value,
                            headers_name,
                            binding_name,
                            resolved.path,
                            seen_paths | {tuple(resolved.path)},
                        )
                    )
                continue
            if "headers" in trait and headers_name is not None:
                if any(schema.name == headers_name for schema in schemas):
                    msg = f"Multiple AsyncAPI message traits define headers for {_path_to_string(path)}"
                    raise Error(msg)
                schemas.extend(
                    self._iter_schema_format_schemas(
                        headers_name,
                        trait["headers"],
                        [*trait_path, "headers"],
                    )
                )
            if "bindings" in trait:
                schemas.extend(
                    self._iter_binding_schemas(
                        trait["bindings"],
                        _make_model_name(binding_name, "Trait"),
                        [*trait_path, "bindings"],
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
        seen_paths = seen_paths or set()
        schemas: list[AsyncAPISchema] = []
        for trait, trait_path in _iter_trait_items(traits, path):
            if resolved := self._resolve_ref_object(trait, seen_paths):
                with self._asyncapi_context(resolved.context):
                    schemas.extend(
                        self._iter_operation_trait_schemas(
                            resolved.value,
                            name,
                            resolved.path,
                            seen_paths | {tuple(resolved.path)},
                        )
                    )
                continue
            if "bindings" not in trait:
                continue
            schemas.extend(
                self._iter_binding_schemas(
                    trait["bindings"],
                    _make_model_name(name, "Trait"),
                    [*trait_path, "bindings"],
                    seen_paths,
                )
            )
        return schemas

    def _iter_message_trait_header_schemas(
        self,
        traits: Any,
        name: str,
        path: list[str],
        seen_paths: set[tuple[str, ...]] | None = None,
    ) -> list[AsyncAPISchema]:
        return self._iter_message_trait_schemas(
            traits,
            name,
            name.removesuffix("Headers") or name,
            path,
            seen_paths,
        )

    def _iter_operation_message_schemas(
        self,
        message: Any,
        name: str,
        path: list[str],
    ) -> list[AsyncAPISchema]:
        if isinstance(message, list):
            schemas: list[AsyncAPISchema] = []
            for index, item in enumerate(message, start=1):
                schemas.extend(
                    self._iter_operation_message_schemas(
                        item,
                        _make_model_name(name, "Message", index),
                        [*path, str(index - 1)],
                    )
                )
            return schemas
        return self._iter_message_payload_schemas(message, name, path)

    def _iter_operation_reply_schemas(
        self,
        reply: Any,
        name: str,
        path: list[str],
        seen_paths: set[tuple[str, ...]] | None = None,
    ) -> list[AsyncAPISchema]:
        seen_paths = seen_paths or set()
        if not isinstance(reply, dict):
            return []
        if (
            schemas := self._recurse_into_ref(reply, name, seen_paths, self._iter_operation_reply_schemas, rename=False)
        ) is not None:
            return schemas
        if "messages" not in reply:
            return []
        return self._iter_operation_message_schemas(
            reply["messages"],
            _make_model_name(name, "Reply"),
            [*path, "messages"],
        )

    def _iter_operation_schemas(
        self,
        operation: Any,
        name: str,
        path: list[str],
        seen_paths: set[tuple[str, ...]] | None = None,
    ) -> list[AsyncAPISchema]:
        seen_paths = seen_paths or set()
        if not isinstance(operation, dict):
            return []
        if (schemas := self._recurse_into_ref(operation, name, seen_paths, self._iter_operation_schemas)) is not None:
            return schemas
        schemas: list[AsyncAPISchema] = []
        if "traits" in operation:
            schemas.extend(
                self._iter_operation_trait_schemas(
                    operation["traits"],
                    name,
                    [*path, "traits"],
                    seen_paths,
                )
            )
        if "bindings" in operation:
            schemas.extend(
                self._iter_binding_schemas(
                    operation["bindings"],
                    name,
                    [*path, "bindings"],
                    seen_paths,
                )
            )
        if "messages" in operation:
            schemas.extend(
                self._iter_operation_message_schemas(
                    operation["messages"],
                    name,
                    [*path, "messages"],
                )
            )
        if "reply" in operation:
            schemas.extend(
                self._iter_operation_reply_schemas(
                    operation["reply"],
                    name,
                    [*path, "reply"],
                )
            )
        return schemas

    def _iter_channel_schemas(
        self,
        channel: Any,
        name: str,
        path: list[str],
        seen_paths: set[tuple[str, ...]] | None = None,
    ) -> list[AsyncAPISchema]:
        seen_paths = seen_paths or set()
        if not isinstance(channel, dict):
            return []
        if (schemas := self._recurse_into_ref(channel, name, seen_paths, self._iter_channel_schemas)) is not None:
            return schemas
        schemas: list[AsyncAPISchema] = []
        if "parameters" in channel:
            for parameter_name, parameter in _iter_mapping_items(channel["parameters"]):
                schemas.extend(
                    self._iter_parameter_schemas(
                        parameter,
                        _make_model_name(name, parameter_name),
                        [*path, "parameters", parameter_name],
                        seen_paths,
                    )
                )
        if "bindings" in channel:
            schemas.extend(
                self._iter_binding_schemas(
                    channel["bindings"],
                    name,
                    [*path, "bindings"],
                    seen_paths,
                )
            )
        for operation_name in OPERATION_NAMES:
            operation = channel.get(operation_name)
            if isinstance(operation, dict) and "message" in operation:
                schemas.extend(
                    self._iter_operation_message_schemas(
                        operation["message"],
                        _make_model_name(name, operation_name),
                        [*path, operation_name, "message"],
                    )
                )
        for message_name, message in _iter_mapping_items(channel.get("messages")):
            schemas.extend(
                self._iter_message_payload_schemas(
                    message,
                    _make_model_name(name, message_name),
                    [*path, "messages", message_name],
                )
            )
        return schemas

    def _iter_asyncapi_schemas(
        self,
        specification: dict[str, Any],
        path_parts: list[str],
    ) -> list[AsyncAPISchema]:
        schemas: list[AsyncAPISchema] = []
        components = specification.get("components", {})
        components = components if isinstance(components, dict) else {}

        for schema_name, raw_schema in _iter_mapping_items(components.get("schemas")):
            if not isinstance(raw_schema, (dict, bool)):
                continue
            schemas.extend(
                self._iter_schema_format_schemas(
                    schema_name,
                    raw_schema,
                    [*path_parts, "#/components", "schemas", schema_name],
                )
            )

        for message_name, message in _iter_mapping_items(components.get("messages")):
            schemas.extend(
                self._iter_message_payload_schemas(
                    message,
                    _make_model_name(message_name),
                    [*path_parts, "#/components", "messages", message_name],
                )
            )

        for trait_name, trait in _iter_mapping_items(components.get("messageTraits")):
            schemas.extend(
                self._iter_message_trait_header_schemas(
                    trait,
                    _make_model_name(trait_name, "Headers"),
                    [*path_parts, "#/components", "messageTraits", trait_name],
                )
            )

        for parameter_name, parameter in _iter_mapping_items(components.get("parameters")):
            schemas.extend(
                self._iter_parameter_schemas(
                    parameter,
                    _make_model_name(parameter_name),
                    [*path_parts, "#/components", "parameters", parameter_name],
                )
            )

        for channel_name, channel in _iter_mapping_items(components.get("channels")):
            schemas.extend(
                self._iter_channel_schemas(
                    channel,
                    _make_model_name(channel_name),
                    [*path_parts, "#/components", "channels", channel_name],
                )
            )

        for reply_name, reply in _iter_mapping_items(components.get("replies")):
            schemas.extend(
                self._iter_operation_reply_schemas(
                    reply,
                    _make_model_name(reply_name),
                    [*path_parts, "#/components", "replies", reply_name],
                )
            )

        for operation_name, operation in _iter_mapping_items(components.get("operations")):
            schemas.extend(
                self._iter_operation_schemas(
                    operation,
                    _make_model_name(operation_name),
                    [*path_parts, "#/components", "operations", operation_name],
                )
            )

        for trait_name, trait in _iter_mapping_items(components.get("operationTraits")):
            schemas.extend(
                self._iter_operation_trait_schemas(
                    trait,
                    _make_model_name(trait_name),
                    [*path_parts, "#/components", "operationTraits", trait_name],
                )
            )

        for channel_name, channel in _iter_mapping_items(specification.get("channels")):
            schemas.extend(
                self._iter_channel_schemas(
                    channel,
                    _make_model_name(channel_name),
                    [*path_parts, "#/channels", channel_name],
                )
            )

        for operation_name, operation in _iter_mapping_items(specification.get("operations")):
            schemas.extend(
                self._iter_operation_schemas(
                    operation,
                    _make_model_name(operation_name),
                    [*path_parts, "#/operations", operation_name],
                )
            )

        return schemas

    def _resolve_asyncapi_unparsed_json_pointer(
        self,
        context_sources: dict[tuple[str, ...], AsyncAPIContext],
    ) -> None:
        """Resolve JSON pointers per AsyncAPI context, preserving sorted reserved-ref fixed-point order."""
        model_count = len(self.results)
        for root_key, context in context_sources.items():
            if not (reserved_refs := self.reserved_refs.get(root_key)):
                continue

            with self._asyncapi_context(context):
                for reserved_ref in sorted(reserved_refs):
                    if self.model_resolver.add_ref(reserved_ref, resolved=True).loaded:
                        continue
                    self.parse_json_pointer(context.raw_obj, reserved_ref, list(root_key))

        if model_count != len(self.results):
            self._resolve_asyncapi_unparsed_json_pointer(context_sources)

    def parse_raw(self) -> None:
        """Parse AsyncAPI component schemas and message payload/header schemas."""
        parsed_paths: set[tuple[str, ...]] = set()
        context_sources: dict[tuple[str, ...], AsyncAPIContext] = {}
        try:
            for source, path_parts in self._get_context_source_path_parts():
                if self.validation:
                    warn_deprecated("cli.validation", stacklevel=2)

                specification = self._load_source_dict(source)
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
                            self.parse_raw_obj(schema.name, raw_schema, schema.path)

            self._resolve_asyncapi_unparsed_json_pointer(context_sources)
            self._generate_forced_base_models()
        finally:
            self._reset_local_source_cache()
