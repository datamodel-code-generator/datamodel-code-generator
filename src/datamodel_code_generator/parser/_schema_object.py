"""The JSON Schema object as the parser validates it from a document, and what it reads while it is validated.

``JsonSchemaObject`` holds the keywords of one schema object; its validators collect the extension keywords, read a
schema-valued ``unevaluatedProperties`` as ``additionalProperties`` under ``--read-unevaluated-properties`` and keep the
edges of the references it holds. Each object takes the location of the document object or located dump it is
validated from (the carrier of ``docs/architecture.md``, Metadata Output). ``parser/jsonschema.py`` re-exports the
public names.
"""

from __future__ import annotations

import enum as _enum
from functools import cached_property, lru_cache
from typing import TYPE_CHECKING, Any, ClassVar, Final, Optional, TypeVar, Union, cast

from pydantic import (
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import core_schema
from typing_extensions import override

from datamodel_code_generator import Error
from datamodel_code_generator._json_schema import DEFINITION_KEYS, schema_positions
from datamodel_code_generator.parser._null_facts import NullSources, resolve_null_facts
from datamodel_code_generator.parser._schema_graph import SCHEMA_GRAPH, canonical_ref_spelling, keep_resolved_ref
from datamodel_code_generator.parser.base import SPECIAL_PATH_FORMAT
from datamodel_code_generator.reference import SCHEMA_LOCATION_KEY, is_url
from datamodel_code_generator.types import FloatConstraint, UnionIntFloat  # noqa: TC001 - pydantic field types
from datamodel_code_generator.util import BaseModel

if TYPE_CHECKING:
    from collections.abc import Container, Mapping

    from pydantic import GetCoreSchemaHandler, ValidatorFunctionWrapHandler
    from pydantic_core import CoreSchema
    from typing_extensions import Self, TypeIs

    from datamodel_code_generator.parser._schema_graph import SchemaNodeId
    from datamodel_code_generator.reference import SchemaLocation

__all__ = [
    "DEFAULT_FIELD_KEYS",
    "EXCLUDE_FIELD_KEYS",
    "EXCLUDE_FIELD_KEYS_IN_JSON_SCHEMA",
    "READ_UNEVALUATED_PROPERTIES_CONTEXT",
    "Discriminator",
    "JSONReference",
    "JsonSchemaLiteral",
    "JsonSchemaObject",
    "get_ref_type",
]

JsonSchemaLiteral = Union[bool, int, str]  # noqa: UP007

_MODELED_POSITIONS = frozenset(schema_positions("modeled"))
"""The subschema keywords ``JsonSchemaObject`` holds as fields."""
_CONDITIONAL_SCHEMA_KEYWORDS = frozenset(schema_positions("conditional"))
_ANNOTATION_KEYWORDS = frozenset({
    "$comment",
    "default",
    "deprecated",
    "description",
    "example",
    "examples",
    "readOnly",
    "title",
    "writeOnly",
})
_FALSE_ALL_OF_KEYWORDS = _ANNOTATION_KEYWORDS | {"allOf"}
_COPIED_NULLABLE_KEY = "_copied_nullable"
_NULLABLE_TYPE_LIST_KEY = "_nullable_type_list"
_PYTHON_FIELD_OVERRIDES_KEY = "__python_field_overrides"
_READ_AS_CONST_KEY = "x-datamodel-code-generator-read-as-const"
_SchemaT = TypeVar("_SchemaT", bound="JsonSchemaObject")


class _LocatedDict(dict):  # noqa: FURB189 - pydantic validates it as the dict it is
    """The dump of a located schema object, which remembers the location of the object it was dumped from."""

    __slots__ = ("schema_location",)

    schema_location: SchemaLocation


def _located(data: dict[str, Any], location: SchemaLocation | None) -> dict[str, Any]:
    """Return a dumped schema object that remembers ``location``."""
    if location is None:
        return data
    result = _LocatedDict(data)
    result.schema_location = location
    return result


def _dump_location(data: object) -> SchemaLocation | None:
    """Return the location a dumped schema object remembers, if it is one."""
    return data.schema_location if isinstance(data, _LocatedDict) else None


def _source_location(values: object) -> SchemaLocation | None:
    """Return the location of the input a model is validated from: a located dump or a document object."""
    return (
        values.schema_location
        if isinstance(values, _LocatedDict)
        else None
        if (graph := SCHEMA_GRAPH.get()) is None
        else graph.locations.get(id(values))
    )


def _take_input_location(values: Any, handler: ValidatorFunctionWrapHandler) -> Any:
    """Validate the fields of a located model; the instance takes the location of the input they come from."""
    fields = handler(values)
    if (location := _source_location(values)) is not None:
        fields[0][SCHEMA_LOCATION_KEY] = location
    return fields


def _located_model_schema(source: Any, handler: GetCoreSchemaHandler) -> CoreSchema:
    """Return the core schema of a model whose instances take the location of the input they are validated from.

    The locating validator wraps the field validation inside the model schema, where pydantic's error locations do not
    name it, so they keep the model's name (a model validator in wrap mode would replace it there). Every schema that
    holds the model, the fields a subclass adds and ``TypeAdapter`` roots included, validates it this way.
    """
    schema = handler(source)
    model: Any = handler.resolve_ref_schema(schema)
    while model["type"] != "model":
        model = model["schema"]
    inner = model["schema"]
    if inner["type"] != "function-wrap" or inner["function"]["function"] is not _take_input_location:
        model["schema"] = core_schema.no_info_wrap_validator_function(_take_input_location, inner)
    return schema


def _locate(obj: _SchemaT, location: SchemaLocation | None) -> _SchemaT:
    """Give a schema object that a copy step created the location of the schema object it stands for, if it has one."""
    obj.__dict__[SCHEMA_LOCATION_KEY] = location
    return obj


def _dump_without_keywords(schema: JsonSchemaObject, keywords: Container[str]) -> dict[str, Any]:
    """Dump the keywords a schema object sets, leaving out ``keywords`` also among its extension keywords."""
    schema_dict = schema.model_dump(exclude_unset=True, by_alias=True)
    for key in [key for key in schema_dict if key in keywords]:
        del schema_dict[key]
    if isinstance(extras := schema_dict.get(JsonSchemaObject.__extra_key__), dict):
        if meaningful_extras := {key: value for key, value in extras.items() if key not in keywords}:
            schema_dict[JsonSchemaObject.__extra_key__] = meaningful_extras
        else:
            schema_dict.pop(JsonSchemaObject.__extra_key__, None)
    return schema_dict


def _is_false_all_of(schema: object) -> bool:
    """Return whether a schema is ``false`` written as an allOf that holds false, beside annotations and ``x-`` keys.

    The schema is a schema object or, before validation, the dict it is validated from.
    """
    match schema:
        case JsonSchemaObject() if any(item is False for item in schema.allOf):
            remaining = _dump_without_keywords(schema, _FALSE_ALL_OF_KEYWORDS)
            extras = remaining.pop(JsonSchemaObject.__extra_key__, {})
            return not any(not key.startswith("x-") for key in (*remaining, *extras))
        case {"allOf": list() as all_of} if any(item is False for item in all_of):
            plain_keys = {key for key in schema if not (isinstance(key, str) and key.startswith("x-"))}
            return plain_keys <= _FALSE_ALL_OF_KEYWORDS
    return False


def _locate_dump(obj: JsonSchemaObject, data: dict[str, Any]) -> dict[str, Any]:
    """Give the dump of a schema object, and the dumps of its schema children, the locations of their sources.

    The correspondence is the object's own structure (field, key, index), never a match across merge inputs.
    """
    fields = obj.__dict__
    for name in obj.model_fields_set.intersection(_MODELED_POSITIONS):
        value, dumped = fields[name], data.get(name)
        if isinstance(dumped, dict):
            if isinstance(value, JsonSchemaObject):
                data[name] = _locate_dump(value, dumped)
            else:
                data[name] = {
                    key: _locate_dump(item, sub)
                    if isinstance(item := value.get(key), JsonSchemaObject) and isinstance(sub, dict)
                    else sub
                    for key, sub in dumped.items()
                }
        elif isinstance(dumped, list):
            data[name] = [
                _locate_dump(item, sub) if isinstance(item, JsonSchemaObject) and isinstance(sub, dict) else sub
                for item, sub in zip(value, dumped, strict=True)
            ]
    return _located(data, fields.get(SCHEMA_LOCATION_KEY))


def _read_type(obj: JsonSchemaObject) -> str | list[str] | None:
    """Return the ``type`` of a schema object, as a list holding null where OpenAPI reads ``nullable`` so.

    ``JsonSchemaParser._get_data_type`` marks the schema objects whose ``nullable`` it reads as a ``type`` list
    (``parser/_null_facts.py``, row ``legacy-nullable-as-type-list``).
    """
    if obj.__dict__.get(_NULLABLE_TYPE_LIST_KEY) and isinstance(obj.type, str):
        return [obj.type, "null"]
    return obj.type


def _type_null_sources(obj: JsonSchemaObject) -> NullSources:
    """Return the null sources of a schema object without the answer ``JsonSchemaObject.type_has_null`` recorded."""
    type_ = _read_type(obj)
    is_list = isinstance(type_, list)
    return NullSources(
        type_null=type_ == "null",
        type_list=is_list,
        type_lists_null=is_list and "null" in type_,
        type_lists_object=is_list and "object" in type_,
        typed=type_ is not None,
        type_string=type_ == "string",
        member_null=any(
            isinstance(item, JsonSchemaObject)
            and (item.type == "null" or (isinstance(item.type, list) and "null" in item.type))
            for item in (*obj.oneOf, *obj.anyOf)
        ),
        nullable=obj.nullable,
        copied_nullable=bool(obj.__dict__.get(_COPIED_NULLABLE_KEY)),
        enum_null=None in obj.enum,
    )


class JSONReference(_enum.Enum):
    """Define types of JSON references."""

    LOCAL = "LOCAL"
    REMOTE = "REMOTE"
    URL = "URL"


class Discriminator(BaseModel):
    """Represent OpenAPI discriminator object.

    This is an OpenAPI-specific concept for supporting polymorphism.
    It identifies which schema applies based on a property value.
    Kept beside ``JsonSchemaObject``, which holds it, to avoid circular imports with openapi.py.
    """

    propertyName: str  # noqa: N815
    mapping: Optional[dict[str, str]] = None  # noqa: UP045


_REF_KEYWORDS: Final = ("$ref", "$dynamicRef", "$recursiveRef")
_IN_PLACE_APPLICATOR_KEYWORDS: Final = frozenset({
    *schema_positions("in-place", without=("then", "else", "extends")),
    *_REF_KEYWORDS,
})
"""The keywords that apply a schema to the instance itself; ``then`` and ``else`` apply only through ``if``."""
READ_UNEVALUATED_PROPERTIES_CONTEXT: Final = "read_unevaluated_properties"


_PROPERTY_EVALUATING_KEYWORDS: Final = frozenset({
    *_IN_PLACE_APPLICATOR_KEYWORDS,
    *_CONDITIONAL_SCHEMA_KEYWORDS,
    *schema_positions("object", without=("propertyNames",)),
})


def _is_document_object(values: object) -> TypeIs[dict[str, Any]]:
    """Return whether ``values`` is a JSON object of a document the running parse loaded, not a dump or a merge."""
    return isinstance(values, dict) and (graph := SCHEMA_GRAPH.get()) is not None and id(values) in graph.locations


def _evaluates_no_property(schema: object) -> bool:
    """Return whether a raw subschema evaluates no property of the object it applies to."""
    return isinstance(schema, bool) or (isinstance(schema, dict) and _PROPERTY_EVALUATING_KEYWORDS.isdisjoint(schema))


def _applicator_evaluates_no_property(keyword: str, value: object) -> bool:
    """Return whether the in-place applicator ``keyword`` with its raw ``value`` evaluates no property.

    ``not`` never evaluates a property: the annotations of a failed subschema do not count. Only ``allOf``,
    ``anyOf`` and ``oneOf`` whose subschemas evaluate none qualify besides; any other applicator may.
    """
    match keyword:
        case "allOf" | "anyOf" | "oneOf":
            return isinstance(value, list) and all(_evaluates_no_property(item) for item in value)
        case "not":
            return True
    return False


class JsonSchemaObject(BaseModel):  # noqa: PLR0904
    """Represent a JSON Schema object with validation and parsing capabilities."""

    if not TYPE_CHECKING:  # pragma: no branch

        @classmethod
        def get_fields(cls) -> dict[str, Any]:
            """Get fields for Pydantic v2 models."""
            return cls.model_fields

    __constraint_field_order__: ClassVar[tuple[str, ...]] = (
        "exclusiveMinimum",
        "minimum",
        "exclusiveMaximum",
        "maximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "pattern",
        "uniqueItems",
    )
    __constraint_fields__: set[str] = set(__constraint_field_order__)  # noqa: RUF012
    __extra_key__: str = SPECIAL_PATH_FORMAT.format("extras")
    __metadata_only_fields__: set[str] = {  # noqa: RUF012
        "title",
        "description",
        "id",
        "$id",
        "$anchor",
        "$schema",
        "$comment",
        "examples",
        "example",
        "x_enum_varnames",
        "x_enum_descriptions",
        "x_enum_field_as_literal",
        "read_as_const",
        *DEFINITION_KEYS,
        "default",
        "readOnly",
        "writeOnly",
        "deprecated",
        "contentEncoding",
        "contentMediaType",
        "contentSchema",
        "externalDocs",
        "xml",
        "$recursiveRef",
        "recursiveRef",
        "$recursiveAnchor",
        "recursiveAnchor",
        "$dynamicRef",
        "dynamicRef",
        "$dynamicAnchor",
        "dynamicAnchor",
    }

    __schema_affecting_extras__: set[str] = {  # noqa: RUF012
        "const",
    }

    @model_validator(mode="before")
    @classmethod
    def validate_exclusive_maximum_and_exclusive_minimum(cls, values: Any) -> Any:
        """Validate and convert boolean exclusive maximum and minimum to numeric values."""
        if not isinstance(values, dict):
            return values
        if not isinstance(values.get("exclusiveMaximum"), bool) and not isinstance(
            values.get("exclusiveMinimum"), bool
        ):
            return values
        values = dict(values)
        for exclusive, bound in (("exclusiveMaximum", "maximum"), ("exclusiveMinimum", "minimum")):
            match values.get(exclusive):
                case True if bound in values:
                    values[exclusive] = values.pop(bound)
                case True | False:
                    del values[exclusive]
        return values

    @model_validator(mode="before")
    @classmethod
    def collect_extra_fields(cls, values: Any) -> Any:
        """Collect raw schema extension fields without overriding known schema fields."""
        if not isinstance(values, dict):
            return values
        alias_extras = values.get(cls.__extra_key__, {})
        raw_extras = {k: v for k, v in values.items() if k not in EXCLUDE_FIELD_KEYS}
        if (overrides := getattr(values.get("allOf"), "_python_field_overrides", None)) is not None:
            raw_extras[_PYTHON_FIELD_OVERRIDES_KEY] = overrides
        if not alias_extras and not raw_extras:
            return values
        extras = {**alias_extras, **raw_extras}
        if "const" in alias_extras:  # pragma: no cover
            extras["const"] = alias_extras["const"]
        return {**values, cls.__extra_key__: extras}

    @model_validator(mode="before")
    @classmethod
    def read_unevaluated_properties_as_additional(cls, values: Any, info: ValidationInfo) -> Any:
        """Read a schema-valued ``unevaluatedProperties`` as ``additionalProperties`` when nothing applies in place.

        Only under ``--read-unevaluated-properties``, which the parser passes in the validation context; nested schema
        objects are validated with the same context. The schema object of the document decides, as written: an
        applicator that evaluates no property leaves the keys ``unevaluatedProperties`` sees those of
        ``additionalProperties``. A dump or a merge the parser validates again keeps that decision, also where it
        drops the applicators. Defined last, it runs before the other validators and sees the document object. A
        value that is ``false`` written as an allOf (``_is_false_all_of``), of a schema object without properties, is
        read as ``false``, which stays where it is.
        """
        if (
            not (info.context or {}).get(READ_UNEVALUATED_PROPERTIES_CONTEXT)
            or not _is_document_object(values)
            or not isinstance(unevaluated_properties := values.get("unevaluatedProperties"), dict)
            or "additionalProperties" in values
            or not all(
                _applicator_evaluates_no_property(keyword, values[keyword])
                for keyword in _IN_PLACE_APPLICATOR_KEYWORDS.intersection(values)
            )
        ):
            return values
        if not values.get("properties") and _is_false_all_of(unevaluated_properties):
            return {**values, "unevaluatedProperties": False}
        return {**values, "additionalProperties": unevaluated_properties}

    @property
    def python_field_overrides(self) -> Mapping[str, str]:
        """Read only converter-owned replacement metadata."""
        if (annotation := self.extras.get(_PYTHON_FIELD_OVERRIDES_KEY)) is None:
            return {}
        from datamodel_code_generator.input_model_result import PythonFieldOverrides  # noqa: PLC0415

        return annotation.fields if isinstance(annotation, PythonFieldOverrides) else {}

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: Any) -> Any:
        """Validate and normalize $ref values (``canonical_ref_spelling``)."""
        return canonical_ref_spelling(value) if isinstance(value, str) else value

    @field_validator("ref", "dynamicRef", mode="wrap")
    @classmethod
    def _keep_resolved_ref(cls, value: Any, handler: ValidatorFunctionWrapHandler) -> Any:
        """Keep the edge of a reference keyword of a loaded document, also when validation normalizes its value."""
        return keep_resolved_ref(value, handler(value))

    @field_validator("required", mode="before")
    @classmethod
    def validate_required(cls, value: Any) -> Any:
        """Validate and normalize required field values."""
        if value is None:
            return []
        if isinstance(value, list):  # pragma: no branch  # noqa: PLR1702
            # Filter to only include valid strings, excluding invalid objects
            required_fields: list[str] = []
            for item in value:
                if isinstance(item, str):
                    required_fields.append(item)

                # In some cases, the required field can include "anyOf", "oneOf", or "allOf" as a dict (#2297)
                elif isinstance(item, dict):  # pragma: no branch
                    for key, val in item.items():
                        if isinstance(val, list):  # pragma: no branch
                            # If 'anyOf' or "oneOf" is present, we won't include it in required fields
                            if key in {"anyOf", "oneOf"}:
                                continue

                            if key == "allOf":  # pragma: no branch
                                # If 'allOf' is present, we include them as required fields
                                required_fields.extend(sub_item for sub_item in val if isinstance(sub_item, str))

            value = required_fields

        return value

    @field_validator("type", mode="before")
    @classmethod
    def validate_null_type(cls, value: Any) -> Any:
        """Validate and convert unquoted null type to string "null"."""
        # TODO[openapi]: This should be supported only for OpenAPI 3.1+
        # See: https://github.com/koxudaxi/datamodel-code-generator/issues/2477#issuecomment-3192480591
        if value is None:
            value = "null"
        if isinstance(value, list) and None in value:
            value = [v if v is not None else "null" for v in value]
        return value

    items: Optional[Union[list[Union[JsonSchemaObject, bool]], JsonSchemaObject, bool]] = None  # noqa: UP007, UP045
    additionalItems: Optional[Union[JsonSchemaObject, bool]] = None  # noqa: N815, UP007, UP045
    prefixItems: Optional[list[Union[JsonSchemaObject, bool]]] = None  # noqa: N815, UP007, UP045
    uniqueItems: Optional[bool] = None  # noqa: N815, UP045
    type: Optional[Union[str, list[str]]] = None  # noqa: UP007, UP045
    format: Optional[str] = None  # noqa: UP045
    pattern: Optional[str] = None  # noqa: UP045
    minLength: Optional[int] = None  # noqa:  N815,UP045
    maxLength: Optional[int] = None  # noqa:  N815,UP045
    minimum: Optional[UnionIntFloat] = None  # noqa:  UP045
    maximum: Optional[UnionIntFloat] = None  # noqa:  UP045
    minItems: Optional[int] = None  # noqa:  N815,UP045
    maxItems: Optional[int] = None  # noqa:  N815,UP045
    minProperties: Optional[int] = None  # noqa: N815, UP045
    maxProperties: Optional[int] = None  # noqa: N815, UP045
    multipleOf: Optional[FloatConstraint] = None  # noqa: N815, UP045
    exclusiveMaximum: Optional[Union[FloatConstraint, bool]] = None  # noqa: N815, UP007, UP045
    exclusiveMinimum: Optional[Union[FloatConstraint, bool]] = None  # noqa: N815, UP007, UP045
    additionalProperties: Optional[Union[JsonSchemaObject, bool]] = None  # noqa: N815, UP007, UP045
    unevaluatedProperties: Optional[Union[JsonSchemaObject, bool]] = None  # noqa: N815, UP007, UP045
    unevaluatedItems: Optional[Union[JsonSchemaObject, bool]] = None  # noqa: N815, UP007, UP045
    patternProperties: Optional[dict[str, Union[JsonSchemaObject, bool]]] = None  # noqa: N815, UP007, UP045
    propertyNames: Optional[Union[JsonSchemaObject, bool]] = None  # noqa: N815, UP007, UP045
    oneOf: list[Union[JsonSchemaObject, bool]] = Field(default_factory=list)  # noqa: N815, UP007
    anyOf: list[Union[JsonSchemaObject, bool]] = Field(default_factory=list)  # noqa: N815, UP007
    allOf: list[Union[JsonSchemaObject, bool]] = Field(default_factory=list)  # noqa: N815, UP007
    enum: list[Any] = Field(default_factory=list)
    writeOnly: Optional[bool] = None  # noqa: N815, UP045
    readOnly: Optional[bool] = None  # noqa: N815, UP045
    properties: Optional[dict[str, Union[JsonSchemaObject, bool]]] = None  # noqa: UP007, UP045
    required: list[str] = Field(default_factory=list)
    ref: Optional[str] = Field(default=None, alias="$ref")  # noqa: UP045
    recursiveRef: Optional[str] = Field(default=None, alias="$recursiveRef")  # noqa: N815, UP045
    recursiveAnchor: Optional[bool] = Field(default=None, alias="$recursiveAnchor")  # noqa: N815, UP045
    dynamicRef: Optional[str] = Field(default=None, alias="$dynamicRef")  # noqa: N815, UP045
    dynamicAnchor: Optional[str] = Field(default=None, alias="$dynamicAnchor")  # noqa: N815, UP045
    nullable: Optional[bool] = None  # noqa: UP045
    x_enum_varnames: list[str | None] = Field(default_factory=list, alias="x-enum-varnames")
    x_enum_descriptions: list[str | None] = Field(default_factory=list, alias="x-enum-descriptions")
    x_enum_names: list[str | None] = Field(default_factory=list, alias="x-enumNames")
    x_enum_field_as_literal: Optional[bool] = Field(default=None, alias="x-enum-field-as-literal")  # noqa: UP045
    description: Optional[str] = None  # noqa: UP045
    title: Optional[str] = None  # noqa: UP045
    example: Any = None
    examples: Any = None
    default: Any = None
    id: Optional[str] = Field(default=None, alias="$id")  # noqa: UP045
    anchor: Optional[str] = Field(default=None, alias="$anchor")  # noqa: UP045
    custom_type_path: Optional[str] = Field(default=None, alias="customTypePath")  # noqa: UP045
    custom_base_path: str | list[str] | None = Field(default=None, alias="customBasePath")
    is_boolean_schema_false: bool = Field(default=False, exclude=True)
    read_as_const: bool = Field(default=False, alias=_READ_AS_CONST_KEY)
    extras: dict[str, Any] = Field(alias=__extra_key__, default_factory=dict)
    discriminator: Optional[Union[Discriminator, str]] = None  # noqa: UP007, UP045
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        ignored_types=(cached_property,),
        defer_build=True,
    )

    @classmethod
    @override
    def __get_pydantic_core_schema__(cls, source: Any, handler: GetCoreSchemaHandler, /) -> CoreSchema:
        """Validate schema objects so that each takes the location of the document object or located dump it is from."""
        return _located_model_schema(source, handler)

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """Dump the object; during a parse, each dumped schema object remembers the location of its source."""
        data = super().model_dump(**kwargs)
        if SCHEMA_GRAPH.get() is None or kwargs.get("mode", "python") != "python":
            return data
        return _locate_dump(self, data)

    @property
    def schema_location(self) -> SchemaLocation | None:
        """Return the location of the schema object this object stands for."""
        return self.__dict__.get(SCHEMA_LOCATION_KEY)

    def with_schema_location(self, location: SchemaLocation | None) -> Self:
        """Return a shallow copy that stands for the schema object at ``location``; this object is never written."""
        if location is None or self.__dict__.get(SCHEMA_LOCATION_KEY) == location:
            return self
        copied = self.model_copy()
        copied.__dict__[SCHEMA_LOCATION_KEY] = location
        return copied

    def model_post_init(self, __context: Any, /) -> None:
        """Apply post-validation compatibility handling for extension metadata."""
        # Support x-propertyNames extension for OpenAPI 3.0
        if "x-propertyNames" in self.extras and self.propertyNames is None:
            x_prop_names = self.extras.pop("x-propertyNames")
            if isinstance(x_prop_names, bool):
                self.propertyNames = x_prop_names
            elif isinstance(x_prop_names, dict):
                self.propertyNames = JsonSchemaObject.model_validate(x_prop_names, context=__context)

    @cached_property
    def is_object(self) -> bool:
        """Check if the schema represents an object type."""
        return self.properties is not None or (
            self.type == "object" and not self.allOf and not self.oneOf and not self.anyOf and not self.ref
        )

    @cached_property
    def is_array(self) -> bool:
        """Check if the schema represents an array type."""
        return self.items is not None or self.prefixItems is not None or self.type == "array"

    @cached_property
    def ref_object_name(self) -> str:  # pragma: no cover
        """Extract the object name from the reference path."""
        return (self.ref or "").rsplit("/", 1)[-1]

    @field_validator("items", mode="before")
    @classmethod
    def validate_items(cls, values: Any) -> Any:
        """Validate items field, converting empty dicts to None."""
        # this condition expects empty dict
        return None if values == {} else values

    @field_validator("custom_base_path", mode="before")
    @classmethod
    def validate_custom_base_path(cls, value: Any) -> Any:
        """Validate schema-controlled custom base class import paths."""
        match value:
            case None:
                return None
            case list():
                for item in value:
                    _validate_schema_python_import_path(item, "customBasePath")
            case _:
                _validate_schema_python_import_path(value, "customBasePath")
        return value

    @cached_property
    def has_default(self) -> bool:
        """Check if the schema has a default value or default factory."""
        return "default" in self.model_fields_set or "default_factory" in self.extras

    @cached_property
    def has_constraint(self) -> bool:
        """Check if the schema has any constraint fields set."""
        return bool(self.__constraint_fields__ & self.model_fields_set)

    @cached_property
    def ref_type(self) -> JSONReference | None:
        """Get the reference type (LOCAL, REMOTE, or URL)."""
        if self.ref:
            return get_ref_type(self.ref)
        return None

    @cached_property
    def type_has_null(self) -> bool:
        """Check if the type list or oneOf/anyOf contains null.

        A ``type`` that excludes null does not drop the null of its members here: that is
        ``JsonSchemaParser._type_has_null``, with --intersect-allof-keywords.
        """
        return bool(resolve_null_facts(_type_null_sources(self), "schema-type").admits_null)

    @cached_property
    def has_multiple_types(self) -> bool:
        """Check if the type is a list with multiple non-null types."""
        if not isinstance(self.type, list):
            return False
        non_null_types = [t for t in self.type if t != "null"]
        return len(non_null_types) > 1

    @cached_property
    def has_ref_with_schema_keywords(self) -> bool:
        """Check if schema has $ref combined with schema-affecting keywords.

        Metadata-only keywords (title, description, etc.) are excluded
        as they don't affect the schema structure. OpenAPI/JSON Schema
        extension fields (x-*) are also excluded as they are vendor
        extensions and don't affect the core schema structure.
        """
        return bool(self.ref) and self._writes_schema_keywords_besides({"ref"})

    @cached_property
    def is_ref_with_nullable_only(self) -> bool:
        """Check if schema has $ref with only nullable: true (no other schema-affecting keywords).

        This is used to avoid creating duplicate models when a $ref is combined
        with nullable: true. In such cases, the reference should be used directly
        with Optional type annotation instead of merging schemas.
        """
        return (
            bool(self.ref) and self.nullable is True and not self._writes_schema_keywords_besides({"ref", "nullable"})
        )

    def _writes_schema_keywords_besides(self, ignored: set[str]) -> bool:
        """Return whether the schema writes a keyword that affects it besides ``ignored`` and the metadata ones."""
        return bool(self.model_fields_set - ignored - self.__metadata_only_fields__ - {"extras"}) or any(
            key in self.__schema_affecting_extras__ for key in self.extras
        )


