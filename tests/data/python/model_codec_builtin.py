"""Run the builtin model codecs of generated client packages over fixture cases, rendering each result as text."""

from __future__ import annotations

import ast
import dataclasses
import importlib
import json
import shutil
import sys
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, time
from pathlib import PurePath
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from datamodel_code_generator import SchemaParseError
from datamodel_code_generator.api_types import APIGenerationError
from tests.data.python.client_runtime import generate_client
from tests.data.python.generated_packages import forget_generated, import_generated

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

MANIFEST = ".dcg-target-manifest.json"
CLIENT = {"default_base_url": "https://codecs.invalid", "validation": {"response": "schema"}}


class _Unset:
    def __repr__(self) -> str:
        return "<unset>"


_UNSET = _Unset()


def _use_key(use: dict[str, Any]) -> str:
    parts = (use["owner"]["source"]["pointer"], use["role"], use["status"], use["location"], use["name"])
    return " ".join(str(part) for part in parts if part)


def _uses(root: Path, package: str) -> list[str]:
    """Return the use key of each binding in a generated package's manifest, in the order of its codecs."""
    manifest = json.loads((root / package / MANIFEST).read_text(encoding="utf-8"))
    return [_use_key(item["use_id"]) for item in manifest["bindings"]]


def generate_package(
    source: Path, fixture: dict[str, Any], root: Path, backend: str, config: dict[str, Any]
) -> list[str]:
    """Generate a fixture's client package, returning the diagnostics of a refusal or the error, or nothing."""
    options = dict(fixture.get("options", {}))
    if "extra_template_data" in options:
        options["extra_template_data"] = defaultdict(dict, options["extra_template_data"])
    if "scopes" in fixture:
        options["openapi_scopes"] = fixture["scopes"]
    root.mkdir(parents=True, exist_ok=True)
    try:
        generate_client(source, root, fixture["package"], backend, options, {**CLIENT, **config})
    except APIGenerationError as error:
        return [
            "APIGenerationError",
            *(f"diagnostic {item.code} {item.source_pointer}: {item.message}" for item in error.diagnostics),
        ]
    except SchemaParseError as error:
        return [f"SchemaParseError {error}"]
    return []


class GeneratedCodecs:
    """A generated client package imported for its model codecs, with the runtime modules its reports read."""

    def __init__(self, name: str, root: Path) -> None:
        """Import the package generated under a root, its bindings, and the runtime modules reports read."""
        self.name = name
        import_generated(name)
        self.bindings = importlib.import_module(f"{name}._generated.model_bindings")
        self.public = importlib.import_module(f"{name}.model_codecs")
        self.media = importlib.import_module(f"{name}._runtime.model_codecs.media")
        self.uses = _uses(root, name)

    def load(self, path: Path) -> Any:
        """Decode a fixture as the package's runtime decodes JSON, keeping every number exact."""
        return self.public.thaw_wire(self.media.decode_json(path.read_bytes()))

    def codec_of(self, index: int) -> Any:
        """Build the generated codec of one binding."""
        return getattr(self.bindings, f"codec_{index}")()

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
        return self.media.encode_json(value).decode()


@contextmanager
def imported(root: Path, name: str) -> Iterator[GeneratedCodecs]:
    """Import a package generated under a root, removing it and its models from the module cache afterwards."""
    sys.path.insert(0, str(root))
    try:
        yield GeneratedCodecs(name, root)
    finally:
        sys.path.remove(str(root))
        forget_generated(name)


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


def _result(package: GeneratedCodecs, value: object, *, wire_only: bool = False) -> str:
    public = package.public
    if isinstance(value, public.ModelValue) and wire_only:
        return f"model wire={package.json(value.wire)} extras={package.json(value.extras)}"
    if isinstance(value, public.ModelValue):
        fields_set = getattr(value.value, "model_fields_set", None)
        return (
            f"model {_native(value.value)}"
            + (f" set={sorted(fields_set)}" if fields_set is not None else "")
            + f" wire={package.json(value.wire)} presence={list(value.presence.pointers())}"
            + f" extras={package.json(value.extras)}"
        )
    if isinstance(value, public.ModelInput):
        issues = ",".join(f"{issue.code}@{issue.pointer}:{issue.field_id}" for issue in value.issues)
        return f"input issues={issues} wire={package.json(value.wire)} extras={package.json(value.extras)}"
    return f"wire={package.json(value)}"


