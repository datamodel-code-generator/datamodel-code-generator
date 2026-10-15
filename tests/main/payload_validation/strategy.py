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
    from collections.abc import Callable, Iterator

    from jsonschema.exceptions import ValidationError
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


@cache
def _regex_classes(regex: str) -> frozenset[str]:
    """Return the character classes that a regex's escapes depend on."""
    return frozenset(_REGEX_CLASS_BY_ESCAPE[escape[1]] for escape in _REGEX_CLASS_ESCAPE.finditer(regex))


class _RegexEnginesDisagreeError(Exception):
    """Abort source validation instead of reporting an error that not, if or oneOf would invert."""


def _agreed_pattern(
    pattern: Callable[[Validator, str, Any, dict[str, Any]], Iterator[ValidationError]],
    validator: Validator,
    regex: str,
    instance: Any,
    schema: dict[str, Any],
) -> Iterator[ValidationError]:
    """Apply the pattern keyword unless the regex engines classify a character of the string differently."""
    if validator.is_type(instance, "string") and not all(
        _regex_engines_agree(regex_class, character) for regex_class in _regex_classes(regex) for character in instance
    ):
        raise _RegexEnginesDisagreeError
    yield from pattern(validator, regex, instance, schema)


@cache
def _regex_agreement_class(validator_class: type[Validator]) -> type[Validator]:
    """Extend a dialect's validator class with the regex agreement check on its pattern keyword."""
    return validators.extend(
        validator_class, {"pattern": partial(_agreed_pattern, validator_class.VALIDATORS["pattern"])}
    )


def _regex_agreement_validator(case: SchemaCase) -> SourceSchemaValidator:
    """Build the source validator whose pattern keyword checks exactly the strings it is applied to.

    The root drops ``$schema`` so references back into it keep the extended validator class.
    """
    agreement_class = _regex_agreement_class(validators.validator_for(case.source_schema))
    schema = _canonical_json_numbers(case.source_schema)
    schema.pop("$schema", None)
    return SourceSchemaValidator(agreement_class(schema))


def _valid_where_regex_engines_agree(validator: SourceSchemaValidator, payload: Any) -> bool:
    """Keep source-valid payloads whose applied patterns both regex engines decide alike."""
    try:
        return validator.is_valid(payload)
    except _RegexEnginesDisagreeError:
        return False


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
    strategy = strategy.filter(partial(_valid_where_regex_engines_agree, _regex_agreement_validator(case)))
    _payload_strategy_cache[cache_key] = strategy
    return strategy
