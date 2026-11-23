"""Tests for heki's contracts of the package: written from the records, and held against today's findings.

heki is a development dependency (the ``test`` dependency group). ``scripts/build_heki_contracts.py`` writes
``scripts/heki.toml``; ``scripts/heki_baseline.toml`` records today's findings, which only shrink
(``scripts/check_ratchets.py`` counts their sites).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest
import tomllib

from scripts import build_heki_contracts, check_ratchets
from scripts.decision_homes import DecisionHome, Home
from scripts.fact_tables import MemberUse, StoreMember

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_heki_contracts.py"
CONTRACTS = ROOT / "scripts" / "heki.toml"
BASELINE = ROOT / "scripts" / "heki_baseline.toml"
PACKAGE = "src/datamodel_code_generator"

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


def _heki(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run heki of the environment (the test dependency group installs it) with the contracts of the repository."""
    heki = shutil.which("heki", path=sysconfig.get_path("scripts")) or "heki"
    command = [heki, "check", "--config", str(CONTRACTS), *args, "src"]
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


@pytest.mark.allow_direct_assert("the contracts out of date must fail the test, not be stored as an expected output")
def test_heki_contracts_are_written_from_the_records() -> None:
    """scripts/heki.toml is what scripts/build_heki_contracts.py writes from today's records."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"], cwd=ROOT, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stdout + result.stderr


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
        "(scripts/build_heki_contracts.py names the records each contract comes from); for a read of the "
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


@pytest.mark.allow_direct_assert("the contracts' attribution must fail the test, not be stored as an expected output")
def test_record_contracts_judge_the_scope_that_makes_the_access() -> None:
    """Homes, builders, held parse methods, dispatch tables, fact rows and store members are judged where they are used.

    The guards of the records (`scripts/_decision_home_reads.py`) report the scope that holds each read, so a call
    handed to a private helper is the helper's, not its caller's; the runtime write guard names a write by the
    function that makes it, as heki names it with lexical attribution.
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
