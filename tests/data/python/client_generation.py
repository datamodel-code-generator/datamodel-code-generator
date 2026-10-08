"""Render client targets from OpenAPI fixtures and report their files, public operations, and failures."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import warnings
from contextlib import AbstractContextManager, nullcontext
from dataclasses import fields
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, TypeAlias, get_type_hints

from datamodel_code_generator import DataModelType, Error, GenerateConfig, InvalidFileFormatError, generate
from datamodel_code_generator._api_generation import generate_target, render_target
from datamodel_code_generator._api_types import (
    APIGenerationError,
    Diagnostic,
    OperationRef,
)
from datamodel_code_generator._client.config import (
    BodyFieldName,
    ClientGenerationConfig,
    ClientOperationConfig,
    IdempotencyMetadata,
    ParameterName,
    ResourceName,
    RuntimeOperationMetadata,
)
from datamodel_code_generator._client.target import ClientTarget
from datamodel_code_generator._client.templates import ClientTemplates
from datamodel_code_generator.enums import OpenAPIScope
from datamodel_code_generator.format import Formatter
from tests.data.python.client_protocol_records import RECORDS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from datamodel_code_generator._api_generation import TargetRender, TargetRequest

SOURCE = Path(__file__).parents[1] / "generation_platform" / "client"
PACKAGE = "client"
_DEFERRED_MODULES = (
    "datamodel_code_generator._api_generation",
    "datamodel_code_generator._client.target",
    "datamodel_code_generator.json_config",
)
_PUBLIC_NAMES_PROBE = (
    "import json, sys\n"
    "import datamodel_code_generator.client as client\n"
    "for name in client.__all__:\n"
    "    getattr(client, name)\n"
    "print(json.dumps([name for name in sys.argv[1:] if name in sys.modules]))\n"
)
_DIGEST = re.compile(r"[0-9a-f]{64}")
Modules: TypeAlias = dict[tuple[str, ...], str]


def _selector(value: object) -> object:
    return OperationRef(**value) if isinstance(value, dict) else value


def _operation(value: object) -> object:
    if not isinstance(value, dict):
        return value
    converted: dict[str, Any] = {**value, "ref": _selector(value.get("ref"))}
    if isinstance(names := value.get("parameter_names"), list):
        converted["parameter_names"] = tuple(
            ParameterName(**item) if isinstance(item, dict) else item for item in names
        )
    if isinstance(fields := value.get("body_field_names"), list):
        converted["body_field_names"] = tuple(
            BodyFieldName(**item) if isinstance(item, dict) else item for item in fields
        )
    if isinstance(runtime := value.get("runtime"), dict):
        statuses = runtime.get("success_statuses", ())
        encodings = runtime.get("accepted_content_encodings", ())
        idempotency = runtime.get("idempotency")
        converted["runtime"] = RuntimeOperationMetadata(**{
            **runtime,
            "success_statuses": tuple(statuses) if isinstance(statuses, list) else statuses,
            "accepted_content_encodings": tuple(encodings) if isinstance(encodings, list) else encodings,
            "idempotency": IdempotencyMetadata(**idempotency) if isinstance(idempotency, dict) else idempotency,
        })
    return ClientOperationConfig(**converted)


def _working_directory(case: dict[str, Any], root: Path) -> AbstractContextManager[object]:
    """Run a case from its root when it asks, importing `contextlib.chdir` only then, since it needs Python 3.11."""
    if not case.get("cwd"):
        return nullcontext()
    from contextlib import chdir

    return chdir(root)


def _protocols(value: object, root: Path) -> object:
    """Return a file under the case root, a record fixture, a path relative to the working directory, or a value."""
    if isinstance(value, str):
        return root / value
    if not isinstance(value, dict):
        return value
    if "python" in value:
        return RECORDS[value["python"]]
    return Path(value["relative"]) if "relative" in value else value["raw"]


def client_config(values: dict[str, Any], root: Path) -> ClientGenerationConfig:
    """Build a client configuration from JSON fixture values; `protocols` names a file, record, or raw value."""
    values = {"output": PACKAGE, "package": PACKAGE, "model_package": "models", **values}
    converted: dict[str, Any] = {}
    for key, value in values.items():
        match key:
            case "output":
                converted[key] = root / value
            case "resource_names" if isinstance(value, list):
                converted[key] = tuple(ResourceName(**item) if isinstance(item, dict) else item for item in value)
            case "operations" if isinstance(value, list):
                converted[key] = tuple(_operation(item) for item in value)
            case "protocols":
                converted[key] = _protocols(value, root)
            case _:
                converted[key] = value
    return ClientGenerationConfig(**converted)


def model_config(output: Path, backend: str, options: Mapping[str, Any]) -> GenerateConfig:
    """Build the model settings of a client case: one models module for a backend, with fixture option values."""
    return GenerateConfig(**{
        "output": output,
        "input_file_type": "openapi",
        "target_python_version": "3.11",
        "openapi_scopes": [OpenAPIScope.Schemas, OpenAPIScope.Api],
        "output_model_type": DataModelType(backend),
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
        **options,
    })


def generate_client(
    source: Path,
    root: Path,
    package: str,
    backend: str,
    model: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> None:
    """Generate a client package and its `<package>_models` module under a root, from fixture option values."""
    generate_target(
        source,
        model_config=model_config(root / f"{package}_models.py", backend, model or {}),
        config=client_config(
            {"output": package, "package": package, "model_package": f"{package}_models", **(config or {})}, root
        ),
        generator=ClientTarget(),
    )


def _diagnostic(item: Diagnostic) -> str:
    location = " ".join(str(part) for part in (item.stage, item.option_path, item.source_pointer) if part is not None)
    return f"  {item.code} {location}: {item.message}"


def copy_references(case: dict[str, Any], root: Path) -> None:
    """Copy the documents a case references beside its input, keeping their relative paths."""
    for reference in case.get("references", ()):
        (root / reference).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE / reference, root / reference)


def _prepare_input(case: dict[str, Any], root: Path) -> Path:
    """Copy a case's unchanged source and reference documents into its generation root."""
    root.mkdir(parents=True, exist_ok=True)
    source = shutil.copy2(SOURCE / case["input"], root / case["input"])
    copy_references(case, root)
    return source


