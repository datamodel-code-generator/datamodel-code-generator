"""Type-check generated FastAPI packages and handwritten samples with mypy, Pyright, and ty in strict modes."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Final

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope
from datamodel_code_generator.fastapi import FastAPIConfig, generate_fastapi
from datamodel_code_generator.format import Formatter

SOURCE = Path(__file__).parents[1] / "generation_platform" / "fastapi"
SAMPLES = SOURCE / "typing"
PYTHON_VERSION = "3.10"
MYPY: Final = ("uvx", "--quiet", "mypy@2.3.1")
PYRIGHT: Final = ("uvx", "--quiet", "pyright@1.1.414")
BIN = Path(sys.executable).parent
_MYPY = re.compile(r"^(\S+?):(\d+): error: .*\[([\w-]+)\]$", re.MULTILINE)
_TY = re.compile(r"^(\S+?):(\d+):\d+: error\[([\w-]+)\]", re.MULTILINE)

Found = list[tuple[str, int, str]]


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


def _mypy(root: Path, targets: list[str]) -> Found:
    completed = subprocess.run(
        [*MYPY, "--strict", "--no-incremental", "--python-executable", sys.executable, "--python-version", PYTHON_VERSION, *targets],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return [(match[1], int(match[2]), match[3]) for match in _MYPY.finditer(completed.stdout)]


def _pyright(root: Path, targets: list[str]) -> Found:
    (root / "pyrightconfig.json").write_text(
        json.dumps({
            "typeCheckingMode": "strict",
            "pythonVersion": PYTHON_VERSION,
            "include": targets,
            "extraPaths": ["."],
        }),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [*PYRIGHT, "--outputjson", "--pythonpath", sys.executable, "--project", "pyrightconfig.json"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return [
        (Path(item["file"]).relative_to(root.resolve()).as_posix(), item["range"]["start"]["line"] + 1, item["rule"])
        for item in json.loads(completed.stdout)["generalDiagnostics"]
        if item["severity"] == "error"
    ]


def _ty(root: Path, targets: list[str]) -> Found:
    completed = subprocess.run(
        [
            BIN / "ty",
            "check",
            "--python",
            sys.executable,
            "--python-version",
            PYTHON_VERSION,
            "--output-format",
            "concise",
            "--extra-search-path",
            ".",
            *targets,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return [(match[1], int(match[2]), match[3]) for match in _TY.finditer(completed.stdout)]


CHECKERS = (("mypy", _mypy), ("pyright", _pyright), ("ty", _ty))


def _checked(root: Path, targets: list[str], label: str) -> list[str]:
    lines: list[str] = []
    for name, check in CHECKERS:
        found = check(root, targets)
        lines.append(f"{name} {label}: {'clean' if not found else ''}".rstrip())
        lines.extend(f"  {file}:{line} {rule}" for file, line, rule in sorted(found))
    return lines


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
    marked = {
        index
        for index, line in enumerate((root / "applications_negative.py").read_text(encoding="utf-8").splitlines(), 1)
        if line.endswith("# error")
    }
    lines = [f"{len(marked)} marked lines", *_checked(root, ["secured", "applications.py"], "secured and applications.py")]
    for name, check in CHECKERS:
        found = check(root, ["applications_negative.py"])
        counts = Counter(line for file, line, _ in found if file == "applications_negative.py")
        lines.append(
            f"{name} applications_negative.py: {sum(counts[line] == 1 for line in marked)}/{len(marked)} marked lines"
            f" with one error, {sum(count for line, count in counts.items() if line not in marked)} elsewhere,"
            f" {len([item for item in found if item[0] != 'applications_negative.py'])} in other files"
        )
        lines.extend(f"  {line} {rule}" for file, line, rule in sorted(found) if file == "applications_negative.py")
    return "\n".join(lines) + "\n"


def fastapi_update_report(root: Path) -> str:
    """Check a user's service against the first document, then against the regenerated package of the second."""
    shutil.copyfile(SAMPLES / "updates_service.py", root / "updates_service.py")
    lines: list[str] = []
    for document in ("updates.yaml", "updates-changed.yaml"):
        _generate(root, SAMPLES / document, "shop", DataModelType.PydanticV2BaseModel)
        lines.extend((f"== {document}", *_checked(root, ["updates_service.py"], "updates_service.py")))
    return "\n".join(lines) + "\n"
