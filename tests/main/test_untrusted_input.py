"""Generate from a schema of an untrusted source under --untrusted-input."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import Error, GenerateConfig, generate, generate_dynamic_models
from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_httpx_get_kwargs, assert_output
from tests.main.conftest import (
    ASYNC_API_DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    PROTOBUF_DATA_PATH,
    XML_SCHEMA_DATA_PATH,
    InputFileTypeLiteral,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import HttpxGetMockFactory

COMMON_ARGS = ("--formatters", "builtin", "--disable-timestamp")
OPTION_SETS = {"released": (), "untrusted": ("--untrusted-input",)}
KEPT: dict[str, tuple[Path, InputFileTypeLiteral]] = {
    "embedded_id_resource": (JSON_SCHEMA_DATA_PATH / "local_id_ref.json", "jsonschema"),
    "refs_through_the_input_file": (JSON_SCHEMA_DATA_PATH / "anchor_at_any_position.json", "jsonschema"),
    "xml_schema_decimal": (XML_SCHEMA_DATA_PATH / "fixed_decimal.xsd", "xmlschema"),
}
"""Inputs whose references stay in the input and whose keywords write no code: the same models with the switch."""
REFUSED: dict[str, tuple[Path, InputFileTypeLiteral]] = {
    "file_ref": (JSON_SCHEMA_DATA_PATH / "external_definitions_root.json", "jsonschema"),
    "url_ref": (JSON_SCHEMA_DATA_PATH / "root_id.json", "jsonschema"),
    "x_python_import": (JSON_SCHEMA_DATA_PATH / "x_python_import_pointer_targets.json", "jsonschema"),
    "x_python_type": (JSON_SCHEMA_DATA_PATH / "external_python_type.json", "jsonschema"),
    "custom_type_path": (JSON_SCHEMA_DATA_PATH / "custom_type_path.json", "jsonschema"),
    "custom_base_path": (JSON_SCHEMA_DATA_PATH / "custom_base_path.json", "jsonschema"),
    "xml_schema_include": (XML_SCHEMA_DATA_PATH / "versioning_include.xsd", "xmlschema"),
    "asyncapi_xml_schema_include": (ASYNC_API_DATA_PATH / "xml-schema-format.yaml", "asyncapi"),
    "protobuf": (PROTOBUF_DATA_PATH / "proto3_optional.proto", "protobuf"),
    "asyncapi_protobuf_import": (ASYNC_API_DATA_PATH / "protobuf-local-import.yaml", "asyncapi"),
}
"""Inputs that read another file or URL, or whose keywords write code: generated as before without the switch."""


@pytest.mark.cli_doc(
    options=["--untrusted-input"],
    option_description="""Treat the input as a schema from an untrusted source.

Use it when the schema comes from a source the project does not control, such as an HTTP request or a tool schema of
an LLM, and the generated module is imported or executed. dcg then reads no file or URL but the input: a `$ref` must
name a location in the input (a JSON pointer, an anchor, an embedded `$id` resource or another file of a directory
input), not another file or a URL, and an XML Schema includes or imports only files of the input. The schema
keywords whose values the generated module imports or evaluates as Python code (`x-python-type`, `x-python-import`,
`customTypePath`, `customBasePath`) are refused, as are Protocol Buffers input, whose imports protoc reads by itself,
and `--validation`. The option implies `--no-allow-remote-refs`. A schema that does none of this generates the same
models.""",
    input_schema="jsonschema/local_id_ref.json",
    cli_args=["--untrusted-input"],
    golden_output="main/jsonschema/local_id_ref.py",
)
@pytest.mark.parametrize("case", KEPT)
@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_untrusted_input(output_file: Path, option_set: str, case: str) -> None:
    """Generate the same models from a schema whose references stay in the input, with or without the switch.

    A reference to an embedded `$id` resource and a reference that resolves through the input file itself stay in the
    input; the `x-python-type` that the XML Schema converter writes for `xs:decimal` is not schema text.
    """
    input_path, input_file_type = KEPT[case]
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type=input_file_type,
        extra_args=[*COMMON_ARGS, *OPTION_SETS[option_set]],
    )


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_untrusted_input_directory(output_dir: Path, option_set: str) -> None:
    """Read the references between the files of a directory input under --untrusted-input: they are the input."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "external_files_in_directory",
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=[*COMMON_ARGS, *OPTION_SETS[option_set]],
    )


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_untrusted_input_xml_schema_directory(tmp_path: Path, option_set: str) -> None:
    """Read the XML Schema import of another file of a directory input under --untrusted-input: it is the input."""
    schemas = XML_SCHEMA_DATA_PATH / "simple_content_inheritance"
    input_dir = tmp_path / "schemas"
    input_dir.mkdir()
    run_main_and_assert(
        input_path=input_dir,
        output_path=tmp_path / "models",
        input_file_type="xmlschema",
        extra_args=[*COMMON_ARGS, *OPTION_SETS[option_set]],
        copy_files=[(schemas / name, input_dir / name) for name in ("imported.xsd", "imported_base.xsd")],
    )


