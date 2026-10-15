"""Payload generation and source-schema validation helpers."""

from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from fractions import Fraction
from functools import cache, partial
from typing import TYPE_CHECKING, Any

from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from jsonschema import validators
from pydantic_core import SchemaValidator, core_schema

from .conformance import _iter_schema_nodes, _schema_types
from .native_numeric import NATIVE_FLOAT_CONTRACT
from .schema import _schema_for_payload_generation

if TYPE_CHECKING:
    from jsonschema.protocols import Validator

    from .models import SchemaCase


_REGEX_CLASS_ESCAPE = re.compile(r"(?<!\\)(?:\\\\)*\\([dDsSwWbB])")
_REGEX_CLASS_BY_ESCAPE = dict.fromkeys("dD", r"\d") | dict.fromkeys("sS", r"\s") | dict.fromkeys("wWbB", r"\w")
"""The character class each escape depends on; word boundaries depend on word characters."""

_SourceSchemaCacheKey = tuple[str, str, str]
_source_schema_validator_cache: dict[_SourceSchemaCacheKey, SourceSchemaValidator] = {}
_payload_strategy_cache: dict[_SourceSchemaCacheKey, st.SearchStrategy[Any]] = {}


def _source_schema_cache_key(case: SchemaCase) -> _SourceSchemaCacheKey:
    return case.input_file_type, case.source_path.as_posix(), case.id


def _json_number(token: str) -> int | Fraction:
    value = Fraction(token)
    return value.numerator if value.denominator == 1 else value


def _canonical_json_numbers(value: Any) -> Any:
    """Interpret the canonical JSON representation without binary numeric rounding."""
    return json.loads(json.dumps(value, allow_nan=False), parse_float=_json_number)


class SourceSchemaValidator:
    """Validate canonical JSON numbers while keeping runtime payloads untouched."""

    def __init__(self, validator: Validator) -> None:
        """Wrap an independently configured exact-number source validator."""
        self.validator = validator

    def validate(self, payload: Any) -> None:
        """Validate the exact canonical JSON projection of a payload."""
        self.validator.validate(_canonical_json_numbers(payload))

    def is_valid(self, payload: Any) -> bool:
        """Return source validity without consulting a generated or native model."""
        return self.validator.is_valid(_canonical_json_numbers(payload))


def source_schema_validator(case: SchemaCase) -> SourceSchemaValidator:
    """Build a JSON Schema validator for the original source schema."""
    cache_key = _source_schema_cache_key(case)
    if (validator := _source_schema_validator_cache.get(cache_key)) is not None:
        return validator

    validator_class = validators.validator_for(case.source_schema)
    validator_class.check_schema(case.source_schema)
    validator = SourceSchemaValidator(validator_class(_canonical_json_numbers(case.source_schema)))
    _source_schema_validator_cache[cache_key] = validator
    return validator


def validate_with_source_schema(case: SchemaCase, payload: Any) -> None:
    """Validate a generated payload against the original source schema."""
    source_schema_validator(case).validate(payload)


def _bound_float_multiples(schema: dict[str, Any]) -> None:
    """Bound the copied sampling domain before integer multipliers become floats.

    The original schema remains the validation oracle. Intervals outside this
    sampling domain need a different strategy, even when their schema is valid.
    """
    for node in _iter_schema_nodes(schema):
        if (
            not isinstance(multiple := node.get("multipleOf"), float)
            or multiple.is_integer()
            or "enum" in node
            or "const" in node
        ):
            continue
        if (schema_types := _schema_types(node)) and not schema_types & {"integer", "number"}:
            continue

        # hypothesis-jsonschema multiplies an integer by multipleOf. Bound both
        # that integer's conversion and its product, with room for rounding and
        # subtraction of the two endpoints during divisibility checks.
        limit = sys.float_info.max / 2 * min(1, multiple)
        minimum = node.get("minimum", -limit)
        maximum = node.get("maximum", limit)
        outside_domain = (
            minimum > limit
            or maximum < -limit
            or (minimum == limit and node.get("exclusiveMinimum") is True)
            or (maximum == -limit and node.get("exclusiveMaximum") is True)
        )
        for keyword, outside in (("exclusiveMinimum", limit), ("exclusiveMaximum", -limit)):
            if isinstance(bound := node.get(keyword), (int, float)) and not isinstance(bound, bool):
                outside_domain |= (keyword == "exclusiveMinimum" and bound >= outside) or (
                    keyword == "exclusiveMaximum" and bound <= outside
                )
        if outside_domain:
            msg = "Numeric interval lies outside the finite multipleOf sampling domain"
            raise ValueError(msg)
        if "minimum" in node and "maximum" in node and minimum == maximum:
            # Sample the exact JSON endpoint; binary multiplication can miss it.
            # The unchanged source validator still enforces multipleOf afterward.
            node["enum"] = [minimum]
            del node["multipleOf"]
            continue
        node["minimum"] = max(minimum, -limit)
        node["maximum"] = min(maximum, limit)


