"""Payload generation and source-schema validation helpers."""

from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from fractions import Fraction
from functools import cache, partial, partialmethod
from itertools import starmap
from typing import TYPE_CHECKING, Any

from attrs import fields
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from jsonschema import validators
from pydantic_core import SchemaValidator, core_schema

from .conformance import _iter_schema_nodes, _schema_types
from .native_numeric import NATIVE_FLOAT_CONTRACT
from .schema import _schema_for_payload_generation

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from jsonschema.exceptions import ValidationError
    from jsonschema.protocols import Validator

    from .models import SchemaCase


_REGEX_CLASS_ESCAPE = re.compile(r"(?<!\\)(?:\\\\)*\\([dDsSwWbB])")
_REGEX_CLASS_BY_ESCAPE = dict.fromkeys("dD", r"\d") | dict.fromkeys("sS", r"\s") | dict.fromkeys("wWbB", r"\w")
"""The character class each escape depends on; word boundaries depend on word characters."""
_REGEX_END_ANCHOR = re.compile(r"\\.|\[\^?\]?(?:\\.|[^\]\\])*\]|(?P<anchor>\$)")
"""An escape or a character class, which keep their ``$``, or the ``$`` anchor itself."""

_SourceSchemaCacheKey = tuple[str, str, str]
_source_schema_validator_cache: dict[_SourceSchemaCacheKey, SourceSchemaValidator] = {}
_payload_strategy_cache: dict[_SourceSchemaCacheKey, st.SearchStrategy[Any]] = {}


def _source_schema_cache_key(case: SchemaCase) -> _SourceSchemaCacheKey:
    return case.input_file_type, case.source_path.as_posix(), case.id


def _json_number(token: str) -> int | Fraction:
    value = Fraction(token)
    return value.numerator if value.denominator == 1 else value


def _canonical_json_numbers(
    value: Any, object_pairs_hook: Callable[[list[tuple[str, Any]]], dict[str, Any]] | None = None
) -> Any:
    """Interpret the canonical JSON representation without binary numeric rounding."""
    return json.loads(json.dumps(value, allow_nan=False), parse_float=_json_number, object_pairs_hook=object_pairs_hook)


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

    hypothesis-jsonschema samples every multipleOf as an unbounded integer times the multiple: a float multiple
    turns the product into a float, and a ``number`` schema the model reads as a float. Hypothesis also draws
    integers from the constants of the modules a worker has loaded, so unbounded products can leave the float range.
    The original schema remains the validation oracle. Intervals outside this
    sampling domain need a different strategy, even when their schema is valid.
    """
    for node in _iter_schema_nodes(schema):
        multiple = node.get("multipleOf")
        if isinstance(multiple, bool) or not isinstance(multiple, int | float) or "enum" in node or "const" in node:
            continue
        schema_types = _schema_types(node)
        numeric_types = schema_types & {"integer", "number"}
        if numeric_types != {"number"} and not (isinstance(multiple, float) and (numeric_types or not schema_types)):
            continue

        # hypothesis-jsonschema multiplies an integer by multipleOf. Bound both
        # that integer's conversion and its product, with room for rounding and
        # subtraction of the two endpoints during divisibility checks.
        limit = sys.float_info.max / 2 * min(1, multiple)
        minimum = node.get("minimum", -limit)
        maximum = node.get("maximum", limit)
        if "minimum" in node and "maximum" in node and minimum == maximum:
            # Sample the exact JSON endpoint; binary multiplication can miss it.
            # The unchanged source validator still enforces multipleOf afterward.
            node["enum"] = [minimum]
            del node["multipleOf"]
            continue
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


def _end_of_input(match: re.Match[str]) -> str:
    """Return the anchor that matches only at the end of the input, as in JSON Schema, for a ``$`` of ``re``."""
    return r"(?:(?!\n\Z)$)" if match["anchor"] else match[0]


@cache
def _end_of_input_regex(regex: str) -> re.Pattern[str] | None:
    """Return the regex with every ``$`` anchoring only the end of the input, or None when it has none."""
    return re.compile(_REGEX_END_ANCHOR.sub(_end_of_input, regex)) if "$" in regex else None


def _end_anchors_agree(regex: str, string: str) -> bool:
    """Return whether Python's ``$``, which also matches before a final newline, decides like the end of the input."""
    if not string.endswith("\n") or (end_of_input := _end_of_input_regex(regex)) is None:
        return True
    return (re.search(regex, string) is None) is (end_of_input.search(string) is None)


