"""Execute size constraints on directly and indirectly recursive containers."""

from __future__ import annotations

import json
from contextlib import suppress
from functools import partial
from typing import TYPE_CHECKING

import msgspec
import pytest
from pydantic import TypeAdapter, ValidationError

from datamodel_code_generator import DataModelType, Formatter, InputFileType
from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, JSON_SCHEMA_DATA_PATH, _generated_model, run_generate_and_assert
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("collapse", [False, True])
@pytest.mark.parametrize(
    "backend", [DataModelType.PydanticV2BaseModel, DataModelType.PydanticV2Dataclass, DataModelType.MsgspecStruct]
)
def test_recursive_container_constraints(output_file: Path, *, collapse: bool, backend: DataModelType) -> None:
    """Keep the size limits on a recursive list while leaving a direct model reference unconstrained."""
    name = backend.name
    run_generate_and_assert(
        input_=JSON_SCHEMA_DATA_PATH / "recursive_container_constraints.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "recursive_container_constraints" / f"{name}.py",
        output_model_type=backend,
        collapse_root_models=collapse,
        field_constraints=True,
        use_annotated=True,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
    )
    payloads = json.loads((DATA_PATH / "payloads/recursive_container_constraints.json").read_text())
    results = []
    with _generated_model(output_file, "recursive_containers", "Node") as model:
        validate = (
            partial(msgspec.convert, type=model)
            if backend == DataModelType.MsgspecStruct
            else TypeAdapter(model).validate_python
        )
        for payload in payloads:
            accepted = False
            with suppress(ValidationError, msgspec.ValidationError):
                validate(payload)
                accepted = True
            results.append(accepted)
    assert_output(
        json.dumps(results, indent=2) + "\n",
        EXPECTED_JSON_SCHEMA_PATH / "recursive_container_constraints/runtime.txt",
    )
