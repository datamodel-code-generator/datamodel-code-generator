"""Open enums: the enums that accept values they do not list (``--open-enums``), on the bases the project gives."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import Error, InputFileType, OpenEnums, generate
from datamodel_code_generator.__main__ import Exit
from tests.main.conftest import (
    GRAPHQL_DATA_PATH,
    OPEN_API_DATA_PATH,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

OPEN_ENUMS = OPEN_API_DATA_PATH / "open_enums.yaml"
OPENAPI_TEXT = OPEN_ENUMS.read_text(encoding="utf-8")
BASE = "tests.data.python.open_enum"
BASE_MAP = json.dumps({"StrEnum": f"{BASE}.OpenStrEnum", "IntEnum": f"{BASE}.OpenIntEnum", "Enum": f"{BASE}.OpenEnum"})
COMMON = ("--formatters", "builtin", "--target-python-version", "3.11")
OUTPUT_MODEL_TYPES = (
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    "typing.TypedDict",
    "msgspec.Struct",
)
MSGSPEC_ARGS = ("--use-msgspec-nullable-enum-alias", "--use-msgspec-tagged-unions")
BASE_OPTION_SETS = {
    "extensible": ("--open-enums", "extensible", "--open-enum-base-class-map", BASE_MAP),
    "all": ("--open-enums", "all", "--open-enum-base-class-map", BASE_MAP),
    "str-only": ("--open-enums", "all", "--open-enum-base-class-map", json.dumps({"StrEnum": f"{BASE}.OpenStrEnum"})),
}
VALID_JOB = json.dumps({
    "status": "paused",
    "priority": 7,
    "phase": "archived",
    "tags": ["red", "green"],
    "mode": "turbo",
    "lifecycle": "Deleting",
    "type": "crawl",
})


@pytest.mark.cli_doc(
    options=["--open-enum-base-class-map"],
    option_description="""Open the Enum classes of open enums on your own base classes.

The keys name the class an enum is written on: `Enum`, `StrEnum` or `IntEnum`. An open enum (`--open-enums`) written as
an Enum class takes the entry of its class as its base in place of that class, so `class JobStatus(StrEnum)` becomes
`class JobStatus(OpenStrEnum)`; the value type `--use-subclass-enum` writes before `Enum` stays, as in
`class JobStatus(str, OpenEnum)`. Each entry is your member-less subclass of its key class whose `_missing_` decides
which values it keeps, so the generator writes no runtime code of its own; a listed value still parses to its member.
Pass the mapping inline as JSON or as a path to a JSON file. Each path is imported as written, like `--base-class-map`.

