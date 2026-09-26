"""Replay target scenarios through the FastAPI entry points and report artifacts, manifests, and failures."""

from __future__ import annotations

import json
import os
import re
import shutil
from contextlib import ExitStack
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import yaml

from datamodel_code_generator import DataModelType, Error, GenerateConfig, _api_publication
from datamodel_code_generator._api_publication import lock_path, resource_locks
from datamodel_code_generator.fastapi import (
    APIGenerationError,
    BuiltinCodecCompatibility,
    Diagnostic,
    FastAPIConfig,
    GeneratedProject,
    GenerationReport,
    ModelExportBinding,
    OperationRef,
    OperationSelection,
    PublicationRollbackError,
    ResponseChoice,
    SchemaRef,
    generate_fastapi,
    render_fastapi,
)
from datamodel_code_generator.remote_lock import RemoteLockError, RemoteReferenceLock
from tests.data.python import fastapi_hooks

if TYPE_CHECKING:
    import pytest

SOURCE = Path(__file__).parents[1] / "generation_platform" / "targets"
MANIFEST = ".dcg-target-manifest.json"
_HASH = re.compile(r'"[0-9a-f]{64}"')
_PRIVATE = re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{32}|[0-9a-f]{16})(?![0-9a-f])")
_MASKED = frozenset({"size", "version", "runtime_revision"})


def fixture_class_name(name: str) -> str:
    """Rename models so legacy process-state handling is exercised."""
    return f"Fixture{name}"


def _runtime(path: Path) -> bool:
    return "_runtime" in path.parts


def _selector(value: str | dict[str, str]) -> OperationRef | str:
    if isinstance(value, str):
        return value
    return OperationRef(**{key: item.replace("{root}", Path.cwd().as_uri()) for key, item in value.items()})


def _schema(value: dict[str, str]) -> SchemaRef:
    return SchemaRef(**value)


def _config(values: dict[str, Any]) -> FastAPIConfig:
    values = {"output": "server", "package": "example.server", "model_package": "example.models", **values}
    converted: dict[str, Any] = {}
    for key, value in values.items():
        match key:
            case "output" | "formatter_settings" | "templates" if isinstance(value, str):
                converted[key] = Path(value)
            case "selection" if value is not None:
                converted[key] = OperationSelection(**{
                    name: tuple(_selector(item) for item in item_value) if isinstance(item_value, list) else item_value
                    for name, item_value in value.items()
                })
            case "builtin_codec_compatibility":
                converted[key] = tuple(
                    BuiltinCodecCompatibility(**{
                        **item,
                        "backend": DataModelType(item["backend"]),
                        "schemas": tuple(_schema(schema) for schema in item["schemas"]),
                    })
                    for item in value
                )
            case "export_bindings":
                converted[key] = tuple(
                    ModelExportBinding(**{**item, "schema": _schema(item["schema"])}) for item in value
                )
            case "primary_responses":
                converted[key] = {_selector(selector): ResponseChoice(**choice) for selector, choice in value}
            case "operation_names":
                converted[key] = {_selector(selector): name for selector, name in value}
            case "hooks":
                converted[key] = tuple(getattr(fastapi_hooks, name) for name in value)
            case "custom_formatters" | "formatters":
                converted[key] = tuple(value)
            case _:
                converted[key] = value
    return FastAPIConfig(**converted)


def _model(values: dict[str, Any], root: Path) -> GenerateConfig:
    values = {"output": "models.py", "disable_timestamp": True, "formatters": [], **values}
    converted: dict[str, Any] = {}
    resolved = None
    for key, value in values.items():
        match key:
            case "output" | "emit_model_metadata" | "custom_file_header_path" | "lockfile":
                converted[key] = None if value is None else Path(value)
            case "custom_class_name_generator":
                converted[key] = fixture_class_name
            case "field_extra_keys":
                converted[key] = set(value)
            case "resolved_lock":
                resolved = value
            case _:
                converted[key] = value
    config = GenerateConfig(**converted)
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
            raise AssertionError(value)


