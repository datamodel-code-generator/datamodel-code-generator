"""Check the source tree for the code shapes that code reviews keep finding, one rule per shape (report only).

usage: python scripts/check_source_rules.py [--root DIR] [--base REF] [--rule NAME]... [--count]

Every rule reads the tree with ``ast`` or ``tokenize`` (standard library only) and reports each site as
``path:line: rule: detail``; the summary gives the count of every rule and how to fix its sites. The counts are
ratchet keys ``source-rule:<rule>`` (``--count`` prints them as JSON, ``{key: {file: count}}``): a change may keep or
lower a count. A rule is marked absolute once its count is 0; the exit status is 1 only when an absolute rule reports
a site.

``lost-last-caller`` compares the tree with ``--base`` (a git revision of the same repository) and runs only with it.
It cannot tell from the source whether tests reach every branch of a callable, so it reports each candidate with the
command that shows its coverage, and every run prints how many public callables it cannot decide (``coverage:``; on
stderr with ``--count``).
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
import tokenize
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

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
UNUSED_IMPORT = ("F401", "unused-import")
_CODES = r"(?::\s*(?P<codes>[A-Z0-9]+(?:[\s,]+[A-Z0-9]+)*))?"
_NOQA = re.compile(rf"#\s*noqa(?![\w-]){_CODES}", re.IGNORECASE)
_FILE_NOQA = re.compile(rf"#\s*(?:ruff|flake8):\s*noqa(?![\w-]){_CODES}")
_RUFF_RULES = re.compile(r"#\s*ruff:\s*(?P<action>ignore|disable|enable)\[(?P<rules>[^\]]*)\]")
_FILE_CITATION = re.compile(r"`([A-Za-z_][\w/]*\.py):([A-Za-z_][\w.]*)")
_CLASS_CITATION = re.compile(r"`([A-Z][A-Za-z0-9_]*)\.([A-Za-z_]\w*)")
_WORD = re.compile(r"\w+")
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
    """One Python file of the tree, parsed once."""

    def __init__(self, root: Path, rel: str) -> None:
        """Read and parse ``rel`` below ``root``."""
        self.rel = rel
        self.text = (root / rel).read_text(encoding="utf-8")
        self.tree = ast.parse(self.text)

    @cached_property
    def name(self) -> str:
        """Return the dotted module name of a package file."""
        parts = list(Path(self.rel).relative_to("src").with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    @cached_property
    def is_package(self) -> bool:
        """Return whether the file is the ``__init__.py`` of a package."""
        return self.rel.endswith("/__init__.py")

    @cached_property
    def lines(self) -> list[str]:
        """Return the lines of the file."""
        return self.text.splitlines()

    @cached_property
    def nodes(self) -> list[ast.AST]:
        """Return every node of the file, in ``ast.walk`` order."""
        return list(ast.walk(self.tree))

    @cached_property
    def bindings(self) -> _Bindings:
        """Return which scope binds each name the file reads."""
        return _Bindings(self.tree)

    @cached_property
    def comments(self) -> dict[int, str]:
        """Return the comments of the file by line."""
        found: dict[int, str] = {}
        for token in tokenize.generate_tokens(io.StringIO(self.text).readline):
            if token.type == tokenize.COMMENT:
                found[token.start[0]] = token.string
        return found


class Tree:
    """The files of a checkout that the rules read."""

    def __init__(self, root: Path) -> None:
        """Index the tree at ``root``."""
        self.root = root
        self._parsed: dict[str, Module] = {}

    def _modules(self, top: str) -> list[Module]:
        return [
            Module(self.root, path.relative_to(self.root).as_posix())
            for path in sorted((self.root / top).rglob("*.py"))
            if GENERATED not in path.parts
        ]

    @cached_property
    def package(self) -> list[Module]:
        """Return the modules of the package, generated template renderers aside."""
        return self._modules(PACKAGE)

    @cached_property
    def scripts(self) -> list[Module]:
        """Return the repository scripts."""
        return self._modules("scripts")

    @cached_property
    def words(self) -> dict[str, frozenset[str]]:
        """Return the words of every Python file that can import the package (test data aside) by path."""
        found: dict[str, frozenset[str]] = {}
        for top in ("src", "scripts", "tests"):
            for path in sorted((self.root / top).rglob("*.py")):
                rel = path.relative_to(self.root).as_posix()
                if not rel.startswith("tests/data/"):
                    found[rel] = frozenset(_WORD.findall(path.read_text(encoding="utf-8", errors="replace")))
        return found

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


def _leaves(target: ast.expr) -> Iterator[ast.expr]:
    """Yield the names, attributes and subscripts a target assigns, those inside tuples, lists and ``*`` included."""
    if isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _leaves(element)
    elif isinstance(target, ast.Starred):
        yield from _leaves(target.value)
    else:
        yield target


def _targets(node: ast.AST) -> list[ast.expr]:
    """Return what an assignment statement assigns, destructuring unpacked (nothing for any other node)."""
    if isinstance(node, ast.Assign):
        return [leaf for target in node.targets for leaf in _leaves(target)]
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        return list(_leaves(node.target))
    return []


def _cuts_at_hash(call: ast.Call) -> bool:
    """Return whether ``call`` cuts at ``#``: ``s.split("#")``, ``str.split(s, "#")`` or ``s.split(sep="#")``."""
    if not isinstance(call.func, ast.Attribute) or call.func.attr not in CUT_METHODS:
        return False
    unbound = isinstance(call.func.value, ast.Name) and call.func.value.id == "str"
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
            if isinstance(node, ast.Call) and _cuts_at_hash(node):
                yield Finding(module.rel, node.lineno, "hash-split", ast.unparse(node))


