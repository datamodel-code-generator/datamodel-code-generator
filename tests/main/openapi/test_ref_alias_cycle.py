"""Type aliases of components that only reference each other, which Pydantic cannot build nor msgspec decode.

Generation stops when a class of output that Pydantic builds or msgspec decodes reaches such aliases (see
``test_error_messages.py``); output where no class reaches them keeps its 0.86.0 bytes, and so does a BaseModel, whose
root models Pydantic builds.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import msgspec
import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError

from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_model,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.main.conftest import InputFileTypeLiteral


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--output-model-type", "pydantic_v2.dataclass"],
        ["--output-model-type", "pydantic_v2.BaseModel", "--use-type-alias"],
    ],
    ids=["pydantic-dataclass", "base-model-type-alias"],
)
def test_ref_alias_cycle_no_class_uses(output_file: Path, extra_args: list[str]) -> None:
    """Keep the aliases of a cycle that no class reaches beside a class that uses other aliases and a class."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "ref_alias_cycle_unused.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=extra_args,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"ref_alias_cycle_unused_{extra_args[1].replace('.', '_')}",
        model_name="Holder",
        valid_json='{"first": "a", "second": "b", "label": "c", "size": {"value": 1}, "count": 2}',
        invalid_json='{"size": {"value": "x"}}',
        expected_error_type="int_parsing",
        expected_attribute_path=("size", "value"),
        expected_attribute_value=1,
    )


def test_ref_alias_cycle_no_class_uses_msgspec(output_file: Path) -> None:
    """Keep the msgspec aliases of a cycle that no class reaches; the class that uses other aliases decodes.

    The schema accepts and rejects the payloads as the generated Struct does.
    """
    input_path = OPEN_API_DATA_PATH / "ref_alias_cycle_unused.yaml"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", "msgspec.Struct"],
    )
    valid = {"first": "a", "second": "b", "label": "c", "size": {"value": 1}, "count": 2}
    invalid = {"size": {"value": "x"}}
    validator = Draft202012Validator({**yaml.safe_load(input_path.read_text()), "$ref": "#/components/schemas/Holder"})
    validator.validate(valid)
    with pytest.raises(SchemaValidationError):
        validator.validate(invalid)
    with _generated_model(output_file, "ref_alias_cycle_unused_msgspec", "Holder") as model:
        parsed = msgspec.json.decode(json.dumps(valid), type=model)
        with pytest.raises(msgspec.ValidationError, match=r"Expected `int`, got `str` - at `\$\.size\.value`"):
            msgspec.json.decode(json.dumps(invalid), type=model)
    assert_output(f"{parsed!r}\n", name="parsed")


def test_ref_alias_cycle_base_model(output_file: Path) -> None:
    """Keep the root models of a BaseModel, whose field that uses the cycle takes null."""
    run_main_and_assert(
        input_path=DATA_PATH / "malformed" / "ref_alias_cycle.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="ref_alias_cycle_base_model",
        model_name="Holder",
        valid_json='{"a": null}',
        invalid_json="[]",
        expected_error_type="model_type",
        expected_attribute_path=("a",),
        expected_attribute_value=None,
    )


@pytest.mark.parametrize(
    ("output_model_type", "input_name", "valid_json", "expected_error_type"),
    [
        pytest.param(
            output_model_type,
            input_name,
            valid_json,
            expected_error_type,
            id=f"{output_model_type}-{case}",
            marks=pytest.mark.skipif(
                output_model_type == "typing.TypedDict" and sys.version_info < (3, 12),
                reason="Pydantic validates typing.TypedDict from Python 3.12 on",
            ),
        )
        for output_model_type, expected_error_type in (
            ("dataclasses.dataclass", "dataclass_type"),
            ("typing.TypedDict", "dict_type"),
        )
        for case, input_name, valid_json in (
            ("field", "ref_alias_cycle.yaml", '{"a": {"k": 1}}'),
            (
                "users",
                "ref_alias_cycle_users.yaml",
                '{"c": [1, "x"], "values": {"v": [1]}, "keys": {"k": 2}, "count": 1, "extra": {}}',
            ),
        )
    ],
)
def test_ref_alias_cycle_any(
    output_file: Path, output_model_type: str, input_name: str, valid_json: str, expected_error_type: str
) -> None:
    """Type ``Any`` where a class uses type aliases that only name each other, in output nothing builds (#6313).

    ``A`` is a ``$ref`` to ``B`` and ``B`` a ``$ref`` back to ``A``: the aliases name each other and no type, so a type
    hint naming them never resolved (a dataclass failed to import). The fields, the alias ``C`` of a list of ``A``, map
    values, map keys and additional properties that use them take ``Any``, what a schema without a type gives; the
    aliases keep naming each other, as no class reaches them.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "malformed" / input_name,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model_type],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"{input_name.partition('.')[0]}_{output_model_type.replace('.', '_')}",
        model_name="Holder",
        valid_json=valid_json,
        invalid_json="[]",
        expected_error_type=expected_error_type,
    )


@pytest.mark.parametrize(
    "output_model_type",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_ref_sibling_cycle_description_collapse_root_models(output_file: Path, output_model_type: str) -> None:
    """Write None where --collapse-root-models inlines the root of a cycle that deduplication left without a type.

    The classes that name the cycle take the type their field writes without ``Annotated``: msgspec writes it inside
    ``Annotated`` too, instead of leaving it empty, which is not Python.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "ref_sibling_cycle_description.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model_type, "--collapse-root-models"],
    )


