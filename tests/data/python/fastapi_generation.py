"""Render FastAPI server targets from OpenAPI fixtures and report their files, decisions, and failures."""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from pathlib import Path, PurePosixPath
from types import FunctionType
from typing import Any, TypeAlias, get_type_hints

from datamodel_code_generator import DataModelType, GenerateConfig, _runtime
from datamodel_code_generator.enums import OpenAPIScope
from datamodel_code_generator.fastapi import (
    APIGenerationError,
    Diagnostic,
    FastAPIConfig,
    GeneratedProject,
    GenerationInput,
    GenerationReport,
    HookReference,
    OperationRef,
    OperationSelection,
    ResponseChoice,
    generate_fastapi,
    render_fastapi,
)
from datamodel_code_generator.format import Formatter
from tests.data.python import fastapi_hooks
from tests.data.python.model_codec_adapters import declaration

SOURCE = Path(__file__).parents[1] / "generation_platform" / "fastapi"
PACKAGE = "server"
MANIFEST = ".dcg-target-manifest.json"
RUNTIME = Path(_runtime.__file__).parent
Modules: TypeAlias = dict[tuple[str, ...], str]


def _selector(value: object) -> object:
    return OperationRef(**value) if isinstance(value, dict) else value


def fastapi_config(values: dict[str, Any], root: Path) -> FastAPIConfig:
    """Build a FastAPI configuration from JSON fixture values."""
    values = {"output": PACKAGE, "package": PACKAGE, "model_package": "models", "formatter_settings": ".", **values}
    converted: dict[str, Any] = {}
    for key, value in values.items():
        match key:
            case "output" | "formatter_settings":
                converted[key] = root / value
            case "selection":
                converted[key] = OperationSelection(**{
                    name: tuple(_selector(item) for item in item_value) if isinstance(item_value, list) else item_value
                    for name, item_value in value.items()
                })
            case "primary_responses" if isinstance(value, list):
                converted[key] = {
                    _selector(selector): ResponseChoice(**choice) if isinstance(choice, dict) else choice
                    for selector, choice in value
                }
            case "handler_modes" | "body_modes" | "operation_names" | "parameter_names" if isinstance(value, list):
                converted[key] = {_selector(selector): item for selector, item in value}
            case "codec_adapters" | "builtin_codec_compatibility" if isinstance(value, list):
                kind = "adapters" if key == "codec_adapters" else "compatibility"
                converted[key] = tuple(declaration(kind, item) for item in value)
            case "formatters":
                converted[key] = tuple(value)
            case "hooks" if isinstance(value, list):
                converted[key] = tuple(_hook(item, root) for item in value)
            case "templates" if isinstance(value, str):
                converted[key] = _copied(value, root)
            case "update_groups" if isinstance(value, list):
                converted[key] = tuple(value)
            case _:
                converted[key] = value
    return FastAPIConfig(**converted)


def _copied(name: str, root: Path) -> Path:
    """Copy a fixture file or directory under the target root, so persistent paths stay relative on every drive."""
    source, target = SOURCE / name, root / name
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    elif source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return target


def _hook(value: object, root: Path) -> object:
    match value:
        case {"file": str() as file, **rest}:
            return HookReference(file=_copied(file, root), **rest)
        case dict():
            return HookReference(**value)
        case str():
            return getattr(fastapi_hooks, value)
        case _:
            return value


def _setting(value: object, root: Path) -> str:
    match value:
        case tuple():
            items = [_setting(item, root) for item in value]
            return f"({', '.join(items)}{',' if len(items) == 1 else ''})"
        case HookReference(file=Path() as file):
            relative = file.relative_to(root if file.is_relative_to(root) else SOURCE)
            return repr(replace(value, file=PurePosixPath(relative.as_posix())))
        case FunctionType(__module__=module, __qualname__=name):
            return f"<function {module}.{name}>"
        case Path():
            return repr(PurePosixPath(value.relative_to(root if value.is_relative_to(root) else SOURCE).as_posix()))
        case _:
            return repr(value)


def _diagnostic(item: Diagnostic) -> str:
    fields = (item.stage, item.option_path, item.source_pointer, item.artifact_path)
    location = " ".join(str(field) for field in fields if field is not None)
    return f"  {item.code} {location}: {item.message}"


