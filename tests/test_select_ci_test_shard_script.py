"""Tests for CI test shard selection."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import select_ci_test_shard
from tests.conftest import assert_output

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "select_ci_test_shard.py"
DATA = Path(__file__).parent / "data/ci_shards"
EXPECTED = Path(__file__).parent / "data/expected/ci_shards"
PAYLOAD_VALIDATION_FILE = "tests/main/test_payload_validation.py"


def _run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def test_recipe_discovers_split_nodeids(tmp_path: Path) -> None:
    """Split-file nodeids are generated from source instead of hand-written lists."""
    recipe_path = tmp_path / "recipe.json"
    _run_script("--write-recipe", str(recipe_path))

    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    nodeids = {item["nodeid"] for item in recipe["items"]}
    payload_nodeids = {nodeid for nodeid in nodeids if nodeid.startswith(f"{PAYLOAD_VALIDATION_FILE}::")}

    assert_output(
        json.dumps(
            {
                "split_file_excluded": PAYLOAD_VALIDATION_FILE not in nodeids,
                "accepts_payload": (
                    f"{PAYLOAD_VALIDATION_FILE}::test_generated_pydantic_v2_model_accepts_schema_derived_payloads"
                )
                in nodeids,
                "discovered_payloads": (
                    f"{PAYLOAD_VALIDATION_FILE}::test_payload_validation_cases_cover_discovered_schema_files"
                )
                in nodeids,
                "multiple_functions": len(payload_nodeids) >= 10,
            },
            sort_keys=True,
        )
        + "\n",
        Path(__file__).parent / "data/expected/ci_shards/discovery.txt",
    )


@pytest.mark.parametrize("shard_total", [2, 3])
def test_recipe_round_trip_selects_disjoint_shards(tmp_path: Path, shard_total: int) -> None:
    """Generated recipes can be read back to select shards without duplicate items."""
    recipe_path = tmp_path / "recipe.json"
    _run_script("--write-recipe", str(recipe_path))

    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    expected_nodeids = {item["nodeid"] for item in recipe["items"]}
    selected_nodeids: list[str] = []

    for shard_index in range(1, shard_total + 1):
        result = _run_script(str(shard_index), str(shard_total), "--recipe", str(recipe_path))
        selected_nodeids.extend(result.stdout.splitlines())

    assert_output(
        json.dumps(
            {
                "complete": set(selected_nodeids) == expected_nodeids,
                "disjoint": len(selected_nodeids) == len(expected_nodeids),
            },
            sort_keys=True,
        )
        + "\n",
        Path(__file__).parent / "data/expected/ci_shards/round-trip.txt",
    )


@pytest.mark.parametrize(
    "case", json.loads((Path(__file__).parent / "data/ci_shards/cases.json").read_text(encoding="utf-8"))
)
def test_recipe_cli_validates_external_cases(case: str, tmp_path: Path) -> None:
    """Validate real recipes through the same entry point as CI."""
    from contextlib import redirect_stdout
    from io import StringIO

    data = json.loads((Path(__file__).parent / "data/ci_shards/cases.json").read_text(encoding="utf-8"))[case]
    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({"version": data.get("version", 1), "items": data["items"]}), encoding="utf-8")
    output = StringIO()
    with redirect_stdout(output):
        try:
            select_ci_test_shard.main([*data["args"], "--recipe", str(recipe)])
        except SystemExit as error:
            output.write(f"error: {error}\n")
    assert_output(output.getvalue(), Path(__file__).parent / f"data/expected/ci_shards/{case}.txt")


@pytest.mark.parametrize("profile", select_ci_test_shard.PROFILES)
def test_discovery_includes_new_files_and_methods(tmp_path: Path, profile: str) -> None:
    """Discover unknown tests and keep deterministic recipes across enumeration order."""
    from contextlib import redirect_stdout
    from io import StringIO

    data = Path(__file__).parent / "data/ci_shards/tree"
    shutil.copytree(data, tmp_path, dirs_exist_ok=True)
    recipe = tmp_path / "recipe.json"
    previous = Path.cwd()
    out = StringIO()
    try:
        os.chdir(tmp_path)
        select_ci_test_shard.main(["--profile", profile, "--write-recipe", str(recipe)])
        with redirect_stdout(out):
            select_ci_test_shard.main(["1", "2", "--profile", profile, "--write-recipe", str(recipe)])
    finally:
        os.chdir(previous)
    items = json.loads(recipe.read_text(encoding="utf-8"))["items"]
    file = tmp_path / "tests/test_new.py"
    file.write_text(file.read_text(encoding="utf-8") + "\n" * 1000, encoding="utf-8")
    try:
        os.chdir(tmp_path)
        select_ci_test_shard.main(["--profile", profile, "--write-recipe", str(recipe)])
    finally:
        os.chdir(previous)
    padded_items = json.loads(recipe.read_text(encoding="utf-8"))["items"]
    reversed_recipe = tmp_path / "reversed.json"
    reversed_recipe.write_text(json.dumps({"version": 1, "items": items[::-1]}), encoding="utf-8")
    reversed_out = StringIO()
    with redirect_stdout(reversed_out):
        select_ci_test_shard.main(["1", "2", "--recipe", str(reversed_recipe)])
    assert_output(
        json.dumps(
            {
                "nodeids": sorted(item["nodeid"] for item in items),
                "positive_weights": all(item["weight"] > 0 for item in items),
                "measured_weight": next(
                    item["weight"]
                    for item in items
                    if item["nodeid"].endswith("::test_generated_pydantic_v2_model_accepts_schema_derived_payloads")
                ),
                "source_size_independent": items == padded_items,
                "order_independent": out.getvalue() == reversed_out.getvalue(),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        Path(__file__).parent / f"data/expected/ci_shards/new-tests-{profile}.txt",
    )


@pytest.mark.parametrize("case", json.loads((DATA / "weights_cases.json").read_text(encoding="utf-8")))
def test_weights_cli_validates_external_cases(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Record JUnit durations into measured weights and reject malformed weight documents."""
    from contextlib import redirect_stdout
    from io import StringIO

    data = json.loads((DATA / "weights_cases.json").read_text(encoding="utf-8"))[case]
    shutil.copytree(DATA / "tree", tmp_path, dirs_exist_ok=True)
    shutil.copytree(DATA / "junit", tmp_path / "junit")
    weights = tmp_path / "weights.json"
    weights.write_text(
        json.dumps(data["weights"])
        if "weights" in data
        else select_ci_test_shard.WEIGHTS_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    output = StringIO()
    with redirect_stdout(output):
        try:
            for args in data["runs"]:
                select_ci_test_shard.main([*args, "--weights", "weights.json"])
        except SystemExit as error:
            output.write(f"error: {error}\n")
    if (profile := data.get("dump")) is not None:
        recorded = json.loads(weights.read_text(encoding="utf-8"))["profiles"][profile]
        output.write(json.dumps(recorded, indent=2, sort_keys=True) + "\n")
    assert_output(output.getvalue(), EXPECTED / f"weights-{case}.txt")
