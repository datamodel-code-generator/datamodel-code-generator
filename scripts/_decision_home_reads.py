"""Read the decision homes of scripts/decision_homes.py in the package: who may read them, and what builders hold.

The record checks of `tests/test_decision_homes.py` and the heki contracts of `scripts/build_heki_contracts.py` read
the same homes and held parse methods from here.
"""

from __future__ import annotations

import ast
import builtins
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from scripts._source_model import bases
from scripts.decision_homes import BuilderHome, DecisionHome, Home
from scripts.where import DEFS, PACKAGE_DIR, SPAN, Page, Repo, scope_name

if TYPE_CHECKING:
    from collections.abc import Iterable

    from scripts._source_model import Reference, Scope, SourceModule
    from scripts.where import Found

ROOT = Path(__file__).parents[1]
PACKAGE = "datamodel_code_generator"


@cache
def default_repo() -> Repo:
    """Return the package and the scripts as `scripts/where.py` reads them, through the source model."""
    return Repo(ROOT, Page([], [], [], []))


def missing(home: Home, repo: Repo | None = None) -> str | None:
    """Return what ``src/`` lacks of a home: its module, or a def, class or module-level assignment of its name.

    `Repo.definitions` finds a def or class by its qualified name and a module-level assignment by its name; an import
    defines nothing, so an imported name is no home. The repository is this one unless given.
    """
    repo = repo or default_repo()
    path = f"src/{PACKAGE}/{home.path}"
    if path not in repo.names:
        return f"{path} does not exist"
    if home.name and not any(found.qualname == home.name for found in repo.definitions(home.name, path)):
        return f"{home.path} defines no {home.name}"
    return None


def _callable_homes(records: Iterable[DecisionHome]) -> dict[Home, tuple[set[str], set[Home]]]:
    """Return the module-level homes of records with the modules and sites that may call each, over every row.

    A module home, a method (its calls are attribute reads that name resolution does not follow) and a module-level
    assignment (a constant or table) are not checked.
    """
    allowed: dict[Home, tuple[set[str], set[Home]]] = {}
    for record in records:
        for home in record.homes:
            if home.name and "." not in home.name:
                modules, sites = allowed.setdefault(home, (set(), set()))
                modules.update(other.path for other in record.homes)
                sites.update(record.sites)
    return allowed


def _covers(site: Home, module: str, scope: str) -> bool:
    """Return whether an adapter site holds a scope: its whole module, its def or class, or a scope within."""
    return site.path == module and (not site.name or scope == site.name or scope.startswith(f"{site.name}."))


def _is_exception(repo: Repo, path: str, name: str) -> bool:
    """Return whether a class of the repository derives from ``BaseException``, through builtins or other classes.

    A base the model cannot resolve to one origin does not make the class an exception.
    """
    model = repo.model(path)
    modules = {module: rel for rel, (_, module) in repo.names.items()}
    for scope in model.scopes:
        if scope.kind != "class" or scope.qualname != name:
            continue
        for base in bases(scope):
            module, _, leaf = (model.origin(base) or "").rpartition(".")
            if module == "builtins":
                if issubclass(getattr(builtins, leaf, object), BaseException):
                    return True
            elif module in modules and _is_exception(repo, modules[module], leaf):
                return True
    return False


def checked_homes(
    repo: Repo, records: Iterable[DecisionHome], package: str = PACKAGE_DIR
) -> list[tuple[Home, Found, set[str], set[Home]]]:
    """Return the def and class homes of records whose readers heki checks, with what may read each.

    Each comes with its definition in the package and the home modules and adapter sites of every row that holds it
    (`_callable_homes`). A home that is an exception class (it derives from ``BaseException``) is not checked: its
    record states where the type is defined, not who may raise or catch it. `scripts/build_heki_contracts.py` writes
    a contract per home: only those modules and sites read it at run time.
    """
    checked: list[tuple[Home, Found, set[str], set[Home]]] = []
    for home, (modules, sites) in _callable_homes(records).items():
        path = f"{package}{home.path}"
        for found in repo.definitions(home.name, path) if path in repo.names else []:
            if found.qualname != home.name or found.parent != "module" or found.kind not in DEFS:
                continue
            if found.kind == "class" and _is_exception(repo, found.path, found.qualname):
                continue
            checked.append((home, found, modules, sites))
    return checked


DISPATCH = f"{PACKAGE}.parser._schema_dispatch"
"""The module of the dispatch tables, as the names its classes lead to through imports."""
BUILD_INDEX = {f"{DISPATCH}.Row": 2, f"{DISPATCH}.Default": 1}
"""The position of the parse method among the arguments of a table entry that holds one (``build``)."""


