"""Offline JSON Schema 2020-12 wire validation with exact numbers and RE2 patterns."""

from __future__ import annotations

import datetime
from collections.abc import Iterator, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, replace
from decimal import Decimal
from functools import cache
from importlib import import_module
from math import gcd
from typing import TYPE_CHECKING, Final, Literal, Protocol
from urllib.parse import urljoin

from typing_extensions import TypeIs

from .context import Direction  # noqa: TC001 - Public annotations support get_type_hints().
from .errors import CodecConfigurationError, CodecResourceLimitError, WireIssue
from .patterns import (
    MatchBudget,
    PatternDialectError,
    PatternPlan,
    PatternResourceError,
    compile_pattern,
    plan_pattern,
    search,
)
from .wire import JSONValue, WireValue, checked_key, checked_scalar, enter, escape_pointer_token, thaw_wire

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from jsonschema.exceptions import ValidationError

STANDARD_FORMATS: Final = (
    "date",
    "time",
    "date-time",
    "duration",
    "uuid",
    "ipv4",
    "ipv6",
    "email",
    "idn-email",
    "hostname",
    "idn-hostname",
    "uri",
    "uri-reference",
    "iri",
    "iri-reference",
    "uri-template",
    "json-pointer",
    "relative-json-pointer",
)
SCHEMA_VALUE_KEYWORDS: Final = frozenset({
    "additionalProperties",
    "contains",
    "contentSchema",
    "else",
    "if",
    "items",
    "not",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
})
SCHEMA_MAP_KEYWORDS: Final = frozenset({"$defs", "definitions", "dependentSchemas", "patternProperties", "properties"})
SCHEMA_ARRAY_KEYWORDS: Final = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_BASE64: Final = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")
_BASE64_QUANTUM: Final = 4
_BASE64_PADDING: Final = 2
_CLOCK_DIGITS: Final = 2
_DATE_WIDTHS: Final = [4, 2, 2]
_TIME_LIMITS: Final = (23, 59, 59)
_INTEGER_FORMATS: Final = {"int32": (-(2**31), 2**31 - 1), "int64": (-(2**63), 2**63 - 1)}
_NUMBER_TYPES: Final = frozenset({int, float, Decimal})
_EXCLUSIONS: Final[dict[Direction, str]] = {"request": "readOnly", "response": "writeOnly"}
_MESSAGES: Final = {
    "additionalProperties": "The property is not allowed by additionalProperties",
    "dependentRequired": "A property required by another present property is missing",
    "false": "No value is allowed at this location",
    "readOnly": "The value is read-only and cannot appear in a request",
    "required": "A required property is missing",
    "type": "The value does not have an allowed type",
    "unevaluatedProperties": "The property is not allowed by unevaluatedProperties",
    "writeOnly": "The value is write-only and cannot appear in a response",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaResource:
    """One normalized 2020-12 resource, with the pointers of the schema roots it contains."""

    uri: str
    contents: WireValue
    roots: tuple[str, ...] = ("",)


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaPatch:
    """Replace one bundled schema object's required keyword with its directional value."""

    uri: str
    pointer: str
    keyword: Literal["required", "dependentRequired"]
    value: WireValue


@dataclass(frozen=True, slots=True, kw_only=True)
class DirectionalView:
    """Relax required members excluded in one direction and assert its readOnly or writeOnly values."""

    direction: Direction
    patches: tuple[SchemaPatch, ...] = ()
    flagged: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Location:
    uri: str
    pointer: str
    base: str
    rejects_all: bool = False


@dataclass(frozen=True, slots=True)
class _CallState:
    plans: Mapping[str, PatternPlan]
    targets: Mapping[int, tuple[object, object]]
    budget: MatchBudget
    direction: Direction | None = None


_CALL: ContextVar[_CallState] = ContextVar("model_codec_schema_call")


class _KeywordValidator(Protocol):
    def descend(
        self,
        instance: object,
        schema: object,
        path: str | int | None = None,
        schema_path: str | int | None = None,
        resolver: object = None,
    ) -> Iterator[ValidationError]: ...

    def evolve(self, **changes: object) -> _KeywordValidator: ...

    def is_valid(self, instance: object) -> bool: ...

    def iter_errors(self, instance: object) -> Iterator[ValidationError]: ...


def _nesting() -> CodecResourceLimitError:
    return CodecResourceLimitError("The wire value is nested beyond the validator recursion limit")


def _number(value: object) -> object:
    return Decimal(repr(value)) if type(value) is float else value


def _instance_copy(value: JSONValue | WireValue, active: set[int]) -> object:
    if isinstance(value, (list, tuple)):
        identity = enter(value, active)
        copied = [_instance_copy(item, active) for item in value]
        active.discard(identity)
        return copied
    if isinstance(value, Mapping):
        identity = enter(value, active)
        items = {checked_key(key): _instance_copy(item, active) for key, item in value.items()}
        active.discard(identity)
        return items
    return _number(checked_scalar(value))


class _ResourceCopy:
    """Copy one resource into validator-owned containers, indexing every schema object."""

    def __init__(
        self,
        uri: str,
        index: dict[int, _Location],
        plans: dict[str, PatternPlan],
        schemas: dict[tuple[str, str], dict[str, object]],
    ) -> None:
        self.uri = uri
        self.index = index
        self.plans = plans
        self.schemas = schemas
        self.references: list[tuple[int, str, str]] = []
        self.active: set[int] = set()

    def copy(self, value: JSONValue | WireValue, pointer: str, base: str, *, schema: bool) -> object:
        match value:
            case False if schema:
                replacement: dict[str, object] = {"not": {}}
                self.index[id(replacement)] = _Location(self.uri, pointer, base, rejects_all=True)
                return replacement
            case Mapping() if schema:
                return self.schema(value, pointer, base)
            case Mapping():
                return self.members(value, pointer, lambda _, item, at: self.copy(item, at, base, schema=False))
            case tuple() | list():
                return self.items(value, pointer, lambda item, at: self.copy(item, at, base, schema=False))
            case _:
                return _number(checked_scalar(value))

    def members(
        self,
        value: Mapping[str, JSONValue] | Mapping[str, WireValue],
        pointer: str,
        copy: Callable[[str, JSONValue | WireValue, str], object],
    ) -> dict[str, object]:
        identity = enter(value, self.active)
        copied = {
            name: copy(name, item, f"{pointer}/{escape_pointer_token(name)}")
            for name, item in ((checked_key(key), item) for key, item in value.items())
        }
        self.active.discard(identity)
        return copied

    def items(
        self,
        value: Sequence[JSONValue | WireValue],
        pointer: str,
        copy: Callable[[JSONValue | WireValue, str], object],
    ) -> list[object]:
        identity = enter(value, self.active)
        copied = [copy(item, f"{pointer}/{index}") for index, item in enumerate(value)]
        self.active.discard(identity)
        return copied

    def member(self, keyword: str, value: JSONValue | WireValue, pointer: str, base: str) -> object:
        match value:
            case Mapping() if keyword in SCHEMA_MAP_KEYWORDS:
                return self.members(value, pointer, lambda _, item, at: self.copy(item, at, base, schema=True))
            case tuple() | list() if keyword in SCHEMA_ARRAY_KEYWORDS:
                return self.items(value, pointer, lambda item, at: self.copy(item, at, base, schema=True))
            case _:
                return self.copy(value, pointer, base, schema=keyword in SCHEMA_VALUE_KEYWORDS)

    def schema(
        self, value: Mapping[str, JSONValue] | Mapping[str, WireValue], pointer: str, base: str
    ) -> dict[str, object]:
        if isinstance(identifier := value.get("$id"), str):
            base = urljoin(base, identifier)
        copied = self.members(value, pointer, lambda name, item, at: self.member(name, item, at, base))
        self.index[id(copied)] = _Location(self.uri, pointer, base)
        self.schemas[self.uri, pointer] = copied
        if "$dynamicRef" in copied:
            msg = f"A dynamic schema reference at {self.uri} requires an explicit schema adapter"
            raise CodecConfigurationError(msg)
        if "$ref" in copied:
            if not isinstance(reference := copied["$ref"], str):
                msg = f"A bundled schema reference at {self.uri} must be a string"
                raise CodecConfigurationError(msg)
            self.references.append((id(copied), base, reference))
        if isinstance(source := copied.get("pattern"), str):
            self.plan(source)
        if _is_object(patterns := copied.get("patternProperties")):
            for source in patterns:
                self.plan(str(source))
        if "multipleOf" in copied and not _is_positive(copied["multipleOf"]):
            msg = f"A bundled schema at {self.uri} has a non-positive multipleOf"
            raise CodecConfigurationError(msg)
        return copied

    def plan(self, source: str) -> None:
        if source in self.plans:
            return
        try:
            planned = plan_pattern(source)
        except (PatternDialectError, PatternResourceError) as error:
            msg = f"A bundled schema pattern at {self.uri} is outside the builtin grammar"
            raise CodecConfigurationError(msg) from error
        compile_pattern(planned.re2_source)
        self.plans[source] = planned

    def container(self, value: JSONValue | WireValue, pointer: str, roots: frozenset[str]) -> object:
        if pointer in roots:
            return self.copy(value, pointer, self.uri, schema=True)
        match value:
            case Mapping():
                return self.members(value, pointer, lambda _, item, at: self.container(item, at, roots))
            case tuple() | list():
                return self.items(value, pointer, lambda item, at: self.container(item, at, roots))
            case _:
                return _number(checked_scalar(value))


def _is_resource_root(value: object) -> TypeIs[bool | dict[str, object]]:
    return isinstance(value, (bool, dict))


def _is_object(value: object) -> TypeIs[dict[str, object]]:
    return isinstance(value, dict)


def _is_array(value: object) -> TypeIs[list[object]]:
    return isinstance(value, list)


def _is_positive(value: object) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool) and value > 0


