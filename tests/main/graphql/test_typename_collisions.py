"""Preserve user and synthetic GraphQL typename fields independently."""

from __future__ import annotations

import dataclasses
import json
import sys
import warnings
from contextlib import nullcontext
from typing import TYPE_CHECKING, Literal, get_args, get_origin, get_type_hints

import msgspec
import pytest
from graphql import build_schema, graphql_sync, validate_schema
from pydantic import TypeAdapter, ValidationError

from datamodel_code_generator import DataModelType, InputFileType, PythonVersion
from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    EXPECTED_GRAPHQL_PATH,
    GRAPHQL_DATA_PATH,
    _generated_model,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.graphql.conftest import assert_file_content

if TYPE_CHECKING:
    from pathlib import Path


def _has_literal(annotation: object) -> bool:
    """Find Literal through optional and requiredness annotation wrappers."""
    if get_origin(annotation) is Literal:
        return True
    return any(_has_literal(argument) for argument in get_args(annotation))


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("output_model_type", list(DataModelType))
@pytest.mark.parametrize("profile", ["collision", "normal", "aliases", "snake", "serialization", "scoped"])
@pytest.mark.parametrize("no_typename", [False, True])
def test_graphql_typename_collisions(
    entrypoint: str, output_model_type: DataModelType, profile: str, no_typename: bool, output_file: Path
) -> None:
    """Retain local typename attributes and inherited aliases across backends."""
    settings = json.loads((DATA_PATH / "payloads/typename_collision_settings.json").read_text())[profile]
    payload = json.loads((DATA_PATH / "payloads" / f"typename_collision_{profile}.json").read_text())
    input_path = GRAPHQL_DATA_PATH / settings.pop("schema")
    expected_name = f"typename_{profile}_{output_model_type.value.replace('.', '_')}_{no_typename}"
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        if entrypoint == "cli":
            run_main_and_assert(
                input_path=input_path,
                output_path=output_file,
                input_file_type="graphql",
                assert_func=assert_file_content,
                expected_file=f"{expected_name}.py",
                skip_code_validation=output_model_type.value in payload.get("import_errors", {}),
                extra_args=[
                    "--output-model-type",
                    output_model_type.value,
                    "--target-python-version",
                    "3.10",
                    *(["--graphql-no-typename"] if no_typename else []),
                    *[
                        argument
                        for option, value in settings.items()
                        for argument in (
                            [f"--{option.replace('_', '-')}", str(GRAPHQL_DATA_PATH / value)]
                            if isinstance(value, str)
                            else [f"--{option.replace('_', '-')}"]
                        )
                    ],
                ],
            )
        else:
            run_generate_file_and_assert(
                input_path=input_path,
                output_path=output_file,
                input_file_type=InputFileType.GraphQL,
                assert_func=assert_file_content,
                expected_file=f"{expected_name}.py",
                output_model_type=output_model_type,
                target_python_version=PythonVersion.PY_310,
                graphql_no_typename=no_typename,
                **{
                    option: json.loads((GRAPHQL_DATA_PATH / value).read_text()) if isinstance(value, str) else value
                    for option, value in settings.items()
                },
            )
    schema = build_schema(input_path.read_text())
    result = graphql_sync(schema, payload["query"], root_value=payload["root"], variable_values=payload["variables"])
    actual = {
        "schema_errors": [str(error) for error in validate_schema(schema)],
        "graphql_errors": [str(error) for error in result.errors or ()],
        "graphql_data": result.data,
        "models": {},
    }
    if messages := [*dict.fromkeys(str(record.message) for record in recorded if record.category is UserWarning)]:
        actual["warnings"] = messages
    inherited_type_roles = []
    with (
        (
            pytest.raises(ValueError, match=payload["import_errors"][output_model_type.value])
            if output_model_type.value in payload.get("import_errors", {})
            else nullcontext()
        ) as error_info,
        _generated_model(output_file, "graphql_typename_runtime", "Item") as item,
    ):
        for name in payload["models"]:
            model = getattr(sys.modules[item.__module__], name)
            values = (
                result.data[payload["responses"][name]] if name in payload["responses"] else payload["models"][name]
            )
            match output_model_type:
                case DataModelType.PydanticV2BaseModel | DataModelType.PydanticV2Dataclass:
                    adapter = TypeAdapter(model)
                    instance = adapter.validate_python(values)
                    if not no_typename:
                        with pytest.raises(ValidationError):
                            adapter.validate_python(dict(values, __typename="wrong"))
                    fields = (
                        list(model.model_fields)
                        if output_model_type == DataModelType.PydanticV2BaseModel
                        else [field.name for field in dataclasses.fields(model)]
                    )
                    dumped = adapter.dump_python(instance, by_alias=True)
                case DataModelType.MsgspecStruct:
                    instance = msgspec.convert(values, type=model)
                    if not no_typename:
                        with pytest.raises(msgspec.ValidationError):
                            msgspec.convert(dict(values, __typename="wrong"), type=model)
                    fields = list(model.__struct_fields__)
                    dumped = msgspec.to_builtins(instance)
                case DataModelType.DataclassesDataclass:
                    instance = model(**payload["python"][name])
                    fields = [field.name for field in dataclasses.fields(model)]
                    dumped = dataclasses.asdict(instance)
                case _:
                    fields = list(get_type_hints(model))
                    values = payload["python"][name].copy()
                    if not no_typename:
                        values.update({field: name for field in fields if field not in values})
                    dumped = model(**values)
            annotations = get_type_hints(model, include_extras=True)
            # Synthetic fields can move when a child adds a colliding user field.
            inherited_type_roles.extend(
                (
                    _has_literal(annotation),
                    (
                        any(_has_literal(child_annotation) for child_annotation in annotations.values())
                        if _has_literal(annotation)
                        else _has_literal(annotations[field])
                    ),
                )
                for interface in getattr(schema.get_type(name), "interfaces", ())
                for field, annotation in get_type_hints(
                    getattr(sys.modules[item.__module__], interface.name), include_extras=True
                ).items()
            )
            actual["models"][name] = {
                "fields": fields,
                "values": dumped,
                "attributes": (
                    dumped
                    if output_model_type == DataModelType.TypingTypedDict
                    else {field: getattr(instance, field) for field in fields}
                ),
            }
    if error_info is not None:
        actual["import_error"] = str(error_info.value)
    assert_output(
        f"{all(base == child for base, child in inherited_type_roles)}\n",
        DATA_PATH / "payloads/typename_inherited_type_compatible.txt",
    )
    assert_output(f"{json.dumps(actual, indent=2)}\n", EXPECTED_GRAPHQL_PATH / f"{expected_name}.txt")
