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
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import cache, cached_property
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._ci_lanes import WORKFLOW, Lane, read_lanes
from scripts._source_model import UNKNOWN, SourceModelError, SourceModule, Unreadable, argument, bases, name_reads
from tests.units import condition_holds, condition_problem

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from scripts._source_model import Binding, Condition, Guard, Import, Reference, Scope

PACKAGE = "src/datamodel_code_generator"
PACKAGE_NAME = "datamodel_code_generator"
GENERATED = "_compiled_templates"
_T = TypeVar("_T")
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
CHECKOUT_INPUTS = ("pyproject.toml", CAPABILITY_HOME)
"""Files this script reads from its own checkout where a counted tree lacks them or they give no answer.

``_floor`` falls back to the ``requires-python`` of that checkout, and ``_table_of`` to its capability table.
"""
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
POSIX_ONLY_CALLS = frozenset({"os.fork", "os.mkfifo", "signal.SIGKILL"})
"""The names that exist on POSIX only, by the origin the source model gives a name or attribute chain."""
POSIX_ONLY_MODULES = frozenset({"resource", "fcntl"})
"""The modules that exist on POSIX only: an import of one is the use."""
PORTABLE_ROOTS = ("scripts", TESTS)
"""The folders whose file names every checkout (Windows, macOS) must be able to hold."""
WINDOWS_REFUSED = re.compile(r'[<>:"|?*\\\x00-\x1f]')
"""The characters Windows refuses in a file name (a backslash is its separator)."""
WINDOWS_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?", re.IGNORECASE)
"""The names Windows reserves for devices, with or without an extension."""
PATH_METHODS = frozenset({"joinpath", "with_name", "with_stem", "with_suffix"})
PATH_ORIGINS = frozenset({"pathlib.Path", "pathlib.PurePath", "os.path.join", "builtins.open"})
_FIXTURE_ORIGIN = "pytest.fixture"
_DRIVE = re.compile(r"[A-Za-z]:")
_PLATFORM_ALTERNATIVE = re.compile(r"platform(?:==|!=)\w+")
_REQUIRES_PYTHON = re.compile(r">=\s*(\d+)\.(\d+)")
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
    def paths(self) -> list[str]:
        """Return every file below ``scripts`` and ``tests`` as a path relative to the root, sorted."""
        found = (
            path.relative_to(self.root).as_posix()
            for top in PORTABLE_ROOTS
            for path in sorted((self.root / top).rglob("*"))
            if path.is_file()
        )
        return [rel for rel in found if self.files is None or rel in self.files]

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
        opened: dict[ast.expr, Scope] = {
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
                unread.setdefault((module.name, binding.name), (module, binding.line))
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
    assert base is not None, "Rule.needs_base: run() calls this rule only with a base"
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
    if isinstance(node, ast.expr) and node in loads:
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
            if isinstance(node, ast.expr | ast.stmt) and (form := _retry_form(node, model, hits, loads)):
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
        loads: list[ast.expr] = [reference.node for reference in model.references if reference.context == "load"]
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

    def lanes_with(self, names: Iterable[str], node: str | None = None) -> list[Lane]:
        """Return the lanes that have every named capability, and run ``node`` if given; an unknown name holds on none.

        A subset lane (a fixed list of tests) runs only what it selects (`Lane.runs`).
        """
        wanted = list(dict.fromkeys(names))
        if any(name not in self.table for name in wanted):
            return []
        clauses = [clause for name in wanted for clause in self.table[name][0]]
        return [
            lane
            for lane in self.lanes
            if all(condition_holds(clause, lane.facts) for clause in clauses) and (node is None or lane.runs(node))
        ]


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
    (lanes that run a fixed list of tests, e.g. the formatter subset) skips no test of the suite. The rule does not
    ask which tests a subset lane lists (`Lane.runs`), so the second case is refused outright.

    The lanes and their facts are read from the workflows, ``tox.ini``, ``pyproject.toml`` and ``uv.lock``
    (`scripts/_ci_lanes.py`); a tree without workflows has no lanes and nothing is judged.

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
    """Report a test, or a ``pytest.param`` of one, whose capabilities together hold on no lane of the CI that runs it.

    A test's capabilities are those its module's and its classes' ``pytestmark`` and decorators name and its own
    decorators; a ``pytest.param`` adds those of its ``marks`` to those of each test that parametrizes over it (a
    param no test's decorator reaches is judged with the module's marks, on a lane that runs some test of the module).
    An unknown capability holds on no lane. A lane that selects only some tests runs only those (`Lane.runs`), so a
    capability that only such a lane has keeps a test it does not list from running anywhere.

    Review class: a test that no lane runs, kept only because its skip hides that.
    """
    if (found := _capabilities(tree)) is None:
        return
    for module in _test_modules(tree):
        model = module.model
        owners: dict[ast.AST, list[tuple[str, list[str]]]] = {}
        for scope in model.scopes:
            if (names := _test_marks(model, scope)) is None:
                continue
            test = f"{module.rel}::{scope.qualname.replace('.', '::')}"
            for decorator in getattr(scope.node, "decorator_list", []):
                for node in _with_referenced_values(model, decorator):
                    owners.setdefault(node, []).append((test, names))
            if names and not found.lanes_with(names, test):
                yield Finding(module.rel, scope.line, "never-run", f"{scope.qualname} needs {', '.join(names)}")
        module_names = _marks_of(model, model.module_scope)
        for node in module.nodes:
            if not (isinstance(node, ast.Call) and model.origin(node.func) == "pytest.param"):
                continue
            marks = next((keyword.value for keyword in node.keywords if keyword.arg == "marks"), None)
            if not (added := _needed(model, marks)):
                continue
            for test, consumer in owners.get(node, [(module.rel, module_names)]):
                if not found.lanes_with([*consumer, *added], test):
                    names = ", ".join(dict.fromkeys([*consumer, *added]))
                    yield Finding(module.rel, node.lineno, "never-run", f"pytest.param needs {names}")
                    break


@cache
def _checkout_table() -> dict[str, tuple[tuple[str, ...], str]]:
    """Return the capability table of the checkout this script belongs to, empty where it has none or cannot read it."""
    return _own_table(Tree(Path(__file__).resolve().parents[1]))


def _table_of(tree: Tree) -> dict[str, tuple[tuple[str, ...], str]]:
    """Return the capability table of a tree, else that of this checkout; empty where the table is unreadable."""
    return _own_table(tree) if (tree.root / CAPABILITY_HOME).is_file() else _checkout_table()


def _own_table(tree: Tree) -> dict[str, tuple[tuple[str, ...], str]]:
    """Return the capability table a tree holds itself, empty where it has none or the table is unreadable."""
    if not (tree.root / CAPABILITY_HOME).is_file():
        return {}
    value = tree.module(CAPABILITY_HOME).model.constant(CAPABILITY_TABLE)
    if isinstance(value, Unreadable) or not isinstance(value, dict) or _table_shape(value) is not None:
        return {}
    return value


def _excludes_windows(table: dict[str, tuple[tuple[str, ...], str]], names: Iterable[str]) -> bool:
    """Return whether the capabilities a test needs together hold on no Windows environment.

    One of their clauses is made of platform alternatives only (``platform!=win32``, ``platform==linux``) and Windows
    fails it. A capability the table lacks excludes nothing.
    """
    return any(
        all(_PLATFORM_ALTERNATIVE.fullmatch(alternative) for alternative in clause.split("|"))
        and not condition_holds(clause, {"platform": "win32"})
        for name in names
        for clause in table.get(name, ((), ""))[0]
    )


def _test_marks(model: SourceModule, scope: Scope) -> list[str] | None:
    """Return the capabilities the marks of a collected test name (its module's, classes' and own), None for no test.

    A function whose name starts with ``test`` inside another function, or a fixture, is no test pytest collects.
    """
    if scope.kind != "function" or not scope.code_name.startswith("test") or _fixture_options(model, scope) is not None:
        return None
    chain, parent = [scope], scope.parent
    while parent is not None and parent.kind in {"class", "module"}:
        chain.append(parent)
        parent = parent.parent
    if parent is not None:
        return None
    return [name for item in reversed(chain) for name in _marks_of(model, item)]


def _decided_by_platform(guard: Guard) -> bool:
    """Return whether a platform test or an ``ImportError`` handler decides that code runs, so it breaks no import."""
    return any(condition.kind in {"platform", "import-error"} for condition in guard)


@dataclass(frozen=True)
class _PosixUse:
    """One use of a name or module that exists on POSIX only: the scope it runs in, its line and what it is."""

    scope: Scope
    line: int
    what: str


def _heads(module: Module) -> dict[ast.AST, Reference]:
    """Return the references of run-time reads by their name node (annotations aside)."""
    return {reference.node: reference for reference in module.model.references if not reference.annotation}


def _chains(module: Module, heads: dict[ast.AST, Reference]) -> Iterator[tuple[ast.expr, Reference]]:
    """Yield every name read and attribute chain read of the module with the reference of its head name."""
    for node in module.nodes:
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            head = node.value
            while isinstance(head, ast.Attribute):
                head = head.value
            if (reference := heads.get(head)) is not None:
                yield node, reference
    for reference in heads.values():
        if reference.context == "load":
            yield reference.node, reference


def _presence_reads(expression: ast.expr, heads: dict[ast.AST, Reference]) -> Iterator[Reference]:
    """Yield the name reads an expression only tests for: ``x``, ``not x``, ``x is None``, ``and``/``or`` of these."""
    if (reference := heads.get(expression)) is not None:
        yield reference
    elif isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
        yield from _presence_reads(expression.operand, heads)
    elif isinstance(expression, ast.BoolOp):
        for value in expression.values:
            yield from _presence_reads(value, heads)
    elif (
        isinstance(expression, ast.Compare)
        and len(expression.ops) == 1
        and isinstance(expression.ops[0], (ast.Is, ast.IsNot, ast.Eq, ast.NotEq))
    ):
        sides = [expression.left, *expression.comparators]
        if any(isinstance(side, ast.Constant) and side.value is None for side in sides):
            yield from (reference for side in sides if (reference := heads.get(side)) is not None)


def _tested(condition: Condition) -> ast.expr | None:
    """Return the expression a condition tests: the test of an ``if`` or a conditional expression, or an operand."""
    node = condition.node
    if isinstance(node, (ast.If, ast.IfExp)):
        return node.test
    return node if isinstance(node, ast.expr) else None


def _presence_tests(module: Module, heads: dict[ast.AST, Reference]) -> set[ast.AST]:
    """Return the name reads the module's tests only check for presence (``if fcntl is None``, ``fcntl and ...``)."""
    tests: list[ast.expr] = []
    for node in module.nodes:
        if isinstance(node, (ast.If, ast.IfExp, ast.Assert)):
            tests.append(node.test)
        elif isinstance(node, ast.BoolOp):
            tests += node.values[:-1]
    return {reference.node for test in tests for reference in _presence_reads(test, heads)}


def _decided_by_name(reference: Reference, heads: dict[ast.AST, Reference]) -> bool:
    """Return whether a test of the name's presence (``if fcntl is not None:``) stands between a read and the module."""
    return any(
        (tested := _tested(condition)) is not None
        and any(read.name == reference.name for read in _presence_reads(tested, heads))
        for condition in reference.guard
    )


def _is_posix_only(origin: str) -> bool:
    """Return whether a dotted path is a closed-list name or a name of a closed-list module."""
    return origin in POSIX_ONLY_CALLS or origin.split(".", maxsplit=1)[0] in POSIX_ONLY_MODULES


def _posix_origin(model: SourceModule, node: ast.expr, head: Reference, loaded: dict[ast.AST, str]) -> str | None:
    """Return the origin of a read (``head`` is its head name's) that is a closed-list name or module's name, else None.

    Every binding of the name counts (`SourceModule.origins`): ``try: import fcntl`` / ``except ImportError:
    fcntl = None`` leaves ``fcntl`` bound twice, and its reads are still reads of the module. So does a name assigned
    a value that holds a literal import of such a module (``loaded``, by call node): ``fcntl =
    importlib.import_module("fcntl") if sys.platform != "win32" else None``.
    """
    parts, part = [], node
    while isinstance(part, ast.Attribute):
        part, parts = part.value, [part.attr, *parts]
    imported = [
        ".".join([loaded[part], *parts])
        for binding in model.resolve(head.node).bindings
        if binding.kind in {"assign", "annassign"} and binding.value is not None
        for part in ast.walk(binding.value)
        if part in loaded
    ]
    return next(filter(_is_posix_only, [*(origin for origin, _ in model.origins(node)), *imported]), None)


def _posix_uses(module: Module) -> list[_PosixUse]:
    """Return the uses of `POSIX_ONLY_CALLS` and `POSIX_ONLY_MODULES` that no platform test or handler decides.

    A name or an attribute chain counts by the origin the model gives it (``from os import fork`` and
    ``import os as o; o.fork`` are ``os.fork``); a string that spells it does not. An import of one of the modules is
    its use, a literal ``importlib.import_module("fcntl")`` too, unless a platform test or an ``ImportError`` handler
    decides it. That guard decides the import and not its readers: a read of the module's name (``resource.getrusage``)
    is a use wherever it stands, unless a platform test decides it or a test of the same name does
    (``if fcntl is not None:``), and a bare test of the name (``fcntl is None``) is no use.
    """
    model = module.model
    found = [
        _PosixUse(item.scope, item.node.lineno, f"import {item.target if item.kind == 'from' else item.source}")
        for item in model.imports
        if isinstance(item.source, str)
        and isinstance(item.target, str)
        and (item.source.split(".")[0] in POSIX_ONLY_MODULES or item.target in POSIX_ONLY_CALLS)
        and not item.typing_only
        and not _decided_by_platform(item.guard)
    ]
    loaded: dict[ast.AST, str] = {
        item.node: item.source
        for item in model.imports
        if item.kind == "dynamic" and isinstance(item.source, str) and _is_posix_only(item.source)
    }
    heads = _heads(module)
    presence = _presence_tests(module, heads)
    longest: dict[ast.AST, tuple[str, int, Scope]] = {}
    for node, reference in _chains(module, heads):
        if (
            (origin := _posix_origin(model, node, reference, loaded)) is None
            or _decided_by_platform(reference.guard)
            or _decided_by_name(reference, heads)
            or node in presence
        ):
            continue
        if reference.node not in longest or len(origin) > len(longest[reference.node][0]):
            longest[reference.node] = (origin, node.lineno, reference.scope)
    found += [_PosixUse(scope, line, origin) for origin, line, scope in longest.values()]
    return found


def _unit_of(module: Module, scope: Scope) -> str | None:
    """Return the top-level name (``module.name``) whose code runs a scope, None for module-level code."""
    while scope.parent is not None and scope.parent.kind != "module":
        scope = scope.parent
    return None if scope.kind == "module" else f"{module.name}.{scope.code_name}"


class _PosixIndex:
    """Where scripts and tests use what exists on POSIX only, and which top-level names reach such a use.

    A top-level function or class reaches a use that runs in its code, or one that a top-level name it reads reaches
    (a call, or a reference passed on), by the origin the model gives the name. A method or an attribute of an object
    whose class the model cannot follow is not followed.
    """

    def __init__(self, modules: list[Module]) -> None:
        self.uses = {module.rel: _posix_uses(module) for module in modules}
        self.units = {
            f"{module.name}.{binding.name}"
            for module in modules
            for binding in module.model.bindings(scope=module.model.module_scope)
            if binding.kind in {"def", "class"} and not binding.guard
        }
        self.reaches: dict[str, str] = {}
        self.edges: dict[str, set[str]] = defaultdict(set)
        for module in modules:
            for use in self.uses[module.rel]:
                if (unit := _unit_of(module, use.scope)) is not None and use.scope.runs_in == "function":
                    self.reaches.setdefault(unit, f"{use.what} in {module.rel}")
            for scope, target in self.reads(module):
                if (unit := _unit_of(module, scope)) is not None and scope.runs_in == "function":
                    self.edges[unit].add(target)
        changed = True
        while changed:
            changed = False
            for unit, targets in self.edges.items():
                if unit not in self.reaches and (hit := next((t for t in sorted(targets) if t in self.reaches), None)):
                    self.reaches[unit] = f"{hit} -> {self.reaches[hit]}"
                    changed = True

    def _target(self, origin: str | None) -> str | None:
        """Return the top-level name an origin leads to (``scripts.x.Cls.method`` leads to ``scripts.x.Cls``)."""
        parts = (origin or "").split(".")
        return next((key for size in range(len(parts), 1, -1) if (key := ".".join(parts[:size])) in self.units), None)

    def reads(self, module: Module) -> list[tuple[Scope, str]]:
        """Return the scope and the top-level name of each read of a module that leads to one (`_posix_uses` guards)."""
        return [
            (reference.scope, target)
            for node, reference in _chains(module, _heads(module))
            if not _decided_by_platform(reference.guard)
            and (target := self._target(module.model.origin(node))) is not None
        ]


def _descendants(scope: Scope) -> Iterator[Scope]:
    """Yield a scope and every scope inside it."""
    yield scope
    for child in scope.children:
        yield from _descendants(child)


@dataclass(frozen=True)
class _Fixture:
    """A pytest fixture: the module and scope that define it, the name tests ask for it by, and if it is autouse."""

    module: Module
    scope: Scope
    name: str
    autouse: bool


def _fixture_options(model: SourceModule, scope: Scope) -> dict[str | None, ast.expr] | None:
    """Return the keywords of a scope's ``pytest.fixture`` decorator (empty when it is not called), None for none."""
    for decorator in getattr(scope.node, "decorator_list", []):
        call = decorator if isinstance(decorator, ast.Call) else None
        if model.origin(call.func if call is not None else decorator) == _FIXTURE_ORIGIN:
            return {keyword.arg: keyword.value for keyword in call.keywords} if call is not None else {}
    return None


def _fixtures(module: Module) -> list[_Fixture]:
    """Return the fixtures a module defines at module level or in a class, by the origin of the decorator.

    The name is the ``name=`` of the decorator, else the function's; ``autouse=True`` makes it apply to every test
    of its module (a class's, of the class) and, in a ``conftest.py``, of its folder.
    """
    model = module.model
    found = []
    for scope in model.scopes:
        if scope.kind != "function" or scope.parent is None or scope.parent.kind not in {"module", "class"}:
            continue
        if (options := _fixture_options(model, scope)) is None:
            continue
        name, autouse = options.get("name"), options.get("autouse")
        found.append(
            _Fixture(
                module,
                scope,
                name.value if isinstance(name, ast.Constant) and isinstance(name.value, str) else scope.code_name,
                isinstance(autouse, ast.Constant) and autouse.value is True,
            )
        )
    return found


def _usefixtures(model: SourceModule, scope: Scope) -> list[str]:
    """Return the fixture names ``pytest.mark.usefixtures`` gives a scope, by decorator and by ``pytestmark``."""
    carriers = list(getattr(scope.node, "decorator_list", []))
    if scope.kind in {"module", "class"}:
        carriers += [binding.value for binding in model.bindings("pytestmark", scope) if binding.value is not None]
    return [
        argument.value
        for carrier in carriers
        for call in ast.walk(carrier)
        if isinstance(call, ast.Call) and model.origin(call.func) == "pytest.mark.usefixtures"
        for argument in call.args
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    ]


def _class_chain(module: Module, scope: Scope, seen: frozenset[Scope] = frozenset()) -> Iterator[Scope]:
    """Yield a class scope and the classes of the same module its bases name, each once."""
    if scope in seen:
        return
    yield scope
    heads = _heads(module)
    for base in bases(scope):
        if (reference := heads.get(base)) is not None:
            for binding in module.model.resolve(reference.node).bindings:
                if binding.kind == "class":
                    yield from _class_chain(module, module.model.scope_defined_by(binding.node), seen | {scope})


def _self_calls(module: Module, scope: Scope) -> Iterator[Scope]:
    """Yield the methods of the scope's class (and of the classes of the module it inherits from) read on ``self``."""
    model = module.model
    receiver = model.receiver(scope)
    if receiver is None or scope.parent is None:
        return
    members: dict[str, list[ast.AST]] = {}
    for owner in _class_chain(module, scope.parent):
        for name, nodes in model.class_members(owner, spelled=True).items():
            members.setdefault(name, nodes)
    receivers = {use.reference.node for use in model.uses(receiver)}
    for node in ast.walk(scope.node):
        if isinstance(node, ast.Attribute) and node.value in receivers:
            for target in members.get(node.attr, []):
                if (method := model.binding_at(target)) is not None and method.kind == "def":
                    yield model.scope_defined_by(target)


class _TestReach:
    """What a collected test reaches that exists on POSIX only: its own code and everything pytest runs for it.

    That is the use in its body and nested scopes, the top-level names it reads (`_PosixIndex`), the methods it calls
    on ``self`` (those of its class and of the classes of its module it inherits from), and the fixtures it gets:
    by parameter, ``usefixtures`` (decorator, class or module ``pytestmark``) and ``autouse``, from its classes, its
    module and the ``conftest.py`` files of its folders (the nearest definition of a name wins), and the fixtures
    those ask for, in turn.
    """

    def __init__(self, tree: Tree, index: _PosixIndex) -> None:
        self.index = index
        self.modules = {module.rel: module for module in _test_modules(tree)}
        self.reads = {module.rel: index.reads(module) for module in self.modules.values()}
        self.fixtures = {rel: _fixtures(module) for rel, module in self.modules.items()}

    def layers(self, module: Module, scope: Scope) -> list[_Fixture]:
        """Return the fixtures a test of ``scope`` sees, far to near: conftest files, the module, its classes."""
        folders = list(Path(module.rel).parents)[::-1]
        conftests = [rel for folder in folders if (rel := (folder / "conftest.py").as_posix()) in self.modules]
        chain = []
        probe = scope.parent
        while probe is not None and probe.kind == "class":
            chain.insert(0, probe)
            probe = probe.parent
        found = [
            fixture
            for rel in [*conftests, module.rel]
            for fixture in self.fixtures[rel]
            if fixture.scope.parent is not None and fixture.scope.parent.kind == "module"
        ]
        return found + [f for owner in chain for f in self.fixtures[module.rel] if f.scope.parent is owner]

    def reach(self, module: Module, scope: Scope, visible: dict[str, _Fixture], seen: set[Scope]) -> str | None:
        """Return the first POSIX-only use the code of ``scope`` reaches and the way there, None if it reaches none."""
        if scope in seen:
            return None
        seen.add(scope)
        owned = set(_descendants(scope))
        for use in self.index.uses[module.rel]:
            if use.scope in owned:
                return f"{use.what} in {module.rel}"
        for owner, target in self.reads[module.rel]:
            if owner in owned and target in self.index.reaches:
                return f"{target} -> {self.index.reaches[target]}"
        for method in _self_calls(module, scope):
            if (inner := self.reach(module, method, visible, seen)) is not None:
                return f"self.{method.code_name} -> {inner}"
        asked = [binding.name for binding in module.model.bindings(scope=scope) if binding.kind == "param"]
        return next(
            filter(None, (self.through(visible[name], visible, seen) for name in asked if name in visible)), None
        )

    def through(self, fixture: _Fixture, visible: dict[str, _Fixture], seen: set[Scope]) -> str | None:
        """Return the way a fixture reaches a POSIX-only use as ``fixture <name> -> <way>``, None if it reaches none."""
        inner = self.reach(fixture.module, fixture.scope, visible, seen)
        return None if inner is None else f"fixture {fixture.name} -> {inner}"

    def test_reach(self, module: Module, scope: Scope) -> str | None:
        """Return what a collected test reaches (see the class): its own code, then the fixtures it is given."""
        layers = self.layers(module, scope)
        visible = {fixture.name: fixture for fixture in layers}
        seen: set[Scope] = set()
        if (found := self.reach(module, scope, visible, seen)) is not None:
            return found
        chain, probe = [scope], scope.parent
        while probe is not None:
            chain.append(probe)
            probe = probe.parent
        asked = [name for item in chain for name in _usefixtures(module.model, item)]
        given = [visible[name] for name in asked if name in visible]
        given += [visible[name] for name in dict.fromkeys(fixture.name for fixture in layers if fixture.autouse)]
        return next(filter(None, (self.through(fixture, visible, seen) for fixture in given)), None)


def posix_only_call(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report a use of what exists on POSIX only that Windows meets: at import, or in a test without a POSIX capability.

    The closed list is `POSIX_ONLY_CALLS` (``os.fork``, ``os.mkfifo``, ``signal.SIGKILL``) and `POSIX_ONLY_MODULES`
    (``resource``, ``fcntl``), read by origin. A use at module or class level, in a script or a test, breaks the import
    on Windows whatever the marks say. In a function it makes the function POSIX-only, and so every top-level function
    or class that reads its name (`_PosixIndex`); a collected test that reaches such a use (`_TestReach`: its code,
    the methods it calls on ``self``, its fixtures, ``conftest.py`` ones included) must need a capability of
    ``tests/capabilities.py`` that Windows lacks (``posix``, ``linux``). A use that a platform test or an
    ``ImportError`` handler decides is no site (`_posix_uses`). A mark on a single ``pytest.param`` is not read.

    Review class: a failure only a Windows lane shows (#7056 round 2: a test of a script that forks its workers).
    """
    table = _table_of(tree)
    modules = [*tree.scripts, *_test_modules(tree)]
    index = _PosixIndex(modules)
    for module in modules:
        for use in index.uses[module.rel]:
            if use.scope.runs_in != "function":
                yield Finding(module.rel, use.line, "posix-only-call", f"{use.what} runs when the module is imported")
    reach = _TestReach(tree, index)
    for module in reach.modules.values():
        model = module.model
        for scope in model.scopes:
            if (names := _test_marks(model, scope)) is None or _excludes_windows(table, names):
                continue
            if (reached := reach.test_reach(module, scope)) is not None:
                yield Finding(module.rel, scope.line, "posix-only-call", f"{scope.qualname} reaches {reached}")


def _requires_python(root: Path) -> tuple[int, int] | None:
    """Return the oldest Python of the ``requires-python`` of ``root``'s ``pyproject.toml``, None where it has none."""
    path = root / "pyproject.toml"
    requires = (
        tomllib.loads(path.read_text(encoding="utf-8")).get("project", {}).get("requires-python", "")
        if path.is_file()
        else ""
    )
    return (int(match[1]), int(match[2])) if (match := _REQUIRES_PYTHON.search(requires)) else None


def _floor(tree: Tree) -> tuple[int, int] | None:
    """Return the oldest Python the tree supports: its ``requires-python``, else that of this checkout, else None."""
    return next(filter(None, map(_requires_python, (tree.root, Path(__file__).resolve().parents[1]))), None)


def floor_syntax(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report a Python file of ``tests`` that parses on this Python and not with the oldest supported grammar.

    Every ``.py`` file under ``tests`` except stored output counts, test data included: a file the oldest Python
    cannot parse breaks collection (a test module) or the run that reads it. The grammar is that of ``ast.parse(...,
    feature_version=<requires-python>)``: it refuses syntax that is newer (type statements and parameters, ``except``
    without parentheses, template strings) and not library features, and it does not know every newer form (PEP 701
    f-strings, PEP 810 ``lazy import``). A file this Python cannot parse at all holds no finding; the oldest Python,
    which reads less, finds the same files or fewer, so a count never rises for another Python. A tree that states no
    ``requires-python``, in a checkout that states none either (a copy run outside a checkout), has no floor to break.

    Review class: a failure only the oldest Python's lane shows (#7030: a fixture with type parameters kept as ``.py``).
    """
    if (floor := _floor(tree)) is None:
        return
    for module in tree.tests:
        text = (tree.root / module.rel).read_text(encoding="utf-8")
        try:
            ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        try:
            ast.parse(text, feature_version=floor)
        except SyntaxError as error:
            yield Finding(
                module.rel, error.lineno or 1, "floor-syntax", f"{error.msg} (oldest supported: {floor[0]}.{floor[1]})"
            )


def unportable_name(part: str, *, whole: bool = True) -> str | None:
    """Return why Windows refuses a file name or the fragment of one, None where it takes it.

    ``whole`` is a complete name (a trailing dot or space and the device names apply); a fragment of an f-string
    (``f"{name}.toml"``) is checked for the characters only.
    """
    if (found := WINDOWS_REFUSED.search(part)) is not None:
        return f"Windows refuses {found.group()!r} in a file name"
    if whole and part.endswith((".", " ")):
        return "Windows drops a trailing dot or space of a file name"
    if whole and WINDOWS_DEVICE.fullmatch(part):
        return "Windows reserves the name for a device"
    return None


def unportable_paths(paths: Iterable[str]) -> list[tuple[str, str]]:
    """Return each ``(path, reason)`` a Windows or macOS checkout cannot hold: a refused name or one that folds.

    A name folds onto another path when both have the same components after Unicode normalization and case folding
    (both file systems are case-insensitive by default and macOS also normalizes), so a checkout merges them. The
    first path in order keeps the spelling; every later spelling of a folded folder or file is a finding.
    """
    found: list[tuple[str, str]] = []
    spelled: dict[tuple[str, ...], str] = {}
    for path in sorted(paths):
        parts = path.split("/")
        if reason := next(filter(None, map(unportable_name, parts)), None):
            found.append((path, reason))
            continue
        for size in range(1, len(parts) + 1):
            key = tuple(unicodedata.normalize("NFC", part).casefold() for part in parts[:size])
            holder = spelled.setdefault(key, "/".join(parts[:size]))
            if holder != "/".join(parts[:size]):
                found.append((path, f"folds onto {holder} where file names are case-insensitive"))
                break
    return found


def _path_operands(module: Module) -> Iterator[tuple[int, ast.expr]]:
    """Yield ``(line, operand)`` for each expression a path is built from.

    The positions are the right operand of ``/``, the arguments of ``Path``/``PurePath``/``os.path.join``, the file
    of ``open`` (by origin; first argument or ``file=``) and the arguments of ``.joinpath``/``.with_name``/
    ``.with_stem``/``.with_suffix``.
    """
    model = module.model
    for node in module.nodes:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            operands = [node.right]
        elif isinstance(node, ast.Call) and (
            (origin := model.origin(node.func)) in PATH_ORIGINS
            or (isinstance(node.func, ast.Attribute) and node.func.attr in PATH_METHODS)
        ):
            opened = argument(node, 0, "file") if origin == "builtins.open" else None
            operands = node.args if origin != "builtins.open" else [opened] if opened not in {None, UNKNOWN} else []
        else:
            continue
        yield from ((node.lineno, operand) for operand in operands)


class _Names:
    """The one resolver of the names a path is built from: what a name may hold, read from the module's bindings.

    A name holds the value of each assignment to it (the paired item of an unpacking), the values of the
    ``parametrize`` that gives a test its parameter (the matching element of each ``pytest.param`` or tuple), and the
    item a ``for`` loop or a comprehension takes from
    a literal list, tuple, set or dict (``items()``, ``keys()`` and ``values()`` too) at the name's position in the
    target (`Assignment.position`). Names bound to names are followed, each once. What a call, a subscript or an
    argument makes is not known.
    """

    def __init__(self, module: Module) -> None:
        self.model = module.model
        self.heads = _heads(module)
        self._active: set[ast.AST] = set()

    def _loaded(self, node: ast.expr) -> Reference | None:
        """Return the reference of a name read, None for any other expression."""
        reference = self.heads.get(node)
        return reference if reference is not None and reference.context == "load" else None

    def _follow(self, name: ast.expr, step: Callable[[ast.expr], list[_T]]) -> list[_T]:
        """Return what ``step`` makes of each expression a name read may hold, none for a name being followed.

        Every walk from a name to what it holds goes through here, so this is the one place that stops a cycle: a
        name read that is being followed holds nothing meanwhile (``for names in names``, ``for d in d.items()``,
        ``a = [b]`` with ``b = [a]``).
        """
        if self._loaded(name) is None or name in self._active:
            return []
        self._active.add(name)
        try:
            return [found for value in self.expressions(name) for found in step(value)]
        finally:
            self._active.discard(name)

    def expressions(self, name: ast.expr) -> list[ast.expr]:
        """Return the expressions a name may hold, one step: its assigned values, parameter values and loop items."""
        if (reference := self._loaded(name)) is None:
            return []
        found: list[ast.expr] = []
        for binding in self.model.resolve(reference.node).bindings:
            assigned = self.model.assignment(binding.node)
            if binding.kind in {"assign", "annassign"} and binding.value is not None:
                found.append(binding.value)
            elif binding.kind in {"assign", "annassign"} and assigned is not None:
                found += [element] if (element := _element(assigned.value, assigned.position)) is not None else []
            elif binding.kind == "param":
                found += self._parametrized(binding)
            elif binding.kind in {"for", "comprehension"} and assigned is not None:
                found += [
                    element
                    for item in self.items(assigned.value)
                    if (element := _element(item, assigned.position)) is not None
                ]
        return found

    def items(self, iterable: ast.expr) -> list[ast.expr]:
        """Return the item expressions a literal collection (or a name bound to one) yields when iterated."""
        if isinstance(iterable, (ast.List, ast.Tuple, ast.Set)):
            return list(iterable.elts)
        if isinstance(iterable, ast.Dict):
            return [key for key in iterable.keys if key is not None]
        if self._loaded(iterable) is not None:
            return self._follow(iterable, self.items)
        if (
            isinstance(iterable, ast.Call)
            and isinstance(iterable.func, ast.Attribute)
            and iterable.func.attr in {"items", "keys", "values"}
            and not iterable.args
        ):
            found: list[ast.expr] = []
            for mapping in self.mappings(iterable.func.value):
                pairs = [(key, value) for key, value in zip(mapping.keys, mapping.values, strict=True) if key]
                found += {
                    "items": [ast.Tuple([key, value], ast.Load()) for key, value in pairs],
                    "keys": [key for key, _ in pairs],
                    "values": [value for _, value in pairs],
                }[iterable.func.attr]
            return found
        return []

    def mappings(self, node: ast.expr) -> list[ast.Dict]:
        """Return the dict displays an expression may be: itself, or those its name may hold."""
        if isinstance(node, ast.Dict):
            return [node]
        if self._loaded(node) is not None:
            return self._follow(node, self.mappings)
        return []

    def _parametrized(self, binding: Binding) -> list[ast.expr]:
        """Return the values ``pytest.mark.parametrize`` gives the test parameter a binding is."""
        found: list[ast.expr] = []
        for decorator in getattr(binding.scope.node, "decorator_list", []):
            if not (isinstance(decorator, ast.Call) and self.model.origin(decorator.func) == "pytest.mark.parametrize"):
                continue
            empty = ast.Tuple([], ast.Load())
            arguments = {"argnames": empty, "argvalues": empty}
            arguments.update(zip(arguments, decorator.args, strict=False))
            arguments.update((keyword.arg, keyword.value) for keyword in decorator.keywords if keyword.arg in arguments)
            names = _argnames(arguments["argnames"])
            if binding.name not in names:
                continue
            position = (names.index(binding.name),)
            whole = len(names) == 1 and isinstance(arguments["argnames"], ast.Constant)
            for item in self.items(arguments["argvalues"]):
                if isinstance(item, ast.Call) and self.model.origin(item.func) == "pytest.param":
                    element = _element(ast.Tuple(list(item.args), ast.Load()), position)
                else:
                    element = item if whole else _element(item, position)
                if element is not None:
                    found.append(element)
        return found

    def strings(self, node: ast.expr) -> list[str]:
        """Return the string constants an expression may take: itself, or what its name or conditional may hold."""
        if isinstance(node, ast.Constant):
            return [node.value] if isinstance(node.value, str) else []
        if isinstance(node, ast.IfExp):
            return [*self.strings(node.body), *self.strings(node.orelse)]
        if self._loaded(node) is not None:
            return self._follow(node, self.strings)
        return []


def _argnames(node: ast.expr) -> list[str]:
    """Return the parameter names a ``parametrize`` call lists: ``"a, b"``, or a list or tuple of strings."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [part.strip() for part in node.value.split(",")]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [item.value for item in node.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)]
    return []


def _element(item: ast.expr, position: tuple[int, ...]) -> ast.expr | None:
    """Return the part of an item that a position leads to, None where the item is no tuple or list there.

    A negative index counts from the end, as for a target after a starred one (`Assignment.position`).
    """
    for index in position:
        if not isinstance(item, (ast.Tuple, ast.List)) or not -len(item.elts) <= index < len(item.elts):
            return None
        item = item.elts[index]
    return item


def _path_literals(module: Module) -> Iterator[tuple[int, str, bool]]:
    """Yield ``(line, text, whole)`` for each string in a path position (`_path_operands`).

    A string a name or an expression may take (`_Names.strings`: the literal itself, a variable, a module constant, a
    ``parametrize`` value, a loop item) is ``whole`` (each of its ``/`` parts is a complete name, a leading drive
    letter aside); the constant parts of an f-string are fragments of names.
    """
    names = _Names(module)
    for line, operand in _path_operands(module):
        if isinstance(operand, ast.JoinedStr):
            yield from (
                (line, part, False)
                for piece in operand.values
                if isinstance(piece, ast.Constant)
                for part in str(piece.value).split("/")
            )
        for text in dict.fromkeys(names.strings(operand)):
            parts = [part for part in text.split("/") if part not in {"", ".", ".."}]
            yield from (
                (line, part, True) for index, part in enumerate(parts) if not (index == 0 and _DRIVE.fullmatch(part))
            )


def _file_name_writers(module: Module) -> dict[str, set[tuple[int | None, str | None]]]:
    """Return the top-level functions that write files named by the entries of a mapping or list they take.

    A ``for`` loop over a parameter (or its ``items()``, ``keys()`` or ``values()``) whose loop variable is read in a
    path position (`_path_operands`), as in ``for name, text in files.items(): (root / f"{name}.toml").write_text()``.
    Each maps to the parameters it names files by, as ``(position, keyword)`` (`_parameter_slot`).
    """
    model = module.model
    positioned = [name for _, operand in _path_operands(module) for name in name_reads(operand)]
    heads = {reference.node: reference for reference in model.references}
    found: dict[str, set[tuple[int | None, str | None]]] = defaultdict(set)
    for binding in model.bindings():
        if (
            binding.kind != "for"
            or binding.scope.kind != "function"
            or (assigned := model.assignment(binding.node)) is None
        ):
            continue
        iterated = assigned.value
        if (
            isinstance(iterated, ast.Call)
            and isinstance(iterated.func, ast.Attribute)
            and iterated.func.attr in ENUMERATING_METHODS
        ):
            iterated = iterated.func.value
        if (reference := heads.get(iterated)) is None:
            continue
        sources = model.resolve(reference.node).bindings
        if (
            sources
            and all(source.kind == "param" for source in sources)
            and any(
                name.id == binding.name and any(found is binding for found in model.resolve(name).bindings)
                for name in positioned
            )
        ):
            found[_unit_of(module, binding.scope) or ""] |= {_parameter_slot(model, source) for source in sources}
    return found


def _parameter_slot(model: SourceModule, parameter: Binding) -> tuple[int | None, str | None]:
    """Return how a call passes a top-level function's parameter: ``(position, keyword)``, or ``(None, None)``.

    The position is the parameter's place among the function's parameters as written; a call gives a keyword-only
    one by its keyword. A parameter of a method or a nested function is ``(None, None)``: every argument may be it.
    """
    if parameter.scope.parent is None or parameter.scope.parent.kind != "module":
        return None, None
    names = [binding.name for binding in model.bindings(scope=parameter.scope) if binding.kind == "param"]
    return names.index(parameter.name), parameter.name


def _writer_arguments(call: ast.Call, slots: set[tuple[int | None, str | None]]) -> list[ast.expr]:
    """Return the arguments of a call that pass the parameters files are named by; every one where that is unknown."""
    every = [*call.args, *(keyword.value for keyword in call.keywords)]
    found: list[ast.expr] = []
    for position, keyword in slots:
        passed = argument(call, position, keyword) if position is not None or keyword is not None else UNKNOWN
        if passed is UNKNOWN:
            return every
        found += [passed] if passed is not None else []
    return found


def _file_name_keys(module: Module) -> Iterator[tuple[int, str]]:
    """Yield ``(line, key)`` for the string keys of the mappings a module passes to a function that names files by them.

    A mapping is the argument a call to such a function (`_file_name_writers`, of the same module) passes for the
    parameter it names files by (every argument where ``*args`` or ``**kwargs`` may pass it), or the value a
    name in an argument may hold (`_Names`), each displayed ``{...}`` at any depth (``f(root, *CASES[case])`` reaches
    every mapping of ``CASES``). A key is a string the resolver reads (a literal, a constant); the key is the file name.
    """
    writers = _file_name_writers(module)
    model = module.model
    if not writers:
        return
    names = _Names(module)
    pending: list[ast.AST] = [
        passed
        for node in module.nodes
        if isinstance(node, ast.Call) and (slots := writers.get(model.origin(node.func) or "")) is not None
        for passed in _writer_arguments(node, slots)
    ]
    seen: set[int] = set()
    while pending:
        node = pending.pop()
        for part in ast.walk(node):
            if id(part) in seen:
                continue
            seen.add(id(part))
            if isinstance(part, ast.Dict):
                yield from ((key.lineno, text) for key in part.keys if key is not None for text in names.strings(key))
            elif isinstance(part, ast.expr):
                pending += names.expressions(part)


def portable_names(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report a path Windows refuses or that another path folds onto, in the file names and the names the code builds.

    A file of ``scripts`` or ``tests`` whose name has a character Windows refuses (``<>:"|?*``, a backslash, a control
    character), ends in a dot or space, is a device name (``con``, ``aux.txt``) or folds onto another path (same
    components after case folding and Unicode normalization). In Python, a string in a path position (`_path_operands`)
    that holds such a name, and a string key of a mapping passed to a function that writes files named by the entries
    of a mapping (`_file_name_keys`: the key is the file name). A name that only a running test builds, from data the
    source does not spell, is not seen, and a backslash in a relative path is a file name on Linux.

    Review class: a failure only a Windows lane shows (#7046: a fixture named ``option:--new-flag``, written as a file).
    """
    for rel, reason in unportable_paths(tree.paths):
        yield Finding(rel, 1, "portable-names", reason)
    for module in (*tree.scripts, *_test_modules(tree)):
        for line, part, whole in _path_literals(module):
            if reason := unportable_name(part, whole=whole):
                yield Finding(module.rel, line, "portable-names", f"{part!r}: {reason}")
        for line, key in _file_name_keys(module):
            for reason in filter(None, map(unportable_name, key.split("/"))):
                yield Finding(module.rel, line, "portable-names", f"key {key!r} names a file: {reason}")


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
        "delete the capability and its uses (no lane of a workflow matrix in .github/workflows has it, so the tests it "
        "guards always run, or never), or fix the clause or the lane the message names",
        absolute=True,
    ),
    Rule(
        "never-run",
        never_run,
        "delete the test, or the capability that excludes every lane that runs it, or list the test in a lane that has "
        "the capability; a test no lane of a workflow matrix in .github/workflows runs checks nothing",
        absolute=True,
    ),
    Rule(
        "posix-only-call",
        posix_only_call,
        'mark the test `@needs("posix")` (or `linux`), or move the POSIX-only use out of module level and behind a '
        "platform test; a script that forks keeps its tests behind the capability",
    ),
    Rule(
        "floor-syntax",
        floor_syntax,
        "store the file as `<name>.py.txt` and copy it where a test reads it on a Python that parses it, or write it "
        "in the oldest supported grammar",
        absolute=True,
    ),
    Rule(
        "portable-names",
        portable_names,
        'rename the file or the name (no `<>:"|?*`, no trailing dot or space, no device name, no two paths that '
        "differ only in case)",
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
