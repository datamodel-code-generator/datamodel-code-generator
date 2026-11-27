"""Tests for heki's contracts of the package: written from the records, and held against today's findings.

heki is a development dependency: the ``heki`` dependency group, which ``dev`` includes and of CI only the ``heki`` tox
environment installs, so the tests that run it need the ``heki`` capability. ``scripts/build_heki_contracts.py``
writes ``scripts/heki.toml``; ``scripts/heki_baseline.toml`` records today's findings, which only shrink
(``scripts/check_ratchets.py`` counts their sites).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import replace
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import tomllib

from scripts import build_heki_contracts, check_ratchets
from scripts.decision_homes import BuilderHome, DecisionHome, Home, Mark
from scripts.fact_tables import (
    ATTRIBUTE_FACTS,
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
)
from scripts.pass_decls import DECISION_DECLS, PASS_DECLS, PassDecl, PassKind, Scope
from tests.capabilities import needs
from tests.conftest import assert_output

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_heki_contracts.py"
CONTRACTS = ROOT / "scripts" / "heki.toml"
BASELINE = ROOT / "scripts" / "heki_baseline.toml"
PACKAGE = "src/datamodel_code_generator"

SEEDED_PYPROJECT = '[project]\nname = "seeded"\nrequires-python = ">=3.11"\n'
"""The project file of a seeded package."""
HEKI_SEEDED = Path(__file__).parent / "data" / "heki_seeded"
"""Seeded packages, each a ``src/`` tree whose modules say in their docstrings what the contracts report there."""


def _contracts_file(path: Path, contracts: Iterable[str], *, unmatched: bool = False) -> None:
    """Write ``contracts`` failing as scripts/heki.toml writes them, so their findings fail the run as they do there.

    With ``unmatched``, an entry that names nothing is what scripts/heki.toml makes it too.
    """
    lint = (
        ["[lint]", f'unmatched-contract-entries = "{build_heki_contracts.UNMATCHED_ENTRIES}"', ""] if unmatched else []
    )
    path.write_text("\n".join([*lint, *build_heki_contracts.failing(contracts)]), encoding="utf-8")


def _seeded_contracts(path: Path) -> Path:
    """Write the contracts of the repository to ``path/heki.toml`` with unmatched entries ignored, and return it.

    A seeded package has few of the classes and keys the contracts name, so the entries naming the others would be
    errors of their own, which fail the run whatever it finds.
    """
    unmatched = f'unmatched-contract-entries = "{build_heki_contracts.UNMATCHED_ENTRIES}"\n'
    text = CONTRACTS.read_text(encoding="utf-8").replace(unmatched, 'unmatched-contract-entries = "ignore"\n', 1)
    (config := path / "heki.toml").write_text(text, encoding="utf-8")
    return config


def _heki(*args: str, cwd: Path, config: Path = CONTRACTS) -> subprocess.CompletedProcess[str]:
    """Run heki of the environment (the heki dependency group installs it), with the contracts of the repository."""
    heki = shutil.which("heki", path=sysconfig.get_path("scripts")) or "heki"
    command = [heki, "check", "--config", str(config), *args, "src"]
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


@pytest.mark.allow_direct_assert("the contracts out of date must fail the test, not be stored as an expected output")
def test_heki_contracts_are_written_from_the_records() -> None:
    """scripts/heki.toml is what scripts/build_heki_contracts.py writes from today's records."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"], cwd=ROOT, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stdout + result.stderr


@needs("heki")
@pytest.mark.allow_direct_assert("the guard of the contracts must fail the test, not be stored as an expected output")
def test_the_package_has_no_heki_finding_beyond_the_baseline(tmp_path: Path) -> None:
    """No heki finding in src/ is missing from the baseline, and every entry still counts what heki finds.

    A shrink-update of a copy of the baseline fails on a new finding and rewrites an entry the code no longer reaches.
    """
    baseline = tmp_path / BASELINE.name
    shutil.copyfile(BASELINE, baseline)

    result = _heki("--baseline", str(baseline), "--update-baseline", "--output-format", "full", cwd=ROOT)

    assert result.returncode == 0, (
        "heki findings the baseline does not record: keep the write, read or call inside its owners "
        "(scripts/build_heki_contracts.py names the records each contract comes from); for a read of a decision "
        "home (a home: contract), go through an adapter site of its row, or name the reader in the row's Adapters "
        "cell and its sites (scripts/decision_homes.py); for a read of the "
        "environment (EFF201), key the decision on an option or a static rule (the help of each report). If the "
        "findings only moved "
        "(a function was renamed or moved), delete scripts/heki_baseline.toml and run heki check --config "
        "scripts/heki.toml --baseline scripts/heki_baseline.toml --update-baseline src; allowlist:heki-baseline "
        "of scripts/check_ratchets.py must not rise\n" + result.stdout + result.stderr
    )
    assert baseline.read_text(encoding="utf-8") == BASELINE.read_text(encoding="utf-8"), (
        "baseline entries the code no longer reaches: run heki check --config scripts/heki.toml --baseline "
        "scripts/heki_baseline.toml --update-baseline src\n" + result.stderr
    )


MODULE_LEVEL_CYCLES = """\
[lint]
select = ["IMP101"]
fail = ["IMP101"]

[imports]
cycle-edges = ["body"]
"""
"""heki's ``IMP101`` over the imports that run while their modules load, which the package holds to no cycle whatever
the baseline of the contracts keeps: such a cycle inside a group of modules that already import each other adds no
report of the contracts, which report a group once."""


@needs("heki")
@pytest.mark.allow_direct_assert("the guard of the package must fail the test, not be stored as an expected output")
def test_the_package_has_no_import_cycle_that_runs_while_its_modules_load(tmp_path: Path) -> None:
    """No import cycle of the package runs while its modules load: at module or class level, under a guard too."""
    config = tmp_path / "module_level_cycles.toml"
    config.write_text(MODULE_LEVEL_CYCLES, encoding="utf-8")

    result = _heki("--output-format", "full", cwd=ROOT, config=config)

    assert result.returncode == 0, (
        "an import here runs while its module loads and closes an import cycle; move what its modules share into a "
        "module that imports none of them\n" + result.stdout + result.stderr
    )


def _seeded(case: str, path: Path) -> Path:
    """Write the seeded package ``case`` of `HEKI_SEEDED` to ``path`` with its project file and an empty baseline.

    The fixtures are stored as ``.py.txt`` so that no scan reads them as package code; they are written as ``.py``, and
    every directory of modules gets the empty ``__init__.py`` the fixtures leave out.
    """
    for source in sorted((HEKI_SEEDED / case).rglob("*.py.txt")):
        target = path / source.relative_to(HEKI_SEEDED / case).with_suffix("")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for package in sorted({target.parent for target in path.rglob("*.py")}):
        (package / "__init__.py").touch()
    (path / "pyproject.toml").write_text(SEEDED_PYPROJECT, encoding="utf-8")
    (path / BASELINE.name).write_text("version = 3\n", encoding="utf-8")
    return path


