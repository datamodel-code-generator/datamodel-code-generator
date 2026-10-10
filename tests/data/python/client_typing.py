"""Type-check generated client packages and handwritten samples with mypy, Pyright, and ty in strict modes."""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope, generate
from datamodel_code_generator.format import Formatter
from tests.data.python.client_generation import SOURCE, client_options, copy_references
from tests.data.python.strict_typing import checked, marked_lines, negative

if TYPE_CHECKING:
    from pathlib import Path

SAMPLES = SOURCE / "typing"


def client_typing_report(
    root: Path,
    backend: DataModelType,
    case_name: str = "pets",
    samples: tuple[str, ...] = ("clients", "signatures", "webhooks", "protocols"),
    *,
    package_targets: tuple[str, ...] = ("pets",),
) -> str:
    """Check a case's package with the positive samples, then their negative samples line by line.

    A case whose models name an output directory writes them as the `pets_models` package.
    """
    case = json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))[case_name]
    copy_references(case, root)
    model = dict(case.get("model", {}))
    modular = model.pop("output", None) is not None
    generate(
        shutil.copy2(SOURCE / case["input"], root / "api.yaml"),
        config=GenerateConfig(
            output=root / ("pets_models" if modular else "pets_models.py"),
            input_file_type="openapi",
            target_python_version="3.11",
            openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
            output_model_type=backend,
            **{"formatters": [Formatter.BUILTIN], **model},
            **client_options(case.get("config", {}), root, "pets"),
        ),
    )
    for sample in samples:
        for name in (f"{sample}.py", f"{sample}_negative.py"):
            shutil.copyfile(SAMPLES / name, root / name)
    positives = [f"{sample}.py" for sample in samples]
    targets = [*package_targets, *positives]
    lines = [*checked(root, targets, " and ".join(targets))]
    for sample in samples:
        marked = marked_lines(root / (name := f"{sample}_negative.py"))
        lines.extend((f"{name} {len(marked)} marked lines", *negative(root, name, marked)))
    return "\n".join(lines) + "\n"


def _generate_pets(source: Path, root: Path) -> None:
    """Generate the Pydantic v2 `pets` package of one API version under a root, replacing the earlier version."""
    generate(
        shutil.copy2(source, root / "api.yaml"),
        config=GenerateConfig(
            output=root / "pets_models.py",
            input_file_type="openapi",
            target_python_version="3.11",
            openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
            output_model_type=DataModelType.PydanticV2BaseModel,
            formatters=[Formatter.BUILTIN],
            **client_options({}, root, "pets"),
        ),
    )


def client_regenerated_typing_report(root: Path) -> str:
    """Check user code against the package of an API, then report what each checker finds once a later version is out.

    The later version removes an operation the user code calls and renames a parameter it passes.
    """
    source = SOURCE / "regeneration"
    _generate_pets(source / "v1.yaml", root)
    shutil.copyfile(source / "extensions.py", root / "extensions.py")
    lines = checked(root, ["pets", "extensions.py"], "v1 pets and extensions.py")
    _generate_pets(source / "v2.yaml", root)
    lines.extend(checked(root, ["extensions.py"], "v2 extensions.py"))
    return "\n".join(lines) + "\n"
