"""Retain root literal constraints across recursive and variant representations."""

from __future__ import annotations

import json
from contextlib import nullcontext
from typing import TYPE_CHECKING

import pytest
import yaml
from jsonschema.validators import validator_for
from pydantic import VERSION as PYDANTIC_VERSION
from pydantic import ValidationError

from datamodel_code_generator import Formatter, GenerateConfig, InputFileType, ReadOnlyWriteOnlyModelType, generate
from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, _generated_model, run_main_with_args
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH

if TYPE_CHECKING:
    from pathlib import Path

CASES = json.loads((DATA_PATH / "payloads/allof_root_regressions/cases.json").read_text())
EXPECTED = EXPECTED_JSON_SCHEMA_PATH / "allof_root_regressions"


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("field_constraints", [False, True])
@pytest.mark.parametrize("formatter", ["builtin", "external"])
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_allof_root_regressions(
    tmp_path: Path, case: str, field_constraints: bool, formatter: str, entrypoint: str
) -> None:
    """Exercise real generation, original-schema validity, and native model validation."""
    record = CASES[case]
    source = DATA_PATH / "allof_root_regressions" / record["source"]
    output = tmp_path / "output.py"
    formatters = ["builtin"] if formatter == "builtin" else ["black", "isort"]
    warning_context = (
        pytest.warns(UserWarning, match="Pydantic serializer warnings:")
        if case != "nonfinite" and PYDANTIC_VERSION in {"2.0", "2.0.0"}
        else nullcontext()
    )
    with warning_context as recorded_warnings:
        if entrypoint == "cli":
            run_main_with_args([
                "--input",
                str(source),
                "--output",
                str(output),
                "--input-file-type",
                "jsonschema",
                "--disable-timestamp",
                "--generate-schema-validators",
                "--formatters",
                *formatters,
                *(["--field-constraints"] if field_constraints else []),
                *(["--read-only-write-only-model-type", "request-response"] if record["variants"] else []),
            ])
        else:
            generate(
                source,
                config=GenerateConfig(
                    output=output,
                    input_file_type=InputFileType.JsonSchema,
                    disable_timestamp=True,
                    generate_schema_validators=True,
                    field_constraints=field_constraints,
                    formatters=[Formatter(value) for value in formatters],
                    **(
                        {"read_only_write_only_model_type": ReadOnlyWriteOnlyModelType.RequestResponse}
                        if record["variants"]
                        else {}
                    ),
                ),
            )
    if recorded_warnings is not None:
        assert_output(
            json.dumps(
                [str(warning.message) for warning in recorded_warnings if issubclass(warning.category, UserWarning)],
                indent=2,
            )
            + "\n",
            EXPECTED / f"{case}_{entrypoint}_{field_constraints}_minimum_warnings.txt",
        )
    assert_output(output.read_text(), EXPECTED / f"{case}_{field_constraints}_{formatter}.py")
    schema = yaml.safe_load(source.read_text())
    validator_class = validator_for(schema)
    validator_class.check_schema(schema)
    native = validator_class(schema)
    results = []
    for model_name in record["models"]:
        with _generated_model(output, "allof_root_regression", model_name) as model:
            for payload in record["payloads"]:
                row = {"model": model_name, "native": native.is_valid(payload)}
                if row["native"]:
                    row["value"] = model.model_validate(payload).model_dump(mode="json")
                else:
                    with pytest.raises(ValidationError):
                        model.model_validate(payload)
                    row["error"] = "ValidationError"
                results.append(row)
    assert_output(json.dumps(results, indent=2) + "\n", EXPECTED / f"{case}_runtime.txt")
