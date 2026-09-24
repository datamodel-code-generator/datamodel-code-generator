"""XML Schema parser implementation.

Converts W3C XML Schema (XSD) documents into the JSON Schema shape consumed by
the existing JSON Schema parser.
"""

from __future__ import annotations

import codecs
import contextlib
import io
import re
import warnings
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, cast
from xml.etree import ElementTree as ET  # noqa: S405

from typing_extensions import Unpack

from datamodel_code_generator import Error
from datamodel_code_generator._format_types import DatetimeClassType
from datamodel_code_generator._xmlschema_detection import XML_SCHEMA_NAMESPACE, XML_SCHEMA_TAG
from datamodel_code_generator._xmlschema_detection import is_xml_schema_text as _is_xml_schema_text
from datamodel_code_generator.enums import VersionMode, XMLSchemaVersion
from datamodel_code_generator.parser import _xmlschema_literals
from datamodel_code_generator.parser._convert_common import _copy_schema, _namespace_name, _unique_name
from datamodel_code_generator.parser._xmlschema_literals import (
    _PythonExpression,
    _safe_bool,
    _safe_date_expression,
    _safe_datetime_expression,
    _safe_day_time_duration_expression,
    _safe_float,
    _safe_time_expression,
)
from datamodel_code_generator.parser.base import Source, title_to_class_name
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from datamodel_code_generator.python_literal import runtime_expression_imports
from datamodel_code_generator.util import record_watch_dependency

if TYPE_CHECKING:
    from collections.abc import Iterator
    from urllib.parse import ParseResult

    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator._types import XMLSchemaParserConfigDict
    from datamodel_code_generator.config import XMLSchemaParserConfig
    from datamodel_code_generator.python_literal import PythonRuntimeExpression

XML_SCHEMA_VERSIONING_NAMESPACE = "http://www.w3.org/2007/XMLSchema-versioning"
XSD11_ELEMENTS = frozenset({"alternative", "assert", "assertion", "defaultOpenContent", "openContent", "override"})
UNBOUNDED = "unbounded"
INTERNAL_OCCURS_ARRAY = "x-xsd-occurs-array"
UNSUPPORTED_XSD_PATTERN = re.compile(r"\\[iIcCpP]|-\[|&&")

DAY_TIME_DURATION_PATTERN = _xmlschema_literals.DAY_TIME_DURATION_PATTERN
IMPORT_DATETIME_MODULE = _xmlschema_literals.IMPORT_DATETIME_MODULE
XML_DATE_PATTERN = _xmlschema_literals.XML_DATE_PATTERN
XSD_WHITESPACE_CHARS = _xmlschema_literals.XSD_WHITESPACE_CHARS
_datetime_expression = _xmlschema_literals._datetime_expression  # noqa: SLF001
_normalize_timezone = _xmlschema_literals._normalize_timezone  # noqa: SLF001
_collect_python_expression_imports = runtime_expression_imports

_XMLSCHEMA_LITERAL_REEXPORTS: tuple[tuple[str, object], ...] = (
    ("DAY_TIME_DURATION_PATTERN", DAY_TIME_DURATION_PATTERN),
    ("IMPORT_DATETIME_MODULE", IMPORT_DATETIME_MODULE),
    ("XML_DATE_PATTERN", XML_DATE_PATTERN),
    ("XSD_WHITESPACE_CHARS", XSD_WHITESPACE_CHARS),
    ("_datetime_expression", _datetime_expression),
    ("_normalize_timezone", _normalize_timezone),
    ("_collect_python_expression_imports", _collect_python_expression_imports),
)
for _xmlschema_literal_reexport_name, _xmlschema_literal_reexport in _XMLSCHEMA_LITERAL_REEXPORTS:
    if globals()[_xmlschema_literal_reexport_name] is not _xmlschema_literal_reexport:  # pragma: no cover
        msg = f"XML Schema literal re-export mismatch: {_xmlschema_literal_reexport_name}"
        raise RuntimeError(msg)
del _xmlschema_literal_reexport_name, _xmlschema_literal_reexport

JsonSchema = dict[str, Any]
QNameKey = tuple[str | None, str]
DefinitionKey = tuple[str, str | None, str]
PYTHON_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_XML_SCHEMA_DATA_CACHE_MAX_SIZE = 128
_XMLSchemaDataCacheKey = tuple[Path, Path, str, str, XMLSchemaVersion | None, VersionMode | None, bool, bool]
_XMLSchemaDataSeenKey = tuple[Path, Path, str, XMLSchemaVersion | None, VersionMode | None, bool, bool]


class _XMLSchemaDataCacheEntry(NamedTuple):
    data: dict[str, YamlValue]
    dependencies: tuple[tuple[Path, str], ...]


_xml_schema_data_cache: OrderedDict[_XMLSchemaDataCacheKey, _XMLSchemaDataCacheEntry] = OrderedDict()
_xml_schema_data_seen_keys: OrderedDict[_XMLSchemaDataSeenKey, None] = OrderedDict()
_xml_schema_data_cache_lock = RLock()


STRING_SCHEMA: JsonSchema = {"type": "string"}
INTEGER_SCHEMA: JsonSchema = {"type": "integer"}
NUMBER_SCHEMA: JsonSchema = {"type": "number"}
BOOLEAN_SCHEMA: JsonSchema = {"type": "boolean"}
DECIMAL_SCHEMA: JsonSchema = {"type": "number", "format": "decimal"}
DATETIME_SCHEMA: JsonSchema = {"type": "string", "format": "date-time"}


class _OccurrenceContext(NamedTuple):
    required: bool = True
    repeating: bool = False
    min_items: int | None = None
    max_items: int | None = 1  # Multiplicative identity; None denotes an unbounded maximum.


DEFAULT_OCCURRENCE = _OccurrenceContext()

BUILTIN_TYPE_SCHEMAS: dict[str, JsonSchema] = {
    "anySimpleType": {},
    "anyAtomicType": {},
    "anyType": {},
    "anyURI": {"type": "string", "format": "uri"},
    "base64Binary": {"type": "string", "format": "byte"},
    "boolean": BOOLEAN_SCHEMA,
    "byte": {"type": "integer", "minimum": -128, "maximum": 127},
    "date": {"type": "string", "format": "date"},
    "dateTime": DATETIME_SCHEMA,
    "decimal": DECIMAL_SCHEMA,
    "double": NUMBER_SCHEMA,
    "duration": STRING_SCHEMA,
    "dayTimeDuration": {"type": "string", "format": "duration"},
    "ENTITIES": {"type": "array", "items": STRING_SCHEMA},
    "ENTITY": STRING_SCHEMA,
    "float": NUMBER_SCHEMA,
    "gDay": {"type": "string"},
    "gMonth": {"type": "string"},
    "gMonthDay": {"type": "string"},
    "gYear": {"type": "string"},
    "gYearMonth": {"type": "string"},
    "hexBinary": STRING_SCHEMA,
    "ID": STRING_SCHEMA,
    "IDREF": STRING_SCHEMA,
    "IDREFS": {"type": "array", "items": STRING_SCHEMA},
    "int": {"type": "integer", "minimum": -2147483648, "maximum": 2147483647},
    "integer": INTEGER_SCHEMA,
    "language": STRING_SCHEMA,
    "long": {"type": "integer", "minimum": -9223372036854775808, "maximum": 9223372036854775807},
    "Name": STRING_SCHEMA,
    "NCName": STRING_SCHEMA,
    "negativeInteger": {"type": "integer", "maximum": -1},
    "NMTOKEN": STRING_SCHEMA,
    "NMTOKENS": {"type": "array", "items": STRING_SCHEMA},
    "nonNegativeInteger": {"type": "integer", "minimum": 0},
    "nonPositiveInteger": {"type": "integer", "maximum": 0},
    "normalizedString": STRING_SCHEMA,
    "NOTATION": STRING_SCHEMA,
    "positiveInteger": {"type": "integer", "minimum": 1},
    "QName": STRING_SCHEMA,
    "short": {"type": "integer", "minimum": -32768, "maximum": 32767},
    "string": STRING_SCHEMA,
    "time": {"type": "string", "format": "time"},
    "token": STRING_SCHEMA,
    "unsignedByte": {"type": "integer", "minimum": 0, "maximum": 255},
    "unsignedInt": {"type": "integer", "minimum": 0, "maximum": 4294967295},
    "unsignedLong": {"type": "integer", "minimum": 0, "maximum": 18446744073709551615},
    "unsignedShort": {"type": "integer", "minimum": 0, "maximum": 65535},
    "yearMonthDuration": STRING_SCHEMA,
    "dateTimeStamp": {"type": "string", "format": "date-time"},
}


