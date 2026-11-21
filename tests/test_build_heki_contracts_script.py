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

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_heki_contracts.py"
CONTRACTS = ROOT / "scripts" / "heki.toml"
BASELINE = ROOT / "scripts" / "heki_baseline.toml"
PACKAGE = "src/datamodel_code_generator"

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
}
"""A package whose ``seeded.py`` writes ``DataModel.fields`` outside the store operations that the fact row names.

``GenerationStore.set_fields``, one of those operations, writes it too: heki allows that write.
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

    result = _heki("--baseline", str(baseline), "--update-baseline", "--output-format", "concise", cwd=ROOT)

    assert result.returncode == 0, (
        "heki findings the baseline does not record: keep the write, read or call inside its owners "
        "(scripts/build_heki_contracts.py names the records each contract comes from)\n" + result.stdout + result.stderr
    )
    assert baseline.read_text(encoding="utf-8") == BASELINE.read_text(encoding="utf-8"), (
        "baseline entries the code no longer reaches: run heki check --config scripts/heki.toml --baseline "
        "scripts/heki_baseline.toml --update-baseline src\n" + result.stderr
    )


@pytest.mark.allow_direct_assert("the findings heki gives on a seeded package are its verdict, not a stored output")
def test_heki_reports_a_fact_write_outside_its_store_operations(tmp_path: Path) -> None:
    """A write of a fact row's attribute outside the store operations the row names is a finding; theirs is none."""
    for name, text in SEEDED_PACKAGE.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")

    result = _heki("--output-format", "json", cwd=tmp_path)

    found = [
        (Path(item["path"]).as_posix(), item["qualname"], item["rule_code"], item["target"])
        for item in json.loads(result.stdout)
    ]
    assert (result.returncode, found) == (
        1,
        [(f"{PACKAGE}/parser/seeded.py", "rename_fields", "MUT201", "DataModel.fields")],
    ), result.stderr