class _Reads:
    """The reads of names and attributes in the package's modules, indexed once per module."""

    def __init__(self, repo: Repo, package: str) -> None:
        """Read the modules of the package under the repository."""
        self.repo = repo
        self.package = package
        self.modules = {module: rel for rel, (_, module) in repo.names.items() if rel.startswith(package)}
        self._attributes: dict[str, dict[str, list[tuple[ast.Attribute, Scope]]]] = {}

    def paths(self, word: str = "") -> list[str]:
        """Return the module paths of the package whose text holds a word."""
        return [rel for rel in self.modules.values() if word in self.repo.text(rel)]

    def home_of(self, rel: str, reference: Reference) -> Home | None:
        """Return the def a name of a module leads to: a def of the class body it is read in, or one of a module."""
        model = self.repo.model(rel)
        resolution = model.resolve(reference.node)
        if resolution.scope is not None and resolution.scope.kind == "class" and resolution.bindings:
            return Home(rel.removeprefix(self.package), f"{resolution.scope.qualname}.{reference.name}")
        module, _, name = (model.origin(reference.node) or "").rpartition(".")
        return Home(self.modules[module].removeprefix(self.package), name) if module in self.modules else None

    def attributes(self, rel: str) -> dict[str, list[tuple[ast.Attribute, Scope]]]:
        """Return the attributes a module loads, by name, each with the scope it runs in (a def's body in the def's).

        A def or class statement, its decorators, defaults, bases and annotations run in the scope around it.
        """
        if (found := self._attributes.get(rel)) is None:
            model = self.repo.model(rel)
            defs = {id(scope.node): scope for scope in model.scopes if scope.kind in {"function", "class"}}
            found = self._attributes[rel] = {}
            todo: list[tuple[ast.AST, Scope]] = [(model.tree, model.module_scope)]
            while todo:
                node, scope = todo.pop()
                if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                    found.setdefault(node.attr, []).append((node, scope))
                inner = defs.get(id(node), scope)
                body = {id(statement) for statement in getattr(node, "body", [])} if inner is not scope else set()
                todo += [(child, inner if id(child) in body else scope) for child in ast.iter_child_nodes(node)]
        return found

    def reads(self, home: Home) -> list[tuple[str, ast.expr, Scope]]:
        """Return the reads of a def by module path: a name that leads to it, or an attribute of its name.

        Name resolution does not follow an attribute (``self.method``, ``module.function``), so every attribute
        spelled as the def's name reads it.
        """
        leaf = home.name.rpartition(".")[2]
        found: list[tuple[str, ast.expr, Scope]] = []
        for rel in self.paths(leaf):
            found += [(rel, node, scope) for node, scope in self.attributes(rel).get(leaf, [])]
            found += [
                (rel, reference.node, reference.scope)
                for reference in self.repo.model(rel).references
                if reference.name == leaf
                and reference.context == "load"
                and not reference.annotation
                and self.home_of(rel, reference) == home
            ]
        return found


def _class_value(repo: Repo, home: Home) -> tuple[SourceModule, ast.expr] | None:
    """Return the module and the value a class attribute or module-level name of a home is assigned once."""
    path = f"src/{PACKAGE}/{home.path}"
    if path not in repo.names:
        return None
    model = repo.model(path)
    owner, _, name = home.name.rpartition(".")
    for scope in model.scopes:
        if scope.qualname == owner and scope.kind == ("class" if owner else "module"):
            values = [binding.value for binding in scope.bindings.get(name, []) if binding.value is not None]
            return (model, values[0]) if len(values) == 1 else None
    return None


def _is_table(model: SourceModule, value: ast.expr) -> bool:
    """Return whether a value is a dispatch table: a call of ``DispatchTable``."""
    return isinstance(value, ast.Call) and model.origin(value.func) == f"{DISPATCH}.DispatchTable"


def _held(model: SourceModule, value: ast.expr) -> list[ast.expr]:
    """Return a table and the values of the names it reads that hold table entries (shared rows), until none is new."""
    held: dict[int, ast.expr] = {}
    todo = [value]
    while todo:
        expr = todo.pop()
        held[id(expr)] = expr
        nodes = {id(node) for node in ast.walk(expr)}
        todo += [
            binding.value
            for reference in model.references
            if id(reference.node) in nodes and reference.context == "load"
            for binding in model.resolve(reference.node).bindings
            if binding.value is not None
            and id(binding.value) not in held
            and any(
                isinstance(call, ast.Call) and model.origin(call.func) in BUILD_INDEX
                for call in ast.walk(binding.value)
            )
        ]
    return list(held.values())


def _builds(held: Iterable[ast.expr], model: SourceModule) -> list[Reference]:
    """Return the parse methods that the entries of held table values name (``Row`` and ``Default``).

    A wrapper call around one (``_then_parse_refs(f)``, ``partial(f, ...)``) names its first argument; an attribute
    (``row.build`` of shared rows, held themselves) and a def a row factory makes inside itself name none.
    """
    names: list[Reference] = []
    references = {id(reference.node): reference for reference in model.references}
    for expr in held:
        for call in ast.walk(expr):
            if not isinstance(call, ast.Call) or (index := BUILD_INDEX.get(model.origin(call.func) or "")) is None:
                continue
            build = next((item.value for item in call.keywords if item.arg == "build"), None)
            build = build or (call.args[index] if len(call.args) > index else None)
            while isinstance(build, ast.Call) and build.args:
                build = build.args[0]
            names += [references[id(build)]] if id(build) in references else []
    return names


