"""Build scripts/heki.toml, the heki contracts of the package, from the records the repository keeps.

Usage:
    python scripts/build_heki_contracts.py
    python scripts/build_heki_contracts.py --check

heki (a development dependency) checks the contracts statically: ``heki check --config scripts/heki.toml --baseline
scripts/heki_baseline.toml src``. Every contract comes from a record that a check of the repository already reads,
so the file is never edited by hand. The contracts of homes, builders and tables judge each call or read in the scope
that holds it (``attribution = "lexical"``), as the guards of those records do:

- ``write-owners`` (``MUT201``, ``MUT205``): the ``fact``, ``presentation`` and ``cache`` rows of
  ``scripts/fact_tables.py`` outside the parser run state, and the run-state rows that name store operations (the
  model lists ``Parser.results`` and ``ModuleContext.models``), one contract per kind and set of store operations; the
  operations and the generation store module may write the attributes, and each write is judged in the scope that
  holds it. A ``__dict__`` key row names the value of its constant, and the cache contract forbids a
  ``cached_property`` on every owner of a row outside the parser run state and the classes deriving from them, as a
  cached property writes its own name into ``__dict__``: the baseline keeps today's, which cache rows name. Each of
  these contracts reports a write of a ``__dict__`` key that no row names (``unlisted-dict-keys``), so a new key
  needs its row first; the ``bookkeeping`` contract names the bookkeeping rows, which any function may write. A row
  whose attribute or key the package no longer has is an error (``unmatched-contract-entries``).
- ``call-owners`` (``MUT204``, ``MUT207``) of the store operations: each operation that
  ``GENERATION_STORE_MUTATION_METHODS`` names is called only in the store module, where ``GraphBuilder`` calls it for
  the passes, and only the store module makes a ``GraphBuilder``.
- ``call-owners``, ``read-owners`` and ``write-owners`` of the members of the graph only the store may use
  (``STORE_MEMBERS`` of ``scripts/fact_tables.py``): each method that writes the graph for the store operations that
  run it is callable by the store module, its own class (through its own receiver too) and the other such methods,
  and may write whatever the store module may write, in every override; a call on a receiver heki cannot tie to a
  class is reported (``MUT207``), as untyped callbacks reach these methods so; the bookkeeping attributes of a
  reference are read or written only by the store module, their class's module and the modules their row names.
- ``call-owners`` (``MUT204``): the def and class homes of ``scripts/decision_homes.py``
  (`scripts._decision_home_reads.checked_homes`), read at run time (called, subclassed, handed on or read as a value:
  ``references = "reads"``; an annotation and a name imported only under ``if TYPE_CHECKING:`` read none, and a name
  bound to the home by itself counts only where it is read by name, not through ``self``, ``cls`` or its class) only by
  the home modules and the adapter sites of their rows, where a class site's own body counts as the code around it; the
  parse methods that dispatch tables hold, callable only where the tables are built; and the outputs that builder
  records mark (a call of a class or function, binding a parameter to a constant where the mark asks for one: an
  argument-list target such as ``DataType(is_dict=True)``), built only by the builder and its parts, and under the
  record's ``within`` directory by no other function. A table, parse method or mark that is a member of a class is
  also named as the member of every Protocol of the package that declares it, since heki judges a use through a value
  typed with a host Protocol by the Protocol's declaration.
- ``readonly-returns`` (``MUT202``): the schema objects and loaded documents that the schema graph validates once and
  gives to every reader (``SchemaGraphBuilder.schema``, ``SchemaGraphBuilder.pointer_value``,
  ``prepare_schema_resources``) and the schema sites the OpenAPI parser keeps (``ApiOpenAPIParser._site_facts``), deep:
  no function writes an object they return, or a part of it, directly or through the functions that return it.
- ``effect-boundaries`` (``EFF201``) and ``read-owners`` (``MUT203``, ``MUT206``) of the decisions: the modules of the
  decisions (``module`` of ``DECISION_DECLS`` in ``scripts/pass_decls.py``) read no render fact (an attribute or
  ``__dict__`` key of the graph whose ``ATTRIBUTE_FACTS`` row holds no ``Fact`` final at B1, or a dict attribute one of
  whose entries is one), also through the functions
  they call, outside the decision writer and the members ``DECISION_ACCESSORS`` lists, nor such an attribute on a value
  heki cannot tie to a class.
- ``call-owners`` (``MUT204``, ``MUT207``) of the decision writer: each method ``writer_methods`` of a decision's
  declaration names is called only in the module of that decision (``module``, which every declaration names), and
  every other public method only in the decision phase.
- ``call-owners`` (``MUT204``) of the import ledger: the methods that change an ``Imports`` (``IMPORTS_MUTATORS`` of
  ``scripts/fact_tables.py``) are called only in the ledger's module, the ``imports`` module and the defs
  ``LEDGER_WRITERS`` names.
- ``read-owners`` (``MUT203``): each recorded dispatch table, read only by its builder and by the scopes that only ask
  it for ``typed_children`` (`scripts._decision_home_reads.typed_children_readers`).
- ``read-owners``, ``call-owners`` and ``write-owners`` of the host rule (``MUT201``, ``MUT203``, ``MUT204``): the
  concern modules (`in_host_scope`: where pyproject.toml turns ruff's SLF001 off) reach a private member (one leading
  underscore, no dunder) of a class outside them, or of a Protocol inside them that is no ``*Host``, only through the
  receiver of the method that holds the access or a value typed with a ``*Host`` Protocol declaring the member
  (``MUT205``, ``MUT206``, ``MUT207`` report what heki cannot tie to a class). heki names no member of a module
  outside the package, so ruff's ``banned-api`` holds ``sys._getframe`` in these modules instead.
- ``call-owners`` and ``read-owners`` of the pass pipeline (``MUT203``, ``MUT204``): ``FinalFacts`` is built only by
  ``GenerationStore.seal_facts`` (a class attribute, a decorator or a default argument counts as a read), and a
  name-mangled member of ``Parser`` (``Parser._Parser__*``, which the pass tables call) is reached only inside the body
  of ``Parser``; a subclass reaches it through ``self`` too, and a receiver heki cannot tie to a class is reported
  (``unresolved = "report"``).
- ``IMP101``: no new import cycle of the package, and no module joins a cycle or merges two (``[imports] cycle-edges``
  names every kind: module and class level, in a function, lazy and under ``TYPE_CHECKING``). heki reports each group of
  modules that import each other once, its modules as the target, so the baseline holds today's groups and a changed
  group is a new report. heki reports it as a warning, so ``[lint] fail`` names it to fail the check. A cycle of
  the imports that run while their modules load inside a group adds no report: the ``heki`` lane refuses every such
  cycle with ``IMP101`` over those imports alone (``tests/test_build_heki_contracts_script.py``).
- ``import-boundaries`` (``IMP201``, ``IMP202``) of the layers of the package (`classify_source_path` of
  ``scripts/_package_layers.py``): each leaf layer imports only the standard library, ``typing_extensions`` and the
  modules `_LEAF_IMPORTS` lists, the runtime its third-party packages and its own modules only relatively; no module
  imports the package root or ``__main__``, the pass modules no parser module but `PARSER_PASS_IMPORTS`, the parser,
  configuration, shared model and reference code no concrete backend, configuration and the targets no parser module,
  the core no target and no code outside the parser a private parser module or a private name of one. Every import
  counts, under ``TYPE_CHECKING`` and by name too, and an import heki cannot name is reported (``IMP202``): a relative
  import that leaves the top-level package, and a call that may be ``importlib.import_module`` or ``__import__``. So
  the baseline counts the imports of modules only the run knows.
- ``read-owners``, ``call-owners`` and ``write-owners`` of the layers (`architecture_contracts`, ``MUT201``,
  ``MUT203``, ``MUT204``, ``MUT206``, ``MUT207``): what a layer reads of another one beyond its imports. Parser code
  reads no member only a backend reads, names no backend module and selects no output family; input model code
  selects none; shared model code reads no module of ``sys.modules``; configuration reads no backend name but the
  ``pydantic_v2`` ones of its module body (the defaults of ``ParserConfig``); the entry point reaches no private
  member of a parser; no module but the root and ``__main__`` reads a name through the root. The functions whose read
  of ``__module__`` or ``sys.modules`` asks something else, and the two private members the entry point reaches, are
  left out with their reasons (`MODULE_NAME_READERS`, `MODULE_REGISTRY_READERS`, `ENTRYPOINT_PRIVATE_READERS`,
  `CONFIG_DEFAULTS_BACKEND`). The checker's ``reference-backend-definition`` rule, a naming lint on the classes of
  reference code, has no contract (owner 2026-11-26): a use of a backend there is held by the import contracts.
- ``effect-boundaries`` (``EFF201``) of the passes, from ``scripts/pass_decls.py`` and the pipeline tables
  (``parser/_passes/_tables.py``): each pass after L1 and each decision, over the functions its step or its
  ``DecisionPass`` runs and those they call, writes only the fact and presentation rows its declaration lets it write
  in its phase (``may_write``: a fact it declares, not frozen at a freeze point the phase has passed, not after the
  row's ``final_after``), and never the phase of the store; an ``Access.ITEM`` row is its entry
  (``extras["init"]``), which heki follows through the key a store operation is given. The glue between the passes
  (the hooks of the tables and the parser methods they call, the runner, the decision phase) writes no fact row. The
  baseline keeps the findings ``KNOWN_FINDINGS`` lists, each with what removes it. The boundaries leave ``unresolved``
  at its default: most passes reach calls and writes heki cannot resolve, where a boundary reports nothing, so they
  check the declarations only through the calls heki follows, beside the finality check of the test suite
  (``tests/checks/finality.py``), which holds every rule.
- ``effect-boundaries`` (``EFF201``): no function or module body of the package reads the environment it runs in
  (the interpreter's version, the platform, a variable, an installed package), guarded imports included: output that
  depends on the environment cost review rounds.

Tables of one kind and family that differ only in their ``targets`` are written as one table (`group_contracts`): the
file holds one table per distinct setting, not one per target. A key with one value in most tables of a kind
is written once in the kind's defaults table (`kind_defaults`); a table that leaves the key out counts with the value
heki documents for it (`HEKI_KEY_DEFAULTS`).

Each effect boundary's report carries the help of its kind, ``BOUNDARY_ADVICE`` of ``scripts/check_ratchets.py``
(``ENVIRONMENT_ADVICE``, ``PASS_ADVICE``, ``GLUE_ADVICE``, ``DECISION_READ_ADVICE``): the ratchet key that counts
the baseline sites of these boundaries, one key per kind (``allowlist:heki-baseline:EFF201:<kind>``), prints the same
help for the kind whose sites rose.

The file also sets ``required-version`` to at least the heki that the ``heki`` dependency group of pyproject.toml pins,
so an older heki stops with a clear error instead of failing on a key it does not know.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import Counter, defaultdict
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple, get_args

import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._decision_home_reads import checked_homes, held_parse_methods, typed_children_readers
from scripts._package_layers import (
    CONCRETE_BACKEND_ROOTS,
    PARSER_LAYERS,
    PRIVATE_PARSER_IMPORT_MESSAGE,
    ROOT_IMPORT_MESSAGE,
    TARGET_MODULES,
    Layer,
    PackageIndex,
    classify_source_path,
    protocol_bases,
)
from scripts._source_model import bases
from scripts.check_generation_store_usage import GENERATION_STORE_MUTATION_METHODS, PARSER_STATE_OWNERS
from scripts.check_ratchets import DECISION_READ_ADVICE, ENVIRONMENT_ADVICE, GLUE_ADVICE, PASS_ADVICE
from scripts.decision_homes import BUILDER_HOMES, DECISION_HOMES, Home, Mark
from scripts.fact_tables import (
    ATTRIBUTE_FACTS,
    DECISION_ACCESSORS,
    IMPORTS_MUTATORS,
    LEDGER_WRITERS,
    STORE_MEMBERS,
    Access,
    AttributeFact,
    DecisionAccessor,
    Fact,
    FactKind,
    FreezePoint,
    LedgerWriter,
    MemberUse,
    Phase,
    RenderFact,
    StoreMember,
    freeze_point,
    holds_render_facts,
)
from scripts.pass_decls import DECISION_DECLS, PASS_DECLS
from scripts.where import ASSIGNS, PACKAGE, PACKAGE_DIR, Page, Repo

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from scripts._source_model import Scope, SourceModule
    from scripts.decision_homes import BuilderHome, DecisionHome
    from scripts.pass_decls import PassDecl

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "scripts" / "heki.toml"
PYPROJECT = ROOT / "pyproject.toml"
SOURCE_ROOT = ROOT / PACKAGE_DIR
STORE_MODULE = f"{PACKAGE}.parser.generation"
"""The generation store's module: its functions write the facts the store operations write."""
GRAPH_BUILDER = f"{STORE_MODULE}.GraphBuilder"
"""The capability to change the graph, which only the store makes: every store operation is called through it."""
DECISION_WRITER = f"{PACKAGE}.parser._phases._DecisionWriter"
"""The one writer of the decisions (``parser/_phases.py``), over the store: what a decision calls to write."""
DECISION_PHASE = "parser/_passes/decide.py"
"""The decision phase, which runs the decisions with the one decision writer and reads what they changed."""
IMPORTS = f"{PACKAGE}.imports.Imports"
"""The imports of a generated module (``imports.py``), which the import ledger writes."""
IMPORT_LEDGER = "parser/_imports_plan.py"
"""The module of the import ledger (``ModuleImportLedger``), the one writer of the imports of a module."""
WRITE_KINDS = (FactKind.FACT, FactKind.PRESENTATION, FactKind.CACHE)
RULES = (
    "EFF109",
    "EFF116",
    "EFF201",
    "IMP101",
    "IMP201",
    "IMP202",
    "MUT201",
    "MUT202",
    "MUT203",
    "MUT204",
    "MUT205",
    "MUT206",
    "MUT207",
)
FAIL = ("IMP101",)
"""The rules whose reports fail the check whatever their tier (``[lint] fail``): ``IMP101`` reports a cycle as a
warning, and the contracts fail through their own ``fail = true`` (`failing`)."""
UNMATCHED_ENTRIES = "error"
"""What heki does with a contract entry that names nothing in src: an error, so a row whose attribute or key the code no
longer has fails the check, in every run, also one over part of the package."""
SHARED_PRODUCERS = (
    ("parser/_schema_graph.py", "SchemaGraphBuilder.schema"),
    ("parser/_schema_graph.py", "SchemaGraphBuilder.pointer_value"),
    ("parser/_schema_documents.py", "prepare_schema_resources"),
    ("parser/openapi_scope.py", "ApiOpenAPIParser._site_facts"),
)
"""The functions that give the readers of a parse an object they share: the path of their module and their name."""
IMPORT_EDGES = ("body", "lazy", "function", "type-checking")
"""The imports that close an ``IMP101`` cycle: every import, those in a function and under ``TYPE_CHECKING`` too."""
HEADER = """\
# Generated by scripts/build_heki_contracts.py from the records of the repository; do not edit by hand.
# Check: heki check --config scripts/heki.toml --baseline scripts/heki_baseline.toml src
"""