class _CitationIndex:
    """Top-level names and class members of every module of the package."""

    def __init__(self, modules: Iterable[Module]) -> None:
        self.top: dict[str, set[str]] = {}
        self.classes: dict[str, dict[str, set[str]]] = {}
        for module in modules:
            rel = module.rel.removeprefix(f"{PACKAGE}/")
            names: set[str] = set()
            classes: dict[str, set[str]] = {}
            for node in module.tree.body:
                names |= _bound(node)
                if isinstance(node, ast.ClassDef):
                    classes[node.name] = _members(node)
            self.top[rel], self.classes[rel] = names, classes

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


def _bound(node: ast.stmt) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        return {target.id for target in _targets(node) if isinstance(target, ast.Name)}
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return {(alias.asname or alias.name).split(".")[0] for alias in node.names}
    if isinstance(node, ast.If):  # TYPE_CHECKING blocks, version branches
        return set().union(*(_bound(child) for child in (*node.body, *node.orelse)))
    return set()


def _members(node: ast.ClassDef) -> set[str]:
    members: set[str] = set()
    for item in node.body:
        members |= _bound(item)
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            members |= {
                sub.attr
                for sub in ast.walk(item)
                if isinstance(sub, ast.Attribute)
                and isinstance(sub.ctx, ast.Store)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "self"
            }
    return members


