"""Render the Python modules of a client target from its plan."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from functools import cached_property
from itertools import starmap
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias

from datamodel_code_generator._api_generation import RenderedFile
from datamodel_code_generator._client._compiled_templates import arguments as arguments_template
from datamodel_code_generator._client._compiled_templates import client as client_template
from datamodel_code_generator._client._compiled_templates import operations as operations_template
from datamodel_code_generator._client._compiled_templates import readme as readme_template
from datamodel_code_generator._client._compiled_templates import resource as resource_template
from datamodel_code_generator._client._compiled_templates import runtime as runtime_template
from datamodel_code_generator._client._compiled_templates import security as security_template
from datamodel_code_generator._client._compiled_templates import types as types_template
from datamodel_code_generator._client.caching import CacheSpec
from datamodel_code_generator._client.codec_render import render_model_bindings
from datamodel_code_generator._client.facade import Attribute, Lazy, Subclass, name_set, render_facade, statement
from datamodel_code_generator._client.naming import CLIENT_KEYWORDS, VIEW_KEYWORDS, helper_classes
from datamodel_code_generator._client.plan import media_range, member_parts, reachable, success_media
from datamodel_code_generator._client.polling import STATES, PollingSpec
from datamodel_code_generator._client.runtime import (
    Capabilities,
    declared_backends,
    declared_helpers,
    declared_security,
)
from datamodel_code_generator._client.uploads import UploadSpec
from datamodel_code_generator._python_layout import Doc, Group, flat, layout
from datamodel_code_generator._runtime.client.security import SecurityScheme
from datamodel_code_generator._runtime.model_codecs.media import media_kind
from datamodel_code_generator._target_contract import UnionType
from datamodel_code_generator._target_module import TargetModule
from datamodel_code_generator._target_render import (
    Keyword,
    Parameter,
    Plan,
    field_plan,
    items,
    parameter_plan,
    runtime_sources,
)
from datamodel_code_generator._target_templates import builtin_role
from datamodel_code_generator.model.base import format_docstring
from datamodel_code_generator.reference import snake_to_upper_camel

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator._client.codec_plan import ClientCodecs, CodecBackend
    from datamodel_code_generator._client.codec_render import RenderedBindings, UseAccessors
    from datamodel_code_generator._client.config import ClientGenerationConfig
    from datamodel_code_generator._client.model_facts import ItemStep
    from datamodel_code_generator._client.pagination import PaginationSpec
    from datamodel_code_generator._client.plan import (
        ClientPlan,
        FieldBranch,
        HeaderSpec,
        MediaSpec,
        OperationSpec,
        ParameterSpec,
        PartSpec,
        ResourceSpec,
        ResponseSpec,
        ServerSpec,
    )
    from datamodel_code_generator._client.runtime import Helper, RawBody
    from datamodel_code_generator._client.security import CredentialSpec
    from datamodel_code_generator._client.sockets import SocketSpec
    from datamodel_code_generator._client.streams import StreamSpec
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.client.multipart import PartPlan
    from datamodel_code_generator._target_contract import (
        GeneratedTypeContractBatch,
        ModelHint,
        TypeUseBinding,
        TypeUseId,
        TypeView,
    )
    from datamodel_code_generator._target_module import TypeNames
    from datamodel_code_generator._target_templates import Role, TemplateOverlay

WIDTH: Final = 88
_RUNTIME: Final = "_runtime.client.operations"
_CODECS: Final = "_runtime.client.codecs"
_OPTION_NAMES: Final = ("Clock", "RequestOptions", "RetryOptions", "ServerSelection")
_CLIENTS: Final = (("._async_client", ("AsyncClient", "AsyncClientView")), ("._client", ("Client", "ClientView")))
_KIND_LABELS: Final = {"api_key": "API key", "basic": "HTTP Basic", "bearer": "bearer token"}
_CREDENTIAL_EXAMPLES: Final = {
    "api_key": ("key", "key: str"),
    "basic": ("(username, password)", "username: str, password: str"),
    "bearer": ("token", "token: str"),
}
_FLOW_CLASSES: Final = {
    "client_credentials": ("ClientCredentials", "client credentials"),
    "refresh_token": ("RefreshToken", "refresh token"),
}
_ERROR_NAMES: Final = (
    "APIConnectionError",
    "APIStatusError",
    "APITimeoutError",
    "AuthenticationError",
    "BadRequestError",
    "ConfigurationError",
    "ConflictError",
    "DecodeError",
    "InternalServerError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
    "SDKError",
    "UnprocessableEntityError",
)
_AUTH_ERROR_NAMES: Final = ("AuthError", "AuthReason")
_PROTOCOL_ERROR_NAMES: Final = (
    "ProtocolDataError",
    "SessionLimitError",
    "StreamInterruptedError",
    "WebSocketClosedError",
)
_PROTOCOL_ERRORS: Final[dict[Helper, tuple[str, ...]]] = {
    "pagination": ("ProtocolDataError", "SessionLimitError"),
    "polling": ("ProtocolDataError", "SessionLimitError"),
    "streams": ("ProtocolDataError", "SessionLimitError", "StreamInterruptedError"),
    "uploads": ("ProtocolDataError",),
    "cache": ("ProtocolDataError",),
    "websocket": ("ProtocolDataError", "WebSocketClosedError"),
    "webhooks": ("ProtocolDataError",),
}
_PROTOCOL_EXPORTS: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    "protocols": {
        "origins": ("Origin",),
        "records": (
            "BodySelector",
            "BodyTarget",
            "HeaderSelector",
            "ParameterTarget",
            "ProgressKey",
            "ProtocolProgress",
            "QuerystringTarget",
            "RequestTarget",
            "Selector",
            "StatusSelector",
        ),
        "references": ("OperationRef",),
    },
    "pagination": {"options": ("PaginationOptions",), "pagination": ("AsyncPager", "Page", "Pager")},
    "polling": {
        "options": ("PollOptions",),
        "records": ("CancelReceipt", "PollSnapshot"),
        "polling": ("AsyncLroHandle", "LroHandle"),
    },
    "streams": {
        "options": ("StreamOptions",),
        "streams": ("AsyncEventStream", "EventStream", "StreamEvent", "UnknownEvent"),
    },
    "uploads": {
        "options": ("UploadOptions",),
        "sources": ("UploadProgress", "UploadSource"),
        "uploads": ("AsyncUploadHandle", "UploadHandle"),
    },
    "cache": {
        "options": ("CacheOptions",),
        "caches": ("AsyncCacheStore", "CacheEntry", "CacheResult", "CacheStore"),
        "cache_stores": ("AsyncMemoryCacheStore", "MemoryCacheStore"),
    },
    "websocket": {
        "options": ("WSOptions",),
        "websocket": ("AsyncWebSocketSession", "Message", "PingReceipt", "WebSocketSession"),
    },
    "webhooks": {
        "references": ("OperationRef",),
        "webhooks": (
            "KeySet",
            "ResolvedWebhookOptions",
            "VerifiedSignature",
            "VerifiedWebhook",
            "Verifier",
            "WebhookOptions",
        ),
    },
}
_LAZY_PROTOCOLS: Final = ("pagination", "polling", "cache_stores", "streams", "uploads", "websocket")
_BODY_NAMES: Final = ("AsyncBinaryBody", "SyncBinaryBody")
_MULTIPART_NAMES: Final = (
    "AsyncBodyInput",
    "AsyncMultipartBody",
    "BodyInput",
    "FieldPart",
    "FilePart",
    "MultipartBody",
)
_RESPONSE_PART_NAMES: Final = ("DecodedPart", "MultipartData")
_PARTS: Final = "_runtime.client.multipart_responses"


class _Facades:
    """Render the facade modules of a client package: its public modules and its package initializers."""

    def __init__(self, role: Role, capabilities: Capabilities) -> None:
        """Keep the roles and the capabilities the package declares."""
        self.role = role
        self.capabilities = capabilities

    def package(self) -> str:
        """Return the package initializer, which imports a client only when one of its names is first requested."""
        return render_facade(
            self.role,
            "",
            "HTTP client generated by datamodel-code-generator.",
            future=True,
            checking=starmap(statement, _CLIENTS),
            exports=sorted(name for _, names in _CLIENTS for name in names),
            lazy=tuple(Lazy(name_set(names), ".", module[1:]) for module, names in reversed(_CLIENTS)),
            loader="Import a client on first use, so importing the package loads no HTTP runtime.",
        )

    def options(self) -> str:
        """Return the options module: the settings of a client, of its views, of each call, and of retries."""
        return render_facade(
            self.role,
            "options",
            "Settings of each call and of retries: None inherits, and UNSET marks a field whose None removes.",
            future=True,
            imports=(
                statement("._runtime.client.options", sorted(_OPTION_NAMES)),
                statement("._runtime.model_codecs.unset", ("UNSET",)),
            ),
            exports=sorted((*_OPTION_NAMES, "UNSET")),
        )

    def errors(self) -> str:
        """Return the errors module: the client's errors, its credentials' errors, and those of the declared helpers.

        The helpers' errors load on first use.
        """
        raised = {name for helper in self.capabilities.helpers for name in _PROTOCOL_ERRORS.get(helper, ())}
        protocol = [name for name in _PROTOCOL_ERROR_NAMES if name in raised]
        auth = _AUTH_ERROR_NAMES if self.capabilities.security else ()
        return render_facade(
            self.role,
            "errors",
            "Exceptions of this package's clients: every class derives from SDKError.",
            future=True,
            imports=(
                *((statement("._runtime.client.auth", auth),) if auth else ()),
                statement("._runtime.client.errors", _ERROR_NAMES),
            ),
            checking=(statement("._runtime.protocols.errors", protocol),) if protocol else (),
            exports=sorted((*_ERROR_NAMES, *auth, *protocol)),
            lazy=(Lazy(name_set(protocol), "._runtime.protocols", "errors", cache=True),) if protocol else (),
            loader="Load the protocol helper exceptions only when one of their classes is requested.",
            listing="List the module's names, including the protocol helper exceptions loaded on first use."
            if protocol
            else "",
        )

    def responses(self) -> str:
        """Return the responses module: typed results, the metadata of their responses, and raw responses."""
        return render_facade(
            self.role,
            "responses",
            "Typed results of this package's calls, the metadata of their responses, and raw responses.",
            imports=(
                statement("._runtime.client.raw", ("AsyncRawResponse", "RawResponse")),
                statement("._runtime.client.responses", ("HeadersView", "Response", "ResponseInfo")),
            ),
            exports=("AsyncRawResponse", "HeadersView", "RawResponse", "Response", "ResponseInfo"),
        )

    def model_codecs(self) -> str:
        """Return the module of the JSON value type and the sentinel for an omitted argument."""
        return render_facade(
            self.role,
            "model_codecs",
            "JSON values and omitted arguments used by the generated client.",
            imports=(
                statement("._runtime.model_codecs.media", ("JSONValue",)),
                statement("._runtime.model_codecs.unset", ("UNSET",)),
            ),
            exports=("JSONValue", "UNSET"),
        )

    def auth(self, credentials: tuple[CredentialSpec, ...]) -> str | None:
        """Return the auth module: the OAuth providers, and one per flow with a declared token URL, or None."""
        if not any(credential.flows for credential in credentials):
            return None
        classes = tuple(
            Subclass(
                f"{credential.prefix}{base}",
                base,
                f"The {label} provider of the `{credential.name}` credential, at its declared token URL by default.",
                (Attribute("token_url", repr(url)),),
            )
            for credential in credentials
            for flow, url in credential.flows
            if url is not None
            for base, label in (_FLOW_CLASSES[flow],)
        )
        return render_facade(
            self.role,
            "auth",
            "The OAuth token providers of this package's declared flows.",
            imports=(statement("._runtime.client.oauth", ("ClientCredentials", "RefreshToken", "TokenSet")),),
            classes=classes,
            exports=sorted({"ClientCredentials", "RefreshToken", "TokenSet", *(item.name for item in classes)}),
        )

    @cached_property
    def body_groups(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Return the names the bodies module imports from each runtime module, none for a module it imports nothing of.

        It exports the binary bodies, the multipart bodies, the parts of multipart responses, and the form data that no
        schema describes, each only when the package's operations declare them.
        """
        capabilities = self.capabilities
        groups = (
            (
                "._runtime.client.bodies",
                _BODY_NAMES if capabilities.binary_bodies or capabilities.multipart_requests else (),
            ),
            ("._runtime.client.multipart", _MULTIPART_NAMES if capabilities.multipart_requests else ()),
            (f".{_PARTS}", _RESPONSE_PART_NAMES if capabilities.multipart_responses else ()),
            ("._runtime.client.operations", ("FormData",) if capabilities.form_data else ()),
        )
        return tuple((module, group) for module, group in groups if group)

    def bodies(self) -> str | None:
        """Return the bodies module: the request bodies and media values the package declares, or None without one."""
        if not (groups := self.body_groups):
            return None
        return render_facade(
            self.role,
            "bodies",
            "Request bodies and the values of this package's media types that no schema describes.",
            imports=starmap(statement, groups),
            exports=sorted(name for _, group in groups for name in group),
        )

    def protocols(self) -> str | None:
        """Return the public protocol module: the records, options, and types of the declared helpers, or None.

        The modules of helper sessions, handles, and memory stores load when one of their names is first requested.
        """
        groups: dict[str, set[str]] = {}
        capabilities = self.capabilities
        for helper in (*(("protocols",) if capabilities.protocols else ()), *sorted(capabilities.helpers)):
            for module, names in _PROTOCOL_EXPORTS.get(helper, {}).items():
                groups.setdefault(module, set()).update(names)
        if not groups:
            return None
        lazy = [module for module in _LAZY_PROTOCOLS if module in groups]
        return render_facade(
            self.role,
            "protocols",
            "Public protocol contracts: selectors, helper options, records, resume state, and webhooks.",
            future=True,
            imports=(
                statement(f".._runtime.protocols.{module}", sorted(groups[module]))
                for module in sorted(groups)
                if module not in lazy
            ),
            checking=(statement(f".._runtime.protocols.{module}", sorted(groups[module])) for module in sorted(lazy)),
            exports=sorted(name for names in groups.values() for name in names),
            lazy=tuple(Lazy(name_set(sorted(groups[module])), ".._runtime.protocols", module) for module in lazy),
            loader="Load a helper's session, handle, or memory store type when its name is first requested.",
        )

    def resource_package(self, resource: ResourceSpec) -> str:
        """Return the initializer of one resource's package, which exports its sync and asyncio classes."""
        sync = sorted((f"{resource.pascal}Resource", *(f"{resource.pascal}{suffix}" for _, _, suffix, _ in _VIEWS)))
        names = [f"Async{name}" for name in sync]
        return render_facade(
            self.role,
            f"resources.{resource.namespace}",
            f"The {resource.namespace} resource.",
            imports=(statement("._async", names), statement("._sync", sync)),
            exports=sorted((*names, *sync)),
        )

    def types_package(self, resource: ResourceSpec) -> str:
        """Return the initializer of one resource's types package, which exports its operations' types."""
        names = sorted(name for spec in resource.operations for name in exports(spec))
        return render_facade(
            self.role,
            f"types.{resource.namespace}",
            f"The types of the {resource.namespace} operations.",
            imports=(statement("._operations", names),) if names else (),
            exports=names,
        )

    def docstring(self, module: str, docstring: str) -> str:
        """Return a package initializer that only documents its package."""
        return render_facade(self.role, module, docstring)


@dataclass(frozen=True, slots=True)
class _Model:
    """A payload of a model type: its type, or the spelling of the type an argument of it takes."""

    type: TypeView | ModelHint


@dataclass(frozen=True, slots=True)
class _Parts:
    """A payload of a form-data response with file parts: the value types of its parts, in member order."""

    values: tuple[_Key, ...]


_Key: TypeAlias = _Model | _Parts | str
_SURFACES: Final = {
    "form": ("bodies", "FormData", ""),
    "multipart": ("bodies", "MultipartData", "[bytes]"),
    "wire": ("model_codecs", "JSONValue", ""),
}
_Headers: TypeAlias = "dict[str, tuple[str, list[tuple[ResponseSpec, HeaderSpec]]]]"


Default: TypeAlias = Literal["required", "unset", "none", "value"]


@dataclass(frozen=True, slots=True)
class _Argument:
    """One keyword argument of an operation method: its name, its type spelled in one module, and its default.

    A `value` default is the literal `value` spells.
    """

    name: str
    annotation: str
    default: Default = "required"
    value: str = "None"

    def record(self, module: TargetModule) -> Parameter:
        """Return the argument as a keyword parameter of a signature, with its default expression."""
        match self.default:
            case "required":
                return Parameter(self.name, self.annotation)
            case "unset":
                return Parameter(self.name, self.annotation, module.local("options", "UNSET"))
            case _:
                pass
        return Parameter(self.name, self.annotation, self.value)

    def parameter(self, module: TargetModule) -> str:
        """Return the argument as a keyword parameter of a signature, as text."""
        record = self.record(module)
        return f"{record.name}: {record.annotation}{f' = {record.default}' if record.default else ''}"

    def key(self, module: TargetModule) -> Parameter:
        """Return the argument as a key of a TypedDict, which a call may omit unless the argument is required."""
        if self.default == "required":
            return Parameter(self.name, self.annotation)
        return Parameter(self.name, f"{module.name('typing_extensions', 'NotRequired')}[{self.annotation}]")

    def lookup(self, module: TargetModule) -> str:
        """Return the argument read from the keywords an unpacked method was given, with its default when omitted."""
        match self.default:
            case "required":
                return f"kwargs[{self.name!r}]"
            case "unset":
                return f"kwargs.get({self.name!r}, {module.local('options', 'UNSET')})"
            case "value":
                return f"kwargs.get({self.name!r}, {self.value})"
            case _:
                pass
        return f"kwargs.get({self.name!r})"


