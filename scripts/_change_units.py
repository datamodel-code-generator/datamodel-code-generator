"""What a candidate changes in the package since a base revision, as the code objects a comparison has to run.

The comparator's evidence (`scripts/compare_release.py`, `Evidence`) is counted by code object: a module body, a class
body, a function, a lambda or a generator expression of a changed file, on the side where the change is. What the
candidate adds is measured on the head (its runs), what it removes on the base (the base's runs of the same cells).

From a change to its units (`changes`, then `map_units`):

- **Changed tokens, not lines.** Per hunk of ``git diff -U0 -M``, the code tokens of its removed lines and of its added
  lines, each keyed by its type, its text and the indentation of its statement, are compared with a sequence matcher:
  the tokens in the parts that differ are removed (base) or added (head). A comment or a blank line has no code token;
  an unchanged part of a changed line needs nothing; a re-indented block changes every token in it.
- **Moves** pair by statement: a module- or class-level statement whose code lines are all removed on base and one
  whose code lines are all added on head pair when their text is equal (`ast.dump`), their binding key is equal (the
  qualname of a ``def`` or ``class``, else the enclosing qualname, the names bound and the kind of statement) and every
  free name resolves to corresponding bindings on the two sides (`SourceTree.definition`: builtins by name, outside
  modules by dotted path, package bindings by name in paired modules, or bound by statements that pair themselves),
  as a greatest fixpoint. A class-level statement pairs only inside the same class (or one that pairs itself) and
  only where it keeps its place among the binding statements of the class body (the order of its fields). Paired
  statements drop out on both sides. A renamed file (``-M``) keeps its pair diff, and
  each of its unchanged statements whose free names no longer correspond counts as added. A copy is new code.
- **Owner and probes** (`map_units`, on the interpreter that measures, which compiles the code objects): each changed
  token belongs to the innermost scope that holds it, lifted to the nearest one with a code object. A token in a
  header (decorators, signature, bases; a lambda's parameters; a generator expression's first iterable) needs the
  scope to start and the code that evaluates it to run there; any other token needs one of the owner's value
  instructions inside the innermost expression that holds it, never outside its `Segment` (a part that runs as a
  unit; a ``case`` after the first is one, so is a case's guard). A statement with no value instruction falls back
  to any instruction, then to the owner's start; a case with none (a wildcard) to its body; any other part of a
  statement with none is unmeasurable, and its unit can never pass.
- **Module and class bodies** run as their module is imported. A token of a statement there that binds names needs
  its own probe (which the import runs, unless the token is in a part the import skipped) and a reader of a name the
  token binds to run (`target_names`; a reader is an instruction that loads it, `Readers`); a binding no reader reads
  counts at import, and its unit says ``run at import only``. A change of a class as a whole (its header, the order
  of its binding statements, its compiled body) needs a reader of the class name.
- **Typing-only forms** give no unit (`SourceModule.typing_only`): ``TYPE_CHECKING`` bodies, ``overload`` stubs,
  ``override``/``final`` decorators and function-local annotations; function annotations under the future import only
  while the package reads no function annotations at run time beyond `READER_SITES`. A function's docstring gives
  none while the package reads no docstring beyond `DOC_READER_SITES`.
- **Compile-time effects**: code objects of a file on both sides, paired by qualname and ordinal, whose normalized
  code differs while neither side has a changed token in them, need to run on both sides: a function its start, a
  class body a reader of the class, a module body its import and a reader of a name its differing statements bind.
- **Files that run as text**: a changed ``resources/*.json``, a ``.jinja2`` template without a compiled renderer and
  any file under ``_runtime/`` need a compared run to read them (a ``read`` probe). A ``_runtime`` module no other
  module of the package imports is only copied into generated packages: it has the ``read`` probe alone.
- **A removed private definition nothing reads**: a module-level private ``def`` or ``class`` the base removes, whose
  name occurs nowhere else in the base package, whose definition registers it nowhere (no decorator but typing ones,
  no base but ``object``, no keyword) and whose module the package reads through no namespace, needs no run; it reads
  ``removed, unreferenced on base``. Methods are not proven this way.

Run as a script by the interpreter that measures: it reads ``{"repo", "base", "candidate", "roots"}`` as JSON on stdin
and writes the units of both sides as JSON (`Unit`).
"""

from __future__ import annotations

import ast
import dis
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._source_model import SourceModule, SourceTree

try:
    from scripts import compile_builtin_templates as compiler
except ImportError:
    compiler = None

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from types import CodeType

    from scripts._source_model import Import, Reference, Scope

PACKAGE = "datamodel_code_generator"
PACKAGE_SOURCE = f"src/{PACKAGE}"
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
"""A hunk of ``git diff -U0``: the first removed line, their count, the first added line, their count."""
SIDES = ("base", "head")
NOT_EVIDENCE = frozenset({
    "NOT_TAKEN",
    "NOP",
    "RESUME",
    "CACHE",
    "EXTENDED_ARG",
    "END_FOR",
    "POP_ITER",
    "RETURN_GENERATOR",
})
"""Instructions that carry the position of the code they jump around or set up, run whether or not it runs."""
JUMPS = frozenset(getattr(dis, "hasjump", ())) | frozenset(dis.hasjrel) | frozenset(dis.hasjabs)
NO_POSITION = frozenset({"NOP", "RESUME", "CACHE", "EXTENDED_ARG"})
"""Instructions that are no evidence even where a statement has nothing else."""
NAMESPACE_SITES = frozenset({"globals", "vars", "locals", "exec", "setattr-module", "star-import"})
"""The dynamic sites of a module (`SourceModule.dynamic_sites`) that can reach any of its names."""
ANNOTATION_READERS = frozenset({
    "typing.get_type_hints",
    "typing_extensions.get_type_hints",
    "inspect.signature",
    "inspect.get_annotations",
    "functools.singledispatch",
    "functools.singledispatchmethod",
})
"""What evaluates the annotations of a function at run time; ``annotationlib.*`` and ``__annotations__`` reads too."""
ANNOTATION_NAMES = frozenset({"__annotations__", "__annotate__"})
READER_SITES = {
    ("input_model.py", "_get_type_hints_safe"): "the type hints of a user's model class (--input-model)",
    ("_option_table.py", "_dataclass_arguments"): "the keys of the DataclassArguments TypedDict",
    ("_target_binding.py", "<module>"): "the keys of the DataclassArguments TypedDict",
    ("model/base.py", "<module>"): "the keys of the DataclassArguments TypedDict",
    ("_runtime/server/application.py", "_signature"): "the signatures of a generated server's handlers",
    ("_runtime/client/tools.py", "callable_arguments"): "the signatures of a generated client's meta tools",
    ("_python_type_annotation.py", "render_python_type_expr"): "the text of generated code",
    ("_records.py", "_plain"): "a record class's namespace",
    ("_records.py", "_build"): "a record class's namespace",
    ("input_model.py", "_model_field_annotations"): "the annotations of a user's model class (--input-model)",
    ("model/field_name.py", "<module>"): "the names a pydantic BaseModel reserves",
}
"""The annotation reader sites of the package that read class annotations only, by path below it and qualname.

While every reader site is one of these, a function's annotations under the future import are strings nothing
evaluates, so a change to them needs no run. A site not listed here turns that off for the whole comparison."""
DOC_READER_SITES = {
    ("_records.py", "_plain"): "a record class's namespace",
    ("model/field_name.py", "<module>"): "the names a pydantic BaseModel reserves",
}
"""The docstring reader sites of the package that read no function's docstring, by path below it and qualname.

While every reader site is one of these, a function's docstring is text nothing reads, so a change to it needs no
run. A site not listed here turns that off for the whole comparison."""
Position = tuple[int, int]
Positioned = tuple[Position, Position, int, bool]
"""An instruction's start, end, offset and whether it is evidence (`Code.instructions`)."""


