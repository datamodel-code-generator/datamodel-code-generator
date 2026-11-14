"""Read the Kubernetes schema extensions as the keywords they stand for under --read-kubernetes-extensions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    assert_generated_model_json_invalid,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

OUTPUT_MODEL_TYPES = (
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    "typing.TypedDict",
    "msgspec.Struct",
)
OPTION_SETS = {"off": (), "on": ("--read-kubernetes-extensions",)}
CRD_SCHEMA = JSON_SCHEMA_DATA_PATH / "kubernetes_crd_schema.yaml"


def _recorded_warnings(recwarn: pytest.WarningsRecorder) -> str:
    """Return the distinct user warnings a generation recorded, one per line."""
    return "".join(
        f"{message}\n"
        for message in dict.fromkeys(str(warning.message) for warning in recwarn if warning.category is UserWarning)
    )


@pytest.mark.cli_doc(
    options=["--read-kubernetes-extensions"],
    option_description="""Read the Kubernetes schema extensions as the keywords they stand for.

Kubernetes schemas, the `openAPIV3Schema` of a CustomResourceDefinition and the `/openapi/v3` documents a cluster
serves, carry extensions that change what a value may be. Without this option they are ignored. With it, wherever a
schema object writes them:

- `x-kubernetes-int-or-string: true`, or the format `int-or-string`, is read as `type: [integer, string]` in place of an
  `anyOf` or `oneOf` of exactly those two types, so a bare one is `int | str` instead of `Any`, and a quantity (that
  `anyOf` beside a `pattern`) is one `int | constr(pattern=...)` instead of two root models;
- `x-kubernetes-preserve-unknown-fields: true` beside `properties` and no `additionalProperties` is read as
  `additionalProperties: true`, so a Pydantic model keeps the keys it does not declare (`extra='allow'`);
- `x-kubernetes-group-version-kind` gives the `apiVersion` and the `kind` property, each where all its entries name one
  value and the property is a plain string, that value as `const` and `default`, so they are `Literal['apps/v1'] =
  'apps/v1'` and `Literal['Deployment'] = 'Deployment'`. Where `const` is no keyword (draft-04) it is not read.