def _reports(result: subprocess.CompletedProcess[str]) -> str:
    """Return the exit code and the reports of a heki run with JSON output as stored text, with each site count."""
    found = sorted(
        (
            Path(item["path"]).as_posix(),
            item["line"],
            item["rule_code"],
            item["qualname"],
            item["target"],
            item.get("sites"),
        )
        for item in json.loads(result.stdout)
    )
    errors = [f"{line}\n" for line in result.stderr.splitlines() if line.startswith("error:")]
    return f"exit: {result.returncode}\n" + "".join([
        *errors,
        *(
            f"{path}:{line}: {rule} {qualname} {target}{f' ({sites} sites)' if sites else ''}\n"
            for path, line, rule, qualname, target, sites in found
        ),
    ])


def _record_reports(
    case: str, path: Path, build: Callable[[build_heki_contracts.Repo], list[str]], *, unmatched: bool = False
) -> str:
    """Return the reports of the contracts ``build`` writes from seeded records over the seeded package ``case``."""
    _seeded(case, path)
    (path / "scripts").mkdir()
    contracts = path / "contracts.toml"
    _contracts_file(
        contracts,
        build(build_heki_contracts.Repo(path, build_heki_contracts.Page([], [], [], []))),
        unmatched=unmatched,
    )
    return _reports(_heki("--output-format", "json", cwd=path, config=contracts))


@needs("heki")
@pytest.mark.parametrize("case", ["cached_properties", "fact_writes", "import_cycles", "shared_objects"])
def test_heki_reports_the_seeded_violations(tmp_path: Path, case: str) -> None:
    """The contracts of the repository report what the docstrings of each seeded package say, and nothing else."""
    _seeded(case, tmp_path)

    result = _heki(
        "--baseline", BASELINE.name, "--output-format", "json", cwd=tmp_path, config=_seeded_contracts(tmp_path)
    )

    assert_output(_reports(result))


@needs("heki")
def test_the_module_level_guard_reports_only_the_cycle_that_runs_while_its_modules_load(tmp_path: Path) -> None:
    """The guard of the imports that run while their modules load reports the module-level cycle of the seeded ones."""
    (config := tmp_path / "module_level_cycles.toml").write_text(MODULE_LEVEL_CYCLES, encoding="utf-8")

    assert_output(_reports(_heki("--output-format", "json", cwd=_seeded("import_cycles", tmp_path), config=config)))


LAYER_FIXTURES = Path(__file__).parent / "data" / "architecture_boundaries"
SEEDED_LAYERS = {
    "entrypoint/forbidden.py": "_facade.py",
    "parser/forbidden.py": "parser/seeded.py",
    "parser_passes/forbidden.py": "parser/_passes/seeded.py",
    "parser_core/forbidden.py": "parser/_intersection.py",
    "facts/forbidden.py": "parser/_facts.py",
    "schema_graph/forbidden.py": "parser/_schema_graph.py",
    "config/forbidden.py": "config.py",
    "input_model/forbidden.py": "input_model.py",
    "reference/forbidden.py": "reference.py",
    "output_model/forbidden.py": "model/pydantic_v2/seeded.py",
    "shared_model/forbidden.py": "model/union.py",
    "shared_model/allowed.py": "model/types.py",
    "shared_model_dynamic_import/forbidden.py": "model/base.py",
    "shared_model_dynamic_import/allowed.py": "model/enum.py",
    "model_composition/allowed.py": "model/__init__.py",
    "shared/forbidden.py": "seeded_shared.py",
    "target/forbidden.py": "_target_config.py",
    "requirements/forbidden.py": "_requirements.py",
    "constraints/forbidden.py": "_constraint_applicability.py",
    "regex/forbidden.py": "_regex_syntax.py",
    "runtime/forbidden.py": "_runtime/seeded.py",
    "unjudged_dynamic_import/forbidden.py": "parser/dynamic.py",
    "unreadable/import.py.txt": "parser/unreadable.py",
}
"""The fixtures of the layers (``tests/data/architecture_boundaries``), each placed at a module of its layer."""
SEEDED_LAYER_STUBS = (
    "__init__.py",
    "_api_generation.py",
    "_api_types.py",
    "_format_types.py",
    "_json_schema.py",
    "_runtime/__init__.py",
    "_runtime/client.py",
    "_source.py",
    "_target_documents.py",
    "enums.py",
    "format.py",
    "imports.py",
    "model/dataclass.py",
    "model/field_name.py",
    "model/msgspec.py",
    "model/output.py",
    "model/pydantic_base.py",
    "model/pydantic_v2/__init__.py",
    "model/pydantic_v2/base_model.py",
    "model/typed_dict.py",
    "parser/__init__.py",
    "parser/_graph.py",
    "parser/_passes/__init__.py",
    "parser/base.py",
    "parser/generation.py",
    "parser/jsonschema.py",
    "parser/mcp.py",
    "parser/openapi.py",
    "types.py",
)
"""The empty modules the fixtures import: heki judges an import of a module the package has."""
SEEDED_LAYER_DEFINITIONS = {
    "_format_types.py": (
        "class PythonVersion:\n    has_typed_dict_closed = True\n    has_typed_dict_non_required = True\n\n"
        "    def _meets(self, construct):\n        return True\n"
    ),
    "_requirements.py": "\n\ndef python_target_meets(construct, version_key):\n    return True\n",
    "enums.py": "class DataModelType:\n    MsgspecStruct = 'msgspec.Struct'\n",
    "model/base.py": (
        "\n\nclass DataModel:\n    PLAIN_PATTERN_ROOT_TYPES = ()\n\n"
        "    def __init__(self):\n        self._internal_template_data = {}\n\n"
        "    def _append_internal_template_data(self, key, value):\n        pass\n\n\n"
        "class DataModelFieldBase:\n    pass\n"
    ),
    "model/pydantic_v2/base_model.py": (
        "from datamodel_code_generator.model.base import DataModel, DataModelFieldBase\n"
        "from datamodel_code_generator.types import DataTypeManager as NeutralDataTypeManager\n\n\n"
        "class BaseModel(DataModel):\n    SCHEMA_RUNTIME_VALIDATION_HELPERS_TEMPLATE_FILE_PATH = ''\n\n\n"
        "class DataModelField(DataModelFieldBase):\n    def is_pydantic_extra_field(self):\n        return False\n\n\n"
        "class DataTypeManager(NeutralDataTypeManager):\n    pass\n"
    ),
    "types.py": "class DataTypeManager:\n    pass\n",
    "model/typed_dict.py": "class TypedDict:\n    pass\n",
    "parser/base.py": (
        "class Parser:\n    _diagnostic_source_path = None\n    _formatter_cwd = None\n\n"
        "    def _dispose(self):\n        pass\n"
    ),
}
"""The definitions the architecture contracts name, appended to the modules of the seeded package that hold them.

A target names only what a class has, so the classes define each name the contracts list for them.
"""