def _quote(text: str) -> str:
    """Return a TOML string of a text, in single quotes where it holds a double quote."""
    return f"'{text}'" if '"' in text else f'"{text}"'


def _array(items: Iterable[str]) -> str:
    """Return a TOML array of strings."""
    return "[" + ", ".join(_quote(item) for item in items) + "]"


def _value(value: object) -> str:
    """Return a TOML boolean, string or array of strings."""
    match value:
        case bool():
            return str(value).lower()
        case str():
            return _quote(value)
        case list() | tuple():
            return _array(value)
    message = f"no TOML form for {value!r}"
    raise TypeError(message)


def _table(kind: str, **keys: object) -> str:
    """Return one contract: an array-of-tables entry with its keys in the order given."""
    return "\n".join([f"[[{kind}]]", *(f"{key.replace('_', '-')} = {_value(v)}" for key, v in keys.items())]) + "\n"


_IDENTITY = frozenset({"name", "targets"})
"""The keys of a table that name it and say what it judges: tables equal in every other key can share them."""


def group_contracts(contracts: Iterable[str]) -> list[str]:
    """Return the contracts with the tables that differ only in their targets written as one table.

    A table with ``targets`` joins the tables of its kind and family (the name up to the first colon) that hold every
    other key but ``name`` equal, in any key order, and so allows and forbids exactly what they did: heki checks each
    target of a table by itself, and a group of ``call-owners`` tables sets ``targets-call-each-other = "report"``, so
    a call of one of its targets by another is judged as the separate tables judged it. The group stands where its
    first table stood and lists the targets of its tables in the order they were given, each once; its name is the
    name of its first table and ``+<n>`` for the n tables that joined it, so the name stays unique and its family
    stays the prefix a reader searches for. A table without ``targets`` and a table no other joins is written as it
    was given.
    """
    groups: dict[object, list[tuple[str, str, Mapping[str, object], list[str]]]] = {}
    for text in contracts:
        ((kind, (table,)),) = tomllib.loads(text).items()
        settings = tuple(
            sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in table.items() if k not in _IDENTITY)
        )
        key = (kind, table["name"].partition(":")[0], settings) if "targets" in table else object()
        groups.setdefault(key, []).append((text, kind, table, table.get("targets", [])))
    return [
        members[0][0]
        if len(members) == 1
        else _table(
            members[0][1],
            name=f"{members[0][2]['name']} +{len(members) - 1}",
            targets=list(dict.fromkeys(target for *_, targets in members for target in targets)),
            **{key: value for key, value in members[0][2].items() if key not in _IDENTITY},
            **({"targets_call_each_other": "report"} if members[0][1] == "call-owners" else {}),
        )
        for members in groups.values()
    ]


def _hashable(value: object) -> object:
    """Return the value of a table key in a form that can be counted and compared: a list as a tuple."""
    return tuple(value) if isinstance(value, list) else value


HEKI_KEY_DEFAULTS: Mapping[str, Mapping[str, object]] = {
    "write-owners": {
        "fresh-objects": "allow",
        "unresolved": "ignore",
        "attribution": "call-graph",
        "own-receiver": "report",
    },
    "read-owners": {"unresolved": "ignore", "attribution": "call-graph", "own-receiver": "report"},
    "call-owners": {
        "subclasses": True,
        "unresolved": "ignore",
        "references": "ignore",
        "attribution": "call-graph",
        "own-receiver": "report",
        "targets-call-each-other": "allow",
    },
    "effect-boundaries": {
        "write-universe": [],
        "allow-writes": [],
        "fresh-objects": "allow",
        "references": "ignore",
        "import-probes": "allow",
        "unresolved": "ignore",
    },
    "import-boundaries": {"type-checking": "ignore", "reach": "statement", "unresolved": "ignore"},
}
"""The value heki takes for a key a table of a kind leaves out, for the keys whose default is a value and not a choice
of what to judge (heki 0.9.0, the "Default" column of the key tables of each kind on
https://heki.koxudaxi.dev/configuration/: write-owners, read-owners, call-owners, effect-boundaries, import-boundaries).
The installed package carries no configuration text, so the page is the one source; ``message``, ``fail`` and the keys
that select what a table judges (targets, scopes, importers, allowed-*, forbidden-*, forbid, read-targets, ...) are not
listed, since leaving one out is not a value another table could share. ``kind_defaults`` writes a key a table leaves
out as heki's value where the kind's defaults table gives the key another one."""


_MISSING = object()
"""The marker of a key without a default to compare with."""


def _effective(kind: str, table: Mapping[str, object], key: str) -> object:
    """Return the value heki reads for a key of a table: its own, else the documented default of the key."""
    return table[key] if key in table else HEKI_KEY_DEFAULTS.get(kind, {}).get(key)


def kind_defaults(contracts: Iterable[str]) -> tuple[list[str], list[str]]:
    """Return the defaults table of each contract kind and the contracts written without the keys it sets.

    A key goes into ``[<kind>-defaults]`` with the value more than half of the kind's tables give it, where the kind
    has two tables or more, and every table of the kind sets the key or leaves it out for a value heki documents in
    ``HEKI_KEY_DEFAULTS``; the tables that leave the key out count with heki's value, and name, targets and the keys
    without a documented default stay out (heki refuses the first two in a defaults table). A table keeps a key only
    where its value differs from the one it reads otherwise: its own key overrides the default and a list replaces it
    whole, so a table that left the key out is written with heki's value where the defaults table gives another. Where
    the common value is heki's own, the defaults table would say nothing, so it is left out and the tables drop the
    keys that state heki's value.
    """
    tables = [next(iter(tomllib.loads(text).items())) for text in contracts]
    members: dict[str, list[dict[str, object]]] = defaultdict(list)
    for kind, (table,) in tables:
        members[kind].append(table)
    defaults: dict[str, dict[str, object]] = {}
    for kind, kind_tables in members.items():
        documented = HEKI_KEY_DEFAULTS.get(kind, {})
        keys = dict.fromkeys(key for table in kind_tables for key in table if key not in _IDENTITY)
        for key in [*keys, *(key for key in documented if key not in keys)]:
            if not all(key in table or key in documented for table in kind_tables):
                continue
            ((value, count),) = Counter(_hashable(_effective(kind, table, key)) for table in kind_tables).most_common(1)
            if 2 * count > len(kind_tables) > 1 and not (key in documented and value == _hashable(documented[key])):
                defaults.setdefault(kind, {})[key] = next(
                    _effective(kind, t, key) for t in kind_tables if _hashable(_effective(kind, t, key)) == value
                )
    written: list[str] = []
    for kind, (table,) in tables:
        kind_values = defaults.get(kind, {})
        documented = HEKI_KEY_DEFAULTS.get(kind, {})
        keys = {key: value for key, value in table.items() if not _same(kind_values, documented, key, value)}
        keys.update({
            key: documented[key]
            for key in kind_values
            if key not in table and key in documented and _hashable(documented[key]) != _hashable(kind_values[key])
        })
        written.append(_table(kind, **keys))
    return [
        "\n".join([f"[{kind}-defaults]", *(f"{key} = {_value(value)}" for key, value in keys.items())]) + "\n"
        for kind, keys in defaults.items()
    ], written


