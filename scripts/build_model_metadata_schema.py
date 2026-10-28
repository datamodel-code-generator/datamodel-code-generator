"""Build the static JSON Schema for --emit-model-metadata output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from datamodel_code_generator.model_metadata import JSON_SCHEMA_DRAFT_2020_12, ModelMetadata

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "src" / "datamodel_code_generator" / "resources" / "model_metadata.schema.json"


def build_model_metadata_schema() -> dict[str, Any]:
    """Generate the JSON Schema from the runtime TypedDict contract."""
    return {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        **TypeAdapter(ModelMetadata).json_schema(),
    }


def _dump_schema(schema: dict[str, Any]) -> str:
    return f"{json.dumps(schema, indent=2, ensure_ascii=False)}\n"


def main() -> int:
    """Run the schema builder."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the static schema file is out of date.")
    args = parser.parse_args()

    expected = _dump_schema(build_model_metadata_schema())
    if args.check:
        if not OUTPUT_PATH.exists() or OUTPUT_PATH.read_text(encoding="utf-8") != expected:
            print(f"{OUTPUT_PATH} is out of date. Run scripts/build_model_metadata_schema.py.", flush=True)
            return 1
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
