"""Tests for the decision homes of scripts/decision_homes.py: homes and sites are definitions their rows name.

Only the home modules and the adapter sites of a row call its homes, or a caller the baseline still lists. The passes
a placement row puts its decision after are declared and write a fact the row reads.
"""

from __future__ import annotations

import ast
import builtins
import re
from collections import Counter
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, TypeGuard

import pytest

from scripts._source_model import bases
from scripts.build_architecture_docs import (
    DECISION_BEGIN_MARKER,
    DECISION_END_MARKER,
    PLACEMENT_BEGIN_MARKER,
    PLACEMENT_END_MARKER,
    generate_decision_table,
    generate_placement_table,
)
from scripts.decision_homes import (
    BUILDER_HOMES,
    DECISION_HOMES,
    PLACEMENTS,
    BuilderHome,
    DecisionHome,
    Home,
    Mark,
    Placement,
)
from scripts.fact_tables import Fact, RenderFact
from scripts.pass_decls import DECISION_DECLS, PASS_DECLS
from scripts.where import DEFS, PACKAGE_DIR, SPAN, Page, Repo, scope_name
from tests.decision_home_allowlist import BUILDER_BYPASSES, UNLISTED_CALLERS

if TYPE_CHECKING:
    from collections.abc import Iterable

    from scripts._source_model import Reference, Scope, SourceModule
    from scripts.where import Found

ROOT = Path(__file__).parents[1]
PACKAGE = "datamodel_code_generator"


@cache
def _repo() -> Repo:
    """Return the package and the scripts as `scripts/where.py` reads them, through the source model."""
    return Repo(ROOT, Page([], [], [], []))


def _missing(home: Home, repo: Repo | None = None) -> str | None:
    """Return what ``src/`` lacks of a home: its module, or a def, class or module-level assignment of its name.

    `Repo.definitions` finds a def or class by its qualified name and a module-level assignment by its name; an import
    defines nothing, so an imported name is no home. The repository is this one unless given.
    """
    repo = repo or _repo()
    path = f"src/{PACKAGE}/{home.path}"
    if path not in repo.names:
        return f"{path} does not exist"
    if home.name and not any(found.qualname == home.name for found in repo.definitions(home.name, path)):
        return f"{home.path} defines no {home.name}"
    return None


def _named(home: Home, cell: str) -> bool:
    """Return whether a cell names a home: its file in a code span, and the last part of its name in one."""
    spans = SPAN.findall(cell)
    files = {file for span in spans if (file := span.partition(":")[0]).endswith(".py")}
    words = {word for span in spans for word in re.findall(r"[A-Za-z_]\w*", span)}
    cited = any(home.path == file or home.path.endswith(f"/{file}") for file in files)
    return cited and (not home.name or home.name.rpartition(".")[2] in words)


def _problems(records: Iterable[DecisionHome]) -> list[str]:
    """Return what is wrong with records: a home or site src/ lacks, one its cell does not name, or no home at all."""
    problems: list[str] = []
    for record in records:
        key = f"{record.decision[:60]}..."
        if not record.homes:
            problems.append(f"{key}: no home")
        for kind, cell, homes in (("core", record.core, record.homes), ("adapters", record.adapters, record.sites)):
            for home in homes:
                if (missing := _missing(home)) is not None:
                    problems.append(f"{key}: {missing}")
                elif not _named(home, cell):
                    problems.append(f"{key}: the {kind} cell does not name {':'.join(filter(None, home))}")
                elif kind == "adapters" and not home.name and (named := _named_after(home, cell)):
                    problems.append(f"{key}: the adapters cell names `{named}` after {home.path}, no whole-module site")
    return problems


def _named_after(home: Home, cell: str) -> str:
    """Return the name a cell gives right after the file of a whole-module site (`` `f.py`: `x` ``, `` `f.py` (`x` ``).

    A cell states a whole module by citing its file with no name after it; a name it gives there that no scope
    carries (a constant, a parameter, an alias) must not widen the site to the module.
    """
    for match in re.finditer(r"`([^`:]+\.py)`(?::| \()\s*`([^`]+)`", cell):
        if home.path == match[1] or home.path.endswith(f"/{match[1]}"):
            return match[2]
    return ""


