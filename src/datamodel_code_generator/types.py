"""Core type system for data model generation.

Provides DataType for representing types with references and constraints,
DataTypeManager as the abstract base for type mappings, and supporting
utilities for handling unions, optionals, and type hints.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from fractions import Fraction
from functools import cache, lru_cache
from itertools import chain, repeat
from math import inf, isfinite
from re import Pattern
from types import MappingProxyType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Final,
    Literal,
    Optional,
    Protocol,
    TypeAlias,
    TypeVar,
    Union,
    cast,
    final,
    runtime_checkable,
)

from pydantic import (
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    PlainSerializer,
    SerializeAsAny,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidatorFunctionWrapHandler,
    WrapValidator,
    create_model,
    field_validator,
)
from pydantic_core import core_schema
from typing_extensions import TypeIs, assert_never, override

from datamodel_code_generator import _constraint_applicability as _applicability
from datamodel_code_generator import _null_markers
from datamodel_code_generator import imports as generator_imports
from datamodel_code_generator._format_types import (
    DateClassType,
    DatetimeClassType,
    PythonVersion,
    PythonVersionMin,
)
from datamodel_code_generator._model_keys import NodeKey
from datamodel_code_generator._parse_memo import PARSE_MEMO, rendering_memo
from datamodel_code_generator.enums import DefaultValueType, TargetPydanticVersion
from datamodel_code_generator.imports import (
    IMPORT_ABC_MAPPING,
    IMPORT_ABC_SEQUENCE,
    IMPORT_ANNOTATED,
    IMPORT_ANY,
    IMPORT_DECIMAL,
    IMPORT_DICT,
    IMPORT_FROZEN_SET,
    IMPORT_LIST,
    IMPORT_LITERAL,
    IMPORT_MAPPING,
    IMPORT_OPTIONAL,
    IMPORT_SEQUENCE,
    IMPORT_SET,
    IMPORT_TUPLE,
    IMPORT_UNION,
    Import,
)
from datamodel_code_generator.python_literal import represent_python_value
from datamodel_code_generator.reference import Reference, _BaseModel
from datamodel_code_generator.util import create_module_getattr

T = TypeVar("T")
SourceT = TypeVar("SourceT")

OPTIONAL = "Optional"
OPTIONAL_PREFIX = f"{OPTIONAL}["

_RUNTIME_EXPRESSION_IMPORTS_DATA_TYPE_KEY = "_runtime_expression_imports"
_ENUM_MEMBER_LITERAL_REFERENCE_DATA_TYPE_KEY = "_enum_member_literal_reference"
_FORWARD_REFERENCE_DATA_TYPE_KEY = "_is_forward_reference"
_REFERENCE_NULL_DATA_TYPE_KEY = _null_markers.REFERENCE_NULL_KEY
_NULL_STRIPPED_DATA_TYPE_KEY = "_is_null_stripped"
_TARGET_NULL_DATA_TYPE_KEY = _null_markers.TARGET_NULL_KEY
_HELD_NULL_DROPPED_DATA_TYPE_KEY = _null_markers.HELD_NULL_DROPPED_KEY
_RENDER_PREPARATION_NULL_DATA_TYPE_KEY = _null_markers.RENDER_PREPARATION_NULL_KEY
_LITERAL_IMPORT_DATA_TYPE_KEY = "_literal_import"
_VALUES_AS_WRITTEN_KEY = _applicability.VALUES_AS_WRITTEN_KEY
_DROPPED_MULTIPLE_OF_KEY = _applicability.DROPPED_MULTIPLE_OF_KEY
_RenderStateWrite: TypeAlias = Literal["reference-null", "member-null", "null-moved-to-union", "empty-union"]


class DefaultValueRecipe(Enum):
    """Describe how a serialized schema value becomes a runtime expression."""

    Decimal = auto()


@dataclass(frozen=True, slots=True)
class DefaultValueDescriptor:
    """Backend-declared semantics for one generated scalar type.

    ``option_kind`` selects the opt-in configuration, while ``recipe`` remains
    deliberately separate so future temporal and identifier values can have
    their own parsing policies without inferring them from an import name.
    """

    option_kind: DefaultValueType
    recipe: DefaultValueRecipe
    constructor_import: Import
    normalize_constraints: bool = False


DECIMAL_DEFAULT_VALUE_DESCRIPTOR = DefaultValueDescriptor(
    option_kind=DefaultValueType.Decimal,
    recipe=DefaultValueRecipe.Decimal,
    constructor_import=IMPORT_DECIMAL,
)
CONSTRAINED_DECIMAL_DEFAULT_VALUE_DESCRIPTOR = DefaultValueDescriptor(
    option_kind=DefaultValueType.Decimal,
    recipe=DefaultValueRecipe.Decimal,
    constructor_import=IMPORT_DECIMAL,
    normalize_constraints=True,
)

UNION = "Union"
UNION_PREFIX = f"{UNION}["
UNION_DELIMITER = ", "
UNION_PATTERN: Pattern[str] = re.compile(r"\s*,\s*")
UNION_OPERATOR_DELIMITER = " | "
UNION_OPERATOR_PATTERN: Pattern[str] = re.compile(r"\s*\|\s*")
NONE = "None"
ANY = "Any"
LITERAL = "Literal"
SEQUENCE = "Sequence"
FROZEN_SET = "FrozenSet"
MAPPING = "Mapping"
DICT = "Dict"
SET = "Set"
LIST = "List"
TUPLE = "Tuple"
STANDARD_DICT = "dict"
STANDARD_LIST = "list"
STANDARD_SET = "set"
STANDARD_TUPLE = "tuple"
STANDARD_FROZEN_SET = "frozenset"
STR = "str"

REQUIRED = "Required"
REQUIRED_PREFIX = f"{REQUIRED}["

NOT_REQUIRED = "NotRequired"
NOT_REQUIRED_PREFIX = f"{NOT_REQUIRED}["

READ_ONLY = "ReadOnly"
READ_ONLY_PREFIX = f"{READ_ONLY}["

__getattr__ = create_module_getattr(
    __name__,
    {
        "StrictTypes": ("datamodel_code_generator.enums", "StrictTypes"),
    },
)

if TYPE_CHECKING:
    import builtins
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
    from types import ModuleType

    from datamodel_code_generator._parse_memo import ParseMemo
    from datamodel_code_generator._python_type_annotation import PythonTypeExpr
    from datamodel_code_generator._python_type_binding import BoundPythonType
    from datamodel_code_generator._requirements import PydanticConstruct
    from datamodel_code_generator.enums import StrictTypes
    from datamodel_code_generator.model._decisions.nullability import NullSource
    from datamodel_code_generator.model._decisions.union_assembly import MemberRole, MergedMembers, UnionRow

    class DataModelFieldBase(Protocol):
        """Type-checking contract for a field that owns a DataType."""

        data_type: DataType

    _BoundPythonTypeField = BoundPythonType

else:
    DataModelFieldBase = Any
    # Keep the optional structured annotation feature off the normal import
    # path. DataType validates every non-None construction input against the exact
    # runtime class below, so this lazy Pydantic field alias is not an unchecked escape.
    _BoundPythonTypeField = Any


def _preserve_float_constraint_precision(value: Any, handler: ValidatorFunctionWrapHandler) -> Any:
    """Retain legacy floats except when conversion would lose integer precision."""
    if isinstance(value, int) and not isinstance(value, bool):
        try:
            float_value = float(value)
        except OverflowError:
            return value
        return float_value if float_value == value else value
    return handler(value)


def _serialize_float_constraint(value: Any) -> SerializeAsAny[float | None]:
    """Preserve numeric values without embedding unhashable serialization metadata in unions."""
    return value


FloatConstraint = Annotated[
    float,
    WrapValidator(_preserve_float_constraint_precision),
    # Float serialization would round the integers retained by the validator.
    PlainSerializer(_serialize_float_constraint),
]


class UnionIntFloat:
    """Pydantic-compatible type that accepts both int and float values."""

    def __init__(self, value: float) -> None:
        """Initialize with an int or float value."""
        self.value: int | float = value

    def __int__(self) -> int:  # pragma: no cover
        """Convert value to int."""
        return int(self.value)

    def __float__(self) -> float:
        """Convert value to float."""
        return float(self.value)

    @classmethod
    def __get_pydantic_core_schema__(  # noqa: PLW3201
        cls, _source_type: Any, _handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        """Return Pydantic v2 core schema."""
        from_int_schema = core_schema.chain_schema([
            core_schema.union_schema([core_schema.int_schema(), core_schema.float_schema()]),
            core_schema.no_info_plain_validator_function(cls.validate),
        ])

        return core_schema.json_or_python_schema(
            json_schema=from_int_schema,
            python_schema=core_schema.union_schema([
                # check if it's an instance first before doing any further work
                core_schema.is_instance_schema(UnionIntFloat),
                from_int_schema,
            ]),
            serialization=core_schema.plain_serializer_function_ser_schema(lambda instance: instance.value),
        )

    @classmethod
    def validate(cls, v: Any) -> UnionIntFloat:
        """Validate and convert value to UnionIntFloat."""
        if isinstance(v, UnionIntFloat):  # pragma: no cover
            return v
        if not isinstance(v, (int, float)):  # pragma: no cover
            try:
                int(v)
                return cls(v)
            except (TypeError, ValueError):
                pass
            try:
                float(v)
                return cls(v)
            except (TypeError, ValueError):
                pass

            msg = f"{v} is not int or float"
            raise TypeError(msg)
        return cls(v)


def _contains_decimal(value: Any) -> bool:
    match value:
        case Decimal():
            return True
        case dict():
            return any(_contains_decimal(k) or _contains_decimal(v) for k, v in value.items())
        case list() | tuple() | set() | frozenset():
            return any(_contains_decimal(item) for item in value)
        case _:
            return False


def _get_fraction_floor(value: Fraction) -> int:
    return value.numerator // value.denominator


def _get_fraction_ceil(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def _get_constraint_number(value: Any) -> Any:
    return value.value if isinstance(value, UnionIntFloat) else value


def _float_constraint(value: Any) -> float:
    """Return a number constraint as a float: an int beyond the float range is the infinity of its sign, as 1e400 is."""
    number = _get_constraint_number(value)
    try:
        return float(number)
    except OverflowError:
        return inf if number > 0 else -inf


def _non_finite_as_written(value: Any, *, as_float: bool) -> float | None:
    """Return a number constraint as the infinity or NaN that a type writes it as, or None when it writes it finite.

    A float type writes an int beyond the float range as an infinity too (``_float_constraint``); an int or a
    ``Decimal`` type writes an int as it is.
    """
    number = _get_constraint_number(value)
    if not as_float and not isinstance(number, float):
        return None
    return None if isfinite(written := _float_constraint(number)) else written


def normalize_integer_constraint(constraint: str, value: Any) -> tuple[str, Any] | None:  # noqa: PLR0911
    """Return an integer-safe pydantic constraint for a numeric schema constraint."""
    number = _get_constraint_number(value)
    try:
        fraction = Fraction(str(number))
    except (TypeError, ValueError):
        return constraint, value

    match constraint:
        case "multiple_of" if fraction.numerator == 1:
            return None
        case "multiple_of":
            return constraint, abs(fraction.numerator)
        case _ if fraction.denominator == 1:
            return constraint, int(fraction)
        case "ge":
            return "ge", _get_fraction_ceil(fraction)
        case "gt":
            return "ge", _get_fraction_floor(fraction) + 1
        case "le":
            return "le", _get_fraction_floor(fraction)
        case "lt":
            return "le", _get_fraction_ceil(fraction) - 1
    return constraint, value


def merge_normalized_constraint(constraints: dict[str, Any], key: str, value: Any) -> None:
    """Merge a normalized constraint, keeping the stronger bound when ge or le collides."""
    match constraints.get(key):
        case None:
            constraints[key] = value
        case current if key == "ge":
            constraints[key] = max(current, value)
        case current:
            constraints[key] = min(current, value)


def normalize_integer_constraints(constraints: dict[str, Any]) -> dict[str, Any]:
    """Return integer-safe pydantic constraints."""
    normalized_constraints: dict[str, Any] = {}
    for key, value in constraints.items():
        if (normalized := normalize_integer_constraint(key, value)) is not None:
            merge_normalized_constraint(normalized_constraints, normalized[0], normalized[1])
    return normalized_constraints


def chain_as_tuple(*iterables: Iterable[T]) -> tuple[T, ...]:
    """Chain multiple iterables and return as a tuple.

    Optimized for the common case of 2 iterables to avoid chain() overhead.
    """
    if len(iterables) == 2:  # noqa: PLR2004
        return (*iterables[0], *iterables[1])
    return tuple(chain(*iterables))


def get_type_base_name(type_str: str) -> str:
    """Extract the base name from a supported Python type annotation.

    Examples:
        "List[str]" -> "List"
        "foo.bar.Baz" -> "Baz"
        "Optional[int]" -> "Optional"
    """
    # Python annotation parsing is an opt-in external-schema boundary. Keep its
    # IR and runtime codec out of ordinary generation that never supplies one.
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        parse_python_type_annotation,
        python_type_expr_base_name,
    )

    fallback = type_str.split("[", maxsplit=1)[0].rsplit(".", 1)[-1].strip()
    if (expression := parse_python_type_annotation(type_str)) is None:
        return fallback
    return python_type_expr_base_name(expression) or fallback


def get_subscript_args(type_str: str) -> list[str]:
    """Extract top-level arguments from a supported Python type annotation.

    Examples:
        "List[str]" -> ["str"]
        "Dict[str, int]" -> ["str", "int"]
        "Union[str, int, None]" -> ["str", "int", "None"]
        "str | int | None" -> ["str", "int", "None"]
        "str" -> []
    """
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        parse_python_type_annotation,
        python_type_expr_arguments,
        render_python_type_expr,
    )

    if (expression := parse_python_type_annotation(type_str)) is None:
        return []
    return [render_python_type_expr(argument) for argument in python_type_expr_arguments(expression)]


def extract_qualified_names(type_str: str) -> list[str]:
    """Extract all fully qualified names from a supported type annotation.

    Finds patterns like 'module.path.ClassName' where the name contains dots.

    Examples:
        "type[foo.bar.Baz]" -> ["foo.bar.Baz"]
        "Dict[a.B, c.D]" -> ["a.B", "c.D"]
        "str" -> []
    """
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        iter_python_type_expr_qualified_names,
        parse_python_type_annotation,
    )

    expression = parse_python_type_annotation(type_str)
    return list(iter_python_type_expr_qualified_names(expression)) if expression is not None else []


@lru_cache(maxsize=1024)
def is_python_type_annotation(type_str: str) -> bool:
    """Return whether a string is a Python type annotation expression."""
    from datamodel_code_generator._python_type_annotation import parse_python_type_annotation  # noqa: PLC0415

    return parse_python_type_annotation(type_str) is not None


def _remove_none_from_union(type_: str, *, use_union_operator: bool) -> str:  # noqa: PLR0912
    """Remove None from a Union type string, handling nested unions."""
    if use_union_operator:
        if " | " not in type_:
            return type_
        separator = "|"
        inner_text = type_
    else:
        if not type_.startswith(UNION_PREFIX):
            return type_
        separator = ","
        inner_text = type_[len(UNION_PREFIX) : -1]

    parts = []
    inner_count = 0
    current_part = ""

    # With this variable we count any non-escaped round bracket, whenever we are inside a
    # constraint string expression. Once found a part starting with `constr(`, we increment
    # this counter for each non-escaped opening round bracket and decrement it for each
    # non-escaped closing round bracket.
    in_constr = 0
    quote = escaped = ""

    # Parse union parts carefully to handle nested structures
    for char in inner_text:
        current_part += char
        if quote:
            if escaped:
                escaped = ""
            elif char == "\\":
                escaped = "\\"
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "[" and in_constr == 0:
            inner_count += 1
        elif char == "]" and in_constr == 0:
            inner_count -= 1
        elif char == "(":
            if current_part.strip().startswith("constr(") and (len(current_part) < 2 or current_part[-2] != "\\"):  # noqa: PLR2004
                in_constr += 1
        elif char == ")":
            if in_constr > 0 and (len(current_part) < 2 or current_part[-2] != "\\"):  # noqa: PLR2004
                in_constr -= 1
        elif char == separator and inner_count == 0 and in_constr == 0:
            part = current_part[:-1].strip()
            if part != NONE:
                # Process nested unions recursively
                # only UNION_PREFIX might be nested but not union_operator
                if not use_union_operator and part.startswith(UNION_PREFIX):
                    part = _remove_none_from_union(part, use_union_operator=False)
                parts.append(part)
            current_part = ""

    part = current_part.strip()
    if current_part and part != NONE:
        # only UNION_PREFIX might be nested but not union_operator
        if not use_union_operator and part.startswith(UNION_PREFIX):  # pragma: no cover
            part = _remove_none_from_union(part, use_union_operator=False)
        parts.append(part)

    if not parts:
        return NONE
    if len(parts) == 1:
        return parts[0]

    if use_union_operator:
        return UNION_OPERATOR_DELIMITER.join(parts)

    return f"{UNION_PREFIX}{UNION_DELIMITER.join(parts)}]"


@lru_cache(maxsize=4096)
def get_optional_type(type_: str, use_union_operator: bool) -> str:  # noqa: FBT001
    """Wrap a type string in Optional or add | None suffix."""
    type_ = _remove_none_from_union(type_, use_union_operator=use_union_operator)

    if not type_ or type_ == NONE:
        return NONE
    if use_union_operator:
        return f"{type_} | {NONE}"
    return f"{OPTIONAL_PREFIX}{type_}]"


@cache
def _nullability_core() -> ModuleType:
    """Return the rendered nullability decision, imported once; ``model`` imports this module, so not at its top."""
    from datamodel_code_generator.model._decisions import nullability  # noqa: PLC0415

    return nullability


@cache
def _union_core() -> ModuleType:
    """Return the union assembly decision, imported once; ``model`` imports this module, so not at its top."""
    from datamodel_code_generator.model._decisions import union_assembly  # noqa: PLC0415

    return union_assembly


def build_union(
    data_type: type[DataType],
    members: Sequence[DataType],
    row: UnionRow = "declared",
    /,
    *,
    nullable: bool = False,
    roles: Sequence[MemberRole] = (),
    **values: Any,
) -> DataType:
    """Build the union of ``members`` that the ``row`` of ``union_assembly.UnionRow`` assembles, with ``data_type``.

    ``nullable`` tells that the builder found that the union admits null, ``roles`` gives each member its role (all
    plain members when empty), and ``values`` go to the constructor.
    """
    union = _union_core()
    plan = union.assemble_union(
        tuple(map(union.MemberFacts, roles)) if roles else (union.MEMBER,) * len(members),
        union.UnionPolicy(row, nullable),
    )
    kept = [members[index] for index in plan.members]
    if plan.one_member_is_type and len(kept) == 1:
        return kept[0]
    if plan.nullable:
        values["is_optional"] = True
    if plan.preserve_order:
        values["preserve_union_member_order"] = True
    return data_type(data_types=kept, **values)


def union_members(data_type: DataType) -> Sequence[DataType]:
    """Return the members of the union a type is at its top level, or nothing when it is no union there.

    A container of several items is no union at its top level: it writes the union of its items inside it.
    """
    if len(data_type.data_types) < 2 or any((  # noqa: PLR2004
        data_type.is_dict,
        data_type.is_list,
        data_type.is_set,
        data_type.is_frozen_set,
        data_type.is_mapping,
        data_type.is_sequence,
        data_type.is_tuple,
    )):
        return ()
    return data_type.data_types


def is_data_model_field(obj: object) -> TypeIs[DataModelFieldBase]:
    """Check if an object structurally owns a DataType."""
    return isinstance(getattr(obj, "data_type", None), DataType)


@runtime_checkable
class Modular(Protocol):
    """Protocol for objects with a module name property."""

    @property
    def module_name(self) -> str:
        """Return the module name."""
        raise NotImplementedError


@runtime_checkable
class Nullable(Protocol):
    """Protocol for objects with a nullable property."""

    @property
    def nullable(self) -> bool:
        """Return whether the type is nullable."""
        raise NotImplementedError


class DataType(_BaseModel):  # noqa: PLR0904
    """Represents a type in generated code with imports and references."""

    model_config = ConfigDict(
        extra="forbid",
        revalidate_instances="never",
        defer_build=True,
    )

    type: Optional[str] = None  # noqa: UP045
    reference: Optional[Reference] = None  # noqa: UP045
    data_types: list[DataType] = Field(default_factory=list)
    is_func: bool = False
    kwargs: Optional[dict[str, Any]] = None  # noqa: UP045
    import_: Optional[Import] = None  # noqa: UP045
    python_type: _BoundPythonTypeField | None = Field(default=None, exclude=True, repr=False)
    python_version: PythonVersion = PythonVersionMin
    is_optional: bool = False
    is_dict: bool = False
    is_list: bool = False
    is_set: bool = False
    is_frozen_set: bool = False
    is_mapping: bool = False
    is_sequence: bool = False
    is_tuple: bool = False
    tuple_item_count: int | None = Field(default=None, ge=0)
    is_custom_type: bool = False
    literals: list[Union[StrictBool, StrictInt, StrictStr]] = Field(default_factory=list)  # noqa: UP007
    enum_member_literals: list[tuple[str, str]] = Field(default_factory=list)  # [(EnumClassName, member_name), ...]
    use_standard_collections: bool = False
    use_generic_container: bool = False
    use_union_operator: bool = False
    preserve_union_member_order: bool = False
    alias: Optional[str] = None  # noqa: UP045
    parent: Union[DataModelFieldBase, DataType, None] = None  # noqa: UP007

    @field_validator("python_type")
    @classmethod
    def _validate_python_type(cls, value: object | None) -> object | None:
        """Enforce the semantic binding type only when the feature is used."""
        if value is None:
            return None
        from datamodel_code_generator._python_type_binding import BoundPythonType  # noqa: PLC0415

        if isinstance(value, BoundPythonType):
            return value
        msg = "python_type must be a BoundPythonType"
        raise ValueError(msg)

    children: list[DataType] = Field(default_factory=list)
    strict: bool = False
    dict_key: Optional[DataType] = None  # noqa: UP045
    treat_dot_as_module: bool = False
    use_serialize_as_any: bool = False
    discriminator: Optional[str] = None  # noqa: UP045

    _exclude_fields: ClassVar[set[str]] = {"parent", "children"}
    _pass_fields: ClassVar[set[str]] = {"parent", "children", "data_types", "reference"}

    @override
    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> DataType:
        """Create a deep copy handling circular references in parent/children fields."""
        if memo is None:
            memo = {}

        obj_id = id(self)
        if obj_id in memo:
            return memo[obj_id]

        cls = self.__class__
        model_fields = cls.model_fields

        shallow_kwargs: dict[str, Any] = {}
        for field_name in model_fields:
            value = getattr(self, field_name)
            if field_name in self._exclude_fields:
                shallow_kwargs[field_name] = None
            else:
                shallow_kwargs[field_name] = value

        new_obj: DataType = cls.model_construct(**shallow_kwargs)
        memo[obj_id] = new_obj

        for field_name in model_fields:
            if field_name not in self._exclude_fields:
                value = getattr(self, field_name)
                copied_value = deepcopy(value, memo)
                object.__setattr__(new_obj, field_name, copied_value)
        new_obj._set_runtime_expression_imports(self.runtime_expression_imports)
        new_obj._set_forward_reference(self._is_forward_reference)
        for key in (
            _REFERENCE_NULL_DATA_TYPE_KEY,
            _NULL_STRIPPED_DATA_TYPE_KEY,
            _VALUES_AS_WRITTEN_KEY,
            _TARGET_NULL_DATA_TYPE_KEY,
            _HELD_NULL_DROPPED_DATA_TYPE_KEY,
            _DROPPED_MULTIPLE_OF_KEY,
        ):
            if key in self.__dict__:
                new_obj.__dict__[key] = self.__dict__[key]
        if (enum_member_literal_reference := self._enum_member_literal_reference) is not None:
            new_obj._set_enum_member_literal_reference(enum_member_literal_reference)

        return new_obj

    @classmethod
    def from_import(  # noqa: PLR0913
        cls: builtins.type[DataTypeT],
        import_: Import,
        *,
        is_optional: bool = False,
        is_dict: bool = False,
        is_list: bool = False,
        is_set: bool = False,
        is_custom_type: bool = False,
        strict: bool = False,
        kwargs: dict[str, Any] | None = None,
    ) -> DataTypeT:
        """Create a DataType from an Import object."""
        return cls(
            type=import_.import_,
            import_=import_,
            is_optional=is_optional,
            is_dict=is_dict,
            is_list=is_list,
            is_set=is_set,
            is_func=bool(kwargs),
            is_custom_type=is_custom_type,
            strict=strict,
            kwargs=kwargs,
        )

    @property
    def unresolved_types(self) -> frozenset[str]:
        """Return set of unresolved type reference paths."""
        return frozenset(
            {t.reference.path for data_types in self.data_types for t in data_types.all_data_types if t.reference}
            | ({self.reference.path} if self.reference else set())
        )

    def replace_reference(self, reference: Reference | None) -> None:
        """Replace this DataType's reference with a new one."""
        if not self.reference:  # pragma: no cover
            msg = f"`{self.__class__.__name__}.replace_reference()` can't be called when `reference` field is empty."
            raise Exception(msg)  # noqa: TRY002
        self_id = id(self)
        self.reference.children = [c for c in self.reference.children if id(c) != self_id]
        self.reference = reference
        if reference:
            reference.children.append(self)

    def register_reference(self) -> None:
        """Register this newly copied type with its existing reference."""
        if self.reference:
            self.reference.children.append(self)

    def unregister_reference(self) -> None:
        """Detach this temporary type from reverse-reference tracking without losing its target."""
        if not self.reference:
            return
        children = self.reference.children
        for index in range(len(children) - 1, -1, -1):
            if children[index] is self:
                children.pop(index)

    def remove_reference(self) -> None:
        """Remove the reference from this DataType."""
        self.replace_reference(None)

    def swap_with(self, new_data_type: DataType) -> None:
        """Detach self and attach new_data_type to the same parent.

        Replaces this DataType with new_data_type in the parent container.
        Works with both field parents and nested DataType parents.
        """
        parent = self.parent
        self.parent = None
        if parent is not None:  # pragma: no cover
            new_data_type.parent = parent
            if is_data_model_field(parent):
                parent.data_type = new_data_type
            elif isinstance(parent, DataType):  # pragma: no cover
                parent.data_types = [new_data_type if d is self else d for d in parent.data_types]

    @property
    def module_name(self) -> str | None:
        """Return the module name from the reference source."""
        if self.reference and (module_name := getattr(self.reference.source, "module_name", None)) is not None:
            return module_name
        return None  # pragma: no cover

    @property
    def full_name(self) -> str:
        """Return the fully qualified name including module."""
        module_name = self.module_name
        if module_name:
            return f"{module_name}.{self.reference.short_name if self.reference else ''}"
        return self.reference.short_name if self.reference else ""

    @property
    def all_data_types(self) -> Iterator[DataType]:
        """Recursively yield all nested DataTypes including self and dict_key."""
        for data_type in self.data_types:
            yield from data_type.all_data_types
        if self.dict_key:
            yield from self.dict_key.all_data_types
        yield self

    def walk(
        self,
        visitor: Callable[[DataType], None],
        visited: set[int] | None = None,
    ) -> None:
        """Recursively walk this DataType tree, calling visitor on each node."""
        if visited is None:
            visited = set()
        node_id = id(self)
        if node_id in visited:
            return
        visited.add(node_id)
        visitor(self)
        for child in self.data_types:
            child.walk(visitor, visited)
        if self.dict_key:
            self.dict_key.walk(visitor, visited)

    def find_source(self, source_type: type[SourceT]) -> SourceT | None:  # ty: ignore[invalid-type-form]
        """Find the first reference source matching the given type from all nested data types."""
        for data_type in self.all_data_types:
            if not data_type.reference:  # pragma: no cover
                continue
            source = data_type.reference.source
            if isinstance(source, source_type):  # pragma: no cover
                return source
        return None  # pragma: no cover

    @property
    def all_imports(self) -> Iterator[Import]:
        """Recursively yield all imports from nested DataTypes and self."""
        for data_type in self.data_types:
            yield from data_type.all_imports
        yield from self.imports

    def _conditional_imports(self) -> Iterator[tuple[bool, Import]]:
        """Yield (condition, import) pairs in the order they may be emitted."""
        use_standard_collections = self.use_standard_collections
        yield (self.admits_none and not self.use_union_operator, IMPORT_OPTIONAL)
        yield (len(self.data_types) > 1 and not self.use_union_operator, IMPORT_UNION)
        has_literal = (bool(self.literals) or bool(self.enum_member_literals)) and not self.alias
        yield (has_literal, self._literal_import if has_literal else IMPORT_LITERAL)
        yield (bool(self.discriminator), IMPORT_ANNOTATED)
        yield (self.is_frozen_set and not use_standard_collections, IMPORT_FROZEN_SET)
        yield (self.is_mapping and use_standard_collections, IMPORT_ABC_MAPPING)
        yield (self.is_mapping and not use_standard_collections, IMPORT_MAPPING)
        yield (self.is_sequence and use_standard_collections, IMPORT_ABC_SEQUENCE)
        yield (self.is_sequence and not use_standard_collections, IMPORT_SEQUENCE)
        if self.use_generic_container:
            if use_standard_collections:
                # frozenset is builtin, no import needed for is_set
                yield (self.is_list, IMPORT_ABC_SEQUENCE)
                yield (self.is_dict, IMPORT_ABC_MAPPING)
            else:  # pragma: no cover
                yield (self.is_list, IMPORT_SEQUENCE)
                yield (self.is_set, IMPORT_FROZEN_SET)
                yield (self.is_dict, IMPORT_MAPPING)
                yield (self.is_tuple, IMPORT_TUPLE)
        elif not use_standard_collections:
            yield (self.is_list, IMPORT_LIST)
            yield (self.is_set, IMPORT_SET)
            yield (self.is_dict, IMPORT_DICT)
            yield (self.is_tuple, IMPORT_TUPLE)

    @property
    def runtime_expression_imports(self) -> tuple[Import, ...]:
        """Return producer-registered runtime imports without walking ``kwargs``."""
        return self.__dict__.get(_RUNTIME_EXPRESSION_IMPORTS_DATA_TYPE_KEY, ())

    def _set_runtime_expression_imports(self, imports: tuple[Import, ...]) -> None:
        """Register parser-owned kwargs expressions once at their producer boundary."""
        if imports:
            self.__dict__[_RUNTIME_EXPRESSION_IMPORTS_DATA_TYPE_KEY] = imports
            return
        self.__dict__.pop(_RUNTIME_EXPRESSION_IMPORTS_DATA_TYPE_KEY, None)

    @property
    def _literal_import(self) -> Import:
        """Return the import that ``Literal`` is written through."""
        return self.__dict__.get(_LITERAL_IMPORT_DATA_TYPE_KEY, IMPORT_LITERAL)

    @property
    def _literal_name(self) -> str:
        """Return the name that ``Literal`` is written as."""
        import_ = self.__dict__.get(_LITERAL_IMPORT_DATA_TYPE_KEY)
        return LITERAL if import_ is None else import_.binding_name

    def _set_literal_import(self, import_: Import) -> None:
        """Write ``Literal`` through an import bound under another name, as when a model of the module holds it."""
        self.__dict__[_LITERAL_IMPORT_DATA_TYPE_KEY] = import_

    @property
    def _is_quoted_reference(self) -> bool:
        """Return whether the referenced model is written as a quoted name."""
        return (reference := self.reference) is not None and self.alias == f'"{reference.short_name}"'

    @property
    def _is_forward_reference(self) -> bool:
        """Return whether the type renders as one quoted forward reference, before any nullability."""
        return self.__dict__.get(_FORWARD_REFERENCE_DATA_TYPE_KEY, False)

    def _set_forward_reference(self, is_forward_reference: bool) -> None:  # noqa: FBT001
        """Render the type as one string so the names inside it stay unquoted."""
        if is_forward_reference:
            self.__dict__[_FORWARD_REFERENCE_DATA_TYPE_KEY] = True

    @property
    def _enum_member_literal_reference(self) -> Reference | None:
        """Return the enum whose members ``enum_member_literals`` render."""
        return self.__dict__.get(_ENUM_MEMBER_LITERAL_REFERENCE_DATA_TYPE_KEY)

    def _set_enum_member_literal_reference(self, reference: Reference) -> None:
        """Keep the enum identity so every rendering module can import the class."""
        self.__dict__[_ENUM_MEMBER_LITERAL_REFERENCE_DATA_TYPE_KEY] = reference

    @property
    def imports(self) -> Iterator[Import]:
        """Yield imports required by this DataType."""
        # Add base import if exists
        if self.import_:
            yield self.import_
        if self.python_type:
            yield from self.python_type.imports
        if self.is_tuple and self.tuple_item_count and (not self.data_types or not self.data_types[0].type_hint):
            yield IMPORT_ANY
        if self.kwargs and self.import_ != IMPORT_DECIMAL and _contains_decimal(self.kwargs):
            yield IMPORT_DECIMAL

        # Yield imports based on conditions
        for field, import_ in self._conditional_imports():
            if field and import_ is not None and import_ != self.import_:
                yield import_

        # Propagate imports from any dict_key type
        if self.dict_key:
            yield from self.dict_key.all_imports

    def __init__(self, **values: Any) -> None:
        """Initialize DataType with validation and reference setup."""
        if not TYPE_CHECKING:  # pragma: no cover
            super().__init__(**values)

        if len(self.data_types) > 1:
            union = _union_core()
            plan = union.assemble_union(
                tuple(
                    union.MemberFacts(is_any=(is_any := type_.type == ANY), optional_any=is_any and type_.is_optional)
                    for type_ in self.data_types
                ),
                union.CONSTRUCTED,
            )
            if plan.rule == "optional-any-to-null":
                self.is_optional = True
                self.data_types = [self.data_types[index] for index in plan.members]

        for data_type in self.data_types:
            if data_type.reference or data_type.data_types:
                data_type.parent = self

        if self.dict_key and (self.dict_key.reference or self.dict_key.data_types):
            self.dict_key.parent = self

        if self.reference:
            self.reference.children.append(self)

    def _get_wrapped_reference_type_hint(self, type_: str) -> str:  # noqa: PLR6301
        """Wrap reference type name if needed (override in subclasses, e.g., for SerializeAsAny).

        Args:
            type_: The reference type name (e.g., "User")

        Returns:
            The potentially wrapped type name
        """
        return type_

    @staticmethod
    def _wrap_discriminator_type_hint(type_: str, discriminator: str) -> str:
        """Preserve the historical discriminator rendering for direct DataType users.

        Output-specific DataType subclasses should override this compatibility
        fallback so their generated syntax remains owned by the output backend.
        """
        return f"Annotated[{type_}, Field(discriminator={discriminator!r})]"

    _TYPE_HINT_CONTAINER_ORDER: ClassVar[tuple[str, ...]] = (
        "frozen_set",
        "set",
        "sequence",
        "list",
        "mapping",
        "dict",
    )
    _BASE_TYPE_HINT_CONTAINER_ORDER: ClassVar[tuple[str, ...]] = ("list", "set", "dict")

    @property
    def names_nullable_model(self) -> bool:
        """Return whether the type names a model that admits null and is not a type alias."""
        return (
            (reference := self.reference) is not None
            and not getattr(source := reference.source, "is_alias", False)
            and bool(getattr(source, "nullable", False))
        )

    @property
    def names_none_alias(self) -> bool:
        """Return whether the type hint is the name of a ``TypeAlias`` whose value is None, which ``|`` cannot join."""
        return self._names_none_alias(())

    def _names_none_alias(self, chain: tuple[Reference, ...]) -> bool:
        """Return ``names_none_alias``, following the aliases of ``chain`` once, so aliases that name each other end."""
        if self.alias or self.type:
            return False
        reference = self.reference
        if reference is not None and (
            self.data_types
            or any(named is reference for named in chain)
            or not getattr(reference.source, "IS_EVALUATED_ALIAS", False)
        ):
            return False
        if self.is_tuple or any(
            _container_name(self, container) is not None for container in self._TYPE_HINT_CONTAINER_ORDER
        ):
            return False
        if reference is None:
            members = self.data_types
            return any(member._names_none_alias(chain) for member in members) and all(  # noqa: SLF001
                member._names_none_alias(chain) or member._is_bare_none  # noqa: SLF001
                for member in members
            )
        fields = getattr(reference.source, "fields", ())[:1]
        return any(_writes_none_alone(field, (*chain, reference)) for field in fields)

    @property
    def _is_bare_none(self) -> bool:
        """Return whether the type is the plain type ``None``."""
        return self.type == NONE and not (self.alias or self.reference or self.data_types or self.is_func)

    def _optional_hint(self, type_: str, use_union_operator: bool) -> str:  # noqa: FBT001
        """Write the hint with its None, except for the name of an alias of None, which is None already."""
        if use_union_operator and self.names_none_alias:
            return type_
        return get_optional_type(type_, use_union_operator)

    def _reference_adds_null(self) -> bool:
        """Return whether the type hint adds the None of the nullable model the type names.

        It does not when the model holds null itself and ``--omit-redundant-reference-null`` dropped it at the use.
        """
        return (
            _NULL_STRIPPED_DATA_TYPE_KEY not in self.__dict__
            and _HELD_NULL_DROPPED_DATA_TYPE_KEY not in self.__dict__
            and self.names_nullable_model
        )

    @property
    def has_target_null(self) -> bool:
        """Whether the type's own None is the one its referenced schema admits (``--omit-redundant-reference-null``)."""
        return _TARGET_NULL_DATA_TYPE_KEY in self.__dict__

    @property
    def held_null_dropped(self) -> bool:
        """Whether ``--omit-redundant-reference-null`` dropped the None of the model the type names."""
        return _HELD_NULL_DROPPED_DATA_TYPE_KEY in self.__dict__

    @property
    def admits_none(self) -> bool:
        """Return whether the rendered type hint includes None, without the writes that rendering makes.

        The None is the type's own, that of the nullable model it names, or that of a union member whose hint
        includes None, which the union takes over when it is rendered (``rendered_nullability``).
        """
        return self._rendered_null_source() is not None

    def _rendered_null_source(self) -> NullSource | None:
        """Return why the rendered type hint includes None, reading the members of a union only when they decide."""
        core = _nullability_core()
        members = self.data_types
        return core.rendered_nullability(
            own=self.is_optional,
            reference=self._reference_adds_null(),
            member_nulls=(member._gives_member_null(core) for member in members)  # noqa: SLF001
            if core.takes_member_null(
                is_tuple=self.is_tuple,
                keeps_member_order=self.preserve_union_member_order,
                member_count=len(members),
            )
            else (),
        )

    def _hint_null_source(self) -> NullSource | None:
        """Return why ``type_hint`` writes None: the type's own, its nullable model's, or its members' by their hints.

        A union takes the None of a member whose rendered hint strips it under the union's operator (row M,
        ``legacy-member-null-from-hint-text``), so the members are rendered only when neither of the others decides.
        """
        return _nullability_core().rendered_nullability(
            own=self.is_optional, reference=self._reference_adds_null(), member_nulls=self._iter_hint_member_nulls()
        )

    def _iter_hint_member_nulls(self) -> Iterator[bool]:
        """Yield whether the union, as ``type_hint`` renders it, takes the None of its members."""
        if not (self.alias or self.type) and _nullability_core().takes_member_null(
            is_tuple=self.is_tuple,
            keeps_member_order=self.preserve_union_member_order,
            member_count=len(self.data_types),
        ):
            yield _merge_union_members(
                self, tuple(type_expr_of(member, base=False) for member in self.data_types)
            ).takes_null

    def _gives_member_null(self, core: ModuleType) -> bool:
        """Return whether the type, as a member of a union, gives the union its None (``member_null``)."""
        verdict = core.member_null(is_none=self.type == NONE and not self.data_types, is_any=self.type == ANY)
        return self._rendered_null_source() is not None if verdict is None else verdict

    def _materialize_render_state(self, write: _RenderStateWrite, /) -> None:
        """Write what rendering decides about the type: the writer of its render-time state on shared nodes.

        ``"reference-null"`` writes the None of the nullable model the type names into ``is_optional`` when the
        rendered None comes from that model (``rendered_nullability``), marked as not the type's own.
        ``"member-null"`` writes the None that a union takes over from a member, ``"null-moved-to-union"`` takes the
        None off a member whose union writes it now, and ``"empty-union"`` gives a union without a member to write
        the import of the ``Any`` it renders as. Before render preparation only union flattening and the imports
        that L3 collects (row ``legacy-l3-import-nullability``) write nullability; rendering and import collection
        write none.
        """
        null: NullSource | None = None
        match write:
            case "reference-null":
                if (
                    not (reference := self._reference_adds_null())
                    or (null := _nullability_core().rendered_nullability(own=self.is_optional, reference=reference))
                    != "reference"
                ):
                    return
            case "member-null":
                null = "member"
            case "null-moved-to-union":
                self.is_optional = False
                return
            case "empty-union":
                self.import_ = self.import_ or IMPORT_ANY
                return
            case _:  # pragma: no cover
                assert_never(write)
        self.is_optional = True
        if null == "reference":
            self.__dict__[_REFERENCE_NULL_DATA_TYPE_KEY] = True

    def _strip_null(self, *, own: bool) -> None:
        """Drop the None of the type, for good when it is the type's own rather than its nullable model's.

        A None that only the referenced model admits comes back with the next type hint.
        """
        if own or (self.is_optional and _REFERENCE_NULL_DATA_TYPE_KEY not in self.__dict__):
            self.__dict__[_NULL_STRIPPED_DATA_TYPE_KEY] = True
        self.is_optional = False

    def _optional_before_render_preparation(self) -> bool:
        """Return ``is_optional`` without the None that render preparation wrote (rows MS and SK read it)."""
        return self.is_optional and _RENDER_PREPARATION_NULL_DATA_TYPE_KEY not in self.__dict__

    @property
    def type_hint(self) -> str:
        """Generate the Python type hint string for this DataType."""
        return render_type_expr(_build_type_expr(self, base=False))

    @property
    def is_union(self) -> bool:
        """Return whether this DataType represents a union of multiple types."""
        return len(self.data_types) > 1

    @property
    def _consumed_constraints(self) -> frozenset[str]:
        """The schema keywords the type enforces itself, which a field of the type leaves out of its constraints."""
        return _applicability.NO_KEYWORDS

    # Historical conversion policy retained for direct DataType users and
    # external subclasses. Output backends must declare their own policy.
    _CONSTRAINED_TYPE_TO_BASE: ClassVar[dict[str, str]] = {
        "constr": "str",
    }

    @property
    def base_type_hint(self) -> str:
        """Return a simplified hint using the backend-provided conversion policy.

        The defaults preserve historical direct DataType and external subclass
        behavior. Output-specific subclasses own the policy ClassVars.
        """
        return render_type_expr(_build_type_expr(self, base=True))