def _decisions(project: GeneratedProject) -> list[str]:
    manifest = next(artifact for artifact in project.artifacts if artifact.path.name == MANIFEST)
    data = json.loads(manifest.content or b"{}")["target_data"]["fastapi"]
    lines = [f"  layout {data['layout']}"]
    for operation in data["operations"]:
        primary = "-" if (chosen := operation["primary_response"]) is None else _primary(chosen)
        lines.append(
            f"  {operation['python_name']}: {operation['method'].upper()} {operation['path']}"
            f" -> {operation['route_path']} [{operation['group_key']}] status {operation['registration_status']}"
            f" primary {primary} body {operation['body_mode']} handler {operation['handler_mode']}"
        )
        lines.extend(
            f"    slot {slot['slot']} = {slot['wire_name']} #{slot['occurrence']}" for slot in operation["path_slots"]
        )
        for projection in operation["projections"]:
            use = projection["use_ids"][0]["use_site"]["pointer"] if projection["use_ids"] else "-"
            source = "" if projection["source"] is None else f" at {projection['source']['pointer']}"
            lines.append(
                f"    {projection['site']} {use}: {projection['transport']} {projection['reason']}{source}"
            )
    lines.extend(
        f"  group {group['key']} -> {group['file_stem']} ({len(group['operations'])})" for group in data["groups"]
    )
    return lines


def _projection(value: object) -> object:
    match value:
        case Mapping():
            return {str(key): _projection(item) for key, item in value.items()}
        case tuple():
            return [_projection(item) for item in value]
        case _ if is_dataclass(value) and not isinstance(value, type):
            return {item.name: _projection(getattr(value, item.name)) for item in fields(value)}
        case _:
            return value


def _primary(chosen: dict[str, Any]) -> str:
    return f"{chosen['status_code']} {chosen['media_type']}"


def _render(case: dict[str, Any], backend: str, root: Path, modules: Modules) -> list[str]:
    model = {
        "output": root / "models.py",
        "input_file_type": "openapi",
        "openapi_scopes": [OpenAPIScope.Schemas, OpenAPIScope.Api],
        "output_model_type": DataModelType(backend),
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
        **case.get("model", {}),
    }
    root.mkdir(parents=True, exist_ok=True)
    source = shutil.copy2(SOURCE / case["input"], root / case["input"])
    for name in case.get("files", ()):
        shutil.copy2(SOURCE / name, root / name)
    fastapi_hooks.RECORDED.clear()
    try:
        project = render_fastapi(
            source, model_config=GenerateConfig(**model), config=fastapi_config(case.get("config", {}), root)
        )
    except APIGenerationError as error:
        return ["  APIGenerationError", *(_diagnostic(item) for item in error.diagnostics)]
    except LookupError as error:
        return [f"  {type(error).__name__}: {error}"]
    encoding = case.get("config", {}).get("encoding", "utf-8")
    lines: list[str] = []
    files: list[str] = []
    for artifact in project.artifacts:
        path, content = artifact.path.relative_to(root), artifact.content or b""
        line = f"  {artifact.action} {path.as_posix()}"
        match path.suffix, path.parts:
            case _, parts if "_runtime" in parts:
                source = RUNTIME.joinpath(*parts[parts.index("_runtime") + 1 :]).read_bytes()
                copied = content.replace(b"\r\n", b"\n").endswith(source.replace(b"\r\n", b"\n"))
                line += f" ({'copied' if copied else 'changed'} runtime)"
                if backend in case.get("runtime", ()):
                    modules[parts] = content.decode(encoding)
            case ".py", parts:
                modules[parts] = content.decode(encoding)
            case _ if path.name != MANIFEST:
                files.append(f"  file {path.as_posix()}")
                files.extend(f"    | {text}" if text else "    |" for text in content.decode().splitlines())
        lines.append(line)
    lines.extend(_decisions(project))
    lines.extend(_diagnostic(item) for item in project.diagnostics)
    for context in fastapi_hooks.RECORDED:
        lines.append("  context")
        lines.extend(f"    | {line}" for line in json.dumps(_projection(context), indent=2).splitlines())
    return [*lines, *files]


