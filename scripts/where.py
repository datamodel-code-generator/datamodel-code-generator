"""Answer where a decision lives and what else touches it, from the repository's own tables.

Usage:
    python -m scripts.where [--all] [--kind KIND] QUERY

A query is one of: ``path:line`` (``src/datamodel_code_generator/types.py:1505``), a path (relative to the package, to
``src/`` or to the repository), ``--flag`` (or an option's config field), ``legacy-<id>``, ``#<issue>`` or a plan id,
a fact (``Fact.BASES``, ``bases``), a pass name, or a symbol (``name``, ``Class.method``, ``path.py:name``).

The script holds no row of its own: it reads the tables of ``docs/architecture.md`` (decision rows, legacy rows, hidden
couplings, case notes), ``scripts/pass_decls.py``, ``scripts/fact_tables.py``, the option table, the recommender rules,
the presets, ``scripts/related_tests.toml`` and the Python sources through ``scripts/_source_model.py``. It checks
nothing and decides nothing. A query that more than one table knows prints one block per table; ``--kind`` keeps one.
Without ``--all`` the answer is at most 60 lines of at most 160 characters. The exit status is 0 when a table knows the
query, 1 when none does (near names follow), and 2 when a source cannot be read.

A name that no decision row cites is answered as such: the page lists no home for it, which is not proof that none
exists.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
import sys
from dataclasses import dataclass
from enum import Enum, unique
from fnmatch import fnmatchcase
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datamodel_code_generator.cli_options import (
    CLI_OPTION_META,
    OPTION_RELATIONS,
    OPTIONS,
    get_cli_option_doc_path,
)
from datamodel_code_generator.preset import get_preset_infos
from scripts._source_model import SourceModule, SourceTree
from scripts.fact_tables import ATTRIBUTE_FACTS, RENDER_FACT_FREEZE_POINTS, Fact, FreezePoint, RenderFact
from scripts.pass_decls import DECISION_DECLS, PASS_DECLS
from scripts.related_tests import mirrors

if TYPE_CHECKING:
    from collections.abc import Iterable

    from scripts._source_model import Binding, Scope
    from scripts.pass_decls import PassDecl

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "datamodel_code_generator"
PACKAGE_DIR = f"src/{PACKAGE}/"
PAGE = "docs/architecture.md"
RULES = "scripts/related_tests.toml"
DECISION_HEADER = "| Decision | Pure core | Adapters |"
LEGACY_HEADER = "| Row | Keeps | Fix |"
COUPLING_HEADER = "| Pass | Crosses modules by | Consequence |"
NOTES_HEADING = "### Case Notes"
HOME_NOTE = 'the page lists no home for it (docs/architecture.md "One home per decision")'
LIMIT = 60
USER_SCOPES = 3
WIDTH = 160
FOUND = 0
UNKNOWN = 1
UNREADABLE = 2
LINE_FORM = re.compile(r"(?P<path>[\w./-]+\.py):(?P<line>\d+)")
ISSUE_FORM = re.compile(r"#\d+|[A-Z]{1,4}-?\d+[a-z]?(?:-\d+)?")
SPAN = re.compile(r"`([^`]+)`")
CELL_SPLIT = re.compile(r" \| (?=(?:[^`]*`[^`]*`)*[^`]*$)")
BULLET_KEY = re.compile(r"#(\d+)")
NOTE_MARKUP = re.compile(r"\*\*|\[(#\d+)\]\([^)]*\)")
DOC_LINE = re.compile(r'\s*[rRuU]?"""\s*(.*?)\s*(?:"""\s*)?$')
HEADING = re.compile(r"^#{2,4} ")
SCOPES = {"module", "class", "function"}
DEFS = {"function", "class"}
ASSIGNS = {"assign", "annassign"}


class UnreadableError(Exception):
    """A source of the answer cannot be read; the message names it."""


@unique
class Kind(Enum):
    """The kinds of answer, one block each."""

    LINE = "line"
    PATH = "path"
    SYMBOL = "symbol"
    PASS = "pass"
    FACT = "fact"
    OPTION = "option"
    LEGACY = "legacy"
    ISSUE = "issue"


class Cite(NamedTuple):
    """What a code span of a page cell names: a file with a qualified name, or a bare token."""

    file: str | None
    name: str


@dataclass
class Row:
    """A row of a page table, or a case note, with the heading above it."""

    section: str
    cells: tuple[str, ...]

    @property
    def key(self) -> str:
        """Return the first cell: the decision, the row id, the pass or the note's number."""
        return self.cells[0]

    @property
    def text(self) -> str:
        """Return every cell."""
        return " ".join(self.cells)


@dataclass
class Page:
    """The tables of the architecture page the answers read."""

    decisions: list[Row]
    legacy: list[Row]
    couplings: list[Row]
    notes: list[Row]


class Found(NamedTuple):
    """A definition: where it is, its kind, its qualified name and the kind of scope that holds it."""

    path: str
    line: int
    kind: str
    qualname: str
    module: str
    parent: str
    doc: str

    @property
    def name(self) -> str:
        """Return the last part of the qualified name."""
        return self.qualname.rpartition(".")[2]

    @property
    def package_path(self) -> str:
        """Return the path the page's cells use: relative to the package directory."""
        return self.path.removeprefix(PACKAGE_DIR)

    @property
    def label(self) -> str:
        """Return ``path:line kind qualname`` and the first line of the docstring."""
        text = f"{self.path}:{self.line} {self.kind} {self.qualname}"
        return f"{text} - {self.doc}" if self.doc else text


class OptionInfo(NamedTuple):
    """What the option table says of one option: its flag, config field, help section and help text."""

    flag: str
    dest: str
    section: str
    help: str


class Section(NamedTuple):
    """A titled list of lines of a block: at most ``cap`` are shown (the last ones with ``tail``)."""

    title: str | None
    items: list[str]
    cap: int = 8
    empty: str | None = None
    tail: bool = False


class Block(NamedTuple):
    """One kind's answer: a heading and sections."""

    kind: Kind
    heading: str
    sections: list[Section]


def short(text: str, width: int = WIDTH) -> str:
    """Return the text on one line of at most ``width`` characters."""
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= width else text[: width - 3] + "..."


def _imports(text: str, module: str) -> bool:
    """Whether the text has an import statement that names the module (its last part) in a module path or a list."""
    name = re.escape(module)
    return bool(
        re.search(
            rf"^[ \t]*(?:from[ \t]+\S*\b{name}\b\S*[ \t]+import\b|from[ \t]+\S+[ \t]+import[ \t]+(?:\([^)]*)?\b{name}\b"
            rf"|import[ \t]+[^\n]*\b{name}\b)",
            text,
            re.MULTILINE,
        )
    )


def clip(line: str) -> str:
    """Return the line cut to the width, keeping its indent."""
    return line if len(line) <= WIDTH else line[: WIDTH - 3] + "..."


def word(name: str) -> re.Pattern[str]:
    """Return the pattern of a name that is not part of a longer word."""
    return re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])")


