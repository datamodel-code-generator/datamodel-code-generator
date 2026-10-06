"""Run the builtin model codecs of generated client or server packages over fixture cases, rendering each result."""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import json
import re
import shutil
import warnings
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, time
from functools import reduce
from itertools import starmap
from operator import or_
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, get_args, get_type_hints

from pydantic import BaseModel, RootModel, TypeAdapter, ValidationError

from datamodel_code_generator import SchemaParseError
from datamodel_code_generator.api_types import APIGenerationError
from datamodel_code_generator.fastapi import generate_fastapi
from tests.data.python.client_generation import generate_client, model_config
from tests.data.python.fastapi_generation import fastapi_config
from tests.data.python.generated_packages import generated_root, import_generated, import_generated_codecs

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

CLIENT = {"default_base_url": "https://codecs.invalid"}


class _Unset:
    def __repr__(self) -> str:
        return "<unset>"


_UNSET = _Unset()


_LOCATIONS = frozenset({"path", "query", "querystring", "header", "cookie"})
_STATUS = re.compile(r"\d{3}|[1-5]XX|default")
_CODEC = re.compile(r"codec_(\d+)")


def _use_key(description: str) -> str:
    """Key a codec by the use its generated docstring describes: owner, role, status, location, and name."""
    head, _, selectors = description.removeprefix("Codec of ").removesuffix(").").partition(" (")
    _, *parts = selectors.split(" ")
    if parts and "/" in parts[-1]:
        parts.pop()
    status = parts.pop() if parts and _STATUS.fullmatch(parts[-1]) else None
    location = parts.pop(0) if parts and parts[0] in _LOCATIONS else None
    return " ".join(part for part in (head, status, location, " ".join(parts)) if part)


def _uses(root: Path, package: str) -> list[str]:
    """Return the use key of each codec a generated package binds, in the order of its codecs.

    A server binds a codec function and a client a codec attribute, each documented by its use.
    """
    body = ast.parse((root / package / "_generated" / "model_bindings.py").read_text(encoding="utf-8")).body
    codecs: dict[int, str] = {}
    for node, following in zip(body, [*body[1:], None], strict=True):
        if isinstance(node, ast.FunctionDef) and (found := _CODEC.fullmatch(node.name)):
            codecs[int(found[1])] = ast.get_docstring(node) or ""
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and (found := _CODEC.fullmatch(node.target.id))
            and isinstance(following, ast.Expr)
            and isinstance(following.value, ast.Constant)
        ):
            codecs[int(found[1])] = str(following.value.value)
    return [_use_key(codecs[index]) for index in range(len(codecs))]


def copied_input(source: Path, root: Path) -> Path:
    """Copy a fixture document under the generation root, so it shares a drive with the target on every platform."""
    (inputs := root / "inputs").mkdir(parents=True, exist_ok=True)
    return shutil.copy2(source, inputs / source.name)


def generate_package(
    source: Path, fixture: dict[str, Any], root: Path, backend: str, config: dict[str, Any], *, server: bool = False
) -> list[str]:
    """Generate a fixture's client or FastAPI server package, returning a refusal's diagnostics, the error, or nothing.

    Both targets keep the generated models module; `config` holds the settings of the chosen target.
    """
    options = dict(fixture.get("options", {}))
    if "extra_template_data" in options:
        options["extra_template_data"] = defaultdict(dict, options["extra_template_data"])
    if "scopes" in fixture:
        options["openapi_scopes"] = fixture["scopes"]
    root.mkdir(parents=True, exist_ok=True)
    package = fixture["package"]
    try:
        if server:
            generate_fastapi(
                source,
                model_config=model_config(root / f"{package}_models.py", backend, options),
                config=fastapi_config(
                    {"output": package, "package": package, "model_package": f"{package}_models", **config}, root
                ),
            )
        else:
            generate_client(source, root, package, backend, options, {**CLIENT, **config})
    except APIGenerationError as error:
        return [
            "APIGenerationError",
            *(f"diagnostic {item.code} {item.source_pointer}: {item.message}" for item in error.diagnostics),
        ]
    except SchemaParseError as error:
        return [f"SchemaParseError {error}"]
    return []