DataTypeT = TypeVar("DataTypeT", bound=DataType)


TypeForm: TypeAlias = Literal["written", "base", "single", "union", "tuple", "literal"]
"""What a data type writes when it has no text of its own (an alias or a type name) when it is rendered.

``written`` writes nothing besides that text; ``base`` writes the base of a constrained call (``base_type_hint``, its
own text left out); ``single`` its one nested type or the model it names; ``union`` the union of its nested types;
``tuple`` a tuple of them; ``literal`` a ``Literal`` of its values or of its enum members.
"""

_MAPPING_CONTAINERS: Final = frozenset({"mapping", "dict"})


@final
@dataclass(frozen=True, slots=True)
class TypeLeaf:
    """A type written as text: ``code``, or the hint of ``node``, whose class writes its own (a backend's override)."""

    code: str = ""
    node: DataType | None = None
    base: bool = False


@final
@dataclass(frozen=True, slots=True)
class RefLeaf:
    """The model ``node`` names, written by the name the model has when the expression is rendered."""

    node: DataType


@final
@dataclass(frozen=True, slots=True)
class TypeApply:
    """What one data type writes: its ``form`` over ``args``, inside its container, quoted, with its None or its call.

    The container is the first of the data type's container order that it is in, written as ``container``; a mapping
    writes ``key`` (``str`` when None) before its value type. A ``base`` expression is that of ``base_type_hint``: it
    writes no discriminator, no forward-reference quotes and no call. The data type's text of its own, its names, its
    None and its quotes are read when the expression is rendered, so an expression built before the last rename and
    the last quote writes the final ones.
    """

    node: DataType
    form: TypeForm
    args: tuple[TypeExpr, ...] = ()
    container: str | None = None
    mapping: bool = False
    key: TypeExpr | None = None
    base: bool = False


