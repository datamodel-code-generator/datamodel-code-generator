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
from re import Pattern
from types import MappingProxyType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Optional,
    Protocol,
    TypeVar,
    Union,
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
from typing_extensions import TypeIs

from datamodel_code_generator._format_types import (
    DateClassType,
    DatetimeClassType,
    PythonVersion,
    PythonVersionMin,
)
from datamodel_code_generator.enums import DefaultValueType
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

    from datamodel_code_generator._python_type_binding import BoundPythonType
    from datamodel_code_generator.enums import StrictTypes

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


class DataType(_BaseModel):
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

    SUPPORTS_SERIALIZE_AS_ANY: ClassVar[bool] = False
    _exclude_fields: ClassVar[set[str]] = {"parent", "children"}
    _pass_fields: ClassVar[set[str]] = {"parent", "children", "data_types", "reference"}

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
        yield (self.is_optional and not self.use_union_operator, IMPORT_OPTIONAL)
        yield (len(self.data_types) > 1 and not self.use_union_operator, IMPORT_UNION)
        yield (bool(self.literals) or bool(self.enum_member_literals), IMPORT_LITERAL)
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

        # Single-pass optimization: detect ANY+optional and non-ANY types together
        # This is a rare edge case optimization - pragma: no cover
        any_optional_found = False
        has_non_any = False
        for type_ in self.data_types:
            if type_.type == ANY and type_.is_optional:
                any_optional_found = True  # pragma: no cover
            elif type_.type != ANY:
                has_non_any = True
            # Early exit if both conditions met
            if any_optional_found and has_non_any:  # pragma: no cover
                break

        if any_optional_found and has_non_any:  # pragma: no cover
            self.is_optional = True
            self.data_types = [t for t in self.data_types if not (t.type == ANY and t.is_optional)]

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

    def _render_nested_type_hint(
        self,
        *,
        use_base_type_hint: bool,
        wrap_discriminator: bool,
    ) -> str:
        if self.is_tuple:
            tuple_type = STANDARD_TUPLE if self.use_standard_collections else TUPLE
            if self.tuple_item_count == 0:
                type_ = f"{tuple_type}[()]"
            elif self.tuple_item_count is not None:
                item_type = ANY
                if self.data_types:
                    item = self.data_types[0]
                    item_type = (item.base_type_hint if use_base_type_hint else item.type_hint) or ANY
                type_ = f"{tuple_type}[{', '.join(repeat(item_type, self.tuple_item_count))}]"
            else:
                inner_types = [
                    (item.base_type_hint if use_base_type_hint else item.type_hint) or ANY for item in self.data_types
                ]
                type_ = f"{tuple_type}[{', '.join(inner_types)}]" if inner_types else f"{tuple_type}[()]"
        elif self.is_union:
            type_ = self._render_union_type_hint(
                use_base_type_hint=use_base_type_hint,
                wrap_discriminator=wrap_discriminator,
            )
        elif len(self.data_types) == 1:
            data_type = self.data_types[0]
            type_ = data_type.base_type_hint if use_base_type_hint else data_type.type_hint
        elif self.enum_member_literals:
            parts = [f"{enum_class}.{member}" for enum_class, member in self.enum_member_literals]
            type_ = f"{LITERAL}[{', '.join(parts)}]"
        elif self.literals:
            type_ = f"{LITERAL}[{', '.join(repr(literal) for literal in self.literals)}]"
        elif self.reference:
            type_ = self.reference.short_name
            type_ = self._get_wrapped_reference_type_hint(type_)
        else:
            # TODO support strict Any
            type_ = ""

        return type_

    def _render_union_type_hint(
        self,
        *,
        use_base_type_hint: bool,
        wrap_discriminator: bool,
    ) -> str:
        if self.preserve_union_member_order:
            return self._render_ordered_union_type_hint(
                use_base_type_hint=use_base_type_hint,
                wrap_discriminator=wrap_discriminator,
            )

        data_types: list[str] = []
        for data_type in self.data_types:
            data_type_type = data_type.base_type_hint if use_base_type_hint else data_type.type_hint
            if not data_type_type or data_type_type in data_types:
                continue

            if data_type_type == NONE:
                self.is_optional = True
                continue

            non_optional_data_type_type = _remove_none_from_union(
                data_type_type, use_union_operator=self.use_union_operator
            )

            if non_optional_data_type_type != data_type_type:
                self.is_optional = True

            data_types.append(non_optional_data_type_type)

        if not data_types:
            type_ = ANY
            self.import_ = self.import_ or IMPORT_ANY
        elif len(data_types) == 1:
            type_ = data_types[0]
        elif self.use_union_operator:
            type_ = UNION_OPERATOR_DELIMITER.join(data_types)
        else:
            type_ = f"{UNION_PREFIX}{UNION_DELIMITER.join(data_types)}]"

        if wrap_discriminator and (discriminator := self.discriminator):
            type_ = self._wrap_discriminator_type_hint(type_, discriminator)
        return type_

    def _render_ordered_union_type_hint(
        self,
        *,
        use_base_type_hint: bool,
        wrap_discriminator: bool,
    ) -> str:
        data_types: list[str] = []
        seen_data_types: set[str] = set()
        for data_type in self.data_types:
            data_type_type = data_type.base_type_hint if use_base_type_hint else data_type.type_hint
            if not data_type_type or data_type_type in seen_data_types:
                continue
            seen_data_types.add(data_type_type)
            data_types.append(data_type_type)

        match len(data_types):
            case 0:
                type_ = ANY
                self.import_ = self.import_ or IMPORT_ANY
            case 1:
                type_ = data_types[0]
            case _ if self.use_union_operator:
                type_ = UNION_OPERATOR_DELIMITER.join(data_types)
            case _:
                type_ = f"{UNION_PREFIX}{UNION_DELIMITER.join(data_types)}]"

        if wrap_discriminator and (discriminator := self.discriminator):
            type_ = self._wrap_discriminator_type_hint(type_, discriminator)
        return type_

    def _apply_nullable_from_reference(self) -> None:
        if not self.reference:
            return

        source = self.reference.source
        if getattr(source, "is_alias", False):
            return
        if getattr(source, "nullable", False):
            self.is_optional = True

    def _wrap_dict_type_hint(
        self,
        type_: str,
        dict_: str,
        *,
        use_base_type_hint: bool,
    ) -> str:
        if self.dict_key or type_:
            key = (
                (self.dict_key.base_type_hint if use_base_type_hint else self.dict_key.type_hint)
                if self.dict_key
                else STR
            )
            return f"{dict_}[{key}, {type_ or ANY}]"
        return dict_

    def _wrap_frozen_set_type_hint(self, type_: str, _use_base_type_hint: bool) -> str:  # noqa: FBT001
        set_ = STANDARD_FROZEN_SET if self.use_standard_collections else FROZEN_SET
        return f"{set_}[{type_}]" if type_ else set_

    def _wrap_set_type_hint(self, type_: str, _use_base_type_hint: bool) -> str:  # noqa: FBT001
        if self.use_generic_container:
            set_ = STANDARD_FROZEN_SET if self.use_standard_collections else FROZEN_SET
        elif self.use_standard_collections:
            set_ = STANDARD_SET
        else:
            set_ = SET
        return f"{set_}[{type_}]" if type_ else set_

    @staticmethod
    def _wrap_sequence_type_hint(type_: str, _use_base_type_hint: bool) -> str:  # noqa: FBT001
        return f"{SEQUENCE}[{type_}]" if type_ else SEQUENCE

    def _wrap_list_type_hint(self, type_: str, _use_base_type_hint: bool) -> str:  # noqa: FBT001
        if self.use_generic_container:
            list_ = SEQUENCE
        elif self.use_standard_collections:
            list_ = STANDARD_LIST
        else:
            list_ = LIST
        return f"{list_}[{type_}]" if type_ else list_

    def _wrap_mapping_type_hint(self, type_: str, use_base_type_hint: bool) -> str:  # noqa: FBT001
        return self._wrap_dict_type_hint(type_, MAPPING, use_base_type_hint=use_base_type_hint)

    def _wrap_builtin_dict_type_hint(self, type_: str, use_base_type_hint: bool) -> str:  # noqa: FBT001
        if self.use_generic_container:
            dict_ = MAPPING
        elif self.use_standard_collections:
            dict_ = STANDARD_DICT
        else:
            dict_ = DICT
        return self._wrap_dict_type_hint(type_, dict_, use_base_type_hint=use_base_type_hint)

    def _container_type_hint_renderer(
        self,
        container: str,
    ) -> Callable[[str, bool], str] | None:
        renderer: Callable[[str, bool], str] | None = None
        match container:
            case "frozen_set" if self.is_frozen_set:
                renderer = self._wrap_frozen_set_type_hint
            case "set" if self.is_set:
                renderer = self._wrap_set_type_hint
            case "sequence" if self.is_sequence:
                renderer = self._wrap_sequence_type_hint
            case "list" if self.is_list:
                renderer = self._wrap_list_type_hint
            case "mapping" if self.is_mapping:
                renderer = self._wrap_mapping_type_hint
            case "dict" if self.is_dict:
                renderer = self._wrap_builtin_dict_type_hint
        return renderer

    def _wrap_container_type_hint(
        self,
        type_: str,
        container_order: tuple[str, ...],
        *,
        use_base_type_hint: bool,
    ) -> str:
        for container in container_order:
            if renderer := self._container_type_hint_renderer(container):
                return renderer(type_, use_base_type_hint)

        return type_

    @property
    def type_hint(self) -> str:
        """Generate the Python type hint string for this DataType."""
        type_: str | None = self.alias or self.type
        if not type_:
            type_ = self._render_nested_type_hint(use_base_type_hint=False, wrap_discriminator=True)
        if self.reference:
            self._apply_nullable_from_reference()
        has_collection_container = self.is_frozen_set or self.is_set or self.is_sequence
        has_mapping_container = self.is_list or self.is_mapping or self.is_dict
        if has_collection_container or has_mapping_container:
            type_ = self._wrap_container_type_hint(
                type_,
                self._TYPE_HINT_CONTAINER_ORDER,
                use_base_type_hint=False,
            )
        if self.is_optional and type_ != ANY:
            return get_optional_type(type_, self.use_union_operator)
        if self.is_func and self.kwargs:
            kwargs: str = ", ".join(f"{k}={represent_python_value(v)}" for k, v in self.kwargs.items())
            return f"{type_}({kwargs})"
        return type_

    @property
    def is_union(self) -> bool:
        """Return whether this DataType represents a union of multiple types."""
        return len(self.data_types) > 1

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
        if self.is_func and self.kwargs:
            type_: str | None = self.alias or self.type
            if type_:  # pragma: no branch
                base_type = self._CONSTRAINED_TYPE_TO_BASE.get(type_)
                if base_type is None:
                    # Preserve the rendered call when the backend policy does not simplify it.
                    return self.type_hint
                if self.is_optional and base_type != ANY:  # pragma: no cover
                    return get_optional_type(base_type, self.use_union_operator)
                return base_type

        type_: str | None = self.alias or self.type
        if not type_:
            type_ = self._render_nested_type_hint(use_base_type_hint=True, wrap_discriminator=False)
        if self.reference:
            self._apply_nullable_from_reference()
        if self.is_list or self.is_set or self.is_dict:
            type_ = self._wrap_container_type_hint(
                type_,
                self._BASE_TYPE_HINT_CONTAINER_ORDER,
                use_base_type_hint=True,
            )

        if self.is_optional and type_ != ANY:
            return get_optional_type(type_, self.use_union_operator)
        return type_


DataTypeT = TypeVar("DataTypeT", bound=DataType)


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


class DataTypeManager(ABC):
    """Abstract base class for managing type mappings in code generation.

    Subclasses implement get_data_type() to map schema types to DataType objects.
    """

    HOSTNAME_REGEX: ClassVar[str] = (
        r"^(([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])\.)*"
        r"([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9\-]{0,61}[A-Za-z0-9])$"
    )
    CONSTRAINED_TYPE_CONSUMED_KEYS: ClassVar[dict[str, tuple[str, ...]]] = {}
    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = False
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = None
    DEFAULT_VALUE_DESCRIPTORS: ClassVar[Mapping[tuple[str | None, str], DefaultValueDescriptor]] = MappingProxyType({})

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

    def get_data_type_from_full_path(self, full_path: str, is_custom_type: bool) -> DataType:  # noqa: FBT001
        """Create a DataType from a fully qualified Python path."""
        return self.data_type.from_import(Import.from_full_path(full_path), is_custom_type=is_custom_type)