@dataclass(frozen=True, slots=True)
class _Variant:
    """One signature's keyword arguments and, for response media choices, its result type."""

    parameters: tuple[_Argument, ...]
    returns: str = ""


_Choice: TypeAlias = "tuple[tuple[_Argument, ...], tuple[_Key, ...]]"
_Branch: TypeAlias = tuple[int, bool, "View", int]
_IMPLEMENTATION: Final = -1


@dataclass(frozen=True, slots=True)
class Signature:
    """One signature of an operation method: its keyword parameters, or the TypedDict it unpacks, and its result."""

    parameters: tuple[Parameter, ...]
    unpacked: str
    returns: str


@dataclass(frozen=True, slots=True)
class Call:
    """An operation method's call of the client core: the operation's plan, its parameters' values, and keywords."""

    operation: str
    parameters: str
    arguments: tuple[Keyword, ...]


@dataclass(frozen=True, slots=True)
class Method:
    """One operation method of a view: its overloads by body and response media, then its implementation.

    `check` names the keywords record an unpacked method checks its keywords with, and is empty otherwise.
    """

    name: str
    view: View
    coroutine: bool
    docstring: str
    overloads: tuple[Signature, ...]
    implementation: Signature
    check: str
    call: Call


@dataclass(frozen=True, slots=True)
class CredentialLabel:
    """A credential argument the runtime reference lists: its name and its scheme kind's label."""

    name: str
    label: str


@dataclass(frozen=True, slots=True)
class CredentialExample:
    """The runtime reference's example of passing the first credential: its name, its value, and the parameters."""

    name: str
    value: str
    annotation: str


@dataclass(frozen=True, slots=True)
class RuntimePagination:
    """The pagination helper kinds a runtime reference describes: cursors, counted positions, and followed URLs."""

    cursors: bool
    counts: bool
    follows: bool


@dataclass(frozen=True, slots=True)
class RuntimeStreams:
    """The stream helpers a runtime reference describes: their label, and whether NDJSON or resumable ones exist."""

    label: str
    ndjson: bool
    resumed: bool


@dataclass(frozen=True, slots=True)
class Constant:
    """A module-level constant: its name, its annotation, and its value expression."""

    name: str
    annotation: str
    value: str


@dataclass(frozen=True, slots=True)
class HeaderOverload:
    """One overload of a header accessor: the literal type of the header name it takes, and its result type."""

    name: str
    returns: str


@dataclass(frozen=True, slots=True)
class Headers:
    """An operation's response header decoders and their accessor function, one overload per header name.

    `name` is the type of the header name the implementation takes.
    """

    constant: str
    annotation: str
    decoders: str
    operation_id: str
    statuses: str
    entries: str
    function: str
    info: str
    overloads: tuple[HeaderOverload, ...]
    name: str
    returns: str
    docstring: str


@dataclass(frozen=True, slots=True)
class Alias:
    """An operation's response alias in its resource's types module, with its header accessor if it has one."""

    name: str
    value: str
    headers: Headers | None


@dataclass(frozen=True, slots=True)
class Record:
    """A TypedDict of the keyword arguments of one signature: its name, base, docstring, and keys."""

    name: str
    base: str
    docstring: str
    members: tuple[Parameter, ...]


@dataclass(frozen=True, slots=True)
class Credential:
    """A credential argument of a root client: its name, its type, and its scheme's name as a literal."""

    name: str
    annotation: str
    scheme: str


@dataclass(frozen=True, slots=True)
class LazyImport:
    """A name a root client imports only for type checking, and from where."""

    module: str
    name: str


@dataclass(frozen=True, slots=True)
class ClientResource:
    """A root resource attribute of a client: its attribute name, namespace, class, and the module it loads from."""

    name: str
    namespace: str
    class_name: str
    module: str


@dataclass(frozen=True, slots=True)
class Member:
    """A cached attribute of a resource view: another view of its operations, or a child resource."""

    name: str
    type: str
    docstring: str


@dataclass(frozen=True, slots=True)
class ResourceView:
    """One class of a resource module: the resource or one of its views, with its attributes and methods."""

    name: str
    docstring: str
    attributes: tuple[Member, ...]
    methods: tuple[Method, ...]


def _union(module: TargetModule, parts: Iterable[str]) -> str:
    return module.union(*parts)


def _merged(module: TargetModule, types: Iterable[TypeView]) -> str:
    """Return the union of model types as type checkers read them, each member once in the order first seen."""
    return _union(
        module,
        (
            module.hint(member)
            for value in types
            for member in (value.members if isinstance(value, UnionType) else (value,))
        ),
    )


def _literal(literal: str, values: Iterable[str]) -> str:
    return f"{literal}[{', '.join(repr(value) for value in values)}]"


def _tuple(values: Iterable[Doc]) -> Group:
    return Group("(", items(values), ")", ",")


def _call(head: str, entries: Iterable[tuple[str, Doc]]) -> Group:
    return Group(f"{head}(", tuple(entries), ")")


def render_sections(role: Role, *, docstring: str, imports: str, sections: Sequence[str]) -> str:
    """Render a module of finished definitions through the `types.jinja2` role, leaving its resource values empty."""
    return role("types.jinja2", types_template.render)(
        docstring=docstring,
        imports=imports,
        type_alias="",
        overload_decorator="",
        aliases=(),
        sections=sections,
    )


def _wire(value: object) -> object:
    """Keep helper literals as ordinary JSON arrays and objects."""
    if isinstance(value, list):
        return list(map(_wire, value))
    return {key: _wire(item) for key, item in value.items()} if isinstance(value, dict) else value


def _mode(*, asynchronous: bool) -> str:
    return "async" if asynchronous else "sync"


def _helpers_module(*, asynchronous: bool) -> str:
    return "_async_helpers" if asynchronous else "_helpers"


def _resume_method(
    module: TargetModule,
    plan: str,
    returns: str,
    options: tuple[list[_Argument], list[tuple[str, Doc]]],
    *,
    asynchronous: bool,
) -> str:
    """Return a polling helper's resume method, which is never awaited and returns the handle start returns."""
    keywords, forwarded = options
    resume = module.local(_POLLING, "aresume_operation" if asynchronous else "resume_operation")
    state = f"state: {module.local('_runtime.model_codecs.media', 'JSONValue')}"
    return "\n".join((
        layout(
            Group(
                "    def resume(",
                items(("self", state, "*", *(argument.parameter(module) for argument in keywords))),
                f") -> {returns}:",
            ),
            4,
            0,
            WIDTH,
        ),
        '        """Return a handle continuing a checkpoint of this helper; it sends nothing until it polls."""',
        "        return "
        + layout(_call(resume, (("", "self._core"), ("", plan), ("", "state"), *forwarded)), 8, 7, WIDTH),
    ))


def _handle(spec: PollingSpec, prefix: str) -> str:
    """Return the name of a polling helper's own handle class, after its dotted name's PascalCase parts."""
    return f"{prefix}{''.join(map(snake_to_upper_camel, spec.helper.name.split('.')))}Handle"


def _docstring(spec: OperationSpec, *, schema: bool) -> str:
    """Return the text of an operation's docstring: its configured description, or its route.

    With `schema`, as `--use-schema-description` sets it, the operation's summary and description, as the models
    document their schemas, come before the route.
    """
    if spec.description:
        return spec.description
    if schema:
        facts = {name: getattr(value, "value", None) for name, value in spec.contract.facts}
        texts = [text.strip() for name in ("summary", "description") if isinstance(text := facts.get(name), str)]
        if text := "\n\n".join(filter(None, texts)):
            return text
    return f"Call {spec.contract.method.upper()} {spec.contract.path}."


def _operation_summary(spec: OperationSpec) -> dict[str, object]:
    """Return how a README lists one operation: its route, body arguments, and the fields or reason of each media."""
    reasons = dict(spec.body_only)
    fields = {branch.media_type: branch.fields for branch in spec.fields}
    return {
        "name": f"{spec.resource}.{spec.name}",
        "method": spec.contract.method.upper(),
        "path": spec.contract.path,
        "body_arguments": spec.body_arguments,
        "media": [
            {
                "media_type": media.media_type,
                "fields": [
                    {"name": field.python_name, "required": field.required}
                    for field in fields.get(media.media_type, ())
                ],
                "reason": reasons.get(media.media_type),
            }
            for media in (() if spec.body is None else spec.body.media)
        ],
    }


def _contract(spec: OperationSpec) -> dict[str, object]:
    """Return the runtime metadata a README declares for one operation."""
    return {
        "operation": f"{spec.resource}.{spec.name}",
        "method": spec.contract.method.upper(),
        "path": spec.contract.path,
        "retry_safety": spec.retry_safety,
        "idempotency": None if (item := spec.idempotency) is None else {"header_name": item.header_name},
        "retry_after_ms_header": spec.retry_after_ms_header,
        "should_retry_header": spec.should_retry_header,
        "security": None
        if spec.security is None
        else [
            {item.scheme.name: list(item.required_scopes) for item in alternative}
            for alternative in spec.security.alternatives
        ],
        "auth_challenge_less_401": spec.auth_challenge_less_401,
    }


def _helper(spec: PaginationSpec | PollingSpec | CacheSpec | UploadSpec | StreamSpec | SocketSpec) -> dict[str, str]:
    """Return how a README lists one protocol helper: its name, kind, and operation."""
    return {
        "helper": spec.helper.name,
        "kind": spec.helper.kind,
        "operation": f"{spec.operation.resource}.{spec.operation.name}",
        "method": spec.operation.contract.method.upper(),
        "path": spec.operation.contract.path,
    }


def exports(spec: OperationSpec) -> tuple[str, ...]:
    """Return the public names the types module of a resource defines for one operation."""
    headers = (f"decode_{spec.name}_header",) if any(response.headers for response in spec.responses) else ()
    name = spec.pascal
    return (f"{name}Response", *headers)


def _header_names(spec: OperationSpec) -> _Headers:
    """Return each declared header name, case-folded, with its spelling and the responses that declare it."""
    names: _Headers = {}
    for response in spec.responses:
        for header in response.headers:
            names.setdefault(header.name.lower(), (header.name, []))[1].append((response, header))
    return names


View: TypeAlias = Literal["plain", "metadata", "raw", "streaming"]
_SIGNATURE_VIEWS: Final[tuple[View, ...]] = ("plain", "metadata", "raw", "streaming")
_VIEWS: Final[tuple[tuple[View, str, str, str], ...]] = (
    ("metadata", "with_response", "WithResponse", "returning each result with its response metadata"),
    ("raw", "with_raw_response", "WithRawResponse", "returning each raw response with its body read into memory"),
    (
        "streaming",
        "with_streaming_response",
        "WithStreamingResponse",
        "returning blocks that send each call on entry and stream its response",
    ),
)


def _core_call(
    module: TargetModule, spec: OperationSpec, *, media: bool, values: Mapping[str, str] | None = None
) -> Call:
    """Return the call an operation method makes of the client core: its parameters, or the values read for them.

    It passes the response media type only when its signature takes one.
    """

    def value(name: str) -> str:
        return name if values is None else values[name]

    arguments: list[Keyword] = []
    if spec.body is not None:
        arguments.append(Keyword("body", value("body")))
        if names := spec.field_names:
            arguments.append(Keyword("fields", flat(_tuple(value(name) for name in names))))
        arguments.append(Keyword("media_type", value("media_type")))
    arguments.append(Keyword("options", value("options")))
    if media:
        arguments.append(Keyword("response_media_type", value("response_media_type")))
    return Call(
        f"{module.root('_operations')}.OPERATION_{spec.index}",
        flat(_tuple(value(parameter.python_name) for parameter in spec.parameters)),
        tuple(arguments),
    )


def _part_plan(module: TargetModule, plan: PartPlan) -> str:
    """Return the PartPlan constructor of one form-data member."""
    repeated = ", repeated=True" if plan.repeated else ""
    return f"{module.local('_runtime.client.multipart', 'PartPlan')}({plan.name!r}, {plan.kind!r}{repeated})"


def _signature(name: str, parameters: tuple[str, ...], returns: str, *, asynchronous: bool, stub: bool) -> str:
    head = f"    {'async ' if asynchronous else ''}def {name}("
    return layout(
        Group(head, items(("self", "*", *parameters)), f") -> {returns}:{' ...' if stub else ''}"), 4, 0, WIDTH
    )


def _function(head: str, parameters: tuple[str, ...], returns: str, *, stub: bool) -> str:
    return layout(Group(head, items(parameters), f") -> {returns}:{' ...' if stub else ''}"), 0, 0, WIDTH)


class _Typing:
    """Spell the payload types of a planned client: model types, or schema-less surfaces."""

    def __init__(
        self,
        plan: ClientPlan,
        accessors: dict[TypeUseId, UseAccessors],
        role: Role = builtin_role,
        *,
        types: TypeNames,
    ) -> None:
        """Keep the plan, the model type names, the codec accessors of every bound use, and the roles."""
        self.plan = plan
        self.types = types
        self.accessors = accessors
        self.role = role

    @staticmethod
    def key(kind: str, use: TypeUseBinding | None) -> _Key:
        """Return the identity of one payload type: its model type, or a schema-less surface."""
        if kind == "binary":
            return "bytes"
        if use is None or use.type is None:
            return {"text": "str", "form": "form", "multipart": "multipart"}.get(kind, "wire")
        return _Model(use.type)

    @staticmethod
    def spell(module: TargetModule, key: _Key) -> str:
        """Return the spelling of one payload type in a module."""
        match key:
            case _Model():
                return module.hint(key.type)
            case _Parts():
                return f"{module.local('bodies', 'MultipartData')}[{_Typing.values(module, key)}]"
            case str() if (surface := _SURFACES.get(key)) is not None:
                package, name, arguments = surface
                return f"{module.local(package, name)}{arguments}"
            case _:
                pass
        return key

    @staticmethod
    def values(module: TargetModule, key: _Parts) -> str:
        """Return the union of the value types of a response's parts."""
        return _union(module, (_Typing.spell(module, value) for value in key.values))

    def argument(self, module: TargetModule, parameter: ParameterSpec) -> str:
        """Return the type of one parameter's keyword argument, with UNSET when it is optional without a default.

        A model type is spelled as the type its argument takes, through its aliases and root models.
        """
        kind = media_kind(parameter.plan.content_media_type) if parameter.plan.content_media_type else "json"
        use = parameter.use
        key = self.key("json" if kind == "form" else kind, use)
        surface = self.spell(
            module,
            _Model(use.argument) if isinstance(key, _Model) and use is not None and use.argument is not None else key,
        )
        if parameter.required or parameter.default is not None:
            return surface
        return module.union(surface, module.local("options", "UNSET"))

    def surface(self, module: TargetModule, kind: str, use: TypeUseBinding | None) -> str:
        """Return the payload type of one media or parameter: its model type, or schema-less surface."""
        return self.spell(module, self.key(kind, use))

    def payloads(self, response: ResponseSpec, media_type: str | None = None) -> list[_Key]:
        """Return the value types one response yields, restricted to those a media type or range dispatches to."""
        if response.bodyless or not response.media:
            return ["None"]
        chosen = (
            None if media_type is None else reachable(media_type, tuple(item.media_type for item in response.media))
        )
        return [
            self.key(media.kind, media.use) if media.members is None else self.parts(media)
            for media in response.media
            if chosen is None or media.media_type in chosen
        ]

    def parts(self, media: MediaSpec) -> _Parts:
        """Return the payload of a response read as parts: each part's type, bytes for files and untyped extras."""
        return _Parts(
            tuple(
                dict.fromkeys(
                    "bytes" if part.use is None else self.key("json", part.use) for part in member_parts(media)
                )
            )
        )

    def successes(self, spec: OperationSpec, media_type: str | None = None) -> tuple[_Key, ...]:
        """Return every success value type without repeats, restricted to one media type when given."""
        keys = (key for response in spec.responses if response.success for key in self.payloads(response, media_type))
        return tuple(dict.fromkeys(keys))

    def union(self, module: TargetModule, keys: Iterable[_Key], empty: str) -> str:
        """Return the union of payload types in a module, or the spelling of an empty union."""
        return _union(module, (self.spell(module, key) for key in keys)) or empty

    def body_surface(self, module: TargetModule, media: MediaSpec, *, asynchronous: bool) -> str:
        """Return the body type of one request media type in the sync or asyncio client."""
        prefix = "Async" if asynchronous else ""
        match media.kind, media.use:
            case "binary", _:
                return module.local("bodies", f"{prefix or 'Sync'}BinaryBody")
            case "multipart", None:
                values = "str"
            case "multipart", _ if media.members is not None:
                values = self.part_values(module, media)
            case _:
                return self.surface(module, media.kind, media.use)
        return f"{module.local('bodies', f'{prefix}MultipartBody')}[{values}]"

    def part_values(self, module: TargetModule, media: MediaSpec) -> str:
        """Return the values the field parts of a body sent as parts take: each member's, JSONValue for any extra."""
        keys = (self.key("json", part.use) for part in member_parts(media) if not part.file)
        return self.union(module, keys, "") or module.name("typing_extensions", "Never")

    def codec(self, module: TargetModule, use: TypeUseBinding) -> str:
        """Return the model bindings codec of a use."""
        return f"{module.local('_generated', 'model_bindings')}.{self.accessors[use.id].codec}"