@needs("heki")
def test_heki_reports_the_imports_that_cross_a_layer(tmp_path: Path) -> None:
    """Each import or read a layer may not make is a finding, however it is written; one heki cannot name is one too.

    The import boundaries and the architecture contracts come from the layers of `_package_layers.classify_source_path`.
    A read of a member only a backend reads, a backend named by module, an output family selected, a module of
    ``sys.modules`` read by shared model code and a private member of a parser reached from the entry point are
    findings, through ``getattr`` and ``hasattr`` too, and on a value heki cannot tie to a class by name. An import
    counts in a function, under ``TYPE_CHECKING``, guarded and by name (``importlib.import_module`` with a constant,
    an alias, a relative name and its package, ``__import__``); a by-name import of a name the code does not fix is
    ``IMP202``. The ``allowed.py`` fixtures and the composition root give none; a name bound to ``importlib`` on one
    branch and to another module on the other is no import heki can name.
    """
    found = _layer_import_reports(tmp_path, SEEDED_LAYER_STUBS, SEEDED_LAYERS)

    assert_output(found)


ROOT_IMPORT_FIXTURES = {
    "__init__.py": "__init__.py",
    "__main__.py": "__main__.py",
    "body.py": "body.py",
    "errors.py": "errors.py",
    "leaf.py": "_caches.py",
    "shadowed.py": "shadowed.py",
    "star.py": "_scc.py",
    "sub/__init__.py": "parser/__init__.py",
    "sub/module.py": "parser/module.py",
}
"""The package of the ``root_import`` fixture, each module at a module the root-import boundary names as importer."""


@needs("heki")
def test_heki_reports_every_import_of_the_package_root_and_main(tmp_path: Path) -> None:
    """No module below the root imports the root or ``__main__``, in any import form; its submodules may be read.

    The fixture is the package of the former checker rule: relative imports two levels up, a star import, a name the
    root binds to shadow a submodule, a name its ``__getattr__`` serves and by-name imports. Each import of a name of
    the root or of ``__main__`` is ``IMP201`` or ``IMP202``, and a read of a root name through a submodule import
    ``MUT204``, in a module of only a body too; ``__main__`` and the root's own re-exports give none.
    """
    places = {
        f"root_import/datamodel_code_generator/{fixture}": place for fixture, place in ROOT_IMPORT_FIXTURES.items()
    }
    found = _layer_import_reports(tmp_path, (), places)

    assert_output(found)


def test_the_import_boundaries_raise_where_the_layer_table_is_out_of_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A layer the check does not classify, or a rule no module of the package is subject to, is an error."""
    (tmp_path / PACKAGE).mkdir(parents=True)
    (tmp_path / PACKAGE / "__init__.py").write_text("", encoding="utf-8")
    repo = build_heki_contracts.Repo(tmp_path, build_heki_contracts.Page([], [], [], []))

    with pytest.raises(
        ValueError, match=r"^import boundaries without a module of the package to judge: root-import, facts-import,"
    ):
        build_heki_contracts.import_boundary_contracts(repo)
    build_heki_contracts.import_boundary_contracts(repo, partial=True)
    with pytest.raises(ValueError, match=r"^architecture contracts without a module of the package to judge: "):
        build_heki_contracts.architecture_contracts(repo)
    build_heki_contracts.architecture_contracts(repo, partial=True)
    monkeypatch.setitem(build_heki_contracts._LEAF_IMPORTS, "no-such-layer", ((), "why"))
    with pytest.raises(ValueError, match=r"classify: no-such-layer$"):
        build_heki_contracts.import_boundary_contracts(repo, partial=True)


@pytest.mark.parametrize("table", ["MODULE_NAME_READERS", "MODULE_REGISTRY_READERS", "ENTRYPOINT_PRIVATE_READERS"])
@pytest.mark.allow_direct_assert("a stale allowance must stop the build, not be stored as an expected output")
def test_the_architecture_allowances_name_a_function_the_package_defines(
    table: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An entry of an allowance table names a function the package defines, or its contract allows nothing."""
    repo = build_heki_contracts.Repo(build_heki_contracts.ROOT, build_heki_contracts.Page([], [], [], []))
    monkeypatch.setattr(build_heki_contracts, table, (*getattr(build_heki_contracts, table), "parser.base:gone"))

    with pytest.raises(
        ValueError, match=r"^architecture contracts allow functions the package does not define: parser\.base:gone$"
    ):
        build_heki_contracts.architecture_contracts(repo)


def _layer_import_reports(tmp_path: Path, stubs: tuple[str, ...], places: dict[str, str]) -> str:
    """Place the fixtures at modules of their layers and return the findings of the layer contracts as stored text."""
    package = tmp_path / PACKAGE
    for stub in stubs:
        (package / stub).parent.mkdir(parents=True, exist_ok=True)
        (package / stub).write_text("", encoding="utf-8")
    for fixture, place in places.items():
        (package / place).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(LAYER_FIXTURES / fixture, package / place)
    for place, definitions in SEEDED_LAYER_DEFINITIONS.items():
        if (package / place).is_file():
            with (package / place).open("a", encoding="utf-8") as module:
                module.write(definitions)
    (tmp_path / "scripts").mkdir()
    repo = build_heki_contracts.Repo(tmp_path, build_heki_contracts.Page([], [], [], []))
    contracts = tmp_path / "contracts.toml"
    _contracts_file(
        contracts,
        [
            *build_heki_contracts.import_boundary_contracts(repo, partial=True),
            *build_heki_contracts.architecture_contracts(repo, partial=True),
        ],
    )

    result = _heki("--output-format", "json", cwd=tmp_path, config=contracts)

    found = sorted(
        (Path(item["path"]).relative_to(PACKAGE).as_posix(), item["line"], item["rule_code"], item["target"])
        for item in json.loads(result.stdout)
    )
    return f"exit: {result.returncode}\n" + "".join(
        f"{path}:{line}: {rule} {target}\n" for path, line, rule, target in found
    )


def _key_row(owner: str, attribute: str, kind: FactKind, access: Access = Access.DICT_KEY) -> AttributeFact:
    """Return a row of `SEEDED_KEY_ROWS`: a fact or presentation row with its facts, else one with none."""
    facts = {FactKind.FACT: (Fact.FIELD_TYPES,), FactKind.PRESENTATION: (RenderFact.IMPORT_ALIASES,)}.get(kind, ())
    frozen = {FactKind.FACT: FreezePoint.B1, FactKind.PRESENTATION: FreezePoint.RENDER}.get(kind)
    return AttributeFact(owner, attribute, kind, facts, Phase.MODULES, frozen, access=access)


