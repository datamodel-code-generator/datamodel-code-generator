"""Install generated FastAPI servers into new environments that hold only their printed dependencies."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.main.conftest import run_main_and_assert

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform" / "fastapi" / "install"
EXPECTED = DATA / "expected" / "main" / "generation_platform" / "fastapi" / "install"
OPTIONS = [
    "--target-python-version",
    "3.11",
    "--openapi-scopes",
    "schemas",
    "api",
    "--output-model-type",
    "pydantic_v2.BaseModel",
    "--formatters",
    "builtin",
    "--disable-timestamp",
]


@pytest.mark.parametrize("resolution", ["lowest-direct", "highest"])
def test_fastapi_install(
    resolution: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Install the printed dependencies at their floors and at their newest versions, then serve from the package."""
    if not os.environ.get("DATAMODEL_CODE_GENERATOR_FASTAPI_INSTALL_E2E"):
        pytest.skip("DATAMODEL_CODE_GENERATOR_FASTAPI_INSTALL_E2E enables installing generated servers")
    monkeypatch.chdir(tmp_path)
    notice = (EXPECTED / "dependencies.txt").read_text(encoding="utf-8")
    run_main_and_assert(
        input_path=Path("contacts.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=OPTIONS,
        copy_files=[
            (SOURCE / "contacts.yaml", tmp_path / "contacts.yaml"),
            (SOURCE / "pyproject-server.toml", tmp_path / "pyproject.toml"),
            (SOURCE / "app.py", tmp_path / "app.py"),
        ],
        capsys=capsys,
        expected_stderr=notice,
    )
    environment = tmp_path / "environment"
    subprocess.run(["uv", "venv", "--quiet", "--python", sys.executable, environment], check=True)
    python = environment / ("Scripts" if os.name == "nt" else "bin") / "python"
    dependencies = shlex.split(notice.splitlines()[-1])[2:]
    subprocess.run(
        ["uv", "pip", "install", "--quiet", "--python", python, "--resolution", resolution, *dependencies], check=True
    )
    served = subprocess.run([python, "app.py"], capture_output=True, text=True, check=False)
    assert_output(served.stdout + served.stderr, EXPECTED / "served.txt")
