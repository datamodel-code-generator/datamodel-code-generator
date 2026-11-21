"""Warn about the enum and const values a Literal of the output model cannot hold, which their types stand in for."""

from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import sys
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator.__main__ import main
from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

OUTPUT_MODEL_TYPES = ("pydantic_v2.BaseModel", "msgspec.Struct", "dataclasses.dataclass", "typing.TypedDict")
OPTION_SETS = {
    "default": (),
    "literal_all": ("--enum-field-as-literal", "all"),
    "literal_one_as_const": ("--enum-field-as-literal", "one", "--use-single-value-enum-as-const"),
}


@pytest.mark.parametrize("option_set", OPTION_SETS)
@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_literal_widened_values(
    output_file: Path, recwarn: pytest.WarningsRecorder, output_model_type: str, option_set: str
) -> None:
    """Name each field written with the types of enum or const values in place of the values, and no other field.

    A Literal holds str and int values, and bool values where the output writes bool literals (not msgspec); a float,
    a list or an object value is written as its type, which admits any other value of that type, and so is a bool
    beside msgspec, unless both bools are values. A one-value enum read as a const is written as the const is. A float
    const that Pydantic or msgspec fixes by its bounds under --use-bounds-for-float-const is no such value
    (``test_float_const_bounds``).
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "literal_widened_values.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", output_model_type, *OPTION_SETS[option_set]],
    )
    assert_output(
        "".join(
            f"{message}\n"
            for message in dict.fromkeys(
                str(warning.message)
                for warning in recwarn
                if warning.category is UserWarning and "a Literal of the output model" in str(warning.message)
            )
        ),
        name="warnings",
    )


FLOAT_CONST_OPTION_SETS = {
    "off": (),
    "default": ("--use-bounds-for-float-const",),
    "field_constraints": ("--use-bounds-for-float-const", "--field-constraints"),
    "annotated": ("--use-bounds-for-float-const", "--use-annotated"),
}


@pytest.mark.cli_doc(
    options=["--use-bounds-for-float-const"],
    option_description="""Type a finite float `const` as a float whose minimum and maximum both equal it.

No `Literal` holds a float, so without this option a float `const`, such as `{"type": "number", "const": 1.5}`, is typed
`float`, which accepts any other number, with a warning. With it the const takes an equal `minimum` and `maximum`,
written as the output writes number bounds: `confloat(ge=1.5, le=1.5)` for Pydantic, `Field(ge=1.5, le=1.5)` under
`--field-constraints` and inside `Annotated` under `--use-annotated`, and `Meta(ge=1.5, le=1.5)` for msgspec, so other
numbers are rejected and the members of a `oneOf` that differ by such consts accept only their own. Of a `minimum` or
`maximum` beside the const the tighter bound is kept, and the other number keywords stay, so a const the schema rejects
stays rejected. Output that checks no bounds, such as dataclasses and TypedDict, keeps `float` with the warning, and so
do the items, map values and union members where the bounds are written on the field (`--field-constraints`, msgspec), a
field of a Pydantic dataclass with `--field-constraints` and without `--use-annotated`, where `Field()` could move it in
the constructor, and a const beside an `enum`.""",
    input_schema="jsonschema/float_const_bounds.json",
    cli_args=["--use-bounds-for-float-const"],
    golden_output="main/jsonschema/literal_widened_values/float_const_bounds[pydantic_v2.BaseModel-default].py",
    comparison_output="main/jsonschema/literal_widened_values/float_const_bounds[pydantic_v2.BaseModel-off].py",
)
@pytest.mark.parametrize("option_set", FLOAT_CONST_OPTION_SETS)
@pytest.mark.parametrize("output_model_type", [*OUTPUT_MODEL_TYPES, "pydantic_v2.dataclass"])
def test_float_const_bounds(
    output_file: Path, recwarn: pytest.WarningsRecorder, output_model_type: str, option_set: str
) -> None:
    """Fix a float const by an equal minimum and maximum under the option where the output writes them (#5090).

    Without the option every float const is ``float`` with the warning. With it Pydantic writes the bounds on the type
    without --field-constraints, also on items, values and union members, and on the field with it, as msgspec does;
    they keep the tighter of the const and a minimum or maximum beside it, and a multipleOf stays. A field's bounds do
    not reach items, values or union members, nor does a dataclass or TypedDict write them, nor a field of a
    Pydantic dataclass with --field-constraints and without --use-annotated, so those keep the warning. Members of a
    oneOf that differ by their float consts accept only their own.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "float_const_bounds.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--output-model-type",
            output_model_type,
            *FLOAT_CONST_OPTION_SETS[option_set],
        ],
    )
    assert_output(
        "".join(
            f"{message}\n"
            for message in dict.fromkeys(
                str(warning.message)
                for warning in recwarn
                if warning.category is UserWarning and "a Literal of the output model" in str(warning.message)
            )
        ),
        name="warnings",
    )
    if output_model_type == "pydantic_v2.BaseModel" and option_set != "off":
        assert_generated_model_json_validation(
            output_file,
            module_name="float_const_bounds",
            model_name="Model",
            valid_json='{"exact": 1.5, "typed": 2.5, "bounded": 3.5, "ratio": 0.5, "variant": {"k": 2.5}}',
            invalid_json='{"exact": 99.0, "variant": {"k": 2.5}}',
            expected_error_type="less_than_equal",
            expected_attribute_path=("variant", "__class__", "__name__"),
            expected_attribute_value="B",
        )


