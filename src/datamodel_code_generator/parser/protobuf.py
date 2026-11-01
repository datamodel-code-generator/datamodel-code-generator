"""Protocol Buffers parser implementation.

The parser compiles ``.proto`` files to descriptors with ``protoc`` and converts
the descriptor model to JSON Schema definitions for the existing JSON Schema parser.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast
from warnings import warn

from typing_extensions import Unpack

from datamodel_code_generator import Error, ProtobufVersion, SchemaParseError, VersionMode
from datamodel_code_generator._message_text import extra_requirement
from datamodel_code_generator.parser._null_facts import null_schema, with_null
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from datamodel_code_generator.python_literal import _converted_float
from datamodel_code_generator.util import record_watch_dependency

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence
    from urllib.parse import ParseResult

    from datamodel_code_generator._types import ProtobufParserConfigDict
    from datamodel_code_generator.config import ProtobufParserConfig

PROTO_TCHARS = re.compile(
    r"(?P<comment>//[^\n]*|/\*[\s\S]*?(?:\*/|\Z))"
    r"|\"(?:\\[\s\S]|[^\"\\])*\"|'(?:\\[\s\S]|[^'\\])*'|[a-zA-Z_][a-zA-Z_0-9]*|[^\s]"
)
WEAK_IMPORT_PATTERN = re.compile(r'^\s*import\s+weak\s+"([^"]+)"\s*;', re.MULTILINE)
IMPORT_PATTERN = re.compile(r'^\s*import\s+(?:public\s+|weak\s+)?"([^"]+)"\s*;', re.MULTILINE)

LABEL_REQUIRED = 2
LABEL_REPEATED = 3

TYPE_DOUBLE = 1
TYPE_FLOAT = 2
TYPE_INT64 = 3
TYPE_UINT64 = 4
TYPE_INT32 = 5
TYPE_FIXED64 = 6
TYPE_FIXED32 = 7
TYPE_BOOL = 8
TYPE_STRING = 9
TYPE_GROUP = 10
TYPE_MESSAGE = 11
TYPE_BYTES = 12
TYPE_UINT32 = 13
TYPE_ENUM = 14
TYPE_SFIXED32 = 15
TYPE_SFIXED64 = 16
TYPE_SINT32 = 17
TYPE_SINT64 = 18

_INTEGER_TYPES = (
    TYPE_INT32,
    TYPE_INT64,
    TYPE_UINT32,
    TYPE_UINT64,
    TYPE_SINT32,
    TYPE_SINT64,
    TYPE_FIXED32,
    TYPE_FIXED64,
    TYPE_SFIXED32,
    TYPE_SFIXED64,
)

PROTO2_DEFAULTS: dict[int, Any] = dict(
    sorted(
        {
            **dict.fromkeys(_INTEGER_TYPES, 0),
            TYPE_DOUBLE: 0.0,
            TYPE_FLOAT: 0.0,
            TYPE_BOOL: False,
            TYPE_STRING: "",
            TYPE_BYTES: b"",
        }.items()
    )
)

SCALAR_SCHEMAS: dict[int, dict[str, Any]] = {
    TYPE_DOUBLE: {"type": "number", "format": "double"},
    TYPE_FLOAT: {"type": "number", "format": "float"},
    TYPE_INT64: {"type": "integer", "format": "int64"},
    TYPE_UINT64: {"type": "integer", "format": "int64", "minimum": 0, "maximum": 18446744073709551615},
    TYPE_INT32: {"type": "integer", "format": "int32"},
    TYPE_UINT32: {"type": "integer", "format": "int32", "minimum": 0, "maximum": 4294967295},
    TYPE_SINT32: {"type": "integer", "format": "int32"},
    TYPE_SINT64: {"type": "integer", "format": "int64"},
    TYPE_FIXED32: {"type": "integer", "format": "int32", "minimum": 0, "maximum": 4294967295},
    TYPE_FIXED64: {"type": "integer", "format": "int64", "minimum": 0, "maximum": 18446744073709551615},
    TYPE_SFIXED32: {"type": "integer", "format": "int32"},
    TYPE_SFIXED64: {"type": "integer", "format": "int64"},
    TYPE_BOOL: {"type": "boolean"},
    TYPE_STRING: {"type": "string"},
    TYPE_BYTES: {"type": "string", "format": "binary"},
}
SCALAR_OR_ENUM_TYPES = frozenset({*SCALAR_SCHEMAS, TYPE_ENUM})


def _watch_dependency_collection_is_active() -> bool:
    """Check the optional watch collector without importing it on regular generation."""
    return (watch_dependencies := sys.modules.get("datamodel_code_generator.watch_dependencies")) is not None and (
        watch_dependencies.collector_is_active()
    )


MAP_KEY_PYTHON_TYPES: dict[int, str] = {**dict.fromkeys(_INTEGER_TYPES, "int"), TYPE_BOOL: "bool", TYPE_STRING: "str"}
WELL_KNOWN_PROTO_PATHS = frozenset({
    "google/protobuf/any.proto",
    "google/protobuf/api.proto",
    "google/protobuf/descriptor.proto",
    "google/protobuf/duration.proto",
    "google/protobuf/empty.proto",
    "google/protobuf/field_mask.proto",
    "google/protobuf/source_context.proto",
    "google/protobuf/struct.proto",
    "google/protobuf/timestamp.proto",
    "google/protobuf/type.proto",
    "google/protobuf/wrappers.proto",
})

WELL_KNOWN_SCHEMAS: dict[str, dict[str, Any]] = {
    "google.protobuf.Timestamp": {"type": "string", "format": "date-time"},
    "google.protobuf.Duration": {"type": "string", "format": "duration"},
    "google.protobuf.Struct": {"type": "object", "additionalProperties": True},
    "google.protobuf.ListValue": {"type": "array", "items": {}},
    "google.protobuf.Value": {
        "anyOf": [
            null_schema(),
            {"type": "boolean"},
            {"type": "number"},
            {"type": "string"},
            {"type": "object", "additionalProperties": True},
            {"type": "array", "items": {}},
        ]
    },
    "google.protobuf.Any": {"type": "object", "additionalProperties": True},
    "google.protobuf.Empty": {"type": "object", "properties": {}, "additionalProperties": False},
    "google.protobuf.FieldMask": {"type": "string"},
    "google.protobuf.DoubleValue": with_null({"type": "number", "format": "double"}, "member"),
    "google.protobuf.FloatValue": with_null({"type": "number", "format": "float"}, "member"),
    "google.protobuf.Int64Value": with_null({"type": "integer", "format": "int64"}, "member"),
    "google.protobuf.UInt64Value": with_null({"type": "integer", "format": "int64", "minimum": 0}, "member"),
    "google.protobuf.Int32Value": with_null({"type": "integer", "format": "int32"}, "member"),
    "google.protobuf.UInt32Value": with_null({"type": "integer", "format": "int32", "minimum": 0}, "member"),
    "google.protobuf.BoolValue": with_null({"type": "boolean"}, "member"),
    "google.protobuf.StringValue": with_null({"type": "string"}, "member"),
    "google.protobuf.BytesValue": with_null({"type": "string", "format": "binary"}, "member"),
}


def _load_grpc_tools() -> tuple[Any, Path]:
    try:
        import grpc_tools  # noqa: PLC0415
        from grpc_tools import protoc  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        msg = f"protobuf input requires grpcio-tools. Install {extra_requirement('protobuf')}."
        raise Error(msg) from exc
    return protoc, Path(grpc_tools.__file__).parent / "_proto"


def _load_descriptor_pb2() -> Any:
    try:
        from google.protobuf import descriptor_pb2  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        msg = f"protobuf input requires google.protobuf. Install {extra_requirement('protobuf')}."
        raise Error(msg) from exc
    return descriptor_pb2


def _full_name(package: str, parents: Sequence[str], name: str) -> str:
    return ".".join(part for part in (*([package] if package else []), *parents, name) if part)


def _type_name(type_name: str) -> str:
    return type_name.removeprefix(".")


def _clean_comment(comment: str) -> str:
    return "\n".join(line.strip() for line in comment.strip().splitlines())


def _sanitize_proto_source(text: str) -> str:
    """Drop custom options while preserving comments and both string delimiters."""
    if "(" not in text:
        return text
    tokens = (token for token in PROTO_TCHARS.finditer(text) if token.lastgroup != "comment")
    result: list[str] = []
    copied = 0
    previous = None
    before_previous = ""
    for token in tokens:
        match token[0]:
            case "[":
                end, replacement = _replace_field_options(text, tokens, token.end())
                if replacement is not None:
                    result.extend((text[copied : token.start()], replacement))
                    copied = end
            case "(" if previous is not None and previous[0] == "option" and before_previous != "rpc":
                depth = 0
                for end_token in tokens:
                    match end_token[0]:
                        case "{" | "[":
                            depth += 1
                        case "}" | "]":
                            depth -= 1
                        case ";" if depth == 0:
                            result.append(text[copied : previous.start()])
                            copied = end_token.end()
                            break
        before_previous = previous[0] if previous is not None else ""
        previous = token
    if not result:
        return text
    result.append(text[copied:])
    return "".join(result)


def _replace_field_options(text: str, tokens: Iterator[re.Match[str]], start: int) -> tuple[int, str | None]:
    """Consume a field option list, retaining its default when custom options occur."""
    depth = 0
    custom = False
    name = ""
    value_start = value_end = start
    default = None
    for token in tokens:
        value = token[0]
        if depth == 0 and value in {",", "]"}:
            if name == "default":
                default = (value_start, value_end)
            if value == "]":
                if not custom:
                    return token.end(), None
                replacement = f"[default = {text[default[0] : default[1]]}]" if default is not None else ""
                return token.end(), replacement
            name = ""
        else:
            if not name:
                name = value
                custom |= value == "("
            elif name == "default" and value == "=":
                value_start = value_end = token.end()
            elif name == "default":
                if value_start == value_end:
                    value_start = token.start()
                value_end = token.end()
            match value:
                case "(" | "{" | "[":
                    depth += 1
                case ")" | "}" | "]":
                    depth -= 1
    return start, None


def convert_protobuf_schema_data(
    raw_schema: Any,
    *,
    base_path: Path | None = None,
    protobuf_version: ProtobufVersion | None = None,
    schema_version_mode: VersionMode | None = None,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Convert a Protocol Buffers schema source to JSON Schema data."""
    return _convert_protobuf_schema_data(
        raw_schema,
        base_path=base_path,
        protobuf_version=protobuf_version,
        schema_version_mode=schema_version_mode,
        encoding=encoding,
        source_safe_non_finite=False,
    )


