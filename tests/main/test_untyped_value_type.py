"""Write the values a schema leaves untyped as one type under --untyped-value-type."""

from __future__ import annotations

import datetime as dt
import json
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any

import pydantic
import pytest

from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_output
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    PROTOBUF_DATA_PATH,
    _generated_model,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

COMMON_ARGS = ("--formatters", "builtin", "--disable-timestamp")
JSON_VALUE = ("--untyped-value-type", "pydantic.JsonValue")
EVENT_LOG = JSON_SCHEMA_DATA_PATH / "event_log_untyped" / "event_log_untyped.json"
OPTION_SETS = {
    "off": (),
    "json-value": JSON_VALUE,
    "json-value-collapse-root-models": (*JSON_VALUE, "--collapse-root-models"),
    "json-value-use-object-type": (*JSON_VALUE, "--use-object-type"),
    "json-value-type-overrides": (*JSON_VALUE, "--type-overrides", '{"AuditEvent.payload": "typing.Any"}'),
    "object": ("--untyped-value-type", "object"),
    "any-use-object-type": ("--untyped-value-type", "Any", "--use-object-type"),
}
VALID_DOCUMENTS: tuple[dict[str, Any], ...] = (
    {
        "payload": {"a": [1, 2.5, None, True]},
        "previous": "x",
        "note": [{"k": "v"}],
        "context": {"n": {"m": []}},
        "flags": {"f": False},
        "attributes": {"a": 1},
        "tags": [1, "two", None],
        "samples": [{"s": 1}],
        "extra": None,
        "labelOrObject": {"k": 1},
        "choice": 3,
        "notText": 4,
        "headers": {"x-trace": "abc"},
        "snapshot": {"deep": {"er": [1]}},
        "baseline": None,
        "actor": {"id": 7, "role": "admin"},
    },
    {"payload": None},
)
"""Documents the schema accepts; every model of the input parses each to the same values with and without the option."""
PYTHON_VALUES: tuple[tuple[str, object], ...] = (
    ("tuple", (1, 2)),
    ("set", {1}),
    ("datetime", dt.datetime(2026, 11, 14, tzinfo=dt.timezone.utc)),
)
"""Python values that are no JSON values, given to ``payload``: ``Any`` takes them, ``JsonValue`` rejects them."""


def _parsed(model: Any, document: object) -> str:
    """Return the values a generated model parses a document to, else that it rejects it."""
    try:
        return repr(model.model_validate(document).model_dump(exclude_none=True))
    except pydantic.ValidationError:
        return "rejected"


