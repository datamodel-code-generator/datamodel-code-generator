"""Type alias model generators.

Provides classes for generating type aliases using different Python syntax:
TypeAlias annotation, TypeAliasType, and type statement (Python 3.12+).
"""

from __future__ import annotations

from typing import ClassVar

from datamodel_code_generator.imports import (
    IMPORT_ANNOTATED,
    IMPORT_TYPE_ALIAS,
    IMPORT_TYPE_ALIAS_TYPE,
    Import,
)
from datamodel_code_generator.model import DataModel
from datamodel_code_generator.types import chain_as_tuple


class TypeAliasBase(DataModel):
    """Base class for all type alias implementations."""

    IS_ALIAS: ClassVar[bool] = True
    SUPPORTS_GENERIC_BASE_CLASS: ClassVar[bool] = False
    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = True
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = None
    DOCSTRING_INDENT: ClassVar[int] = 0
    FIELD_DOCSTRING_INDENT: ClassVar[int] = 0

    @property
    def imports(self) -> tuple[Import, ...]:
        """Get imports including Annotated if needed."""
        imports = super().imports
        if self.fields and (self.fields[0].annotated or self.fields[0].field):
            imports = chain_as_tuple(imports, (IMPORT_ANNOTATED,))

        return imports


class TypeAlias(TypeAliasBase):
    """TypeAlias annotation for Python 3.10+ (Name: TypeAlias = type)."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "TypeAliasAnnotation.jinja2"
    BASE_CLASS: ClassVar[str] = ""
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_TYPE_ALIAS,)


class TypeAliasTypeBackport(TypeAliasBase):
    """TypeAliasType for Python 3.10-3.11 (Name = TypeAliasType("Name", type))."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "TypeAliasType.jinja2"
    BASE_CLASS: ClassVar[str] = ""
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_TYPE_ALIAS_TYPE,)
    DEFERS_RECURSIVE_TYPE_HINT: ClassVar[bool] = True


class TypeStatement(TypeAliasBase):
    """Type statement for Python 3.12+ (type Name = type).

    Note: Python 3.12+ type statements use deferred evaluation,
    so forward references don't need to be quoted.
    """

    TEMPLATE_FILE_PATH: ClassVar[str] = "TypeStatement.jinja2"
    BASE_CLASS: ClassVar[str] = ""
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = ()
