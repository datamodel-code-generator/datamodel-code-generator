"""Pydantic v2 type manager.

Maps schema types to Pydantic v2 specific types with AwareDatetime, NaiveDatetime, etc.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar

from datamodel_code_generator._format_types import DateClassType, DatetimeClassType, PythonVersion, PythonVersionMin
from datamodel_code_generator.imports import (
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
from datamodel_code_generator.model.pydantic_v2._output_context import (
    ANNOTATED_CONSTRAINTS_CONTEXT as _ANNOTATED_CONSTRAINTS_CONTEXT,
)
from datamodel_code_generator.model.pydantic_v2.imports import (
    IMPORT_ANYURL,
    IMPORT_AWARE_DATETIME,
    IMPORT_BASE64BYTES,
    IMPORT_BASE64STR,
    IMPORT_CONBYTES,
    IMPORT_CONDECIMAL,
    IMPORT_CONFLOAT,
    IMPORT_CONINT,
    IMPORT_CONSTR,
    IMPORT_EMAIL_STR,
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
    IMPORT_UUID1,
    IMPORT_UUID3,
    IMPORT_UUID4,
    IMPORT_UUID5,
)
from datamodel_code_generator.python_literal import PythonCode
from datamodel_code_generator.types import (
    CONSTRAINED_DECIMAL_DEFAULT_VALUE_DESCRIPTOR,
    DECIMAL_DEFAULT_VALUE_DESCRIPTOR,
    DataType,
    StrictTypes,
    Types,
    UnionIntFloat,
    normalize_integer_constraints,
)
from datamodel_code_generator.types import DataTypeManager as _DataTypeManagerBase

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator, Sequence

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


number_kwargs: tuple[str, ...] = (
    "minimum",
    "maximum",
    "multipleOf",
    "exclusiveMaximum",
    "exclusiveMinimum",
)

string_kwargs: tuple[str, ...] = ("pattern", "minLength", "maxLength", "minItems", "maxItems")

bytes_kwargs: tuple[str, ...] = ("minLength", "maxLength")

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
    CONSTRAINED_TYPE_CONSUMED_KEYS: ClassVar[dict[str, tuple[str, ...]]] = {
        "PositiveInt": ("exclusiveMinimum",),
        "NegativeInt": ("exclusiveMaximum",),
        "NonNegativeInt": ("minimum",),
        "NonPositiveInt": ("maximum",),
        "PositiveFloat": ("exclusiveMinimum",),
        "NegativeFloat": ("exclusiveMaximum",),
        "NonNegativeFloat": ("minimum",),
        "NonPositiveFloat": ("maximum",),
    }
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

    def transform_kwargs(self, kwargs: dict[str, Any], filter_: Collection[str]) -> dict[str, str]:
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
                return self.data_type.from_import(
                    IMPORT_CONDECIMAL,
                    kwargs={
                        k: Decimal(str(v.value if isinstance(v, UnionIntFloat) else v))
                        for k, v in data_type_kwargs.items()
                    },
                )
            if not strict:
                if data_type_kwargs == {"gt": 0}:
                    return self.data_type.from_import(IMPORT_POSITIVE_FLOAT)
                if data_type_kwargs == {"lt": 0}:
                    return self.data_type.from_import(IMPORT_NEGATIVE_FLOAT)
                if data_type_kwargs == {"ge": 0} and self.use_non_positive_negative_number_constrained_types:
                    return self.data_type.from_import(IMPORT_NON_NEGATIVE_FLOAT)
                if data_type_kwargs == {"le": 0} and self.use_non_positive_negative_number_constrained_types:
                    return self.data_type.from_import(IMPORT_NON_POSITIVE_FLOAT)
            kwargs = {k: float(v) for k, v in data_type_kwargs.items()}
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

    def get_data_str_type(self, types: Types, **kwargs: Any) -> DataType:
        """Get string data type with constraints (constr)."""
        data_type_kwargs: dict[str, Any] = self.transform_kwargs(kwargs, string_kwargs)
        strict = StrictTypes.str in self.strict_types
        if data_type_kwargs:
            if strict:
                data_type_kwargs["strict"] = True
            if self.PATTERN_KEY in data_type_kwargs:
                data_type_kwargs[self.PATTERN_KEY] = _get_regex_literal(data_type_kwargs[self.PATTERN_KEY])
            return self.data_type.from_import(IMPORT_CONSTR, kwargs=data_type_kwargs)
        if strict:
            return self.copy_data_type(self.strict_type_map[StrictTypes.str])
        return self.copy_data_type(self.type_map[types])

    def get_data_bytes_type(self, types: Types, **kwargs: Any) -> DataType:
        """Get bytes data type with constraints (conbytes)."""
        base64_encoded = kwargs.pop("base64_encoded", False)
        data_type_kwargs: dict[str, Any] = self.transform_kwargs(kwargs, bytes_kwargs)
        strict = StrictTypes.bytes in self.strict_types
        if base64_encoded and not data_type_kwargs and not strict:
            return self.data_type.from_import(IMPORT_BASE64BYTES)
        if data_type_kwargs and not strict:
            return self.data_type.from_import(IMPORT_CONBYTES, kwargs=data_type_kwargs)
        # conbytes doesn't accept strict argument
        # https://github.com/samuelcolvin/pydantic/issues/2489
        if strict:
            return self.copy_data_type(self.strict_type_map[StrictTypes.bytes])
        return self.copy_data_type(self.type_map[types])

    def get_data_type(  # noqa: PLR0911
        self,
        types: Types,
        *,
        field_constraints: bool = False,
        **kwargs: Any,
    ) -> DataType:
        """Get data type with appropriate constraints for the given type."""
        if types == Types.string:
            return self.get_data_str_type(types, **kwargs)
        if types in {Types.int32, Types.int64, Types.integer}:
            return self.get_data_int_type(types, **kwargs)
        if types in {Types.float, Types.double, Types.number, Types.time}:
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

    SUPPORTS_SERIALIZE_AS_ANY: ClassVar[bool] = True
    _CONSTRAINED_TYPE_TO_BASE: ClassVar[dict[str, str]] = {"constr": "str"}
    _BASE_TYPE_HINT_CONTAINER_ORDER: ClassVar[tuple[str, ...]] = ("list", "set", "dict")

    @staticmethod
    def _wrap_discriminator_type_hint(type_: str, discriminator: str) -> str:
        return f"Annotated[{type_}, Field(discriminator={discriminator!r})]"

    def _should_wrap_with_serialize_as_any(self) -> bool:
        if not self.use_serialize_as_any:
            return False

        assert self.reference is not None

        from datamodel_code_generator.model.base import DataModel  # noqa: PLC0415

        return any(isinstance(child, DataModel) and child.fields for child in self.reference.children)

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
    def imports(self) -> Iterator[Import]:
        """Yield imports including SerializeAsAny when needed."""
        yield from super().imports

        if self._needs_serialize_as_any_import():
            yield IMPORT_SERIALIZE_AS_ANY


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

    def type_map_factory(  # noqa: PLR0913, PLR0917
        self,
        data_type: type[DataType],
        strict_types: Sequence[StrictTypes],
        pattern_key: str,
        target_datetime_class: DatetimeClassType | None = None,
        use_object_type: bool = False,  # noqa: FBT001, FBT002
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