def _render(
    case: dict[str, Any],
    backend: str,
    root: Path,
    modules: Modules,
    *,
    documents: dict[str, str] | None = None,
    builtin_sources: bool = False,
) -> list[str]:
    source = _prepare_input(case, root)
    model = case.get("model", {})
    if builtin_sources:
        shutil.copytree(ClientTemplates.BUILTIN, root / "builtin-sources" / "client")
        model = {**model, "custom_template_dir": root / "builtin-sources"}
    try:
        with _working_directory(case, root):
            project = (generate_target if case.get("publish") else render_target)(
                source,
                model_config=model_config(root / "models.py", backend, model),
                config=client_config(case.get("config", {}), root),
                generator=ClientTarget(),
            )
    except APIGenerationError as error:
        lines = ["  APIGenerationError", *(_diagnostic(item) for item in error.diagnostics)]
        if case.get("publish"):
            kept = {case["input"], *case.get("references", ())}
            files = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
            lines.append(f"  files {[path for path in files if path not in kept]}")
        return lines
    except Error as error:
        return [f"  Error: {error}"]
    lines: list[str] = []
    for artifact in project.artifacts:
        path, content = (root / artifact.path).relative_to(root), artifact.content or b""
        if documents is not None and path.suffix in {".md", ".toml"}:
            documents[path.as_posix()] = content.decode("utf-8")
        match path.suffix, path.parts:
            case _, parts if "_runtime" in parts:
                continue
            case ".py", parts:
                modules[parts] = content.decode(case.get("model", {}).get("encoding", "utf-8"))
            case _:
                pass
        lines.append(f"  {artifact.action} {path.as_posix()}")
    lines.append(f"  dependencies {list(project.dependencies)}")
    return lines


class _BindingDiagnosticsTarget(ClientTarget):
    """Render a client package and keep the binding diagnostics of the batch it renders from."""

    def __init__(self) -> None:
        super().__init__()
        self.binding_diagnostics: list[str] = []

    def render(self, request: TargetRequest) -> TargetRender:
        """Keep the batch's binding diagnostics, then render as the client target does."""
        self.binding_diagnostics = [
            " ".join(["binding", item.code, *(f"{key}={value}" for key, value in item.details)])
            for item in request.batch.diagnostics
        ]
        return super().render(request)


