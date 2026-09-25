"""Render the Python modules of a FastAPI server target from its plan."""

from __future__ import annotations

import keyword
import re
from functools import cache
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._api_generation import RenderedFile
from datamodel_code_generator._codec_type_source import Namespace, TypeSource
from datamodel_code_generator._fastapi._compiled_templates import application as application_template
from datamodel_code_generator._fastapi._compiled_templates import router as router_template
from datamodel_code_generator._fastapi.plan import BODYLESS_STATUSES, Default
from datamodel_code_generator._fastapi.routes import tags
from datamodel_code_generator._openapi_codec_render import render_model_bindings, render_model_codecs
from datamodel_code_generator._python_layout import Chain, Doc, Group, layout
from datamodel_code_generator._runtime.model_codecs.media import media_kind
from datamodel_code_generator._runtime.model_codecs.unset import Unset
from datamodel_code_generator._runtime.model_codecs.wire import checked_wire, thaw_wire

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from datamodel_code_generator._fastapi.config import FastAPIConfig
    from datamodel_code_generator._fastapi.plan import (
        Argument,
        BodySpec,
        GroupSpec,
        HeaderSpec,
        MediaSpec,
        NativeField,
        OperationSpec,
        ResponseSpec,
        Scalar,
        ServerPlan,
    )
    from datamodel_code_generator._generation_contract import FinalPythonType, GeneratedTypeContractBatch, TypeUseId
    from datamodel_code_generator._openapi_codec_plan import CodecPlan
    from datamodel_code_generator._openapi_codec_render import UseAccessors
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.bindings import UseBinding
    from datamodel_code_generator._runtime.model_codecs.media import FieldPlan
    from datamodel_code_generator._runtime.model_codecs.parameters import ParameterPlan

WIDTH: Final = 88
_RUNTIME: Final = Path(__file__).parents[1] / "_runtime"
_SCALARS: Final = {"str": "str", "int": "int", "float": "float", "bool": "bool"}
_IMPORTED: Final = {
    "date": ("datetime", "date"),
    "aware_datetime": ("pydantic", "AwareDatetime"),
    "uuid": ("uuid", "UUID"),
}
_METHODS: Final = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
_RUNTIME_IMPORT: Final = re.compile(r"^from \.+_runtime\.(\w+)\.(\w+) import", re.MULTILINE)
_RELATIVE_IMPORT: Final = re.compile(r"^\s*from (\.+)(\w+(?:\.\w+)*) import", re.MULTILINE)
_PLAN_DEFAULTS: Final[dict[str, object]] = {
    "style": None,
    "explode": False,
    "required": False,
    "allow_reserved": False,
    "content_media_type": None,
    "shape": "scalar",
    "kind": "string",
}


def _python(value: object) -> str:
    """Return a Python literal for a finite configuration or schema value."""
    return repr(thaw_wire(checked_wire(value)))


def _unique(base: str, taken: Iterable[str]) -> str:
    names = set(taken)
    name = base
    count = 0
    while name in names or keyword.iskeyword(name):
        count += 1
        name = f"{base}_{count}"
    return name


def _items(values: Iterable[Doc]) -> tuple[tuple[str, Doc], ...]:
    return tuple(("", value) for value in values)


class Module:
    """One generated module: collision-free import aliases, type spellings, and runtime imports at its depth."""

    def __init__(self, reserved: Iterable[str], symbols: Mapping[int, str], *, level: int) -> None:
        """Reserve the names the module defines, and remember how deep below the package root it lives."""
        self.namespace = Namespace(reserved)
        self.types = TypeSource(self.namespace, symbols)
        self.level = level

    def name(self, module: str, name: str) -> str:
        """Return the local alias of an imported name."""
        return self.namespace.name(module, name)

    def local(self, module: str, name: str) -> str:
        """Return the local alias of a name imported from a module of the generated package."""
        return self.namespace.name(f"{'.' * self.level}{module}", name)

    def static(self, value: FinalPythonType) -> str:
        """Return the static spelling of a final type."""
        return self.types.static(value)

    def imports(self) -> str:
        """Return the module's import statements."""
        return "\n".join(self.namespace.imports())


