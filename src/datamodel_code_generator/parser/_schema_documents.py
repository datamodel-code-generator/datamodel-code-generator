"""The document reading of the JSON Schema parser (L0): loading documents, their schema resources and the sources.

These functions load a referenced document (from a local mirror of an HTTP reference, a file or a URL text) and read
it under ``--read-kubernetes-extensions``, ``--read-format-as-annotation`` and ``--use-single-value-enum-as-const``,
register the schema resources a document declares in the schema graph (rewriting the references an embedded `$id`
resolves, copy-on-write) and index it so that the graph locates its JSON objects, map the references of documents that
resolve them against themselves, like OpenAPI, walk the definitions and their namespaces, read the input sources and
parse each of them, and list the targets a source reserves.

Each function takes the parser through ``SchemaDocumentsHost``, which ``JsonSchemaParser`` satisfies structurally.
The methods that tests call or that other parts of the parser call stay on the parser as delegators to these
functions (``parser/jsonschema.py``), and these functions call them through the parser. Parser methods too:
``_get_ref_body``, ``_resolve_local_ref_path`` and ``parse_raw``, which warn, and ``_get_ref_body_from_url`` and
``_get_ref_body_from_remote``, the frames the warning of ``_resolve_local_ref_path`` names;
``_load_ref_data_from_path``, which reads ``load_data_from_path`` of ``parser/jsonschema.py``;
``_init_schema_resources``, which declares the attributes of the parser; the context managers ``root_id_context``
and ``_parsing_session``, which ``--debug`` traces as parser methods; the override ``_source_from_path``, which calls
``super()``; the static ``_schema_object_raw_key_sets``; and ``_is_version_definition_namespace_name``, the one
reader of the ``re`` import that ``parser/jsonschema.py`` keeps. They read a loaded document by JSON pointer with the
walk of ``parser/_schema_reading.py``, which the reference reading (``parser/_schema_refs.py``) shares.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from copy import copy
from dataclasses import replace
from functools import partial
from itertools import chain
from operator import is_not
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypeAlias, cast
from urllib.parse import ParseResult, unquote, urljoin, urlparse

from typing_extensions import Protocol

from datamodel_code_generator import Error, InvalidClassNameError, InvalidFileFormatError, JsonSchemaVersion
from datamodel_code_generator._document_reader import member_key, refuse_oversized_document
from datamodel_code_generator._json_pointer import escape_token, is_pointer_fragment
from datamodel_code_generator._json_schema import DEFINITION_KEYS, NAMESPACE_DEFINITION_KEYS, subschema_entries
from datamodel_code_generator._openapi_objects import held_schemas
from datamodel_code_generator._paths import file_uri, file_uri_path, files_under
from datamodel_code_generator._source import _load_parser_source_data_from_path_bytes, load_data
from datamodel_code_generator._url_redaction import redact_url
from datamodel_code_generator.parser._null_facts import dump_null_sources, resolve_null_facts
from datamodel_code_generator.parser._schema_graph import (
    ConvertedRefs,
    DocumentId,
    ReservedTarget,
    ResolvedRef,
    keep_resolved_ref,
    pointer_ref_spelling,
    walk_schema_resources,
)
from datamodel_code_generator.parser._schema_object import (
    _READ_AS_CONST_KEY,
    JSONReference,
    JsonSchemaObject,
    get_ref_type,
)
from datamodel_code_generator.parser._schema_reading import (
    _MISSING_JSON_POINTER,
    _document_child_entries,
    _DocumentEntries,
    _fragment_value,
    _is_plain_name_fragment,
    get_model_by_path,
)
from datamodel_code_generator.parser.base import title_to_class_name
from datamodel_code_generator.parser.schema_version import JsonSchemaFeatures, _detect_declared_jsonschema_version
from datamodel_code_generator.python_literal import stable_repr
from datamodel_code_generator.reference import SchemaLocation, context_variable, is_url
from datamodel_code_generator.util import get_yaml_parse_errors

if TYPE_CHECKING:
    from collections import defaultdict
    from collections.abc import Callable, Generator, Iterator, Sequence
    from contextlib import AbstractContextManager

    from datamodel_code_generator._parser_context import ParserSourceContext
    from datamodel_code_generator._shared_types import DefaultPutDict, ResponseObserver
    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator.config import JSONSchemaParserConfig
    from datamodel_code_generator.enums import InputFileType
    from datamodel_code_generator.format import PythonVersion
    from datamodel_code_generator.parser._schema_graph import RefReading, ResourceRegistry, SchemaGraphBuilder
    from datamodel_code_generator.parser._schema_reading import SlashNames
    from datamodel_code_generator.parser.base import Source
    from datamodel_code_generator.parser.schema_version import DocumentDialect
    from datamodel_code_generator.reference import ModelResolver

__all__ = [
    "SchemaDocumentsHost",
    "SchemaPathsHost",
    "add_id_callback",
    "cache_source_ref_body",
    "declared_location",
    "document_as_written",
    "document_id_field",
    "document_source",
    "duplicate_name_location",
    "first_definitions",
    "get_context_source_path_parts",
    "get_ref_body_from_local_http_path",
    "has_schema_affecting_keywords",
    "implicit_schema_resources",
    "index_document",
    "is_input_document_root",
    "iter_definition_namespace_entries",
    "iter_local_source_paths",
    "iter_schema_definition_entries",
    "iter_schema_definition_entry",
    "iter_schema_resource_children",
    "known_schema_object_raw_keys",
    "lenient_pointer_exists",
    "lenient_ref",
    "load_ref_data_from_local_http_file",
    "load_ref_data_from_text",
    "load_source_dict",
    "loaded_document_dialect",
    "loading_document_dialect",
    "loads_document",
    "mount_converted_schema",
    "normalize_external_ref",
    "note_converted_document",
    "note_plain_name",
    "parse_converted_sources",
    "parse_id",
    "parse_ref",
    "parse_source",
    "plain_name_ref",
    "prepare_schema_resources",
    "read_document",
    "referenced_document_context",
    "reserved_targets",
    "resolve_lenient_resource_ref",
    "resolve_root_model_name",
    "resolve_schema_resource_ref",
    "rewrite_schema_resource_refs",
    "schema_resource_document",
    "schema_resource_identifier",
]


_SchemaReading: TypeAlias = "Callable[[dict[str, YamlValue]], dict[str, YamlValue]]"
"""One reading of a schema object: it takes the object as the readings before it left it, children read."""


def _read_schema_objects(
    value: YamlValue,
    named_entry_maps: frozenset[str],
    readings: tuple[_SchemaReading, ...],
    entries: _DocumentEntries = "object",
) -> YamlValue:
    """Return a document with each schema object read by ``readings`` in order, after its children (one walk).

    The values of instance data keywords, extensions other than x-propertyNames, discriminator objects, links,
    security requirements and servers hold no schemas and stay as written. Those words are names, not keywords, in
    maps of named entries such as properties, $defs, responses, webhooks, channels or the component maps. Unchanged
    parts are shared, so cached documents stay intact.
    """
    if isinstance(value, list):
        items = [_read_schema_objects(item, named_entry_maps, readings) for item in value]
        return items if any(map(is_not, items, value)) else value
    if not isinstance(value, dict):
        return value
    changed = {
        key: updated
        for key, child in value.items()
        if (child_entries := _document_child_entries(entries, key, named_entry_maps)) is not None
        and (updated := _read_schema_objects(child, named_entry_maps, readings, child_entries)) is not child
    }
    result = {**value, **changed} if changed else value
    return _read_schema_object(result, readings) if entries == "object" else result


def _read_schema_object(schema: dict[str, YamlValue], readings: tuple[_SchemaReading, ...]) -> dict[str, YamlValue]:
    """Return one schema object, its children already read, read by each of ``readings`` in turn."""
    for read in readings:
        schema = read(schema)
    return schema


def _single_value_enum_as_const(schema: dict[str, YamlValue]) -> dict[str, YamlValue]:
    """Return a schema whose enum lists one value written as the const it equals (``_read_schema_objects``).

    A schema with nullable true beside a value other than null keeps its enum, since a const does not carry nullable.
    Each schema read so that holds a const is marked, so that the const branches the option reads are known wherever
    the parser copies them to.
    """
    if (
        isinstance(enum := schema.get("enum"), list)
        and len(enum) == 1
        and "const" not in schema
        and resolve_null_facts(replace(dump_null_sources(schema), enum_null=enum[0] is None), "enum-as-const").rule
        != "nullable-enum-not-const"
    ):
        schema = {
            ("const" if key == "enum" else key): enum[0] if key == "enum" else item for key, item in schema.items()
        }
    return {**schema, _READ_AS_CONST_KEY: True} if "const" in schema else schema


def _read_format_as_annotation(
    schema: dict[str, YamlValue], *, typed_formats: frozenset[tuple[str, str]]
) -> dict[str, YamlValue]:
    """Return a schema without its format unless the format still selects its type (``_read_schema_objects``).

    ``format: binary`` does, as raw octets are no JSON string, and so does a format that a --type-mappings entry of
    ``typed_formats`` names for a type the schema declares; a schema that declares no type, such as an allOf part, has
    no such entry.
    """
    if "format" not in schema or (format_ := schema["format"]) == "binary":
        return schema
    declared = schema.get("type")
    if isinstance(format_, str) and any(
        (type_, format_) in typed_formats for type_ in (declared if isinstance(declared, list) else [declared])
    ):
        return schema
    return {key: item for key, item in schema.items() if key != "format"}


_INT_OR_STRING_TYPES = frozenset({"integer", "string"})
_INT_OR_STRING_BRANCHES = ({"type": "integer"}, {"type": "string"})
_KUBERNETES_FIXED_FIELD_BLOCKERS = frozenset({"$ref", "allOf", "anyOf", "const", "enum", "oneOf"})


def _is_int_or_string(schema: dict[str, YamlValue]) -> bool:
    """Return whether a schema says it holds an integer or a string, in a form read as ``type: [integer, string]``.

    It writes x-kubernetes-int-or-string true or the format int-or-string, declares no type other than integer, string
    or that pair, and each anyOf or oneOf beside lists exactly the two branches ``{type: integer}``, ``{type: string}``.
    """
    types = schema.get("type")
    return (
        (schema.get("x-kubernetes-int-or-string") is True or schema.get("format") == "int-or-string")
        and (
            types is None
            or (isinstance(types, str) and types in _INT_OR_STRING_TYPES)
            or (
                isinstance(types, list)
                and len(types) == len(_INT_OR_STRING_TYPES)
                and all(name in types for name in _INT_OR_STRING_TYPES)
            )
        )
        and all(
            isinstance(branches := schema[key], list)
            and len(branches) == len(_INT_OR_STRING_BRANCHES)
            and all(branch in branches for branch in _INT_OR_STRING_BRANCHES)
            for key in ("anyOf", "oneOf")
            if key in schema
        )
    )


def _group_version_kind_values(entries: YamlValue) -> dict[str, YamlValue]:
    """Return the apiVersion and the kind that every entry of an x-kubernetes-group-version-kind list names alike.

    The apiVersion is ``group/version``, or the version alone for the core group, whose name is empty. A property the
    entries give several values, and every property for a malformed list, gets none.
    """
    if not isinstance(entries, list) or not entries:
        return {}
    values: dict[str, set[str]] = {"apiVersion": set(), "kind": set()}
    for entry in entries:
        if not (
            isinstance(entry, dict)
            and isinstance(group := entry.get("group"), str)
            and isinstance(version := entry.get("version"), str)
            and isinstance(kind := entry.get("kind"), str)
        ):
            return {}
        values["apiVersion"].add(f"{group}/{version}" if group else version)
        values["kind"].add(kind)
    return {name: next(iter(names)) for name, names in values.items() if len(names) == 1}


def _read_kubernetes_extensions(
    schema: dict[str, YamlValue], *, const_support: bool, read_property: _SchemaReading
) -> dict[str, YamlValue]:
    """Return a schema with its Kubernetes extensions read as the keywords they stand for (``_read_schema_objects``).

    x-kubernetes-int-or-string true, or the format int-or-string, is ``type: [integer, string]`` in place of the
    anyOf or oneOf of those two types (``_is_int_or_string``), without that format. x-kubernetes-preserve-unknown-fields
    true beside properties and no additionalProperties is ``additionalProperties: true``. Where const is a keyword, an
    x-kubernetes-group-version-kind writes the one value it gives the apiVersion or the kind property as the const and
    default of that property, when the property is a plain string schema whose default, if any, is that value; each
    property so written is read by ``read_property``, the readings after this one.
    """
    read = schema
    if _is_int_or_string(read):
        read = {
            key: item
            for key, item in read.items()
            if key not in {"anyOf", "oneOf"} and (key != "format" or item != "int-or-string")
        }
        if not isinstance(read.get("type"), list):
            read["type"] = ["integer", "string"]
    if (
        read.get("x-kubernetes-preserve-unknown-fields") is True
        and "properties" in read
        and "additionalProperties" not in read
    ):
        read = {**read, "additionalProperties": True}
    if (
        const_support
        and isinstance(properties := read.get("properties"), dict)
        and (
            fixed := {
                name: read_property({**prop, "const": value, "default": value})
                for name, value in _group_version_kind_values(read.get("x-kubernetes-group-version-kind")).items()
                if isinstance(prop := properties.get(name), dict)
                and not _KUBERNETES_FIXED_FIELD_BLOCKERS.intersection(prop)
                and prop.get("type", "string") == "string"
                and prop.get("default", value) == value
            }
        )
    ):
        read = {**read, "properties": {**properties, **fixed}}
    return read


def _find_json_schema_anchor_pointer(schema: YamlValue, anchor: str) -> str | None:
    """Return the JSON pointer for an anchor within one schema document."""
    pending: list[tuple[YamlValue, tuple[str, ...]]] = [(schema, ())]
    while pending:
        value, path = pending.pop()
        if not isinstance(value, dict):
            continue
        # An embedded $id starts a distinct schema resource. This search is scoped
        # to the resource supplied as ``schema``, so its anchors must not leak into
        # the parent resource's anchor namespace.
        if path and "$id" in value:
            continue
        if value.get("$anchor") == anchor:
            return "/" + "/".join(escape_token(part) for part in path)
        for keyword, child in value.items():
            pending.extend(
                (subschema, (*path, *map(str, entry))) for entry, subschema in subschema_entries(keyword, child)
            )
    return None


def _document_location(location: str, document: str) -> str:
    """Return a registry location as a reference from a document, local when it lies in the document."""
    target_document, _, pointer = location.partition("#")
    return f"#{pointer}" if target_document == document else location


class SchemaPathsHost(Protocol):
    """What reading the definitions container of a document reads of the parser."""

    def _schema_paths_at(self, at: SchemaLocation | None) -> list[tuple[str, list[str]]]:
        """Return the schema paths for definitions and defs of the document a location is in, None: the parsed one."""


def first_definitions(
    parser: SchemaPathsHost, raw: dict[str, YamlValue], location: SchemaLocation | None
) -> tuple[str, dict[str, YamlValue]]:
    """Return the schema path and the value of the first definitions container of a document that holds an entry.

    The containers are those of the schema paths of the document at ``location`` (None: the document being parsed,
    ``_schema_paths_at``); ``""`` and the last value read, an empty one, when none holds an entry.
    """
    definitions: dict[str, YamlValue] = {}
    for schema_path, split_schema_path in parser._schema_paths_at(location):  # noqa: SLF001
        if definitions := cast("dict[str, YamlValue]", get_model_by_path(raw, split_schema_path)):
            return schema_path, definitions
    return "", definitions


class DocumentNamesHost(Protocol):
    """What naming a loaded document reads of the parser (``schema_resource_document``)."""

    base_path: Path
    _schema_resource_document_aliases: dict[str, str]

    @property
    def model_resolver(self) -> ModelResolver:
        """The resolver of references and class names."""


class SchemaResourcesHost(DocumentNamesHost, Protocol):
    """What resolving a reference by the schema resources and definitions of loaded documents reads of the parser."""

    SCHEMA_OBJECT_TYPE: ClassVar[type[JsonSchemaObject]]
    _named_entry_maps: ClassVar[frozenset[str]]
    allow_remote_refs: bool | None
    raw_obj: dict[str, YamlValue]
    _document_roles: dict[str, dict[str, None]]
    _has_embedded_schema_resources: bool
    _implicit_documents: set[str]
    _lenient_documents: dict[str, dict[str, Any]]
    _resource_ref_warnings: set[str]
    _schema_resource_path_parts: dict[str, list[str]]
    _schema_resource_cache: dict[str, tuple[dict[str, Any], dict[str, Any]]]
    _schema_resource_registry: ResourceRegistry

    @property
    def config(self) -> JSONSchemaParserConfig:
        """The configuration of the parser."""

    @property
    def _pointer_slash_names(self) -> SlashNames:
        """How a JSON pointer reads a ``/`` unescaped inside a name (``SlashNames``)."""

    @property
    def _schema_graph(self) -> SchemaGraphBuilder | None:
        """The schema graph of the parse, once it is built."""

    def _get_ref_body(self, resolved_ref: str) -> dict[str, YamlValue]:
        """Get the body of a reference from URL or remote file."""

    def _is_version_definition_namespace_name(self, name: str) -> bool:
        """Return whether a name of a definitions namespace is a version, like ``v1`` or ``v2_0``."""

    @staticmethod
    def _schema_object_raw_key_sets(
        schema_object_type: type[JsonSchemaObject],
    ) -> tuple[frozenset[str], frozenset[str]]:
        """Return the keywords a schema object type reads and those of them that change the schema."""

    def _source_path_for_diagnostics(self, source_path: Path | None = None) -> str:
        """Return source context without changing parser path semantics."""


class SchemaDocumentsHost(SchemaResourcesHost, Protocol):
    """The options, caches and hooks of the parser that the document reading reads."""

    _cache_local_sources_during_parse: ClassVar[bool]
    _component_schemas_are_resources: ClassVar[bool]
    _input_file_type: ClassVar[InputFileType]
    _non_dict_source_is_invalid: ClassVar[bool]
    class_name: str | None
    current_source_path: Path | None
    custom_class_name_generator: Callable[[str], str] | None
    encoding: str
    http_headers: Sequence[tuple[str, str]] | None
    http_local_ref_path: Path | None
    http_query_parameters: Sequence[tuple[str, str]] | None
    remote_object_cache: DefaultPutDict[str, dict[str, YamlValue]]
    reserved_refs: defaultdict[tuple[str, ...], set[str]]
    read_format_as_annotation: bool
    source: str | Path | list[Path] | ParseResult | dict[str, YamlValue]
    type_mappings: dict[tuple[str, str], str]
    untrusted_input: bool
    _cache_local_sources: bool
    _document_dialects: dict[str, DocumentDialect]
    _referring_dialect: DocumentDialect | None
    _documents_as_written: dict[int, tuple[dict[str, YamlValue], dict[str, YamlValue]]]
    _loads_only_used_references: bool
    _local_source_cache: tuple[Source, ...] | None
    _remote_response_observer: ResponseObserver | None
    _source_context: ParserSourceContext

    @property
    def iter_source(self) -> Iterator[Source]:
        """Iterate over all source files to be parsed."""

    @property
    def schema_features(self) -> JsonSchemaFeatures:
        """The features of the JSON Schema version being parsed."""

    @property
    def target_python_version(self) -> PythonVersion:
        """The Python version the output targets."""

    @property
    def use_single_value_enum_as_const(self) -> bool:
        """Whether a single-value enum is read as a const (``--use-single-value-enum-as-const``)."""

    @property
    def read_kubernetes_extensions(self) -> bool:
        """Whether the Kubernetes schema extensions are read (``--read-kubernetes-extensions``)."""

    @classmethod
    def get_url_path_parts(cls, url: ParseResult) -> list[str]:
        """Split URL into scheme/host and path components."""

    def _index_document(self, raw: dict[str, Any], path_parts: list[str]) -> dict[str, Any]:
        """Return a loaded document as a tree whose JSON objects the schema graph locates (L0)."""

    def _iter_local_source_paths(self) -> Iterator[Path]:
        """Yield the paths of the local input files."""

    def _iter_source_uncached(self) -> Iterator[Source]:
        """Iterate over the input sources without the cache of local sources."""

    def _load_ref_data_from_path(self, path: Path) -> dict[str, YamlValue]:
        """Load one referenced path and contextualize only its decode failures."""

    def _parse_dynamic_specializations(self) -> None:
        """Parse pending specialized models iteratively, outside of any specialization being parsed."""

    def _parse_file(  # noqa: PLR0913
        self,
        raw: dict[str, Any],
        obj_name: str,
        path_parts: list[str],
        object_paths: list[str] | None = None,
        reference_paths: list[str] | None = None,
        *,
        preserve_root_class_name: bool = False,
        ref: str | None = None,
    ) -> None:
        """Parse a file containing JSON Schema definitions and references."""

    def _parsing_session(self) -> AbstractContextManager[None]:
        """Parse in the block, then the targets the sources reserve, end the schema parsing and clear the caches."""

    def _ref_reading(self, root: Literal["root", "object"]) -> RefReading:
        """Return how ``SchemaGraphBuilder.with_ref_edges`` reads a document (``root``) or a schema (``object``)."""

    def _resolve_ref_callback(self, obj: JsonSchemaObject, path: list[str]) -> None:
        """Resolve $ref in schema object, and the targets of $dynamicRef and $recursiveRef as their data types do."""

    def _ignores_ref_siblings_at(self, location: SchemaLocation | None) -> bool:
        """Return whether a ``$ref`` at a location is read alone (--ignore-ref-siblings-by-schema-version)."""

    def _read_dialect(self, raw: dict[str, Any], *, inherited: DocumentDialect | None = None) -> DocumentDialect:
        """Return the dialect a document of the parser's kind is read by (``parser/jsonschema.py``)."""

    @property
    def _reads_schema_version_per_document(self) -> bool:
        """Whether --read-schema-version-per-document reads each schema in the version of its own document."""

    def _schema_features_for(self, root: dict[str, YamlValue]) -> JsonSchemaFeatures:
        """Get the schema features of a root document based on config or its detected version."""

    def _should_preserve_explicit_root_class_name(self, class_name: str) -> bool:
        """Return whether an explicit root class name with a leading underscore keeps its spelling."""

    def _traverse_schema_objects(
        self,
        obj: JsonSchemaObject,
        path: list[str],
        callback: Callable[[JsonSchemaObject, list[str]], None],
    ) -> None:
        """Traverse schema objects recursively and apply callback, within the schema resources they declare."""