@dataclass(frozen=True)
class Sites:
    """What reads a kind of compile-time text at run time (`reader_sites`).

    The words find the modules to look into; the readers by origin, the modules whose every name reads it and the
    names that hold the text are the sites.
    """

    words: re.Pattern[str]
    readers: frozenset[str]
    modules: tuple[str, ...]
    names: frozenset[str]


ANNOTATIONS = Sites(
    re.compile(r"get_type_hints|signature|get_annotations|singledispatch|annotationlib|__annotat"),
    ANNOTATION_READERS,
    ("annotationlib.",),
    ANNOTATION_NAMES,
)
DOCSTRINGS = Sites(
    re.compile(r"__doc__|getdoc|pydoc"), frozenset({"inspect.getdoc"}), ("pydoc.",), frozenset({"__doc__"})
)


@dataclass
class FileChange:
    """A file of the package the candidate changes: its path on each side (None: absent) and the lines of each hunk."""

    base: str | None
    head: str | None
    hunks: list[tuple[range, range]] = field(default_factory=list)
    binary: bool = False

    @property
    def renamed(self) -> bool:
        """Whether the file moved: it is on both sides under different paths."""
        return self.base is not None and self.head is not None and self.base != self.head


@dataclass
class Probe:
    """What must run, on its side, for a unit to count: one of its kinds.

    ``start`` (the code object starts), ``instr`` (one of ``offsets`` of the code object runs), ``read`` (the file is
    opened outside the import system), ``readers`` (one of ``readers`` runs, each an ``instr`` probe that may also need,
    with ``chain``, its instruction to run while the object that name and attribute chain names finds the attribute
    in the class ``receiver``), ``unmeasurable``.
    ``code`` keys a code object: path below the package, ``co_qualname``, ``co_firstlineno`` and the position of its
    first instruction. ``where`` is the source span the probe stands for.
    """

    kind: str
    code: list[str | int] | None = None
    offsets: list[int] = field(default_factory=list)
    where: str = ""
    file: str = ""
    readers: list[dict[str, object]] = field(default_factory=list)


@dataclass
class Unit:
    """A changed code object (or file) of one side and the probes that must all run for it to count."""

    side: str
    path: str
    qualname: str
    probes: list[Probe] = field(default_factory=list)
    note: str = ""


def git(repo: Path, *args: str, index: Path | None = None) -> str:
    """Return the output of git in `repo` without the caller's repository settings; `index` replaces its index."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=env, check=True).stdout


def file_changes(repo: Path, base: str, candidate: str | None) -> list[FileChange]:
    """Return the files of the package the candidate changes since `base`, with renames paired (``-M``) and no copies.

    `candidate` is a revision, or None for the working tree, staged into a temporary index with the files git does not
    track yet, so a move to an untracked file is a rename too.
    """
    options = ["diff", "-U0", "--no-color", "--no-ext-diff", "-M", "--src-prefix=a/", "--dst-prefix=b/"]
    if candidate:
        text = git(repo, *options, base, candidate, "--", PACKAGE_SOURCE)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "index"
            git(repo, "read-tree", base, index=index)
            git(repo, "add", "-A", "--", PACKAGE_SOURCE, index=index)
            text = git(repo, *options, "--cached", base, "--", PACKAGE_SOURCE, index=index)
    found: list[FileChange] = []
    for line in text.splitlines():
        if line.startswith("diff --git "):
            old, new = (part[2:].removeprefix(f"{PACKAGE_SOURCE}/") for part in line.split()[2:4])
            found.append(FileChange(old, new))
        elif not found:
            continue
        elif line.startswith("new file mode"):
            found[-1].base = None
        elif line.startswith("deleted file mode"):
            found[-1].head = None
        elif line.startswith("Binary files"):
            found[-1].binary = True
        elif match := HUNK.match(line):
            first, count, start, added = (int(group) if group is not None else 1 for group in match.groups())
            found[-1].hunks.append((range(first, first + count), range(start, start + added)))
    return [change for change in found if not any(_bytecode(path) for path in (change.base, change.head) if path)]


def _bytecode(path: str) -> bool:
    """Whether a path is bytecode a run left in the tree (a working tree without an ignore file stages it)."""
    return "__pycache__" in Path(path).parts or path.endswith((".pyc", ".pyo"))


def module_name(path: str) -> str:
    """Return the dotted name of a module of the package from its path below the package."""
    parts = [PACKAGE, *Path(path).with_suffix("").parts]
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


class Side:
    """One side's package: the models of its modules and the tree that follows names across them."""

    def __init__(self, name: str, root: Path) -> None:
        self.name = name
        self.root = root
        self.tree = SourceTree(root, [PACKAGE], verify=False)
        self.models: dict[str, SourceModule] = {}

    def model(self, path: str) -> SourceModule:
        """Return the model of a module of this side by its path below the package."""
        if path not in self.models:
            name = module_name(path)
            self.models[path] = self.tree.module(name) if name in self.tree.paths else self._read(path, name)
        return self.models[path]

    def _read(self, path: str, name: str) -> SourceModule:
        text = (self.root / PACKAGE / path).read_text(encoding="utf-8")
        return SourceModule(text, str(self.root / PACKAGE / path), name, verify=False)

    def text(self, path: str) -> str:
        """Return the text of a file of this side."""
        return (self.root / PACKAGE / path).read_text(encoding="utf-8", errors="surrogateescape")


def _key(model: SourceModule, token_start: Position, kind: int, text: str) -> tuple[int, str, int]:
    """Return what the token diff compares of a token: its type, text and the indentation of its statement."""
    statement = model.statement_at(token_start[0])
    return kind, text, statement.col_offset if statement is not None else -1


def _hunk_tokens(model: SourceModule, lines: range) -> list[tuple[Position, tuple[int, str, int]]]:
    """Return the code tokens that start on `lines` or span into them, with their diff keys.

    A token that spans into them starts in the statement that holds their first line.
    """
    found = []
    statement = model.statement_at(lines.start) if lines else None
    first = min(statement.lineno, lines.start) if statement is not None else lines.start
    for item in model.tokens(range(first, lines.stop)):
        token = item.token
        start, end = token.start[0], token.end[0]
        if start in lines or (start < lines.start <= end and lines):
            found.append((token.start, _key(model, token.start, token.type, token.string)))
    return found


def changed_tokens(sides: dict[str, Side], change: FileChange) -> dict[str, set[Position]]:
    """Return the tokens a Python file's hunks remove (base) and add (head), by start position."""
    found: dict[str, set[Position]] = {"base": set(), "head": set()}
    models = {
        side: sides[side].model(path) for side, path in zip(SIDES, (change.base, change.head), strict=True) if path
    }
    for removed, added in change.hunks:
        before = _hunk_tokens(models["base"], removed) if "base" in models else []
        after = _hunk_tokens(models["head"], added) if "head" in models else []
        matcher = SequenceMatcher(None, [key for _, key in before], [key for _, key in after], autojunk=False)
        for tag, first, last, start, end in matcher.get_opcodes():
            if tag != "equal":
                found["base"].update(position for position, _ in before[first:last])
                found["head"].update(position for position, _ in after[start:end])
    return found


@dataclass(eq=False)
class Statement:
    """A module- or class-level statement of one side: where it is and what it binds."""

    side: str
    path: str
    node: ast.stmt
    scope: Scope
    key: str

    @property
    def module(self) -> str:
        """The dotted name of its module."""
        return module_name(self.path)


