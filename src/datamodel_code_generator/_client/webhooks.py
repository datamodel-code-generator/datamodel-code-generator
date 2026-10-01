"""Plan and render the webhook verification helpers of a client target, at `pkg.webhooks.<name>`.

A helper's event schema is bound through the JSON request body of a webhook or callback operation that uses it, since
only directional uses have codecs; the event is then received as a server receives that request.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from textwrap import fill
from typing import TYPE_CHECKING, Any, Final

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client._compiled_templates import types as types_template
from datamodel_code_generator._client.naming import folded
from datamodel_code_generator._client.render import WIDTH, Module
from datamodel_code_generator._generation_contract import BindingCaptureError, OperationId, SourceLocation
from datamodel_code_generator._openapi_wire_plan import plan_wire
from datamodel_code_generator._python_layout import Chain, Group, layout
from datamodel_code_generator._runtime.model_codecs.codec import needs_schema
from datamodel_code_generator._runtime.model_codecs.media import media_kind
from datamodel_code_generator._target_render import items

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import DiagnosticStage
    from datamodel_code_generator._client.config import ClientGenerationConfig
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper
    from datamodel_code_generator._generation_contract import TypeUseBinding, TypeUseId
    from datamodel_code_generator._openapi_codec_plan import CodecPlan
    from datamodel_code_generator._openapi_codec_render import UseAccessors

__all__ = ("WebhookSpec", "plan_webhooks", "webhook_files", "webhook_uses")

_SENDERS: Final = frozenset({"webhook", "callback"})
_PARTS: Final = {"raw-body": "RAW_BODY", "timestamp": "TIMESTAMP", "delivery-id": "DELIVERY_ID"}
_ALGORITHMS: Final = {"hmac-sha256": ("HMAC_SHA256", "HMAC-SHA256"), "hmac-sha512": ("HMAC_SHA512", "HMAC-SHA512")}
_CHUNK: Final = 48
_PACKAGE: Final = '"""Webhook verification helpers of this package, by their dotted names."""\n'
_KEYS: Final = '''"""The key types of this package's webhook signature profiles."""

from .._runtime.protocols.webhook_keys import HmacKey