def failing(contracts: Iterable[str]) -> list[str]:
    """Return the contracts, each with ``fail = true``.

    heki reports every finding of the package as heuristic, which fails a run only where its contract sets ``fail``
    (or ``[lint] fail`` names its rule): every contract here is a check the package keeps, so each one fails it.
    """
    return [contract.removesuffix("\n") + "\nfail = true\n" for contract in contracts]


def _same(defaults: Mapping[str, object], documented: Mapping[str, object], key: str, value: object) -> bool:
    """Return whether a table key states what its kind reads without it: the defaults table's value, else heki's."""
    read = defaults[key] if key in defaults else documented.get(key, _MISSING)
    return read is not _MISSING and _hashable(read) == _hashable(value)


def _module(path: str) -> str:
    """Return the dotted module of a path relative to the package directory."""
    parts = path.removesuffix(".py").split("/")
    return ".".join([PACKAGE, *parts[: -1 if parts[-1] == "__init__" else None]])


def _scope(module: str, name: str) -> list[str]:
    """Return the pattern of a scope of a module and the scopes inside it, or of the whole module without a name.

    ``{,.*}`` is a glob alternative that may be empty: ``m:f{,.*}`` names ``f`` and every scope nested in it.
    """
    return [f"{module}:{name}{{,.*}}"] if name else [f"{module}:*"]


def _package_modules(repo: Repo) -> dict[str, str]:
    """Return the dotted module of each module path of the package (relative to the repository)."""
    return {rel: name for rel, (_, name) in repo.names.items() if rel.startswith(PACKAGE_DIR)}


def _constant_values(repo: Repo, name: str) -> set[str]:
    """Return the strings a name is bound to in the package, at module or class level, through aliases of constants."""
    modules = _package_modules(repo)
    paths = {module: rel for rel, module in modules.items()}
    values: set[str] = set()
    todo = [(rel, name) for rel in modules if name in repo.text(rel)]
    seen: set[tuple[str, str]] = set()
    while todo:
        rel, wanted = todo.pop()
        if (rel, wanted) in seen:
            continue
        seen.add((rel, wanted))
        model = repo.model(rel)
        for scope in model.scopes:
            for binding in scope.bindings.get(wanted, []) if scope.kind in {"module", "class"} else []:
                value = binding.value if binding.kind in ASSIGNS else None
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    values.add(value.value)
                elif value is not None and (origin := model.origin(value)):
                    module, _, leaf = origin.rpartition(".")
                    todo += [(paths[module], leaf)] if module in paths else []
    return values


def _attribute(repo: Repo, attribute: str, access: Access) -> str:
    """Return the attribute a fact row names as heki names it: a ``__dict__`` key constant by its value."""
    if access is not Access.DICT_KEY or not attribute.lstrip("_").isupper():
        return attribute
    if len(values := _constant_values(repo, attribute)) != 1:
        message = f"{attribute}: a __dict__ key constant must be bound to one string in the package, found {values}"
        raise SystemExit(message)
    return values.pop()


def _container(row: AttributeFact) -> str:
    """Return the dict attribute an ``Access.ITEM`` row names an entry of (``extras`` of ``extras["kw_only"]``)."""
    return row.attribute.partition("[")[0]


def _package_functions(repo: Repo) -> set[str]:
    """Return the dotted name of every function and method the package defines."""
    return {
        f"{module}.{scope.qualname}"
        for rel, module in _package_modules(repo).items()
        for scope in repo.model(rel).scopes
        if scope.kind == "function"
    }


class _Classes:
    """The classes of the package by dotted name, each with its bases read by origin, and its functions."""

    def __init__(self, repo: Repo) -> None:
        """Read the class scopes of the package's modules."""
        index = PackageIndex(repo.root / PACKAGE_DIR)
        self.modules: dict[str, list[str]] = {}
        """The classes by the module that defines them, as ``module`` and ``qualname`` joined by a dot."""
        self.bases: dict[str, list[str]] = {}
        self._scopes: dict[str, tuple[SourceModule, Scope]] = {}
        for rel, module in _package_modules(repo).items():
            model = repo.model(rel)
            for scope in model.scopes:
                if scope.kind == "class" and "<locals>" not in scope.qualname:
                    self.modules.setdefault(module, []).append(scope.qualname)
                    self._scopes[f"{module}.{scope.qualname}"] = (model, scope)
                    self.bases[f"{module}.{scope.qualname}"] = [
                        index.resolve(o) for base in bases(scope) if (o := model.origin(base))
                    ]
        self.functions = _package_functions(repo)

    def lineage(self, name: str) -> list[str]:
        """Return the class ``name`` and every class it derives from, through bases read by origin."""
        todo, seen = [name], []
        while todo:
            if (current := todo.pop()) not in seen:
                seen.append(current)
                todo += self.bases.get(current, [])
        return seen

    def derives(self, name: str, root: str) -> bool:
        """Return whether the class ``name`` is ``root`` or derives from it."""
        return root in self.lineage(name)

    def definers(self, root: str, name: str) -> list[str]:
        """Return the scopes of the methods ``name`` that ``root`` and the classes deriving from it define."""
        return sorted(
            f"{module}:{qualname}.{name}"
            for module, qualnames in self.modules.items()
            for qualname in qualnames
            if f"{module}.{qualname}.{name}" in self.functions and self.derives(f"{module}.{qualname}", root)
        )


def _methods(
    repo: Repo, members: Iterable[StoreMember | DecisionAccessor], table: str
) -> dict[StoreMember | DecisionAccessor, list[str]]:
    """Return each row of a table that names a method, with the scopes of the method and of its overrides.

    An override is a method of that name in a class of the package that derives from the row's class, through bases
    read by origin.
    """
    classes = _Classes(repo)
    methods: dict[StoreMember | DecisionAccessor, list[str]] = {}
    for member in members:
        owner = f"{_module(member.path)}.{member.owner}"
        if f"{owner}.{member.name}" not in classes.functions:
            message = f"{table}: {member.path} defines no method {member.owner}.{member.name}"
            raise SystemExit(message)
        methods[member] = classes.definers(owner, member.name)
    return methods


def _store_methods(
    repo: Repo, members: Iterable[StoreMember] | None = None
) -> dict[StoreMember | DecisionAccessor, list[str]]:
    """Return the ``CALL`` rows of ``members``, each with the scopes of the method and of its overrides.

    ``members`` defaults to ``STORE_MEMBERS``, read when the function runs.
    """
    rows = STORE_MEMBERS if members is None else members
    return _methods(repo, (member for member in rows if member.use is MemberUse.CALL), "STORE_MEMBERS")


def _store_hooks(repo: Repo, members: Iterable[StoreMember] | None = None) -> list[str]:
    """Return the scopes of the methods that write the graph for the store operations, overrides included."""
    return sorted({scope for scopes in _store_methods(repo, members).values() for scope in scopes})


def write_owner_contracts(
    repo: Repo, rows: Iterable[AttributeFact] = ATTRIBUTE_FACTS, members: Iterable[StoreMember] | None = None
) -> list[str]:
    """Return the ``write-owners`` contracts of the fact rows.

    The cache contract forbids a ``cached_property`` on every owner of a row outside the parser run state and on the
    classes deriving from one: a cached property writes its own name into ``__dict__``, outside the store. Each
    contract reports a write of a ``__dict__`` key no row names; the last one, ``bookkeeping``, names the bookkeeping
    rows and lets every function write them.
    """
    hooks = _store_hooks(repo, members)
    table = tuple(rows)
    groups: dict[tuple[str, tuple[str, ...]], list[str]] = defaultdict(list)
    for row in table:
        if row.kind not in WRITE_KINDS or (row.owner in PARSER_STATE_OWNERS and not row.store_ops):
            continue
        groups[row.kind.value, tuple(sorted(row.store_ops))].append(
            f"{row.owner}.{_attribute(repo, row.attribute, row.access)}"
        )
    described = sorted({row.owner for row in table if row.owner not in PARSER_STATE_OWNERS})
    contracts: list[str] = []
    for (kind, operations), targets in sorted(groups.items()):
        cached = described if kind == FactKind.CACHE.value else []
        contracts.append(
            _table(
                "write-owners",
                name=f"{kind}:{'|'.join(operations) or 'no store operation'}",
                targets=sorted(targets),
                allowed_writers=[*(f"GenerationStore.{op}" for op in operations), *_scope(STORE_MODULE, ""), *hooks],
                **({"forbid_cached_property_on": cached} if cached else {}),
                unresolved="report",
                fresh_objects="report",
                attribution="lexical",
                unlisted_dict_keys="report",
            )
        )
    bookkeeping = {
        f"{row.owner}.{_attribute(repo, row.attribute, row.access)}"
        for row in table
        if row.kind is FactKind.BOOKKEEPING
    }
    contracts.append(_table("write-owners", name="bookkeeping", targets=sorted(bookkeeping), allowed_writers=["*"]))
    return contracts


def store_member_contracts(repo: Repo) -> list[str]:
    """Return the contracts of ``STORE_MEMBERS``: who may call each method, and read or write each attribute."""
    methods = _store_methods(repo)
    hooks = sorted({scope for scopes in methods.values() for scope in scopes})
    bookkeeping = {(row.owner, row.attribute) for row in ATTRIBUTE_FACTS if row.kind is FactKind.BOOKKEEPING}
    contracts: list[str] = []
    for member in STORE_MEMBERS:
        module = _module(member.path)
        owners = sorted({
            *_scope(STORE_MODULE, ""),
            *_scope(module, member.owner if member.use is MemberUse.CALL else ""),
            *(pattern for home in member.homes for pattern in _scope(_module(home), "")),
            *hooks,
        })
        name = f"store-{member.use.value}:{member.path}:{member.owner}.{member.name}"
        if member.use is MemberUse.CALL:
            contracts.append(
                _table(
                    "call-owners",
                    name=name,
                    targets=[f"{module}.{member.owner}.{member.name}"],
                    allowed_callers=owners,
                    references="calls",
                    own_receiver="allow",
                    unresolved="report",
                    attribution="lexical",
                )
            )
            continue
        if (member.owner, member.name) not in bookkeeping:
            message = f"STORE_MEMBERS: {member.owner}.{member.name} has no bookkeeping row in ATTRIBUTE_FACTS"
            raise SystemExit(message)
        kind, allowed = (
            ("read-owners", "allowed_readers") if member.use is MemberUse.READ else ("write-owners", "allowed_writers")
        )
        contracts.append(
            _table(
                kind, name=name, targets=[f"{member.owner}.{member.name}"], **{allowed: owners}, attribution="lexical"
            )
        )
    return contracts