_BINDERS: Final[dict[str, tuple[str, str]]] = {
    "binary": ("_runtime.client.body_sources", "BINARY_BODIES"),
    "multipart": ("_runtime.client.multipart", "MULTIPART_BODIES"),
}


class _Resources(_Typing):
    """Render the root clients and the resource modules with their typed operation methods."""

    def __init__(  # noqa: PLR0913
        self,
        plan: ClientPlan,
        accessors: dict[TypeUseId, UseAccessors],
        *,
        unpacked: bool = False,
        helpers: tuple[PaginationSpec | PollingSpec | CacheSpec | UploadSpec, ...] = (),
        streams: tuple[StreamSpec, ...] = (),
        sockets: tuple[SocketSpec, ...] = (),
        role: Role = builtin_role,
        types: TypeNames,
        raw_body: RawBody = "multipart",
        use_schema_description: bool = False,
        use_single_line_docstring: bool = False,
    ) -> None:
        """Keep the typing context, unpacked methods' TypedDicts, protocol helpers, and the bodies raw calls take.

        An operation method's docstring is its configured description, else, with `use_schema_description`, the
        operation's summary and description, else its route, formatted as the models' docstrings are.
        """
        super().__init__(plan, accessors, role, types=types)
        self.records = _Records(self) if unpacked else None
        self.helpers = helpers
        self.streams = streams
        self.sockets = sockets
        self.raw_body: RawBody = raw_body
        self.use_schema_description = use_schema_description
        self.use_single_line_docstring = use_single_line_docstring
        self.docstrings: dict[int, str] = {}

    def defaults(self, module: TargetModule) -> Plan:
        """Return the generated defaults of the clients."""
        arguments: list[Keyword] = []
        if self.plan.security_schemes:
            arguments.append(Keyword("security_schemes", f"{module.local('_generated', 'security')}.ROOT_SCHEMES"))
        name = module.local("_runtime.client.client", "ClientDefaults")
        if helpers := (*self.helpers, *self.streams, *self.sockets):
            value = flat(_tuple(repr((spec.helper.name, spec.helper.kind)) for spec in helpers))
            arguments.append(Keyword("helpers", value))
        if any("gzip" in spec.accepted_content_encodings for spec in self.plan.operations):
            arguments.append(Keyword("request_coding", module.local("_runtime.client.compression", "GZIP")))
        if (binder := _BINDERS.get(self.raw_body)) is not None:
            arguments.append(Keyword("bodies", module.local(*binder)))
        return Plan("_DEFAULTS", "", name, tuple(arguments))

    @staticmethod
    def _create(keywords: list[Parameter]) -> tuple[Keyword, ...]:
        """Return the settings keywords of the root's core creation, the helper keywords as its `HelperSettings`."""
        arguments = [Keyword(item.name, item.name) for item in keywords if item.name not in _HELPER_SETTINGS]
        if helpers := [
            f"{_HELPER_SETTINGS[item.name]}={item.name}" for item in keywords if item.name in _HELPER_SETTINGS
        ]:
            arguments.append(Keyword("protocols", f"HelperSettings({', '.join(helpers)})"))
        return tuple(arguments)

    def raw_annotation(self, module: TargetModule, prefix: str) -> str:
        """Return the body type `request_raw` takes: the bodies of the package's request media, else bytes."""
        match self.raw_body:
            case "multipart":
                return f"{module.local('bodies', f'{prefix}BodyInput')}[{module.local('model_codecs', 'JSONValue')}]"
            case "binary":
                return module.local("bodies", f"{prefix or 'Sync'}BinaryBody")
            case _:
                pass
        return "bytes"

    @cached_property
    def _follows(self) -> bool:
        """Whether a pagination helper follows the URLs a server gives: a next-URL or Link continuation."""
        return any(
            spec.continuation["kind"] in {"next_url", "link"}
            for spec in self.helpers
            if not isinstance(spec, PollingSpec | CacheSpec | UploadSpec)
        )

    def _settings(self, *, asynchronous: bool) -> tuple[tuple[str, str], ...]:
        """Return the helper settings record and the types the helper keywords name, with the modules they come from.

        The record comes first; the client module imports it where the root creates its core, and the types only for
        type checking.
        """
        options = "._runtime.protocols.options"
        names = [("HelperSettings", options), ("HelperOptions", options)]
        if any(isinstance(spec, CacheSpec) for spec in self.helpers):
            names.append((f"{'Async' if asynchronous else ''}CacheStore", "._runtime.protocols.caches"))
        if self._follows:
            names.append(("Origin", "._runtime.protocols.origins"))
        return tuple(names)

    def _keywords(self, module: TargetModule, *, asynchronous: bool, protocols: bool) -> tuple[list[Parameter], ...]:
        """Return the settings keywords of a root's constructor and of `with_options`: name, annotation, default.

        A root takes `compression` only when an operation declares gzip, `helper_defaults` only beside declared
        helpers, `cache_stores` only beside a cache helper, and `allowed_origins` only beside a helper following a
        server's URLs.
        """
        unset = module.local("_runtime.model_codecs.unset", "UNSET")
        native = module.module("httpx2")
        pairs = f"{module.name('collections.abc', 'Mapping')}[str, str | None] | None"
        given = {
            "base_url": ("str | None", "None"),
            "server": (f"{module.local('options', 'ServerSelection')} | None", "None"),
            "timeout": (f"float | {native}.Timeout | {unset} | None", unset),
            "total_timeout": (f"float | {unset} | None", unset),
            "max_retries": ("int | None", "None"),
            "retry": (f"{module.local('options', 'RetryOptions')} | None", "None"),
            "default_headers": (pairs, "None"),
            "default_query": (pairs, "None"),
            "follow_redirects": ("bool | None", "None"),
            "auth": (f"{native}.Auth | {unset} | None", unset),
        }
        view = [Parameter(name, *given[name]) for name in VIEW_KEYWORDS]
        root = {
            **given,
            "total_timeout": ("float | None", "None"),
            "max_retries": ("int", "2"),
            "clock": (f"{module.local('options', 'Clock')} | None", "None"),
            "http_client": (f"{native}.{'Async' if asynchronous else ''}Client | None", "None"),
        }
        if any("gzip" in spec.accepted_content_encodings for spec in self.plan.operations):
            root["compression"] = (f'{module.name("typing", "Literal")}["gzip"] | None', '"gzip"')
        if protocols:
            root["helper_defaults"] = (
                f"{module.name('collections.abc', 'Mapping')}[str, HelperOptions] | None",
                "None",
            )
        if any(isinstance(spec, CacheSpec) for spec in self.helpers):
            store = f"{'Async' if asynchronous else ''}CacheStore"
            root["cache_stores"] = (f"{module.name('collections.abc', 'Mapping')}[str, {store}] | None", "None")
        if self._follows:
            root["allowed_origins"] = (f"{module.name('collections.abc', 'Sequence')}[Origin] | None", "None")
        client = [Parameter(name, *root[name]) for name in CLIENT_KEYWORDS if name in root]
        return client, view

    @staticmethod
    def _credential_annotation(module: TargetModule, kind: str) -> str:
        """Return the type of a credential argument of the scheme kind."""
        runtime = "_runtime.client.auth"
        if kind == "basic":
            return module.local(runtime, "UserPassword")
        secret = module.local(runtime, "Secret")
        return f"{secret} | {module.local(runtime, 'TokenSource')}" if kind == "bearer" else secret

    def client(self, *, asynchronous: bool) -> str:
        """Return a root client module: its constructor, lazy resource attributes, and close methods."""
        prefix = "Async" if asynchronous else ""
        roots = [
            (
                resource,
                f"{prefix}{resource.pascal}Resource",
                f".resources.{resource.namespace}._{_mode(asynchronous=asynchronous)}",
            )
            for resource in self.plan.roots
        ]
        names = {
            f"{prefix}Client",
            f"{prefix}ClientView",
            f"{prefix}ClientWithStreamingResponse",
            f"{prefix}ProtocolHelpers",
            "_DEFAULTS",
        }
        protocols = f"{prefix}ProtocolHelpers" if self.helpers or self.streams or self.sockets else None
        settings = self._settings(asynchronous=asynchronous) if protocols is not None else ()
        names.update(name for name, _ in settings)
        module = TargetModule(self.types, {*names, *(name for _, name, _ in roots)}, level=1)
        lazy = [(name, path) for _, name, path in roots]
        helpers_module = f".protocols.{_helpers_module(asynchronous=asynchronous)}"
        if protocols is not None:
            lazy.extend(((protocols, helpers_module), *settings[1:]))
        core = module.local("_runtime.client.client", f"{prefix}ClientCore")
        client_keywords, view_keywords = self._keywords(module, asynchronous=asynchronous, protocols=bool(protocols))
        credentials = [
            Credential(item.name, self._credential_annotation(module, item.scheme.kind), json.dumps(item.scheme.name))
            for item in self.plan.credentials
        ]
        return self.role("client.jinja2", client_template.render)(
            asynchronous=asynchronous,
            docstring=f"The {'asyncio' if asynchronous else 'synchronous'} client of this package.",
            type_checking=module.name("typing", "TYPE_CHECKING") if lazy else "",
            lazy_imports=[LazyImport(path, name) for name, path in lazy],
            defaults=self.defaults(module),
            name=f"{prefix}Client",
            class_docstring=f"Call the operations of this API{' with asyncio' if asynchronous else ''} through HTTPX2.",
            core=core,
            view_keywords=view_keywords,
            client_keywords=client_keywords,
            credentials=credentials,
            create=self._create(client_keywords),
            scheme_credentials=module.local("_runtime.client.auth", "SchemeCredentials") if credentials else "",
            unset=module.local("_runtime.model_codecs.unset", "UNSET"),
            request_options=module.local("options", "RequestOptions"),
            cached_property=module.name("functools", "cached_property"),
            raw=module.local("responses", f"{prefix}RawResponse"),
            binary=self.raw_annotation(module, prefix),
            manager=module.name("contextlib", f"Abstract{prefix}ContextManager"),
            self_type=module.name("typing_extensions", "Self"),
            traceback=module.name("types", "TracebackType"),
            add_secondary=module.local("_runtime.client.errors", "add_secondary"),
            resources=[
                ClientResource(resource.parts[0], resource.namespace, name, path) for resource, name, path in roots
            ],
            protocols=protocols or "",
            protocols_module=helpers_module,
            helper_settings=settings[0][1] if settings else "",
            imports=module.imports(),
        )

    def resource(self, resource: ResourceSpec, *, asynchronous: bool) -> str:
        """Return a resource module: the resource and its metadata, raw, and streaming views of every operation."""
        prefix = "Async" if asynchronous else ""
        main = f"{prefix}{resource.pascal}Resource"
        views: list[tuple[View, str, str, str]] = [
            (view, attribute, f"{prefix}{resource.pascal}{suffix}", doc) for view, attribute, suffix, doc in _VIEWS
        ]
        module = TargetModule(self.types, {main, *(name for _, _, name, _ in views)}, level=len(resource.parts) + 2)
        cached = module.name("functools", "cached_property")
        attributes = [Member(attribute, name, f"The same operations, {doc}.") for _, attribute, name, doc in views]
        pascals = {item.namespace: item.pascal for item in self.plan.resources}
        for child in resource.children:
            name = child.rpartition(".")[2]
            class_name = f"{prefix}{pascals[child]}Resource"
            alias = module.name(f".{name}._{_mode(asynchronous=asynchronous)}", class_name)
            attributes.append(Member(name, alias, f"The {child} operations."))
        classes = [
            ResourceView(
                main,
                f"The {resource.namespace} operations.",
                tuple(attributes),
                tuple(
                    self.method(module, spec, asynchronous=asynchronous, view="plain") for spec in resource.operations
                ),
            )
        ]
        classes.extend(
            ResourceView(
                name,
                f"The {resource.namespace} operations, {doc}.",
                (),
                tuple(self.method(module, spec, asynchronous=asynchronous, view=view) for spec in resource.operations),
            )
            for view, _, name, doc in views
        )
        core = module.local("_runtime.client.client", f"{prefix}ClientCore")
        overloaded = any(method.overloads for view in classes for method in view.methods)
        kind = "asyncio" if asynchronous else "synchronous"
        return self.role("resource.jinja2", resource_template.render)(
            docstring=f"The {kind} operations of the {resource.namespace} resource.",
            core=core,
            cached_property=cached,
            overload_decorator=module.name("typing", "overload") if overloaded else "",
            views=classes,
            imports=module.imports(),
        )

    def parameter(self, module: TargetModule, parameter: ParameterSpec) -> _Argument:
        """Return one argument of an operation method: optional ones default to their schema's default, or UNSET."""
        annotation = self.argument(module, parameter)
        if parameter.required:
            return _Argument(parameter.python_name, annotation)
        if (default := parameter.default) is None:
            return _Argument(parameter.python_name, annotation, "unset")
        return _Argument(parameter.python_name, annotation, "value", repr(default.value))

    def requests(
        self, module: TargetModule, spec: OperationSpec, *, asynchronous: bool
    ) -> tuple[list[_Variant], tuple[_Argument, ...]]:
        """Return the body signatures of an operation and the body keywords of its implementation.

        A binary body takes the bytes, file, path, and iterable inputs of the client's mode. A body declared in a
        media range such as image/* takes the concrete media type a call sends within it as any string, and a body
        without declared media takes nothing.
        """
        if (body := spec.body) is None:
            return [_Variant(())], ()
        literal = module.name("typing", "Literal")
        unset = "" if body.required else module.local("options", "UNSET")
        groups: dict[str, list[str]] = {}
        for media in body.media:
            groups.setdefault(self.body_surface(module, media, asynchronous=asynchronous), []).append(media.media_type)
        if not groups:
            nothing = (
                _Argument("body", module.local("options", "UNSET"), "unset"),
                _Argument("media_type", "None", "none"),
            )
            return [_Variant(nothing)], nothing

        def choices(entries: list[str]) -> str:
            return "str" if any(media_range(media) for media in entries) else _literal(literal, entries)

        def selecting(entries: list[str]) -> _Argument:
            if body.default in entries:
                return _Argument("media_type", module.optional(choices(entries)), "none")
            return _Argument("media_type", choices(entries))

        surfaces = _union(module, groups)
        every = choices([media for entries in groups.values() for media in entries])
        if spec.fields:
            return self.field_requests(module, spec, groups, selecting, every)
        implementation = (
            _Argument("body", surfaces) if body.required else _Argument("body", module.union(surfaces, unset), "unset"),
            _Argument("media_type", module.optional(every), "none"),
        )
        if len(groups) == 1 and (body.default is not None or body.required):
            surface, declared = next(iter(groups.items()))
            if body.default is None:
                single = (_Argument("body", surface), _Argument("media_type", choices(declared)))
                return [_Variant(single)], single
            return [_Variant(implementation)], implementation
        variants = [_Variant((_Argument("body", surface), selecting(media))) for surface, media in groups.items()]
        if not body.required:
            variants.append(_Variant((_Argument("body", unset, "unset"), _Argument("media_type", "None", "none"))))
        return variants, implementation

    def field_requests(
        self,
        module: TargetModule,
        spec: OperationSpec,
        groups: dict[str, list[str]],
        selecting: Callable[[list[str]], _Argument],
        every: str,
    ) -> tuple[list[_Variant], tuple[_Argument, ...]]:
        """Return the body signatures of an operation whose calls may give fields, and its implementation's keywords.

        A body signature takes each field argument as UNSET, a field signature takes the body as UNSET and its media's
        fields, and an optional body's signature taking nothing sends nothing. An optional body whose fields are all
        optional has one field signature for each of its fields, which that signature requires.
        """
        body = spec.body
        assert body is not None
        unset = module.local("options", "UNSET")
        omitted = tuple(_Argument(name, unset, "unset") for name in spec.field_names)
        variants = [
            _Variant((_Argument("body", surface), *omitted, selecting(media))) for surface, media in groups.items()
        ]
        shapes: dict[tuple[_Argument, ...], list[str]] = {}
        types: dict[str, list[TypeView]] = {}
        for branch in spec.fields:
            for field in branch.fields:
                types.setdefault(field.python_name, []).append(field.type)
            for arguments in self.field_signatures(module, spec, branch, witnessed=not body.required):
                shapes.setdefault(arguments, []).append(branch.media_type)
        unset_body = _Argument("body", unset, "unset")
        variants.extend(_Variant((unset_body, *arguments, selecting(media))) for arguments, media in shapes.items())
        if not body.required:
            variants.append(_Variant((unset_body, *omitted, _Argument("media_type", "None", "none"))))
        implementation = (
            _Argument("body", module.union(_union(module, groups), unset), "unset"),
            *(_Argument(name, module.union(_merged(module, types[name]), unset), "unset") for name in spec.field_names),
            _Argument("media_type", module.optional(every), "none"),
        )
        return variants, implementation

    @staticmethod
    def field_signatures(
        module: TargetModule, spec: OperationSpec, branch: FieldBranch, *, witnessed: bool
    ) -> list[tuple[_Argument, ...]]:
        """Return the field arguments of one media's field signatures, one for each field when witnessed ones must be.

        A field of another media takes only UNSET.
        """
        unset = module.local("options", "UNSET")
        present = {field.python_name: field for field in branch.fields}

        def argument(name: str) -> _Argument:
            if (field := present.get(name)) is None:
                return _Argument(name, unset, "unset")
            annotation = module.hint(field.type)
            return (
                _Argument(name, annotation)
                if field.required
                else _Argument(name, module.union(annotation, unset), "unset")
            )

        arguments = tuple(argument(name) for name in spec.field_names)
        if not witnessed or branch.required:
            return [arguments]
        return [
            tuple(
                _Argument(argument.name, module.hint(field.type)) if argument.name == field.python_name else argument
                for argument in arguments
            )
            for field in branch.fields
        ]

    def named(self, module: TargetModule, spec: OperationSpec, keys: tuple[_Key, ...]) -> str:
        """Return a union of success types, spelled by the operation's Response alias when it is every one of them."""
        if keys == (every := self.successes(spec)):
            return module.local(f"types.{spec.resource}", f"{spec.pascal}Response")
        return self.union(module, keys, "" if every else module.name("typing_extensions", "Never"))

    def response_arguments(self, module: TargetModule, spec: OperationSpec) -> tuple[list[_Choice], _Choice]:
        """Return the response media arguments of each signature with its success types, and the implementation's."""
        declared = success_media(spec.responses)
        ranged = any(
            media_range(media.media_type)
            for response in spec.responses
            if response.success and not response.bodyless
            for media in response.media
        )
        default = self.successes(spec, spec.response_media_type)
        if not declared and not ranged:
            return [((), default)], ((), default)
        literal = module.name("typing", "Literal") if declared else ""
        groups: dict[tuple[_Key, ...], list[str]] = {}
        for media_type in declared:
            groups.setdefault(self.successes(spec, media_type), []).append(media_type)
        annotation = "str" if ranged else _literal(literal, declared)
        parameters = (_Argument("response_media_type", module.optional(annotation), "none"),)
        outcomes = set(groups)
        if ranged:
            outcomes.add(self.successes(spec))
        if outcomes == {default}:
            return [(parameters, default)], (parameters, default)
        variants: list[_Choice] = [((_Argument("response_media_type", "None", "none"),), default)]
        variants.extend(
            ((_Argument("response_media_type", _literal(literal, media)),), keys) for keys, media in groups.items()
        )
        if ranged:
            variants.append(((_Argument("response_media_type", "str"),), self.successes(spec)))
        return variants, (parameters, self.successes(spec))

    def branches(  # noqa: PLR0913
        self,
        module: TargetModule,
        spec: OperationSpec,
        *,
        asynchronous: bool,
        view: View,
        returns: bool = True,
        arguments_elsewhere: bool = False,
    ) -> tuple[list[_Variant], _Variant]:
        """Return the overloads of an operation method in a view, none for a single signature, and its implementation.

        Raw and streaming views return the raw response whatever media answered, so only the body chooses overloads.
        Without returns, only the keyword arguments are spelled; when they are spelled elsewhere, the module imports
        none of their types.
        """
        spelling = TargetModule(self.types, level=module.level) if arguments_elsewhere else module
        arguments = tuple(self.parameter(spelling, parameter) for parameter in spec.parameters)
        options = _Argument("options", spelling.optional(spelling.local("options", "RequestOptions")), "none")
        bodies, body = self.requests(spelling, spec, asynchronous=asynchronous)
        choices, implementation = self.response_arguments(spelling, spec)
        if view in {"raw", "streaming"}:
            choices = [implementation]

        def result(keys: tuple[_Key, ...]) -> str:
            return self.result(module, spec, keys, asynchronous=asynchronous, view=view) if returns else ""

        overloads = (
            [
                _Variant((*arguments, *variant.parameters, *parameters, options), result(keys))
                for variant in bodies
                for parameters, keys in choices
            ]
            if len(bodies) * len(choices) > 1
            else []
        )
        return overloads, _Variant((*arguments, *body, *implementation[0], options), result(implementation[1]))

    def method(self, module: TargetModule, spec: OperationSpec, *, asynchronous: bool, view: View) -> Method:
        """Return one operation method of a view: its overloads by body and response media, then its implementation.

        An unpacked method takes each signature's keywords as a TypedDict and checks them as Python binds explicit ones.
        """
        overloads, implementation = self.branches(
            module, spec, asynchronous=asynchronous, view=view, arguments_elsewhere=self.records is not None
        )
        signatures = tuple(
            self.signature(module, variant, (spec.index, asynchronous, view, position))
            for position, variant in (*enumerate(overloads), (_IMPLEMENTATION, implementation))
        )
        check = ""
        values = None
        if self.records is not None:
            check = module.local("_generated.client_arguments", f"KEYWORDS_{spec.index}")
            values = {argument.name: argument.lookup(module) for argument in implementation.parameters}
        media = any(argument.name == "response_media_type" for argument in implementation.parameters)
        return Method(
            spec.name,
            view,
            asynchronous and view != "streaming",
            self.docstring(spec),
            signatures[:-1],
            signatures[-1],
            check,
            _core_call(module, spec, media=media, values=values),
        )

    def docstring(self, spec: OperationSpec) -> str:
        """Return the docstring of an operation's methods, formatted once for every view of either client."""
        if (found := self.docstrings.get(spec.index)) is None:
            text = _docstring(spec, schema=self.use_schema_description)
            found = self.docstrings[spec.index] = format_docstring(
                text, 8, use_single_line_docstring=self.use_single_line_docstring
            )
        return found

    def signature(self, module: TargetModule, variant: _Variant, branch: _Branch) -> Signature:
        """Return one signature of an operation method: an overload stub, or the head of its implementation."""
        if (records := self.records) is None:
            return Signature(tuple(argument.record(module) for argument in variant.parameters), "", variant.returns)
        record = module.local("_generated.client_arguments", records.names[branch])
        return Signature((), f"{module.name('typing_extensions', 'Unpack')}[{record}]", variant.returns)

    def result(
        self, module: TargetModule, spec: OperationSpec, keys: tuple[_Key, ...], *, asynchronous: bool, view: View
    ) -> str:
        """Return what an operation method of a view returns when it answers with one of some success types."""
        prefix = "Async" if asynchronous else ""
        match view:
            case "metadata":
                return f"{module.local('responses', 'Response')}[{self.named(module, spec, keys)}]"
            case "raw":
                return module.local("responses", f"{prefix}RawResponse")
            case "streaming":
                manager = module.name("contextlib", f"Abstract{prefix}ContextManager")
                return f"{manager}[{module.local('responses', f'{prefix}RawResponse')}]"
            case _:
                pass
        return self.named(module, spec, keys)


