"""The names a model's rendered text loads, and their rewriting through the bindings of its module.

``DataModel.render`` writes a field annotation or default that names an import its module binds under another name
through that name (``_bind_field_expressions``), and ``DataModel._render`` and the module rendering write every bare
load of a builtin the module imports under an alias through the alias (``_bind_builtin_names``); the pass that aliases
those builtins asks which of them a text loads bare (``_bare_loads_of``), and so does the pass that aliases the module
imports named like a builtin, by its own row. Each parses the rendered text as the target writes it
(``host_parsable``, ``_parsed``); the two binders rewrite name nodes at the offsets the parse gives. A model's own
fields are the annotated statements of the class body of its name (``_class_bodies``), and the names an expression
names its name nodes (``_names_of``). Nothing here reads a model.
"""

from __future__ import annotations

import ast
from itertools import accumulate
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, cast

from datamodel_code_generator._target_unicode import host_parsable

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator, Mapping


def _parsed(rendered: str) -> tuple[ast.Module, Callable[[str], str]]:
    """Return the tree of ``rendered`` at its offsets, as the running interpreter parses it, and how to restore a name.

    Identifier characters the running interpreter does not know are lent letters of the same UTF-8 length and a type
    statement is parsed as an assignment of the same length (`host_parsable`).
    """
    host_source, restore_name = host_parsable(rendered)
    return ast.parse(host_source), restore_name


def _class_bodies(tree: ast.Module, restore_name: Callable[[str], str], class_name: str) -> Iterator[list[ast.stmt]]:
    """Yield the body of each class statement of ``tree`` whose restored name is ``class_name``, in order."""
    return (
        statement.body
        for statement in tree.body
        if isinstance(statement, ast.ClassDef) and restore_name(statement.name) == class_name
    )


def _names_of(tree: ast.AST, *, loads_only: bool = False) -> Iterator[ast.Name]:
    """Yield the name nodes of ``tree`` in ``ast.walk`` order: every one, or only those it loads."""
    return (
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and (not loads_only or isinstance(node.ctx, ast.Load))
    )


def _expression_names(expression: ast.AST, restore_name: Callable[[str], str]) -> set[str]:
    """Return the restored names of every name node of ``expression``, whatever its context."""
    return {restore_name(node.id) for node in _names_of(expression)}


def _rewrite_names(rendered: str, replacements: Iterable[tuple[ast.Name, str]]) -> str:
    """Write each name node parsed from ``rendered`` as its replacement, at the UTF-8 offsets ``ast`` gives."""
    source = rendered.encode()
    offsets = [0, *accumulate(map(len, source.splitlines(keepends=True)))]
    spans = [
        (
            offsets[node.lineno - 1] + node.col_offset,
            offsets[cast("int", node.end_lineno) - 1] + cast("int", node.end_col_offset),
            alias,
        )
        for node, alias in replacements
    ]
    for start, end, alias in sorted(spans, reverse=True):
        source = source[:start] + alias.encode() + source[end:]
    return source.decode()


def _bind_field_expressions(rendered: str, class_name: str, bindings: Mapping[str, str]) -> str:
    """Bind names in field annotations and defaults without changing template objects.

    The rendered text is parsed at its own offsets (`_parsed`), so the name nodes rewrite in place.
    """
    tree, restore_name = _parsed(rendered)
    return _rewrite_names(
        rendered,
        [
            (node, alias)
            for body in _class_bodies(tree, restore_name, class_name)
            for field in body
            if isinstance(field, ast.AnnAssign)
            for expression in ((field.annotation,) if field.value is None else (field.annotation, field.value))
            for node in _names_of(expression, loads_only=True)
            if (alias := bindings.get(restore_name(node.id))) is not None
        ],
    )


_NAME_SCOPES: Final = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _local_names(scope: ast.AST) -> set[str]:
    """Return the names a function, lambda or comprehension binds itself: its parameters and its assigned names."""
    arguments = getattr(scope, "args", None)
    parameters = (
        (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs, arguments.vararg, arguments.kwarg)
        if isinstance(arguments, ast.arguments)
        else ()
    )
    return {parameter.arg for parameter in parameters if parameter is not None} | {
        node.id for node in ast.walk(scope) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }


def _bare_loads(tree: ast.AST, bound: frozenset[str] = frozenset()) -> Iterator[ast.Name]:
    """Yield the names a tree loads bare: loaded, not the module of an attribute (``name.X``), not bound locally."""
    match tree:
        case ast.Name(ctx=ast.Load()):
            if tree.id not in bound:
                yield tree
        case ast.Attribute(value=ast.Name()):
            return
        case _:
            if isinstance(tree, _NAME_SCOPES):
                bound |= _local_names(tree)
            for child in ast.iter_child_nodes(tree):
                yield from _bare_loads(child, bound)


_BareLoadRule: TypeAlias = Literal["scoped", "legacy-flat-bare-loads"]
"""Which names of a text `_bare_loads_of` counts.

`scoped` the names it loads that no function, lambda or comprehension of it binds itself, the loads
`_bind_builtin_names` rewrites; `legacy-flat-bare-loads` every name node, the stores and the names a function of a
custom template binds included, as the check of module imports named like a builtin counts them since 0.87.0.
"""


def _flat_names(tree: ast.AST) -> Iterator[ast.Name]:
    """Yield every name node of a tree that is not the module of an attribute (``name.X``), whatever its context."""
    attribute_values = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    yield from (node for node in _names_of(tree) if id(node) not in attribute_values)


def _bare_loads_of(rendered: str, names: Collection[str], *, rule: _BareLoadRule = "scoped") -> set[str]:
    """Return the names of ``names`` that ``rendered`` loads bare by the row."""
    tree, restore_name = _parsed(rendered)
    nodes = _bare_loads(tree) if rule == "scoped" else _flat_names(tree)
    return {name for node in nodes if (name := restore_name(node.id)) in names}


def _bind_builtin_names(rendered: str, bindings: Mapping[str, str]) -> str:
    """Write every bare load of a bound builtin in ``rendered`` through its alias, e.g. ``list_aliased[str]``."""
    tree, restore_name = _parsed(rendered)
    return _rewrite_names(
        rendered,
        [(node, alias) for node in _bare_loads(tree) if (alias := bindings.get(restore_name(node.id))) is not None],
    )