def _statements(model: SourceModule) -> Iterator[tuple[ast.stmt, Scope]]:
    """Yield the statements of the module body and of every class body that runs at import, with their scope."""
    scopes = [model.module_scope]
    while scopes:
        scope = scopes.pop()
        yield from ((child, scope) for child in ast.iter_child_nodes(scope.node) if isinstance(child, ast.stmt))
        scopes += [child for child in scope.children if child.kind == "class"]


def _binding_key(model: SourceModule, node: ast.stmt, scope: Scope) -> str:
    """Return the key a statement binds under: a definition's qualname, else the names it binds and its kind."""
    extent = model.extent(node)
    defined = {child.node: child for child in scope.children}
    if node in defined:
        return defined[node].qualname
    names = sorted({binding.name for binding in model.bindings(scope=scope) if binding.line in extent})
    return f"{scope.qualname}|{','.join(names)}|{type(node).__name__}"


def class_order(model: SourceModule, scope: Scope) -> list[str]:
    """Return the binding keys of the statements of a class body that bind names, in source order."""
    keys = [_binding_key(model, node, scope) for node in getattr(scope.node, "body", [])]
    return [key for key in keys if "|" not in key or key.split("|")[-2]]


def reordered_classes(before: SourceModule, after: SourceModule) -> list[tuple[Scope, Scope]]:
    """Return the classes of a module on both sides whose binding statements both sides hold come in another order."""
    heads = {(scope.qualname, scope.ordinal): scope for scope in after.scopes if scope.kind == "class"}
    found = []
    for scope in before.scopes:
        if scope.kind == "class" and (other := heads.get((scope.qualname, scope.ordinal))) is not None:
            old, new = class_order(before, scope), class_order(after, other)
            if [key for key in old if key in new] != [key for key in new if key in old]:
                found.append((scope, other))
    return found


def _free_names(model: SourceModule, node: ast.stmt) -> set[str]:
    """Return the names a statement reads from its module or from builtins."""
    extent = model.extent(node)
    return {
        reference.name
        for reference in model.references
        if reference.line in extent
        and reference.context == "load"
        and not reference.annotation
        and (
            (resolved := model.resolve(reference.node)).kind in {"builtin", "unbound", "star"}
            or (resolved.scope is not None and resolved.scope.kind == "module")
        )
    }


class Moves:
    """The statements a change moves (pairs of a removed and an added statement), and the module pairs they rely on."""

    def __init__(
        self, sides: dict[str, Side], changes: list[FileChange], lines: dict[str, dict[str, set[int]]]
    ) -> None:
        self.sides = sides
        self.modules = {
            module_name(change.base): module_name(change.head) for change in changes if change.base and change.head
        }
        self.pairs: dict[int, Statement] = {}
        candidates: dict[str, list[Statement]] = {"base": [], "head": []}
        for side in SIDES:
            for path, changed in lines[side].items():
                model = sides[side].model(path)
                for node, scope in _statements(model):
                    code = [line for line in model.extent(node) if line in model.code_lines]
                    if code and all(line in changed for line in code):
                        candidates[side].append(Statement(side, path, node, scope, _binding_key(model, node, scope)))
        taken: set[int] = set()
        for removed in candidates["base"]:
            dumped = ast.dump(removed.node)
            for added in candidates["head"]:
                if (
                    id(added) not in taken
                    and added.key == removed.key
                    and ast.dump(added.node) == dumped
                    and self._order_kept(removed, added)
                ):
                    self.pairs[id(removed)] = added
                    self.pairs[id(added)] = removed
                    taken.add(id(added))
                    break
        self.by_line = {
            (statement.side, statement.module, line): statement
            for side in SIDES
            for statement in candidates[side]
            for line in sides[side].model(statement.path).extent(statement.node)
        }
        self.defining = {
            (statement.side, id(statement.node)): statement for side in SIDES for statement in candidates[side]
        }
        while dropped := [statement for statement in candidates["base"] if self._dropped(statement)]:
            for statement in dropped:
                self.pairs.pop(id(self.pairs.pop(id(statement))), None)

    def _order_kept(self, removed: Statement, added: Statement) -> bool:
        """Whether a class-body statement keeps its place among the binding statements both class bodies hold.

        The order of a class body is the order of its fields (``__init__``, ``fields()``, ``model_fields``) and of its
        validators; a module-level statement may move anywhere.
        """
        if removed.scope.kind != "class":
            return True
        before = class_order(self.sides["base"].model(removed.path), removed.scope)
        after = class_order(self.sides["head"].model(added.path), added.scope)
        if removed.key not in before:
            return True
        common = set(before) & set(after)
        return set(before[: before.index(removed.key)]) & common == set(after[: after.index(added.key)]) & common

    def _dropped(self, removed: Statement) -> bool:
        """Whether a pair no longer holds: a free name leads elsewhere, or the class around it is another class."""
        added = self.pairs.get(id(removed))
        if added is None:
            return False
        if removed.scope.kind == "class" and self.modules.get(removed.module, removed.module) != added.module:
            outer = self.defining.get(("base", id(removed.scope.node)))
            if outer is None or self.pairs.get(id(outer)) is not self.defining.get(("head", id(added.scope.node))):
                return True
        names = _free_names(self.sides["base"].model(removed.path), removed.node)
        names |= _free_names(self.sides["head"].model(added.path), added.node)
        return not all(self.corresponds(removed.module, added.module, name) for name in names)

    def _definitions(self, side: str, module: str, name: str) -> set[tuple[str, ...]]:
        """Return where a module-level name of a module of a side leads, as comparable keys."""
        tree = self.sides[side].tree
        if module not in tree.paths:
            return {("unreadable", module, name)}
        found = set()
        for definition in tree.definition(module, name):
            if definition.kind in {"outside", "module", "attribute", "unreadable", "cycle"}:
                found.add((definition.kind, definition.module, definition.name))
            elif definition.kind == "unbound":
                found.add(("unbound", name))
            else:
                found.add(("package", definition.module, definition.name, str(definition.line)))
        return found

    def corresponds(self, base_module: str, head_module: str, name: str) -> bool:
        """Whether a name read in a base module and in a head module resolves to corresponding bindings."""
        before, after = self._definitions("base", base_module, name), self._definitions("head", head_module, name)
        return all(any(self._pairs_with(item, other) for other in after) for item in before) and all(
            any(self._pairs_with(item, other) for item in before) for other in after
        )

    def _pairs_with(self, before: tuple[str, ...], after: tuple[str, ...]) -> bool:
        """Whether a base and a head definition correspond: the same name in paired modules, or moved together."""
        if before[0] != after[0] or before[0] in {"unreadable", "cycle"}:
            return False
        if before[0] not in {"package", "module", "attribute"}:
            return before == after
        if self.modules.get(before[1], before[1]) == after[1] and before[2] == after[2]:
            return True
        if before[0] != "package":
            return False
        statement = self.by_line.get(("base", before[1], int(before[3])))
        partner = self.pairs.get(id(statement)) if statement is not None else None
        return partner is not None and partner is self.by_line.get(("head", after[1], int(after[3])))

    def moved_lines(self, side: str) -> dict[str, set[int]]:
        """Return the lines of the paired statements of a side, by path."""
        found: dict[str, set[int]] = {}
        for statement in {id(item): item for item in self.by_line.values()}.values():
            if statement.side == side and id(statement) in self.pairs:
                found.setdefault(statement.path, set()).update(
                    self.sides[side].model(statement.path).extent(statement.node)
                )
        return found