def _is_number(_: object, instance: object) -> bool:
    return type(instance) in _NUMBER_TYPES


def _is_integer(_: object, instance: object) -> bool:
    match instance:
        case bool():
            return False
        case int():
            return True
        case Decimal():
            return instance == instance.to_integral_value()
        case _:
            return False


def _digits(value: float | Decimal) -> tuple[int, int, int]:
    _, digits, exponent = (Decimal(repr(value)) if type(value) is float else Decimal(value)).as_tuple()
    return int(Decimal((0, digits, 0))), len(digits), int(exponent)


def is_multiple(value: float | Decimal, divisor: float | Decimal) -> bool:
    """Decide exact decimal divisibility by a positive divisor without large powers of ten."""
    value_digits, digit_count, value_exponent = _digits(value)
    divisor_digits, _, divisor_exponent = _digits(divisor)
    if not value_digits:
        return True
    if (shift := value_exponent - divisor_exponent) < 0:
        return -shift < digit_count and not value_digits % (divisor_digits * 10**-shift)
    remainder = divisor_digits // gcd(divisor_digits, value_digits)
    twos = (remainder & -remainder).bit_length() - 1
    remainder >>= twos
    fives = 0
    while not remainder % 5:
        remainder //= 5
        fives += 1
    return remainder == 1 and max(twos, fives) <= shift


