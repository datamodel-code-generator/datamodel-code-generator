"""The object maps of the JSON Schema parser (L1): additionalProperties, patternProperties and propertyNames dicts.

Each function takes the parser through ``ObjectMapsHost``, which ``JsonSchemaParser`` satisfies structurally. The
methods that are public hooks, that tests call or that other parts of the parser call stay on the parser as delegators
to these functions (``parser/jsonschema.py``), and these functions call them through the parser.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from typing_extensions import Protocol

from datamodel_code_generator._regex_syntax import literal_pattern_value
from datamodel_code_generator.parser._schema_object import JsonSchemaObject
from datamodel_code_generator.parser._schema_reading import _merges_ref_siblings, _object_map_other_types
from datamodel_code_generator.parser.base import get_special_path
from datamodel_code_generator.types import ANY, Types, build_union

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from datamodel_code_generator._python_type_annotation import PythonTypeExpr
    from datamodel_code_generator.enums import TargetPydanticVersion
    from datamodel_code_generator.model import DataModel, DataModelFieldBase
    from datamodel_code_generator.model.output import OutputModelContext
    from datamodel_code_generator.parser._null_facts import NullFacts, NullSite
    from datamodel_code_generator.reference import ModelResolver
    from datamodel_code_generator.types import DataType, DataTypeManager

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


class ObjectMapsHost(Protocol):
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
    def data_model_field_type(self) -> type[DataModelFieldBase]:
        """The field type of the output."""

    @property
    def data_model_type(self) -> type[DataModel]:
        """The model type of the output."""

    @property
    def data_type(self) -> type[DataType]:
        """The data type class of the type manager."""

    @property
    def data_type_manager(self) -> DataTypeManager:
        """The type manager of the output."""

    @property
    def field_constraints(self) -> bool:
        """Whether constraints are written on fields (``--field-constraints``)."""

    @property
    def model_resolver(self) -> ModelResolver:
        """The resolver of references and class names."""

    @property
    def target_pydantic_version(self) -> TargetPydanticVersion | None:
        """The Pydantic version the output targets, if configured."""

    @property
    def use_annotated(self) -> bool:
        """Whether constraints are written with ``Annotated`` (``--use-annotated``)."""

    @property
    def use_single_dict_for_object_maps(self) -> bool:
        """Whether an object map is one dict (``--use-single-dict-for-object-maps``)."""

    @property
    def use_typed_map_values_beside_other_types(self) -> bool:
        """Whether a map beside other types of its type array types its values."""

    @property
    def _legacy_object_maps(self) -> frozenset[str]:
        """The rows of 0.86.0 object maps kept."""

    @property
    def _output_model_context(self) -> OutputModelContext:
        """The capabilities of the output model types."""

    @property
    def _types_nullable_map_values(self) -> bool:
        """Whether this run types the values of nullable maps."""

    @staticmethod
    def _property_names_forbids_all_keys(property_names: JsonSchemaObject | bool | None) -> bool:  # noqa: FBT001
        """Return whether a propertyNames schema admits no key."""

    def get_ref_data_type(self, ref: str) -> DataType:
        """Return the data type a reference names."""

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

    def _conjoined_pattern(self, patterns: Sequence[str], where: str) -> str:
        """Return the conjoined pattern, warning about the patterns it leaves out."""

    def _contains_false_schema(self, items: Iterable[JsonSchemaObject | bool]) -> bool:
        """Return whether one of ``items`` is a false schema."""

    def _get_python_type_flags(self, obj: JsonSchemaObject) -> dict[str, bool]:
        """Return the container flags the x-python-type of a schema gives its data type."""

    def _get_x_python_type(self, obj: JsonSchemaObject) -> PythonTypeExpr | None:
        """Return the type expression of the x-python-type of a schema, if any."""

    def _has_pattern_properties_validator(self, obj: JsonSchemaObject) -> bool:
        """Return whether schema runtime validation owns patternProperties dispatch."""

    def _iter_non_object_union_types(
        self, name: str, obj: JsonSchemaObject, type_list: list[str], path: list[str], *, keep_constraints: bool = False
    ) -> Iterator[DataType]:
        """Yield the types of a type array other than object, keeping their constraints with ``keep_constraints``."""

    def _load_ref_schema_object(self, ref: str) -> JsonSchemaObject:
        """Return the schema object a reference names."""

    def _merge_primitive_schemas_for_allof(self, items: list[JsonSchemaObject]) -> JsonSchemaObject | None:
        """Return the one primitive schema the parts of an allOf merge into, if they merge."""

    def _merge_ref_with_schema(self, obj: JsonSchemaObject, where: str | None = None) -> JsonSchemaObject:
        """Return the schema a ``$ref`` names merged with the sibling keywords of the reference."""

    def _null_facts(self, obj: JsonSchemaObject, site: NullSite) -> NullFacts:
        """Return the null facts of a schema at a site."""

    def _object_map_key_type(
        self,
        property_names: JsonSchemaObject | bool | None,  # ruff: ignore[boolean-type-hint-positional-argument]
        row: Literal["pattern-map", "property-names-map", "extra-field"],
        *,
        pattern: str | None = None,
        name: str = "",
        path: Sequence[str] = (),
    ) -> DataType | None:
        """Return the key type of the dict of an object map, ``None`` for ``str`` keys."""

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

    def _schema_has_own_value_keywords(self, schema: JsonSchemaObject) -> bool:
        """Return whether a schema has value keywords of its own."""

    def _schema_object(self, raw: object) -> JsonSchemaObject:
        """Validate a raw schema into a schema object."""

    def _schema_requires_model_type(self, item: JsonSchemaObject) -> bool:
        """Return whether a schema needs a model type."""

    def _typed_extra_field_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Return the additionalProperties schema the typed extra field of the model built from ``obj`` types."""

    def _without_allof_keywords(self, schema: JsonSchemaObject) -> JsonSchemaObject:
        """Return a schema without its allOf."""


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


