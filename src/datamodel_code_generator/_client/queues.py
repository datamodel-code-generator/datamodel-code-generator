"""Plan the queue helpers of a client target and the checks each queued operation must pass.

An operation may be queued only when sending it again is safe: it is side-effect free, or its idempotency header carries
a stable key the server deduplicates. Its request is saved as JSON wire values, so other request media are refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._client.naming import HELPER_ARGUMENTS
from datamodel_code_generator._client.pagination import _label, _problem

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from datamodel_code_generator._api_types import Diagnostic
    from datamodel_code_generator._client.plan import ClientPlan, OperationSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper, Link

_SAFE_METHODS: Final = frozenset({"get", "head", "options"})


@dataclass(frozen=True, slots=True, kw_only=True)
class QueuedSpec:
    """One operation a queue helper may enqueue: its alias, its operation, and its key's deduplication period."""

    alias: str
    operation: OperationSpec
    dedupe_ttl: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class QueueSpec:
    """A queue helper ready to render: its operations in declaration order."""

    helper: Helper
    operations: tuple[QueuedSpec, ...]


def _checks(helper: Helper, alias: str, link: Link, spec: OperationSpec, *, first: bool) -> Iterator[Diagnostic]:
    """Refuse a queued operation that cannot be saved or sent again safely, or whose arguments collide."""
    queued, at, label = helper.tree["operations"][alias], link.at.rpartition(".")[0], _label(spec)
    names = (*(parameter.python_name for parameter in spec.parameters), *spec.field_names)
    if not first and (taken := sorted(HELPER_ARGUMENTS.intersection(names))):
        message = (
            f"The queue helper {helper.name!r} reserves the argument{'s' if len(taken) > 1 else ''} "
            f"{', '.join(map(repr, taken))} of {label}; rename them with parameter_names or body_field_names"
        )
        yield _problem("E_NAME_COLLISION", "target", link.at, message, spec)
    if (body := spec.body) is not None and any(media.kind != "json" for media in body.media):
        message = (
            f"The queued operation {alias!r} of {helper.name!r} sends a request body of {label} other than JSON, "
            "which needs a blob store, not supported yet"
        )
        yield _problem("E_CLIENT_UNSUPPORTED", "target", link.at, message, spec)
    if not queued["side_effects"] and spec.contract.method not in _SAFE_METHODS:
        message = (
            f"The queued operation {alias!r} of {helper.name!r} declares {label} free of side effects, which only "
            "GET, HEAD, and OPTIONS are; declare side_effects: true with a key_binding"
        )
        yield _problem("E_CONFIG_VALUE", "config", f"{at}.side_effects", message, spec)
    if (target := queued.get("key_binding")) is None:
        return
    idempotency = spec.idempotency
    if idempotency is None or not idempotency.replay_safe_with_key:
        message = (
            f"The queued operation {alias!r} of {helper.name!r} sends {label} again with its key, which needs the "
            "operation's runtime idempotency with replay_safe_with_key: true"
        )
        yield _problem("E_CONFIG_VALUE", "config", f"{at}.key_binding", message, spec)
        return
    if target["in"] != "header" or target["name"].lower() != idempotency.header_name.lower():
        message = (
            f"The key_binding of {alias!r} in {helper.name!r} must be the idempotency header "
            f"{idempotency.header_name!r} of {label}"
        )
        yield _problem("E_CONFIG_VALUE", "config", f"{at}.key_binding", message, spec)
    if queued["dedupe_ttl"] > idempotency.retention_seconds:
        message = (
            f"The dedupe_ttl of {alias!r} in {helper.name!r} exceeds the {idempotency.retention_seconds:g} seconds "
            f"{label} retains its idempotency keys"
        )
        yield _problem("E_CONFIG_VALUE", "config", f"{at}.dedupe_ttl", message, spec)


def plan_queues(
    protocols: Protocols | None, plan: ClientPlan
) -> tuple[tuple[QueueSpec, ...], dict[str, list[Diagnostic]]]:
    """Plan every enabled queue helper, returning the planned ones and each checked helper's problems."""
    if protocols is None:
        return (), {}
    operations: Mapping[object, OperationSpec] = {spec.contract.id: spec for spec in plan.operations}
    specs: list[QueueSpec] = []
    problems: dict[str, list[Diagnostic]] = {}
    for helper in protocols.helpers:
        if not helper.enabled or helper.kind != "queue":
            continue
        queued: list[QueuedSpec] = []
        found = problems[helper.name] = []
        for index, (alias, link) in enumerate(zip(helper.tree["operations"], helper.links, strict=True)):
            spec = operations[protocols.operations[link.ref].id]
            found.extend(_checks(helper, alias, link, spec, first=not index))
            queued.append(
                QueuedSpec(alias=alias, operation=spec, dedupe_ttl=helper.tree["operations"][alias].get("dedupe_ttl"))
            )
        if not found:
            specs.append(QueueSpec(helper=helper, operations=tuple(queued)))
    return tuple(specs), problems