SEEDED_KEY_ROWS = [
    _key_row("DataType", "type", FactKind.FACT, Access.ATTRIBUTE),
    _key_row("DataModel", "fields", FactKind.FACT, Access.ATTRIBUTE),
    _key_row("DataModelFieldBase", "name", FactKind.FACT, Access.ATTRIBUTE),
    _key_row("Reference", "path", FactKind.FACT, Access.ATTRIBUTE),
    _key_row("DataType", "_LISTED_KEY", FactKind.PRESENTATION),
    _key_row("DataType", "_BOOKKEEPING_KEY", FactKind.BOOKKEEPING),
    _key_row("DataType", "_GONE_KEY", FactKind.PRESENTATION),
    _key_row("DataType", "_GONE_BOOKKEEPING_KEY", FactKind.BOOKKEEPING),
    _key_row("DataType", "_READ_ONLY_KEY", FactKind.CACHE),
    _key_row("DataModel", "gone_cached", FactKind.CACHE, Access.CACHED_PROPERTY),
    _key_row("DataType", "gone_attribute", FactKind.PRESENTATION, Access.ATTRIBUTE),
]
"""Rows for the seeded package: one attribute of each owner, a presentation and a bookkeeping key that a pass writes,
and five rows that name nothing the package has: a presentation key and a bookkeeping key no code writes, a cache
key only read, a cached property no class declares and an attribute no class declares."""


@needs("heki")
def test_heki_reports_unlisted_dict_keys_and_rows_that_name_nothing(tmp_path: Path) -> None:
    """A write of a ``__dict__`` key no row names is a finding, in every form; a row naming nothing is an error.

    On an owner of a row or a subclass of one the write is ``MUT201``, on a value heki cannot tie to a class
    ``MUT205``; a key a fact row names is judged by that row's contract, a bookkeeping key any function may write, and
    the store module any key. A write whose key heki cannot read, or a call on the namespace that may write one, is
    ``MUT205`` on the class. A mutation of an entry writes no key: the entry exists only after a write of its key, which
    is judged. The namespaces of a callable, a schema object and a class no row describes are none of the contracts'.
    """
    assert_output(
        _record_reports(
            "keys",
            tmp_path,
            lambda repo: build_heki_contracts.write_owner_contracts(repo, SEEDED_KEY_ROWS, ()),
            unmatched=True,
        )
    )


@pytest.mark.allow_direct_assert("the contracts' attribution must fail the test, not be stored as an expected output")
def test_record_contracts_judge_the_scope_that_makes_the_access() -> None:
    """Homes, builders, held parse methods, dispatch tables, fact rows and store members are judged where they are used.

    A call handed to a private helper is the helper's, not its caller's, as the baseline records it by the scope that
    holds it; a write is the function's that makes it, as heki names it with lexical attribution. A table that sets
    no attribution takes the one of the defaults table of its kind.
    """
    contracts = tomllib.loads(CONTRACTS.read_text(encoding="utf-8"))
    judged = [
        (table["name"], table.get("attribution", contracts.get(f"{kind}-defaults", {}).get("attribution")))
        for kind in ("call-owners", "read-owners", "write-owners")
        for table in contracts[kind]
        if table["name"].startswith((
            "home:",
            "builder:",
            "held:",
            "table:",
            "fact:",
            "presentation:",
            "cache:",
            "store-call:",
            "store-read:",
            "store-write:",
        ))
    ]

    assert {name.partition(":")[0] for name, _ in judged} == {
        "store-call",
        "store-read",
        "store-write",
        "home",
        "builder",
        "held",
        "table",
        "fact",
        "presentation",
        "cache",
    }
    assert [name for name, attribution in judged if attribution != "lexical"] == []


SEEDED_HOME_RECORD = DecisionHome(
    "seeded",
    homes=(
        Home("core.py", "decide"),
        Home("core.py", "Rule"),
        Home("core.py", "TABLE"),
        Home("core.py", "Rule.method"),
        Home("core.py", "Failure"),
        Home("core.py", "Detail"),
    ),
    core="",
    adapters="",
    sites=(Home("adapter.py", "site"), Home("adapter.py", "Site"), Home("whole.py")),
)


@needs("heki")
def test_heki_reports_reads_of_a_home_outside_its_modules_and_sites(tmp_path: Path) -> None:
    """A run-time read of a def or class home outside its row's home modules and sites is a finding; theirs is none.

    A call, an ``isinstance`` check, a module attribute and a re-export read a home; an annotation, a name imported
    only under ``if TYPE_CHECKING:``, a constant home and an exception home do not count. With lexical attribution a
    call handed to a private helper is the helper's.
    """
    assert_output(
        _record_reports(
            "homes", tmp_path, lambda repo: build_heki_contracts.decision_home_contracts(repo, [SEEDED_HOME_RECORD])
        )
    )


SEEDED_BUILDER_RECORDS = (
    BuilderHome(
        output="seeded: a parse of the table",
        prose="",
        builder=Home("parser/kinds.py", "Parser.parse_site"),
        table=Home("parser/kinds.py", "Parser._TABLE"),
    ),
    BuilderHome(
        output="seeded: a map",
        prose="",
        builder=Home("parser/maps.py", "build_map"),
        marks=(
            Mark("datamodel_code_generator.types.DataType", "is_dict", value=True),
            Mark("datamodel_code_generator.parser.maps.MapParser._container_flags", "slot", "dict"),
        ),
        parts=(Home("parser/maps.py", "part"),),
        within="parser/",
    ),
    BuilderHome(
        output="seeded: a named model",
        prose="",
        builder=Home("model.py", "name_model"),
        marks=(Mark("datamodel_code_generator.model.Ref"),),
    ),
)


@needs("heki")
def test_heki_reports_what_bypasses_a_builder_home(tmp_path: Path) -> None:
    """A parse method called outside its tables, a table read outside its builder, a marked output built elsewhere.

    Those are findings; the builders and their parts are none. A mark with an argument list is a call that binds the
    parameter to the constant, by keyword, by position or through a ``**`` dict display, under the record's ``within``
    only.
    """
    assert_output(
        _record_reports(
            "builders", tmp_path, lambda repo: build_heki_contracts.builder_contracts(repo, SEEDED_BUILDER_RECORDS)
        )
    )


@needs("heki")
def test_heki_reports_private_access_of_the_concern_modules(tmp_path: Path) -> None:
    """A concern module reads, calls or writes another class's private member only through its receiver or a Host.

    The host contracts report every other access, also by ``__dict__`` key or ``getattr`` name, and one heki cannot
    tie to a class; a module outside the concern modules is ruff's (SLF001).
    """
    assert_output(_record_reports("host_package", tmp_path, build_heki_contracts.host_contracts))