class Caller(NamedTuple):
    """A run-time read of a home outside the home modules and the adapter sites of every row that holds the home."""

    home: str
    """The home as ``path:name``."""
    caller: str
    """The scope that reads it as ``path:qualname`` (``<module>`` for the module's own code)."""
    line: int


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
    """Return the def and class homes of records that the caller guard checks, with what may call each.

    Each comes with its definition in the package and the home modules and adapter sites of every row that holds it
    (`_callable_homes`). A home that is an exception class (it derives from ``BaseException``) is not checked: its
    record states where the type is defined, not who may raise or catch it. `scripts/build_heki_contracts.py` writes
    the same homes as heki contracts.
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


def unlisted_callers(repo: Repo, records: Iterable[DecisionHome], package: str = PACKAGE_DIR) -> list[Caller]:
    """Return the run-time reads of a def or class home of records in a package outside the modules and sites.

    Paths of homes, sites and callers are relative to the package directory, ``src/datamodel_code_generator/`` unless
    given.

    `Repo.module_reads` finds the references; a store binds the name and one in an annotation names a type, so neither
    calls the home. A site is a def or a class (and the scopes inside it), or a whole module. The homes are those
    `checked_homes` returns.
    """
    callers: list[Caller] = []
    for home, found, modules, sites in checked_homes(repo, records, package):
        for rel, references in repo.module_reads(found).items():
            module = rel.removeprefix(package)
            if not rel.startswith(package) or module in modules:
                continue
            callers += [
                Caller(f"{home.path}:{home.name}", f"{module}:{scope}", reference.line)
                for reference in references
                if reference.context == "load" and not reference.annotation
                for scope in [scope_name(reference.scope)]
                if not any(_covers(site, module, scope) for site in sites)
            ]
    return sorted(callers)


DISPATCH = f"{PACKAGE}.parser._schema_dispatch"
"""The module of the dispatch tables, as the names its classes lead to through imports."""
BUILD_INDEX = {f"{DISPATCH}.Row": 2, f"{DISPATCH}.Default": 1}
"""The position of the parse method among the arguments of a table entry that holds one (``build``)."""


class Bypass(NamedTuple):
    """A scope that reaches what a builder home keeps: a parse method a table row holds, or the output of a builder."""

    home: str
    """The parse method, or the builder, as ``path:qualname``."""
    scope: str
    """The scope that calls the parse method outside the tables, or builds the marked output, as ``path:qualname``."""
    line: int


class _Reads:
    """The reads of names and attributes in the package's modules, indexed once per module."""

    def __init__(self, repo: Repo, package: str) -> None:
        """Read the modules of the package under the repository."""
        self.repo = repo
        self.package = package
        self.modules = {module: rel for rel, (_, module) in repo.names.items() if rel.startswith(package)}
        self._scoped: dict[str, list[tuple[ast.AST, Scope]]] = {}
        self._attributes: dict[str, dict[str, list[tuple[ast.AST, Scope]]]] = {}

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

    def scoped(self, rel: str) -> list[tuple[ast.AST, Scope]]:
        """Return every node of a module with the scope it runs in, a def's body in the def's, found once.

        A def or class statement, its decorators, defaults, bases and annotations run in the scope around it.
        """
        if (found := self._scoped.get(rel)) is None:
            model = self.repo.model(rel)
            defs = {id(scope.node): scope for scope in model.scopes if scope.kind in {"function", "class"}}
            found = self._scoped[rel] = []
            todo: list[tuple[ast.AST, Scope]] = [(model.tree, model.module_scope)]
            while todo:
                node, scope = todo.pop()
                found.append((node, scope))
                inner = defs.get(id(node), scope)
                body = {id(statement) for statement in getattr(node, "body", [])} if inner is not scope else set()
                todo += [(child, inner if id(child) in body else scope) for child in ast.iter_child_nodes(node)]
            self._attributes[rel] = attributes = {}
            for node, scope in found:
                if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                    attributes.setdefault(node.attr, []).append((node, scope))
        return found

    def reads(self, home: Home) -> list[tuple[str, ast.AST, Scope]]:
        """Return the reads of a def by module path: a name that leads to it, or an attribute of its name.

        Name resolution does not follow an attribute (``self.method``, ``module.function``), so every attribute
        spelled as the def's name reads it.
        """
        leaf = home.name.rpartition(".")[2]
        found: list[tuple[str, ast.AST, Scope]] = []
        for rel in self.paths(leaf):
            self.scoped(rel)
            found += [(rel, node, scope) for node, scope in self._attributes[rel].get(leaf, [])]
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


def _marks_of(record: BuilderHome) -> tuple[Mark, ...]:
    """Return the marks of what a record's builder builds: a call of its table's ``choose``, else its own marks."""
    return (Mark(callee=f"{record.table.name.rpartition('.')[2]}.choose"),) if record.table else record.marks