@pytest.mark.parametrize("untrusted_input", [False, True])
def test_untrusted_input_file_list(monkeypatch: pytest.MonkeyPatch, untrusted_input: bool) -> None:
    """Read the reference from one file of a list input to another under untrusted_input: both are the input."""
    monkeypatch.chdir(JSON_SCHEMA_DATA_PATH)
    modules = generate(
        [JSON_SCHEMA_DATA_PATH / "external_definitions_root.json", JSON_SCHEMA_DATA_PATH / "external_definitions.json"],
        input_file_type="jsonschema",
        formatters=["builtin"],
        disable_timestamp=True,
        untrusted_input=untrusted_input,
    )
    assert_output("".join(f"# {'/'.join(path)}\n{body}\n" for path, body in sorted(modules.items())))


@pytest.mark.parametrize("case", REFUSED)
def test_untrusted_input_refuses(
    output_file: Path, capsys: pytest.CaptureFixture[str], mock_httpx_get: HttpxGetMockFactory, case: str
) -> None:
    """Refuse a reference to another file or a URL, and a keyword whose value the module imports or evaluates.

    Without the switch the input generates its models as before; with it, generation stops with an error that names
    the reference or the keyword, and nothing is fetched.
    """
    httpx_get_mock = mock_httpx_get()
    input_path, input_file_type = REFUSED[case]
    run_main_and_assert(
        input_path=input_path, output_path=output_file, input_file_type=input_file_type, extra_args=COMMON_ARGS
    )
    capsys.readouterr()
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type=input_file_type,
        extra_args=[*COMMON_ARGS, "--untrusted-input"],
        expected_exit=Exit.ERROR,
    )
    assert_output(capsys.readouterr().err, name="refused")
    assert_httpx_get_kwargs(httpx_get_mock, called=False)


def test_untrusted_input_refuses_validation(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Refuse --validation under --untrusted-input: its validator resolves the references of the document itself."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[*COMMON_ARGS, "--untrusted-input", "--validation"],
        expected_exit=Exit.ERROR,
    )
    assert_output(capsys.readouterr().err)


def test_untrusted_input_dynamic_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse, for a schema given as a dict, a ``$ref`` to a file beside the working directory under untrusted_input.

    Without it the dynamic API reads the file, as a schema from an HTTP request could make it do.
    """
    monkeypatch.chdir(JSON_SCHEMA_DATA_PATH)
    schema = {"type": "object", "properties": {"name": {"$ref": "external_definitions.json#/elegantName"}}}
    models = generate_dynamic_models(schema, config=GenerateConfig(formatters=[]), cache_size=0)
    assert_output("".join(f"{name}\n" for name in sorted(models)), name="models")
    with pytest.raises(Error) as refused:
        generate_dynamic_models(schema, config=GenerateConfig(formatters=[], untrusted_input=True), cache_size=0)
    assert_output(str(refused.value), name="refused")


def test_untrusted_input_refuses_merged_trait_headers() -> None:
    """Refuse an ``x-python-type`` in the headers of a message trait that --merge-message-trait-headers merges.

    The merged headers are a schema dcg builds from the input, not one a converter writes, so their keywords are schema
    text like any other.
    """
    trait_headers = {"type": "object", "properties": {"trace": {"type": "string", "x-python-type": "os.PathLike[str]"}}}
    document = {
        "asyncapi": "3.0.0",
        "info": {"title": "Merged trait headers", "version": "1.0.0"},
        "channels": {
            "events": {
                "address": "events",
                "messages": {"event": {"payload": {"type": "string"}, "traits": [{"headers": trait_headers}]}},
            }
        },
    }
    options = {"merge_message_trait_headers": True, "formatters": ["builtin"], "disable_timestamp": True}
    assert_output(generate(document, input_file_type="asyncapi", **options), name="models")
    with pytest.raises(Error) as refused:
        generate(document, input_file_type="asyncapi", untrusted_input=True, **options)
    assert_output(str(refused.value), name="refused")
