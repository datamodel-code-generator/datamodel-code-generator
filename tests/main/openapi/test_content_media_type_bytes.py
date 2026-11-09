"""Type an OpenAPI string with a content media type and no content encoding as bytes under the option."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import OPEN_API_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = OPEN_API_DATA_PATH / "content_media_type_bytes.yaml"


@pytest.mark.cli_doc(
    options=["--use-bytes-for-content-media-type"],
    option_description="""Type an OpenAPI string with a `contentMediaType` and no `contentEncoding` as `bytes`.

OpenAPI 3.1 writes raw binary content, such as an uploaded image, as a string with a `contentMediaType` and no
`contentEncoding`, in place of 3.0's `format: binary`, and the generated clients and servers send and receive such a
string as a file. Without this option the models type it `str`, so a generated server refuses every upload that is
not UTF-8. With it the string is `bytes`, as a `format: binary` string is, at a property, an item, a nullable property
and a root, with its length constraints; a string with a `contentEncoding` or a format of its own keeps its type.""",
    input_schema="openapi/content_media_type_bytes.yaml",
    cli_args=["--use-bytes-for-content-media-type"],
    golden_output="main/openapi/content_media_type_bytes/content_media_type_bytes[pydantic_v2-option].py",
    comparison_output="main/openapi/content_media_type_bytes/content_media_type_bytes[pydantic_v2].py",
)
@pytest.mark.parametrize(
    ("output_model_type", "extra_args", "validated"),
    [
        pytest.param("pydantic_v2.BaseModel", [], False, id="pydantic_v2"),
        pytest.param("pydantic_v2.BaseModel", ["--use-bytes-for-content-media-type"], True, id="pydantic_v2-option"),
        pytest.param(
            "pydantic_v2.BaseModel",
            ["--use-bytes-for-content-media-type", "--field-constraints", "--collapse-root-models"],
            True,
            id="pydantic_v2-option-field_constraints",
        ),
        pytest.param(
            "pydantic_v2.dataclass",
            ["--use-bytes-for-content-media-type", "--use-annotated"],
            True,
            id="pydantic_dataclass",
        ),
        pytest.param("msgspec.Struct", ["--use-bytes-for-content-media-type"], False, id="msgspec-option"),
        pytest.param("typing.TypedDict", ["--use-bytes-for-content-media-type"], False, id="typed_dict-option"),
    ],
)
def test_content_media_type_bytes(
    output_file: Path, output_model_type: str, extra_args: list[str], validated: bool
) -> None:
    """Write a string with a content media type and no content encoding as ``bytes`` under the option (#5858).

    Without it ``image``, ``limited``, the items of ``images``, ``nullable`` and the root ``Picture`` are ``str``; with
    it they are ``bytes``, ``limited`` with its ``maxLength``, so the image parses as bytes and nine bytes for
    ``limited`` are rejected, while ``encoded``, ``email`` and ``text`` keep their types.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            *("--output-model-type", output_model_type),
            *extra_args,
            *("--formatters", "builtin", "--disable-timestamp", "--target-python-version", "3.10"),
        ],
        force_exec_validation=True,
    )
    if validated:
        assert_generated_model_json_validation(
            output_file,
            module_name=f"content_media_type_bytes_{output_model_type.replace('.', '_')}",
            model_name="Upload",
            valid_json=json.dumps({"image": "PNG", "images": ["a"], "nullable": None, "picture": "JPG"}),
            invalid_json=json.dumps({"image": "PNG", "limited": "123456789"}),
            expected_error_type="bytes_too_long",
            expected_attribute_path=("image",),
            expected_attribute_value=b"PNG",
        )
