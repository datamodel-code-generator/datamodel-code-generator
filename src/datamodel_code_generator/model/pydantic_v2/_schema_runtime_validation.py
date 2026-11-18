"""Small Python renderers for Pydantic v2 schema runtime validation extensions."""

from __future__ import annotations

from itertools import zip_longest
from typing import TYPE_CHECKING, TypeAlias, TypeVar

from datamodel_code_generator._records import record
from datamodel_code_generator.python_literal import stable_repr

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from datamodel_code_generator.model.runtime_validation import (
        ConditionalBoundsRule,
        ConstantTypesRule,
        PropertyCountRule,
        SchemaRuntimeValidation,
        UniqueItemsRule,
    )

_Model = TypeVar("_Model")
SchemaRuntimeValidationCapabilities: TypeAlias = tuple[bool, ...]


@record
class SchemaRuntimeValidationModulePlan:
    """Small module-level plan reused by the final helper renderer."""

    base_class_name: str
    plans_capabilities: bool
    helper_base_class_names: tuple[tuple[SchemaRuntimeValidationCapabilities, str], ...] = ()


def plan_schema_runtime_validation_bases(
    models: list[_Model],
    runtime_validation_by_model: dict[int, SchemaRuntimeValidation],
    *,
    get_base_models: Callable[[_Model], Iterable[_Model]],
    get_external_capabilities: Callable[[_Model], SchemaRuntimeValidationCapabilities],
    get_model_requirements: Callable[[_Model, SchemaRuntimeValidation], SchemaRuntimeValidationCapabilities],
) -> dict[int, SchemaRuntimeValidationCapabilities]:
    """Plan the exact runtime-helper capabilities each generated model lacks."""
    effective_capabilities_by_model: dict[int, SchemaRuntimeValidationCapabilities] = {}
    missing_capabilities_by_model: dict[int, SchemaRuntimeValidationCapabilities] = {}
    resolving_model_ids: set[int] = set()

    def effective_capabilities(model: _Model) -> SchemaRuntimeValidationCapabilities:
        model_id = id(model)
        if (capabilities := effective_capabilities_by_model.get(model_id)) is not None:
            return capabilities
        if model_id in resolving_model_ids:
            return ()

        resolving_model_ids.add(model_id)
        try:
            inherited_capabilities = _merge_capabilities(
                effective_capabilities(base_model) for base_model in get_base_models(model)
            )
            if (runtime_validation := runtime_validation_by_model.get(model_id)) is None:
                capabilities = _merge_capabilities((inherited_capabilities, get_external_capabilities(model)))
            else:
                missing_capabilities = _get_missing_capabilities(
                    get_model_requirements(model, runtime_validation),
                    inherited_capabilities,
                )
                missing_capabilities_by_model[model_id] = missing_capabilities
                capabilities = _merge_capabilities((inherited_capabilities, missing_capabilities))
        finally:
            resolving_model_ids.remove(model_id)
        effective_capabilities_by_model[model_id] = capabilities
        return capabilities

    for model in models:
        effective_capabilities(model)
    return missing_capabilities_by_model


def _merge_capabilities(
    capabilities: Iterable[SchemaRuntimeValidationCapabilities],
) -> SchemaRuntimeValidationCapabilities:
    """Return the capabilities any of ``capabilities`` has; ``()`` (a model on a cycle) has none."""
    merged: tuple[bool, ...] = ()
    for current in capabilities:
        merged = tuple(map(any, zip_longest(merged, current, fillvalue=False)))
    return merged


def _get_missing_capabilities(
    requirements: SchemaRuntimeValidationCapabilities,
    inherited_capabilities: SchemaRuntimeValidationCapabilities,
) -> SchemaRuntimeValidationCapabilities:
    return tuple(
        required and not inherited
        for required, inherited in zip_longest(requirements, inherited_capabilities, fillvalue=False)
    )


def render_property_count_rule(rule: PropertyCountRule) -> str:
    """Render a property-count class variable through ``class_body_lines``."""
    return (
        "__json_schema_property_count_rule__: ClassVar[tuple[Any, ...]] = "
        f"({stable_repr(rule.min_properties)}, {stable_repr(rule.max_properties)})"
    )


def render_unique_items_rules(rules: Iterable[UniqueItemsRule]) -> tuple[str, ...]:
    """Render raw uniqueItems paths through existing ``class_body_lines``."""
    return _render_path_rules("__json_schema_unique_items__", (rule.path for rule in rules))


def render_constant_types_rules(rules: Iterable[ConstantTypesRule]) -> tuple[str, ...]:
    """Render the raw paths of the constant checks, each with its values, through ``class_body_lines``."""
    return _render_path_rules("__json_schema_constant_types__", ((rule.path, rule.values) for rule in rules))


def render_conditional_bounds_rules(rules: Iterable[ConditionalBoundsRule]) -> tuple[str, ...]:
    """Render the condition and the then and else bounds of each conditional bounds check."""
    return _render_path_rules(
        "__json_schema_conditional_bounds__",
        ((rule.condition, rule.then_bounds, rule.else_bounds) for rule in rules),
    )


def _render_path_rules(name: str, rules: Iterable[tuple[object, ...]]) -> tuple[str, ...]:
    return (
        f"{name}: ClassVar[tuple[tuple[object, ...], ...]] = (",
        *(f"    {stable_repr(rule)}," for rule in rules),
        ")\n",
    )


