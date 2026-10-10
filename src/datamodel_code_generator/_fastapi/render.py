"""Render the Python modules of a FastAPI server target from its plan."""

from __future__ import annotations

import keyword
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._api_generation import RenderedFile
from datamodel_code_generator._fastapi._compiled_templates import application as application_template
from datamodel_code_generator._fastapi._compiled_templates import contract as contract_template
from datamodel_code_generator._fastapi._compiled_templates import facade as facade_template
from datamodel_code_generator._fastapi._compiled_templates import openapi as openapi_template
from datamodel_code_generator._fastapi._compiled_templates import readme as readme_template
from datamodel_code_generator._fastapi._compiled_templates import router as router_template
from datamodel_code_generator._fastapi._compiled_templates import security as security_template
from datamodel_code_generator._fastapi._compiled_templates import services as services_template
from datamodel_code_generator._fastapi.plan import (
    Default,
    MemberDefault,
    ParameterDefault,
    RootDefault,
    default_media,
    fact,
    json_value,
)
from datamodel_code_generator._fastapi.routes import BUILDER_NAMES, tags
from datamodel_code_generator._python_layout import flat
from datamodel_code_generator._runtime.model_codecs.media import charset, media_kind
from datamodel_code_generator._target_contract import (
    BuiltinType,
    ConstructorType,
    GenericType,
    ImportedExpression,
    ImportedType,
    LiteralScalar,
    LiteralSequence,
    NoneType,
    SourceExpression,
    UnionType,
)
from datamodel_code_generator._target_module import TargetModule
from datamodel_code_generator._target_render import field_plan, parameter_plan, runtime_sources
from datamodel_code_generator._target_templates import builtin_role
from datamodel_code_generator.model.base import escape_docstring, format_docstring
from datamodel_code_generator.model.pydantic_v2._annotated_types import annotated_constraint_metadata

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from datamodel_code_generator._fastapi.config import FastAPIConfig
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
    from datamodel_code_generator._openapi_codec_plan import PydanticBackend
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._target_contract import (
        GeneratedTypeContractBatch,
        TypeArgument,
        TypeView,
    )
    from datamodel_code_generator._target_module import TypeNames
    from datamodel_code_generator._target_templates import Role, TemplateOverlay

_MIN_CONTENT_STATUS: Final = 200
_METADATA: Final = frozenset({"Field", "StringConstraints"})
_CONTAINERS: Final[dict[tuple[str | None, str], str]] = {
    (None, "list"): "is_list",
    (None, "set"): "is_set",
    (None, "frozenset"): "is_frozen_set",
    (None, "dict"): "is_dict",
    ("collections.abc", "Sequence"): "is_sequence",
    ("typing", "Sequence"): "is_sequence",
    ("collections.abc", "Mapping"): "is_mapping",
    ("typing", "Mapping"): "is_mapping",
}
_KEYED: Final = frozenset({"is_dict", "is_mapping"})
_SETTINGS: Final = ("dependencies", "operation_dependencies", "prefix")
_EXPORTS: Final = (
    ("_generated.contract", "OperationDependencies"),
    ("_runtime.server.application", "validation_error_handler"),
    ("_generated.openapi", "serve_source_openapi"),
)
_PACKAGE_NAMES: Final = (
    "OperationDependencies",
    "build_router",
    "create_app",
    "serve_source_openapi",
    "validation_error_handler",
)
_ROUTERS_DOCSTRING: Final = "Router groups of this package."
_SECURITY_NAMES: Final = ("AsyncAuthorize", "Authorize", "Credentials", "RequirementSets")
_SECURITY_EXPORTS: Final = tuple(("_runtime.server.security", name) for name in _SECURITY_NAMES)
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
_SCHEME_FACTS: Final[dict[SchemeKind, tuple[str, ...]]] = {
    "bearer": ("bearerFormat", "description"),
    "oauth2": ("flows", "description"),
    "openid": ("openIdConnectUrl", "description"),
}


_SERVER_RUNTIME: Final = ("server/application.py", "server/openapi.py", "server/responses.py")
_SECURITY_RUNTIME: Final = ("server/security.py",)
_INPUT_RUNTIME: Final = (
    "model_codecs/errors.py",
    "model_codecs/media.py",
    "model_codecs/parameter_reads.py",
    "model_codecs/parameters.py",
    "model_codecs/unset.py",
    "server/errors.py",
    "server/requests.py",
)


def _constrained(value: TypeView) -> bool:
    """Return whether a type holds a constrained scalar, whose validating annotation the server spells itself.

    A tuple keeps the model's own annotation, which validates its constrained members as the model does.
    """
    match value:
        case ConstructorType():
            return value.base is not None
        case UnionType():
            return any(map(_constrained, value.members))
        case GenericType():
            return value.tuple_form != "fixed" and any(map(_constrained, value.arguments))
        case _:
            pass
    return False


def _plan_classes(plan: ServerPlan, written: set[str]) -> dict[str, str]:
    """Name each operation's plan class by its PascalCase name, unique among the module's classes.

    A name the model types write takes a `Model` suffix, then a number, as the model generator renames such a class.
    """
    taken = {spec.pascal for spec in plan.operations if spec.pascal not in written}
    classes: dict[str, str] = {}
    for spec in plan.operations:
        name = spec.pascal
        if name in written:
            base, count = f"{name}Model", 0
            name = base
            while name in taken:
                count += 1
                name = f"{base}{count}"
            taken.add(name)
        classes[spec.key] = name
    return classes


def _chain(module: Module, members: Iterable[str]) -> str:
    """Return a union of support types as the model generator writes it, each member once in the order first seen."""
    parts = tuple(dict.fromkeys(members))
    return " | ".join(parts) if module.union("T", "U") == "T | U" else module.union(*parts)


def _reads_inputs(spec: OperationSpec) -> bool:
    """Return whether an operation reads an input through an adapter."""
    return (spec.body is not None and spec.body.decision.transport == "adapter") or any(
        argument.kind == "adapter" for argument in spec.arguments
    )


