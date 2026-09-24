"""Apache Avro schema parser implementation.

Converts Avro JSON schemas into the JSON Schema shape consumed by the existing
JSON Schema parser while preserving Avro named type and logical type semantics.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, cast

from typing_extensions import Unpack

from datamodel_code_generator import Error
from datamodel_code_generator._avro_detection import COMPLEX_TYPES as _COMPLEX_TYPES
from datamodel_code_generator._avro_detection import JSON_SCHEMA_MARKER_KEYS as _JSON_SCHEMA_MARKER_KEYS
from datamodel_code_generator._avro_detection import NAMED_TYPES, PRIMITIVE_TYPES
from datamodel_code_generator._avro_detection import (
    is_avro_schema_data as _is_avro_schema_data,
)
from datamodel_code_generator._source import YamlValue, load_yaml
from datamodel_code_generator.imports import Import
from datamodel_code_generator.parser._convert_common import _copy_schema, _namespace_name, _unique_name
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from urllib.parse import ParseResult

    from datamodel_code_generator._types import AvroParserConfigDict
    from datamodel_code_generator.config import AvroParserConfig
    from datamodel_code_generator.parser.base import ModuleContext, Source

JsonSchema = dict[str, Any]

NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MICROSECONDS_PER_DAY = 86_400_000_000
DURATION_BYTE_LENGTH = 12
_PHYSICAL_DEFAULT_OVERRIDES = frozenset({
    Import.from_full_path("builtins.int"),
    Import.from_full_path("builtins.bytes"),
})

STRING_SCHEMA: JsonSchema = {"type": "string"}
NULL_SCHEMA: JsonSchema = {"type": "null"}
BOOLEAN_SCHEMA: JsonSchema = {"type": "boolean"}
BYTES_SCHEMA: JsonSchema = {"type": "string", "format": "binary"}

PRIMITIVE_SCHEMAS: dict[str, JsonSchema] = {
    "null": NULL_SCHEMA,
    "boolean": BOOLEAN_SCHEMA,
    "int": {"type": "integer", "format": "int32"},
    "long": {"type": "integer", "format": "int64"},
    "float": {"type": "number", "format": "float"},
    "double": {"type": "number", "format": "double"},
    "bytes": BYTES_SCHEMA,
    "string": STRING_SCHEMA,
}


def is_avro_schema_data(data: YamlValue) -> bool:
    """Return whether loaded data appears to be an Avro schema."""
    return _is_avro_schema_data(data)


def __getattr__(name: str) -> Any:
    """Return compatibility constants moved to the lightweight detector."""
    match name:
        case "COMPLEX_TYPES":
            return _COMPLEX_TYPES
        case "JSON_SCHEMA_MARKER_KEYS":
            return _JSON_SCHEMA_MARKER_KEYS
    raise AttributeError(name)


class _Name(NamedTuple):
    fullname: str
    namespace: str | None
    name: str


def _to_class_title(name: str) -> str:
    return f"{name[:1].upper()}{name[1:]}"


def _is_valid_name(name: str) -> bool:
    return bool(NAME_PATTERN.fullmatch(name))


def _is_valid_fullname(name: str) -> bool:
    return all(_is_valid_name(part) for part in name.split("."))


def _is_valid_namespace(namespace: str) -> bool:
    return not namespace or _is_valid_fullname(namespace)


def _logical_default_expression(kind: str, value: str) -> Any:
    """Build a logical constructor with an import identity that can be aliased."""
    from datamodel_code_generator.python_literal import (  # ruff: ignore[import-outside-top-level]
        PythonRuntimeExpression,
    )

    if kind == "decimal":
        return PythonRuntimeExpression.from_import_call(Import(from_="decimal", import_="Decimal"), repr(value))
    if kind == "timedelta":
        return PythonRuntimeExpression.from_import_call(
            Import(from_="datetime", import_="timedelta"), f"milliseconds={value}"
        )
    return PythonRuntimeExpression(
        Import(import_="datetime", alias="datetime_module"), "", f".{kind}.fromisoformat({value!r})"
    )


class _AvroSchemaConverter:
    def __init__(
        self,
        logical_default: Callable[[str, str, str], Any] | None = None,
        *,
        convert_logical_defaults: bool = True,
        logical_default_enabled: Callable[[str, str, Import | None], bool] | None = None,
        default_type_overrides: Callable[[str, JsonSchema, bool], dict[str, Import]] | None = None,
        default_converters: dict[str, _AvroSchemaConverter] | None = None,
    ) -> None:
        self.named_schemas: dict[str, JsonSchema] = {}
        self.names: dict[str, _Name] = {}
        self.definition_names: dict[str, str] = {}
        self.definitions: dict[str, JsonSchema] = {}
        self._building_definitions: set[str] = set()
        self._logical_default = logical_default
        self._convert_logical_defaults = convert_logical_defaults
        self._logical_default_enabled = logical_default_enabled
        self._default_type_overrides = default_type_overrides
        self._default_converters = default_converters
        self.overridden_defaults: dict[str, dict[str, tuple[JsonSchema, Any]]] | None = (
            {} if default_converters is not None else None
        )

    def convert_raw(self, raw_obj: YamlValue) -> dict[str, YamlValue]:
        self._collect_named_schemas(raw_obj)
        self._prepare_definition_names()
        schema = self._convert_schema(raw_obj, namespace=None, root=True)
        schema.setdefault("title", "Model")
        if self.definitions:
            schema["definitions"] = self.definitions
        schema.setdefault("$schema", "http://json-schema.org/draft-07/schema#")
        return cast("dict[str, YamlValue]", schema)

    def convert(self, source: Source) -> dict[str, YamlValue]:
        if self._default_converters is not None:
            self._default_converters[source.path.as_posix()] = self
            self._default_converters = None
        raw_obj = source.raw_data if source.raw_data is not None else load_yaml(source.text)
        return self.convert_raw(raw_obj)

    def _collect_named_schemas(self, schema: YamlValue, namespace: str | None = None) -> None:
        match schema:
            case [*_] as union:
                self._collect_schema_items(cast("list[YamlValue]", union), namespace)
                return
            case dict() as schema:
                pass
            case _:
                return

        type_value = schema.get("type")
        match type_value:
            case [*_] as union:
                self._collect_schema_items(cast("list[YamlValue]", union), namespace)
                return
            case dict() as nested_schema:
                self._collect_named_schemas(nested_schema, namespace)
                return
            case str() as type_name:
                child_namespace = self._register_named_schema(schema, type_name, namespace)
                self._collect_schema_children(schema, type_name, child_namespace)

    def _collect_schema_items(self, schemas: list[YamlValue], namespace: str | None) -> None:
        for schema in schemas:
            self._collect_named_schemas(schema, namespace)

    def _register_named_schema(self, schema: JsonSchema, type_name: str, namespace: str | None) -> str | None:
        if type_name not in NAMED_TYPES:
            return namespace

        name = schema.get("name")
        if not isinstance(name, str):
            msg = f"Avro {type_name} schema requires a string name"
            raise Error(msg)
        if not _is_valid_fullname(name):
            msg = f"Invalid Avro {type_name} name: {name}"
            raise Error(msg)
        if name.rsplit(".", maxsplit=1)[-1] in PRIMITIVE_TYPES:
            msg = f"Avro primitive type names may not be redefined: {name}"
            raise Error(msg)
        namespace_value = schema.get("namespace")
        if "." not in name and isinstance(namespace_value, str) and not _is_valid_namespace(namespace_value):
            msg = f"Invalid Avro namespace: {namespace_value}"
            raise Error(msg)
        name_info = self._make_name(name, schema.get("namespace"), namespace)
        if (existing := self.named_schemas.get(name_info.fullname)) is not None and existing is not schema:
            msg = f"Duplicate Avro named type: {name_info.fullname}"
            raise Error(msg)
        self.named_schemas[name_info.fullname] = schema
        self.names[name_info.fullname] = name_info
        return name_info.namespace

    def _collect_schema_children(self, schema: JsonSchema, type_name: str, namespace: str | None) -> None:
        match type_name:
            case "record":
                fields = schema.get("fields", [])
                if not isinstance(fields, list):
                    msg = f"Avro record fields must be a list: {schema.get('name')}"
                    raise Error(msg)
                for field in fields:
                    if isinstance(field, dict):
                        self._collect_named_schemas(field.get("type"), namespace)
            case "array":
                self._collect_named_schemas(schema.get("items"), namespace)
            case "map":
                self._collect_named_schemas(schema.get("values"), namespace)

    def _prepare_definition_names(self) -> None:
        fullnames_by_local: dict[str, list[str]] = {}
        for fullname, name_info in self.names.items():
            fullnames_by_local.setdefault(name_info.name.rsplit(".", maxsplit=1)[-1], []).append(fullname)

        used_names: set[str] = set()
        for local, fullnames in sorted(fullnames_by_local.items()):
            for fullname in sorted(fullnames):
                name_info = self.names[fullname]
                name = (
                    _to_class_title(local)
                    if len(fullnames) == 1
                    else f"{_namespace_name(name_info.namespace, _to_class_title)}{_to_class_title(local)}"
                )
                candidate = _unique_name(name, used_names)
                self.definition_names[fullname] = candidate
                used_names.add(candidate)

    def _convert_schema(
        self,
        schema: YamlValue,
        namespace: str | None,
        *,
        root: bool = False,
    ) -> JsonSchema:
        match schema:
            case str() as type_name:
                return self._convert_type_name(type_name, namespace)
            case [*_] as union:
                return self._convert_union(cast("list[YamlValue]", union), namespace)
            case dict() as schema:
                return self._convert_schema_dict(schema, namespace, root=root)

        msg = f"Unsupported Avro schema value: {schema!r}"
        raise Error(msg)

    def _convert_schema_dict(
        self,
        schema: JsonSchema,
        namespace: str | None,
        *,
        root: bool,
    ) -> JsonSchema:
        match schema.get("type"):
            case [*_] as union:
                converted = self._convert_union(cast("list[YamlValue]", union), namespace)
                self._copy_common_metadata(schema, converted)
                return converted
            case dict() as nested_schema:
                converted = self._convert_schema(nested_schema, namespace, root=root)
                self._copy_common_metadata(schema, converted)
                return converted
            case str() as type_name:
                return self._convert_schema_object(schema, type_name, namespace, root=root)
            case _:
                msg = f"Avro schema object requires a string, object, or union type: {schema!r}"
                raise Error(msg)

    def _convert_schema_object(
        self,
        schema: JsonSchema,
        type_name: str,
        namespace: str | None,
        *,
        root: bool,
    ) -> JsonSchema:
        match type_name:
            case type_name if type_name in PRIMITIVE_TYPES:
                converted = _copy_schema(PRIMITIVE_SCHEMAS[type_name])
                self._copy_common_metadata(schema, converted)
                return self._apply_logical_type(schema, converted, avro_type=type_name)
            case "record" | "enum" | "fixed":
                fullname = self._fullname_from_named_schema(schema, namespace)
                if root:
                    return self._build_definition(fullname, as_root=True)
                self._ensure_definition(fullname)
                return {"$ref": self._ref(fullname)}
            case "array":
                converted = {"type": "array", "items": self._convert_schema(schema.get("items"), namespace)}
                self._copy_common_metadata(schema, converted)
                return converted
            case "map":
                converted = {
                    "type": "object",
                    "additionalProperties": self._convert_schema(schema.get("values"), namespace),
                }
                self._copy_common_metadata(schema, converted)
                return converted
        return self._convert_type_name(type_name, namespace)

    def _convert_type_name(self, name: str, namespace: str | None) -> JsonSchema:
        if name in PRIMITIVE_TYPES:
            return _copy_schema(PRIMITIVE_SCHEMAS[name])
        fullname = self._resolve_fullname(name, namespace)
        self._ensure_definition(fullname)
        return {"$ref": self._ref(fullname)}

    def _convert_union(self, union: list[YamlValue], namespace: str | None) -> JsonSchema:
        self._validate_union(union)
        return {"anyOf": [self._convert_schema(item, namespace) for item in union]}

    @staticmethod
    def _validate_union(union: list[YamlValue]) -> None:
        seen_unnamed_types: set[str] = set()
        for item in union:
            if isinstance(item, list):
                msg = "Avro unions may not immediately contain other unions"
                raise Error(msg)
            if isinstance(item, str):
                union_type = item if item in PRIMITIVE_TYPES else None
            elif isinstance(item, dict):
                type_value = item.get("type")
                if isinstance(type_value, list):
                    msg = "Avro unions may not immediately contain other unions"
                    raise Error(msg)
                union_type = type_value if isinstance(type_value, str) and type_value not in NAMED_TYPES else None
            else:
                msg = f"Unsupported Avro union value: {item!r}"
                raise Error(msg)

            if union_type is None or union_type in NAMED_TYPES:
                continue
            if union_type in seen_unnamed_types:
                msg = f"Avro unions may not contain duplicate unnamed type: {union_type}"
                raise Error(msg)
            seen_unnamed_types.add(union_type)

    def _ensure_definition(self, fullname: str) -> None:
        definition_key = self.definition_names.get(fullname)
        if (
            definition_key is None or definition_key not in self.definitions
        ) and fullname not in self._building_definitions:
            self._build_definition(fullname)

    def _build_definition(self, fullname: str, *, as_root: bool = False) -> JsonSchema:
        raw_schema = self.named_schemas.get(fullname)
        if raw_schema is None:
            msg = f"Unknown Avro named type reference: {fullname}"
            raise Error(msg)
        definition_key = self.definition_names[fullname]

        self._building_definitions.add(fullname)
        type_value = raw_schema.get("type")
        if type_value == "record":
            converted = self._convert_record(raw_schema, fullname, as_root=as_root)
        elif type_value == "enum":
            converted = self._convert_enum(raw_schema, fullname)
        elif type_value == "fixed":
            converted = self._convert_fixed(raw_schema, fullname)
        else:  # pragma: no cover
            msg = f"Unsupported Avro named type: {type_value!r}"
            raise Error(msg)
        converted.setdefault("title", definition_key)
        self._building_definitions.remove(fullname)
        self.definitions[definition_key] = converted
        return _copy_schema(converted) if as_root else converted

    def _convert_record(self, schema: JsonSchema, fullname: str, *, as_root: bool = False) -> JsonSchema:
        name_info = self.names[fullname]
        fields = schema.get("fields", [])
        overrides = (
            self._default_type_overrides(self.definition_names[fullname], schema, as_root)
            if self._default_type_overrides is not None
            else None
        )

        properties: dict[str, JsonSchema] = {}
        required: list[str] = []
        for field in fields:
            if not isinstance(field, dict) or not isinstance(field.get("name"), str):
                msg = f"Avro record field requires a string name: {fullname}"
                raise Error(msg)
            field_name = cast("str", field["name"])
            if not _is_valid_name(field_name):
                msg = f"Invalid Avro record field name: {fullname}.{field_name}"
                raise Error(msg)
            if field_name in properties:
                msg = f"Duplicate Avro record field name: {fullname}.{field_name}"
                raise Error(msg)
            field_schema = self._convert_schema(field.get("type"), name_info.namespace)
            if "doc" in field and isinstance(field["doc"], str):
                field_schema["description"] = field["doc"]
            self._copy_aliases(field, field_schema)
            if "order" in field:
                field_schema["x-avro-order"] = field["order"]
            if "default" in field:
                field_schema["default"] = self._convert_default(
                    field["default"],
                    field.get("type"),
                    name_info.namespace,
                    overrides.get(field_name) if overrides else None,
                )
                if (
                    self.overridden_defaults is not None
                    and overrides
                    and overrides.get(field_name) in _PHYSICAL_DEFAULT_OVERRIDES
                ):
                    self.overridden_defaults.setdefault(fullname, {})[field_name] = field, field_schema["default"]
            else:
                required.append(field_name)
            properties[field_name] = field_schema

        converted: JsonSchema = {"type": "object", "properties": properties}
        if required:
            converted["required"] = required
        self._copy_common_metadata(schema, converted)
        converted["x-avro-name"] = name_info.name
        if name_info.namespace:
            converted["x-avro-namespace"] = name_info.namespace
        converted["x-avro-fullname"] = fullname
        return converted

    def _convert_default(
        self, value: Any, schema: Any, namespace: str | None, type_override: Import | None = None
    ) -> Any:
        """Decode defaults using the Avro schema's physical and logical types."""
        if not isinstance(value, str | bytes | list | dict):
            return (
                self._convert_integer_default(value, schema, type_override)
                if type(value) is int and self._convert_logical_defaults
                else value
            )
        while isinstance(schema, list | dict):
            match schema:
                case [first, *_]:
                    schema = first
                case {"type": "array", "items": item_schema} if isinstance(value, list):
                    converted = value
                    for index, item in enumerate(value):
                        if (
                            converted_item := self._convert_default(item, item_schema, namespace, type_override)
                        ) is not item:
                            if converted is value:
                                converted = value.copy()
                            converted[index] = converted_item
                    return converted
                case {"type": "map" | "record"} if isinstance(value, dict):
                    return self._convert_default_mapping(value, schema, namespace, type_override)
                case {"type": "bytes" | "fixed", "logicalType": "decimal"} | {
                    "type": "fixed",
                    "logicalType": "duration",
                } if self._convert_logical_defaults:
                    return self._convert_logical_bytes_default(value, schema, namespace, type_override)
                case {"type": nested_schema}:
                    schema = nested_schema
                case _:
                    return value
        return self._convert_default_type(value, schema, namespace, type_override)

    def _convert_integer_default(self, value: int, schema: Any, type_override: Import | None = None) -> Any:
        """Inspect only integer defaults for temporal logical types."""
        while isinstance(schema, list | dict):
            match schema:
                case [first, *_]:
                    schema = first
                case {"type": "int" | "long" as avro_type, "logicalType": str() as logical_type}:
                    return self._convert_temporal_default(value, avro_type, logical_type, type_override)
                case {"type": nested_schema}:
                    schema = nested_schema
                case _:
                    return value
        return value

    def _convert_temporal_default(
        self, value: int, avro_type: str, logical_type: str, type_override: Import | None = None
    ) -> Any:
        """Preserve temporal units and timezone semantics without rounding."""
        match avro_type, logical_type:
            case "int", "date":
                kind = "date"
            case ("int", "time-millis") | ("long", "time-micros"):
                kind = "time"
            case "long", (
                "timestamp-millis"
                | "timestamp-micros"
                | "timestamp-nanos"
                | "local-timestamp-millis"
                | "local-timestamp-micros"
                | "local-timestamp-nanos"
            ):
                kind = "datetime"
            case _:
                return value

        if self._logical_default_enabled is not None and not self._logical_default_enabled(
            kind, logical_type, type_override
        ):
            return value

        from datetime import datetime, timedelta, timezone  # ruff: ignore[import-outside-top-level]

        microseconds = self._temporal_microseconds(value, logical_type)
        if kind == "time" and not 0 <= microseconds < MICROSECONDS_PER_DAY:
            msg = f"Avro {logical_type} default must be within a single day: {value}"
            raise Error(msg)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc if logical_type.startswith("timestamp-") else None)
        try:
            converted = epoch + (timedelta(days=value) if kind == "date" else timedelta(microseconds=microseconds))
        except OverflowError as exc:
            msg = f"Avro {logical_type} default is outside the Python {kind} range: {value}"
            raise Error(msg) from exc

        iso_value = (getattr(converted, kind)() if kind != "datetime" else converted).isoformat()
        if self._logical_default is not None:
            return self._logical_default(kind, logical_type, iso_value)
        return _logical_default_expression(kind, iso_value)

    @staticmethod
    def _temporal_microseconds(value: int, logical_type: str) -> int:
        """Scale an integer timestamp exactly to Python's microsecond precision."""
        if logical_type.endswith("-millis"):
            return value * 1000
        if logical_type.endswith("-nanos"):
            microseconds, remainder = divmod(value, 1000)
            if remainder:
                msg = f"Avro {logical_type} default cannot be represented exactly at microsecond precision: {value}"
                raise Error(msg)
            return microseconds
        return value

    def _convert_default_type(
        self, value: Any, schema: Any, namespace: str | None, type_override: Import | None = None
    ) -> Any:
        """Resolve named defaults in their record scope and decode bytes leaves."""
        if not isinstance(schema, str):  # pragma: no cover - rejected while converting the field schema
            return value
        if schema not in {"bytes", "fixed"}:
            fullname = self._resolve_fullname(schema, namespace)
            if (named_schema := self.named_schemas.get(fullname)) is None:
                return value
            if named_schema.get("type") == "record" and isinstance(value, dict):
                return self._convert_default_mapping(value, named_schema, namespace, type_override)
            if named_schema.get("type") != "fixed":
                return value
            if named_schema.get("logicalType") in {"decimal", "duration"} and self._convert_logical_defaults:
                return self._convert_logical_bytes_default(value, named_schema, namespace, type_override)
        return self._decode_bytes_default(value)

    @staticmethod
    def _decode_bytes_default(value: Any) -> Any:
        """Map Avro default code points to their original byte values."""
        if not isinstance(value, str):
            return value
        try:
            return value.encode("latin-1")
        except UnicodeEncodeError as exc:
            msg = "Avro bytes and fixed defaults must contain only code points from 0 through 255"
            raise Error(msg) from exc

    def _convert_logical_bytes_default(
        self, value: Any, schema: JsonSchema, namespace: str | None, type_override: Import | None = None
    ) -> Any:
        """Decode decimal and duration defaults using their distinct byte layouts."""
        value = self._decode_bytes_default(value)
        if type_override is None and self._default_type_overrides is not None and schema["type"] == "fixed":
            fullname = self._fullname_from_named_schema(schema, namespace)
            type_override = self._default_type_overrides(self.definition_names[fullname], schema, False).get("")  # ruff: ignore[boolean-positional-value-in-call]
        if self._logical_default_enabled is not None and not self._logical_default_enabled(
            "timedelta" if schema["logicalType"] == "duration" else "decimal", schema["logicalType"], type_override
        ):
            if type_override in _PHYSICAL_DEFAULT_OVERRIDES and isinstance(value, bytes):
                from datamodel_code_generator.python_literal import PythonCode  # ruff: ignore[import-outside-top-level]

                # This is an intentional physical literal, including in shared Avro definitions.
                return PythonCode(repr(value))
            return value
        if schema["logicalType"] == "duration":
            return self._convert_duration_default(value, schema)
        precision = schema.get("precision")
        scale = schema.get("scale", 0)
        if (
            not isinstance(value, bytes)
            or type(precision) is not int
            or type(scale) is not int
            or not 0 <= scale <= precision
            or precision == 0
        ):
            return value

        from decimal import MIN_ETINY, Decimal  # ruff: ignore[import-outside-top-level]

        if scale > -MIN_ETINY:
            msg = f"Avro decimal default scale is outside the Python Decimal range: {scale}"
            raise Error(msg)
        if schema.get("type") == "fixed" and len(value) != schema["size"]:
            msg = f"Avro fixed decimal default must contain exactly {schema['size']} bytes: {len(value)}"
            raise Error(msg)
        coefficient = Decimal(int.from_bytes(value, "big", signed=True))
        if coefficient.adjusted() >= precision:
            msg = f"Avro decimal default exceeds precision {precision}: {coefficient}"
            raise Error(msg)
        decimal_value = f"{coefficient}E-{scale}"
        if self._logical_default is not None:
            return self._logical_default("decimal", "decimal", decimal_value)
        return _logical_default_expression("decimal", decimal_value)

    def _convert_duration_default(self, value: Any, schema: JsonSchema) -> Any:
        """Preserve fixed milliseconds without approximating calendar components."""
        if not isinstance(value, bytes):
            return value
        if schema.get("size") != DURATION_BYTE_LENGTH or len(value) != DURATION_BYTE_LENGTH:
            msg = "Avro duration default requires a fixed size of 12 and exactly 12 encoded bytes"
            raise Error(msg)
        months = int.from_bytes(value[:4], "little")
        days = int.from_bytes(value[4:8], "little")
        if months or days:
            msg = (
                "Avro duration default with calendar components cannot be represented by timedelta: "
                f"months={months}, days={days}"
            )
            raise Error(msg)
        milliseconds = str(int.from_bytes(value[8:], "little"))
        if self._logical_default is not None:
            return self._logical_default("timedelta", "duration", milliseconds)
        return _logical_default_expression("timedelta", milliseconds)

    def _convert_default_mapping(
        self, value: dict[str, Any], schema: JsonSchema, namespace: str | None, type_override: Import | None = None
    ) -> Any:
        """Decode mapping leaves without changing the input values or their order."""
        field_types = None
        overrides = None
        if schema["type"] == "record":
            fullname = self._fullname_from_named_schema(schema, namespace)
            namespace = self.names[fullname].namespace
            if self._default_type_overrides is not None:
                overrides = self._default_type_overrides(self.definition_names[fullname], schema, False)  # ruff: ignore[boolean-positional-value-in-call]
            field_types = {
                field["name"]: field.get("type")
                for field in schema.get("fields", [])
                if isinstance(field, dict) and isinstance(field.get("name"), str)
            }
        converted = value
        for name, item in value.items():
            item_schema = field_types.get(name) if field_types is not None else schema.get("values")
            if (
                converted_item := self._convert_default(
                    item, item_schema, namespace, overrides.get(name, type_override) if overrides else type_override
                )
            ) is not item:
                if converted is value:
                    converted = value.copy()
                converted[name] = converted_item
        return converted

    def _convert_enum(self, schema: JsonSchema, fullname: str) -> JsonSchema:
        symbols = schema.get("symbols")
        if not isinstance(symbols, list) or not all(isinstance(symbol, str) for symbol in symbols):
            msg = f"Avro enum symbols must be a list of strings: {fullname}"
            raise Error(msg)
        seen_symbols: set[str] = set()
        for symbol in symbols:
            if not _is_valid_name(symbol):
                msg = f"Invalid Avro enum symbol: {fullname}.{symbol}"
                raise Error(msg)
            if symbol in seen_symbols:
                msg = f"Duplicate Avro enum symbol: {fullname}.{symbol}"
                raise Error(msg)
            seen_symbols.add(symbol)
        converted: JsonSchema = {"type": "string", "enum": list(symbols)}
        self._copy_common_metadata(schema, converted)
        converted["x-avro-fullname"] = fullname
        return converted

    def _convert_fixed(self, schema: JsonSchema, fullname: str) -> JsonSchema:
        size = schema.get("size")
        if not isinstance(size, int):
            msg = f"Avro fixed size must be an integer: {fullname}"
            raise Error(msg)
        converted: JsonSchema = {
            "type": "string",
            "format": "binary",
            "minLength": size,
            "maxLength": size,
            "x-avro-fullname": fullname,
        }
        self._copy_common_metadata(schema, converted)
        return self._apply_logical_type(schema, converted, avro_type="fixed")

    @staticmethod
    def _copy_common_metadata(source: JsonSchema, target: JsonSchema) -> None:
        if isinstance(doc := source.get("doc"), str):
            target["description"] = doc
        _AvroSchemaConverter._copy_aliases(source, target)
        if isinstance(logical_type := source.get("logicalType"), str):
            target["x-avro-logicalType"] = logical_type

    @staticmethod
    def _copy_aliases(source: JsonSchema, target: JsonSchema) -> None:
        if "aliases" not in source:
            return
        aliases = source["aliases"]
        target["x-avro-aliases"] = list(aliases) if isinstance(aliases, list) else aliases

    def _apply_logical_type(self, source: JsonSchema, target: JsonSchema, *, avro_type: str) -> JsonSchema:
        logical_type = source.get("logicalType")
        if not isinstance(logical_type, str):
            return target

        updates: JsonSchema | None = None
        match logical_type:
            case "decimal" if avro_type in {"bytes", "fixed"}:
                updates = self._decimal_schema(source)
                updates["x-avro-logicalType"] = logical_type
            case "big-decimal" if avro_type == "bytes":
                updates = self._decimal_schema(source)
                updates["x-avro-logicalType"] = logical_type
            case "uuid" if avro_type in {"string", "fixed"}:
                updates = {"type": "string", "format": "uuid", "x-avro-logicalType": logical_type}
            case "date" if avro_type == "int":
                updates = {"type": "string", "format": "date", "x-avro-logicalType": logical_type}
            case "time-millis" if avro_type == "int":
                updates = {"type": "string", "format": "time", "x-avro-logicalType": logical_type}
            case "time-micros" if avro_type == "long":
                updates = {"type": "string", "format": "time", "x-avro-logicalType": logical_type}
            case "timestamp-millis" | "timestamp-micros" | "timestamp-nanos" if avro_type == "long":
                updates = {"type": "string", "format": "date-time", "x-avro-logicalType": logical_type}
            case "local-timestamp-millis" | "local-timestamp-micros" | "local-timestamp-nanos" if avro_type == "long":
                updates = {"type": "string", "format": "date-time-local", "x-avro-logicalType": logical_type}
            case "duration" if avro_type == "fixed":
                updates = {"type": "string", "format": "duration", "x-avro-logicalType": logical_type}
            case _:
                return target
        converted = _copy_schema(target)
        converted.update(updates)
        if avro_type == "fixed":
            converted.pop("minLength", None)
            converted.pop("maxLength", None)
        return converted

    @staticmethod
    def _decimal_schema(source: JsonSchema) -> JsonSchema:
        converted: JsonSchema = {"type": "string", "format": "decimal", "x-avro-logicalType": "decimal"}
        if isinstance(source.get("precision"), int):
            converted["x-avro-precision"] = source["precision"]
        if isinstance(source.get("scale"), int):
            converted["x-avro-scale"] = source["scale"]
        return converted

    def _fullname_from_named_schema(self, schema: JsonSchema, namespace: str | None) -> str:
        name = schema.get("name")
        assert isinstance(name, str)
        return self._make_name(name, schema.get("namespace"), namespace).fullname

    @staticmethod
    def _make_name(name: str, namespace: Any, enclosing_namespace: str | None) -> _Name:
        if "." in name:
            fullname = name
            resolved_namespace = name.rsplit(".", maxsplit=1)[0]
        else:
            resolved_namespace = namespace if isinstance(namespace, str) else enclosing_namespace
            fullname = f"{resolved_namespace}.{name}" if resolved_namespace else name
        return _Name(fullname=fullname, namespace=resolved_namespace, name=name)

    def _resolve_fullname(self, name: str, namespace: str | None) -> str:
        """Prefer the current namespace when resolving an unqualified name."""
        if "." in name:
            return name
        if namespace and (namespaced := f"{namespace}.{name}") in self.named_schemas:
            return namespaced
        return name

    def _ref(self, fullname: str) -> str:
        return f"#/definitions/{self.definition_names[fullname]}"


