"""Write the dataclass decorator arguments set to false under ``--keep-false-dataclass-arguments``."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "keep_false_dataclass_arguments.json"

ARGUMENTS = json.dumps({"eq": False, "frozen": True})
"""Arguments that ask the dataclasses to compare by identity, and to be frozen."""


@pytest.mark.cli_doc(
    options=["--keep-false-dataclass-arguments"],
    option_description="""Write every dataclass decorator argument set to false.

Without this option an argument of `--dataclass-arguments` set to `false` is left out of the decorator, so it takes
its default: `{"eq": false, "frozen": true}` writes `@dataclass(frozen=True)`, which compares by value and hashes the
values of its fields (`eq=True` is the default). With it the decorator writes `@dataclass(eq=False, frozen=True)`, so
the dataclass compares by identity as asked. Every decision that reads the decorator reads what it writes: a set of
such dataclasses under `--use-unique-items-as-set-by-value` is a list with the option, as they hash by identity.""",
    input_schema="jsonschema/keep_false_dataclass_arguments.json",
    cli_args=[
        *("--output-model-type", "dataclasses.dataclass", "--dataclass-arguments", ARGUMENTS),
        "--keep-false-dataclass-arguments",
    ],
    golden_output="main/jsonschema/keep_false_dataclass_arguments/keep_false_dataclass_arguments[dataclass-kept].py",
    comparison_output="main/jsonschema/keep_false_dataclass_arguments/keep_false_dataclass_arguments[dataclass].py",
)
@pytest.mark.parametrize(
    ("output_model_type", "extra_args"),
    [
        pytest.param("dataclasses.dataclass", [], id="dataclass"),
        pytest.param("dataclasses.dataclass", ["--keep-false-dataclass-arguments"], id="dataclass-kept"),
        pytest.param("pydantic_v2.dataclass", [], id="pydantic_dataclass"),
        pytest.param("pydantic_v2.dataclass", ["--keep-false-dataclass-arguments"], id="pydantic_dataclass-kept"),
    ],
)
def test_keep_false_dataclass_arguments(output_file: Path, output_model_type: str, extra_args: list[str]) -> None:
    """Write ``eq=False`` of ``--dataclass-arguments`` with the option, and decide sets by what is written (#6331).

    Without the option ``eq=False`` is left out, so ``Item`` compares by value and, frozen, hashes by value, and
    ``items`` is a set under ``--use-unique-items-as-set-by-value``. With it ``Item`` compares by identity and hashes
    by identity, so ``items`` is a list.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *("--output-model-type", output_model_type, "--dataclass-arguments", ARGUMENTS),
            *("--use-unique-items-as-set", "--use-unique-items-as-set-by-value", "--formatters", "builtin"),
            *extra_args,
        ],
        force_exec_validation=True,
    )
    module_name = f"keep_false_dataclass_arguments_{output_model_type.replace('.', '_')}_{len(extra_args)}"
    with _generated_model(output_file, module_name, "Item") as item:
        assert_output(f"Item(x=1) == Item(x=1): {item(x=1) == item(x=1)}\n", name="equality")