def _convert_protobuf_schema_data(  # noqa: PLR0913
    raw_schema: Any,
    *,
    base_path: Path | None = None,
    protobuf_version: ProtobufVersion | None = None,
    schema_version_mode: VersionMode | None = None,
    encoding: str = "utf-8",
    source_safe_non_finite: bool,
) -> dict[str, Any]:
    """Convert Protocol Buffers data for a parser that may need source-safe float values."""
    if not isinstance(raw_schema, str):
        msg = "Protocol Buffers schemaFormat requires a .proto schema string"
        raise Error(msg)
    parser = ProtobufParser(
        raw_schema,
        base_path=base_path,
        protobuf_version=protobuf_version,
        schema_version_mode=schema_version_mode,
        encoding=encoding,
    )
    return parser._convert_to_json_schema_data(source_safe_non_finite=source_safe_non_finite)  # noqa: SLF001


def _declarations(
    file_descriptor: Any, *, post_order: bool = False
) -> Iterator[tuple[bool, Any, str, tuple[int, ...]]]:
    """Yield ``(is_enum, descriptor, full name, source path)`` for every message and enum declared in a file.

    In pre-order each message comes before its enums and nested messages and the file's enums come last, as symbols
    are registered; in post-order the file's enums come first and each message after its enums and nested messages,
    as definitions are written.
    """
    package = file_descriptor.package
    enums = [
        (True, enum, _full_name(package, (), enum.name), (5, index))
        for index, enum in enumerate(file_descriptor.enum_type)
    ]
    if post_order:
        yield from enums
    yield from _message_declarations(file_descriptor.message_type, package, (), (4,), post_order=post_order)
    if not post_order:
        yield from enums


