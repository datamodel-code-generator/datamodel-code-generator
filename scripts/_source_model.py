"""One reading of Python source for every repository check that reads Python.

A check asks five questions of a module, and this module is the only place that answers them:

- **bindings**: which scopes exist (the language's scopes, each with the code object it compiles to), which names each
  binds, where, by which form and under which guard, what each assignment target takes (`SourceModule.assignment`), a
  class's members and a method's receiver;
- **references**: what each name occurrence reads (by the language's scoping rules), the dotted origin of a name or an
  attribute chain through imports and simple aliases, how a read is used, the annotations and the type positions in
  them, what a name read in a scope resolves to (`SourceModule.lookup`, for the names a string reads: `string_type`,
  `name_reads`), literal tables (`SourceModule.constant`), the argument a call passes for a parameter (`argument`), the
  origins of a statement's decorators (`SourceModule.decorator_origins`) and the parts of a module only type checkers
  read (`SourceModule.typing_only`);
- **imports**: each import with its absolute target, scope and guard (``TYPE_CHECKING``, version, platform,
  ``ImportError`` fallback, ...), and across modules (`SourceTree`) the definitions names reach and the import graph;
- **suppressions**: the suppression comments (``noqa``, ``ruff: ignore/disable``, ``type: ignore`` and its siblings,
  ``heki: ignore``, coverage pragmas) and the lines each one covers;
- **extents**: statement extents, the clauses of compound statements, the statement that holds a line, the lines with
  code, the innermost scope region that holds a position, the header and the nodes that hold one, the `Segment` (a
  statement or a part of one that may be skipped) that holds one, and folded source text.

Answers come from `ast` by the language reference's rules, so they are the same on every supported Python. Nothing of
`symtable` reaches a caller. The only per-version answer is `SourceModule.compiled`, the code objects of the running
interpreter.

What it refuses to answer: value flow (which object a local holds, container aliases), which binding reaches a read in
flow order (`SourceModule.resolve` lists every binding of the resolved scope), attribute types, method resolution and
call graphs, dynamic namespaces (``globals()[k] = ...``, ``setattr(sys.modules[__name__], ...)``, ``vars()``,
``exec``, star imports, ``import_module(<variable>)``: listed in `SourceModule.dynamic_sites`, and the names they may
bind resolve as ``star`` or ``unbound``, never as a guess), run-time facts (both branches of a guard are bindings with
their guards), strings that hold dotted paths, and the run-time line mapping of code objects. A question beyond the
five is a design question, not an extension added in passing.

The compiler check (`SourceModule.verify`, on in ``tests/test_source_model.py`` through `VERIFY_ENV`) compares the
model with `symtable` on the running interpreter, both ways: the scope tree (kinds and lines), the names each scope
binds, ``global`` and ``nonlocal`` declarations, the reads the model records against the reads the compiler counts
and back, per name and symbol table (a second read of a name in the same table stands behind a missing one), and the
kind of each read (local, enclosing, global or builtin). A module the compiler rejects though `ast` reads it (a late
``from __future__`` import) is a disagreement too.
The forms in ``tests/data/source_forms/`` alone stand behind the other answers: guards, origins, uses (of names and of
any expression), the binding of a node, what an assignment target takes, class members, receivers, annotations, type
positions and expressions, the names a string reads, constants, decorator origins, imports, suppressions and extents.

The compiler differs by version, and the check normalizes the differences in one place (`SourceModule._compare`): on
3.11 list, set and dict comprehensions have tables of their own, from 3.12 they are inlined into the enclosing table
(PEP 709) unless the code that evaluates them sees a class namespace, and their reads there are compared by presence;
from 3.14, annotations without ``from __future__ import annotations`` live in ``__annotate__`` tables, whose reads and
inner scopes are compared there by presence (a comprehension inlined into a ``lambda`` or generator expression inside
an annotation lives in that scope's own table); with the future import, annotations are never compiled and neither their
reads nor the scopes inside them are compared; reads in function-local annotations are compared only from the
compiler's side; type-parameter tables ("type parameter"/"TypeVar bound" on 3.12, "type parameters"/"type variable"
later) and ``lambda``/``<lambda>`` (3.15) are matched by kind; child tables are paired by line, kind and the names
they bind, so the compiler's visiting order (a dict comprehension's value before its key) does not matter; an
augmented assignment's read is not counted by the compiler and is compared by presence; names are compared mangled;
implicit names (``.0``, ``__class__``, ``__classdict__``, ``.type_params``, ``.format``, ...) are ignored. A lazy
import (3.15) is recorded on its `Import`.

A disagreement, or a form the model has no rule for, raises `SourceModelError` naming the file, the line and the form.
The fix is a rule here plus a form under ``tests/data/source_forms/<question>/`` in the same pull request; the
repository's gate judges a pull request with the base copy of this module, without the compiler check.
"""

from __future__ import annotations

import ast
import dis
import itertools
import operator
import os
import re
import symtable
import sys
import tokenize
import types
import unicodedata
from bisect import bisect_left
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from functools import cached_property
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn

if TYPE_CHECKING:
    from collections.abc import Callable, Container, Generator, Iterable, Iterator

VERIFY_ENV = "DATAMODEL_CODE_GENERATOR_CHECK_SOURCE_MODEL"
"""Set to ``1`` to compare every model with the compiler (``tests/test_source_model.py`` sets it)."""

QUESTIONS = ("bindings", "references", "imports", "suppressions", "extents")
"""The five questions, in the order answers are rendered."""

BUILTINS = frozenset({
    "ArithmeticError",
    "AssertionError",
    "AttributeError",
    "BaseException",
    "BaseExceptionGroup",
    "BlockingIOError",
    "BrokenPipeError",
    "BufferError",
    "BytesWarning",
    "ChildProcessError",
    "ConnectionAbortedError",
    "ConnectionError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "DeprecationWarning",
    "EOFError",
    "Ellipsis",
    "EncodingWarning",
    "EnvironmentError",
    "Exception",
    "ExceptionGroup",
    "False",
    "FileExistsError",
    "FileNotFoundError",
    "FloatingPointError",
    "FutureWarning",
    "GeneratorExit",
    "IOError",
    "ImportError",
    "ImportWarning",
    "IndentationError",
    "IndexError",
    "InterruptedError",
    "IsADirectoryError",
    "KeyError",
    "KeyboardInterrupt",
    "LookupError",
    "MemoryError",
    "ModuleNotFoundError",
    "NameError",
    "None",
    "NotADirectoryError",
    "NotImplemented",
    "NotImplementedError",
    "OSError",
    "OverflowError",
    "PendingDeprecationWarning",
    "PermissionError",
    "ProcessLookupError",
    "RecursionError",
    "ReferenceError",
    "ResourceWarning",
    "RuntimeError",
    "RuntimeWarning",
    "StopAsyncIteration",
    "StopIteration",
    "SyntaxError",
    "SyntaxWarning",
    "SystemError",
    "SystemExit",
    "TabError",
    "TimeoutError",
    "True",
    "TypeError",
    "UnboundLocalError",
    "UnicodeDecodeError",
    "UnicodeEncodeError",
    "UnicodeError",
    "UnicodeTranslateError",
    "UnicodeWarning",
    "UserWarning",
    "ValueError",
    "Warning",
    "ZeroDivisionError",
    "__build_class__",
    "__debug__",
    "__doc__",
    "__import__",
    "__loader__",
    "__name__",
    "__package__",
    "__spec__",
    "abs",
    "aiter",
    "all",
    "anext",
    "any",
    "ascii",
    "bin",
    "bool",
    "breakpoint",
    "bytearray",
    "bytes",
    "callable",
    "chr",
    "classmethod",
    "compile",
    "complex",
    "copyright",
    "credits",
    "delattr",
    "dict",
    "dir",
    "divmod",
    "enumerate",
    "eval",
    "exec",
    "exit",
    "filter",
    "float",
    "format",
    "frozenset",
    "getattr",
    "globals",
    "hasattr",
    "hash",
    "help",
    "hex",
    "id",
    "input",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "license",
    "list",
    "locals",
    "map",
    "max",
    "memoryview",
    "min",
    "next",
    "object",
    "oct",
    "open",
    "ord",
    "pow",
    "print",
    "property",
    "quit",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "setattr",
    "slice",
    "sorted",
    "staticmethod",
    "str",
    "sum",
    "super",
    "tuple",
    "type",
    "vars",
    "zip",
})
"""The names of `builtins` on every platform from the oldest supported Python (3.11) on."""

CONDITIONAL_BUILTINS: dict[str, tuple[str | None, tuple[int, int]]] = {
    "ImportCycleError": (None, (3, 15)),
    "PythonFinalizationError": (None, (3, 13)),
    "WindowsError": ("win32", (3, 11)),
    "_IncompleteInputError": (None, (3, 13)),
    "__lazy_import__": (None, (3, 15)),
    "frozendict": (None, (3, 15)),
    "sentinel": (None, (3, 15)),
}
"""The names of `builtins` that exist only on one platform or from a version on: the ``sys.platform`` each needs
(``None``: every platform) and the first version that has it.

A read resolves to these as to `BUILTINS` on every platform and version, since code under a platform or version guard
reads them, so an answer depends on the source text alone."""

_BUILTIN_NAMES = BUILTINS.union(CONDITIONAL_BUILTINS)

MODULE_ATTRIBUTES = frozenset({
    "__annotations__",
    "__builtins__",
    "__cached__",
    "__doc__",
    "__file__",
    "__loader__",
    "__name__",
    "__package__",
    "__path__",
    "__spec__",
})
"""The names the import system binds in every module's namespace."""

ScopeKind = Literal["module", "class", "function", "lambda", "comprehension", "type-params", "type-alias", "type-bound"]
BindingKind = Literal[
    "param",
    "assign",
    "augassign",
    "annassign",
    "annotation-only",
    "for",
    "with",
    "except",
    "import",
    "import-from",
    "def",
    "class",
    "del",
    "match",
    "walrus",
    "comprehension",
    "type-param",
    "type-alias",
]
Region = tuple[tuple[int, int], tuple[int, int]]
Positioned = ast.expr | ast.stmt | ast.pattern | ast.arg | ast.keyword | ast.excepthandler | ast.alias
"""A node with a position of its own that `_Extents.nodes_at` and `_Extents.segment` read."""

_COMPREHENSION_NAMES = {ast.ListComp: "<listcomp>", ast.SetComp: "<setcomp>", ast.DictComp: "<dictcomp>"}
_INLINED_KINDS = (ast.ListComp, ast.SetComp, ast.DictComp)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_ANNOTATION_SCOPES = frozenset({"type-params", "type-alias", "type-bound"})
_NOT_RUN = frozenset({"deferred", "never"})
_DEFERRED_SCOPES = frozenset({"function", "lambda", "type-alias", "type-bound"})
"""The scopes whose code runs later than the statement that defines them."""
_LOAD_CONST = dis.opmap["LOAD_CONST"]
_NEVER = ((99, 0), (99, 0))
_TYPE_CHECKING = frozenset({"typing.TYPE_CHECKING", "typing_extensions.TYPE_CHECKING"})
_VERSIONS = ("sys.version_info", "sys.hexversion")
_PLATFORMS = frozenset({"sys.platform", "os.name", "platform.system"})
_IMPORT_ERRORS = frozenset({
    "builtins.ImportError",
    "builtins.ModuleNotFoundError",
    "builtins.Exception",
    "builtins.BaseException",
})
_LITERAL_HEADS = frozenset({"typing.Literal", "typing_extensions.Literal"})
_ANNOTATED_HEADS = frozenset({"typing.Annotated", "typing_extensions.Annotated"})
_CASTS = frozenset({"typing.cast", "typing_extensions.cast"})
_OVERLOADS = frozenset({"typing.overload", "typing_extensions.overload"})
_TYPING_DECORATORS = frozenset({
    "typing.override",
    "typing_extensions.override",
    "typing.final",
    "typing_extensions.final",
})
_TYPE_VARS = frozenset({"typing.TypeVar", "typing_extensions.TypeVar"})
_TYPE_ALIASES = frozenset({"typing.TypeAlias", "typing_extensions.TypeAlias"})
_NAMESPACE_CALLS = {
    "builtins.globals": "globals",
    "builtins.vars": "vars",
    "builtins.locals": "locals",
    "builtins.exec": "exec",
}
_IMPORT_CALLS = frozenset({"importlib.import_module", "builtins.__import__"})
_LITERAL_CALLS: dict[str, Callable[..., object]] = {
    "builtins.frozenset": frozenset,
    "builtins.set": set,
    "builtins.tuple": tuple,
    "builtins.list": list,
    "builtins.dict": dict,
    "types.MappingProxyType": types.MappingProxyType,
}
_MUTATORS = frozenset({
    "add",
    "append",
    "clear",
    "discard",
    "extend",
    "insert",
    "pop",
    "popitem",
    "remove",
    "reverse",
    "setdefault",
    "sort",
    "update",
})
_WRITES = frozenset({"subscript-store", "subscript-del", "attribute-store", "attribute-del", "augassign"})
_NOT_CODE = frozenset({
    tokenize.COMMENT,
    tokenize.NL,
    tokenize.NEWLINE,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.ENDMARKER,
    tokenize.ENCODING,
})
_LAYOUT = _NOT_CODE - {tokenize.COMMENT}
_STRING_STARTS = frozenset(
    getattr(tokenize, name) for name in ("FSTRING_START", "TSTRING_START") if hasattr(tokenize, name)
)
_STRING_ENDS = frozenset(getattr(tokenize, name) for name in ("FSTRING_END", "TSTRING_END") if hasattr(tokenize, name))
_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_CODES = r"(?::\s*(?P<codes>[A-Z0-9]+(?:[\s,]+[A-Z0-9]+)*))?"
_NOQA = re.compile(rf"#\s*noqa(?![\w-]){_CODES}", re.IGNORECASE)
_FILE_NOQA = re.compile(rf"#\s*(?:ruff|flake8):\s*noqa(?![\w-]){_CODES}")
_RUFF_RULES = re.compile(r"#\s*ruff:\s*(?P<action>ignore|disable|enable)\[(?P<rules>[^\]]*)\]")
_TYPE_IGNORE = re.compile(r"\b(?P<tool>ty|pyright|type):\s*ignore\b(?:\[(?P<codes>[^\]]*)\])?")
_HEKI_IGNORE = re.compile(r"\bheki:\s*ignore\b(?!\[[^\]]*$)(?:\[(?P<codes>[^\]]*)\])?", re.IGNORECASE)
_PRAGMA = re.compile(r"\bpragma:\s*(no\s+cover|no\s+branch|\S+(?:\s+no)?\s+cover)\b")
_PRAGMA_FORMS = {"no cover": "no-cover", "no branch": "no-branch"}
_LOOP_KEYWORDS = {ast.For: "for", ast.AsyncFor: "async for", ast.While: "while", ast.With: "with"}
_TABLE_KINDS = {
    "module": "module",
    "class": "class",
    "function": "function",
    "annotation": "annotation",
    "type alias": "type-alias",
    "type parameter": "type-params",
    "type parameters": "type-params",
    "TypeVar bound": "type-bound",
    "type variable": "type-bound",
}
_IMPLICIT = frozenset({
    "__annotate__",
    "__class__",
    "__classcell__",
    "__classdict__",
    "__classdictcell__",
    "__conditional_annotations__",
    "__firstlineno__",
    "__module__",
    "__qualname__",
    "__static_attributes__",
    "__type_params__",
})
_UNHASHABLE = "an unhashable key or member"
_MALFORMED = "an entry that is not a key and a value"
_ASKED: list[set[str]] = []