def doc_citations(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report a backticked ``path.py:Name[.attr]`` or ``Class.attr`` of the architecture page that names no code.

    Review class: docs or a script citing removed code.
    """
    index = _CitationIndex(tree.package)
    text = (tree.root / ARCHITECTURE_DOCS).read_text(encoding="utf-8")
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


def _names_rule(codes: str | None, rule: tuple[str, str]) -> bool:
    """Return whether the code list of a ``noqa`` comment (None: no list, every rule) names ``rule``."""
    return codes is None or rule[0] in re.split(r"[\s,]+", codes.upper())


def _next_code_line(module: Module, line: int) -> int:
    """Return the first line after ``line`` that holds code (``line`` itself when none follows)."""
    for number in range(line + 1, len(module.lines) + 1):
        if (text := module.lines[number - 1].strip()) and not text.startswith("#"):
            return number
    return line


def _rule_items(match: re.Match[str]) -> tuple[str, ...]:
    return tuple(item.strip() for item in match["rules"].split(","))


def _range_end(module: Module, line: int, listed: tuple[str, ...]) -> int:
    """Return the line that ends a ``ruff: disable`` at ``line``.

    That is an ``enable`` at the same indentation with the identical list of rules in the same order (ruff 0.17.4
    ends a range on nothing else), or the end of the block or of the file.
    """
    indent = len(module.lines[line - 1]) - len(module.lines[line - 1].lstrip())
    for number in range(line + 1, len(module.lines) + 1):
        text = module.lines[number - 1]
        if not (stripped := text.lstrip()):
            continue
        column = len(text) - len(stripped)
        if not stripped.startswith("#"):
            if column < indent:
                return number
        elif column == indent and any(
            match["action"] == "enable" and _rule_items(match) == listed for match in _RUFF_RULES.finditer(stripped)
        ):
            return number
    return len(module.lines) + 1


def suppressed_lines(module: Module, rule: tuple[str, str]) -> frozenset[int]:
    """Return the lines on which ruff does not report ``rule`` (its code and its name), from every comment form.

    The forms: ``# noqa`` (bare or naming the code) on the line; ``# ruff: ignore[...]`` naming the code or the name,
    on the line or on its own line before it; ``# ruff: disable[...]`` up to its ``enable`` or the end of its block;
    a file-level ``# ruff: noqa`` or ``# flake8: noqa`` (bare or naming the code) on its own line.
    """
    lines: set[int] = set()
    for number, comment in module.comments.items():
        own_line = module.lines[number - 1].lstrip().startswith("#")
        if (match := _NOQA.search(comment)) is not None and _names_rule(match["codes"], rule):
            lines.add(number)
        if own_line and (match := _FILE_NOQA.match(comment)) is not None and _names_rule(match["codes"], rule):
            return frozenset(range(1, len(module.lines) + 1))
        for match in _RUFF_RULES.finditer(comment):
            if not set(rule) & set(listed := _rule_items(match)):
                continue
            if match["action"] == "ignore":
                lines.add(_next_code_line(module, number) if own_line else number)
            elif match["action"] == "disable" and own_line:
                lines.update(range(number, _range_end(module, number, listed)))
    return frozenset(lines)


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


class _Scope(NamedTuple):
    node: ast.AST
    names: frozenset[str]
    is_class: bool


def _own_names(
    scope: ast.FunctionDef
    | ast.AsyncFunctionDef
    | ast.Lambda
    | ast.ClassDef
    | ast.ListComp
    | ast.SetComp
    | ast.DictComp
    | ast.GeneratorExp,
) -> frozenset[str]:
    """Return the names a function, lambda, class or comprehension binds in its own scope."""
    names: set[str] = set()
    todo: list[ast.AST] = []
    if isinstance(scope, _FUNCTIONS):
        arguments = scope.args
        names |= {arg.arg for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)}
        names |= {arg.arg for arg in (arguments.vararg, arguments.kwarg) if arg is not None}
        todo += scope.body if isinstance(scope.body, list) else [scope.body]
    elif isinstance(scope, ast.ClassDef):
        todo += scope.body
    else:
        todo += [generator.target for generator in scope.generators]
    declared: set[str] = set()
    while todo:
        node = todo.pop()
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            continue
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            declared.update(node.names)
        if not isinstance(node, (*_FUNCTIONS, *_COMPREHENSIONS)):
            todo += ast.iter_child_nodes(node)
    return frozenset(names - declared)


class _Bindings:
    """Which scope binds each name a file reads, and the scope of each import: the one resolver of the rules.

    A scope is the module tree, a function, a lambda, a class or a comprehension. A name that no enclosing function
    scope (nor the class body that reads it) binds belongs to the module.
    """

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.reads: set[tuple[ast.AST, str]] = set()
        self.scope_of: dict[ast.AST, ast.AST] = {}
        self._visit(tree, (_Scope(tree, frozenset(), is_class=False),))

    def global_reads(self) -> set[str]:
        """Return the names the file reads as module-level bindings."""
        return {name for scope, name in self.reads if scope is self.tree}

    def import_is_read(self, node: ast.Import | ast.ImportFrom, name: str) -> bool:
        """Return whether the scope that holds the import ``node`` reads the ``name`` it binds."""
        return (self.scope_of[node], name) in self.reads

    def _enter(
        self,
        node: ast.FunctionDef
        | ast.AsyncFunctionDef
        | ast.Lambda
        | ast.ClassDef
        | ast.ListComp
        | ast.SetComp
        | ast.DictComp
        | ast.GeneratorExp,
        chain: tuple[_Scope, ...],
    ) -> None:
        """Visit what a scope node evaluates in the scope around it, then its body in its own scope."""
        inner = (*chain, _Scope(node, _own_names(node), isinstance(node, ast.ClassDef)))
        outside: list[ast.AST | None] = []
        body: list[ast.AST] = []
        if isinstance(node, _FUNCTIONS):
            arguments = node.args
            outside += [*arguments.defaults, *arguments.kw_defaults]
            if not isinstance(node, ast.Lambda):
                outside += [*node.decorator_list, node.returns]
                outside += [arg.annotation for arg in ast.walk(arguments) if isinstance(arg, ast.arg)]
            body += node.body if isinstance(node.body, list) else [node.body]
        elif isinstance(node, ast.ClassDef):
            outside += [*node.decorator_list, *node.bases, *(keyword.value for keyword in node.keywords)]
            body += node.body
        else:
            first, *later = node.generators
            outside.append(first.iter)
            body += [first.target, *first.ifs]
            for generator in later:
                body += [generator.target, generator.iter, *generator.ifs]
            body += [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        for item in outside:
            if item is not None:
                self._visit(item, chain)
        for item in body:
            self._visit(item, inner)

    def _visit(self, node: ast.AST, chain: tuple[_Scope, ...]) -> None:
        if isinstance(node, (*_FUNCTIONS, *_COMPREHENSIONS, ast.ClassDef)):
            self._enter(node, chain)
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            self.reads.add((self._scope(node.id, chain), node.id))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            self.scope_of[node] = chain[-1].node
        for child in ast.iter_child_nodes(node):
            self._visit(child, chain)

    @staticmethod
    def _scope(name: str, chain: tuple[_Scope, ...]) -> ast.AST:
        for index in range(len(chain) - 1, 0, -1):
            if name in chain[index].names and (not chain[index].is_class or index == len(chain) - 1):
                return chain[index].node
        return chain[0].node


def private_reexport(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report an import of a private name that only a suppression of unused imports keeps: a private re-export.

    Every comment form counts (``suppressed_lines``), on the name's line or on the first line of its statement. An
    import that its own scope reads is no re-export.

    Review class: a copy of the step's decision left outside the routed sites (a second reader through an old path).
    """
    for module in tree.package:
        suppressed: frozenset[int] | None = None
        for node in module.nodes:
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for alias in node.names:
                bound = (alias.asname or alias.name).split(".")[0]
                if not bound.startswith("_") or bound.startswith("__"):
                    continue
                if suppressed is None:
                    suppressed = suppressed_lines(module, UNUSED_IMPORT)
                if {node.lineno, alias.lineno} & suppressed and not module.bindings.import_is_read(node, bound):
                    yield Finding(module.rel, alias.lineno, "private-reexport", bound)


def _parents(module: ast.Module) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(module) for child in ast.iter_child_nodes(node)}


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    while (node := parents.get(node)) is not None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return None