TypeExpr: TypeAlias = TypeLeaf | RefLeaf | TypeApply
"""A type expression: the structure of a data type tree, with the nodes whose hooks and names write it."""


def _writes_none_alone(field: Any, chain: tuple[Reference, ...]) -> bool:
    """Return whether the field of a type alias writes the value None and nothing else: no annotation, no call."""
    value = field.data_type
    return (value._names_none_alias(chain) or value._is_bare_none) and not (field.annotated or field.field)  # noqa: SLF001


def _container_name(data_type: DataType, container: str) -> str | None:
    """Return the name the data type's container ``container`` is written as, or None when it is not in one."""
    standard = data_type.use_standard_collections
    frozen_set = STANDARD_FROZEN_SET if standard else FROZEN_SET
    name: str | None = None
    match container:
        case "frozen_set" if data_type.is_frozen_set:
            name = frozen_set
        case "set" if data_type.is_set:
            name = frozen_set if data_type.use_generic_container else STANDARD_SET if standard else SET
        case "sequence" if data_type.is_sequence:
            name = SEQUENCE
        case "list" if data_type.is_list:
            name = SEQUENCE if data_type.use_generic_container else STANDARD_LIST if standard else LIST
        case "mapping" if data_type.is_mapping:
            name = MAPPING
        case "dict" if data_type.is_dict:
            name = MAPPING if data_type.use_generic_container else STANDARD_DICT if standard else DICT
    return name