@lru_cache
def get_ref_type(ref: str) -> JSONReference:
    """Determine the type of reference (LOCAL, REMOTE, or URL)."""
    if ref[0] == "#":
        return JSONReference.LOCAL
    if is_url(ref):
        return JSONReference.URL
    return JSONReference.REMOTE


JsonSchemaObject.model_rebuild()

DEFAULT_FIELD_KEYS: set[str] = {
    "example",
    "examples",
    "description",
    "discriminator",
    "title",
    "const",
    "default_factory",
    "deprecated",
    "contentEncoding",
    "contentMediaType",
    "contentSchema",
    "externalDocs",
    "xml",
}


def _validate_schema_python_import_path(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        msg = f"{field_name} must be a dotted Python identifier path: {value!r}"
        raise Error(msg)
    from datamodel_code_generator.validators import _validate_dotted_python_identifier_path  # noqa: PLC0415

    try:
        return _validate_dotted_python_identifier_path(value)
    except ValueError as exc:
        msg = f"{field_name} {exc}"
        raise Error(msg) from None


EXCLUDE_FIELD_KEYS_IN_JSON_SCHEMA: set[str] = {
    "readOnly",
    "writeOnly",
}

EXCLUDE_FIELD_KEYS = (set(JsonSchemaObject.get_fields()) - DEFAULT_FIELD_KEYS - EXCLUDE_FIELD_KEYS_IN_JSON_SCHEMA) | {
    "$id",
    "$ref",
    "$recursiveRef",
    "$recursiveAnchor",
    "$dynamicRef",
    "$dynamicAnchor",
    "self",
    _READ_AS_CONST_KEY,
    JsonSchemaObject.__extra_key__,
}


def _named_path(location: SchemaLocation | None, path: list[str]) -> list[str]:
    """Return the path a message names: the schema location, or the parse path when the location is unknown."""
    return path if location is None else [str(location)]


def _schema_node(schema: JsonSchemaObject, resolved_ref: str) -> SchemaNodeId | str:
    """Return the schema graph node a loaded schema object stands for, or its key when it has no location."""
    return resolved_ref if (location := schema.schema_location) is None else cast("SchemaNodeId", location)