def failure(package: GeneratedCodecs, error: Exception) -> str:
    """Name a codec failure by its class, with the code and pointer of each wire or native issue."""
    if isinstance(error, package.public.WireValidationError):
        return "WireValidationError " + ",".join(f"{issue.code}@{issue.instance_pointer}" for issue in error.issues)
    if isinstance(error, package.public.NativeValidationError):
        return "NativeValidationError " + ",".join(
            f"{issue.code}@{issue.pointer}{list(issue.native_path)}" for issue in error.issues
        )
    return f"{type(error).__name__} {error}"


def _context(package: GeneratedCodecs, case: dict[str, Any], binding: Any) -> Any:
    """Return the context of a case: its surface, the bound use, and any field the case overrides."""
    return package.public.CodecContext(**{
        "surface": str(case.get("surface", "server")),
        "direction": binding.direction,
        "schema_id": binding.schema_id,
        "operation_id": binding.operation_id,
        "media_type": binding.media_type,
        **case.get("context", {}),
    })


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
                return getattr(self.results[spec["result"]], spec["attr"])
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

    def wire(self, spec: object) -> object:
        if isinstance(spec, dict) and spec.keys() & {"call", "result", "nested", "py"}:
            return self.native(spec)
        return self.package.public.freeze_wire(spec)

    def run(self, case: dict[str, Any]) -> str:
        public = self.package.public
        codec = self.codecs[str(case["use"])]
        context = _context(self.package, case, codec.binding)
        value = self.wire(case.get("value"))
        presence = public.presence_of(case["presence"]) if "presence" in case else None
        result: object = None
        try:
            match case["op"]:
                case "decode":
                    result = codec.decode(value, context)
                case "from_wire":
                    result = codec.from_wire(value, context)
                case "encode":
                    result = codec.encode(value, context)
                case "snapshot":
                    result = codec.snapshot(value, context, presence=presence)
                case "native-outbound":
                    result = public.NativeOutboundCodec(codec, context).from_wire(value)
                case "envelope-outbound":
                    result = public.EnvelopeOutboundCodec(codec, context).from_wire(value)
                case "envelope-snapshot":
                    result = public.EnvelopeOutboundCodec(codec, context).snapshot(value, presence=presence)
                case "mutate":
                    target = self.results[str(case["target"])]
                    native = self.native(case.get("native")) if "native" in case else value
                    setattr(target.value, str(case["attr"]), native)
                    return f"mutated {case['attr']}"
                case "require":
                    return f"required {_native(self.results[str(case['target'])].require_model())}"
                case "echo":
                    native = codec.decode(value, context).require_model()
                    result = codec.encode(native, dataclasses.replace(context, surface="server"))
                case "serialize":
                    result = codec.serialize(value, context)
                case "check":
                    result = codec.serialize(value, context, validate=True)
                case "assemble":
                    result = codec.assemble(self.native(case["fields"]), context)
                case "convert":
                    converted = codec.convert(value, context)
                    self.results[str(case["name"])] = converted
                    return f"converted {_native(converted)}"
                case _:
                    msg = f"Unknown case operation: {case['op']}"
                    raise ValueError(msg)
        except public.CodecError as error:
            return failure(self.package, error)
        self.results[str(case["name"])] = result
        return _result(self.package, result, wire_only=self.wire_only)


def _codecs(package: GeneratedCodecs, lines: list[str] | None = None, *, strategies: bool = False) -> dict[str, Any]:
    """Build the generated codec of every binding, listing each binding or its failure when lines are given."""
    codecs: dict[str, Any] = {}
    for index, key in enumerate(package.uses):
        try:
            codecs[key] = codec = package.codec_of(index)
        except package.public.CodecError as error:
            if lines is not None:
                lines.append(f"codec {key}: {failure(package, error)}")
            continue
        if lines is not None:
            binding = codec.binding
            strategy = f" {binding.converter_strategy}" if strategies else ""
            models = [(model.symbol, model.native_kind, model.extra, model.open) for model in binding.models]
            lines.append(
                f"use {key}: {binding.projection_mode} {binding.native_kind} {binding.native_export}{strategy} "
                f"models={models}"
            )
    return codecs