def type_expr_of(data_type: DataType, *, base: bool = False) -> TypeExpr:
    """Return the expression of the data type's type hint, or with ``base`` that of its ``base_type_hint``.

    A data type whose class writes its own hint is a leaf written by that hint, as are such data types inside it.
    """
    data_type_class = type(data_type)
    if (
        data_type_class.base_type_hint is not DataType.base_type_hint
        if base
        else data_type_class.type_hint is not DataType.type_hint
    ):
        return TypeLeaf(node=data_type, base=base)
    return _build_type_expr(data_type, base=base)


def _build_type_expr(data_type: DataType, *, base: bool) -> TypeExpr:
    """Return the expression ``DataType.type_hint``, or with ``base`` ``DataType.base_type_hint``, writes.

    The base hint writes a constrained call as the base type the backend's ``_CONSTRAINED_TYPE_TO_BASE`` gives it, or
    as the type hint when it gives none.
    """
    if base and data_type.is_func and data_type.kwargs and (written := data_type.alias or data_type.type):
        if (base_type := data_type._CONSTRAINED_TYPE_TO_BASE.get(written)) is None:  # noqa: SLF001
            return type_expr_of(data_type, base=False)
        return TypeApply(data_type, "base", (TypeLeaf(base_type),), base=True)
    form: TypeForm = "written"
    args: tuple[TypeExpr, ...] = ()
    if not (data_type.alias or data_type.type):
        members = data_type.data_types
        if data_type.is_tuple:
            count = data_type.tuple_item_count
            form = "tuple"
            items = members if count is None else members[:1]
            args = () if count == 0 else tuple(type_expr_of(item, base=base) for item in items)
        elif len(members) > 1:
            form, args = "union", tuple(type_expr_of(member, base=base) for member in members)
        elif members:
            form, args = "single", (type_expr_of(members[0], base=base),)
        elif data_type.enum_member_literals or data_type.literals:
            form = "literal"
        elif data_type.reference:
            form, args = "single", (RefLeaf(data_type),)
    order = data_type._BASE_TYPE_HINT_CONTAINER_ORDER if base else data_type._TYPE_HINT_CONTAINER_ORDER  # noqa: SLF001
    for container in order:
        if (name := _container_name(data_type, container)) is not None:
            mapping = container in _MAPPING_CONTAINERS
            key = type_expr_of(data_type.dict_key, base=base) if mapping and data_type.dict_key else None
            return TypeApply(data_type, form, args, name, mapping, key, base)
    return TypeApply(data_type, form, args, base=base)