def test_ref_sibling_cycle_description_collapse_root_models_msgspec_decode(output_file: Path) -> None:
    """Decode the msgspec classes that name a cycle beside a description; the schema decides the other fields."""
    input_path = OPEN_API_DATA_PATH / "ref_sibling_cycle_description.yaml"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", "msgspec.Struct", "--collapse-root-models"],
    )
    valid = {"count": 1}
    invalid = {"count": "x"}
    validator = Draft202012Validator({**yaml.safe_load(input_path.read_text()), "$ref": "#/components/schemas/Holder"})
    validator.validate({})
    validator.validate(valid)
    with pytest.raises(SchemaValidationError):
        validator.validate(invalid)
    with _generated_model(output_file, "ref_sibling_cycle_description_msgspec", "Holder") as model:
        parsed = [msgspec.json.decode(json.dumps(payload), type=model) for payload in ({}, valid)]
        with pytest.raises(msgspec.ValidationError, match=r"Expected `int`, got `str` - at `\$\.count`"):
            msgspec.json.decode(json.dumps(invalid), type=model)
    assert_output(f"{parsed!r}\n", name="parsed")


@pytest.mark.parametrize(
    "output_model_type",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_ref_sibling_cycle_maps_collapse_root_models(output_file: Path, output_model_type: str) -> None:
    """Write None where --collapse-root-models inlines the root of a cycle without a type inside a map (#6318).

    The map values, map keys and additional properties that name the cycle take None, as a field whose whole type it
    is writes, instead of nothing: an empty key type, which is not Python, and a map that took any value.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "ref_sibling_cycle_maps.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model_type, "--collapse-root-models"],
        force_exec_validation=True,
    )


def test_ref_sibling_cycle_maps_collapse_root_models_validation(output_file: Path) -> None:
    """Validate the maps that name a cycle without a type under --collapse-root-models: only empty maps match them."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "ref_sibling_cycle_maps.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--collapse-root-models"],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="ref_sibling_cycle_maps_collapse_root_models",
        model_name="Holder",
        valid_json='{"scores": {}, "tags": {}, "count": 1}',
        invalid_json='{"tags": {"x": 1}}',
        expected_error_type="none_required",
        expected_attribute_path=("scores",),
        expected_attribute_value={},
    )


