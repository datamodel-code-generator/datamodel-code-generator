"""Pydantic v2 type manager.

Maps schema types to Pydantic v2 specific types with AwareDatetime, NaiveDatetime, etc.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar

from typing_extensions import override

from datamodel_code_generator import _constraint_applicability as _applicability
from datamodel_code_generator._format_types import DateClassType, DatetimeClassType, PythonVersion, PythonVersionMin
from datamodel_code_generator._requirements import pydantic_target_meets
from datamodel_code_generator.imports import (
    IMPORT_ANNOTATED,
    IMPORT_ANY,
    IMPORT_DATE,
    IMPORT_DATETIME,
    IMPORT_DECIMAL,
    IMPORT_PATH,
    IMPORT_PENDULUM_DATE,
    IMPORT_PENDULUM_DATETIME,
    IMPORT_PENDULUM_DURATION,
    IMPORT_PENDULUM_TIME,
    IMPORT_TIME,
    IMPORT_TIMEDELTA,
    IMPORT_ULID,
    IMPORT_UUID,
)
from datamodel_code_generator.model.pydantic_v2 import imports as pydantic_imports
from datamodel_code_generator.model.pydantic_v2._output_context import (
    ANNOTATED_CONSTRAINTS_CONTEXT as _ANNOTATED_CONSTRAINTS_CONTEXT,
)
from datamodel_code_generator.model.pydantic_v2.imports import (
    IMPORT_ANYURL,
    IMPORT_AWARE_DATETIME,
    IMPORT_BASE64_ENCODER,
    IMPORT_BASE64BYTES,
    IMPORT_BASE64STR,
    IMPORT_CONBYTES,
    IMPORT_CONDECIMAL,
    IMPORT_CONFLOAT,
    IMPORT_CONINT,
    IMPORT_CONSTR,
    IMPORT_EMAIL_STR,
    IMPORT_ENCODED_BYTES,
    IMPORT_FIELD,
    IMPORT_FUTURE_DATE,
    IMPORT_FUTURE_DATETIME,
    IMPORT_IPV4ADDRESS,
    IMPORT_IPV4NETWORKS,
    IMPORT_IPV6ADDRESS,
    IMPORT_IPV6NETWORKS,
    IMPORT_NAIVE_DATETIME,
    IMPORT_NEGATIVE_FLOAT,
    IMPORT_NEGATIVE_INT,
    IMPORT_NON_NEGATIVE_FLOAT,
    IMPORT_NON_NEGATIVE_INT,
    IMPORT_NON_POSITIVE_FLOAT,
    IMPORT_NON_POSITIVE_INT,
    IMPORT_PAST_DATE,
    IMPORT_PAST_DATETIME,
    IMPORT_POSITIVE_FLOAT,
    IMPORT_POSITIVE_INT,
    IMPORT_SECRET_STR,
    IMPORT_SERIALIZE_AS_ANY,
    IMPORT_STRICT_BOOL,
    IMPORT_STRICT_BYTES,
    IMPORT_STRICT_FLOAT,
    IMPORT_STRICT_INT,
    IMPORT_STRICT_STR,
    IMPORT_STRING_CONSTRAINTS,
    IMPORT_UUID1,
    IMPORT_UUID3,
    IMPORT_UUID4,
    IMPORT_UUID5,
)
from datamodel_code_generator.python_literal import PythonCode, represent_python_value
from datamodel_code_generator.types import (
    CONSTRAINED_DECIMAL_DEFAULT_VALUE_DESCRIPTOR,
    DECIMAL_DEFAULT_VALUE_DESCRIPTOR,
    DataType,
    StrictTypes,
    Types,
    UnionIntFloat,
    _float_constraint,
    _non_finite_as_written,
    normalize_integer_constraints,
)
from datamodel_code_generator.types import DataTypeManager as _DataTypeManagerBase

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator, Mapping, Sequence
    from types import ModuleType

    from datamodel_code_generator._requirements import PydanticConstruct
    from datamodel_code_generator.enums import TargetPydanticVersion
    from datamodel_code_generator.imports import Import


# --- Shared Pydantic type helpers ---


def type_map_factory(
    data_type: type[DataType],
    strict_types: Sequence[StrictTypes],
    pattern_key: str,
    use_pendulum: bool,  # noqa: FBT001
    use_object_type: bool = False,  # noqa: FBT001, FBT002
) -> dict[Types, DataType]:
    """Create a mapping of schema types to Pydantic data types."""
    data_type_int = data_type(type="int")
    data_type_float = data_type(type="float")
    data_type_str = data_type(type="str")
    object_data_type = data_type(type="object") if use_object_type else data_type.from_import(IMPORT_ANY)
    result = {
        Types.integer: data_type_int,
        Types.int32: data_type_int,
        Types.int64: data_type_int,
        Types.number: data_type_float,
        Types.float: data_type_float,
        Types.double: data_type_float,
        Types.decimal: data_type.from_import(IMPORT_DECIMAL),
        Types.time: data_type.from_import(IMPORT_TIME),
        Types.string: data_type_str,
        Types.byte: data_type_str,  # base64 encoded string
        Types.binary: data_type(type="bytes"),
        Types.date: data_type.from_import(IMPORT_DATE),
        Types.date_time: data_type.from_import(IMPORT_DATETIME),
        Types.date_time_local: data_type.from_import(IMPORT_DATETIME),
        Types.time_local: data_type.from_import(IMPORT_TIME),
        Types.timedelta: data_type.from_import(IMPORT_TIMEDELTA),
        Types.path: data_type.from_import(IMPORT_PATH),
        Types.password: data_type.from_import(IMPORT_SECRET_STR),
        Types.email: data_type.from_import(IMPORT_EMAIL_STR),
        Types.uuid: data_type.from_import(IMPORT_UUID),
        Types.uuid1: data_type.from_import(IMPORT_UUID1),
        Types.uuid2: data_type.from_import(IMPORT_UUID),
        Types.uuid3: data_type.from_import(IMPORT_UUID3),
        Types.uuid4: data_type.from_import(IMPORT_UUID4),
        Types.uuid5: data_type.from_import(IMPORT_UUID5),
        Types.ulid: data_type.from_import(IMPORT_ULID),
        Types.uri: data_type.from_import(IMPORT_ANYURL),
        Types.hostname: data_type.from_import(
            IMPORT_CONSTR,
            strict=StrictTypes.str in strict_types,
            kwargs={
                pattern_key: _get_regex_literal(HOSTNAME_REGEX),
                **({"strict": True} if StrictTypes.str in strict_types else {}),
            },
        ),
        Types.ipv4: data_type.from_import(IMPORT_IPV4ADDRESS),
        Types.ipv6: data_type.from_import(IMPORT_IPV6ADDRESS),
        Types.ipv4_network: data_type.from_import(IMPORT_IPV4NETWORKS),
        Types.ipv6_network: data_type.from_import(IMPORT_IPV6NETWORKS),
        Types.boolean: data_type(type="bool"),
        Types.object: data_type(data_types=[object_data_type], is_dict=True),
        Types.null: data_type(type="None"),
        Types.array: data_type(data_types=[object_data_type], is_list=True),
        Types.any: data_type.from_import(IMPORT_ANY),
    }
    if use_pendulum:
        result[Types.date] = data_type.from_import(IMPORT_PENDULUM_DATE)
        result[Types.date_time] = data_type.from_import(IMPORT_PENDULUM_DATETIME)
        result[Types.time] = data_type.from_import(IMPORT_PENDULUM_TIME)
        result[Types.timedelta] = data_type.from_import(IMPORT_PENDULUM_DURATION)

    return result


def strict_type_map_factory(data_type: type[DataType]) -> dict[StrictTypes, DataType]:
    """Create a mapping of strict types to Pydantic strict data types."""
    return {
        StrictTypes.int: data_type.from_import(IMPORT_STRICT_INT, strict=True),
        StrictTypes.float: data_type.from_import(IMPORT_STRICT_FLOAT, strict=True),
        StrictTypes.bytes: data_type.from_import(IMPORT_STRICT_BYTES, strict=True),
        StrictTypes.bool: data_type.from_import(IMPORT_STRICT_BOOL, strict=True),
        StrictTypes.str: data_type.from_import(IMPORT_STRICT_STR, strict=True),
    }


number_kwargs: tuple[str, ...] = _applicability.NUMBER_KEYWORD_ORDER

string_kwargs: tuple[str, ...] = _applicability.STRING_LENGTH_KEYWORD_ORDER
_ANNOTATED_STRING_IMPORTS = (IMPORT_ANNOTATED,)
_ENCODED_BYTES_IMPORTS = (IMPORT_ANNOTATED, IMPORT_FIELD, IMPORT_ENCODED_BYTES, IMPORT_BASE64_ENCODER)

bytes_kwargs: tuple[str, ...] = _applicability.LENGTH_KEYWORD_ORDER
_FLOAT_TYPES: frozenset[Types] = frozenset({Types.float, Types.double, Types.number, Types.time})
_INT_TYPES: frozenset[Types] = frozenset({Types.int32, Types.int64, Types.integer})
_ZERO_BOUND_KEYWORDS: tuple[str, ...] = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")
"""The bounds of which exactly one equal to 0 gives a zero-bound class in the field forms, in schema order."""

escape_characters = str.maketrans({
    "'": r"\'",
    "\b": r"\b",
    "\f": r"\f",
    "\n": r"\n",
    "\r": r"\r",
    "\t": r"\t",
})

HOSTNAME_REGEX = _DataTypeManagerBase.HOSTNAME_REGEX


def _get_regex_literal(pattern: str) -> str:
    escaped_regex = pattern.translate(escape_characters)
    raw_literal = f"r'{escaped_regex}'"
    try:
        ast.literal_eval(raw_literal)
    except SyntaxError:
        return pattern
    return PythonCode(raw_literal, pattern)


class _PydanticDataTypeManager(_DataTypeManagerBase):
    """Base data type manager for Pydantic models with constrained types."""

    PATTERN_KEY: ClassVar[str] = "pattern"
    HOSTNAME_REGEX: ClassVar[str] = HOSTNAME_REGEX
    CONSTRAINED_TYPE_CONSUMED_KEYS: ClassVar[dict[str, tuple[str, ...]]] = dict(
        _applicability.PYDANTIC_OUTPUT.zero_bound_classes
    )
    CONSTRAINT_KEYWORDS: ClassVar[Mapping[str, frozenset[str]]] = _applicability.PYDANTIC_OUTPUT.type_keywords
    KNOWN_TYPE_IMPORTS: ClassVar[tuple[ModuleType, ...]] = (*_DataTypeManagerBase.KNOWN_TYPE_IMPORTS, pydantic_imports)
    NUMBER_VALUE_TYPES: ClassVar[frozenset[str]] = _applicability.PYDANTIC_OUTPUT.number_value_types
    TARGET_NUMBER_KEYWORDS: ClassVar[Mapping[str, tuple[PydanticConstruct, frozenset[str]]]] = (
        _applicability.PYDANTIC_OUTPUT.target_number_keywords
    )
    _WRITES_CONSTRAINED_TYPES: ClassVar[bool] = _applicability.PYDANTIC_OUTPUT.writes_constrained_types
    DEFAULT_VALUE_DESCRIPTORS = MappingProxyType({
        (IMPORT_DECIMAL.from_, IMPORT_DECIMAL.import_): DECIMAL_DEFAULT_VALUE_DESCRIPTOR,
        (IMPORT_CONDECIMAL.from_, IMPORT_CONDECIMAL.import_): CONSTRAINED_DECIMAL_DEFAULT_VALUE_DESCRIPTOR,
    })

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
        target_date_class: DateClassType | None = None,  # noqa: ARG002
        treat_dot_as_module: bool | None = None,  # noqa: FBT001
        use_serialize_as_any: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        """Initialize the DataTypeManager with Pydantic type mappings."""
        super().__init__(
            python_version=python_version,
            use_standard_collections=use_standard_collections,
            use_generic_container_types=use_generic_container_types,
            strict_types=strict_types,
            use_non_positive_negative_number_constrained_types=use_non_positive_negative_number_constrained_types,
            use_decimal_for_multiple_of=use_decimal_for_multiple_of,
            use_union_operator=use_union_operator,
            use_pendulum=use_pendulum,
            use_object_type=use_object_type,
            target_datetime_class=target_datetime_class,
            treat_dot_as_module=treat_dot_as_module,
            use_serialize_as_any=use_serialize_as_any,
        )

        self.type_map: dict[Types, DataType] = self.type_map_factory(
            self.data_type,
            strict_types=self.strict_types,
            pattern_key=self.PATTERN_KEY,
            target_datetime_class=self.target_datetime_class,
        )
        self.strict_type_map: dict[StrictTypes, DataType] = strict_type_map_factory(
            self.data_type,
        )

        self.kwargs_schema_to_model: dict[str, str] = {
            "exclusiveMinimum": "gt",
            "minimum": "ge",
            "exclusiveMaximum": "lt",
            "maximum": "le",
            "multipleOf": "multiple_of",
            "minItems": "min_length",
            "maxItems": "max_length",
            "minLength": "min_length",
            "maxLength": "max_length",
            "pattern": self.PATTERN_KEY,
        }

    def type_map_factory(
        self,
        data_type: type[DataType],
        strict_types: Sequence[StrictTypes],
        pattern_key: str,
        target_datetime_class: DatetimeClassType | None,  # noqa: ARG002
        use_object_type: bool = False,  # noqa: FBT001, FBT002
    ) -> dict[Types, DataType]:
        """Create type mapping with Pydantic specific types."""
        return type_map_factory(
            data_type,
            strict_types,
            pattern_key,
            self.use_pendulum,
            use_object_type,
        )

    def transform_kwargs(self, kwargs: dict[str, Any], filter_: Collection[str]) -> dict[str, Any]:
        """Transform schema kwargs to Pydantic field kwargs.

        Iterates whichever side is smaller. Output order is identical either way
        because the filters are tuples in JsonSchemaObject field-definition order,
        which is also the iteration order of kwargs built from model_dump().
        """
        if len(kwargs) <= len(filter_):
            return {
                self.kwargs_schema_to_model.get(k, k): v for (k, v) in kwargs.items() if v is not None and k in filter_
            }
        return {self.kwargs_schema_to_model.get(k, k): v for k in filter_ if (v := kwargs.get(k)) is not None}

    def get_data_int_type(  # noqa: PLR0911
        self,
        types: Types,
        **kwargs: Any,
    ) -> DataType:
        """Get int data type with constraints (conint, PositiveInt, etc.)."""
        data_type_kwargs: dict[str, Any] = self.transform_kwargs(kwargs, number_kwargs)
        strict = StrictTypes.int in self.strict_types
        if data_type_kwargs:
            if not strict:
                if data_type_kwargs == {"gt": 0}:
                    return self.data_type.from_import(IMPORT_POSITIVE_INT)
                if data_type_kwargs == {"lt": 0}:
                    return self.data_type.from_import(IMPORT_NEGATIVE_INT)
                if data_type_kwargs == {"ge": 0} and self.use_non_positive_negative_number_constrained_types:
                    return self.data_type.from_import(IMPORT_NON_NEGATIVE_INT)
                if data_type_kwargs == {"le": 0} and self.use_non_positive_negative_number_constrained_types:
                    return self.data_type.from_import(IMPORT_NON_POSITIVE_INT)
            if not (kwargs := normalize_integer_constraints(data_type_kwargs)):
                return (
                    self.copy_data_type(self.strict_type_map[StrictTypes.int])
                    if strict
                    else self.copy_data_type(self.type_map[types])
                )
            if strict:
                kwargs["strict"] = True
            return self.data_type.from_import(IMPORT_CONINT, kwargs=kwargs)
        if strict:
            return self.copy_data_type(self.strict_type_map[StrictTypes.int])
        return self.copy_data_type(self.type_map[types])

    def get_data_float_type(  # noqa: PLR0911
        self,
        types: Types,
        **kwargs: Any,
    ) -> DataType:
        """Get float data type with constraints (confloat, PositiveFloat, etc.)."""
        data_type_kwargs = self.transform_kwargs(kwargs, number_kwargs)
        strict = StrictTypes.float in self.strict_types
        if data_type_kwargs:
            # Use Decimal instead of float when multipleOf is present to avoid floating-point precision issues
            if self.use_decimal_for_multiple_of and "multiple_of" in data_type_kwargs:
                decimal_kwargs: dict[str, Any] = {
                    k: Decimal(str(v.value if isinstance(v, UnionIntFloat) else v)) for k, v in data_type_kwargs.items()
                }
                if strict and self.use_decimal_in_field_forms:
                    decimal_kwargs["strict"] = True
                return self.data_type.from_import(IMPORT_CONDECIMAL, kwargs=decimal_kwargs)
            if not strict:
                if data_type_kwargs == {"gt": 0}:
                    return self.data_type.from_import(IMPORT_POSITIVE_FLOAT)
                if data_type_kwargs == {"lt": 0}:
                    return self.data_type.from_import(IMPORT_NEGATIVE_FLOAT)
                if data_type_kwargs == {"ge": 0} and self.use_non_positive_negative_number_constrained_types:
                    return self.data_type.from_import(IMPORT_NON_NEGATIVE_FLOAT)
                if data_type_kwargs == {"le": 0} and self.use_non_positive_negative_number_constrained_types:
                    return self.data_type.from_import(IMPORT_NON_POSITIVE_FLOAT)
            kwargs = {k: _float_constraint(v) for k, v in data_type_kwargs.items()}
            if strict:
                kwargs["strict"] = True
            return self.data_type.from_import(IMPORT_CONFLOAT, kwargs=kwargs)
        if strict:
            return self.copy_data_type(self.strict_type_map[StrictTypes.float])
        return self.copy_data_type(self.type_map[types])

    def get_data_decimal_type(self, types: Types, **kwargs: Any) -> DataType:
        """Get decimal data type with constraints (condecimal)."""
        data_type_kwargs = self.transform_kwargs(kwargs, number_kwargs)
        if data_type_kwargs:
            return self.data_type.from_import(
                IMPORT_CONDECIMAL,
                kwargs={
                    k: Decimal(str(v.value if isinstance(v, UnionIntFloat) else v)) for k, v in data_type_kwargs.items()
                },
            )
        return self.copy_data_type(self.type_map[types])

    def get_data_str_type(
        self,
        types: Types,
        *,
        use_annotated: bool = False,
        target_pydantic_version: TargetPydanticVersion | None = None,
        **kwargs: Any,
    ) -> DataType:
        """Get a string type with constraints in the requested annotation style."""
        strict = StrictTypes.str in self.strict_types
        if data_type_kwargs := self.transform_kwargs(kwargs, string_kwargs):
            if strict:
                data_type_kwargs["strict"] = True
            if self.PATTERN_KEY in data_type_kwargs:
                data_type_kwargs[self.PATTERN_KEY] = _get_regex_literal(data_type_kwargs[self.PATTERN_KEY])
            if use_annotated:
                from datamodel_code_generator.model.pydantic_v2._annotated_types import (  # noqa: PLC0415
                    AnnotatedStringDataType,
                )

                constraint_import = (
                    IMPORT_STRING_CONSTRAINTS
                    if pydantic_target_meets("pydantic.string_constraints", target_pydantic_version)
                    else IMPORT_FIELD
                )
                data_type = AnnotatedStringDataType(
                    type=constraint_import.import_,
                    import_=constraint_import,
                    is_func=True,
                    kwargs=data_type_kwargs,
                    python_version=self.python_version,
                )
                data_type._set_runtime_expression_imports(_ANNOTATED_STRING_IMPORTS)  # noqa: SLF001
                return data_type
            return self.data_type.from_import(IMPORT_CONSTR, kwargs=data_type_kwargs)
        if strict:
            return self.copy_data_type(self.strict_type_map[StrictTypes.str])
        return self.copy_data_type(self.type_map[types])

    def get_data_bytes_type(self, types: Types, **kwargs: Any) -> DataType:
        """Get bytes data type with constraints (conbytes).

        Bytes written as base64 text decode it, like ``Base64Str`` without strictness: ``Base64Bytes``, or with lengths
        an ``Annotated`` type that checks them on the base64 text, as JSON Schema counts them, before decoding it.
        """
        strict = StrictTypes.bytes in self.strict_types
        data_type_kwargs: dict[str, Any] = self.transform_kwargs(kwargs, bytes_kwargs)
        if kwargs.get("base64_encoded"):
            if not data_type_kwargs:
                return self.data_type.from_import(IMPORT_BASE64BYTES, strict=strict)
            from datamodel_code_generator.types import _create_context_data_type  # noqa: PLC0415

            data_type = _create_context_data_type(
                "PydanticV2EncodedBytesDataType",
                EncodedBytesDataType,
                self.python_version,
                self.use_standard_collections,
                self.use_generic_container_types,
                self.use_union_operator,
                self.treat_dot_as_module,
                self.use_serialize_as_any,
            ).from_import(IMPORT_ENCODED_BYTES, kwargs=data_type_kwargs)
            data_type._set_runtime_expression_imports(_ENCODED_BYTES_IMPORTS)  # noqa: SLF001
            return data_type
        if data_type_kwargs:
            if strict:
                data_type_kwargs["strict"] = True
            return self.data_type.from_import(IMPORT_CONBYTES, kwargs=data_type_kwargs)
        if strict:
            return self.copy_data_type(self.strict_type_map[StrictTypes.bytes])
        return self.copy_data_type(self.type_map[types])

    def _is_decimal_multiple(self, types: Types, kwargs: Mapping[str, Any]) -> bool:
        """Return whether a number schema with ``multipleOf`` of a field form is a ``Decimal``.

        It is under ``use_decimal_for_multiple_of`` with ``use_decimal_in_field_forms``; without the latter the field
        forms write a float, as in 0.86.0.
        """
        return (
            self.use_decimal_for_multiple_of
            and self.use_decimal_in_field_forms
            and kwargs.get("multipleOf") is not None
            and self._is_float_number(types, kwargs)
        )

    @override
    def _non_finite_multiple_of(self, types: Types, kwargs: Mapping[str, Any]) -> float | None:
        """Return the infinity or NaN that a number type would write its ``multipleOf`` as, which Pydantic refuses.

        A float writes an int beyond the float range as an infinity too; a ``Decimal`` under
        ``use_decimal_for_multiple_of`` and an int write an int as it is.
        """
        if (multiple_of := kwargs.get("multipleOf")) is None or not (
            types in _INT_TYPES or types == Types.decimal or self._is_float_number(types, kwargs)
        ):
            return None
        return _non_finite_as_written(
            multiple_of, as_float=types in _FLOAT_TYPES and not self.use_decimal_for_multiple_of
        )

    @staticmethod
    def _is_float_number(types: Types, kwargs: Mapping[str, Any]) -> bool:
        """Return whether a number type writes its constraints as a float, not as a string of a format."""
        return types in _FLOAT_TYPES and not kwargs.get("string_format")

    def _is_strict_number(self, types: Types) -> bool:
        """Return whether ``--strict-types`` makes the number type a schema type maps to strict."""
        if types in _INT_TYPES:
            return StrictTypes.int in self.strict_types
        return types in _FLOAT_TYPES and StrictTypes.float in self.strict_types

    def _field_form_kwargs(self, types: Types, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Return the arguments a type of a field form is built from: the constraints it takes, and the others.

        Under ``omit_redundant_constraints`` a strict number takes no zero bound: its field writes the bound beside
        ``StrictInt`` or ``StrictFloat`` instead of a strict ``con*`` type that repeats it.
        """
        if kwargs.get("base64_encoded"):
            return kwargs
        taken = {key: value for key, value in kwargs.items() if key not in self.kwargs_schema_to_model}
        if (
            self.use_non_positive_negative_number_constrained_types
            and not (self.omit_redundant_constraints and self._is_strict_number(types))
            and len(zero_bounds := [key for key in _ZERO_BOUND_KEYWORDS if kwargs.get(key) == 0]) == 1
        ):
            taken[zero_bounds[0]] = kwargs[zero_bounds[0]]
        return taken

    @override
    def get_data_type(  # noqa: PLR0911
        self,
        types: Types,
        *,
        field_constraints: bool = False,
        **kwargs: Any,
    ) -> DataType:
        """Get data type with appropriate constraints for the given type.

        The parser passes every constraint the schema declares for the type. In the field forms
        (``field_constraints``) the field writes them, so the type takes none of them but the lengths of bytes decoded
        from base64 text and, under ``use_non_positive_negative_number_constrained_types``, the one bound equal to 0
        of a schema that has exactly one (row ``legacy-consumed-at-object-property`` writes it again, except under
        ``omit_redundant_constraints``, where a strict number takes none of them); a number with
        ``multipleOf`` is a ``Decimal`` under ``use_decimal_for_multiple_of`` with ``use_decimal_in_field_forms``,
        strict under ``--strict-types float``.
        A ``multipleOf`` Pydantic refuses (``_non_finite_multiple_of``) is left out as if absent.
        """
        if self._non_finite_multiple_of(types, kwargs) is not None:
            kwargs = {key: value for key, value in kwargs.items() if key != "multipleOf"}
        if field_constraints:
            if self._is_decimal_multiple(types, kwargs):
                return self.data_type.from_import(IMPORT_DECIMAL, strict=StrictTypes.float in self.strict_types)
            kwargs = self._field_form_kwargs(types, kwargs)
        if types == Types.string:
            return self.get_data_str_type(types, **kwargs)
        if types in _INT_TYPES:
            return self.get_data_int_type(types, **kwargs)
        if self._is_float_number(types, kwargs):
            return self.get_data_float_type(types, **kwargs)
        if types == Types.decimal:
            return self.get_data_decimal_type(types, **kwargs)
        if types == Types.binary:
            return self.get_data_bytes_type(types, **kwargs)
        if types == Types.boolean and StrictTypes.bool in self.strict_types:
            return self.copy_data_type(self.strict_type_map[StrictTypes.bool])
        if types == Types.hostname and field_constraints:
            strict = StrictTypes.str in self.strict_types
            if strict:
                return self.copy_data_type(self.strict_type_map[StrictTypes.str])
            return self.data_type(type="str")

        return self.copy_data_type(self.type_map[types])


