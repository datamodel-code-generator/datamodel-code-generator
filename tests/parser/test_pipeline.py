"""The passes of ``Parser.parse()``: which run, in which order, for a few inputs that reach most of them.

A trace lists every pass that runs, named as in ``parser/_pass_decls.py``, with a ``-- module`` line where the pipeline
starts processing each output module; ``scripts/check_pass_pipeline.py`` records it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from datamodel_code_generator.format import Formatter
from datamodel_code_generator.model.pydantic_v2 import BaseModel
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from scripts import check_pass_pipeline
from tests.conftest import assert_output

DATA = Path(__file__).parents[1] / "data"

GOLDEN_RUNS = {
    "jsonschema": "--input tests/data/jsonschema/person.json --input-file-type jsonschema",
    "openapi-modules": (
        "--input tests/data/openapi/modular.yaml --input-file-type openapi --collapse-root-models --reuse-model "
        "--use-annotated --aliases tests/data/openapi/aliases.json --use-default-factory-for-optional-nested-models "
        "--use-root-model-sequence-interface --generate-schema-validators"
    ),
    "msgspec-tree-reuse": (
        "--input tests/data/openapi/discriminator.yaml --input-file-type openapi --output-model-type msgspec.Struct "
        "--collapse-root-models --reuse-model --reuse-scope tree"
    ),
    "typed-dict-import-overrides": (
        "--input tests/data/jsonschema/datetime.json --input-file-type jsonschema --output-model-type typing.TypedDict "
        "--target-python-version 3.10 "
        """--import-overrides '{"TypedDict": "my_project.typing_compat", "NotRequired": "my_project.typing_compat"}'"""
    ),
    "renamed-sequence-roots": (
        "--input tests/data/jsonschema/root_sequence_final/reuse.json --input-file-type jsonschema "
        "--use-root-model-sequence-interface --reuse-model --collapse-reuse-models --generate-schema-validators"
    ),
}
COVERAGE_RUNS = (
    (
        "--input tests/data/jsonschema/person.json --input-file-type jsonschema "
        "--custom-template-dir tests/data/templates --emit-model-metadata {tmp}/metadata.json"
    ),
    (
        "--input tests/data/jsonschema/type_overrides_target.json --input-file-type jsonschema "
        "--output-model-type typing.TypedDict --target-python-version 3.10 "
        """--type-overrides '{"Model.c": "typing.LiteralString", "Model.missing": "enum.StrEnum"}'"""
    ),
    (
        "--input tests/data/jsonschema/typed_dict_allof_extra_items.json --input-file-type jsonschema "
        "--output-model-type typing.TypedDict --target-python-version 3.10"
    ),
    (
        "--input tests/data/openapi/nullable_map_value_types.yaml --input-file-type openapi "
        "--use-typed-nullable-map-values"
    ),
    (
        "--input tests/data/jsonschema/serialized_decimal_default_collision.json --input-file-type jsonschema "
        "--deserialize-default-values decimal"
    ),
)
"""Runs that reach the passes no golden run reaches, each under the condition it names."""


class OutsideModel(BaseModel):
    """A model class defined outside the package, whose imports a module cannot keep cached."""


@pytest.mark.parametrize("arguments", GOLDEN_RUNS.values(), ids=GOLDEN_RUNS)
def test_pipeline_trace_matches_golden(arguments: str) -> None:
    """The passes run in the stored order, under the conditions each run sets."""
    assert_output(check_pass_pipeline.pass_trace(f"{arguments} --disable-timestamp"))


def test_every_declared_pass_runs() -> None:
    """Some golden or coverage run runs each declared pass, so that every table entry is executed."""
    traces = [check_pass_pipeline.pass_trace(arguments) for arguments in [*GOLDEN_RUNS.values(), *COVERAGE_RUNS]]
    outside = JsonSchemaParser(
        DATA / "jsonschema" / "person.json", data_model_type=OutsideModel, formatters=[Formatter.BUILTIN]
    )
    traces.append("\n".join(check_pass_pipeline.call_trace(outside.parse)))

    assert_output("".join(f"{name}\n" for name in check_pass_pipeline.unrun_passes(traces)) or "every pass runs\n")