def _python(value: object) -> str:
    """Return a Python literal for a finite configuration or schema value."""
    return repr(value)


def _unique(base: str, taken: Iterable[str]) -> str:
    names = set(taken)
    name = base
    count = 0
    while name in names or keyword.iskeyword(name):
        count += 1
        name = f"{base}_{count}"
    return name


def _route_names(spec: OperationSpec, group: GroupSpec) -> dict[str, str]:
    """Name what one route adder defines so that none equals the method, an argument, or another of its names."""
    taken = {spec.python_name, *(argument.name for argument in spec.arguments)}
    names: dict[str, str] = {}
    for key, base in (
        ("router", "router"),
        ("wiring", "wiring"),
        ("service", group.stem),
        ("handler", f"{spec.python_name}_handler"),
        ("principal", f"{spec.python_name}_principal"),
        ("authorize", f"{spec.python_name}_authorize"),
        ("record", "parameters"),
    ):
        taken.add(name := _unique(base, taken))
        names[key] = name
    return names


class Module(TargetModule):
    """One generated server module: model types as the models spell them, with FastAPI's validating annotations.

    A constrained scalar validates as `Annotated[base, Field(...)]`, with `StringConstraints` for a string, its
    constraints split as the model generator splits them for a type alias, so it reads the same in one annotation with
    a FastAPI declaration. A type the planner rebuilt, such as a parameter type with its aliases inlined, is composed
    from its parts as the model generator composes types.
    """

    def static(self, value: TypeView) -> str:
        """Return the type type checkers read: a constrained scalar as its base."""
        match value:
            case ConstructorType() if value.hint is None and value.base is not None:
                return self.hint(value.base)
            case UnionType() | GenericType() if value.hint is None:
                return self.composed(value, static=True)
            case _:
                pass
        return self.hint(value)

    def annotation(self, value: TypeView, *metadata: str) -> str:
        """Return the annotation FastAPI validates with, joined by extra metadata such as a parameter declaration."""
        base, own = self.parts(value)
        if not (items := (*own, *metadata)):
            return base
        return f"{self.name('typing', 'Annotated')}[{base}, {', '.join(items)}]"

    def parts(self, value: TypeView) -> tuple[str, tuple[str, ...]]:
        """Return a type's validating base and the Annotated metadata that constrains a constrained scalar."""
        if isinstance(value, ConstructorType) and value.base is not None:
            return self.hint(value.base), self.constraints(value, value.base)
        return self.validating(value), ()

    def validating(self, value: TypeView) -> str:
        match value:
            case ConstructorType() if value.base is not None:
                return self.annotation(value)
            case UnionType() | GenericType() if value.hint is None or _constrained(value):
                return self.composed(value, static=False)
            case _:
                pass
        return self.hint(value, static=False)

    def composed(self, value: UnionType | GenericType, *, static: bool) -> str:
        """Return a union or container of spelled parts, as the model generator writes one."""
        spell = self.static if static else self.validating
        if isinstance(value, UnionType):
            return self.union(*map(spell, value.members))
        arguments = [spell(item) for item in value.arguments]
        base = value.base
        identity = (base.import_.from_, base.import_.import_) if isinstance(base, ImportedType) else (None, "")
        flag = _CONTAINERS[(None, base.name) if isinstance(base, BuiltinType) else identity]
        if flag in _KEYED:
            return self.container(flag, arguments[-1], key=arguments[0])
        return self.container(flag, arguments[0])

    def constraints(self, value: ConstructorType, base: BuiltinType | ImportedType) -> tuple[str, ...]:
        """Return the metadata of a constrained scalar's keywords, split as the model generator splits them."""
        keywords = dict(value.keywords)
        name = base.name if isinstance(base, BuiltinType) else base.import_.import_
        raw = {key: item.value if isinstance(item, LiteralScalar) else item for key, item in keywords.items()}
        field, multiple_of = annotated_constraint_metadata(name, raw)
        callable_ = value.callable.import_
        metadata = (
            self.imported(callable_)
            if callable_.import_ in _METADATA
            else self.name("pydantic", "StringConstraints" if name == "str" else "Field")
        )
        found: list[str] = []
        if field:
            found.append(f"{metadata}({', '.join(f'{key}={self.argument(keywords[key])}' for key in field)})")
        if multiple_of is not None:
            found.append(f"{self.name('annotated_types', 'MultipleOf')}({self.argument(keywords['multiple_of'])})")
        return tuple(found)

    def argument(self, value: TypeArgument) -> str:
        """Return the Python expression of a recorded literal or source expression."""
        match value:
            case LiteralScalar(kind="decimal"):
                return f"{self.name('decimal', 'Decimal')}({str(value.value)!r})"
            case LiteralScalar():
                return repr(value.value)
            case SourceExpression():
                return value.text
            case _:
                pass
        assert isinstance(value, ImportedExpression), "constraints and defaults hold scalars and expressions"
        return f"{value.prefix}{self.imported(value.import_)}{value.suffix}"


@dataclass(frozen=True, slots=True)
class Parameter:
    """One parameter of a generated signature, with its default expression or none."""

    name: str
    annotation: str
    default: str = ""


@dataclass(frozen=True, slots=True)
class Keyword:
    """One keyword argument of a generated call."""

    keyword: str
    value: str


@dataclass(frozen=True, slots=True)
class Builder:
    """A keyword-only builder function: `build_router` or `create_app`."""

    name: str
    parameters: tuple[Parameter, ...]
    returns: str


@dataclass(frozen=True, slots=True)
class Lookup:
    """The call that looks up and checks one operation's service method."""

    function: str
    method: str
    label: str
    asynchronous: bool


@dataclass(frozen=True, slots=True)
class Principal:
    """The dependency that authorizes one operation from its schemes' credentials."""

    name: str
    authorize: str
    authenticate: str
    parameters: tuple[Parameter, ...]
    requirements: str
    credentials: str
    challenge: str