def cell_parts(line: str) -> tuple[str, ...]:
    """Split a table row into cells; a ``|`` inside a code span stays in its cell."""
    body = line.strip().removeprefix("|").removesuffix("|")
    return tuple(cell.strip() for cell in CELL_SPLIT.split(body.strip()))


@cache
def cites(cell: str) -> tuple[Cite, ...]:
    """Return what the code spans of a cell name (never its prose).

    A span ``file.py:qualname`` names that file and qualname; a span ``file.py`` sets the file of the bare dotted
    identifiers that follow in the cell; other spans (flags, row ids) are tokens without a file.
    """
    found: list[Cite] = []
    current: str | None = None
    for span in SPAN.findall(cell):
        file, separator, qualname = span.partition(":")
        if file.endswith(".py"):
            current = file
            found.append(Cite(file, qualname.strip() if separator else ""))
        elif re.fullmatch(r"[\w.]+", span):
            found.append(Cite(current, span))
        else:
            found.append(Cite(None, span))
    return tuple(found)


def read_page(text: str) -> Page:
    """Read the tables of the architecture page by their header rows; a missing header raises `UnreadableError`."""
    lines = text.splitlines()
    sections: list[str] = []
    fenced: list[bool] = []
    inside = False
    heading = ""
    for line in lines:
        inside ^= line.lstrip().startswith("```")
        if not inside and HEADING.match(line):
            heading = line.lstrip("#").strip()
        sections.append(heading)
        fenced.append(inside)

    def table(header: str) -> list[Row]:
        start = next((index for index, line in enumerate(lines) if line.strip() == header and not fenced[index]), None)
        if start is None:
            message = f"{PAGE}: no table with the header {header}"
            raise UnreadableError(message)
        rows: list[Row] = []
        for index in range(start + 2, len(lines)):
            if not lines[index].startswith("|"):
                break
            rows.append(Row(sections[index], cell_parts(lines[index])))
        return rows

    return Page(table(DECISION_HEADER), table(LEGACY_HEADER), table(COUPLING_HEADER), _notes(lines, sections))


def _notes(lines: list[str], sections: list[str]) -> list[Row]:
    """Return the bullets under the case notes heading, each keyed by the first issue number it names."""
    start = next((index for index, line in enumerate(lines) if line.strip() == NOTES_HEADING), None)
    if start is None:
        message = f"{PAGE}: no heading {NOTES_HEADING}"
        raise UnreadableError(message)
    bullets: list[list[str]] = []
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            break
        if line.startswith("- "):
            bullets.append([line[2:]])
        elif bullets and line.strip():
            bullets[-1].append(line.strip())
    rows = []
    for bullet in bullets:
        text = " ".join(bullet)
        found = BULLET_KEY.search(text)
        rows.append(Row(sections[start], (f"#{found.group(1)}" if found else "note", text)))
    return rows


@cache
def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@cache
def _trees(root: Path) -> tuple[SourceTree, SourceTree]:
    return SourceTree(root / "src", [PACKAGE], verify=False), SourceTree(root, ["scripts"], verify=False)


@cache
def _rules(root: Path) -> list[dict]:
    return tomllib.loads(_text(root / RULES))["rule"]


