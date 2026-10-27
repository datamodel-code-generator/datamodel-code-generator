"""Today's output and accept/reject results for schema intersection shapes ($ref siblings, allOf members)."""

from __future__ import annotations

import json
import sys
import warnings
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
import yaml
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.validators import validator_for
from pydantic import TypeAdapter, ValidationError

from datamodel_code_generator import DataModelType
from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    EXPERIMENTAL_MISSING_IMPORT_WARNING,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_model,
    run_main_and_assert,
    run_main_with_args,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

PAYLOADS: dict[str, dict[str, Any]] = json.loads(
    (DATA_PATH / "payloads/schema_intersection.json").read_text(encoding="utf-8")
)
DOCUMENTS: dict[str, Path] = {
    path.stem: path
    for path in (
        *sorted((JSON_SCHEMA_DATA_PATH / "schema_intersection").glob("*.json")),
        *sorted((OPEN_API_DATA_PATH / "schema_intersection").glob("*.yaml")),
    )
}
OUTPUT_DOCUMENTS: dict[str, Path] = {
    **DOCUMENTS,
    "pattern_conjunction_edges": JSON_SCHEMA_DATA_PATH / "pattern_conjunctions" / "edges.yaml",
    "ref_sibling_member_item_constraints": JSON_SCHEMA_DATA_PATH / "ref_sibling_member_item_constraints.json",
    "enum_refs_unholdable_str": JSON_SCHEMA_DATA_PATH / "enum_refs_unholdable_str.json",
    "enum_refs_disjoint_str": JSON_SCHEMA_DATA_PATH / "enum_refs_disjoint_str.json",
}
"""The documents whose output is pinned: the shapes, patterns that some engine does not read, and item constraints
beside an allOf member written as a ``$ref`` with siblings, which keep their earlier form (no payloads), and a merged
string enum (``enum_refs_disjoint_str`` pins its output only), whose payloads need a Python with ``enum.StrEnum``
(``test_schema_intersection_str_enum_payloads``)."""
SITE_MODELS = "--use-site-models-for-constrained-refs"
TYPE_OVERRIDES = json.dumps({"Quantity": "decimal.Decimal", "Status": "typing.Any"})
RUNS: tuple[tuple[DataModelType, tuple[str, ...]], ...] = (
    (DataModelType.PydanticV2BaseModel, ()),
    (DataModelType.PydanticV2Dataclass, ()),
    (DataModelType.DataclassesDataclass, ()),
    (DataModelType.TypingTypedDict, ()),
    (DataModelType.MsgspecStruct, ()),
    (DataModelType.PydanticV2BaseModel, ("--field-constraints",)),
    (DataModelType.PydanticV2BaseModel, ("--use-annotated",)),
    (DataModelType.PydanticV2BaseModel, ("--collapse-root-models",)),
    (DataModelType.MsgspecStruct, ("--collapse-root-models",)),
    (DataModelType.PydanticV2BaseModel, (SITE_MODELS,)),
    (DataModelType.PydanticV2Dataclass, (SITE_MODELS,)),
    (DataModelType.DataclassesDataclass, (SITE_MODELS,)),
    (DataModelType.TypingTypedDict, (SITE_MODELS,)),
    (DataModelType.MsgspecStruct, (SITE_MODELS,)),
    (DataModelType.PydanticV2BaseModel, ("--field-constraints", SITE_MODELS)),
    (DataModelType.PydanticV2BaseModel, ("--collapse-root-models", SITE_MODELS)),
)
EXTRA_RUNS: dict[str, tuple[tuple[DataModelType, tuple[str, ...]], ...]] = {
    "allof_4716": (
        (DataModelType.PydanticV2BaseModel, ("--type-overrides", TYPE_OVERRIDES)),
        (DataModelType.PydanticV2BaseModel, ("--type-overrides", TYPE_OVERRIDES, SITE_MODELS)),
    ),
    "enum_ref_consts": (
        (DataModelType.PydanticV2BaseModel, (SITE_MODELS, "--use-single-value-enum-as-const")),
        (DataModelType.MsgspecStruct, (SITE_MODELS, "--use-single-value-enum-as-const")),
        (DataModelType.PydanticV2BaseModel, ("--preset", "standard-py310-20261027")),
        (DataModelType.MsgspecStruct, ("--preset", "standard-py310-20261027")),
    ),
    "enum_refs_unholdable_str": (
        (DataModelType.PydanticV2BaseModel, ("--target-python-version", "3.11")),
        (DataModelType.MsgspecStruct, ("--target-python-version", "3.11")),
    ),
    "enum_refs_disjoint_str": (
        (DataModelType.PydanticV2BaseModel, ("--target-python-version", "3.11")),
        (DataModelType.MsgspecStruct, ("--target-python-version", "3.11")),
    ),
    "allof_ref_item_constraints": (
        (DataModelType.PydanticV2BaseModel, ("--field-constraints", "--collapse-root-models")),
        (DataModelType.PydanticV2Dataclass, ("--field-constraints",)),
    ),
    "nullable_array_items_oas30": (
        (DataModelType.PydanticV2BaseModel, ("--strict-nullable", "--use-annotated", "--field-constraints")),
        (DataModelType.MsgspecStruct, ("--strict-nullable",)),
        (
            DataModelType.PydanticV2BaseModel,
            ("--strict-nullable", "--use-annotated", "--field-constraints", "--omit-redundant-reference-null"),
        ),
        (DataModelType.MsgspecStruct, ("--strict-nullable", "--omit-redundant-reference-null")),
    ),
    "null_multiple_types_oas30": (
        (DataModelType.PydanticV2BaseModel, ("--use-missing-sentinel",)),
        (DataModelType.PydanticV2BaseModel, ("--strict-nullable", "--use-missing-sentinel")),
        (DataModelType.PydanticV2Dataclass, ("--use-missing-sentinel",)),
        (
            DataModelType.PydanticV2BaseModel,
            ("--strict-nullable", "--use-missing-sentinel", "--omit-redundant-reference-null"),
        ),
    ),
    "ref_sibling_nullable_oas30": ((DataModelType.PydanticV2BaseModel, ("--strict-nullable",)),),
    "ref_siblings": ((DataModelType.PydanticV2BaseModel, ("--generate-schema-validators",)),),
    "root_members": ((DataModelType.PydanticV2BaseModel, ("--generate-schema-validators",)),),
    "unsat_root_literals": ((DataModelType.PydanticV2BaseModel, ("--generate-schema-validators",)),),
}
VALIDATING_BACKENDS = {
    DataModelType.PydanticV2BaseModel,
    DataModelType.PydanticV2Dataclass,
    DataModelType.MsgspecStruct,
}