class _Records:
    """The TypedDicts that unpacked methods take: one for each distinct signature of an operation, shared by its views.

    Their names follow the operation's index and the order its signatures first appear, sync before asyncio, in the
    private module that also holds each operation's binding plan.
    """

    def __init__(self, resources: _Resources) -> None:
        """Spell the arguments of every signature of every operation in the module that defines the TypedDicts."""
        self.module = module = TargetModule(resources.types, level=2)
        self.role = resources.role
        self.names: dict[_Branch, str] = {}
        self.definitions: list[tuple[str, str, tuple[_Argument, ...]]] = []
        self.keywords: list[tuple[int, str, tuple[_Argument, ...]]] = []
        for spec in resources.plan.operations:
            shapes: dict[tuple[_Argument, ...], str] = {}
            for asynchronous in (False, True):
                for view in _SIGNATURE_VIEWS:
                    overloads, implementation = resources.branches(
                        module, spec, asynchronous=asynchronous, view=view, returns=False
                    )
                    for position, variant in (*enumerate(overloads), (_IMPLEMENTATION, implementation)):
                        if (name := shapes.get(variant.parameters)) is None:
                            name = shapes[variant.parameters] = f"Operation{spec.index}Arguments{len(shapes) or ''}"
                            self.definitions.append((name, spec.name, variant.parameters))
                        self.names[spec.index, asynchronous, view, position] = name
                    if (asynchronous, view) == (False, "plain"):
                        self.keywords.append((spec.index, spec.name, implementation.parameters))

    def source(self) -> str:
        """Return the private module of the TypedDicts and binding plans."""
        module = self.module
        typed_dict = module.name("typing_extensions", "TypedDict")
        records = [
            Record(
                name,
                typed_dict,
                f"The keyword arguments of one signature of {method}.",
                tuple(argument.key(module) for argument in arguments),
            )
            for name, method, arguments in self.definitions
        ]
        final, keywords = module.name("typing", "Final"), module.local("_runtime.client.arguments", "Keywords")
        plans = [
            Plan(
                f"KEYWORDS_{index}",
                final,
                keywords,
                (
                    Keyword("", repr(method)),
                    Keyword("", flat(_tuple(repr(argument.name) for argument in arguments))),
                    Keyword("", flat(_tuple(repr(item.name) for item in arguments if item.default == "required"))),
                ),
            )
            for index, method, arguments in self.keywords
        ]
        return self.role("arguments.jinja2", arguments_template.render)(
            docstring="The keyword arguments that unpacked operation methods take, with how each binds them.",
            imports=module.imports(),
            records=records,
            keywords=plans,
        )


class _Types(_Typing):
    """Render each resource's types module: result aliases, HTTP errors, and header accessors."""

    def module(self, resource: ResourceSpec) -> str:
        """Return the types module of one resource."""
        reserved = {name for spec in resource.operations for name in exports(spec)}
        reserved.update(f"_{spec.name.upper()}_HEADERS" for spec in resource.operations)
        module = TargetModule(self.types, reserved, level=len(resource.parts) + 2)
        type_alias = module.name("typing", "TypeAlias")
        aliases = [self.alias(module, spec) for spec in resource.operations]
        overloaded = any(alias.headers is not None and alias.headers.overloads for alias in aliases)
        return self.role("types.jinja2", types_template.render)(
            docstring=f"The result types and header accessors of the {resource.namespace} operations.",
            type_alias=type_alias,
            overload_decorator=module.name("typing", "overload") if overloaded else "",
            aliases=aliases,
            sections=(),
            imports=module.imports(),
        )

    def alias(self, module: TargetModule, spec: OperationSpec) -> Alias:
        """Return one operation's response alias, with its header decoders and accessor when it declares headers."""
        successes = self.union(module, self.successes(spec), "None")
        headers = self.header_accessor(module, spec, names) if (names := _header_names(spec)) else None
        return Alias(f"{spec.pascal}Response", successes, headers)

    def header_accessor(self, module: TargetModule, spec: OperationSpec, headers: _Headers) -> Headers:
        """Return one operation's header decoders and its typed accessor function."""
        unset = module.local("options", "UNSET")
        results, every, entries = self.header_entries(module, headers)
        missing = any(not header.required for _, branches in headers.values() for _, header in branches)
        decoders = module.local(_CODECS, "ResponseHeaders")
        absent = unset if missing else module.name("typing_extensions", "Never")
        annotation = f"{module.name('typing', 'Final')}[{decoders}[{_union(module, every)}, {absent}]]"
        keys = ", ".join(repr(response.status) for response in spec.responses)
        info = module.local("responses", "ResponseInfo")
        literal = module.name("typing", "Literal")
        overloads = (
            tuple(HeaderOverload(f"{literal}[{spelling!r}]", result) for spelling, result in results.items())
            if len(results) > 1
            else ()
        )
        return Headers(
            constant=f"_{spec.name.upper()}_HEADERS",
            annotation=annotation,
            decoders=decoders,
            operation_id=repr(spec.operation_id),
            statuses=f"frozenset({{{keys}}})",
            entries=flat(_tuple(entries)),
            function=f"decode_{spec.name}_header",
            info=info,
            overloads=overloads,
            name=_literal(literal, results),
            returns=_union(module, (*every, *((unset,) if missing else ()))),
            docstring=f"Decode one declared response header of {spec.name} from a response's metadata.",
        )

    def header_entries(self, module: TargetModule, headers: _Headers) -> tuple[dict[str, str], list[str], list[Doc]]:
        """Return each header's result type, every value type, and each header's declarations by status."""
        unset = module.local("options", "UNSET")
        results: dict[str, str] = {}
        every: list[str] = []
        entries: list[Doc] = []
        for spelling, branches in headers.values():
            kinds = [self.surface(module, "json", header.use) for _, header in branches]
            optional = not all(header.required for _, header in branches)
            every.extend(kinds)
            results[spelling] = _union(module, (*kinds, *((unset,) if optional else ())))
            declared = _tuple(
                Group("(", (("", repr(response.status)), ("", self.header_branch(module, header))), ")")
                for response, header in branches
            )
            entries.append(Group("(", (("", repr(spelling)), ("", declared)), ")"))
        return results, every, entries

    def header_branch(self, module: TargetModule, header: HeaderSpec) -> Group:
        """Return the HeaderBranch constructor of one status's declaration of a header."""
        assert header.use is not None
        missing = "required_header" if header.required else "optional_header"
        return _call(
            module.local(_CODECS, "HeaderBranch"),
            (
                ("plan=", parameter_plan(module.local, header.plan)),
                ("codec=", self.codec(module, header.use)),
                ("missing=", module.local(_CODECS, missing)),
            ),
        )


class _Security:
    """Render immutable security declarations without importing codecs or models."""

    def __init__(self, plan: ClientPlan, role: Role, types: TypeNames) -> None:
        """Share structurally equal schemes across root and operation catalogues."""
        self.plan = plan
        self.role = role
        self.types = types
        self.schemes = dict.fromkeys((
            *plan.security_schemes,
            *(scheme for spec in plan.operations if spec.security is not None for scheme in spec.security.schemes),
        ))
        self.names = {scheme: f"_SCHEME_{index}" for index, scheme in enumerate(self.schemes)}

    def source(self) -> str:
        """Return the typed root catalogue and each declared operation binding."""
        module = TargetModule(self.types, {"ROOT_SCHEMES", *self.names.values()}, level=2)
        runtime = "_runtime.client.security"
        final = module.name("typing", "Final")
        schemes: list[Plan] = []
        for scheme, name in self.names.items():
            arguments = [Keyword("name", repr(scheme.name))]
            kind = "UnavailableSecurityScheme"
            if isinstance(scheme, SecurityScheme):
                kind = "SecurityScheme"
                arguments.extend((
                    Keyword("kind", repr(scheme.kind)),
                    Keyword("location", repr(scheme.location)),
                    Keyword("wire_name", repr(scheme.wire_name)),
                ))
            schemes.append(Plan(name, final, module.local(runtime, kind), tuple(arguments)))
        root_annotation = f"{final}[tuple[{module.local(runtime, 'SecuritySchemeEntry')}, ...]]"
        operations: list[Plan] = []
        for spec in self.plan.operations:
            if (binding := spec.security) is None:
                continue
            alternatives = _tuple(
                _tuple(
                    _call(
                        module.local(runtime, "SecurityRequirement"),
                        (("scheme=", self.names[item.scheme]), ("required_scopes=", repr(item.required_scopes))),
                    )
                    for item in alternative
                )
                for alternative in binding.alternatives
            )
            constructor = module.local(runtime, "SecurityBinding")
            arguments = [
                Keyword("schemes", flat(_tuple(self.names[scheme] for scheme in binding.schemes))),
                Keyword("alternatives", flat(alternatives)),
            ]
            operations.append(Plan(f"OPERATION_{spec.index}", final, constructor, tuple(arguments)))
        return self.role("security.jinja2", security_template.render)(
            docstring="Immutable security catalogues and ordered operation requirements.",
            imports=module.imports(),
            schemes=schemes,
            root_annotation=root_annotation,
            root=[self.names[scheme] for scheme in self.plan.security_schemes],
            operations=operations,
        )


