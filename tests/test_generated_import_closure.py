"""Check that every stored generated module binds the names it uses.

A generated module that loads a name nothing binds fails with ``NameError`` when it is imported, when a model library
evaluates its annotations, or when one of its functions runs. This audit reads every stored expected ``.py`` file with
``ast`` and resolves each loaded name through Python's scope rules to an import, a definition, a builtin, or a module
attribute. Code that runs on import (module statements, class bodies, base classes, decorators, default values) must
load a module name after the module binds it. Annotations are deferred, as under ``from __future__ import
annotations`` and Python 3.14, but every name in them must be bound: module, class, and signature annotations,
``TypeAlias`` values, ``type`` statements, ``TypeAliasType``/``TypeVar``/``NewType`` arguments, and generic base
classes, including the forward references written as strings in them (not ``Literal`` values or ``Annotated``
metadata). Names imported only under ``if TYPE_CHECKING:`` bind deferred annotations only. Local variable annotations
are never evaluated and are skipped. Names in ``__all__`` must be bound unless the module defines ``__getattr__``.

Files the running Python cannot parse are skipped, so the allowlist is compared only for the files that were read.
"""

from __future__ import annotations

import ast
import builtins
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.generated_import_closure_allowlist import UNBOUND_NAME_GROUP_REASONS, UNBOUND_NAME_VIOLATIONS

if TYPE_CHECKING:
    from collections.abc import Sequence

EXPECTED_ROOT = Path(__file__).parent / "data" / "expected"
BUCKETS = 8
MODULE_ATTRIBUTES = frozenset({
    "__annotations__",
    "__builtins__",
    "__cached__",
    "__doc__",
    "__file__",
    "__loader__",
    "__name__",
    "__package__",
    "__path__",
    "__spec__",
})
NEWER_BUILTINS = frozenset({
    "BaseExceptionGroup",
    "ExceptionGroup",
    "ImportCycleError",
    "PythonFinalizationError",
    "frozendict",
    "sentinel",
})
ALWAYS_BOUND = frozenset(dir(builtins)) | NEWER_BUILTINS | MODULE_ATTRIBUTES
GENERIC_ATTRIBUTES = frozenset({"dict", "frozenset", "list", "set", "tuple", "type"})
TYPE_ARGUMENT_CALLS = frozenset({"NewType", "TypeAliasType", "TypeVar"})
TYPE_KEYWORDS = frozenset({"bound", "value"})
UNBOUND_FAILURE_MESSAGE = (
    "These stored generated modules load names (path::name) that nothing binds, or that the module binds only after "
    "loading them on import, so importing the module or evaluating its annotations raises NameError. Fix the "
    "generator so the module imports or defines each name in time; UNBOUND_NAME_VIOLATIONS in "
    "tests/generated_import_closure_allowlist.py only shrinks."
)
STALE_FAILURE_MESSAGE = (
    "These UNBOUND_NAME_VIOLATIONS entries no longer match an unbound name in a stored module. Remove them, so the "
    "allowlist only shrinks and stays exact."
)

Use = tuple[str, bool, "int | None", "int | None"]


@dataclass(eq=False)
class _Scope:
    """Names bound in one Python scope and the names loaded in it, with the import-time clock of each load.

    ``history`` lists, for each name, the clock ticks at which the scope binds it (``True``) or deletes it (``False``).
    ``deferred_names`` are module names that a function body binds through ``global``, which exist only once it runs.
    ``entry_deferred`` is the deferral depth at which a class body runs, so its own loads follow execution order.
    """

    kind: str
    parent: _Scope | None
    names: set[str] = field(default_factory=set)
    type_checking_names: set[str] = field(default_factory=set)
    declared_global: set[str] = field(default_factory=set)
    history: dict[str, list[tuple[int, bool]]] = field(default_factory=dict)
    deferred_names: set[str] = field(default_factory=set)
    entry_deferred: int = 0
    uses: list[Use] = field(default_factory=list)
    star_import: bool = False