@cache
def _recommender_options() -> dict[str, dict]:
    resource = files(f"{PACKAGE}.resources").joinpath("recommender_rules.json")
    return json.loads(resource.read_text(encoding="utf-8"))["options"]


def _passes() -> dict[str, PassDecl]:
    return {declaration.name: declaration for declaration in [*PASS_DECLS, *DECISION_DECLS.values()]}


def _phase_of(declaration: PassDecl) -> int:
    return declaration.phase.value


def _pipeline() -> list[PassDecl]:
    return sorted([*PASS_DECLS, *DECISION_DECLS.values()], key=_phase_of)


def _facts(query: str) -> list[Fact | RenderFact]:
    wanted = query.lower()
    return [
        member
        for enum in (Fact, RenderFact)
        for member in enum
        if wanted in {member.value, member.name.lower(), f"{enum.__name__}.{member.name}".lower()}
    ]


def _option_of(query: str) -> OptionInfo | None:
    """Return the option a flag, its ``--no-`` form or its config field names."""
    for option in OPTIONS:
        names = {*option.flags, option.dest}
        names |= {f"--no-{flag[2:]}" for flag in option.flags if flag.startswith("--")}
        if query in names:
            flag = next((flag for flag in option.flags if flag.startswith("--")), option.flags[0])
            return OptionInfo(flag, option.dest, option.section.value, str(option.arguments.get("help", "")))
    return None


def _scope_name(scope: Scope) -> str:
    current = scope
    while current.kind not in SCOPES and current.parent is not None:
        current = current.parent
    return current.qualname or "<module>"


class Repo:
    """The repository read for one answer: the page, the sources and the source model's views of them."""

    def __init__(self, root: Path, page: Page) -> None:
        """Index the modules of the package and of the scripts under the root."""
        self.root = root
        self.page = page
        self.names: dict[str, tuple[SourceTree, str]] = {}
        for tree in _trees(root):
            for name, path in tree.paths.items():
                self.names[path.relative_to(root).as_posix()] = (tree, name)

    def text(self, path: str) -> str:
        """Return the source text of a module path."""
        return _text(self.root / path)

    def model(self, path: str) -> SourceModule:
        """Return the source model of a module path."""
        tree, name = self.names[path]
        return tree.module(name)

    def resolve_path(self, query: str) -> str | None:
        """Return the module path a query names (relative to the package, ``src/`` or the repository), or None."""
        base = self.root.resolve()
        for prefix in ("", "src/", PACKAGE_DIR):
            candidate = (self.root / f"{prefix}{query}").resolve()
            if not candidate.is_file():
                continue
            if not candidate.is_relative_to(base):
                message = f"{query}: outside the repository"
                raise UnreadableError(message)
            if (relative := candidate.relative_to(base).as_posix()) in self.names:
                return relative
        return None

    def found(self, path: str, scope: Scope) -> Found:
        """Return the definition a scope makes."""
        _, module = self.names[path]
        model = self.model(path)
        lines = self.text(path).splitlines()
        stop = model.header(scope).stop
        doc = DOC_LINE.match(lines[stop - 1]) if scope.kind in DEFS and stop <= len(lines) else None
        parent = scope.parent.kind if scope.parent else "module"
        return Found(path, scope.line, scope.kind, scope.qualname, module, parent, doc[1] if doc else "")

    def definitions(self, name: str, path: str | None = None) -> list[Found]:
        """Return every definition of a name: defs and classes by qualified name, module-level assignments by name."""
        leaf = name.rpartition(".")[2]
        declared = re.compile(
            rf"^\s*(?:async\s+def|def|class)\s+{re.escape(leaf)}\b|^{re.escape(leaf)}\s*[:=]", re.MULTILINE
        )
        found: list[Found] = []
        for rel in [path] if path else self.names:
            if not declared.search(self.text(rel)):
                continue
            model = self.model(rel)
            found += [
                self.found(rel, scope)
                for scope in model.scopes
                if scope.kind in DEFS
                and "<locals>" not in scope.qualname
                and (scope.qualname == name or scope.qualname.endswith(f".{name}"))
            ]
            if "." not in name:
                _, module = self.names[rel]
                found += [
                    Found(rel, binding.line, binding.kind, name, module, "module", "")
                    for binding in model.module_scope.bindings.get(name, [])
                    if binding.kind in ASSIGNS
                ]
        return sorted(found, key=_site_key)

    def elsewhere(self, name: str, found: list[Found]) -> list[Found]:
        """Return the definitions of the last part of a qualified name that the qualified name does not reach."""
        known = {(site.path, site.line) for site in found}
        return [site for site in self.definitions(name.rpartition(".")[2]) if (site.path, site.line) not in known]

    def module_users(self, found: Found) -> dict[str, set[str]]:
        """Return the scopes by file that read a module-level definition, by the binding the name leads to."""
        _, module = self.names[found.path]
        leaf, last = found.name, module.rpartition(".")[2]
        users: dict[str, set[str]] = {}
        for rel, (tree, other) in self.names.items():
            text = self.text(rel)
            if rel != found.path and not (word(leaf).search(text) and _imports(text, last)):
                continue
            model = self.model(rel)
            direct, aliases = self._bindings_of(model, tree, other, module, leaf)
            if rel == found.path:
                direct = [*direct, *model.module_scope.bindings.get(leaf, [])]
            direct_names, alias_names = {b.name for b in direct}, {b.name for b in aliases}
            lines = text.splitlines()
            for reference in model.references:
                if reference.name in direct_names:
                    if any(binding in direct for binding in model.resolve(reference.node).bindings):
                        users.setdefault(rel, set()).add(_scope_name(reference.scope))
                elif reference.name in alias_names:
                    after = lines[reference.line - 1][reference.column + len(reference.name) :]
                    if after.startswith(".") and word(leaf).match(after[1:]):
                        users.setdefault(rel, set()).add(_scope_name(reference.scope))
        return users

    @staticmethod
    def _bindings_of(
        model: SourceModule, tree: SourceTree, module: str, target: str, leaf: str
    ) -> tuple[list[Binding], list[Binding]]:
        """Return the import bindings of a module that lead to ``target.leaf``, and those of ``target`` itself."""
        direct: list[Binding] = []
        aliases: list[Binding] = []
        for binding in model.bindings():
            if binding.kind not in {"import", "import-from"} or binding.origin is None:
                continue
            if binding.origin == target:
                aliases.append(binding)
            elif binding.origin == f"{target}.{leaf}":
                direct.append(binding)
            elif binding.scope is model.module_scope and leaf in {binding.name, binding.origin.rpartition(".")[2]}:
                leads = tree.definition(module, binding.name)
                if any(site.module == target and site.name == leaf for site in leads):
                    direct.append(binding)
        return direct, aliases

    def attribute_users(self, leaf: str, owners: Iterable[Found]) -> dict[str, int]:
        """Return the files that spell ``.leaf`` with the count: the defining modules and those naming the class."""
        pattern = re.compile(rf"\.{re.escape(leaf)}\b")
        paths = {site.path for site in owners}
        names = {site.qualname.rpartition(".")[0].rpartition(".")[2] for site in owners} - {""}
        names |= {site.module.rpartition(".")[2] for site in owners}
        counts: dict[str, int] = {}
        for rel in self.names:
            text = self.text(rel)
            if (count := len(pattern.findall(text))) and (rel in paths or any(word(n).search(text) for n in names)):
                counts[rel] = count
        return counts


