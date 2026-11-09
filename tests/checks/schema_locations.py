"""``SCHEMA_LOCATIONS``: every model parsed from a JSON document has a schema location at the end of L1."""

from __future__ import annotations

from typing import Any

from tests.checks.areas import Area, mode


def check_schema_locations(parser: Any) -> None:
    """Report the models parsed from a JSON document without a schema location (the copy step lost it).

    A model whose Reference several schema objects reach has none by rule and is not reported.
    """
    if parser._schema_graph is None or (locations_mode := mode(Area.SCHEMA_LOCATIONS)) is None:
        return
    from datamodel_code_generator.reference import SCHEMA_LOCATION_CONFLICT_KEY, location_of

    if missing := [
        model
        for model in parser.results
        if location_of(model.reference) is None and not model.reference.__dict__.get(SCHEMA_LOCATION_CONFLICT_KEY)
    ]:
        from datamodel_code_generator import Error
        from datamodel_code_generator._url_redaction import redact_reference

        paths = [(model.class_name, redact_reference(model.reference.path)) for model in missing]
        names = ", ".join(f"{name} ({path})" for name, path in paths)
        locations_mode.violation(
            Error,
            f"models without a schema location: {names}",
            models=[{"class_name": name, "path": path} for name, path in paths],
        )
