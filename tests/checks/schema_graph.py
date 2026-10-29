"""``SCHEMA_GRAPH`` at the end of L1: no reader wrote a schema object the graph validated once and shares.

With a report file, it also records how many references each site read with their edge and as plain strings. The
other checks of the area still run in the package, which keeps the fingerprints this compares; this reads the mode the
package gives the area.
"""

from __future__ import annotations

from typing import Any


def check_shared_schemas(parser: Any) -> None:
    """Report the shared schema objects that changed after validation, and record the reference counts per site."""
    from datamodel_code_generator._checks import CheckArea, check_mode

    if (graph := parser._schema_graph) is None or (graph_mode := check_mode(CheckArea.SCHEMA_GRAPH)) is None:
        return
    from datamodel_code_generator import Error
    from datamodel_code_generator._url_redaction import redact_reference

    if changed := [redact_reference(str(location)) for location in graph.changed_schemas()]:
        graph_mode.violation(
            Error,
            f"shared schema objects changed after validation: {', '.join(changed)}",
            kind="shared-schema",
            locations=changed,
        )
    for (site, origin), (plain, carried) in graph.ref_site_counts().items():
        graph_mode.record(
            f"{CheckArea.SCHEMA_GRAPH.value}: {site} read {carried} references of origin {origin.value} with their "
            f"edge and {plain} as plain strings",
            kind="ref-sites",
            site=site,
            origin=origin.value,
            plain=plain,
            carried=carried,
        )