The other extensions (`x-kubernetes-list-type`, `x-kubernetes-list-map-keys`, `x-kubernetes-map-type`,
`x-kubernetes-validations`, `x-kubernetes-embedded-resource`) stay as they are; `--field-extra-keys` carries them into
the field.""",
    input_schema="jsonschema/kubernetes_crd_schema.yaml",
    cli_args=["--read-kubernetes-extensions"],
    golden_output="main/kubernetes_extensions/kubernetes_crd_schema[pydantic_v2.BaseModel-on].py",
    comparison_output="main/kubernetes_extensions/kubernetes_crd_schema[pydantic_v2.BaseModel-off].py",
)
@pytest.mark.parametrize("option_set", OPTION_SETS)
@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_kubernetes_crd_schema(output_file: Path, output_model_type: str, option_set: str) -> None:
    """Read the extensions of an extracted CRD version only under the option, the same way for every output type.

    Without the option the extensions are ignored: a bare x-kubernetes-int-or-string is any value, a quantity (an anyOf
    of integer and string beside a pattern) is two root models, a model drops the keys
    x-kubernetes-preserve-unknown-fields keeps, and apiVersion and kind are free strings. With it the int-or-string
    fields are an integer or a string, the quantity keeps its pattern inline where the output writes one, the model
    beside properties keeps unknown keys where the output can (Pydantic), and apiVersion and kind are the Literal the
    x-kubernetes-group-version-kind names, with that default. The list, map, embedded-resource and validation
    extensions read nothing either way.
    """
    run_main_and_assert(
        input_path=CRD_SCHEMA,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", output_model_type, *OPTION_SETS[option_set]],
    )
    if output_model_type == "pydantic_v2.BaseModel" and option_set == "on":
        assert_generated_model_json_validation(
            output_file,
            module_name="kubernetes_crd_schema",
            model_name="Widget",
            valid_json=(
                '{"spec": {"replicas": 1, "port": "http", "maxSurge": 1, "resources": {"limits": {"cpu": "500m", '
                '"memory": 1024}}, "selector": {"app": "web", "tier": "front"}}}'
            ),
            invalid_json='{"kind": "Gadget", "spec": {"replicas": 1}}',
            expected_error_type="literal_error",
            expected_attribute_path=("spec", "selector", "tier"),
            expected_attribute_value="front",
        )
        assert_generated_model_json_invalid(
            output_file,
            module_name="kubernetes_crd_schema",
            model_name="Widget",
            invalid_json='{"spec": {"replicas": 1, "port": [1]}}',
            expected_error_type="int_type",
        )


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(("--extra-fields", "ignore"), id="extra-fields-ignore"),
        pytest.param(("--use-single-value-enum-as-const",), id="use-single-value-enum-as-const"),
        pytest.param(("--field-extra-keys", "x-kubernetes-validations"), id="field-extra-keys"),
        pytest.param(("--snake-case-field",), id="snake-case-field"),
        pytest.param(("--collapse-root-models",), id="collapse-root-models"),
        pytest.param(("--schema-version", "draft-04"), id="draft-04"),
    ],
)
def test_kubernetes_crd_schema_beside_other_options(output_file: Path, extra_args: tuple[str, ...]) -> None:
    """Read the extensions under the option beside the options they meet.

    --extra-fields wins over the extra keys x-kubernetes-preserve-unknown-fields keeps, as it does over
    additionalProperties; a one-value enum is still read as const; --field-extra-keys still writes the extensions it
    names, which stay in the read schema; --snake-case-field keeps the Literal on the renamed field; the quantities
    are written the same with --collapse-root-models; and where const is no keyword (draft-04) the
    x-kubernetes-group-version-kind is not read while the other extensions are.
    """
    run_main_and_assert(
        input_path=CRD_SCHEMA,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-kubernetes-extensions",
            *extra_args,
        ],
    )


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_kubernetes_group_version_document(output_dir: Path, recwarn: pytest.WarningsRecorder, option_set: str) -> None:
    """Read the extensions of a cluster's OpenAPI document of one group version under the option.

    The int-or-string format of IntOrString is read as the integer or string it names, so the warning that the format
    is not understood goes, and the type stays ``int | str``. Each resource with one x-kubernetes-group-version-kind
    gets the Literal apiVersion and kind; DeleteOptions, listed under two groups, gets only its kind; WatchEvent has no
    such properties; an operation's x-kubernetes-group-version-kind, a map, is no schema. The served CRD root keeps the
    unknown keys of its spec and of its embedded resource. JSONSchemaProps names its properties after the extensions,
    which are property names there, not extensions.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "kubernetes_group_version.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--formatters",
            "builtin",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            *OPTION_SETS[option_set],
        ],
    )
    assert_output(_recorded_warnings(recwarn), name="warnings")


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_kubernetes_extensions_directory(output_dir: Path, recwarn: pytest.WarningsRecorder, option_set: str) -> None:
    """Read the extensions in every document of a directory input and in the documents they reference.

    ``Port`` of ``common.yaml``, which ``gadget.yaml`` references, is an integer or a string; ``Gadget``, whose
    x-kubernetes-group-version-kind lists two versions, gets only its kind, and its apiVersion keeps its enum;
    a kind whose default is the kind takes the Literal; ``ConfigMapRef``, whose entry names no group, is not read;
    ``plain.yaml``, which has no extension, is generated as without the option.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "kubernetes_extensions",
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            *OPTION_SETS[option_set],
        ],
    )
    assert_output(_recorded_warnings(recwarn), name="warnings")


def test_kubernetes_extensions_referenced_document(output_file: Path) -> None:
    """Read the extensions of a document that only a reference of the input loads."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "kubernetes_extensions" / "gadget.yaml",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-kubernetes-extensions",
        ],
    )


def test_kubernetes_extensions_beside_format_as_annotation(output_file: Path) -> None:
    """Read the extensions before --read-format-as-annotation leaves formats out, in the one walk of a document.

    The format int-or-string of ``timeout`` is read as the integer or string it names, ``int | str``, before the
    format reading would leave it out and type it ``str``, so the models are those of --read-kubernetes-extensions.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "kubernetes_extensions" / "gadget.yaml",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-kubernetes-extensions",
            "--read-format-as-annotation",
        ],
    )
