"""Type-check generated client packages and handwritten samples with mypy, Pyright, and ty in strict modes."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import warnings
from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType, GenerateConfig, LeftoverFilesWarning, OpenAPIScope, generate
from datamodel_code_generator.format import Formatter
from tests.api_generation.support.backends import backend_settings
from tests.api_generation.support.client_generation import SOURCE, client_options, copy_references, generator_warnings
from tests.api_generation.support.client_publication import PACKAGE
from tests.api_generation.support.strict_typing import checked, marked_lines, negative

if TYPE_CHECKING:
    from pathlib import Path

SAMPLES = SOURCE / "typing"
_MODEL_LIBRARIES = (
    "from importlib.util import find_spec; print(*(name for name in ('pydantic', 'msgspec') if find_spec(name)))"
)


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
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", UserWarning)
        generate(
            shutil.copy2(SOURCE / case["input"], root / "api.yaml"),
            config=GenerateConfig(
                output=root / ("pets_models" if modular else "pets_models.py"),
                input_file_type="openapi",
                target_python_version="3.11",
                openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
                **{**backend_settings(backend), "formatters": [Formatter.BUILTIN], **model},
                **client_options(case.get("config", {}), root, "pets"),
            ),
        )
    for sample in samples:
        for name in (f"{sample}.py", f"{sample}_negative.py"):
            shutil.copyfile(SAMPLES / name, root / name)
    positives = [f"{sample}.py" for sample in samples]
    targets = [*package_targets, *positives]
    lines = [*generator_warnings(caught), *checked(root, targets, " and ".join(targets))]
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

    The later version removes an operation the user code calls and renames a parameter it passes, and its generation
    warns about the modules of the removed resource that it leaves in place.
    """
    source = SOURCE / "regeneration"
    _generate_pets(source / "v1.yaml", root)
    shutil.copyfile(source / "extensions.py", root / "extensions.py")
    lines = checked(root, ["pets", "extensions.py"], "v1 pets and extensions.py")
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", LeftoverFilesWarning)
        _generate_pets(source / "v2.yaml", root)
    lines.extend(f"{item.category.__name__}: {item.message}" for item in recorded)
    lines.extend(checked(root, ["extensions.py"], "v2 extensions.py"))
    return "\n".join(lines).replace(root.resolve().as_posix(), "<root>") + "\n"


def client_dependency_typing_report(project: Path, environment: Path, stderr: str) -> str:
    """Check a generated package with an interpreter that holds only the dependencies its generation printed.

    The dependencies are installed at their printed minimums into a new environment, whose model libraries the report
    names before the checkers' findings over the package and its models.
    """
    added = next(line for line in stderr.splitlines() if line.lstrip().startswith("uv add "))
    dependencies = shlex.split(added)[2:]
    subprocess.run(["uv", "venv", "--quiet", "--python", sys.executable, environment], check=True)
    python = environment / ("Scripts" if os.name == "nt" else "bin") / "python"
    install = ["uv", "pip", "install", "--quiet", "--python", python, "--resolution", "lowest-direct", *dependencies]
    subprocess.run(install, check=True)
    held = subprocess.run([python, "-I", "-c", _MODEL_LIBRARIES], check=True, capture_output=True, text=True)
    targets = [PACKAGE, f"{PACKAGE}_models.py"]
    return "\n".join([
        f"dependencies {' '.join(dependencies)}",
        f"installed model libraries {held.stdout.strip() or '-'}",
        *checked(project, targets, " and ".join(targets), python),
        "",
    ])
