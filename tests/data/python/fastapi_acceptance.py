"""Check server generation across interpreters, checkouts, and working directories, reporting what each run did."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope
from datamodel_code_generator.fastapi import (
    FastAPIConfig,
    GeneratedProject,
    GenerationReport,
    generate_fastapi,
    render_fastapi,
)
from datamodel_code_generator.format import Formatter
from tests.data.python.fastapi_generation import SOURCE

if TYPE_CHECKING:
    import pytest

_SCOPE = Path(__file__).with_name("fastapi_scope.py")
_LOCKS = ".dcg-api-state"


def fastapi_scope_report(root: Path) -> str:
    """Run the entry points with unsupported settings in a fresh interpreter, reporting runs, imports, and files."""
    result = subprocess.run(
        [sys.executable, str(_SCOPE), str(root)], capture_output=True, text=True, check=True, cwd=root
    )
    data = json.loads(result.stdout.splitlines()[-1])
    lines = [f"ordinary run without a target: {data['ordinary']}", *data["runs"]]
    return "\n".join([*lines, f"imported {data['imported']}", f"written {data['written']}"]) + "\n"


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and _LOCKS not in path.relative_to(root).parts
    }


def _compare(label: str, first: Path, second: Path) -> str:
    before, after = _files(first), _files(second)
    differences = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    return f"{label}: {len(after)} files, {differences or 'identical'}"


def _settings(checkout: Path, cwd: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, GenerateConfig, FastAPIConfig]:
    monkeypatch.chdir(cwd)
    base = Path() if cwd == checkout else checkout
    model = GenerateConfig(
        output=base / "models.py",
        input_file_type="openapi",
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[Formatter.BUILTIN],
    )
    return base / "api.yaml", model, FastAPIConfig(output=base / "server", package="server", model_package="models")


def _generate(checkout: Path, cwd: Path, monkeypatch: pytest.MonkeyPatch) -> GenerationReport:
    source, model, config = _settings(checkout, cwd, monkeypatch)
    return generate_fastapi(source, model_config=model, config=config)


def _render(checkout: Path, cwd: Path, monkeypatch: pytest.MonkeyPatch) -> GeneratedProject:
    source, model, config = _settings(checkout, cwd, monkeypatch)
    return render_fastapi(source, model_config=model, config=config)


def fastapi_checkout_report(root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Generate one document in several checkouts and directories, comparing every file each run leaves."""
    root = root.resolve()
    for name in ("first", "second", "absolute"):
        (root / name).mkdir()
        shutil.copy2(SOURCE / "pets.yaml", root / name / "api.yaml")
    _generate(root / "first", root / "first", monkeypatch)
    _generate(root / "second", root / "second", monkeypatch)
    _generate(root / "absolute", root, monkeypatch)
    lines = [
        _compare("another checkout", root / "first", root / "second"),
        _compare("absolute paths from another directory", root / "first", root / "absolute"),
    ]
    report = _generate(root / "absolute", root / "absolute", monkeypatch)
    lines.append(f"the same checkout from inside it writes {len(report.written_files)} files")
    shutil.copytree(root / "first", root / "moved")
    project = _render(root / "moved", root / "moved", monkeypatch)
    lines.append(f"a moved checkout renders {sorted({artifact.action for artifact in project.artifacts})}")
    return "\n".join(lines) + "\n"