class ServerRenderer:  # noqa: PLR0904
    """Render every module of one server package from its plan, codec plan, and wire plan."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        config: FastAPIConfig,
        package: PurePosixPath,
        plan: ServerPlan,
        batch: GeneratedTypeContractBatch,
        wire: WirePlan,
        codecs: CodecPlan,
    ) -> None:
        """Keep the plans and index the codec accessors of every bound use."""
        self.config = config
        self.package = package
        self.plan = plan
        self.symbols = dict(codecs.imports)
        self.bindings = render_model_bindings(codecs, wire, batch, surface="server")
        self.accessors: dict[TypeUseId, UseAccessors] = {item.use: item for item in self.bindings.uses}
        self.use_bindings: dict[TypeUseId, UseBinding] = dict(codecs.bindings)

    def file(
        self, path: PurePosixPath, kind: str, text: str, group: str | None = None, *, verbatim: bool = False
    ) -> RenderedFile:
        """Return one owned file of the package."""
        return RenderedFile(
            path=self.package / path, kind=kind, ownership="owned", text=text, group=group, verbatim=verbatim
        )

    def files(self) -> tuple[RenderedFile, ...]:
        """Return every rendered server file in the fixed artifact order."""
        generated = PurePosixPath("_generated")
        files = (
            self.file(PurePosixPath("__init__.py"), "package", _PACKAGE),
            self.file(PurePosixPath("application.py"), "application", self.application()),
            *self.routers(),
            self.file(generated / "__init__.py", "package", '"""Generated plans of this package."""\n'),
            self.file(generated / "contract.py", "contract", self.contract()),
            self.file(generated / "model_bindings.py", "model_bindings", self.bindings.source),
            self.file(PurePosixPath("model_codecs.py"), "model_codecs", render_model_codecs("server")),
            self.file(PurePosixPath("responses.py"), "responses", self.responses()),
            self.file(PurePosixPath("errors.py"), "errors", _ERRORS),
        )
        return (*files, *self.runtime(files))

    def runtime(self, files: tuple[RenderedFile, ...]) -> Iterator[RenderedFile]:
        """Copy the runtime modules the package imports, with their own imports, in ascending path order."""
        graph = _runtime_imports()
        pending = [f"{package}/{module}.py" for file in files for package, module in _RUNTIME_IMPORT.findall(file.text)]
        needed: set[str] = set()
        while pending:
            if (module := pending.pop()) not in needed:
                needed.add(module)
                pending.extend(graph[module])
        packages = {f"{PurePosixPath(module).parent}/__init__.py" for module in needed}
        for path in sorted({"__init__.py", *packages, *needed}):
            yield self.file(PurePosixPath("_runtime", path), "runtime", (_RUNTIME / path).read_text(), verbatim=True)

    def application(self) -> str:
        """Return the application module, rendered from its builtin template."""
        module = Module({"OPERATIONS", "build_router", "handlers", "checked", "router", "add"}, {}, level=1)
        if self.config.layout == "single":
            modules = [module.local("", "routes")]
        else:
            modules = [module.local("routers", group.stem) for group in self.plan.groups]
        routes = (*(f"*{name}.LITERAL_ROUTES" for name in modules), *(f"*{name}.TEMPLATED_ROUTES" for name in modules))
        operations = Group(
            "(",
            _items(
                Group("(", (("", repr(spec.python_name)), ("", _keywords(spec.arguments))), ")")
                for spec in self.plan.operations
            ),
            ")",
            ",",
        )
        handlers = _handlers(module)
        router = module.name("fastapi", "APIRouter")
        final = module.name("typing", "Final")
        check = module.local("_runtime.server.application", "checked_handlers")
        return application_template.render(
            imports=module.imports(),
            handlers=handlers,
            router=router,
            final=final,
            check=check,
            operations=layout(operations, 0, len(f"OPERATIONS: {final} = "), WIDTH),
            routes=layout(Group("(", _items(routes), ")", ","), 4, len("for add in "), WIDTH),
        )

    def routers(self) -> Iterator[RenderedFile]:
        """Return the router modules: one routes module, or a package with one module per group."""
        if self.config.layout == "single":
            group = self.plan.groups[0] if self.plan.groups else None
            yield self.file(PurePosixPath("routes.py"), "router", self.router(group, level=1), "all")
            return
        yield self.file(PurePosixPath("routers/__init__.py"), "package", '"""Router groups of this package."""\n')
        for group in self.plan.groups:
            yield self.file(
                PurePosixPath("routers", f"{group.stem}.py"), "router", self.router(group, level=2), group.key
            )

    def router(self, group: GroupSpec | None, *, level: int) -> str:
        """Return one router module, rendered from its builtin template."""
        operations = () if group is None else group.operations
        reserved = {"LITERAL_ROUTES", "TEMPLATED_ROUTES", "build_router", "router", "handlers", "add"}
        for spec in operations:
            reserved.update((
                spec.python_name,
                f"_add_{spec.python_name}",
                *(argument.name for argument in spec.arguments),
            ))
        module = Module(reserved, self.symbols, level=level)
        routes = [self.route(module, spec) for spec in operations]
        literal = [f"_add_{spec.python_name}" for spec in operations if not spec.route.templated]
        templated = [f"_add_{spec.python_name}" for spec in operations if spec.route.templated]
        name = "the operations" if group is None else f"the {group.stem} operations"
        handlers = _handlers(module)
        router = module.name("fastapi", "APIRouter")
        final = module.name("typing", "Final")
        return router_template.render(
            docstring=f"Endpoints of {name}; regenerate them instead of editing.",
            imports=module.imports(),
            routes=routes,
            router=router,
            handlers=handlers,
            final=final,
            literal=layout(Group("(", _items(literal), ")", ","), 0, len(f"LITERAL_ROUTES: {final} = "), WIDTH),
            templated=layout(Group("(", _items(templated), ")", ","), 0, len(f"TEMPLATED_ROUTES: {final} = "), WIDTH),
            group=name,
        )

    def route(self, module: Module, spec: OperationSpec) -> dict[str, str]:
        """Return the fragments of one operation's endpoint: decorator, signature, and handler call."""
        plan = f"{module.local('_generated', 'contract')}.{spec.pascal}"
        names = {argument.name for argument in spec.arguments}
        handler = _unique(f"{spec.python_name}_handler", names)
        record = _unique("parameters", names)
        parameters: list[Doc] = [
            self.parameter(module, spec, argument, plan)
            for argument in spec.arguments
            if argument.kind not in {"media_type", "adapter"}
        ]
        if any(argument.kind == "adapter" for argument in spec.arguments):
            annotated, depends = module.name("typing", "Annotated"), module.name("fastapi", "Depends")
            parameters.append(f"{record}: {annotated}[{plan}.Parameters, {depends}({plan}.PARAMETERS)]")
        native = spec.native_primary
        returns = "object" if native else module.name("fastapi.responses", "Response")
        signature = Group(
            f"def {spec.python_name}(", _items(("*", *parameters)) if parameters else (), f") -> {returns}:"
        )
        call = Group(
            f"{handler}(",
            tuple((f"{argument.name}=", _value(module, spec, argument, record)) for argument in spec.arguments),
            ")",
        )
        dispatch = module.local("_runtime.server.responses", "dispatch" if native else "respond")
        body = Group(f"return {dispatch}(", (("", call), ("", f"{plan}.RESPONSES")), ")")
        return {
            "adder": f"_add_{spec.python_name}",
            "handler": handler,
            "key": repr(spec.python_name),
            "decorator": layout(_decorator(module, spec), 4, 0, WIDTH),
            "signature": layout(signature, 4, 0, WIDTH),
            "body": layout(body, 8, 0, WIDTH),
        }

    def parameter(self, module: Module, spec: OperationSpec, argument: Argument, plan: str) -> Doc:
        """Return the endpoint parameter of one handler argument that FastAPI or the body adapter supplies."""
        if argument.kind == "request":
            return f"request: {module.name('fastapi', 'Request')}"
        if argument.native is not None:
            return _native(module, argument.name, argument.native)
        body = spec.body
        assert body is not None
        annotated = module.name("typing", "Annotated")
        if body.decision.transport == "fastapi_native":
            media = body.media[0]
            assert media.use is not None
            assert media.use.type is not None
            api = module.name("fastapi", "Body")
            return (
                f"{argument.name}: {annotated}[{module.static(media.use.type)}, {api}(media_type={media.media_type!r})]"
            )
        depends = module.name("fastapi", "Depends")
        kind = self.body_type(module, body)
        if len(body.media) > 1:
            return f"{argument.name}: {annotated}[tuple[str | None, {kind}], {depends}({plan}.BODY.receive)]"
        return f"{argument.name}: {annotated}[{kind}, {depends}({plan}.BODY)]"

    def body_type(self, module: Module, body: BodySpec) -> str:
        """Return the type the handler receives for an adapter body, with UNSET for an optional one."""
        kinds = dict.fromkeys(self.media_type(module, media, sent=False) for media in body.media)
        if not body.required:
            kinds[module.local("_runtime.model_codecs.unset", "Unset")] = None
        return " | ".join(kinds)

    def media_type(self, module: Module, media: MediaSpec, *, sent: bool) -> str:
        """Return one media's payload type: the codec's type, its envelope, or the media surface."""
        use = media.use
        if media.kind == "binary" or use is None or use.type is None:
            return (
                module.local("_runtime.model_codecs.wire", "WireValue")
                if media.kind == "json"
                else "str"
                if media.kind == "text"
                else "bytes"
            )
        static = module.static(use.type)
        binding = self.use_bindings.get(use.id)
        envelope = binding is not None and binding.projection_mode == "envelope"
        if not sent:
            return f"{module.local('_runtime.model_codecs.values', 'DecodedValue')}[{static}]" if envelope else static
        values = [static, f"{module.local('_runtime.model_codecs.values', 'ModelValue')}[{static}]"]
        if envelope:
            values.append(f"{module.local('_runtime.model_codecs.values', 'ModelInput')}[{static}]")
        return " | ".join(values)

    def contract(self) -> str:
        """Return the contract module: each operation's response plans and request adapters."""
        module = Module({spec.pascal for spec in self.plan.operations}, self.symbols, level=2)
        bindings = module.name(".", "model_bindings")
        sections = [self.operation_plan(module, spec, bindings) for spec in self.plan.operations]
        head = (
            '"""Operation plans of this package; regenerate them instead of editing."""\n\n'
            "from __future__ import annotations\n\n"
        )
        return head + module.imports() + "\n\n\n" + "\n\n".join(sections)

    def codec(self, use: TypeUseId, bindings: str) -> str:
        """Return the (codec accessor, context) pair of one bound use."""
        accessors = self.accessors[use]
        return f"({bindings}.{accessors.codec}, {bindings}.{accessors.context})"

    def envelope(self, use: TypeUseId) -> bool:
        """Return whether a use's codec projects into an envelope."""
        binding = self.use_bindings.get(use)
        return binding is not None and binding.projection_mode == "envelope"

    def operation_plan(self, module: Module, spec: OperationSpec, bindings: str) -> str:
        """Return one operation's plan class: adapter parameter record, request adapters, and responses."""
        final = module.name("typing", "Final")
        lines = [f"class {spec.pascal}:", f'    """Plans of the {spec.python_name} operation."""', ""]
        adapters = [argument for argument in spec.arguments if argument.kind == "adapter"]
        if adapters:
            lines.extend((
                f"    class Parameters({module.name('typing', 'NamedTuple')}):",
                f'        """The adapter parameters of {spec.python_name}."""',
                "",
                *(f"        {argument.name}: {self.parameter_type(module, argument)}" for argument in adapters),
                "",
            ))
            adapter = self.parameter_adapter(module, spec, adapters, bindings)
            lines.append(f"    PARAMETERS: {final} = {layout(adapter, 4, len('PARAMETERS: Final = '), WIDTH)}")
        if spec.body is not None and spec.body.decision.transport == "codec_adapter":
            body = self.body_adapter(module, spec.body, bindings)
            lines.append(f"    BODY: {final} = {layout(body, 4, len('BODY: Final = '), WIDTH)}")
        responses = self.responses_plan(module, spec, bindings)
        lines.append(f"    RESPONSES: {final} = {layout(responses, 4, len('RESPONSES: Final = '), WIDTH)}")
        return "\n".join(lines) + "\n"

    def parameter_type(self, module: Module, argument: Argument) -> str:
        """Return the handler type of one adapter parameter: its codec's type, or the media surface without a schema."""
        parameter = argument.parameter
        assert parameter is not None
        assert parameter.plan is not None
        if (use := parameter.use) is None:
            json = media_kind(parameter.plan.content_media_type or "") == "json"
            static = module.local("_runtime.model_codecs.wire", "WireValue") if json else "str"
        else:
            assert use.type is not None
            static = module.static(use.type)
            if self.envelope(use.id):
                static = f"{module.local('_runtime.model_codecs.values', 'DecodedValue')}[{static}]"
        if parameter.required or not isinstance(parameter.default, Unset):
            return static
        return f"{static} | {module.local('_runtime.model_codecs.unset', 'Unset')}"

    def parameter_adapter(self, module: Module, spec: OperationSpec, adapters: list[Argument], bindings: str) -> Group:
        """Return the ParameterAdapter constructor of an operation's adapter parameters."""
        arguments: list[Doc] = []
        names: set[str] = set()
        for argument in adapters:
            parameter = argument.parameter
            assert parameter is not None
            assert parameter.plan is not None
            entries: list[tuple[str, Doc]] = [
                ("name=", repr(argument.name)),
                ("plan=", _parameter_plan(module, parameter.plan)),
            ]
            if (use := parameter.use) is not None:
                entries.append(("codec=", self.codec(use.id, bindings)))
                if self.envelope(use.id):
                    entries.append(("envelope=", "True"))
                names.update(self.property_names(use.id))
            if not isinstance(parameter.default, Unset):
                entries.append((
                    "default=",
                    f"{module.local('_runtime.model_codecs.wire', 'freeze_wire')}({_python(parameter.default)})",
                ))
            arguments.append(
                Group(f"{module.local('_runtime.server.requests', 'ParameterArgument')}(", tuple(entries), ")")
            )
        items: list[tuple[str, Doc]] = [
            ("arguments=", Group("(", _items(arguments), ")", ",")),
            ("record=", "Parameters"),
        ]
        if path := _raw_path(module, spec, adapters):
            items.append(("path=", path))
        if names:
            items.append(("names=", _frozenset(names)))
        return Group(f"{module.local('_runtime.server.requests', 'ParameterAdapter')}(", tuple(items), ")")

    def property_names(self, use: TypeUseId) -> set[str]:
        """Return the declared property names a use's codec knows, which error locations may show."""
        binding = self.use_bindings.get(use)
        return set() if binding is None else {field.wire_name for model in binding.models for field in model.fields}

    def body_adapter(self, module: Module, body: BodySpec, bindings: str) -> Group:
        """Return the BodyAdapter constructor of an adapter body."""
        media: list[Doc] = []
        names: set[str] = set()
        for item in body.media:
            entries: list[tuple[str, Doc]] = [
                ("media_type=", repr(item.media_type)),
                ("kind=", repr(_request_kind(item))),
            ]
            if item.kind != "binary" and item.use is not None and item.use.id in self.accessors:
                entries.append(("codec=", self.codec(item.use.id, bindings)))
                if self.envelope(item.use.id):
                    entries.append(("envelope=", "True"))
                names.update(self.property_names(item.use.id))
            if item.kind == "form":
                fields = Group("(", _items(_field_plan(module, plan) for plan in body.form_fields), ")", ",")
                entries.append(("fields=", fields))
                if body.form_additional is not None:
                    entries.append(("additional=", _field_plan(module, body.form_additional)))
            media.append(Group(f"{module.local('_runtime.server.requests', 'BodyMedia')}(", tuple(entries), ")"))
        items: list[tuple[str, Doc]] = [("media=", Group("(", _items(media), ")", ","))]
        if not body.required:
            items.append(("required=", "False"))
        if names:
            items.append(("names=", _frozenset(names)))
        return Group(f"{module.local('_runtime.server.requests', 'BodyAdapter')}(", tuple(items), ")")

    def responses_plan(self, module: Module, spec: OperationSpec, bindings: str) -> Group:
        """Return the OperationResponses constructor of one operation."""
        responses = _items(
            self.response_plan(module, response, bindings, head=spec.head) for response in spec.responses
        )
        items: list[tuple[str, Doc]] = [("responses=", Group("(", responses, ")", ","))]
        if (primary := spec.primary) is not None:
            media = None if primary.media is None or spec.head else primary.media.media_type
            items.append(("primary=", f"({primary.status}, {media!r})"))
        if spec.head:
            items.append(("head=", "True"))
        return Group(f"{module.local('_runtime.server.responses', 'OperationResponses')}(", tuple(items), ")")

    def response_plan(self, module: Module, response: ResponseSpec, bindings: str, *, head: bool) -> Group:
        """Return the ResponsePlan constructor of one declared response."""
        items: list[tuple[str, Doc]] = [("status=", repr(response.status))]
        media: list[Doc] = []
        for item in () if head else response.media:
            entries: list[tuple[str, Doc]] = [
                ("media_type=", repr(item.media_type)),
                ("kind=", repr(_response_kind(item))),
            ]
            if item.kind != "binary" and item.use is not None and item.use.id in self.accessors:
                entries.append(("codec=", self.codec(item.use.id, bindings)))
            media.append(Group(f"{module.local('_runtime.server.responses', 'MediaPlan')}(", tuple(entries), ")"))
        if media:
            items.append(("media=", Group("(", _items(media), ")", ",")))
        if headers := [self.header_plan(module, header, bindings) for header in response.headers]:
            items.append(("headers=", Group("(", _items(headers), ")", ",")))
        return Group(f"{module.local('_runtime.server.responses', 'ResponsePlan')}(", tuple(items), ")")

    def header_plan(self, module: Module, header: HeaderSpec, bindings: str) -> Group:
        """Return the HeaderPlan constructor of one declared response header."""
        entries: list[tuple[str, Doc]] = [("name=", repr(header.name))]
        if header.required:
            entries.append(("required=", "True"))
        if header.plan is not None and header.use is not None and header.use.id in self.accessors:
            entries.extend((
                ("plan=", _parameter_plan(module, header.plan)),
                ("codec=", self.codec(header.use.id, bindings)),
            ))
        return Group(f"{module.local('_runtime.server.responses', 'HeaderPlan')}(", tuple(entries), ")")

    def responses(self) -> str:
        """Return the responses module: result types, payload aliases, and response codec facades."""
        reserved = {"UNSET", "Unset", "HTTPResult"}
        for spec in self.plan.operations:
            reserved.update((
                f"{spec.pascal}ResponsePayload",
                f"{spec.pascal}ResponseCodecs",
                f"_{spec.pascal}ResponseCodecs",
                f"_{spec.pascal}ResponseCodec",
            ))
        module = Module(reserved, self.symbols, level=1)
        exports = ["UNSET", "HTTPResult", "Unset"]
        sections: list[str] = []
        for spec in self.plan.operations:
            alias = f"{spec.pascal}ResponsePayload"
            head = f"{alias}: {module.name('typing', 'TypeAlias')} = "
            sections.extend((
                f"{head}{layout(self.payload(module, spec), 0, len(head), WIDTH)}\n",
                self.facade(module, spec),
            ))
            exports.extend((alias, f"{spec.pascal}ResponseCodecs"))
        head = (
            '"""Results, payload types, and response codecs of this package."""\n\n'
            "from ._runtime.model_codecs.unset import UNSET, Unset\n"
            "from ._runtime.server.responses import HTTPResult\n"
        )
        listing = "".join(f"    {name!r},\n" for name in sorted(exports))
        return f"{head}{module.imports()}\n\n\n" + "\n\n".join(sections) + f"\n\n__all__ = [\n{listing}]\n"

    def payload(self, module: Module, spec: OperationSpec) -> Doc:
        """Return the union of every declared response payload type, in declaration order."""
        parts: dict[str, None] = {}
        for response in spec.responses:
            if spec.head or (response.status.isdigit() and int(response.status) in BODYLESS_STATUSES):
                continue
            for media in response.media:
                parts.update(dict.fromkeys(self.media_type(module, media, sent=True).split(" | ")))
        return Chain("|", tuple(parts)) if parts else module.name("typing_extensions", "Never")

    def facade(self, module: Module, spec: OperationSpec) -> str:
        """Return one operation's response codec facade: typed overloads over its codec-bearing bodies."""
        branches = list(self.branches(module, spec))
        kinds = tuple(dict.fromkeys(kind for _, _, kind, _ in branches))
        name = f"_{spec.pascal}ResponseCodecs"
        alias = ""
        if len(kinds) > 1:
            union = f"_{spec.pascal}ResponseCodec"
            head = f"{union}: {module.name('typing', 'TypeAlias')} = "
            alias = f"{head}{layout(Chain('|', kinds), 0, len(head), WIDTH)}\n\n\n"
        else:
            union = kinds[0] if kinds else module.name("typing_extensions", "Never")
        base = f"{module.local('_runtime.server.codecs', 'ResponseCodecs')}[{union}]"
        lines = (
            alias + layout(Group(f"class {name}(", (("", base),), "):"), 0, 0, WIDTH),
            f'    """Outbound codecs of the {spec.python_name} response bodies."""',
            "",
            *_overloads(module.name("typing", "overload"), module.name("typing", "Literal"), branches, union),
            f"    {_body_signature('int', 'str | None = None', f'{union}:')}",
            '        """Return the outbound codec of one declared response body."""',
            "        return self.select(status_code, media_type)",
        )
        bindings = module.local("_generated", "model_bindings")
        bodies: dict[str, list[Doc]] = {}
        for status, media_type, _, accessor in branches:
            bodies.setdefault(status, []).append(f"({media_type!r}, {bindings}.{accessor})")
        statuses = _items(
            Group("(", (("", repr(status)), ("", Group("(", _items(items), ")", ","))), ")")
            for status, items in bodies.items()
        )
        instance = f"{spec.pascal}ResponseCodecs: {module.name('typing', 'Final')} = "
        table = layout(Group(f"{name}(", (("", Group("(", statuses, ")", ",")),), ")"), 0, len(instance), WIDTH)
        return "\n".join(lines) + f"\n\n\n{instance}{table}\n"

    def branches(self, module: Module, spec: OperationSpec) -> Iterator[tuple[str, str, str, str]]:
        """Yield the status key, media type, codec type, and accessor of each codec-bearing response body."""
        for response in () if spec.head else spec.responses:
            for media in response.media:
                if media.use is None or media.use.id not in self.accessors or media.kind == "binary":
                    continue
                accessor = self.accessors[media.use.id].outbound
                assert accessor is not None
                assert media.use.type is not None
                codec = "EnvelopeOutboundCodec" if self.envelope(media.use.id) else "NativeOutboundCodec"
                kind = f"{module.local('_runtime.model_codecs.outbound', codec)}[{module.static(media.use.type)}]"
                yield response.status, media.media_type, kind, accessor


