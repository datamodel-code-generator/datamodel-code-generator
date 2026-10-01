"""Generate servers or clients with fixture codec declarations, import them, and run cases through their bindings.

Server-surface fixtures generate a FastAPI server; client-surface ones generate a client. A fixture's `refusal` run
first reports the diagnostics of a generation the target refuses, with another input, extra declarations, or other
settings; the fixture's own declarations and `config` then generate the package that runs the cases. The report names
the adapter each generated accessor attaches, read from the generated bindings.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any

from datamodel_code_generator import DataModelType, GenerateConfig
from datamodel_code_generator.api_types import APIGenerationError
from datamodel_code_generator.enums import OpenAPIScope
from datamodel_code_generator.fastapi import generate_fastapi
from datamodel_code_generator.format import Formatter
from tests.data.python.client_generation import generate_client
from tests.data.python.codec_declarations import declaration
from tests.data.python.fastapi_generation import fastapi_config
from tests.data.python.generated_packages import generated_root, import_generated_codecs

if TYPE_CHECKING:
    from pathlib import Path

PACKAGE = "adapted"
MODELS = f"{PACKAGE}_models"
MANIFEST = ".dcg-target-manifest.json"
ACCESSOR = re.compile(r"(codec|validator|parameter)_(\d+)")
KINDS = {"codec": "model", "validator": "schema", "parameter": "parameter"}
SETTINGS = {"codec_adapters": "adapters", "builtin_codec_compatibility": "compatibility", "export_bindings": "exports"}
_ISOLATED = """
import json
import sys

root, blocked, cases = sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3])
for name in blocked:
    sys.modules[name] = None
sys.path.insert(0, root)
import adapted._generated.model_bindings as bindings
import adapted_models as models
from adapted.model_codecs import thaw_wire

for name, accessor, operation, value in cases:
    codec = getattr(bindings, accessor)()
    if operation == "outbound":
        result = codec.from_wire(value)
    else:
        result = codec.snapshot(getattr(models, value["model"])(**value["args"]))
    print(f"{name}: model {result.value!r} wire={json.dumps(thaw_wire(result.wire))}")
print(f"loaded {sorted(module for module in sys.modules if module.split('.')[0] in blocked and sys.modules[module])}")
"""


def _use_key(use: dict[str, Any]) -> str:
    projection = "" if use["projection"] == "value" else use["projection"]
    parts = (use["owner"]["source"]["pointer"], use["role"], use["status"], use["location"], use["name"], projection)
    return " ".join(str(part) for part in parts if part)


def _use_keys(uses: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Name each bound use for the report, adding the media type to names several uses share, then list repeats."""
    keys = [_use_key(use) for use in uses]
    shared = Counter(keys)
    keys = [f"{key} {use['media']}" if shared[key] > 1 else key for key, use in zip(keys, uses, strict=True)]
    return keys, sorted(key for key, count in Counter(keys).items() if count > 1)


def _invalid(kind: str, data: dict[str, object]) -> str:
    try:
        declaration(kind, data)
    except ValueError as error:
        return str(error)
    return "accepted"


def _generate(source: Path, fixture: dict[str, Any], root: Path, run: dict[str, Any]) -> list[str]:
    """Generate the fixture's server or client package, returning the diagnostics of a refusal, or nothing."""
    declarations = {kind: list(items) for kind, items in fixture.get("declarations", {}).items()}
    for kind, items in run.get("declarations", {}).items():
        declarations.setdefault(kind, []).extend(items)
    config = {key: declarations[kind] for key, kind in SETTINGS.items() if kind in declarations}
    config.update(fixture.get("config", {}) | run.get("config", {}))
    root.mkdir(parents=True, exist_ok=True)
    name = run.get("input", source.name)
    spec = shutil.copy2(source.with_name(name), root / name)
    backend = fixture.get("backend", "pydantic_v2.BaseModel")
    model = {
        "openapi_scopes": [OpenAPIScope(scope) for scope in fixture.get("scopes", ["schemas", "api"])],
        **fixture.get("options", {}),
    }
    try:
        if fixture.get("surface") == "client":
            generate_client(
                spec, root, PACKAGE, backend, model, {"default_base_url": "https://codecs.invalid", **config}
            )
        else:
            generate_fastapi(
                spec,
                model_config=GenerateConfig(
                    output=root / f"{MODELS}{'' if fixture.get('modular') else '.py'}",
                    input_file_type="openapi",
                    target_python_version="3.11",
                    output_model_type=DataModelType(backend),
                    disable_timestamp=True,
                    formatters=[Formatter.BUILTIN],
                    **model,
                ),
                config=fastapi_config({"output": PACKAGE, "package": PACKAGE, "model_package": MODELS, **config}, root),
            )
    except APIGenerationError as error:
        return [
            "APIGenerationError",
            *(f"diagnostic {item.code} {item.source_pointer or ''}: {item.message}" for item in error.diagnostics),
        ]
    return []


def _called(call: ast.Call) -> str:
    return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")