def _runs(stem: str) -> tuple[tuple[DataModelType, tuple[str, ...]], ...]:
    return (*RUNS, *EXTRA_RUNS.get(stem, ()))


def _label(options: tuple[str, ...]) -> str:
    return "-".join(option.removeprefix("--") for option in options if option.startswith("--")) or "default"


def _input_file_type(input_path: Path) -> str:
    return "openapi" if input_path.is_relative_to(OPEN_API_DATA_PATH) else "jsonschema"


OPTION_ARGS: tuple[str, ...] = (
    "--read-const-beside-enum",
    "--filter-literal-values",
    "--intersect-allof-keywords",
    "--read-nullable-beside-type-list",
)
"""The options that make the stored output the intersection of the parts and write the null a root declares; without
them it is the output of 0.86.0."""


def _args(
    input_path: Path, output_path: Path, backend: DataModelType, options: tuple[str, ...], *, with_options: bool = True
) -> list[str]:
    return [
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--input-file-type",
        _input_file_type(input_path),
        "--output-model-type",
        backend.value,
        "--disable-timestamp",
        "--formatters",
        "builtin",
        *(OPTION_ARGS if with_options else ()),
        *options,
    ]


@pytest.mark.parametrize(
    ("stem", "backend", "options"),
    [(stem, backend, options) for stem in OUTPUT_DOCUMENTS for backend, options in _runs(stem)],
    ids=[f"{stem}-{backend.value}-{_label(options)}" for stem in OUTPUT_DOCUMENTS for backend, options in _runs(stem)],
)
def test_schema_intersection_shape_output(
    output_file: Path, stem: str, backend: DataModelType, options: tuple[str, ...]
) -> None:
    """Pin the code each backend and option set writes for every intersection shape with --intersect-allof-keywords."""
    _assert_shape_output(output_file, stem, backend, options, with_options=True)


@pytest.mark.parametrize(
    ("stem", "backend", "options"),
    [(stem, backend, options) for stem in OUTPUT_DOCUMENTS for backend, options in _runs(stem)],
    ids=[f"{stem}-{backend.value}-{_label(options)}" for stem in OUTPUT_DOCUMENTS for backend, options in _runs(stem)],
)
def test_schema_intersection_shape_output_without_option(
    output_file: Path, stem: str, backend: DataModelType, options: tuple[str, ...]
) -> None:
    """Write the output of 0.86.0 for every intersection shape without --intersect-allof-keywords.

    The keyword of the last part wins where the parts disagree: the sibling of a ``$ref``, the overriding property and
    the last member of an ``allOf``.
    """
    _assert_shape_output(output_file, stem, backend, options, with_options=False)