def render_client(
    source: Path,
    root: Path,
    backend: str,
    model: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    models: str = "models.py",
    binding_diagnostics: bool = False,
) -> tuple[list[str], Modules]:
    """Render a document's client package under a root, returning the refusal or diagnostics and the Python modules.

    The models go to the module or package path models names under the root. With binding_diagnostics, the
    diagnostics also hold those of the model binding batch, which no package shows.
    """
    target = _BindingDiagnosticsTarget()
    try:
        project = render_target(
            source,
            model_config=model_config(root / models, backend, model),
            config=client_config(dict(config), root),
            generator=target,
        )
    except APIGenerationError as error:
        lines = ["APIGenerationError", *(_diagnostic(item).strip() for item in error.diagnostics)]
        return [*lines, *(target.binding_diagnostics if binding_diagnostics else ())], {}
    modules: Modules = {
        path.parts: (artifact.content or b"").decode()
        for artifact in project.artifacts
        if (path := artifact.path.relative_to(root)).suffix == ".py" and "_runtime" not in path.parts
    }
    return target.binding_diagnostics if binding_diagnostics else [], modules


def _rendered(case: dict[str, Any], root: Path) -> dict[str, bytes]:
    """Render a case into its own root and return every artifact by its path relative to that root."""
    root.mkdir(parents=True)
    copy_references(case, root)
    project = render_target(
        shutil.copy2(SOURCE / case["input"], root / case["input"]),
        model_config=model_config(root / "models.py", "pydantic_v2.BaseModel", case.get("model", {})),
        config=client_config(case.get("config", {}), root),
        generator=ClientTarget(),
    )
    return {artifact.path.relative_to(root).as_posix(): artifact.content or b"" for artifact in project.artifacts}


def client_helper_spelling_report(first: str, second: str, root: Path) -> str:
    """Render two spellings of the same helper settings and report each file whose bytes differ between them."""
    cases = json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))
    left, right = (_rendered(cases[name], root / name) for name in (first, second))
    return "".join([
        f"# {first} and {second}\n",
        f"  same files {left == right}\n",
        *(f"  differs {path}\n" for path in sorted(left.keys() | right.keys()) if left.get(path) != right.get(path)),
    ])


def client_render(case_name: str, root: Path, *, builtin_sources: bool = False) -> tuple[str, dict[str, Modules]]:
    """Render one fixture for each of its backends, returning a report and every backend's Python modules.

    A case's `modules` keeps all modules (`true`), none (`false`), the listed ones,
    or maps each backend to one of these. With builtin_sources, a custom template directory holds a copy of the
    builtin client templates, so that every role renders from its Jinja source instead of its compiled renderer.
    """
    case = json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))[case_name]
    lines = [f"# {case.get('expected', case_name)}"]
    rendered: dict[str, Modules] = {}
    selected = case.get("modules", True)
    for backend in case.get("backends", ["pydantic_v2.BaseModel"]):
        lines.append(f"render {backend}")
        lines.extend(
            _render(
                case,
                backend,
                root / (name := backend.replace(".", "_")),
                modules := {},
                builtin_sources=builtin_sources,
            )
        )
        if modules and (kept := selected[backend] if isinstance(selected, dict) else selected):
            rendered[name] = modules if kept is True else {parts: modules[parts] for parts in map(tuple, kept)}
    return "\n".join(lines).replace(root.resolve().as_posix(), "<root>") + "\n", rendered


def client_cli_arguments(pyproject: Path) -> list[str]:
    """Spell the [tool.datamodel-codegen] keys of a pyproject.toml as the command-line options that set them."""
    import tomllib

    arguments: list[str] = []
    for key, value in tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["datamodel-codegen"].items():
        match value:
            case True:
                arguments.append(f"--{key}")
            case list():
                arguments.extend((f"--{key}", *value))
            case dict():
                arguments.extend((f"--{key}", json.dumps(value)))
            case _:
                arguments.extend((f"--{key}", str(value)))
    return arguments


def prepare_client_case(case_name: str, root: Path) -> None:
    """Copy a case's source and reference documents into a root."""
    _prepare_input(json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))[case_name], root)


