"""End-to-end coverage for the string formats uuid6, uuid7 and uuid8 under --read-uuid6-uuid7-uuid8-formats."""

from __future__ import annotations

import json
import warnings
from contextlib import nullcontext
from typing import TYPE_CHECKING

import pydantic
import pytest

from tests.conftest import assert_output
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_model,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

OPTION = "--read-uuid6-uuid7-uuid8-formats"
COMMON_ARGS = ("--formatters", "builtin", "--disable-timestamp", "--target-python-version", "3.10")
SCHEMA = JSON_SCHEMA_DATA_PATH / "uuid_version_formats.json"
BASE_MODEL = ("--output-model-type", "pydantic_v2.BaseModel")
STANDARD_PRIMITIVES = ("--use-standard-primitive-types",)
UUID4 = "6f1c2a7e-0c3d-4a59-9a8e-2f7d1b9c0e11"
UUID6 = "1ec9414c-232a-6b00-b3c8-9e6bdeced846"
UUID7 = "01890a5d-ac96-774b-bcce-b302099a8057"
UUID8 = "320c3d4d-cc00-875b-8ec9-32d5f69181c0"
VALID = {
    "id4": UUID4,
    "id6": UUID6,
    "id7": UUID7,
    "id8": UUID8,
    "limited": UUID7,
    "history": [UUID7],
    "ref": UUID6,
    "byKey": {"a": UUID8},
    "parent": UUID7,
}
INVALID: tuple[tuple[str, object], ...] = (
    ("id7", UUID4),
    ("id6", UUID7),
    ("history", [UUID8]),
    ("ref", UUID4),
    ("byKey", {"a": UUID7}),
    ("id8", "not a uuid"),
)
"""Values the schema rejects for ``Event``: each format names the version of the UUID."""


def _verdicts(model: type[pydantic.BaseModel]) -> list[str]:
    """Return the Python types of a valid document's values, then whether the model rejects each invalid value."""
    event = model.model_validate(VALID)
    lines = [f"valid: {', '.join(f'{name}={type(getattr(event, name)).__name__}' for name in VALID)}\n"]
    for name, value in INVALID:
        try:
            model.model_validate({**VALID, name: value})
        except pydantic.ValidationError:
            verdict = "rejected"
        else:
            verdict = "accepted"
        lines.append(f"invalid {name}={json.dumps(value)}: {verdict}\n")
    return lines


@pytest.mark.cli_doc(
    options=[OPTION],
    option_description="""Read the string formats uuid6, uuid7 and uuid8 as the UUID versions they name.

RFC 9562 adds the UUID versions 6, 7 and 8, and Pydantic 2.11 their types `UUID6`, `UUID7` and `UUID8`. Without this
option `format: uuid6`, `uuid7` and `uuid8` are unknown formats, as in 0.99.0: each gives a warning and the field is
`str`. With it they are read as `format: uuid4` is: Pydantic output types them as `UUID6`, `UUID7` and `UUID8`, which
parse the value into a `uuid.UUID` and check its version, also in arrays, unions and map values; a
`--target-pydantic-version` before 2.11 gets `uuid.UUID`, as format `uuid2` does. The other outputs type them as
`uuid.UUID` under `--use-standard-primitive-types` and as `str` otherwise, as they type `uuid4`. A `--type-mappings`
entry may name them as a source or a target, and `--read-format-as-annotation` keeps them `str`.""",
    input_schema="jsonschema/uuid_version_formats.json",
    cli_args=[OPTION],
    golden_output="main/jsonschema/uuid_version_formats/read_uuid6_uuid7_uuid8_formats[read].py",
    comparison_output="main/jsonschema/uuid_version_formats/read_uuid6_uuid7_uuid8_formats[released].py",
)
@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(BASE_MODEL, id="released"),
        pytest.param((*BASE_MODEL, OPTION), id="read"),
        pytest.param((*BASE_MODEL, OPTION, "--target-pydantic-version", "2"), id="read-target-2"),
        pytest.param((*BASE_MODEL, OPTION, "--target-pydantic-version", "2.11"), id="read-target-2.11"),
        pytest.param((*BASE_MODEL, OPTION, "--use-annotated"), id="read-annotated"),
        pytest.param((*BASE_MODEL, OPTION, "--strict-types", "str"), id="read-strict-str"),
        pytest.param((*BASE_MODEL, OPTION, "--type-mappings", "string+uuid7=uuid", "uuid=uuid8"), id="read-mapped"),
        pytest.param((*BASE_MODEL, OPTION, "--read-format-as-annotation"), id="read-as-annotation"),
        pytest.param(("--output-model-type", "pydantic_v2.dataclass", OPTION), id="read-pydantic-dataclass"),
        pytest.param(("--output-model-type", "dataclasses.dataclass", OPTION), id="read-dataclass"),
        pytest.param(("--output-model-type", "dataclasses.dataclass", *STANDARD_PRIMITIVES), id="released-standard"),
        pytest.param(
            ("--output-model-type", "dataclasses.dataclass", *STANDARD_PRIMITIVES, OPTION), id="read-standard"
        ),
        pytest.param(("--output-model-type", "typing.TypedDict", *STANDARD_PRIMITIVES, OPTION), id="read-typeddict"),
        pytest.param(("--output-model-type", "msgspec.Struct", OPTION), id="read-msgspec"),
        pytest.param(
            ("--output-model-type", "msgspec.Struct", *STANDARD_PRIMITIVES, OPTION), id="read-msgspec-standard"
        ),
    ],
)
def test_read_uuid6_uuid7_uuid8_formats(
    output_file: Path, monkeypatch: pytest.MonkeyPatch, extra_args: tuple[str, ...]
) -> None:
    """Type the UUID versions 6 to 8 as their Pydantic types, or as the type of format uuid4, opt-in (#7239).

    Without the option each of the three formats warns and is ``str``, also under --use-standard-primitive-types. With
    it the Pydantic models parse each into a UUID of its version and reject another version, in a field, an array, a
    union member, a map value and a nullable field with a default; ``maxLength`` goes as for ``uuid4``. Target
    Pydantic 2 writes ``uuid.UUID``, the other outputs write ``uuid.UUID`` or ``str`` as for ``uuid4``, a type mapping
    reads them as a source and a target, and --read-format-as-annotation keeps ``str``. The warnings are stored.
    """
    monkeypatch.delenv("DATAMODEL_CODE_GENERATOR_CHECK_BUILTIN_FORMATTER_PARITY", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run_main_and_assert(
            input_path=SCHEMA,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[*COMMON_ARGS, *extra_args],
            force_exec_validation=True,
        )
    lines = [f"warning: {warning.message}\n" for warning in caught]
    if "pydantic_v2.BaseModel" in extra_args:
        with _generated_model(output_file, "uuid_version_formats", "Event") as model:
            lines.extend(_verdicts(model))
    assert_output("".join(lines), name="payloads")


@pytest.mark.parametrize("extra_args", [pytest.param((), id="released"), pytest.param((OPTION,), id="read")])
def test_read_uuid6_uuid7_uuid8_formats_openapi(output_file: Path, extra_args: tuple[str, ...]) -> None:
    """Read the UUID versions 6 to 8 of OpenAPI schemas as of JSON Schema: ``str`` with warnings, or their types."""
    context = nullcontext() if extra_args else pytest.warns(UserWarning, match="format of 'uuid[678]' not understood")
    with context:
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "uuid_version_formats.json",
            output_path=output_file,
            input_file_type="openapi",
            extra_args=[*COMMON_ARGS, *BASE_MODEL, *extra_args],
            force_exec_validation=True,
        )
