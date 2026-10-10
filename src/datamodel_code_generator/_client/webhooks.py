"""Plan and render the webhook helpers of a client target, at `pkg.webhooks.<name>`.

A helper's event schema, or each schema of its event mapping, is bound through the JSON request body of a webhook or
callback operation that uses it, since only directional uses have codecs; the event is then received as a server
receives that request. A builtin signature is verified by the runtime, an adapter signature by the caller's verifier,
and a helper without a signature only decodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Final, NamedTuple

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client._compiled_templates import webhook as webhook_template
from datamodel_code_generator._client.facade import render_facade, statement
from datamodel_code_generator._client.naming import folded
from datamodel_code_generator._client.plan import schema_use, schema_uses
from datamodel_code_generator._client.render import Function
from datamodel_code_generator._runtime.model_codecs.media import media_kind
from datamodel_code_generator._target_contract import OperationId, SourceLocation
from datamodel_code_generator._target_module import TargetModule
from datamodel_code_generator._target_render import Keyword, Parameter, Plan, call

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import DiagnosticStage, SchemaRef
    from datamodel_code_generator._client.codec_plan import ClientCodecs
    from datamodel_code_generator._client.codec_render import UseAccessors
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper
    from datamodel_code_generator._target_contract import TypeUseBinding, TypeUseId
    from datamodel_code_generator._target_module import TypeNames
    from datamodel_code_generator._target_templates import Role

__all__ = (
    "WebhookEvent",
    "WebhookSpec",
    "key_class",
    "plan_webhooks",
    "webhook_dependencies",
    "webhook_files",
    "webhook_uses",
)

_SENDERS: Final = frozenset({"webhook", "callback"})
_CRYPTOGRAPHY: Final = "cryptography>=50.0.0"


class _Algorithm(NamedTuple):
    """A signature kind's runtime algorithm, by its module and name, its display name, and its key class."""

    module: str
    name: str
    display: str
    key_module: str
    key: str


_HMAC: Final = "_runtime.protocols.signatures"
_PUBLIC_KEYS: Final = "_runtime.protocols.public_keys"
_RSA_PSS: Final = "RSA-PSS with SHA-256, MGF1 with SHA-256, and a 32-byte salt"
_ALGORITHMS: Final = {
    "standard_webhooks": _Algorithm(_HMAC, "HMAC_SHA256", "HMAC-SHA256", "_runtime.protocols.webhook_keys", "HmacKey"),
    "stripe_style": _Algorithm(_HMAC, "HMAC_SHA256", "HMAC-SHA256", "_runtime.protocols.webhook_keys", "HmacKey"),
    "body_hmac": _Algorithm(_HMAC, "HMAC_SHA256", "HMAC-SHA256", "_runtime.protocols.webhook_keys", "HmacKey"),
    "hmac-sha256": _Algorithm(_HMAC, "HMAC_SHA256", "HMAC-SHA256", "_runtime.protocols.webhook_keys", "HmacKey"),
    "hmac-sha512": _Algorithm(_HMAC, "HMAC_SHA512", "HMAC-SHA512", "_runtime.protocols.webhook_keys", "HmacKey"),
    "ed25519": _Algorithm(_PUBLIC_KEYS, "ED25519", "Ed25519", _PUBLIC_KEYS, "Ed25519Key"),
    "rsa-pss-sha256": _Algorithm(_PUBLIC_KEYS, "RSA_PSS_SHA256", _RSA_PSS, _PUBLIC_KEYS, "RSAPSSKey"),
}
_ROOTS: Final = {
    frozenset({True}): "Webhook verification helpers of this package, by their dotted names.",
    frozenset({False}): "Webhook helpers of this package, by their dotted names; they decode unsigned deliveries only.",
    frozenset({True, False}): (
        "Webhook helpers of this package, by their dotted names; they verify signed deliveries or decode unsigned ones."
    ),
}
_KINDS: Final = {
    frozenset({True}): "verification helpers",
    frozenset({False}): "helpers, which decode unsigned deliveries without verifying them",
    frozenset({True, False}): "helpers, which verify signed deliveries or decode unsigned ones",
}
_EVENTS: Final = "_runtime.protocols.webhook_events"
_WEBHOOKS: Final = "_runtime.protocols.webhooks"


def _algorithm(signature: Mapping[str, Any]) -> _Algorithm:
    """Return the builtin algorithm of a signature, the configured one for a body HMAC."""
    return _ALGORITHMS[signature.get("algorithm", signature["kind"])]


def key_class(kind: str) -> str | None:
    """Return the name of the key class of a builtin signature kind, None for an adapter or no signature."""
    return None if (algorithm := _ALGORITHMS.get(kind)) is None else algorithm.key