class _Registry(_Typing):
    """Render the operation registry: each operation's servers, parameters, body, and response decoder."""

    def module(self) -> str:
        """Return the operation registry module."""
        servers: dict[tuple[ServerSpec, ...], str] = {}
        for spec in self.plan.operations:
            servers.setdefault(spec.servers, f"_SERVERS_{len(servers)}")
        reserved = {*(f"OPERATION_{spec.index}" for spec in self.plan.operations), *servers.values()}
        module = TargetModule(self.types, reserved, level=1)
        final = module.name("typing", "Final")
        constants = [Constant(name, final, flat(self.servers(module, specs))) for specs, name in servers.items()]
        plan = module.local(_RUNTIME, "OperationPlan")
        operations = []
        for spec in self.plan.operations:
            annotation = f"{final}[{plan}[{module.local(f'types.{spec.resource}', f'{spec.pascal}Response')}]]"
            arguments = self.operation(module, spec, servers[spec.servers])
            operations.append(Plan(f"OPERATION_{spec.index}", annotation, plan, arguments))
        return self.role("operations.jinja2", operations_template.render)(
            docstring="The operation registry of this package; regenerate it instead of editing.",
            imports=module.imports(),
            servers=constants,
            operations=operations,
        )

    @staticmethod
    def servers(module: TargetModule, specs: tuple[ServerSpec, ...]) -> Doc:
        """Return the tuple of one operation's servers."""
        servers: list[Doc] = []
        for server in specs:
            entries: list[tuple[str, Doc]] = [("url=", repr(server.url))]
            if server.variables:
                variable = module.local(_RUNTIME, "ServerVariable")
                entries.append((
                    "variables=",
                    _tuple(
                        _call(
                            variable,
                            (
                                ("name=", repr(name)),
                                ("default=", repr(default)),
                                *((("enum=", repr(enum)),) if enum else ()),
                            ),
                        )
                        for name, default, enum in server.variables
                    ),
                ))
            servers.append(_call(module.local(_RUNTIME, "ServerPlan"), entries))
        return _tuple(servers)

    def operation(self, module: TargetModule, spec: OperationSpec, servers: str) -> tuple[Keyword, ...]:
        """Return the keywords of the OperationPlan constructor of one operation."""
        entries: list[tuple[str, Doc]] = [
            ("operation_id=", repr(spec.operation_id)),
            ("method=", repr(spec.contract.method.upper())),
            ("path=", repr(spec.contract.path)),
            ("servers=", servers),
            ("responses=", self.decoder(module, spec)),
        ]
        if spec.parameters:
            entries.append(("parameters=", _tuple(self.parameter(module, item) for item in spec.parameters)))
        if (body := spec.body) is not None:
            fields: list[tuple[str, Doc]] = [("media=", _tuple(self.media(module, item) for item in body.media))]
            if body.default is not None:
                fields.append(("default=", repr(body.default)))
            if body.required:
                fields.append(("required=", "True"))
            entries.append(("body=", _call(module.local(_RUNTIME, "RequestBody"), fields)))
        if spec.request_id_header is not None:
            entries.append(("request_id_header=", repr(spec.request_id_header)))
        if spec.response_media_type is not None:
            entries.append(("response_media_type=", repr(spec.response_media_type)))
        entries.extend(self.retry_metadata(module, spec))
        if spec.security is not None:
            entries.append(("security=", f"{module.local('_generated', 'security')}.OPERATION_{spec.index}"))
        if spec.auth_challenge_less_401:
            entries.append(("auth_challenge_less_401=", "True"))
        entries.extend(self.protection_metadata(spec))
        if spec.fields:
            entries.append(("fields=", self.field_arguments(module, spec)))
        return tuple(Keyword(keyword.removesuffix("="), flat(value)) for keyword, value in entries)

    @staticmethod
    def protection_metadata(spec: OperationSpec) -> list[tuple[str, Doc]]:
        """Return an operation's accepted request codings, when it declares them."""
        entries: list[tuple[str, Doc]] = []
        if spec.accepted_content_encodings:
            entries.append(("accepted_content_encodings=", _tuple(map(repr, spec.accepted_content_encodings))))
        return entries

    @staticmethod
    def retry_metadata(module: TargetModule, spec: OperationSpec) -> list[tuple[str, Doc]]:
        """Return the explicit operation retry contract without inferring key or vendor declarations."""
        entries: list[tuple[str, Doc]] = []
        if spec.retry_safety != "method_default":
            entries.append(("retry_safety=", repr(spec.retry_safety)))
        if (idempotency := spec.idempotency) is not None:
            entries.append((
                "idempotency=",
                _call(
                    module.local("_runtime.client.retry", "IdempotencyPlan"),
                    (("header_name=", repr(idempotency.header_name)),),
                ),
            ))
        entries.extend(
            (f"{name}=", repr(header))
            for name in ("retry_after_ms_header", "should_retry_header")
            if (header := getattr(spec, name)) is not None
        )
        return entries

    @staticmethod
    def field_arguments(module: TargetModule, spec: OperationSpec) -> Group:
        """Return the FieldArguments constructor of an operation: each media's fields by their argument positions."""
        names = spec.field_names
        positions = {name: index for index, name in enumerate(names)}
        media: list[Doc] = []
        for branch in spec.fields:
            entries: list[tuple[str, Doc]] = [
                ("media_type=", repr(branch.media_type)),
                (
                    "fields=",
                    _tuple(
                        _tuple((str(positions[field.python_name]), repr(field.wire_name), repr(field.required)))
                        for field in branch.fields
                    ),
                ),
            ]
            media.append(_call(module.local(_RUNTIME, "BodyFields"), entries))
        return _call(
            module.local(_RUNTIME, "FieldArguments"),
            (("method=", repr(spec.name)), ("names=", _tuple(map(repr, names))), ("media=", _tuple(media))),
        )

    def parameter(self, module: TargetModule, parameter: ParameterSpec) -> Group:
        """Return the ParameterSpec constructor of one parameter."""
        entries: list[tuple[str, Doc]] = [("plan=", parameter_plan(module.local, parameter.plan))]
        if (use := parameter.use) is not None and use.id in self.accessors:
            entries.append(("codec=", self.codec(module, use)))
            if parameter.converts:
                entries.append(("converts=", "True"))
        return _call(module.local(_RUNTIME, "ParameterSpec"), entries)

    def media(self, module: TargetModule, media: MediaSpec) -> Group:
        """Return the BodyMedia constructor of one request media type, a form-data one with its form encoder."""
        kind = media.kind if media.kind in {"json", "text", "form", "multipart"} else "binary"
        entries: list[tuple[str, Doc]] = [("media_type=", repr(media.media_type)), ("kind=", repr(kind))]
        form: list[tuple[str, Doc]] = []
        if media.members is not None:
            form.append(("parts=", _tuple(self.sent_plan(module, part) for part in media.members)))
            if media.extra is not None:
                form.append(("additional=", self.sent_plan(module, media.extra)))
        elif kind != "binary" and media.use is not None and media.use.id in self.accessors:
            entries.append(("codec=", self.codec(module, media.use)))
            if kind == "multipart":
                form.extend((("members=", "True"), *self.multipart_members(module, media)))
            else:
                entries.extend(self.form(module, media))
        if kind == "multipart":
            entries.append(("form=", _call(module.local("_runtime.client.multipart", "MultipartForm"), form)))
        return _call(module.local(_RUNTIME, "BodyMedia"), entries)

    def sent_plan(self, module: TargetModule, part: PartSpec) -> Group:
        """Return the PartPlan constructor of one member of a body sent as parts."""
        plan = part.plan
        return _call(
            module.local("_runtime.client.multipart", "PartPlan"),
            (
                ("", repr(plan.name)),
                *((("repeated=", "True"),) if plan.repeated else ()),
                *((("codec=", self.codec(module, part.use)),) if part.use is not None else ()),
                *((("content_types=", _tuple(map(repr, plan.content_types))),) if plan.content_types else ()),
                *((("style=", parameter_plan(module.local, plan.style)),) if plan.style is not None else ()),
            ),
        )

    @staticmethod
    def form(module: TargetModule, media: MediaSpec) -> list[tuple[str, Doc]]:
        """Return the member plan keywords of a URL-encoded media type."""
        entries: list[tuple[str, Doc]] = []
        if media.fields:
            entries.append(("fields=", _tuple(field_plan(module.local, item) for item in media.fields)))
        if media.additional is not None:
            entries.append(("additional=", field_plan(module.local, media.additional)))
        if media.encoded:
            entries.append(("encoded=", _tuple(parameter_plan(module.local, item) for item in media.encoded)))
        return entries

    @staticmethod
    def multipart_members(module: TargetModule, media: MediaSpec) -> list[tuple[str, Doc]]:
        """Return the keywords of a form-data media type whose members its codec writes: their media and styles."""
        entries: list[tuple[str, Doc]] = []
        if media.content_types:
            entries.append(("content_types=", _tuple(repr(pair) for pair in media.content_types)))
        if media.encoded:
            entries.append(("encoded=", _tuple(parameter_plan(module.local, item) for item in media.encoded)))
        return entries

    def decoder(self, module: TargetModule, spec: OperationSpec) -> Group:
        """Return the ResponseDecoder constructor of one operation."""
        success = [branch for item in spec.responses if item.success for branch in self.branches(module, item)]
        errors = [branch for item in spec.responses if item.error for branch in self.branches(module, item)]
        entries: list[tuple[str, Doc]] = [
            ("", _tuple(success)),
            ("", _tuple(errors)),
        ]
        if spec.success_statuses:
            entries.append(("success_statuses=", f"frozenset({{{', '.join(map(str, spec.success_statuses))}}})"))
        if spec.head:
            entries.append(("head=", "True"))
        return _call(module.local(_RUNTIME, "ResponseDecoder"), entries)

    def branches(self, module: TargetModule, response: ResponseSpec) -> list[Doc]:
        """Return the branches of one declared response: one per media type, or one without a body."""
        status = repr(response.status)
        if response.bodyless or not response.media:
            return [f"{module.local(_RUNTIME, 'empty_branch')}({status})"]
        branches: list[Doc] = []
        for media in response.media:
            media_type = repr(media.media_type)
            if media.kind == "binary":
                branches.append(f"{module.local(_RUNTIME, 'binary_branch')}({status}, {media_type})")
            elif media.members is not None:
                branches.append(self.parts_branch(module, status, media))
            elif media.use is None or media.use.id not in self.accessors:
                runtime, name = {
                    "json": (_RUNTIME, "wire_branch"),
                    "text": (_RUNTIME, "text_branch"),
                    "multipart": (_PARTS, "multipart_branch"),
                }.get(media.kind, (_RUNTIME, "form_branch"))
                branches.append(f"{module.local(runtime, name)}({status}, {media_type})")
            elif media.kind == "multipart":
                entries: list[tuple[str, Doc]] = [
                    ("", status),
                    ("", media_type),
                    ("", self.codec(module, media.use)),
                    ("", _tuple(_part_plan(module, item) for item in media.parts)),
                ]
                if media.additional_part is not None:
                    entries.append(("", _part_plan(module, media.additional_part)))
                branches.append(_call(module.local(_PARTS, "object_branch"), entries))
            else:
                entries = [
                    ("", status),
                    ("", media_type),
                    ("", repr(media.kind)),
                    ("", self.codec(module, media.use)),
                    *self.form(module, media),
                ]
                branches.append(_call(module.local(_RUNTIME, "model_branch"), entries))
        return branches

    def parts_branch(self, module: TargetModule, status: str, media: MediaSpec) -> Group:
        """Return the branch of a form-data response with file parts: its reader of each member's parts."""
        reader = f"{module.local(_PARTS, 'PartsReader')}[{self.values(module, self.parts(media))}]"
        entries: list[tuple[str, Doc]] = [("", _tuple(self.read_part(module, part) for part in media.members or ()))]
        if media.extra is not None:
            entries.append(("additional=", self.read_part(module, media.extra)))
        return _call(
            module.local(_PARTS, "parts_branch"),
            (("", status), ("", repr(media.media_type)), ("", _call(reader, entries))),
        )

    def read_part(self, module: TargetModule, part: PartSpec) -> Group:
        """Return the PartDecoder of one member of a response read as parts: its bytes, or its codec's value."""
        plan = part.plan
        flags = [
            (flag, "True")
            for flag, value in (
                ("repeated=", plan.repeated),
                ("required=", part.required),
                ("excluded=", part.excluded),
            )
            if value
        ]
        if part.use is None:
            return _call(module.local(_PARTS, "file_part"), (("", repr(plan.name)), *flags))
        codec = self.codec(module, part.use)
        return _call(
            module.local(_PARTS, "value_part"), (("", repr(plan.name)), ("", repr(plan.kind)), ("", codec), *flags)
        )


_HELPER_OPTIONS: Final = (
    ("pagination_options", ".", "PaginationOptions"),
    ("options", "..options", "RequestOptions"),
)
_STREAM_OPTIONS: Final = (
    ("stream_options", ".", "StreamOptions"),
    ("options", "..options", "RequestOptions"),
)
_SOCKET_OPTIONS: Final = (
    ("ws_options", ".", "WSOptions"),
    ("options", "..options", "RequestOptions"),
)
_HELPER_SETTINGS: Final = {
    "helper_defaults": "defaults",
    "cache_stores": "cache_stores",
    "allowed_origins": "allowed_origins",
}
_HELPER_KINDS: Final = {
    "pagination": "pagination helper",
    "polling": "polling helper",
    "resumable_upload": "upload helper",
    "sse": "SSE helper",
    "ndjson": "NDJSON helper",
    "websocket": "WebSocket helper",
    "cache": "cache helper",
}
_STREAM_KINDS: Final = {"sse": "event stream", "ndjson": "NDJSON stream"}
_STREAM_LABELS: Final = {"sse": "SSE", "ndjson": "NDJSON"}
_HELPER_CALLS: Final[dict[bool, tuple[str, str, str, str]]] = {
    False: ("first_page", "iterate_pages", "following_page", "resume_pages"),
    True: ("afirst_page", "aiterate_pages", "afollowing_page", "aresume_pages"),
}
_POLL_OPTIONS: Final = (("poll_options", ".", "PollOptions"), *_HELPER_OPTIONS[1:])
_POLLING: Final = "_runtime.protocols.polling"
_UPLOADS: Final = "_runtime.protocols.uploads"
_UPLOAD_OPTIONS: Final = (("upload_options", ".", "UploadOptions"), *_HELPER_OPTIONS[1:])
_STREAMS: Final = "_runtime.protocols.streams"
_CACHE: Final = "_runtime.protocols.cache"
_CACHE_OPTIONS: Final = (("cache_options", ".", "CacheOptions"), _HELPER_OPTIONS[1])
_DEFAULT_STATUSES: Final = [200]


