"""Type-check generated FastAPI packages and handwritten samples with mypy, Pyright, and ty in strict modes."""

from __future__ import annotations

import shutil
from pathlib import Path

from datamodel_code_generator import DataModelType, OpenAPIScope, generate
from datamodel_code_generator.format import Formatter
from tests.api_generation.support.backends import backend_settings
from tests.api_generation.support.strict_typing import checked, marked_lines, negative

SOURCE = Path(__file__).parents[2] / "data" / "generation_platform" / "fastapi"
SAMPLES = SOURCE / "typing"


def _generate(root: Path, source: Path, package: str, backend: DataModelType, **settings: object) -> None:
    shutil.copy2(source, root / "api.yaml")
    generate(
        root / "api.yaml",
        output=root / f"{package}_models.py",
        input_file_type="openapi",
        target_python_version="3.11",
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
        formatters=[Formatter.BUILTIN],
        generate_server="fastapi",
        server_output=root / package,
        server_package=package,
        server_model_package=f"{package}_models",
        **backend_settings(backend),
        **settings,
    )


def fastapi_typing_report(root: Path, backend: DataModelType) -> str:
    """Check the secured package with the application sample, then the negative sample line by line."""
    _generate(
        root,
        SOURCE / "server-security.yaml",
        "secured",
        backend,
        server_handler_modes={"/paths/~1maybe/get": "async", "/paths/~1custom/get": "async"},
    )
    return _samples_report(root, "secured", "applications")


def fastapi_missing_report(root: Path) -> str:
    """Check a service of Pydantic dataclasses that reads unset request members and leaves response members unset."""
    _generate(root, SOURCE.parent / "client" / "missing-sentinel.yaml", "pets", DataModelType.PydanticV2Dataclass)
    return _samples_report(root, "pets", "missing_service")


def _samples_report(root: Path, package: str, sample: str) -> str:
    """Check a package, with its entrypoint and the service stubs generation wrote, and a positive sample.

    Then check the negative sample line by line.
    """
    for name in (sample, f"{sample}_negative"):
        shutil.copyfile(SAMPLES / f"{name}.py", root / f"{name}.py")
    marked = marked_lines(root / f"{sample}_negative.py")
    lines = [
        f"{len(marked)} marked lines",
        *checked(root, [package, "services", f"{sample}.py"], f"{package}, services, and {sample}.py"),
        *negative(root, f"{sample}_negative.py", marked),
    ]
    return "\n".join(lines) + "\n"


def fastapi_bodies_report(root: Path) -> str:
    """Check the bodies package, whose copied runtime reads requests through adapters, form fields and JSON parts."""
    _generate(root, SOURCE / "server-bodies.yaml", "bodies", DataModelType.PydanticV2BaseModel)
    return "\n".join(checked(root, ["bodies"], "bodies")) + "\n"


def fastapi_parameters_report(root: Path) -> str:
    """Check the parameters package, whose routes with renamed path placeholders register `wire_named` route classes."""
    _generate(root, SOURCE / "server-parameters.yaml", "parameters", DataModelType.PydanticV2BaseModel)
    return "\n".join(checked(root, ["parameters"], "parameters")) + "\n"


def fastapi_update_report(root: Path) -> str:
    """Check a user's service against the first document, then against the regenerated package of the second."""
    shutil.copyfile(SAMPLES / "updates_service.py", root / "updates_service.py")
    lines: list[str] = []
    for document in ("updates.yaml", "updates-changed.yaml"):
        _generate(root, SAMPLES / document, "shop", DataModelType.PydanticV2BaseModel)
        lines.extend((f"== {document}", *checked(root, ["updates_service.py"], "updates_service.py")))
    return "\n".join(lines) + "\n"