def builtin_codec_report(source: Path, cases: Path, root: Path) -> str:
    """Generate a client package, import it, and run every case through the generated codec of its use.

    A fixture's `refusal` configuration first reports the diagnostics of a generation the target refuses; its
    `config` then generates the package that runs the cases.
    """
    fixture = json.loads(cases.read_text(encoding="utf-8"))
    package = fixture["package"]
    lines: list[str] = []
    if isinstance(refusal_config := fixture.get("refusal"), dict):
        refused = generate_package(source, fixture, root / "refused", fixture["backend"], refusal_config)
        lines.extend(f"refused {line}" for line in refused or ["nothing"])
    accepted = root / "accepted"
    if diagnostics := generate_package(source, fixture, accepted, fixture["backend"], fixture.get("config", {})):
        return _text([*lines, *diagnostics], package)
    with imported(accepted, package) as generated:
        codecs = _codecs(generated, lines, strategies=bool(fixture.get("strategies")))
        runner = _Runner(generated, codecs)
        lines.extend(f"{case['name']}: {runner.run(case)}" for case in generated.load(cases)["cases"])
    return _text(lines, package)


def backend_comparison_report(source: Path, cases: Path, root: Path) -> str:
    """Run one set of wire cases through the codecs generated for every backend, grouping backends that agree."""
    fixture = json.loads(cases.read_text(encoding="utf-8"))
    outcomes: dict[str, dict[str, str]] = {case["name"]: {} for case in fixture["cases"]}
    for backend in fixture["backends"]:
        package = f"{fixture['package']}_{backend.replace('.', '_').lower()}"
        directory = root / package
        if diagnostics := generate_package(source, {**fixture, "package": package}, directory, backend, {}):
            for results in outcomes.values():
                results[backend] = " ".join(diagnostics)
            continue
        with imported(directory, package) as generated:
            runner = _Runner(generated, _codecs(generated), wire_only=True)
            for case in generated.load(cases)["cases"]:
                outcomes[case["name"]][backend] = runner.run(case).replace(f"{package}_models", "models")
    lines = []
    for name, results in outcomes.items():
        groups: dict[str, list[str]] = {}
        for backend, outcome in results.items():
            groups.setdefault(outcome, []).append(backend)
        lines.extend(f"{name} [{', '.join(backends)}]: {outcome}" for outcome, backends in groups.items())
    return "\n".join(lines) + "\n"


