"""``SCHEMA_GRAPH``: no reader writes a schema object the schema graph validated once and shares, or a schema site.

``SchemaGraphBuilder.schema`` validates each JSON object of a loaded document once and gives every reader the same
object. The wrapper of ``schema`` keeps a fingerprint of each object when it is validated, and the check compares the
objects with their fingerprints at the end of L1.

``ApiOpenAPIParser._site_facts`` reads the site of a location once, for the target bindings. Its wrapper reads a kept
site anew from the documents at every read and compares the two, and the wrapper of ``site_facts_of`` reports a string
`$ref` that leads to no loaded schema when a site is read: a site read before the document its reference names loads.
"""

from __future__ import annotations

from dataclasses import fields
from functools import cached_property, wraps
from typing import TYPE_CHECKING, Any, Final

from tests.checks.areas import Area, mode

if TYPE_CHECKING:
    from collections.abc import Callable

_SHARED: Final = "shared schemas"
"""The schema objects each schema graph validated in the parse, with their fingerprints, by the id of the graph."""


def _fingerprint(value: object, active: frozenset[int] = frozenset()) -> object:
    """Return a comparable record of what an object holds, its attributes and those of the objects it holds included.

    The values ``functools.cached_property`` keeps in an object's ``__dict__`` are not part of it. A container that is
    its own ancestor is recorded by its type.
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
    if not isinstance(attributes := getattr(value, "__dict__", None), dict):
        return type(value), value
    kind = type(value)
    return (
        kind,
        tuple(
            (name, _fingerprint(item, active))
            for name, item in attributes.items()
            if not isinstance(getattr(kind, name, None), cached_property)
        ),
        tuple(
            _fingerprint(getattr(value, name, None), active)
            for name in ("__pydantic_fields_set__", "__pydantic_extra__", "__pydantic_private__")
        ),
    )


def schema(original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``SchemaGraphBuilder.schema``: keep the fingerprint of each object the graph validates for its readers.

    A value the graph does not locate is validated at every read and shared by none, so it is not kept.
    """

    @wraps(original)
    def schema(self: Any, raw: object, kind: type[Any], validate: Callable[[], Any]) -> Any:
        if (
            (graph_mode := mode(Area.SCHEMA_GRAPH)) is None
            or not isinstance(raw, dict)
            or id(raw) not in self.locations
        ):
            return original(self, raw, kind, validate)

        def validated() -> Any:
            value = validate()
            graph_mode.state.setdefault((_SHARED, id(self)), []).append((
                self.locations[id(raw)],
                value,
                _fingerprint(value),
            ))
            return value

        return original(self, raw, kind, validated)

    return schema


def check_shared_schemas(parser: Any) -> None:
    """Report the shared schema objects that changed after validation, at the end of L1."""
    if (graph := parser._schema_graph) is None or (graph_mode := mode(Area.SCHEMA_GRAPH)) is None:
        return
    from datamodel_code_generator._url_redaction import redact_reference

    if changed := [
        redact_reference(str(location))
        for location, value, recorded in graph_mode.state.get((_SHARED, id(graph)), ())
        if _fingerprint(value) != recorded
    ]:
        from datamodel_code_generator import Error

        graph_mode.violation(
            Error,
            f"shared schema objects changed after validation: {', '.join(changed)}",
            kind="shared-schema",
            locations=changed,
        )


def _site_record(site: Any) -> tuple[object, ...]:
    """Return a comparable record of each field of a schema site; the annotations it holds as written included."""
    return tuple(_fingerprint(getattr(site, field.name)) for field in fields(site))


def site_facts(original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``ApiOpenAPIParser._site_facts``: a kept site is read anew from the documents and compared at every read."""

    @wraps(original)
    def site_facts(self: Any, location: Any) -> Any:
        if (graph_mode := mode(Area.SCHEMA_GRAPH)) is None:
            return original(self, location)
        first = location not in self._schema_sites
        kept = original(self, location)
        # the record is taken at the first read: the site holds the document's own annotation values, so a write
        # nested in one of them would change the kept site and a fresh read alike
        records = self.__dict__.setdefault("_kept_site_records", {})
        if first or location not in records:
            records[location] = _site_record(kept)
            return kept
        if (fresh := self._located_site(location)) is None or _site_record(fresh) != records[location]:
            from datamodel_code_generator import Error
            from datamodel_code_generator._url_redaction import redact_reference

            where = redact_reference(str(location))
            graph_mode.violation(
                Error,
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
            from datamodel_code_generator import Error
            from datamodel_code_generator._url_redaction import redact_reference

            where = redact_reference(str(location))
            graph_mode.violation(
                Error,
                f"the $ref {redact_reference(ref)} of the schema site {where} leads to no loaded schema",
                kind="schema-site-reference",
                location=where,
            )
        return site

    return site_facts_of
