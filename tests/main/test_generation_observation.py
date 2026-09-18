"""Observe existing generation without replacing parser methods or getters."""

from __future__ import annotations

import gc
import hashlib
import json
import sys
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType, InputFileType, generate
from tests.conftest import assert_inputs_not_mutated, assert_output
from tests.data.python.generation_observer import GenerationObserver

DATA = Path(__file__).parents[1] / "data"
EXPECTED = DATA / "expected/main/generation_platform"


@pytest.mark.parametrize("backend", list(DataModelType))
def test_generation_observation(backend: DataModelType, tmp_path: Path) -> None:
    """Freeze bytes, actual engine calls, input immutability, and graph disposal."""
    source = json.loads((DATA / "generation_platform/observation.json").read_text())
    observer = GenerationObserver()
    imports_before = frozenset(sys.modules)
    previous = sys.getprofile()
    try:
        sys.setprofile(observer.record)
        with assert_inputs_not_mutated({"source": source}):
            result = generate(
                source,
                input_file_type=InputFileType.OpenAPI,
                output=tmp_path / "models.py",
                output_model_type=backend,
                disable_timestamp=True,
                formatters=[],
                openapi_scopes=["schemas", "paths"],
                read_only_write_only_model_type="all",
                collapse_root_models=True,
                reuse_model=True,
            )
    finally:
        sys.setprofile(previous)
    gc.collect()
    observation = {
        "calls": dict(sorted(observer.calls.items())),
        "phases": observer.phases,
        "result": result,
        "retained_parsers": sum(reference() is not None for reference in observer.references),
        "artifacts": {
            path.relative_to(tmp_path).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(tmp_path.rglob("*"))
            if path.is_file()
        },
        "target_imports": sorted(
            name
            for name in sys.modules
            if name not in imports_before
            and name.startswith((
                "datamodel_code_generator._generation_contract",
                "datamodel_code_generator._openapi_generation",
                "datamodel_code_generator.parser.openapi_contract",
                "datamodel_code_generator.fastapi",
                "datamodel_code_generator.client",
            ))
        ),
    }
    expected = EXPECTED / ("windows" if sys.platform == "win32" else ".") / f"{backend.name}.txt"
    assert_output(json.dumps(observation, indent=2) + "\n", expected)