class SourceModelError(Exception):
    """The model and the compiler disagree, or the source holds a form the model has no rule for."""


class CompilerRejectedError(SourceModelError):
    """The compiler rejects a module that ``ast`` reads (for example ``nonlocal x`` without a binding)."""


class _NotLiteralError(Exception):
    """An expression `SourceModule.constant` cannot evaluate from source."""


@contextmanager
def questions_asked() -> Generator[set[str]]:
    """Collect the questions (`QUESTIONS`) that any model is asked while the block runs."""
    asked: set[str] = set()
    _ASKED.append(asked)
    try:
        yield asked
    finally:
        _ASKED[:] = [item for item in _ASKED if item is not asked]


def _ask(question: str) -> None:
    for asked in _ASKED:
        asked.add(question)


@dataclass(frozen=True, slots=True)
class Unreadable:
    """An answer the model cannot give from source: why, and the lines that make it so."""

    reason: str
    lines: tuple[int, ...] = ()

    def __str__(self) -> str:
        """Render the reason with its lines."""
        where = f" (lines {', '.join(map(str, self.lines))})" if self.lines else ""
        return f"unreadable: {self.reason}{where}"


@dataclass(eq=False, repr=False, slots=True)
class Scope:
    """A scope of the language: module, class, function, lambda, comprehension or a 3.12+ annotation scope.

    ``qualname`` follows ``__qualname__`` (list, set and dict comprehensions and type-parameter scopes add no part, as
    on 3.12 and later); ``ordinal`` is the index among the scopes of the same parent with the same qualname, in the
    model's visiting order (a comprehension's first iterable before the comprehension), so ``(qualname, ordinal)``
    pairs scopes across two versions of a file. ``code_name`` and ``first_line`` match ``co_name`` and
    ``co_firstlineno``, ``line`` is the line of the statement or expression that opens the scope; ``annotation`` is the
    mode of the annotation the scope sits in (empty outside annotations). The keys of ``bindings`` and the declared
    names are the names the compiler stores (mangled). The model builds scopes; callers only read them.
    """

    kind: ScopeKind
    node: ast.AST
    parent: Scope | None
    qualname: str
    ordinal: int
    code_name: str
    first_line: int
    line: int
    annotation: str = ""
    bindings: dict[str, list[Binding]] = field(default_factory=dict)
    declared_global: set[str] = field(default_factory=set)
    declared_nonlocal: set[str] = field(default_factory=set)
    children: list[Scope] = field(default_factory=list)
    star_import: bool = False

    @property
    def code_object(self) -> str:
        """Whether the scope compiles to a code object of its own: ``yes``, ``no``, ``3.11 only`` or ``3.11, 3.14+``.

        List, set and dict comprehensions are inlined from 3.12 (PEP 709), except where the code that evaluates them
        sees a class namespace (an annotation scope inside a class; from 3.14 a class-level annotation). A scope inside
        an annotation that is never evaluated (deferred by the future import, or a function-local one) is never
        compiled.
        """
        if self.annotation in _NOT_RUN:
            return "no"
        return {((3, 12), (99, 0)): "3.11 only", ((3, 12), (3, 14)): "3.11, 3.14+"}.get(self._inlined_from(), "yes")

    def compiles_on(self, version: tuple[int, int]) -> bool:
        """Whether the scope has a code object of its own on a Python version."""
        return self.code_object != "no" and not self.inlined_on(version)

    def inlined_on(self, version: tuple[int, int]) -> bool:
        """Whether a comprehension's names live in the enclosing scope's symbol table on a Python version."""
        start, end = self._inlined_from()
        return start <= version < end

    def _inlined_from(self) -> tuple[tuple[int, int], tuple[int, int]]:
        """Return the versions ``[from, until)`` on which a comprehension is inlined into its enclosing code."""
        parent = self.parent
        if not isinstance(self.node, _INLINED_KINDS) or parent is None or parent.sees_class:
            return _NEVER
        return ((3, 12), (3, 14)) if self.annotation == "evaluated" and parent.kind == "class" else ((3, 12), (99, 0))

    @property
    def runs_in(self) -> str:
        """Where the scope's code runs: ``module`` or ``class`` as the module is imported, else ``function``.

        A list, set or dict comprehension and a type-parameter scope run where they stand; a ``def``, a ``lambda``, a
        ``type`` alias or bound and a generator expression's own scope (its element, conditions and later ``for``
        clauses; its first iterable is evaluated in the enclosing scope) run when they are called, read or iterated,
        and so does everything inside them, a class body included.
        """
        level = ""
        probe: Scope | None = self
        while probe is not None:
            if probe.kind in _DEFERRED_SCOPES or isinstance(probe.node, ast.GeneratorExp):
                return "function"
            level = level or (probe.kind if probe.kind in {"module", "class"} else "")
            probe = probe.parent
        return level or "module"

    @property
    def sees_class(self) -> bool:
        """Whether code in this annotation scope reads the namespace of the class it sits in (PEP 695)."""
        probe: Scope | None = self
        while probe is not None and probe.kind in _ANNOTATION_SCOPES:
            probe = probe.parent
        return self.kind in _ANNOTATION_SCOPES and probe is not None and probe.kind == "class"


class _Test:
    """The classification of one test (an ``if`` or a ``try``), set after names are resolved."""

    __slots__ = ("kind", "negated", "operands")

    def __init__(self, kind: str = "") -> None:
        self.kind = kind
        self.negated = False
        self.operands: tuple[str, ...] = ()


@dataclass(frozen=True, eq=False, slots=True)
class Condition:
    """One condition on the way to a statement: its kind, the branch taken, and the node that holds it.

    The node is the ``if``, ``try`` or loop statement, a conditional expression, or (``and``/``or``) the operand
    that decides whether the next ones run.

    Kinds: ``type-checking``, ``version``, ``platform``, ``if`` (any other test; ``operands`` lists the kinds of the
    operands of a mixed ``and``/``or``), ``import-error``, ``try``, ``loop``, ``case``, ``function``. Tests are
    classified by the origin of what they read, never by spelling. For ``type-checking`` the branch is semantic:
    ``body`` runs only for type checkers, ``else`` at run time (``if not TYPE_CHECKING:`` has its body on ``else``).
    """

    test: _Test = field(repr=False)
    syntactic_branch: str
    node: ast.AST = field(repr=False)
    line: int

    @property
    def kind(self) -> str:
        """The kind of the condition."""
        return self.test.kind

    @property
    def operands(self) -> tuple[str, ...]:
        """The kinds of the operands of a boolean test."""
        return self.test.operands

    @property
    def branch(self) -> str:
        """The branch: ``body``, ``else``, ``except`` or ``finally``."""
        swapped = {"body": "else", "else": "body"}
        if self.test.negated and self.test.kind == "type-checking":
            return swapped[self.syntactic_branch]
        return self.syntactic_branch

    def __str__(self) -> str:
        """Render as ``kind[operands]:branch@line``."""
        operands = f"[{','.join(self.operands)}]" if self.kind == "if" and self.operands else ""
        return f"{self.kind}{operands}:{self.branch}@{self.line}"


Guard = tuple[Condition, ...]


@dataclass(frozen=True, eq=False, slots=True)
class Binding:
    """A site that binds a name: ``scope`` is where the name lives (after ``global``/``nonlocal``).

    ``name`` is the spelling, ``mangled`` the name the compiler stores. ``origin`` is the dotted path an import binds;
    ``value`` the assigned expression of a single-target assignment; ``reexport_form`` the typing spec's re-export form
    (``import X as X``, ``from M import X as X``, ``from . import X``).
    """

    scope: Scope
    name: str
    mangled: str
    kind: BindingKind
    node: ast.AST = field(repr=False)
    line: int
    guard: Guard
    origin: str | None = None
    value: ast.expr | None = field(default=None, repr=False)
    reexport_form: str | None = None


@dataclass(frozen=True, eq=False, slots=True)
class Reference:
    """A name occurrence and the scope it is evaluated in.

    ``annotation`` is empty for a run-time read, else ``evaluated``, ``deferred`` (future import), ``never`` (a
    function-local annotation) or ``string`` (a name parsed from a string in a type position, at the string's position).
    """

    node: ast.Name = field(repr=False)
    name: str
    mangled: str
    scope: Scope
    context: str
    annotation: str
    guard: Guard
    line: int
    column: int


_TypeName = tuple[ast.expr, Reference]
"""A name or attribute chain in a type position and the reference of its head name."""


@dataclass(frozen=True, slots=True)
class Resolution:
    """What a name occurrence reads: the kind, the scope and its binding sites.

    Kinds: ``local``, ``enclosing``, ``global``, ``builtin``, ``unbound``, ``star``, ``class-cell``. A ``global`` with
    no bindings is a name the import system binds (``__name__``). ``fallback`` is what a class body reads when its own
    namespace lacks the name at run time (the module, then builtins), and what an annotation scope inside a class reads
    when the class lacks it.
    """

    kind: str
    scope: Scope | None
    bindings: tuple[Binding, ...]
    fallback: Resolution | None = None


@dataclass(frozen=True, slots=True)
class Use:
    """How a load is used.

    Kinds: ``call`` (the callee), ``call:<method>`` (the receiver of a method call), ``subscript-store``,
    ``subscript-del``, ``attribute-store``, ``attribute-del`` (the receiver of such a target), ``augassign`` (the
    target), ``iterate`` (the iterable of a ``for`` or of a comprehension's ``for``), ``unpack`` (under ``*`` or
    ``**``), ``argument`` (an argument of a call) and ``read`` (any other load, such as an operand or the receiver of
    an attribute that is read).
    """

    reference: Reference
    kind: str


@dataclass(frozen=True, eq=False, slots=True)
class Import:
    """An import: ``kind`` is ``import``, ``from``, ``star`` or ``dynamic`` (literal ``import_module``/``__import__``).

    ``source`` is the module the statement loads, ``target`` the dotted name it binds (``source.name`` for ``from``);
    both are `Unreadable` for a relative import that leaves the top-level package.
    """

    node: ast.stmt | ast.expr = field(repr=False)
    alias: ast.alias | None = field(repr=False)
    kind: str
    bound: str | None
    source: str | Unreadable
    target: str | Unreadable
    scope: Scope
    guard: Guard
    level: int
    lazy: bool

    @property
    def unconditional(self) -> bool:
        """Whether the import runs whenever its module is imported: module level, no condition on the way."""
        return self.scope.kind == "module" and not self.guard

    @property
    def typing_only(self) -> bool:
        """Whether only type checkers run the import (it sits in the body of a ``TYPE_CHECKING`` test)."""
        return any(condition.kind == "type-checking" and condition.branch == "body" for condition in self.guard)


@dataclass(frozen=True, eq=False, slots=True)
class DynamicSite:
    """A statement that may bind names the source does not spell.

    Kinds: ``globals``, ``vars``, ``locals``, ``exec``, ``setattr-module``, ``star-import``, ``import-module`` (a
    non-literal module name).
    """

    kind: str
    node: ast.AST = field(repr=False)
    line: int
    scope: Scope
    guard: Guard


@dataclass(frozen=True, slots=True)
class Suppression:
    """A suppression comment: the tool, the form, the codes it names (None: every code) and the lines it covers."""

    tool: str
    form: str
    codes: tuple[str, ...] | None
    line: int
    lines: frozenset[int]


@dataclass(frozen=True, slots=True)
class Clause:
    """A clause of a compound statement: its keyword, header lines (decorators included) and suite lines."""

    keyword: str
    header: range
    suite: range


@dataclass(frozen=True, slots=True)
class SourceToken:
    """A code token with the scope region that owns it and its role there (``header`` or ``body``)."""

    token: tokenize.TokenInfo
    owner: Scope
    role: str


@dataclass(frozen=True, eq=False, slots=True)
class Segment:
    """A part of the code that runs as a unit when the code around it runs: the innermost one at a position.

    Kinds: ``statement``; ``arm`` (the ``body`` or ``orelse`` of a conditional expression); ``operand`` (an operand of
    ``and``/``or`` after the first); ``element`` (a comprehension's element, a dict comprehension's key and value as
    one); ``target`` (a ``for`` clause's target with its first condition: two spans, since 3.13+ fuses the store of
    the target with the first load of the condition); ``condition`` (a later condition of a ``for`` clause);
    ``iterable`` (the iterable of a ``for`` clause after the first); ``case`` (a ``case`` of a ``match`` after the
    first, from its keyword to the end of its pattern and guard: it runs only where the cases above it fail);
    ``guard`` (a case's guard: it runs only where the pattern matched). ``spans`` are ``(start, end)`` positions
    (columns in characters, ends exclusive); ``node`` is the statement or the expression the segment starts with, the
    ``match_case`` of a ``case``.
    """

    kind: str
    spans: tuple[Region, ...]
    node: ast.AST = field(repr=False)


@dataclass(frozen=True, slots=True)
class Definition:
    """Where a name leads: the module and name of the statement that defines it, its kind, and the hops on the way.

    ``path`` holds one ``(module, guard)`` per binding followed. Kinds are binding kinds (``def``, ``class``,
    ``assign``, ...) and ``module``, ``attribute`` (of a definition), ``outside`` (a module outside the tree),
    ``unbound``, ``unreadable`` and ``cycle``.
    """

    module: str
    name: str
    line: int
    kind: str
    path: tuple[tuple[str, Guard], ...]


@dataclass(frozen=True, slots=True)
class Edge:
    """An import edge between two modules of a tree.

    ``via`` is ``import``, ``from``, ``submodule``, ``parent`` or ``dynamic-literal``; ``level`` is where the import
    runs (`Scope.runs_in`): ``module`` or ``class`` as the module is imported, ``function`` when a function runs.
    """

    importer: str
    imported: str
    line: int
    guard: Guard
    level: str
    via: str


@dataclass(frozen=True, slots=True)
class Assignment:
    """What a target of an assignment, a ``for``, a comprehension's ``for``, a ``with`` item or a walrus takes.

    ``value`` is the expression the statement evaluates for it: the assigned value, the iterable, the context
    expression. ``element`` is the part of it the target takes: where the target and the value are tuples or lists of
    one length, each item takes the item at its position (at every level of unpacking, a starred item too), else the
    whole value. ``position`` is where the target sits in the unpacking target it belongs to: the index at each level
    (``c`` of ``a, (b, c)`` is ``(1, 1)``, a starred item its own index, an item after it its index from the end:
    ``c`` of ``a, *b, c`` is ``(-1,)``), empty for the whole target. ``kind`` is
    ``assign``, ``annassign``, ``for``, ``comprehension``, ``with`` or ``walrus``. An
    augmented assignment, a deletion and an annotation without a value assign nothing.
    """

    kind: str
    value: ast.expr = field(repr=False)
    element: ast.expr = field(repr=False)
    position: tuple[int, ...] = ()


