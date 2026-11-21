"""Structured runtime validation rules derived from source schemas."""

from __future__ import annotations

from copy import copy
from dataclasses import field
from functools import partial
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, TypeAlias, cast

from typing_extensions import Sentinel

from datamodel_code_generator._python_syntax import holds_lone_surrogate
from datamodel_code_generator._records import mutable_record, record
from datamodel_code_generator._regex_syntax import pattern_engine, selects_python_engine
from datamodel_code_generator._requirements import pydantic_target_meets
from datamodel_code_generator.python_literal import stable_repr

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from datamodel_code_generator._regex_syntax import PatternEngine
    from datamodel_code_generator.enums import TargetPydanticVersion
    from datamodel_code_generator.types import DataType


class _AliasModel(Protocol):
    """The members of a type-alias model (``IS_ALIAS``) that its pattern engine depends on."""

    extra_template_data: dict[str, Any]
    fields: list[Any]


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
_INTERNAL_SCHEMA_RUNTIME_VALIDATION_TOKEN = Sentinel("_INTERNAL_SCHEMA_RUNTIME_VALIDATION_TOKEN")
_INTERNAL_SCHEMA_RUNTIME_VALIDATION_ERROR = "internal schema runtime validation must be created by the parser"


@record(slots=False)
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

    @property
    def python_regex_types(self) -> tuple[int, ...]:
        """Return the positions in ``data_types`` whose patterns need the ``python-re`` engine of an adapter.

        A pattern of a type the rule names is checked by the adapter the helper builds for it, which has no model
        config; the adapter row of ``pattern_engine`` decides there (a model's row keeps the default engine for a
        verbose pattern that Rust accepts), over the patterns of the type (``selects_python_engine``). A type alias has
        no config either, so the string patterns of the aliases a type names count as its own.
        """
        return tuple(
            index
            for index, data_type in enumerate(self.data_types)
            if selects_python_engine(_adapter_pattern_engines(data_type, set()))
        )


def alias_keeps_string_pattern(pattern: str, target: TargetPydanticVersion | None) -> bool:
    """Return whether a type alias keeps ``pattern`` a string, so that the models that use the alias select its engine.

    A target that predates compiled patterns keeps every pattern a string, and no target compiles one that holds a
    lone surrogate (``holds_lone_surrogate``); a later target compiles every other pattern that needs Python's engine.
    """
    return not pydantic_target_meets("pydantic.compiled_pattern", target) or holds_lone_surrogate(pattern)


def _counts_every_pattern(_pattern: str) -> bool:
    """Count every pattern: those outside the body of a type alias."""
    return True


def _counted_engine(engine: PatternEngine | None, pattern: str, counts: Callable[[str], bool]) -> PatternEngine | None:
    """Return ``engine``, what a string pattern asks of the config or adapter that builds it, where it counts.

    A pattern ``counts`` leaves out asks for no engine, but still runs on the engine of that config or adapter: one
    that the default engine runs as written counts.
    """
    return engine if engine is not None and (counts(pattern) or engine == "default") else None


def alias_pattern_counter(alias: _AliasModel) -> Callable[[str], bool]:
    """Return the test that counts the string patterns a type alias keeps strings (``alias_keeps_string_pattern``)."""
    return partial(alias_keeps_string_pattern, target=alias.extra_template_data.get("target_pydantic_version"))


def _adapter_pattern_engines(
    data_type: DataType, visited: set[int], counts: Callable[[str], bool] = _counts_every_pattern
) -> Iterator[PatternEngine | None]:
    """Yield what each pattern of ``data_type``, and each string pattern of an alias it names, asks of ``python-re``.

    ``counts`` selects the patterns that ask for an engine: those an alias keeps strings
    (``alias_keeps_string_pattern``) inside the body of an alias (``_counted_engine``).
    """
    for nested in data_type.all_data_types:
        if isinstance(pattern := (nested.kwargs or {}).get("pattern"), str):
            yield _counted_engine(pattern_engine(pattern, row="adapter"), pattern, counts)
        if (
            (reference := nested.reference) is None
            or not getattr(source := reference.source, "IS_ALIAS", False)
            or id(source) in visited
        ):
            continue
        visited.add(id(source))
        alias = cast("_AliasModel", source)
        kept = alias_pattern_counter(alias)
        for alias_field in alias.fields:
            if isinstance(pattern := getattr(alias_field.constraints, "pattern", None), str):
                yield _counted_engine(pattern_engine(pattern, row="adapter"), pattern, kept)
            yield from _adapter_pattern_engines(alias_field.data_type, visited, kept)


class IndependentDeclaredPatternPropertiesRule(PatternPropertiesRule):
    """Pattern validation must preserve the input for a declared field."""


class IndependentModelPatternPropertiesRule(PatternPropertiesRule):
    """Distinct pattern models must validate the same original input."""


@record(slots=False)
class RequiredGroupsRule:
    """Runtime rule for required-property oneOf/anyOf groups and not-required groups."""

    keyword: Literal["anyOf", "oneOf", "not"]
    groups: RequiredGroups


@record(slots=False)
class ConditionalRequiredRule:
    """Runtime rule for if/then/else required-property conditions."""

    condition: Condition
    then_groups: RequiredGroups
    else_groups: RequiredGroups


BoundKeyword: TypeAlias = Literal["minimum", "exclusiveMinimum", "maximum", "exclusiveMaximum"]
ConditionalBounds: TypeAlias = tuple[tuple[InputNames, BoundKeyword, int | float], ...]