def lenient_ref(parser: SchemaResourcesHost, ref: str) -> str:
    """Return a reference of the current document mapped as its lenient resolution maps it, if it has one.

    The model resolver registers anchors only while parsing JSON Schema documents, so plain-name references
    of any document with implicit schema resources, like OpenAPI, resolve through the resource registry. A
    reference with an edge maps so in its own document, as part of its key (``_compute_ref_key``).
    """
    documents = parser._implicit_documents if _is_plain_name_fragment(ref) else parser._lenient_documents  # noqa: SLF001
    if not documents or isinstance(ref, ResolvedRef) or (document := current_document(parser)) not in documents:
        return ref
    return keep_resolved_ref(
        ref,
        resolve_lenient_resource_ref(parser, ref, parser._schema_resource_registry.root_bases[document], document),  # noqa: SLF001
    )


def normalize_external_ref(parser: SchemaDocumentsHost, ref: str) -> str:
    """Resolve an external anchor before falling back to legacy shorthand pointers.

    Only the fragment is rewritten: the document part stays as written, relative to the document parsed now, since the
    reference is resolved again from there. A name several schemas of the document declare names the first of them under
    --read-dynamic-anchors-in-document-order, as a local reference to it does (``duplicate_name_location``).
    """
    if get_ref_type(ref := lenient_ref(parser, ref)) == JSONReference.LOCAL:
        return ref

    resolved_ref = parser.model_resolver.resolve_ref(ref)
    if get_ref_type(resolved_ref) == JSONReference.LOCAL:
        return ref

    relative_path, separator, object_path = resolved_ref.partition("#")
    if not separator or not object_path or object_path.startswith("/"):
        return ref

    ref_body = parser._get_ref_body(relative_path)  # noqa: SLF001
    document = schema_resource_document(parser, [relative_path])
    file_part = ref.partition("#")[0]
    if location := duplicate_name_location(
        parser,
        parser._schema_resource_registry.plain_name_uri(document, "", f"#{object_path}"),  # noqa: SLF001
    ):
        return keep_resolved_ref(ref, f"{file_part}#{location.partition('#')[2]}")
    if (anchor_pointer := _find_json_schema_anchor_pointer(ref_body, object_path)) is not None:
        return keep_resolved_ref(ref, f"{file_part}#{'' if anchor_pointer == '/' else anchor_pointer}")
    return keep_resolved_ref(ref, f"{file_part}#/{object_path}")