def decision_home_contracts(repo: Repo, records: Iterable[DecisionHome] = DECISION_HOMES) -> list[str]:
    """Return a ``call-owners`` contract per def or class home of records that `checked_homes` returns.

    A run-time read of the home outside the home modules and the adapter sites of every row that holds it is a
    finding, in the scope that makes it.
    """
    contracts: dict[Home, str] = {}
    for home, _, modules, sites in checked_homes(repo, records):
        allowed = {f"{_module(module)}:*" for module in modules}
        allowed.update(pattern for site in sites for pattern in _scope(_module(site.path), site.name))
        contracts[home] = _table(
            "call-owners",
            name=f"home:{home.path}:{home.name}",
            targets=[f"{_module(home.path)}.{home.name}"],
            allowed_callers=sorted(allowed),
            references="reads",
            subclasses=False,
            attribution="lexical",
        )
    return [contracts[home] for home in sorted(contracts)]


def decision_writer_contracts(repo: Repo, decls: Mapping[str, PassDecl] = DECISION_DECLS) -> list[str]:
    """Return a ``call-owners`` contract per method of the decision writer: only the module of its decision calls it.

    ``writer_methods`` of ``DECISION_DECLS`` names the methods of ``_DecisionWriter`` each decision writes through, and
    ``module`` the module of the decision. A public method no decision names belongs to the decision phase
    (``DECISION_PHASE``), which runs the decisions and reads what they changed. A call from another module, also
    through the functions it calls, or on a receiver heki cannot tie to a class is a finding.
    """
    module, _, owner = DECISION_WRITER.rpartition(".")
    if empty := [decl.name for decl in decls.values() if not (decl.module and decl.writer_methods)]:
        message = f"DECISION_DECLS: {', '.join(sorted(empty))} names no module or no write (module, writer_methods)"
        raise SystemExit(message)
    callers = [
        (method, decl.module)
        for decl in sorted(decls.values(), key=lambda decl: decl.name)
        for method in decl.writer_methods
    ]
    prefix = f"{DECISION_WRITER}."
    claimed = {method for method, _ in callers}
    callers += [
        (method, DECISION_PHASE)
        for method in sorted(name.removeprefix(prefix) for name in _package_functions(repo) if name.startswith(prefix))
        if not method.startswith("_") and "." not in method and method not in claimed
    ]
    return [
        _table(
            "call-owners",
            name=f"decision-writer:{method}",
            targets=[f"{module}:{owner}.{method}"],
            allowed_callers=_scope(_module(path), ""),
            attribution="call-graph",
            unresolved="report",
        )
        for method, path in callers
    ]


def decision_contracts(
    repo: Repo,
    decls: Mapping[str, PassDecl] = DECISION_DECLS,
    accessors: Iterable[DecisionAccessor] = DECISION_ACCESSORS,
    rows: Iterable[AttributeFact] = ATTRIBUTE_FACTS,
) -> list[str]:
    """Return the contracts that hold the decisions to the facts final at B1: they read no render fact.

    A render fact is an attribute or ``__dict__`` key of a graph class whose row holds facts none of which is a
    ``Fact`` final at B1 (``holds_render_facts``). An entry of a dict attribute that holds one makes the whole attribute
    a target, as heki reads an entry as a read of the whole attribute: the decisions read its other entries through
    the members ``DECISION_ACCESSORS`` lists. The modules of the decisions (``module`` of ``DECISION_DECLS``) reach none
    of them, through the functions they call too, outside the decision writer and those members
    (``effect-boundaries``), and read none on a receiver heki cannot tie to a class (``read-owners``).
    """
    modules = sorted({_module(decl.module) for decl in decls.values() if decl.module})
    targets = sorted({
        f"{row.owner}.{name}"
        for row in rows
        if holds_render_facts(row) and row.owner not in PARSER_STATE_OWNERS
        for name in [_container(row) if row.access is Access.ITEM else _attribute(repo, row.attribute, row.access)]
    })
    callees = sorted({
        scope.replace(":", ".")
        for scopes in _methods(repo, accessors, "DECISION_ACCESSORS").values()
        for scope in scopes
    })
    return [
        _table(
            "effect-boundaries",
            name="decisions-read-final-facts",
            scopes=modules,
            forbid=["reads-targets"],
            read_targets=targets,
            allowed_callees=[DECISION_WRITER, *callees],
            message=DECISION_READ_ADVICE,
        ),
        _table(
            "read-owners",
            name="decisions-read-final-facts",
            targets=targets,
            forbidden_readers=[f"{module}:*" for module in modules],
            attribution="lexical",
            unresolved="report",
        ),
    ]


def import_ledger_contracts(
    repo: Repo, mutators: Iterable[str] = IMPORTS_MUTATORS, writers: Iterable[LedgerWriter] = LEDGER_WRITERS
) -> list[str]:
    """Return the ``call-owners`` contract of the methods that change an ``Imports``: only the ledger calls them.

    The import ledger's module, the ``imports`` module and the defs ``LEDGER_WRITERS`` names call the
    ``IMPORTS_MUTATORS``; any other call, judged in the scope that makes it, is a finding.
    """
    functions = _package_functions(repo)
    module, _, owner = IMPORTS.rpartition(".")
    targets = [f"{module}:{owner}.{name}" for name in mutators]
    allowed = [*_scope(_module(IMPORT_LEDGER), ""), *_scope(module, "")]
    for writer in writers:
        if f"{_module(writer.path)}.{writer.name}" not in functions:
            message = f"LEDGER_WRITERS: {writer.path} defines no {writer.name}"
            raise SystemExit(message)
        allowed += _scope(_module(writer.path), writer.name)
    return [
        _table(
            "call-owners",
            name="imports-through-the-ledger",
            targets=targets,
            allowed_callers=allowed,
            attribution="lexical",
        )
    ]


def store_operation_contracts(operations: Iterable[str] = GENERATION_STORE_MUTATION_METHODS) -> list[str]:
    """Return the contracts that route every change of the graph through the store's ``GraphBuilder``.

    A store operation (``GENERATION_STORE_MUTATION_METHODS``, the names of ``SITE_EFFECTS`` and ``NO_SITE_EFFECT`` in
    ``parser/generation.py``) is called only in the store module, where the builder calls it, also on a receiver heki
    cannot tie to a class (``store-op:``); only the store module makes a ``GraphBuilder`` (``graph-builder``).
    """
    builder_module, _, builder = GRAPH_BUILDER.rpartition(".")
    return [
        *(
            _table(
                "call-owners",
                name=f"store-op:{operation}",
                targets=[f"{STORE_MODULE}:GenerationStore.{operation}"],
                allowed_callers=_scope(STORE_MODULE, ""),
                unresolved="report",
                attribution="lexical",
            )
            for operation in sorted(operations)
        ),
        _table(
            "call-owners",
            name="graph-builder",
            targets=[f"{builder_module}:{builder}"],
            allowed_callers=_scope(STORE_MODULE, ""),
            attribution="lexical",
        ),
    ]


class _Protocols:
    """The members the Protocols of the package declare, by name, and the classes of the package, by dotted name."""

    def __init__(self, repo: Repo) -> None:
        """Read the class scopes of the package's modules."""
        self.classes: set[str] = set()
        self.members: dict[str, set[str]] = defaultdict(set)
        for rel, module in _package_modules(repo).items():
            model = repo.model(rel)
            for scope in model.scopes:
                if scope.kind != "class" or "<locals>" in scope.qualname:
                    continue
                self.classes.add(f"{module}.{scope.qualname}")
                if protocol_bases(model, scope):
                    for name in scope.bindings:
                        self.members[name].add(f"{module}.{scope.qualname}.{name}")

    def targets(self, target: str, arguments: str = "") -> list[str]:
        """Return a target and, for a member of a class, the same member of every Protocol that declares its name.

        heki judges a call or read through a value typed with a Protocol by the Protocol's declaration, so a host
        Protocol that declares the member reaches it too; ``arguments`` is the argument list each target ends in.
        """
        owner, _, name = target.rpartition(".")
        protocols = self.members.get(name, set()) if owner in self.classes else set()
        return [f"{found}{arguments}" for found in sorted({target, *protocols})]


def _mark_targets(mark: Mark, protocols: _Protocols) -> list[str]:
    """Return the heki targets of a mark: its callee, with an argument list where it asks for a parameter's constant."""
    value = f'"{mark.value}"' if isinstance(mark.value, str) else repr(mark.value)
    return protocols.targets(mark.callee, f"({mark.keyword}={value})" if mark.keyword else "")


def builder_contracts(repo: Repo, records: Iterable[BuilderHome] = BUILDER_HOMES) -> list[str]:
    """Return the contracts of the builder records: tables read by their builders, held parse methods, marked outputs.

    A table is read only by its builder and the scopes that only ask it for ``typed_children``. A call with a mark of a
    record is made only by its builder and parts; under its ``within`` directory, by no other function of those
    modules. Constructing a subclass of a marked class builds the output too, and every target that is a member of a
    class also names that member of each Protocol declaring it. Each parse method has a contract of its own: heki lets
    the targets of one contract call each other.
    """
    contracts: list[str] = []
    protocols = _Protocols(repo)
    records = list(records)
    tables = sorted({_module(record.table.path) for record in records if record.table})
    for record in records:
        builder = _scope(_module(record.builder.path), record.builder.name)
        if record.table:
            readers = [f"{_module(home.path)}:{home.name}" for home in typed_children_readers(repo, record.table)]
            contracts.append(
                _table(
                    "read-owners",
                    name=f"table:{record.table.path}:{record.table.name}",
                    targets=protocols.targets(f"{_module(record.table.path)}.{record.table.name}"),
                    allowed_readers=[*builder, *readers],
                    attribution="lexical",
                )
            )
        if record.marks:
            parts = [pattern for part in record.parts for pattern in _scope(_module(part.path), part.name)]
            within = [f"{_module(record.within.rstrip('/'))}.*"] if record.within else []
            contracts.append(
                _table(
                    "call-owners",
                    name=f"builder:{record.builder.path}:{record.builder.name}",
                    targets=sorted(target for mark in record.marks for target in _mark_targets(mark, protocols)),
                    allowed_callers=[*builder, *parts],
                    **({"forbidden_callers": within} if within else {}),
                    attribution="lexical",
                )
            )
    contracts += [
        _table(
            "call-owners",
            name=f"held:{method.path}:{method.name}",
            targets=protocols.targets(f"{_module(method.path)}.{method.name}"),
            allowed_callers=[f"{table}:<module>" for table in tables],
            references="calls",
            attribution="lexical",
        )
        for method in held_parse_methods(repo, records)
    ]
    return contracts


