"""The public API record of a package, written from its source through the source model (`scripts/_source_model.py`).

What is public:

- a module whose dotted path has no part with a single leading underscore (``__main__`` is public);
- in it, every name its run-time statements bind without a leading underscore, except imports outside the typing
  spec's re-export forms (``import X as X``, ``from M import X as X``, ``from . import X``); every name of its literal
  ``__all__``; every key of each lazy table it passes to ``_lazy_exports.module_hooks``;
- in a class, every member without a leading underscore or a dunder, in source order (the field and constructor
  order), and the public attributes its ``__init__`` stores on ``self``.

Each public name is followed through imports, aliases and lazy entries to its definitions, and each definition is
written once, at its home: the module and name its ``__module__`` gives when that public name reaches it, else the
first public name in sorted order that reaches it; every other name writes ``Name = <home>``. A class or function
whose ``__module__`` and name differ from its home writes ``# identity <module>.<name>``. Every binding carries the
guard of the branches it sits in (``[if <test>]``, ``[else of <test>]``, ``[try]``, ``[except <E>]``,
``[case <pattern>]``, ``[built by <f>()]``, ``[lazy]``), whichever branch the interpreter takes; what only type
checkers run is left out. Decorators, bases, signatures and annotations are written as source. Values are written in
full, except a module-level value or a class attribute that is not a field (a ``TypeAlias`` aside), whose lists,
tuples, sets and dicts of more than `ITEMS` items are written as their size; a constant that a written value reads and
no public name reaches is written under it (``uses``). A lazy entry writes its expression, or its loader's body; a
``(module path, attribute)`` pair is followed to the attribute. A module's own ``def __getattr__`` and
``def __dir__`` are written under its header like a loader: the signature, the statements one level deep and the
constants they read in full.
A base class no public name reaches is written in a last section, under the first public subclass that reaches it.

The record is a function of the source text alone: nothing of the package is imported, so no installed library,
platform or import order reaches it, and it has the same bytes on every Python (expressions are written by
`ast.unparse`, f-strings and lambdas by their folded source, the two forms it writes differently by version).
What the record cannot read in a public module (a public name bound by a loop, a dynamic namespace write at module
level, a lazy table or an ``__all__`` that is no literal, a name nothing binds, a hook bound by anything but ``def`` or
``module_hooks``) is a `Refusal` with its module and line.

Known limits, where the record stays silent although the behaviour can change:

- what a private decorator or helper does is not read: a change inside ``_records.record`` that flips a default of
  the classes it decorates does not move the record, only a change to its call (``@record(eq=False)``) does;
- a function a hook or a loader calls is read by its call only (one level deep), not by its body;
- a module-level ``setattr(Class, "x", value)`` is not read, while ``Class.x = value`` is.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from scripts import _source_model as source_model

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

PACKAGE = "datamodel_code_generator"
ITEMS = 16
"""The largest list, tuple, set or dict a collapsed value writes item by item."""

_IMPORTS = frozenset({"import", "import-from"})
_VALUES = frozenset({"assign", "annassign", "augassign", "match", "walrus", "type-alias"})
_UNBOUND = frozenset({"except"})
_DEFINITIONS = frozenset({"def", "class"}) | _VALUES
_CLASS_VARS = frozenset({"typing.ClassVar", "typing_extensions.ClassVar"})
_TYPE_ALIASES = frozenset({"typing.TypeAlias", "typing_extensions.TypeAlias"})
_UNPACK = frozenset({"typing.Unpack", "typing_extensions.Unpack"})
_MUTATORS = ("append", "extend", "insert", "remove", "pop", "clear", "sort", "reverse", "update", "add", "discard")
_ALL_WRITES = frozenset({"subscript-store", "subscript-del", *(f"call:{method}" for method in _MUTATORS)})
_VALUE_WRITES = _ALL_WRITES | {"attribute-store", "attribute-del", "call:setdefault", "call:popitem"}
_PAIR = 2
_HOOKS = frozenset({"__getattr__", "__dir__"})
_EXCEPT = {"body": "try", "else": "try else", "finally": "finally"}

Key = tuple[str, str, int]
"""A definition: its module, the name it binds and its line."""


class RecordError(Exception):
    """The source holds forms the record cannot read; the message lists each with its module and line."""


@dataclass(frozen=True)
class Refusal:
    """A form of a public module the record cannot read, and where it stands."""

    module: str
    line: int
    reason: str

    def __str__(self) -> str:
        """Render as ``<module>:<line>: <reason>``."""
        return f"{self.module}:{self.line}: {self.reason}"


@dataclass(frozen=True)
class Entry:
    """The lines a public name of a module writes, and the line of the module that makes the name public."""

    module: str
    name: str
    line: int
    lines: list[str]


@dataclass
class ModuleFacts:
    """What the record reads of one public module, for the run-time check.

    ``bound`` holds every name its run-time statements bind without a leading underscore (imports included), its
    ``__all__`` names and its lazy keys; ``always`` those bound whichever branch runs; ``identities`` the
    ``(__module__, name)`` of each class or function a public name leads to.
    """

    bound: set[str] = field(default_factory=set)
    always: set[str] = field(default_factory=set)
    lazy: dict[str, ast.expr] = field(default_factory=dict)
    all_names: list[str] | None = None
    computed_all: bool = False
    identities: dict[str, set[tuple[str, str]]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Target:
    """Where a public name leads under a guard: a definition, a module, an outside name or a lazy value."""

    kind: str
    guard: tuple[str, ...]
    dotted: str = ""
    definition: source_model.Definition | None = None
    module: str = ""
    node: ast.AST | None = None


@dataclass(frozen=True)
class _LateWrite:
    """An attribute a module-level statement stores on a definition after it: ``X.attr = value``.

    ``identity`` is the module a ``__module__`` store names.
    """

    attribute: str
    value: ast.expr
    module: str
    identity: str = ""


def is_public_module(name: str) -> bool:
    """Whether a dotted module name has no part with a single leading underscore below its top-level package."""
    return not any(part.startswith("_") and not _dunder(part) for part in name.split(".")[1:])


def _dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _public_member(name: str) -> bool:
    return not name.startswith("_") or _dunder(name)


def _typing_only(guard: source_model.Guard) -> bool:
    return any(condition.kind == "type-checking" and condition.branch == "body" for condition in guard)


def _raises(condition: source_model.Condition) -> bool:
    """Whether the branch of a condition ends in ``raise``."""
    node = condition.node
    body = getattr(node, "orelse" if condition.branch == "else" else "body", None)
    if condition.branch == "finally" or not isinstance(body, list) or not body:
        return False
    return isinstance(body[-1], ast.Raise)


def _executable(body: Iterable[ast.stmt]) -> list[ast.stmt]:
    """Return a body's statements but its docstring and bare constants (``...``); a call statement stays."""
    return [
        statement
        for statement in body
        if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
    ]


