"""Find test code that reads, copies or lists stored expected output by path, outside the ``.refs`` ledger.

Tests with equal output share one stored file, and fix runs split, merge and move stored files, so a path below
``tests/data/expected`` names whatever happens to be stored there. Test code reads or copies such a file through
``tests.conftest.stored_expected``, and lists no directory there, directly or through that helper: what a listing
finds is no test's output, so a test compares a generated tree as a whole instead.

Paths are followed symbolically, the way tests build them: through module constants, imports between test modules,
local names, loops and unpacking. Parameters are not followed. This module uses the standard library only.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

__all__ = ["LEDGER", "Access", "expected_accesses"]

LEDGER = ("conftest.py", "expected_access.py", "units.py")
"""The modules at the tests root that read stored expected output for every test: the ledger and this module."""
_READS = frozenset({"copy", "copy_into", "open", "read_bytes", "read_text"})
_READ_FUNCTIONS = frozenset({"copy", "copy2", "copyfile", "copytree", "open"})
_LISTINGS = frozenset({"glob", "iterdir", "rglob", "walk"})
_LISTING_FUNCTIONS = frozenset({"glob", "iglob", "listdir", "scandir", "walk"})
_PATH_KEYWORDS = frozenset({"file", "path", "pathname", "src", "top"})
_PATH_TYPES = frozenset({"Path", "PurePath", "PurePosixPath", "PureWindowsPath"})
_PASS_THROUGH = frozenset({"fspath", "list", "reversed", "sorted", "str", "tuple"})
_SAME_PATH = frozenset({"absolute", "expanduser", "resolve"})
_RENAMES = frozenset({"with_name", "with_stem", "with_suffix"})
_SANITIZER = "stored_expected"
_ROOT = "/"
_INDEXED = "="
_ROOTS = frozenset({_ROOT, _INDEXED})
_PLAIN = frozenset({_ROOT})
_ANY = "*"
_MODULE = "<module>"

Value = tuple[str, ...]
"""A path: ``/`` (``=`` once ``stored_expected`` resolved it) and the segments from the repository root, or relative
segments; ``*`` is an unknown segment."""


@dataclass(frozen=True)
class Access:
    """A read, copy or listing of a path below the expected root, and the function (or ``<module>``) it happens in."""

    module: Path
    owner: str
    lineno: int
    text: str


@dataclass(eq=False)
class _Scope:
    """A module or function: the names it binds and its parameters, and the test function it belongs to."""

    module: _Module
    parent: _Scope | None
    owner: str
    bindings: dict[str, list[ast.expr]] = field(default_factory=dict)
    params: set[str] = field(default_factory=set)


@dataclass(eq=False)
class _Module:
    """A test module: its path, dotted name, imports and calls."""

    path: Path
    name: str
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)
    calls: list[tuple[ast.Call, _Scope]] = field(default_factory=list)
    scope: _Scope = field(init=False)


def _name(node: ast.expr) -> str | None:
    """Return the name a call calls: ``f`` in ``f()`` and in ``m.f()``."""
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None


def _copied(call: ast.Call) -> list[ast.expr]:
    """Return the sources of the ``copy_files=[(source, destination), ...]`` pairs the shared run helpers copy."""
    pairs = next((keyword.value for keyword in call.keywords if keyword.arg == "copy_files"), None)
    return [
        pair.elts[0]
        for pair in (pairs.elts if isinstance(pairs, ast.List | ast.Tuple) else [])
        if isinstance(pair, ast.Tuple) and pair.elts
    ]


def _rooted(value: Value) -> bool:
    """Return whether a path starts at the repository root."""
    return value[:1] in {(_ROOT,), (_INDEXED,)}


def _path_argument(call: ast.Call) -> ast.expr | None:
    """Return the path a read, copy or listing function receives first, positionally or by its keyword."""
    if call.args:
        return call.args[0]
    return next((keyword.value for keyword in call.keywords if keyword.arg in _PATH_KEYWORDS), None)


def _alternatives(node: ast.IfExp | ast.BoolOp | ast.List | ast.Tuple | ast.Set) -> list[ast.expr]:
    """Return the expressions a conditional, a boolean operation or a collection may evaluate to."""
    match node:
        case ast.IfExp():
            return [node.body, node.orelse]
        case ast.BoolOp():
            return node.values
        case _:
            pass
    return node.elts


class _Collector(ast.NodeVisitor):
    """Build a module's scopes, the names each binds, and its calls."""

    def __init__(self, module: _Module) -> None:
        self.module = module
        self.scope = module.scope = _Scope(module, None, _MODULE)
        self.classes: list[str] = []

    def bind(self, target: ast.expr, value: ast.expr) -> None:
        """Bind the names of a target to the value they take; unpacked names take the matching element."""
        match target:
            case ast.Name():
                self.scope.bindings.setdefault(target.id, []).append(value)
            case ast.Tuple() | ast.List():
                elements = value.elts if isinstance(value, ast.Tuple | ast.List) else []
                for index, item in enumerate(target.elts):
                    self.bind(item, elements[index] if len(elements) == len(target.elts) else value)
            case ast.Starred():
                self.bind(target.value, value)
            case _:
                pass

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Record what each imported name names; a relative import starts at the module's package."""
        package = self.module.name.split(".")[: None if self.module.path.name == "__init__.py" else -1]
        base = package[: len(package) - node.level + 1] if node.level else []
        source = ".".join([*base, *([node.module] if node.module else [])])
        for alias in node.names:
            self.module.imports[alias.asname or alias.name] = (source, alias.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for child in (*node.bases, *node.keywords, *node.decorator_list):
            self.visit(child)
        self.classes.append(node.name)
        for child in node.body:
            self.visit(child)
        self.classes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> None:
        name = getattr(node, "name", None)
        for child in (*getattr(node, "decorator_list", ()), *node.args.defaults, *node.args.kw_defaults):
            if child is not None:
                self.visit(child)
        top = self.scope.owner == _MODULE and name is not None
        owner = "::".join([*self.classes, str(name)]) if top else self.scope.owner
        scope = _Scope(self.module, self.scope, owner)
        arguments = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs, node.args.vararg, node.args.kwarg)
        scope.params = {argument.arg for argument in arguments if argument is not None}
        outer, self.scope = self.scope, scope
        for child in node.body if isinstance(node.body, list) else [node.body]:
            self.visit(child)
        self.scope = outer

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit_FunctionDef(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self.bind(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.bind(node.target, node.value)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.bind(node.target, node.value)
        self.generic_visit(node)

    def visit_For(self, node: ast.For | ast.AsyncFor | ast.comprehension) -> None:
        self.bind(node.target, node.iter)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit_For(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.visit_For(node)

    def visit_withitem(self, node: ast.withitem) -> None:
        if node.optional_vars is not None:
            self.bind(node.optional_vars, node.context_expr)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.module.calls.append((node, self.scope))
        self.generic_visit(node)


class _Program:
    """Every module below a tests root, and the paths their expressions evaluate to."""

    def __init__(self, root: Path, tests_root: Path) -> None:
        base = tests_root.relative_to(root).parts
        self.expected = (*base, "data", "expected")
        self.ledger = {tests_root.relative_to(root) / name for name in LEDGER}
        self.modules: dict[str, _Module] = {}
        for path in sorted(tests_root.rglob("*.py")):
            relative = path.relative_to(root)
            if relative.parts[len(base)] != "data":
                module = _Module(relative, ".".join(relative.with_suffix("").parts).removesuffix(".__init__"))
                _Collector(module).visit(ast.parse(path.read_bytes()))
                self.modules[module.name] = module
        self.memo: dict[tuple[int, int], Value | None] = {}

    def below_expected(self, value: Value | None, roots: frozenset[str] = _ROOTS) -> bool:
        """Return whether a path from one of the given roots lies below the expected root."""
        return (
            value is not None
            and value[:1] in {(root,) for root in roots}
            and value[1:][: len(self.expected)] == self.expected
        )

    def pick(self, values: Iterable[Value | None]) -> Value | None:
        """Return a value below the expected root when one is, else the first path."""
        found = [value for value in values if value is not None]
        return next((value for value in found if self.below_expected(value)), found[0] if found else None)

    def value(self, node: ast.expr, scope: _Scope) -> Value | None:
        """Return the path an expression evaluates to in a scope, or None; recursion through cycles stops at None."""
        key = (id(node), id(scope))
        if key not in self.memo:
            self.memo[key] = None
            self.memo[key] = self.evaluate(node, scope)
        return self.memo[key]

    def evaluate(self, node: ast.expr, scope: _Scope) -> Value | None:  # noqa: PLR0911
        match node:
            case ast.Constant(value=str()):
                return tuple(part for part in node.value.split("/") if part not in {"", "."})
            case ast.JoinedStr():
                return (_ANY,)
            case ast.Name(id="__file__"):
                return (_ROOT, *scope.module.path.parts)
            case ast.Name():
                return self.lookup(node.id, scope)
            case (
                ast.Attribute(attr="parent")
                | ast.Subscript(value=ast.Attribute(attr="parents"), slice=ast.Constant(value=int()))
            ):
                return self.ancestor(node, scope)
            case ast.Subscript():
                return self.value(node.value, scope)
            case ast.BinOp(op=ast.Div()):
                return self.join(self.value(node.left, scope), [node.right], scope)
            case ast.IfExp() | ast.BoolOp() | ast.List() | ast.Tuple() | ast.Set():
                return self.pick(self.value(item, scope) for item in _alternatives(node))
            case ast.Call():
                return self.call(node, scope)
            case _:
                pass
        return None

    def ancestor(self, node: ast.Attribute | ast.Subscript, scope: _Scope) -> Value | None:
        """Return the directory ``.parent`` or ``.parents[n]`` names, for a path from the repository root."""
        inner, drop = (node.value, 1) if isinstance(node, ast.Attribute) else (node.value.value, 1 + node.slice.value)
        value = self.value(inner, scope)
        return value[:-drop] if value is not None and _rooted(value) else None

    def join(self, base: Value | None, parts: Iterable[ast.expr], scope: _Scope) -> Value | None:
        """Append parts to a path; a part that is not a relative path is one unknown segment."""
        if base is None:
            return None
        for part in parts:
            value = self.value(part, scope)
            base = (*base, *(value if value is not None and not _rooted(value) else (_ANY,)))
        return base

    def call(self, node: ast.Call, scope: _Scope) -> Value | None:  # noqa: PLR0911
        """Return the path a call returns: path constructors and helpers, ``stored_expected``, and listed entries."""
        name = _name(node.func)
        if name in _PATH_TYPES | _PASS_THROUGH | {_SANITIZER, "join"} and not node.args:
            return None
        if name == _SANITIZER:
            value = self.value(node.args[0], scope)
            return None if value is None else (_INDEXED, *(value[1:] if _rooted(value) else value))
        if name in _PATH_TYPES or name == "join":
            return self.join(self.value(node.args[0], scope), node.args[1:], scope)
        if name in _PASS_THROUGH:
            return self.value(node.args[0], scope)
        base = self.value(node.func.value, scope) if isinstance(node.func, ast.Attribute) else None
        if base is None:
            return None
        if name in _SAME_PATH:
            return base
        if name == "joinpath":
            return self.join(base, node.args, scope)
        if name in _RENAMES:
            return (*base[:-1], _ANY)
        return (_ROOT, *base[1:], _ANY) if name in _LISTINGS else None

    def lookup(self, name: str, scope: _Scope) -> Value | None:
        """Return the value of a name bound in an enclosing scope or imported from another test module.

        A parameter is not followed: it shadows the names outside its function.
        """
        current: _Scope | None = scope
        while current is not None:
            if name in current.params:
                return None
            if name in current.bindings:
                return self.pick(self.value(expression, current) for expression in current.bindings[name])
            current = current.parent
        if (imported := scope.module.imports.get(name)) is not None and (module := self.modules.get(imported[0])):
            return self.lookup(imported[1], module.scope)
        return None

    def opens(self, node: ast.expr, scope: _Scope, roots: frozenset[str]) -> bool:
        """Return whether a call opens a path below the expected root; relative paths start at the root."""
        value = self.value(node, scope)
        return value is not None and self.below_expected(value if _rooted(value) else (_ROOT, *value), roots)

    def accesses(self) -> Iterator[Access]:
        """Yield every read, copy and listing of a path below the expected root, outside the ledger modules.

        Reading or copying a path ``stored_expected`` resolved is how tests read stored files; listing it is still a
        listing, and the entries a listing yields are plain paths again.
        """
        for module in self.modules.values():
            if module.path in self.ledger:
                continue
            for call, scope in module.calls:
                name = _name(call.func)
                candidates: list[tuple[ast.expr, frozenset[str]]] = []
                if name in _READS | _LISTINGS and isinstance(call.func, ast.Attribute):
                    candidates.append((call.func.value, _PLAIN if name in _READS else _ROOTS))
                if name in _READ_FUNCTIONS | _LISTING_FUNCTIONS and (target := _path_argument(call)) is not None:
                    candidates.append((target, _PLAIN if name in _READ_FUNCTIONS else _ROOTS))
                candidates += [(source, _PLAIN) for source in _copied(call)]
                if any(self.opens(target, scope, roots) for target, roots in candidates):
                    yield Access(module.path, scope.owner, call.lineno, ast.unparse(call))


def expected_accesses(root: Path, tests_root: Path) -> list[Access]:
    """Return every read, copy and listing of a path below ``<tests root>/data/expected`` in the modules below it."""
    return sorted(_Program(root, tests_root).accesses(), key=lambda access: (access.module, access.lineno, access.text))
