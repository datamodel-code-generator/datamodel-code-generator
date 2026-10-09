"""Replay target scenarios through the FastAPI entry points and report artifacts, files, and failures."""

from __future__ import annotations

import json
import re
import shutil
import warnings
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import yaml

from datamodel_code_generator import Error, GenerateConfig, InvalidFileFormatError
from datamodel_code_generator.fastapi import (
    APIGenerationError,
    Diagnostic,
    FastAPIConfig,
    GeneratedProject,
    OperationRef,
    ResponseChoice,
    generate_fastapi,
    render_fastapi,
)
from datamodel_code_generator.remote_lock import RemoteLockError, RemoteReferenceLock
from tests.data.python.client_generation import cyclic_input_failure

if TYPE_CHECKING:
    import pytest

SOURCE = Path(__file__).parents[1] / "generation_platform" / "targets"
_PRIVATE = re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{32}|[0-9a-f]{16})(?![0-9a-f])")


def _runtime(path: Path) -> bool:
    return "_runtime" in path.parts


def _selector(value: str | dict[str, str]) -> OperationRef | str:
    if isinstance(value, str):
        return value
    return OperationRef(**{key: item.replace("{root}", Path.cwd().as_uri()) for key, item in value.items()})


def _config(values: dict[str, Any]) -> FastAPIConfig:
    values = {"output": "server", "package": "example.server", "model_package": "example.models", **values}
    converted: dict[str, Any] = {}
    for key, value in values.items():
        match key:
            case "output" if isinstance(value, str):
                converted[key] = Path(value)
            case "primary_responses":
                converted[key] = {_selector(selector): ResponseChoice(**choice) for selector, choice in value}
            case "operation_names":
                converted[key] = {_selector(selector): name for selector, name in value}
            case _:
                converted[key] = value
    return FastAPIConfig(**converted)


def _model(values: dict[str, Any], root: Path) -> GenerateConfig:
    values = {"output": "models.py", "disable_timestamp": True, "formatters": [], **values}
    converted: dict[str, Any] = {}
    resolved = None
    for key, value in values.items():
        match key:
            case "output" | "emit_model_metadata" | "lockfile" | "custom_template_dir":
                converted[key] = None if value is None else Path(value)
            case "resolved_lock":
                resolved = value
            case _:
                converted[key] = value
    config = GenerateConfig(**{"target_python_version": "3.11", **converted})
    if resolved is not None:
        config.resolve_remote_lock(
            RemoteReferenceLock.open(root / "resolved.lock", update=resolved == "update", locked=False)
        )
    return config


def _input(value: dict[str, Any], server: str | None) -> object:
    match value:
        case {"path": str() as path}:
            return Path("spec", path)
        case {"text": str() as path}:
            return Path("spec", path).read_text(encoding="utf-8")
        case {"mapping": str() as path}:
            return yaml.safe_load(Path("spec", path).read_text(encoding="utf-8"))
        case {"list": list() as paths}:
            return [Path("spec", path) for path in paths]
        case {"directory": str() as path}:
            return Path("spec", path)
        case {"url": str() as path}:
            return urlparse(f"{server}/{path}")
        case _:
            raise ValueError(value)


def _relative(path: Path, root: Path) -> str:
    if not path.is_absolute():
        return path.as_posix()
    return (path.relative_to(root) if path.is_relative_to(root) else path.relative_to(root.resolve())).as_posix()


def _diagnostic(item: Diagnostic) -> str:
    fields = (item.stage, item.option_path, item.source_pointer, item.artifact_path)
    location = " ".join(str(field) for field in fields if field is not None)
    return f"  {item.code} {item.severity} {location}: {item.message}"


def _runtime_line(actions: list[str]) -> list[str]:
    return [f"  {len(actions)} runtime modules {sorted(set(actions))}"] if actions else []


def _report_project(project: GeneratedProject, root: Path, lines: list[str]) -> None:
    lines.append(f"  target={project.target} schema_version={project.schema_version}")
    lines.extend(
        f"  {artifact.action} {artifact.kind} {_relative(artifact.path, root)}"
        for artifact in project.artifacts
        if not _runtime(artifact.path)
    )
    lines.extend(_runtime_line([artifact.action for artifact in project.artifacts if _runtime(artifact.path)]))