def is_xml_schema_text(text: str) -> bool:
    """Return whether text is an XML Schema document."""
    return _is_xml_schema_text(text)


def _local_name(tag_or_qname: str) -> str:
    if "}" in tag_or_qname:
        return tag_or_qname.rsplit("}", maxsplit=1)[-1]
    return tag_or_qname.rsplit(":", maxsplit=1)[-1]


def _namespace(tag: str) -> str | None:
    if tag.startswith("{"):
        return tag[1:].split("}", maxsplit=1)[0]
    return None


def _is_xsd_element(element: ET.Element, *names: str) -> bool:
    return _namespace(element.tag) == XML_SCHEMA_NAMESPACE and _local_name(element.tag) in names


def _xsd_children(element: ET.Element, *names: str) -> Iterator[ET.Element]:
    for child in element:
        if _is_xsd_element(child, *names):
            yield child


def _first_xsd_child(element: ET.Element, *names: str) -> ET.Element | None:
    return next(_xsd_children(element, *names), None)


def _documentation(element: ET.Element) -> str | None:
    docs: list[str] = []
    for annotation in _xsd_children(element, "annotation"):
        for documentation in _xsd_children(annotation, "documentation"):
            text = "".join(documentation.itertext()).strip()
            if text:
                docs.append(text)
    return "\n\n".join(docs) or None


def _to_class_title(name: str) -> str:
    if PYTHON_NAME_PATTERN.match(name):
        return f"{name[:1].upper()}{name[1:]}"
    return title_to_class_name(name)


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _is_supported_pattern(value: str) -> bool:
    if UNSUPPORTED_XSD_PATTERN.search(value):
        return False
    with contextlib.suppress(re.error):
        re.compile(value)
        return True
    return False


def _version_decimal(version: XMLSchemaVersion) -> Decimal:
    return Decimal(version.value)


def _safe_decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _versioning_value(element: ET.Element, name: str) -> Decimal | None:
    value = element.get(f"{{{XML_SCHEMA_VERSIONING_NAMESPACE}}}{name}")
    return _safe_decimal(value) if value is not None else None


def _has_xmlschema_versioning_attribute(element: ET.Element) -> bool:
    return any(_namespace(attribute) == XML_SCHEMA_VERSIONING_NAMESPACE for attribute in element.attrib)


def _read_xml_text(path: Path, encoding: str) -> str:
    record_watch_dependency(path)
    return _decode_xml_bytes(path.read_bytes(), encoding)


def _decode_xml_bytes(data: bytes, encoding: str) -> str:
    for bom, xml_encoding in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if data.startswith(bom):
            return data.decode(xml_encoding)
    return data.decode(encoding)


def _digest_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def _digest_path(path: Path) -> str:
    record_watch_dependency(path)
    return _digest_bytes(path.read_bytes())


def _xml_schema_cache_dependencies(paths: set[Path]) -> tuple[tuple[Path, str], ...]:
    return tuple((path, _digest_path(path)) for path in sorted(paths))


def _xml_schema_cache_entry_is_fresh(entry: _XMLSchemaDataCacheEntry) -> bool:
    return all(path.is_file() and _digest_path(path) == digest for path, digest in entry.dependencies)


def _load_xml_schema_data_from_path(  # noqa: PLR0913
    path: Path,
    base_path: Path,
    encoding: str,
    *,
    xmlschema_version: XMLSchemaVersion | None,
    schema_version_mode: VersionMode | None,
    use_xmlschema_datetime_default: bool,
    source_safe_non_finite: bool,
) -> dict[str, YamlValue]:
    resolved_path = path.resolve()
    resolved_base_path = base_path.resolve()
    seen_key = (
        resolved_path,
        resolved_base_path,
        encoding,
        xmlschema_version,
        schema_version_mode,
        use_xmlschema_datetime_default,
        source_safe_non_finite,
    )
    with _xml_schema_data_cache_lock:
        use_cache = seen_key in _xml_schema_data_seen_keys
        _xml_schema_data_seen_keys[seen_key] = None
        _xml_schema_data_seen_keys.move_to_end(seen_key)
        while len(_xml_schema_data_seen_keys) > _XML_SCHEMA_DATA_CACHE_MAX_SIZE:
            _xml_schema_data_seen_keys.popitem(last=False)

    if not use_cache:
        converter = _XMLSchemaConverter(
            base_path=base_path,
            encoding=encoding,
            xmlschema_version=xmlschema_version,
            schema_version_mode=schema_version_mode,
            use_xmlschema_datetime_default=use_xmlschema_datetime_default,
            source_safe_non_finite=source_safe_non_finite,
        )
        return converter.convert(Source(path=path.relative_to(base_path), text=_read_xml_text(path, encoding)))

    cache_key = (
        resolved_path,
        resolved_base_path,
        _digest_path(resolved_path),
        encoding,
        xmlschema_version,
        schema_version_mode,
        use_xmlschema_datetime_default,
        source_safe_non_finite,
    )
    with _xml_schema_data_cache_lock:
        if (entry := _xml_schema_data_cache.get(cache_key)) is not None and _xml_schema_cache_entry_is_fresh(entry):
            _xml_schema_data_cache.move_to_end(cache_key)
            return _copy_schema(entry.data)

    converter = _XMLSchemaConverter(
        base_path=base_path,
        encoding=encoding,
        xmlschema_version=xmlschema_version,
        schema_version_mode=schema_version_mode,
        use_xmlschema_datetime_default=use_xmlschema_datetime_default,
        source_safe_non_finite=source_safe_non_finite,
    )
    data = converter.convert(Source(path=path.relative_to(base_path), text=_read_xml_text(path, encoding)))
    dependencies = _xml_schema_cache_dependencies(converter.loaded_source_paths)
    with _xml_schema_data_cache_lock:
        _xml_schema_data_cache[cache_key] = _XMLSchemaDataCacheEntry(
            data=_copy_schema(data),
            dependencies=dependencies,
        )
        _xml_schema_data_cache.move_to_end(cache_key)
        while len(_xml_schema_data_cache) > _XML_SCHEMA_DATA_CACHE_MAX_SIZE:
            _xml_schema_data_cache.popitem(last=False)
    return data


def _clear_xml_schema_data_cache() -> None:
    with _xml_schema_data_cache_lock:
        _xml_schema_data_cache.clear()
        _xml_schema_data_seen_keys.clear()


def detect_xmlschema_version(source: ET.Element | str) -> XMLSchemaVersion:
    """Detect XML Schema version from XSD 1.1 versioning attributes and constructs."""
    root = ET.fromstring(source) if isinstance(source, str) else source  # noqa: S314
    for element in root.iter():
        if _has_xmlschema_versioning_attribute(element) or _is_xsd_element(element, *XSD11_ELEMENTS):
            return XMLSchemaVersion.V11
    return XMLSchemaVersion.V10


def convert_xml_schema_data(
    raw_schema: Any,
    *,
    base_path: Path | None = None,
    xmlschema_version: XMLSchemaVersion | None = None,
    schema_version_mode: VersionMode | None = None,
    encoding: str = "utf-8",
) -> dict[str, YamlValue]:
    """Convert an XML Schema source string to JSON Schema data."""
    return _convert_xml_schema_data(
        raw_schema,
        base_path=base_path,
        xmlschema_version=xmlschema_version,
        schema_version_mode=schema_version_mode,
        encoding=encoding,
        source_safe_non_finite=False,
    )


def _convert_xml_schema_data(  # noqa: PLR0913
    raw_schema: Any,
    *,
    base_path: Path | None = None,
    xmlschema_version: XMLSchemaVersion | None = None,
    schema_version_mode: VersionMode | None = None,
    encoding: str = "utf-8",
    source_safe_non_finite: bool,
) -> dict[str, YamlValue]:
    """Convert XML Schema data for a parser that may need source-safe float values."""
    if not isinstance(raw_schema, str):
        msg = "XML Schema schemaFormat requires an XSD schema string"
        raise Error(msg)
    converter = _XMLSchemaConverter(
        base_path=base_path or Path.cwd(),
        encoding=encoding,
        xmlschema_version=xmlschema_version,
        schema_version_mode=schema_version_mode,
        source_safe_non_finite=source_safe_non_finite,
    )
    return converter.convert(Source(path=Path("__asyncapi_schema__.xsd"), text=raw_schema))


