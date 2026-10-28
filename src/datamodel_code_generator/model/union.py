"""Union type model generators.

Provides classes for generating union type aliases for GraphQL union types.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from datamodel_code_generator.imports import (
    IMPORT_TYPE_ALIAS,
    IMPORT_TYPE_ALIAS_TYPE,
    IMPORT_UNION,
    Import,
)
from datamodel_code_generator.model import DataModel, DataModelFieldBase
from datamodel_code_generator.model.base import UNDEFINED, comment_safe

if TYPE_CHECKING:
    from collections import defaultdict
    from pathlib import Path

    from datamodel_code_generator._requirements import PydanticConstruct
    from datamodel_code_generator.reference import Reference


class _DataTypeUnionBase(DataModel):
    """Base class for GraphQL union types with shared __init__ logic."""

    FORMAT_DESCRIPTION_AS_DOCSTRING: ClassVar[bool] = False

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
        """Initialize GraphQL union type."""
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
            description=comment_safe(description),
            default=default,
            nullable=nullable,
            keyword_only=keyword_only,
            treat_dot_as_module=treat_dot_as_module,
        )


class DataTypeUnion(_DataTypeUnionBase):
    """GraphQL union using TypeAlias annotation for Python 3.10+ (Name: TypeAlias = Union[...])."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "UnionTypeAliasAnnotation.jinja2"
    BASE_CLASS: ClassVar[str] = ""
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (
        IMPORT_TYPE_ALIAS,
        IMPORT_UNION,
    )


class DataTypeUnionTypeBackport(_DataTypeUnionBase):
    """GraphQL union using TypeAliasType for Python 3.10-3.11 (Name = TypeAliasType("Name", Union[...]))."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "UnionTypeAliasType.jinja2"
    BASE_CLASS: ClassVar[str] = ""
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (
        IMPORT_TYPE_ALIAS_TYPE,
        IMPORT_UNION,
    )


class DataTypeUnionTypeStatement(_DataTypeUnionBase):
    """GraphQL union using type statement for Python 3.12+ (type Name = Union[...])."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "UnionTypeStatement.jinja2"
    BASE_CLASS: ClassVar[str] = ""
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_UNION,)
    _PYDANTIC_FORM: ClassVar[PydanticConstruct | None] = "pydantic.type_statement"
