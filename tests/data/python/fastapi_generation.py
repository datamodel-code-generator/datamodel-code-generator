"""Generate FastAPI server targets from OpenAPI fixtures through generate() and report their files and failures."""

from __future__ import annotations

import json
import re
import shutil
import warnings
from dataclasses import fields
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, TypeAlias, get_type_hints

from datamodel_code_generator import DataModelType, Error, GenerateConfig, _runtime, generate
from datamodel_code_generator.enums import OpenAPIScope
from datamodel_code_generator.fastapi import (
    FastAPIConfig,
    GeneratedProject,
    GenerationInput,
    OperationRef,
    ResponseChoice,
    generate_fastapi,
    render_fastapi,
)
from datamodel_code_generator.format import Formatter

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

SOURCE = Path(__file__).parents[1] / "generation_platform" / "fastapi"
PACKAGE = "server"
RUNTIME = Path(_runtime.__file__).parent
BUILTIN_TEMPLATES = RUNTIME.parent / "_fastapi" / "templates"
_DIGEST = re.compile(r'"[0-9a-f]{64}"')
_SIZE = re.compile(r'"size":\d+')
Modules: TypeAlias = dict[tuple[str, ...], str]


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
            case "primary_responses" if isinstance(value, list):
                converted[key] = {
                    _selector(selector): ResponseChoice(**choice) if isinstance(choice, dict) else choice
                    for selector, choice in value
                }
            case "handler_modes" | "body_modes" | "operation_names" | "parameter_names" if isinstance(value, list):
                converted[key] = {_selector(selector): item for selector, item in value}
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


def _setting(value: object, root: Path) -> str:
    match value:
        case tuple():
            items = [_setting(item, root) for item in value]
            return f"({', '.join(items)}{',' if len(items) == 1 else ''})"
        case Path():
            return repr(PurePosixPath(value.relative_to(root if value.is_relative_to(root) else SOURCE).as_posix()))
        case _:
            return repr(value)


def server_options(case: dict[str, Any], package: str = PACKAGE, models: str = "models") -> dict[str, Any]:
    """Return the generate() options that select the FastAPI server of a case, with its own server settings."""
    return {
        "generate_server": "fastapi",
        "server_package": package,
        "server_model_package": models,
        **case.get("config", {}),
    }


def in_directory(root: Path) -> AbstractContextManager[object]:
    """Run from root, importing `contextlib.chdir` only then, since it needs Python 3.11."""
    from contextlib import chdir

    return chdir(root)


def _render(
    case: dict[str, Any], backend: str, root: Path, modules: Modules, *, builtin_sources: bool = False
) -> list[str]:
    model = {
        "input_file_type": "openapi",
        "target_python_version": "3.11",
        "openapi_scopes": [OpenAPIScope.Schemas, OpenAPIScope.Api],
        "output_model_type": DataModelType(backend),
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
        **case.get("model", {}),
    }
    root.mkdir(parents=True, exist_ok=True)
    if isinstance(directory := model.get("custom_template_dir"), str):
        model["custom_template_dir"] = _copied(directory, root)
    if builtin_sources:
        shutil.copytree(BUILTIN_TEMPLATES, root / "builtin-sources" / "fastapi")
        model["custom_template_dir"] = root / "builtin-sources"
    source = shutil.copy2(SOURCE / case["input"], root / case["input"])
    for name in case.get("files", ()):
        shutil.copy2(SOURCE / name, root / name)
    try:
        with in_directory(root):
            files = generate(source, **model, **server_options(case))
    except Error as error:
        return [f"  Error: {error}"]
    lines: list[str] = []
    shown: list[str] = []
    for parts, text in files.items():
        line = f"  {'/'.join(parts)}"
        match Path(*parts).suffix, parts:
            case _, parts if "_runtime" in parts:
                source = RUNTIME.joinpath(*parts[parts.index("_runtime") + 1 :]).read_text(encoding="utf-8")
                line += f" ({'copied' if text.endswith(source) else 'changed'} runtime)"
                if backend in case.get("runtime", ()):
                    modules[parts] = text
            case ".py", parts:
                if parts[0] != PACKAGE or backend in case.get(
                    "package_snapshots", case.get("backends", ["pydantic_v2.BaseModel"])
                ):
                    modules[parts] = text
            case _:
                shown.append(f"  file {'/'.join(parts)}")
                masked = _SIZE.sub('"size":"<size>"', _DIGEST.sub('"<sha256>"', text))
                shown.extend(f"    | {item}" if item else "    |" for item in masked.splitlines())
        lines.append(line)
    return [*lines, *shown]


def fastapi_render(case_name: str, root: Path, *, builtin_sources: bool = False) -> tuple[str, dict[str, Modules]]:
    """Render one fixture for each of its backends, returning a report and every backend's Python modules.

    The report is headed by the case's `expected` name, or its own. With builtin_sources, a custom template directory
    holds a copy of the builtin server templates, so that every role renders from its Jinja source instead of its
    compiled renderer.
    """
    case = json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))[case_name]
    lines = [f"# {case.get('expected', case_name)}"]
    rendered: dict[str, Modules] = {}
    for backend in case.get("backends", ["pydantic_v2.BaseModel"]):
        lines.append(f"render {backend}")
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always", UserWarning)
            lines.extend(
                _render(
                    case,
                    backend,
                    root / (name := backend.replace(".", "_")),
                    modules := {},
                    builtin_sources=builtin_sources,
                )
            )
        lines.extend(f"  {item.category.__name__}: {item.message}" for item in recorded)
        if modules:
            rendered[name] = modules
    return "\n".join(lines).replace(root.resolve().as_posix(), "<root>") + "\n", rendered


def fastapi_config_report(case_name: str, root: Path) -> str:
    """Construct one FastAPI configuration and report every setting or the ordered diagnostics."""
    case = json.loads((SOURCE / "configs.json").read_text(encoding="utf-8"))[case_name]
    try:
        config = fastapi_config(case, root)
    except Error as error:
        return f"Error: {error}\n"
    return "".join(f"{item.name}={_setting(getattr(config, item.name), root)}\n" for item in fields(config))


def fastapi_api_report(root: Path) -> str:
    """Resolve the entry points' annotations, render, generate twice, then generate over an edited file."""
    hints = {"input_": GenerationInput, "model_config": GenerateConfig, "config": FastAPIConfig}
    lines = [
        f"{function.__name__} resolves {sorted(hints)}: {get_type_hints(function) == {**hints, 'return': result}}"
        for function, result in ((generate_fastapi, type(None)), (render_fastapi, GeneratedProject))
    ]
    source = shutil.copy2(SOURCE / "pets.yaml", root / "api.yaml")
    model = GenerateConfig(
        output=root / "models.py",
        input_file_type="openapi",
        target_python_version="3.11",
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
        output_model_type=DataModelType.PydanticV2BaseModel,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
    )
    config = fastapi_config({}, root)
    project = render_fastapi(source, model_config=model, config=config)
    lines.append(f"render {sorted({artifact.action for artifact in project.artifacts})}")
    for _ in range(2):
        result = generate_fastapi(source, model_config=model, config=config)
        files = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
        lines.append(f"generate returned {result}; files {files}")
    readme = root / PACKAGE / "README.md"
    original = readme.read_bytes()
    readme.write_text("# edited\n", encoding="utf-8")
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", UserWarning)
        result = generate_fastapi(source, model_config=model, config=config)
    lines.append(
        f"generate returned {result}; readme restored {readme.read_bytes() == original}; warnings {len(recorded)}"
    )
    return "\n".join(lines).replace(root.as_posix(), "<root>") + "\n"