FINAL_FACTS = Home("parser/_phases.py", "FinalFacts")
"""The facts final at B1, built once per parse."""
FINAL_FACTS_SITE = Home("parser/generation.py", "GenerationStore.seal_facts")
"""The one function that builds ``FinalFacts``."""
PARSER = Home("parser/base.py", "Parser")
"""The class whose name-mangled members only its own body reaches."""


def pipeline_contracts(repo: Repo) -> list[str]:
    """Return the contracts of the pass pipeline: one builder of ``FinalFacts``, ``Parser``'s mangled members kept.

    The constants above name the scopes; heki refuses a target that names nothing, and the build stops where the
    function that may build ``FinalFacts`` is gone.
    """
    if f"{_module(FINAL_FACTS_SITE.path)}.{FINAL_FACTS_SITE.name}" not in _package_functions(repo):
        message = f"{FINAL_FACTS_SITE.path} defines no {FINAL_FACTS_SITE.name}"
        raise SystemExit(message)
    parser = [f"{_module(PARSER.path)}.{PARSER.name}._{PARSER.name}__*"]
    body = _scope(_module(PARSER.path), PARSER.name)
    return [
        _table(
            "call-owners",
            name="final-facts-built-by-seal-facts",
            targets=[f"{_module(FINAL_FACTS.path)}.{FINAL_FACTS.name}"],
            allowed_callers=_scope(_module(FINAL_FACTS_SITE.path), FINAL_FACTS_SITE.name),
            references="reads",
            attribution="lexical",
        ),
        _table(
            "call-owners",
            name="parser-mangled-call",
            targets=parser,
            allowed_callers=body,
            unresolved="report",
            attribution="lexical",
        ),
        _table(
            "read-owners",
            name="parser-mangled-read",
            targets=parser,
            allowed_readers=body,
            unresolved="report",
            attribution="lexical",
        ),
    ]


HOST_SCOPES: Final = (f"{PACKAGE}.parser", f"{PACKAGE}.parser._*", f"{PACKAGE}.model._field_imports")
"""The concern modules, as heki module patterns (``*`` spans dots): the files the SLF001 per-file ignores of
pyproject.toml match, every module below a ``parser/_*`` module or package (``parser/__init__.py`` too), and
``model/_field_imports.py``."""


def in_host_scope(path: Path) -> bool:
    """Return whether the host contracts (and not ruff's SLF001, see pyproject.toml) own private access in a file."""
    try:
        relative = path.resolve().relative_to(SOURCE_ROOT).as_posix()
    except ValueError:
        return False
    return any(fnmatchcase(_module(relative), pattern) for pattern in HOST_SCOPES)


def host_contracts(repo: Repo) -> list[str]:
    """Return the contracts of the host rule: the concern modules reach no private member of other classes.

    A private member is one with a single leading underscore (dunders are Python's protocol). The concern modules
    (`HOST_SCOPES`) read, call or write such a member only through their own receiver (``self``, ``cls``,
    ``super()``) or a value typed with a ``*Host`` Protocol, whose declaration the member must be in; a class deriving
    from a Host is not one, and an access heki cannot tie to a class is reported.
    """
    targets: list[str] = []
    for rel, module in sorted(_package_modules(repo).items()):
        concern = in_host_scope(ROOT / rel)
        model = repo.model(rel)
        targets += [
            f"{module}.{scope.qualname}._[!_]*"
            for scope in model.scopes
            if scope.kind == "class"
            and "<locals>" not in scope.qualname
            and (not concern or (protocol_bases(model, scope) and not scope.qualname.endswith("Host")))
        ]
    concerns = [f"{pattern}:*" for pattern in HOST_SCOPES]
    targets = sorted(set(targets))
    keys = {
        "own_receiver": "allow",
        "allowed_protocols": ["*Host"],
        "protocol_subclasses": False,
        "unresolved": "report",
        "attribution": "lexical",
    }
    return [
        _table(
            kind,
            name=f"host-private-{use}",
            targets=targets,
            **{f"forbidden_{role}": concerns},
            **keys,
        )
        for kind, use, role in (
            ("read-owners", "read", "readers"),
            ("call-owners", "call", "callers"),
            ("write-owners", "write", "writers"),
        )
    ]


_LEAF_IMPORTS: Final[dict[str, tuple[tuple[str, ...], str]]] = {
    "facts": (("_records",), "the attribute-to-fact table is read by checkers without the package"),
    "schema-graph": (
        ("_json_pointer", "_records", "reference"),
        "the schema graph is the L0 core the parsers build, and takes parser rules as arguments",
    ),
    "json-pointer": ((), "the RFC 6901 pointer codec is a leaf that every layer reads"),
    "free-names": ((), "the free-name numbering is a leaf that every layer reads"),
    "name-words": ((), "the class name made from the words of a text is a leaf that every layer reads"),
    "naming": (
        ("_free_names", "_model_keys", "_records"),
        "the names of the models are a leaf that the parsers and their passes read",
    ),
    "graph-links": (
        (),
        (
            "the back links of the generation graph are a leaf that reference.py, types.py and the model package "
            "import while they import each other"
        ),
    ),
    "container-kinds": (
        (),
        (
            "the container slots and kinds of a type tree node are a leaf that types.py, the model package, the parser "
            "and the targets read"
        ),
    ),
    "context-scope": (
        (),
        (
            "the context variable scope is a leaf that the command line, the parser, the model package and the "
            "reference resolver import"
        ),
    ),
    "warning-control": (
        (),
        (
            "how the generator's own warnings are shown is a leaf that the entry point, the recommender, the parser "
            "and its passes import"
        ),
    ),
    "message-text": (
        (),
        (
            "how the generator's messages write English is a leaf that the command line, the recommender, the parser, "
            "the model package and the targets import"
        ),
    ),
    "module-paths": ((), "the paths of the written modules are a leaf that every layer reads"),
    "openapi-paths": (
        (),
        (
            "the operation fields of a path item and the paths --openapi-include-paths selects are a leaf that the "
            "parsers, the targets and the recommender read"
        ),
    ),
    "python-syntax": (
        (),
        "the Python display writer is a leaf that the model backends, the formatter and the targets read",
    ),
    "requirements": (("_message_text", "_records", "enums"), "the requirement table is a leaf"),
    "constraints": (
        ("_records", "_regex_syntax", "_requirements", "enums"),
        "the constraint applicability table is a leaf that types.py reads while it is imported",
    ),
    "regex": (
        ("_python_syntax", "_records"),
        "the static regex scan is a leaf that the keyword table, the literal check and the backends read",
    ),
    "json-schema": (("_json_pointer",), "the subschema position table is a leaf that every schema walker reads"),
    "parser-core": (
        ("_json_schema", "_model_keys", "_records", "_regex_syntax"),
        "a pure parser core computes from the dumped schemas it is given",
    ),
}
"""The leaf layers of `classify_source_path`: the modules of the package each may import besides the standard
library and ``typing_extensions`` (with their submodules), and why."""
RUNTIME_PACKAGE: Final = f"{PACKAGE}._runtime"
RUNTIME_IMPORTS: Final = (
    "annotationlib",
    "anyio",
    "cryptography",
    "fastapi",
    "httpcore2",
    "httpx2",
    "mcp",
    "msgspec",
    "pydantic",
    "pydantic_core",
    "python_multipart",
    "starlette",
    "typing_extensions",
    "wsproto",
)
"""What the runtime imports besides the standard library: the packages generated code depends on (``annotationlib``
from Python 3.14), and its own modules, relatively."""
PARSER_PASS_IMPORTS: Final = (
    "_facts",
    "_field_rows",
    "_graph_ids",
    "_imports_plan",
    "_init_exports",
    "_intersection",
    "_model_shapes",
    "_naming",
    "_null_facts",
    "_passes",
    "_phases",
    "_pipeline",
    "_python_type_imports",
    "_tag_plan",
    "_type_overrides",
    "generation",
)
"""The parser modules a pass module may import: none imports ``parser/base.py``, so no pass closes a cycle."""


def _below(*modules: str) -> list[str]:
    """Return the patterns of modules and every module below them (``{,.*}`` is an alternative that may be empty)."""
    return [f"{module}{{,.*}}" for module in modules]


def _under(package: str, every: Iterable[str]) -> list[str]:
    """Return the modules of ``every`` that are ``package`` or below it."""
    return [module for module in every if module == package or module.startswith(f"{package}.")]


def _layer(modules: Iterable[str], every: Iterable[str], *, exact: bool = False) -> tuple[list[str], list[str]]:
    """Return the fewest heki module patterns naming a set of modules, and the modules they name beyond it.

    A package is named whole as ``<package>{,.*}`` (heki's ``*`` spans dots), its modules outside the set being the
    exceptions that a contract allows or exempts, or by its own module and the patterns of its children, whichever
    takes fewer entries. With ``exact`` a package is named whole only where all its modules are in the set, so there
    are no exceptions.
    """
    chosen, every = set(modules), sorted(every)

    def best(package: str) -> tuple[list[str], list[str]]:
        under = _under(package, every)
        if not chosen.intersection(under):
            return [], []
        if len(under) == 1:
            return [package], []
        children = sorted({f"{package}.{module[len(package) + 1 :].partition('.')[0]}" for module in under[1:]})
        parts = [best(child) for child in children]
        split = (
            [*([package] if package in chosen else []), *(pattern for named, _ in parts for pattern in named)],
            [exception for _, exempt in parts for exception in exempt],
        )
        whole = ([f"{package}{{,.*}}"], [module for module in under if module not in chosen])
        if (exact and whole[1]) or len(split[0]) + len(split[1]) <= len(whole[0]) + len(whole[1]):
            return split
        return whole

    roots = sorted({module.partition(".")[0] for module in chosen})
    found = [best(root) for root in roots]
    return [pattern for named, _ in found for pattern in named], sorted(e for _, exempt in found for e in exempt)


def _listing(names: Iterable[str]) -> str:
    """Return names as an English list: ``a``, ``a and b``, ``a, b and c``."""
    *rest, last = names
    return f"{', '.join(rest)} and {last}" if rest else last


def import_layers(repo: Repo) -> dict[str, list[str]]:
    """Return the modules of the package by their layer (`classify_source_path` of the architecture check)."""
    layers: dict[str, list[str]] = defaultdict(list)
    for rel, module in sorted(_package_modules(repo).items()):
        layers[classify_source_path(SOURCE_ROOT / Path(rel).relative_to(PACKAGE_DIR))].append(module)
    return layers


