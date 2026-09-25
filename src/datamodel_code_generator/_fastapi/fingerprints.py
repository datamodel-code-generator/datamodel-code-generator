"""Conservative fingerprints of each planned operation, which a partial update compares with the previous run."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import TYPE_CHECKING

from typing_extensions import TypeIs

from datamodel_code_generator._api_manifest import canonical_bytes, sha256

if TYPE_CHECKING:
    from datamodel_code_generator._api_manifest import DocumentTable
    from datamodel_code_generator._fastapi.plan import OperationSpec
    from datamodel_code_generator._generation_contract import TypeUseBinding, TypeUseId
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.bindings import UseBinding
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue


def digest(value: object) -> str:
    """Return the SHA-256 of a value's canonical JSON projection."""
    return sha256(canonical_bytes(projection(value)))


def projection(value: object) -> JSONValue:
    """Project a frozen plan value into JSON: records become objects of their fields, enums their values."""
    match value:
        case None | bool() | int() | float() | str():
            return value
        case Enum():
            return projection(value.value)
        case _ if _is_sequence(value):
            return [projection(item) for item in value]
        case _ if _is_mapping(value):
            return {str(key): projection(item) for key, item in value.items()}
        case _:
            pass
    if not is_dataclass(value) or isinstance(value, type):
        raise AssertionError(type(value).__qualname__)
    return {item.name: projection(getattr(value, item.name)) for item in fields(value)}


def _is_sequence(value: object) -> TypeIs[tuple[object, ...] | list[object]]:
    return isinstance(value, (tuple, list))


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def plan_digest(spec: OperationSpec, documents: DocumentTable) -> str:
    """Return the fingerprint of an operation's finalized route, names, arguments, decisions, and responses."""
    primary = spec.primary
    return digest({
        "operation": documents.operation(spec.contract.id),
        "method": spec.contract.method,
        "facts": spec.contract.facts,
        "route": spec.route,
        "python_name": spec.python_name,
        "group": spec.group,
        "mode": spec.mode,
        "registration_status": spec.registration_status,
        "arguments": [
            (argument.name, argument.kind, argument.location, argument.wire_name, argument.required, argument.native)
            for argument in spec.arguments
        ],
        "decisions": [
            (
                decision.site,
                decision.transport,
                decision.reason,
                [documents.use(use) for use in decision.uses],
                None if decision.source is None else documents.source(decision.source),
            )
            for decision in spec.decisions()
        ],
        "primary": None
        if primary is None
        else (primary.status, None if primary.media is None else primary.media.media_type),
        "security": None if spec.security is None else spec.security.requirements,
        "body": None
        if spec.body is None
        else (spec.body.required, [(media.media_type, media.kind) for media in spec.body.media]),
        "responses": [
            (
                response.status,
                [(media.media_type, media.kind) for media in response.media],
                [(header.name, header.required) for header in response.headers],
            )
            for response in spec.responses
        ],
    })


def codec_digest(
    uses: tuple[TypeUseId, ...],
    bindings: Mapping[TypeUseId, UseBinding],
    type_uses: Mapping[TypeUseId, TypeUseBinding],
    wire: WirePlan,
) -> str:
    """Return the fingerprint of an operation's codec bindings and the schemas they validate against."""
    entries: list[tuple[UseBinding, object]] = []
    for use in uses:
        if (binding := bindings.get(use)) is None:
            continue
        schema = type_uses[use].schema
        entries.append((binding, None if schema is None else wire.schema(schema)[1]))
    return digest(entries)