def client_cli_modules(case_name: str, root: Path) -> tuple[str, Modules]:
    """Return the package a case's renders compare with and the Python modules a run wrote under root that it keeps.

    The modules are those the case keeps of its first backend.
    """
    case = json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))[case_name]
    modules: Modules = {
        parts: path.read_text(encoding="utf-8")
        for path in root.rglob("*.py")
        if "_runtime" not in (parts := path.relative_to(root).parts)
    }
    selected = case.get("modules", True)
    kept = selected[case.get("backends", ["pydantic_v2.BaseModel"])[0]] if isinstance(selected, dict) else selected
    return case.get("expected", case_name), modules if kept is True else {
        parts: modules[parts] for parts in map(tuple, kept)
    }


def client_api_report(root: Path) -> str:
    """Resolve the entry points' annotations, then render, generate twice, and generate over an edited file.

    The helpers are a JSON object whose documents resolve against the working directory, and the helper records
    load from the public module on first use: a fresh interpreter that reads every public name imports neither the
    JSON option schemas nor the generation modules.
    """
    import subprocess
    import sys

    import datamodel_code_generator.client as client_api
    from datamodel_code_generator._client import protocols

    hints = {"input_": client_api.GenerationInput, "model_config": GenerateConfig, "config": ClientGenerationConfig}
    functions = (
        (client_api.generate_client, type(None)),
        (client_api.render_client, client_api.GeneratedProject),
    )
    lines = [
        f"{function.__name__} resolves {sorted(hints)}: {get_type_hints(function) == {**hints, 'return': result}}"
        for function, result in functions
    ]
    lines.append(f"records {client_api.PaginationHelper is protocols.PaginationHelper}")
    probe = subprocess.run(
        [sys.executable, "-c", _PUBLIC_NAMES_PROBE, *_DEFERRED_MODULES], capture_output=True, text=True, check=True
    )
    lines.append(f"reading every public name imports {json.loads(probe.stdout)} of {list(_DEFERRED_MODULES)}")
    try:
        client_api.Missing  # noqa: B018
    except AttributeError as error:
        lines.append(f"AttributeError: {error}")
    source = shutil.copy2(SOURCE / "cli" / "options.yaml", root / "api.yaml")
    helpers = json.loads((SOURCE / "cli" / "protocols.json").read_text(encoding="utf-8"))
    helpers["pets.all"]["operation"] = {"pointer": "/paths/~1pets/get", "document": "api.yaml"}
    model = model_config(root / "models.py", "pydantic_v2.BaseModel", {})
    config = client_config({"protocols": {"raw": helpers}}, root)
    with _working_directory({"cwd": True}, root):
        project = client_api.render_client(source, model_config=model, config=config)
        lines.append(f"render {sorted({artifact.action for artifact in project.artifacts})}")
        for _ in range(2):
            result = client_api.generate_client(source, model_config=model, config=config)
            files = sorted(
                path.relative_to(root).as_posix()
                for path in root.rglob("*")
                if path.is_file() and "_runtime" not in path.parts
            )
            lines.append(f"generate returned {result}; files {files}")
        original = (owned := root / PACKAGE / "_client.py").read_bytes()
        owned.write_text("# edited\n", encoding="utf-8")
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always", UserWarning)
            result = client_api.generate_client(source, model_config=model, config=config)
    restored = owned.read_bytes() == original
    lines.append(f"generate returned {result}; edited file restored {restored}; warnings {len(recorded)}")
    return "\n".join(lines) + "\n"


def client_model_parity_report(case_name: str, root: Path, rendered: dict[str, Modules]) -> str:
    """Report whether ordinary generation matches each backend's already-rendered model bytes."""
    case = json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))[case_name]
    matches: list[bool] = []
    for backend in case.get("backends", ["pydantic_v2.BaseModel"]):
        attempt = root / (name := backend.replace(".", "_"))
        attempt.mkdir(parents=True)
        source = _prepare_input(case, attempt)
        with _working_directory(case, attempt):
            generate(source, config=model_config(attempt / "models.py", backend, case.get("model", {})))
        ordinary = {path.relative_to(attempt).parts: path.read_bytes() for path in attempt.rglob("*.py")}
        encoding = case.get("model", {}).get("encoding", "utf-8")
        target = {parts: content.encode(encoding) for parts, content in rendered.get(name, {}).items()}
        matches.append(bool(ordinary) and ordinary == target)
    return f"{bool(matches) and all(matches)}\n"