def changes(repo: Path, base: str, candidate: str | None, roots: dict[str, Path]) -> tuple[dict[str, Side], Changes]:
    """Return the sides and what the candidate changes: changed tokens per side and path, and the changed files."""
    sides = {name: Side(name, roots[name]) for name in SIDES}
    found = Changes()
    lines: dict[str, dict[str, set[int]]] = {"base": {}, "head": {}}
    python = []
    for change in file_changes(repo, base, candidate):
        paths = (change.base, change.head)
        if not all(path is None or path.endswith(".py") for path in paths) or change.binary:
            for side, path in zip(SIDES, paths, strict=True):
                if path is not None:
                    found.files.setdefault(side, set()).add(path)
            continue
        python.append(change)
        for side, path in zip(SIDES, paths, strict=True):
            if path is not None:
                lines[side].setdefault(path, set()).update(
                    line for removed, added in change.hunks for line in (removed if side == "base" else added)
                )
        if change.base and change.head:
            found.pairs[change.base] = change.head
    moves = Moves(sides, python, lines)
    removed = [
        (statement.path, statement.node, scope)
        for statement in {id(item): item for item in moves.by_line.values()}.values()
        if statement.side == "base"
        and id(statement) not in moves.pairs
        and statement.scope.kind == "module"
        and (scope := next((child for child in statement.scope.children if child.node is statement.node), None))
        and scope.kind in {"function", "class"}
    ]
    for change in python:
        if change.base and change.head:
            for before, after in reordered_classes(sides["base"].model(change.base), sides["head"].model(change.head)):
                found.reordered["base"].add((change.base, before.qualname))
                found.reordered["head"].add((change.head, after.qualname))
    proven = unreferenced(sides, removed)
    found.unreferenced = sorted(f"{path} {scope.qualname}" for path, _, scope in proven)
    dropped: dict[str, set[int]] = {}
    for path, node, _ in proven:
        dropped.setdefault(path, set()).update(sides["base"].model(path).extent(node))
    for change in python:
        for side, positions in changed_tokens(sides, change).items():
            path = change.base if side == "base" else change.head
            if path is None:
                continue
            moved = moves.moved_lines(side).get(path, set()) | (dropped.get(path, set()) if side == "base" else set())
            kept = {position for position in positions if position[0] not in moved}
            found.tokens.setdefault(side, {}).setdefault(path, set()).update(kept)
        if change.renamed:
            found.tokens.setdefault("head", {}).setdefault(change.head or "", set()).update(
                _moved_meaning(moves, change)
            )
    return sides, found


def _moved_meaning(moves: Moves, change: FileChange) -> set[Position]:
    """Return the tokens of the statements of a renamed file whose free names no longer resolve as they did."""
    base, head = module_name(change.base or ""), module_name(change.head or "")
    model = moves.sides["head"].model(change.head or "")
    found: set[Position] = set()
    for node, _ in _statements(model):
        names = _free_names(model, node)
        if not all(moves.corresponds(base, head, name) for name in names):
            extent = model.extent(node)
            found.update(item.token.start for item in model.tokens(extent))
    return found


@dataclass
class Changes:
    """What a candidate changes: the changed tokens per side and path, changed files that are not Python, file pairs."""

    tokens: dict[str, dict[str, set[Position]]] = field(default_factory=dict)
    files: dict[str, set[str]] = field(default_factory=dict)
    pairs: dict[str, str] = field(default_factory=dict)
    unreferenced: list[str] = field(default_factory=list)
    reordered: dict[str, set[tuple[str, str]]] = field(default_factory=lambda: {"base": set(), "head": set()})


def _registers(model: SourceModule, node: ast.stmt) -> bool:
    """Whether defining a function or class may register it where its name is never spelled.

    A decorator that only type checkers read does not; any other decorator, a base other than ``object`` (its
    ``__init_subclass__``) or a keyword (a metaclass) may.
    """
    decorators = getattr(node, "decorator_list", [])
    bases = getattr(node, "bases", [])
    return (
        any(model.typing_only(item.lineno, item.col_offset) != "typing-decorator" for item in decorators)
        or bool(getattr(node, "keywords", []))
        or any(model.origin(base) != "builtins.object" for base in bases)
    )


def unreferenced(
    sides: dict[str, Side], statements: Iterable[tuple[str, ast.stmt, Scope]]
) -> list[tuple[str, ast.stmt, Scope]]:
    """Return the removed private module-level definitions of the base that nothing in the base package can reach.

    Reachable: its name anywhere else in the package's text (a read, an attribute, a string, ``__all__``), a namespace
    site of its module (``globals()``, ``vars()``, ...), ``sys.modules`` anywhere, or a ``getattr`` family call on
    its module with a name that is no constant. A class with a base other than ``object`` or a keyword is not proven:
    defining it may register it (`_registers`).
    """
    base = sides["base"]
    texts = {
        path.relative_to(base.root / PACKAGE).as_posix(): path.read_text(encoding="utf-8", errors="surrogateescape")
        for path in (base.root / PACKAGE).rglob("*.py")
        if "__pycache__" not in path.parts
    }
    namespaced = any("sys.modules" in text for text in texts.values())
    found = []
    for path, node, scope in statements:
        model = base.model(path)
        name = scope.qualname
        if (
            namespaced
            or not name.startswith("_")
            or name.startswith("__")
            or _registers(model, node)
            or any(site.kind in NAMESPACE_SITES for site in model.dynamic_sites)
        ):
            continue
        extent = model.extent(node)
        word = re.compile(rf"\b{re.escape(name)}\b")
        own = "\n".join(line for number, line in enumerate(model.lines, 1) if number not in extent)
        if word.search(own) or any(word.search(text) for other, text in texts.items() if other != path):
            continue
        found.append((path, node, scope))
    return found


# Mapping: runs on the interpreter that measures.


class Code:
    """The code objects of one module on the running interpreter, and the value instructions of each by line."""

    def __init__(self, model: SourceModule, path: str) -> None:
        self.model = model
        self.path = path
        self.objects = model.compiled()
        self.lines = model.lines
        self._instructions: dict[int, tuple[list[Positioned], list[Positioned]]] = {}

    def key(self, scope: Scope) -> list[str | int]:
        """Return the key the tracer finds a scope's code object by (`Probe.code`)."""
        code = self.objects[scope]
        return [self.path, code.co_qualname, code.co_firstlineno, *first_position(code)]

    def lifted(self, scope: Scope) -> Scope:
        """Return the scope itself, or the nearest enclosing one that has a code object on this interpreter."""
        probe: Scope | None = scope
        while probe is not None and probe not in self.objects:
            probe = probe.parent
        return probe or self.model.module_scope

    def _column(self, line: int, offset: int) -> int:
        text = self.lines[line - 1] if 0 < line <= len(self.lines) else ""
        return offset if text.isascii() else len(text.encode()[:offset].decode(errors="replace"))

    def instructions(self, scope: Scope) -> list[Positioned]:
        """Return the instructions of a scope's code object with a position: start, end, offset, and if evidence."""
        return self._positioned(scope)[0]

    def nops(self, scope: Scope, spans: Iterable[tuple[Position, Position]]) -> list[int]:
        """Return the offsets of a scope's ``NOP`` instructions inside any of `spans`.

        A ``NOP`` keeps the line of a statement that compiles to nothing else (``pass``, a wildcard ``case _:``); it
        runs where that statement does, which is evidence only where nothing else stands for it.
        """
        spans = list(spans)
        return [
            offset
            for start, end, offset, _ in self._positioned(scope)[1]
            if any(a <= start and end <= b for a, b in spans)
        ]

    def _positioned(self, scope: Scope) -> tuple[list[Positioned], list[Positioned]]:
        """Return the positioned instructions of a scope's code object, and apart its ``NOP`` instructions."""
        code = self.objects[scope]
        if id(code) not in self._instructions:
            found: tuple[list[Positioned], list[Positioned]] = ([], [])
            for item in dis.get_instructions(code):
                position = item.positions
                line, last = (position.lineno, position.end_lineno) if position is not None else (None, None)
                if position is None or line is None or last is None or position.col_offset is None:
                    continue
                start = (line, self._column(line, position.col_offset))
                end = (last, self._column(last, position.end_col_offset or 0))
                evidence = item.opname not in NOT_EVIDENCE and item.opcode not in JUMPS
                if item.opname not in NO_POSITION:
                    found[0].append((start, end, item.offset, evidence))
                elif item.opname == "NOP":
                    found[1].append((start, end, item.offset, False))
            self._instructions[id(code)] = found
        return self._instructions[id(code)]

    def inside(self, scope: Scope, spans: Iterable[tuple[Position, Position]], *, evidence: bool = True) -> list[int]:
        """Return the offsets of a scope's instructions positioned inside any of `spans` (value instructions only)."""
        spans = list(spans)
        return [
            offset
            for start, end, offset, valued in self.instructions(scope)
            if (valued or not evidence) and any(first <= start and end <= last for first, last in spans)
        ]


