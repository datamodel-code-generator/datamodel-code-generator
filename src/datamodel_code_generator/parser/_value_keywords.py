"""The value keywords of the JSON Schema parser (L1): enum and const literals, constraint keywords and array shapes.

Each function takes the parser through ``ValueKeywordsHost``, which ``JsonSchemaParser`` satisfies structurally. The
methods that are public hooks, that tests call or that other parts of the parser call stay on the parser as delegators
to these functions (``parser/jsonschema.py``), and these functions call them through the parser. The methods that a
warning names (``_parse_enum_class``, ``_admitted_or_warn``, ``_literal_view``) stay parser methods.
"""

from __future__ import annotations

from functools import partial
from itertools import compress
from operator import and_
from typing import TYPE_CHECKING, Any, ClassVar, Final, cast

from typing_extensions import Protocol

from datamodel_code_generator._constraint_applicability import (
    LITERAL_DECIDABLE_KEYWORDS,
    NO_KEYWORDS,
    NUMBER_KEYWORD_ORDER,
    STRING_KEYWORD_ORDER,
    VALUES_AS_WRITTEN_KEY,
    DeclaredKinds,
    declared_keywords,
    declared_string_keywords_taken,
    enforced_keywords,
    holds_number_format,
    literal_check,
    literal_enforces_every_keyword,
    split_declared_keywords,
    undeclared_keywords_taken,
    untaken_values,
    writes_beside_literal,
)
from datamodel_code_generator._json_schema import SCHEMA_MAP_KEYS, SCHEMA_VALUE_KEYS
from datamodel_code_generator.imports import IMPORT_ANY
from datamodel_code_generator.model._decisions.enum_kind import enum_as_literal, is_literal_kind, literal_value_kind
from datamodel_code_generator.model._decisions.literal_default import const_type
from datamodel_code_generator.parser._intersection import declared_types, inherited_positional_tail
from datamodel_code_generator.parser._null_facts import NullSources, dump_null_sources, resolve_null_facts
from datamodel_code_generator.parser._schema_object import _ANNOTATION_KEYWORDS, JsonSchemaObject, _locate
from datamodel_code_generator.parser._schema_reading import (
    _constraint_values,
    _get_json_value_type,
    _object_map_other_types,
    _schema_dump,
)
from datamodel_code_generator.parser.base import _TYPE_DEPENDENT_CONSTRAINTS_KEY
from datamodel_code_generator.types import ANY, NONE, Types, _is_value_literal

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator._shared_types import LiteralType
    from datamodel_code_generator.enums import TargetPydanticVersion
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.model import DataModelFieldBase
    from datamodel_code_generator.model._decisions.enum_kind import LiteralSetting, LiteralValueKind
    from datamodel_code_generator.model.output import OutputModelContext
    from datamodel_code_generator.parser._null_facts import NullFacts, NullSite
    from datamodel_code_generator.parser._schema_object import JsonSchemaLiteral
    from datamodel_code_generator.parser.generation import GraphBuilder
    from datamodel_code_generator.reference import ModelResolver
    from datamodel_code_generator.types import DataType, DataTypeManager

__all__ = [
    "ValueKeywordsHost",
    "accepts_number_constraints",
    "check_literal_const",
    "combined_values_admitted",
    "const_is_literal",
    "const_literal_enforces_constraints",
    "const_type_is_exact",
    "contains_false_requires_match",
    "contains_matches_every_item",
    "create_synthetic_enum_obj",
    "declared_constraint_keywords",
    "declared_kinds",
    "dropped_property_counts",
    "enum_as_literal_under",
    "enum_literal",
    "extract_const_enum_from_combined",
    "get_array_constraints",
    "get_array_item_schemas",
    "get_array_max_items_constraints",
    "get_const_data_type",
    "get_constraint_values",
    "get_contains_count_constraints",
    "get_data_type_from_json_value",
    "get_effective_array_length",
    "get_first_false_schema_index",
    "get_fixed_length_homogeneous_tuple_length",
    "get_fixed_positional_items",
    "get_list_item_schemas",
    "get_position_validation_keywords",
    "get_property_count_constraints",
    "get_scalar_data_type_from_json_value",
    "has_effective_constraints",
    "has_prefix_items_tail_schema_or_boolean",
    "is_constraints_field",
    "is_fixed_length_tuple",
    "keywords_taken",
    "literal_written",
    "mark_type_dependent_constraints",
    "partition_enum_values",
    "positions_with_tail",
    "resolve_array_field_required_nullable",
    "should_parse_empty_object_as_dict",
    "should_parse_enum_as_literal",
    "split_constraint_values",
    "suppress_array_length_constraints",
    "type_enforced_keywords",
]

_CONST_ENUM_TYPES: Final[Mapping[LiteralValueKind, str]] = {
    "str": "string",
    "bool": "boolean",
    "int": "integer",
    "float": "number",
}
"""The ``type`` a merged enum of const branches takes from the kind of the first const that has one."""
_NON_ASSERTING_KEYWORDS = _ANNOTATION_KEYWORDS | {"contentEncoding", "contentMediaType", "contentSchema", "format"}


def _validation_keywords(
    value: dict[str, Any] | bool,  # ruff: ignore[boolean-type-hint-positional-argument]
    canonical_ref: Callable[[str], str],
) -> object:
    """Return the keywords of a dumped schema that decide what it accepts, to compare schemas by what they accept.

    Keywords that only annotate (meta-data, ``format``, content, ``$comment``) and ``x-`` extension keys are left out at
    every level, ``true`` reads as ``{}``, a ``type`` list as its sorted members (one member as that type) and every
    ``$ref`` as its canonical reference key.
    """
    if isinstance(value, bool):
        return {} if value else False

    def schema_value(member: object) -> object:
        return _validation_keywords(member, canonical_ref) if isinstance(member, (dict, bool)) else member

    keywords: dict[str, Any] = {}
    extras = value.get(JsonSchemaObject.__extra_key__)
    for key, item in {**(extras if isinstance(extras, dict) else {}), **value}.items():
        if key in _NON_ASSERTING_KEYWORDS or key == JsonSchemaObject.__extra_key__ or key.startswith("x-"):
            continue
        if key == "$ref" and isinstance(item, str):
            keywords[key] = canonical_ref(item)
        elif key == "type" and isinstance(item, list):
            types = sorted({str(member) for member in item})
            keywords[key] = types[0] if len(types) == 1 else types
        elif key in SCHEMA_VALUE_KEYS:
            keywords[key] = [schema_value(member) for member in item] if isinstance(item, list) else schema_value(item)
        elif key in SCHEMA_MAP_KEYS and isinstance(item, dict):
            keywords[key] = {name: schema_value(member) for name, member in item.items()}
        else:
            keywords[key] = item
    return keywords