class _Helpers:  # noqa: PLR0904 - It renders every helper kind of a package.
    """Render the protocol helpers of a client package: their plans and their sync and asyncio namespaces."""

    def __init__(self, resources: _Resources, fingerprints: Mapping[str, str]) -> None:
        """Keep the resources' typing context, its helpers, and each helper's contract fingerprint."""
        self.resources = resources
        self.helpers = resources.helpers
        self.streams = resources.streams
        self.sockets = resources.sockets
        self.fingerprints = fingerprints

    @staticmethod
    def page(module: TargetModule, spec: PaginationSpec | PollingSpec | UploadSpec) -> str:
        """Return the type of a helper's page or create response: its operation's response alias."""
        return _Helpers.response(module, spec.operation)

    @staticmethod
    def response(module: TargetModule, operation: OperationSpec) -> str:
        """Return the response alias of an operation."""
        return module.local(f"types.{operation.resource}", f"{operation.pascal}Response")

    @staticmethod
    def result(module: TargetModule, spec: PollingSpec) -> str:
        """Return the type of a polling helper's result: the inline value's, the fetch's response, or None."""
        if spec.fetch is not None:
            return _Helpers.response(module, spec.fetch)
        return "None" if spec.value is None else module.hint(spec.value)

    def plans(self) -> str:
        """Return the module of every helper's accessors and plan, then each stream and WebSocket helper's plan."""
        names = ("PLAN_{}", "CANCEL_{}", "_items_{}", "_result_{}", "_immediate_{}")
        streams, sockets = self.streams, self.sockets
        module = TargetModule(
            self.resources.types,
            {
                *(name.format(index) for index in range(len(self.helpers)) for name in names),
                *(f"STREAM_{index}" for index in range(len(streams))),
                *(f"SOCKET_{index}" for index in range(len(sockets))),
            },
            level=2,
        )
        sections: list[str] = []
        for index, spec in enumerate(self.helpers):
            if isinstance(spec, PollingSpec):
                sections.extend(self.polling(module, index, spec))
            elif isinstance(spec, UploadSpec):
                sections.append(self.upload(module, index, spec))
            elif isinstance(spec, CacheSpec):
                sections.extend(self.cache(module, index, spec))
            else:
                sections.extend((self.items(module, index, spec), self.plan(module, index, spec)))
        sections.extend(self.stream_plan(module, index, spec) for index, spec in enumerate(streams))
        sections.extend(self.socket_plan(module, index, spec) for index, spec in enumerate(sockets))
        kinds = sorted({
            *(spec.helper.kind for spec in self.helpers),
            *(("stream",) if streams else ()),
            *(("WebSocket",) if sockets else ()),
        })
        return render_sections(
            self.resources.role,
            docstring=(
                f"The plans of this package's {kinds[0] if len(kinds) == 1 else 'protocol'} helpers; regenerate them "
                "instead of editing."
            ),
            imports=module.imports(),
            sections=sections,
        )

    def cache(self, module: TargetModule, index: int, spec: CacheSpec) -> list[str]:
        """Return a cache helper's plan, naming only settings it declares."""
        helper, operations = spec.helper, module.root("_operations")
        tree = helper.tree
        plan = module.local(_CACHE, "CachePlan")
        entries: list[tuple[str, Doc]] = [
            ("helper_id=", repr(helper.name)),
            ("operation=", self.reference(module, spec.operation)),
            ("call=", f"{operations}.OPERATION_{spec.operation.index}"),
            ("validator=", repr(tree["validator"])),
            ("authenticated=", repr(tree["authenticated"])),
            ("fingerprint=", repr(self.fingerprints[helper.name])),
        ]
        if (statuses := list(tree["statuses"])) != _DEFAULT_STATUSES:
            entries.append(("statuses=", _call("frozenset", (("", _tuple(map(repr, statuses))),))))
        if names := sorted({name.lower() for name in tree["vary_allowlist"]}):
            entries.append(("vary_allowlist=", _call("frozenset", (("", _tuple(map(repr, names))),))))
        head = f"PLAN_{index}: {module.name('typing', 'Final')}[{plan}[{self.response(module, spec.operation)}]] = "
        return [head + layout(_call(plan, entries), 0, len(head), WIDTH)]

    def items(self, module: TargetModule, index: int, spec: PaginationSpec) -> str:
        """Return a pagination helper's typed items accessor."""
        returns = module.optional(module.sequence(module.hint(spec.item)))
        what = f"the items of one page of {spec.helper.name}"
        return self.accessor(module, f"_items_{index}", self.page(module, spec), returns, what, spec.steps)

    @staticmethod
    def accessor(  # noqa: PLR0913, PLR0917
        module: TargetModule, name: str, data: str, returns: str, what: str, steps: tuple[ItemStep, ...]
    ) -> str:
        """Return a typed accessor reading a value through its steps, returning None where an optional step is None."""
        lines = [_function(f"def {name}(", (f"data: {data}",), returns, stub=False), f'    """Return {what}."""']
        expression, last = "data", len(steps) - 1
        for position, step in enumerate(steps):
            match step.kind:
                case "key":
                    expression = f"{expression}[{step.name!r}]"
                case "get":
                    expression = f"{expression}.get({step.name!r})"
                case _:
                    expression = f"{expression}.{step.name}"
            if (step.none and position < last) or step.unset:
                value = f"value_{position}"
                if step.unset:
                    unset = f"isinstance({value}, {module.name('msgspec', 'UnsetType')})"
                    tested = f"isinstance({value} := {expression}, {module.name('msgspec', 'UnsetType')})"
                    condition = f"({value} := {expression}) is None or {unset}" if step.none else tested
                else:
                    condition = f"({value} := {expression}) is None"
                lines.extend((f"    if {condition}:", "        return None"))
                expression = value
        lines.append(f"    return {expression}")
        return "\n".join(lines)

    @staticmethod
    def selector(module: TargetModule, selector: Mapping[str, Any]) -> str:
        """Return the runtime record of a selector; a header selector names its occurrence only when it is all."""
        record, arguments = "StatusSelector", ""
        match selector["from"]:
            case "body":
                record, arguments = "BodySelector", f"pointer={selector['pointer']!r}"
            case "header":
                every = ", occurrence='all'" if selector["occurrence"] == "all" else ""
                record, arguments = "HeaderSelector", f"name={selector['name']!r}{every}"
        return f"{module.local('_runtime.protocols.records', record)}({arguments})"

    @staticmethod
    def target(module: TargetModule, target: Mapping[str, Any]) -> str:
        """Return the runtime record of a request target."""
        records = "_runtime.protocols.records"
        if (location := target["in"]) == "body":
            return f"{module.local(records, 'BodyTarget')}(pointer={target['pointer']!r})"
        if location == "querystring":
            return (
                f"{module.local(records, 'QuerystringTarget')}(name={target['name']!r}, pointer={target['pointer']!r})"
            )
        return f"{module.local(records, 'ParameterTarget')}(location={location!r}, name={target['name']!r})"

    def binding(self, module: TargetModule, binding: Mapping[str, Any]) -> Doc:
        """Return the runtime record of a helper's binding: its target, and its literal or its source and selector."""
        value = binding["value"]
        entries: list[tuple[str, Doc]] = [("target=", self.target(module, binding["target"]))]
        if "literal" in value:
            entries.append(("literal=", repr(_wire(value["literal"]))))
        else:
            entries.extend((
                ("source=", repr(value["source"])),
                ("selector=", self.selector(module, value["selector"])),
            ))
        return _call(module.local("_runtime.protocols.pagination", "PageBinding"), entries)

    def continuation(self, module: TargetModule, spec: PaginationSpec) -> Doc:
        """Return the runtime record of a helper's cursor, offset, page-number, next-URL, or Link continuation."""
        runtime = "_runtime.protocols.pagination"
        continuation = spec.continuation
        kind = continuation["kind"]
        if kind == "link":
            rel = continuation["rel"]
            entries: list[tuple[str, Doc]] = [
                ("header=", repr(continuation["header"])),
                *((("rel=", repr(rel)),) if rel != "next" else ()),
            ]
            return _call(module.local(runtime, "LinkPlan"), entries)
        if kind in {"offset", "page"}:
            evidence = spec.evidence
            return _call(
                module.local(runtime, "CountPlan"),
                (
                    ("kind=", repr(kind)),
                    ("write=", self.target(module, continuation["write"])),
                    ("first=", repr(continuation["first"])),
                    ("step=", repr(continuation["step"].get("literal"))),
                    (f"{evidence}=", self.selector(module, continuation[evidence])),
                ),
            )
        ends = [end["kind"] for end in continuation["end"]]
        values = [repr(end["value"]) for end in continuation["end"] if end["kind"] == "value"]
        entries = [
            ("read=", self.selector(module, continuation["read"])),
            *((("write=", self.target(module, continuation["write"])),) if kind == "cursor" else ()),
            *((("end_missing=", "True"),) if "missing" in ends else ()),
            *((("end_null=", "True"),) if "null" in ends else ()),
            *((("end_values=", _tuple(values)),) if values else ()),
            *((("empty_string_ends=", "True"),) if continuation.get("empty_string") == "end" else ()),
            *((("repeat_request_body=", "True"),) if continuation.get("repeat_request_body") else ()),
        ]
        return _call(module.local(runtime, "CursorPlan" if kind == "cursor" else "NextUrlPlan"), entries)

    def plan(self, module: TargetModule, index: int, spec: PaginationSpec) -> str:
        """Return a helper's plan: its identity, operation, items, continuation, fingerprint, and bindings."""
        records = "_runtime.protocols.records"
        runtime = "_runtime.protocols.pagination"
        helper = spec.helper
        plan = module.local(runtime, "PaginationPlan")
        value = _call(
            plan,
            (
                ("helper_id=", repr(helper.name)),
                (
                    "operation=",
                    (
                        f"{module.local('_runtime.protocols.references', 'OperationRef')}"
                        f"(pointer={spec.operation.contract.id.use_site.pointer!r})"
                    ),
                ),
                ("call=", f"{module.root('_operations')}.OPERATION_{spec.operation.index}"),
                ("items=", f"_items_{index}"),
                (
                    "items_selector=",
                    f"{module.local(records, 'BodySelector')}(pointer={helper.tree['items']['pointer']!r})",
                ),
                ("continuation=", self.continuation(module, spec)),
                ("fingerprint=", repr(self.fingerprints[helper.name])),
                *(
                    (("bindings=", _tuple([self.binding(module, item) for item in bindings])),)
                    if (bindings := helper.tree["bindings"])
                    else ()
                ),
            ),
        )
        head = (
            f"PLAN_{index}: {module.name('typing', 'Final')}"
            f"[{plan}[{module.hint(spec.item)}, {self.page(module, spec)}]] = "
        )
        return head + layout(value, 0, len(head), WIDTH)

    @staticmethod
    def reference(module: TargetModule, operation: OperationSpec) -> str:
        """Return the runtime reference of an operation."""
        return (
            f"{module.local('_runtime.protocols.references', 'OperationRef')}"
            f"(pointer={operation.contract.id.use_site.pointer!r})"
        )

    def polling(self, module: TargetModule, index: int, spec: PollingSpec) -> list[str]:  # noqa: PLR0914
        """Return a polling helper's result accessors and its plan.

        The plan names the create, poll, and result operations, the states, the bindings, the interval, and the
        immediate result, each only when the helper declares it.
        """
        helper, operations = spec.helper, module.root("_operations")
        tree, name = helper.tree, helper.name
        result, poll, create = self.result(module, spec), self.response(module, spec.poll), self.page(module, spec)
        sections: list[str] = []
        entries: list[tuple[str, Doc]] = [
            ("helper_id=", repr(name)),
            ("operation=", self.reference(module, spec.operation)),
            ("create=", f"{operations}.OPERATION_{spec.operation.index}"),
            ("accepted=", _tuple(map(repr, tree["accepted_statuses"]))),
            ("poll_operation=", self.reference(module, spec.poll)),
            ("poll=", f"{operations}.OPERATION_{spec.poll.index}"),
            ("state=", self.selector(module, tree["state"])),
            *(
                (f"{state}=", _tuple(repr(_wire(value)) for value in values))
                for state in STATES
                if (values := tree[state])
            ),
        ]
        if bindings := tree["bindings"]:
            entries.append(("bindings=", _tuple([self.binding(module, item) for item in bindings])))
        records = "_runtime.protocols.records"
        if spec.result == "inline":
            what = f"the result of {name} in its final poll"
            sections.append(self.accessor(module, f"_result_{index}", poll, module.optional(result), what, spec.steps))
            pointer = tree["result"]["selector"]["pointer"]
            entries.extend((
                ("inline=", f"_result_{index}"),
                ("inline_selector=", f"{module.local(records, 'BodySelector')}(pointer={pointer!r})"),
            ))
        if (fetch := spec.fetch) is not None:
            entries.extend((
                ("fetch_operation=", self.reference(module, fetch)),
                ("fetch=", f"{operations}.OPERATION_{fetch.index}"),
            ))
            if bindings := tree["result"]["bindings"]:
                entries.append(("fetch_bindings=", _tuple([self.binding(module, item) for item in bindings])))
        if (steps := spec.immediate) is not None:
            immediate = tree["immediate_result"]
            what = f"the result of {name} in an immediate create response"
            sections.append(self.accessor(module, f"_immediate_{index}", create, module.optional(result), what, steps))
            pointer = immediate["selector"]["pointer"]
            entries.extend((
                ("immediate=", f"_immediate_{index}"),
                ("immediate_statuses=", _tuple(map(repr, immediate["statuses"]))),
                ("immediate_selector=", f"{module.local(records, 'BodySelector')}(pointer={pointer!r})"),
            ))
        interval = tree["interval"]
        entries.append(("interval=", repr(float(interval["seconds"]))))
        if (header := interval["retry_after_header"]) is not None:
            entries.append(("retry_after_header=", repr(header)))
        if (cancel := spec.cancel) is not None:
            sections.append(self.cancel(module, index, spec, cancel))
            entries.append(("cancel=", f"CANCEL_{index}"))
        if (expires_at := tree.get("expires_at")) is not None:
            entries.append(("expires_at=", self.selector(module, expires_at)))
        plan = module.local(_POLLING, "PollingPlan")
        head = f"PLAN_{index}: {module.name('typing', 'Final')}[{plan}[{result}, {poll}, {create}]] = "
        sections.append(head + layout(_call(plan, entries), 0, len(head), WIDTH))
        return sections

    def cancel(self, module: TargetModule, index: int, spec: PollingSpec, cancel: OperationSpec) -> str:
        """Return the plan of a polling helper's remote cancellation, typed by its operation's response."""
        bindings = spec.helper.tree["remote_cancel"]["bindings"]
        entries: list[tuple[str, Doc]] = [
            ("operation=", self.reference(module, cancel)),
            ("call=", f"{module.root('_operations')}.OPERATION_{cancel.index}"),
        ]
        if bindings:
            entries.append(("bindings=", _tuple([self.binding(module, item) for item in bindings])))
        plan = module.local(_POLLING, "CancelPlan")
        head = f"CANCEL_{index}: {module.name('typing', 'Final')}[{plan}[{self.response(module, cancel)}]] = "
        return head + layout(_call(plan, entries), 0, len(head), WIDTH)

    def module(self, *, asynchronous: bool) -> str:  # noqa: PLR0914
        """Return the sync or asyncio module of the helper namespaces and the helpers."""
        prefix = "Async" if asynchronous else ""
        root = f"{prefix}ProtocolHelpers"
        nodes: dict[tuple[str, ...], dict[str, tuple[str, str]]] = {(): {}}
        named: dict[str, str] = {}
        kinds: dict[str, str] = {}
        for spec in (*self.helpers, *self.streams, *self.sockets):
            helper = spec.helper
            for parts, class_name in helper_classes(helper.name, helper.kind):
                nodes.setdefault(parts[:-1], {})[parts[-1]] = (f"{prefix}{class_name}", ".".join(parts))
                if parts == tuple(helper.name.split(".")):
                    named[helper.name] = f"{prefix}{class_name}"
                    kinds[f"{prefix}{class_name}"] = _HELPER_KINDS[helper.kind]
                else:
                    nodes.setdefault(parts, {})
        leaves = {named[spec.helper.name]: spec for spec in self.helpers}
        streams = {named[spec.helper.name]: spec for spec in self.streams}
        sockets = {named[spec.helper.name]: spec for spec in self.sockets}
        handles = {
            name: _handle(spec, prefix)
            for name, spec in leaves.items()
            if isinstance(spec, PollingSpec) and spec.cancel is not None
        }
        names = {
            root,
            *(name for children in nodes.values() for name, _ in children.values()),
            *handles.values(),
        }
        module = TargetModule(self.resources.types, names, level=2)
        core = module.local("_runtime.protocols.client", f"{prefix}ClientCore")
        base_core = module.local("_runtime.client.client", f"{prefix}ClientCore")
        cached = module.name("functools", "cached_property")
        sections: list[str] = []
        for parts, children in nodes.items():
            name = f"{prefix}{''.join(map(snake_to_upper_camel, parts))}Protocols" if parts else root
            members = [
                f"    @{cached}\n    def {attribute}(self) -> {child}:\n"
                f'        """The {dotted} {kinds.get(child, "protocol helpers")}."""\n'
                f"        return {child}(self._core)"
                for attribute, (child, dotted) in children.items()
            ]
            what = f"the {'.'.join(parts)} protocol helpers" if parts else "the protocol helpers of this API"
            sections.append(
                self.node(
                    name,
                    what,
                    core if parts else (base_core, f"{core}.from_client(core)"),
                    members,
                )
            )
        for index, (name, spec) in enumerate(leaves.items()):
            sections.extend(
                self.leaf(module, index, name, spec, core, handle=handles.get(name), asynchronous=asynchronous)
            )
        sections.extend(
            self.node(
                name,
                f"the {spec.helper.name} {_HELPER_KINDS[spec.helper.kind]} of {spec.operation.contract.method.upper()} "
                f"{spec.operation.contract.path}",
                core,
                self.stream_methods(module, index, spec, asynchronous=asynchronous),
                leaf=True,
            )
            for index, (name, spec) in enumerate(streams.items())
        )
        sections.extend(
            self.node(
                name,
                f"the {spec.helper.name} WebSocket helper of {spec.operation.contract.method.upper()} "
                f"{spec.operation.contract.path}",
                core,
                [self.socket_method(module, index, spec, asynchronous=asynchronous)],
                leaf=True,
            )
            for index, (name, spec) in enumerate(sockets.items())
        )
        kind = "asyncio" if asynchronous else "synchronous"
        return render_sections(
            self.resources.role,
            docstring=f"The {kind} protocol helpers of this package, by their dotted names.",
            imports=module.imports(),
            sections=sections,
        )

    def leaf(  # noqa: PLR0913
        self,
        module: TargetModule,
        index: int,
        name: str,
        spec: PaginationSpec | PollingSpec | CacheSpec | UploadSpec,
        core: str,
        *,
        handle: str | None,
        asynchronous: bool,
    ) -> list[str]:
        """Return a helper's class and a polling helper's handle class when it cancels remotely."""
        route = f"{spec.operation.contract.method.upper()} {spec.operation.contract.path}"
        what = f"the {spec.helper.name} {_HELPER_KINDS[spec.helper.kind]} of {route}"
        if isinstance(spec, PollingSpec):
            node = self.node(
                name, what, core, self.start(module, index, spec, handle, asynchronous=asynchronous), leaf=True
            )
            if handle is None:
                return [node]
            return [node, self.handle(module, index, spec, handle, asynchronous=asynchronous)]
        if isinstance(spec, UploadSpec):
            return [
                self.node(
                    name, what, core, self.upload_methods(module, index, spec, asynchronous=asynchronous), leaf=True
                )
            ]
        if not isinstance(spec, CacheSpec):
            return [
                self.node(name, what, core, self.methods(module, index, spec, asynchronous=asynchronous), leaf=True)
            ]
        members = self.fetch(module, index, spec, asynchronous=asynchronous)
        return [self.node(name, what, core, members, leaf=True)]

    @staticmethod
    def node(name: str, what: str, core: str | tuple[str, str], members: list[str], *, leaf: bool = False) -> str:
        """Return a namespace or helper class holding the client core its members send through."""
        kind, binding = (core, "core") if isinstance(core, str) else core
        head = (
            f'class {name}:\n    """{what[0].upper()}{what[1:]}."""\n\n'
            f"    def __init__(self, core: {kind}) -> None:\n"
            f'        """Keep the client core {"the helper sends" if leaf else "its helpers send"} through."""\n'
            f"        self._core = {binding}"
        )
        return "\n\n".join((head, *members))

    def methods(self, module: TargetModule, index: int, spec: PaginationSpec, *, asynchronous: bool) -> list[str]:  # noqa: PLR0914
        """Return a pagination helper's page, iterate, next_page, and resume methods."""
        operation = replace(spec.operation, fields=())
        resources = self.resources
        runtime = "_runtime.protocols.pagination"
        arguments = [resources.parameter(module, parameter) for parameter in operation.parameters]
        body = resources.requests(module, operation, asynchronous=asynchronous)[1]
        options = [
            _Argument(name, module.optional(module.name(source, kind)), "none")
            for name, source, kind in _HELPER_OPTIONS
        ]
        item, page = module.hint(spec.item), self.page(module, spec)
        page_type = f"{module.local(runtime, 'Page')}[{item}, {page}]"
        pager = f"{module.local(runtime, 'AsyncPager' if asynchronous else 'Pager')}[{item}, {page}]"
        plan = f"{module.name('.', '_plans')}.PLAN_{index}"
        passed = [
            ("", "self._core"),
            ("", plan),
            ("", _tuple(parameter.python_name for parameter in operation.parameters)),
            *((f"{argument.name}=", argument.name) for argument in body),
            *((f"{name}=", name) for name, _, _ in _HELPER_OPTIONS),
        ]
        first, iterate, following, resume = (module.local(runtime, name) for name in _HELPER_CALLS[asynchronous])
        keywords = [argument.parameter(module) for argument in options]
        forwarded = [(f"{name}=", name) for name, _, _ in _HELPER_OPTIONS]
        route = f"{operation.contract.method.upper()} {operation.contract.path}"
        signature = tuple(argument.parameter(module) for argument in (*arguments, *body, *options))
        wait = "await " if asynchronous else ""
        return [
            "\n".join((
                _signature("page", signature, page_type, asynchronous=asynchronous, stub=False),
                f'        """Fetch the first page of {route}."""',
                f"        return {wait}{layout(_call(first, passed), 8, 7 + len(wait), WIDTH)}",
            )),
            "\n".join((
                _signature("iterate", signature, pager, asynchronous=False, stub=False),
                f'        """Return a pager over the items of {route}; it sends nothing until it is iterated."""',
                f"        return {layout(_call(iterate, passed), 8, 7, WIDTH)}",
            )),
            "\n".join((
                layout(
                    Group(
                        f"    {'async ' if asynchronous else ''}def next_page(",
                        items(("self", f"page: {page_type}", "*", *keywords)),
                        f") -> {module.optional(page_type)}:",
                    ),
                    4,
                    0,
                    WIDTH,
                ),
                '        """Fetch the page after a page of this helper, or return None after the last page."""',
                f"        return {wait}"
                + layout(
                    _call(following, (("", "self._core"), ("", plan), ("", "page"), *forwarded)),
                    8,
                    7 + len(wait),
                    WIDTH,
                ),
            )),
            "\n".join((
                layout(
                    Group(
                        "    def resume(",
                        items((
                            "self",
                            f"state: {module.local('_runtime.model_codecs.media', 'JSONValue')}",
                            "*",
                            *signature,
                        )),
                        f") -> {pager}:",
                    ),
                    4,
                    0,
                    WIDTH,
                ),
                '        """Return a pager continuing a checkpoint; it sends nothing until it is iterated."""',
                "        return " + layout(_call(resume, (*passed[:2], ("", "state"), *passed[2:])), 8, 7, WIDTH),
            )),
        ]

    def start(
        self, module: TargetModule, index: int, spec: PollingSpec, handle: str | None, *, asynchronous: bool
    ) -> list[str]:
        """Return a polling helper's start and resume methods, which return its handle, or its own handle class."""
        operation = replace(spec.operation, fields=())
        resources = self.resources
        arguments = [resources.parameter(module, parameter) for parameter in operation.parameters]
        body = resources.requests(module, operation, asynchronous=asynchronous)[1]
        options = [
            _Argument(name, module.optional(module.name(source, kind)), "none") for name, source, kind in _POLL_OPTIONS
        ]
        base = module.local(_POLLING, "AsyncLroHandle" if asynchronous else "LroHandle")
        returns = handle or f"{base}[{self.result(module, spec)}, {self.response(module, spec.poll)}]"
        plan = f"{module.name('.', '_plans')}.PLAN_{index}"
        forwarded = [
            *((("handle=", handle),) if handle is not None else ()),
            *((f"{name}=", name) for name, _, _ in _POLL_OPTIONS),
        ]
        passed = [
            ("", "self._core"),
            ("", plan),
            ("", _tuple(parameter.python_name for parameter in operation.parameters)),
            *((f"{argument.name}=", argument.name) for argument in body),
            *forwarded,
        ]
        start = module.local(_POLLING, "astart_operation" if asynchronous else "start_operation")
        route = f"{operation.contract.method.upper()} {operation.contract.path}"
        signature = tuple(argument.parameter(module) for argument in (*arguments, *body, *options))
        wait = "await " if asynchronous else ""
        return [
            "\n".join((
                _signature("start", signature, returns, asynchronous=asynchronous, stub=False),
                f'        """Create the operation of {route} and return the handle that polls it."""',
                f"        return {wait}{layout(_call(start, passed), 8, 7 + len(wait), WIDTH)}",
            )),
            _resume_method(module, plan, returns, (options, forwarded), asynchronous=asynchronous),
        ]

    def fetch(self, module: TargetModule, index: int, spec: CacheSpec, *, asynchronous: bool) -> list[str]:
        """Return a cache helper's fetch method."""
        operation = spec.operation
        arguments = [self.resources.parameter(module, parameter) for parameter in operation.parameters]
        options = [
            _Argument(name, module.optional(module.name(source, kind)), "none") for name, source, kind in _CACHE_OPTIONS
        ]
        plan = f"{module.name('.', '_plans')}.PLAN_{index}"
        passed = [
            ("", "self._core"),
            ("", plan),
            ("", _tuple(parameter.python_name for parameter in operation.parameters)),
            *((f"{name}=", name) for name, _, _ in _CACHE_OPTIONS),
        ]
        result = f"{module.local('_runtime.protocols.caches', 'CacheResult')}[{self.response(module, operation)}]"
        fetch = module.local(_CACHE, "afetch" if asynchronous else "fetch")
        route = f"{operation.contract.method.upper()} {operation.contract.path}"
        signature = tuple(argument.parameter(module) for argument in (*arguments, *options))
        wait = "await " if asynchronous else ""
        return [
            "\n".join((
                _signature("fetch", signature, result, asynchronous=asynchronous, stub=False),
                f'        """Fetch {route} through the helper\'s cache, revalidating a stale entry."""',
                f"        return {wait}{layout(_call(fetch, passed), 8, 7 + len(wait), WIDTH)}",
            )),
        ]

    def handle(self, module: TargetModule, index: int, spec: PollingSpec, name: str, *, asynchronous: bool) -> str:
        """Return a polling helper's own handle class, which also cancels its operation remotely."""
        cancel = spec.cancel
        assert cancel is not None
        base = module.local(_POLLING, "AsyncLroHandle" if asynchronous else "LroHandle")
        receipt = f"{module.local('_runtime.protocols.records', 'CancelReceipt')}[{self.response(module, cancel)}]"
        route = f"{cancel.contract.method.upper()} {cancel.contract.path}"
        prefix, wait = ("async ", "await ") if asynchronous else ("", "")
        return (
            f"class {name}({base}[{self.result(module, spec)}, {self.response(module, spec.poll)}]):\n"
            f'    """A handle of the {spec.helper.name} polling helper, which also cancels the operation with '
            f'{route}."""'
            "\n\n    __slots__ = ()\n\n"
            f"    {prefix}def cancel_remote(self) -> {receipt}:\n"
            '        """Ask the server to cancel the operation; the handle keeps its last poll until it polls again."""'
            "\n"
            f"        return {wait}self._cancel_remote({module.name('.', '_plans')}.CANCEL_{index})"
        )

    @staticmethod
    def upload_result(module: TargetModule, spec: UploadSpec) -> str:
        """Return the type of an upload helper's result: the completion operation's response, or None."""
        return "None" if spec.completion is None else _Helpers.response(module, spec.completion)

    def upload(self, module: TargetModule, index: int, spec: UploadSpec) -> str:
        """Return an upload helper's plan: its operations, selectors, targets, bindings, chunk limit, and completion.

        Each optional entry appears only when the helper declares it.
        """
        helper, operations = spec.helper, module.root("_operations")
        tree, name = helper.tree, helper.name
        create, probe, append = tree["create"], tree["probe"], tree["append"]
        entries: list[tuple[str, Doc]] = [
            ("helper_id=", repr(name)),
            ("operation=", self.reference(module, spec.operation)),
            ("create=", f"{operations}.OPERATION_{spec.operation.index}"),
            ("probe_operation=", self.reference(module, spec.probe)),
            ("probe=", f"{operations}.OPERATION_{spec.probe.index}"),
            ("remote_offset=", self.selector(module, probe["remote_offset"])),
            ("append_operation=", self.reference(module, spec.append)),
            ("append=", f"{operations}.OPERATION_{spec.append.index}"),
            ("offset=", self.target(module, append["offset"])),
            ("max_chunk_bytes=", repr(tree["max_chunk_bytes"])),
            ("partial_commit=", repr(tree["partial_commit"] == "allowed")),
        ]
        if (size := create.get("size")) is not None:
            entries.append(("size=", self.target(module, size)))
        if (expires_at := create.get("expires_at")) is not None:
            entries.append(("expires_at=", self.selector(module, expires_at)))
        entries.extend(
            (key, _tuple([self.binding(module, item) for item in bindings]))
            for key, bindings in (("probe_bindings=", probe["bindings"]), ("append_bindings=", append["bindings"]))
            if bindings
        )
        if (length := append.get("length")) is not None:
            entries.append(("length=", self.target(module, length)))
        if (checksum := append.get("checksum")) is not None:
            entries.extend((
                ("checksum=", self.target(module, checksum["target"])),
                ("checksum_algorithm=", repr(checksum["algorithm"])),
                ("checksum_encoding=", repr(checksum["encoding"])),
                ("checksum_prefix=", repr(checksum["algorithm_prefix"])),
            ))
        if (completion := spec.completion) is not None:
            entries.extend((
                ("completion_operation=", self.reference(module, completion)),
                ("completion=", f"{operations}.OPERATION_{completion.index}"),
                *(
                    ("completion_bindings=", _tuple([self.binding(module, item) for item in bindings]))
                    for bindings in (tree["completion"]["bindings"],)
                    if bindings
                ),
            ))
        plan = module.local(_UPLOADS, "UploadPlan")
        result = self.upload_result(module, spec)
        head = f"PLAN_{index}: {module.name('typing', 'Final')}[{plan}[{result}, {self.page(module, spec)}]] = "
        return head + layout(_call(plan, entries), 0, len(head), WIDTH)

    def upload_methods(self, module: TargetModule, index: int, spec: UploadSpec, *, asynchronous: bool) -> list[str]:  # noqa: PLR0914
        """Return an upload helper's start and resume methods, which return the handle that appends its chunks."""
        operation = replace(spec.operation, fields=())
        resources = self.resources
        size = None if spec.size is None else spec.size.python_name
        parameters = [parameter for parameter in operation.parameters if parameter.python_name != size]
        arguments = [resources.parameter(module, parameter) for parameter in parameters]
        body = resources.requests(module, operation, asynchronous=asynchronous)[1]
        options = [
            _Argument(name, module.optional(module.name(source, kind)), "none")
            for name, source, kind in _UPLOAD_OPTIONS
        ]
        prefix = "Async" if asynchronous else ""
        handle = f"{module.local(_UPLOADS, f'{prefix}UploadHandle')}[{self.upload_result(module, spec)}]"
        source = f"source: {module.name('.', 'UploadSource')}"
        state = f"state: {module.local('_runtime.model_codecs.media', 'JSONValue')}"
        plan = ("", f"{module.name('.', '_plans')}.PLAN_{index}")
        forwarded = [(f"{name}=", name) for name, _, _ in _UPLOAD_OPTIONS]
        start = module.local(_UPLOADS, "astart_upload" if asynchronous else "start_upload")
        resume = module.local(_UPLOADS, "aresume_upload" if asynchronous else "resume_upload")
        route = f"{operation.contract.method.upper()} {operation.contract.path}"
        keywords = tuple(argument.parameter(module) for argument in (*arguments, *body, *options))
        wait, head = ("await ", "    async def ") if asynchronous else ("", "    def ")
        started = (
            ("", "self._core"),
            plan,
            ("", "source"),
            ("", _tuple(parameter.python_name for parameter in parameters)),
            *((f"{argument.name}=", argument.name) for argument in body),
            *forwarded,
        )
        resumed = (("", "self._core"), plan, ("", "source"), ("", "state"), *forwarded)
        option_keywords = tuple(argument.parameter(module) for argument in options)
        return [
            "\n".join((
                layout(Group(f"{head}start(", items(("self", source, "*", *keywords)), f") -> {handle}:"), 4, 0, WIDTH),
                f'        """Measure the source, create the upload of {route}, and return its handle."""',
                f"        return {wait}{layout(_call(start, started), 8, 7 + len(wait), WIDTH)}",
            )),
            "\n".join((
                layout(
                    Group(f"{head}resume(", items(("self", source, state, "*", *option_keywords)), f") -> {handle}:"),
                    4,
                    0,
                    WIDTH,
                ),
                '        """Check a checkpoint and the source size, then continue from the server offset."""',
                f"        return {wait}{layout(_call(resume, resumed), 8, 7 + len(wait), WIDTH)}",
            )),
        ]

    @staticmethod
    def event_type(module: TargetModule, spec: StreamSpec) -> str:
        """Return the type of a stream's event data: its event types, with UnknownEvent when it keeps unknown events."""
        types = [use.type for _, use in spec.events if use.type is not None]
        unknown = (
            (module.local("_runtime.protocols.streams", "UnknownEvent"),)
            if spec.helper.tree["unknown"] == "raw"
            else ()
        )
        return _union(module, (_merged(module, types), *unknown))

    def codec(self, module: TargetModule, use: TypeUseBinding) -> str:
        """Return the codec of an event, error, or message schema's data."""
        return self.resources.codec(module, use)

    def stream_plan(self, module: TargetModule, index: int, spec: StreamSpec) -> str:
        """Return a stream helper's plan: its identity, operation, media type, event and error decoders, and end.

        An NDJSON plan also names its kind, and its final line when the body may end without a line end, and a plan of
        a helper declaring resumption how it reopens its stream.
        """
        runtime = "_runtime.protocols.streams"
        helper = spec.helper
        tree = helper.tree
        plan = module.local(runtime, "EventPlan")
        entries: list[tuple[str, Doc]] = [
            ("helper_id=", repr(helper.name)),
            (
                "operation=",
                (
                    f"{module.local('_runtime.protocols.references', 'OperationRef')}"
                    f"(pointer={spec.operation.contract.id.use_site.pointer!r})"
                ),
            ),
            ("call=", f"{module.root('_operations')}.OPERATION_{spec.operation.index}"),
            ("media=", repr(spec.media)),
        ]
        if not isinstance(schema := tree["event_schema"], dict):
            entries.append(("event=", self.codec(module, spec.events[0][1])))
        else:
            entries.append((
                "routes=",
                _tuple(_tuple((repr(key), self.codec(module, use))) for key, use in spec.events),
            ))
            if (selector := schema["discriminator"])["from"] == "body":
                body = module.local("_runtime.protocols.records", "BodySelector")
                entries.append(("discriminator=", f"{body}(pointer={selector['pointer']!r})"))
            if tree["unknown"] == "raw":
                entries.append(("unknown=", module.local(runtime, "unknown_event")))
        if spec.errors:
            entries.append((
                "errors=",
                _tuple(_tuple((repr(key), self.codec(module, use))) for key, use in spec.errors),
            ))
        if (completion := tree["completion"])["kind"] != "eof":
            entries.extend((("completion=", repr(completion["kind"])), ("terminal=", repr(completion["value"]))))
        if helper.kind != "sse":
            entries.append(("kind=", repr(helper.kind)))
            if (final := tree["final_line"]) != "require_newline":
                entries.append(("final_line=", repr(final)))
        if (reopen := spec.reopen) is not None:
            entries.append(("resume=", self.resumption(module, spec, reopen)))
        head = f"STREAM_{index}: {module.name('typing', 'Final')}[{plan}[{self.event_type(module, spec)}]] = "
        return head + layout(_call(plan, entries), 0, len(head), WIDTH)

    def resumption(self, module: TargetModule, spec: StreamSpec, reopen: OperationSpec) -> Doc:
        """Return the runtime record of a stream helper's resumption: its reopen, cursor, bindings, and expiry.

        A setting is named only when it differs from the runtime's default.
        """
        resume = spec.helper.tree["resume"]
        entries: list[tuple[str, Doc]] = [
            ("operation=", self.reference(module, reopen)),
            ("call=", f"{module.root('_operations')}.OPERATION_{reopen.index}"),
            ("media=", repr(spec.reopen_media)),
            ("write=", self.target(module, resume["write"])),
        ]
        if spec.own:
            entries.append(("own=", "True"))
        if (cursor := resume["cursor"]) != "event_id":
            entries.append(("cursor=", self.selector(module, cursor)))
            entries.extend(
                (f"{name}=", repr(resume[name]))
                for name, default in (("missing", "inherit"), ("null", "clear"))
                if resume[name] != default
            )
        if bindings := resume["bindings"]:
            entries.append(("bindings=", _tuple([self.binding(module, item) for item in bindings])))
        if (reasons := tuple(resume["reconnect_on"])) != ("transport_interruption",):
            entries.append(("reconnect_on=", _tuple(map(repr, reasons))))
        if (expires_at := resume.get("expires_at")) is not None:
            entries.append(("expires_at=", self.selector(module, expires_at)))
        return _call(module.local(_STREAMS, "StreamResumePlan"), entries)

    def stream_methods(self, module: TargetModule, index: int, spec: StreamSpec, *, asynchronous: bool) -> list[str]:
        """Return a stream helper's open method, taking its operation's parameters and body, and any resume method."""
        operation = replace(spec.operation, fields=())
        resources = self.resources
        runtime = "_runtime.protocols.streams"
        arguments = [resources.parameter(module, parameter) for parameter in operation.parameters]
        body = resources.requests(module, operation, asynchronous=asynchronous)[1]
        options = [
            _Argument(name, module.optional(module.name(source, kind)), "none")
            for name, source, kind in _STREAM_OPTIONS
        ]
        handle = module.local(runtime, "AsyncEventStream" if asynchronous else "EventStream")
        passed = [
            ("", "self._core"),
            ("", f"{module.name('.', '_plans')}.STREAM_{index}"),
            ("", _tuple(parameter.python_name for parameter in operation.parameters)),
            *((f"{argument.name}=", argument.name) for argument in body),
            *((f"{name}=", name) for name, _, _ in _STREAM_OPTIONS),
        ]
        opener = module.local(runtime, "aopen_events" if asynchronous else "open_events")
        route = f"{operation.contract.method.upper()} {operation.contract.path}"
        signature = tuple(argument.parameter(module) for argument in (*arguments, *body, *options))
        wait = "await " if asynchronous else ""
        stream = _STREAM_KINDS[spec.helper.kind]
        returns = f"{handle}[{self.event_type(module, spec)}]"
        methods = [
            "\n".join((
                _signature("open", signature, returns, asynchronous=asynchronous, stub=False),
                f'        """Open the {stream} of {route}, returning once its response is a declared success."""',
                f"        return {wait}{layout(_call(opener, passed), 8, 7 + len(wait), WIDTH)}",
            ))
        ]
        if spec.reopen is not None:
            if not spec.own:
                signature = tuple(argument.parameter(module) for argument in options)
                passed = [*passed[:2], *passed[-len(_STREAM_OPTIONS) :]]
            methods.append(self.stream_resume(module, spec, (signature, returns), passed, wait))
        return methods

    @staticmethod
    def stream_resume(
        module: TargetModule,
        spec: StreamSpec,
        signature: tuple[tuple[str, ...], str],
        passed: Sequence[tuple[str, Doc]],
        wait: str,
    ) -> str:
        """Return a stream helper's resume method, which takes a checkpoint and the open method's options.

        A reopen of the helper's own operation also takes the open method's arguments and body, which it sends again.
        """
        given, returns = signature
        resume = module.local(_STREAMS, "aresume_events" if wait else "resume_events")
        state = f"state: {module.local('_runtime.model_codecs.media', 'JSONValue')}"
        passed = [*passed[:2], ("", "state"), *passed[2:]]
        what = f"Reopen the {_STREAM_KINDS[spec.helper.kind]} after a checkpoint's cursor"
        return "\n".join((
            layout(
                Group(
                    f"    {'async ' if wait else ''}def resume(",
                    items(("self", state, "*", *given)),
                    f") -> {returns}:",
                ),
                4,
                0,
                WIDTH,
            ),
            f'        """{what}, returning once its response is a declared success."""',
            f"        return {wait}{layout(_call(resume, passed), 8, 7 + len(wait), WIDTH)}",
        ))

    def message_types(self, module: TargetModule, spec: SocketSpec) -> tuple[str, str]:
        """Return the types a WebSocket helper sends and receives: a schema's types, str for text, bytes for bytes."""
        tree, resources = spec.helper.tree, self.resources
        kinds = {"json": "json", "utf8": "text", "bytes": "binary"}
        return (
            resources.surface(module, kinds[tree["send"]["codec"]], spec.send),
            resources.surface(module, kinds[tree["receive"]["codec"]], spec.receive),
        )

    def socket_plan(self, module: TargetModule, index: int, spec: SocketSpec) -> str:
        """Return a WebSocket helper's plan: its identity, handshake call, messages, and subprotocols.

        Only the settings that differ from the plan's defaults are written.
        """
        helper = spec.helper
        tree = helper.tree
        plan = module.local("_runtime.protocols.websocket", "ChannelPlan")
        entries: list[tuple[str, Doc]] = [
            ("helper_id=", repr(helper.name)),
            (
                "operation=",
                (
                    f"{module.local('_runtime.protocols.references', 'OperationRef')}"
                    f"(pointer={spec.operation.contract.id.use_site.pointer!r})"
                ),
            ),
            ("call=", f"{module.root('_operations')}.OPERATION_{spec.operation.index}"),
            ("fingerprint=", repr(self.fingerprints[helper.name])),
        ]
        for direction, use in (("send", spec.send), ("receive", spec.receive)):
            message = tree[direction]
            if message["codec"] != "json":
                entries.append((f"{direction}_codec=", repr(message["codec"])))
            if message["frame"] != "text":
                entries.append((f"{direction}_frame=", repr(message["frame"])))
            if use is not None:
                entries.append(("encoder=" if direction == "send" else "decoder=", self.codec(module, use)))
        if subprotocols := tree["subprotocols"]:
            entries.append(("subprotocols=", _tuple(map(repr, subprotocols))))
        sent, received = self.message_types(module, spec)
        head = f"SOCKET_{index}: {module.name('typing', 'Final')}[{plan}[{sent}, {received}]] = "
        return head + layout(_call(plan, entries), 0, len(head), WIDTH)

    def socket_method(self, module: TargetModule, index: int, spec: SocketSpec, *, asynchronous: bool) -> str:
        """Return a WebSocket helper's connect method, which takes its operation's parameters."""
        operation = replace(spec.operation, fields=())
        runtime = "_runtime.protocols.websocket"
        arguments = [self.resources.parameter(module, parameter) for parameter in operation.parameters]
        options = [
            _Argument(name, module.optional(module.name(source, kind)), "none")
            for name, source, kind in _SOCKET_OPTIONS
        ]
        session = module.local(runtime, "AsyncWebSocketSession" if asynchronous else "WebSocketSession")
        passed = [
            ("", "self._core"),
            ("", f"{module.name('.', '_plans')}.SOCKET_{index}"),
            ("", _tuple(parameter.python_name for parameter in operation.parameters)),
            *((f"{name}=", name) for name, _, _ in _SOCKET_OPTIONS),
        ]
        opener = module.local(runtime, "aconnect_socket" if asynchronous else "connect_socket")
        route = f"{operation.contract.method.upper()} {operation.contract.path}"
        signature = tuple(argument.parameter(module) for argument in (*arguments, *options))
        sent, received = self.message_types(module, spec)
        returns = f"{session}[{sent}, {received}]"
        summary = f"Open the WebSocket of {route}, returning once its handshake got a valid 101."
        if asynchronous:
            returns = f"{module.name('contextlib', 'AbstractAsyncContextManager')}[{returns}]"
            summary = f"Open the WebSocket of {route} for an async with block once its handshake got a valid 101."
        return "\n".join((
            _signature("connect", signature, returns, asynchronous=False, stub=False),
            f'        """{summary}"""',
            f"        return {layout(_call(opener, passed), 8, 7, WIDTH)}",
        ))


