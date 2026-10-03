"""Observe the existing shared inheritance DAG through public generation and native APIs."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from tests.data.python.allof_model_inheritance import generation_row, module_from_output, native_value


def shared_join_report(tmp_path: Path, run_cli: Any) -> str:
    """Record LF text digests and native behavior while multiple joins share an ancestor."""
    fixture = Path("tests/data/msgspec_inheritance/jsonschema_redundant_dag.json")
    row = {
        "entry": "api",
        "input": "jsonschema",
        "backend": "pydantic_v2.BaseModel",
        "options": {"target_python_version": "3.10"},
    }
    output = tmp_path / "models.py"
    report = {
        "fixture_text_sha256": hashlib.sha256(fixture.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
        "recipe": row,
        "generation": generation_row(row, fixture, output, run_cli),
        "source": output.read_text(encoding="utf-8"),
    }
    try:
        module = module_from_output(output)
        cases = json.loads((fixture.parent / "cases.json").read_text(encoding="utf-8"))
        payload = next(case["payload"] for case in cases if case["name"] == fixture.stem)
        instance, value = native_value({"backend": row["backend"], "payload": payload}, module, module.Payload)
        report["native"] = {
            "constructed_type": type(instance).__name__,
            "mro": [base.__name__ for base in module.Payload.__mro__],
            "fields": list(module.Payload.model_fields),
            "serialized": value,
            "serialized_order": list(value),
        }
    finally:
        sys.modules.pop("allof_models", None)
    return json.dumps(report, indent=2) + "\n"
