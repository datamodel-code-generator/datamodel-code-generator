"""Real caller isolation and naming checks for private inflection imports."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import DataModelType
from tests.conftest import assert_output, create_assert_file_content
from tests.main.conftest import DATA_PATH, EXPECTED_MAIN_PATH, _generated_model

if TYPE_CHECKING:
    from pathlib import Path

DATA = DATA_PATH / "inflect_isolation"
EXPECTED = EXPECTED_MAIN_PATH / "inflect_isolation"
assert_file_content = create_assert_file_content(EXPECTED)


@pytest.mark.parametrize(
    ("scenario", "backend"),
    [
        *[("generate_first", backend) for backend in DataModelType],
        *[
            (scenario, DataModelType.PydanticV2BaseModel)
            for scenario in ["inflect_first", "typeguard_first", "concurrent", "parallel_generation"]
        ],
        *[
            (scenario, DataModelType.PydanticV2BaseModel)
            for scenario in ["unexpected_runtime", "unexpected_value", "unexpected_base"]
        ],
        *[
            (f"failure_{failure}", DataModelType.PydanticV2BaseModel)
            for failure in ["missing_spec", "missing_get_code", "no_code", "code_error", "future_api", "reentrant"]
        ],
    ],
)
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_inflect_independent_callers_preserve_checks_and_names(
    output_file: Path, scenario: str, entrypoint: str, backend: DataModelType
) -> None:
    """Generate repeatedly in fresh processes without changing independent inflect consumers."""
    root = DATA_PATH.parents[1]
    coverage_file = os.environ.get("COVERAGE_FILE", "")
    coverage_args = (
        [
            "-m",
            "coverage",
            "run",
            "--branch",
            "--concurrency=thread",
            "--parallel-mode",
            "--source",
            str(root / "src/datamodel_code_generator"),
            "--rcfile",
            str(root / "pyproject.toml"),
        ]
        if coverage_file and "-nocov" not in coverage_file
        else []
    )
    result = subprocess.run(
        [sys.executable, *coverage_args, str(DATA / "probe.py"), scenario, entrypoint, backend.value, str(output_file)],
        check=True,
        capture_output=True,
        text=True,
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        timeout=60,
    )
    expected_probe = (
        "unexpected"
        if scenario.startswith("unexpected_")
        else "fallback"
        if scenario.startswith("failure_")
        else scenario
    )
    assert_output(result.stdout, EXPECTED / f"{expected_probe}.txt")
    assert_file_content(output_file, f"{backend.value.replace('.', '_')}.py")
    with _generated_model(output_file, "generated_inflect_isolation", "Catalog") as model:
        assert_output(model.__name__ + "\n", EXPECTED / "model_name.txt")