def import_boundary_contracts(repo: Repo, *, partial: bool = False) -> list[str]:
    """Return the import boundaries of the layers (``IMP201``, ``IMP202``), one per rule of the layer table.

    Every import counts: in a function, by a name ``importlib.import_module`` or ``__import__`` is given, and under
    ``TYPE_CHECKING``; an import heki cannot name is reported (``IMP202``). A leaf layer the classification does not
    know, or a rule none of the package's modules is subject to, raises: the table is out of step with the layers and
    would drop its contract silently. ``partial`` is for a seeded package that holds only some layers.
    """
    if unknown := sorted(set(_LEAF_IMPORTS) - set(get_args(Layer))):
        msg = f"_LEAF_IMPORTS names layers the architecture check does not classify: {', '.join(unknown)}"
        raise ValueError(msg)
    layers = import_layers(repo)
    backends = _below(*(f"{PACKAGE}.model.{root}" for root in sorted(CONCRETE_BACKEND_ROOTS)))
    parser = _below(f"{PACKAGE}.parser")
    leaves = {module for layer in _LEAF_IMPORTS.keys() | {"runtime"} for module in layers[layer]}
    parsers = [*layers["parser"], *layers["parser-passes"]]
    main = f"{PACKAGE}.__main__"
    others = [m for found in layers.values() for m in found if m not in {PACKAGE, main}]
    rows: list[tuple[str, list[str], dict[str, object], str]] = [
        ("root-import", others, {"forbidden_imports": [PACKAGE, main]}, ROOT_IMPORT_MESSAGE),
        ("root-main-import", [PACKAGE], {"forbidden_imports": [main]}, ROOT_IMPORT_MESSAGE),
    ]
    for layer, (modules, why) in _LEAF_IMPORTS.items():
        allowed = ["the standard library", "typing_extensions", *(f"{PACKAGE}.{module}" for module in modules)]
        keys: dict[str, object] = {"allowed_imports": ["<stdlib>", *_below("typing_extensions", *allowed[2:])]}
        rows.append((f"{layer}-import", layers[layer], keys, f"{why}: import only {_listing(allowed)}"))
    rows += [
        (
            "runtime-import",
            layers["runtime"],
            {"allowed_imports": ["<stdlib>", *_below(*RUNTIME_IMPORTS)], "relative_imports": _below(RUNTIME_PACKAGE)},
            (
                "the runtime is copied into the generated packages: import only the standard library, the third-party "
                "packages generated code depends on and other runtime modules, relatively"
            ),
        ),
        (
            "parser-passes-import",
            layers["parser-passes"],
            {
                "forbidden_imports": parser,
                "allowed_imports": _below(*(f"{PACKAGE}.parser.{m}" for m in PARSER_PASS_IMPORTS)),
            },
            (
                "pass modules must not import parser/base.py or the format parsers, not even for typing: take the "
                "parser through a narrow Protocol and import only the store, the import ledger and other pass modules"
            ),
        ),
        (
            "backend-import",
            parsers,
            {"forbidden_imports": backends},
            "parser code must depend on neutral model capabilities, not a concrete output backend",
        ),
        (
            "config-backend-import",
            layers["config"],
            {"forbidden_imports": backends},
            "configuration must receive output implementations through a neutral registry or injection",
        ),
        (
            "config-parser-import",
            layers["config"],
            {"forbidden_imports": parser},
            "configuration must not depend on parser implementation modules",
        ),
        (
            "shared-model-backend-import",
            layers["shared-model"],
            {"forbidden_imports": backends},
            "shared model code must not depend on a concrete backend; expose a neutral capability instead",
        ),
        (
            "reference-backend-import",
            layers["reference"],
            {"forbidden_imports": [*backends, *_below("msgspec")]},
            "reference code must not import concrete output backends except bounded compatibility exports",
        ),
        (
            "target-parser-import",
            layers["target"],
            {"forbidden_imports": parser},
            "target generation must consume accepted contracts, not parser modules",
        ),
        (
            "target-reverse-import",
            sorted(m for layer, found in layers.items() if layer != "target" for m in found if m not in leaves),
            {"forbidden_imports": _below(*sorted(TARGET_MODULES))},
            "model generation must not depend on target generation; targets depend on the core",
        ),
        (
            "private-parser-import",
            sorted(
                m for layer, found in layers.items() if layer not in PARSER_LAYERS for m in found if m not in leaves
            ),
            {
                "forbidden_imports": [
                    f"{PACKAGE}.parser._*",
                    f"{PACKAGE}.parser.*._*",
                    f"{PACKAGE}.parser:_*",
                    f"{PACKAGE}.parser.*:_*",
                ]
            },
            PRIVATE_PARSER_IMPORT_MESSAGE,
        ),
    ]
    if not partial and (empty := [name for name, importers, _, _ in rows if not importers]):
        msg = f"import boundaries without a module of the package to judge: {', '.join(empty)}"
        raise ValueError(msg)
    every = [module for found in layers.values() for module in found]
    return [
        _table(
            "import-boundaries",
            name=name,
            importers=patterns,
            **({"exempt_importers": exempt} if exempt else {}),
            **keys,
            type_checking="report",
            unresolved="report",
            message=message,
        )
        for name, importers, keys, message in rows
        if importers
        for patterns, exempt in [_layer(importers, every)]
    ]


BACKEND_SEMANTIC_MESSAGE: Final = "parser code must query a neutral DataModel or DataModelFieldBase capability"
BACKEND_IDENTITY_MESSAGE: Final = (
    "parser code must not identify an output backend by module/name; declare a neutral capability"
)
ENTRYPOINT_PRIVATE_MESSAGE: Final = "entrypoint code must use the Parser public lifecycle and run-context API"
SEMANTIC_METHODS: Final = (
    "model.base.DataModel._append_internal_template_data",
    "model.base.DataModelFieldBase.is_pydantic_extra_field",
    "_format_types.PythonVersion.has_typed_dict_closed",
    "_format_types.PythonVersion.has_typed_dict_non_required",
)
"""The methods of the two neutral model bases and the target version that only a backend calls or reads: parser code
calls none and reads none, with ``getattr`` or ``hasattr`` too. heki names a member of a class in its subclasses too,
and where only a subclass defines it, so the backend classes need no entry of their own."""
SEMANTIC_VALUES: Final = (
    "model.base.DataModel.PLAIN_PATTERN_ROOT_TYPES",
    "model.base.DataModel.SCHEMA_RUNTIME_VALIDATION_HELPERS_TEMPLATE_FILE_PATH",
    "model.base.DataModel._internal_template_data",
)
"""The other members of those classes only a backend reads."""
REQUIREMENT_READERS: Final = ("_requirements.python_target_meets", "_format_types.PythonVersion._meets")
"""The functions that answer whether the target Python has a construct: parser code asks them for no TypedDict one."""
MODULE_REGISTRY_READERS: Final = (
    "model.base:_model_rebuild_namespace",
    "model.base:_rebuild_model_with_datamodel_namespace",
)
"""The shared model code that reads ``sys.modules``: both complete a pydantic model of the package with the names of
the module that defines it, never a backend's module, so the contract, which refuses every read of the registry and
not only one keyed by a backend module, leaves them out."""
MODULE_NAME_READERS: Final = (
    "parser.base:_naming_rules_of",
    "parser.jsonschema:JsonSchemaParser._reads_schema_version_per_document",
    "parser._passes.render_prep:deserialize_decimal_default",
)
"""The parser code that reads the ``__module__`` of a class: each asks whether a class comes from this package or is
``decimal.Decimal``, never which backend a model is, so the contract, which refuses every read of ``__module__`` and
not only a comparison with a backend module, leaves them out."""
CONFIG_DEFAULTS_BACKEND: Final = "pydantic_v2"
"""The backend whose names the module body of the configuration may read: the defaults of ``ParserConfig``
(``data_model_type`` and the other three) are its classes, the public defaults of the parser. heki judges a class
body as the module body, so the contract of the module body leaves out this backend and keeps the others; the
functions of the configuration read none, and the module imports only this backend (``config-backend-import``).

The narrowing (owner 2026-11-26): the contract allows any module-body read of this backend, not exactly the four
defaults the checker allowed, until heki attributes the body of a class to the class; the contract then names the
defaults of ``ParserConfig`` alone (a heki request follows)."""
ENTRYPOINT_PRIVATE_READERS: Final = (
    "_generation:_build_parser",
    "_generation:_parse_run",
)
"""The entry point code that reaches a private member of a parser class: the JSON Schema parser built from Python type
expressions (``_from_python_type_expressions``, the one caller of that constructor) and whether a captured parser
factory reads the API scope (``_supports_api_scope``, a class flag of the parser classes)."""


