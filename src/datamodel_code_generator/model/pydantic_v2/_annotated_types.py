"""Lazy representations for annotated constrained scalar types."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from datamodel_code_generator.model.pydantic_v2.types import PydanticV2DataType
from datamodel_code_generator.python_literal import represent_python_value

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator.imports import Import


class AnnotatedStringDataType(PydanticV2DataType):
    """Keep string metadata and its annotation import without a second DataType."""

    annotated_string: ClassVar[bool] = True

    @property
    def type_hint(self) -> str:
        """Render string constraints inside an Annotated subscript."""
        return f"{self.runtime_expression_imports[0].binding_name}[str, {super().type_hint}]"

    @property
    def imports(self) -> Iterator[Import]:
        """Include the annotation's independently aliasable import."""
        yield from super().imports
        yield self.runtime_expression_imports[0]

    @property
    def base_type_hint(self) -> str:
        """Keep RootModel bases independent of their string constraints."""
        return "str"


class AnnotatedConstraintDataType(PydanticV2DataType):
    """Render a ``con*`` call as ``Annotated[base, Field(...)]``, which type checkers accept in type aliases.

    The data type keeps the ``con*`` import and keywords, so code that reads constraints sees the call it
    replaces. Its runtime imports start with ``Annotated`` and ``Field`` so that aliasing reaches them.
    """

    constrained_base: str = "str"

    @property
    def annotated_base_type(self) -> str:
        """Return the constrained scalar through its current binding, as ``Decimal`` may be aliased."""
        return next(
            (
                import_.binding_name
                for import_ in self.runtime_expression_imports
                if import_.import_ == self.constrained_base
            ),
            self.constrained_base,
        )

    @property
    def type_hint(self) -> str:
        """Render the ``con*`` keywords as ``Field`` constraints inside an Annotated subscript."""
        annotated, field = self.runtime_expression_imports[:2]
        keywords = ", ".join(f"{name}={represent_python_value(value)}" for name, value in (self.kwargs or {}).items())
        return f"{annotated.binding_name}[{self.annotated_base_type}, {field.binding_name}({keywords})]"

    @property
    def imports(self) -> Iterator[Import]:
        """Replace the ``con*`` import with the annotation imports."""
        yield from (import_ for import_ in super().imports if import_ != self.import_)
        yield from self.runtime_expression_imports