def read_document(
    parser: SchemaDocumentsHost, document: dict[str, YamlValue], root: dict[str, YamlValue] | None = None
) -> dict[str, YamlValue]:
    """Return a document as read, each schema object read under the options that read documents (one walk).

    Each schema object, after its children, is read in this order (``_read_schema_objects``): under
    --read-kubernetes-extensions its Kubernetes extensions as the keywords they stand for
    (``_read_kubernetes_extensions``), which reads the format int-or-string before anything leaves it out; under
    --read-format-as-annotation its format as an annotation (``_read_format_as_annotation``); under
    --use-single-value-enum-as-const its single-value enum as const (``_single_value_enum_as_const``). Each reading
    takes the object as the readings before it left it. Where const is no keyword nothing reads a const: under
    --read-schema-version-per-document in the version of the document (an input document, without ``root``, read by
    itself, another one by its own version or that of the document that loads it); without the option (row
    ``legacy-first-read-dialect``) under the draft-04 schema features the parser already uses, or else those of its
    root document. Documents converted from other input types are not read here, so their formats stay. The document
    as written is kept beside a rewritten one for the targets that serve it. Under --untrusted-input a document past
    the size limits is refused first (``refuse_oversized_document``), before anything reads it.
    """
    if parser.untrusted_input:
        refuse_oversized_document(document)
    reads_const = (parser.use_single_value_enum_as_const or parser.read_kubernetes_extensions) and _reads_const(
        parser, document, root
    )
    later: tuple[_SchemaReading, ...] = ()
    if parser.read_format_as_annotation:
        later = (partial(_read_format_as_annotation, typed_formats=frozenset(parser.type_mappings)),)
    if parser.use_single_value_enum_as_const and reads_const:
        later = (*later, _single_value_enum_as_const)
    readings = later
    if parser.read_kubernetes_extensions:
        read_property = partial(_read_schema_object, readings=later)
        readings = (
            partial(_read_kubernetes_extensions, const_support=reads_const, read_property=read_property),
            *later,
        )
    if not readings:
        return document
    read = cast("dict[str, YamlValue]", _read_schema_objects(document, parser._named_entry_maps, readings))  # noqa: SLF001
    if read is not document:
        parser._documents_as_written[id(read)] = read, document  # noqa: SLF001
    return read