class GeneratedCodecs:
    """A generated client or server package imported for its model codecs, with the runtime modules its reports read."""

    def __init__(self, name: str, root: Path) -> None:
        """Import the package generated under a root, its bindings, and the runtime modules reports read."""
        self.name = name
        import_generated_codecs(name, root)
        self.bindings = importlib.import_module(f"{name}._generated.model_bindings")
        self.public = importlib.import_module(f"{name}.model_codecs")
        self.media = importlib.import_module(f"{name}._runtime.model_codecs.media")
        self.uses = _uses(root, name)

    def load(self, path: Path) -> Any:
        """Decode a fixture as the package's runtime decodes JSON, keeping every number exact."""
        return json.loads(path.read_bytes())

    def codec_of(self, index: int) -> Any:
        """Build the generated codec of one binding."""
        return getattr(self.bindings, f"codec_{index}")

    def symbol(self, key: str) -> str:
        """Name a fixture's symbol in the generated models module, which fixtures call `models`."""
        module, _, name = key.rpartition(":")
        return f"{self.name}_models:{name}" if module in {"", "models"} else key

    def export(self, symbol: str) -> Any:
        """Return the object one `module:name` symbol names."""
        module, _, name = symbol.partition(":")
        return getattr(importlib.import_module(module), name)

    def json(self, value: object) -> str:
        """Encode a wire value as the runtime encodes JSON."""
        return self.media.json_bytes(value).decode()


@contextmanager
def imported(root: Path, name: str) -> Iterator[GeneratedCodecs]:
    """Import a package generated under a root, removing it and its models from the module cache afterwards."""
    with generated_root(root, name):
        yield GeneratedCodecs(name, root)


def _text(lines: list[str], package: str) -> str:
    return "\n".join(lines).replace(f"{package}_models", "models") + "\n"


def _native(value: object) -> str:
    match value:
        case BaseModel():
            names = [*type(value).model_fields, *(value.model_extra or {})]
            return f"{type(value).__name__}({', '.join(f'{name}={_native(getattr(value, name))}' for name in names)})"
        case _ if dataclasses.is_dataclass(value) and not isinstance(value, type):
            fields = ", ".join(
                f"{field.name}={_native(getattr(value, field.name, _UNSET))}" for field in dataclasses.fields(value)
            )
            return f"{type(value).__name__}({fields})"
        case _ if hasattr(type(value), "__struct_fields__"):
            fields = ", ".join(f"{name}={_native(getattr(value, name))}" for name in type(value).__struct_fields__)
            return f"{type(value).__name__}({fields})"
        case set() | frozenset():
            return "{" + ", ".join(sorted(_native(item) for item in value)) + "}"
        case PurePath():
            return f"Path({str(value)!r})"
        case list():
            return "[" + ", ".join(_native(item) for item in value) + "]"
        case dict():
            return "{" + ", ".join(f"{key!r}: {_native(item)}" for key, item in value.items()) + "}"
        case _:
            pass
    return repr(value)