def _matches(source: str, subject: str) -> bool:
    state = _CALL.get()
    return search(state.plans[source], subject, state.budget)


def _error(*, path: tuple[str, ...] = ()) -> ValidationError:
    from jsonschema.exceptions import ValidationError  # noqa: PLC0415

    return ValidationError("", path=path)


def _pattern(_: _KeywordValidator, pattern: str, instance: object, __: object) -> Iterator[ValidationError]:
    if isinstance(instance, str) and not _matches(pattern, instance):
        yield _error()


def _pattern_properties(
    validator: _KeywordValidator, patterns: Mapping[str, object], instance: object, _: object
) -> Iterator[ValidationError]:
    if not _is_object(instance):
        return
    for pattern, subschema in patterns.items():
        for name, value in instance.items():
            if _matches(pattern, name):
                yield from validator.descend(value, subschema, path=name, schema_path=pattern)


def _is_additional(name: str, schema: Mapping[str, object]) -> bool:
    if _is_object(properties := schema.get("properties")) and name in properties:
        return False
    patterns = schema.get("patternProperties")
    return not _is_object(patterns) or not any(_matches(pattern, name) for pattern in patterns)


def _additional_properties(
    validator: _KeywordValidator, additional: object, instance: object, schema: Mapping[str, object]
) -> Iterator[ValidationError]:
    if not _is_object(instance):
        return
    for name, value in instance.items():
        if _is_additional(name, schema):
            yield from validator.descend(value, additional, path=name)