@pytest.mark.allow_direct_assert("a scope that drifts from the ruff ignores must fail the test, not be stored")
def test_host_contracts_cover_the_files_where_slf001_is_off() -> None:
    """The host contracts check exactly the files where the ruff per-file ignores turn SLF001 off.

    ruff's ``banned-api`` holds ``sys._getframe`` in the same files, which a negated per-file ignore names.
    """
    ignores = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["ruff"]["lint"][
        "per-file-ignores"
    ]
    globs = [glob for glob, rules in ignores.items() if glob.startswith("src/") and "private-member-access" in rules]
    (banned,) = [glob for glob, rules in ignores.items() if "banned-api" in rules]
    head, alternatives, tail = re.fullmatch(r"!(.*)\{(.*)\}(.*)", banned).groups()
    banned_globs = [f"{head}{alternative}{tail}" for alternative in alternatives.split(",")]

    def scopes(relative: str) -> tuple[bool, bool, bool]:
        return (
            any(fnmatchcase(relative, glob) for glob in globs),
            build_heki_contracts.in_host_scope(ROOT / relative),
            any(fnmatchcase(relative, glob) for glob in banned_globs),
        )

    named = {
        relative: scopes(f"{PACKAGE}/{relative}")
        for relative in (
            "parser/_new/x.py",
            "parser/_new/sub/y.py",
            "parser/_passes/sub/z.py",
            "parser/base.py",
            "parser/jsonschema.py",
            "model/_field_imports.py",
            "model/base.py",
        )
    }
    differing = [
        relative
        for path in sorted((ROOT / "src").rglob("*.py"))
        if len(set(scopes(relative := path.relative_to(ROOT).as_posix()))) > 1
    ]

    inside, outside = (True, True, True), (False, False, False)
    assert (named, scopes("tests/parser/_x.py"), differing) == (
        {
            "parser/_new/x.py": inside,
            "parser/_new/sub/y.py": inside,
            "parser/_passes/sub/z.py": inside,
            "parser/base.py": outside,
            "parser/jsonschema.py": outside,
            "model/_field_imports.py": inside,
            "model/base.py": outside,
        },
        outside,
        [],
    )


@needs("heki")
@pytest.mark.allow_direct_assert(
    "the rule names of the ratchet must fail the test, not be stored as an expected output"
)
def test_the_ratchet_knows_every_rule_of_the_pinned_heki() -> None:
    """The names and code families the ratchet reads in suppression comments are the rules the pinned heki lists."""
    heki = shutil.which("heki", path=sysconfig.get_path("scripts")) or "heki"
    result = subprocess.run(
        [heki, "rule", "--all", "--output-format", "json"], capture_output=True, text=True, check=False
    )

    rules = {item["slug"]: item["code"] for item in json.loads(result.stdout)}

    assert rules == check_ratchets.HEKI_RULES, result.stderr
    assert [code for code in rules.values() if not check_ratchets.HEKI_CODE.fullmatch(code)] == []


@pytest.mark.parametrize(
    ("member", "message"),
    [
        pytest.param(
            StoreMember("types.py", "DataType", "never_defined", MemberUse.CALL),
            "STORE_MEMBERS: types.py defines no method DataType.never_defined",
            id="method",
        ),
        pytest.param(
            StoreMember("reference.py", "Reference", "name", MemberUse.WRITE),
            "STORE_MEMBERS: Reference.name has no bookkeeping row in ATTRIBUTE_FACTS",
            id="attribute",
        ),
    ],
)
@pytest.mark.allow_direct_assert("a stale table row must stop the build, not be stored as an expected output")
def test_store_members_name_a_method_or_a_bookkeeping_row(
    monkeypatch: pytest.MonkeyPatch, member: StoreMember, message: str
) -> None:
    """A ``STORE_MEMBERS`` row names a method its class defines, or a bookkeeping row of ``ATTRIBUTE_FACTS``."""
    monkeypatch.setattr(build_heki_contracts, "STORE_MEMBERS", (member,))
    repo = build_heki_contracts.Repo(build_heki_contracts.ROOT, build_heki_contracts.Page([], [], [], []))

    with pytest.raises(SystemExit) as stopped:
        build_heki_contracts.store_member_contracts(repo)

    assert str(stopped.value) == message


@pytest.mark.allow_direct_assert("a stale producer must stop the build, not be stored as an expected output")
def test_shared_producers_name_a_function_the_package_defines() -> None:
    """A ``SHARED_PRODUCERS`` row names a function its module defines."""
    repo = build_heki_contracts.Repo(build_heki_contracts.ROOT, build_heki_contracts.Page([], [], [], []))

    with pytest.raises(SystemExit) as stopped:
        build_heki_contracts.shared_object_contracts(repo, [("parser/_schema_graph.py", "SchemaGraphBuilder.gone")])

    assert str(stopped.value) == "SHARED_PRODUCERS: parser/_schema_graph.py defines no function SchemaGraphBuilder.gone"


SEEDED_DECISION_DECLS = {
    name: replace(DECISION_DECLS[name], module=module, writer_methods=methods)
    for name, module, methods in (
        ("omit_held_null", "parser/_held_null.py", ("drop_held_null",)),
        ("omit_incomplete_model_defaults", "parser/_incomplete_defaults.py", ("omit_default",)),
    )
}


@needs("heki")
def test_heki_reports_a_decision_that_calls_the_write_of_another(tmp_path: Path) -> None:
    """A write of the decision writer is called only in the module of the decision that writes through it.

    A call from another decision's module, through a helper of another module or on an untyped receiver is a finding,
    and so is a call of a public method no decision names outside the decision phase.
    """
    assert_output(
        _record_reports(
            "decision_writes",
            tmp_path,
            lambda repo: build_heki_contracts.decision_writer_contracts(repo, SEEDED_DECISION_DECLS),
        )
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"module": ""}, "DECISION_DECLS: omit_held_null names no module or no write (module, writer_methods)"),
        ({"writer_methods": ()}, "DECISION_DECLS: omit_held_null names no module or no write (module, writer_methods)"),
    ],
    ids=["no-module", "no-write"],
)
@pytest.mark.allow_direct_assert("a stale table row must stop the build, not be stored as an expected output")
def test_decision_writes_name_a_method_of_the_writer(changes: dict[str, Any], message: str) -> None:
    """Each declaration of ``DECISION_DECLS`` names its module and writes; heki refuses a method naming nothing."""
    repo = build_heki_contracts.Repo(build_heki_contracts.ROOT, build_heki_contracts.Page([], [], [], []))
    decls = {"omit_held_null": replace(DECISION_DECLS["omit_held_null"], **changes)}

    with pytest.raises(SystemExit) as stopped:
        build_heki_contracts.decision_writer_contracts(repo, decls)

    assert str(stopped.value) == message


