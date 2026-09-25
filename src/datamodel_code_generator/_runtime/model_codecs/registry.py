"""The offline schema registry that schema adapters read: one direction's bundled documents as independent copies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from typing_extensions import TypeIs

from .views import SchemaView
from .wire import WireValue, thaw_wire

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from referencing import Registry

    from .schema import SchemaBundle
    from .views import CodecSourceRef
    from .wire import JSONValue


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaSource:
    """Describe a bundled schema's source location, dialect, and schema as written, for its SchemaView."""

    schema_id: str
    source: CodecSourceRef
    dialect: str
    raw: WireValue


class BundleSchemaRegistry:
    """Serve one direction's bundled schemas, as its bundle validates them, without network or file access."""

    __slots__ = ("_bundle", "_views")

    def __init__(self, bundle: SchemaBundle, sources: Iterable[SchemaSource]) -> None:
        """Build each source's view from the directional bundle."""
        self._bundle = bundle
        self._views = {
            source.schema_id: SchemaView(
                schema_id=source.schema_id,
                source=source.source,
                dialect=source.dialect,
                raw=source.raw,
                normalized=bundle.normalized(source.schema_id),
            )
            for source in sources
        }

    @property
    def schema_ids(self) -> tuple[str, ...]:
        """Return every schema identifier the registry serves."""
        return tuple(self._views)

    def get(self, schema_id: str) -> SchemaView:
        """Return the view of one bundled schema, raising KeyError for unknown identifiers."""
        return self._views[schema_id]

    def as_referencing_registry(self) -> Registry[bool | Mapping[str, JSONValue]]:  # ty: ignore[invalid-type-form]
        """Return an immutable registry of independent copies of every bundled document."""
        from referencing import Registry  # noqa: PLC0415
        from referencing.jsonschema import DRAFT202012  # noqa: PLC0415

        registry: Registry[bool | Mapping[str, JSONValue]] = Registry()  # ty: ignore[invalid-type-form]
        return registry.with_resources(
            (uri, DRAFT202012.create_resource(contents))
            for uri, document in self._bundle.documents().items()
            if _is_root(contents := thaw_wire(document))
        ).crawl()


def _is_root(value: JSONValue) -> TypeIs[bool | dict[str, JSONValue]]:
    return isinstance(value, (bool, dict))