def _attached(path: Path) -> tuple[dict[int, str], dict[int, list[tuple[str, str]]]]:
    """Read each use's projection mode and the adapters its generated accessors attach from the bindings module."""
    projections: dict[int, str] = {}
    adapters: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for function in ast.parse(path.read_bytes()).body:
        if not isinstance(function, ast.FunctionDef) or (found := ACCESSOR.fullmatch(function.name)) is None:
            continue
        kind, index = found[1], int(found[2])
        names: list[str] = []
        excluded: list[str] = []
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            keywords = {item.arg: item.value for item in call.keywords}
            match _called(call):
                case "AdapterManifest":
                    names.append(ast.literal_eval(keywords["name"]))
                case "SchemaAdapterValidator" if isinstance(value := keywords.get("excluded"), ast.Call):
                    excluded = sorted(ast.literal_eval(value.args[0]))
                case _:
                    pass
            if kind == "codec" and isinstance(mode := keywords.get("projection_mode"), ast.Constant):
                projections.setdefault(index, mode.value)
        shown = f" excluded={excluded}" if kind == "validator" else ""
        adapters[index].extend((name, f"{KINDS[kind]}{shown}") for name in names)
    return projections, adapters


class _Package:
    """A generated package imported for its model bindings, with the report name of each bound use."""

    def __init__(self, root: Path) -> None:
        import_generated_codecs(PACKAGE, root)
        self.bindings = importlib.import_module(f"{PACKAGE}._generated.model_bindings")
        self.public = importlib.import_module(f"{PACKAGE}.model_codecs")
        self.media = importlib.import_module(f"{PACKAGE}._runtime.model_codecs.media")
        self.manifest = json.loads((root / PACKAGE / MANIFEST).read_text(encoding="utf-8"))["bindings"]
        self.keys, self.repeated = _use_keys([item["use_id"] for item in self.manifest])
        self.indexes = {key: index for index, key in enumerate(self.keys)}
        self.projections, self.adapters = _attached(root / PACKAGE / "_generated" / "model_bindings.py")

    def accessor(self, kind: str, index: int) -> str | None:
        """Return the name of a use's accessor of one kind, if the bindings render it."""
        return name if hasattr(self.bindings, name := f"{kind}_{index}") else None

    def load(self, path: Path) -> Any:
        """Decode a fixture as the package's runtime decodes JSON, keeping every number exact."""
        return self.public.thaw_wire(self.media.decode_json(path.read_bytes()))

    def json(self, value: object) -> str:
        """Encode a wire value as the runtime encodes JSON."""
        return self.media.encode_json(value).decode()

    def uses(self, *, bindings: bool) -> list[str]:
        """List each bound use's accessors, strategy, projection, and adapters, and with `bindings` its binding."""
        lines = [f"duplicate report keys: {self.repeated}"] if self.repeated else []
        for index, (key, item) in enumerate(zip(self.keys, self.manifest, strict=True)):
            accessors = " ".join(str(self.accessor(kind, index)) for kind in ("codec", "outbound", "parameter"))
            lines.append(f"use {key}: {accessors} {item['converter_strategy']} {self.projections[index]}")
            lines.extend(f"adapter {name} {key}: {detail}" for name, detail in self.adapters[index])
            if bindings:
                lines.append(f"binding {key}: {self.binding(index)}")
        return lines

    def binding(self, index: int) -> str:
        """Describe the binding of a use's built codec."""
        binding = getattr(self.bindings, f"codec_{index}")().binding
        export = binding.native_export
        export = export if isinstance(export, str) else f"{export.module}:{export.symbol}"
        return f"{export} {binding.native_kind} {binding.converter_strategy} {binding.projection_mode}"


