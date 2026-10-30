"""Keep every decision in its home, and every ``legacy-*`` row named and planned.

``scripts/decision_homes.toml`` lists, for each decision that has one home (``docs/architecture.md``, "One home per
decision"), the home's core and adapters and the AST *signatures* that mean "this code decides it". The checker scans
``src/datamodel_code_generator`` with ``ast``, so it never imports the package, and reports a signature found outside
its home as

    decision <X> has its home in <file:function>

Signature kinds (``kind`` in the toml; ``paths`` limits the files a signature scans, ``exclude`` leaves places out of
them, ``what`` words the message):

* ``name``: a read, write or any use (``ctx`` = ``load`` (default), ``store`` or ``any``) of an attribute or a name
  in ``names``; ``receivers`` limits an attribute to values whose last name is listed (``reference.name``).
* ``call``: a call whose callee ends in one of ``names`` (any callee when omitted), that passes every keyword in
  ``keywords`` and none of ``without`` (a keyword passed a literal ``False`` does not count; a call that unpacks
  ``**`` is skipped when ``without`` is set), whose positional arguments name one of ``mentions`` (when given) and hold
  one of ``strings`` as a string literal (when given).
* ``function``: a ``def`` whose name matches the regular expression ``pattern``.
* ``compare-in``: ``"x" in y`` or ``"x" not in y`` for a string ``x`` in ``values``.
* ``compare-eq``: ``y == "x"`` or ``y != "x"`` (the string on either side) for a string ``x`` in ``values``.
* ``split-each``: the names of a sequence split one by one at a separator ``s`` in ``strings``: a comprehension that
  iterates ``y.split(s)`` for a ``y`` an earlier ``for`` clause of it binds, a ``for`` loop that calls ``y.split(s)``
  on its own target, or ``s.join(...).split(s)``.
* ``number``: an integer in ``literals`` or ``2 ** n`` / ``1 << n`` for an ``n`` in ``powers``.
* ``keyword-set``: a set, tuple, list or dict literal holding at least ``min`` distinct strings of ``keywords``; a
  dict holds its keys, and its values too with ``dict_values = true`` (a table that maps names to the spellings).
* ``key``: a subscript ``x["k"]`` that is read (not assigned or deleted) or a call ``x.get("k")`` / ``x.pop("k")`` /
  ``x.setdefault("k", ...)`` whose string key is in ``keys`` (``names`` and ``mentions`` match names, not strings, so
  a raw read of a loaded document is invisible to them).
* ``literal``: a string literal, or an f-string with a literal part, that holds one of ``contains`` (the spelling of a
  value that only its home may write); an f-string is one hit at its own position.
* ``display``: an f-string, or a ``+`` chain of strings, that writes ``", ".join(...)`` right after an opening bracket
  of ``brackets`` (``(`` a call or tuple display, ``[`` a list display): Python source spelled by hand; a ``[`` right
  after a name, a value or a closing bracket opens a subscript and does not count.
* ``keyword``: an f-string that writes ``{name}={value}``, a value, the text ``=`` and another value: a keyword
  argument whose keyword is data, spelled by hand.
* ``branch``: an ``if`` statement or a conditional expression whose test compares with a string in ``values`` and one
  of whose branches calls a callee ending in one of ``names`` (a rule applied only where a value is that string).
* ``name-search``: a ``while`` loop whose test reads a name that the loop builds itself (its test or body, not a
  function inside it, assigns the name an f-string or a value holding one, or adds a string to it with ``+=``): a
  search for the first name a scope does not hold.

A hit counts as at home when its file and enclosing function are covered by the home's ``core``, ``adapters`` or
``planned`` places (``file``, ``file:Qualified.function`` or ``file:Class``; a directory ends in ``/``). A ``planned``
place is the core a later change creates: it need not exist yet, and the check fails once it does, so that the change
moves it to ``core``.

A ``call`` hit is also at home wherever it occurs when its callee is a function or class that the home's ``core``
defines: calling the core is the reuse a home is for, so its callers need no adapter entry (an adapter reads raw facts
and hands them to the core). The callee is looked up like Python does, in the scopes around the call and then in the
module: a parameter, variable, nested definition or ``global`` rebinding of the name is not the imported function, and
nor is a module-level rebinding of it. An import leads to a module-level function or class (re-exports followed; a
method or nested definition of that name is not one), and ``module.name`` leads through the module the alias names.
A hand-written copy outside the core, a method of a value, a name outside the package and every other kind of
signature are not exempt.

Every other hit must be in the allowlist, the
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
from pathlib import Path, PurePosixPath
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
SIGNATURE_KINDS = frozenset({
    "name",
    "call",
    "function",
    "compare-in",
    "compare-eq",
    "split-each",
    "number",
    "keyword-set",
    "key",
    "literal",
    "display",
    "keyword",
    "branch",
    "name-search",
})
CONTEXTS = {"load": ast.Load, "store": ast.Store}
LEGACY_ROW = re.compile(r"legacy[-.][A-Za-z0-9_]+(?:[-.][A-Za-z0-9_]+)*(?![-\w])")
DOC_ROW = re.compile(r"^\| `(legacy[-.][^`]+)` \| (\S+) \| .* \| (#\d+|[A-Za-z0-9-]+) \|$", re.MULTILINE)
FIX_FORMAT = re.compile(r"#\d+|keep|[A-Z]{1,4}-?\d+[a-z]?(?:-\d+)?")
ALLOWLIST_START = re.compile(rf"^(?:{re.escape(ALLOWLIST_MARKER)}|\[allow\.)", re.MULTILINE)
STRING_OPTIONS = frozenset({
    "names",
    "keywords",
    "without",
    "mentions",
    "strings",
    "values",
    "receivers",
    "keys",
    "contains",
    "brackets",
})
INTEGER_OPTIONS = frozenset({"powers", "literals"})
REQUIRED_STRINGS = {
    "name": "names",
    "compare-in": "values",
    "compare-eq": "values",
    "split-each": "strings",
    "keyword-set": "keywords",
    "key": "keys",
    "literal": "contains",
    "display": "brackets",
    "branch": "values",
}
KEY_METHODS = frozenset({"get", "pop", "setdefault"})
ITEM_KEYS = frozenset({"home", "count", "audit"})
Key = tuple[str, str, str]
Origin = tuple[str, str]
UNRESOLVED: Origin = ("", "")


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
    planned: tuple[Place, ...] = ()

    def covers(self, file: str, function: str) -> bool:
        """Return whether the hit is in the core, an adapter or a planned place."""
        return any(place.covers(file, function) for place in (*self.core, *self.adapters, *self.planned))

    def in_core(self, file: str, function: str) -> bool:
        """Return whether the function or class ``function`` defined in ``file`` is part of the core."""
        return any(place.covers(file, function) for place in self.core)

    @property
    def where(self) -> str:
        """The core places (the planned ones when none exists yet), as messages name them."""
        return ", ".join(str(place) for place in self.core or self.planned)


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
    exclude: tuple[Place, ...] = ()

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
    options = {key: value for key, value in table.items() if key not in {"kind", "what", "paths", "exclude", "audit"}}
    for key in STRING_OPTIONS & options.keys():
        _strings(options, key, owner)
    for key in INTEGER_OPTIONS & options.keys():
        if not isinstance(options[key], list) or not all(type(item) is int for item in options[key]):
            msg = f"{owner}: {key} must be a list of integers"
            raise ConfigError(msg)
    if not isinstance(options.get("dict_values", False), bool):
        msg = f"{owner}: dict_values must be a boolean"
        raise ConfigError(msg)
    if kind == "function":
        options["pattern"] = re.compile(table.get("pattern", ""))
    elif kind in REQUIRED_STRINGS:
        options.setdefault("ctx", "load")
        if kind == "name" and options["ctx"] not in {*CONTEXTS, "any"}:
            msg = f"{owner}: ctx must be load, store or any"
            raise ConfigError(msg)
        _strings(options, REQUIRED_STRINGS[kind], owner, required=True)
        if kind == "branch":
            _strings(options, "names", owner, required=True)
    return Signature(
        id=f"{home}.{name}",
        home=home,
        kind=kind,
        what=str(table.get("what", "")),
        options=options,
        paths=tuple(Place.parse(path) for path in _strings(table, "paths", owner)),
        audit=audit,
        exclude=tuple(Place.parse(path) for path in _strings(table, "exclude", owner)),
    )


def parse_config(text: str) -> Config:
    """Parse the toml text of the decision-home table."""
    data = tomllib.loads(text)
    homes: dict[str, Home] = {}
    signatures: dict[str, Signature] = {}
    for home_id, table in data.get("homes", {}).items():
        owner = f"home {home_id}"
        planned = tuple(Place.parse(item) for item in _strings(table, "planned", owner))
        homes[home_id] = Home(
            id=home_id,
            decision=str(table.get("decision", "")),
            core=tuple(Place.parse(item) for item in _strings(table, "core", owner, required=not planned)),
            adapters=tuple(Place.parse(item) for item in _strings(table, "adapters", owner)),
            planned=planned,
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


def _string_arguments(nodes: Iterable[ast.AST]) -> Iterator[str]:
    for node in nodes:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def _string_elements(node: ast.AST, *, values: bool = False) -> Iterator[str]:
    items = [*node.keys, *(node.values if values else ())] if isinstance(node, ast.Dict) else getattr(node, "elts", ())
    for item in items:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            yield item.value


def _key_read(node: ast.AST) -> str | None:
    """Return the string key that ``x["k"]``, ``x.get("k")``, ``x.pop("k")`` or ``x.setdefault("k", ...)`` reads.

    A subscript that is assigned or deleted (``x["k"] = v``, ``del x["k"]``) reads no key.
    """
    match node:
        case ast.Subscript(slice=ast.Constant(value=str() as key), ctx=ast.Load()):
            return key
        case ast.Call(func=ast.Attribute(attr=method), args=[ast.Constant(value=str() as key), *_]):
            return key if method in KEY_METHODS else None
    return None


def _string_parts(node: ast.AST) -> list[ast.AST]:
    """Return the parts of an f-string or a ``+`` chain in order: string constants and the values between them."""
    match node:
        case ast.JoinedStr(values=values):
            return [part.value if isinstance(part, ast.FormattedValue) else part for part in values]
        case ast.BinOp(left=left, op=ast.Add(), right=right):
            return [*_string_parts(left), *_string_parts(right)]
    return [node]


def _is_comma_join(node: ast.AST) -> bool:
    match node:
        case ast.Call(func=ast.Attribute(value=ast.Constant(value=", "), attr="join")):
            return True
    return False


def _opens_display(parts: list[ast.AST], index: int, brackets: Iterable[str]) -> bool:
    """Return whether the part before ``parts[index]`` ends with an opening bracket of a display or a call."""
    match parts[index - 1] if index else None:
        case ast.Constant(value=str() as text) if text and text[-1] in brackets:
            before = text[:-1]
            subscript = (bool(before) and (before[-1].isidentifier() or before[-1] in ")]")) or (
                not before and index > 1
            )
            return text[-1] != "[" or not subscript
    return False


def _hand_display(node: ast.AST, brackets: Iterable[str]) -> bool:
    """Return whether ``node`` joins expression texts with ``", "`` right inside a bracket it opens itself.

    A ``+`` chain counts at the operation whose right operand is the join, so a chain is one hit.
    """
    if isinstance(node, ast.BinOp):
        if not (isinstance(node.op, ast.Add) and _is_comma_join(node.right)):
            return False
        parts = [*_string_parts(node.left), node.right]
        return _opens_display(parts, len(parts) - 1, brackets)
    parts = _string_parts(node)
    return any(_is_comma_join(part) and _opens_display(parts, index, brackets) for index, part in enumerate(parts))


def _hand_keyword(node: ast.AST) -> bool:
    """Return whether the f-string ``node`` writes a value, the text ``=`` alone and another value."""
    parts = _string_parts(node)
    return any(
        isinstance(part, ast.Constant) and part.value == "=" and not isinstance(before, ast.Constant)
        for before, part, after in zip(parts, parts[1:], parts[2:], strict=False)
        if not isinstance(after, ast.Constant)
    )


def _branch_calls(node: ast.AST, values: Iterable[str], names: Iterable[str]) -> bool:
    """Return whether an ``if`` or a conditional expression tests for one of ``values`` and a branch calls ``names``."""
    if not isinstance(node, ast.If | ast.IfExp) or not isinstance(test := node.test, ast.Compare):
        return False
    if not {item.value for item in (test.left, *test.comparators) if isinstance(item, ast.Constant)} & set(values):
        return False
    branches = (node.body, node.orelse) if isinstance(node, ast.IfExp) else (*node.body, *node.orelse)
    return any(
        isinstance(call, ast.Call) and _last_name(call.func) in names
        for branch in branches
        for call in ast.walk(branch)
    )


def _split_receiver(node: ast.AST, separators: Iterable[str]) -> ast.AST | None:
    """Return ``y`` of a call ``y.split(s)`` for a separator ``s`` of ``separators``, or None for any other node."""
    match node:
        case ast.Call(func=ast.Attribute(value=receiver, attr="split"), args=[ast.Constant(value=str() as separator)]):
            return receiver if separator in separators else None
    return None


def _bound_names(targets: Iterable[ast.AST]) -> set[str]:
    return {name.id for target in targets for name in ast.walk(target) if isinstance(name, ast.Name)}


def _splits_each(node: ast.AST, separators: Iterable[str]) -> bool:
    """Return whether ``node`` splits the names of a sequence one by one at one of ``separators`` (``split-each``)."""
    match node:
        case ast.ListComp() | ast.SetComp() | ast.GeneratorExp() | ast.DictComp():
            generators = node.generators
            return any(
                isinstance(receiver := _split_receiver(clause.iter, separators), ast.Name)
                and receiver.id in _bound_names(earlier.target for earlier in generators[:index])
                for index, clause in enumerate(generators)
            )
        case ast.For():
            targets = _bound_names([node.target])
            return any(
                isinstance(receiver := _split_receiver(call, separators), ast.Name) and receiver.id in targets
                for statement in node.body
                for call in ast.walk(statement)
            )
        case ast.Call():
            match _split_receiver(node, separators):
                case ast.Call(func=ast.Attribute(value=ast.Constant(value=str() as joiner), attr="join")):
                    return joiner in separators
    return False


def _holds_f_string(value: ast.AST) -> bool:
    """Return whether the text ``value`` assigns holds an f-string, outside call arguments and lambda bodies."""
    pending = [value]
    while pending:
        node = pending.pop()
        if isinstance(node, ast.JoinedStr):
            return True
        if isinstance(node, ast.Lambda):
            continue
        pending.extend([node.func] if isinstance(node, ast.Call) else ast.iter_child_nodes(node))
    return False


def _searches_name(loop: ast.While) -> bool:
    """Return whether ``loop`` tests a name that it builds itself as text (``name-search``)."""
    built: set[str] = set()
    pending: list[ast.AST] = [loop.test, *loop.body]
    while pending:
        node = pending.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        pending.extend(ast.iter_child_nodes(node))
        targets: list[ast.expr] = []
        if isinstance(node, ast.NamedExpr) and _holds_f_string(node.value):
            targets = [node.target]
        elif isinstance(node, ast.Assign) and _holds_f_string(node.value):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None and _holds_f_string(node.value):
            targets = [node.target]
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add):
            text = isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            targets = [node.target] if text or _holds_f_string(node.value) else []
        built.update(target.id for target in targets if isinstance(target, ast.Name))
    return any(isinstance(node, ast.Name) and node.id in built for node in ast.walk(loop.test))


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
            if "strings" in options and not (set(options["strings"]) & set(_string_arguments(node.args))):
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
        case "compare-eq":
            return (
                isinstance(node, ast.Compare)
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.Eq | ast.NotEq)
                and any(
                    isinstance(side, ast.Constant) and side.value in options["values"]
                    for side in (node.left, *node.comparators)
                )
            )
        case "split-each":
            return _splits_each(node, options["strings"])
        case "number":
            match node:
                case ast.Constant(value=int() as value) if not isinstance(value, bool):
                    return value in options.get("literals", ())
                case ast.BinOp(
                    left=ast.Constant(value=base), op=ast.Pow() | ast.LShift() as op, right=ast.Constant(value=power)
                ):
                    return power in options.get("powers", ()) and base == (2 if isinstance(op, ast.Pow) else 1)
            return False
        case "key":
            return _key_read(node) in options["keys"]
        case "branch":
            return _branch_calls(node, options["values"], options["names"])
        case "literal":
            texts = [
                part.value
                for part in (node.values if isinstance(node, ast.JoinedStr) else [node])
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            ]
            return any(spelling in text for text in texts for spelling in options["contains"])
        case "display":
            return _hand_display(node, options["brackets"])
        case "keyword":
            return _hand_keyword(node)
        case "name-search":
            return isinstance(node, ast.While) and _searches_name(node)
    return isinstance(node, ast.Set | ast.Tuple | ast.List | ast.Dict) and len(
        set(_string_elements(node, values=options.get("dict_values", False))) & set(options["keywords"])
    ) >= options.get("min", 3)


NODE_TYPES = {
    "name": (ast.Attribute, ast.Name),
    "call": (ast.Call,),
    "function": (ast.FunctionDef, ast.AsyncFunctionDef),
    "compare-in": (ast.Compare,),
    "compare-eq": (ast.Compare,),
    "split-each": (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp, ast.For, ast.Call),
    "number": (ast.Constant, ast.BinOp),
    "keyword-set": (ast.Set, ast.Tuple, ast.List, ast.Dict),
    "key": (ast.Subscript, ast.Call),
    "literal": (ast.Constant, ast.JoinedStr),
    "display": (ast.JoinedStr, ast.BinOp),
    "keyword": (ast.JoinedStr,),
    "branch": (ast.If, ast.IfExp),
    "name-search": (ast.While,),
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
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        f_string_parts: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                f_string_parts.update(id(part) for part in node.values if isinstance(part, ast.Constant))
            elif id(node) in f_string_parts:
                continue
            for signature in signatures.get(type(node), ()):
                if _matches(signature, node):
                    function = store.function_at(path, node.lineno)
                    if (signature.paths and not any(place.covers(file, function) for place in signature.paths)) or any(
                        place.covers(file, function) for place in signature.exclude
                    ):
                        continue
                    home = config.homes[signature.home]
                    if not home.covers(file, function) and not (
                        signature.kind == "call"
                        and isinstance(node, ast.Call)
                        and calls_core(home, root, file, tree, node)
                    ):
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
    """Return the qualified name of every function and class in ``path``, named as ``store.function_at`` names them."""
    names: set[str] = set()
    pending: list[tuple[ast.AST, tuple[str, ...]]] = [(ast.parse(path.read_text(encoding="utf-8")), ())]
    while pending:
        node, scope = pending.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = (*scope, child.name)
                names.add(".".join(inner))
                pending.append((child, inner))
            else:
                pending.append((child, scope))
    return frozenset(names)


Binder = tuple[str, tuple[str, ...] | None, str | None]
Scope = (
    ast.Module
    | ast.FunctionDef
    | ast.AsyncFunctionDef
    | ast.ClassDef
    | ast.Lambda
    | ast.ListComp
    | ast.SetComp
    | ast.DictComp
    | ast.GeneratorExp
)
DEFINITION: Binder = ("def", None, None)
REBINDING: Binder = ("other", None, None)
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _module_parts(name: str, package: tuple[str, ...], module: str | None, level: int) -> tuple[str, ...] | None:
    """Return the path under the package ``name`` of the module an import names, or None for a module outside it."""
    parts = tuple(module.split(".")) if module else ()
    if level:
        return (*package[: len(package) - level + 1], *parts) if level <= len(package) + 1 else None
    return parts[1:] if parts[:1] == (name,) else None


def _module_file(root: Path, parts: tuple[str, ...]) -> str | None:
    """Return the file under ``root`` of the module at ``parts``, or None when the package has no such module."""
    base = "/".join(parts)
    return next((file for file in (f"{base}.py", f"{base}/__init__.py".lstrip("/")) if (root / file).is_file()), None)


def _parameters(arguments: ast.arguments) -> list[ast.arg]:
    parameters = (*arguments.posonlyargs, *arguments.args, arguments.vararg, *arguments.kwonlyargs, arguments.kwarg)
    return [parameter for parameter in parameters if parameter]


def _scope_roots(scope: Scope) -> list[ast.AST]:
    """Return the nodes that bind the names of ``scope``: its comprehension targets, parameters and body."""
    if isinstance(scope, COMPREHENSIONS):
        return [generator.target for generator in scope.generators]
    if isinstance(scope, ast.Module | ast.ClassDef):
        return list(scope.body)
    if isinstance(scope, ast.Lambda):
        return [*_parameters(scope.args), scope.body]
    return [*_parameters(scope.args), *scope.body]


def _own_nodes(scope: Scope) -> Iterator[ast.AST]:
    """Yield the nodes of ``scope`` itself: a nested scope is yielded (it binds its name) but not entered."""
    pending = _scope_roots(scope)
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, COMPREHENSIONS):
            yield from (inner.target for inner in ast.walk(node) if isinstance(inner, ast.NamedExpr))
        elif not isinstance(node, SCOPES):
            pending.extend(ast.iter_child_nodes(node))


def _binds(root: Path, file: str, node: ast.AST, *, module_level: bool) -> Iterator[tuple[str, Binder]]:
    """Yield each name ``node`` binds with what binds it: an import, a definition or any other (re)binding."""
    package = PurePosixPath(file).parent.parts
    if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
        yield node.id, REBINDING
    elif isinstance(node, ast.arg):
        yield node.arg, REBINDING
    elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name:
        yield node.name, REBINDING
    elif isinstance(node, ast.MatchMapping) and node.rest:
        yield node.rest, REBINDING
    elif isinstance(node, ast.Global | ast.Nonlocal):
        yield from ((name, REBINDING) for name in node.names)
    elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        yield node.name, DEFINITION if module_level else REBINDING
    elif isinstance(node, ast.ImportFrom):
        module = _module_parts(root.name, package, node.module, node.level)
        yield from ((alias.asname or alias.name, ("import", module, alias.name)) for alias in node.names)
    elif isinstance(node, ast.Import):
        for alias in node.names:
            imported = alias.name if alias.asname else alias.name.partition(".")[0]
            yield alias.asname or imported, ("import", _module_parts(root.name, package, imported, 0), None)


def _binders(root: Path, file: str, scope: Scope) -> dict[str, list[Binder]]:
    """Return what binds each name in ``scope`` itself."""
    binders: dict[str, list[Binder]] = {}
    for node in _own_nodes(scope):
        for name, binder in _binds(root, file, node, module_level=isinstance(scope, ast.Module)):
            binders.setdefault(name, []).append(binder)
    return binders


@cache
def _module_binders(root: Path, file: str) -> dict[str, list[Binder]]:
    """Return what binds each module-level name of ``file``; a ``global`` statement anywhere rebinds its names."""
    tree = ast.parse((root / file).read_text(encoding="utf-8"))
    binders = _binders(root, file, tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            for name in node.names:
                binders.setdefault(name, []).append(REBINDING)
    return binders


def _enclosing_scopes(tree: ast.Module, target: ast.Call) -> list[Scope]:
    """Return the scopes in which ``target`` looks a name up, innermost first and the module last.

    A function, lambda or class scope starts at its body (not at its decorators, defaults or bases) and a
    comprehension scope at the whole comprehension. A class scope counts only for code directly in the class body.
    """
    pending: list[tuple[ast.AST, tuple[Scope, ...]]] = [(tree, (tree,))]
    while True:
        node, scopes = pending.pop()
        if node is target:
            return [
                scope for index, scope in enumerate(reversed(scopes)) if not (index and isinstance(scope, ast.ClassDef))
            ]
        children: list[tuple[ast.AST, tuple[Scope, ...]]] = []
        for attribute, value in ast.iter_fields(node):
            inner = scopes
            if isinstance(node, COMPREHENSIONS) or (isinstance(node, SCOPES) and attribute == "body"):
                inner = (*scopes, node)
            children.extend(
                (child, inner)
                for child in (value if isinstance(value, list) else [value])
                if isinstance(child, ast.AST) and (getattr(child, "end_lineno", None) or target.lineno) >= target.lineno
            )
        pending.extend(reversed(children))


def _imported_origins(
    root: Path, module: tuple[str, ...] | None, imported: str | None, seen: frozenset[Origin]
) -> set[Origin]:
    if module is None:
        return {UNRESOLVED}
    target = _module_file(root, module)
    if imported is None:
        return {(target, "")} if target else {UNRESOLVED}
    found = _origins(root, target, imported, seen) if target else {UNRESOLVED}
    if found == {UNRESOLVED} and (submodule := _module_file(root, (*module, imported))):
        return {(submodule, "")}
    return set(found)


def _binder_origins(
    root: Path, file: str, name: str, binders: Iterable[Binder], seen: frozenset[Origin]
) -> frozenset[Origin]:
    """Return what ``name`` is where ``binders`` bind it, as ``(file, name)`` of a module-level definition.

    An import leads to what it imports (re-exports followed; a module is ``(file, "")``). Any other binding, and a name
    that nothing binds, is ``UNRESOLVED``.
    """
    origins: set[Origin] = set()
    for kind, module, imported in binders:
        if kind == "import":
            origins |= _imported_origins(root, module, imported, seen)
        else:
            origins.add((file, name) if kind == "def" else UNRESOLVED)
    return frozenset(origins or {UNRESOLVED})


def _origins(root: Path, file: str, name: str, seen: frozenset[Origin] = frozenset()) -> frozenset[Origin]:
    """Return what the module-level ``name`` of the module ``file`` is."""
    if (file, name) in seen:
        return frozenset({UNRESOLVED})
    return _binder_origins(root, file, name, _module_binders(root, file).get(name, ()), seen | {(file, name)})


def _scoped_origins(root: Path, file: str, tree: ast.Module, call: ast.Call, name: str) -> frozenset[Origin]:
    """Return what ``name`` is where ``call`` looks it up: the innermost scope that binds it, else the module."""
    for scope in _enclosing_scopes(tree, call)[:-1]:
        if binders := _binders(root, file, scope).get(name):
            return _binder_origins(root, file, name, binders, frozenset())
    return _origins(root, file, name)


def calls_core(home: Home, root: Path, file: str, tree: ast.Module, call: ast.Call) -> bool:
    """Return whether ``call`` in ``file`` (parsed as ``tree``) calls a function or class of the core of ``home``.

    A name is looked up in the lexical scopes around the call, then in the module; a parameter, a local variable, a
    nested definition or a ``global`` rebinding of the name is not the imported function. Imports lead to the
    definition (re-exports followed), and only module-level definitions of the module count. ``module.name`` resolves
    through the module the alias names; a method of a value or a computed callee is not resolved.
    """
    callee = call.func
    if isinstance(callee, ast.Name):
        origins = _scoped_origins(root, file, tree, call, callee.id)
    elif isinstance(callee, ast.Attribute) and isinstance(callee.value, ast.Name):
        origins = frozenset(
            origin
            for module, qualified in _scoped_origins(root, file, tree, call, callee.value.id)
            for origin in (_origins(root, module, callee.attr) if module and not qualified else (UNRESOLVED,))
        )
    else:
        return False
    return all(function and home.in_core(defined_in, function) for defined_in, function in origins)


def _place_exists(place: Place, source_root: Path) -> bool:
    path = source_root / place.file
    if place.file.endswith("/"):
        return path.is_dir()
    return path.is_file() and (place.function is None or place.function in _function_names(path))


def _names_planned(place: Place, planned: Iterable[Place]) -> bool:
    """Return whether a signature path is a planned place, inside one, or the file or directory that holds one."""
    return any(
        item.covers(place.file, place.function)
        if place.function
        else (item.in_file(place.file) or place.in_file(item.file))
        for item in planned
    )


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
            Problem(f"home {home.id}: planned place {place} exists now; move it to core or adapters")
            for place in home.planned
            if _place_exists(place, root)
        )
    planned = [place for home in config.homes.values() for place in home.planned]
    problems.extend(
        Problem(f"signature {signature.id}: {place} does not exist")
        for signature in config.signatures.values()
        for place in signature.paths
        if not _names_planned(place, planned) and not _place_exists(place, root)
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