def cyclic_input_failure(error: Exception, *, source: str, pointer: str, location: tuple[int, int]) -> str:
    """Report only the two handled cyclic-input refusals for the seven retained YAML cases.

    A loader may reject the fixed anchor before the target can report E_INPUT_CYCLE. Raw recursion errors,
    other parse errors, model errors, and the removed BindingCaptureError remain failures.
    """
    if isinstance(error, APIGenerationError) and len(error.diagnostics) == 1:
        diagnostic = error.diagnostics[0]
        if (diagnostic.code, diagnostic.severity, diagnostic.stage, diagnostic.message, diagnostic.source_pointer) == (
            "E_INPUT_CYCLE",
            "error",
            "input",
            "The input document contains a cyclic mapping or sequence",
            pointer,
        ) and Path(diagnostic.source_uri or "").name == source:
            return "cyclic YAML input rejected"
    if isinstance(error, InvalidFileFormatError):
        original = error.original_error
        if type(original) is Error:
            original = original.__cause__
        if (
            type(original).__module__ == "_ryaml"
            and type(original).__name__ == "InvalidYamlError"
            and str(original) == f"recursion limit exceeded at line {location[0]} column {location[1]}"
            and (error.source is None or Path(error.source).name == source)
        ):
            return "cyclic YAML input rejected"
    raise error


def client_cyclic_metadata_report(source: Path, root: Path, model: Mapping[str, Any]) -> str:
    """Keep cyclic session metadata on the public target path without accepting the old capture crash."""
    lines = ["# session-cyclic-metadata", "render pydantic_v2.BaseModel default"]
    try:
        project = render_target(
            source,
            model_config=model_config(root / "models.py", "pydantic_v2.BaseModel", model),
            config=client_config({"default_base_url": "https://bindings.invalid"}, root),
            generator=ClientTarget(),
        )
    except (APIGenerationError, InvalidFileFormatError) as error:
        lines.append(
            "  "
            + cyclic_input_failure(
                error,
                source=source.name,
                pointer="/paths/~1items/get/externalDocs/extra",
                location=(8, 21),
            )
        )
    else:
        lines.append(f"  render succeeded with {len(project.artifacts)} artifacts")
    files = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_relative_to(root / "inputs")
    )
    lines.append(f"  files {files}")
    return "\n".join(lines) + "\n"


def _cyclic_reference_models(source: Path, output: Path, backend: str, case: dict[str, Any]) -> list[str]:
    """Keep ordinary model output or the exact loader refusal of the retained cyclic reference."""
    lines = [f"model-only {backend}"]
    try:
        generate(source, config=model_config(output, backend, case.get("model", {})))
    except InvalidFileFormatError as error:
        lines.append(
            "  InvalidFileFormatError: "
            + cyclic_input_failure(
                error, source="input-cycle-reference-model.yaml", pointer="/x-cycle/self", location=(6, 10)
            )
        )
        files = sorted(
            path.relative_to(output.parent).as_posix()
            for path in output.parent.rglob("*")
            if path.is_file() and path.name not in {case["input"], *case.get("references", ())}
        )
        lines.append(f"  files {files}")
    else:
        lines.extend(f"  | {line}" for line in output.read_text(encoding="utf-8").splitlines())
    return lines


