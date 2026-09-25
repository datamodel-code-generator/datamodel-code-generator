"""Conservative fingerprints of each planned operation, which a partial update compares with the previous run."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final

from typing_extensions import TypeIs

from datamodel_code_generator._api_manifest import canonical_bytes, sha256

if TYPE_CHECKING:
    from datamodel_code_generator._api_manifest import DocumentTable
    from datamodel_code_generator._fastapi.plan import OperationSpec
    from datamodel_code_generator._generation_contract import TypeUseBinding, TypeUseId
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.bindings import UseBinding
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue

_FIELDS: Final[dict[type, tuple[str, ...] | None]] = {}


class Fingerprints:
    """Digest plan values, projecting each record once however often the plan refers to it."""

    def __init__(self) -> None:
        """Start with no projected records; each projection keeps its record alive, so its identity stays unique."""
        self.records: dict[int, tuple[object, JSONValue]] = {}

    def digest(self, value: object) -> str:
        """Return the SHA-256 of a value's canonical JSON projection."""
        return sha256(canonical_bytes(self.projection(value)))

    def projection(self, value: object) -> JSONValue:
        """Project a plan value into JSON: records become objects of their fields, enums their values."""
        if value is None or isinstance(value, (str, int, float)):
            return value
        if _is_sequence(value):
            return [self.projection(item) for item in value]
        if (names := _names(type(value))) is not None:
            if (known := self.records.get(identity := id(value))) is None:
                known = self.records[identity] = (
                    value,
                    {name: self.projection(getattr(value, name)) for name in names},
                )
            return known[1]
        if isinstance(value, Enum):
            return self.projection(value.value)
        if not _is_mapping(value):
            raise AssertionError(type(value).__qualname__)
        return {str(key): self.projection(item) for key, item in value.items()}

    def plan(self, spec: OperationSpec, documents: DocumentTable, projections: list[JSONValue]) -> str:
        """Return the fingerprint of an operation's finalized route, names, arguments, decisions, and responses."""
        primary = spec.primary
        return self.digest({
            "operation": documents.operation(spec.contract.id),
            "method": spec.contract.method,
            "facts": spec.contract.facts,
            "route": spec.route,
            "python_name": spec.python_name,
            "group": spec.group,
            "mode": spec.mode,
            "registration_status": spec.registration_status,
            "arguments": [
                (
                    argument.name,
                    argument.kind,
                    argument.location,
                    argument.wire_name,
                    argument.required,
                    argument.native,
                )
                for argument in spec.arguments
            ],
            "decisions": projections,
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

    def codec(
        self,
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
        return self.digest(entries)


def _names(kind: type) -> tuple[str, ...] | None:
    if kind not in _FIELDS:
        _FIELDS[kind] = tuple(item.name for item in fields(kind)) if is_dataclass(kind) else None
    return _FIELDS[kind]


def _is_sequence(value: object) -> TypeIs[tuple[object, ...] | list[object]]:
    return isinstance(value, (tuple, list))


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)