def _tables(reads: _Reads) -> list[Home]:
    """Return every class attribute or module-level name of the package assigned a dispatch table."""
    found: list[Home] = []
    for rel in reads.paths("DispatchTable("):
        model = reads.repo.model(rel)
        found += [
            Home(rel.removeprefix(reads.package), f"{scope.qualname}.{name}" if scope.qualname else name)
            for scope in model.scopes
            if scope.kind in {"class", "module"}
            for name, bindings in scope.bindings.items()
            if any(binding.value is not None and _is_table(model, binding.value) for binding in bindings)
        ]
    return found


def _unreached(reads: _Reads, record: BuilderHome) -> list[Home]:
    """Return the parts of a record that the builder does not reach by reads through the parts it reaches."""
    reached = [record.builder]
    left = list(record.parts)
    while more := [
        part
        for part in left
        if any(
            _covers(holder, rel.removeprefix(reads.package), scope_name(scope))
            for rel, _, scope in reads.reads(part)
            for holder in reached
        )
    ]:
        reached += more
        left = [part for part in left if part not in more]
    return left


def builder_problems(repo: Repo, records: Iterable[BuilderHome], page: str, package: str = PACKAGE_DIR) -> list[str]:
    """Return what is wrong with builder records and the dispatch tables of a package.

    A record is wrong where its passage is not on the page or does not name its builder (a code span that ends the
    builder's qualified name), where it states neither or both of a table and a mark, where src/ lacks its builder or a
    part, where its table is no dispatch table and where the builder does not reach a part. A dispatch table of the
    package that no record holds is wrong too.
    """
    problems: list[str] = []
    reads = _Reads(repo, package)
    flat = " ".join(page.split())
    for record in (records := list(records)):
        key, name = f"{record.output[:60]}...", record.builder.name
        if " ".join(record.prose.split()) not in flat:
            problems.append(f"{key}: the page has no passage {record.prose!r}")
        elif not any(name == span or name.endswith(f".{span}") for span in SPAN.findall(record.prose)):
            problems.append(f"{key}: the passage does not name {name}")
        if (record.table is None) is (not record.marks):
            problems.append(f"{key}: a record states either a table or a mark")
        problems += [f"{key}: {gap}" for home in (record.builder, *record.parts) if (gap := missing(home, repo))]
        if record.table and ((value := _class_value(repo, record.table)) is None or not _is_table(*value)):
            problems.append(f"{key}: {record.table.path} assigns no dispatch table to {record.table.name}")
        problems += [f"{key}: the builder does not reach {part.path}:{part.name}" for part in _unreached(reads, record)]
    held = {record.table for record in records if record.table}
    problems += [
        f"{table.path}:{table.name} is a dispatch table no record holds"
        for table in _tables(reads)
        if table not in held
    ]
    return problems


def _holders(
    repo: Repo, records: Iterable[BuilderHome], package: str
) -> list[tuple[str, SourceModule, list[ast.expr]]]:
    """Return the module path, the model and the held values (`_held`) of each recorded table of a package."""
    return [
        (rel, model, held)
        for record in records
        if record.table and (value := _class_value(repo, record.table)) is not None
        for model, table in [value]
        for rel in [f"{package}{record.table.path}"]
        for held in [_held(model, table)]
    ]


def _held_builds(reads: _Reads, holders: Iterable[tuple[str, SourceModule, list[ast.expr]]]) -> set[Home]:
    """Return the defs that the entries of held table values name as their parse methods."""
    return {home for rel, model, held in holders for name in _builds(held, model) if (home := reads.home_of(rel, name))}


def held_parse_methods(repo: Repo, records: Iterable[BuilderHome], package: str = PACKAGE_DIR) -> list[Home]:
    """Return the parse methods that the entries of the recorded tables hold, which run only through the tables.

    `scripts/build_heki_contracts.py` writes a heki contract per method: only the modules of the tables call it.
    """
    return sorted(_held_builds(_Reads(repo, package), _holders(repo, records, package)))


def typed_children_readers(repo: Repo, table: Home, package: str = PACKAGE_DIR) -> list[Home]:
    """Return the scopes of a package whose every read of a recorded table asks it for ``typed_children``.

    Such a scope reads what the rows of the table type, not which row parses a site, so it may read the table besides
    the builder; a scope that also reads the table otherwise (its ``choose``, a name bound to it) is none. heki cannot
    tell the method a read is followed by, so `scripts/build_heki_contracts.py` names these scopes as readers.
    """
    reads = _Reads(repo, package)
    parents: dict[str, dict[int, ast.AST]] = {}
    only: dict[Home, bool] = {}
    for rel, node, scope in reads.reads(table):
        if rel not in parents:
            tree = repo.model(rel).tree
            parents[rel] = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        parent = parents[rel].get(id(node))
        home = Home(rel.removeprefix(package), scope_name(scope))
        only[home] = only.get(home, True) and isinstance(parent, ast.Attribute) and parent.attr == "typed_children"
    return sorted(home for home, typed in only.items() if typed)