def render_type_expr(expr: TypeExpr, /) -> str:
    """Write a type expression, through the rendering hooks of the data types that it holds."""
    if isinstance(expr, TypeApply):
        return _render_apply(expr)
    if isinstance(expr, RefLeaf):
        node = expr.node
        return node._get_wrapped_reference_type_hint(cast("Reference", node.reference).short_name)  # noqa: SLF001
    if (node := expr.node) is None:
        return expr.code
    return node.base_type_hint if expr.base else node.type_hint


def _render_apply(expr: TypeApply) -> str:
    """Write what one data type writes: its own text or its form, its container, its quotes, its None or its call."""
    node = expr.node
    text = node.alias or node.type
    member_null = False
    if expr.form == "base":
        text = render_type_expr(expr.args[0])
    elif not text:
        text, member_null = _render_form(expr)
    if (container := expr.container) is not None:
        if expr.mapping:
            key = STR if expr.key is None else render_type_expr(expr.key)
            text = f"{container}[{key}, {text or ANY}]" if expr.key is not None or text else container
        else:
            text = f"{container}[{text}]" if text else container
    if not expr.base and node._is_forward_reference:  # noqa: SLF001
        text = repr(text) if '"' in text or "\\" in text else f'"{text}"'
    if (
        node.is_optional or member_null or (expr.form != "base" and node._reference_adds_null())  # noqa: SLF001
    ) and text != ANY:
        return node._optional_hint(text, node.use_union_operator)  # noqa: SLF001
    if not expr.base and node.is_func and node.kwargs:
        return f"{text}({', '.join(f'{k}={represent_python_value(v)}' for k, v in node.kwargs.items())})"
    return text