msgspec honours `_missing_` from msgspec 0.18.2 on; dataclasses and TypedDict validate nothing, so there the class only
lets you build `JobStatus("paused")` yourself. Pydantic's JSON schema of such a class still lists only the known
values.""",
    input_schema="openapi/open_enums.yaml",
    cli_args=[*COMMON, "--open-enums", "extensible", "--open-enum-base-class-map", BASE_MAP],
    golden_output="main/openapi/open_enums/open_enum_base_classes[pydantic_v2.BaseModel-extensible].py",
)
@pytest.mark.parametrize("option_set", BASE_OPTION_SETS)
@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_open_enum_base_classes(output_file: Path, output_model_type: str, option_set: str) -> None:
    """Write each open enum on the base the map gives for its class, the others as before (#6818).

    ``extensible`` opens the enums an ``x-extensible-enum: true`` or an ``x-ms-enum`` ``modelAsString: true`` marks,
    ``all`` also the unmarked ones of two or more values; a marker set to false, a const beside the enum and a single
    value keep it closed. An enum merged from enums that are all open takes the base of the first, one merged with a
    closed enum stays closed; a module whose enums are all open imports no stdlib enum class it does not write. An
    open enum whose class has no entry stays the closed class.
    """
    run_main_and_assert(
        input_path=OPEN_ENUMS,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            *COMMON,
            "--output-model-type",
            output_model_type,
            *BASE_OPTION_SETS[option_set],
            *(MSGSPEC_ARGS if output_model_type == "msgspec.Struct" else ()),
        ],
    )
    if output_model_type == "pydantic_v2.BaseModel" and option_set != "str-only":
        assert_generated_model_json_validation(
            output_file,
            module_name=f"open_enum_base_classes_{option_set}",
            model_name="Job",
            valid_json=VALID_JOB,
            invalid_json='{"status": "queued", "region": "asia"}',
            expected_error_type="enum",
            expected_repr=(
                "Job(status=<JobStatus.paused: 'paused'>, provisioningState=None, type=<JobType.crawl: 'crawl'>, "
                "priority=<Priority.VALUE_7: 7>, phase=<Phase.archived: 'archived'>, region=None, tier=None, "
                "tags=[<Tag.red: 'red'>, <Tag.green: 'green'>], mode=<Mode.turbo: 'turbo'>, kind=None, "
                "pinned=None, quota=None, lifecycle=<Lifecycle.Deleting: 'Deleting'>, stage=None, color=None, "
                "pet=None)"
            ),
        )


@pytest.mark.cli_doc(
    options=["--open-enums"],
    option_description="""Accept the values an enum does not list.

APIs add enum values over time, and a closed Enum class rejects a response that carries one the client does not know
yet. `extensible` opens the enums whose schema marks them open: `x-extensible-enum: true`, or `x-ms-enum` with
`modelAsString: true`. `all` opens every enum of two or more values, except one whose schema closes it
(`x-extensible-enum: false`, `modelAsString: false`); a one-value enum is a tag, so it stays closed unless it is marked.
`none` opens no enum, as without the option, which turns the option of a preset off. A const beside an enum narrows it
to that const, so it stays closed. The list form of `x-extensible-enum`, which lists the values in place of `enum`, is
not read: such a schema has no `enum`, so its field is already a plain `str`.

An open enum written as an Enum class takes the base `--open-enum-base-class-map` names for its class.""",
    input_schema="openapi/open_enums.yaml",
    cli_args=[*COMMON, "--open-enums", "all", "--open-enum-base-class-map", BASE_MAP],
    golden_output="main/openapi/open_enums/open_enum_base_classes[pydantic_v2.BaseModel-all].py",
    comparison_output="main/openapi/open_enums/open_enum_base_classes[pydantic_v2.BaseModel-extensible].py",
)
@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(("--target-python-version", "3.10", "--use-subclass-enum"), id="subclass-310"),
        pytest.param(("--target-python-version", "3.10"), id="plain-310"),
        pytest.param(("--use-subclass-enum", "--no-use-specialized-enum"), id="subclass-311"),
        pytest.param(("--set-default-enum-member", "--capitalise-enum-members"), id="default-member"),
        pytest.param(("--reuse-model",), id="reuse-model"),
        pytest.param(("--collapse-root-models",), id="collapse-root-models"),
        pytest.param(("--enum-field-as-literal", "one"), id="literal-one"),
        pytest.param(("--enum-field-as-literal", "all"), id="literal-all"),
    ],
)
def test_open_enum_base_classes_beside_enum_options(output_file: Path, extra_args: tuple[str, ...]) -> None:
    """Keep what the other enum options decide: the base replaces only the stdlib class of an open Enum class.

    A value type ``--use-subclass-enum`` writes stays before the ``Enum`` entry, a target without ``StrEnum`` takes the
    ``Enum`` entry, member names and defaults stay, an open and a closed enum of the same values are two classes under
    ``--reuse-model``, and an enum ``--enum-field-as-literal`` writes as a ``Literal`` keeps it.
    """
    base_map = (
        json.dumps({"IntEnum": f"{BASE}.OpenIntEnum", "Enum": f"{BASE}.OpenEnum"}) if "3.10" in extra_args else BASE_MAP
    )
    run_main_and_assert(
        input_path=OPEN_ENUMS,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            *COMMON,
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--open-enums",
            "extensible",
            "--open-enum-base-class-map",
            base_map,
            *extra_args,
        ],
    )


def test_open_enum_base_classes_from_file(output_file: Path, tmp_path: Path) -> None:
    """Read the map from a JSON file, as the inline JSON gives it."""
    base_map = tmp_path / "open_enum_bases.json"
    base_map.write_text(BASE_OPTION_SETS["extensible"][-1], encoding="utf-8")
    run_main_and_assert(
        input_path=OPEN_ENUMS,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[*COMMON, "--open-enums", "extensible", "--open-enum-base-class-map", str(base_map)],
    )


def test_open_enum_base_classes_in_modules(tmp_path: Path) -> None:
    """Import the base only in the module whose enum is open, not in a module beside it."""
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "status.json").write_text(
        json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Status",
            "type": "string",
            "enum": ["queued", "done"],
            "x-extensible-enum": True,
        }),
        encoding="utf-8",
    )
    (schemas / "color.json").write_text(
        json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Color",
            "type": "string",
            "enum": ["red", "blue"],
        }),
        encoding="utf-8",
    )
    run_main_and_assert(
        input_path=schemas,
        output_path=tmp_path / "models",
        input_file_type="jsonschema",
        extra_args=[*COMMON, "--open-enums", "extensible", "--open-enum-base-class-map", BASE_MAP],
    )


def test_open_graphql_enums(output_file: Path) -> None:
    """Open every GraphQL enum of two or more values under ``all``: SDL marks none, so ``extensible`` opens none."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "open_enums.graphql",
        output_path=output_file,
        input_file_type="graphql",
        extra_args=[*COMMON, "--open-enums", "all", "--open-enum-base-class-map", BASE_MAP],
    )


