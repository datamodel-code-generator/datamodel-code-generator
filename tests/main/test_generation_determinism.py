"""Determinism guards for representative code generation paths."""

from __future__ import annotations

import difflib
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

import pytest

from datamodel_code_generator import InputFileType, generate
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, OPEN_API_DATA_PATH

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class DeterminismCase:
    """Representative input for a deterministic generation check."""

    input_path: Path
    input_file_type: InputFileType


HASH_SEED_SCRIPT = """
from pathlib import Path
import sys

from datamodel_code_generator import InputFileType, generate

result = generate(
    input_=Path(sys.argv[1]),
    input_file_type=InputFileType(sys.argv[2]),
    disable_timestamp=True,
    formatters=[],
)
if not isinstance(result, str):
    raise TypeError(f"Expected generated output to be str, got {type(result).__name__}")
sys.stdout.write(result)
"""

HASH_SEED_TIMEOUT_SECONDS = 60
DETERMINISM_CASES = [
    pytest.param(
        DeterminismCase(JSON_SCHEMA_DATA_PATH / "person.json", InputFileType.JsonSchema),
        id="jsonschema-person",
    ),
    pytest.param(
        DeterminismCase(OPEN_API_DATA_PATH / "api.yaml", InputFileType.OpenAPI),
        id="openapi-api",
    ),
]


def _generate_output(input_path: Path, input_file_type: InputFileType) -> str:
    result = generate(
        input_=input_path,
        input_file_type=input_file_type,
        disable_timestamp=True,
        formatters=[],
    )
    if not isinstance(result, str):  # pragma: no cover
        message = f"Expected generated output to be str, got {type(result).__name__}"
        raise TypeError(message)

    return result


def _fail_with_diff(first: str, second: str, context: str) -> NoReturn:  # pragma: no cover
    diff = "\n".join(
        difflib.unified_diff(
            first.splitlines(),
            second.splitlines(),
            fromfile="first",
            tofile="second",
            lineterm="",
        )
    )
    pytest.fail(f"Generated output changed for {context}:\n{diff}", pytrace=False)


def _assert_same_output(first: str, second: str, context: str) -> None:
    if first == second:
        return

    _fail_with_diff(first, second, context)  # pragma: no cover


def _generate_output_with_hash_seed(input_path: Path, input_file_type: InputFileType, seed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    try:
        result = subprocess.run(
            [sys.executable, "-c", HASH_SEED_SCRIPT, str(input_path), input_file_type.value],
            check=True,
            env=env,
            capture_output=True,
            text=True,
            timeout=HASH_SEED_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover
        message = (
            f"Generation timed out after {HASH_SEED_TIMEOUT_SECONDS}s for "
            f"{input_path.as_posix()} with PYTHONHASHSEED={seed}"
        )
        raise AssertionError(message) from exc
    return result.stdout


@pytest.mark.parametrize(
    "case",
    DETERMINISM_CASES,
)
def test_generate_output_is_repeatable(case: DeterminismCase) -> None:
    """Repeated generation in one process should be byte-stable."""
    first = _generate_output(case.input_path, case.input_file_type)
    second = _generate_output(case.input_path, case.input_file_type)

    _assert_same_output(first, second, case.input_path.as_posix())


@pytest.mark.parametrize(
    "case",
    DETERMINISM_CASES,
)
def test_generate_output_is_stable_across_hash_seeds(case: DeterminismCase) -> None:
    """Representative output should not depend on hash iteration order."""
    seed_zero_output = _generate_output_with_hash_seed(case.input_path, case.input_file_type, "0")
    seed_one_output = _generate_output_with_hash_seed(case.input_path, case.input_file_type, "1")

    _assert_same_output(seed_zero_output, seed_one_output, f"{case.input_path.as_posix()} hash seeds")