# --- Pydantic v2 specific ---


class PydanticV2DataType(DataType):
    """Pydantic v2-specific DataType with SerializeAsAny support."""

    _CONSTRAINED_TYPE_TO_BASE: ClassVar[dict[str, str]] = {"constr": "str"}
    _BASE_TYPE_HINT_CONTAINER_ORDER: ClassVar[tuple[str, ...]] = ("list", "set", "dict")

    @staticmethod
    @override
    def _wrap_discriminator_type_hint(type_: str, discriminator: str) -> str:
        return f"Annotated[{type_}, Field(discriminator={discriminator!r})]"

    def _should_wrap_with_serialize_as_any(self) -> bool:
        if not self.use_serialize_as_any:
            return False

        assert self.reference is not None

        from datamodel_code_generator.model.base import DataModel  # noqa: PLC0415

        return any(isinstance(child, DataModel) and child.fields for child in self.reference.children)

    @override
    def _get_wrapped_reference_type_hint(self, type_: str) -> str:
        if self._should_wrap_with_serialize_as_any():
            return f"SerializeAsAny[{type_}]"

        return type_

    def _needs_serialize_as_any_import(self) -> bool:
        if not self.use_serialize_as_any:
            return False
        if self.alias or self.type or self.reference is None:
            return False
        return self._should_wrap_with_serialize_as_any()

    @property
    @override
    def imports(self) -> Iterator[Import]:
        """Yield imports including SerializeAsAny when needed."""
        yield from super().imports

        if self._needs_serialize_as_any_import():
            yield IMPORT_SERIALIZE_AS_ANY