def _reads_const(
    parser: SchemaDocumentsHost, document: dict[str, YamlValue], root: dict[str, YamlValue] | None
) -> bool:
    """Whether const is a keyword where ``read_document`` reads a document, as its docstring says."""
    if parser._reads_schema_version_per_document:  # noqa: SLF001
        inherited = None if root is None else loading_document_dialect(parser)
        return parser._read_dialect(document, inherited=inherited).features.const_support  # noqa: SLF001
    features = parser.__dict__.get("schema_features") or parser._schema_features_for(root or document)  # noqa: SLF001
    return features.const_support


def document_as_written(parser: SchemaDocumentsHost, document: dict[str, YamlValue]) -> dict[str, YamlValue]:
    """Return a document as written, before the options that read documents read it (``read_document``)."""
    entry = parser._documents_as_written.get(id(document))  # noqa: SLF001
    return entry[1] if entry is not None and entry[0] is document else document


def load_ref_data_from_text(parser: SchemaDocumentsHost, text: str, source: str) -> dict[str, YamlValue]:
    """Decode one referenced text body and contextualize only that operation."""
    try:
        return read_document(parser, load_data(text), parser.raw_obj)
    except (json.JSONDecodeError, TypeError, *get_yaml_parse_errors()) as exc:
        raise InvalidFileFormatError(exc, parser._input_file_type, source=source) from exc  # noqa: SLF001


def load_ref_data_from_local_http_file(parser: SchemaDocumentsHost, path: Path, ref: str) -> dict[str, YamlValue]:
    """Decode a local HTTP mirror while locking the same raw bytes as HTTP."""
    if parser._remote_response_observer is None:  # noqa: SLF001
        return parser._load_ref_data_from_path(path)  # noqa: SLF001
    data = path.read_bytes()
    parser._remote_response_observer(ref, parser.http_headers, parser.http_query_parameters, data)  # noqa: SLF001
    try:
        result = _load_parser_source_data_from_path_bytes(path, data, parser.encoding)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, *get_yaml_parse_errors()) as exc:
        raise InvalidFileFormatError(exc, parser._input_file_type, source=path) from exc  # noqa: SLF001
    if isinstance(result, dict):
        return read_document(parser, result, parser.raw_obj)
    msg = f"Expected dict, got {type(result).__name__}"
    raise InvalidFileFormatError(TypeError(msg), parser._input_file_type, source=path)  # noqa: SLF001


def get_ref_body_from_local_http_path(parser: SchemaDocumentsHost, ref: str) -> dict[str, YamlValue]:
    assert parser.http_local_ref_path is not None
    parsed = urlparse(ref)
    if parsed.scheme not in {"http", "https"}:  # pragma: no cover
        msg = f"Unsupported local HTTP $ref URL: {redact_url(ref)}"
        raise Error(msg)

    parts = [unquote(part) for part in parsed.path.split("/") if part]
    host = parsed.netloc.rpartition("@")[2]
    if not host or any(part in {".", ".."} or "/" in part or "\\" in part for part in parts):
        msg = f"Unsupported local HTTP $ref URL path: {redact_url(ref)}"
        raise Error(msg)

    base_path = parser.http_local_ref_path.resolve()
    relative_path = Path(host, *parts)
    file_paths = [(base_path / relative_path).resolve()]
    if not parts or not Path(parts[-1]).suffix:
        file_paths.append((base_path / relative_path.with_name(f"{relative_path.name}.json")).resolve())

    if any(not file_path.is_relative_to(base_path) for file_path in file_paths):
        msg = f"Unsupported local HTTP $ref URL path: {redact_url(ref)}"
        raise Error(msg)

    for file_path in file_paths:
        if file_path.is_file():
            cache_key = f"{file_path}\x00{ref}" if parser._remote_response_observer is not None else str(file_path)  # noqa: SLF001
            return parser.remote_object_cache.get_or_put(
                # Several remote identities may intentionally share one
                # checked-in mirror. Include the logical request only when
                # each identity must reach the lock observer.
                cache_key,
                default_factory=lambda _, file_path=file_path: load_ref_data_from_local_http_file(
                    parser, file_path, ref
                ),
            )

    msg = f"$ref local file not found for {redact_url(ref)}: tried {', '.join(str(path) for path in file_paths)}"
    raise Error(msg)


