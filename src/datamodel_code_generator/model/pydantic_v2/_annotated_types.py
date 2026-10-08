"""Lazy representations for annotated constrained scalar types."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from datamodel_code_generator.model.pydantic_v2.types import PydanticV2DataType
from datamodel_code_generator.python_literal import represent_python_value

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

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


def annotated_constraint_metadata(base: str, keywords: Mapping[str, Any]) -> tuple[dict[str, Any], Any]:
    """Split ``con*`` keywords into ``Field`` keywords and a ``Decimal`` ``multiple_of``.

    ``conbytes`` omits a zero ``min_length`` from its JSON schema, so it is dropped. ``Field`` types
    ``multiple_of`` as a float, so a ``Decimal`` one becomes ``annotated_types.MultipleOf``.
    """
    field_keywords = {
        name: value for name, value in keywords.items() if not (base == "bytes" and name == "min_length" and value == 0)
    }
    multiple_of = field_keywords.pop("multiple_of", None) if base == "Decimal" else None
    return field_keywords, multiple_of


class AnnotatedConstraintDataType(PydanticV2DataType):
    """Render a ``con*`` call as ``Annotated`` constraints, which type checkers accept in type aliases.

    The data type keeps the ``con*`` import and keywords, so code that reads constraints sees the call it
    replaces. Its runtime imports hold the annotation imports so that aliasing reaches them.
    """

    constrained_base: str = "str"

    def _binding(self, name: str) -> str:
        return next(import_.binding_name for import_ in self.runtime_expression_imports if import_.import_ == name)

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
        """Render the ``con*`` keywords as constraints inside an Annotated subscript."""
        field_keywords, multiple_of = annotated_constraint_metadata(self.constrained_base, self.kwargs or {})
        metadata: list[str] = []
        if field_keywords:
            keywords = ", ".join(f"{name}={represent_python_value(value)}" for name, value in field_keywords.items())
            metadata.append(f"{self._binding('Field')}({keywords})")
        if multiple_of is not None:
            metadata.append(f"{self._binding('MultipleOf')}({represent_python_value(multiple_of)})")
        if not metadata:
            return self.annotated_base_type
        return f"{self._binding('Annotated')}[{self.annotated_base_type}, {', '.join(metadata)}]"

    @property
    def imports(self) -> Iterator[Import]:
        """Replace the ``con*`` import with the annotation imports."""
        yield from (import_ for import_ in super().imports if import_ != self.import_)
        yield from self.runtime_expression_imports
