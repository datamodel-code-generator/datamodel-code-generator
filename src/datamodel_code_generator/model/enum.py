"""Enumeration model generator.

Provides Enum, StrEnum, and specialized enum classes for code generation.
"""

from __future__ import annotations

from functools import lru_cache
from math import isnan
from typing import TYPE_CHECKING, Any, ClassVar, Optional

from typing_extensions import Sentinel, assert_never, override

from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._parse_memo import MemoTable, memoized
from datamodel_code_generator._records import record
from datamodel_code_generator.imports import IMPORT_ANY, IMPORT_ENUM, IMPORT_INT_ENUM, IMPORT_STR_ENUM, Import
from datamodel_code_generator.model import DataModel, DataModelFieldBase
from datamodel_code_generator.model.base import UNDEFINED, BaseClassDataType, field_imports, forget_model_memos
from datamodel_code_generator.python_literal import _semantic_value_text
from datamodel_code_generator.types import DataType, Types

if TYPE_CHECKING:
    from collections import defaultdict
    from pathlib import Path

    from datamodel_code_generator.model._decisions.enum_kind import EnumKind
    from datamodel_code_generator.reference import Reference


_INT: str = "int"
_FLOAT: str = "float"
_BYTES: str = "bytes"
_STR: str = "str"
_JSON_NUMBER_KEY = Sentinel("_JSON_NUMBER_KEY")
_JSON_NAN_KEY = Sentinel("_JSON_NAN_KEY")

escape_characters = str.maketrans({
    "\u0000": r"\x00",  # Null byte
    "\\": r"\\",
    "'": r"\'",
    "\b": r"\b",
    "\f": r"\f",
    "\n": r"\n",
    "\r": r"\r",
    "\t": r"\t",
})


class _StructuredEnumMemberValue:
    """Common identity for enum values preserved independently of source text."""

    __slots__ = ()


@record
class EnumMemberValue(_StructuredEnumMemberValue):
    """A raw string enum value that renders as its Python source literal."""

    value: str

    @override
    def __str__(self) -> str:
        """Render the value using the historical single-quoted source form."""
        return f"'{self.value.translate(escape_characters)}'"

    @override
    def __repr__(self) -> str:
        """Render the value as Python source when used by generic literal renderers."""
        return str(self)


class _NullEnumMemberValue(_StructuredEnumMemberValue):
    """A unique structured marker for an explicit JSON null enum member."""

    __slots__ = ()
    value: None = None

    @override
    def __str__(self) -> str:
        """Render JSON null as its Python source equivalent."""
        return "None"

    @override
    def __repr__(self) -> str:
        """Render the marker as Python source in generic literal renderers."""
        return "None"


NULL_ENUM_MEMBER_VALUE = _NullEnumMemberValue()


@lru_cache(maxsize=4096)
def _get_legacy_raw_enum_member_value(default: str) -> Any:
    """Decode a rendered default supplied by a third-party parser subclass."""
    from ast import literal_eval  # noqa: PLC0415

    try:
        return literal_eval(default)
    except (SyntaxError, ValueError):
        return default


def get_raw_enum_member_value(default: Any) -> Any:
    """Return one semantic value from structured or legacy rendered defaults."""
    match default:
        case EnumMemberValue() | _NullEnumMemberValue():
            return default.value
        case str():
            return _get_legacy_raw_enum_member_value(default)
    return default


def _json_value_key(value: Any) -> tuple[object, Any] | None:
    """Build a hash key with the same bool/number/type distinctions as JSON comparison."""
    if isinstance(value, bool):
        return bool, value
    if isinstance(value, (int, float)):
        return _JSON_NUMBER_KEY, _JSON_NAN_KEY if isinstance(value, float) and isnan(value) else value
    try:
        hash(value)
    except TypeError:
        return None
    return type(value), value


SUBCLASS_BASE_CLASSES: dict[Types, str] = {
    Types.int32: _INT,
    Types.int64: _INT,
    Types.integer: _INT,
    Types.float: _FLOAT,
    Types.double: _FLOAT,
    Types.number: _FLOAT,
    Types.byte: _BYTES,
    Types.string: _STR,
}