def _required(_: _KeywordValidator, required: list[str], instance: object, __: object) -> Iterator[ValidationError]:
    if _is_object(instance):
        yield from (_error(path=(name,)) for name in required if name not in instance)


def _read_only(_: _KeywordValidator, flag: object, __: object, ___: object) -> Iterator[ValidationError]:
    if flag is True and _CALL.get().direction == "request":
        yield _error()


def _write_only(_: _KeywordValidator, flag: object, __: object, ___: object) -> Iterator[ValidationError]:
    if flag is True and _CALL.get().direction == "response":
        yield _error()


def _dependent_required(
    _: _KeywordValidator, dependencies: Mapping[str, list[str]], instance: object, __: object
) -> Iterator[ValidationError]:
    if not _is_object(instance):
        return
    for name, required in dependencies.items():
        if name in instance:
            yield from (_error(path=(member,)) for member in required if member not in instance)


def _multiple_of(
    _: _KeywordValidator, divisor: int | Decimal, instance: object, __: object
) -> Iterator[ValidationError]:
    if (
        isinstance(instance, (int, float, Decimal))
        and not isinstance(instance, bool)
        and not is_multiple(instance, divisor)
    ):
        yield _error()


def _is_valid(errors: Iterator[ValidationError]) -> bool:
    return next(errors, None) is None


def _reference(
    validator: _KeywordValidator, _: object, instance: object, schema: dict[str, object]
) -> Iterator[ValidationError]:
    contents, resolver = _CALL.get().targets[id(schema)]
    yield from validator.descend(instance, contents, resolver=resolver)


def _referenced_keys(validator: _KeywordValidator, instance: dict[str, object], schema: dict[str, object]) -> set[str]:
    if (target := _CALL.get().targets.get(id(schema))) is None:
        return set()
    contents, resolver = target
    return _evaluated_keys(validator.evolve(schema=contents, _resolver=resolver), instance, contents)


def _branch_keys(validator: _KeywordValidator, instance: dict[str, object], schema: dict[str, object]) -> set[str]:
    evaluated: set[str] = set()
    if _is_object(dependencies := schema.get("dependentSchemas")):
        for name, subschema in dependencies.items():
            if name in instance:
                evaluated |= _evaluated_keys(validator, instance, subschema)
    for keyword in ("allOf", "oneOf", "anyOf"):
        if _is_array(subschemas := schema.get(keyword)):
            for subschema in subschemas:
                if _is_valid(validator.descend(instance, subschema)):
                    evaluated |= _evaluated_keys(validator, instance, subschema)
    if "if" not in schema:
        return evaluated
    if validator.evolve(schema=schema["if"]).is_valid(instance):
        return (
            evaluated
            | _evaluated_keys(validator, instance, schema["if"])
            | _evaluated_keys(validator, instance, schema.get("then"))
        )
    return evaluated | _evaluated_keys(validator, instance, schema.get("else"))


def _evaluated_keys(validator: _KeywordValidator, instance: dict[str, object], schema: object) -> set[str]:
    if not _is_object(schema):
        return set()
    evaluated = _referenced_keys(validator, instance, schema) | _branch_keys(validator, instance, schema)
    if _is_object(properties := schema.get("properties")):
        evaluated.update(name for name in instance if name in properties)
    for keyword in ("additionalProperties", "unevaluatedProperties"):
        if (subschema := schema.get(keyword)) is not None:
            evaluated.update(name for name, value in instance.items() if _is_valid(validator.descend(value, subschema)))
    if _is_object(patterns := schema.get("patternProperties")):
        evaluated.update(name for name in instance if any(_matches(pattern, name) for pattern in patterns))
    return evaluated


def _applicable(validator: _KeywordValidator, instance: object, schema: dict[str, object]) -> Iterator[object]:
    if _is_array(members := schema.get("allOf")):
        yield from members
    for keyword in ("oneOf", "anyOf"):
        if _is_array(members := schema.get(keyword)):
            yield from (member for member in members if _is_valid(validator.descend(instance, member)))
    if "if" in schema:
        yield schema.get("then" if validator.evolve(schema=schema["if"]).is_valid(instance) else "else")
    if _is_object(dependencies := schema.get("dependentSchemas")) and _is_object(instance):
        yield from (subschema for name, subschema in dependencies.items() if name in instance)


