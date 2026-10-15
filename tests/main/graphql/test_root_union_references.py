"""GraphQL unions retain referenced operation roots across output backends."""

from __future__ import annotations

import json
import sys
import warnings
from typing import TYPE_CHECKING, get_type_hints

import pytest
from graphql import build_schema, graphql_sync, validate_schema

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


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("output_model_type", list(DataModelType))
@pytest.mark.parametrize("case", ["query", "mutation", "subscription", "custom", "ordinary", "field"])
def test_graphql_root_union_references(
    case: str, entrypoint: str, output_model_type: DataModelType, output_file: Path
) -> None:
    """Generate only union-required roots and construct recursive union members."""
    input_path = GRAPHQL_DATA_PATH / f"root_union_{case}.graphql"
    expected_name = f"root_union_{case}_{output_model_type.value.replace('.', '_')}"
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        if entrypoint == "cli":
            run_main_and_assert(
                input_path=input_path,
                output_path=output_file,
                input_file_type="graphql",
                assert_func=assert_file_content,
                expected_file=f"{expected_name}.py",
                extra_args=["--output-model-type", output_model_type.value, "--target-python-version", "3.10"],
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
            )
    payload = json.loads((DATA_PATH / "payloads" / f"root_union_{case}.json").read_text())
    schema = build_schema(input_path.read_text())
    actual = {"schema_errors": [str(error) for error in validate_schema(schema)], "members": []}
    if messages := [*dict.fromkeys(str(record.message) for record in recorded if record.category is UserWarning)]:
        actual["warnings"] = messages
    with _generated_model(output_file, "graphql_root_union_runtime", "Other") as other:
        namespace = vars(sys.modules[other.__module__])
        actual["emitted_roots"] = [name for name in payload["roots"] if name in namespace]
        for sample in payload["samples"]:
            result = graphql_sync(schema, payload["query"], root_value={"choice": sample})
            member = namespace[sample["__typename"]]
            data = result.data["choice"]
            arguments = {"value": data["value"]}
            if "self" in data:
                arguments["self"] = member(**data["self"])
            instance = member(**arguments)
            nested = instance.get("self") if isinstance(instance, dict) else getattr(instance, "self", None)
            actual["members"].append({
                "graphql_errors": [str(error) for error in result.errors or ()],
                "graphql_data": result.data,
                "name": member.__name__,
                "fields": list(get_type_hints(member)),
                "value": instance["value"] if isinstance(instance, dict) else instance.value,
                "nested_value": nested["value"] if isinstance(nested, dict) else getattr(nested, "value", None),
            })
    assert_output(f"{json.dumps(actual, indent=2)}\n", EXPECTED_GRAPHQL_PATH / f"{expected_name}.txt")
