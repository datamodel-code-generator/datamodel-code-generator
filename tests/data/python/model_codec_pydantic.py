"""Run bound Pydantic v2 model codecs over really generated models, rendering each case's result as text."""

from __future__ import annotations

import dataclasses
import importlib
import sys
from dataclasses import replace
from typing import TYPE_CHECKING

from pydantic import BaseModel

from datamodel_code_generator import DataModelType, GenerateConfig, InputFileType, OpenAPIScope
from datamodel_code_generator._generation_contract import OperationId
from datamodel_code_generator._openapi_codec_plan import plan_model_codecs
from datamodel_code_generator._openapi_wire_plan import plan_wire
from datamodel_code_generator._runtime.model_codecs.context import CodecContext
from datamodel_code_generator._runtime.model_codecs.media import decode_json, encode_json
from datamodel_code_generator._runtime.model_codecs.errors import (
    CodecError,
    NativeValidationError,
    WireValidationError,
)
from datamodel_code_generator._runtime.model_codecs.outbound import EnvelopeOutboundCodec, NativeOutboundCodec
from datamodel_code_generator._runtime.model_codecs.pydantic_v2 import PydanticModelCodec
from datamodel_code_generator._runtime.model_codecs.schema import SchemaBundle, SchemaPatch
from datamodel_code_generator._runtime.model_codecs.values import ModelInput, ModelValue
from datamodel_code_generator._runtime.model_codecs.wire import freeze_wire, presence_of, thaw_wire
from tests.data.python.generation_session_inputs import generate_product

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _use_key(use: object) -> str:
    owner = use.owner.use_site.pointer if isinstance(use.owner, OperationId) else use.owner.pointer
    return " ".join(str(part) for part in (owner, use.role, use.status, use.location, use.name) if part)


def _json(value: object) -> str:
    return encode_json(value).decode()


def _native(value: object) -> str:
    match value:
        case BaseModel():
            names = [*type(value).model_fields, *(value.model_extra or {})]
            return f"{type(value).__name__}({', '.join(f'{name}={_native(getattr(value, name))}' for name in names)})"
        case _ if dataclasses.is_dataclass(value) and not isinstance(value, type):
            fields = ", ".join(f"{field.name}={_native(getattr(value, field.name))}" for field in dataclasses.fields(value))
            return f"{type(value).__name__}({fields})"
        case set() | frozenset():
            return "{" + ", ".join(sorted(_native(item) for item in value)) + "}"
        case list():
            return "[" + ", ".join(_native(item) for item in value) + "]"
        case dict():
            return "{" + ", ".join(f"{key!r}: {_native(item)}" for key, item in value.items()) + "}"
        case _:
            return repr(value)


def _result(value: object) -> str:
    match value:
        case ModelValue():
            fields_set = getattr(value.value, "model_fields_set", None)
            return (
                f"model {_native(value.value)}"
                + (f" set={sorted(fields_set)}" if fields_set is not None else "")
                + f" wire={_json(value.wire)} presence={list(value.presence.pointers())} extras={_json(value.extras)}"
            )
        case ModelInput():
            issues = ",".join(f"{issue.code}@{issue.pointer}:{issue.field_id}" for issue in value.issues)
            return f"input issues={issues} wire={_json(value.wire)} extras={_json(value.extras)}"
        case _:
            return f"wire={_json(value)}"


def _failure(error: CodecError) -> str:
    match error:
        case WireValidationError(issues=issues):
            return "WireValidationError " + ",".join(f"{issue.code}@{issue.instance_pointer}" for issue in issues)
        case NativeValidationError(issues=issues):
            return "NativeValidationError " + ",".join(
                f"{issue.code}@{issue.pointer}{list(issue.native_path)}" for issue in issues
            )
        case _:
            return f"{type(error).__name__} {error}"


class _Runner:
    def __init__(self, package: str, codecs: dict[str, PydanticModelCodec[object]]) -> None:
        self.package = package
        self.codecs = codecs
        self.results: dict[str, object] = {}

    def native(self, spec: object) -> object:
        match spec:
            case {"call": str() as name, "args": dict() as args}:
                module, _, symbol = name.rpartition(":")
                native = getattr(importlib.import_module(f"{self.package}.{module or 'models'}"), symbol)
                return native(**{key: self.native(value) for key, value in args.items()})
            case {"result": str() as name, "attr": str() as attr}:
                return getattr(self.results[name], attr)
            case {"result": str() as name}:
                return self.results[name]
            case {"py": "object"}:
                return object()
            case {"py": "set", "items": list() as items}:
                return {self.native(item) for item in items}
            case {"py": "tuple", "items": list() as items}:
                return tuple(self.native(item) for item in items)
            case {"py": "dict", "items": list() as items}:
                return {self.native(key): self.native(value) for key, value in items}
            case {"nested": int() as depth}:
                nested: list[object] = []
                for _ in range(depth):
                    nested = [nested]
                return nested
            case dict():
                return {key: self.native(value) for key, value in spec.items()}
            case list():
                return [self.native(item) for item in spec]
            case _:
                return spec

    def wire(self, spec: object) -> object:
        if isinstance(spec, dict) and spec.keys() & {"call", "result", "nested"}:
            return self.native(spec)
        return freeze_wire(spec)

    def run(self, case: dict[str, object]) -> str:
        codec = self.codecs[str(case["use"])]
        binding = codec.binding
        context = CodecContext(
            surface=str(case.get("surface", "server")),
            direction=binding.direction,
            schema_id=binding.schema_id,
            operation_id=binding.operation_id,
            media_type=binding.media_type,
        )
        if isinstance(overrides := case.get("context"), dict):
            context = CodecContext(**{
                "surface": context.surface,
                "direction": context.direction,
                "schema_id": context.schema_id,
                "operation_id": context.operation_id,
                "media_type": context.media_type,
                **overrides,
            })
        value = self.wire(case.get("value"))
        presence = presence_of(case["presence"]) if "presence" in case else None
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
                    result = NativeOutboundCodec(codec, context).from_wire(value)
                case "envelope-outbound":
                    result = EnvelopeOutboundCodec(codec, context).from_wire(value)
                case "envelope-snapshot":
                    result = EnvelopeOutboundCodec(codec, context).snapshot(value, presence=presence)
                case "mutate":
                    target = self.results[str(case["target"])]
                    setattr(target.value, str(case["attr"]), self.native(case.get("native")) if "native" in case else value)
                    return f"mutated {case['attr']}"
                case "require":
                    return f"required {_native(self.results[str(case['target'])].require_model())}"
        except CodecError as error:
            return _failure(error)
        self.results[str(case["name"])] = result
        return _result(result)


