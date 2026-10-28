"""Type alias model generators.

Provides classes for generating type aliases using different Python syntax:
TypeAlias annotation, TypeAliasType, and type statement (Python 3.12+).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from typing_extensions import override

from datamodel_code_generator.imports import (
    IMPORT_ANNOTATED,
    IMPORT_TYPE_ALIAS,
    IMPORT_TYPE_ALIAS_TYPE,
    Import,
)
from datamodel_code_generator.model import DataModel
from datamodel_code_generator.types import chain_as_tuple

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator._requirements import PydanticConstruct


class TypeAliasBase(DataModel):
    """Base class for all type alias implementations."""

    IS_ALIAS: ClassVar[bool] = True
    SUPPORTS_GENERIC_BASE_CLASS: ClassVar[bool] = False
    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = True
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = None
    DOCSTRING_INDENT: ClassVar[int] = 0
    FIELD_DOCSTRING_INDENT: ClassVar[int] = 0
    _FREEZES_FIELD_RENDER_PLANS: ClassVar[bool] = True
    """A field that renders from a plan (Pydantic's) writes the alias from its frozen plan; others build none."""

    @property
    @override
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
    IS_EVALUATED_ALIAS: ClassVar[bool] = True
    """Whether the name is the value itself at runtime, so a name of the value None is None."""


class TypeAliasTypeBackport(TypeAliasBase):
    """TypeAliasType for Python 3.10-3.11 (Name = TypeAliasType("Name", type))."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "TypeAliasType.jinja2"
    BASE_CLASS: ClassVar[str] = ""
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_TYPE_ALIAS_TYPE,)
    DEFERS_RECURSIVE_TYPE_HINT: ClassVar[bool] = True
    _WRITES_REFERENCE_ALIAS_TYPE: ClassVar[bool] = True
    """Whether an alias whose value is another model or alias is a ``TypeAliasType`` too."""

    def _aliases_reference(self) -> bool:
        """Return whether the alias's value is another model or alias."""
        return bool(self.fields) and self.fields[0].data_type.reference is not None

    @override
    def _pydantic_constructs(self) -> Iterator[tuple[PydanticConstruct, str | None]]:
        """Also yield an alias of another model or alias: Pydantic resolves it inside a model from 2.10 on."""
        yield from super()._pydantic_constructs()
        if self._WRITES_REFERENCE_ALIAS_TYPE and self._aliases_reference():
            yield "pydantic.model_type_alias", None


class TypeStatement(TypeAliasBase):
    """Type statement for Python 3.12+ (type Name = type).

    Note: Python 3.12+ type statements use deferred evaluation,
    so forward references don't need to be quoted.
    """

    TEMPLATE_FILE_PATH: ClassVar[str] = "TypeStatement.jinja2"
    BASE_CLASS: ClassVar[str] = ""
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = ()
    _PYDANTIC_FORM: ClassVar[PydanticConstruct | None] = "pydantic.type_statement"
