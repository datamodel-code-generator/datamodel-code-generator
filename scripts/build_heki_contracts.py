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
  cached property writes its own name into ``__dict__``: the baseline keeps today's, which cache rows name.
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
  group is a new report. heki reports it as a warning, so ``fail-on = "warning"`` makes it fail the check. A cycle of
  the imports that run while their modules load inside a group adds no report: the ``heki`` lane refuses every such
  cycle with ``IMP101`` over those imports alone (``tests/test_build_heki_contracts_script.py``).
- ``import-boundaries`` (``IMP201``, ``IMP202``) of the layers of the package (`_classify_source_path` of
  ``scripts/check_architecture_boundaries.py``, which keeps the reads an import boundary cannot name): each leaf layer
  imports only the standard library, ``typing_extensions`` and the modules `_LEAF_IMPORTS` lists, the runtime its
  third-party packages and its own modules only relatively; no module imports the package root or ``__main__``, the
  pass modules no parser module but `PARSER_PASS_IMPORTS`, the parser, configuration, shared model and reference code
  no concrete backend, configuration and the targets no parser module, the core no target and no code outside the
  parser a private parser module. Every import counts, under ``TYPE_CHECKING`` and by name too, and an import heki
  cannot name is reported (``IMP202``), so the baseline counts the imports of modules only the run knows.
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
file holds one table per distinct setting, not one per target.

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
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple, get_args

import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._decision_home_reads import checked_homes, held_parse_methods, typed_children_readers
from scripts.check_architecture_boundaries import (
    _PARSER_LAYERS,
    _PRIVATE_PARSER_IMPORT_MESSAGE,
    _ROOT_IMPORT_MESSAGE,
    _TARGET_MODULES,
    CONCRETE_BACKEND_ROOTS,
    Layer,
    PackageIndex,
    _classify_source_path,
    _protocol_bases,
    bases,
)
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
RULES = ("EFF201", "IMP101", "IMP201", "IMP202", "MUT201", "MUT202", "MUT203", "MUT204", "MUT205", "MUT206", "MUT207")
FAIL_ON = "warning"
"""The severity that fails the check: contract findings are heuristic warnings, which heki's default lets pass."""
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


def _module(path: str) -> str:
    """Return the dotted module of a path relative to the package directory."""
    parts = path.removesuffix(".py").split("/")
    return ".".join([PACKAGE, *parts[: -1 if parts[-1] == "__init__" else None]])


def _scope(module: str, name: str) -> list[str]:
    """Return the patterns of a scope of a module and the scopes inside it, or of the whole module without a name."""
    return [f"{module}:{name}", f"{module}:{name}.*"] if name else [f"{module}:*"]


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
        index = PackageIndex()
        self.modules: dict[str, list[str]] = {}
        """The classes by the module that defines them, as ``module`` and ``qualname`` joined by a dot."""
        self.bases: dict[str, list[str]] = {}
        for rel, module in _package_modules(repo).items():
            model = repo.model(rel)
            for scope in model.scopes:
                if scope.kind == "class" and "<locals>" not in scope.qualname:
                    self.modules.setdefault(module, []).append(scope.qualname)
                    self.bases[f"{module}.{scope.qualname}"] = [
                        index.resolve(o) for base in bases(scope) if (o := model.origin(base))
                    ]
        self.functions = _package_functions(repo)

    def derives(self, name: str, root: str) -> bool:
        """Return whether the class ``name`` is ``root`` or derives from it."""
        todo, seen = [name], set()
        while todo:
            if (current := todo.pop()) == root:
                return True
            seen.add(current)
            todo += [parent for parent in self.bases.get(current, []) if parent not in seen]
        return False

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


def _store_methods(repo: Repo) -> dict[StoreMember | DecisionAccessor, list[str]]:
    """Return the ``CALL`` rows of ``STORE_MEMBERS``, each with the scopes of the method and of its overrides."""
    return _methods(repo, (member for member in STORE_MEMBERS if member.use is MemberUse.CALL), "STORE_MEMBERS")


def _store_hooks(repo: Repo) -> list[str]:
    """Return the scopes of the methods that write the graph for the store operations, overrides included."""
    return sorted({scope for scopes in _store_methods(repo).values() for scope in scopes})


