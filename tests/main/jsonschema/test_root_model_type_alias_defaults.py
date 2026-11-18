"""End-to-end coverage for the defaults of fields that name root models written as aliases."""

from __future__ import annotations

import json
import warnings
from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

SCHEMA = JSON_SCHEMA_DATA_PATH / "root_model_type_alias_defaults.json"


@pytest.mark.cli_doc(
    options=["--use-validate-default-for-root-model-type-aliases"],
    option_description="""Validate the default of a field that names a root model written as an alias.

`--use-root-model-type-alias` writes a root model as `Tags = RootModel[list[str]]`. Without this option a field whose
type names such a root model, also inside a list, a dict or a union, writes its default as the raw value, as in 0.97.0:
`Settings().tags` is the list `['a', 'b']`, not the `Tags` that `Settings(tags=['a', 'b']).tags` is, the two instances
compare unequal and `model_dump()` warns. With it the field writes `validate_default=True`, as the field of a root
model class does, so Pydantic validates the default into the root model (Pydantic v2 only).""",
    input_schema="jsonschema/root_model_type_alias_defaults.json",
    cli_args=["--use-root-model-type-alias", "--use-validate-default-for-root-model-type-aliases"],
    golden_output="main/jsonschema/root_model_type_alias_defaults/root_model_type_alias_defaults[validated].py",
    comparison_output="main/jsonschema/root_model_type_alias_defaults/root_model_type_alias_defaults[released].py",
)
@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param((), id="released"),
        pytest.param(("--use-validate-default-for-root-model-type-aliases",), id="validated"),
        pytest.param(
            ("--use-validate-default-for-root-model-type-aliases", "--use-annotated"), id="validated-annotated"
        ),
    ],
)
def test_root_model_type_alias_defaults(output_file: Path, extra_args: tuple[str, ...]) -> None:
    """Validate the defaults of fields naming RootModel aliases into the root models, opt-in (#7065).

    ``Settings()`` is compared with ``Settings`` validated from the defaults the schema gives: without the option the
    defaults of ``tags``, ``count``, ``pets``, ``tagLists``, ``tagMap`` and ``tagsOrText`` stay raw values, so the two
    differ and the dump warns; with it, also under ``--use-annotated``, they are the root models and nothing warns.
    """
    run_main_and_assert(
        input_path=SCHEMA,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *("--output-model-type", "pydantic_v2.BaseModel", "--use-root-model-type-alias"),
            *("--target-python-version", "3.10", "--disable-timestamp", "--formatters", "builtin", *extra_args),
        ],
        force_exec_validation=True,
    )
    defaults = {name: value["default"] for name, value in json.loads(SCHEMA.read_text())["properties"].items()}
    with _generated_model(output_file, "root_model_type_alias_defaults", "Settings") as settings:
        empty, given = settings(), settings.model_validate(defaults)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            dump = empty.model_dump_json()
    assert_output(
        f"{dump}\n"
        f"{[type(getattr(empty, name)).__name__ for name in type(empty).model_fields]}\n"
        f"equal to the validated defaults: {empty == given}\n"
        f"warnings: {[type(warning.message).__name__ for warning in caught]}\n",
        name="payloads",
    )
