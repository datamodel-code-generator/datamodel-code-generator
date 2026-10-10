"""Plan and render the webhook helpers of a client target, at `pkg.webhooks.<name>`.

A helper's event schema, or each schema of its event mapping, is bound through the JSON request body of a webhook or
callback operation that uses it, since only directional uses have codecs; the event is then received as a server
receives that request. A builtin signature is verified by the runtime, an adapter signature by the caller's verifier,
and a helper without a signature only decodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from textwrap import fill
from typing import TYPE_CHECKING, Any, Final, NamedTuple

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.facade import render_facade, statement
from datamodel_code_generator._client.naming import folded
from datamodel_code_generator._client.plan import schema_use, schema_uses
from datamodel_code_generator._client.render import WIDTH, render_sections
from datamodel_code_generator._python_layout import Group, layout
from datamodel_code_generator._runtime.model_codecs.media import media_kind
from datamodel_code_generator._target_contract import OperationId, SourceLocation
from datamodel_code_generator._target_module import TargetModule
from datamodel_code_generator._target_render import items

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


def _call(head: str, entries: list[tuple[str, Any]]) -> Group:
    return Group(f"{head}(", tuple(entries), ")")


def _docstring(summary: str, text: str, indent: str) -> str:
    """Return a docstring's summary line, then text wrapped to the generated width."""
    body = fill(text, WIDTH, initial_indent=indent, subsequent_indent=indent)
    return f'{indent}"""{summary}\n\n{body}\n{indent}"""'


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

    def decoder(self, module: TargetModule, event: WebhookEvent) -> Group:
        """Return the runtime decoder of one event type."""
        codec = f"{module.local('_generated', 'model_bindings')}.{self.accessors[event.use.id].codec}"
        return _call(module.local(_EVENTS, "EventDecoder"), [("", codec)])

    def event(self, module: TargetModule, spec: WebhookSpec) -> tuple[str, Group]:
        """Return the spelling of a helper's event type, the union of its mapped types, and its decoder."""
        types: dict[str, None] = {}
        for event in spec.events:
            assert event.use.type is not None
            types[module.hint(event.use.type)] = None
        first = spec.events[0]
        if first.name is None:
            return module.union(*types), self.decoder(module, first)
        pointer = spec.helper.tree["event_schema"]["discriminator"]["pointer"]
        mapping = Group("{", tuple((f"{event.name!r}: ", self.decoder(module, event)) for event in spec.events), "}")
        return module.union(*types), _call(
            module.local(_EVENTS, "MappedEventDecoder"), [("", repr(pointer)), ("", mapping)]
        )

    @staticmethod
    def plan(module: TargetModule, spec: WebhookSpec, event: str, decoder: Group) -> tuple[str | None, str]:
        """Return the spelling of a builtin helper's key type, None for another kind, and its plan's definition."""
        helper = spec.helper
        signature = helper.tree["signature"]
        kind = signature["kind"]
        key, arguments, entries = None, event, list[tuple[str, Any]]()
        if kind == "none":
            plan = module.local(_EVENTS, "EventPlan")
        elif kind == "adapter":
            plan = module.local("_runtime.protocols.adapters", "AdapterPlan")
            entries = [
                ("timestamp=", repr(signature["timestamp"] == "required")),
                ("delivery_id=", repr(signature["delivery_id"] == "required")),
            ]
        else:
            algorithm = _algorithm(signature)
            key = module.local(algorithm.key_module, algorithm.key)
            plan = module.local("_runtime.protocols.verification", "WebhookPlan")
            arguments = f"{event}, {key}"
            standard = kind == "standard_webhooks"
            entries = [
                ("kind=", repr(kind)),
                ("algorithm=", module.local(algorithm.module, algorithm.name)),
                ("header=", repr(signature.get("header", "webhook-signature").lower())),
                ("encoding=", repr(signature.get("encoding", "base64" if standard else "hex"))),
                ("prefix=", repr(signature.get("prefix", "v1," if standard else ""))),
            ]
        value = _call(plan, [("helper_id=", repr(helper.name)), *entries, ("event=", decoder)])
        head = f"_PLAN: {module.name('typing', 'Final')}[{plan}[{arguments}]] = "
        return key, head + layout(value, 0, len(head), WIDTH)

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
        if kind == "none":
            summary = "Decode the unsigned deliveries of one webhook helper."
            text = (
                f"The helper is {helper.name}, whose deliveries carry no signature: nothing authenticates who sent "
                "one, that its body is unchanged, or that it is not a replay, so treat every event as untrusted input."
            )
            sections = [plan, self.unsigned(module, event)]
        elif kind == "adapter":
            summary = (
                "Verify the deliveries of one webhook helper with the caller's verifier, then decode their events."
            )
            text = (
                f"The helper is {helper.name}, whose deliveries are verified by the Verifier the caller passes, which "
                "must authenticate the whole raw body and every fact it returns."
            )
            variable = f'K = {module.name("typing", "TypeVar")}("K")'
            sections = [variable, plan, *self.functions(module, event, None, signature)]
        else:
            summary = "Verify the signed deliveries of one webhook helper, then decode their events."
            text = f"The helper is {helper.name}, whose deliveries are signed with {_algorithm(signature).display}."
            sections = [plan, *self.functions(module, event, key, signature)]
        docstring = _docstring(summary, text, "")
        exported = ", ".join(f'"{name}"' for name in sorted(names - {"_PLAN"}))
        return render_sections(
            self.role,
            docstring=docstring.removeprefix('"""').removesuffix('"""'),
            imports=module.imports(),
            sections=[f"__all__ = [{exported}]", *sections],
        )

    @staticmethod
    def functions(module: TargetModule, event: str, key: str | None, signature: Mapping[str, Any]) -> list[str]:
        """Return the sync and asyncio verify functions of a builtin helper, or of an adapter one without a key."""
        timestamp = (
            signature["kind"] in {"standard_webhooks", "stripe_style"} or signature.get("timestamp") == "required"
        )
        window = (
            "Deliveries outside the timestamp window are rejected. " if timestamp else ""
        ) + "Verification retains no delivery state; deduplicate in your application using delivery_id when present."
        if key is None:
            window = (
                "The verifier is called once, synchronously in both functions, and must authenticate the whole raw "
                "body and every fact it returns; a result that is not a VerifiedSignature, lacks a fact this helper "
                "requires, returns one it does not declare, or has a naive timestamp raises ConfigurationError "
                f"before decoding. {window}"
            )
        return [_Webhooks.function(module, event, key, window, asynchronous=mode) for mode in (False, True)]

    @staticmethod
    def function(module: TargetModule, event: str, key: str | None, window: str, *, asynchronous: bool) -> str:
        """Return the sync or asyncio verify function of a builtin helper, or of an adapter one without a key."""
        keys = module.local(_WEBHOOKS, "KeySet")
        verifier = () if key is not None else (f"verifier: {module.local(_WEBHOOKS, 'Verifier')}[K]",)
        parameters = (
            "raw_body: bytes",
            f"headers: {module.name('collections.abc', 'Sequence')}[tuple[str, str]]",
            f"keys: {keys}[{'K' if key is None else key}]",
            "*",
            *verifier,
            f"now: {module.name('datetime', 'datetime')}",
            f"options: {module.optional(module.local(_WEBHOOKS, 'WebhookOptions'))} = None",
        )
        name = "verify_async" if asynchronous else "verify"
        signature = layout(
            Group(
                f"{'async ' if asynchronous else ''}def {name}(",
                items(parameters),
                f") -> {module.local(_WEBHOOKS, 'VerifiedWebhook')}[{event}]:",
            ),
            0,
            0,
            WIDTH,
        )
        runtime, call = (
            ("_runtime.protocols.verification", "averify_webhook" if asynchronous else "verify_webhook")
            if key is not None
            else ("_runtime.protocols.adapters", "averify_adapted" if asynchronous else "verify_adapted")
        )
        verb = "Verify a delivery" if key is not None else "Verify a delivery with verifier"
        summary = f"{verb} and decode its event."
        head = f"return {'await ' if asynchronous else ''}{module.local(runtime, call)}("
        arguments = (
            "_PLAN",
            "raw_body",
            "headers",
            "keys",
            *(() if key is not None else ("verifier=verifier",)),
            "now=now",
            "options=options",
        )
        returned = layout(Group(head, items(arguments), ")"), 4, 0, WIDTH)
        return f"{signature}\n{_docstring(summary, window, '    ')}\n    {returned}"

    @staticmethod
    def unsigned(module: TargetModule, event: str) -> str:
        """Return the decode function of a helper without a signature."""
        options = module.optional(module.local(_WEBHOOKS, "WebhookOptions"))
        parameters = ("raw_body: bytes", "*", f"options: {options} = None")
        signature = layout(Group("def decode_unverified(", items(parameters), f") -> {event}:"), 0, 0, WIDTH)
        text = (
            "Nothing verifies who sent the delivery, that its body is unchanged, or that it is not a replay, and no "
            "duplicate information is returned: treat the event as untrusted input. Of options, only max_body_bytes "
            "applies."
        )
        call = f"return {module.local(_EVENTS, 'decode_unsigned')}(_PLAN, raw_body, options=options)"
        summary = "Decode a delivery's event without authenticating it."
        return f"{signature}\n{_docstring(summary, text, '    ')}\n    {call}"


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
