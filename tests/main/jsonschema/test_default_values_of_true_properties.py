"""A ``Class.field`` key of ``--default-values`` reaches a property whose schema is ``true``."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, OPEN_API_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

TRUE_PROPERTIES_PATH = JSON_SCHEMA_DATA_PATH / "default_values_true_properties"
OPTION = "--apply-default-values-to-true-properties"
SETTINGS_KEYS = {
    "Settings.mode": "fast",
    "Settings.forbidden": 1,
    "Settings.limit": 5,
    "Settings.retry-count": 3,
    "Settings.retry-limit": 4,
    "Nested.flag": True,
}
RESTATED_KEYS = {"Dog.name": "rex", "Dog.tag": "dt", "Puppy.name": "pup", "Pet.name": "pn"}


def _default_values(keys: dict[str, object]) -> tuple[str, str]:
    """Return ``--default-values`` and its inline JSON for ``keys``."""
    return "--default-values", json.dumps(keys)


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([*_default_values(SETTINGS_KEYS)], id="off"),
        pytest.param([*_default_values(SETTINGS_KEYS), OPTION], id="on"),
        pytest.param([*_default_values(SETTINGS_KEYS), OPTION, "--use-default"], id="required-use-default"),
        pytest.param(
            [*_default_values({"mode": "fast", "flag": True, "limit": 5, "retry-count": 3, "retry-limit": 4}), OPTION],
            id="flat-key",
        ),
        pytest.param([*_default_values(SETTINGS_KEYS), OPTION, "--strict-nullable"], id="strict-nullable"),
        pytest.param([*_default_values(SETTINGS_KEYS), OPTION, "--use-default-kwarg"], id="use-default-kwarg"),
        pytest.param(
            [
                *_default_values(SETTINGS_KEYS),
                *(OPTION, "--use-default", "--output-model-type", "dataclasses.dataclass"),
            ],
            id="dataclass",
        ),
        pytest.param(
            [*_default_values(SETTINGS_KEYS), *(OPTION, "--use-default", "--output-model-type", "msgspec.Struct")],
            id="msgspec",
        ),
    ],
)
def test_default_values_reach_a_true_property(output_file: Path, extra_args: list[str]) -> None:
    """Give a property whose schema is ``true`` the default its key gives it, as a property whose schema is ``{}``.

    A ``false`` property admits no value and keeps none, the required property keeps its default only under
    ``--use-default``, and without the option the ``true`` properties keep no default, as in 0.100.0.
    """
    run_main_and_assert(
        input_path=TRUE_PROPERTIES_PATH / "settings.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", *extra_args],
    )


@pytest.mark.cli_doc(
    options=["--apply-default-values-to-true-properties"],
    option_description="""Give the default of `--default-values` to a property whose schema is `true`.

Without it a property whose schema is `true` takes no default from `--default-values`: the key `Dog.name` leaves
`name: str | None = None` where `Dog` restates an inherited `name` as `true` (the usual way to mark an inherited
property as evaluated beside `unevaluatedProperties: false`), though it gives the default to a property declared as `{}`
or with a description. With this option a `true` property takes the default its `Class.field` or `field` key gives it,
in the class that declares it and in a child that restates an inherited property: `name: str | None = 'rex'`. A
property whose schema is `false` admits no value and keeps none.""",
    input_schema="jsonschema/default_values_true_properties/restated.json",
    cli_args=[
        "--default-values",
        '{"Dog.name": "rex", "Dog.tag": "dt", "Puppy.name": "pup", "Pet.name": "pn"}',
        "--apply-default-values-to-true-properties",
    ],
    golden_output=(
        "main/jsonschema/default_values_of_true_properties/"
        "default_values_reach_a_property_a_child_restates_as_true[merge-all-on].py"
    ),
    comparison_output=(
        "main/jsonschema/default_values_of_true_properties/"
        "default_values_reach_a_property_a_child_restates_as_true[off].py"
    ),
    related_options=["--default-values", "--allof-merge-mode", "--use-default"],
)
@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([*_default_values(RESTATED_KEYS)], id="off"),
        pytest.param([*_default_values(RESTATED_KEYS), OPTION], id="on"),
        pytest.param([*_default_values(RESTATED_KEYS), OPTION, "--allof-merge-mode", "none"], id="merge-none"),
        pytest.param([*_default_values(RESTATED_KEYS), "--allof-merge-mode", "all"], id="merge-all-off"),
        pytest.param([*_default_values(RESTATED_KEYS), OPTION, "--allof-merge-mode", "all"], id="merge-all-on"),
        pytest.param(
            [*_default_values(RESTATED_KEYS), OPTION, "--output-model-type", "dataclasses.dataclass"], id="dataclass"
        ),
        pytest.param([*_default_values(RESTATED_KEYS), OPTION, "--output-model-type", "msgspec.Struct"], id="msgspec"),
    ],
)
def test_default_values_reach_a_property_a_child_restates_as_true(output_file: Path, extra_args: list[str]) -> None:
    """Give a property a child restates as ``true`` the default the key of the child gives it.

    The child of a child restates it too. ``--allof-merge-mode all`` writes the default of the child with or without
    the option, but that of the child of a child only with it, whose parent restates the property as ``true`` too.
    """
    run_main_and_assert(
        input_path=TRUE_PROPERTIES_PATH / "restated.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", *extra_args],
    )


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([*_default_values({"Dog.name": "rex", "Owner.any-value": 1}), OPTION], id="on"),
        pytest.param([*_default_values({"Dog.name": "rex", "Owner.any-value": 1})], id="off"),
    ],
)
def test_default_values_reach_a_true_property_of_an_openapi_schema(output_file: Path, extra_args: list[str]) -> None:
    """Give the ``true`` properties of OpenAPI schemas, a child's restatement included, their defaults."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "default_values_true_properties.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", *extra_args],
    )


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([*_default_values({"Dog.name": "rex", "Dog.note": "n"})], id="off"),
        pytest.param([*_default_values({"Dog.name": "rex", "Dog.note": "n"}), OPTION], id="on"),
    ],
)
def test_default_values_reach_a_true_property_in_request_and_response_models(
    output_file: Path, extra_args: list[str]
) -> None:
    """Give the request and the response model of a class the defaults of its ``true`` properties."""
    run_main_and_assert(
        input_path=TRUE_PROPERTIES_PATH / "read_only.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--read-only-write-only-model-type", "request-response", *extra_args],
    )


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="off"),
        pytest.param([OPTION], id="on"),
    ],
)
def test_default_values_reach_a_property_a_child_restates_over_a_mapped_parent(
    output_file: Path, extra_args: list[str]
) -> None:
    """Give the ``true`` property of a child the default of its key where the parent is a mapped ``$ref``."""
    run_main_and_assert(
        input_path=TRUE_PROPERTIES_PATH / "mapped.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *("--formatters", "builtin"),
            *("--external-ref-mapping", "parent_document.json=mypkg.parent"),
            *_default_values({"Dog.name": "rex"}),
            *extra_args,
        ],
    )