def _site_key(site: Found) -> tuple[str, int]:
    return site.path, site.line


def _cited(found: tuple[Cite, ...], sites: list[Found]) -> bool:
    """Whether a cell cites a definition: its file and its name, or the class that holds it."""
    return any(
        cite.file == site.package_path
        and cite.name
        and (site.qualname == cite.name or site.qualname.startswith(f"{cite.name}."))
        for cite in found
        for site in sites
    )


def _named(found: tuple[Cite, ...], sites: list[Found]) -> bool:
    """Whether a cell names a definition: as `_cited` does, or by the bare name of a module-level one."""
    return _cited(found, sites) or any(
        cite.name == site.qualname for cite in found for site in sites if site.parent == "module"
    )


def _home_line(row: Row) -> str:
    return f"home: {short(row.key, 100)} [{short(row.section, 30)}]"


def _site_line(row: Row) -> str:
    return f"site: {short(row.key, 90)} [{short(row.section, 30)}] (home {short(row.cells[1], 40)})"


def _row_line(row: Row) -> str:
    return f"row: {short(row.key, 100)} [{short(row.section, 30)}]"


def _legacy_line(row: Row) -> str:
    return f"{row.cells[0].strip('`')} [{row.cells[2]}] {short(row.cells[1], 100)}"


def _note_line(row: Row) -> str:
    return short(NOTE_MARKUP.sub(r"\1", row.cells[1]), 130)


def _symbol_rows(page: Page, sites: list[Found]) -> list[Section]:
    """Return the page rows that cite a definition: homes, sites, legacy rows and case notes."""
    homes = [row for row in page.decisions if _cited(cites(row.cells[1]), sites)]
    listed = [row for row in page.decisions if row not in homes and _cited(cites(row.cells[2]), sites)]
    legacy = [row for row in page.legacy if _cited(cites(row.cells[1]), sites)]
    notes = [row for row in page.notes if _named(cites(row.cells[1]), sites)]
    claim = f", claimed by {len(homes)} rows" if len(homes) > 1 else ""
    return [
        Section(f"rows that claim it as home{claim}", [_home_line(row) for row in homes], cap=6),
        Section("rows that list it as a site", [_site_line(row) for row in listed], cap=6),
        Section("legacy rows", [_legacy_line(row) for row in legacy], cap=4),
        Section("case notes", [_note_line(row) for row in notes], cap=2),
    ]