@dataclass(frozen=True, eq=False, slots=True)
class Annotation:
    """An annotation the source writes, the scope it is evaluated in, and when it is evaluated.

    ``mode`` is ``evaluated``, ``deferred`` (future import) or ``never`` (a function-local variable's). ``target`` is
    the annotated assignment target or parameter (None for a return annotation), ``value`` the value an annotated
    assignment assigns.
    """

    node: ast.expr = field(repr=False)
    scope: Scope
    mode: str
    line: int
    target: ast.AST | None = field(default=None, repr=False)
    value: ast.expr | None = field(default=None, repr=False)


UNKNOWN = ast.Constant(value=Ellipsis)
"""What `argument` returns where ``*args`` or ``**kwargs`` may supply the parameter."""


def argument(call: ast.Call, position: int | None, keyword: str | None) -> ast.expr | None:
    """Return the expression a call passes for a parameter (by position and/or keyword), None, or `UNKNOWN`."""
    _ask("references")
    for item in call.keywords:
        if keyword is not None and item.arg == keyword:
            return item.value
    for index, item in enumerate(call.args if position is not None else ()):
        if isinstance(item, ast.Starred):
            return UNKNOWN
        if index == position:
            return item
    return UNKNOWN if any(item.arg is None for item in call.keywords) else None


def bases(scope: Scope) -> list[ast.expr]:
    """Return the bases a class scope lists, each as the name or attribute chain `SourceModule.origin` reads.

    A subscript gives its value (``Protocol[T]`` gives ``Protocol``) and a keyword (``metaclass=``) is no base; any
    other expression stays as written. A scope that is no class has none.
    """
    _ask("bindings")
    node = scope.node
    listed = node.bases if scope.kind == "class" and isinstance(node, ast.ClassDef) else []
    return [base.value if isinstance(base, ast.Subscript) else base for base in listed]


def annotation(binding: Binding) -> ast.expr | None:
    """Return the annotation of the parameter a binding makes, None for any other binding or a parameter without one."""
    _ask("bindings")
    return binding.node.annotation if isinstance(binding.node, ast.arg) else None


def nested_name(scope: Scope) -> str:
    """Return the names of the classes and functions around a scope and its own, joined by dots ("" for the module).

    The definitions as the source nests them: ``__qualname__`` without ``<locals>``, lambdas, comprehensions and
    annotation scopes, and without the restart a ``global`` declaration gives the qualname of a nested definition.
    """
    _ask("bindings")
    names = []
    probe: Scope | None = scope
    while probe is not None:
        names += [probe.code_name] if probe.kind in {"class", "function"} else []
        probe = probe.parent
    return ".".join(reversed(names))


def _verifying(*, verify: bool | None) -> bool:
    return os.environ.get(VERIFY_ENV) == "1" if verify is None else verify


class _SourceText:
    """The text of a module: lines, tokens, and the conversion of `ast` columns (UTF-8 bytes) to characters."""

    text: str
    path: str
    lines: list[str]
    tree: ast.Module
    _scopes: list[Scope]
    _module_scope: Scope

    @cached_property
    def _tokens(self) -> list[tokenize.TokenInfo]:
        return list(tokenize.generate_tokens(StringIO(self.text).readline))

    @cached_property
    def _parents(self) -> dict[ast.AST, ast.AST]:
        return {child: node for node in ast.walk(self.tree) for child in ast.iter_child_nodes(node)}

    @cached_property
    def _starts(self) -> list[tuple[int, int]]:
        return [token.start for token in self._tokens]

    def _column(self, line: int, offset: int) -> int:
        """Convert an `ast` column (UTF-8 bytes) to characters, the unit of `tokenize` and of positions here."""
        text = self.lines[line - 1]
        return offset if text.isascii() else len(text.encode()[:offset].decode(errors="replace"))

    def _position(self, node: Positioned) -> tuple[int, int]:
        return node.lineno, self._column(node.lineno, node.col_offset)

    def _end(self, node: Positioned) -> tuple[int, int]:
        line = node.end_lineno or node.lineno
        return line, self._column(line, node.end_col_offset or 0)

    def _slice(self, start: tuple[int, int], end: tuple[int, int]) -> str:
        if start[0] == end[0]:
            return self.lines[start[0] - 1][start[1] : end[1]]
        middle = self.lines[start[0] : end[0] - 1]
        return "\n".join([self.lines[start[0] - 1][start[1] :], *middle, self.lines[end[0] - 1][: end[1]]])

    def _code_token_before(self, position: tuple[int, int]) -> tokenize.TokenInfo:
        index = bisect_left(self._starts, position) - 1
        while self._tokens[index].type in _NOT_CODE:
            index -= 1
        return self._tokens[index]

    def _name_before(self, keyword: str, position: tuple[int, int]) -> tuple[int, int]:
        """Return the start of the nearest ``keyword`` name token before a position (a clause keyword)."""
        index = bisect_left(self._starts, position) - 1
        while self._tokens[index].type != tokenize.NAME or self._tokens[index].string != keyword:
            index -= 1
        return self._tokens[index].start


class _Extents(_SourceText):
    """Statement extents, clauses, owners of positions and folded source text."""

    @staticmethod
    def extent(node: ast.stmt) -> range:
        """Return the lines of a statement, from its first decorator to its last line."""
        _ask("extents")
        return range(_first_line(node), (node.end_lineno or node.lineno) + 1)

    def clauses(self, node: ast.stmt) -> list[Clause]:
        """Return the clauses of a compound statement (none for a simple one); ``else``/``finally`` found by token."""
        _ask("extents")
        return self._clauses(node)

    def statement_at(self, line: int) -> ast.stmt | None:
        """Return the innermost statement that holds a line: any line of a simple statement, a compound one's header."""
        _ask("extents")
        return self._line_statements.get(line)

    @property
    def code_lines(self) -> frozenset[int]:
        """The lines that hold a code token (not blank, not comment-only; every line of a multi-line string)."""
        _ask("extents")
        return self._code_lines

    def owner(self, line: int, column: int) -> Scope:
        """Return the innermost scope region holding a position (column in characters); a def's starts at its ``@``."""
        _ask("extents")
        best, best_key = self._module_scope, ((0, 0), (0, 0))
        for scope, (start, end) in self._regions:
            if start <= (line, column) < end and (key := (start, (-end[0], -end[1]))) > best_key:
                best, best_key = scope, key
        return best

    def header(self, scope: Scope) -> range:
        """Return the header lines of a ``def``, ``class`` or ``lambda`` scope (decorators to the ``:``), else none."""
        _ask("extents")
        body = self._body_start(scope)
        return range(scope.first_line, self._code_token_before(body).start[0] + 1) if body is not None else range(0)

    def tokens(self, lines: Iterable[int] | None = None) -> list[SourceToken]:
        """Return the code tokens (on the given lines) with the scope region that owns each and its role there."""
        _ask("extents")
        wanted = None if lines is None else set(lines)
        found = []
        for token in self._tokens:
            if token.type in _NOT_CODE or (wanted is not None and token.start[0] not in wanted):
                continue
            owner = self.owner(*token.start)
            body = self._body_start(owner)
            found.append(SourceToken(token, owner, "header" if body is not None and token.start < body else "body"))
        return found

    def source(self, node: ast.expr | ast.stmt) -> str:
        """Return a node's source text: its tokens with every run of whitespace, line breaks and comments one space."""
        _ask("extents")
        end = self._end(node)
        index = bisect_left(self._starts, self._position(node))
        parts: list[str] = []
        previous: tuple[int, int] | None = None
        first = self._position(node)
        depth = 0
        while index < len(self._tokens) and self._tokens[index].start < end:
            token = self._tokens[index]
            index += 1
            if token.type in _NOT_CODE and depth == 0:
                continue
            if depth == 0:
                parts.append(" " if previous is not None and previous != token.start else "")
                first = token.start
            depth += (token.type in _STRING_STARTS) - (token.type in _STRING_ENDS)
            if depth == 0:
                parts.append(self._slice(first, token.end))
                previous = token.end
        return "".join(parts)

    def nodes_at(self, line: int, column: int) -> list[ast.expr | ast.stmt | ast.pattern]:
        """Return the expressions, patterns and statements at a position, innermost first (a def's from its ``@``)."""
        _ask("extents")
        return [
            node
            for node in reversed(self._holders((line, column)))
            if isinstance(node, (ast.expr, ast.stmt, ast.pattern))
        ]

    def segment(self, line: int, column: int) -> Segment:
        """Return the innermost `Segment` that holds a position: a statement, or a part of one that may be skipped.

        A position between two statements on one line (a ``;``) is in the statement that holds the line.
        """
        _ask("extents")
        chain = self._holders((line, column))
        if not chain and (statement := self._line_statements.get(line)) is not None:
            chain = [statement]
        if not chain:
            self._refuse(line, "a position outside every statement")
        found = Segment("statement", (self._span(chain[0]),), chain[0])
        for node, inner in itertools.pairwise([*chain, None]):
            if isinstance(node, ast.Match):
                found = self._case_of(node, (line, column)) or found
            if inner is not None:
                found = self._segment_of(node, inner) or found
        return found

    def _case_of(self, node: ast.Match, position: tuple[int, int]) -> Segment | None:
        """Return the segment of the case after the first whose keyword, pattern or guard holds a position, if any."""
        for case in node.cases[1:]:
            start = self._name_before("case", self._position(case.pattern))
            if start <= position < self._statement_start(case.body[0]):
                return Segment("case", ((start, self._end(case.guard or case.pattern)),), case)
        return None

    def _segment_of(self, node: Positioned, inner: Positioned) -> Segment | None:
        """Return the segment that `inner`, the child of `node` on the way to a position, opens, if any."""
        kind, spans = "", (self._span(inner),)
        if isinstance(inner, ast.stmt):
            kind = "statement"
        elif isinstance(node, ast.Match):
            kind = "guard" if any(inner is case.guard for case in node.cases) else ""
        elif isinstance(node, ast.IfExp):
            kind = "arm" if inner is not node.test else ""
        elif isinstance(node, ast.BoolOp):
            kind = "operand" if inner is not node.values[0] else ""
        elif isinstance(node, ast.DictComp) and inner in {node.key, node.value}:
            kind, spans = "element", ((self._position(node.key), self._end(node.value)),)
        elif isinstance(node, _COMPREHENSIONS):
            kind, spans = self._comprehension_part(node, inner)
        return Segment(kind, spans, inner) if kind else None

    def _comprehension_part(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp, inner: Positioned
    ) -> tuple[str, tuple[Region, ...]]:
        """Return the kind and spans of the part of a comprehension that `inner` opens (none: its first iterable)."""
        found: tuple[str, tuple[Region, ...]] = ("element", (self._span(inner),))
        for index, clause in enumerate(node.generators):
            first = clause.ifs[:1]
            if inner is clause.target or any(inner is condition for condition in first):
                found = ("target", tuple(self._span(part) for part in [clause.target, *first]))
            elif any(inner is condition for condition in clause.ifs[1:]):
                found = ("condition", (self._span(inner),))
            elif inner is clause.iter:
                found = ("iterable", (self._span(inner),)) if index else ("", ())
        return found

    def header_of(self, line: int, column: int) -> Scope | None:
        """Return the scope whose header holds a position: decorators, signature or bases, a generator's first iterable.

        The innermost scope region holding the position decides (a list, set or dict comprehension is looked through:
        it runs where it stands); None where that region holds it in its body, and at module level.
        """
        _ask("extents")
        position = (line, column)
        holding = [(scope, start) for scope, (start, end) in self._regions if start <= position < end]
        for scope, _ in sorted(holding, key=operator.itemgetter(1), reverse=True):
            node = scope.node
            if isinstance(node, _INLINED_KINDS):
                continue
            if isinstance(node, ast.GeneratorExp):
                start, end = self._span(node.generators[0].iter)
                return scope if start <= position < end else None
            body = self._body_start(scope)
            return scope if body is not None and position < body else None
        return None

    def span(self, node: Positioned) -> Region:
        """Return where a node starts and ends (columns in characters, the end exclusive; a def's from its ``@``)."""
        _ask("extents")
        return self._span(node)

    def _span(self, node: Positioned) -> Region:
        start = self._statement_start(node) if isinstance(node, ast.stmt) else self._position(node)
        return start, self._end(node)

    def _holders(self, position: tuple[int, int]) -> list[Positioned]:
        """Return the nodes with a position that hold `position`, outermost first; nodes without one are looked into.

        A type parameter is looked into too: its bound and default have positions of their own.
        """
        chain: list[Positioned] = []
        children = list(ast.iter_child_nodes(self.tree))
        while children:
            child = children.pop(0)
            if not isinstance(
                child, (ast.expr, ast.stmt, ast.pattern, ast.arg, ast.keyword, ast.excepthandler, ast.alias)
            ):
                children[:0] = ast.iter_child_nodes(child)
            elif (span := self._span(child))[0] <= position < span[1]:
                chain.append(child)
                children = list(ast.iter_child_nodes(child))
        return chain

    def _refuse(self, line: int, message: str) -> NoReturn:
        statement = self._line_statements.get(line)
        form = type(statement).__name__ if statement is not None else "module"
        msg = (
            f"{self.path}:{line}: {message} (form: {form}); the source model has no rule for this form: teach "
            "scripts/_source_model.py and add a form under tests/data/source_forms/<question>/ in the same pull request"
        )
        raise SourceModelError(msg)

    @cached_property
    def _code_lines(self) -> frozenset[int]:
        return frozenset(
            line
            for token in self._tokens
            if token.type not in _NOT_CODE
            for line in range(token.start[0], token.end[0] + 1)
        )

    @cached_property
    def _line_statements(self) -> dict[int, ast.stmt]:
        index: dict[int, ast.stmt] = {}
        todo: list[ast.AST] = [self.tree]
        while todo:
            node = todo.pop()
            if isinstance(node, ast.stmt):
                clauses = self._clauses(node)
                lines = [line for clause in clauses for line in clause.header] if clauses else self.extent(node)
                index.update(dict.fromkeys(lines, node))
            todo.extend(reversed(list(ast.iter_child_nodes(node))))
        return index

    @cached_property
    def _header_clauses(self) -> dict[int, Clause]:
        index: dict[int, Clause] = {}
        for node in ast.walk(self.tree):
            for clause in self._clauses(node) if isinstance(node, ast.stmt) else ():
                index.update(dict.fromkeys(clause.header, clause))
        return index

    @cached_property
    def _regions(self) -> list[tuple[Scope, Region]]:
        regions = []
        for scope in self._scopes:
            node = scope.node
            if scope.kind != "type-params" and isinstance(node, (ast.stmt, ast.expr)):
                start = self._statement_start(node) if isinstance(node, ast.stmt) else self._position(node)
                regions.append((scope, (start, self._end(node))))
        return regions

    def _statement_start(self, node: ast.stmt) -> tuple[int, int]:
        """Return the position a statement starts at: its first decorator's ``@``, else its first token."""
        if decorators := getattr(node, "decorator_list", None):
            return self._code_token_before(self._position(decorators[0])).start
        return self._position(node)

    def _header(self, start: int, body: list[ast.stmt]) -> range:
        return range(start, self._code_token_before(self._statement_start(body[0])).start[0] + 1)

    def _keyword_clause(self, keyword: str, body: list[ast.stmt]) -> Clause:
        line = self._name_before(keyword, self._statement_start(body[0]))[0]
        return Clause(keyword, self._header(line, body), self._suite(body))

    @staticmethod
    def _suite(body: list[ast.stmt]) -> range:
        return range(_first_line(body[0]), (body[-1].end_lineno or body[-1].lineno) + 1)

    def _clauses(self, node: ast.stmt) -> list[Clause]:
        if isinstance(node, _DEFINITIONS):
            keyword = "async def" if isinstance(node, ast.AsyncFunctionDef) else type(node).__name__[:-3].lower()
            return [Clause(keyword, self._header(_first_line(node), node.body), self._suite(node.body))]
        if isinstance(node, ast.If):
            return self._if_clauses(node)
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
            keyword = _LOOP_KEYWORDS.get(type(node), "async with")
            orelse = getattr(node, "orelse", [])
            found = [Clause(keyword, self._header(node.lineno, node.body), self._suite(node.body))]
            return found + ([self._keyword_clause("else", orelse)] if orelse else [])
        if isinstance(node, (ast.Try, ast.TryStar)):
            return self._try_clauses(node)
        return self._match_clauses(node) if isinstance(node, ast.Match) else []

    def _if_clauses(self, node: ast.If) -> list[Clause]:
        keyword = "elif" if self._is_elif(node) else "if"
        found = [Clause(keyword, self._header(node.lineno, node.body), self._suite(node.body))]
        chained = len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If) and self._is_elif(node.orelse[0])
        return found + ([self._keyword_clause("else", node.orelse)] if node.orelse and not chained else [])

    def _try_clauses(self, node: ast.Try | ast.TryStar) -> list[Clause]:
        found = [Clause("try", self._header(node.lineno, node.body), self._suite(node.body))]
        keyword = "except*" if isinstance(node, ast.TryStar) else "except"
        found += [
            Clause(keyword, self._header(item.lineno, item.body), self._suite(item.body)) for item in node.handlers
        ]
        found += [self._keyword_clause("else", node.orelse)] if node.orelse else []
        return found + ([self._keyword_clause("finally", node.finalbody)] if node.finalbody else [])

    def _match_clauses(self, node: ast.Match) -> list[Clause]:
        cases = []
        for case in node.cases:
            start = self._name_before("case", self._position(case.pattern))
            cases.append((start, Clause("case", self._header(start[0], case.body), self._suite(case.body))))
        header = range(node.lineno, self._code_token_before(cases[0][0]).start[0] + 1)
        return [Clause("match", header, range(cases[0][1].header.start, cases[-1][1].suite.stop))] + [
            clause for _, clause in cases
        ]

    def _is_elif(self, node: ast.If) -> bool:
        return self.lines[node.lineno - 1][self._column(node.lineno, node.col_offset) :].startswith("elif")

    def _body_start(self, scope: Scope) -> tuple[int, int] | None:
        node = scope.node
        if isinstance(node, _DEFINITIONS):
            return self._statement_start(node.body[0])
        return self._position(node.body) if isinstance(node, ast.Lambda) else None