def _sets(pairs: Iterable[tuple[str | None, ast.expr]], mark: Mark) -> bool:
    """Return whether keyword and value pairs set the mark's keyword to its value as a constant."""
    return any(
        key == mark.keyword and isinstance(value, ast.Constant) and value.value is mark.value
        if isinstance(mark.value, bool)
        else key == mark.keyword and isinstance(value, ast.Constant) and value.value == mark.value
        for key, value in pairs
    )


def _pairs(node: ast.AST) -> list[tuple[str | None, ast.expr]]:
    """Return the keywords a call passes, those of a ``**`` dict display included, or the constant keys of a dict."""
    if isinstance(node, ast.Dict):
        return [(key.value, value) for key, value in zip(node.keys, node.values, strict=True) if _is_text(key)]
    if not isinstance(node, ast.Call):
        return []
    pairs: list[tuple[str | None, ast.expr]] = [(item.arg, item.value) for item in node.keywords if item.arg]
    return pairs + [pair for item in node.keywords if item.arg is None for pair in _pairs(item.value)]


def _is_text(node: ast.expr | None) -> TypeGuard[ast.Constant]:
    """Return whether a dict key is a string constant."""
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _marked(model: SourceModule, node: ast.AST, mark: Mark) -> bool:
    """Return whether a call carries a mark, or, for a keyword asked of any call, a dict display of flags sets it.

    A mark may ask the call for a constant positional argument (``_container_flags(obj, "dict")``).
    """
    if isinstance(node, ast.Dict):
        return not mark.callee and bool(mark.keyword) and _sets(_pairs(node), mark)
    if not isinstance(node, ast.Call):
        return False
    callee = ast.unparse(node.func)
    if mark.callee and mark.callee not in {model.origin(node.func), callee} and not callee.endswith(f".{mark.callee}"):
        return False
    if mark.argument is not None:
        index, constant = mark.argument
        given = node.args[index] if len(node.args) > index else None
        if not (isinstance(given, ast.Constant) and given.value == constant):
            return False
    return not mark.keyword or _sets(_pairs(node), mark)


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
        problems += [
            f"{key}: {missing}" for home in (record.builder, *record.parts) if (missing := _missing(home, repo))
        ]
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

    `builder_bypasses` reports their reads outside the tables; `scripts/build_heki_contracts.py` writes them as heki
    contracts.
    """
    return sorted(_held_builds(_Reads(repo, package), _holders(repo, records, package)))


def builder_bypasses(repo: Repo, records: Iterable[BuilderHome], package: str = PACKAGE_DIR) -> list[Bypass]:
    """Return the scopes of a package that bypass a builder home.

    A parse method that an entry of a recorded table holds (``Row.build``, ``Default.build``) runs only through the
    tables: a read of it outside every recorded table (and the shared rows a table reads) is a bypass, whatever calls
    it, a public hook too. A read of a record's table outside its builder, other than by ``typed_children``, is a
    bypass (an alias of the table escapes the ``choose`` mark otherwise). A call with a mark of a record outside its
    builder and parts, in a module under its ``within``, is a bypass: for a table record the mark is a call of the
    table's ``choose``.
    """
    reads = _Reads(repo, package)
    found: dict[tuple[str, str], int] = {}
    records = list(records)
    holders = _holders(repo, records, package)
    inside = {id(node) for _, _, held in holders for expr in held for node in ast.walk(expr)}
    for build in _held_builds(reads, holders):
        for rel, node, scope in reads.reads(build):
            if id(node) not in inside:
                key = (f"{build.path}:{build.name}", f"{rel.removeprefix(package)}:{scope_name(scope)}")
                found[key] = min(found.get(key, node.lineno), node.lineno)
    parents: dict[str, dict[int, ast.AST]] = {}
    for record, table in [(record, record.table) for record in records if record.table]:
        for rel, node, scope in reads.reads(table):
            tree = parents.setdefault(
                rel,
                {
                    id(child): parent
                    for parent in ast.walk(repo.model(rel).tree)
                    for child in ast.iter_child_nodes(parent)
                },
            )
            parent = tree.get(id(node))
            if (isinstance(parent, ast.Attribute) and parent.attr == "typed_children") or _covers(
                record.builder, rel.removeprefix(package), scope_name(scope)
            ):
                continue
            key = (f"{record.builder.path}:{record.builder.name}", f"{rel.removeprefix(package)}:{scope_name(scope)}")
            found[key] = min(found.get(key, node.lineno), node.lineno)
    for record, mark in [(holder, mark) for holder in records for mark in _marks_of(holder)]:
        for rel in reads.paths(mark.keyword or mark.callee.rpartition(".")[2]):
            if not rel.startswith(f"{package}{record.within}"):
                continue
            model = repo.model(rel)
            for node, inside_scope in reads.scoped(rel):
                scope = scope_name(inside_scope) if _marked(model, node, mark) else ""
                if scope and not any(
                    _covers(holder, rel.removeprefix(package), scope) for holder in (record.builder, *record.parts)
                ):
                    key = (f"{record.builder.path}:{record.builder.name}", f"{rel.removeprefix(package)}:{scope}")
                    found[key] = min(found.get(key, node.lineno), node.lineno)
    return sorted(Bypass(home, scope, line) for (home, scope), line in found.items())


def placement_problems(records: Iterable[Placement]) -> list[str]:
    """Return what is wrong with placement records: a pass that is not declared or writes none of the row's facts.

    A pass of ``after`` is a pass or a decision of ``scripts/pass_decls.py`` that writes a fact the row reads, as
    `scripts/check_pass_pipeline.py` asks of an ``After`` edge, and the place cell names it in a code span; a pass the
    place cell names is in ``after``. Every fact is a member of ``Fact`` or ``RenderFact``, and no two records read
    the same.
    """
    declared = {decl.name: decl for decl in (*PASS_DECLS, *DECISION_DECLS.values())}
    problems: list[str] = []
    for record in records:
        key = f"{record.reads[:60]}..."
        spans = SPAN.findall(record.place)
        problems.extend(
            f"{key}: {fact!r} is no member of Fact or RenderFact"
            for fact in record.facts
            if not isinstance(fact, (Fact, RenderFact))
        )
        for name in record.after:
            if (decl := declared.get(name)) is None:
                problems.append(f"{key}: {name} is no pass of scripts/pass_decls.py")
            elif not decl.writes & record.facts:
                problems.append(f"{key}: after {name}, which writes none of its facts")
            if name not in spans:
                problems.append(f"{key}: the place cell does not name {name}")
        problems.extend(
            f"{key}: the place cell names {name}, which after leaves out"
            for name in spans
            if name in declared and name not in record.after
        )
    repeated = [reads for reads, count in Counter(record.reads for record in records).items() if count > 1]
    problems.extend(f"{reads[:60]}...: recorded more than once" for reads in repeated)
    return problems


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_decision_homes_are_defined_in_src() -> None:
    """Every home of a record is a definition of src/ that the record's core cell names."""
    problems = _problems(DECISION_HOMES)

    assert not problems, "\n".join(problems)