def webhook_dependencies(specs: tuple[WebhookSpec, ...]) -> tuple[str, ...]:
    """Return cryptography when a helper verifies a public-key signature, and nothing otherwise."""
    builtins = (_ALGORITHMS.get(spec.helper.tree["signature"]["kind"]) for spec in specs)
    return (_CRYPTOGRAPHY,) if any(item is not None and item.module == _PUBLIC_KEYS for item in builtins) else ()


@dataclass(frozen=True, slots=True, kw_only=True)
class WebhookEvent:
    """One event type of a helper: its mapped name, if any, request-body use, event schema, and decoding mode."""

    name: str | None
    use: TypeUseBinding
    schema: Mapping[str, str]


@dataclass(frozen=True, slots=True, kw_only=True)
class WebhookSpec:
    """A webhook helper ready to render: its single event, or its mapped events in declaration order."""

    helper: Helper
    events: tuple[WebhookEvent, ...]


def _problem(code: str, stage: DiagnosticStage, at: str, message: str) -> Diagnostic:
    return Diagnostic(code=code, severity="error", stage=stage, message=message, option_path=at)


def _names(helpers: tuple[Helper, ...]) -> Iterator[tuple[Helper, Diagnostic]]:
    """Refuse a webhook name whose module path is the keys module, or whose packages differ from another's by case."""
    spellings: dict[str, str] = {}
    for helper in helpers:
        parts = helper.name.split(".")
        if folded(parts[0]) == "keys":
            message = f"The webhook helper name {helper.name!r} is taken by the module of the webhook key types"
            yield helper, _problem("E_NAME_COLLISION", "target", helper.at, message)
            continue
        for index in range(1, len(parts)):
            package = ".".join(parts[:index])
            if (spelled := spellings.setdefault(folded(package), package)) != package:
                message = (
                    f"The webhook helper name {helper.name!r} gives the package {package!r}, which differs from "
                    f"{spelled!r} only in case"
                )
                yield helper, _problem("E_NAME_COLLISION", "target", helper.at, message)
                break


def webhook_uses(
    protocols: Protocols | None, request: TargetRequest
) -> tuple[tuple[WebhookSpec, ...], dict[str, list[Diagnostic]]]:
    """Return each enabled webhook helper with its events' request-body uses, and each such helper's problems.

    An event's use is the first JSON request body of a webhook or callback operation whose schema, through references,
    is the event's schema; its decoding mode is settled once the use is bound.
    """
    helpers = (
        ()
        if protocols is None
        else tuple(item for item in protocols.helpers if item.enabled and item.kind == "webhook")
    )
    if protocols is None or not helpers:
        return (), {}
    problems: dict[str, list[Diagnostic]] = {helper.name: [] for helper in helpers}
    for helper, problem in _names(helpers):
        problems[helper.name].append(problem)
    documents = {pointer: document for document, pointer in request.documents.pointers.items()}
    candidates = [
        use
        for use in request.batch.type_uses
        if use.id.role == "request_body"
        and isinstance(owner := use.id.owner, OperationId)
        and owner.kind in _SENDERS
        and use.type is not None
        and media_kind(use.id.media or "") == "json"
    ]
    schemas = schema_uses(request.batch.type_uses)

    def event(helper: Helper, name: str | None, reference: SchemaRef, at: str) -> WebhookEvent | None:
        """Return the event a schema reference binds, adding the helper's problem when there is none."""
        location = SourceLocation(documents[protocols.documents[reference]], reference.pointer, "schema")
        if not request.lease.exists(location):
            message = f"The event_schema {reference.pointer!r} of {helper.name!r} does not exist in its document"
            problems[helper.name].append(_problem("E_CONFIG_VALUE", "config", at, message))
            return None
        expected = None if (bound := schema_use(schemas, location, "request")) is None else bound.type
        if expected is None or (use := next((item for item in candidates if item.type == expected), None)) is None:
            message = (
                f"The event_schema {reference.pointer!r} of {helper.name!r} is not the schema of a JSON request body "
                "of any webhook or callback operation"
            )
            problems[helper.name].append(_problem("E_CONFIG_VALUE", "config", at, message))
            return None
        schema = {"document": protocols.documents[reference], "pointer": reference.pointer}
        return WebhookEvent(name=name, use=use, schema=schema)

    specs: list[WebhookSpec] = []
    for helper in helpers:
        at, reference = f"{helper.at}.event_schema", helper.tree["event_schema"]
        references = (
            [(name, item, f"{at}.mapping[{name!r}]") for name, item in reference["mapping"].items()]
            if isinstance(reference, dict)
            else [(None, reference, at)]
        )
        events = [event(helper, name, item, where) for name, item, where in references]
        if all(item is not None for item in events):
            specs.append(WebhookSpec(helper=helper, events=tuple(item for item in events if item is not None)))
    return tuple(specs), problems


