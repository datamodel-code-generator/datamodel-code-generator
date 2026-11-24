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
from typing import Any

import pytest
import tomllib

from scripts import build_heki_contracts, check_ratchets
from scripts.decision_homes import BuilderHome, DecisionHome, Home, Mark
from scripts.fact_tables import ATTRIBUTE_FACTS, DecisionAccessor, LedgerWriter, MemberUse, StoreMember
from scripts.pass_decls import DECISION_DECLS
from tests.capabilities import needs

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_heki_contracts.py"
CONTRACTS = ROOT / "scripts" / "heki.toml"
BASELINE = ROOT / "scripts" / "heki_baseline.toml"
PACKAGE = "src/datamodel_code_generator"
PACKAGE_NAME = "datamodel_code_generator"

SEEDED_ENVIRONMENT = """\
import os
import platform
import sys
from importlib import import_module, util

try:
    import tomllib
except ImportError:
    tomllib = None
try:
    from yaml import CDumper, CSafeLoader
except ImportError:
    CDumper = CSafeLoader = None
try:
    import_module("orjson")
except ModuleNotFoundError:
    pass
try:
    import ujson
except (ImportError, AttributeError):
    ujson = None
try:
    import tomli
except ImportError:
    import tomllib as tomli

HOME = os.environ.get("HOME")
EDITOR = os.getenv("EDITOR")
FOUND = util.find_spec("yaml")
WINDOWS = sys.platform == "win32" or os.name == "nt"
HEX = sys.hexversion
SYSTEM = platform.system()


def old_python() -> bool:
    return sys.version_info < (3, 12)


class Platform:
    DARWIN = sys.platform == "darwin"


def lazy_import() -> None:
    try:
        import csv
    except Exception:
        return
    csv.reader([])
"""
"""The forms of reading the environment, in a module body, a function and a class body: 14 sites of ``<module>``.

Guarded imports (an import, a ``from`` import of two names, which are two probes, an ``import_module`` call under
``ModuleNotFoundError``, an import under a tuple of handlers, one more import whose fallback import in the handler is
none), ``os.environ``, ``os.getenv``, ``importlib.util.find_spec``, ``sys.platform``, ``os.name``, ``sys.hexversion``,
``platform.system()``, and ``sys.platform`` in a class body; ``old_python`` reads ``sys.version_info``. The lazy
import of ``lazy_import`` under ``except Exception`` reads nothing.
"""

SEEDED_PACKAGE = {
    "pyproject.toml": '[project]\nname = "seeded"\nrequires-python = ">=3.11"\n',
    f"{PACKAGE}/__init__.py": "",
    f"{PACKAGE}/model/__init__.py": "",
    f"{PACKAGE}/model/base.py": (
        "class DataModel:\n    def __init__(self) -> None:\n        self.fields: list[str] = []\n"
        "        self.set_base_class()\n\n"
        "    def set_base_class(self) -> None:\n        self.fields = []\n"
    ),
    f"{PACKAGE}/reference.py": (
        "class Reference:\n    def __init__(self) -> None:\n"
        "        self.children: list[object] = []\n        self.loaded = True\n"
    ),
    f"{PACKAGE}/parser/__init__.py": "",
    f"{PACKAGE}/parser/generation.py": (
        "from datamodel_code_generator.model.base import DataModel\n\n\n"
        "class GenerationStore:\n"
        "    def set_fields(self, model: DataModel, fields: list[str]) -> None:\n        model.fields = fields\n\n"
        "    def reset_base_classes(self, model: DataModel) -> None:\n        model.set_base_class()\n"
    ),
    f"{PACKAGE}/parser/base.py": (
        "class Parser:\n    def __init__(self) -> None:\n        self.results: list[str] = []\n"
    ),
    f"{PACKAGE}/parser/seeded.py": (
        "from datamodel_code_generator.model.base import DataModel\n"
        "from datamodel_code_generator.parser.base import Parser\n"
        "from datamodel_code_generator.reference import Reference\n\n\n"
        'def rename_fields(model: DataModel) -> None:\n    model.fields = ["renamed"]\n\n\n'
        'def register(parser: Parser) -> None:\n    parser.results.append("model")\n\n\n'
        "def rebase(model: DataModel) -> None:\n    model.set_base_class()\n\n\n"
        "def users(reference: Reference) -> list[object]:\n    return reference.children\n\n\n"
        "def unload(reference: Reference) -> None:\n    reference.loaded = False\n"
    ),
    f"{PACKAGE}/parser/seeded_env.py": SEEDED_ENVIRONMENT,
}
"""A package whose ``seeded.py`` writes ``DataModel.fields`` and the run-state list ``Parser.results`` outside the
store operations that their rows name, calls ``DataModel.set_base_class``, which only the store runs, and reads and
writes the bookkeeping of a ``Reference``.

``GenerationStore.set_fields``, one of those operations, writes ``DataModel.fields`` too, ``Parser.__init__``
builds ``results``, the store and the model's own initialization call ``set_base_class``, which writes ``fields`` for
the store, and ``Reference.__init__`` builds its bookkeeping: heki allows them all. ``seeded_env.py`` holds
`SEEDED_ENVIRONMENT`, which the environment boundary forbids in functions and module bodies.
"""


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