def _suffix(guard: Iterable[str]) -> str:
    listed = "; ".join(guard)
    return f"  [{listed}]" if listed else ""


class _Writer(ast.NodeTransformer):
    """Write f-strings and lambdas as their folded source and, collapsing, large containers as their size."""

    def __init__(self, model: source_model.SourceModule, lambdas: set[int], *, collapse: bool) -> None:
        self.model = model
        self.lambdas = lambdas
        self.collapse = collapse

    def visit(self, node: ast.AST) -> ast.AST:
        return ast.Name(id=self.model.source(node)) if id(node) in self.lambdas else super().visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> ast.AST:
        return ast.Name(id=self.model.source(node))

    def _sized(self, node: ast.expr, items: int, kind: str) -> ast.AST:
        return ast.Name(id=f"<{kind} of {items}>") if self.collapse and items > ITEMS else self.generic_visit(node)

    def visit_List(self, node: ast.List) -> ast.AST:
        return self._sized(node, len(node.elts), "list")

    def visit_Tuple(self, node: ast.Tuple) -> ast.AST:
        return self._sized(node, len(node.elts), "tuple")

    def visit_Set(self, node: ast.Set) -> ast.AST:
        return self._sized(node, len(node.elts), "set")

    def visit_Dict(self, node: ast.Dict) -> ast.AST:
        return self._sized(node, len(node.keys), "dict")


def _literal_names(node: ast.expr | None) -> list[str] | None:
    """Return the strings of a literal list or tuple of strings, else None."""
    items = node.elts if isinstance(node, (ast.List, ast.Tuple)) else None
    if items is None or not all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in items):
        return None
    return [item.value for item in items if isinstance(item, ast.Constant)]


