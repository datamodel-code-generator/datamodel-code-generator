"""The names a model's rendered text loads, and their rewriting through the bindings of its module.

``DataModel.render`` writes a field annotation or default that names an import its module binds under another name
through that name (``_bind_field_expressions``), and ``DataModel._render`` and the module rendering write every bare
load of a builtin the module imports under an alias through the alias (``_bind_builtin_names``); the pass that aliases
those builtins asks which of them a text loads bare (``_bare_loads_of``). Each parses the rendered text as the target
writes it (``host_parsable``); the two binders rewrite name nodes at the offsets the parse gives. Nothing here reads
a model.
"""

from __future__ import annotations

import ast
from itertools import accumulate
from typing import TYPE_CHECKING, Final, cast

from datamodel_code_generator._target_unicode import host_parsable

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator, Mapping


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

    Identifier characters the running interpreter does not know are lent letters of the same UTF-8 length and a type
    statement is parsed as an assignment of the same length (`host_parsable`), so the offsets it parses hold for the
    rendered text.
    """
    replacements: list[tuple[ast.Name, str]] = []
    host_source, restore_name = host_parsable(rendered)
    for statement in ast.parse(host_source).body:
        if not isinstance(statement, ast.ClassDef) or restore_name(statement.name) != class_name:
            continue
        for field in statement.body:
            if not isinstance(field, ast.AnnAssign):
                continue
            expressions = (field.annotation,) if field.value is None else (field.annotation, field.value)
            replacements.extend(
                (node, alias)
                for expression in expressions
                for node in ast.walk(expression)
                if isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and (alias := bindings.get(restore_name(node.id))) is not None
            )
    return _rewrite_names(rendered, replacements)


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


def _bare_loads_of(rendered: str, names: Collection[str]) -> set[str]:
    """Return the names of ``names`` that ``rendered`` loads bare, the loads ``_bind_builtin_names`` rewrites."""
    host_source, restore_name = host_parsable(rendered)
    return {name for node in _bare_loads(ast.parse(host_source)) if (name := restore_name(node.id)) in names}


def _bind_builtin_names(rendered: str, bindings: Mapping[str, str]) -> str:
    """Write every bare load of a bound builtin in ``rendered`` through its alias, e.g. ``list_aliased[str]``."""
    host_source, restore_name = host_parsable(rendered)
    return _rewrite_names(
        rendered,
        [
            (node, alias)
            for node in _bare_loads(ast.parse(host_source))
            if (alias := bindings.get(restore_name(node.id))) is not None
        ],
    )
