"""Exercise the release comparison script end to end with the running interpreter on both sides."""

from __future__ import annotations

import sys
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from scripts import compare_release
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ROOT / "tests/data/expected/compare_release"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="compare_release.py forks its workers")


@pytest.mark.parametrize(
    ("case", "args"),
    [
        (
            "identical",
            [
                "--types",
                "pydantic_v2.BaseModel,typing.TypedDict",
                "--options",
                "default;--collapse-root-models",
                "--import-check",
                str(ROOT / "tests/data/openapi/api.yaml"),
            ],
        ),
        (
            "directory",
            [
                "--types",
                "pydantic_v2.BaseModel",
                "--extra=--input-file-type jsonschema",
                "--import-check",
                str(ROOT / "tests/data/jsonschema/alias_import_alias"),
            ],
        ),
        (
            "path_option",
            [
                "--types",
                "pydantic_v2.BaseModel",
                f"--options=--aliases={ROOT / 'tests/data/openapi/aliases.json'}",
                str(ROOT / "tests/data/openapi/api.yaml"),
            ],
        ),
        (
            "fail_both",
            [
                "--types",
                "pydantic_v2.BaseModel",
                "--extra=--input-file-type jsonschema",
                "--options=--no-such-option",
                str(ROOT / "tests/data/jsonschema/person.json"),
            ],
        ),
    ],
)
def test_compare_release(case: str, args: list[str], tmp_path: Path) -> None:
    """Both sides run through warm workers; the second run reuses the cached base outputs."""
    outputs = []
    for _ in range(2):
        stdout = StringIO()
        with redirect_stdout(stdout), redirect_stderr(StringIO()):
            code = compare_release.main([
                "--base-python",
                sys.executable,
                "--candidate-python",
                sys.executable,
                "--cache-dir",
                str(tmp_path),
                "-j",
                "2",
                *args,
            ])
        outputs.append(f"exit={code}\n{stdout.getvalue()}")
    assert_output("\n".join(outputs), EXPECTED / f"{case}.txt")