def schema_resource_document(parser: DocumentNamesHost, path_parts: list[str]) -> str:
    """Identify one physical schema document without changing its public reference names."""
    document = parser.model_resolver.join_path(tuple(path_parts)).split("#", 1)[0]
    if (location := parser._schema_resource_document_aliases.get(document)) is None:  # noqa: SLF001
        location = document if not document or is_url(document) else (parser.base_path / document).resolve().as_posix()
        parser._schema_resource_document_aliases[document] = location  # noqa: SLF001
    return location


def current_document(parser: DocumentNamesHost) -> str:
    """Identify the physical schema document of the root being parsed (``schema_resource_document``)."""
    return schema_resource_document(parser, list(parser.model_resolver.current_root))


def located_document(parser: DocumentNamesHost, location: SchemaLocation) -> DocumentId:
    """Identify the physical schema document that a location of a loaded document is in."""
    return DocumentId(schema_resource_document(parser, [location.document]))


def iter_schema_resource_children(
    parser: SchemaResourcesHost, schema: dict[str, Any]
) -> Iterator[tuple[tuple[str | int, ...], dict[str, Any]]]:
    """Visit schema-valued keywords without inspecting examples, defaults or extensions."""
    for keyword, value in schema.items():
        if keyword in DEFINITION_KEYS and isinstance(value, dict):
            for _name, child, path in iter_schema_definition_entries(parser, value, [keyword]):
                if isinstance(child, dict):
                    yield tuple(path), child
            continue
        yield from subschema_entries(keyword, value)


def schema_resource_identifier(
    parser: SchemaDocumentsHost, schema: dict[str, Any], id_field: str | None, *, is_root: bool, document: str
) -> tuple[str | None, object]:
    """Inherit draft identifiers while preserving fallbacks for roots and unknown dialects.

    A schema below no `$schema` reads the identifier keyword of the version its document is read by under
    --read-schema-version-per-document, else that of ``schema_features`` (row ``legacy-first-read-dialect``). Under
    --ignore-ref-siblings-by-schema-version a schema with a `$ref` declares none where the version of its document
    ignores the keywords beside the `$ref`, as its schema object is the `$ref` alone (``reads_ref_alone``).
    """
    if "$schema" in schema and getattr(parser.config, "jsonschema_version", None) in {None, JsonSchemaVersion.Auto}:
        version = _detect_declared_jsonschema_version(schema)
        id_field = JsonSchemaFeatures.from_version(version).id_field if version is not None else None
    if isinstance(schema.get("$ref"), str) and parser._ignores_ref_siblings_at(  # noqa: SLF001
        SchemaLocation(document, ())
    ):
        return id_field, None
    identifier = schema.get(id_field or document_id_field(parser, document))
    if not identifier and (is_root or id_field is None):
        identifier = schema.get("$id") or schema.get("id")
    return id_field, identifier


def document_id_field(parser: SchemaDocumentsHost, document: str) -> str:
    """Return the identifier keyword a loaded document reads below no `$schema`.

    It is that of the version the document is read by under --read-schema-version-per-document, else that of
    ``schema_features`` (row ``legacy-first-read-dialect``).
    """
    if parser._reads_schema_version_per_document:  # noqa: SLF001
        return parser._document_dialects[document].features.id_field  # noqa: SLF001
    return parser.schema_features.id_field


def plain_name_ref(parser: SchemaResourcesHost, ref: str, pointer: str) -> str:
    """Return the reference a plain-name fragment of the document parsed now names, written at ``pointer`` of it.

    The name names the schema the registry records for it against the base URI of the schema resource that encloses
    ``pointer``: an `$anchor` or a `$dynamicAnchor` at any position of the resource. A name the model resolver has an
    `$id` or `$anchor` for in the document keeps that answer, row ``legacy-anchor-parse-index``: the walk of the
    definitions registers the ones at the positions it visits, the last one where several schemas declare the name,
    unless --read-dynamic-anchors-in-document-order names the first (``duplicate_name_location``). A name nothing
    declares stays as written.
    """
    document = current_document(parser)
    registry = parser._schema_resource_registry  # noqa: SLF001
    uri = registry.plain_name_uri(document, pointer, ref)
    if (location := duplicate_name_location(parser, uri)) is None and (
        parser.model_resolver.registered_id(ref) is not None or (location := registry.locations.get(uri)) is None
    ):
        return ref
    return keep_resolved_ref(ref, _document_location(location, document))


def note_plain_name(parser: SchemaResourcesHost, document: str, pointer: str, ref: str) -> str | None:
    """Note a plain-name fragment written at ``pointer`` of a registered document (``duplicate_name_location``).

    Return the first declaration of a name several schemas declare, under --read-dynamic-anchors-in-document-order.
    """
    if not _is_plain_name_fragment(ref):
        return None
    return duplicate_name_location(parser, parser._schema_resource_registry.plain_name_uri(document, pointer, ref))  # noqa: SLF001


def duplicate_name_location(parser: SchemaResourcesHost, uri: str) -> str | None:
    """Return the first declaration of a plain name several schemas of a document declare, under the option.

    The document is the one that declares the name, where the registry locates its URI, whichever document the
    reference is in. JSON Schema leaves a reference to such a name undefined (2020-12 Core 8.2.2 and 9.1.2): it warns
    once the schemas are parsed. Under --read-dynamic-anchors-in-document-order it names the first declaration in
    document order; without it, and for a name only one schema declares, the reader resolves it as before (None).
    """
    registry = parser._schema_resource_registry  # noqa: SLF001
    if (declared := registry.locations.get(uri)) is None or (
        locations := registry.duplicate_names.get(document := declared.partition("#")[0], {}).get(uri)
    ) is None:
        return None
    name = uri.partition("#")[2]
    source = document_source(parser, document)
    declarations = ", ".join(stable_repr(redact_url(_document_location(location, document))) for location in locations)
    first = parser.config.read_dynamic_anchors_in_document_order
    parser._resource_ref_warnings.add(  # noqa: SLF001
        f"The anchor {stable_repr(name)} is declared by more than one schema in {source} ({declarations}), so a "
        f"reference to {stable_repr(f'#{name}')} names {'the first' if first else 'one'} of them, which JSON Schema "
        "leaves undefined. Give each schema an anchor of its own."
    )
    return locations[0] if first else None


def document_source(parser: SchemaResourcesHost, document: str) -> str:
    """Return how diagnostics name a registered document: its redacted path, else the input's diagnostics path."""
    return redact_url("/".join(parser._schema_resource_path_parts[document]) or parser._source_path_for_diagnostics())  # noqa: SLF001


def declared_location(parser: SchemaResourcesHost, absolute: str) -> str | None:
    """Return the location of the schema an absolute reference names in a registered schema resource, if any.

    A plain name several schemas of a document declare names the first under --read-dynamic-anchors-in-document-order
    (``duplicate_name_location``).
    """
    return duplicate_name_location(parser, absolute) or parser._schema_resource_registry.location(absolute)  # noqa: SLF001