class EncodedBytesDataType(PydanticV2DataType):
    """Bytes written as base64 text, with the lengths that Pydantic checks on that text before it decodes it.

    JSON Schema counts ``minLength`` and ``maxLength`` on the base64 text, while Pydantic checks a ``Field()`` length
    of ``Base64Bytes`` on the decoded bytes in some forms, so the lengths stay in front of the decoding
    ``EncodedBytes``. Pydantic checks them so from 2.10 on.
    """

    annotated_bytes: ClassVar[bool] = True

    @property
    @override
    def _consumed_constraints(self) -> frozenset[str]:
        """The lengths the type writes in front of its decoding, which it enforces itself."""
        return _applicability.base64_bytes_consumed_keywords(self.kwargs or {})

    def _binding(self, name: str) -> str:
        return next(import_.binding_name for import_ in self.runtime_expression_imports if import_.import_ == name)

    @property
    @override
    def type_hint(self) -> str:
        """Render the lengths and the base64 decoding inside an Annotated subscript."""
        keywords = ", ".join(f"{name}={represent_python_value(value)}" for name, value in (self.kwargs or {}).items())
        return (
            f"{self._binding('Annotated')}[bytes, {self._binding('Field')}({keywords}), "
            f"{self._binding('EncodedBytes')}(encoder={self._binding('Base64Encoder')})]"
        )

    @property
    @override
    def imports(self) -> Iterator[Import]:
        """Yield the annotation imports, which aliasing reaches."""
        yield from (import_ for import_ in super().imports if import_ != self.import_)
        yield from self.runtime_expression_imports