SEEDED_DECISION_READS = {
    "pyproject.toml": '[project]\nname = "seeded"\nrequires-python = ">=3.11"\n',
    f"{PACKAGE}/__init__.py": "",
    f"{PACKAGE}/types.py": (
        "class DataType:\n    def __init__(self) -> None:\n"
        "        self.type = 'str'\n        self.is_optional = False\n\n"
        "    @property\n    def admits_none(self) -> bool:\n        return self.is_optional\n"
    ),
    f"{PACKAGE}/model/__init__.py": "",
    f"{PACKAGE}/model/base.py": (
        "from datamodel_code_generator.types import DataType\n\n\n"
        "class DataModel:\n    def __init__(self) -> None:\n        self.class_name = 'Model'\n"
        "        self.data_type = DataType()\n\n\n"
        "class DataModelFieldBase:\n    def __init__(self) -> None:\n        self.extras: dict[str, object] = {}\n\n"
        "    @property\n    def has_default_factory(self) -> bool:\n        return 'default_factory' in self.extras\n"
    ),
    f"{PACKAGE}/model/helpers.py": (
        "from datamodel_code_generator.types import DataType\n\n\n"
        "def reads_render_fact(data_type: DataType) -> bool:\n    return data_type.is_optional\n"
    ),
    f"{PACKAGE}/parser/__init__.py": "",
    f"{PACKAGE}/parser/_phases.py": (
        "from datamodel_code_generator.model.base import DataModel\n\n\n"
        "class _DecisionWriter:\n    def rename(self, model: DataModel) -> str:\n        return model.class_name\n"
    ),
    f"{PACKAGE}/parser/_held_null.py": (
        "from datamodel_code_generator.model.base import DataModel, DataModelFieldBase\n"
        "from datamodel_code_generator.model.helpers import reads_render_fact\n"
        "from datamodel_code_generator.parser._phases import _DecisionWriter\n\n\n"
        "def reads_facts(model: DataModel, writer: _DecisionWriter) -> object:\n"
        "    writer.rename(model)\n    return model.data_type.type, model.data_type.admits_none\n\n\n"
        "def direct(model: DataModel) -> bool:\n    return model.data_type.is_optional\n\n\n"
        "def name(model: DataModel) -> str:\n    return model.class_name\n\n\n"
        "def through_a_helper(model: DataModel) -> bool:\n    return reads_render_fact(model.data_type)\n\n\n"
        "def untyped(data_type) -> bool:\n    return data_type.is_optional\n\n\n"
        "def factory(field: DataModelFieldBase) -> bool:\n    return field.has_default_factory\n\n\n"
        "def keyword_only(field: DataModelFieldBase) -> object:\n    return field.extras['kw_only']\n"
    ),
    f"{PACKAGE}/parser/_incomplete_defaults.py": "def decide() -> None:\n    pass\n",
}
"""A decision module that reads the facts of a model, the rendered nullability through an accessor and through the
decision writer, whether a field names a default factory through an accessor, and render facts: directly, the name of a
model, through a helper of another module, on an untyped value and an entry of a dict attribute."""
SEEDED_DECISION_ROWS = [
    replace(row, owner=owner)
    for row in ATTRIBUTE_FACTS
    for owner, attribute in (
        ("DataType", "is_optional"),
        ("DataModel", "class_name"),
        ("DataType", "type"),
        ("DataModelFieldBase", 'extras["kw_only"]'),
    )
    if (row.owner, row.attribute) == (owner, attribute)
]