def _publish(project: GeneratedProject, root: Path) -> None:
    for artifact in project.artifacts:
        if artifact.action == "write":
            (location := root / artifact.path).parent.mkdir(parents=True, exist_ok=True)
            location.write_bytes(artifact.content)


def _files(project: GeneratedProject, root: Path) -> dict[str, bytes]:
    return {_relative(artifact.path, root): artifact.content for artifact in project.artifacts}


def _run(
    case: dict[str, Any], overrides: dict[str, Any], root: Path, server: str | None, *, publish: bool
) -> GeneratedProject | str | None:
    spec = {**case, **{key: value for key, value in overrides.items() if key not in {"model", "config"}}}
    model = {**case.get("model", {}), **overrides.get("model", {})}
    config = {**case.get("config", {}), **overrides.get("config", {})}
    try:
        return (generate_fastapi if publish else render_fastapi)(
            _input(spec["input"], server), model_config=_model(model, root), config=_config(config)
        )
    except (APIGenerationError, Error, RemoteLockError, OSError, UnicodeError) as error:
        if case["input"] in ({"path": "input-cycle-dict.yaml"}, {"path": "input-cycle-reference.yaml"}):
            if not isinstance(error, (APIGenerationError, InvalidFileFormatError)):
                raise
            cycles = {
                "input-cycle-dict.yaml": ("input-cycle-dict.yaml", "/x-cycle/self", (12, 10)),
                "input-cycle-list.yaml": ("input-cycle-list.yaml", "/x-cycle/0", (12, 10)),
                "input-cycle-mutual.yaml": (
                    "input-cycle-mutual.yaml",
                    "/x-outer/nested/child/back~1to~0outer/0",
                    (13, 11),
                ),
                "input-cycle-reference.yaml": ("input-cycle-reference-model.yaml", "/x-cycle/self", (6, 10)),
            }
            filename, pointer, location = cycles[spec["input"]["path"]]
            return "  " + cyclic_input_failure(error, source=filename, pointer=pointer, location=location)
        if isinstance(error, APIGenerationError):
            return "\n".join((f"  Error: {error}", *(_diagnostic(item) for item in error.diagnostics))).replace(
                root.resolve().as_posix(), "<root>"
            )
        return f"  {type(error).__name__}: {error}".replace(str(root.resolve()), "<root>").replace("\\", "/")