class PublicApi:
    """The public API record of a package under ``root``: its entries, its refusals, and the facts of each module."""

    def __init__(self, root: Path, package: str = PACKAGE) -> None:
        """Read every module of the package and write the record."""
        self.root = root
        self.package = package
        self.tree = source_model.SourceTree(root, [package])
        self.refusals: list[Refusal] = []
        self._lambda_nodes: dict[str, frozenset[int]] = {}
        self._load_nodes: dict[str, dict[int, source_model.Reference]] = {}
        self._annotation_nodes: dict[str, dict[int, ast.expr]] = {}
        self._tables: dict[str, dict[str, ast.expr]] = {}
        self.public = sorted(name for name in self.tree.paths if is_public_module(name))
        self.facts = {module: self._facts(module) for module in self.public}
        self.names = {module: self._public_names(module) for module in self.public}
        self.targets = {
            (module, name): self._targets(module, name) for module in self.public for name in self.names[module]
        }
        self.late = self._late_writes()
        self.homes = self._homes()
        self._bases: list[tuple[Key, str, str]] = []
        self._expanding = True
        self._written: set[Key] = set()
        self.header: dict[str, list[str]] = {module: self._header(module) for module in self.public}
        self.entries = [self._entry(module, name) for module in self.public for name in self.names[module]]
        self._expanding = False
        self.base_lines = self._base_section()
        for module in self.public:
            self._identities(module)

    def text(self) -> str:
        """Return the record: per public module its header and entries, then the definitions only they reach.

        Raise `RecordError` listing every refusal instead.
        """
        if self.refusals:
            raise RecordError("\n".join(map(str, self.refusals)))
        lines = [f"# The public API of {self.package}, written from its source by tests/main/public_api_record.py."]
        by_module: dict[str, list[Entry]] = {}
        for entry in self.entries:
            by_module.setdefault(entry.module, []).append(entry)
        for module in self.public:
            lines += ["", f"[{module}]", *self.header[module]]
            lines += [line for entry in by_module.get(module, []) for line in entry.lines]
        lines += ["", "[definitions no public name reaches]", *self.base_lines] if self.base_lines else []
        return "\n".join(lines) + "\n"

    def place(self, module: str) -> str:
        """Return the path of a module's file relative to the package directory."""
        return self.tree.paths[module].relative_to(self.root / self.package).as_posix()

    # ---- what is read of each module once, per record: a record keeps no tree alive after it ----------------------
    def _text(self, model: source_model.SourceModule, node: ast.AST, *, collapse: bool = False) -> str:
        """Write a node of a module's tree as `ast.unparse` does, with the forms of `_Writer` replaced."""
        copies: dict[int, ast.AST] = {}
        copied = copy.deepcopy(node, copies)
        lambdas = {id(copies[key]) for key in self._lambdas(model) if key in copies}
        return ast.unparse(_Writer(model, lambdas, collapse=collapse).visit(copied))

    def _lambdas(self, model: source_model.SourceModule) -> frozenset[int]:
        """Return the nodes of a module's lambdas: the scopes of kind ``lambda``."""
        if model.module not in self._lambda_nodes:
            self._lambda_nodes[model.module] = frozenset(
                id(scope.node) for scope in model.scopes if scope.kind == "lambda"
            )
        return self._lambda_nodes[model.module]

    def _loads(self, model: source_model.SourceModule) -> dict[int, source_model.Reference]:
        """Return the name loads of a module by node."""
        if model.module not in self._load_nodes:
            self._load_nodes[model.module] = {
                id(reference.node): reference for reference in model.references if reference.context == "load"
            }
        return self._load_nodes[model.module]

    def _annotations(self, model: source_model.SourceModule) -> dict[int, ast.expr]:
        """Return the annotation of each annotated target of a module, by the node of the target."""
        if model.module not in self._annotation_nodes:
            self._annotation_nodes[model.module] = {
                id(annotation.target): annotation.node for annotation in model.annotations if annotation.target
            }
        return self._annotation_nodes[model.module]

    # ---- what each module binds ----------------------------------------------------------------------------------
    def _refuse(self, module: str, line: int, reason: str) -> None:
        self.refusals.append(Refusal(module, line, reason))

    def _bindings(self, model: source_model.SourceModule, scope: source_model.Scope) -> list[source_model.Binding]:
        """Return the bindings of a scope that are left after it runs, in source order.

        An annotation without a value binds nothing outside a class body (where it declares a field), an
        ``except ... as`` name is deleted at the end of its handler,
        and a branch that ends in ``raise`` leaves nothing it binds.
        """
        found = (
            binding
            for binding in model.bindings(scope=scope)
            if not _typing_only(binding.guard)
            and binding.kind not in _UNBOUND
            and (binding.kind != "annotation-only" or scope.kind == "class")
            and not any(map(_raises, binding.guard))
        )
        return sorted(found, key=lambda binding: binding.line)

    def _facts(self, module: str) -> ModuleFacts:
        model = self.tree.module(module)
        facts = ModuleFacts()
        bindings = self._bindings(model, model.module_scope)
        for site in model.dynamic_sites:
            if site.scope.runs_in == "module" and not _typing_only(site.guard):
                self._refuse(module, site.line, f"a dynamic namespace write ({site.kind}) the record cannot read")
        facts.all_names = self._all(module, [binding for binding in bindings if binding.name == "__all__"])
        for binding in bindings:
            if binding.kind == "def" and binding.name == "__getattr__":
                facts.computed_all = any(
                    isinstance(node, ast.Constant) and node.value == "__all__" for node in ast.walk(binding.node)
                )
        facts.lazy = self._lazy(module)
        facts.bound = {
            binding.name
            for binding in bindings
            if not binding.name.startswith("_") and not (binding.origin or "").startswith("__future__.")
        }
        facts.bound |= {name for name in [*(facts.all_names or ()), *facts.lazy] if not name.startswith("_")}
        facts.always = {name for name in facts.bound if self._always(model, name)} | set(facts.lazy)
        return facts

    def _all(self, module: str, bindings: list[source_model.Binding]) -> list[str] | None:
        """Return the names of a literal ``__all__`` and the literal ``+=`` after it; None where there is none."""
        model = self.tree.module(module)
        names: list[str] | None = None
        written = {(use.reference.line, use.kind) for binding in bindings for use in model.uses(binding)}
        for line, kind in sorted(write for write in written if write[1] in _ALL_WRITES):
            self._refuse(module, line, f"__all__ is changed by {kind}")
        for binding in bindings:
            augmented = binding.kind == "augassign"
            value = getattr(model.statement_at(binding.line), "value", None) if augmented else binding.value
            listed = _literal_names(value)
            if listed is None:
                self._refuse(module, binding.line, "an __all__ that is no literal list or tuple of strings")
                continue
            names = [*((names or []) if augmented else ()), *listed]
        return names

    def _lazy(self, module: str) -> dict[str, ast.expr]:
        """Return the lazy entries of a module: those of the tables its module-level ``module_hooks`` calls serve."""
        if module not in self._tables:
            model = self.tree.module(module)
            self._tables[module] = {
                name: entry
                for binding in self._bindings(model, model.module_scope)
                for name, entry in self._lazy_table(module, binding).items()
            }
        return self._tables[module]

    def _hooks_call(self, model: source_model.SourceModule, binding: source_model.Binding) -> ast.Call | None:
        """Return the ``_lazy_exports.module_hooks`` call a binding takes its value from, else None."""
        assignment = model.assignment(binding.node)
        call = assignment.value if assignment is not None else None
        hooks = model.origin(call.func) if isinstance(call, ast.Call) else None
        helper, _, function = (hooks or "").rpartition(".")
        known = (
            helper in self.tree.paths and helper.rpartition(".")[2] == "_lazy_exports" and function == "module_hooks"
        )
        return call if known and isinstance(call, ast.Call) else None

    def _lazy_table(self, module: str, binding: source_model.Binding) -> dict[str, ast.expr]:
        """Return the table a module-level ``module_hooks`` call that a binding takes serves: a literal dict."""
        model = self.tree.module(module)
        call = self._hooks_call(model, binding)
        if call is None or binding.name != "__dir__":
            return {}
        table = source_model.argument(call, 1, "table")
        reached = self._reach(origin) if table is not None and (origin := model.origin(table)) is not None else []
        definitions = reached if isinstance(reached, list) else []
        single = len(definitions) == 1 and definitions[0].kind == "assign"
        value = self._binding(definitions[0]).value if single else None
        if not isinstance(value, ast.Dict) or not all(
            isinstance(key, ast.Constant) and isinstance(key.value, str) for key in value.keys
        ):
            self._refuse(module, binding.line, "a lazy table that is no literal dict with string keys")
            return {}
        return {
            key.value: entry
            for key, entry in zip(value.keys, value.values, strict=True)
            if isinstance(key, ast.Constant)
        }

    def _always(self, model: source_model.SourceModule, name: str) -> bool:
        """Whether a name is bound whichever branch runs: once without a guard, or on both arms of one test."""
        guards = [
            [condition for condition in binding.guard if condition.kind != "type-checking"]
            for binding in self._bindings(model, model.module_scope)
            if binding.name == name and binding.kind != "del"
        ]
        if any(not guard for guard in guards):
            return True
        single = [guard[0] for guard in guards if len(guard) == 1]
        statements = {id(condition.node): condition for condition in single if condition.branch == "body"}
        return any(
            id(condition.node) in statements
            or any(isinstance(other.node, ast.Try) and condition.node in other.node.handlers for other in single)
            for condition in single
            if condition.branch in {"else", "except"}
        )

    def _public_names(self, module: str) -> list[str]:
        model = self.tree.module(module)
        facts = self.facts[module]
        names = {
            binding.name
            for binding in self._bindings(model, model.module_scope)
            if not binding.name.startswith("_") and (binding.kind not in _IMPORTS or binding.reexport_form)
        }
        return sorted(names | set(facts.all_names or ()) | set(facts.lazy))

    # ---- where each name leads -----------------------------------------------------------------------------------
    def _guard(self, model: source_model.SourceModule, guard: source_model.Guard, skip: int = 0) -> list[str]:
        """Write the conditions of a guard after the first ``skip``; a ``TYPE_CHECKING`` run-time branch is none."""
        written = []
        for condition in guard[skip:]:
            node = condition.node
            if condition.kind == "type-checking":
                continue
            if condition.kind == "function":
                written.append(f"built by {getattr(node, 'name', '')}()")
            elif isinstance(node, ast.match_case):
                case = f" if {self._text(model, node.guard)}" if node.guard else ""
                written.append(f"case {self._text(model, node.pattern)}{case}")
            elif condition.branch == "except":
                handled = getattr(node, "type", None)
                written.append(f"except {self._text(model, handled)}" if handled is not None else "except")
            elif isinstance(node, (ast.Try, ast.TryStar)):
                written.append(_EXCEPT[condition.branch])
            elif (test := getattr(node, "test", None)) is not None:
                written.append(
                    f"if {self._text(model, test)}"
                    if condition.branch == "body"
                    else f"else of {self._text(model, test)}"
                )
            else:
                written.append(f"{condition.kind} {condition.branch}")
        return written

    def _path_guard(self, definition: source_model.Definition, prefix: tuple[str, ...] = ()) -> tuple[str, ...]:
        hops = [item for module, guard in definition.path for item in self._guard(self.tree.module(module), guard)]
        return (*prefix, *hops)

    def _reach(self, dotted: str) -> list[source_model.Definition] | str:
        """Return the definitions a dotted name of the tree leads to, or the dotted name where it leaves the tree."""
        parts = dotted.split(".")
        count = next((count for count in range(len(parts), 0, -1) if ".".join(parts[:count]) in self.tree.paths), 0)
        module, rest = ".".join(parts[:count]), parts[count:]
        if count and len(rest) == 1:
            return self.tree.definition(module, rest[0])
        return [source_model.Definition(module, "", 0, "module", ())] if count and not rest else dotted

    def _targets(self, module: str, name: str) -> list[_Target]:
        model = self.tree.module(module)
        found: list[_Target] = []
        refused = len(self.refusals)
        if name in model.module_scope.bindings or name not in self.facts[module].lazy:
            found += self._convert(module, name, self.tree.definition(module, name))
        if (entry := self.facts[module].lazy.get(name)) is not None:
            found += self._lazy_targets(module, name, entry)
        if not found and len(self.refusals) == refused:
            self._refuse(module, self._line(module, name), f"{name} leads to nothing that runs")
        return found

    def _convert(
        self, module: str, name: str, definitions: list[source_model.Definition] | str, prefix: tuple[str, ...] = ()
    ) -> list[_Target]:
        """Turn the definitions a public name leads to into targets; refuse those the record cannot write."""
        if isinstance(definitions, str):
            return [_Target("outside", prefix, dotted=definitions)]
        found = []
        line = self._line(module, name)
        for definition in definitions:
            if any(_typing_only(guard) for _, guard in definition.path) or definition.kind == "annotation-only":
                continue
            guard = self._path_guard(definition, prefix)
            dotted = ".".join(part for part in (definition.module, definition.name) if part)
            kind = self._read_kind(definition)
            if kind in {"module", "outside", "attribute"}:
                found.append(_Target(kind, guard, dotted=dotted))
            elif kind in _DEFINITIONS:
                found.append(_Target("definition", guard, definition=replace(definition, kind=kind)))
            elif kind == "unbound":
                owner, _, attribute = definition.module.rpartition(".")
                if (entry := self._lazy(owner).get(attribute)) is None:
                    self._refuse(module, line, f"{name} leads to {dotted}, which nothing binds")
                else:
                    found += self._lazy_targets(owner, attribute, entry, guard)
            else:
                self._refuse(module, line, f"{name} leads to {dotted}, bound by {kind}")
        return found

    @staticmethod
    def _lazy_dotted(entry: ast.expr, name: str) -> str | None:
        """Return the dotted name a module path or a ``(module path, attribute)`` pair entry names, else None."""
        if isinstance(entry, ast.Constant) and isinstance(entry.value, str):
            return f"{entry.value}.{name}"
        items = entry.elts if isinstance(entry, ast.Tuple) else []
        pair = [item.value for item in items if isinstance(item, ast.Constant) and isinstance(item.value, str)]
        return ".".join(pair) if len(items) == len(pair) == _PAIR else None

    def _lazy_targets(self, module: str, name: str, entry: ast.expr, prefix: tuple[str, ...] = ()) -> list[_Target]:
        """Follow a lazy entry: a module path, a ``(module path, attribute)`` pair, a loader, a name or a lambda."""
        prefix = (*prefix, "lazy")
        line = entry.lineno
        if (dotted := self._lazy_dotted(entry, name)) is not None:
            return self._convert(module, name, self._reach(dotted), prefix)
        model = self.tree.module(module)
        if id(entry) in self._lambdas(model):
            return [_Target("lazy", prefix, module=module, node=getattr(entry, "body", None))]
        reached = self._reach(origin) if (origin := model.origin(entry)) is not None else []
        definitions = reached if isinstance(reached, list) else []
        loaders = [definition for definition in definitions if definition.kind == "def"]
        if isinstance(reached, str) or (definitions and not loaders):
            return self._convert(module, name, reached, prefix)
        if len(loaders) != 1 or len(definitions) != 1:
            self._refuse(module, line, f"the lazy entry of {name} is no module path, loader, name or lambda")
            return []
        loader = self._binding(loaders[0])
        returned = self._returned_import(self.tree.module(loaders[0].module), loader.node)
        if returned is not None:
            return self._convert(module, name, self._reach(returned), prefix)
        return [_Target("loader", prefix, module=loaders[0].module, node=loader.node)]

    def _read_kind(self, definition: source_model.Definition) -> str:
        """Return a definition's kind; an assignment of an expression `origin` cannot follow is a value."""
        if definition.kind != "unreadable":
            return definition.kind
        bound = self.tree.module(definition.module).module_scope.bindings.get(definition.name, [])
        kinds = [binding.kind for binding in bound if binding.line == definition.line and binding.kind in _VALUES]
        return kinds[0] if kinds else definition.kind

    @staticmethod
    def _returned_import(model: source_model.SourceModule, node: ast.AST) -> str | None:
        """Return what a loader imports and returns (``from m import x as y; return y``), else None."""
        body = _executable(getattr(node, "body", []))
        bound = model.bindings(scope=model.scope_defined_by(node))
        if len(body) != 2 or len(bound) != 1 or bound[0].kind != "import-from" or bound[0].line != body[0].lineno:
            return None
        returned = body[1].value if isinstance(body[1], ast.Return) else None
        return bound[0].origin if returned is not None and model.origin(returned) == bound[0].origin else None

    def _line(self, module: str, name: str) -> int:
        """Return the line of a module that makes a name public: its first binding, its lazy key or its ``__all__``."""
        model = self.tree.module(module)
        bound = self._bindings(model, model.module_scope)
        lines = [binding.line for binding in bound if binding.name == name]
        lazy = self.facts[module].lazy.get(name)
        lines += [lazy.lineno] if lazy is not None else []
        lines += [binding.line for binding in bound if binding.name == "__all__"]
        return lines[0] if lines else 0

    # ---- homes and identities ------------------------------------------------------------------------------------
    def _late_writes(self) -> dict[Key, list[_LateWrite]]:
        """Return the attributes module-level statements of any module store on the definitions they name."""
        found: dict[Key, list[_LateWrite]] = {}
        for module in self.tree.paths:
            model = self.tree.module(module)
            for reference in model.references:
                if reference.scope.runs_in != "module" or model.use_of(reference.node) != "attribute-store":
                    continue
                statement = model.statement_at(reference.line)
                targets = getattr(statement, "targets", [])
                target = targets[0] if len(targets) == 1 else None
                if not isinstance(target, ast.Attribute) or target.value is not reference.node:
                    continue
                value = getattr(statement, "value", target)
                write = _LateWrite(target.attr, value, module)
                if target.attr == "__module__":
                    write = replace(write, identity=self._module_named(model, value))
                for dotted in self._receivers(model, reference.node):
                    reached = self._reach(dotted)
                    for definition in reached if isinstance(reached, list) else []:
                        key = (definition.module, definition.name, definition.line)
                        found.setdefault(key, []).append(write)
        return found

    def _module_named(self, model: source_model.SourceModule, value: ast.expr) -> str:
        """Return the module a ``__module__`` store names: ``__name__``, a string, or a name bound once to a string."""
        load = self._loads(model).get(id(value))
        if load is not None and load.name == "__name__" and not model.resolve(load.node).bindings:
            return model.module
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        reached = self._reach(origin) if (origin := model.origin(value)) is not None else []
        named = [
            self.tree.module(definition.module).constant(definition.name)
            for definition in (reached if isinstance(reached, list) else [])
        ]
        if len(named) == 1 and isinstance(named[0], str):
            return named[0]
        self._refuse(model.module, value.lineno, f"a __module__ the record cannot read: {self._text(model, value)}")
        return model.module

    def _receivers(self, model: source_model.SourceModule, node: ast.expr) -> list[str]:
        """Return what a receiver names: its origin, or each item of the tuple a ``for`` over names gives it."""
        resolution = model.resolve(self._loads(model)[id(node)].node)
        loops = [binding for binding in resolution.bindings if binding.kind == "for"]
        if not loops:
            return [origin] if (origin := model.origin(node)) is not None else []
        assignment = model.assignment(loops[0].node)
        items = assignment.value.elts if assignment is not None and isinstance(assignment.value, ast.Tuple) else []
        return [origin for item in items if (origin := model.origin(item)) is not None]

    def _identity(self, key: Key) -> tuple[str, str]:
        """Return the ``__module__`` and name of a definition: from a late ``__module__`` store, else its own module."""
        module, name, _ = key
        named = [write.identity for write in self.late.get(key, []) if write.attribute == "__module__"]
        return (named[-1] if named else module), name

    def _homes(self) -> dict[Key, tuple[str, str]]:
        reach: dict[Key, list[tuple[str, str]]] = {}
        for (module, name), targets in sorted(self.targets.items()):
            for target in targets:
                if target.definition is not None:
                    definition = target.definition
                    reach.setdefault((definition.module, definition.name, definition.line), []).append((module, name))
        return {key: home if (home := self._identity(key)) in found else found[0] for key, found in reach.items()}

    def _identities(self, module: str) -> None:
        facts = self.facts[module]
        for name in self.names[module]:
            for target in self.targets[module, name]:
                definition = target.definition
                if definition is not None and definition.kind in {"def", "class"}:
                    key = (definition.module, definition.name, definition.line)
                    facts.identities.setdefault(name, set()).add(self._identity(key))

    # ---- writing -------------------------------------------------------------------------------------------------
    def _header(self, module: str) -> list[str]:
        facts = self.facts[module]
        lines = []
        if self.tree.paths[module].name == "__init__.py":
            children = [name.rpartition(".")[2] for name in self.public if name.rpartition(".")[0] == module]
            lines += [f"submodules: {', '.join(children)}"] if children else []
        if facts.all_names is not None:
            lines += ["__all__ = [", *(f"    {name!r}," for name in facts.all_names), "]"]
        elif facts.computed_all:
            lines.append("__all__ = computed by __getattr__")
        return lines + self._hooks(module)

    def _hooks(self, module: str) -> list[str]:
        """Write the ``__getattr__`` and ``__dir__`` a public module defines itself, as loader bodies are written.

        Each is its signature, its statements one level deep and the constants they read (``uses``); one that is
        bound by anything but ``def`` or a `module_hooks` call is refused.
        """
        model = self.tree.module(module)
        lines = []
        for binding in self._bindings(model, model.module_scope):
            if binding.name not in _HOOKS:
                continue
            if binding.kind != "def":
                if self._hooks_call(model, binding) is None:
                    reason = f"{binding.name} is bound by {binding.kind}, not by def or module_hooks"
                    self._refuse(module, binding.line, reason)
                continue
            label = f"{module}.{binding.name}"
            body = _executable(binding.node.body)
            lines += self._function(
                model, binding.node, binding.name, "", _suffix(self._guard(model, binding.guard)), label
            )
            lines += self._loader_body(model, binding.node, label)
            lines += self._uses(model, body, "    ", collapse=False)
        return lines

    def _entry(self, module: str, name: str) -> Entry:
        lines: list[str] = []
        for target in self.targets[module, name]:
            suffix = _suffix(target.guard)
            if target.kind == "module":
                lines.append(f"{name} = module {target.dotted}{suffix}")
            elif target.kind in {"outside", "attribute"}:
                lines.append(f"{name} = {target.dotted}{suffix}")
            elif target.kind == "lazy":
                model = self.tree.module(target.module)
                assert isinstance(target.node, ast.expr)
                lines.append(f"{name} = lazy {self._text(model, target.node)}{suffix}")
                lines += self._uses(model, [target.node], "    ")
            elif target.kind == "loader":
                model = self.tree.module(target.module)
                assert target.node is not None
                lines.append(f"{name} = lazy {getattr(target.node, 'name', '')}(){suffix}")
                lines += self._loader_body(model, target.node, f"{target.module}.{name}")
            else:
                assert target.definition is not None
                lines += self._definition(module, name, target.definition, suffix)
        return Entry(module, name, self._line(module, name), lines)

    def _loader_body(self, model: source_model.SourceModule, node: ast.AST, label: str) -> list[str]:
        """Write a loader's statements, docstring aside: a class or function it defines as such, others as source."""
        bound = model.bindings(scope=model.scope_defined_by(node))
        defined = {id(binding.node): binding for binding in bound if binding.kind in {"def", "class"}}
        lines = []
        executable = _executable(getattr(node, "body", []))
        for statement in getattr(node, "body", []):
            binding = defined.get(id(statement))
            if binding is not None and binding.kind == "class":
                lines += self._class(model, binding, binding.name, "    | ", "", label)
            elif binding is not None:
                lines += self._function(model, statement, binding.name, "    | ", "", label)
            elif statement in executable:
                lines += [f"    | {line}" for line in self._text(model, statement).splitlines()]
        return lines

    def _binding(self, definition: source_model.Definition) -> source_model.Binding:
        model = self.tree.module(definition.module)
        return next(
            binding
            for binding in model.module_scope.bindings[definition.name]
            if binding.line == definition.line and binding.kind == definition.kind
        )

    def _definition(self, module: str, name: str, definition: source_model.Definition, suffix: str) -> list[str]:
        key = (definition.module, definition.name, definition.line)
        home = self.homes[key]
        if home != (module, name):
            return [f"{name} = {home[0]}.{home[1]}{suffix}"]
        self._written.add(key)
        return self._with_identity(self._write(definition, name, suffix, f"{module}.{name}"), definition, home)

    def _with_identity(self, lines: list[str], definition: source_model.Definition, home: tuple[str, str]) -> list[str]:
        """Add ``# identity`` under a class or function line whose ``__module__`` and name differ from its home."""
        identity = self._identity((definition.module, definition.name, definition.line))
        if definition.kind in {"def", "class"} and identity != home:
            decorators = sum(line.startswith("@") for line in lines)
            lines.insert(decorators + 1, f"    # identity {identity[0]}.{identity[1]}")
        return lines

    def _write(self, definition: source_model.Definition, name: str, suffix: str, label: str) -> list[str]:
        model = self.tree.module(definition.module)
        binding = self._binding(definition)
        if binding.kind == "def":
            lines = self._function(model, binding.node, name, "", suffix, label)
        elif binding.kind == "class":
            lines = self._class(model, binding, name, "", suffix, label)
        else:
            lines = self._value(model, binding, name, "", suffix, collapse=True)
            lines += [
                f"    then {self._text(model, statement)}"
                for use in model.uses(binding)
                if use.kind in _VALUE_WRITES and use.reference.scope.runs_in == "module"
                for statement in [model.statement_at(use.reference.line)]
                if statement is not None
            ]
        for write in self.late.get((definition.module, definition.name, definition.line), []):
            if write.attribute != "__module__":
                written = self._text(self.tree.module(write.module), write.value)
                lines.append(f"    {write.attribute} = {written}  [set in {write.module}]")
        return lines

    def _function(
        self,
        model: source_model.SourceModule,
        node: ast.AST,
        name: str,
        indent: str,
        suffix: str,
        label: str,
    ) -> list[str]:
        lines = [f"{indent}@{self._text(model, decorator)}" for decorator in node.decorator_list]
        keyword = "async def" if isinstance(node, ast.stmt) and model.clauses(node)[0].keyword == "async def" else "def"
        returns = f" -> {self._text(model, node.returns)}" if node.returns is not None else ""
        lines.append(f"{indent}{keyword} {name}({self._text(model, node.args)}){returns}{suffix}")
        defaults = [*node.args.defaults, *(default for default in node.args.kw_defaults if default is not None)]
        kwargs = node.args.kwarg.annotation if node.args.kwarg is not None else None
        unpacked = kwargs.slice if isinstance(kwargs, ast.Subscript) and model.origin(kwargs.value) in _UNPACK else None
        for annotation in (unpacked, node.returns) if self._expanding else ():
            for expression in model.type_expressions(annotation) if annotation is not None else ():
                self._queue(model.origin(expression), "named by the signature of", label)
        return lines + self._uses(model, defaults, f"{indent}    ")

    def _class(
        self,
        model: source_model.SourceModule,
        binding: source_model.Binding,
        name: str,
        indent: str,
        suffix: str,
        label: str,
    ) -> list[str]:
        node = binding.node
        scope = model.scope_defined_by(node)
        lines = [f"{indent}@{self._text(model, decorator)}" for decorator in node.decorator_list]
        bases = ", ".join(self._text(model, item) for item in [*node.bases, *node.keywords])
        lines.append(f"{indent}class {name}{f'({bases})' if bases else ''}{suffix}")
        inner = f"{indent}    "
        for member in self._bindings(model, scope):
            if not _public_member(member.name):
                continue
            guard = _suffix(self._guard(model, member.guard, len(binding.guard)))
            if member.kind == "def":
                lines += self._function(model, member.node, member.name, inner, guard, label)
            elif member.kind == "class":
                lines += self._class(model, member, member.name, inner, guard, label)
            elif member.kind in {"assign", "annassign", "annotation-only", "type-alias"}:
                lines += self._value(model, member, member.name, inner, guard, collapse=not self._field(model, member))
            else:
                self._refuse(model.module, member.line, f"a class member bound by {member.kind}")
        members = model.class_members(scope, spelled=True)
        stored = {
            attribute: nodes[0].lineno
            for attribute, nodes in members.items()
            if attribute not in scope.bindings and _public_member(attribute) and self._stored_by_init(model, nodes)
        }
        lines += [f"{inner}self.{attribute}" for attribute in sorted(stored, key=stored.__getitem__)]
        for base in source_model.bases(scope):
            self._queue_base(model, base, label)
        return lines

    @staticmethod
    def _stored_by_init(model: source_model.SourceModule, nodes: list[ast.AST]) -> bool:
        return any(isinstance(node, ast.Attribute) and model.scope_of(node).code_name == "__init__" for node in nodes)

    def _field(self, model: source_model.SourceModule, binding: source_model.Binding) -> bool:
        """Whether a class member is a field: annotated, and not as a ``ClassVar``."""
        annotation = self._annotations(model).get(id(binding.node))
        if annotation is None:
            return False
        head = annotation.value if isinstance(annotation, ast.Subscript) else annotation
        return model.origin(head) not in _CLASS_VARS

    def _value(
        self,
        model: source_model.SourceModule,
        binding: source_model.Binding,
        name: str,
        indent: str,
        suffix: str,
        *,
        collapse: bool,
        expand: bool = True,
    ) -> list[str]:
        """Write a value binding; with ``expand`` false only its own line, the constants it reads left to `_uses`."""
        statement = model.statement_at(binding.line)
        annotation = self._annotations(model).get(id(binding.node))
        written = f": {self._text(model, annotation)}" if annotation is not None else ""
        alias = annotation is not None and model.origin(annotation) in _TYPE_ALIASES
        assignment = model.assignment(binding.node)
        augmented = binding.kind == "augassign"
        value = getattr(statement, "value", None) if augmented else binding.value
        value = value if value is not None or assignment is None else assignment.element
        if binding.kind == "annotation-only":
            return [f"{indent}{name}{written}{suffix}"]
        if binding.kind == "type-alias":
            parameters = ", ".join(self._text(model, item) for item in getattr(statement, "type_params", ()))
            alias_value = self._text(model, getattr(statement, "value", binding.node))
            return [f"{indent}type {name}{f'[{parameters}]' if parameters else ''} = {alias_value}{suffix}"]
        if binding.kind == "match" or value is None:
            return [f"{indent}{name}{written} = <{binding.kind} target>{suffix}"]
        operator = " += " if augmented else " = "
        text = self._text(model, value, collapse=collapse and not alias)
        lines = [f"{indent}{name}{written}{operator}{text}{suffix}"]
        return lines + (self._uses(model, [value], f"{indent}    ") if expand else [])

    def _uses(
        self, model: source_model.SourceModule, values: list[ast.expr], indent: str, *, collapse: bool = True
    ) -> list[str]:
        """Write the constants the values read that no public name reaches, once each, the ones they read after them.

        Large containers are written as their size unless ``collapse`` is false.
        """
        lines: list[str] = []
        seen: set[Key] = set()
        pending = [(model, value) for value in values]
        while pending:
            current, value = pending.pop(0)
            for node in ast.walk(value):
                origin = current.origin(node) if id(node) in self._loads(current) else None
                reached = self._reach(origin) if origin is not None and "." in origin else []
                for definition in reached if isinstance(reached, list) else []:
                    key = (definition.module, definition.name, definition.line)
                    if definition.kind not in {"assign", "annassign"} or key in self.homes or key in seen:
                        continue
                    seen.add(key)
                    used = self.tree.module(definition.module)
                    binding = self._binding(definition)
                    lines += self._value(
                        used, binding, f"uses {definition.name}", indent, "", collapse=collapse, expand=False
                    )
                    pending += [(used, binding.value)] if binding.value is not None else []
        return lines

    def _queue_base(self, model: source_model.SourceModule, base: ast.expr, label: str) -> None:
        origins = {origin for origin, guard in model.origins(base) if not _typing_only(guard)}
        origins = origins or ({origin} if (origin := model.origin(base)) is not None else set())
        if len(origins) != 1:
            self._refuse(model.module, base.lineno, f"a base the record cannot follow: {self._text(model, base)}")
            return
        for origin in origins:
            self._queue(origin, "a base of", label)

    def _queue(self, origin: str | None, reason: str, label: str) -> None:
        """Queue the package definitions a dotted name leads to that no public name reaches, for the last section."""
        reached = self._reach(origin) if origin is not None else []
        for definition in reached if isinstance(reached, list) else []:
            key = (definition.module, definition.name, definition.line)
            if definition.kind in _DEFINITIONS and key not in self.homes:
                self._bases.append((key, reason, label))

    def _base_section(self) -> list[str]:
        lines: list[str] = []
        while self._bases:
            key, reason, label = self._bases.pop(0)
            if key in self._written:
                continue
            self._written.add(key)
            module, name, line = key
            definition = source_model.Definition(module, name, line, self._kind(key), ())
            written = self._write(definition, name, "", label)
            lines += [f"# {reason} {label}", *self._with_identity(written, definition, (module, name))]
        return lines

    def _kind(self, key: Key) -> str:
        module, name, line = key
        bound = self.tree.module(module).module_scope.bindings[name]
        return next(binding.kind for binding in bound if binding.line == line)


def read(root: Path, package: str = PACKAGE) -> PublicApi:
    """Read the public API of a package under ``root``."""
    return PublicApi(root, package)
