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
        ContainsRule,
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


def render_contains_rules(rules: Iterable[ContainsRule]) -> tuple[str, ...]:
    """Render the raw path of each contains check with its matcher and its counts through ``class_body_lines``."""
    return _render_path_rules(
        "__json_schema_contains__",
        ((rule.path, (rule.matcher, rule.min_contains, rule.max_contains)) for rule in rules),
    )


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
    return _render_path_validation_base(
        class_name,
        base_class_name,
        "constant_types",
        "values",
        """            if (
                isinstance(value, (int, float))
                and value in values
                and not any(
                    isinstance(value, bool) is isinstance(expected, bool) and value == expected
                    for expected in values
                )
            ):
                raise ValueError('Expected the JSON type of the schema constant')""",
    )


def render_contains_validation_base(class_name: str, base_class_name: str) -> str:
    """Render the shared validator base that counts the items of the arrays on raw paths that a matcher accepts.

    A path takes the steps of the uniqueItems paths. The matcher holds the keywords of the ``contains`` schema and
    reads them as JSON Schema does: a boolean is no number, ``1.0`` is an integer, a string keyword applies to a string
    only and a numeric one to a number only; ``const`` and ``enum`` use the JSON equality of the core helper this layer
    is then rendered on (``_json_schema_conditional_equal``).
    """
    walk = _render_path_validation_base(
        class_name,
        base_class_name,
        "contains",
        "counts",
        """            if not isinstance(value, (list, tuple)):
                return
            matcher, min_contains, max_contains = counts
            count = sum(1 for item in value if cls._json_schema_contains_matches(item, matcher))
            if min_contains is not None and count < min_contains:
                raise ValueError(f'Expected at least {min_contains} items to match contains')
            if max_contains is not None and count > max_contains:
                raise ValueError(f'Expected at most {max_contains} items to match contains')""",
    )
    return f"""{walk}

    @classmethod
    def _json_schema_contains_matches(cls, item: Any, matcher: tuple[tuple[str, Any], ...]) -> bool:
        item = cls._json_schema_json_value(item)
        number = isinstance(item, (int, float)) and not isinstance(item, bool)
        string = isinstance(item, str)
        for keyword, expected in matcher:
            match keyword:
                case 'type':
                    if not any(
                        (name == 'null' and item is None)
                        or (name == 'boolean' and isinstance(item, bool))
                        or (name == 'integer' and number and (isinstance(item, int) or item.is_integer()))
                        or (name == 'number' and number)
                        or (name == 'string' and string)
                        or (name == 'array' and isinstance(item, (list, tuple)))
                        or (name == 'object' and (isinstance(item, dict) or isinstance(item, _Mapping)))
                        for name in expected
                    ):
                        return False
                case 'const':
                    if not cls._json_schema_conditional_equal(item, expected):
                        return False
                case 'enum':
                    if not any(cls._json_schema_conditional_equal(item, value) for value in expected):
                        return False
                case 'pattern' | 'minLength' | 'maxLength' if string:
                    if (
                        (keyword == 'pattern' and not re.search(expected, item))
                        or (keyword == 'minLength' and len(item) < expected)
                        or (keyword == 'maxLength' and len(item) > expected)
                    ):
                        return False
                case 'minimum' | 'exclusiveMinimum' | 'maximum' | 'exclusiveMaximum' | 'multipleOf' if number:
                    if (
                        (keyword == 'minimum' and item < expected)
                        or (keyword == 'exclusiveMinimum' and item <= expected)
                        or (keyword == 'maximum' and item > expected)
                        or (keyword == 'exclusiveMaximum' and item >= expected)
                        or (
                            keyword == 'multipleOf'
                            and (
                                not (item / expected).is_integer()
                                if isinstance(expected, float)
                                else item % expected != 0
                            )
                        )
                    ):
                        return False
        return True"""


def _render_path_validation_base(
    class_name: str,
    base_class_name: str,
    stem: str,
    rule_argument: str,
    end_check: str,
) -> str:
    """Render a shared validator base that walks each rule's raw path and runs ``end_check`` on the value at its end.

    The steps are those of the uniqueItems paths; a rule is a ``path`` and the ``rule_argument`` that ``end_check``
    reads.
    """
    method = f"_validate_json_schema_{stem}_path"
    call = f"cls.{method}({{}}, path, position + 1, {rule_argument})"
    return f"""class {class_name}({base_class_name}):
    __json_schema_{stem}__: ClassVar[tuple[tuple[object, ...], ...]] = ()

    @model_validator(mode='before')
    @classmethod
    def _validate_json_schema_{stem}(cls, data: Any) -> Any:
        for path, {rule_argument} in cls.__json_schema_{stem}__:
            cls.{method}(data, path, 0, {rule_argument})
        return data

    @classmethod
    def {method}(
        cls,
        value: Any,
        path: tuple[object, ...],
        position: int,
        {rule_argument}: tuple[object, ...],
    ) -> None:
        if position == len(path):
{end_check}
            return

        match path[position]:
            case None:
                if not isinstance(value, (list, tuple)):
                    return
                for item in value:
                    {call.format("item")}
            case ('__json_schema_array_tail__', int() as start):
                if not isinstance(value, (list, tuple)):
                    return
                for item in value[start:]:
                    {call.format("item")}
            case int() as index:
                if isinstance(value, (list, tuple)) and index < len(value):
                    {call.format("value[index]")}
            case '__json_schema_mapping_values__':
                if not (isinstance(value, dict) or isinstance(value, _Mapping)):
                    return
                for item in value.values():
                    {call.format("item")}
            case ('__json_schema_mapping_pattern_values__', str() as pattern, None):
                if not (isinstance(value, dict) or isinstance(value, _Mapping)):
                    return
                for name, item in value.items():
                    if re.search(pattern, name):
                        {call.format("item")}
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
                    {call.format("item")}
            case tuple() as input_names:
                if not (isinstance(value, dict) or isinstance(value, _Mapping)):
                    return
                for name in input_names:
                    if name in value:
                        {call.format("value[name]")}
                        return"""


def render_conditional_bounds_validation_base(class_name: str, base_class_name: str) -> str:
    """Render the shared validator base that checks the numeric bounds of the branch an if/then/else selects.

    The condition is read with the JSON equality of the core helper this layer is rendered on
    (``_json_schema_conditional_equal``: a boolean is no number); it and a bound read a property by the first of its
    input names the input holds, the one the field's value comes from. A bound applies to a JSON number only.
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
                next(
                    (
                        not expected
                        or cls._json_schema_conditional_any(data[name], expected)
                        for name in names
                        if name in data
                    ),
                    False,
                )
                for names, expected in condition
            )
            for names, keyword, limit in then_bounds if condition_matches else else_bounds:
                value = cls._json_schema_json_value(next((data[name] for name in names if name in data), None))
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