def _same_probe(one: Probe, other: Probe) -> bool:
    """Whether two probes need the same thing to run (where each was found aside)."""
    keys = ("kind", "code", "offsets", "file", "readers")
    return all(getattr(one, key) == getattr(other, key) for key in keys)


def first_position(code: CodeType) -> tuple[int, int]:
    """Return where a code object's first instruction with a source span starts, which tells apart two lambdas.

    The instructions before it (``RESUME``, a generator's setup) have no span or an empty one at column 0, alike for
    every code object that starts on the line; the first span lies in the code object's own source.
    """
    return next(
        (
            (line, column)
            for line, last, column, end in code.co_positions()
            if line is not None
            and last is not None
            and column is not None
            and end is not None
            and (last, end) > (line, column)
        ),
        (0, 0),
    )


def _span_text(spans: Iterable[tuple[Position, Position]]) -> str:
    return ",".join(f"{start[0]}:{start[1]}-{end[0]}:{end[1]}" for start, end in spans)


class Mapper:
    """Maps the changed tokens of one side to its units and probes (`map_units`)."""

    def __init__(self, side: Side, *, deferred_annotations: bool, unread_docstrings: bool = True) -> None:
        self.side = side
        self.deferred = deferred_annotations
        self.docless = unread_docstrings
        self.codes: dict[str, Code] = {}
        self.readers = Readers(side)
        self.typed: set[tuple[str, str]] = set()
        """The code objects (path, qualname) that compile a typing-only token: what only type checkers read."""

    def code(self, path: str) -> Code:
        """Return the code objects of a module of this side."""
        if path not in self.codes:
            self.codes[path] = Code(self.side.model(path), path)
        return self.codes[path]

    def units(self, path: str, positions: Iterable[Position]) -> dict[tuple[str, str], Unit]:
        """Return the units the tokens at `positions` of a module make, by path and qualname, with their probes."""
        code = self.code(path)
        model = code.model
        found: dict[tuple[str, str], Unit] = {}
        for position in sorted(positions):
            form = model.typing_only(*position)
            if form is not None and (form != "deferred-annotation" or self.deferred):
                self.typed.update((path, scope.qualname) for scope in self._compiling(code, position))
                continue
            if self.docless and function_docstring(model, position):
                self.typed.add((path, model.owner(*position).qualname))
                continue
            for scope, probe, note in self.probes(code, position):
                unit = found.setdefault(
                    (path, scope.qualname), Unit(self.side.name, path, scope.qualname or "<module>")
                )
                if not any(_same_probe(probe, other) for other in unit.probes):
                    unit.probes.append(probe)
                unit.note = unit.note or note
        return found

    @staticmethod
    def _compiling(code: Code, position: Position) -> list[Scope]:
        """Return the code objects a token compiles into: its owner's, and for a header token the enclosing one's."""
        model = code.model
        found = [code.lifted(model.owner(*position))]
        header = model.header_of(*position)
        if header is not None and header.parent is not None:
            found.append(code.lifted(header.parent))
        return found

    def probes(self, code: Code, position: Position) -> list[tuple[Scope, Probe, str]]:
        """Return the probes a changed token needs, each with the unit (scope) it belongs to and a note."""
        model = code.model
        header = model.header_of(*position)
        owner = code.lifted(model.owner(*position))
        if header is not None and header.kind == "class" and header.parent is not None:
            return self._binding_probes(code, code.lifted(header.parent), position, header)
        if header is not None:
            started = code.lifted(header)
            evaluating = code.lifted(header.parent) if started is header and header.parent is not None else owner
            if evaluating is started:
                evaluating = code.lifted(started.parent) if started.parent is not None else started
            return [
                (started, Probe("start", code.key(started), where=_span_text([(position, position)])), ""),
                (evaluating, self._body_probe(code, evaluating, position), ""),
            ]
        if owner.kind in {"module", "class"}:
            return self._binding_probes(code, owner, position, None)
        return [(owner, self._body_probe(code, owner, position), "")]

    @staticmethod
    def _body_probe(code: Code, owner: Scope, position: Position) -> Probe:
        """Return the instruction probe of a token: inside its innermost node, never outside its segment."""
        model = code.model
        segment = model.segment(*position)
        spans = segment.spans
        for node in model.nodes_at(*position):
            span = model.span(node)
            inside = any(first <= span[0] and span[1] <= last for first, last in spans)
            if inside and (offsets := code.inside(owner, [span])):
                return Probe("instr", code.key(owner), offsets, _span_text([span]))
        if offsets := code.inside(owner, spans):
            return Probe("instr", code.key(owner), offsets, _span_text(spans))
        if isinstance(case := segment.node, ast.match_case):
            body = [(model.span(case.body[0])[0], model.span(case.body[-1])[1])]
            if offsets := code.inside(owner, body) or code.nops(owner, [*spans, *body]):
                return Probe("instr", code.key(owner), offsets, _span_text(body))
        if segment.kind == "statement":
            if offsets := code.inside(owner, spans, evidence=False):
                return Probe("instr", code.key(owner), offsets, _span_text(spans))
            return Probe("start", code.key(owner), where=_span_text(spans))
        return Probe("unmeasurable", code.key(owner), where=_span_text(spans))

    def _binding_probes(
        self, code: Code, owner: Scope, position: Position, defined: Scope | None
    ) -> list[tuple[Scope, Probe, str]]:
        """Return the probes of a token of a module or class body: a reader of what its statement binds, else import.

        The names are the class's for a class header, else those the token binds (`target_names`).
        """
        model = code.model
        statement = next(node for node in model.nodes_at(*position) if isinstance(node, ast.stmt))
        names = (
            [defined.qualname.rpartition(".")[2]]
            if defined is not None
            else target_names(model, statement, owner, position)
        )
        probe = self._body_probe(code, owner, position)
        if readers := self.readers_probe(code, owner, names, _span_text([model.span(statement)])):
            return [(owner, readers, ""), (owner, probe, "")]
        return [(owner, probe, "run at import only" if names else "")]

    def class_binding(self, path: str, scope: Scope) -> tuple[list[Probe], str]:
        """Return the probes of a change of a class as a whole (its order, its compiled body), and the unit's note.

        It is a change of the binding of the class, as a change of its header is: a reader of the class name.
        """
        code = self.code(path)
        statement = code.model.statement_at(scope.first_line)
        position = code.model.span(statement)[0] if statement is not None else (scope.first_line, 0)
        found = self._binding_probes(code, code.lifted(scope.parent or scope), position, scope)
        return [probe for _, probe, _ in found], next((note for _, _, note in found if note), "")

    def readers_probe(self, code: Code, scope: Scope, names: Iterable[str], where: str) -> Probe | None:
        """Return the probe one reader of `names` (bound in a module or class body) runs; None where none reads them.

        A read whose receiver no run can tell (`Readers.of`) is a reader too, one no run credits.
        """
        readers: list[dict[str, object]] = []
        unknown = 0
        for name in names:
            found, missing = self.readers.of(code.path, scope, name)
            readers += found
            unknown += missing
        if not readers and not unknown:
            return None
        where += f"; {unknown} read on a receiver no run can tell" if unknown else ""
        return Probe("readers", code.key(scope), where=where, readers=readers)