class _Suppressions(_Extents):
    """Suppression comments and the lines each one covers."""

    @property
    def comments(self) -> dict[int, str]:
        """The comment tokens by line (a ``#`` inside a string is no comment)."""
        _ask("suppressions")
        return self._comments

    def suppressions(self, tool: str | None = None) -> list[Suppression]:
        """Return the suppression comments (of one tool: ruff, type, ty, pyright, heki, coverage)."""
        _ask("suppressions")
        return [item for item in self._suppressions if tool is None or item.tool == tool]

    def suppressed(self, tool: str, codes: tuple[str, ...]) -> frozenset[int]:
        """Return the lines a suppression of ``tool`` covers for any of ``codes`` (a bare one covers every code)."""
        _ask("suppressions")
        wanted = {code.upper() for code in codes}
        return frozenset(
            line
            for item in self._suppressions
            if item.tool == tool and (item.codes is None or wanted & {code.upper() for code in item.codes})
            for line in item.lines
        )

    @cached_property
    def _comments(self) -> dict[int, str]:
        return {token.start[0]: token.string for token in self._tokens if token.type == tokenize.COMMENT}

    @cached_property
    def _suppressions(self) -> list[Suppression]:
        found: list[Suppression] = []
        everything = frozenset(range(1, len(self.lines) + 1))
        for line, comment in sorted(self._comments.items()):
            own_line = self.lines[line - 1].lstrip().startswith("#")
            if (match := _NOQA.search(comment)) is not None:
                found.append(Suppression("ruff", "noqa", _codes(match["codes"]), line, frozenset({line})))
            if own_line and (match := _FILE_NOQA.match(comment)) is not None:
                found.append(Suppression("ruff", "file-noqa", _codes(match["codes"]), line, everything))
            for match in _RUFF_RULES.finditer(comment):
                listed = tuple(item.strip() for item in match["rules"].split(","))
                if match["action"] == "ignore":
                    lines = frozenset({self._next_code_line(line) if own_line else line})
                    found.append(Suppression("ruff", "ignore", listed, line, lines))
                elif match["action"] == "disable" and own_line:
                    lines = frozenset(range(line, self._range_end(line, listed)))
                    found.append(Suppression("ruff", "disable", listed, line, lines))
            for match in _TYPE_IGNORE.finditer(comment):
                codes = (
                    tuple(code.strip() for code in match["codes"].split(",")) if match["codes"] is not None else None
                )
                found.append(Suppression(match["tool"], "ignore", codes, line, frozenset({line})))
            for match in _HEKI_IGNORE.finditer(comment):
                codes = (
                    tuple(code.strip() for code in match["codes"].split(",") if code.strip())
                    if match["codes"] is not None
                    else None
                )
                if codes != ():
                    found.append(Suppression("heki", "ignore", codes, line, frozenset({line})))
            for match in _PRAGMA.finditer(comment):
                form = _PRAGMA_FORMS.get(" ".join(match[1].split()), "conditional-cover")
                lines = frozenset({line}) if form == "no-branch" else self._covered(line)
                found.append(Suppression("coverage", form, None, line, lines))
        return found

    def _next_code_line(self, line: int) -> int:
        return min((number for number in self._code_lines if number > line), default=line)

    def _range_end(self, line: int, listed: tuple[str, ...]) -> int:
        """Return the line ending a ``ruff: disable``: an ``enable`` of the identical list at its indentation, or code.

        Lines are read by the token that starts them, so a line inside a multi-line string neither ends the range nor
        holds an ``enable`` comment; code that starts left of the comment ends it.
        """
        indent = self._line_starts[line].start[1]
        for number in range(line + 1, len(self.lines) + 1):
            token = self._line_starts.get(number)
            if token is None:
                continue
            if token.type != tokenize.COMMENT and token.start[1] < indent:
                return number
            enables = [
                tuple(item.strip() for item in match["rules"].split(","))
                for match in _RUFF_RULES.finditer(token.string)
                if match["action"] == "enable"
            ]
            if token.type == tokenize.COMMENT and token.start[1] == indent and listed in enables:
                return number
        return len(self.lines) + 1

    @cached_property
    def _line_starts(self) -> dict[int, tokenize.TokenInfo]:
        """The token that starts each line outside strings (a comment or code), by line."""
        starts: dict[int, tokenize.TokenInfo] = {}
        depth = 0
        for token in self._tokens:
            if depth == 0 and token.type not in _LAYOUT:
                starts.setdefault(token.start[0], token)
            depth += (token.type in _STRING_STARTS) - (token.type in _STRING_ENDS)
        return starts

    def _covered(self, line: int) -> frozenset[int]:
        """Return the lines coverage excludes for a pragma on a line: a clause's header and suite, or the statement."""
        if (clause := self._header_clauses.get(line)) is not None:
            return frozenset(range(clause.header.start, clause.suite.stop))
        statement = self._line_statements.get(line)
        return frozenset(self.extent(statement)) if statement is not None else frozenset({line})


class _CompilerCheck(_Suppressions):
    """The comparison of the model with the compiler of the running interpreter: symbol tables and code objects."""

    future_annotations: bool
    _references: list[Reference]
    _resolved: dict[ast.Name, Resolution]
    _augmented: set[ast.Name]

    def compiled(self) -> dict[Scope, types.CodeType]:
        """Map each scope that compiles to a code object of its own on the running interpreter to that code object.

        Each scope is paired with a code object that the code of its nearest compiled enclosing scope loads, by
        ``co_name`` and the position of the instruction that loads it (the position of the scope's node; 3.12 loads a
        type parameter's bound at the parameter), so scopes with one name on one line each get their own; the
        ``__annotate__`` functions of 3.14 have no scope, and the code objects they load count as loaded by the code
        that loads them. Raises `SourceModelError` where a scope finds no code object or a code object no scope.
        """
        _ask("bindings")
        module = self._code
        loads = _loaded_code(module)
        found: dict[Scope, types.CodeType] = {self._module_scope: module}
        version = (sys.version_info.major, sys.version_info.minor)
        for scope in self._scopes[1:]:
            if not scope.compiles_on(version):
                continue
            parent = scope.parent
            while parent is not None and parent not in found:
                parent = parent.parent
            loaded = loads[found[parent]] if parent is not None else {}
            nodes = [scope.node, self._parents[scope.node]] if scope.kind == "type-bound" else [scope.node]
            keys = [(scope.code_name, getattr(node, "lineno", 0), getattr(node, "col_offset", 0)) for node in nodes]
            code = next((code for key in keys if (code := loaded.pop(key, None)) is not None), None)
            if code is None:
                self._refuse(scope.line, f"{scope.qualname} has no code object {keys[0]}")
            found[scope] = code
        if leftover := sorted(key for keys in loads.values() for key in keys):
            self._refuse(leftover[0][1], f"code objects without a scope: {leftover[:3]}")
        return found

    def verify(self) -> None:
        """Compare the model with the compiler's symbol table on the running interpreter; raise on a disagreement."""
        if problems := self.disagreements():
            raise SourceModelError(problems[0])

    def disagreements(self) -> list[str]:
        """Return where the model and the compiler's symbol table disagree on the running interpreter (the first)."""
        try:
            self._compare(self._module_scope, self._symbol_table())
        except SourceModelError as error:
            return [str(error)]
        return []

    @cached_property
    def _code(self) -> types.CodeType:
        """The module's code object on the running interpreter; a module the compiler rejects is a disagreement."""
        try:
            return compile(self.text, self.path, "exec", dont_inherit=True)
        except SyntaxError as error:
            self._rejected(error)

    def _symbol_table(self) -> symtable.SymbolTable:
        """Return the compiler's symbol table of the module, which the compiler must accept first (`_code`)."""
        try:
            return symtable.symtable(self.text, self._code.co_filename, "exec")
        except SyntaxError as error:
            self._rejected(error)

    def _rejected(self, error: SyntaxError) -> NoReturn:
        msg = f"{self.path}:{error.lineno or 0}: the compiler rejects the module, which ast reads ({error.msg})"
        raise CompilerRejectedError(msg) from None

    def _compare(self, scope: Scope, table: symtable.SymbolTable) -> None:
        group = [member for member in _inlined(scope) if not self._compiled_away(member)]
        symbols = {symbol.get_name(): symbol for symbol in table.get_symbols()}
        pool: dict[str, symtable.Symbol] = {}
        tables: list[symtable.SymbolTable] = []
        todo = list(table.get_children())
        while todo:
            child = todo.pop(0)
            if _table_kind(child) == "annotation":
                pool.update((symbol.get_name(), symbol) for symbol in child.get_symbols())
                todo[:0] = child.get_children()
            else:
                tables.append(child)
        children = [
            child
            for member in group
            for child in member.children
            if child not in _inlined(member) and not self._compiled_away(child)
        ]
        pairs = self._pair(scope, children, tables)
        self._compare_bindings(scope, group, symbols, pool)
        self._compare_reads(scope, group, symbols, pool)
        for child, child_table in pairs:
            self._compare(child, child_table)

    def _compiled_away(self, scope: Scope) -> bool:
        """Return whether the compiler never sees a scope: one inside an annotation the future import defers."""
        return self.future_annotations and bool(scope.annotation)

    def _compare_bindings(
        self, scope: Scope, group: list[Scope], symbols: dict[str, symtable.Symbol], pool: dict[str, symtable.Symbol]
    ) -> None:
        bound = {key for member in group for key in member.bindings}
        for member in group:
            for key in member.bindings:
                symbol = symbols.get(key) if member is scope else symbols.get(key, pool.get(key))
                local = symbol is not None and (member is not scope or scope.kind == "module" or symbol.is_local())
                if not local:
                    self._refuse(member.line, f"{member.qualname} binds {key}, the compiler does not")
        for name, symbol in [*symbols.items(), *pool.items()]:
            binds = symbol.is_assigned() or symbol.is_imported() if scope.kind == "module" else symbol.is_local()
            if binds and not _implicit(name) and name not in bound:
                self._refuse(scope.line, f"the compiler binds {name} in {scope.qualname}, the model does not")
        declared = scope.declared_global | scope.declared_nonlocal if scope.kind != "module" else set()
        for name, symbol in symbols.items() if scope.kind != "module" else ():
            if symbol.is_declared_global() != (name in scope.declared_global):
                self._refuse(scope.line, f"{scope.qualname}: global {name} disagrees with the compiler")
            if symbol.is_nonlocal() != (name in scope.declared_nonlocal):
                self._refuse(scope.line, f"{scope.qualname}: nonlocal {name} disagrees with the compiler")
        if unknown := sorted(declared - set(symbols)):
            self._refuse(scope.line, f"{scope.qualname}: the compiler does not know the declared {unknown[0]}")

    def _compare_reads(
        self, scope: Scope, group: list[Scope], symbols: dict[str, symtable.Symbol], pool: dict[str, symtable.Symbol]
    ) -> None:
        reads = [ref for member in group for ref in self._by_scope.get(member, ()) if ref.context == "load"]
        names = {reference.mangled for reference in reads}
        for name, symbol in [*symbols.items(), *pool.items()]:
            if symbol.is_referenced() and not _implicit(name) and name not in names:
                self._refuse(scope.line, f"the compiler counts a read of {name} in {scope.qualname} the model misses")
        in_annotations = sys.version_info >= (3, 14)
        for reference in reads:
            if reference.annotation in {"string", "deferred", "never"}:
                continue
            direct = reference.scope is scope
            born = not direct and bool(reference.scope.annotation) and not scope.annotation
            located = in_annotations and (bool(reference.annotation) or born)
            symbol = (pool if located else symbols).get(reference.mangled)
            if symbol is None:
                self._refuse(reference.line, f"{reference.name} is read here, the compiler knows no such name")
            if direct and not located and reference.node not in self._augmented and not symbol.is_referenced():
                self._refuse(reference.line, f"{reference.name} is read here, the compiler counts no read")
            if direct and not located and not self._agrees(reference, symbol):
                kind = self._resolved[reference.node].kind
                self._refuse(reference.line, f"{reference.name} is read as {kind}, the compiler disagrees")

    @cached_property
    def _by_scope(self) -> dict[Scope, list[Reference]]:
        index: dict[Scope, list[Reference]] = {}
        for reference in self._references:
            index.setdefault(reference.scope, []).append(reference)
        return index

    def _agrees(self, reference: Reference, symbol: symtable.Symbol) -> bool:
        resolution = self._resolved[reference.node]
        if resolution.kind == "local":
            return symbol.is_local() or symbol.is_parameter()
        if reference.scope.kind == "module":
            return not (symbol.is_assigned() or symbol.is_imported())
        reads_class = (
            resolution.kind == "enclosing" and resolution.scope is not None and resolution.scope.kind == "class"
        )
        if resolution.kind in {"enclosing", "class-cell"} and not reads_class:
            return symbol.is_free()
        return symbol.is_global()

    def _pair(
        self, scope: Scope, children: list[Scope], tables: list[symtable.SymbolTable]
    ) -> list[tuple[Scope, symtable.SymbolTable]]:
        left = list(tables)
        pairs = []
        for child in children:
            own = frozenset(key for key in child.bindings if not _implicit(key))
            inlined = own | {
                key
                for member in _inlined(child)
                if not self._compiled_away(member)
                for key in member.bindings
                if not _implicit(key)
            }
            match = next(
                (
                    table
                    for table in left
                    if (table.get_lineno(), _table_kind(table)) == (child.line, child.kind)
                    and own <= _table_names(table) <= inlined
                ),
                None,
            )
            if match is None:
                self._refuse(child.line, f"{child.qualname} ({child.kind}) has no compiler scope in {scope.qualname}")
            left.remove(match)
            pairs.append((child, match))
        if left:
            kind = _table_kind(left[0])
            self._refuse(left[0].get_lineno(), f"the compiler has a {kind} scope in {scope.qualname} the model lacks")
        return pairs


