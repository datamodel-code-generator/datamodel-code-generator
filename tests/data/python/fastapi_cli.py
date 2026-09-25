"""Run the FastAPI target from the command line and the public entry points, reporting exits, output, and files."""

from __future__ import annotations

import io
import json
import shutil
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING, get_type_hints

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope
from datamodel_code_generator.__main__ import main
from datamodel_code_generator.fastapi import (
    APIGenerationError,
    FastAPIConfig,
    GeneratedProject,
    GenerationInput,
    GenerationReport,
    generate_fastapi,
    render_fastapi,
)
from datamodel_code_generator.format import Formatter
from tests.data.python.fastapi_generation import SOURCE

if TYPE_CHECKING:
    import pytest

_MODEL = [
    "--output",
    "models.py",
    "--input-file-type",
    "openapi",
    "--openapi-scopes",
    "schemas",
    "api",
    "--output-model-type",
    "pydantic_v2.BaseModel",
    "--formatters",
    "builtin",
    "--disable-timestamp",
]
_TARGET = ["--generate-server", "fastapi", "--target-config", "fastapi.toml"]
_ARGUMENTS = {
    "cli": ["--input", "api.yaml", *_MODEL, *_TARGET],
    "model": ["--input", "api.yaml", *_MODEL],
    "bare": _MODEL,
}
_TOML = ["schema_version = 1", 'package = "server"', 'model_package = "models"', 'output = "server"']
_HIDDEN = frozenset({"_runtime", ".dcg-state", ".dcg-api-state"})


def _cli(args: list[str], stdin: str | None, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    out, err = io.StringIO(), io.StringIO()
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    with redirect_stdout(out), redirect_stderr(err):
        code = main(args)
    return [
        f"  exit {int(code)}",
        *(f"  stderr | {line}" for line in err.getvalue().splitlines() if not line.startswith(" ")),
        *(f"  stdout | {line}" for line in out.getvalue().splitlines()),
    ]


def _api(entry: str, root: Path) -> list[str]:
    if entry == "hints":
        hints = {"input_": GenerationInput, "model_config": GenerateConfig, "config": FastAPIConfig}
        entries = ((generate_fastapi, GenerationReport), (render_fastapi, GeneratedProject))
        return [
            f"  {function.__name__} resolves {sorted(hints)}: {get_type_hints(function) == {**hints, 'return': result}}"
            for function, result in entries
        ]
    model = GenerateConfig(
        output=Path("models.py"),
        input_file_type="openapi",
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
        output_model_type=DataModelType.PydanticV2BaseModel,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
    )
    config = FastAPIConfig(output=Path("server"), package="server", model_package="models")
    try:
        if entry == "render":
            project = render_fastapi(root / "api.yaml", model_config=model, config=config)
            return [
                f"  {artifact.action} {artifact.path.as_posix()}"
                for artifact in project.artifacts
                if _shown(artifact.path)
            ]
        report = generate_fastapi(root / "api.yaml", model_config=model, config=config)
    except APIGenerationError as error:
        return [f"  {item.code} {item.artifact_path}: {item.message}" for item in error.diagnostics]
    return [f"  written {record.path.as_posix()}" for record in report.written_files if _shown(record.path)]


def _shown(path: Path) -> bool:
    return not _HIDDEN & set(path.parts)


def _failing(*_args: object) -> None:
    msg = "The renderer stopped"
    raise RuntimeError(msg)


class _ReadOnlyPath(type(Path())):
    def write_text(self, *_args: object, **_kwargs: object) -> int:
        raise PermissionError(13, "Permission denied")


_BREAKS = {
    "render": ("datamodel_code_generator._fastapi.target.FastAPITarget.render", _failing),
    "write": ("datamodel_code_generator._target_cli.Path", _ReadOnlyPath),
}


def fastapi_cli_report(case_name: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Replay one command-line or entry-point case in its own directory, reporting each run and the files."""
    root = root.resolve()
    monkeypatch.chdir(root)
    (root / "fastapi.toml").write_text("\n".join(_TOML) + "\n", encoding="utf-8")
    lines = [f"# {case_name}"]
    for step in json.loads((SOURCE / "cli.json").read_text(encoding="utf-8"))[case_name]:
        ((action, value),) = step.items()
        match action, value:
            case "spec", [str() as name, str() as fixture]:
                shutil.copy2(SOURCE / fixture, root / name)
                lines.append(f"spec {name} <- {fixture}")
            case "toml", list() as settings:
                (root / "fastapi.toml").write_text("\n".join([*_TOML, *settings]) + "\n", encoding="utf-8")
                lines.extend(("toml", *(f"  | {line}" for line in settings)))
            case "write", [str() as name, str() as text]:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(text, encoding="utf-8")
                lines.append(f"write {name}")
            case "remove", str() as name:
                (root / name).unlink()
                lines.append(f"remove {name}")
            case "cli" | "model" | "bare", list() as extra:
                lines.append(" ".join((action, *extra)))
                lines.extend(_cli([*_ARGUMENTS[action], *extra], None, monkeypatch))
            case "stdin", [str() as name, list() as extra]:
                lines.append(" ".join(("stdin", name, *extra)))
                lines.extend(_cli([*_MODEL, *_TARGET, *extra], (root / name).read_text(encoding="utf-8"), monkeypatch))
            case "api", str() as entry:
                lines.append(f"api {entry}")
                lines.extend(_api(entry, root))
            case "break", str() as name:
                monkeypatch.setattr(*_BREAKS[name])
                lines.append(f"break {name}")
            case "show", str() as name:
                lines.append(f"show {name}")
                lines.extend(f"  | {line}" for line in (root / name).read_text(encoding="utf-8").splitlines())
            case "tree", None:
                lines.append("tree")
                lines.extend(
                    sorted(
                        f"  {path.relative_to(root).as_posix()}"
                        for path in root.rglob("*")
                        if path.is_file() and _shown(path.relative_to(root))
                    )
                )
            case _:
                raise AssertionError(step)
    return "\n".join(lines).replace(root.as_posix(), "<root>") + "\n"