def _assert_shape_output(
    output_file: Path, stem: str, backend: DataModelType, options: tuple[str, ...], *, with_options: bool
) -> None:
    input_path = OUTPUT_DOCUMENTS[stem]
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=_input_file_type(input_path),
            extra_args=[
                "--output-model-type",
                backend.value,
                "--disable-timestamp",
                "--formatters",
                "builtin",
                *(OPTION_ARGS if with_options else ()),
                *options,
            ],
        )
    if messages := dict.fromkeys(str(record.message) for record in recorded if record.category is UserWarning):
        assert_output("".join(f"{message}\n" for message in messages), name="warnings")


def _decoder(model: Any, backend: DataModelType) -> tuple[Callable[[str], Any], type[Exception]]:
    if backend is DataModelType.MsgspecStruct:
        return msgspec.json.Decoder(model).decode, msgspec.ValidationError
    return TypeAdapter(model).validate_json, ValidationError


def _outcome(decode: Callable[[str], Any], rejected: type[Exception], instance: Any) -> str:
    try:
        decode(json.dumps(instance))
    except rejected:
        return "rejected"
    except TypeError:
        return "raised TypeError"
    return "accepted"


@EXPERIMENTAL_MISSING_IMPORT_WARNING
@pytest.mark.parametrize("stem", DOCUMENTS)
def test_schema_intersection_shape_payloads(tmp_path: Path, stem: str) -> None:
    """Prove the payload lists with jsonschema, then record what each model generated with the option accepts."""
    assert_output(_shape_payload_lines(tmp_path, stem, with_options=True))


@EXPERIMENTAL_MISSING_IMPORT_WARNING
@pytest.mark.parametrize("stem", DOCUMENTS)
def test_schema_intersection_shape_payloads_without_option(tmp_path: Path, stem: str) -> None:
    """Record what each model generated without --intersect-allof-keywords accepts, as the models of 0.86.0 do.

    The payloads the intersection rejects, such as an enum value outside the narrower enum or a number below the
    larger minimum, are accepted without the option.
    """
    assert_output(_shape_payload_lines(tmp_path, stem, with_options=False))


@EXPERIMENTAL_MISSING_IMPORT_WARNING
@pytest.mark.skipif(sys.version_info < (3, 11), reason="The merged StrEnum imports only where enum.StrEnum exists")
@pytest.mark.parametrize("with_options", [True, False], ids=["with-option", "without-option"])
def test_schema_intersection_str_enum_payloads(tmp_path: Path, *, with_options: bool) -> None:
    """Record what the models accept where an allOf merges a string enum with an enum holding other values.

    The merged ``StrEnum`` (target Python 3.11) leaves out the members that are no str, so its module imports.
    """
    assert_output(_shape_payload_lines(tmp_path, "enum_refs_unholdable_str", with_options=with_options))


def _shape_payload_lines(tmp_path: Path, stem: str, *, with_options: bool) -> str:
    input_path = OUTPUT_DOCUMENTS[stem]
    case = PAYLOADS[stem]
    if input_path.suffix == ".yaml":
        document = yaml.safe_load(input_path.read_text(encoding="utf-8"))
        validator = validator_for({"$schema": case["dialect"]})(document).evolve(
            schema=document["components"]["schemas"][case["model"]]
        )
    else:
        document = json.loads(input_path.read_text(encoding="utf-8"))
        validator = validator_for(document)(document)
    base = case.get("base", {})
    values = [(name, value) for name, lists in case["fields"].items() for value in (*lists["valid"], *lists["invalid"])]
    for name, lists in case["fields"].items():
        for value in lists["valid"]:
            validator.validate({**base, name: value})
        for value in lists["invalid"]:
            with pytest.raises(JsonSchemaValidationError):
                validator.validate({**base, name: value})
    lines = []
    for index, (backend, options) in enumerate(_runs(stem)):
        if backend not in VALIDATING_BACKENDS:
            continue
        output_path = tmp_path / f"run_{index}.py"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            run_main_with_args(_args(input_path, output_path, backend, options, with_options=with_options))
            try:
                with _generated_model(output_path, f"schema_intersection_{stem}_{index}", case["model"]) as model:
                    decoder = _decoder(model, backend)
            except (AttributeError, ImportError, NameError, TypeError, ValueError):
                outcomes = ["import error"] * len(values)
            else:
                outcomes = [_outcome(*decoder, {**base, name: value}) for name, value in values]
        lines.extend(
            f"{backend.value} {' '.join(options) or 'default'} {name} {json.dumps(value)} {outcome}\n"
            for (name, value), outcome in zip(values, outcomes, strict=True)
        )
    return "".join(lines)