def _member_schemas(schema: dict[str, object], name: str) -> Iterator[object]:
    if _is_object(properties := schema.get("properties")) and name in properties:
        yield properties[name]
    if _is_object(patterns := schema.get("patternProperties")):
        yield from (subschema for pattern, subschema in patterns.items() if _matches(pattern, name))
    if "additionalProperties" in schema and _is_additional(name, schema):
        yield schema["additionalProperties"]


def _item_schemas(schema: dict[str, object], index: int) -> Iterator[object]:
    prefix = schema.get("prefixItems")
    if _is_array(prefix) and index < len(prefix):
        yield prefix[index]
    elif "items" in schema:
        yield schema["items"]


@dataclass(slots=True)
class _Exclusions:
    flag: str
    pointers: list[str]


def _flagged(validator: _KeywordValidator, instance: object, schema: object, pointer: str, found: _Exclusions) -> None:
    if not _is_object(schema):
        return
    if schema.get(found.flag) is True:
        found.pointers.append(pointer)
        return
    if (target := _CALL.get().targets.get(id(schema))) is not None:
        contents, resolver = target
        _flagged(validator.evolve(schema=contents, _resolver=resolver), instance, contents, pointer, found)
    for subschema in _applicable(validator, instance, schema):
        _flagged(validator, instance, subschema, pointer, found)
    if _is_object(instance):
        for name, value in instance.items():
            for subschema in _member_schemas(schema, name):
                _flagged(validator, value, subschema, f"{pointer}/{escape_pointer_token(name)}", found)
    elif _is_array(instance):
        for index, value in enumerate(instance):
            for subschema in _item_schemas(schema, index):
                _flagged(validator, value, subschema, f"{pointer}/{index}", found)


def _unevaluated_properties(
    validator: _KeywordValidator, unevaluated: object, instance: object, schema: dict[str, object]
) -> Iterator[ValidationError]:
    if not _is_object(instance):
        return
    evaluated = _evaluated_keys(validator, instance, schema)
    for name, value in instance.items():
        if name not in evaluated:
            yield from validator.descend(value, unevaluated, path=name, schema_path=name)


def _is_byte(instance: object) -> bool:
    if type(instance) is not str:
        return True
    body = instance.rstrip("=")
    return (
        not len(instance) % _BASE64_QUANTUM
        and len(instance) - len(body) <= _BASE64_PADDING
        and all(char in _BASE64 for char in body)
    )


def _integer_format(name: str) -> Callable[[object], bool]:
    low, high = _INTEGER_FORMATS[name]

    def check(instance: object) -> bool:
        if not isinstance(instance, (int, float, Decimal)) or isinstance(instance, bool):
            return True
        return _is_integer(None, instance) and low <= instance <= high

    return check


def _is_ascii_digits(text: str) -> bool:
    return text.isascii() and text.isdigit()


def _is_local_time(text: str) -> bool:
    clock, dot, fraction = text.partition(".")
    fields = clock.split(":")
    return (
        len(fields) == len(_TIME_LIMITS)
        and all(
            len(value) == _CLOCK_DIGITS and _is_ascii_digits(value) and int(value) <= limit
            for value, limit in zip(fields, _TIME_LIMITS, strict=True)
        )
        and (not dot or _is_ascii_digits(fraction))
    )


def _is_date(text: str) -> bool:
    fields = text.split("-")
    if [len(value) for value in fields] != _DATE_WIDTHS or not all(map(_is_ascii_digits, fields)):
        return False
    try:
        datetime.date(*map(int, fields))
    except ValueError:
        return False
    return True


def _is_time_local(instance: object) -> bool:
    return type(instance) is not str or _is_local_time(instance)


def _is_date_time_local(instance: object) -> bool:
    if type(instance) is not str:
        return True
    date, separator, time = instance.partition("T") if "T" in instance else instance.partition("t")
    return bool(separator) and _is_date(date) and _is_local_time(time)


