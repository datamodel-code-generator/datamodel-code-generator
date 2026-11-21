"""Check that every stored generated module binds the names it uses.

A generated module that loads a name nothing binds fails with ``NameError`` when it is imported, when a model library
evaluates its annotations, or when one of its functions runs. This audit reads every stored expected ``.py`` file
through ``scripts/_source_model.py``, which resolves each loaded name by Python's scope rules to its bindings, a
builtin, or a module attribute. Code that runs on import (module statements, class bodies, base classes, decorators,
default values) must load a module or class name after a binding of it that the import runs: a binding holds from the
end of the statement or expression that makes it (the value an assignment, loop or ``with`` evaluates for it; a
``def`` or ``class`` statement; the start of an ``except`` clause), and a deletion ends it. Annotations are deferred, as
under ``from __future__ import annotations`` and Python 3.14, but every name in them must be bound: module, class, and
signature annotations, ``TypeAlias`` values, ``type`` statements, ``TypeAliasType``/``TypeVar``/``NewType`` arguments,
and generic base classes, including the forward references written as strings in them (not ``Literal`` values,
``Annotated`` metadata, call arguments, or keys looked up on a value). Names bound only under ``if TYPE_CHECKING:``
bind deferred annotations only, and an annotation without a value binds nothing. Local variable annotations are never
evaluated and are skipped. Names in ``__all__`` must be bound unless the module defines ``__getattr__``.

Files the running Python cannot parse are skipped, so the allowlist is compared only for the files that were read.
"""

from __future__ import annotations

import ast
import operator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from scripts._source_model import (
    BUILTINS,
    CONDITIONAL_BUILTINS,
    MODULE_ATTRIBUTES,
    CompilerRejectedError,
    SourceModule,
    name_reads,
    string_type,
)
from tests.capabilities import needs
from tests.generated_import_closure_allowlist import UNBOUND_NAME_GROUP_REASONS, UNBOUND_NAME_VIOLATIONS

if TYPE_CHECKING:
    from scripts._source_model import Binding, Reference, Resolution, Scope

EXPECTED_ROOT = Path(__file__).parent / "data" / "expected"
BUCKETS = 8
GENERIC_ATTRIBUTES = frozenset({"dict", "frozenset", "list", "set", "tuple", "type"})
TYPING_MODULES = ("typing", "typing_extensions")
TYPE_ARGUMENT_CALLS = frozenset(
    f"{module}.{name}" for module in TYPING_MODULES for name in ("NewType", "TypeAliasType", "TypeVar")
)
TYPE_KEYWORDS = frozenset({"bound", "value"})
LITERALS = frozenset(f"{module}.Literal" for module in TYPING_MODULES)
ANNOTATED = frozenset(f"{module}.Annotated" for module in TYPING_MODULES)
TYPE_ALIASES = frozenset(f"{module}.TypeAlias" for module in TYPING_MODULES)
ALWAYS_BOUND = BUILTINS | frozenset(CONDITIONAL_BUILTINS) | MODULE_ATTRIBUTES
RUNTIME_KINDS = frozenset({"builtin", "star", "class-cell"})
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

Position = tuple[int, ...]


@dataclass(frozen=True)
class _Load:
    """A loaded name: where it is read (the clock of code that runs on import), and whether it is a deferred type.

    ``deferred`` is set for every load that runs after import (a function body, an annotation, a string);
    ``delayed`` for the loads that run after the code of the class body that holds them (an annotation, a string).
    """

    name: str
    line: int
    position: Position
    deferred: bool
    in_type: bool
    delayed: bool = False


@dataclass
class _TypeParts:
    """The names and the strings (with the scope each is evaluated in) that a type evaluation reads as types."""

    names: set[ast.expr] = field(default_factory=set)
    strings: list[tuple[ast.Constant, Scope]] = field(default_factory=list)


def _start(node: ast.AST) -> Position:
    return getattr(node, "lineno", 0), getattr(node, "col_offset", 0)


def _end(node: ast.AST) -> Position:
    return getattr(node, "end_lineno", None) or getattr(node, "lineno", 0), getattr(node, "end_col_offset", None) or 0


