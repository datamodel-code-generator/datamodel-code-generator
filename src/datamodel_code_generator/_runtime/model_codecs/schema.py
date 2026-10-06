"""Schema records and keyword groups used only by generation-time wire planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from .context import Direction  # noqa: TC001 - Public annotations support get_type_hints().
from .wire import WireValue  # noqa: TC001 - Public annotations support get_type_hints().

SCHEMA_VALUE_KEYWORDS: Final = frozenset({
    "additionalProperties",
    "contains",
    "contentSchema",
    "else",
    "if",
    "items",
    "not",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
})


SCHEMA_MAP_KEYWORDS: Final = frozenset({"$defs", "definitions", "dependentSchemas", "patternProperties", "properties"})


SCHEMA_ARRAY_KEYWORDS: Final = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaResource:
    """One normalized 2020-12 resource, with the pointers of the schema roots it contains."""

    uri: str
    contents: WireValue
    roots: tuple[str, ...] = ("",)


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaPatch:
    """Replace one bundled schema object's required keyword with its directional value."""

    uri: str
    pointer: str
    keyword: Literal["required", "dependentRequired"]
    value: WireValue


@dataclass(frozen=True, slots=True, kw_only=True)
class DirectionalView:
    """Relax required members excluded in one direction and assert its readOnly or writeOnly values."""

    direction: Direction
    patches: tuple[SchemaPatch, ...] = ()
    flagged: tuple[str, ...] = ()