@needs("heki")
@pytest.mark.allow_direct_assert("the findings heki gives on a seeded package are its verdict, not a stored output")
def test_heki_reports_fact_writes_outside_the_store_operations_and_environment_reads(tmp_path: Path) -> None:
    """A write of a fact row's attribute outside the store operations the row names is a finding; theirs is none.

    A call of a method only the store runs (``STORE_MEMBERS``) outside the store and the method's class is a finding,
    and so are a read and a write of a reference's bookkeeping outside its module.

    A read of the environment the package runs in is a finding too, in a function and in a module body, counted per
    site.
    """
    for name, text in SEEDED_PACKAGE.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")

    result = _heki("--output-format", "json", cwd=tmp_path)

    found = sorted(
        (Path(item["path"]).as_posix(), item["qualname"], item["rule_code"], item["target"], item.get("sites") or 1)
        for item in json.loads(result.stdout)
    )
    environment = "no-environment-reads:reads-environment"
    assert (result.returncode, found) == (
        1,
        [
            (
                f"{PACKAGE}/parser/seeded.py",
                "rebase",
                "MUT204",
                "datamodel_code_generator.model.base.DataModel.set_base_class",
                1,
            ),
            (f"{PACKAGE}/parser/seeded.py", "register", "MUT201", "Parser.results", 1),
            (f"{PACKAGE}/parser/seeded.py", "rename_fields", "MUT201", "DataModel.fields", 1),
            (f"{PACKAGE}/parser/seeded.py", "unload", "MUT201", "Reference.loaded", 1),
            (f"{PACKAGE}/parser/seeded.py", "users", "MUT203", "Reference.children", 1),
            (f"{PACKAGE}/parser/seeded_env.py", "<module>", "EFF201", environment, 14),
            (f"{PACKAGE}/parser/seeded_env.py", "old_python", "EFF201", environment, 1),
        ],
    ), result.stderr


SEEDED_CYCLES = {
    f"{PACKAGE}/__init__.py": "",
    f"{PACKAGE}/body_a.py": "from datamodel_code_generator import body_b\n\nVALUE = body_b\n",
    f"{PACKAGE}/body_b.py": "from datamodel_code_generator import body_a\n\nVALUE = body_a\n",
    f"{PACKAGE}/function_a.py": "from datamodel_code_generator import function_b\n\nVALUE = function_b\n",
    f"{PACKAGE}/function_b.py": (
        "def load() -> object:\n    from datamodel_code_generator import function_a\n\n    return function_a\n"
    ),
    f"{PACKAGE}/typing_a.py": "from datamodel_code_generator import typing_b\n\nVALUE = typing_b\n",
    f"{PACKAGE}/typing_b.py": (
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from datamodel_code_generator import typing_a\n"
    ),
    f"{PACKAGE}/one_way_a.py": "from datamodel_code_generator import one_way_b\n\nVALUE = one_way_b\n",
    f"{PACKAGE}/one_way_b.py": "VALUE = 1\n",
}
"""Three pairs of modules that import each other, by an import at module level, in a function and under
``TYPE_CHECKING``, and a pair that imports one way."""
MODULE_LEVEL_CYCLES = """\
[lint]
select = ["IMP101"]
fail-on = "warning"

[imports]
cycle-edges = ["body"]
"""
"""heki's ``IMP101`` over the imports that run while their modules load, which the package holds to no cycle whatever
the baseline of the contracts keeps: such a cycle inside a group of modules that already import each other adds no
report of the contracts, which report a group once."""


def _cycle_reports(result: subprocess.CompletedProcess[str]) -> list[tuple[str, str, str]]:
    """Return the path, rule and target of each report of a heki run with JSON output."""
    return sorted(
        (Path(item["path"]).as_posix(), item["rule_code"], item["target"]) for item in json.loads(result.stdout)
    )


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


@needs("heki")
@pytest.mark.allow_direct_assert("the findings heki gives on a seeded package are its verdict, not a stored output")
def test_heki_reports_the_import_cycles_of_every_import(tmp_path: Path) -> None:
    """A cycle fails the check whether the import that closes it runs while its module loads, in a function or never.

    Each report names the modules of its cycle, which a baseline entry keeps. The guard of the imports that run while
    their modules load reports only the first.
    """
    for name, text in SEEDED_CYCLES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / BASELINE.name).write_text("version = 2\n", encoding="utf-8")
    (config := tmp_path / "module_level_cycles.toml").write_text(MODULE_LEVEL_CYCLES, encoding="utf-8")

    result = _heki("--baseline", BASELINE.name, "--output-format", "json", cwd=tmp_path)
    module_level = _heki("--output-format", "json", cwd=tmp_path, config=config)

    body = (f"{PACKAGE}/body_b.py", "IMP101", f"{PACKAGE_NAME}.body_a, {PACKAGE_NAME}.body_b")
    assert (result.returncode, _cycle_reports(result)) == (
        1,
        [
            body,
            (f"{PACKAGE}/function_b.py", "IMP101", f"{PACKAGE_NAME}.function_a, {PACKAGE_NAME}.function_b"),
            (f"{PACKAGE}/typing_b.py", "IMP101", f"{PACKAGE_NAME}.typing_a, {PACKAGE_NAME}.typing_b"),
        ],
    ), result.stderr
    assert (module_level.returncode, _cycle_reports(module_level)) == (1, [body]), module_level.stderr


SEEDED_SHARED = {
    f"{PACKAGE}/__init__.py": "",
    f"{PACKAGE}/parser/__init__.py": "",
    f"{PACKAGE}/parser/_schema_graph.py": (
        'class Schema:\n    def __init__(self) -> None:\n        self.title = ""\n\n\n'
        "class SchemaGraphBuilder:\n    def __init__(self) -> None:\n        self.shared = Schema()\n\n"
        "    def schema(self) -> Schema:\n        return self.shared\n"
    ),
    f"{PACKAGE}/parser/reader.py": (
        "from datamodel_code_generator.parser._schema_graph import Schema, SchemaGraphBuilder\n\n\n"
        "def read(graph: SchemaGraphBuilder) -> str:\n    return graph.schema().title\n\n\n"
        'def write(graph: SchemaGraphBuilder) -> None:\n    graph.schema().title = "written"\n\n\n'
        "def write_through_a_helper(graph: SchemaGraphBuilder) -> None:\n    _rename(graph.schema())\n\n\n"
        'def _rename(schema: Schema) -> None:\n    schema.title = "written"\n\n\n'
        'def write_a_parameter(schema: Schema) -> None:\n    schema.title = "written"\n'
    ),
}
"""A package whose ``reader.py`` reads, and writes, the object that ``SchemaGraphBuilder.schema`` shares.

``write_through_a_helper`` hands the shared object to a function that writes it; ``write_a_parameter`` writes a
parameter that no call of the package gives the shared object, which heki does not follow.
"""