def _render_form(expr: TypeApply) -> tuple[str, bool]:
    """Write the form of a data type without text of its own, and whether a union takes the None of its members."""
    node = expr.node
    text = ""
    member_null = False
    match expr.form:
        case "tuple":
            tuple_type = STANDARD_TUPLE if node.use_standard_collections else TUPLE
            if (count := node.tuple_item_count) is not None:
                item = (render_type_expr(expr.args[0]) or ANY) if expr.args else ANY
                text = f"{tuple_type}[{', '.join(repeat(item, count)) or '()'}]"
            else:
                text = f"{tuple_type}[{', '.join(render_type_expr(item) or ANY for item in expr.args) or '()'}]"
        case "union":
            text, member_null = _render_union(node, expr.args, discriminated=not expr.base)
        case "single":
            text = render_type_expr(expr.args[0])
        case "literal":
            parts = (
                [f"{enum_class}.{member}" for enum_class, member in node.enum_member_literals]
                if node.enum_member_literals
                else [repr(literal) for literal in node.literals]
            )
            text = f"{node._literal_name}[{', '.join(parts)}]"  # noqa: SLF001
    return text, member_null


def _render_union(node: DataType, members: tuple[TypeExpr, ...], *, discriminated: bool) -> tuple[str, bool]:
    """Write a union of member hints, each once, and whether it takes the None of its members, which it writes no more.

    A union that keeps its member order compares the hints as written and takes no None from them.
    """
    if node.preserve_union_member_order:
        union = _union_core()
        merged = union.merge_members(union.WrittenMember(hint, hint) for hint in _union_member_hints(members) if hint)
    else:
        merged = _merge_union_members(node, members)
    kept, takes_null = merged.kept, merged.takes_null
    if node.use_union_operator:
        kept, takes_null = _drop_none_after_none_alias(members, kept, takes_null=takes_null)
    if not kept:
        text = ANY
        node._materialize_render_state("empty-union")  # noqa: SLF001
    elif len(kept) == 1:
        text = kept[0]
    else:
        text = _union_core().join_union(kept, union_operator=node.use_union_operator)
        if (memo := PARSE_MEMO.get()) is not None:
            memo.keep_union_members(NodeKey(id(node)), node, text, kept)
    if discriminated and (discriminator := node.discriminator):
        text = node._wrap_discriminator_type_hint(text, discriminator)  # noqa: SLF001
    return text, takes_null


def _drop_none_after_none_alias(
    members: tuple[TypeExpr, ...], kept: tuple[str, ...], *, takes_null: bool
) -> tuple[tuple[str, ...], bool]:
    """Drop the None that follows the name of an alias of None, which ``|`` cannot join: the alias is None already.

    The names are those of the members that are such aliases; the None is a member written as ``None``, a second such
    name, or the None the union takes over from its members, which a union of that name alone writes no more.
    """
    names = {render_type_expr(member) for member in members if member.node is not None and member.node.names_none_alias}
    if not names:
        return kept, takes_null
    none_valued = {*names, NONE}
    if len(kept) > 1 and kept[0] in none_valued and kept[1] in none_valued:
        kept = (kept[0], *(hint for hint in kept[1:] if hint not in none_valued))
    return kept, takes_null and not (len(kept) == 1 and kept[0] in names)


def _merge_union_members(node: DataType, members: tuple[TypeExpr, ...]) -> MergedMembers[str]:
    """Merge the hints of the union members, taking the None out of each hint that strips it."""
    union = _union_core()
    return union.merge_members(
        union.WrittenMember(
            hint,
            kept := NONE
            if (null := hint == NONE)
            else _remove_none_from_union(hint, use_union_operator=node.use_union_operator),
            null=null,
            strips_null=kept != hint,
        )
        for hint in _union_member_hints(members)
        if hint
    )


def _union_member_hints(members: tuple[TypeExpr, ...]) -> list[str]:
    """Return the hints of union members, spreading a member union that renders as a plain union.

    A member's rendering keeps the hints it joins in the parse's memo, or outside a parse in the rendering's own.
    """
    if (memo := PARSE_MEMO.get()) is not None:
        return _spread_union_member_hints(memo, members)
    with rendering_memo() as memo:
        return _spread_union_member_hints(memo, members)


def _spread_union_member_hints(memo: ParseMemo, members: tuple[TypeExpr, ...]) -> list[str]:
    hints: list[str] = []
    for member in members:
        joins = memo.joins
        hint = render_type_expr(member)
        node = cast("DataType", member.node)
        if spread := memo.union_members(NodeKey(id(node)), node, hint, joins):
            hints.extend(spread)
        else:
            hints.append(hint)
    return hints


class EmptyDataType(DataType):
    """A DataType placeholder for empty or unresolved types."""


class Types(Enum):
    """Standard type identifiers for schema type mapping."""

    integer = auto()
    int32 = auto()
    int64 = auto()
    number = auto()
    float = auto()
    double = auto()
    decimal = auto()
    time = auto()
    string = auto()
    byte = auto()
    binary = auto()
    date = auto()
    date_time = auto()
    date_time_local = auto()
    time_local = auto()
    timedelta = auto()
    password = auto()
    path = auto()
    email = auto()
    uuid = auto()
    uuid1 = auto()
    uuid2 = auto()
    uuid3 = auto()
    uuid4 = auto()
    uuid5 = auto()
    ulid = auto()
    uri = auto()
    hostname = auto()
    ipv4 = auto()
    ipv4_network = auto()
    ipv6 = auto()
    ipv6_network = auto()
    boolean = auto()
    object = auto()
    null = auto()
    array = auto()
    any = auto()


@cache
def _create_context_data_type(  # noqa: PLR0913, PLR0917
    model_name: str,
    base: type[DataType],
    python_version: PythonVersion,
    use_standard_collections: bool,  # noqa: FBT001
    use_generic_container: bool,  # noqa: FBT001
    use_union_operator: bool,  # noqa: FBT001
    treat_dot_as_module: bool | None,  # noqa: FBT001
    use_serialize_as_any: bool,  # noqa: FBT001
) -> type[DataType]:
    """Create or reuse a DataType subclass with context-specific defaults.

    Building a pydantic model class is expensive; the class is fully determined
    by its arguments, so identical configurations share one class.
    """
    context_data_type: type[DataType] = create_model(
        model_name,
        python_version=(PythonVersion, python_version),
        use_standard_collections=(bool, use_standard_collections),
        use_generic_container=(bool, use_generic_container),
        use_union_operator=(bool, use_union_operator),
        treat_dot_as_module=(bool, treat_dot_as_module),
        use_serialize_as_any=(bool, use_serialize_as_any),
        __base__=base,
    )
    from datamodel_code_generator.model import _rebuild_model_with_datamodel_namespace  # noqa: PLC0415

    _rebuild_model_with_datamodel_namespace(context_data_type)
    return context_data_type


