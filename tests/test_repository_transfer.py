"""Exercise transferred repository references without contacting notification APIs."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import update_docs_version
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests/data/repository_transfer"
EXPECTED = ROOT / "tests/data/expected/repository_transfer"


@pytest.mark.skipif(shutil.which("node") is None, reason="Workflow JavaScript requires Node.js")
def test_release_notification_references() -> None:
    """Run the workflow's real extraction and guard code against old and new references."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/release-notify.yaml").read_text())
    job = workflow["jobs"]["notify"]
    result = subprocess.run(
        ["node", str(DATA / "release_notify.cjs")],
        input=json.dumps({
            "script": job["steps"][0]["with"]["script"],
            "env": job["env"],
            "cases": json.loads((DATA / "release_notify.json").read_text()),
        }),
        capture_output=True,
        text=True,
        check=True,
    )
    assert_output(result.stdout, EXPECTED / "release_notify.txt")


def test_update_docs_version_preserves_other_references(tmp_path: Path) -> None:
    """Check mode leaves bytes intact; updates preserve unrelated Actions and Docker images."""
    target = tmp_path / "actions.md"
    target.write_text((DATA / "actions.txt").read_text())
    results = {"stale": update_docs_version.update_file(target, "9.8.7", check=True)}
    assert_output(target.read_text(), EXPECTED / "actions_check.txt")
    results["updated"] = update_docs_version.update_file(target, "9.8.7")
    results["current"] = update_docs_version.update_file(target, "9.8.7", check=True)
    results["unchanged"] = update_docs_version.update_file(target, "9.8.7")
    assert_output(target.read_text(), EXPECTED / "actions.txt")
    assert_output(json.dumps(results, indent=2) + "\n", EXPECTED / "action_results.txt")