def _field_plan(module: Module, field: FieldPlan) -> str:
    repeated = ", repeated=True" if field.repeated else ""
    return f"{module.local('_runtime.model_codecs.media', 'FieldPlan')}({field.name!r}, {field.kind!r}{repeated})"


def _native(module: Module, name: str, field: NativeField) -> Doc:
    annotated = module.name("typing", "Annotated")
    if field.scalar is None:
        upload = module.name("fastapi", "UploadFile")
        surface = f"list[{upload}]" if field.array else upload
    else:
        surface = _scalar(module, field.scalar, field.items)
        surface = f"list[{surface}]" if field.array else surface
    keywords = [f"alias={field.alias!r}"]
    if field.api == "Header":
        keywords.append("convert_underscores=False")
    keywords.extend(f"{key}={_python(value)}" for key, value in field.keywords)
    default = ""
    if field.default is Default.ABSENT:
        keywords.append(f"default_factory={module.local('_runtime.server.requests', 'absent')}")
    elif field.default is not Default.REQUIRED and field.array:
        value = _python(field.default)
        keywords.extend((f"default_factory=lambda: {value}", f"json_schema_extra={{'default': {value}}}"))
    elif field.default is not Default.REQUIRED:
        default = f" = {_python(field.default)}"
    api = module.name("fastapi", field.api)
    return Group(f"{name}: {annotated}[{surface}, {api}(", _items(keywords), f")]{default}")


