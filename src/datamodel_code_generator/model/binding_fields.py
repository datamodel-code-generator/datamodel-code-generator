"""Resolve final field ownership from immutable model and field identities."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from datamodel_code_generator._generation_contract import BindingCaptureError


@dataclass(frozen=True, slots=True)
class DeclaredField:
    """Keep a declaring slot and its real wire key, including an empty key."""

    slot: FieldSlot
    wire_name: str


@dataclass(frozen=True, slots=True)
class FieldOwner:
    """Freeze the exact final base and own-field order without retaining models."""

    symbol: SymbolId
    reference_path: str
    bases: tuple[SymbolId, ...]
    fields: tuple[DeclaredField, ...]
    unknown_bases: bool = False


@dataclass(frozen=True, slots=True)
class FieldOverride:
    """Retain a displaced declaring slot instead of silently losing its origin."""

    key: str
    original: FieldSlot
    replacement: FieldSlot


@dataclass(frozen=True, slots=True)
class FieldOwnershipView:
    """Associate one consumer with the actual declarations supplying its fields."""

    consumer: SymbolId
    fields: tuple[DeclaredField, ...]
    overrides: tuple[FieldOverride, ...]

    def functional_declarations(
        self, model_name: str, declarations: Mapping[FieldSlot, ExpectedFieldDeclaration]
    ) -> tuple[ExpectedFieldDeclaration, ...]:
        """Associate expanded entries with their declaring slots and this consumer."""
        entries: list[ExpectedFieldDeclaration] = []
        for ordinal, field in enumerate(self.fields):
            if (declaration := declarations.get(field.slot)) is None:
                msg = "A functional TypedDict entry has no final declaring field"
                raise BindingCaptureError(msg)
            if declaration.backend != "typeddict" or declaration.excluded_by_tag:
                msg = "A functional TypedDict entry has incompatible declaring field semantics"
                raise BindingCaptureError(msg)
            entries.append(
                replace(
                    declaration,
                    consumer=self.consumer,
                    model_name=model_name,
                    form="typeddict_entry",
                    entry_key=field.wire_name,
                    entry_ordinal=ordinal,
                )
            )
        return tuple(entries)


_UNRESOLVED: Final = "BND_FIELD_UNRESOLVED"
_AMBIGUOUS: Final = "BND_FIELD_AMBIGUOUS"
_CUSTOM: Final = "BND_CUSTOM_BINDING_REQUIRED"

FieldOwnershipReason: TypeAlias = Literal["BND_FIELD_UNRESOLVED", "BND_FIELD_AMBIGUOUS", "BND_CUSTOM_BINDING_REQUIRED"]


@dataclass(frozen=True, slots=True)
class FieldOwnershipProjection:
    """Carry unresolved ownership as a finite diagnostic, never an invented field."""

    value: FieldOwnershipView | None
    reason: FieldOwnershipReason | None = None


class _OwnershipError(Exception):
    def __init__(self, reason: FieldOwnershipReason) -> None:
        self.reason: FieldOwnershipReason = reason
        super().__init__(reason)


class FieldOwnershipIndex:
    """Build each requested C3 or functional TypedDict view once from value records."""

    def __init__(self, owners: Mapping[SymbolId, FieldOwner]) -> None:
        """Borrow only the caller's final value inventory; cache requested views."""
        self._owners = owners
        self._mro: dict[SymbolId, tuple[SymbolId, ...]] = {}
        self._views: dict[tuple[SymbolId, bool], FieldOwnershipProjection] = {}

    def project(self, symbol: SymbolId, *, functional_typeddict: bool = False) -> FieldOwnershipProjection:
        """Return the same immutable view for repeated requests within this inventory."""
        key = symbol, functional_typeddict
        if (known := self._views.get(key)) is not None:
            return known
        try:
            projected = FieldOwnershipProjection(self._project(symbol, functional_typeddict=functional_typeddict))
        except _OwnershipError as cause:
            projected = FieldOwnershipProjection(None, cause.reason)
        self._views[key] = projected
        return projected

    def _project(self, symbol: SymbolId, *, functional_typeddict: bool) -> FieldOwnershipView:
        order = self._parent_first(symbol) if functional_typeddict else reversed(self._linearize(symbol))
        fields: dict[str, DeclaredField] = {}
        overrides: list[FieldOverride] = []
        for owner_id in order:
            own_names: set[str] = set()
            for field in self._owner(owner_id).fields:
                field_key = field.wire_name if functional_typeddict else field.slot.name
                if field_key in own_names:
                    raise _OwnershipError(_AMBIGUOUS)
                own_names.add(field_key)
                if (previous := fields.get(field_key)) is not None and previous.slot != field.slot:
                    overrides.append(FieldOverride(field_key, previous.slot, field.slot))
                fields[field_key] = field
        return FieldOwnershipView(symbol, tuple(fields.values()), tuple(overrides))

    def _owner(self, symbol: SymbolId) -> FieldOwner:
        if (owner := self._owners.get(symbol)) is None:
            raise _OwnershipError(_UNRESOLVED)
        if owner.unknown_bases:
            raise _OwnershipError(_CUSTOM)
        return owner

    def _linearize(self, symbol: SymbolId) -> tuple[SymbolId, ...]:
        pending = [(symbol, False)]
        active: set[SymbolId] = set()
        while pending:
            current, leaving = pending.pop()
            if current in self._mro:
                continue
            owner = self._owner(current)
            if leaving:
                active.remove(current)
                inherited = (
                    self._mro[owner.bases[0]]
                    if len(owner.bases) == 1
                    else self._merge((*[self._mro[base] for base in owner.bases], owner.bases))
                )
                self._mro[current] = (current, *inherited)
                continue
            if current in active:
                raise _OwnershipError(_AMBIGUOUS)
            active.add(current)
            pending.append((current, True))
            pending.extend((base, False) for base in reversed(owner.bases))
        return self._mro[symbol]

    @staticmethod
    def _merge(sequences: tuple[tuple[SymbolId, ...], ...]) -> tuple[SymbolId, ...]:
        """Apply C3 head selection while retaining the existing base declaration order."""
        remaining = [(sequence, 0) for sequence in sequences if sequence]
        tails = Counter(symbol for sequence in sequences for index, symbol in enumerate(sequence) if index)
        result: list[SymbolId] = []
        while remaining:
            candidate = next((sequence[offset] for sequence, offset in remaining if not tails[sequence[offset]]), None)
            if candidate is None:
                raise _OwnershipError(_AMBIGUOUS)
            result.append(candidate)
            updated: list[tuple[tuple[SymbolId, ...], int]] = []
            for sequence, offset in remaining:
                next_offset = offset
                if sequence[offset] == candidate:
                    next_offset += 1
                    if next_offset == len(sequence):
                        continue
                    tails[sequence[next_offset]] -= 1
                updated.append((sequence, next_offset))
            remaining = updated
        return tuple(result)

    def _parent_first(self, symbol: SymbolId) -> tuple[SymbolId, ...]:
        """Mirror functional TypedDict's visited suppression by actual Reference.path."""
        visited: set[str] = set()
        order: list[SymbolId] = []
        pending = [(symbol, False)]
        while pending:
            current, leaving = pending.pop()
            owner = self._owner(current)
            if leaving:
                order.append(current)
                continue
            if owner.reference_path in visited:
                continue
            visited.add(owner.reference_path)
            pending.append((current, True))
            pending.extend((base, False) for base in reversed(owner.bases))
        return tuple(order)


if TYPE_CHECKING:
    from collections.abc import Mapping

    from datamodel_code_generator._generation_contract import FieldSlot, SymbolId
    from datamodel_code_generator.model.binding import ExpectedFieldDeclaration
