"""The object maps of the JSON Schema parser (L1): additionalProperties, patternProperties and propertyNames dicts.

Each function takes the parser through ``ObjectMapsHost``, which ``JsonSchemaParser`` satisfies structurally. The
methods that are public hooks, that tests call or that other parts of the parser call stay on the parser as delegators
to these functions (``parser/jsonschema.py``), and these functions call them through the parser.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NamedTuple, cast

from typing_extensions import Protocol

from datamodel_code_generator._regex_syntax import literal_pattern_value
from datamodel_code_generator._url_redaction import named_location
from datamodel_code_generator.parser import _all_of_values, _lightweight_types
from datamodel_code_generator.parser._all_of_values import AllOfValuesHost
from datamodel_code_generator.parser._naming import Composed, composed_name
from datamodel_code_generator.parser._origins import with_field_origin, with_type_origin
from datamodel_code_generator.parser._schema_object import JsonSchemaObject
from datamodel_code_generator.parser._schema_reading import _object_map_other_types, _with_ref_siblings
from datamodel_code_generator.parser.base import get_special_path
from datamodel_code_generator.reference import unique_items_of
from datamodel_code_generator.types import ANY, Types, build_union

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from datamodel_code_generator._container_kinds import ContainerSlot
    from datamodel_code_generator._python_type_annotation import PythonTypeExpr
    from datamodel_code_generator.model import DataModel, DataModelFieldBase
    from datamodel_code_generator.parser._naming import Claim, NamingScope
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

__all__ = [
    "ObjectMapsHost",
    "flatten_additional_properties_all_of",
    "get_typed_additional_properties_field",
    "is_direct_key_schema",
    "legacy_object_maps",
    "object_map_admits_null",
    "other_keys_schema",
    "parse_additional_properties_map",
    "parse_nullable_map",
    "parse_pattern_map",
    "parse_pattern_properties",
    "parse_pattern_values",
    "parse_property_name_key_schema",
    "parse_property_names",
    "parse_string_property_name_union",
    "pattern_map_type",
    "typed_nullable_map_value_schema",
]

_ALL_OF_METADATA_FIELDS = ("nullable", "description", "default", "example", "examples", "readOnly", "writeOnly")


class ObjectMapsHost(AllOfValuesHost, Protocol):
    """The options, run state and hooks of the parser that the object maps read and write."""

    typed_nullable_maps: bool
    """Whether this parse typed the values of a nullable map."""

    @property
    def STRING_PROPERTY_NAME_FIELDS(self) -> frozenset[str]:  # noqa: N802
        """The keywords a propertyNames schema of constrained string keys may hold."""

    @property
    def check_pattern_maps_strictly(self) -> bool:
        """Whether the maps the patternProperties validator checks key and count by their patterns."""

    @property
    def data_model_type(self) -> type[DataModel]:
        """The model type of the output."""

    @property
    def use_annotated(self) -> bool:
        """Whether constraints are written with ``Annotated`` (``--use-annotated``)."""

    @property
    def use_single_dict_for_object_maps(self) -> bool:
        """Whether an object map is one dict (``--use-single-dict-for-object-maps``)."""

    @property
    def use_typed_map_values_beside_other_types(self) -> bool:
        """Whether a map beside other types of its type array types its values."""

    def _claiming(self, claim: Claim | None) -> NamingScope:
        """Return the naming frame that makes ``claim`` for the model its block builds, while L1 records them."""

    @property
    def _types_nullable_map_values(self) -> bool:
        """Whether this run types the values of nullable maps."""

    def get_object_field(  # noqa: PLR0913
        self,
        *,
        field_name: str | None,
        field: JsonSchemaObject | None,
        required: bool,
        field_type: DataType,
        alias: str | list[str] | None,
        original_field_name: str | None,
        effective_default: Any = None,
        effective_has_default: bool | None = None,
        use_default_with_required: bool = False,
        class_name: str | None = None,
        output_names: tuple[str, ...] = (),
    ) -> DataModelFieldBase:
        """Build an output field using the shared JSON Schema field policy; without a schema, without its keywords."""

    def parse_item(
        self,
        name: str,
        item: JsonSchemaObject,
        path: list[str],
        singular_name: bool = False,  # noqa: FBT001, FBT002
        parent: JsonSchemaObject | None = None,
    ) -> DataType:
        """Parse a schema into the data type of a field or a value."""

    def _add_nullable_combined_schema_branches(self, obj: JsonSchemaObject) -> JsonSchemaObject:
        """Return a schema whose anyOf and oneOf branches that admit null are followed by a null branch."""

    def _const_narrowed_enum(self, obj: JsonSchemaObject) -> JsonSchemaObject:
        """Return the schema without the enum entries its const excludes."""

    def _container_flags(self, obj: JsonSchemaObject, slot: ContainerSlot) -> dict[str, Any]:
        """Return the container flags and class the x-python-type of a schema gives a data type in ``slot``."""

    def _get_x_python_type(self, obj: JsonSchemaObject) -> PythonTypeExpr | None:
        """Return the type expression of the x-python-type of a schema, if any."""

    def _iter_non_object_union_types(
        self, name: str, obj: JsonSchemaObject, type_list: list[str], path: list[str], *, keep_constraints: bool = False
    ) -> Iterator[DataType]:
        """Yield the types of a type array other than object, keeping their constraints with ``keep_constraints``."""

    def _parse_additional_properties_value(
        self,
        name: str,
        path: list[str],
        parent: JsonSchemaObject,
        *,
        additional_properties: JsonSchemaObject,
        constrained_name: str | None = None,
    ) -> DataType:
        """Parse a mapping value schema while preserving supported Annotated constraints."""

    def _parse_combined_property_names_key_type(
        self, name: str, property_names: JsonSchemaObject, path: list[str]
    ) -> DataType:
        """Build the key type of an anyOf, oneOf or allOf propertyNames schema, parsing it only when needed."""

    def _pattern_value_alias(
        self,
        name: str,
        schema: JsonSchemaObject,
        value_type: DataType,
        path: list[str],
        role: Literal["pattern-property", "conditional-property"] = "pattern-property",
    ) -> DataType:
        """Return the type alias that writes a patternProperties value with its constraints, else ``value_type``."""


def _with_null(data_type: DataType) -> DataType:
    """Return a type equal to ``data_type``, which this parse has just built and nothing holds yet, admitting null.

    The constructor builds it, so the types nested in it take it as their parent.
    """
    fields = {
        name: getattr(data_type, name) for name in type(data_type).model_fields if name not in {"parent", "children"}
    }
    return type(data_type)(**{**fields, "is_optional": True})


def _nullable_map_value_schema(obj: JsonSchemaObject, *, beside_other_types: bool) -> JsonSchemaObject | None:
    """Return the additionalProperties schema of a type array that lists object, beside other types only if asked."""
    return (
        obj.additionalProperties
        if isinstance(obj.type, list)
        and "object" in obj.type
        and (beside_other_types or not obj.has_multiple_types)
        and isinstance(obj.additionalProperties, JsonSchemaObject)
        and "const" not in obj.extras
        else None
    )


def _pattern_value_groups(
    patterns: list[str], value_types: list[DataType], built_types: list[DataType]
) -> dict[str, tuple[list[str], list[DataType]]]:
    """Group the patterns of row ``legacy-map-union-of-dicts`` by the rendered hint of the types their values built.

    A value written as the alias that holds its constraints (``_pattern_value_alias``) is grouped by the type the alias
    holds, so the dicts are those of a map without the aliases. A group keeps the value type of its first pattern and
    that of each later pattern that is written as another type, an alias, or whose schemas declare ``uniqueItems`` at
    other nodes, which are written alike as one; --use-unique-items-as-set then writes an array as a set where every
    value of the group declares it.
    """
    groups: dict[str, tuple[list[str], list[DataType]]] = {}
    for pattern, value_type, built_type in zip(patterns, value_types, built_types, strict=True):
        group_patterns, group_values = groups.setdefault(built_type.type_hint, ([], []))
        group_patterns.append(pattern)
        if all(
            kept.type_hint != value_type.type_hint or _unique_items_paths(kept) != _unique_items_paths(value_type)
            for kept in group_values
        ):
            group_values.append(value_type)
    return groups


def _unique_items_paths(data_type: DataType, path: tuple[int, ...] = ()) -> frozenset[tuple[int, ...]]:
    """Return the paths of indexes to the nodes of a type tree that record ``uniqueItems`` (``unique_items_of``)."""
    paths = {path} if unique_items_of(data_type) else set()
    for index, nested in enumerate(data_type.data_types):
        paths |= _unique_items_paths(nested, (*path, index))
    return frozenset(paths)


def _is_plain_any(data_type: DataType) -> bool:
    """Return whether a type admits any value and nothing else qualifies it."""
    return (
        data_type.type == ANY
        and not data_type.is_optional
        and not data_type.data_types
        and data_type.reference is None
        and data_type.dict_key is None
    )


def _schema_assertions(schema: JsonSchemaObject) -> dict[str, Any]:
    """Return a schema without the keywords that only annotate it, to compare it with another; ``true`` is ``{}``."""
    annotations = JsonSchemaObject.__metadata_only_fields__
    assertions = {key: value for key, value in schema.model_dump(exclude_unset=True).items() if key not in annotations}
    if extras := {name: item for name, item in assertions.pop("extras", {}).items() if name not in annotations}:
        assertions["extras"] = extras
    return assertions


def get_typed_additional_properties_field(
    parser: ObjectMapsHost,
    reference: Reference,
    class_name: str,
    obj: JsonSchemaObject,
    path: list[str],
) -> DataModelFieldBase | None:
    """Build the typed extra field of the model of ``reference`` for its schema-valued extras.

    The extras are those of ``_typed_extra_field_schema``. The field and its dict record the location of the
    additionalProperties schema as their origin (``reference.field_origin_of``, ``reference.type_origin_of``). The model
    of the values is named after ``class_name``, the model's name, which L1 records as its claim (``Composed``) while
    it records naming frames.
    """
    if (additional_props := typed_extra_field_schema(parser, obj)) is None:
        return None
    additional_props = parser._add_nullable_combined_schema_branches(additional_props)  # noqa: SLF001
    additional_property_name = composed_name(class_name, "additional-property")
    with parser._claiming(Composed(reference, "additional-property")) as claimed:  # noqa: SLF001
        extra_value_type = claimed.built(
            parser._parse_additional_properties_value(  # noqa: SLF001
                additional_property_name,
                [*path, "additionalProperties"],
                obj,
                additional_properties=additional_props,
                constrained_name=additional_property_name,
            )
        )
    dict_key = object_map_key_type(parser, obj.propertyNames, "extra-field")

    location = additional_props.schema_location
    field = parser.data_model_type.create_typed_extra_field(
        field_model=parser.data_model_field_type,
        data_type=with_type_origin(
            parser.data_type(data_types=[extra_value_type], is_dict=True, dict_key=dict_key), location
        ),
    )
    return None if field is None else with_field_origin(field, "additional", location)


def flatten_additional_properties_all_of(
    parser: ObjectMapsHost, obj: JsonSchemaObject
) -> list[JsonSchemaObject] | None:
    """Resolve and flatten an additionalProperties allOf schema."""
    schemas: list[JsonSchemaObject] = []
    pending = [obj]
    while pending:
        schema = pending.pop()
        if schema.ref:
            if parser._load_ref_schema_object(schema.ref).is_boolean_schema_false:  # noqa: SLF001
                return None
            schema = parser._merge_ref_with_schema(schema)  # noqa: SLF001
            if schema.ref:  # pragma: no cover
                return None  # pragma: no cover
        if schema.title:
            schema = parser._schema_object(schema.model_dump(exclude={"title"}, exclude_unset=True, by_alias=True))  # noqa: SLF001
        if schema.allOf:
            own_schema = _all_of_values.without_allof_keywords(parser, schema)
            if _all_of_values.schema_has_own_value_keywords(parser, own_schema):
                pending.append(own_schema)
            for item in reversed(schema.allOf):
                match item:
                    case False:
                        return None
                    case True:
                        continue
                    case JsonSchemaObject():  # pragma: no branch
                        pending.append(item)
            continue
        if (
            _all_of_values.schema_requires_model_type(parser, schema)
            or any((schema.anyOf, schema.oneOf, schema.enum))
            or "const" in schema.extras
            or isinstance(schema.type, list)
        ):
            return None  # pragma: no cover
        schemas.append(schema)
    return schemas


def _merge_additional_properties_all_of(parser: ObjectMapsHost, obj: JsonSchemaObject) -> JsonSchemaObject | None:
    """Merge a primitive allOf value schema so every constraint is retained."""
    if not (schemas := flatten_additional_properties_all_of(parser, obj)):
        return None

    schema_types = {schema.type for schema in schemas if schema.type}
    if not schema_types or not schema_types <= {"integer", "number", "string"}:  # pragma: no cover
        return None  # pragma: no cover
    if len(schema_types) > 1 and schema_types != {"integer", "number"}:  # pragma: no cover
        return None  # pragma: no cover
    if (merged_schema := parser._merge_primitive_schemas_for_allof(schemas)) is None:  # pragma: no cover  # noqa: SLF001
        return None  # pragma: no cover

    metadata = {field: getattr(obj, field) for field in _ALL_OF_METADATA_FIELDS if field in obj.model_fields_set}
    if obj.extras:
        metadata["extras"] = obj.extras
    return merged_schema.model_copy(update=metadata) if metadata else merged_schema


def parse_additional_properties_map(
    parser: ObjectMapsHost, name: str, obj: JsonSchemaObject, path: list[str], additional_properties: JsonSchemaObject
) -> DataType:
    """Parse a map whose values follow the additionalProperties schema."""
    return parser.data_type(
        data_types=[
            parser._parse_additional_properties_value(  # noqa: SLF001
                name,
                get_special_path("additionalProperties", path),
                obj,
                additional_properties=additional_properties,
            )
        ],
        **parser._container_flags(obj, "dict"),  # noqa: SLF001
    )


def legacy_object_maps(parser: ObjectMapsHost) -> frozenset[str]:
    """Return the rows of 0.86.0 object maps kept: the rows each map option leaves out of ``_parse_object_map``.

    Without ``--use-single-dict-for-object-maps``: ``legacy-map-union-of-dicts`` (patternProperties give a union
    of dicts, one per value type, with no inexact-map warning), ``legacy-map-pattern-keys-only``
    (additionalProperties beside patternProperties types no key) and ``legacy-map-null-left-to-field`` (the dict
    takes neither the null nor the property counts of its type array or ``nullable``). Without
    ``--check-pattern-maps-strictly``: ``legacy-validated-pattern-maps`` (a map the patternProperties validator
    checks keys its root dict ``str`` and counts no pattern, a declared key witnesses the check, and its model
    joins no other type of its type array).
    """
    rows: set[str] = set()
    if not parser.use_single_dict_for_object_maps:
        rows |= {"legacy-map-union-of-dicts", "legacy-map-pattern-keys-only", "legacy-map-null-left-to-field"}
    if not parser.check_pattern_maps_strictly:
        rows.add("legacy-validated-pattern-maps")
    return frozenset(rows)


def typed_nullable_map_value_schema(parser: ObjectMapsHost, obj: JsonSchemaObject) -> JsonSchemaObject | None:
    """Return the value schema of a nullable map when this run types the values of nullable maps."""
    return (
        _nullable_map_value_schema(obj, beside_other_types=parser.use_typed_map_values_beside_other_types)
        if parser._types_nullable_map_values  # noqa: SLF001
        else None
    )


def parse_nullable_map(
    parser: ObjectMapsHost, name: str, obj: JsonSchemaObject, path: list[str], additional_properties: JsonSchemaObject
) -> DataType:
    """Parse the dict of a type array of object and null, keeping the value type of the map.

    Models created for the values give way to every other model, so the names other models had before
    typed values stay theirs. The object map builder gives the dict its null.
    """
    parser.typed_nullable_maps = True
    with parser.model_resolver.yielding_new_references():
        return parse_additional_properties_map(parser, name, obj, path, additional_properties)


def parse_pattern_properties(
    parser: ObjectMapsHost,
    name: str,
    pattern_properties: dict[str, JsonSchemaObject | bool],
    path: list[str],
    *,
    property_names: JsonSchemaObject | bool | None = None,
) -> DataType:
    """Parse patternProperties into one dict whose keys match a pattern and whose value is the union of theirs.

    Each value is parsed where its pattern declares it, in document order; equal values collapse when the
    type is written, and a value that admits anything makes the whole value ``Any``. The patterns are joined
    into one key pattern, intersected with ``property_names``. A ``false`` pattern admits no key. A map whose
    additionalProperties types the keys that match no pattern (``_other_keys_schema``) is not built here.
    Row ``legacy-map-union-of-dicts`` keeps the map of 0.86.0, a union of dicts, one per value type, whose keys
    match its patterns; an output that cannot decode a union of several dicts, such as msgspec, gets the one dict.
    """
    values = parse_pattern_values(parser, name, pattern_properties, path)
    patterns = [value.pattern for value in values]
    value_types = _constrained_pattern_values(
        parser, name, values, keys_checked=lambda: _pattern_keys_checked(parser, property_names)
    )
    if (
        "legacy-map-union-of-dicts" in parser._legacy_object_maps  # noqa: SLF001
        and value_types
        and (
            len(groups := _pattern_value_groups(patterns, value_types, [value.data_type for value in values])) == 1
            or parser._output_model_context.decodes_value_unions  # noqa: SLF001
        )
    ):
        return build_union(
            parser.data_type,
            [
                pattern_map_type(parser, group_patterns, group_values, property_names, joins_patterns=True)
                for group_patterns, group_values in groups.values()
            ],
        )
    return pattern_map_type(parser, patterns, value_types, property_names, joins_patterns=True)


class _PatternValue(NamedTuple):
    """A pattern that admits a key, the schema of its value, where it is parsed and its type.

    A ``true`` pattern has no schema; its value admits anything, so no value of its dict takes constraints.
    """

    pattern: str
    schema: JsonSchemaObject | None
    path: list[str]
    data_type: DataType


def parse_pattern_values(
    parser: ObjectMapsHost, name: str, pattern_properties: dict[str, JsonSchemaObject | bool], path: list[str]
) -> list[_PatternValue]:
    """Return the patterns that admit a key and their value types, parsed where each pattern declares its value."""
    values: list[_PatternValue] = []
    for i, (pattern, schema) in enumerate(pattern_properties.items()):
        if schema is False:
            continue
        if schema is True:
            values.append(_PatternValue(pattern, None, path, parser.data_type_manager.get_data_type(Types.any)))
            continue
        value_path = get_special_path(f"patternProperties/{i}", path)
        values.append(_PatternValue(pattern, schema, value_path, parser.parse_item(name, schema, value_path)))
    return values


def _constrained_pattern_values(
    parser: ObjectMapsHost,
    name: str,
    values: list[_PatternValue],
    *,
    keys_checked: Callable[[], bool],
    beside: Sequence[DataType] = (),
) -> list[DataType]:
    """Return the value types of the dict of a pattern map, each written with the constraints its type leaves out.

    A value takes them (``_pattern_value_alias``) only where the dict admits no key that matches none of its patterns,
    or types such keys by additionalProperties beside the patterns, which ``keys_checked`` tells: elsewhere a value at
    such a key, which the patterns leave unconstrained, would be checked against them. A dict whose value admits
    anything, as one of its values or those ``beside`` them does, takes none; nor does a dict of several values where
    the output decodes some unions of values only (msgspec), whose union drops the constraints of members of one type
    once its fallbacks are decided.
    """
    value_types = [value.data_type for value in values]
    if (
        not (parser._output_model_context.decodes_value_unions or len(value_types) + len(beside) == 1)  # noqa: SLF001
        or any(_is_plain_any(value_type) for value_type in (*value_types, *beside))
        or not keys_checked()
    ):
        return value_types
    return [
        parser._pattern_value_alias(name, cast("JsonSchemaObject", value.schema), value.data_type, value.path)  # noqa: SLF001
        for value in values
    ]


def _pattern_keys_checked(
    parser: ObjectMapsHost,
    property_names: JsonSchemaObject | bool | None,  # ruff: ignore[boolean-type-hint-positional-argument]
) -> bool:
    """Return whether the dict of a pattern map rejects every key that matches none of its patterns.

    Its key joins the patterns (``object_map_key_type``), which the type manager's map key type checks where it checks a
    pattern (``checks_map_key_patterns``), unless propertyNames, its $ref merged with its siblings, types the keys by
    itself (``_property_name_key_row``), which does not read the patterns.
    """
    if not parser.data_type_manager.checks_map_key_patterns:
        return False
    if not isinstance(property_names, JsonSchemaObject):
        return True
    schema = parser._merge_ref_with_schema(property_names) if property_names.ref else property_names  # noqa: SLF001
    return _property_name_key_row(parser, schema) == "string"


def parse_pattern_map(  # noqa: PLR0913, PLR0917
    parser: ObjectMapsHost,
    name: str,
    pattern_properties: dict[str, JsonSchemaObject | bool],
    path: list[str],
    property_names: JsonSchemaObject | bool | None,  # ruff: ignore[boolean-type-hint-positional-argument]
    other_keys: JsonSchemaObject,
) -> DataType:
    """Build the dict of a pattern map whose additionalProperties ``other_keys`` types the keys no pattern matches.

    The dict's keys are those of ``property_names`` and its value joins the value of ``other_keys`` last.
    """
    values = parse_pattern_values(parser, name, pattern_properties, path)
    other_keys_type = parser._parse_additional_properties_value(  # noqa: SLF001
        name,
        get_special_path("additionalProperties", path),
        other_keys,
        additional_properties=other_keys,
    )
    value_types = [
        *_constrained_pattern_values(parser, name, values, keys_checked=lambda: True, beside=(other_keys_type,)),
        other_keys_type,
    ]
    return pattern_map_type(
        parser, [value.pattern for value in values], value_types, property_names, joins_patterns=False
    )


def pattern_map_type(
    parser: ObjectMapsHost,
    patterns: list[str],
    value_types: list[DataType],
    property_names: JsonSchemaObject | bool | None,  # ruff: ignore[boolean-type-hint-positional-argument]
    *,
    joins_patterns: bool,
) -> DataType:
    """Return the one dict of a pattern map whose value is the union of ``value_types``.

    Its keys match one of ``patterns`` where ``joins_patterns``, else those of ``property_names``. Each dict of row
    ``legacy-map-union-of-dicts`` is one too.
    """
    if not value_types:
        return parser.data_type(
            data_types=[parser.data_type_manager.get_data_type(Types.any)],
            is_dict=True,
        )

    if (any_type := next((value_type for value_type in value_types if _is_plain_any(value_type)), None)) is not None:
        value_types = [any_type]
    return parser.data_type(
        data_types=value_types,
        is_dict=True,
        dict_key=object_map_key_type(
            parser, property_names, "pattern-map", pattern="|".join(patterns) if joins_patterns else None
        ),
    )


def other_keys_schema(parser: ObjectMapsHost, obj: JsonSchemaObject) -> JsonSchemaObject | None:
    """Return the additionalProperties schema that types the keys no pattern of the pattern map of ``obj`` matches.

    Only a schema that asserts something types them, only where the output decodes a union of any value types
    (msgspec keeps its earlier type until its union fallbacks are decided), and not where row
    ``legacy-map-pattern-keys-only`` is kept; otherwise the map admits the keys of its patterns only.
    """
    if (
        "legacy-map-pattern-keys-only" not in parser._legacy_object_maps  # noqa: SLF001
        and isinstance(additional_properties := obj.additionalProperties, JsonSchemaObject)
        and parser._output_model_context.decodes_value_unions  # noqa: SLF001
        and _schema_assertions(additional_properties)
    ):
        return additional_properties
    return None


def is_direct_key_schema(
    parser: ObjectMapsHost,
    property_names: JsonSchemaObject | bool | None,  # ruff: ignore[boolean-type-hint-positional-argument]
) -> bool:
    """Return whether propertyNames is a schema that constrains keys without a ``$ref`` or a combined schema."""
    match property_names:
        case JsonSchemaObject(ref=None, anyOf=[], oneOf=[], allOf=[]) as property_names if (
            not type(parser)._property_names_forbids_all_keys(property_names)  # noqa: SLF001
            and (
                (
                    bool(x_python_type := property_names.extras.get("x-python-type"))
                    and (isinstance(x_python_type, str) or parser._get_x_python_type(property_names) is not None)  # noqa: SLF001
                )
                or property_names.pattern is not None
                or property_names.minLength is not None
                or property_names.maxLength is not None
                or any(isinstance(value, str) for value in property_names.enum)
                or isinstance(property_names.extras.get("const"), str)
            )
        ):
            return True
    return False


def object_map_admits_null(parser: ObjectMapsHost, obj: JsonSchemaObject) -> bool:
    """Return whether an object map admits null: its type array lists object and null, or it is nullable.

    ``nullable: true`` counts under ``--strict-nullable``, the rule ``_get_data_type`` applies to other types,
    unless a combined schema copied it into its branch: the union of the branches admits that null.
    """
    return bool(parser._null_facts(obj, "object-map").admits_null)  # noqa: SLF001


def parse_property_name_key_schema(  # noqa: PLR0911
    parser: ObjectMapsHost,
    property_names: JsonSchemaObject | bool,  # noqa: FBT001
    *,
    member: bool = False,
) -> DataType:
    """Return the key type a propertyNames schema gives the keys of a dict.

    Constrained string keys take the type manager's map key type, except a ``member`` of a union of keys, which takes
    its string type (msgspec decodes a union of strings by one of them).
    """
    if isinstance(property_names, bool):
        return parser.data_type_manager.get_data_type(Types.string)
    match _property_name_key_row(parser, property_names):
        case "int":
            return parser.data_type_manager.get_data_type(Types.integer)
        case "bool":
            return parser.data_type_manager.get_data_type(Types.boolean)
        case "str":
            return parser.data_type_manager.get_data_type(Types.string)
        case "ref":
            return parser.get_ref_data_type(cast("str", property_names.ref))
        case "enum":
            string_enums = [
                value
                for value in parser._const_narrowed_enum(property_names).enum  # noqa: SLF001
                if isinstance(value, str)
            ]
            if string_enums:
                return parser.data_type(literals=string_enums)
            return parser.data_type_manager.get_data_type(Types.string)
        case "const":
            return parser.data_type(literals=[property_names.extras["const"]])
    if (
        property_names.pattern is not None
        or property_names.minLength is not None
        or property_names.maxLength is not None
    ):
        kwargs: dict[str, Any] = {"pattern": property_names.pattern} if property_names.pattern else {}
        kwargs.update(
            (key, value) for key in ("minLength", "maxLength") if (value := getattr(property_names, key)) is not None
        )
        return _map_key_str_type(parser, member=member, **kwargs)
    return parser.data_type_manager.get_data_type(Types.string)


def _map_key_str_type(parser: ObjectMapsHost, *, member: bool = False, **keywords: Any) -> DataType:
    """Return the string type of map keys that ``keywords`` (pattern, lengths) constrain, as the output writes it.

    A ``member`` of a union of key types is a constrained string; the keys themselves take the map key type of the
    data type manager. Both read ``--use-annotated`` and the target Pydantic version.
    """
    keywords.update(use_annotated=parser.use_annotated, target_pydantic_version=parser.target_pydantic_version)
    manager = parser.data_type_manager
    return manager.get_data_type(Types.string, **keywords) if member else manager.get_map_key_str_type(**keywords)


def _property_name_key_row(
    parser: ObjectMapsHost, property_names: JsonSchemaObject
) -> Literal["int", "bool", "str", "ref", "enum", "const", "string"]:
    """Return what types the keys of a propertyNames schema (``parse_property_name_key_schema``).

    The first that applies: its x-python-type of ``int``, ``bool`` or ``str``, its $ref, its enum, its string const,
    else a string that its pattern and lengths constrain (``string``).
    """
    if (python_type := parser._get_x_python_type(property_names)) is not None:  # noqa: SLF001
        from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
            python_type_expr_base_name,
        )

        match base_name := python_type_expr_base_name(python_type):
            case "int" | "bool" | "str":
                return base_name
    if property_names.ref:
        return "ref"
    if property_names.enum:
        return "enum"
    if isinstance(property_names.extras.get("const"), str):
        return "const"
    return "string"


def parse_string_property_name_union(
    parser: ObjectMapsHost, schema: JsonSchemaObject, *, discard_nonstring: bool = False, member: bool = False
) -> tuple[DataType | None, bool]:
    """Build constrained string keys, retaining unions already dominated by a plain string branch.

    ``member`` reads a branch of a union of keys; the one branch an ``anyOf`` keeps is read as the ``anyOf`` is.
    """
    if (
        schema.model_fields_set - parser.STRING_PROPERTY_NAME_FIELDS
        or type(parser)._property_names_forbids_all_keys(schema)  # noqa: SLF001
        or (
            # Newly enforced patterns must work without changing the regex engine.
            # Keep complex patterns on their existing path, including Pydantic 2.0.
            discard_nonstring and schema.pattern and literal_pattern_value(schema.pattern.removeprefix("^")) is None
        )
    ):
        return None, False
    if not schema.anyOf:
        return parse_property_name_key_schema(parser, schema, member=member), not schema.has_constraint
    if schema.has_constraint:
        return None, False
    branches = [
        branch
        for branch in schema.anyOf
        if not (
            branch is False
            or (
                discard_nonstring
                and isinstance(branch, JsonSchemaObject)
                and not branch.model_fields_set - parser.STRING_PROPERTY_NAME_FIELDS
                and type(parser)._property_names_forbids_all_keys(branch)  # noqa: SLF001
            )
        )
    ]
    data_types: list[DataType] = []
    branch_count = 0
    for branch in branches:
        data_type, unrestricted = (
            parse_string_property_name_union(
                parser, branch, discard_nonstring=discard_nonstring, member=member or len(branches) > 1
            )
            if isinstance(branch, JsonSchemaObject)
            else (parser.data_type_manager.get_data_type(Types.string), True)
        )
        if unrestricted and (not parser.field_constraints or branch_count == 0 or len(data_types) != branch_count):
            return None, True
        branch_count += 1
        if data_type is not None:
            data_types.append(data_type)
    if not data_types or len(data_types) != branch_count:
        return None, False
    return build_union(parser.data_type, data_types), False


def parse_property_names(  # noqa: PLR0913, PLR0917
    parser: ObjectMapsHost,
    name: str,
    property_names: JsonSchemaObject | bool,  # noqa: FBT001
    additional_properties: JsonSchemaObject | bool | None,  # noqa: FBT001
    path: list[str],
    parent_obj: JsonSchemaObject | None = None,
) -> DataType:
    """Parse propertyNames into a dict whose keys it constrains and whose values additionalProperties types.

    ``parent_obj`` is the schema whose x-python-type the dict takes.
    """
    # Determine value type from additionalProperties
    if isinstance(additional_properties, JsonSchemaObject):
        value_type = parser._parse_additional_properties_value(  # noqa: SLF001
            name,
            get_special_path("propertyNames/value", path),
            parent_obj or additional_properties,
            additional_properties=additional_properties,
        )
    else:
        value_type = parser.data_type_manager.get_data_type(Types.any)

    key_type = object_map_key_type(parser, property_names, "property-names-map", name=name, path=path)

    dict_flags = parser._container_flags(parent_obj, "dict") if parent_obj else {"is_dict": True}  # noqa: SLF001

    return parser.data_type(
        data_types=[value_type],
        **dict_flags,
        dict_key=key_type,
    )


def typed_extra_field_schema(parser: ObjectMapsHost, obj: JsonSchemaObject) -> JsonSchemaObject | None:
    """Return the additionalProperties schema the typed extra field of the model built from ``obj`` types.

    None where the output model has no typed extra field, additionalProperties is no schema, the patternProperties
    rules of --generate-schema-validators type it (``_pattern_rule_additional_properties``) or it is an allOf with a
    false schema. A $ref with sibling keywords is read merged with them.
    """
    if (
        parser.data_model_type.TYPED_EXTRA_FIELD_NAME is None
        or not isinstance(additional_props := obj.additionalProperties, JsonSchemaObject)
        or parser._has_pattern_properties_validator(obj)  # noqa: SLF001
    ):
        return None
    additional_props = _with_ref_siblings(parser, additional_props)
    if additional_props.allOf and _lightweight_types.contains_false_schema(parser, additional_props.allOf):
        return None
    return additional_props


def get_additional_properties_root_field(
    parser: ObjectMapsHost,
    name: str,
    obj: JsonSchemaObject,
    path: list[str],
) -> DataModelFieldBase:
    """Build the dict root field of an object without declared fields, without leaking value constraints.

    additionalProperties types the values. Where the patternProperties validator checks the map, the values are
    ``Any``; its keys are read from propertyNames as a pattern map reads them and the field reads the keywords that
    count the keys of the object, patternProperties included, so a key that a pattern admits counts, unless row
    ``legacy-validated-pattern-maps`` keeps ``str`` keys and counts without the patterns. Elsewhere the keys are
    ``str``: an object with propertyNames and without properties reaches this field only where the validator checks
    its patterns. The dict takes the object map builder's null unless row ``legacy-map-null-left-to-field`` is kept.
    The field and its dict record the location of ``obj`` as their origin.
    """
    value_type = parser.data_type_manager.get_data_type(Types.any)
    key_type = None
    counts: dict[str, Any] = {
        "minProperties": obj.minProperties,
        "maxProperties": obj.maxProperties,
        "propertyNames": obj.propertyNames,
        "additionalProperties": obj.additionalProperties,
    }
    if parser._has_pattern_properties_validator(obj):  # noqa: SLF001
        if "legacy-validated-pattern-maps" not in parser._legacy_object_maps:  # noqa: SLF001
            key_type = object_map_key_type(parser, obj.propertyNames, "pattern-map")
            counts["patternProperties"] = obj.patternProperties
    else:
        match obj.additionalProperties:
            case JsonSchemaObject() as additional_properties:
                value_type = parser._parse_additional_properties_value(  # noqa: SLF001
                    name,
                    [*path, "additionalProperties"],
                    obj,
                    additional_properties=additional_properties,
                )
    location = obj.schema_location
    map_type = with_type_origin(parser.data_type(data_types=[value_type], is_dict=True, dict_key=key_type), location)
    field = parser.get_object_field(
        field_name=None,
        field=parser._schema_object(counts),  # noqa: SLF001
        required=True,
        original_field_name=None,
        field_type=(
            _with_null(map_type)
            if "legacy-map-null-left-to-field" not in parser._legacy_object_maps and object_map_admits_null(parser, obj)  # noqa: SLF001
            else map_type
        ),
        alias=None,
    )
    with_type_origin(with_field_origin(field, "root", location).data_type, location)
    return field


def object_map_key_type(  # noqa: PLR0913
    parser: ObjectMapsHost,
    property_names: JsonSchemaObject | bool | None,  # ruff: ignore[boolean-type-hint-positional-argument]
    row: Literal["pattern-map", "property-names-map", "extra-field"],
    *,
    pattern: str | None = None,
    name: str = "",
    path: Sequence[str] = (),
) -> DataType | None:
    """Return the key type of the dict of an object map, ``None`` for ``str`` keys.

    Every row reads the key schema it keeps through ``_parse_property_name_key_schema``; the rows differ by the
    position of the dict:

    - ``pattern-map``, the dict of patternProperties and the root dict a pattern validator checks: propertyNames
      read as a string schema, its ``$ref`` resolved; ``pattern`` joins it when the map admits only the keys of its
      patterns, and gives ``str`` keys of that pattern without a propertyNames schema.
    - ``property-names-map``, the dict of ``parse_property_names``: a ``$ref`` merged with its siblings; an anyOf,
      oneOf or allOf parsed as a type under ``name`` and ``path`` (``_parse_combined_property_names_key_type``).
    - ``extra-field``, the typed extra field of a model: only a direct schema that constrains the keys.

    A key type read from a propertyNames schema records its location as its origin (``reference.type_origin_of``).
    """
    if row == "pattern-map" and isinstance(property_names, JsonSchemaObject):
        schema = property_names.model_copy(deep=True)
        schema.type = "string"
        if pattern is not None:
            schema.pattern = (
                pattern
                if schema.pattern is None
                else parser._conjoined_pattern(  # noqa: SLF001
                    (schema.pattern, pattern),
                    named_location(property_names.schema_location, "propertyNames"),
                )
            )
        if schema.ref:
            schema = parser._merge_ref_with_schema(schema)  # noqa: SLF001
    elif row == "pattern-map":
        return None if pattern is None else _map_key_str_type(parser, pattern=pattern)
    elif row == "property-names-map":
        schema = cast("JsonSchemaObject | bool", property_names)
        if isinstance(schema, JsonSchemaObject):
            schema = _with_ref_siblings(parser, schema)
        if isinstance(schema, JsonSchemaObject) and (schema.anyOf or schema.oneOf or schema.allOf):
            return with_type_origin(
                parser._parse_combined_property_names_key_type(name, schema, [*path]),  # noqa: SLF001
                schema.schema_location,
            )
    elif is_direct_key_schema(parser, property_names):
        schema = cast("JsonSchemaObject", property_names)
    else:
        return None
    return with_type_origin(
        parse_property_name_key_schema(parser, schema),
        schema.schema_location if isinstance(schema, JsonSchemaObject) else None,
    )


def object_map_type_union(
    parser: ObjectMapsHost, name: str, obj: JsonSchemaObject, path: list[str], map_type: DataType
) -> DataType | None:
    """Return the union of the dict of an object map with the other types of its type array, or None."""
    if (type_list := _object_map_other_types(obj)) is None:
        return None
    return build_union(
        parser.data_type,
        [map_type, *parser._iter_non_object_union_types(name, obj, type_list, path, keep_constraints=True)],  # noqa: SLF001
        nullable=object_map_admits_null(parser, obj),
    )
