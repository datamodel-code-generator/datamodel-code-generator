"""Warn about the msgspec type aliases on a recursion that no Struct breaks, which msgspec cannot decode."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
from jsonschema import Draft202012Validator

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "msgspec_recursive_aliases.json"
DOCUMENTS = (
    ("Model", {"nested": [1, [2]]}),
    ("Model", {"tree": {"children": [1]}}),
    ("Node", {"children": [1, {"children": []}]}),
    ("Node", {"children": ["x"]}),
)


@pytest.mark.parametrize("target_python_version", ["3.10", "3.12"])
def test_msgspec_recursive_aliases(
    output_file: Path, recwarn: pytest.WarningsRecorder, target_python_version: str
) -> None:
    """Warn once per cycle of type aliases that name each other through no Struct, in every form the output writes.

    An alias that only uses such a cycle is named by no warning of its own. msgspec refuses the Struct that holds a
    warned alias and decodes a recursion through a Struct; the payloads are decoded with the aliases of Python 3.10,
    which every Python imports.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--output-model-type",
            "msgspec.Struct",
            "--target-python-version",
            target_python_version,
        ],
    )
    assert_output(
        "".join(
            f"{message}\n"
            for message in dict.fromkeys(str(warning.message) for warning in recwarn if warning.category is UserWarning)
        ),
        name="warnings",
    )
    if target_python_version != "3.10":
        return
    validator = Draft202012Validator(json.loads(INPUT_PATH.read_text()))
    lines = []
    for name, document in DOCUMENTS:
        valid = validator.is_valid(document if name == "Model" else {"tree": document})
        with _generated_model(output_file, "msgspec_recursive_aliases_model", name) as model:
            lines.append(
                f"{name} {json.dumps(document)} ({'valid' if valid else 'invalid'}): {_verdict(model, document)}\n"
            )
    assert_output("".join(lines), name="payloads")


def _verdict(model: Any, document: object) -> str:
    """Return whether the generated model decodes a JSON document, or that msgspec refuses the model's type."""
    try:
        msgspec.json.decode(json.dumps(document), type=model)
    except msgspec.ValidationError:
        return "rejected"
    except TypeError:
        return "type refused by msgspec"
    return "accepted"
