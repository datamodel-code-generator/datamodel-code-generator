"""Payload generation and source-schema validation helpers."""

from __future__ import annotations

import base64
import ipaddress
import json
import math
import re
import sys
from copy import deepcopy
from datetime import datetime, time, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import cache, partial, partialmethod
from itertools import starmap
from typing import TYPE_CHECKING, Any, Final

from attrs import fields
from hypothesis import HealthCheck, find, settings
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from jsonschema import validators
from pydantic_core import SchemaValidator, core_schema

from datamodel_code_generator import DataModelType, InputFileType, generate
from datamodel_code_generator.format import PythonVersion
from datamodel_code_generator.parser.schema_version import get_data_formats
from datamodel_code_generator.types import Types

from .conformance import _iter_schema_nodes, _schema_types
from .constants import PAYLOAD_TARGET_PYTHON_VERSION
from .native_numeric import NATIVE_FLOAT_CONTRACT
from .schema import _schema_for_payload_generation

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from jsonschema.exceptions import ValidationError
    from jsonschema.protocols import Validator

    from .models import PayloadBackend, SchemaCase


_REGEX_CLASS_ESCAPE = re.compile(r"(?<!\\)(?:\\\\)*\\([dDsSwWbB])")
_REGEX_CLASS_BY_ESCAPE = dict.fromkeys("dD", r"\d") | dict.fromkeys("sS", r"\s") | dict.fromkeys("wWbB", r"\w")
"""The character class each escape depends on; word boundaries depend on word characters."""
_REGEX_END_ANCHOR = re.compile(r"\\.|\[\^?\]?(?:\\.|[^\]\\])*\]|(?P<anchor>\$)")
"""An escape or a character class, which keep their ``$``, or the ``$`` anchor itself."""

_DECIMAL_DIGITS: Final = 28
"""The precision of the default Decimal context, in which the generated models check a decimal multipleOf."""
_DECIMAL_NUMBER_FORMATS: Final = frozenset(
    name for name, types in get_data_formats(is_openapi=True)["number"].items() if types is Types.decimal
)
"""The formats the generator types a number as ``Decimal`` for."""

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
    A number the generator types as ``Decimal`` keeps its quotient within the precision of the default Decimal
    context, in which the model checks multipleOf. The original schema remains the validation oracle. Intervals outside
    this sampling domain need a different strategy, even when their schema is valid.
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
        if node.get("format") in _DECIMAL_NUMBER_FORMATS:
            limit = min(limit, multiple * 10 ** (_DECIMAL_DIGITS - 1))
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


_LOW_BOUNDS: Final = ("minimum", "exclusiveMinimum")
_HIGH_BOUNDS: Final = ("maximum", "exclusiveMaximum")
_NUMBER_KEYWORDS: Final = (*_LOW_BOUNDS, *_HIGH_BOUNDS, "multipleOf")
"""The number keywords the generator applies to the strings of the numeric formats."""


def _is_number(value: object) -> bool:
    return type(value) in {int, float}


def _multiple_text(step: Decimal, quotient: int) -> str:
    """Write a whole multiple of a decimal step exactly."""
    with localcontext(prec=_DECIMAL_DIGITS + len(step.as_tuple().digits)):
        return str(quotient * step)


def _quotient_bound(keywords: dict[str, object], names: tuple[str, ...], step: Decimal) -> Iterator[Fraction]:
    """Yield the numeric bounds among the keywords, in multiples of the step."""
    return (Fraction(json.dumps(keywords[name])) / Fraction(step) for name in names if _is_number(keywords.get(name)))


def _number_validator(kind: str, keywords: dict[str, object]) -> Validator:
    """Return the validator of the number keywords, read in the draft their exclusive bounds are written for."""
    flags = any(isinstance(keywords.get(name), bool) for name in ("exclusiveMinimum", "exclusiveMaximum"))
    return (validators.Draft4Validator if flags else validators.Draft202012Validator)(
        _canonical_json_numbers({"type": kind, **keywords})
    )


def _number_or_text(value: object) -> object:
    """Return the number a string writes, read as the payload's texts are read; any other value, or text, as it is."""
    if isinstance(value, str):
        try:
            return _json_number(value)
        except (ValueError, ZeroDivisionError):
            return value
    return value


