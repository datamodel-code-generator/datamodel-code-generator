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
    _python_type_name,
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


_LEGACY_STARRED_SENTINEL_BASE = "__datamodel_code_generator_starred"
_OPENING_DELIMITERS = {"(": ")", "[": "]", "{": "}"}


def _legacy_starred_parse_text(type_str: str) -> tuple[str, str] | None:
    """Rewrite only version-invariant variadic punctuation for Python 3.10.

    ``generate_tokens`` belongs to the running Python, not the requested output
    version. It must never be extended here to validate or interpret target
    grammar; this compatibility pass recognizes only delimiter depth and a
    prefix ``*`` in the annotation subset shared by supported runtimes.
    """
    import tokenize  # noqa: PLC0415  # Import only after AST rejects a variadic annotation.
    from io import StringIO  # noqa: PLC0415

    try:
        tokens = list(tokenize.generate_tokens(StringIO(type_str).readline))
    except tokenize.TokenError as error:
        raise _InvalidPythonTypeAnnotationError from error

    ignored_token_types = frozenset({
        tokenize.COMMENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.INDENT,
        tokenize.NEWLINE,
        tokenize.NL,
    })
    identifiers = {token_info.string for token_info in tokens if token_info.type == tokenize.NAME}
    sentinel = _LEGACY_STARRED_SENTINEL_BASE
    while sentinel in identifiers:
        sentinel += "_"

    result: list[tuple[int, str]] = []
    delimiter_stack: list[str] = []
    starred_depths: list[int] = []
    previous_significant = ""
    replaced = False

    for token_info in tokens:
        token_type, lexeme = token_info.type, token_info.string
        if lexeme in {",", ")", "]", "}"}:
            while starred_depths and starred_depths[-1] == len(delimiter_stack):
                result.append((tokenize.OP, ")"))
                starred_depths.pop()

        # Runtime token streams never decide target syntax. Only the outer,
        # cross-version variadic marker emitted by this codec is rewritten.
        is_variadic_star = lexeme == "*" and "]" in delimiter_stack and previous_significant in {"(", "[", ","}
        if is_variadic_star:
            result.extend(((tokenize.NAME, sentinel), (tokenize.OP, "(")))
            starred_depths.append(len(delimiter_stack))
            replaced = True
        else:
            result.append((token_type, lexeme))

        if lexeme in _OPENING_DELIMITERS:
            delimiter_stack.append(_OPENING_DELIMITERS[lexeme])
        elif lexeme in {")", "]", "}"} and delimiter_stack and lexeme == delimiter_stack[-1]:
            delimiter_stack.pop()

        if token_type not in ignored_token_types and token_type != tokenize.ENDMARKER:
            previous_significant = lexeme

    return (tokenize.untokenize(result), sentinel) if replaced else None


class _LegacyStarredRestorer(ast.NodeTransformer):
    """Restore compatibility-parser sentinel calls to native ``ast.Starred`` nodes."""

    def __init__(self, sentinel: str) -> None:
        self.sentinel = sentinel

    def visit_Call(self, node: ast.Call) -> ast.AST:
        """Restore only the exact sentinel call shape emitted by the tokenizer."""
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == self.sentinel
            and len(node.args) == 1
            and not node.keywords
        ):
            return ast.copy_location(ast.Starred(value=self.visit(node.args[0]), ctx=ast.Load()), node)
        return self.generic_visit(node)


def _parse_python_type_annotation_ast(type_str: str) -> ast.expr:
    try:
        # feature_version is a best-effort grammar switch in the running AST
        # implementation. The strict node codec below, not this call alone,
        # defines the version-independent annotation subset we accept.
        return ast.parse(type_str, mode="eval", feature_version=(3, 10)).body
    except SyntaxError:
        # Most failures are ordinary invalid input. Avoid importing and running
        # the runtime tokenizer unless the only fallback we support can apply.
        if "*" not in type_str or (legacy_parse := _legacy_starred_parse_text(type_str)) is None:
            raise
        rewritten, sentinel = legacy_parse
        expression = ast.parse(rewritten, mode="eval", feature_version=(3, 10)).body
        restored = _LegacyStarredRestorer(sentinel).visit(expression)
        if not isinstance(restored, ast.expr):  # pragma: no cover
            raise _InvalidPythonTypeAnnotationError from None
        return restored


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


def _python_type_expr_from_ast(  # noqa: PLR0911, PLR0912
    node: ast.expr,
    *,
    allow_literal: bool,
) -> PythonTypeExpr:
    match node:
        case ast.Name(id=name):
            return _python_type_name(name)
        case ast.Attribute():
            if (parts := _qualified_name_parts(node)) is None:
                raise _InvalidPythonTypeAnnotationError
            return PythonTypeQualifiedName(parts)
        case ast.Subscript(value=value, slice=slice_node):
            base = _python_type_expr_from_ast(value, allow_literal=False)
            if isinstance(slice_node, ast.Tuple):
                arguments = tuple(_python_type_expr_from_ast(item, allow_literal=True) for item in slice_node.elts)
            else:
                arguments = (_python_type_expr_from_ast(slice_node, allow_literal=True),)
            return PythonTypeSubscript(base, arguments)
        case ast.List(elts=items) if allow_literal:
            return PythonTypeParameterList(
                tuple(_python_type_expr_from_ast(item, allow_literal=True) for item in items)
            )
        case ast.Tuple(elts=items) if allow_literal:
            return PythonTypeTuple(tuple(_python_type_expr_from_ast(item, allow_literal=True) for item in items))
        case ast.Starred(value=value) if allow_literal:
            return PythonTypeStarred(_python_type_expr_from_ast(value, allow_literal=False))
        case ast.BinOp(op=ast.BitOr()):
            return _python_type_union_from_ast(node)
        case ast.Constant(value=None):
            return _python_type_name("None")
        case ast.Constant(value=value) if allow_literal and value is Ellipsis:
            return PythonTypeEllipsis()
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
