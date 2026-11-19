"""Check the source tree for the code shapes that code reviews keep finding, one rule per shape (report only).

usage: python scripts/check_source_rules.py [--root DIR] [--base REF] [--rule NAME]... [--count]

Every rule reads the tree with ``ast`` and reads bindings, scopes, references, imports and suppression comments through
`scripts/_source_model.py` (`Module.model`), never with a walk of its own; it reports each site as
``path:line: rule: detail``; a file the running Python cannot parse (newer syntax, say) reports none. The summary gives
the count of every rule and how to fix its sites. The counts are ratchet keys ``source-rule:<rule>`` (``--count``
prints them as JSON, ``{key: {file: count}}``), which `scripts/check_ratchets.py` counts on every tree it compares:
a change may keep or lower a count. A rule is marked absolute once its count is 0; the ratchet then fails on any site,
and the exit status here is 1 only when an absolute rule reports a site.

``lost-last-caller`` compares the tree with ``--base`` (a git revision of the same repository) and runs only with it.
It cannot tell from the source whether tests reach every branch of a callable, so it reports each candidate with the
command that shows its coverage (the ratchet prints them and never fails on them), and every run prints how many
public callables it cannot decide (``coverage:``; on stderr with ``--count``).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._ci_lanes import WORKFLOW, Lane, read_lanes
from scripts._source_model import SourceModelError, SourceModule, Unreadable
from tests.units import condition_holds, condition_problem

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from scripts._source_model import Binding, Import, Scope

PACKAGE = "src/datamodel_code_generator"
PACKAGE_NAME = "datamodel_code_generator"
GENERATED = "_compiled_templates"
ARCHITECTURE_DOCS = "docs/architecture.md"
REFERENCE_HOME = f"{PACKAGE}/_json_pointer.py"
CLONE_MIN_NODES = 11
CLONE_SKIPPED = (f"{PACKAGE}/_types/",)
NAMESPACE_HOOKS = frozenset({"__getattr__", "__dir__"})
CUT_METHODS = frozenset({"split", "rsplit", "partition", "rpartition", "find", "rfind", "index", "rindex"})
ENUMERATING_METHODS = frozenset({"values", "items", "keys", "copy"})
ENUMERATING_USES = frozenset({"iterate", "unpack", "argument", *(f"call:{method}" for method in ENUMERATING_METHODS)})
"""How the value of ``globals()`` is used when it is enumerated (`SourceModule.use_of`)."""
IF_CONDITIONS = frozenset({"type-checking", "version", "platform", "if"})
"""The kinds of condition that an ``if`` makes (`Condition.kind`): an import under one loads nothing for certain."""
UNUSED_IMPORT = ("F401", "unused-import")
TESTS = "tests"
TESTS_STORED = "tests/data/expected/"
SLEEP_ALLOWLIST = "scripts/test_sleep_allowlist.toml"
RERUN_MODULES = frozenset({"flaky", "pytest_rerunfailures", "rerunfailures"})
TEST_DATA = "tests/data/"
CAPABILITY_HOME = "tests/capabilities.py"
CAPABILITY_TABLE = "CAPABILITIES"
NEEDS = "tests.capabilities.needs"
SKIP_ORIGINS = frozenset({
    "pytest.importorskip",
    "pytest.mark.skip",
    "pytest.mark.skipif",
    "pytest.skip",
    "unittest.SkipTest",
    "unittest.skip",
    "unittest.skipIf",
    "unittest.skipUnless",
})
"""The skip markers, calls and exceptions, by the origin the source model gives a name or attribute chain."""
_FILE_CITATION = re.compile(r"`([A-Za-z_][\w/]*\.py):([A-Za-z_][\w.]*)")
_CLASS_CITATION = re.compile(r"`([A-Z][A-Za-z0-9_]*)\.([A-Za-z_]\w*)")
_WORD = re.compile(r"\w+")
_RERUN_OPTION = re.compile(
    r"(?:-p\s*)?(?:pytest_)?rerunfailures|--(?:reruns|reruns-delay|only-rerun|rerun-except)(?:[=\s]\S*)?"
)
_DOTTED_PATH = re.compile(r"[A-Za-z_]\w*(?:[.:][A-Za-z_]\w*)*")
_STRING_PATH = re.compile(r"(?P<module>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)[.:](?P<name>[A-Za-z_]\w*)")


@dataclass(frozen=True)
class Finding:
    """One site a rule reports."""

    path: str
    line: int
    rule: str
    detail: str


class Module:
    """One Python file of the tree, read through the source model (`model`) on first use."""

    def __init__(self, root: Path, rel: str) -> None:
        """Name ``rel`` below ``root``; a file this Python cannot read or parse holds nothing to report."""
        self.root = root
        self.rel = rel

    @cached_property
    def name(self) -> str:
        """Return the dotted module name of a file (``src/`` dropped, a package ``__init__`` its package)."""
        parts = list(Path(self.rel).with_suffix("").parts)
        if parts[0] == "src":
            parts.pop(0)
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    @cached_property
    def _read(self) -> tuple[SourceModule, str]:
        """Read the file: its model (an empty one if unread) and why the source model refuses it (empty: it does not).

        A file this Python cannot parse (newer syntax) holds nothing to report. One that parses and that the model
        refuses is a failure of the model: `Tree.unreadable` reports it, since its sites are not counted.
        """
        try:
            return SourceModule((self.root / self.rel).read_text(encoding="utf-8"), self.rel, self.name), ""
        except (SyntaxError, ValueError):
            return SourceModule("", self.rel, self.name), ""
        except SourceModelError as error:
            return SourceModule("", self.rel, self.name), " ".join(str(error).split())

    @property
    def model(self) -> SourceModule:
        """Return what the file's names, imports and comments are, by the language's rules."""
        return self._read[0]

    @property
    def error(self) -> str:
        """Return why the source model refuses the file, empty where it reads it or the file does not parse."""
        return self._read[1]

    @cached_property
    def star_reads(self) -> dict[ast.AST, str]:
        """Return the names read where a star import may bind them: a name the model leaves open (``star``) by node.

        A rule that looks for a builtin (``str``, ``globals``) counts such a read as the builtin it spells, unless the
        model knows the star import binds it: what a star import binds is not read from another module.
        """
        model = self.model
        return {
            reference.node: reference.name
            for reference in model.references
            if reference.context == "load" and model.resolve(reference.node).kind == "star"
        }

    @property
    def text(self) -> str:
        """Return the text of the file."""
        return self.model.text

    @property
    def tree(self) -> ast.Module:
        """Return the syntax tree the model reads (its nodes are the ones the model answers about)."""
        return self.model.tree

    @cached_property
    def lines(self) -> list[str]:
        """Return the lines of the file."""
        return self.text.splitlines()

    @cached_property
    def nodes(self) -> list[ast.AST]:
        """Return every node of the file, in ``ast.walk`` order."""
        return list(ast.walk(self.tree))


class Tree:
    """The files of a checkout that the rules read: every ``.py`` file below ``root``, or those of ``files``."""

    def __init__(self, root: Path, files: frozenset[str] | None = None) -> None:
        """Index the tree at ``root``; ``files`` (paths relative to it) limits what the rules read."""
        self.root = root
        self.files = files
        self._parsed: dict[str, Module] = {}

    def _python_files(self, top: str) -> list[str]:
        """Return the ``.py`` files below ``top`` as paths relative to the root, sorted."""
        found = (path.relative_to(self.root).as_posix() for path in sorted((self.root / top).rglob("*.py")))
        return [rel for rel in found if self.files is None or rel in self.files]

    def _modules(self, top: str) -> list[Module]:
        return [Module(self.root, rel) for rel in self._python_files(top) if GENERATED not in Path(rel).parts]

    @cached_property
    def package(self) -> list[Module]:
        """Return the modules of the package, generated template renderers aside."""
        return self._modules(PACKAGE)

    @cached_property
    def scripts(self) -> list[Module]:
        """Return the repository scripts."""
        return self._modules("scripts")

    @cached_property
    def tests(self) -> list[Module]:
        """Return the Python files of ``tests`` (stored expected output aside)."""
        return [Module(self.root, rel) for rel in self._python_files(TESTS) if not rel.startswith(TESTS_STORED)]

    @cached_property
    def words(self) -> dict[str, frozenset[str]]:
        """Return the words of every Python file that can import the package (test data aside) by path."""
        found: dict[str, frozenset[str]] = {}
        for top in ("src", "scripts", "tests"):
            for rel in self._python_files(top):
                if not rel.startswith("tests/data/"):
                    text = (self.root / rel).read_text(encoding="utf-8", errors="replace")
                    found[rel] = frozenset(_WORD.findall(text))
        return found

    def unreadable(self) -> dict[str, str]:
        """Return the files that parse and that the source model refuses, with its error: no rule can read them."""
        modules = {module.rel: module for module in (*self.package, *self.scripts, *self.tests, *self._parsed.values())}
        return {rel: module.error for rel, module in sorted(modules.items()) if module.error}

    def module(self, rel: str) -> Module:
        """Return the parsed file ``rel``, read once."""
        if not self._parsed:
            self._parsed.update((module.rel, module) for module in (*self.package, *self.scripts))
        if rel not in self._parsed:
            self._parsed[rel] = Module(self.root, rel)
        return self._parsed[rel]


@dataclass(frozen=True)
class Rule:
    """A source rule: the review lesson it encodes, its check and how to fix a site."""

    name: str
    check: Callable[[Tree, Tree | None], Iterator[Finding]]
    fix: str
    absolute: bool = False
    needs_base: bool = False
    coverage: Callable[[Tree], str] | None = None


def _cuts_at_hash(call: ast.Call, module: Module) -> bool:
    """Return whether ``call`` cuts at ``#``: ``s.split("#")``, ``str.split(s, "#")`` or ``s.split(sep="#")``.

    ``str`` is the builtin by origin: a parameter or an import named ``str`` is not it, and a star import that may
    bind it does not hide it (`Module.star_reads`).
    """
    if not isinstance(call.func, ast.Attribute) or call.func.attr not in CUT_METHODS:
        return False
    unbound = module.model.origin(call.func.value) == "builtins.str" or module.star_reads.get(call.func.value) == "str"
    position = 1 if unbound else 0
    separators = call.args[position : position + 1]
    if call.func.attr in {"split", "rsplit"}:
        separators += [keyword.value for keyword in call.keywords if keyword.arg == "sep"]
    return any(isinstance(item, ast.Constant) and item.value == "#" for item in separators)


def hash_split(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report a reference string cut at ``#`` by a string method outside the reference home.

    Review class: a copy of the step's decision left outside the routed sites (a hand-split ``#``).
    """
    for module in tree.package:
        if module.rel == REFERENCE_HOME:
            continue
        for node in module.nodes:
            if isinstance(node, ast.Call) and _cuts_at_hash(node, module):
                yield Finding(module.rel, node.lineno, "hash-split", ast.unparse(node))