NUMBER_KEYWORDS: frozenset[str] = _applicability.NUMBER_KEYWORDS
NUMBER_BOUND_KEYWORDS: frozenset[str] = _applicability.NUMBER_BOUND_KEYWORDS
LENGTH_KEYWORDS: frozenset[str] = _applicability.LENGTH_KEYWORDS
STRING_KEYWORDS: frozenset[str] = _applicability.STRING_KEYWORDS
ARRAY_KEYWORDS: frozenset[str] = _applicability.ARRAY_KEYWORDS
SEQUENCE_KEYWORDS: frozenset[str] = _applicability.SEQUENCE_KEYWORDS
TYPE_DEPENDENT_KEYWORDS: frozenset[str] = _applicability.TYPE_DEPENDENT_KEYWORDS
_NO_KEYWORDS: frozenset[str] = _applicability.NO_KEYWORDS
_UNION_PATHS: frozenset[str] = frozenset({"typing.Union", "typing.Optional", UNION, OPTIONAL})
_QUALIFIER_PATHS: frozenset[str] = frozenset({"typing.Annotated", "typing.Final", IMPORT_ANNOTATED.import_, "Final"})
_LITERAL_VALUE_TYPE_PATHS: Mapping[type, str] = MappingProxyType({
    str: "str",
    bytes: "bytes",
    int: "int",
    float: "float",
    bool: "bool",
})
_STANDARD_CLASS_PATHS: Mapping[str, str] = MappingProxyType({
    "typing.Text": "str",
    "typing.List": "list",
    "typing.Tuple": "tuple",
    "typing.Set": "set",
    "typing.FrozenSet": "frozenset",
    "typing.AbstractSet": "frozenset",
    "collections.abc.Set": "frozenset",
    "typing.Deque": "collections.deque",
    **{f"typing.{name}": f"collections.abc.{name}" for name in ("Sized", "Iterable", "Generator", "ByteString")},
    **{
        f"{module}.{name}": path
        for module in ("typing", "collections.abc")
        for name, path in (
            ("Sequence", "list"),
            ("MutableSequence", "list"),
            ("Collection", "list"),
            ("MutableSet", "set"),
        )
    },
})


def _standard_type_path(path: str, target_version: tuple[int, int]) -> str:
    """Return the path that classifies a type: unprefixed builtins, typing_extensions as typing, resolved aliases."""
    path = path.removeprefix("builtins.")
    if path.startswith("typing_extensions."):
        from datamodel_code_generator._python_type_import_registry import (  # noqa: PLC0415
            is_known_python_type_path,
        )

        if is_known_python_type_path(
            typing_path := f"typing.{path.removeprefix('typing_extensions.')}", target_version
        ):
            path = typing_path
    return _STANDARD_CLASS_PATHS.get(path, path)


def _annotation_base_path(expression: PythonTypeExpr, target_version: tuple[int, int]) -> tuple[PythonTypeExpr, str]:
    """Return the base of an annotation, the type it subscripts or itself, and the path that classifies that base."""
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        PythonTypeBoundName,
        PythonTypeSubscript,
        iter_python_type_expr_qualified_names,
        python_type_expr_base_name,
    )

    base = expression.base if isinstance(expression, PythonTypeSubscript) else expression
    return base, _standard_type_path(
        f"{base.import_from}.{base.import_name}"
        if isinstance(base, PythonTypeBoundName) and base.import_from
        else next(iter_python_type_expr_qualified_names(base), "") or python_type_expr_base_name(base),
        target_version,
    )


def _qualified_type_path(expression: PythonTypeExpr, target_version: tuple[int, int]) -> str:
    """Return the path that classifies the type an annotation names, that of the type Annotated or Final qualifies."""
    from datamodel_code_generator._python_type_annotation import python_type_expr_arguments  # noqa: PLC0415

    base, path = _annotation_base_path(expression, target_version)
    if base is expression or path not in _QUALIFIER_PATHS:
        return path
    return next(
        (_qualified_type_path(member, target_version) for member in python_type_expr_arguments(expression)[:1]), path
    )


@cache
def _import_paths(module: ModuleType) -> frozenset[str]:
    """Return the import path of every type or name a generator imports module declares."""
    return frozenset(
        f"{import_.from_}.{import_.import_}" for import_ in vars(module).values() if isinstance(import_, Import)
    )


def _is_value_literal(data_type: DataType) -> bool:
    """Return whether a type is the ``Literal`` of the values of a const or an enum."""
    return bool(data_type.literals) and not data_type.type and data_type.import_ is None