class _XMLSchemaConverter:
    def __init__(  # noqa: PLR0913
        self,
        base_path: Path,
        encoding: str,
        *,
        xmlschema_version: XMLSchemaVersion | None = None,
        schema_version_mode: VersionMode | None = None,
        use_xmlschema_datetime_default: bool = False,
        source_safe_non_finite: bool = False,
    ) -> None:
        self.base_path = base_path
        self.encoding = encoding
        self.xmlschema_version = xmlschema_version
        self.schema_version_mode = schema_version_mode or VersionMode.Lenient
        self.use_xmlschema_datetime_default = use_xmlschema_datetime_default
        self.source_safe_non_finite = source_safe_non_finite
        self._resolved_xmlschema_version: XMLSchemaVersion | None = None
        self.namespaces: dict[str, str] = {}
        self.target_namespace: str | None = None
        self.simple_types: dict[QNameKey, ET.Element] = {}
        self.complex_types: dict[QNameKey, ET.Element] = {}
        self.elements: dict[QNameKey, ET.Element] = {}
        self.attributes: dict[QNameKey, ET.Element] = {}
        self.groups: dict[QNameKey, ET.Element] = {}
        self.attribute_groups: dict[QNameKey, ET.Element] = {}
        self.default_open_content: ET.Element | None = None
        self._redefined_base_complex_types: dict[QNameKey, ET.Element] = {}
        self._redefined_base_simple_types: dict[QNameKey, ET.Element] = {}
        self._redefined_base_groups: dict[QNameKey, ET.Element] = {}
        self._redefined_base_attribute_groups: dict[QNameKey, ET.Element] = {}
        self._active_groups: set[QNameKey] = set()
        self._active_attribute_groups: set[QNameKey] = set()
        self.substitution_groups: dict[QNameKey, set[QNameKey]] = {}
        self.substitution_members: set[QNameKey] = set()
        self.referenced_elements: set[QNameKey] = set()
        self.local_elements: set[QNameKey] = set()
        self._loaded_locations: set[tuple[Path, str | None]] = set()
        self._element_namespaces: dict[int, dict[str, str]] = {}
        self._building_definitions: set[DefinitionKey] = set()
        self._built_definitions: dict[DefinitionKey, JsonSchema] = {}
        self._definitions: dict[str, JsonSchema] = {}
        self._definition_names: dict[DefinitionKey, str] = {}
        self.loaded_source_paths: set[Path] = set()

    def convert(self, source: Source) -> dict[str, YamlValue]:
        if source.raw_data is not None:
            if not isinstance(source.raw_data, dict):  # pragma: no cover
                msg = f"Expected dict, got {type(source.raw_data).__name__}"
                raise TypeError(msg)
            return source.raw_data
        source_path = self.base_path / source.path
        self.loaded_source_paths.add(source_path.resolve())
        root = self._parse_schema(source.text, source_path)
        version = self._detect_effective_xmlschema_version(root, source_path)
        self._resolved_xmlschema_version = version
        self._prepare_schema_root(root, version)
        self._collect_schema(root, source_path=source_path, is_root=True)

        self._prepare_definition_names()
        self._definitions = self._build_definitions()
        global_elements = [
            (key, element)
            for key, element in self.elements.items()
            if key in self.local_elements
            and element.get("name")
            and element.get("abstract") != "true"
            and key not in self.substitution_members
        ]

        if len(global_elements) == 1:
            root_key, root_element = global_elements[0]
            root_name = root_element.get("name") or "Model"
            root_type = root_element.get("type")
            root_type_key = (
                self._resolve_key(root_type, self.simple_types, self.complex_types, element=root_element)
                if root_type
                else None
            )
            root_definition_key = (
                self._type_definition_key(root_type_key) if root_type_key else self._element_definition_key(root_key)
            )
            root_definition_name = self._definition_name(root_definition_key)
            if (
                root_type is not None
                and _local_name(root_type) == root_name
                and root_definition_name in self._definitions
            ):
                schema = _copy_schema(self._definitions[root_definition_name])
                schema.setdefault("title", _to_class_title(root_name))
                if not self._has_definition_ref(root_definition_key):
                    self._definitions.pop(root_definition_name)
            else:
                schema = self._convert_global_element_as_root(root_element, root_name)
        else:
            properties: dict[str, JsonSchema] = {}
            for _key, element in global_elements:
                name = cast("str", element.get("name"))
                properties[name] = self._convert_global_element_as_property(element)
            schema = {
                "title": "Model",
                "type": "object",
                "properties": properties,
                "required": list(properties),
            }

        if self._definitions:
            schema["definitions"] = self._definitions
        schema.setdefault("$schema", "http://json-schema.org/draft-07/schema#")
        self._strip_internal_metadata(schema)
        return cast("dict[str, YamlValue]", schema)

    def _resolve_xmlschema_version(self, root: ET.Element) -> XMLSchemaVersion:
        if self.xmlschema_version is not None and self.xmlschema_version != XMLSchemaVersion.Auto:
            return self.xmlschema_version
        return detect_xmlschema_version(root)

    def _detect_effective_xmlschema_version(
        self,
        root: ET.Element,
        source_path: Path,
        seen_locations: set[Path] | None = None,
    ) -> XMLSchemaVersion:
        version = self._resolve_xmlschema_version(root)
        if self.xmlschema_version is not None and self.xmlschema_version != XMLSchemaVersion.Auto:
            return version
        seen = seen_locations if seen_locations is not None else set()
        source_dir = source_path.parent if source_path.name else self.base_path
        for child in _xsd_children(root, "include", "import", "redefine", "override"):
            schema_location = child.get("schemaLocation")
            if not schema_location:
                continue
            location = self._resolve_schema_location(source_dir, schema_location)
            if location in seen or not location.is_file():
                continue
            self.loaded_source_paths.add(location)
            seen.add(location)
            included_root = self._parse_schema(_read_xml_text(location, self.encoding), location)
            included_version = self._detect_effective_xmlschema_version(included_root, location, seen)
            if _version_decimal(included_version) > _version_decimal(version):
                version = included_version
        return version

    def _resolve_schema_location(self, source_dir: Path, schema_location: str) -> Path:
        base_path = self.base_path.resolve()
        location = (source_dir / schema_location).resolve()
        if location.is_relative_to(base_path):
            return location

        msg = (
            f"Blocked unsafe XML Schema schemaLocation: {schema_location}\n"
            "Reason: the resolved file is outside the input base path.\n"
            f"Base path: {base_path}\n"
            f"Resolved path: {location}\n"
            "Move trusted included schemas under the input directory before generating models."
        )
        raise Error(msg)

    def _prepare_schema_root(self, root: ET.Element, version: XMLSchemaVersion) -> None:
        self._check_version_specific_features(root, version)
        self._prune_versioned_elements(root, version)

    def _check_version_specific_features(self, root: ET.Element, version: XMLSchemaVersion) -> None:
        if self.schema_version_mode != VersionMode.Strict or version != XMLSchemaVersion.V10:
            return
        for element in root.iter():
            if _has_xmlschema_versioning_attribute(element):
                warnings.warn(
                    "XML Schema versioning attributes are XSD 1.1 features, but schema version is 1.0",
                    stacklevel=2,
                )
                return
            if _is_xsd_element(element, *XSD11_ELEMENTS):
                warnings.warn(
                    f"xs:{_local_name(element.tag)} is an XSD 1.1 construct, but schema version is 1.0",
                    stacklevel=2,
                )
                return

    def _prune_versioned_elements(self, root: ET.Element, version: XMLSchemaVersion) -> None:
        processor_version = _version_decimal(version)
        for element in root.iter():
            element[:] = [child for child in element if self._is_version_applicable(child, processor_version)]

    def _is_version_applicable(self, element: ET.Element, processor_version: Decimal) -> bool:  # noqa: PLR6301
        min_version = _versioning_value(element, "minVersion")
        if min_version is not None and processor_version < min_version:
            return False
        max_version = _versioning_value(element, "maxVersion")
        return max_version is None or processor_version < max_version

    def _parse_schema(self, text: str, source_path: Path) -> ET.Element:
        try:
            root: ET.Element | None = None
            active_namespaces: dict[str, str] = {}
            namespace_stack: list[tuple[str, str | None]] = []
            for event, payload in ET.iterparse(io.StringIO(text), events=("start", "start-ns", "end-ns")):  # noqa: S314
                match event:
                    case "start-ns":
                        prefix, uri = cast("tuple[str, str]", payload)
                        namespace_stack.append((prefix, active_namespaces.get(prefix)))
                        active_namespaces[prefix] = uri
                        self.namespaces.setdefault(prefix, uri)
                    case "end-ns":
                        prefix, previous_uri = namespace_stack.pop()
                        if previous_uri is None:
                            active_namespaces.pop(prefix, None)
                        else:
                            active_namespaces[prefix] = previous_uri
                    case "start":
                        element = cast("ET.Element", payload)
                        if root is None:
                            root = element
                        self._element_namespaces[id(element)] = active_namespaces.copy()
                    case _:  # pragma: no cover
                        pass
        except ET.ParseError as exc:
            msg = f"Invalid XML Schema document {source_path}: {exc}"
            raise Error(msg) from exc
        if root is None:  # pragma: no cover
            msg = f"Invalid XML Schema document {source_path}: empty document"
            raise Error(msg)
        if root.tag != XML_SCHEMA_TAG:
            msg = f"XML Schema root element must be xs:schema: {source_path}"
            raise Error(msg)
        if target_namespace := root.get("targetNamespace"):
            self.target_namespace = target_namespace
        return root

    def _collect_schema(
        self,
        root: ET.Element,
        source_path: Path,
        *,
        is_root: bool = False,
        namespace_override: str | None = None,
    ) -> None:
        source_dir = source_path.parent if source_path.name else self.base_path
        schema_namespace = root.get("targetNamespace") or namespace_override
        for child in _xsd_children(root, "include", "import", "redefine", "override"):
            schema_location = child.get("schemaLocation")
            if not schema_location:
                continue
            location = self._resolve_schema_location(source_dir, schema_location)
            if not location.is_file():
                continue
            self.loaded_source_paths.add(location)
            included_root = self._parse_schema(_read_xml_text(location, self.encoding), location)
            self._prepare_schema_root(
                included_root, self._resolved_xmlschema_version or self._resolve_xmlschema_version(included_root)
            )
            child_namespace_override = (
                schema_namespace
                if _local_name(child.tag) in {"include", "redefine", "override"}
                and included_root.get("targetNamespace") is None
                else None
            )
            load_key = (location, child_namespace_override)
            if load_key not in self._loaded_locations:
                self._loaded_locations.add(load_key)
                self._collect_schema(
                    included_root,
                    location,
                    is_root=is_root and _local_name(child.tag) in {"include", "redefine", "override"},
                    namespace_override=child_namespace_override,
                )
            if _is_xsd_element(child, "redefine", "override"):
                self._collect_schema_declarations(child, schema_namespace, replace=True, is_root=is_root)

        for child in root:
            if _namespace(child.tag) != XML_SCHEMA_NAMESPACE:
                continue
            self._collect_schema_declaration(child, schema_namespace, is_root=is_root)
        for child in root.iter():
            if _is_xsd_element(child, "element") and (ref := child.get("ref")):
                self.referenced_elements.add(self._qname_key(ref, child))

    def _collect_schema_declarations(
        self,
        owner: ET.Element,
        schema_namespace: str | None,
        *,
        replace: bool,
        is_root: bool = False,
    ) -> None:
        for child in owner:
            if _namespace(child.tag) == XML_SCHEMA_NAMESPACE:
                self._collect_schema_declaration(child, schema_namespace, replace=replace, is_root=is_root)

    def _collect_schema_declaration(
        self,
        child: ET.Element,
        schema_namespace: str | None,
        *,
        is_root: bool = False,
        replace: bool = False,
    ) -> None:
        if _is_xsd_element(child, "defaultOpenContent"):
            self.default_open_content = child
            return
        name = child.get("name")
        if not name:
            return
        key = (schema_namespace, name)
        name_to_registry = {
            "simpleType": self.simple_types,
            "complexType": self.complex_types,
            "element": self.elements,
            "attribute": self.attributes,
            "group": self.groups,
            "attributeGroup": self.attribute_groups,
        }
        local_name = _local_name(child.tag)
        registry = name_to_registry.get(local_name)
        if registry is None:
            return
        if local_name == "element":
            if is_root:
                self.local_elements.add(key)
            for substitution_group in (child.get("substitutionGroup") or "").split():
                head_key = self._qname_key(substitution_group, child)
                self.substitution_groups.setdefault(head_key, set()).add(key)
                self.substitution_members.add(key)
        if replace:
            if local_name == "complexType" and key in registry:
                self._redefined_base_complex_types.setdefault(key, registry[key])
            elif local_name == "simpleType" and key in registry:
                self._redefined_base_simple_types.setdefault(key, registry[key])
            elif local_name == "group" and key in registry:
                self._redefined_base_groups.setdefault(key, registry[key])
            elif local_name == "attributeGroup" and key in registry:
                self._redefined_base_attribute_groups.setdefault(key, registry[key])
            registry[key] = child
        else:
            registry.setdefault(key, child)

    def _prepare_definition_names(self) -> None:
        keys = {self._type_definition_key(key) for key in set(self.simple_types) | set(self.complex_types)}
        keys.update(self._element_definition_key(key) for key in self._resolved_referenced_elements())
        keys_by_local: dict[str, list[DefinitionKey]] = {}
        for key in keys:
            keys_by_local.setdefault(key[2], []).append(key)

        used_names: set[str] = set()
        for local, local_keys in sorted(keys_by_local.items()):
            sorted_keys = sorted(local_keys, key=self._definition_sort_key)
            namespaces = {key[1] for key in sorted_keys}
            for key in sorted_keys:
                same_qname_count = sum(candidate[1:] == key[1:] for candidate in sorted_keys)
                if len(sorted_keys) == 1:
                    name = local
                else:
                    namespace_name = _namespace_name(key[1], _to_class_title) if len(namespaces) > 1 else ""
                    kind_name = _to_class_title(key[0]) if same_qname_count > 1 else ""
                    name = f"{namespace_name}{_to_class_title(local)}{kind_name}"
                candidate = _unique_name(name, used_names)
                self._definition_names[key] = candidate
                used_names.add(candidate)

    def _build_definitions(self) -> dict[str, JsonSchema]:
        definitions = self._definitions
        for key in sorted(self.simple_types, key=self._sort_key):
            definition_key = self._type_definition_key(key)
            definitions[self._definition_name(definition_key)] = self._build_definition(definition_key)
        for key in sorted(self.complex_types, key=self._sort_key):
            definition_key = self._type_definition_key(key)
            definitions[self._definition_name(definition_key)] = self._build_definition(definition_key)
        for key in sorted(self._resolved_referenced_elements(), key=self._sort_key):
            definition_key = self._element_definition_key(key)
            definitions[self._definition_name(definition_key)] = self._build_definition(definition_key)
        return definitions

    def _build_definition(self, key: DefinitionKey) -> JsonSchema:
        if key in self._built_definitions:
            return self._built_definitions[key]
        if key in self._building_definitions:  # pragma: no cover
            return {"$ref": self._ref_for_definition_key(key)}
        self._building_definitions.add(key)
        qname_key = self._qname_from_definition_key(key)
        if key[0] == "element":
            element = self.elements.get(qname_key)
            schema = self._convert_element(element) if element is not None else {}
        else:
            simple_type = self.simple_types.get(qname_key)
            complex_type = self.complex_types.get(qname_key)
            if simple_type is not None:
                schema = self._convert_simple_type(simple_type)
            elif complex_type is not None:
                schema = self._convert_complex_type(complex_type)
            else:  # pragma: no cover
                schema = {}
        if schema is None:  # pragma: no cover
            schema = {}
        name = self._definition_name(key)
        schema.setdefault("title", _to_class_title(name))
        self._building_definitions.remove(key)
        self._built_definitions[key] = schema
        self._definitions[name] = schema
        return schema

    def _has_definition_ref(self, key: DefinitionKey) -> bool:
        ref = self._ref_for_definition_key(key)
        return any(self._contains_ref(schema, ref) for schema in self._definitions.values())

    def _contains_ref(self, value: Any, ref: str) -> bool:
        if isinstance(value, dict):
            return value.get("$ref") == ref or any(self._contains_ref(item, ref) for item in value.values())
        if isinstance(value, list):
            return any(self._contains_ref(item, ref) for item in value)
        return False

    def _convert_global_element_as_root(self, element: ET.Element, name: str) -> JsonSchema:
        schema = self._convert_element(element)
        if "$ref" in schema:
            schema = {"title": _to_class_title(name), "allOf": [schema]}
        else:
            schema.setdefault("title", _to_class_title(name))
        return schema

    def _convert_global_element_as_property(self, element: ET.Element) -> JsonSchema:
        schema = self._convert_element(element)
        if "$ref" in schema:
            return schema
        name = cast("str", element.get("name"))
        schema.setdefault("title", _to_class_title(name))
        return schema

    def _convert_element(self, element: ET.Element) -> JsonSchema:
        if ref := element.get("ref"):
            key = self._resolve_key(ref, self.elements, element=element)
            schema = self._schema_for_substitution_group(key) or {"$ref": self._ref_for_element_key(key)}
        elif type_name := element.get("type"):
            schema = self._schema_for_qname(type_name, element)
        elif (simple_type := _first_xsd_child(element, "simpleType")) is not None:
            schema = self._convert_simple_type(simple_type)
        elif (complex_type := _first_xsd_child(element, "complexType")) is not None:
            schema = self._convert_complex_type(complex_type)
        else:
            schema = {}

        if alternative_schema := self._schema_for_alternatives(element, schema):
            schema = alternative_schema
        schema = self._apply_common_element_metadata(element, _copy_schema(schema))
        return self._apply_occurs(element, schema)

    def _schema_for_alternatives(self, element: ET.Element, fallback_schema: JsonSchema) -> JsonSchema | None:
        schemas = [
            schema
            for alternative in _xsd_children(element, "alternative")
            if (schema := self._convert_alternative(alternative))
        ]
        if not schemas:
            return None
        if fallback_schema and fallback_schema not in schemas:
            schemas.append(fallback_schema)
        return schemas[0] if len(schemas) == 1 else {"anyOf": schemas}

    def _convert_alternative(self, alternative: ET.Element) -> JsonSchema:
        if type_name := alternative.get("type"):
            return self._schema_for_qname(type_name, alternative)
        if (simple_type := _first_xsd_child(alternative, "simpleType")) is not None:
            return self._convert_simple_type(simple_type)
        if (complex_type := _first_xsd_child(alternative, "complexType")) is not None:
            return self._convert_complex_type(complex_type)
        return {}

    def _apply_common_element_metadata(self, element: ET.Element, schema: JsonSchema) -> JsonSchema:
        if documentation := _documentation(element):
            schema["description"] = documentation
        if "default" in element.attrib:
            schema["default"] = self._parse_literal(str(element.get("default")), schema, parse_temporal=True)
        if "fixed" in element.attrib:
            self._apply_fixed_value(schema, self._parse_literal(str(element.get("fixed")), schema))
        if element.get("nillable") in {"true", "1"}:
            schema = self._make_nullable(schema)
        if element.get("abstract") == "true":
            schema["x-xsd-abstract"] = True
        return schema

    @staticmethod
    def _apply_fixed_value(schema: JsonSchema, fixed_value: Any) -> None:
        if isinstance(fixed_value, Decimal):
            schema["x-python-type"] = "Decimal"
        schema["const"] = fixed_value
        schema.setdefault("default", fixed_value)

    def _make_nullable(self, schema: JsonSchema) -> JsonSchema:  # noqa: PLR6301
        schema = _copy_schema(schema)
        schema_type = schema.get("type")
        if isinstance(schema_type, str):
            schema["type"] = [schema_type, "null"]
            return schema
        if "anyOf" in schema:
            schema["anyOf"] = [*schema["anyOf"], {"type": "null"}]
            return schema
        return {"anyOf": [schema, {"type": "null"}]}

    def _apply_occurs(self, element: ET.Element, schema: JsonSchema) -> JsonSchema:  # noqa: PLR6301
        min_occurs_value = element.get("minOccurs")
        min_occurs = (_safe_int(min_occurs_value) or 0) if min_occurs_value else 1
        max_occurs = element.get("maxOccurs")
        if max_occurs is None or max_occurs == "1":
            return schema
        array_schema: JsonSchema = {"type": "array", "items": schema}
        if min_occurs:
            array_schema["minItems"] = min_occurs
        if max_occurs != UNBOUNDED:
            max_items = _safe_int(max_occurs)
            if max_items is not None:
                array_schema["maxItems"] = max_items
        array_schema[INTERNAL_OCCURS_ARRAY] = True
        return array_schema

    def _convert_simple_type(self, simple_type: ET.Element) -> JsonSchema:
        if (restriction := _first_xsd_child(simple_type, "restriction")) is not None:
            base_schema = self._restriction_base_schema(restriction)
            schema = self._apply_restriction_facets(restriction, base_schema)
        elif (list_element := _first_xsd_child(simple_type, "list")) is not None:
            item_type = list_element.get("itemType")
            item_schema = self._schema_for_qname(item_type, list_element) if item_type else {}
            if (inline_item_type := _first_xsd_child(list_element, "simpleType")) is not None:
                item_schema = self._convert_simple_type(inline_item_type)
            schema = {"type": "array", "items": item_schema}
        elif (union_element := _first_xsd_child(simple_type, "union")) is not None:
            schemas = []
            member_types = (union_element.get("memberTypes") or "").split()
            schemas.extend(self._schema_for_qname(member_type, union_element) for member_type in member_types)
            schemas.extend(self._convert_simple_type(child) for child in _xsd_children(union_element, "simpleType"))
            schema = {"anyOf": schemas} if schemas else {}
        else:
            schema = _copy_schema(STRING_SCHEMA)

        if documentation := _documentation(simple_type):
            schema = _copy_schema(schema)
            schema["description"] = documentation
        return schema

    def _restriction_base_schema(self, restriction: ET.Element) -> JsonSchema:
        if base := restriction.get("base"):
            key = self._resolve_key(base, self.simple_types, element=restriction)
            if key in self.simple_types:
                if self._type_definition_key(key) in self._building_definitions:
                    return _copy_schema(STRING_SCHEMA)
                return self._convert_simple_type(self.simple_types[key])
            return self._schema_for_qname(base, restriction)
        if (simple_type := _first_xsd_child(restriction, "simpleType")) is not None:
            return self._convert_simple_type(simple_type)
        return _copy_schema(STRING_SCHEMA)

    def _apply_restriction_facets(self, restriction: ET.Element, schema: JsonSchema) -> JsonSchema:  # noqa: PLR0912
        schema = _copy_schema(schema)
        enum_values: list[Any] = []
        base_pattern = schema.get("pattern")
        first_pattern: str | None = None
        patterns: dict[str, None] | None = None
        for facet in restriction:
            if _namespace(facet.tag) != XML_SCHEMA_NAMESPACE:
                continue
            value = facet.get("value")
            if value is None:
                continue
            match _local_name(facet.tag):
                case "enumeration":
                    enum_values.append(self._parse_literal(value, schema))
                case "pattern":
                    if _is_supported_pattern(value):
                        if first_pattern is None:
                            first_pattern = value
                        elif value != first_pattern:
                            if patterns is None:
                                patterns = {first_pattern: None}
                            patterns[value] = None
                        schema["pattern"] = value
                case "length":
                    self._set_length(schema, value, same=True)
                case "minLength":
                    self._set_length(schema, value, same=False, minimum=True)
                case "maxLength":
                    self._set_length(schema, value, same=False, minimum=False)
                case "minInclusive":
                    if self._is_numeric_schema(schema):
                        schema["minimum"] = self._parse_number(value, schema)
                case "maxInclusive":
                    if self._is_numeric_schema(schema):
                        schema["maximum"] = self._parse_number(value, schema)
                case "minExclusive":
                    if self._is_numeric_schema(schema):
                        schema["exclusiveMinimum"] = self._parse_number(value, schema)
                case "maxExclusive":
                    if self._is_numeric_schema(schema):
                        schema["exclusiveMaximum"] = self._parse_number(value, schema)
                case "totalDigits":
                    schema["x-xsd-totalDigits"] = _safe_int(value)
                case "fractionDigits":
                    schema["x-xsd-fractionDigits"] = _safe_int(value)
        if patterns is not None:
            # Match the full XSD value; the lookahead also selects Pydantic's Python regex engine.
            schema["pattern"] = rf"(?=\A)(?:{'|'.join(patterns)})\Z"
        if first_pattern is not None and base_pattern is not None and base_pattern != schema["pattern"]:
            schema["pattern"] = rf"(?=\A(?:{base_pattern})\Z)(?:{schema['pattern']})\Z"
        if enum_values:
            schema["enum"] = enum_values
        return schema

    def _set_length(self, schema: JsonSchema, value: str, *, same: bool, minimum: bool = True) -> None:  # noqa: PLR6301
        length = _safe_int(value)
        if length is None:
            return
        min_key, max_key = ("minItems", "maxItems") if schema.get("type") == "array" else ("minLength", "maxLength")
        if same:
            schema[min_key] = length
            schema[max_key] = length
        elif minimum:
            schema[min_key] = length
        else:
            schema[max_key] = length

    def _parse_literal(self, value: str, schema: JsonSchema, *, parse_temporal: bool = False) -> Any:
        if any_of := schema.get("anyOf"):
            return self._parse_union_literal(value, any_of, parse_temporal=parse_temporal)
        if parse_temporal and (parsed_temporal := self._parse_temporal_literal(value, schema)) is not None:
            return parsed_temporal
        match schema.get("type"):
            case "array":
                return self._parse_list_literal(value, schema, parse_temporal=parse_temporal)
            case "integer":
                parsed: Any = _safe_int(value)
            case "number" if schema.get("format") == "decimal":
                parsed = _safe_decimal(value)
            case "number":
                parsed = _safe_float(value, source_safe_non_finite=self.source_safe_non_finite)
            case "boolean":
                parsed = _safe_bool(value)
            case _:
                return value
        return parsed if parsed is not None else value

    @staticmethod
    def _parse_temporal_literal(value: str, schema: JsonSchema) -> _PythonExpression | None:
        if schema.get("type") != "string":
            return None
        match schema.get("format"):
            case "date":
                return _safe_date_expression(value)
            case "time":
                return _safe_time_expression(value)
            case "date-time":
                return _safe_datetime_expression(value)
            case "duration":
                return _safe_day_time_duration_expression(value)
        return None

    def _parse_union_literal(self, value: str, schemas: list[JsonSchema], *, parse_temporal: bool = False) -> Any:
        for schema in schemas:
            parsed = self._parse_literal(value, schema, parse_temporal=parse_temporal)
            if parsed != value or schema.get("type") == "string":
                return parsed
        return value

    def _parse_list_literal(self, value: str, schema: JsonSchema, *, parse_temporal: bool = False) -> list[Any]:
        items = schema.get("items")
        item_schema = items if isinstance(items, dict) else STRING_SCHEMA
        return [self._parse_literal(item, item_schema, parse_temporal=parse_temporal) for item in value.split()]

    def _parse_number(self, value: str, schema: JsonSchema) -> int | float | Decimal | str:
        if schema.get("type") == "integer":
            return integer if (integer := _safe_int(value)) is not None else value
        if schema.get("format") == "decimal":
            return decimal if (decimal := _safe_decimal(value)) is not None else value
        return (
            number
            if (number := _safe_float(value, source_safe_non_finite=self.source_safe_non_finite)) is not None
            else value
        )

    @staticmethod
    def _is_numeric_schema(schema: JsonSchema) -> bool:
        return schema.get("type") in {"integer", "number"}

    def _new_object_schema(
        self,
        owner: ET.Element,
        *,
        mixed_owners: tuple[ET.Element, ...] | None = None,
    ) -> JsonSchema:
        schema: JsonSchema = {"type": "object", "properties": {}}
        self._apply_open_content(owner, schema)
        self._apply_model_group(owner, schema)
        self._apply_attributes(owner, schema)
        if mixed_owners:
            mixed_owner, *additional_owners = mixed_owners
            self._apply_mixed_content(mixed_owner, schema, *additional_owners)
        else:
            self._apply_mixed_content(owner, schema)
        return schema

    def _convert_complex_type(self, complex_type: ET.Element) -> JsonSchema:
        if (complex_content := _first_xsd_child(complex_type, "complexContent")) is not None:
            return self._convert_complex_content(complex_content, complex_type)
        if (simple_content := _first_xsd_child(complex_type, "simpleContent")) is not None:
            return self._convert_simple_content(simple_content, complex_type)

        schema = self._new_object_schema(complex_type)
        if documentation := _documentation(complex_type):
            schema["description"] = documentation
        return schema

    def _convert_complex_content(self, complex_content: ET.Element, owner: ET.Element) -> JsonSchema:
        child = _first_xsd_child(complex_content, "extension", "restriction")
        if child is None:
            return self._convert_complex_type_without_content(owner)

        schema = self._new_object_schema(child, mixed_owners=(complex_content, owner))
        if _local_name(child.tag) == "extension" and (base := child.get("base")):
            owner_key = self._key_for_declaration(self.complex_types, owner)
            base_key = self._resolve_key(base, self.simple_types, self.complex_types, element=child)
            if owner_key is not None and base_key == owner_key:
                base_complex_type = self._redefined_base_complex_types.get(base_key)
                if base_complex_type is not None:
                    return {"allOf": [self._convert_complex_type(base_complex_type), schema]}
                return schema
            return {"allOf": [self._schema_for_qname(base, child), schema]}
        return schema

    def _convert_complex_type_without_content(self, complex_type: ET.Element) -> JsonSchema:
        return self._new_object_schema(complex_type)

    def _apply_open_content(self, owner: ET.Element, schema: JsonSchema) -> None:
        open_content = _first_xsd_child(owner, "openContent")
        if open_content is not None:
            if open_content.get("mode", "interleave") != "none" and _first_xsd_child(open_content, "any") is not None:
                schema["additionalProperties"] = True
            return
        if (
            self.default_open_content is not None
            and _first_xsd_child(self.default_open_content, "any") is not None
            and (self._has_explicit_content(owner) or self.default_open_content.get("appliesToEmpty") in {"true", "1"})
        ):
            schema["additionalProperties"] = True

    @staticmethod
    def _has_explicit_content(owner: ET.Element) -> bool:
        return _first_xsd_child(owner, "sequence", "all", "choice", "group") is not None

    def _inherited_simple_content_schema(self, child: ET.Element, owner: ET.Element) -> JsonSchema | None:
        """Copy only generated simple-content objects, preserving the ordinary scalar path."""
        if not (base := child.get("base")) or self._qname_namespace(base, child) == XML_SCHEMA_NAMESPACE:
            return None
        base_key = self._resolve_key(base, self.simple_types, self.complex_types, element=child)
        base_type = self.complex_types.get(base_key)
        if base_type is None or _first_xsd_child(base_type, "simpleContent") is None:
            return None
        if base_type is owner and (original := self._redefined_base_complex_types.get(base_key)) is not None:
            return _copy_schema(self._convert_complex_type(original))
        definition_key = self._type_definition_key(base_key)
        if definition_key in self._building_definitions:
            return None
        schema = _copy_schema(self._build_definition(definition_key))
        schema.pop("title", None)
        return schema

    def _inherited_content_restriction(self, child: ET.Element, value_schema: JsonSchema) -> JsonSchema:
        """Apply derived facets to the scalar behind an inherited named simple type."""
        if (simple_type := _first_xsd_child(child, "simpleType")) is not None:
            return self._convert_simple_type(simple_type)
        if (ref := value_schema.get("$ref")) and (
            resolved := self._definitions.get(ref.removeprefix("#/definitions/"))
        ) is not None:
            value_schema = _copy_schema(resolved)
            value_schema.pop("title", None)
        return value_schema

    def _convert_simple_content(self, simple_content: ET.Element, owner: ET.Element) -> JsonSchema:
        child = _first_xsd_child(simple_content, "extension", "restriction")
        inherited = self._inherited_simple_content_schema(child, owner) if child is not None else None
        if inherited is not None:
            value_schema = inherited["properties"]["value"]
        elif child is None:
            value_schema = _copy_schema(STRING_SCHEMA)
        elif base := child.get("base"):
            value_schema = self._schema_for_qname(base, child)
        elif (simple_type := _first_xsd_child(child, "simpleType")) is not None:
            value_schema = self._convert_simple_type(simple_type)
        else:
            value_schema = _copy_schema(STRING_SCHEMA)
        if child is not None and _local_name(child.tag) == "restriction":
            if inherited is not None:
                value_schema = self._inherited_content_restriction(child, value_schema)
            value_schema = self._apply_restriction_facets(child, value_schema)

        if inherited is None:
            schema: JsonSchema = {"type": "object", "properties": {"value": value_schema}, "required": ["value"]}
        else:
            schema = inherited
            schema["properties"]["value"] = value_schema
        inherited_required = frozenset(schema["required"]) if inherited is not None else None
        self._apply_attributes(owner, schema, inherited_required=inherited_required)
        if child is not None:
            self._apply_attributes(child, schema, inherited_required=inherited_required)
        return schema

    def _apply_model_group(
        self,
        owner: ET.Element,
        schema: JsonSchema,
        *,
        occurrence: _OccurrenceContext = DEFAULT_OCCURRENCE,
    ) -> None:
        for child in owner:
            if _is_xsd_element(child, "sequence", "all", "choice"):
                self._apply_particle(
                    child,
                    schema,
                    occurrence=occurrence,
                )
            elif _is_xsd_element(child, "group"):
                self._apply_group(
                    child,
                    schema,
                    occurrence=occurrence,
                )

    def _apply_particle(
        self,
        particle: ET.Element,
        schema: JsonSchema,
        *,
        occurrence: _OccurrenceContext,
    ) -> None:
        if particle.get("maxOccurs") == "0":
            return
        is_choice = _local_name(particle.tag) == "choice"
        particle_required = occurrence.required and particle.get("minOccurs", "1") != "0" and not is_choice
        particle_repeating = self._is_repeating(particle)
        repeating = occurrence.repeating or particle_repeating
        min_items = self._combine_min_items(
            occurrence.min_items,
            particle.get("minOccurs") if occurrence.min_items is not None or particle_repeating else None,
        )
        child_min_items = min_items if particle_required and not is_choice else None
        max_items = self._combine_max_items(
            occurrence.max_items,
            particle.get("maxOccurs") if particle_repeating else None,
        )
        child_occurrence = _OccurrenceContext(
            required=particle_required,
            repeating=repeating,
            min_items=child_min_items,
            max_items=max_items,
        )
        for child in particle:
            if _is_xsd_element(child, "element"):
                self._add_property_from_element(
                    child,
                    schema,
                    occurrence=child_occurrence,
                )
            elif _is_xsd_element(child, "sequence", "all", "choice"):
                self._apply_particle(
                    child,
                    schema,
                    occurrence=child_occurrence,
                )
            elif _is_xsd_element(child, "group"):
                self._apply_group(
                    child,
                    schema,
                    occurrence=child_occurrence,
                )
            elif _is_xsd_element(child, "any") and child.get("maxOccurs") != "0":
                schema["additionalProperties"] = True

    def _apply_group(
        self,
        group: ET.Element,
        schema: JsonSchema,
        *,
        occurrence: _OccurrenceContext,
    ) -> None:
        if group.get("maxOccurs") == "0":
            return
        group_required = occurrence.required and group.get("minOccurs", "1") != "0"
        group_repeating = self._is_repeating(group)
        repeating = occurrence.repeating or group_repeating
        min_items = self._combine_min_items(
            occurrence.min_items,
            group.get("minOccurs") if occurrence.min_items is not None or group_repeating else None,
        )
        child_min_items = min_items if group_required else None
        max_items = self._combine_max_items(occurrence.max_items, group.get("maxOccurs") if group_repeating else None)
        child_occurrence = _OccurrenceContext(
            required=group_required,
            repeating=repeating,
            min_items=child_min_items,
            max_items=max_items,
        )
        ref = group.get("ref")
        if not ref:
            self._apply_particle(
                group,
                schema,
                occurrence=child_occurrence,
            )
            return
        target_key = self._resolve_key(ref, self.groups, element=group)
        target = self.groups.get(target_key)
        already_active = target_key in self._active_groups
        if already_active:
            target = self._redefined_base_groups.get(target_key)
        if target is not None:
            if not already_active:
                self._active_groups.add(target_key)
            try:
                self._apply_model_group(
                    target,
                    schema,
                    occurrence=child_occurrence,
                )
            finally:
                if not already_active:
                    self._active_groups.remove(target_key)

    def _add_property_from_element(
        self,
        element: ET.Element,
        schema: JsonSchema,
        *,
        occurrence: _OccurrenceContext,
    ) -> None:
        if element.get("maxOccurs") == "0":
            return
        name = element.get("name") or _local_name(element.get("ref", ""))
        if not name:
            return
        properties = schema.setdefault("properties", {})
        property_schema = self._convert_element(element)
        if occurrence.repeating:
            property_schema = self._repeat_property_schema(
                property_schema,
                min_items=occurrence.min_items,
                max_items=occurrence.max_items,
            )
        properties[name] = property_schema
        if occurrence.required and element.get("minOccurs", "1") != "0":
            schema.setdefault("required", []).append(name)

    def _apply_attributes(
        self, owner: ET.Element, schema: JsonSchema, *, inherited_required: frozenset[str] | None = None
    ) -> None:
        for child in owner:
            if _is_xsd_element(child, "attribute"):
                if inherited_required is not None and child.get("use") == "prohibited":
                    name = child.get("name") or _local_name(child.get("ref", ""))
                    schema.get("properties", {}).pop(name, None)
                self._add_attribute(child, schema, inherited_required=inherited_required)
            elif _is_xsd_element(child, "attributeGroup"):
                self._apply_attribute_group(child, schema, inherited_required=inherited_required)
            elif _is_xsd_element(child, "anyAttribute"):
                schema["additionalProperties"] = True

    def _apply_attribute_group(
        self, attribute_group: ET.Element, schema: JsonSchema, *, inherited_required: frozenset[str] | None = None
    ) -> None:
        ref = attribute_group.get("ref")
        if not ref:
            self._apply_attributes(attribute_group, schema, inherited_required=inherited_required)
            return
        target_key = self._resolve_key(ref, self.attribute_groups, element=attribute_group)
        target = self.attribute_groups.get(target_key)
        already_active = target_key in self._active_attribute_groups
        if already_active:
            target = self._redefined_base_attribute_groups.get(target_key)
        if target is not None:
            if not already_active:
                self._active_attribute_groups.add(target_key)
            try:
                self._apply_attributes(target, schema, inherited_required=inherited_required)
            finally:
                if not already_active:
                    self._active_attribute_groups.remove(target_key)

    def _add_attribute(
        self, attribute: ET.Element, schema: JsonSchema, *, inherited_required: frozenset[str] | None = None
    ) -> None:
        if attribute.get("use") == "prohibited":
            return
        ref = attribute.get("ref")
        source_attribute = (
            self.attributes.get(self._resolve_key(ref, self.attributes, element=attribute)) if ref else None
        )
        if source_attribute is None:
            source_attribute = attribute
        name = attribute.get("name") or source_attribute.get("name") or _local_name(ref or "")
        if not name:
            return
        use = attribute.get("use") or source_attribute.get("use")
        if type_name := attribute.get("type") or source_attribute.get("type"):
            attribute_schema = self._schema_for_qname(type_name, attribute)
        else:
            simple_type = _first_xsd_child(attribute, "simpleType")
            if simple_type is None:
                simple_type = _first_xsd_child(source_attribute, "simpleType")
            attribute_schema = self._convert_simple_type(simple_type) if simple_type is not None else STRING_SCHEMA
        attribute_schema = _copy_schema(attribute_schema)
        if documentation := _documentation(attribute) or _documentation(source_attribute):
            attribute_schema["description"] = documentation
        default = attribute.get("default")
        if default is None and "default" not in attribute.attrib and "fixed" not in attribute.attrib:
            default = source_attribute.get("default")
        if default is not None:
            attribute_schema["default"] = self._parse_literal(default, attribute_schema, parse_temporal=True)
        fixed = attribute.get("fixed")
        if fixed is None and "fixed" not in attribute.attrib:
            fixed = source_attribute.get("fixed")
        if fixed is not None:
            self._apply_fixed_value(attribute_schema, self._parse_literal(fixed, attribute_schema))
        schema.setdefault("properties", {})[name] = attribute_schema
        if use == "required" and (inherited_required is None or name not in inherited_required):
            schema.setdefault("required", []).append(name)

    @staticmethod
    def _apply_mixed_content(
        owner: ET.Element,
        schema: JsonSchema,
        *additional_owners: ET.Element,
    ) -> None:
        for candidate in (owner, *additional_owners):
            if candidate.get("mixed") == "true":
                schema.setdefault("properties", {})["value"] = _copy_schema(STRING_SCHEMA)
                return

    def _schema_for_substitution_group(self, head_key: QNameKey) -> JsonSchema | None:
        member_keys = self.substitution_groups.get(head_key)
        if not member_keys:
            return None
        schemas = []
        head_element = self.elements.get(head_key)
        if head_element is not None and head_element.get("abstract") != "true":
            schemas.append({"$ref": self._ref_for_element_key(head_key)})
        schemas.extend({"$ref": self._ref_for_element_key(key)} for key in sorted(member_keys, key=self._sort_key))
        return {"anyOf": schemas} if len(schemas) > 1 else schemas[0]

    @staticmethod
    def _is_repeating(element: ET.Element) -> bool:
        max_occurs = element.get("maxOccurs")
        return max_occurs is not None and max_occurs != "1"

    def _combine_max_items(self, parent_max_items: int | None, max_occurs: str | None) -> int | None:  # noqa: PLR6301
        if max_occurs is None:
            return parent_max_items
        if max_occurs == UNBOUNDED or parent_max_items is None:
            return None
        max_items = _safe_int(max_occurs)
        if max_items is None:
            return parent_max_items
        return parent_max_items * max_items

    def _combine_min_items(self, parent_min_items: int | None, min_occurs: str | None) -> int | None:  # noqa: PLR6301
        if min_occurs is None:
            return parent_min_items
        min_items = _safe_int(min_occurs)
        if min_items is None:
            return parent_min_items
        return parent_min_items * min_items if parent_min_items is not None else min_items

    def _repeat_property_schema(
        self,
        schema: JsonSchema,
        *,
        min_items: int | None,
        max_items: int | None,
    ) -> JsonSchema:
        occurs_array = schema.get("type") == "array" and schema.get(INTERNAL_OCCURS_ARRAY)
        if not occurs_array:
            array_schema: JsonSchema = {"type": "array", "items": schema, INTERNAL_OCCURS_ARRAY: True}
        else:
            array_schema = _copy_schema(schema)
        if min_items is not None:
            self._set_repeated_bound(array_schema, "minItems", min_items)
        if max_items is None:
            array_schema.pop("maxItems", None)
        elif not occurs_array or "maxItems" in array_schema:
            self._set_repeated_bound(array_schema, "maxItems", max_items)
        return array_schema

    @staticmethod
    def _set_repeated_bound(schema: JsonSchema, key: str, value: int) -> None:
        current = schema.get(key)
        schema[key] = current * value if isinstance(current, int) else value

    def _strip_internal_metadata(self, value: Any) -> None:
        if isinstance(value, dict):
            value.pop(INTERNAL_OCCURS_ARRAY, None)
            for item in value.values():
                self._strip_internal_metadata(item)
        elif isinstance(value, list):
            for item in value:
                self._strip_internal_metadata(item)

    def _schema_for_qname(self, qname: str | None, element: ET.Element | None = None) -> JsonSchema:
        if not qname:  # pragma: no cover
            return {}
        local = _local_name(qname)
        namespace = self._qname_namespace(qname, element)
        is_unprefixed_builtin = namespace is None and local in BUILTIN_TYPE_SCHEMAS
        key = self._resolve_key(qname, self.simple_types, self.complex_types, element=element)
        is_user_defined = key in self.simple_types or key in self.complex_types
        if namespace == XML_SCHEMA_NAMESPACE or (is_unprefixed_builtin and not is_user_defined):
            return self._builtin_type_schema(local, default=STRING_SCHEMA)
        if is_user_defined:
            return {"$ref": self._ref_for_type_key(key)}
        return self._builtin_type_schema(local, default={})

    def _builtin_type_schema(self, local: str, *, default: JsonSchema) -> JsonSchema:
        schema = _copy_schema(BUILTIN_TYPE_SCHEMAS.get(local, default))
        if local == "dateTime" and self.use_xmlschema_datetime_default:
            schema["x-python-type"] = "datetime"
        return schema

    def _qname_namespace(self, qname: str, element: ET.Element | None = None) -> str | None:
        if ":" not in qname:
            return None
        prefix = qname.split(":", maxsplit=1)[0]
        return self._namespaces_for(element).get(prefix)

    def _qname_key(self, qname: str, element: ET.Element | None = None) -> QNameKey:
        return (self._qname_namespace(qname, element), _local_name(qname))

    def _resolve_key(
        self,
        qname: str | QNameKey,
        *registries: dict[QNameKey, ET.Element],
        element: ET.Element | None = None,
    ) -> QNameKey:
        key = qname if isinstance(qname, tuple) else self._qname_key(qname, element)
        if any(key in registry for registry in registries):
            return key
        namespace, local = key
        if namespace is not None:
            return key
        matches = {candidate for registry in registries for candidate in registry if candidate[1] == local}
        if len(matches) == 1:
            return next(iter(matches))
        return key

    def _resolved_referenced_elements(self) -> set[QNameKey]:
        resolved = {self._resolve_key(ref, self.elements) for ref in self.referenced_elements}
        for ref in tuple(resolved):
            resolved.update(self.substitution_groups.get(ref, set()))
        return resolved

    @staticmethod
    def _key_for_declaration(registry: dict[QNameKey, ET.Element], declaration: ET.Element) -> QNameKey | None:
        return next((key for key, element in registry.items() if element is declaration), None)

    @staticmethod
    def _type_definition_key(key: QNameKey) -> DefinitionKey:
        return ("type", key[0], key[1])

    @staticmethod
    def _element_definition_key(key: QNameKey) -> DefinitionKey:
        return ("element", key[0], key[1])

    @staticmethod
    def _qname_from_definition_key(key: DefinitionKey) -> QNameKey:
        return key[1], key[2]

    def _definition_name(self, key: DefinitionKey) -> str:
        return self._definition_names.get(key, key[2])

    def _ref_for_type_key(self, key: QNameKey) -> str:
        return self._ref_for_definition_key(self._type_definition_key(key))

    def _ref_for_element_key(self, key: QNameKey) -> str:
        return self._ref_for_definition_key(self._element_definition_key(key))

    def _ref_for_definition_key(self, key: DefinitionKey) -> str:
        return self._definition_ref(self._definition_name(key))

    def _namespaces_for(self, element: ET.Element | None) -> dict[str, str]:
        if element is None:  # pragma: no cover
            return self.namespaces
        return self._element_namespaces.get(id(element), self.namespaces)

    @staticmethod
    def _sort_key(key: QNameKey) -> tuple[str, str]:
        namespace, local = key
        return namespace or "", local

    @staticmethod
    def _definition_sort_key(key: DefinitionKey) -> tuple[str, str, str]:
        kind, namespace, local = key
        return namespace or "", local, kind

    @staticmethod
    def _definition_ref(name: str) -> str:
        return f"#/definitions/{name}"