@dataclass(frozen=True, slots=True)
class Endpoint:
    """The endpoint function FastAPI calls for one operation."""

    name: str
    asynchronous: bool
    parameters: tuple[Parameter, ...]


@dataclass(frozen=True, slots=True)
class Call:
    """The endpoint's call of the service method, dispatched with the operation's responses plan."""

    dispatch: str
    arguments: tuple[Keyword, ...]
    responses: str


@dataclass(frozen=True, slots=True)
class Registration:
    """The route registration of one operation; without a response model, the response class answers."""

    name: str
    path: str
    methods: str
    status_code: str
    response_model: str
    response_class: str
    keywords: tuple[Keyword, ...]


@dataclass(frozen=True, slots=True)
class Route:
    """One operation's route adder: its local names, lookup, principal, endpoint, call, and registration."""

    adder: str
    router: str
    wiring: str
    service: str
    protocol: str
    group: str
    handler: str
    lookup: Lookup
    principal: Principal | None
    endpoint: Endpoint
    call: Call
    registration: Registration


@dataclass(frozen=True, slots=True)
class RouteEntry:
    """One entry of a router module's route tables: the operation's name and its adder."""

    name: str
    adder: str


@dataclass(frozen=True, slots=True)
class Method:
    """One abstract service method."""

    name: str
    asynchronous: bool
    parameters: tuple[Parameter, ...]
    returns: str
    docstring: str


@dataclass(frozen=True, slots=True)
class Service:
    """One router group's service Protocol."""

    name: str
    base: str
    docstring: str
    methods: tuple[Method, ...]


@dataclass(frozen=True, slots=True)
class BodyPlan:
    """The adapter body of one operation plan."""

    media: str
    optional: bool


@dataclass(frozen=True, slots=True)
class ResponsesPlan:
    """The responses plan of one operation: the declared responses, the primary status, and HEAD."""

    responses: str
    primary: str
    head: bool


@dataclass(frozen=True, slots=True)
class Plan:
    """One operation's plan class in the contract module."""

    class_name: str
    operation: str
    parameters: tuple[Parameter, ...]
    arguments: str
    path: str
    body: BodyPlan | None
    responses: ResponsesPlan


@dataclass(frozen=True, slots=True)
class Scheme:
    """One scheme's security dependency: a FastAPI security class instance, or a function without a class."""

    name: str
    scheme: str
    class_name: str
    keywords: tuple[Keyword, ...]