@needs("heki")
@pytest.mark.allow_direct_assert("the findings heki gives on a seeded package are its verdict, not a stored output")
def test_heki_reports_a_write_of_an_object_the_schema_graph_shares(tmp_path: Path) -> None:
    """A function that writes the object ``SchemaGraphBuilder.schema`` returns fails the check; a reader does not.

    The write through a helper that is given the object fails too, as the helper is a function that mutates it.
    """
    for name, text in SEEDED_SHARED.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / BASELINE.name).write_text("version = 2\n", encoding="utf-8")

    result = _heki("--baseline", BASELINE.name, "--output-format", "json", cwd=tmp_path)

    found = sorted(
        (Path(item["path"]).as_posix(), item["qualname"], item["rule_code"]) for item in json.loads(result.stdout)
    )
    assert (result.returncode, found) == (
        1,
        [
            (f"{PACKAGE}/parser/reader.py", "write", "MUT202"),
            (f"{PACKAGE}/parser/reader.py", "write_through_a_helper", "MUT202"),
        ],
    ), result.stderr


SEEDED_WRITE_FORMS = """\
from copy import deepcopy

from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
from datamodel_code_generator.parser.generation import GenerationStore


def through_the_store(store: GenerationStore, model: DataModel) -> None:
    store.set_fields(model, list(model.fields))


def reorder(model: DataModel) -> None:
    model.fields.reverse()
    model.fields[:] = model.fields[::-1]
    model.fields[0] = model.fields[0]
    model.fields.insert(0, model.fields.pop())
    model.fields.sort(reverse=True)


def reorder_a_copy(model: DataModel) -> None:
    copied = deepcopy(model.fields)
    copied.reverse()


def mark_through_an_alias(field: DataModelFieldBase) -> None:
    extras = field.extras
    extras["seen"] = True


def rebase_in_a_callback(models: list[DataModel]) -> list[None]:
    return list(map(lambda owner: owner.set_base_class(), models))


def set_base_class(model: DataModel) -> None:
    model.fields = []


def clear_untyped(models):
    for model in models:
        model.fields = []
"""
"""Writes of fact rows by a pass: through a store operation, by every list mutator, to a deep copy, through a container
alias, by a store-only method that a callback calls on an untyped receiver, by a function that only shares the name
of such a method, and through a receiver whose class heki cannot tell."""
SEEDED_FIELD_CLASS = """

class DataModelFieldBase:
    def __init__(self) -> None:
        self.extras: dict[str, object] = {}
"""
"""A field class for the seeded ``model/base.py``, whose ``extras`` a write form aliases."""


@needs("heki")
@pytest.mark.allow_direct_assert("the findings heki gives on a seeded package are its verdict, not a stored output")
def test_heki_reports_every_form_of_a_fact_write_outside_the_store(tmp_path: Path) -> None:
    """Each list mutator, a write through an alias and a store-only call in a callback are findings, once per call.

    The write of the store operation and the copy, which no graph object holds, are none, though a pass that calls the
    operation itself, not through the store's builder, is; a function named like a method only the store runs is
    judged by the attribute it writes, and a write through a receiver heki cannot resolve is ``MUT205``.
    """
    package = {
        **SEEDED_PACKAGE,
        f"{PACKAGE}/model/base.py": SEEDED_PACKAGE[f"{PACKAGE}/model/base.py"] + SEEDED_FIELD_CLASS,
        f"{PACKAGE}/parser/forms.py": SEEDED_WRITE_FORMS,
    }
    for name, text in package.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")

    result = _heki("--output-format", "json", cwd=tmp_path)

    found = [
        (item["qualname"], item["rule_code"], item["target"], item["line"])
        for item in json.loads(result.stdout)
        if Path(item["path"]).name == "forms.py"
    ]
    assert (result.returncode, found) == (
        1,
        [
            ("through_the_store", "MUT204", f"{PACKAGE_NAME}.parser.generation:GenerationStore.set_fields", 8),
            ("reorder", "MUT201", "DataModel.fields", 12),
            ("reorder", "MUT201", "DataModel.fields", 13),
            ("reorder", "MUT201", "DataModel.fields", 14),
            ("reorder", "MUT201", "DataModel.fields", 15),
            ("reorder", "MUT201", "DataModel.fields", 15),
            ("reorder", "MUT201", "DataModel.fields", 16),
            ("mark_through_an_alias", "MUT201", "DataModelFieldBase.extras", 26),
            ("rebase_in_a_callback", "MUT207", "set_base_class", 30),
            ("set_base_class", "MUT201", "DataModel.fields", 34),
            ("clear_untyped", "MUT205", "fields", 39),
        ],
    ), result.stderr