def _message_declarations(
    messages: Iterable[Any], package: str, parents: tuple[str, ...], path: tuple[int, ...], *, post_order: bool
) -> Iterator[tuple[bool, Any, str, tuple[int, ...]]]:
    for index, message in enumerate(messages):
        message_path = (*path, index)
        declaration = (False, message, _full_name(package, parents, message.name), message_path)
        if not post_order:
            yield declaration
        nested_parents = (*parents, message.name)
        for enum_index, enum in enumerate(message.enum_type):
            yield True, enum, _full_name(package, nested_parents, enum.name), (*message_path, 4, enum_index)
        yield from _message_declarations(
            message.nested_type, package, nested_parents, (*message_path, 3), post_order=post_order
        )
        if post_order:
            yield declaration


def _comment_map(file_descriptor: Any) -> dict[tuple[int, ...], str]:
    comments: dict[tuple[int, ...], str] = {}
    for location in file_descriptor.source_code_info.location:
        comment = location.leading_comments or location.trailing_comments
        if comment:
            comments[tuple(location.path)] = _clean_comment(comment)
    return comments


class _ProtoInputPreparer:
    def __init__(self, parser: ProtobufParser) -> None:
        self.parser = parser
        self.temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self.weak_import_dir: Path | None = None

    def __enter__(self) -> tuple[list[Path], list[Path], frozenset[str]]:
        self.temp_dir = tempfile.TemporaryDirectory()
        try:
            temp_path = Path(self.temp_dir.name)
            include_paths = [temp_path, self.parser.base_path]
            input_files = self._input_files(temp_path)
            input_file_names = frozenset(path.relative_to(temp_path).as_posix() for path in input_files)
            include_paths.extend(self._additional_include_paths(input_files))
            self.weak_import_dir = temp_path / "__weak_imports__"
            self._write_missing_weak_imports(input_files, include_paths)
            if self.weak_import_dir.exists():
                include_paths.append(self.weak_import_dir)
        except Exception:
            self.temp_dir.cleanup()
            raise
        else:
            return input_files, include_paths, input_file_names

    def _additional_include_paths(self, input_files: Sequence[Path]) -> list[Path]:
        paths = {path.parent for path in input_files}
        source = self.parser.source
        if isinstance(source, Path) and source.is_file():
            paths.add(source.parent)
        elif isinstance(source, list):
            paths.update(path.parent for path in source)
        paths.discard(Path())
        paths.discard(self.parser.base_path)
        return sorted(paths)

    def __exit__(self, *args: object) -> None:
        assert self.temp_dir is not None
        self.temp_dir.cleanup()

    def _input_files(self, temp_path: Path) -> list[Path]:
        source = self.parser.source
        if isinstance(source, Path):
            if source.is_dir():
                paths = sorted((path for path in source.rglob("*.proto") if path.is_file()), key=lambda path: path.name)
                return [self._write_sanitized_file(path, temp_path, source) for path in paths]
            return [self._write_sanitized_file(source, temp_path, self.parser.base_path)]
        if isinstance(source, list):
            return [self._write_sanitized_file(path, temp_path, self.parser.base_path) for path in source]

        input_files: list[Path] = []
        for index, item in enumerate(self.parser.iter_source):
            file_name = item.path.name if item.path.name.endswith(".proto") else f"input_{index}.proto"
            path = temp_path / file_name
            path.write_text(_sanitize_proto_source(item.text), encoding=self.parser.encoding)
            input_files.append(path)
        return input_files

    def _write_sanitized_file(self, source_path: Path, temp_path: Path, root: Path) -> Path:
        record_watch_dependency(source_path)
        if source_path.is_relative_to(root):
            target = temp_path / source_path.relative_to(root)
        else:  # pragma: no cover
            target = temp_path / source_path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            _sanitize_proto_source(source_path.read_text(encoding=self.parser.encoding)), encoding=self.parser.encoding
        )
        return target

    def _write_missing_weak_imports(self, input_files: Iterable[Path], include_paths: Sequence[Path]) -> None:
        assert self.weak_import_dir is not None
        weak_import_dir = self.weak_import_dir.resolve()
        for input_file in input_files:
            text = input_file.read_text(encoding=self.parser.encoding)
            syntax = 'syntax = "proto3";\n' if 'syntax = "proto3"' in text else 'syntax = "proto2";\n'
            for import_path in WEAK_IMPORT_PATTERN.findall(text):
                stub = (weak_import_dir / import_path).resolve()
                if not stub.is_relative_to(weak_import_dir):
                    msg = f"Invalid Protocol Buffers weak import path: {import_path!r}"
                    raise SchemaParseError(msg)
                if any((include_path / import_path).exists() for include_path in include_paths):
                    continue
                stub.parent.mkdir(parents=True, exist_ok=True)
                stub.write_text(syntax, encoding=self.parser.encoding)


