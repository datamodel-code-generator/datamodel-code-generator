"""``SCHEMA_GRAPH``: no reader writes a schema object the schema graph validated once and shares.

``SchemaGraphBuilder.schema`` validates each JSON object of a loaded document once and gives every reader the same
object. The wrapper of ``schema`` keeps a fingerprint of each object when it is validated, and the check compares the
objects with their fingerprints at the end of L1.
"""

from __future__ import annotations

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