@pytest.mark.cli_doc(
    options=["--untyped-value-type"],
    option_description="""Write each value the schema leaves untyped as one type instead of `Any`.

A value the schema leaves untyped, `{}`, `true`, a schema whose keywords give it no type (`{"description": "..."}`, not
an `enum`), the values of an object without properties and the items of an array without items, is written as `Any`, and
under `--use-object-type` the values and items of containers as `object`, as in 0.96.0. With `--untyped-value-type
pydantic.JsonValue` each of them is written as Pydantic's `JsonValue` (Pydantic 2.5 or later), in every position: a
field, a union member, a dict value or key's value, a list item, the root of a root model, the value of a type alias and
`__pydantic_extra__`. The structure is as without the option, and the models parse a JSON document to the same values;
Python values that are no JSON values, such as a tuple, a set or a `datetime`, are rejected. The value may also be
`object`, `Any`, or the dotted import path of a type of your own, which the models import (for example a recursive alias
for `typing.TypedDict` output). The option takes precedence over `--use-object-type`; `--type-overrides`,
`x-python-type` and `x-python-import` keep their types, and `msgspec.Struct` output keeps `Any`, which msgspec decodes
into JSON values. A definition named like the imported type is renamed by the rule for names an import takes
(`JsonValue` becomes `JsonValueModel`), unless `--collapse-root-models` writes the type at its references.""",
    input_schema="jsonschema/event_log_untyped/event_log_untyped.json",
    cli_args=["--untyped-value-type", "pydantic.JsonValue"],
    golden_output="main/untyped_value_type/untyped_value_type[json-value-pydantic_v2.BaseModel].py",
    comparison_output="main/untyped_value_type/untyped_value_type[off-pydantic_v2.BaseModel].py",
)
@pytest.mark.parametrize("output_model_type", ["pydantic_v2.BaseModel", "pydantic_v2.dataclass"])
@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_untyped_value_type(output_file: Path, option_set: str, output_model_type: str) -> None:
    """Write every untyped value of an audit event as pydantic.JsonValue, keeping the structure (#6817).

    ``payload``, ``previous`` (``true``), ``note``, the values of ``context``, ``flags``, ``attributes``, ``headers``
    and the extra keys of ``actor``, the items of ``tags`` and ``samples``, the untyped member of ``choice``,
    ``notText`` and the root of the ``JsonValue`` definition are each ``JsonValue``; the definition named like the
    import becomes ``JsonValueModel``, and ``--collapse-root-models`` writes ``JsonValue`` at its references.
    ``--use-object-type`` yields to the option, a ``--type-overrides`` entry of ``typing.Any`` and the
    ``x-python-type`` of ``rawJson`` keep ``Any``. ``object`` is written as given, and ``Any`` writes ``Any`` where
    ``--use-object-type`` alone writes ``object``. Every model parses the documents the schema accepts to the values
    it parses them to without the option, and ``JsonValue`` rejects the Python values that are no JSON values.
    """
    run_main_and_assert(
        input_path=EVENT_LOG,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*COMMON_ARGS, "--output-model-type", output_model_type, *OPTION_SETS[option_set]],
        force_exec_validation=True,
    )
    if output_model_type != "pydantic_v2.BaseModel":
        return
    with _generated_model(output_file, "event_log_untyped", "AuditEvent") as model:
        lines = [
            *(f"valid {json.dumps(document)}: {_parsed(model, document)}\n" for document in VALID_DOCUMENTS),
            *(f"payload {name}: {_parsed(model, {'payload': value})}\n" for name, value in PYTHON_VALUES),
        ]
    assert_output("".join(lines), name="payloads")


@pytest.mark.parametrize(
    ("output_model_type", "value"),
    [
        pytest.param("typing.TypedDict", "pydantic.JsonValue", id="typed-dict"),
        pytest.param("dataclasses.dataclass", "object", id="dataclass"),
    ],
)
def test_untyped_value_type_stdlib_outputs(output_file: Path, output_model_type: str, value: str) -> None:
    """Write the untyped values of TypedDict and dataclass output as the given type, the alias of a definition too."""
    run_main_and_assert(
        input_path=EVENT_LOG,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*COMMON_ARGS, "--output-model-type", output_model_type, "--untyped-value-type", value],
        force_exec_validation=True,
    )


def test_untyped_value_type_keeps_msgspec_any(output_file: Path, tmp_path: Path) -> None:
    """Generate the same msgspec models with the option as without it: msgspec decodes ``Any`` into JSON values."""
    without = tmp_path / "without.py"
    args = [*COMMON_ARGS, "--output-model-type", "msgspec.Struct"]
    run_main_and_assert(input_path=EVENT_LOG, output_path=without, input_file_type="jsonschema", extra_args=args)
    run_main_and_assert(
        input_path=EVENT_LOG,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*args, *JSON_VALUE],
        expected_output=without.read_text(encoding="utf-8"),
    )


@pytest.mark.parametrize("value", ["JsonValue", "pydantic.", "my module.Value"])
def test_untyped_value_type_refuses_other_values(
    output_file: Path, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    """Refuse a value that is not ``Any``, ``object`` or a dotted import path."""
    run_main_and_assert(
        input_path=EVENT_LOG,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*COMMON_ARGS, "--untyped-value-type", value],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="untyped_value_type must be Any, object or a dotted import path",
    )