__all__ = ["HmacKey"]
'''


@dataclass(frozen=True, slots=True, kw_only=True)
class WebhookSpec:
    """A webhook helper ready to render: its event's request-body use, event schema, and decoding mode."""

    helper: Helper
    use: TypeUseBinding
    event_schema: Mapping[str, str]
    validate: bool


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
    """Return each enabled webhook helper with its event's request-body use, and each such helper's problems.

    The use is the first JSON request body of a webhook or callback operation whose schema, through references, is the
    event schema; its decoding mode is settled once the use is bound.
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
        (use, schema)
        for use in request.batch.type_uses
        if use.id.role == "request_body"
        and isinstance(owner := use.id.owner, OperationId)
        and owner.kind in _SENDERS
        and (schema := use.schema) is not None
        and media_kind(use.id.media or "") == "json"
    ]
    wire = plan_wire(
        request.batch, request.lease, [use.id for use, _ in candidates], documents=request.documents.pointers
    )
    events: list[WebhookSpec] = []
    for helper in helpers:
        at, reference = f"{helper.at}.event_schema", helper.tree["event_schema"]
        if isinstance(reference, dict):
            message = f"The webhook helper {helper.name!r} with an event mapping is not supported yet"
            problems[helper.name].append(_problem("E_CLIENT_UNSUPPORTED", "target", at, message))
            continue
        location = SourceLocation(documents[protocols.documents[reference]], reference.pointer, "schema")
        try:
            request.lease.borrow(location)
        except BindingCaptureError:
            message = f"The event_schema {reference.pointer!r} of {helper.name!r} does not exist in its document"
            problems[helper.name].append(_problem("E_CONFIG_VALUE", "config", at, message))
            continue
        resolved = wire.schema(location)[0]
        if (use := next((item for item, schema in candidates if wire.schema(schema)[0] == resolved), None)) is None:
            message = (
                f"The event_schema {reference.pointer!r} of {helper.name!r} is not the schema of a JSON request body "
                "of any webhook or callback operation"
            )
            problems[helper.name].append(_problem("E_CONFIG_VALUE", "config", at, message))
            continue
        schema = {"document": protocols.documents[reference], "pointer": reference.pointer}
        events.append(WebhookSpec(helper=helper, use=use, event_schema=schema, validate=False))
    return tuple(events), problems


def plan_webhooks(
    events: tuple[WebhookSpec, ...],
    codecs: CodecPlan,
    config: ClientGenerationConfig,
    problems: dict[str, list[Diagnostic]],
) -> tuple[WebhookSpec, ...]:
    """Plan every webhook helper whose event has a native codec and no other problem, adding the others' problems.

    An event whose union members differ only by their schemas is always validated against its schema.
    """
    bindings = dict(codecs.bindings)
    specs: list[WebhookSpec] = []
    for event in events:
        helper, binding = event.helper, bindings[event.use.id]
        if binding.projection_mode != "native" or binding.converter_strategy == "registered_adapter":
            message = (
                f"The webhook helper {helper.name!r} decodes an envelope-projected event, which is not supported yet"
            )
            problems[helper.name].append(
                _problem("E_CLIENT_UNSUPPORTED", "target", f"{helper.at}.event_schema", message)
            )
        if not problems[helper.name]:
            validate = config.validation.response == "schema" or needs_schema(binding)
            specs.append(replace(event, validate=validate))
    return tuple(specs)


def _call(head: str, entries: list[tuple[str, Any]]) -> Group:
    return Group(f"{head}(", tuple(entries), ")")


def _fact(module: Module, kind: str, settings: Mapping[str, Any], constraint: Mapping[str, Any]) -> Group:
    """Return the runtime record of a signed header fact: its lowercase name, unit, and constraint.

    Validation gives every signed fact one constraint.
    """
    entries: list[tuple[str, Any]] = [("header=", repr(settings["header"].lower()))]
    if "unit" in settings:
        entries.append(("unit=", repr(settings["unit"])))
    entries.extend(
        ("ascii_bytes=", _bytes(value.encode("ascii"))) if name == "ascii_bytes" else ("fixed_bytes=", repr(value))
        for name, value in constraint.items()
    )
    return _call(module.local("_runtime.protocols.signatures", kind), entries)


def _bytes(value: bytes) -> str | Chain:
    """Return a bytes literal, split into concatenated pieces when it is too long for one line."""
    if len(value) <= _CHUNK:
        return repr(value)
    return Chain("+", tuple(repr(value[start : start + _CHUNK]) for start in range(0, len(value), _CHUNK)))


def _docstring(summary: str, text: str, indent: str) -> str:
    """Return a docstring's summary line, then text wrapped to the generated width."""
    body = fill(text, WIDTH, initial_indent=indent, subsequent_indent=indent)
    return f'{indent}"""{summary}\n\n{body}\n{indent}"""'


class _Webhooks:
    """Render the webhook package of a client: its namespaces, key types, and one module per helper."""

    def __init__(
        self,
        specs: tuple[WebhookSpec, ...],
        symbols: Mapping[int, str],
        fingerprints: Mapping[str, str],
        accessors: Mapping[TypeUseId, UseAccessors],
    ) -> None:
        self.specs = specs
        self.symbols = symbols
        self.fingerprints = fingerprints
        self.accessors = accessors

    def files(self) -> tuple[tuple[PurePosixPath, str], ...]:
        """Return the package's webhook files, nothing without helpers."""
        if not self.specs:
            return ()
        packages = {
            PurePosixPath("webhooks", *spec.helper.name.split(".")[:index])
            for spec in self.specs
            for index in range(1, spec.helper.name.count(".") + 1)
        }
        return (
            (PurePosixPath("webhooks", "__init__.py"), _PACKAGE),
            (PurePosixPath("webhooks", "keys.py"), _KEYS),
            *(
                (package / "__init__.py", f'"""The {".".join(package.parts[1:])} webhook verification helpers."""\n')
                for package in sorted(packages)
            ),
            *(
                (PurePosixPath("webhooks", *spec.helper.name.split(".")).with_suffix(".py"), self.module(spec))
                for spec in self.specs
            ),
        )

    @staticmethod
    def profile(module: Module, signature: Mapping[str, Any]) -> Group:
        """Return the runtime signature profile of a helper's normalized signature settings."""
        signatures = "_runtime.protocols.signatures"
        constraints = signature["field_constraints"]
        separator, key_id = signature["separator"], signature["key_id"]
        timestamp, delivery = signature["timestamp"], signature["delivery_id"]
        parts = [
            module.local(signatures, _PARTS[part]) if isinstance(part, str) else repr(part["literal"].encode("ascii"))
            for part in signature["signed_parts"]
        ]
        entries: list[tuple[str, Any]] = [
            ("algorithm=", module.local(signatures, _ALGORITHMS[signature["kind"]][0])),
            ("header=", repr(signature["header"].lower())),
            ("encoding=", repr(signature["encoding"])),
            ("prefix=", repr(signature["prefix"])),
            ("separator=", repr(None if separator == "none" else separator)),
            ("key_id=", repr(None if key_id == "none" else key_id["header"].lower())),
            (
                "timestamp=",
                "None" if timestamp == "none" else _fact(module, "TimestampField", timestamp, constraints["timestamp"]),
            ),
            (
                "delivery_id=",
                "None" if delivery == "none" else _fact(module, "FactField", delivery, constraints["delivery-id"]),
            ),
            ("parts=", Group("(", items(parts), ")", ",")),
        ]
        return _call(module.local(signatures, "SignatureProfile"), entries)

    def plan(self, module: Module, spec: WebhookSpec) -> tuple[str, str, str]:
        """Return the spellings of a helper's event and key types, and its plan's definition."""
        helper, use = spec.helper, spec.use
        verification = "_runtime.protocols.verification"
        assert use.type is not None
        event = module.types.static(use.type)
        key = module.local("_runtime.protocols.webhook_keys", "HmacKey")
        accessor = self.accessors[use.id]
        bindings = module.local("_generated", "model_bindings")
        decoder = _call(
            module.local(verification, "EventDecoder"),
            [
                ("", f"{bindings}.{accessor.codec}"),
                ("", f"{bindings}.{accessor.context}"),
                ("validate=", repr(spec.validate)),
            ],
        )
        plan = module.local(verification, "WebhookPlan")
        value = _call(
            plan,
            [
                ("helper_id=", repr(helper.name)),
                ("fingerprint=", repr(self.fingerprints[helper.name])),
                ("signature=", self.profile(module, helper.tree["signature"])),
                ("event=", decoder),
                ("duplicates=", repr(helper.tree["duplicates"])),
            ],
        )
        head = f"_PLAN: {module.name('typing', 'Final')}[{plan}[{event}, {key}]] = "
        return event, key, head + layout(value, 0, len(head), WIDTH)

    def module(self, spec: WebhookSpec) -> str:
        """Return a helper's module: its plan and its sync and asyncio verify functions."""
        helper = spec.helper
        signature = helper.tree["signature"]
        module = Module({"_PLAN", "verify", "verify_async"}, self.symbols, level=helper.name.count(".") + 2)
        event, key, plan = self.plan(module, spec)
        algorithm = _ALGORITHMS[signature["kind"]][1]
        window = (
            "Deliveries outside the timestamp window are rejected. The window closes at the timestamp plus "
            "past_tolerance, while a replay store keeps a claim until the timestamp plus past_tolerance and "
            "future_tolerance."
            if signature["timestamp"] != "none"
            else "This webhook has no timestamp, so replay protection has no time window: a replay store keeps a "
            "claim for replay_ttl after now."
        ) + (
            " Duplicates are detected only until the claim expires. The claim is made after decoding, before your code "
            "processes the event: if processing then fails, a retried delivery is a duplicate, or rejected under "
            "duplicates: reject, until the claim expires, so make processing durable or idempotent before "
            "acknowledging, or use a store whose claims you can release. Without a replay store, duplicate=False does "
            "not mean the delivery is new."
        )
        functions = [
            self.function(module, event, key, window, asynchronous=asynchronous) for asynchronous in (False, True)
        ]
        docstring = _docstring(
            "Verify the signed deliveries of one webhook helper, then decode their events.",
            f"The helper is {helper.name}, whose deliveries are signed with {algorithm}.",
            "",
        )
        return types_template.render(
            docstring=docstring.removeprefix('"""').removesuffix('"""'),
            imports=module.imports(),
            sections=['__all__ = ["verify", "verify_async"]', plan, *functions],
        )

    @staticmethod
    def function(module: Module, event: str, key: str, window: str, *, asynchronous: bool) -> str:
        """Return the sync or asyncio verify function of a helper."""
        webhooks, verification = "_runtime.protocols.webhooks", "_runtime.protocols.verification"
        store = module.local(webhooks, "AsyncReplayStore" if asynchronous else "ReplayStore")
        parameters = (
            "raw_body: bytes",
            f"headers: {module.name('collections.abc', 'Sequence')}[tuple[str, str]]",
            f"keys: {module.local(webhooks, 'KeySet')}[{key}]",
            "*",
            f"now: {module.name('datetime', 'datetime')}",
            f"replay_store: {store} | None = None",
            f"options: {module.local(webhooks, 'WebhookOptions')} | None = None",
        )
        name = "verify_async" if asynchronous else "verify"
        signature = layout(
            Group(
                f"{'async ' if asynchronous else ''}def {name}(",
                items(parameters),
                f") -> {module.local(webhooks, 'VerifiedWebhook')}[{event}]:",
            ),
            0,
            0,
            WIDTH,
        )
        call = module.local(verification, "averify_webhook" if asynchronous else "verify_webhook")
        summary = (
            "Verify a delivery and decode its event, claiming it in an asyncio store."
            if asynchronous
            else "Verify a delivery and decode its event, claiming it in a store."
        )
        head = f"return {'await ' if asynchronous else ''}{call}("
        arguments = ("_PLAN", "raw_body", "headers", "keys", "now=now", "replay_store=replay_store", "options=options")
        returned = layout(Group(head, items(arguments), ")"), 4, 0, WIDTH)
        return f"{signature}\n{_docstring(summary, window, '    ')}\n    {returned}"


def webhook_files(
    specs: tuple[WebhookSpec, ...],
    symbols: Mapping[int, str],
    fingerprints: Mapping[str, str],
    accessors: Mapping[TypeUseId, UseAccessors],
) -> tuple[tuple[PurePosixPath, str], ...]:
    """Return the path and text of every webhook file of a package, nothing without webhook helpers."""
    return _Webhooks(specs, symbols, fingerprints, accessors).files()