def architecture_contracts(repo: Repo, *, partial: bool = False) -> list[str]:
    """Return the contracts of what a layer may read of another one, beyond the modules it imports.

    Parser code reads no member only a backend reads and asks the target version for no TypedDict construct
    (`SEMANTIC_METHODS`, `SEMANTIC_VALUES`, `REQUIREMENT_READERS`), names no backend module, by a ``module=`` argument
    or by the ``__module__`` of a class of the package (the functions of `MODULE_NAME_READERS` read it, and the contract
    allows them), and selects no output family (``DataModelType``, ``ModelType.PYDANTIC`` and ``ModelType.MSGSPEC``);
    input model code selects none either; shared model code reads no module of the ``sys.modules`` registry (the
    functions of `MODULE_REGISTRY_READERS` read it, and the contract allows them); the functions of the configuration
    read no backend name, and its module body may read the backend of `CONFIG_DEFAULTS_BACKEND`; the entry point
    reads and calls no private member of a parser, but in the functions of `ENTRYPOINT_PRIVATE_READERS`, and writes
    none, on a parser it has just built too; no module but the root and ``__main__`` reads a name through the
    package root, in a function, a class or a module body. Each read counts in the scope that holds it, a class
    body too, and a read heki cannot tie to a class is reported (``MUT206``, ``MUT207``). A layer is named by package
    patterns (`_layer`), its modules outside it allowed; a target or a module that names nothing is heki's error
    (``unmatched-contract-entries``). A contract no module of the package is subject to raises, as in
    `import_boundary_contracts`, and so does an allowed function the package does not define; ``partial`` is for a
    seeded package that holds only some layers.
    """
    layers = import_layers(repo)
    every = [m for found in layers.values() for m in found]
    parsers = [*layers["parser"], *layers["parser-passes"]]
    backends = f"{PACKAGE}.model.{{{','.join(sorted(CONCRETE_BACKEND_ROOTS))}}}"
    parser_private = [f"{PACKAGE}.parser.base.Parser._[!_]*"]
    entry_allowed = [f"{PACKAGE}.{scope}{{,.*}}" for scope in ENTRYPOINT_PRIVATE_READERS]
    output_policy = "parser code must query output capabilities instead of selecting concrete backend policy"
    config = "configuration must receive output implementations through a neutral registry or injection"
    reads = {"references": "reads"}
    rows: list[tuple[str, str, list[str], list[str], list[str], Mapping[str, object], str]] = [
        (
            "read-owners",
            "backend-semantic-inspection",
            [f"{PACKAGE}.{member}" for member in (*SEMANTIC_METHODS, *SEMANTIC_VALUES)],
            parsers,
            [],
            {},
            BACKEND_SEMANTIC_MESSAGE,
        ),
        (
            "call-owners",
            "backend-semantic-inspection",
            [
                *(f"{PACKAGE}.{method}" for method in SEMANTIC_METHODS),
                *(f'{PACKAGE}.{reader}(construct="python.typed_dict_*")' for reader in REQUIREMENT_READERS),
            ],
            parsers,
            [],
            {},
            BACKEND_SEMANTIC_MESSAGE,
        ),
        (
            "call-owners",
            "backend-module-identity",
            [f'*(module="{backends}{{,.*}}")'],
            parsers,
            [],
            {},
            BACKEND_IDENTITY_MESSAGE,
        ),
        (
            "read-owners",
            "backend-module-identity",
            [f"{PACKAGE}.*.__module__"],
            parsers,
            [f"{PACKAGE}.{scope}{{,.*}}" for scope in MODULE_NAME_READERS],
            {},
            BACKEND_IDENTITY_MESSAGE,
        ),
        ("call-owners", "parser-output-policy", [f"{PACKAGE}.enums.DataModelType"], parsers, [], reads, output_policy),
        (
            "read-owners",
            "parser-output-policy",
            [f"{PACKAGE}.reference.ModelType.PYDANTIC", f"{PACKAGE}.reference.ModelType.MSGSPEC"],
            parsers,
            [],
            {},
            output_policy,
        ),
        (
            "call-owners",
            "input-model-output-policy",
            [f"{PACKAGE}.enums.DataModelType"],
            layers["input-model"],
            [],
            reads,
            "input-model conversion must obtain output-family policy from the output selection registry",
        ),
        (
            "read-owners",
            "shared-model-backend-module-access",
            ["sys.modules"],
            layers["shared-model"],
            [f"{PACKAGE}.{scope}{{,.*}}" for scope in MODULE_REGISTRY_READERS],
            {},
            (
                "shared model code must not inspect a concrete backend through sys.modules; "
                "move backend lifecycle or cache management to the backend or composition root"
            ),
        ),
        *(
            (
                "call-owners",
                "config-backend-reference",
                [f"{PACKAGE}.model.{{{','.join(found)}}}{{,.*}}:*"],
                [f"{module}:{scope}" for module in layers["config"]],
                [],
                {**reads, "subclasses": False},
                config,
            )
            for scope, found in (
                ("[!<]*", sorted(CONCRETE_BACKEND_ROOTS)),
                ("<module>", sorted(CONCRETE_BACKEND_ROOTS - {CONFIG_DEFAULTS_BACKEND})),
            )
        ),
        *(
            (
                kind,
                "entrypoint-parser-private-access",
                parser_private,
                layers["entrypoint"],
                [] if kind == "write-owners" else entry_allowed,
                {"fresh_objects": "report"} if kind == "write-owners" else {},
                ENTRYPOINT_PRIVATE_MESSAGE,
            )
            for kind in ("read-owners", "call-owners", "write-owners")
        ),
        (
            "call-owners",
            "root-import",
            [f"{PACKAGE}:*"],
            [module for module in every if module not in {PACKAGE, f"{PACKAGE}.__main__"}],
            [],
            reads,
            ROOT_IMPORT_MESSAGE,
        ),
    ]
    if not partial and (empty := [f"{kind}:{name}" for kind, name, _, scopes, _, _, _ in rows if not scopes]):
        msg = f"architecture contracts without a module of the package to judge: {', '.join(empty)}"
        raise ValueError(msg)
    defined = _package_functions(repo)
    allowed = (*MODULE_NAME_READERS, *MODULE_REGISTRY_READERS, *ENTRYPOINT_PRIVATE_READERS)
    if not partial and (missing := [name for name in allowed if f"{PACKAGE}.{name.replace(':', '.')}" not in defined]):
        msg = f"architecture contracts allow functions the package does not define: {', '.join(missing)}"
        raise ValueError(msg)
    role = {"read-owners": "readers", "call-owners": "callers", "write-owners": "writers"}
    return [
        _table(
            kind,
            name=name,
            targets=targets,
            **{f"forbidden_{role[kind]}": forbidden},
            **({f"allowed_{role[kind]}": [*allowed, *exempt]} if allowed or exempt else {}),
            **keys,
            attribution="lexical",
            **({} if "references" in keys else {"unresolved": "report"}),
            message=message,
        )
        for kind, name, targets, scopes, allowed, keys, message in rows
        if scopes
        for forbidden, exempt in [_scopes(scopes, every)]
    ]


def _scopes(scopes: list[str], every: Iterable[str]) -> tuple[list[str], list[str]]:
    """Return the forbidden scopes of a layer's modules (`_layer`) and those of its packages' other modules.

    A scope that already names its part of a module (``<module>:<qualname>``) is kept as it is.
    """
    if any(":" in scope for scope in scopes):
        return scopes, []
    patterns, exempt = _layer(scopes, every)
    return [f"{pattern}:*" for pattern in patterns], [f"{module}:*" for module in exempt]


def environment_contracts() -> list[str]:
    """Return the effect boundary that keeps the package's functions and module bodies from reading the environment."""
    return [
        _table(
            "effect-boundaries",
            name="no-environment-reads",
            scopes=[f"{PACKAGE}.*", f"{PACKAGE}:*", f"{PACKAGE}.*:<module>", f"{PACKAGE}:<module>"],
            forbid=["reads-environment"],
            import_probes="report",
            message=ENVIRONMENT_ADVICE,
        )
    ]


def shared_object_contracts(repo: Repo, producers: Iterable[tuple[str, str]] = SHARED_PRODUCERS) -> list[str]:
    """Return the ``readonly-returns`` contract of the objects the schema graph shares with the readers of a parse."""
    functions = _package_functions(repo)
    names = []
    for path, name in producers:
        module = _module(path)
        if f"{module}.{name}" not in functions:
            message = f"SHARED_PRODUCERS: {path} defines no function {name}"
            raise SystemExit(message)
        names.append(name if "." in name else f"{module}.{name}")
    return [_table("readonly-returns", name="schema-graph-shared-objects", producers=names, deep=True)]


TABLES = "parser/_passes/_tables.py"
"""The pipeline tables of ``Parser``: their steps name the functions each pass and hook runs."""
PIPELINE_RUNNER = "parser/_pipeline.py"
"""The module that runs the tables: segments, module loops, hook calls and passes."""
PARSER_CLASS = f"{PACKAGE}.parser.base.Parser"
"""The parser a table step's ``self`` is: a method it calls is any parser class's method of that name."""
MODEL_CLASS = f"{PACKAGE}.model.base.DataModel"
"""The model class a table step's ``self.data_model_type`` is."""
STEP_CLASSES = {f"{PACKAGE}.parser._pipeline.PassSpec": "pass", f"{PACKAGE}.parser._pipeline.HookCall": "hook"}
DECISION_CLASS = f"{PACKAGE}.parser._phases.DecisionPass"
LATE = f"{PACKAGE}.parser._pipeline.late"
"""The marker of a step whose function the parser instance looks up when the step runs."""
FREEZE_PHASES = {FreezePoint.B1: Phase.RENDER_PREPARATION, FreezePoint.B2: Phase.IMPORTS}
"""The first phase in which the facts final at each freeze point are frozen.

The facts final at B1 stay writable for the decisions that declare them until the decision phase ends.
"""
PHASE_TARGET = "GenerationStore.phase"
"""The phase of the pipeline: no pass enters a phase, the pipeline does."""
FINALITY_KINDS = (FactKind.FACT, FactKind.PRESENTATION)


class KnownFinding(NamedTuple):
    """A finding of the pass boundaries that ``scripts/heki_baseline.toml`` keeps, with what removes it."""

    target: str
    """The target of the baseline entry: ``pass:<pass>:<row>`` or ``pass-glue:<row>``."""
    removed_by: str


_B2_FREEZE: Final = (
    "the owner's call on BACKEND_CONFIG and DEFAULT_EXPRESSION, final at B2, which the import passes write after "
    "renaming classes: either they freeze later or the writes move before B2"
)
_FIELD_PLAN_C: Final = "the step that renders msgspec, dataclass, TypedDict and enum fields from one plan"
_FRESH_COPY: Final = (
    "a heki that reads the copy Parser._copy_inherited_field makes (copy_owning, model_copy) as a fresh object: "
    "the pass writes these rows on that copy, never on a field of the input"
)
_COPIED_ROWS: Final = (
    "DataModelFieldBase._nullable_without_default",
    "DataType.data_types",
    "DataType.is_dict",
    "DataType.is_frozen_set",
    "DataType.is_list",
    "DataType.is_mapping",
    "DataType.is_optional",
    "DataType.is_sequence",
    "DataType.is_set",
    "DataType.is_tuple",
    "DataType.kwargs",
    "DataType.tuple_item_count",
)
"""The rows ``override_required_field`` writes on the copy of an inherited field before the copy replaces the
placeholder; heki counts each as a write of the pass's input."""
_FRESH_FIELD: Final = (
    "a heki that reads the field Parser._build_field constructs as a fresh object: apply_discriminator_type records "
    "the alias scope of the tag field it builds and appends in the same pass, never of a field of the input"
)
KNOWN_FINDINGS: Final = (
    KnownFinding("pass:name_final_classes_of_additional_properties:DataModel._internal_template_data", _B2_FREEZE),
    KnownFinding("pass:refresh_sequence_interface_after_renames:DataModel._internal_template_data", _B2_FREEZE),
    KnownFinding(
        "pass:set_nested_model_default_factory_metadata:DataModel._nested_model_default_factory_order", _B2_FREEZE
    ),
    KnownFinding(
        "pass:set_nested_model_default_factory_metadata:DataModel._nested_model_default_factory_recursive_keys",
        _B2_FREEZE,
    ),
    KnownFinding('pass:fix_constructor_field_ordering:DataModelFieldBase.extras["init"]', _FIELD_PLAN_C),
    KnownFinding(
        'pass:order_constructor_fields_by_decided_nested_factories:DataModelFieldBase.extras["init"]', _FIELD_PLAN_C
    ),
    KnownFinding(
        "pass-glue:Parser._recursion_facts",
        "#7417: the module loop settles the update actions of each module, outside every pass",
    ),
    *(KnownFinding(f"pass:override_required_field:{row}", _FRESH_COPY) for row in _COPIED_ROWS),
    KnownFinding("pass:apply_discriminator_type:DataModelFieldBase._alias_resolution_class_name", _FRESH_FIELD),
)
"""The findings of the pass boundaries that the baseline keeps; the baseline holds exactly these.

Constructor ordering, after B1, gives a required field back the ``init`` entry (``REQUIRED``) its base left out; the
dataclass field plan will hold it. The import passes write the backend config of the template data and the order of
the nested default factories after B2, where those facts are final. ``override_required_field`` writes the copy of an
inherited field, which heki cannot tell from the field it copies, and ``apply_discriminator_type`` the tag field it
builds, which heki cannot tell from a field of the input.
"""


def _names(spec: ast.expr, name: str) -> bool:
    """Return whether ``spec`` is a dict display whose ``"name"`` entry is ``name``."""
    return isinstance(spec, ast.Dict) and any(
        isinstance(key, ast.Constant) and key.value == "name" and ast.literal_eval(value) == name
        for key, value in zip(spec.keys, spec.values, strict=True)
    )


