"""Small Python renderers for Pydantic v2 schema runtime validation extensions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from datamodel_code_generator.model.runtime_validation import (
        PropertyCountRule,
        SchemaRuntimeValidation,
        UniqueItemsRule,
    )

_Model = TypeVar("_Model")
SchemaRuntimeValidationCapabilities: TypeAlias = tuple[bool, bool]


@dataclass(frozen=True, slots=True)
class SchemaRuntimeValidationModulePlan:
    """Small module-level plan reused by the final helper renderer."""

    base_class_name: str
    has_property_count: bool
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
            return False, False

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
    core = property_count = False
    for current_core, current_property_count in capabilities:
        core = core or current_core
        property_count = property_count or current_property_count
    return core, property_count


def _get_missing_capabilities(
    requirements: SchemaRuntimeValidationCapabilities,
    inherited_capabilities: SchemaRuntimeValidationCapabilities,
) -> SchemaRuntimeValidationCapabilities:
    return (
        requirements[0] and not inherited_capabilities[0],
        requirements[1] and not inherited_capabilities[1],
    )


def render_property_count_rule(rule: PropertyCountRule) -> str:
    """Render a property-count class variable through ``class_body_lines``."""
    return (
        "__json_schema_property_count_rule__: ClassVar[tuple[Any, ...]] = "
        f"({rule.min_properties!r}, {rule.max_properties!r})"
    )


def render_unique_items_rules(rules: Iterable[UniqueItemsRule]) -> tuple[str, ...]:
    """Render raw uniqueItems paths through existing ``class_body_lines``."""
    return (
        "__json_schema_unique_items__: ClassVar[tuple[tuple[object, ...], ...]] = (",
        *(f"    {rule.path!r}," for rule in rules),
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
        if not (isinstance(data, dict) or isinstance(data, _collections_abc.Mapping)):
            return data
        property_count = len(data)
        min_properties, max_properties = rule
        if min_properties is not None and property_count < min_properties:
            raise ValueError(f'Expected at least {{min_properties}} properties')
        if max_properties is not None and property_count > max_properties:
            raise ValueError(f'Expected at most {{max_properties}} properties')
        return data"""
