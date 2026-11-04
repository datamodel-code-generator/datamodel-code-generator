"""Lightweight immutable expressions for Python type annotations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

from datamodel_code_generator._copy_on_change import unchanged
from datamodel_code_generator._module_paths import dotted_name
from datamodel_code_generator._python_syntax import list_display, subscript_text, tuple_display
from datamodel_code_generator._target_unicode import NEWEST_TARGET, is_python_name
from datamodel_code_generator.python_literal import stable_repr

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
        if self.value != "None" and not is_python_name(self.value, NEWEST_TARGET):
            msg = f"Python type name must be one identifier: {stable_repr(self.value)}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PythonTypeQualifiedName(PythonTypeExpr):
    """A syntactic dotted name parsed from external annotation text."""

    parts: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.parts or not all(is_python_name(part, NEWEST_TARGET) for part in self.parts):
            msg = f"Python qualified type name must contain identifiers: {stable_repr(self.parts)}"
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
            or not all(is_python_name(part, NEWEST_TARGET) for part in self.qualname_parts)
            or not all(is_python_name(part, NEWEST_TARGET) for part in module_parts)
        ):
            msg = (
                f"Invalid runtime Python type identity: {stable_repr(self.module)}, {stable_repr(self.qualname_parts)}"
            )
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PythonTypeMember(PythonTypeExpr):
    """A member of a class written as a ``Literal`` value, such as ``HTTPStatus.OK``."""

    owner: PythonTypeExpr
    name: str

    def __post_init__(self) -> None:
        if not is_python_name(self.name, NEWEST_TARGET):
            msg = f"Python type member must be one identifier: {stable_repr(self.name)}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PythonTypeBoundName(PythonTypeExpr):
    """A rendered name tied to the exact import that introduced it."""

    value: str
    import_from: str | None
    import_name: str

    def __post_init__(self) -> None:
        if not is_python_name(self.value, NEWEST_TARGET) or not self.import_name:
            msg = (
                f"Invalid bound Python type name: {stable_repr(self.value)}, {stable_repr(self.import_from)}, "
                f"{stable_repr(self.import_name)}"
            )
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
            msg = f"Unsupported Python type literal value: {stable_repr(self.value)}"
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
    return dotted_name(symbol.module, ".".join(symbol.qualname_parts))


_ChildRow: TypeAlias = Literal["all", "values"]
"""The children of a node a walk visits: ``all`` of them, or the ``values``: the arguments of a subscript and the items
of a union, a parameter list or a tuple, without the base of a subscript, the model of a field path, the owner of a
member or the value of a starred expression."""


def _children(expression: PythonTypeExpr, row: _ChildRow) -> tuple[PythonTypeExpr, ...] | None:
    """Return the expressions a node holds in ``row``, in written order; None for a node that holds none there."""
    match expression:
        case PythonTypeSubscript():
            return expression.arguments if row == "values" else (expression.base, *expression.arguments)
        case PythonTypeUnion() | PythonTypeParameterList() | PythonTypeTuple():
            return expression.items
        case PythonTypeModelField() if row == "all":
            return (expression.model,)
        case PythonTypeMember() if row == "all":
            return (expression.owner,)
        case PythonTypeStarred() if row == "all":
            return (expression.value,)
    return None


def _with_children(
    expression: PythonTypeExpr,
    row: _ChildRow,
    children: tuple[PythonTypeExpr, ...],
    rewritten: tuple[PythonTypeExpr, ...],
) -> PythonTypeExpr:
    """Return the node holding ``rewritten`` in place of its ``children`` in ``row``, itself where none changed.

    A starred expression is the one node with children that no case names.
    """
    if unchanged(rewritten, children):
        return expression
    match expression:
        case PythonTypeSubscript():
            base, arguments = (expression.base, rewritten) if row == "values" else (rewritten[0], rewritten[1:])
            return PythonTypeSubscript(base, arguments)
        case PythonTypeUnion() | PythonTypeParameterList() | PythonTypeTuple():
            return type(expression)(rewritten)
        case PythonTypeModelField():
            return PythonTypeModelField(rewritten[0], expression.field_name, expression.arguments, expression.pydantic)
        case PythonTypeMember():
            return PythonTypeMember(rewritten[0], expression.name)
    return PythonTypeStarred(rewritten[0])


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
            result = f"{render_python_type_expr(expression.model)}.{member}[{stable_repr(expression.field_name)}]"
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
            return stable_repr(expression.value)
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


def is_literal_python_type_expr(expression: PythonTypeExpr) -> bool:
    """Return whether an expression subscripts ``Literal``, by its base's last name."""
    return isinstance(expression, PythonTypeSubscript) and python_type_expr_base_name(expression) == "Literal"


_NameForm: TypeAlias = Literal["terminal", "qualified", "head"]
"""What a walk yields of a dotted name: its last name, all of it (a bare name yields nothing) or its first name."""


def _iter_names(expression: PythonTypeExpr, *, form: _NameForm) -> Iterator[str]:
    match expression:
        case PythonTypeName() | PythonTypeBoundName():
            if form != "qualified":
                yield expression.value
        case PythonTypeQualifiedName() | PythonTypeRuntimeSymbol():
            dotted = (
                ".".join(expression.parts)
                if isinstance(expression, PythonTypeQualifiedName)
                else _runtime_symbol_name(expression)
            )
            match form:
                case "qualified":
                    yield dotted
                case "head":
                    yield dotted.partition(".")[0]
                case _:
                    yield dotted.rpartition(".")[2]
        case _:
            for child in _children(expression, "all") or ():
                yield from _iter_names(child, form=form)


