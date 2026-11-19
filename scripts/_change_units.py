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
  as a greatest fixpoint. Paired statements drop out on both sides. A renamed file (``-M``) keeps its pair diff, and
  each of its unchanged statements whose free names no longer correspond counts as added. A copy is new code.
- **Owner and probes** (`map_units`, on the interpreter that measures, which compiles the code objects): each changed
  token belongs to the innermost scope that holds it, lifted to the nearest one with a code object. A token in a
  header (decorators, signature, bases; a lambda's parameters; a generator expression's first iterable) needs the
  scope to start and the code that evaluates it to run there; any other token needs one of the owner's value
  instructions inside the innermost expression that holds it, never outside its `Segment` (a part that runs as a
  unit). A statement with no value instruction falls back to any instruction, then to the owner's start; a part of a
  statement with none is unmeasurable, and its unit can never pass.
- **Module and class bodies** run as their module is imported. A token of a statement there that binds names needs a
  reader of a name it binds to run (an instruction that loads it: in its module, through ``from m import NAME``, as an
  attribute ``m.NAME`` or ``C.X``, or ``self.X``/``cls.X`` in a method started with a receiver whose class finds ``X``
  in that class); a binding no reader reads counts at import, and its unit says ``run at import only``.
- **Typing-only forms** give no unit (`SourceModule.typing_only`): ``TYPE_CHECKING`` bodies, ``overload`` stubs,
  ``override``/``final`` decorators and function-local annotations; function annotations under the future import only
  while the package reads no function annotations at run time beyond `READER_SITES`.
- **Compile-time effects**: code objects of a file on both sides, paired by qualname and ordinal, whose normalized
  code differs while neither side has a changed token in them, need to start on both sides.
- **Files that run as text**: a changed ``resources/*.json``, a ``.jinja2`` template without a compiled renderer and
  any file under ``_runtime/`` need a compared run to read them (a ``read`` probe). A ``_runtime`` module no other
  module of the package imports is only copied into generated packages: it has the ``read`` probe alone.
- **A removed private definition nothing reads**: a module-level private ``def`` or ``class`` the base removes, whose
  name occurs nowhere else in the base package and whose module the package reads through no namespace, needs no run;
  it reads ``removed, unreferenced on base``. Methods are not proven this way.

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
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._source_model import SourceModule, SourceTree

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from scripts._source_model import Scope

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
READER_SITES = frozenset({
    ("input_model.py", "_get_type_hints_safe"),
})
"""The annotation reader sites of the package that read class annotations only, by path below it and qualname.

While every reader site is one of these, a function's annotations under the future import are strings nothing
evaluates, so a change to them needs no run. A site not listed here turns that off for the whole comparison."""
Position = tuple[int, int]


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
    opened outside the import system), ``readers`` (one of ``readers`` runs, each an ``instr`` probe that may also need
    its method to start with a receiver whose class finds the attribute in a class, ``receiver``), ``unmeasurable``.
    ``code`` keys a code object: path below the package, ``co_qualname``, ``co_firstlineno`` and the position of its
    first instruction. ``where`` is the source span the probe stands for.
    """

    kind: str
    code: list[Any] | None = None
    offsets: list[int] = field(default_factory=list)
    where: str = ""
    file: str = ""
    readers: list[dict[str, Any]] = field(default_factory=list)


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
    return found


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
    """Return the code tokens that start on `lines` or span into them, with their diff keys."""
    found = []
    for item in model.tokens():
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
                if id(added) not in taken and added.key == removed.key and ast.dump(added.node) == dumped:
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
        while dropped := [statement for statement in candidates["base"] if self._dropped(statement)]:
            for statement in dropped:
                self.pairs.pop(id(self.pairs.pop(id(statement))), None)

    def _dropped(self, removed: Statement) -> bool:
        added = self.pairs.get(id(removed))
        if added is None:
            return False
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
        return all(any(self._matches(item, other) for other in after) for item in before) and all(
            any(self._matches(item, other) for item in before) for other in after
        )

    def _matches(self, before: tuple[str, ...], after: tuple[str, ...]) -> bool:
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
        return statement is not None and self.pairs.get(id(statement)) is self.by_line.get((
            "head",
            after[1],
            int(after[3]),
        ))

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


def unreferenced(
    sides: dict[str, Side], statements: Iterable[tuple[str, ast.stmt, Scope]]
) -> list[tuple[str, ast.stmt, Scope]]:
    """Return the removed private module-level definitions of the base that nothing in the base package can reach.

    Reachable: its name anywhere else in the package's text (a read, an attribute, a string, ``__all__``), a namespace
    site of its module (``globals()``, ``vars()``, ...), ``sys.modules`` anywhere, or a ``getattr`` family call on
    its module with a name that is no constant.
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
        decorators = getattr(node, "decorator_list", [])
        if (
            namespaced
            or not name.startswith("_")
            or name.startswith("__")
            or any(model.typing_only(item.lineno, item.col_offset) != "typing-decorator" for item in decorators)
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


def main() -> None:
    """Read the comparison from stdin and write the changed tokens of both sides to stdout (`changes`)."""
    request = json.loads(sys.stdin.read())
    roots = {side: Path(root) for side, root in request["roots"].items()}
    _, found = changes(Path(request["repo"]), request["base"], request["candidate"], roots)
    tokens = {side: {path: sorted(items) for path, items in paths.items()} for side, paths in found.tokens.items()}
    sys.stdout.write(json.dumps({"tokens": tokens, "unreferenced": found.unreferenced}) + "\n")


if __name__ == "__main__":
    main()
