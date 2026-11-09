"""Private build transport for input-model structured type expressions."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from datamodel_code_generator._python_type_annotation import PythonTypeExpr

from datamodel_code_generator._json_schema import json_nodes
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


def _resolve_python_type_expressions(
    schema: dict[str, object],
    expressions: Mapping[str, PythonTypeExpr],
) -> dict[str, object]:
    """Replace surviving build tokens in place without copying the schema tree."""
    for node in json_nodes(schema, tuples=True):
        if (
            isinstance(node, dict)
            and isinstance(token := node.get("x-python-type"), str)
            and (expression := expressions.get(token)) is not None
        ):
            node["x-python-type"] = PythonTypeSchemaAnnotation(expression, token)
    return schema


__all__ = [
    "LoadedInputModelSchema",
    "PythonTypeExpressionCollector",
]
