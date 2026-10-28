"""Schema normalization helpers for payload validation tests."""

from __future__ import annotations

import json
import math
import warnings
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from hypothesis_jsonschema import from_schema
from jsonschema import exceptions, validators

from .constants import (
    ARRAY_SCHEMA_KEYWORDS,
    COMBINED_SCHEMA_KEYS,
    OBJECT_SCHEMA_KEYWORDS,
    PAYLOAD_FORMAT_ENUMS,
    SIMPLE_ALLOF_IGNORED_KEYS,
    SIMPLE_ALLOF_SUPPORTED_KEYS,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


def _any_schema_node(value: Any, predicate: Callable[[dict[str, Any]], bool]) -> bool:
    result = False
    match value:
        case dict():
            result = predicate(value) or any(_any_schema_node(child, predicate) for child in value.values())
        case list():
            result = any(_any_schema_node(child, predicate) for child in value)
    return result


def _has_external_ref(value: Any) -> bool:
    def is_external_ref(schema: dict[str, Any]) -> bool:
        ref = schema.get("$ref")
        return isinstance(ref, str) and not ref.startswith("#/")

    return _any_schema_node(value, is_external_ref)


def _has_unresolved_internal_ref(schema: dict[str, Any]) -> bool:
    for ref in _find_internal_refs(schema):
        current: Any = schema
        for raw_part in ref.removeprefix("#/").split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, dict) or part not in current:
                return True
            current = current[part]
    return False


def _has_unsupported_keyword(value: Any) -> bool:
    def has_unsupported_keyword(schema: dict[str, Any]) -> bool:
        if "$dynamicAnchor" in schema or "$dynamicRef" in schema:
            return True
        if any(key.startswith("x-python-") for key in schema):
            return True
        return "customBasePath" in schema or "customTypePath" in schema or "patternProperties" in schema

    return _any_schema_node(value, has_unsupported_keyword)


def _has_dotted_definition_name(schema: dict[str, Any]) -> bool:
    for definitions_key in ("definitions", "$defs"):
        definitions = schema.get(definitions_key)
        if isinstance(definitions, dict) and any("." in key for key in definitions):
            return True
    return False


def _has_allof_property_override(value: Any) -> bool:
    def has_allof_property_override(schema: dict[str, Any]) -> bool:
        properties = schema.get("properties")
        all_of = schema.get("allOf")
        return isinstance(properties, dict) and bool(properties) and isinstance(all_of, list)

    return _any_schema_node(value, has_allof_property_override)


def _find_internal_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    match value:
        case dict():
            if isinstance(ref := value.get("$ref"), str) and ref.startswith("#/"):
                refs.add(ref)
            for child in value.values():
                refs.update(_find_internal_refs(child))
        case list():
            for child in value:
                refs.update(_find_internal_refs(child))
    return refs


def _normalize_openapi_component_ref(ref: str) -> str:
    if ref.startswith("#/") or "/" in ref or "#" in ref:
        return ref
    return f"#/components/schemas/{ref}"


def _find_openapi_component_refs(value: Any) -> set[str]:
    refs = _find_internal_refs(value)
    match value:
        case {"discriminator": {"mapping": mapping}, **rest} if isinstance(mapping, dict):
            refs.update(
                normalized_ref
                for ref in mapping.values()
                if isinstance(ref, str) and (normalized_ref := _normalize_openapi_component_ref(ref)).startswith("#/")
            )
            for child in rest.values():
                refs.update(_find_openapi_component_refs(child))
        case dict():
            for child in value.values():
                refs.update(_find_openapi_component_refs(child))
        case list():
            for child in value:
                refs.update(_find_openapi_component_refs(child))
    return refs


def _has_allof_ref(schema: dict[str, Any], ref: str) -> bool:
    all_of = schema.get("allOf")
    return isinstance(all_of, list) and any(isinstance(item, dict) and item.get("$ref") == ref for item in all_of)


def _has_recursive_definition_ref(schema: dict[str, Any]) -> bool:
    definitions: dict[str, Any] = {}
    for definitions_key in ("definitions", "$defs"):
        raw_definitions = schema.get(definitions_key)
        if isinstance(raw_definitions, dict):
            for name, definition in raw_definitions.items():
                if isinstance(definition, dict):
                    definitions[f"#/{definitions_key}/{name}"] = definition
    if not definitions:
        return False

    graph = {
        ref: {child_ref for child_ref in _find_internal_refs(definition) if child_ref in definitions}
        for ref, definition in definitions.items()
    }

    def visit(ref: str, stack: set[str]) -> bool:
        if ref in stack:
            return True
        return any(visit(child_ref, {*stack, ref}) for child_ref in graph[ref])

    return any(visit(ref, set()) for ref in graph)


