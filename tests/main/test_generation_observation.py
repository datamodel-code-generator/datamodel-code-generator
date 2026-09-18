"""Observe existing generation without replacing parser methods or getters."""

from __future__ import annotations

import gc
import hashlib
import json
import sys
import weakref
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import DataModelType, InputFileType, generate
from tests.conftest import assert_inputs_not_mutated, assert_output

if TYPE_CHECKING:
    from types import FrameType

DATA = Path(__file__).parents[1] / "data"
EXPECTED = DATA / "expected/main/generation_platform"
OBSERVED = frozenset({
    "_copy_data_model_field",
    "_copy_data_type",
    "_copy_resolved_inherited_field",
    "resolve_ref",
    "add_ref",
    "_validate_schema_object",
    "_get_ref_raw_schema",
    "_load_ref_schema_object",
    "_cache_ref_data_type_facts",
    "_generate_module_output",
})
PHASES = frozenset({"_build_generation_parser", "_parse_with_disposal", "_emit_generation"})


@pytest.mark.parametrize("backend", list(DataModelType))
def test_generation_observation(backend: DataModelType, tmp_path: Path) -> None:
    """Freeze bytes, actual engine calls, input immutability, and graph disposal."""
    source = json.loads((DATA / "generation_platform/observation.json").read_text())
    calls: Counter[str] = Counter()
    phases: list[str] = []
    references: list[weakref.ReferenceType[object]] = []

    def observe(frame: FrameType, event: str, arg: object) -> None:
        if not frame.f_globals.get("__name__", "").startswith("datamodel_code_generator"):
            return
        name = frame.f_code.co_name
        if event == "call":
            if name in OBSERVED:
                calls[name] += 1
            if name in PHASES:
                phases.append(name)
        elif event == "return" and name == "_build_generation_parser":
            references.append(weakref.ref(arg))

    imports_before = frozenset(sys.modules)
    previous = sys.getprofile()
    try:
        sys.setprofile(observe)
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
        "calls": dict(sorted(calls.items())),
        "phases": phases,
        "result": result,
        "retained_parsers": sum(reference() is not None for reference in references),
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
    assert_output(json.dumps(observation, indent=2) + "\n", EXPECTED / f"{backend.name}.txt")
