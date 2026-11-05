"""Write the dataclass decorator arguments set to false under ``--keep-false-dataclass-arguments``."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator.__main__ import Exit
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
such dataclasses under `--use-unique-items-as-set-by-value` is a list with the option, as they hash by identity.
The generation stops on arguments the decorator refuses when the module is imported, such as `eq=False` beside
`order=True`, `weakref_slot` without `slots=True` or for Python 3.10, and `match_args`, `weakref_slot` or `init=True`
for a Pydantic dataclass.""",
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


REFUSED = {
    "order_without_eq": (
        "dataclasses.dataclass",
        {"eq": False, "order": True},
        "3.11",
        "@dataclass(eq=False, order=True): order=True needs eq=True",
    ),
    "weakref_slot_without_slots": (
        "dataclasses.dataclass",
        {"slots": False, "weakref_slot": True},
        "3.11",
        "@dataclass(slots=False, weakref_slot=True): weakref_slot=True needs slots=True",
    ),
    "weakref_slot_on_python_3_10": (
        "dataclasses.dataclass",
        {"weakref_slot": False},
        "3.10",
        "@dataclass(weakref_slot=False): weakref_slot needs --target-python-version 3.11 or higher",
    ),
    "pydantic_dataclass": (
        "pydantic_v2.dataclass",
        {"init": True, "eq": False, "order": True, "match_args": False, "weakref_slot": False},
        "3.11",
        "@dataclass(init=True, eq=False, order=True, match_args=False, weakref_slot=False): Pydantic dataclasses take "
        "no match_args; Pydantic dataclasses take no weakref_slot; Pydantic dataclasses take only init=False; "
        "order=True needs eq=True",
    ),
}
"""Decorator arguments that ``dataclass()`` refuses when the module is imported, and why the generation stops."""


@pytest.mark.parametrize(
    ("case", "keep_false"),
    [
        *(pytest.param(case, True, id=case) for case in REFUSED),
        pytest.param("order_without_eq", False, id="order_without_eq-left-out"),
        pytest.param("weakref_slot_on_python_3_10", False, id="weakref_slot_on_python_3_10-left-out"),
    ],
)
def test_keep_false_dataclass_arguments_refused(
    output_file: Path, capsys: pytest.CaptureFixture[str], case: str, *, keep_false: bool
) -> None:
    """Stop for decorator arguments that the dataclass refuses at import, instead of writing the module.

    With the option the decorator writes every argument, so ``eq=False`` beside ``order=True`` raised ``ValueError``
    and ``weakref_slot=False`` for Python 3.10, or for a Pydantic dataclass, ``TypeError`` when the module was
    imported. The error names the model, the decorator and why. Without the option an argument set to false is left
    out, so the same arguments write a decorator that imports, as in 0.89.0.
    """
    output_model_type, arguments, target, message = REFUSED[case]
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *("--output-model-type", output_model_type, "--dataclass-arguments", json.dumps(arguments)),
            *("--target-python-version", target, "--formatters", "builtin"),
            *(["--keep-false-dataclass-arguments"] if keep_false else []),
        ],
        expected_exit=Exit.ERROR if keep_false else Exit.OK,
        capsys=capsys,
        expected_stderr_contains=f"model 'Item' cannot write {message}" if keep_false else None,
        output_should_not_exist=keep_false,
        force_exec_validation=not keep_false,
    )