def _has_recursive_component_ref(schema: dict[str, Any]) -> bool:
    components = schema.get("components")
    if not isinstance(components, dict):
        return False
    raw_schemas = components.get("schemas")
    if not isinstance(raw_schemas, dict):
        return False
    definitions = {
        f"#/components/schemas/{name}": component_schema
        for name, component_schema in raw_schemas.items()
        if isinstance(component_schema, dict)
    }
    graph = {
        ref: {child_ref for child_ref in _find_internal_refs(definition) if child_ref in definitions}
        for ref, definition in definitions.items()
    }

    def visit(ref: str, stack: set[str]) -> bool:
        if ref in stack:
            return True
        return any(visit(child_ref, {*stack, ref}) for child_ref in graph[ref])

    return any(visit(ref, set()) for ref in graph)


def _has_default_on_combined_schema(value: Any) -> bool:
    return _any_schema_node(
        value,
        lambda schema: "default" in schema and any(key in schema for key in ("allOf", "anyOf", "oneOf")),
    )


def _has_object_keywords_without_object_type(value: Any) -> bool:
    return _any_schema_node(
        value,
        lambda schema: "type" not in schema and bool(OBJECT_SCHEMA_KEYWORDS & schema.keys()),
    )


def _has_array_keywords_without_array_type(value: Any) -> bool:
    return _any_schema_node(value, lambda schema: "type" not in schema and bool(ARRAY_SCHEMA_KEYWORDS & schema.keys()))


def _has_unsatisfiable_contains_false(value: Any) -> bool:
    def has_unsatisfiable_contains_false(schema: dict[str, Any]) -> bool:
        min_contains = schema.get("minContains", 1)
        return schema.get("contains") is False and (not isinstance(min_contains, int) or min_contains > 0)

    return _any_schema_node(value, has_unsatisfiable_contains_false)


def _has_unsatisfiable_contains_bounds(value: Any) -> bool:
    def has_unsatisfiable_contains_bounds(schema: dict[str, Any]) -> bool:
        min_contains = schema.get("minContains", 1)
        max_contains = schema.get("maxContains")
        return (
            "contains" in schema
            and isinstance(min_contains, int)
            and isinstance(max_contains, int)
            and max_contains < min_contains
        )

    return _any_schema_node(value, has_unsatisfiable_contains_bounds)


def _has_unsatisfiable_property_count(value: Any) -> bool:
    def has_unsatisfiable_property_count(schema: dict[str, Any]) -> bool:
        properties = schema.get("properties")
        property_count = len(properties) if isinstance(properties, dict) else 0
        min_properties = schema.get("minProperties")
        max_properties = schema.get("maxProperties")
        if schema.get("additionalProperties") is False:
            if isinstance(min_properties, int) and min_properties > property_count:
                return True
            if isinstance(max_properties, int) and max_properties < len(schema.get("required", [])):
                return True
        return False

    return _any_schema_node(value, has_unsatisfiable_property_count)


def _schema_exclusion_reason(schema: dict[str, Any], *, is_openapi: bool = False) -> str | None:  # noqa: PLR0911, PLR0912
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            validators.validator_for(schema).check_schema(schema)
    except exceptions.SchemaError:
        return "schema is invalid for its declared JSON Schema draft"
    if _has_external_ref(schema):
        return "contains external or non-fragment $ref values"
    if _has_unresolved_internal_ref(schema):
        return "contains unresolved internal $ref values"
    if _has_unsupported_keyword(schema):
        return "uses schema features requiring non-default generator configuration"
    if _has_dotted_definition_name(schema):
        return "dotted definition names require modular output"
    if _has_allof_property_override(schema):
        return "allOf property override emits duplicate-field warnings under warning-as-error tests"
    if _has_recursive_definition_ref(schema):
        return "hypothesis-jsonschema cannot resolve recursive definition references"
    if _has_recursive_component_ref(schema):
        return "hypothesis-jsonschema cannot resolve recursive OpenAPI component references"
    if _has_default_on_combined_schema(schema):
        return "combined schemas with defaults can validate generated defaults before payload input"
    if _has_unsatisfiable_contains_false(schema):
        return "contains false with minContains greater than zero has no valid array payloads"
    if _has_unsatisfiable_contains_bounds(schema):
        return "contains minContains/maxContains bounds have no valid array payloads"
    if _has_unsatisfiable_property_count(schema):
        return "object property count constraints have no valid payloads"
    if _any_schema_node(schema, lambda node: "allOf" in node) and from_schema(deepcopy(schema)).is_empty:
        return "allOf has no schema-valid payload strategy"
    if not is_openapi and _has_object_keywords_without_object_type(schema):
        return "JSON Schema object keywords without type object allow non-object payloads"
    if not is_openapi and _has_array_keywords_without_array_type(schema):
        return "JSON Schema array keywords without type array allow non-array payloads"
    return None