@pytest.mark.allow_direct_assert("the guard of the homes must fail the test, not be stored as an expected output")
def test_only_the_home_modules_and_adapter_sites_call_a_home() -> None:
    """A home is called only by its row's home modules and adapter sites, or by a caller the baseline still lists.

    The baseline (`tests/decision_home_allowlist.py`) holds the callers of today's code that no row lists; it only
    shrinks (`scripts/check_ratchets.py` counts it), so an entry the code no longer has must go.
    """
    found = {(caller.home, caller.caller) for caller in unlisted_callers(_repo(), DECISION_HOMES)}
    listed = {(home, caller) for home, callers in UNLISTED_CALLERS.items() for caller in callers}
    new = [f"{caller} calls {home}" for home, caller in sorted(found - listed)]
    stale = [f"{caller} calls {home}" for home, caller in sorted(listed - found)]

    assert not new, (
        "calls of a home outside its row's modules and adapter sites: call it through an adapter site of the row, or "
        "name the caller in the row's Adapters cell and its sites (scripts/decision_homes.py):\n" + "\n".join(new)
    )
    assert not stale, "callers tests/decision_home_allowlist.py lists that no longer call: remove them\n" + "\n".join(
        stale
    )


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_decisions_are_recorded_once() -> None:
    """No two records hold the same decision."""
    repeated = [decision for decision, count in Counter(r.decision for r in DECISION_HOMES).items() if count > 1]

    assert not repeated, "\n".join(repeated)


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_the_page_table_is_written_from_the_records() -> None:
    """The decision table of docs/architecture.md is the table the records write, row for row."""
    docs = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    written = f"{DECISION_BEGIN_MARKER}{generate_decision_table()}{DECISION_END_MARKER}"

    assert written in docs, "docs/architecture.md: run python scripts/build_architecture_docs.py"


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_placements_name_declared_passes_that_write_their_facts() -> None:
    """Every pass a placement row puts its decision after is declared, named by its place and writes a fact it reads."""
    problems = placement_problems(PLACEMENTS)

    assert not problems, "\n".join(problems)


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_the_placement_table_is_written_from_the_records() -> None:
    """The placement table of docs/architecture.md is the table the records write, row for row."""
    docs = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    written = f"{PLACEMENT_BEGIN_MARKER}{generate_placement_table()}{PLACEMENT_END_MARKER}"

    assert written in docs, "docs/architecture.md: run python scripts/build_architecture_docs.py"


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_builder_homes_are_stated_and_defined() -> None:
    """Every builder record's passage is on the page and names its builder; src/ defines what it names."""
    page = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    problems = builder_problems(_repo(), BUILDER_HOMES, page)

    assert not problems, "\n".join(problems)