class XMLSchemaParser(JsonSchemaParser):
    """Parse XML Schema documents by converting them to JSON Schema first."""

    _config_class_name = "XMLSchemaParserConfig"
    _cache_parsed_sources_from_path: ClassVar[bool] = True

    def __init__(
        self,
        source: str | Path | list[Path] | ParseResult,
        *,
        config: XMLSchemaParserConfig | None = None,
        **options: Unpack[XMLSchemaParserConfigDict],
    ) -> None:
        """Initialize the XML Schema parser with JSON Schema parser configuration."""
        if config is None:
            self.use_xmlschema_datetime_default = options.get("target_datetime_class") is None
            options.setdefault("target_datetime_class", DatetimeClassType.Awaredatetime)
        else:
            self.use_xmlschema_datetime_default = config.target_datetime_class is None
            if self.use_xmlschema_datetime_default:
                config_updates: dict[str, Any] = {"target_datetime_class": DatetimeClassType.Awaredatetime}
                config = config.model_copy(update=config_updates)
        super().__init__(source=source, config=config, **options)

    def _source_from_xml_path(self, path: Path) -> Source:
        relative_path = path.relative_to(self.base_path)
        if not self._use_parsed_source_cache:
            return Source(path=relative_path, text=_read_xml_text(path, self.encoding))

        config = cast("XMLSchemaParserConfig", self.config)
        return Source(
            path=relative_path,
            raw_data=_load_xml_schema_data_from_path(
                path,
                self.base_path,
                self.encoding,
                xmlschema_version=config.xmlschema_version,
                schema_version_mode=config.schema_version_mode,
                use_xmlschema_datetime_default=self.use_xmlschema_datetime_default,
                source_safe_non_finite=True,
            ),
        )

    @property
    def iter_source(self) -> Iterator[Source]:
        """Iterate over XML Schema sources with XML encoding detection for local files."""
        match self.source:
            case Path() as path:
                if path.is_dir():  # pragma: no cover
                    for file_path in sorted(path.rglob("*"), key=lambda item: item.name):
                        if file_path.is_file():
                            yield self._source_from_xml_path(file_path)
                else:
                    yield self._source_from_xml_path(path)
            case list() as paths:  # pragma: no cover
                for path in paths:
                    yield self._source_from_xml_path(path)
            case _:
                yield from super().iter_source

    def parse_raw(self) -> None:
        """Parse all XML Schema input sources into data models."""
        config = cast("XMLSchemaParserConfig", self.config)
        self._parse_converted_sources(
            lambda: _XMLSchemaConverter(
                base_path=self.base_path,
                encoding=self.encoding,
                xmlschema_version=config.xmlschema_version,
                schema_version_mode=config.schema_version_mode,
                use_xmlschema_datetime_default=self.use_xmlschema_datetime_default,
                source_safe_non_finite=True,
            )
        )
        self._register_runtime_expression_imports()

    def _register_runtime_expression_imports(self) -> None:
        """Prepare XML expressions once so repeated field import collection stays constant time."""
        # Share immutable expressions within this generation without retaining schemas in a global cache.
        prepared_patterns: dict[str, PythonRuntimeExpression] = {}
        for model in self.results:
            for field in model.fields:
                field._set_runtime_expression_imports(_collect_python_expression_imports(field.default))  # noqa: SLF001
                if (prepare_patterns := field.PREPARE_PYTHON_PATTERNS) is not None:
                    # Only combined XSD patterns use this prefix; single patterns keep their existing representation.
                    prepare_patterns(field, r"(?=\A", prepared_patterns)


__all__ = ["XMLSchemaParser", "convert_xml_schema_data", "detect_xmlschema_version", "is_xml_schema_text"]
