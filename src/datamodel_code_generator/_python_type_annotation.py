"""Lightweight immutable expressions for Python type annotations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from datamodel_code_generator._python_syntax import list_display, subscript_text, tuple_display
from datamodel_code_generator._target_unicode import is_python_name

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


class PythonTypeExpr:
    """Base class for immutable semantic Python type expressions."""

    __slots__ = ()

    def __deepcopy__(self, _memo: dict[int, object]) -> PythonTypeExpr:
        """Share immutable expressions across copied model graphs."""
        return self


@dataclass(frozen=True, slots=True)
class PythonTypeName(PythonTypeExpr):
    """One unqualified Python name."""

    value: str

    def __post_init__(self) -> None:
        if self.value != "None" and not is_python_name(self.value, rule="host"):
            msg = f"Python type name must be one identifier: {self.value!r}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PythonTypeQualifiedName(PythonTypeExpr):
    """A syntactic dotted name parsed from external annotation text."""

    parts: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.parts or not all(is_python_name(part, rule="host") for part in self.parts):
            msg = f"Python qualified type name must contain identifiers: {self.parts!r}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PythonTypeRuntimeSymbol(PythonTypeExpr):
    """An exact module and qualified-name identity supplied by a runtime producer."""

    module: str
    qualname_parts: tuple[str, ...]

    def __post_init__(self) -> None:
        module_parts = self.module.split(".") if self.module else ()
        if (
            not self.qualname_parts
            or not all(is_python_name(part, rule="host") for part in self.qualname_parts)
            or not all(is_python_name(part, rule="host") for part in module_parts)
        ):
            msg = f"Invalid runtime Python type identity: {self.module!r}, {self.qualname_parts!r}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PythonTypeMember(PythonTypeExpr):
    """A member of a class written as a ``Literal`` value, such as ``HTTPStatus.OK``."""

    owner: PythonTypeExpr
    name: str

    def __post_init__(self) -> None:
        if not is_python_name(self.name, rule="host"):
            msg = f"Python type member must be one identifier: {self.name!r}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PythonTypeBoundName(PythonTypeExpr):
    """A rendered name tied to the exact import that introduced it."""

    value: str
    import_from: str | None
    import_name: str

    def __post_init__(self) -> None:
        if not is_python_name(self.value, rule="host") or not self.import_name:
            msg = f"Invalid bound Python type name: {self.value!r}, {self.import_from!r}, {self.import_name!r}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PythonTypeOpaqueText(PythonTypeExpr):
    """Text retained only for a future runtime or forward-reference boundary."""

    value: str


@dataclass(frozen=True, slots=True)
class PythonTypeSubscript(PythonTypeExpr):
    """A subscripted type expression such as ``list[str]``."""

    base: PythonTypeExpr
    arguments: tuple[PythonTypeExpr, ...]


@dataclass(frozen=True, slots=True)
class PythonTypeModelField(PythonTypeExpr):
    """An observed native Pydantic field annotation, with optional type arguments."""

    model: PythonTypeExpr
    field_name: str
    arguments: tuple[int, ...] = ()
    pydantic: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.field_name, str) or not isinstance(self.pydantic, bool):
            msg = "A native annotation path requires a string field and a boolean model family"
            raise TypeError(msg)
        if any(type(index) is not int or index < 0 for index in self.arguments):
            msg = "Native annotation argument indexes must be nonnegative integers"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PythonTypeUnion(PythonTypeExpr):
    """A union rendered with the ``|`` spelling."""

    items: tuple[PythonTypeExpr, ...]


@dataclass(frozen=True, slots=True)
class PythonTypeParameterList(PythonTypeExpr):
    """The bracketed positional parameter list inside ``Callable``."""

    items: tuple[PythonTypeExpr, ...]


@dataclass(frozen=True, slots=True)
class PythonTypeTuple(PythonTypeExpr):
    """An explicit tuple expression nested inside an annotation argument."""

    items: tuple[PythonTypeExpr, ...]


@dataclass(frozen=True, slots=True)
class PythonTypeStarred(PythonTypeExpr):
    """One unpacked variadic expression such as ``*Ts``."""

    value: PythonTypeExpr


_LITERAL_VALUE_TYPES = (str, bytes, int, float, bool)
_MODULE_MEMBER_PARTS = 3


@dataclass(frozen=True, slots=True, eq=False)
class PythonTypeLiteralValue(PythonTypeExpr):
    """A supported literal value rendered with its deterministic ``repr``."""

    value: object

    def __post_init__(self) -> None:
        if self.value is not None and type(self.value) not in _LITERAL_VALUE_TYPES:
            msg = f"Unsupported Python type literal value: {self.value!r}"
            raise ValueError(msg)
        if isinstance(self.value, float) and math.isnan(self.value):
            msg = "NaN is not a supported Python type literal value"
            raise ValueError(msg)

    def __eq__(self, other: object) -> bool:
        """Compare both the literal value and its exact Python type."""
        return (
            isinstance(other, PythonTypeLiteralValue)
            and type(self.value) is type(other.value)
            and self.value == other.value
        )

    def __hash__(self) -> int:
        """Hash consistently with type-sensitive literal equality."""
        return hash((type(self.value), self.value))


@dataclass(frozen=True, slots=True)
class PythonTypeEllipsis(PythonTypeExpr):
    """The ellipsis argument used by ``Callable[..., ReturnType]``."""


_COMMON_TYPE_NAMES = {
    value: PythonTypeName(value)
    for value in (
        "None",
        "Any",
        "Annotated",
        "Callable",
        "Dict",
        "FrozenSet",
        "List",
        "Literal",
        "Mapping",
        "Optional",
        "Sequence",
        "Set",
        "Tuple",
        "Type",
        "Union",
        "bool",
        "bytes",
        "dict",
        "float",
        "frozenset",
        "int",
        "list",
        "object",
        "set",
        "str",
        "tuple",
        "type",
    )
}


def python_type_name(value: str) -> PythonTypeName:
    """Return a shared semantic node for common unqualified names."""
    return _COMMON_TYPE_NAMES.get(value) or PythonTypeName(value)


def _runtime_symbol_name(symbol: PythonTypeRuntimeSymbol) -> str:
    name = ".".join(symbol.qualname_parts)
    return f"{symbol.module}.{name}" if symbol.module else name


def _unchanged(rewritten: tuple[PythonTypeExpr, ...], original: tuple[PythonTypeExpr, ...]) -> bool:
    return all(new is old for new, old in zip(rewritten, original, strict=True))


def render_python_type_expr(expression: PythonTypeExpr) -> str:  # ruff: ignore[too-many-return-statements, too-many-branches]
    """Render an expression with stable formatting and no AST round trip."""
    match expression:
        case PythonTypeName() | PythonTypeBoundName() | PythonTypeOpaqueText():
            return expression.value
        case PythonTypeQualifiedName():
            return ".".join(expression.parts)
        case PythonTypeRuntimeSymbol():
            return _runtime_symbol_name(expression)
        case PythonTypeMember():
            return f"{render_python_type_expr(expression.owner)}.{expression.name}"
        case PythonTypeSubscript():
            rendered_base = render_python_type_expr(expression.base)
            if isinstance(expression.base, PythonTypeUnion):
                rendered_base = f"({rendered_base})"
            return subscript_text(rendered_base, map(render_python_type_expr, expression.arguments))
        case PythonTypeModelField():
            member = "model_fields" if expression.pydantic else "__annotations__"
            result = f"{render_python_type_expr(expression.model)}.{member}[{expression.field_name!r}]"
            if expression.pydantic:
                result += ".annotation"
            for index in expression.arguments:
                result += f".__args__[{index}]"
            return result
        case PythonTypeUnion():
            return " | ".join(map(render_python_type_expr, expression.items))
        case PythonTypeParameterList():
            return list_display(map(render_python_type_expr, expression.items))
        case PythonTypeTuple():
            return tuple_display(map(render_python_type_expr, expression.items))
        case PythonTypeStarred():
            value = render_python_type_expr(expression.value)
            return f"*({value})" if isinstance(expression.value, PythonTypeUnion) else f"*{value}"
        case PythonTypeLiteralValue():
            if isinstance(expression.value, float) and math.isinf(expression.value):
                return "1e309" if expression.value > 0 else "-1e309"
            return repr(expression.value)
        case PythonTypeEllipsis():
            return "..."
    msg = f"Unsupported Python type expression: {type(expression).__name__}"
    raise TypeError(msg)


if TYPE_CHECKING:
    # Declare the lazy attribute without a function body. A TYPE_CHECKING-only
    # stub looks like a real procedure to whole-program analyzers and can make
    # them infer that every runtime call returns None.
    parse_python_type_annotation: Callable[[str], PythonTypeExpr | None]


def __getattr__(name: str) -> object:
    """Load the raw-text parser only when the compatibility API requests it."""
    if name != "parse_python_type_annotation":
        raise AttributeError(name)

    # This is the single raw-text boundary. Internal codegen stages that already
    # own PythonTypeExpr must not pay for or pass back through the runtime codec.
    from datamodel_code_generator._python_type_annotation_codec import (  # noqa: PLC0415
        parse_python_type_annotation,
    )

    globals()[name] = parse_python_type_annotation
    return parse_python_type_annotation


def is_union_python_type_expr(expression: PythonTypeExpr) -> bool:
    """Return whether an expression is ``|``, ``Union``, or ``Optional``."""
    return isinstance(expression, PythonTypeUnion) or (
        isinstance(expression, PythonTypeSubscript) and python_type_expr_base_name(expression) in {"Union", "Optional"}
    )


def _iter_names(expression: PythonTypeExpr, *, qualified: bool) -> Iterator[str]:
    match expression:
        case PythonTypeName() | PythonTypeBoundName():
            if not qualified:
                yield expression.value
        case PythonTypeQualifiedName():
            yield ".".join(expression.parts) if qualified else expression.parts[-1]
        case PythonTypeRuntimeSymbol():
            yield _runtime_symbol_name(expression) if qualified else expression.qualname_parts[-1]
        case PythonTypeSubscript():
            yield from _iter_names(expression.base, qualified=qualified)
            for argument in expression.arguments:
                yield from _iter_names(argument, qualified=qualified)
        case PythonTypeModelField():
            yield from _iter_names(expression.model, qualified=qualified)
        case PythonTypeMember():
            yield from _iter_names(expression.owner, qualified=qualified)
        case PythonTypeStarred():
            yield from _iter_names(expression.value, qualified=qualified)
        case PythonTypeUnion() | PythonTypeParameterList() | PythonTypeTuple():
            for item in expression.items:
                yield from _iter_names(item, qualified=qualified)


def iter_python_type_expr_names(expression: PythonTypeExpr) -> Iterator[str]:
    """Yield semantic leaf names in stable structure order."""
    return _iter_names(expression, qualified=False)


def python_type_expr_base_name(expression: PythonTypeExpr) -> str:
    """Return the terminal base name of an annotation expression."""
    if isinstance(expression, PythonTypeSubscript):
        return python_type_expr_base_name(expression.base)
    if isinstance(expression, PythonTypeName | PythonTypeBoundName):
        return expression.value
    if isinstance(expression, PythonTypeQualifiedName):
        return expression.parts[-1]
    if isinstance(expression, PythonTypeRuntimeSymbol):
        return expression.qualname_parts[-1]
    return ""


def python_type_expr_arguments(expression: PythonTypeExpr) -> tuple[PythonTypeExpr, ...]:
    """Return top-level generic or union arguments."""
    if isinstance(expression, PythonTypeSubscript):
        return expression.arguments
    if isinstance(expression, PythonTypeUnion):
        return expression.items
    return ()


def iter_python_type_expr_qualified_names(expression: PythonTypeExpr) -> Iterator[str]:
    """Yield dotted syntactic and runtime names in stable traversal order."""
    return _iter_names(expression, qualified=True)


def rewrite_python_type_expr(
    expression: PythonTypeExpr,
    transform_leaf: Callable[[PythonTypeExpr], PythonTypeExpr],
) -> PythonTypeExpr:
    """Rewrite leaves while sharing every unchanged immutable subtree."""
    match expression:
        case PythonTypeModelField():
            model = rewrite_python_type_expr(expression.model, transform_leaf)
            return (
                expression
                if model is expression.model
                else PythonTypeModelField(model, expression.field_name, expression.arguments, expression.pydantic)
            )
        case PythonTypeSubscript():
            base = rewrite_python_type_expr(expression.base, transform_leaf)
            arguments = tuple(rewrite_python_type_expr(item, transform_leaf) for item in expression.arguments)
            return (
                expression
                if base is expression.base and _unchanged(arguments, expression.arguments)
                else PythonTypeSubscript(base, arguments)
            )
        case PythonTypeUnion() | PythonTypeParameterList() | PythonTypeTuple():
            items = tuple(rewrite_python_type_expr(item, transform_leaf) for item in expression.items)
            return expression if _unchanged(items, expression.items) else type(expression)(items)
        case PythonTypeStarred():
            value = rewrite_python_type_expr(expression.value, transform_leaf)
            return expression if value is expression.value else PythonTypeStarred(value)
        case PythonTypeMember():
            owner = rewrite_python_type_expr(expression.owner, transform_leaf)
            return expression if owner is expression.owner else PythonTypeMember(owner, expression.name)
    return transform_leaf(expression)


def read_literal_enum_members(expression: PythonTypeExpr) -> PythonTypeExpr:
    """Read a dotted ``package.module.MEMBER`` value of a ``Literal`` as the member ``MEMBER`` of ``package.module``.

    A ``Literal`` holds values, so the last name is a member, such as an enum member, of what the names before it
    import: ``http.HTTPStatus.OK`` imports ``HTTPStatus`` from ``http`` and ``http.client.OK`` imports the module
    ``client`` from ``http``, as a from-import takes a class or a submodule alike. A dotted name of two parts keeps
    naming an imported symbol.
    """
    match expression:
        case PythonTypeSubscript():
            literal = python_type_expr_base_name(expression.base) == "Literal"
            arguments = tuple(
                PythonTypeMember(PythonTypeQualifiedName(argument.parts[:-1]), argument.parts[-1])
                if literal
                and isinstance(argument, PythonTypeQualifiedName)
                and len(argument.parts) >= _MODULE_MEMBER_PARTS
                else read_literal_enum_members(argument)
                for argument in expression.arguments
            )
            return (
                expression
                if _unchanged(arguments, expression.arguments)
                else PythonTypeSubscript(expression.base, arguments)
            )
        case PythonTypeUnion() | PythonTypeParameterList() | PythonTypeTuple():
            items = tuple(map(read_literal_enum_members, expression.items))
            return expression if _unchanged(items, expression.items) else type(expression)(items)
        case _:
            pass
    return expression


def iter_literal_members(expression: PythonTypeExpr) -> Iterator[PythonTypeExpr]:
    """Yield each value of a ``Literal`` that is a member of a class or module, such as an enum member."""
    match expression:
        case PythonTypeSubscript():
            literal = python_type_expr_base_name(expression.base) == "Literal"
            for argument in expression.arguments:
                if literal and isinstance(argument, PythonTypeMember | PythonTypeRuntimeSymbol):
                    yield argument
                else:
                    yield from iter_literal_members(argument)
        case PythonTypeUnion() | PythonTypeParameterList() | PythonTypeTuple():
            for item in expression.items:
                yield from iter_literal_members(item)
        case _:
            pass


__all__ = [
    "PythonTypeBoundName",
    "PythonTypeEllipsis",
    "PythonTypeExpr",
    "PythonTypeLiteralValue",
    "PythonTypeMember",
    "PythonTypeModelField",
    "PythonTypeName",
    "PythonTypeOpaqueText",
    "PythonTypeParameterList",
    "PythonTypeQualifiedName",
    "PythonTypeRuntimeSymbol",
    "PythonTypeStarred",
    "PythonTypeSubscript",
    "PythonTypeTuple",
    "PythonTypeUnion",
    "is_union_python_type_expr",
    "iter_literal_members",
    "iter_python_type_expr_names",
    "iter_python_type_expr_qualified_names",
    "parse_python_type_annotation",
    "python_type_expr_arguments",
    "python_type_expr_base_name",
    "python_type_name",
    "read_literal_enum_members",
    "render_python_type_expr",
    "rewrite_python_type_expr",
]