class ServerRenderer:  # ruff: ignore[too-many-public-methods]
    """Render every module of one server package from its plan, codec plan, and wire plan."""

    def __init__(  # ruff: ignore[too-many-arguments]
        self,
        *,
        config: FastAPIConfig,
        backend: PydanticBackend,
        plan: ServerPlan,
        batch: GeneratedTypeContractBatch,
        wire: WirePlan,
        types: TypeNames,
        templates: TemplateOverlay | None = None,
        document: str = "{}",
        use_schema_description: bool = False,
        use_single_line_docstring: bool = False,
    ) -> None:
        """Keep the plans.

        The overrides of a custom template directory replace builtin roles. `document` is the JSON text of the source
        document the application serves. The docstring settings are the models' own: a service method documents its
        operation's summary and description with `use_schema_description`, its route otherwise.
        """
        self.config = config
        self.backend = backend
        self.plan = plan
        self.role: Role = builtin_role if templates is None else templates.role
        self.document = document
        self.batch = batch
        self.wire = wire
        self.types = types
        self.use_schema_description = use_schema_description
        self.use_single_line_docstring = use_single_line_docstring
        self.classes = _plan_classes(plan, {name for module, name in types.fixed if module is not None})
        self.scheme_names = {scheme.name: scheme.python_name for scheme in plan.schemes}

    @staticmethod
    def file(path: PurePosixPath, kind: str, text: str, *, verbatim: bool = False) -> RenderedFile:
        """Return one generated file of the package."""
        return RenderedFile(path=path, kind=kind, text=text, verbatim=verbatim)

    def files(self) -> tuple[RenderedFile, ...]:
        """Return every rendered server file in the fixed artifact order."""
        generated = PurePosixPath("_generated")
        files = (
            self.file(PurePosixPath("__init__.py"), "package", self.package()),
            self.file(PurePosixPath("application.py"), "application", self.application()),
            *self.routers(),
            self.file(
                generated / "__init__.py", "package", self.facade("_generated", "Generated plans of this package.")
            ),
            self.file(generated / "contract.py", "contract", self.contract()),
            self.file(generated / "openapi.py", "openapi", self.openapi()),
            *((self.file(PurePosixPath("security.py"), "security", self.security()),) if self.plan.schemes else ()),
            self.file(PurePosixPath("services.py"), "services", self.services_module()),
            self.file(PurePosixPath("README.md"), "readme", self.readme()),
        )
        return (*files, *self.runtime())

    def facade(self, module: str, docstring: str, imports: str = "", exports: Iterable[str] = ()) -> str:
        """Return a package initializer, rendered from the facade role: its docstring, imports, and exports."""
        return self.role("facade.jinja2", facade_template.render)(
            module=module, docstring=docstring, imports=imports, exports=list(exports)
        )

    def package(self) -> str:
        """Return the package initializer, which exports the security aliases only when a scheme is used."""
        module = Module(self.types, level=1, hints=False)
        security = _SECURITY_NAMES if self.plan.schemes else ()
        names = sorted((*_PACKAGE_NAMES, *security))
        exported = sorted((
            *(module.local("application", name) for name in names),
            module.local("_runtime.server.responses", "HTTPResult"),
        ))
        return self.facade("", "FastAPI server generated by datamodel-code-generator.", module.imports(), exported)

    def openapi(self) -> str:
        """Return the module of the served document: its JSON text, one string literal per line, read on first use."""
        lines = [repr(line) for line in self.document.splitlines(keepends=True)]
        return self.role("openapi.jinja2", openapi_template.render)(lines=lines)

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
        )

    def runtime(self) -> Iterator[RenderedFile]:
        """Copy the server runtime, with security for schemes and the request adapters when an operation uses them."""
        security = _SECURITY_RUNTIME if self.plan.schemes else ()
        inputs = _INPUT_RUNTIME if any(map(_reads_inputs, self.plan.operations)) else ()
        for path, text in runtime_sources((*_SERVER_RUNTIME, *security, *inputs)):
            yield self.file(path, "runtime", text, verbatim=True)

    def application(self) -> str:
        """Return the application module, rendered from its builtin template.

        The router modules come first, so the names they take keep every other import away from the builders'
        service arguments, which share their names.
        """
        single = self.config.layout == "single"
        groups = self.plan.groups
        services = [group.stem for group in groups]
        module = Module(self.types, {*BUILDER_NAMES, *(services if single else ())}, level=1)
        modules = [module.local("", "routes")] if single else [module.local("routers", stem) for stem in services]
        routes = [*(f"*{name}.LITERAL_ROUTES" for name in modules), *(f"*{name}.TEMPLATED_ROUTES" for name in modules)]
        secured = bool(self.plan.schemes)
        final = module.name("typing", "Final")
        options = f"dict[str, {module.name('typing', 'Any')}]"
        info = "{" + ", ".join(f"{key!r}: {_python(value)}" for key, value in self.plan.info) + "}"
        router = module.name("fastapi", "APIRouter")
        fastapi = module.name("fastapi", "FastAPI")
        exports = sorted({
            *(repr(module.local(*item)) for item in (*_EXPORTS, *(_SECURITY_EXPORTS if secured else ()))),
            "'build_router'",
            "'create_app'",
        })
        any_ = module.name("typing", "Any")
        return self.role("application.jinja2", application_template.render)(
            final=final,
            options=options,
            routes=routes,
            info=info,
            build_router=_builder(module, "build_router", router, groups, secured=secured),
            build=module.local("_runtime.server.application", "build"),
            services=_services(services),
            settings=_settings(secured=secured),
            create_app=_builder(
                module,
                "create_app",
                fastapi,
                groups,
                Parameter("source_openapi", "bool", "True"),
                Parameter("**fastapi_kwargs", any_),
                secured=secured,
                dependencies=False,
            ),
            arguments=(*services, *_settings(secured=secured, dependencies=False)),
            fastapi=fastapi,
            error_handlers=module.local("_runtime.server.application", "error_handlers"),
            serve_source_openapi=module.local("_generated.openapi", "serve_source_openapi"),
            exports=exports,
            imports=module.imports(),
        )

    def routers(self) -> Iterator[RenderedFile]:
        """Return the router modules: one routes module, or a package with one module per group."""
        if self.config.layout == "single":
            group = self.plan.groups[0] if self.plan.groups else None
            yield self.file(PurePosixPath("routes.py"), "router", self.router(group, level=1))
            return
        yield self.file(PurePosixPath("routers/__init__.py"), "package", self.facade("routers", _ROUTERS_DOCSTRING))
        for group in self.plan.groups:
            yield self.file(PurePosixPath("routers", f"{group.stem}.py"), "router", self.router(group, level=2))

    def router(self, group: GroupSpec | None, *, level: int) -> str:
        """Return one router module, rendered from its builtin template."""
        operations = () if group is None else group.operations
        groups = () if group is None else (group,)
        services = [group.stem for group in groups]
        secured = any(group.secured for group in groups)
        reserved = {*BUILDER_NAMES, *services}
        names = [] if group is None else [_route_names(spec, group) for spec in operations]
        for spec, locals_ in zip(operations, names, strict=True):
            reserved.update((
                spec.python_name,
                f"_add_{spec.python_name}",
                *(argument.name for argument in spec.arguments),
                *locals_.values(),
            ))
        module = Module(self.types, reserved, level=level)
        routes = (
            []
            if group is None
            else [self.route(module, spec, group, locals_) for spec, locals_ in zip(operations, names, strict=True)]
        )
        entries = {
            templated: [
                RouteEntry(repr(spec.python_name), f"_add_{spec.python_name}")
                for spec in operations
                if spec.route.templated is templated
            ]
            for templated in (False, True)
        }
        name = "every operation" if group is None or self.config.layout == "single" else f"the {group.stem} operations"
        router = module.name("fastapi", "APIRouter")
        final = module.name("typing", "Final")
        return self.role("router.jinja2", router_template.render)(
            docstring=f"Endpoints of {name}; regenerate them instead of editing.",
            routes=routes,
            api_router=router,
            wiring=module.local("_runtime.server.application", "Wiring"),
            final=final,
            literal=entries[False],
            templated=entries[True],
            builder=_builder(module, "build_router", router, groups, secured=secured),
            group=name,
            build=module.local("_runtime.server.application", "build"),
            services=_services(services),
            settings=_settings(secured=secured),
            imports=module.imports(),
        )

    def route(self, module: Module, spec: OperationSpec, group: GroupSpec, names: dict[str, str]) -> Route:
        """Return the records of one operation's route adder: method lookup, principal, endpoint, and registration."""
        plan = f"{module.local('_generated', 'contract')}.{self.classes[spec.key]}"
        service = module.local("services", group.service)
        handler, record = names["handler"], names["record"]
        principal = "" if spec.security is None else names["principal"]
        asynchronous = spec.mode == "async"
        label = f"The {group.stem}.{spec.python_name} method of {spec.contract.method.upper()} {spec.contract.path}"
        lookup = Lookup(
            module.local("_runtime.server.application", "checked"), spec.python_name, repr(label), asynchronous
        )
        parameters = [
            self.parameter(module, spec, argument, plan, principal)
            for argument in spec.arguments
            if argument.kind not in {"media_type", "adapter"}
        ]
        if any(argument.kind == "adapter" for argument in spec.arguments):
            annotated, depends = module.name("typing", "Annotated"), module.name("fastapi", "Depends")
            parameters.append(Parameter(record, f"{annotated}[{plan}.Parameters, {depends}({plan}.PARAMETERS)]"))
        call = Call(
            module.local("_runtime.server.responses", "dispatch"),
            tuple(Keyword(argument.name, _value(spec, argument, record)) for argument in spec.arguments),
            f"{plan}.RESPONSES",
        )
        return Route(
            adder=f"_add_{spec.python_name}",
            router=names["router"],
            wiring=names["wiring"],
            service=names["service"],
            protocol=f"{service}[object]" if group.secured else service,
            group=repr(group.stem),
            handler=handler,
            lookup=lookup,
            principal=None if not principal else self.principal(module, spec, names),
            endpoint=Endpoint(spec.python_name, asynchronous, tuple(parameters)),
            call=call,
            registration=_registration(module, spec),
        )

    def principal(self, module: Module, spec: OperationSpec, locals_: dict[str, str]) -> Principal:
        """Return the dependency that authorizes an operation from the credentials of its schemes' dependencies.

        The authorize callback is taken from the wiring first, so a router without one fails as it registers.
        """
        assert spec.security is not None
        requirements = spec.security.requirements
        scopes: dict[str, dict[str, None]] = {}
        for requirement in requirements:
            for scheme, needed in requirement:
                scopes.setdefault(scheme, {}).update(dict.fromkeys(needed))
        schemes = {scheme.name: scheme for scheme in self.plan.schemes}
        security = module.local("", "security")
        annotated, depends = module.name("typing", "Annotated"), module.name("fastapi", "Security")
        authenticate = module.local("_runtime.server.security", "authenticate")
        authorize = locals_["authorize"]
        taken = {authorize, authenticate}
        names: dict[str, str] = {}
        parameters: list[Parameter] = []
        for scheme, needed in scopes.items():
            taken.add(local := names.setdefault(scheme, _unique(self.scheme_names[scheme], taken)))
            arguments = f"{security}.{self.scheme_names[scheme]}" + (f", scopes={list(needed)!r}" if needed else "")
            kind = _credential_type(module, schemes[scheme])
            parameters.append(Parameter(local, f"{annotated}[{kind}, {depends}({arguments})]"))
        challenge = ", ".join(
            dict.fromkeys(_CHALLENGES[kind] for scheme in scopes if (kind := schemes[scheme].kind) in _CHALLENGES)
        )
        return Principal(
            name=locals_["principal"],
            authorize=authorize,
            authenticate=authenticate,
            parameters=tuple(parameters),
            requirements=_tuple([_requirement(item) for item in requirements]),
            credentials="{" + ", ".join(f"{scheme!r}: {names[scheme]}" for scheme in scopes) + "}",
            challenge=repr(challenge),
        )

    def security(self) -> str:
        """Return the security module: one overridable FastAPI dependency for each scheme the operations use."""
        module = Module(self.types, set(self.scheme_names.values()), level=1)
        schemes = [_scheme(module, scheme, self.scheme_names[scheme.name]) for scheme in self.plan.schemes]
        return self.role("security.jinja2", security_template.render)(
            schemes=schemes,
            exports=[repr(name) for name in sorted(self.scheme_names.values())],
            imports=module.imports(),
        )

    def parameter(
        self, module: Module, spec: OperationSpec, argument: Argument, plan: str, principal: str
    ) -> Parameter:
        """Return the endpoint parameter of one handler argument that FastAPI or an adapter supplies."""
        if argument.kind == "request":
            return Parameter("request", module.name("fastapi", "Request"))
        if argument.kind == "principal":
            annotated, depends = module.name("typing", "Annotated"), module.name("fastapi", "Depends")
            return Parameter("principal", f"{annotated}[object, {depends}({principal})]")
        if argument.native is not None:
            return _native(module, argument.name, argument.native)
        body = spec.body
        assert body is not None
        annotated = module.name("typing", "Annotated")
        if body.decision.transport == "fastapi_native":
            media = body.media[0]
            api = module.name("fastapi", "Form" if body.form else "Body")
            annotation = (
                f"{annotated}[{_body(module, media, required=body.required)}, {api}(media_type={media.media_type!r})]"
            )
            return Parameter(argument.name, annotation, "" if body.required else "None")
        depends = module.name("fastapi", "Depends")
        kind = self.body_type(module, body)
        if len(body.media) > 1:
            media_type = module.optional("str")
            return Parameter(argument.name, f"{annotated}[tuple[{media_type}, {kind}], {depends}({plan}.BODY.receive)]")
        return Parameter(argument.name, f"{annotated}[{kind}, {depends}({plan}.BODY)]")

    def body_type(self, module: Module, body: BodySpec) -> str:
        """Return the type the handler receives for a body, with None for an optional one."""
        kinds = module.union(*(self.media_type(module, media) for media in body.media))
        if body.required or any(_nullable(_payload(media)) for media in body.media):
            return kinds
        return module.optional(kinds)

    @staticmethod
    def media_type(module: Module, media: MediaSpec) -> str:
        """Return one media's payload type: the model's type, or the media surface without a schema."""
        if (value := _payload(media)) is None:
            return module.name("typing", "Any") if media.kind == "json" else "str" if media.kind == "text" else "bytes"
        return module.static(value)

    def services_module(self) -> str:
        """Return the services module: one Protocol per router group with an abstract method per operation."""
        groups = self.plan.groups
        reserved = {"PrincipalT_contra", *(group.service for group in groups)}
        for spec in self.plan.operations:
            reserved.update(argument.name for argument in spec.arguments)
        module = Module(self.types, reserved, level=1)
        protocol = module.name("typing", "Protocol")
        single = self.config.layout == "single"
        protocols = [
            Service(
                name=group.service,
                base=f"{protocol}[PrincipalT_contra]" if group.secured else protocol,
                docstring=f"Implement {'every operation' if single else f'the {group.stem} operations'}: "
                "subclass this Protocol, or give an object its methods.",
                methods=tuple(self.method(module, spec) for spec in group.operations),
            )
            for group in groups
        ]
        return self.role("services.jinja2", services_template.render)(
            typevar=module.name("typing_extensions", "TypeVar") if any(group.secured for group in groups) else "",
            abstract=module.name("abc", "abstractmethod"),
            protocol_type=protocol,
            protocols=protocols,
            imports=module.imports(),
        )

    def method(self, module: Module, spec: OperationSpec) -> Method:
        """Return one operation's method: the keyword-only arguments the endpoint passes and the results it takes."""
        return Method(
            name=spec.python_name,
            asynchronous=spec.mode == "async",
            parameters=tuple(self.parameters(module, spec, "PrincipalT_contra")),
            returns=_chain(module, self.results(module, spec)),
            docstring=format_docstring(
                self.docstring(spec), 8, use_single_line_docstring=self.use_single_line_docstring
            ),
        )

    def docstring(self, spec: OperationSpec) -> str:
        """Return an operation's docstring text: its summary and description, as the models document, or its route."""
        if self.use_schema_description:
            facts = {name: getattr(value, "value", None) for name, value in spec.contract.facts}
            texts = [text for name in ("summary", "description") if isinstance(text := facts.get(name), str)]
            if text := "\n\n".join(text.strip() for text in texts if text.strip()):
                return text
        return f"Handle {spec.contract.method.upper()} {spec.contract.path}."

    def parameters(self, module: Module, spec: OperationSpec, principal: str) -> list[Parameter]:
        """Return the keyword-only parameters of one operation's method, typed as the endpoint passes them."""
        return [
            Parameter(argument.name, self.surface(module, spec, argument, principal)) for argument in spec.arguments
        ]

    def surface(self, module: Module, spec: OperationSpec, argument: Argument, principal: str) -> str:
        """Return the type a method receives for one argument."""
        match argument.kind:
            case "request":
                return module.name("fastapi", "Request")
            case "principal":
                anonymous = spec.security is not None and spec.security.anonymous
                return module.optional(principal) if anonymous else principal
            case "native" | "adapter" if argument.parameter is not None:
                return _parameter_type(module, argument.parameter)
            case "media_type":
                return module.optional("str")
            case _:
                pass
        body = spec.body
        assert body is not None
        return self.body_type(module, body)

    def results(self, module: Module, spec: OperationSpec) -> list[str]:
        """Return the members of a method's result type, in the order the result type spells them."""
        payloads = dict.fromkeys(
            self.media_type(module, media)
            for response in spec.responses
            if _body_status(spec, response.status) and (media := default_media(response)) is not None
        )
        result = f"{module.local('_runtime.server.responses', 'HTTPResult')}[{module.union(*payloads) or 'None'}]"
        return [*self.bare(module, spec), result, module.name("fastapi.responses", "Response")]

    def bare(self, module: Module, spec: OperationSpec) -> list[str]:
        """Return the members of the type a method returns bare: the primary payload, None, or nothing."""
        if (primary := spec.primary) is None:
            return []
        if (media := primary.media) is None or not _body_status(spec, str(primary.status)):
            return ["None"]
        return [self.media_type(module, media)]

    def contract(self) -> str:
        """Return the contract module: the dependency keys, and each operation's plans and request adapters."""
        reserved = {"OperationDependencies", *self.classes.values()}
        module = Module(self.types, reserved, level=2)
        typed_dict = module.name("typing", "TypedDict")
        dependencies = [
            Parameter(repr(spec.python_name), _dependency_sequence(module)) for spec in self.plan.operations
        ]
        plans = [self.operation_plan(module, spec, self.classes[spec.key]) for spec in self.plan.operations]
        adapters = any(plan.parameters for plan in plans)
        bodies = any(plan.body is not None for plan in plans)
        requests = "_runtime.server.requests"
        return self.role("contract.jinja2", contract_template.render)(
            typed_dict=typed_dict,
            dependencies=dependencies,
            plans=plans,
            final=module.name("typing", "Final") if plans else "",
            dataclass=module.name("dataclasses", "dataclass") if adapters else "",
            parameter_adapter=module.local(requests, "ParameterAdapter") if adapters else "",
            body_adapter=module.local(requests, "BodyAdapter") if bodies else "",
            operation_responses=module.local("_runtime.server.responses", "OperationResponses") if plans else "",
            imports=module.imports(),
        )

    @staticmethod
    def operation_plan(module: Module, spec: OperationSpec, name: str) -> Plan:
        """Return one operation's plan class: adapter parameter record, request adapters, and responses."""
        adapters = [argument for argument in spec.arguments if argument.kind == "adapter"]
        parameters = tuple(
            Parameter(argument.name, _parameter_type(module, argument.parameter)) for argument in adapters
        )
        body = None
        if spec.body is not None and spec.body.decision.transport == "adapter":
            body = BodyPlan(_body_media(module, spec.body), optional=not spec.body.required)
        return Plan(
            class_name=name,
            operation=spec.python_name,
            parameters=parameters,
            arguments=_parameter_arguments(module, adapters) if adapters else "",
            path=_raw_path(module, spec, adapters) if adapters else "",
            body=body,
            responses=_responses_plan(module, spec),
        )


