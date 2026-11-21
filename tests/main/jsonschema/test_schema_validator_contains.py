"""Check the item counts that contains, minContains and maxContains give under --generate-schema-validators (#7052)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pydantic
import pytest
from jsonschema.validators import validator_for

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "schema_validator_contains.json"
DOCUMENTS = (
    {"keys": ["k1", "a"]},
    {"keys": ["k1", "k2"]},
    {"keys": ["a"]},
    {"ints": [1, "2"]},
    {"ints": [1, 2]},
    {"ints": [1.0, 2]},
    {"ints": [True, 1]},
    {"ints": ["1"]},
    {"ints": [1.5, 2]},
    {"nums": [10, 10.5]},
    {"nums": [10, 10.25]},
    {"nums": [19.5, 20, 11]},
    {"nums": [10, 11, 12, 13]},
    {"counts": [3, 4.5, -3, 33]},
    {"counts": [3.0, 6]},
    {"counts": [True, 0, 3]},
    {"flags": [True]},
    {"flags": [1.0]},
    {"flags": [True, 1]},
    {"shapes": [{"a": [1, False]}, {"a": [True, False]}]},
    {"shapes": [{"a": [1.0, False]}, {"a": [1, False]}]},
    {"letters": []},
    {"letters": ["bb", "a"]},
    {"letters": ["a", "a"]},
    {"nullable": [1, None]},
    {"nullable": [True, None]},
    {"nullable": [1, "x"]},
    {"containers": [{}, 1]},
    {"containers": [{}, [1]]},
    {"grid": [[5], [1, 6]]},
    {"grid": [[5], [1]]},
    {"byName": {"a": [0], "b": [1, 0]}},
    {"byName": {"a": [0, 0]}},
    {"mixed": ["a", "b", 1]},
    {"mixed": ["a", 1]},
    {"mixed": ["a", "b", 1, 2]},
    {"rows": [["p"], ["t"], ["x", "t"]]},
    {"rows": [["x"]]},
    {"rows": [["p"], ["t", "t"]]},
    {"tags": ["#a", "b"]},
    {"tags": ["#a", "#b", "#c"]},
    {"tags": ["b"]},
    {"tagLists": [["#a"], ["#b", "#c", "#d"]]},
    {"either": ["x"]},
    {"either": ["y"]},
    {"either": "s"},
    {"records": [{"kind": 1}, {"kind": 2}]},
    {"emails": ["a@b.c"]},
    {"anything": [1, 2]},
    {"nothing": [1, 2]},
    {"optional": ["a"]},
)
ROOT_PATH = JSON_SCHEMA_DATA_PATH / "schema_validator_contains_root.json"
DRAFT4_PATH = JSON_SCHEMA_DATA_PATH / "schema_validator_contains_draft4.json"
DRAFT7_PATH = JSON_SCHEMA_DATA_PATH / "schema_validator_contains_draft7.json"
INEXACT_PATH = JSON_SCHEMA_DATA_PATH / "schema_validator_contains_inexact.json"


@pytest.mark.parametrize(
    "option",
    [
        ("--generate-schema-validators",),
        ("--generate-schema-validators", "--strict-types", "str", "bytes", "int", "float", "bool"),
        ("--schema-validator-type", "pydantic-v2"),
        ("--generate-schema-validators", "--collapse-root-models"),
        ("--generate-schema-validators", "--ignore-enum-constraints"),
    ],
)
def test_schema_validator_contains(output_file: Path, option: tuple[str, ...]) -> None:
    """Count the items the contains schema accepts as JSON Schema reads it, whatever the generated item type takes.

    A boolean is no number, ``1.0`` is an integer and a string keyword applies to strings only. A contains with a
    keyword the matcher does not read, a pattern Python rejects, or an enum ``--ignore-enum-constraints`` drops gets
    no count, so it accepts what the schema rejects but never rejects what the schema accepts.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            *option,
        ],
    )
    with _generated_model(output_file, INPUT_PATH.stem, "Probe") as model:
        assert_output(_verdicts(INPUT_PATH, model, DOCUMENTS), name="payloads")


@pytest.mark.parametrize(
    ("input_path", "documents"),
    [
        (ROOT_PATH, ([], ["other"], ["target"], ["target", "target"])),
        (DRAFT4_PATH, ([], ["target", "target"])),
        (DRAFT7_PATH, ([], ["other"], ["target"], ["target", "target"])),
        (
            INEXACT_PATH,
            (
                {"steps": [0.5, 1.0]},
                {"steps": [0.5, 0.3]},
                {"even": [2, 4]},
                {"even": [2, 3]},
            ),
        ),
    ],
    ids=["root", "draft4", "draft7", "inexact"],
)
def test_schema_validator_contains_rule_scope(
    output_file: Path, input_path: Path, documents: tuple[object, ...]
) -> None:
    """Count the items of a root array (#7052) where the matcher is exact for the dialect and the draft's keywords.

    A draft-04 contains, where const is not defined, gets no count; a draft-07 contains needs one match and ignores
    ``minContains`` and ``maxContains``; a ``multipleOf`` that is no integer gets none.
    """
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--generate-schema-validators",
        ],
    )
    with _generated_model(output_file, input_path.stem, "Probe") as model:
        assert_output(_verdicts(input_path, model, documents))


def _verdicts(input_path: Path, model: type[pydantic.BaseModel], documents: tuple[object, ...]) -> str:
    """Return the schema's verdict and the generated model's in Python and in JSON for each document."""
    schema = json.loads(input_path.read_text(encoding="utf-8"))
    validator = validator_for(schema)(schema)

    return "".join(
        f"{json.dumps(document)} ({'valid' if validator.is_valid(document) else 'invalid'}): "
        f"{_accepts(model.model_validate, document)}/"
        f"{_accepts(model.model_validate_json, json.dumps(document))}\n"
        for document in documents
    )


def _accepts(validate: Callable[..., object], value: object) -> str:
    """Return whether ``validate`` accepts ``value``."""
    try:
        validate(value)
    except pydantic.ValidationError:
        return "rejected"
    return "accepted"
