"""Ordinary inputs that reach branches no other test reaches."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import Error, GenerateConfig, generate_dynamic_models, generate_dynamic_module
from tests.main.conftest import run_main_with_args

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.mark.parametrize("dynamic_api", [generate_dynamic_models, generate_dynamic_module])
def test_dynamic_api_refuses_a_config_that_writes_a_file(dynamic_api: Callable[..., object], tmp_path: Path) -> None:
    """A config with an output file makes generate() write it and return no code, which the dynamic API refuses."""
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    with pytest.raises(Error, match=r"generate\(\) returned None"):
        dynamic_api(schema, config=GenerateConfig(output=tmp_path / "model.py"), cache_size=0)


@pytest.mark.parametrize("ref_strategy", ["reuse-all", "reuse-foreign"])
def test_recursive_input_model_is_reused_from_its_own_definitions(
    ref_strategy: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A recursive Pydantic model is in the definitions of its own schema, so the reuse strategies import it."""
    run_main_with_args(
        [
            "--input-model",
            "tests.data.python.input_model.pydantic_models:RecursiveNode",
            "--input-model-ref-strategy",
            ref_strategy,
        ],
        capsys=capsys,
        derived_stdout=True,
    )