class _Reader:
    """One stored module read through the source model: its type positions, its loads and its bindings' clocks."""

    def __init__(self, model: SourceModule) -> None:
        self.model = model
        self.loads = {reference.node: reference for reference in model.references if reference.context == "load"}
        self.parts = _TypeParts()
        self.parsed: dict[ast.expr, Scope] = {}
        self.late = self._late_parts()
        for root in self._type_roots():
            self._collect(root, model.scope_of(root))

    def _late_parts(self) -> list[tuple[Position, Position, Position]]:
        """Return the parts Python evaluates after code the text writes later: (start, end, where they run after).

        A comprehension's element (or key and value) runs after its generators, and a conditional expression's body
        after its test.
        """
        parts = []
        for scope in self.model.scopes:
            if scope.kind == "comprehension":
                last = scope.node.generators[-1]
                after = _end(last.ifs[-1] if last.ifs else last.iter)
                elements = [scope.node.key, scope.node.value] if hasattr(scope.node, "key") else [scope.node.elt]
                parts += [(_start(element), _end(element), after) for element in elements]
        for node in ast.walk(self.model.tree):
            if isinstance(node, ast.IfExp):
                parts.append((_start(node.body), _end(node.body), _end(node.test)))
        return parts

    def order(self, position: Position) -> Position:
        """Return a position as the order code runs: a part that runs after later-written code sorts after it."""
        inside = [part for part in self.late if part[0] <= position <= part[1]]
        outer_first = sorted(sorted(inside, key=operator.itemgetter(1), reverse=True), key=operator.itemgetter(0))
        return (*(number for _, _, after in outer_first for number in (*after, 1)), *position)

    def _type_roots(self) -> list[ast.expr]:
        """Return the expressions a type evaluation reads: annotations that run, type aliases, bounds, generic bases."""
        model = self.model
        roots = [annotation.node for annotation in model.annotations if annotation.mode != "never"]
        roots += [
            annotation.value
            for annotation in model.annotations
            if annotation.mode != "never"
            and annotation.value is not None
            and model.origin(annotation.node) in TYPE_ALIASES
        ]
        for scope in model.scopes:
            if scope.kind == "type-alias":
                roots.append(scope.node.value)
            elif scope.kind == "type-bound":
                roots.append(scope.node)
            elif scope.kind == "class":
                roots += [base.slice for base in scope.node.bases if isinstance(base, ast.Subscript)]
        for node in ast.walk(model.tree):
            if isinstance(node, ast.Call) and self._head(node.func) in TYPE_ARGUMENT_CALLS:
                roots += node.args[1:]
                roots += [keyword.value for keyword in node.keywords if keyword.arg in TYPE_KEYWORDS]
        return roots

    def _collect(self, node: ast.expr, scope: Scope) -> None:
        """Collect the type positions of an expression read as a type in a scope: names, and strings parsed later.

        ``Literal`` values, ``Annotated`` metadata, calls and keys looked up on a value (a lowercase attribute, not a
        builtin generic, subscripted) are values, not types.
        """
        if isinstance(node, ast.Constant):
            self.parts.strings += [(node, scope)] if isinstance(node.value, str) else []
        elif node in self.loads or node in self.parsed:
            self.parts.names.add(node)
        elif isinstance(node, ast.Subscript):
            self._collect(node.value, scope)
            head = node.value.attr if isinstance(node.value, ast.Attribute) else ""
            value_lookup = bool(head) and not (head[:1].isupper() or head in GENERIC_ATTRIBUTES)
            origin = self._head(node.value)
            if origin in ANNOTATED and isinstance(node.slice, ast.Tuple) and node.slice.elts:
                self._collect(node.slice.elts[0], scope)
            elif not (origin in LITERALS or value_lookup):
                self._collect(node.slice, scope)
        elif not isinstance(node, ast.Call):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    self._collect(child, scope)

    def _origin(self, node: ast.expr) -> str | None:
        """Return the origin of a name or attribute chain of the module or of a parsed string, if it has one."""
        if node not in self.parsed and not isinstance(node, ast.Attribute):
            return self.model.origin(node)
        if isinstance(node, ast.Attribute):
            base = self._origin(node.value)
            return None if base is None else f"{base}.{node.attr}"
        resolution = self.model.lookup(self._spelled(node) or "", self.parsed[node])
        if resolution.kind == "builtin":
            return f"builtins.{self._spelled(node)}"
        origins = {binding.origin for binding in resolution.bindings}
        return origins.pop() if len(origins) == 1 else None

    def _spelled(self, node: ast.expr) -> str | None:
        """Return the last name of a name or attribute chain as written."""
        if node in self.loads:
            return self.loads[node].name
        return getattr(node, "id" if node in self.parsed else "attr", None)

    def _head(self, node: ast.expr) -> str | None:
        """Return the origin of a name or attribute chain; one without a single origin reads as its ``typing`` name."""
        if (origin := self._origin(node)) is not None:
            return origin
        spelled = self._spelled(node)
        return None if spelled is None else f"typing.{spelled}"

    def loads_of_module(self) -> list[tuple[_Load, Resolution | None]]:
        """Return every load with what it resolves to (None: a string no Python parses)."""
        found: list[tuple[_Load, Resolution | None]] = []
        for reference in self.loads.values():
            if reference.annotation in {"never", "string"}:
                continue
            delayed = bool(reference.annotation)
            deferred = delayed or reference.scope.runs_in == "function"
            in_type = reference.node in self.parts.names
            found.append((
                self._load(reference, deferred=deferred, in_type=in_type, delayed=delayed),
                self.model.resolve(reference.node),
            ))
        for string, scope in self.parts.strings:
            if (parsed := string_type(string)) is None:
                found.append((
                    _Load(repr(string.value), string.lineno, self.order(_start(string)), True, True, True),
                    None,
                ))
                continue
            names = name_reads(parsed)
            self.parsed.update(dict.fromkeys(names, scope))
            self._collect(parsed, scope)
            found += [
                (
                    _Load(name.id, string.lineno, self.order(_start(string)), True, name in self.parts.names, True),
                    self.model.lookup(name.id, scope),
                )
                for name in names
            ]
        return found

    def _load(self, reference: Reference, *, deferred: bool, in_type: bool, delayed: bool) -> _Load:
        position = self.order(_start(reference.node))
        return _Load(reference.name, reference.line, position, deferred, in_type, delayed)

    def clock(self, binding: Binding) -> Position:
        """Return the position from which a binding holds as the code that makes it runs."""
        if binding.kind in {"def", "class"}:
            return self.order(_end(binding.node))
        if binding.kind == "except":
            return self.order(_start(binding.node))
        assignment = self.model.assignment(binding.node)
        return self.order(_end(assignment.value if assignment is not None else binding.node))

    def typing_only(self, binding: Binding) -> bool:
        """Return whether only type checkers run a binding (it sits in the body of a ``TYPE_CHECKING`` test)."""
        return any(condition.kind == "type-checking" and condition.branch == "body" for condition in binding.guard)

    def later(self, binding: Binding) -> bool:
        """Return whether a binding of a name that lives at import time is made by code that runs later (``global``)."""
        return binding.scope.runs_in != "function" and self.model.scope_of(binding.node).runs_in == "function"

    def holds(self, resolution: Resolution, load: _Load) -> bool | None:
        """Return whether the scope a load resolves to holds the name for it; None where its fallback decides.

        A module or class body runs on import, so a load that is not deferred sees the last binding or deletion before
        it, in the order the code runs; a deferred load sees the last one. A class body runs its own loads in order
        wherever the class is defined, so only its annotations and strings are delayed past it. A function's name is
        held by any binding.
        """
        scope = resolution.scope
        bindings = [binding for binding in resolution.bindings if binding.kind != "annotation-only"]
        runtime = [binding for binding in bindings if not self.typing_only(binding) and not self.later(binding)]
        typing = load.deferred and load.in_type and any(self.typing_only(binding) for binding in bindings)
        if scope is None:
            return None
        if scope.kind == "module":
            if load.name in ALWAYS_BOUND or scope.star_import:
                return True
            if load.deferred and any(self.later(binding) for binding in bindings):
                return True
            return self._state(runtime, load, deferred=load.deferred) if runtime else typing
        held = (
            (bool(runtime) if load.delayed else self._state(runtime, load, deferred=False))
            if scope.kind == "class"
            else bool(runtime)
        )
        return True if held or typing else None

    def _state(self, runtime: list[Binding], load: _Load, *, deferred: bool) -> bool:
        """Return whether the last binding or deletion a load sees binds the name."""
        events = sorted((self.clock(binding), binding.kind != "del") for binding in runtime)
        states = [bound for clock, bound in events if deferred or clock < load.position]
        return bool(states) and states[-1]

    def resolves(self, load: _Load, resolution: Resolution | None) -> bool:
        """Return whether a load reads a name bound where Python looks it up, by the time the module reads it."""
        while resolution is not None:
            if resolution.kind in RUNTIME_KINDS or (resolution.kind == "global" and not resolution.bindings):
                return True
            if (held := self.holds(resolution, load)) is not None:
                return held
            resolution = resolution.fallback
        return False

    def exported(self) -> list[_Load]:
        """Return the names the module lists in an unconditional ``__all__``, unless it defines ``__getattr__``."""
        module = self.model.module_scope
        if any(not self.typing_only(binding) for binding in module.bindings.get("__getattr__", [])):
            return []
        names: list[_Load] = []
        for binding in module.bindings.get("__all__", []):
            statement = self.model.statement_at(binding.line)
            value = binding.value if binding.kind != "augassign" else getattr(statement, "value", None)
            names += [
                _Load(item.value, binding.line, (0, 0), deferred=True, in_type=False)
                for item in (value.elts if not binding.guard and isinstance(value, (ast.List, ast.Tuple)) else [])
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]
        return names


