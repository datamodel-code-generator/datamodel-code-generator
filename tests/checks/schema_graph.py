"""``SCHEMA_GRAPH``: no reader writes a loaded document, a value a document record keeps for a pointer or a schema site.

The wrapper of ``prepare_schema_resources`` keeps the fingerprint of each document as the loader read it, when it is
first prepared, and the check compares the documents with them at the end of L1: the parser never writes a loaded
document. The wrapper of ``pointer_value`` reads a value the record of a document keeps for a pointer anew from its tree
at every ask and compares the two. That no reader writes an object ``SchemaGraphBuilder.schema``, ``pointer_value``,
``prepare_schema_resources`` or ``ApiOpenAPIParser._site_facts`` returns is heki's ``MUT202`` (``readonly-returns`` of
``scripts/build_heki_contracts.py``); a write reached through a parameter or an attribute, which heki does not follow
to those functions, is this check's.

``ApiOpenAPIParser._site_facts`` reads the site of a location once, for the target bindings. Its wrapper reads a kept
site anew from the documents at every read and compares the two, and the wrapper of ``site_facts_of`` reports a string
`$ref` that leads to no loaded schema when a site is read: a site read before the document its reference names loads.
"""

from __future__ import annotations

from dataclasses import fields
from functools import wraps
from typing import TYPE_CHECKING, Any, Final

from tests.checks.areas import Area, mode

if TYPE_CHECKING:
    from collections.abc import Callable

    from datamodel_code_generator.parser._schema_graph import SchemaGraphBuilder

_LOADED: Final = "loaded documents"
"""The documents each schema graph prepared as the loader read them, with their names and fingerprints, by identity."""


def _fingerprint(value: object, active: frozenset[int] = frozenset()) -> object:
    """Return a comparable record of what a value of a document holds, the values its containers hold included.

    A container that is its own ancestor is recorded by its type.
    """
    if (value_id := id(value)) in active:
        return type(value)
    active |= {value_id}
    match value:
        case dict():
            return dict, tuple((key, _fingerprint(item, active)) for key, item in value.items())
        case list() | tuple() | set() | frozenset():
            items = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else value
            return type(value), tuple(_fingerprint(item, active) for item in items)
    return type(value), value


def prepare_schema_resources(original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``prepare_schema_resources``: keep the fingerprint of a document the loader read, before it is prepared.

    A document already kept is not taken again; a prepared tree given again is no loaded document.
    """
    from datamodel_code_generator.parser._schema_documents import document_key, schema_resource_document

    @wraps(original)
    def prepare_schema_resources(parser: Any, raw: Any, path_parts: list[str]) -> Any:
        if (graph_mode := mode(Area.SCHEMA_GRAPH)) is None:
            return original(parser, raw, path_parts)
        kept = graph_mode.state.setdefault((_LOADED, id(parser._graph)), {})
        recorded = None if id(raw) in kept else _fingerprint(raw)
        prepared = original(parser, raw, path_parts)
        if recorded is not None and parser._graph.known(schema_resource_document(parser, path_parts)).loaded is raw:
            kept[id(raw)] = document_key(parser, path_parts), raw, recorded
        return prepared

    return prepare_schema_resources


def pointer_value(original: Callable[..., object]) -> Callable[..., object]:
    """Wrap ``SchemaGraphBuilder.pointer_value``: a value a record keeps is read anew from its tree at every ask."""

    @wraps(original)
    def pointer_value(
        self: SchemaGraphBuilder, tree: object, fragment: str, reading: str, read: Callable[[], object]
    ) -> object:
        read_now = False

        def counted() -> object:
            nonlocal read_now
            read_now = True
            return read()

        kept = original(self, tree, fragment, reading, counted)
        if (graph_mode := mode(Area.SCHEMA_GRAPH)) is not None and not read_now and read() is not kept:
            from datamodel_code_generator._url_redaction import redact_reference

            where = redact_reference(f"{self.locations[id(tree)].document}#{fragment}")
            graph_mode.violation(
                f"the value kept for {where} differs from its document",
                kind="pointer-value",
                location=where,
            )
        return kept

    return pointer_value


def check_loaded_documents(parser: Any) -> None:
    """Report the loaded documents that changed after their load, at the end of L1."""
    if (graph := parser._schema_graph) is None or (graph_mode := mode(Area.SCHEMA_GRAPH)) is None:
        return
    from datamodel_code_generator._url_redaction import redact_reference

    if written := [
        redact_reference(document)
        for document, raw, recorded in graph_mode.state.get((_LOADED, id(graph)), {}).values()
        if _fingerprint(raw) != recorded
    ]:
        graph_mode.violation(
            f"loaded documents changed after their load: {', '.join(written)}",
            kind="loaded-document",
            locations=written,
        )


def _site_record(site: Any) -> tuple[object, ...]:
    """Return a comparable record of each field of a schema site; the annotations it holds as written included."""
    return tuple(_fingerprint(getattr(site, field.name)) for field in fields(site))


def site_facts(original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``ApiOpenAPIParser._site_facts``: a kept site is read anew from the documents and compared at every read."""

    @wraps(original)
    def site_facts(self: Any, location: Any) -> Any:
        first = location not in self._schema_sites
        kept = original(self, location)
        # the record is taken at the first read: the site holds the document's own annotation values, so a write
        # nested in one of them would change the kept site and a fresh read alike
        records = self.__dict__.setdefault("_kept_site_records", {})
        if (graph_mode := mode(Area.SCHEMA_GRAPH)) is None or first or location not in records:
            records[location] = _site_record(kept)
            return kept
        if (fresh := self._located_site(location)) is None or _site_record(fresh) != records[location]:
            from datamodel_code_generator._url_redaction import redact_reference

            where = redact_reference(str(location))
            graph_mode.violation(
                f"the schema site kept for {where} differs from the documents",
                kind="schema-site",
                location=where,
            )
        return kept

    return site_facts


def site_facts_of(original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``site_facts_of``: a string `$ref` of a site that leads to no loaded schema is reported."""

    @wraps(original)
    def site_facts_of(value: object, location: Any, follow: Callable[[str, str], Any]) -> Any:
        site = original(value, location, follow)
        if (
            (graph_mode := mode(Area.SCHEMA_GRAPH)) is not None
            and site.ref is None
            and isinstance(value, dict)
            and isinstance(ref := value.get("$ref"), str)
        ):
            from datamodel_code_generator._url_redaction import redact_reference

            where = redact_reference(str(location))
            graph_mode.violation(
                f"the $ref {redact_reference(ref)} of the schema site {where} leads to no loaded schema",
                kind="schema-site-reference",
                location=where,
            )
        return site

    return site_facts_of