def resolve_schema_resource_ref(
    parser: SchemaResourcesHost, reference: str, base: str, document: str, *, nested_scope: bool
) -> str:
    """Resolve registered resources before considering a physical document fetch."""
    if document in parser._lenient_documents:  # noqa: SLF001
        return resolve_lenient_resource_ref(parser, reference, base, document)
    absolute = urljoin(base, reference)
    resource, _, fragment = absolute.partition("#")
    if (location := declared_location(parser, absolute)) is not None:
        return _document_location(location, document)
    if (
        not is_pointer_fragment(fragment)
        and (root := parser._schema_resource_registry.locations.get(resource)) is not None  # noqa: SLF001
        and root.split("#", 1)[1]
    ):
        msg = f"Embedded schema resource has no anchor {stable_repr(fragment)}: {stable_repr(redact_url(reference))}"
        raise Error(msg)
    if nested_scope and resource.startswith("file://") and not reference.startswith("file://"):
        absolute = file_uri_path(resource).as_posix() + absolute[len(resource) :]
    return absolute if nested_scope else reference


def resolve_lenient_resource_ref(parser: SchemaResourcesHost, reference: str, base: str, document: str) -> str:
    """Resolve a reference of a document that resolves references against itself, like OpenAPI.

    The reference keeps resolving against the document whenever that finds a target, and names the
    schema resource an $id of the document declares only otherwise. A reference that JSON Schema
    resolves elsewhere warns, so the schema can be fixed to mean the same under both.
    """
    if _is_plain_name_fragment(reference):
        location = declared_location(parser, urljoin(base, reference))
        return reference if location is None else _document_location(location, document)
    if (location := parser._schema_resource_registry.location(urljoin(base, reference))) is None:  # noqa: SLF001
        return reference
    if (target := _document_location(location, document)) == reference or not target.startswith("#"):
        return reference
    source = document_source(parser, document)
    if reference.startswith("#"):
        if not lenient_pointer_exists(parser, document, reference):
            return target if lenient_pointer_exists(parser, document, target) else reference
        message = (
            f"$ref {stable_repr(redact_url(reference))} in {source} is resolved against the document for "
            "compatibility, but JSON Schema resolves it against the enclosing $id, to "
            f"{stable_repr(redact_url(target))}."
        )
    else:
        if not loads_document(parser, urljoin(parser._schema_resource_registry.root_bases[document], reference)):  # noqa: SLF001
            return target
        message = (
            f"$ref {stable_repr(redact_url(reference))} in {source} loads the referenced document for compatibility, "
            f"but JSON Schema resolves it to the schema with that $id at {stable_repr(redact_url(target))}."
        )
    parser._resource_ref_warnings.add(f"{message} Update the reference or the $id so that both name one schema.")  # noqa: SLF001
    return reference


def lenient_pointer_exists(parser: SchemaResourcesHost, document: str, reference: str) -> bool:
    """Return whether a local JSON pointer reference names a location in a lenient document."""
    fragment = reference.partition("#")[2]
    value = _fragment_value(
        parser._lenient_documents[document],  # noqa: SLF001
        fragment,
        or_missing=True,
        names=parser._pointer_slash_names,  # noqa: SLF001
    )
    return value is not _MISSING_JSON_POINTER


def loads_document(parser: SchemaResourcesHost, absolute: str) -> bool:
    """Return whether loading an absolute reference as a document would find one."""
    if not absolute.startswith("file://"):
        return is_url(absolute) and parser.allow_remote_refs is not False
    return file_uri_path(absolute.partition("#")[0]).is_file()


def rewrite_schema_resource_refs(  # noqa: PLR0913, PLR0917
    parser: SchemaDocumentsHost,
    schema: dict[str, Any],
    document: str,
    base: str,
    root_base: str,
    id_field: str | None,
    *,
    is_root: bool = False,
) -> dict[str, Any]:
    """Copy only schema containers whose resource-relative references change.

    A reference is resolved with its pointer opened by ``/`` (``pointer_ref_spelling``), which the walk of the
    schema graph writes in place of an opening ``%2F`` either way.
    """
    id_field, identifier = schema_resource_identifier(parser, schema, id_field, is_root=is_root, document=document)
    if isinstance(identifier, str):
        base = urljoin(base, identifier).split("#", 1)[0]
    result = schema
    if isinstance(reference := schema.get("$ref"), str):
        reference = pointer_ref_spelling(reference)
        resolved = resolve_schema_resource_ref(parser, reference, base, document, nested_scope=base != root_base)
        if resolved != reference:
            result = {**schema, "$ref": resolved}
    children = iter_schema_resource_children(parser, schema)
    for path, child in chain(children, implicit_schema_resources(parser, schema, document)) if is_root else children:
        rewritten = rewrite_schema_resource_refs(parser, child, document, base, root_base, id_field)
        if rewritten is child:
            continue
        if result is schema:
            result = dict(schema)
        parent: Any = result
        original: Any = schema
        for token in path[:-1]:
            part = member_key(original, str(token))
            if parent[part] is original[part]:
                parent[part] = copy(original[part])
            parent, original = parent[part], original[part]
        parent[member_key(original, str(path[-1]))] = rewritten
    return result


def implicit_schema_resources(
    parser: SchemaDocumentsHost, raw: dict[str, Any], document: str
) -> Iterator[tuple[tuple[str | int, ...], dict[str, Any]]]:
    """Yield the paths and schemas that a document evaluates as schema resources without an $id.

    Formats such as OpenAPI evaluate each component schema independently, and each schema of the object a reference
    names the document's root as (``_schema_refs.hold_document_root``), such as the schema of a Parameter Object.
    """
    if parser._component_schemas_are_resources:  # noqa: SLF001
        match raw.get("components"):
            case {"schemas": dict() as schemas}:
                for name, schema in schemas.items():
                    if isinstance(schema, dict):
                        yield ("components", "schemas", name), schema
    roles = parser._document_roles.get(document, ())  # noqa: SLF001
    yield from {path: schema for role in roles for path, schema in held_schemas(raw, role)}.items()


def loaded_document_dialect(parser: SchemaDocumentsHost, raw: dict[str, Any], document: str) -> DocumentDialect:
    """Return the schema version a loaded document is read by (L0).

    An input document is read by itself. Any other document that declares no version keeps the version an earlier
    load gave it, else takes that of the document whose reference loads it (``loading_document_dialect``).
    """
    inherited = None
    if raw is not parser.raw_obj and document not in parser.model_resolver.after_load_files:
        inherited = parser._document_dialects.get(document) or loading_document_dialect(parser)  # noqa: SLF001
    return parser._read_dialect(raw, inherited=inherited)  # noqa: SLF001


def record_document_dialect(parser: SchemaDocumentsHost, raw: dict[str, Any], document: str) -> None:
    """Record the schema version a loaded document is read by (``loaded_document_dialect``, L0).

    A document it loads that declares no version takes it from there (``loading_document_dialect``).
    """
    parser._document_dialects[document] = loaded_document_dialect(parser, raw, document)  # noqa: SLF001


def loading_document_dialect(parser: SchemaDocumentsHost) -> DocumentDialect | None:
    """Return the version of the document whose reference loads a document, which it takes by default.

    It is the version of the document being parsed; in the resolution context of a document no load has recorded
    yet, which a reference entered to load it, that of the document the reference is in.
    """
    current = current_document(parser)
    return parser._document_dialects.get(current) or parser._referring_dialect  # noqa: SLF001


@contextmanager
def referenced_document_context(
    parser: SchemaDocumentsHost, context: AbstractContextManager[None]
) -> Generator[None, None, None]:
    """Enter ``context``, the resolution context of a document a reference names, keeping the referring version.

    The version of the document the reference is in is read before the switch, so a document the context loads
    takes it when it declares none (``loading_document_dialect``).
    """
    referring = loading_document_dialect(parser) if parser._reads_schema_version_per_document else None  # noqa: SLF001
    setter = partial(setattr, parser, "_referring_dialect")
    with context, context_variable(setter, parser._referring_dialect, referring):  # noqa: SLF001
        yield


