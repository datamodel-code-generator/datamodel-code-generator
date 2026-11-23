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
  holds it. A ``__dict__`` key row names the value of its constant; a
  cached-property row also forbids the decorator on its owner.
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
- ``read-owners`` (``MUT203``): each recorded dispatch table, read only by its builder and by the scopes that only ask
  it for ``typed_children`` (`scripts._decision_home_reads.typed_children_readers`).
- ``read-owners``, ``call-owners`` and ``write-owners`` of the host rule (``MUT201``, ``MUT203``, ``MUT204``): the
  concern modules (`in_host_scope`: where pyproject.toml turns ruff's SLF001 off) reach a private member (one leading
  underscore, no dunder) of a class outside them, or of a Protocol inside them that is no ``*Host``, only through the
  receiver of the method that holds the access or a value typed with a ``*Host`` Protocol declaring the member
  (``MUT205``, ``MUT206``, ``MUT207`` report what heki cannot tie to a class). heki names no member of a module
  outside the package, so ruff's ``banned-api`` holds ``sys._getframe`` in these modules instead.
- ``effect-boundaries`` (``EFF201``): no function or module body of the package reads the environment it runs in
  (the interpreter's version, the platform, a variable, an installed package), guarded imports included: output that
  depends on the environment cost review rounds. Each report carries the advice of the ratchet key that counts these
  sites, ``allowlist:heki-baseline:EFF201`` of ``scripts/check_ratchets.py``.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._decision_home_reads import checked_homes, held_parse_methods, typed_children_readers
from scripts.check_architecture_boundaries import PackageIndex, _protocol_bases, bases
from scripts.check_generation_store_usage import PARSER_STATE_OWNERS
from scripts.check_ratchets import ENVIRONMENT_ADVICE
from scripts.decision_homes import BUILDER_HOMES, DECISION_HOMES, Home, Mark
from scripts.fact_tables import ATTRIBUTE_FACTS, STORE_MEMBERS, Access, FactKind, MemberUse, StoreMember
from scripts.where import ASSIGNS, PACKAGE, PACKAGE_DIR, Page, Repo

if TYPE_CHECKING:
    from collections.abc import Iterable

    from scripts.decision_homes import BuilderHome, DecisionHome

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "scripts" / "heki.toml"
SOURCE_ROOT = ROOT / PACKAGE_DIR
STORE_MODULE = f"{PACKAGE}.parser.generation"
"""The generation store's module: its functions write the facts the store operations write."""
WRITE_KINDS = (FactKind.FACT, FactKind.PRESENTATION, FactKind.CACHE)
RULES = ("EFF201", "MUT201", "MUT203", "MUT204", "MUT205", "MUT206", "MUT207")
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


def _store_methods(repo: Repo) -> dict[StoreMember, list[str]]:
    """Return the ``CALL`` rows of ``STORE_MEMBERS``, each with the scopes of the method and of its overrides.

    An override is a method of that name in a class of the package that derives from the row's class, through bases
    read by origin.
    """
    index = PackageIndex()
    classes: dict[tuple[str, str], list[str]] = {}
    functions: set[str] = set()
    for rel, module in _package_modules(repo).items():
        model = repo.model(rel)
        for scope in model.scopes:
            if scope.kind == "class" and "<locals>" not in scope.qualname:
                classes[module, scope.qualname] = [
                    index.resolve(o) for base in bases(scope) if (o := model.origin(base))
                ]
            elif scope.kind == "function":
                functions.add(f"{module}.{scope.qualname}")
    bases_of = {f"{module}.{qualname}": parents for (module, qualname), parents in classes.items()}

    def derives(name: str, root: str) -> bool:
        todo, seen = [name], set()
        while todo:
            if (current := todo.pop()) == root:
                return True
            seen.add(current)
            todo += [parent for parent in bases_of.get(current, []) if parent not in seen]
        return False

    methods: dict[StoreMember, list[str]] = {}
    for member in STORE_MEMBERS:
        if member.use is not MemberUse.CALL:
            continue
        owner = f"{_module(member.path)}.{member.owner}"
        if f"{owner}.{member.name}" not in functions:
            message = f"STORE_MEMBERS: {member.path} defines no method {member.owner}.{member.name}"
            raise SystemExit(message)
        methods[member] = sorted(
            f"{module}:{qualname}.{member.name}"
            for module, qualname in classes
            if f"{module}.{qualname}.{member.name}" in functions and derives(f"{module}.{qualname}", owner)
        )
    return methods


def _store_hooks(repo: Repo) -> list[str]:
    """Return the scopes of the methods that write the graph for the store operations, overrides included."""
    return sorted({scope for scopes in _store_methods(repo).values() for scope in scopes})


def write_owner_contracts(repo: Repo) -> list[str]:
    """Return the ``write-owners`` contracts of the fact rows."""
    hooks = _store_hooks(repo)
    groups: dict[tuple[str, tuple[str, ...]], list[str]] = defaultdict(list)
    cached_on: set[str] = set()
    for row in ATTRIBUTE_FACTS:
        if row.kind not in WRITE_KINDS or (row.owner in PARSER_STATE_OWNERS and not row.store_ops):
            continue
        if row.access is Access.CACHED_PROPERTY:
            cached_on.add(row.owner)
        groups[row.kind.value, tuple(sorted(row.store_ops))].append(
            f"{row.owner}.{_attribute(repo, row.attribute, row.access)}"
        )
    contracts: list[str] = []
    for (kind, operations), targets in sorted(groups.items()):
        cached = sorted(owner for owner in cached_on if kind == FactKind.CACHE.value)
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


def generate() -> str:
    """Return the contents of scripts/heki.toml."""
    repo = Repo(ROOT, Page([], [], [], []))
    contracts = [
        *write_owner_contracts(repo),
        *store_member_contracts(repo),
        *decision_home_contracts(repo),
        *builder_contracts(repo),
        *host_contracts(repo),
        *environment_contracts(),
    ]
    return "\n".join([f"{HEADER}\n[lint]\nselect = {_array(RULES)}\n", *contracts])


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
