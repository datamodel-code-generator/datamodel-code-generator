"""Regressions for model reuse through empty intermediate package modules."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import InputFileType, OpenAPIScope
from tests.main.conftest import (
    EXPECTED_OPENAPI_PATH,
    OPEN_API_DATA_PATH,
    _generated_package_module,
    run_generate_file_and_assert,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path


_PACKAGE_IMPORTS = [("", None), ("parent", "ParentModel"), ("parent.child", None), ("parent.child.deep", "DeepModel")]


@pytest.mark.parametrize(("module_path", "model_name"), _PACKAGE_IMPORTS)
@pytest.mark.parametrize("include_api", [False, True], ids=["schemas", "schemas-api"])
def test_generate_reuse_with_empty_modules(
    output_dir: Path, include_api: bool, module_path: str, model_name: str | None
) -> None:
    """Compare generated package bytes and import each retained module and model."""
    run_generate_file_and_assert(
        input_path=OPEN_API_DATA_PATH / "all_exports_no_child.yaml",
        output_path=output_dir,
        input_file_type=InputFileType.OpenAPI,
        expected_directory=EXPECTED_OPENAPI_PATH / "reuse_dedup_empty_modules",
        reuse_model=True,
        collapse_reuse_models=True,
        disable_timestamp=True,
        **({"openapi_scopes": [OpenAPIScope.Schemas, OpenAPIScope.Api]} if include_api else {}),
    )
    with _generated_package_module(output_dir, module_path) as module:
        if model_name is not None:
            getattr(module, model_name)


@pytest.mark.parametrize(("module_path", "model_name"), _PACKAGE_IMPORTS)
@pytest.mark.parametrize("include_api", [False, True], ids=["schemas", "schemas-api"])
def test_cli_reuse_with_empty_modules(
    output_dir: Path, include_api: bool, module_path: str, model_name: str | None
) -> None:
    """Compare CLI package bytes, then explicitly import each retained module and model."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "all_exports_no_child.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        expected_directory=EXPECTED_OPENAPI_PATH / "reuse_dedup_empty_modules",
        extra_args=[
            "--reuse-model",
            "--collapse-reuse-models",
            "--disable-timestamp",
            *(["--openapi-scopes", "schemas", "api"] if include_api else []),
        ],
    )
    with _generated_package_module(output_dir, module_path) as module:
        if model_name is not None:
            getattr(module, model_name)