class DataTypeManager(_PydanticDataTypeManager):
    """Type manager for Pydantic v2 with pattern key support."""

    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = True
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = _ANNOTATED_CONSTRAINTS_CONTEXT

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
        """Initialize with pydantic v2-specific DataType."""
        self._target_date_class = target_date_class
        super().__init__(
            python_version=python_version,
            use_standard_collections=use_standard_collections,
            use_generic_container_types=use_generic_container_types,
            strict_types=strict_types,
            use_non_positive_negative_number_constrained_types=use_non_positive_negative_number_constrained_types,
            use_decimal_for_multiple_of=use_decimal_for_multiple_of,
            use_union_operator=use_union_operator,
            use_pendulum=use_pendulum,
            use_object_type=use_object_type,
            target_datetime_class=target_datetime_class,
            target_date_class=target_date_class,
            treat_dot_as_module=treat_dot_as_module,
            use_serialize_as_any=use_serialize_as_any,
        )

        # Override the data_type with our pydantic v2 version
        from datamodel_code_generator.types import _create_context_data_type  # noqa: PLC0415

        self.data_type: type[DataType] = _create_context_data_type(
            "PydanticV2ContextDataType",
            PydanticV2DataType,
            python_version,
            use_standard_collections,
            use_generic_container_types,
            use_union_operator,
            treat_dot_as_module,
            use_serialize_as_any,
        )

    @override
    def type_map_factory(
        self,
        data_type: type[DataType],
        strict_types: Sequence[StrictTypes],
        pattern_key: str,
        target_datetime_class: DatetimeClassType | None = None,
        use_object_type: bool = False,
        target_date_class: DateClassType | None = None,
    ) -> dict[Types, DataType]:
        """Create type mapping with Pydantic v2 specific types and datetime classes."""
        effective_date_class = target_date_class or getattr(self, "_target_date_class", None)
        result = {
            **super().type_map_factory(
                data_type,
                strict_types,
                pattern_key,
                target_datetime_class or DatetimeClassType.Datetime,
                use_object_type or self.use_object_type,
            ),
            Types.byte: self.data_type.from_import(
                IMPORT_BASE64STR,
                strict=StrictTypes.str in strict_types,
            ),
            Types.date_time_local: data_type.from_import(IMPORT_NAIVE_DATETIME),
        }
        if target_datetime_class == DatetimeClassType.Awaredatetime:
            result[Types.date_time] = data_type.from_import(IMPORT_AWARE_DATETIME)
        elif target_datetime_class == DatetimeClassType.Naivedatetime:
            result[Types.date_time] = data_type.from_import(IMPORT_NAIVE_DATETIME)
        elif target_datetime_class == DatetimeClassType.Pastdatetime:
            result[Types.date_time] = data_type.from_import(IMPORT_PAST_DATETIME)
        elif target_datetime_class == DatetimeClassType.Futuredatetime:
            result[Types.date_time] = data_type.from_import(IMPORT_FUTURE_DATETIME)
        if effective_date_class == DateClassType.Pastdate:
            result[Types.date] = data_type.from_import(IMPORT_PAST_DATE)
        elif effective_date_class == DateClassType.Futuredate:
            result[Types.date] = data_type.from_import(IMPORT_FUTURE_DATE)
        return result