@record(slots=False)
class ConditionalBoundsRule:
    """Runtime rule for the numeric bounds the ``then`` and ``else`` of an if/then/else put on properties.

    Each bound is (input names, keyword, limit); the condition is that of ``ConditionalRequiredRule``.
    """

    condition: Condition
    then_bounds: ConditionalBounds
    else_bounds: ConditionalBounds


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


@record
class PropertyCountRule:
    """Runtime rule for JSON Schema object property-count bounds."""

    min_properties: int | None = None
    max_properties: int | None = None


@record
class UniqueItemsRule:
    """Runtime rule for an array's JSON Schema ``uniqueItems`` constraint."""

    path: UniqueItemsPath


@record
class ConstantTypesRule:
    """Runtime rule that rejects a JSON boolean for a numeric const or enum value equal to it, and the reverse.

    ``values`` are the bool and number values of the const or enum that Python's ``True == 1`` conflates. ``booleans``
    marks the booleans among them, so that a rule over ``(1,)`` never equals or hashes as one over ``(True,)``.
    """

    path: UniqueItemsPath
    values: tuple[bool | int | float, ...]
    booleans: tuple[bool, ...] = field(init=False)

    def __post_init__(self) -> None:
        """Derive ``booleans`` from ``values``."""
        object.__setattr__(self, "booleans", tuple(isinstance(value, bool) for value in self.values))


ContainsMatcher: TypeAlias = tuple[tuple[str, object], ...]
CONTAINS_EQUALITY_KEYWORDS: Final = frozenset({"const", "enum"})


@record
class ContainsRule:
    """Runtime rule that counts the items of the array at ``path`` that ``matcher`` accepts.

    ``matcher`` holds the keywords of the ``contains`` schema as (keyword, value) pairs; the count must be at least
    ``min_contains`` and at most ``max_contains`` where each is set. ``key`` writes the matcher, so that a rule whose
    const is ``1`` never equals or hashes as one whose const is ``true``.
    """

    path: UniqueItemsPath
    matcher: ContainsMatcher = field(compare=False)
    min_contains: int | None
    max_contains: int | None
    key: str = field(init=False)

    def __post_init__(self) -> None:
        """Derive ``key`` from ``matcher``."""
        object.__setattr__(self, "key", stable_repr(self.matcher))

    @property
    def uses_json_equality(self) -> bool:
        """Return whether the matcher compares items with JSON values (``const``, ``enum``)."""
        return any(keyword in CONTAINS_EQUALITY_KEYWORDS for keyword, _ in self.matcher)


def unique_items_path_uses_regex(path: UniqueItemsPath) -> bool:
    """Return whether a compact uniqueItems path needs regex key selection."""
    for step in path:
        match step:
            case (marker, str(), None) if marker == UNIQUE_ITEMS_MAPPING_PATTERN_VALUES_PATH_STEP:
                return True
            case (marker, tuple(), tuple()) if marker == UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP:
                return True
    return False


@mutable_record
class SchemaRuntimeValidation:
    """Schema-derived runtime validation rules for a generated model."""

    pattern_properties: list[PatternPropertiesRule] = field(default_factory=list)
    required_groups: list[RequiredGroupsRule] = field(default_factory=list)
    conditional_required: list[ConditionalRequiredRule] = field(default_factory=list)
    property_count: PropertyCountRule | None = None
    unique_items: list[UniqueItemsRule] = field(default_factory=list)
    replace_unique_items: bool = False
    constant_types: list[ConstantTypesRule] = field(default_factory=list, init=False)
    replace_constant_types: bool = field(default=False, init=False)
    conditional_bounds: list[ConditionalBoundsRule] = field(default_factory=list, init=False)
    contains: list[ContainsRule] = field(default_factory=list, init=False)
    replace_contains: bool = field(default=False, init=False)

    def __bool__(self) -> bool:
        """Return whether any runtime validation rule is registered."""
        return bool(
            self.pattern_properties
            or self.required_groups
            or self.conditional_required
            or self.property_count
            or self.unique_items
            or self.replace_unique_items
            or self.constant_types
            or self.replace_constant_types
            or self.conditional_bounds
            or self.contains
            or self.replace_contains
        )

    @property
    def data_types(self) -> tuple[DataType, ...]:
        """Return all generated data types referenced by runtime rules."""
        return tuple(data_type for rule in self.pattern_properties for data_type in rule.data_types)

    def _written_form(self) -> tuple[object, ...]:
        """Return what the rules write, hashable, so that two holders of rules compare by the rules they write."""
        return (
            tuple(
                (
                    type(rule).__name__,
                    rule.declared_properties,
                    rule.rejected_patterns,
                    tuple((pattern, data_type.type_hint) for pattern, data_type in rule.pattern_properties),
                    None if rule.additional_property_type is None else rule.additional_property_type.type_hint,
                    rule.allow_unmatched,
                )
                for rule in self.pattern_properties
            ),
            stable_repr(self.required_groups),
            stable_repr(self.conditional_required),
            stable_repr(self.property_count),
            stable_repr(self.unique_items),
            self.replace_unique_items,
            stable_repr(self.constant_types),
            self.replace_constant_types,
            stable_repr(self.conditional_bounds),
            stable_repr(self.contains),
            self.replace_contains,
        )

    def _replace_pattern_properties(self, pattern_properties: list[PatternPropertiesRule]) -> SchemaRuntimeValidation:
        """Return a copy that holds ``pattern_properties``, so another holder of this value keeps its rules."""
        replaced = copy(self)
        replaced.pattern_properties = pattern_properties
        return replaced


class _InternalSchemaRuntimeValidation(SchemaRuntimeValidation):
    """A parser-owned runtime-validation value safe for built-in templates."""

    __slots__ = ()

    def __init__(
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