def _generate(overrides: dict[str, Any], root: Path) -> list[str]:
    model = GenerateConfig(
        output=root / "models.py",
        input_file_type="openapi",
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
        output_model_type=DataModelType.PydanticV2BaseModel,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
    )
    try:
        report = generate_fastapi(
            root / overrides.get("input", "api.yaml"),
            model_config=model,
            config=fastapi_config(overrides.get("config", {}), root),
        )
    except APIGenerationError as error:
        return ["  APIGenerationError", *(_diagnostic(item) for item in error.diagnostics)]
    changes = [
        f"  {action} {record.path.relative_to(root).as_posix()}"
        for action, records in (("written", report.written_files), ("deleted", report.deleted_files))
        for record in records
        if "_runtime" not in record.path.parts
    ]
    return [*changes, *(_diagnostic(item) for item in report.diagnostics)]


def fastapi_scenario_report(case_name: str, root: Path) -> str:
    """Replay one regeneration scenario of spec changes and manifest edits, reporting each generation's writes."""
    root = root.resolve()
    lines = [f"# {case_name}"]
    for step in json.loads((SOURCE / "scenarios.json").read_text(encoding="utf-8"))[case_name]:
        ((action, value),) = step.items()
        match action, value:
            case "spec", [str() as name, str() as fixture]:
                shutil.copy2(SOURCE / fixture, root / name)
                lines.append(f"spec {name} <- {fixture}")
            case "generate", dict() as overrides:
                lines.append(f"generate {json.dumps(overrides, sort_keys=True)}")
                lines.extend(_generate(overrides, root))
            case "patch", [str() as name, str() as pointer, replacement]:
                data = json.loads((root / name).read_text(encoding="utf-8"))
                *parents, last = pointer.removeprefix("/").split("/")
                container = data
                for token in parents:
                    container = container[int(token)] if isinstance(container, list) else container[token]
                container[last] = replacement
                (root / name).write_text(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
                lines.append(f"patch {name} {pointer}")
            case _:
                raise AssertionError(step)
    return "\n".join(lines) + "\n"


def fastapi_render(case_name: str, root: Path) -> tuple[str, dict[str, Modules]]:
    """Render one fixture for each of its backends, returning a report and every backend's Python modules."""
    case = json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))[case_name]
    lines = [f"# {case_name}"]
    rendered: dict[str, Modules] = {}
    for backend in case.get("backends", ["pydantic_v2.BaseModel"]):
        lines.append(f"render {backend}")
        lines.extend(_render(case, backend, root / (name := backend.replace(".", "_")), modules := {}))
        if modules:
            rendered[name] = modules
    return "\n".join(lines).replace(root.resolve().as_posix(), "<root>") + "\n", rendered


def fastapi_config_report(case_name: str, root: Path) -> str:
    """Construct one FastAPI configuration and report every setting or the ordered diagnostics."""
    case = json.loads((SOURCE / "configs.json").read_text(encoding="utf-8"))[case_name]
    try:
        config = fastapi_config(case, root)
    except APIGenerationError as error:
        return "\n".join(["APIGenerationError", *(_diagnostic(item) for item in error.diagnostics)]) + "\n"
    return "".join(f"{item.name}={_setting(getattr(config, item.name), root)}\n" for item in fields(config))


def fastapi_api_report(root: Path) -> str:
    """Resolve the entry points' annotations, then render, generate twice, and render over an edited owned file."""
    hints = {"input_": GenerationInput, "model_config": GenerateConfig, "config": FastAPIConfig}
    lines = [
        f"{function.__name__} resolves {sorted(hints)}: {get_type_hints(function) == {**hints, 'return': result}}"
        for function, result in ((generate_fastapi, GenerationReport), (render_fastapi, GeneratedProject))
    ]
    source = shutil.copy2(SOURCE / "pets.yaml", root / "api.yaml")
    model = GenerateConfig(
        output=root / "models.py",
        input_file_type="openapi",
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
        output_model_type=DataModelType.PydanticV2BaseModel,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
    )
    config = fastapi_config({}, root)
    project = render_fastapi(source, model_config=model, config=config)
    lines.append(f"render {sorted({artifact.action for artifact in project.artifacts})}")
    for _ in range(2):
        report = generate_fastapi(source, model_config=model, config=config)
        lines.append(f"generate wrote {len(report.written_files)} and kept {len(report.unchanged_files)}")
    (root / PACKAGE / "README.md").write_text("# edited\n", encoding="utf-8")
    try:
        render_fastapi(source, model_config=model, config=config)
    except APIGenerationError as error:
        lines.extend(_diagnostic(item) for item in error.diagnostics)
    return "\n".join(lines) + "\n"
