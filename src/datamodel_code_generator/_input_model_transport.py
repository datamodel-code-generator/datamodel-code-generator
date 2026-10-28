"""Private build transport for input-model structured type expressions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from datamodel_code_generator._python_type_annotation import PythonTypeExpr

from datamodel_code_generator.input_model_result import LoadedInputModelSchema, PythonTypeSchemaAnnotation

_PYTHON_TYPE_TOKEN_PREFIX = "<datamodel-code-generator-python-type:"  # noqa: S105


class PythonTypeExpressionCollector:
    """Intern expressions behind per-load tokens that survive schema rewrites."""

    __slots__ = ("_expressions", "_nonce", "_tokens")

    def __init__(self) -> None:
        self._nonce: str | None = None
        self._expressions: dict[str, PythonTypeExpr] = {}
        self._tokens: dict[PythonTypeExpr, str] = {}

    def add(self, expression: PythonTypeExpr) -> str:
        """Return one stable token for an equal immutable expression."""
        if token := self._tokens.get(expression):
            return token
        if (nonce := self._nonce) is None:
            from secrets import token_hex  # noqa: PLC0415

            self._nonce = nonce = token_hex(16)
        token = f"{_PYTHON_TYPE_TOKEN_PREFIX}{nonce}:{len(self._expressions)}>"
        self._tokens[expression] = token
        self._expressions[token] = expression
        return token

    def loaded_schema(self, schema: dict[str, object]) -> LoadedInputModelSchema:
        """Resolve build-only tokens into neutral IR at the input-model boundary."""
        return LoadedInputModelSchema(_resolve_python_type_expressions(schema, self._expressions))


def is_python_type_token(value: object) -> bool:
    """Return whether a value belongs to the private, deliberately invalid syntax."""
    return isinstance(value, str) and value.startswith(_PYTHON_TYPE_TOKEN_PREFIX) and value.endswith(">")


def externalize_python_type_token(value: Any, expressions: Mapping[str, PythonTypeExpr] | None) -> Any:
    """Preserve the historical token externalizer for private callers."""
    if expressions is None or not isinstance(value, str) or (expression := expressions.get(value)) is None:
        return value

    from datamodel_code_generator._python_type_annotation import render_python_type_expr  # noqa: PLC0415

    return render_python_type_expr(expression)


def _resolve_python_type_expressions(
    schema: dict[str, object],
    expressions: Mapping[str, PythonTypeExpr],
) -> dict[str, object]:
    """Replace surviving build tokens in place without copying the schema tree."""
    pending: list[object] = [schema]
    while pending:
        match pending.pop():
            case dict() as mapping:
                if (
                    isinstance(token := mapping.get("x-python-type"), str)
                    and (expression := expressions.get(token)) is not None
                ):
                    mapping["x-python-type"] = PythonTypeSchemaAnnotation(expression, token)
                pending.extend(mapping.values())
            case list() | tuple() as sequence:
                pending.extend(sequence)
            case _:
                continue
    return schema


__all__ = [
    "LoadedInputModelSchema",
    "PythonTypeExpressionCollector",
    "externalize_python_type_token",
    "is_python_type_token",
]
