"""Type aliases whose whole value is a model that names itself, which Pydantic 2 refuses in their second user.

Pydantic builds the schema of such a ``TypeAliasType`` (or ``type`` statement) for the first model that uses it and
refuses it in the next one, so the module fails to import or that model fails to validate (#5269).
``--use-plain-alias-for-recursive-models`` writes such an alias as a plain ``TypeAlias``; without it the output keeps
its 0.89.0 bytes and a warning names the alias, its class and the classes that use it.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

TWO_USERS = JSON_SCHEMA_DATA_PATH / "recursive_model_alias_two_users.json"
KEPT = JSON_SCHEMA_DATA_PATH / "recursive_model_alias_kept.json"
OUTPUTS = {
    "pydantic-dataclass": (["--output-model-type", "pydantic_v2.dataclass"], "dataclass_type"),
    "base-model-type-alias": (["--output-model-type", "pydantic_v2.BaseModel", "--use-type-alias"], "model_type"),
}
"""The Pydantic outputs that write type aliases, with the error type a node that is no object gives."""
TARGETS = (
    pytest.param("3.10", id="3.10"),
    pytest.param(
        "3.12",
        id="3.12",
        marks=pytest.mark.skipif(sys.version_info < (3, 12), reason="Runtime requires Python type statements"),
    ),
)


@pytest.mark.cli_doc(
    options=["--use-plain-alias-for-recursive-models"],
    option_description="""Write the type alias of a model that names itself as a plain alias in Pydantic output.

A type alias whose whole value is a model that names itself, such as `Alias = TypeAliasType("Alias", Node)` for
`Node.next: Node | None`, is refused by Pydantic 2 in the second model that uses it: the module fails to import
(circular-reference-schema), or, where the alias carries metadata, that model fails to validate. With
`--use-plain-alias-for-recursive-models` the alias is written as `Alias: TypeAlias = Node`, the model itself, which
every model that uses it builds; it then has no entry of its own in `model_json_schema()` `$defs`.""",
    input_schema="jsonschema/recursive_model_alias_two_users.json",
    cli_args=["--output-model-type", "pydantic_v2.dataclass", "--use-plain-alias-for-recursive-models"],
    golden_output=(
        "main/jsonschema/plain_alias_for_recursive_models/"
        "use_plain_alias_for_recursive_models[pydantic-dataclass-3.10].py"
    ),
    comparison_output=(
        "main/jsonschema/plain_alias_for_recursive_models/recursive_model_alias_warns[pydantic-dataclass].py"
    ),
)
@pytest.mark.parametrize("target_python_version", TARGETS)
@pytest.mark.parametrize("output", OUTPUTS)
def test_use_plain_alias_for_recursive_models(output_file: Path, output: str, target_python_version: str) -> None:
    """Write the aliases of a model that names itself as plain aliases, which both models that use them build.

    ``Alias``, ``Described`` and ``Solo`` are a ``$ref`` to ``Node``, whose ``next`` is a ``Node``; ``Described`` and
    ``Solo`` carry a description. ``A`` and ``B`` both use the first two, ``A`` alone ``Solo``, and ``B`` validates the
    nodes the schema describes.
    """
    run_main_and_assert(
        input_path=TWO_USERS,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *OUTPUTS[output][0],
            "--target-python-version",
            target_python_version,
            "--use-plain-alias-for-recursive-models",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"recursive_model_alias_two_users_{output.replace('-', '_')}_{target_python_version[2:]}",
        model_name="B",
        valid_json='{"n": {"next": {"next": null}}, "d": {"next": null}}',
        invalid_json='{"n": {"next": 1}}',
        expected_error_type=OUTPUTS[output][1],
    )


@pytest.mark.parametrize("output", OUTPUTS)
def test_recursive_model_alias_warns(output_file: Path, output: str, recwarn: pytest.WarningsRecorder) -> None:
    """Keep the 0.89.0 aliases without the option, and warn about each one that two classes use, not ``Solo``."""
    run_main_and_assert(
        input_path=TWO_USERS,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*OUTPUTS[output][0], "--formatters", "builtin", "--disable-timestamp"],
    )
    assert_output(
        "".join(
            f"{message}\n"
            for message in dict.fromkeys(str(warning.message) for warning in recwarn if warning.category is UserWarning)
        )
    )


@pytest.mark.parametrize("extra_args", [[], ["--use-plain-alias-for-recursive-models"]], ids=["default", "option"])
def test_recursive_model_alias_kept(output_file: Path, extra_args: list[str]) -> None:
    """Keep the aliases that Pydantic builds for every model: of a container, of a union and of a plain model.

    ``Nodes`` and ``MaybeNode`` hold ``Node`` in a list and beside None, ``LeafAlias`` is a model that does not name
    itself, and ``ChainAlias`` is a model that names the alias, which its cycle runs through. The option changes none
    of them, and no warning names them.
    """
    run_main_and_assert(
        input_path=KEPT,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.dataclass",
            *extra_args,
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"recursive_model_alias_kept_{len(extra_args)}",
        model_name="B",
        valid_json='{"nodes": [{"next": null}], "maybe": null, "leaf": {"value": 1}, "chain": {"next": {}}}',
        invalid_json='{"leaf": {"value": "x"}}',
        expected_error_type="int_parsing",
    )