class _Reads(_CompilerCheck):
    """Annotations a module writes, what its assignment targets take, and what a name read in a scope resolves to."""

    _annotations: list[Annotation]

    def lookup(self, name: str, scope: Scope) -> Resolution:
        """Return what a name read in a scope resolves to, as `SourceModule.resolve` answers for an occurrence there.

        For reads the tree does not hold as names: the names of a parsed string (`name_reads`), ``__all__`` entries.
        """
        _ask("references")
        return self._resolve(_mangle(name, scope), scope)

    @property
    def annotations(self) -> list[Annotation]:
        """Every annotation: of a parameter, a return and an annotated assignment, in the order the walk meets them."""
        _ask("references")
        return self._annotations

    def assignment(self, target: ast.AST) -> Assignment | None:
        """Return what a target node takes (`Assignment`): a name, attribute, subscript, starred or unpacking target.

        None for any other node, and for the target of an augmented assignment, a deletion or a bare annotation.
        """
        _ask("bindings")
        return self._assignments.get(target)

    @cached_property
    def _assignments(self) -> dict[ast.AST, Assignment]:
        found: dict[ast.AST, Assignment] = {}
        for node in ast.walk(self.tree):
            for kind, target, value in _assigned(node):
                _pair(found, kind, target, value, value)
        return found

    def _resolve(self, mangled: str, scope: Scope) -> Resolution:
        if mangled in scope.declared_global:
            return self._global(mangled)
        if mangled in scope.bindings:
            fallback = self._global(mangled) if scope.kind == "class" else None
            return Resolution("local", scope, tuple(scope.bindings[mangled]), fallback)
        class_cell = mangled == "__class__" and scope.kind != "class"
        sees_class = scope.sees_class
        probe = scope.parent
        while probe is not None and probe.kind != "module":
            if probe.kind == "class" and class_cell:
                return Resolution("class-cell", probe, ())
            if probe.kind == "class" and sees_class and mangled in probe.bindings:
                return Resolution("enclosing", probe, tuple(probe.bindings[mangled]), self._global(mangled))
            if probe.kind != "class" and (mangled in probe.declared_global or mangled in probe.bindings):
                declared = mangled in probe.declared_global
                return (
                    self._global(mangled)
                    if declared
                    else Resolution("enclosing", probe, tuple(probe.bindings[mangled]))
                )
            sees_class = sees_class and probe.kind in _ANNOTATION_SCOPES
            probe = probe.parent
        return self._global(mangled)

    def _global(self, mangled: str) -> Resolution:
        module = self._module_scope
        if mangled in module.bindings:
            return Resolution("global", module, tuple(module.bindings[mangled]))
        if mangled in MODULE_ATTRIBUTES:
            return Resolution("global", module, ())
        if module.star_import:
            return Resolution("star", module, ())
        return Resolution("builtin" if mangled in _BUILTIN_NAMES else "unbound", None, ())


class _TypingForms(_Reads):
    """The parts of a module only type checkers read, by the origin of what decides it."""

    _guards: dict[ast.stmt, Guard]
    _node_scope: dict[ast.AST, Scope]
    _origin: Callable[..., str | None]

    def typing_only(self, line: int, column: int) -> str | None:
        """Return the form that makes a position code only type checkers read, or None where it may run.

        Forms: ``type-checking`` (a statement only type checkers run, `Import.typing_only`'s rule), ``overload`` (a
        ``def`` decorated with ``typing.overload``, from its ``@``), ``typing-decorator`` (a ``typing.override`` or
        ``typing.final`` decorator, which only sets an attribute), ``local-annotation`` (the annotation of an annotated
        assignment in a function, never evaluated) and ``deferred-annotation`` (a parameter or return annotation of a
        function under ``from __future__ import annotations``: a string only an annotation reader evaluates). Each
        decorator is read by origin (the ``typing_extensions`` names too).
        """
        _ask("references")
        chain = self._holders((line, column))
        position = (line, column)
        pairs = zip(chain, [*chain[1:], None][: len(chain)], strict=True)
        forms = (self._typing_form(node, inner, position) for node, inner in pairs)
        return next((form for form in forms if form is not None), None)

    def decorator_origins(self, node: ast.stmt) -> tuple[str | None, ...]:
        """Return the origin of each decorator of a ``def`` or ``class`` statement, in order; none for other statements.

        A decorator call (``@pytest.fixture(scope="module")``) is read by the origin of what it calls; a decorator
        without one origin (`SourceModule.origin`) is None.
        """
        _ask("references")
        decorators = (
            node.decorator_list if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else []
        )
        return tuple(self._origin(item.func if isinstance(item, ast.Call) else item) for item in decorators)

    def _typing_form(self, node: Positioned, inner: Positioned | None, position: tuple[int, int]) -> str | None:
        """Return the typing-only form `node` makes of its child `inner` (None: `position` itself), if any.

        The ``@`` of a decorator belongs to the decorator.
        """
        guard = self._guards.get(node, ()) if isinstance(node, ast.stmt) else ()
        function = node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
        decorators = function.decorator_list if function is not None else []
        if inner is None:
            starts = {self._code_token_before(self._position(item)).start: item for item in decorators}
            inner = starts.get(position)
        origins = set(self.decorator_origins(function)) if function is not None else set()
        annotation = (function is not None and inner is function.returns) or (
            isinstance(node, ast.arg) and inner is node.annotation
        )
        forms = (
            ("type-checking", any(item.kind == "type-checking" and item.branch == "body" for item in guard)),
            ("overload", bool(_OVERLOADS & origins)),
            (
                "typing-decorator",
                any(item is inner and self._origin(item) in _TYPING_DECORATORS for item in decorators),
            ),
            ("deferred-annotation", self.future_annotations and inner is not None and annotation),
            (
                "local-annotation",
                isinstance(node, ast.AnnAssign)
                and inner is node.annotation
                and self._node_scope[node].kind == "function",
            ),
        )
        return next((form for form, holds in forms if holds), None)


