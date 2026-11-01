"""Union defaults convert to the member that can hold them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType, InputFileType, PythonVersion
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, _generated_model, run_generate_file_and_assert
from tests.main.jsonschema.conftest import assert_file_content

if TYPE_CHECKING:
    from pathlib import Path


def test_msgspec_union_list_default_uses_the_list_member(output_file: Path) -> None:
    """A list default converts through the list member, not the first Struct member."""
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "msgspec_union_list_default.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        assert_func=assert_file_content,
        expected_file="msgspec_union_list_default.py",
        output_model_type=DataModelType.MsgspecStruct,
        target_python_version=PythonVersion.PY_310,
        disable_timestamp=True,
    )

    with _generated_model(output_file, "msgspec_union_list_default_runtime", "Model") as model:
        instance = model()

    assert instance.spec == ["x"]


def test_msgspec_union_alias_list_default_uses_the_alias_member(output_file: Path) -> None:
    """A list default converts through an aliased list member, not a Struct member."""
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "msgspec_union_alias_list_default.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        assert_func=assert_file_content,
        expected_file="msgspec_union_alias_list_default.py",
        output_model_type=DataModelType.MsgspecStruct,
        target_python_version=PythonVersion.PY_310,
        use_type_alias=True,
        disable_timestamp=True,
    )

    with _generated_model(output_file, "msgspec_union_alias_list_default_runtime", "Model") as model:
        instance = model()

    assert [item.id for item in instance.aliased_spec] == [1]
    assert [item.id for item in instance.chained_spec] == [2]
