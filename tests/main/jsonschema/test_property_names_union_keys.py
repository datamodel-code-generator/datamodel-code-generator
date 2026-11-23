"""Type the RootModels that a union map key names by their root types (#6933)."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    _generated_model,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pydantic


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="default"),
        pytest.param(["--field-constraints"], id="field-constraints"),
        pytest.param(["--use-annotated"], id="use-annotated"),
        pytest.param(["--collapse-root-models"], id="collapse-root-models"),
        pytest.param(["--enable-faux-immutability"], id="faux-immutability"),
    ],
)
def test_property_names_root_model_union_keys(extra_args: list[str], output_file: Path) -> None:
    """Type the RootModels that a union map key of RootModels names by their root types, as instances cannot be keys.

    A key of RootModel members alone raised ``TypeError`` for every key it accepts (#6933). Every position of such a
    union key is typed like a key that names the RootModel alone: a ``propertyNames`` ``anyOf`` or ``oneOf``, the map
    of a RootModel, the items of a list, a map in a map value and a member that names another RootModel. Frozen
    RootModels keep their class, which hashes.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "property_names_root_model_union_keys.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=extra_args,
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="property_names_root_model_union_keys",
        model_name="Model",
        valid_json=json.dumps({
            "direct": {"kg": 1, "x-a": 2},
            "limits": {"lb": 1, "x-b": 2},
            "rows": [{"x-c": 1}, {"kg": 2}],
            "outer": {"o": {"x-d": 1, "lb": 2}},
        }),
        invalid_json=json.dumps({"direct": {"zz": 1}}),
        expected_error_type="string_pattern_mismatch",
    )


@pytest.mark.parametrize(
    "extra_args",
    [pytest.param([], id="default"), pytest.param(["--field-constraints"], id="field-constraints")],
)
def test_property_names_root_model_union_key_roots(extra_args: list[str], output_file: Path) -> None:
    """Type the RootModels that the root type of a union key member names too, down to the first repeated model.

    A member whose root type is a union of RootModels is typed through each of them; a RootModel that its own root
    type names again, here through an array, keeps its class there.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "property_names_root_model_union_key_roots.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=extra_args,
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="property_names_root_model_union_key_roots",
        model_name="Model",
        valid_json='{"either": {"kg": 1, "e-a": 2, "long-key": 3}, "recursive": {"r1": 1}}',
        invalid_json='{"recursive": {"x": 1}}',
        expected_error_type="string_pattern_mismatch",
    )


@pytest.mark.parametrize("output_model_type", ["pydantic_v2.dataclass", "typing.TypedDict", "dataclasses.dataclass"])
def test_property_names_root_model_union_keys_output_models(output_file: Path, output_model_type: str) -> None:
    """Keep the union map key of every backend whose root models are type aliases, which hash as their types."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "property_names_root_model_union_keys.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", output_model_type],
        force_exec_validation=True,
    )


def test_property_names_root_model_union_key_modules(output_dir: Path) -> None:
    """Type a union map key by the root type of a RootModel of another module, with the imports its module needs."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "property_names_root_model_union_key_modules",
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
        force_exec_validation=True,
    )


OPTION = "--use-root-types-for-mixed-union-map-keys"
MIXED_KEYS = JSON_SCHEMA_DATA_PATH / "property_names_root_model_mixed_union_keys.json"
UUID_KEY = "123e4567-e89b-12d3-a456-426614174000"
MIXED_PAYLOADS: tuple[tuple[str, str], ...] = (
    ("open", "x-a"),
    ("open", "zz"),
    ("mixed", "kg"),
    ("mixed", "y-1"),
    ("mixed", "x-2"),
    ("mixed", "zz"),
    ("ids", UUID_KEY),
    ("ids", "plain"),
    ("units", "kg"),
    ("units", "x-a"),
    ("anything", "x-a"),
    ("anything", "zz"),
)
"""A map and a key of it for each union key of the input: where a RootModel member matches, where it does not."""


def _key_verdicts(model: type[pydantic.BaseModel]) -> str:
    """Return, for each payload, the type of the parsed key or the error the key raised.

    Python words the unhashable error differently from 3.14 on, so only the part both wordings share is kept.
    """
    lines = []
    for name, key in MIXED_PAYLOADS:
        try:
            parsed = getattr(model.model_validate_json(json.dumps({name: {key: 1}})), name)
        except TypeError as error:
            unhashable = re.search(r"unhashable type: '(\w+)'", str(error))
            verdict = f"TypeError: {unhashable.group(0) if unhashable else error}"
        except ValueError:
            verdict = "rejected"
        else:
            verdict = type(next(iter(parsed))).__name__
        lines.append(f"{name} {key}: {verdict}\n")
    return "".join(lines)


@pytest.mark.cli_doc(
    options=[OPTION],
    option_description="""Type the RootModel members of a union map key with a non-RootModel member by their root types.

A `propertyNames` `anyOf` of an enum, or of a `str` with a `maxLength`, and a RootModel is written with the RootModel
class, which Pydantic cannot hash, so a key that only the RootModel matches raises `TypeError`, and a key that both
match is read by the other member, as in 0.100.0. With the option the member is written as its root type
(`dict[constr(max_length=64) | UUID, int]`), as the key of a RootModel alone is. For JSON input, a key that both the
`str` and a uuid-format RootModel match parses as the `UUID`; for a Python dict input it stays a `str`. A key whose
members are all RootModels is typed by the root types with or without the option.""",
    input_schema="jsonschema/property_names_root_model_mixed_union_keys.json",
    cli_args=[OPTION],
    golden_output="main/jsonschema/property_names_union_keys/property_names_root_model_mixed_union_keys[option].py",
    comparison_output="main/jsonschema/property_names_union_keys/property_names_root_model_mixed_union_keys[released].py",
)
@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="released"),
        pytest.param([OPTION], id="option"),
        pytest.param([OPTION, "--field-constraints"], id="option-field-constraints"),
        pytest.param([OPTION, "--use-annotated"], id="option-annotated"),
    ],
)
def test_property_names_root_model_mixed_union_keys(extra_args: list[str], output_file: Path) -> None:
    """Type the RootModel members of a union key that has a non-RootModel member only under the option.

    Without it the key keeps the RootModel class, as in 0.100.0: a key that only the RootModel matches raises
    ``TypeError`` (an enum beside a pattern RootModel, the shape of #6933, parses only the enum's keys), and a key
    that both a ``str`` and a uuid-format RootModel match parses as ``str``. With it the root types are written and
    that key parses as ``UUID`` from JSON.
    """
    run_main_and_assert(
        input_path=MIXED_KEYS,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=extra_args,
        force_exec_validation=True,
    )
    with _generated_model(output_file, "property_names_root_model_mixed_union_keys", "Model") as model:
        assert_output(_key_verdicts(model), name="payloads")