def write_owner_contracts(repo: Repo) -> list[str]:
    """Return the ``write-owners`` contracts of the fact rows.

    The cache contract forbids a ``cached_property`` on every owner of a row outside the parser run state and on the
    classes deriving from one: a cached property writes its own name into ``__dict__``, outside the store.
    """
    hooks = _store_hooks(repo)
    groups: dict[tuple[str, tuple[str, ...]], list[str]] = defaultdict(list)
    for row in ATTRIBUTE_FACTS:
        if row.kind not in WRITE_KINDS or (row.owner in PARSER_STATE_OWNERS and not row.store_ops):
            continue
        groups[row.kind.value, tuple(sorted(row.store_ops))].append(
            f"{row.owner}.{_attribute(repo, row.attribute, row.access)}"
        )
    described = sorted({row.owner for row in ATTRIBUTE_FACTS if row.owner not in PARSER_STATE_OWNERS})
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
            )
        )
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
    functions = _package_functions(repo)
    modules = set(_package_modules(repo).values())
    contracts: list[str] = []
    claimed: set[str] = set()
    for decl in sorted(decls.values(), key=lambda decl: decl.name):
        if not decl.writer_methods or _module(decl.module) not in modules:
            message = f"DECISION_DECLS: {decl.name} names no module of the package or no write (module, writer_methods)"
            raise SystemExit(message)
        claimed.update(decl.writer_methods)
        for method in decl.writer_methods:
            if f"{DECISION_WRITER}.{method}" not in functions:
                message = f"DECISION_DECLS: {decl.name} writes through {method}, which {owner} does not define"
                raise SystemExit(message)
            contracts.append(
                _table(
                    "call-owners",
                    name=f"decision-writer:{method}",
                    targets=[f"{module}:{owner}.{method}"],
                    allowed_callers=_scope(_module(decl.module), ""),
                    attribution="call-graph",
                    unresolved="report",
                )
            )
    prefix = f"{DECISION_WRITER}."
    contracts.extend(
        _table(
            "call-owners",
            name=f"decision-writer:{method}",
            targets=[f"{module}:{owner}.{method}"],
            allowed_callers=_scope(_module(DECISION_PHASE), ""),
            attribution="call-graph",
            unresolved="report",
        )
        for method in sorted(
            name.removeprefix(prefix)
            for name in functions
            if name.startswith(prefix) and not name.removeprefix(prefix).startswith("_")
        )
        if "." not in method and method not in claimed
    )
    return contracts


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
    for target in targets:
        if target.replace(":", ".") not in functions:
            message = f"IMPORTS_MUTATORS: {owner} defines no method {target.rpartition('.')[2]}"
            raise SystemExit(message)
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