@dataclass
class _Scenario:
    """Replay one case's steps against a checkout, collecting the report lines."""

    case: dict[str, Any]
    root: Path
    monkeypatch: pytest.MonkeyPatch
    server: str | None
    lines: list[str] = field(default_factory=list)
    project: GeneratedProject | None = None
    remembered: dict[str, dict[str, bytes]] = field(default_factory=dict)

    @property
    def current(self) -> GeneratedProject:
        if self.project is None:
            raise AssertionError(self.lines)
        return self.project

    def render(self, overrides: dict[str, Any]) -> None:
        self.lines.append("render")
        match _run(self.case, overrides, self.root, self.server, publish=False):
            case str() as failure:
                self.project = None
                self.lines.append(failure)
            case GeneratedProject() as rendered:
                self.project = rendered
                _report_project(rendered, self.root, self.lines)
            case report:
                raise AssertionError(report)

    def generate(self, overrides: dict[str, Any]) -> None:
        self.lines.append("generate")
        match _run(self.case, overrides, self.root, self.server, publish=True):
            case str() as failure:
                self.lines.append(failure)
            case None:
                self.lines.append("  returned None")
                self.tree(None)
            case project:
                raise AssertionError(project)

    def chmod(self, value: list[Any]) -> None:
        path, mode = value
        (self.root / path).chmod(int(mode, 8))
        self.lines.append(f"chmod {path} {mode}")

    def symlink(self, value: list[str]) -> None:
        link, target = value
        (self.root / link).symlink_to(target, target_is_directory=True)
        self.lines.append(f"symlink {link} -> {target}")

    def file_modes(self, paths: list[str]) -> None:
        for path in paths:
            self.lines.append(f"mode {path}: {(self.root / path).stat().st_mode & 0o777:04o}")

    def dependencies(self, _: None) -> None:
        self.lines.append(f"dependencies {list(self.current.dependencies)}")

    def show(self, path: str) -> None:
        self.lines.append(f"show {path}")
        text = (self.root / path).read_bytes().decode("utf-8", errors="backslashreplace")
        self.lines.extend(f"  | {line}" for line in text.splitlines())

    def mkdir(self, path: str) -> None:
        (self.root / path).mkdir(parents=True)
        self.lines.append(f"mkdir {path}")

    def leftovers(self, _: None) -> None:
        hidden = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob(".*"))
        self.lines.append(f"leftovers {[_PRIVATE.sub('<private>', path) for path in hidden]}")

    def tree(self, _: None) -> None:
        self.lines.append("tree")
        files = [
            path.relative_to(self.root)
            for path in self.root.rglob("*")
            if path.is_file() and path.relative_to(self.root).parts[0] not in {"spec", "templates"}
        ]
        self.lines.extend(
            sorted(f"  {_PRIVATE.sub('<private>', path.as_posix())}" for path in files if not _runtime(path))
        )
        self.lines.extend(_runtime_line(["present" for path in files if _runtime(path)]))

    def publish(self, _: None) -> None:
        _publish(self.current, self.root)
        self.lines.append("publish")

    def write(self, value: list[str]) -> None:
        path, text = value
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / path).write_bytes(text.encode())
        self.lines.append(f"write {path}")

    def remove(self, path: str) -> None:
        (self.root / path).unlink()
        self.lines.append(f"remove {path}")

    def remember(self, name: str) -> None:
        self.remembered[name] = _files(self.current, self.root)

    def compare(self, name: str) -> None:
        self.lines.append(f"files identical to {name}: {_files(self.current, self.root) == self.remembered[name]}")

    def relocate(self, name: str) -> None:
        other = self.root / name
        sources = list(self.root.iterdir())
        other.mkdir()
        for source in sources:
            (shutil.copytree if source.is_dir() else shutil.copy2)(source, other / source.name)
        self.monkeypatch.chdir(other)
        match _run(self.case, {}, other, self.server, publish=False):
            case GeneratedProject() as relocated:
                identical = _files(relocated, other) == _files(self.current, self.root)
                self.lines.append(f"relocated files identical: {identical}")
            case failure:
                raise AssertionError(failure)
        self.monkeypatch.chdir(self.root)


def target_render_report(case_name: str, root: Path, monkeypatch: pytest.MonkeyPatch, server: str | None = None) -> str:
    """Run one scenario's renders, publications, and edits, reporting every observable outcome."""
    case = json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))[case_name]
    shutil.copytree(SOURCE / "spec", root / "spec")
    shutil.copytree(SOURCE / "templates", root / "templates")
    monkeypatch.chdir(root)
    scenario = _Scenario(case, root, monkeypatch, server, [f"# {case_name}"])
    for step in case["steps"]:
        ((name, value),) = step.items()
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always", UserWarning)
            getattr(scenario, name)(value)
        scenario.lines.extend(f"  {item.category.__name__}: {item.message}" for item in recorded)
    return "\n".join(scenario.lines) + "\n"


def target_config_report(case_name: str) -> str:
    """Construct one target configuration and report every setting or the ordered diagnostics."""
    values = json.loads((SOURCE / "configs.json").read_text(encoding="utf-8"))[case_name]
    try:
        config = _config(values)
    except APIGenerationError as error:
        return "\n".join((f"Error: {error}", *(_diagnostic(item).lstrip() for item in error.diagnostics))) + "\n"
    return "".join(
        f"{item.name}={value.as_posix() if isinstance(value := getattr(config, item.name), Path) else value!r}\n"
        for item in fields(config)
    )