@needs("heki")
@pytest.mark.allow_direct_assert("the findings heki gives on a seeded package are its verdict, not a stored output")
def test_heki_reports_a_decision_that_reads_a_render_fact(tmp_path: Path) -> None:
    """A decision reads only the facts final at B1: a render fact it reads, also through a helper, is a finding.

    Reads inside the decision writer and an accessor ``DECISION_ACCESSORS`` lists are theirs; a read of a render
    fact's attribute on an untyped value is one heki cannot tell, which it reports. An entry that holds a render fact
    makes its whole dict attribute one, as heki reads an entry as the attribute.
    """
    for name, text in SEEDED_DECISION_READS.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    repo = build_heki_contracts.Repo(tmp_path, build_heki_contracts.Page([], [], [], []))
    contracts = tmp_path / "contracts.toml"
    accessors = [
        DecisionAccessor("types.py", "DataType", "admits_none", "the rendered nullability"),
        DecisionAccessor("model/base.py", "DataModelFieldBase", "has_default_factory", "the default factory entry"),
    ]
    _contracts_file(
        contracts, build_heki_contracts.decision_contracts(repo, SEEDED_DECISION_DECLS, accessors, SEEDED_DECISION_ROWS)
    )
    heki = shutil.which("heki", path=sysconfig.get_path("scripts")) or "heki"

    result = subprocess.run(
        [heki, "check", "--config", str(contracts), "--output-format", "json", "src"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    found = sorted(
        (Path(item["path"]).name, item["qualname"], item["rule_code"], item["target"])
        for item in json.loads(result.stdout)
    )
    assert (result.returncode, found) == (
        1,
        [
            ("_held_null.py", "direct", "EFF201", "decisions-read-final-facts:reads:DataType.is_optional"),
            ("_held_null.py", "direct", "MUT203", "DataType.is_optional"),
            (
                "_held_null.py",
                "keyword_only",
                "EFF201",
                "decisions-read-final-facts:reads:DataModelFieldBase.extras",
            ),
            ("_held_null.py", "keyword_only", "MUT203", "DataModelFieldBase.extras"),
            ("_held_null.py", "name", "EFF201", "decisions-read-final-facts:reads:DataModel.class_name"),
            ("_held_null.py", "name", "MUT203", "DataModel.class_name"),
            ("_held_null.py", "through_a_helper", "EFF201", "decisions-read-final-facts:reads:DataType.is_optional"),
            ("_held_null.py", "untyped", "MUT206", "is_optional"),
        ],
    ), result.stderr


SEEDED_IMPORT_WRITES = {
    "pyproject.toml": '[project]\nname = "seeded"\nrequires-python = ">=3.11"\n',
    f"{PACKAGE}/__init__.py": "",
    f"{PACKAGE}/imports.py": (
        "class Imports:\n    def __init__(self) -> None:\n        self.names: list[str] = []\n\n"
        "    def append(self, name: str) -> None:\n        self.names.append(name)\n\n"
        "    def merged(self, other: 'Imports') -> 'Imports':\n        merged = Imports()\n"
        "        for name in other.names:\n            merged.append(name)\n        return merged\n"
    ),
    f"{PACKAGE}/parser/__init__.py": "",
    f"{PACKAGE}/parser/_imports_plan.py": (
        "from datamodel_code_generator.imports import Imports\n\n\n"
        "class ModuleImportLedger:\n    def append(self, imports: Imports, name: str) -> None:\n"
        "        imports.append(name)\n"
    ),
    f"{PACKAGE}/parser/_init_exports.py": (
        "from datamodel_code_generator.imports import Imports\n\n\n"
        "def build_all_exports_code(imports: Imports) -> None:\n    imports.append('re-export')\n\n\n"
        "def beside_the_writer(imports: Imports) -> None:\n    imports.append('not the writer')\n"
    ),
    f"{PACKAGE}/parser/passes.py": (
        "from typing import Any, cast\n\n"
        "from datamodel_code_generator.imports import Imports\n\n\n"
        "class ModuleContext:\n    def __init__(self) -> None:\n        self.imports = Imports()\n\n\n"
        "def direct(ctx: ModuleContext) -> None:\n    ctx.imports.append('os')\n\n\n"
        "def _add(imports: Imports) -> None:\n    imports.append('os')\n\n\n"
        "def through_a_helper(ctx: ModuleContext) -> None:\n    _add(ctx.imports)\n\n\n"
        "def through_a_cast(ctx: ModuleContext) -> None:\n    cast('Any', ctx).imports.append('os')\n\n\n"
        "def untyped(ctx: Any) -> None:\n    ctx.imports.append('os')\n"
    ),
}
"""The ledger and the imports module change an ``Imports``, and so does a ``LEDGER_WRITERS`` def; another def of the
writer's module, a pass directly, through a helper or a ``cast``, and a pass on an untyped value change it too."""


@needs("heki")
@pytest.mark.allow_direct_assert("the findings heki gives on a seeded package are its verdict, not a stored output")
def test_heki_reports_imports_written_outside_the_ledger(tmp_path: Path) -> None:
    """A method that changes an ``Imports`` is called only in the ledger, the ``imports`` module and the writers.

    A call in another def, also of a writer's module, is a finding in the scope that makes it, a helper's included;
    a ``cast`` keeps the class heki knows; one on a value only ``Any`` types is not seen (a gap of heki, which a
    write-owners form would close).
    """
    for name, text in SEEDED_IMPORT_WRITES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    repo = build_heki_contracts.Repo(tmp_path, build_heki_contracts.Page([], [], [], []))
    contracts = tmp_path / "contracts.toml"
    writers = [LedgerWriter("parser/_init_exports.py", "build_all_exports_code", "re-exports")]
    _contracts_file(contracts, build_heki_contracts.import_ledger_contracts(repo, ("append",), writers))
    heki = shutil.which("heki", path=sysconfig.get_path("scripts")) or "heki"

    result = subprocess.run(
        [heki, "check", "--config", str(contracts), "--output-format", "json", "src"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    found = sorted(
        (Path(item["path"]).name, item["qualname"], item["rule_code"], item["target"].rpartition(".")[2])
        for item in json.loads(result.stdout)
    )
    assert (result.returncode, found) == (
        1,
        [
            ("_init_exports.py", "beside_the_writer", "MUT204", "append"),
            ("passes.py", "_add", "MUT204", "append"),
            ("passes.py", "direct", "MUT204", "append"),
            ("passes.py", "through_a_cast", "MUT204", "append"),
        ],
    ), result.stderr


@pytest.mark.allow_direct_assert("a stale table row must stop the build, not be stored as an expected output")
def test_import_ledger_rows_name_defined_methods() -> None:
    """``LEDGER_WRITERS`` names defs the package defines; heki refuses an ``IMPORTS_MUTATORS`` row naming nothing."""
    repo = build_heki_contracts.Repo(build_heki_contracts.ROOT, build_heki_contracts.Page([], [], [], []))
    writers = (LedgerWriter("parser/base.py", "Parser.never_defined", "none"),)

    with pytest.raises(SystemExit) as stopped:
        build_heki_contracts.import_ledger_contracts(repo, (), writers)

    assert str(stopped.value) == "LEDGER_WRITERS: parser/base.py defines no Parser.never_defined"


SEEDED_STORE_CALLS = {
    "pyproject.toml": '[project]\nname = "seeded"\nrequires-python = ">=3.11"\n',
    f"{PACKAGE}/__init__.py": "",
    f"{PACKAGE}/parser/__init__.py": "",
    f"{PACKAGE}/parser/generation.py": (
        "class GenerationStore:\n    def __init__(self) -> None:\n        self._builder = GraphBuilder(self)\n\n"
        "    def builder(self) -> 'GraphBuilder':\n        return self._builder\n\n"
        "    def omit_field_default(self, field: object) -> None:\n        self.omitted = field\n\n\n"
        "class GraphBuilder:\n    def __init__(self, store: GenerationStore) -> None:\n        self._store = store\n\n"
        "    def omit_field_default(self, field: object) -> None:\n        self._store.omit_field_default(field)\n"
    ),
    f"{PACKAGE}/parser/passes.py": (
        "from typing import Any, cast\n\n"
        "from datamodel_code_generator.parser.generation import GenerationStore, GraphBuilder\n\n\n"
        "def through_the_builder(store: GenerationStore) -> None:\n    store.builder().omit_field_default(1)\n\n\n"
        "def direct(store: GenerationStore) -> None:\n    store.omit_field_default(1)\n\n\n"
        "def _helper(store: GenerationStore) -> None:\n    store.omit_field_default(1)\n\n\n"
        "def through_a_helper(store: GenerationStore) -> None:\n    _helper(store)\n\n\n"
        "def through_a_cast(store: GenerationStore) -> None:\n    cast('Any', store).omit_field_default(1)\n\n\n"
        "def untyped(store: Any) -> None:\n    store.omit_field_default(1)\n\n\n"
        "def own_builder(store: GenerationStore) -> GraphBuilder:\n    return GraphBuilder(store)\n"
    ),
}
"""A pass that changes the graph through the store's builder, and one that calls a store operation directly, in a
helper, through a ``cast``, on an untyped value, or that makes a builder of its own."""


@needs("heki")
@pytest.mark.allow_direct_assert("the findings heki gives on a seeded package are its verdict, not a stored output")
def test_heki_reports_store_operations_called_outside_the_builder(tmp_path: Path) -> None:
    """A store operation is called only in the store module, and only the store module makes a ``GraphBuilder``.

    A call elsewhere, a helper's in the helper, is a finding, and so is one on a value heki cannot tie to a class.
    """
    for name, text in SEEDED_STORE_CALLS.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    contracts = tmp_path / "contracts.toml"
    _contracts_file(contracts, build_heki_contracts.store_operation_contracts(("omit_field_default",)))
    heki = shutil.which("heki", path=sysconfig.get_path("scripts")) or "heki"

    result = subprocess.run(
        [heki, "check", "--config", str(contracts), "--output-format", "json", "src"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    found = sorted(
        (Path(item["path"]).name, item["qualname"], item["rule_code"], item["target"].rpartition(".")[2])
        for item in json.loads(result.stdout)
    )
    assert (result.returncode, found) == (
        1,
        [
            ("passes.py", "_helper", "MUT204", "omit_field_default"),
            ("passes.py", "direct", "MUT204", "omit_field_default"),
            ("passes.py", "own_builder", "MUT204", "generation:GraphBuilder"),
            ("passes.py", "through_a_cast", "MUT204", "omit_field_default"),
            ("passes.py", "untyped", "MUT207", "omit_field_default"),
        ],
    ), result.stderr


@pytest.mark.allow_direct_assert("a stale pipeline home must stop the build, not be stored as an expected output")
def test_pipeline_contracts_name_the_site_the_package_defines(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one function the pipeline contracts let build ``FinalFacts`` exists in the package."""
    monkeypatch.setattr(build_heki_contracts, "FINAL_FACTS_SITE", Home("parser/base.py", "NeverDefined"))
    repo = build_heki_contracts.Repo(build_heki_contracts.ROOT, build_heki_contracts.Page([], [], [], []))

    with pytest.raises(SystemExit) as stopped:
        build_heki_contracts.pipeline_contracts(repo)

    assert str(stopped.value) == "parser/base.py defines no NeverDefined"


PHASES = {decl.phase.name: decl.phase for decl in PASS_DECLS}
"""The phases of the pipeline by name, as the declarations name them."""


def _seeded_pass(name: str, phase: str, *writes: Fact | RenderFact) -> PassDecl:
    return PassDecl(name, PHASES[phase], Scope.MODULE, PassKind.TRANSFORM, writes=frozenset(writes))


SEEDED_PASS_DECLS = (
    _seeded_pass("parse_raw", "PARSE"),
    _seeded_pass("declared", "MODULES", Fact.METADATA),
    _seeded_pass("undeclared", "MODULES", Fact.REQUIRED),
    _seeded_pass("after_final", "CROSS_MODULE", Fact.METADATA),
    _seeded_pass("keyed", "MODULES", Fact.REQUIRED),
    _seeded_pass("frozen_key", "IMPORTS", Fact.REQUIRED, RenderFact.KEYWORD_ONLY),
    _seeded_pass("enters_phase", "MODULES"),
)
"""``parse_raw`` is a pass of L1 and ``declared`` declares what it writes. ``undeclared`` writes ``METADATA`` through a
helper and declares ``REQUIRED``; ``after_final`` writes ``read_only`` after ``MODULES``; ``frozen_key`` writes the
``kw_only`` entry after B2 (and the ``init`` entry after its ``final_after``); ``enters_phase`` changes the phase."""
SEEDED_PASS_ROWS = [
    row
    for row in ATTRIBUTE_FACTS
    if (row.owner, row.attribute)
    in {
        ("DataModelFieldBase", "read_only"),
        ("DataModelFieldBase", "extras"),
        ("DataModelFieldBase", 'extras["init"]'),
        ("DataModelFieldBase", 'extras["kw_only"]'),
    }
]


@needs("heki")
def test_heki_reports_a_pass_that_writes_beyond_its_declaration(tmp_path: Path) -> None:
    """A pass after L1 writes only the facts it declares, before they are final, and the glue between passes none.

    A write through a helper counts for the pass, an entry a store operation writes is the entry its key names, and the
    phase is the pipeline's to enter.
    """
    assert_output(
        _record_reports(
            "passes",
            tmp_path,
            lambda repo: build_heki_contracts.finality_contracts(repo, SEEDED_PASS_DECLS, {}, SEEDED_PASS_ROWS),
        )
    )


@pytest.mark.parametrize(
    ("tables", "message"),
    [
        (
            'PIPELINE = (PassSpec("declared", lambda self, run: run.rows.clear()),)\n',
            f"{build_heki_contracts.TABLES}: cannot tell which function `run.rows.clear` calls",
        ),
        (
            'PIPELINE = (PassSpec("declared", late(steps.never_defined)),)\n',
            (
                f"{build_heki_contracts.TABLES}: datamodel_code_generator.parser._passes.steps.never_defined is no "
                "function or class of the package"
            ),
        ),
        (
            'PIPELINE = (PassSpec("other", late(steps.declared)),)\n',
            f"declared: no step of {build_heki_contracts.TABLES} and no decision runs it",
        ),
    ],
    ids=["unknown-callee", "undefined-function", "undeclared-step"],
)
@pytest.mark.allow_direct_assert("a table the builder cannot read must stop the build, not be stored as an output")
def test_pass_boundaries_name_the_functions_each_step_runs(tmp_path: Path, tables: str, message: str) -> None:
    """The builder reads the function every pass of the tables runs; one it cannot tell stops the build."""
    _seeded("passes", tmp_path)
    (tmp_path / f"{PACKAGE}/parser/_passes/_tables.py").write_text(
        "from datamodel_code_generator.parser._passes import steps\n"
        "from datamodel_code_generator.parser._pipeline import PassSpec, late\n\n" + tables,
        encoding="utf-8",
    )
    (tmp_path / "scripts").mkdir()
    repo = build_heki_contracts.Repo(tmp_path, build_heki_contracts.Page([], [], [], []))

    with pytest.raises(SystemExit) as stopped:
        build_heki_contracts.finality_contracts(repo, SEEDED_PASS_DECLS[1:2], {}, SEEDED_PASS_ROWS)

    assert str(stopped.value) == message


@pytest.mark.allow_direct_assert("a stale decision must stop the build, not be stored as an expected output")
def test_pass_boundaries_name_the_function_each_decision_runs() -> None:
    """Each decision of ``DECISION_DECLS`` is a ``DecisionPass`` of the module it declares."""
    repo = build_heki_contracts.Repo(build_heki_contracts.ROOT, build_heki_contracts.Page([], [], [], []))
    decls = {"omit_held_null": replace(DECISION_DECLS["omit_held_null"], module="parser/_unique_sets.py")}

    with pytest.raises(SystemExit) as stopped:
        build_heki_contracts.finality_contracts(repo, (), decls)

    assert str(stopped.value) == "DECISION_DECLS: parser/_unique_sets.py makes no DecisionPass named omit_held_null"


@pytest.mark.allow_direct_assert("a boundary without the help of its kind must fail, not be stored as an output")
def test_each_effect_boundary_carries_the_help_its_ratchet_prints() -> None:
    """Each effect boundary's message is the help of its kind, which the rise of its baseline sites prints too.

    A boundary that sets no message takes the one of ``[effect-boundaries-defaults]``.
    """
    contracts = tomllib.loads(CONTRACTS.read_text(encoding="utf-8"))
    default = contracts.get("effect-boundaries-defaults", {}).get("message")
    boundaries = contracts["effect-boundaries"]
    targets = [
        entry["target"]
        for entry in tomllib.loads(BASELINE.read_text(encoding="utf-8"))["entries"]
        if entry["rule_code"] in check_ratchets.HEKI_OWN_KEY_RULES
    ]
    kinds = {check_ratchets.boundary_kind(boundary["name"]) for boundary in boundaries}

    assert {
        boundary["name"]: boundary.get("message", default)
        for boundary in boundaries
        if boundary.get("message", default)
        != check_ratchets.BOUNDARY_ADVICE.get(check_ratchets.boundary_kind(boundary["name"]))
    } == {}
    assert kinds == set(check_ratchets.BOUNDARY_ADVICE)
    assert {check_ratchets.boundary_kind(target) for target in targets} <= kinds


@pytest.mark.allow_direct_assert("a finding the baseline keeps without a reason must fail, not be stored as an output")
def test_the_baseline_keeps_the_known_pass_findings_only() -> None:
    """The findings of the pass boundaries in the baseline are those ``KNOWN_FINDINGS`` lists with what removes them.

    A new finding needs its entry there, and a finding the code no longer makes leaves both (heki shrinks the baseline).
    """
    entries = tomllib.loads(BASELINE.read_text(encoding="utf-8"))["entries"]
    kept = sorted(
        entry["target"]
        for entry in entries
        if entry["rule_code"] == "EFF201" and entry["target"].startswith(("pass:", "pass-glue:"))
    )

    assert kept == sorted(finding.target for finding in build_heki_contracts.KNOWN_FINDINGS)