def _apply_nullable(schema: dict[str, Any]) -> None:
    match schema.get("type"):
        case str(schema_type):
            schema["type"] = [schema_type, "null"]
        case list(schema_types):
            schema["type"] = [*schema_types, "null"] if "null" not in schema_types else schema_types
        case _ if "oneOf" in schema:
            schema["oneOf"] = [*schema["oneOf"], {"type": "null"}]
        case _ if "anyOf" in schema:
            schema["anyOf"] = [*schema["anyOf"], {"type": "null"}]
        case _:
            schema["anyOf"] = [{"type": "null"}, {}]


def _set_default_openapi_object_type(schema: dict[str, Any]) -> None:
    if (
        "type" not in schema
        and any(key in schema for key in ("properties", "required", "additionalProperties"))
        and not any(key in schema for key in (*COMBINED_SCHEMA_KEYS, "$ref"))
    ):
        schema["type"] = "object"


def _set_discriminator_mapping_enum(schema: dict[str, Any], source_schema: dict[str, Any]) -> None:
    discriminator = source_schema.get("discriminator")
    if not isinstance(discriminator, dict) or not isinstance(discriminator.get("mapping"), dict):
        return
    property_name = discriminator.get("propertyName")
    properties = schema.get("properties")
    if not isinstance(property_name, str) or not isinstance(properties, dict):
        return
    property_schema = properties.get(property_name)
    if isinstance(property_schema, dict) and property_schema.get("type") == "string":
        property_schema.setdefault("enum", list(discriminator["mapping"]))
    return


def _to_json_schema(value: Any) -> Any:
    """Convert common OpenAPI schema extensions to JSON Schema-compatible shape."""
    converted_value = value
    match value:
        case dict():
            converted = {key: _to_json_schema(child) for key, child in value.items() if key != "nullable"}
            if (property_names := converted.get("x-propertyNames")) is not None:
                converted.setdefault("propertyNames", property_names)
            _set_discriminator_mapping_enum(converted, value)
            _set_default_openapi_object_type(converted)
            if value.get("nullable") is True:
                _apply_nullable(converted)
            converted_value = converted
        case list():
            converted_value = [_to_json_schema(child) for child in value]
    return converted_value


def _normalize_schema_dialect(schema: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(schema))
    if normalized.get("$schema") == "http://json-schema.org/draft/2019-09/schema#":
        normalized["$schema"] = "https://json-schema.org/draft/2019-09/schema"
    elif normalized.get("$schema") == "http://json-schema.org/schema#":
        normalized["$schema"] = "http://json-schema.org/draft-07/schema#"
    return normalized


def _openapi_components(document: dict[str, Any]) -> dict[str, Any]:
    if isinstance(components := document.get("components"), dict) and isinstance(
        schemas := components.get("schemas"),
        dict,
    ):
        return schemas
    return {}


def _has_dotted_openapi_component_name(document: dict[str, Any]) -> bool:
    return any("." in name for name in _openapi_components(document))


