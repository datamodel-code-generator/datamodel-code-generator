"""Preserve inherited root wrappers during tree reuse and root collapse."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from datamodel_code_generator import InputFileType, OpenAPIScope, chdir
from tests.main.conftest import (
    OPEN_API_DATA_PATH,
    _assert_generated_package_model_validation,
    _generated_package_module,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.openapi.conftest import EXPECTED_OPENAPI_PATH

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("include_api", [False, True], ids=["schemas", "schemas-api"])
def test_collapsed_tree_reuse_partial(output_dir: Path, entrypoint: str, *, include_api: bool) -> None:
    """Generate and validate the existing partial-schema package with inherited roots."""
    expected_directory = EXPECTED_OPENAPI_PATH / "collapsed_tree_reuse_partial"
    if entrypoint == "cli":
        with chdir(output_dir.parent):
            run_main_and_assert(
                input_path=OPEN_API_DATA_PATH / "allof_partial_unconstrained_schemas.yaml",
                output_path=output_dir,
                input_file_type="openapi",
                expected_directory=expected_directory,
                extra_args=[
                    "--reuse-model",
                    "--reuse-scope",
                    "tree",
                    "--collapse-root-models",
                    "--disable-timestamp",
                    *(["--openapi-scopes", "schemas", "api"] if include_api else []),
                ],
                force_exec_validation=True,
            )
    else:
        run_generate_file_and_assert(
            input_path=OPEN_API_DATA_PATH / "allof_partial_unconstrained_schemas.yaml",
            output_path=output_dir,
            input_file_type=InputFileType.OpenAPI,
            expected_directory=expected_directory,
            reuse_model=True,
            reuse_scope="tree",
            collapse_root_models=True,
            disable_timestamp=True,
            **({"openapi_scopes": [OpenAPIScope.Schemas, OpenAPIScope.Api]} if include_api else {}),
        )
    valid_payload = {
        "arrayValue": [{"code": "ok"}],
        "mappingValue": {"item": {"code": "ok"}},
        "anyEmptyValue": {"code": "ok"},
        "anyTrueValue": {"code": "ok"},
        "oneEmptyValue": {"code": "ok"},
        "oneTrueValue": {"code": "ok"},
        "allEmptyValue": {"code": "ok"},
        "allTrueValue": {"code": "ok"},
        "nullableValue": {"code": "ok"},
        "nullableObjectValue": {"code": "ok"},
        "directScalarValue": "a",
        "directScalarInferred": "a",
        "inlineScalarInferred": "a",
        "scalarArrayInferred": ["a"],
        "scalarMappingInferred": {"item": "a"},
        "scalarDeepInferred": [["a"]],
        "scalarArrayRootInferred": ["ab"],
        "scalarMappingRootInferred": {"item": "ab"},
        "arrayNeutralComposition": [{"code": "ok"}],
        "mappingNeutralComposition": {"item": {"code": "ok"}},
        "deepArrayNeutralComposition": [[{"code": "ok"}]],
        "scalarArrayWeaker": ["a"],
        "scalarMappingWeaker": {"item": "a"},
        "scalarDeepWeaker": [["a"]],
        "prefixItemsNeutral": ["ab", 1],
        "legacyItemsNeutral": ["ab", 1],
        "unevaluatedItemsNeutral": ["ab", 1],
        "inlineObjectNeutral": {"code": "ab"},
        "refObjectNeutral": {"code": "ab"},
    }
    _assert_generated_package_model_validation(output_dir, module_path="", model_name="Child", data=valid_payload)
    with _generated_package_module(output_dir, "") as module:
        with pytest.raises(ValidationError, match="String should have at least 2 characters"):
            module.Base.model_validate(valid_payload)
        with pytest.raises(ValidationError, match="Field required"):
            module.Child.model_validate({
                field_name: value for field_name, value in valid_payload.items() if field_name != "directScalarValue"
            })
        with pytest.raises(ValidationError, match="String should have at least 2 characters"):
            module.Child.model_validate({**valid_payload, "arrayValue": [{"code": "x"}]})
        with pytest.raises(ValidationError, match="Input should be a valid string"):
            module.Child.model_validate({**valid_payload, "scalarArrayRootInferred": [1]})
        for field_name in ("prefixItemsNeutral", "legacyItemsNeutral", "unevaluatedItemsNeutral"):
            with pytest.raises(ValidationError, match="String should have at least 2 characters"):
                module.Child.model_validate({**valid_payload, field_name: ["x", 1]})
