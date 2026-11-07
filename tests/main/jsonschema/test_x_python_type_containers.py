"""Write a container class an ``x-python-type`` names, compatible with the schema, as given under the option."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "x_python_type_containers.json"
OPTION = "--use-x-python-type-as-written"


@pytest.mark.parametrize(
    ("output_model_type", "extra_args", "validated"),
    [
        pytest.param("pydantic_v2.BaseModel", [], False, id="pydantic_v2"),
        pytest.param("pydantic_v2.BaseModel", [OPTION], True, id="pydantic_v2-option"),
        pytest.param(
            "pydantic_v2.BaseModel",
            [OPTION, "--collapse-root-models"],
            True,
            id="pydantic_v2-option-collapse_root_models",
        ),
        pytest.param(
            "pydantic_v2.BaseModel",
            [OPTION, "--use-generic-container-types", "--use-unique-items-as-set"],
            True,
            id="pydantic_v2-option-generic_unique_sets",
        ),
        pytest.param(
            "pydantic_v2.BaseModel",
            [OPTION, "--use-unique-items-as-set", "--use-unique-items-as-set-by-value"],
            True,
            id="pydantic_v2-option-unique_sets_by_value",
        ),
        pytest.param(
            "pydantic_v2.BaseModel", [OPTION, "--no-use-standard-collections"], True, id="pydantic_v2-option-typing"
        ),
        pytest.param("pydantic_v2.dataclass", [OPTION], True, id="pydantic_dataclass-option"),
        pytest.param("msgspec.Struct", [OPTION], False, id="msgspec-option"),
        pytest.param("typing.TypedDict", [OPTION], False, id="typed_dict-option"),
        pytest.param("dataclasses.dataclass", [OPTION], False, id="dataclass-option"),
    ],
)
def test_x_python_type_containers(
    output_file: Path, output_model_type: str, extra_args: list[str], validated: bool
) -> None:
    """Write a compatible container ``x-python-type`` around the schema's item, key and value types (#6138).

    Without the option ``tags`` and ``flags`` (``tuple``) are lists, ``names``, ``pets``, ``aliases`` and ``Codes``
    (``MutableSequence``) sequences, ``ids`` (``MutableSet``) a set, ``frozen_ids`` (``AbstractSet``) a frozen set and
    ``counts`` (``MutableMapping``) a mapping. With it each is written as the class it names, with the schema's items:
    ``tags`` still rejects a four-character tag, ``point`` (``tuple[int, int]`` over ``items``) is ``tuple[int, ...]``,
    and ``pair``, a ``prefixItems`` tuple, stays one. The class is the ``x-python-type``'s, so
    --use-generic-container-types keeps ``list`` and ``set`` and --use-unique-items-as-set keeps the ``list`` of
    ``unique_ids``, while ``routes``, a uniqueItems array of tuples, becomes a set of them and ``batches``, of
    ``MutableSequence`` items that do not hash, stays a list. ``ratio`` (``int`` on a ``number``) stays ``float``.
    ``Tagged`` overrides ``names`` through ``allOf`` with a schema of its own.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *("--output-model-type", output_model_type),
            *extra_args,
            *("--formatters", "builtin", "--disable-timestamp", "--target-python-version", "3.10"),
        ],
        force_exec_validation=True,
    )
    if validated:
        assert_generated_model_json_validation(
            output_file,
            module_name=f"x_python_type_containers_{output_model_type.replace('.', '_')}",
            model_name="Inventory",
            valid_json=json.dumps({
                "tags": ["a", "bc"],
                "names": ["x"],
                "ids": [1],
                "frozen_ids": [2],
                "counts": {"k": 1},
                "grid": [[1, 2]],
                "point": [1, 2, 3],
                "routes": [[1, 2]],
                "pair": [1, 2],
            }),
            invalid_json=json.dumps({"tags": ["abcd"]}),
            expected_error_type="string_too_long",
            expected_attribute_path=("point",),
            expected_attribute_value=(1, 2, 3),
        )