def _parameter_plan(module: Module, plan: ParameterPlan) -> Group:
    items: list[tuple[str, Doc]] = [("location=", repr(plan.location)), ("name=", repr(plan.name))]
    items.extend(
        (f"{name}=", repr(value))
        for name, default in _PLAN_DEFAULTS.items()
        if (value := getattr(plan, name)) != default
    )
    if plan.fields:
        items.append((
            "fields=",
            Group("(", _items(_field_plan(module, item) for item in plan.fields), ")", ","),
        ))
    if plan.additional is not None:
        items.append(("additional=", _field_plan(module, plan.additional)))
    if plan.reserved_names:
        items.append(("reserved_names=", repr(plan.reserved_names)))
    return Group(f"{module.local('_runtime.model_codecs.parameters', 'ParameterPlan')}(", tuple(items), ")")


def _decorator(module: Module, spec: OperationSpec) -> Group:
    contract = spec.contract
    facts = {name: getattr(value, "value", None) for name, value in contract.facts}
    method = contract.method
    items: list[tuple[str, Doc]] = [("", repr(spec.route.route_path))]
    if method not in _METHODS:
        items.append(("methods=", f"[{method.upper()!r}]"))
    items.append(("status_code=", str(spec.registration_status)))
    primary = spec.primary
    if spec.native_primary and primary is not None and primary.media is not None and primary.media.use is not None:
        assert primary.media.use.type is not None
        items.extend((
            ("response_model=", module.static(primary.media.use.type)),
            ("response_model_by_alias=", "True"),
            ("response_model_exclude_unset=", "True"),
        ))
    else:
        items.extend((
            ("response_model=", "None"),
            ("response_class=", module.name("fastapi.responses", "Response")),
        ))
    if contract.explicit_operation_id and isinstance(operation_id := facts.get("operationId"), str):
        items.append(("operation_id=", repr(operation_id)))
    if found := tags(contract):
        items.append(("tags=", f"[{', '.join(repr(tag) for tag in found)}]"))
    items.extend(
        (f"{name}=", repr(value)) for name in ("summary", "description") if isinstance(value := facts.get(name), str)
    )
    if facts.get("deprecated") is True:
        items.append(("deprecated=", "True"))
    return Group(f"@router.{method}(" if method in _METHODS else "@router.api_route(", tuple(items), ")")