def client_input_report(case_name: str, root: Path) -> str:
    """Render and publish an input for each backend, reporting diagnostics and the resulting files."""
    case = json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))[case_name]
    lines = [f"# {case_name}"]
    cycles = {
        "input-cycle-dict": ("input-cycle-dict.yaml", "/x-cycle/self", (14, 10)),
        "input-cycle-list": ("input-cycle-list.yaml", "/x-cycle/0", (14, 10)),
        "input-cycle-mutual": ("input-cycle-mutual.yaml", "/x-outer/nested/child/back~1to~0outer/0", (15, 11)),
        "input-cycle-reference": ("input-cycle-reference-model.yaml", "/x-cycle/self", (6, 10)),
    }
    for backend in case["backends"]:
        for name, entry in (("render", render_target), ("generate", generate_target)):
            attempt = root / backend.replace(".", "_") / name
            attempt.mkdir(parents=True)
            source = shutil.copy2(SOURCE / case["input"], attempt / case["input"])
            copy_references(case, attempt)
            config = client_config(case.get("config", {}), attempt)
            lines.append(f"{name} {backend}")
            try:
                entry(
                    source,
                    model_config=model_config(attempt / "models.py", backend, case.get("model", {})),
                    config=config,
                    generator=ClientTarget(),
                )
            except (APIGenerationError, InvalidFileFormatError) as error:
                if case_name in cycles:
                    filename, pointer, location = cycles[case_name]
                    failure = cyclic_input_failure(error, source=filename, pointer=pointer, location=location)
                    lines.extend(
                        (
                            "  APIGenerationError",
                            *(_diagnostic(item) for item in error.diagnostics),
                            *(f"  source {item.source_uri}" for item in error.diagnostics),
                        )
                        if case_name == "input-cycle-reference" and isinstance(error, APIGenerationError)
                        else ("  " + failure, f"  source ../{filename}")
                    )
                elif isinstance(error, APIGenerationError):
                    lines.extend(("  APIGenerationError", *(_diagnostic(item) for item in error.diagnostics)))
                    lines.extend(f"  source {item.source_uri}" for item in error.diagnostics)
                else:
                    raise
            else:
                lines.append("  accepted")
            files = sorted(
                _DIGEST.sub("<sha256>", path.relative_to(attempt).as_posix())
                for path in attempt.rglob("*")
                if path.is_file()
                and path.relative_to(attempt).as_posix() not in {case["input"], *case.get("references", ())}
            )
            lines.extend((
                f"  files {[path for path in files if '_runtime' not in Path(path).parts]}",
                f"  runtime files {sum('_runtime' in Path(path).parts for path in files)}",
            ))
        if case.get("model_only"):
            attempt = root / backend.replace(".", "_") / "models-only"
            attempt.mkdir(parents=True)
            source = shutil.copy2(SOURCE / case["input"], attempt / case["input"])
            copy_references(case, attempt)
            output = attempt / "models.py"
            if case_name == "input-cycle-reference":
                lines.extend(_cyclic_reference_models(source, output, backend, case))
            else:
                generate(source, config=model_config(output, backend, case.get("model", {})))
                lines.append(f"model-only {backend}")
                lines.extend(f"  | {line}" for line in output.read_text(encoding="utf-8").splitlines())
    return "\n".join(lines) + "\n"


def client_documentation_report(case_name: str, root: Path, *, builtin_sources: bool = False) -> str:
    """Report generated Markdown and packaging files for a finalized client selection.

    With builtin_sources, the files render from a copy of the builtin client templates, as in `client_render`.
    """
    case = json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))[case_name]
    documents: dict[str, str] = {}
    lines = _render(case, "pydantic_v2.BaseModel", root, {}, documents=documents, builtin_sources=builtin_sources)
    return (
        "\n"
        .join((
            *lines,
            *(f"\n# artifact {path}\n{content}" for path, content in documents.items()),
        ))
        .replace(root.resolve().as_posix(), "<root>")
        .rstrip("\n")
        + "\n"
    )


def _setting(value: object, root: Path) -> str:
    match value:
        case tuple():
            items = [_setting(item, root) for item in value]
            return f"({', '.join(items)}{',' if len(items) == 1 else ''})"
        case Path():
            return repr(PurePosixPath(value.relative_to(root).as_posix()))
        case _:
            return repr(value)


def client_config_report(case_name: str, root: Path) -> str:
    """Construct one client configuration from Python values and report it or its diagnostics."""
    case = json.loads((SOURCE / "configs.json").read_text(encoding="utf-8"))[case_name]
    try:
        config = client_config(case, root)
    except APIGenerationError as error:
        return "\n".join(["APIGenerationError", *(_diagnostic(item) for item in error.diagnostics)]) + "\n"
    except TypeError as error:
        return f"TypeError: {error}\n"
    return "".join(f"{item.name}={_setting(getattr(config, item.name), root)}\n" for item in fields(config))


