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

from scripts import check_ratchets

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
    ),
    f"{PACKAGE}/parser/__init__.py": "",
    f"{PACKAGE}/parser/generation.py": (
        "from datamodel_code_generator.model.base import DataModel\n\n\n"
        "class GenerationStore:\n"
        "    def set_fields(self, model: DataModel, fields: list[str]) -> None:\n        model.fields = fields\n"
    ),
    f"{PACKAGE}/parser/seeded.py": (
        "from datamodel_code_generator.model.base import DataModel\n\n\n"
        'def rename_fields(model: DataModel) -> None:\n    model.fields = ["renamed"]\n'
    ),
    f"{PACKAGE}/parser/seeded_env.py": SEEDED_ENVIRONMENT,
}
"""A package whose ``seeded.py`` writes ``DataModel.fields`` outside the store operations that the fact row names.

``GenerationStore.set_fields``, one of those operations, writes it too: heki allows that write. ``seeded_env.py``
holds `SEEDED_ENVIRONMENT`, which the environment boundary forbids in functions and module bodies.
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
            (f"{PACKAGE}/parser/seeded.py", "rename_fields", "MUT201", "DataModel.fields", 1),
            (f"{PACKAGE}/parser/seeded_env.py", "<module>", "EFF201", environment, 14),
            (f"{PACKAGE}/parser/seeded_env.py", "old_python", "EFF201", environment, 1),
        ],
    ), result.stderr


SEEDED_HOMES = {
    "pyproject.toml": '[project]\nname = "seeded"\nrequires-python = ">=3.11"\n',
    f"{PACKAGE}/__init__.py": "",
    f"{PACKAGE}/home.py": "def decide() -> int:\n    return 1\n",
    f"{PACKAGE}/caller.py": (
        "from datamodel_code_generator.home import decide\n\n\n"
        "def run() -> int:\n    return decide()\n\n\n"
        "def run_through_helper() -> int:\n    return _helper()\n\n\n"
        "def _helper() -> int:\n    return decide()\n"
    ),
}
"""A decision home that `run` and `run_through_helper` may call; the second hands the call to a private helper."""
SEEDED_HOME_CONTRACT = """[[call-owners]]
name = "home:home.py:decide"
targets = ["datamodel_code_generator.home.decide"]
allowed-callers = ["datamodel_code_generator.caller:run", "datamodel_code_generator.caller:run_through_helper"]
references = "calls"
subclasses = false
attribution = "lexical"
"""


@pytest.mark.allow_direct_assert("the contracts' attribution must fail the test, not be stored as an expected output")
def test_record_contracts_judge_the_scope_that_makes_the_access() -> None:
    """Homes, builders, held parse methods and dispatch tables are judged where the call or read is written.

    The guards of the records (`scripts/_decision_home_reads.py`) report the scope that holds each read, so a call
    handed to a private helper is the helper's, not its caller's.
    """
    contracts = tomllib.loads(CONTRACTS.read_text(encoding="utf-8"))
    judged = [
        (table["name"], table.get("attribution"))
        for kind in ("call-owners", "read-owners")
        for table in contracts[kind]
        if table["name"].startswith(("home:", "builder:", "held:", "table:"))
    ]

    assert judged
    assert [name for name, attribution in judged if attribution != "lexical"] == []


@pytest.mark.allow_direct_assert("the findings heki gives on a seeded package are its verdict, not a stored output")
def test_heki_judges_a_home_call_in_the_helper_that_makes_it(tmp_path: Path) -> None:
    """With lexical attribution a call delegated to a private helper is reported at the helper, as the guard does."""
    for name, text in SEEDED_HOMES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    contracts = tmp_path / "contracts.toml"
    contracts.write_text(SEEDED_HOME_CONTRACT, encoding="utf-8")
    heki = shutil.which("heki", path=sysconfig.get_path("scripts")) or "heki"

    result = subprocess.run(
        [heki, "check", "--config", str(contracts), "--output-format", "json", "src"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    found = [(item["qualname"], item["rule_code"]) for item in json.loads(result.stdout)]
    assert (result.returncode, found) == (1, [("_helper", "MUT204")]), result.stderr


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
