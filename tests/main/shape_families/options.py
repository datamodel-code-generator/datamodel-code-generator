"""The option-effect table: which options must not change what the generated models accept.

An option is one of three effects, by the configuration field it sets:

presentation (``PRESENTATION``)
    It changes only how the models are written: annotation spellings, imports, quotes, headers, enum and root model
    forms, model order, frozen or keyword-only classes. A presentation option must not change any verdict of a
    cell (the relation ``option:<name>`` of ``relations.py``); the table holds the arguments that turn it on against
    the default output.
acceptance (``ACCEPTANCE``)
    It changes what the models accept by its purpose (a stricter or more exact reading of the schema, other types for
    some values). Nothing is asserted.
unknown
    Every other option. Nothing is asserted until the option is placed in one of the two.

The arguments of a presentation option keep the checked definition's class name and a module that imports on the
oldest Python the tests run on; an option that renames classes or needs other inputs (a template, a file, a mapping)
stays unknown here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

PRESENTATION: Final[Mapping[str, tuple[str, ...]]] = {
    "capitalise_enum_members": ("--capitalise-enum-members",),
    "collapse_root_models": ("--collapse-root-models",),
    "disable_future_imports": ("--disable-future-imports",),
    "enable_command_header": ("--enable-command-header",),
    "enable_faux_immutability": ("--enable-faux-immutability",),
    "enum_field_as_literal": ("--enum-field-as-literal", "all"),
    "field_constraints": ("--field-constraints",),
    "frozen_dataclasses": ("--frozen-dataclasses",),
    "keep_model_order": ("--keep-model-order",),
    "keyword_only": ("--keyword-only",),
    "reuse_model": ("--reuse-model",),
    "snake_case_field": ("--snake-case-field",),
    "target_python_version": ("--target-python-version", "3.11"),
    "use_annotated": ("--use-annotated",),
    "use_double_quotes": ("--use-double-quotes",),
    "use_frozen_field": ("--use-frozen-field",),
    "use_generic_base_class": ("--use-generic-base-class",),
    "use_generic_container_types": ("--use-generic-container-types",),
    "use_root_model_type_alias": ("--use-root-model-type-alias",),
    "use_specialized_enum": ("--no-use-specialized-enum",),
    "use_standard_collections": ("--no-use-standard-collections",),
    "use_subclass_enum": ("--use-subclass-enum",),
    "use_type_alias": ("--use-type-alias",),
    "use_union_operator": ("--no-use-union-operator",),
}
"""Each presentation option by its configuration field, with the arguments that move it off its default."""

ACCEPTANCE: Final = frozenset({
    "allow_population_by_field_name",
    "check_pattern_maps_strictly",
    "distribute_allof_over_unions",
    "extra_fields",
    "filter_literal_values",
    "intersect_allof_keywords",
    "intersect_combined_schema_keywords",
    "output_date_class",
    "output_datetime_class",
    "read_length_keywords_by_type",
    "read_object_keywords_by_type",
    "read_unevaluated_properties",
    "strict_nullable",
    "strict_types",
    "use_decimal_for_multiple_of",
    "use_default",
    "use_one_literal_as_default",
    "use_serialization_alias",
    "use_single_dict_for_object_maps",
    "use_tuple_for_closed_arrays",
    "use_tuple_for_fixed_length_arrays",
    "use_typed_map_values_beside_other_types",
    "use_typed_nullable_map_values",
    "use_unique_items_as_set",
})
"""Every option that changes what the models accept by its purpose."""
