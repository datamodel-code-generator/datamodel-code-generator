"""Payload generation and source-schema validation helpers."""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from copy import deepcopy
from fractions import Fraction
from typing import TYPE_CHECKING, Any

from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from jsonschema import validators

from .conformance import _iter_schema_nodes, _schema_types
from .native_numeric import NATIVE_FLOAT_CONTRACT
from .schema import _schema_for_payload_generation

if TYPE_CHECKING:
    from jsonschema.protocols import Validator

    from .models import SchemaCase


_UNICODE_BEYOND_SHARED_REGEX_TABLES = tuple(map(int, unicodedata.unidata_version.split("."))) > (16, 0, 0)
"""Whether Python's re uses Unicode tables newer than 16.0, the newest that pydantic-core's regex shares with it."""
_NON_ASCII_WORD_CHARACTER = re.compile(r"(?=[^\x00-\x7f])\w")
_UNICODE_CLASS_ESCAPE = re.compile(r"\\[dDwWbB]")

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


def _within_shared_regex_tables(value: Any) -> bool:  # pragma: >=3.15 cover
    """Keep word characters ASCII, where Python's re and pydantic-core's regex classify them alike."""
    match value:
        case str():
            return _NON_ASCII_WORD_CHARACTER.search(value) is None
        case dict():
            return all(
                _within_shared_regex_tables(key) and _within_shared_regex_tables(item) for key, item in value.items()
            )
        case list():
            return all(map(_within_shared_regex_tables, value))
    return True


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
    patterns = [pattern for node in _iter_schema_nodes(schema) if isinstance(pattern := node.get("pattern"), str)]
    literals = [pattern for pattern in patterns if not re.search(r"[.\\^$*+?{}\[\]|()]", pattern)]
    if literals:
        candidates = dict.fromkeys([*literals, "".join(literals), "".join(reversed(literals))])
        if witnesses := [candidate for candidate in candidates if validator.is_valid(candidate)]:
            strategy = st.one_of(st.sampled_from(witnesses), strategy)
    strategy = strategy.filter(validator.is_valid)
    if _UNICODE_BEYOND_SHARED_REGEX_TABLES and any(map(_UNICODE_CLASS_ESCAPE.search, patterns)):  # pragma: >=3.15 cover
        strategy = strategy.filter(_within_shared_regex_tables)
    _payload_strategy_cache[cache_key] = strategy
    return strategy