def _responses_plan(module: Module, spec: OperationSpec) -> ResponsesPlan:
    """Return the OperationResponses keywords of one operation: each declared response's default media and model."""
    responses: list[str] = []
    for response in spec.responses:
        entries: list[str] = []
        if _body_status(spec, response.status) and (media := default_media(response)) is not None:
            entries.append(f"media_type={media.media_type!r}")
            if media.kind != "json":
                entries.append(f"kind={media.kind!r}")
            if (encoding := charset(media.media_type)) != "utf-8":
                entries.append(f"charset={encoding!r}")
            if media.kind != "binary" and media.use is not None and media.use.type is not None:
                entries.append(f"model={module.annotation(media.use.type)}")
        declared = module.local("_runtime.server.responses", "Declared")
        responses.append(f"{response.status!r}: {declared}({', '.join(entries)})")
    primary = spec.primary
    return ResponsesPlan(
        responses="{" + ", ".join(responses) + "}",
        primary=str(primary.status) if primary is not None and not spec.native_primary else "",
        head=spec.head,
    )


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


def _tuple(items: list[str]) -> str:
    """Return a tuple display of expressions, with the comma a lone item needs."""
    return f"({', '.join(items)}{',' if len(items) == 1 else ''})"


def _native(module: Module, name: str, field: NativeField) -> Parameter:
    keywords = [f"alias={field.alias!r}"]
    if field.api == "Header":
        keywords.append("convert_underscores=False")
    keywords.extend(f"{key}={_python(value)}" for key, value in field.keywords)
    if field.default is Default.ABSENT:
        parts: tuple[str, ...] = (_none(module, module.annotation(field.type), field.type),)
        default = "None"
    else:
        base, metadata = module.parts(field.type)
        parts = (base, *metadata)
        default = _default_value(module, field.default) or ""
    declaration = f"{module.name('fastapi', field.api)}({', '.join(keywords)})"
    return Parameter(name, f"{module.name('typing', 'Annotated')}[{', '.join((*parts, declaration))}]", default)