def target_names(model: SourceModule, statement: ast.stmt, scope: Scope, position: Position) -> list[str]:
    """Return the names a token of a module- or class-level statement binds: all, unless the token is in one part.

    The part is an alias of an import, or an element of a tuple or list assignment and the target it assigns.
    """
    extent = model.extent(statement)
    bound = [binding for binding in model.bindings(scope=scope) if binding.line in extent]
    kinds = {binding.kind for binding in bound}
    parts: list[tuple[ast.AST, ...]] = []
    if kinds and kinds <= {"import", "import-from"}:
        parts = [(binding.node,) for binding in bound]
    elif kinds == {"assign"}:
        parts = _element_pairs(statement)
    for part in parts:
        spans = [model.span(node) for node in part]
        if any(start <= position < end for start, end in spans):
            within = spans[0]
            return sorted({
                binding.name
                for binding in bound
                if within[0] <= model.span(binding.node)[0] and model.span(binding.node)[1] <= within[1]
            })
    return sorted({binding.name for binding in bound})


def _element_pairs(statement: ast.stmt) -> list[tuple[ast.AST, ...]]:
    """Return each target of a tuple or list assignment with the value it gets, where each gets one element."""
    targets = getattr(statement, "targets", [])
    targets = targets[0] if len(targets) == 1 else None
    values = getattr(statement, "value", None)
    if not isinstance(targets, (ast.Tuple, ast.List)) or not isinstance(values, (ast.Tuple, ast.List)):
        return []
    elements = [*targets.elts, *values.elts]
    if len(targets.elts) != len(values.elts) or any(isinstance(item, ast.Starred) for item in elements):
        return []
    return list(zip(targets.elts, values.elts, strict=True))