def store_operation_contracts(repo: Repo, operations: Iterable[str] = GENERATION_STORE_MUTATION_METHODS) -> list[str]:
    """Return the contracts that route every change of the graph through the store's ``GraphBuilder``.

    A store operation (``GENERATION_STORE_MUTATION_METHODS``, the names of ``SITE_EFFECTS`` and ``NO_SITE_EFFECT`` in
    ``parser/generation.py``) is called only in the store module, where the builder calls it, also on a receiver heki
    cannot tie to a class (``store-op:``); only the store module makes a ``GraphBuilder`` (``graph-builder``).
    """
    functions = _package_functions(repo)
    owner = f"{STORE_MODULE}.GenerationStore"
    contracts: list[str] = []
    for operation in sorted(operations):
        if f"{owner}.{operation}" not in functions:
            message = f"GENERATION_STORE_MUTATION_METHODS: GenerationStore defines no {operation}"
            raise SystemExit(message)
        contracts.append(
            _table(
                "call-owners",
                name=f"store-op:{operation}",
                targets=[f"{STORE_MODULE}:GenerationStore.{operation}"],
                allowed_callers=_scope(STORE_MODULE, ""),
                unresolved="report",
                attribution="lexical",
            )
        )
    builder_module, _, builder = GRAPH_BUILDER.rpartition(".")
    return [
        *contracts,
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
                if _protocol_bases(model, scope):
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

    The constants above name the scopes; a name that no module of the package defines stops the build.
    """
    scopes = {
        (module, scope.qualname)
        for rel, module in _package_modules(repo).items()
        for scope in repo.model(rel).scopes
        if scope.kind in {"class", "function"}
    }
    for home in (FINAL_FACTS, FINAL_FACTS_SITE, PARSER):
        if (_module(home.path), home.name) not in scopes:
            message = f"{home.path} defines no {home.name}"
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


def in_host_scope(path: Path) -> bool:
    """Return whether the host contracts (and not ruff's SLF001, see pyproject.toml) own private access in a file.

    The scope is the files the SLF001 per-file ignores match, whose ``*`` also matches ``/``: every module below a
    ``parser/_*`` module or package, and ``model/_field_imports.py``.
    """
    try:
        relative = path.resolve().relative_to(SOURCE_ROOT).parts
    except ValueError:
        return False
    match relative:
        case ("parser", first, *_) if first.startswith("_"):
            return True
        case ("model", "_field_imports.py"):
            return True
    return False


def host_contracts(repo: Repo) -> list[str]:
    """Return the contracts of the host rule: the concern modules reach no private member of other classes.

    A private member is one with a single leading underscore (dunders are Python's protocol). The concern modules read,
    call or write such a member only through their own receiver (``self``, ``cls``, ``super()``) or a value typed with
    a ``*Host`` Protocol, whose declaration the member must be in; a class deriving from a Host is not one, and an
    access heki cannot tie to a class is reported.
    """
    targets: list[str] = []
    concerns: list[str] = []
    for rel, module in sorted(_package_modules(repo).items()):
        concern = in_host_scope(ROOT / rel)
        concerns += [f"{module}:*"] if concern else []
        model = repo.model(rel)
        targets += [
            f"{module}.{scope.qualname}._[!_]*"
            for scope in model.scopes
            if scope.kind == "class"
            and "<locals>" not in scope.qualname
            and (not concern or (_protocol_bases(model, scope) and not scope.qualname.endswith("Host")))
        ]
    targets = sorted(set(targets))
    keys = {
        "own_receiver": "allow",
        "allowed_protocols": ["*Host"],
        "protocol_subclasses": False,
        "unresolved": "report",
        "attribution": "lexical",
    }
    return [
        _table("read-owners", name="host-private-read", targets=targets, forbidden_readers=concerns, **keys),
        _table("call-owners", name="host-private-call", targets=targets, forbidden_callers=concerns, **keys),
        _table("write-owners", name="host-private-write", targets=targets, forbidden_writers=concerns, **keys),
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
"""The leaf layers of `_classify_source_path`: the modules of the package each may import besides the standard
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
    """Return the patterns of modules and every module below them."""
    return [pattern for module in modules for pattern in (module, f"{module}.*")]


def _listing(names: Iterable[str]) -> str:
    """Return names as an English list: ``a``, ``a and b``, ``a, b and c``."""
    *rest, last = names
    return f"{', '.join(rest)} and {last}" if rest else last


def import_layers(repo: Repo) -> dict[str, list[str]]:
    """Return the modules of the package by their layer (`_classify_source_path` of the architecture check)."""
    layers: dict[str, list[str]] = defaultdict(list)
    for rel, module in sorted(_package_modules(repo).items()):
        layers[_classify_source_path(SOURCE_ROOT / Path(rel).relative_to(PACKAGE_DIR))].append(module)
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
    others = sorted(m for found in layers.values() for m in found if m not in {PACKAGE, main})
    rows: list[tuple[str, list[str], dict[str, object], str]] = [
        ("root-import", others, {"forbidden_imports": [PACKAGE, main]}, _ROOT_IMPORT_MESSAGE),
        ("root-main-import", [PACKAGE], {"forbidden_imports": [main]}, _ROOT_IMPORT_MESSAGE),
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
            {"forbidden_imports": _below(*sorted(_TARGET_MODULES))},
            "model generation must not depend on target generation; targets depend on the core",
        ),
        (
            "private-parser-import",
            sorted(
                m for layer, found in layers.items() if layer not in _PARSER_LAYERS for m in found if m not in leaves
            ),
            {"forbidden_imports": [f"{PACKAGE}.parser._*", f"{PACKAGE}.parser.*._*"]},
            _PRIVATE_PARSER_IMPORT_MESSAGE,
        ),
    ]
    if not partial and (empty := [name for name, importers, _, _ in rows if not importers]):
        msg = f"import boundaries without a module of the package to judge: {', '.join(empty)}"
        raise ValueError(msg)
    return [
        _table(
            "import-boundaries",
            name=name,
            importers=importers,
            **keys,
            type_checking="report",
            unresolved="report",
            message=message,
        )
        for name, importers, keys, message in rows
        if importers
    ]


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
        self.index = PackageIndex()
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
    contracts = group_contracts([
        *write_owner_contracts(repo),
        *store_member_contracts(repo),
        *store_operation_contracts(repo),
        *decision_home_contracts(repo),
        *decision_writer_contracts(repo),
        *decision_contracts(repo),
        *import_ledger_contracts(repo),
        *builder_contracts(repo),
        *host_contracts(repo),
        *import_boundary_contracts(repo),
        *pipeline_contracts(repo),
        *shared_object_contracts(repo),
        *environment_contracts(),
        *finality_contracts(repo),
    ])
    head = "\n".join([
        HEADER,
        f"required-version = {_quote(required_version())}",
        "",
        "[lint]",
        f"select = {_array(RULES)}",
        f"fail-on = {_quote(FAIL_ON)}",
        "",
        "[imports]",
        f"cycle-edges = {_array(IMPORT_EDGES)}",
        "",
    ])
    return "\n".join([head, *contracts])


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