def _payload(media: MediaSpec) -> TypeView | None:
    """Return the model type of a media's payload, or None for binary media and media without a schema."""
    return None if media.kind == "binary" or media.use is None else media.use.type


def _nullable(value: TypeView | None) -> bool:
    """Return whether a type already takes None, so an optional input of it needs no None member."""
    return isinstance(value, UnionType) and any(isinstance(member, NoneType) for member in value.members)


def _none(module: Module, text: str, value: TypeView | None) -> str:
    """Return a runtime annotation that also takes None, as FastAPI declares an optional input."""
    return text if _nullable(value) else module.optional(text)


def _body(module: Module, media: MediaSpec, *, required: bool) -> str:
    value = None if media.use is None else media.use.type
    text = module.name("typing", "Any") if value is None else module.annotation(value)
    return text if required else _none(module, text, value)


def _adapter(module: Module, value: TypeView) -> str:
    return f"{module.name('pydantic', 'TypeAdapter')}({module.annotation(value)})"


def _default(module: Module, value: LiteralScalar | LiteralSequence) -> str:
    if isinstance(value, LiteralScalar):
        return module.argument(value)
    return f"[{', '.join(module.argument(item) for item in value.items if isinstance(item, LiteralScalar))}]"


def _default_value(module: Module, default: ParameterDefault) -> str | None:
    """Return the expression of a parameter's default, or None without one.

    A root model takes the schema's default unvalidated, as pydantic leaves defaults, or its own; an enum type takes
    the members the default names.
    """
    match default:
        case LiteralScalar() | LiteralSequence():
            return _default(module, default)
        case RootDefault():
            model = module.annotation(default.type)
            literal = default.literal
            return f"{model}()" if literal is None else f"{model}.model_construct({_default(module, literal)})"
        case MemberDefault():
            enum, literal = module.annotation(default.type), default.literal
            if isinstance(literal, LiteralScalar):
                return f"{enum}({module.argument(literal)})"
            items = (item for item in literal.items if isinstance(item, LiteralScalar))
            return f"[{', '.join(f'{enum}({module.argument(item)})' for item in items)}]"
        case _:
            pass
    return None