def _attribute_chain(node: ast.expr, references: dict[int, Reference]) -> list[str]:
    """Return a name and the attributes read on it (``a.b.c``: ``["a", "b", "c"]``); empty for any other expression."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return [reference.name, *parts[::-1]] if (reference := references.get(id(node))) is not None else []


def _run_time(reference: Reference) -> bool:
    """Whether a read runs: not one of a name in a string, a postponed or a never evaluated annotation.

    Nor one only type checkers run (in the body of a ``TYPE_CHECKING`` test, `Import.typing_only`'s rule).
    """
    return reference.annotation in {"", "evaluated"} and not any(
        condition.kind == "type-checking" and condition.branch == "body" for condition in reference.guard
    )


class Readers:
    """The instructions that read a module or class binding, across the modules of one side (`Probe.readers`).

    A reader is a load of the name the binding defines, followed through imports and re-exports to it
    (`SourceTree.definition`): in its own scope, in any module or function that imports it (``from m import NAME``,
    under any name a re-export gives it), as an attribute ``m.NAME`` or ``C.X``. A class attribute is also read as
    ``v.X`` on a name or attribute chain ``v`` (``self.X``/``cls.X`` in a method too), which counts where the
    instruction runs while the object ``v`` names finds ``X`` in that class. A read of ``X`` on any other receiver (a
    call, a subscript) is a reader no run credits.
    """

    def __init__(self, side: Side) -> None:
        self.side = side
        self.paths = sorted(
            path.relative_to(side.root / PACKAGE).as_posix()
            for path in (side.root / PACKAGE).rglob("*.py")
            if "__pycache__" not in path.parts
        )
        self.texts = {path: side.text(path) for path in self.paths}
        self.codes: dict[str, Code] = {}
        self.found: dict[tuple[str, str, int, str], tuple[list[dict[str, object]], int]] = {}
        self.leads: dict[str, frozenset[str]] = {}

    def code(self, path: str) -> Code:
        """Return the code objects of a module of this side."""
        if path not in self.codes:
            self.codes[path] = Code(self.side.model(path), path)
        return self.codes[path]

    def of(self, path: str, scope: Scope, name: str) -> tuple[list[dict[str, object]], int]:
        """Return the reader instructions of `name` bound in `scope` (a module or class body) of a module.

        Also return how many reads of it no run can credit: a receiver no run can tell, or a read with no instruction.
        """
        if (key := (path, scope.qualname, scope.ordinal, name)) not in self.found:
            self.found[key] = self._of(path, scope, name)
        return self.found[key]

    def canonical(self, origin: str) -> frozenset[str]:
        """Return the dotted paths an origin names: itself and where the package's imports and re-exports lead it."""
        if (found := self.leads.get(origin)) is None:
            found = self.leads[origin] = self._canonical(origin, frozenset())
        return found

    def _canonical(self, origin: str, seen: frozenset[str]) -> frozenset[str]:
        tree = self.side.tree
        parts = origin.split(".")
        prefix = next((count for count in range(len(parts), 0, -1) if ".".join(parts[:count]) in tree.paths), 0)
        if not prefix or prefix == len(parts) or origin in seen:
            return frozenset({origin})
        found = {origin}
        for definition in tree.definition(".".join(parts[:prefix]), parts[prefix]):
            if definition.kind not in {"unreadable", "cycle", "unbound", "outside"}:
                target = ".".join([definition.module, *[definition.name] * bool(definition.name), *parts[prefix + 1 :]])
                found |= self._canonical(target, seen | {origin}) if target != origin else set()
        return frozenset(found)

    def _names(self, dotted: str, name: str) -> set[str]:
        """Return the names a binding is imported under anywhere in the package: its own and each re-export's."""
        names = {name}
        while True:
            word = re.compile(rf"\b(?:{'|'.join(map(re.escape, sorted(names)))})\b")
            more = {
                item.bound
                for other in self.paths
                if word.search(self.texts[other])
                for item in self._imports(self.side.model(other), dotted, names)
                if item.bound
            }
            if more <= names:
                return names
            names |= more

    def _imports(self, model: SourceModule, dotted: str, names: set[str]) -> list[Import]:
        """Return the ``from`` imports of a module that import one of `names` and lead to the binding `dotted`."""
        return [
            item
            for item in model.imports
            if item.kind == "from"
            and isinstance(item.target, str)
            and item.target.rpartition(".")[2] in names
            and dotted in self.canonical(item.target)
        ]

    def _of(self, path: str, scope: Scope, name: str) -> tuple[list[dict[str, object]], int]:
        model = self.side.model(path)
        module = module_name(path)
        dotted = f"{module}.{name}" if scope.kind == "module" else f"{module}.{scope.qualname}.{name}"
        found: list[dict[str, object]] = []
        unknown = 0
        loads: list[tuple[str, ast.expr, Reference | None]] = [
            (path, use.reference.node, use.reference)
            for binding in model.bindings(name, scope)
            for use in model.uses(binding)
        ]
        names = self._names(dotted, name) if scope.kind == "module" else {name}
        word = re.compile(rf"\b(?:{'|'.join(map(re.escape, sorted(names)))})\b")
        for other in self.paths:
            if not word.search(self.texts[other]):
                continue
            other_model = self.side.model(other)
            for item in self._imports(other_model, dotted, names):
                if item.alias is not None and (binding := other_model.binding_at(item.alias)) is not None:
                    loads += [(other, use.reference.node, use.reference) for use in other_model.uses(binding)]
            references = {id(reference.node): reference for reference in other_model.references}
            for node in ast.walk(other_model.tree):
                if not (isinstance(node, ast.Attribute) and node.attr in names and isinstance(node.ctx, ast.Load)):
                    continue
                if (origin := other_model.origin(node)) is not None and dotted in self.canonical(origin):
                    loads.append((other, node, None))
                elif scope.kind == "class" and (check := self._check(other_model, node, references)) is not None:
                    reader = self._reader(other, node, [module, scope.qualname, name], check)
                    found += reader
                    unknown += not reader
        for where, node, reference in loads:
            if reference is None or _run_time(reference):
                reader = self._reader(where, node, None, None)
                found += reader
                unknown += not reader
        return found, unknown

    def _check(self, model: SourceModule, node: ast.Attribute, references: dict[int, Reference]) -> list[str] | None:
        """Return how a run tells that an attribute read reads a class attribute; None where it reads no class's.

        A name or attribute chain (a method's ``self``/``cls`` too): the object it names where the read runs, so the
        read that runs is the one checked. ``["?"]``: no run can tell (a call, a subscript). None: the receiver is a
        module or something outside the package.
        """
        origin = model.origin(node.value)
        if origin is not None and any(
            lead in self.side.tree.paths or lead.partition(".")[0] != PACKAGE for lead in self.canonical(origin)
        ):
            return None
        return _attribute_chain(node.value, references) or ["?"]

    def _reader(
        self, path: str, node: ast.expr, receiver: list[str] | None, chain: list[str] | None
    ) -> list[dict[str, object]]:
        """Return the reader of a load: the instructions inside it, the last one alone where it checks its object."""
        if chain == ["?"]:
            return []
        code = self.code(path)
        model = code.model
        owner = code.lifted(model.scope_of(node))
        span = model.span(node)
        offsets = code.inside(owner, [span])
        if not offsets:
            return []
        reader: dict[str, object] = {
            "code": code.key(owner),
            "offsets": offsets,
            "where": f"{path} {_span_text([span])}",
        }
        return [reader | {"receiver": receiver, "chain": chain, "offsets": offsets[-1:] if chain else offsets}]


def normalized(code: CodeType) -> tuple[object, ...]:
    """Return what the compiler made of a code object, without its positions: the comparison of compile-time effects.

    Instructions as name and argument, nested code objects by qualname, constants by type and value (a frozenset in
    order), the argument that feeds a class body's ``__firstlineno__`` store left out; flags, argument counts and the
    names the code uses. Positions and the file name are left out, so moving code changes nothing here.
    """
    found = tuple(item for item, _ in _instructions(code))
    names = (code.co_varnames, code.co_cellvars, code.co_freevars, code.co_names)
    counts = (code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount, code.co_flags)
    return found, names, counts, code.co_exceptiontable


def _instructions(code: CodeType) -> list[tuple[tuple[str, object], int | None]]:
    """Return the instructions of a code object as `normalized` compares them, each with the line it stands for."""
    instructions = list(dis.get_instructions(code))
    found: list[tuple[tuple[str, object], int | None]] = []
    for index, item in enumerate(instructions):
        following = instructions[index + 1] if index + 1 < len(instructions) else None
        value: object = item.argval
        if following is not None and following.opname == "STORE_NAME" and following.argval == "__firstlineno__":
            value = None
        elif hasattr(value, "co_qualname"):
            value = ("code", getattr(value, "co_qualname", ""))
        elif isinstance(value, frozenset):
            value = ("frozenset", tuple(sorted(map(repr, value))))
        elif item.opname.startswith("LOAD_") and item.opname not in {"LOAD_FAST", "LOAD_GLOBAL", "LOAD_NAME"}:
            value = (type(value).__name__, repr(value))
        found.append(((item.opname, value), item.positions.lineno if item.positions is not None else None))
    return found


def changed_bindings(codes: dict[str, Code], scopes: dict[str, Scope]) -> dict[str, list[str]]:
    """Return, per side, the names bound by the statements of a module body whose compiled instructions differ."""
    found = {side: _instructions(codes[side].objects[scopes[side]]) for side in SIDES}
    keys = {side: [item for item, _ in found[side]] for side in SIDES}
    lines: dict[str, set[int | None]] = {side: set() for side in SIDES}
    for tag, first, last, start, end in SequenceMatcher(None, keys["base"], keys["head"], autojunk=False).get_opcodes():
        if tag != "equal":
            lines["base"].update(line for _, line in found["base"][first:last])
            lines["head"].update(line for _, line in found["head"][start:end])
    names: dict[str, list[str]] = {}
    for side in SIDES:
        model = codes[side].model
        bound = model.bindings(scope=scopes[side])
        names[side] = sorted({
            binding.name
            for node in getattr(scopes[side].node, "body", [])
            if lines[side] & set(extent := model.extent(node))
            for binding in bound
            if binding.line in extent
        })
    return names


def compile_time_units(mappers: dict[str, Mapper], pairs: dict[str, str], units: dict[str, dict]) -> None:
    """Add a unit on both sides for code objects of a changed file whose compiled code differs alone.

    Code objects pair by qualname and ordinal (`Scope.ordinal`); a pair with a changed token on either side is a unit
    already, and one that compiles a typing-only token (`Mapper.typed`) differs by what only type checkers read. A
    function needs to start; a class body is a change of the class, which needs a reader of the class name
    (`Mapper.class_binding`); a module body needs its import and a reader of a name its differing statements bind
    (`changed_bindings`), else it runs at import only.
    """
    for base_path, head_path in pairs.items():
        codes = {"base": mappers["base"].code(base_path), "head": mappers["head"].code(head_path)}
        keyed = {
            side: {(scope.qualname, scope.ordinal): scope for scope in code.objects} for side, code in codes.items()
        }
        for key in sorted(keyed["base"].keys() & keyed["head"].keys()):
            scopes = {side: keyed[side][key] for side in SIDES}
            paths = {"base": base_path, "head": head_path}
            if any((paths[side], scopes[side].qualname) in units[side] for side in SIDES):
                continue
            if any((paths[side], scopes[side].qualname) in mappers[side].typed for side in SIDES):
                continue
            if normalized(codes["base"].objects[scopes["base"]]) == normalized(codes["head"].objects[scopes["head"]]):
                continue
            names = changed_bindings(codes, scopes) if scopes["base"].kind == "module" else {}
            for side in SIDES:
                scope = scopes[side]
                probes, note = [Probe("start", codes[side].key(scope), where="compiled code")], "compiled differently"
                if scope.kind == "class":
                    probes, note = mappers[side].class_binding(paths[side], scope)
                elif scope.kind == "module":
                    readers = mappers[side].readers_probe(codes[side], scope, names[side], "compiled code")
                    probes += [readers] if readers is not None else []
                    note = note if readers is not None else "run at import only"
                units[side][paths[side], scope.qualname] = Unit(
                    side, paths[side], scope.qualname or "<module>", probes, note
                )


def _runtime(path: str) -> bool:
    return path.startswith("_runtime/")


def _imported_runtime(side: Side) -> set[str]:
    """Return the modules under ``_runtime`` that the rest of the package imports (at any level, through each other)."""
    edges: dict[str, set[str]] = {}
    for edge in side.tree.import_edges():
        if not any(condition.kind == "type-checking" and condition.branch == "body" for condition in edge.guard):
            edges.setdefault(edge.importer, set()).add(edge.imported)
    runtime = f"{PACKAGE}._runtime"
    pending = [module for importer, targets in edges.items() if not importer.startswith(runtime) for module in targets]
    found: set[str] = set()
    while pending:
        if (module := pending.pop()).startswith(runtime) and module not in found:
            found.add(module)
            pending += edges.get(module, ())
    return found


def _compiled_renderers(side: Side) -> set[str]:
    """Return the templates (paths below the package) that a compiled renderer of this side renders.

    The template sets of ``scripts/compile_builtin_templates.py`` say which template directory compiles into which
    renderer directory; a repository without that script compiles none.
    """
    if compiler is None:
        return set()
    package = compiler.ROOT / PACKAGE_SOURCE
    found = set()
    for template_dir, output_dir, *_ in compiler.template_sets():
        templates = side.root / PACKAGE / template_dir.relative_to(package)
        renderers = side.root / PACKAGE / output_dir.relative_to(package)
        for template in templates.rglob("*.jinja2") if templates.is_dir() else ():
            name = compiler.module_name_for_path(template.relative_to(templates))
            if (renderers / f"{name}.py").is_file():
                found.add(template.relative_to(side.root / PACKAGE).as_posix())
    return found


def file_units(side: Side, files: Iterable[str], python: Iterable[str]) -> tuple[list[Unit], set[str]]:
    """Return the ``read`` units of a side's changed files that run as text, and its Python files that are only copied.

    A file under ``_runtime`` is copied into generated packages; a ``_runtime`` module nothing else imports has no code
    units.
    """
    units = []
    templates = None
    for path in sorted(files):
        if path.endswith(".jinja2") and templates is None:
            templates = _compiled_renderers(side)
        json_resource = "resources" in Path(path).parts and path.endswith(".json")
        template = path.endswith(".jinja2") and path not in (templates or set())
        if _runtime(path) or json_resource or template:
            units.append(Unit(side.name, path, "<file>", [Probe("read", file=path, where=path)]))
    copied = set()
    runtime = [path for path in python if _runtime(path)]
    imported = _imported_runtime(side) if runtime else set()
    for path in sorted(runtime):
        units.append(Unit(side.name, path, "<file>", [Probe("read", file=path, where=path)]))
        if module_name(path) not in imported:
            copied.add(path)
    return units, copied


def annotation_readers(side: Side) -> list[str]:
    """Return the sites of a side's package that read annotations at run time and `READER_SITES` lacks."""
    return sorted(
        f"{path}:{line} {qualname}"
        for (path, qualname), lines in reader_sites(side).items()
        if (path, qualname) not in READER_SITES
        for line in lines
    )


def doc_readers(side: Side) -> list[str]:
    """Return the sites of a side's package that read docstrings at run time and `DOC_READER_SITES` lacks."""
    return sorted(
        f"{path}:{line} {qualname}"
        for (path, qualname), lines in reader_sites(side, DOCSTRINGS).items()
        if (path, qualname) not in DOC_READER_SITES
        for line in lines
    )


def reader_sites(side: Side, kind: Sites | None = None) -> dict[tuple[str, str], list[int]]:
    """Return the code of a side's package that may read annotations (or `kind`) at run time, by path and qualname.

    A site is a reference to a reader (`ANNOTATION_READERS`, anything of ``annotationlib``), by its name or as an
    attribute of its module (``annotationlib.get_annotations``), a read of a name the reader reads
    (``__annotations__``/``__annotate__``: a name, an attribute or a string, as ``getattr`` takes it). Each site has
    its lines.
    """
    kind = kind or ANNOTATIONS
    found: dict[tuple[str, str], list[int]] = {}
    for path in sorted((side.root / PACKAGE).rglob("*.py")):
        relative = path.relative_to(side.root / PACKAGE).as_posix()
        if "__pycache__" in path.parts or not kind.words.search(side.text(relative)):
            continue
        model = side.model(relative)
        sites: list[ast.AST] = [
            reference.node
            for reference in model.references
            if reference.context == "load"
            and (
                (origin := model.origin(reference.node) or "") in kind.readers
                or origin.startswith(kind.modules)
                or reference.name in kind.names
            )
        ]
        sites += [
            node
            for node in ast.walk(model.tree)
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Load)
                and (
                    node.attr in kind.names
                    or (origin := model.origin(node) or "") in kind.readers
                    or origin.startswith(kind.modules)
                )
            )
            or (isinstance(node, ast.Constant) and node.value in kind.names)
        ]
        for node in sites:
            lines = found.setdefault((relative, model.scope_of(node).qualname or "<module>"), [])
            lines += [] if (line := getattr(node, "lineno", 0)) in lines else [line]
    return found