def unbound_sites(model: SourceModule) -> list[tuple[int, str]]:
    """Return the line and name of each load of a module that nothing binds, or that it binds only after loading."""
    reader = _Reader(model)
    loads = [
        *reader.loads_of_module(),
        *((load, model.lookup(load.name, model.module_scope)) for load in reader.exported()),
    ]
    return sorted({(load.line, load.name) for load, resolution in loads if not reader.resolves(load, resolution)})


def unbound_names(model: SourceModule) -> set[str]:
    """Return the names a module loads that nothing binds, or that it binds only after loading them on import."""
    return {name for _, name in unbound_sites(model)}


PEP_695 = needs("type-statements")
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
        "def f():\n    class C:\n        x = Later\n        Later = 1\n\n    return C\n",
        {"Later"},
        id="class-body-in-function-loads-before-binding",
    ),
    pytest.param(
        "def f():\n    class C:\n        Later = 1\n        x = Later\n\n    return C\n",
        set(),
        id="class-body-in-function-loads-after-binding",
    ),
    pytest.param(
        "class C:\n    def m(self, x: Later) -> None: ...\n\n    Later = 1\n",
        set(),
        id="class-body-annotation-sees-later-binding",
    ),
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
    pytest.param("values = [x for _ in [(x := 1)]]\n", set(), id="comprehension-element-runs-after-its-generators"),
    pytest.param(
        "values = [(x := 1) for _ in range(x)]\n", {"x"}, id="comprehension-generator-runs-before-its-element"
    ),
    pytest.param("value = x if (x := 1) else 0\n", set(), id="conditional-body-runs-after-its-test"),
    pytest.param("value = (x := 1) if x else 0\n", {"x"}, id="conditional-test-runs-before-its-body"),
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
    assert unbound_names(SourceModule(source)) == expected


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
            model = SourceModule(path.read_text(encoding="utf-8"), relative_path)
        except (SyntaxError, ValueError, CompilerRejectedError):
            continue
        read.add(relative_path)
        found.update(f"{relative_path}::{name}" for name in unbound_names(model))
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