class _ProtobufDescriptorConverter:
    def __init__(
        self,
        *,
        protobuf_version: ProtobufVersion | None,
        schema_version_mode: VersionMode | None,
        input_file_names: frozenset[str],
        source_safe_non_finite: bool,
    ) -> None:
        self.protobuf_version = protobuf_version
        self.schema_version_mode = schema_version_mode or VersionMode.Lenient
        self.input_file_names = input_file_names
        self.source_safe_non_finite = source_safe_non_finite
        self.definitions: dict[str, dict[str, Any]] = {}
        self.enums: dict[str, Any] = {}
        self.messages: dict[str, Any] = {}
        self.map_entries: dict[str, Any] = {}
        self.definition_keys: dict[str, str] = {}
        self.used_definition_keys: dict[str, str] = {}
        self._referenced_standard_types: dict[str, None] | None = None

    def convert(self, file_descriptor_set: Any) -> dict[str, Any]:
        for file_descriptor in file_descriptor_set.file:
            for is_enum, descriptor, full_name, _ in _declarations(file_descriptor):
                self._register_definition_key(full_name)
                symbols = self.enums if is_enum else self.map_entries if descriptor.options.map_entry else self.messages
                symbols[full_name] = descriptor

        for file_descriptor in file_descriptor_set.file:
            if file_descriptor.name in WELL_KNOWN_PROTO_PATHS and file_descriptor.name not in self.input_file_names:
                continue
            comments = _comment_map(file_descriptor)
            effective_version = self._effective_version(file_descriptor)
            for is_enum, descriptor, full_name, path in _declarations(file_descriptor, post_order=True):
                if is_enum:
                    self._convert_enum(descriptor, full_name, comments, path)
                else:
                    self._convert_message(descriptor, full_name, comments, path, effective_version)

        if referenced := self._referenced_standard_types:
            omitted_files = [
                file
                for file in file_descriptor_set.file
                if file.name in WELL_KNOWN_PROTO_PATHS and file.name not in self.input_file_names
            ]
            self._convert_referenced_standard_types(omitted_files, referenced)

        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Model",
            "definitions": self.definitions,
        }

    def _convert_referenced_standard_types(self, omitted_files: list[Any], referenced: dict[str, None]) -> None:
        locations = {
            full_name: (file_descriptor, path)
            for file_descriptor in omitted_files
            for _, _, full_name, path in _declarations(file_descriptor)
        }
        comments_by_file: dict[str, dict[tuple[int, ...], str]] = {}
        while referenced:
            pending_names = tuple(referenced)
            referenced.clear()
            for full_name in pending_names:
                if self._definition_key(full_name) in self.definitions:
                    continue
                file_descriptor, path = locations[full_name]
                if file_descriptor.name not in comments_by_file:
                    comments_by_file[file_descriptor.name] = _comment_map(file_descriptor)
                comments = comments_by_file[file_descriptor.name]
                if full_name in self.enums:
                    self._convert_enum(self.enums[full_name], full_name, comments, path)
                else:
                    self._convert_message(
                        self.messages[full_name], full_name, comments, path, self._effective_version(file_descriptor)
                    )

    def _reference_schema(self, full_name: str) -> dict[str, Any]:
        if full_name.startswith("google.protobuf."):
            if self._referenced_standard_types is None:
                self._referenced_standard_types = {}
            self._referenced_standard_types.setdefault(full_name, None)
        return {"$ref": f"#/definitions/{self._definition_key(full_name)}"}

    @staticmethod
    def _make_definition_key(full_name: str) -> str:
        return full_name.replace(".", "__")

    def _register_definition_key(self, full_name: str) -> None:
        key = self._make_definition_key(full_name)
        existing_full_name = self.used_definition_keys.get(key)
        if existing_full_name is not None and existing_full_name != full_name:
            key = f"{key}__{full_name.encode().hex()}"
        self.definition_keys[full_name] = key
        self.used_definition_keys[key] = full_name

    def _definition_key(self, full_name: str) -> str:
        return self.definition_keys.get(full_name, self._make_definition_key(full_name))

    def _effective_version(self, file_descriptor: Any) -> ProtobufVersion:
        declared = self._declared_version(file_descriptor)
        if self.protobuf_version is None or self.protobuf_version == ProtobufVersion.Auto:
            return declared
        if self.schema_version_mode == VersionMode.Strict and self.protobuf_version != declared:
            warn(
                f"Protobuf file {file_descriptor.name!r} declares {declared.value}, "
                f"but --schema-version {self.protobuf_version.value} was requested.",
                stacklevel=3,
            )
        return self.protobuf_version

    @staticmethod
    def _declared_version(file_descriptor: Any) -> ProtobufVersion:
        if file_descriptor.syntax == "proto3":
            return ProtobufVersion.Proto3
        if file_descriptor.syntax == "editions":
            return ProtobufVersion.Edition2023
        return ProtobufVersion.Proto2

    def _convert_enum(
        self,
        enum_descriptor: Any,
        full_name: str,
        comments: dict[tuple[int, ...], str],
        path: tuple[int, ...],
    ) -> None:
        values = [value.name for value in enum_descriptor.value]
        schema: dict[str, Any] = {"type": "string", "enum": values, "title": enum_descriptor.name}
        if comment := comments.get(path):
            schema["description"] = comment
        schema["x-enum-varnames"] = values
        value_comments = [comments.get((*path, 2, index), "") for index, _ in enumerate(enum_descriptor.value)]
        if any(value_comments):
            schema["x-enum-descriptions"] = value_comments
        self.definitions[self._definition_key(full_name)] = schema

    def _convert_message(
        self,
        message_descriptor: Any,
        full_name: str,
        comments: dict[tuple[int, ...], str],
        path: tuple[int, ...],
        syntax: ProtobufVersion,
    ) -> None:
        if message_descriptor.options.map_entry:
            return

        properties: dict[str, Any] = {}
        required: list[str] = []
        oneof_names = [oneof.name for oneof in message_descriptor.oneof_decl]
        synthetic_oneofs = {
            field.oneof_index
            for field in message_descriptor.field
            if field.proto3_optional and field.HasField("oneof_index")
        }
        real_oneof_fields: dict[int, list[str]] = {
            index: [] for index, _ in enumerate(message_descriptor.oneof_decl) if index not in synthetic_oneofs
        }
        for index, field in enumerate(message_descriptor.field):
            field_schema = self._field_schema(field, syntax)
            if comment := comments.get((*path, 2, index)):
                field_schema["description"] = comment
            if field.HasField("oneof_index") and field.oneof_index not in synthetic_oneofs:
                oneof_name = oneof_names[field.oneof_index]
                real_oneof_fields[field.oneof_index].append(field.name)
                oneof_note = f"oneof: {oneof_name}"
                field_schema["description"] = (
                    f"{field_schema['description']}\n{oneof_note}" if field_schema.get("description") else oneof_note
                )
            properties[field.name] = field_schema
            if field.label == LABEL_REQUIRED and syntax == ProtobufVersion.Proto2:
                required.append(field.name)

        schema: dict[str, Any] = {
            "type": "object",
            "title": message_descriptor.name,
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            schema["required"] = required
        if comment := comments.get(path):
            schema["description"] = comment
        self.definitions[self._definition_key(full_name)] = schema

    def _field_schema(self, field: Any, syntax: ProtobufVersion) -> dict[str, Any]:
        if field.label == LABEL_REPEATED:
            map_schema = self._map_field_schema(field)
            if map_schema is not None:
                return map_schema
            return {"type": "array", "items": self._single_field_schema(field), "default": []}

        schema = self._single_field_schema(field)
        if field.HasField("default_value"):
            schema["default"] = self._parse_default(field)
        elif self._uses_implicit_proto3_default(field, syntax):
            default = self._proto3_default(field)
            if default is not None:  # pragma: no branch
                schema["default"] = default
        return schema

    def _single_field_schema(self, field: Any) -> dict[str, Any]:
        if field.type in SCALAR_SCHEMAS:
            return dict(SCALAR_SCHEMAS[field.type])
        if field.type == TYPE_ENUM:
            return self._reference_schema(_type_name(field.type_name))
        if field.type in {TYPE_MESSAGE, TYPE_GROUP}:
            type_name = _type_name(field.type_name)
            if type_name in WELL_KNOWN_SCHEMAS:
                return deepcopy(WELL_KNOWN_SCHEMAS[type_name])
            return self._reference_schema(type_name)
        return {}  # pragma: no cover

    def _map_field_schema(self, field: Any) -> dict[str, Any] | None:
        if field.type != TYPE_MESSAGE:
            return None
        entry = self.map_entries.get(_type_name(field.type_name))
        if entry is None:
            return None
        key_field = next((item for item in entry.field if item.name == "key"), None)
        if key_field is None:  # pragma: no cover
            return None
        map_key_python_type = MAP_KEY_PYTHON_TYPES.get(key_field.type)
        if map_key_python_type is None:  # pragma: no cover
            msg = f"Protocol Buffers map field {field.name!r} uses unsupported key type"
            raise SchemaParseError(msg)
        value_field = next((item for item in entry.field if item.name == "value"), None)
        if value_field is None:  # pragma: no cover
            return None
        return {
            "type": "object",
            "propertyNames": {"type": "string", "x-python-type": map_key_python_type},
            "additionalProperties": self._single_field_schema(value_field),
            "default": {},
        }

    @staticmethod
    def _uses_implicit_proto3_default(field: Any, syntax: ProtobufVersion) -> bool:
        if syntax != ProtobufVersion.Proto3:
            return False
        if field.proto3_optional or field.HasField("oneof_index") or field.type == TYPE_MESSAGE:
            return False
        return field.type in SCALAR_OR_ENUM_TYPES

    def _proto3_default(self, field: Any) -> Any:
        if field.type == TYPE_ENUM:
            enum = self.enums.get(_type_name(field.type_name))
            return enum.value[0].name if enum and enum.value else None
        return PROTO2_DEFAULTS.get(field.type)

    def _parse_default(self, field: Any) -> Any:
        value = field.default_value
        if field.type == TYPE_BOOL:
            return value == "true"
        if field.type == TYPE_BYTES:
            from google.protobuf import text_encoding  # noqa: PLC0415

            return text_encoding.CUnescape(value)
        if field.type in {TYPE_DOUBLE, TYPE_FLOAT}:
            number = float(value)
            return _converted_float(number, source_safe_non_finite=self.source_safe_non_finite)
        if field.type in _INTEGER_TYPES:
            return int(value)
        return value


class ProtobufParser(JsonSchemaParser):
    """Parse Protocol Buffers schemas by converting descriptors to JSON Schema."""

    _config_class_name: ClassVar[str] = "ProtobufParserConfig"
    _cache_local_sources_during_parse: ClassVar[bool] = False
    _cache_parsed_sources_from_path: ClassVar[bool] = False

    def __init__(
        self,
        source: str | Path | list[Path] | ParseResult,
        *,
        config: ProtobufParserConfig | None = None,
        **options: Unpack[ProtobufParserConfigDict],
    ) -> None:
        """Initialize the Protobuf parser with JSON Schema parser configuration."""
        super().__init__(source=source, config=config, **options)

    def _compile_descriptor_set(self) -> tuple[Any, frozenset[str]]:
        protoc, well_known_include = _load_grpc_tools()
        descriptor_pb2 = _load_descriptor_pb2()
        with _ProtoInputPreparer(self) as (input_files, include_paths, input_file_names):
            if not input_files:
                msg = "No .proto files found in input"
                raise SchemaParseError(msg)
            with tempfile.NamedTemporaryFile(suffix=".pb", delete=False) as output_file:
                output_path = Path(output_file.name)
            collect_dependencies = _watch_dependency_collection_is_active()
            persistent_include_paths: tuple[Path, ...] = ()
            try:
                if collect_dependencies:
                    lexical_sources = self._lexical_source_files()
                    persistent_include_paths = self._persistent_include_paths(lexical_sources)
                    self._record_lexical_import_candidates(lexical_sources, persistent_include_paths)
                args = [
                    "grpc_tools.protoc",
                    *(f"-I{path}" for path in [*include_paths, well_known_include]),
                    "--include_imports",
                    "--include_source_info",
                    f"--descriptor_set_out={output_path}",
                    *(str(path) for path in input_files),
                ]
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    exit_code = protoc.main(args)
                if exit_code:
                    details = stderr.getvalue().strip()
                    msg = f"Invalid Protocol Buffers schema: {details or f'protoc exited with {exit_code}'}"
                    raise SchemaParseError(msg)
                descriptor_set = descriptor_pb2.FileDescriptorSet()
                descriptor_set.ParseFromString(output_path.read_bytes())
                if collect_dependencies:
                    self._record_descriptor_dependencies(descriptor_set, persistent_include_paths)
                return descriptor_set, input_file_names
            finally:
                with contextlib.suppress(OSError):
                    output_path.unlink()

    def _lexical_source_files(self) -> Sequence[Path]:
        """Return persistent local sources without protoc's temporary sanitized copies."""
        if isinstance(self.source, Path):
            return sorted(self.source.rglob("*.proto")) if self.source.is_dir() else [self.source]
        return self.source if isinstance(self.source, list) else ()

    def _persistent_include_paths(self, input_files: Sequence[Path]) -> tuple[Path, ...]:
        """Return lexical include roots that survive preparer cleanup."""
        return tuple(dict.fromkeys((self.base_path, *(path.parent for path in input_files))))

    def _record_lexical_import_candidates(self, input_files: Sequence[Path], include_paths: Sequence[Path]) -> None:
        """Depth-first record source imports before ``protoc`` can reject a nested missing file."""
        candidate_roots = tuple(dict.fromkeys(path.resolve(strict=False) for path in include_paths))
        pending_files = [path.resolve(strict=False) for path in input_files]
        visited_files: set[Path] = set()
        while pending_files:
            source_path = pending_files.pop()
            if source_path in visited_files:
                continue
            visited_files.add(source_path)
            try:
                text = source_path.read_text(encoding=self.config.encoding)
            except OSError:
                continue
            source_root = source_path.parent
            source_candidate_roots = (
                candidate_roots if source_root in candidate_roots else (source_root, *candidate_roots)
            )
            for import_path in IMPORT_PATTERN.findall(text):
                candidates = tuple(
                    (candidate_root / import_path).resolve(strict=False) for candidate_root in source_candidate_roots
                )
                existing_candidate = next((candidate for candidate in candidates if candidate.is_file()), None)
                if existing_candidate is not None:
                    record_watch_dependency(existing_candidate)
                    pending_files.append(existing_candidate)
                else:
                    for candidate in candidates:
                        record_watch_dependency(candidate)

    @staticmethod
    def _record_descriptor_dependencies(descriptor_set: Any, include_paths: Sequence[Path]) -> None:
        """Record local ``protoc`` imports without retaining descriptor contents."""
        for descriptor in descriptor_set.file:
            for include_path in include_paths:
                candidate = include_path / descriptor.name
                if candidate.is_file():
                    record_watch_dependency(candidate)
                    break

    def convert_to_json_schema_data(self) -> dict[str, Any]:
        """Convert Protocol Buffers input sources into JSON Schema data."""
        return self._convert_to_json_schema_data(source_safe_non_finite=False)

    def _convert_to_json_schema_data(self, *, source_safe_non_finite: bool) -> dict[str, Any]:
        """Convert Protocol Buffers input sources for an internal parser consumer."""
        config = cast("ProtobufParserConfig", self.config)
        descriptor_set, input_file_names = self._compile_descriptor_set()
        converter = _ProtobufDescriptorConverter(
            protobuf_version=config.protobuf_version,
            schema_version_mode=config.schema_version_mode,
            input_file_names=input_file_names,
            source_safe_non_finite=source_safe_non_finite,
        )
        return converter.convert(descriptor_set)

    def parse_raw(self) -> None:
        """Parse all Protocol Buffers input sources into data models."""
        with self._parsing_session():
            raw_obj = self._convert_to_json_schema_data(source_safe_non_finite=True)
            source = next(self.iter_source)
            source.raw_data = raw_obj
            self.raw_obj = raw_obj
            self._parse_file(raw_obj, "Model", [])


__all__ = ["ProtobufParser", "convert_protobuf_schema_data"]