class SourceModule(_TypingForms):
    """The answers for one module's source text.

    ``path`` names the file in messages (``__init__.py`` marks a package), ``module`` is its dotted name (relative
    imports resolve against it). With ``verify`` (default: `VERIFY_ENV`), the model is compared with the compiler.
    """

    def __init__(self, text: str, path: str = "<source>", module: str = "", *, verify: bool | None = None) -> None:
        """Read the module: one walk over the tree, then resolution and classification."""
        self.text, self.path, self.module = text, path, module
        self.is_package = Path(path).name == "__init__.py"
        self.lines = _LINE_BREAK.split(text.removesuffix("\n").removesuffix("\r"))
        self.tree = ast.parse(text, filename=path)
        self.future_annotations = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "__future__"
            and any(alias.name == "annotations" for alias in node.names)
            for node in self.tree.body
        )
        self._scopes: list[Scope] = []
        self._references: list[Reference] = []
        self._imports: list[Import] = []
        self._dynamic: list[DynamicSite] = []
        self._calls: list[tuple[ast.Call, Scope, Guard]] = []
        self._tests: list[tuple[_Test, ast.expr]] = []
        self._tries: list[tuple[_Test, ast.Try | ast.TryStar]] = []
        self._roots: list[tuple[ast.expr, Scope, Guard]] = []
        self._annotations: list[Annotation] = []
        self._aliases: list[tuple[ast.expr, ast.expr, Scope, Guard]] = []
        self._pending: list[Binding] = []
        self._augmented: set[ast.Name] = set()
        self._node_scope: dict[ast.AST, Scope] = {}
        self._guards: dict[ast.stmt, Guard] = {}
        self._defined: dict[ast.AST, Scope] = {}
        self._string_refs: dict[ast.Constant, list[_TypeName]] = {}
        self._module_scope = self._new_scope("module", self.tree, None, "<module>", 0)
        self._walk_body(self.tree.body, self._module_scope, ())
        for binding in self._pending:
            owner = self._nonlocal_owner(binding)
            owner.bindings.setdefault(binding.mangled, []).append(replace(binding, scope=owner))
        self._resolved = {ref.node: self._resolve(ref.mangled, ref.scope) for ref in self._references}
        self._classify()
        self._read_calls()
        for root, scope, guard in self._roots:
            self._type_positions(root, scope, guard, [], record=True)
        if _verifying(verify=verify):
            self.verify()

    # ---- the answers ---------------------------------------------------------------------------------------------
    @property
    def scopes(self) -> list[Scope]:
        """Every scope, in source order (a parent before its children)."""
        _ask("bindings")
        return self._scopes

    @property
    def module_scope(self) -> Scope:
        """The module's scope."""
        _ask("bindings")
        return self._module_scope

    @property
    def references(self) -> list[Reference]:
        """Every name occurrence, including the names of strings in type positions."""
        _ask("references")
        return self._references

    @property
    def imports(self) -> list[Import]:
        """Every import, static and literal dynamic, in source order (none from an annotation that never runs)."""
        _ask("imports")
        return self._imports

    @property
    def dynamic_sites(self) -> list[DynamicSite]:
        """The statements that may bind names the source does not spell."""
        _ask("bindings")
        return self._dynamic

    def scope_of(self, node: ast.AST) -> Scope:
        """Return the scope a node of this module's tree is evaluated in (a ``def`` statement: the enclosing scope)."""
        _ask("bindings")
        return self._node_scope[node]

    def scope_defined_by(self, node: ast.AST) -> Scope:
        """Return the scope a ``def``, ``class``, ``lambda``, comprehension or ``type`` statement creates."""
        _ask("bindings")
        return self._defined[node]

    def bindings(self, name: str | None = None, scope: Scope | None = None) -> list[Binding]:
        """Return the bindings of a name (spelled or mangled) and/or of a scope, in source order per scope."""
        _ask("bindings")
        return [
            binding
            for current in (self._scopes if scope is None else [scope])
            for bindings in current.bindings.values()
            for binding in bindings
            if name is None or name in {binding.name, binding.mangled}
        ]

    def binding_at(self, node: ast.AST) -> Binding | None:
        """Return the binding a node of this module's tree makes, wherever the name lives; None for any other node.

        The nodes that bind are a binding's `Binding.node`: an import's ``alias``, a target name, a parameter, a
        ``def`` or ``class`` statement, an ``except`` handler, a pattern and a type parameter. A ``global`` or
        ``nonlocal`` name gives the binding of the scope it lives in.
        """
        _ask("bindings")
        return self._binding_nodes.get(node)

    def class_members(self, scope: Scope, *, spelled: bool = False) -> dict[str, list[ast.AST]]:
        """Return the names a class body binds plus the attributes its methods store on their receiver.

        The keys are the names the compiler stores (``_C__x`` for ``__x`` in class ``C``), or with ``spelled`` the
        names as the source writes them.
        """
        _ask("bindings")
        members: dict[str, list[ast.AST]] = {}
        for key, bindings in scope.bindings.items():
            for binding in bindings:
                members.setdefault(binding.name if spelled else key, []).append(binding.node)
        methods = [child for child in scope.children if child.kind == "function"]
        methods += [inner for child in scope.children if child.kind == "type-params" for inner in child.children]
        for method in methods:
            receiver = self.receiver(method)
            for use in self._uses.get(receiver, []) if receiver is not None else ():
                attribute = self._parents[use.reference.node]
                if use.kind == "attribute-store" and isinstance(attribute, ast.Attribute):
                    name = attribute.attr if spelled else _mangle(attribute.attr, scope)
                    members.setdefault(name, []).append(attribute)
        return members

    def receiver(self, scope: Scope) -> Binding | None:
        """Return a method's first positional parameter (``self``/``cls``); None for a staticmethod or a non-method."""
        _ask("bindings")
        node, parent = scope.node, scope.parent
        owner = parent.parent if parent is not None and parent.kind == "type-params" else parent
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or owner is None or owner.kind != "class":
            return None
        static = any(self._origin(decorator) == "builtins.staticmethod" for decorator in node.decorator_list)
        parameters = [] if static else [*node.args.posonlyargs, *node.args.args]
        bindings = scope.bindings.get(_mangle(parameters[0].arg, scope), []) if parameters else []
        return next((binding for binding in bindings if binding.node is parameters[0]), None)

    def resolve(self, node: ast.Name) -> Resolution:
        """Return what a name occurrence reads or binds: its scope and the binding sites there."""
        _ask("references")
        return self._resolved[node]

    def origin(self, node: ast.expr) -> str | None:
        """Return the dotted path a name or attribute chain names through imports and simple aliases, if exactly one.

        A module-level definition or value bound once is ``<module>.<name>``; a builtin is ``builtins.<name>``.
        """
        _ask("references")
        return self._origin(node)

    def origins(self, node: ast.expr) -> list[tuple[str, Guard]]:
        """Return every dotted path a name or attribute chain may name, each with the guard of its binding.

        An alias of a name bound more than once names the paths of each of its bindings, though `origin` gives none.
        """
        _ask("references")
        return self._origins(node, frozenset())

    def uses(self, binding: Binding) -> list[Use]:
        """Return the loads that may read a binding (a class body's fallback reads included), with how each is used."""
        _ask("references")
        return self._uses.get(binding, [])

    def use_of(self, node: ast.expr) -> str:
        """Return how the value of an expression of this module's tree is used: one of the kinds of `Use`.

        ``globals()`` in ``for k in globals():`` is ``iterate``, in ``[*globals()]`` ``unpack``, in
        ``dict(globals())`` ``argument`` and in ``globals().keys()`` ``call:keys``.
        """
        _ask("references")
        return self._use_kind(node)

    def type_positions(self, node: ast.expr) -> list[Reference]:
        """Return the names in the type positions of an expression; strings parsed, ``Literal`` values left out.

        ``Annotated`` metadata is left out too. Names parsed here from strings outside annotations, ``cast`` targets,
        ``TypeVar`` bounds and ``TypeAlias`` values do not join `references`. An attribute chain gives its head name
        (``typing`` of ``typing.Any``); `type_expressions` gives the whole chain.
        """
        _ask("references")
        return [reference for _, reference in self._type_names(node)]

    def type_expressions(self, node: ast.expr) -> list[ast.expr]:
        """Return the types an expression names in its type positions: each name or attribute chain, read by `origin`.

        The positions are those of `type_positions`; ``typing.Any`` and ``"t.Any"`` are one expression each, whose
        `origin` is ``typing.Any``. A name parsed from a string is a node of its own at the string's position.

        How a check counts ``Any`` with this answer (the ``any`` key of ``scripts/check_ratchets.py``): one site per
        type expression whose `origin` is ``typing.Any`` or ``typing_extensions.Any``, in the annotation of a
        parameter, a return or an annotated assignment and in the target of a ``cast``. `origin` follows aliases, so
        each use of an alias of ``Any`` (``Config: TypeAlias = Any``) is a site where it is used, and the alias's own
        value is none. A name whose bindings name different things (a ``TYPE_CHECKING`` import beside an ``Any``
        bound for run time) has no single origin and is no site: type checkers read the import. A name imported from
        another module of the tree is followed there with `SourceTree.definition`, and is a site when every definition
        it leads to is an assignment whose value's `origin` is ``Any``, so moving an alias to another module hides none
        of its uses.
        """
        _ask("references")
        return [expression for expression, _ in self._type_names(node)]

    def _type_names(self, node: ast.expr) -> list[_TypeName]:
        found: list[_TypeName] = []
        self._type_positions(node, self._node_scope[node], (), found, record=False)
        return found

    def constant(self, name: str) -> object:
        """Return the value of a module-level name bound once to a literal, never written through; else `Unreadable`."""
        _ask("references")
        bindings = self._module_scope.bindings.get(name, [])
        lines = tuple(binding.line for binding in bindings)
        value = bindings[0].value if len(bindings) == 1 and bindings[0].kind in {"assign", "annassign"} else None
        if value is None:
            return Unreadable(f"{name} is not bound exactly once by an assignment", lines)
        writes = tuple(
            use.reference.line
            for use in self._uses.get(bindings[0], [])
            if use.kind in _WRITES or use.kind.removeprefix("call:") in _MUTATORS
        )
        if writes:
            return Unreadable(f"{name} is written through", writes)
        try:
            return self._literal(value)
        except _NotLiteralError as error:
            return Unreadable(f"{name} is not a literal: {error}", lines)

    # ---- pass 1: one walk ----------------------------------------------------------------------------------------
    def _new_scope(self, kind: ScopeKind, node: ast.AST, parent: Scope | None, code_name: str, line: int) -> Scope:
        base = parent
        while base is not None and (base.kind == "type-params" or isinstance(base.node, _INLINED_KINDS)):
            base = base.parent
        explicit = (
            base is not None and kind in {"function", "class"} and _mangle(code_name, base) in base.declared_global
        )
        dotted = base is not None and base.kind != "module" and not explicit
        separator = "." if base is not None and base.kind == "class" else ".<locals>."
        prefix = f"{base.qualname}{separator}" if base is not None and dotted else ""
        qualname = "<module>" if parent is None else prefix + code_name
        siblings = parent.children if parent is not None else []
        ordinal = sum(sibling.qualname == qualname for sibling in siblings)
        first_line = _first_line(node) if isinstance(node, _DEFINITIONS) else max(line, 1)
        scope = Scope(kind, node, parent, qualname, ordinal, code_name, first_line, line)
        scope.annotation = parent.annotation if parent is not None else ""
        siblings.append(scope)
        self._scopes.append(scope)
        if kind != "type-params":
            self._defined[node] = scope
        return scope

    def _bind(self, scope: Scope, name: str, kind: BindingKind, node: ast.AST, guard: Guard, **extra: object) -> None:
        mangled = _mangle(name, scope)
        target = self._module_scope if mangled in scope.declared_global else scope
        plain = Binding(target, name, mangled, kind, node, getattr(node, "lineno", 0), guard)
        binding = replace(plain, **extra) if extra else plain
        if mangled in scope.declared_nonlocal:
            self._pending.append(binding)
        else:
            target.bindings.setdefault(mangled, []).append(binding)

    def _nonlocal_owner(self, binding: Binding) -> Scope:
        probe = binding.scope.parent
        while probe is not None and probe.kind != "module":
            if probe.kind != "class" and binding.mangled in probe.declared_nonlocal:
                return self._nonlocal_owner(replace(binding, scope=probe))
            if probe.kind != "class" and binding.mangled in probe.bindings:
                return probe
            probe = probe.parent
        return self._refuse(binding.line, f"nonlocal {binding.name} has no enclosing binding")

    def _walk_body(self, body: list[ast.stmt], scope: Scope, guard: Guard) -> None:
        for node in _statements_within(body):
            if isinstance(node, ast.Global):
                scope.declared_global.update(_mangle(name, scope) for name in node.names)
            elif isinstance(node, ast.Nonlocal):
                scope.declared_nonlocal.update(_mangle(name, scope) for name in node.names)
        self._statements(body, scope, guard)

    def _statements(self, body: list[ast.stmt], scope: Scope, guard: Guard) -> None:
        for statement in body:
            self._statement(statement, scope, guard)

    def _statement(self, node: ast.stmt, scope: Scope, guard: Guard) -> None:
        self._node_scope[node] = scope
        self._guards[node] = guard
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._function(node, scope, guard)
        elif isinstance(node, ast.ClassDef):
            self._class(node, scope, guard)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            self._import(node, scope, guard)
        elif isinstance(node, ast.If):
            test = _Test()
            self._tests.append((test, node.test))
            self._expr(node.test, scope, guard)
            self._statements(node.body, scope, (*guard, Condition(test, "body", node, node.lineno)))
            self._statements(node.orelse, scope, (*guard, Condition(test, "else", node, node.lineno)))
        elif isinstance(node, (ast.Try, ast.TryStar)):
            self._try(node, scope, guard)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            self._loop(node, scope, guard)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                self._node_scope[item] = scope
                self._expr(item.context_expr, scope, guard)
                if item.optional_vars is not None:
                    self._target(item.optional_vars, scope, "with", guard)
            self._statements(node.body, scope, guard)
        elif isinstance(node, ast.Match):
            self._match(node, scope, guard)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                self._target(target, scope, "assign", guard, node.value)
            self._expr(node.value, scope, guard)
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name):
                self._augmented.add(node.target)
                self._reference(node.target, scope, guard, "load")
            self._target(node.target, scope, "augassign", guard)
            self._expr(node.value, scope, guard)
        elif isinstance(node, ast.AnnAssign):
            self._annotated_assignment(node, scope, guard)
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                self._target(target, scope, "del", guard)
        elif sys.version_info >= (3, 12) and isinstance(node, ast.TypeAlias):
            outer = self._type_params(node, node.name.id, scope, guard)
            self._target(node.name, scope, "type-alias", guard)
            self._expr(node.value, self._new_scope("type-alias", node, outer, node.name.id, node.lineno), guard)
        elif not isinstance(node, (ast.Global, ast.Nonlocal)):
            for child in ast.iter_child_nodes(node):
                self._expr(child, scope, guard)

    def _loop(self, node: ast.For | ast.AsyncFor | ast.While, scope: Scope, guard: Guard) -> None:
        loop = _Test("loop")
        body, otherwise = Condition(loop, "body", node, node.lineno), Condition(loop, "else", node, node.lineno)
        if isinstance(node, ast.While):
            self._expr(node.test, scope, guard)
        else:
            self._target(node.target, scope, "for", (*guard, body))
            self._expr(node.iter, scope, guard)
        self._statements(node.body, scope, (*guard, body))
        self._statements(node.orelse, scope, (*guard, otherwise))

    def _match(self, node: ast.Match, scope: Scope, guard: Guard) -> None:
        self._expr(node.subject, scope, guard)
        for case in node.cases:
            self._node_scope[case] = scope
            inner = (*guard, Condition(_Test("case"), "body", case, case.pattern.lineno))
            self._pattern(case.pattern, scope, inner)
            if case.guard is not None:
                self._expr(case.guard, scope, inner)
            self._statements(case.body, scope, inner)

    def _annotation_mode(self) -> str:
        return "deferred" if self.future_annotations else "evaluated"

    def _annotation(self, annotation: Annotation | None, guard: Guard) -> None:
        if annotation is not None:
            self._annotations.append(annotation)
            self._roots.append((annotation.node, annotation.scope, guard))
            self._expr(annotation.node, annotation.scope, guard, annotation.mode)

    def _annotated_assignment(self, node: ast.AnnAssign, scope: Scope, guard: Guard) -> None:
        kind: BindingKind = "annassign" if node.value is not None else "annotation-only"
        if node.simple or node.value is not None or not isinstance(node.target, ast.Name):
            self._target(node.target, scope, kind, guard, node.value)
        else:
            self._node_scope[node.target] = scope
        local = scope.kind not in {"module", "class"}
        mode = "never" if local else self._annotation_mode()
        self._annotation(_annotated(node.annotation, scope, mode, node.target, node.value), guard)
        if node.value is not None:
            self._aliases.append((node.annotation, node.value, scope, guard))
            self._expr(node.value, scope, guard)

    def _defaults(self, arguments: ast.arguments, scope: Scope, guard: Guard, annotation: str = "") -> None:
        for default in [*arguments.defaults, *(item for item in arguments.kw_defaults if item is not None)]:
            self._expr(default, scope, guard, annotation)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, scope: Scope, guard: Guard) -> None:
        for decorator in node.decorator_list:
            self._expr(decorator, scope, guard)
        self._defaults(node.args, scope, guard)
        outer = self._type_params(node, node.name, scope, guard)
        parameters = _parameters(node.args)
        for parameter in parameters:
            self._annotation(_annotated(parameter.annotation, outer, self._annotation_mode(), parameter), guard)
        self._annotation(_annotated(node.returns, outer, self._annotation_mode()), guard)
        self._bind(scope, node.name, "def", node, guard)
        inner = self._new_scope("function", node, outer, node.name, node.lineno)
        call = (*guard, Condition(_Test("function"), "body", node, node.lineno))
        for parameter in parameters:
            self._node_scope[parameter] = inner
            self._bind(inner, parameter.arg, "param", parameter, call)
        self._walk_body(node.body, inner, call)

    def _class(self, node: ast.ClassDef, scope: Scope, guard: Guard) -> None:
        for decorator in node.decorator_list:
            self._expr(decorator, scope, guard)
        outer = self._type_params(node, node.name, scope, guard)
        for part in [*node.bases, *node.keywords]:
            self._expr(part, outer, guard)
        self._bind(scope, node.name, "class", node, guard)
        inner = self._new_scope("class", node, outer, node.name, node.lineno)
        self._walk_body(node.body, inner, guard)

    def _type_params(self, node: ast.stmt, name: str, scope: Scope, guard: Guard) -> Scope:
        """Open the annotation scope of a 3.12+ generic ``def``, ``class`` or ``type`` and bind its type parameters."""
        parameters = getattr(node, "type_params", None)
        if not parameters:
            return scope
        params = self._new_scope("type-params", node, scope, f"<generic parameters of {name}>", node.lineno)
        for parameter in parameters:
            self._node_scope[parameter] = params
            self._bind(params, parameter.name, "type-param", parameter, guard)
            for lazy in (getattr(parameter, "bound", None), getattr(parameter, "default_value", None)):
                if lazy is not None:
                    bound = self._new_scope("type-bound", lazy, params, parameter.name, parameter.lineno)
                    self._expr(lazy, bound, guard)
        return params

    def _try(self, node: ast.Try | ast.TryStar, scope: Scope, guard: Guard) -> None:
        test = _Test()
        self._tries.append((test, node))
        self._statements(node.body, scope, (*guard, Condition(test, "body", node, node.lineno)))
        for handler in node.handlers:
            self._node_scope[handler] = scope
            inner = (*guard, Condition(test, "except", handler, handler.lineno))
            if handler.type is not None:
                self._expr(handler.type, scope, inner)
            if handler.name:
                self._bind(scope, handler.name, "except", handler, inner)
            self._statements(handler.body, scope, inner)
        self._statements(node.orelse, scope, (*guard, Condition(test, "else", node, node.lineno)))
        self._statements(node.finalbody, scope, (*guard, Condition(test, "finally", node, node.lineno)))

    def _import(self, node: ast.Import | ast.ImportFrom, scope: Scope, guard: Guard) -> None:
        lazy = bool(getattr(node, "is_lazy", 0))
        source: str | Unreadable = self._absolute(node) if isinstance(node, ast.ImportFrom) else ""
        level = node.level if isinstance(node, ast.ImportFrom) else 0
        for alias in node.names:
            self._node_scope[alias] = scope
            if isinstance(node, ast.Import):
                bound = alias.asname or alias.name.partition(".")[0]
                form = "import X as X" if alias.asname == alias.name else None
                origin = alias.name if alias.asname else bound
                self._bind(scope, bound, "import", alias, guard, origin=origin, reexport_form=form)
                self._imports.append(
                    Import(node, alias, "import", bound, alias.name, alias.name, scope, guard, 0, lazy)
                )
            elif alias.name == "*":
                scope.star_import = True
                self._dynamic.append(DynamicSite("star-import", node, node.lineno, scope, guard))
                self._imports.append(Import(node, alias, "star", None, source, source, scope, guard, level, lazy))
            else:
                bound = alias.asname or alias.name
                target = f"{source}.{alias.name}" if isinstance(source, str) else source
                relative = level == 1 and getattr(node, "module", None) is None and alias.asname is None
                form = "from M import X as X" if alias.asname == alias.name else "from . import X" if relative else None
                origin = target if isinstance(target, str) else None
                self._bind(scope, bound, "import-from", alias, guard, origin=origin, reexport_form=form)
                self._imports.append(Import(node, alias, "from", bound, source, target, scope, guard, level, lazy))

    def _absolute(self, node: ast.ImportFrom) -> str | Unreadable:
        package = self.module.split(".") if self.is_package else self.module.split(".")[:-1]
        if node.level and (not self.module or node.level - 1 >= len(package)):
            name = self.module or "an unnamed module"
            return Unreadable(f"the relative import leaves the package of {name}", (node.lineno,))
        base = package[: len(package) - (node.level - 1)] if node.level else []
        return ".".join([*base, *([node.module] if node.module else [])])

    def _target(
        self, target: ast.expr, scope: Scope, kind: BindingKind, guard: Guard, value: ast.expr | None = None
    ) -> None:
        self._node_scope[target] = scope
        if isinstance(target, ast.Name):
            single = value if kind in {"assign", "annassign"} else None
            self._bind(scope, target.id, kind, target, guard, value=single)
            self._reference(target, scope, guard, "del" if kind == "del" else "store")
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._target(item, scope, kind, guard)
        elif isinstance(target, ast.Starred):
            self._target(target.value, scope, kind, guard)
        else:
            for child in ast.iter_child_nodes(target):
                self._expr(child, scope, guard)

    def _pattern(self, pattern: ast.pattern, scope: Scope, guard: Guard) -> None:
        self._node_scope[pattern] = scope
        for child in ast.iter_child_nodes(pattern):
            if isinstance(child, ast.pattern):
                self._pattern(child, scope, guard)
            else:
                self._expr(child, scope, guard)
        name = getattr(pattern, "rest" if isinstance(pattern, ast.MatchMapping) else "name", None)
        if isinstance(name, str):
            self._bind(scope, name, "match", pattern, guard)

    def _expr(self, node: ast.AST, scope: Scope, guard: Guard, annotation: str = "") -> None:
        self._node_scope[node] = scope
        if isinstance(node, ast.Name):
            self._reference(node, scope, guard, "load", annotation)
        elif isinstance(node, ast.Lambda):
            self._lambda(node, scope, guard, annotation)
        elif isinstance(node, _COMPREHENSIONS):
            self._comprehension(node, scope, guard, annotation)
        elif isinstance(node, ast.NamedExpr):
            self._walrus(node, scope, guard, annotation)
        elif isinstance(node, ast.IfExp):
            self._if_expression(node, scope, guard, annotation)
        elif isinstance(node, ast.BoolOp):
            self._short_circuit(node, scope, guard, annotation)
        else:
            if isinstance(node, ast.Call) and _NOT_RUN.isdisjoint({annotation, scope.annotation}):
                self._calls.append((node, scope, guard))
            for child in ast.iter_child_nodes(node):
                self._expr(child, scope, guard, annotation)

    def _if_expression(self, node: ast.IfExp, scope: Scope, guard: Guard, annotation: str) -> None:
        """Read ``body if test else orelse``: each branch runs under the test, like the branches of an ``if``."""
        test = _Test()
        self._tests.append((test, node.test))
        self._expr(node.test, scope, guard, annotation)
        self._expr(node.body, scope, (*guard, Condition(test, "body", node, node.lineno)), annotation)
        self._expr(node.orelse, scope, (*guard, Condition(test, "else", node, node.lineno)), annotation)

    def _short_circuit(self, node: ast.BoolOp, scope: Scope, guard: Guard, annotation: str) -> None:
        """Read ``a and b`` / ``a or b``: each operand after the first runs under the test of the one before it.

        The condition holds the operand it tests (the ``and`` branch is ``body``, the ``or`` branch ``else``).
        """
        branch = "body" if isinstance(node.op, ast.And) else "else"
        *operands, last = node.values
        for value in operands:
            self._expr(value, scope, guard, annotation)
            test = _Test()
            self._tests.append((test, value))
            guard = (*guard, Condition(test, branch, value, value.lineno))
        self._expr(last, scope, guard, annotation)

    def _lambda(self, node: ast.Lambda, scope: Scope, guard: Guard, annotation: str) -> None:
        self._defaults(node.args, scope, guard, annotation)
        inner = self._new_scope("lambda", node, scope, "<lambda>", node.lineno)
        inner.annotation = inner.annotation or annotation
        call = (*guard, Condition(_Test("function"), "body", node, node.lineno))
        for parameter in _parameters(node.args):
            self._node_scope[parameter] = inner
            self._bind(inner, parameter.arg, "param", parameter, call)
        self._expr(node.body, inner, call)

    def _comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp, scope: Scope, guard: Guard, mode: str
    ) -> None:
        first = node.generators[0]
        self._expr(first.iter, scope, guard, mode)
        name = _COMPREHENSION_NAMES.get(type(node), "<genexpr>")
        inner = self._new_scope("comprehension", node, scope, name, node.lineno)
        inner.annotation = inner.annotation or mode
        loop = (*guard, Condition(_Test("loop"), "body", node, node.lineno))
        for index, generator in enumerate(node.generators):
            self._node_scope[generator] = inner
            self._target(generator.target, inner, "comprehension", loop)
            if index:
                self._expr(generator.iter, inner, loop)
            for condition in generator.ifs:
                self._expr(condition, inner, loop)
        for part in [node.value, node.key] if isinstance(node, ast.DictComp) else [node.elt]:
            self._expr(part, inner, loop)

    def _walrus(self, node: ast.NamedExpr, scope: Scope, guard: Guard, annotation: str) -> None:
        self._expr(node.value, scope, guard, annotation)
        owner = scope
        while owner.kind == "comprehension" and owner.parent is not None:
            owner = owner.parent
        if owner is not scope:
            (scope.declared_global if owner.kind == "module" else scope.declared_nonlocal).add(node.target.id)
        self._node_scope[node.target] = owner
        self._bind(owner, node.target.id, "walrus", node.target, guard)
        self._reference(node.target, owner, guard, "store")

    def _reference(self, node: ast.Name, scope: Scope, guard: Guard, context: str, annotation: str = "") -> None:
        column = self._column(node.lineno, node.col_offset)
        mangled = _mangle(node.id, scope)
        self._references.append(
            Reference(node, node.id, mangled, scope, context, annotation, guard, node.lineno, column)
        )

    # ---- pass 2: resolution and classification -------------------------------------------------------------------
    def _origin(self, node: ast.expr, active: frozenset[Binding] = frozenset()) -> str | None:
        head, parts = _chain(node)
        resolution = self._resolved.get(head) if head is not None else None
        if head is None or resolution is None:
            return None
        if resolution.kind == "builtin":
            return ".".join(["builtins", head.id, *parts])
        origins = {self._binding_origin(binding, active) for binding in resolution.bindings}
        found = origins.pop() if len(origins) == 1 else None
        return ".".join([found, *parts]) if found is not None else None

    def _origins(self, node: ast.expr, active: frozenset[Binding]) -> list[tuple[str, Guard]]:
        head, parts = _chain(node)
        resolution = self._resolved.get(head) if head is not None else None
        return [
            (".".join([origin, *parts]), guard)
            for binding in (resolution.bindings if resolution is not None else ())
            for origin, guard in self._binding_origins(binding, active)
        ]

    def _binding_origins(self, binding: Binding, active: frozenset[Binding]) -> list[tuple[str, Guard]]:
        """Return every path a binding may give its name, each with the guard it holds under.

        An alias of a multiply bound name gives each path of theirs, under its own guard and the alias's.
        """
        alias = binding.kind in {"assign", "annassign"} and isinstance(binding.value, (ast.Name, ast.Attribute))
        if alias and binding.origin is None and binding not in active and binding.value is not None:
            return [
                (origin, (*binding.guard, *(condition for condition in guard if condition not in binding.guard)))
                for origin, guard in self._origins(binding.value, active | {binding})
            ]
        found = self._binding_origin(binding, active)
        return [] if found is None else [(found, binding.guard)]

    def _binding_origin(self, binding: Binding, active: frozenset[Binding]) -> str | None:
        if binding.origin is not None:
            return binding.origin
        if binding in active or len(binding.scope.bindings[binding.mangled]) != 1:
            return None
        if binding.kind in {"assign", "annassign"} and isinstance(binding.value, (ast.Name, ast.Attribute)):
            return self._origin(binding.value, active | {binding})
        definitions = {"def", "class", "assign", "annassign", "annotation-only", "type-alias"}
        if binding.scope.kind == "module" and self.module and binding.kind in definitions:
            return f"{self.module}.{binding.mangled}"
        return None

    def _operand(self, node: ast.expr) -> str:
        negated = False
        while isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            node, negated = node.operand, not negated
        if isinstance(node, (ast.Name, ast.Attribute)) and self._origin(node) in _TYPE_CHECKING:
            return "not-type-checking" if negated else "type-checking"
        origins = [self._origin(part) or "" for part in ast.walk(node) if isinstance(part, (ast.Name, ast.Attribute))]
        if any(origin.startswith(_VERSIONS) for origin in origins):
            return "version"
        return "platform" if _PLATFORMS.intersection(origins) else "other"

    def _classify(self) -> None:
        for test, condition in self._tests:
            node = condition
            while isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                node, test.negated = node.operand, not test.negated
            values = node.values if isinstance(node, ast.BoolOp) else [node]
            kinds = [self._operand(value) for value in values]
            conjunction = isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And)
            single = not isinstance(node, ast.BoolOp)
            if "type-checking" in kinds and "not-type-checking" not in kinds and (conjunction or single):
                test.kind = "type-checking"
            elif len(set(kinds)) == 1 and kinds[0] in {"version", "platform"}:
                test.kind = kinds[0]
            else:
                test.kind = "if"
            test.operands = () if single else tuple(kinds)
        for test, node in self._tries:
            caught = {
                self._origin(part) if part is not None else "builtins.BaseException"
                for handler in node.handlers
                for part in (handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type])
            }
            imports = any(isinstance(item, (ast.Import, ast.ImportFrom)) for item in _statements_within(node.body))
            test.kind = "import-error" if imports and caught & _IMPORT_ERRORS else "try"

    def _read_calls(self) -> None:
        for call, scope, guard in self._calls:
            origin = self._origin(call.func) or ""
            setattr_module = origin == "builtins.setattr" and bool(call.args) and self._is_module_object(call.args[0])
            if origin in _NAMESPACE_CALLS or setattr_module:
                kind = _NAMESPACE_CALLS.get(origin, "setattr-module")
                self._dynamic.append(DynamicSite(kind, call, call.lineno, scope, guard))
            elif origin in _IMPORT_CALLS:
                self._dynamic_import(call, origin, scope, guard)
            elif origin in _CASTS and call.args:
                self._roots.append((call.args[0], scope, guard))
            elif origin in _TYPE_VARS:
                bounds = [*call.args[1:], *(item.value for item in call.keywords if item.arg == "bound")]
                self._roots.extend((bound, scope, guard) for bound in bounds)
        for annotation, value, scope, guard in self._aliases:
            if self._origin(annotation) in _TYPE_ALIASES:
                self._roots.append((value, scope, guard))
        self._imports.sort(key=lambda item: (item.node.lineno, item.node.col_offset))

    def _dynamic_import(self, call: ast.Call, origin: str, scope: Scope, guard: Guard) -> None:
        target = self._literal_import(call, origin)
        if target is not None:
            self._imports.append(Import(call, None, "dynamic", None, target, target, scope, guard, 0, lazy=False))
        else:
            self._dynamic.append(DynamicSite("import-module", call, call.lineno, scope, guard))

    def _is_module_object(self, node: ast.expr) -> bool:
        return isinstance(node, ast.Subscript) and self._origin(node.value) == "sys.modules"

    def _literal_import(self, call: ast.Call, origin: str) -> str | None:
        name = _string(argument(call, 0, "name"))
        if name is None:
            return None
        level, base, rest = self._import_anchor(call, origin, name)
        if not level:
            return None if level is None else rest
        parts = base.split(".") if base else []
        if level - 1 >= len(parts):
            return None
        return ".".join([*parts[: len(parts) - (level - 1)], *([rest] if rest else [])])

    def _import_anchor(self, call: ast.Call, origin: str, name: str) -> tuple[int | None, str, str]:
        """Return a literal import call's level (None: unreadable), the package it counts from, and the name's rest.

        ``import_module`` counts the leading dots of the name from ``package``; ``__import__`` takes ``level`` as an
        argument and counts it from the package of its ``globals`` (``globals()``: this module's).
        """
        if origin == "builtins.__import__":
            level = argument(call, 4, "level")
            namespace = argument(call, 1, "globals")
            own = isinstance(namespace, ast.Call) and self._origin(namespace.func) == "builtins.globals"
            count = 0 if level is None else _count(level)
            return (None if name.startswith(".") else count), self._package() if own else "", name
        level = len(name) - len(name.lstrip("."))
        package = argument(call, 1, "package")
        implicit = isinstance(package, ast.Name) and self._resolved[package] == Resolution(
            "global", self._module_scope, ()
        )
        own_names = {"__name__": self.module, "__package__": self._package()}
        base = own_names.get(package.id) if implicit and isinstance(package, ast.Name) else _string(package)
        return level, base or "", name[level:]

    def _package(self) -> str:
        return self.module if self.is_package else self.module.rpartition(".")[0]

    def _type_positions(
        self, node: ast.expr, scope: Scope, guard: Guard, found: list[_TypeName], *, record: bool
    ) -> None:
        head = _chain(node)[0] if isinstance(node, (ast.Name, ast.Attribute)) else None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node not in self._string_refs:
                self._string_refs[node] = self._parse_string(node, scope, guard, record=record)
            found.extend(self._string_refs[node])
        elif isinstance(node, ast.Subscript):
            self._type_positions(node.value, scope, guard, found, record=record)
            head = self._origin(node.value)
            index = node.slice
            if head in _ANNOTATED_HEADS and isinstance(index, ast.Tuple) and index.elts:
                index = index.elts[0]
            if head not in _LITERAL_HEADS:
                self._type_positions(index, scope, guard, found, record=record)
        elif head is not None:
            found.append((node, self._load_of(head, scope, guard, record=record)))
        else:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    self._type_positions(child, scope, guard, found, record=record)

    def _parse_string(self, node: ast.Constant, scope: Scope, guard: Guard, *, record: bool) -> list[_TypeName]:
        inner = _string_expression(node)
        if inner is None:
            return []
        for part in ast.walk(inner):
            if isinstance(part, ast.expr):
                part.lineno, part.col_offset = node.lineno, node.col_offset
        found: list[_TypeName] = []
        self._type_positions(inner, scope, guard, found, record=record)
        return found

    def _load_of(self, node: ast.Name, scope: Scope, guard: Guard, *, record: bool) -> Reference:
        known = self._loads.get(node)
        if known is not None:
            return known
        column = self._column(node.lineno, node.col_offset)
        mangled = _mangle(node.id, scope)
        reference = Reference(node, node.id, mangled, scope, "load", "string", guard, node.lineno, column)
        self._resolved[node] = self._resolve(mangled, scope)
        self._loads[node] = reference
        if record:
            self._references.append(reference)
        return reference

    @cached_property
    def _loads(self) -> dict[ast.Name, Reference]:
        return {reference.node: reference for reference in self._references if reference.context == "load"}

    def _literal(self, node: ast.expr) -> object:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            items = [self._literal(item) for item in node.elts]
            build = {ast.Tuple: tuple, ast.List: list}.get(type(node), set)
            return _built(build, items)
        if isinstance(node, ast.Dict) and None not in node.keys:
            keys = [self._literal(key) for key in node.keys if key is not None]
            return _built(dict, list(zip(keys, map(self._literal, node.values), strict=True)))
        operand = node.operand if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)) else None
        value = self._literal(operand) if operand is not None else None
        if isinstance(value, (int, float, complex)) and not isinstance(value, bool):
            return -value if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) else value
        build = _LITERAL_CALLS.get(self._origin(node.func) or "") if isinstance(node, ast.Call) else None
        if isinstance(node, ast.Call) and build is not None and not node.keywords and len(node.args) <= 1:
            return _built(build, self._literal(node.args[0]) if node.args else ())
        message = "a ** entry" if isinstance(node, ast.Dict) else f"a {type(node).__name__} at line {node.lineno}"
        raise _NotLiteralError(message)

    @cached_property
    def _uses(self) -> dict[Binding, list[Use]]:
        index: dict[Binding, list[Use]] = {}
        for reference in self._references:
            resolution: Resolution | None = self._resolved[reference.node] if reference.context == "load" else None
            use = Use(reference, self._use_kind(reference.node))
            while resolution is not None:
                for binding in resolution.bindings:
                    index.setdefault(binding, []).append(use)
                resolution = resolution.fallback
        return index

    def _use_kind(self, node: ast.expr) -> str:
        parent = self._parents.get(node)
        grandparent = self._parents.get(parent) if parent is not None else None
        if isinstance(parent, (ast.Subscript, ast.Attribute)) and parent.value is node:
            return _receiver_use(parent, grandparent)
        return _position_use(node, parent, grandparent)

    @cached_property
    def _binding_nodes(self) -> dict[ast.AST, Binding]:
        return {binding.node: binding for binding in self.bindings()}


