"""Generate a package with rendered model bindings, register fixture adapters, and run cases through it."""

from __future__ import annotations

import importlib
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType, GenerateConfig, InputFileType, OpenAPIScope
from datamodel_code_generator._codec_declarations import (
    BuiltinCodecCompatibility,
    CodecAdapterRegistration,
    CodecDeclarations,
    ModelExportBinding,
    OperationRef,
    SchemaDirectionalUse,
    SchemaRef,
    TypeUseRef,
)
from datamodel_code_generator._generation_contract import OperationId
from datamodel_code_generator._openapi_codec_plan import plan_model_codecs
from datamodel_code_generator._openapi_codec_render import render_model_bindings, render_model_codecs
from datamodel_code_generator._openapi_wire_plan import plan_wire
from datamodel_code_generator._runtime.model_codecs import adapters, capabilities
from datamodel_code_generator._runtime.model_codecs.media import decode_json, encode_json
from datamodel_code_generator._runtime.model_codecs.wire import thaw_wire
from tests.data.python.generation_session_inputs import generate_product

if TYPE_CHECKING:
    from types import ModuleType

    import pytest

RUNTIME = Path(adapters.__file__).parent


def _use_key(use: object) -> str:
    owner = use.owner.use_site.pointer if isinstance(use.owner, OperationId) else use.owner.pointer
    projection = "" if use.projection == "value" else use.projection
    return " ".join(str(part) for part in (owner, use.role, use.status, use.location, use.name, projection) if part)


def _use_keys(uses: set[object]) -> dict[object, str]:
    keys = {use: _use_key(use) for use in uses}
    shared = Counter(keys.values())
    keys = {use: f"{key} {use.media}" if shared[key] > 1 else key for use, key in keys.items()}
    if len(set(keys.values())) != len(keys):
        msg = "Two reported uses share a report key"
        raise AssertionError(msg)
    return keys


def _tuples(data: dict[str, object]) -> dict[str, object]:
    return {key: tuple(value) if isinstance(value, list) else value for key, value in data.items()}


def _selector(data: dict[str, object]) -> TypeUseRef | SchemaDirectionalUse:
    if "schema" in data:
        return SchemaDirectionalUse(schema=SchemaRef(**data["schema"]), direction=data["direction"])
    operation = data["operation"]
    return TypeUseRef(**{**data, "operation": OperationRef(**operation) if isinstance(operation, dict) else operation})


def _registration(data: dict[str, object]) -> CodecAdapterRegistration:
    record = dict(data["capabilities"])
    return CodecAdapterRegistration(
        kind=data["kind"],
        name=data["name"],
        import_ref=data["import_ref"],
        dependencies=tuple(data.get("dependencies", ())),
        python_requires=data.get("python_requires", ">=3.10"),
        capabilities=getattr(capabilities, record.pop("record"))(**_tuples(record)),
        uses=tuple(_selector(use) for use in data["uses"]),
        api_version=data.get("api_version", 1),
    )


def declaration(kind: str, data: dict[str, object]) -> object:
    """Build one declaration record from fixture data."""
    match kind:
        case "compatibility":
            return BuiltinCodecCompatibility(
                **{
                    **_tuples(data),
                    "backend": DataModelType(data["backend"]),
                    "schemas": tuple(SchemaRef(**item) for item in data.get("schemas", ())),
                }
            )
        case "exports":
            return ModelExportBinding(**{**data, "schema": SchemaRef(**data["schema"])})
        case "adapters":
            return _registration(data)
        case _:
            return _selector(data)


def _invalid(kind: str, data: dict[str, object]) -> str:
    try:
        declaration(kind, data)
    except ValueError as error:
        return str(error)
    return "accepted"


def _declarations(data: dict[str, list[dict[str, object]]]) -> CodecDeclarations:
    return CodecDeclarations(**{kind: tuple(declaration(kind, item) for item in items) for kind, items in data.items()})