def _enumerates(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> bool:
    parent = parents.get(call)
    if isinstance(parent, ast.Attribute):
        return parent.attr in ENUMERATING_METHODS
    return isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension, ast.Starred, ast.Call))


def globals_registry(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report ``globals()`` enumerated as a registry, outside a module ``__getattr__`` or ``__dir__``.

    Review class: a CodeQL alert group (every entry of such a registry reads as an unused global).
    """
    for module in (*tree.package, *tree.scripts):
        calls = [
            node
            for node in module.nodes
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "globals"
        ]
        parents = _parents(module.tree) if calls else {}
        for node in calls:
            if _enumerates(node, parents) and _enclosing_function(node, parents) not in NAMESPACE_HOOKS:
                yield Finding(module.rel, node.lineno, "globals-registry", module.lines[node.lineno - 1].strip())


def _dotted(node: ast.expr) -> str | None:
    """Return ``a.b.c`` for a chain of attribute reads on a name (None for any other expression)."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return ".".join([node.id, *reversed(parts)])


def _module_reads(module: Module, names: set[str]) -> set[tuple[str, str]]:
    """Return the ``(module, name)`` pairs of ``names`` that ``module`` reads from a module by its name.

    A read is an import of the name (``from m import _x``), an attribute read on an imported module (``import m``
    then ``m._x``) or a string holding its dotted path (``"m._x"``, ``"m:_x"``).
    """
    reads: set[tuple[str, str]] = set()
    bound: dict[str, set[str]] = defaultdict(set)
    for node in module.nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                head = alias.name.split(".")[0]
                bound[alias.asname or head].add(alias.name if alias.asname else head)
        elif isinstance(node, ast.ImportFrom) and (not node.level or module.rel.startswith("src/")):
            source = _from_module(module, node)
            for alias in node.names:
                bound[alias.asname or alias.name].add(f"{source}.{alias.name}")
                reads.add((source, alias.name))
    for node in module.nodes:
        if (
            isinstance(node, ast.Attribute)
            and node.attr in names
            and not isinstance(node.ctx, ast.Store)
            and (chain := _dotted(node.value)) is not None
        ):
            head, _, rest = chain.partition(".")
            reads |= {(".".join(filter(None, (target, rest))), node.attr) for target in bound.get(head, ())}
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and (path := _STRING_PATH.fullmatch(node.value)) is not None
        ):
            reads.add((path["module"], path["name"]))
    return reads