def _parameter_type(module: Module, parameter: ParameterSpec | None) -> str:
    """Return the type a method receives for a parameter: the model's type, or Any without one, with None if absent."""
    assert parameter is not None
    if parameter.type is not None:
        text = module.static(parameter.type)
    elif parameter.plan is not None and media_kind(parameter.plan.content_media_type or "text/plain") == "text":
        text = "str"
    else:
        text = module.name("typing", "Any")
    if parameter.required or parameter.default is not Default.ABSENT or _nullable(parameter.type):
        return text
    return module.optional(text)


def _parameter_arguments(module: Module, adapters: list[Argument]) -> str:
    """Return the ParameterArgument constructors of an operation's adapter parameters, as one tuple."""
    arguments: list[str] = []
    for argument in adapters:
        parameter = argument.parameter
        assert parameter is not None
        assert parameter.plan is not None
        entries = [f"name={argument.name!r}", f"plan={flat(parameter_plan(module.local, parameter.plan))}"]
        if parameter.type is not None:
            entries.append(f"adapter={_adapter(module, parameter.type)}")
        if (value := _default_value(module, parameter.default)) is not None:
            entries.append(f"default={value}")
        arguments.append(f"{module.local('_runtime.server.requests', 'ParameterArgument')}({', '.join(entries)})")
    return _tuple(arguments)


def _body_media(module: Module, body: BodySpec) -> str:
    """Return the BodyMedia constructors of an adapter body, as one tuple."""
    media: list[str] = []
    for item in body.media:
        entries = [f"media_type={item.media_type!r}", f"kind={_request_kind(item)!r}"]
        if item.kind != "binary" and item.use is not None and item.use.type is not None:
            entries.append(f"adapter={_adapter(module, item.use.type)}")
        if item.kind in {"form", "multipart"} and (fields := body.form_fields):
            entries.append(f"fields={_tuple([field_plan(module.local, plan) for plan in fields])}")
        media.append(f"{module.local('_runtime.server.requests', 'BodyMedia')}({', '.join(entries)})")
    return _tuple(media)