def prepare_schema_resources(parser: SchemaDocumentsHost, raw: dict[str, Any], path_parts: list[str]) -> dict[str, Any]:
    """Add a loaded document to the schema graph once, instead of rescanning it on each reference (L0).

    The schema version the document is read by is recorded (``loaded_document_dialect``); the graph walk finds the
    document's resources and fills the registry dicts from them; then `$ref`s that resolve through an embedded `$id`
    are rewritten copy-on-write, and the prepared document is indexed.
    """
    document = schema_resource_document(parser, path_parts)
    parser._schema_resource_path_parts.setdefault(document, path_parts)  # noqa: SLF001
    if (cached := parser._schema_resource_cache.get(document)) is not None and (raw is cached[0] or raw is cached[1]):  # noqa: SLF001
        return cached[1]
    record_document_dialect(parser, raw, document)
    registry = parser._schema_resource_registry  # noqa: SLF001
    registry.forget(document)
    parser._lenient_documents.pop(document, None)  # noqa: SLF001
    parser._implicit_documents.discard(document)  # noqa: SLF001
    base = document if is_url(document) else file_uri(Path(document)) if document else f"{file_uri(parser.base_path)}/"
    id_field = (
        parser.schema_features.id_field
        if getattr(parser.config, "jsonschema_version", None) not in {None, JsonSchemaVersion.Auto}
        else None
    )
    resources = walk_schema_resources(
        raw,
        tuple(implicit_schema_resources(parser, raw, document)),
        document=DocumentId(document),
        base=base,
        id_field=id_field,
        children=partial(iter_schema_resource_children, parser),
        identifier=partial(schema_resource_identifier, parser, document=document),
    )
    registry.register(resources)
    if resources.implicit_resources:
        parser._implicit_documents.add(document)  # noqa: SLF001
    if resources.implicit_resources and resources.embeds_resources:
        parser._lenient_documents[document] = raw  # noqa: SLF001
    prepared = raw
    if resources.embeds_resources:
        parser._has_embedded_schema_resources = True  # noqa: SLF001
        prepared = rewrite_schema_resource_refs(
            parser, raw, document, base, resources.root_base, id_field, is_root=True
        )
    prepared = parser._index_document(prepared, path_parts)  # noqa: SLF001
    parser._schema_resource_cache[document] = raw, prepared  # noqa: SLF001
    return prepared


def index_document(parser: SchemaDocumentsHost, raw: dict[str, Any], path_parts: list[str]) -> dict[str, Any]:
    """Return a loaded document as a tree whose JSON objects the schema graph locates (L0).

    The document is named as reference paths name it. A copy of the root keeps the document as written that
    ``_document_as_written`` returns for the loaded one. A mounted converted schema keeps its locations.
    """
    if (graph := parser._schema_graph) is None or graph.is_mounted(raw):  # noqa: SLF001
        return raw
    indexed = graph.index_document(
        raw,
        document_root(parser, path_parts),
        DocumentId(schema_resource_document(parser, path_parts)),
        parser._ref_reading("root"),  # noqa: SLF001
    )
    if (entry := parser._documents_as_written.get(id(raw))) is not None and entry[0] is raw:  # noqa: SLF001
        parser._documents_as_written[id(indexed)] = indexed, entry[1]  # noqa: SLF001
    return indexed


def document_root(parser: SchemaDocumentsHost, path_parts: Sequence[str]) -> SchemaLocation:
    """Return the location of the root of the document read at ``path_parts``, named as reference paths name it.

    The document whose schemas are being parsed is read at ``model_resolver.current_root``.
    """
    return SchemaLocation(parser.model_resolver.join_path(tuple(path_parts)).partition("#")[0], ())


def note_converted_document(parser: SchemaDocumentsHost, path_parts: list[str]) -> None:
    """Record that the document read at ``path_parts`` is a schema converted from another format (L0)."""
    cast("SchemaGraphBuilder", parser._schema_graph).note_converted(document_root(parser, path_parts))  # noqa: SLF001


def mount_converted_schema(
    parser: SchemaDocumentsHost,
    converted: dict[str, Any],
    location: SchemaLocation | None,
    container: str = "",
    home: str | None = None,
) -> dict[str, Any]:
    """Locate a JSON Schema converted from a value of a document at the location of that value (L0).

    Its references are local: below ``container``, the pointer the parser keeps the schema at in the document, or
    to its own root. Their edges name the locations their targets are mounted at, and resolve in ``home``, the
    document the parser keeps the schema in when that is not the document of the value.
    """
    if location is None or (graph := parser._schema_graph) is None:  # noqa: SLF001
        return converted  # pragma: no cover - the values a parser converts are in documents it has indexed

    def mounted_ref(ref: str) -> str:
        return f"#{location.pointer}{ref.partition('#')[2].removeprefix(container)}"

    document = located_document(parser, location)
    graph.note_converted(location)
    return graph.mount(converted, location, document, parser._ref_reading("object"), ConvertedRefs(mounted_ref, home))  # noqa: SLF001


def add_id_callback(parser: SchemaDocumentsHost, obj: JsonSchemaObject, path: list[str]) -> None:
    """Add $id and $anchor to model resolver."""
    if obj.id:
        parser.model_resolver.add_id(obj.id, path)
    if obj.anchor:
        parser.model_resolver.add_id(f"#{obj.anchor}", path)


def parse_ref(parser: SchemaDocumentsHost, obj: JsonSchemaObject, path: list[str]) -> None:
    """Recursively parse all $ref references in a schema object, unless only the used ones are loaded."""
    if parser._loads_only_used_references:  # noqa: SLF001
        return
    parser._traverse_schema_objects(obj, path, parser._resolve_ref_callback)  # noqa: SLF001
    parser._parse_dynamic_specializations()  # noqa: SLF001


def parse_id(parser: SchemaDocumentsHost, obj: JsonSchemaObject, path: list[str]) -> None:
    """Recursively parse all $id fields in a schema object."""
    parser._traverse_schema_objects(obj, path, partial(add_id_callback, parser))  # noqa: SLF001


def get_context_source_path_parts(parser: SchemaDocumentsHost) -> Iterator[tuple[Source, list[str]]]:
    """Get source and path parts for each input file with context managers."""
    if isinstance(parser.source, list) or (isinstance(parser.source, Path) and parser.source.is_dir()):
        parser.current_source_path = Path()
        parser._cache_local_sources = parser._cache_local_sources_during_parse  # noqa: SLF001
        parser.model_resolver.after_load_files = {
            path.resolve().as_posix()
            for path in parser._iter_local_source_paths()  # noqa: SLF001
        }

    for source in parser.iter_source:
        if isinstance(parser.source, ParseResult):
            path_parts = type(parser).get_url_path_parts(parser.source)
        else:
            path_parts = list(source.path.parts)
        if parser.current_source_path is not None:
            parser.current_source_path = source.path
        with (
            parser.model_resolver.current_base_path_context(source.path.parent),
            parser.model_resolver.current_root_context(path_parts),
        ):
            yield source, path_parts


def is_input_document_root(parser: SchemaDocumentsHost, key: str) -> bool:
    """Return whether the key ``key`` names the root of an input document, one read from a source, not a ``$ref``.

    The input documents are known from the sources before any model is parsed (``get_context_source_path_parts``): the
    local files of a directory or list input (``after_load_files``), else the one source, named as its path parts.
    """
    document, _, pointer = key.partition("#")
    if pointer:
        return False
    location = schema_resource_document(parser, [document])
    if parser.model_resolver.after_load_files:
        return location in parser.model_resolver.after_load_files
    path_parts: list[str] = []
    if isinstance(parser.source, ParseResult):
        path_parts = type(parser).get_url_path_parts(parser.source)
    elif isinstance(parser.source, Path):
        path_parts = list(parser.source.relative_to(parser.base_path).parts)
    return location == schema_resource_document(parser, path_parts)