def _annotated(
    node: ast.expr | None, scope: Scope, mode: str, target: ast.AST | None = None, value: ast.expr | None = None
) -> Annotation | None:
    """Return the annotation a node is, or None where the source writes none."""
    return None if node is None else Annotation(node, scope, mode, node.lineno, target, value)


def _assigned(node: ast.AST) -> list[tuple[str, ast.expr, ast.expr]]:
    """Return the targets a node assigns, each with its form and the expression it evaluates for them."""
    if isinstance(node, ast.Assign):
        return [("assign", target, node.value) for target in node.targets]
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return [("annassign", node.target, node.value)]
    if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
        return [("for" if isinstance(node, (ast.For, ast.AsyncFor)) else "comprehension", node.target, node.iter)]
    if isinstance(node, ast.withitem) and node.optional_vars is not None:
        return [("with", node.optional_vars, node.context_expr)]
    return [("walrus", node.target, node.value)] if isinstance(node, ast.NamedExpr) else []


def _pair(found: dict[ast.AST, Assignment], kind: str, target: ast.expr, value: ast.expr, element: ast.expr) -> None:
    """Record what a target takes, and pair the items of an unpacking target with the items of a value as long."""
    _record(found, target, Assignment(kind, value, element))


def _record(found: dict[ast.AST, Assignment], target: ast.expr, assignment: Assignment) -> None:
    found[target] = assignment
    if isinstance(target, ast.Starred):
        _record(found, target.value, assignment)
    elif isinstance(target, (ast.Tuple, ast.List)):
        element = assignment.element
        same = isinstance(element, (ast.Tuple, ast.List)) and len(element.elts) == len(target.elts)
        parts = element.elts if same else [element] * len(target.elts)
        star = next(
            (index for index, item in enumerate(target.elts) if isinstance(item, ast.Starred)), len(target.elts)
        )
        for index, (item, part) in enumerate(zip(target.elts, parts, strict=True)):
            at = index - len(target.elts) if index > star else index
            _record(found, item, replace(assignment, element=part, position=(*assignment.position, at)))