@pytest.mark.allow_direct_assert("the contracts' attribution must fail the test, not be stored as an expected output")
def test_record_contracts_judge_the_scope_that_makes_the_access() -> None:
    """Homes, builders, held parse methods, dispatch tables, fact rows and store members are judged where they are used.

    A call handed to a private helper is the helper's, not its caller's, as the baseline records it by the scope that
    holds it; a write is the function's that makes it, as heki names it with lexical attribution.
    """
    contracts = tomllib.loads(CONTRACTS.read_text(encoding="utf-8"))
    judged = [
        (table["name"], table.get("attribution"))
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


SEEDED_HOMES = {
    "pyproject.toml": '[project]\nname = "seeded"\nrequires-python = ">=3.11"\n',
    f"{PACKAGE}/__init__.py": "from .core import decide\n",
    f"{PACKAGE}/core.py": (
        "class Rule:\n    def method(self) -> None:\n        pass\n\n\nTABLE = {}\n\n\n"
        "def decide() -> Rule:\n    return Rule()\n\n\n"
        "class Failure(Exception):\n    pass\n\n\nclass Detail(Failure):\n    pass\n"
    ),
    f"{PACKAGE}/adapter.py": (
        "from .core import TABLE, Rule, decide\n\n\ndef site() -> None:\n    decide()\n\n\n"
        "class Site:\n    def nested(self) -> None:\n        decide()\n\n\n"
        "def typed(rule: Rule) -> object:\n    return TABLE\n\n\n"
        "def bypass(value: object) -> bool:\n    return isinstance(value, Rule) and decide() is not None\n\n\n"
        "def run_through_helper() -> Rule:\n    return _helper()\n\n\n"
        "def _helper() -> Rule:\n    return decide()\n"
    ),
    f"{PACKAGE}/whole.py": "from .core import decide\n\nRESULT = decide()\n",
    f"{PACKAGE}/raises.py": (
        "from .core import Detail, Failure\n\n\n"
        "def raised() -> None:\n    raise Failure('x')\n\n\n"
        "def caught() -> bool:\n    try:\n        raise Detail\n    except Failure as error:\n"
        "        return isinstance(error, Detail)\n"
    ),
    f"{PACKAGE}/through.py": (
        "from typing import TYPE_CHECKING\n\nfrom . import core\nfrom datamodel_code_generator import decide\n\n"
        "if TYPE_CHECKING:\n    from .core import Rule\n\n    Checked = Rule | None\n\n\n"
        "def by_module() -> None:\n    core.decide()\n\n\nby_package = decide()\n"
    ),
}
"""A package whose ``core.py`` holds the homes: read by name, by module attribute and through a re-export.

``adapter.py`` holds the sites ``site`` and ``Site``, ``whole.py`` is a site as a whole module. ``bypass`` reads
``Rule`` and calls ``decide`` outside them, and ``run_through_helper`` hands the call to a private helper. ``typed``
names ``Rule`` in an annotation and reads ``TABLE``, a constant home. ``raises.py`` raises and catches the exception
homes ``Failure`` and ``Detail`` (a subclass of it), and ``through.py`` reads ``Rule`` in a type alias under ``if
TYPE_CHECKING:``, where it imports ``Rule``.
"""
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
@pytest.mark.allow_direct_assert("the findings heki gives on a seeded package are its verdict, not a stored output")
def test_heki_reports_reads_of_a_home_outside_its_modules_and_sites(tmp_path: Path) -> None:
    """A run-time read of a def or class home outside its row's home modules and sites is a finding; theirs is none.

    A call, an ``isinstance`` check, a module attribute and a re-export read a home; an annotation, a name imported
    only under ``if TYPE_CHECKING:``, a constant home and an exception home do not count. With lexical attribution a
    call handed to a private helper is the helper's.
    """
    for name, text in SEEDED_HOMES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    repo = build_heki_contracts.Repo(tmp_path, build_heki_contracts.Page([], [], [], []))
    contracts = tmp_path / "contracts.toml"
    contracts.write_text(
        "\n".join(build_heki_contracts.decision_home_contracts(repo, [SEEDED_HOME_RECORD])), encoding="utf-8"
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
        (Path(item["path"]).name, item["qualname"], item["rule_code"], item["target"].rpartition(".")[2])
        for item in json.loads(result.stdout)
    )
    assert (result.returncode, found) == (
        1,
        [
            ("adapter.py", "_helper", "MUT204", "decide"),
            ("adapter.py", "bypass", "MUT204", "Rule"),
            ("adapter.py", "bypass", "MUT204", "decide"),
            ("through.py", "<module>", "MUT204", "decide"),
            ("through.py", "by_module", "MUT204", "decide"),
        ],
    ), result.stderr


SEEDED_BUILDERS = {
    "pyproject.toml": '[project]\nname = "seeded"\nrequires-python = ">=3.11"\n',
    f"{PACKAGE}/__init__.py": "",
    f"{PACKAGE}/types.py": (
        "class DataType:\n    def __init__(self, *, is_dict: bool = False, is_list: bool = False) -> None:\n"
        "        self.is_dict = is_dict\n        self.is_list = is_list\n"
    ),
    f"{PACKAGE}/parser/__init__.py": "",
    f"{PACKAGE}/parser/_schema_dispatch.py": (
        "class Row:\n    def __init__(self, name: str, applies: object, build: object) -> None:\n"
        "        self.build = build\n\n\n"
        "class Default:\n    def __init__(self, name: str, build: object) -> None:\n        self.build = build\n\n\n"
        "class DispatchTable:\n    def __init__(self, entries: tuple[Row, ...], default: Default) -> None:\n"
        "        self.entries = entries\n\n"
        "    def choose(self, parser: object, site: object) -> object:\n        return self.entries[0].build\n\n"
        "    def typed_children(self, parser: object, site: object) -> tuple[Row, ...]:\n"
        "        return self.entries\n"
    ),
    f"{PACKAGE}/parser/kinds.py": (
        "from functools import partial\nfrom typing import Protocol\n\n"
        "from datamodel_code_generator.parser._schema_dispatch import Default, DispatchTable, Row\n\n\n"
        "def _shared(parser: 'Parser', site: object) -> object:\n    return parser.parse_object(site)\n\n\n"
        "def _then(build: object) -> object:\n    return build\n\n\n"
        "class Parser:\n"
        "    def _build_array(self, site: object) -> object:\n"
        "        return self._build_enum(site) or self._build_default(site)\n\n"
        "    def _build_enum(self, site: object) -> object:\n        return site\n\n"
        "    def _build_default(self, site: object) -> object:\n        return self._build_array(site)\n\n"
        "    def parse_object(self, site: object) -> object:\n        return _shared(self, site)\n\n"
        "    _ROWS = (Row('enum', None, _then(_build_enum)),)\n"
        "    _TABLE = DispatchTable(\n"
        "        (Row('array', None, build=partial(_build_array)), *_ROWS, Row('shared', None, _shared)),\n"
        "        Default('default', _build_default),\n    )\n\n"
        "    def parse_site(self, site: object) -> object:\n        return self._TABLE.choose(self, site)\n\n"
        "    def shortcut(self, site: object) -> object:\n        return self._TABLE.choose(self, site)\n\n"
        "    def parameter(self, _build_enum: object) -> object:\n        return _build_enum\n\n"
        "    def alias(self, site: object) -> object:\n"
        "        table = self._TABLE\n        return table.choose(self, site)\n\n\n"
        "class KindsHost(Protocol):\n    _TABLE: DispatchTable\n\n"
        '    def _build_enum(self, site: object) -> object:\n        """The parse method of the enum row."""\n\n\n'
        "def via_host(parser: KindsHost, site: object) -> object:\n"
        "    return parser._build_enum(parser._TABLE.choose(parser, site))\n\n\n"
        "def typed(parser: KindsHost, site: object) -> object:\n"
        "    return parser._TABLE.typed_children(parser, site)\n"
    ),
    f"{PACKAGE}/parser/maps.py": (
        "from typing import Protocol\n\nfrom datamodel_code_generator.types import DataType\n\n\n"
        "class MapsHost(Protocol):\n"
        '    @property\n    def data_type(self) -> type[DataType]:\n        """The data type class."""\n\n'
        "    def _container_flags(self, obj: object, slot: str) -> dict[str, bool]:\n"
        '        """The flags of a container slot."""\n\n\n'
        "class MapParser:\n"
        "    @property\n    def data_type(self) -> type[DataType]:\n        return DataType\n\n"
        "    def _container_flags(self, obj: object, slot: str) -> dict[str, bool]:\n"
        "        return {'is_dict': slot == 'dict'}\n\n\n"
        "def build_map(parser: MapsHost) -> DataType:\n    return part(parser)\n\n\n"
        "def part(parser: MapsHost) -> DataType:\n    return parser.data_type(is_dict=True)\n\n\n"
        "def flags(parser: MapsHost) -> DataType:\n    return parser.data_type(**{'is_dict': True})\n\n\n"
        "def handed_on() -> dict[str, bool]:\n    return {'is_dict': True, 'is_list': False}\n\n\n"
        "def not_a_map(parser: MapsHost) -> DataType:\n    return parser.data_type(is_dict=False)\n\n\n"
        "class MapType(DataType):\n    pass\n\n\n"
        "def subclass_map() -> DataType:\n    return MapType(is_dict=True)\n\n\n"
        "def seeded_slot(parser: MapsHost, obj: object) -> DataType:\n"
        "    return parser.data_type(**parser._container_flags(obj, 'dict'))\n\n\n"
        "def other_slot(parser: MapsHost, obj: object) -> dict[str, bool]:\n"
        "    return parser._container_flags(obj, 'list')\n"
    ),
    f"{PACKAGE}/model.py": (
        "from pydantic import BaseModel\n\n"
        "from datamodel_code_generator.types import DataType\n\n\n"
        "class Ref(BaseModel):\n    pass\n\n\n"
        "def name_model() -> Ref:\n    return Ref()\n\n\n"
        "def outside() -> DataType:\n    return DataType(is_dict=True)\n"
    ),
    f"{PACKAGE}/naming.py": (
        "from datamodel_code_generator.model import Ref as Named\n\n\n"
        "def rename() -> Named:\n    return Named()\n\n\n"
        "def construct() -> Named:\n    return Named.model_construct()\n"
    ),
    f"{PACKAGE}/other.py": "class Ref:\n    pass\n\n\ndef local() -> Ref:\n    return Ref()\n",
}
"""A package with a dispatch table in ``parser/kinds.py``, an object-map builder in ``parser/maps.py`` and a builder of
``Ref`` in ``model.py``.

``Parser._build_array`` calls the parse methods of the ``enum`` row (held through the shared rows and a wrapper) and of
the default outside the table, ``_build_default`` calls the one of the ``array`` row (held through ``partial``),
``parse_object`` calls ``_shared``, the parse method of another row, and ``shortcut`` and ``alias`` read the table
outside ``parse_site``; ``parameter`` reads a parameter spelled as a parse method. ``via_host`` calls the parse method
and the table's ``choose`` through the host protocol ``KindsHost``, and ``typed`` only asks the table for
``typed_children``. ``build_map`` hands the map to its part ``part``; ``flags`` builds the dict flag through a ``**``
dict display and ``seeded_slot`` asks ``_container_flags`` for the ``dict`` slot outside them, both through the host
protocol ``MapsHost``, which ``MapParser`` implements (heki ties a call through a protocol to a class only where one
implements it), and ``subclass_map`` builds one as a subclass of ``DataType``. ``not_a_map``, ``other_slot`` (another
slot) and ``outside`` (outside ``parser/``) build no map, and ``handed_on`` returns a flag dict that no call takes
there. ``naming.py`` builds a ``Ref`` of ``model.py`` through an alias and by ``model_construct`` outside
``name_model``; ``other.py`` builds a class of its own of that name.
"""
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
@pytest.mark.allow_direct_assert("the findings heki gives on a seeded package are its verdict, not a stored output")
def test_heki_reports_what_bypasses_a_builder_home(tmp_path: Path) -> None:
    """A parse method called outside its tables, a table read outside its builder, a marked output built elsewhere.

    Those are findings; the builders and their parts are none. A mark with an argument list is a call that binds the
    parameter to the constant, by keyword, by position or through a ``**`` dict display, under the record's ``within``
    only.
    """
    for name, text in SEEDED_BUILDERS.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    repo = build_heki_contracts.Repo(tmp_path, build_heki_contracts.Page([], [], [], []))
    contracts = tmp_path / "contracts.toml"
    contracts.write_text(
        "\n".join(build_heki_contracts.builder_contracts(repo, SEEDED_BUILDER_RECORDS)), encoding="utf-8"
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
        (Path(item["path"]).name, item["qualname"], item["rule_code"], item["target"].removeprefix(f"{PACKAGE_NAME}."))
        for item in json.loads(result.stdout)
    )
    assert (result.returncode, found) == (
        1,
        [
            ("kinds.py", "Parser._build_array", "MUT204", "parser.kinds.Parser._build_default"),
            ("kinds.py", "Parser._build_array", "MUT204", "parser.kinds.Parser._build_enum"),
            ("kinds.py", "Parser._build_default", "MUT204", "parser.kinds.Parser._build_array"),
            ("kinds.py", "Parser.alias", "MUT203", "parser.kinds.Parser._TABLE"),
            ("kinds.py", "Parser.parse_object", "MUT204", "parser.kinds._shared"),
            ("kinds.py", "Parser.shortcut", "MUT203", "parser.kinds.Parser._TABLE"),
            ("kinds.py", "via_host", "MUT203", "parser.kinds.KindsHost._TABLE"),
            ("kinds.py", "via_host", "MUT204", "parser.kinds.KindsHost._build_enum"),
            ("maps.py", "flags", "MUT204", "types.DataType(is_dict=True)"),
            ("maps.py", "seeded_slot", "MUT204", 'parser.maps.MapsHost._container_flags(slot="dict")'),
            ("maps.py", "subclass_map", "MUT204", "types.DataType(is_dict=True)"),
            ("naming.py", "construct", "MUT204", "model.Ref"),
            ("naming.py", "rename", "MUT204", "model.Ref"),
        ],
    ), result.stderr


SEEDED_HOST_ACCESS = """\
from typing import Protocol

from datamodel_code_generator.parser.base import Parser


class CountHost(Protocol):
    _count: int

    def _reset(self) -> None: ...


class Counter(Protocol):
    _count: int


class NarrowCount(CountHost, Protocol):
    pass


class Child(Parser):
    def bump(self) -> None:
        self._count += 1
        self._reset()


def through_the_host(parser: CountHost) -> int:
    parser._reset()
    parser._count = 1
    return parser._count


def through_the_class(parser: Parser) -> int:
    parser._reset()
    parser._count = 1
    return parser._count


def through_a_key(parser: Parser) -> object:
    parser.__dict__["_count"] = 2
    return getattr(parser, "_count")


def through_other_protocols(counter: Counter, narrow: NarrowCount) -> int:
    return counter._count + narrow._count


def untyped(parser) -> int:
    return parser._count + len(parser.__class__.__name__)
"""
"""A concern module (``parser/_*``) reaching the private members of ``Parser``, which ``parser/base.py`` defines.

A method of a subclass on its own receiver and a ``*Host`` Protocol that declares the member reach them; the class
itself, a ``__dict__`` key or ``getattr`` name, a Protocol of the module that is no ``*Host``, a subprotocol of a
Host and an untyped receiver do not. Dunders are none of the targets."""
SEEDED_HOST_PACKAGE = {
    "pyproject.toml": '[project]\nname = "seeded"\nrequires-python = ">=3.11"\n',
    f"{PACKAGE}/__init__.py": "",
    f"{PACKAGE}/parser/__init__.py": "",
    f"{PACKAGE}/parser/base.py": (
        "class Parser:\n    def __init__(self) -> None:\n        self._count = 0\n\n"
        "    def _reset(self) -> None:\n        self._count = 0\n\n\n"
        "def outside_the_concern(parser: Parser) -> int:\n    return parser._count\n"
    ),
    f"{PACKAGE}/parser/_concern.py": SEEDED_HOST_ACCESS,
}


@needs("heki")
@pytest.mark.allow_direct_assert("the findings heki gives on a seeded package are its verdict, not a stored output")
def test_heki_reports_private_access_of_the_concern_modules(tmp_path: Path) -> None:
    """A concern module reads, calls or writes another class's private member only through its receiver or a Host.

    The host contracts report every other access, also by ``__dict__`` key or ``getattr`` name, and one heki cannot
    tie to a class; a module outside the concern modules is ruff's (SLF001).
    """
    for name, text in SEEDED_HOST_PACKAGE.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    repo = build_heki_contracts.Repo(tmp_path, build_heki_contracts.Page([], [], [], []))
    contracts = tmp_path / "contracts.toml"
    contracts.write_text("\n".join(build_heki_contracts.host_contracts(repo)), encoding="utf-8")
    heki = shutil.which("heki", path=sysconfig.get_path("scripts")) or "heki"

    result = subprocess.run(
        [heki, "check", "--config", str(contracts), "--output-format", "json", "src"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    found = sorted(
        (item["qualname"], item["rule_code"], item["target"].removeprefix(f"{PACKAGE_NAME}."), item["line"])
        for item in json.loads(result.stdout)
    )
    assert (result.returncode, found) == (
        1,
        [
            ("through_a_key", "MUT201", "parser.base.Parser._[!_]*", 39),
            ("through_a_key", "MUT203", "parser.base.Parser._[!_]*", 40),
            ("through_other_protocols", "MUT203", "parser._concern.Counter._[!_]*", 44),
            ("through_other_protocols", "MUT203", "parser._concern.NarrowCount._[!_]*", 44),
            ("through_the_class", "MUT201", "parser.base.Parser._[!_]*", 34),
            ("through_the_class", "MUT203", "parser.base.Parser._[!_]*", 35),
            ("through_the_class", "MUT204", "parser.base.Parser._[!_]*", 33),
            ("untyped", "MUT206", "_count", 48),
        ],
    ), result.stderr


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


SEEDED_DECISION_WRITES = {
    "pyproject.toml": '[project]\nname = "seeded"\nrequires-python = ">=3.11"\n',
    f"{PACKAGE}/__init__.py": "",
    f"{PACKAGE}/parser/__init__.py": "",
    f"{PACKAGE}/parser/_phases.py": (
        "class _DecisionWriter:\n"
        "    def drop_held_null(self, field: object) -> None:\n        self.dropped = field\n\n"
        "    def omit_default(self, field: object) -> None:\n        self.omitted = field\n\n"
        "    def outcome(self) -> object:\n        return self.dropped\n"
    ),
    f"{PACKAGE}/parser/_passes/__init__.py": "",
    f"{PACKAGE}/parser/_passes/decide.py": (
        "from datamodel_code_generator.parser._phases import _DecisionWriter\n\n\n"
        "def decide(writer: _DecisionWriter) -> object:\n    return writer.outcome()\n"
    ),
    f"{PACKAGE}/parser/_held_null.py": (
        "from datamodel_code_generator.model.helpers import omit_through_a_helper\n"
        "from datamodel_code_generator.parser._phases import _DecisionWriter\n\n\n"
        "def own_write(writer: _DecisionWriter) -> None:\n    writer.drop_held_null(1)\n\n\n"
        "def other_write(writer: _DecisionWriter) -> None:\n    writer.omit_default(1)\n\n\n"
        "def helper_write(writer: _DecisionWriter) -> None:\n    omit_through_a_helper(writer)\n\n\n"
        "def untyped_write(writer) -> None:\n    writer.omit_default(1)\n\n\n"
        "def phase_read(writer: _DecisionWriter) -> object:\n    return writer.outcome()\n"
    ),
    f"{PACKAGE}/parser/_incomplete_defaults.py": (
        "from datamodel_code_generator.parser._phases import _DecisionWriter\n\n\n"
        "def own_write(writer: _DecisionWriter) -> None:\n    writer.omit_default(1)\n"
    ),
    f"{PACKAGE}/model/__init__.py": "",
    f"{PACKAGE}/model/helpers.py": (
        "from datamodel_code_generator.parser._phases import _DecisionWriter\n\n\n"
        "def omit_through_a_helper(writer: _DecisionWriter) -> None:\n    writer.omit_default(1)\n"
    ),
}
"""Two decisions, their writes and the decision phase: ``_held_null.py`` writes through its own write, through the
write of the incomplete defaults directly, through a helper of another module and on an untyped receiver, and calls
the writer method no decision names, which the decision phase calls."""
SEEDED_DECISION_DECLS = {
    name: replace(DECISION_DECLS[name], module=module, writer_methods=methods)
    for name, module, methods in (
        ("omit_held_null", "parser/_held_null.py", ("drop_held_null",)),
        ("omit_incomplete_model_defaults", "parser/_incomplete_defaults.py", ("omit_default",)),
    )
}


@needs("heki")
@pytest.mark.allow_direct_assert("the findings heki gives on a seeded package are its verdict, not a stored output")
def test_heki_reports_a_decision_that_calls_the_write_of_another(tmp_path: Path) -> None:
    """A write of the decision writer is called only in the module of the decision that writes through it.

    A call from another decision's module, through a helper of another module or on an untyped receiver is a finding,
    and so is a call of a public method no decision names outside the decision phase.
    """
    for name, text in SEEDED_DECISION_WRITES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    repo = build_heki_contracts.Repo(tmp_path, build_heki_contracts.Page([], [], [], []))
    contracts = tmp_path / "contracts.toml"
    contracts.write_text(
        "\n".join(build_heki_contracts.decision_writer_contracts(repo, SEEDED_DECISION_DECLS)), encoding="utf-8"
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
        (Path(item["path"]).name, item["qualname"], item["rule_code"], item["target"].rpartition(".")[2])
        for item in json.loads(result.stdout)
    )
    assert (result.returncode, found) == (
        1,
        [
            ("_held_null.py", "other_write", "MUT204", "omit_default"),
            ("_held_null.py", "phase_read", "MUT204", "outcome"),
            ("_held_null.py", "untyped_write", "MUT207", "omit_default"),
            ("helpers.py", "omit_through_a_helper", "MUT204", "omit_default"),
        ],
    ), result.stderr


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"writer_methods": ("never_defined",)},
            "DECISION_DECLS: omit_held_null writes through never_defined, which _DecisionWriter does not define",
        ),
        (
            {"module": ""},
            "DECISION_DECLS: omit_held_null names no module of the package or no write (module, writer_methods)",
        ),
        (
            {"module": "parser/never_defined.py"},
            "DECISION_DECLS: omit_held_null names no module of the package or no write (module, writer_methods)",
        ),
        (
            {"writer_methods": ()},
            "DECISION_DECLS: omit_held_null names no module of the package or no write (module, writer_methods)",
        ),
    ],
    ids=["undefined-write", "no-module", "unknown-module", "no-write"],
)
@pytest.mark.allow_direct_assert("a stale table row must stop the build, not be stored as an expected output")
def test_decision_writes_name_a_method_of_the_writer(changes: dict[str, Any], message: str) -> None:
    """Each declaration of ``DECISION_DECLS`` names its module and writes, methods ``_DecisionWriter`` defines."""
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
    contracts.write_text(
        "\n".join(
            build_heki_contracts.decision_contracts(repo, SEEDED_DECISION_DECLS, accessors, SEEDED_DECISION_ROWS)
        ),
        encoding="utf-8",
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
        "def untyped(ctx: ModuleContext) -> None:\n    cast('Any', ctx).imports.append('os')\n"
    ),
}
"""The ledger and the imports module change an ``Imports``, and so does a ``LEDGER_WRITERS`` def; another def of the
writer's module, a pass directly and through a helper, and a pass on an untyped value change it too."""


