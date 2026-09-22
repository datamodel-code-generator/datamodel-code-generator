"""Exercise shared-driver retry selection with real accepted batches and exceptional fault injection."""

from __future__ import annotations

import json
import operator
import shutil
import sys
from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.data.python.generation_session_inputs import run_generation_session

DATA = Path(__file__).parents[1] / "data"
EXPECTED = DATA / "expected/main/generation_platform"
CASES = json.loads((DATA / "generation_platform/attempts.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=operator.itemgetter("name"))
def test_generation_attempt_lifetime(case: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Preserve the independent S01 trace oracle with real S03 session ownership."""
    failure = case.get("failure", "")
    source = (DATA / "generation_platform" / case["input"]).resolve()
    if failure in {"source_change", "discard"}:
        copied = tmp_path / source.name
        shutil.copyfile(source, copied)
        source = copied
    output = None
    if failure == "emit":
        blocked = tmp_path / "blocked"
        blocked.write_text("not a directory")
        output = blocked / "models.py"
    observation, retained = run_generation_session(
        source, failure=failure, monkeypatch=monkeypatch, output=output, api_scope=case.get("api") == "true"
    )
    expected = EXPECTED / ("windows" if sys.platform == "win32" and failure == "emit" else "")
    assert_output(json.dumps(observation, indent=2) + "\n", expected / f"attempt_{case['name']}.txt")
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")