def _engines_agree(regex: str, string: str) -> bool:
    """Return whether the regex engines decide alike on the string: its characters' classes and its final newline."""
    return _end_anchors_agree(regex, string) and all(
        _regex_engines_agree(regex_class, character) for regex_class in _regex_classes(regex) for character in string
    )


class _RegexEnginesDisagreeError(Exception):
    """Abort source validation instead of reporting an error that not, if or oneOf would invert."""


def _pattern_strings(
    validator: Validator, regex: str, instance: Any, _schema: dict[str, Any]
) -> Iterable[tuple[str, str]]:
    """Return the string the pattern keyword matches against its regex."""
    return ((regex, instance),) if validator.is_type(instance, "string") else ()


def _property_names(
    validator: Validator, _value: Any, instance: Any, schema: dict[str, Any]
) -> Iterable[tuple[str, str]]:
    """Return the property names a keyword matches against the patternProperties regexes of its schema."""
    if not validator.is_type(instance, "object"):
        return ()
    return ((regex, name) for regex in schema.get("patternProperties", {}) for name in instance)


_REGEX_MATCHES = {
    "pattern": _pattern_strings,
    **dict.fromkeys(("patternProperties", "additionalProperties", "unevaluatedProperties"), _property_names),
}
"""The regex and string pairs each regex-matching keyword matches."""


def _agreed(
    matches: Callable[[Validator, Any, Any, dict[str, Any]], Iterable[tuple[str, str]]],
    keyword: Callable[[Validator, Any, Any, dict[str, Any]], Iterator[ValidationError]],
    validator: Validator,
    value: Any,
    instance: Any,
    schema: dict[str, Any],
) -> Iterator[ValidationError]:
    """Apply a keyword unless the regex engines decide differently on a string it matches."""
    if not all(starmap(_engines_agree, matches(validator, value, instance, schema))):
        raise _RegexEnginesDisagreeError
    yield from keyword(validator, value, instance, schema)


def _evolve_within_dialect(
    validator: Validator, evolve: Callable[..., Validator], dialect: type[Validator], **changes: Any
) -> Validator:
    """Evolve into a subschema, such as the root a reference re-enters, without its ``$schema`` dropping the checks.

    A subschema declaring another dialect switches to that dialect's extended class, as jsonschema switches classes.
    """
    if isinstance(schema := changes.get("schema", validator.schema), dict) and "$schema" in schema:
        changes["schema"] = {keyword: value for keyword, value in schema.items() if keyword != "$schema"}
        if (declared := validators.validator_for(schema, default=dialect)) is not dialect:
            declared_class = _regex_agreement_class(declared)
            return declared_class(
                **{field.alias: getattr(validator, field.name) for field in fields(declared_class) if field.init}
                | changes
            )
    return evolve(validator, **changes)


@cache
def _regex_agreement_class(validator_class: type[Validator]) -> type[Validator]:
    """Extend a dialect's validator class with the regex agreement check on every keyword that matches a regex."""
    agreement_class = validators.extend(
        validator_class,
        {
            keyword: partial(_agreed, matches, validator_class.VALIDATORS[keyword])
            for keyword, matches in _REGEX_MATCHES.items()
            if keyword in validator_class.VALIDATORS
        },
    )
    agreement_class.evolve = partialmethod(_evolve_within_dialect, agreement_class.evolve, validator_class)
    return agreement_class


def _unevaluated_properties_last(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Order unevaluatedProperties after its adjacent keywords, as JSON Schema evaluates it.

    It reuses the patternProperties regexes of the subschemas those keywords apply, so their checks run first.
    """
    return dict(sorted(pairs, key=lambda pair: pair[0] == "unevaluatedProperties"))


def _regex_agreement_validator(case: SchemaCase) -> SourceSchemaValidator:
    """Build the source validator whose regex keywords check exactly the strings they match."""
    agreement_class = _regex_agreement_class(validators.validator_for(case.source_schema))
    return SourceSchemaValidator(
        agreement_class(_canonical_json_numbers(case.source_schema, _unevaluated_properties_last))
    )


def _valid_where_regex_engines_agree(validator: SourceSchemaValidator, payload: Any) -> bool:
    """Keep source-valid payloads whose matched regexes both regex engines decide alike."""
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
