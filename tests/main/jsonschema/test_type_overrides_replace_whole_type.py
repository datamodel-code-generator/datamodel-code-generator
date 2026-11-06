"""Replace the whole type of a field with a ``--type-overrides`` class under ``--type-overrides-replace-whole-type``."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "type_overrides_replace_whole_type.json"

OVERRIDES = json.dumps({
    "Inventory.counts": "builtins.dict",
    "Inventory.sized": "builtins.dict",
    "Inventory.amounts": "typing.Any",
    "Inventory.keyed": "collections.abc.Mapping",
    "Inventory.labels": "dict[str, str]",
    "Inventory.tags": "builtins.tuple",
    "Inventory.notes": "builtins.str",
    "Inventory.code": "builtins.bytes",
})
"""A class or an annotation for each map and array of the schema, and a class for its string."""


@pytest.mark.cli_doc(
    options=["--type-overrides-replace-whole-type"],
    option_description="""Make every `--type-overrides` value of a field replace the field's whole type.

A class path of `--type-overrides` names the type of the whole field, but without this option the field keeps the dict
of an object map around it: `{"M.f": "builtins.dict"}` on a map of integers writes `dict[str, dict]`, which rejects
`{"a": 1}`, valid for the schema, and `decimal.Decimal` writes a dict of `Decimal` values. With it the class replaces
the map itself (`dict`, `Decimal`), as an annotation such as `dict[str, int]` already does, and a `propertyNames` key
type leaves with the map.

The item counts of an array and the property counts of a map that an override replaces stay only where the new type
counts the same: a sequence class its items (`builtins.tuple`), a mapping class its keys (`builtins.dict`,
`collections.abc.Mapping`), or a type the generator cannot classify. Without the option they stay whatever the new type
is: a `str` in place of an array with `maxItems` limits the length of the string (`notes`), and `typing.Any` in place of
an array with `minItems` checks the length of every value and raises `TypeError` for one without a length. A count the
new type does not count is left out with a warning.""",
    input_schema="jsonschema/type_overrides_replace_whole_type.json",
    cli_args=["--type-overrides", OVERRIDES, "--type-overrides-replace-whole-type"],
    golden_output="main/jsonschema/type_overrides_replace_whole_type/type_overrides_replace_whole_type[pydantic_v2-replaced].py",
    comparison_output="main/jsonschema/type_overrides_replace_whole_type/type_overrides_replace_whole_type[pydantic_v2].py",
)
@pytest.mark.parametrize(
    ("output_model_type", "extra_args", "validated"),
    [
        pytest.param("pydantic_v2.BaseModel", [], False, id="pydantic_v2"),
        pytest.param("pydantic_v2.BaseModel", ["--type-overrides-replace-whole-type"], True, id="pydantic_v2-replaced"),
        pytest.param(
            "pydantic_v2.dataclass",
            ["--type-overrides-replace-whole-type", "--use-annotated"],
            True,
            id="pydantic_dataclass-replaced",
        ),
        pytest.param("msgspec.Struct", ["--type-overrides-replace-whole-type"], False, id="msgspec-replaced"),
        pytest.param("typing.TypedDict", ["--type-overrides-replace-whole-type"], False, id="typed_dict-replaced"),
    ],
)
def test_type_overrides_replace_whole_type(
    output_file: Path, recwarn: pytest.WarningsRecorder, output_model_type: str, extra_args: list[str], validated: bool
) -> None:
    """Write a class override of a map as the class and keep only the counts the new type counts (#5987).

    ``counts`` and ``sized`` were written as ``dict[str, dict]``, ``amounts`` as ``dict[str, Any]`` and ``keyed`` as
    ``dict[constr(max_length=2), Mapping]``. With the option they are ``dict``, ``Any`` and ``Mapping``: ``sized``
    keeps its ``minProperties`` and ``labels``, an annotation, too, as a dict counts its keys, and ``tags`` keeps its
    ``minItems`` on a tuple; ``amounts`` and ``notes`` leave their counts out with a warning, as ``Any`` counts no keys
    and a ``str`` no items. The lengths of the string ``code`` stay where the field writes them, as without the option.
    msgspec checks no count on a class its table does not list.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *("--output-model-type", output_model_type, "--type-overrides", OVERRIDES),
            *extra_args,
            *("--formatters", "builtin", "--disable-timestamp", "--target-python-version", "3.10"),
        ],
        force_exec_validation=True,
    )
    assert_output(
        "".join(
            f"{message}\n"
            for message in dict.fromkeys(str(warning.message) for warning in recwarn if warning.category is UserWarning)
        ),
        name="warnings",
    )
    if validated:
        assert_generated_model_json_validation(
            output_file,
            module_name=f"type_overrides_replace_whole_type_{output_model_type.replace('.', '_')}",
            model_name="Inventory",
            valid_json=json.dumps({"counts": {"a": 1}, "sized": {"a": 1}, "labels": {"a": "b"}, "tags": ["x"]}),
            invalid_json=json.dumps({"sized": {}}),
            expected_error_type="too_short",
        )