def _read_as_number(node: dict[str, object], kind: str, *, names_format: bool) -> None:
    """Make the node's ``string`` type the number kind and its listed values the numbers their texts write.

    The node that names the format has a type. ``enum`` members and ``const`` are read as the payload's texts are, so
    the projected node accepts exactly the numbers whose texts the string node accepts; a member that writes no number
    stays text, which the number kind rejects.
    """
    types = node.get("type")
    if types == "string":
        node["type"] = kind
    elif isinstance(types, list) and "string" in types:
        node["type"] = [kind if name == "string" else name for name in types]
    elif names_format and types is None:
        node["type"] = kind
    if isinstance(members := node.get("enum"), list):
        node["enum"] = [_number_or_text(member) for member in members]
    if "const" in node:
        node["const"] = _number_or_text(node["const"])


def numeric_text_schema(schema: object) -> object:
    """Return the schema with the strings of each numeric format read as the numbers they write.

    The generator applies every number keyword of such a string, also those of an ``allOf`` member beside the one that
    names the format, to the number its text writes, which the schema validator ignores for a string. The validator
    over this schema, given the numbers in place of the texts, decides them by all of their keywords, and a text that
    writes no number stays a string, which the number kind rejects. It reads no code of the generator's.
    """
    projected = deepcopy(schema)
    for node in _iter_schema_nodes(projected):
        members = [node, *(member for member in node.get("allOf", []) if isinstance(member, dict))]
        if names := [
            name for member in members if isinstance(name := member.get("format"), str) and name in NUMERIC_FORMAT_KINDS
        ]:
            for member in members:
                _read_as_number(member, NUMERIC_FORMAT_KINDS[names[0]], names_format=member.get("format") in names)
    return projected


def numeric_keywords(node: dict[str, object]) -> dict[str, object]:
    """Return the number keywords a schema sets, which the generator applies to the strings of a numeric format."""
    return {keyword: node[keyword] for keyword in _NUMBER_KEYWORDS if keyword in node}


def _number_texts(kind: str, keywords: dict[str, object]) -> st.SearchStrategy[str]:
    """Draw the text of the numbers that meet the number keywords, which the generator applies to these strings.

    A decimal multipleOf draws whole multiples whose quotient fits the precision of the default Decimal context; the
    unchanged keywords, read in the draft their exclusive bounds are written for, decide exactly which texts are kept.
    """
    exact = _number_validator(kind, keywords)
    multiple = keywords.get("multipleOf")
    if kind == "number" and isinstance(multiple, int | float) and not isinstance(multiple, bool) and multiple > 0:
        step = Decimal(json.dumps(multiple))
        reach = 10 ** (_DECIMAL_DIGITS - 1)
        low = max([-reach, *map(math.ceil, _quotient_bound(keywords, _LOW_BOUNDS, step))])
        high = min([reach, *map(math.floor, _quotient_bound(keywords, _HIGH_BOUNDS, step))])
        texts = st.integers(low, high).map(partial(_multiple_text, step))
    else:
        texts = from_schema({"type": kind, **keywords}).map(repr)
    return texts.filter(lambda text: exact.is_valid(_json_number(text)))


NUMERIC_FORMAT_KINDS: Final = {"decimal": "number", "integer": "integer", "unixtime": "integer"}
"""The string formats whose text writes a number, with the JSON kind the generator applies the number keywords as."""


def _iso_duration(value: timedelta) -> str:
    """Write a duration as ISO 8601 text, which Pydantic and msgspec read as a timedelta."""
    sign, magnitude = ("-", -value) if value < timedelta(0) else ("", value)
    return f"{sign}P{magnitude.days}DT{magnitude.seconds}.{magnitude.microseconds:06d}S"


def _ip_network(address: str, prefix: int) -> str:
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))


def _ip_networks(version: int, prefixes: int) -> st.SearchStrategy[str]:
    return st.builds(_ip_network, st.ip_addresses(v=version).map(str), st.integers(0, prefixes))