def plan_webhooks(
    events: tuple[WebhookSpec, ...], codecs: ClientCodecs, problems: dict[str, list[Diagnostic]]
) -> tuple[WebhookSpec, ...]:
    """Plan every helper whose events have acquired native codecs and no other problem."""
    return tuple(
        spec
        for spec in events
        if not problems[spec.helper.name] and all(event.use.id in codecs for event in spec.events)
    )


class _Webhooks:
    """Render the webhook package of a client: its namespaces, key types, and one module per helper."""

    def __init__(
        self,
        specs: tuple[WebhookSpec, ...],
        types: TypeNames,
        accessors: Mapping[TypeUseId, UseAccessors],
        role: Role,
    ) -> None:
        self.specs = specs
        self.types = types
        self.accessors = accessors
        self.role = role

    def files(self) -> tuple[tuple[PurePosixPath, str], ...]:
        """Return the package's webhook files, nothing without helpers, and a keys module only for builtin kinds."""
        if not self.specs:
            return ()
        packages: dict[PurePosixPath, set[bool]] = {}
        for spec in self.specs:
            parts = spec.helper.name.split(".")
            for index in range(len(parts)):
                packages.setdefault(PurePosixPath("webhooks", *parts[:index]), set()).add(
                    spec.helper.tree["signature"]["kind"] != "none"
                )
        root = packages.pop(PurePosixPath("webhooks"))
        keys = self.keys()
        return (
            (PurePosixPath("webhooks", "__init__.py"), render_facade(self.role, "webhooks", _ROOTS[frozenset(root)])),
            *(() if keys is None else ((PurePosixPath("webhooks", "keys.py"), keys),)),
            *(
                (
                    package / "__init__.py",
                    render_facade(
                        self.role,
                        ".".join(package.parts),
                        f"The {'.'.join(package.parts[1:])} webhook {_KINDS[frozenset(kinds)]}.",
                    ),
                )
                for package, kinds in sorted(packages.items())
            ),
            *(
                (PurePosixPath("webhooks", *spec.helper.name.split(".")).with_suffix(".py"), self.module(spec))
                for spec in self.specs
            ),
        )

    def keys(self) -> str | None:
        """Return the keys module, which imports the key class of each selected builtin kind only, or None for none."""
        modules: dict[str, set[str]] = {}
        for spec in self.specs:
            if (algorithm := _ALGORITHMS.get(spec.helper.tree["signature"]["kind"])) is not None:
                modules.setdefault(algorithm.key_module, set()).add(algorithm.key)
        if not modules:
            return None
        return render_facade(
            self.role,
            "webhooks.keys",
            "The key types of this package's webhook signatures.",
            imports=(statement(f"..{module}", sorted(names)) for module, names in sorted(modules.items())),
            exports=sorted(set().union(*modules.values())),
        )

    def decoder(self, module: TargetModule, event: WebhookEvent) -> str:
        """Return the runtime decoder of one event type."""
        codec = f"{module.local('_generated', 'model_bindings')}.{self.accessors[event.use.id].codec}"
        return call(module.local(_EVENTS, "EventDecoder"), (codec,))

    def event(self, module: TargetModule, spec: WebhookSpec) -> tuple[str, str]:
        """Return the spelling of a helper's event type, the union of its mapped types, and its decoder."""
        types: dict[str, None] = {}
        for event in spec.events:
            assert event.use.type is not None
            types[module.hint(event.use.type)] = None
        first = spec.events[0]
        if first.name is None:
            return module.union(*types), self.decoder(module, first)
        pointer = spec.helper.tree["event_schema"]["discriminator"]["pointer"]
        mapping = ", ".join(f"{event.name!r}: {self.decoder(module, event)}" for event in spec.events)
        return module.union(*types), call(
            module.local(_EVENTS, "MappedEventDecoder"), (repr(pointer), f"{{{mapping}}}")
        )

    @staticmethod
    def plan(module: TargetModule, spec: WebhookSpec, event: str, decoder: str) -> tuple[str | None, Plan]:
        """Return the spelling of a builtin helper's key type, None for another kind, and its plan."""
        helper = spec.helper
        signature = helper.tree["signature"]
        kind = signature["kind"]
        key, arguments, entries = None, event, list[Keyword]()
        if kind == "none":
            plan = module.local(_EVENTS, "EventPlan")
        elif kind == "adapter":
            plan = module.local("_runtime.protocols.adapters", "AdapterPlan")
            entries = [
                Keyword("timestamp", repr(signature["timestamp"] == "required")),
                Keyword("delivery_id", repr(signature["delivery_id"] == "required")),
            ]
        else:
            algorithm = _algorithm(signature)
            key = module.local(algorithm.key_module, algorithm.key)
            plan = module.local("_runtime.protocols.verification", "WebhookPlan")
            arguments = f"{event}, {key}"
            standard = kind == "standard_webhooks"
            entries = [
                Keyword("kind", repr(kind)),
                Keyword("algorithm", module.local(algorithm.module, algorithm.name)),
                Keyword("header", repr(signature.get("header", "webhook-signature").lower())),
                Keyword("encoding", repr(signature.get("encoding", "base64" if standard else "hex"))),
                Keyword("prefix", repr(signature.get("prefix", "v1," if standard else ""))),
            ]
        annotation = f"{module.name('typing', 'Final')}[{plan}[{arguments}]]"
        values = (Keyword("helper_id", repr(helper.name)), *entries, Keyword("event", decoder))
        return key, Plan("_PLAN", annotation, plan, values)

    def module(self, spec: WebhookSpec) -> str:
        """Return a helper's module: its plan and its sync and asyncio verify functions, or its decode function."""
        helper = spec.helper
        signature = helper.tree["signature"]
        kind = signature["kind"]
        names = {"_PLAN", *(("decode_unverified",) if kind == "none" else ("verify", "verify_async"))}
        module = TargetModule(
            self.types, {*names, *(("K",) if kind == "adapter" else ())}, level=helper.name.count(".") + 2
        )
        event, decoder = self.event(module, spec)
        key, plan = self.plan(module, spec, event, decoder)
        typevar = ""
        if kind == "none":
            functions = (self.unsigned(module, event),)
        else:
            typevar = module.name("typing", "TypeVar") if kind == "adapter" else ""
            functions = tuple(self.function(module, event, key, asynchronous=mode) for mode in (False, True))
        return self.role("webhook.jinja2", webhook_template.render)(
            kind=kind if kind in {"none", "adapter"} else "builtin",
            name=helper.name,
            algorithm="" if key is None else _algorithm(signature).display,
            timestamp=kind in {"standard_webhooks", "stripe_style"} or signature.get("timestamp") == "required",
            imports=module.imports(),
            exports=sorted(names - {"_PLAN"}),
            typevar=typevar,
            plan=plan,
            functions=functions,
        )

    @staticmethod
    def function(module: TargetModule, event: str, key: str | None, *, asynchronous: bool) -> Function:
        """Return the sync or asyncio verify function of a builtin helper, or of an adapter one without a key."""
        keys = module.local(_WEBHOOKS, "KeySet")
        verifier = () if key is not None else (Parameter("verifier", f"{module.local(_WEBHOOKS, 'Verifier')}[K]"),)
        positional = (
            Parameter("raw_body", "bytes"),
            Parameter("headers", f"{module.name('collections.abc', 'Sequence')}[tuple[str, str]]"),
            Parameter("keys", f"{keys}[{'K' if key is None else key}]"),
        )
        keywords = (
            *verifier,
            Parameter("now", module.name("datetime", "datetime")),
            Parameter("options", module.optional(module.local(_WEBHOOKS, "WebhookOptions")), "None"),
        )
        returns = f"{module.local(_WEBHOOKS, 'VerifiedWebhook')}[{event}]"
        runtime, name = (
            ("_runtime.protocols.verification", "averify_webhook" if asynchronous else "verify_webhook")
            if key is not None
            else ("_runtime.protocols.adapters", "averify_adapted" if asynchronous else "verify_adapted")
        )
        arguments = (
            Keyword("", "_PLAN"),
            Keyword("", "raw_body"),
            Keyword("", "headers"),
            Keyword("", "keys"),
            *(() if key is not None else (Keyword("verifier", "verifier"),)),
            Keyword("now", "now"),
            Keyword("options", "options"),
        )
        return Function(
            "verify_async" if asynchronous else "verify",
            positional,
            keywords,
            returns,
            "",
            module.local(runtime, name),
            arguments,
            coroutine=asynchronous,
        )

    @staticmethod
    def unsigned(module: TargetModule, event: str) -> Function:
        """Return the decode function of a helper without a signature."""
        options = module.optional(module.local(_WEBHOOKS, "WebhookOptions"))
        return Function(
            "decode_unverified",
            (Parameter("raw_body", "bytes"),),
            (Parameter("options", options, "None"),),
            event,
            "",
            module.local(_EVENTS, "decode_unsigned"),
            (Keyword("", "_PLAN"), Keyword("", "raw_body"), Keyword("options", "options")),
        )


def webhook_files(
    specs: tuple[WebhookSpec, ...],
    types: TypeNames,
    accessors: Mapping[TypeUseId, UseAccessors],
    role: Role,
) -> tuple[tuple[PurePosixPath, str], ...]:
    """Return the path and text of every webhook file of a package, nothing without webhook helpers.

    The modules render through the package's template roles.
    """
    return _Webhooks(specs, types, accessors, role).files()