def _forget(names: tuple[str, ...]) -> None:
    for name in [name for name in sys.modules if name in names or name.startswith(tuple(f"{item}." for item in names))]:
        del sys.modules[name]


def _write(package: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = package.joinpath(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


class _Runner:
    def __init__(self, bindings: ModuleType, public: ModuleType, accessors: dict[str, object]) -> None:
        self.bindings = bindings
        self.public = public
        self.accessors = accessors
        self.results: dict[str, object] = {}

    def native(self, spec: object) -> object:
        match spec:
            case {"model": str() as name, "args": dict() as args}:
                return getattr(importlib.import_module("adapted.models"), name)(**args)
            case {"construct": str() as name, "args": dict() as args}:
                return getattr(importlib.import_module("adapted.models"), name).model_construct(**args)
            case {"result": str() as name}:
                return self.results[name].value
            case {"snapshot": str() as name}:
                return self.results[name]
            case _:
                return spec

    def result(self, value: object) -> str:
        public = self.public
        match value:
            case public.ModelValue():
                native = value.value
                shown = native.model_dump() if hasattr(native, "model_dump") else native
                return f"model {type(native).__name__} {shown!r} wire={_json(value.wire)} presence={list(value.presence.pointers())}"
            case public.ModelInput():
                issues = ",".join(f"{issue.code}@{issue.pointer}:{issue.field_id}" for issue in value.issues)
                return f"input issues={issues} wire={_json(value.wire)}"
            case public.FragmentContribution():
                fragments = ", ".join(f"{fragment.name!r}={fragment.value!r}" for fragment in value.ordered_fragments)
                return f"fragments {value.location} [{fragments}]"
            case public.QueryStringContribution():
                return f"querystring {value.raw_query!r}"
            case _ if value is public.UNSET:
                return "UNSET"
            case _:
                return f"wire={_json(value)}"

    def failure(self, error: Exception) -> str:
        match error:
            case self.public.WireValidationError():
                return "WireValidationError " + ",".join(
                    f"{issue.code}@{issue.instance_pointer}" for issue in error.issues
                )
            case self.public.NativeValidationError():
                return "NativeValidationError " + ",".join(f"{issue.code}@{issue.pointer}" for issue in error.issues)
            case _:
                cause = f" (from {type(error.__cause__).__name__})" if error.__cause__ else ""
                return f"{type(error).__name__} {error}{cause}"

    def run(self, case: dict[str, object]) -> str:
        names = self.accessors[case["use"]]
        context = getattr(self.bindings, names.context)
        value = case.get("value")
        try:
            match case["op"]:
                case "build":
                    getattr(self.bindings, getattr(names, case.get("accessor", "codec")))()
                    return "built"
                case "decode":
                    result = getattr(self.bindings, names.codec)().decode(value, context)
                case "from_wire":
                    result = getattr(self.bindings, names.codec)().from_wire(value, context)
                case "encode":
                    result = getattr(self.bindings, names.codec)().encode(self.native(value), context)
                case "outbound":
                    result = getattr(self.bindings, names.outbound)().from_wire(value)
                case "snapshot":
                    presence = self.public.presence_of(case["presence"]) if "presence" in case else None
                    result = getattr(self.bindings, names.outbound)().snapshot(self.native(value), presence=presence)
                case "parameter-encode":
                    result = getattr(self.bindings, names.parameter)().encode(self.public.freeze_wire(value), context)
                case "parameter-decode":
                    raw = self.public.RawParameter(
                        location=case["location"],
                        fragments=tuple(
                            self.public.ParameterFragment(
                                name=None if name is None else name.encode(), value=text.encode()
                            )
                            for name, text in case.get("fragments", ())
                        ),
                        raw_query=case["query"].encode() if "query" in case else None,
                    )
                    result = getattr(self.bindings, names.parameter)().decode(raw, context)
        except self.public.CodecError as error:
            return self.failure(error)
        self.results[case.get("name", "")] = result
        return self.result(result)


def _json(value: object) -> str:
    return encode_json(value).decode()


def adapter_codec_report(source: Path, cases: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Generate models and bindings with fixture declarations, import the package, and run every case in order."""
    fixture = thaw_wire(decode_json(cases.read_bytes()))
    backend = fixture.get("backend", "pydantic_v2.BaseModel")
    product, _ = generate_product(
        source,
        GenerateConfig(
            input_file_type=InputFileType.OpenAPI,
            openapi_scopes=[OpenAPIScope(scope) for scope in fixture.get("scopes", ["api"])],
            formatters=[],
            output_model_type=DataModelType(backend),
            **fixture.get("options", {}),
        ),
        model_package="adapted.models",
    )
    package = root / "adapted"
    try:
        models = {
            "models.py" if artifact.path == ("models.py",) else "/".join(("models", *artifact.path)): artifact.content.decode()
            for artifact in product.artifacts
        }
        wire = plan_wire(product.batch, product.source_lease)
        plan = plan_model_codecs(
            product.batch,
            wire,
            backend,
            declarations=_declarations(fixture.get("declarations", {})),
            surface=fixture.get("surface", "server"),
            lease=product.source_lease,
            sources={
                ".".join(("adapted", *name.removesuffix(".py").split("/"))).removesuffix(".__init__"): text
                for name, text in models.items()
            }
            | fixture.get("sources", {}),
        )
        rendered = render_model_bindings(plan, wire, product.batch, surface=fixture.get("surface", "server"))
    finally:
        product.close()
    keys = _use_keys({
        *(use for use, _ in plan.bindings),
        *(item.use for item in plan.adapters),
        *(item.use for item in rendered.uses),
    })
    lines = [f"invalid {kind}: {_invalid(kind, data)}" for kind, data in fixture.get("invalid", ())]
    lines.extend(f"diagnostic {item.code} {item.source.pointer}: {item.message}" for item in plan.diagnostics)
    lines.extend(
        f"adapter {item.registration.name} {keys[item.use]}: {item.binding.converter_strategy} {item.binding.projection_mode}"
        + (f" excluded={list(item.schema.excluded)}" if item.schema else "")
        for item in plan.adapters
    )
    if fixture.get("bindings"):
        lines.extend(
            f"binding {keys[use]}: {binding.native_export} {binding.native_kind} {binding.converter_strategy} "
            f"{binding.projection_mode}"
            for use, binding in plan.bindings
        )
    accessors = {keys[item.use]: item for item in rendered.uses}
    lines.extend(
        f"use {key}: {item.codec} {item.outbound} {item.parameter}" for key, item in accessors.items()
    )
    if fixture.get("source"):
        lines.extend(("--- _generated/model_bindings.py", rendered.source, "--- model_codecs.py", render_model_codecs(fixture.get("surface", "server"))))
    if not fixture.get("cases"):
        return "\n".join(lines) + "\n"
    _write(
        package,
        {
            "__init__.py": "",
            **models,
            "_runtime/__init__.py": "",
            "_runtime/model_codecs/__init__.py": f"__path__ = [{str(RUNTIME)!r}]\n",
            "_generated/__init__.py": "",
            "_generated/model_bindings.py": rendered.source,
            "model_codecs.py": render_model_codecs(fixture.get("surface", "server")),
        },
    )
    shutil.copytree(source.parent / "modules", root, ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    modules = tuple(path.name.removesuffix(".py") for path in (source.parent / "modules").iterdir())
    monkeypatch.syspath_prepend(str(root))
    try:
        runner = _Runner(
            importlib.import_module("adapted._generated.model_bindings"),
            importlib.import_module("adapted.model_codecs"),
            accessors,
        )
        lines.extend(f"{case['name']}: {runner.run(case)}" for case in fixture["cases"])
    finally:
        _forget(("adapted", *modules))
    return "\n".join(lines) + "\n"