PAYLOAD_FORMAT_STRATEGIES: Final[
    dict[str, st.SearchStrategy[str] | Callable[[dict[str, object]], st.SearchStrategy[str]] | str]
] = {
    "byte": st.text().map(lambda text: base64.b64encode(text.encode()).decode()),
    "date": "hypothesis-jsonschema draws RFC 3339 full dates",
    "date-time": "hypothesis-jsonschema draws RFC 3339 date-times",
    "timestamp with time zone": from_schema({"type": "string", "format": "date-time"}),
    "date-time-local": st.datetimes().map(datetime.isoformat),
    "duration": st.timedeltas().map(_iso_duration),
    "time": "hypothesis-jsonschema draws RFC 3339 full times",
    "time-local": st.times().map(time.isoformat),
    "path": "every text is a path",
    "email": "hypothesis-jsonschema draws email addresses",
    "idn-email": "hypothesis-jsonschema draws email addresses",
    "uuid": st.uuids().map(str),
    **{f"uuid{version}": st.uuids(version=version).map(str) for version in range(1, 6)},
    "uri": "hypothesis-jsonschema draws https URLs",
    "hostname": "hypothesis-jsonschema draws domain names, which the generated hostname pattern matches",
    "ipv4": "hypothesis-jsonschema draws IPv4 addresses",
    "ipv6": "hypothesis-jsonschema draws IPv6 addresses",
    "ipv4-network": _ip_networks(4, 32),
    "ipv6-network": _ip_networks(6, 128),
    **{name: partial(_number_texts, kind) for name, kind in NUMERIC_FORMAT_KINDS.items()},
    "ulid": st.from_regex(r"[0-7][0-9A-HJKMNP-TV-Z]{25}", fullmatch=True),
    "binary": (
        "Pydantic and the dataclasses take any text as bytes; msgspec reads it as base64, which "
        "MSGSPEC_BINARY_FORMAT_EXCLUSION_REASON covers"
    ),
    "password": "every text is a secret string",
}
"""What each string format that a payload backend types as other than ``str`` draws.

A strategy, or for a numeric format the strategy its number keywords build; a text says why the strings
hypothesis-jsonschema draws for the format already suit those types. test_payload_format_strategies keeps the keys
equal to the formats the generator types as other than ``str``.
"""


_ABSENT_ALTERNATIVES: Final = frozenset({"None", "UnsetType"})
"""The alternatives an optional field adds beside the type of its value."""
_FIELD_LINE: Final = re.compile(r"^\s+(?P<name>field\d+): (?P<annotation>.+) = ", re.MULTILINE)
"""A field of the generated class: its name and its annotation, as the generator writes them without a formatter."""


@cache
def typed_string_formats(backend: PayloadBackend) -> frozenset[str]:
    """Return the string formats whose generated field the backend types as other than ``str``."""
    formats = [name for name in get_data_formats(is_openapi=True)["string"] if name != "default"]
    fields_by_name = {f"field{index}": name for index, name in enumerate(formats)}
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Payload",
        "type": "object",
        "properties": {field: {"type": "string", "format": name} for field, name in fields_by_name.items()},
    }
    output = generate(
        json.dumps(schema),
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType(backend.output_model_type),
        target_python_version=PythonVersion(PAYLOAD_TARGET_PYTHON_VERSION),
        formatters=[],
        disable_timestamp=True,
    )
    return frozenset(
        fields_by_name[field["name"]]
        for field in _FIELD_LINE.finditer(str(output))
        if {*field["annotation"].split(" | ")} - _ABSENT_ALTERNATIVES != {"str"}
    )


@cache
def _format_text(name: str, keywords: str) -> str | None:
    """Return the simplest text the format's strategy draws under the number keywords (a JSON text), if it has one."""
    entry = PAYLOAD_FORMAT_STRATEGIES.get(name)
    strategy = entry(json.loads(keywords)) if callable(entry) else entry
    if not isinstance(strategy, st.SearchStrategy):
        return None
    return find(
        strategy,
        lambda _: True,
        settings=settings(database=None, derandomize=True, suppress_health_check=list(HealthCheck), max_examples=200),
    )


def format_example(name: str, keywords: dict[str, object]) -> str | None:
    """Return the simplest text a payload takes for a string format that has a strategy, none for the others."""
    return _format_text(name, json.dumps(keywords, sort_keys=True))


def _payload_formats(schema: dict[str, object]) -> dict[str, st.SearchStrategy[str]]:
    """Give the formats of the sampling schema their strategies; a numeric one is named apart by its number keywords."""
    formats: dict[str, st.SearchStrategy[str]] = {}
    for node in _iter_schema_nodes(schema):
        if not isinstance(name := node.get("format"), str) or (
            (types := _schema_types(node)) and "string" not in types
        ):
            continue
        entry = PAYLOAD_FORMAT_STRATEGIES.get(name)
        if isinstance(entry, st.SearchStrategy):
            formats[name] = entry
        elif callable(entry):
            keywords = {keyword: node[keyword] for keyword in _NUMBER_KEYWORDS if keyword in node}
            if (named := f"{name} {json.dumps(keywords, sort_keys=True)}") not in formats:
                formats[named] = entry(keywords)
            node["format"] = named
    return formats


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
            strategy = from_schema(schema, custom_formats=_payload_formats(schema))
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
