"""Inspect inference imports in an otherwise fresh Python process."""
import json
import sys
from pathlib import Path

from datamodel_code_generator import infer_input_type

outcome = infer_input_type(Path(sys.argv[1]).read_text()).value
print(json.dumps({
    "outcome": outcome,
    "loaded": [name for name in sys.modules if name.startswith((
        "grpc_tools", "google.protobuf", "datamodel_code_generator.parser.", "datamodel_code_generator.model."
    ))],
}, indent=2))
