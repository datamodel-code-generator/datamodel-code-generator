"""Keep string length bounds on witnessed root-array pattern adapters."""

from __future__ import annotations

import json
import operator
from contextlib import nullcontext
from typing import TYPE_CHECKING

import pytest
from jsonschema import Draft202012Validator
from pydantic import VERSION as PYDANTIC_VERSION
from pydantic import ValidationError

from datamodel_code_generator import GenerateConfig, InputFileType, generate
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.model.base import TEMPLATE_DIR
from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, JSON_SCHEMA_DATA_PATH, _generated_model, run_main_with_args
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("formatter", ["builtin", "external"])
@pytest.mark.parametrize(
    "case",
    json.loads((DATA_PATH / "payloads/pattern_string_adapters/cases.json").read_text()),
    ids=operator.itemgetter("name"),
)
def test_pattern_string_adapters(tmp_path: Path, entrypoint: str, formatter: str, case: dict) -> None:
    """Exercise native validation and exact output through both public entrypoints."""
    source = JSON_SCHEMA_DATA_PATH / "pattern_string_adapters" / f"{case.get('source', case['name'])}.json"
    output = tmp_path / "model.py"
    options = {"field_constraints": True, "generate_schema_validators": True, **case["options"]}
    formatters = ["builtin"] if formatter == "builtin" else ["black", "isort"]
    # Pydantic 2.0 emits these same serializer diagnostics before this fix.
    warning_context = (
        pytest.warns(UserWarning, match="Pydantic serializer warnings:")
        if case["name"] == "inherited" and PYDANTIC_VERSION in {"2.0", "2.0.0"}
        else nullcontext()
    )
    with warning_context as recorded_warnings:
        if entrypoint == "cli":
            args = ["--input", str(source), "--input-file-type", "jsonschema", "--output", str(output)]
            args.extend(["--disable-timestamp", "--formatters", *formatters])
            if case["custom"]:
                args.extend(["--custom-template-dir", str(TEMPLATE_DIR)])
            for key, value in options.items():
                if value:
                    args.append(
                        {
                            "apply_default_values_for_required_fields": "--use-default",
                            "force_optional_for_required_fields": "--force-optional",
                        }.get(key, "--" + key.replace("_", "-"))
                    )
                    if isinstance(value, list):
                        args.extend(value)
            run_main_with_args(args)
        else:
            generate(
                source,
                config=GenerateConfig(
                    input_file_type=InputFileType.JsonSchema,
                    output=output,
                    disable_timestamp=True,
                    formatters=[Formatter(value) for value in formatters],
                    custom_template_dir=TEMPLATE_DIR if case["custom"] else None,
                    **options,
                ),
            )
    if recorded_warnings is not None:
        assert_output(
            json.dumps(
                [str(warning.message) for warning in recorded_warnings if issubclass(warning.category, UserWarning)],
                indent=2,
            )
            + "\n",
            EXPECTED_JSON_SCHEMA_PATH / f"pattern_string_adapters/inherited_minimum_{entrypoint}_warnings.txt",
        )
    assert_output(output.read_text(), EXPECTED_JSON_SCHEMA_PATH / "pattern_string_adapters" / f"{case['name']}.py")
    results = []
    with _generated_model(output, "pattern_string_adapter", "Root") as model:
        for payload in json.loads((DATA_PATH / "payloads/pattern_string_adapters/values.json").read_text()):
            result = {"native": Draft202012Validator(json.loads(source.read_text())).is_valid(payload)}
            try:
                result["dump"] = model.model_validate_json(json.dumps(payload)).model_dump(mode="json")
            except ValidationError:
                result["error"] = "ValidationError"
            results.append(result)
    assert_output(
        json.dumps(results, indent=2) + "\n",
        EXPECTED_JSON_SCHEMA_PATH / "pattern_string_adapters" / f"{case['name']}.runtime.txt",
    )
