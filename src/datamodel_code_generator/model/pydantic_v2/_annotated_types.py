"""Lazy representations for annotated constrained scalar types."""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator.model.pydantic_v2.types import PydanticV2DataType

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator.imports import Import


class AnnotatedStringDataType(PydanticV2DataType):
    """Keep string metadata and its annotation import without a second DataType."""

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
