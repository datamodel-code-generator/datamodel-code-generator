"""Render fixture targets through the single-target coordinator and report artifacts, manifests, and failures."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field, fields
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from datamodel_code_generator import DataModelType, Error, GenerateConfig
from datamodel_code_generator._api_generation import TargetBinding, TargetRender, render_target
from datamodel_code_generator._api_manifest import MANIFEST_NAME, PlannedFile
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, OperationSelection
from datamodel_code_generator._codec_declarations import (
    BuiltinCodecCompatibility,
    ModelExportBinding,
    OperationRef,
    SchemaRef,
)
from datamodel_code_generator._generation_contract import BindingCaptureError, LiteralScalar, LiteralSequence
from datamodel_code_generator._source import load_yaml
from datamodel_code_generator._target_config import TargetConfig, load_target_config
from datamodel_code_generator.remote_lock import RemoteLockError, RemoteReferenceLock

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import GeneratedProject, TargetKind
    from datamodel_code_generator._generation_contract import OperationContract

SOURCE = Path(__file__).parents[1] / "generation_platform" / "targets"
_HASH = re.compile(r'"[0-9a-f]{64}"')
_MASKED = frozenset({"size", "version", "runtime_revision"})


def fixture_hook() -> None:
    """Stand in for a user callable the manifest records only by identity."""


def fixture_class_name(name: str) -> str:
    """Rename models so legacy process-state handling is exercised."""
    return f"Fixture{name}"


class FixtureToken:
    """Stand in for an arbitrary settings object the manifest cannot project."""


@dataclass(frozen=True, slots=True, kw_only=True)
class FixtureConfig(TargetConfig):
    """Add settings a real target might have: a callable, an operation reference, and opaque values."""

    hook: Callable[[], None] | None = None
    primary: OperationRef | None = None
    token: object = None
    labels: frozenset[str] = frozenset()
    failure: str | None = None


class FixtureTarget:
    """Group selected operations by first tag into route modules the target owns."""

    def __init__(self, kind: TargetKind, backends: frozenset[DataModelType]) -> None:
        """Declare the target kind and the model backends it accepts."""
        self.kind: TargetKind = kind
        self.backends = backends
        self.unsupported_backend = f"E_{kind.upper()}_BACKEND_UNSUPPORTED"

    def render(self, request: TargetRequest) -> TargetRender:
        """Plan files, bindings, and manifest data from the coordinator's request."""
        config = request.config
        groups: dict[str, list[OperationContract]] = {}
        for operation in request.operations:
            tags = dict(operation.facts).get("tags")
            first = tags.items[0] if isinstance(tags, LiteralSequence) and tags.items else None
            groups.setdefault(str(first.value) if isinstance(first, LiteralScalar) else "default", []).append(operation)
        files = [PlannedFile(path=PurePosixPath("__init__.py"), kind="package", content=b"")]
        for group, operations in groups.items():
            routes = "".join(f"# {operation.method.upper()} {operation.path}\n" for operation in operations)
            files.append(
                PlannedFile(path=PurePosixPath("routes", f"{group}.py"), kind="routes", content=routes.encode(), group=group)
            )
        selected = {operation.id for operation in request.operations}
        failure = getattr(config, "failure", None)
        return TargetRender(
            files=tuple(files),
            target_data={
                "groups": {
                    group: [request.documents.operation(operation.id) for operation in operations]
                    for group, operations in groups.items()
                },
                "excluded": len(request.excluded),
            },
            bindings=tuple(
                TargetBinding(
                    use=use.id,
                    backend=request.model_config.output_model_type.value,
                    strategy="native",
                    converter_strategy="pydantic_type_adapter",
                )
                for use in request.batch.type_uses
                if use.state == "bound" and use.id.owner in selected
            ),
            diagnostics=()
            if failure is None
            else (
                Diagnostic(
                    code=failure,
                    severity="error",
                    stage="target",
                    message="The fixture target refuses to render",
                    target_id=request.target_id,
                ),
            ),
            persistent_diagnostics=tuple(
                Diagnostic(
                    code="I_FIXTURE_GROUP",
                    severity="info",
                    stage="target",
                    message=f"The {group} group has {len(operations)} operations",
                    target_id=request.target_id,
                )
                for group, operations in groups.items()
            ),
            protocol_metadata={"models": len(request.models)} if self.kind == "client" else {},
        )


TARGETS = {
    "server": FixtureTarget(
        "fastapi", frozenset({DataModelType.PydanticV2BaseModel, DataModelType.PydanticV2Dataclass})
    ),
    "client": FixtureTarget("client", frozenset(DataModelType)),
}


def _selector(value: str | dict[str, str]) -> OperationRef | str:
    if isinstance(value, str):
        return value
    return OperationRef(**{key: item.replace("{root}", Path.cwd().as_uri()) for key, item in value.items()})


def _schema(value: dict[str, str]) -> SchemaRef:
    return SchemaRef(**value)