def _registration(module: Module, spec: OperationSpec) -> Registration:
    contract = spec.contract
    facts = {name: getattr(value, "value", None) for name, value in contract.facts}
    primary = spec.primary
    model = response_class = ""
    if spec.native_primary and primary is not None and primary.media is not None and primary.media.use is not None:
        assert primary.media.use.type is not None
        model = module.annotation(primary.media.use.type)
    else:
        response_class = module.name("fastapi.responses", "Response")
    keywords: list[Keyword] = []
    if contract.explicit_operation_id and isinstance(operation_id := facts.get("operationId"), str):
        keywords.append(Keyword("operation_id", repr(operation_id)))
    if found := tags(contract):
        keywords.append(Keyword("tags", f"[{', '.join(repr(tag) for tag in found)}]"))
    keywords.extend(
        Keyword(name, repr(value)) for name in ("summary", "description") if isinstance(value := facts.get(name), str)
    )
    if facts.get("deprecated") is True:
        keywords.append(Keyword("deprecated", "True"))
    if isinstance(description := _response_description(spec), str):
        keywords.append(Keyword("response_description", repr(description)))
    return Registration(
        name=repr(spec.python_name),
        path=repr(spec.route.route_path),
        methods=f"[{contract.method.upper()!r}]",
        status_code=str(spec.registration_status),
        response_model=model,
        response_class=response_class,
        keywords=tuple(keywords),
    )


def _response_description(spec: OperationSpec) -> object:
    if (primary := spec.primary) is not None:
        return fact(primary.response.declaration, "description")
    responses = {response.status: response for response in spec.responses}
    status = str(spec.registration_status)
    represented = responses.get("default", responses.get(f"{status[0]}XX"))
    return None if represented is None else fact(represented.declaration, "description")


def _value(spec: OperationSpec, argument: Argument, record: str) -> str:
    if argument.kind == "adapter":
        return f"{record}.{argument.name}"
    if argument.kind == "media_type":
        return "body[0]"
    if argument.kind == "body" and spec.body is not None and len(spec.body.media) > 1:
        return "body[1]"
    return argument.name


def _raw_path(module: Module, spec: OperationSpec, adapters: list[Argument]) -> str:
    if not (wanted := {item.wire_name for item in adapters if item.location == "path" and item.wire_name is not None}):
        return ""
    names = f"frozenset({{{', '.join(repr(name) for name in sorted(wanted))}}})"
    return f"{module.local('_runtime.server.requests', 'RawPath')}(template={spec.route.path!r}, names={names})"


def _settings(*, secured: bool, dependencies: bool = True) -> tuple[str, ...]:
    settings = _SETTINGS if dependencies else _SETTINGS[1:]
    return ("authorize", *settings) if secured else settings


def _services(services: list[str]) -> str:
    return "{" + ", ".join(f"{service!r}: {service}" for service in services) + "}"


def _builder(  # ruff: ignore[too-many-arguments]
    module: Module,
    name: str,
    returns: str,
    groups: Iterable[GroupSpec],
    *extra: Parameter,
    secured: bool,
    dependencies: bool = True,
) -> Builder:
    contract = module.local("_generated.contract", "OperationDependencies")
    parameters = (
        *(Parameter(group.stem, _service(module, group)) for group in groups),
        *((_security(module),) if secured else ()),
        *((Parameter("dependencies", _dependency_sequence(module), "()"),) if dependencies else ()),
        Parameter("operation_dependencies", module.optional(contract), "None"),
        Parameter("prefix", "str", '""'),
        *extra,
    )
    return Builder(name, parameters, returns)


def _service(module: Module, group: GroupSpec) -> str:
    service = module.local("services", group.service)
    return f"{service}[{module.local('_runtime.server.security', 'PrincipalT')}]" if group.secured else service


def _security(module: Module) -> Parameter:
    principal = module.local("_runtime.server.security", "PrincipalT")
    authorizers = (
        module.local("_runtime.server.security", "Authorize"),
        module.local("_runtime.server.security", "AsyncAuthorize"),
    )
    return Parameter("authorize", _chain(module, [f"{item}[{principal}]" for item in authorizers]))


def _dependency_sequence(module: Module) -> str:
    return f"{module.name('collections.abc', 'Sequence')}[{module.name('fastapi', 'params')}.Depends]"


def _credential_type(module: Module, scheme: SchemeSpec) -> str:
    """Return the type of the credential a scheme's FastAPI dependency returns."""
    match scheme.kind:
        case "basic":
            return module.optional(module.name("fastapi.security", "HTTPBasicCredentials"))
        case "bearer" | "digest":
            return module.optional(module.name("fastapi.security", "HTTPAuthorizationCredentials"))
        case "custom":
            return "object"
        case _:
            pass
    return module.optional("str")


def _scheme(module: Module, scheme: SchemeSpec, name: str) -> Scheme:
    """Return the module-level dependency of one scheme: a FastAPI security class, or a function to override."""
    if scheme.kind == "custom":
        return Scheme(name, escape_docstring(scheme.name) or "", "", ())
    facts = dict(scheme.declaration.facts)
    keywords: list[Keyword] = []
    if scheme.kind == "api_key":
        keywords.append(Keyword("name", repr(scheme.parameter)))
        cls = _API_KEY_CLASSES[str(scheme.location)]
    else:
        cls = _SCHEME_CLASSES[scheme.kind]
    keywords.extend(
        Keyword(keyword, repr(value))
        for keyword in _SCHEME_FACTS.get(scheme.kind, ("description",))
        if (fact_value := facts.get(keyword)) is not None and (value := json_value(fact_value)) is not None
    )
    keywords.extend((Keyword("scheme_name", repr(scheme.name)), Keyword("auto_error", "False")))
    return Scheme(name, scheme.name, module.name("fastapi.security", cls), tuple(keywords))


def _requirement(requirement: Requirement) -> str:
    return _tuple([f"({name!r}, {scopes!r})" for name, scopes in requirement])


def _request_kind(media: MediaSpec) -> str:
    return media.kind if media.kind in {"json", "text", "form", "multipart"} else "binary"