def tests_section(repo: Repo, path: str) -> Section:
    """Return the tests of a file: its mirror test module and the rows of the related-tests table that name it."""
    mirror = next((test for test in mirrors(path) if (repo.root / test).exists()), None)
    fired = [
        rule
        for rule in _rules(repo.root)
        if any(fnmatchcase(path, glob) for glob in rule["paths"])
        and not any(fnmatchcase(path, glob) for glob in rule.get("exclude", []))
    ]
    everywhere = [f"{path.partition('/')[0]}/*"]
    plain = [rule for rule in fired if "pattern" not in rule and rule["paths"] != everywhere]
    items = [f"mirror: {mirror or 'none'}", *(f"row {rule['id']}: {', '.join(rule['tests'])}" for rule in plain)]
    rest = [rule["id"] for rule in fired if rule not in plain]
    return Section("tests", [*items, *([f"other rows (by changed lines): {', '.join(rest)}"] if rest else [])], cap=6)


def _user_lines(repo: Repo, sites: list[Found]) -> list[str]:
    users: dict[str, set[str]] = {}
    for site in sites:
        if site.parent == "module" and site.kind in DEFS | ASSIGNS:
            for path, names in repo.module_users(site).items():
                users.setdefault(path, set()).update(names)
    lines = []
    for path, names in sorted(users.items()):
        ordered = sorted(names)
        more = f" (+{len(ordered) - USER_SCOPES})" if len(ordered) > USER_SCOPES else ""
        lines.append(f"{path}: {', '.join(ordered[:USER_SCOPES])}{more}")
    return lines


def symbol_block(repo: Repo, query: str, sites: list[Found], others: list[Found]) -> Block:
    """Return the answer for a symbol: its definitions, its users, the rows that name it and its tests."""
    methods = [site for site in sites if site.parent == "class"]
    counts = repo.attribute_users(methods[0].name, methods) if methods else {}
    rows = _symbol_rows(repo.page, sites)
    sections = [
        Section("defined", [site.label for site in sites], cap=6),
        Section("same name elsewhere", [site.label for site in others], cap=4),
        Section("users", _user_lines(repo, sites), cap=12, empty="none found" if not methods else None),
        Section(
            f"users of .{methods[0].name} by name (attribute types are not resolved)" if methods else None,
            [f"{p} x{n}" for p, n in sorted(counts.items())],
            cap=12,
        ),
        *rows,
    ]
    if not any(section.items for section in rows):
        sections.append(Section("rows", [], empty=f"none name it; {HOME_NOTE}"))
    sections.append(tests_section(repo, sites[0].path))
    return Block(Kind.SYMBOL, f"symbol {query}", sections)


def path_block(repo: Repo, path: str) -> Block:
    """Return the answer for a file: its docstring, the rows that name it, the passes it defines and its tests."""
    model = repo.model(path)
    package_path = path.removeprefix(PACKAGE_DIR)
    names = _passes()

    def cell_cites(row: Row, index: int) -> bool:
        return any(cite.file == package_path for cite in cites(row.cells[index]))

    homes = [row for row in repo.page.decisions if cell_cites(row, 1)]
    listed = [row for row in repo.page.decisions if row not in homes and cell_cites(row, 2)]
    legacy = [row for row in repo.page.legacy if cell_cites(row, 1)]
    defined = [
        f"{scope.qualname} ({names[scope.qualname].phase.name})"
        for scope in model.scopes
        if scope.kind == "function" and scope.qualname in names
    ]
    doc = (ast.get_docstring(model.tree) or "no module docstring").partition("\n")[0]
    rows = [
        Section("rows that claim it as home", [_home_line(row) for row in homes], cap=40),
        Section("rows that list it as a site", [_site_line(row) for row in listed], cap=8),
        Section("legacy rows", [_legacy_line(row) for row in legacy], cap=5),
    ]
    sections = [Section(None, [doc]), *rows, Section("passes defined in it", defined, cap=8)]
    if not any(section.items for section in rows):
        sections.append(Section("rows", [], empty=f"none name this file; {HOME_NOTE}"))
    sections.append(tests_section(repo, path))
    return Block(Kind.PATH, f"path {path}", sections)


def line_block(repo: Repo, path: str, line: int) -> list[Block]:
    """Return the scope chain of a line and the answer for its innermost definition (or its file)."""
    source = repo.text(path).splitlines()
    if not 1 <= line <= len(source):
        message = f"{path}:{line}: the file has {len(source)} lines"
        raise UnreadableError(message)
    text = source[line - 1]
    scope = repo.model(path).owner(line, len(text) - len(text.lstrip()))
    chain = []
    current: Scope | None = scope
    while current is not None:
        if current.kind in SCOPES:
            chain.append(f"{current.qualname or '<module>'} ({current.kind}, line {current.line})")
        current = current.parent
    while scope.kind not in SCOPES and scope.parent is not None:
        scope = scope.parent
    heading = Block(Kind.LINE, f"{path}:{line}", [Section("scope chain", chain[::-1], cap=6)])
    if scope.kind == "module":
        return [heading, path_block(repo, path)]
    site = repo.found(path, scope)
    return [heading, symbol_block(repo, site.qualname, [site], repo.elsewhere(site.qualname, [site]))]


def _names_pass(found: tuple[Cite, ...], name: str) -> bool:
    return any(cite.name == name for cite in found)


