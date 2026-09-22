"""Freeze finite builtin values without executing user containers or expressions."""

from __future__ import annotations

from decimal import Decimal
from math import isfinite
from typing import TYPE_CHECKING, Final, Literal

from typing_extensions import TypeIs

from datamodel_code_generator._generation_contract import (
    ImportedExpression,
    LiteralMapping,
    LiteralScalar,
    LiteralSequence,
    SourceExpression,
)
from datamodel_code_generator.python_literal import PythonCode, PythonRuntimeExpression

_CUSTOM_BINDING_REQUIRED: Final = "BND_CUSTOM_BINDING_REQUIRED"


class UnsupportedBindingValueError(Exception):
    """Report a finite unsupported literal without executing or coercing it."""

    def __init__(self, reason: TypeProjectionReason) -> None:
        """Keep the same diagnostic identity through projection and emitted facts."""
        self.reason: TypeProjectionReason = reason
        super().__init__(reason)


def _is_literal_mapping(value: object) -> TypeIs[dict[object, object]]:
    return type(value) is dict


def _is_literal_sequence(value: object) -> TypeIs[list[object] | tuple[object, ...] | set[object] | frozenset[object]]:
    return type(value) in {list, tuple, set, frozenset}


def _literal_scalar(value: object) -> LiteralScalar | None:  # ruff: ignore[too-many-return-statements]
    if value is None:
        return LiteralScalar("none", None)
    if type(value) is bool:
        return LiteralScalar("bool", value)
    if type(value) is int:
        return LiteralScalar("int", value)
    if type(value) is float and isfinite(value):
        return LiteralScalar("float", value)
    if type(value) is str:
        return LiteralScalar("str", value)
    if type(value) is bytes:
        return LiteralScalar("bytes", value)
    if type(value) is Decimal and value.is_finite():
        return LiteralScalar("decimal", value)
    return None


def freeze_literal(value: object, active: set[int]) -> FrozenLiteral:
    if (scalar := _literal_scalar(value)) is not None:
        return scalar
    identity = id(value)
    if identity in active:
        raise UnsupportedBindingValueError(_CUSTOM_BINDING_REQUIRED)
    active.add(identity)
    try:
        if _is_literal_mapping(value):
            return LiteralMapping(
                tuple((freeze_literal(key, active), freeze_literal(item, active)) for key, item in value.items())
            )
        if _is_literal_sequence(value):
            kind: Literal["list", "tuple", "set", "frozenset"]
            match value:
                case list():
                    kind = "list"
                case tuple():
                    kind = "tuple"
                case set():
                    kind = "set"
                case _:
                    kind = "frozenset"
            return LiteralSequence(kind, tuple(freeze_literal(item, active) for item in value))
        raise UnsupportedBindingValueError(_CUSTOM_BINDING_REQUIRED)
    finally:
        active.remove(identity)


def freeze_argument(value: object) -> TypeArgument:
    if type(value) is PythonCode:
        return SourceExpression(value.code)
    if type(value) is PythonRuntimeExpression:
        return ImportedExpression(value.import_, value.prefix, value.suffix)
    return freeze_literal(value, set())


if TYPE_CHECKING:
    from datamodel_code_generator._generation_contract import FrozenLiteral, TypeArgument, TypeProjectionReason