class DataTypeManager(ABC):
    """Abstract base class for managing type mappings in code generation.

    Subclasses implement get_data_type() to map schema types to DataType objects.
    """

    HOSTNAME_REGEX: ClassVar[str] = (
        r"^(([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])\.)*"
        r"([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9\-]{0,61}[A-Za-z0-9])$"
    )
    omit_redundant_constraints: bool = False
    CONSTRAINED_TYPE_CONSUMED_KEYS: ClassVar[dict[str, tuple[str, ...]]] = dict(
        _applicability.BASE_OUTPUT.zero_bound_classes
    )
    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = False
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = None
    DEFAULT_VALUE_DESCRIPTORS: ClassVar[Mapping[tuple[str | None, str], DefaultValueDescriptor]] = MappingProxyType({})
    CONSTRAINT_KEYWORDS: ClassVar[Mapping[str, frozenset[str]]] = _applicability.BASE_OUTPUT.type_keywords
    KNOWN_TYPE_IMPORTS: ClassVar[tuple[ModuleType, ...]] = (generator_imports,)
    NUMBER_VALUE_TYPES: ClassVar[frozenset[str]] = _applicability.BASE_OUTPUT.number_value_types
    """Known types that a number is a value of, which check the number keywords of a number schema on it."""
    TARGET_NUMBER_KEYWORDS: ClassVar[Mapping[str, tuple[PydanticConstruct, frozenset[str]]]] = (
        _applicability.BASE_OUTPUT.target_number_keywords
    )
    """Number keywords a type takes once the target version of the output model has a construct, by the type's path."""
    ANNOTATION_UNIONS_TAKE_CONSTRAINTS: ClassVar[bool] = _applicability.BASE_OUTPUT.annotation_unions_take
    FIXED_TUPLES_TAKE_LENGTH: ClassVar[bool] = _applicability.BASE_OUTPUT.fixed_tuples_take_length
    LITERALS_TAKE_CONSTRAINTS: ClassVar[bool] = _applicability.BASE_OUTPUT.literals_take
    _WRITES_CONSTRAINED_TYPES: ClassVar[bool] = _applicability.BASE_OUTPUT.writes_constrained_types
    """Whether the type manager writes scalar constraints into ``con*`` types (``OutputKeywords``)."""

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        python_version: PythonVersion = PythonVersionMin,
        use_standard_collections: bool = False,  # noqa: FBT001, FBT002
        use_generic_container_types: bool = False,  # noqa: FBT001, FBT002
        strict_types: Sequence[StrictTypes] | None = None,
        use_non_positive_negative_number_constrained_types: bool = False,  # noqa: FBT001, FBT002
        use_decimal_for_multiple_of: bool = False,  # noqa: FBT001, FBT002
        use_union_operator: bool = False,  # noqa: FBT001, FBT002
        use_pendulum: bool = False,  # noqa: FBT001, FBT002
        use_standard_primitive_types: bool = False,  # noqa: FBT001, FBT002, ARG002
        use_object_type: bool = False,  # noqa: FBT001, FBT002
        target_datetime_class: DatetimeClassType | None = None,
        target_date_class: DateClassType | None = None,
        treat_dot_as_module: bool | None = None,  # noqa: FBT001
        use_serialize_as_any: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        """Initialize DataTypeManager with code generation options."""
        self.python_version = python_version
        self.use_standard_collections: bool = use_standard_collections
        self.use_generic_container_types: bool = use_generic_container_types
        self.strict_types: Sequence[StrictTypes] = strict_types or ()
        self.use_non_positive_negative_number_constrained_types: bool = (
            use_non_positive_negative_number_constrained_types
        )
        self.use_decimal_for_multiple_of: bool = use_decimal_for_multiple_of
        self.use_union_operator: bool = use_union_operator
        self.use_pendulum: bool = use_pendulum
        self.use_object_type: bool = use_object_type
        self.target_datetime_class: DatetimeClassType | None = target_datetime_class
        self.target_date_class: DateClassType | None = target_date_class
        self.treat_dot_as_module: bool = treat_dot_as_module or False
        self.use_serialize_as_any: bool = use_serialize_as_any

        self.data_type: type[DataType] = _create_context_data_type(
            "ContextDataType",
            DataType,
            python_version,
            use_standard_collections,
            use_generic_container_types,
            use_union_operator,
            treat_dot_as_module,
            use_serialize_as_any,
        )

    @abstractmethod
    def get_data_type(self, types: Types, **kwargs: Any) -> DataType:
        """Map a Types enum value to a DataType. Must be implemented by subclasses."""
        raise NotImplementedError

    def _non_finite_multiple_of(self, types: Types, kwargs: Mapping[str, Any]) -> float | None:  # noqa: ARG002, PLR6301
        """Return the ``multipleOf`` that ``get_data_type`` leaves out of the type as the backend refuses it, if any."""
        return None

    def get_default_value_descriptor(self, data_type: DataType) -> DefaultValueDescriptor | None:
        """Return this backend's semantic descriptor for an emitted scalar import."""
        if (import_ := data_type.import_) is None:
            return None
        return self.DEFAULT_VALUE_DESCRIPTORS.get((import_.from_, import_.import_))

    @staticmethod
    def copy_data_type(data_type: DataType) -> DataType:
        """Copy a type-map prototype for caller-owned mutation.

        Leaf prototypes (no nested data types, reference, dict key, or parent)
        take a shallow-copy fast path with fresh mutable containers, which is
        equivalent to deepcopy because every other shared value is immutable
        (scalars, enums, and the frozen Import dataclass).
        """
        if (
            data_type.data_types
            or data_type.reference is not None
            or data_type.dict_key is not None
            or data_type.parent is not None
        ):
            copied_data_type = deepcopy(data_type)
            for nested_data_type in copied_data_type.all_data_types:
                nested_data_type.children = []
            return copied_data_type
        copied_data_type = data_type.model_copy()
        copied_data_type.data_types = []
        copied_data_type.children = []
        copied_data_type.literals = list(data_type.literals)
        copied_data_type.enum_member_literals = list(data_type.enum_member_literals)
        if (kwargs := data_type.kwargs) is not None:
            copied_data_type.kwargs = deepcopy(kwargs)
        return copied_data_type

    @staticmethod
    def _shape_constraint_keywords(data_type: DataType) -> frozenset[str] | None:
        """Return the keywords a container or a referenced model takes, or None for any other type."""
        return _applicability.shape_keywords(
            mapping=data_type.is_dict or data_type.is_mapping,
            sequence=any((
                data_type.is_list,
                data_type.is_set,
                data_type.is_frozen_set,
                data_type.is_sequence,
                data_type.is_tuple,
            )),
            reference=bool(data_type.reference),
        )

    @classmethod
    def constraint_keywords(
        cls,
        data_type: DataType,
        target_version: tuple[int, int],
        *,
        number_values: bool = False,
        target_model_version: TargetPydanticVersion | str | None = None,
    ) -> frozenset[str]:
        """Return the type-dependent keywords that every member of an emitted type takes in this output model.

        A union takes what every member other than ``None`` takes, as a union annotation does, and the ``Literal`` of a
        const or an enum takes what every value other than ``None`` takes as an instance of its type, as a ``Literal``
        annotation does. With ``number_values`` the keywords that a number value of the type meets are returned
        instead: those a member takes, as a number validates as that member, where the ``Literal`` of a const or an
        enum with a number value and a type of ``NUMBER_VALUE_TYPES`` take the number keywords, and an explicit
        ``target_model_version`` adds the ``TARGET_NUMBER_KEYWORDS`` it reaches.
        """
        if (keywords := cls._shape_constraint_keywords(data_type)) is not None:
            return keywords
        if data_type.data_types:
            return _applicability.member_keywords(
                (
                    cls.constraint_keywords(
                        member,
                        target_version,
                        number_values=number_values,
                        target_model_version=target_model_version,
                    )
                    for member in data_type.data_types
                    if member.type != NONE or member.data_types
                ),
                number_values=number_values,
            )
        if _is_value_literal(data_type):
            if number_values:
                return _applicability.literal_number_keywords(
                    literals_take=cls.LITERALS_TAKE_CONSTRAINTS,
                    has_non_string_value=any(type(value) is not str for value in data_type.literals),
                )
            return cls._literal_keywords(
                {_LITERAL_VALUE_TYPE_PATHS.get(type(value), "") for value in data_type.literals if value is not None},
                target_version,
            )
        if data_type.python_type is None:
            import_ = data_type.import_
            return cls.type_constraint_keywords(
                f"{import_.from_}.{import_.import_}" if import_ and import_.from_ else data_type.type or "",
                target_version,
                number_values=number_values,
                target_model_version=target_model_version,
            )
        return cls._annotation_constraint_keywords(
            data_type.python_type.expression,
            target_version,
            number_values=number_values,
            target_model_version=target_model_version,
        )

    @classmethod
    def _annotation_constraint_keywords(
        cls,
        expression: PythonTypeExpr,
        target_version: tuple[int, int],
        *,
        number_values: bool = False,
        target_model_version: TargetPydanticVersion | str | None = None,
    ) -> frozenset[str]:
        """Return the keywords every member of a union takes, or those of the type that Annotated or Final qualifies.

        A ``Literal`` takes the keywords every value other than ``None`` takes as an instance of its type, where a
        ``bool`` takes none and a nested ``Literal`` contributes its values. With ``number_values`` a union and a
        ``Literal`` take what one member or value takes, as for ``constraint_keywords``, where a value of a type the
        generator cannot classify, such as an enum member, takes them all.
        """
        from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
            PythonTypeEllipsis,
            PythonTypeUnion,
            python_type_expr_arguments,
            python_type_expr_base_name,
        )

        base, path = _annotation_base_path(expression, target_version)
        members = None
        if isinstance(expression, PythonTypeUnion) or (base is not expression and path in _UNION_PATHS):
            if not cls.ANNOTATION_UNIONS_TAKE_CONSTRAINTS:
                return _NO_KEYWORDS
            members = tuple(
                member
                for member in python_type_expr_arguments(expression)
                if python_type_expr_base_name(member) != NONE
            )
        elif base is not expression and path in _QUALIFIER_PATHS:
            members = python_type_expr_arguments(expression)[:1]
        if members is not None:
            return _applicability.member_keywords(
                (
                    cls._annotation_constraint_keywords(
                        member,
                        target_version,
                        number_values=number_values,
                        target_model_version=target_model_version,
                    )
                    for member in members
                ),
                number_values=number_values,
            )
        if path == "typing.Literal" and base is not expression:
            return cls._literal_keywords(
                set(cls._literal_value_paths(expression)), target_version, number_values=number_values
            )
        if (
            path == "tuple"
            and base is not expression
            and not cls.FIXED_TUPLES_TAKE_LENGTH
            and not any(isinstance(argument, PythonTypeEllipsis) for argument in python_type_expr_arguments(expression))
        ):
            return _NO_KEYWORDS
        return cls.type_constraint_keywords(
            path, target_version, number_values=number_values, target_model_version=target_model_version
        )

    @classmethod
    def _literal_keywords(
        cls, value_paths: Iterable[str], target_version: tuple[int, int], *, number_values: bool = False
    ) -> frozenset[str]:
        """Return the keywords a ``Literal`` takes: those every value takes as an instance of its type at its path.

        A value of a type the generator cannot classify (path ``""``) takes none, or all of them with
        ``number_values``, where a ``Literal`` takes what one value takes; an output model that checks no keyword on
        a ``Literal`` takes none.
        """
        if not cls.LITERALS_TAKE_CONSTRAINTS:
            return _NO_KEYWORDS
        return _applicability.member_keywords(
            (
                cls.type_constraint_keywords(value, target_version, number_values=number_values)
                if value or not number_values
                else TYPE_DEPENDENT_KEYWORDS
                for value in value_paths
            ),
            number_values=number_values,
        )

    @staticmethod
    def _literal_value_paths(expression: PythonTypeExpr) -> Iterator[str]:
        """Yield the type path of each value of a ``Literal`` other than ``None``, or ``""`` for an unclassified one."""
        from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
            PythonTypeLiteralValue,
            PythonTypeSubscript,
            python_type_expr_arguments,
            python_type_expr_base_name,
        )

        for argument in python_type_expr_arguments(expression):
            if isinstance(argument, PythonTypeSubscript) and python_type_expr_base_name(argument) == "Literal":
                yield from DataTypeManager._literal_value_paths(argument)
            elif python_type_expr_base_name(argument) != NONE:
                yield (
                    _LITERAL_VALUE_TYPE_PATHS.get(type(argument.value), "")
                    if isinstance(argument, PythonTypeLiteralValue)
                    else ""
                )

    @classmethod
    def type_constraint_keywords(
        cls,
        path: str,
        target_version: tuple[int, int],
        *,
        number_values: bool = False,
        target_model_version: TargetPydanticVersion | str | None = None,
    ) -> frozenset[str]:
        """Return the type-dependent keywords a type takes: its table entry, none for a known type, else all of them.

        The table lists the keywords that the output model enforces on a type across its supported versions.
        A typing alias or an abstract collection takes what the builtin or standard-library class it stands for takes.
        A known type is a Python builtin or a standard-library type of the import registry that the target has, or a
        type that the generator itself imports; a type the generator cannot classify keeps the keywords the schema
        declares. With ``number_values`` a type of ``NUMBER_VALUE_TYPES`` takes the number keywords, and a type of
        ``TARGET_NUMBER_KEYWORDS`` also those an explicit ``target_model_version`` reaches.
        """
        path = _standard_type_path(path, target_version)
        if (
            keywords := _applicability.listed_type_keywords(
                path,
                type_keywords=cls.CONSTRAINT_KEYWORDS,
                number_value_types=cls.NUMBER_VALUE_TYPES,
                target_number_keywords=cls.TARGET_NUMBER_KEYWORDS,
                number_values=number_values,
                target_model_version=target_model_version,
            )
        ) is not None:
            return keywords
        if not path or any(path in _import_paths(module) for module in cls.KNOWN_TYPE_IMPORTS):
            return _applicability.unlisted_type_keywords(known=True)
        from datamodel_code_generator._python_type_import_registry import (  # noqa: PLC0415
            is_known_python_type_path,
        )

        return _applicability.unlisted_type_keywords(known=is_known_python_type_path(path, target_version))

    def get_data_type_from_full_path(self, full_path: str, is_custom_type: bool) -> DataType:  # noqa: FBT001
        """Create a DataType from a fully qualified Python path."""
        return self.data_type.from_import(Import.from_full_path(full_path), is_custom_type=is_custom_type)