class _Runner:
    def __init__(self, package: GeneratedCodecs, codecs: dict[str, Any], *, wire_only: bool = False) -> None:
        self.package = package
        self.codecs = codecs
        self.wire_only = wire_only
        self.results: dict[str, Any] = {}

    def native(self, spec: object) -> object:
        match spec:
            case {"call": str(), "args": dict()}:
                native = self.package.export(self.package.symbol(spec["call"]))
                return native(**{key: self.native(value) for key, value in spec["args"].items()})
            case {"result": str(), "attr": str()}:
                value = self.results[spec["result"]]
                return value if spec["attr"] == "value" else getattr(value, spec["attr"])
            case {"result": str()}:
                return self.results[spec["result"]]
            case {"py": "object"}:
                return object()
            case {"py": "float", "text": str()}:
                return float(spec["text"])
            case {"py": "datetime", "text": str()}:
                return datetime.fromisoformat(spec["text"])
            case {"py": "time", "text": str()}:
                return time.fromisoformat(spec["text"])
            case {"py": "set", "items": list()}:
                return {self.native(item) for item in spec["items"]}
            case {"py": "tuple", "items": list()}:
                return tuple(self.native(item) for item in spec["items"])
            case {"py": "dict", "items": list()}:
                return {self.native(key): self.native(value) for key, value in spec["items"]}
            case {"nested": int()}:
                nested: list[object] = []
                for _ in range(spec["nested"]):
                    nested = [nested]
                return nested
            case dict():
                return {key: self.native(value) for key, value in spec.items()}
            case list():
                return [self.native(item) for item in spec]
            case _:
                pass
        return spec

    def run(self, case: dict[str, Any]) -> str:
        codec = self.codecs[str(case["use"])]
        try:
            value = self.native(case.get("value"))
            result: object = None
            match case["op"]:
                case "decode" | "from_wire":
                    result = codec.decode(self.package.media.json_bytes(value))
                case "encode" | "serialize":
                    encoded = json.dumps(
                        _unordered_json(value, json.loads(codec.encode(value))),
                        separators=(",", ":"), ensure_ascii=False,
                    )
                    return f"json={encoded}"
                case "mutate":
                    target = self.results[str(case["target"])]
                    setattr(target, str(case["attr"]), self.native(case.get("native", case.get("value"))))
                    return f"mutated {case['attr']}"
                case "require":
                    return f"native {_native(self.results[str(case['target'])])}"
                case "echo" | "resend":
                    result = codec.decode(self.package.media.json_bytes(value))
                case "assemble":
                    result = codec.assemble(self.native(case["fields"]))
                case "convert":
                    result = codec.convert(value)
                case _:
                    msg = f"Unknown native case operation: {case['op']}"
                    raise ValueError(msg)
            self.results[str(case["name"])] = result
            encoded = json.dumps(
                _unordered_json(result, codec.dump(result)), separators=(",", ":"), ensure_ascii=False, allow_nan=False
            )
            fields_set = getattr(result, "model_fields_set", None)
            return (
                f"native {_native(result)}"
                + (f" set={sorted(fields_set)}" if fields_set is not None else "")
                + f" json={encoded}"
            )
        except ValidationError as error:
            return "ValidationError " + ",".join(f"{item['type']}@{list(item['loc'])}" for item in error.errors())
        except RecursionError:
            return "RecursionError"
        except (*codec.errors, TypeError, ValueError, KeyError, AttributeError) as error:
            return type(error).__name__


def _codecs(package: GeneratedCodecs, lines: list[str] | None = None, *, strategies: bool = False) -> dict[str, Any]:
    """Read each statically constructed native codec, listing the backend when requested."""
    codecs = {}
    for index, key in enumerate(package.uses):
        codecs[key] = codec = package.codec_of(index)
        if lines is not None:
            lines.append(f"use {key}: {type(codec).__name__}")
    return codecs