def pass_block(repo: Repo, declaration: PassDecl) -> Block:
    """Return the answer for a pass: its declaration, order edges, definition, users and the rows that name it."""
    name = declaration.name
    order = _pipeline()
    index = next(position for position, other in enumerate(order) if other.name == name)
    reads = ", ".join(sorted(member.value for member in declaration.reads)) or "-"
    writes = ", ".join(sorted(member.value for member in declaration.writes)) or "-"
    before = order[index - 1].name if index else "-"
    after = order[index + 1].name if index + 1 < len(order) else "-"
    condition = f"; condition: {declaration.condition}" if declaration.condition else ""
    head = [
        short(declaration.summary),
        f"{declaration.phase.name}, scope {declaration.scope.value}, {declaration.kind.value}{condition}",
        *([f"trigger: {declaration.trigger.text}"] if declaration.trigger else []),
        *([f"removed by: {declaration.removed_by}"] if declaration.removed_by else []),
        *([f"moves to {declaration.target.name} once its change lands"] if declaration.target else []),
        f"reads: {reads}",
        f"writes: {writes}",
        f"neighbours: runs after {before}, before {after}",
    ]
    sites = repo.definitions(name)
    couplings = [row for row in repo.page.couplings if _names_pass(cites(row.cells[0]), name)]
    decisions = [
        row
        for row in repo.page.decisions
        if _names_pass(cites(row.cells[1]), name) or _names_pass(cites(row.cells[2]), name)
    ]
    notes = [row for row in repo.page.notes if _names_pass(cites(row.cells[1]), name)]
    sections = [
        Section(None, head, cap=12),
        Section(
            "order edges", [f"after {edge.pass_name}: {short(edge.reason, 120)}" for edge in declaration.after], cap=4
        ),
        Section("defined", [site.label for site in sites], cap=2, empty="no definition found"),
        Section("users", _user_lines(repo, sites), cap=6),
        Section(
            "hidden couplings",
            [f"crosses modules by {short(row.cells[1], 70)}; {short(row.cells[2], 60)}" for row in couplings],
            cap=2,
        ),
        Section("decision rows that cite it", [_row_line(row) for row in decisions], cap=4),
        Section("case notes", [_note_line(row) for row in notes], cap=2),
    ]
    if sites:
        sections.append(tests_section(repo, sites[0].path))
    return Block(Kind.PASS, f"pass {name}", sections)


def _freeze_index(point: FreezePoint | None) -> int:
    return list(FreezePoint).index(point) if point else -1


def fact_block(member: Fact | RenderFact) -> Block:
    """Return the answer for a fact: its attributes, the passes that write and read it, where it becomes final."""
    rows = [row for row in ATTRIBUTE_FACTS if member in row.facts]
    order = _pipeline()
    writers = [declaration for declaration in order if member in declaration.writes]
    readers = [declaration for declaration in order if member in declaration.reads]
    frozen = (
        RENDER_FACT_FREEZE_POINTS[member]
        if isinstance(member, RenderFact)
        else max((row.frozen_at for row in rows), key=_freeze_index, default=None)
    )
    head = [f"freezes at {frozen.name} ({frozen.value})" if frozen else "no freeze point"]
    final = {row.final_after for row in rows}
    head += [f"final after its last declared writer: {last.name} ({last.phase.name})" for last in writers[-1:]]
    head += [
        f"the tables disagree: ATTRIBUTE_FACTS rows say {', '.join(sorted(phase.name for phase in final))}, "
        f"the last writer of PASS_DECLS runs in {last.phase.name}"
        for last in writers[-1:]
        if rows and max(phase.value for phase in final) != last.phase.value
    ]
    head += [
        f"read by {len(readers)} passes, first {first.name} ({first.phase.name}), "
        f"last {readers[-1].name} ({readers[-1].phase.name})"
        for first in readers[:1]
    ]
    attributes = []
    for row in rows:
        freeze = f", frozen at {row.frozen_at.name}" if row.frozen_at else ""
        attributes.append(
            f"{row.owner}.{row.attribute} {row.kind.value}, final after {row.final_after.name}{freeze};"
            f" store ops: {', '.join(row.store_ops) or '-'}"
        )
    sections = [
        Section(None, head, cap=6),
        Section("attribute rows", attributes, cap=8),
        Section("written by (pipeline order)", [f"{p.name} ({p.phase.name})" for p in writers], cap=8, tail=True),
    ]
    return Block(Kind.FACT, f"fact {type(member).__name__}.{member.name}", sections)


def _presets_line(dest: str) -> str:
    """Return the first dated preset of each family that sets a config field, with the value."""
    first: dict[str, tuple[str, str]] = {}
    for info in get_preset_infos():
        family, _, _ = info.name.value.partition("-")
        date = info.name.value.rpartition("-")[2]
        for group in info.option_groups:
            for item in group.config.items():
                if item.field_name == dest and (family not in first or date < first[family][0]):
                    value = item.value.value if isinstance(item.value, Enum) else item.value
                    first[family] = (date, str(value))
    if not first:
        return "presets: no dated preset sets it"
    return "presets: " + "; ".join(f"{family} from {date} ({value})" for family, (date, value) in sorted(first.items()))


