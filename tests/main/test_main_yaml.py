"""Tests for YAML input file code generation."""

from __future__ import annotations

import sys
import warnings
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import yaml

from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.util import get_safe_loader, get_yaml_backend, get_yaml_parse_errors
from tests.conftest import assert_output, create_assert_file_content
from tests.main.conftest import (
    EXPECTED_MAIN_PATH,
    OPEN_API_DATA_PATH,
    YAML_DATA_PATH,
    assert_generated_model_json_validation,
    run_main_and_assert,
    run_main_with_args,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

assert_file_content = create_assert_file_content(EXPECTED_MAIN_PATH)


class InvalidYamlError(ValueError):
    """The error ryaml raises for a document it cannot read."""


def _read_nothing(_text: str) -> object:
    """Refuse a document as ryaml refuses one holding an integer outside 64 bits."""
    msg = "ryaml cannot read this document"
    raise InvalidYamlError(msg)


@pytest.fixture
def yaml_backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Read YAML with PyYAML alone, or with a ryaml that reads no document (the default)."""
    ryaml = SimpleNamespace(InvalidYamlError=InvalidYamlError, loads=_read_nothing)
    monkeypatch.setitem(sys.modules, "ryaml", ryaml if getattr(request, "param", "ryaml") == "ryaml" else None)
    get_yaml_backend.cache_clear()
    get_yaml_parse_errors.cache_clear()
    yield
    get_yaml_backend.cache_clear()
    get_yaml_parse_errors.cache_clear()


@pytest.fixture
def libyaml(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Read YAML with PyYAML on libyaml (the default) or, as an install without libyaml does, on its own scanner."""
    if getattr(request, "param", "libyaml") == "pure":
        monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    get_safe_loader.cache_clear()
    yield
    get_safe_loader.cache_clear()


@pytest.mark.benchmark
@pytest.mark.cli_doc(
    options=["--input-file-type"],
    option_description="""Generate models from raw YAML sample data.

The `--input-file-type yaml` option treats the input file as **raw YAML data**
and automatically infers a JSON Schema from it.

**Note:** This is NOT for JSON Schema files written in YAML format.
For schema definition files (JSON Schema or OpenAPI), use `--input-file-type jsonschema`
or `--input-file-type openapi` instead, regardless of whether the file is in JSON or YAML format.""",
    input_schema="yaml/pet.yaml",
    cli_args=["--input-file-type", "yaml"],
    golden_output="yaml.py",
)
def test_main_yaml(output_file: Path) -> None:
    """Generate models from raw YAML sample data.

    The `--input-file-type yaml` option treats the input file as **raw YAML data**
    and automatically infers a JSON Schema from it.

    **Note:** This is NOT for JSON Schema files written in YAML format.
    For schema definition files (JSON Schema or OpenAPI), use `--input-file-type jsonschema`
    or `--input-file-type openapi` instead, regardless of whether the file is in JSON or YAML format.
    """
    run_main_and_assert(
        input_path=YAML_DATA_PATH / "pet.yaml",
        output_path=output_file,
        input_file_type="yaml",
        assert_func=assert_file_content,
    )


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("a: 1\nb:\n  - x\n", id="block"),
        pytest.param("{a: 1, b: [x]}\n", id="flow"),
    ],
)
def test_main_yaml_sample_data_auto_detection(
    output_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], document: str
) -> None:
    """Infer YAML sample data that is no JSON document as yaml, as `--input-file-type yaml` reads it (#6310)."""
    input_path = tmp_path / "data.yaml"
    input_path.write_text(document)
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        capsys=capsys,
        expected_stderr_contains="The input file type was determined to be: yaml",
    )


def test_main_yaml_root_list(output_file: Path) -> None:
    """Test YAML file with list as root element generates a list root model."""
    run_main_and_assert(
        input_path=YAML_DATA_PATH / "root_list.yaml",
        output_path=output_file,
        input_file_type="yaml",
        assert_func=assert_file_content,
        expected_file="yaml/root_list.py",
    )