class _TableSteps:
    """The functions the steps of the pipeline tables and the decisions run, read from their sources.

    A step's run is a function (``late(f)`` or ``f``) or a lambda; a lambda runs every function it calls. A method
    called on the step's parser (its first parameter) is the method of every parser class that defines it, and one
    called on ``self.data_model_type`` that of every model class. A name that leads outside the package (a builtin,
    ``typing.cast``) runs nothing of the package; any other call that names no function or class stops the build.
    """

    def __init__(self, repo: Repo) -> None:
        """Read the tables module and the classes of the package."""
        self.repo = repo
        self.classes = _Classes(repo)
        self.index = PackageIndex(repo.root / PACKAGE_DIR)
        self.model = repo.model(f"{PACKAGE_DIR}{TABLES}")
        self.modules = set(_package_modules(repo).values())
        self.lambdas = {id(scope.node): scope for scope in self.model.scopes if scope.kind == "lambda"}

    def _symbol(self, dotted: str) -> str | None:
        """Return a function or class of the package as ``module:qualname``, or None outside the package."""
        dotted = self.index.resolve(dotted)
        for module in sorted(self.modules, key=len, reverse=True):
            if dotted.startswith(f"{module}."):
                qualname = dotted.removeprefix(f"{module}.")
                if dotted in self.classes.functions or qualname in self.classes.modules.get(module, ()):
                    return f"{module}:{qualname}"
                break
        if not dotted.startswith(f"{PACKAGE}."):
            return None
        message = f"{TABLES}: {dotted} is no function or class of the package"
        raise SystemExit(message)

    def _callee(self, func: ast.expr, receiver: str | None) -> list[str]:
        """Return the scopes of the functions a call of ``func`` runs, ``receiver`` being the step's parser."""
        root = None
        if isinstance(func, ast.Attribute) and receiver is not None:
            roots = {receiver: PARSER_CLASS, f"{receiver}.data_model_type": MODEL_CLASS}
            if (root := roots.get(self.model.source(func.value))) is not None and (
                found := self.classes.definers(root, func.attr)
            ):
                return found
        if root is None and (origin := self.model.origin(func)) is not None:
            return [] if (symbol := self._symbol(origin)) is None else [symbol]
        message = f"{TABLES}: cannot tell which function `{ast.unparse(func)}` calls"
        raise SystemExit(message)

    def run(self, node: ast.expr) -> list[str]:
        """Return the scopes of the functions a step's run runs."""
        if isinstance(node, ast.Call) and self.model.origin(node.func) == LATE and node.args:
            return self._callee(node.args[0], None)
        if (scope := self.lambdas.get(id(node))) is not None:
            receiver = next((name for name, bound in scope.bindings.items() if bound[0].kind == "param"), None)
            calls = (call.func for call in ast.walk(scope.node) if isinstance(call, ast.Call))
            return sorted({symbol for func in calls for symbol in self._callee(func, receiver)})
        return self._callee(node, None)

    def steps(self) -> dict[str, tuple[str, list[str]]]:
        """Return each pass and hook of the tables by name, with whether it is one and the scopes it runs."""
        found: dict[str, tuple[str, list[str]]] = {}
        for call in ast.walk(self.model.tree):
            if (
                isinstance(call, ast.Call)
                and (kind := STEP_CLASSES.get(self.model.origin(call.func) or ""))
                and isinstance(name := call.args[0], ast.Constant)
                and isinstance(step := name.value, str)
            ):
                found[step] = (kind, self.run(call.args[1]))
        return found

    def decisions(self, decls: Mapping[str, PassDecl]) -> dict[str, list[str]]:
        """Return the scope of the function each decision runs: its ``DecisionPass`` in the module it declares."""
        found: dict[str, list[str]] = {}
        for decl in decls.values():
            model = self.repo.model(f"{PACKAGE_DIR}{decl.module}")
            for call in ast.walk(model.tree):
                if (
                    isinstance(call, ast.Call)
                    and model.origin(call.func) == DECISION_CLASS
                    and _names(call.args[0], decl.name)
                    and (origin := model.origin(call.args[1])) is not None
                    and (symbol := self._symbol(origin)) is not None
                ):
                    found[decl.name] = [symbol]
            if decl.name not in found:
                message = f"DECISION_DECLS: {decl.module} makes no DecisionPass named {decl.name}"
                raise SystemExit(message)
        return found


def finality_targets(repo: Repo, rows: Iterable[AttributeFact] = ATTRIBUTE_FACTS) -> dict[str, list[AttributeFact]]:
    """Return the fact and presentation rows by the target heki names their writes by.

    An ``Access.ITEM`` row is its entry (``extras["init"]``) and its dict attribute too, which a write of the whole
    dict changes; a ``__dict__`` key row is the value of its constant. A cached property is computed, not written.
    """
    targets: dict[str, list[AttributeFact]] = defaultdict(list)
    for row in rows:
        if row.kind not in FINALITY_KINDS or row.access is Access.CACHED_PROPERTY:
            continue
        if row.access is Access.ITEM:
            targets[f"{row.owner}.{_container(row)}"].append(row)
            targets[f"{row.owner}.{row.attribute}"].append(row)
        else:
            targets[f"{row.owner}.{_attribute(repo, row.attribute, row.access)}"].append(row)
    return dict(targets)


def _frozen(fact: Fact | RenderFact, row: AttributeFact, phase: Phase) -> bool:
    """Return whether ``fact`` of ``row`` is frozen in ``phase``: its freeze point has passed."""
    return (first := FREEZE_PHASES.get(freeze_point(row, fact))) is not None and phase.value >= first.value


def may_write(decl: PassDecl, row: AttributeFact) -> bool:
    """Return whether the pass of ``decl``, in the phase it declares, may write ``row``.

    It declares one of the row's facts (a fact row), or ``BASES`` for a fact row of a base; not every fact it declares
    of the row is frozen in its phase; and the phase is not later than the row's ``final_after`` (a fact row), which
    for a decision is the end of the decision phase at the earliest.
    """
    facts = {*row.facts, *((Fact.BASES,) if any(isinstance(fact, Fact) for fact in row.facts) else ())}
    declared = facts & decl.writes
    if row.kind is FactKind.FACT and not declared:
        return False
    if declared and all(_frozen(fact, row, decl.phase) for fact in declared):
        return False
    final_after = row.final_after
    if decl.phase is Phase.DECIDE and final_after.value < Phase.DECIDE.value:
        final_after = Phase.DECIDE
    return row.kind is not FactKind.FACT or decl.phase.value <= final_after.value


def finality_contracts(
    repo: Repo,
    decls: Iterable[PassDecl] = PASS_DECLS,
    decisions: Mapping[str, PassDecl] = DECISION_DECLS,
    rows: Iterable[AttributeFact] = ATTRIBUTE_FACTS,
) -> list[str]:
    """Return the effect boundaries that hold each pass to its declaration, and the pipeline between them to none.

    Every pass after L1 (the ``parse`` segment, whose passes build the graph) and every decision is a boundary over
    the functions it runs, which reaches only the rows of its universe that ``may_write`` lets it write, and never the
    phase. The glue (the hooks of the tables, the parser methods they call, the runner and the decision phase) writes
    no fact row.
    """
    steps = _TableSteps(repo)
    tables = steps.steps()
    targets = finality_targets(repo, rows)
    universe = sorted([*targets, PHASE_TARGET])
    runs = {name: scopes for name, (kind, scopes) in tables.items() if kind == "pass"} | steps.decisions(decisions)
    contracts: list[str] = []
    for decl in [*decls, *decisions.values()]:
        if decl.phase is Phase.PARSE:
            continue
        if decl.name not in runs:
            message = f"{decl.name}: no step of {TABLES} and no decision runs it"
            raise SystemExit(message)
        contracts.append(
            _table(
                "effect-boundaries",
                name=f"pass:{decl.name}",
                scopes=runs[decl.name],
                write_universe=universe,
                allow_writes=sorted(
                    target for target, written in targets.items() if all(may_write(decl, row) for row in written)
                ),
                message=PASS_ADVICE,
            )
        )
    hooks = [(name, scopes) for name, (kind, scopes) in tables.items() if kind == "hook"]
    glue = sorted({
        *(scope for _, scopes in hooks for scope in scopes),
        *(scope for name, _ in hooks for scope in steps.classes.definers(PARSER_CLASS, f"_{name}")),
        *_scope(_module(PIPELINE_RUNNER), ""),
        *_scope(_module(DECISION_PHASE), ""),
    })
    fact_targets = sorted(
        target for target, written in targets.items() if any(r.kind is FactKind.FACT for r in written)
    )
    contracts.append(
        _table(
            "effect-boundaries",
            name="pass-glue",
            scopes=glue,
            forbid=["writes-targets"],
            write_targets=fact_targets,
            message=GLUE_ADVICE,
        )
    )
    return contracts


def required_version() -> str:
    """Return the ``required-version`` of the contracts: at least the heki that the ``heki`` dependency group pins."""
    group = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["dependency-groups"]["heki"]
    return f">={next(pin.removeprefix('heki==') for pin in group if pin.startswith('heki=='))}"


def generate() -> str:
    """Return the contents of scripts/heki.toml."""
    repo = Repo(ROOT, Page([], [], [], []))
    defaults, contracts = kind_defaults(
        failing(
            group_contracts([
                *write_owner_contracts(repo),
                *store_member_contracts(repo),
                *store_operation_contracts(),
                *decision_home_contracts(repo),
                *decision_writer_contracts(repo),
                *decision_contracts(repo),
                *import_ledger_contracts(repo),
                *builder_contracts(repo),
                *host_contracts(repo),
                *import_boundary_contracts(repo),
                *architecture_contracts(repo),
                *pipeline_contracts(repo),
                *shared_object_contracts(repo),
                *environment_contracts(),
                *finality_contracts(repo),
            ])
        )
    )
    head = "\n".join([
        HEADER,
        f"required-version = {_quote(required_version())}",
        "",
        "[lint]",
        f"select = {_array(RULES)}",
        f"fail = {_array(FAIL)}",
        f"unmatched-contract-entries = {_quote(UNMATCHED_ENTRIES)}",
        "",
        "[imports]",
        f"cycle-edges = {_array(IMPORT_EDGES)}",
        "",
    ])
    return "\n".join([head, *defaults, *contracts])


def main(argv: list[str] | None = None) -> int:
    """Write scripts/heki.toml, or with ``--check`` report whether it is up to date."""
    parser = argparse.ArgumentParser(description=__doc__.partition("\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 where scripts/heki.toml is not up to date")
    args = parser.parse_args(argv)
    text = generate()
    if args.check:
        if (CONTRACTS.read_text(encoding="utf-8") if CONTRACTS.is_file() else None) != text:
            print(f"{CONTRACTS.relative_to(ROOT)} is out of date: run python scripts/build_heki_contracts.py")
            return 1
        return 0
    CONTRACTS.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
