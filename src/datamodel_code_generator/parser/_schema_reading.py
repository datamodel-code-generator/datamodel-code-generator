"""What the concern modules of the JSON Schema parser read alike from schema objects, their dumps and JSON documents.

A module-level helper of ``parser/jsonschema.py`` that the functions of two or more concern modules
(``parser/_<concern>.py``) use lives here, below them all, so that no concern module imports another or the public
module. None of them reads the parser. ``parser/jsonschema.py`` imports them too, and keeps the public names of the
JSON pointer walk (``get_model_by_path``, ``split_json_pointer``, ``unescape_json_pointer_segment``) importable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, Literal, NamedTuple
from urllib.parse import unquote

from typing_extensions import Sentinel

from datamodel_code_generator._document_reader import ArrayIndexOutOfRangeError, member
from datamodel_code_generator._json_pointer import unescape_token
from datamodel_code_generator._json_schema import SCHEMA_MAP_KEYS, SCHEMA_POSITIONS, schema_positions
from datamodel_code_generator._source import YamlValue  # noqa: TC001 - Public annotations support get_type_hints().
from datamodel_code_generator.parser._intersection import intersect_keywords
from datamodel_code_generator.parser._null_facts import dump_null_sources, resolve_null_facts
from datamodel_code_generator.parser._schema_object import JsonSchemaObject, _type_null_sources
from datamodel_code_generator.types import DataType, UnionIntFloat

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping, Sequence

    from datamodel_code_generator.parser._intersection import KeywordIntersection

__all__ = ["get_model_by_path", "split_json_pointer", "unescape_json_pointer_segment"]

_MODELED_SCHEMA_MAPS = schema_positions("modeled", shapes=("map",))
"""The maps of named subschemas ``JsonSchemaObject`` models: ``properties`` and ``patternProperties``."""


def _merges_ref_siblings(obj: JsonSchemaObject) -> bool:
    """Return whether a definition reads the sibling keywords of its `$ref` with the schema it names."""
    return obj.has_ref_with_schema_keywords and not obj.is_ref_with_nullable_only


def _schema_dump(schema: JsonSchemaObject | bool) -> dict[str, Any] | bool:  # ruff: ignore[boolean-type-hint-positional-argument]
    """Return the keywords a schema object sets as a dict, or a boolean schema itself."""
    return schema.model_dump(exclude_unset=True, by_alias=True) if isinstance(schema, JsonSchemaObject) else schema


_JSON_VALUE_TYPES: Final = (
    (bool, "boolean"),
    (int, "integer"),
    (float, "number"),
    (str, "string"),
    (list, "array"),
    (dict, "object"),
    (type(None), "null"),
)


def _get_json_value_type(value: object) -> str:
    """Return a JSON Schema primitive type name for a concrete value."""
    return next((name for kind, name in _JSON_VALUE_TYPES if isinstance(value, kind)), "")


def _constraint_values(obj: JsonSchemaObject, keys: Iterable[str], *, as_numbers: bool = False) -> dict[str, Any]:
    """Return the value of each of ``keys`` that a schema sets, in their order; ``as_numbers`` unwraps a number."""
    return {
        key: value.value if as_numbers and isinstance(value, UnionIntFloat) else value
        for key in keys
        if (value := getattr(obj, key, None)) is not None
    }


def _dump_parts_admit_null(parts: Sequence[Mapping[str, Any]]) -> bool:
    """Return whether the dumped parts of an intersection admit null (site ``all-of-parts``); the last is its own."""
    return bool(
        resolve_null_facts(
            dump_null_sources(parts[-1]),
            "all-of-parts",
            parts=[dump_null_sources(part) for part in parts if "type" in part],
        ).admits_null
    )


def _intersect_dumps(
    parts: Sequence[Mapping[str, Any]],
    row: dict[str, Any],
    *,
    ref_siblings_are_parts: bool = True,
    legacy_rows: Collection[str] = (),
) -> KeywordIntersection:
    """Return ``parser/_intersection.py:intersect_keywords`` of dumped schema objects, null by ``all-of-parts``."""
    return intersect_keywords(
        parts,
        row,
        extras_key=JsonSchemaObject.__extra_key__,
        parts_admit_null=_dump_parts_admit_null,
        ref_siblings_are_parts=ref_siblings_are_parts,
        legacy_rows=legacy_rows,
    )


def _is_object_only_type(type_: str | list[str] | None) -> bool:
    """Return whether a schema type permits only object values."""
    match type_:
        case None | "object" | ["object"]:
            return True
    return False


def _object_map_other_types(obj: JsonSchemaObject) -> list[str] | None:
    """Return the type array of an object map whose types other than object and null join the dict in a union."""
    match obj.type:
        case list() as types if "object" in types and not set(types) <= {"object", "null"}:
            return types
    return None


_ARRAY_ITEMS_CONSTRAINT_FIELDS = frozenset(schema_positions("modeled", "array"))


def _enum_without_positions(obj: JsonSchemaObject, excluded: Collection[int]) -> JsonSchemaObject:
    """Return a copy of an enum schema without the values at the ``excluded`` positions, with their x-enum entries.

    Each entry stays with its value, matched as ``parse_enum`` matches them.
    """
    skip_null = _enum_entries_skip_null(obj)
    entry_positions = (position for position, value in enumerate(obj.enum) if value is not None or not skip_null)
    dropped = {index for index, position in enumerate(entry_positions) if position in excluded}
    return obj.model_copy(
        update={
            "enum": [value for position, value in enumerate(obj.enum) if position not in excluded],
            **{
                field: [entry for index, entry in enumerate(entries) if index not in dropped]
                for field in _ENUM_ENTRY_FIELDS
                if (entries := getattr(obj, field))
            },
        }
    )


_ENUM_ENTRY_FIELDS = ("x_enum_varnames", "x_enum_names", "x_enum_descriptions")


def _enum_entries_skip_null(obj: JsonSchemaObject) -> bool:
    """Return whether the x-enum entries of a schema are matched to its enum values without null.

    The class of a string or nullable enum leaves out the null the enum lists, and its entries skip it too.
    """
    return resolve_null_facts(_type_null_sources(obj), "enum").rule == "enum-null-as-nullable"


_LIGHTWEIGHT_MAX_DEPTH: Final = 3
_LIGHTWEIGHT_MAX_UNION_ELEMENTS: Final = 5


class _AllOfItemGroups(NamedTuple):
    """The allOf members of one schema by the part of its lightweight type each one gives."""

    refs: list[JsonSchemaObject]
    ref_data_types: list[DataType]
    primitives: list[JsonSchemaObject]
    constraint_only: list[JsonSchemaObject]
    objects: list[JsonSchemaObject]


_NUMERIC_TYPE_DOMAINS = {
    "integer": frozenset({"integer"}),
    "number": frozenset({"integer", "number"}),
    "null": frozenset({"null"}),
}


def _numeric_type_domain(schema_type: str | list[str]) -> frozenset[str] | None:
    """Expand number to its numeric subtypes within the numeric/null domain."""
    if isinstance(schema_type, str):
        return _NUMERIC_TYPE_DOMAINS.get(schema_type)
    types = frozenset(schema_type)
    if not types.issubset(_NUMERIC_TYPE_DOMAINS):
        return None
    return types | _NUMERIC_TYPE_DOMAINS["integer"] if "number" in types else types


_INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS = frozenset({"contains", "maxContains", "minContains"})
_RAW_SCHEMA_EXPLICIT_FIELD_EXTRAS_KEY = "_raw_schema_explicit_field_extras"


def _without_contains_keywords(extras: Mapping[str, Any]) -> dict[str, Any]:
    """Return extension keywords without ``contains`` and its bounds (``_INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS``)."""
    return {key: value for key, value in extras.items() if key not in _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS}


def unescape_json_pointer_segment(segment: str) -> str:
    """Decode one URI-fragment JSON Pointer token in RFC evaluation order."""
    return unescape_token(unquote(segment))


_MISSING_JSON_POINTER = Sentinel("_MISSING_JSON_POINTER")


def get_model_by_path(schema: dict[str, YamlValue] | list[YamlValue], keys: list[str] | list[int]) -> YamlValue:
    """Retrieve a model from schema by traversing raw JSON Pointer keys."""
    return _get_model_by_decoded_path(
        schema, [unescape_json_pointer_segment(key) if isinstance(key, str) else key for key in keys]
    )


def _get_model_by_decoded_path(
    schema: dict[str, YamlValue] | list[YamlValue], keys: Sequence[str | int], *, or_missing: bool = False
) -> Any:
    """Retrieve a model after JSON Pointer tokens have already been decoded.

    Each key's member is read by row ``refusing`` (``_document_reader.member``). A key a dict lacks reads as ``{}``
    and an index a list lacks raises; under ``or_missing`` both are the sentinel.
    """
    if not keys:
        if isinstance(schema, dict):
            return schema
        msg = f"Does not support json pointer to array. schema={schema}, key={keys}"  # pragma: no cover
        raise NotImplementedError(msg)  # pragma: no cover
    current: YamlValue = schema
    for index, key in enumerate(keys):
        try:
            value = member(current, str(key), "refusing")
        except KeyError:
            value = _MISSING_JSON_POINTER if or_missing else {}
        except ArrayIndexOutOfRangeError:
            if not or_missing:
                raise
            value = _MISSING_JSON_POINTER
        if value is _MISSING_JSON_POINTER or index == len(keys) - 1:
            return value
        if not isinstance(value, (dict, list)):
            msg = f"Cannot traverse non-container value. schema={current}, key={keys[index:]}"  # pragma: no cover
            raise NotImplementedError(msg)  # pragma: no cover
        current = value
    raise AssertionError  # pragma: no cover


def _fragment_value(document: Any, fragment: str, *, or_missing: bool = False) -> Any:
    """Return the value a JSON pointer fragment names in a loaded document, the document itself for no fragment."""
    return (
        _get_model_by_decoded_path(document, split_json_pointer(document, fragment), or_missing=or_missing)
        if fragment
        else document
    )


def split_json_pointer(schema: dict[str, YamlValue] | list[YamlValue], pointer: str) -> list[str]:
    """Split a JSON pointer into path parts, preserving slash-containing dict keys."""
    return _split_json_pointer(schema, pointer)[0]


def _split_json_pointer(schema: dict[str, YamlValue] | list[YamlValue], pointer: str) -> tuple[list[str], list[str]]:
    """Split a JSON pointer into lookup and reference path parts."""
    raw_parts = pointer.lstrip("/").split("/") if pointer else []
    if "://" not in pointer and "~1" not in pointer:
        return [unescape_json_pointer_segment(part) for part in raw_parts], raw_parts

    parts: list[str] = []
    reference_parts: list[str] = []
    current: YamlValue = schema
    index = 0
    while index < len(raw_parts):
        if isinstance(current, dict):
            direct_key = unescape_json_pointer_segment(raw_parts[index])
            if direct_key in current:
                parts.append(direct_key)
                reference_parts.append(raw_parts[index])
                current = current.get(direct_key, {})
                index += 1
                continue

            matched_key: str | None = None
            matched_end = index
            for end in range(len(raw_parts), index, -1):
                key = unescape_json_pointer_segment("/".join(raw_parts[index:end]))
                if key in current:
                    matched_key = key
                    matched_end = end
                    break
            if matched_key is None:  # pragma: no cover
                matched_key = unescape_json_pointer_segment(raw_parts[index])
                matched_end = index + 1
            parts.append(matched_key)
            reference_parts.append("/".join(raw_parts[index:matched_end]))
            current = current.get(matched_key, {})
            index = matched_end
            continue
        part = unescape_json_pointer_segment(raw_parts[index])
        parts.append(part)
        reference_parts.append(raw_parts[index])
        if isinstance(current, list):  # pragma: no branch
            try:
                current = member(current, part, "refusing")
            except ArrayIndexOutOfRangeError:
                parts.extend(unescape_json_pointer_segment(part) for part in raw_parts[index + 1 :])
                reference_parts.extend(raw_parts[index + 1 :])
                return parts, reference_parts
        index += 1
    return parts, reference_parts


_NON_SCHEMA_KEYWORDS = frozenset({
    "const",
    "default",
    "enum",
    "example",
    "examples",
    "links",
    "security",
    "server",
    "serverVariables",
    "servers",
})
_NAMED_ENTRY_MAP_KEYWORDS = SCHEMA_MAP_KEYS | {
    "callbacks",
    "channels",
    "content",
    "encoding",
    "messages",
    "operations",
    "parameters",
    "responses",
    "webhooks",
}
_DocumentEntries = Literal["object", "names", "maps"]


def _document_child_entries(
    entries: _DocumentEntries, key: object, named_entry_maps: frozenset[str]
) -> _DocumentEntries | None:
    """Return how the value under a key of a document object is read, or None when it holds no schemas."""
    if entries == "names":
        return "object"
    if key in _NON_SCHEMA_KEYWORDS or (isinstance(key, str) and key.startswith("x-") and key != "x-propertyNames"):
        return None
    child: _DocumentEntries = "object"
    match entries, key:
        case "maps", _:
            child = "names"
        case "object", "components":
            child = "maps"
        case "object", str() if key in named_entry_maps:
            child = "names"
    return child


_RefEdgeEntries = Literal["root", "object", "names", "maps"]
_REF_EDGE_NAME_MAPS = frozenset({"bindings", "paths"})
_REF_EDGE_KEYWORDS = (
    frozenset(SCHEMA_POSITIONS)
    | _NAMED_ENTRY_MAP_KEYWORDS
    | _REF_EDGE_NAME_MAPS
    | {
        "additionalOperations",
        "channel",
        "components",
        "delete",
        "get",
        "head",
        "headers",
        "message",
        "messageTraits",
        "operationTraits",
        "options",
        "patch",
        "payload",
        "pathItems",
        "post",
        "publish",
        "put",
        "query",
        "reply",
        "requestBodies",
        "requestBody",
        "schema",
        "schemas",
        "itemSchema",
        "subscribe",
        "trace",
        "traits",
        "x-propertyNames",
    }
)
"""The keywords of a schema, path item, operation or message object under which another such object is read."""


def _ref_edge_child_entries(
    entries: _RefEdgeEntries, key: object, named_entry_maps: frozenset[str]
) -> _DocumentEntries | None:
    """Return how the value under a key of a document object is read for `$ref` edges, or None for data.

    Below the root, only the keywords that hold schemas or the objects around them are read; names of OAuth scopes,
    discriminator mappings and unknown keywords are data, so a `$ref` key there is not a reference. The keys of the
    root are read as written, since a referenced document names its schemas there.
    """
    if entries == "object" and key not in _REF_EDGE_KEYWORDS:
        return None
    read: _DocumentEntries = "object" if entries == "root" else entries
    return _document_child_entries(read, key, named_entry_maps | _REF_EDGE_NAME_MAPS)


def _is_plain_name_fragment(ref: str) -> bool:
    """Return whether a reference is a plain-name fragment, which names an anchor instead of a location."""
    return ref.startswith("#") and ref[1:2] not in {"", "/"}
