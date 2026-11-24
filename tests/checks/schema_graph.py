"""``SCHEMA_GRAPH``: a string `$ref` of a schema site that leads to no loaded schema is reported when the site is read.

A site read before the document its reference names loads has a reference that leads nowhere: an ordering fact only a
run shows. That no reader writes the schema objects and documents the schema graph shares is heki's ``MUT202``
(``readonly-returns`` of ``scripts/build_heki_contracts.py``).
"""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, Any

from tests.checks.areas import Area, mode

if TYPE_CHECKING:
    from collections.abc import Callable


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
