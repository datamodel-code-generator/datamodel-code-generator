"""Today's output and accept/reject results for schema intersection shapes ($ref siblings, allOf members)."""

from __future__ import annotations

import json
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
}
"""The documents whose output is pinned: the shapes, patterns that some engine does not read, and item constraints
beside an allOf member written as a ``$ref`` with siblings, which keep their earlier form (no payloads)."""
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


def _args(input_path: Path, output_path: Path, backend: DataModelType, options: tuple[str, ...]) -> list[str]:
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
        "--read-const-beside-enum",
        "--filter-literal-values",
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
    """Pin the code each backend and option set writes for every intersection shape today."""
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
                "--read-const-beside-enum",
                "--filter-literal-values",
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
    """Prove the payload lists with jsonschema, then record what each generated model accepts today."""
    input_path = DOCUMENTS[stem]
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
            run_main_with_args(_args(input_path, output_path, backend, options))
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
    assert_output("".join(lines))