def _value(module: Module, spec: OperationSpec, argument: Argument, record: str) -> str:
    if argument.kind == "adapter":
        return f"{record}.{argument.name}"
    if argument.kind == "media_type":
        return "body[0]"
    if argument.kind == "body" and spec.body is not None and len(spec.body.media) > 1:
        return "body[1]"
    if argument.native is not None and argument.native.default is Default.ABSENT:
        unset = module.local("_runtime.model_codecs.unset", "UNSET")
        return f"{unset} if {argument.name} is None else {argument.name}"
    return argument.name


def _scalar(module: Module, scalar: Scalar, constraints: tuple[tuple[str, object], ...]) -> str:
    if scalar.kind == "literal":
        text = f"{module.name('typing', 'Literal')}[{', '.join(repr(value) for value in scalar.values)}]"
    elif (imported := _IMPORTED.get(scalar.kind)) is not None:
        text = module.name(*imported)
    else:
        text = _SCALARS[scalar.kind]
    if not constraints:
        return text
    keywords = ", ".join(f"{key}={_python(value)}" for key, value in constraints)
    return f"{module.name('typing', 'Annotated')}[{text}, {module.name('pydantic', 'Field')}({keywords})]"


def _raw_path(module: Module, spec: OperationSpec, adapters: list[Argument]) -> Group | None:
    if not (wanted := {item.wire_name for item in adapters if item.location == "path" and item.wire_name is not None}):
        return None
    return Group(
        f"{module.local('_runtime.server.requests', 'RawPath')}(",
        (("template=", repr(spec.route.path)), ("names=", _frozenset(wanted))),
        ")",
    )