@pytest.mark.allow_direct_assert(
    "the guard of the builder homes must fail the test, not be stored as an expected output"
)
def test_only_the_builders_build_their_outputs() -> None:
    """A table row's parse method runs only through the tables, and a marked output is built only by its builder.

    The baseline (`BUILDER_BYPASSES` of `tests/decision_home_allowlist.py`) holds the bypasses of today's code; it
    only shrinks (`scripts/check_ratchets.py` counts it), so an entry the code no longer has must go.
    """
    found = {(bypass.home, bypass.scope) for bypass in builder_bypasses(_repo(), BUILDER_HOMES)}
    listed = {(home, scope) for home, scopes in BUILDER_BYPASSES.items() for scope in scopes}
    new = [f"{scope} bypasses {home}" for home, scope in sorted(found - listed)]
    stale = [f"{scope} bypasses {home}" for home, scope in sorted(listed - found)]

    assert not new, (
        "a parse method of a dispatch-table row called outside the tables, or an output built outside its builder: "
        "choose the row through its table's position, or build the output in the builder or one of its parts "
        "(scripts/decision_homes.py BUILDER_HOMES):\n" + "\n".join(new)
    )
    assert not stale, (
        "bypasses tests/decision_home_allowlist.py lists that no longer happen: remove them\n" + "\n".join(stale)
    )


SEEDED = (
    DecisionHome(
        "seeded: homes src/ lacks",
        homes=(
            Home("types.py", "no_such_decision"),
            Home("no_such_module.py", "decide"),
            Home("types.py", "DataType.no_such_member"),
            Home("parser/_intersection.py", "json_values_equal"),
        ),
        core="`types.py`: `no_such_decision`, `DataType.no_such_member`; `no_such_module.py:decide`; "
        "`parser/_intersection.py:json_values_equal`",
        adapters="`types.py:no_such_site`",
        sites=(Home("types.py", "no_such_site"),),
    ),
    DecisionHome(
        "seeded: a home the core cell does not name",
        homes=(Home("types.py", "DataType.admits_none"), Home("_json_schema.py")),
        core="`types.py:DataType`",
        adapters="`types.py:DataType`",
        sites=(Home("types.py", "DataType.admits_none"),),
    ),
    DecisionHome(
        "seeded: a whole-module site the cell qualifies",
        homes=(Home("types.py", "DataType"),),
        core="`types.py:DataType`",
        adapters="`parser/avro.py` (`NULL_SCHEMA`), `_json_schema.py`",
        sites=(Home("parser/avro.py"), Home("_json_schema.py")),
    ),
    DecisionHome("seeded: no home", homes=(), core="", adapters="", sites=()),
)
"""Records whose homes or sites src/ does not define, or that their cells do not name or qualify."""


@pytest.mark.allow_direct_assert("the problems of seeded records are the check's verdict, not a stored output")
def test_decision_home_check_fails_on_seeded_records() -> None:
    """A missing function, module or class member, an import, an unnamed home or site and no home are problems."""
    assert _problems(SEEDED) == [
        "seeded: homes src/ lacks...: types.py defines no no_such_decision",
        f"seeded: homes src/ lacks...: src/{PACKAGE}/no_such_module.py does not exist",
        "seeded: homes src/ lacks...: types.py defines no DataType.no_such_member",
        "seeded: homes src/ lacks...: parser/_intersection.py defines no json_values_equal",
        "seeded: homes src/ lacks...: types.py defines no no_such_site",
        "seeded: a home the core cell does not name...: the core cell does not name types.py:DataType.admits_none",
        "seeded: a home the core cell does not name...: the core cell does not name _json_schema.py",
        "seeded: a home the core cell does not name...: the adapters cell does not name types.py:DataType.admits_none",
        (
            "seeded: a whole-module site the cell qualifies...: "
            "the adapters cell names `NULL_SCHEMA` after parser/avro.py, no whole-module site"
        ),
        "seeded: no home...: no home",
    ]


