"""Tests for the decision homes of scripts/decision_homes.py: homes and sites are definitions their rows name.

Only the home modules and the adapter sites of a row call its homes, or a caller the baseline still lists.
"""

from __future__ import annotations

import re
from collections import Counter
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pytest

from scripts.build_architecture_docs import DECISION_BEGIN_MARKER, DECISION_END_MARKER, generate_decision_table
from scripts.decision_homes import DECISION_HOMES, DecisionHome, Home
from scripts.where import DEFS, PACKAGE_DIR, SPAN, Page, Repo, scope_name
from tests.decision_home_allowlist import UNLISTED_CALLERS

if TYPE_CHECKING:
    from collections.abc import Iterable

ROOT = Path(__file__).parents[1]
PACKAGE = "datamodel_code_generator"


@cache
def _repo() -> Repo:
    """Return the package and the scripts as `scripts/where.py` reads them, through the source model."""
    return Repo(ROOT, Page([], [], [], []))


def _missing(home: Home) -> str | None:
    """Return what ``src/`` lacks of a home: its module, or a def, class or module-level assignment of its name.

    `Repo.definitions` finds a def or class by its qualified name and a module-level assignment by its name; an import
    defines nothing, so an imported name is no home.
    """
    path = f"src/{PACKAGE}/{home.path}"
    if path not in _repo().names:
        return f"{path} does not exist"
    if home.name and not any(found.qualname == home.name for found in _repo().definitions(home.name, path)):
        return f"{home.path} defines no {home.name}"
    return None


def _named(home: Home, cell: str) -> bool:
    """Return whether a cell names a home: its file in a code span, and the last part of its name in one."""
    spans = SPAN.findall(cell)
    files = {file for span in spans if (file := span.partition(":")[0]).endswith(".py")}
    words = {word for span in spans for word in re.findall(r"[A-Za-z_]\w*", span)}
    cited = any(home.path == file or home.path.endswith(f"/{file}") for file in files)
    return cited and (not home.name or home.name.rpartition(".")[2] in words)


def _problems(records: Iterable[DecisionHome]) -> list[str]:
    """Return what is wrong with records: a home or site src/ lacks, one its cell does not name, or no home at all."""
    problems: list[str] = []
    for record in records:
        key = f"{record.decision[:60]}..."
        if not record.homes:
            problems.append(f"{key}: no home")
        for kind, cell, homes in (("core", record.core, record.homes), ("adapters", record.adapters, record.sites)):
            for home in homes:
                if (missing := _missing(home)) is not None:
                    problems.append(f"{key}: {missing}")
                elif not _named(home, cell):
                    problems.append(f"{key}: the {kind} cell does not name {':'.join(filter(None, home))}")
                elif kind == "adapters" and not home.name and (named := _named_after(home, cell)):
                    problems.append(f"{key}: the adapters cell names `{named}` after {home.path}, no whole-module site")
    return problems


def _named_after(home: Home, cell: str) -> str:
    """Return the name a cell gives right after the file of a whole-module site (`` `f.py`: `x` ``, `` `f.py` (`x` ``).

    A cell states a whole module by citing its file with no name after it; a name it gives there that no scope
    carries (a constant, a parameter, an alias) must not widen the site to the module.
    """
    for match in re.finditer(r"`([^`:]+\.py)`(?::| \()\s*`([^`]+)`", cell):
        if home.path == match[1] or home.path.endswith(f"/{match[1]}"):
            return match[2]
    return ""


class Caller(NamedTuple):
    """A run-time read of a home outside the home modules and the adapter sites of every row that holds the home."""

    home: str
    """The home as ``path:name``."""
    caller: str
    """The scope that reads it as ``path:qualname`` (``<module>`` for the module's own code)."""
    line: int


def _callable_homes(records: Iterable[DecisionHome]) -> dict[Home, tuple[set[str], set[Home]]]:
    """Return the module-level homes of records with the modules and sites that may call each, over every row.

    A module home, a method (its calls are attribute reads that name resolution does not follow) and a module-level
    assignment (a constant or table) are not checked.
    """
    allowed: dict[Home, tuple[set[str], set[Home]]] = {}
    for record in records:
        for home in record.homes:
            if home.name and "." not in home.name:
                modules, sites = allowed.setdefault(home, (set(), set()))
                modules.update(other.path for other in record.homes)
                sites.update(record.sites)
    return allowed