def _overloads(overload: str, literal: str, branches: list[tuple[str, str, str, str]], union: str) -> Iterator[str]:
    statuses: dict[str, list[tuple[str, str]]] = {}
    for status, media_type, kind, _ in branches:
        statuses.setdefault(status, []).append((media_type, kind))
    for status, media in statuses.items():
        if not status.isdigit():
            continue
        code = f"{literal}[{status}]"
        if len(media) == 1:
            yield from _overload(overload, code, f"{literal}[{media[0][0]!r}] | None = None", media[0][1])
            continue
        for media_type, kind in media:
            yield from _overload(overload, code, f"{literal}[{media_type!r}]", kind)
        yield from _overload(overload, code, "None = None", _default_kind(media))
    if branches:
        yield from _overload(overload, "int", "str | None = None", union)


def _default_kind(media: list[tuple[str, str]]) -> str:
    return next(
        (kind for media_type, kind in media if media_type == "application/json"),
        next((kind for media_type, kind in media if media_type.partition(";")[0].endswith("+json")), media[0][1]),
    )


def _overload(overload: str, status: str, media: str, returns: str) -> list[str]:
    return [f"    @{overload}", f"    {_body_signature(status, media, f'{returns}: ...')}"]


def _body_signature(status: str, media: str, returns: str) -> str:
    parameters = _items(("self", "*", f"status_code: {status}", f"media_type: {media}"))
    return layout(Group("def body(", parameters, f") -> {returns}"), 4, 0, WIDTH)


