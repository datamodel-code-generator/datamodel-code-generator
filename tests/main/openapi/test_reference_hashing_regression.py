"""Regressions for inherited bases redirected during content reuse."""

from __future__ import annotations

from pathlib import Path
from shutil import copyfile

import pytest

from datamodel_code_generator import InputFileType, OpenAPIScope
from tests.main.conftest import (
    OPEN_API_DATA_PATH,
    assert_generated_model_json_invalid,
    assert_generated_model_json_validation,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.openapi.conftest import assert_file_content


@pytest.mark.parametrize("entrypoint", ["generate", "cli"])
@pytest.mark.parametrize("api_scope", [False, True], ids=["schemas", "schemas-api"])
def test_reused_inherited_bases(tmp_path: Path, entrypoint: str, api_scope: bool) -> None:
    """Collapse duplicate parents while keeping required nested fields and diamond inheritance."""
    output = tmp_path / "models"
    source = OPEN_API_DATA_PATH / "allof_required_inherited_nested_inline.yaml"
    expected = f"reuse_reference_identity{'_cli' if entrypoint == 'cli' else ''}.py"
    if entrypoint == "generate":
        run_generate_file_and_assert(
            input_path=source,
            output_path=output,
            input_file_type=InputFileType.OpenAPI,
            assert_func=assert_file_content,
            expected_file=expected,
            reuse_model=True,
            collapse_reuse_models=True,
            disable_timestamp=True,
            formatters=[],
            **({"openapi_scopes": [OpenAPIScope.Schemas, OpenAPIScope.Api]} if api_scope else {}),
        )
    else:
        run_main_and_assert(
            input_path=source,
            output_path=output,
            input_file_type="openapi",
            assert_func=assert_file_content,
            expected_file=expected,
            extra_args=[
                "--formatters",
                "builtin",
                "--reuse-model",
                "--collapse-reuse-models",
                "--disable-timestamp",
                *(["--openapi-scopes", "schemas", "api"] if api_scope else []),
            ],
            force_exec_validation=True,
        )
    import_output = Path(copyfile(output, tmp_path / "models.py"))
    for model_name in ("BaseFirstDerived", "BaseFirstGrandchild", "DiamondDerived"):
        module_name = f"reference_identity_{entrypoint}_{api_scope}_{model_name}"
        assert_generated_model_json_validation(
            import_output,
            module_name=module_name,
            model_name=model_name,
            valid_json='{"detail":{"id":1}}',
            invalid_json='{"detail":{"id":"bad"}}',
            expected_error_type="int_parsing",
            expected_attribute_path=("detail", "id"),
            expected_attribute_value=1,
        )
        for invalid_json in ("{}", '{"detail":{}}'):
            assert_generated_model_json_invalid(
                import_output,
                module_name=module_name,
                model_name=model_name,
                invalid_json=invalid_json,
                expected_error_type="missing",
            )