def test_main_yaml_unsupported_set_tag(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test YAML file with a set tag fails with invalid file format error."""
    run_main_and_assert(
        input_path=YAML_DATA_PATH / "unsupported_set_tag.yaml",
        output_path=output_file,
        input_file_type="yaml",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Invalid file format",
    )


def test_main_yaml_deprecated_bool(output_file: Path) -> None:
    """Test YAML file with deprecated bool syntax emits deprecation warning."""
    with pytest.warns(DeprecationWarning, match=r"YAML bool 'True' is deprecated"):
        run_main_and_assert(
            input_path=YAML_DATA_PATH / "deprecated_bool.yaml",
            output_path=output_file,
            input_file_type="openapi",
        )


@pytest.mark.usefixtures("yaml_backend")
def test_main_yaml_bool_text_in_strings_does_not_warn(output_file: Path) -> None:
    """A block scalar or quoted string spelling a YAML 1.1 boolean holds text and does not warn with ryaml (#5546)."""
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message="YAML bool", category=DeprecationWarning)
        run_main_and_assert(
            input_path=YAML_DATA_PATH / "bool_text_in_strings.yaml",
            output_path=output_file,
            input_file_type="jsonschema",
        )


@pytest.mark.usefixtures("yaml_backend")
def test_main_yaml_bool_word_does_not_warn(output_file: Path, tmp_path: Path) -> None:
    """A word that starts with a YAML 1.1 boolean, such as ``TrueColor``, holds no boolean and does not warn (#5546)."""
    input_path = tmp_path / "bool_word.yaml"
    input_path.write_text("type: object\ntitle: TrueColor\nproperties:\n  a:\n    type: string\n")
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message="YAML bool", category=DeprecationWarning)
        run_main_and_assert(input_path=input_path, output_path=output_file, input_file_type="jsonschema")


@pytest.mark.parametrize("yaml_backend", ["pyyaml", "ryaml"], indirect=True)
@pytest.mark.usefixtures("yaml_backend")
def test_main_yaml_integer_beyond_int64(output_file: Path) -> None:
    """Integer bounds just outside 64 bits generate the same models with either YAML backend (#4910).

    ryaml cannot read such a document, so PyYAML reads it, and the models keep the exact bounds.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "integer_bounds_beyond_int64.yaml",
        output_path=output_file,
        input_file_type="openapi",
    )
    for valid, invalid, error_type in (
        ("-9223372036854776000", "-9223372036854776001", "greater_than_equal"),
        ("9223372036854776000", "9223372036854776001", "less_than_equal"),
    ):
        assert_generated_model_json_validation(
            output_file,
            module_name="integer_bounds_beyond_int64",
            model_name="Seed",
            valid_json=valid,
            invalid_json=invalid,
            expected_error_type=error_type,
        )


@pytest.mark.parametrize(
    "document",
    [
        pytest.param((YAML_DATA_PATH / "broken.yaml").read_text(), id="unclosed"),
        pytest.param(
            f"default: {'9' * (getattr(sys, 'get_int_max_str_digits', lambda: 4300)() + 1)}\n",
            id="integer_beyond_digit_limit",
            marks=pytest.mark.skipif(not hasattr(sys, "set_int_max_str_digits"), reason="no int digit limit"),
        ),
    ],
)
@pytest.mark.usefixtures("yaml_backend")
def test_main_yaml_ryaml_error_when_pyyaml_cannot_read(
    output_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], document: str
) -> None:
    """Report ryaml's error for a document PyYAML cannot read either, such as an integer beyond the digit limit."""
    input_path = tmp_path / "document.yaml"
    input_path.write_text(document)
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="document.yaml: InvalidYamlError: ryaml cannot read this document",
    )


def test_main_yaml_scientific_notation(output_file: Path) -> None:
    """Test YAML file with scientific notation default values (issue #1955).

    Scientific notation without decimal point (e.g., 1e-5) should be parsed as float,
    not as string.
    """
    run_main_and_assert(
        input_path=YAML_DATA_PATH / "scientific_notation.yaml",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
        assert_func=assert_file_content,
        expected_file="yaml/scientific_notation.py",
    )


@pytest.mark.parametrize("yaml_backend", ["pyyaml"], indirect=True)
@pytest.mark.parametrize("libyaml", ["libyaml", "pure"], indirect=True)
@pytest.mark.usefixtures("yaml_backend", "libyaml")
def test_main_yaml_tabs_read_alike(output_file: Path) -> None:
    """Tabs in a block scalar and between the words of plain scalars read alike with and without libyaml (#6592).

    libyaml refuses the tab in the block scalar and PyYAML's own scanner refused the others.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "tab_characters.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--use-schema-description"],
    )


@pytest.mark.parametrize("yaml_backend", ["pyyaml"], indirect=True)
@pytest.mark.parametrize("libyaml", ["libyaml", "pure"], indirect=True)
@pytest.mark.usefixtures("yaml_backend", "libyaml")
def test_main_yaml_plain_equals_is_text(output_file: Path) -> None:
    """A plain `=` reads as the string "=" with and without libyaml, as YAML 1.2 reads it (#6611)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "equals_enum.yaml",
        output_path=output_file,
        input_file_type="openapi",
    )