@pytest.mark.parametrize("option_set", [(), ("--field-constraints",)])
def test_float_const_bounds_keep_the_float_type_of_decimal_multiple_of(
    output_file: Path, recwarn: pytest.WarningsRecorder, option_set: tuple[str, ...]
) -> None:
    """Keep a float const with a multipleOf a float under --use-decimal-for-multiple-of, as without the bounds (#5090).

    The bounds with that multipleOf would make the type a decimal and the valid value 0.3 a ``Decimal``; the option
    never changes the type of a field or a parsed value, so the const stays ``float`` with the warning.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "float_const_decimal_multiple_of.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--use-decimal-for-multiple-of",
            "--use-bounds-for-float-const",
            *option_set,
        ],
    )
    assert_output(
        "".join(
            f"{message}\n"
            for message in dict.fromkeys(
                str(warning.message)
                for warning in recwarn
                if warning.category is UserWarning and "a Literal of the output model" in str(warning.message)
            )
        ),
        name="warnings",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="float_const_decimal_multiple_of",
        model_name="Model",
        valid_json='{"c": 0.3}',
        invalid_json='{"c": "x"}',
        expected_error_type="float_parsing",
        expected_repr="Model(c=0.3)",
    )


@pytest.mark.parametrize(
    ("input_name", "extra_args"),
    [
        ("float_const_bounds_allof_required", ()),
        ("float_const_bounds_allof_promoted_beside_default", ()),
        ("float_const_bounds_inherited_required", ()),
        ("float_const_bounds_rw_required", ("--read-only-write-only-model-type", "request-response")),
    ],
)
def test_float_const_bounds_keep_the_constructor_of_pydantic_dataclass_fields(
    tmp_path: Path,
    output_file: Path,
    recwarn: pytest.WarningsRecorder,
    input_name: str,
    extra_args: tuple[str, ...],
) -> None:
    """Write no bounds where a ``Field()`` call could move a Pydantic dataclass field in the constructor (#5090).

    ``--field-constraints`` without ``--use-annotated`` writes the bounds as ``Field(..., ge=c, le=c)``, a constructor
    default for a field that turns out required (through an allOf, an inherited required or a request-response
    variant), which are final only after the field is built. The const stays ``float`` with the warning, so every
    constructor takes its arguments in the order it takes them without the option.
    """
    args = [
        "--input",
        str(JSON_SCHEMA_DATA_PATH / f"{input_name}.json"),
        "--input-file-type",
        "jsonschema",
        "--output-model-type",
        "pydantic_v2.dataclass",
        "--formatters",
        "builtin",
        "--field-constraints",
        *extra_args,
    ]
    off_file = tmp_path / "off.py"
    main([*args, "--output", str(off_file)])
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / f"{input_name}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*args[4:], "--use-bounds-for-float-const"],
    )
    assert_output(
        "".join(
            f"{message}\n"
            for message in dict.fromkeys(
                str(warning.message)
                for warning in recwarn
                if warning.category is UserWarning and "a Literal of the output model" in str(warning.message)
            )
        ),
        name="warnings",
    )
    modules = {}
    for label, path in (("off", off_file), ("on", output_file)):
        spec = importlib.util.spec_from_file_location(f"{input_name}_{label}", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        modules[label] = module

    def signatures(module: ModuleType) -> str:
        return "".join(
            f"{name}({', '.join(inspect.signature(cls).parameters)})\n"
            for name, cls in vars(module).items()
            if dataclasses.is_dataclass(cls) and cls.__module__ == module.__name__
        )

    assert_output(
        f"without the option:\n{signatures(modules['off'])}with the option:\n{signatures(modules['on'])}",
        name="signatures",
    )
