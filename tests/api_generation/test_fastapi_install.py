"""Install generated FastAPI servers into new environments that hold only their printed requirements."""

from __future__ import annotations

import os
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
    "--openapi-scopes",
    "schemas",
    "api",
    "--output-model-type",
    "pydantic_v2.BaseModel",
    "--formatters",
    "builtin",
    "--generate-server",
    "fastapi",
    "--target-config",
    "fastapi.toml",
    "--dependency-format",
    "requirements",
]


@pytest.mark.parametrize(("layout", "models"), [("embedded", "models.py"), ("standalone", "service/src/models.py")])
def test_fastapi_install(
    layout: str, models: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Install only the printed requirements with uv into a new environment, then serve requests from the package."""
    if not os.environ.get("DATAMODEL_CODE_GENERATOR_FASTAPI_INSTALL_E2E"):
        pytest.skip("DATAMODEL_CODE_GENERATOR_FASTAPI_INSTALL_E2E enables installing generated servers")
    monkeypatch.chdir(tmp_path)
    requirements = EXPECTED / f"{layout}-requirements.txt"
    run_main_and_assert(
        input_path=Path("contacts.yaml"),
        output_path=Path(models),
        input_file_type="openapi",
        extra_args=OPTIONS,
        copy_files=[
            (SOURCE / "contacts.yaml", tmp_path / "contacts.yaml"),
            (SOURCE / f"{layout}.toml", tmp_path / "fastapi.toml"),
            (SOURCE / "app.py", tmp_path / "app.py"),
            (requirements, tmp_path / "requirements.txt"),
        ],
        capsys=capsys,
        expected_stdout_path=requirements,
    )
    environment = tmp_path / "environment"
    subprocess.run(["uv", "venv", "--quiet", "--python", sys.executable, environment], check=True)
    python = environment / ("Scripts" if os.name == "nt" else "bin") / "python"
    subprocess.run(
        ["uv", "pip", "install", "--quiet", "--python", python, "--requirement", "requirements.txt"], check=True
    )
    served = subprocess.run([python, "app.py"], capture_output=True, text=True, check=False)
    assert_output(served.stdout + served.stderr, EXPECTED / "served.txt")