def _tail_name(node: ast.AST | None) -> str | None:
    """Return the last name of a dotted expression such as ``typing.Literal``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


class _Collector(ast.NodeVisitor):
    """Record every scope of a module with its bindings and its loaded names, in execution order."""

    def __init__(self) -> None:
        self.module = _Scope("module", None)
        self.scope = self.module
        self.scopes = [self.module]
        self.in_type = False
        self.under_type_checking = False
        self.imported_as: dict[str, str] = {}
        self.clock = 0
        self.deferred = 0

    def _tick(self) -> int:
        self.clock += 1
        return self.clock

    def _head_name(self, node: ast.AST) -> str | None:
        """Return the imported name a dotted expression such as ``typing.Literal`` or ``Literal_1`` stands for."""
        name = _tail_name(node)
        return self.imported_as.get(name, name) if isinstance(node, ast.Name) else name

    def _use(self, name: str, *, in_type: bool) -> None:
        tick = self._tick()
        in_order = self.scope.kind == "class" and self.deferred == self.scope.entry_deferred
        self.scope.uses.append((name, in_type, None if self.deferred else tick, tick if in_order else None))

    def _bind(self, name: str, scope: _Scope | None = None, *, delete: bool = False) -> None:
        target = scope or self.scope
        if name in target.declared_global:
            target = self.module
        if target is self.module and self.under_type_checking:
            target.type_checking_names.add(name)
            return
        if target is self.module and self.deferred:
            if not delete:
                target.deferred_names.add(name)
            return
        target.names.add(name)
        target.history.setdefault(name, []).append((self._tick(), not delete))

    def _enter(self, kind: str) -> _Scope:
        self.scope = _Scope(kind, self.scope, entry_deferred=self.deferred)
        self.scopes.append(self.scope)
        return self.scope

    def _leave(self, scope: _Scope) -> None:
        self.scope = scope.parent or self.module

    def _visit_as(self, node: ast.AST | None, *, in_type: bool) -> None:
        if node is None:
            return
        outer, self.in_type = self.in_type, in_type
        self.visit(node)
        self.in_type = outer

    def _visit_all(self, nodes: Sequence[ast.AST | None], *, in_type: bool = False) -> None:
        for node in nodes:
            self._visit_as(node, in_type=in_type)

    def _visit_deferred(self, nodes: Sequence[ast.AST | None], *, in_type: bool = False) -> None:
        self.deferred += 1
        self._visit_all(nodes, in_type=in_type)
        self.deferred -= 1

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self._use(node.id, in_type=self.in_type)
        else:
            self._bind(node.id, delete=isinstance(node.ctx, ast.Del))

    def visit_Constant(self, node: ast.Constant) -> None:
        if not (self.in_type and isinstance(node.value, str)):
            return
        try:
            expression = ast.parse(node.value, mode="eval")
        except SyntaxError:
            self._use(repr(node.value), in_type=True)
            return
        self._visit_deferred([expression.body], in_type=True)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if not self.in_type:
            self.generic_visit(node)
            return
        self.visit(node.value)
        head = self._head_name(node.value) or ""
        value_lookup = isinstance(node.value, ast.Attribute) and not (head[:1].isupper() or head in GENERIC_ATTRIBUTES)
        if head == "Literal" or value_lookup:
            self._visit_as(node.slice, in_type=False)
        elif head == "Annotated" and isinstance(node.slice, ast.Tuple) and node.slice.elts:
            self.visit(node.slice.elts[0])
            self._visit_all(node.slice.elts[1:])
        else:
            self.visit(node.slice)

    def visit_Call(self, node: ast.Call) -> None:
        self._visit_as(node.func, in_type=False)
        type_arguments = self._head_name(node.func) in TYPE_ARGUMENT_CALLS
        for index, argument in enumerate(node.args):
            self._visit_as(argument, in_type=type_arguments and index > 0)
        for keyword in node.keywords:
            self._visit_as(keyword.value, in_type=type_arguments and keyword.arg in TYPE_KEYWORDS)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._visit_as(node.value, in_type=False)
        self._visit_all(node.targets)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Name):
            self._use(node.target.id, in_type=False)
        self._visit_as(node.value, in_type=False)
        self._visit_as(node.target, in_type=False)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        evaluated = self.scope.kind in {"module", "class"}
        if evaluated:
            self._visit_deferred([node.annotation], in_type=True)
        if node.value is None:
            if not isinstance(node.target, ast.Name):
                self._visit_as(node.target, in_type=False)
            return
        self._visit_as(node.value, in_type=evaluated and self._head_name(node.annotation) == "TypeAlias")
        self._visit_as(node.target, in_type=False)

    def _visit_for(self, node: ast.For | ast.AsyncFor) -> None:
        self._visit_as(node.iter, in_type=False)
        self._visit_as(node.target, in_type=False)
        self._visit_all(node.body)
        self._visit_all(node.orelse)

    def visit_For(self, node: ast.For) -> None:
        self._visit_for(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_for(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._bind(alias.asname or alias.name.partition(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                self.scope.star_import = True
                continue
            if alias.asname:
                self.imported_as[alias.asname] = alias.name
            self._bind(alias.asname or alias.name)

    def visit_Global(self, node: ast.Global) -> None:
        self.scope.declared_global.update(node.names)

    def visit_If(self, node: ast.If) -> None:
        self._visit_as(node.test, in_type=False)
        outer = self.under_type_checking
        self.under_type_checking = outer or _tail_name(node.test) == "TYPE_CHECKING"
        self._visit_all(node.body)
        self.under_type_checking = outer
        self._visit_all(node.orelse)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self._visit_as(node.type, in_type=False)
        if node.name:
            self._bind(node.name)
        self._visit_all(node.body)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self._bind(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self._bind(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self._bind(node.rest)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        scope = self.scope
        while scope.kind == "comprehension" and scope.parent is not None:
            scope = scope.parent
        self._bind(node.target.id, scope)

    def _bind_type_params(self, node: ast.AST) -> _Scope | None:
        type_params = getattr(node, "type_params", None)
        if not type_params:
            return None
        scope = self._enter("function")
        for type_param in type_params:
            self._bind(type_param.name)
        self._visit_deferred(
            [
                expression
                for type_param in type_params
                for expression in (getattr(type_param, "bound", None), getattr(type_param, "default_value", None))
            ],
            in_type=True,
        )
        return scope

    def _visit_arguments(self, arguments: ast.arguments, *, annotations: bool) -> list[str]:
        self._visit_all([*arguments.defaults, *arguments.kw_defaults])
        parameters = [
            *arguments.posonlyargs,
            *arguments.args,
            *([arguments.vararg] if arguments.vararg else []),
            *arguments.kwonlyargs,
            *([arguments.kwarg] if arguments.kwarg else []),
        ]
        if annotations:
            self._visit_deferred([parameter.annotation for parameter in parameters], in_type=True)
        return [parameter.arg for parameter in parameters]

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._visit_all(node.decorator_list)
        type_params = self._bind_type_params(node)
        parameters = self._visit_arguments(node.args, annotations=True)
        self._visit_deferred([node.returns], in_type=True)
        in_class = (type_params.parent if type_params else self.scope).kind == "class"
        body = self._enter("function")
        for name in (*parameters, *(["__class__"] if in_class else [])):
            self._bind(name)
        self._visit_deferred(node.body)
        self._leave(body)
        if type_params:
            self._leave(type_params)
        self._bind(node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        parameters = self._visit_arguments(node.args, annotations=False)
        body = self._enter("function")
        for name in parameters:
            self._bind(name)
        self._visit_deferred([node.body])
        self._leave(body)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_all(node.decorator_list)
        type_params = self._bind_type_params(node)
        for base in node.bases:
            if isinstance(base, ast.Subscript):
                self._visit_as(base.value, in_type=False)
                self._visit_as(base.slice, in_type=True)
            else:
                self._visit_as(base, in_type=False)
        self._visit_all([keyword.value for keyword in node.keywords])
        body = self._enter("class")
        self._visit_all(node.body)
        self._leave(body)
        if type_params:
            self._leave(type_params)
        self._bind(node.name)

    def visit_TypeAlias(self, node: ast.TypeAlias) -> None:
        self._bind(node.name.id)
        type_params = self._bind_type_params(node)
        self._visit_deferred([node.value], in_type=True)
        if type_params:
            self._leave(type_params)

    def _visit_comprehension(self, node: ast.DictComp | ast.GeneratorExp | ast.ListComp | ast.SetComp) -> None:
        self._visit_as(node.generators[0].iter, in_type=False)
        scope = self._enter("comprehension")
        for index, generator in enumerate(node.generators):
            self._visit_as(generator.target, in_type=False)
            if index:
                self._visit_as(generator.iter, in_type=False)
            self._visit_all(generator.ifs)
        self._visit_all([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt])
        self._leave(scope)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node)


def _exported_names(module: ast.Module) -> list[str]:
    """Return the names a module lists in ``__all__``."""
    exported: list[str] = []
    for statement in module.body:
        targets = statement.targets if isinstance(statement, ast.Assign) else [getattr(statement, "target", None)]
        value = getattr(statement, "value", None)
        if _tail_name(targets[0]) == "__all__" and isinstance(value, (ast.List, ast.Tuple)):
            exported.extend(
                element.value
                for element in value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
    return exported


def _bound_at(scope: _Scope, name: str, clock: float) -> bool:
    """Return whether a scope holds a name at a clock tick: its last binding or deletion before then is a binding."""
    states = [bound for tick, bound in scope.history.get(name, ()) if tick < clock]
    return bool(states) and states[-1]


def _resolves(collector: _Collector, scope: _Scope, use: Use) -> bool:
    """Return whether a loaded name is bound where Python looks it up, by the time the module loads it."""
    name, in_type, clock, local_clock = use
    module = collector.module
    current: _Scope | None = module if name in scope.declared_global else scope
    while current is not None and current is not module:
        if current is scope and current.kind == "class" and local_clock is not None and name in current.names:
            if _bound_at(current, name, local_clock):
                return True
            break
        if (current is scope or current.kind != "class") and name in current.names:
            return True
        current = current.parent
    if name in ALWAYS_BOUND or module.star_import:
        return True
    if clock is None and name in module.deferred_names:
        return True
    if name in module.history:
        return _bound_at(module, name, math.inf if clock is None else clock)
    return in_type and clock is None and name in module.type_checking_names


def unbound_names(module: ast.Module) -> set[str]:
    """Return the names a module loads that nothing binds, or that it binds only after loading them on import."""
    collector = _Collector()
    collector.visit(module)
    if "__getattr__" not in collector.module.names:
        collector.module.uses.extend((name, False, None, None) for name in _exported_names(module))
    return {use[0] for scope in collector.scopes for use in scope.uses if not _resolves(collector, scope, use)}


PEP_695 = pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax needs Python 3.12 or later")
UNBOUND_NAME_CASES = [
    pytest.param(
        "def outer():\n    n = 1\n\n    def inner():\n        nonlocal n\n        n = 2\n", set(), id="nonlocal"
    ),
    pytest.param(
        "def set_it():\n    global late\n    late = 1\n\n\ndef get_it():\n    return late\n", set(), id="global"
    ),
    pytest.param("class C:\n    x = Later\n    Later = 1\n", {"Later"}, id="class-body-loads-before-binding"),
    pytest.param("class C:\n    x = 1\n    y = x\n", set(), id="class-body-loads-after-binding"),
    pytest.param(
        "Later = 1\n\n\nclass C:\n    x = Later\n    Later = 2\n", set(), id="class-body-falls-back-to-module"
    ),
    pytest.param(
        "def f():\n    global Later\n    Later = 1\n\n\nclass C(Later): ...\n", {"Later"}, id="deferred-global-write"
    ),
    pytest.param("value = 1\ndel value\nprint(value)\n", {"value"}, id="del-removes-binding"),
    pytest.param("value = 1\ndel value\nvalue = 2\nprint(value)\n", set(), id="rebinding-after-del"),
    pytest.param("value = 1\ndel value\n\n\ndef f():\n    return value\n", {"value"}, id="deferred-load-after-del"),
    pytest.param("value = 1\n\n\ndef f():\n    global value\n    del value\n", set(), id="deferred-global-del"),
    pytest.param("def f(a):\n    del a\n", set(), id="del-in-function"),
    pytest.param("x: 'not valid(' = 1\n", {"'not valid('"}, id="unparsable-forward-reference"),
    pytest.param("def f():\n    missing.count += 1\n", {"missing"}, id="attribute-augmented-assignment"),
    pytest.param("def f():\n    missing.count: int\n", {"missing"}, id="attribute-annotation-without-value"),
    pytest.param(
        "async def f(items):\n    async for item in missing:\n        print(item)\n", {"missing"}, id="async-for"
    ),
    pytest.param("from os import *\nprint(path)\n", set(), id="star-import"),
    pytest.param(
        "def f(v):\n    match v:\n        case [first, *rest]:\n            return rest\n", set(), id="match-star"
    ),
    pytest.param(
        "def f(v):\n    match v:\n        case {'a': 1, **rest}:\n            return rest\n", set(), id="match-mapping"
    ),
    pytest.param("values = [x for x in range(3) if (last := x)]\nprint(last)\n", set(), id="walrus-in-comprehension"),
    pytest.param(
        "def f[T](x: T, y: Missing) -> T: ...\n\n\nvalue: T = None\n",
        {"Missing", "T"},
        marks=PEP_695,
        id="generic-function",
    ),
    pytest.param(
        "class C[T](list[T]):\n    def m(self, x: T, y: Missing) -> T: ...\n\n\nvalue: T = None\n",
        {"Missing", "T"},
        marks=PEP_695,
        id="generic-class",
    ),
]


@pytest.mark.allow_direct_assert("checks the scope rules of the audit on small modules")
@pytest.mark.parametrize(("source", "expected"), UNBOUND_NAME_CASES)
def test_unbound_names_follow_python_scope_rules(source: str, expected: set[str]) -> None:
    """Each construct binds or loads names the way Python scopes do."""
    assert unbound_names(ast.parse(source)) == expected


@pytest.mark.allow_direct_assert("audits stored modules against the shrink-only unbound name allowlist")
@pytest.mark.parametrize("bucket", range(BUCKETS))
def test_generated_modules_bind_the_names_they_use(bucket: int) -> None:
    """Every stored generated module binds each name it loads, except the allowlisted entries.

    Each bucket reads every BUCKETS-th stored module, so the buckets together read every module once.
    """
    found: set[str] = set()
    read: set[str] = set()
    for path in sorted(EXPECTED_ROOT.rglob("*.py"))[bucket::BUCKETS]:
        relative_path = path.relative_to(EXPECTED_ROOT).as_posix()
        try:
            module = ast.parse(path.read_bytes())
        except (SyntaxError, ValueError):
            continue
        read.add(relative_path)
        found.update(f"{relative_path}::{name}" for name in unbound_names(module))
    allowed = {
        entry for entries in UNBOUND_NAME_VIOLATIONS.values() for entry in entries if entry.partition("::")[0] in read
    }

    assert sorted(found - allowed) == [], UNBOUND_FAILURE_MESSAGE
    assert sorted(allowed - found) == [], STALE_FAILURE_MESSAGE


@pytest.mark.allow_direct_assert("checks the shape of the shrink-only unbound name allowlist")
def test_unbound_name_allowlist_names_stored_modules() -> None:
    """Allowlist groups are sorted, each has a reason, and every entry names a stored module exactly once."""
    entries = [entry for group in UNBOUND_NAME_VIOLATIONS.values() for entry in group]

    assert sorted(UNBOUND_NAME_VIOLATIONS) == sorted(UNBOUND_NAME_GROUP_REASONS)
    assert all(list(group) == sorted(group) for group in UNBOUND_NAME_VIOLATIONS.values())
    assert len(entries) == len(set(entries))
    assert [entry for entry in entries if not (EXPECTED_ROOT / entry.partition("::")[0]).is_file()] == [], (
        STALE_FAILURE_MESSAGE
    )