def string_type(node: ast.Constant) -> ast.expr | None:
    """Return the expression a string reads as when it is evaluated as a type; None where nothing parses it.

    It is parsed as `typing` compiles a forward reference (leading whitespace does not parse), except that a character
    the running Python's Unicode tables do not assign is read as a letter, so a name of a newer Python's identifiers
    parses on every version. `SourceModule.type_positions` reads the names of a string in a type position from it.
    """
    _ask("references")
    return _string_expression(node)


def _string_expression(node: ast.Constant) -> ast.expr | None:
    text = "".join("x" if unicodedata.category(character) == "Cn" else character for character in str(node.value))
    try:
        return ast.parse(text, mode="eval").body
    except SyntaxError:
        return None


def name_reads(node: ast.expr) -> list[ast.Name]:
    """Return the names an expression outside a module's tree reads (one `string_type` parsed), in walk order.

    Resolve each with `SourceModule.lookup` in the scope the expression is evaluated in.
    """
    _ask("references")
    return [part for part in ast.walk(node) if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Load)]


def _receiver_use(parent: ast.Subscript | ast.Attribute, grandparent: ast.AST | None) -> str:
    """Return the use of a value a subscript or an attribute is taken on: a store, a delete, a method call or a read."""
    if not isinstance(parent.ctx, ast.Load):
        kind = "subscript" if isinstance(parent, ast.Subscript) else "attribute"
        return f"{kind}-{'del' if isinstance(parent.ctx, ast.Del) else 'store'}"
    called = isinstance(parent, ast.Attribute) and isinstance(grandparent, ast.Call) and grandparent.func is parent
    return f"call:{parent.attr}" if isinstance(parent, ast.Attribute) and called else "read"


def _position_use(node: ast.expr, parent: ast.AST | None, grandparent: ast.AST | None) -> str:
    """Return the use of a value by where it stands: callee, argument, ``*``/``**``, iterable or augmented target."""
    if isinstance(parent, ast.Call):
        return "call" if parent.func is node else "argument"
    if isinstance(parent, ast.keyword):
        return "unpack" if parent.arg is None else "argument" if isinstance(grandparent, ast.Call) else "read"
    spread = isinstance(parent, ast.Starred) or (
        isinstance(parent, ast.Dict)
        and any(key is None and value is node for key, value in zip(parent.keys, parent.values, strict=True))
    )
    kinds = {
        "augassign": isinstance(parent, ast.AugAssign) and parent.target is node,
        "iterate": isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)) and parent.iter is node,
        "unpack": spread,
    }
    return next((kind for kind, hit in kinds.items() if hit), "read")


def _mangle(name: str, scope: Scope) -> str:
    """Return the name the compiler stores: ``__x`` inside class ``C`` becomes ``_C__x`` (private name mangling)."""
    if not name.startswith("__") or name.endswith("__") or "." in name:
        return name
    probe: Scope | None = scope
    while probe is not None and probe.kind != "class":
        probe = probe.parent
    owner = probe.code_name.lstrip("_") if probe is not None else ""
    return f"_{owner}{name}" if owner else name


def _chain(node: ast.expr) -> tuple[ast.Name | None, list[str]]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return (node if isinstance(node, ast.Name) else None), parts[::-1]


def _first_line(node: ast.stmt) -> int:
    return min([node.lineno, *(decorator.lineno for decorator in getattr(node, "decorator_list", ()))])


def _parameters(arguments: ast.arguments) -> list[ast.arg]:
    return [
        *arguments.posonlyargs,
        *arguments.args,
        *([arguments.vararg] if arguments.vararg is not None else []),
        *arguments.kwonlyargs,
        *([arguments.kwarg] if arguments.kwarg is not None else []),
    ]


def _loaded_code(module: types.CodeType) -> dict[types.CodeType, dict[tuple[str, int, int], types.CodeType]]:
    """Return, for each code object, the code objects it loads by name and the position of the loading instruction.

    An ``__annotate__`` function (3.14+) gets no entry: the code objects it loads count as loaded by its own loader.
    """
    loads: dict[types.CodeType, dict[tuple[str, int, int], types.CodeType]] = {}
    todo = [module]
    while todo:
        owner = todo.pop()
        loaded = loads[owner] = {}
        readers = [owner]
        while readers:
            for child, line, column in _code_loads(readers.pop()):
                if child.co_name == "__annotate__":
                    readers.append(child)
                else:
                    loaded[child.co_name, line, column] = child
                    todo.append(child)
    return loads


def _code_loads(code: types.CodeType) -> Iterator[tuple[types.CodeType, int, int]]:
    """Yield the code objects a code object loads (``LOAD_CONST``) with the line and column of the loading instruction.

    Reads ``co_code`` one code unit at a time beside ``co_positions()`` (one position per unit); the same pairs as
    `dis.get_instructions`, at a tenth of its cost.
    """
    raw, constants = code.co_code, code.co_consts
    extended = 0
    for index, (line, _, column, _) in enumerate(code.co_positions()):
        operation, argument = raw[2 * index], raw[2 * index + 1] | extended
        extended = argument << 8 if operation == dis.EXTENDED_ARG else 0
        if operation == _LOAD_CONST and isinstance(child := constants[argument], types.CodeType):
            yield child, line or 0, column or 0


def _inlined(scope: Scope) -> list[Scope]:
    """Return the scope and the comprehensions inlined into its symbol table on the running Python (PEP 709)."""
    group = [scope]
    version = (sys.version_info.major, sys.version_info.minor)
    index = 0
    while index < len(group):
        group.extend(child for child in group[index].children if child.inlined_on(version))
        index += 1
    return group


def _statements_within(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Yield the statements of a body and of its compound statements, without entering definitions."""
    todo: list[ast.AST] = list(body)
    while todo:
        node = todo.pop()
        if isinstance(node, ast.stmt):
            yield node
        if not isinstance(node, _DEFINITIONS):
            todo.extend(
                child
                for child in ast.iter_child_nodes(node)
                if isinstance(child, (ast.stmt, ast.excepthandler, ast.match_case))
            )


def _string(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value else None


def _count(node: ast.expr) -> int | None:
    value = node.value if isinstance(node, ast.Constant) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _codes(text: str | None) -> tuple[str, ...] | None:
    return tuple(re.split(r"[\s,]+", text.upper())) if text else None


def _built(build: Callable[..., object], items: object) -> object:
    try:
        return build(items)
    except TypeError:
        raise _NotLiteralError(_UNHASHABLE) from None
    except ValueError:
        raise _NotLiteralError(_MALFORMED) from None


def _implicit(name: str) -> bool:
    return name.startswith(".") or name in _IMPLICIT


def _table_kind(table: symtable.SymbolTable) -> str | None:
    kind = _TABLE_KINDS.get(str(table.get_type()))
    name = table.get_name().strip("<>")
    if kind != "function":
        return kind
    if name == "lambda":
        return "lambda"
    comprehension = name in {"genexpr", "listcomp", "setcomp", "dictcomp"} and ".0" in table.get_identifiers()
    return "comprehension" if comprehension else "function"


def _table_names(table: symtable.SymbolTable) -> frozenset[str]:
    return frozenset(
        symbol.get_name() for symbol in table.get_symbols() if symbol.is_local() and not _implicit(symbol.get_name())
    )


def _path_parts(path: Path) -> tuple[str, ...]:
    """Sort key of a file: its parts as written (a Windows path sorts case-insensitively, ``Models`` after ``_x``)."""
    return path.parts


class SourceTree:
    """The modules of packages under a root, read on first use, for the questions that cross modules."""

    def __init__(
        self, root: Path, packages: Iterable[str], *, verify: bool | None = None, files: Container[str] | None = None
    ) -> None:
        """Index the ``.py`` files of each package directory under ``root`` by dotted module name.

        ``files`` limits the tree to the files whose path relative to ``root`` (``/``-separated) it holds.
        """
        self.root = root
        self.verify = verify
        self.paths: dict[str, Path] = {}
        for package in packages:
            for path in sorted((root / package).rglob("*.py"), key=_path_parts):
                if files is not None and path.relative_to(root).as_posix() not in files:
                    continue
                parts = path.relative_to(root).with_suffix("").parts
                self.paths[".".join(parts[:-1] if parts[-1] == "__init__" else parts)] = path
        self._modules: dict[str, SourceModule] = {}

    def module(self, name: str) -> SourceModule:
        """Return the model of a module of the tree."""
        if name not in self._modules:
            path = self.paths[name]
            relative = path.relative_to(self.root).as_posix()
            self._modules[name] = SourceModule(path.read_text(encoding="utf-8"), relative, name, verify=self.verify)
        return self._modules[name]

    def definition(self, module: str, name: str) -> list[Definition]:
        """Follow a module-level name of a module of the tree through imports and simple aliases to its definitions.

        ``from pkg import tool`` reads what ``pkg`` binds as ``tool`` and the submodule ``pkg.tool`` only where ``pkg``
        binds no such name, as the import system does.
        """
        _ask("imports")
        return self._definition(module, name, (), frozenset())

    def _definition(
        self, module: str, name: str, path: tuple[tuple[str, Guard], ...], seen: frozenset[tuple[str, str]]
    ) -> list[Definition]:
        if (module, name) in seen:
            return [Definition(module, name, 0, "cycle", path)]
        model = self.module(module)
        bindings = model.module_scope.bindings.get(name, [])
        submodule = f"{module}.{name}"
        found = (
            [] if bindings else [Definition(submodule, "", 0, "module" if submodule in self.paths else "unbound", path)]
        )
        for binding in bindings:
            hop = (*path, (module, binding.guard))
            value = binding.value if binding.kind in {"assign", "annassign"} else None
            alias = isinstance(value, (ast.Name, ast.Attribute))
            target = model.origin(value) if alias and isinstance(value, ast.expr) else binding.origin
            if binding.kind in {"import", "import-from"} or alias:
                found += self._follow(target, binding, hop, seen | {(module, name)})
            else:
                found.append(Definition(module, name, binding.line, binding.kind, hop))
        return found

    def _follow(
        self,
        target: str | None,
        binding: Binding,
        path: tuple[tuple[str, Guard], ...],
        seen: frozenset[tuple[str, str]],
    ) -> list[Definition]:
        if target is None:
            return [Definition(path[-1][0], binding.name, binding.line, "unreadable", path)]
        if binding.kind == "import-from" and self._source_binds(path[-1][0], target):
            source, _, attribute = target.rpartition(".")
            return self._definition(source, attribute, path, seen)
        parts = target.split(".")
        prefix = next((count for count in range(len(parts), 0, -1) if ".".join(parts[:count]) in self.paths), 0)
        module, rest = ".".join(parts[:prefix]), parts[prefix:]
        if not prefix:
            return [Definition(target, "", binding.line, "outside", path)]
        if len(rest) != 1:
            kind = "attribute" if rest else "module"
            return [Definition(module, ".".join(rest), binding.line, kind, path)]
        return self._definition(module, rest[0], path, seen)

    def _source_binds(self, importer: str, target: str) -> bool:
        """Whether ``from <source> import <name>`` (``target``) in ``importer`` reads a name the source module binds.

        Then the name leads to that binding, not to a submodule ``<source>.<name>``; a package importing from itself
        (``from . import tool`` in its ``__init__``) reads the submodule. `import_edges` keeps the submodule edge: the
        import may still load it where the binding is guarded or is that submodule.
        """
        source, _, name = target.rpartition(".")
        return source in self.paths and source != importer and name in self.module(source).module_scope.bindings

    def import_edges(self) -> list[Edge]:
        """Return the import edges between the tree's modules (parents, submodules, literal dynamic imports)."""
        _ask("imports")
        edges: list[Edge] = []
        for importer in self.paths:
            for item in self.module(importer).imports:
                source = item.source if isinstance(item.source, str) else ""
                level = item.scope.runs_in
                parts = source.split(".")
                via = {"import": "import", "dynamic": "dynamic-literal"}.get(item.kind, "from")
                found = [(".".join(parts[:count]), "parent") for count in range(1, len(parts))] + [(source, via)]
                found += [(item.target, "submodule")] if item.kind == "from" and item.target in self.paths else []
                edges.extend(
                    Edge(importer, str(imported), item.node.lineno, item.guard, level, kind)
                    for imported, kind in found
                    if imported in self.paths and imported != importer and not importer.startswith(f"{imported}.")
                )
        return edges
