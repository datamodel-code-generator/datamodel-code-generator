"""The site of a located schema object of a loaded API document: its keywords as written, read once.

The client and server bindings read the schemas of an API document by location while the walk acquires them
(``_target_binding.py:_Reader``), through ``openapi_scope.py:ApiOpenAPIParser._site_facts``, which builds the site of
a location once from the loaded document, with the references it writes resolved as the parser resolves them. No pass
writes a site, and no reuse, collapse or merge of models moves one.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, final

from datamodel_code_generator._document_reader import MISSING, as_mapping
from datamodel_code_generator._json_schema import (
    MODEL_VALUE_POSITIONS,
    MODELED_SCHEMA_MAPS,
    SCHEMA_SHAPES,
    SEQUENCE_SCHEMA_SHAPES,
    SINGLE_SCHEMA_SHAPES,
)
from datamodel_code_generator._records import record
from datamodel_code_generator.model._decisions.enum_kind import json_type

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator.reference import SchemaLocation

__all__ = ["ANNOTATION_KEYWORDS", "EMPTY_SITE", "Branch", "OthersSaid", "SiteFacts", "site_facts_of"]

Branch: TypeAlias = Literal["reference", "schema", "other"]
"""An allOf branch: a mapping with a `$ref`, any other mapping, or any other value."""
OthersSaid: TypeAlias = Literal["absent", "false", "true", "empty", "schema", "other"]
"""What `additionalProperties` says: nothing, `false`, `true`, `{}`, a non-empty mapping, or any other value."""

ANNOTATION_KEYWORDS: Final = ("title", "description", "deprecated", "examples", "default")
"""The documentation keywords of a schema, and its default."""
_IDENTIFIERS: Final = frozenset({"$id", "$anchor", "$schema"})
_VALUE_SCHEMA_LISTS: Final = tuple(
    keyword for keyword in MODEL_VALUE_POSITIONS if SCHEMA_SHAPES[keyword] in SEQUENCE_SCHEMA_SHAPES
)
_VALUE_SCHEMAS: Final = tuple(
    keyword for keyword in MODEL_VALUE_POSITIONS if SCHEMA_SHAPES[keyword] in SINGLE_SCHEMA_SHAPES
)


@final
@record(eq=False)
class SiteFacts:
    """A located value of a loaded document as the target bindings read it as a schema.

    Its keywords as written, read once, and where its references lead as the parser resolves them; any value but a
    non-empty mapping reads as the empty schema (``EMPTY_SITE``).
    """

    keys: frozenset[str]
    """Every key the mapping writes."""
    ref: SchemaLocation | None
    """Where a string `$ref` leads, None without one or where it leads to no loaded document."""
    dynamic_ref: SchemaLocation | None
    """Where a string `$dynamicRef` leads, likewise."""
    types: frozenset[str] | None
    """The JSON types `type` names, as a string or each item of a list; None for any other value or none."""
    enum: frozenset[str] | None
    """The JSON types of the members of a list `enum` (a value of no literal kind as an object); None without one."""
    const: str | None
    """The JSON type of `const` (a value of no literal kind as an object); None without one."""
    format: str | None
    """`format` when it is a string."""
    all_of: tuple[Branch, ...]
    """Each branch of a list `allOf`."""
    sequences: Mapping[str, int]
    """The length of each list a value position of its schemas holds (allOf, anyOf, oneOf, prefixItems, items)."""
    maps: Mapping[str, tuple[str, ...]]
    """The names of each map of named schemas (properties, patternProperties), none where it holds no mapping."""
    singles: tuple[str, ...]
    """The value positions that hold one schema as a mapping (items, additionalProperties)."""
    others: OthersSaid
    """What `additionalProperties` says of its other properties."""
    annotations: Mapping[str, YamlValue]
    """Each documentation keyword and the default it writes (``ANNOTATION_KEYWORDS``), as written."""

    @property
    def bare(self) -> bool:
        """Return whether it writes nothing but a `$ref` and identification keywords."""
        return self.keys - _IDENTIFIERS == {"$ref"}

    @property
    def json_types(self) -> frozenset[str]:
        """Return the JSON types `type` names, none when it names none."""
        return self.types or frozenset()

    @property
    def properties(self) -> tuple[str, ...]:
        """Return the names of its properties, in order."""
        return self.maps["properties"]

    def children(self, location: SchemaLocation) -> Iterator[SchemaLocation]:
        """Yield where the schemas its values are made of sit: its branches', items', members' and other properties'.

        ``location`` is its own; the targets of its references are not among them.
        """
        for keyword, count in self.sequences.items():
            yield from (location.child(keyword, str(index)) for index in range(count))
        for keyword, names in self.maps.items():
            yield from (location.child(keyword, str(name)) for name in names)
        yield from (location.child(keyword) for keyword in self.singles)


_NO_MAPS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(dict.fromkeys(MODELED_SCHEMA_MAPS, ()))
EMPTY_SITE: Final = SiteFacts(
    frozenset(),
    None,
    None,
    None,
    None,
    None,
    None,
    (),
    MappingProxyType({}),
    _NO_MAPS,
    (),
    "absent",
    MappingProxyType({}),
)
"""The site of any value but a non-empty mapping: a schema that writes nothing."""


def _others(said: object) -> OthersSaid:
    """Return what an `additionalProperties` value, ``MISSING`` without one, says of the other properties."""
    return (
        "absent"
        if said is MISSING
        else ("true" if said else "false")
        if isinstance(said, bool)
        else ("schema" if said else "empty")
        if isinstance(said, dict)
        else "other"
    )


def site_facts_of(
    value: YamlValue, location: SchemaLocation, follow: Callable[[str, str], SchemaLocation | None]
) -> SiteFacts:
    """Read the site of the value at a location; ``follow`` resolves a reference written in a document.

    ``follow(document, ref)`` returns where a reference that ``document`` holds leads, None for no loaded schema.
    """
    if not isinstance(value, dict) or not value:
        return EMPTY_SITE
    types = value.get("type")
    enum = value.get("enum")
    format_ = value.get("format")
    branches = value.get("allOf")
    return SiteFacts(
        frozenset(value),
        follow(location.document, ref) if isinstance(ref := value.get("$ref"), str) else None,
        follow(location.document, ref) if isinstance(ref := value.get("$dynamicRef"), str) else None,
        frozenset({types})
        if isinstance(types, str)
        else frozenset(str(item) for item in types)
        if isinstance(types, list)
        else None,
        frozenset(json_type(item, "object") for item in enum) if isinstance(enum, list) else None,
        json_type(value["const"], "object") if "const" in value else None,
        format_ if isinstance(format_, str) else None,
        tuple(
            ("reference" if "$ref" in branch else "schema") if isinstance(branch, dict) else "other"
            for branch in (branches if isinstance(branches, list) else ())
        ),
        {keyword: len(items) for keyword in _VALUE_SCHEMA_LISTS if isinstance(items := value.get(keyword), list)},
        {keyword: tuple(as_mapping(value.get(keyword))) for keyword in MODELED_SCHEMA_MAPS},
        tuple(keyword for keyword in _VALUE_SCHEMAS if isinstance(value.get(keyword), dict)),
        _others(value.get("additionalProperties", MISSING)),
        {keyword: value[keyword] for keyword in ANNOTATION_KEYWORDS if keyword in value},
    )
