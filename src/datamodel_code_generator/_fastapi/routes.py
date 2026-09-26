"""Plan route paths, internal path slots, groups, and operation names of a FastAPI server target."""

from __future__ import annotations

import keyword
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._fastapi.naming import file_stem_conflict, normalize
from datamodel_code_generator._generation_contract import LiteralScalar, LiteralSequence

if TYPE_CHECKING:
    from collections.abc import Iterable

    from datamodel_code_generator._generation_contract import OperationContract

_PLACEHOLDER: Final = re.compile(r"\{([^{}]*)\}")
_SLOT_PREFIX: Final = "dcg_p"
_BUILDER_NAMES: Final = frozenset({"OPERATIONS", "ROUTES", "LITERAL_ROUTES", "TEMPLATED_ROUTES"})


class RouteError(ValueError):
    """Report a path template FastAPI cannot register as declared."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PathSlot:
    """One placeholder the route path renames: its wire name, internal slot name, and occurrence index."""

    wire_name: str
    slot: str
    occurrence: int


@dataclass(frozen=True, slots=True, kw_only=True)
class RoutePath:
    """A route path with its wire placeholders in order and the slots renamed for Starlette."""

    path: str
    route_path: str
    placeholders: tuple[str, ...]
    slots: tuple[PathSlot, ...]

    @property
    def templated(self) -> bool:
        """Return whether the path has placeholders, which register after literal paths."""
        return bool(self.placeholders)

    def shape(self) -> str:
        """Return the path with every placeholder erased, which identifies routes FastAPI cannot tell apart."""
        return _PLACEHOLDER.sub("{}", self.path)


def placeholders(path: str) -> tuple[str, ...]:
    """Return a path template's placeholder names in order, rejecting braces FastAPI cannot route."""
    names = tuple(_PLACEHOLDER.findall(path))
    if _PLACEHOLDER.sub("", path).count("{") or _PLACEHOLDER.sub("", path).count("}"):
        msg = "The path has an unmatched brace"
        raise RouteError(msg)
    for name in names:
        if not name or ":" in name:
            msg = "A path placeholder is empty or uses converter syntax"
            raise RouteError(msg)
    return names


def route_path(path: str, taken: Iterable[str], *, repeatable: bool) -> RoutePath:
    """Rename placeholders Starlette cannot match by their wire names, keeping ordinary identifiers unchanged."""
    names = placeholders(path)
    if len(set(names)) != len(names) and not repeatable:
        msg = "OpenAPI 3.2 does not allow a placeholder twice in one path"
        raise RouteError(msg)
    reserved = {*names, *taken}
    counts: dict[str, int] = {}
    slots: list[PathSlot] = []
    index = 0
    parts: list[str] = []
    position = 0
    for match in _PLACEHOLDER.finditer(path):
        name = match.group(1)
        occurrence = counts.get(name, 0)
        counts[name] = occurrence + 1
        if _plain(name) and names.count(name) == 1 and name not in taken:
            slot = name
        else:
            while (slot := f"{_SLOT_PREFIX}{index}") in reserved:
                index += 1
            index += 1
            slots.append(PathSlot(wire_name=name, slot=slot, occurrence=occurrence))
        parts.extend((path[position : match.start()], f"{{{slot}}}"))
        position = match.end()
    parts.append(path[position:])
    return RoutePath(path=path, route_path="".join(parts), placeholders=names, slots=tuple(slots))


def _plain(name: str) -> bool:
    return name.isascii() and name.isidentifier() and not keyword.iskeyword(name)


def operation_name(operation: OperationContract) -> str:
    """Return an operation's default function name: its operationId, or its method and path."""
    facts = dict(operation.facts)
    source = operation_id.value if isinstance(operation_id := facts.get("operationId"), LiteralScalar) else None
    text = (
        source
        if isinstance(source, str) and operation.explicit_operation_id
        else f"{operation.method}_{operation.path}"
    )
    return normalize(text, empty="operation", digit="op_")


def tags(operation: OperationContract) -> tuple[str, ...]:
    """Return an operation's tags in declaration order."""
    if not isinstance(value := dict(operation.facts).get("tags"), LiteralSequence):
        return ()
    return tuple(item.value for item in value.items if isinstance(item, LiteralScalar) and isinstance(item.value, str))


def group_key(operation: OperationContract, *, single: bool) -> str:
    """Return the group of an operation: its first tag, untagged, or all for the single layout."""
    if single:
        return "all"
    return f"tag:{found[0]}" if (found := tags(operation)) else "untagged"


def group_stem(key: str) -> str:
    """Return a group's default name, its router file stem and service argument: its first tag, untagged, or service."""
    match key:
        case "all":
            return "service"
        case "untagged":
            return key
        case _:
            pass
    return normalize(key.removeprefix("tag:"), empty="router", digit="router_")


def stem_conflicts(stems: Iterable[str]) -> set[str]:
    """Return group names that repeat under casefolding, name reserved files, or take a name the builders define.

    Each name is a router file stem and the keyword that passes the group's service to a builder.
    """
    names = list(stems)
    folded = Counter(name.casefold() for name in names)
    return {name for name in names if folded[name.casefold()] > 1 or file_stem_conflict(name) or name in _BUILDER_NAMES}