def client_metadata_cycle_diagnostic_report(backend: str, root: Path, *, external_sequence: bool = False) -> str:
    """Render and publish fixed metadata cycles through the public target entry points."""
    import yaml

    source = SOURCE.parent / "binding" / "session-cyclic-metadata.yaml"
    lines = [f"# cyclic metadata {backend}"]
    for name, entry in (("render", render_target), ("generate", generate_target)):
        attempt = root / name
        attempt.mkdir(parents=True)
        if external_sequence:
            inputs = attempt / "inputs"
            inputs.mkdir()
            for filename in ("session-cyclic-sequence.yaml", "session-cyclic-sequence-library.yaml"):
                shutil.copy2(source.parent / filename, inputs / filename)
            document = inputs / "session-cyclic-sequence.yaml"
        else:
            document = yaml.safe_load(source.read_text(encoding="utf-8"))
        lines.append(name)
        try:
            entry(
                document,
                model_config=model_config(attempt / "models.py", backend, {}),
                config=client_config({}, attempt),
                generator=ClientTarget(),
            )
        except (APIGenerationError, InvalidFileFormatError) as error:
            if external_sequence:
                lines.append(
                    cyclic_input_failure(
                        error,
                        source="session-cyclic-sequence-library.yaml",
                        pointer="/path-item/get/externalDocs/extra/0",
                        location=(5, 14),
                    )
                )
            elif isinstance(error, APIGenerationError):
                lines.extend((
                    type(error).__name__,
                    json.dumps(
                        [
                            {field.name: getattr(item, field.name) for field in fields(item)}
                            for item in error.diagnostics
                        ],
                        indent=2,
                        sort_keys=True,
                    ),
                ))
            else:
                raise
        else:
            lines.append("generation succeeded")
        files = sorted(
            path.relative_to(attempt).as_posix()
            for path in attempt.rglob("*")
            if path.is_file() and not path.is_relative_to(attempt / "inputs")
        )
        lines.append(f"files {files}")
    return "\n".join(lines) + "\n"


def _regenerate(source: Path, root: Path) -> list[str]:
    """Generate one regeneration fixture, reporting warnings and the files it leaves."""
    shutil.copy2(source, root / "api.yaml")
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", UserWarning)
        generate_client(root / "api.yaml", root, "pets", "pydantic_v2.BaseModel")
    paths = sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file())
    return [
        *(f"  file {path.as_posix()}" for path in paths if "_runtime" not in path.parts),
        f"  runtime files {sum('_runtime' in path.parts for path in paths)}",
        *(f"  {item.category.__name__}: {item.message}" for item in recorded),
    ]


def client_regeneration_report(root: Path) -> str:
    """Regenerate a package from a changed API over user code, an edited generated file, and a foreign file.

    The second version adds an operation in a new resource, removes the only operation of another, and renames a
    parameter. Like model generation, one run overwrites the edited file and the foreign file, which takes a path of
    the new resource, keeps the user module, and leaves the modules of the removed resource in place.
    """
    source = SOURCE / "regeneration"
    extensions, generated, foreign = (
        root / "pets" / "extensions.py",
        root / "pets" / "_client.py",
        root / "pets" / "resources" / "orders" / "__init__.py",
    )
    lines = ["# generate v1", *_regenerate(source / "v1.yaml", root)]
    shutil.copy2(source / "extensions.py", extensions)
    digest = hashlib.sha256(extensions.read_bytes()).hexdigest()
    generated.write_text(edited := f"{generated.read_text(encoding='utf-8')}# Edited by hand.\n", encoding="utf-8")
    foreign.parent.mkdir(parents=True)
    foreign.write_text(written := "# Someone else's module.\n", encoding="utf-8")
    lines.extend((
        "# add pets/extensions.py, edit pets/_client.py, and write pets/resources/orders/__init__.py",
        "# generate v2",
        *_regenerate(source / "v2.yaml", root),
        f"  pets/extensions.py unchanged {hashlib.sha256(extensions.read_bytes()).hexdigest() == digest}",
        f"  pets/_client.py still edited {generated.read_text(encoding='utf-8') == edited}",
        f"  pets/resources/orders/__init__.py still foreign {foreign.read_text(encoding='utf-8') == written}",
    ))
    return "\n".join(lines).replace(root.resolve().as_posix(), "<root>") + "\n"