@cache
def _runtime_imports() -> dict[str, tuple[str, ...]]:
    """Map every runtime module to the runtime modules it imports relatively."""
    graph: dict[str, tuple[str, ...]] = {}
    for source in _RUNTIME.rglob("*.py"):
        module = PurePosixPath(source.relative_to(_RUNTIME).as_posix())
        graph[module.as_posix()] = tuple(
            f"{module.parents[len(dots) - 1].joinpath(*target.split('.')).as_posix()}.py"
            for dots, target in _RELATIVE_IMPORT.findall(source.read_text())
        )
    return graph


def _handlers(module: Module) -> str:
    return (
        f"{module.name('collections.abc', 'Mapping')}[str, {module.name('collections.abc', 'Callable')}[..., object]]"
    )


def _keywords(arguments: tuple[Argument, ...]) -> Doc:
    return Group("(", _items(repr(argument.name) for argument in arguments), ")", ",")


def _frozenset(names: set[str]) -> str:
    return f"frozenset({{{', '.join(repr(name) for name in sorted(names))}}})"


def _request_kind(media: MediaSpec) -> str:
    return media.kind if media.kind in {"json", "text", "form"} else "binary"


def _response_kind(media: MediaSpec) -> str:
    return media.kind if media.kind in {"json", "text"} else "binary"


_PACKAGE: Final = '''"""FastAPI server generated by datamodel-code-generator."""

from .application import build_router
from .errors import HandlerConfigurationError
from .responses import UNSET, HTTPResult, Unset

__all__ = ["UNSET", "HTTPResult", "HandlerConfigurationError", "Unset", "build_router"]
'''
_ERRORS: Final = '''"""Errors a generated server raises while it builds routers."""

from ._runtime.server.application import HandlerConfigurationError

__all__ = ["HandlerConfigurationError"]
'''