def _config(values: dict[str, Any]) -> FixtureConfig:
    values = {"output": "server", "package": "example.server", "model_package": "example.models", **values}
    converted: dict[str, Any] = {}
    for key, value in values.items():
        match key:
            case "output" | "formatter_settings" if isinstance(value, str):
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
            case "hook":
                converted[key] = fixture_hook
            case "token":
                converted[key] = FixtureToken()
            case "primary":
                converted[key] = _selector(value)
            case "labels":
                converted[key] = frozenset(value)
            case "custom_formatters" | "formatters":
                converted[key] = tuple(value)
            case _:
                converted[key] = value
    return FixtureConfig(**converted)


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
            case "resolved_lock":
                resolved = value
            case _:
                converted[key] = value
    config = GenerateConfig(**converted)
    match resolved:
        case "update" | "readonly":
            config.resolve_remote_lock(
                RemoteReferenceLock.open(root / "resolved.lock", update=resolved == "update", locked=False)
            )
        case _:
            pass
    return config


def _input(value: dict[str, Any], server: str | None) -> object:
    match value:
        case {"path": str() as path}:
            return Path("spec", path)
        case {"text": str() as path}:
            return Path("spec", path).read_text(encoding="utf-8")
        case {"mapping": str() as path}:
            return load_yaml(Path("spec", path).read_text(encoding="utf-8"))
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
                key: "<masked>" if key in _MASKED and item is not None else _mask(item, server)
                for key, item in value.items()
            }
        case list():
            return [_mask(item, server) for item in value]
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


def _report_project(project: GeneratedProject, root: Path, lines: list[str]) -> None:
    lines.append(f"  target={project.target} schema_version={project.schema_version}")
    lines.extend(
        f"  {artifact.action} {artifact.kind} {_relative(artifact.path, root)}"
        + ("" if artifact.target_id is None else " (target)")
        + ("" if artifact.content is None else f" {len(artifact.content.splitlines())} lines")
        for artifact in project.artifacts
    )
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
    return next(artifact.content for artifact in project.artifacts if artifact.path.name == MANIFEST_NAME) or b""


def _render(case: dict[str, Any], overrides: dict[str, Any], root: Path, server: str | None) -> GeneratedProject | str:
    spec = {**case, **{key: value for key, value in overrides.items() if key not in {"model", "config"}}}
    model = {**case.get("model", {}), **overrides.get("model", {})}
    config = {**case.get("config", {}), **overrides.get("config", {})}
    try:
        return render_target(
            _input(spec["input"], server),
            model_config=_model(model, root),
            config=_config(config),
            generator=TARGETS[spec["target"]],
        )
    except APIGenerationError as error:
        return "\n".join(["  APIGenerationError", *(_diagnostic(item) for item in error.diagnostics)])
    except (BindingCaptureError, Error, RemoteLockError, OSError) as error:
        return f"  {type(error).__name__}: {error}".replace(str(root.resolve()), "<root>")


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

    @property
    def current(self) -> GeneratedProject:
        if self.project is None:
            raise AssertionError(self.lines)
        return self.project

    def render(self, overrides: dict[str, Any]) -> None:
        self.lines.append("render")
        match _render(self.case, overrides, self.root, self.server):
            case str() as failure:
                self.project = None
                self.lines.append(failure)
            case rendered:
                self.project = rendered
                _report_project(rendered, self.root, self.lines)

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
        match _render(self.case, {}, other, self.server):
            case str() as failure:
                self.lines.append(failure)
            case relocated:
                self.lines.append(f"relocated manifest identical: {_manifest(relocated) == _manifest(self.current)}")
        self.monkeypatch.chdir(self.root)


def target_render_report(case_name: str, root: Path, monkeypatch: pytest.MonkeyPatch, server: str | None = None) -> str:
    """Run one scenario's renders, publications, and edits, reporting every observable outcome."""
    case = json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))[case_name]
    shutil.copytree(SOURCE / "spec", root / "spec")
    monkeypatch.chdir(root)
    scenario = _Scenario(case, root, monkeypatch, server, [f"# {case_name}"])
    for step in case["steps"]:
        ((name, value),) = step.items()
        getattr(scenario, name)(value)
    return _HASH.sub('"<sha256>"', "\n".join(scenario.lines)) + "\n"


def target_config_report(case_name: str, root: Path) -> str:
    """Construct or load one target configuration and report the value or its ordered diagnostics."""
    case = json.loads((SOURCE / "configs.json").read_text(encoding="utf-8"))[case_name]
    try:
        match case:
            case {"toml": str() as text, **rest}:
                path = root / "target.toml"
                path.write_text(text, encoding="utf-8")
                output = rest.get("output")
                config = load_target_config(path, FixtureConfig, output=None if output is None else Path(output))
            case {"python": dict() as values}:
                config = _config(values)
            case _:
                raise AssertionError(case)
    except APIGenerationError as error:
        return "\n".join(["APIGenerationError", *(_diagnostic(item) for item in error.diagnostics)]) + "\n"
    values = {
        item.name: _relative(value, root) if isinstance(value := getattr(config, item.name), Path) else value
        for item in fields(config)
    }
    return (
        "\n".join(f"{key}={value!r}" for key, value in values.items()).replace(root.resolve().as_uri(), "<root>") + "\n"
    )