class Enum(DataModel):
    """DataModel implementation for Python enumerations."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "Enum.jinja2"
    BASE_CLASS: ClassVar[str] = "enum.Enum"
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_ENUM,)
    SUPPORTS_GENERIC_BASE_CLASS: ClassVar[bool] = False
    _FREEZES_FIELD_RENDER_PLANS: ClassVar[bool] = True
    """A member is a field of the output's backend: the template writes ``name = value``, and the member's imports
    (row ``legacy.enum-member-import-bindings``) come from the plan its backend froze for it."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        reference: Reference,
        fields: list[DataModelFieldBase],
        decorators: list[str] | None = None,
        base_classes: list[Reference] | None = None,
        custom_base_class: str | list[str] | None = None,
        custom_template_dir: Path | None = None,
        extra_template_data: defaultdict[str, dict[str, Any]] | None = None,
        methods: list[str] | None = None,
        path: Path | None = None,
        description: str | None = None,
        type_: Types | None = None,
        default: Any = UNDEFINED,
        nullable: bool = False,
        keyword_only: bool = False,
        treat_dot_as_module: bool | None = None,
    ) -> None:
        """Initialize Enum with optional specialized base class based on type."""
        super().__init__(
            reference=reference,
            fields=fields,
            decorators=decorators,
            base_classes=base_classes,
            custom_base_class=custom_base_class,
            custom_template_dir=custom_template_dir,
            extra_template_data=extra_template_data,
            methods=methods,
            path=path,
            description=description,
            default=default,
            nullable=nullable,
            keyword_only=keyword_only,
            treat_dot_as_module=treat_dot_as_module,
        )
        if not base_classes and type_ and (base_class := SUBCLASS_BASE_CLASSES.get(type_)):
            self.base_classes: list[BaseClassDataType] = [
                BaseClassDataType(type=base_class),
                *self.base_classes,
            ]

    @classmethod
    def get_data_type(cls, types: Types, **kwargs: Any) -> DataType:
        """Get data type for enum (not implemented)."""
        raise NotImplementedError

    @property
    @override
    def holds_null(self) -> bool:
        """Whether a member of the enum is None."""
        return any(get_raw_enum_member_value(field.default) is None for field in self.fields)

    def get_member(self, field: DataModelFieldBase) -> Member:
        """Create a Member instance for the given field."""
        return Member(self, field)

    @property
    def _member_index(self) -> dict[tuple[object, Any], DataModelFieldBase]:
        """Build the exact-value index only when member lookup is used; the parse's memo keeps it."""
        return memoized(MemoTable.ENUM_MEMBER_INDEX, model_key_of(self.reference), self, None, self._index_members)

    def _index_members(self) -> dict[tuple[object, Any], DataModelFieldBase]:
        exact: dict[tuple[object, Any], DataModelFieldBase] = {}
        for field in self.fields:
            if field.default is None:
                continue
            member_value = get_raw_enum_member_value(field.default)
            if (key := _json_value_key(member_value)) is not None:
                exact.setdefault(key, field)
        return exact

    @property
    def _coerced_member_index(self) -> dict[str, DataModelFieldBase]:
        """Build the string-coercing index only for discriminator-style lookup; the parse's memo keeps it."""
        return memoized(
            MemoTable.COERCED_ENUM_MEMBER_INDEX, model_key_of(self.reference), self, None, self._index_coerced_members
        )

    def _index_coerced_members(self) -> dict[str, DataModelFieldBase]:
        coerced: dict[str, DataModelFieldBase] = {}
        for field in self.fields:
            if field.default is None:
                continue
            member_value = get_raw_enum_member_value(field.default)
            coerced.setdefault(
                member_value if isinstance(member_value, str) else _semantic_value_text(member_value), field
            )
        return coerced

    def find_member(self, value: Any, *, coerce_strings: bool = False) -> Member | None:
        """Find the enum member whose value equals the given schema value.

        coerce_strings lets a string value match a non-string member with the same
        string representation, as required for OpenAPI discriminator mapping keys.
        """
        if coerce_strings and isinstance(value, str):
            return self.get_member(field) if (field := self._coerced_member_index.get(value)) is not None else None
        if (key := _json_value_key(value)) is not None:
            return self.get_member(field) if (field := self._member_index.get(key)) is not None else None

        for field in self.fields:
            if field.default is None:
                continue
            member_value = get_raw_enum_member_value(field.default)
            if type(member_value) is type(value) and member_value == value:
                return self.get_member(field)
        return None

    @override
    def invalidate_render_caches(self) -> None:
        """Clear rendering state and the member index after field mutations."""
        super().invalidate_render_caches()
        forget_model_memos(self, MemoTable.ENUM_MEMBER_INDEX, MemoTable.COERCED_ENUM_MEMBER_INDEX)

    @property
    @override
    def imports(self) -> tuple[Import, ...]:
        """Get imports excluding Any."""
        return tuple(i for i in super().imports if i != IMPORT_ANY)

    @override
    def _unrendered_imports(self) -> tuple[Import, ...]:
        """Return the imports of the member declarations, which the built-in template renders as ``name = value``.

        ``imports`` keeps them, so the module binds their names (``legacy.enum-member-import-bindings`` in
        ``parser/_imports_plan.py``). A custom ``Enum.jinja2`` may render what needs them, so it renders them all.
        """
        if self._uses_custom_root_template:
            return ()
        return tuple(i for f in self.fields for i in field_imports(f) if i != IMPORT_ANY)


class StrEnum(Enum):
    """String enumeration type."""

    BASE_CLASS: ClassVar[str] = "enum.StrEnum"
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_STR_ENUM,)


class IntEnum(Enum):
    """Integer enumeration type."""

    BASE_CLASS: ClassVar[str] = "enum.IntEnum"
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_INT_ENUM,)


SPECIALIZED_ENUM_TYPE_MATCH: dict[Types, type[Enum]] = {
    Types.int32: IntEnum,
    Types.int64: IntEnum,
    Types.integer: IntEnum,
    Types.string: StrEnum,
}
"""
Map specialized enum types to their corresponding Enum subclasses.

Kept for import compatibility; nothing reads it; ``model/_decisions/enum_kind.py:decide_enum_kind`` decides the class.
"""


def _enum_class(kind: EnumKind, first_merged: type[Enum] = Enum) -> type[Enum]:
    """Return the class an enum of the kind is written as; the merged rows keep the class of the first merged enum."""
    match kind.base:
        case "Enum":
            return Enum
        case "IntEnum":
            return IntEnum
        case "StrEnum":
            return StrEnum
        case None:
            return first_merged
        case _:  # pragma: no cover
            assert_never(kind.base)


class Member:
    """Represents an enum member with its parent enum and field."""

    def __init__(self, enum: Enum, field: DataModelFieldBase) -> None:
        """Initialize enum member."""
        self.enum: Enum = enum
        self.field: DataModelFieldBase = field
        self.alias: Optional[str] = None  # noqa: UP045

    @override
    def __repr__(self) -> str:
        """Return string representation of enum member."""
        return f"{self.alias or self.enum.class_name}.{self.field.name}"

    @property
    def value(self) -> Any:
        """Return the raw semantic value represented by this enum member."""
        return get_raw_enum_member_value(self.field.default)