class _Source:
    """Edit one generated module by replacing the source text of its syntax nodes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = path.read_bytes()
        self.tree = ast.parse(self.data)
        self.starts = [0]
        for line in self.data.splitlines(keepends=True):
            self.starts.append(self.starts[-1] + len(line))
        self.edits: list[tuple[int, int, str]] = []

    def span(self, node: ast.expr) -> tuple[int, int]:
        """Return the byte offsets of a node, whose columns count UTF-8 bytes."""
        end_line = node.end_lineno or node.lineno
        return self.starts[node.lineno - 1] + node.col_offset, self.starts[end_line - 1] + (node.end_col_offset or 0)

    def text(self, node: ast.expr) -> str:
        """Return the source text of a node."""
        start, end = self.span(node)
        return self.data[start:end].decode()

    def replace(self, node: ast.expr, text: str) -> None:
        """Replace the source text of a node when the module is saved."""
        self.edits.append((*self.span(node), text))

    def returned(self, name: str) -> ast.expr | None:
        """Return the expression the module-level function of a name returns."""
        function = next(node for node in self.tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
        return function.body[-1].value if isinstance(function.body[-1], ast.Return) else None

    def call(self, name: str) -> ast.Call:
        """Return the call the module-level function of a name returns."""
        if not isinstance(call := self.returned(name), ast.Call):
            msg = f"{name} does not return a call"
            raise TypeError(msg)
        return call

    def save(self, *appended: str) -> None:
        """Write the edited module, followed by appended statements."""
        data = self.data
        for start, end, text in sorted(self.edits, reverse=True):
            data = data[:start] + text.encode() + data[end:]
        self.path.write_bytes(data + "".join(f"{line}\n" for line in appended).encode())


def _literal(value: object) -> str:
    """Spell a fixture value as generated source: a node class by name, a list as a tuple, or a literal."""
    match value:
        case {"node": str()}:
            return f"{value['node']}()"
        case list():
            return repr(tuple(value))
        case _:
            pass
    return repr(value)


def _edit_bindings(path: Path, package: str, index: int, case: dict[str, Any]) -> None:
    """Edit the binding, models, and bundle of the generated `codec_<index>`, or patch its direction's view."""
    bindings = _Source(path)
    use, _, models, bundle = bindings.call(f"codec_{index}").args
    if isinstance(models, ast.Call) and isinstance(models.func, ast.Name):
        models = bindings.returned(models.func.id)
    if not isinstance(use, ast.Call) or not isinstance(models, ast.Dict):
        msg = f"codec_{index} does not build its binding inline"
        raise TypeError(msg)
    fields = {item.arg: item.value for item in use.keywords}
    for name, value in case.get("binding", {}).items():
        bindings.replace(fields[name], _literal(value))
    if "bundle" in case:
        bindings.replace(bundle, f"{case['bundle']}_bundle")
    swaps = case.get("models", {})
    if removed := {f"{package}_models:{key.rpartition(':')[2]}" for key, value in swaps.items() if not value}:
        kept = [
            f"{bindings.text(key)}: {bindings.text(value)}"
            for key, value in zip(models.keys, models.values, strict=True)
            if key is not None and ast.literal_eval(key) not in removed
        ]
        bindings.replace(models, "{" + ", ".join(kept) + "}")
    appended: tuple[str, ...] = ()
    if isinstance(patch := case.get("patch"), dict):
        direction = ast.literal_eval(fields["direction"])
        view = {item.arg: item.value for item in bindings.call(f"_{direction}_view").keywords}
        added = (
            f"SchemaPatch(uri={patch['uri']!r}, pointer={patch['pointer']!r}, keyword={patch['keyword']!r}, "
            f"value=freeze_wire({patch['value']!r}))"
        )
        if isinstance(patches := view.get("patches"), ast.Tuple):
            bindings.replace(patches, f"({''.join(f'{bindings.text(item)}, ' for item in patches.elts)}{added},)")
        else:
            bindings.replace(view["direction"], f"{bindings.text(view['direction'])}, patches=({added},)")
        appended = (
            "from .._runtime.model_codecs.schema import SchemaPatch",
            "from .._runtime.model_codecs.wire import freeze_wire",
        )
    bindings.save(*appended)


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
        path.write_text(path.read_text(encoding="utf-8") + "".join(f"{line}\n" for line in lines), encoding="utf-8")


def _startup(generated: Path, root: Path, package: str, case: dict[str, Any], values: dict[str, Any]) -> str:
    """Copy a generated package, edit it as one startup case says, then build and call its generated codec."""
    directory = root / case["name"]
    shutil.copytree(generated, directory, ignore=shutil.ignore_patterns("_runtime", ".dcg-api-state", "__pycache__"))
    index = _uses(directory, package).index(case["use"])
    _edit_bindings(directory / package / "_generated" / "model_bindings.py", package, index, case)
    _swap_models(directory / f"{package}_models.py", case)
    with imported(directory, package) as edited:
        public = edited.public
        try:
            codec = edited.codec_of(index)
            context = _context(edited, case, codec.binding)
            if "decode" in values:
                return _result(edited, codec.decode(public.freeze_wire(values["decode"]), context))
            if "convert" in values:
                return f"converted {_native(codec.convert(public.freeze_wire(values['convert']), context))}"
        except public.CodecError as error:
            return failure(edited, error)
    return "built"


def builtin_codec_startup_report(source: Path, cases: Path, root: Path) -> str:
    """Generate a client package, then edit a copy per case out of sync with itself and build the edited codec.

    Each case edits the generated files as a stale or hand-edited package would: it rebinds model names in the
    models module, or changes the binding, models, bundle, or directional view of one generated codec.
    """
    fixture = json.loads(cases.read_text(encoding="utf-8"))
    package = fixture["package"]
    generated = root / "generated"
    if diagnostics := generate_package(source, fixture, generated, fixture["backend"], fixture.get("config", {})):
        return _text(diagnostics, package)
    with imported(generated, package) as codecs:
        values = codecs.load(cases)["startup"]
    lines = [
        f"{case['name']}: {_startup(generated, root / 'cases', package, case, value)}"
        for case, value in zip(fixture["startup"], values, strict=True)
    ]
    return _text(lines, package)
