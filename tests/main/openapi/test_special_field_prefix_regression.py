"""Exercise numeric names exposed by prefix removal through public generation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType, InputFileType, OpenAPIScope, generate
from tests.conftest import assert_generated_file_matches_output, assert_output, validate_generated_code

DATA = Path(__file__).parents[2] / "data"
SOURCE = DATA / "generation_platform/api_scope/discriminator.json"
EXPECTED = DATA / "expected/main/openapi"
DRIVER = DATA / "python/core_special_field_prefix_generate.py"


@pytest.mark.parametrize("api_scope", [False, True], ids=["schemas", "schemas-api"])
@pytest.mark.parametrize("route", ["cli", "generate"])
def test_special_field_prefix_discriminator(route: str, api_scope: bool, tmp_path: Path) -> None:
    """Complete generation with distinct percent-key models and canonical discriminator references."""
    output = tmp_path / "models.py"
    if route == "cli":
        (tmp_path / "pyproject.toml").write_text("[tool.datamodel-codegen]\nformatters = []\n", encoding="utf-8")
        command = [
            sys.executable,
            "-m",
            "datamodel_code_generator",
            "--input",
            str(SOURCE),
            "--input-file-type",
            "openapi",
            "--output",
            str(output),
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--special-field-name-prefix",
            "x",
            "--remove-special-field-name-prefix",
            "--disable-timestamp",
        ]
        if api_scope:
            command.extend(["--openapi-scopes", "schemas", "api"])
    else:
        command = [sys.executable, str(DRIVER), str(SOURCE), str(output)]
        if api_scope:
            command.append("api")
    subprocess.run(command, cwd=tmp_path, check=True, timeout=60)
    code = output.read_text(encoding="utf-8")
    expected = EXPECTED / (
        "special_field_prefix_discriminator.py" if api_scope else "special_field_prefix_discriminator_schemas.py"
    )
    assert_output(code, expected)
    if route == "generate":
        assert_generated_file_matches_output(
            generate(
                SOURCE,
                input_file_type=InputFileType.OpenAPI,
                output_model_type=DataModelType.PydanticV2BaseModel,
                special_field_name_prefix="x",
                remove_special_field_name_prefix=True,
                disable_timestamp=True,
                formatters=[],
                **({"openapi_scopes": [OpenAPIScope.Schemas, OpenAPIScope.Api]} if api_scope else {}),
            ),
            output,
        )
    validate_generated_code(code, str(output), do_exec=True)