class AvroParser(JsonSchemaParser):
    """Parse Apache Avro schemas with the existing JSON Schema model builder.

    Avro is converted before parsing, but the generated models still rely on
    JsonSchemaParser's reference resolution, model construction, formatting,
    and configuration surface. The Avro-specific state is kept in a short-lived
    converter per source so named type resolution cannot leak between inputs.
    """

    _config_class_name = "AvroParserConfig"
    _cache_parsed_sources_from_path: ClassVar[bool] = False

    def __init__(
        self,
        source: str | Path | list[Path] | ParseResult,
        *,
        config: AvroParserConfig | None = None,
        **options: Unpack[AvroParserConfigDict],
    ) -> None:
        """Initialize the Avro parser with JSON Schema parser configuration."""
        super().__init__(source=source, config=config, **options)

    def parse_raw(self) -> None:
        """Parse all Avro schema input sources into data models."""
        self._default_converters: dict[str, _AvroSchemaConverter] | None = {} if self._type_override_imports else None
        self._parse_converted_sources(
            lambda: _AvroSchemaConverter(
                self._logical_default,
                convert_logical_defaults=self.data_model_type.SUPPORTS_DESERIALIZED_DEFAULT_VALUES,
                logical_default_enabled=self._logical_default_enabled
                if self.type_mappings or self._type_override_imports
                else None,
                default_type_overrides=self._default_type_overrides if self._type_override_imports else None,
                default_converters=self._default_converters,
            )
        )
        if self._has_runtime_expressions:
            from datamodel_code_generator.python_literal import (  # ruff: ignore[import-outside-top-level]
                runtime_expression_imports,
            )

            for model in self.results:
                for field in model.fields:
                    if imports := runtime_expression_imports(field.default):
                        field._set_runtime_expression_imports(imports)  # ruff: ignore[private-member-access]

    def _finalize_structured_imports(self, contexts: list[ModuleContext]) -> None:
        """Keep shared logical defaults aligned with the final non-overridden fields."""
        if self._default_converters:
            from datamodel_code_generator.python_literal import (  # ruff: ignore[import-outside-top-level]
                runtime_expression_imports,
            )

            for context in contexts:
                for model in context.models:
                    converter = self._default_converters.get(model.reference.path.partition("#")[0] or ".")
                    fullname = model.extra_template_data.get("extensions", {}).get("x-avro-fullname")
                    if converter is None or not converter.overridden_defaults:
                        continue
                    defaults = converter.overridden_defaults.get(fullname, {})
                    for field in model.fields:
                        candidate = defaults.get(field.original_name or "")
                        if (
                            candidate is None
                            or f"{model.class_name}.{field.name}" in self._type_override_imports
                            or field.data_type.import_ in self._model_type_override_imports.values()
                        ):
                            continue
                        raw_field, physical_default = candidate
                        _, has_default_override = self.model_resolver.resolve_default_value(
                            raw_field["name"], None, has_default=False, class_name=model.class_name
                        )
                        if has_default_override:
                            continue
                        try:
                            default = converter._convert_default(  # ruff: ignore[private-member-access]
                                raw_field["default"], raw_field["type"], converter.names[fullname].namespace
                            )
                        except Error:
                            # An unrepresentable auxiliary default must not reject a valid physical override.
                            continue
                        if repr(default) == repr(physical_default):
                            continue
                        field.default = default
                        field._set_runtime_expression_imports(runtime_expression_imports(default))  # ruff: ignore[private-member-access]
                        field.invalidate_semantic_caches()
                        model.clear_imports_cache()
            self._default_converters = None
        super()._finalize_structured_imports(contexts)

    @staticmethod
    def _logical_default_format(kind: str, logical_type: str) -> str:
        """Resolve the JSON Schema format used for an Avro logical default."""
        match kind:
            case "timedelta":
                return "duration"
            case "datetime":
                return "date-time-local" if logical_type.startswith("local-") else "date-time"
            case _:
                return kind

    def _default_type_overrides(self, name: str, schema: JsonSchema, as_root: bool) -> dict[str, Import]:  # ruff: ignore[boolean-type-hint-positional-argument]
        """Resolve default overrides with the existing model and field naming rules."""
        if as_root:
            name, preserve_name = self._resolve_root_model_name({"title": name})
        else:
            preserve_name = False
        class_name = self.model_resolver.get_class_name(
            name, unique=False, is_root=as_root, preserve_name=preserve_name
        ).name
        if schema["type"] == "fixed":
            return (
                {"": override}
                if not self.collapse_root_models and (override := self._model_type_override_imports.get(class_name))
                else {}
            )
        if class_name not in self._reuse_optimization_context.type_override_model_names:
            return {}
        overrides = {}
        excludes: set[str] = set()
        for field in schema.get("fields", []):
            if not isinstance(field, dict) or not isinstance(field.get("name"), str):
                continue
            field_name, _ = self.model_resolver.get_valid_field_name_and_alias(
                field["name"], excludes=excludes, model_type=self.field_name_model_type, class_name=class_name
            )
            excludes.add(field_name)
            if override := self._type_override_imports.get(f"{class_name}.{field_name}"):
                overrides[field["name"]] = override
        return overrides

    def _logical_default_enabled(self, kind: str, logical_type: str, type_override: Import | None = None) -> bool:
        """Keep physical defaults when a mapping replaces their logical representation."""
        if type_override in _PHYSICAL_DEFAULT_OVERRIDES:
            return False
        if not self.type_mappings:
            return True
        format_ = self._logical_default_format(kind, logical_type)
        mapped_type = self._get_type_with_mappings("string", format_)
        return (
            mapped_type == self._data_formats["string"][format_]
            or self.data_type_manager.get_data_type(mapped_type).type == "str"
        )

    def _logical_default(self, kind: str, logical_type: str, value: str) -> Any:
        """Keep defaults compatible with the backend's existing logical type mapping."""
        logical_type_kind = self._get_type_with_mappings("string", self._logical_default_format(kind, logical_type))
        if self.data_type_manager.get_data_type(logical_type_kind).type == "str":
            return value
        self._register_runtime_expression()
        return _logical_default_expression(kind, value)


def convert_avro_schema_data(data: YamlValue) -> dict[str, YamlValue]:
    """Convert in-memory Avro schema data to the JSON Schema shape used by the parser."""
    return _AvroSchemaConverter().convert_raw(data)


__all__ = ["AvroParser", "convert_avro_schema_data", "is_avro_schema_data"]
