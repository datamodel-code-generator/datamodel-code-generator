"""Neutral results produced by input-model conversion."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datamodel_code_generator._python_type_annotation import PythonTypeExpr

_LEGACY_PYTHON_TYPE_TOKEN_PREFIX = "<datamodel-code-generator-python-type:"  # noqa: S105


def is_legacy_python_type_token(value: object) -> bool:
    """Recognize the former private token shape for compatibility errors."""
    return isinstance(value, str) and value.startswith(_LEGACY_PYTHON_TYPE_TOKEN_PREFIX) and value.endswith(">")


class PythonTypeSchemaAnnotation:
    """Opaque schema annotation that preserves type IR through model dumps."""

    __slots__ = ("_expression", "_legacy_token")

    def __init__(self, expression: PythonTypeExpr, legacy_token: str | None = None) -> None:
        """Wrap one neutral type expression for schema transport."""
        self._expression = expression
        self._legacy_token = legacy_token

    @property
    def expression(self) -> PythonTypeExpr:
        """Return the neutral expression carried by this schema annotation."""
        return self._expression

    @property
    def legacy_token(self) -> str | None:
        """Return the build token retained for lazy private compatibility."""
        return self._legacy_token

    def __deepcopy__(self, _memo: dict[int, object]) -> PythonTypeSchemaAnnotation:
        """Share one immutable annotation across copied schema graphs."""
        return self


_EMPTY_PYTHON_TYPE_EXPRESSIONS: Mapping[str, PythonTypeExpr] = MappingProxyType({})


class PythonFieldOverrides:
    """Immutable, converter-owned Python field replacement information."""

    __slots__ = ("_fields",)

    def __init__(self, fields: Mapping[str, str]) -> None:
        """Freeze the field bindings for one Python model."""
        self._fields = MappingProxyType(dict(fields))

    @property
    def fields(self) -> Mapping[str, str]:
        """The child wire names and inherited bindings."""
        return self._fields

    def __deepcopy__(self, _memo: dict[int, object]) -> PythonFieldOverrides:
        """Share immutable bindings across schema copies."""
        return self


class PythonModelBases(list[dict[str, str]]):  # noqa: FURB189 - must remain JSON-serializable as an ordinary list.
    """JSON-compatible base references carrying owned override metadata."""

    __slots__ = ("_python_field_overrides",)

    def __init__(self, reference: str, fields: Mapping[str, str]) -> None:
        """Attach field replacements without reserving a caller extension key."""
        super().__init__([{"$ref": reference}])
        self._python_field_overrides = PythonFieldOverrides(fields)


@dataclass(frozen=True, slots=True, init=False)
class LoadedInputModelSchema(Mapping[str, object]):
    """Loaded input-model schema carrying neutral Python type annotations."""

    schema: dict[str, object]
    _legacy_python_type_expressions: Mapping[str, PythonTypeExpr] | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _python_type_expressions_cache: Mapping[str, PythonTypeExpr] | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __init__(
        self,
        schema: dict[str, object],
        python_type_expressions: Mapping[str, PythonTypeExpr] | None = None,
    ) -> None:
        """Initialize new IR or the historical two-argument private envelope."""
        object.__setattr__(self, "schema", schema)
        object.__setattr__(
            self,
            "_legacy_python_type_expressions",
            MappingProxyType(dict(python_type_expressions)) if python_type_expressions is not None else None,
        )
        object.__setattr__(self, "_python_type_expressions_cache", None)

    def __getitem__(self, key: str) -> object:
        """Return one schema value."""
        return self.schema[key]

    def __iter__(self) -> Iterator[str]:
        """Iterate schema keys."""
        return iter(self.schema)

    def __len__(self) -> int:
        """Return the number of top-level schema keys."""
        return len(self.schema)

    @property
    def name(self) -> str:
        """Preserve the historical header name of the former JSON text input."""
        return "<stdin>"

    @property
    def legacy_python_type_expressions(self) -> Mapping[str, PythonTypeExpr] | None:
        """Return an explicitly supplied legacy token table without deriving one."""
        return self._legacy_python_type_expressions

    @property
    def python_type_expressions(self) -> Mapping[str, PythonTypeExpr]:
        """Lazily expose the historical pruned token table for private callers."""
        if (expressions := self._legacy_python_type_expressions) is not None:
            return expressions
        if (expressions := self._python_type_expressions_cache) is not None:
            return expressions

        resolved: dict[str, PythonTypeExpr] = {}
        pending: list[object] = [self.schema]
        while pending:
            match pending.pop():
                case dict() as mapping:
                    pending.extend(mapping.values())
                case list() | tuple() as sequence:
                    pending.extend(sequence)
                case PythonTypeSchemaAnnotation() as annotation if annotation.legacy_token is not None:
                    resolved[annotation.legacy_token] = annotation.expression
                case _:
                    continue
        expressions = MappingProxyType(resolved) if resolved else _EMPTY_PYTHON_TYPE_EXPRESSIONS
        object.__setattr__(self, "_python_type_expressions_cache", expressions)  # noqa: PLC2801
        return expressions


# Preserve the former private transport identity for repr and pickling.
LoadedInputModelSchema.__module__ = "datamodel_code_generator._input_model_transport"

__all__ = ["LoadedInputModelSchema", "PythonTypeSchemaAnnotation", "is_legacy_python_type_token"]