class _CitationIndex:
    """Top-level names and class members of every module of the package."""

    def __init__(self, modules: Iterable[Module]) -> None:
        self.top: dict[str, set[str]] = {}
        self.classes: dict[str, dict[str, set[str]]] = {}
        for module in modules:
            model = module.model
            rel = module.rel.removeprefix(f"{PACKAGE}/")
            top = model.bindings(scope=model.module_scope)
            classes: dict[str, set[str]] = {}
            for binding in top:
                if binding.kind == "class":
                    members = model.class_members(model.scope_defined_by(binding.node), spelled=True)
                    classes.setdefault(binding.name, set()).update(members)
            self.top[rel] = {binding.name for binding in top if binding.kind != "del"}
            self.classes[rel] = classes

    def resolves_file(self, rel: str, dotted: str) -> bool | None:
        if rel.startswith(("scripts/", "tests/", "docs/")):
            return None
        if rel not in self.top:  # the page shortens paths (`msgspec.py`, `pydantic_v2/base_model.py`)
            return any(self.resolves_file(path, dotted) for path in self.top if path.endswith(f"/{rel}"))
        head, _, rest = dotted.partition(".")
        if head not in self.top[rel]:
            return False
        if not rest or head not in self.classes[rel]:
            return True
        return rest.split(".")[0] in self.classes[rel][head]

    def resolves_class(self, cls: str, attr: str) -> bool | None:
        holders = [members for classes in self.classes.values() if (members := classes.get(cls)) is not None]
        if not holders:
            return None  # not a class of the package (an Enum member, a pydantic name, a type)
        return any(attr in members for members in holders)


