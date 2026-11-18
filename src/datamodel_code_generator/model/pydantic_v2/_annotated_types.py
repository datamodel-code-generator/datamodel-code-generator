"""Lazy representations for annotated constrained scalar types."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from typing_extensions import override

from datamodel_code_generator._python_syntax import call_text, keyword_argument, subscript_text
from datamodel_code_generator.imports import IMPORT_ANNOTATED, _import_key
from datamodel_code_generator.model.pydantic_v2.imports import (
    IMPORT_CONBYTES,
    IMPORT_CONDECIMAL,
    IMPORT_CONFLOAT,
    IMPORT_CONINT,
    IMPORT_CONSTR,
    IMPORT_FIELD,
    IMPORT_MULTIPLE_OF,
)
from datamodel_code_generator.model.pydantic_v2.types import PydanticV2DataType, _RuntimeAnnotatedDataType
from datamodel_code_generator.python_literal import represent_python_value

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from datamodel_code_generator.imports import Import, _ImportKey
    from datamodel_code_generator.types import DataType

_ANNOTATED_CONSTRAINT_BASES: dict[_ImportKey, str] = {
    _import_key(IMPORT_CONSTR): "str",
    _import_key(IMPORT_CONINT): "int",
    _import_key(IMPORT_CONFLOAT): "float",
    _import_key(IMPORT_CONDECIMAL): "Decimal",
    _import_key(IMPORT_CONBYTES): "bytes",
}
"""The scalar each ``con*`` call constrains, by its import."""


def constrained_base(data_type: DataType) -> str | None:
    """Return the scalar the ``con*`` call ``data_type`` constrains, or None for any other type."""
    return None if (import_ := data_type.import_) is None else _ANNOTATED_CONSTRAINT_BASES.get(_import_key(import_))


def annotated_constraints(data_type: DataType) -> DataType:
    """Return a ``con*`` call written as ``Annotated`` constraints, which type checkers accept, else ``data_type``."""
    return (
        data_type
        if (base := constrained_base(data_type)) is None
        else AnnotatedConstraintDataType.of_call(data_type, base)
    )


class AnnotatedStringDataType(PydanticV2DataType):
    """Keep string metadata and its annotation import without a second DataType."""

    annotated_string: ClassVar[bool] = True

    @property
    @override
    def type_hint(self) -> str:
        """Render string constraints inside an Annotated subscript."""
        return subscript_text(self.runtime_expression_imports[0].binding_name, ("str", super().type_hint))

    @property
    @override
    def imports(self) -> Iterator[Import]:
        """Include the annotation's independently aliasable import."""
        yield from super().imports
        yield self.runtime_expression_imports[0]

    @property
    @override
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


class AnnotatedConstraintDataType(_RuntimeAnnotatedDataType):
    """Render a ``con*`` call as ``Annotated`` constraints, which type checkers accept in type aliases.

    The data type keeps the ``con*`` import and keywords, so code that reads constraints sees the call it
    replaces. Its runtime imports hold the annotation imports so that aliasing reaches them.
    """

    constrained_base: str = "str"

    @classmethod
    def of_call(cls, data_type: DataType, base: str) -> AnnotatedConstraintDataType:
        """Return the ``con*`` call ``data_type``, which constrains ``base``, written as ``Annotated`` constraints."""
        values: dict[str, Any] = {
            **dict(data_type),
            "type": None,
            "parent": None,
            "children": [],
            "constrained_base": base,
        }
        return cls.model_construct(**values)

    @property
    @override
    def runtime_expression_imports(self) -> tuple[Import, ...]:
        """The imports set on the type, else those of its ``Annotated`` constraints, which aliasing reaches."""
        if imports := super().runtime_expression_imports:
            return imports
        field_keywords, multiple_of = annotated_constraint_metadata(self.constrained_base, self.kwargs or {})
        annotation_imports: tuple[Import, ...] = (IMPORT_FIELD,) if field_keywords else ()
        if multiple_of is not None:
            annotation_imports = (*annotation_imports, IMPORT_MULTIPLE_OF)
        return (IMPORT_ANNOTATED, *annotation_imports) if annotation_imports else ()

    @property
    def annotated_base_type(self) -> str:
        """Return the constrained scalar through its current binding, as ``Decimal`` may be aliased."""
        return self._binding(self.constrained_base, self.constrained_base)

    @property
    @override
    def base_type_hint(self) -> str:
        """The constrained scalar alone, so a ``RootModel`` base never builds a pattern before the config's engine."""
        return self.annotated_base_type

    @property
    @override
    def type_hint(self) -> str:
        """Render the ``con*`` keywords as constraints inside an Annotated subscript."""
        field_keywords, multiple_of = annotated_constraint_metadata(self.constrained_base, self.kwargs or {})
        metadata: list[str] = []
        if field_keywords:
            keywords = (keyword_argument(name, represent_python_value(value)) for name, value in field_keywords.items())
            metadata.append(call_text(self._binding("Field"), keywords))
        if multiple_of is not None:
            metadata.append(f"{self._binding('MultipleOf')}({represent_python_value(multiple_of)})")
        if not metadata:
            return self.annotated_base_type
        return subscript_text(self._binding("Annotated"), (self.annotated_base_type, *metadata))
