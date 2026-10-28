"""Quote a ``type`` statement that a model names before the module defines it, where the runtime evaluates the name.

The value of ``type Chain = ...`` is deferred, but the name ``Chain`` is bound only when the statement runs. A class
body evaluates its annotations as it is defined unless they are deferred, by ``from __future__ import annotations`` or
by Python 3.14, so ``chain: Chain | None`` raised ``NameError`` when the class came before the statement. The typed
extras of a Pydantic model are evaluated as the class is defined even under ``from __future__ import annotations``,
and so is the value of a ``TypeAliasType`` call, which no deferred annotation reaches.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
import yaml
from jsonschema import Draft7Validator, Draft202012Validator
from pydantic import TypeAdapter, ValidationError

from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_model,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

INPUT = OPEN_API_DATA_PATH / "type_statement_forward_references.yaml"
PAYLOADS = json.loads((DATA_PATH / "payloads" / "type_statement_forward_references" / "payloads.json").read_text())
OUTPUT_MODEL_TYPES = (
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    "typing.TypedDict",
    "msgspec.Struct",
)
OPTION_SETS = (
    pytest.param(
        ("--target-python-version", "3.12", "--disable-future-imports", "--use-type-alias-type"),
        id="no_future_imports",
        marks=pytest.mark.skipif(sys.version_info < (3, 12), reason="The output has type statements"),
    ),
    pytest.param(
        ("--target-python-version", "3.12", "--use-type-alias-type"),
        id="future_imports",
        marks=pytest.mark.skipif(sys.version_info < (3, 12), reason="The output has type statements"),
    ),
    pytest.param(
        ("--target-python-version", "3.14", "--disable-future-imports", "--use-type-alias-type"),
        id="native_deferred",
        marks=pytest.mark.skipif(sys.version_info < (3, 14), reason="The output defers its annotations natively"),
    ),
)


def _model_verdict(model: Any, output_model_type: str, payload: object) -> str:
    """Return whether the generated model accepts a payload, with the first error type where Pydantic rejects it."""
    try:
        if output_model_type == "msgspec.Struct":
            msgspec.convert(payload, model)
        else:
            TypeAdapter(model).validate_python(payload)
    except ValidationError as error:
        return f"rejects ({error.errors()[0]['type']})"
    except msgspec.ValidationError:
        return "rejects"
    return "accepts"


@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
@pytest.mark.parametrize("options", OPTION_SETS)
def test_type_statement_forward_references(output_file: Path, output_model_type: str, options: tuple[str, ...]) -> None:
    """Import the module and validate payloads through models that name the type statements the module defines later.

    ``Holder`` comes before ``type Chain``, so its fields name it as a string while the classes are evaluated as they
    are defined; ``type Early`` comes before ``Holder`` and stays a name. The typed extras of ``Bag`` name the later
    ``type Later`` as a string under ``from __future__ import annotations`` too, and as a name on Python 3.14, as does
    msgspec, whose forward class references are strings without future imports.
    """
    run_main_and_assert(
        input_path=INPUT,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", "--output-model-type", output_model_type, *options],
    )
    components = yaml.safe_load(INPUT.read_text())["components"]
    names = ("Holder", "Bag") if output_model_type == "pydantic_v2.BaseModel" else ("Holder",)
    lines = []
    for name in names:
        validator = Draft202012Validator({"$ref": f"#/components/schemas/{name}", "components": components})
        with _generated_model(output_file, f"type_statement_forward_references_{name}", name) as model:
            lines.extend(
                f"{name} {json.dumps(payload)}: schema {'accepts' if validator.is_valid(payload) else 'rejects'}, "
                f"model {_model_verdict(model, output_model_type, payload)}"
                for payload in (PAYLOADS[name]["valid"], *PAYLOADS[name]["invalid"])
            )
    assert_output("".join(f"{line}\n" for line in lines), name="payloads")


@pytest.mark.parametrize("options", OPTION_SETS)
def test_type_statement_forward_references_aliases(output_file: Path, options: tuple[str, ...]) -> None:
    """Quote the later ``type`` statement that a ``TypeAliasType`` call names, which no deferred annotation defers.

    The constrained array of ``ArrayTypeUnionSelfRef`` is ``TypeAliasType("...", Annotated[list[...], Field(...)])``,
    whose value is evaluated when the module runs, so it names ``type ArrayTypeUnionSelfRef`` as a string, also under
    ``from __future__ import annotations`` and on Python 3.14.
    """
    input_path = JSON_SCHEMA_DATA_PATH / "array_type_union_self_ref.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", "pydantic_v2.BaseModel", *options],
    )
    name = "ArrayTypeUnionSelfRef"
    validator = Draft7Validator(json.loads(input_path.read_text()))
    with _generated_model(output_file, "type_statement_forward_references_alias", name) as model:
        lines = [
            f"{json.dumps(payload)}: schema {'accepts' if validator.is_valid(payload) else 'rejects'}, "
            f"model {_model_verdict(model, 'pydantic_v2.BaseModel', payload)}"
            for payload in (PAYLOADS[name]["valid"], *PAYLOADS[name]["invalid"])
        ]
    assert_output("".join(f"{line}\n" for line in lines), name="payloads")