def iter_python_type_expr_names(expression: PythonTypeExpr) -> Iterator[str]:
    """Yield semantic leaf names in stable structure order."""
    return _iter_names(expression, form="terminal")


def iter_python_type_expr_head_names(expression: PythonTypeExpr) -> Iterator[str]:
    """Yield the name each leaf binds where the expression is written: ``ipaddress`` of ``ipaddress.IPv4Address``."""
    return _iter_names(expression, form="head")


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
    return _iter_names(expression, form="qualified")


def rewrite_python_type_expr(
    expression: PythonTypeExpr,
    transform_leaf: Callable[[PythonTypeExpr], PythonTypeExpr],
) -> PythonTypeExpr:
    """Rewrite leaves while sharing every unchanged immutable subtree."""
    if (children := _children(expression, "all")) is None:
        return transform_leaf(expression)
    return _with_children(
        expression, "all", children, tuple(rewrite_python_type_expr(child, transform_leaf) for child in children)
    )


def _literal_member(name: PythonTypeQualifiedName, names_class: Callable[[str], bool]) -> PythonTypeMember | None:
    """Return the member a dotted ``Literal`` value names, or None when it names an imported symbol."""
    if len(name.parts) >= _MODULE_MEMBER_PARTS:
        return PythonTypeMember(PythonTypeQualifiedName(name.parts[:-1]), name.parts[-1])
    head, member = name.parts
    return PythonTypeMember(python_type_name(head), member) if names_class(head) else None


def read_literal_enum_members(expression: PythonTypeExpr, names_class: Callable[[str], bool]) -> PythonTypeExpr:
    """Read a dotted ``package.module.MEMBER`` value of a ``Literal`` as the member ``MEMBER`` of ``package.module``.

    A ``Literal`` holds values, so the last name is a member, such as an enum member, of what the names before it
    import: ``http.HTTPStatus.OK`` imports ``HTTPStatus`` from ``http`` and ``http.client.OK`` imports the module
    ``client`` from ``http``, as a from-import takes a class or a submodule alike. A dotted name of two parts is a
    member of its first name where ``names_class`` tells that the name binds a class, such as ``Color.RED``, and keeps
    naming an imported symbol, such as ``datetime.MINYEAR``, otherwise.
    """
    if (children := _children(expression, "values")) is None:
        return expression
    literal = is_literal_python_type_expr(expression)
    return _with_children(
        expression,
        "values",
        children,
        tuple(
            member
            if literal
            and isinstance(child, PythonTypeQualifiedName)
            and (member := _literal_member(child, names_class)) is not None
            else read_literal_enum_members(child, names_class)
            for child in children
        ),
    )


def iter_literal_members(expression: PythonTypeExpr) -> Iterator[PythonTypeExpr]:
    """Yield each value of a ``Literal`` that is a member of a class or module, such as an enum member."""
    literal = is_literal_python_type_expr(expression)
    for child in _children(expression, "values") or ():
        if literal and isinstance(child, PythonTypeMember | PythonTypeRuntimeSymbol):
            yield child
        else:
            yield from iter_literal_members(child)


def _flat_literal_values(arguments: tuple[PythonTypeExpr, ...]) -> Iterator[PythonTypeExpr]:
    """Yield the values of a ``Literal``, those of a ``Literal`` it nests included."""
    for argument in arguments:
        if isinstance(argument, PythonTypeSubscript) and is_literal_python_type_expr(argument):
            yield from _flat_literal_values(argument.arguments)
        else:
            yield argument


def write_refused_literal_values(
    expression: PythonTypeExpr, refused: tuple[type, ...]
) -> tuple[PythonTypeExpr, tuple[PythonTypeLiteralValue, ...]]:
    """Write each value of a ``Literal`` whose exact type is in ``refused`` as the type, and return the values.

    A ``Literal`` of such values alone becomes the union of their types (``Literal[5.0]`` the type ``float``); one that
    holds other values keeps them (``Literal[1, 5.0]`` becomes ``Literal[1] | float``), and a nested ``Literal`` is
    flattened. An expression that holds no such value is returned as it is.
    """
    values: list[PythonTypeLiteralValue] = []

    def write(node: PythonTypeExpr) -> PythonTypeExpr:
        if isinstance(node, PythonTypeSubscript) and is_literal_python_type_expr(node):
            members = tuple(_flat_literal_values(node.arguments))
            written = [m for m in members if isinstance(m, PythonTypeLiteralValue) and type(m.value) in refused]
            if not written:
                return node
            values.extend(written)
            kept = tuple(m for m in members if m not in written)
            items = (
                *((PythonTypeSubscript(node.base, kept),) if kept else ()),
                *dict.fromkeys(python_type_name(type(m.value).__name__) for m in written),
            )
            return items[0] if len(items) == 1 else PythonTypeUnion(items)
        if (children := _children(node, "all")) is None:
            return node
        return _with_children(node, "all", children, tuple(map(write, children)))

    return write(expression), tuple(values)


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
    "is_literal_python_type_expr",
    "is_union_python_type_expr",
    "iter_literal_members",
    "iter_python_type_expr_head_names",
    "iter_python_type_expr_names",
    "iter_python_type_expr_qualified_names",
    "parse_python_type_annotation",
    "python_type_expr_arguments",
    "python_type_expr_base_name",
    "python_type_name",
    "read_literal_enum_members",
    "render_python_type_expr",
    "rewrite_python_type_expr",
    "write_refused_literal_values",
]