def _covers(site: Home, module: str, scope: str) -> bool:
    """Return whether an adapter site holds a scope: its whole module, its def or class, or a scope within."""
    return site.path == module and (not site.name or scope == site.name or scope.startswith(f"{site.name}."))


def unlisted_callers(repo: Repo, records: Iterable[DecisionHome], package: str = PACKAGE_DIR) -> list[Caller]:
    """Return the run-time reads of a def or class home of records in a package outside the modules and sites.

    Paths of homes, sites and callers are relative to the package directory, ``src/datamodel_code_generator/`` unless
    given.

    `Repo.module_reads` finds the references; a store binds the name and one in an annotation names a type, so neither
    calls the home. A site is a def or a class (and the scopes inside it), or a whole module.
    """
    callers: list[Caller] = []
    for home, (modules, sites) in _callable_homes(records).items():
        path = f"{package}{home.path}"
        for found in repo.definitions(home.name, path) if path in repo.names else []:
            if found.qualname != home.name or found.parent != "module" or found.kind not in DEFS:
                continue
            for rel, references in repo.module_reads(found).items():
                module = rel.removeprefix(package)
                if not rel.startswith(package) or module in modules:
                    continue
                callers += [
                    Caller(f"{home.path}:{home.name}", f"{module}:{scope}", reference.line)
                    for reference in references
                    if reference.context == "load" and not reference.annotation
                    for scope in [scope_name(reference.scope)]
                    if not any(_covers(site, module, scope) for site in sites)
                ]
    return sorted(callers)


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_decision_homes_are_defined_in_src() -> None:
    """Every home of a record is a definition of src/ that the record's core cell names."""
    problems = _problems(DECISION_HOMES)

    assert not problems, "\n".join(problems)


@pytest.mark.allow_direct_assert("the guard of the homes must fail the test, not be stored as an expected output")
def test_only_the_home_modules_and_adapter_sites_call_a_home() -> None:
    """A home is called only by its row's home modules and adapter sites, or by a caller the baseline still lists.

    The baseline (`tests/decision_home_allowlist.py`) holds the callers of today's code that no row lists; it only
    shrinks (`scripts/check_ratchets.py` counts it), so an entry the code no longer has must go.
    """
    found = {(caller.home, caller.caller) for caller in unlisted_callers(_repo(), DECISION_HOMES)}
    listed = {(home, caller) for home, callers in UNLISTED_CALLERS.items() for caller in callers}
    new = [f"{caller} calls {home}" for home, caller in sorted(found - listed)]
    stale = [f"{caller} calls {home}" for home, caller in sorted(listed - found)]

    assert not new, (
        "calls of a home outside its row's modules and adapter sites: call it through an adapter site of the row, or "
        "name the caller in the row's Adapters cell and its sites (scripts/decision_homes.py):\n" + "\n".join(new)
    )
    assert not stale, "callers tests/decision_home_allowlist.py lists that no longer call: remove them\n" + "\n".join(
        stale
    )


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_decisions_are_recorded_once() -> None:
    """No two records hold the same decision."""
    repeated = [decision for decision, count in Counter(r.decision for r in DECISION_HOMES).items() if count > 1]

    assert not repeated, "\n".join(repeated)


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_the_page_table_is_written_from_the_records() -> None:
    """The decision table of docs/architecture.md is the table the records write, row for row."""
    docs = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    written = f"{DECISION_BEGIN_MARKER}{generate_decision_table()}{DECISION_END_MARKER}"

    assert written in docs, "docs/architecture.md: run python scripts/build_architecture_docs.py"