def test_ref_sibling_cycle_description_error(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Name each component of the chain a model took in where Pydantic builds a class that uses it (#5261).

    ``B`` took the users of ``A``, a ``$ref`` to ``B`` beside ``minProperties``, so its root names ``B``: the chain
    runs through ``A``, which no model stands for.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "ref_sibling_cycle_description.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Holder uses a $ref chain that loops back without reaching a schema, which Pydantic "
        "cannot build: B (ref_sibling_cycle_description.yaml#/components/schemas/B) -> "
        "A (ref_sibling_cycle_description.yaml#/components/schemas/A) -> "
        "B (ref_sibling_cycle_description.yaml#/components/schemas/B)",
        output_should_not_exist=True,
    )


@pytest.mark.parametrize(
    ("output_model_type", "input_name", "valid_json"),
    [
        pytest.param(
            output_model_type,
            input_name,
            valid_json,
            id=f"{output_model_type}-{case}",
            marks=pytest.mark.skipif(
                output_model_type == "typing.TypedDict" and case == "field" and sys.version_info < (3, 12),
                reason="Pydantic validates typing.TypedDict from Python 3.12 on",
            ),
        )
        for output_model_type in ("dataclasses.dataclass", "typing.TypedDict")
        for case, input_name, valid_json in (
            ("field", "ref_sibling_cycle_description.yaml", '{"a": {"k": 1}, "count": 1}'),
            ("maps", "ref_sibling_cycle_maps.yaml", '{"scores": {"x": {"k": 1}}, "tags": {"y": 2}, "count": 1}'),
        )
    ],
)
def test_ref_sibling_cycle_any(output_file: Path, input_name: str, valid_json: str, output_model_type: str) -> None:
    """Type ``Any`` where a class names a cycle that deduplication left without a type, where nothing builds (#5261).

    ``A`` is a ``$ref`` to ``B`` beside ``minProperties``, and ``B`` a ``$ref`` back to ``A``: once the users of ``A``
    follow ``B``, the root of ``B`` names ``B``, and deduplication drops it. The fields, map values, map keys and
    additional properties that used it named a class no module writes, so no type hint of ``Holder`` resolved; they
    take ``Any``, what a schema without a type gives.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / input_name,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model_type],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"{input_name.partition('.')[0]}_{output_model_type.replace('.', '_')}",
        model_name="Holder",
        valid_json=valid_json,
        invalid_json='{"count": "x"}',
        expected_error_type="int_parsing",
    )


@pytest.mark.parametrize(
    ("input_name", "input_file_type", "output_model_type", "extra_args"),
    [
        pytest.param(
            "ref_self_reference_unused.yaml", "openapi", "dataclasses.dataclass", [], id="unused-alias-dataclass"
        ),
        pytest.param("ref_self_reference_unused.yaml", "openapi", "typing.TypedDict", [], id="unused-alias-typeddict"),
        pytest.param(
            "ref_self_reference_functional.yaml", "openapi", "typing.TypedDict", [], id="functional-typeddict"
        ),
        pytest.param(
            "ref_self_reference.yaml",
            "openapi",
            "dataclasses.dataclass",
            ["--disable-future-imports"],
            id="eager-annotations-dataclass",
        ),
        pytest.param(
            "ref_self_reference.yaml",
            "openapi",
            "typing.TypedDict",
            ["--disable-future-imports"],
            id="eager-annotations-typeddict",
        ),
        pytest.param(
            "ref_self_reference_through_alias.json",
            "jsonschema",
            "dataclasses.dataclass",
            [],
            id="through-alias-dataclass",
        ),
        pytest.param(
            "ref_self_reference_through_alias.json", "jsonschema", "typing.TypedDict", [], id="through-alias-typeddict"
        ),
    ],
)
def test_ref_self_reference_any(
    output_file: Path,
    input_name: str,
    input_file_type: InputFileTypeLiteral,
    output_model_type: str,
    extra_args: list[str],
) -> None:
    """Type ``Any`` where the module names a component that is only a ``$ref`` to itself, in output nothing builds.

    No module writes such a component. A type alias or a class that the module evaluates as it runs named it, which
    stopped generation with an error that Python cannot evaluate it (#5261); the alias and the fields take ``Any``, and
    the module imports.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "malformed" / input_name,
        output_path=output_file,
        input_file_type=input_file_type,
        extra_args=["--output-model-type", output_model_type, *extra_args, "--formatters", "builtin"],
        force_exec_validation=True,
    )


@pytest.mark.parametrize("output_model_type", ["dataclasses.dataclass", "typing.TypedDict"])
def test_ref_self_reference_unnamed_imports(output_file: Path, output_model_type: str) -> None:
    """Keep the output where no type names a component that is only a $ref to itself: the module imports."""
    run_main_and_assert(
        input_path=DATA_PATH / "malformed" / "ref_self_reference_unnamed.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model_type],
    )
    with _generated_model(output_file, "ref_self_reference_unnamed", "Holder") as model:
        assert_output(f"{model(labels=['x'], tree=[[]])!r}\n", name="instance")


@pytest.mark.parametrize("output_model_type", ["dataclasses.dataclass", "typing.TypedDict"])
def test_ref_self_reference_unnamed_type_statement(output_file: Path, output_model_type: str) -> None:
    """Keep the ``type`` statements beside a component that is only a $ref to itself, which no type names."""
    run_main_and_assert(
        input_path=DATA_PATH / "malformed" / "ref_self_reference_unnamed.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model_type, "--target-python-version", "3.12"],
    )