SEEDED_PACKAGE = {
    "__init__.py": "from .core import decide\n",
    "core.py": (
        "class Rule:\n    def method(self) -> None:\n        pass\n\n\nTABLE = {}\n\n\n"
        "def decide() -> Rule:\n    return Rule()\n\n\n"
        "class Failure(Exception):\n    pass\n\n\nclass Detail(Failure):\n    pass\n"
    ),
    "adapter.py": (
        "from .core import TABLE, Rule, decide\n\n\ndef site() -> None:\n    decide()\n\n\n"
        "class Site:\n    def nested(self) -> None:\n        decide()\n\n\n"
        "def typed(rule: Rule) -> object:\n    return TABLE\n\n\n"
        "def bypass(value: object) -> bool:\n    return isinstance(value, Rule) and decide() is not None\n"
    ),
    "whole.py": "from .core import decide\n\nRESULT = decide()\n",
    "raises.py": (
        "from .core import Detail, Failure\n\n\n"
        "def raised() -> None:\n    raise Failure('x')\n\n\n"
        "def caught() -> bool:\n    try:\n        raise Detail\n    except Failure as error:\n"
        "        return isinstance(error, Detail)\n"
    ),
    "through.py": (
        "from . import core\nfrom datamodel_code_generator import decide\n\n\n"
        "def by_module() -> None:\n    core.decide()\n\n\nby_package = decide()\n"
    ),
}
"""A package whose ``core.py`` holds the homes: callers by name, by module attribute and through a re-export.

``raises.py`` raises and catches the exception homes ``Failure`` and ``Detail`` (a subclass of it) outside ``core.py``:
the guard does not report them.
"""


@pytest.mark.allow_direct_assert("the callers the guard finds in a seeded package are its verdict, not a stored output")
def test_unlisted_callers_on_a_seeded_package(tmp_path: Path) -> None:
    """Reads outside the home modules and sites are callers; a site's nested scopes, annotations and tables are not."""
    package = tmp_path / "src" / PACKAGE
    package.mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    for name, text in SEEDED_PACKAGE.items():
        (package / name).write_text(text, encoding="utf-8")
    record = DecisionHome(
        "seeded",
        homes=(
            Home("core.py", "decide"),
            Home("core.py", "Rule"),
            Home("core.py", "TABLE"),
            Home("core.py", "Rule.method"),
            Home("core.py", "Failure"),
            Home("core.py", "Detail"),
        ),
        core="",
        adapters="",
        sites=(Home("adapter.py", "site"), Home("adapter.py", "Site"), Home("whole.py")),
    )

    assert unlisted_callers(Repo(tmp_path, Page([], [], [], [])), [record]) == [
        Caller("core.py:Rule", "adapter.py:bypass", 18),
        Caller("core.py:decide", "adapter.py:bypass", 18),
        Caller("core.py:decide", "through.py:<module>", 9),
        Caller("core.py:decide", "through.py:by_module", 6),
    ]


SEEDED_BUILDER_PACKAGE = {
    "parser/__init__.py": "",
    "parser/_schema_dispatch.py": (
        "class Row:\n    pass\n\n\nclass Default:\n    pass\n\n\nclass DispatchTable:\n    pass\n"
    ),
    "parser/kinds.py": (
        "from functools import partial\n\n"
        "from datamodel_code_generator.parser._schema_dispatch import Default, DispatchTable, Row\n\n\n"
        "def _shared(parser, site):\n    return parser.parse_object(site)\n\n\n"
        "def _then(build):\n    return build\n\n\n"
        "class Parser:\n"
        "    def _build_array(self, site):\n        return self._build_enum(site)\n\n"
        "    def _build_enum(self, site):\n        return site\n\n"
        "    def _build_default(self, site):\n        return site\n\n"
        "    def parse_object(self, site):\n        return _shared(self, site)\n\n"
        "    _ROWS = (Row('enum', None, _then(_build_enum)),)\n"
        "    _TABLE = DispatchTable(\n"
        "        (Row('array', None, build=partial(_build_array)), *_ROWS, Row('shared', None, _shared)),\n"
        "        Default('default', _build_default),\n    )\n"
        "    _OTHER = DispatchTable((), Default('other', None))\n\n"
        "    def parse_site(self, site):\n        return self._TABLE.choose(self, site)\n\n"
        "    def shortcut(self, site):\n        return self._TABLE.choose(self, site)\n\n"
        "    def parameter(self, _build_enum):\n        return _build_enum\n\n"
        "    def alias(self, site):\n        table = self._TABLE\n        return table.choose(self, site)\n"
    ),
    "parser/maps.py": (
        "def build_map(parser):\n    return part(parser)\n\n\n"
        "def part(parser):\n    return parser.data_type(is_dict=True)\n\n\n"
        "def flags(parser):\n    return parser.data_type(**{'is_dict': True})\n\n\n"
        "def handed_on():\n    return {'is_dict': True, 'is_list': False}\n\n\n"
        "def not_a_map(parser):\n    return parser.data_type(is_dict=False)\n\n\n"
        "def unreached(parser):\n    return parser\n\n\n"
        "def seeded_slot(parser, obj):\n    return parser.data_type(**parser._container_flags(obj, 'dict'))\n\n\n"
        "def other_slot(parser, obj):\n    return parser._container_flags(obj, 'list')\n"
    ),
    "model.py": (
        "class Ref:\n    pass\n\n\ndef name_model():\n    return Ref()\n\n\n"
        "def outside(parser):\n    return parser.data_type(is_dict=True)\n"
    ),
    "naming.py": (
        "from datamodel_code_generator.model import Ref as Named\n\n\ndef rename():\n    return Named()\n\n\n"
        "def construct():\n    return Named.model_construct()\n"
    ),
    "other.py": "class Ref:\n    pass\n\n\ndef local():\n    return Ref()\n",
}
"""A package with a dispatch table in ``parser/kinds.py`` and an object-map builder in ``parser/maps.py``.

``Parser._build_array`` calls the parse method of the ``enum`` row (held through the shared rows and a wrapper),
``_shared`` calls a public hook that is no row's parse method and the hook calls ``_shared`` back, ``shortcut`` calls
the table's ``choose`` outside its position, and ``_OTHER`` is a table no record holds. ``flags`` and ``handed_on``
build the dict flag outside the builder; ``model.py`` lies outside the record's ``within``. ``naming.py`` builds a
``Ref`` of ``model.py`` through an alias outside ``name_model``; ``other.py`` builds a class of its own of that name.
``Parser.parameter`` reads a parameter spelled as a parse method, ``Parser.alias`` reads the table into a name,
``seeded_slot`` asks ``_container_flags`` for the ``dict`` slot (``other_slot`` for another) and ``naming.construct``
builds a ``Ref`` by ``model_construct``.
"""