SEEDED = (
    DecisionHome(
        "seeded: homes src/ lacks",
        homes=(
            Home("types.py", "no_such_decision"),
            Home("no_such_module.py", "decide"),
            Home("types.py", "DataType.no_such_member"),
            Home("parser/_intersection.py", "json_values_equal"),
        ),
        core="`types.py`: `no_such_decision`, `DataType.no_such_member`; `no_such_module.py:decide`; "
        "`parser/_intersection.py:json_values_equal`",
        adapters="`types.py:no_such_site`",
        sites=(Home("types.py", "no_such_site"),),
    ),
    DecisionHome(
        "seeded: a home the core cell does not name",
        homes=(Home("types.py", "DataType.admits_none"), Home("_json_schema.py")),
        core="`types.py:DataType`",
        adapters="`types.py:DataType`",
        sites=(Home("types.py", "DataType.admits_none"),),
    ),
    DecisionHome("seeded: no home", homes=(), core="", adapters="", sites=()),
)
"""Records whose homes or sites src/ does not define, or that their cells do not name."""


@pytest.mark.allow_direct_assert("the problems of seeded records are the check's verdict, not a stored output")
def test_decision_home_check_fails_on_seeded_records() -> None:
    """A missing function, module or class member, an import, an unnamed home or site and no home are problems."""
    assert _problems(SEEDED) == [
        "seeded: homes src/ lacks...: types.py defines no no_such_decision",
        f"seeded: homes src/ lacks...: src/{PACKAGE}/no_such_module.py does not exist",
        "seeded: homes src/ lacks...: types.py defines no DataType.no_such_member",
        "seeded: homes src/ lacks...: parser/_intersection.py defines no json_values_equal",
        "seeded: homes src/ lacks...: types.py defines no no_such_site",
        "seeded: a home the core cell does not name...: the core cell does not name types.py:DataType.admits_none",
        "seeded: a home the core cell does not name...: the core cell does not name _json_schema.py",
        "seeded: a home the core cell does not name...: the adapters cell does not name types.py:DataType.admits_none",
        "seeded: no home...: no home",
    ]


SEEDED_PACKAGE = {
    "__init__.py": "from .core import decide\n",
    "core.py": (
        "class Rule:\n    def method(self) -> None:\n        pass\n\n\nTABLE = {}\n\n\n"
        "def decide() -> Rule:\n    return Rule()\n"
    ),
    "adapter.py": (
        "from .core import TABLE, Rule, decide\n\n\ndef site() -> None:\n    decide()\n\n\n"
        "class Site:\n    def nested(self) -> None:\n        decide()\n\n\n"
        "def typed(rule: Rule) -> object:\n    return TABLE\n\n\n"
        "def bypass(value: object) -> bool:\n    return isinstance(value, Rule) and decide() is not None\n"
    ),
    "whole.py": "from .core import decide\n\nRESULT = decide()\n",
    "through.py": (
        "from . import core\nfrom datamodel_code_generator import decide\n\n\n"
        "def by_module() -> None:\n    core.decide()\n\n\nby_package = decide()\n"
    ),
}
"""A package whose ``core.py`` holds the homes: callers by name, by module attribute and through a re-export."""


@pytest.mark.allow_direct_assert("the callers the guard finds in a seeded package are its verdict, not a stored output")
def test_unlisted_callers_on_a_seeded_package(tmp_path: Path) -> None:
    """Reads outside the home modules and sites are callers; a site's nested scopes, annotations and tables are not."""
    package = tmp_path / "src" / PACKAGE
    package.mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    for name, text in SEEDED_PACKAGE.items():
        (package / name).write_text(text, encoding="utf-8")
    record = DecisionHome(
        "seeded",
        homes=(
            Home("core.py", "decide"),
            Home("core.py", "Rule"),
            Home("core.py", "TABLE"),
            Home("core.py", "Rule.method"),
        ),
        core="",
        adapters="",
        sites=(Home("adapter.py", "site"), Home("adapter.py", "Site"), Home("whole.py")),
    )

    assert unlisted_callers(Repo(tmp_path, Page([], [], [], [])), [record]) == [
        Caller("core.py:Rule", "adapter.py:bypass", 18),
        Caller("core.py:decide", "adapter.py:bypass", 18),
        Caller("core.py:decide", "through.py:<module>", 9),
        Caller("core.py:decide", "through.py:by_module", 6),
    ]