@needs("heki")
@pytest.mark.allow_direct_assert("the findings heki gives on a seeded package are its verdict, not a stored output")
def test_heki_reports_imports_written_outside_the_ledger(tmp_path: Path) -> None:
    """A method that changes an ``Imports`` is called only in the ledger, the ``imports`` module and the writers.

    A call in another def, also of a writer's module, is a finding in the scope that makes it, a helper's included;
    one on a value heki cannot type is not seen (a gap of heki, which a write-owners form would close).
    """
    for name, text in SEEDED_IMPORT_WRITES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    repo = build_heki_contracts.Repo(tmp_path, build_heki_contracts.Page([], [], [], []))
    contracts = tmp_path / "contracts.toml"
    writers = [LedgerWriter("parser/_init_exports.py", "build_all_exports_code", "re-exports")]
    contracts.write_text(
        "\n".join(build_heki_contracts.import_ledger_contracts(repo, ("append",), writers)), encoding="utf-8"
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
        (Path(item["path"]).name, item["qualname"], item["rule_code"], item["target"].rpartition(".")[2])
        for item in json.loads(result.stdout)
    )
    assert (result.returncode, found) == (
        1,
        [
            ("_init_exports.py", "beside_the_writer", "MUT204", "append"),
            ("passes.py", "_add", "MUT204", "append"),
            ("passes.py", "direct", "MUT204", "append"),
        ],
    ), result.stderr


