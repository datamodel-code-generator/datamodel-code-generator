"""Runtime raw-text codec for immutable Python type annotations."""

from __future__ import annotations

import ast
from functools import lru_cache

from datamodel_code_generator._python_type_annotation import (
    _LITERAL_VALUE_TYPES,
    PythonTypeEllipsis,
    PythonTypeExpr,
    PythonTypeLiteralValue,
    PythonTypeParameterList,
    PythonTypeQualifiedName,
    PythonTypeStarred,
    PythonTypeSubscript,
    PythonTypeTuple,
    PythonTypeUnion,
    python_type_name,
)


def _qualified_name_parts(node: ast.expr) -> tuple[str, ...] | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return tuple(reversed(parts))


class _InvalidPythonTypeAnnotationError(ValueError):
    pass


def _parse_python_type_annotation_ast(type_str: str) -> ast.expr:
    # feature_version is a best-effort grammar switch in the running AST
    # implementation. The strict node codec below, not this call alone,
    # defines the version-independent annotation subset we accept.
    return ast.parse(type_str, mode="eval", feature_version=(3, 10)).body


def _python_type_union_from_ast(node: ast.BinOp) -> PythonTypeUnion:
    """Convert and flatten a bit-or tree iteratively in source order."""
    pending: list[ast.expr] = [node]
    items: list[PythonTypeExpr] = []
    while pending:
        current = pending.pop()
        if isinstance(current, ast.BinOp) and isinstance(current.op, ast.BitOr):
            pending.extend((current.right, current.left))
        else:
            items.append(_python_type_expr_from_ast(current, allow_literal=False))
    return PythonTypeUnion(tuple(items))


def _is_literal_base(base: PythonTypeExpr) -> bool:
    """Report whether a subscript base is ``Literal`` or a ``….Literal`` alias.

    Only ``Literal[...]`` arguments may carry string/bytes values. Elsewhere a
    string constant would render as a quoted forward reference, which Pydantic v2
    evaluates via ``eval()`` during ``model_rebuild()`` — a code-execution sink.
    """
    if isinstance(base, PythonTypeQualifiedName):
        return base.parts[-1] == "Literal"
    return getattr(base, "value", None) == "Literal"


def _python_type_expr_from_ast(  # noqa: PLR0911, PLR0912
    node: ast.expr,
    *,
    allow_literal: bool,
    allow_string_literal: bool = False,
) -> PythonTypeExpr:
    match node:
        case ast.Name(id=name):
            return python_type_name(name)
        case ast.Attribute():
            if (parts := _qualified_name_parts(node)) is None:
                raise _InvalidPythonTypeAnnotationError
            return PythonTypeQualifiedName(parts)
        case ast.Subscript(value=value, slice=slice_node):
            base = _python_type_expr_from_ast(value, allow_literal=False)
            strings_allowed = _is_literal_base(base)
            arguments = slice_node.elts if isinstance(slice_node, ast.Tuple) else (slice_node,)
            return PythonTypeSubscript(
                base,
                tuple(
                    _python_type_expr_from_ast(item, allow_literal=True, allow_string_literal=strings_allowed)
                    for item in arguments
                ),
            )
        case ast.List() | ast.Tuple() if allow_literal:
            return (PythonTypeParameterList if isinstance(node, ast.List) else PythonTypeTuple)(
                tuple(
                    _python_type_expr_from_ast(item, allow_literal=True, allow_string_literal=allow_string_literal)
                    for item in node.elts
                )
            )
        case ast.Starred(value=value) if allow_literal:
            return PythonTypeStarred(_python_type_expr_from_ast(value, allow_literal=False))
        case ast.BinOp(op=ast.BitOr()):
            return _python_type_union_from_ast(node)
        case ast.Constant(value=None):
            return python_type_name("None")
        case ast.Constant(value=value) if allow_literal and value is Ellipsis:
            return PythonTypeEllipsis()
        case ast.Constant(value=value) if allow_literal and isinstance(value, (str, bytes)):
            # String/bytes constants are only valid as ``Literal[...]`` members.
            # In any other position they render as a quoted forward reference
            # that Pydantic v2 later evaluates, so reject them here.
            if not allow_string_literal:
                raise _InvalidPythonTypeAnnotationError
            return PythonTypeLiteralValue(value)
        case ast.Constant(value=value) if allow_literal and isinstance(value, _LITERAL_VALUE_TYPES):
            return PythonTypeLiteralValue(value)
        case ast.UnaryOp(op=ast.UAdd() | ast.USub() as operator, operand=ast.Constant(value=value)) if (
            allow_literal and (type(value) is int or type(value) is float)
        ):
            return PythonTypeLiteralValue(+value if isinstance(operator, ast.UAdd) else -value)
    raise _InvalidPythonTypeAnnotationError


@lru_cache(maxsize=1024)
def parse_python_type_annotation(type_str: str) -> PythonTypeExpr | None:
    """Parse external annotation text using the runtime only as a strict codec.

    The running AST/tokenizer is not target-version authority. Internal codegen
    stages must carry ``PythonTypeExpr`` and must not render/reparse through this
    function; the explicit conversion below defines the accepted shared subset.
    The bounded cache keeps repeated external annotations off the parsing path.
    """
    try:
        node = _parse_python_type_annotation_ast(type_str)
        return _python_type_expr_from_ast(node, allow_literal=False)
    except (SyntaxError, UnicodeError, ValueError, RecursionError, _InvalidPythonTypeAnnotationError):
        return None