class ClientRenderer:
    """Render every module of one client package from its plan, codec plan, and wire plan."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        config: ClientGenerationConfig,
        plan: ClientPlan,
        batch: GeneratedTypeContractBatch,
        wire: WirePlan,
        codecs: ClientCodecs,
        helpers: tuple[PaginationSpec | PollingSpec | CacheSpec | UploadSpec, ...] = (),
        streams: tuple[StreamSpec, ...] = (),
        sockets: tuple[SocketSpec, ...] = (),
        fingerprints: Mapping[str, str] | None = None,
        webhooks: Callable[[Mapping[TypeUseId, UseAccessors], Role], tuple[tuple[PurePosixPath, str], ...]],
        signatures: frozenset[str] = frozenset(),
        backend: CodecBackend,
        types: TypeNames,
        dependencies: tuple[str, ...] = (),
        templates: TemplateOverlay | None = None,
        use_schema_description: bool = False,
        use_single_line_docstring: bool = False,
    ) -> None:
        """Keep the plans and helpers; the model bindings module and its accessors are rendered when first used.

        The webhook modules are rendered from the use accessors; `signatures` holds the webhook signature kinds. The
        README names the model backend and the runtime dependencies, and a template directory overrides builtin roles.
        Operation methods are documented by the models' docstring options.
        """
        self.config = config
        self.plan = plan
        self.batch = batch
        self.wire = wire
        self.codecs = codecs
        self.helpers = helpers
        self.streams = streams
        self.sockets = sockets
        self.fingerprints = fingerprints or {}
        self.webhooks = webhooks
        self.signatures = signatures
        self.backend = backend
        self.types = types
        self.dependencies = dependencies
        self.role: Role = builtin_role if templates is None else templates.role
        self.use_schema_description = use_schema_description
        self.use_single_line_docstring = use_single_line_docstring

    @cached_property
    def bindings(self) -> RenderedBindings:
        """Return the model bindings module of every bound use, rendered once."""
        return render_model_bindings(self.codecs, self.types)

    @cached_property
    def accessors(self) -> dict[TypeUseId, UseAccessors]:
        """Return the codec accessors of every bound use."""
        return {item.use: item for item in self.bindings.uses}

    @staticmethod
    def file(path: PurePosixPath, kind: str, text: str, *, verbatim: bool = False) -> RenderedFile:
        """Return one generated file of the package."""
        return RenderedFile(path=path, kind=kind, text=text, verbatim=verbatim)

    def readme(self, capabilities: Capabilities, runtime: tuple[PurePosixPath, ...], *, bodies: bool) -> str:
        """Describe the finalized package: its profile, operations, contracts, helpers, dependencies, and capabilities.

        The runtime holds the paths of the copied runtime modules, and `bodies` marks a package with a bodies module.
        """
        config = self.config
        helpers = (*self.helpers, *self.streams, *self.sockets)
        groups: dict[str, list[str]] = {}
        for path in runtime:
            groups.setdefault(path.parent.as_posix(), []).append(path.name)
        public = ("options", *(("bodies",) if bodies else ()), "errors")
        return self.role("readme.jinja2", readme_template.render)(
            package=config.package,
            public_modules=f"{', '.join(public)}, and response types",
            reference="runtime.md",
            signature_style=config.signature_style,
            body_arguments=config.body_arguments,
            backend=self.backend,
            model_package=config.model_package,
            operations=[_operation_summary(spec) for spec in self.plan.operations],
            contracts=json.dumps([_contract(spec) for spec in self.plan.operations], indent=2, ensure_ascii=True),
            helpers=json.dumps([_helper(spec) for spec in helpers], indent=2, ensure_ascii=True) if helpers else "",
            stream_label=self.stream_label if self.streams else "",
            resumes=any(spec.reopen is not None for spec in self.streams),
            sockets=bool(self.sockets),
            kinds=sorted({spec.helper.kind for spec in self.helpers}),
            compression=json.dumps(accepted, indent=2, ensure_ascii=True)
            if (accepted := self._accepted_encodings())
            else "",
            dependencies=self.dependencies,
            security=sorted(capabilities.security),
            capabilities=sorted(capabilities.helpers),
            runtime=[{"package": package, "modules": modules} for package, modules in groups.items()],
        )

    def _accepted_encodings(self) -> dict[str, list[str]]:
        """Return the request codings each operation accepts, by its resource and method name."""
        return {
            f"{spec.resource}.{spec.name}": list(spec.accepted_content_encodings)
            for spec in self.plan.operations
            if spec.accepted_content_encodings
        }

    def runtime_documentation(self, capabilities: Capabilities) -> str:
        """Render the runtime settings and obligations of the capabilities the package declares."""
        credentials = self.plan.credentials
        example = None
        if credentials:
            value, annotation = _CREDENTIAL_EXAMPLES[credentials[0].scheme.kind]
            example = CredentialExample(credentials[0].name, value, annotation)
        pages = [spec for spec in self.helpers if not isinstance(spec, PollingSpec | CacheSpec | UploadSpec)]
        kinds = {spec.continuation["kind"] for spec in pages}
        return self.role("runtime.jinja2", runtime_template.render)(
            package=self.config.package,
            oauth=capabilities.oauth,
            schemes=capabilities.schemes,
            security=bool(capabilities.security),
            raw_bytes=capabilities.raw_body == "bytes",
            multipart_requests=capabilities.multipart_requests,
            credentials=[CredentialLabel(item.name, _KIND_LABELS[item.scheme.kind]) for item in credentials],
            example=example,
            flows=any(item.flows for item in credentials),
            pagination=RuntimePagination(
                "cursor" in kinds, bool(kinds & {"offset", "page"}), bool(kinds & {"next_url", "link"})
            )
            if pages
            else None,
            polling=any(isinstance(spec, PollingSpec) for spec in self.helpers),
            cache=any(isinstance(spec, CacheSpec) for spec in self.helpers),
            uploads=any(isinstance(spec, UploadSpec) for spec in self.helpers),
            streams=RuntimeStreams(
                self.stream_label,
                any(spec.helper.kind == "ndjson" for spec in self.streams),
                any(spec.reopen is not None for spec in self.streams),
            )
            if self.streams
            else None,
            sockets=bool(self.sockets),
            compression=bool(self._accepted_encodings()),
        )

    @cached_property
    def stream_label(self) -> str:
        """Return the kinds of the package's stream helpers, as their documentation names them."""
        return " or ".join(dict.fromkeys(_STREAM_LABELS[spec.helper.kind] for spec in self.streams))

    def helper_files(self, resources: _Resources) -> tuple[RenderedFile, ...]:
        """Return the modules of the package's protocol helpers, none without helpers."""
        if not (self.helpers or self.streams or self.sockets):
            return ()
        helpers = _Helpers(resources, self.fingerprints)
        return (
            self.file(PurePosixPath("protocols", "_plans.py"), "protocols", helpers.plans()),
            self.file(PurePosixPath("protocols", "_helpers.py"), "protocols", helpers.module(asynchronous=False)),
            self.file(PurePosixPath("protocols", "_async_helpers.py"), "protocols", helpers.module(asynchronous=True)),
        )

    def public_files(self, facades: _Facades, resources: _Resources) -> Iterator[RenderedFile]:
        """Yield the package initializer, the clients, and the public modules the package declares."""
        yield self.file(PurePosixPath("__init__.py"), "package", facades.package())
        yield self.file(PurePosixPath("_client.py"), "client", resources.client(asynchronous=False))
        yield self.file(PurePosixPath("_async_client.py"), "client", resources.client(asynchronous=True))
        yield self.file(PurePosixPath("options.py"), "options", facades.options())
        yield self.file(PurePosixPath("errors.py"), "errors", facades.errors())
        yield self.file(PurePosixPath("responses.py"), "responses", facades.responses())
        if (auth := facades.auth(self.plan.credentials)) is not None:
            yield self.file(PurePosixPath("auth.py"), "auth", auth)
        if (bodies := facades.bodies()) is not None:
            yield self.file(PurePosixPath("bodies.py"), "bodies", bodies)
        yield self.file(PurePosixPath("model_codecs.py"), "model_codecs", facades.model_codecs())
        if (protocols := facades.protocols()) is not None:
            yield self.file(PurePosixPath("protocols", "__init__.py"), "protocols", protocols)
        resources_docstring = facades.docstring("resources", "The resources of the clients.")
        yield self.file(PurePosixPath("resources", "__init__.py"), "package", resources_docstring)

    def resource_files(self, facades: _Facades, resources: _Resources) -> Iterator[RenderedFile]:
        """Yield each resource's package and modules, then the types packages and modules of the resources."""
        for resource in self.plan.resources:
            directory = PurePosixPath("resources", *resource.parts)
            yield self.file(directory / "__init__.py", "package", facades.resource_package(resource))
            yield self.file(directory / "_sync.py", "resource", resources.resource(resource, asynchronous=False))
            yield self.file(directory / "_async.py", "resource", resources.resource(resource, asynchronous=True))
        yield self.file(
            PurePosixPath("types", "__init__.py"), "package", facades.docstring("types", "The types of the resources.")
        )
        types = _Types(self.plan, self.accessors, self.role, types=self.types)
        for resource in self.plan.resources:
            directory = PurePosixPath("types", *resource.parts)
            yield self.file(directory / "__init__.py", "package", facades.types_package(resource))
            yield self.file(directory / "_operations.py", "types", types.module(resource))

    def files(self) -> tuple[RenderedFile, ...]:
        """Return every rendered client file in the fixed artifact order."""
        config = self.config
        operations = self.plan.operations
        sent = [media for spec in operations if spec.body is not None for media in spec.body.media]
        received = [media for spec in operations for response in spec.responses for media in response.media]
        capabilities = Capabilities(
            security=declared_security(self.plan),
            helpers=declared_helpers(
                (
                    *(spec.helper.kind for spec in (*self.helpers, *self.streams, *self.sockets)),
                    *(("webhook",) if self.signatures else ()),
                ),
                self.plan,
            ),
            signatures=self.signatures,
            backends=declared_backends(self.codecs),
            keywords=config.signature_style == "unpack",
            schemes=bool(self.plan.security_schemes) or any(spec.security is not None for spec in operations),
            binary_bodies=any(media.kind == "binary" for media in sent),
            multipart_requests=any(media.kind == "multipart" for media in sent),
            form_data=any(
                media.kind == "form" and (media.use is None or media.use.type is None) for media in (*sent, *received)
            ),
            response_headers=any(response.headers for spec in operations for response in spec.responses),
            multipart_responses=any(media.kind == "multipart" for media in received),
        )
        resources = _Resources(
            self.plan,
            self.accessors,
            unpacked=config.signature_style == "unpack",
            helpers=self.helpers,
            streams=self.streams,
            sockets=self.sockets,
            role=self.role,
            types=self.types,
            raw_body=capabilities.raw_body,
            use_schema_description=self.use_schema_description,
            use_single_line_docstring=self.use_single_line_docstring,
        )
        registry = _Registry(self.plan, self.accessors, self.role, types=self.types)
        webhooks = tuple(self.file(path, "webhooks", text) for path, text in self.webhooks(self.accessors, self.role))
        facades = _Facades(self.role, capabilities)
        files = [*self.public_files(facades, resources), *self.resource_files(facades, resources)]
        files.extend((
            self.file(
                PurePosixPath("_generated", "__init__.py"),
                "package",
                facades.docstring("_generated", "Generated plans of this package."),
            ),
            self.file(PurePosixPath("_generated", "model_bindings.py"), "model_bindings", self.bindings.source),
        ))
        if (records := resources.records) is not None:
            files.append(self.file(PurePosixPath("_generated", "client_arguments.py"), "arguments", records.source()))
        if capabilities.schemes:
            files.append(
                self.file(
                    PurePosixPath("_generated", "security.py"),
                    "security",
                    _Security(self.plan, self.role, self.types).source(),
                )
            )
        files.append(self.file(PurePosixPath("_operations.py"), "operations", registry.module()))
        helpers = (*self.helper_files(resources), *webhooks)
        runtime = tuple(runtime_sources(capabilities.modules()))
        documentation = PurePosixPath("_generated_docs")
        readme = self.readme(capabilities, tuple(path for path, _ in runtime), bodies=bool(facades.body_groups))
        return (
            *files,
            *(self.file(path, "runtime", text, verbatim=True) for path, text in runtime),
            *helpers,
            self.file(documentation / "README.md", "readme", readme),
            self.file(documentation / "runtime.md", "documentation", self.runtime_documentation(capabilities)),
        )