def _pattern_value_groups(patterns: list[str], value_types: list[DataType]) -> dict[str, tuple[list[str], DataType]]:
    """Group the patterns of row ``legacy-map-union-of-dicts`` by the rendered hint of their value types."""
    groups: dict[str, tuple[list[str], DataType]] = {}
    for pattern, value_type in zip(patterns, value_types, strict=True):
        groups.setdefault(value_type.type_hint, ([], value_type))[0].append(pattern)
    return groups


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
    class_name: str,
    obj: JsonSchemaObject,
    path: list[str],
) -> DataModelFieldBase | None:
    """Build the output model's typed extra field for schema-valued extras (``_typed_extra_field_schema``)."""
    if (additional_props := parser._typed_extra_field_schema(obj)) is None:  # noqa: SLF001
        return None
    additional_props = parser._add_nullable_combined_schema_branches(additional_props)  # noqa: SLF001
    additional_property_name = f"{class_name}AdditionalProperty"
    extra_value_type = parser._parse_additional_properties_value(  # noqa: SLF001
        additional_property_name,
        [*path, "additionalProperties"],
        obj,
        additional_properties=additional_props,
        constrained_name=additional_property_name,
    )
    dict_key = parser._object_map_key_type(obj.propertyNames, "extra-field")  # noqa: SLF001

    return parser.data_model_type.create_typed_extra_field(
        field_model=parser.data_model_field_type,
        data_type=parser.data_type(
            data_types=[extra_value_type],
            is_dict=True,
            dict_key=dict_key,
        ),
    )


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
            own_schema = parser._without_allof_keywords(schema)  # noqa: SLF001
            if parser._schema_has_own_value_keywords(own_schema):  # noqa: SLF001
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
            parser._schema_requires_model_type(schema)  # noqa: SLF001
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
        **(parser._get_python_type_flags(obj) or {"is_dict": True}),  # noqa: SLF001
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
    patterns, value_types = parse_pattern_values(parser, name, pattern_properties, path)
    if (
        "legacy-map-union-of-dicts" in parser._legacy_object_maps  # noqa: SLF001
        and value_types
        and (
            len(groups := _pattern_value_groups(patterns, value_types)) == 1
            or parser._output_model_context.decodes_value_unions  # noqa: SLF001
        )
    ):
        return build_union(
            parser.data_type,
            [
                pattern_map_type(parser, group_patterns, [value_type], property_names, joins_patterns=True)
                for group_patterns, value_type in groups.values()
            ],
        )
    return pattern_map_type(parser, patterns, value_types, property_names, joins_patterns=True)