@pytest.mark.parametrize(
    ("target", "warns"),
    [pytest.param("2", True, id="pydantic-2"), pytest.param("2.11", False, id="pydantic-2.11")],
)
def test_untyped_value_type_pydantic_floor(output_file: Path, target: str, *, warns: bool) -> None:
    """State that pydantic.JsonValue needs Pydantic 2.5 for an older explicit target, and nothing for 2.11."""
    with pytest.warns(UserWarning, match=r"pydantic\.JsonValue needs 2\.5") if warns else nullcontext():
        run_main_and_assert(
            input_path=EVENT_LOG,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[*COMMON_ARGS, *JSON_VALUE, "--target-pydantic-version", target],
        )


def test_untyped_value_type_imports_only_where_used(output_dir: Path) -> None:
    """Import the type only in the modules that write an untyped value: ``events`` does, ``users`` does not."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "event_log_untyped" / "modules",
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=[*COMMON_ARGS, *JSON_VALUE],
    )


@pytest.mark.parametrize("option_set", ["off", "json-value", "json-value-collapse-root-models"])
def test_untyped_value_type_openapi(output_file: Path, option_set: str) -> None:
    """Type the untyped component and maps of an OpenAPI document, the JsonValue component of a FastAPI app among them.

    The component named ``JsonValue`` becomes ``JsonValueModel`` beside the import, and ``--collapse-root-models``
    writes ``JsonValue`` at its references instead.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "untyped_value_type" / "task_api.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[*COMMON_ARGS, *OPTION_SETS[option_set]],
        force_exec_validation=True,
    )


UNTYPED_API = """openapi: 3.0.3
info: {title: Items, version: '1'}
servers: [{url: 'https://api.example.com'}]
paths:
  /items/{id}:
    post:
      operationId: putItem
      parameters:
        - {name: id, in: path, required: true, schema: {type: string}}
        - {name: filter, in: query, schema: {}}
      requestBody:
        content:
          application/json:
            schema: {$ref: '#/components/schemas/Item'}
      responses:
        '200':
          description: The stored value
          content:
            application/json:
              schema: {}
components:
  schemas:
    Item:
      type: object
      properties:
        payload: {}
        history: {type: array, items: {}}
        labels: {type: object, additionalProperties: {}}
"""


@pytest.mark.parametrize(
    "target",
    [
        pytest.param(
            [
                *("--generate-client", "httpx2", "--client-output", "client"),
                *("--client-package", "client", "--client-model-package", "models"),
            ],
            id="client",
        ),
        pytest.param(
            [
                *("--generate-server", "fastapi", "--server-output", "server"),
                *("--server-package", "server", "--server-model-package", "models"),
            ],
            id="server",
        ),
    ],
)
def test_untyped_value_type_object_in_targets(
    target: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generate a client or a server whose operations carry values written as ``object``.

    The targets read the type the models write for an untyped field, query parameter and response, ``object``, which
    needs no import.
    """
    monkeypatch.chdir(tmp_path)
    document = tmp_path / "items.yaml"
    document.write_text(UNTYPED_API, encoding="utf-8")
    run_main_and_assert(
        input_path=document,
        output_path=tmp_path / "models.py",
        input_file_type="openapi",
        extra_args=[
            *COMMON_ARGS,
            *("--openapi-scopes", "schemas", "api", "--untyped-value-type", "object"),
            *target,
        ],
    )


def test_untyped_value_type_protobuf(output_file: Path) -> None:
    """Write the values of ``google.protobuf.Struct``, ``Value`` and ``ListValue`` as the type too."""
    run_main_and_assert(
        input_path=PROTOBUF_DATA_PATH / "complex_proto3.proto",
        output_path=output_file,
        input_file_type="protobuf",
        extra_args=[*COMMON_ARGS, *JSON_VALUE],
    )
