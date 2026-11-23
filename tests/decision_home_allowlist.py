"""The scopes of today's code that bypass a builder home of ``scripts/decision_homes.py`` (``BUILDER_HOMES``).

``tests/test_decision_homes.py`` fails on a bypass this table does not hold, and on an entry the code no longer has.
The table only shrinks (``scripts/check_ratchets.py`` counts its entries).
"""

from __future__ import annotations

from typing import Final

BUILDER_BYPASSES: Final[dict[str, tuple[str, ...]]] = {
    "parser/jsonschema.py:JsonSchemaParser._build_field_schema_validator_object": (
        "parser/jsonschema.py:JsonSchemaParser._parse_object_item",
    ),
    "parser/jsonschema.py:JsonSchemaParser._parse_object_item": (
        "parser/jsonschema.py:JsonSchemaParser._build_field_ref_site",
    ),
    "parser/jsonschema.py:JsonSchemaParser._parse_object_map": (
        "parser/_object_maps.py:get_additional_properties_root_field",
        "parser/_object_maps.py:get_typed_additional_properties_field",
        "parser/_value_keywords.py:get_data_type_from_json_value",
        "parser/jsonschema.py:JsonSchemaParser._build_allof_type",
        "parser/jsonschema.py:JsonSchemaParser._build_lightweight_type",
    ),
    "reference.py:ModelResolver.add": (
        "model/base.py:DataModel.create_reuse_model",
        "parser/_passes/cross_module.py:apply_generic_base_class",
    ),
}
"""The scopes of today's code that bypass a builder home of ``scripts/decision_homes.py`` (``BUILDER_HOMES``).

Keyed by a parse method that a dispatch-table row holds, the scopes that call it outside the tables; keyed by a
builder, the scopes that build its marked output outside the builder and its parts. Both as ``path:qualname``,
relative to ``src/datamodel_code_generator``. ``tests/test_decision_homes.py`` fails on a bypass this table does not
hold and on an entry the code no longer has; the table only shrinks (``scripts/check_ratchets.py`` counts it).
"""