SEEDED_BUILDERS = (
    BuilderHome(
        output="seeded: a parse of the table",
        prose="the table that `parse_site` chooses from",
        builder=Home("parser/kinds.py", "Parser.parse_site"),
        table=Home("parser/kinds.py", "Parser._TABLE"),
    ),
    BuilderHome(
        output="seeded: a map",
        prose="one builder (`build_map`)",
        builder=Home("parser/maps.py", "build_map"),
        marks=(Mark(keyword="is_dict", value=True), Mark(callee="_container_flags", argument=(1, "dict"))),
        parts=(Home("parser/maps.py", "part"),),
        within="parser/",
    ),
    BuilderHome(
        output="seeded: a named model",
        prose="Names come from `name_model`.",
        builder=Home("model.py", "name_model"),
        marks=(Mark(callee=f"{PACKAGE}.model.Ref"), Mark(callee=f"{PACKAGE}.model.Ref.model_construct")),
    ),
)
"""Records of the seeded package that its page states."""

SEEDED_BUILDER_PAGE = (
    "Each site takes the table that\n`parse_site` chooses from. A map has one builder (`build_map`). Names come from "
    "`name_model`."
)


def _seeded_builder_repo(root: Path) -> Repo:
    """Write the seeded builder package under a root and return the repository read there."""
    package = root / "src" / PACKAGE
    (root / "scripts").mkdir()
    for name, text in SEEDED_BUILDER_PACKAGE.items():
        (package / name).parent.mkdir(parents=True, exist_ok=True)
        (package / name).write_text(text, encoding="utf-8")
    return Repo(root, Page([], [], [], []))


@pytest.mark.allow_direct_assert(
    "the bypasses the guard finds in a seeded package are its verdict, not a stored output"
)
def test_builder_bypasses_on_a_seeded_package(tmp_path: Path) -> None:
    """Calls of row parse methods outside the tables, of ``choose`` outside the position and marks are bypasses."""
    assert builder_bypasses(_seeded_builder_repo(tmp_path), SEEDED_BUILDERS) == [
        Bypass("model.py:name_model", "naming.py:construct", 9),
        Bypass("model.py:name_model", "naming.py:rename", 5),
        Bypass("parser/kinds.py:Parser._build_enum", "parser/kinds.py:Parser._build_array", 16),
        Bypass("parser/kinds.py:Parser.parse_site", "parser/kinds.py:Parser.alias", 44),
        Bypass("parser/kinds.py:Parser.parse_site", "parser/kinds.py:Parser.shortcut", 38),
        Bypass("parser/kinds.py:_shared", "parser/kinds.py:Parser.parse_object", 25),
        Bypass("parser/maps.py:build_map", "parser/maps.py:flags", 10),
        Bypass("parser/maps.py:build_map", "parser/maps.py:handed_on", 14),
        Bypass("parser/maps.py:build_map", "parser/maps.py:seeded_slot", 26),
    ]


