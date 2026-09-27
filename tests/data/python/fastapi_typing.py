"""Type-check generated FastAPI packages and handwritten samples with mypy, Pyright, and ty in strict modes."""

from __future__ import annotations

import shutil
from pathlib import Path

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope
from datamodel_code_generator.fastapi import FastAPIConfig, generate_fastapi
from datamodel_code_generator.format import Formatter
from tests.data.python.strict_typing import checked, marked_lines, negative

SOURCE = Path(__file__).parents[1] / "generation_platform" / "fastapi"
SAMPLES = SOURCE / "typing"


def _generate(root: Path, source: Path, package: str, backend: DataModelType, **settings: object) -> None:
    shutil.copy2(source, root / "api.yaml")
    generate_fastapi(
        root / "api.yaml",
        model_config=GenerateConfig(
            output=root / f"{package}_models.py",
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
            output_model_type=backend,
            formatters=[Formatter.BUILTIN],
        ),
        config=FastAPIConfig(
            output=root / package, package=package, model_package=f"{package}_models", **settings
        ),
    )


def fastapi_typing_report(root: Path, backend: DataModelType) -> str:
    """Check the secured package with the application sample, then the negative sample line by line."""
    _generate(
        root,
        SOURCE / "server-security.yaml",
        "secured",
        backend,
        handler_modes={"/paths/~1maybe/get": "async", "/paths/~1custom/get": "async"},
    )
    for sample in ("applications", "applications_negative"):
        shutil.copyfile(SAMPLES / f"{sample}.py", root / f"{sample}.py")
    marked = marked_lines(root / "applications_negative.py")
    lines = [
        f"{len(marked)} marked lines",
        *checked(root, ["secured", "applications.py"], "secured and applications.py"),
        *negative(root, "applications_negative.py", marked),
    ]
    return "\n".join(lines) + "\n"


def fastapi_update_report(root: Path) -> str:
    """Check a user's service against the first document, then against the regenerated package of the second."""
    shutil.copyfile(SAMPLES / "updates_service.py", root / "updates_service.py")
    lines: list[str] = []
    for document in ("updates.yaml", "updates-changed.yaml"):
        _generate(root, SAMPLES / document, "shop", DataModelType.PydanticV2BaseModel)
        lines.extend((f"== {document}", *checked(root, ["updates_service.py"], "updates_service.py")))
    return "\n".join(lines) + "\n"
