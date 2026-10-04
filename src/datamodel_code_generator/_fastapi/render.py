"""Render the Python modules of a FastAPI server target from its plan."""

from __future__ import annotations

import keyword
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._api_generation import RenderedFile
from datamodel_code_generator._api_types import APIGenerationError
from datamodel_code_generator._codec_type_source import Namespace, TypeSource
from datamodel_code_generator._fastapi._compiled_templates import application as application_template
from datamodel_code_generator._fastapi._compiled_templates import readme as readme_template
from datamodel_code_generator._fastapi._compiled_templates import router as router_template
from datamodel_code_generator._fastapi._compiled_templates import services as services_template
from datamodel_code_generator._fastapi.documentation import documentation
from datamodel_code_generator._fastapi.naming import normalize
from datamodel_code_generator._fastapi.plan import CONSTRAINED, Default, default_media, fact, symbol_imports
from datamodel_code_generator._fastapi.routes import BUILDER_NAMES, tags
from datamodel_code_generator._fastapi.templates import OPERATION, ROUTER, invalid
from datamodel_code_generator._python_layout import Chain, Doc, Group, layout
from datamodel_code_generator._runtime.model_codecs.media import media_kind
from datamodel_code_generator._runtime.model_codecs.wire import checked_wire, thaw_wire
from datamodel_code_generator._target_contract import ConstructorType, LiteralScalar, LiteralSequence
from datamodel_code_generator._target_render import field_plan, parameter_plan, runtime_sources

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping

    from datamodel_code_generator._fastapi.config import FastAPIConfig
    from datamodel_code_generator._fastapi.context import FastAPIContext
    from datamodel_code_generator._fastapi.documentation import Documentation
    from datamodel_code_generator._fastapi.plan import (
        Argument,
        BodySpec,
        GroupSpec,
        MediaSpec,
        NativeField,
        OperationSpec,
        ParameterSpec,
        Requirement,
        SchemeKind,
        SchemeSpec,
        ServerPlan,
    )
    from datamodel_code_generator._fastapi.templates import TemplateSet
    from datamodel_code_generator._openapi_codec_plan import PydanticBackend
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue
    from datamodel_code_generator._target_contract import (
        FinalPythonType,
        GeneratedEnumMember,
        GeneratedTypeContractBatch,
        TypeArgument,
    )

WIDTH: Final = 88
_MIN_CONTENT_STATUS: Final = 200
_BASES: Final = {
    "conbytes": (None, "bytes"),
    "condecimal": ("decimal", "Decimal"),
    "confloat": (None, "float"),
    "conint": (None, "int"),
    "constr": (None, "str"),
}
_SETTINGS: Final = ("dependencies", "operation_dependencies", "prefix")
_EXPORTS: Final = (
    ("_generated.contract", "OperationDependencies"),
    ("_generated.contract", "OperationKey"),
    ("_runtime.server.application", "Dependency"),
    ("_runtime.server.application", "FastAPIOptions"),
    ("_runtime.server.security", "AsyncAuthorize"),
    ("_runtime.server.security", "Authorize"),
    ("_runtime.server.security", "Credentials"),
    ("_runtime.server.security", "RequirementSets"),
)
_CREDENTIALS: Final[dict[SchemeKind, str]] = {
    "basic": "HTTP Basic credentials, as `HTTPBasicCredentials`",
    "bearer": "a bearer token, as `HTTPAuthorizationCredentials`",
    "digest": "HTTP Digest credentials, as `HTTPAuthorizationCredentials`",
    "oauth2": "the `Authorization` header, as FastAPI's `OAuth2` reads it",
    "openid": "the `Authorization` header, as FastAPI's `OpenIdConnect` reads it",
    "custom": "no FastAPI class reads it: override its dependency to return the credential",
}
_SCHEME_CLASSES: Final[dict[SchemeKind, str]] = {
    "basic": "HTTPBasic",
    "bearer": "HTTPBearer",
    "digest": "HTTPDigest",
    "oauth2": "OAuth2",
    "openid": "OpenIdConnect",
}
_API_KEY_CLASSES: Final = {"header": "APIKeyHeader", "query": "APIKeyQuery", "cookie": "APIKeyCookie"}
_CHALLENGES: Final[dict[SchemeKind, str]] = {
    "api_key": "APIKey",
    "basic": "Basic",
    "bearer": "Bearer",
    "digest": "Digest",
    "oauth2": "Bearer",
    "openid": "Bearer",
}
_SCHEME_FACTS: Final = ("bearerFormat", "openIdConnectUrl", "flows", "description")


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