def iter_local_source_paths(parser: SchemaDocumentsHost) -> Iterator[Path]:
    if parser._cache_local_sources and parser._source_context.directory_input_filter is not None:  # noqa: SLF001
        parser._local_source_cache = tuple(parser._iter_source_uncached())  # noqa: SLF001
        yield from (parser.base_path / source.path for source in parser._local_source_cache)  # noqa: SLF001
        return
    match parser.source:
        case Path() as path if path.is_dir():
            yield from files_under(path, order="name")
        case list() as paths:
            yield from ((parser.base_path / path) for path in paths)


def load_source_dict(parser: SchemaDocumentsHost, source: Source) -> dict[str, YamlValue]:
    """Load a source into a schema dictionary."""
    if source.raw_data is None:
        try:
            return read_document(parser, load_data(source.text))
        except (json.JSONDecodeError, *get_yaml_parse_errors()) as exc:
            source_path = parser._source_path_for_diagnostics(source.path)  # noqa: SLF001
            raise InvalidFileFormatError(exc, parser._input_file_type, source=source_path) from exc  # noqa: SLF001
        except TypeError as exc:
            if not parser._non_dict_source_is_invalid:  # noqa: SLF001
                raise
            source_path = parser._source_path_for_diagnostics(source.path)  # noqa: SLF001
            raise InvalidFileFormatError(exc, parser._input_file_type, source=source_path) from exc  # noqa: SLF001
    if isinstance(source.raw_data, dict):
        return read_document(parser, {**source.raw_data})

    msg = f"Expected dict, got {type(source.raw_data).__name__}"
    error = TypeError(msg)
    if not parser._non_dict_source_is_invalid:  # noqa: SLF001
        raise error
    source_path = parser._source_path_for_diagnostics(source.path)  # noqa: SLF001
    raise InvalidFileFormatError(error, parser._input_file_type, source=source_path) from error  # noqa: SLF001


def cache_source_ref_body(parser: SchemaDocumentsHost, source: Source, raw_obj: dict[str, YamlValue]) -> None:
    """Cache a local source body for later local $ref resolution."""
    if not source.path.parts:
        return
    parser.remote_object_cache[str((parser.base_path / source.path).resolve())] = raw_obj


def resolve_root_model_name(parser: SchemaDocumentsHost, raw_obj: dict[str, Any]) -> tuple[str, bool]:
    title = raw_obj.get("title")
    title_str = str(title) if title is not None else "Model"
    if parser.custom_class_name_generator:
        return title_str, False

    if class_name := parser.class_name:
        if not parser.model_resolver.validate_name(class_name, parser.target_python_version):
            raise InvalidClassNameError(class_name)
        return class_name, parser._should_preserve_explicit_root_class_name(class_name)  # noqa: SLF001

    obj_name = title_str
    if not parser.model_resolver.validate_name(obj_name, parser.target_python_version):
        obj_name = title_to_class_name(obj_name)
    if not parser.model_resolver.validate_name(obj_name, parser.target_python_version):
        raise InvalidClassNameError(obj_name)
    return obj_name, False


def parse_source(parser: SchemaDocumentsHost, source: Source, raw_obj: dict[str, Any], path_parts: list[str]) -> None:
    """Parse the document ``raw_obj`` read from a source."""
    cache_source_ref_body(parser, source, raw_obj)
    parser.raw_obj = raw_obj
    obj_name, preserve_root_class_name = resolve_root_model_name(parser, raw_obj)
    parser._parse_file(raw_obj, obj_name, path_parts, preserve_root_class_name=preserve_root_class_name)  # noqa: SLF001


def parse_converted_sources(parser: SchemaDocumentsHost, make_converter: Callable[[], Any]) -> None:
    with parser._parsing_session():  # noqa: SLF001
        for source, path_parts in get_context_source_path_parts(parser):
            raw_obj = source.raw_data = make_converter().convert(source)
            if parser.untrusted_input:
                refuse_oversized_document(raw_obj)
            note_converted_document(parser, path_parts)
            parse_source(parser, source, raw_obj, path_parts)


def reserved_targets(
    parser: SchemaDocumentsHost, path_parts: list[str], tree: dict[str, YamlValue], loaded: Callable[[str], bool]
) -> Iterator[ReservedTarget]:
    """Yield the targets the source document at ``path_parts`` reserves and ``loaded`` does not, in key order.

    The keys are read when the first target is taken, and each key is tested when its turn comes, after the target
    before it is parsed; the targets parsed meanwhile reserve more for a later round. The node of a target is the
    object its key names in ``tree``, the document's prepared tree.
    """
    document = DocumentId(schema_resource_document(parser, path_parts))
    graph = cast("SchemaGraphBuilder", parser._schema_graph)  # noqa: SLF001
    for key in sorted(parser.reserved_refs.get(tuple(path_parts)) or ()):
        if not loaded(key):
            value = _fragment_value(tree, key.split("#", 1)[-1], or_missing=True, names=parser._pointer_slash_names)  # noqa: SLF001
            yield ReservedTarget(document=document, key=key, node=graph.node(value))


def known_schema_object_raw_keys(parser: SchemaResourcesHost) -> frozenset[str]:
    return type(parser)._schema_object_raw_key_sets(parser.SCHEMA_OBJECT_TYPE)[0]  # noqa: SLF001


def has_schema_affecting_keywords(parser: SchemaResourcesHost, raw: dict[str, Any]) -> bool:
    schema_affecting_keys = type(parser)._schema_object_raw_key_sets(parser.SCHEMA_OBJECT_TYPE)[1]  # noqa: SLF001
    return any(str(key) in schema_affecting_keys for key in raw)


def iter_definition_namespace_entries(
    parser: SchemaResourcesHost,
    raw: dict[str, Any],
    path: list[str],
    *,
    include_direct_children: bool,
) -> Iterator[tuple[str, YamlValue, list[str]]]:
    for schema_key in NAMESPACE_DEFINITION_KEYS:
        if isinstance(definitions := raw.get(schema_key), dict):
            yield from iter_schema_definition_entries(parser, definitions, [*path, schema_key])

    if not include_direct_children:
        return

    known_keys = known_schema_object_raw_keys(parser)
    for key, value in raw.items():
        key_str = str(key)
        if key_str in known_keys or key_str.startswith("x-") or not isinstance(value, (dict, bool)):
            continue
        yield from iter_schema_definition_entry(parser, key_str, value, [*path, key_str])


def iter_schema_definition_entry(
    parser: SchemaResourcesHost,
    name: str,
    raw: YamlValue,
    path: list[str],
) -> Iterator[tuple[str, YamlValue, list[str]]]:
    if isinstance(raw, dict) and not has_schema_affecting_keywords(parser, raw):
        entries = list(
            iter_definition_namespace_entries(
                parser,
                raw,
                path,
                include_direct_children=parser._is_version_definition_namespace_name(name),  # noqa: SLF001
            )
        )
        if entries:
            yield from entries
            return
    yield name, raw, path


def iter_schema_definition_entries(
    parser: SchemaResourcesHost,
    definitions: dict[str, YamlValue],
    base_path: list[str],
) -> Iterator[tuple[str, YamlValue, list[str]]]:
    for key, model in definitions.items():
        name = str(key)
        yield from iter_schema_definition_entry(parser, name, model, [*base_path, name])


def definition_entries(
    parser: SchemaResourcesHost, definitions: dict[str, YamlValue], base_path: list[str]
) -> list[tuple[str, YamlValue, list[str]]]:
    """Return the name, value and path of each entry of a definitions container, then of the walk of its namespaces.

    The entries of the container come first, one per key, then ``iter_schema_definition_entries``, which yields an
    entry that is no namespace again.
    """
    return [
        *((str(key), model, [*base_path, str(key)]) for key, model in definitions.items()),
        *iter_schema_definition_entries(parser, definitions, base_path),
    ]