@pytest.mark.parametrize(
    ("mutators", "writers", "message"),
    [
        pytest.param(("never_defined",), (), "IMPORTS_MUTATORS: Imports defines no method never_defined", id="mutator"),
        pytest.param(
            (),
            (LedgerWriter("parser/base.py", "Parser.never_defined", "none"),),
            "LEDGER_WRITERS: parser/base.py defines no Parser.never_defined",
            id="writer",
        ),
    ],
)
@pytest.mark.allow_direct_assert("a stale table row must stop the build, not be stored as an expected output")
def test_import_ledger_rows_name_defined_methods(
    mutators: tuple[str, ...], writers: tuple[LedgerWriter, ...], message: str
) -> None:
    """``IMPORTS_MUTATORS`` names methods of ``Imports`` and ``LEDGER_WRITERS`` defs the package defines."""
    repo = build_heki_contracts.Repo(build_heki_contracts.ROOT, build_heki_contracts.Page([], [], [], []))

    with pytest.raises(SystemExit) as stopped:
        build_heki_contracts.import_ledger_contracts(repo, mutators, writers)

    assert str(stopped.value) == message


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
        "def untyped(store: GenerationStore) -> None:\n    cast('Any', store).omit_field_default(1)\n\n\n"
        "def own_builder(store: GenerationStore) -> GraphBuilder:\n    return GraphBuilder(store)\n"
    ),
}
"""A pass that changes the graph through the store's builder, and one that calls a store operation directly, in a
helper, on an untyped value, or that makes a builder of its own."""


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
    repo = build_heki_contracts.Repo(tmp_path, build_heki_contracts.Page([], [], [], []))
    contracts = tmp_path / "contracts.toml"
    contracts.write_text(
        "\n".join(build_heki_contracts.store_operation_contracts(repo, ("omit_field_default",))), encoding="utf-8"
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
        (Path(item["path"]).name, item["qualname"], item["rule_code"], item["target"].rpartition(".")[2])
        for item in json.loads(result.stdout)
    )
    assert (result.returncode, found) == (
        1,
        [
            ("passes.py", "_helper", "MUT204", "omit_field_default"),
            ("passes.py", "direct", "MUT204", "omit_field_default"),
            ("passes.py", "own_builder", "MUT204", "generation:GraphBuilder"),
            ("passes.py", "untyped", "MUT207", "omit_field_default"),
        ],
    ), result.stderr