def unused_private_global(tree: Tree, _base: Tree | None) -> Iterator[Finding]:
    """Report a module-level private name (``_NAME = ...``) that nothing which can reach it reads.

    What can reach it: a read in its module, and from any other file of the package, the scripts or the tests, an
    import of it, an attribute read on its module or a string holding its dotted path. The same name in another module
    is another global.

    Review class: dead code left by a move (a CodeQL unused global).
    """
    unread: dict[tuple[str, str], tuple[Module, int]] = {}
    for module in tree.package:
        loads = module.bindings.global_reads()
        for node in module.tree.body:
            for target in _targets(node):
                if (
                    isinstance(target, ast.Name)
                    and target.id.startswith("_")
                    and not target.id.startswith("__")
                    and target.id not in loads
                ):
                    unread.setdefault((module.name, target.id), (module, node.lineno))
    names = {name for _, name in unread}
    for rel, words in tree.words.items():
        if names & words:
            for read in _module_reads(tree.module(rel), names):
                unread.pop(read, None)
    for (_, name), (module, line) in unread.items():
        yield Finding(module.rel, line, "unused-private-global", name)


def _from_module(module: Module, node: ast.ImportFrom) -> str:
    """Return the dotted name of the module ``from ... import`` reads, relative forms resolved."""
    base = node.module or ""
    if not node.level:
        return base
    package = module.name.split(".") if module.is_package else module.name.split(".")[:-1]
    package = package[: len(package) - (node.level - 1)]
    return ".".join([*package, base] if base else package)


def _import_targets(module: Module, node: ast.Import | ast.ImportFrom, names: set[str]) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    base = _from_module(module, node)
    return [candidate if (candidate := f"{base}.{alias.name}") in names else base for alias in node.names]


class _ImportGraph:
    """The module-level imports of the package outside any ``if`` and the function-level ones."""

    def __init__(self, modules: list[Module]) -> None:
        self.names = {module.name for module in modules}
        self.edges: dict[str, set[str]] = defaultdict(set)
        self.local: list[tuple[Module, ast.Import | ast.ImportFrom, list[str]]] = []
        for module in modules:
            self._visit(module, module.tree, in_function=False, guarded=False)
        self._reach: dict[str, set[str]] = {}

    def _visit(self, module: Module, node: ast.AST, *, in_function: bool, guarded: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                targets = [target for target in _import_targets(module, child, self.names) if target in self.names]
                if in_function:
                    self.local.append((module, child, targets))
                elif not guarded:
                    for target in targets:
                        self.edges[module.name].add(target)
                        self.edges[module.name].update(self._parents(target))
                continue
            self._visit(
                module,
                child,
                in_function=in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)),
                guarded=guarded or isinstance(child, ast.If),
            )

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
        for node in module.tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) or node.name.startswith("_"):
                continue
            found[module.rel, node.name] = (module.name, node.lineno, node.end_lineno or node.lineno)
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                        found[module.rel, f"{node.name}.{item.name}"] = (
                            module.name,
                            item.lineno,
                            item.end_lineno or item.lineno,
                        )
    return found


def _references(modules: Iterable[Module]) -> dict[str, list[tuple[str, int]]]:
    """Return where the package reads each name: as a name, an attribute or a dotted path in a string.

    The entries of ``__all__`` are not reads.
    """
    found: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for module in modules:
        exports = {
            id(element)
            for node in module.tree.body
            if any(isinstance(target, ast.Name) and target.id == "__all__" for target in _targets(node))
            for element in ast.walk(node)
        }
        for node in module.nodes:
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                found[node.id].append((module.rel, node.lineno))
            elif isinstance(node, ast.Attribute) and not isinstance(node.ctx, ast.Store):
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
        node.name
        for module in tree.package
        for node in module.nodes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
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
    """Run the source rules and print their report; exit 1 when an absolute rule reports a site."""
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
    if args.count:
        sys.stdout.write(format_counts(findings))
        sys.stderr.writelines(f"{name}: coverage: {note}\n" for name, note in notes.items())
    else:
        sys.stdout.write(format_report(rules, findings, notes))
    return int(any(findings.get(rule.name) for rule in RULES if rule.absolute))


if __name__ == "__main__":
    sys.exit(main())
