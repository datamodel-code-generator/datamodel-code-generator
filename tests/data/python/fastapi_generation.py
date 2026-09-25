"""Render FastAPI server targets from OpenAPI fixtures and report their files, decisions, and failures."""

from __future__ import annotations

import json
import shutil
from dataclasses import fields
from pathlib import Path
from typing import Any

from datamodel_code_generator import DataModelType, GenerateConfig
from datamodel_code_generator._api_generation import render_target
from datamodel_code_generator._api_manifest import MANIFEST_NAME
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, GeneratedProject, OperationSelection
from datamodel_code_generator._codec_declarations import OperationRef
from datamodel_code_generator._fastapi.config import FastAPIConfig, ResponseChoice
from datamodel_code_generator._fastapi.target import FastAPITarget
from datamodel_code_generator._target_config import TargetConfig, load_target_config
from datamodel_code_generator.enums import OpenAPIScope
from datamodel_code_generator.format import Formatter
from tests.data.python.model_codec_adapters import declaration

SOURCE = Path(__file__).parents[1] / "generation_platform" / "fastapi"
PACKAGE = "server"


def _selector(value: object) -> object:
    return OperationRef(**value) if isinstance(value, dict) else value


def fastapi_config(values: dict[str, Any], root: Path) -> FastAPIConfig:
    """Build a FastAPI configuration from JSON fixture values."""
    values = {"output": PACKAGE, "package": PACKAGE, "model_package": "models", **values}
    converted: dict[str, Any] = {}
    for key, value in values.items():
        match key:
            case "output":
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
            case "body_modes" | "operation_names" | "parameter_names" if isinstance(value, list):
                converted[key] = {_selector(selector): item for selector, item in value}
            case "codec_adapters" | "builtin_codec_compatibility" if isinstance(value, list):
                kind = "adapters" if key == "codec_adapters" else "compatibility"
                converted[key] = tuple(declaration(kind, item) for item in value)
            case "formatters":
                converted[key] = tuple(value)
            case _:
                converted[key] = value
    return FastAPIConfig(**converted)


def _diagnostic(item: Diagnostic) -> str:
    location = " ".join(str(field) for field in (item.stage, item.option_path, item.source_pointer) if field is not None)
    return f"  {item.code} {location}: {item.message}"


def _decisions(project: GeneratedProject) -> list[str]:
    manifest = next(artifact for artifact in project.artifacts if artifact.path.name == MANIFEST_NAME)
    data = json.loads(manifest.content or b"{}")["target_data"]["fastapi"]
    lines = [f"  layout {data['layout']}"]
    for operation in data["operations"]:
        primary = "-" if (chosen := operation["primary_response"]) is None else _primary(chosen)
        lines.append(
            f"  {operation['python_name']}: {operation['method'].upper()} {operation['path']}"
            f" -> {operation['route_path']} [{operation['group_key']}] status {operation['registration_status']}"
            f" primary {primary} body {operation['body_mode']}"
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


def _primary(chosen: dict[str, Any]) -> str:
    return f"{chosen['status_code']} {chosen['media_type']}"


def _render(case: dict[str, Any], backend: str, root: Path) -> list[str]:
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
    try:
        project = render_target(
            source,
            model_config=GenerateConfig(**model),
            config=fastapi_config(case.get("config", {}), root),
            generator=FastAPITarget(),
        )
    except APIGenerationError as error:
        return ["  APIGenerationError", *(_diagnostic(item) for item in error.diagnostics)]
    package = root / PACKAGE
    lines = [
        f"  {artifact.path.relative_to(package).as_posix()}"
        for artifact in project.artifacts
        if artifact.path.is_relative_to(package) and "_runtime" not in artifact.path.parts
    ]
    lines.extend(_decisions(project))
    for name in case.get("show", ()):
        content = next(artifact.content for artifact in project.artifacts if artifact.path == package / name)
        lines.append(f"  show {name}")
        lines.extend(f"    | {line}" if line else "    |" for line in (content or b"").decode().splitlines())
    return lines


def fastapi_render_report(case_name: str, root: Path) -> str:
    """Render one fixture for each of its backends and report the files, decisions, shown files, or failures."""
    case = json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))[case_name]
    lines = [f"# {case_name}"]
    for backend in case.get("backends", ["pydantic_v2.BaseModel"]):
        lines.append(f"render {backend}")
        lines.extend(_render(case, backend, root / backend.replace(".", "_")))
    return "\n".join(lines).replace(root.resolve().as_posix(), "<root>") + "\n"


def fastapi_config_report(case_name: str, root: Path) -> str:
    """Construct or load one FastAPI configuration and report its values or ordered diagnostics."""
    case = json.loads((SOURCE / "configs.json").read_text(encoding="utf-8"))[case_name]
    try:
        if "toml" in case:
            path = root / "target.toml"
            path.write_text(case["toml"], encoding="utf-8")
            config = load_target_config(path, FastAPIConfig)
        else:
            config = fastapi_config(case["python"], root)
    except APIGenerationError as error:
        return "\n".join(["APIGenerationError", *(_diagnostic(item) for item in error.diagnostics)]) + "\n"
    shared = {item.name for item in fields(TargetConfig)}
    return "".join(f"{item.name}={getattr(config, item.name)!r}\n" for item in fields(config) if item.name not in shared)
