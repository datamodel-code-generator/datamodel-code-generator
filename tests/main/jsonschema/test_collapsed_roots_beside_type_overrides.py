"""Remove the root models collapse inlined from a module that a model-level --type-overrides key changes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT = JSON_SCHEMA_DATA_PATH / "collapsed_roots_beside_type_overrides.json"
OVERRIDES = json.dumps({"Other": "collections.OrderedDict"})


@pytest.mark.cli_doc(
    options=["--remove-unused-models-beside-type-overrides"],
    option_description="""Remove the unused models from a module that a model-level `--type-overrides` key changes.

A model that nothing uses any more, such as a root model `--collapse-root-models` inlined into every field that named
it, leaves the output. In a module from which a model-level `--type-overrides` key
(`{"Other": "collections.OrderedDict"}`) takes the model it replaces, such models stay written, unused:
`class Code(RootModel[constr(max_length=3)])` beside `code: constr(max_length=3) | None`. With this option they leave
that module too, as they leave every other module.""",
    input_schema="jsonschema/collapsed_roots_beside_type_overrides.json",
    cli_args=[
        "--collapse-root-models",
        "--collapse-root-models-name-strategy",
        "child",
        "--type-overrides",
        OVERRIDES,
        "--remove-unused-models-beside-type-overrides",
    ],
    golden_output=(
        "main/jsonschema/collapsed_roots_beside_type_overrides/"
        "collapsed_roots_beside_type_overrides[pydantic_v2-removed].py"
    ),
    comparison_output=(
        "main/jsonschema/collapsed_roots_beside_type_overrides/"
        "collapsed_roots_beside_type_overrides[pydantic_v2-released].py"
    ),
)
@pytest.mark.parametrize(
    "option_set",
    [pytest.param((), id="released"), pytest.param(("--remove-unused-models-beside-type-overrides",), id="removed")],
)
@pytest.mark.parametrize(
    "output_model_type", ["pydantic_v2.BaseModel", "msgspec.Struct"], ids=["pydantic_v2", "msgspec"]
)
def test_collapsed_roots_beside_type_overrides(
    output_file: Path, output_model_type: str, option_set: tuple[str, ...]
) -> None:
    """Leave out the inlined root models of a module that a model-level type override changes under the option (#6371).

    Without it the module keeps `Code`, a constrained string root inlined into `code`, and `Codes`, a root that the
    child strategy replaced by the `Name` it names, both unused beside the override of `Other`, as in 0.90.0; with it
    neither is written, as without the override. The fields keep their types either way.
    """
    run_main_and_assert(
        input_path=INPUT,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *("--output-model-type", output_model_type, "--collapse-root-models"),
            *("--collapse-root-models-name-strategy", "child", "--type-overrides", OVERRIDES),
            *("--formatters", "builtin", "--disable-timestamp"),
            *option_set,
        ],
        force_exec_validation=True,
    )