def _mask(value: Any, server: str | None) -> Any:
    match value:
        case dict():
            return {
                key: "<fastapi>"
                if key == "target_data"
                else "<masked>"
                if key in _MASKED and item is not None
                else _mask(item, server)
                for key, item in value.items()
            }
        case list():
            return [
                _mask(item, server)
                for item in value
                if not (isinstance(item, dict) and str(item.get("path", "")).startswith("_runtime/"))
            ]
        case str() if server is not None and server in value:
            return value.replace(server, "http://server")
        case _:
            return value


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
        + ("" if artifact.target_id is None else " (target)")
        for artifact in project.artifacts
        if not _runtime(artifact.path)
    )
    lines.extend(_runtime_line([artifact.action for artifact in project.artifacts if _runtime(artifact.path)]))
    lines.extend(_diagnostic(item) for item in project.diagnostics)


def _publish(project: GeneratedProject, root: Path) -> None:
    for artifact in project.artifacts:
        location = root / artifact.path
        match artifact.action, artifact.content:
            case "write", bytes() as content:
                location.parent.mkdir(parents=True, exist_ok=True)
                location.write_bytes(content)
            case "delete", _:
                location.unlink()
            case _:
                pass


def _patched(data: bytes, changes: dict[str, Any]) -> bytes:
    value = json.loads(data)
    for pointer, item in [*changes.get("set", {}).items(), *((pointer, None) for pointer in changes.get("delete", []))]:
        *parents, last = [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]
        container = value
        for token in parents:
            container = container[int(token)] if isinstance(container, list) else container[token]
        key = int(last) if isinstance(container, list) else last
        if pointer in changes.get("set", {}):
            container[key] = item
        else:
            del container[key]
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _manifest(project: GeneratedProject) -> bytes:
    return next(artifact.content for artifact in project.artifacts if artifact.path.name == MANIFEST) or b""


def _run(
    case: dict[str, Any], overrides: dict[str, Any], root: Path, server: str | None, *, publish: bool
) -> GeneratedProject | GenerationReport | str:
    spec = {**case, **{key: value for key, value in overrides.items() if key not in {"model", "config"}}}
    model = {**case.get("model", {}), **overrides.get("model", {})}
    config = {**case.get("config", {}), **overrides.get("config", {})}
    try:
        return (generate_fastapi if publish else render_fastapi)(
            _input(spec["input"], server), model_config=_model(model, root), config=_config(config)
        )
    except APIGenerationError as error:
        lines = ["  APIGenerationError", *(_diagnostic(item) for item in error.diagnostics)]
        return "\n".join(lines).replace(root.resolve().as_posix(), "<root>")
    except PublicationRollbackError as error:
        unrestored = ", ".join(_relative(path, root) for path in error.unrestored)
        cause = type(error.__cause__).__name__
        return f"  PublicationRollbackError after {cause}: unrestored {unrestored}; {len(error.backups)} backups kept"
    except (Error, RemoteLockError, OSError, UnicodeError, KeyboardInterrupt) as error:
        return f"  {type(error).__name__}: {error}".replace(str(root.resolve()), "<root>").replace("\\", "/")


def _report_generation(report: GenerationReport, root: Path, lines: list[str]) -> None:
    lines.append(f"  target={report.target} schema_version={report.schema_version}")
    for name, records in (
        ("written", report.written_files),
        ("unchanged", report.unchanged_files),
        ("deleted", report.deleted_files),
    ):
        lines.extend(
            f"  {name} {record.kind} {_relative(record.path, root)}" + ("" if record.target_id is None else " (target)")
            for record in records
            if not _runtime(record.path)
        )
        lines.extend(_runtime_line([name for record in records if _runtime(record.path)]))