def _referenced_openapi_components(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    referenced: dict[str, Any] = {}
    pending = [
        ref.removeprefix("#/components/schemas/")
        for ref in _find_openapi_component_refs(schema)
        if ref.startswith("#/components/schemas/")
    ]
    while pending:
        name = pending.pop()
        if name in referenced:
            continue
        component = components.get(name)
        if not isinstance(component, dict):
            continue
        referenced[name] = component
        if isinstance(component.get("discriminator"), dict):
            component_ref = f"#/components/schemas/{name}"
            pending.extend(
                component_name
                for component_name, component_schema in components.items()
                if isinstance(component_schema, dict) and _has_allof_ref(component_schema, component_ref)
            )
        pending.extend(
            ref.removeprefix("#/components/schemas/")
            for ref in _find_openapi_component_refs(component)
            if ref.startswith("#/components/schemas/")
        )
    return referenced


def _openapi_component_mapping_tags(mapping: Any) -> dict[str, str]:
    if not isinstance(mapping, dict):
        return {}
    return {
        normalized_ref.removeprefix("#/components/schemas/"): tag
        for tag, ref in mapping.items()
        if isinstance(tag, str)
        and isinstance(ref, str)
        and (normalized_ref := _normalize_openapi_component_ref(ref)).startswith("#/components/schemas/")
    }


def _iter_branch_component_refs(schema: dict[str, Any], *keys: str) -> Iterator[str]:
    for key in keys:
        for item in schema.get(key, []):
            if (
                isinstance(item, dict)
                and isinstance(ref := item.get("$ref"), str)
                and ref.startswith("#/components/schemas/")
            ):
                yield ref.removeprefix("#/components/schemas/")


def _apply_discriminator_payload_constraints(json_schema: dict[str, Any], schema: dict[str, Any]) -> None:
    discriminator = schema.get("discriminator")
    if not isinstance(discriminator, dict):
        return
    property_name = discriminator.get("propertyName")
    if not isinstance(property_name, str):
        return
    component_schemas = _openapi_components(json_schema)
    if not component_schemas:
        return
    mapped_tags = _openapi_component_mapping_tags(discriminator.get("mapping"))
    branch_refs = set(_iter_branch_component_refs(schema, "anyOf", "oneOf"))
    branch_refs.update(mapped_tags)
    for component_name in branch_refs:
        if not isinstance(component_schema := component_schemas.get(component_name), dict):
            continue
        properties = component_schema.get("properties")
        if not isinstance(properties, dict) or not isinstance(property_schema := properties.get(property_name), dict):
            continue
        _add_required_property(component_schema, property_name)
        tag = mapped_tags.get(component_name, component_name)
        if property_schema.get("type") == "string" and not any(
            key in property_schema for key in ("$ref", "const", "enum")
        ):
            property_schema["enum"] = [tag]
    return


def _add_required_property(schema: dict[str, Any], property_name: str) -> None:
    required = schema.setdefault("required", [])
    if isinstance(required, list) and property_name not in required:
        required.append(property_name)


def _require_discriminator_property(schema: dict[str, Any], property_name: str) -> dict[str, Any] | None:
    schema.setdefault("type", "object")
    properties = schema.setdefault("properties", {})
    if not isinstance(properties, dict):
        return None
    property_schema = properties.setdefault(property_name, {"type": "string"})
    if not isinstance(property_schema, dict):
        return None
    _add_required_property(schema, property_name)
    return property_schema


def _set_discriminator_property_constraint(schema: dict[str, Any], property_name: str, tags: list[str]) -> None:
    if not tags:
        return
    property_schema = _require_discriminator_property(schema, property_name)
    if property_schema is None:
        return
    if property_schema.get("type", "string") == "string" and "$ref" not in property_schema:
        property_schema["enum"] = tags
    return


def _apply_component_discriminator_payload_constraints(json_schema: dict[str, Any]) -> None:
    component_schemas = _openapi_components(json_schema)
    if not component_schemas:
        return
    for base_name, base_schema in list(component_schemas.items()):
        if not isinstance(base_schema, dict):
            continue
        discriminator = base_schema.get("discriminator")
        if not isinstance(discriminator, dict):
            continue
        property_name = discriminator.get("propertyName")
        if not isinstance(property_name, str):
            continue
        subtype_tags = _openapi_component_mapping_tags(discriminator.get("mapping"))
        if not subtype_tags:
            base_ref = f"#/components/schemas/{base_name}"
            subtype_tags = {
                component_name: component_name
                for component_name, component_schema in component_schemas.items()
                if component_name != base_name
                and isinstance(component_schema, dict)
                and _has_allof_ref(component_schema, base_ref)
            }
        _set_discriminator_property_constraint(base_schema, property_name, list(subtype_tags.values()))
        for component_name, tag in subtype_tags.items():
            component_schema = component_schemas.get(component_name)
            if isinstance(component_schema, dict):
                _set_discriminator_property_constraint(component_schema, property_name, [tag])
    return


def _merge_equal_constraint(merged: dict[str, Any], schema: dict[str, Any], key: str) -> bool:
    if key not in schema:
        return True
    if key in merged and merged[key] != schema[key]:
        return False
    merged[key] = schema[key]
    return True


def _merge_enum_constraint(merged: dict[str, Any], schema: dict[str, Any]) -> bool:
    if "enum" not in schema:
        return True
    schema_enum = schema["enum"]
    if not isinstance(schema_enum, list):
        return False
    merged["enum"] = [value for value in merged["enum"] if value in schema_enum] if "enum" in merged else schema_enum
    return bool(merged["enum"])


def _merge_multiple_of_constraint(merged: dict[str, Any], schema: dict[str, Any]) -> bool:
    if (multiple_of := schema.get("multipleOf")) is None:
        return True
    if not isinstance(multiple_of, int) or multiple_of <= 0:
        return False
    existing_multiple_of = merged.get("multipleOf")
    merged["multipleOf"] = multiple_of if existing_multiple_of is None else math.lcm(existing_multiple_of, multiple_of)
    return True


def _merge_bound_constraints(
    merged: dict[str, Any],
    schema: dict[str, Any],
    keys: tuple[str, ...],
    merge: Callable[[Any, Any], Any],
) -> None:
    for key in keys:
        if key in schema:
            merged[key] = schema[key] if key not in merged else merge(merged[key], schema[key])


def _merge_simple_allof_for_payload_generation(schema: dict[str, Any]) -> dict[str, Any]:
    all_of = schema.get("allOf")
    if not isinstance(all_of, list) or not all(isinstance(item, dict) for item in all_of):
        return schema

    constraint_schemas = [
        {key: value for key, value in item.items() if key not in SIMPLE_ALLOF_IGNORED_KEYS} for item in all_of
    ]
    if any(set(item) - SIMPLE_ALLOF_SUPPORTED_KEYS for item in constraint_schemas):
        return schema
    if not any(constraint_schemas):
        return schema

    merged = {key: value for key, value in schema.items() if key != "allOf"}
    if set(merged) - SIMPLE_ALLOF_IGNORED_KEYS:
        return schema
    merged = {key: value for key, value in merged.items() if key not in SIMPLE_ALLOF_IGNORED_KEYS}

    is_mergeable = True
    for item in constraint_schemas:
        if not all(_merge_equal_constraint(merged, item, key) for key in ("type", "format", "const")):
            is_mergeable = False
            break
        if not _merge_enum_constraint(merged, item) or not _merge_multiple_of_constraint(merged, item):
            is_mergeable = False
            break
        _merge_bound_constraints(merged, item, ("minimum", "exclusiveMinimum", "minItems", "minLength"), max)
        _merge_bound_constraints(merged, item, ("maximum", "exclusiveMaximum", "maxItems", "maxLength"), min)
        if item.get("uniqueItems") is True:
            merged["uniqueItems"] = True

    return merged if is_mergeable and merged else schema


def _rewrite_prefix_items(schema: dict[str, Any]) -> None:
    if not isinstance(prefix_items := schema.pop("prefixItems", None), list):
        return
    tail_items = schema.pop("items", None)
    schema["items"] = prefix_items
    if tail_items is not None:
        schema["additionalItems"] = tail_items
    return


def _rewrite_contains(schema: dict[str, Any]) -> None:
    match schema.get("contains"):
        case False if schema.get("minContains") == 0:
            schema.pop("contains", None)
            schema.pop("minContains", None)
            schema.pop("maxContains", None)
        case True:
            min_contains = schema.pop("minContains", None)
            max_contains = schema.pop("maxContains", None)
            schema.pop("contains", None)
            if isinstance(min_contains, int):
                schema["minItems"] = max(schema.get("minItems", 0), min_contains)
            if isinstance(max_contains, int):
                schema["maxItems"] = min(schema.get("maxItems", max_contains), max_contains)


def _set_payload_format_enum(schema: dict[str, Any]) -> None:
    if schema.get("type") == "string" and isinstance(
        format_enum := PAYLOAD_FORMAT_ENUMS.get(schema.get("format")),
        list,
    ):
        schema.setdefault("enum", format_enum)


def _schema_for_payload_generation(value: Any) -> Any:
    payload_schema = value
    match value:
        case dict():
            schema = {key: _schema_for_payload_generation(child) for key, child in value.items()}
            schema = _merge_simple_allof_for_payload_generation(schema)
            if schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema":
                schema["$schema"] = "http://json-schema.org/draft-07/schema#"
            _rewrite_prefix_items(schema)
            _rewrite_contains(schema)
            _set_payload_format_enum(schema)
            payload_schema = schema
        case list():
            payload_schema = [_schema_for_payload_generation(child) for child in value]
    return payload_schema
