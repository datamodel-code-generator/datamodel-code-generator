"""Bind live Python runtime types to the immutable annotation IR."""

from __future__ import annotations

import types
from collections.abc import Callable as ABCCallable
from enum import Enum
from functools import lru_cache
from typing import Annotated, Concatenate, ForwardRef, Literal, ParamSpec, Union, get_args, get_origin
from weakref import ReferenceType, ref

from datamodel_code_generator._python_type_annotation import (
    PythonTypeEllipsis,
    PythonTypeExpr,
    PythonTypeLiteralValue,
    PythonTypeOpaqueText,
    PythonTypeParameterList,
    PythonTypeRuntimeSymbol,
    PythonTypeSubscript,
    PythonTypeUnion,
    python_type_name,
)


def _runtime_symbol(module: str, qualname: str, *, qualify: bool) -> PythonTypeExpr:
    """Build a runtime-identity leaf without parsing its rendered spelling."""
    parts = tuple(qualname.split("."))
    if not all(part.isidentifier() for part in parts):
        return PythonTypeOpaqueText(f"{module}.{qualname}" if module and qualify else qualname)
    if qualify and module not in {"", "builtins", "typing", "collections.abc"}:
        try:
            return PythonTypeRuntimeSymbol(module, parts)
        except ValueError:
            return PythonTypeOpaqueText(f"{module}.{qualname}")
    return python_type_name(qualname) if len(parts) == 1 else PythonTypeRuntimeSymbol("", parts)


def _runtime_leaf_expr(tp: object) -> PythonTypeExpr:
    if isinstance(tp, ForwardRef):
        # ForwardRef text is already a raw external boundary. Never parse it
        # with the host AST/tokenizer: the configured target may use a newer
        # grammar than the Python runtime executing the generator.
        return PythonTypeOpaqueText(tp.__forward_arg__)
    module = getattr(tp, "__module__", "")
    qualname = getattr(tp, "__qualname__", None) or getattr(tp, "__name__", None)
    if qualname is None:
        return PythonTypeOpaqueText(str(tp).replace("typing.", ""))
    return _runtime_symbol(module, qualname, qualify=True)


def _runtime_origin_expr(origin: object, *, qualify: bool) -> PythonTypeExpr:
    qualname = getattr(origin, "__qualname__", None) or getattr(origin, "__name__", None)
    if qualname is not None:
        return _runtime_symbol(getattr(origin, "__module__", ""), qualname, qualify=qualify)
    rendered = str(origin).replace("typing.", "")
    return python_type_name(rendered) if rendered.isidentifier() else PythonTypeOpaqueText(rendered)


def _callable_parameter_expr(parameters: object) -> PythonTypeExpr:
    """Normalize the runtime parameter portion of a Callable annotation."""
    if parameters is ...:
        return PythonTypeEllipsis()
    if isinstance(parameters, (list, tuple)):
        return PythonTypeParameterList(tuple(_runtime_expr(item, full_name=False) for item in parameters))
    if isinstance(parameters, ParamSpec) or get_origin(parameters) is Concatenate:
        return _runtime_expr(parameters, full_name=False)
    return PythonTypeParameterList((_runtime_expr(parameters, full_name=False),))


def python_callable_expr_from_runtime_args(args: tuple[object, ...]) -> PythonTypeExpr:
    """Build a normalized ``Callable`` expression from runtime arguments."""
    if not args:
        return python_type_name("Callable")
    parameters = args[0] if len(args) == 2 else list(args[:-1])  # noqa: PLR2004
    return PythonTypeSubscript(
        python_type_name("Callable"),
        (_callable_parameter_expr(parameters), _runtime_expr(args[-1], full_name=False)),
    )


def _runtime_expr(tp: object, *, full_name: bool) -> PythonTypeExpr:  # noqa: PLR0911
    """Convert a runtime type; ``full_name`` keeps the historical simple generic base and opaque strings."""
    if tp is type(None):
        return python_type_name("None")
    if full_name and isinstance(tp, str):
        return PythonTypeOpaqueText(tp)
    if not full_name and tp is ...:
        return PythonTypeEllipsis()

    origin = get_origin(tp)
    args = get_args(tp)
    if origin is None:
        return _runtime_leaf_expr(tp)
    if origin is ABCCallable:
        return python_callable_expr_from_runtime_args(args)
    if origin is Union or origin is getattr(types, "UnionType", None):
        return PythonTypeUnion(tuple(_runtime_expr(item, full_name=full_name) for item in args))
    if origin is Annotated:
        return (
            _runtime_expr(args[0], full_name=full_name)
            if args
            else PythonTypeOpaqueText(str(tp).replace("typing.", ""))
        )
    if origin is Literal:
        return PythonTypeSubscript(
            python_type_name("Literal"),
            tuple(
                PythonTypeOpaqueText(str(item)) if isinstance(item, Enum) else PythonTypeLiteralValue(item)
                for item in args
            ),
        )
    if origin is type:
        return (
            PythonTypeSubscript(python_type_name("Type"), (_runtime_expr(args[0], full_name=full_name),))
            if args
            else python_type_name("Type")
        )
    base = _runtime_origin_expr(origin, qualify=not full_name)
    return PythonTypeSubscript(base, tuple(_runtime_expr(item, full_name=full_name) for item in args)) if args else base


class _IdentityWeakRef:
    """Weak cache key whose equality follows referent identity, not user code."""

    __slots__ = ("_identity", "_reference")

    def __init__(self, value: object) -> None:
        self._identity = id(value)
        self._reference: ReferenceType[object] = ref(value)

    def __call__(self) -> object | None:
        """Return the live referent without retaining it."""
        return self._reference()

    def __hash__(self) -> int:
        """Hash independently of a user-defined referent hash."""
        return self._identity

    def __eq__(self, other: object) -> bool:
        """Compare live referents by identity and never invoke their equality."""
        if not isinstance(other, _IdentityWeakRef):
            return NotImplemented
        if self is other:
            return True
        value = self()
        return value is not None and value is other()


@lru_cache(maxsize=256)
def _python_type_expr_from_runtime_cached(
    type_ref: _IdentityWeakRef,
    full_name: bool,  # noqa: FBT001
) -> PythonTypeExpr:
    if (tp := type_ref()) is None:  # pragma: no cover
        msg = "Runtime type was released during annotation binding"
        raise RuntimeError(msg)
    return _runtime_expr(tp, full_name=full_name)


def _cached_runtime_expression(tp: object, *, full_name: bool) -> PythonTypeExpr:
    """Convert a runtime typing object without retaining user-owned model types.

    Runtime objects are an external input boundary, so opaque ForwardRef text is
    allowed here. It is deliberately not parsed with the host runtime grammar;
    the configured target Python can support a different annotation grammar.
    """
    try:
        type_ref = _IdentityWeakRef(tp)
    except TypeError:
        return _runtime_expr(tp, full_name=full_name)
    return _python_type_expr_from_runtime_cached(type_ref, full_name)


def python_type_expr_from_runtime(tp: object) -> PythonTypeExpr:
    """Convert a runtime typing object to an exact semantic expression."""
    return _cached_runtime_expression(tp, full_name=False)


def python_type_expr_from_runtime_full_name(tp: object) -> PythonTypeExpr:
    """Convert a runtime type using the historical generic-name policy."""
    return _cached_runtime_expression(tp, full_name=True)


__all__ = [
    "python_callable_expr_from_runtime_args",
    "python_type_expr_from_runtime",
    "python_type_expr_from_runtime_full_name",
]