def _option_lines(option: OptionInfo) -> list[str]:
    """Return what the option tables say about an option: help section, relations, recommender, presets, docs."""
    flag = option.flag
    meta = CLI_OPTION_META.get(flag)
    cell = "no documentation row"
    if meta is not None:
        group = meta.group.value if meta.group else "-"
        deprecated = "; deprecated" if meta.deprecated else ""
        cell = f"{meta.category.value}/{group}; since {meta.since_version or '-'}{deprecated}"
    lines = [f"{option.section}; {cell}", f"help: {short(option.help, 130)}"]
    for label in ("implies", "requires", "conflicts"):
        lines += [
            f"{label} {relation.option}{'' if relation.value is None else f'={relation.value}'}"
            for relation in getattr(meta, label, ())
        ]
    lines += [
        f"checked: {' '.join(relation.pair)} at {relation.stage} ({relation.action})"
        for relation in OPTION_RELATIONS
        if flag in {relation.pair[0], relation.pair[2]} and relation.stage
    ]
    entry = _recommender_options().get(option.dest, {})
    if "rules" in entry:
        lines.append(f"recommender: rules {', '.join(entry['rules'])}")
    else:
        lines.append(
            f"recommender: not recommended ({entry.get('not_recommended')}): {short(entry.get('reason', ''), 100)}"
        )
    lines.append(_presets_line(option.dest))
    docs = get_cli_option_doc_path(flag)
    return [*lines, f"docs: docs/cli-reference/{docs}"] if docs else lines


def option_block(repo: Repo, option: OptionInfo) -> Block:
    """Return the answer for an option: the tables that describe it, its readers and the rows that name it."""
    dest = option.dest
    pattern = re.compile(rf"\.{re.escape(dest)}\b")
    counts = {rel: len(pattern.findall(repo.text(rel))) for rel in repo.names}
    field_line = re.compile(rf"^\s+{re.escape(dest)}\s*[:=]", re.MULTILINE)
    declared = [
        rel
        for rel in repo.names
        if field_line.search(repo.text(rel))
        and any(dest in s.bindings for s in repo.model(rel).scopes if s.kind == "class")
    ]
    names = {option.flag, f"--no-{option.flag[2:]}"}
    rows = [row for row in repo.page.decisions if any(cite.name in names for cite in cites(row.text))]
    legacy = [row for row in repo.page.legacy if any(cite.name in names for cite in cites(row.cells[1]))]
    sections = [
        Section(None, _option_lines(option), cap=14),
        Section("declared as a field in", declared, cap=4),
        Section(
            "readers by name (attribute types are not resolved)",
            [f"{rel} .{dest} x{count}" for rel, count in sorted(counts.items()) if count],
            cap=12,
        ),
        Section("decision rows that name it", [_row_line(row) for row in rows], cap=8),
        Section("legacy rows", [_legacy_line(row) for row in legacy], cap=8),
    ]
    return Block(Kind.OPTION, f"option {option.flag} (config field {dest})", sections)


def legacy_block(repo: Repo, row: Row) -> Block:
    """Return the answer for a legacy row: what it keeps, its option, and the code that spells its id."""
    key = row.cells[0].strip("`")
    flag = next((cite.name for cite in cites(row.cells[1]) if cite.name.startswith("--")), None)
    option = _option_of(flag) if flag else None
    sites = []
    for rel in sorted(repo.names):
        if key not in repo.text(rel):
            continue
        model = repo.model(rel)
        for number, text in enumerate(repo.text(rel).splitlines(), 1):
            if key in text:
                sites.append(f"{rel}:{number} {_scope_name(model.owner(number, len(text) - len(text.lstrip())))}")
    summary = [f"option {option.flag} (config field {option.dest}): {_option_lines(option)[0]}"] if option else []
    sections = [
        Section(None, [f"fix: {row.cells[2]}; keeps: {short(row.cells[1], 120)}", *summary], cap=2),
        Section("sites that spell it", sites, cap=8, empty="no source spells it"),
    ]
    return Block(Kind.LEGACY, f"legacy {key}", sections)


def issue_block(repo: Repo, query: str) -> Block:
    """Return the answer for an issue number or a plan id: the rows and notes that name it."""
    plain = query.upper().replace("-", "")
    pattern = word(query)
    legacy = [
        row
        for row in repo.page.legacy
        if plain == re.sub(r"[`\s-]", "", row.cells[2]).upper() or pattern.search(row.cells[1])
    ]
    notes = [row for row in repo.page.notes if row.key == query or pattern.search(row.cells[1])]
    decisions = [row for row in repo.page.decisions if pattern.search(row.text)]
    sections = [
        Section("legacy rows", [_legacy_line(row) for row in legacy], cap=8),
        Section("case notes", [_note_line(row) for row in notes], cap=4),
        Section("decision rows that name it", [_row_line(row) for row in decisions], cap=6),
    ]
    return Block(Kind.ISSUE, f"issue {query}", sections)


