"""The allOf value merging of the JSON Schema parser (L1): value parts, their intersection, merged roots and sites.

Each function takes the parser through ``AllOfValuesHost``, which ``JsonSchemaParser`` satisfies structurally. The
methods that tests call or that other parts of the parser call stay on the parser as delegators to these functions
(``parser/jsonschema.py``), and these functions call them through the parser. The methods that a warning names
(``_enforced_value_keywords``, ``_merge_all_of_root_validation_keywords``, ``_satisfiable_all_of_value_parts``) and
``_merge_value_schemas``, which names ``JsonSchemaParser``, stay parser methods.
"""

from __future__ import annotations

import json
from functools import partial, reduce
from itertools import chain
from typing import TYPE_CHECKING, Any, ClassVar, Final, cast

from typing_extensions import Protocol

from datamodel_code_generator import AllOfClassHierarchy, AllOfMergeMode, SchemaParseError
from datamodel_code_generator._constraint_applicability import all_of_root_keyword_kinds
from datamodel_code_generator._json_schema import (
    DEFINITION_KEYS,
    INSTANCE_DATA_KEYWORDS,
    SINGLE_SCHEMA_SHAPES,
    schema_positions,
)
from datamodel_code_generator._model_keys import FIELDLESS_SITE_KEY
from datamodel_code_generator._regex_syntax import is_portable_pattern
from datamodel_code_generator._url_redaction import named_location
from datamodel_code_generator.model._decisions.enum_kind import holds_inherited_member_value, holds_member_value
from datamodel_code_generator.model.enum import Enum, IntEnum, StrEnum
from datamodel_code_generator.parser._intersection import (
    all_of_member_row,
    common_types,
    concatenating_row,
    declared_types,
    intersect_enum_values,
    intersects_all_of_constraints,
)
from datamodel_code_generator.parser._intersection import json_values_equal as _json_literal_values_equal
from datamodel_code_generator.parser._null_facts import dump_null_sources, null_schema
from datamodel_code_generator.parser._schema_object import (
    _MODELED_POSITIONS,
    JsonSchemaObject,
    _is_false_all_of,
    _located,
    _named_path,
)
from datamodel_code_generator.parser._schema_reading import (
    _LIGHTWEIGHT_MAX_DEPTH,
    _LIGHTWEIGHT_MAX_UNION_ELEMENTS,
    _AllOfItemGroups,
    _enum_without_positions,
    _intersect_dumps,
    _is_object_only_type,
    _merges_ref_siblings,
    _numeric_type_domain,
    _schema_dump,
)
from datamodel_code_generator.reference import Reference, _is_special_path

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
    from contextlib import AbstractContextManager

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator.config import JSONSchemaParserConfig
    from datamodel_code_generator.enums import TargetPydanticVersion
    from datamodel_code_generator.model import DataModel, DataModelFieldBase
    from datamodel_code_generator.model.output import OutputModelContext
    from datamodel_code_generator.parser._intersection import KeywordIntersection
    from datamodel_code_generator.parser._null_facts import NullFacts, NullMode, NullSite, NullSources
    from datamodel_code_generator.parser._schema_graph import SchemaGraphBuilder, _TargetNode
    from datamodel_code_generator.parser.schema_version import JsonSchemaFeatures
    from datamodel_code_generator.types import DataType, Types

__all__ = [
    "AllOfValuesHost",
    "all_of_enum_site_schema",
    "all_of_pattern_conjunction",
    "all_of_reference_value_schema",
    "all_of_root_value_schema",
    "all_of_site_schema",
    "all_of_value_parts",
    "all_of_value_row",
    "allof_lightweight_items",
    "allof_requires_model_type",
    "array_all_of_site_schema",
    "collect_all_of_value_parts",
    "handle_allof_root_model_with_constraints",
    "intersect_all_of_value_parts",
    "intersect_typed_value_parts",
    "intersects_value_parts",
    "is_all_of_mapping_item",
    "is_all_of_value_part",
    "is_root_model_schema",
    "layers_on_reference",
    "merge_all_of_object",
    "merge_all_of_root_schema",
    "merge_all_of_root_value_children",
    "merge_all_of_root_value_nodes",
    "merge_all_of_value_schema",
    "merged_enum_fails",
    "output_enforced_value_keywords",
    "patterns_need_holder_engine",
    "raise_unsatisfiable_schema",
    "read_false_all_of_array_positions",
    "read_false_all_of_map_schemas",
    "read_false_all_of_values",
    "ref_member_narrows_literals",
    "reference_site_schema",
    "reference_site_value",
    "reference_value_parts",
    "schema_has_own_value_keywords",
    "schema_requires_model_type",
    "schema_signature",
    "site_schema",
    "without_allof_keywords",
]

_DERIVED_FROM_REF_KEY = "_derived_from_ref"
_ALL_OF_VALUE_PART_EXCLUDED_EXTRAS = frozenset({
    *schema_positions("applicator", without=(*_MODELED_POSITIONS, "dependencies", "extends")),
    "dependentRequired",
    "x-python-type",
})
"""The extras that keep an allOf member from being a value part: ``dependentRequired``, ``x-python-type`` and the
applicators held in extras, except ``dependencies`` and ``extends`` (legacy)."""
_ALL_OF_PARTS: Final = "its allOf members and its own keywords"
"""The parts the unsatisfiable warning names for an allOf site no value satisfies."""
_ALL_OF_MEMBER_ANNOTATIONS: Final = frozenset({"description", "default", "examples", "deprecated"})
"""The annotations an allOf site takes from its members when it does not write them itself."""


def _bare_ref_members(obj: JsonSchemaObject) -> list[str]:
    """Return the references of the ``$ref`` members of an allOf, or none when one of them has sibling keywords.

    A member written with sibling keywords is the site's own inline schema (decision 4), so the site is no reference
    with more assertions.
    """
    members = [item for item in obj.allOf if isinstance(item, JsonSchemaObject) and item.ref]
    if any(member.has_ref_with_schema_keywords for member in members):
        return []
    return [ref for member in members if (ref := member.ref)]


def _derived_site(site: JsonSchemaObject, refs: Sequence[str]) -> JsonSchemaObject:
    """Mark a site schema built from the parts of its one ``$ref`` target without the reference, and return it.

    The model built from it records that target (``JsonSchemaParser._record_derived_from``), so a model-level type
    override of the target applies to it as well.
    """
    if len(refs) == 1 and not site.ref and not site.allOf:
        site.__dict__[_DERIVED_FROM_REF_KEY] = refs[0]
    return site


def _enum_narrowed_by_part_consts(
    intersection: JsonSchemaObject, parts: Sequence[JsonSchemaObject], *, const_support: bool
) -> JsonSchemaObject:
    """Return the intersection of allOf value parts with its enum narrowed to the const of each part without an enum.

    A dialect without ``const`` (draft-04, OpenAPI 3.0) reads it as an unknown keyword, so nothing narrows there.

    Such a const is a part of its own that a value must equal, as the one-value enum --use-single-value-enum-as-const
    reads as that const is, so it narrows the enum the same way and the enum then stands for it. The intersection is
    returned as it is without such a const, and where no enum value equals every such const (no value meets the parts).
    """
    if (
        not const_support
        or not intersection.enum
        or not (consts := [part.extras["const"] for part in parts if _is_enumless_const(part)])
    ):
        return intersection
    excluded = {
        position
        for position, value in enumerate(intersection.enum)
        if not all(_json_literal_values_equal(const, value) for const in consts)
    }
    if not excluded or len(excluded) == len(intersection.enum):
        return intersection
    return _enum_without_positions(intersection, excluded).model_copy(
        update={"extras": {key: value for key, value in intersection.extras.items() if key != "const"}}
    )


def _is_enumless_const(part: JsonSchemaObject) -> bool:
    """Return whether a value part holds a const and no enum."""
    return "const" in part.extras and not part.enum


def _non_null_types(type_: str | list[str] | None) -> str | list[str] | None:
    """Return a ``type`` keyword without null in its list."""
    return [item for item in type_ if item != "null"] if isinstance(type_, list) else type_


def _excludes_objects(type_: str | list[str] | None) -> bool:
    """Return whether a schema type is written and names no object."""
    return type_ is not None and "object" not in declared_types(type_)


_ALL_OF_VALUE_ITEM_KEYWORDS: Final = schema_positions("modeled", "array", shapes=SINGLE_SCHEMA_SHAPES)
"""The item schemas an allOf value part writes, which the intersection of the parts keeps."""
_ALL_OF_VALUE_KEYWORDS: Final = frozenset({
    "type",
    "format",
    "enum",
    "nullable",
    *_ALL_OF_VALUE_ITEM_KEYWORDS,
    *JsonSchemaObject.__constraint_fields__,
})
"""The keywords of an allOf site that its parts' intersection writes, not its own annotations."""
_PATTERN_SCAN_SKIPPED_KEYWORDS: Final = INSTANCE_DATA_KEYWORDS | {"patternProperties", *DEFINITION_KEYS}
"""The keywords whose values hold no subschema a type carries; ``patternProperties`` counts by its keys and values."""