class _Annotations(TypeSource):
    """Spell final types as runtime annotations that type checkers also read.

    A constrained scalar such as `conint(ge=1)` becomes `Annotated[int, Field(ge=1)]`, which validates the same.
    """

    __slots__ = ()

    def parts(self, value: FinalPythonType) -> tuple[str, tuple[str, ...]]:
        """Return a type's runtime base and the Annotated metadata that constrains a constrained scalar."""
        if (
            not isinstance(value, ConstructorType)
            or ((imported := value.callable.import_).from_, imported.import_) not in CONSTRAINED
        ):
            return self.runtime(value), ()
        module, name = _BASES[imported.import_]
        base = name if module is None else self._namespace.name(module, name)
        metadata = self._namespace.name("pydantic", "StringConstraints" if name == "str" else "Field")
        return base, (f"{metadata}({self._keywords(value.keywords)})",)

    def literal(self, value: LiteralScalar) -> str:
        """Return the Python literal of a scalar value."""
        return self._spell(value, static=False)

    def _spell(self, value: FinalPythonType | TypeArgument | GeneratedEnumMember, *, static: bool) -> str:
        if static or not isinstance(value, ConstructorType):
            return super()._spell(value, static=static)
        base, metadata = self.parts(value)
        return f"{self._namespace.name('typing', 'Annotated')}[{base}, {', '.join(metadata)}]" if metadata else base