@pytest.mark.parametrize(
    ("document", "error"),
    [
        pytest.param(
            "root:\n  child: [\n",
            "ParserError: while parsing a flow node\nexpected the node content, but found '<stream end>'",
            id="unclosed",
        ),
        pytest.param(
            "root:\n  child: !!python/name:os.system\n",
            "ConstructorError: could not determine a constructor for the tag 'tag:yaml.org,2002:python/name:os.system'",
            id="unknown_tag",
        ),
        pytest.param(
            "root:\n  child: !!value =\n",
            "ConstructorError: could not determine a constructor for the tag 'tag:yaml.org,2002:value'",
            id="value_tag",
        ),
    ],
)
@pytest.mark.parametrize("yaml_backend", ["pyyaml"], indirect=True)
@pytest.mark.parametrize("libyaml", ["libyaml", "pure"], indirect=True)
@pytest.mark.usefixtures("yaml_backend", "libyaml")
def test_main_yaml_error_reads_alike(
    output_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], document: str, error: str
) -> None:
    """A document that neither reader reads fails alike with and without libyaml (#6592)."""
    input_path = tmp_path / "document.yaml"
    input_path.write_text(document)
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=f"document.yaml: {error}",
    )


YAML_1_1_NUMBERS_PATH = OPEN_API_DATA_PATH / "yaml_1_1_numbers"
YAML_1_1_NUMBER_READINGS = {"numbers": (), "text": ("--read-yaml-1-1-numbers-as-text",)}


@pytest.mark.cli_doc(
    options=["--read-yaml-1-1-numbers-as-text"],
    option_description="""Read the unquoted YAML values that only YAML 1.1 reads as numbers as their text.

PyYAML reads YAML 1.1, where an unquoted value of digits joined by `_` (`18_24`, `1_000`) or by `:` in base 60
(`16:9` is 969, `00:00:00.00` is 0.0) is a number. OpenAPI names YAML 1.2, which reads such a value as a string, as the
optional `ryaml` reader does. With this option they are their text everywhere: in `enum`, `default`, `const`,
`examples`, extensions and YAML data input, so an enum of aspect ratios keeps the values `'16:9'` and `'4:3'`.

Without it they are the numbers YAML 1.1 reads, as before, except in a document that these numbers make invalid: where
a string is required (a `pattern`, `format`, `title` or `description`, a component, path or property name, an item of
`required`) such a document is read again with these values as their text, and with the numbers elsewhere.""",
    input_schema="openapi/yaml_1_1_numbers/ratio.yaml",
    cli_args=["--read-yaml-1-1-numbers-as-text"],
    golden_output="main/main_yaml/1_1_numbers[libyaml-pyyaml-ratio-text].py",
    comparison_output="main/main_yaml/1_1_numbers[libyaml-pyyaml-ratio-numbers].py",
)
@pytest.mark.parametrize("reading", YAML_1_1_NUMBER_READINGS)
@pytest.mark.parametrize("document", ["pattern", "names", "ratio", "quoted", "positions"])
@pytest.mark.parametrize("yaml_backend", ["pyyaml"], indirect=True)
@pytest.mark.parametrize("libyaml", ["libyaml", "pure"], indirect=True)
@pytest.mark.usefixtures("yaml_backend", "libyaml")
def test_main_yaml_1_1_numbers(output_file: Path, document: str, reading: str) -> None:
    """Read the unquoted values with `_` or `:` that YAML 1.1 reads as numbers and YAML 1.2 as text (#6879).

    Without the option the pattern `00:00:00.00` (0.0) and the component `18_24` (1824) are read again as text, as when
    quoted, where they made the document invalid, while the enum `[16:9, 4:3]` stays the integers 969 and 243. In the
    document of every position, the component names, the property name `10:30`, its `required` item, its `pattern`,
    the schema `description`, the `x-enum-varnames` and the discriminator mapping are text, so the references and the
    mapping name the models, while the enum, its default, the example and the pairs of an `!!omap` default stay
    numbers. With the option every such value is its text, as the quoted document writes it.
    """
    run_main_and_assert(
        input_path=YAML_1_1_NUMBERS_PATH / f"{document}.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=list(YAML_1_1_NUMBER_READINGS[reading]),
    )
    if document == "positions":
        ratio = {"numbers": "969", "text": '"16:9"'}[reading]
        assert_generated_model_json_validation(
            output_file,
            module_name=f"yaml_1_1_numbers_{reading}",
            model_name="Audience",
            valid_json=f'{{"group": "young", "10:30": "00:00:00.00", "ratio": {ratio}}}',
            invalid_json='{"group": "young", "ratio": ' + ratio + "}",
            expected_error_type="missing",
            expected_attribute_path=("root", "group"),
            expected_attribute_value="young",
        )


