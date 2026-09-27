"""Type-check generated client packages and handwritten samples with mypy, Pyright, and ty in strict modes."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope
from datamodel_code_generator._api_generation import generate_target
from datamodel_code_generator._client.target import ClientTarget
from datamodel_code_generator.format import Formatter
from tests.data.python.client_generation import SOURCE, client_config
from tests.data.python.strict_typing import checked, marked_lines, negative

SAMPLES = SOURCE / "typing"


def client_typing_report(root: Path, backend: DataModelType) -> str:
    """Check the pets package with the positive sample, then the negative sample line by line."""
    case = json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))["pets"]
    generate_target(
        shutil.copy2(SOURCE / case["input"], root / "api.yaml"),
        model_config=GenerateConfig(
            output=root / "pets_models.py",
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
            output_model_type=backend,
            formatters=[Formatter.BUILTIN],
        ),
        config=client_config(
            {"output": "pets", "package": "pets", "model_package": "pets_models", **case["config"]}, root
        ),
        generator=ClientTarget(),
    )
    for sample in ("clients", "clients_negative"):
        shutil.copyfile(SAMPLES / f"{sample}.py", root / f"{sample}.py")
    marked = marked_lines(root / "clients_negative.py")
    lines = [
        f"{len(marked)} marked lines",
        *checked(root, ["pets", "clients.py"], "pets and clients.py"),
        *negative(root, "clients_negative.py", marked),
    ]
    return "\n".join(lines) + "\n"