class Module:
    """One generated module: collision-free import aliases, type spellings, and runtime imports at its depth."""

    def __init__(self, reserved: Iterable[str], symbols: Mapping[int, str], *, level: int) -> None:
        """Reserve the names the module defines, and remember its depth."""
        self.namespace = Namespace(reserved)
        self.types = _Annotations(self.namespace, symbols)
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

    def annotation(self, value: FinalPythonType, *metadata: str) -> str:
        """Return the spelling FastAPI validates with: the model's runtime type, readable by type checkers.

        Extra metadata, such as a FastAPI parameter declaration, joins the type's own Annotated metadata.
        """
        base, own = self.types.parts(value)
        if not (items := (*own, *metadata)):
            return base
        return f"{self.name('typing', 'Annotated')}[{base}, {', '.join(items)}]"

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
        backend: PydanticBackend,
        plan: ServerPlan,
        batch: GeneratedTypeContractBatch,
        wire: WirePlan,
        templates: TemplateSet | None = None,
        context: FastAPIContext | None = None,
        docs: Documentation | None = None,
    ) -> None:
        """Keep the plans.

        A template set overrides builtin roles and adds extra files, which render from the context. The documentation
        adds what FastAPI cannot derive from the routes to its own document.
        """
        self.config = config
        self.package = package
        self.backend = backend
        self.plan = plan
        self.templates = templates
        self.context = context
        self.docs = docs
        self.batch = batch
        self.wire = wire
        self.symbols = symbol_imports(batch)
        self.services = {group.key: group.stem for group in plan.groups}
        taken: set[str] = set()
        self.scheme_names: dict[str, str] = {}
        for scheme in plan.schemes:
            taken.add(name := _unique(normalize(scheme.name, empty="scheme", digit="s_"), taken))
            self.scheme_names[scheme.name] = name

    def role(self, name: str, compiled: Callable[..., str], **frame: object) -> Callable[..., str]:
        """Return the renderer of one builtin role: the template directory's override, or the compiled builtin."""
        if (templates := self.templates) is None or name not in templates.roles:
            return compiled
        values = {"context": self.context, **frame}

        def render(**context: object) -> str:
            return templates.render(name, {**context, **values})

        return render

    def router_frame(self, group: GroupSpec | None) -> dict[str, object]:
        """Return the router view and tag a router template receives."""
        if self.context is None or group is None:
            return {}
        view = next(router for router in self.context.routers if router.key == group.key)
        return {"router": view, "tag": view.primary_tag}

    def extras(self) -> Iterator[RenderedFile]:
        """Render the extra files of the template manifest, once for a project or each router or operation."""
        if (templates := self.templates) is None or (context := self.context) is None:
            return
        for extra in templates.extras:
            frames: list[tuple[str, dict[str, object]]]
            if extra.scope == "project":
                frames = [(extra.path, {})]
            elif extra.scope == "router":
                frames = [
                    (extra.path.replace(ROUTER, view.file_stem), {"router": view, "tag": view.primary_tag})
                    for view in context.routers
                ]
            else:
                frames = [
                    (extra.path.replace(OPERATION, view.python_name), {"operation": view})
                    for view in context.operations
                ]
            for path, frame in frames:
                text = templates.render(extra.template, {"context": context, **frame})
                if (problem := invalid(extra.format, text)) is not None:
                    raise APIGenerationError((templates.problem(f"{path} is not valid {extra.format}: {problem}"),))
                yield RenderedFile(path=self.package / path, kind="template", text=text, header=extra.header)

    def placed(self, files: tuple[RenderedFile, ...], extras: tuple[RenderedFile, ...]) -> None:
        """Reject extra files that take the path of a builtin file, a runtime module, or another extra file."""
        taken = {file.path for file in files}
        assert self.templates is not None
        for extra in extras:
            if extra.path in taken:
                message = f"Several generated files take {extra.path.as_posix()}"
                raise APIGenerationError((self.templates.conflict(message),))
            taken.add(extra.path)

    def file(self, path: PurePosixPath, kind: str, text: str, *, verbatim: bool = False) -> RenderedFile:
        """Return one owned file of the package."""
        return RenderedFile(path=self.package / path, kind=kind, text=text, verbatim=verbatim)

    def files(self) -> tuple[RenderedFile, ...]:
        """Return every rendered server file in the fixed artifact order."""
        generated = PurePosixPath("_generated")
        files = (
            self.file(PurePosixPath("__init__.py"), "package", _PACKAGE),
            self.file(PurePosixPath("application.py"), "application", self.application()),
            *self.routers(),
            self.file(generated / "__init__.py", "package", '"""Generated plans of this package."""\n'),
            self.file(generated / "contract.py", "contract", self.contract()),
            self.file(PurePosixPath("errors.py"), "errors", _ERRORS),
            *((self.file(PurePosixPath("security.py"), "security", self.security()),) if self.plan.schemes else ()),
            self.file(PurePosixPath("services.py"), "services", self.services_module()),
            RenderedFile(path=PurePosixPath("README.md"), kind="readme", text=self.readme()),
        )
        extras = tuple(self.extras())
        runtime = tuple(self.runtime((*files, *extras)))
        if extras:
            self.placed((*files, *runtime), extras)
        return (*files, *extras, *runtime)

    def readme(self) -> str:
        """Return the README of the target root: the operations, how to implement and connect them, and regeneration."""
        plan, config = self.plan, self.config
        info = dict(plan.info)
        groups = plan.groups
        arguments = [f"{group.stem}={_implementation(group)}()" for group in groups]
        return self.role("readme.jinja2", readme_template.render)(
            title=info.get("title", config.package),
            package=config.package,
            model_package=config.model_package,
            backend=self.backend,
            operations=[
                {
                    "service": group.service,
                    "name": spec.python_name,
                    "method": spec.contract.method.upper(),
                    "path": spec.contract.path.replace("|", "\\|"),
                    "key": spec.key.replace("|", "\\|"),
                }
                for group in plan.groups
                for spec in group.operations
            ],
            services=[
                {
                    "protocol": group.service,
                    "implementation": _implementation(group),
                    "secured": group.secured,
                    "methods": _listed([f"`{spec.python_name}`" for spec in group.operations]),
                }
                for group in groups
            ],
            protocols=", ".join(group.service for group in groups),
            arguments=", ".join((*arguments, *(("authorize=authorize",) if plan.schemes else ()))),
            schemes=[{"name": scheme.name, "credential": _credential(scheme)} for scheme in plan.schemes],
            forms=any(spec.body is not None and spec.body.form for spec in plan.operations),
            raw_request=any(
                spec.body is not None and spec.body.decision.transport == "raw_request" for spec in plan.operations
            ),
            standalone=config.package_mode == "standalone",
        )

    def runtime(self, files: tuple[RenderedFile, ...]) -> Iterator[RenderedFile]:
        """Copy the runtime modules the package imports, with their own imports, in ascending path order."""
        for path, text in runtime_sources(file.text for file in files):
            yield self.file(path, "runtime", text, verbatim=True)

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
        exports = sorted({
            *(repr(module.local(*item)) for item in _EXPORTS),
            "'build_router'",
            "'create_app'",
        })
        return self.role("application.jinja2", application_template.render)(
            final=final,
            options=options,
            routes=layout(Group("(", _items(routes), ")", ","), 0, len(f"ROUTES: {final} = "), WIDTH),
            info=layout(info, 0, len(f"INFO: {final}[{options}] = "), WIDTH),
            build_signature=_builder(module, "build_router", router, groups, secured=secured),
            build=module.local("_runtime.server.application", "build"),
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
            yield self.file(PurePosixPath("routes.py"), "router", self.router(group, level=1))
            return
        yield self.file(PurePosixPath("routers/__init__.py"), "package", '"""Router groups of this package."""\n')
        for group in self.plan.groups:
            yield self.file(PurePosixPath("routers", f"{group.stem}.py"), "router", self.router(group, level=2))

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
        return self.role("router.jinja2", router_template.render, **self.router_frame(group))(
            docstring=f"Endpoints of {name}; regenerate them instead of editing.",
            routes=routes,
            api_router=router,
            wiring=module.local("_runtime.server.application", "Wiring"),
            final=final,
            literal=layout(Group("(", _items(pairs[False]), ")", ","), 0, len(f"LITERAL_ROUTES: {final} = "), WIDTH),
            templated=layout(Group("(", _items(pairs[True]), ")", ","), 0, len(f"TEMPLATED_ROUTES: {final} = "), WIDTH),
            signature=_builder(module, "build_router", router, groups, secured=secured),
            group=name,
            build=module.local("_runtime.server.application", "build"),
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
        asynchronous = spec.mode == "async"
        signature = Group(
            f"{'async def' if asynchronous else 'def'} {spec.python_name}(",
            _items(("*", *parameters)) if parameters else (),
            ") -> object:",
        )
        call = Group(
            f"{'await ' if asynchronous else ''}{handler}(",
            tuple((f"{argument.name}=", _value(module, spec, argument, record)) for argument in spec.arguments),
            ")",
        )
        dispatch = module.local("_runtime.server.responses", "dispatch")
        body = Group(f"return {dispatch}(", (("", call), ("", f"{plan}.RESPONSES")), ")")
        return {
            "adder": f"_add_{spec.python_name}",
            "handler": handler,
            "handlers": "async_handlers" if asynchronous else "handlers",
            "name": repr(spec.python_name),
            "principal": "" if not principal else self.principal(module, spec, principal),
            "key": repr(spec.key),
            "registration": layout(self.registration(module, spec), 4, 0, WIDTH),
            "signature": layout(signature, 4, 0, WIDTH),
            "body": layout(body, 8, 0, WIDTH),
        }

    def principal(self, module: Module, spec: OperationSpec, name: str) -> str:
        """Return the dependency that authorizes an operation from the credentials of its schemes' dependencies."""
        assert spec.security is not None
        requirements = spec.security.requirements
        scopes: dict[str, dict[str, None]] = {}
        for requirement in requirements:
            for scheme, needed in requirement:
                scopes.setdefault(scheme, {}).update(dict.fromkeys(needed))
        schemes = {scheme.name: scheme for scheme in self.plan.schemes}
        security = module.local("", "security")
        annotated, depends = module.name("typing", "Annotated"), module.name("fastapi", "Security")
        parameters: list[Doc] = []
        for scheme, needed in scopes.items():
            arguments = f"{security}.{self.scheme_names[scheme]}" + (f", scopes={list(needed)!r}" if needed else "")
            kind = _credential_type(module, schemes[scheme])
            parameters.append(f"{self.scheme_names[scheme]}: {annotated}[{kind}, {depends}({arguments})]")
        signature = Group(f"async def {name}(", _items(("*", *parameters)), ") -> object:")
        credentials = Group("{", tuple((f"{scheme!r}: ", self.scheme_names[scheme]) for scheme in scopes), "}")
        challenge = ", ".join(
            dict.fromkeys(_CHALLENGES[kind] for scheme in scopes if (kind := schemes[scheme].kind) in _CHALLENGES)
        )
        call = Group(
            f"return await {module.local('_runtime.server.security', 'authenticate')}(",
            (
                ("", Group("(", _items(_requirement(item) for item in requirements), ")", ",")),
                ("", credentials),
                ("", "wiring.authorize"),
                ("", repr(challenge)),
            ),
            ")",
        )
        return f"{layout(signature, 4, 0, WIDTH)}\n        {layout(call, 8, 0, WIDTH)}"

    def security(self) -> str:
        """Return the security module: one overridable FastAPI dependency for each scheme the operations use."""
        module = Module(set(self.scheme_names.values()), self.symbols, level=1)
        definitions = [
            _scheme_dependency(module, scheme, self.scheme_names[scheme.name]) for scheme in self.plan.schemes
        ]
        names = sorted(self.scheme_names.values())
        listing = "".join(f"    {name!r},\n" for name in names)
        head = (
            '"""Security dependencies of this package, one for each scheme; override one with '
            'app.dependency_overrides."""\n'
        )
        imports = module.imports()
        return f"{head}\n{imports}\n\n\n" + "\n\n".join(definitions) + f"\n\n__all__ = [\n{listing}]\n"

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
            keywords = [f"media_type={media.media_type!r}"]
            if not body.required:
                keywords.append(f"default_factory={module.local('_runtime.server.requests', 'absent')}")
            api = module.name("fastapi", "Form" if body.form else "Body")
            return Group(f"{argument.name}: {annotated}[{_body(module, media)}, {api}(", _items(keywords), ")]")
        depends = module.name("fastapi", "Depends")
        kind = self.body_type(module, body)
        if len(body.media) > 1:
            return f"{argument.name}: {annotated}[tuple[str | None, {kind}], {depends}({plan}.BODY.receive)]"
        return f"{argument.name}: {annotated}[{kind}, {depends}({plan}.BODY)]"

    def body_type(self, module: Module, body: BodySpec) -> str:
        """Return the type the handler receives for a body, with UNSET for an optional one."""
        kinds = dict.fromkeys(self.media_type(module, media) for media in body.media)
        if not body.required:
            kinds[module.local("_runtime.model_codecs.unset", "Unset")] = None
        return " | ".join(kinds)

    @staticmethod
    def media_type(module: Module, media: MediaSpec) -> str:
        """Return one media's payload type: the model's type, or the media surface without a schema."""
        use = media.use
        if media.kind == "binary" or use is None or use.type is None:
            return module.name("typing", "Any") if media.kind == "json" else "str" if media.kind == "text" else "bytes"
        return module.static(use.type)

    def services_module(self) -> str:
        """Return the services module: one Protocol per router group with an abstract method per operation."""
        groups = self.plan.groups
        reserved = {"PrincipalT_contra", *(group.service for group in groups)}
        for spec in self.plan.operations:
            reserved.update(argument.name for argument in spec.arguments)
        module = Module(reserved, self.symbols, level=1)
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
        return self.role("services.jinja2", services_template.render)(
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

    def surface(self, module: Module, spec: OperationSpec, argument: Argument, principal: str) -> str:
        """Return the type a method receives for one argument."""
        match argument.kind:
            case "request":
                return module.name("fastapi", "Request")
            case "principal":
                return f"{principal} | None" if spec.security is not None and spec.security.anonymous else principal
            case "native" | "adapter" if argument.parameter is not None:
                return _parameter_type(module, argument.parameter)
            case "media_type":
                return "str | None"
            case _:
                pass
        body = spec.body
        assert body is not None
        return self.body_type(module, body)

    def returns(self, module: Module, spec: OperationSpec) -> Chain:
        """Return a method's result type: the bare primary payload, an HTTPResult of any payload, or a Response."""
        return Chain("|", tuple(self.results(module, spec)))

    def results(self, module: Module, spec: OperationSpec) -> list[str]:
        """Return the members of a method's result type, in the order the result type spells them."""
        payloads = dict.fromkeys(
            self.media_type(module, media)
            for response in spec.responses
            if _body_status(spec, response.status) and (media := default_media(response)) is not None
        )
        result = f"{module.local('_runtime.server.responses', 'HTTPResult')}[{' | '.join(payloads) or 'None'}]"
        return [*self.bare(module, spec), result, module.name("fastapi.responses", "Response")]

    def bare(self, module: Module, spec: OperationSpec) -> list[str]:
        """Return the members of the type a method returns bare: the primary payload, None, or nothing."""
        if (primary := spec.primary) is None:
            return []
        if (media := primary.media) is None or not _body_status(spec, str(primary.status)):
            return ["None"]
        return [self.media_type(module, media)]

    def contract(self) -> str:
        """Return the contract module: key types, schemes, and each operation's plans and request adapters."""
        reserved = {"OperationKey", "OperationDependencies"}
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
        head = (
            '"""Operation plans of this package; regenerate them instead of editing."""\n\n'
            "from __future__ import annotations\n\n"
        )
        keys = [spec.key for spec in self.plan.operations]
        definitions = (
            f"OperationKey: {alias} = {_literal(module, keys, len(f'OperationKey: {alias} = '))}\n"
            f"OperationDependencies = {layout(typed, 0, len('OperationDependencies = '), WIDTH)}\n"
        )
        return head + module.imports() + "\n\n" + definitions + "\n\n" + "\n\n".join(sections)

    def registration(self, module: Module, spec: OperationSpec) -> Group:
        """Return the route registration of one operation, with what FastAPI cannot derive from the route documented."""
        assert self.docs is not None
        return _registration(module, spec, self.docs)

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
                *(f"        {argument.name}: {_parameter_type(module, argument.parameter)}" for argument in adapters),
                "",
            ))
            adapter = _parameter_adapter(module, spec, adapters)
            lines.append(f"    PARAMETERS: {final} = {layout(adapter, 4, len('PARAMETERS: Final = '), WIDTH)}")
        if spec.body is not None and spec.body.decision.transport == "adapter":
            body = _body_adapter(module, spec.body)
            lines.append(f"    BODY: {final} = {layout(body, 4, len('BODY: Final = '), WIDTH)}")
        responses = _responses_plan(module, spec)
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
            items.append(("secured=", "True"))
        return Group(f"{module.local('_runtime.server.application', 'OperationPlan')}(", tuple(items), ")")


def _responses_plan(module: Module, spec: OperationSpec) -> Group:
    """Return the OperationResponses constructor of one operation: each declared response's default media and model."""
    responses: list[tuple[str, Doc]] = []
    for response in spec.responses:
        entries: list[tuple[str, Doc]] = []
        if _body_status(spec, response.status) and (media := default_media(response)) is not None:
            entries.append(("media_type=", repr(media.media_type)))
            if media.kind != "binary" and media.use is not None and media.use.type is not None:
                entries.append(("adapter=", _adapter(module, media.use.type)))
        declared = Group(f"{module.local('_runtime.server.responses', 'Declared')}(", tuple(entries), ")")
        responses.append((f"{response.status!r}: ", declared))
    items: list[tuple[str, Doc]] = [("responses=", Group("{", tuple(responses), "}"))]
    if (primary := spec.primary) is not None and not spec.native_primary:
        items.append(("primary=", str(primary.status)))
    if spec.head:
        items.append(("head=", "True"))
    return Group(f"{module.local('_runtime.server.responses', 'OperationResponses')}(", tuple(items), ")")


def _body_status(spec: OperationSpec, status: str) -> bool:
    """Return whether a declared response of an operation carries a body: not for HEAD, 1XX, 204, 205, or 304."""
    return not spec.head and not status.startswith("1") and status not in {"204", "205", "304"}


def _implementation(group: GroupSpec) -> str:
    return group.service.removesuffix("Service") or "Operations"


def _listed(names: list[str]) -> str:
    *rest, last = names
    if not rest:
        return last
    return f"{', '.join(rest)}{',' if len(rest) > 1 else ''} and {last}"


def _credential(scheme: SchemeSpec) -> str:
    if scheme.kind != "api_key":
        return _CREDENTIALS[scheme.kind]
    place = "query parameter" if scheme.location == "query" else scheme.location
    return f"an API key in the `{scheme.parameter}` {place}"


def _native(module: Module, name: str, field: NativeField) -> Doc:
    keywords = [f"alias={field.alias!r}"]
    if field.api == "Header":
        keywords.append("convert_underscores=False")
    keywords.extend(f"{key}={_python(value)}" for key, value in field.keywords)
    default = ""
    if field.default is Default.ABSENT:
        keywords.append(f"default_factory={module.local('_runtime.server.requests', 'absent')}")
    elif not isinstance(field.default, Default):
        default = f" = {_default(module, field.default)}"
    base, metadata = module.types.parts(field.type)
    head = ", ".join((base, *metadata, f"{module.name('fastapi', field.api)}("))
    return Group(f"{name}: {module.name('typing', 'Annotated')}[{head}", _items(keywords), f")]{default}")


def _body(module: Module, media: MediaSpec) -> str:
    return (
        module.name("typing", "Any")
        if media.use is None or media.use.type is None
        else module.annotation(media.use.type)
    )


def _adapter(module: Module, value: FinalPythonType) -> str:
    return f"{module.name('pydantic', 'TypeAdapter')}({module.annotation(value)})"


def _default(module: Module, value: LiteralScalar | LiteralSequence) -> str:
    if isinstance(value, LiteralScalar):
        return module.types.literal(value)
    return f"[{', '.join(module.types.literal(item) for item in value.items if isinstance(item, LiteralScalar))}]"


def _parameter_type(module: Module, parameter: ParameterSpec | None) -> str:
    """Return the type a method receives for a parameter: the model's type, or Any without one, with UNSET if absent."""
    assert parameter is not None
    if parameter.type is not None:
        text = module.static(parameter.type)
    elif parameter.plan is not None and media_kind(parameter.plan.content_media_type or "text/plain") == "text":
        text = "str"
    else:
        text = module.name("typing", "Any")
    if parameter.required or parameter.default is not Default.ABSENT:
        return text
    return f"{text} | {module.local('_runtime.model_codecs.unset', 'Unset')}"


def _parameter_adapter(module: Module, spec: OperationSpec, adapters: list[Argument]) -> Group:
    """Return the ParameterAdapter constructor of an operation's adapter parameters."""
    arguments: list[Doc] = []
    for argument in adapters:
        parameter = argument.parameter
        assert parameter is not None
        assert parameter.plan is not None
        entries: list[tuple[str, Doc]] = [
            ("name=", repr(argument.name)),
            ("plan=", parameter_plan(module.local, parameter.plan)),
        ]
        if parameter.type is not None:
            entries.append(("adapter=", _adapter(module, parameter.type)))
        if isinstance(parameter.default, (LiteralScalar, LiteralSequence)):
            entries.append(("default=", _default(module, parameter.default)))
        arguments.append(
            Group(f"{module.local('_runtime.server.requests', 'ParameterArgument')}(", tuple(entries), ")")
        )
    items: list[tuple[str, Doc]] = [
        ("arguments=", Group("(", _items(arguments), ")", ",")),
        ("record=", "Parameters"),
    ]
    if path := _raw_path(module, spec, adapters):
        items.append(("path=", path))
    return Group(f"{module.local('_runtime.server.requests', 'ParameterAdapter')}(", tuple(items), ")")


def _body_adapter(module: Module, body: BodySpec) -> Group:
    """Return the BodyAdapter constructor of an adapter body."""
    media: list[Doc] = []
    for item in body.media:
        entries: list[tuple[str, Doc]] = [
            ("media_type=", repr(item.media_type)),
            ("kind=", repr(_request_kind(item))),
        ]
        if item.kind != "binary" and item.use is not None and item.use.type is not None:
            entries.append(("adapter=", _adapter(module, item.use.type)))
        if item.kind in {"form", "multipart"} and (fields := body.form_fields):
            entries.append((
                "fields=",
                Group("(", _items(field_plan(module.local, plan) for plan in fields), ")", ","),
            ))
        media.append(Group(f"{module.local('_runtime.server.requests', 'BodyMedia')}(", tuple(entries), ")"))
    items: list[tuple[str, Doc]] = [("media=", Group("(", _items(media), ")", ","))]
    if not body.required:
        items.append(("required=", "False"))
    return Group(f"{module.local('_runtime.server.requests', 'BodyAdapter')}(", tuple(items), ")")


def _registration(module: Module, spec: OperationSpec, docs: Documentation) -> Group:
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
    if isinstance(description := _response_description(spec), str):
        items.append(("response_description=", repr(description)))
    if responses := docs.responses(spec):
        items.append(("responses=", _responses(module, spec, responses)))
    if extra := docs.openapi_extra(spec):
        items.append(("openapi_extra=", _json_literal(extra)))
    items.append(("dependencies=", f"wiring.dependencies.get({spec.key!r})"))
    return Group("router.add_api_route(", tuple(items), ")")


def _responses(module: Module, spec: OperationSpec, documented: dict[str, dict[str, JSONValue]]) -> Group:
    """Return the responses a route documents; FastAPI documents a JSON body of a model from the model itself."""
    models = {
        response.status: (str(media.declaration.name), media.use.type)
        for response in spec.responses
        for media in response.media
        if media.media_type == "application/json" and media.use is not None and media.use.type is not None
    }
    entries: list[tuple[str, Doc]] = []
    for status, response in documented.items():
        items: list[tuple[str, Doc]] = []
        content = response.get("content")
        if (model := models.get(status)) is not None and isinstance(content, dict) and content.pop(model[0]):
            items.append(("'model': ", module.annotation(model[1])))
            if not content:
                del response["content"]
        items.extend((f"{key!r}: ", _json_literal(item)) for key, item in response.items())
        entries.append((f"{status!r}: ", Group("{", tuple(items), "}")))
    return Group("{", tuple(entries), "}")


def _json_literal(value: JSONValue) -> Doc:
    """Return the Python literal of a JSON value, laid out like the rest of the module."""
    match value:
        case dict():
            return Group("{", tuple((f"{key!r}: ", _json_literal(item)) for key, item in value.items()), "}")
        case list():
            return Group("[", _items(_json_literal(item) for item in value), "]")
        case _:
            pass
    return repr(value)


def _response_description(spec: OperationSpec) -> object:
    if (primary := spec.primary) is not None:
        return fact(primary.response.declaration, "description")
    responses = {response.status: response for response in spec.responses}
    status = str(spec.registration_status)
    represented = responses.get("default", responses.get(f"{status[0]}XX"))
    return None if represented is None else fact(represented.declaration, "description")


def _value(module: Module, spec: OperationSpec, argument: Argument, record: str) -> str:
    if argument.kind == "adapter":
        return f"{record}.{argument.name}"
    if argument.kind == "media_type":
        return "body[0]"
    if argument.kind == "body" and spec.body is not None and len(spec.body.media) > 1:
        return "body[1]"
    native = argument.native
    body = spec.body
    absent = (native is not None and native.default is Default.ABSENT) or (
        argument.kind == "body"
        and body is not None
        and body.decision.transport == "fastapi_native"
        and not body.required
    )
    return f"{module.local('_runtime.server.requests', 'present')}({argument.name})" if absent else argument.name


def _raw_path(module: Module, spec: OperationSpec, adapters: list[Argument]) -> Group | None:
    if not (wanted := {item.wire_name for item in adapters if item.location == "path" and item.wire_name is not None}):
        return None
    return Group(
        f"{module.local('_runtime.server.requests', 'RawPath')}(",
        (("template=", repr(spec.route.path)), ("names=", _frozenset(wanted))),
        ")",
    )


def _settings(*, secured: bool) -> tuple[str, ...]:
    return ("authorize", *_SETTINGS) if secured else _SETTINGS


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
    principal = module.local("_runtime.server.security", "PrincipalT")
    authorizers = (
        module.local("_runtime.server.security", "Authorize"),
        module.local("_runtime.server.security", "AsyncAuthorize"),
    )
    return (("authorize", Chain("|", tuple(f"{item}[{principal}]" for item in authorizers)), ""),)


def _dependency_sequence(module: Module) -> str:
    return f"{module.name('collections.abc', 'Sequence')}[{module.local('_runtime.server.application', 'Dependency')}]"


def _literal(module: Module, values: list[str], used: int) -> str:
    if not values:
        return module.name("typing_extensions", "Never")
    return layout(
        Group(f"{module.name('typing', 'Literal')}[", _items(repr(value) for value in values), "]"), 0, used, WIDTH
    )


def _credential_type(module: Module, scheme: SchemeSpec) -> str:
    """Return the type of the credential a scheme's FastAPI dependency returns."""
    match scheme.kind:
        case "basic":
            return f"{module.name('fastapi.security', 'HTTPBasicCredentials')} | None"
        case "bearer" | "digest":
            return f"{module.name('fastapi.security', 'HTTPAuthorizationCredentials')} | None"
        case "custom":
            return "object"
        case _:
            pass
    return "str | None"


def _scheme_dependency(module: Module, scheme: SchemeSpec, name: str) -> str:
    """Return the module-level dependency of one scheme: a FastAPI security class, or a function to override."""
    if scheme.kind == "custom":
        return (
            f"def {name}() -> object:\n"
            f'    """Return the {scheme.name} credential, which no FastAPI security class reads; override this '
            'dependency."""\n'
            "    return None\n"
        )
    facts = dict(scheme.declaration.facts)
    keywords: list[tuple[str, Doc]] = []
    if scheme.kind == "api_key":
        keywords.append(("name=", repr(scheme.parameter)))
        cls = _API_KEY_CLASSES[str(scheme.location)]
    else:
        cls = _SCHEME_CLASSES[scheme.kind]
    keywords.extend(
        (f"{keyword}=", _json_literal(value))
        for keyword in _SCHEME_FACTS
        if (fact_value := facts.get(keyword)) is not None and (value := documentation(fact_value)) is not None
    )
    keywords.extend((("scheme_name=", repr(scheme.name)), ("auto_error=", "False")))
    return (
        f"{name} = "
        + layout(Group(f"{module.name('fastapi.security', cls)}(", tuple(keywords), ")"), 0, len(name) + 3, WIDTH)
        + "\n"
    )


def _requirement(requirement: Requirement) -> Group:
    return Group(
        "(", _items(Group("(", (("", repr(name)), ("", repr(scopes))), ")") for name, scopes in requirement), ")", ","
    )


def _keywords(arguments: tuple[Argument, ...]) -> Doc:
    return Group("(", _items(repr(argument.name) for argument in arguments), ")", ",")


def _frozenset(names: set[str]) -> str:
    return f"frozenset({{{', '.join(repr(name) for name in sorted(names))}}})"


def _request_kind(media: MediaSpec) -> str:
    return media.kind if media.kind in {"json", "text", "form", "multipart"} else "binary"


_PACKAGE: Final = '''"""FastAPI server generated by datamodel-code-generator."""

from .application import (
    AsyncAuthorize,
    Authorize,
    Credentials,
    Dependency,
    FastAPIOptions,
    OperationDependencies,
    OperationKey,
    RequirementSets,
    build_router,
    create_app,
)
from .errors import AuthConfigurationError, HandlerConfigurationError, OpenAPIConfigurationError
from ._runtime.model_codecs.unset import UNSET, Unset
from ._runtime.server.responses import HTTPResult

__all__ = [
    "UNSET",
    "AsyncAuthorize",
    "AuthConfigurationError",
    "Authorize",
    "Credentials",
    "Dependency",
    "FastAPIOptions",
    "HTTPResult",
    "HandlerConfigurationError",
    "OpenAPIConfigurationError",
    "OperationDependencies",
    "OperationKey",
    "RequirementSets",
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