def map_units(sides: dict[str, Side], found: Changes) -> dict[str, object]:
    """Return every unit of both sides with its probes, and what the mapping noted (`Unit`)."""
    readers = [site for side in SIDES for site in annotation_readers(sides[side])] if _deferred(sides, found) else []
    documented = any(
        function_docstring(sides[side].model(path), position)
        for side in SIDES
        for path, positions in found.tokens.get(side, {}).items()
        for position in positions
    )
    docs = [site for side in SIDES for site in doc_readers(sides[side])] if documented else []
    mappers = {
        side: Mapper(sides[side], deferred_annotations=not readers, unread_docstrings=not docs) for side in SIDES
    }
    units: dict[str, dict[tuple[str, str], Unit]] = {side: {} for side in SIDES}
    copied: dict[str, set[str]] = {}
    files: list[Unit] = []
    for side in SIDES:
        python = [path for path, positions in found.tokens.get(side, {}).items() if positions]
        read, copied[side] = file_units(sides[side], found.files.get(side, set()), python)
        files += read
        for path in python:
            if path not in copied[side]:
                units[side].update(mappers[side].units(path, found.tokens[side][path]))
        for path, qualname in sorted(found.reordered[side]):
            if path in copied[side]:
                continue
            scope = next(
                item for item in sides[side].model(path).scopes if (item.kind, item.qualname) == ("class", qualname)
            )
            unit = units[side].setdefault((path, qualname), Unit(side, path, qualname))
            probes, note = mappers[side].class_binding(path, scope)
            unit.probes += [probe for probe in probes if not any(_same_probe(probe, other) for other in unit.probes)]
            unit.note = "" if any(probe.kind == "readers" for probe in unit.probes) else unit.note or note
    pairs = {
        base: head
        for base, head in found.pairs.items()
        if base not in copied["base"] and head not in copied["head"] and base.endswith(".py")
    }
    compile_time_units(mappers, pairs, units)
    every = [*(unit for side in SIDES for _, unit in sorted(units[side].items())), *files]
    return {
        "units": [asdict(unit) for unit in every],
        "unreferenced": found.unreferenced,
        "annotation_readers": readers,
        "docstring_readers": docs,
    }


def function_docstring(model: SourceModule, position: Position) -> bool:
    """Whether a position is in the docstring of a function: compile-time text only ``__doc__`` reads."""
    scope = model.owner(*position)
    first = getattr(scope.node, "body", [None])[0] if scope.kind == "function" else None
    if (
        not isinstance(first, ast.Expr)
        or not isinstance(first.value, ast.Constant)
        or not isinstance(first.value.value, str)
    ):
        return False
    start, end = model.span(first)
    return start <= position < end


def _deferred(sides: dict[str, Side], found: Changes) -> bool:
    """Whether any changed token is a function annotation under the future import (the reader sites then matter)."""
    return any(
        sides[side].model(path).typing_only(*position) == "deferred-annotation"
        for side in SIDES
        for path, positions in found.tokens.get(side, {}).items()
        for position in positions
    )


def main() -> None:
    """Read the comparison from stdin and write the units of both sides to stdout (`map_units`)."""
    request = json.loads(sys.stdin.read())
    roots = {side: Path(root) for side, root in request["roots"].items()}
    sides, found = changes(Path(request["repo"]), request["base"], request["candidate"], roots)
    sys.stdout.write(json.dumps(map_units(sides, found)) + "\n")


if __name__ == "__main__":
    main()