@pytest.mark.parametrize(
    ("extra_args", "stderr"),
    [
        pytest.param(
            ("--open-enum-base-class-map", BASE_MAP),
            "Error: --open-enum-base-class-map requires --open-enums extensible or --open-enums all\n",
            id="without-open-enums",
        ),
        pytest.param(
            ("--open-enums", "none", "--open-enum-base-class-map", BASE_MAP),
            "Error: --open-enum-base-class-map requires --open-enums extensible or --open-enums all\n",
            id="open-enums-none",
        ),
        pytest.param(
            ("--open-enums", "all", "--open-enum-base-class-map", BASE_MAP, "--target-python-version", "3.10"),
            "Error: the StrEnum entry of --open-enum-base-class-map requires --target-python-version 3.11 or later.\n"
            "Current target version: 3.10\n"
            "StrEnum is only available in Python 3.11+; string enums use the Enum entry there.\n",
            id="str-enum-before-311",
        ),
    ],
)
def test_open_enum_base_class_map_refused(
    output_file: Path, capsys: pytest.CaptureFixture[str], extra_args: tuple[str, ...], stderr: str
) -> None:
    """Refuse a map that opens nothing, and a StrEnum entry for a target that has no StrEnum."""
    run_main_and_assert(
        input_path=OPEN_ENUMS,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", *extra_args],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=stderr,
        output_should_not_exist=True,
    )


@pytest.mark.parametrize(
    ("base_map", "message"),
    [
        pytest.param(
            {"Color": f"{BASE}.OpenStrEnum"},
            "Invalid --open-enum-base-class-map",
            id="unknown-key",
        ),
        pytest.param(
            {"StrEnum": "not a path"},
            "open_enum_base_class_map",
            id="not-a-path",
        ),
    ],
)
def test_open_enum_base_class_map_rejects_entries(
    output_file: Path, capsys: pytest.CaptureFixture[str], base_map: dict[str, str], message: str
) -> None:
    """Refuse a key no enum class is written on and an entry that is no dotted path."""
    run_main_and_assert(
        input_path=OPEN_ENUMS,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--formatters",
            "builtin",
            "--open-enums",
            "all",
            "--open-enum-base-class-map",
            json.dumps(base_map),
        ],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=message,
        output_should_not_exist=True,
    )


def test_open_enum_base_class_map_rejects_unknown_keys_in_generate() -> None:
    """Refuse, through ``generate()``, a key no enum class is written on, as the command line does."""
    with pytest.raises(Error, match=r"open_enum_base_class_map keys must be Enum, StrEnum, IntEnum, not 'Color'"):
        generate(
            OPENAPI_TEXT,
            input_file_type=InputFileType.OpenAPI,
            open_enums=OpenEnums.All,
            open_enum_base_class_map={"Color": f"{BASE}.OpenStrEnum"},
        )