@cache
def _rust_regex_class(regex_class: str) -> SchemaValidator:
    """Match one character of a class with pydantic-core's default Rust regex engine."""
    return SchemaValidator(core_schema.str_schema(pattern=f"^{regex_class}$"))


@cache
def _regex_engines_agree(regex_class: str, character: str) -> bool:
    """Return whether Python's re and pydantic-core's Rust regex agree on a character's class membership."""
    python_member = re.fullmatch(regex_class, character) is not None
    return python_member is _rust_regex_class(regex_class).isinstance_python(character)


def _classified_alike(value: Any, regex_classes: frozenset[str]) -> bool:
    """Keep payloads whose characters both regex engines place alike in the schema's pattern classes."""
    match value:
        case str():
            return all(
                _regex_engines_agree(regex_class, character) for character in value for regex_class in regex_classes
            )
        case dict():
            return all(
                _classified_alike(key, regex_classes) and _classified_alike(item, regex_classes)
                for key, item in value.items()
            )
        case list():
            return all(_classified_alike(item, regex_classes) for item in value)
    return True


def _pattern_regex_classes(schema: dict[str, Any]) -> frozenset[str]:
    """Return the classes escaped in patterns and pattern property names, including OpenAPI components."""
    roots = [schema]
    match schema:
        case {"components": {"schemas": dict()}}:
            roots.extend(schema["components"]["schemas"].values())
    return frozenset(
        _REGEX_CLASS_BY_ESCAPE[escape[1]]
        for node in _iter_schema_nodes(roots)
        for regex in (node.get("pattern"), *node.get("patternProperties", {}))
        if isinstance(regex, str)
        for escape in _REGEX_CLASS_ESCAPE.finditer(regex)
    )


def payload_strategy(case: SchemaCase) -> st.SearchStrategy[Any]:
    """Build the deterministic payload strategy for a schema case."""
    cache_key = _source_schema_cache_key(case)
    if (strategy := _payload_strategy_cache.get(cache_key)) is not None:
        return strategy

    validator = source_schema_validator(case)
    schema = _schema_for_payload_generation(deepcopy(case.source_schema))
    _bound_float_multiples(schema)
    match case.source_schema:
        case {"const": value} if type(value) in {int, float}:
            strategy = st.just(value)
        case {"enum": values} if values and all(type(value) in {int, float} for value in values):
            strategy = st.sampled_from(values)
        case _:
            strategy = from_schema(schema)
    if case.id == NATIVE_FLOAT_CONTRACT["case_id"]:
        witnesses = st.sampled_from(NATIVE_FLOAT_CONTRACT["source_valid_witnesses"]).map(deepcopy)
        strategy = st.one_of(witnesses, strategy)
    # Literal regex fragments provide constructive witnesses for intersections whose
    # Unicode alphabet is prohibitively unlikely under rejection sampling. Each
    # candidate is proved against the unchanged source, including refs and bounds.
    literals = [
        pattern
        for node in _iter_schema_nodes(schema)
        if isinstance(pattern := node.get("pattern"), str) and not re.search(r"[.\\^$*+?{}\[\]|()]", pattern)
    ]
    if literals:
        candidates = dict.fromkeys([*literals, "".join(literals), "".join(reversed(literals))])
        if witnesses := [candidate for candidate in candidates if validator.is_valid(candidate)]:
            strategy = st.one_of(st.sampled_from(witnesses), strategy)
    strategy = strategy.filter(validator.is_valid)
    if regex_classes := _pattern_regex_classes(schema):
        strategy = strategy.filter(partial(_classified_alike, regex_classes=regex_classes))
    _payload_strategy_cache[cache_key] = strategy
    return strategy
