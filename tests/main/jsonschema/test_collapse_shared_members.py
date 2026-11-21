"""Keep the members of a root type that --collapse-root-models inlined at several sites at each of them."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pydantic
import pytest

from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_model,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

INPUT = JSON_SCHEMA_DATA_PATH / "collapse_shared_members.json"
NULLABLE_INPUT = OPEN_API_DATA_PATH / "collapse_shared_nullable_members.yaml"
KEEP = "--keep-shared-members-on-collapsed-root-models"
KEEP_NULL = "--keep-reference-null-on-collapsed-root-models"
OPTION_SETS = {"released": (), "keep": (KEEP,)}
NULLABLE_OPTION_SETS = {"keep_null": (KEEP_NULL,), "keep_null_keep": (KEEP_NULL, KEEP)}
COMMON_ARGS = ("--collapse-root-models", "--formatters", "builtin", "--disable-timestamp")


@pytest.mark.cli_doc(
    options=[KEEP],
    option_description="""Keep each member of a root type that collapse inlined at several sites at every one of them.

`--collapse-root-models` writes the root type of a root model at each site that names it, and those copies share
their members. When a member names another root model that collapse inlines only afterwards, as the plain member of
a recursive union is reached through the recursion, earlier releases put its type into one copy alone and left the
other copies with a member that writes nothing: `values: dict[str, "Entry"]` drops the `str` of `Fallback`, so a
value the schema accepts is rejected, and a copy that has no member left writes `Any`. With this option every copy
takes the member's type (`values: dict[str, Union["Entry", str]]`); a copy that lies inside that type keeps naming
the member's model, so no type contains itself.""",
    input_schema="jsonschema/collapse_shared_members.json",
    cli_args=["--collapse-root-models", KEEP],
    golden_output="main/jsonschema/collapse_shared_members/collapse_shared_members[pydantic_v2-keep].py",
    comparison_output="main/jsonschema/collapse_shared_members/collapse_shared_members[pydantic_v2-released].py",
)
@pytest.mark.parametrize("option_set", OPTION_SETS)
@pytest.mark.parametrize(
    "output_model_type",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass"],
    ids=["pydantic_v2", "pydantic_v2_dataclass"],
)
def test_collapse_shared_members(output_file: Path, output_model_type: str, option_set: str) -> None:
    """Keep the plain member of a recursive union at every site under the option (#5205).

    Without it the map values of ``Model`` lose ``Fallback``'s ``str``, as in 0.90.0: Pydantic rejects ``"str"``, and
    the dataclass output writes ``Any``, which also accepts ``5``. With it both outputs accept every valid payload and
    reject every invalid one.
    """
    run_main_and_assert(
        input_path=INPUT,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", output_model_type, *COMMON_ARGS, *OPTION_SETS[option_set]],
    )
    _store_payload_verdicts(output_file, "collapse_shared_members")


@pytest.mark.parametrize("option_set", NULLABLE_OPTION_SETS)
def test_collapse_shared_nullable_members(output_file: Path, option_set: str) -> None:
    """Keep the null of a nullable member at every site that shares it under both options.

    Under --keep-reference-null-on-collapsed-root-models alone the sites lose ``Text`` with its null, as in 0.90.0; with
    --keep-shared-members-on-collapsed-root-models too each site writes ``str | None`` and accepts null.
    """
    run_main_and_assert(
        input_path=NULLABLE_INPUT,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[*COMMON_ARGS, *NULLABLE_OPTION_SETS[option_set]],
    )
    _store_payload_verdicts(output_file, "collapse_shared_nullable_members")


@pytest.mark.parametrize(
    ("input_path", "extra_args"),
    [
        pytest.param(JSON_SCHEMA_DATA_PATH / "collapse_root_models_any_union.json", [], id="any-union"),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "object_maps" / "collapsed_recursive_pattern_map.json",
            ["--use-single-dict-for-object-maps"],
            id="pattern-map",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "object_maps" / "collapsed_recursive_property_names_map.json",
            [],
            id="property-names-map",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "collapse_root_models_transitive_reference_cycle.json",
            [],
            id="transitive-reference-cycle",
        ),
    ],
)
def test_collapse_shared_members_of_released_outputs(
    output_file: Path, input_path: Path, extra_args: list[str]
) -> None:
    """Write the members that the released outputs of these inputs lose, or write as ``Any``, under the option.

    Their default outputs stay as pinned. A union that had no member left writes its members beside ``Any``, a recursive
    map writes its value union, and the cycle of ``ModelA`` and ``ModelB`` keeps every member at both fields, with
    ``ModelA`` named where it reaches itself.
    """
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*COMMON_ARGS, *extra_args, KEEP],
    )


def _store_payload_verdicts(output_file: Path, name: str) -> None:
    """Store whether the generated ``Model`` accepts each valid and invalid payload of ``name``."""
    payloads = json.loads((DATA_PATH / "payloads" / f"{name}.json").read_text())
    documents = [
        *((f"valid {json.dumps(payload)}", payload) for payload in payloads["valid"]),
        *((f"invalid {json.dumps(payload)}", payload) for payload in payloads["invalid"]),
    ]
    with _generated_model(output_file, f"{name}_model", "Model") as model:
        adapter = pydantic.TypeAdapter(model)
        assert_output(
            "".join(f"{label}: {_verdict(adapter, document)}\n" for label, document in documents), name="payloads"
        )


def _verdict(adapter: pydantic.TypeAdapter[Any], document: object) -> str:
    """Return whether the generated model accepts a document."""
    try:
        adapter.validate_python(document)
    except pydantic.ValidationError:
        return "rejected"
    return "accepted"