def parse_pattern_values(
    parser: ObjectMapsHost, name: str, pattern_properties: dict[str, JsonSchemaObject | bool], path: list[str]
) -> tuple[list[str], list[DataType]]:
    """Return the patterns that admit a key and their value types, parsed where each pattern declares its value."""
    patterns: list[str] = []
    value_types: list[DataType] = []
    for i, (pattern, schema) in enumerate(pattern_properties.items()):
        if schema is False:
            continue
        patterns.append(pattern)
        value_types.append(
            parser.data_type_manager.get_data_type(Types.any)
            if schema is True
            else parser.parse_item(name, schema, get_special_path(f"patternProperties/{i}", path))
        )
    return patterns, value_types


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
    patterns, value_types = parse_pattern_values(parser, name, pattern_properties, path)
    value_types.append(
        parser._parse_additional_properties_value(  # noqa: SLF001
            name,
            get_special_path("additionalProperties", path),
            other_keys,
            additional_properties=other_keys,
        )
    )
    return pattern_map_type(parser, patterns, value_types, property_names, joins_patterns=False)


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
        dict_key=parser._object_map_key_type(  # noqa: SLF001
            property_names, "pattern-map", pattern="|".join(patterns) if joins_patterns else None
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
) -> DataType:
    """Return the key type a propertyNames schema gives the keys of a dict."""
    if isinstance(property_names, bool):
        return parser.data_type_manager.get_data_type(Types.string)
    if (python_type := parser._get_x_python_type(property_names)) is not None:  # noqa: SLF001
        from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
            python_type_expr_base_name,
        )

        match python_type_expr_base_name(python_type):
            case "int":
                return parser.data_type_manager.get_data_type(Types.integer)
            case "bool":
                return parser.data_type_manager.get_data_type(Types.boolean)
            case "str":
                return parser.data_type_manager.get_data_type(Types.string)
    if property_names.ref:
        return parser.get_ref_data_type(property_names.ref)
    if property_names.enum:
        string_enums = [value for value in parser._const_narrowed_enum(property_names).enum if isinstance(value, str)]  # noqa: SLF001
        if string_enums:
            return parser.data_type(literals=string_enums)
        return parser.data_type_manager.get_data_type(Types.string)
    if isinstance(property_names.extras.get("const"), str):
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
        return parser.data_type_manager.get_data_type(
            Types.string,
            use_annotated=parser.use_annotated,
            target_pydantic_version=parser.target_pydantic_version,
            **kwargs,
        )
    return parser.data_type_manager.get_data_type(Types.string)


def parse_string_property_name_union(
    parser: ObjectMapsHost, schema: JsonSchemaObject, *, discard_nonstring: bool = False
) -> tuple[DataType | None, bool]:
    """Build constrained string keys, retaining unions already dominated by a plain string branch."""
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
        return parse_property_name_key_schema(parser, schema), not schema.has_constraint
    if schema.has_constraint:
        return None, False
    data_types: list[DataType] = []
    branch_count = 0
    for branch in schema.anyOf:
        if branch is False or (
            discard_nonstring
            and isinstance(branch, JsonSchemaObject)
            and not branch.model_fields_set - parser.STRING_PROPERTY_NAME_FIELDS
            and type(parser)._property_names_forbids_all_keys(branch)  # noqa: SLF001
        ):
            continue
        data_type, unrestricted = (
            (parser.data_type_manager.get_data_type(Types.string), True)
            if branch is True
            else parse_string_property_name_union(parser, branch, discard_nonstring=discard_nonstring)
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

    key_type = parser._object_map_key_type(property_names, "property-names-map", name=name, path=path)  # noqa: SLF001

    dict_flags: dict[str, bool] = {"is_dict": True}
    if parent_obj:  # pragma: no branch
        python_type_flags = parser._get_python_type_flags(parent_obj)  # noqa: SLF001
        if python_type_flags:  # pragma: no cover
            dict_flags = python_type_flags

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
    if _merges_ref_siblings(additional_props):
        additional_props = parser._merge_ref_with_schema(additional_props)  # noqa: SLF001
    if additional_props.allOf and parser._contains_false_schema(additional_props.allOf):  # noqa: SLF001
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
            key_type = parser._object_map_key_type(obj.propertyNames, "pattern-map")  # noqa: SLF001
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
    map_type = parser.data_type(data_types=[value_type], is_dict=True, dict_key=key_type)
    return parser.get_object_field(
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
    """
    if row == "pattern-map" and isinstance(property_names, JsonSchemaObject):
        schema = property_names.model_copy(deep=True)
        schema.type = "string"
        if pattern is not None:
            schema.pattern = (
                pattern
                if schema.pattern is None
                else parser._conjoined_pattern(  # noqa: SLF001
                    (schema.pattern, pattern), str(property_names.schema_location or "propertyNames")
                )
            )
        if schema.ref:
            schema = parser._merge_ref_with_schema(schema)  # noqa: SLF001
    elif row == "pattern-map":
        return (
            None
            if pattern is None
            else parser.data_type_manager.get_data_type(
                Types.string,
                pattern=pattern,
                use_annotated=parser.use_annotated,
                target_pydantic_version=parser.target_pydantic_version,
            )
        )
    elif row == "property-names-map":
        schema = cast("JsonSchemaObject | bool", property_names)
        if isinstance(schema, JsonSchemaObject) and _merges_ref_siblings(schema):
            schema = parser._merge_ref_with_schema(schema)  # noqa: SLF001
        if isinstance(schema, JsonSchemaObject) and (schema.anyOf or schema.oneOf or schema.allOf):
            return parser._parse_combined_property_names_key_type(name, schema, [*path])  # noqa: SLF001
    elif is_direct_key_schema(parser, property_names):
        schema = cast("JsonSchemaObject", property_names)
    else:
        return None
    return parse_property_name_key_schema(parser, schema)


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
