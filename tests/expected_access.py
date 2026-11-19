"""Find test code that reads, copies or lists stored expected output by path, outside the ``.refs`` ledger.

Tests with equal output share one stored file, and fix runs split, merge and move stored files, so a path below
``tests/data/expected`` names whatever happens to be stored there. Test code reads or copies such a file through
``tests.conftest.stored_expected``, and lists no directory there, directly or through that helper: what a listing
finds is no test's output, so a test compares a generated tree as a whole instead.

Paths are followed symbolically, the way tests build them: through module constants, imports between test modules,
local names, loops and unpacking. Names are read through ``scripts/_source_model.py``, which resolves each read to the
bindings Python's scope rules give it and tells what each binding target takes. Parameters are not followed.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from scripts._source_model import SourceModule

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

    from scripts._source_model import Binding, Import, Reference, Scope

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
_FILE = "__file__"

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
class _Module:
    """A test module read through the source model: its path, dotted name, name reads, imports and calls."""

    path: Path
    name: str
    model: SourceModule
    loads: dict[ast.expr, Reference] = field(init=False)
    imports: dict[ast.AST, Import] = field(init=False)
    calls: list[ast.Call] = field(init=False)

    def __post_init__(self) -> None:
        self.loads = {reference.node: reference for reference in self.model.references if reference.context == "load"}
        self.imports = {item.alias: item for item in self.model.imports if item.alias is not None}
        self.calls = [node for node in ast.walk(self.model.tree) if isinstance(node, ast.Call)]

    def callee(self, node: ast.expr) -> str | None:
        """Return the name a call calls: ``f`` in ``f()`` and in ``m.f()``."""
        return self.loads[node].name if node in self.loads else getattr(node, "attr", None)

    def owner(self, node: ast.AST) -> str:
        """Return the test a node belongs to: its outermost function outside other functions, after its classes."""
        chain: list[Scope] = []
        scope: Scope | None = self.model.scope_of(node)
        while scope is not None:
            chain.append(scope)
            scope = scope.parent
        classes: list[str] = []
        for scope in reversed(chain):
            if scope.kind == "function":
                return "::".join([*classes, scope.code_name])
            if scope.kind not in {"module", "class", "type-params"}:
                break
            classes += [scope.code_name] if scope.kind == "class" else []
        return _MODULE


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
                name = ".".join(relative.with_suffix("").parts).removesuffix(".__init__")
                model = SourceModule(path.read_text(encoding="utf-8"), relative.as_posix(), name)
                self.modules[name] = _Module(relative, name, model)
        self.memo: dict[int, Value | None] = {}

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

    def value(self, node: ast.expr, module: _Module) -> Value | None:
        """Return the path an expression of a module evaluates to, or None; recursion through cycles stops at None."""
        key = id(node)
        if key not in self.memo:
            self.memo[key] = None
            self.memo[key] = self.evaluate(node, module)
        return self.memo[key]

    def evaluate(self, node: ast.expr, module: _Module) -> Value | None:  # noqa: PLR0911
        if (reference := module.loads.get(node)) is not None:
            return self.read(reference, module)
        match node:
            case ast.Constant(value=str()):
                return tuple(part for part in node.value.split("/") if part not in {"", "."})
            case ast.JoinedStr():
                return (_ANY,)
            case (
                ast.Attribute(attr="parent")
                | ast.Subscript(value=ast.Attribute(attr="parents"), slice=ast.Constant(value=int()))
            ):
                return self.ancestor(node, module)
            case ast.Subscript():
                return self.value(node.value, module)
            case ast.BinOp(op=ast.Div()):
                return self.join(self.value(node.left, module), [node.right], module)
            case ast.IfExp() | ast.BoolOp() | ast.List() | ast.Tuple() | ast.Set():
                return self.pick(self.value(item, module) for item in _alternatives(node))
            case ast.Call():
                return self.call(node, module)
            case _:
                pass
        return None

    def ancestor(self, node: ast.Attribute | ast.Subscript, module: _Module) -> Value | None:
        """Return the directory ``.parent`` or ``.parents[n]`` names, for a path from the repository root."""
        inner, drop = (node.value, 1) if isinstance(node, ast.Attribute) else (node.value.value, 1 + node.slice.value)
        value = self.value(inner, module)
        return value[:-drop] if value is not None and _rooted(value) else None

    def join(self, base: Value | None, parts: Iterable[ast.expr], module: _Module) -> Value | None:
        """Append parts to a path; a part that is not a relative path is one unknown segment."""
        if base is None:
            return None
        for part in parts:
            value = self.value(part, module)
            base = (*base, *(value if value is not None and not _rooted(value) else (_ANY,)))
        return base

    def call(self, node: ast.Call, module: _Module) -> Value | None:  # noqa: PLR0911
        """Return the path a call returns: path constructors and helpers, ``stored_expected``, and listed entries."""
        name = module.callee(node.func)
        if name in _PATH_TYPES | _PASS_THROUGH | {_SANITIZER, "join"} and not node.args:
            return None
        if name == _SANITIZER:
            value = self.value(node.args[0], module)
            return None if value is None else (_INDEXED, *(value[1:] if _rooted(value) else value))
        if name in _PATH_TYPES or name == "join":
            return self.join(self.value(node.args[0], module), node.args[1:], module)
        if name in _PASS_THROUGH:
            return self.value(node.args[0], module)
        base = self.value(node.func.value, module) if isinstance(node.func, ast.Attribute) else None
        if base is None:
            return None
        if name in _SAME_PATH:
            return base
        if name == "joinpath":
            return self.join(base, node.args, module)
        if name in _RENAMES:
            return (*base[:-1], _ANY)
        return (_ROOT, *base[1:], _ANY) if name in _LISTINGS else None

    def read(self, reference: Reference, module: _Module) -> Value | None:
        """Return the value of a name read: ``__file__``, or what its bindings take, through imports between modules."""
        resolution = module.model.resolve(reference.node)
        if reference.name == _FILE and resolution.kind == "global" and not resolution.bindings:
            return (_ROOT, *module.path.parts)
        return self.bound(list(resolution.bindings), module)

    def bound(self, bindings: list[Binding], module: _Module) -> Value | None:
        """Return the value the bindings of one name take; a parameter is not followed, it shadows the names outside.

        A name imported from another test module takes the value that module binds it to.
        """
        if any(binding.kind == "param" for binding in bindings):
            return None
        values: list[Value | None] = []
        for binding in bindings:
            imported = module.imports.get(binding.node) if binding.kind == "import-from" else None
            source = imported.source if imported is not None and imported.alias is not None else None
            if imported is not None and imported.alias is not None and (found := self.modules.get(str(source))):
                values.append(self.bound(found.model.module_scope.bindings.get(imported.alias.name, []), found))
            elif (assignment := module.model.assignment(binding.node)) is not None:
                values.append(self.value(assignment.element, module))
        return self.pick(values)

    def opens(self, node: ast.expr, module: _Module, roots: frozenset[str]) -> bool:
        """Return whether a call opens a path below the expected root; relative paths start at the root."""
        value = self.value(node, module)
        return value is not None and self.below_expected(value if _rooted(value) else (_ROOT, *value), roots)

    def accesses(self) -> Iterator[Access]:
        """Yield every read, copy and listing of a path below the expected root, outside the ledger modules.

        Reading or copying a path ``stored_expected`` resolved is how tests read stored files; listing it is still a
        listing, and the entries a listing yields are plain paths again.
        """
        for module in self.modules.values():
            if module.path in self.ledger:
                continue
            for call in module.calls:
                name = module.callee(call.func)
                candidates: list[tuple[ast.expr, frozenset[str]]] = []
                if name in _READS | _LISTINGS and isinstance(call.func, ast.Attribute):
                    candidates.append((call.func.value, _PLAIN if name in _READS else _ROOTS))
                if name in _READ_FUNCTIONS | _LISTING_FUNCTIONS and (target := _path_argument(call)) is not None:
                    candidates.append((target, _PLAIN if name in _READ_FUNCTIONS else _ROOTS))
                candidates += [(source, _PLAIN) for source in _copied(call)]
                if any(self.opens(target, module, roots) for target, roots in candidates):
                    yield Access(module.path, module.owner(call), call.lineno, ast.unparse(call))


def expected_accesses(root: Path, tests_root: Path) -> list[Access]:
    """Return every read, copy and listing of a path below ``<tests root>/data/expected`` in the modules below it."""
    return sorted(_Program(root, tests_root).accesses(), key=lambda access: (access.module, access.lineno, access.text))