def _native_cases(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    """Retain model operations; envelope and presence snapshots have no native surface."""
    retired = {"snapshot", "native-outbound", "envelope-outbound", "envelope-snapshot"}
    return [case for case in fixture["cases"] if case["op"] not in retired]


def _unordered_json(value: object, wire: Any) -> Any:
    """Sort only native set elements in an observed JSON report; retain ordered arrays and backend field names."""
    if isinstance(value, RootModel):
        return _unordered_json(value.root, wire)
    if isinstance(value, (set, frozenset)) and isinstance(wire, list):
        return sorted(wire, key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, (list, tuple)) and isinstance(wire, list):
        return list(starmap(_unordered_json, zip(value, wire, strict=True)))
    if isinstance(value, dict) and isinstance(wire, dict):
        return {key: _unordered_json(value.get(key), encoded) for key, encoded in wire.items()}
    if isinstance(wire, dict) and (
        isinstance(value, BaseModel) or dataclasses.is_dataclass(value) or hasattr(type(value), "__struct_fields__")
    ):
        if isinstance(value, BaseModel) or hasattr(type(value), "__pydantic_fields__"):
            fields = type(value).model_fields if isinstance(value, BaseModel) else type(value).__pydantic_fields__
            names = {(field.serialization_alias or field.alias or name): name for name, field in fields.items()}
        elif hasattr(type(value), "__struct_fields__"):
            names = dict(zip(type(value).__struct_encode_fields__, type(value).__struct_fields__, strict=True))
        else:
            names = {field.name: field.name for field in dataclasses.fields(value)}
        return {
            key: _unordered_json(getattr(value, names.get(key, key), None), encoded) for key, encoded in wire.items()
        }
    return wire


class _NativeServerCases:
    """Observe fixture operations through the generated FastAPI app and its native model annotations."""

    def __init__(self, server: Any, package: str) -> None:
        self.package = package
        self.results: dict[str, Any] = {}
        self.calls: dict[str, Any] = {}
        self.services = get_type_hints(server.create_app)
        services = {
            name: self
            for name, parameter in inspect.signature(server.create_app).parameters.items()
            if parameter.default is inspect.Parameter.empty and parameter.kind == inspect.Parameter.KEYWORD_ONLY
        }
        self.app = server.create_app(**services)
        router = server.build_router(**services)
        self.routes = {
            (route.path, method.lower()): route
            for route in router.routes
            if hasattr(route, "methods")
            for method in route.methods
        }

    def __getattr__(self, name: str) -> Any:
        from fastapi.responses import Response

        def handle(**values: Any) -> Response:
            self.calls.update(values)
            return Response(status_code=204)

        return handle

    def native(self, spec: object) -> object:
        if isinstance(spec, dict) and "call" in spec:
            module, _, name = spec["call"].rpartition(":")
            module = f"{self.package}_models" if module in {"", "models"} else module
            return getattr(importlib.import_module(module), name)(**{
                key: self.native(value) for key, value in spec["args"].items()
            })
        if isinstance(spec, dict) and "result" in spec:
            value = self.results[spec["result"]]
            return getattr(value, spec["attr"]) if spec.get("attr") not in {None, "value"} else value
        if isinstance(spec, dict) and spec.keys() & {"py", "nested"}:
            return self.literal(spec)
        if isinstance(spec, dict):
            return {key: self.native(value) for key, value in spec.items()}
        if isinstance(spec, list):
            return [self.native(value) for value in spec]
        return spec

    def literal(self, spec: dict[str, Any]) -> object:
        if "nested" in spec:
            value: list[object] = []
            for _ in range(spec["nested"]):
                value = [value]
            return value
        name = spec["py"]
        if name in {"object", "float", "datetime", "time"}:
            constructors = {
                "object": object,
                "float": float,
                "datetime": datetime.fromisoformat,
                "time": time.fromisoformat,
            }
            return constructors[name](spec["text"]) if "text" in spec else constructors[name]()
        items = spec["items"]
        if name == "dict":
            return {self.native(key): self.native(value) for key, value in items}
        constructor = {"set": set, "tuple": tuple}[name]
        return constructor(self.native(value) for value in items)

    def use(self, key: str) -> tuple[Any, object]:
        pointer, role, *parts = key.split()
        _, _, path, method = pointer.split("/")
        path = path.replace("~1", "/").replace("~0", "~")
        route = self.routes[path, method]
        if role == "response_body":
            status = parts[0]
            native_type = route.response_model if int(status) == route.status_code else route.responses[status]["model"]
        else:
            name = "body" if role == "request_body" else parts[-1]
            hints = get_type_hints(route.endpoint, include_extras=True)
            if name in hints:
                native_type = hints[name]
            else:
                name = re.sub(r"\W", "_", name).lower()
                service = next(service for service in self.services.values() if hasattr(service, route.name))
                native_type = get_type_hints(getattr(service, route.name))[name]
                members = tuple(
                    member for member in get_args(native_type) if getattr(member, "__name__", "") != "Unset"
                )
                if members:
                    native_type = reduce(or_, members)
        return route, native_type

    def request(self, route: Any, value: object) -> object:
        from fastapi.testclient import TestClient

        self.calls.clear()
        path = re.sub(r"\{[^}]+\}", "1", route.path)
        with TestClient(self.app) as api:
            response = api.request(next(iter(route.methods)), path, json=value)
        if response.status_code != 204:
            return f"HTTP {response.status_code} {response.text}"
        return self.calls["body"]

    def run(self, case: dict[str, Any]) -> str:
        route, native_type = self.use(case["use"])
        try:
            return self.operation(case, route, native_type)
        except ValidationError as error:
            return "ValidationError " + ",".join(f"{item['type']}@{list(item['loc'])}" for item in error.errors())
        except RecursionError:
            return "RecursionError"
        except (TypeError, ValueError, KeyError, AttributeError) as error:
            return f"{type(error).__name__} {error}"

    def operation(self, case: dict[str, Any], route: Any, native_type: object) -> str:
        adapter = TypeAdapter(native_type)
        value = self.native(case.get("value"))
        operation = case["op"]
        if operation == "mutate":
            setattr(self.results[case["target"]], case["attr"], self.native(case.get("native", case.get("value"))))
            return f"mutated {case['attr']}"
        if operation == "require":
            return f"native {_native(self.results[case['target']])}"
        if operation in {"assemble", "convert"}:
            value = self.native(case["fields"]) if operation == "assemble" else value
        if operation in {
            "decode",
            "from_wire",
            "convert",
            "assemble",
            "native-outbound",
            "envelope-outbound",
            "echo",
            "resend",
        }:
            if operation == "decode" and " request_body" in case["use"]:
                value = self.request(route, value)
                if isinstance(value, str) and value.startswith("HTTP "):
                    return value
            else:
                value = adapter.validate_python(value)
        self.results[case["name"]] = value
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter("always", UserWarning)
            encoded = adapter.dump_json(value, by_alias=True, exclude_unset=True).decode()
        encoded = json.dumps(_unordered_json(value, json.loads(encoded)), separators=(",", ":"), ensure_ascii=False)
        fields_set = getattr(value, "model_fields_set", None)
        return (
            f"native {_native(value)}"
            + (f" set={sorted(fields_set)}" if fields_set is not None else "")
            + f" json={encoded}"
            + (f" warnings={[str(item.message) for item in observed]}" if observed else "")
        )


def _native_server_report(root: Path, package: str, fixture: dict[str, Any]) -> list[str]:
    """Run the retained recipes against the generated native app and model types, without codec sidecars."""
    with generated_root(root, package):
        runner = _NativeServerCases(import_generated(package), package)
        return [f"{case['name']}: {runner.run(case)}" for case in fixture["cases"]]


def builtin_codec_report(source: Path, cases: Path, root: Path, *, server: bool = False) -> str:
    """Run cases through native server models or the generated client codec of their use.

    A fixture's `refusal` configuration first reports the diagnostics of a generation the target refuses; its
    `config` then generates the package that runs the cases.
    """
    source = copied_input(source, root)
    fixture = json.loads(cases.read_text(encoding="utf-8"))
    package, backend = fixture["package"], fixture["backend"]
    lines: list[str] = []
    if isinstance(refusal_config := fixture.get("refusal"), dict):
        refused = generate_package(source, fixture, root / "refused", backend, refusal_config, server=server)
        lines.extend(f"refused {line}" for line in refused or ["nothing"])
    accepted = root / "accepted"
    if diagnostics := generate_package(source, fixture, accepted, backend, fixture.get("config", {}), server=server):
        return _text([*lines, *diagnostics], package)
    if server:
        return _text([*lines, *_native_server_report(accepted, package, fixture)], package)
    try:
        with imported(accepted, package) as generated:
            codecs = _codecs(generated, lines, strategies=bool(fixture.get("strategies")))
            runner = _Runner(generated, codecs)
            lines.extend(f"{case['name']}: {runner.run(case)}" for case in _native_cases(generated.load(cases)))
    except TypeError:
        lines.append("native startup TypeError")
    return _text(lines, package)


def backend_comparison_report(source: Path, cases: Path, root: Path) -> str:
    """Run one set of wire cases through the codecs generated for every backend, grouping backends that agree."""
    source = copied_input(source, root)
    fixture = json.loads(cases.read_text(encoding="utf-8"))
    outcomes: dict[str, dict[str, str]] = {case["name"]: {} for case in _native_cases(fixture)}
    for backend in fixture["backends"]:
        package = f"{fixture['package']}_{backend.replace('.', '_').lower()}"
        directory = root / package
        if diagnostics := generate_package(source, {**fixture, "package": package}, directory, backend, {}):
            for results in outcomes.values():
                results[backend] = " ".join(diagnostics)
            continue
        try:
            with imported(directory, package) as generated:
                runner = _Runner(generated, _codecs(generated), wire_only=True)
                for case in _native_cases(generated.load(cases)):
                    outcomes[case["name"]][backend] = runner.run(case).replace(f"{package}_models", "models")
        except TypeError:
            for results in outcomes.values():
                results[backend] = "native startup TypeError"
    lines = []
    for name, results in outcomes.items():
        groups: dict[str, list[str]] = {}
        for backend, outcome in results.items():
            groups.setdefault(outcome, []).append(backend)
        lines.extend(f"{name} [{', '.join(backends)}]: {outcome}" for outcome, backends in groups.items())
    return "\n".join(lines) + "\n"


def _swap_models(path: Path, case: dict[str, Any]) -> None:
    """Rebind generated model names at the end of the models module, as a hand-edited module would."""
    lines = []
    for key, value in case.get("models", {}).items():
        name = key.rpartition(":")[2]
        module, _, symbol = value.rpartition(":")
        if module in {"", "models"} and symbol:
            lines.append(f"{name} = {symbol}")
        elif symbol:
            lines.append(f"from {module} import {symbol} as {name}")
    if lines:
        path.write_bytes(path.read_bytes() + "".join(f"{line}\n" for line in lines).encode())


def _startup(generated: Path, root: Path, package: str, case: dict[str, Any], values: dict[str, Any]) -> str:
    """Observe a retained model substitution through the native codec's own construction."""
    directory = root / case["name"]
    shutil.copytree(generated, directory, ignore=shutil.ignore_patterns("_runtime", "__pycache__"))
    _swap_models(directory / f"{package}_models.py", case)
    try:
        with imported(directory, package) as edited:
            operation = "decode" if "decode" in values else "convert"
            runner = _Runner(edited, _codecs(edited))
            return runner.run({**case, "op": operation, "value": values.get(operation)})
    except TypeError:
        return "native startup TypeError"


def _native_startup(generated: Path, root: Path, package: str, case: dict[str, Any]) -> str:
    """Apply retained model substitutions and start the native app; codec-sidecar edits have no native surface."""
    directory = root / case["name"]
    shutil.copytree(generated, directory)
    _swap_models(directory / f"{package}_models.py", case)
    with generated_root(directory, package):
        runner = _NativeServerCases(import_generated(package), package)
        removed = [key for key in ("binding", "bundle", "patch") if key in case]
        lines = [f"native app routes={len(runner.app.openapi()['paths'])}"]
        if removed:
            lines.append(f"removed codec-sidecar edits={removed}")
        if "models" in case:
            models = importlib.import_module(f"{package}_models")
            lines.append(
                "models=" + repr({key: getattr(models, key.rpartition(":")[2]).__name__ for key in case["models"]})
            )
        if "decode" in case or "convert" in case:
            operation = "decode" if "decode" in case else "convert"
            lines.append(runner.run({**case, "op": operation, "value": case[operation]}))
        return " ".join(lines)


def builtin_codec_startup_report(source: Path, cases: Path, root: Path, *, server: bool = False) -> str:
    """Edit generated copies and start their native app or client codec.

    Each case edits the generated files as a stale or hand-edited package would: it rebinds model names in the
    models module, or changes the binding, models, bundle, or directional view of one generated codec.
    """
    source = copied_input(source, root)
    fixture = json.loads(cases.read_text(encoding="utf-8"))
    package, backend = fixture["package"], fixture["backend"]
    generated = root / "generated"
    if diagnostics := generate_package(source, fixture, generated, backend, fixture.get("config", {}), server=server):
        return _text(diagnostics, package)
    if server:
        return _text(
            [
                f"{case['name']}: {_native_startup(generated, root / 'cases', package, case)}"
                for case in fixture["startup"]
                if case["name"] not in {"converter", "backend", "missing-model"}
            ],
            package,
        )
    lines = [
        f"{case['name']}: {_startup(generated, root / 'cases', package, case, case)}"
        for case in fixture["startup"]
        if "models" in case and case.keys().isdisjoint({"binding", "bundle", "patch"})
    ]
    return _text(lines, package)
