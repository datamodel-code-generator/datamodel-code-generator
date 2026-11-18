"""Write no field example into the dict literal of a functional TypedDict under --use-field-description-example.

The dict literal holds no docstring, so the example lines that the option writes under each field went into it as
raw text and the module did not parse (#7000); they are left out as the field descriptions are.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import OPEN_API_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Pydantic validates typing.TypedDict from Python 3.12 on")
@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(["--use-field-description-example", "--formatters", "builtin"], id="option"),
        pytest.param(["--preset", "practical-py311-20261113"], id="practical-preset"),
    ],
)
def test_typed_dict_functional_description_example(output_file: Path, extra_args: list[str]) -> None:
    """Write ``Headers``, whose keys ``content-type`` and ``rate-limit`` are not names, without their examples."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "typed_dict_functional_description_example.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", "typing.TypedDict", *extra_args],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"typed_dict_functional_description_example_{extra_args[1].replace('-', '_')}",
        model_name="Headers",
        valid_json='{"content-type": "application/json", "rate-limit": 10}',
        invalid_json='{"rate-limit": 10}',
        expected_error_type="missing",
        expected_repr="{'content-type': 'application/json', 'rate-limit': 10}",
    )