def doc_citations(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report a backticked ``path.py:Name[.attr]`` or ``Class.attr`` of the architecture page that names no code.

    Review class: docs or a script citing removed code. A tree without the page cites nothing.
    """
    if not (page := tree.root / ARCHITECTURE_DOCS).is_file():
        return
    index = _CitationIndex(tree.package)
    text = page.read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), start=1):
        cited = {(match[1], match[2].rstrip("."), True) for match in _FILE_CITATION.finditer(line)}
        cited |= {(match[1], match[2], False) for match in _CLASS_CITATION.finditer(line)}
        for first, second, is_file in sorted(cited):
            if second.endswith("_"):  # `has_*`-style prefixes name a family, not a name
                continue
            if is_file and index.resolves_file(first, second) is False:
                yield Finding(ARCHITECTURE_DOCS, number, "doc-citations", f"`{first}:{second}`")
            elif not is_file and index.resolves_class(first, second) is False:
                yield Finding(ARCHITECTURE_DOCS, number, "doc-citations", f"`{first}.{second}`")


def ellipsis_statement(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report a ``...`` statement (a stub body) in the package.

    Review class: a CodeQL alert (ineffectual statement) that costs a CI round.
    """
    for module in tree.package:
        for node in module.nodes:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and node.value.value is Ellipsis:
                yield Finding(module.rel, node.lineno, "ellipsis-statement", "...")


def private_reexport(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report an import of a private name that only a suppression of unused imports keeps: a private re-export.

    Every comment form counts (`SourceModule.suppressed`), on the name's line or on the first line of its statement.
    An import that its own scope reads is no re-export.

    Review class: a copy of the step's decision left outside the routed sites (a second reader through an old path).
    """
    for module in tree.package:
        model = module.model
        suppressed: frozenset[int] | None = None
        for item in model.imports:
            if item.alias is None or item.bound is None:
                continue
            if not item.bound.startswith("_") or item.bound.startswith("__"):
                continue
            if suppressed is None:
                suppressed = model.suppressed("ruff", UNUSED_IMPORT)
            binding = model.binding_at(item.alias)
            if {item.node.lineno, item.alias.lineno} & suppressed and binding is not None and not model.uses(binding):
                yield Finding(module.rel, item.alias.lineno, "private-reexport", item.bound)


def _enclosing_function(scope: Scope | None) -> str | None:
    """Return the name of the function whose code runs ``scope`` (the nearest ``def``), None outside any."""
    while scope is not None and scope.kind != "function":
        scope = scope.parent
    return None if scope is None else scope.code_name


def globals_registry(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report ``globals()`` enumerated as a registry, outside a module ``__getattr__`` or ``__dir__``.

    ``globals`` is the builtin by origin (`SourceModule.dynamic_sites`), or a name a star import may bind
    (`Module.star_reads`), and the enumeration is how the value is used (`SourceModule.use_of`): iterated, unpacked,
    passed as an argument or asked for its ``values``, ``items``, ``keys`` or ``copy``.

    Review class: a CodeQL alert group (every entry of such a registry reads as an unused global).
    """
    for module in (*tree.package, *tree.scripts):
        model = module.model
        calls = {site.node: site.scope for site in model.dynamic_sites if site.kind == "globals"}
        opened = {
            reference.node: reference.scope
            for reference in model.references
            if module.star_reads.get(reference.node) == "globals"
        }
        for node in module.nodes:
            if isinstance(node, ast.Call) and node.func in opened:
                calls[node] = opened[node.func]
        for node, scope in calls.items():
            if (
                isinstance(node, ast.expr)
                and model.use_of(node) in ENUMERATING_USES
                and _enclosing_function(scope) not in NAMESPACE_HOOKS
            ):
                yield Finding(module.rel, node.lineno, "globals-registry", module.lines[node.lineno - 1].strip())


def _module_reads(module: Module, names: set[str]) -> set[tuple[str, str]]:
    """Return the ``(module, name)`` pairs of ``names`` that ``module`` reads from a module by its name.

    A read is an import of the name (``from m import _x``), an attribute read on an imported module (``import m``
    then ``m._x``, by the origin the model gives the receiver) or a string holding its dotted path (``"m._x"``,
    ``"m:_x"``).
    """
    model = module.model
    reads = {
        (item.source, item.alias.name)
        for item in model.imports
        if item.kind in {"from", "star"} and item.alias is not None and isinstance(item.source, str)
    }
    for node in module.nodes:
        if isinstance(node, ast.Attribute) and node.attr in names and isinstance(node.ctx, ast.Load):
            reads |= {(origin, node.attr) for origin, _ in model.origins(node.value)}
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and (path := _STRING_PATH.fullmatch(node.value)) is not None
        ):
            reads.add((path["module"], path["name"]))
    return reads


def unused_private_global(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report a module-level private name (``_NAME = ...``) that nothing which can reach it reads.

    What can reach it: a read in its module (an augmented assignment is no read), and from any other file of the
    package, the scripts or the tests, an import of it, an attribute read on its module or a string holding its dotted
    path. The same name in another module is another global.

    Review class: dead code left by a move (a CodeQL unused global).
    """
    unread: dict[tuple[str, str], tuple[Module, int]] = {}
    for module in tree.package:
        model = module.model
        for binding in model.bindings(scope=model.module_scope):
            if (
                binding.kind in {"assign", "annassign", "augassign"}
                and not binding.guard
                and binding.name.startswith("_")
                and not binding.name.startswith("__")
                and not any(use.kind != "augassign" for use in model.uses(binding))
            ):
                unread.setdefault((module.name, binding.name), (module, binding.node.lineno))
    names = {name for _, name in unread}
    for rel, words in tree.words.items():
        if names & words:
            for read in _module_reads(tree.module(rel), names):
                unread.pop(read, None)
    for (_, name), (module, line) in unread.items():
        yield Finding(module.rel, line, "unused-private-global", name)


def _import_targets(item: Import, names: set[str]) -> list[str]:
    """Return the module an import loads: the submodule a ``from`` import names, else the module it reads."""
    if not isinstance(item.source, str):
        return []
    return [item.target if item.kind == "from" and item.target in names else item.source]


class _ImportGraph:
    """The module-level imports of the package outside any ``if`` and the function-level ones."""

    def __init__(self, modules: list[Module]) -> None:
        self.names = {module.name for module in modules}
        self.edges: dict[str, set[str]] = defaultdict(set)
        self.local: list[tuple[Module, ast.stmt | ast.expr, list[str]]] = []
        for module in modules:
            statements: dict[ast.stmt | ast.expr, list[str]] = {}
            for item in module.model.imports:
                if item.kind == "dynamic":
                    continue
                targets = [target for target in _import_targets(item, self.names) if target in self.names]
                if item.scope.runs_in == "function":
                    statements.setdefault(item.node, []).extend(targets)
                elif not any(condition.kind in IF_CONDITIONS for condition in item.guard):
                    for target in targets:
                        self.edges[module.name].add(target)
                        self.edges[module.name].update(self._parents(target))
            self.local += [(module, node, targets) for node, targets in statements.items()]
        self._reach: dict[str, set[str]] = {}

    def _parents(self, name: str) -> list[str]:
        """Return the packages that importing ``name`` initialises first, the root package aside."""
        parts = name.split(".")
        return [parent for index in range(2, len(parts)) if (parent := ".".join(parts[:index])) in self.names]

    def reach(self, name: str) -> set[str]:
        """Return the modules that importing ``name`` loads at module level."""
        if name not in self._reach:
            seen = {name, *self._parents(name)}
            todo = list(seen)
            while todo:
                for target in self.edges.get(todo.pop(), ()):
                    if target not in seen:
                        seen.add(target)
                        todo.append(target)
            self._reach[name] = seen
        return self._reach[name]


def redundant_local_import(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report a function-level import of package modules that the importer already loads at module level.

    An import inside a module-level ``if`` loads nothing for certain (a ``TYPE_CHECKING`` block, a fast path on
    ``sys.argv``, a ``sys.version_info`` branch that another interpreter skips), so only imports outside such a guard
    count.

    Review class: a local import kept for no reason (it defers nothing and closes no cycle). Imports of the root
    package are left out: they wait for its names to move to leaf modules, not for a hoist.
    """
    graph = _ImportGraph(tree.package)
    for module, node, imported in graph.local:
        targets = [target for target in imported if target != PACKAGE_NAME]
        if targets and all(
            target in graph.reach(module.name) and module.name not in graph.reach(target) for target in targets
        ):
            yield Finding(module.rel, node.lineno, "redundant-local-import", ", ".join(dict.fromkeys(targets)))


def _shape(node: object, names: dict[str, str], bound: set[str]) -> str:
    """Dump ``node`` like ``ast.dump`` with the names in ``bound`` numbered in the order they appear."""
    if isinstance(node, list):
        return "[" + ",".join(_shape(item, names, bound) for item in node) + "]"
    if not isinstance(node, ast.AST):
        return repr(node)
    fields = []
    for field, value in ast.iter_fields(node):
        if isinstance(node, ast.arg) and field != "arg":
            continue  # parameter annotations do not change what a body does
        if (field == "id" and isinstance(node, ast.Name)) or (field == "arg" and isinstance(node, ast.arg)):
            fields.append(names.setdefault(value, f"v{len(names)}") if value in bound else value)
        else:
            fields.append(_shape(value, names, bound))
    return f"{type(node).__name__}({','.join(fields)})"


def _body_key(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    body = node.body
    if (
        isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    inner = [sub for statement in body for sub in ast.walk(statement)]
    if len(inner) < CLONE_MIN_NODES:
        return None
    arguments = node.args
    bound = {arg.arg for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)}
    bound |= {arg.arg for arg in (arguments.vararg, arguments.kwarg) if arg is not None}
    bound |= {sub.id for sub in inner if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store)}
    bound |= {sub.arg for sub in inner if isinstance(sub, ast.arg)}  # parameters of lambdas and nested functions
    return hashlib.sha256(_shape(body, {}, bound).encode()).hexdigest()


def clone_bodies(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report each function body beyond the first of a group of bodies equal up to the names of parameters and locals.

    A group grows by one site for every copy added, in one module or in several.

    Review class: a copy of a decision outside its home (two functions that are one decision).
    """
    groups: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for module in tree.package:
        if module.rel.startswith(CLONE_SKIPPED):
            continue
        for node in module.nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (key := _body_key(node)) is not None:
                groups[key].append((module.rel, node.lineno, node.name))
    for members in groups.values():
        first, *others = members
        for rel, line, name in others:
            yield Finding(rel, line, "clone-bodies", f"{name} = {first[0]}:{first[1]} {first[2]}")


def _is_public_module(rel: str) -> bool:
    return not any(part.startswith("_") and part != "__init__.py" for part in Path(rel).parts)


def _public_callables(modules: Iterable[Module]) -> dict[tuple[str, str], tuple[str, int, int]]:
    """Return the public functions, classes and methods of public classes: (path, qualname) -> (module, start, end)."""
    found: dict[tuple[str, str], tuple[str, int, int]] = {}
    for module in modules:
        if not _is_public_module(module.rel):
            continue
        model = module.model
        for binding in model.bindings(scope=model.module_scope):
            if binding.kind not in {"def", "class"} or binding.guard or binding.name.startswith("_"):
                continue
            found[module.rel, binding.name] = _extent(module, binding.node)
            if binding.kind == "class":
                for method in model.bindings(scope=model.scope_defined_by(binding.node)):
                    if method.kind == "def" and not method.guard and not method.name.startswith("_"):
                        found[module.rel, f"{binding.name}.{method.name}"] = _extent(module, method.node)
    return found


def _extent(module: Module, node: ast.AST) -> tuple[str, int, int]:
    """Return the module name and the first and last line of a ``def`` or ``class`` statement."""
    start = getattr(node, "lineno", 0)
    return module.name, start, getattr(node, "end_lineno", None) or start


def _references(modules: Iterable[Module]) -> dict[str, list[tuple[str, int]]]:
    """Return where the package reads each name: as a name, an attribute or a dotted path in a string.

    The entries of ``__all__`` are not reads.
    """
    found: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for module in modules:
        model = module.model
        exports = {
            id(element)
            for binding in model.bindings("__all__", model.module_scope)
            if (statement := model.statement_at(binding.line)) is not None
            for element in ast.walk(statement)
        }
        for reference in model.references:
            if reference.context == "load" and reference.annotation != "string":
                found[reference.name].append((module.rel, reference.line))
        for node in module.nodes:
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                found[node.attr].append((module.rel, node.lineno))
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in exports
                and _DOTTED_PATH.fullmatch(node.value)
            ):
                found[re.split(r"[.:]", node.value)[-1]].append((module.rel, node.lineno))
    return found


def _callers(
    modules: list[Module],
) -> tuple[dict[tuple[str, str], tuple[str, int, int]], Counter[tuple[str, str]]]:
    """Return the public callables and how often the package reads each one outside its own body."""
    callables = _public_callables(modules)
    references = _references(modules)
    counts: Counter[tuple[str, str]] = Counter()
    for (rel, qualname), (_, start, end) in callables.items():
        counts[rel, qualname] = sum(
            1
            for path, line in references.get(qualname.rpartition(".")[2], ())
            if path != rel or not start <= line <= end
        )
    return callables, counts


def undecided_callers(tree: Tree) -> str:
    """Return how many public callables ``lost-last-caller`` cannot follow, and why."""
    callables = _public_callables(tree.package)
    defined = Counter(
        scope.code_name
        for module in tree.package
        for scope in module.model.scopes
        if scope.kind in {"function", "class"}
    )
    shared = sum(1 for _, qualname in callables if defined[qualname.rpartition(".")[2]] > 1)
    return (
        f"{shared} of {len(callables)} public callables undecided: each shares its name with another function or "
        "class of the package and uses are counted by name, so the loss of its last caller may go unseen"
    )


def lost_last_caller(tree: Tree, base: Tree | None) -> Iterator[Finding]:
    """Report a public callable the package used on the base and no longer uses: only tests reach its branches now.

    Review class: a branch only CI's combined coverage found (a public method whose internal callers were routed away).
    Report only: whether tests reach every branch needs coverage data, so each site names the command that shows it.
    """
    assert base is not None  # Rule.needs_base: run() calls this rule only with a base
    _, before = _callers(base.package)
    callables, after = _callers(tree.package)
    for (rel, qualname), (module, start, _) in sorted(callables.items()):
        if before.get((rel, qualname)) and not after[rel, qualname]:
            yield Finding(
                rel,
                start,
                "lost-last-caller",
                f"{qualname} (package uses on the base: {before[rel, qualname]}; "
                f"pytest <its tests> -p no:xdist --cov={module} --cov-branch --cov-report=term-missing)",
            )


def _retry_form(node: ast.AST, model: SourceModule, hits: frozenset[ast.AST], loads: frozenset[ast.AST]) -> str | None:
    """Return what kind of rerun or flaky marker, plugin or option ``node`` is, if it is one.

    A marker is ``pytest.mark.flaky`` or a name that an import from the ``flaky`` package binds, read by the origin
    the model gives it. ``hits`` are the import statements of a rerun plugin and ``loads`` the names that are read.
    """
    if isinstance(node, ast.Attribute) and node.attr == "flaky":
        return "flaky marker" if model.origin(node) == "pytest.mark.flaky" else None
    if node in loads:
        return "flaky marker" if (model.origin(node) or "").split(".")[0] == "flaky" else None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "rerun plugin or option" if _RERUN_OPTION.fullmatch(node.value.strip()) else None
    return "rerun plugin or option" if node in hits else None


def _allowed_sleeps(tree: Tree) -> dict[str, int]:
    path = tree.root / SLEEP_ALLOWLIST
    if not path.exists():
        return {}
    return {name: entry["count"] for name, entry in tomllib.loads(path.read_text(encoding="utf-8")).items()}


def flaky_test_workarounds(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report rerun or flaky markers and plugins in the tests, and ``time.sleep`` calls beyond the allowlist.

    Review class: a test that is retried or slept into passing hides a race that a deterministic wait fixes. The
    allowlist ``scripts/test_sleep_allowlist.toml`` counts today's sleeps per file with the reason; a count above it
    or an entry the file no longer needs is reported, so the list only shrinks.
    """
    allowed = _allowed_sleeps(tree)
    found: Counter[str] = Counter()
    for module in tree.tests:
        model = module.model
        hits = frozenset(
            item.node
            for item in model.imports
            if item.level == 0
            and item.kind in {"import", "from"}
            and isinstance(item.source, str)
            and item.source.split(".")[0] in RERUN_MODULES
        )
        loads = frozenset(reference.node for reference in model.references if reference.context == "load")
        for node in module.nodes:
            if form := _retry_form(node, model, hits, loads):
                yield Finding(module.rel, node.lineno, "flaky-test-workarounds", form)
            elif isinstance(node, ast.Call) and model.origin(node.func) == "time.sleep":
                found[module.rel] += 1
                if found[module.rel] > allowed.get(module.rel, 0):
                    yield Finding(module.rel, node.lineno, "flaky-test-workarounds", "time.sleep beyond the allowlist")
    for name, count in allowed.items():
        if found[name] < count:
            yield Finding(SLEEP_ALLOWLIST, 1, "flaky-test-workarounds", f"{name} allows {count}, holds {found[name]}")


def _test_modules(tree: Tree) -> Iterator[Module]:
    """Yield the test modules a skip rule reads: those of ``tests``, test data and the capability table aside."""
    for module in tree.tests:
        if module.rel != CAPABILITY_HOME and not module.rel.startswith(TEST_DATA):
            yield module


def raw_skip(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report a skip marker, call or exception in the tests outside the capability table (``tests/capabilities.py``).

    A site is a read of ``pytest.mark.skip``/``skipif``, ``pytest.skip``, ``pytest.importorskip`` or a ``unittest``
    skip, by origin (an alias counts, a local of the same name does not).

    Review class: a test that verifies nothing on some or all lanes, added by copying a neighbour's ``skipif``.
    """
    for module in _test_modules(tree):
        model = module.model
        loads = [reference.node for reference in model.references if reference.context == "load"]
        loads += [node for node in module.nodes if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)]
        for node in loads:
            if (origin := model.origin(node)) in SKIP_ORIGINS:
                yield Finding(module.rel, node.lineno, "raw-skip", origin)


@dataclass(frozen=True)
class _Capabilities:
    """The capability table of a tree, the line of each entry, what makes either unreadable, and the CI lanes."""

    table: dict[str, tuple[tuple[str, ...], str]]
    lines: dict[str, int]
    problems: list[Finding]
    lanes: list[Lane]

    def lanes_with(self, names: Iterable[str]) -> list[Lane]:
        """Return the lanes that have every named capability; an unknown name holds on none."""
        wanted = list(dict.fromkeys(names))
        if any(name not in self.table for name in wanted):
            return []
        clauses = [clause for name in wanted for clause in self.table[name][0]]
        return [lane for lane in self.lanes if all(condition_holds(clause, lane.facts) for clause in clauses)]


def _table_shape(value: object) -> str | None:
    """Return why a table value is not ``{name: ((clause, ...), reason)}``, or None."""
    if not isinstance(value, dict):
        return "it is not a dict"
    for name, entry in value.items():
        match entry:
            case (tuple() as clauses, str()) if isinstance(name, str) and all(isinstance(c, str) for c in clauses):
                continue
            case _:
                return f"{name!r} is not ((clause, ...), reason)"
    return None


def _capabilities(tree: Tree) -> _Capabilities | None:
    """Return the capability table and the lanes of a tree; None without the matrix (no lane judges anything)."""
    lanes, unreadable = read_lanes(tree.root)
    if not lanes and not unreadable:
        return None
    problems = [Finding(WORKFLOW, 1, "dead-capability", problem) for problem in unreadable]
    if not (tree.root / CAPABILITY_HOME).is_file():
        return _Capabilities({}, {}, problems, lanes)
    model = tree.module(CAPABILITY_HOME).model
    value = model.constant(CAPABILITY_TABLE)
    shape = value.reason if isinstance(value, Unreadable) else _table_shape(value)
    if shape is not None or not isinstance(value, dict):
        problems.append(Finding(CAPABILITY_HOME, 1, "dead-capability", f"{CAPABILITY_TABLE} is unreadable: {shape}"))
        return _Capabilities({}, {}, problems, lanes)
    bindings = model.bindings(CAPABILITY_TABLE, model.module_scope)
    keys = bindings[0].value.keys if isinstance(bindings[0].value, ast.Dict) else []
    lines = {key.value: key.lineno for key in keys if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    return _Capabilities(value, lines, problems, lanes)


def dead_capability(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report a capability of the table that no lane of the CI matrix has, or every lane has, or only subset lanes lack.

    Also a malformed one and an unreadable lane. A capability every lane has never skips; one only subset lanes lack
    (lanes that run a fixed list of tests, e.g. the formatter subset) skips no test of the suite. The lane reader
    knows which lanes are subset lanes, not which tests a subset lane lists, so the second case is refused outright.

    The lanes and their facts are read from the workflow, ``tox.ini``, ``pyproject.toml`` and ``uv.lock``
    (`scripts/_ci_lanes.py`); a tree without the workflow has no lanes and nothing is judged.

    Review class: a skip that guards against an environment no lane installs (black 19, Pydantic 1), so it says what
    the suite covers and covers nothing.
    """
    if (found := _capabilities(tree)) is None:
        return
    yield from found.problems
    for name, (clauses, _) in found.table.items():
        line = found.lines.get(name, 1)
        if problems := [problem for problem in map(condition_problem, clauses) if problem]:
            yield Finding(CAPABILITY_HOME, line, "dead-capability", f"{name}: {'; '.join(problems)}")
            continue
        holding = found.lanes_with([name])
        lacking = [lane for lane in found.lanes if lane not in holding]
        if not holding:
            yield Finding(CAPABILITY_HOME, line, "dead-capability", f"{name}: no lane has {' and '.join(clauses)}")
        elif not lacking:
            yield Finding(CAPABILITY_HOME, line, "dead-capability", f"{name}: every lane has it, so no skip fires")
        elif all(lane.subset for lane in lacking):
            yield Finding(
                CAPABILITY_HOME,
                line,
                "dead-capability",
                f"{name}: only subset lanes (fixed lists of tests) lack it, so no test of the suite is skipped",
            )


def _needed(model: SourceModule, node: ast.expr | None, seen: frozenset[Binding] = frozenset()) -> list[str]:
    """Return the capabilities a mark expression names: a ``needs(...)`` call, a name bound to one, or a list of them.

    ``needs`` is found by origin; a name is followed to its single module-level or local assignment, each once, so
    names bound to each other end the walk. An argument that is not a string literal is kept as its source text,
    which names no capability, so it holds on no lane.
    """
    if node is None:
        return []
    if isinstance(node, ast.Call):
        if model.origin(node.func) != NEEDS:
            return []
        return [
            arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else ast.unparse(arg)
            for arg in node.args
        ]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [name for item in node.elts for name in _needed(model, item, seen)]
    reference = next((item for item in model.references if item.node is node and item.context == "load"), None)
    if (
        reference is not None
        and len(bindings := model.resolve(reference.node).bindings) == 1
        and bindings[0] not in seen
    ):
        return _needed(model, bindings[0].value, seen | {bindings[0]})
    return []


def _marks_of(model: SourceModule, scope: Scope) -> list[str]:
    """Return the capabilities a scope's own marks name: its decorators, and the ``pytestmark`` of a module or class."""
    names = [name for decorator in getattr(scope.node, "decorator_list", []) for name in _needed(model, decorator)]
    if scope.kind in {"module", "class"}:
        for binding in model.bindings("pytestmark", scope):
            names += _needed(model, binding.value)
    return names


def _with_referenced_values(model: SourceModule, decorator: ast.expr) -> Iterator[ast.AST]:
    """Yield the nodes of a decorator and of the single assignment each name in it is bound to, each once.

    A ``parametrize`` over a module-level list of ``pytest.param`` belongs to every test that names the list.
    """
    seen: set[ast.AST] = set()
    pending: list[ast.AST] = [decorator]
    while pending:
        for node in ast.walk(pending.pop()):
            if node in seen:
                continue
            seen.add(node)
            yield node
            reference = next((item for item in model.references if item.node is node and item.context == "load"), None)
            if (
                reference is not None
                and len(bindings := model.resolve(reference.node).bindings) == 1
                and bindings[0].value is not None
            ):
                pending.append(bindings[0].value)


def never_run(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report a test, or a ``pytest.param`` of one, whose capabilities together hold on no lane of the CI matrix.

    A test's capabilities are those its module's and its classes' ``pytestmark`` and decorators name and its own
    decorators; a ``pytest.param`` adds those of its ``marks`` to those of each test that parametrizes over it. An
    unknown capability holds on no lane. A lane that selects only some tests is counted as running every test.

    Review class: a test that no lane runs, kept only because its skip hides that.
    """
    if (found := _capabilities(tree)) is None:
        return
    for module in _test_modules(tree):
        model = module.model
        owners: dict[ast.AST, list[list[str]]] = {}
        for scope in model.scopes:
            if scope.kind != "function" or not scope.code_name.startswith("test"):
                continue
            chain, parent = [scope], scope.parent
            while parent is not None and parent.kind in {"class", "module"}:
                chain.append(parent)
                parent = parent.parent
            if parent is not None:
                continue  # a function inside another function is no test pytest collects
            names = [name for item in reversed(chain) for name in _marks_of(model, item)]
            for decorator in getattr(scope.node, "decorator_list", []):
                for node in _with_referenced_values(model, decorator):
                    owners.setdefault(node, []).append(names)
            if names and not found.lanes_with(names):
                yield Finding(module.rel, scope.line, "never-run", f"{scope.qualname} needs {', '.join(names)}")
        module_names = _marks_of(model, model.module_scope)
        for node in module.nodes:
            if not (isinstance(node, ast.Call) and model.origin(node.func) == "pytest.param"):
                continue
            marks = next((keyword.value for keyword in node.keywords if keyword.arg == "marks"), None)
            if not (added := _needed(model, marks)):
                continue
            for consumer in owners.get(node, [module_names]):
                if not found.lanes_with([*consumer, *added]):
                    names = ", ".join(dict.fromkeys([*consumer, *added]))
                    yield Finding(module.rel, node.lineno, "never-run", f"pytest.param needs {names}")
                    break


RULES: tuple[Rule, ...] = (
    Rule(
        "hash-split",
        hash_split,
        "cut references through the reference home `_json_pointer.py` (`document_part`, `reference_tokens`, ...); "
        "add a function there when none fits",
    ),
    Rule(
        "doc-citations",
        doc_citations,
        "cite the current name of the code in docs/architecture.md, or drop the citation if the code is gone",
        absolute=True,
    ),
    Rule("ellipsis-statement", ellipsis_statement, "give the body a docstring instead of `...`"),
    Rule(
        "private-reexport",
        private_reexport,
        "import the private name from the module that defines it in every importer, then delete this import",
    ),
    Rule(
        "globals-registry",
        globals_registry,
        "list the entries in one explicit tuple (or register each where it is defined) instead of reading globals()",
    ),
    Rule(
        "unused-private-global",
        unused_private_global,
        "delete the unused name (a move left it behind)",
        absolute=True,
    ),
    Rule(
        "redundant-local-import",
        redundant_local_import,
        "import at module level: the importer already loads these modules there (outside any `if`), so the local "
        "import defers nothing",
    ),
    Rule(
        "clone-bodies",
        clone_bodies,
        "keep one body in the module that owns the decision and call it from the others",
    ),
    Rule(
        "lost-last-caller",
        lost_last_caller,
        "test every branch of the callable directly (run the command shown and add cases for the missing lines), "
        "or route its tests through the code that replaced the callers",
        needs_base=True,
        coverage=undecided_callers,
    ),
    Rule(
        "flaky-test-workarounds",
        flaky_test_workarounds,
        "fix the race the retry or sleep hides (wait on an event, a condition or an injected clock) or delete the test "
        "with the reason in the commit; never add an entry to scripts/test_sleep_allowlist.toml",
        absolute=True,
    ),
    Rule(
        "raw-skip",
        raw_skip,
        'say what the test needs with a capability of tests/capabilities.py (`@needs("symlinks")`), adding one with '
        "its reason when none fits; delete a skip that no lane takes",
    ),
    Rule(
        "dead-capability",
        dead_capability,
        "delete the capability and its uses (no lane of .github/workflows/test.yaml has it, so the tests it guards "
        "always run, or never), or fix the clause or the lane the message names",
        absolute=True,
    ),
    Rule(
        "never-run",
        never_run,
        "delete the test, or the capability that excludes every lane; a test no lane of .github/workflows/test.yaml "
        "runs checks nothing",
        absolute=True,
    ),
)


def _extract(root: Path, ref: str, dest: Path) -> Tree:
    archive = subprocess.run(
        ["git", "-C", str(root), "archive", "--format=tar", ref, PACKAGE],
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")
    return Tree(dest)


def run(tree: Tree, rules: Iterable[Rule], base: Tree | None) -> dict[str, list[Finding]]:
    """Run the rules that can run (the base-relative ones only with a base) and return their findings by rule."""
    return {
        rule.name: sorted(rule.check(tree, base), key=lambda finding: (finding.path, finding.line))
        for rule in rules
        if base is not None or not rule.needs_base
    }


def format_report(rules: Iterable[Rule], findings: dict[str, list[Finding]], notes: dict[str, str]) -> str:
    """Return every site, then the count and fix of each rule (or why it did not run) and what it could not decide."""
    lines = [
        f"{finding.path}:{finding.line}: {finding.rule}: {finding.detail}"
        for found in findings.values()
        for finding in found
    ]
    lines += ["", "rule counts:"]
    for rule in rules:
        if rule.name in findings:
            kind = "absolute" if rule.absolute else "report only" if rule.needs_base else "ratchet"
            lines.append(f"  {rule.name}: {len(findings[rule.name])} ({kind}); fix: {rule.fix}")
            if rule.name in notes:
                lines.append(f"    coverage: {notes[rule.name]}")
        else:
            lines.append(f"  {rule.name}: not run (needs --base)")
    return "\n".join(lines) + "\n"


def format_counts(findings: dict[str, list[Finding]]) -> str:
    """Return the counts as ratchet keys: ``{"source-rule:<rule>": {file: count}}``."""
    counts = {
        f"source-rule:{name}": dict(sorted(Counter(finding.path for finding in found).items()))
        for name, found in findings.items()
    }
    return json.dumps(counts, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Run the source rules and print their report; exit 1 on a site of an absolute rule or an unreadable file."""
    parser = argparse.ArgumentParser(description="Report the code shapes that code reviews keep finding.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="checkout to read")
    parser.add_argument("--base", help="git revision that lost-last-caller compares with")
    parser.add_argument("--rule", action="append", choices=[rule.name for rule in RULES], help="run only this rule")
    parser.add_argument("--count", action="store_true", help="print the counts as JSON ratchet keys")
    args = parser.parse_args(argv)
    rules = [rule for rule in RULES if args.rule is None or rule.name in args.rule]
    tree = Tree(args.root)
    with tempfile.TemporaryDirectory() as temporary:
        base = _extract(args.root, args.base, Path(temporary)) if args.base else None
        findings = run(tree, rules, base)
    notes = {rule.name: rule.coverage(tree) for rule in rules if rule.coverage is not None and rule.name in findings}
    unreadable = tree.unreadable()
    sys.stderr.writelines(f"{path}: the source model refuses the file: {error}\n" for path, error in unreadable.items())
    if args.count:
        sys.stdout.write(format_counts(findings))
        sys.stderr.writelines(f"{name}: coverage: {note}\n" for name, note in notes.items())
    else:
        sys.stdout.write(format_report(rules, findings, notes))
    return int(bool(unreadable) or any(findings.get(rule.name) for rule in RULES if rule.absolute))


if __name__ == "__main__":
    sys.exit(main())