@pytest.mark.parametrize("reading", YAML_1_1_NUMBER_READINGS)
@pytest.mark.parametrize("yaml_backend", ["pyyaml"], indirect=True)
@pytest.mark.usefixtures("yaml_backend")
def test_main_yaml_1_1_numbers_in_data(output_file: Path, reading: str) -> None:
    """Read the YAML 1.1-only numbers of YAML data as numbers, or as their text under the option."""
    run_main_and_assert(
        input_path=YAML_DATA_PATH / "yaml_1_1_numbers.yaml",
        output_path=output_file,
        input_file_type="yaml",
        extra_args=list(YAML_1_1_NUMBER_READINGS[reading]),
    )


@pytest.mark.parametrize("yaml_backend", ["pyyaml"], indirect=True)
@pytest.mark.usefixtures("yaml_backend")
def test_main_yaml_1_1_numbers_still_refused(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Refuse as before a document that its YAML 1.1-only numbers and another error make invalid.

    Read again with the numbers as text where a string is required, the pattern is valid, but the maxLength `ten` is
    not, so the run ends with the error of its first reading, which names the pattern read as 0.0, and shows no
    warning of the read-again, such as the one of the format `guidish` that the first reading never reached.
    """
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        run_main_and_assert(
            input_path=YAML_1_1_NUMBERS_PATH / "still_refused.yaml",
            output_path=output_file,
            input_file_type="openapi",
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="pattern\n  Input should be a valid string [type=string_type, input_value=0.0",
        )
    assert_output(
        "".join(
            f"{record.category.__name__}: {record.message}\n"
            for record in recorded
            if issubclass(record.category, UserWarning)
        )
    )


@pytest.mark.parametrize("yaml_backend", ["pyyaml"], indirect=True)
@pytest.mark.usefixtures("yaml_backend")
def test_main_yaml_1_1_numbers_document_roles(output_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Read again a split document that its number refused, as the first reading, with the roles its references need.

    A pointer into the parameter document, which holds the pattern `00:00:00.00`, loads it before the reference that
    holds it as a parameter; the read-again stops there as the first reading does and reads once more with that role,
    so the run generates as when the pattern is quoted and shows the warning of the format `unknown-format` once. One
    generation is recorded, so the second one of the formatter parity check is left out.
    """
    monkeypatch.delenv("DATAMODEL_CODE_GENERATOR_CHECK_BUILTIN_FORMATTER_PARITY", raising=False)
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "anchor_object_documents" / "held_after_pointer" / "yaml_numbers_api.yaml",
            output_path=output_file,
            input_file_type="openapi",
            extra_args=[
                "--openapi-scopes",
                "schemas",
                "paths",
                "parameters",
                "--output-model-type",
                "pydantic_v2.BaseModel",
            ],
        )
    assert_output(
        "".join(
            f"{record.category.__name__}: {record.message}\n"
            for record in recorded
            if issubclass(record.category, UserWarning)
        )
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="yaml_1_1_numbers_document_roles",
        model_name="NotesGetParametersQuery",
        valid_json='{"page": {"since": "00:00:00.00", "size": 1, "limit": 2}}',
        invalid_json='{"page": {"since": "16:9"}}',
        expected_error_type="string_pattern_mismatch",
    )


@pytest.mark.parametrize("yaml_backend", ["pyyaml"], indirect=True)
@pytest.mark.usefixtures("yaml_backend")
def test_main_yaml_1_1_numbers_read_again_from_cache(output_file: Path, tmp_path: Path) -> None:
    """Read again a refused document that the parsed-source cache serves: the cache keeps that it held such numbers.

    The document is cached from its second read and served from the cache from its third, which the third run reads.
    """
    args = ["--input", str(YAML_1_1_NUMBERS_PATH / "names.yaml"), "--input-file-type", "openapi"]
    for index in range(2):
        run_main_with_args([*args, "--output", str(tmp_path / f"names_{index}.py"), "--disable-timestamp"])
    run_main_and_assert(
        input_path=YAML_1_1_NUMBERS_PATH / "names.yaml",
        output_path=output_file,
        input_file_type="openapi",
    )
