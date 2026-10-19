"""Scalar type model generator.

Generates type aliases for GraphQL scalar types.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, ClassVar

from typing_extensions import override

from datamodel_code_generator.imports import (
    IMPORT_TYPE_ALIAS,
    IMPORT_TYPE_ALIAS_TYPE,
    Import,
)
from datamodel_code_generator.model.base import UNDEFINED
from datamodel_code_generator.model.type_alias import TypeAliasBase
from datamodel_code_generator.python_literal import represent_untrusted_public_type_name

if TYPE_CHECKING:
    from pathlib import Path

    from datamodel_code_generator._requirements import PydanticConstruct
    from datamodel_code_generator.model import DataModelFieldBase
    from datamodel_code_generator.reference import Reference

_INT: str = "int"
_FLOAT: str = "float"
_BOOLEAN: str = "bool"
_STR: str = "str"

# default graphql scalar types
DEFAULT_GRAPHQL_SCALAR_TYPE = _STR

DEFAULT_GRAPHQL_SCALAR_TYPES: dict[str, str] = {
    "Boolean": _BOOLEAN,
    "String": _STR,
    "ID": _STR,
    "Int": _INT,
    "Float": _FLOAT,
}


class _DataTypeScalarBase(TypeAliasBase):
    """Base class for GraphQL scalar types with shared __init__ logic."""

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
        default: Any = UNDEFINED,
        nullable: bool = False,
        keyword_only: bool = False,
        treat_dot_as_module: bool | None = None,
    ) -> None:
        """Initialize GraphQL scalar type with Python type mapping."""
        extra_template_data = extra_template_data or defaultdict(dict)

        scalar_name = reference.name
        if scalar_name not in extra_template_data:
            extra_template_data[scalar_name] = defaultdict(dict)

        # py_type
        py_type = extra_template_data[reference.original_name].get(
            "py_type",
            DEFAULT_GRAPHQL_SCALAR_TYPES.get(reference.original_name, DEFAULT_GRAPHQL_SCALAR_TYPE),
        )
        extra_template_data[scalar_name]["py_type"] = py_type

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

    @override
    def _builtin_template_data(self) -> dict[str, Any]:
        """Serialize public scalar aliases before built-in templates emit them."""
        template_data = super()._builtin_template_data()
        return {
            **template_data,
            "py_type": represent_untrusted_public_type_name(template_data.get("py_type", DEFAULT_GRAPHQL_SCALAR_TYPE)),
        }


class DataTypeScalar(_DataTypeScalarBase):
    """GraphQL scalar using TypeAlias annotation for Python 3.10+ (Name: TypeAlias = type)."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "ScalarTypeAliasAnnotation.jinja2"
    BASE_CLASS: ClassVar[str] = ""
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_TYPE_ALIAS,)


class DataTypeScalarTypeBackport(_DataTypeScalarBase):
    """GraphQL scalar using TypeAliasType for Python 3.10-3.11 (Name = TypeAliasType("Name", type))."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "ScalarTypeAliasType.jinja2"
    BASE_CLASS: ClassVar[str] = ""
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_TYPE_ALIAS_TYPE,)


class DataTypeScalarTypeStatement(_DataTypeScalarBase):
    """GraphQL scalar using type statement for Python 3.12+ (type Name = type)."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "ScalarTypeStatement.jinja2"
    BASE_CLASS: ClassVar[str] = ""
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = ()
    _PYDANTIC_FORM: ClassVar[PydanticConstruct | None] = "pydantic.type_statement"
