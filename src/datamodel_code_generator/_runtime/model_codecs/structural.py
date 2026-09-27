"""Structural model codecs: converters built from the final annotations of standard library model types."""

from __future__ import annotations

import dataclasses
import operator
import re
import sys
import typing
from collections import abc
from collections.abc import Callable, Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from functools import partial
from math import isfinite
from types import MappingProxyType, NoneType, UnionType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Final,
    Literal,
    Protocol,
    TypeAlias,
    TypeVar,
    Union,
    overload,
)

import typing_extensions

from .bindings import ArrayNode, FieldBinding, LeafNode, MapNode, ModelBinding, ModelNode, TupleNode, UnionNode
from .codec import (
    EMPTY,
    BuiltinModelCodec,
    Walk,
    child_value,
    directional_gap,
    is_mapping,
    is_sequence,
    is_set,
    selected,
    shape_error,
)
from .errors import CodecConfigurationError, ModelProjectionError, NativeIssue, NativeValidationError
from .media import encode_json
from .patterns import PatternDialectError, PatternPlan, PatternResourceError, plan_pattern, search
from .values import DecodedValue, ModelInput, ModelValue, ProjectionIssue
from .wire import (
    JSONValue,
    PresenceTree,
    WireValue,
    check_array_presence,
    check_object_presence,
    checked_key,
    checked_scalar,
    escape_pointer_token,
    snapshot_presence,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Set as AbstractSet

    from .bindings import Representation, TypeNode, UseBinding
    from .patterns import MatchBudget
    from .schema import SchemaBundle, WireValidator

    _Check: TypeAlias = tuple[Callable[[Any, MatchBudget], bool], "_Failure"]

T = TypeVar("T")

_STRATEGIES: Final = {
    "dataclass_structural": "dataclasses.dataclass",
    "typeddict_structural": "typing.TypedDict",
    "msgspec_structural": "msgspec.Struct",
    "msgspec_convert": "msgspec.Struct",
}
_ALIASES: Final[tuple[type[typing_extensions.TypeAliasType], ...]] = tuple({
    typing_extensions.TypeAliasType,
    getattr(typing, "TypeAliasType", typing_extensions.TypeAliasType),
})
_SEQUENCES: Final[dict[object, type]] = {list: list, set: set, frozenset: frozenset, abc.Sequence: list}
_MAPPINGS: Final = frozenset({dict, abc.Mapping})
_CONTAINERS: Final[dict[type, str]] = {list: "list", set: "set", frozenset: "frozenset"}
_CLOCK: Final = r"([0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]+))?([Zz]|[+-][0-9]{2}:[0-9]{2})"
_DATE_TEXT: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TIME_TEXT: Final = re.compile(_CLOCK)
_DATETIME_TEXT: Final = re.compile(rf"([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}})[Tt]{_CLOCK}")
_DECIMAL_TEXT: Final = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_AMOUNT: Final = r"([0-9]+(?:[.,][0-9]+)?)"
_DURATION_TEXT: Final = re.compile(
    rf"([+-]?)P(?:{_AMOUNT}Y)?(?:{_AMOUNT}M)?(?:{_AMOUNT}W)?(?:{_AMOUNT}D)?"
    rf"(?:T(?=[0-9])(?:{_AMOUNT}H)?(?:{_AMOUNT}M)?(?:{_AMOUNT}S)?)?"
)
_MICROSECONDS: Final = (7 * 86_400_000_000, 86_400_000_000, 3_600_000_000, 60_000_000, 1_000_000)
_LONGEST: Final = timedelta.max // timedelta(microseconds=1)
_MINUTE: Final = timedelta(minutes=1)
_ABSENT: Final = object()
_QUALIFIERS: Final = frozenset({typing_extensions.Required, typing_extensions.NotRequired, typing_extensions.ReadOnly})

_JSONKey: TypeAlias = tuple[str, object]
_Route: TypeAlias = "tuple[_Route, str | int, str | int] | None"


@dataclasses.dataclass(frozen=True, slots=True)
class _Failure:
    """A converter's refusal of a wire value, as a native issue code.

    ``outside`` marks a value outside the wire domain of the leaf, which a union passes on to its next member;
    a value inside the domain that the native type cannot represent fails without trying another member.
    """

    code: str
    outside: bool = True


_STRING_TYPE: Final = _Failure("native.string_type")
_FLOAT_OVERFLOW: Final = _Failure("native.float_overflow", outside=False)
_DECIMAL_PARSING: Final = _Failure("native.decimal_parsing", outside=False)
_DURATION_SYNTAX: Final = _Failure("native.duration")
_DURATION: Final = _Failure("native.duration", outside=False)
_ENUM: Final = _Failure("native.enum")
_LITERAL: Final = _Failure("native.literal_error")
_UNHASHABLE: Final = _Failure("native.set_item_not_hashable")
_CONSTRUCTOR: Final = _Failure("native.constructor")
_EXTRA_FORBIDDEN: Final = _Failure("native.extra_forbidden")
_TAG: Final = _Failure("native.tag")
_KIND_FAILURES: Final = {
    "dataclass": _Failure("native.dataclass_type"),
    "typed_dict": _Failure("native.dict_type"),
    "struct": _Failure("native.struct_type"),
}
_BOUNDS: Final = (("gt", operator.gt), ("ge", operator.ge), ("lt", operator.lt), ("le", operator.le))
_MSGSPEC_PATH: Final = re.compile(r"\[(\d+|\.\.\.)\]")
_STRINGS: Final = (
    ("uuid", ("UUID",), _Failure("native.uuid")),
    ("ipaddress", ("IPv4Address", "IPv6Address"), _Failure("native.ip_address")),
    ("ipaddress", ("IPv4Network", "IPv6Network"), _Failure("native.ip_network", outside=False)),
    ("pathlib", ("PurePath",), _Failure("native.path", outside=False)),
)


def _json_key(value: object) -> _JSONKey:
    match value:
        case bool():
            return ("boolean", value)
        case int() | float() | Decimal():
            return ("number", value)
        case _:
            pass
    return (type(value).__name__, value)


def _located(route: _Route) -> tuple[str, tuple[str | int, ...]]:
    """Return the wire pointer and native path that a decoding route reached."""
    tokens: list[str | int] = []
    names: list[str | int] = []
    while route is not None:
        route, token, name = route
        tokens.append(token)
        names.append(name)
    return "".join(f"/{escape_pointer_token(token)}" for token in reversed(tokens)), tuple(reversed(names))


def _unwrap(annotation: object) -> tuple[object, tuple[object, ...]]:
    """Return an annotation without its aliases, Annotated layers and TypedDict qualifiers, and the layers' metadata."""
    metadata: tuple[object, ...] = ()
    while True:
        if isinstance(annotation, _ALIASES):
            annotation = annotation.__value__
        elif (origin := typing_extensions.get_origin(annotation)) is Annotated:
            annotation, *extra = typing_extensions.get_args(annotation)
            metadata = (*metadata, *extra)
        elif origin in _QUALIFIERS:
            annotation = typing_extensions.get_args(annotation)[0]
        else:
            return annotation, metadata


def _absent(member: object) -> bool:
    """Return whether a union member marks a missing value rather than a type: None, or msgspec's UnsetType."""
    return member is NoneType or member is getattr(sys.modules.get("msgspec"), "UnsetType", NoneType)


def _bounded(compare: Callable[[Any, Any], bool], bound: object, value: object, _: MatchBudget) -> bool:
    return compare(value, bound)


def _multiple(step: float, value: float, _: MatchBudget) -> bool:
    return value % step == 0


def _searched(plan: PatternPlan, value: str, budget: MatchBudget) -> bool:
    return search(plan, value, budget)


def _longer(least: int, value: abc.Sized, _: MatchBudget) -> bool:
    return len(value) >= least


def _shorter(most: int, value: abc.Sized, _: MatchBudget) -> bool:
    return len(value) <= most


def _zoned(aware: bool, value: datetime | time, _: MatchBudget) -> bool:  # noqa: FBT001
    return (value.tzinfo is not None) == aware


def _checks(metadata: tuple[object, ...]) -> tuple[_Check, ...]:
    """Return the checks of the msgspec Meta constraints among an annotation's metadata."""
    meta = getattr(sys.modules.get("msgspec"), "Meta", None)
    return tuple(
        check for item in metadata if meta is not None and isinstance(item, meta) for check in _meta_checks(item)
    )


def _meta_checks(item: Any) -> list[_Check]:
    """Return the checks of one msgspec Meta, whose type is loaded only with msgspec."""
    checks: list[_Check] = [
        (partial(_bounded, compare, bound), _Failure(f"native.{name}"))
        for name, compare in _BOUNDS
        if (bound := getattr(item, name)) is not None
    ]
    if item.multiple_of is not None:
        checks.append((partial(_multiple, item.multiple_of), _Failure("native.multiple_of")))
    if item.pattern is not None:
        checks.append((partial(_searched, plan_pattern(item.pattern)), _Failure("native.pattern")))
    if item.min_length is not None:
        checks.append((partial(_longer, item.min_length), _Failure("native.min_length")))
    if item.max_length is not None:
        checks.append((partial(_shorter, item.max_length), _Failure("native.max_length")))
    if item.tz is not None:
        checks.append((partial(_zoned, item.tz), _Failure("native.tz")))
    return checks


def _union_members(annotation: object) -> tuple[object, ...] | None:
    """Return a union's members with the members of aliased unions in place of the aliases, as Python flattens them."""
    if typing.get_origin(annotation) not in {Union, UnionType}:
        return None
    return tuple(member for item in typing.get_args(annotation) for member in _aliased(item))


def _aliased(item: object) -> tuple[object, ...]:
    """Return the members of a union behind an alias without metadata, or else the member itself."""
    annotation, metadata = _unwrap(item)
    return (item,) if metadata or (members := _union_members(annotation)) is None else members


def _flattened(node: UnionNode) -> tuple[list[TypeNode], bool]:
    """Return a union node's members with nested unions in place, and whether any of them admits null."""
    items: list[TypeNode] = []
    nullable = node.nullable
    for member in node.members:
        match member:
            case UnionNode():
                nested, optional = _flattened(member)
                items.extend(nested)
                nullable |= optional
            case _:
                items.append(member)
    return items, nullable


def _string_failure(kind: type) -> _Failure | None:
    for module, names, failure in _STRINGS:
        if (loaded := sys.modules.get(module)) is not None and issubclass(
            kind, tuple(getattr(loaded, name) for name in names)
        ):
            return failure
    return None


def _stringified(value: object) -> bool:
    return any(
        (loaded := sys.modules.get(module)) is not None
        and isinstance(value, tuple(getattr(loaded, name) for name in names))
        for module, names, _ in _STRINGS
    )


def _thawed(wire: WireValue) -> JSONValue:
    """Copy a frozen snapshot into lists and dictionaries, without the checks that freezing it already made."""
    match wire:
        case tuple():
            return [_thawed(item) for item in wire]
        case str() | int() | float() | Decimal() | None:
            return wire
        case _:
            pass
    return {key: _thawed(item) for key, item in wire.items()}


def _same(wire: WireValue) -> WireValue:
    return wire


def _parsed(parse: Callable[[str], object], failure: _Failure) -> Callable[[str], object]:
    def convert(text: str) -> object:
        try:
            return parse(text)
        except ValueError:
            return failure

    return convert


def _number(wire: float | Decimal) -> float | _Failure:
    try:
        value = float(wire)
    except OverflowError:
        return _FLOAT_OVERFLOW
    return value if isfinite(value) else _FLOAT_OVERFLOW


def _decimal(wire: float | Decimal) -> Decimal:
    return Decimal(repr(wire)) if isinstance(wire, float) else Decimal(wire)


def _decimal_text(text: str) -> Decimal | _Failure:
    return _DECIMAL_PARSING if _DECIMAL_TEXT.fullmatch(text) is None else Decimal(text)


def _clock(parts: re.Match[str], start: int) -> str:
    clock, fraction, offset = parts.group(start, start + 1, start + 2)
    return f"{clock}.{(fraction or '')[:6].ljust(6, '0')}{'+00:00' if offset in {'Z', 'z'} else offset or ''}"


def _iso_datetime(text: str) -> datetime:
    if (parts := _DATETIME_TEXT.fullmatch(text)) is None:
        raise ValueError
    return datetime.fromisoformat(f"{parts[1]}T{_clock(parts, 2)}")


def _iso_date(text: str) -> date:
    if _DATE_TEXT.fullmatch(text) is None:
        raise ValueError
    return date.fromisoformat(text)


def _iso_time(text: str) -> time:
    if (parts := _TIME_TEXT.fullmatch(text)) is None:
        raise ValueError
    return time.fromisoformat(_clock(parts, 1))


def _duration(text: str) -> timedelta | _Failure:
    if (parts := _DURATION_TEXT.fullmatch(text)) is None or not any(parts.groups()[1:]):
        return _DURATION_SYNTAX
    years, months, *amounts = (Decimal(part.replace(",", ".")) if part else None for part in parts.groups()[1:])
    total = sum((amount * unit for amount, unit in zip(amounts, _MICROSECONDS, strict=True) if amount), Decimal(0))
    if years or months or total > _LONGEST:
        return _DURATION
    return -timedelta(microseconds=int(total)) if parts[1] == "-" else timedelta(microseconds=int(total))


def _iso_duration(value: timedelta) -> str:
    total = value // timedelta(microseconds=1)
    days, rest = divmod(abs(total), 86_400_000_000)
    hours, rest = divmod(rest, 3_600_000_000)
    minutes, rest = divmod(rest, 60_000_000)
    seconds, micros = divmod(rest, 1_000_000)
    clock = "".join((
        f"{hours}H" if hours else "",
        f"{minutes}M" if minutes else "",
        f"{seconds}{f'.{micros:06d}'.rstrip('0') if micros else ''}S" if seconds or micros else "",
    ))
    body = f"{f'{days}D' if days else ''}{f'T{clock}' if clock else ''}" or "T0S"
    return f"{'-' if total < 0 else ''}P{body}"


def _member(members: Mapping[_JSONKey, object], failure: _Failure) -> Callable[[WireValue], object]:
    def convert(wire: WireValue) -> object:
        return members.get(_json_key(wire), failure)

    return convert


def _members(values: tuple[object, ...]) -> dict[_JSONKey, object]:
    return {_json_key(value.value if isinstance(value, Enum) else value): value for value in values}


def _accepts_string(wire: WireValue) -> bool:
    return type(wire) is str


def _accepts_integer(wire: WireValue) -> bool:
    match wire:
        case bool():
            return False
        case int():
            return True
        case float():
            return wire.is_integer()
        case Decimal():
            return wire == wire.to_integral_value()
        case _:
            pass
    return False


def _accepts_number(wire: WireValue) -> bool:
    return not isinstance(wire, bool) and isinstance(wire, (int, float, Decimal))


def _accepts_boolean(wire: WireValue) -> bool:
    return isinstance(wire, bool)


def _accepts_null(wire: WireValue) -> bool:
    return wire is None


def _accepts_scalar(wire: WireValue) -> bool:
    return not isinstance(wire, (tuple, Mapping))


def _accepts_any(_: WireValue) -> bool:
    return True


@dataclasses.dataclass(frozen=True, slots=True)
class _Leaf:
    accepts: Callable[[WireValue], bool]
    convert: Callable[[Any], object]
    failure: _Failure
    native: type | None = None
    representation: Representation = "value"
    checks: tuple[_Check, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class _Sequence:
    item: _Plan
    container: type
    failure: _Failure
    checks: tuple[_Check, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class _Tuple:
    items: tuple[_Plan, ...]
    failure: ClassVar[_Failure] = _Failure("native.tuple_type")


@dataclasses.dataclass(frozen=True, slots=True)
class _Map:
    key: Callable[[str], object]
    value: _Plan
    checks: tuple[_Check, ...] = ()
    failure: ClassVar[_Failure] = _Failure("native.dict_type")


@dataclasses.dataclass(frozen=True, slots=True)
class _Union:
    members: tuple[_Plan, ...]
    nullable: bool
    failure: ClassVar[_Failure] = _Failure("native.union")


@dataclasses.dataclass(frozen=True, slots=True)
class _Field:
    binding: FieldBinding
    plan: _Plan


@dataclasses.dataclass(slots=True)
class _Model:
    """A dataclass or msgspec Struct built by its constructor, or a TypedDict (``record``), the dict of its keys.

    ``unset`` is the value that leaves a field out of the wire, and ``tag`` a tagged Struct's tag field and value.
    """

    binding: ModelBinding
    native: type
    record: bool
    failure: _Failure
    fields: tuple[_Field, ...] = ()
    wire: Mapping[str, _Field] = dataclasses.field(default_factory=dict[str, _Field])
    required: tuple[_Field, ...] = ()
    keys: frozenset[str] = frozenset()
    extras: _Plan | None = None
    unset: object = _ABSENT
    tag: tuple[str, object] | None = None


class _TypedDictClass(Protocol):
    __total__: bool
    __extra_items__: object


class _StructConfig(Protocol):
    tag_field: str | None
    tag: object


class _StructClass(Protocol):
    __struct_config__: _StructConfig


_Plan: TypeAlias = _Leaf | _Sequence | _Tuple | _Map | _Union | _Model

_ANY: Final = _Leaf(_accepts_any, _thawed, _STRING_TYPE)
_DECIMAL_STRING: Final = _Leaf(_accepts_string, _decimal_text, _STRING_TYPE, Decimal, "decimal_string")
_PLAIN: Final[dict[type, _Leaf]] = {
    str: _Leaf(_accepts_string, _same, _STRING_TYPE, str),
    int: _Leaf(_accepts_integer, int, _Failure("native.int_type"), int),
    float: _Leaf(_accepts_number, _number, _Failure("native.float_type"), float),
    bool: _Leaf(_accepts_boolean, _same, _Failure("native.bool_type"), bool),
    NoneType: _Leaf(_accepts_null, _same, _Failure("native.none_required"), NoneType),
    Decimal: _Leaf(_accepts_number, _decimal, _Failure("native.decimal_type"), Decimal),
    datetime: _Leaf(_accepts_string, _parsed(_iso_datetime, _Failure("native.datetime")), _STRING_TYPE, datetime),
    date: _Leaf(_accepts_string, _parsed(_iso_date, _Failure("native.date")), _STRING_TYPE, date),
    time: _Leaf(_accepts_string, _parsed(_iso_time, _Failure("native.time")), _STRING_TYPE, time),
    timedelta: _Leaf(_accepts_string, _duration, _STRING_TYPE, timedelta),
}


def _leaf(annotation: object, representation: Representation) -> _Leaf | None:  # noqa: PLR0911
    match annotation:
        case _ if annotation is Any or annotation is object:
            return _ANY
        case _ if typing.get_origin(annotation) is Literal:
            return _Leaf(_accepts_scalar, _member(_members(typing.get_args(annotation)), _LITERAL), _LITERAL)
        case type() if issubclass(annotation, Enum):
            return _Leaf(_accepts_scalar, _member(_members(tuple(annotation)), _ENUM), _ENUM, annotation)
        case type() if annotation is Decimal and representation == "decimal_string":
            return _DECIMAL_STRING
        case type() if (plain := _PLAIN.get(annotation)) is not None:
            return plain
        case type() if (failure := _string_failure(annotation)) is not None:
            return _Leaf(_accepts_string, _parsed(annotation, failure), _STRING_TYPE, annotation)
        case _:
            pass
    return None


def _required(item: dataclasses.Field[object]) -> bool:
    return item.init and item.default is dataclasses.MISSING and item.default_factory is dataclasses.MISSING


def _fits_dataclass(binding: ModelBinding, native: type) -> bool:
    """Return whether a native dataclass agrees with every bound field and binds every required argument."""
    if not dataclasses.is_dataclass(native):
        return False
    declared = {item.name: item for item in dataclasses.fields(native)}
    planned = {member.native_name: member for member in binding.fields}
    return all(
        (item := declared.get(name)) is not None
        and item.init == member.constructible
        and _required(item) == member.required
        for name, member in planned.items()
    ) and all(name in planned for name, item in declared.items() if _required(item))


def _qualifiers(hint: object) -> set[object]:
    found: set[object] = set()
    while (origin := typing_extensions.get_origin(hint)) in _QUALIFIERS:
        found.add(origin)
        hint = typing_extensions.get_args(hint)[0]
    return found


def _extra_items(native: type) -> object:
    """Return the evaluated type of a TypedDict's extra items, which may be written as a string."""
    extra_items = typing.cast("_TypedDictClass", native).__extra_items__
    if isinstance(extra_items, str):
        module = vars(sys.modules[native.__module__])
        return typing_extensions.evaluate_forward_ref(typing.ForwardRef(extra_items), globals=module)
    return extra_items


def _record_required(native: type) -> set[str]:
    """Return the required keys of a TypedDict, each read under the totality of the TypedDict that declares it.

    The annotations are read rather than ``__required_keys__``, which misses the qualifiers of postponed ones.
    """
    required: set[str] = set()
    inherited: set[str] = set()
    for base in getattr(native, "__orig_bases__", ()):
        if typing_extensions.is_typeddict(base):
            required |= _record_required(base)
            inherited |= typing_extensions.get_type_hints(base).keys()
    total = typing.cast("_TypedDictClass", native).__total__
    return required | {
        key
        for key, hint in typing_extensions.get_type_hints(native, include_extras=True).items()
        if key not in inherited
        and (
            typing_extensions.Required in (found := _qualifiers(hint))
            or (total and typing_extensions.NotRequired not in found)
        )
    }


def _fits_struct(binding: ModelBinding, native: type) -> bool:
    """Return whether a msgspec Struct declares every bound field under its wire name and binds each required one."""
    import msgspec  # noqa: PLC0415 - Only the msgspec backend loads msgspec.

    if not issubclass(native, msgspec.Struct):
        return False
    declared = {item.name: item for item in msgspec.structs.fields(native)}
    planned = {member.native_name: member for member in binding.fields}
    return (
        all(
            (item := declared.get(name)) is not None
            and item.encode_name == member.wire_name
            and item.required == member.required
            for name, member in planned.items()
        )
        and all(name in planned for name, item in declared.items() if item.required)
        and _struct_tag(native) == binding.tag
    )


def _struct_tag(native: type) -> tuple[str, object] | None:
    config = typing.cast("_StructClass", native).__struct_config__
    return None if config.tag_field is None else (config.tag_field, config.tag)


def _fits_record(binding: ModelBinding, native: type) -> bool:
    """Return whether a TypedDict declares the bound keys, their requiredness, closure, and extra items."""
    if not typing_extensions.is_typeddict(native):
        return False
    return (
        {member.native_name for member in binding.fields} <= typing_extensions.get_type_hints(native).keys()
        and _record_required(native) == {member.native_name for member in binding.fields if member.required}
        and (binding.extra == "forbid") == (getattr(native, "__closed__", None) is True)
        and (binding.extra_items is None)
        == (getattr(native, "__extra_items__", typing_extensions.NoExtraItems) is typing_extensions.NoExtraItems)
    )


class StructuralModelCodec(BuiltinModelCodec[T]):
    """Validate, construct, snapshot, and encode one bound use of a standard dataclass or TypedDict model type."""

    __slots__ = ("_convert", "_invalid", "_plan", "_plans", "_scan")

    @overload
    def __init__(
        self,
        binding: UseBinding,
        native_type: type[T],
        models: Mapping[str, type],
        bundle: SchemaBundle,
        *,
        validator: WireValidator | None = None,
    ) -> None: ...
    @overload
    def __init__(
        self,
        binding: UseBinding,
        native_type: object,
        models: Mapping[str, type],
        bundle: SchemaBundle,
        *,
        validator: WireValidator | None = None,
    ) -> None: ...
    def __init__(
        self,
        binding: UseBinding,
        native_type: object,
        models: Mapping[str, type],
        bundle: SchemaBundle,
        *,
        validator: WireValidator | None = None,
    ) -> None:
        """Build the converters of the binding's type graph from the final annotations, before any value is processed.

        A schema adapter's validator replaces the bundle's builtin validator of the use's schema.
        """
        super().__init__(
            binding,
            bundle,
            validator,
            selects=_STRATEGIES.get(binding.converter_strategy) == binding.backend,
            converter="a structural converter",
        )
        self._types = {}
        for model in binding.models:
            if not isinstance(native := models.get(model.symbol), type):
                raise self._mismatch(model.symbol)
            self._types[model.symbol] = native
        self._plans: dict[str, _Model] = {}
        self._plan = self._build(binding.type, native_type, binding.native_export or binding.schema_id)
        self._scan = binding.projection_mode == "envelope"
        self._convert: Callable[[JSONValue], object] | None = None
        self._invalid: type[Exception] = ModelProjectionError
        if binding.converter_strategy == "msgspec_convert":
            self._convert, self._invalid = self._converter(native_type)

    def _converter(self, native_type: object) -> tuple[Callable[[JSONValue], object], type[Exception]]:
        """Return msgspec's strict converter of the use's type, which msgspec must describe at startup.

        Describing the type walks all of it, so a union msgspec refuses or a type it cannot read from JSON stops here.
        """
        import msgspec  # noqa: PLC0415 - Only the msgspec backend loads msgspec.

        try:
            msgspec.json.schema(native_type)
        except TypeError:
            raise self._mismatch(self._binding.native_export or self._binding.schema_id) from None
        return partial(msgspec.convert, type=native_type, strict=True), msgspec.ValidationError

    @staticmethod
    def _mismatch(where: str) -> CodecConfigurationError:
        return CodecConfigurationError(f"The native type of {where} does not match its binding")

    def _build(self, node: TypeNode, annotation: object, where: str) -> _Plan:
        """Build the converter of a node and its annotation, with the checks of its msgspec Meta constraints."""
        annotation, metadata = _unwrap(annotation)
        plan = self._shape(node, annotation, where)
        try:
            checks = _checks(metadata)
        except (PatternDialectError, PatternResourceError):
            raise self._mismatch(where) from None
        match plan:
            case _ if not checks:
                return plan
            case _Leaf() | _Sequence() | _Map():
                return dataclasses.replace(plan, checks=checks)
            case _:
                pass
        raise self._mismatch(where)

    def _shape(self, node: TypeNode, annotation: object, where: str) -> _Plan:  # noqa: PLR0911
        members = _union_members(annotation)
        match node:
            case ModelNode() if annotation is self._types[node.symbol]:
                return self._model(node.symbol)
            case UnionNode() if members is not None:
                items, nullable = _flattened(node)
                present = tuple(member for member in members if not _absent(member))
                if (NoneType in members) < nullable or len(present) != len(items):
                    raise self._mismatch(where)
                return _Union(
                    tuple(self._build(item, member, where) for item, member in zip(items, present, strict=True)),
                    nullable,
                )
            case _ if (
                members is not None
                and len(real := [item for item in members if not _absent(item)]) == 1
                and len(real) < len(members)
            ):
                return self._build(node, real[0], where)
            case ArrayNode(item=item, container=kind) if (
                container := _SEQUENCES.get(typing.get_origin(annotation))
            ) is not None:
                if len(arguments := typing.get_args(annotation)) != 1 or _CONTAINERS[container] != kind:
                    raise self._mismatch(where)
                return _Sequence(self._build(item, arguments[0], where), container, _Failure(f"native.{kind}_type"))
            case TupleNode(items=items) if typing.get_origin(annotation) is tuple and len(
                arguments := typing.get_args(annotation)
            ) == len(items):
                return _Tuple(
                    tuple(self._build(item, member, where) for item, member in zip(items, arguments, strict=True))
                )
            case MapNode(value=value) if typing.get_origin(annotation) in _MAPPINGS:
                key, member = typing.get_args(annotation)
                return _Map(self._key(_unwrap(key)[0], where), self._build(value, member, where))
            case LeafNode(representation=representation) if (leaf := _leaf(annotation, representation)) is not None:
                return leaf
            case _:
                pass
        raise self._mismatch(where)

    def _key(self, annotation: object, where: str) -> Callable[[str], object]:
        match annotation:
            case _ if annotation is str or annotation is Any or annotation is object:
                return _same
            case _ if typing.get_origin(annotation) is Literal:
                return _member(_members(typing.get_args(annotation)), _LITERAL)
            case type() if issubclass(annotation, Enum):
                return _member(_members(tuple(annotation)), _ENUM)
            case _:
                pass
        raise self._mismatch(where)

    def _model(self, symbol: str) -> _Model:
        if (existing := self._plans.get(symbol)) is not None:
            return existing
        binding, native = self._models[symbol], self._types[symbol]
        kind = binding.native_kind
        try:
            hints = typing_extensions.get_type_hints(native, include_extras=True)
            fits = {"typed_dict": _fits_record, "struct": _fits_struct}.get(kind, _fits_dataclass)(binding, native)
            extra_items = _extra_items(native) if fits and binding.extra_items is not None else None
        except (NameError, TypeError):
            raise self._mismatch(symbol) from None
        if not fits:
            raise self._mismatch(symbol)
        plan = self._plans[symbol] = _Model(binding, native, kind == "typed_dict", _KIND_FAILURES[kind])
        if kind == "struct":
            plan.unset = sys.modules["msgspec"].UNSET
            plan.tag = _struct_tag(native)
        fields = tuple(
            _Field(member, self._build(member.type, hints[member.native_name], member.field_id))
            for member in binding.fields
        )
        plan.fields = fields
        plan.wire = {member.binding.wire_name: member for member in fields}
        plan.required = tuple(member for member in fields if member.binding.required)
        plan.keys = frozenset(member.binding.native_name for member in fields)
        if binding.extra_items is not None:
            plan.extras = self._build(binding.extra_items, extra_items, f"{symbol} extra items")
        return plan

    def _project(self, wire: WireValue, budget: MatchBudget) -> DecodedValue[T]:
        presence = snapshot_presence(wire)
        binding_id = self._binding.binding_id
        if self._scan or self._convert is not None:
            scan = Walk(budget)
            self._decode(wire, self._plan, None, scan)
            if scan.issues and self._scan:
                return ModelInput(
                    binding_id=binding_id,
                    wire=wire,
                    presence=presence,
                    extras=MappingProxyType(scan.extras),
                    issues=tuple(scan.issues),
                )
            if scan.issues:
                msg = f"The native use has an unplanned projection gap at {scan.issues[0].pointer or '/'}"
                raise ModelProjectionError(msg)
            if self._convert is not None:
                if scan.native:
                    raise NativeValidationError(tuple(scan.native))
                try:
                    value = typing.cast("T", self._convert(_thawed(wire)))
                except self._invalid as error:
                    raise NativeValidationError((_msgspec_issue(str(error), wire),)) from None
                return ModelValue(
                    value=value,
                    binding_id=binding_id,
                    wire=wire,
                    presence=presence,
                    extras=MappingProxyType(scan.extras) if scan.extras else EMPTY,
                )
        state = Walk(budget, construct=True)
        value = typing.cast("T", self._decode(wire, self._plan, None, state))
        if state.native:
            raise NativeValidationError(tuple(state.native))
        return ModelValue(
            value=value,
            binding_id=binding_id,
            wire=wire,
            presence=presence,
            extras=MappingProxyType(state.extras) if state.extras else EMPTY,
        )

    def _decode(self, wire: WireValue, plan: _Plan, route: _Route, state: Walk) -> object:  # noqa: PLR0911
        match plan:
            case _Model() if isinstance(wire, Mapping):
                return self._decode_model(wire, plan, route, state)
            case _Union(nullable=True) if wire is None:
                return None
            case _Union(members=members) if (member := self._member(wire, members, state.budget)) is not None:
                return self._decode(wire, member, route, state)
            case _Sequence(item=item, container=container) if isinstance(wire, tuple):
                items = [self._decode(entry, item, (route, index, index), state) for index, entry in enumerate(wire)]
                if not state.construct:
                    return None
                try:
                    return self._checked(container(items), plan.checks, route, state)
                except TypeError:
                    return self._refuse(state, _UNHASHABLE, route)
            case _Tuple(items=items) if isinstance(wire, tuple) and len(wire) == len(items):
                values = tuple(
                    self._decode(entry, item, (route, index, index), state)
                    for index, (entry, item) in enumerate(zip(wire, items, strict=True))
                )
                return values if state.construct else None
            case _Map() if isinstance(wire, Mapping):
                return self._decode_map(wire, plan, route, state)
            case _Leaf() if not state.construct:
                return None
            case _Leaf(accepts=accepts, convert=convert) if accepts(wire):
                if isinstance(value := convert(wire), _Failure):
                    return self._refuse(state, value, route)
                return self._checked(value, plan.checks, route, state)
            case _:
                pass
        return self._refuse(state, plan.failure, route)

    @staticmethod
    def _refuse(state: Walk, failure: _Failure, route: _Route) -> None:
        pointer, path = _located(route)
        state.native.append(NativeIssue(code=failure.code, pointer=pointer, native_path=path))

    def _checked(self, value: object, checks: tuple[_Check, ...], route: _Route, state: Walk) -> object:
        for check, failure in checks:
            if not check(value, state.budget):
                return self._refuse(state, failure, route)
        return value

    def _decode_map(self, wire: Mapping[str, WireValue], plan: _Map, route: _Route, state: Walk) -> object:
        entries: dict[object, object] = {}
        for name, entry in wire.items():
            if isinstance(key := plan.key(name), _Failure):
                self._refuse(state, key, (route, name, name))
            else:
                entries[key] = self._decode(entry, plan.value, (route, name, name), state)
        return self._checked(entries, plan.checks, route, state) if state.construct else None

    def _decode_model(self, wire: Mapping[str, WireValue], plan: _Model, route: _Route, state: Walk) -> object:
        failures, gaps = len(state.native), len(state.issues)
        arguments: dict[str, object] = {}
        for name, entry in wire.items():
            if (member := plan.wire.get(name)) is None:
                self._unbound(name, entry, plan, (route, name, name), state, arguments)
            elif not (binding := member.binding).constructible:
                state.issues.append(self._gap("FIELD_NOT_CONSTRUCTIBLE", plan, binding, route))
            else:
                arguments[binding.native_name] = self._decode(
                    entry, member.plan, (route, name, binding.native_name), state
                )
        state.issues.extend(
            directional_gap(plan.binding, member.binding, _located(route)[0])
            if self._excluded(member.binding)
            else self._gap("MODEL_PROJECTION_GAP", plan, member.binding, route)
            for member in plan.required
            if member.binding.wire_name not in wire
        )
        if len(state.issues) > gaps and state.construct:
            msg = f"The native use has an unplanned projection gap at {state.issues[gaps].pointer or '/'}"
            raise ModelProjectionError(msg)
        if not state.construct or len(state.native) > failures:
            return None
        if plan.record:
            return arguments
        try:
            return plan.native(**arguments)
        except Exception:  # noqa: BLE001
            return self._refuse(state, _CONSTRUCTOR, route)

    def _unbound(  # noqa: PLR0913, PLR0917
        self, name: str, entry: WireValue, plan: _Model, route: _Route, state: Walk, arguments: dict[str, object]
    ) -> None:
        """Take a member no field binds: a Struct's tag, a TypedDict's extra item, a forbidden member, or an extra."""
        if plan.tag is not None and name == plan.tag[0]:
            if type(entry) is not type(plan.tag[1]) or entry != plan.tag[1]:
                self._refuse(state, _TAG, route)
        elif plan.extras is not None:
            arguments[name] = self._decode(entry, plan.extras, route, state)
        elif plan.binding.extra == "forbid":
            self._refuse(state, _EXTRA_FORBIDDEN, route)
        else:
            state.extras[_located(route)[0]] = entry

    @staticmethod
    def _gap(
        code: Literal["FIELD_NOT_CONSTRUCTIBLE", "MODEL_PROJECTION_GAP"],
        plan: _Model,
        member: FieldBinding,
        route: _Route,
    ) -> ProjectionIssue:
        return ProjectionIssue(
            code=code,
            pointer=_located((route, member.wire_name, member.native_name))[0],
            schema_location=plan.binding.schema_id or "",
            field_id=member.field_id,
            message="The native constructor cannot take this property"
            if code == "FIELD_NOT_CONSTRUCTIBLE"
            else "The native constructor requires a property that the wire value omits",
        )

    def _member(self, wire: WireValue, members: tuple[_Plan, ...], budget: MatchBudget) -> _Plan | None:
        for member in members:
            match member:
                case _Model(binding=binding) if self._matches(binding.symbol, wire, budget):
                    return member
                case _Sequence() | _Tuple() if isinstance(wire, tuple):
                    return member
                case _Map() if isinstance(wire, Mapping):
                    return member
                case _Leaf(accepts=accepts, convert=convert) if accepts(wire) and not _outside(convert(wire)):
                    return member
                case _:
                    continue
        return None

    def _native_wire(self, value: object, presence: PresenceTree | None) -> WireValue:
        return self._encode(value, self._plan, presence, None)

    def _encode(self, native: object, plan: _Plan, presence: PresenceTree | None, route: _Route) -> WireValue:  # noqa: PLR0911
        match plan:
            case _ if native is None:
                return None
            case _Model(record=True) if is_mapping(native):
                return self._encode_model(native, plan, presence, route)
            case _Model(record=False, native=kind) if isinstance(native, kind):
                return self._encode_model(native, plan, presence, route)
            case _Union(members=members) if (member := _native_plan(native, members)) is not None:
                return self._encode(native, member, presence, route)
            case _Sequence(item=item) if is_set(native):
                _array_presence(presence, len(native), route)
                return _sorted(self._encode(entry, item, None, route) for entry in native)
            case _Sequence(item=item) if is_sequence(native):
                _array_presence(presence, len(native), route)
                return tuple(
                    self._encode(entry, item, presence and presence.child(index), (route, index, index))
                    for index, entry in enumerate(native)
                )
            case _Tuple(items=items) if is_sequence(native) and len(native) == len(items):
                _array_presence(presence, len(native), route)
                return tuple(
                    self._encode(entry, item, presence and presence.child(index), (route, index, index))
                    for index, (entry, item) in enumerate(zip(native, items, strict=True))
                )
            case _Map(value=value) if is_mapping(native):
                entries = {_key_text(key, route): entry for key, entry in native.items()}
                _object_presence(presence, entries.keys(), route)
                return MappingProxyType({
                    name: self._encode(entries[name], value, child, (route, name, name))
                    for name, child in selected(presence, entries)
                })
            case _Leaf(representation=representation):
                return _json(native, representation, presence, route)
            case _:
                pass
        raise shape_error(_located(route)[0])

    def _encode_model(self, native: object, plan: _Model, presence: PresenceTree | None, route: _Route) -> WireValue:
        items = typing.cast("Mapping[object, object]", native) if plan.record else None
        extras = (
            {_key_text(name, route): value for name, value in items.items() if name not in plan.keys}
            if items is not None and plan.extras is not None
            else {}
        )
        if presence is not None:
            present = {
                member.binding.wire_name
                for member in plan.fields
                if _read(native, items, member.binding.native_name, plan.unset) is not _ABSENT
            }
            tag: set[str] = set() if plan.tag is None else {plan.tag[0]}
            check_object_presence(presence, present | extras.keys() | tag, _located(route)[0])
        members: dict[str, WireValue] = {} if plan.tag is None else {plan.tag[0]: _scalar(plan.tag[1], route)}
        for member in plan.fields:
            binding = member.binding
            value = (
                getattr(native, binding.native_name, _ABSENT)
                if items is None
                else items.get(binding.native_name, _ABSENT)
            )
            if value is _ABSENT or value is plan.unset:
                continue
            child = None if presence is None else presence.child(binding.wire_name)
            if (presence is not None and child is None) or (presence is None and binding.omit_none and value is None):
                continue
            name = binding.wire_name
            members[name] = self._encode(value, member.plan, child, (route, name, name))
        if extras and (extras_plan := plan.extras) is not None:
            for name, child in selected(presence, extras):
                members[name] = self._encode(extras[name], extras_plan, child, (route, name, name))
        return MappingProxyType(members)


def _read(native: object, items: Mapping[object, object] | None, name: str, unset: object) -> object:
    value = getattr(native, name, _ABSENT) if items is None else items.get(name, _ABSENT)
    return _ABSENT if value is unset else value


def _array_presence(presence: PresenceTree | None, length: int, route: _Route) -> None:
    if presence is not None:
        check_array_presence(presence, length, _located(route)[0])


def _object_presence(presence: PresenceTree | None, names: AbstractSet[str], route: _Route) -> None:
    if presence is not None:
        check_object_presence(presence, names, _located(route)[0])


def _scalar(value: object, route: _Route) -> WireValue:
    try:
        return checked_scalar(value)
    except (TypeError, ValueError) as error:
        msg = f"{error} at {_located(route)[0] or '/'}"
        raise ModelProjectionError(msg) from None


def _key_text(key: object, route: _Route) -> str:
    try:
        return checked_key(key.value if isinstance(key, Enum) else key)
    except (TypeError, ValueError) as error:
        msg = f"{error} at {_located(route)[0] or '/'}"
        raise ModelProjectionError(msg) from None


def _sorted(items: Iterable[WireValue]) -> WireValue:
    return tuple(sorted(items, key=encode_json))


def _fits(plan: _Model, native: object) -> bool:
    """Return whether a native value can be a model's: an instance of a dataclass, or a TypedDict's keys."""
    if not plan.record:
        return isinstance(native, plan.native)
    return (
        is_mapping(native)
        and all(member.binding.native_name in native for member in plan.required)
        and (plan.extras is not None or all(name in plan.keys for name in native))
    )


def _msgspec_issue(message: str, wire: WireValue) -> NativeIssue:
    """Locate the issue msgspec reports by walking its ``$`` path through the wire value it read.

    A member name may itself hold dots, so the longest name of the object the path continues with is taken; a map
    entry, which msgspec writes as ``[...]``, ends the walk at its map.
    """
    _, found, rest = message.rpartition(" - at `$")
    rest = rest.removesuffix("`") if found else ""
    tokens: list[str | int] = []
    while rest:
        if (index := _MSGSPEC_PATH.match(rest)) is not None and index[1] != "...":
            tokens.append(position := int(index[1]))
            wire, rest = child_value(wire, position), rest[index.end() :]
        elif isinstance(wire, Mapping) and (
            name := max(
                (
                    key
                    for key in wire
                    if rest.startswith(f".{key}") and rest[len(key) + 1 : len(key) + 2] in {"", ".", "["}
                ),
                key=len,
                default=None,
            )
        ):
            tokens.append(name)
            wire, rest = wire[name], rest[len(name) + 1 :]
        else:
            break
    pointer = "".join(f"/{escape_pointer_token(token)}" for token in tokens)
    return NativeIssue(code="native.validation_error", pointer=pointer, native_path=tuple(tokens))


def _outside(value: object) -> bool:
    return isinstance(value, _Failure) and value.outside


def _native_plan(native: object, members: tuple[_Plan, ...]) -> _Plan | None:
    """Select the union member that encodes a native value, preferring the model of exactly its type."""
    if (
        exact := next((item for item in members if isinstance(item, _Model) and item.native is type(native)), None)
    ) is not None:
        return exact
    for member in members:
        match member:
            case _Model() if _fits(member, native):
                return member
            case _Sequence() | _Tuple() if is_sequence(native) or is_set(native):
                return member
            case _Map() if is_mapping(native):
                return member
            case _Leaf(native=kind) if kind is not None and isinstance(native, kind):
                return member
            case _:
                continue
    return next((member for member in members if isinstance(member, _Leaf)), None)


def _json(value: object, representation: Representation, presence: PresenceTree | None, route: _Route) -> WireValue:  # noqa: PLR0911
    match value:
        case Enum():
            return _json(value.value, representation, presence, route)
        case Decimal() if representation == "decimal_string":
            return str(value)
        case None | bool() | int() | float() | str() | Decimal():
            return _scalar(value, route)
        case datetime() | time() if (offset := value.utcoffset()) is None or offset % _MINUTE:
            msg = f"The value at {_located(route)[0] or '/'} has no UTC offset in whole minutes"
            raise ModelProjectionError(msg)
        case datetime() | date() | time():
            return value.isoformat()
        case timedelta():
            return _iso_duration(value)
        case _ if _stringified(value):
            return str(value)
        case _ if is_mapping(value):
            entries = {_key_text(key, route): entry for key, entry in value.items()}
            _object_presence(presence, entries.keys(), route)
            return MappingProxyType({
                name: _json(entries[name], "value", child, (route, name, name))
                for name, child in selected(presence, entries)
            })
        case _ if is_set(value):
            _array_presence(presence, len(value), route)
            return _sorted(_json(entry, "value", None, route) for entry in value)
        case _ if is_sequence(value):
            _array_presence(presence, len(value), route)
            return tuple(
                _json(entry, "value", presence and presence.child(index), (route, index, index))
                for index, entry in enumerate(value)
            )
        case _:
            pass
    msg = f"The value at {_located(route)[0] or '/'} has no JSON representation"
    raise ModelProjectionError(msg)