def _prepare(
    source: Path, fixture: dict[str, object], root: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[object, object, str]:
    backend = str(fixture["backend"])
    package = str(fixture["package"])
    product, _ = generate_product(
        source,
        GenerateConfig(
            input_file_type=InputFileType.OpenAPI,
            openapi_scopes=[OpenAPIScope(scope) for scope in fixture.get("scopes", ["api"])],
            formatters=[],
            output_model_type=DataModelType(backend),
            **fixture.get("options", {}),
        ),
    )
    try:
        wire = plan_wire(product.batch, product.source_lease)
        plan = plan_model_codecs(product.batch, wire, fixture.get("planned_backend", backend))
        directory = root / package
        directory.mkdir()
        (directory / "__init__.py").write_text("")
        for artifact in product.artifacts:
            directory.joinpath(*artifact.path).write_bytes(artifact.content)
    finally:
        product.close()
    monkeypatch.syspath_prepend(str(root))
    return plan, wire, package


def _export(package: str, key: str) -> object:
    module, _, symbol = key.partition(":")
    return getattr(importlib.import_module(module if module.startswith("tests.") else f"{package}.{module}"), symbol)


def _forget(package: str, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [name for name in sys.modules if name == package or name.startswith(f"{package}.")]:
        monkeypatch.delitem(sys.modules, name)


def pydantic_codec_report(source: Path, cases: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Generate models, plan their codecs, import the generated package, and run every case in order."""
    fixture = thaw_wire(decode_json(cases.read_bytes()))
    plan, wire, package = _prepare(source, fixture, root, monkeypatch)
    lines = [f"diagnostic {item.code} {item.source.pointer}: {item.message}" for item in plan.diagnostics]
    bundles = {view.direction: SchemaBundle(wire.resources, view) for view in wire.views}
    codecs: dict[str, PydanticModelCodec[object]] = {}
    try:
        for use, binding in plan.bindings:
            lines.append(
                f"use {_use_key(use)}: {binding.projection_mode} {binding.native_kind} {binding.native_export} "
                f"models={[(model.symbol, model.native_kind, model.extra, model.open) for model in binding.models]}"
            )
            if binding.native_export is not None:
                codecs[_use_key(use)] = PydanticModelCodec(
                    binding,
                    _export(package, binding.native_export),
                    {model.symbol: _export(package, model.symbol) for model in binding.models},
                    bundles[binding.direction],
                )
        runner = _Runner(package, codecs)
        lines.extend(f"{case['name']}: {runner.run(case)}" for case in fixture["cases"])
    finally:
        _forget(package, monkeypatch)
    return "\n".join(lines) + "\n"


def pydantic_codec_startup_report(source: Path, cases: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Build codecs from deliberately mismatched bindings, bundles, and native types, naming each failure."""
    fixture = thaw_wire(decode_json(cases.read_bytes()))
    plan, wire, package = _prepare(source, fixture, root, monkeypatch)
    bindings = {_use_key(use): binding for use, binding in plan.bindings}
    bundles = {view.direction: SchemaBundle(wire.resources, view) for view in wire.views}
    lines = []
    try:
        for case in fixture["startup"]:
            binding = bindings[case["use"]]
            if isinstance(changes := case.get("binding"), dict):
                binding = replace(binding, **changes)
            models = {model.symbol: _export(package, model.symbol) for model in binding.models}
            models.update({key: _export(package, value) if value else None for key, value in case.get("models", {}).items()})
            try:
                bundle = bundles[case.get("bundle", binding.direction)]
                if isinstance(patch := case.get("patch"), dict):
                    view = next(view for view in wire.views if view.direction == binding.direction)
                    bundle = SchemaBundle(
                        wire.resources,
                        replace(view, patches=(*view.patches, SchemaPatch(**{**patch, "value": freeze_wire(patch["value"])}))),
                    )
                codec = PydanticModelCodec(
                    binding,
                    _export(package, binding.native_export or ""),
                    {key: value for key, value in models.items() if value is not None},
                    bundle,
                )
                result = "built"
                if "decode" in case:
                    context = CodecContext(
                        surface="server",
                        direction=binding.direction,
                        schema_id=binding.schema_id,
                        operation_id=binding.operation_id,
                        media_type=binding.media_type,
                    )
                    result = _result(codec.decode(freeze_wire(case["decode"]), context))
                lines.append(f"{case['name']}: {result}")
            except CodecError as error:
                lines.append(f"{case['name']}: {_failure(error)}")
    finally:
        _forget(package, monkeypatch)
    return "\n".join(lines) + "\n"