@pytest.mark.allow_direct_assert("a stale table row must stop the build, not be stored as an expected output")
def test_store_operations_name_a_method_of_the_store() -> None:
    """An operation ``GENERATION_STORE_MUTATION_METHODS`` names is a method ``GenerationStore`` defines."""
    repo = build_heki_contracts.Repo(build_heki_contracts.ROOT, build_heki_contracts.Page([], [], [], []))

    with pytest.raises(SystemExit) as stopped:
        build_heki_contracts.store_operation_contracts(repo, ("never_defined",))

    assert str(stopped.value) == "GENERATION_STORE_MUTATION_METHODS: GenerationStore defines no never_defined"


@pytest.mark.parametrize("name", ["FINAL_FACTS", "FINAL_FACTS_SITE", "PARSER"])
@pytest.mark.allow_direct_assert("a stale pipeline home must stop the build, not be stored as an expected output")
def test_pipeline_contracts_name_scopes_the_package_defines(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """The scopes the pipeline contracts name exist in the package."""
    monkeypatch.setattr(build_heki_contracts, name, Home("parser/base.py", "NeverDefined"))
    repo = build_heki_contracts.Repo(build_heki_contracts.ROOT, build_heki_contracts.Page([], [], [], []))

    with pytest.raises(SystemExit) as stopped:
        build_heki_contracts.pipeline_contracts(repo)

    assert str(stopped.value) == "parser/base.py defines no NeverDefined"
