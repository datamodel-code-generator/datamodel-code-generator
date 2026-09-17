"""Structured runtime validation rules derived from source schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

if TYPE_CHECKING:
    from datamodel_code_generator.types import DataType

InputNames: TypeAlias = tuple[str, ...]
InputNameGroups: TypeAlias = tuple[InputNames, ...]
RequiredGroup: TypeAlias = tuple[InputNames, ...]
RequiredGroups: TypeAlias = tuple[RequiredGroup, ...]
Condition: TypeAlias = tuple[tuple[InputNames, tuple[object, ...]], ...]
UNIQUE_ITEMS_MAPPING_VALUES_PATH_STEP: Final = "__json_schema_mapping_values__"
UNIQUE_ITEMS_ARRAY_TAIL_PATH_STEP: Final = "__json_schema_array_tail__"
UNIQUE_ITEMS_MAPPING_PATTERN_VALUES_PATH_STEP: Final = "__json_schema_mapping_pattern_values__"
UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP: Final = "__json_schema_mapping_additional_values__"
UniqueItemsArrayTailPathStep: TypeAlias = tuple[Literal["__json_schema_array_tail__"], int]
UniqueItemsMappingPatternPathStep: TypeAlias = tuple[Literal["__json_schema_mapping_pattern_values__"], str, None]
UniqueItemsMappingAdditionalPathStep: TypeAlias = tuple[
    Literal["__json_schema_mapping_additional_values__"], InputNameGroups, tuple[str, ...]
]
UniqueItemsPathStep: TypeAlias = (
    InputNames
    | UniqueItemsArrayTailPathStep
    | UniqueItemsMappingPatternPathStep
    | UniqueItemsMappingAdditionalPathStep
    | int
    | Literal["__json_schema_mapping_values__"]
    | None
)
UniqueItemsPath: TypeAlias = tuple[UniqueItemsPathStep, ...]
_INTERNAL_SCHEMA_RUNTIME_VALIDATION_TOKEN = object()
_INTERNAL_SCHEMA_RUNTIME_VALIDATION_ERROR = "internal schema runtime validation must be created by the parser"


@dataclass(frozen=True)
class PatternPropertiesRule:
    """Runtime rule for JSON Schema patternProperties."""

    declared_properties: tuple[str, ...]
    pattern_properties: tuple[tuple[str, DataType], ...]
    rejected_patterns: tuple[str, ...] = ()
    additional_property_type: DataType | None = None
    allow_unmatched: bool = True

    @property
    def data_types(self) -> tuple[DataType, ...]:
        """Return all generated data types referenced by this rule."""
        data_types = tuple(data_type for _, data_type in self.pattern_properties)
        if self.additional_property_type is None:
            return data_types
        return (*data_types, self.additional_property_type)


class IndependentDeclaredPatternPropertiesRule(PatternPropertiesRule):
    """Pattern validation must preserve the input for a declared field."""


class IndependentModelPatternPropertiesRule(PatternPropertiesRule):
    """Distinct pattern models must validate the same original input."""


@dataclass(frozen=True)
class RequiredGroupsRule:
    """Runtime rule for required-property oneOf/anyOf groups and not-required groups."""

    keyword: Literal["anyOf", "oneOf", "not"]
    groups: RequiredGroups


@dataclass(frozen=True)
class ConditionalRequiredRule:
    """Runtime rule for if/then/else required-property conditions."""

    condition: Condition
    then_groups: RequiredGroups
    else_groups: RequiredGroups


def conditional_value_uses_json_equality(value: object) -> bool:
    """Identify condition literals for which Python conflates booleans and numbers."""
    match value:
        case bool():
            return True
        case int() | float():
            return value in {0, 1}
        case list() as array:
            return any(conditional_value_uses_json_equality(item) for item in array)
        case dict() as object_:
            return any(conditional_value_uses_json_equality(item) for item in object_.values())
    return False


@dataclass(frozen=True, slots=True)
class PropertyCountRule:
    """Runtime rule for JSON Schema object property-count bounds."""

    min_properties: int | None = None
    max_properties: int | None = None


@dataclass(frozen=True, slots=True)
class UniqueItemsRule:
    """Runtime rule for an array's JSON Schema ``uniqueItems`` constraint."""

    path: UniqueItemsPath


def unique_items_path_uses_regex(path: UniqueItemsPath) -> bool:
    """Return whether a compact uniqueItems path needs regex key selection."""
    for step in path:
        match step:
            case (marker, str(), None) if marker == UNIQUE_ITEMS_MAPPING_PATTERN_VALUES_PATH_STEP:
                return True
            case (marker, tuple(), tuple()) if marker == UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP:
                return True
    return False


@dataclass
class SchemaRuntimeValidation:
    """Schema-derived runtime validation rules for a generated model."""

    pattern_properties: list[PatternPropertiesRule] = field(default_factory=list)
    required_groups: list[RequiredGroupsRule] = field(default_factory=list)
    conditional_required: list[ConditionalRequiredRule] = field(default_factory=list)
    property_count: PropertyCountRule | None = None
    unique_items: list[UniqueItemsRule] = field(default_factory=list)
    replace_unique_items: bool = False

    def __bool__(self) -> bool:
        """Return whether any runtime validation rule is registered."""
        return bool(
            self.pattern_properties
            or self.required_groups
            or self.conditional_required
            or self.property_count
            or self.unique_items
            or self.replace_unique_items
        )

    @property
    def data_types(self) -> tuple[DataType, ...]:
        """Return all generated data types referenced by runtime rules."""
        return tuple(data_type for rule in self.pattern_properties for data_type in rule.data_types)


class _InternalSchemaRuntimeValidation(SchemaRuntimeValidation):
    """A parser-owned runtime-validation value safe for built-in templates."""

    __slots__ = ()

    def __init__(  # noqa: PLR0913
        self,
        token: object,
        *,
        pattern_properties: list[PatternPropertiesRule] | None = None,
        required_groups: list[RequiredGroupsRule] | None = None,
        conditional_required: list[ConditionalRequiredRule] | None = None,
        property_count: PropertyCountRule | None = None,
        unique_items: list[UniqueItemsRule] | None = None,
    ) -> None:
        if token is not _INTERNAL_SCHEMA_RUNTIME_VALIDATION_TOKEN:
            raise TypeError(_INTERNAL_SCHEMA_RUNTIME_VALIDATION_ERROR)
        super().__init__(
            pattern_properties=[] if pattern_properties is None else pattern_properties,
            required_groups=[] if required_groups is None else required_groups,
            conditional_required=[] if conditional_required is None else conditional_required,
            property_count=property_count,
            unique_items=[] if unique_items is None else unique_items,
        )


def _make_internal_schema_runtime_validation(
    *,
    pattern_properties: list[PatternPropertiesRule] | None = None,
    required_groups: list[RequiredGroupsRule] | None = None,
    conditional_required: list[ConditionalRequiredRule] | None = None,
    property_count: PropertyCountRule | None = None,
    unique_items: list[UniqueItemsRule] | None = None,
) -> SchemaRuntimeValidation:
    """Create parser-owned runtime validation metadata for built-in rendering."""
    return _InternalSchemaRuntimeValidation(
        _INTERNAL_SCHEMA_RUNTIME_VALIDATION_TOKEN,
        pattern_properties=pattern_properties,
        required_groups=required_groups,
        conditional_required=conditional_required,
        property_count=property_count,
        unique_items=unique_items,
    )


def _is_internal_schema_runtime_validation(value: object) -> bool:
    """Return whether a value was created by the parser-owned factory."""
    return isinstance(value, _InternalSchemaRuntimeValidation)