def _named_blocks(repo: Repo, query: str, kinds: set[Kind]) -> list[Block]:
    blocks = [fact_block(member) for member in _facts(query)]
    if (declaration := _passes().get(query)) is not None:
        blocks.append(pass_block(repo, declaration))
    if (path := repo.resolve_path(query)) is not None:
        blocks.append(path_block(repo, path))
    if (option := _option_of(query)) is not None:
        blocks.append(option_block(repo, option))
    name_path, _, name = query.rpartition(":")
    restricted = repo.resolve_path(name_path) if name_path.endswith(".py") else None
    sites = repo.definitions(name if restricted else query, restricted)
    is_pass = any(block.kind is Kind.PASS for block in blocks)
    if sites and not (is_pass and kinds == set(Kind)):
        blocks.append(symbol_block(repo, query, sites, repo.elsewhere(query, sites) if "." in query else []))
    return blocks


def blocks_for(repo: Repo, query: str, kinds: set[Kind]) -> list[Block]:
    """Return the blocks the tables give for a query; the form of the query decides first."""
    blocks: list[Block] = []
    if match := LINE_FORM.fullmatch(query):
        if (path := repo.resolve_path(match["path"])) is None:
            message = f"{match['path']}: not a module of src or scripts"
            raise UnreadableError(message)
        blocks += line_block(repo, path, int(match["line"]))
    elif query.startswith("--"):
        blocks += [option_block(repo, option)] if (option := _option_of(query)) else []
    elif query.startswith("legacy-"):
        blocks += [legacy_block(repo, row) for row in repo.page.legacy if row.cells[0].strip("`") == query]
    elif ISSUE_FORM.fullmatch(query):
        blocks.append(issue_block(repo, query))
    else:
        blocks += _named_blocks(repo, query, kinds)
    return [block for block in blocks if block.kind in kinds and (block.kind is Kind.LINE or _filled(block))]


def _filled(block: Block) -> bool:
    return any(section.items for section in block.sections)


def known_names() -> set[str]:
    """Return the names the tables use for options, passes and facts."""
    return {*CLI_OPTION_META, *_passes(), *(member.value for enum in (Fact, RenderFact) for member in enum)}


def near_names(query: str) -> list[str]:
    """Return up to five names of the tables that resemble the query."""
    return difflib.get_close_matches(query, sorted(known_names() - {query}), n=5)


def _render_block(block: Block, share: int | None) -> list[str]:
    lines = [block.heading]
    for section in block.sections:
        count = len(section.items)
        if not count:
            lines += [f"  {section.title}: {section.empty}"] if section.empty and section.title else []
            continue
        title = 1 if section.title else 0
        shown = count if share is None else min(section.cap, count)
        if share is not None:
            room = share - len(lines)
            shown = min(shown, room - title)
            shown = max(min(shown, room - title - 1) if shown < count else shown, 0)
            if not shown:
                lines += [f"  {section.title or 'more'} ({count}): --all lists them"] if room > 0 else []
                continue
        hidden = count - shown
        indent = "    " if section.title else "  "
        picked = section.items[hidden:] if section.tail else section.items[:shown]
        lines += [f"  {section.title} ({count}):"] if section.title else []
        lines += [f"{indent}... {hidden} earlier (--all)"] if hidden and section.tail else []
        lines += [f"{indent}{item}" for item in picked]
        lines += [f"{indent}... {hidden} more (--all)"] if hidden and not section.tail else []
    return lines


def render(blocks: list[Block], *, everything: bool) -> list[str]:
    """Return the lines of the blocks, each block within its share of the line limit unless everything is asked."""
    out: list[str] = []
    for index, block in enumerate(blocks):
        share = None if everything else (LIMIT - len(out)) // (len(blocks) - index)
        out += _render_block(block, share)
    return [clip(line) for line in out]


def main(argv: list[str] | None = None, *, page: str | None = None) -> int:
    """Print the answer for a query; ``page`` replaces the text of the architecture page (for tests)."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.where", description=(__doc__ or "").partition("Usage:")[0].strip(), allow_abbrev=False
    )
    parser.add_argument(
        "query", nargs="?", help="a path:line, path, --flag, legacy-id, #issue or plan id, fact, pass or symbol"
    )
    parser.add_argument("--all", action="store_true", help="lift the caps and the 60-line limit")
    parser.add_argument("--kind", choices=[kind.value for kind in Kind], help="print only this kind of answer")
    args, flags = parser.parse_known_args(argv)
    queries = [*([args.query] if args.query else []), *flags]
    if len(queries) != 1:
        parser.error("give exactly one query")
    query = queries[0]
    kinds = {Kind(args.kind)} if args.kind else set(Kind)
    try:
        repo = Repo(ROOT, read_page(page if page is not None else _text(ROOT / PAGE)))
        blocks = blocks_for(repo, query, kinds)
    except UnreadableError as error:
        sys.stderr.write(f"where: {error}\n")
        return UNREADABLE
    if not blocks:
        near = near_names(query)
        known = (
            f"no {args.kind} answer for {query}" if args.kind and query in known_names() else f"no table knows {query}"
        )
        sys.stdout.write(f"{known}\n" + (f"near: {', '.join(near)}\n" if near else ""))
        return UNKNOWN
    sys.stdout.write("".join(f"{line}\n" for line in render(blocks, everything=args.all)))
    return FOUND


if __name__ == "__main__":
    sys.exit(main())