class AllOfValuesHost(Protocol):
    """The options, run state and hooks of the parser that the allOf value merging reads."""

    SCHEMA_OBJECT_TYPE: ClassVar[type[JsonSchemaObject]]
    _all_of_root_value_ref_stack: set[tuple[_TargetNode, ...]] | None

    @property
    def allof_class_hierarchy(self) -> AllOfClassHierarchy:
        """How allOf members become base classes (``--allof-class-hierarchy``)."""

    @property
    def allof_merge_mode(self) -> AllOfMergeMode:
        """How the constraints of allOf members merge (``--allof-merge-mode``)."""

    @property
    def collapse_root_models(self) -> bool:
        """Whether root models are collapsed into their users."""

    @property
    def config(self) -> JSONSchemaParserConfig:
        """The configuration of the parser."""

    @property
    def data_model_field_type(self) -> type[DataModelFieldBase]:
        """The field type of the output."""

    @property
    def data_model_root_type(self) -> type[DataModel]:
        """The root model type of the output."""

    @property
    def field_constraints(self) -> bool:
        """Whether constraints are written on fields (``--field-constraints``)."""

    @property
    def generate_schema_validators(self) -> bool:
        """Whether the models validate the schema rules their fields cannot express."""

    @property
    def ignore_enum_constraints(self) -> bool:
        """Whether an enum leaves its constraints out (``--ignore-enum-constraints``)."""

    @property
    def schema_features(self) -> JsonSchemaFeatures:
        """The features of the JSON Schema version being parsed."""

    @property
    def target_pydantic_version(self) -> TargetPydanticVersion | None:
        """The Pydantic version the output targets, if configured."""

    @property
    def target_python_version(self) -> PythonVersion:
        """The Python version the output targets."""

    @property
    def type_mappings(self) -> dict[tuple[str, str], str]:
        """The ``--type-mappings`` entries."""

    @property
    def _data_formats(self) -> dict[str, dict[str, Types]]:
        """The data format mappings of this parser type."""

    @property
    def _legacy_intersection(self) -> frozenset[str]:
        """The rows of ``parser/_intersection.py:LEGACY_ROWS`` kept: every row without --intersect-allof-keywords."""

    @property
    def _null_mode(self) -> NullMode:
        """The options that change how parse sites read null (``parser/_null_facts.py``)."""

    @property
    def _output_model_context(self) -> OutputModelContext:
        """The capabilities of the output model types."""

    @property
    def _ref_sibling_keywords_enabled(self) -> bool:
        """Whether the sibling keywords of a ``$ref`` apply beside it in the document being parsed."""

    @property
    def _schema_graph(self) -> SchemaGraphBuilder | None:
        """The schema graph of the parse, once it is built."""

    def get_ref_data_type(self, ref: str) -> DataType:
        """Return the data type a reference names."""

    def parse_root_type(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> DataType:
        """Parse a root-level type into a root model."""

    def should_parse_enum_as_literal(
        self,
        obj: JsonSchemaObject,
        property_name: str | None = None,
        property_obj: JsonSchemaObject | None = None,
    ) -> bool:
        """Determine if an enum should be parsed as a literal type (``enum_as_literal``)."""

    def _allof_lightweight_items(self, schema: JsonSchemaObject) -> Sequence[JsonSchemaObject | bool]:
        """Return the members ``_build_allof_type`` builds a type from: own keywords first, then allOf."""

    def _conjoined_pattern(self, patterns: Sequence[str], where: str) -> str:
        """Return the conjoined pattern, warning about the patterns it leaves out."""

    def _drop_incompatible_inherited_constraints(
        self,
        child: dict[str, Any],
        parent_types: frozenset[str],
    ) -> dict[str, Any]:
        """Avoid applying schema constraints to incompatible Python value types."""

    def _enforced_value_keywords(
        self, schema: JsonSchemaObject
    ) -> tuple[frozenset[str], bool, dict[str, Any], dict[str, Any]]:
        """Return what a value schema enforces: its types, null, the constraints its type takes and other keywords."""

    def _get_array_item_schemas(
        self,
        obj: JsonSchemaObject,
        *,
        include_true_tail_schema: bool = False,
        force_prefix_items: bool = False,
    ) -> tuple[list[JsonSchemaObject | bool], bool, bool, int | None]:
        """Return item schemas plus tuple/constraint flags and homogeneous tuple length."""

    def _get_enum_model_class(self, type_: Types | None, enum_values: list[Any]) -> tuple[type[Enum], Types | None]:
        """Return the enum model class and the schema type whose value type it also subclasses, else None."""

    def _get_enum_type(self, obj: JsonSchemaObject) -> Types | None:
        """Return the type an enum class of the schema may specialize or subclass."""

    def _get_fixed_length_homogeneous_tuple_length(self, obj: JsonSchemaObject) -> int | None:
        """Return the length of an opted-in homogeneous fixed-length tuple."""

    def _get_inherited_property_map(
        self,
        base_classes: list[Reference],
    ) -> dict[str, tuple[JsonSchemaObject | bool, str]]:
        """Build the effective raw property map once in inherited C3 order."""

    def _get_type_with_mappings(self, type_: str, format_: str | None = None) -> Types:
        """Get the Types enum for a given type and format, applying custom type mappings."""

    def _group_allof_items(
        self,
        allof_items: Sequence[JsonSchemaObject],
        depth: int,
        visited: frozenset[int],
        max_depth: int,
        max_union_elements: int,
    ) -> _AllOfItemGroups | None:
        """Group allOf members by the part of the lightweight type each one gives, or None when one gives none."""

    def _has_pattern_properties_validator(self, obj: JsonSchemaObject) -> bool:
        """Return whether schema runtime validation owns patternProperties dispatch."""

    def _inherited_override_row(self, parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
        """Return ``parser/_intersection.py:inherited_override_row`` under the merge mode of this parse."""

    def _is_all_of_mapping_item(self, schema: JsonSchemaObject) -> bool:
        """Return whether an allOf item is an object map, which a mapping-shaped allOf merges into one root map."""

    def _is_root_model_schema(self, obj: JsonSchemaObject) -> bool:
        """Check if schema represents a root model (primitive type with constraints)."""

    def _iter_schema_validation_sources(
        self,
        obj: JsonSchemaObject,
        visited_refs: frozenset[_TargetNode] = frozenset(),
        *,
        include_references: bool = True,
    ) -> Iterator[JsonSchemaObject]:
        """Yield the schema and its allOf sources, without the document each was read from."""

    def _key_node(self, resolved_ref: str) -> _TargetNode:
        """Return the schema graph node at a reference key, or the key when the graph does not locate its value."""

    def _load_inherited_schema_object(self, resolved_ref: str) -> JsonSchemaObject:
        """Load and cache a schema used only for inherited field inspection."""

    def _load_ref_schema_object(self, ref: str) -> JsonSchemaObject:
        """Return the schema object a reference names."""

    def _merge_all_of_root_validation_keywords(self, merged: dict[str, Any], sources: list[JsonSchemaObject]) -> None:
        """Preserve literal and item intersections while materializing a value reference."""

    def _merge_primitive_schemas(self, items: list[JsonSchemaObject]) -> JsonSchemaObject:
        """Merge primitive schemas into the intersection of their constraints."""

    def _merge_primitive_schemas_for_allof(self, items: list[JsonSchemaObject]) -> JsonSchemaObject | None:
        """Return the one primitive schema the parts of an allOf merge into, if they merge."""

    def _merge_ref_with_schema(self, obj: JsonSchemaObject, where: str | None = None) -> JsonSchemaObject:
        """Return the schema a ``$ref`` names merged with the sibling keywords of the reference."""

    def _merge_schema_constraints(
        self,
        base_dict: dict[str, Any],
        items: list[JsonSchemaObject],
        *,
        intersect: bool,
    ) -> None:
        """Merge the constraints of ``items`` into ``base_dict``, intersecting them when ``intersect`` is set."""

    def _merge_value_schemas(self, schemas: list[JsonSchemaObject]) -> JsonSchemaObject | None:
        """Merge schemas that constrain one value, unless their types, formats or literals disagree."""

    def _null_facts(
        self,
        obj: JsonSchemaObject,
        site: NullSite,
        *,
        required: bool = False,
        has_default: bool = False,
        parts: Sequence[NullSources] = (),
    ) -> NullFacts:
        """Return what ``site`` reads from where the null of ``obj`` comes from (``parts``: of its typed parts)."""

    def _other_keys_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Return the additionalProperties schema typing the keys no pattern matches (``parser/_object_maps.py``)."""

    def _parse_all_of_root_value(self, name: str, merged_root: JsonSchemaObject, path: list[str]) -> DataType:
        """Keep literal membership alongside the existing scalar or container representation."""

    def _read_false_all_of_values(self, obj: JsonSchemaObject, *, lightweight: bool = False) -> JsonSchemaObject:
        """Return the site ``obj`` with the schema that types its values written ``false`` where it is a false allOf."""

    def _reads_enum_as_const(self, obj: JsonSchemaObject) -> bool:
        """Return whether --use-single-value-enum-as-const reads an enum admitting the const beside it as that const."""

    def _ref_path(self, ref: str) -> str:
        """Return the reference key a reference names."""

    def _satisfiable_all_of_value_parts(self, obj: JsonSchemaObject) -> list[JsonSchemaObject] | None:
        """Return the parts of an allOf site whose members each describe one value, references resolved, else None."""

    def _schema_has_own_value_keywords(self, schema: JsonSchemaObject) -> bool:
        """Return whether a schema has value keywords of its own."""

    def _schema_literal_values(self, obj: JsonSchemaObject) -> tuple[object, ...] | None:
        """Return direct const/enum values after their JSON Schema intersection."""

    def _schema_object(self, raw: object) -> JsonSchemaObject:
        """Validate a raw schema into a schema object."""

    def _schema_requires_model_type(
        self,
        item: JsonSchemaObject,
        *,
        resolve_ref: bool = False,
        visited_refs: frozenset[_TargetNode] | None = None,
    ) -> bool:
        """Return whether a schema describes a model-shaped object."""

    def _temporarily_enable_field_constraints(self) -> AbstractContextManager[None]:
        """Render an internal annotated alias with Field constraints without bypassing parser hooks."""

    def _temporary_constraint_values(self, obj: JsonSchemaObject, data_type: DataType | None) -> dict[str, Any]:
        """Return the constraint values ``obj`` writes beside ``data_type``, a type built only to read them."""

    def _typed_nullable_map_value_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Return the value schema of a nullable map when this run types its values (``parser/_object_maps.py``)."""

    def _unvisited_ref_target(
        self, ref: str, visited_refs: frozenset[_TargetNode]
    ) -> tuple[JsonSchemaObject, frozenset[_TargetNode]] | None:
        """Return the schema ``ref`` names with ``visited_refs`` and its node, or None when its node was visited."""

    def _validate_schema_object(
        self,
        raw: dict[str, YamlValue] | YamlValue,
        path: list[str],
    ) -> JsonSchemaObject:
        """Validate raw data as JsonSchemaObject with path context in errors; a too deep nesting stops the parse."""

    def _warn_intersection(self, where: str, intersection: KeywordIntersection, *parts: str) -> None:
        """Warn about the keywords of an intersection whose parts have no value in common, and its untaken patterns."""

    def _without_allof_keywords(self, schema: JsonSchemaObject) -> JsonSchemaObject:
        """Return a schema without its allOf."""


def schema_requires_model_type(
    parser: AllOfValuesHost,
    item: JsonSchemaObject,
    *,
    resolve_ref: bool = False,
    visited_refs: frozenset[_TargetNode] | None = None,
) -> bool:
    """Return whether a schema describes a model-shaped object."""
    if item.ref:
        if not resolve_ref or (target := parser._unvisited_ref_target(item.ref, visited_refs or frozenset())) is None:  # noqa: SLF001
            return True
        return parser._schema_requires_model_type(target[0], resolve_ref=True, visited_refs=target[1])  # noqa: SLF001
    return bool(
        item.properties is not None
        or item.patternProperties is not None
        or item.propertyNames is not None
        or item.additionalProperties is not None
        or item.unevaluatedProperties is not None
        or item.required
        or item.minProperties is not None
        or item.maxProperties is not None
        or "object" in declared_types(item.type)
    )


def allof_requires_model_type(
    parser: AllOfValuesHost,
    items: Iterable[JsonSchemaObject | bool],
    *,
    resolve_ref: bool = False,
    visited_refs: frozenset[_TargetNode] | None = None,
) -> bool:
    """Return whether allOf members describe a model-shaped schema."""
    return any(
        isinstance(item, JsonSchemaObject)
        and (
            parser._schema_requires_model_type(item, resolve_ref=resolve_ref, visited_refs=visited_refs)  # noqa: SLF001
            or any(
                members and allof_requires_model_type(parser, members, resolve_ref=True, visited_refs=visited_refs)
                for members in (item.allOf, item.anyOf, item.oneOf)
            )
        )
        for item in items
    )


def schema_has_own_value_keywords(parser: AllOfValuesHost, schema: JsonSchemaObject) -> bool:  # noqa: ARG001
    return bool(schema.type or schema.format or schema.enum or schema.has_constraint or "const" in schema.extras)


def without_allof_keywords(parser: AllOfValuesHost, schema: JsonSchemaObject) -> JsonSchemaObject:
    schema_dict = schema.model_dump(exclude_unset=True, by_alias=True)
    schema_dict.pop("allOf", None)
    return parser._schema_object(schema_dict)  # noqa: SLF001


def is_all_of_value_part(parser: AllOfValuesHost, schema: JsonSchemaObject) -> bool:
    """Return whether an allOf member is a schema of one value whose keywords the intersection table reads.

    A ``pattern`` the static scanner cannot certify as portable keeps the earlier form of the site.
    """
    return not (
        schema.ref
        or schema.is_boolean_schema_false
        or schema.anyOf
        or schema.oneOf
        or schema.prefixItems
        or schema.recursiveRef
        or schema.dynamicRef
        or schema.custom_type_path
        or _ALL_OF_VALUE_PART_EXCLUDED_EXTRAS.intersection(schema.extras)
        or parser._schema_requires_model_type(schema)  # noqa: SLF001
        or (schema.pattern is not None and not is_portable_pattern(schema.pattern))
    )


def collect_all_of_value_parts(
    parser: AllOfValuesHost, schema: JsonSchemaObject, parts: list[JsonSchemaObject], *, resolve_refs: bool = False
) -> bool:
    """Append the parts of an allOf schema to ``parts``: its members in document order, then its own keywords.

    A member that is itself an allOf contributes its parts, and with ``resolve_refs`` a ``$ref`` member contributes
    those of the schema it references, merged with its siblings. The own keywords are a part when they write a
    value keyword or an item schema. Return False when a part is no schema of one value.
    """
    for item in schema.allOf:
        if item is True:
            continue
        if not isinstance(item, JsonSchemaObject):
            return False
        member = (
            parser._merge_ref_with_schema(  # noqa: SLF001
                item, named_location(schema.schema_location, "allOf", row="legacy-unredacted-locations")
            )
            if resolve_refs
            else item
        )
        if not is_all_of_value_part(parser, member):
            return False
        if member.allOf:
            if not collect_all_of_value_parts(parser, member, parts, resolve_refs=resolve_refs):
                return False
        else:
            parts.append(member)
    if parser._schema_has_own_value_keywords(schema) or any(  # noqa: SLF001
        getattr(schema, keyword) is not None for keyword in _ALL_OF_VALUE_ITEM_KEYWORDS
    ):
        if not is_all_of_value_part(parser, own := parser._without_allof_keywords(schema)):  # noqa: SLF001
            return False
        parts.append(own)
    return True


def all_of_value_row(parser: AllOfValuesHost, obj: JsonSchemaObject) -> dict[str, Any]:
    """Return the schema the lightweight type of an allOf value schema is built from, as a dump.

    It is the merge ``_build_allof_type`` writes for members that are value schemas (the parts of
    ``_collect_all_of_value_parts``): the first typed member with every member's constraints intersected, and
    nothing when it writes no type.
    """
    items = [item for item in parser._allof_lightweight_items(obj) if isinstance(item, JsonSchemaObject)]  # noqa: SLF001
    if len(items) == 1:
        return items[0].model_dump(exclude_unset=True, by_alias=True)
    if (
        groups := parser._group_allof_items(  # noqa: SLF001
            items, 0, frozenset(), _LIGHTWEIGHT_MAX_DEPTH, _LIGHTWEIGHT_MAX_UNION_ELEMENTS
        )
    ) is None or not groups.primitives:
        return {}
    return parser._merge_primitive_schemas([*groups.primitives, *groups.constraint_only]).model_dump(  # noqa: SLF001
        exclude_unset=True, by_alias=True
    )


def intersect_all_of_value_parts(
    parser: AllOfValuesHost, parts: list[JsonSchemaObject], row: dict[str, Any], where: str | None
) -> JsonSchemaObject:
    """Return the intersection of the parts of an allOf value schema, keeping the merge ``row`` where it is exact.

    A keyword the row drops (another member's ``enum``, ``const`` or item schemas, a nested member's bounds, a type
    list) is taken from the first part that has it before the parts are intersected: ``additionalItems`` only with
    the ``items`` of its own part, which it qualifies, and item schemas only where the intersected ``type`` admits
    arrays, since they constrain nothing else. A keyword whose parts have no value
    in common keeps the row's value, with a warning naming ``where`` unless it is None.
    """
    row.pop("allOf", None)
    dumps = [part.model_dump(exclude_unset=True, by_alias=True) for part in parts]
    extras_key = JsonSchemaObject.__extra_key__
    row_extras = row[extras_key] = dict(row.get(extras_key, {}))
    added = {
        keyword: next(dump[keyword] for dump in dumps if keyword in dump)
        for keyword in (
            "type",
            "enum",
            "nullable",
            "items",
            "unevaluatedItems",
            *JsonSchemaObject.__constraint_field_order__,
        )
        if keyword not in row and any(keyword in dump for dump in dumps)
    }
    if "items" in added and "additionalItems" not in row:
        items_part = next(dump for dump in dumps if "items" in dump)
        if "additionalItems" in items_part:
            added["additionalItems"] = items_part["additionalItems"]
    row.update(added)
    if "const" not in row_extras and (
        const_part := next((dump for dump in dumps if "const" in dump.get(extras_key, {})), None)
    ):
        row_extras["const"] = added["const"] = const_part[extras_key]["const"]
    intersection = _intersect_dumps(dumps, row)
    keywords = intersection.keywords
    if where is not None:
        parser._warn_intersection(where, intersection, _ALL_OF_PARTS)  # noqa: SLF001
    dropped = set(intersection.unsatisfiable)
    if "array" not in declared_types(keywords.get("type")):
        dropped.update(_ALL_OF_VALUE_ITEM_KEYWORDS)
    for keyword in added.keys() & dropped:
        if keyword == "const":
            keywords[extras_key] = {key: value for key, value in keywords[extras_key].items() if key != "const"}
        else:
            keywords.pop(keyword)
    return parser._schema_object(keywords)  # noqa: SLF001


def intersect_typed_value_parts(
    parser: AllOfValuesHost, parts: list[JsonSchemaObject], where: str | None
) -> JsonSchemaObject:
    """Return the intersection of value parts whose row is the first typed part with every part's constraints."""
    return intersect_all_of_value_parts(
        parser,
        parts,
        parser._merge_primitive_schemas(parts).model_dump(exclude_unset=True, by_alias=True),  # noqa: SLF001
        where,
    )


def reference_value_parts(parser: AllOfValuesHost, ref: str) -> list[JsonSchemaObject] | None:
    """Return the value parts of the schema ``ref`` names, or None when it is no schema of one value."""
    parts: list[JsonSchemaObject] = []
    return (
        parts
        if collect_all_of_value_parts(
            parser,
            parser._schema_object({"allOf": [{"$ref": ref}]}),  # noqa: SLF001
            parts,
            resolve_refs=True,
        )
        else None
    )


def output_enforced_value_keywords(
    parser: AllOfValuesHost, schema: JsonSchemaObject
) -> tuple[frozenset[str], bool, dict[str, Any], dict[str, Any]]:
    """Return what a value schema enforces in this output: its constraints only where the output checks them.

    Types, null, formats, literals and item schemas count in every output (``_enforced_value_keywords``). The
    constraints beside an ``enum`` or ``const`` count in none: its literals are those that meet them.
    """
    types, null, constraints, written = parser._enforced_value_keywords(schema)  # noqa: SLF001
    checked = parser._output_model_context.checks_constraints and not (schema.enum or "const" in schema.extras)  # noqa: SLF001
    return types, null, constraints if checked else {}, written


def layers_on_reference(parser: AllOfValuesHost, target: JsonSchemaObject, keys: Collection[str]) -> bool:
    """Return whether the output writes constraints ``keys`` beside a reference to the value schema ``target``.

    Only an alias takes them, and an output that rejects a constraint the alias sets as well (msgspec ``Meta``)
    takes only the others.
    """
    return parser.data_model_root_type.IS_ALIAS and (
        parser._output_model_context.layers_repeated_constraints  # noqa: SLF001
        or not any(
            getattr(target, keyword) is not None for keyword in target.__constraint_field_order__ if keyword in keys
        )
    )


def reference_site_value(
    parser: AllOfValuesHost, obj: JsonSchemaObject, *, layered: bool
) -> tuple[list[JsonSchemaObject], dict[str, Any]] | None:
    """Return the parts and the value of an allOf site whose earlier form leaves out what its parts enforce.

    That form is the type of the site's first ``$ref`` member; with ``layered`` it writes the site's own
    constraints beside it, as an output with field constraints does where the site is a model. It fails when the
    reference is a model class or the output rejects a constraint the referenced alias sets as well, unless
    ``--collapse-root-models`` inlines the site into its holders, which merge the constraints; else it enforces the
    target's constraints with the written ones over those of the same keyword. Where it does not fail, the site
    keeps it (None), under ``--use-site-models-for-constrained-refs`` only where it also enforces what the
    intersection of the parts enforces in this output (``_output_enforced_value_keywords``). None also for a site
    with a ``$ref`` member written with siblings, whose parts are no value schemas or are not intersected here
    (``_intersects_value_parts``), and for one whose parts have no value in common (warned about) where that form
    does not fail. The value is the reference alone when the intersection enforces nothing beyond the referenced
    schema, the reference with the intersection's other constraints layered on it where the output writes field
    constraints and takes them exactly, else the intersection.
    """
    if (
        not (refs := _bare_ref_members(obj))
        or (parts := all_of_value_parts(parser, obj)) is None
        or not intersects_value_parts(parser, parts)
        or (target_parts := reference_value_parts(parser, refs[0])) is None
    ):
        return None
    satisfiable = parser._satisfiable_all_of_value_parts(obj) is not None  # noqa: SLF001
    written = parser._temporary_constraint_values(obj, parser.get_ref_data_type(refs[0])) if layered else {}  # noqa: SLF001
    target = intersect_typed_value_parts(parser, target_parts, None)
    intersection = _enum_narrowed_by_part_consts(
        intersect_typed_value_parts(
            parser, parts, named_location(obj.schema_location, "allOf", row="legacy-unredacted-locations")
        ),
        parts,
        const_support=parser.schema_features.const_support,
    )
    target_dump = target.model_dump(exclude_unset=True, by_alias=True)
    fails = bool(written) and not (parser.collapse_root_models or layers_on_reference(parser, target, written))
    if not fails and (
        not satisfiable
        or not parser.config.use_site_models_for_constrained_refs
        or output_enforced_value_keywords(parser, parser.SCHEMA_OBJECT_TYPE.model_validate({**target_dump, **written}))
        == output_enforced_value_keywords(parser, intersection)
    ):
        return None
    enforced = parser._enforced_value_keywords(intersection)  # noqa: SLF001
    target_enforced = parser._enforced_value_keywords(target)  # noqa: SLF001
    if len(refs) == 1 and enforced == target_enforced:
        return parts, {"$ref": refs[0]}
    value = intersection.model_dump(exclude_unset=True, by_alias=True)
    added = {
        keyword: value[keyword]
        for keyword in intersection.__constraint_field_order__
        if keyword in value and value[keyword] != target_dump.get(keyword)
    }
    if (
        parser.field_constraints
        and len(refs) == 1
        and (enforced[0], enforced[1], enforced[3]) == (target_enforced[0], target_enforced[1], target_enforced[3])
        and layers_on_reference(parser, target, added)
    ):
        return parts, {"allOf": [{"$ref": refs[0]}], **added}
    return parts, value


def all_of_reference_value_schema(parser: AllOfValuesHost, obj: JsonSchemaObject) -> JsonSchemaObject | None:
    """Return the value schema of an allOf site whose earlier form writes its own constraints beside a reference.

    That form is the type of the first ``$ref`` member with the site's own constraints. Under
    ``--use-site-models-for-constrained-refs`` a site of bare ``$ref`` members keeps it where it enforces what the
    site's parts enforce together and does not fail (``_reference_site_value``). Any other site keeps it wherever
    the output takes the constraints beside the reference, else it takes the reference alone where the intersection
    of its parts enforces nothing beyond it, and else that intersection. None for every other site, which keeps its
    earlier form: a site without own constraints the referenced type takes, members that are no value schemas and
    nested combinators. Under ``legacy-all-of-site-form`` a site leaves its earlier form only where that form
    fails, so it keeps it under ``--collapse-root-models``, whose holders merge the constraints.
    """
    members = [item for item in obj.allOf if isinstance(item, JsonSchemaObject)]
    refs = [member.ref for member in members if member.ref]
    if not refs or not (constraints := parser._temporary_constraint_values(obj, parser.get_ref_data_type(refs[0]))):  # noqa: SLF001
        return None
    if parser.config.use_site_models_for_constrained_refs and _bare_ref_members(obj):
        site = reference_site_value(parser, obj, layered=True)
        return None if site is None else _derived_site(parser.SCHEMA_OBJECT_TYPE.model_validate(site[1]), refs)
    parts: list[JsonSchemaObject] = []
    if (
        (parser.collapse_root_models and "legacy-all-of-site-form" in parser._legacy_intersection)  # noqa: SLF001
        or any(member.allOf or member.anyOf or member.oneOf for member in members)
        or (target_parts := reference_value_parts(parser, refs[0])) is None
        or not collect_all_of_value_parts(parser, obj, parts, resolve_refs=True)
    ):
        return None
    target = intersect_typed_value_parts(parser, target_parts, None)
    if layers_on_reference(parser, target, constraints):
        return None
    intersection = intersect_typed_value_parts(
        parser, parts, named_location(obj.schema_location, "allOf", row="legacy-unredacted-locations")
    )
    if len(refs) == 1 and parser._enforced_value_keywords(intersection) == parser._enforced_value_keywords(target):  # noqa: SLF001
        return parser._schema_object({"$ref": refs[0]})  # noqa: SLF001
    return intersection


def allof_lightweight_items(parser: AllOfValuesHost, schema: JsonSchemaObject) -> Sequence[JsonSchemaObject | bool]:
    """Return the members ``_build_allof_type`` builds a type from: own keywords first, then allOf."""
    allof_items: Sequence[JsonSchemaObject | bool] = schema.allOf or []
    if parser._schema_has_own_value_keywords(schema):  # noqa: SLF001
        return [parser._without_allof_keywords(schema), *allof_items]  # noqa: SLF001
    return allof_items


def raise_unsatisfiable_schema(
    parser: AllOfValuesHost,  # noqa: ARG001
    obj: JsonSchemaObject,
    path: list[str],
    keyword: str,
) -> None:
    """Reject the schema ``obj``, whose ``keyword`` branches leave no value it accepts."""
    raise SchemaParseError(
        message=f"{keyword} contains a boolean false schema that makes the schema unsatisfiable",
        path=_named_path(obj.schema_location, path),
    )


def schema_signature(parser: AllOfValuesHost, prop_schema: JsonSchemaObject | bool) -> str | bool:  # noqa: ARG001, FBT001
    """Normalize property schema for comparison across allOf items."""
    if isinstance(prop_schema, bool):
        return prop_schema
    return json.dumps(prop_schema.model_dump(exclude_unset=True, by_alias=True), sort_keys=True, default=repr)


def is_root_model_schema(parser: AllOfValuesHost, obj: JsonSchemaObject) -> bool:
    """Check if schema represents a root model (primitive type with constraints).

    Based on parse_raw_obj() else branch conditions. Returns True when
    the schema would be processed by parse_root_type().
    """
    return not (
        obj.is_array
        or obj.allOf
        or obj.oneOf
        or obj.anyOf
        or obj.properties
        or obj.patternProperties
        or obj.propertyNames is not None
        or obj.type == "object"
    ) and (not obj.enum or parser.ignore_enum_constraints)


def handle_allof_root_model_with_constraints(  # noqa: PLR0911, PLR0912, PLR0915
    parser: AllOfValuesHost,
    name: str,
    obj: JsonSchemaObject,
    path: list[str],
) -> DataType | None:
    """Handle allOf that combines a root model $ref with additional constraints.

    This handler is for generating a root model from a root model reference.
    Object inheritance (with properties) is handled by existing _parse_all_of_item() path.
    Only applies to named schema definitions, not inline properties.
    """
    if any(_is_special_path(path_element) for path_element in path):
        return None  # pragma: no cover

    ref_items = [item for item in obj.allOf if isinstance(item, JsonSchemaObject) and item.ref]

    if len(ref_items) != 1:
        return None

    ref_item = ref_items[0]
    if _merges_ref_siblings(ref_item):
        ref_schema = parser._merge_ref_with_schema(ref_item)  # noqa: SLF001
    else:
        ref_schema = parser._load_ref_schema_object(cast("str", ref_item.ref))  # noqa: SLF001

    if not parser._is_root_model_schema(ref_schema):  # noqa: SLF001
        return None

    constraint_items: list[JsonSchemaObject] = []
    for item in obj.allOf:
        if not isinstance(item, JsonSchemaObject):  # pragma: no cover
            continue
        if item.ref:
            continue
        if item.properties or item.items:
            return None
        if item.has_constraint or item.type or item.format:
            constraint_items.append(item)

    if not constraint_items:
        return None

    site_types: tuple[str, ...] | None = None
    if ref_schema.type and (typed := [item.type for item in constraint_items if item.type]):
        ref_types = declared_types(ref_schema.type)
        if any(_numeric_type_domain(type_) is None for type_ in (ref_schema.type, *typed)):
            applying = [*typed, *([obj.type] if obj.type else [])]
            site_types = reduce(common_types, map(declared_types, applying), ref_types)
            if not site_types:
                return None
            if site_types == ref_types:
                site_types = None

    all_items = [ref_schema, *constraint_items]
    merged_schema = parser._merge_primitive_schemas_for_allof(all_items)  # noqa: SLF001
    if merged_schema is None:
        return None
    if site_types:
        merged_schema = parser._schema_object({  # noqa: SLF001
            **merged_schema.model_dump(exclude_unset=True, by_alias=True),
            "type": site_types[0] if len(site_types) == 1 else list(site_types),
        })

    has_literals = (
        parser.generate_schema_validators
        and hasattr(parser.data_model_root_type, "add_literal_validation")
        and ("enum" in obj.model_fields_set or "const" in obj.extras)
    )
    if obj.has_constraint or obj.description or obj.format or has_literals or parser.field_constraints:
        merged_dict = merged_schema.model_dump(exclude_unset=True, by_alias=True)
        if obj.has_constraint:
            parser._merge_schema_constraints(merged_dict, [obj], intersect=True)  # noqa: SLF001
        if obj.format and not merged_schema.format:
            merged_dict["format"] = obj.format
        if has_literals:
            parser._merge_all_of_root_validation_keywords(merged_dict, [*all_items, obj])  # noqa: SLF001
        if obj.description:
            merged_dict["description"] = obj.description
        if isinstance(schema_type := merged_schema.type, list) and not merged_schema.has_multiple_types:
            schema_type = next((item for item in schema_type if item != "null"), None)
        if isinstance(schema_type, str):
            constraint_types = frozenset({schema_type})
            if (
                (format_ := merged_dict.get("format"))
                and (
                    format_ in parser._data_formats.get(schema_type, {})  # noqa: SLF001
                    or (schema_type, format_) in parser.type_mappings
                )
                and (
                    kinds := all_of_root_keyword_kinds(
                        schema_type,
                        parser._get_type_with_mappings(schema_type, format_).name,  # noqa: SLF001
                    )
                )
                is not None
            ):
                constraint_types = kinds
            merged_dict = parser._drop_incompatible_inherited_constraints(merged_dict, constraint_types)  # noqa: SLF001
        merged_schema = parser._schema_object(merged_dict)  # noqa: SLF001

    merged_schema = merged_schema.with_schema_location(obj.schema_location)
    if has_literals or (obj.format and merged_schema.has_constraint):
        with parser._temporarily_enable_field_constraints():  # noqa: SLF001
            if has_literals:
                return parser._parse_all_of_root_value(name, merged_schema, path)  # noqa: SLF001
            return parser.parse_root_type(name, merged_schema, path)
    return parser.parse_root_type(name, merged_schema, path)


def all_of_pattern_conjunction(parser: AllOfValuesHost) -> Callable[[Sequence[str]], str] | None:
    """Return how ``all_of_member_row`` conjoins two patterns, or None under ``legacy-lookahead-patterns``."""
    return (
        None
        if "legacy-lookahead-patterns" in parser._legacy_intersection  # noqa: SLF001
        else partial(parser._conjoined_pattern, where="allOf")  # noqa: SLF001
    )


def _closes_properties(source: JsonSchemaObject) -> bool:
    """Return whether ``source`` admits no property but its own: additionalProperties false and no patternProperties."""
    return source.additionalProperties is False and not source.patternProperties


def inline_sources_close(parser: AllOfValuesHost, schemas: Iterable[JsonSchemaObject]) -> bool:
    """Return whether a schema of ``schemas`` or one of their inline allOf sources closes the object they combine."""
    return any(
        _closes_properties(source)
        for schema in schemas
        for source in parser._iter_schema_validation_sources(schema, include_references=False)  # noqa: SLF001
    )


def merge_all_of_object(parser: AllOfValuesHost, obj: JsonSchemaObject) -> JsonSchemaObject | None:
    """Merge allOf items when they share object properties to avoid duplicate models.

    Skip merging when there is exactly one $ref (inheritance with property overrides).
    Continue merging when multiple $refs have conflicting property definitions to avoid MRO issues.
    Child property overrides (obj.properties) are not considered conflicts.
    """
    if parser.allof_class_hierarchy == AllOfClassHierarchy.Always:
        # Skip merging when always inherit from the base classes
        return None

    ref_count = sum(1 for item in obj.allOf if isinstance(item, JsonSchemaObject) and item.ref)
    if ref_count == 1:
        return None

    resolved_items: list[JsonSchemaObject] = []
    property_signatures: dict[str, set[str | bool]] = {}
    for item in obj.allOf:
        if not isinstance(item, JsonSchemaObject):  # pragma: no cover
            continue
        resolved_item = parser._load_ref_schema_object(item.ref) if item.ref else item  # noqa: SLF001
        if resolved_item.is_boolean_schema_false:
            raise_unsatisfiable_schema(parser, obj, [], "allOf")
        resolved_items.append(resolved_item)
        if resolved_item.properties:
            for prop_name, prop_schema in resolved_item.properties.items():
                property_signatures.setdefault(prop_name, set()).add(schema_signature(parser, prop_schema))

    if not any(len(signatures) > 1 for signatures in property_signatures.values()):
        return None

    merged_schema: dict[str, Any] = obj.model_dump(exclude={"allOf"}, exclude_unset=True, by_alias=True)
    merge_schema = (
        concatenating_row
        if parser.allof_merge_mode == AllOfMergeMode.NoMerge
        else partial(all_of_member_row, conjoin_patterns=all_of_pattern_conjunction(parser))
    )
    for resolved_item in resolved_items:
        merged_schema = merge_schema(merged_schema, resolved_item.model_dump(exclude_unset=True, by_alias=True))
    if inline_sources_close(parser, (obj, *resolved_items)):
        merged_schema["additionalProperties"] = False

    if "required" in merged_schema and isinstance(merged_schema["required"], list):
        merged_schema["required"] = list(dict.fromkeys(merged_schema["required"]))

    merged_schema.pop("allOf", None)
    return parser._schema_object(merged_schema)  # noqa: SLF001


def merge_all_of_root_value_nodes(
    parser: AllOfValuesHost, nodes: list[JsonSchemaObject | bool]
) -> dict[str, Any] | bool:
    """Intersect schema nodes without dropping constraints on nested properties."""
    first = nodes[0]
    if len(nodes) == 1:
        return _schema_dump(first)
    if False in nodes:
        return False
    references = tuple(
        parser._key_node(parser._ref_path(node.ref))  # noqa: SLF001
        for node in nodes
        if isinstance(node, JsonSchemaObject) and node.ref
    )
    if not references:
        return merge_all_of_root_value_children(parser, nodes)
    if parser._all_of_root_value_ref_stack is None:  # noqa: SLF001
        parser._all_of_root_value_ref_stack = set()  # noqa: SLF001
    if references in parser._all_of_root_value_ref_stack:  # noqa: SLF001
        return {"allOf": [_schema_dump(node) for node in nodes]}
    parser._all_of_root_value_ref_stack.add(references)  # noqa: SLF001
    try:
        return merge_all_of_root_value_children(parser, nodes)
    finally:
        parser._all_of_root_value_ref_stack.remove(references)  # noqa: SLF001


def merge_all_of_root_value_children(  # noqa: PLR0912
    parser: AllOfValuesHost, nodes: list[JsonSchemaObject | bool]
) -> dict[str, Any] | bool:
    """Merge acyclic nodes while leaving recursive references for normal reference resolution.

    The merged node stands for the last node, whose keywords win.
    """
    children: list[JsonSchemaObject] = []
    for child in nodes:
        if not isinstance(child, JsonSchemaObject):
            continue
        child_schema = child
        if child.ref:
            resolved = parser._load_ref_schema_object(child.ref).model_dump(exclude_unset=True, by_alias=True)  # noqa: SLF001
            children.append(parser._schema_object(resolved))  # noqa: SLF001
            child_schema = child.model_copy(update={"ref": None})
        children.append(child_schema)
    merged: dict[str, Any] = {}
    for child in children:
        merged.update(child.model_dump(exclude={"ref"}, exclude_unset=True, by_alias=True))
    if typed := [declared_types(child.type) for child in children if child.type]:
        if not (allowed_types := reduce(common_types, typed, typed[0])):
            return False
        merged["type"] = allowed_types[0] if len(allowed_types) == 1 else sorted(allowed_types)
    parser._merge_schema_constraints(  # noqa: SLF001
        merged, children, intersect=intersects_all_of_constraints(parser.allof_merge_mode.value)
    )
    parser._merge_all_of_root_validation_keywords(merged, children)  # noqa: SLF001
    for keyword in ("properties", "patternProperties"):
        properties: dict[str, list[JsonSchemaObject | bool]] = {}
        for source in children:
            for name, node in (getattr(source, keyword) or {}).items():
                properties.setdefault(name, []).append(node)
        if properties:
            if keyword == "properties":
                forbidden = set()
                for source in children:
                    if _closes_properties(source):
                        forbidden.update(name for name in properties if name not in (source.properties or {}))
                if forbidden:
                    if any(name in source.required for name in forbidden for source in children):
                        return False
                    for name in forbidden:
                        properties.pop(name)
            merged[keyword] = {name: merge_all_of_root_value_nodes(parser, nodes) for name, nodes in properties.items()}
    if required := list(dict.fromkeys(name for source in children for name in source.required)):
        merged["required"] = required
    return _located(merged, children[-1].schema_location if children else None)


def is_all_of_mapping_item(parser: AllOfValuesHost, schema: JsonSchemaObject) -> bool:
    """Return whether an allOf item is an object map, which a mapping-shaped allOf merges into one root map."""
    return not (
        schema.properties
        or schema.patternProperties
        or schema.propertyNames is not None
        or not _is_object_only_type(
            _non_null_types(schema.type) if parser._null_mode.type_excludes_member_null else schema.type  # noqa: SLF001
        )
        or not isinstance(schema.additionalProperties, JsonSchemaObject)
    )


def merge_all_of_root_schema(parser: AllOfValuesHost, obj: JsonSchemaObject) -> JsonSchemaObject | None:  # noqa: PLR0912
    """Unwrap one value schema or merge mapping-shaped allOf items into a root schema."""
    if obj.properties or obj.patternProperties or obj.propertyNames is not None:
        return None

    supports_literal_validation = hasattr(parser.data_model_root_type, "add_literal_validation")
    single_value = len(obj.allOf) == 1 and not parser._schema_requires_model_type(obj)  # noqa: SLF001
    mapping_schemas: list[JsonSchemaObject] = []
    sibling_types: list[str | list[str]] = []
    for item in obj.allOf:
        match item:
            case JsonSchemaObject() as schema:
                if ref := schema.ref:
                    schema = parser._load_ref_schema_object(ref)  # noqa: SLF001
                    if item.type and parser._ref_sibling_keywords_enabled:  # noqa: SLF001
                        sibling_types.append(item.type)
            case _:
                return None
        narrows_literals = bool(
            ref
            and single_value
            and not supports_literal_validation
            and schema.enum
            and ref_member_narrows_literals(parser, item, schema.enum, obj)
        )
        materialize_siblings = bool(
            ref
            and len(item.model_fields_set) > 1
            and parser._ref_sibling_keywords_enabled  # noqa: SLF001
            and (
                item.has_ref_with_schema_keywords
                if supports_literal_validation
                else item.has_constraint or narrows_literals
            )
        )
        scalar_or_array = (
            schema.type
            and schema.type != "object"
            and not (schema.enum and not (materialize_siblings and (supports_literal_validation or narrows_literals)))
        )
        if (
            single_value
            and scalar_or_array
            and not (schema.allOf or schema.anyOf or schema.oneOf)
            and not parser._schema_requires_model_type(schema)  # noqa: SLF001
        ):
            merged_root: JsonSchemaObject | None = None
            if materialize_siblings:
                merged = schema.model_dump(exclude_unset=True, by_alias=True)
                referenced_schema = parser._schema_object(merged)  # noqa: SLF001
                merged.update(item.model_dump(exclude={"ref"}, exclude_unset=True, by_alias=True))
                merged.update(obj.model_dump(exclude={"allOf"}, exclude_unset=True, by_alias=True))
                parser._merge_schema_constraints(  # noqa: SLF001
                    merged, [schema, item, obj], intersect=intersects_all_of_constraints(parser.allof_merge_mode.value)
                )
                if supports_literal_validation or narrows_literals:
                    parser._merge_all_of_root_validation_keywords(merged, [referenced_schema, item, obj])  # noqa: SLF001
                merged_root = parser._schema_object(merged)  # noqa: SLF001
            if not merged_root and ref:
                merged_root = reference_site_schema(parser, obj, layered=parser.field_constraints)
            return merged_root or (
                obj.model_copy(update={"ref": ref, "allOf": []})
                if ref
                else parser._schema_object({  # noqa: SLF001
                    **schema.model_dump(exclude_unset=True, by_alias=True),
                    **obj.model_dump(exclude={"allOf"}, exclude_unset=True, by_alias=True),
                })
            )
        if not parser._is_all_of_mapping_item(schema):  # noqa: SLF001
            return None
        mapping_schemas.append(schema)

    if not mapping_schemas:
        return None

    merged: dict[str, Any] = {}
    for schema in mapping_schemas:
        merged = parser._inherited_override_row(merged, schema.model_dump(exclude_unset=True, by_alias=True))  # noqa: SLF001
    merged = parser._inherited_override_row(  # noqa: SLF001
        merged, obj.model_dump(exclude={"allOf"}, exclude_unset=True, by_alias=True)
    )
    typed = [
        dump_null_sources({"type": type_})
        for type_ in (*sibling_types, *(schema.type for schema in (*mapping_schemas, obj) if schema.type))
    ]
    if parser._null_mode.type_excludes_member_null and any(part.type_lists_null for part in typed):  # noqa: SLF001
        merged["type"] = (
            ["object", "null"] if parser._null_facts(obj, "all-of-map", parts=typed).admits_null else "object"  # noqa: SLF001
        )
    elif merged.get("type") == ["object"]:
        merged["type"] = "object"
    else:
        merged.setdefault("type", "object")
    return parser._schema_object(merged)  # noqa: SLF001


def merge_all_of_value_schema(
    parser: AllOfValuesHost, obj: JsonSchemaObject, path: list[str], base_classes: list[Reference]
) -> JsonSchemaObject | None:
    """Rewrite a named allOf whose members describe one scalar value as the schema it is without allOf."""
    if (
        any(_is_special_path(element) for element in path)
        or any(
            not (target := parser._load_inherited_schema_object(base.path)).ref  # noqa: SLF001
            and parser._schema_requires_model_type(target)  # noqa: SLF001
            for base in base_classes
        )
        or (base_classes and parser._get_inherited_property_map(base_classes))  # noqa: SLF001
    ):
        return None
    own_schemas: list[JsonSchemaObject] = []
    schemas: list[JsonSchemaObject] = []
    pending = [(obj, False)]
    while pending:
        schema, referenced = pending.pop()
        if schema.ref:
            if not referenced:
                own_schemas.append(schema)
            schema, referenced = parser._merge_ref_with_schema(schema), True  # noqa: SLF001
        if schema.allOf:
            pending.append((schema.model_copy(update={"allOf": []}), referenced))
            pending.extend(
                (
                    item if isinstance(item, JsonSchemaObject) else parser._validate_schema_object(item, path),  # noqa: SLF001
                    referenced,
                )
                for item in reversed(schema.allOf)
            )
            continue
        if (
            parser._schema_requires_model_type(schema)  # noqa: SLF001
            or any((schema.is_boolean_schema_false, schema.is_array, schema.anyOf, schema.oneOf))
            or (referenced and parser._schema_literal_values(schema) is not None)  # noqa: SLF001
        ):
            return None
        schemas.append(schema)
        if not referenced:
            own_schemas.append(schema)
    if (
        sum("pattern" in schema.model_fields_set for schema in schemas) > 1
        or (not any(schema.type for schema in schemas) and any(schema.has_constraint for schema in schemas))
        or (merged := parser._merge_value_schemas(schemas)) is None  # noqa: SLF001
    ):
        return None
    if (
        sum(bool(schema.ref) for schema in own_schemas) == 1
        and (reference := parser._merge_value_schemas(own_schemas))  # noqa: SLF001
        and not _merges_ref_siblings(reference)
    ):
        return reference
    return merged


def all_of_value_parts(parser: AllOfValuesHost, obj: JsonSchemaObject) -> list[JsonSchemaObject] | None:
    """Return the parts of an allOf site whose members each describe one value, references resolved, else None."""
    parts: list[JsonSchemaObject] = []
    if (
        parser._schema_requires_model_type(obj)  # noqa: SLF001
        or allof_requires_model_type(parser, obj.allOf, resolve_ref=True)
        or not collect_all_of_value_parts(parser, obj, parts, resolve_refs=True)
    ):
        return None
    return parts


def intersects_value_parts(
    parser: AllOfValuesHost, parts: Sequence[JsonSchemaObject], *, literals: bool = True
) -> bool:
    """Return whether the intersection of value parts is taken here, which needs a value keyword in some part.

    It is not for two parts with ``items`` (their conjunction is not intersected here), two different ``format``,
    and, unless ``literals``, a part with an ``enum`` or ``const``.
    """
    return (
        any(parser._schema_has_own_value_keywords(part) for part in parts)  # noqa: SLF001
        and (literals or not any(part.enum or "const" in part.extras for part in parts))
        and sum(part.items is not None for part in parts) <= 1
        and len({part.format for part in parts if part.format}) <= 1
    )


def all_of_site_schema(
    parser: AllOfValuesHost,
    obj: JsonSchemaObject,
    parts: Sequence[JsonSchemaObject],
    value: dict[str, Any],
    *,
    null_member: bool = False,
) -> JsonSchemaObject:
    """Return the schema of an allOf site with the ``value`` of its parts; its own annotations win, the parts' fill.

    The site admits null as its null facts say (``all-of-value``): in the value's type, else as ``nullable``. With
    ``null_member`` a value without a type, such as a lone ``$ref``, takes the null as an ``anyOf`` member instead,
    which the root-model rules read as they read a ``null`` in a type list; ``nullable`` they read only as legacy.
    """
    annotations: dict[str, Any] = {}
    for part in reversed(parts):
        annotations.update(part.model_dump(include=_ALL_OF_MEMBER_ANNOTATIONS, exclude_unset=True, by_alias=True))
    own = obj.model_dump(exclude={"allOf", *_ALL_OF_VALUE_KEYWORDS}, exclude_unset=True, by_alias=True)
    if value.pop("nullable", False) or parser._null_facts(obj, "all-of-value").admits_null:  # noqa: SLF001
        if (type_ := value.get("type")) is not None:
            value["type"] = [*(item for item in declared_types(type_) if item != "null"), "null"]
        elif null_member:
            value = {"anyOf": [value, null_schema()]}
        else:
            value["nullable"] = True
    return parser._schema_object({**value, **annotations, **own})  # noqa: SLF001


def all_of_root_value_schema(parser: AllOfValuesHost, obj: JsonSchemaObject) -> JsonSchemaObject | None:
    """Return the schema of an allOf site of values that would get a model without fields: its parts intersected.

    A single ``$ref`` member stays the type when the intersection enforces nothing beyond the referenced schema.
    The site's own annotations win, the members' fill in (``_all_of_site_schema``). None when no part has a value
    keyword, and where the site keeps its earlier form: a part with an ``enum`` or ``const`` (an allOf of enum
    references is decided by ``_all_of_enum_site_schema``) and the parts ``_intersects_value_parts`` leaves out.
    Under ``legacy-all-of-site-form`` only where that model is the type of the site, not the member an array site
    writes beside its own array, and the intersection names a type without objects, whose values the model would
    all reject; the site schema is then marked as one (``FIELDLESS_SITE_KEY``) and admits null as a JSON Schema
    null (``null_member``). Such a site keeps the model where a pattern of the intersection needs a regex engine of
    the models that use its type (``_patterns_need_holder_engine``): that engine would apply to their other
    patterns.
    """
    legacy = "legacy-all-of-site-form" in parser._legacy_intersection  # noqa: SLF001
    if (
        (obj.is_array and legacy)
        or (parts := parser._satisfiable_all_of_value_parts(obj)) is None  # noqa: SLF001
        or not intersects_value_parts(parser, parts, literals=False)
        or not (
            _excludes_objects((intersection := intersect_typed_value_parts(parser, parts, None)).type) or not legacy
        )
    ):
        return None
    if legacy and patterns_need_holder_engine(parser, intersection):
        return None
    value: dict[str, Any] = intersection.model_dump(exclude_unset=True, by_alias=True)
    if (
        len(refs := [item.ref for item in obj.allOf if isinstance(item, JsonSchemaObject) and item.ref]) == 1
        and (target_parts := reference_value_parts(parser, refs[0])) is not None
        and parser._enforced_value_keywords(intersection)  # noqa: SLF001
        == parser._enforced_value_keywords(intersect_typed_value_parts(parser, target_parts, None))  # noqa: SLF001
    ):
        value = {"$ref": refs[0]}
    site = all_of_site_schema(parser, obj, parts, value, null_member=legacy)
    if legacy:
        site.__dict__[FIELDLESS_SITE_KEY] = True
    return site


def patterns_need_holder_engine(parser: AllOfValuesHost, schema: JsonSchemaObject) -> bool:
    """Return whether a pattern of a value schema needs a regex engine of the models that use its root type.

    Every pattern its type carries counts: those of its subschemas, the keys of its ``patternProperties`` and those
    of the schemas it names, which the models that use the type reach through it.
    """
    if (needs_engine := parser.data_model_field_type.PATTERN_NEEDS_HOLDER_ENGINE) is None:
        return False
    pending: list[object] = [schema.model_dump(exclude_unset=True, by_alias=True)]
    named: set[str] = set()
    while pending:
        match value := pending.pop():
            case list():
                pending.extend(value)
            case dict():
                pattern_properties: dict[str, object] = value.get("patternProperties", {})
                patterns = [*pattern_properties]
                if isinstance(pattern := value.get("pattern"), str):
                    patterns.append(pattern)
                if any(
                    needs_engine(
                        parser.data_model_root_type,
                        pattern,
                        parser.target_python_version,
                        parser.target_pydantic_version,
                    )
                    for pattern in patterns
                ):
                    return True
                if isinstance(ref := value.get("$ref"), str) and ref not in named:
                    named.add(ref)
                    pending.append(
                        parser._merge_ref_with_schema(parser._schema_object({"$ref": ref}), "allOf").model_dump(  # noqa: SLF001
                            exclude_unset=True, by_alias=True
                        )
                    )
                pending.extend(pattern_properties.values())
                pending.extend(item for key, item in value.items() if key not in _PATTERN_SCAN_SKIPPED_KEYWORDS)
    return False


def reference_site_schema(
    parser: AllOfValuesHost, obj: JsonSchemaObject, *, layered: bool = False
) -> JsonSchemaObject | None:
    """Return the schema of an allOf site whose earlier form, the type of its first ``$ref`` member, it leaves.

    ``_reference_site_value`` decides; the site's own annotations win, the parts' fill (``_all_of_site_schema``).
    """
    if (site := reference_site_value(parser, obj, layered=layered)) is None:
        return None
    return _derived_site(all_of_site_schema(parser, obj, *site), _bare_ref_members(obj))


def all_of_enum_site_schema(parser: AllOfValuesHost, obj: JsonSchemaObject) -> JsonSchemaObject | None:
    """Return the schema of an allOf site of enum references whose merged enum leaves out what its parts enforce.

    Such a site gets a model without fields that ``extract_inherited_enum`` writes as one enum of the members of
    every referenced enum, in the class of the first, without the members a class with a value base cannot hold.
    That enum stays where each referenced enum lists exactly the values the parts admit together, and where the
    parts are no value schemas or are not intersected here. Where they admit no value in common (warned about), it
    stays where its class holds every member, else the site takes its first part. Elsewhere, under
    ``--use-site-models-for-constrained-refs``, the site is the intersection of its parts.
    """
    if (
        not (refs := _bare_ref_members(obj))
        or (parts := all_of_value_parts(parser, obj)) is None
        or not intersects_value_parts(parser, parts)
    ):
        return None
    targets = [
        intersect_typed_value_parts(parser, target_parts, None)
        for ref in refs
        if (target_parts := reference_value_parts(parser, ref)) is not None
    ]
    if not (enums := [target for target in targets if target.enum]):
        return None
    if parser._satisfiable_all_of_value_parts(obj) is None:  # noqa: SLF001
        return (
            all_of_site_schema(parser, obj, parts, parts[0].model_dump(exclude_unset=True, by_alias=True))
            if merged_enum_fails(parser, enums)
            else None
        )
    if not parser.config.use_site_models_for_constrained_refs:
        return None
    intersection = _enum_narrowed_by_part_consts(
        intersect_typed_value_parts(
            parser, parts, named_location(obj.schema_location, "allOf", row="legacy-unredacted-locations")
        ),
        parts,
        const_support=parser.schema_features.const_support,
    )
    literals = intersection.enum
    if all(
        len(intersect_enum_values(target.enum, literals)) == len(target.enum)
        and len(intersect_enum_values(literals, target.enum)) == len(literals)
        for target in enums
    ):
        return None
    return _derived_site(
        all_of_site_schema(parser, obj, parts, intersection.model_dump(exclude_unset=True, by_alias=True)), refs
    )


def merged_enum_fails(parser: AllOfValuesHost, enums: Sequence[JsonSchemaObject]) -> bool:
    """Return whether the enum merged from ``enums``, in the class of the first, takes a member it cannot hold.

    A class with a value base (``IntEnum``, ``StrEnum``) cannot hold a member its value type does not hold; a
    ``StrEnum`` holds only a str, as ``extract_inherited_enum`` decides.
    ``extract_inherited_enum`` leaves out the members it cannot construct (``holds_inherited_member_value``), so
    the merge imports; this decides only whether a site whose parts admit no value in common takes its first part,
    as it did when the merge failed at import. A ``None`` member counts unless ``legacy-all-of-null-as-written``. An
    output that writes the first enum as a ``Literal`` or as its const merges no enum class.
    """
    first = enums[0]
    if parser.should_parse_enum_as_literal(first) or parser._reads_enum_as_const(first):  # noqa: SLF001
        return False
    enum_class = parser._get_enum_model_class(parser._get_enum_type(first), first.enum)[0]  # noqa: SLF001
    value_type = "int" if issubclass(enum_class, IntEnum) else "str" if issubclass(enum_class, StrEnum) else None
    keep = parser.config.keep_enum_member_values
    return value_type is not None and any(
        parser._null_mode.type_excludes_member_null  # noqa: SLF001
        if value is None
        else not (
            holds_inherited_member_value(value_type, value)
            if value_type == "str"
            else holds_member_value(value_type, value, keep_member_values=keep)
        )
        for target in enums
        for value in target.enum
    )


def read_false_all_of_values(
    parser: AllOfValuesHost, obj: JsonSchemaObject, *, lightweight: bool = False
) -> JsonSchemaObject:
    """Return the site ``obj`` with the schema that types its values written ``false`` where it is a false allOf.

    That schema is the additionalProperties of a map that types values with it: an object without properties or
    patternProperties, the keys no pattern matches under --use-single-dict-for-object-maps (``_other_keys_schema``)
    where no patternProperties validator owns them, and a type array of object under
    --use-typed-nullable-map-values (``_typed_nullable_map_value_schema``). It is also the items of an array,
    unless the array is a tuple of no items under --use-tuple-for-fixed-length-arrays
    (``_get_fixed_length_homogeneous_tuple_length``). Where it is ``false`` written as an allOf
    (``_is_false_all_of``), the site is read as the same site with ``false``: the map admits no key it types and the
    array no item past its prefixItems. Where no value is typed with it, it keeps its output. The positions of an
    array (``_read_false_all_of_array_positions``) and the patternProperties and propertyNames of a map
    (``_read_false_all_of_map_schemas``) are read the same way. ``lightweight`` reads the array positions as the
    lightweight type of a TypedDict ``extra_items`` types them (``_build_lightweight_array_type``). A site reads
    none of them while no loaded document holds an ``allOf`` that lists ``false``
    (``SchemaGraphBuilder.holds_false_all_of``).
    """
    if (graph := parser._schema_graph) is None or not graph.holds_false_all_of:  # noqa: SLF001
        return obj
    update: dict[str, object] = {}
    if (
        _is_false_all_of(obj.additionalProperties)
        and not obj.properties
        and (
            not parser._has_pattern_properties_validator(obj) and parser._other_keys_schema(obj) is not None  # noqa: SLF001
            if obj.patternProperties
            else obj.is_object or parser._typed_nullable_map_value_schema(obj) is not None  # noqa: SLF001
        )
    ):
        update["additionalProperties"] = False
    if _is_false_all_of(obj.items) and parser._get_fixed_length_homogeneous_tuple_length(obj) != 0:  # noqa: SLF001
        update["items"] = False
    update |= read_false_all_of_array_positions(parser, obj, lightweight=lightweight) | read_false_all_of_map_schemas(
        parser, obj
    )
    return obj.model_copy(update=update) if update else obj


def read_false_all_of_array_positions(
    parser: AllOfValuesHost, obj: JsonSchemaObject, *, lightweight: bool = False
) -> dict[str, object]:
    """Return the array keywords of ``obj`` whose false allOf the array parse types, with ``false`` in its place.

    Those are the entries of a tuple ``items`` and of ``prefixItems`` that array parsing types, the schemas before
    the first false (``_get_array_item_schemas``), and the ``additionalItems`` or ``unevaluatedItems`` that types
    the items past them. A position it leaves untyped keeps its output. The lightweight type of ``lightweight``
    types the prefixItems and the true tail as well.
    """
    if not obj.is_array or obj.ref:
        return {}
    entries = {"items": obj.items if isinstance(obj.items, list) else [], "prefixItems": obj.prefixItems or []}
    tails = {"additionalItems": obj.additionalItems, "unevaluatedItems": obj.unevaluatedItems}
    if not any(_is_false_all_of(item) for item in (*chain.from_iterable(entries.values()), *tails.values())):
        return {}
    typed = {
        id(item)
        for item in parser._get_array_item_schemas(  # noqa: SLF001
            obj, include_true_tail_schema=lightweight, force_prefix_items=lightweight
        )[0]
    }

    def reads(item: object) -> bool:
        return id(item) in typed and _is_false_all_of(item)

    return {
        **{
            key: [False if reads(item) else item for item in items]
            for key, items in entries.items()
            if any(map(reads, items))
        },
        **{key: False for key, item in tails.items() if reads(item)},
    }


def read_false_all_of_map_schemas(parser: AllOfValuesHost, obj: JsonSchemaObject) -> dict[str, object]:
    """Return the patternProperties or propertyNames of the map ``obj`` whose false allOf it types, as ``false``.

    An object map without properties (``_parse_object_map``) types each value of its patternProperties; without
    patternProperties it types its propertyNames. A patternProperties validator owns the patterns instead, and
    the other schemas of ``obj`` (properties, items, ``$ref``, combinators) leave both untyped, so they keep their
    output.
    """
    if any((obj.properties, obj.is_array, obj.ref, obj.allOf, obj.anyOf, obj.oneOf)):
        return {}
    if pattern_properties := obj.patternProperties:
        if parser.generate_schema_validators or not any(map(_is_false_all_of, pattern_properties.values())):
            return {}
        return {
            "patternProperties": {
                pattern: False if _is_false_all_of(schema) else schema for pattern, schema in pattern_properties.items()
            }
        }
    return {"propertyNames": False} if _is_false_all_of(obj.propertyNames) else {}


def array_all_of_site_schema(parser: AllOfValuesHost, obj: JsonSchemaObject) -> JsonSchemaObject:
    """Return an array site with ``allOf`` as the intersection of its own keywords and its members, else ``obj``.

    The site is then one value schema, as a site of another type is in ``parse_all_of``, instead of its own array
    beside a model of its members. It keeps its form where the root rule (``_all_of_root_value_schema``) does.
    """
    if not (obj.allOf and obj.is_array) or (value := all_of_root_value_schema(parser, obj)) is None:
        return obj
    return value.with_schema_location(obj.schema_location)


def site_schema(
    parser: AllOfValuesHost, obj: JsonSchemaObject, *, merge_ref_siblings: bool = False
) -> JsonSchemaObject:
    """Return ``obj`` as a site reads it: an array ``allOf`` as one value schema, a false allOf as ``false``.

    With ``merge_ref_siblings``, a ``$ref`` whose sibling keywords are parts of it is merged with them first.
    """
    site = array_all_of_site_schema(parser, obj)
    if merge_ref_siblings and _merges_ref_siblings(site):
        site = parser._merge_ref_with_schema(site)  # noqa: SLF001
    return parser._read_false_all_of_values(site)  # noqa: SLF001


def ref_member_narrows_literals(
    parser: AllOfValuesHost, member: JsonSchemaObject, target_literals: object, site: JsonSchemaObject
) -> bool:
    """Return whether the sibling ``enum`` or ``const`` of a ``$ref`` member admits some, not all, target literals.

    Only in a dialect that reads ``$ref`` siblings as parts, and unless ``legacy-all-of-site-form`` is kept;
    siblings that admit none keep the reference.
    """
    if not (
        "legacy-all-of-site-form" not in parser._legacy_intersection  # noqa: SLF001
        and parser._ref_sibling_keywords_enabled  # noqa: SLF001
        and (member.enum or "const" in member.extras)
    ):
        return False
    merged = parser._schema_literal_values(  # noqa: SLF001
        parser._merge_ref_with_schema(  # noqa: SLF001
            member, named_location(site.schema_location, "allOf", row="legacy-unredacted-locations")
        )
    )
    return (
        isinstance(target_literals, list)
        and bool(merged)
        and len(cast("tuple[object, ...]", merged)) < len(target_literals)
    )