class _Runner:
    def __init__(self, package: _Package) -> None:
        self.package = package
        self.results: dict[str, object] = {}

    def native(self, spec: object) -> object:
        match spec:
            case {"model": str() as name, "args": dict() as args}:
                return getattr(importlib.import_module(MODELS), name)(**args)
            case {"construct": str() as name, "args": dict() as args}:
                return getattr(importlib.import_module(MODELS), name).model_construct(**args)
            case {"result": str() as name}:
                return self.results[name].value
            case {"snapshot": str() as name}:
                return self.results[name]
            case _:
                pass
        return spec

    def result(self, value: object) -> str:
        package = self.package
        public = package.public
        match value:
            case public.ModelValue():
                native = value.value
                shown = native.model_dump() if hasattr(native, "model_dump") else native
                return (
                    f"model {type(native).__name__} {shown!r} wire={package.json(value.wire)} "
                    f"presence={list(value.presence.pointers())}"
                )
            case public.ModelInput():
                issues = ",".join(f"{issue.code}@{issue.pointer}:{issue.field_id}" for issue in value.issues)
                return f"input issues={issues} wire={package.json(value.wire)}"
            case public.FragmentContribution():
                fragments = ", ".join(f"{fragment.name!r}={fragment.value!r}" for fragment in value.ordered_fragments)
                return f"fragments {value.location} [{fragments}]"
            case public.QueryStringContribution():
                return f"querystring {value.raw_query!r}"
            case _ if value is public.UNSET:
                return "UNSET"
            case _:
                pass
        return f"wire={package.json(value)}"

    def failure(self, error: Exception) -> str:
        public = self.package.public
        match error:
            case public.WireValidationError():
                return "WireValidationError " + ",".join(
                    f"{issue.code}@{issue.instance_pointer}" for issue in error.issues
                )
            case public.NativeValidationError():
                return "NativeValidationError " + ",".join(f"{issue.code}@{issue.pointer}" for issue in error.issues)
            case _:
                pass
        cause = f" (from {type(error.__cause__).__name__})" if error.__cause__ else ""
        return f"{type(error).__name__} {error}{cause}"

    def raw(self, case: dict[str, Any]) -> object:
        public = self.package.public
        return public.RawParameter(
            location=case["location"],
            fragments=tuple(
                public.ParameterFragment(name=None if name is None else name.encode(), value=text.encode())
                for name, text in case.get("fragments", ())
            ),
            raw_query=case["query"].encode() if "query" in case else None,
        )

    def call(self, case: dict[str, Any]) -> object:
        """Run one case's operation on an accessor of its use, returning the result."""
        bindings, public = self.package.bindings, self.package.public
        index = self.package.indexes[case["use"]]
        context = getattr(bindings, f"CONTEXT_{index}")
        operation, value = case["op"], case.get("value")
        result: object = None
        match operation:
            case "build":
                getattr(bindings, f"codec_{index}")()
            case "decode" | "from_wire" | "convert":
                result = getattr(getattr(bindings, f"codec_{index}")(), operation)(value, context)
            case "encode" | "serialize" | "assemble":
                result = getattr(getattr(bindings, f"codec_{index}")(), operation)(self.native(value), context)
            case "outbound":
                result = getattr(bindings, f"outbound_{index}")().from_wire(value)
            case "snapshot":
                presence = public.presence_of(case["presence"]) if "presence" in case else None
                result = getattr(bindings, f"outbound_{index}")().snapshot(self.native(value), presence=presence)
            case "parameter-encode":
                result = getattr(bindings, f"parameter_{index}")().encode(public.freeze_wire(value), context)
            case "parameter-decode":
                result = getattr(bindings, f"parameter_{index}")().decode(self.raw(case), context)
            case _:
                msg = f"Unknown case operation: {operation}"
                raise ValueError(msg)
        return result

    def run(self, case: dict[str, Any]) -> str:
        try:
            result = self.call(case)
        except self.package.public.CodecError as error:
            return self.failure(error)
        if case["op"] == "build":
            return "built"
        self.results[case.get("name", "")] = result
        return self.result(result)


def _isolated(root: Path, package: _Package, blocked: list[str], cases: list[dict[str, Any]]) -> list[str]:
    """Run outbound cases in a fresh interpreter that blocks the given modules, listing any it still loads."""
    calls = [[case["name"], f"outbound_{package.indexes[case['use']]}", case["op"], case["value"]] for case in cases]
    result = subprocess.run(
        [sys.executable, "-c", _ISOLATED, str(root), json.dumps(blocked), json.dumps(calls)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        msg = f"The isolated codec run exited with {result.returncode}:\n{result.stderr}"
        raise RuntimeError(msg)
    return result.stdout.splitlines()


def adapter_codec_report(source: Path, cases: Path, root: Path) -> str:
    """Generate a package with the fixture's declarations, import it, and run every case in order."""
    fixture = json.loads(cases.read_text(encoding="utf-8"))
    lines = [f"invalid {kind}: {_invalid(kind, data)}" for kind, data in fixture.get("invalid", ())]
    if isinstance(refusal := fixture.get("refusal"), dict):
        refused = _generate(source, fixture, root / "refused", refusal)
        lines.extend(f"refused {line}" for line in refused or ["nothing"])
    accepted = root / "accepted"
    if refused := _generate(source, fixture, accepted, {}):
        return "\n".join([*lines, *refused]) + "\n"
    modules = source.parent / "modules"
    shutil.copytree(modules, accepted, ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    with generated_root(accepted, PACKAGE, *(path.name.removesuffix(".py") for path in modules.iterdir())):
        package = _Package(accepted)
        lines.extend(package.uses(bindings=bool(fixture.get("bindings"))))
        if fixture.get("source"):
            generated = accepted / PACKAGE
            lines.extend(
                f"--- {name}\n{generated.joinpath(name).read_text(encoding='utf-8').rstrip()}"
                for name in ("_generated/model_bindings.py", "model_codecs.py")
            )
        if blocked := fixture.get("blocked"):
            lines.extend(_isolated(accepted, package, blocked, fixture["cases"]))
        else:
            runner = _Runner(package)
            lines.extend(f"{case['name']}: {runner.run(case)}" for case in package.load(cases).get("cases", ()))
    return "\n".join(lines) + "\n"
