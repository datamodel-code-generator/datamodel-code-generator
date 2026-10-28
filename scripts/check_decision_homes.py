"""Keep every decision in its home, and every ``legacy-*`` row named and planned.

``scripts/decision_homes.toml`` lists, for each decision that has one home (``docs/architecture.md``, "One home per
decision"), the home's core and adapters and the AST *signatures* that mean "this code decides it". The checker scans
``src/datamodel_code_generator`` with ``ast``, so it never imports the package, and reports a signature found outside
its home as

    decision <X> has its home in <file:function>

Signature kinds (``kind`` in the toml; ``paths`` limits the files a signature scans, ``what`` words the message):

* ``name``: a read, write or any use (``ctx`` = ``load`` (default), ``store`` or ``any``) of an attribute or a name
  in ``names``; ``receivers`` limits an attribute to values whose last name is listed (``reference.name``).
* ``call``: a call whose callee ends in one of ``names`` (any callee when omitted), that passes every keyword in
  ``keywords`` and none of ``without`` (a keyword passed a literal ``False`` does not count; a call that unpacks
  ``**`` is skipped when ``without`` is set), and whose positional arguments name one of ``mentions`` (when given).
* ``function``: a ``def`` whose name matches the regular expression ``pattern``.
* ``compare-in``: ``"x" in y`` or ``"x" not in y`` for a string ``x`` in ``values``.
* ``number``: an integer in ``literals`` or ``2 ** n`` / ``1 << n`` for an ``n`` in ``powers``.
* ``keyword-set``: a set, tuple, list or dict literal holding at least ``min`` distinct strings of ``keywords``.

A hit counts as at home when its file and enclosing function are covered by the home's ``core`` or ``adapters``
(``file`` or ``file:Qualified.function``; a directory ends in ``/``). Every other hit must be in the allowlist, the
section of the toml after ``ALLOWLIST_MARKER``, keyed by (file, function, signature) with a count, the home and the
entry of ``docs`` / the audit it belongs to. A new hit fails, and so does an entry whose count is higher than what the
scan finds (the list only shrinks). ``--write-allowlist`` lowers it after hits were routed to their home and refuses
to add one; ``--seed`` writes it from the scan once, when the allowlist is empty.

Every ``legacy-*`` / ``legacy.*`` row id in ``src/`` must be a table of ``legacy_rows`` with its ``home`` and its
``fix`` (``"#nnnn"`` the issue that deletes the row, a plan group such as ``"G12"``, or ``"keep"``) and a row of the
"Legacy rows" table of ``docs/architecture.md`` with the same home and fix; the page names no row the code does not
define. The list only shrinks unless the owner agrees to a new row.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

try:
    from scripts import check_generation_store_usage as store
except ImportError:
    import check_generation_store_usage as store  # ty: ignore[unresolved-import]

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

ROOT = store.ROOT
SOURCE_ROOT = store.SOURCE_ROOT
DOCS_PATH = Path("docs/architecture.md")
CONFIG_PATH = Path("scripts/decision_homes.toml")
COMPILED_TEMPLATE_DIRECTORY = store.COMPILED_TEMPLATE_DIRECTORY
ALLOWLIST_MARKER = "# ==== allowlist (shrink-only; `--write-allowlist` rewrites everything below this line) ===="
SIGNATURE_KINDS = frozenset({"name", "call", "function", "compare-in", "number", "keyword-set"})
CONTEXTS = {"load": ast.Load, "store": ast.Store}
LEGACY_ROW = re.compile(r"legacy[-.][A-Za-z0-9_]+(?:[-.][A-Za-z0-9_]+)*(?![-\w])")
DOC_ROW = re.compile(r"^\| `(legacy[-.][^`]+)` \| (\S+) \| .* \| (#\d+|[A-Za-z0-9-]+) \|$", re.MULTILINE)
FIX_FORMAT = re.compile(r"#\d+|keep|[A-Z]{1,4}-?\d+[a-z]?(?:-\d+)?")
ALLOWLIST_START = re.compile(rf"^(?:{re.escape(ALLOWLIST_MARKER)}|\[allow\.)", re.MULTILINE)
STRING_OPTIONS = frozenset({"names", "keywords", "without", "mentions", "values", "receivers"})
INTEGER_OPTIONS = frozenset({"powers", "literals"})
REQUIRED_STRINGS = {"name": "names", "compare-in": "values", "keyword-set": "keywords"}
ITEM_KEYS = frozenset({"home", "count", "audit"})
Key = tuple[str, str, str]


class ConfigError(ValueError):
    """The decision-home table is malformed."""


@dataclass(frozen=True)
class Place:
    """A file, a directory (ending in ``/``) or one function (and its nested ones) of a file."""

    file: str
    function: str | None = None

    @classmethod
    def parse(cls, text: str) -> Place:
        """Parse ``file`` or ``file:Qualified.function``."""
        file, _, function = text.partition(":")
        return cls(file, function or None)

    def in_file(self, file: str) -> bool:
        """Return whether ``file`` holds this place."""
        return file.startswith(self.file) if self.file.endswith("/") else file == self.file

    def covers(self, file: str, function: str) -> bool:
        """Return whether a hit of ``function`` in ``file`` is inside this place."""
        if not self.in_file(file):
            return False
        return (
            self.file.endswith("/")
            or self.function is None
            or function == self.function
            or function.startswith(f"{self.function}.")
        )

    def __str__(self) -> str:
        """Return the place as the table writes it."""
        return self.file if self.function is None else f"{self.file}:{self.function}"


@dataclass(frozen=True)
class Home:
    """One decision with its core and adapters."""

    id: str
    decision: str
    core: tuple[Place, ...]
    adapters: tuple[Place, ...]

    def covers(self, file: str, function: str) -> bool:
        """Return whether the hit is in the core or an adapter."""
        return any(place.covers(file, function) for place in (*self.core, *self.adapters))

    @property
    def where(self) -> str:
        """The core places, as messages name them."""
        return ", ".join(str(place) for place in self.core)


@dataclass(frozen=True)
class Signature:
    """An AST shape that means "this code decides it"."""

    id: str
    home: str
    kind: str
    what: str
    options: dict[str, Any]
    paths: tuple[Place, ...]
    audit: Any

    def audit_for(self, file: str, function: str) -> str:
        """Return the audit entry id of a hit; a table maps files (or ``file:function``) to ids."""
        if isinstance(self.audit, str):
            return self.audit
        best = ("", self.audit["default"])
        for key, value in self.audit.items():
            if key != "default" and Place.parse(key).covers(file, function) and len(key) > len(best[0]):
                best = (key, value)
        return best[1]


@dataclass(frozen=True)
class LegacyRow:
    """A named exception that keeps the output of an earlier release."""

    id: str
    home: str
    fix: str


@dataclass(frozen=True)
class Entry:
    """An allowlisted hit count with the home and audit entry it belongs to."""

    home: str
    count: int
    audit: str


@dataclass(frozen=True)
class Config:
    """The parsed decision-home table."""

    homes: dict[str, Home]
    signatures: dict[str, Signature]
    legacy_rows: dict[str, LegacyRow]
    allowlist: dict[Key, Entry]
    text: str = ""


@dataclass(frozen=True)
class Hit:
    """One occurrence of a signature."""

    file: str
    function: str
    signature: str
    line: int
    column: int

    @property
    def key(self) -> Key:
        """The line-independent allowlist key."""
        return (self.file, self.function, self.signature)


@dataclass(frozen=True)
class Problem:
    """A finding; ``file`` is empty for findings about the tables."""

    message: str
    file: str = ""
    line: int = 0
    column: int = 0
    function: str = ""

    def format(self) -> str:
        """Format the finding for the command line."""
        if not self.file:
            return self.message
        suffix = f" (in {self.function})" if self.function else ""
        return f"{self.file}:{self.line}:{self.column}: {self.message}{suffix}"


@dataclass
class Scan:
    """What a scan found."""

    hits: list[Hit] = field(default_factory=list)
    scanned: set[str] = field(default_factory=set)

    @property
    def counts(self) -> Counter[Key]:
        """The number of hits per allowlist key."""
        return Counter(hit.key for hit in self.hits)


def _strings(table: dict[str, Any], key: str, owner: str, *, required: bool = False) -> tuple[str, ...]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value) or (required and not value):
        msg = f"{owner}: {key} must be a {'non-empty ' if required else ''}list of strings"
        raise ConfigError(msg)
    return tuple(value)


def _signature(home: str, name: str, table: dict[str, Any]) -> Signature:
    owner = f"signature {home}.{name}"
    kind = table.get("kind")
    if kind not in SIGNATURE_KINDS:
        msg = f"{owner}: kind must be one of {', '.join(sorted(SIGNATURE_KINDS))}"
        raise ConfigError(msg)
    audit = table.get("audit")
    if not (isinstance(audit, str) or (isinstance(audit, dict) and isinstance(audit.get("default"), str))):
        msg = f'{owner}: audit must be an entry id or a table with a "default" id'
        raise ConfigError(msg)
    options = {key: value for key, value in table.items() if key not in {"kind", "what", "paths", "audit"}}
    for key in STRING_OPTIONS & options.keys():
        _strings(options, key, owner)
    for key in INTEGER_OPTIONS & options.keys():
        if not isinstance(options[key], list) or not all(type(item) is int for item in options[key]):
            msg = f"{owner}: {key} must be a list of integers"
            raise ConfigError(msg)
    if kind == "function":
        options["pattern"] = re.compile(table.get("pattern", ""))
    elif kind in REQUIRED_STRINGS:
        options.setdefault("ctx", "load")
        if kind == "name" and options["ctx"] not in {*CONTEXTS, "any"}:
            msg = f"{owner}: ctx must be load, store or any"
            raise ConfigError(msg)
        _strings(options, REQUIRED_STRINGS[kind], owner, required=True)
    return Signature(
        id=f"{home}.{name}",
        home=home,
        kind=kind,
        what=str(table.get("what", "")),
        options=options,
        paths=tuple(Place.parse(path) for path in _strings(table, "paths", owner)),
        audit=audit,
    )


def parse_config(text: str) -> Config:
    """Parse the toml text of the decision-home table."""
    data = tomllib.loads(text)
    homes: dict[str, Home] = {}
    signatures: dict[str, Signature] = {}
    for home_id, table in data.get("homes", {}).items():
        homes[home_id] = Home(
            id=home_id,
            decision=str(table.get("decision", "")),
            core=tuple(Place.parse(item) for item in _strings(table, "core", f"home {home_id}", required=True)),
            adapters=tuple(Place.parse(item) for item in _strings(table, "adapters", f"home {home_id}")),
        )
        for name, signature in table.get("signatures", {}).items():
            signatures[f"{home_id}.{name}"] = _signature(home_id, name, signature)
    legacy_rows = {
        row_id: LegacyRow(row_id, str(table.get("home", "")), str(table.get("fix", "")))
        for row_id, table in data.get("legacy_rows", {}).items()
    }
    allowlist: dict[Key, Entry] = {}
    for file, functions in data.get("allow", {}).items():
        for function, items in functions.items():
            for signature, item in items.items():
                if not isinstance(item, dict) or set(item) != ITEM_KEYS or not isinstance(item["count"], int):
                    msg = f"allowlist entry {file} {function} {signature}: write {{ home, count, audit }}"
                    raise ConfigError(msg)
                allowlist[file, function, signature] = Entry(str(item["home"]), item["count"], str(item["audit"]))
    return Config(homes, signatures, legacy_rows, allowlist, text)


def load_config(path: Path | None = None) -> Config:
    """Read the decision-home table."""
    return parse_config((path or ROOT / CONFIG_PATH).read_text(encoding="utf-8"))


def _last_name(node: ast.AST) -> str | None:
    match node:
        case ast.Name(id=name):
            return name
        case ast.Attribute(attr=name):
            return name
    return None


def _names_in(nodes: Iterable[ast.AST]) -> Iterator[str]:
    for root in nodes:
        for node in ast.walk(root):
            if name := _last_name(node):
                yield name


def _string_elements(node: ast.AST) -> Iterator[str]:
    items = node.keys if isinstance(node, ast.Dict) else getattr(node, "elts", ())
    for item in items:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            yield item.value


def _matches(signature: Signature, node: ast.AST) -> bool:  # ruff: ignore[too-many-return-statements]
    options = signature.options
    match signature.kind:
        case "name":
            if signature.options["ctx"] != "any" and not isinstance(
                getattr(node, "ctx", None), CONTEXTS[options["ctx"]]
            ):
                return False
            if isinstance(node, ast.Attribute):
                receivers = options.get("receivers")
                return node.attr in options["names"] and (receivers is None or _last_name(node.value) in receivers)
            return isinstance(node, ast.Name) and "receivers" not in options and node.id in options["names"]
        case "call":
            if not isinstance(node, ast.Call):
                return False
            keywords = {keyword.arg for keyword in node.keywords}
            if "names" in options and _last_name(node.func) not in options["names"]:
                return False
            if not set(options.get("keywords", ())) <= keywords:
                return False
            if "mentions" in options and not (set(options["mentions"]) & set(_names_in(node.args))):
                return False
            without = set(options.get("without", ()))
            excluding = {
                keyword.arg
                for keyword in node.keywords
                if not (isinstance(keyword.value, ast.Constant) and keyword.value.value is False)
            }
            return not (without and (None in excluding or without & excluding))
        case "function":
            return isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and bool(
                options["pattern"].match(node.name)
            )
        case "compare-in":
            return (
                isinstance(node, ast.Compare)
                and isinstance(node.ops[0], ast.In | ast.NotIn)  # codespell:ignore
                and isinstance(node.left, ast.Constant)
                and node.left.value in options["values"]
            )
        case "number":
            match node:
                case ast.Constant(value=int() as value) if not isinstance(value, bool):
                    return value in options.get("literals", ())
                case ast.BinOp(
                    left=ast.Constant(value=base), op=ast.Pow() | ast.LShift() as op, right=ast.Constant(value=power)
                ):
                    return power in options.get("powers", ()) and base == (2 if isinstance(op, ast.Pow) else 1)
            return False
    return isinstance(node, ast.Set | ast.Tuple | ast.List | ast.Dict) and len(
        set(_string_elements(node)) & set(options["keywords"])
    ) >= options.get("min", 3)


NODE_TYPES = {
    "name": (ast.Attribute, ast.Name),
    "call": (ast.Call,),
    "function": (ast.FunctionDef, ast.AsyncFunctionDef),
    "compare-in": (ast.Compare,),
    "number": (ast.Constant, ast.BinOp),
    "keyword-set": (ast.Set, ast.Tuple, ast.List, ast.Dict),
}


def source_files(source_root: Path) -> list[Path]:
    """Return the Python files of the package that are scanned."""
    return [path for path in sorted(source_root.rglob("*.py")) if COMPILED_TEMPLATE_DIRECTORY not in path.parts]


def scan_tree(config: Config, source_root: Path | None = None) -> Scan:
    """Scan the package (or ``source_root``) for the signatures of the table."""
    root = source_root or ROOT / SOURCE_ROOT
    scan = Scan()
    by_type: dict[type[ast.AST], list[Signature]] = {}
    for signature in config.signatures.values():
        for node_type in NODE_TYPES[signature.kind]:
            by_type.setdefault(node_type, []).append(signature)
    for path in source_files(root):
        file = path.relative_to(root).as_posix()
        scan.scanned.add(file)
        signatures = {
            node_type: [item for item in items if not item.paths or any(place.in_file(file) for place in item.paths)]
            for node_type, items in by_type.items()
        }
        if not any(signatures.values()):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            for signature in signatures.get(type(node), ()):
                if _matches(signature, node):
                    function = store.function_at(path, node.lineno)
                    if signature.paths and not any(place.covers(file, function) for place in signature.paths):
                        continue
                    if not config.homes[signature.home].covers(file, function):
                        scan.hits.append(Hit(file, function, signature.id, node.lineno, node.col_offset))
    return scan


def _repo_path(file: str, source_root: Path | None) -> str:
    return (SOURCE_ROOT / file).as_posix() if source_root is None else file


def apply_allowlist(config: Config, scan: Scan, source_root: Path | None = None) -> list[Problem]:
    """Return the hits beyond their allowlisted count and the entries that no longer match."""
    allowed = config.allowlist
    emitted: Counter[Key] = Counter()
    problems: list[Problem] = []
    for hit in sorted(scan.hits, key=lambda item: (item.file, item.line, item.column, item.signature)):
        key = (_repo_path(hit.file, source_root), hit.function, hit.signature)
        emitted[key] += 1
        if emitted[key] > (allowed[key].count if key in allowed else 0):
            signature = config.signatures[hit.signature]
            home = config.homes[signature.home]
            problems.append(
                Problem(
                    f"decision {home.id} ({home.decision}) has its home in {home.where}; {signature.what}. "
                    f"Route this site through the home, or add it to {CONFIG_PATH.as_posix()} with the owner's OK",
                    _repo_path(hit.file, source_root),
                    hit.line,
                    hit.column,
                    hit.function,
                )
            )
    scanned = {_repo_path(file, source_root) for file in scan.scanned}
    problems.extend(
        Problem(
            f"stale allowlist entry {signature}: expected {entry.count}, found {emitted[file, function, signature]}; "
            f"remove or lower it in {CONFIG_PATH.as_posix()}",
            file,
            0,
            0,
            function,
        )
        for (file, function, signature), entry in sorted(allowed.items())
        if (file not in scanned or emitted[file, function, signature] < entry.count)
    )
    return problems


@cache
def _function_names(path: Path) -> frozenset[str]:
    """Return the qualified name of every function in ``path``, named as ``store.function_at`` names them."""
    names: set[str] = set()
    pending: list[tuple[ast.AST, tuple[str, ...]]] = [(ast.parse(path.read_text(encoding="utf-8")), ())]
    while pending:
        node, scope = pending.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = (*scope, child.name)
                if not isinstance(child, ast.ClassDef):
                    names.add(".".join(inner))
                pending.append((child, inner))
            else:
                pending.append((child, scope))
    return frozenset(names)


def _place_exists(place: Place, source_root: Path) -> bool:
    path = source_root / place.file
    if place.file.endswith("/"):
        return path.is_dir()
    return path.is_file() and (place.function is None or place.function in _function_names(path))


def table_problems(config: Config, source_root: Path | None = None) -> list[Problem]:
    """Return what is wrong with the table itself: missing places, unknown homes, and entries of another home."""
    root = source_root or ROOT / SOURCE_ROOT
    problems: list[Problem] = []
    for home in config.homes.values():
        problems.extend(
            Problem(f"home {home.id}: {place} does not exist")
            for place in (*home.core, *home.adapters)
            if not _place_exists(place, root)
        )
    problems.extend(
        Problem(f"signature {signature.id}: {place} does not exist")
        for signature in config.signatures.values()
        for place in signature.paths
        if not _place_exists(place, root)
    )
    problems.extend(
        Problem(
            f"allowlist entry {file} {function} {signature}: the signature is unknown, belongs to another home than "
            f"{entry.home}, or the count is below 1"
        )
        for (file, function, signature), entry in sorted(config.allowlist.items())
        if config.signatures.get(signature) is None
        or config.signatures[signature].home != entry.home
        or entry.count < 1
    )
    return problems


def source_legacy_rows(source_root: Path | None = None) -> set[str]:
    """Return the ``legacy-*`` row ids that ``src/`` names."""
    return {
        row
        for path in source_files(source_root or ROOT / SOURCE_ROOT)
        for row in LEGACY_ROW.findall(path.read_text(encoding="utf-8"))
    }


def documented_rows(docs_text: str) -> dict[str, tuple[str, str]]:
    """Return the rows of the "Legacy rows" table of ``docs/architecture.md`` with their home and fix."""
    return {row: (home, fix) for row, home, fix in DOC_ROW.findall(docs_text)}


def legacy_row_problems(config: Config, docs_text: str, source_root: Path | None = None) -> list[Problem]:
    """Return the ``legacy-*`` rows of ``src/``, the table and the docs that disagree."""
    in_source = source_legacy_rows(source_root)
    documented = documented_rows(docs_text)
    table = CONFIG_PATH.as_posix()
    problems = [
        Problem(f"row {row} is in src/ but not in {table}: add it as a legacy_rows table with its home and fix")
        for row in sorted(in_source - config.legacy_rows.keys())
    ]
    problems.extend(
        Problem(f"stale legacy row {row}: src/ no longer names it; remove it from {table}")
        for row in sorted(config.legacy_rows.keys() - in_source)
    )
    for row_id, row in sorted(config.legacy_rows.items()):
        if row.home not in config.homes:
            problems.append(Problem(f"row {row_id}: home {row.home!r} is not in homes"))
        if not FIX_FORMAT.fullmatch(row.fix):
            problems.append(Problem(f'row {row_id}: fix must be "#nnnn", a plan group such as "G12", or "keep"'))
        if row_id in in_source and documented.get(row_id) != (row.home, row.fix):
            problems.append(
                Problem(
                    f"row {row_id}: the Legacy rows table of {DOCS_PATH.as_posix()} must list it with home "
                    f"{row.home} and fix {row.fix}, found {documented.get(row_id)}"
                )
            )
    problems.extend(
        Problem(f"{DOCS_PATH.as_posix()} names row {row}, which src/ does not define")
        for row in sorted(set(LEGACY_ROW.findall(docs_text)) - in_source)
    )
    return problems


def check_all(
    config: Config | None = None,
    *,
    source_root: Path | None = None,
    docs_text: str | None = None,
) -> tuple[list[Problem], Scan]:
    """Run every check and return the findings with the scan."""
    config = config or load_config()
    docs_text = (ROOT / DOCS_PATH).read_text(encoding="utf-8") if docs_text is None else docs_text
    scan = scan_tree(config, source_root)
    problems = [
        *table_problems(config, source_root),
        *apply_allowlist(config, scan, source_root),
        *legacy_row_problems(config, docs_text, source_root),
    ]
    return problems, scan


def format_report(problems: Iterable[Problem]) -> str:
    """Return the findings, one per line."""
    return "".join(f"{problem.format()}\n" for problem in problems)


def _toml_key(text: str) -> str:
    return json.dumps(text)


def format_allowlist(entries: dict[Key, Entry]) -> str:
    """Return the allowlist section for ``entries``."""
    lines: list[str] = []
    section: tuple[str, str] | None = None
    for (file, function, signature), entry in sorted(entries.items()):
        if section != (file, function):
            section = (file, function)
            lines.extend(("", f"[allow.{_toml_key(file)}.{_toml_key(function)}]"))
        lines.append(
            f"{_toml_key(signature)} = {{ home = {_toml_key(entry.home)}, count = {entry.count}, "
            f"audit = {_toml_key(entry.audit)} }}"
        )
    return "\n".join(lines) + "\n"


def write_allowlist(
    config: Config,
    scan: Scan,
    *,
    seed: bool,
    path: Path | None = None,
    source_root: Path | None = None,
) -> list[Key]:
    """Write the allowlist from the scan; return the keys that would have to grow (nothing is written then)."""
    config_path = path or ROOT / CONFIG_PATH
    counts = scan.counts
    entries: dict[Key, Entry] = {}
    grown: list[Key] = []
    for key, found in sorted(counts.items()):
        repo_key = (_repo_path(key[0], source_root), key[1], key[2])
        signature = config.signatures[key[2]]
        current = config.allowlist.get(repo_key)
        if seed:
            entries[repo_key] = Entry(signature.home, found, signature.audit_for(key[0], key[1]))
        elif current is None or found > current.count:
            grown.append(repo_key)
        else:
            entries[repo_key] = Entry(current.home, found, current.audit)
    if grown:
        return grown
    head = ALLOWLIST_START.split(config.text, maxsplit=1)[0].rstrip("\n")
    config_path.write_text(f"{head}\n\n{ALLOWLIST_MARKER}\n{format_allowlist(entries)}", encoding="utf-8")
    return []


def allowlist_sizes(config: Config) -> dict[str, tuple[int, int]]:
    """Return (entries, hits) per home."""
    sizes: dict[str, list[int]] = {}
    for entry in config.allowlist.values():
        size = sizes.setdefault(entry.home, [0, 0])
        size[0] += 1
        size[1] += entry.count
    return {home: (entries, hits) for home, (entries, hits) in sorted(sizes.items())}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-allowlist", action="store_true", help="lower the allowlist; never adds an entry")
    parser.add_argument("--seed", action="store_true", help="write the allowlist from the scan; needs an empty one")
    parser.add_argument("--sizes", action="store_true", help="print the allowlist size per home")
    parser.add_argument("--config", type=Path, help="the decision-home table (default: scripts/decision_homes.toml)")
    parser.add_argument("--source-root", type=Path, help="the package to scan (default: src/datamodel_code_generator)")
    parser.add_argument("--docs", type=Path, help="the architecture page (default: docs/architecture.md)")
    args = parser.parse_args(argv)

    config_path = args.config or ROOT / CONFIG_PATH
    config = load_config(config_path)
    if args.sizes:
        for home, (entries, hits) in allowlist_sizes(config).items():
            print(f"{home}: {entries} entries, {hits} hits")
        return 0
    if args.seed and config.allowlist:
        print("--seed needs an empty allowlist", file=sys.stderr)
        return 1
    if args.seed or args.write_allowlist:
        if problems := table_problems(config, args.source_root):
            print("Decision home violations found:", file=sys.stderr)
            print(format_report(problems), end="", file=sys.stderr)
            return 1
        scan = scan_tree(config, args.source_root)
        if grown := write_allowlist(config, scan, seed=args.seed, path=config_path, source_root=args.source_root):
            print("Refusing to add allowlist entries; route these sites through their home:", file=sys.stderr)
            for file, function, signature in grown:
                print(f"{file}: {function}: {signature}", file=sys.stderr)
            return 1
        config = load_config(config_path)
    docs_text = (args.docs or ROOT / DOCS_PATH).read_text(encoding="utf-8")
    problems, _ = check_all(config, source_root=args.source_root, docs_text=docs_text)
    if problems:
        print("Decision home violations found:", file=sys.stderr)
        print(format_report(problems), end="", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