def render_property_count_validation_base(class_name: str, base_class_name: str) -> str:
    """Render the shared raw-mapping property-count validator base."""
    return f"""class {class_name}({base_class_name}):
    __json_schema_property_count_rule__: ClassVar[tuple[Any, ...]] = ()

    @model_validator(mode='before')
    @classmethod
    def _validate_json_schema_property_count(cls, data: Any) -> Any:
        if not (rule := cls.__json_schema_property_count_rule__):
            return data
        if not (isinstance(data, dict) or isinstance(data, _Mapping)):
            return data
        property_count = len(data)
        min_properties, max_properties = rule
        if min_properties is not None and property_count < min_properties:
            raise ValueError(f'Expected at least {{min_properties}} properties')
        if max_properties is not None and property_count > max_properties:
            raise ValueError(f'Expected at most {{max_properties}} properties')
        return data"""


def render_constant_types_validation_base(class_name: str, base_class_name: str) -> str:
    """Render the shared validator base that rejects the other JSON type for the constants on raw paths.

    A path takes the steps of the uniqueItems paths; the value at its end is rejected where it is a boolean equal
    only to numbers of the constants, or a number equal only to booleans of them.
    """
    return f"""class {class_name}({base_class_name}):
    __json_schema_constant_types__: ClassVar[tuple[tuple[object, ...], ...]] = ()

    @model_validator(mode='before')
    @classmethod
    def _validate_json_schema_constant_types(cls, data: Any) -> Any:
        for path, values in cls.__json_schema_constant_types__:
            cls._validate_json_schema_constant_types_path(data, path, 0, values)
        return data

    @classmethod
    def _validate_json_schema_constant_types_path(
        cls,
        value: Any,
        path: tuple[object, ...],
        position: int,
        values: tuple[object, ...],
    ) -> None:
        if position == len(path):
            if (
                isinstance(value, (int, float))
                and value in values
                and not any(
                    isinstance(value, bool) is isinstance(expected, bool) and value == expected
                    for expected in values
                )
            ):
                raise ValueError('Expected the JSON type of the schema constant')
            return

        match path[position]:
            case None:
                if not isinstance(value, (list, tuple)):
                    return
                for item in value:
                    cls._validate_json_schema_constant_types_path(item, path, position + 1, values)
            case ('__json_schema_array_tail__', int() as start):
                if not isinstance(value, (list, tuple)):
                    return
                for item in value[start:]:
                    cls._validate_json_schema_constant_types_path(item, path, position + 1, values)
            case int() as index:
                if isinstance(value, (list, tuple)) and index < len(value):
                    cls._validate_json_schema_constant_types_path(value[index], path, position + 1, values)
            case '__json_schema_mapping_values__':
                if not (isinstance(value, dict) or isinstance(value, _Mapping)):
                    return
                for item in value.values():
                    cls._validate_json_schema_constant_types_path(item, path, position + 1, values)
            case ('__json_schema_mapping_pattern_values__', str() as pattern, None):
                if not (isinstance(value, dict) or isinstance(value, _Mapping)):
                    return
                for name, item in value.items():
                    if re.search(pattern, name):
                        cls._validate_json_schema_constant_types_path(item, path, position + 1, values)
            case ('__json_schema_mapping_additional_values__', tuple() as declared_name_groups, tuple() as patterns):
                if not (isinstance(value, dict) or isinstance(value, _Mapping)):
                    return
                declared_names = {{
                    next((name for name in names if name in value), None)
                    for names in declared_name_groups
                }}
                for name, item in value.items():
                    if name in declared_names or any(re.search(pattern, name) for pattern in patterns):
                        continue
                    cls._validate_json_schema_constant_types_path(item, path, position + 1, values)
            case tuple() as input_names:
                if not (isinstance(value, dict) or isinstance(value, _Mapping)):
                    return
                for name in input_names:
                    if name in value:
                        cls._validate_json_schema_constant_types_path(value[name], path, position + 1, values)
                        return"""


def render_conditional_bounds_validation_base(class_name: str, base_class_name: str) -> str:
    """Render the shared validator base that checks the numeric bounds of the branch an if/then/else selects.

    The condition is read as the conditional required rule reads it, with the JSON equality of the core helper this
    layer is rendered on (``_json_schema_conditional_equal``: a boolean is no number); a bound applies to a JSON number
    only.
    """
    return f"""class {class_name}({base_class_name}):
    __json_schema_conditional_bounds__: ClassVar[tuple[tuple[object, ...], ...]] = ()

    @model_validator(mode='before')
    @classmethod
    def _validate_json_schema_conditional_bounds(cls, data: Any) -> Any:
        if not (isinstance(data, dict) or isinstance(data, _Mapping)):
            return data
        for condition, then_bounds, else_bounds in cls.__json_schema_conditional_bounds__:
            condition_matches = all(
                any(
                    name in data
                    and (
                        not expected
                        or any(
                            cls._json_schema_conditional_equal(data[name], value)
                            for value in expected
                        )
                    )
                    for name in names
                )
                for names, expected in condition
            )
            for names, keyword, limit in then_bounds if condition_matches else else_bounds:
                value = next((data[name] for name in names if name in data), None)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                if (
                    (keyword == 'minimum' and value < limit)
                    or (keyword == 'exclusiveMinimum' and value <= limit)
                    or (keyword == 'maximum' and value > limit)
                    or (keyword == 'exclusiveMaximum' and value >= limit)
                ):
                    raise ValueError(f'Expected {{keyword}} {{limit}}')
        return data"""
