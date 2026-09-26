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
from datamodel_code_generator._fastapi._compiled_templates import services as services_template
from datamodel_code_generator._fastapi.plan import BODYLESS_STATUSES, Default
from datamodel_code_generator._fastapi.routes import BUILDER_NAMES, tags
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
        Requirement,
        ResponseSpec,
        Scalar,
        SchemeSpec,
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
_MIN_CONTENT_STATUS: Final = 200
_RUNTIME: Final = Path(__file__).parents[1] / "_runtime"
_SCALARS: Final = {"str": "str", "int": "int", "float": "float", "bool": "bool"}
_IMPORTED: Final = {
    "date": ("datetime", "date"),
    "aware_datetime": ("pydantic", "AwareDatetime"),
    "uuid": ("uuid", "UUID"),
}
_RUNTIME_IMPORT: Final = re.compile(r"^from \.+_runtime\.(\w+)\.(\w+) import", re.MULTILINE)
_RELATIVE_IMPORT: Final = re.compile(r"^\s*from (\.+)(\w+(?:\.\w+)*) import", re.MULTILINE)
_SETTINGS: Final = ("dependencies", "operation_dependencies", "prefix")
_PUBLIC: Final = {
    "_runtime.model_codecs.unset": "model_codecs",
    "_runtime.model_codecs.values": "model_codecs",
    "_runtime.model_codecs.wire": "model_codecs",
    "_runtime.server.responses": "responses",
}
_EXPORTS: Final = (
    ("_generated.contract", "OperationDependencies"),
    ("_generated.contract", "OperationKey"),
    ("_generated.contract", "SchemeKey"),
    ("_runtime.server.application", "Dependency"),
    ("_runtime.server.application", "FastAPIOptions"),
    ("auth_types", "AsyncAuthorizer"),
    ("auth_types", "AsyncCredentialExtractor"),
    ("auth_types", "Authorizer"),
    ("auth_types", "CredentialExtractor"),
    ("auth_types", "CredentialExtractors"),
)
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

    def __init__(
        self, reserved: Iterable[str], symbols: Mapping[int, str], *, level: int, public: bool = False
    ) -> None:
        """Reserve the names the module defines, and remember its depth and whether it imports public modules only."""
        self.namespace = Namespace(reserved)
        self.types = TypeSource(self.namespace, symbols)
        self.level = level
        self.public = public

    def name(self, module: str, name: str) -> str:
        """Return the local alias of an imported name."""
        return self.namespace.name(module, name)

    def local(self, module: str, name: str) -> str:
        """Return the local alias of a name imported from a module of the generated package.

        A module users read to implement it imports runtime names through the package's public modules instead.
        """
        public = _PUBLIC.get(module, module) if self.public else module
        return self.namespace.name(f"{'.' * self.level}{public}", name)

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
        self.services = {group.key: group.stem for group in plan.groups}

    def file(
        self, path: PurePosixPath, kind: str, text: str, group: str | None = None, *, verbatim: bool = False
    ) -> RenderedFile:
        """Return one owned file of the package."""
        return RenderedFile(path=self.package / path, kind=kind, text=text, group=group, verbatim=verbatim)

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
            self.file(PurePosixPath("auth_types.py"), "auth_types", _AUTH_TYPES),
            self.file(PurePosixPath("services.py"), "services", self.services_module()),
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
            yield self.file(
                PurePosixPath("_runtime", path), "runtime", (_RUNTIME / path).read_text(encoding="utf-8"), verbatim=True
            )

    def application(self) -> str:
        """Return the application module, rendered from its builtin template.

        The router modules come first, so the names they take keep every other import away from the builders'
        service arguments, which share their names.
        """
        single = self.config.layout == "single"
        groups = self.plan.groups
        services = [group.stem for group in groups]
        module = Module({*BUILDER_NAMES, *(services if single else ())}, {}, level=1)
        modules = [module.local("", "routes")] if single else [module.local("routers", stem) for stem in services]
        routes = (*(f"*{name}.LITERAL_ROUTES" for name in modules), *(f"*{name}.TEMPLATED_ROUTES" for name in modules))
        secured = bool(self.plan.schemes)
        final = module.name("typing", "Final")
        options = module.local("_runtime.server.application", "FastAPIOptions")
        info = Group("{", tuple((f"{key!r}: ", _python(value)) for key, value in self.plan.info), "}")
        router = module.name("fastapi", "APIRouter")
        fastapi = module.name("fastapi", "FastAPI")
        exports = sorted({*(repr(module.local(*item)) for item in _EXPORTS), "'build_router'", "'create_app'"})
        return application_template.render(
            final=final,
            options=options,
            routes=layout(Group("(", _items(routes), ")", ","), 0, len(f"ROUTES: {final} = "), WIDTH),
            info=layout(info, 0, len(f"INFO: {final}[{options}] = "), WIDTH),
            build_signature=_builder(module, "build_router", router, groups, secured=secured),
            build=module.local("_runtime.server.application", "build"),
            schemes=f"{module.local('_generated', 'contract')}.SCHEMES",
            services=_services(services),
            settings=_settings(secured=secured),
            app_signature=_builder(
                module,
                "create_app",
                fastapi,
                groups,
                ("fastapi_options", f"{options} | None", " = None"),
                secured=secured,
            ),
            arguments=(*services, *_settings(secured=secured)),
            fastapi=fastapi,
            application_options=module.local("_runtime.server.application", "application_options"),
            exports=exports,
            imports=module.imports(),
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
        groups = () if group is None else (group,)
        services = [group.stem for group in groups]
        secured = any(group.secured for group in groups)
        reserved = {"router", "wiring", *BUILDER_NAMES, *services}
        for spec in operations:
            reserved.update((
                spec.python_name,
                f"_add_{spec.python_name}",
                *(argument.name for argument in spec.arguments),
            ))
        module = Module(reserved, self.symbols, level=level)
        routes = [self.route(module, spec) for spec in operations]
        contract = module.local("_generated", "contract")
        pairs = {
            templated: [
                Group("(", (("", f"{contract}.{spec.pascal}.OPERATION"), ("", f"_add_{spec.python_name}")), ")")
                for spec in operations
                if spec.route.templated is templated
            ]
            for templated in (False, True)
        }
        name = "every operation" if group is None or self.config.layout == "single" else f"the {group.stem} operations"
        router = module.name("fastapi", "APIRouter")
        final = module.name("typing", "Final")
        return router_template.render(
            docstring=f"Endpoints of {name}; regenerate them instead of editing.",
            routes=routes,
            router=router,
            wiring=module.local("_runtime.server.application", "Wiring"),
            final=final,
            literal=layout(Group("(", _items(pairs[False]), ")", ","), 0, len(f"LITERAL_ROUTES: {final} = "), WIDTH),
            templated=layout(Group("(", _items(pairs[True]), ")", ","), 0, len(f"TEMPLATED_ROUTES: {final} = "), WIDTH),
            signature=_builder(module, "build_router", router, groups, secured=secured),
            group=name,
            build=module.local("_runtime.server.application", "build"),
            schemes=f"{contract}.SCHEMES",
            services=_services(services),
            settings=_settings(secured=secured),
            imports=module.imports(),
        )

    def route(self, module: Module, spec: OperationSpec) -> dict[str, str]:
        """Return the fragments of one operation's endpoint: decorator, signature, and handler call."""
        plan = f"{module.local('_generated', 'contract')}.{spec.pascal}"
        names = {argument.name for argument in spec.arguments}
        handler = _unique(f"{spec.python_name}_handler", names)
        principal = "" if spec.security is None else _unique(f"{spec.python_name}_principal", {*names, handler})
        record = _unique("parameters", names)
        parameters: list[Doc] = [
            self.parameter(module, spec, argument, plan, principal)
            for argument in spec.arguments
            if argument.kind not in {"media_type", "adapter"}
        ]
        if any(argument.kind == "adapter" for argument in spec.arguments):
            annotated, depends = module.name("typing", "Annotated"), module.name("fastapi", "Depends")
            parameters.append(f"{record}: {annotated}[{plan}.Parameters, {depends}({plan}.PARAMETERS)]")
        native = spec.native_primary
        returns = "object" if native else module.name("fastapi.responses", "Response")
        asynchronous = spec.mode == "async"
        signature = Group(
            f"{'async def' if asynchronous else 'def'} {spec.python_name}(",
            _items(("*", *parameters)) if parameters else (),
            f") -> {returns}:",
        )
        call = Group(
            f"{'await ' if asynchronous else ''}{handler}(",
            tuple((f"{argument.name}=", _value(module, spec, argument, record)) for argument in spec.arguments),
            ")",
        )
        dispatch = module.local("_runtime.server.responses", "dispatch" if native else "respond")
        body = Group(f"return {dispatch}(", (("", call), ("", f"{plan}.RESPONSES")), ")")
        return {
            "adder": f"_add_{spec.python_name}",
            "handler": handler,
            "handlers": "async_handlers" if asynchronous else "handlers",
            "name": repr(spec.python_name),
            "principal": principal,
            "key": repr(spec.key),
            "registration": layout(_registration(module, spec), 4, 0, WIDTH),
            "signature": layout(signature, 4, 0, WIDTH),
            "body": layout(body, 8, 0, WIDTH),
        }

    def parameter(self, module: Module, spec: OperationSpec, argument: Argument, plan: str, principal: str) -> Doc:
        """Return the endpoint parameter of one handler argument that FastAPI or an adapter supplies."""
        if argument.kind == "request":
            return f"request: {module.name('fastapi', 'Request')}"
        if argument.kind == "principal":
            annotated, depends = module.name("typing", "Annotated"), module.name("fastapi", "Depends")
            return f"principal: {annotated}[object, {depends}({principal})]"
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

    def services_module(self) -> str:
        """Return the services module: one Protocol per router group with an abstract method per operation."""
        groups = self.plan.groups
        reserved = {"PrincipalT_contra", *(group.service for group in groups)}
        for spec in self.plan.operations:
            reserved.update(argument.name for argument in spec.arguments)
        module = Module(reserved, self.symbols, level=1, public=True)
        protocol = module.name("typing", "Protocol")
        single = self.config.layout == "single"
        protocols = [
            {
                "name": group.service,
                "base": f"{protocol}[PrincipalT_contra]" if group.secured else protocol,
                "docstring": f"Implement {'every operation' if single else f'the {group.stem} operations'}: "
                "subclass this Protocol, or give an object its methods.",
                "methods": [self.method(module, spec) for spec in group.operations],
            }
            for group in groups
        ]
        return services_template.render(
            typevar=module.name("typing_extensions", "TypeVar") if any(group.secured for group in groups) else "",
            abstract=module.name("abc", "abstractmethod"),
            protocols=protocols,
            imports=module.imports(),
        )

    def method(self, module: Module, spec: OperationSpec) -> str:
        """Return one operation's method: the keyword-only arguments the endpoint passes and the results it takes."""
        parameters = self.parameters(module, spec, "PrincipalT_contra")
        returns = layout(self.returns(module, spec), 4, len(") -> "), WIDTH)
        signature = Group(
            f"{'async def' if spec.mode == 'async' else 'def'} {spec.python_name}(",
            _items(("self", "*", *parameters) if parameters else ("self",)),
            f") -> {returns}: ...",
        )
        return layout(signature, 4, 0, WIDTH)

    def parameters(self, module: Module, spec: OperationSpec, principal: str) -> list[Doc]:
        """Return the keyword-only parameters of one operation's method, typed as the endpoint passes them."""
        return [f"{argument.name}: {self.surface(module, spec, argument, principal)}" for argument in spec.arguments]

    def surface(self, module: Module, spec: OperationSpec, argument: Argument, principal: str) -> str:  # noqa: PLR0911
        """Return the type a method receives for one argument."""
        match argument.kind:
            case "request":
                return module.name("fastapi", "Request")
            case "principal":
                return f"{principal} | None" if spec.security is not None and spec.security.anonymous else principal
            case "native" if argument.native is not None:
                return _surface(module, argument.native)
            case "adapter":
                return self.parameter_type(module, argument)
            case "media_type":
                return "str | None"
            case _:
                pass
        body = spec.body
        assert body is not None
        if body.decision.transport == "fastapi_native":
            use = body.media[0].use
            assert use is not None
            assert use.type is not None
            return module.static(use.type)
        return self.body_type(module, body)

    def returns(self, module: Module, spec: OperationSpec) -> Chain:
        """Return a method's result type: the bare primary payload, an HTTPResult of any payload, or a Response."""
        payload = module.local("responses", f"{spec.pascal}ResponsePayload")
        result = f"{module.local('_runtime.server.responses', 'HTTPResult')}[{payload}]"
        return Chain("|", (*self.bare(module, spec), result, module.name("fastapi.responses", "Response")))

    def bare(self, module: Module, spec: OperationSpec) -> list[str]:
        """Return the members of the type a method returns bare: the primary payload, None, or nothing.

        A primary response that requires a header takes nothing bare, since a bare value cannot carry the header.
        """
        if (primary := spec.primary) is None or any(header.required for header in primary.response.headers):
            return []
        if (
            (media := primary.media) is None
            or spec.head
            or primary.status < _MIN_CONTENT_STATUS
            or primary.status in BODYLESS_STATUSES
        ):
            return ["None"]
        return self.media_type(module, media, sent=True).split(" | ")

    def contract(self) -> str:
        """Return the contract module: key types, schemes, and each operation's plans and request adapters."""
        reserved = {"OperationKey", "SchemeKey", "OperationDependencies", "SCHEMES"}
        module = Module({*reserved, *(spec.pascal for spec in self.plan.operations)}, self.symbols, level=2)
        sections = [self.operation_plan(module, spec) for spec in self.plan.operations]
        alias = module.name("typing", "TypeAlias")
        dependencies: Doc = (
            Group("{", tuple((f"{spec.key!r}: ", _dependency_sequence(module)) for spec in self.plan.operations), "}")
            if self.plan.operations
            else "{}"
        )
        typed = Group(
            f"{module.name('typing', 'TypedDict')}(",
            (("", "'OperationDependencies'"), ("", dependencies), ("total=", "False")),
            ")",
        )
        schemes = Group("(", _items(_scheme_plan(module, scheme) for scheme in self.plan.schemes), ")", ",")
        final = module.name("typing", "Final")
        head = (
            '"""Operation plans of this package; regenerate them instead of editing."""\n\n'
            "from __future__ import annotations\n\n"
        )
        keys = (
            ("OperationKey", [spec.key for spec in self.plan.operations]),
            ("SchemeKey", [scheme.name for scheme in self.plan.schemes]),
        )
        definitions = "".join(
            f"{name}: {alias} = {_literal(module, values, len(f'{name}: {alias} = '))}\n" for name, values in keys
        ) + (
            f"OperationDependencies = {layout(typed, 0, len('OperationDependencies = '), WIDTH)}\n"
            f"SCHEMES: {final} = {layout(schemes, 0, len(f'SCHEMES: {final} = '), WIDTH)}\n"
        )
        return head + module.imports() + "\n\n" + definitions + "\n\n" + "\n\n".join(sections)

    def codec(self, module: Module, use: TypeUseId) -> str:
        """Return the (codec accessor, context) pair of one bound use."""
        accessors = self.accessors[use]
        bindings = module.name(".", "model_bindings")
        return f"({bindings}.{accessors.codec}, {bindings}.{accessors.context})"

    def envelope(self, use: TypeUseId) -> bool:
        """Return whether a use's codec projects into an envelope."""
        binding = self.use_bindings.get(use)
        return binding is not None and binding.projection_mode == "envelope"

    def operation_plan(self, module: Module, spec: OperationSpec) -> str:
        """Return one operation's plan class: adapter parameter record, request adapters, and responses."""
        final = module.name("typing", "Final")
        lines = [f"class {spec.pascal}:", f'    """Plans of the {spec.python_name} operation."""', ""]
        operation = self.operation(module, spec)
        lines.append(f"    OPERATION: {final} = {layout(operation, 4, len('OPERATION: Final = '), WIDTH)}")
        adapters = [argument for argument in spec.arguments if argument.kind == "adapter"]
        if adapters:
            lines.extend((
                f"    @{module.name('dataclasses', 'dataclass')}(frozen=True, slots=True, kw_only=True)",
                "    class Parameters:",
                f'        """The adapter parameters of {spec.python_name}."""',
                "",
                *(f"        {argument.name}: {self.parameter_type(module, argument)}" for argument in adapters),
                "",
            ))
            adapter = self.parameter_adapter(module, spec, adapters)
            lines.append(f"    PARAMETERS: {final} = {layout(adapter, 4, len('PARAMETERS: Final = '), WIDTH)}")
        if spec.body is not None and spec.body.decision.transport == "codec_adapter":
            body = self.body_adapter(module, spec.body)
            lines.append(f"    BODY: {final} = {layout(body, 4, len('BODY: Final = '), WIDTH)}")
        responses = self.responses_plan(module, spec)
        lines.append(f"    RESPONSES: {final} = {layout(responses, 4, len('RESPONSES: Final = '), WIDTH)}")
        return "\n".join(lines) + "\n"

    def operation(self, module: Module, spec: OperationSpec) -> Group:
        """Return the OperationPlan constructor of one operation: method, key, service, keywords, mode, and security."""
        items: list[tuple[str, Doc]] = [
            ("name=", repr(spec.python_name)),
            ("key=", repr(spec.key)),
            ("service=", repr(self.services[spec.group])),
        ]
        if spec.arguments:
            items.append(("keywords=", _keywords(spec.arguments)))
        if spec.mode == "async":
            items.append(("asynchronous=", "True"))
        if spec.security is not None:
            requirements = Group("(", _items(_requirement(item) for item in spec.security.requirements), ")", ",")
            plan = Group(
                f"{module.local('_runtime.server.security', 'SecurityPlan')}(", (("requirements=", requirements),), ")"
            )
            items.append(("security=", plan))
        return Group(f"{module.local('_runtime.server.application', 'OperationPlan')}(", tuple(items), ")")

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

    def parameter_adapter(self, module: Module, spec: OperationSpec, adapters: list[Argument]) -> Group:
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
                entries.append(("codec=", self.codec(module, use.id)))
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

    def body_adapter(self, module: Module, body: BodySpec) -> Group:
        """Return the BodyAdapter constructor of an adapter body."""
        media: list[Doc] = []
        names: set[str] = set()
        for item in body.media:
            entries: list[tuple[str, Doc]] = [
                ("media_type=", repr(item.media_type)),
                ("kind=", repr(_request_kind(item))),
            ]
            if item.kind != "binary" and item.use is not None and item.use.id in self.accessors:
                entries.append(("codec=", self.codec(module, item.use.id)))
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

    def responses_plan(self, module: Module, spec: OperationSpec) -> Group:
        """Return the OperationResponses constructor of one operation."""
        responses = _items(self.response_plan(module, response, head=spec.head) for response in spec.responses)
        items: list[tuple[str, Doc]] = [("responses=", Group("(", responses, ")", ","))]
        if (primary := spec.primary) is not None:
            media = None if primary.media is None or spec.head else primary.media.media_type
            items.append(("primary=", f"({primary.status}, {media!r})"))
        if spec.head:
            items.append(("head=", "True"))
        return Group(f"{module.local('_runtime.server.responses', 'OperationResponses')}(", tuple(items), ")")

    def response_plan(self, module: Module, response: ResponseSpec, *, head: bool) -> Group:
        """Return the ResponsePlan constructor of one declared response."""
        items: list[tuple[str, Doc]] = [("status=", repr(response.status))]
        media: list[Doc] = []
        for item in () if head else response.media:
            entries: list[tuple[str, Doc]] = [
                ("media_type=", repr(item.media_type)),
                ("kind=", repr(_response_kind(item))),
            ]
            if item.kind != "binary" and item.use is not None and item.use.id in self.accessors:
                entries.append(("codec=", self.codec(module, item.use.id)))
            media.append(Group(f"{module.local('_runtime.server.responses', 'MediaPlan')}(", tuple(entries), ")"))
        if media:
            items.append(("media=", Group("(", _items(media), ")", ",")))
        if headers := [self.header_plan(module, header) for header in response.headers]:
            items.append(("headers=", Group("(", _items(headers), ")", ",")))
        return Group(f"{module.local('_runtime.server.responses', 'ResponsePlan')}(", tuple(items), ")")

    def header_plan(self, module: Module, header: HeaderSpec) -> Group:
        """Return the HeaderPlan constructor of one declared response header."""
        entries: list[tuple[str, Doc]] = [("name=", repr(header.name))]
        if header.required:
            entries.append(("required=", "True"))
        if header.plan is not None and header.use is not None and header.use.id in self.accessors:
            entries.extend((
                ("plan=", _parameter_plan(module, header.plan)),
                ("codec=", self.codec(module, header.use.id)),
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
        declaration = (
            alias + layout(Group(f"class {name}(", (("", base),), "):"), 0, 0, WIDTH),
            f'    """Outbound codecs of the {spec.python_name} response bodies."""',
        )
        overloads = _overloads(module, branches, union)
        lines = (
            (
                *declaration,
                "",
                *overloads,
                f"    {_body_signature('int', 'str | None = None', f'{union}:')}",
                '        """Return the outbound codec of one declared response body."""',
                "        return self.select(status_code, media_type)",
            )
            if overloads
            else declaration
        )
        bodies: dict[str, list[Doc]] = {}
        for status, media_type, _, accessor in branches:
            bindings = module.local("_generated", "model_bindings")
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


def _registration(module: Module, spec: OperationSpec) -> Group:
    contract = spec.contract
    facts = {name: getattr(value, "value", None) for name, value in contract.facts}
    items: list[tuple[str, Doc]] = [
        ("", repr(spec.route.route_path)),
        ("", spec.python_name),
        ("methods=", f"[{contract.method.upper()!r}]"),
        ("status_code=", str(spec.registration_status)),
    ]
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
    items.append(("dependencies=", f"wiring.dependencies.get({spec.key!r})"))
    return Group("router.add_api_route(", tuple(items), ")")


def _value(module: Module, spec: OperationSpec, argument: Argument, record: str) -> str:
    if argument.kind == "adapter":
        return f"{record}.{argument.name}"
    if argument.kind == "media_type":
        return "body[0]"
    if argument.kind == "body" and spec.body is not None and len(spec.body.media) > 1:
        return "body[1]"
    if argument.native is not None and argument.native.default is Default.ABSENT:
        return f"{module.local('_runtime.server.requests', 'present')}({argument.name})"
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


def _overloads(module: Module, branches: list[tuple[str, str, str, str]], union: str) -> list[str]:
    statuses: dict[str, list[tuple[str, str]]] = {}
    for status, media_type, kind, _ in branches:
        if status.isdigit():
            statuses.setdefault(status, []).append((media_type, kind))
    if not statuses:
        return []
    overload, literal = module.name("typing", "overload"), module.name("typing", "Literal")
    lines: list[str] = []
    for status, media in statuses.items():
        code = f"{literal}[{status}]"
        if len(media) == 1:
            lines.extend(_overload(overload, code, f"{literal}[{media[0][0]!r}] | None = None", media[0][1]))
            continue
        for media_type, kind in media:
            lines.extend(_overload(overload, code, f"{literal}[{media_type!r}]", kind))
        lines.extend(_overload(overload, code, "None = None", _default_kind(media)))
    lines.extend(_overload(overload, "int", "str | None = None", union))
    return lines


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
            for dots, target in _RELATIVE_IMPORT.findall(source.read_text(encoding="utf-8"))
        )
    return graph


def _settings(*, secured: bool) -> tuple[str, ...]:
    return ("authorizer", "credential_extractors", *_SETTINGS) if secured else _SETTINGS


def _services(services: list[str]) -> str:
    return layout(
        Group("{", tuple((f"{service!r}: ", service) for service in services), "}"), 8, len("services="), WIDTH
    )


def _builder(
    module: Module, name: str, returns: str, groups: Iterable[GroupSpec], *extra: tuple[str, Doc, str], secured: bool
) -> str:
    parameters: tuple[tuple[str, Doc, str], ...] = (
        *((group.stem, _service(module, group), "") for group in groups),
        *(_security(module) if secured else ()),
        ("dependencies", _dependency_sequence(module), " = ()"),
        ("operation_dependencies", f"{module.local('_generated.contract', 'OperationDependencies')} | None", " = None"),
        ("prefix", "str", ' = ""'),
        *extra,
    )
    lines = "".join(
        f"    {key}: {layout(annotation, 4, len(key) + len(default) + 3, WIDTH)}{default},\n"
        for key, annotation, default in parameters
    )
    return f"def {name}(\n    *,\n{lines}) -> {returns}:"


def _service(module: Module, group: GroupSpec) -> str:
    service = module.local("services", group.service)
    return f"{service}[{module.local('_runtime.server.security', 'PrincipalT')}]" if group.secured else service


def _security(module: Module) -> tuple[tuple[str, Doc, str], ...]:
    secret = module.local("_runtime.server.security", "SecretT")
    principal = module.local("_runtime.server.security", "PrincipalT")
    authorizers = (module.local("auth_types", "Authorizer"), module.local("auth_types", "AsyncAuthorizer"))
    return (
        ("authorizer", Chain("|", tuple(f"{item}[{secret}, {principal}]" for item in authorizers)), ""),
        ("credential_extractors", f"{module.local('auth_types', 'CredentialExtractors')}[{secret}] | None", " = None"),
    )


def _surface(module: Module, field: NativeField) -> str:
    if field.scalar is None:
        upload = module.name("fastapi", "UploadFile")
        text = f"list[{upload}]" if field.array else upload
    else:
        scalar = _scalar(module, field.scalar, ())
        text = f"list[{scalar}]" if field.array else scalar
    if field.default is Default.ABSENT:
        return f"{text} | {module.local('_runtime.model_codecs.unset', 'Unset')}"
    return text


def _dependency_sequence(module: Module) -> str:
    return f"{module.name('collections.abc', 'Sequence')}[{module.local('_runtime.server.application', 'Dependency')}]"


def _literal(module: Module, values: list[str], used: int) -> str:
    if not values:
        return module.name("typing_extensions", "Never")
    return layout(
        Group(f"{module.name('typing', 'Literal')}[", _items(repr(value) for value in values), "]"), 0, used, WIDTH
    )


def _scheme_plan(module: Module, scheme: SchemeSpec) -> Group:
    items: list[tuple[str, Doc]] = [("name=", repr(scheme.name)), ("kind=", repr(scheme.kind))]
    if scheme.location is not None:
        items.extend((("location=", repr(scheme.location)), ("parameter=", repr(scheme.parameter))))
    return Group(f"{module.local('_runtime.server.security', 'SchemePlan')}(", tuple(items), ")")


def _requirement(requirement: Requirement) -> Group:
    return Group(
        "(", _items(Group("(", (("", repr(name)), ("", repr(scopes))), ")") for name, scopes in requirement), ")", ","
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

from .application import (
    AsyncAuthorizer,
    AsyncCredentialExtractor,
    Authorizer,
    CredentialExtractor,
    CredentialExtractors,
    Dependency,
    FastAPIOptions,
    OperationDependencies,
    OperationKey,
    SchemeKey,
    build_router,
    create_app,
)
from .errors import AuthConfigurationError, HandlerConfigurationError, OpenAPIConfigurationError
from .responses import UNSET, HTTPResult, Unset

__all__ = [
    "UNSET",
    "AsyncAuthorizer",
    "AsyncCredentialExtractor",
    "AuthConfigurationError",
    "Authorizer",
    "CredentialExtractor",
    "CredentialExtractors",
    "Dependency",
    "FastAPIOptions",
    "HTTPResult",
    "HandlerConfigurationError",
    "OpenAPIConfigurationError",
    "OperationDependencies",
    "OperationKey",
    "SchemeKey",
    "Unset",
    "build_router",
    "create_app",
]
'''
_ERRORS: Final = '''"""Errors a generated server raises while it builds routers and applications."""

from ._runtime.server.application import HandlerConfigurationError, OpenAPIConfigurationError
from ._runtime.server.security import AuthConfigurationError

__all__ = ["AuthConfigurationError", "HandlerConfigurationError", "OpenAPIConfigurationError"]
'''
_AUTH_TYPES: Final = '''"""Authentication records and protocols keyed by this package's operation and scheme names."""

from collections.abc import Mapping
from typing import TypeAlias

from typing_extensions import TypeVar

from ._generated.contract import OperationKey, SchemeKey
from ._runtime.server.security import ApiKeySecret, BasicSecret, BearerSecret, CustomSecret
from ._runtime.server.security import AsyncAuthorizer as _AsyncAuthorizer
from ._runtime.server.security import AsyncCredentialExtractor as _AsyncCredentialExtractor
from ._runtime.server.security import AuthContext as _AuthContext
from ._runtime.server.security import Authorizer as _Authorizer
from ._runtime.server.security import Credential as _Credential
from ._runtime.server.security import CredentialExtractor as _CredentialExtractor
from ._runtime.server.security import RequirementCandidate as _RequirementCandidate
from ._runtime.server.security import SchemeRequirement as _SchemeRequirement

_SecretT = TypeVar("_SecretT")
_SecretT_co = TypeVar("_SecretT_co", covariant=True)
_SecretT_contra = TypeVar("_SecretT_contra", contravariant=True)
_PrincipalT_co = TypeVar("_PrincipalT_co", covariant=True)

Credential: TypeAlias = _Credential[_SecretT_co, SchemeKey]
SchemeRequirement: TypeAlias = _SchemeRequirement[SchemeKey]
RequirementCandidate: TypeAlias = _RequirementCandidate[_SecretT_co, SchemeKey]
AuthContext: TypeAlias = _AuthContext[_SecretT_co, SchemeKey, OperationKey]
Authorizer: TypeAlias = _Authorizer[_SecretT_contra, _PrincipalT_co, SchemeKey, OperationKey]
AsyncAuthorizer: TypeAlias = _AsyncAuthorizer[_SecretT_contra, _PrincipalT_co, SchemeKey, OperationKey]
CredentialExtractor: TypeAlias = _CredentialExtractor[_SecretT_co, SchemeKey]
AsyncCredentialExtractor: TypeAlias = _AsyncCredentialExtractor[_SecretT_co, SchemeKey]
CredentialExtractors: TypeAlias = Mapping[SchemeKey, CredentialExtractor[_SecretT] | AsyncCredentialExtractor[_SecretT]]

__all__ = [
    "ApiKeySecret",
    "AsyncAuthorizer",
    "AsyncCredentialExtractor",
    "AuthContext",
    "Authorizer",
    "BasicSecret",
    "BearerSecret",
    "Credential",
    "CredentialExtractor",
    "CredentialExtractors",
    "CustomSecret",
    "OperationKey",
    "RequirementCandidate",
    "SchemeKey",
    "SchemeRequirement",
]
'''