_LITERAL_PARTS: Final = "its values and the constraints beside them"
"""The parts the unsatisfiable warning names for a const or an enum none of whose values meets its constraints."""


def _keep_values_as_written(obj: JsonSchemaObject, values: list[Any]) -> JsonSchemaObject:
    """Keep on a schema copy that holds only some enum values those as written, unless it keeps earlier ones."""
    obj.__dict__.setdefault(VALUES_AS_WRITTEN_KEY, values)
    return obj


def _literal_constraints(obj: JsonSchemaObject, written: frozenset[str] = NO_KEYWORDS) -> dict[str, float | str]:
    """Return the limits of the keywords a literal check decides that a schema sets and its field does not write."""
    keys = (key for key in obj.__constraint_field_order__ if key in LITERAL_DECIDABLE_KEYWORDS and key not in written)
    return _constraint_values(obj, keys, as_numbers=True)


def _literal_met(
    obj: JsonSchemaObject, values: Sequence[object], written: frozenset[str]
) -> tuple[tuple[str, ...], tuple[bool, ...], tuple[bool, ...]]:
    """Return the keywords of a schema a literal check decides on values, and whether each value meets them.

    The second result checks every keyword, the third only those the field leaves out (all but ``written``).
    """
    decided, met = literal_check(values, _literal_constraints(obj))
    return decided, met, literal_check(values, _literal_constraints(obj, written))[1] if written else met


def _carried_constraint_keywords(data_type: DataType) -> frozenset[str]:
    """Return the keywords that a type, or a member of a union it is, consumed itself, which its field leaves out."""
    if data_type.is_list or data_type.is_dict or data_type.is_tuple or data_type.is_set or data_type.is_frozen_set:
        return data_type._consumed_constraints  # noqa: SLF001
    return data_type._consumed_constraints.union(  # noqa: SLF001
        *(member._consumed_constraints for member in data_type.data_types)  # noqa: SLF001
    )


def _enforcing_leaves(data_type: DataType) -> Iterator[tuple[str | None, Sequence[object]]]:
    """Yield each type a value of a type validates as but ``None``, as ``enforced_keywords`` reads it.

    A union yields its members' leaves, a container its own leaf without a class name, the ``Literal`` of a const
    or an enum its values and another type its class name.
    """
    if data_type.is_list or data_type.is_dict or data_type.is_tuple or data_type.is_set or data_type.is_frozen_set:
        yield None, ()
    elif members := [member for member in data_type.data_types if member.type != NONE or member.data_types]:
        for member in members:
            yield from _enforcing_leaves(member)
    else:
        yield (None, data_type.literals) if _is_value_literal(data_type) else (data_type.type, ())


def _imported_type_paths(data_type: DataType) -> Iterator[str]:
    """Yield the path of every type that a type and its members import, as a type or inside an annotation."""
    for nested in data_type.all_data_types:
        for import_ in (nested.import_, *(nested.python_type.imports if nested.python_type else ())):
            if import_ is not None and import_.from_:
                yield f"{import_.from_}.{import_.import_}"


_NULL_TYPE_SOURCES: Final[NullSources] = NullSources(type_null=True)
"""The null sources of a branch whose ``type`` is null as written, which ``_extract_const_enum_from_combined`` reads."""