def _is_regex(instance: object) -> bool:
    if type(instance) is not str:
        return True
    try:
        plan_pattern(instance)
    except (PatternDialectError, PatternResourceError):
        return False
    return True


@cache
def _validator_factory() -> Callable[[object, object, object], _KeywordValidator]:
    from jsonschema import Draft202012Validator, FormatChecker  # noqa: PLC0415

    extend = import_module("jsonschema.validators").extend

    available = Draft202012Validator.FORMAT_CHECKER.checkers
    if missing := [name for name in STANDARD_FORMATS if name not in available]:
        msg = f"Format checkers are unavailable: {', '.join(missing)}"
        raise CodecConfigurationError(msg)
    checker = FormatChecker(())
    checker.checkers.update({name: available[name] for name in STANDARD_FORMATS})
    for name, function in (
        ("regex", _is_regex),
        ("byte", _is_byte),
        ("int32", _integer_format("int32")),
        ("int64", _integer_format("int64")),
        ("date-time-local", _is_date_time_local),
        ("time-local", _is_time_local),
    ):
        checker.checks(name)(function)
    validator = extend(
        Draft202012Validator,
        validators={
            "$ref": _reference,
            "additionalProperties": _additional_properties,
            "dependentRequired": _dependent_required,
            "multipleOf": _multiple_of,
            "pattern": _pattern,
            "patternProperties": _pattern_properties,
            "readOnly": _read_only,
            "required": _required,
            "unevaluatedProperties": _unevaluated_properties,
            "writeOnly": _write_only,
        },
        type_checker=Draft202012Validator.TYPE_CHECKER.redefine_many({"number": _is_number, "integer": _is_integer}),
        format_checker=checker,
    )

    def create(schema: object, registry: object, resolver: object) -> _KeywordValidator:
        created: _KeywordValidator = validator(schema, registry=registry, format_checker=checker, _resolver=resolver)
        return created

    return create


def _issue(error: ValidationError, index: Mapping[int, _Location]) -> WireIssue:
    location = index[id(error.schema)]
    keyword = "false" if location.rejects_all else str(error.validator)
    message = (
        f"The value does not match format {error.validator_value!r}"
        if keyword == "format"
        else _MESSAGES.get(keyword, f"The value does not satisfy {keyword}")
    )
    return WireIssue(
        code=f"schema.{keyword}",
        message=message[:1024],
        instance_pointer="".join(f"/{escape_pointer_token(part)}" for part in error.absolute_path),
        schema_id=location.uri,
        schema_pointer=location.pointer
        if location.rejects_all
        else f"{location.pointer}/{escape_pointer_token(keyword)}",
    )


class WireSchemaValidator:
    """Validate one use's wire values against its bundled offline schema."""

    __slots__ = ("_exclusion", "_index", "_state", "_validator", "schema_id")

    def __init__(
        self,
        schema_id: str,
        validator: _KeywordValidator,
        index: Mapping[int, _Location],
        state: _CallState,
        exclusion: tuple[object, str] | None = None,
    ) -> None:
        """Keep the bundle-owned validator, identity index, pattern plans, and reference targets."""
        self.schema_id = schema_id
        self._validator = validator
        self._index = index
        self._state = state
        self._exclusion = exclusion

    def excluded(self, wire: WireValue, *, budget: MatchBudget | None = None) -> tuple[str, ...]:
        """Return pointers of values annotated as excluded in this direction, following only valid branches."""
        if self._exclusion is None:
            return ()
        root, flag = self._exclusion
        token = _CALL.set(replace(self._state, budget=budget or MatchBudget(), direction=None))
        found = _Exclusions(flag, [])
        try:
            _flagged(self._validator, _instance_copy(wire, set()), root, "", found)
        except RecursionError:
            raise _nesting() from None
        finally:
            _CALL.reset(token)
        return tuple(found.pointers)

    def validate(self, wire: WireValue, *, budget: MatchBudget | None = None) -> tuple[WireIssue, ...]:
        """Return value-free issues in schema-evaluation order; an empty tuple means valid."""
        token = _CALL.set(replace(self._state, budget=budget or MatchBudget()))
        try:
            instance = _instance_copy(wire, set())
            return tuple(_issue(error, self._index) for error in self._validator.iter_errors(instance))
        except RecursionError:
            raise _nesting() from None
        finally:
            _CALL.reset(token)


