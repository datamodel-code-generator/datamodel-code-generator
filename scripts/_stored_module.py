"""Payload verdicts of a stored module's own models, for the stored-module proof of scripts/behaviour_diff.py.

Usage:
  python -E -m scripts._stored_module <model.py | package dir> <output model type> <out.json> [values.json]

Run in a fresh process per side, from the repository root. Without a file of values it writes the module's public
models (`models`) and, per model, `STORED_DRAWS` distinct values drawn (derandomised) from the model's own JSON
Schema (`values`: pydantic's `TypeAdapter.json_schema()`, or `msgspec.json.schema` for msgspec Structs, through
`hypothesis_jsonschema`); a model whose schema cannot be built or drawn from has none. With a file of values per model
it writes the verdict and the parsed value's fingerprint of each one (`verdicts`), as the behaviour signature takes
them (`_behaviour_signature.model_verdicts`).
"""

from __future__ import annotations

import contextlib
import json
import signal
import sys
from pathlib import Path

import msgspec
import pydantic
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema

from scripts._behaviour_signature import model_types, model_verdicts

STORED_DRAWS = 10
TIMEOUT = 90
"""Seconds one side may take for its draws or its verdicts."""
GIVEN = 4
"""The arguments before the optional file of values to judge."""


def drawn(target: type, output_model_type: str) -> list[object]:
    """Return `STORED_DRAWS` distinct values drawn from a model's own JSON Schema; none where it has none."""
    strategy = None
    with contextlib.suppress(Exception):
        schema = (
            msgspec.json.schema(target)
            if output_model_type == "msgspec.Struct"
            else pydantic.TypeAdapter(target).json_schema()
        )
        strategy = from_schema(schema)
    if strategy is None:
        return []
    found: dict[str, object] = {}

    @settings(
        database=None,
        deadline=None,
        derandomize=True,
        max_examples=STORED_DRAWS,
        phases=[Phase.generate],
        suppress_health_check=list(HealthCheck),
    )
    @given(data=st.data())
    def collect(data: st.DataObject) -> None:
        value = data.draw(strategy)
        found.setdefault(json.dumps(value, sort_keys=True), value)

    with contextlib.suppress(Exception):
        collect()
    return list(found.values())


def main(argv: list[str]) -> int:
    """Write the draws, or the verdicts of the given values, of the stored module named on the command line."""
    sys.dont_write_bytecode = True
    signal.alarm(TIMEOUT)
    target, output_model_type, out = Path(argv[0]), argv[1], Path(argv[2])
    models = model_types(target)
    result: dict[str, object]
    if len(argv) < GIVEN:
        result = {
            "models": sorted(models),
            "values": {key: values for key, model in models.items() if (values := drawn(model, output_model_type))},
        }
    else:
        verdicts: dict[str, object] = {}
        for key, values in json.loads(Path(argv[3]).read_text(encoding="utf-8")).items():
            with contextlib.suppress(Exception):
                verdicts[key] = model_verdicts(models[key], output_model_type, values)
        result = {"verdicts": verdicts}
    out.write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