@dataclass
class _Scenario:
    """Replay one case's steps against a checkout, collecting the report lines."""

    case: dict[str, Any]
    root: Path
    monkeypatch: pytest.MonkeyPatch
    server: str | None
    lines: list[str] = field(default_factory=list)
    project: GeneratedProject | None = None
    published: dict[str, bytes] = field(default_factory=dict)
    remembered: dict[str, bytes] = field(default_factory=dict)
    held: ExitStack = field(default_factory=ExitStack)

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
            case GenerationReport() as report:
                _report_generation(report, self.root, self.lines)
            case project:
                raise AssertionError(project)

    def hold(self, value: str) -> None:
        kind, _, name = value.partition(":")
        resource = (self.root / (name or "server")).resolve()
        match kind:
            case "thread":
                self.held.enter_context(resource_locks([resource]))
            case _:
                descriptor = _api_publication._open_lockfile(lock_path(resource))
                self.held.callback(os.close, descriptor)
                _api_publication._acquire(descriptor, resource)
                self.held.callback(_api_publication._release, descriptor)
        self.lines.append(f"hold {kind} lock on {name or 'server'}")

    def release(self, _: None) -> None:
        self.held.close()
        self.lines.append("release")

    def show(self, path: str) -> None:
        self.lines.append(f"show {path}")
        text = (self.root / path).read_bytes().decode("utf-8", errors="backslashreplace")
        self.lines.extend(f"  | {line}" for line in text.splitlines())

    def mkdir(self, path: str) -> None:
        (self.root / path).mkdir(parents=True)
        self.lines.append(f"mkdir {path}")

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
        self.published.update(
            (artifact.path.as_posix(), artifact.content)
            for artifact in self.current.artifacts
            if artifact.content is not None
        )
        self.lines.append("publish")

    def write(self, value: list[str]) -> None:
        path, text = value
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / path).write_bytes(text.encode())
        self.lines.append(f"write {path}")

    def restore(self, path: str) -> None:
        (self.root / path).write_bytes(self.published[path])
        self.lines.append(f"restore {path}")

    def remove(self, path: str) -> None:
        (self.root / path).unlink()
        self.lines.append(f"remove {path}")

    def patch(self, value: dict[str, Any]) -> None:
        path, changes = value["file"], {key: item for key, item in value.items() if key != "file"}
        (self.root / path).write_bytes(_patched(self.published[path], changes))
        self.lines.append(f"patch {path} {json.dumps(changes, sort_keys=True)}")

    def remember(self, name: str) -> None:
        self.remembered[name] = _manifest(self.current)

    def compare(self, name: str) -> None:
        self.lines.append(f"manifest identical to {name}: {_manifest(self.current) == self.remembered[name]}")

    def manifest(self, _: None) -> None:
        self.lines.append(json.dumps(_mask(json.loads(_manifest(self.current)), self.server), indent=2, sort_keys=True))

    def relocate(self, name: str) -> None:
        other = self.root / name
        sources = list(self.root.iterdir())
        other.mkdir()
        for source in sources:
            (shutil.copytree if source.is_dir() else shutil.copy2)(source, other / source.name)
        self.monkeypatch.chdir(other)
        match _run(self.case, {}, other, self.server, publish=False):
            case GeneratedProject() as relocated:
                self.lines.append(f"relocated manifest identical: {_manifest(relocated) == _manifest(self.current)}")
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
    with scenario.held:
        for step in case["steps"]:
            ((name, value),) = step.items()
            getattr(scenario, name)(value)
    return _HASH.sub('"<sha256>"', "\n".join(scenario.lines)) + "\n"


def target_config_report(case_name: str) -> str:
    """Construct one target configuration and report every setting or the ordered diagnostics."""
    values = json.loads((SOURCE / "configs.json").read_text(encoding="utf-8"))[case_name]
    try:
        config = _config(values)
    except APIGenerationError as error:
        return "\n".join(["APIGenerationError", *(_diagnostic(item) for item in error.diagnostics)]) + "\n"
    return "".join(
        f"{item.name}={value.as_posix() if isinstance(value := getattr(config, item.name), Path) else value!r}\n"
        for item in fields(config)
    )