class SchemaBundle:
    """Own offline normalized resources, their pattern plans, and per-use validators."""

    __slots__ = ("_direction", "_flagged", "_index", "_plans", "_registry", "_targets", "_validators")

    def __init__(self, resources: Iterable[SchemaResource], view: DirectionalView | None = None) -> None:
        """Copy resources, apply a directional view, check every reference and pattern, and build a registry."""
        from referencing.exceptions import Unresolvable  # noqa: PLC0415
        from referencing.jsonschema import DRAFT202012, EMPTY_REGISTRY  # noqa: PLC0415

        self._index: dict[int, _Location] = {}
        self._plans: dict[str, PatternPlan] = {}
        self._validators: dict[str, WireSchemaValidator] = {}
        self._direction: Direction | None = None if view is None else view.direction
        self._flagged = frozenset(() if view is None else view.flagged)
        schemas: dict[tuple[str, str], dict[str, object]] = {}
        copies: dict[str, bool | dict[str, object]] = {}
        references: list[tuple[str, int, str, str]] = []
        for resource in resources:
            if resource.uri in copies:
                msg = f"A schema resource is bundled twice: {resource.uri}"
                raise CodecConfigurationError(msg)
            copier = _ResourceCopy(resource.uri, self._index, self._plans, schemas)
            root = (
                copier.copy(resource.contents, "", resource.uri, schema=True)
                if "" in resource.roots
                else copier.container(resource.contents, "", frozenset(resource.roots))
            )
            if not _is_resource_root(root):
                msg = f"A schema resource root must be a boolean or an object: {resource.uri}"
                raise CodecConfigurationError(msg)
            copies[resource.uri] = root
            references.extend((resource.uri, *reference) for reference in copier.references)
        for patch in () if view is None else view.patches:
            if (patched := schemas.get((patch.uri, patch.pointer))) is None or patch.keyword not in patched:
                msg = f"A directional view patches no bundled {patch.keyword} keyword in {patch.uri}"
                raise CodecConfigurationError(msg)
            patched[patch.keyword] = thaw_wire(patch.value)
        self._registry = EMPTY_REGISTRY.with_resources(
            (uri, DRAFT202012.create_resource(contents)) for uri, contents in copies.items()
        ).crawl()
        self._targets: dict[int, tuple[object, object]] = {}
        for uri, schema, base, reference in references:
            try:
                target = self._registry.resolver(base_uri=base).lookup(reference)
            except Unresolvable as error:
                msg = f"A bundled schema reference cannot be resolved offline from {uri}"
                raise CodecConfigurationError(msg) from error
            if target.contents is not True and id(target.contents) not in self._index:
                msg = f"A bundled schema reference from {uri} does not target a schema"
                raise CodecConfigurationError(msg)
            self._targets[schema] = (target.contents, target.resolver)

    @property
    def direction(self) -> Direction | None:
        """Return the direction whose view this bundle validates, or None for the neutral view."""
        return self._direction

    def validator(self, schema_id: str) -> WireSchemaValidator:
        """Return the cached validator for one bundled schema identifier."""
        if (existing := self._validators.get(schema_id)) is not None:
            return existing
        from referencing.exceptions import Unresolvable  # noqa: PLC0415

        try:
            target = self._registry.resolver().lookup(schema_id)
        except Unresolvable as error:
            msg = f"Schema {schema_id} is not bundled"
            raise CodecConfigurationError(msg) from error
        if (root := self._index.get(id(target.contents))) is None and target.contents is not True:
            msg = f"Schema {schema_id} does not identify a bundled schema"
            raise CodecConfigurationError(msg)
        base = schema_id.partition("#")[0] if root is None else root.base
        created = WireSchemaValidator(
            schema_id,
            _validator_factory()(target.contents, self._registry, self._registry.resolver(base_uri=base)),
            self._index,
            _CallState(self._plans, self._targets, MatchBudget(), self._direction),
            (target.contents, _EXCLUSIONS[self._direction])
            if self._direction is not None and schema_id in self._flagged
            else None,
        )
        self._validators[schema_id] = created
        return created
