"""Check server generation across interpreters, checkouts, and working directories, reporting what each run did."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from datamodel_code_generator import DataModelType, OpenAPIScope, generate
from datamodel_code_generator.format import Formatter
from tests.data.python.fastapi_generation import SOURCE

if TYPE_CHECKING:
    import pytest

_SCOPE = Path(__file__).with_name("fastapi_scope.py")


def fastapi_scope_report(root: Path) -> str:
    """Run server generation with unsupported settings in a fresh interpreter, reporting runs, imports, and files."""
    result = subprocess.run(
        [sys.executable, str(_SCOPE), str(root)], capture_output=True, text=True, check=True, cwd=root
    )
    data = json.loads(result.stdout.splitlines()[-1])
    lines = [f"ordinary run without a target: {data['ordinary']}", *data["runs"]]
    return "\n".join([*lines, f"imported {data['imported']}", f"written {data['written']}"]) + "\n"


def _files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _compare(label: str, first: Path, second: Path) -> str:
    before, after = _files(first), _files(second)
    differences = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    return f"{label}: {len(after)} files, {differences or 'identical'}"


def _generate(checkout: Path, cwd: Path, monkeypatch: pytest.MonkeyPatch, *, output: bool = True) -> Any:
    """Generate the checkout's server from cwd into the checkout, or return its files without an output."""
    monkeypatch.chdir(cwd)
    base = Path() if cwd == checkout else checkout
    return generate(
        base / "api.yaml",
        output=base / "models.py" if output else None,
        input_file_type="openapi",
        target_python_version="3.11",
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[Formatter.BUILTIN],
        disable_timestamp=True,
        generate_server="fastapi",
        server_output=base / "server",
        server_package="server",
        server_model_package="models",
    )


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
    lines.append(f"the same checkout from inside it returns {report}")
    shutil.copytree(root / "first", root / "moved")
    files = _generate(root / "moved", root / "moved", monkeypatch, output=False)
    written = {name: content.decode("utf-8") for name, content in _files(root / "moved").items()}
    changed = sorted(name for parts, text in files.items() if written.get(name := "/".join(parts)) != text)
    lines.append(f"a moved checkout returns {len(files)} files, {changed or 'all as written'}")
    return "\n".join(lines) + "\n"