class ValueKeywordsHost(Protocol):
    """The options, run state and hooks of the parser that the value keywords read."""

    SCHEMA_OBJECT_TYPE: ClassVar[type[JsonSchemaObject]]

    @property
    def checks_literal_values(self) -> bool:
        """Whether literal values are checked against their constraints (``--filter-literal-values``)."""

    @property
    def data_model_field_type(self) -> type[DataModelFieldBase]:
        """The field type of the output."""

    @property
    def data_type(self) -> type[DataType]:
        """The data type class of the type manager."""

    @property
    def data_type_manager(self) -> DataTypeManager:
        """The type manager of the output."""

    @property
    def enum_field_as_literal(self) -> LiteralType | None:
        """The ``--enum-field-as-literal`` setting."""

    @property
    def enum_field_as_literal_map(self) -> dict[str, str]:
        """The ``--enum-field-as-literal-map`` entries."""

    @property
    def field_constraints(self) -> bool:
        """Whether constraints are written on fields (``--field-constraints``)."""

    @property
    def ignore_enum_constraints(self) -> bool:
        """Whether an enum leaves its constraints out (``--ignore-enum-constraints``)."""

    @property
    def model_resolver(self) -> ModelResolver:
        """The resolver of references and class names."""

    @property
    def read_const_beside_enum(self) -> bool:
        """Whether a const beside an enum is read (``--read-const-beside-enum``)."""

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
    def use_single_value_enum_as_const(self) -> bool:
        """Whether a single-value enum is read as a const (``--use-single-value-enum-as-const``)."""

    @property
    def use_tuple_for_closed_arrays(self) -> bool:
        """Whether closed arrays are written as tuples (``--use-tuple-for-closed-arrays``)."""

    @property
    def use_tuple_for_fixed_items(self) -> bool:
        """Whether a positional ``items`` list is written as a tuple."""

    @property
    def use_tuple_for_fixed_length_arrays(self) -> bool:
        """Whether a homogeneous fixed-length array is written as a tuple."""

    @property
    def _data_formats(self) -> dict[str, dict[str, Types]]:
        """The data format mappings of this parser type."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The capability through which the passes change the graph of the generation store."""

    @property
    def _legacy_object_maps(self) -> frozenset[str]:
        """The rows of 0.86.0 object maps kept."""

    @property
    def _output_model_context(self) -> OutputModelContext:
        """The capabilities of the output model types."""

    @property
    def _type_override_imports(self) -> dict[str, Import]:
        """The imports of the scoped type overrides."""

    @classmethod
    def _get_array_items_constraints(cls, obj: JsonSchemaObject) -> dict[str, int]:
        """Return array length constraints derived from boolean items."""

    @staticmethod
    def _property_names_forbids_all_keys(property_names: JsonSchemaObject | bool | None) -> bool:  # noqa: FBT001
        """Return whether a propertyNames schema rejects every JSON object key."""

    def _admitted_or_warn(
        self, obj: JsonSchemaObject, met: tuple[bool, ...], kept: tuple[bool, ...], keywords: tuple[str, ...]
    ) -> tuple[bool, ...] | None:
        """Return whether the field keeps each value (``kept``), None when it keeps all."""

    def _get_data_type_from_json_value(self, value: object) -> DataType:
        """Infer a normal Python type from a JSON value."""

    def _get_fixed_length_homogeneous_tuple_length(self, obj: JsonSchemaObject) -> int | None:
        """Return the length of an opted-in homogeneous fixed-length tuple."""

    def _get_fixed_positional_items(
        self,
        obj: JsonSchemaObject,
        canonical_ref: Callable[[str], str] | None = None,
    ) -> list[JsonSchemaObject | bool] | None:
        """Return the positions of a positional array written as a tuple, or None when it is written as a list."""

    def _get_type_with_mappings(self, type_: str, format_: str | None = None) -> Types:
        """Get the Types enum for a given type and format, applying custom type mappings."""

    def _is_fixed_length_tuple(self, obj: JsonSchemaObject) -> bool:
        """Check if an array field represents a fixed-length tuple."""

    def _literal_values_type(self, values: list[Any]) -> DataType:
        """Return the Literal type of enum values, joined with the types of values no Literal holds."""

    def _literal_view(self, obj: JsonSchemaObject, written: frozenset[str] = NO_KEYWORDS) -> JsonSchemaObject:
        """Return an enum schema with only the values that meet the constraints its field leaves out."""

    def _load_inherited_schema_object(self, resolved_ref: str) -> JsonSchemaObject:
        """Load and cache a schema used only for inherited field inspection."""

    def _null_facts(self, obj: JsonSchemaObject, site: NullSite, *, has_default: bool = False) -> NullFacts:
        """Return the null facts of a schema at a site."""

    def _ref_path(self, ref: str) -> str:
        """Return the reference key a reference names: its edge's key, or a plain string's ``resolve_ref``."""

    def _ref_schema_exists(self, resolved_ref: str) -> bool:
        """Return whether a resolved JSON pointer identifies a source schema."""

    def _resolve_external_ref_mapping(self, ref: str) -> tuple[str, str] | None:
        """Return the package and fragment ``--external-ref-mapping`` maps a reference to, if any."""

    def _split_constraint_values(
        self, obj: JsonSchemaObject, data_type: DataType | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return the constraint values of the type-dependent keywords that a field takes, and those it leaves out."""


def should_parse_enum_as_literal(
    parser: ValueKeywordsHost,
    obj: JsonSchemaObject,
    property_name: str | None = None,
    property_obj: JsonSchemaObject | None = None,
) -> bool:
    """Determine if an enum should be parsed as a literal type (``enum_as_literal``).

    Priority (highest to lowest):
    1. x-enum-field-as-literal on the property schema
    2. enum_field_as_literal_map matching Model.field or field
    3. Global enum_field_as_literal setting
    """
    return enum_as_literal_under(
        parser,
        parser.enum_field_as_literal.value if parser.enum_field_as_literal else None,
        obj,
        property_name,
        property_obj,
    )


def enum_as_literal_under(
    parser: ValueKeywordsHost,
    setting: LiteralSetting | None,
    obj: JsonSchemaObject,
    property_name: str | None,
    property_obj: JsonSchemaObject | None,
) -> bool:
    """Return whether an enum is written as a Literal under an ``--enum-field-as-literal`` setting."""
    return enum_as_literal(
        extension=(property_obj if property_obj is not None else obj).x_enum_field_as_literal,
        mapped=parser.enum_field_as_literal_map.get(property_name) if property_name else None,
        setting=setting,
        member_count=len(obj.enum),
    )


def extract_const_enum_from_combined(  # noqa: PLR0912
    parser: ValueKeywordsHost, items: list[JsonSchemaObject | bool], parent: JsonSchemaObject, *, writes: bool = False
) -> tuple[list[Any], list[str], list[str], str | None, bool, list[Any] | None] | None:
    """Extract enum values from oneOf/anyOf const pattern.

    Const branches that --use-single-value-enum-as-const read merge into one Enum only when there are two or more
    and all hold values of one JSON type; otherwise each branch is typed as written, each const as its Literal and
    null as None. Under --read-const-beside-enum, without the option a document's own read-as-const marker reads
    nothing; without --read-const-beside-enum it reads, as in 0.86.0. The values are those that meet their branch's
    constraints and the parent's (``_combined_values_admitted``), with all the consts as written when some do not;
    the parent's field writes its own when ``writes``.
    """
    enum_values: list[Any] = []
    branches: list[JsonSchemaObject] = []
    null_branches: list[NullSources] = []
    inferred_type: str | None = None
    read_as_const = parser.use_single_value_enum_as_const or not parser.read_const_beside_enum

    for item in items:
        if item is False:
            continue
        if item is True:
            return None
        if item.type == "null" and "const" not in item.extras:
            null_branches.append(_NULL_TYPE_SOURCES)
            continue

        if "const" not in item.extras:
            return None

        if item.ref or item.properties or item.oneOf or item.anyOf or item.allOf:
            return None

        const_value = item.extras["const"]
        enum_values.append(const_value)
        branches.append(item)
        read_as_const = read_as_const and item.read_as_const

        if inferred_type is None:
            inferred_type = _CONST_ENUM_TYPES.get(literal_value_kind(const_value))

    if not enum_values:  # pragma: no cover
        return None
    if read_as_const and (
        len(values := [value for value in enum_values if value is not None]) == 1
        or len({_get_json_value_type(value) for value in values}) > 1
    ):
        return None
    values_as_written = None
    if (admitted := combined_values_admitted(parser, parent, enum_values, branches, writes=writes)) is not None:
        values_as_written = enum_values
        enum_values = list(compress(enum_values, admitted))
        branches = list(compress(branches, admitted))
    final_type = inferred_type
    parent_type = parent.type
    match parent_type:
        case str():
            final_type = parent_type
        case list():
            non_null_types = [t for t in parent_type if t != "null"]
            final_type = non_null_types[0] if non_null_types else inferred_type
        case _:
            pass

    return (
        enum_values,
        [item.title for item in branches if item.title],
        [item.description or "" for item in branches],
        final_type,
        bool(
            resolve_null_facts(dump_null_sources({"type": parent.type}), "const-enum", parts=null_branches).admits_null
        ),
        values_as_written,
    )


def combined_values_admitted(
    parser: ValueKeywordsHost,
    parent: JsonSchemaObject,
    values: list[Any],
    branches: list[JsonSchemaObject],
    *,
    writes: bool,
) -> tuple[bool, ...] | None:
    """Return whether the field keeps each const of a oneOf or anyOf, None when it keeps all.

    A const is kept when it meets its branch's constraints and those of the parent its field leaves out
    (``_literal_written``); a const that meets its branch's and every one of the parent's is a value the schema
    admits (``_admitted_or_warn``). None without --filter-literal-values.
    """
    if not parser.checks_literal_values:
        return None
    keywords: dict[str, None] = {}
    own: list[bool] = []
    for value, branch in zip(values, branches, strict=True):
        decided, (met,) = literal_check([value], _literal_constraints(branch))
        keywords.update(dict.fromkeys(decided))
        own.append(met)
    decided, met_parent, kept_parent = _literal_met(
        parent, values, literal_written(parser, parent, values, writes=writes)
    )
    keywords.update(dict.fromkeys(decided))
    return parser._admitted_or_warn(  # noqa: SLF001
        parent, tuple(map(and_, own, met_parent)), tuple(map(and_, own, kept_parent)), ("const", *keywords)
    )


def literal_written(
    parser: ValueKeywordsHost, obj: JsonSchemaObject, values: list[Any], *, writes: bool
) -> frozenset[str]:
    """Return the keywords a literal check leaves to the field of a schema's values, as the field writes them.

    A field that writes Field() arguments (``writes`` at its site) writes beside the ``Literal`` of the values the
    keywords ``_split_constraint_values`` gives it, which Pydantic checks on the ``Literal``; under
    ``--omit-redundant-constraints`` the literal check decides them all and the field writes none of them.
    """
    if not (
        writes_beside_literal(
            parser.data_type_manager.constraint_options,
            writes_field_arguments=writes
            and parser.checks_literal_values
            and parser.data_model_field_type.SUPPORTS_FIELD_CONSTRAINTS,
        )
        and _literal_constraints(obj)
    ):
        return NO_KEYWORDS
    return (
        frozenset(parser._split_constraint_values(obj, parser._literal_values_type(values))[0])  # noqa: SLF001
        & LITERAL_DECIDABLE_KEYWORDS
    )


def check_literal_const(parser: ValueKeywordsHost, obj: JsonSchemaObject, const: object) -> None:
    """Warn when a const does not meet every constraint beside it that the literal check decides."""
    if not parser.checks_literal_values:
        return
    decided, met, _ = _literal_met(obj, [const], NO_KEYWORDS)
    parser._admitted_or_warn(obj, met, met, ("const", *decided))  # noqa: SLF001


def enum_literal(parser: ValueKeywordsHost, obj: JsonSchemaObject, written: frozenset[str] = NO_KEYWORDS) -> DataType:
    """Return the Literal type of the enum values that meet the constraints its field leaves out.

    A type of only some of the values (``_literal_view``) keeps them as written.
    """
    view = parser._literal_view(obj, written)  # noqa: SLF001
    data_type = parser._literal_values_type(view.enum)  # noqa: SLF001
    if (values_as_written := view.__dict__.get(VALUES_AS_WRITTEN_KEY)) is not None:
        parser._graph_builder.record_values_as_written(data_type, values_as_written)  # noqa: SLF001
    return data_type


def create_synthetic_enum_obj(  # noqa: PLR0913, PLR0917
    parser: ValueKeywordsHost,
    original: JsonSchemaObject,
    enum_values: list[Any],
    varnames: list[str],
    descriptions: list[str],
    enum_type: str | None,
    nullable: bool,  # noqa: FBT001
    values_as_written: list[Any] | None = None,
) -> JsonSchemaObject:
    """Create a synthetic JsonSchemaObject for enum parsing, which keeps the consts as written when given."""
    final_enum = [*enum_values, None] if nullable else enum_values
    final_varnames = varnames if len(varnames) == len(enum_values) else []
    enum_metadata = {"x-enum-varnames": final_varnames}
    if any(descriptions):
        enum_metadata["x-enum-descriptions"] = descriptions

    synthetic = parser.SCHEMA_OBJECT_TYPE(
        type=enum_type,
        enum=final_enum,
        nullable=nullable,
        title=original.title,
        description=original.description,
        **(enum_metadata | ({"default": original.default} if original.has_default else {})),
    )
    if values_as_written is not None:
        _keep_values_as_written(synthetic, [*values_as_written, None] if nullable else values_as_written)
    return _locate(synthetic, original.schema_location)


def is_constraints_field(parser: ValueKeywordsHost, obj: JsonSchemaObject) -> bool:
    """Check if a field should include constraints."""
    return obj.is_array or (
        parser.field_constraints
        and not (
            obj.ref
            or obj.anyOf
            or obj.oneOf
            or obj.allOf
            or obj.is_object
            or (obj.enum and not parser.ignore_enum_constraints)
        )
    )


def has_effective_constraints(parser: ValueKeywordsHost, obj: JsonSchemaObject) -> bool:
    """Return whether direct or derived constraints require a generated field."""
    if obj.properties or obj.patternProperties or obj.required:
        return False
    if obj.is_object or obj.propertyNames is not None:
        return bool(get_property_count_constraints(type(parser), obj))
    if obj.is_array:
        return bool(obj.minItems is not None or obj.maxItems is not None or parser._get_array_items_constraints(obj))  # noqa: SLF001

    match obj.type:
        case str() as schema_type:
            pass
        case list() as schema_types if (
            schema_type := next((item for item in schema_types if item != "null"), None)
        ) is not None and all(item in ("null", schema_type) for item in schema_types):  # noqa: PLR6201
            pass
        case _:
            return False

    constraint_fields = (
        NUMBER_KEYWORD_ORDER
        if schema_type in {"integer", "number"}
        else STRING_KEYWORD_ORDER
        if schema_type == "string"
        else None
    )
    if constraint_fields is None:
        return False
    return any(field in obj.model_fields_set for field in constraint_fields)


def accepts_number_constraints(parser: ValueKeywordsHost, types: Sequence[str], format_: str | None) -> bool:
    """Return whether a declared type of the schema holds numbers, as number, integer or a numeric string format."""
    format_ = format_ or "default"
    return any(
        type_ in {"number", "integer"}
        or (
            (
                (type_, format_) in parser.type_mappings
                or (type_ == "string" and format_ in parser._data_formats["string"])  # noqa: SLF001
            )
            and holds_number_format(parser._get_type_with_mappings(type_, format_).name)  # noqa: SLF001
        )
        for type_ in types
    )


def declared_kinds(parser: ValueKeywordsHost, type_: str | list[str] | None, format_: str | None) -> DeclaredKinds:
    """Return the JSON kinds whose keywords a schema's declared types hold, with its format."""
    if type_ is None:
        return DeclaredKinds()
    types = declared_types(type_)
    return DeclaredKinds(
        numbers=accepts_number_constraints(parser, types, format_), string="string" in types, array="array" in types
    )


def declared_constraint_keywords(parser: ValueKeywordsHost, obj: JsonSchemaObject) -> frozenset[str]:
    """Return the type-dependent keywords whose kind a declared type of the schema holds.

    A declared string also holds minItems and maxItems and a declared array also holds minLength and maxLength,
    which are written as their length bounds.
    """
    return declared_keywords(declared_kinds(parser, obj.type, obj.format))


def keywords_taken(
    parser: ValueKeywordsHost, kinds: DeclaredKinds, data_type: DataType
) -> tuple[frozenset[str], frozenset[str]]:
    """Return the keywords of the declared kinds that a field of the emitted type writes, and those it leaves out.

    An output model that writes constraints as ``Field()`` arguments writes a number keyword of a declared number
    only when a number value of the type meets it, and a keyword of a declared string only when the type takes
    it (``declared_string_keywords_taken``), which a ``date`` or a ``UUID`` does not; another output model writes
    them all. The kinds of a schema type and format (``_declared_kinds``) ask it of the type they map to before a
    field is built.
    """
    if not parser.data_model_field_type.SUPPORTS_FIELD_CONSTRAINTS:
        return declared_keywords(kinds), NO_KEYWORDS
    target = parser.target_python_version.version_key
    return split_declared_keywords(
        kinds,
        number_taken=parser.data_type_manager.constraint_keywords(
            data_type, target, number_values=True, target_model_version=parser.target_pydantic_version
        )
        if kinds.numbers
        else None,
        string_taken=declared_string_keywords_taken(
            parser.data_type_manager.constraint_keywords(data_type, target), _imported_type_paths(data_type)
        )
        if kinds.string
        else None,
    )


def get_constraint_values(
    parser: ValueKeywordsHost, obj: JsonSchemaObject, data_type: DataType | None = None
) -> dict[str, Any]:
    """Return JSON Schema constraint values, with the type-dependent keywords that the field takes."""
    return parser._split_constraint_values(obj, data_type)[0]  # noqa: SLF001


def split_constraint_values(
    parser: ValueKeywordsHost, obj: JsonSchemaObject, data_type: DataType | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the constraint values of the type-dependent keywords that a field takes, and those it leaves out.

    The keywords of a kind a declared schema type holds (number, string or array) follow ``_keywords_taken``;
    the emitted Python type decides the others. The left-out ones are the declared keywords the type does not
    take, to warn about, ``minItems`` and ``maxItems`` of a declared string as the lengths they are written as.
    """
    values = _constraint_values(obj, obj.__constraint_field_order__)
    if data_type is not None and (carried := type_enforced_keywords(parser, obj, data_type)):
        values = {key: value for key, value in values.items() if key not in carried}
    if not values:
        return values, {}
    kinds = declared_kinds(parser, obj.type, obj.format)
    if data_type is None:
        declared = declared_keywords(kinds)
        return {key: value for key, value in values.items() if key in declared}, {}
    written, untaken = keywords_taken(parser, kinds, data_type)
    if values.keys() <= written:
        return values, {}
    written |= (
        undeclared_keywords_taken(
            parser.data_type_manager.constraint_keywords(data_type, parser.target_python_version.version_key),
            value_literal=_is_value_literal(data_type),
        )
        - untaken
    )
    return {key: value for key, value in values.items() if key in written}, untaken_values(values, untaken)


def const_literal_enforces_constraints(parser: ValueKeywordsHost, obj: JsonSchemaObject) -> bool:
    """Return whether ``--omit-redundant-constraints`` types a schema by the ``Literal`` of its const alone.

    The ``Literal`` enforces every constraint beside the const when the const meets each of them as the literal
    check decides it, so an item of the schema needs no root model to write them on.
    """
    if "const" not in obj.extras or not const_is_literal(parser, const := obj.extras["const"]):
        return False
    return literal_enforces_every_keyword(
        parser.data_type_manager.constraint_options,
        [const],
        _literal_constraints(obj),
        obj.__constraint_fields__ & obj.model_fields_set,
    )


def type_enforced_keywords(parser: ValueKeywordsHost, obj: JsonSchemaObject, data_type: DataType) -> frozenset[str]:
    """Return the keywords of a schema that the type of its field enforces itself, which the field leaves out.

    The keywords the type consumed (``_carried_constraint_keywords``), and under ``--omit-redundant-constraints``
    also those it enforces by its class or its literal values (``enforced_keywords``).
    """
    return _carried_constraint_keywords(data_type) | enforced_keywords(
        parser.data_type_manager.constraint_options,
        _enforcing_leaves(data_type),
        _literal_constraints(obj),
        parser.data_type_manager.CONSTRAINED_TYPE_CONSUMED_KEYS,
    )


def mark_type_dependent_constraints(
    parser: ValueKeywordsHost, model_field: DataModelFieldBase, obj: JsonSchemaObject
) -> None:
    """Remember the keywords the emitted type decides, so a scoped type override decides them again."""
    if not parser._type_override_imports:  # noqa: SLF001
        return
    declared = declared_constraint_keywords(parser, obj)
    if type_dependent_constraints := _constraint_values(
        obj, (key for key in obj.__constraint_field_order__ if key not in declared)
    ):
        model_field.__dict__[_TYPE_DEPENDENT_CONSTRAINTS_KEY] = type_dependent_constraints


def is_fixed_length_tuple(parser: ValueKeywordsHost, obj: JsonSchemaObject) -> bool:
    """Check if an array field represents a fixed-length tuple."""
    return (
        parser._get_fixed_positional_items(obj) is not None  # noqa: SLF001
        or parser._get_fixed_length_homogeneous_tuple_length(obj) is not None  # noqa: SLF001
    )


def get_fixed_length_homogeneous_tuple_length(parser: ValueKeywordsHost, obj: JsonSchemaObject) -> int | None:
    """Return the length of an opted-in homogeneous fixed-length tuple."""
    if (
        not parser.use_tuple_for_fixed_length_arrays
        or obj.prefixItems is not None
        or not isinstance(obj.items, JsonSchemaObject)
    ):
        return None
    if (tuple_length := obj.minItems) is None or tuple_length != obj.maxItems or tuple_length < 0:
        return None
    return tuple_length


def positions_with_tail(
    cls: type[ValueKeywordsHost],
    positions: Sequence[JsonSchemaObject | bool],
    *tail_candidates: JsonSchemaObject | bool | list[JsonSchemaObject | bool] | None,
    include_true_schema: bool,
) -> list[JsonSchemaObject | bool]:
    """Return the positions before the first false one, or all of them and the first schema of the candidates.

    ``true`` is a candidate only under ``include_true_schema``.
    """
    if (false_index := get_first_false_schema_index(cls, positions)) is not None:
        return [*positions[:false_index]]
    for tail_candidate in tail_candidates:
        match tail_candidate:
            case JsonSchemaObject():
                return [*positions, tail_candidate]
            case True if include_true_schema:
                return [*positions, True]
    return [*positions]


def get_array_item_schemas(
    parser: ValueKeywordsHost,
    obj: JsonSchemaObject,
    *,
    include_true_tail_schema: bool = False,
    force_prefix_items: bool = False,
) -> tuple[list[JsonSchemaObject | bool], bool, bool, int | None]:
    """Return item schemas plus tuple/constraint flags and homogeneous tuple length."""
    if (fixed_items := parser._get_fixed_positional_items(obj)) is not None:  # noqa: SLF001
        return fixed_items, True, True, None
    if (
        isinstance(item_schema := obj.items, JsonSchemaObject)
        and (tuple_item_count := parser._get_fixed_length_homogeneous_tuple_length(obj)) is not None  # noqa: SLF001
    ):
        return [item_schema] if tuple_item_count else [], True, True, tuple_item_count
    return (
        get_list_item_schemas(
            parser,
            obj,
            include_true_tail_schema=include_true_tail_schema,
            force_prefix_items=force_prefix_items,
        ),
        False,
        False,
        None,
    )


def get_list_item_schemas(
    parser: ValueKeywordsHost,
    obj: JsonSchemaObject,
    *,
    include_true_tail_schema: bool = False,
    force_prefix_items: bool = False,
) -> list[JsonSchemaObject | bool]:
    """Return the item schemas whose union the list form of an array holds; none means any item."""
    if obj.prefixItems is not None and (force_prefix_items or has_prefix_items_tail_schema_or_boolean(parser, obj)):
        return positions_with_tail(
            type(parser), obj.prefixItems, obj.items, obj.unevaluatedItems, include_true_schema=include_true_tail_schema
        )

    match obj.items:
        case JsonSchemaObject() as item_schema:
            return [item_schema]
        case list() as item_schemas:
            return positions_with_tail(
                type(parser), item_schemas, obj.additionalItems, include_true_schema=include_true_tail_schema
            )

    match obj.unevaluatedItems:
        case JsonSchemaObject() as item_schema:
            return [item_schema]
        case True if include_true_tail_schema:
            return [True]

    return []


def get_property_count_constraints(cls: type[ValueKeywordsHost], obj: JsonSchemaObject) -> dict[str, int]:
    """Return dict length constraints derived from object property-count keywords."""
    constraints: dict[str, int] = {}
    if obj.minProperties is not None:
        constraints["minItems"] = obj.minProperties
    if obj.maxProperties is not None:
        constraints["maxItems"] = obj.maxProperties
    if cls._property_names_forbids_all_keys(obj.propertyNames) or (
        obj.additionalProperties is False
        and not obj.properties
        and all(value is False for value in (obj.patternProperties or {}).values())
    ):
        constraints["maxItems"] = 0
    return constraints


def dropped_property_counts(parser: ValueKeywordsHost, obj: JsonSchemaObject, data_type: DataType) -> dict[str, int]:
    """Return the property counts of ``obj`` that the object map builder leaves out of its type-array union.

    A field takes the counts only on a type that is a map itself; they are keyed as the field writes the length
    of a map, so the dropped-constraint warning names them. Row ``legacy-map-null-left-to-field`` keeps the counts
    on the field.
    """
    if (
        "legacy-map-null-left-to-field" in parser._legacy_object_maps  # noqa: SLF001
        or not (obj.patternProperties or obj.propertyNames is not None)
        or _object_map_other_types(obj) is None
        or not any(member.is_dict or member.is_mapping for member in data_type.data_types)
    ):
        return {}
    return {
        "minLength" if key == "minItems" else "maxLength": value
        for key, value in get_property_count_constraints(type(parser), obj).items()
    }


def should_parse_empty_object_as_dict(parser: ValueKeywordsHost, obj: JsonSchemaObject) -> bool:  # noqa: ARG001
    return bool(obj.minProperties is not None or obj.maxProperties is not None or obj.propertyNames is not None)


def get_array_max_items_constraints(cls: type[ValueKeywordsHost], obj: JsonSchemaObject) -> list[int]:
    max_items = [
        index
        for positions in (obj.prefixItems, obj.items if isinstance(obj.items, list) else None)
        if (index := get_first_false_schema_index(cls, positions)) is not None
    ]
    if obj.items is False and obj.prefixItems is None:
        max_items.append(0)
    if obj.items is False and obj.prefixItems is not None:
        max_items.append(len(obj.prefixItems))
    if obj.additionalItems is False and isinstance(obj.items, list):
        max_items.append(len(obj.items))
    if obj.unevaluatedItems is False and obj.items is None:
        max_items.append(len(obj.prefixItems or []))
    return max_items


def contains_matches_every_item(parser: ValueKeywordsHost | type[ValueKeywordsHost], obj: JsonSchemaObject) -> bool:  # noqa: ARG001
    contains = obj.extras.get("contains")
    return contains is True or contains == {}


def get_contains_count_constraints(
    cls: type[ValueKeywordsHost],  # noqa: ARG001
    obj: JsonSchemaObject,
) -> tuple[int | None, int | None]:
    min_contains = obj.extras.get("minContains")
    max_contains = obj.extras.get("maxContains")
    min_items = (
        min_contains
        if isinstance(min_contains, int) and not isinstance(min_contains, bool) and min_contains > 0
        else 1
        if "minContains" not in obj.extras
        else None
    )
    max_items = max_contains if isinstance(max_contains, int) and not isinstance(max_contains, bool) else None
    return min_items, max_items


def contains_false_requires_match(parser: ValueKeywordsHost | type[ValueKeywordsHost], obj: JsonSchemaObject) -> bool:  # noqa: ARG001
    if obj.extras.get("contains") is not False:
        return False
    if "minContains" not in obj.extras:
        return True
    min_contains = obj.extras["minContains"]
    return isinstance(min_contains, int) and not isinstance(min_contains, bool) and min_contains > 0


def get_effective_array_length(cls: type[ValueKeywordsHost], obj: JsonSchemaObject) -> int | None:
    """Return the length that the effective length bounds fix, the bounds the length constraints come from."""
    derived = cls._get_array_items_constraints(obj)
    min_items = derived.get("minItems", obj.minItems)
    return min_items if min_items is not None and min_items == derived.get("maxItems", obj.maxItems) else None


def get_fixed_positional_items(
    parser: ValueKeywordsHost,
    obj: JsonSchemaObject,
    canonical_ref: Callable[[str], str] | None = None,
) -> list[JsonSchemaObject | bool] | None:
    """Return the positions of a positional array written as a tuple, or None when it is written as a list.

    Equal explicit ``minItems`` and ``maxItems`` make a tuple. Otherwise the effective length bounds must fix the
    length, and the tuple is written only where the list form would lose positions: a list is kept when its items
    and every allowed position accept the same values, so the list and its length constraints are exact.
    ``canonical_ref`` reads the ``$ref`` values of ``obj``; by default they belong to the document being parsed.
    Without ``--use-tuple-for-closed-arrays`` only equal explicit ``minItems`` and ``maxItems`` make a tuple.
    """
    if obj.prefixItems is not None:
        positional_key, positions = "prefixItems", obj.prefixItems
        explicit = obj.minItems is not None and obj.minItems == obj.maxItems
    elif parser.use_tuple_for_fixed_items and isinstance(obj.items, list):
        positional_key, positions = "items", obj.items
        explicit = obj.minItems == obj.maxItems == len(obj.items)
    else:
        return None
    if not explicit and not parser.use_tuple_for_closed_arrays:
        return None
    length = obj.minItems if explicit else get_effective_array_length(type(parser), obj)
    if length is None or length < 0:
        return None
    tail = cast("JsonSchemaObject | bool", inherited_positional_tail(partial(getattr, obj), positional_key))
    items = [*positions[:length], *(tail for _ in range(length - len(positions)))]
    if any(item is False for item in items):
        return None
    if explicit:
        return items
    read_ref = canonical_ref or parser.model_resolver.resolve_ref
    first, *others = (
        get_position_validation_keywords(parser, schema, read_ref)
        for schema in [*items, *(get_list_item_schemas(parser, obj) or [True])]
    )
    return None if all(other == first for other in others) else items


def get_position_validation_keywords(
    parser: ValueKeywordsHost,
    schema: JsonSchemaObject | bool,  # ruff: ignore[boolean-type-hint-positional-argument]
    canonical_ref: Callable[[str], str],
) -> object:
    """Return the keywords that decide what a position accepts, following a position that is only a ``$ref``.

    A target that an external reference mapping imports, a missing target and a cycle stay the reference key.
    """
    keywords = _validation_keywords(_schema_dump(schema), canonical_ref)
    followed: set[str] = set()
    while (
        isinstance(keywords, dict)
        and keywords.keys() == {"$ref"}
        and (ref := keywords["$ref"]) not in followed
        and parser._resolve_external_ref_mapping(ref) is None  # noqa: SLF001
        and parser._ref_schema_exists(ref)  # noqa: SLF001
    ):
        followed.add(ref)
        target = parser._load_inherited_schema_object(ref)  # noqa: SLF001
        keywords = _validation_keywords(
            False if target.is_boolean_schema_false else target.model_dump(exclude_unset=True, by_alias=True),
            parser._ref_path,  # noqa: SLF001
        )
    return keywords


def get_array_constraints(parser: ValueKeywordsHost, obj: JsonSchemaObject, data_type: DataType) -> dict[str, Any]:
    """Return direct and boolean-schema array constraints."""
    constraints = get_constraint_values(parser, obj, data_type)
    constraints.update(parser._get_array_items_constraints(obj))  # noqa: SLF001
    return constraints


def get_first_false_schema_index(
    cls: type[ValueKeywordsHost],  # noqa: ARG001
    items: Sequence[JsonSchemaObject | bool] | None,
) -> int | None:
    """Return the first tuple/prefix item index that rejects every value."""
    if items is None:
        return None
    return next((index for index, item in enumerate(items) if item is False), None)


def has_prefix_items_tail_schema_or_boolean(parser: ValueKeywordsHost, obj: JsonSchemaObject) -> bool:  # noqa: ARG001
    return bool(
        obj.prefixItems is not None
        and (
            obj.items is False
            or isinstance(obj.items, JsonSchemaObject)
            or obj.unevaluatedItems is False
            or isinstance(obj.unevaluatedItems, JsonSchemaObject)
            or any(isinstance(item, bool) for item in obj.prefixItems)
        )
    )


def suppress_array_length_constraints(
    parser: ValueKeywordsHost, constraints: dict[str, Any] | None, obj: JsonSchemaObject
) -> None:
    if not constraints:
        return
    if parser._is_fixed_length_tuple(obj):  # noqa: SLF001
        constraints.pop("minItems", None)
        constraints.pop("maxItems", None)


def resolve_array_field_required_nullable(parser: ValueKeywordsHost, obj: JsonSchemaObject) -> tuple[bool, bool | None]:
    facts = parser._null_facts(obj, "array-field", has_default=obj.has_default)  # noqa: SLF001
    return bool(facts.required), facts.admits_null


def get_scalar_data_type_from_json_value(parser: ValueKeywordsHost, value: object) -> DataType | None:
    """Infer a normal Python type from a scalar JSON value."""
    if value is None:
        return parser.data_type_manager.get_data_type(Types.null)
    if isinstance(value, bool):
        return parser.data_type_manager.get_data_type(Types.boolean)
    if isinstance(value, int):
        return parser.data_type_manager.get_data_type(Types.integer)
    if isinstance(value, float):
        return parser.data_type_manager.get_data_type(Types.float)
    if isinstance(value, str):
        return parser.data_type_manager.get_data_type(Types.string)
    return None


def get_data_type_from_json_value(parser: ValueKeywordsHost, value: object) -> DataType:
    """Infer a normal Python type from a JSON value."""
    scalar_type = get_scalar_data_type_from_json_value(parser, value)
    if scalar_type is not None:
        return scalar_type
    if isinstance(value, list):
        item_types = [parser._get_data_type_from_json_value(item) for item in value]  # noqa: SLF001
        return parser.data_type(
            data_types=item_types or [parser.data_type(type=ANY, import_=IMPORT_ANY)],
            is_list=True,
        )
    if isinstance(value, dict):
        value_types = [parser._get_data_type_from_json_value(item) for item in value.values()]  # noqa: SLF001
        return parser.data_type(
            data_types=value_types or [parser.data_type(type=ANY, import_=IMPORT_ANY)],
            is_dict=True,
        )
    return parser.data_type_manager.get_data_type(Types.any)


def const_is_literal(parser: ValueKeywordsHost, const: object) -> bool:
    """Return whether the output model writes a JSON Schema const value as a Literal (``const_type``)."""
    return const_type(const, bool_literals=parser._output_model_context.supports_boolean_literals) == "literal"  # noqa: SLF001


def const_type_is_exact(parser: ValueKeywordsHost, const: object) -> bool:
    """Return whether the type of a const value admits that value only: its Literal, or None for null.

    A float, an object or, where the output writes no boolean Literal, a boolean is typed by its value type.
    """
    return const is None or const_is_literal(parser, const)


def get_const_data_type(parser: ValueKeywordsHost, const: object, obj: JsonSchemaObject | None = None) -> DataType:
    """Return a DataType for a JSON Schema const value: its Literal (``const_type``) or its value's type.

    With the schema ``obj``, a Literal const that does not meet the constraints beside it is kept as written, with
    the unsatisfiable warning.
    """
    if const_is_literal(parser, const):
        if obj is not None:
            check_literal_const(parser, obj, const)
        return parser.data_type(literals=[const])
    return parser._get_data_type_from_json_value(const)  # noqa: SLF001


def partition_enum_values(
    parser: ValueKeywordsHost, enum_values: list[Any]
) -> tuple[list[JsonSchemaLiteral], list[object], bool]:
    """Split enum values into literal (``is_literal_kind``) and non-literal values."""
    literal_values: list[JsonSchemaLiteral] = []
    non_literal_values: list[object] = []
    has_null = False
    bool_literals = parser._output_model_context.supports_boolean_literals  # noqa: SLF001
    for enum_value in enum_values:
        if (kind := literal_value_kind(enum_value)) == "none":
            has_null = True
            non_literal_values.append(enum_value)
        elif is_literal_kind(kind, bool_literals=bool_literals):
            literal_values.append(enum_value)
        else:
            non_literal_values.append(enum_value)
    return literal_values, non_literal_values, has_null
