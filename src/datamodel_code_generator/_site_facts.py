"""The site of a located schema object of a loaded API document: its keywords as written, read once.

The client and server bindings read the default and annotations of a parameter's schema by location while the walk
acquires it (``_target_binding.py:_Reader``), through ``openapi_scope.py:ApiOpenAPIParser._site_facts``, which builds
the site of a location once from the loaded document, with the reference it writes resolved as the parser resolves it.
No pass writes a site, and no reuse, collapse or merge of models moves one.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, final

from datamodel_code_generator._records import record

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator.reference import SchemaLocation

__all__ = [
    "ANNOTATION_KEYWORDS",
    "EMPTY_SITE",
    "Projection",
    "SiteFacts",
    "TypeUseRole",
    "site_facts_of",
]

Projection: TypeAlias = Literal["value", "item_stream_array"]
"""How the walk reads a declared schema: as the value it declares, or as the array of the items of a stream."""
TypeUseRole: TypeAlias = Literal[
    "schema",
    "parameter",
    "request_body",
    "response_body",
    "response_header",
    "request_encoding_header",
    "response_encoding_header",
]
"""Where the walk finds a declared schema: among the schemas, or in a parameter, a body or a header.

A header is a response's or that of the encoding of a request or a response body.
"""

ANNOTATION_KEYWORDS: Final = ("title", "description", "deprecated", "examples", "default")
"""The documentation keywords of a schema, and its default."""
_IDENTIFIERS: Final = frozenset({"$id", "$anchor", "$schema"})


@final
@record(eq=False)
class SiteFacts:
    """A located value of a loaded document as the target bindings read it as a schema.

    Its keywords as written, read once, and where its reference leads as the parser resolves it; any value but a
    non-empty mapping reads as the empty schema (``EMPTY_SITE``).
    """

    keys: frozenset[str]
    """Every key the mapping writes."""
    ref: SchemaLocation | None
    """Where a string `$ref` leads, None without one or where it leads to no loaded document."""
    annotations: Mapping[str, YamlValue]
    """Each documentation keyword and the default it writes (``ANNOTATION_KEYWORDS``), as written."""

    @property
    def bare(self) -> bool:
        """Return whether it writes nothing but a `$ref` and identification keywords."""
        return self.keys - _IDENTIFIERS == {"$ref"}


EMPTY_SITE: Final = SiteFacts(frozenset(), None, MappingProxyType({}))
"""The site of any value but a non-empty mapping: a schema that writes nothing."""


def site_facts_of(
    value: YamlValue, location: SchemaLocation, follow: Callable[[str, str], SchemaLocation | None]
) -> SiteFacts:
    """Read the site of the value at a location; ``follow`` resolves a reference written in a document.

    ``follow(document, ref)`` returns where a reference that ``document`` holds leads, None for no loaded schema.
    """
    if not isinstance(value, dict) or not value:
        return EMPTY_SITE
    return SiteFacts(
        frozenset(value),
        follow(location.document, ref) if isinstance(ref := value.get("$ref"), str) else None,
        {keyword: value[keyword] for keyword in ANNOTATION_KEYWORDS if keyword in value},
    )
