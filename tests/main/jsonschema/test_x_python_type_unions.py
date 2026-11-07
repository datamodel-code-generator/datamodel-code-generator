"""Type a field by an ``x-python-type`` union spelled ``Optional[...]`` or ``Union[...]`` under the option."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "x_python_type_unions.json"


@pytest.mark.cli_doc(
    options=["--use-x-python-type-as-written"],
    option_description="""Write a field's `x-python-type` as given: unions of every spelling and container classes.

An `x-python-type` types its field where it is not compatible with the schema's type, and a union spelled `X | Y`, such
as `Literal['a', 'b'] | None`, never is. Without this option the same union spelled `Optional[Literal['a', 'b']]` or
`Union[Literal['low'], Literal['high']]` counts as compatible with every type the schema declares, so it is ignored
and the field takes the schema's `str`. With it every spelling of a union types the field, at a property, an item, a
map value and a root.

A container class compatible with the schema's type, such as `tuple[str, ...]`, `MutableSequence[str]`, `MutableSet`,
`AbstractSet` or `MutableMapping`, is written as that class around the schema's item, key and value types, which keep
their constraints (`tuple[constr(max_length=3), ...]`), where without the option it is written as `list`, `Sequence`,
`set`, `frozenset` or `Mapping`; --use-generic-container-types and --use-unique-items-as-set no longer rewrite it. A
`prefixItems` tuple stays a tuple.""",
    input_schema="jsonschema/x_python_type_unions.json",
    cli_args=["--use-x-python-type-as-written"],
    golden_output="main/jsonschema/x_python_type_unions/x_python_type_unions[pydantic_v2-option].py",
    comparison_output="main/jsonschema/x_python_type_unions/x_python_type_unions[pydantic_v2].py",
)
@pytest.mark.parametrize(
    ("output_model_type", "extra_args", "validated"),
    [
        pytest.param("pydantic_v2.BaseModel", [], False, id="pydantic_v2"),
        pytest.param("pydantic_v2.BaseModel", ["--use-x-python-type-as-written"], True, id="pydantic_v2-option"),
        pytest.param(
            "pydantic_v2.BaseModel",
            ["--use-x-python-type-as-written", "--collapse-root-models"],
            True,
            id="pydantic_v2-option-collapse_root_models",
        ),
        pytest.param(
            "pydantic_v2.dataclass",
            ["--use-x-python-type-as-written", "--use-annotated"],
            True,
            id="pydantic_dataclass",
        ),
        pytest.param("msgspec.Struct", ["--use-x-python-type-as-written"], False, id="msgspec-option"),
        pytest.param("typing.TypedDict", ["--use-x-python-type-as-written"], False, id="typed_dict-option"),
    ],
)
def test_x_python_type_unions(
    output_file: Path, output_model_type: str, extra_args: list[str], validated: bool
) -> None:
    """Write an ``x-python-type`` union spelled ``Optional[...]`` or ``Union[...]`` as the field's type (#6317).

    Without the option ``mode``, ``level``, the items of ``modes``, the values of ``by_key`` and the root ``Kind`` are
    ``str``, while ``spelled``, the same union written ``Literal['a', 'b'] | None``, takes its annotation. With it every
    one of them takes its annotation, so ``"c"`` for ``mode`` is rejected as its ``Literal`` rejects it.
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
            module_name=f"x_python_type_unions_{output_model_type.replace('.', '_')}",
            model_name="Settings",
            valid_json=json.dumps({
                "mode": "a",
                "level": "low",
                "spelled": None,
                "modes": ["b", None],
                "by_key": {"k": "x"},
                "kind": "k1",
            }),
            invalid_json=json.dumps({"mode": "c"}),
            expected_error_type="literal_error",
        )