@pytest.mark.allow_direct_assert("the problems of seeded records are the check's verdict, not a stored output")
def test_builder_problems_on_seeded_records(tmp_path: Path) -> None:
    """A passage the page lacks or that names another builder, a missing def, no table, no reach are problems."""
    records = (
        *SEEDED_BUILDERS,
        BuilderHome(
            output="seeded: a passage the page lacks",
            prose="the builder (`build_map`) of nothing",
            builder=Home("parser/maps.py", "build_map"),
            marks=(Mark(callee="build"),),
        ),
        BuilderHome(
            output="seeded: a passage that names another builder",
            prose="one builder (`build_map`)",
            builder=Home("parser/maps.py", "part"),
            table=Home("parser/kinds.py", "Parser._ROWS"),
            marks=(Mark(callee="build"),),
            parts=(Home("parser/maps.py", "unreached"), Home("parser/maps.py", "no_such_part")),
        ),
        BuilderHome(
            output="seeded: tables src/ lacks",
            prose="one builder (`build_map`)",
            builder=Home("parser/maps.py", "build_map"),
            table=Home("parser/no_such_module.py", "Parser._TABLE"),
        ),
        BuilderHome(
            output="seeded: a table of a class src/ lacks",
            prose="one builder (`build_map`)",
            builder=Home("parser/maps.py", "build_map"),
            table=Home("parser/kinds.py", "NoSuchParser._TABLE"),
        ),
        BuilderHome(
            output="seeded: neither a table nor a mark",
            prose="one builder (`build_map`)",
            builder=Home("parser/maps.py", "build_map"),
        ),
    )

    assert builder_problems(_seeded_builder_repo(tmp_path), records, SEEDED_BUILDER_PAGE) == [
        "seeded: a passage the page lacks...: the page has no passage 'the builder (`build_map`) of nothing'",
        "seeded: a passage that names another builder...: the passage does not name part",
        "seeded: a passage that names another builder...: a record states either a table or a mark",
        "seeded: a passage that names another builder...: parser/maps.py defines no no_such_part",
        ("seeded: a passage that names another builder...: parser/kinds.py assigns no dispatch table to Parser._ROWS"),
        "seeded: a passage that names another builder...: the builder does not reach parser/maps.py:unreached",
        "seeded: a passage that names another builder...: the builder does not reach parser/maps.py:no_such_part",
        "seeded: tables src/ lacks...: parser/no_such_module.py assigns no dispatch table to Parser._TABLE",
        "seeded: a table of a class src/ lacks...: parser/kinds.py assigns no dispatch table to NoSuchParser._TABLE",
        "seeded: neither a table nor a mark...: a record states either a table or a mark",
        "parser/kinds.py:Parser._OTHER is a dispatch table no record holds",
    ]


SEEDED_PLACEMENTS = (
    Placement("seeded: a pass pass_decls.py lacks", frozenset({Fact.BASES}), "after `no_such_pass`", ("no_such_pass",)),
    Placement(
        "seeded: a pass that writes none of its facts",
        frozenset({Fact.METADATA}),
        "after L2 `name_models`",
        ("name_models",),
    ),
    Placement(
        "seeded: a pass the place cell does not name",
        frozenset({RenderFact.CLASS_NAMES}),
        "after L2",
        ("name_models",),
    ),
    Placement("seeded: a pass the record leaves out", frozenset({Fact.BASES}), "L6, after `apply_generic_base_class`"),
    Placement("seeded: a fact of no fact table", frozenset({"field_set"}), "L1 at that object"),
    Placement("seeded: a fact of no fact table", frozenset(), "any pass"),
)
"""Placement records whose passes are not declared, write none of their facts or differ from their place cell."""


@pytest.mark.allow_direct_assert("the problems of seeded records are the check's verdict, not a stored output")
def test_placement_check_fails_on_seeded_records() -> None:
    """A pass undeclared, writing none of the facts, unnamed or left out, a fact of no table, a repeat: problems."""
    assert placement_problems(SEEDED_PLACEMENTS) == [
        "seeded: a pass pass_decls.py lacks...: no_such_pass is no pass of scripts/pass_decls.py",
        "seeded: a pass that writes none of its facts...: after name_models, which writes none of its facts",
        "seeded: a pass the place cell does not name...: the place cell does not name name_models",
        (
            "seeded: a pass the record leaves out...: "
            "the place cell names apply_generic_base_class, which after leaves out"
        ),
        "seeded: a fact of no fact table...: 'field_set' is no member of Fact or RenderFact",
        "seeded: a fact of no fact table...: recorded more than once",
    ]
