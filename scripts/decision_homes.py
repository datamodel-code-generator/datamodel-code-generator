"""The decisions that have one home: the pure core each decision lives in, and the adapters that hand it its facts.

One ``DecisionHome`` per row of the table under "One home per decision" in ``docs/architecture.md``, in its order;
``scripts/build_architecture_docs.py`` writes that table from ``DECISION_HOMES``. The three cells stay the Markdown the
page shows; ``homes`` is what checks read: every definition of the pure core that the core cell names, where it is
defined (never where it is imported), and ``sites``: the defs, classes and modules of the adapters cell, the scopes
besides the home modules that may call a home. ``tests/test_decision_homes.py`` fails for a home or site that ``src/``
does not define, for one its own cell does not name, and for a caller of a home outside them that
``tests/decision_home_allowlist.py`` does not list.

One ``BuilderHome`` per builder home that the L1 prose of the page states (the parse method a dispatch table
chooses at each position, the one builder of object maps, the resolver that names models): the builder, the passage of
the page that states it, and the syntactic mark of what it builds. The prose stays as written; the guards of
``tests/test_decision_homes.py`` read these records.

One ``Placement`` per row of the table of the Placement Guide that says where a decision reading some facts goes, in
its order; ``scripts/build_architecture_docs.py`` writes that table from ``PLACEMENTS``. The two cells stay the Markdown
the page shows; ``facts`` (members of ``Fact`` and ``RenderFact`` of ``scripts/fact_tables.py``) and ``after`` (passes
of ``scripts/pass_decls.py``) are what ``tests/test_decision_homes.py`` checks: every pass a place names is declared,
writes a fact its row reads and is named by the place cell, which names no other pass.
"""

from __future__ import annotations

from typing import Final, NamedTuple

from scripts.fact_tables import Fact, RenderFact

__all__ = ["BUILDER_HOMES", "DECISION_HOMES", "PLACEMENTS", "BuilderHome", "DecisionHome", "Home", "Mark", "Placement"]


class Home(NamedTuple):
    """Where a decision lives: a module of the package and a name it defines."""

    path: str
    """The module's path relative to ``src/datamodel_code_generator``."""
    name: str = ""
    """The qualified name the module defines (``Class.method``), or ``""`` for the module itself."""


class DecisionHome(NamedTuple):
    """One decision: what it decides, its home, and the Markdown of its three cells in the page table."""

    decision: str
    """What the decision decides: the Markdown of its first cell, which no other record repeats."""
    homes: tuple[Home, ...]
    """Every definition of the pure core that ``core`` names, in its order."""
    core: str
    """The Markdown of the pure core cell."""
    adapters: str
    """The Markdown of the adapters cell: the sites that read the facts and call the core."""
    sites: tuple[Home, ...]
    """Every def or class the adapters cell names, in its order, or a module it names without naming one of its
    definitions: the scopes, besides the home modules, that may call a home (``tests/test_decision_homes.py``)."""


class Mark(NamedTuple):
    """The syntactic mark of a built output: a call of ``callee``, or a call that sets ``keyword`` to ``value``."""

    callee: str = ""
    """The callee as the dotted path its name leads to through imports, or the attribute chain it ends with; ``""``:
    any call."""
    keyword: str = ""
    """A keyword the call passes, as ``keyword=value``, in a ``**`` dict display or in a dict display of flags handed
    on; ``""``: none is asked."""
    value: bool | int | str | None = None
    """The constant the keyword takes."""
    argument: tuple[int, str] | None = None
    """A positional argument the call passes as ``(index, constant)``: the slot a flags helper is asked for
    (``_container_flags(obj, "dict")``); ``None``: none is asked."""


class BuilderHome(NamedTuple):
    """One builder home: what it builds, the passage of the page that states it, the builder, and how to find a bypass.

    A record with a ``table`` stands for a position of L1: its builder chooses the parse method from that dispatch table
    (``parser/_schema_dispatch.py``), the only call of the table's ``choose``, and the parse methods the table's rows
    hold run only through the tables; the table is read outside the builder only by ``typed_children``. A record with
    ``marks`` stands for an output: a call with one of those marks is built only in the builder and its ``parts``.
    The marks are syntactic: a call of the class (or its ``model_construct``/``model_validate``), a keyword, a flag
    dict or a constant slot argument. An output built by ``model_copy`` or by a flag computed from a variable slot
    (``{CONTAINER_FLAGS[slot]: True}``) carries no mark and is not seen.
    """

    output: str
    """What the builder builds."""
    prose: str
    """The passage of ``docs/architecture.md`` that states the home, verbatim up to line breaks; it names the
    builder."""
    builder: Home
    """The def that builds the output."""
    table: Home | None = None
    """The dispatch table the builder chooses from, as the class attribute that holds it."""
    marks: tuple[Mark, ...] = ()
    """The marks of the output outside a dispatch table: a call with any of them builds it."""
    parts: tuple[Home, ...] = ()
    """The defs the builder hands parts of the output to, which the builder reaches by calls through one another."""
    within: str = ""
    """The package path prefix of the modules where a call with the mark is the output (``""``: the whole package)."""


class Placement(NamedTuple):
    """One row of the placement table: what a decision reads, the earliest correct place for it, and their data."""

    reads: str
    """What the decision reads: the Markdown of the first cell, which no other record repeats."""
    facts: frozenset[Fact | RenderFact]
    """The facts of ``scripts/fact_tables.py`` that ``reads`` names; empty where it names none (raw keywords, the
    location of a model, the target version) or any fact."""
    place: str
    """The Markdown of the earliest correct place."""
    after: tuple[str, ...] = ()
    """The passes of ``scripts/pass_decls.py`` that ``place`` puts the decision after; empty where the
    place is no pass (L1, the module loop, the end of a hook, render, any pass)."""


DECISION_HOMES: Final[tuple[DecisionHome, ...]] = (
    DecisionHome(
        decision=(
            "the schema a `$ref` with sibling keywords, an inherited property override or, under "
            "`--intersect-combined-schema-keywords`, a branch of an `anyOf` or `oneOf` beside keywords stands for: the "
            "intersection of its parts, keyword by keyword, keeping the earlier merge wherever it enforces the same "
            "(JSON value equality, enum intersection and tightest bounds included); an allOf whose `anyOf` and `oneOf` "
            "members hold values is the union of one intersection per combination of one branch of each, at most "
            "`BRANCH_COMBINATION_LIMIT`"
        ),
        homes=(
            Home("parser/_intersection.py", "intersect_keywords"),
            Home("parser/_intersection.py", "warn_unsatisfiable"),
            Home("_json_schema.py", "json_values_equal"),
            Home("parser/_intersection.py", "intersect_enum_values"),
            Home("parser/_intersection.py", "intersect_constraint"),
            Home("parser/_intersection.py", "enum_metadata"),
            Home("parser/_intersection.py", "INTERSECTED_KEYWORDS"),
            Home("parser/_intersection.py", "branch_combinations"),
            Home("parser/_intersection.py", "LEGACY_ROWS"),
            Home("parser/_intersection.py", "kept_legacy_rows"),
            Home("parser/_intersection.py", "inherited_override_row"),
            Home("parser/_intersection.py", "inherits_keyword"),
            Home("parser/_intersection.py", "merge_inherited_value"),
            Home("parser/_intersection.py", "merge_inherited_keyword"),
            Home("parser/_intersection.py", "InheritedRow"),
            Home("parser/_intersection.py", "inherited_positional_tail"),
        ),
        core=(
            "`parser/_intersection.py`: `intersect_keywords`, `warn_unsatisfiable`, `json_values_equal` (from "
            "`_json_schema.py`), `intersect_enum_values`, `intersect_constraint`, `enum_metadata`, "
            "`INTERSECTED_KEYWORDS`, `branch_combinations`; `LEGACY_ROWS`, by the option whose off state they are, the "
            "rows of 0.86.0 output a parse keeps without `--intersect-allof-keywords` (`legacy-merge-row`, "
            "`legacy-lookahead-patterns`, `legacy-all-of-site-form`, `legacy-all-of-null-as-written`) and of 0.88.0 "
            "output without `--intersect-combined-schema-keywords` (`legacy-combined-branch-row`, "
            "`legacy-enum-outside-type-kept`, `legacy-schema-maps-merged-as-schemas`, "
            "`legacy-type-narrowing-override-deferred`, `legacy-type-shape-length-from-max-items`, "
            "`legacy-all-of-ref-siblings-ignored`, `legacy-all-of-alias-member-base`, "
            "`legacy-all-of-union-null-dropped`, `legacy-all-of-required-names-dropped`), with `kept_legacy_rows`, the "
            "options' only policy; the inherited property override: its presentation row `inherited_override_row` and "
            "the rows of `--allof-merge-mode` (`inherits_keyword`, which keywords and defaults an override inherits; "
            "`merge_inherited_value` and `merge_inherited_keyword`, how one subschema value or keyword merges, rows "
            "`override` and `type-shape` of `InheritedRow`; `inherited_positional_tail`)"
        ),
        adapters=(
            "`parser/jsonschema.py`: `JsonSchemaParser._legacy_intersection` (the kept rows, read by every site the "
            "options change; `legacy-all-of-null-as-written` through `NullMode.type_excludes_member_null`; "
            "`legacy-enum-outside-type-kept` as `removes_enum_outside_type` of every site's intersection, which "
            "`parser/_schema_reading.py:_intersect_dumps` hands the core as the value check `_json_value_admitted`, "
            "also read by `parser/_schema_validators.py`), `JsonSchemaParser._combined_branch_row` (each inline branch "
            "`_combined_schema_members` gives `parse_combined_schema` and the request/response walk, over "
            "`all_of_member_row` without bounds), `JsonSchemaParser._value_ref_branch_with_site` (a `$ref` branch that "
            "names no object, merged with the value keywords beside its union as with siblings, then an inline branch, "
            "unless `legacy-ref-branch-without-site-parts`) and `JsonSchemaParser._parse_ref_branch_model` (the class "
            "of a `$ref` branch: with the allOf members beside its union, `parse_all_of` of both, unless that row; a "
            "`$ref` branch a discriminator expanded is one such class per subtype, read from the `$ref` the expansion "
            "records on each member, `_expanded_variants` over `parser/_origins.py:with_subtype_ref`), "
            "`JsonSchemaParser._parse_all_of_union_parts` and `_all_of_union_combinations` (the union parts of an "
            "allOf, a part at `union_part-<p>` below the site, and a union model per combination of one branch of each "
            "over `branch_combinations` by model, unless `legacy-union-parts-joined`); `parser/_schema_refs.py`: "
            "`intersect_ref_siblings_schema` (the merged schema of the intersection, its unsatisfiable `enum` or "
            "`const` marked as reported, for `merge_ref_with_schema`, which adds the item bounds of a `contains` "
            "beside the `$ref` through `holder` and keeps the reference it ends at through `target_ref`, and "
            "`parser/_inherited_schemas.py:resolve_inherited_parent_property`) over `intersect_ref_siblings`; "
            "`parser/_rw_split.py`: the inherited field overlay `overlay_inherited_field`; "
            "`parser/_passes/_shared.py`: the field whose default an override takes, `inherited_default_source`; "
            "`parser/_inherited_schemas.py`: `merge_properties_with_parent_constraints` and its one entry per property "
            "`inherited_override_schema` (the type shape of `--allof-merge-mode none`: "
            "`merge_inherited_type_shape_dict`, the length of a closed tuple through `get_inherited_type_shape`), "
            "`override_row` (the presentation row under the parse's merge mode, maps name by name unless "
            "`legacy-schema-maps-merged-as-schemas`, also for the allOf of maps in `parser/_all_of_values.py`), "
            "`get_deferred_inherited_property_names` with `copy_carries_override` (which partial overrides stay a copy "
            "of the inherited field), and `all_of_members` with `alias_target`, the one reader of allOf members for "
            "`JsonSchemaParser.parse_all_of` (`_parse_all_of_item` and the declared property names), `iter_allof_refs` "
            "and `JsonSchemaParser._typed_model_children` (a `$ref` member's siblings as a part where the dialect "
            "reads them, unless `legacy-all-of-ref-siblings-ignored`, and the target an alias names, unless "
            "`legacy-all-of-alias-member-base`, and a `$ref` member naming an allOf that holds a union read in its "
            "place, marked with the locations read through, unless `legacy-ref-member-union-dropped`: "
            "`_read_through_members` over `parser/_all_of_values.py:holds_union_member`, and a property such a member "
            "and another member both declare is one field, the intersection of the declarations over "
            "`all_of_member_row`: `property_conjunctions`, written where first declared by "
            "`with_conjoined_properties`, which also gives each declaration of a property that members within one "
            "location declare the bounds, `multipleOf` and `pattern` of all of them, in every dialect and without the "
            "options: `checked_declarations`; the class of a union member of an allOf declares a property its bases "
            "declare with the check keywords of all: `union_model_properties` over `model_declarations`); the allOf "
            "merges read its helpers (`parser/_lightweight_types.py:_intersect_constraint`, `intersect_enum_values`) "
            "and its rows `concatenating_row` and `all_of_member_row`; "
            "`parser/_all_of_values.py:all_of_value_union_site` (the allOf of value unions of "
            "`JsonSchemaParser.parse_all_of`, before it parses a member, and of a field under "
            "`--distribute-allof-over-unions`) and `all_of_value_variants` (the value branches beside object branches "
            "there), unless `legacy-value-union-members-unread`"
        ),
        sites=(
            Home("parser/jsonschema.py", "JsonSchemaParser._legacy_intersection"),
            Home("parser/_schema_reading.py", "_intersect_dumps"),
            Home("parser/_schema_reading.py", "_json_value_admitted"),
            Home("parser/jsonschema.py", "JsonSchemaParser._combined_branch_row"),
            Home("parser/jsonschema.py", "JsonSchemaParser._combined_schema_members"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_combined_schema"),
            Home("parser/jsonschema.py", "JsonSchemaParser._value_ref_branch_with_site"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_ref_branch_model"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_all_of"),
            Home("parser/jsonschema.py", "_expanded_variants"),
            Home("parser/_origins.py", "with_subtype_ref"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_all_of_union_parts"),
            Home("parser/jsonschema.py", "JsonSchemaParser._all_of_union_combinations"),
            Home("parser/_schema_refs.py", "intersect_ref_siblings_schema"),
            Home("parser/_passes/_shared.py", "_FieldTagFacts.const"),
            Home("parser/_schema_refs.py", "merge_ref_with_schema"),
            Home("parser/_inherited_schemas.py", "resolve_inherited_parent_property"),
            Home("parser/_schema_refs.py", "intersect_ref_siblings"),
            Home("parser/_rw_split.py", "overlay_inherited_field"),
            Home("parser/_passes/_shared.py", "inherited_default_source"),
            Home("parser/_inherited_schemas.py", "merge_properties_with_parent_constraints"),
            Home("parser/_inherited_schemas.py", "inherited_override_schema"),
            Home("parser/_inherited_schemas.py", "merge_inherited_type_shape_dict"),
            Home("parser/_inherited_schemas.py", "get_inherited_type_shape"),
            Home("parser/_inherited_schemas.py", "override_row"),
            Home("parser/_inherited_schemas.py", "get_deferred_inherited_property_names"),
            Home("parser/_inherited_schemas.py", "copy_carries_override"),
            Home("parser/_inherited_schemas.py", "all_of_members"),
            Home("parser/_inherited_schemas.py", "alias_target"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_all_of_item"),
            Home("parser/_inherited_schemas.py", "iter_allof_refs"),
            Home("parser/jsonschema.py", "JsonSchemaParser._typed_model_children"),
            Home("parser/_inherited_schemas.py", "_read_through_members"),
            Home("parser/_all_of_values.py", "holds_union_member"),
            Home("parser/_inherited_schemas.py", "property_conjunctions"),
            Home("parser/_inherited_schemas.py", "with_conjoined_properties"),
            Home("parser/_inherited_schemas.py", "checked_declarations"),
            Home("parser/_inherited_schemas.py", "union_model_properties"),
            Home("parser/_inherited_schemas.py", "model_declarations"),
            Home("parser/_lightweight_types.py", "_intersect_constraint"),
            Home("parser/_all_of_values.py", "all_of_value_union_site"),
            Home("parser/_all_of_values.py", "all_of_value_variants"),
            Home("parser/_schema_validators.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "where a schema object's null comes from (a `type` of null or a `type` list holding null, an "
            "`anyOf`/`oneOf` member typed null, `nullable`, a null `enum` value) and what each L1 site reads from it; "
            "OpenAPI passes `nullable` under `--strict-nullable` as a fact (a marker the first `_get_data_type` call "
            "sets) instead of rewriting `type`; today's disagreeing sites are named `legacy-*` rows, each deleted by "
            "its fix (the null decision of the allOf intersection): root models ignore `nullable` "
            "(`legacy-root-model-type-only`), references "
            "read only a `type` of null (`legacy-ref-target-null-type-only`), root and array sites read `nullable` "
            "without `--strict-nullable` (`legacy-root-type-nullable-ungated`, `legacy-array-nullable-ungated`; "
            "`allOf` sites read it so by the allOf intersection's null rule, `all-of-own-null`), a copied `nullable` "
            "leaves a map without "
            "null (`legacy-map-copied-nullable`), a `Literal` enum reads its null value alone "
            "(`legacy-enum-literal-value-only`), and OpenAPI's `nullable` counts as a type list only after the type is "
            "first built (`legacy-nullable-as-type-list`); the step that writes rendered nullability once at render "
            "preparation deletes the three use-site rows "
            "(`legacy-nullable-as-type-list`, `legacy-root-model-type-only`, `legacy-ref-target-null-type-only`)"
        ),
        homes=(
            Home("parser/_null_facts.py", "resolve_null_facts"),
            Home("parser/_null_facts.py", "NullSources"),
            Home("parser/_null_facts.py", "NullMode"),
            Home("parser/_null_facts.py", "NullFacts"),
            Home("parser/_null_facts.py", "null_sources"),
            Home("parser/_null_facts.py", "dump_null_sources"),
            Home("parser/_schema_object.py", "_type_null_sources"),
            Home("parser/_null_facts.py", "with_null"),
            Home("parser/_null_facts.py", "NullForm"),
            Home("parser/_null_facts.py", "null_schema"),
            Home("parser/_all_of_values.py", "all_of_site_schema"),
        ),
        core=(
            "`parser/_null_facts.py`: `resolve_null_facts` (`NullSources`, `NullMode`, `NullFacts`), `null_sources` "
            "(the one builder of `NullSources`, which `dump_null_sources` calls with the own `type`, `nullable` and "
            "`enum` of a dump and `parser/_schema_object.py:_type_null_sources` with those of a schema object), and "
            "the forms converters write null in, `with_null` (`NullForm`) and `null_schema` (also the null member of "
            "`JsonSchemaParser._add_nullable_combined_schema_branches` and "
            "`parser/_all_of_values.py:all_of_site_schema`)"
        ),
        adapters=(
            "`parser/_intersection.py:inherited_override_row` (row `inherited-typed-without-null` from a dump); "
            "`parser/jsonschema.py:_null_sources` and `parser/_schema_object.py`: `_read_type` and "
            "`_type_null_sources` (the facts), `JsonSchemaParser._null_mode` (overridden by "
            "`parser/openapi.py:OpenAPIParser._null_mode`) and `JsonSchemaParser._null_facts`; sites "
            "`JsonSchemaObject.type_has_null`, `_excluded_null_members` (for "
            "`JsonSchemaParser.parse_combined_schema`), `_enum_entries_skip_null`, `JsonSchemaParser`: "
            "`get_object_field`, `_get_data_type`, `_get_python_type_override`, `_object_nullable`, "
            "`_create_registered_root_model`, `get_ref_data_type` and `_cache_ref_data_type_facts`, "
            "`_parse_ref_nullable_only_item`, `_build_constrained_type_union`, `parse_array_fields`, "
            "`_parse_root_type_with_context`, `_parse_multiple_types_with_properties`, `parse_enum`, "
            "`_literal_values_type`, `_add_nullable_combined_schema_branches`, `_enforced_value_keywords`, "
            "`_reads_enum_as_const`, `_all_of_part_admits_null` (site `all-of-union`, read by `parse_all_of` for the "
            "union of an allOf member's variants unless `legacy-all-of-union-null-dropped`); "
            "`parser/_schema_documents.py:document_as_written`, the document rewrite; `parser/_inherited_schemas.py`: "
            "`merge_inherited_type_shape_dict`, `mark_partial_inherited_fields`, `get_inherited_schema_types`; "
            "`parser/_value_keywords.py`: `resolve_array_field_required_nullable`, `extract_const_enum_from_combined`; "
            "`parser/_all_of_values.py`: `merge_all_of_root_schema`, `all_of_root_value_schema`; "
            "`parser/_object_maps.py:object_map_admits_null`; `parser/graphql.py:GraphQLParser.parse_field` "
            "(`GraphQLParser._null_mode`); the converters `parser/avro.py` (`NULL_SCHEMA`), `parser/protobuf.py` "
            "(`WELL_KNOWN_SCHEMAS`) and `parser/xmlschema.py:_XMLSchemaConverter._apply_common_element_metadata` "
            "(`nillable`)"
        ),
        sites=(
            Home("parser/_intersection.py", "inherited_override_row"),
            Home("parser/jsonschema.py", "_null_sources"),
            Home("parser/_schema_object.py", "_read_type"),
            Home("parser/_schema_object.py", "_type_null_sources"),
            Home("parser/jsonschema.py", "JsonSchemaParser._null_mode"),
            Home("parser/openapi.py", "OpenAPIParser._null_mode"),
            Home("parser/jsonschema.py", "JsonSchemaParser._null_facts"),
            Home("parser/_schema_object.py", "JsonSchemaObject.type_has_null"),
            Home("parser/jsonschema.py", "_excluded_null_members"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_combined_schema"),
            Home("parser/jsonschema.py", "JsonSchemaParser"),
            Home("parser/jsonschema.py", "JsonSchemaParser.get_object_field"),
            Home("parser/jsonschema.py", "JsonSchemaParser._get_data_type"),
            Home("parser/jsonschema.py", "JsonSchemaParser._get_python_type_override"),
            Home("parser/jsonschema.py", "JsonSchemaParser._object_nullable"),
            Home("parser/jsonschema.py", "JsonSchemaParser._create_registered_root_model"),
            Home("parser/jsonschema.py", "JsonSchemaParser.get_ref_data_type"),
            Home("parser/jsonschema.py", "JsonSchemaParser._cache_ref_data_type_facts"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_ref_nullable_only_item"),
            Home("parser/jsonschema.py", "JsonSchemaParser._build_constrained_type_union"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_array_fields"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_root_type_with_context"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_multiple_types_with_properties"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_enum"),
            Home("parser/jsonschema.py", "JsonSchemaParser._literal_values_type"),
            Home("parser/jsonschema.py", "JsonSchemaParser._add_nullable_combined_schema_branches"),
            Home("parser/jsonschema.py", "JsonSchemaParser._enforced_value_keywords"),
            Home("parser/jsonschema.py", "JsonSchemaParser._reads_enum_as_const"),
            Home("parser/jsonschema.py", "JsonSchemaParser._all_of_part_admits_null"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_all_of"),
            Home("parser/_schema_documents.py", "document_as_written"),
            Home("parser/_inherited_schemas.py", "merge_inherited_type_shape_dict"),
            Home("parser/_inherited_schemas.py", "mark_partial_inherited_fields"),
            Home("parser/_inherited_schemas.py", "get_inherited_schema_types"),
            Home("parser/_value_keywords.py", "resolve_array_field_required_nullable"),
            Home("parser/_value_keywords.py", "extract_const_enum_from_combined"),
            Home("parser/_all_of_values.py", "merge_all_of_root_schema"),
            Home("parser/_all_of_values.py", "all_of_root_value_schema"),
            Home("parser/_object_maps.py", "object_map_admits_null"),
            Home("parser/graphql.py", "GraphQLParser.parse_field"),
            Home("parser/graphql.py", "GraphQLParser._null_mode"),
            Home("parser/xmlschema.py", "_XMLSchemaConverter._apply_common_element_metadata"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether a type's rendered hint, or the annotation a field writes around it, includes None, and the None a "
            "reference keeps after model merges: where the None of a type comes from is one record on the type "
            "(`TypeNull`), which each merge writes with the null of the schema every moved reference named, in both "
            "directions; row `legacy-merged-reference-null`: a reference whose schema admits null takes the null of "
            "the model it names after the merge, unless `--keep-reference-null-on-merged-models` makes the merges "
            "record that null as the one the reference keeps (`TypeNull.keeps_named`, passed by "
            "`parser/_passes/_shared.py:merge_duplicate` and `subclass_duplicate` and read by "
            "`_target_binding.py:_Binder._reference_null`)"
        ),
        homes=(
            Home("model/_decisions/nullability.py", "rendered_nullability"),
            Home("model/_decisions/nullability.py", "takes_member_null"),
            Home("model/_decisions/nullability.py", "takes_item_null"),
            Home("model/_decisions/nullability.py", "member_null"),
            Home("model/_decisions/nullability.py", "field_renders_none"),
            Home("model/_decisions/nullability.py", "reference_null"),
            Home("model/_decisions/nullability.py", "reference_adds_null"),
            Home("model/_decisions/nullability.py", "TypeNull"),
        ),
        core=(
            "`model/_decisions/nullability.py`: `rendered_nullability`, `takes_member_null`, `takes_item_null`, "
            "`member_null`, `field_renders_none`, `reference_null`, `reference_adds_null` (record `TypeNull`)"
        ),
        adapters=(
            "`types.py:DataType.admits_none`, `items_admit_none` (the None of a container's item union, inside it), "
            "`names_nullable_model`, `adds_reference_null`; `model/_field_imports.py:field_none_source` (the field's "
            "facts into `field_renders_none`, for `model/base.py:DataModelFieldBase._field_null_source` and "
            "`field_admits_none`); writes: `types.py:DataType._materialize_render_state` (see L7) and the record, "
            "through `DataType._update_type_null`, from the store operations `redirect_reference_users`, "
            "`redirect_model_reference_users`, `mark_target_null`, `clear_target_null`, `write_rendered_null` and "
            "`drop_held_null` of `parser/generation.py:GenerationStore`; the targets read them: "
            "`_target_binding.py:_Projector._admits_none` (`DataType.admits_none` of a node; a reference that binds no "
            "symbol is read through as the type it stands for, which holds the None of its model already, and adds "
            "only its own None), `_Projector._structural` (`DataType.items_admit_none` of a container whose items are "
            "several schemas), `field_admits_none` for an annotation (`_Models.field_facts` and "
            "`_Models.alias_nullable`, whose field's own null is `field_renders_none`'s); not yet: the null of the "
            "wire, `_Models.wire_null` over `_Models.preexisting_null` (rows `legacy-wire-null-ignores-nullable-false` "
            "and `legacy-wire-null-unknown-type`)"
        ),
        sites=(
            Home("types.py", "DataType.admits_none"),
            Home("types.py", "DataType.items_admit_none"),
            Home("types.py", "DataType.names_nullable_model"),
            Home("types.py", "DataType.adds_reference_null"),
            Home("model/_field_imports.py", "field_none_source"),
            Home("model/base.py", "DataModelFieldBase._field_null_source"),
            Home("model/_field_imports.py", "field_admits_none"),
            Home("types.py", "DataType._materialize_render_state"),
            Home("types.py", "DataType._update_type_null"),
            Home("parser/generation.py", "GenerationStore"),
            Home("_target_binding.py", "_Projector._admits_none"),
            Home("_target_binding.py", "_Projector._structural"),
            Home("_target_binding.py", "_Models.field_facts"),
            Home("_target_binding.py", "_Models.alias_nullable"),
            Home("_target_binding.py", "_Models.wire_null"),
            Home("_target_binding.py", "_Models.preexisting_null"),
        ),
    ),
    DecisionHome(
        decision="whether a member of a union is the None it adds: the type None without members of its own",
        homes=(Home("types.py", "_is_plain_none"),),
        core="`types.py:_is_plain_none`",
        adapters=(
            "the `is_none` that a member gives `member_null` (`types.py:DataType._gives_member_null`), the members "
            "whose keywords a union takes (`types.py:DataTypeManager.constraint_keywords`) and whose leaves it "
            "enforces (`parser/_value_keywords.py:_enforcing_leaves`), and the None members an override drops from an "
            "inherited union (`parser/_field_copies.py:_without_null_members`); left out: the bare None a hint writes "
            "(`types.py:DataType._is_bare_none`), which has no alias, reference or callable either"
        ),
        sites=(
            Home("types.py", "DataType._gives_member_null"),
            Home("types.py", "DataTypeManager.constraint_keywords"),
            Home("parser/_value_keywords.py", "_enforcing_leaves"),
            Home("parser/_field_copies.py", "_without_null_members"),
            Home("types.py", "DataType._is_bare_none"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a data type writes its type hint, and the base hint of RootModel generics: one expression of its tree "
            "(`TypeExpr`: `TypeLeaf`, `RefLeaf`, `TypeApply`) that one renderer writes through the backend hooks of "
            "its nodes (`_get_wrapped_reference_type_hint`, `_wrap_discriminator_type_hint`, "
            "`_CONSTRAINED_TYPE_TO_BASE`, the container orders, the `type_hint` of a class that writes its own); "
            "names, quotes and None are read when it is written"
        ),
        homes=(
            Home("types.py", "type_expr_of"),
            Home("types.py", "render_type_expr"),
        ),
        core="`types.py`: `type_expr_of`, `render_type_expr`",
        adapters=(
            "`types.py:DataType.type_hint` and `base_type_hint`; the expression frozen with each field render plan is "
            "checked against the type hint when the modules are written (`DATAMODEL_CODE_GENERATOR_CHECK_RENDER_PLANS`)"
        ),
        sites=(
            Home("types.py", "DataType.type_hint"),
            Home("types.py", "DataType.base_type_hint"),
        ),
    ),
    DecisionHome(
        decision=(
            "which class an enum is written as: under `--keep-enum-member-values` a value base (`IntEnum`, `StrEnum`, "
            "`(T, Enum)`) only when it keeps every member value, else `Enum`; without it the 0.86.0 base of the schema "
            "type unless that base refuses a member (`legacy-value-base-changing-a-member`); merged enums keep today's "
            "kinds as named `legacy-*` rows, and msgspec keeps the 0.86.0 base where it could not decode that `Enum` "
            "(`legacy-msgspec-decodable-value-base`, until msgspec Enums are decided as a whole; capability "
            "`DataModel._DECODES_MIXED_ENUM_VALUES` through "
            "`model/output.py:OutputModelContext.decodes_mixed_enum_values`)"
        ),
        homes=(Home("model/_decisions/enum_kind.py", "decide_enum_kind"),),
        core="`model/_decisions/enum_kind.py`: `decide_enum_kind`",
        adapters=(
            "`parser/jsonschema.py:JsonSchemaParser._get_enum_model_class` (schema enums, also those merged from "
            "`const` branches and those of the converted formats); "
            "`parser/graphql.py:GraphQLParser.parse_enum_as_enum_class`; "
            "`parser/_passes/modules.py:extract_inherited_enum` (L5); classes: `model/enum.py:_enum_class`"
        ),
        sites=(
            Home("parser/jsonschema.py", "JsonSchemaParser._get_enum_model_class"),
            Home("parser/graphql.py", "GraphQLParser.parse_enum_as_enum_class"),
            Home("parser/_passes/modules.py", "extract_inherited_enum"),
            Home("model/enum.py", "_enum_class"),
        ),
    ),
    DecisionHome(
        decision=(
            "the builtin type the members of an enum are instances of, its value base: the `T` of the `(T, Enum)` that "
            "`--use-subclass-enum` writes, or the `str` and `int` of a `StrEnum` and an `IntEnum`; it tells whether "
            "the members equal their values (the keys of discriminator choices), which constraint keywords a type that "
            "names the enum takes and which values msgspec decodes; row `legacy-merged-enum-specialized-only`: merging "
            "the enums that a model inherits reads the class of the first alone (`StrEnum`, `IntEnum`)"
        ),
        homes=(
            Home("model/enum.py", "enum_value_base"),
            Home("model/enum.py", "EnumValueBaseRow"),
        ),
        core="`model/enum.py`: `enum_value_base` (rows `EnumValueBaseRow`)",
        adapters=(
            "`model/enum.py:Enum.value_base` (read by `types.py:DataTypeManager._constraint_keywords` through "
            "`ModelView.value_base`); `parser/_tag_plan.py:_member_choice_keys` (the keys of the choices that members "
            "make, read by `_written_enum_member_keys` before the tags are written and by `settle_site` after), "
            "`parser/_tag_plan.py:writes_member_values` (the members of a plain Enum that "
            "`--use-enum-values-in-discriminator` writes as tags hold their values beside them, `Literal[Kind.a, "
            "'a']`, written by `parser/base.py:Parser._create_discriminator_data_type`); `parser/_passes/imports.py`: "
            "`_msgspec_mixed_enum_message`, `_msgspec_wide_int_enum_message`; "
            "`parser/_passes/modules.py:extract_inherited_enum` and its L1 prediction "
            "`parser/_all_of_values.py:merged_enum_fails`"
        ),
        sites=(
            Home("model/enum.py", "Enum.value_base"),
            Home("types.py", "DataTypeManager._constraint_keywords"),
            Home("parser/_tag_plan.py", "_member_choice_keys"),
            Home("parser/_tag_plan.py", "_written_enum_member_keys"),
            Home("parser/_tag_plan.py", "settle_site"),
            Home("parser/_tag_plan.py", "writes_member_values"),
            Home("parser/base.py", "Parser._create_discriminator_data_type"),
            Home("parser/_passes/imports.py", "_msgspec_mixed_enum_message"),
            Home("parser/_passes/imports.py", "_msgspec_wide_int_enum_message"),
            Home("parser/_passes/modules.py", "extract_inherited_enum"),
            Home("parser/_all_of_values.py", "merged_enum_fails"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether an enum is written as a `Literal`, and the form an enum site writes (its const, a `Literal` or an "
            "Enum class)"
        ),
        homes=(
            Home("model/_decisions/enum_kind.py", "enum_as_literal"),
            Home("parser/jsonschema.py", "JsonSchemaParser._enum_site_form"),
            Home("parser/jsonschema.py", "JsonSchemaParser.should_parse_enum_as_literal"),
        ),
        core=(
            "`model/_decisions/enum_kind.py`: `enum_as_literal`; the site form: "
            "`parser/jsonschema.py:JsonSchemaParser._enum_site_form` (the const beside the enum, then "
            "`should_parse_enum_as_literal`, else the class)"
        ),
        adapters=(
            "`should_parse_enum_as_literal` of `parser/jsonschema.py:JsonSchemaParser` and "
            "`parser/graphql.py:GraphQLParser`; the sites of `_enum_site_form`: "
            "`parser/jsonschema.py:JsonSchemaParser` `_parse_enum_item`, `parse_array_fields` (row "
            "`legacy-array-enum-class-only`), `_parse_root_type_with_context`, `_definition_enum_applies`, "
            "`_parse_all_of_merged_root`, `_parse_combined_const_enum`, `_should_create_type_alias_for_title` (row "
            "`legacy-title-alias-literal-unread-as-const`); "
            "`parser/openapi.py:OpenAPIParser._operation_literal_enum_applies`; "
            "`parser/_all_of_values.py:merged_enum_fails`"
        ),
        sites=(
            Home("parser/jsonschema.py", "JsonSchemaParser"),
            Home("parser/graphql.py", "GraphQLParser"),
            Home("parser/jsonschema.py", "JsonSchemaParser._enum_site_form"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_enum_item"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_array_fields"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_root_type_with_context"),
            Home("parser/jsonschema.py", "JsonSchemaParser._definition_enum_applies"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_all_of_merged_root"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_combined_const_enum"),
            Home("parser/jsonschema.py", "JsonSchemaParser._should_create_type_alias_for_title"),
            Home("parser/openapi.py", "OpenAPIParser._operation_literal_enum_applies"),
            Home("parser/_all_of_values.py", "merged_enum_fails"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether an enum accepts the values of its type that it does not list (`--open-enums`: the "
            "`x-extensible-enum` or `x-ms-enum` `modelAsString` marker beside its `enum`, else `all` for two or more "
            "values; a const beside it keeps it closed), and the base an open Enum class takes in place of its `Enum`, "
            "`StrEnum` or `IntEnum` (`--open-enum-base-class-map`)"
        ),
        homes=(
            Home("model/_decisions/enum_kind.py", "enum_open_marker"),
            Home("model/_decisions/enum_kind.py", "enum_is_open"),
            Home("model/_decisions/enum_kind.py", "open_enum_base"),
        ),
        core="`model/_decisions/enum_kind.py`: `enum_open_marker`, `enum_is_open`, `open_enum_base`",
        adapters=(
            "`parser/base.py:Parser`: `_enum_is_open`, `_open_enum_base` (the class through "
            "`model/enum.py:enum_class_base`); `parser/jsonschema.py:JsonSchemaParser`: `_schema_enum_is_open` "
            "(`parse_enum`), `_open_enum_without_base` (an open enum whose class has no entry is a `Literal` whatever "
            "`enum_as_literal` says: `parser/_value_keywords.py:enum_as_literal_under`), `_with_open_enum_type` (the "
            "`Literal` of an open enum joined with the type of its schema, the keywords beside it on that type: "
            "`parser/_value_keywords.py:enum_literal`, `parser/_object_maps.py:parse_property_name_key_schema`); "
            "`parser/graphql.py:GraphQLParser._graphql_enum_is_open` (`parse_enum_as_enum_class`, "
            "`should_parse_enum_as_literal`, `parse_enum_as_literal`: `Literal[...] | str`); "
            "`parser/_passes/modules.py:_merged_open_enum_base` (`extract_inherited_enum`: the base of the first "
            "merged enum when every one has one); the stdlib import a base replaces is left out by "
            "`model/enum.py:Enum._default_imports`"
        ),
        sites=(
            Home("parser/base.py", "Parser"),
            Home("parser/base.py", "Parser._enum_is_open"),
            Home("parser/base.py", "Parser._open_enum_base"),
            Home("model/enum.py", "enum_class_base"),
            Home("parser/jsonschema.py", "JsonSchemaParser"),
            Home("parser/jsonschema.py", "JsonSchemaParser._schema_enum_is_open"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_enum"),
            Home("parser/jsonschema.py", "JsonSchemaParser._open_enum_without_base"),
            Home("parser/_value_keywords.py", "enum_as_literal_under"),
            Home("parser/jsonschema.py", "JsonSchemaParser._with_open_enum_type"),
            Home("parser/_value_keywords.py", "enum_literal"),
            Home("parser/_object_maps.py", "parse_property_name_key_schema"),
            Home("parser/graphql.py", "GraphQLParser._graphql_enum_is_open"),
            Home("parser/graphql.py", "GraphQLParser.parse_enum_as_enum_class"),
            Home("parser/_value_keywords.py", "should_parse_enum_as_literal"),
            Home("parser/graphql.py", "GraphQLParser.parse_enum_as_literal"),
            Home("parser/_passes/modules.py", "_merged_open_enum_base"),
            Home("parser/_passes/modules.py", "extract_inherited_enum"),
            Home("model/enum.py", "Enum._default_imports"),
        ),
    ),
    DecisionHome(
        decision=(
            "which constraint keywords each emitted type takes in each output model, which keywords a declared schema "
            "type holds (a string reads `minItems`/`maxItems` as its lengths), which JSON kind each keyword applies "
            "to, the `con*` and `msgspec.Meta` arguments, the classes that enforce a zero bound and the values decoded "
            "from base64 text that carry their constraints; which declared keywords a field writes (a number keyword "
            "when a number value of the emitted type meets it, a string keyword when the type takes it) and which it "
            "leaves out with a warning; the keywords a type enforces itself by its zero-bound class or its literal "
            "values, which `--omit-redundant-constraints` leaves out at every position, the zero bound a field form "
            "keeps on its type, the `Decimal` of `--use-decimal-in-field-forms`, the `EncodedStr` of "
            "`--use-encoded-str-for-constrained-base64`, the `StrictStr` it decodes under "
            "`--use-strict-str-for-base64`, the reading by kind of `--read-length-keywords-by-type` (the options as "
            "the record `ConstraintOptions`); which types count the keys of a map they replace, a mapping class or a "
            "type the generator cannot classify, and so take its property counts (`counts_keys`, under "
            "`--type-overrides-replace-whole-type`), by the mapping classes of each output "
            "(`OutputKeywords.mapping_classes`), whose keys `msgspec.Meta` also checks as a length; why a field drops "
            "its constraints when it is written (a self-reference outside a container, a strict type with arguments, "
            "`AnyUrl`, the number constraints of a `None` type, in the Pydantic outputs the keywords no value of a "
            "type whose values are the instances of models takes) and the float `multipleOf` divisors msgspec checks "
            "inexactly; how a field assembles its constraints, one rule for an object property and a root field (the "
            "declared keywords it reads, an array's whatever the options and the others under `--field-constraints` "
            "and not beside a model or an enum class; the hostname pattern, the property counts of a map, the item "
            "counts of an array (a field whose type is no array takes none, nor does a root beside a model: "
            "`can_hold_array`; nor does one whose schema declares types without an array: `admits_array`) and the "
            "lengths a fixed tuple leaves out, none of the keywords and item counts for a heterogeneous type list but "
            "the `uniqueItems` its array member reads, then what it records), whose site facts are the rows of "
            "`_ConstraintSite` (a root is the model its schema becomes, so it reads the keywords beside a `$ref`, a "
            "combinator or an object too; a type list of number kinds keeps its shared bounds on a property, whose "
            "type leaves them out; a root always holds a constraints record); rows "
            "`legacy-consumed-at-object-property`, `legacy-property-counts-beside-models`, "
            "`legacy-root-tuple-lengths`, `legacy-root-records-by-option`, `legacy-all-of-root-format-kinds`, "
            "`legacy-undeclared-keywords-off-literal`, `legacy-override-keeps-string-keywords`, "
            "`legacy-class-override-keeps-container`, `legacy-anyurl-keeps-string-keywords`, "
            "`legacy-reference-takes-every-keyword`, `legacy-length-keywords-across-kinds` keep today's disagreeing "
            "sites"
        ),
        homes=(
            Home("_constraint_applicability.py", "OutputKeywords"),
            Home("_constraint_applicability.py", "BASE_OUTPUT"),
            Home("_constraint_applicability.py", "PYDANTIC_OUTPUT"),
            Home("_constraint_applicability.py", "MSGSPEC_OUTPUT"),
            Home("_constraint_applicability.py", "listed_type_keywords"),
            Home("_constraint_applicability.py", "unlisted_type_keywords"),
            Home("_constraint_applicability.py", "shape_keywords"),
            Home("_constraint_applicability.py", "key_count_keywords"),
            Home("_constraint_applicability.py", "member_keywords"),
            Home("_constraint_applicability.py", "literal_number_keywords"),
            Home("_constraint_applicability.py", "DeclaredKinds"),
            Home("_constraint_applicability.py", "declared_keywords"),
            Home("_constraint_applicability.py", "other_kind_keywords"),
            Home("_constraint_applicability.py", "split_declared_keywords"),
            Home("_constraint_applicability.py", "declared_string_keywords_taken"),
            Home("_constraint_applicability.py", "length_reading"),
            Home("_constraint_applicability.py", "untaken_values"),
            Home("_constraint_applicability.py", "undeclared_keywords_taken"),
            Home("_constraint_applicability.py", "property_consumed_keywords"),
            Home("_constraint_applicability.py", "keyword_kinds"),
            Home("_constraint_applicability.py", "all_of_root_keyword_kinds"),
            Home("_constraint_applicability.py", "constrained_type_keyword_order"),
            Home("_constraint_applicability.py", "shape_meta_keywords"),
            Home("_constraint_applicability.py", "meta_keywords"),
            Home("_constraint_applicability.py", "overridden_declared_constraints"),
            Home("_constraint_applicability.py", "resplit_overridden_constraints"),
            Home("_constraint_applicability.py", "untaken_minimums"),
            Home("_constraint_applicability.py", "ConstraintOptions"),
            Home("_constraint_applicability.py", "enforced_keywords"),
            Home("_constraint_applicability.py", "literal_enforces_every_keyword"),
            Home("_constraint_applicability.py", "writes_beside_literal"),
            Home("_constraint_applicability.py", "literal_met_keywords"),
            Home("_constraint_applicability.py", "field_form_zero_bound"),
            Home("_constraint_applicability.py", "decimal_field_form"),
            Home("_constraint_applicability.py", "strict_decimal_multiple"),
            Home("_constraint_applicability.py", "encoded_str_field_form"),
            Home("_constraint_applicability.py", "strict_encoded_str"),
            Home("_constraint_applicability.py", "length_keywords_by_type"),
            Home("_constraint_applicability.py", "render_drop"),
            Home("_constraint_applicability.py", "none_type_keywords"),
            Home("_constraint_applicability.py", "inexact_float_divisor"),
            Home("_constraint_applicability.py", "engine_pattern_types"),
        ),
        core=(
            "`_constraint_applicability.py`: the `OutputKeywords` rows `BASE_OUTPUT`, `PYDANTIC_OUTPUT`, "
            "`MSGSPEC_OUTPUT`; `listed_type_keywords`, `unlisted_type_keywords`, `shape_keywords`, "
            "`key_count_keywords`, `member_keywords`, `literal_number_keywords`, `DeclaredKinds`, `kind_keywords`, "
            "`declared_keywords`, `other_kind_keywords`, `split_declared_keywords`, `declared_string_keywords_taken`, "
            "`length_reading`, `untaken_values`, `undeclared_keywords_taken`, `property_consumed_keywords`, "
            "`keyword_kinds`, `all_of_root_keyword_kinds`, `constrained_type_keyword_order`, `shape_meta_keywords`, "
            "`meta_keywords`, `overridden_declared_constraints`, `resplit_overridden_constraints`, `untaken_minimums`; "
            "`ConstraintOptions`, `enforced_keywords`, `literal_enforces_every_keyword`, `writes_beside_literal`, "
            "`literal_met_keywords`, `field_form_zero_bound`, `decimal_field_form`, `strict_decimal_multiple`, "
            "`encoded_str_field_form`, `strict_encoded_str`, `length_keywords_by_type`, `render_drop`, "
            "`none_type_keywords`, `inexact_float_divisor`, `engine_pattern_types`"
        ),
        adapters=(
            "`types.py:DataTypeManager.constraint_keywords`, `member_constraint_keywords` (one walk, "
            "`_constraint_keywords`: the model a type names is read only for a union member's value, row "
            "`legacy-reference-takes-every-keyword`), `model_value_constraint_keywords` (what one value of a type "
            "whose values are the instances of models takes, over that walk) and `type_constraint_keywords` (its "
            "`CONSTRAINT_KEYWORDS`, `NUMBER_VALUE_TYPES`, `TARGET_NUMBER_KEYWORDS`, `CONSTRAINED_TYPE_CONSUMED_KEYS` "
            "and the flags, the keyword groups of `types.py` and the `con*` filters of `model/pydantic_v2/types.py` "
            "read the rows); `parser/_value_keywords.py`: `split_constraint_values`, `keywords_taken`, "
            "`declared_kinds`, `schema_kinds`, `type_list_keywords` (also for the array builder of a type list, "
            "`JsonSchemaParser.parse_array_fields`); the field assembly "
            "`parser/jsonschema.py:JsonSchemaParser._assemble_field_constraints` and `_mark_assembled_constraints` "
            "(sites `_PROPERTY_SITE` for `get_object_field`, with the zero-bound keywords of an object property, and "
            "`_ROOT_SITE` for `_parse_root_type_with_context`); the zero-bound keywords of "
            "`parser/_inherited_schemas.py:drop_incompatible_inherited_constraints`; the format step of "
            "`parser/_all_of_values.py:handle_allof_root_model_with_constraints`; `parser/_type_overrides.py`: "
            "`decide_overridden_constraints` (with `_untaken_container_counts`) and `mark_untaken_constraints`; "
            "`parser/_passes/modules.py:_discriminator_tag_constraints` (the tag keyword); "
            "`model/msgspec.py:DataModelField._meta_keywords`, `_dropped_meta_keywords` and "
            "`_literal_decided_meta_keywords`; `types.py:DataTypeManager.constraint_options` (the options) and "
            "`encodes_constrained_base64`; `parser/_value_keywords.py`: `type_enforced_keywords` (over "
            "`_enforcing_leaves`), `can_hold_array`, `const_literal_enforces_constraints` and `literal_written`; "
            "`model/pydantic_v2/types.py:DataTypeManager._field_form_kwargs`, `get_data_type` and "
            "`get_data_float_type`; `model/base.py:DataModelFieldBase._constraints_blocked_by`, "
            "`model/pydantic_base.py:DataModelField._warned_omissions` and `_normalized_constraint_arguments`, "
            "`model/pydantic_base.py:DataModelField._untaken_beside_models`; capability "
            "`DataTypeManager._WRITES_CONSTRAINED_TYPES` through "
            "`model/output.py:OutputModelContext.writes_constrained_types`; the string and bytes types whose pattern "
            "the regex engine choice reads (`model/pydantic_v2/base_model.py:_pattern_data_type`); not yet: "
            "`parser/_value_keywords.py:has_effective_constraints` (its own keywords of a type) and the array "
            "builder's own assembly (`JsonSchemaParser.parse_array_fields`: `get_array_constraints` and the lengths of "
            "a fixed tuple)"
        ),
        sites=(
            Home("types.py", "DataTypeManager.constraint_keywords"),
            Home("types.py", "DataTypeManager.member_constraint_keywords"),
            Home("types.py", "DataTypeManager._constraint_keywords"),
            Home("types.py", "DataTypeManager.model_value_constraint_keywords"),
            Home("types.py", "DataTypeManager.type_constraint_keywords"),
            Home("parser/_value_keywords.py", "split_constraint_values"),
            Home("parser/_value_keywords.py", "keywords_taken"),
            Home("parser/_value_keywords.py", "declared_kinds"),
            Home("parser/_value_keywords.py", "schema_kinds"),
            Home("parser/_value_keywords.py", "type_list_keywords"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_array_fields"),
            Home("parser/jsonschema.py", "JsonSchemaParser._assemble_field_constraints"),
            Home("parser/jsonschema.py", "JsonSchemaParser._mark_assembled_constraints"),
            Home("parser/jsonschema.py", "JsonSchemaParser.get_object_field"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_root_type_with_context"),
            Home("parser/_inherited_schemas.py", "drop_incompatible_inherited_constraints"),
            Home("parser/_all_of_values.py", "handle_allof_root_model_with_constraints"),
            Home("parser/_type_overrides.py", "decide_overridden_constraints"),
            Home("parser/_type_overrides.py", "_untaken_container_counts"),
            Home("parser/_type_overrides.py", "mark_untaken_constraints"),
            Home("parser/_passes/modules.py", "_discriminator_tag_constraints"),
            Home("model/msgspec.py", "DataModelField._meta_keywords"),
            Home("model/msgspec.py", "DataModelField._dropped_meta_keywords"),
            Home("model/msgspec.py", "DataModelField._literal_decided_meta_keywords"),
            Home("types.py", "DataTypeManager.constraint_options"),
            Home("types.py", "DataTypeManager.encodes_constrained_base64"),
            Home("parser/_value_keywords.py", "type_enforced_keywords"),
            Home("parser/_value_keywords.py", "_enforcing_leaves"),
            Home("parser/_value_keywords.py", "can_hold_array"),
            Home("parser/_value_keywords.py", "const_literal_enforces_constraints"),
            Home("parser/_value_keywords.py", "literal_written"),
            Home("model/pydantic_v2/types.py", "DataTypeManager._field_form_kwargs"),
            Home("model/pydantic_v2/types.py", "DataTypeManager.get_data_type"),
            Home("model/pydantic_v2/types.py", "DataTypeManager.get_data_float_type"),
            Home("model/base.py", "DataModelFieldBase._constraints_blocked_by"),
            Home("model/pydantic_base.py", "DataModelField._warned_omissions"),
            Home("model/pydantic_base.py", "DataModelField._normalized_constraint_arguments"),
            Home("model/pydantic_base.py", "DataModelField._untaken_beside_models"),
            Home("model/pydantic_v2/base_model.py", "_pattern_data_type"),
            Home("parser/_value_keywords.py", "has_effective_constraints"),
            Home("parser/_value_keywords.py", "get_array_constraints"),
        ),
    ),
    DecisionHome(
        decision=(
            "the keys deduplication and reuse compare models by (the rendered model and its imports, the rendering "
            "with referenced names masked, the union tags taken before any merge, the referenced models compared "
            "pairwise, the reuse candidate key and the stdout fingerprint; the type alias of an item-count site, an "
            "array that was inline up to 0.89.0, is compared only with other such aliases, so the models those "
            "releases wrote group as they did); rendering for a key writes nothing, as "
            "`model/base.py:DataModel._prepare_dedup_key` first decides a root model's sequence interface and writes "
            "the nullability the field types render; today's disagreeing users are named `legacy-*` rows, replaced by "
            "keys over model facts (#4834, #4816): same-name deduplication leaves union tags out, renders models under "
            "their own class name, redirects root models that name the model of their own name and tells bases apart "
            "by type hint (`legacy-same-name-dedup`), collapse-reuse skips root models, renders each round anew and "
            "tells bases apart by reference (`legacy-collapse-reuse`), tree scope compares root models "
            "(`legacy-tree-scope`), and `--reuse-model` skips root models only under `--collapse-root-models` "
            "(`legacy-reuse-model`); every user but collapse-reuse compares the key the parse's memo kept for a model "
            "in an earlier pass, which only render-cache invalidation drops (`legacy-stale-dedup-keys`); which model "
            "of a group of duplicates stays canonical, keeping its name and its place: a model created to yield its "
            "name yields that place too, a site model in same-name deduplication and a yielding model (a model of a "
            "typed map value) for every other user, recorded once those names are settled"
        ),
        homes=(
            Home("parser/_model_shapes.py", "ModelShapes"),
            Home("parser/_model_shapes.py", "ShapeRow"),
            Home("parser/_model_shapes.py", "ShapeSource"),
            Home("parser/_model_shapes.py", "skips_root_model"),
            Home("parser/_model_shapes.py", "reuse_candidate_key"),
            Home("parser/_model_shapes.py", "stdout_fingerprint"),
            Home("parser/_model_shapes.py", "STALE_DEDUP_KEYS"),
            Home("parser/_model_shapes.py", "ReuseGroups"),
            Home("parser/_model_shapes.py", "ReuseGroups.site_models_last"),
            Home("parser/_model_shapes.py", "ReuseGroups.typed_map_values_last"),
            Home("parser/_model_shapes.py", "ReuseGroups.yields_to"),
            Home("parser/_model_shapes.py", "ReuseGroups.canonical_pair"),
        ),
        core=(
            "`parser/_model_shapes.py`: `ModelShapes` (`ShapeRow`, `ShapeSource`), `skips_root_model`, "
            "`reuse_candidate_key`, `stdout_fingerprint`, `STALE_DEDUP_KEYS`, `ReuseGroups` (`site_models_last`, "
            "`typed_map_values_last`, `yields_to`, `canonical_pair`)"
        ),
        adapters=(
            "`parser/base.py`: `_ModelShapeSource` (the facts); `model/base.py:DataModel._fresh_dedup_key` (writes "
            "what the rendering decides, then renders the key; `get_dedup_key` keeps it in the parse's memo); "
            "`parser/_passes/_shared.py:compared_models` (the models a user compares: those reuse may change, without "
            "the root models its row leaves out); user `parser/_stdout_repair.py:stdout_model_fingerprint` (behind "
            "`Parser._stdout_model_fingerprint`); `parser/_passes/settle.py:settle_yielding_names` (records the plan); "
            "`parser/_passes/modules.py`: user `reuse_model`; `parser/_passes/plans.py`: user "
            "`find_possible_reuse_duplicates`; `parser/_passes/structure.py`: users `delete_duplicate_models` "
            "(`renders_as_duplicate`), `replace_duplicate_name_in_module`, `find_duplicate_models_across_modules`, "
            "`replace_typed_map_value_duplicates`; `parser/_passes/imports.py:record_model_placement`; "
            "`parser/_stdout_repair.py:model_inventory` (behind `Parser.generated_model_inventory`); not yet: L4 "
            "`find_possible_reuse_duplicates` predicts with the first model of a candidate key, without the yield"
        ),
        sites=(
            Home("parser/base.py", "_ModelShapeSource"),
            Home("model/base.py", "DataModel._fresh_dedup_key"),
            Home("model/base.py", "DataModel.get_dedup_key"),
            Home("parser/_passes/_shared.py", "compared_models"),
            Home("parser/_stdout_repair.py", "stdout_model_fingerprint"),
            Home("parser/base.py", "Parser._stdout_model_fingerprint"),
            Home("parser/_passes/settle.py", "settle_yielding_names"),
            Home("parser/_passes/modules.py", "reuse_model"),
            Home("parser/_passes/plans.py", "find_possible_reuse_duplicates"),
            Home("parser/_passes/structure.py", "delete_duplicate_models"),
            Home("parser/_passes/structure.py", "renders_as_duplicate"),
            Home("parser/_passes/structure.py", "replace_duplicate_name_in_module"),
            Home("parser/_passes/structure.py", "find_duplicate_models_across_modules"),
            Home("parser/_passes/structure.py", "replace_typed_map_value_duplicates"),
            Home("parser/_passes/imports.py", "record_model_placement"),
            Home("parser/_stdout_repair.py", "model_inventory"),
        ),
    ),
    DecisionHome(
        decision=(
            "what becomes of a duplicate that deduplication or reuse found, in the slot of the user that found it: it "
            "leaves the output and every user, in any module, names the equal canonical model and records the null of "
            "the schema it named; a duplicate that reuse removes instead of subclassing it keeps the tags of the "
            "unions that named it (`keeps_duplicate_tags`), and the children of a merged model take their bases once "
            "each as the user's row tells them apart (`ShapeRow.base_dedup`); after a merge or a subclass, every model "
            "that inherits the canonical model or the subclass, however distant, and whose bases Python would refuse "
            "(a base twice, an order C3 cannot merge: `model/base.py:python_base_classes`, not for a TypedDict, which "
            "merges its bases itself) lists them once each, descendants first (`settle_python_bases`); reuse removes "
            "an Enum, the model of a typed map value, every duplicate under `--collapse-reuse-models` and, in tree "
            "scope, every duplicate the output's models cannot inherit "
            "(`SUPPORTS_TREE_SCOPE_REUSE_MODEL_INHERITANCE`), and makes any other an empty subclass of the canonical "
            "model in its place, which the users of its module (of every module under single-model splitting) name; "
            "the canonical model records each duplicate merged into it either way, read by "
            "`replace_merged_unique_list_to_set`"
        ),
        homes=(
            Home("parser/_passes/_shared.py", "merge_duplicate"),
            Home("parser/_passes/_shared.py", "removes_reuse_duplicate"),
            Home("parser/_passes/_shared.py", "subclass_duplicate"),
            Home("parser/_passes/_shared.py", "merged_models"),
            Home("parser/_passes/_shared.py", "settle_python_bases"),
            Home("model/base.py", "_REUSE_PATH_SUFFIX"),
            Home("model/base.py", "DataModel.create_reuse_model"),
            Home("_target_binding.py", "_Binder.source"),
            Home("_target_binding.py", "_Binder.reused"),
        ),
        core=(
            "`parser/_passes/_shared.py`: `merge_duplicate`, `removes_reuse_duplicate`, `subclass_duplicate`, "
            "`merged_models`, `settle_python_bases`; the path suffix of the subclass, "
            "`model/base.py:_REUSE_PATH_SUFFIX` (written by `DataModel.create_reuse_model`), and the model it "
            "replaced, `DataModel.reused_reference` (read by `_target_binding.py:_Binder.source` and `_Binder.reused`, "
            "wherever a later step moved either model)"
        ),
        adapters=(
            "`parser/_passes/structure.py`: `delete_duplicate_models` (same-name and collapse-reuse merges), "
            "`replace_typed_map_value_duplicates`, `create_shared_module_from_duplicates`; "
            "`parser/_passes/modules.py`: `reuse_model` (`redirect_removed_reuse_users` records the users whose module "
            "already imported the duplicate, for `import_removed_reuse_canonicals`); not routed: a root model that "
            "names the model of its own name (`_drop_duplicate_root_model`: its users name what its root names, "
            "without a merge)"
        ),
        sites=(
            Home("parser/_passes/structure.py", "delete_duplicate_models"),
            Home("parser/_passes/structure.py", "replace_typed_map_value_duplicates"),
            Home("parser/_passes/structure.py", "create_shared_module_from_duplicates"),
            Home("parser/_passes/modules.py", "reuse_model"),
            Home("parser/_passes/modules.py", "redirect_removed_reuse_users"),
            Home("parser/_passes/structure.py", "_drop_duplicate_root_model"),
        ),
    ),
    DecisionHome(
        decision=(
            "which values of a `const` or an `enum` meet the length, bound and `multipleOf` keywords beside them that "
            "the field does not write (the `Literal` of a Pydantic field or root that writes a keyword keeps its "
            "values), and which keywords the values decide, so msgspec leaves them out silently; no value left keeps "
            "them as written with the unsatisfiable warning"
        ),
        homes=(
            Home("_constraint_applicability.py", "literal_check"),
            Home("_constraint_applicability.py", "LITERAL_DECIDABLE_KEYWORDS"),
        ),
        core="`_constraint_applicability.py`: `literal_check`, `LITERAL_DECIDABLE_KEYWORDS`",
        adapters=(
            "`parser/jsonschema.py:JsonSchemaParser`: `_literal_view` (`parse_enum`, `parse_enum_as_literal`), warning "
            "`_admitted_or_warn`; `parser/_value_keywords.py`: `check_literal_const` (`get_const_data_type`), "
            "`combined_values_admitted` (`extract_const_enum_from_combined`), `literal_written` (the const of "
            "`_get_data_type`, the enum and const of `_parse_root_type_with_context`); "
            "`parser/_intersection.py:warn_unsatisfiable`; "
            "`model/msgspec.py:DataModelField._literal_decided_meta_keywords`; not yet: `pattern` (the static "
            "pattern scanner) and the literals of `_build_lightweight_type`"
        ),
        sites=(
            Home("parser/jsonschema.py", "JsonSchemaParser"),
            Home("parser/jsonschema.py", "JsonSchemaParser._literal_view"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_enum"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_enum_as_literal"),
            Home("parser/jsonschema.py", "JsonSchemaParser._admitted_or_warn"),
            Home("parser/_value_keywords.py", "check_literal_const"),
            Home("parser/_value_keywords.py", "get_const_data_type"),
            Home("parser/_value_keywords.py", "combined_values_admitted"),
            Home("parser/_value_keywords.py", "extract_const_enum_from_combined"),
            Home("parser/_value_keywords.py", "literal_written"),
            Home("parser/jsonschema.py", "JsonSchemaParser._get_data_type"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_root_type_with_context"),
            Home("parser/_intersection.py", "warn_unsatisfiable"),
            Home("model/msgspec.py", "DataModelField._literal_decided_meta_keywords"),
            Home("parser/jsonschema.py", "JsonSchemaParser._build_lightweight_type"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether a JSON value is a number: an int or a float, a `bool` being none (the generated runtime keeps its "
            "own copy, `_runtime/model_codecs/media.py:json_number`, as it never imports the generator)"
        ),
        homes=(Home("_constraint_applicability.py", "json_number"),),
        core="`_constraint_applicability.py:json_number`",
        adapters=(
            "the values the literal check compares with a bound or `multipleOf` "
            "(`_constraint_applicability.py:_meets`), the zero bounds and the `multipleOf` divisors the recommender "
            "counts (`_recommend/_signals.py`: `_redundant_constraint`, `_Walker.count_validation_keywords`) and a "
            "protocol helper's seconds (`_client/protocols.py:_Validator.seconds`); left out: an integer "
            "(`json_integer`, the row below) and the dispatch that tells a bool before a number "
            "(`_option_names.py:toml_text`); not yet: this test and `json_integer` read from the kind of a value "
            "(`_json_schema.py:literal_value_kind`, which the JSON equality of a number reads), as the constraint leaf "
            "does not import that leaf"
        ),
        sites=(
            Home("_constraint_applicability.py", "_meets"),
            Home("_recommend/_signals.py", "_redundant_constraint"),
            Home("_recommend/_signals.py", "_Walker.count_validation_keywords"),
            Home("_client/protocols.py", "_Validator.seconds"),
            Home("_constraint_applicability.py", "json_integer"),
            Home("_option_names.py", "toml_text"),
            Home("_json_schema.py", "literal_value_kind"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether a JSON value is an integer: an int, a `bool` being none (the generated runtime keeps its own "
            "copy, `_runtime/model_codecs/media.py:json_integer`)"
        ),
        homes=(Home("_constraint_applicability.py", "json_integer"),),
        core="`_constraint_applicability.py:json_integer`",
        adapters=(
            "the counts `minContains` and `maxContains` give (`parser/_value_keywords.py`: "
            "`get_contains_count_constraints`, `contains_false_requires_match`), the int constraint values the schema "
            "models keep exact (`types.py:_preserve_float_constraint_precision`) and a builtin formatter line length "
            "(`_builtin_formatter.py:_is_valid_builtin_line_length`); left out: an int of exactly the `int` class "
            "(`type(value) is int`: `_client/protocols.py`, `_client/config.py`, `parser/avro.py`, "
            "`python_literal.py`, `_target_binding.py`), which refuses the subclasses of `int` too"
        ),
        sites=(
            Home("parser/_value_keywords.py", "get_contains_count_constraints"),
            Home("parser/_value_keywords.py", "contains_false_requires_match"),
            Home("types.py", "_preserve_float_constraint_precision"),
            Home("_builtin_formatter.py", "_is_valid_builtin_line_length"),
            Home("_client/protocols.py"),
            Home("_client/config.py"),
            Home("parser/avro.py"),
            Home("python_literal.py"),
            Home("_target_binding.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the least or greatest integer that a `ge`, `gt`, `le` or `lt` bound admits, as the `ge` or `le` that says "
            "so: the ceiling or floor of the bound, one more or less for an exclusive one; the schema models read the "
            'decimal that is written (`arithmetic="exact"`), the check of a msgspec union reads the float it is '
            '(`arithmetic="float"`, row `legacy-msgspec-float-bounds`), or the decimal the schema writes under '
            "`--read-msgspec-union-bounds-exactly` (`union_bound_arithmetic`)"
        ),
        homes=(
            Home("_constraint_applicability.py", "integer_bound"),
            Home("_constraint_applicability.py", "union_bound_arithmetic"),
        ),
        core="`_constraint_applicability.py`: `integer_bound`, `union_bound_arithmetic`",
        adapters=(
            "`types.py:normalize_integer_constraint` (the bounds of an integer field), "
            "`model/msgspec.py:_integer_bounds` (which integers the int member of a union admits beside a float "
            "member, over `_UnionLeaf.read`: the `Meta` values or every schema bound of a side that `Meta` takes, "
            "together, `DataModelField._union_meta_constraints`; the options reach "
            "`DataModelField.unconstrain_union_members` from "
            "`parser/_passes/plans.py:unconstrain_union_members_in_modules`)"
        ),
        sites=(
            Home("types.py", "normalize_integer_constraint"),
            Home("model/msgspec.py", "_integer_bounds"),
            Home("model/msgspec.py", "_UnionLeaf.read"),
            Home("model/msgspec.py", "DataModelField._union_meta_constraints"),
            Home("model/msgspec.py", "DataModelField.unconstrain_union_members"),
            Home("parser/_passes/plans.py", "unconstrain_union_members_in_modules"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether the instances of a model hash by value: a set item model keeps the builtin hash of its class "
            "where the backend lets set item models keep it and no `--additional-imports` may shadow the builtin "
            "annotations, the first class of its chain (the model and the generated models it inherits from, walked "
            "over the reference graph) that sets `frozen` freezes it, every class of the chain is the backend's own "
            "class without a custom base class, class code or custom templates, and the values validated for each "
            "field hash as builtin values; any other set item model hashes by identity (`__hash__ = object.__hash__`); "
            "a dict key that names a root model whose chain does not freeze it, and that checks nothing beyond its "
            "root type, takes the root type; an array can be a set where some value of its items hashes (a list, "
            "sequence, set, dict or mapping as it is written takes none, a tuple one where each position does, a union "
            "one where a member does, None and any other type do, a model whose instances never hash none: a "
            "TypedDict, a dataclass or msgspec Struct that compares by value without being frozen, without "
            "`unsafe_hash`, class code, a custom base class or templates, and a RootModel written as an alias)"
        ),
        homes=(
            Home("model/_decisions/hashability.py", "set_items_may_keep_builtin_hash"),
            Home("model/_decisions/hashability.py", "keeps_builtin_hash"),
            Home("model/_decisions/hashability.py", "frozen_by_config"),
            Home("model/_decisions/hashability.py", "HashClassFacts"),
            Home("model/_decisions/hashability.py", "dict_key_takes_root_type"),
            Home("model/_decisions/hashability.py", "field_value_hashes"),
            Home("model/_decisions/hashability.py", "value_hashes"),
            Home("model/_decisions/hashability.py", "ValueHashFacts"),
            Home("model/_decisions/hashability.py", "set_items_can_hash"),
            Home("model/_decisions/hashability.py", "SetItemFacts"),
        ),
        core=(
            "`model/_decisions/hashability.py`: `set_items_may_keep_builtin_hash`, `keeps_builtin_hash`, "
            "`frozen_by_config` (`HashClassFacts`), `dict_key_takes_root_type`, `field_value_hashes`, `value_hashes` "
            "(`ValueHashFacts`), `set_items_can_hash` (`SetItemFacts`), `instances_never_hash`"
        ),
        adapters=(
            "`parser/_passes/_shared.py:_set_item_facts` (for `unique_items_plan`, which reads a type alias as its "
            "value and a model as a value that hashes, and for `unhashable_unique_sets`, which reads the class of a "
            "model once root models collapsed); `model/base.py`: `set_item_models_keeping_builtin_hash` (for "
            "`parser/_passes/render_prep.py:mark_set_item_models_hashable`), `DataModel._keys_take_root_type` (for "
            "`parser/_passes/modules.py:type_dict_keys_by_root_types`), `DataModel._hash_class_facts`, "
            "`DataModel._instances_never_hash` (rows `typed_dict.py:TypedDict`, `dataclass.py:_DataclassReuseMixin` "
            "for both dataclass backends, `msgspec.py:Struct` over its struct chain and the generic base class "
            "configuration, `pydantic_v2/root_model_type_alias.py:RootModelTypeAlias`); rows "
            "`model/pydantic_v2/base_model.py`: `BaseModel._hash_class_facts` (`frozen` of its `ConfigDict`, the "
            "builtin class `_uses_builtin_hash_implementation`, also of `RootModel`), `_validated_value_hashes` (its "
            "exact `DataModelField` class); capabilities `DataModel._FIELD_VALUE_HASHES` (None: every set item model "
            "hashes by identity; Pydantic v2 `BaseModel`: `_validated_value_hashes`) and "
            "`DataModel._DICT_KEYS_TAKE_ROOT_TYPE` (Pydantic v2 `RootModel`)"
        ),
        sites=(
            Home("parser/_passes/_shared.py", "_set_item_facts"),
            Home("parser/_passes/_shared.py", "unique_items_plan"),
            Home("parser/_passes/_shared.py", "unhashable_unique_sets"),
            Home("model/base.py", "set_item_models_keeping_builtin_hash"),
            Home("parser/_passes/render_prep.py", "mark_set_item_models_hashable"),
            Home("model/base.py", "DataModel._keys_take_root_type"),
            Home("parser/_passes/modules.py", "type_dict_keys_by_root_types"),
            Home("model/base.py", "DataModel._hash_class_facts"),
            Home("model/base.py", "DataModel._instances_never_hash"),
            Home("model/typed_dict.py", "TypedDict"),
            Home("model/pydantic_v2/root_model_type_alias.py", "RootModelTypeAlias"),
            Home("model/pydantic_v2/base_model.py", "BaseModel._hash_class_facts"),
            Home("model/pydantic_v2/base_model.py", "BaseModel._uses_builtin_hash_implementation"),
            Home("model/pydantic_v2/base_model.py", "_validated_value_hashes"),
            Home("model/pydantic_v2/base_model.py", "DataModelField"),
            Home("model/pydantic_v2/base_model.py", "BaseModel"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether `--collapse-root-models` keeps a root model named at a site that names it or inlines it there (it "
            "stays named without a root field, when a model-level `--type-overrides` key replaces it, when circular, "
            "with runtime object rules, as the item of a set or frozen set where no value of its root type hashes, as "
            "the `Literal` type alias of an enum under `--keep-enum-literal-aliases`, with constraints its holder's "
            "type would not carry (the type alias of an item-count site, an array that was inline there up to 0.89.0, "
            "keeps its counts itself, so only the models it reaches count), with a constrained map, under "
            "`--field-constraints` with constraints beside a dict, union or list of the holder (unless, under "
            "`--collapse-constrained-root-models-as-annotated`, the member, item or value it is takes a string type "
            "that writes them in `Annotated`), where its root type names a model other than such an alias and no name "
            "strategy is set, and as a list item where the parser does not collapse those, which GraphQL does not), "
            "and whether the later holders of a root model that the `parent` strategy collapsed at its first holder "
            "name its inner model too; row `legacy-predicted-rename` keeps such a root model for its later holders, as "
            "0.86.0 did, where the run renames colliding types and both models are written in one module, until "
            "`settle_kept_parent_roots` reads the final class names"
        ),
        homes=(
            Home("model/_decisions/collapse_eligibility.py", "keeps_root_model_named"),
            Home("model/_decisions/collapse_eligibility.py", "CollapseSiteFacts"),
            Home("model/_decisions/collapse_eligibility.py", "KeepRule"),
            Home("model/_decisions/collapse_eligibility.py", "later_holders_follow"),
            Home("model/_decisions/collapse_eligibility.py", "LaterHolderFacts"),
        ),
        core=(
            "`model/_decisions/collapse_eligibility.py`: `keeps_root_model_named` (`CollapseSiteFacts`, `KeepRule`), "
            "`later_holders_follow` (`LaterHolderFacts`)"
        ),
        adapters=(
            "`parser/_passes/modules.py`: `collapse_site_facts` (for `collapse_root_models_in_scope`; the string type "
            "of a member that writes the constraints through `annotated_member_type`; the hashability of the root type "
            "through `parser/_passes/_shared.py:takes_hashable_value`), `first_holder_facts`, "
            "`settle_kept_parent_roots`; capability `Parser._collapses_list_item_root_models` (False in "
            "`parser/graphql.py`)"
        ),
        sites=(
            Home("parser/_passes/modules.py", "collapse_site_facts"),
            Home("parser/_passes/modules.py", "collapse_root_models_in_scope"),
            Home("parser/_passes/modules.py", "annotated_member_type"),
            Home("parser/_passes/_shared.py", "takes_hashable_value"),
            Home("parser/_passes/modules.py", "first_holder_facts"),
            Home("parser/_passes/modules.py", "settle_kept_parent_roots"),
            Home("parser/graphql.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "how the constraints of two fields overlay (the overriding values win): the class of the result comes from "
            "the first given argument (inherited field copies) or the first `ConstraintsBase` (collapsing root models, "
            "`ConstraintsBase.merge_constraints`)"
        ),
        homes=(Home("model/base.py", "_overlay_constraints"),),
        core="`model/base.py:_overlay_constraints`, rows `first-given` and `first-constraints`",
        adapters=(
            "`parser/_field_copies.py:_copy_resolved_inherited_field`; "
            "`parser/_passes/modules.py:collapse_root_models_in_scope` through `ConstraintsBase.merge_constraints`"
        ),
        sites=(
            Home("parser/_field_copies.py", "_copy_resolved_inherited_field"),
            Home("parser/_passes/modules.py", "collapse_root_models_in_scope"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether a root field constrains its value: row `declared` when its constraints set a value "
            "(`ConstraintsBase.has_constraints`), row `with-consumed` also when its type or a direct member of it "
            "writes a keyword itself"
        ),
        homes=(
            Home("model/base.py", "_has_root_field_constraints"),
            Home("model/base.py", "_RootConstraintRow"),
        ),
        core="`model/base.py:_has_root_field_constraints` (`_RootConstraintRow`)",
        adapters=(
            "`declared`: `parser/_passes/modules.py:collapse_site_facts` (`CollapseSiteFacts.constrained_root_field`), "
            "`_target_binding.py:_Binder.constrains` (an alias or root model the target checks); `with-consumed`: "
            "`collapse_site_facts` (`constrained_root`), `parser/base.py:Parser._get_duplicate_root_reference` (under "
            "`--use-site-models-for-constrained-refs`); another rule: the constraints but `uniqueItems` a root field "
            "writes (`parser/_passes/modules.py:_root_constraint_keywords`, "
            "`model/pydantic_v2/root_model_type_alias.py:_constrained_root_model`)"
        ),
        sites=(
            Home("parser/_passes/modules.py", "collapse_site_facts"),
            Home("_target_binding.py", "_Binder.constrains"),
            Home("parser/base.py", "Parser._get_duplicate_root_reference"),
            Home("model/pydantic_v2/root_model_type_alias.py", "_constrained_root_model"),
        ),
    ),
    DecisionHome(
        decision=(
            "the kind of a JSON value (a bool is no int) and its JSON type, none for a value outside JSON, which only "
            "a schema built in Python holds; row of `json_type`: outside JSON a Decimal is a number and any other "
            "value an array"
        ),
        homes=(
            Home("_json_schema.py", "literal_value_kind"),
            Home("_json_schema.py", "json_value_type"),
            Home("_json_schema.py", "json_type"),
        ),
        core="`_json_schema.py`: `literal_value_kind`, `json_value_type`, `json_type`",
        adapters=(
            "`model/_decisions/enum_kind.py` (keeps `literal_value_kind` and `json_type` under their names for the "
            "readers of the row below); `_json_schema.py:json_value_key` (the kind of an instance of a scalar "
            "subclass; a value of a scalar type itself is keyed by the table `_KEY_KINDS`, where a bool and a number "
            "are two entries); `parser/_schema_reading.py:_get_json_value_type` (`json_value_type`: a value outside "
            "JSON has no type); `_target_binding.py:_Models.symbols` and "
            "`parser/jsonschema.py:JsonSchemaParser._with_open_enum_type` (`json_type` of enum values); the literals "
            "of a protocol helper (`json_type`: `_client/pagination.py:_Pages` `ends` and `bindings`, "
            "`_client/polling.py:_Polls.states`); the str values of a `Literal`, which msgspec counts as its str "
            "member: `model/_decisions/msgspec_str_like.py:msgspec_str_like_leaf` (read by the msgspec decoding "
            "warnings and `--merge-msgspec-str-like-union-members`) and `parser/_str_like_unions.py:_other_literals` "
            "(the values that merge keeps); not yet: `_constraint_applicability.py` (`json_number`, `json_integer`, "
            "the rows above)"
        ),
        sites=(
            Home("_json_schema.py", "literal_value_kind"),
            Home("_json_schema.py", "json_type"),
            Home("_json_schema.py", "json_value_key"),
            Home("parser/_schema_reading.py", "_get_json_value_type"),
            Home("_json_schema.py", "json_value_type"),
            Home("_target_binding.py", "_Models.symbols"),
            Home("parser/jsonschema.py", "JsonSchemaParser._with_open_enum_type"),
            Home("_client/pagination.py", "_Pages"),
            Home("_client/pagination.py", "_Pages.ends"),
            Home("_client/pagination.py", "_Pages.bindings"),
            Home("_client/polling.py", "_Polls.states"),
            Home("_constraint_applicability.py", "json_number"),
            Home("_constraint_applicability.py", "json_integer"),
            Home("model/_decisions/enum_kind.py"),
            Home("model/_decisions/msgspec_str_like.py", "msgspec_str_like_leaf"),
            Home("parser/_str_like_unions.py", "_other_literals"),
        ),
    ),
    DecisionHome(
        decision=(
            "the kind of a const or enum value, the kinds of the members an enum class holds (its value base, or the "
            "values as written without the aliases of earlier members), and whether a `Literal` or a msgspec tag holds "
            "it, so which values a type is written in place of, admitting others"
        ),
        homes=(
            Home("model/_decisions/enum_kind.py", "enum_member_kinds"),
            Home("model/_decisions/enum_kind.py", "is_literal_kind"),
            Home("model/_decisions/enum_kind.py", "is_tag_kind"),
            Home("model/_decisions/enum_kind.py", "values_no_literal_holds"),
        ),
        core=(
            "`model/_decisions/enum_kind.py`: `enum_member_kinds`, `is_literal_kind`, `is_tag_kind`, "
            "`values_no_literal_holds`, over `literal_value_kind` (the row above)"
        ),
        adapters=(
            "`parser/_value_keywords.py`: `partition_enum_values`, `get_const_data_type`, "
            "`extract_const_enum_from_combined`, `get_scalar_data_type_from_json_value` (the type of each scalar "
            "kind); `parser/jsonschema.py:JsonSchemaParser._widened` (`values_no_literal_holds`, kept for "
            "`parser/_passes/imports.py:widened_values_warning`); `parser/_passes/imports.py`: `_msgspec_enum_kinds` "
            "(`enum_member_kinds`, read by `_msgspec_enum_clashes`, `_msgspec_member_slots`, "
            "`_msgspec_null_enum_message`, `_msgspec_mixed_enum_message` and the decision of "
            "`--merge-msgspec-str-like-union-members`, `parser/_str_like_unions.py:_Classifier.slot`); discriminator "
            "tags through "
            "`model/_decisions/tag_value.py` (`field_tag`, `enum_tag_literal`, `union_tag_values`); the type of a "
            "const through `model/_decisions/literal_default.py:const_type`"
        ),
        sites=(
            Home("parser/_value_keywords.py", "partition_enum_values"),
            Home("parser/_value_keywords.py", "get_const_data_type"),
            Home("parser/_value_keywords.py", "extract_const_enum_from_combined"),
            Home("parser/_value_keywords.py", "get_scalar_data_type_from_json_value"),
            Home("parser/jsonschema.py", "JsonSchemaParser._widened"),
            Home("parser/_passes/imports.py", "widened_values_warning"),
            Home("parser/_passes/imports.py", "_msgspec_enum_kinds"),
            Home("parser/_passes/imports.py", "_msgspec_enum_clashes"),
            Home("parser/_passes/imports.py", "_msgspec_member_slots"),
            Home("parser/_passes/imports.py", "_msgspec_null_enum_message"),
            Home("parser/_passes/imports.py", "_msgspec_mixed_enum_message"),
            Home("model/_decisions/tag_value.py", "field_tag"),
            Home("model/_decisions/tag_value.py", "enum_tag_literal"),
            Home("model/_decisions/tag_value.py", "union_tag_values"),
            Home("model/_decisions/literal_default.py", "const_type"),
        ),
    ),
    DecisionHome(
        decision=(
            "the JSON equality of two values: a bool is no number, `1 == 1.0`, NaN equals NaN, arrays and objects item "
            "by item, an instance of a subclass of `int`, `float` or `str` (an `IntEnum` or `StrEnum` member of a "
            "schema built in Python) as the value of that type it holds, a value outside JSON equal to nothing; it is "
            "the equality of their keys, by which values are also found; the rows of enum member lookups are the "
            "parameters of the key: `legacy-exact-type-key` (`exact_types`) and `legacy-python-equality-fallback` "
            "(`python_items`)"
        ),
        homes=(
            Home("_json_schema.py", "json_value_key"),
            Home("_json_schema.py", "json_values_equal"),
        ),
        core="`_json_schema.py`: `json_value_key`, `json_values_equal`",
        adapters=(
            "`model/enum.py:Enum` (`find_member`, `_index_members`: the members by `_member_key`, the key with both "
            "rows on); `parser/_intersection.py` (`json_values_equal` under its name, read by the enum and const "
            "intersections, and `_retained_indices` and `enum_metadata`, which find the values of one enum among the "
            "keys of another), `parser/_inherited_schemas.py`, `parser/_schema_validators.py`, "
            "`parser/_all_of_values.py`, `parser/jsonschema.py`; a const is still compared with each value of an enum "
            "(`json_values_equal`, two keys per pair) in `parser/_intersection.py:_Reconciler._const`, "
            "`parser/_all_of_values.py:_enum_narrowed_by_part_consts` and "
            "`parser/jsonschema.py:JsonSchemaParser._enum_positions_outside_const`; not routed: the JSON literal check "
            "a generated root model carries writes its own key "
            "(`model/pydantic_v2/root_model.py:RootModel.add_literal_validation`), as generated code cannot import "
            "this module"
        ),
        sites=(
            Home("model/enum.py", "Enum"),
            Home("model/enum.py", "Enum.find_member"),
            Home("model/enum.py", "Enum._index_members"),
            Home("parser/_intersection.py", "_retained_indices"),
            Home("parser/_intersection.py", "enum_metadata"),
            Home("parser/_intersection.py", "_Reconciler._const"),
            Home("parser/_all_of_values.py", "_enum_narrowed_by_part_consts"),
            Home("parser/jsonschema.py", "JsonSchemaParser._enum_positions_outside_const"),
            Home("model/pydantic_v2/root_model.py", "RootModel.add_literal_validation"),
            Home("parser/_inherited_schemas.py"),
            Home("parser/_schema_validators.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "discriminator tags: which value tags a union variant (L1 branch literals, L5 tag fields, L6 redeclared "
            "tag fields), which field holds the tag property, which schemas a mapping path names, which value a "
            "mapping key names, how tags are told apart, and the `tag` and `tag_field` of a msgspec Struct; today's "
            "disagreements are named `legacy-*` rows, each deleted by its fix: the L6 redeclared tag field read only "
            "as a one-value `Literal` (`legacy-literal-only`), the tag field of a nested or inline variant read "
            "without the root model its type names (`legacy-nested-root-model-tag`), `1 == True` at L5 and L6 but not "
            "at L1 (`legacy-python-equality`), the redeclared tag field picked in string-hash order when both its "
            "names are tag fields (`legacy-hash-order`), msgspec `tag_field` by Python name without "
            "`--use-msgspec-tagged-unions` (`legacy-python-name`), the L1 tag property replacing a declared one "
            "(`legacy-literal-property-replaces-declared`), the wider mapping-path keys of the msgspec layout "
            "prediction (`legacy-any-key`), and a variant a mapping names taking only its keys, not the other values "
            "its tag field admits, without `--use-unmapped-variant-tag-values` (`legacy-mapped-tags-only`); a variant "
            "whose tag field admits several values takes those no mapping key and no other variant claim "
            "(`own-field-values`), a msgspec Struct, which takes one tag, only where they are one"
        ),
        homes=(
            Home("model/_decisions/tag_value.py", "field_tag"),
            Home("model/_decisions/tag_value.py", "variant_tags"),
            Home("model/_decisions/tag_value.py", "tag_field_matches"),
            Home("model/_decisions/tag_value.py", "tag_field_kept"),
            Home("model/_decisions/tag_value.py", "redeclared_tag_field"),
            Home("model/_decisions/tag_value.py", "mapped_tags"),
            Home("model/_decisions/tag_value.py", "mapping_path_keys"),
            Home("model/_decisions/tag_value.py", "schema_path_keys"),
            Home("model/_decisions/tag_value.py", "mapping_key_tag"),
            Home("model/_decisions/tag_value.py", "tag_key"),
            Home("model/_decisions/tag_value.py", "same_tag"),
            Home("model/_decisions/tag_value.py", "same_tags"),
            Home("model/_decisions/tag_value.py", "distinct_tags"),
            Home("model/_decisions/tag_value.py", "union_tag_values"),
            Home("model/_decisions/tag_value.py", "tagged_union_discriminator"),
            Home("model/_decisions/tag_value.py", "struct_tag_field"),
            Home("model/_decisions/tag_value.py", "struct_tag_conflicts"),
            Home("model/_decisions/tag_value.py", "struct_tag_clash"),
            Home("model/_decisions/tag_value.py", "struct_tag_options"),
            Home("model/_decisions/tag_value.py", "struct_discriminator_tag"),
            Home("model/_decisions/tag_value.py", "enum_tag_literal"),
            Home("model/_decisions/tag_value.py", "enum_tag_members"),
            Home("model/_decisions/tag_value.py", "enum_tag_as_values"),
        ),
        core=(
            "`model/_decisions/tag_value.py`: `field_tag`, `variant_tags`, `tag_field_matches`, `tag_field_kept`, "
            "`redeclared_tag_field`, `mapped_tags` (`mapping_path_keys`, `schema_path_keys`), `mapping_key_tag`, "
            "`tag_key` (`same_tag`, `same_tags`, `distinct_tags`), `union_tag_values`, `tagged_union_discriminator`, "
            "`struct_tag_field`, `struct_tag_conflicts`, `struct_tag_clash`, `struct_tag_options`, "
            "`struct_discriminator_tag`, `enum_tag_literal`, `enum_tag_members`, `enum_tag_as_values`"
        ),
        adapters=(
            "`parser/_passes/_shared.py`: `_get_discriminator_field_value` with `_FieldTagFacts` "
            "(`_FieldTagFacts.values`: every value a tag field admits), `_tag_field_matches`, `_keeps_instance_field`, "
            "`_get_mapping_discriminator_values`, `enum_discriminator_literal` (also as "
            "`parser/base.py:Parser._get_enum_discriminator_literal`), `enum_discriminator_members` (read with "
            "`enum_tag_as_values` by L6 `write_enum_member_tags_as_values` in `parser/_passes/cross_module.py`); "
            "`parser/_tag_plan.py`: `tags_of_variant` with `_VariantTagFacts` (`UnionView.tags`), `site_tag_values` "
            "(the values each variant of a union admits, read before the union writes any); read by L3 "
            "`declared_union_tags`, L4 `possible_tag_names` and the L5 decisions `site_validity`, "
            "`iter_tag_field_decisions` and `tag_msgspec_unions` (`parser/_tag_plan.py`), which L5 "
            "`apply_discriminator_type` (`parser/_passes/modules.py`) writes, and the L6 decision "
            "`iter_struct_tag_decisions` (`parser/_tag_plan.py`), which L6 `tag_fields_of_tagged_bases` "
            "(`parser/_passes/cross_module.py`) writes, `parser/_passes/imports.py:_msgspec_refused_structs`; "
            "`parser/_union_tags.py:get_single_literal_value` (also as `JsonSchemaParser._get_single_literal_value`) "
            "with `_SchemaTagFacts`, `get_union_variant_literal_values`, `get_tagged_union_field_values`, "
            "`set_tagged_union_discriminator` (L1); `model/msgspec.py:Struct._tag_options`, "
            "`Struct.discriminator_tag`; not here: tag defaults and nullability (next row) and tag patterns "
            "(`_drop_discriminator_tag_patterns`)"
        ),
        sites=(
            Home("parser/_passes/_shared.py", "_get_discriminator_field_value"),
            Home("parser/_passes/_shared.py", "_FieldTagFacts"),
            Home("parser/_passes/_shared.py", "_FieldTagFacts.values"),
            Home("parser/_passes/_shared.py", "_tag_field_matches"),
            Home("parser/_passes/_shared.py", "_keeps_instance_field"),
            Home("parser/_passes/_shared.py", "_get_mapping_discriminator_values"),
            Home("parser/_passes/_shared.py", "enum_discriminator_literal"),
            Home("parser/base.py", "Parser._get_enum_discriminator_literal"),
            Home("parser/_passes/_shared.py", "enum_discriminator_members"),
            Home("parser/_passes/cross_module.py", "write_enum_member_tags_as_values"),
            Home("parser/_tag_plan.py", "tags_of_variant"),
            Home("parser/_tag_plan.py", "_VariantTagFacts"),
            Home("parser/_tag_plan.py", "UnionView.tags"),
            Home("parser/_tag_plan.py", "site_tag_values"),
            Home("parser/_tag_plan.py", "declared_union_tags"),
            Home("parser/_tag_plan.py", "possible_tag_names"),
            Home("parser/_tag_plan.py", "site_validity"),
            Home("parser/_tag_plan.py", "iter_tag_field_decisions"),
            Home("parser/_tag_plan.py", "tag_msgspec_unions"),
            Home("parser/_passes/modules.py", "apply_discriminator_type"),
            Home("parser/_tag_plan.py", "iter_struct_tag_decisions"),
            Home("parser/_passes/cross_module.py", "tag_fields_of_tagged_bases"),
            Home("parser/_passes/imports.py", "_msgspec_refused_structs"),
            Home("parser/_union_tags.py", "get_single_literal_value"),
            Home("parser/_union_tags.py", "_SchemaTagFacts"),
            Home("parser/_union_tags.py", "get_union_variant_literal_values"),
            Home("parser/_union_tags.py", "get_tagged_union_field_values"),
            Home("parser/_union_tags.py", "set_tagged_union_discriminator"),
            Home("model/msgspec.py", "Struct._tag_options"),
            Home("model/msgspec.py", "Struct.discriminator_tag"),
            Home("parser/_passes/modules.py", "_drop_discriminator_tag_patterns"),
        ),
    ),
    DecisionHome(
        decision=(
            "the tag plan: which fields are union sites (a discriminator object naming a property), and what the tag "
            "passes recorded in a parse (the tags each variant keeps from the first union that tags it, or under "
            "`--merge-shared-variant-tags` every tag the unions give it, read from every union once the variants it "
            "nests have tags, the inline variants that take the tag they inherit, the fields a union with a reused "
            "variant annotates, the msgspec unions that wait for later modules, what became of the structs below "
            "tagged bases, and the union tags reuse compares); each pass keeps its slot, so today's timing is named "
            "`legacy-*` rows, each kept for good as the default output (fix `keep` in the table below): a union is "
            "tagged when its module is processed (`legacy-decide-in-union-module`), a variant keeps the tags of the "
            "first union that tags it (`legacy-tag-from-earlier-union`), the inline variants are decided at the first "
            "collapse or apply that asks (`legacy-inline-variants-at-first-ask`), L4 layout flattening reads a "
            "superset of the msgspec tag fields (`legacy-predicted-tag-names`), and the union `--collapse-root-models` "
            "inlines as list items is checked by the Python names of its variants' tag fields where L5 checks one that "
            "writes `discriminator=` by the schema's names (`legacy-collapse-python-name-mode`); the L5 decisions, "
            "which the passes write through the store in the order they read them: whether a union can be tagged, the "
            "Python name of its tag field, the tags of each variant and the form each tag field takes, which msgspec "
            "unions take struct tags, and which `discriminator=` Pydantic cannot build once every module wrote its "
            "tags (the lone variant of a union without a tag, a variant whose root has a default, two choices that "
            "hold one tag in the tag fields that were written); the L6 decision, which L6 writes the same way, base by "
            "base in the order of the reference graph: which msgspec structs below tagged bases take the tags their "
            "fields redeclare, opt out of the tag they inherit, or declare the fields of their bases; the class "
            "constants of a backend these read are the rows of `TagCapabilities` (`variant_takes_tag`, "
            "`writes_argument`, `struct_tags`, `bool_tags`, `literal_holds_members`, `flattens_layouts`), a variant's "
            "from its own class"
        ),
        homes=(
            Home("parser/_tag_plan.py", "UnionSite"),
            Home("parser/_tag_plan.py", "union_site"),
            Home("parser/_tag_plan.py", "iter_union_sites"),
            Home("parser/_tag_plan.py", "TagPlan"),
            Home("parser/_tag_plan.py", "VariantTags"),
            Home("parser/_tag_plan.py", "UnionTagDecision"),
            Home("parser/_tag_plan.py", "TagPlan.inline_variant_tags"),
            Home("parser/_tag_plan.py", "TagPlan.merged_variant_tags"),
            Home("parser/_tag_plan.py", "TagPlan.record_variant_tags"),
            Home("parser/_tag_plan.py", "TagPlan.record_collapsed_tags"),
            Home("parser/_tag_plan.py", "TagPlan.record_settled"),
            Home("parser/_tag_plan.py", "TagCapabilities"),
            Home("parser/_tag_plan.py", "tag_capabilities"),
            Home("parser/_tag_plan.py", "site_names"),
            Home("parser/_tag_plan.py", "shared_site_names"),
            Home("parser/_tag_plan.py", "field_namer"),
            Home("parser/_tag_plan.py", "_SiteReading"),
            Home("parser/_tag_plan.py", "_site_reading"),
            Home("parser/_tag_plan.py", "_SITE_MODES"),
            Home("parser/_tag_plan.py", "site_mode"),
            Home("parser/_tag_plan.py", "collapsed_item_site"),
            Home("parser/_tag_plan.py", "_tag_fields_of"),
            Home("parser/_tag_plan.py", "_own_tag_fields"),
            Home("parser/_tag_plan.py", "_inherited_tag_fields"),
            Home("parser/_tag_plan.py", "TagFieldOrder"),
            Home("parser/_tag_plan.py", "_inherited_models"),
            Home("model/base.py", "_walk_bases_first"),
            Home("model/base.py", "DataModel.iter_all_fields"),
            Home("parser/_tag_plan.py", "site_validity"),
            Home("parser/_tag_plan.py", "has_any_variant"),
            Home("parser/_tag_plan.py", "nested_variant_tags"),
            Home("parser/_tag_plan.py", "UnionView"),
            Home("parser/_tag_plan.py", "TagReading"),
            Home("parser/_tag_plan.py", "iter_tag_field_decisions"),
            Home("parser/_tag_plan.py", "KeptTagField"),
            Home("parser/_tag_plan.py", "RetypedTagField"),
            Home("parser/_tag_plan.py", "mapped_struct_tag"),
            Home("parser/_tag_plan.py", "appended_takes_struct_tag"),
            Home("parser/_tag_plan.py", "tag_msgspec_unions"),
            Home("parser/_tag_plan.py", "MsgspecUnion"),
            Home("parser/_tag_plan.py", "settle_site"),
            Home("parser/_tag_plan.py", "iter_unbuildable_inlined_unions"),
            Home("parser/_tag_plan.py", "_settle_choices"),
            Home("parser/_tag_plan.py", "_shares_written_tag"),
            Home("parser/_tag_plan.py", "_variant_carries_tag"),
            Home("parser/_tag_plan.py", "_class_tag_field"),
            Home("parser/_tag_plan.py", "iter_struct_tag_decisions"),
            Home("parser/_tag_plan.py", "StructTagDecision"),
            Home("parser/_tag_plan.py", "declared_union_tags"),
            Home("parser/_tag_plan.py", "possible_tag_names"),
        ),
        core=(
            "`parser/_tag_plan.py`: `UnionSite`, `union_site`, `iter_union_sites`, `TagPlan` (`VariantTags`, "
            "`UnionTagDecision`, `inline_variant_tags`, `merged_variant_tags`, `record_variant_tags`, "
            "`record_collapsed_tags`, `record_settled`), `TagCapabilities` (`tag_capabilities`), `site_names`, "
            "`shared_site_names` (`field_namer`), `_SiteReading` (`_site_reading`, the mode columns `_SITE_MODES` by "
            "`writes_argument`, `site_mode`), `collapsed_item_site`, `_tag_fields_of` (the fields of a variant that "
            "hold the tag property: those it declares, `_own_tag_fields`, then those it inherits, "
            "`_inherited_tag_fields`, model by model in an order of `TagFieldOrder`: `nearest-first`, with the rows "
            "`legacy-root-most-first` and `legacy-direct-bases-first`; the models in that order are "
            "`_inherited_models`, whose root-most order is `model/base.py:_walk_bases_first`, the walk of "
            "`DataModel.iter_all_fields`), `site_validity` (`has_any_variant`), `nested_variant_tags`, `UnionView` "
            "(`TagReading`), `iter_tag_field_decisions` (`KeptTagField`, `RetypedTagField`), `mapped_struct_tag`, "
            "`appended_takes_struct_tag`, `tag_msgspec_unions` (`MsgspecUnion`), `settle_site` and "
            "`iter_unbuildable_inlined_unions` (`_settle_choices`, `_shares_written_tag`, `_variant_carries_tag`, "
            "`_class_tag_field`), `iter_struct_tag_decisions` (`StructTagDecision`), `declared_union_tags`, "
            "`possible_tag_names`"
        ),
        adapters=(
            "`parser/_passes/structure.py:snapshot_union_tags` (L3); `parser/_passes/plans.py`: `reset_per_run_state`, "
            "`flatten_conflicting_base_layouts` (L4); `parser/_passes/modules.py`: `tag_reading`, "
            "`collapse_root_models_in_scope`, `apply_discriminator_type` (`_tag_discriminator_unions`), "
            "`settle_discriminated_unions`, `process_each_module` (L5); "
            "`parser/_passes/cross_module.py:tag_fields_of_tagged_bases` (L6); "
            "`parser/_passes/render_prep.py:fix_tag_opt_out_field_ordering`; `parser/_passes/imports.py`: "
            "`warn_about_variant_discriminator_tags`, `alias_reused_variant_literal`; `parser/base.py`: "
            "`Parser._model_shapes`, `Parser._tag_capabilities` (the `when` of the tag passes in "
            "`parser/_passes/_tables.py`); not yet: the discriminator reads of `get_collapsed_unions` "
            "(`parser/_passes/modules.py`) and of `warn_about_msgspec_decoding`, which also take a discriminator that "
            "names no property, as the list-item check of `collapse_root_models_in_scope` does through "
            "`collapsed_item_site` (`legacy-collapse-any-discriminator`); the fields of a struct's bases that "
            "`_base_fields` reads through `DataModel.iter_all_fields` for `_has_related_instance_field` and "
            "`_bases_keep_instance_field` (the instance fields the document names like the tag property, which under "
            "an inheritance cycle include the struct's own, where `_inherited_tag_fields` leaves them out); and a "
            "parser method through which the client and server targets ask the tags of a variant after the last pass "
            "(`tags_of_variant` with the reading of the finished `TagPlan`), which waits for its first caller"
        ),
        sites=(
            Home("parser/_passes/structure.py", "snapshot_union_tags"),
            Home("parser/_passes/plans.py", "reset_per_run_state"),
            Home("parser/_passes/plans.py", "flatten_conflicting_base_layouts"),
            Home("parser/_passes/modules.py", "tag_reading"),
            Home("parser/_passes/modules.py", "collapse_root_models_in_scope"),
            Home("parser/_passes/modules.py", "apply_discriminator_type"),
            Home("parser/_passes/modules.py", "_tag_discriminator_unions"),
            Home("parser/_passes/modules.py", "settle_discriminated_unions"),
            Home("parser/_passes/modules.py", "process_each_module"),
            Home("parser/_passes/cross_module.py", "tag_fields_of_tagged_bases"),
            Home("parser/_passes/render_prep.py", "fix_tag_opt_out_field_ordering"),
            Home("parser/_passes/imports.py", "warn_about_variant_discriminator_tags"),
            Home("parser/_passes/imports.py", "alias_reused_variant_literal"),
            Home("parser/base.py", "Parser._model_shapes"),
            Home("parser/base.py", "Parser._tag_capabilities"),
            Home("parser/_passes/modules.py", "get_collapsed_unions"),
            Home("parser/_passes/imports.py", "warn_about_msgspec_decoding"),
            Home("parser/_passes/_tables.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the type a const takes, and the default, required and nullability of a field fixed to one value: by its "
            "`const`, by one `Literal` under `--use-one-literal-as-default`, or as a discriminator tag (an optional "
            "tag is its field's default where Pydantic rejects one, a tag whose type L5 writes is required); today's "
            "disagreements are named `legacy-*` rows, each deleted by its fix: a required or defaulted const drops the "
            "null its schema admits (`legacy-const-drops-null`, #4873), an optional const that no `Literal` holds is "
            "not nullable either (`legacy-const-without-literal-not-null`), a msgspec const is never nullable and "
            "takes no default (`legacy-msgspec-const-not-null`), and a one-literal default drops the null of a `type` "
            "list that a tag keeps (`legacy-one-literal-drops-type-null`)"
        ),
        homes=(
            Home("model/_decisions/literal_default.py", "decide_literal_default"),
            Home("model/_decisions/literal_default.py", "const_type"),
        ),
        core="`model/_decisions/literal_default.py`: `decide_literal_default`, `const_type`",
        adapters=(
            "`model/base.py:DataModelFieldBase._process_const_as_literal` (the `process_const` hook of the Pydantic, "
            "dataclass and TypedDict fields: L1, and L5 `collapse_root_models` again), "
            "`model/msgspec.py:DataModelField.process_const`; `parser/_passes/modules.py` (L5): "
            "`set_one_literal_on_default`, and `set_tag_literal_default` for the tags fields hold (also those nested "
            "choices give a union) and those `apply_discriminator_type` writes (also the Enum tags of nested choices "
            "where `discriminator=` is written; default from `_get_single_discriminator_default`); "
            "`parser/_value_keywords.py:get_const_data_type` (L1, which returns the const type before the null a "
            "`type` list or `nullable` adds); writes: `model/base.py:_apply_literal_default`; not routed: the tag "
            "field `apply_discriminator_type` appends is built required and takes no default under `--force-optional`, "
            "where a tag whose type it writes does"
        ),
        sites=(
            Home("model/base.py", "DataModelFieldBase._process_const_as_literal"),
            Home("model/base.py", "DataModelFieldBase.process_const"),
            Home("parser/_passes/modules.py", "collapse_root_models"),
            Home("model/msgspec.py", "DataModelField.process_const"),
            Home("parser/_passes/modules.py", "set_one_literal_on_default"),
            Home("parser/_passes/modules.py", "set_tag_literal_default"),
            Home("parser/_passes/modules.py", "apply_discriminator_type"),
            Home("parser/_passes/modules.py", "_get_single_discriminator_default"),
            Home("parser/_value_keywords.py", "get_const_data_type"),
            Home("model/base.py", "DataModel.nullable"),
            Home("model/base.py", "_apply_literal_default"),
        ),
    ),
    DecisionHome(
        decision=(
            "which members a union holds, in which order, whether it writes None itself, and how it writes them: each "
            "builder passes its row (`declared` for the jsonschema and OpenAPI builders, `ordered` for msgspec field "
            "hints with None and `UNSET` as members), render preparation rewrites the union around a quoted container "
            "under `flattened` (a member that only groups its members gives them its place, and every member's None "
            "moves to the union), every `DataType` drops a member that admits any value and null beside another member "
            "and takes its None (`constructed`), and rendering keeps each written member once and takes over the None "
            "of its members; today's disagreements are named `legacy-*` rows, each deleted by its fix: the branches "
            "beside `array` of a type array go before it (`legacy-non-array-first`), the member collapse replaces goes "
            "last (`legacy-replacement-last`), the GraphQL union template writes `Union[...]` whatever the union "
            "operator (`legacy-graphql-template`), rendering compares a member's text before stripping its None "
            "(`legacy-dedup-before-strip`), and Pydantic writes `MISSING` after the rendered hint, keeping a union in "
            "it nested (`legacy-missing-appended`)"
        ),
        homes=(
            Home("model/_decisions/union_assembly.py", "assemble_union"),
            Home("model/_decisions/union_assembly.py", "MemberFacts"),
            Home("model/_decisions/union_assembly.py", "UnionPolicy"),
            Home("model/_decisions/union_assembly.py", "UnionPlan"),
            Home("model/_decisions/union_assembly.py", "merge_members"),
            Home("model/_decisions/union_assembly.py", "join_union"),
        ),
        core=(
            "`model/_decisions/union_assembly.py`: `assemble_union` (`MemberFacts`, `UnionPolicy`, `UnionPlan`), "
            "`merge_members`, `join_union`"
        ),
        adapters=(
            "`types.py:build_union` (with the builder's `self.data_type`) for `parser/jsonschema.py` and "
            "`parser/openapi.py`, `model/msgspec.py:DataModelField._ordered_union_data_type`; "
            "`types.py:DataType.__init__`; `parser/generation.py:GenerationStore.replace_nested_data_type` (L5 "
            "collapse); `parser/graphql.py:GraphQLParser.parse_union`; `types.py:render_type_expr` (`_render_union`); "
            "`_target_binding.py` (`_ordered_union`, the structural projection); "
            "`model/pydantic_v2/base_model.py:DataModelField._type_hint_with_missing_sentinel`; "
            "`parser/_passes/render_prep.py:_flatten_union_members` (`flattened`); `types.py:union_members` gives the "
            "members of a type that is a union at its top level and `types.py:written_union_members` the members of a "
            "union as written, with such member unions spread (msgspec's plain-union checks, and the nested-union "
            "checks of `parser/_passes/imports.py` and `parser/_passes/_shared.py:_iter_discriminator_data_types`), "
            "through `types.py:_union_members` and `_written_union_members`, which the decisions call for views; "
            "`parser/_passes/_shared.py:msgspec_union_members` the union a msgspec type writes (its own, or the items "
            "of a list, set or map), which the msgspec decoding warnings and `parser/_str_like_unions.py` read; a "
            "container's item union takes `constructed` from `DataType.__init__`, and the None of its items stays "
            "inside the container (`model/_decisions/nullability.py:takes_member_null`: a container is no union; "
            "`takes_item_null`: its item union takes it, read by the targets through `DataType.items_admit_none`); not "
            "yet: msgspec's decode view (`model/msgspec.py:DataModelField._union_leaves`) spreads alias unions by its "
            "own rule"
        ),
        sites=(
            Home("types.py", "build_union"),
            Home("model/msgspec.py", "DataModelField._ordered_union_data_type"),
            Home("types.py", "DataType.__init__"),
            Home("parser/generation.py", "GenerationStore.replace_nested_data_type"),
            Home("parser/graphql.py", "GraphQLParser.parse_union"),
            Home("types.py", "render_type_expr"),
            Home("types.py", "_render_union"),
            Home("_target_binding.py", "_ordered_union"),
            Home("model/pydantic_v2/base_model.py", "DataModelField._type_hint_with_missing_sentinel"),
            Home("parser/_passes/render_prep.py", "_flatten_union_members"),
            Home("types.py", "union_members"),
            Home("types.py", "written_union_members"),
            Home("parser/_passes/_shared.py", "_iter_discriminator_data_types"),
            Home("model/_decisions/nullability.py", "takes_member_null"),
            Home("model/_decisions/nullability.py", "takes_item_null"),
            Home("types.py", "DataType.items_admit_none"),
            Home("model/msgspec.py", "DataModelField._union_leaves"),
            Home("parser/jsonschema.py"),
            Home("parser/openapi.py"),
            Home("parser/_passes/imports.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the type of an object map: one dict with the key type of its patterns or `propertyNames` and the union of "
            "the value types of its key classes, the null and the other types of its type array (an L1 builder over "
            "raw keywords of the map object, not a pure core); an `additionalProperties` schema that asserts something "
            "admits the keys outside the patterns, which makes the keys those of `propertyNames` or `str`, except for "
            "msgspec, which keeps its 0.86.0 type until msgspec union fallbacks are decided as a whole (capability "
            "`DataModel._DECODES_VALUE_UNIONS` through `model/output.py:OutputModelContext.decodes_value_unions`)"
        ),
        homes=(Home("parser/jsonschema.py", "JsonSchemaParser._parse_object_map"),),
        core="`parser/jsonschema.py:JsonSchemaParser._parse_object_map`",
        adapters=(
            "the map positions of `parser/jsonschema.py`: the object and typed nullable map rows of the field table "
            "(`parse_item`), the map branches of `_parse_root_type_with_context`, the dict root of `parse_object` "
            "(`parser/_object_maps.py:get_additional_properties_root_field`, whose counts read the object's own "
            "keywords; where schema validators check the patterns its dict is `dict[<propertyNames key or str>, "
            "Any]`); the dict is built by the public hooks `parse_pattern_properties` and `parse_property_names` and "
            "by `parse_additional_properties_map` and `parse_nullable_map` (`parser/_object_maps.py`); a dict that is "
            "the only member of a type array of object and null, built by `_get_data_type`, is the type itself "
            "(`_get_type_array_data_type`); `parser/_object_maps.py:legacy_object_maps` (cached as "
            "`JsonSchemaParser._legacy_object_maps`), the only reader of `--use-single-dict-for-object-maps` and "
            "`--check-pattern-maps-strictly`, gives the rows of 0.86.0 maps they leave out (`legacy-map-*`, "
            "`legacy-validated-pattern-maps`), and `typed_nullable_map_value_schema` the values "
            "`--use-typed-nullable-map-values` and `--use-typed-map-values-beside-other-types` type; "
            "`parser/_object_maps.py:object_map_key_type` gives the key type of every map position (rows "
            "`pattern-map`, `property-names-map`, `extra-field`)"
        ),
        sites=(
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_root_type_with_context"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_object"),
            Home("parser/_object_maps.py", "get_additional_properties_root_field"),
            Home("parser/_object_maps.py", "parse_pattern_properties"),
            Home("parser/_object_maps.py", "parse_property_names"),
            Home("parser/_object_maps.py", "parse_additional_properties_map"),
            Home("parser/_object_maps.py", "parse_nullable_map"),
            Home("parser/jsonschema.py", "JsonSchemaParser._get_data_type"),
            Home("parser/jsonschema.py", "JsonSchemaParser._get_type_array_data_type"),
            Home("parser/_object_maps.py", "legacy_object_maps"),
            Home("parser/jsonschema.py", "JsonSchemaParser._legacy_object_maps"),
            Home("parser/_object_maps.py", "typed_nullable_map_value_schema"),
            Home("parser/_object_maps.py", "object_map_key_type"),
        ),
    ),
    DecisionHome(
        decision=(
            "what the class body writes after a field's annotation (its call, its default, or nothing) and what that "
            "gives the model's constructor (a default value or factory, whether the field takes part, whether it must "
            "be given a value: required, its default not kept beside it under `--use-default`), per output row "
            "(`model`, `dataclass`, `pydantic-dataclass`, `msgspec`), classified when a pass reads it; row "
            "`legacy-stdlib-annotated` (a stdlib dataclass `field(...)` under `--use-annotated` counts as unassigned); "
            "the written assignment is read with the nested-model default factories it has then, before L6 records the "
            "recursive paths that drop them (`legacy-nested-factory-at-read`)"
        ),
        homes=(
            Home("model/_decisions/assignment.py", "classify_assignment"),
            Home("model/_decisions/assignment.py", "participates_in_constructor"),
            Home("model/_decisions/assignment.py", "excluded_from_init"),
            Home("model/_decisions/assignment.py", "requires_value"),
            Home("model/_decisions/assignment.py", "struct_assigned"),
            Home("model/_decisions/assignment.py", "written_data"),
            Home("model/_render_plan.py", "AssignmentFacts"),
            Home("model/base.py", "_default_state"),
            Home("model/_decisions/assignment.py", "DefaultState"),
        ),
        core=(
            "`model/_decisions/assignment.py`: `classify_assignment`, `participates_in_constructor`, "
            "`excluded_from_init`, `requires_value`, `struct_assigned`, `written_data`; the record "
            "`model/_render_plan.py:AssignmentFacts`; `model/base.py:_default_state` (a default is none given, `None` "
            "or another value: `DefaultState`)"
        ),
        adapters=(
            "`model/base.py:DataModel._field_assignment_facts` (the row of the class, or its own hooks when it "
            "replaces them without naming a row) over `_assignment_inputs` and the field hook "
            "`DataModelFieldBase._written_assignment` (overridden by the dataclass, Pydantic dataclass and msgspec "
            "fields, and by the Pydantic model field for the stripped `None` its template keeps for a type rendered "
            "with None, `ModelWritten.keeps_none`); readers: the L1 construction key, "
            "`_target_binding.py:_field_facts` (the targets' constructor requiredness and `in_init`, "
            "`ModelFieldFacts.constructor_required` and `.in_init`), `parser/_passes/render_prep.py`: "
            "`fix_constructor_field_ordering` and `fix_tag_opt_out_field_ordering` (after the decision phase), "
            "`DataModel.field_binds_class_attribute` (L5 `hides_named_type` of `parser/_passes/modules.py`, "
            "`_is_hidden_by_earlier_field`), `warn_required_assignments_after_defaults` "
            "(`parser/_passes/render_prep.py`); views: `FIELD_ASSIGNMENT_CHECKER`, `FIELD_DEFAULT_CLASSIFIER`, "
            "`FIELD_PARTICIPATES_IN_CONSTRUCTOR`, `FIELD_CONSTRUCTION_ORDER_KEY` of the built-in outputs and "
            "`DataModelFieldBase._get_constructor_default_info`; `requires_value` readers: the `Field(...)`, default "
            "and default-factory choices of `model/pydantic_base.py`, `model/pydantic_v2/dataclass.py`, "
            "`model/dataclass.py` and `model/msgspec.py`, the nested-factory tests of `model/base.py`, "
            "`_applies_default` (`parser/_passes/render_prep.py`) and the constructor ordering conflicts "
            "(`parser/_passes/_shared.py`); the four model templates (`model/template/`) spell the same test "
            "themselves; `_default_state` readers: `_model_written`, the `field()` items of `model/dataclass.py`, the "
            "default and default-factory choices of `model/pydantic_base.py`, `model/dataclass.py` and "
            "`model/msgspec.py`, the nested-factory tests of `model/base.py`, the `MISSING` default of "
            "`model/pydantic_v2/base_model.py`, `_applies_default` (`parser/_passes/render_prep.py`) and "
            "`_target_binding.py:_emitted_default`; `excluded_from_init` readers (a declared `init=False`): "
            "`_assignment_inputs`, the constructor participation hooks of the neutral model, `model/dataclass.py` and "
            "`model/pydantic_v2/dataclass.py`, the `field()` items and the inherited `init=False` a required copy "
            "keeps (`DataClass.prepare_required_inherited_field`), which "
            "`DataClass.restore_required_inherited_field_state` removes and tests as it pops it; a declared "
            "`default_factory` is `DataModelFieldBase.has_default_factory`, read by `_model_written`, the inherited "
            "factory a required copy drops (`DataModel.prepare_required_inherited_field`), the nested-model factory of "
            "`model/dataclass.py` and the declared factory of `model/pydantic_base.py`; not routed: "
            "`model/dataclass.py:_has_constructor_default` (nested mapping defaults, which skips rendering structured "
            "values) and the nested-factory metadata (`_set_nested_model_default_factory_metadata`)"
        ),
        sites=(
            Home("model/base.py", "DataModel._field_assignment_facts"),
            Home("model/base.py", "_assignment_inputs"),
            Home("model/base.py", "DataModelFieldBase._written_assignment"),
            Home("_target_binding.py", "_field_facts"),
            Home("parser/_passes/render_prep.py", "fix_constructor_field_ordering"),
            Home("parser/_passes/render_prep.py", "fix_tag_opt_out_field_ordering"),
            Home("model/base.py", "DataModel.field_binds_class_attribute"),
            Home("parser/_passes/modules.py", "hides_named_type"),
            Home("model/base.py", "_is_hidden_by_earlier_field"),
            Home("parser/_passes/render_prep.py", "warn_required_assignments_after_defaults"),
            Home("model/base.py", "DataModelFieldBase._get_constructor_default_info"),
            Home("parser/_passes/render_prep.py", "_applies_default"),
            Home("model/base.py", "_default_state"),
            Home("model/base.py", "_model_written"),
            Home("_target_binding.py", "_emitted_default"),
            Home("model/dataclass.py", "DataClass.prepare_required_inherited_field"),
            Home("model/dataclass.py", "DataClass.restore_required_inherited_field_state"),
            Home("model/base.py", "DataModelFieldBase.has_default_factory"),
            Home("model/base.py", "DataModel.prepare_required_inherited_field"),
            Home("model/dataclass.py", "_has_constructor_default"),
            Home("model/pydantic_base.py"),
            Home("model/pydantic_v2/dataclass.py"),
            Home("model/msgspec.py"),
            Home("parser/_passes/_shared.py"),
            Home("model/pydantic_v2/base_model.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "which model or field an option keyed by a class name, `<class>.<field>` or a field name applies to, per "
            "option at the pass that reads it and under the names models have there: the order of the keys "
            "(`<class>.<field>` before `field`), the class part of a key (the pairs it splits into at its dots: row "
            "`every-dot`, `first-dot` for `--type-overrides`), model-level against scoped `--type-overrides` keys, and "
            "which of a model's names the class part is compared with (`class_key` rows `predicted`, `reference-name`, "
            "`original-name`, `class-name`, named where a model is looked up; `model_type_override` rows `class-name`, "
            "read by the removal, the references and the bases alike, and `predicted-class-name` for a model-level "
            "`--type-overrides` key); row `legacy-reparse-for-final-names` keeps the options read while models are "
            "built on the name a model is created under, and parses again with the final names when a yielding name "
            "that their keys name changed; a key of those options that names the name a rename after L2 "
            "(`GenerationStore.rename_model`) gave a model also names that model: the run records the name by the path "
            "the model had at L2 and parses again, and the lookups of that path compare the key with it after the name "
            "of their row, the template data of the name joining that of the path; the class-name keys of "
            "`--model-name-map` name the models of the output without them: a generation run generates the input "
            "without them first and translates each to the model it names there, by schema path, and every other name "
            "that model had there stays free only for the models that end that run with it"
        ),
        homes=(
            Home("_user_keys.py", "field_keys"),
            Home("_user_keys.py", "class_field_key"),
            Home("_user_keys.py", "class_key"),
            Home("_user_keys.py", "ClassScope"),
            Home("_user_keys.py", "scoped_key"),
            Home("_user_keys.py", "first_present"),
            Home("_user_keys.py", "first_given"),
            Home("_user_keys.py", "mapped_base_class"),
            Home("_user_keys.py", "template_data_keys"),
            Home("_user_keys.py", "model_level_type_override"),
            Home("_user_keys.py", "model_type_override"),
            Home("_user_keys.py", "type_override_classes"),
            Home("_user_keys.py", "ConstructionKeys"),
            Home("_user_keys.py", "class_name_keys"),
            Home("_user_keys.py", "model_name_path_keys"),
            Home("_user_keys.py", "translate_class_keys"),
            Home("_user_keys.py", "ModelNameRecord"),
            Home("_user_keys.py", "ModelNameKeys"),
        ),
        core=(
            "`_user_keys.py`: `field_keys`, `class_field_key`, `class_key`, `ClassScope`, `scoped_key`, "
            "`first_present`, `first_given`, `mapped_base_class`, `template_data_keys`, `model_level_type_override`, "
            "`model_type_override`, `type_override_classes`, `ConstructionKeys`, `class_name_keys`, "
            "`model_name_path_keys`, `translate_class_keys` (over a `ModelNameRecord`, to `ModelNameKeys`)"
        ),
        adapters=(
            "`_generation.py:_parse_generation` (the run without the class-name keys); `reference.py`: "
            "`ModelResolver._get_model_name_map_value`, `ModelResolver.apply_model_name_keys`, the allocations and the "
            "settle it records; `parser/_passes/imports.py:record_generated_class_names` (the final names), "
            "`translate_final_name_keys` (the output names keys name, by path); `_generation.py:_parse_generation_run` "
            "(the parse again with them); `reference.py:ModelResolver.class_scope`; `reference.py`: "
            "`FieldNameResolver.prepare_explicit_field_aliases`, `FieldNameResolver.get_valid_field_name_and_alias`, "
            "`ModelResolver.resolve_default_value`; `parser/base.py`: `Parser.get_serialization_alias`, "
            "`Parser._apply_final_class_field_aliases`, `Parser._resolve_base_class`; `parser/_passes/settle.py`: "
            "`settle_yielding_names`; `parser/_passes/modules.py`: `apply_field_type_overrides`, "
            "`remove_overridden_models`, `derived_override_import`, the reused tag fields; "
            "`parser/_passes/_shared.py`: `_ReuseOptimizationContext.from_type_overrides`, "
            "`enum_site_override_import`; `parser/_type_overrides.py:apply_override_to_data_type`; "
            "`parser/_passes/plans.py` (the base layouts); `parser/_tag_plan.py` (tag field names); `parser/avro.py`; "
            "`parser/graphql.py`; `parser/openapi.py:OpenAPIParser.parse_all_parameters`; `parser/jsonschema.py` (the "
            "root models, `parse_object`, allOf, `JsonSchemaParser._extra_template_data_for_reference`); "
            "`parser/_inherited_schemas.py:parse_inherited_schema_fields`; `parser/_rw_split.py`; "
            "`model/base.py:DataModel.__init__` (template data keys); `model/scalar.py:_DataTypeScalarBase.__init__`; "
            "`_target_naming.py:TargetNames.alias`"
        ),
        sites=(
            Home("_generation.py", "_parse_generation"),
            Home("reference.py", "ModelResolver._get_model_name_map_value"),
            Home("reference.py", "ModelResolver.apply_model_name_keys"),
            Home("parser/_passes/imports.py", "record_generated_class_names"),
            Home("parser/_passes/imports.py", "translate_final_name_keys"),
            Home("_generation.py", "_parse_generation_run"),
            Home("reference.py", "ModelResolver.class_scope"),
            Home("reference.py", "FieldNameResolver.prepare_explicit_field_aliases"),
            Home("reference.py", "FieldNameResolver.get_valid_field_name_and_alias"),
            Home("reference.py", "ModelResolver.resolve_default_value"),
            Home("parser/base.py", "Parser.get_serialization_alias"),
            Home("parser/base.py", "Parser._apply_final_class_field_aliases"),
            Home("parser/base.py", "Parser._resolve_base_class"),
            Home("parser/_passes/settle.py", "settle_yielding_names"),
            Home("parser/_passes/modules.py", "apply_field_type_overrides"),
            Home("parser/_passes/modules.py", "remove_overridden_models"),
            Home("parser/_passes/modules.py", "derived_override_import"),
            Home("parser/_passes/_shared.py", "_ReuseOptimizationContext.from_type_overrides"),
            Home("parser/_passes/_shared.py", "enum_site_override_import"),
            Home("parser/_type_overrides.py", "apply_override_to_data_type"),
            Home("parser/openapi.py", "OpenAPIParser.parse_all_parameters"),
            Home("parser/jsonschema.py", "JsonSchemaParser._extra_template_data_for_reference"),
            Home("parser/_inherited_schemas.py", "parse_inherited_schema_fields"),
            Home("model/base.py", "DataModel.__init__"),
            Home("model/scalar.py", "_DataTypeScalarBase.__init__"),
            Home("_target_naming.py", "TargetNames.alias"),
            Home("parser/_passes/plans.py"),
            Home("parser/_tag_plan.py"),
            Home("parser/avro.py"),
            Home("parser/graphql.py"),
            Home("parser/_rw_split.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "where a run's remote lock lives and its policy: the lockfile option or `datamodel-codegen.lock` in the "
            "default directory (the directory of the pyproject.toml the command line read, or that "
            "`load_pyproject_config()` read for the config of `generate()`; else the working directory on the command "
            "line and the caller's directory in `generate()`), its literal path (the lexical form from the caller's "
            "directory, the working directory on the command line, in every run) and its canonical path (the resolved "
            "form of the literal one, which every run opens, checks and publishes to), and update, locked, verify or "
            "inactive"
        ),
        homes=(
            Home("_lock_plan.py", "default_lock_parent"),
            Home("_lock_plan.py", "remote_lock_plan"),
            Home("_lock_plan.py", "owns_lock_update"),
            Home("_lock_plan.py", "models_lockfile"),
        ),
        core="`_lock_plan.py`: `default_lock_parent`, `remote_lock_plan`, `owns_lock_update`, `models_lockfile`",
        adapters=(
            "`__main__.py`: `_watch_remote_lock_setup`, `_recommend_lock_observer`, `_run_jobs`, `_run_watched_jobs`, "
            "`_run_config` (each calls `_remote_lock_plan`); `_target_cli.py`: `_run`; `_generation.py`: "
            "`_resolve_generation_remote_lock`, `_prepare_atomic_generation_remote_lock`"
        ),
        sites=(
            Home("__main__.py", "_watch_remote_lock_setup"),
            Home("__main__.py", "_recommend_lock_observer"),
            Home("__main__.py", "_run_jobs"),
            Home("__main__.py", "_run_watched_jobs"),
            Home("__main__.py", "_run_config"),
            Home("_target_cli.py", "_run"),
            Home("_generation.py", "_resolve_generation_remote_lock"),
            Home("_generation.py", "_prepare_atomic_generation_remote_lock"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a remote lock update is staged for the publication journal: the held directory it is published into, "
            "its private staging (`.datamodel-codegen-lock-`) and the staged file"
        ),
        homes=(
            Home("remote_lock.py", "_open_lock_staging"),
            Home("remote_lock.py", "_staged_lock"),
        ),
        core="`remote_lock.py`: `_open_lock_staging`, `_staged_lock`",
        adapters=(
            "`_generation.py:_generate_with_atomic_remote_update`, `__main__.py:_RemoteLockTransaction`, "
            "`_api_generation.py:_stage`, `remote_lock.py:RemoteReferenceLock.commit`"
        ),
        sites=(
            Home("_generation.py", "_generate_with_atomic_remote_update"),
            Home("__main__.py", "_RemoteLockTransaction"),
            Home("_api_generation.py", "_stage"),
            Home("remote_lock.py", "RemoteReferenceLock.commit"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a generation's output and model metadata are staged for the publication journal: their private "
            "staging, the options that write there (`output`, `emit_model_metadata`), the held directory each is "
            "published into (an output that is an existing directory at its root, else its parent), an output that is "
            "an existing directory staged as an empty directory (as the models of a target are through "
            "`staged_artifact_path`), and the staged files paired with their targets (a staged directory file by file, "
            "nothing staged pairs nothing); the concrete roots (each artifact's resolved path and concrete parent) are "
            "bound when the caller plans the run (the command line when it plans the job, `generate()` when it "
            "stages); the model metadata pairs as staged, so one that is missing when a run publishes fails the "
            "publication of every file"
        ),
        homes=(
            Home("_publication.py", "artifact_roots"),
            Home("_publication.py", "stage_outputs"),
            Home("_publication.py", "StagedOutputs"),
            Home("_publication.py", "StagedArtifact"),
        ),
        core="`_publication.py`: `artifact_roots`, `stage_outputs`, `StagedOutputs`, `StagedArtifact`",
        adapters=(
            "`_generation.py:_generate_with_atomic_remote_update`; `__main__.py`: `_get_job_config`, "
            "`_single_job_plan`, "
            "`_stage_job_plan`"
        ),
        sites=(
            Home("_generation.py", "_generate_with_atomic_remote_update"),
            Home("__main__.py", "_get_job_config"),
            Home("__main__.py", "_single_job_plan"),
            Home("__main__.py", "_stage_job_plan"),
        ),
    ),
    DecisionHome(
        decision=(
            "where a private staging directory beside a target goes, on the filesystem the target is published to: in "
            "the deepest existing parent of the target (a relative target joined to the caller's directory), or beside "
            "the output `around` that contains the target, so a render writes nothing into that output"
        ),
        homes=(Home("_publication.py", "staging_directory_for"),),
        core="`_publication.py`: `staging_directory_for`",
        adapters=(
            "`_publication.py:stage_artifact` (the command line and `generate()`), `_api_generation.py:_staging` (the "
            "models and the formatting of a target); left out: `_api_generation.py:_stage` (stages in the anchor "
            "`publication_anchor` holds) and `_target_selection.py:_render` (a render directory under the working "
            "directory)"
        ),
        sites=(
            Home("_publication.py", "stage_artifact"),
            Home("_api_generation.py", "_staging"),
            Home("_api_generation.py", "_stage"),
            Home("_publication.py", "publication_anchor"),
            Home("_target_selection.py", "_render"),
        ),
    ),
    DecisionHome(
        decision=(
            "where an artifact is staged in its private staging directory: under the target's name, or a default name "
            "(`output`, `model-metadata.json`) for a target without one, created as an empty directory when the caller "
            "asks"
        ),
        homes=(Home("_publication.py", "staged_artifact_path"),),
        core="`_publication.py:staged_artifact_path`",
        adapters=(
            "`_publication.py:stage_artifact` (the output and model metadata of the command line and `generate()`), "
            "`_api_generation.py:_generate_models` (the models of a target and their model metadata)"
        ),
        sites=(
            Home("_publication.py", "stage_artifact"),
            Home("_api_generation.py", "_generate_models"),
        ),
    ),
    DecisionHome(
        decision=(
            "the encoding a generated file of a target run is written in and read back in: models and Python files "
            "(`.py`, `.pyi`) in the model encoding, other text as UTF-8; the planner writes the files of a target in "
            "it, and the generation payload, `generate()`'s returned texts and `--check` decode rendered files in it"
        ),
        homes=(
            Home("_api_generation.py", "file_encoding"),
            Home("_api_generation.py", "artifact_text"),
        ),
        core="`_api_generation.py`: `file_encoding`, `artifact_text`",
        adapters=(
            "`_api_generation.py:_Planner.encoding`; `_target_cli.py`: `_target_json`, `_compare_target` (the files a "
            "directory comparison compares one by one); `_target_selection.py:_render`"
        ),
        sites=(
            Home("_api_generation.py", "_Planner.encoding"),
            Home("_target_cli.py", "_target_json"),
            Home("_target_cli.py", "_compare_target"),
            Home("_target_selection.py", "_render"),
        ),
    ),
    DecisionHome(
        decision=(
            "the encoding of the text Ruff reads and of the code and messages it writes: UTF-8, as Ruff reads and "
            "writes UTF-8 only; a file written in another encoding is held as UTF-8 while Ruff formats it and written "
            "back in its encoding, and the files of a target are staged for Ruff in UTF-8"
        ),
        homes=(Home("format.py", "_RUFF_ENCODING"),),
        core="`format.py:_RUFF_ENCODING`",
        adapters=(
            "`format.py`: `CodeFormatter.apply_ruff_lint`, `CodeFormatter.apply_ruff_formatter`, "
            "`CodeFormatter.apply_ruff_check_and_format`, `CodeFormatter._ruff_check`, "
            "`CodeFormatter._run_ruff_command` (its messages), `_held_as_utf8`; `_api_generation.py:_Planner.defer`; "
            "left out: `format.py:_read_by_ruff` (reads `utf-8-sig`, as Ruff skips a byte order mark)"
        ),
        sites=(
            Home("format.py", "CodeFormatter.apply_ruff_lint"),
            Home("format.py", "CodeFormatter.apply_ruff_formatter"),
            Home("format.py", "CodeFormatter.apply_ruff_check_and_format"),
            Home("format.py", "CodeFormatter._ruff_check"),
            Home("format.py", "CodeFormatter._run_ruff_command"),
            Home("format.py", "_held_as_utf8"),
            Home("_api_generation.py", "_Planner.defer"),
            Home("format.py", "_read_by_ruff"),
        ),
    ),
    DecisionHome(
        decision=(
            "the deepest existing ancestor of a local path: rows `directory` (an existing directory: the publication "
            "anchor, the remote lock's temporary file, a watch root) and `exists` (any existing path: the parents a "
            "publication creates are the ones below it); the deepest existing path above a path a run creates must be "
            "a directory (`creation_root`: private staging, and the check of `generate()`, the command line and the "
            "target runs that the output, the model metadata and a lock update can be created, which names a regular "
            "file there or a root that does not exist); a root that fails the test (a missing drive) is returned (the "
            "watcher) or raises the caller's error (the remote lock; `missing_root`, which names the root, for the "
            "publication walks)"
        ),
        homes=(
            Home("_paths.py", "existing_ancestor"),
            Home("_paths.py", "missing_root"),
            Home("_paths.py", "creation_root"),
        ),
        core="`_paths.py`: `existing_ancestor`, `missing_root`, `creation_root`",
        adapters=(
            "`_generation.py:_refuse_uncreatable_paths`; `_publication.py`: `publication_anchor`, "
            "`staging_directory_for`, `_create_target_parent`; `remote_lock.py:_nearest_existing_directory`; "
            "`watch_dependencies.py:_nearest_existing_directory`"
        ),
        sites=(
            Home("_generation.py", "_refuse_uncreatable_paths"),
            Home("_publication.py", "publication_anchor"),
            Home("_publication.py", "staging_directory_for"),
            Home("_publication.py", "_create_target_parent"),
            Home("remote_lock.py", "_nearest_existing_directory"),
            Home("watch_dependencies.py", "_nearest_existing_directory"),
        ),
    ),
    DecisionHome(
        decision=(
            "where a local path is published: its concrete parent (its parent with `~` expanded and symlinks resolved) "
            "joined with its last component as written; the journal binds staged files to it and checks it again on "
            "Windows, the remote lock commits to it, the watcher watches it beside the lexical and resolved forms, the "
            "target coordinator stages, shares and collision-checks models at it, and the command line and "
            "`generate()` anchor staged output and model metadata at the concrete parent"
        ),
        homes=(
            Home("_paths.py", "concrete"),
            Home("_paths.py", "concrete_parent"),
        ),
        core="`_paths.py`: `concrete`, `concrete_parent`",
        adapters=(
            "`_publication.py`: `_planned_staged_file`, `_validate_planned_target`; "
            "`remote_lock.py:RemoteReferenceLock.commit`; `watch_dependencies.py:_path_variants`; "
            "`_api_generation.py`: `_Planner.check_collisions`, `_stage`, `_unshared`; "
            "`_publication.py:artifact_roots`; `_generation.py:_prepare_directory_input`; left out: "
            "`_target_cli.py:_stage_implementations` (resolves an existing services directory to compare it with the "
            "output root)"
        ),
        sites=(
            Home("_publication.py", "_planned_staged_file"),
            Home("_publication.py", "_validate_planned_target"),
            Home("remote_lock.py", "RemoteReferenceLock.commit"),
            Home("watch_dependencies.py", "_path_variants"),
            Home("_api_generation.py", "_Planner.check_collisions"),
            Home("_api_generation.py", "_stage"),
            Home("_api_generation.py", "_unshared"),
            Home("_publication.py", "artifact_roots"),
            Home("_generation.py", "_prepare_directory_input"),
            Home("_target_cli.py", "_stage_implementations"),
        ),
    ),
    DecisionHome(
        decision=(
            "the lexical and the resolved form of a path an option or a caller spells: `~` expanded and a relative "
            "spelling joined to the caller's directory (or the working directory), then its dot components folded as "
            "written (lexical, symlinks kept) or its symlinks resolved (resolved: a symlink loop stays as written on "
            "every Python version, and an existing path whose links lead to nothing that exists, such as `/dev/stdin` "
            "reading a pipe, keeps its lexical form); the path checks of `generate()`, the command line and the target "
            "coordinator compare these forms, the watcher records the lexical form, staging is placed from them, and "
            "the command line reads its path options and `load_pyproject_config` its start path in the resolved form"
        ),
        homes=(
            Home("_paths.py", "lexical"),
            Home("_paths.py", "resolved"),
        ),
        core="`_paths.py`: `lexical`, `resolved`",
        adapters=(
            "`_generation.py`: `_validate_generation_path_conflicts`, `_prepare_generation_input`; `__main__.py`: "
            "`_config_artifacts` (the artifacts of the job and lock checks), `_preflight_job_plans` (the job inputs), "
            "`_validate_staged_job_plans`; `_api_generation.py`: `_root_input`, `_remote_lock`, `_Planner`; "
            "`_publication.py`: `staging_directory_for`, `artifact_roots`; `_lock_plan.py:remote_lock_plan`; "
            "`watch_dependencies.py`: `_path_variants`, `_symlink_event_paths`, `_resolved_path`; `_cli_config.py` "
            "(the path validator of the command-line config); `_project_config.py:load_pyproject_config`"
        ),
        sites=(
            Home("_generation.py", "_validate_generation_path_conflicts"),
            Home("_generation.py", "_prepare_generation_input"),
            Home("__main__.py", "_config_artifacts"),
            Home("__main__.py", "_preflight_job_plans"),
            Home("__main__.py", "_validate_staged_job_plans"),
            Home("_api_generation.py", "_root_input"),
            Home("_api_generation.py", "_remote_lock"),
            Home("_api_generation.py", "_Planner"),
            Home("_publication.py", "staging_directory_for"),
            Home("_publication.py", "artifact_roots"),
            Home("_lock_plan.py", "remote_lock_plan"),
            Home("watch_dependencies.py", "_path_variants"),
            Home("watch_dependencies.py", "_symlink_event_paths"),
            Home("watch_dependencies.py", "_resolved_path"),
            Home("_project_config.py", "load_pyproject_config"),
            Home("_cli_config.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "how two local paths relate: they overlap when one lies inside the other or is the other, compared as "
            "written (`Path.is_relative_to` either way; one inside the other is `Path.is_relative_to` itself), and "
            "they name one file when both exist and reach one inode; the callers choose the forms they compare "
            "(lexical, resolved or both) and what an error of the filesystem means (the path check of `generate()` and "
            "of a direct command raises it; the job and lock checks of the command line, the watcher and the journal's "
            "rollback take it as no match)"
        ),
        homes=(
            Home("_paths.py", "overlaps"),
            Home("_paths.py", "same_file"),
        ),
        core="`_paths.py`: `overlaps`, `same_file`",
        adapters=(
            "`_generation.py:_validate_generation_path_conflicts`; `__main__.py:_paths_overlap_or_samefile` (the job "
            "and "
            "lock checks); `_api_generation.py:_remote_lock`; `_publication.py:_restore_backup`; "
            "`watch_dependencies.py:_logical_working_directory`"
        ),
        sites=(
            Home("_generation.py", "_validate_generation_path_conflicts"),
            Home("__main__.py", "_paths_overlap_or_samefile"),
            Home("_api_generation.py", "_remote_lock"),
            Home("_publication.py", "_restore_backup"),
            Home("watch_dependencies.py", "_logical_working_directory"),
        ),
    ),
    DecisionHome(
        decision=(
            "what a generation wrote at an output path and whether it can replace what the destination holds: a file "
            "is one module, any other existing path a package, nothing written neither; one module pairs with the "
            "destination itself, a package's files with their paths below it; one module cannot replace an existing "
            "directory, a package an existing file, and every run that writes, stages or checks an output (a direct "
            "write, `--check`, `--output-format json`, `--update-lock`, jobs, `generate()` with a lock update, the "
            "models of a target) refuses such a generation with the refusal of a direct write before it copies, "
            "compares or publishes anything; `--diff-against` reads no destination"
        ),
        homes=(
            Home("_paths.py", "output_layout"),
            Home("_paths.py", "output_files"),
            Home("_paths.py", "layout_refusal"),
        ),
        core="`_paths.py`: `output_layout`, `output_files`, `layout_refusal`",
        adapters=(
            "`__main__.py`: `_run_config` (the refusal and the single-file switch of `--check`), "
            "`_copy_generated_output`, `_generated_files_from_output`; `_output_comparison.py:_compare_directories`; "
            "`_generation.py`: `_write_results_to_output`, `_generate_with_atomic_remote_update`; "
            "`_facade.py:generate`; `_publication.py:StagedArtifact.files`; `_api_generation.py`: `_staged_models`, "
            "`_generate_models`"
        ),
        sites=(
            Home("__main__.py", "_run_config"),
            Home("_output_comparison.py", "_compare_directories"),
            Home("__main__.py", "_copy_generated_output"),
            Home("__main__.py", "_generated_files_from_output"),
            Home("_generation.py", "_write_results_to_output"),
            Home("_facade.py", "generate"),
            Home("_generation.py", "_generate_with_atomic_remote_update"),
            Home("_publication.py", "StagedArtifact.files"),
            Home("_api_generation.py", "_staged_models"),
            Home("_api_generation.py", "_generate_models"),
        ),
    ),
    DecisionHome(
        decision=(
            "the Pydantic release that generated models need above an explicit target, which a generation states once, "
            "as a `UserWarning`, for the output it keeps"
        ),
        homes=(
            Home("_requirements.py", "pydantic_floor"),
            Home("_requirements.py", "pydantic_floor_message"),
            Home("_requirements.py", "CONFIG_KEY_REQUIREMENTS"),
        ),
        core=(
            "`_requirements.py`: `pydantic_floor`, `pydantic_floor_message` over the rows (`label`, `config_key`; "
            "`CONFIG_KEY_REQUIREMENTS` is a view of them)"
        ),
        adapters=(
            "constructs: `_pydantic_constructs` of `model/base.py:DataModel` and `DataModelFieldBase`, "
            "`model/type_alias.py`, and `model/pydantic_v2/base_model.py` (`DataModelField`, `BaseModel`, and "
            "`_PydanticV2Model` for `BaseModel` and `dataclass.py:DataClass`), whose config keys come from "
            "`_rendered_config_items` (`_PydanticV2Model`, `RootModel`), the items the renderer, the imports and the "
            "alias decisions read too; record: `parser/_passes/imports.py:record_pydantic_floor` at the end of L6, "
            "into `pydantic_floors_recorded`; statement: `_generation.py:_parse_generation`, for the parse it keeps"
        ),
        sites=(
            Home("model/base.py", "DataModel"),
            Home("model/base.py", "DataModelFieldBase"),
            Home("model/pydantic_v2/base_model.py", "DataModelField"),
            Home("model/pydantic_v2/base_model.py", "BaseModel"),
            Home("model/pydantic_v2/base_model.py", "_PydanticV2Model"),
            Home("model/pydantic_v2/base_model.py", "_PydanticV2Model._rendered_config_items"),
            Home("parser/_passes/imports.py", "record_pydantic_floor"),
            Home("_generation.py", "_parse_generation"),
            Home("model/type_alias.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether Pydantic needs Python's `re` (the `python-re` engine) for a pattern, decided from the static scan "
            "of the Rust grammar: a lookaround and syntax Rust rejects (also a Unicode property it does not implement, "
            "a class escape as the start of a range and counted repetitions past its compiled-size limit, alone by "
            "`_SIZE_THRESHOLDS` or together by `_CERTAIN_THRESHOLDS`), whichever Python's `re` parses it; row `model` "
            "(the config of a model, or of the models that use a type alias keeping string patterns: syntax Rust "
            "rejects counts for the pattern of a string type or a `Literal`, which Pydantic compiles, the string types "
            "read from the constraint table; a verbose pattern is read when it starts with `(?x)` and any other keeps "
            "the default engine), row `adapter` (a schema validator's adapter: also a pattern the scan cannot read, a "
            "verbose pattern; `is_portable_pattern`, a pattern it leaves on the default engine, is what the allOf "
            "intersection and the pattern conjunction read); the rows disagree only on a verbose pattern that Rust "
            "accepts; row `legacy-dataclass-overwrites-engine` (a `pydantic_v2.dataclass` replaces an engine its "
            "config names); row `legacy-dataclass-follows-every-reference` (a `pydantic_v2.dataclass` also reads the "
            "patterns of every model its fields reach); a lone surrogate selects the engine only where every other "
            "pattern the config builds holds one too or needs the engine (#6320), otherwise the config keeps the "
            "default engine and warns"
        ),
        homes=(
            Home("_regex_syntax.py", "pattern_engine"),
            Home("_regex_syntax.py", "selects_python_engine"),
            Home("_regex_syntax.py", "is_portable_pattern"),
        ),
        core="`_regex_syntax.py`: `pattern_engine`, `selects_python_engine`, `is_portable_pattern`",
        adapters=(
            "`model/pydantic_v2/base_model.py`: `_model_pattern_engine` (the string type or `Literal` a field's "
            "pattern constrains), `_pattern_needs_holder_engine` (a type-alias root before compiled patterns, read at "
            "L1 by `parser/_all_of_values.py:patterns_need_holder_engine`), `_select_surrogate_regex_engines` (the "
            "lone surrogates, at render preparation once every model is final: the patterns the config of a model "
            "builds, of its fields, of the bases it inherits and of the type aliases it reaches, and those of the "
            "models that take its config); `model/runtime_validation.py:_adapter_pattern_engines` (the types of an "
            "adapter and the string patterns of the aliases they name); "
            "`model/pydantic_v2/base_model.py:_PydanticV2Model._select_python_regex_engine` (the config a model or "
            "dataclass writes)"
        ),
        sites=(
            Home("model/pydantic_v2/base_model.py", "_model_pattern_engine"),
            Home("model/pydantic_v2/base_model.py", "_pattern_needs_holder_engine"),
            Home("parser/_all_of_values.py", "patterns_need_holder_engine"),
            Home("model/pydantic_v2/base_model.py", "_select_surrogate_regex_engines"),
            Home("model/runtime_validation.py", "_adapter_pattern_engines"),
            Home("model/pydantic_v2/base_model.py", "_PydanticV2Model._select_python_regex_engine"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a schema's `pattern` is written for the regex engines of the output's models "
            "(`OutputKeywords.regex_engines`): left out with a warning where no engine compiles it (Python's `re` "
            "rejects it, and Rust too where Rust is an engine), with each `[` inside a class escaped where only that "
            "makes Rust read it as Python does, else as written; read from the text alone, on each schema object with "
            "a location as it is validated (L1)"
        ),
        homes=(Home("_regex_syntax.py", "read_pattern"),),
        core="`_regex_syntax.py`: `read_pattern`",
        adapters=(
            "`parser/_schema_object.py:JsonSchemaObject.read_pattern_as_the_engines_compile_it`, reported by "
            "`parser/jsonschema.py:JsonSchemaParser._report_parse_diagnostics`"
        ),
        sites=(
            Home("parser/_schema_object.py", "JsonSchemaObject.read_pattern_as_the_engines_compile_it"),
            Home("parser/jsonschema.py", "JsonSchemaParser._report_parse_diagnostics"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether a pattern means the same when each control character in it is written as its escape (`\\n` for a "
            "line feed), which decides a pattern's raw literal: a regex reads `\\t`, `\\n`, `\\f` and `\\r` as the "
            "character each names, except after a backslash and in a verbose pattern, where the character is ignored "
            "or ends a comment, and `\\b` is a word boundary, not a backspace; a pattern for which it does not is "
            "written as the plain literal"
        ),
        homes=(Home("_regex_syntax.py", "control_escapes_keep_meaning"),),
        core="`_regex_syntax.py`: `control_escapes_keep_meaning`",
        adapters="`model/pydantic_v2/types.py:_get_regex_literal`",
        sites=(Home("model/pydantic_v2/types.py", "_get_regex_literal"),),
    ),
    DecisionHome(
        decision=(
            "how the config of a Pydantic v2 model takes Python's regex engine once a pattern needs it: "
            '`regex_engine="python-re"` unless the config already names an engine, which the model keeps (row '
            "`legacy-dataclass-overwrites-engine`: a dataclass writes it over the engine its config names); the config "
            "is stored with the items its template writes, which the readers take from there inside a parse"
        ),
        homes=(
            Home("model/pydantic_v2/base_model.py", "_PydanticV2Model._select_python_regex_engine"),
            Home("model/pydantic_v2/base_model.py", "_PydanticV2Model._store_config"),
            Home("model/pydantic_v2/base_model.py", "_PydanticV2Model._rendered_config_items"),
            Home("model/pydantic_v2/base_model.py", "_PydanticV2Model._own_patterns_select_python"),
        ),
        core=(
            "`model/pydantic_v2/base_model.py`: `_PydanticV2Model._select_python_regex_engine`, "
            "`_PydanticV2Model._store_config` (a model's config as a `ConfigDict`, a dataclass's as the mapping), "
            "`_PydanticV2Model._rendered_config_items`, `_PydanticV2Model._own_patterns_select_python` (whether a "
            "model's own patterns select the engine; a dataclass's are also those of the models its fields reach, "
            "`_FOLLOWS_PATTERN_REFERENCES`)"
        ),
        adapters=(
            "`BaseModel.refresh_field_config` (called by `BaseModel.__init__`, the pass `refresh_field_config` and "
            "`type_dict_keys_by_root_types`), `_PydanticV2Model.finalize_type_alias_users` (again from the final "
            "patterns of every BaseModel), `model/pydantic_v2/dataclass.py`: "
            "`DataClass._apply_lookaround_regex_engine` (lazily, from `imports` and `render`); the stores of "
            "`BaseModel.__init__` and `DataClass.__init__`; the readers `_applied_config_items`, "
            "`_pydantic_constructs`, `DataClass._takes_config_of`, `DataClass._bases_without_inherited_config`; "
            "`model/pydantic_v2/root_model.py`: `RootModel._decide_config_items` stores a root model's items again "
            "once its literal values and neutral `extra` are final, which no reader before it reads"
        ),
        sites=(
            Home("model/pydantic_v2/dataclass.py", "DataClass._apply_lookaround_regex_engine"),
            Home("model/pydantic_v2/dataclass.py", "DataClass.imports"),
            Home("model/pydantic_v2/dataclass.py", "DataClass.render"),
            Home("model/pydantic_v2/dataclass.py", "DataClass.__init__"),
            Home("model/pydantic_v2/dataclass.py", "DataClass._takes_config_of"),
            Home("model/pydantic_v2/dataclass.py", "DataClass._bases_without_inherited_config"),
            Home("model/pydantic_v2/root_model.py", "RootModel._decide_config_items"),
        ),
    ),
    DecisionHome(
        decision=(
            "targets (`--generate-client`, `--generate-server`): what an API object of an OpenAPI document says "
            "(method, path, parameters, bodies, responses, media, encodings, headers, links, callbacks, servers, "
            "security, schemes), read once where the API walk visits it, then recorded; no `YamlValue` or raw-key read "
            "after the walk (the served-document copier copies the recorded objects); the messages an AsyncAPI message "
            "value stands for, after every `$ref` and `oneOf`, with where each payload schema is and the `$ref` it is "
            "used as (`OpenAPIParser._reference_use`, as an OpenAPI body), recorded where the AsyncAPI walk reaches "
            "them and read by the channel reader (`_asyncapi_connections.py:_Reader.messages`)"
        ),
        homes=(
            Home("parser/asyncapi.py", "AsyncAPIParser._walked_message"),
            Home("_target_binding.py", "TargetAsyncAPIParser"),
            Home("parser/openapi_scope.py"),
        ),
        core=(
            "`parser/openapi_scope.py` (the walk); `parser/asyncapi.py:AsyncAPIParser._walked_message` (the AsyncAPI "
            "messages, recorded by `_target_binding.py:TargetAsyncAPIParser`); planned: `_target_walk.py`"
        ),
        adapters="`_fastapi/source_document.py` (copies the source as written, #4534)",
        sites=(Home("_fastapi/source_document.py"),),
    ),
    DecisionHome(
        decision=(
            "targets: where a `$ref` or a JSON pointer of a loaded document leads (no second `urljoin`/`unquote` "
            "follower in a target)"
        ),
        homes=(
            Home("parser/_schema_graph.py"),
            Home("parser/_api_reference.py"),
            Home("parser/openapi_scope.py"),
        ),
        core="`parser/_schema_graph.py`, `parser/_api_reference.py`, `parser/openapi_scope.py`",
        adapters="`_target_documents.py`",
        sites=(Home("_target_documents.py"),),
    ),
    DecisionHome(
        decision=(
            "which container a node of a type tree is in: the flag of each container slot (`is_frozen_set`, `is_set`, "
            "`is_sequence`, `is_list`, `is_mapping`, `is_dict`, `is_tuple`), the order in which a type hint looks for "
            "the slots it writes as a generic class (the tuple is a form of its own), and the kinds of slot a decision "
            "treats alike: mappings (a dict or a `Mapping`, which writes a key), item containers, sequences (a list or "
            "a `Sequence`), arrays (with the tuple), generic containers, any container, unhashable containers (what "
            "Pydantic validates as a list, a set or a dict) and sets, each without the slots a legacy row leaves out "
            "(`ContainerKind.without`); the class each slot is written as (a builtin or an abstract class of "
            "`collections.abc`, or the alias of `typing` without `--use-standard-collections`; a set, list or dict as "
            "the frozen set, `Sequence` or `Mapping` under `--use-generic-container-types`) and the slot each builtin "
            "container class stands for; the container a class an `x-python-type` names gives a node, its slot and, "
            "under `--use-x-python-type-as-written` where it is compatible with the schema "
            "(`DataType.given_container`), the class it is written as around the schema's item, key and value types, a "
            "tuple with `...`, whose values hash as that class; and the slot of each generic class a target's type "
            "view writes a container as (a `frozenset`, `Sequence` or `Mapping` under `--use-generic-container-types` "
            "reads as the slot that writes it), which the view keeps beside its class (`GenericType.slot`), so an "
            "`--import-overrides` entry that moves where the class is imported from leaves it"
        ),
        homes=(
            Home("_container_kinds.py", "CONTAINER_FLAGS"),
            Home("_container_kinds.py", "WRITTEN_CONTAINERS"),
            Home("_container_kinds.py", "SLOT_CLASSES"),
            Home("_container_kinds.py", "ContainerClass"),
            Home("_container_kinds.py", "written_slot"),
            Home("_container_kinds.py", "abstract_class_module"),
            Home("_container_kinds.py", "BUILTIN_CONTAINERS"),
            Home("_container_kinds.py", "CONTAINER_CLASSES"),
            Home("_container_kinds.py", "GIVEN_CONTAINERS"),
            Home("_container_kinds.py", "GivenContainer"),
            Home("_container_kinds.py", "given_container_of"),
            Home("_container_kinds.py", "hash_slot"),
            Home("_container_kinds.py", "ContainerKind"),
            Home("_container_kinds.py", "ITEM_CONTAINERS"),
            Home("_container_kinds.py", "SEQUENCES"),
            Home("_container_kinds.py", "ARRAYS"),
            Home("_container_kinds.py", "GENERIC_CONTAINERS"),
            Home("_container_kinds.py", "CONTAINERS"),
            Home("_container_kinds.py", "UNHASHABLE_CONTAINERS"),
            Home("_container_kinds.py", "SETS"),
            Home("_container_kinds.py", "LegacyKindRow"),
            Home("types.py", "_container_name"),
            Home("parser/jsonschema.py", "JsonSchemaParser"),
            Home("parser/jsonschema.py", "JsonSchemaParser._get_python_type_flags"),
            Home("parser/jsonschema.py", "JsonSchemaParser._container_flags"),
        ),
        core=(
            "`_container_kinds.py`: `CONTAINER_FLAGS`, `WRITTEN_CONTAINERS`, `SLOT_CLASSES` (`ContainerClass`), "
            "`written_slot`, `abstract_class_module`, `BUILTIN_CONTAINERS`, `CONTAINER_CLASSES` (read off "
            "`SLOT_CLASSES`), `GIVEN_CONTAINERS` (`GivenContainer`), `given_container_of`, `hash_slot`, "
            "`ContainerKind` (`node in MAPPINGS`, `ITEM_CONTAINERS`, `SEQUENCES`, `ARRAYS`, `GENERIC_CONTAINERS`, "
            "`CONTAINERS`, `UNHASHABLE_CONTAINERS`, `SETS`), `LegacyKindRow`; `types.py:_container_name` (the class a "
            "written slot is spelled as); `parser/jsonschema.py:JsonSchemaParser` (`_get_python_type_flags`, the slot "
            "an `x-python-type` gives, and `_container_flags`, the class)"
        ),
        adapters=(
            "the class a slot is written as: `types.py` (`_container_name`, the tuple of `_render_form`), "
            "`_target_binding.py:_Projector` (`_container`, which records the slot, `_structural`), "
            "`parser/_passes/_shared.py:_set_item_facts` (whether a set item hashes); the slot of a builtin class: "
            "`parser/_passes/modules.py:_is_builtin_type_collision`, `_target_types.py` (a container has no lexical "
            "kind); every caller asks a kind: `types.py:DataType._is_container`, "
            "`DataTypeManager._shape_constraint_keywords`, `_build_type_expr`; "
            "`model/msgspec.py:DataModelField._shape_meta_keywords`, `model/dataclass.py:_mapping_annotation`, "
            "`model/pydantic_v2/base_model.py:_value_hash_facts`; `parser/_sites.py:position_step`, "
            "`parser/_value_keywords.py:dropped_property_counts`, "
            "`parser/_schema_validators.py:get_executable_unique_items_paths`, `parser/jsonschema.py` (the property "
            "counts of a mapping field and root, `_get_root_model_sequence_type`), `parser/_passes/modules.py` "
            "(`collapse_site_facts`, `root_key_data_type`), `parser/_passes/render_prep.py` "
            "(`_is_generic_class_container`, `collect_set_item_references`), "
            "`parser/_passes/_shared.py:_unique_items_arrays`, `parser/base.py:Parser._get_duplicate_root_reference`, "
            "`model/pydantic_v2/root_model.py:RootModel.finalize_sequence_interface`; `_target_binding.py` (`_ALONE`, "
            "`_Projector._container`); a target's type view through `_target_types.py:container_slot` "
            "(`_target_types.py:TypeTables.contained`, `_client/codec_plan.py:_Planner.shape`, "
            "`_target_types.py:TypeTables.json_kinds`, `_target_module.py:TargetModule.generic`); the JSON kinds of a "
            "class by its name: `CONTAINER_CLASSES` (`_target_types.py:_LEAF_KINDS`); the key, snapshot and merge of "
            "every flag (`model/_field_imports.py:data_type_import_key`, `parser/_field_copies.py`) are other "
            "decisions; a kind without a legacy row's slots: `parser/_value_keywords.py` "
            "(`_carried_constraint_keywords`, `_enforcing_leaves`), `parser/_schema_validators.py` "
            "(`data_type_may_accept_array`, the item paths of `get_executable_unique_items_paths`), "
            "`parser/jsonschema.py` (the array of an `allOf` root), `parser/_passes/modules.py:collapse_site_facts` "
            "(`holder_has_container`)"
        ),
        sites=(
            Home("types.py", "_container_name"),
            Home("types.py", "_render_form"),
            Home("_target_binding.py", "_Projector"),
            Home("_target_binding.py", "_Projector._container"),
            Home("_target_binding.py", "_Projector._structural"),
            Home("parser/_passes/_shared.py", "_set_item_facts"),
            Home("parser/_passes/modules.py", "_is_builtin_type_collision"),
            Home("types.py", "DataType._is_container"),
            Home("types.py", "DataTypeManager._shape_constraint_keywords"),
            Home("types.py", "_build_type_expr"),
            Home("model/msgspec.py", "DataModelField._shape_meta_keywords"),
            Home("model/dataclass.py", "_mapping_annotation"),
            Home("model/pydantic_v2/base_model.py", "_value_hash_facts"),
            Home("parser/_sites.py", "position_step"),
            Home("parser/_value_keywords.py", "dropped_property_counts"),
            Home("parser/_schema_validators.py", "get_executable_unique_items_paths"),
            Home("parser/jsonschema.py", "JsonSchemaParser._get_root_model_sequence_type"),
            Home("parser/_passes/modules.py", "collapse_site_facts"),
            Home("parser/_passes/modules.py", "root_key_data_type"),
            Home("parser/_passes/render_prep.py", "_is_generic_class_container"),
            Home("parser/_passes/render_prep.py", "collect_set_item_references"),
            Home("parser/_passes/_shared.py", "_unique_items_arrays"),
            Home("parser/base.py", "Parser._get_duplicate_root_reference"),
            Home("model/pydantic_v2/root_model.py", "RootModel.finalize_sequence_interface"),
            Home("_target_types.py", "container_slot"),
            Home("_target_types.py", "TypeTables.contained"),
            Home("_client/codec_plan.py", "_Planner.shape"),
            Home("_target_types.py", "TypeTables.json_kinds"),
            Home("_target_module.py", "TargetModule.generic"),
            Home("model/_field_imports.py", "data_type_import_key"),
            Home("parser/_value_keywords.py", "_carried_constraint_keywords"),
            Home("parser/_value_keywords.py", "_enforcing_leaves"),
            Home("parser/_schema_validators.py", "data_type_may_accept_array"),
            Home("parser/_field_copies.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a `DataType` is copied: a node copy owns its lists and `kwargs` and keeps the private state of its "
            "original (`_copied_node`); a tree copy shares the References it names, registered with them or not and "
            "linked to its holders or not (`_copy_data_type`: `register_references`, `link_parents`); a deep copy "
            "copies the References too and keeps the private state but the `Literal` import alias, with its rendered "
            "None not rendered yet (`DataType.__deepcopy__`)"
        ),
        homes=(
            Home("types.py", "_copied_node"),
            Home("types.py", "_copy_data_type"),
            Home("types.py", "DataType.__deepcopy__"),
        ),
        core="`types.py`: `_copied_node`, `_copy_data_type`, `DataType.__deepcopy__`",
        adapters=(
            "`types.py:DataTypeManager.copy_data_type` (a leaf prototype takes a node copy, a nested one a deep copy), "
            "`parser/base.py:Parser._copy_model_type`, `model/msgspec.py:DataModelField._copy_data_type`"
        ),
        sites=(Home("types.py", "DataTypeManager.copy_data_type"),),
    ),
    DecisionHome(
        decision=(
            "how a field is copied: a copy owns its data type tree (`_copy_data_type`, or the type its caller built), "
            "its extras, validation aliases and mutable default, and is made with the names and aliases "
            "(`FieldIdentity`) and the other values (`FieldCopyFacts`: required, nullability, constraints, extras, "
            "read/write only, default) its caller gives, so no copy is finished by hand before it is installed"
        ),
        homes=(
            Home("model/base.py", "DataModelFieldBase.copy_owning"),
            Home("parser/_field_copies.py", "_copy_data_model_field"),
            Home("parser/_field_rows.py", "FieldIdentity"),
            Home("parser/_field_rows.py", "FieldCopyFacts"),
        ),
        core=(
            "`model/base.py:DataModelFieldBase.copy_owning`; `parser/_field_copies.py:_copy_data_model_field` (its "
            "typed form); `parser/_field_rows.py`: `FieldIdentity`, `FieldCopyFacts`"
        ),
        adapters=(
            "every copy calls the core, `model/base.py:DataModelFieldBase.copy_deep` (public) directly and the others "
            "through the overridable `Parser._copy_model_field` hook outside `parser/_field_copies.py`: "
            "`parser/_field_copies.py:_copy_resolved_inherited_field` (both placeholder kinds), "
            "`parser/_passes/_shared.py:override_required_field` (its plain copy), "
            "`parser/_inherited_schemas.py:build_missing_required_field`, "
            "`parser/_passes/plans.py:flatten_base_layouts`, `parser/_passes/render_prep.py:copy_inherited_conflicts`, "
            "`parser/_rw_split.py`: `copy_unregistered_fields`, `prepare_rw_model_variant`, "
            "`parser/graphql.py:GraphQLParser` (inherited typename fields); the target binding records copies through "
            "the hook (`_target_binding.py:_TargetRecording._copy_model_field`); not routed: the functional-syntax "
            "view of `model/typed_dict.py:TypedDict.all_fields`, and the field "
            "`parser/openapi.py:OpenAPIParser.parse_object_fields` rebuilds from a field's `__dict__`"
        ),
        sites=(
            Home("model/base.py", "DataModelFieldBase.copy_deep"),
            Home("parser/_field_copies.py", "_copy_resolved_inherited_field"),
            Home("parser/_passes/_shared.py", "override_required_field"),
            Home("parser/_inherited_schemas.py", "build_missing_required_field"),
            Home("parser/_passes/plans.py", "flatten_base_layouts"),
            Home("parser/_passes/render_prep.py", "copy_inherited_conflicts"),
            Home("parser/_rw_split.py", "copy_unregistered_fields"),
            Home("parser/_rw_split.py", "prepare_rw_model_variant"),
            Home("parser/graphql.py", "GraphQLParser"),
            Home("_target_binding.py", "_TargetRecording._copy_model_field"),
            Home("model/typed_dict.py", "TypedDict.all_fields"),
            Home("parser/openapi.py", "OpenAPIParser.parse_object_fields"),
        ),
    ),
    DecisionHome(
        decision=(
            "making a field required where a required name asks for it: its `required`, and under `--use-default` the "
            "default it keeps beside it when it has one (`uses_default_with_required`)"
        ),
        homes=(
            Home("parser/generation.py", "GenerationStore.require_field"),
            Home("parser/generation.py", "GraphBuilder.require_field"),
            Home("parser/_field_rows.py", "uses_default_with_required"),
        ),
        core=(
            "`parser/generation.py:GenerationStore.require_field`, through `GraphBuilder.require_field`; "
            "`parser/_field_rows.py:uses_default_with_required`"
        ),
        adapters=(
            "`parser/jsonschema.py:JsonSchemaParser._parse_object_common_part` (the object's required names, and a "
            "property its bases require, which keeps no default beside it), `parser/_rw_split.py`: "
            "`collect_inherited_fields_for_request_response`, `get_raw_inherited_fields` (the inherited fields of the "
            "request and response variants), `parser/_passes/plans.py:flatten_base_layouts` (a field a model adopts "
            "from a flattened base); a copy takes `required` when it is made (`FieldCopyFacts`)"
        ),
        sites=(
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_object_common_part"),
            Home("parser/_rw_split.py", "collect_inherited_fields_for_request_response"),
            Home("parser/_rw_split.py", "get_raw_inherited_fields"),
            Home("parser/_passes/plans.py", "flatten_base_layouts"),
        ),
    ),
    DecisionHome(
        decision=(
            "which data types and models name a `Reference` (its back links, `Reference.children`, in the order they "
            "were added): a data type when it is built, registered as a copy or moved to another reference, and a "
            "model for each base when it is built, come after the other children (`add_child`); a data type that "
            "leaves its reference drops every record of itself (`drop_child`), the type a root-model collapse replaces "
            "drops with every child nothing holds (`drop_child_and_detached`), a type built only to read facts from "
            "drops every node of its tree (`drop_tree`), and disposing a parse forgets them all (`clear_children`); "
            "the children the store redirects, and asks the modules of, are the data types (`data_type_children`: a "
            "data type names its reference through `replace_reference`), the others are the models that inherit it "
            "(`Reference.iter_data_model_children`, which `--use-serialize-as-any` asks for a subclass with fields)"
        ),
        homes=(
            Home("_graph_links.py", "add_child"),
            Home("_graph_links.py", "drop_child"),
            Home("_graph_links.py", "drop_child_and_detached"),
            Home("_graph_links.py", "drop_tree"),
            Home("_graph_links.py", "clear_children"),
            Home("_graph_links.py", "data_type_children"),
            Home("reference.py", "Reference.iter_data_model_children"),
        ),
        core=(
            "`_graph_links.py`: `add_child`, `drop_child`, `drop_child_and_detached`, `drop_tree`, `clear_children`, "
            "`data_type_children`; `reference.py:Reference.iter_data_model_children`"
        ),
        adapters=(
            "every caller calls the core: `types.py:DataType` (`__init__`, `replace_reference`, `register_reference`, "
            "`unregister_reference`), `parser/_rw_split.py:unregister_temporary_type`, "
            "`_target_binding.py:_Models.lightweight`, "
            "`model/pydantic_v2/base_model.py:BaseModel.create_typed_extra_field`, `model/base.py:DataModel` "
            "(`__init__`, `replace_children_in_models`), `reference.py:Reference.replace_children_references`, "
            "`parser/generation.py:GenerationStore` (`_dispose`, `redirect_reference_users`, "
            "`has_users_in_other_modules`, `redirect_model_reference_users`, `collapse_root_data_type`, "
            "`_replace_data_type_reference`), `model/pydantic_v2/types.py:_writes_serialize_as_any` (whether "
            "`--use-serialize-as-any` wraps a type naming the reference, which "
            "`PydanticV2DataType._should_wrap_with_serialize_as_any` and `_target_binding.py:_Binder.reference` ask)"
        ),
        sites=(
            Home("types.py", "DataType"),
            Home("types.py", "DataType.__init__"),
            Home("types.py", "DataType.replace_reference"),
            Home("types.py", "DataType.register_reference"),
            Home("types.py", "DataType.unregister_reference"),
            Home("parser/_rw_split.py", "unregister_temporary_type"),
            Home("_target_binding.py", "_Models.lightweight"),
            Home("model/pydantic_v2/base_model.py", "BaseModel.create_typed_extra_field"),
            Home("model/base.py", "DataModel"),
            Home("model/base.py", "DataModel.__init__"),
            Home("model/base.py", "DataModel.replace_children_in_models"),
            Home("reference.py", "Reference.replace_children_references"),
            Home("parser/generation.py", "GenerationStore"),
            Home("parser/generation.py", "GenerationStore._dispose"),
            Home("parser/generation.py", "GenerationStore.redirect_reference_users"),
            Home("parser/generation.py", "GenerationStore.has_users_in_other_modules"),
            Home("parser/generation.py", "GenerationStore.redirect_model_reference_users"),
            Home("parser/generation.py", "GenerationStore.collapse_root_data_type"),
            Home("parser/generation.py", "GenerationStore._replace_data_type_reference"),
            Home("model/pydantic_v2/types.py", "_writes_serialize_as_any"),
            Home("model/pydantic_v2/types.py", "PydanticV2DataType._should_wrap_with_serialize_as_any"),
            Home("_target_binding.py", "_Binder.reference"),
        ),
    ),
    DecisionHome(
        decision=(
            "which field and model hold a data type node (its other back links, through `parent`): a data type is the "
            "holder of its children, its members and then its dict key (`type_children`, the one list of them that "
            "every walk of a type tree reads; `link_members`, which every copy that keeps its parents and every member "
            "msgspec writes again calls, so that collapse sees a dict key as it sees a member), its nearest holder "
            "that is a field, through the data types above it (a field is the holder with "
            "`invalidate_semantic_caches`; a model has no `parent`), its outermost holder (the field's model, or the "
            "top of a chain nothing holds), and the models of a list whose `base_classes` hold a base class node, "
            "which has no `parent` (the store scans its models while its facts are stale and a refresh is deferred, "
            "and reads the base owners its facts index otherwise); a tree its holder lets go of clears the `parent` of "
            "every node (`clear_parents`)"
        ),
        homes=(
            Home("_graph_links.py", "type_children"),
            Home("_graph_links.py", "link_members"),
            Home("_graph_links.py", "nearest_field"),
            Home("_graph_links.py", "chain_root"),
            Home("_graph_links.py", "base_owners"),
            Home("_graph_links.py", "clear_parents"),
        ),
        core=(
            "`_graph_links.py`: `type_children`, `link_members`, `nearest_field`, `chain_root`, `base_owners`, "
            "`clear_parents`"
        ),
        adapters=(
            "every caller calls the core: the walks of a type tree (`types.py`: `DataType.walk`, `_link_parts`; "
            "`_parse_memo.py:ParseMemo._references`; `parser/_origins.py:with_type_origin`; `parser/generation.py`: "
            "`_tree_nodes`, `GenerationIndex.inlined_holders_of`; `parser/_passes/render_prep.py`: "
            "`_contains_model_reference`, `_iter_alias_references`; `parser/_passes/modules.py:_value_positions`; "
            "`parser/_type_overrides.py:apply_override_to_data_type`; "
            "`model/_field_imports.py:write_pre_render_nullability`; `parser/_held_null.py:_types`, over the "
            "decision's views; `model/pydantic_base.py:DataModelField._has_anyurl_outside_container`), "
            "`_parse_memo.py:ParseMemo.bump_node`, `types.py:_copy_data_type`, "
            "`model/msgspec.py:DataModelField._unconstrained_members`, "
            "`model/msgspec.py:DataModelField._written_type`, `model/pydantic_base.py:DataModelField._written_type`, "
            "`model/pydantic_v2/base_model.py:BaseModel.create_typed_extra_field`, "
            "`parser/_passes/_shared.py:_detach_deferred_inherited_field_parents`, "
            "`parser/generation.py:GenerationStore._cache_owners_for_data_type`, "
            "`model/base.py:DataModel.replace_children_in_models`; the public `_internal_utils.py:get_most_of_parent` "
            "(the nearest holder of a class, re-exported by `parser/base.py`) keeps its own walk; not this rule: "
            "`DataType.all_data_types` (the public postorder a subclass may override), "
            "`parser/generation.py:_walk_tree` (labels the role and position of each child) and `DataType.all_imports` "
            "(the members only)"
        ),
        sites=(
            Home("types.py", "DataType.walk"),
            Home("types.py", "_link_parts"),
            Home("_parse_memo.py", "ParseMemo._references"),
            Home("parser/_origins.py", "with_type_origin"),
            Home("parser/generation.py", "_tree_nodes"),
            Home("parser/generation.py", "GenerationIndex.inlined_holders_of"),
            Home("parser/_passes/render_prep.py", "_contains_model_reference"),
            Home("parser/_passes/render_prep.py", "_iter_alias_references"),
            Home("parser/_passes/modules.py", "_value_positions"),
            Home("parser/_type_overrides.py", "apply_override_to_data_type"),
            Home("model/_field_imports.py", "write_pre_render_nullability"),
            Home("parser/_held_null.py", "_types"),
            Home("model/pydantic_base.py", "DataModelField._has_anyurl_outside_container"),
            Home("_parse_memo.py", "ParseMemo.bump_node"),
            Home("types.py", "_copy_data_type"),
            Home("model/msgspec.py", "DataModelField._unconstrained_members"),
            Home("model/msgspec.py", "DataModelField._written_type"),
            Home("model/pydantic_base.py", "DataModelField._written_type"),
            Home("model/pydantic_v2/base_model.py", "BaseModel.create_typed_extra_field"),
            Home("parser/_passes/_shared.py", "_detach_deferred_inherited_field_parents"),
            Home("parser/generation.py", "GenerationStore._cache_owners_for_data_type"),
            Home("model/base.py", "DataModel.replace_children_in_models"),
            Home("_internal_utils.py", "get_most_of_parent"),
            Home("types.py", "DataType.all_data_types"),
            Home("parser/generation.py", "_walk_tree"),
            Home("types.py", "DataType.all_imports"),
            Home("parser/base.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "how the generation store indexes the data type occurrences of a model, whether a full build records the "
            "model or a refresh records it again: each occurrence by id, the occurrence its object is found by (the "
            "last in `(owner parse order, id)` order) with the ids of every occurrence of an object that occurs more "
            "than once, the owners of a base class node, and the sites of each target in that order wherever a site is "
            "inserted; an occurrence the model keeps keeps its id, the others take new ids; an id is its slot (a "
            "field's tree or the base list) above a sequence number, so a refresh records only the slots that changed"
        ),
        homes=(
            Home("parser/generation.py", "GenerationIndexBuilder._record_slots"),
            Home("parser/generation.py", "GenerationIndexBuilder._record_data_types"),
            Home("parser/generation.py", "GenerationIndexBuilder._record_fact"),
            Home("parser/generation.py", "GenerationIndexBuilder._index_data_type_object"),
            Home("parser/generation.py", "GenerationIndexBuilder._add_target_site"),
            Home("parser/generation.py", "GenerationIndexBuilder._forget_fact"),
            Home("parser/generation.py", "GenerationIndexBuilder._update_base_owners"),
        ),
        core=(
            "`parser/generation.py:GenerationIndexBuilder._record_slots` (the slots and their sites) and "
            "`_record_data_types` (the ids and facts), with `_record_fact` (`_index_data_type_object`, "
            "`_add_target_site`), `_forget_fact` and `_update_base_owners`"
        ),
        adapters=(
            "`GenerationIndexBuilder.build` (every model, with no old ids) and `GenerationIndexBuilder.update` (the "
            "appended models and the slots of the changed ones, whose sites it replaces); only the key order of the "
            "base owners differs between the two, and nothing reads it"
        ),
        sites=(),
    ),
    DecisionHome(
        decision=(
            "whether text is a name Python binds (an identifier and no keyword), alone or as a dotted path: row "
            "`target` (the target's identifier characters, for every name the generator writes or checks for the "
            "target, the names an option, template data or a schema extension gives included: "
            "`--schema-validator-base-class-name`, `--validators` field names and functions, `--additional-imports`, "
            "the paths `--base-class`, `--base-class-map`, `--class-decorators` and `--type-overrides` name, "
            "`customBasePath`, `customTypePath`, `x-python-import` (its `qualname`, `native-field` and `symbols` "
            "included), template keys and the bare type names of template data) and `ascii` (Starlette's path "
            "placeholders, the field names the Pydantic resolver keeps as given); whether Python imports a module by a "
            "part of its path: row `target` (a name by the target's rule, the parts `--treat-dot-as-module` writes for "
            "the dots of a name), `keyword` (a keyword alone is refused, the parts written without it) and "
            "`legacy-keyword-directory-kept` (an identifier, a keyword kept)"
        ),
        homes=(
            Home("_target_unicode.py", "is_python_name"),
            Home("_target_unicode.py", "is_dotted_python_name"),
            Home("_target_unicode.py", "is_importable_part"),
        ),
        core="`_target_unicode.py`: `is_python_name`, `is_dotted_python_name`, `is_importable_part`",
        adapters=(
            "every caller calls the core with its row; the parts of a module path: a directory of the input that keeps "
            "its name (`model/base.py:_directory_module_name`, the oldest target) and the relative imports a module "
            "writes (`parser/_passes/imports.py:reject_unimportable_imports`); untrusted data (template keys and "
            "options, `--validators` field names and functions, `--additional-imports`, the paths `--base-class`, "
            "`--base-class-map`, `--class-decorators` and `--type-overrides` name (`base_config.py:_NAMED_PATHS`), "
            "`customBasePath`, the type names of template data) goes through "
            "`python_literal.py:_normalized_safe_python_name` with its target, which reads a `str` past its subclass; "
            "a check made before the run's target is final (the config validators, "
            "`validators.py:ValidatorDefinition`, "
            "`parser/_schema_object.py:JsonSchemaObject.validate_custom_base_path`) refuses only what the newest "
            "target does not bind, and the run checks again for its target where it writes the name "
            "(`Parser.__init__`, `Parser._resolve_base_class`, the model's `validators.py:normalize_validators`), as "
            "`_target_config.py:TargetConfig` checks for the newest target and then `check_target`; a model reads its "
            "target from `DataModel.target_python_version` (the newest by default), which "
            "`model/base.py:_build_for_target` sets while the model is constructed, so its constructor's decisions "
            "read it and no constructor takes an argument for it: the parser builds every model through "
            "`Parser._new_model`, and a model built from another one takes that one's target; the nodes of a Python "
            "type expression (`_python_type_annotation.py`) hold names of the newest target, whichever parser gave "
            "them, and a runtime class whose name a node refuses is opaque text "
            "(`_python_type_runtime.py:_runtime_symbol`, which row `legacy-host-identifier-runtime-names` makes read "
            "the running interpreter's identifiers too); text the running interpreter reads for a name does not depend "
            "on which characters it knows: the path of a class decorator and the decorator the matcher names are read "
            "with letters lent for the others (`_target_unicode.py:host_identifiers`, in "
            "`_python_decorator.py:python_decorator_path` and `is_named_python_decorator`), and a `--type-overrides` "
            "value that is a name or a dotted path is read by the target's characters, never by the parser "
            "(`parser/_type_overrides.py:_override_expression`, `base_config.py:_type_override_class_paths`); the "
            "field-name resolver's keyword escape (`reference.py:FieldNameResolver.get_valid_name`) and the decorator "
            "matcher's tokens (`_python_decorator.py:_parse_direct_target`) are other decisions"
        ),
        sites=(
            Home("model/base.py", "_directory_module_name"),
            Home("parser/_passes/imports.py", "reject_unimportable_imports"),
            Home("python_literal.py", "_normalized_safe_python_name"),
            Home("validators.py", "ValidatorDefinition"),
            Home("parser/_schema_object.py", "JsonSchemaObject.validate_custom_base_path"),
            Home("validators.py", "normalize_validators"),
            Home("_target_config.py", "TargetConfig"),
            Home("_target_config.py", "TargetConfig.check_target"),
            Home("model/base.py", "_build_for_target"),
            Home("_python_type_runtime.py", "_runtime_symbol"),
            Home("_target_unicode.py", "host_identifiers"),
            Home("_python_decorator.py", "python_decorator_path"),
            Home("_python_decorator.py", "is_named_python_decorator"),
            Home("parser/_type_overrides.py", "_override_expression"),
            Home("base_config.py", "_type_override_class_paths"),
            Home("reference.py", "FieldNameResolver.get_valid_name"),
            Home("_python_decorator.py", "_parse_direct_target"),
            Home("_python_type_annotation.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the key two names or paths of generated files compare in, as case-insensitive file systems compare them: "
            "NFC, then case-folded by the target's rules (the generated files of an API run, the client's helper names "
            "and webhook packages, the reserved file names `__init__` and the Windows devices, the folded name scopes "
            "of client resources and server routers, the client's resource namespaces); a derived name of generated "
            "API code is written in the NFKC form Python reads an identifier in (`TargetNames.function`, "
            "`TargetNames.pascal`), so two tags Python reads as one name take the duplicate suffix"
        ),
        homes=(
            Home("_target_naming.py", "fold_key"),
            Home("_target_naming.py", "TargetNames"),
        ),
        core="`_target_naming.py`: `fold_key`, `TargetNames`",
        adapters=(
            "every caller calls the core; the order of the server entrypoint's imports "
            "(`_fastapi/render.py:ServerRenderer.main`) sorts case-folded and is another decision"
        ),
        sites=(Home("_fastapi/render.py", "ServerRenderer.main"),),
    ),
    DecisionHome(
        decision=(
            "which names the builtins namespace has, by the static table and never the running interpreter: its "
            "classes and `None`, the names an annotation writes bare, of the target (row `target`: type overrides, "
            "bare type names and the builtins that a module import, a package's submodule or a field hides, where a "
            "name a later Python adds is free for a user's symbol) or of every supported target (row `any-target`: a "
            "`builtins.X` path that some target has is told from an unknown one, and refused for a target without it); "
            "with its functions and constants, the names a field hides (`target_builtin_names`); the public attributes "
            "of `str`, `int`, `float` and `bytes` on the target, which the members of a subclass enum avoid "
            "(`target_builtin_type_attributes`)"
        ),
        homes=(
            Home("_python_target_names.py", "is_builtin_type_name"),
            Home("_python_target_names.py", "target_builtin_names"),
            Home("_python_target_names.py", "target_builtin_type_attributes"),
        ),
        core=(
            "`_python_target_names.py`: `is_builtin_type_name`, `target_builtin_names`, "
            "`target_builtin_type_attributes`"
        ),
        adapters=(
            "every caller calls the core with its row: `_python_type_import_registry.py` "
            "(`is_python_builtin_type_name`, `get_qualified_python_type_import_path`), `parser/base.py:Parser` "
            "(`builtin_names`); `parser/_passes/imports.py` (`_alias_builtin_shadowing_module_imports`, "
            "`_alias_builtins_hidden_by_submodules`, `_bind_module_field_names`, all of the target); "
            "`reference.py:EnumFieldNameResolver` reads the attributes of its target, or of the oldest supported "
            "target (`PythonVersionMin`) without one (API only)"
        ),
        sites=(
            Home("_python_type_import_registry.py", "is_python_builtin_type_name"),
            Home("_python_type_import_registry.py", "get_qualified_python_type_import_path"),
            Home("parser/base.py", "Parser"),
            Home("parser/_passes/imports.py", "_alias_builtin_shadowing_module_imports"),
            Home("parser/_passes/imports.py", "_alias_builtins_hidden_by_submodules"),
            Home("parser/_passes/imports.py", "_bind_module_field_names"),
            Home("reference.py", "EnumFieldNameResolver"),
        ),
    ),
    DecisionHome(
        decision=(
            "which top-level modules the standard library of the target has, by the static table and never the running "
            "interpreter"
        ),
        homes=(Home("_python_target_names.py", "target_stdlib_module_names"),),
        core="`_python_target_names.py:target_stdlib_module_names`",
        adapters=(
            "`_builtin_formatter.py:_import_category` (the standard-library import section); "
            "`parser/_type_overrides.py:bind_python_type` (a two-part `Literal` value whose first name is such a "
            "module names one of its symbols, never a member of a class of the same name)"
        ),
        sites=(
            Home("_builtin_formatter.py", "_import_category"),
            Home("parser/_type_overrides.py", "bind_python_type"),
        ),
    ),
    DecisionHome(
        decision=(
            "how `repr()` writes text and the values that hold it, so that a message quoting a name or path a user "
            "gave and a string literal of generated code read the same on every interpreter: a character that the "
            "newest target prints is itself and any other its escape, by the static tables (`ASSIGNED`, `UNPRINTABLE`) "
            "and never the running interpreter; a string of a class that keeps the `repr()` of `str` is its text (but "
            "row `legacy-str-subclass-host-repr`); a tuple, list, set or dict is written as `repr()` writes it around "
            "the `repr()` of its items (`[...]` inside itself), by the displays that also write a container, a "
            "subclass's too, for `represent_python_value` and `represent_untrusted_python_value`, which sort a set's "
            "items (`_SetOrder`); `pprint` wraps the strings it writes so"
        ),
        homes=(
            Home("_target_unicode.py", "string_repr"),
            Home("python_literal.py", "stable_repr"),
            Home("python_literal.py", "_represent_container"),
            Home("python_literal.py", "stable_pformat"),
        ),
        core=(
            "`_target_unicode.py:string_repr`, `python_literal.py`: `stable_repr`, `_represent_container`, "
            "`stable_pformat`"
        ),
        adapters=(
            "callers of the core: `python_literal.py` (`represent_python_value`, `represent_untrusted_python_value`, "
            "`message_repr`, which the messages of the name checks call: `validators.py`, `parser/_type_overrides.py`, "
            "`model/base.py`), the `repr` and `pprint` filters of the model templates (`model/base.py`, "
            "`model/_compiled_template_runtime.py:filter_repr` and `filter_pprint`), the literals the models write "
            "themselves (`model/`: validator fields, msgspec tags, discriminators, examples, `extra_items`, Literal "
            "sources, runtime validation rules, the `deprecated` message), the other messages that quote a value and "
            "the code that `_client`, `_fastapi` and the other targets write; `imports.py`, which the literal writer "
            "imports, calls `string_repr`; not routed: the builtin formatter (`_builtin_line_split.py:_string_split` "
            "splits the text of string literals that already exist and never writes one), the requirement table's own "
            "error (`_requirements.py`, a leaf that names its ASCII rows), the `AttributeError` of a module "
            "`__getattr__`, which writes the interpreter's own message and runs while modules import, the `repr()` of "
            "bytes, which is ASCII, and the keys that compare values only "
            "(`parser/_all_of_values.py:schema_signature`, `parser/avro.py`, `_recommend/_signals.py`)"
        ),
        sites=(
            Home("python_literal.py", "represent_python_value"),
            Home("python_literal.py", "represent_untrusted_python_value"),
            Home("python_literal.py", "message_repr"),
            Home("model/_compiled_template_runtime.py", "filter_repr"),
            Home("model/_compiled_template_runtime.py", "filter_pprint"),
            Home("_builtin_line_split.py", "_string_split"),
            Home("parser/_all_of_values.py", "schema_signature"),
            Home("validators.py"),
            Home("parser/_type_overrides.py"),
            Home("model/base.py"),
            Home("imports.py"),
            Home("_requirements.py"),
            Home("parser/avro.py"),
            Home("_recommend/_signals.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the supported target versions and how a dotted version compares: its integer parts (a target Pydantic "
            "version, the minimum of a requirement row), the oldest target Python version (`PythonVersionMin`) and the "
            "newest (`NEWEST_TARGET`: the default target on an interpreter newer than every target, and the target "
            "whose identifier rules accept every identifier)"
        ),
        homes=(
            Home("enums.py", "_version_key"),
            Home("_format_types.py", "PythonVersion.version_key"),
            Home("_format_types.py", "PythonVersionMin"),
            Home("_format_types.py", "NEWEST_TARGET"),
        ),
        core=(
            "`enums.py:_version_key`, `_format_types.py` (`PythonVersion.version_key`, `PythonVersionMin`, "
            "`NEWEST_TARGET`)"
        ),
        adapters=(
            "`enums.py:_pydantic_version_key`, `_requirements.py` (the minimum keys of the rows), `model/__init__.py` "
            "(`DEFAULT_TARGET_PYTHON_VERSION`), `_target_unicode.py` (the identifier rules of the newest target)"
        ),
        sites=(
            Home("enums.py", "_pydantic_version_key"),
            Home("_requirements.py"),
            Home("_target_unicode.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the target Python version a generated construct needs, as an option's documentation, a message or an "
            "implied setting names it: the minimum of the construct's row in the requirement table"
        ),
        homes=(Home("_requirements.py", "REQUIREMENTS"),),
        core="`_requirements.py:REQUIREMENTS`",
        adapters=(
            "`_option_rules.py:OPTION_RELATIONS` (the `--keyword-only` and `--use-specialized-enum` relations), "
            "`base_config.py:implied_target_settings` (the target a target implies for `StrEnum`), "
            "`parser/_type_overrides.py:_base_class_refusal` (the target that can subclass `typing.Any`)"
        ),
        sites=(
            Home("base_config.py", "implied_target_settings"),
            Home("parser/_type_overrides.py", "_base_class_refusal"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a name that its scope already holds is told apart: the first candidate of the feature's sequence that "
            "the scope does not hold, row `underscore` (`name_1`: fields, target scopes and imports, `_internal` "
            "modules, merged classes, validator functions, import aliases), `joined` (`Name1`: classes of the models "
            "and of target scopes, which take the duplicate name suffix of the model type: its "
            "`--duplicate-name-suffix` entry, the default entry, else the suffix given alone; the exports of an "
            "`__init__.py` whose name a prefixed name of `--all-exports-collision-strategy` takes get none), "
            "`joined-from-2` (`Name2`: converted definitions, schema validator helpers), `underscore-from-2` "
            "(`name_2`: input model definitions, bundled components of the served document), `joined-from-0` (route "
            "slots) and `padded` (`name_`: field-collision import aliases, the starred-parse sentinel)"
        ),
        homes=(
            Home("_free_names.py", "first_free"),
            Home("_free_names.py", "candidate_at"),
            Home("_free_names.py", "is_numbered"),
            Home("reference.py", "ModelResolver._duplicate_name_suffix"),
            Home("reference.py", "ModelResolver._name_taken"),
            Home("reference.py", "ModelResolver.reserved_for_another"),
        ),
        core=(
            "`_free_names.py`: `first_free`, `candidate_at`, `is_numbered`; `reference.py`: "
            "`ModelResolver._duplicate_name_suffix`, `ModelResolver._name_taken` (the test of the models' class names, "
            "by row: `plain`, `beside-yielding` for a model that does not yield beside yielding names, "
            "`yielding-module` for a yielding name, `map-conflict` for a `--model-name-map` value; every row but "
            "`map-conflict` takes a name `--model-name-map` reserves for other models, "
            "`ModelResolver.reserved_for_another`, which the `module` scheme of L3 reads too)"
        ),
        adapters=(
            "every caller passes its scope's test and its hints, and the target scopes read the models' suffix and "
            "naming strategy rows (`_target_naming.py:TargetNames.of`: the rows of `reference.py:_NAMING_STRATEGIES` "
            "that `ModelResolver._naming_rows` applies, which say whether a strategy prefixes a held name, with which "
            "class-name join (the target cases it as a model class name) and by which enclosing scopes, and whether it "
            "claims declared names first; other names join with `_`); the field-name resolver's loop "
            "(`reference.py:FieldNameResolver.get_valid_name`) makes a name valid and numbers the name before its case "
            "is changed"
        ),
        sites=(
            Home("_target_naming.py", "TargetNames.of"),
            Home("reference.py", "ModelResolver._naming_rows"),
            Home("reference.py", "FieldNameResolver.get_valid_name"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a class name is made from the words of a free text: its runs of ASCII letters and digits, each "
            "titled, joined; the feature's row is how a word is titled (`first_upper`: the converters' definition and "
            "namespace names, MCP tools, AsyncAPI, the recommender's `--class-name` for an input file; `str.title`: a "
            "schema title, `title_to_class_name`; the XML Schema titler for its namespace names), the prefix a name "
            "that would start with a digit takes (`Model`: MCP tools and the recommender) and the name of a text "
            "without a word (each caller's)"
        ),
        homes=(
            Home("_name_words.py", "pascal_words"),
            Home("_name_words.py", "first_upper"),
            Home("_name_words.py", "ASCII_WORD"),
        ),
        core="`_name_words.py`: `pascal_words`, `first_upper`, `ASCII_WORD`",
        adapters=(
            "every caller calls the core with its row: `parser/base.py:title_to_class_name`, "
            "`parser/_convert_common.py:_namespace_name`, `parser/mcp.py:_to_definition_name`, "
            "`parser/asyncapi.py:_make_model_name`, `_recommend/_rules.py:pascal_case_input_stem`, the Avro and XML "
            "Schema titlers"
        ),
        sites=(
            Home("parser/base.py", "title_to_class_name"),
            Home("parser/_convert_common.py", "_namespace_name"),
            Home("parser/mcp.py", "_to_definition_name"),
            Home("parser/asyncapi.py", "_make_model_name"),
            Home("_recommend/_rules.py", "pascal_case_input_stem"),
        ),
    ),
    DecisionHome(
        decision=(
            "the name L1 gives a model it builds for another model, the parent: the parent's name and the word of the "
            "model's role, after the parent's allocated name for the enum of a nullable enum (`Enum`) and after the "
            "name the parent asked for otherwise: the model of a map's values (`AdditionalProperty`), the enum of an "
            "allOf array's items (`Item`), the map branch of a type list (`Object`), the constrained branch of a type "
            "list (its type, titled) and the response of a status code under `--use-status-code-in-response-name` (the "
            "code, capitalized); the request and response variants of a model (the suffix goes before a trailing "
            "`--class-name-suffix` and its digits); a union branch without a name of its own takes the union's name "
            "(row `legacy-variant-takes-parent-name`)"
        ),
        homes=(
            Home("parser/_naming.py", "composed_name"),
            Home("parser/_naming.py", "ComposedRole"),
            Home("parser/_naming.py", "rw_variant_name"),
        ),
        core="`parser/_naming.py`: `composed_name` (`ComposedRole`), `rw_variant_name`",
        adapters=(
            "`parser/jsonschema.py:JsonSchemaParser` (`_parse_all_of_root_value`, "
            "`_parse_additional_properties_value`, `_build_constrained_type_union`, "
            "`_parse_array_union_constrained_branch`, `parse_enum`), "
            "`parser/_object_maps.py:get_typed_additional_properties_field`, "
            "`parser/_schema_validators.py:collect_pattern_property_validators`, "
            "`parser/openapi.py:OpenAPIParser.parse_responses`, `parser/_rw_split.py:get_rw_model_variant_reference`, "
            "`parser/_union_tags.py:parse_combined_schema_items`"
        ),
        sites=(
            Home("parser/jsonschema.py", "JsonSchemaParser"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_all_of_root_value"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_additional_properties_value"),
            Home("parser/jsonschema.py", "JsonSchemaParser._build_constrained_type_union"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_array_union_constrained_branch"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_enum"),
            Home("parser/_object_maps.py", "get_typed_additional_properties_field"),
            Home("parser/_schema_validators.py", "collect_pattern_property_validators"),
            Home("parser/openapi.py", "OpenAPIParser.parse_responses"),
            Home("parser/_rw_split.py", "get_rw_model_variant_reference"),
            Home("parser/_union_tags.py", "parse_combined_schema_items"),
        ),
    ),
    DecisionHome(
        decision=(
            "the model whose builder built a model (its owner), and what the code that names a model claims for it, "
            "recorded while L1 parses when a naming rule reads them: the builder frames open when a model's location "
            "is recorded (a read or write variant stands for its base in them, a document parses with none open), "
            "whether the model is an entry of a definitions container of its document, the model a name is composed "
            "from (`Composed`: the enum of a nullable enum, the model of a typed extra field's values, a read or write "
            "variant) or the position of a union branch named as the union (`Ordinal`), the part of a branch's name "
            "its tag gives (`Tag`), the union member and the shared part an allOf builds for a oneOf or anyOf it holds "
            "(`AllOfPart`), and the name each model asked the resolver for (`reference.py:NameRequest`)"
        ),
        homes=(
            Home("parser/_naming.py", "NamingFrames"),
            Home("parser/_naming.py", "NamingScope"),
            Home("parser/_naming.py", "UNRECORDED"),
            Home("parser/_naming.py", "BuildFrame"),
            Home("parser/_naming.py", "BuildRecord"),
            Home("parser/_naming.py", "BuildRecord.owner"),
            Home("parser/_naming.py", "BuildRecord.root"),
            Home("parser/_naming.py", "BuildRecord.claim_for"),
            Home("parser/_naming.py", "Composed"),
            Home("parser/_naming.py", "Ordinal"),
            Home("parser/_naming.py", "Tag"),
            Home("parser/_naming.py", "AllOfPart"),
        ),
        core=(
            "`parser/_naming.py`: `NamingFrames`, `NamingScope` (`UNRECORDED`), `BuildFrame`, `BuildRecord` (`owner`, "
            "`root`, `claim_for`), `Composed`, `Ordinal`, `Tag`, `AllOfPart`"
        ),
        adapters=(
            "`parser/base.py:Parser._start_naming_frames` (`_records_naming_claims`), `parser/jsonschema.py`: the six "
            "model builders, whose bodies run in a frame (`_model_frame`), so the frames add no call to a nested "
            "parse, `_parse_file` (`_detached_naming_frames`), `JsonSchemaParser._record_schema_location` "
            "(`_is_definition_location`), `JsonSchemaParser._get_rw_model_variant_reference`, `parse_enum` "
            "(`_claiming`); `parser/_object_maps.py:get_typed_additional_properties_field`, "
            "`parser/_union_tags.py:parse_combined_schema_items`, `JsonSchemaParser.parse_all_of`, "
            "`reference.py:ModelResolver` (`add`, `_add_ref_at`); `tests/checks/model_graph.py:check_model_owners` "
            "reads the records"
        ),
        sites=(
            Home("parser/base.py", "Parser._start_naming_frames"),
            Home("parser/base.py", "Parser._records_naming_claims"),
            Home("parser/jsonschema.py", "JsonSchemaParser._model_frame"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_file"),
            Home("parser/jsonschema.py", "JsonSchemaParser._detached_naming_frames"),
            Home("parser/jsonschema.py", "JsonSchemaParser._record_schema_location"),
            Home("parser/jsonschema.py", "JsonSchemaParser._is_definition_location"),
            Home("parser/jsonschema.py", "JsonSchemaParser._get_rw_model_variant_reference"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_enum"),
            Home("parser/jsonschema.py", "JsonSchemaParser._claiming"),
            Home("parser/_object_maps.py", "get_typed_additional_properties_field"),
            Home("parser/_union_tags.py", "parse_combined_schema_items"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_all_of"),
            Home("reference.py", "ModelResolver"),
            Home("reference.py", "ModelResolver.add"),
            Home("reference.py", "ModelResolver._add_ref_at"),
        ),
    ),
    DecisionHome(
        decision=(
            "which name a model takes once every model has claimed one, and the name L3 numbers a class its module "
            "holds from and gives back where the module leaves it free: the plan of the names after the last claim, by "
            "model key (`PlannedName`: module, class and base), made under the naming rules (`NamingRules`) from the "
            "claims of the models (`NameClaim`: role, owner, the model a name is composed from, document rank and "
            "order, the name L1 gave); with no rule on (`legacy`) it plans no name, so every model keeps the name the "
            "L1 resolver allocated first come, first served (`legacy-first-come`, "
            "`legacy-external-only-primary-first`, `legacy-closest-registered-parent`, "
            "`legacy-fixed-definition-patterns`, `legacy-allof-members-under-holder`) and `settle_yielding_names` "
            "settled, and L3 restores the name it asked for (`legacy-module-restore`); under `--naming-strategy "
            "definitions-first` the first definition of each family of one asked name takes it, a model that held it "
            "takes the name the definition gave up or the first free one, a model whose name is composed from a "
            "renamed one is named again from it, and every other name stays reserved; under `--naming-strategy "
            "enclosing-model` (L1 names as `full-path` does) every model is named again, owners first and definitions "
            "before the others: a model built for another one takes the owner's final class joined once with its own "
            "part (the name it asked for, the part its tag gives, its position as a branch named as the union, or the "
            "word of a composed role), a definition, a document root and an operation model their own names; under "
            "`--name-allof-union-members-after-branches` the classes an allOf builds for the oneOf or anyOf it holds "
            "take the holder's name and the branch's tag (under `--infer-union-variant-names` or "
            "`--use-tag-as-union-variant-name`), the class of a `$ref` branch or the member's position, a branch-only "
            "base the member's name and `Base`, the shared part the holder's name and `Base`; a model prefixed with "
            "its owner's name under `full-path` or `parent-prefixed` is named again from the owner's new name; the "
            "options read while models are built see the planned names in a parse again"
        ),
        homes=(
            Home("parser/_naming.py", "plan_names"),
            Home("parser/_naming.py", "NamingPlan"),
            Home("parser/_naming.py", "NamingPlan.base_of"),
            Home("parser/_naming.py", "PlannedName"),
            Home("parser/_naming.py", "NamingRules"),
            Home("parser/_naming.py", "NameClaim"),
            Home("parser/_naming.py", "Namer"),
        ),
        core=(
            "`parser/_naming.py`: `plan_names`, `NamingPlan` (`base_of`), `PlannedName`, `NamingRules`, `NameClaim`, "
            "`Namer`"
        ),
        adapters=(
            "`parser/_passes/naming.py:name_models` (the claims, the resolver's names as a `Namer`, the renames of "
            "scheme `plan` and the names a parse again assigns, `planned_names_to_assign`); `parser/base.py`: "
            "`_naming_rules_of` (a resolver class outside the package that overrides how models are named keeps them, "
            "with a warning), `_ModelShapeSource.own_class_name` (the dedup of one class name renders a model under "
            "its base); `reference.py:ModelResolver` (`add`, `_add_ref_at` take `_assigned_names`); "
            "`_generation.py:_parse_generation` (the parse again); `parser/_passes/structure.py`: "
            "`delete_duplicate_models` (groups the models of a module by their base), "
            "`replace_duplicate_name_in_module` (gives the base back); the L1 allocation stays in "
            "`reference.py:ModelResolver` (`add`, `_add_ref_at`, `get_class_name`, `_get_unique_name`, "
            "`_rename_external_ref_with_same_name`, `_find_parent_reference`, `_is_primary_definition`) and "
            "`parser/jsonschema.py:JsonSchemaParser._parse_all_of_union_model`; `tests/checks/naming.py` replays the "
            "resolver's naming calls of a parse and requires the names it ends with"
        ),
        sites=(
            Home("parser/_passes/naming.py", "name_models"),
            Home("parser/base.py", "_naming_rules_of"),
            Home("parser/base.py", "_ModelShapeSource.own_class_name"),
            Home("reference.py", "ModelResolver"),
            Home("reference.py", "ModelResolver.add"),
            Home("reference.py", "ModelResolver._add_ref_at"),
            Home("_generation.py", "_parse_generation"),
            Home("parser/_passes/structure.py", "delete_duplicate_models"),
            Home("parser/_passes/structure.py", "replace_duplicate_name_in_module"),
            Home("reference.py", "ModelResolver.get_class_name"),
            Home("reference.py", "ModelResolver._get_unique_name"),
            Home("reference.py", "ModelResolver._rename_external_ref_with_same_name"),
            Home("reference.py", "ModelResolver._find_parent_reference"),
            Home("reference.py", "ModelResolver._is_primary_definition"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_all_of_union_model"),
        ),
    ),
    DecisionHome(
        decision=(
            "the definition name each named type of a converted schema takes (Avro, XML Schema): in the order of their "
            "local names, a local name one type declares is its name (row `lone`: `titled` for Avro, `as-written` for "
            "XML Schema), else the namespace's name (`_namespace_name`) when the namespaces of the local name differ, "
            "the titled local name and the titled kind when one qualified name has several kinds (an XML Schema type "
            "and element); each takes the first free name (`joined-from-2`); the caller's rows are its titler "
            "(`first_upper`, the XML Schema titler) and the order of the types of one local name (Avro by full name, "
            "XML Schema by namespace, absent first, then kind)"
        ),
        homes=(Home("parser/_convert_common.py", "_name_definitions"),),
        core="`parser/_convert_common.py:_name_definitions`",
        adapters=(
            "every converter calls the core with its rows: "
            "`parser/avro.py:_AvroSchemaConverter._prepare_definition_names`, "
            "`parser/xmlschema.py:_XMLSchemaConverter._prepare_definition_names`"
        ),
        sites=(
            Home("parser/avro.py", "_AvroSchemaConverter._prepare_definition_names"),
            Home("parser/xmlschema.py", "_XMLSchemaConverter._prepare_definition_names"),
        ),
    ),
    DecisionHome(
        decision=(
            "how generated code builds a date, time or datetime, and an XML Schema timedelta, at run time: an "
            "expression on the `datetime` module imported as `datetime_module`, and for the ISO text of a date, time "
            "or datetime its `fromisoformat` call"
        ),
        homes=(
            Home("imports.py", "IMPORT_DATETIME_MODULE"),
            Home("python_literal.py", "_datetime_expression"),
            Home("python_literal.py", "_isoformat_expression"),
        ),
        core=(
            "`imports.py:IMPORT_DATETIME_MODULE`; `python_literal.py`: `_datetime_expression`, `_isoformat_expression`"
        ),
        adapters=(
            "Avro's date, time and datetime logical defaults (`parser/avro.py:_logical_default_expression`; its "
            "timedelta defaults import `timedelta` directly), XML Schema's temporal defaults "
            "(`parser/_xmlschema_literals.py`)"
        ),
        sites=(
            Home("parser/avro.py", "_logical_default_expression"),
            Home("parser/_xmlschema_literals.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "how generated code builds a value from its text at run time: the call of the value's constructor on the "
            "string literal of the text, an expression whose value (`str()`) stays the text"
        ),
        homes=(Home("python_literal.py", "_text_constructor_expression"),),
        core="`python_literal.py:_text_constructor_expression`",
        adapters=(
            "Decimal defaults (`parser/_passes/render_prep.py:deserialize_decimal_default`) and constraints "
            "(`parser/_passes/_shared.py:normalize_default_value_constraints`), compiled patterns "
            "(`model/pydantic_v2/base_model.py:_compiled_python_pattern`); Avro's `Decimal` defaults keep the call as "
            "their value (`parser/avro.py:_logical_default_expression`)"
        ),
        sites=(
            Home("parser/_passes/render_prep.py", "deserialize_decimal_default"),
            Home("parser/_passes/_shared.py", "normalize_default_value_constraints"),
            Home("model/pydantic_v2/base_model.py", "_compiled_python_pattern"),
            Home("parser/avro.py", "_logical_default_expression"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether a parsed non-finite float is held source-safe (written `float('inf')`, `float('nan')`) or as a "
            "plain float: each one of a parsed JSON value, and one of a converted schema exactly when the converter's "
            "caller writes Python source (row `source_safe_non_finite`: the parsers and AsyncAPI payloads that convert "
            "Protocol Buffers or XML Schema, not the `convert_*_schema_data` functions)"
        ),
        homes=(
            Home("python_literal.py", "_converted_float"),
            Home("python_literal.py", "_safe_non_finite_value"),
        ),
        core="`python_literal.py`: `_converted_float`, `_safe_non_finite_value`",
        adapters=(
            "the converters' float literals, each with its grammar's non-finite forms: "
            "`parser/protobuf.py:_ProtobufDescriptorConverter._parse_default`, "
            "`parser/_xmlschema_literals.py:_safe_float`"
        ),
        sites=(
            Home("parser/protobuf.py", "_ProtobufDescriptorConverter._parse_default"),
            Home("parser/_xmlschema_literals.py", "_safe_float"),
        ),
    ),
    DecisionHome(
        decision=(
            "the path that the output writes a module's file at: each `-` of a name as `_`, then without "
            "`--treat-dot-as-module` the dots of a name but the one before its extension as `_` (the parser's result "
            "keys; a model's module path has the dots of its directories and its file's name as `_` already, and a "
            "directory Python cannot import by its name once its `-` is `_` (`my dir`, `1.0`; `café-models` can) is "
            "sanitized as a file's name is, `model/base.py:get_module_path`), with it each dotted name split into "
            "packages, a file name keeping its extension"
        ),
        homes=(
            Home("_module_paths.py", "written_name"),
            Home("_module_paths.py", "written_names"),
            Home("_module_paths.py", "flat_name"),
            Home("_module_paths.py", "normalized_path"),
            Home("_module_paths.py", "package_path"),
            Home("_module_paths.py", "expanded_path"),
            Home("_module_paths.py", "written_path"),
        ),
        core=(
            "`_module_paths.py`: `written_name`, `written_names`, `flat_name`, `normalized_path`, `package_path`, "
            "`expanded_path`, `written_path`"
        ),
        adapters=(
            "every caller calls the core: the module files and the result keys of the parser (`parser/base.py`, "
            "`parser/_passes/imports.py:render_modules`), the directories of a model's module path "
            "(`model/base.py:_directory_module_name`), the stdout repair (`parser/_stdout_repair.py`), the imported "
            "module names (`parser/_passes/_import_binding.py:reference_import_target`), the `--all-exports-scope` "
            "exports (`parser/_init_exports.py`), the written submodules of a package (`parser/_passes/imports.py`), "
            "the packages of the import cycles (`parser/_passes/_import_edges.py`) and the targets' result keys "
            "(`_target_binding.py`); left out: option and config keys and the alias generators"
        ),
        sites=(
            Home("parser/_passes/imports.py", "render_modules"),
            Home("model/base.py", "_directory_module_name"),
            Home("parser/_passes/_import_binding.py", "reference_import_target"),
            Home("parser/base.py"),
            Home("parser/_stdout_repair.py"),
            Home("parser/_init_exports.py"),
            Home("parser/_passes/_import_edges.py"),
            Home("_target_binding.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "which module a written Python file holds: the file's path without its `.py` and without a package's "
            "`__init__` (a module path without a file name reads the same), below the package the reader names, and "
            "whether a file is a package's `__init__.py`"
        ),
        homes=(
            Home("_module_paths.py", "module_parts"),
            Home("_module_paths.py", "dotted_module"),
            Home("_module_paths.py", "is_package_init"),
        ),
        core="`_module_paths.py`: `module_parts`, `dotted_module`, `is_package_init`",
        adapters=(
            "every caller calls the core: the parser's module names (`parser/base.py`, `parser/_passes/plans.py`, "
            "`parser/_passes/imports.py`), the `--all-exports-scope` exports (`parser/_init_exports.py`), the written "
            "submodules of a package (`parser/_passes/imports.py:_package_submodule_names`), the targets' model "
            "imports (`_openapi_codec_plan.py:artifact_module`), the dynamic models (`dynamic.py`), the import of the "
            "generic base class (`parser/_passes/_import_binding.py:generic_base_class_import`), and the `__init__.py` "
            "tests of the target CLI, the API generation and the source filter; left out: the files on disk beside a "
            "written module (`_shadowed_modules.py`)"
        ),
        sites=(
            Home("parser/_passes/imports.py", "_package_submodule_names"),
            Home("_openapi_codec_plan.py", "artifact_module"),
            Home("parser/_passes/_import_binding.py", "generic_base_class_import"),
            Home("parser/base.py"),
            Home("parser/_passes/plans.py"),
            Home("parser/_init_exports.py"),
            Home("dynamic.py"),
            Home("__init__.py"),
            Home("_shadowed_modules.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the dotted name of a name in a module: the module's dotted name, a dot and the name, or the name alone "
            "without a module; a model is named across modules by it (its class name in the root module), and an "
            "import names the path of its name (`import m` names `m`; `Import.from_full_path` splits a path back)"
        ),
        homes=(
            Home("_module_paths.py", "dotted_name"),
            Home("imports.py", "_import_path"),
        ),
        core="`_module_paths.py`: `dotted_name`; `imports.py:_import_path`",
        adapters=(
            "every caller calls the core: the models a data type names (`types.py:DataType.full_name`, "
            "`parser/_passes/_shared.py:_get_data_type_target_full_name`), the enum classes of enum member literals "
            "(`parser/_passes/cross_module.py`), the inner models collapsed roots name (`parser/_passes/modules.py`), "
            "the released names of an internal module (`parser/_passes/imports.py`), the nested models of a dotted "
            "class name (`parser/jsonschema.py:JsonSchemaParser.parse_object_fields`), the paths of imported types "
            "(`types.py`, `parser/_value_keywords.py`, `parser/_type_overrides.py`, `_target_binding.py`, "
            "`_client/model_facts.py`), runtime symbols (`_python_type_annotation.py`) and the server packages beside "
            "a server package (`_fastapi/packages.py`); left out: Avro full names"
        ),
        sites=(
            Home("types.py", "DataType.full_name"),
            Home("parser/_passes/_shared.py", "_get_data_type_target_full_name"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_object_fields"),
            Home("parser/_passes/cross_module.py"),
            Home("parser/_passes/modules.py"),
            Home("parser/_passes/imports.py"),
            Home("parser/_value_keywords.py"),
            Home("parser/_type_overrides.py"),
            Home("_target_binding.py"),
            Home("_client/model_facts.py"),
            Home("_python_type_annotation.py"),
            Home("_fastapi/packages.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "which expressions a node of a Python type annotation (`PythonTypeExpr`) holds, in written order, and the "
            "node rebuilt around the ones a rewrite changed, sharing every unchanged subtree: all of them (row `all`: "
            "the base and the arguments of a subscript, the items of a union, a parameter list or a tuple, the model "
            "of a field path, the owner of a member, the value of a starred expression) or its values (row `values`: "
            "the arguments of a subscript and the items); and whether a subscript is a `Literal`, by its base's last "
            "name, a base that is a subscript itself naming none"
        ),
        homes=(
            Home("_python_type_annotation.py", "_children"),
            Home("_python_type_annotation.py", "_with_children"),
            Home("_python_type_annotation.py", "_is_literal_base"),
            Home("_python_type_annotation.py", "is_literal_python_type_expr"),
        ),
        core=(
            "`_python_type_annotation.py`: `_children`, `_with_children`, `_is_literal_base`, "
            "`is_literal_python_type_expr`"
        ),
        adapters=(
            "the string constants the codec admits only inside a `Literal` "
            "(`_python_type_annotation_codec.py:_python_type_expr_from_ast`); `_python_type_annotation.py`: "
            "`_iter_names` (row `all`, the names of `iter_python_type_expr_names`, "
            "`iter_python_type_expr_qualified_names` and `iter_python_type_expr_head_names`), "
            "`rewrite_python_type_expr`, `write_refused_literal_values`, `bytes_and_str_literal` and "
            "`literal_values_as_text` (row `all`), `read_literal_enum_members` and `iter_literal_members` (row "
            "`values`); `types.py:DataTypeManager._literal_value_paths`; left out: `render_python_type_expr`, which "
            "spells each kind; the decisions that read some kinds only (`python_type_expr_arguments`, "
            "`model/_decisions/nullability.py:written_annotation_null`, "
            "`model/_decisions/hashability.py:_python_type_hashes`); a `Literal` told by its import "
            "(`written_annotation_null`) or by its path (`types.py:DataTypeManager._annotation_constraint_keywords`)"
        ),
        sites=(
            Home("_python_type_annotation_codec.py", "_python_type_expr_from_ast"),
            Home("_python_type_annotation.py", "_iter_names"),
            Home("_python_type_annotation.py", "iter_python_type_expr_names"),
            Home("_python_type_annotation.py", "iter_python_type_expr_qualified_names"),
            Home("_python_type_annotation.py", "iter_python_type_expr_head_names"),
            Home("_python_type_annotation.py", "rewrite_python_type_expr"),
            Home("_python_type_annotation.py", "write_refused_literal_values"),
            Home("_python_type_annotation.py", "bytes_and_str_literal"),
            Home("_python_type_annotation.py", "literal_values_as_text"),
            Home("_python_type_annotation.py", "read_literal_enum_members"),
            Home("_python_type_annotation.py", "iter_literal_members"),
            Home("types.py", "DataTypeManager._literal_value_paths"),
            Home("_python_type_annotation.py", "render_python_type_expr"),
            Home("_python_type_annotation.py", "python_type_expr_arguments"),
            Home("model/_decisions/nullability.py", "written_annotation_null"),
            Home("model/_decisions/hashability.py", "_python_type_hashes"),
            Home("types.py", "DataTypeManager._annotation_constraint_keywords"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether a rewrite changed no part of a value, so that it keeps the value itself, shared with every "
            "holder, and builds a new one only after a change: each rewritten part is its original part at its "
            "position, the same object"
        ),
        homes=(Home("_copy_on_change.py", "unchanged"),),
        core="`_copy_on_change.py:unchanged`",
        adapters=(
            "the node of a Python type annotation rebuilt around its rewritten children "
            "(`_python_type_annotation.py:_with_children`), the containers that hold parser-owned runtime expressions, "
            "a mapping by its keys and values in order (`python_literal.py`: `rewrite_runtime_expressions`, "
            "`_rewrite_runtime_mapping`), the lists and mappings of a parsed JSON value that hold a non-finite float "
            "(`python_literal.py:_safe_non_finite_value`), the imports a module writes "
            "(`parser/_imports_plan.py:ModuleImportLedger.rendered`, which returns the live imports when no entry "
            "changed) and the nested values of an inherited schema "
            "(`parser/_inherited_schemas.py:normalize_inherited_nested_schema_value`)"
        ),
        sites=(
            Home("_python_type_annotation.py", "_with_children"),
            Home("python_literal.py", "rewrite_runtime_expressions"),
            Home("python_literal.py", "_rewrite_runtime_mapping"),
            Home("python_literal.py", "_safe_non_finite_value"),
            Home("parser/_imports_plan.py", "ModuleImportLedger.rendered"),
            Home("parser/_inherited_schemas.py", "normalize_inherited_nested_schema_value"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether a module lies below a package (the package's path starts its own and is shorter, so Python runs "
            "the package's `__init__` first) or within it (that package or below it); a dotted name lies below or "
            "within another by their parts"
        ),
        homes=(
            Home("_module_paths.py", "is_below"),
            Home("_module_paths.py", "is_within"),
            Home("_module_paths.py", "is_below_name"),
            Home("_module_paths.py", "is_within_name"),
        ),
        core="`_module_paths.py`: `is_below`, `is_within`, `is_below_name`, `is_within_name`",
        adapters=(
            "every caller calls the core: the ancestor package imports "
            "(`_module_paths.py:is_ancestor_package_reference`), the forwarders (`parser/base.py`), the import cycles "
            "below a package (`parser/_passes/_import_edges.py`), the stdout repair (`parser/_stdout_repair.py`), the "
            "modules a package's `__init__.py` exports (`parser/_init_exports.py:collect_exports_for_init`) and the "
            "submodules a class of a package hides (`parser/_passes/imports.py:submodules_hidden_by_packages`); by "
            "dotted name: the loaded modules of a watched formatter package and the children its refresh loads from "
            "source (`format.py`: `_local_package_modules`, `_package_modules`, `_WatchSourceFinder`), the private "
            "inflect package a failed import removes "
            "(`reference.py:_import_inflect_without_typeguard_instrumentation`), the optional libraries a target's "
            "imported names need (`_target_render.py:model_dependencies`) and the client helper names that collide "
            "(`_client/protocols.py:_collides`); left out: a schema path below a definitions container "
            "(`parser/jsonschema.py:JsonSchemaParser._is_named_schema_definition_path`)"
        ),
        sites=(
            Home("_module_paths.py", "is_ancestor_package_reference"),
            Home("parser/_init_exports.py", "collect_exports_for_init"),
            Home("parser/_passes/imports.py", "submodules_hidden_by_packages"),
            Home("format.py", "_local_package_modules"),
            Home("format.py", "_package_modules"),
            Home("format.py", "_WatchSourceFinder"),
            Home("reference.py", "_import_inflect_without_typeguard_instrumentation"),
            Home("_target_render.py", "model_dependencies"),
            Home("_client/protocols.py", "_collides"),
            Home("parser/jsonschema.py", "JsonSchemaParser._is_named_schema_definition_path"),
            Home("parser/base.py"),
            Home("parser/_passes/_import_edges.py"),
            Home("parser/_stdout_repair.py"),
            Home("__init__.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "which modules a written module imports when Python runs it, read from its final import plan: for each "
            "relative import block, the module its text names from the module's package (a module that writes a "
            "package's `__init__.py`, by the path it is written at, names it from that package) and the dotted name of "
            "each name it imports, which is the submodule of that name where the module has one"
        ),
        homes=(
            Home("parser/_passes/_import_edges.py", "relative_import_targets"),
            Home("parser/_passes/_import_edges.py", "module_import_edges"),
            Home("parser/_passes/_import_edges.py", "_written_package"),
        ),
        core="`parser/_passes/_import_edges.py`: `relative_import_targets`, `module_import_edges`, `_written_package`",
        adapters=(
            "every caller calls the core: the modules the written modules load "
            "(`parser/_passes/imports.py:_loaded_modules`, for the imports of a package's `__init__.py` that a loaded "
            "submodule hides), the refusal of a block written with a part Python cannot import by "
            "(`parser/_passes/imports.py:reject_unimportable_imports`), the refusal of a class named like a module "
            "below its package, unless the package loads the module first "
            "(`parser/_passes/imports.py:reject_class_named_like_submodule`, which adds the imports of the forwarders) "
            "and the packages whose `__init__.py` a module writes (the aliases of `parser/_passes/imports.py` for the "
            "names a package's submodules hide)"
        ),
        sites=(
            Home("parser/_passes/imports.py", "_loaded_modules"),
            Home("parser/_passes/imports.py", "reject_unimportable_imports"),
            Home("parser/_passes/imports.py", "reject_class_named_like_submodule"),
            Home("__init__.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "which modules import each other before the imports are planned, and which of their import cycles fail "
            "when one of their modules is imported first: a module imports the modules of the models that the field "
            "and base types of its models reference and that their schema validator rules name; once the modules of "
            "each cycle merge into an `_internal` module, a cycle through it fails unless it is a package and modules "
            "below it, which runs first (the cycles left without that package are checked again); importing a module "
            "runs the `__init__` module of each package above it first, and with `--all-exports-scope` the `__init__` "
            "module of a package imports the modules its exports reach"
        ),
        homes=(
            Home("parser/_passes/_import_edges.py", "module_dependency_graph"),
            Home("parser/_passes/_import_edges.py", "_failing_cycles"),
            Home("parser/_passes/_import_edges.py", "_failing_import_cycles"),
            Home("parser/_init_exports.py", "exported_modules"),
        ),
        core=(
            "`parser/_passes/_import_edges.py`: `module_dependency_graph`, `_failing_cycles`, `_failing_import_cycles` "
            "(`parser/_init_exports.py:exported_modules`)"
        ),
        adapters=(
            "`parser/_passes/structure.py:_module_merges` (behind `resolve_circular_imports`), which leaves the "
            "stand-ins out, names the `_internal` module of each merge and joins each failing cycle to its merges; the "
            "imports that passes after the merges add (the module of the generic base class, "
            "`parser/_passes/cross_module.py:apply_generic_base_class`, which picks it by "
            "`parser/_passes/_import_edges.py:generic_base_class_module`, and the models reuse makes canonical, "
            "`parser/_passes/modules.py`), which no merge reads, are checked on the written modules instead (next row)"
        ),
        sites=(
            Home("parser/_passes/structure.py", "_module_merges"),
            Home("parser/_passes/structure.py", "resolve_circular_imports"),
            Home("parser/_passes/cross_module.py", "apply_generic_base_class"),
            Home("parser/_passes/_import_edges.py", "generic_base_class_module"),
            Home("parser/_passes/modules.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether a written module fails to import, and what a parse again lays out otherwise: importing a module "
            "runs the packages above it first, then its relative import blocks in the order isort, Ruff and the "
            "builtin formatter write them (the furthest package first), each importing the module it names and then "
            "the submodule of each name the module has, or else reading the name, which a module that still runs binds "
            "only if one of its import lines that ran bound it; then the names its definitions read of the modules it "
            "imports (`pets.Pet`); a read of a name a running module has not bound fails; where the failure runs an "
            "import of the generic base class, the class moves once to the first module that takes it and reaches no "
            "other module that takes or imports it, else the modules of the import cycle that holds it merge"
        ),
        homes=(
            Home("parser/_passes/_import_edges.py", "written_import_blocks"),
            Home("parser/_passes/_import_edges.py", "import_plan"),
            Home("parser/_passes/_import_edges.py", "runs_into_import_cycle"),
            Home("parser/_passes/_import_edges.py", "failing_import_reads"),
            Home("parser/_passes/_import_edges.py", "import_cycle_seeds"),
        ),
        core=(
            "`parser/_passes/_import_edges.py`: `written_import_blocks`, `import_plan`, `runs_into_import_cycle`, "
            "`failing_import_reads`, `import_cycle_seeds`"
        ),
        adapters=(
            "`parser/_passes/imports.py:check_import_reads` (L7, after the last block is written), which reads the "
            "definition reads from the final facts (`_definition_reads`) and maps each written module back to the "
            "modules of the module structure; `parser/_passes/structure.py:_module_merges` joins the seeded merges"
        ),
        sites=(
            Home("parser/_passes/imports.py", "check_import_reads"),
            Home("parser/_passes/imports.py", "_definition_reads"),
            Home("parser/_passes/structure.py", "_module_merges"),
        ),
    ),
    DecisionHome(
        decision=(
            "which fields the statement that defines a model evaluates as the module runs: none of a `type` statement, "
            "which defers its value; the fields of the functional TypedDict's dict literal, the inherited ones "
            "included; the value of any other type alias and the root type of a Pydantic root model; and the "
            "annotations of a class where the module does not defer them (row `definition`); the passes that order the "
            "models of a module or check what it evaluates read less or more (rows "
            "`legacy-class-body-without-alias-values`, `legacy-keep-order-type-statement`, "
            "`legacy-evaluated-without-root-model`)"
        ),
        homes=(Home("parser/_passes/_shared.py", "definition_reads"),),
        core="`parser/_passes/_shared.py:definition_reads`",
        adapters=(
            "every caller calls the core with its row: the forward references a module quotes "
            "(`parser/_passes/render_prep.py:update_type_aliases`, row `definition`), the models a class body "
            "evaluates first (`parser/_passes/_shared.py:_sort_models_by_class_body_references` and the unresolved "
            "models of `parser/_passes/settle.py:sort_data_models`), `--keep-model-order` "
            "(`parser/_passes/modules.py:_keep_model_order_deps`) and the root models that name themselves "
            "(`parser/_passes/imports.py:reject_alias_cycles_without_schema`); not yet: the module merges "
            "(`parser/_passes/_import_edges.py:module_dependency_graph`), which read every reference"
        ),
        sites=(
            Home("parser/_passes/render_prep.py", "update_type_aliases"),
            Home("parser/_passes/_shared.py", "_sort_models_by_class_body_references"),
            Home("parser/_passes/settle.py", "sort_data_models"),
            Home("parser/_passes/modules.py", "_keep_model_order_deps"),
            Home("parser/_passes/imports.py", "reject_alias_cycles_without_schema"),
            Home("parser/_passes/_import_edges.py", "module_dependency_graph"),
        ),
    ),
    DecisionHome(
        decision=(
            "which names a package's `__init__.py` exports under `--all-exports-scope`, and of which modules: the "
            "names of a module's models that do not start with `_`, of each module below the package that the scope "
            "reaches (`children`: the modules directly below it, `recursive`: every module below it)"
        ),
        homes=(
            Home("parser/_init_exports.py", "exported_names"),
            Home("parser/_init_exports.py", "exported_modules"),
        ),
        core="`parser/_init_exports.py`: `exported_names`, `exported_modules`",
        adapters=(
            "every caller calls the core: the exports of an `__init__.py` "
            "(`parser/_init_exports.py:collect_exports_for_init`) and the names of its own models, which they must not "
            "take (`parser/base.py`), the modules the import graph of a package's `__init__.py` targets "
            "(`parser/_passes/_import_edges.py:module_import_edges`: every module the scope reaches, also one whose "
            "models are all private, for which the `__init__.py` writes no import; the modules the written modules "
            "load read them, `parser/_passes/imports.py:_loaded_modules`) and the import cycles of the module merges, "
            "through the modules a package re-exports (`parser/_passes/_import_edges.py:_failing_import_cycles`, "
            "`parser/_passes/structure.py:_module_merges`, which counts only the modules with a name to export)"
        ),
        sites=(
            Home("parser/_init_exports.py", "collect_exports_for_init"),
            Home("parser/_passes/_import_edges.py", "module_import_edges"),
            Home("parser/_passes/imports.py", "_loaded_modules"),
            Home("parser/_passes/_import_edges.py", "_failing_import_cycles"),
            Home("parser/_passes/structure.py", "_module_merges"),
            Home("parser/base.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether a model names a model of its module at its own or a later position, which the module defines only "
            "once the model is defined (a forward reference, or one to the model itself): by the position of the model "
            "the reference keys, or of the last model of its short name (row `legacy-forward-by-name`)"
        ),
        homes=(Home("parser/_passes/_shared.py", "_ModelPositions.names_at_or_after"),),
        core="`parser/_passes/_shared.py`: `_ModelPositions.names_at_or_after`",
        adapters=(
            "every caller calls the core: the quoted forward references, rule types and recursive containers "
            "(`parser/_passes/render_prep.py`), the forward enum member literals "
            "(`parser/_passes/_shared.py:_forward_enum_member_literals`), the forward references, update actions and "
            "forward RootModel aliases of a module (`parser/base.py`) and the named types a field hides "
            "(`parser/_passes/modules.py:hides_named_type`); left out: the fields a target before Pydantic 2.11 writes "
            "by assignment (`model/pydantic_v2/base_model.py:assigns_forward_reference_field`), a public hook that "
            "reads the positions by path (`_ModelPositions.paths`) and leaves a reference to the model itself out"
        ),
        sites=(
            Home("parser/_passes/_shared.py", "_forward_enum_member_literals"),
            Home("parser/_passes/modules.py", "hides_named_type"),
            Home("model/pydantic_v2/base_model.py", "assigns_forward_reference_field"),
            Home("parser/_passes/render_prep.py"),
            Home("parser/base.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the package of the user's implementations that a server package imports: `services`, beside the server "
            "package (in its parent package, at the top level for a top-level server package), and the dotted name of "
            "another package beside it; the docstrings of the server package's and the `services` package's "
            "initializers, which the renderer writes and the API generation reads back from an earlier run (the server "
            "package a directory holds, also from the docstring of earlier versions without its name, and the server "
            "package that imports the implementations, rewritten when another server package takes them over)"
        ),
        homes=(
            Home("_fastapi/packages.py", "SERVICES"),
            Home("_fastapi/packages.py", "services_package"),
            Home("_fastapi/packages.py", "beside"),
            Home("_fastapi/packages.py", "server_docstring"),
            Home("_fastapi/packages.py", "generated_server"),
            Home("_fastapi/packages.py", "implementations_docstring"),
            Home("_fastapi/packages.py", "implementations_owner"),
            Home("_fastapi/packages.py", "adopted"),
        ),
        core=(
            "`_fastapi/packages.py`: `SERVICES`, `services_package`, `beside`, `server_docstring`, `generated_server`, "
            "`implementations_docstring`, `implementations_owner`, `adopted`"
        ),
        adapters=(
            "every caller calls the core: the stubs, the package initializers, the README and the next steps of the "
            "server renderer (`_fastapi/render.py`) and the services checks of the API generation "
            "(`_api_generation.py`); left out: the module `services.py` inside the server package, which holds the "
            "Protocols"
        ),
        sites=(
            Home("_fastapi/render.py"),
            Home("_api_generation.py"),
            Home("_fastapi/_compiled_templates/services.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a JSON Pointer token is escaped, joined, split and decoded, and how the fragment of a `$ref` is read: "
            "split at each `/` first, then each token percent-decoded and unescaped, so `a%2Fb` is the one token `a/b` "
            "and a leading `%2F` opens the pointer (`pointer_opening`), which the parser reads as the pointer `/` "
            "opens (`#%2Fdefinitions/A` is `#/definitions/A`)"
        ),
        homes=(
            Home("_json_pointer.py", "escape_token"),
            Home("_json_pointer.py", "unescape_token"),
            Home("_json_pointer.py", "join_pointer"),
            Home("_json_pointer.py", "fragment_pointer"),
            Home("_json_pointer.py", "pointer_tokens"),
            Home("_json_pointer.py", "fragment_tokens"),
            Home("_json_pointer.py", "reference_tokens"),
            Home("_json_pointer.py", "decode_token"),
        ),
        core=(
            "`_json_pointer.py`: `escape_token`, `unescape_token`, `join_pointer`, `fragment_pointer`, "
            "`pointer_tokens`, `fragment_tokens`, `reference_tokens`, `decode_token`"
        ),
        adapters=(
            "the readers of a `$ref` fragment: the parser's spelling of a reference keyword "
            "(`parser/_schema_graph.py:pointer_ref_spelling`, read by `canonical_ref_spelling`, the keywords the "
            "schema graph indexes and `parser/_schema_documents.py:rewrite_schema_resource_refs`), "
            "`parser/_api_reference.py:pointer_tokens` (strict), "
            "`parser/_schema_reading.py:unescape_json_pointer_segment`, "
            "`parser/openapi_scope.py:ApiOpenAPIParser._schema_reference_location`, "
            "`_target_binding.py:_pointer_tokens`, `_fastapi/source_document.py:SourceDocument.resolve` and "
            "`_recommend/_signals.py:_local_tokens`; left out: a text that is already decoded (`fragment_tokens`: "
            "`parser/base.py:_source_path_from_reference_path`, `_recommend/_metrics.py:_identity_steps`)"
        ),
        sites=(
            Home("parser/_schema_graph.py", "pointer_ref_spelling"),
            Home("parser/_schema_graph.py", "canonical_ref_spelling"),
            Home("parser/_schema_documents.py", "rewrite_schema_resource_refs"),
            Home("parser/_api_reference.py", "pointer_tokens"),
            Home("parser/_schema_reading.py", "unescape_json_pointer_segment"),
            Home("parser/openapi_scope.py", "ApiOpenAPIParser._schema_reference_location"),
            Home("_target_binding.py", "_pointer_tokens"),
            Home("_fastapi/source_document.py", "SourceDocument.resolve"),
            Home("_recommend/_signals.py", "_local_tokens"),
            Home("parser/base.py", "_source_path_from_reference_path"),
            Home("_recommend/_metrics.py", "_identity_steps"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether the fragment of a reference locates by JSON Pointer (an empty one the whole document) or is a "
            "plain name that names an anchor, and how long the opening of its pointer is: `/` or its octet `%2F` in a "
            "URI fragment, `/` in a decoded one"
        ),
        homes=(
            Home("_json_pointer.py", "pointer_opening"),
            Home("_json_pointer.py", "is_pointer_fragment"),
        ),
        core="`_json_pointer.py`: `pointer_opening`, `is_pointer_fragment`",
        adapters=(
            "`_json_pointer.py`: `reference_tokens`, `fragment_tokens` (row `decoded`); "
            "`parser/_api_reference.py:check_pointer_interpretation`; `parser/_schema_graph.py:pointer_ref_spelling` "
            "(which writes an opening `%2F` of a reference keyword as `/`); `reference.py:ModelResolver.resolve_ref` "
            "(the anchor of a document and the `$id` of the current scope), "
            "`parser/_schema_reading.py:_is_plain_name_fragment` (read by `parser/_schema_documents.py` and "
            "`parser/_schema_refs.py`), `parser/_schema_documents.py:resolve_schema_resource_ref`, "
            "`_json_pointer.py:uri_entry`"
        ),
        sites=(
            Home("_json_pointer.py", "reference_tokens"),
            Home("_json_pointer.py", "fragment_tokens"),
            Home("parser/_api_reference.py", "check_pointer_interpretation"),
            Home("parser/_schema_graph.py", "pointer_ref_spelling"),
            Home("reference.py", "ModelResolver.resolve_ref"),
            Home("parser/_schema_reading.py", "_is_plain_name_fragment"),
            Home("parser/_schema_documents.py", "resolve_schema_resource_ref"),
            Home("_json_pointer.py", "uri_entry"),
            Home("parser/_schema_refs.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "which entry of a map of declared URIs an absolute URI names, and the pointer fragment below it: a "
            "declared URI (an identifier, an `$anchor` or a `$dynamicAnchor`) its own entry, any other URI whose "
            "fragment is a pointer that pointer below the entry of its document part"
        ),
        homes=(Home("_json_pointer.py", "uri_entry"),),
        core="`_json_pointer.py:uri_entry`",
        adapters=(
            "`parser/_schema_graph.py:ResourceRegistry.location`, `_fastapi/source_document.py:SourceDocument.resolve` "
            "(over the registry's URIs the source lease carries)"
        ),
        sites=(
            Home("parser/_schema_graph.py", "ResourceRegistry.location"),
            Home("_fastapi/source_document.py", "SourceDocument.resolve"),
        ),
    ),
    DecisionHome(
        decision=(
            "how generated Python spells a call, a subscript, a keyword argument, a tuple, list, set or dict display "
            "and an import statement from the texts of their items (the separator, `()` for a subscript without "
            "arguments, the comma a lone tuple item needs, `set()` for a set without items, the colon of a dict entry, "
            "the equals sign of a keyword its data names, and `import m`, `from m import a, b` and `a as b`), the "
            "escapes of the text of a single-quoted literal (the quote, the backslash, the control characters, the "
            "null byte and a lone surrogate, which a module cannot hold, and which Pydantic and a msgspec field name "
            "cannot hold either (`holds_lone_surrogate`); a raw literal only the quote and the control characters, and "
            "only for a text whose closing quote stays the first one a reader meets and that holds no null byte or "
            "lone surrogate, which a raw literal cannot escape; a pattern's caller also asks that its control "
            "characters keep their meaning as regex escapes), and the text of a docstring (the backslash, a triple "
            "quote and the null byte escaped, a lone surrogate replaced by U+FFFD, as no docstring that holds one "
            "compiles from Python 3.13; the one-line form, whose last double quote is escaped, and the block form) and "
            "of a comment (a lone surrogate replaced by U+FFFD, line endings as LF; an inline comment continues each "
            "line with `# `); each writer makes its item and value texts itself, a type renderer decides which types a "
            "subscript holds, and a writer of imports which names it imports, in which order and when one takes an "
            "alias"
        ),
        homes=(
            Home("_python_syntax.py", "call_text"),
            Home("_python_syntax.py", "subscript_text"),
            Home("_python_syntax.py", "keyword_argument"),
            Home("_python_syntax.py", "tuple_display"),
            Home("_python_syntax.py", "list_display"),
            Home("_python_syntax.py", "set_display"),
            Home("_python_syntax.py", "dict_display"),
            Home("_python_syntax.py", "module_import"),
            Home("_python_syntax.py", "from_import"),
            Home("_python_syntax.py", "aliased"),
            Home("_python_syntax.py", "SINGLE_QUOTED_ESCAPES"),
            Home("_python_syntax.py", "QUOTE_AND_CONTROL_ESCAPES"),
            Home("_python_syntax.py", "holds_lone_surrogate"),
            Home("_python_syntax.py", "escape_docstring"),
            Home("_python_syntax.py", "format_docstring"),
            Home("_python_syntax.py", "comment_safe"),
            Home("_python_syntax.py", "inline_comment_safe"),
            Home("model/base.py"),
        ),
        core=(
            "`_python_syntax.py`: `call_text`, `subscript_text`, `keyword_argument`, `tuple_display`, `list_display`, "
            "`set_display`, `dict_display`, `module_import`, `from_import`, `aliased`, `SINGLE_QUOTED_ESCAPES`, "
            "`RAW_SINGLE_QUOTED_ESCAPES`, `QUOTE_AND_CONTROL_ESCAPES`, `raw_single_quoted`, `holds_lone_surrogate`, "
            "`escape_docstring`, `format_docstring`, `comment_safe`, `inline_comment_safe` (`model/base.py` keeps "
            "their names, and the Jinja filters `escape_docstring` and `format_docstring`)"
        ),
        adapters="the docstring of a client codec (`_client/codec_render.py:_description`)",
        sites=(Home("_client/codec_render.py", "_description"),),
    ),
    DecisionHome(
        decision="the line endings of a text read as Python's universal newlines read a file: CRLF and a lone CR as LF",
        homes=(Home("_python_syntax.py", "normalize_newlines"),),
        core="`_python_syntax.py:normalize_newlines`",
        adapters=(
            "`_python_syntax.py:comment_safe`; `_generation.py`: `_is_comment_only_source`, `_prepare_directory_input` "
            "(the headers that mark a generated file); `_source.py:DirectoryInputFilter.iter_files`; "
            "`parser/base.py:Source.from_path`; `_output_comparison.py:_read_existing_output` and the generated files "
            "`--check` compares; `_api_generation.py:_normalized` (then one final newline); "
            "`_builtin_formatter.py:_split_python_lines`"
        ),
        sites=(
            Home("_python_syntax.py", "comment_safe"),
            Home("_generation.py", "_is_comment_only_source"),
            Home("_generation.py", "_prepare_directory_input"),
            Home("_source.py", "DirectoryInputFilter.iter_files"),
            Home("parser/base.py", "Source.from_path"),
            Home("_output_comparison.py", "_read_existing_output"),
            Home("_api_generation.py", "_normalized"),
            Home("_builtin_formatter.py", "_split_python_lines"),
        ),
    ),
    DecisionHome(
        decision=(
            "the generation timestamp a file header names when its run pins none: the current UTC time in whole "
            "seconds, as ISO 8601 text"
        ),
        homes=(Home("_generation_timestamp.py", "current_timestamp"),),
        core="`_generation_timestamp.py:current_timestamp`",
        adapters=(
            "the file header of a run without a pinned timestamp (`_generation.py:_build_file_header_parts`), a target "
            "run, which pins one before it generates its models (`_api_generation.py:plan_target`; each later target "
            "of a batch takes the first one's), and the two runs of `--diff-against`, which pin one for both "
            "(`__main__.py:_run_config`)"
        ),
        sites=(
            Home("_generation.py", "_build_file_header_parts"),
            Home("_api_generation.py", "plan_target"),
            Home("__main__.py", "_run_config"),
        ),
    ),
    DecisionHome(
        decision=(
            "which member of a loaded document a JSON Pointer token names, and which tokens are errors rather than "
            "misses: a list item by an RFC 6901 index only (never `-1`, `01` or a character of a string), and a value "
            "that is no container has no member; row `canonical` (locations the parser recorded and the target "
            "readers: a mapping member by the token, as the parser's `$ref` reads it, else by the integer YAML key the "
            "token is the index of; every token that names none is a miss) and `refusing` (the targets of the parser's "
            "`$ref`s: a mapping member by the token only, a missing member a `KeyError`, a list token that is no index "
            "an error)"
        ),
        homes=(
            Home("_document_reader.py", "member"),
            Home("_document_reader.py", "member_key"),
            Home("parser/_schema_documents.py", "rewrite_schema_resource_refs"),
            Home("_document_reader.py", "descend"),
        ),
        core=(
            "`_document_reader.py`: `member`, `member_key` (the key a list or a mapping holds the member of a token "
            "under, which `member` reads and the copy-on-write rewrite of "
            "`parser/_schema_documents.py:rewrite_schema_resource_refs` writes), `descend`"
        ),
        adapters=(
            "the walks that read through it: `parser/_schema_reading.py:_get_model_by_decoded_path` (the parser's: "
            "`{}` or the missing sentinel, also for a walk through a scalar) and `_split_json_pointer`; "
            "`_openapi_generation.py:SourceLease.exists`, the schema sites of "
            "`parser/openapi_scope.py:ApiOpenAPIParser._located_site`, the `borrow` reader of `_target_binding.py`, "
            "`_fastapi/source_document.py`, `_recommend/_signals.py:_resolve_local`"
        ),
        sites=(
            Home("parser/_schema_reading.py", "_get_model_by_decoded_path"),
            Home("parser/_schema_reading.py", "_split_json_pointer"),
            Home("_openapi_generation.py", "SourceLease.exists"),
            Home("parser/openapi_scope.py", "ApiOpenAPIParser._located_site"),
            Home("_target_binding.py", "_Documents.borrow"),
            Home("_recommend/_signals.py", "_resolve_local"),
            Home("_fastapi/source_document.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a plain YAML scalar reads that YAML 1.1 reads as a number and YAML 1.2 as text (`_` or `:` in it, "
            "such as `16:9` and `18_24`; not `0b101`, `-0x1F` or `017`, which ryaml reads as numbers): as the number "
            "PyYAML reads, counted for the generation run; as its text under `--read-yaml-1-1-numbers-as-text`; and, "
            "in a parse again of a run that the numbers made invalid, as its text where a string is required (a "
            "mapping key, the value of a keyword the document models type as strings: a scalar, a list's items, a "
            "map's values) and as its number anywhere else"
        ),
        homes=(
            Home("util.py", "_construct_yaml_1_1_number"),
            Home("util.py", "settle_yaml_numbers"),
            Home("util.py", "yaml_number_reading"),
            Home("util.py", "yaml_numbers_read_where_strings_required"),
            Home("parser/openapi.py", "yaml_string_keywords"),
        ),
        core=(
            "`util.py`: `_construct_yaml_1_1_number`, `settle_yaml_numbers`, the readings `yaml_number_reading` and "
            "`yaml_numbers_read_where_strings_required`; the keywords, `parser/openapi.py:yaml_string_keywords` (the "
            "field types of `JsonSchemaObject`, `Discriminator` and the OpenAPI objects)"
        ),
        adapters=(
            "`_source.py`: `_load_pyyaml` and the parsed-source cache (the reading in its key, the count in its "
            "value); `_generation.py`: `_run_generation_with_metadata` and `_collect_model_metadata` (the run's "
            "reading), "
            "`_parse_yaml_numbers_again` (the read-again, a first parse of the run through `_parse_first`, whose "
            "warnings show only where it succeeds) and `_parse_again` (the reading of each parse again); "
            "`parser/base.py:Parser.parse` (the option for a parser used alone)"
        ),
        sites=(
            Home("_source.py", "_load_pyyaml"),
            Home("_generation.py", "_run_generation_with_metadata"),
            Home("_generation.py", "_collect_model_metadata"),
            Home("_generation.py", "_parse_yaml_numbers_again"),
            Home("_generation.py", "_parse_first"),
            Home("_generation.py", "_parse_again"),
            Home("parser/base.py", "Parser.parse"),
        ),
    ),
    DecisionHome(
        decision=(
            "the parse path of the target a JSON Pointer names: the parse path of its document, then `#/` with the "
            "pointer's first token, then its other tokens (the path the resolver adds the target's model at)"
        ),
        homes=(Home("parser/jsonschema.py", "JsonSchemaParser._pointer_parse_path"),),
        core="`parser/jsonschema.py:JsonSchemaParser._pointer_parse_path`",
        adapters=(
            "`parser/jsonschema.py:JsonSchemaParser`: `_parse_pointer_target` (for `parse_json_pointer` and "
            "`_parse_base_stand_in`), `_parse_pointer_ref` (for `_parse_reserved_targets`) and `_parse_file` (the "
            "reference tokens of `_split_json_pointer`, as written), "
            "`parser/asyncapi.py:AsyncAPIParser._resolve_asyncapi_ref` (the decoded tokens); left out: the paths a "
            "parser builds for a container it names itself (`parser/openapi.py`: "
            "`OpenAPIParser._definition_entry_prefixes`, `_process_path_items`; `parser/asyncapi.py`: "
            "`_iter_converted_schema_schemas`, `_iter_asyncapi_schemas`) and the `#` path of "
            "`parser/openapi_scope.py:ApiOpenAPIParser.parse_json_pointer`"
        ),
        sites=(
            Home("parser/jsonschema.py", "JsonSchemaParser"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_pointer_target"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_json_pointer"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_base_stand_in"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_pointer_ref"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_reserved_targets"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_file"),
            Home("parser/asyncapi.py", "AsyncAPIParser._resolve_asyncapi_ref"),
            Home("parser/openapi.py", "OpenAPIParser._definition_entry_prefixes"),
            Home("parser/openapi.py", "OpenAPIParser._process_path_items"),
            Home("parser/asyncapi.py", "AsyncAPIParser._iter_converted_schema_schemas"),
            Home("parser/asyncapi.py", "AsyncAPIParser._iter_asyncapi_schemas"),
            Home("parser/openapi_scope.py", "ApiOpenAPIParser.parse_json_pointer"),
        ),
    ),
    DecisionHome(
        decision=(
            "what a loaded document value that is not a mapping reads as: an empty mapping, so a reader finds no "
            "members, keys or items in it"
        ),
        homes=(Home("_document_reader.py", "as_mapping"),),
        core="`_document_reader.py:as_mapping`",
        adapters=(
            "the readers of loaded documents: `_target_binding.py`, `_fastapi/source_document.py`, "
            "`_fastapi/plan.py:_member`, `_recommend/_signals.py` (the schema signals), "
            "`_recommend/_metrics.py:_component_names` (the trial vocabulary), "
            "`parser/_value_keywords.py:_validation_keywords`, `parser/asyncapi.py`: `_iter_mapping_items`, "
            "`AsyncAPIParser._iter_asyncapi_schemas` and `OpenAPIParser._collect_discriminator_document`, "
            "`model/msgspec.py:_user_struct_options` and `model/pydantic_v2/base_model.py:_config_dict_items` "
            "(template data) and `remote_lock.py:_read_entries`; "
            "`parser/_passes/modules.py:collapse_root_models_in_scope` keeps its own guard while routing it would move "
            "the line a warning of its file names"
        ),
        sites=(
            Home("_fastapi/plan.py", "_member"),
            Home("_recommend/_metrics.py", "_component_names"),
            Home("parser/_value_keywords.py", "_validation_keywords"),
            Home("parser/asyncapi.py", "_iter_mapping_items"),
            Home("parser/asyncapi.py", "AsyncAPIParser._iter_asyncapi_schemas"),
            Home("model/msgspec.py", "_user_struct_options"),
            Home("model/pydantic_v2/base_model.py", "_config_dict_items"),
            Home("remote_lock.py", "_read_entries"),
            Home("parser/_passes/modules.py", "collapse_root_models_in_scope"),
            Home("_target_binding.py"),
            Home("_fastapi/source_document.py"),
            Home("_recommend/_signals.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "a model's read (request) or write (response) variant (`--read-only-write-only-model-type`): its path, the "
            "base path followed by one special token naming the direction, which the parser adds the variant at and "
            "the target binding looks the variant up at, and the base path and direction a reader takes back from the "
            "last special token; the direction its class-name suffix (`Request`, `Response`) names; and the flag that "
            "leaves a property out of it (readOnly for a request variant, writeOnly for a response one)"
        ),
        homes=(
            Home("reference.py", "_rw_variant_path"),
            Home("reference.py", "_rw_variant_of"),
            Home("reference.py", "_rw_direction"),
            Home("reference.py", "_rw_variant_flag"),
        ),
        core="`reference.py`: `_rw_variant_path`, `_rw_variant_of`, `_rw_direction`, `_rw_variant_flag`",
        adapters=(
            "`parser/_rw_split.py:get_rw_model_variant_reference`, `_target_binding.py:_Binder._variant`, "
            "`_TargetRecording._create_variant_model` (the split's left-out fields by the variant's key); readers "
            "`reference.py:_source_path` (`_Binder.source`, `parser/_passes/_shared.py:_get_schema_path`), "
            "`parser/_rw_split.py:ref_schema_generates_variant`, `request_response_ref_schema_generates_variant`, "
            "`_target_binding.py:_Models.__init__`, `_Models.variant_direction`; the flag: "
            "`parser/_rw_split.py:_rw_facts_need_variant`, `_rw_variant_fields` (`create_request_response_models`), "
            "`_target_binding.py:_Models.variant_exclusions` (the name of what the split left out), "
            "`_client/fields.py:_Fields.model` (request), `_client/plan.py:Planner._received` (response)"
        ),
        sites=(
            Home("parser/_rw_split.py", "get_rw_model_variant_reference"),
            Home("_target_binding.py", "_Binder._variant"),
            Home("_target_binding.py", "_TargetRecording._create_variant_model"),
            Home("reference.py", "_source_path"),
            Home("_target_binding.py", "_Binder.source"),
            Home("parser/_passes/_shared.py", "_get_schema_path"),
            Home("parser/_rw_split.py", "ref_schema_generates_variant"),
            Home("parser/_rw_split.py", "request_response_ref_schema_generates_variant"),
            Home("_target_binding.py", "_Models.__init__"),
            Home("_target_binding.py", "_Models.variant_direction"),
            Home("parser/_rw_split.py", "_rw_facts_need_variant"),
            Home("parser/_rw_split.py", "_rw_variant_fields"),
            Home("parser/_rw_split.py", "create_request_response_models"),
            Home("_target_binding.py", "_Models.variant_exclusions"),
            Home("_client/fields.py", "_Fields.model"),
            Home("_client/plan.py", "Planner._received"),
        ),
    ),
    DecisionHome(
        decision=(
            "the token of a model the generator makes at a path (a special path, `parser.base.get_special_path`): how "
            "it is spelled and built, and whether a path, or a token of one, holds one (a model the generator made, or "
            "a place below one: no document holds a schema there)"
        ),
        homes=(
            Home("reference.py", "_special_path"),
            Home("reference.py", "_is_special_path"),
            Home("reference.py", "_special_token_pattern"),
        ),
        core="`reference.py`: `_special_path`, `_is_special_path`, `_special_token_pattern`",
        adapters=(
            "every caller calls the core: the raw-schema readers of `parser/_inherited_schemas.py`, "
            "`parser/_rw_split.py` and `parser/_all_of_values.py`, the inline variants of `parser/_tag_plan.py`, "
            "`parser/_api_reference.py:ApiModelResolver`, `reference.py:ModelResolver.resolve_ref`, "
            "`_target_binding.py:_SchemaUses.schema_uses`, and the identity steps of `_recommend/_metrics.py` "
            "(keywords of word characters only)"
        ),
        sites=(
            Home("parser/_api_reference.py", "ApiModelResolver"),
            Home("reference.py", "ModelResolver.resolve_ref"),
            Home("_target_binding.py", "_SchemaUses.schema_uses"),
            Home("parser/_inherited_schemas.py"),
            Home("parser/_rw_split.py"),
            Home("parser/_all_of_values.py"),
            Home("parser/_tag_plan.py"),
            Home("_recommend/_metrics.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the HTTP vocabulary the generator and both generated runtimes read: the status classes (`STATUSES`, "
            "`SUCCESS_STATUSES`, `REDIRECT_STATUSES`, `ERROR_STATUSES`), the named statuses (`SWITCHING_PROTOCOLS`, "
            "`OK`, `NO_CONTENT`, `NOT_MODIFIED`), the status key a status selects, its range key and the declared "
            "response it selects (`status_key`, `range_key`, `declaring`), the key under which an operation declares a "
            "response (`response_key`: `default`, a status of three ASCII digits from 100 to 599, or a range `1XX` to "
            "`5XX`; any other key, such as an extension `x-note`, an empty key, `0204` or a status with a non-ASCII "
            "digit, declares no response to the client, the server, the cache and polling helpers and the wire plan "
            "alike), the status an exact or range key names (`exact_status`, `range_start`), which responses carry no "
            "content (HEAD, 204, 205, 304 and 1XX: `bodyless`, and for a declared key `response_bodyless`, which reads "
            "the key `1XX` as the statuses it holds; the server plan gives its answer to the server render as "
            "`ResponseSpec.bodyless` and `PrimarySpec.bodyless`), a media type's essence and kind, media ranges, and "
            "the declared media type a received one dispatches to (`most_specific`: exact, its essence, `type/*`, "
            "`*/*`, the first without parameters within one, for a client's response, request body and multipart part "
            "as for a server's request body); the ASCII digit run a header writes for a count, a delay or a timestamp "
            "(`ascii_digits`: a page's total, an upload offset, `Retry-After`, a `Cache-Control` delta, a signature "
            "timestamp; each site keeps its sign, bound and conversion), the token and quoted-string of a header field "
            "(`TCHARS`, `TOKEN`, `QUOTED`: a media type's parameters, a Link's parameters, Cache-Control's "
            "directives), whether text is a token as a whole (`is_token`: a raw call's method, a header field name of "
            "a request option, a header or cookie parameter and a server result, a cookie name a cookie parameter "
            "writes, the header or cookie name of an apiKey security scheme (`_target_contract.py:api_key_place`), and "
            "a client setting that names a header or a subprotocol), the JSON media type (`JSON_MEDIA_TYPE`: the media "
            "type a server result without one, a socket message, a JSON form-data part and an OAuth token response "
            "take), which header fields a set of lowercase names leaves out, the others kept in order "
            "(`fields_without`: the Content-Type a client call relabels, the Content-Length of a compressed body, the "
            "framing HTTPX2 writes, the credentials a client strips on a redirect to another origin, a cache entry's "
            "replaced fields and its Content-Length, and the credentials the generator drops when a URL input's fetch "
            "is redirected to another origin), the quoted-string of a MIME header field as a multipart part's "
            "Content-Disposition is read (`LENIENT_QUOTED`), and the text of a parameter value, a quoted string "
            "without its quotes and escapes (`unquoted`: a media type's, a Link's and a Content-Disposition's "
            "parameters)"
        ),
        homes=(
            Home("_target_contract.py", "response_key"),
            Home("_target_contract.py", "exact_status"),
            Home("_target_contract.py", "range_start"),
            Home("_runtime/model_codecs/media.py"),
        ),
        core=(
            "`_runtime/model_codecs/media.py` (copied into every client and server package; the generator imports it, "
            "the runtime never imports the generator); `_target_contract.py`: `response_key`, `exact_status`, "
            "`range_start` (the declared keys only the generator reads)"
        ),
        adapters=(
            "`_target_binding.py:_Contracts.operation` (the responses an operation declares, by `response_key`; two "
            "keys of one response key, such as `2XX` and `2xx`, are an error: "
            "`_document_reader.py:document_statuses`), `_client/naming.py:token` (a setting value of any type), "
            "`_target_operations.py:response_bodyless` (a declared response's key), "
            "`_client/caching.py:_Caches.responses` (the response a cacheable status selects: "
            "`_client/plan.py:success_response`; a status that carries no content, such as a 204 a `2XX` selects, is "
            "refused: `bodyless`), `_client/plan.py:Planner.response` and `_fastapi/plan.py:Planner.response`, "
            "`_primary` (the answers of `response_bodyless`)"
        ),
        sites=(
            Home("_target_binding.py", "_Contracts.operation"),
            Home("_document_reader.py", "document_statuses"),
            Home("_client/naming.py", "token"),
            Home("_target_operations.py", "response_bodyless"),
            Home("_client/caching.py", "_Caches.responses"),
            Home("_client/plan.py", "success_response"),
            Home("_client/plan.py", "Planner.response"),
            Home("_fastapi/plan.py", "Planner.response"),
            Home("_fastapi/plan.py", "_primary"),
        ),
    ),
    DecisionHome(
        decision=(
            "what a received model codec's failure says of the data, in every client runtime site that decodes through "
            "a codec (bodies, parts, events, messages, webhook events): bytes that are not JSON or a value the model "
            "refuses (`codec_condition`), the body error each condition becomes (`coded`; a checked binary body reads "
            "every failure as a refused value, `syntax=False`), and the response failure a body error becomes "
            "(`ResponseDecoder._decoded`)"
        ),
        homes=(
            Home("_runtime/client/operations.py", "codec_condition"),
            Home("_runtime/client/operations.py", "coded"),
            Home("_runtime/client/operations.py", "ResponseDecoder._decoded"),
        ),
        core=(
            "`_runtime/client/operations.py`: `codec_condition`, `coded`, `ResponseDecoder._decoded` (copied into "
            "every client package)"
        ),
        adapters=(
            "the sites that raise their own errors from the condition: "
            "`_runtime/protocols/streams.py:_Events._decoded`, `_runtime/protocols/websocket.py:_Sockets._message`, "
            "`_runtime/protocols/webhook_events.py:EventDecoder.decode`"
        ),
        sites=(
            Home("_runtime/protocols/streams.py", "_Events._decoded"),
            Home("_runtime/protocols/websocket.py", "_Sockets._message"),
            Home("_runtime/protocols/webhook_events.py", "EventDecoder.decode"),
        ),
    ),
    DecisionHome(
        decision=(
            "what the runtime reads back from JSON and writes as JSON, so that every site parses JSON text, restores a "
            "saved value, writes JSON text and tells JSON numbers alike: the builtin values JSON text parses into "
            '(`json_value`; row `constants="refuse"`: NaN and the infinities raise ValueError where a saved value '
            'holds the result, a stream\'s cursor; row `floats="decimal"`: a number with a fraction or an exponent is '
            "the Decimal its text spells, a page's total), and the value a saved wire value restores, itself without a "
            "codec, else as its codec decodes its JSON (`_restored`), the compact JSON text a value writes with the "
            "runtime's own errors, worded alike on every Python version (`json_text`, `json_bytes`), whether a value "
            "is a JSON integer or number, a bool being neither (`json_integer`, `json_number`), and the canonical JSON "
            "that identifies a value, its members sorted by name (`canonical_json`: a polling state, a pager's end "
            "values), and the SHA-256 digest of it (`json_digest`: a pager's continuations and seed, a cache helper's "
            "base key)"
        ),
        homes=(
            Home("_runtime/model_codecs/media.py", "json_value"),
            Home("_runtime/model_codecs/media.py", "json_text"),
            Home("_runtime/model_codecs/media.py", "json_integer"),
            Home("_runtime/model_codecs/media.py", "json_number"),
            Home("_runtime/client/operations.py", "_restored"),
            Home("_runtime/protocols/records.py", "canonical_json"),
            Home("_runtime/protocols/records.py", "json_digest"),
        ),
        core=(
            "`_runtime/model_codecs/media.py`: `json_value`, `json_text`, `json_integer`, `json_number` (copied into "
            "every client and server package), `_runtime/client/operations.py:_restored` (copied into every client "
            "package), `_runtime/protocols/records.py`: `canonical_json`, `json_digest` (copied with every helper)"
        ),
        adapters=(
            "the sites that read them: the stdlib codecs' `decode` (`model_codecs/stdlib.py.jinja2`), "
            "`_runtime/protocols/records.py:plain_copy`, `_runtime/protocols/webhook_events.py:_parsed`, "
            "`_runtime/protocols/streams.py:_json`, `_runtime/protocols/pagination.py:_exact`, "
            "`_runtime/client/operations.py`: `ParameterSpec.restored`, `BodyMedia.restored`; "
            "`_runtime/server/responses.py:_content`; `_runtime/protocols/pagination.py`: `_integer`, `_restored`, "
            "`_runtime/protocols/resume.py:state_count`, `_runtime/protocols/sources.py:_count`, "
            "`_runtime/protocols/uploads.py:_Upload._offered`, `_runtime/protocols/caches.py`, "
            "`_runtime/client/timing.py:usable_seconds`"
        ),
        sites=(
            Home("_runtime/protocols/records.py", "plain_copy"),
            Home("_runtime/protocols/webhook_events.py", "_parsed"),
            Home("_runtime/protocols/streams.py", "_json"),
            Home("_runtime/protocols/pagination.py", "_exact"),
            Home("_runtime/client/operations.py", "ParameterSpec.restored"),
            Home("_runtime/client/operations.py", "BodyMedia.restored"),
            Home("_runtime/server/responses.py", "_content"),
            Home("_runtime/protocols/pagination.py", "_integer"),
            Home("_runtime/protocols/pagination.py", "_restored"),
            Home("_runtime/protocols/resume.py", "state_count"),
            Home("_runtime/protocols/sources.py", "_count"),
            Home("_runtime/protocols/uploads.py", "_Upload._offered"),
            Home("_runtime/client/timing.py", "usable_seconds"),
            Home("_runtime/protocols/caches.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "how the client runtime reads a URL-encoded or form-data body before its model reads it, so that a form "
            "response, a form object and a form-data part are read alike: the ordered name/value pairs URL-encoded "
            "bytes hold, each percent-decoded with a plus sign as a space (`form_pairs`: a form response's pairs, "
            "`decode_form`'s members), and whether a form-data part is read as JSON, its member being of the JSON kind "
            "or the part naming a JSON media type, or as the text of its member's lexical kind in the part's charset "
            "(`_text_kind`, `_typed_text`: a form object's members and the parts a codec decodes)"
        ),
        homes=(
            Home("_runtime/model_codecs/media.py", "form_pairs"),
            Home("_runtime/client/multipart_responses.py", "_text_kind"),
            Home("_runtime/client/multipart_responses.py", "_typed_text"),
        ),
        core=(
            "`_runtime/model_codecs/media.py:form_pairs` (copied into every client and server package), "
            "`_runtime/client/multipart_responses.py`: `_text_kind`, `_typed_text` (copied with multipart responses)"
        ),
        adapters=(
            "`_runtime/client/operations.py:_pairs`, `_runtime/model_codecs/media.py:decode_form`; "
            "`_runtime/client/multipart_responses.py`: `_part_value`, `_part_text` (a part's text failure as the "
            "body's), `_decoded`"
        ),
        sites=(
            Home("_runtime/client/operations.py", "_pairs"),
            Home("_runtime/model_codecs/media.py", "decode_form"),
            Home("_runtime/client/multipart_responses.py", "_part_value"),
            Home("_runtime/client/multipart_responses.py", "_part_text"),
            Home("_runtime/client/multipart_responses.py", "_decoded"),
        ),
    ),
    DecisionHome(
        decision=(
            "how the generated runtimes keep a value built once per object, so that every cache of a native reader, "
            "serializer or per-call value is kept alike: keyed by the object's identity, the entry holding the object "
            "so that the identity stays its own, built on first use and kept only when the build succeeds "
            "(`identity_cached`: a msgspec decoder and a Pydantic TypeAdapter of a type, the extras serializer of an "
            "adapter, the client's source of a binary body input and base URL of a server list)"
        ),
        homes=(Home("_runtime/model_codecs/shared.py", "identity_cached"),),
        core=(
            "`_runtime/model_codecs/shared.py:identity_cached` (copied into every client package, and into a server "
            "package with the extras serializer)"
        ),
        adapters=(
            "`_runtime/model_codecs/msgspec_structs.py:_decoder`, "
            "`_runtime/model_codecs/pydantic_adapters.py:adapter`, "
            "`_runtime/model_codecs/pydantic_extras.py:extras_serializer`, "
            "`_runtime/client/body_sources.py:BodyBindings.bind`, `_runtime/client/client.py:Core._base`"
        ),
        sites=(
            Home("_runtime/model_codecs/msgspec_structs.py", "_decoder"),
            Home("_runtime/model_codecs/pydantic_adapters.py", "adapter"),
            Home("_runtime/model_codecs/pydantic_extras.py", "extras_serializer"),
            Home("_runtime/client/body_sources.py", "BodyBindings.bind"),
            Home("_runtime/client/client.py", "Core._base"),
        ),
    ),
    DecisionHome(
        decision=(
            "what every model codec backend builds alike, so that a backend's codec class writes only how it converts, "
            "decodes and serializes: a value from header, form or part text through the backend's own conversion, text "
            "that is no JSON literal staying text for a backend that validates it (row `_validates`: msgspec and "
            "Pydantic) and refused by the stdlib mappers (`ModelCodec.text`), and a validating backend's model from "
            "the field arguments a call gives by wire name, converted as one mapping (`ValidatingCodec.assemble`); the "
            "generated type a validating codec keeps, by which its backend reads its native reader "
            "(`ValidatingCodec.type`)"
        ),
        homes=(
            Home("_runtime/model_codecs/shared.py", "ModelCodec"),
            Home("_runtime/model_codecs/shared.py", "ValidatingCodec"),
        ),
        core="`_runtime/model_codecs/shared.py`: `ModelCodec`, `ValidatingCodec` (copied into every client package)",
        adapters=(
            "the codec classes that build on them: `_runtime/model_codecs/msgspec_structs.py:MsgspecCodec`, "
            "`_runtime/model_codecs/pydantic_adapters.py:AdapterCodec`, the stdlib codecs of "
            "`_runtime/model_codecs/stdlib.py.jinja2`"
        ),
        sites=(
            Home("_runtime/model_codecs/msgspec_structs.py", "MsgspecCodec"),
            Home("_runtime/model_codecs/pydantic_adapters.py", "AdapterCodec"),
        ),
    ),
    DecisionHome(
        decision=(
            "what identifies a parameter of a request: its location and name, a header's name without regard to case "
            "(RFC 9110); the API walk's duplicate and override identity, the target checks that compare parameters, "
            "credentials and helper targets, and the client runtime's helper writes read it; and where a parameter or "
            "an apiKey credential of the target contract goes: a parameter's declared location and wire name, which "
            "keys its plan, and an apiKey security scheme's location (one of `API_KEY_LOCATIONS`: query, header, "
            "cookie) and nonempty name, a token in a header or a cookie (RFC 9110, RFC 6265: `is_token`), which the "
            "client, the server and the wire plan read alike; and the parameter a `location:name` key of a "
            "parameter-name setting selects, with an empty name for a nameless querystring"
        ),
        homes=(
            Home("_runtime/model_codecs/parameters.py", "parameter_key"),
            Home("_target_contract.py", "parameter_location"),
            Home("_target_contract.py", "parameter_place"),
            Home("_target_contract.py", "api_key_place"),
            Home("_target_contract.py", "parameter_name_key"),
        ),
        core=(
            "`_runtime/model_codecs/parameters.py:parameter_key` (copied into every client and server package; the "
            "generator imports it, the runtime never imports the generator); `_target_contract.py`: "
            "`parameter_location`, `parameter_place`, `api_key_place`, `parameter_name_key`"
        ),
        adapters=(
            "`parser/openapi.py:_parameter_identity`, which the paths scope's walk "
            "(`OpenAPIParser._process_path_items`) and "
            "`parser/openapi_scope.py:ApiOpenAPIParser._parameter_declarations` identify a parameter by (its record "
            "keeps the name first); `_openapi_wire_plan.py`: `_claimed`, `parameter_collisions`, `_parameters`, "
            "`_parameter`; `_client/security.py`: `SecurityPlanner.binding` and `_scheme`, "
            "`_client/protocol_plan.py:_target_problem`, `_client/plan.py:Planner._idempotency_header` and "
            "`Planner.parameters`, `_client/pagination.py:_target_key` and the helper planners; `_fastapi/plan.py`: "
            "`Planner.parameter` and `_scheme`; `_openapi_wire_plan.py:auth_names`; "
            "`_target_operations.py:placeholder_problems`; the `parameter_names` settings: "
            "`_client/config.py:_parameter_name`, `_fastapi/config.py:_parameter_key`; `_runtime/protocols/`: "
            "`writes.position`, `writes.targeted`, `pagination._started`, `uploads.UploadPlan.__post_init__`"
        ),
        sites=(
            Home("parser/openapi.py", "_parameter_identity"),
            Home("parser/openapi.py", "OpenAPIParser._process_path_items"),
            Home("parser/openapi_scope.py", "ApiOpenAPIParser._parameter_declarations"),
            Home("_openapi_wire_plan.py", "_claimed"),
            Home("_openapi_wire_plan.py", "parameter_collisions"),
            Home("_openapi_wire_plan.py", "_parameters"),
            Home("_openapi_wire_plan.py", "_parameter"),
            Home("_client/security.py", "SecurityPlanner.binding"),
            Home("_client/security.py", "_scheme"),
            Home("_client/protocol_plan.py", "_target_problem"),
            Home("_client/plan.py", "Planner._idempotency_header"),
            Home("_client/plan.py", "Planner.parameters"),
            Home("_client/pagination.py", "_target_key"),
            Home("_fastapi/plan.py", "Planner.parameter"),
            Home("_fastapi/plan.py", "_scheme"),
            Home("_openapi_wire_plan.py", "auth_names"),
            Home("_target_operations.py", "placeholder_problems"),
            Home("_client/config.py", "_parameter_name"),
            Home("_fastapi/config.py", "_parameter_key"),
        ),
    ),
    DecisionHome(
        decision=(
            "what the keys of an OpenAPI document's paths say: which fields of a path item hold an operation "
            "(`PATH_ITEM_METHODS`; views: `OPERATION_METHODS`, the fields before OpenAPI 3.2 added `QUERY_METHOD`, "
            "which the paths scope, the API walk of an earlier document and the recommender read; "
            "`path_item_operations`, the API walk's reading of a path item: the methods, from OpenAPI 3.2 also "
            "`QUERY_METHOD` and each entry of `additionalOperations`, which is refused when it is no mapping or names "
            "one of the `FIXED_HTTP_METHODS`), which keys of `paths` hold a path item (not `PATHS_PARAMETERS`, the "
            "parameters of every path, and no `x-` extension key; row `legacy-object-extension-paths` reads such a key "
            "that holds an object as a path, row `legacy-object-parameters-path` of `--skip-path-extension-keys` only "
            "a `parameters` key), which paths `--openapi-include-paths` selects (each path and pattern with a leading "
            "slash, by `fnmatch.fnmatch`), which entries of an operation's Responses Object are responses (its status "
            "codes and `default`; an `x-` extension that holds no object is none, and row "
            "`legacy-object-extension-responses` reads one that holds an object as a response), and the operation in "
            "effect: its own parameters, then those of its path item (after those of `paths`) it does not override, "
            "and the document's security requirement unless it declares its own (an empty list included), on a copy "
            "that leaves the document unchanged"
        ),
        homes=(
            Home("_openapi_paths.py", "PATH_ITEM_METHODS"),
            Home("_openapi_paths.py", "OPERATION_METHODS"),
            Home("_openapi_paths.py", "QUERY_METHOD"),
            Home("_openapi_paths.py", "ADDITIONAL_OPERATIONS"),
            Home("_openapi_paths.py", "FIXED_HTTP_METHODS"),
            Home("_openapi_paths.py", "PATHS_PARAMETERS"),
            Home("_openapi_paths.py", "path_item_operations"),
            Home("_openapi_paths.py", "holds_path_item"),
            Home("_openapi_paths.py", "operation_responses"),
            Home("_openapi_paths.py", "path_selected"),
            Home("_openapi_paths.py", "rooted_path"),
            Home("_openapi_paths.py", "effective_parameters"),
            Home("_openapi_paths.py", "inherited_security"),
            Home("_openapi_paths.py", "effective_operation"),
        ),
        core=(
            "`_openapi_paths.py`: `PATH_ITEM_METHODS`, `OPERATION_METHODS`, `QUERY_METHOD`, `ADDITIONAL_OPERATIONS`, "
            "`FIXED_HTTP_METHODS`, `PATHS_PARAMETERS`, `path_item_operations`, `holds_path_item`, "
            "`operation_responses`, `path_selected`, `rooted_path`, `effective_parameters`, `inherited_security`, "
            "`effective_operation`"
        ),
        adapters=(
            "every caller reads or calls the core: `parser/openapi.py` (`OPERATION_NAMES`, the public list of "
            "`OPERATION_METHODS`, which both walks read; `OpenAPIParser._normalize_path` and `_matches_path_pattern`, "
            "the hooks of both walks; `OpenAPIParser._process_path_items`, which overrides by `_declared_identity`; "
            "the `Operation` model, whose `responses` it reads by `operation_responses`), "
            "`parser/openapi_scope.py:ApiOpenAPIParser._api_operations` (`path_item_operations`) and "
            "`_walk_api_operation` (overrides by the identity of `_parameter_identity`; reads the responses by "
            "`operation_responses`), the operation contract of the target binding "
            "(`_target_binding.py:_Contracts.operation`, the inherited security as a fact), the `$ref` edges of a "
            "document (`parser/_schema_reading.py`), the members of an OpenAPI path item "
            "(`_openapi_objects.py:OBJECT_MEMBERS`) and the recommender (`_recommend/_signals.py`; `_path_operations` "
            "reads the keys of `paths` and the fields of a path item of the api scope as its walk does, through "
            "`holds_path_item` and `path_item_operations`, and those of the paths and webhooks scopes as theirs do, "
            "with row `legacy-object-extension-paths` and `OPERATION_METHODS`; `_count_operations` counts the "
            "operations of the api scope's walk, and in the other scopes the methods of every key of `paths`, a key "
            "that is no string included, for the size note)"
        ),
        sites=(
            Home("parser/openapi.py", "OpenAPIParser._normalize_path"),
            Home("parser/openapi.py", "OpenAPIParser._matches_path_pattern"),
            Home("parser/openapi.py", "OpenAPIParser._process_path_items"),
            Home("parser/openapi.py", "OpenAPIParser._declared_identity"),
            Home("parser/openapi.py", "Operation"),
            Home("parser/openapi_scope.py", "ApiOpenAPIParser._api_operations"),
            Home("parser/openapi_scope.py", "ApiOpenAPIParser._walk_api_operation"),
            Home("parser/openapi.py", "_parameter_identity"),
            Home("_target_binding.py", "_Contracts.operation"),
            Home("_recommend/_signals.py", "_path_operations"),
            Home("_recommend/_signals.py", "_count_operations"),
            Home("parser/_schema_reading.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "which members of an OpenAPI object of each role (path item, operation, parameter, header, media type, "
            "encoding, request body, response, callback, the root and its components) hold other objects, in one, a "
            "list or a map, and their role; the component kind of each role a reference can name; and the schemas an "
            "object of a role holds, through the objects between them: a document a reference names at its root holds "
            "the object that reference's position expects (OpenAPI: \"a document containing a referenceable Object at "
            'its root based on the expected type of the reference"), so those schemas declare its `$id` and `$anchor` '
            "values (a Reference Object holds none, and an object that holds itself, as a YAML alias does, is "
            "descended once on its path)"
        ),
        homes=(
            Home("_openapi_objects.py", "OBJECT_MEMBERS"),
            Home("_openapi_objects.py", "COMPONENT_KINDS"),
            Home("_openapi_objects.py", "COMPONENT_ROLES"),
            Home("_openapi_objects.py", "object_members"),
            Home("_openapi_objects.py", "held_schemas"),
        ),
        core=(
            "`_openapi_objects.py`: `OBJECT_MEMBERS`, `COMPONENT_KINDS`, `COMPONENT_ROLES`, `object_members`, "
            "`held_schemas`"
        ),
        adapters=(
            "the schema resources of a document (`parser/_schema_documents.py:implicit_schema_resources`, the roles a "
            "reference records where it is followed, `parser/_schema_refs.py:hold_document_root`, at "
            "`parser/openapi.py:OpenAPIParser.get_ref_model`, `_global_parameter` and "
            "`parser/openapi_scope.py:ApiOpenAPIParser._reference_target`), the components the API walk reads "
            "(`parser/openapi_scope.py`) and the served document (`_fastapi/source_document.py`: the members it "
            "copies; what the schemas of a document declare it reads from the parser's registry)"
        ),
        sites=(
            Home("parser/_schema_documents.py", "implicit_schema_resources"),
            Home("parser/_schema_refs.py", "hold_document_root"),
            Home("parser/openapi.py", "OpenAPIParser.get_ref_model"),
            Home("parser/openapi_scope.py", "ApiOpenAPIParser._reference_target"),
            Home("_fastapi/source_document.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "what the security of the target contract says, read alike by the wire plan and both targets: an "
            "operation's security requirement alternatives in source order, each piece as declared (an alternative "
            "that is not a mapping, a key that is not a scalar, scopes that are not a list of strings) and the view "
            "that takes only mappings from string scheme names to lists of string scopes (`Requirement`; empty without "
            "a `security` fact), the schemes a requirement names (those the document that uses its operation declares, "
            "by name), the scopes a requirement may list for a scheme (scope tokens for oauth2 and openIdConnect; role "
            "names, any text, for another scheme, and none in OpenAPI 3.0), and the scheme an http security scheme "
            "names, in lower case"
        ),
        homes=(
            Home("_target_contract.py", "requirement_alternatives"),
            Home("_target_contract.py", "security_requirements"),
            Home("_target_contract.py", "scope_problem"),
            Home("_target_contract.py", "scheme_catalogues"),
            Home("_target_contract.py", "operation_schemes"),
            Home("_target_contract.py", "http_scheme"),
        ),
        core=(
            "`_target_contract.py`: `requirement_alternatives`, `security_requirements`, `scope_problem`, "
            "`scheme_catalogues`, `operation_schemes`, `http_scheme`"
        ),
        adapters=(
            "`_client/security.py`: `SecurityPlanner` (`binding`, a diagnostic per piece) and `_scheme`; "
            "`_fastapi/plan.py`: `Planner.security`, `Planner.scheme` and `_scheme`; "
            "`_openapi_wire_plan.py:auth_names` (the apiKey credentials of the view)"
        ),
        sites=(
            Home("_client/security.py", "SecurityPlanner"),
            Home("_client/security.py", "SecurityPlanner.binding"),
            Home("_client/security.py", "_scheme"),
            Home("_fastapi/plan.py", "Planner.security"),
            Home("_fastapi/plan.py", "Planner.scheme"),
            Home("_fastapi/plan.py", "_scheme"),
            Home("_openapi_wire_plan.py", "auth_names"),
        ),
    ),
    DecisionHome(
        decision=(
            "the text a recorded literal of the target contract holds: the value of a string scalar (`LiteralScalar` "
            "holding a `str`), and none for any other literal"
        ),
        homes=(Home("_target_contract.py", "literal_text"),),
        core="`_target_contract.py:literal_text`",
        adapters=(
            "the tags of an operation, which leave the others out (`_target_operations.py:tags`), the scopes of a "
            "security requirement (`_target_contract.py:_texts`), the tag values a client codec reads from a tag "
            "field's `Literal` (`_client/codec_plan.py:_Planner.discriminator`), the operation a server link names "
            "(`_fastapi/plan.py:_link_operation_problem`) and the member names of a documentation mapping "
            "(`_fastapi/plan.py:json_literal`)"
        ),
        sites=(
            Home("_target_operations.py", "tags"),
            Home("_target_contract.py", "_texts"),
            Home("_client/codec_plan.py", "_Planner.discriminator"),
            Home("_fastapi/plan.py", "_link_operation_problem"),
            Home("_fastapi/plan.py", "json_literal"),
        ),
    ),
    DecisionHome(
        decision=(
            "what a sequence read item by item gives: the value of every item in order, or none for the whole when one "
            "item gives none; a lazy reading stops at the first item that gives none, so a caller whose readings must "
            "all run (each records its problem, or may raise) reads them into a list first"
        ),
        homes=(Home("_all_or_none.py", "all_or_none"),),
        core="`_all_or_none.py:all_or_none`",
        adapters=(
            "the scopes of a security requirement and the requirements of an operation (`_target_contract.py`: "
            "`_texts`, `security_requirements`), the member names of a documentation mapping and a list of JSON "
            "scalars as a default (`_fastapi/plan.py`: `json_literal`, `_literal`), the servers of a client operation "
            "and their variables (`_client/plan.py`: `_servers`, `_variables`), the events of a webhook helper "
            "(`_client/webhooks.py:webhook_uses`), the kinds of a union's members together "
            "(`_target_types.py:_joined`), the arguments of a generic an input model reuses "
            "(`input_model.py:_exported_generic_expression`), the parts of a legacy IPv4 literal "
            "(`http.py:_parse_legacy_ipv4_address`), the lineages of the heads of a C3 merge (`_mro.py:linearize`) and "
            "the models of an acyclic order (`_reference_graph.py:ReferenceGraph.acyclic_order`); left out: the "
            "literal patterns of the lookaheads (`_regex_syntax.py:lookahead_patterns`; the regex leaf imports only "
            "the standard library and `_records`)"
        ),
        sites=(
            Home("_target_contract.py", "_texts"),
            Home("_target_contract.py", "security_requirements"),
            Home("_fastapi/plan.py", "json_literal"),
            Home("_fastapi/plan.py", "_literal"),
            Home("_client/plan.py", "_servers"),
            Home("_client/plan.py", "_variables"),
            Home("_client/webhooks.py", "webhook_uses"),
            Home("_target_types.py", "_joined"),
            Home("input_model.py", "_exported_generic_expression"),
            Home("http.py", "_parse_legacy_ipv4_address"),
            Home("_mro.py", "linearize"),
            Home("_reference_graph.py", "ReferenceGraph.acyclic_order"),
            Home("_regex_syntax.py", "lookahead_patterns"),
        ),
    ),
    DecisionHome(
        decision=(
            "the parts of a request the client runtime writes and reads back, so that what one site writes another "
            "reads alike (a credential placed is the credential a redirect strips, a dot segment refused is the "
            "segment sent): the schemes of an HTTP URL (`HTTP_SCHEMES`, also the generator's server base), the "
            "placeholders of a path or server template (`PLACEHOLDER`, also the server's and the generator's) and the "
            "template filled with values (`filled`), the characters the literal text of a path keeps (`PATH_SAFE`) and "
            "the encoding that keeps a text's percent-encoded octets (`percent_encoded`), the name of a query field as "
            "forms decode it (`query_field`), and the name of a Cookie pair (`cookie_name`)"
        ),
        homes=(
            Home("_runtime/client/options.py", "HTTP_SCHEMES"),
            Home("_runtime/client/paths.py", "filled"),
            Home("_runtime/model_codecs/parameters.py", "PLACEHOLDER"),
            Home("_runtime/model_codecs/parameters.py", "PATH_SAFE"),
            Home("_runtime/model_codecs/parameters.py", "percent_encoded"),
            Home("_runtime/client/urls.py", "query_field"),
            Home("_runtime/client/positions.py", "cookie_name"),
        ),
        core=(
            "`_runtime/client/options.py:HTTP_SCHEMES`, `_runtime/client/paths.py:filled`, "
            "`_runtime/model_codecs/parameters.py`: `PLACEHOLDER`, `PATH_SAFE`, `percent_encoded`, "
            "`_runtime/client/urls.py:query_field`, `_runtime/client/positions.py:cookie_name` (copied into every "
            "client package, `parameters.py` into every server package too; the generator imports `options.py` and "
            "`parameters.py`, the runtime never imports the generator)"
        ),
        adapters=(
            "`_client/plan.py:Planner.servers` (the document's location as a server base); the sites that read them: "
            "`_runtime/client/client.py` (`_query`, `_server_url`, `Core._prepare`), "
            "`_runtime/client/positions.py:credentialed`, `_runtime/client/auth.py:_place`, `_runtime/client/urls.py`: "
            "`_origin`, `strip_query`; `_runtime/protocols/client.py:CacheRequest.identity`, "
            "`_runtime/protocols/pagination.py:_Walk.sent`; `_runtime/server/requests.py` (`RawPath`, `_encoded`, "
            "`ParameterAdapter._path`), `_runtime/model_codecs/parameters.py:_percent` (a parameter that allows "
            "reserved characters), `_target_naming.py:PATH_PLACEHOLDER` (the generator's routes, names and server "
            "URLs), `_client/render.py:_sent_path`"
        ),
        sites=(
            Home("_client/plan.py", "Planner.servers"),
            Home("_runtime/client/client.py", "_query"),
            Home("_runtime/client/client.py", "_server_url"),
            Home("_runtime/client/client.py", "Core._prepare"),
            Home("_runtime/client/positions.py", "credentialed"),
            Home("_runtime/client/auth.py", "_place"),
            Home("_runtime/client/urls.py", "_origin"),
            Home("_runtime/client/urls.py", "strip_query"),
            Home("_runtime/protocols/client.py", "CacheRequest.identity"),
            Home("_runtime/protocols/pagination.py", "_Walk.sent"),
            Home("_runtime/server/requests.py", "RawPath"),
            Home("_runtime/server/requests.py", "_encoded"),
            Home("_runtime/server/requests.py", "ParameterAdapter._path"),
            Home("_runtime/model_codecs/parameters.py", "_percent"),
            Home("_client/render.py", "_sent_path"),
        ),
    ),
    DecisionHome(
        decision=(
            "the text of a credential the client places, so that a credential obeys the rule of its position and both "
            "client authentications write Basic alike: the credentials of the Basic scheme (`basic_credentials`: a "
            "basic scheme's and an OAuth client's), the octets a cookie value sent as it is may hold (`cookie_octets`, "
            "beside `header_text`: a cookie credential's and a cookie parameter's), and the error the failure of an "
            "application's credential callable or callback becomes (`provider_failure`: `AuthError` `provider_failed` "
            "keeping it as the cause, for the failures the application failure row below wraps)"
        ),
        homes=(
            Home("_runtime/client/auth.py", "basic_credentials"),
            Home("_runtime/client/auth.py", "provider_failure"),
            Home("_runtime/model_codecs/parameters.py", "cookie_octets"),
        ),
        core=(
            "`_runtime/client/auth.py`: `basic_credentials`, `provider_failure` (copied with any credential scheme); "
            "`_runtime/model_codecs/parameters.py:cookie_octets` (copied into every client and server package)"
        ),
        adapters=(
            "the sites that place or check them: `_runtime/client/auth.py`: `_called`, `_text`, `_place`; "
            "`_runtime/client/oauth.py`: `_secret`, `_Provider._notify`, `_Provider._request`, `_Provider.atoken`; "
            "`_runtime/model_codecs/parameters.py:_encode_cookie`"
        ),
        sites=(
            Home("_runtime/client/auth.py", "_called"),
            Home("_runtime/client/auth.py", "_text"),
            Home("_runtime/client/auth.py", "_place"),
            Home("_runtime/client/oauth.py", "_secret"),
            Home("_runtime/client/oauth.py", "_Provider._notify"),
            Home("_runtime/client/oauth.py", "_Provider._request"),
            Home("_runtime/client/oauth.py", "_Provider.atoken"),
            Home("_runtime/model_codecs/parameters.py", "_encode_cookie"),
        ),
    ),
    DecisionHome(
        decision=(
            "what a failure of application code the client runtime calls becomes, so that every feature keeps the "
            "application's own SDK error and chains any other exception alike: an SDK error it raises propagates as it "
            "is, with its own cause and context, and any other exception is raised as the calling feature's failure, "
            "which keeps it as its cause (`called`, `acalled` with the feature's failure: a credential callable's, an "
            "OAuth client secret's and a token callback's `provider_failure`, a cache store's `store_failed`)"
        ),
        homes=(
            Home("_runtime/client/errors.py", "called"),
            Home("_runtime/client/errors.py", "acalled"),
        ),
        core="`_runtime/client/errors.py`: `called`, `acalled` (copied into every client package)",
        adapters=(
            "the sites that call application code: `_runtime/client/auth.py:_called`; `_runtime/client/oauth.py`: "
            "`_secret`, `_Provider._notify`, `_Provider.atoken`; `_runtime/protocols/cache.py`: `_run`, `_arun` (a "
            "store's `get`, `set` and `delete`, with `store_failed`)"
        ),
        sites=(
            Home("_runtime/client/auth.py", "_called"),
            Home("_runtime/client/oauth.py", "_secret"),
            Home("_runtime/client/oauth.py", "_Provider._notify"),
            Home("_runtime/client/oauth.py", "_Provider.atoken"),
            Home("_runtime/protocols/cache.py", "_run"),
            Home("_runtime/protocols/cache.py", "_arun"),
        ),
    ),
    DecisionHome(
        decision=(
            "what the client generator calls each helper kind, so that a new kind is named in one row: the label "
            "documentation and messages give the kind (`SSE`, `upload`), the keyword the kind's methods take its "
            "options by (`HELPER_ARGUMENTS` reserves each), and the runtime class of those options"
        ),
        homes=(
            Home("_client/naming.py", "HELPER_KINDS"),
            Home("_runtime/protocols/options.py", "_KIND_OPTIONS"),
        ),
        core=(
            "`_client/naming.py:HELPER_KINDS`, `_runtime/protocols/options.py:_KIND_OPTIONS` (the class each kind's "
            "`helper_defaults` take; the generator imports it, the runtime never imports the generator)"
        ),
        adapters=(
            "`_client/render.py:_helper_options` (keyword and class of a method's options), the readers of the label: "
            "`_client/render.py` (`_helper_noun`, `ClientRenderer.stream_label`), "
            "`_client/streams.py:_Streams.helper`, `_client/polling.py:_Polls.required` (body refusals)"
        ),
        sites=(
            Home("_client/render.py", "_helper_options"),
            Home("_client/render.py", "_helper_noun"),
            Home("_client/render.py", "ClientRenderer.stream_label"),
            Home("_client/streams.py", "_Streams.helper"),
            Home("_client/polling.py", "_Polls.required"),
        ),
    ),
    DecisionHome(
        decision=(
            "what a protocol helper's configuration stands for at run time, so that the validator checks the records "
            "the renderer spells: the selector and request target record of each selector and target "
            "(`selector_record`, `target_record`: a body pointer, a header and its occurrence or the status; a "
            "parameter's location and name, a querystring property or a body member), the frame every WebSocket "
            "message of a codec takes with the media kind its payload is typed as (`MESSAGE_CODECS`), and the "
            "continuation kinds that count a pagination helper's position (`COUNTED_CONTINUATIONS`: an item offset or "
            "a page number, each with a first value, a step and one end evidence, run as a `CountPlan`)"
        ),
        homes=(
            Home("_client/protocols.py", "selector_record"),
            Home("_client/protocols.py", "target_record"),
            Home("_client/protocols.py", "MESSAGE_CODECS"),
            Home("_client/protocols.py", "COUNTED_CONTINUATIONS"),
        ),
        core="`_client/protocols.py` (`selector_record`, `target_record`, `MESSAGE_CODECS`, `COUNTED_CONTINUATIONS`)",
        adapters=(
            "`_client/protocols.py:_Validator` (`selector`, `target`, `message`, `continuation`), "
            "`_client/render.py:_Helpers` (`selector`, `target`, `continuation`, `message_types`), the counted "
            "pagination a client's runtime reference describes "
            "(`_client/render.py:ClientRenderer.runtime_documentation`)"
        ),
        sites=(
            Home("_client/protocols.py", "_Validator"),
            Home("_client/protocols.py", "_Validator.selector"),
            Home("_client/protocols.py", "_Validator.target"),
            Home("_client/protocols.py", "_Validator.message"),
            Home("_client/protocols.py", "_Validator.continuation"),
            Home("_client/render.py", "_Helpers"),
            Home("_client/render.py", "_Helpers.selector"),
            Home("_client/render.py", "_Helpers.target"),
            Home("_client/render.py", "_Helpers.continuation"),
            Home("_client/render.py", "_Helpers.message_types"),
            Home("_client/render.py", "ClientRenderer.runtime_documentation"),
        ),
    ),
    DecisionHome(
        decision=(
            "how generated code spells a runtime record the generator holds, so that a record's class, keywords and "
            "defaults come from the runtime record itself: its class named from the module of the package's runtime "
            "copy that defines it, each field the constructor takes by keyword unless it holds the field's default, a "
            "record in a field by its own constructor and a tuple item by item, as a call or as the arguments of a "
            "module-level plan constant (rows: a member's plan by its positional constructor, its name and kind and "
            "`repeated=True` only when it repeats, for the members of a parameter plan, a form and a form-data body "
            "read as parts (`member_plan`: `FieldPlan`, `PartPlan`); a security record's schemes by their constants)"
        ),
        homes=(
            Home("_target_render.py", "runtime_record"),
            Home("_target_render.py", "record_plan"),
            Home("_target_render.py", "member_plan"),
        ),
        core="`_target_render.py`: `runtime_record`, `record_plan`, `member_plan`",
        adapters=(
            "`_target_render.py:parameter_plan`, the form and part plans of a client's body media "
            "(`_client/render.py:_Registry`: `form`, `multipart_members`, `branches`) and a server's form media "
            "(`_fastapi/render.py:_body_media`); left out: a member of a body sent as parts, which writes its kind "
            "only for JSON and its codec, media types and style by keyword (`_client/render.py:_Registry.sent_plan`); "
            "`_client/render.py:_Helpers` (`selector`, `target`, `continuation`: selectors, request targets and the "
            "four continuation plans), `_client/render.py:_Registry` (`servers`, `retry_metadata`, `field_arguments`: "
            "an operation's servers and their variables, its idempotency key header and its field arguments), "
            "`_client/render.py:_Security.source` (the security schemes and each operation's security binding)"
        ),
        sites=(
            Home("_target_render.py", "parameter_plan"),
            Home("_client/render.py", "_Registry"),
            Home("_client/render.py", "_Registry.form"),
            Home("_client/render.py", "_Registry.multipart_members"),
            Home("_client/render.py", "_Registry.branches"),
            Home("_fastapi/render.py", "_body_media"),
            Home("_client/render.py", "_Registry.sent_plan"),
            Home("_client/render.py", "_Helpers"),
            Home("_client/render.py", "_Helpers.selector"),
            Home("_client/render.py", "_Helpers.target"),
            Home("_client/render.py", "_Helpers.continuation"),
            Home("_client/render.py", "_Registry.servers"),
            Home("_client/render.py", "_Registry.retry_metadata"),
            Home("_client/render.py", "_Registry.field_arguments"),
            Home("_client/render.py", "_Security.source"),
        ),
    ),
    DecisionHome(
        decision=(
            "where a protocol helper's schema reference is and the type its value use is read or sent as, so that "
            "every helper kind refuses a missing schema alike: the reference's location when its document has the "
            "pointer (`location`), the type of the schema's value use in a direction, or `missing` (`schema_type`), "
            "and the refusal that names the setting (`missing_schema`: the item_schema, result schema, schema or "
            "event_schema)"
        ),
        homes=(
            Home("_client/protocol_plan.py", "HelperPlanner.location"),
            Home("_client/protocol_plan.py", "HelperPlanner.schema_type"),
            Home("_client/protocol_plan.py", "missing_schema"),
        ),
        core="`_client/protocol_plan.py`: `HelperPlanner.location`, `HelperPlanner.schema_type`, `missing_schema`",
        adapters=(
            "the planners that check a helper's schemas: `_client/pagination.py:_Pages.schema` (item and result "
            "schemas of pagination, polling and upload helpers), `_client/streams.py:_Streams.locations` (stream and "
            "WebSocket schemas), `_client/webhooks.py:webhook_uses` (event schemas)"
        ),
        sites=(
            Home("_client/pagination.py", "_Pages.schema"),
            Home("_client/streams.py", "_Streams.locations"),
            Home("_client/webhooks.py", "webhook_uses"),
        ),
    ),
    DecisionHome(
        decision=(
            "how an SSE, NDJSON or webhook helper routes its events, so that the planners, the stream plan and the "
            "webhook decoder read one configuration alike: the selector its discriminator reads, none for one event "
            "schema (`discriminator`), and whether a stream helper gives an event of an unmapped type as an "
            "`UnknownEvent` (`StreamSpec.keeps_unknown`)"
        ),
        homes=(
            Home("_client/streams.py", "discriminator"),
            Home("_client/streams.py", "StreamSpec.keeps_unknown"),
        ),
        core="`_client/streams.py`: `discriminator`, `StreamSpec.keeps_unknown`",
        adapters=(
            "`_client/streams.py` (`_events`, `_Streams.terminal`, `_discriminated`), `_client/render.py:_Helpers` "
            "(`event_type`, `stream_plan`), `_client/webhooks.py:_Webhooks.event`"
        ),
        sites=(
            Home("_client/streams.py", "_events"),
            Home("_client/streams.py", "_Streams.terminal"),
            Home("_client/streams.py", "_discriminated"),
            Home("_client/render.py", "_Helpers"),
            Home("_client/render.py", "_Helpers.event_type"),
            Home("_client/render.py", "_Helpers.stream_plan"),
            Home("_client/webhooks.py", "_Webhooks.event"),
        ),
    ),
    DecisionHome(
        decision=(
            "the names a client package's generated modules define for each other, so that the module that defines a "
            "name and every module that imports it spell it alike: the constant or function of a family that serves a "
            "module's index-th item (`numbered`: an operation's registry plan and security binding `OPERATION_n`, its "
            "keywords record `KEYWORDS_n`, a helper's plans `PLAN_n`, `CANCEL_n`, `STREAM_n`, `SOCKET_n` and typed "
            "accessors `_items_n`, `_result_n`, `_immediate_n`, a use's model codec `codec_n`), an operation's "
            "response alias (`response_alias`), header decoder (`header_decoder`), the constant of its header decoders "
            "(`header_constant`: the method name upper-cased, which the types module claims in its scope, as two names "
            "can upper-case alike), the classes of a resource and its views in the sync and asyncio modules "
            "(`resource_class`), a resource's PascalCase name (the class name of each part of its dotted namespace, "
            "joined), the namespaces a resource opens (its outer ones first) and its parts, and the class of a helper "
            "namespace (the target's PascalCase parts, then `Protocols`)"
        ),
        homes=(
            Home("_client/naming.py", "numbered"),
            Home("_client/naming.py", "response_alias"),
            Home("_client/naming.py", "header_decoder"),
            Home("_client/naming.py", "header_constant"),
            Home("_client/naming.py", "resource_class"),
            Home("_client/naming.py", "helper_classes"),
            Home("_client/naming.py", "namespace_class"),
            Home("_client/plan.py", "Planner._resource_pascal"),
            Home("_client/plan.py", "_opened"),
            Home("_client/plan.py", "_namespace_parts"),
        ),
        core=(
            "`_client/naming.py`: `numbered`, `response_alias`, `header_decoder`, `header_constant`, `resource_class`, "
            "`helper_classes`, `namespace_class`; `_client/plan.py`: `Planner._resource_pascal`, `_opened`, "
            "`_namespace_parts`"
        ),
        adapters=(
            "the references of other modules: `_client/render.py` (`_response_type`, `_operation_plan`, "
            "`_helper_plan`, the helper namespaces of `_Helpers.module`), "
            "`_client/codec_render.py:UseAccessors.reference`; the resource names and types of the client plan "
            "(`_client/plan.py`) and the helper classes of the protocol plan (`_client/protocol_plan.py`)"
        ),
        sites=(
            Home("_client/render.py", "_response_type"),
            Home("_client/render.py", "_operation_plan"),
            Home("_client/render.py", "_helper_plan"),
            Home("_client/render.py", "_Helpers.module"),
            Home("_client/codec_render.py", "UseAccessors.reference"),
            Home("_client/plan.py"),
            Home("_client/protocol_plan.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "what a client package declares from its operations and helpers, so that its clients, runtime copies and "
            "documentation read one answer: an operation accepting gzip request bodies (`compresses`: the "
            "`compression` keyword, the request coding and the compression runtime), the pagination helpers among its "
            "helpers (`_pages`) and whether one follows the URLs a server gives (`PaginationSpec.follows`, `_follows`: "
            "a next-URL or Link continuation, the `allowed_origins` keyword and the runtime reference), and whether a "
            "cache helper is among them (`_caches`)"
        ),
        homes=(
            Home("_client/runtime.py", "compresses"),
            Home("_client/pagination.py", "PaginationSpec.follows"),
            Home("_client/render.py", "_pages"),
            Home("_client/render.py", "_follows"),
            Home("_client/render.py", "_caches"),
        ),
        core=(
            "`_client/runtime.py:compresses`, `_client/pagination.py:PaginationSpec.follows`, `_client/render.py`: "
            "`_pages`, `_follows`, `_caches`"
        ),
        adapters="-",
        sites=(),
    ),
    DecisionHome(
        decision=(
            "how a target turns its output into files, so that the client and the server render through their roles "
            "and copy their runtime alike: the roles a target renders its modules through (a template directory's "
            "overrides, or the builtins without one; `roles`) and the copies of the runtime modules it declares with "
            "their packages' initializers, in path order, which the coordinator heads and encodes without formatting "
            "(`runtime_files`)"
        ),
        homes=(
            Home("_target_templates.py", "roles"),
            Home("_api_generation.py", "runtime_files"),
        ),
        core="`_target_templates.py:roles`, `_api_generation.py:runtime_files`",
        adapters="-",
        sites=(),
    ),
    DecisionHome(
        decision=(
            "the values a protocol helper's bindings write into a later request, so that every helper reads and checks "
            "them alike: each selector's value read from a response that must give it (`required_values`: a missing "
            "value and a header read once that the response repeats refused), and a read value that makes a path "
            "segment a dot segment once encoded refused as a value the server gave (`refuse_dotted`: a targeted "
            "operation's, a pager's or a stream reopen's segments; the caller's own path arguments in a segment keep "
            "the texts `callers` gives)"
        ),
        homes=(
            Home("_runtime/protocols/pagination.py", "required_values"),
            Home("_runtime/protocols/writes.py", "dotted_write"),
            Home("_runtime/protocols/writes.py", "refuse_dotted"),
        ),
        core=(
            "`_runtime/protocols/pagination.py:required_values`, `_runtime/protocols/writes.py`: `dotted_write`, "
            "`refuse_dotted` (copied with any helper that has bindings)"
        ),
        adapters=(
            "the sites that read or restore binding values: `_runtime/protocols/pagination.py`: `targeted_values`, "
            "`_Walk.bound`, `_Walk.dotted`; `_runtime/protocols/streams.py`: `_bound`, `_restore`; "
            "`_runtime/protocols/polling.py:_Operation._restore`, `_runtime/protocols/uploads.py:_resume_start`"
        ),
        sites=(
            Home("_runtime/protocols/pagination.py", "targeted_values"),
            Home("_runtime/protocols/pagination.py", "_Walk.bound"),
            Home("_runtime/protocols/pagination.py", "_Walk.dotted"),
            Home("_runtime/protocols/streams.py", "_bound"),
            Home("_runtime/protocols/streams.py", "_restore"),
            Home("_runtime/protocols/polling.py", "_Operation._restore"),
            Home("_runtime/protocols/uploads.py", "_resume_start"),
        ),
    ),
    DecisionHome(
        decision=(
            "where a protocol helper writes values into a later request and which names it writes, so that every "
            "helper's plan derives them alike: the operation taking the values as wire values (`targeted_writes`: each "
            "binding's and then each extra target's argument position or pointer, its codec skipped, and the path "
            "segments a read value is written to; `targeted` without the path segments), and the header and query "
            "fields those writes replace, which a call's options must not patch (`header_written`: a header parameter "
            "written, by its name in any case; `query_written`: a query parameter written, by its name, but an "
            "exploded form or deepObject object parameter by the properties it owns, any name where it accepts "
            "additional ones, and a form querystring by the member a pointer names or, written whole, the properties "
            "it owns; a path or cookie parameter, another querystring and the body write neither; a helper's writers "
            "are every operation it writes, an upload's create call with the size target included)"
        ),
        homes=(
            Home("_runtime/protocols/writes.py", "targeted"),
            Home("_runtime/protocols/writes.py", "targeted_writes"),
            Home("_runtime/protocols/writes.py", "header_written"),
            Home("_runtime/protocols/writes.py", "query_written"),
            Home("_runtime/protocols/writes.py", "written_fields"),
        ),
        core=(
            "`_runtime/protocols/writes.py`: `targeted`, `targeted_writes`, `header_written`, `query_written`, "
            "`written_fields` (copied with any helper that has bindings)"
        ),
        adapters=(
            "the plans that hold them: `_runtime/protocols/pagination.py`: `PaginationPlan.__post_init__`, `_first`, "
            "`_limits` (a count's own position); `_runtime/protocols/polling.py`: `CancelPlan.__post_init__`, "
            "`PollingPlan.__post_init__`; `_runtime/protocols/uploads.py:UploadPlan.__post_init__`; "
            "`_runtime/protocols/streams.py`: `StreamResumePlan.__post_init__`, `_unpatched`"
        ),
        sites=(
            Home("_runtime/protocols/pagination.py", "PaginationPlan.__post_init__"),
            Home("_runtime/protocols/pagination.py", "_first"),
            Home("_runtime/protocols/pagination.py", "_limits"),
            Home("_runtime/protocols/polling.py", "CancelPlan.__post_init__"),
            Home("_runtime/protocols/polling.py", "PollingPlan.__post_init__"),
            Home("_runtime/protocols/uploads.py", "UploadPlan.__post_init__"),
            Home("_runtime/protocols/streams.py", "StreamResumePlan.__post_init__"),
            Home("_runtime/protocols/streams.py", "_unpatched"),
        ),
    ),
    DecisionHome(
        decision=(
            "what a protocol helper reads from a response at a selector, so that every helper reads and refuses "
            "response values alike: the wire value a body pointer, a header (every occurrence as an array, or one, a "
            "repeated one refused as malformed) or the status gives (`selected`, `read_selector`), one that must be "
            "given (`required`), the decoded value a helper returns from a response, read by its accessor only after "
            "the wire value has the member (`accessed`: a page's items, an operation's immediate or inline result; a "
            "missing or null member, or a None the accessor reads, refused), and the rule a null or mistyped value "
            "breaks besides an absent one (`unfit`, through `absence`)"
        ),
        homes=(
            Home("_runtime/protocols/values.py", "selected"),
            Home("_runtime/protocols/values.py", "read_selector"),
            Home("_runtime/protocols/values.py", "required"),
            Home("_runtime/protocols/values.py", "accessed"),
            Home("_runtime/protocols/values.py", "unfit"),
        ),
        core=(
            "`_runtime/protocols/values.py`: `selected`, `read_selector`, `required`, `accessed`, `unfit` (copied with "
            "every helper that reads responses)"
        ),
        adapters=(
            "the sites that read them: `_runtime/protocols/pagination.py`: `_Walk.build`, `_selected`, `_ended` (a "
            "missing or null continuation may end the traversal), `_runtime/protocols/polling.py`: "
            "`_Operation._created`, `_Operation._succeeded`; `_runtime/protocols/streams.py:_Events._advance` (a "
            "missing or null cursor is kept or cleared by its plan's rows), `_runtime/protocols/webhook_events.py`"
        ),
        sites=(
            Home("_runtime/protocols/pagination.py", "_Walk.build"),
            Home("_runtime/protocols/pagination.py", "_selected"),
            Home("_runtime/protocols/pagination.py", "_ended"),
            Home("_runtime/protocols/polling.py", "_Operation._created"),
            Home("_runtime/protocols/polling.py", "_Operation._succeeded"),
            Home("_runtime/protocols/streams.py", "_Events._advance"),
            Home("_runtime/protocols/webhook_events.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether a protocol helper continues from a checkpoint, so that every resumable helper refuses a state "
            "alike: its state as a plain JSON copy that the helper's decoder reads (`decoded_state`: a state that is "
            "not JSON, or one that does not fit the helper, refused as the state's invalid value), then the server's "
            "expiry it saved not past by the client's wall clock (`unexpired`: refused as expired), in that order "
            "(`restored`)"
        ),
        homes=(Home("_runtime/protocols/resume.py", "restored"),),
        core="`_runtime/protocols/resume.py:restored` (copied with every polling, stream and upload helper)",
        adapters=(
            "the helpers that restore one: `_runtime/protocols/polling.py:_restored` (the handle restores itself and "
            "keeps the expiry), `_runtime/protocols/streams.py:_restored`, "
            "`_runtime/protocols/uploads.py:_resume_start`; a pager's checkpoint is its server continuation, kept as "
            "the caller gives it (`_runtime/protocols/pagination.py:_restored`)"
        ),
        sites=(
            Home("_runtime/protocols/polling.py", "_restored"),
            Home("_runtime/protocols/streams.py", "_restored"),
            Home("_runtime/protocols/uploads.py", "_resume_start"),
            Home("_runtime/protocols/pagination.py", "_restored"),
        ),
    ),
    DecisionHome(
        decision=(
            "the bytes the client runtime moves a body in and keeps of one, so that every read, send and download "
            "takes the same slices and every error the same prefix: the 64 KiB slice a body is read, sent, compressed "
            "and iterated in (`CHUNK`, `pieces`; a compressor is fed through `_fed`, which calls `check` before each "
            "slice where an attempt gives one), the 64 KiB an error keeps of a received body (`MAX_ERROR_BODY_BYTES`, "
            "also the helpers' `MAX_RAW_PREFIX`; `bounded_body` cuts a body to it, or to a WebSocket's lower message "
            "limit, and tells whether it cut), and how a body written to a path becomes the file under its name or "
            "leaves none (`_created`, `_committed`, `_discarded`) and how a failed write ends the stream it started "
            "(`_Raw._write_failed`)"
        ),
        homes=(
            Home("_runtime/client/content.py", "CHUNK"),
            Home("_runtime/client/content.py", "pieces"),
            Home("_runtime/client/errors.py", "MAX_ERROR_BODY_BYTES"),
            Home("_runtime/client/errors.py", "bounded_body"),
            Home("_runtime/client/compression.py", "_fed"),
            Home("_runtime/client/raw.py", "_created"),
            Home("_runtime/client/raw.py", "_committed"),
            Home("_runtime/client/raw.py", "_discarded"),
            Home("_runtime/client/raw.py", "_Raw._write_failed"),
        ),
        core=(
            "`_runtime/client/content.py`: `CHUNK`, `pieces`; `_runtime/client/errors.py`: `MAX_ERROR_BODY_BYTES`, "
            "`bounded_body`; `_runtime/client/compression.py:_fed`; `_runtime/client/raw.py`: `_created`, "
            "`_committed`, `_discarded`, `_Raw._write_failed` (copied into every client package; compression with a "
            "declared coding)"
        ),
        adapters=(
            "the sites that read them: `_runtime/client/raw.py`: `RawResponse.stream_to`, `RawResponse._write`, "
            "`AsyncRawResponse.stream_to`, `AsyncRawResponse._download`, the `_iterate` of both responses, "
            "`ReceivedBody.add`, `_Raw._saved_failure`; `_runtime/client/compression.py`: `_gzipped`, "
            "`gzipped_attempt`, `_AsyncGzipAttempt.aiter_bytes`; `_runtime/client/bodies.py:BinarySource.chunks`; "
            "`_runtime/protocols/streams.py:_Events._decode_error`, "
            "`_runtime/protocols/websocket.py:_Sockets._decode_error` (through `bounded_body`), "
            "`_runtime/protocols/errors.py:MAX_RAW_PREFIX` (a re-export); `_runtime/protocols/uploads.py:_Content.read`"
        ),
        sites=(
            Home("_runtime/client/raw.py", "RawResponse.stream_to"),
            Home("_runtime/client/raw.py", "RawResponse._write"),
            Home("_runtime/client/raw.py", "AsyncRawResponse.stream_to"),
            Home("_runtime/client/raw.py", "AsyncRawResponse._download"),
            Home("_runtime/client/raw.py", "AsyncRawResponse._iterate"),
            Home("_runtime/client/raw.py", "ReceivedBody.add"),
            Home("_runtime/client/raw.py", "_Raw._saved_failure"),
            Home("_runtime/client/compression.py", "_gzipped"),
            Home("_runtime/client/compression.py", "gzipped_attempt"),
            Home("_runtime/client/compression.py", "_AsyncGzipAttempt.aiter_bytes"),
            Home("_runtime/client/bodies.py", "BinarySource.chunks"),
            Home("_runtime/protocols/streams.py", "_Events._decode_error"),
            Home("_runtime/protocols/websocket.py", "_Sockets._decode_error"),
            Home("_runtime/protocols/uploads.py", "_Content.read"),
        ),
    ),
    DecisionHome(
        decision=(
            "which native failure proves that its request was never sent, so that a send counts as unsent exactly when "
            "a retry may send it again: a failure to connect or to take a connection from the pool (ConnectTimeout, "
            "PoolTimeout, ConnectError), with its retry reason"
        ),
        homes=(Home("_runtime/client/native.py", "_unsent"),),
        core="`_runtime/client/native.py:_unsent` (copied into every client package)",
        adapters=(
            "`_runtime/client/native.py`: `delivery` (the send it leaves NOT_SENT), `transport_retry_reason` (the "
            "reason of an `APIConnectionError`'s cause)"
        ),
        sites=(
            Home("_runtime/client/native.py", "delivery"),
            Home("_runtime/client/native.py", "transport_retry_reason"),
        ),
    ),
    DecisionHome(
        decision=(
            "the timeouts a client call runs within, so that every call, helper session and native client bounds its "
            "phases and deadlines alike: phase timeouts that give every phase one value (`every_phase`: a number, None "
            "for no limit, a WebSocket handshake's cap), resolved phases as an HTTPX2 timeout (`native_phases`: the "
            "SDK-owned native client's from `DEFAULT_TIMEOUT`, a stream's with its idle timeout as the read phase), "
            "the deadline a total timeout sets from the instant a call or a helper session started (`total_deadline`), "
            "and the earliest of several deadlines, the first given of those that end together (`earliest`)"
        ),
        homes=(
            Home("_runtime/client/timing.py", "every_phase"),
            Home("_runtime/client/timing.py", "total_deadline"),
            Home("_runtime/client/timing.py", "earliest"),
            Home("_runtime/client/native.py", "native_phases"),
        ),
        core=(
            "`_runtime/client/timing.py`: `every_phase`, `total_deadline`, `earliest`; "
            "`_runtime/client/native.py:native_phases` (copied into every client package)"
        ),
        adapters=(
            "the sites that read them: `_runtime/client/options.py:phases` and `_UNLIMITED`, "
            "`_runtime/client/native.py`: `native_client`, `native_async_client`; `_runtime/client/logical.py`: "
            "`OperationSession.__init__`, `LogicalCallContext.__init__`; `_runtime/protocols/client.py`: "
            "`_SessionCall.__init__`, `_SocketCall.timeout`, `_SessionWait.__init__`; "
            "`_runtime/protocols/streams.py:_limits`, `_runtime/protocols/websocket.py:_Sockets._wait`"
        ),
        sites=(
            Home("_runtime/client/options.py", "phases"),
            Home("_runtime/client/native.py", "native_client"),
            Home("_runtime/client/native.py", "native_async_client"),
            Home("_runtime/client/logical.py", "OperationSession.__init__"),
            Home("_runtime/client/logical.py", "LogicalCallContext.__init__"),
            Home("_runtime/protocols/client.py", "_SessionCall.__init__"),
            Home("_runtime/protocols/client.py", "_SocketCall.timeout"),
            Home("_runtime/protocols/client.py", "_SessionWait.__init__"),
            Home("_runtime/protocols/streams.py", "_limits"),
            Home("_runtime/protocols/websocket.py", "_Sockets._wait"),
        ),
    ),
    DecisionHome(
        decision=(
            "the call layers of a protocol helper, so that every helper merges its limits and refuses a patch alike: "
            "the layers each limit of a call is merged from (`helper_layers`: the call's helper options, then its "
            "request options, refused when of another type; then the call's helper options before the client's helper "
            "defaults of the helper), and the refusal of a call that fixes an idempotency key of the helper's "
            "operations or sends a header or query name of a field the helper writes (`refuse_patched`, by the "
            "`written_fields` of the helper's writes: among the call's own extra ones, then the client's and its "
            "views' default ones; a pager's count position, which the defaults may set for the first request, among "
            "the call's own ones only)"
        ),
        homes=(
            Home("_runtime/protocols/client.py", "_ProtocolCore"),
            Home("_runtime/protocols/client.py", "_ProtocolCore.helper_layers"),
            Home("_runtime/protocols/client.py", "_ProtocolCore.refuse_patched"),
            Home("_runtime/protocols/client.py", "_ProtocolCore.fixed_key"),
            Home("_runtime/protocols/client.py", "_ProtocolCore.named"),
        ),
        core=(
            "`_runtime/protocols/client.py:_ProtocolCore`: `helper_layers`, `refuse_patched`, `fixed_key`, `named` "
            "(copied with any helper)"
        ),
        adapters=(
            "the `_limits` of `_runtime/protocols/`: `pagination.py`, `polling.py`, `uploads.py`, `streams.py` (with "
            "`_unpatched`), `websocket.py`, `cache.py`"
        ),
        sites=(
            Home("_runtime/protocols/websocket.py", "_limits"),
            Home("_runtime/protocols/cache.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the values the client runtime takes from an application, so that every option, record and saved value "
            "refuses the same ones: a timezone-aware datetime (`is_aware`: a datetime whose offset from UTC is not "
            "None; a token set's expiry, a webhook's now, a cache entry's times, a saved server expiry read back), and "
            "an option of its declared types or, where a callable may stand in for it, one that can be called "
            "(`checked_instance` with `callables`: a scheme credential, an OAuth client secret and refresh callback, a "
            "clock source; each site keeps its own field path), and a duration in seconds (`usable_seconds`: a number, "
            "no boolean, finite as a float and not negative, zero only where allowed; row "
            "`legacy-compare-before-float`; each site raises its own error)"
        ),
        homes=(
            Home("_runtime/client/timing.py", "is_aware"),
            Home("_runtime/client/timing.py", "checked_instance"),
            Home("_runtime/client/timing.py", "usable_seconds"),
        ),
        core=(
            "`_runtime/client/timing.py`: `is_aware`, `checked_instance`, `usable_seconds` (copied into every client "
            "package)"
        ),
        adapters=(
            "the sites that check them: `_runtime/client/timing.py`: `seconds`, `checked_seconds`; "
            "`_runtime/client/options.py:RetryOptions.__post_init__`; `_runtime/client/oauth.py`: `_aware`, "
            "`_settings`, `_refresh_settings`, `_expiry`; `_runtime/client/auth.py:SchemeCredentials.__init__`; "
            "`_runtime/client/timing.py:Clock.__post_init__`; `_runtime/protocols/webhook_events.py:local_arguments`; "
            "`_runtime/protocols/caches.py:CacheEntry.__post_init__`; `_runtime/protocols/resume.py:state_expiry`"
        ),
        sites=(
            Home("_runtime/client/timing.py", "seconds"),
            Home("_runtime/client/timing.py", "checked_seconds"),
            Home("_runtime/client/options.py", "RetryOptions.__post_init__"),
            Home("_runtime/client/oauth.py", "_aware"),
            Home("_runtime/client/oauth.py", "_settings"),
            Home("_runtime/client/oauth.py", "_refresh_settings"),
            Home("_runtime/client/oauth.py", "_expiry"),
            Home("_runtime/client/auth.py", "SchemeCredentials.__init__"),
            Home("_runtime/client/timing.py", "Clock.__post_init__"),
            Home("_runtime/protocols/webhook_events.py", "local_arguments"),
            Home("_runtime/protocols/caches.py", "CacheEntry.__post_init__"),
            Home("_runtime/protocols/resume.py", "state_expiry"),
        ),
    ),
    DecisionHome(
        decision=(
            "which config fields hold paths and JSON and what each stage of a run does with them: every path field is "
            "read relative to the pyproject.toml directory when it comes from that file (`~` expanded, and inline JSON "
            "of a JSON field is a value, not a file, unless a file of that name exists, which the JSON options read "
            "first and `--watch` watches: a failed plan's recovery reads each raw value the same way), the command "
            "line expands `~` and resolves the ones `cli_resolved` marks, `generate()` expands `~` in the paths a run "
            "writes (`OUTPUT_PATH_FIELDS`: the model output, the model metadata and a target's package) and makes the "
            "ones `caller_relative` marks absolute against the caller's working directory (`http_local_ref_path` "
            "against the output's directory), and `--watch` watches each as the input, a file, a directory or an "
            "output; a target's JSON fields are the JSON fields with the prefix of its settings; the default encoding "
            "(also of the JSON files option values name) and the default watch delay"
        ),
        homes=(
            Home("_config_fields.py", "PATH_FIELDS"),
            Home("_config_fields.py", "TARGET_SELECTORS"),
            Home("_config_fields.py", "names_path"),
            Home("_config_fields.py", "names_file"),
            Home("_config_fields.py", "json_file"),
            Home("_config_fields.py", "option_path"),
            Home("_config_fields.py", "DEFAULT_ENCODING"),
            Home("_config_fields.py", "DEFAULT_WATCH_DELAY"),
        ),
        core=(
            "`_config_fields.py`: `PATH_FIELDS`, `TARGET_SELECTORS` and the views of them, `names_path`, `names_file`, "
            "`json_file`, `option_path` (`~` expanded, a `~user` that names no user refused as an invalid value of the "
            "option), `DEFAULT_ENCODING`, `DEFAULT_WATCH_DELAY`"
        ),
        adapters=(
            "`_project_config.py`: `_resolve_pyproject_relative_paths`, `_resolve_relative_paths_from`, "
            "`load_pyproject_config`; `_cli_config.py:Config.validate_path`; "
            "`config.py:GenerateConfig.expand_output_paths`; `_generation.py:_prepare_generation_config`; "
            "`watch_dependencies.py`: `WatchDependencies._add_config`, `_configured_path`; `__main__.py`: "
            "`_record_raw_job_config_dependencies`, `_target_usage_errors`; `base_config.py:_selected_target`; the "
            "defaults: `_option_table.py` (the `--encoding` and `--watch-delay` help), `arguments.py` "
            "(`DEFAULT_ENCODING`), `json_config.py:document_base` and `_read_json_or_inline` (`DEFAULT_ENCODING`, "
            "`json_file`), "
            "`base_config.py:BaseGenerateConfig.encoding`, `config.py:ParserConfig.encoding`, "
            "`_parser_context.py:ParserSourceContext.encoding`, `_cli_config.py:Config.watch_delay`, "
            "`__main__.py:_batch_outer_settings` (the `Config` field defaults); left out: the `encoding` parameter "
            "defaults of functions, which every caller in the package passes (`http.py`, `format.py:CodeFormatter`, "
            "`parser/protobuf.py`, `parser/xmlschema.py`), `_generation.py:_prepare_atomic_generation_remote_lock` "
            "(the "
            "model outputs the lock is checked against) and `_target_selection.py` (the settings file and "
            "`http_local_ref_path` of a target render)"
        ),
        sites=(
            Home("_project_config.py", "_resolve_pyproject_relative_paths"),
            Home("_project_config.py", "_resolve_relative_paths_from"),
            Home("_project_config.py", "load_pyproject_config"),
            Home("config.py", "GenerateConfig.expand_output_paths"),
            Home("_generation.py", "_prepare_generation_config"),
            Home("watch_dependencies.py", "WatchDependencies._add_config"),
            Home("watch_dependencies.py", "_configured_path"),
            Home("__main__.py", "_record_raw_job_config_dependencies"),
            Home("__main__.py", "_target_usage_errors"),
            Home("base_config.py", "_selected_target"),
            Home("__main__.py", "_batch_outer_settings"),
            Home("http.py", "_FetchSettings.encoding"),
            Home("format.py", "CodeFormatter"),
            Home("_generation.py", "_prepare_atomic_generation_remote_lock"),
            Home("_option_table.py"),
            Home("json_config.py", "document_base"),
            Home("json_config.py", "_read_json_or_inline"),
            Home("parser/protobuf.py"),
            Home("parser/xmlschema.py"),
            Home("_target_selection.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether a profile selection names a profile: an empty name selects none, as no name does, for "
            "`--profile`, the `profile` of `load_pyproject_config()` and of a job, and each parent of `extends`; a run "
            "that selects none reads, watches, recommends for and names the base table"
        ),
        homes=(Home("_project_config.py", "names_profile"),),
        core="`_project_config.py:names_profile`",
        adapters=(
            "`_project_config.py`: `_resolve_profile_extends`, `_get_pyproject_toml_config_with_path`; `__main__.py`: "
            "`_get_job_config`, `_record_raw_profile_dependencies`, `_plan_jobs_unchecked`, `_run_config` (the "
            "selector and table of the recommendation); `_target_cli.py:_implied_notice`"
        ),
        sites=(
            Home("_project_config.py", "_resolve_profile_extends"),
            Home("_project_config.py", "_get_pyproject_toml_config_with_path"),
            Home("__main__.py", "_get_job_config"),
            Home("__main__.py", "_record_raw_profile_dependencies"),
            Home("__main__.py", "_plan_jobs_unchecked"),
            Home("__main__.py", "_run_config"),
            Home("_target_cli.py", "_implied_notice"),
        ),
    ),
    DecisionHome(
        decision=(
            "which option an enabled option turns on, over any value given for that option (`--use-annotated` turns on "
            "`--field-constraints`, `--use-type-alias-type` turns on `--use-type-alias`, "
            "`--intersect-combined-schema-keywords` turns on `--intersect-allof-keywords`), at every stage that "
            "applies option values, and which option of the table turns on each implied option that is on, for the "
            "warning where it was turned off"
        ),
        homes=(
            Home("_option_rules.py", "IMPLIED_OPTIONS"),
            Home("_option_rules.py", "implied_options"),
            Home("_option_rules.py", "implied_option_sources"),
            Home("_option_rules.py", "enabled_implied_options"),
            Home("_option_rules.py", "enable_implied_options"),
        ),
        core=(
            "`_option_rules.py`: `IMPLIED_OPTIONS`, `implied_options`, `implied_option_sources`, "
            "`enabled_implied_options`, `enable_implied_options`"
        ),
        adapters=(
            "`_cli_config.py`: `_prepare_cli_config_args` (the command-line values, as merging copies only the given "
            "fields), `Config.validate_root` (each of the two validators applies one row, row `validator-stage-rows`: "
            "the base validator cannot apply the rows whose source options a frozen `PresetConfig` sets); "
            "`base_config.py`: `BaseGenerateConfig.normalize_use_type_alias_type`, `implied_model_settings` (every row "
            "over every source option of the config, beside its own rules: an unset `field_constraints` follows "
            "`use_annotated`, and msgspec turns an unset `use_annotated` on); `preset.py:ResolvedPresetConfig.updates` "
            "(every row over the updates it makes, `use_annotated` read as `force_field_constraints`); "
            "`model/__init__.py:get_data_model_types` (the `use_type_alias_type` row, through `implied_options`); "
            "`_option_stages.py:_replaced_options` (every row, through `enabled_implied_options`, for the warning and "
            "for "
            "`_implied_model_updates` under `--read-implied-options-from-merged-values`); "
            "`cli_options.py:_documented_options` (the `implies` relations of `CLI_OPTION_META`)"
        ),
        sites=(
            Home("_cli_config.py", "_prepare_cli_config_args"),
            Home("preset.py", "PresetConfig"),
            Home("base_config.py", "BaseGenerateConfig.normalize_use_type_alias_type"),
            Home("base_config.py", "implied_model_settings"),
            Home("preset.py", "ResolvedPresetConfig.updates"),
            Home("model/__init__.py", "get_data_model_types"),
            Home("_option_stages.py", "_replaced_options"),
            Home("_option_stages.py", "_implied_model_updates"),
            Home("cli_options.py", "_documented_options"),
        ),
    ),
    DecisionHome(
        decision=(
            "which options a run refuses or warns of together (an option that requires another, or a value of it, and "
            "two options that conflict), with which text and action (the package's `Error`, a `ValueError` that "
            "pydantic reports as an invalid configuration, a printed refusal, a printed warning), at which stage, in "
            "which order, and what the CLI reference documents for the same pair: one row per documented relation, the "
            "check on one of them; the stages see different values, so a row never changes stage: `base-validator` "
            "(every config as it is validated: on the command line the pyproject.toml values alone, then the arguments "
            "alone, never the merge), `cli-validator` (the command line's `Config` with the same two inputs, and the "
            "table `load_pyproject_config` reads), `cli-after-preset` (the command line's merged values after its "
            "preset and implied settings), `run-options` (a command's resolved run with its record; the "
            "`--use-specialized-enum` row reads the value given, row `specialized-enum-by-value`), `sentinel` (the "
            "stage of `generate()` that gives `--use-missing-sentinel` its target pydantic version, which every "
            "command runs too) and `parser-options` (`generate()` as it prepares the options of its parsers); "
            "`generate()` checks `base-validator`, `sentinel` and `parser-options` alone (row `cli-only-refusals`); "
            "the options and the input kinds a server or client target requires, which the CLI reference states for "
            "the option that selects it"
        ),
        homes=(
            Home("_option_rules.py", "OPTION_RELATIONS"),
            Home("_option_rules.py", "check_option_relations"),
            Home("_option_rules.py", "check_option_relation"),
            Home("_option_rules.py", "TARGET_REQUIRED_OPTIONS"),
            Home("_option_rules.py", "TARGET_INPUTS"),
        ),
        core=(
            "`_option_rules.py`: `OPTION_RELATIONS`, `check_option_relations`, `check_option_relation`, "
            "`TARGET_REQUIRED_OPTIONS`, `TARGET_INPUTS`"
        ),
        adapters=(
            "`base_config.py`: `BaseGenerateConfig.validate_remote_lock_mode`, `validate_target_selection` (each its "
            "row, through `check_option_relation`); `_cli_config.py:Config.validate_cli_option_relations` (after the "
            "`--output-datetime-class` and `--alias-generator` checks); `__main__.py`: `_resolve_cli_config` (after "
            "`implied-model`), `_apply_model_run_options` (after the black refusal and the deprecation of the "
            "`--use-annotated` default); `_option_stages.py:_missing_sentinel_updates`; "
            "`_generation.py:_prepare_parser_common_options`; "
            "`cli_options.py`: `_with_relation_rows` (the `requires` and `conflicts` of `CLI_OPTION_META`), "
            "`_documented_options` (the `requires` of `--generate-server` and `--generate-client`); "
            "`base_config.py:_target_options_error` (the refusal of a target's options); left out: `__main__.py`: "
            "`_diff_against_validation_error`, `_reject_batch_input_diff` (the `--diff-against` family), "
            "`_generation_option_errors`, `_target_usage_errors` and the `--input-model` refusal of `_run_config`, the "
            "asyncapi refusals of `base_config.py:_target_options_error`, the argparse exclusive groups, "
            "`_option_rules.py`: `_validate_output_datetime_class`, `_validate_alias_generator` (each with its own "
            "relation "
            "in `CLI_OPTION_META` or none)"
        ),
        sites=(
            Home("base_config.py", "BaseGenerateConfig.validate_remote_lock_mode"),
            Home("base_config.py", "BaseGenerateConfig.validate_target_selection"),
            Home("__main__.py", "_resolve_cli_config"),
            Home("__main__.py", "_apply_model_run_options"),
            Home("_option_stages.py", "_missing_sentinel_updates"),
            Home("_generation.py", "_prepare_parser_common_options"),
            Home("cli_options.py", "_with_relation_rows"),
            Home("cli_options.py", "_documented_options"),
            Home("base_config.py", "_target_options_error"),
            Home("__main__.py", "_diff_against_validation_error"),
            Home("__main__.py", "_reject_batch_input_diff"),
            Home("__main__.py", "_generation_option_errors"),
            Home("__main__.py", "_target_usage_errors"),
            Home("__main__.py", "_run_config"),
            Home("_option_rules.py", "_validate_output_datetime_class"),
            Home("_option_rules.py", "_validate_alias_generator"),
        ),
    ),
    DecisionHome(
        decision=(
            "which values the options of a run take before the parser exists, and what a user sees first where a value "
            "does not fit: the stages `preset` (the values a preset gives the fields that are not explicit to it; one "
            "that does not fit the target Python version is the package's `Error`), `implied-target` (the settings a "
            "server or client target needs that neither a source nor an earlier stage gave a value, the preset "
            "included), `sentinel` (the target pydantic version of `--use-missing-sentinel`) and `implied-model` (the "
            "constraint settings the other settings imply) run in that one order, each on the config the stages before "
            "it left; an entry point brings the record of where its values came from (`RunOptions`), names the stages "
            "it runs and where its own checks sit, and the stages read the record, never who calls: the implied model "
            "settings read the given fields as they were before the preset (row `implied-reads-given-before-preset`); "
            "the values of a stage that differ from the config's are applied to a copy, and the record that comes back "
            "names every field a stage gave a value, so nothing reads the fields a config has set after it came in; a "
            "call that ran `implied-model` ends, after the entry point's checks, with a `UserWarning` for each option "
            "that a source turned off (not a pyproject.toml value of an option that the `preset` stage of the call "
            "gave a value under a preset of an argument, which replaces it, row `preset-over-pyproject`) and that is "
            "on: where an enabled option turns it on, naming that option, where it came from (the preset that set it, "
            "the output model type it was set for, or that it was given as well) and how to turn both off (its "
            "argument, or its key where pyproject.toml gave it and no argument did); where the option that turns it on "
            "is off, as the command line's validator applied the row to the pyproject.toml values before an argument "
            "turned that option off (row `validator-stage-rows`), naming the key, the argument and how it is off (the "
            "key, or `--read-implied-options-from-merged-values`, under which `implied-model` gives it False, as the "
            "merged values say): a run shows it once, as a later resolution reads the resolved value as the given one "
            "and a call of other stages alone reports nothing"
        ),
        homes=(
            Home("_option_stages.py", "_run_option_stages"),
            Home("_option_stages.py", "_OPTION_STAGES"),
            Home("_option_stages.py", "_replaced_options"),
            Home("_option_stages.py", "_warn_of_replaced_options"),
            Home("_option_stages.py", "_resolve_preset"),
            Home("_shared_types.py", "Error"),
        ),
        core=(
            "`_option_stages.py`: `_run_option_stages` (the stages: `_OPTION_STAGES`), `_replaced_options`, "
            "`_warn_of_replaced_options`, `_resolve_preset` (the resolution and its `Error`, "
            "`_shared_types.py`: `Error`)"
        ),
        adapters=(
            "the entry points: `__main__.py:_resolve_cli_config` (a command and each batch job: the record of the "
            "arguments and the pyproject.toml values as they are before validation, then `preset`, `implied-model` and "
            "the relation rows of `cli-after-preset`, which only the merged values decide, with the warnings of the "
            "stages ignored under `--disable-warnings`, whose filter the run installs later; the config goes to "
            "`generate()` without a preset, which runs `sentinel` after the command's own refusals, row "
            "`cli-sentinel-in-generate`) and `__main__.py:_imply_target_settings` (`implied-target` with the record "
            "the config carries, where a target is selected: a run after its option checks, `--recommend-options` "
            "after the target's option check); `_project_config.py:load_pyproject_config` (the record of the "
            "pyproject.toml keys it keeps and its overrides, then `implied-model` alone: every setting the stage gave "
            "counts as set on the config it returns, which keeps its `preset` for `generate()` to resolve, row "
            "`loader-applies-no-preset`); `_option_stages.py:_resolve_run_options` (a config that brings no record: "
            "the "
            "fields it has set are the given ones, with the values they have there, row `given-is-fields-set`; a "
            "config `load_pyproject_config()` returned gives no values, as the loader marks every field its stage gave "
            "as set, so what a preset of its table replaces is not reported) for "
            "`_generation.py:_prepare_generate_facade_config` (`generate()`, a trial of the recommender and the "
            "metadata "
            "run of dynamic generation: the facade's two warnings, then `preset`, `sentinel`, `implied-model`, then "
            "the metadata path and the output encoding), `_api_generation.py:prepare_target` (a target through "
            "`generate()` and through the command line: the model output, then every stage, with the Pydantic target "
            "of a sentinel and then the facade's two warnings after `implied-target`, then the metadata path and the "
            "output encoding, then the target's checks, which read the resolved settings) and "
            "`dynamic.py:_without_default_formatting` (`preset` alone, for the formatters a preset selects, so no "
            "later refusal comes before the facade's warnings); `_recommend/__init__.py:_plan` (`_resolve_preset` once "
            "for each preset it compares; the plan keeps the config updates it makes for the trial)"
        ),
        sites=(
            Home("__main__.py", "_resolve_cli_config"),
            Home("__main__.py", "_imply_target_settings"),
            Home("_project_config.py", "load_pyproject_config"),
            Home("_option_stages.py", "_resolve_run_options"),
            Home("_generation.py", "_prepare_generate_facade_config"),
            Home("_api_generation.py", "prepare_target"),
            Home("dynamic.py", "_without_default_formatting"),
            Home("_recommend/__init__.py", "_plan"),
            Home("_option_stages.py", "_resolve_preset"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether an option of a run was given, by whom and with which value, and which fields an option stage gave "
            "a value, so that every reader gets one answer: the given fields (the keys of the arguments and of the "
            "pyproject.toml values with profile and job merged, or the fields a `generate()` config has set; never a "
            "field that only a preset or an implied setting set), the fields explicit to a preset (the arguments alone "
            "when an argument selects the preset, which then replaces the pyproject.toml values: row "
            "`preset-over-pyproject`), the value a field was given (an argument's over the pyproject.toml one, or the "
            "value a set field of a `generate()` config had when its run started), the merged values, and the fields "
            "with a value from a source or from a stage; the record is built before any value is validated, is never "
            "written, and is shared by the copies of the config that carries it"
        ),
        homes=(
            Home("_run_record.py", "RunOptions"),
            Home("_run_record.py", "RunOptions.given"),
            Home("_run_record.py", "RunOptions.preset_explicit"),
            Home("_run_record.py", "RunOptions.set_fields"),
            Home("_run_record.py", "RunOptions.merged"),
            Home("_run_record.py", "RunOptions.given_value"),
            Home("_run_record.py", "RunOptions.staged_by"),
            Home("_run_record.py", "RunOptions.after"),
        ),
        core=(
            "`_run_record.py:RunOptions` (`given`, `preset_explicit`, `set_fields`, `merged`, `given_value`, "
            "`staged_by`, `after`)"
        ),
        adapters=(
            "`_option_stages.py`: `_run_option_stages` and the stages of `_OPTION_STAGES`, `_replaced_options` (the "
            "value an "
            "option was turned off with), `_warn_of_replaced_options` (the stage or the source that gave the option "
            "that turns it on), `_resolve_run_options` (the record of a config); `_cli_config.py:Config._run_options` "
            "(the record a command-line config carries); `__main__.py`: `_main` (the record of a command, and the "
            "merged values a watch is staged and configured with), `_rerun_copy` (the pyproject.toml values alone, "
            "which a watch is configured with again where a batch job or the transaction of a lock runs the resolved "
            "config once more, so a path an argument gives is then watched where pyproject.toml names it, row "
            "`lock-reentry-watch-values-pyproject-only`), `_get_job_config`, `_run_watched_jobs`, "
            "`_apply_model_run_options` (the deprecation of the `--use-annotated` default where the option is not "
            "given), `_collect_naming_hint`, `_target_usage_errors`, `_run_recommend_options`; `_target_cli.py`: "
            "`_implied` (the settings the target set, which its notice and both JSON payloads name), `_implied_notice` "
            "(a target an argument selected), `_base` (the JSON file an option was given as, and whether an argument "
            "gave it); `_api_generation.py:implied_conflict` (the sentinel the target implied); "
            "`_option_rules.py:OPTION_RELATIONS` (the `--use-specialized-enum` refusal by the value given, not by "
            "whether one was given, row `specialized-enum-by-value`); `_project_config.py:load_pyproject_config`; left "
            "out: `_generation.py:_parse_generation_run` (the Pydantic floor warning names `--use-missing-sentinel` as "
            "the source of a 2.12 target from the values, also where an option gave the target, row "
            "`floor-target-from-by-value`), `_cli_config.py:Config.merge_args` and `__main__.py`: "
            "`_plan_jobs_unchecked` and `_batch_outer_settings`, `_cli_messages.py:_recommend_options_conflict` and "
            "`_config_export.py:_cli_config_values`, "
            "with `prompt.py:_current_options` (the arguments of a command, read from the namespace)"
        ),
        sites=(
            Home("_option_stages.py", "_run_option_stages"),
            Home("_option_stages.py", "_replaced_options"),
            Home("_option_stages.py", "_warn_of_replaced_options"),
            Home("_option_stages.py", "_resolve_run_options"),
            Home("__main__.py", "_main"),
            Home("__main__.py", "_rerun_copy"),
            Home("__main__.py", "_get_job_config"),
            Home("__main__.py", "_run_watched_jobs"),
            Home("__main__.py", "_apply_model_run_options"),
            Home("__main__.py", "_collect_naming_hint"),
            Home("__main__.py", "_target_usage_errors"),
            Home("__main__.py", "_run_recommend_options"),
            Home("_target_cli.py", "_implied"),
            Home("_target_cli.py", "_implied_notice"),
            Home("_target_cli.py", "_base"),
            Home("_api_generation.py", "implied_conflict"),
            Home("_project_config.py", "load_pyproject_config"),
            Home("_generation.py", "_parse_generation_run"),
            Home("__main__.py", "_plan_jobs_unchecked"),
            Home("_cli_messages.py", "_recommend_options_conflict"),
            Home("__main__.py", "_batch_outer_settings"),
            Home("_config_export.py", "_cli_config_values"),
            Home("prompt.py", "_current_options"),
        ),
    ),
    DecisionHome(
        decision=(
            "which output model types are Pydantic's (`pydantic_v2.BaseModel`, `pydantic_v2.dataclass`): the outputs "
            "that take the MISSING sentinel, that warn of the coming `--use-annotated` default, for which a target "
            "that uses the sentinel implies Pydantic 2.14, that write a type alias as `TypeAliasType` before Python "
            "3.12, that take the Pydantic group of a preset, whose target packages depend on Pydantic, and the "
            "Pydantic family of `--input-model-ref-strategy reuse-foreign` (also for no output type); and the "
            "arguments a dataclass decorator takes, the keys of `DataclassArguments`"
        ),
        homes=(
            Home("enums.py", "_PYDANTIC_MODEL_TYPES"),
            Home("enums.py", "DataclassArguments"),
        ),
        core="`enums.py`: `_PYDANTIC_MODEL_TYPES`, `DataclassArguments`",
        adapters=(
            "`enums.py:_get_output_model_family`; `_option_rules.py:OPTION_RELATIONS` (the `--use-missing-sentinel` "
            "row); `__main__.py:_apply_model_run_options`; `base_config.py:implied_target_settings`; "
            "`model/__init__.py:get_data_model_types`; `preset.py` (the Pydantic option group of `standard-20260619`); "
            "`_target_render.py:PYDANTIC_BACKENDS` (their values, read by `_target_render.py:pydantic_target_problem` "
            "and `_client/target.py:_backend_dependencies`); the dataclass arguments: "
            "`model/base.py:_safe_dataclass_arguments`, `_option_table.py:_dataclass_arguments`, "
            "`_target_binding.py:_DATACLASS_PARAMETERS`; left out: `_openapi_codec_plan.py:PydanticBackend`, a "
            "`Literal` type of the same values that `_fastapi/target.py:_BACKENDS` (the server's backends) maps them to"
        ),
        sites=(
            Home("enums.py", "_get_output_model_family"),
            Home("__main__.py", "_apply_model_run_options"),
            Home("base_config.py", "implied_target_settings"),
            Home("model/__init__.py", "get_data_model_types"),
            Home("_target_render.py", "pydantic_target_problem"),
            Home("_client/target.py", "_backend_dependencies"),
            Home("model/base.py", "_safe_dataclass_arguments"),
            Home("_option_table.py", "_dataclass_arguments"),
            Home("preset.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the choices of a command-line option that takes the values of an enum: the values of its members, in "
            "their order"
        ),
        homes=(Home("_option_table.py", "_values"),),
        core="`_option_table.py:_values`",
        adapters=(
            "the options of `_option_table.py` that take an enum's values (`--extra-fields` listed those of "
            "`ExtraFields` by hand); not routed: the options whose choices are no enum's (`--output-format`, "
            "`--install-skill`, `--skill-scope`, `--preset`, `--output-format-json-schema`, `--list-deprecations` and "
            "`--list-experimental`)"
        ),
        sites=(Home("_option_table.py"),),
    ),
    DecisionHome(
        decision=(
            "which command-line options are those of a command, whose value no config carries (`--help`, "
            "`--generate-prompt`, `--job`, ...), so that a config field and the option table cannot drift: the rows "
            "marked `config=False`, the others each setting the config field of its dest"
        ),
        homes=(Home("_option_table.py", "COMMAND_DESTS"),),
        core="`_option_table.py`: `Option.config`, `COMMAND_DESTS`",
        adapters=(
            "`__main__.py:EXCLUDED_CONFIG_OPTIONS` (the options `--generate-pyproject-config` leaves out: these and "
            "the config fields of a run mode), `prompt.py:PROMPT_EXCLUDED_OPTIONS` (these except the five that shape "
            "the run: `--profile`, `--job`, `--all-jobs`, `--ignore-pyproject`, `--fail-on-multi-module-stdout`), each "
            "listed them by hand; checked: "
            "`tests/cli_doc/test_cli_options_sync.py:test_option_table_matches_the_config_models` (every row against "
            "the CLI `Config` and `GenerateConfig` fields, with the fields no option sets)"
        ),
        sites=(),
    ),
    DecisionHome(
        decision=(
            "which command-line options are deprecated, and the message each one's help (`Deprecated: <message>`) and "
            "documentation row (`CLIOptionMeta.deprecated`, `deprecated_message`) give: the `cli-option` entries of "
            "the deprecation registry, by the flag each targets"
        ),
        homes=(Home("deprecations.py", "cli_deprecations"),),
        core="`deprecations.py:cli_deprecations`",
        adapters=(
            "`_option_table.py:deprecated` (the help of `--allow-extra-fields`, `--parent-scoped-naming`, "
            "`--set-default-enum-member` and `--validation`), `cli_options.py:_documented_options` (the documentation "
            "rows); not routed: the warnings an option emits when it is used, by registry id (`_cli_config.py`, "
            "`warn_deprecated`)"
        ),
        sites=(
            Home("_option_table.py", "deprecated"),
            Home("cli_options.py", "_documented_options"),
            Home("_cli_config.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the namespace a parse of the command line starts from: colors on, every other option unset, in the "
            "namespace the argument parser fills"
        ),
        homes=(Home("arguments.py", "_reset_namespace"),),
        core="`arguments.py:_reset_namespace`",
        adapters=(
            "`arguments.py` (the shared `namespace` when the module loads); `__main__.py`: the fast path of the prompt "
            "helper outputs and `_main`; `_agent_skill_cli.py:run_agent_skill_installer`"
        ),
        sites=(
            Home("__main__.py", "_main"),
            Home("_agent_skill_cli.py", "run_agent_skill_installer"),
            Home("arguments.py"),
        ),
    ),
    DecisionHome(
        decision="which command-line arguments set a config field to a value",
        homes=(Home("_option_names.py", "option_arguments"),),
        core="`_option_names.py:option_arguments`",
        adapters=(
            "`cli_options._negative_option`, the writers' spellings: `_config_export.py:generate_cli_command`, "
            "`_option_names.py:values_arguments`, `cli_options.py:_option_arguments`"
        ),
        sites=(
            Home("_config_export.py", "generate_cli_command"),
            Home("_option_names.py", "values_arguments"),
            Home("cli_options.py", "_option_arguments"),
        ),
    ),
    DecisionHome(
        decision=(
            "the values that preset config items set, keyed by field name, as pyproject.toml spells them (each item's "
            "spelling is `PresetConfigItem.pyproject_value`)"
        ),
        homes=(Home("preset.py", "_pyproject_values"),),
        core="`preset.py:_pyproject_values`",
        adapters=(
            "the readers of a preset's values: `_recommend/__init__.py:_plan`, `_recommend/_rules.py:_rule_values`, "
            "`preset.py:_copyable_config_data`; `scripts/recommender_calibrate.py`"
        ),
        sites=(
            Home("_recommend/__init__.py", "_plan"),
            Home("_recommend/_rules.py", "_rule_values"),
            Home("preset.py", "_copyable_config_data"),
        ),
    ),
    DecisionHome(
        decision=(
            "the command-line arguments that set several config fields to values: each field's arguments "
            "(`option_arguments`) in the given order, a value that no option can express left out"
        ),
        homes=(Home("_option_names.py", "values_arguments"),),
        core="`_option_names.py:values_arguments`",
        adapters=(
            "`__main__.py:_cli_arguments` (the command of a recommendation: sorted by field name, each argument as "
            "`str`), `cli_options.py:_values_arguments` (the recommender's options and trial candidates: "
            "`_recommend/__init__.py:_option`, `_recommend/_report.py:_candidate_payload`), "
            "`_target_cli.py:_implied_notice` (the command that sets the settings a target implied); not this "
            "decision: one value's arguments (`_config_export.py:generate_cli_command`, which writes a list as one "
            "argument, and whether an option expresses a value, `_recommend/__init__.py:_plan`)"
        ),
        sites=(
            Home("__main__.py", "_cli_arguments"),
            Home("cli_options.py", "_values_arguments"),
            Home("_recommend/__init__.py", "_option"),
            Home("_recommend/_report.py", "_candidate_payload"),
            Home("_target_cli.py", "_implied_notice"),
            Home("_config_export.py", "generate_cli_command"),
            Home("_recommend/__init__.py", "_plan"),
        ),
    ),
    DecisionHome(
        decision=(
            "the value a configuration holds for a field as pyproject.toml spells it, the spelling that rule values "
            "and configured values are compared in"
        ),
        homes=(Home("_option_names.py", "configured_value"),),
        core="`_option_names.py:configured_value`",
        adapters=(
            "`_recommend/__init__.py`: `_plan` (the baseline a preset leaves), `_disagreements`; "
            "`_recommend/_rules.py:_rule_values` (an optional rule's values)"
        ),
        sites=(
            Home("_recommend/__init__.py", "_plan"),
            Home("_recommend/__init__.py", "_disagreements"),
            Home("_recommend/_rules.py", "_rule_values"),
        ),
    ),
    DecisionHome(
        decision=(
            "the config values that recommended options set together: each option's values in order, a later option's "
            "value of a field replacing an earlier one's"
        ),
        homes=(Home("_recommend/__init__.py", "_merged_values"),),
        core="`_recommend/__init__.py:_merged_values`",
        adapters=(
            "`_recommend/__init__.py`: `_applied` (a candidate's generation config), `_trial_candidates` (the options "
            "a candidate adds), `_set_command` (the command and pyproject.toml keys of the recommendation)"
        ),
        sites=(
            Home("_recommend/__init__.py", "_applied"),
            Home("_recommend/__init__.py", "_trial_candidates"),
            Home("_recommend/__init__.py", "_set_command"),
        ),
    ),
    DecisionHome(
        decision=(
            "which classes of two generations of a trial are the same class: a key that one class holds on each side, "
            "the identity path of its model or the set of its field names (a class with fewer than two fields has "
            "none); a renamed pair is renumbered when the names differ in digits only"
        ),
        homes=(Home("_recommend/_metrics.py", "_match"),),
        core="`_recommend/_metrics.py:_match`",
        adapters=(
            "`_recommend/_metrics.py`: `location_changes` (by identity path, with the keys only one side holds), "
            "`name_stability` (by field names)"
        ),
        sites=(
            Home("_recommend/_metrics.py", "location_changes"),
            Home("_recommend/_metrics.py", "name_stability"),
        ),
    ),
    DecisionHome(
        decision=(
            "how the recommender names a trial candidate next to another configuration: that configuration's label "
            "(`current`, `preset`, `recommended` or the selected candidate), then `+` or `-` and the decision of the "
            "option the candidate adds or removes"
        ),
        homes=(Home("_recommend/__init__.py", "_label"),),
        core="`_recommend/__init__.py:_label`",
        adapters=(
            "`_recommend/__init__.py`: `_candidates` (the recommendation with or without one option), `_remeasure` "
            "(the selected candidate with or without one option)"
        ),
        sites=(
            Home("_recommend/__init__.py", "_candidates"),
            Home("_recommend/__init__.py", "_remeasure"),
        ),
    ),
    DecisionHome(
        decision=(
            "how the recommender fills a text with the signals of a schema: the counts and facts by name, an empty "
            "fact written as `none`"
        ),
        homes=(Home("_recommend/_rules.py", "format_text"),),
        core="`_recommend/_rules.py:format_text`",
        adapters=(
            "`_recommend/_report.py`: the rule texts of an option (reason, caveats) and the rows of the schema summary "
            "(`_signal_lines`); `_recommend/_report.py:_option_payload` (JSON)"
        ),
        sites=(
            Home("_recommend/_report.py", "_signal_lines"),
            Home("_recommend/_report.py", "_option_payload"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a text report of the command line wraps a paragraph: lines of at most 100 columns, never broken at a "
            "hyphen, the first line and the others each after its indent"
        ),
        homes=(Home("_message_text.py", "wrapped_lines"),),
        core="`_message_text.py:wrapped_lines`",
        adapters=(
            "`_recommend/_report.py` (the rule texts, trial effects and footnotes, the changes of a migration, the "
            "notes), `_registry_render.py:_render_registry_table` (the notes of `--list-experimental`)"
        ),
        sites=(
            Home("_registry_render.py", "_render_registry_table"),
            Home("_recommend/_report.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the JSON text of a machine-readable output of the command line and of the JSON Schemas of its outputs and "
            "config: indented by two spaces, in the style each output was released with, row `ordered` (key order and "
            "non-ASCII text kept: the generation, check, batch, command and recommendation payloads, model metadata), "
            "`sorted` (sorted keys: the prompt payload and its schema, the remote lock) or `sorted-ascii` (sorted "
            "keys, non-ASCII text escaped: the registry and preset listings, the config schema); a document whose "
            "array is not held in memory (the jobs of a batch run, the resources of the remote lock) is written item "
            "by item in the same bytes"
        ),
        homes=(
            Home("_json_output.py", "dump_json"),
            Home("_json_output.py", "schema_document"),
            Home("_json_output.py", "JSON_SCHEMA_DRAFT_2020_12"),
            Home("_json_output.py", "write_streamed_json"),
        ),
        core=(
            "`_json_output.py`: `dump_json`, `schema_document` (`JSON_SCHEMA_DRAFT_2020_12`, re-exported by "
            "`model_metadata.py`), `write_streamed_json`"
        ),
        adapters=(
            "none: the callers pass their row; `scripts/build_model_metadata_schema.py` builds the static model "
            "metadata schema through `schema_document`"
        ),
        sites=(),
    ),
    DecisionHome(
        decision=(
            "the keys every structured JSON output of the command line starts with, before its `kind`: `version` 1 and "
            "`format` `json`"
        ),
        homes=(
            Home("_json_output.py", "OUTPUT_HEAD"),
            Home("_json_output.py", "OutputHead"),
            Home("prompt.py"),
        ),
        core=(
            "`_json_output.py:OUTPUT_HEAD` (typed by `OutputHead`, which `_Envelope` of `prompt.py` declares as "
            "payload fields)"
        ),
        adapters=(
            "none: the payload constructions unpack it (`_structured_output.py`: `generation_output_json`, "
            "`command_output_json`, `check_output_json`; `prompt.py:_generate_prompt_json`; "
            "`_recommend/_report.py:recommendation_payload`), and `__main__.py:_write_batch_json_spool` writes the "
            "head of the batch document from it through `write_streamed_json`"
        ),
        sites=(
            Home("_structured_output.py", "generation_output_json"),
            Home("_structured_output.py", "command_output_json"),
            Home("_structured_output.py", "check_output_json"),
            Home("prompt.py", "_generate_prompt_json"),
            Home("_recommend/_report.py", "recommendation_payload"),
            Home("__main__.py", "_write_batch_json_spool"),
        ),
    ),
    DecisionHome(
        decision=(
            "the payload `--emit-model-metadata` writes: `version` 1, then the generated models (none for a run that "
            "collected no metadata)"
        ),
        homes=(
            Home("model_metadata.py", "model_metadata_payload"),
            Home("model_metadata.py", "ModelMetadata"),
        ),
        core="`model_metadata.py:model_metadata_payload` (typed by `ModelMetadata`)",
        adapters=(
            "none: `parser/base.py:Parser._build_model_metadata` and `model_metadata.py:dump_model_metadata` call it"
        ),
        sites=(
            Home("parser/base.py", "Parser._build_model_metadata"),
            Home("model_metadata.py", "dump_model_metadata"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a registry of the command line (deprecations, experimental features) is listed: its entries as JSON "
            "(`sorted-ascii`), a text table (each entry's wrapped note after it where the registry's page sets "
            "`table_notes`: experimental features) or a Markdown page with a summary table and the details of each "
            "entry, the format `--list-deprecations` / `--list-experimental` names, the JSON items of its "
            "`--output-format json` payload, and the release-note sections of a version (a heading and a bullet per "
            "entry, only when it has entries)"
        ),
        homes=(
            Home("_registry_render.py", "_RegistryPage"),
            Home("_registry_render.py", "_render_registry"),
            Home("_registry_render.py", "_render_registry_table"),
            Home("_registry_render.py", "_render_registry_markdown"),
            Home("_registry_render.py", "_render_registry_entries_json"),
            Home("_registry_render.py", "_render_release_notes"),
        ),
        core=(
            "`_registry_render.py`: `_RegistryPage`, `_render_registry`, `_render_registry_table`, "
            "`_render_registry_markdown`, `_render_registry_entries_json`, `_render_release_notes`"
        ),
        adapters=(
            "`deprecations.py` and `experimental.py` keep their data, sort order, cells and details and give their "
            "page; `__main__.py:_print_registry` (the `--list-*` output); `preset.py:render_presets_json` writes its "
            "own entries through `_render_registry_json`"
        ),
        sites=(
            Home("__main__.py", "_print_registry"),
            Home("preset.py", "render_presets_json"),
            Home("deprecations.py"),
            Home("experimental.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the JSON Schemas `--output-format-json-schema` prints: their names, in the order of the option's choices, "
            "the function that writes each, and where the command line prints it, row `argv` (before the argument "
            "parser loads, when the request is the whole command: `config`, `generation`, `model-metadata`, "
            "`structured-output`), `parsed` (once the parser has read the command, before `--version`: "
            "`generate-prompt`) or `main` (where the command handles `--version`: `recommend-options`); a parsed "
            "command with the options of `--install-skill` steps aside from the fast paths, so that every name refuses "
            "them without it"
        ),
        homes=(
            Home("_json_output.py", "JSON_SCHEMA_NAMES"),
            Home("_json_output.py", "json_schema_path"),
            Home("_json_output.py", "json_schema_text"),
        ),
        core="`_json_output.py`: `JSON_SCHEMA_NAMES`, `json_schema_path`, `json_schema_text`",
        adapters=(
            "`__main__.py` (the argv and parsed fast paths, `_skill_options_given`, `_command_output`), "
            "`_option_table.py` (the option's choices and metavar), `prompt.py:PROMPT_GUIDANCE_SECTIONS` (the list of "
            "names)"
        ),
        sites=(
            Home("__main__.py", "_skill_options_given"),
            Home("__main__.py", "_command_output"),
            Home("_option_table.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "targets: whether a type admits None and how None is stripped (the target half of the null row above; a "
            "target never inspects members for `NoneType`)"
        ),
        homes=(Home("model/_decisions/nullability.py", "written_annotation_null"),),
        core="`model/_decisions/nullability.py` (`written_annotation_null` for an annotation the document writes)",
        adapters=(
            "`_target_types.py` (`admits_none`, `present`, `without_none`, `TypeTables.takes_null` over the frozen "
            "views; `sole_present`, the one member of a union besides None, which "
            "`_target_types.py:TypeTables.unwrapped`, `_client/model_facts.py` (`ModelFacts.unwrapped`, "
            "`ModelFacts.model`, `ModelFacts.present`) and `_fastapi/plan.py` (`Planner.member`, `_constrained`) "
            "unwrap a type by, and `_target_binding.py` (`_SchemaUses.held`, the model a type holds, and "
            "`_UseFacts.holder`) read the model a type holds by), `_target_binding.py:_Binder.schema_null` (the null a "
            "merged schema's use keeps), `types.py:DataType.admits_none`, `types.py:reference_names_nullable`, "
            "`model/base.py:DataModelFieldBase._field_null_source`"
        ),
        sites=(
            Home("_target_types.py", "admits_none"),
            Home("_target_types.py", "present"),
            Home("_target_types.py", "without_none"),
            Home("_target_types.py", "TypeTables.takes_null"),
            Home("_target_types.py", "sole_present"),
            Home("_target_types.py", "TypeTables.unwrapped"),
            Home("_client/model_facts.py", "ModelFacts.unwrapped"),
            Home("_client/model_facts.py", "ModelFacts.model"),
            Home("_client/model_facts.py", "ModelFacts.present"),
            Home("_fastapi/plan.py", "Planner.member"),
            Home("_fastapi/plan.py", "_constrained"),
            Home("_target_binding.py", "_SchemaUses.held"),
            Home("_target_binding.py", "_UseFacts.holder"),
            Home("_target_binding.py", "_Binder.schema_null"),
            Home("types.py", "DataType.admits_none"),
            Home("types.py", "reference_names_nullable"),
            Home("model/base.py", "DataModelFieldBase._field_null_source"),
        ),
    ),
    DecisionHome(
        decision=(
            "targets: the JSON and lexical kind of a type's values (the kind tables of codecs, parameters, forms and "
            "parts), and the kind of each strict or constrained Pydantic scalar type, which the lexical kinds and the "
            "client's JSON types both read"
        ),
        homes=(
            Home("_target_types.py", "TypeTables.json_kinds"),
            Home("_target_types.py", "_LEAF_KINDS"),
            Home("_target_types.py", "CONSTRAINED_SCALARS"),
            Home("_target_types.py", "_VOCABULARIES"),
            Home("_target_types.py", "TypeTables.lexical_kind"),
        ),
        core=(
            "`_target_types.py:TypeTables.json_kinds` (one table by class, `_LEAF_KINDS` with `CONSTRAINED_SCALARS`, "
            "and the vocabularies `schema`, `codec` and `lexical` as rows of `_VOCABULARIES`), "
            "`TypeTables.lexical_kind`"
        ),
        adapters=(
            "`model/_decisions/enum_kind.py:literal_value_kind` (the kind row above), `_target_binding.py:_UseFacts` "
            "(the text of parameters, headers and forms, by the type each use binds), "
            "`_openapi_wire_plan.py:LexicalKinds` (the batch reader of form members and parts, through "
            "`_target_types.py:TypeTables.member_kind`), `_client/model_facts.py:ModelFacts.field_types` and the "
            "pagination and socket readers of the `schema` vocabulary, `_client/codec_plan.py:_Planner.union` (the "
            "`codec` vocabulary)"
        ),
        sites=(
            Home("_target_binding.py", "_UseFacts"),
            Home("_openapi_wire_plan.py", "LexicalKinds"),
            Home("_target_types.py", "TypeTables.member_kind"),
            Home("_client/model_facts.py", "ModelFacts.field_types"),
            Home("_client/codec_plan.py", "_Planner.union"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a parser builds a field it writes: the field class's constructor (its validation-free form for the "
            "`property` row where the class declares one), the configured field options each kind of field takes (a "
            "new option reaches every kind unless its row leaves it out; rows `property`, `typename`, `array-root`, "
            "`root`, `enum-member`, `bare`, and `legacy-graphql-field`, `legacy-boolean-property`, "
            "`legacy-alias-option-only`), the alias or validation aliases and the `--serialization-aliases` entry of a "
            "named field; the name, original name and aliases that a copy takes from the placeholder it replaces, and "
            "those a field built earlier takes for a new name (rows `required-copy`, `final-class`, `rename`), in the "
            "form the field class writes them (`DataModelFieldBase.alias_form`: a field before Pydantic 2.4 writes a "
            "non-identifier alias as the serialization alias and a validation alias, as its constructor does, unless "
            "its class has a generated base class, whose signature the class takes, so the alias stays `alias=`; "
            "`GenerationStore.set_field_identity` and `rename_field` write it, for the class the caller says inherits "
            "or the model that holds the field, and `set_base_classes` writes it again for the fields of a model it "
            "leaves without a base class, as L3 `delete_duplicate_models` leaves a model whose only base is a root "
            "model); whether a required field keeps its default beside `required` under `--use-default` "
            "(`use_default_with_required`), where it is built and where a required name makes it required later; the "
            "description of a GraphQL enum value, which its member takes in its extras only where "
            "`--use-field-description` or `--use-inline-field-description` writes it (a Pydantic field with a "
            "description registers the `Field` import, which moves the order of the module imports)"
        ),
        homes=(
            Home("parser/base.py", "Parser._build_field"),
            Home("parser/base.py", "Parser._field_policy"),
            Home("parser/base.py", "Parser._field_option_kwargs"),
            Home("parser/base.py", "Parser._field_aliases"),
            Home("parser/base.py", "Parser._take_field_aliases"),
            Home("parser/_field_rows.py", "FieldRow"),
            Home("parser/_field_rows.py", "FieldFacts"),
            Home("parser/_field_rows.py", "FieldAliasRow"),
            Home("parser/_field_rows.py", "FieldIdentity"),
            Home("parser/_field_rows.py", "FIELD_OPTIONS_LEFT_OUT"),
            Home("parser/_field_rows.py", "field_options_left_out"),
            Home("parser/_field_rows.py", "FIELD_ROWS_BUILT_WITHOUT_VALIDATION"),
            Home("parser/_field_rows.py", "FIELD_ROWS_WITHOUT_VALIDATION_ALIASES"),
            Home("parser/_field_rows.py", "uses_default_with_required"),
            Home("parser/_passes/_shared.py", "placeholder_identity"),
            Home("model/base.py", "DataModelFieldBase.alias_form"),
            Home("model/pydantic_v2/dataclass.py", "DataModelFieldBackport"),
            Home("parser/generation.py", "_write_alias_form"),
        ),
        core=(
            "`parser/base.py:Parser._build_field` over `Parser._field_policy` (the constructor of each row with the "
            "`Parser._field_option_kwargs` it takes, read once per run), `Parser._field_aliases`, "
            "`Parser._take_field_aliases`; `parser/_field_rows.py`: `FieldRow`, `FieldFacts`, `FieldAliasRow`, "
            "`FieldIdentity`, `FIELD_OPTIONS_LEFT_OUT`, `field_options_left_out` (the `legacy-*` rows take every "
            "option under `--apply-field-options-to-every-field`), `FIELD_ROWS_BUILT_WITHOUT_VALIDATION`, "
            "`FIELD_ROWS_WITHOUT_VALIDATION_ALIASES`, `uses_default_with_required`; "
            "`parser/_passes/_shared.py:placeholder_identity`; `model/base.py:DataModelFieldBase.alias_form` (the form "
            "is written by `model/pydantic_v2/dataclass.py:DataModelFieldBackport` and "
            "`parser/generation.py:_write_alias_form`)"
        ),
        adapters=(
            "`parser/_field_copies.py:_copy_resolved_inherited_field` (both placeholder kinds); "
            "`parser/base.py:Parser._apply_final_class_field_aliases`; "
            "`parser/_passes/_shared.py:override_required_field` (its plain copy); "
            "`parser/_inherited_schemas.py:build_missing_required_field` (also its copy of an inherited field); "
            "`parser/jsonschema.py:JsonSchemaParser`: `get_object_field` (the field's schema facts), "
            "`parse_object_fields`, `parse_array_fields`, `parse_array`, `_parse_root_type_with_context`, "
            "`_parse_multiple_types_with_properties`, `parse_enum`, `_build_all_of_ref_root_model`; "
            "`parser/graphql.py:GraphQLParser`: `parse_field`, `_typename_field`, `parse_enum_as_enum_class`, "
            "`_parse_enum_as_root`, `parse_union`, `_resolve_typename_collisions`; "
            "`parser/_passes/modules.py:apply_discriminator_type` (through `DiscriminatorHost`); "
            "`uses_default_with_required` readers: `Parser._effective_default_state` (the fields the JSON Schema, "
            "OpenAPI and GraphQL parsers build), `JsonSchemaParser._parse_object_common_part`, `parser/_rw_split.py`: "
            "`collect_inherited_fields_for_request_response`, `get_raw_inherited_fields`, "
            "`parser/_passes/_shared.py:apply_inherited_field_default`, "
            "`parser/_passes/plans.py:flatten_base_layouts`; the placeholder copy of `override_required_field` without "
            "a class keeps its default whether or not the copy is required; not routed: the copy "
            "`OpenAPIParser.parse_object_fields` rebuilds from a field's `__dict__`, and the typed extra field of "
            "`model/pydantic_v2/base_model.py:BaseModel.create_typed_extra_field`"
        ),
        sites=(
            Home("parser/_field_copies.py", "_copy_resolved_inherited_field"),
            Home("parser/base.py", "Parser._apply_final_class_field_aliases"),
            Home("parser/_passes/_shared.py", "override_required_field"),
            Home("parser/_inherited_schemas.py", "build_missing_required_field"),
            Home("parser/jsonschema.py", "JsonSchemaParser"),
            Home("parser/jsonschema.py", "JsonSchemaParser.get_object_field"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_object_fields"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_array_fields"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_array"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_root_type_with_context"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_multiple_types_with_properties"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_enum"),
            Home("parser/jsonschema.py", "JsonSchemaParser._build_all_of_ref_root_model"),
            Home("parser/graphql.py", "GraphQLParser"),
            Home("parser/graphql.py", "GraphQLParser.parse_field"),
            Home("parser/graphql.py", "GraphQLParser._typename_field"),
            Home("parser/graphql.py", "GraphQLParser.parse_enum_as_enum_class"),
            Home("parser/graphql.py", "GraphQLParser._parse_enum_as_root"),
            Home("parser/graphql.py", "GraphQLParser.parse_union"),
            Home("parser/graphql.py", "GraphQLParser._resolve_typename_collisions"),
            Home("parser/_passes/modules.py", "apply_discriminator_type"),
            Home("parser/_passes/modules.py", "DiscriminatorHost"),
            Home("parser/base.py", "Parser._effective_default_state"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_object_common_part"),
            Home("parser/_rw_split.py", "collect_inherited_fields_for_request_response"),
            Home("parser/_rw_split.py", "get_raw_inherited_fields"),
            Home("parser/_passes/_shared.py", "apply_inherited_field_default"),
            Home("parser/_passes/plans.py", "flatten_base_layouts"),
            Home("model/pydantic_v2/base_model.py", "BaseModel.create_typed_extra_field"),
        ),
    ),
    DecisionHome(
        decision=(
            "the method resolution order of generated models and the effective fields a model has with its bases "
            "(targets enumerate members through it, not by their own C3): the C3 merge and walk over any item, by rule "
            "`first-head` (the generator's order: a conflict takes the first head, a cycle is cut where the walk meets "
            "it) or `python` (Python's order, None where Python refuses the class), the descendants-first order of "
            "bases, and the member each name resolves to through that order (the first owner that declares it, at the "
            "place of the last: msgspec's struct options, the fields of a TypedDict; or, as dataclasses, Pydantic and "
            "msgspec collect fields, the field of the first base whose own bases or itself declare it: the fields of "
            "every other model and the fields a target binds), with whether a field takes a positional place in the "
            "constructor"
        ),
        homes=(
            Home("_mro.py", "c3_merge"),
            Home("_mro.py", "linearize"),
            Home("_mro.py", "sort_descendants_first"),
            Home("_mro.py", "resolve_members"),
            Home("_mro.py", "collect_members"),
            Home("model/base.py", "linearize_data_models"),
        ),
        core=(
            "`_mro.py`: `c3_merge`, `linearize`, `sort_descendants_first`, `resolve_members`, `collect_members`; "
            "`model/base.py:linearize_data_models` (the models' order, from the snapshot of their bases)"
        ),
        adapters=(
            "`model/base.py`: `_effective_field_owners` (a model's fields, the class variables as rows `declared` or "
            "`no-place`, the fields of bases as rows `collected` or `nearest` of `DataModel._BASE_FIELDS`), "
            "`python_base_classes`, `DataModel.iter_all_fields`, `sort_data_models_for_mro` (its `c3_merge` names the "
            "core's); `parser/_passes/_shared.py:takes_positional_place`; "
            "`parser/_passes/settle.py:sort_base_classes_for_mro`; `parser/_inherited_schemas.py`: "
            "`linearize_inherited_schema_refs` and `sort_inherited_base_classes_for_mro` (raw schemas, `first-head`); "
            "rule `python`: `parser/_inheritance_check.py:check_model_inheritance` (behind "
            "`Parser._check_model_inheritance`), `_target_binding.py:_Models.field_bindings`"
        ),
        sites=(
            Home("model/base.py", "_effective_field_owners"),
            Home("model/base.py", "python_base_classes"),
            Home("model/base.py", "DataModel.iter_all_fields"),
            Home("model/base.py", "sort_data_models_for_mro"),
            Home("parser/_passes/_shared.py", "takes_positional_place"),
            Home("parser/_passes/settle.py", "sort_base_classes_for_mro"),
            Home("parser/_inherited_schemas.py", "linearize_inherited_schema_refs"),
            Home("parser/_inherited_schemas.py", "sort_inherited_base_classes_for_mro"),
            Home("parser/_inheritance_check.py", "check_model_inheritance"),
            Home("_target_binding.py", "_Models.field_bindings"),
        ),
    ),
    DecisionHome(
        decision=(
            "which schema parts a model inherits and in what order a reader meets them, hence which ancestor's "
            "declaration of a property it takes: the inherited schemas of the bases in C3 order, each as its parts, "
            "the schema first (row `c3`: the first declaration of a property wins), or ancestors first, each with its "
            "members first (row `ancestor-first`: the order of the fields a request or response variant collects); the "
            "parts are the members `all_of_members` reads"
        ),
        homes=(
            Home("parser/_inherited_schemas.py", "walk_inherited_schemas"),
            Home("parser/_inherited_schemas.py", "_InheritedSchema"),
            Home("parser/_inherited_schemas.py", "_BaseList"),
        ),
        core=(
            "`parser/_inherited_schemas.py:walk_inherited_schemas` (the records `_InheritedSchema` and `_BaseList` "
            "keep what is read of a reference key and of a tuple of keys)"
        ),
        adapters=(
            "`parser/_inherited_schemas.py`: `get_inherited_property_map` and `model_declarations` (row `c3`), "
            "`get_inherited_property_order` (row `ancestor-first`), `get_inherited_field_schema` (row `c3` under "
            "`--read-inherited-property-schemas-in-mro-order`, else row `legacy-dfs-inline-first`: "
            "`parser/jsonschema.py:JsonSchemaParser._inherited_declaration_row`); "
            "`JsonSchemaParser._get_inherited_field_type` (row `legacy-direct-then-grandparents`: the generated fields "
            "of the parents' parents); not routed, open: `parser/_rw_split.py:join_rw_model_property_flags` (the "
            "read/write flags of a schema, from its members as written) and "
            "`parser/_schema_validators.py:get_input_names_by_property` (the names a base without a model declares, "
            "from its own `properties` alone); not this rule: "
            "`parser/_rw_split.py:collect_inherited_fields_for_request_response`, which reads the C3 order and takes "
            "for each name the first schema whose own fields hold it, also one that only requires the property or "
            "whose fields are enum members, where the property map names the schema that declares it"
        ),
        sites=(
            Home("parser/_inherited_schemas.py", "get_inherited_property_map"),
            Home("parser/_inherited_schemas.py", "model_declarations"),
            Home("parser/_inherited_schemas.py", "get_inherited_property_order"),
            Home("parser/_inherited_schemas.py", "get_inherited_field_schema"),
            Home("parser/jsonschema.py", "JsonSchemaParser._inherited_declaration_row"),
            Home("parser/jsonschema.py", "JsonSchemaParser._get_inherited_field_type"),
            Home("parser/_rw_split.py", "join_rw_model_property_flags"),
            Home("parser/_schema_validators.py", "get_input_names_by_property"),
            Home("parser/_rw_split.py", "collect_inherited_fields_for_request_response"),
        ),
    ),
    DecisionHome(
        decision=(
            "Pydantic `extra`, closedness and by-name configuration of a model through its bases (targets read it, "
            "never derive it)"
        ),
        homes=(
            Home("model/pydantic_v2/base_model.py", "_PydanticV2Model._applied_config_items"),
            Home("model/pydantic_v2/dataclass.py"),
        ),
        core=(
            "`model/pydantic_v2/base_model.py`, `model/pydantic_v2/dataclass.py`: `_applied_config_items` (a model "
            "merges its bases' in order, then its own; a dataclass applies its own, else its first base's)"
        ),
        adapters=(
            "`_target_binding.py:_applied_configuration`, recorded once as `FinalModelSymbol.extra` and `.by_name`; "
            "read by `_openapi_codec_plan.py` (`extras_symbols`), `_fastapi/plan.py` (form members by field name) and "
            "`_target_binding.py:_held` (what a class takes of the other properties its schema gives, read as "
            "`TextShape.additional`, `EncodingFacts.declared` and `PartFacts.extra`; a reader that keeps the parts "
            "itself reads the schema's `PartFacts.loose`)"
        ),
        sites=(
            Home("_target_binding.py", "_applied_configuration"),
            Home("_openapi_codec_plan.py", "extras_symbols"),
            Home("_target_binding.py", "_held"),
            Home("_fastapi/plan.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "targets: the structure and spelling of a model type: the model's `TypeExpr`, frozen once at the seam (no "
            "target builds, unwraps or inspects a type view for a decision)"
        ),
        homes=(
            Home("types.py", "type_expr_of"),
            Home("types.py", "render_type_expr"),
        ),
        core="`types.py`: `type_expr_of`, `render_type_expr`; planned: `freeze_type` in `_target_types.py`",
        adapters="`_target_module.py:TargetModule`, `_target_module.py:TypeComposer` (text of support compositions)",
        sites=(
            Home("_target_module.py", "TargetModule"),
            Home("_target_module.py", "TypeComposer"),
        ),
    ),
    DecisionHome(
        decision=(
            "targets: the Python builtins a type names bare, which a module's own names must not shadow: the twelve "
            "classes a `BuiltinType` names (`bool`, `bytes`, `complex`, `float`, `int`, `str`, `object`, `list`, "
            "`set`, `frozenset`, `dict`, `tuple`)"
        ),
        homes=(Home("_target_contract.py", "BuiltinName"),),
        core="`_target_contract.py:BuiltinName`",
        adapters=(
            "`_target_binding.py:_BUILTINS` (the `BuiltinType` of each), `_target_module.py:_BUILTINS` (the names "
            "`TargetModule` reserves)"
        ),
        sites=(Home("_target_module.py", "TargetModule"),),
    ),
    DecisionHome(
        decision=(
            "targets: the operation plan both targets read (groups, operation and argument names, settings resolution, "
            "parameters and defaults, media, bodies, responses, status classes, bodyless statuses, forms, parts, "
            "security requirements); where the client and the server differ, a named policy row"
        ),
        homes=(
            Home("_target_operations.py", "declared_in"),
            Home("_target_operations.py", "declared_media_type"),
            Home("_target_operations.py", "json_null"),
            Home("_target_operations.py", "coded_use"),
            Home("_target_operations.py", "native_type"),
            Home("_target_operations.py", "UNSPELLABLE"),
            Home("_target_operations.py", "binding_problem"),
            Home("_target_operations.py", "operation_docstring"),
            Home("_target_operations.py", "raise_planned"),
            Home("_target_operations.py", "TargetPolicy"),
            Home("_target_operations.py", "CLIENT"),
            Home("_target_operations.py", "SERVER"),
        ),
        core=(
            "`_target_operations.py` (the computations both targets share today: the grouping tag, derived operation "
            "and type names, argument names and `--aliases` entries, settings resolution, path placeholder checks, "
            "whether a document declares a declaration itself (`declared_in`), a media key's normalized type and its "
            "refusal (`declared_media_type`), whether a body or response sends JSON null (`json_null`), whether a "
            "body's model reads and writes it, all but raw bytes the models leave unconstrained (`coded_use`), the "
            "refusal of a use without a native type or with one no generated module can import (`native_type`, "
            "`UNSPELLABLE`, reported at the binding stage by `binding_problem`), the text of an operation's docstring "
            "(`operation_docstring`: a configured description, else the operation's summary and description under "
            "`--use-schema-description`, else its route after the policy's verb), and the stop of a planning phase "
            "(`raise_planned`); the differences are rows of `TargetPolicy`, `CLIENT` and `SERVER`; statuses and media "
            "types are the HTTP vocabulary row's)"
        ),
        adapters=(
            "`_client/plan.py:Planner.resolved`, `_fastapi/plan.py:Planner.selected` (each target keeps only its own "
            "forms and its own rules where they differ)"
        ),
        sites=(
            Home("_client/plan.py", "Planner.resolved"),
            Home("_fastapi/plan.py", "Planner.selected"),
        ),
    ),
    DecisionHome(
        decision=(
            "targets: how a model use's native codec is chosen and copied, alike in both targets: the file a target "
            "copies and the module generated code imports from of each model codec runtime module, and the runtime "
            "module that writes the extra members of Pydantic dataclasses, which Pydantic never dumps (its files, its "
            "own and those of the codec modules it imports, its module, the serializer a client's codecs take and the "
            "JSON writer a server's responses dump by)"
        ),
        homes=(
            Home("_openapi_codec_plan.py", "codec_file"),
            Home("_openapi_codec_plan.py", "codec_module"),
            Home("_openapi_codec_plan.py", "ExtrasRuntime"),
            Home("_openapi_codec_plan.py", "EXTRAS"),
        ),
        core="`_openapi_codec_plan.py`: `codec_file`, `codec_module`, `ExtrasRuntime` (`EXTRAS`)",
        adapters=(
            "`_client/codec_plan.py:CodecRuntime.modules`, `_client/codec_render.py` (the model bindings' import and "
            "codec arguments), `_client/runtime.py:Capabilities.modules`, `_fastapi/render.py` "
            "(`ServerRenderer.runtime`, `_declared`)"
        ),
        sites=(
            Home("_client/codec_plan.py", "CodecRuntime.modules"),
            Home("_client/runtime.py", "Capabilities.modules"),
            Home("_fastapi/render.py", "ServerRenderer.runtime"),
            Home("_fastapi/render.py", "_declared"),
            Home("_client/codec_render.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "targets: how the targets read one accepted batch: its type uses and symbols by identity, the field "
            "bindings of each symbol in the order its class declares them, the value use of each schema occurrence by "
            "its location and direction, the facts of the value an alias or root model stands for (the one member the "
            "binder gives it), whether a parameter, header or request body is required (its `required` fact, which the "
            "binder records as the parser reads it, a value such as `'yes'` or `1` included, and refuses where the "
            "parser does: `_target_binding.py:_read_required`; true for a path parameter a placeholder names; a header "
            "records no location or name, which its headers map implies), and the first reference of a declaration "
            "that reaches no object or whose document does not load"
        ),
        homes=(
            Home("_target_contract.py", "uses_by_id"),
            Home("_target_contract.py", "symbols_by_id"),
            Home("_target_contract.py", "members_by_consumer"),
            Home("_target_contract.py", "schema_uses"),
            Home("_target_contract.py", "wrapped_facts"),
            Home("_target_contract.py", "wrapped_type"),
            Home("_target_contract.py", "is_required"),
            Home("_target_contract.py", "unresolved_reference"),
        ),
        core=(
            "`_target_contract.py`: `uses_by_id`, `symbols_by_id`, `members_by_consumer`, `schema_uses`, "
            "`wrapped_facts`, `wrapped_type`, `is_required`, `unresolved_reference`"
        ),
        adapters=(
            "`_openapi_wire_plan.py` (`_WirePlanner`, `_parameter`), `_client/plan.py:Planner`, "
            "`_client/security.py:_scheme`, `_fastapi/plan.py:Planner` and `_unresolved`; the symbol and member tables "
            "through `_target_types.py:TypeTables` (`of`, built once per target with its uses by identity and scheme "
            "catalogues by `_openapi_wire_plan.py:BatchTables`, which the wire planner, "
            "`_client/model_facts.py:ModelFacts` (which also keeps the target's `schema_uses`), the planners and "
            "`_client/security.py:SecurityPlanner` read, and once in `_target_binding.py:_Models` over the tables the "
            "binder fills; `TypeTables.wrapped`, the one step through an alias or root model, given the symbols seen "
            "and the `through` row (`is_wrapper`, `is_alias`), for every recursive reader of `_target_types.py`, "
            "`_client/model_facts.py`, `_client/codec_plan.py`, `_client/pagination.py` and `_fastapi/plan.py`)"
        ),
        sites=(
            Home("_openapi_wire_plan.py", "_WirePlanner"),
            Home("_openapi_wire_plan.py", "_parameter"),
            Home("_client/plan.py", "Planner"),
            Home("_client/security.py", "_scheme"),
            Home("_fastapi/plan.py", "Planner"),
            Home("_fastapi/plan.py", "_unresolved"),
            Home("_target_types.py", "TypeTables"),
            Home("_target_types.py", "TypeTables.of"),
            Home("_openapi_wire_plan.py", "BatchTables"),
            Home("_client/model_facts.py", "ModelFacts"),
            Home("_target_binding.py", "_SchemaUses.schema_uses"),
            Home("_client/security.py", "SecurityPlanner"),
            Home("_target_binding.py", "_Models"),
            Home("_target_types.py", "TypeTables.wrapped"),
            Home("_target_types.py", "is_wrapper"),
            Home("_target_types.py", "is_alias"),
            Home("_client/codec_plan.py"),
            Home("_client/pagination.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "targets: the id of a type use a binding records (`TypeUseId`): the sites a use is located at (its use "
            "site, schema and declaration, each in its loaded document), the direction its role reads or sends "
            "(`neutral` for a schema's own use), and the neutral value use a schema has of itself, which `schema_uses` "
            "binds and the lookups of the extra and helper schemas find by that id"
        ),
        homes=(Home("_target_binding.py", "_SchemaUses.use_id"),),
        core="`_target_binding.py:_SchemaUses.use_id`",
        adapters=(
            "`_target_binding.py:_SchemaUses.use` (every use it binds), `_SchemaUses.own_use` (the lookups of "
            "`_SchemaUses.extras` and `_SchemaUses.helper_uses`)"
        ),
        sites=(
            Home("_target_binding.py", "_SchemaUses.use"),
            Home("_target_binding.py", "_SchemaUses.own_use"),
            Home("_target_binding.py", "_SchemaUses.extras"),
            Home("_target_binding.py", "_SchemaUses.helper_uses"),
        ),
    ),
    DecisionHome(
        decision=(
            "what the wire declarations of an operation hold, read alike by the wire plan and both targets: the "
            "children of a declaration of one kind, in order (`children_of`: a response's media, headers and links, a "
            "body's media, a media's encodings, an encoding's headers), the media of an operation's request body, none "
            "without one (`request_media`), and the headers the encodings of that body declare (`encoding_headers`)"
        ),
        homes=(
            Home("_target_contract.py", "children_of"),
            Home("_target_contract.py", "request_media"),
            Home("_target_contract.py", "encoding_headers"),
        ),
        core="`_target_contract.py`: `children_of`, `request_media`, `encoding_headers`",
        adapters=(
            "`_openapi_wire_plan.py:_headers` (the headers of the responses, then the requested encoding headers); "
            "`_client/plan.py`: `Planner.part_media`, `Planner._headers`, `Planner.media_list`, `_encoded`, "
            "`encoding_header_uses`, `style_uses`, `form_uses`; `_fastapi/plan.py`: `invalid_links`, "
            "`MediaSpec.encodings`, `Planner.body`, `Planner.response`; left out: the children a use walk descends "
            "into, every kind but encodings (`_openapi_wire_plan.py:operation_uses`), the content of a content "
            "parameter, its first child (`_openapi_wire_plan.py:_content_parameter`), and the encodings of a media "
            "that need a header only parts carry, which read every child of the media (`_client/plan.py:Planner.media`)"
        ),
        sites=(
            Home("_openapi_wire_plan.py", "_headers"),
            Home("_client/plan.py", "Planner.part_media"),
            Home("_client/plan.py", "Planner._headers"),
            Home("_client/plan.py", "Planner.media_list"),
            Home("_client/plan.py", "_encoded"),
            Home("_client/plan.py", "encoding_header_uses"),
            Home("_client/plan.py", "style_uses"),
            Home("_client/plan.py", "form_uses"),
            Home("_fastapi/plan.py", "invalid_links"),
            Home("_fastapi/plan.py", "Planner.body"),
            Home("_fastapi/plan.py", "Planner.response"),
            Home("_openapi_wire_plan.py", "operation_uses"),
            Home("_openapi_wire_plan.py", "_content_parameter"),
            Home("_client/plan.py", "Planner.media"),
        ),
    ),
    DecisionHome(
        decision=(
            "the style a parameter or a form member's encoding writes and whether it explodes (OpenAPI: the Encoding "
            "Object's `style` and `explode` follow the Parameter Object's): the style defaults by the location, and an "
            "`explode` that is not given is true for the form and cookie styles"
        ),
        homes=(Home("_openapi_wire_plan.py", "_style_of"),),
        core="`_openapi_wire_plan.py:_style_of`",
        adapters=(
            "`_openapi_wire_plan.py`: `_parameter` (the location of the parameter) and `_encoding` (a member of a form "
            "is a query parameter, whose cookie style the plan refuses); left out: whether an encoding's written facts "
            "differ from the defaults (`_fastapi/plan.py:_planned`, `_client/plan.py:_encoded`: the first encoding of "
            "a multipart response no builtin reader applies)"
        ),
        sites=(
            Home("_openapi_wire_plan.py", "_parameter"),
            Home("_openapi_wire_plan.py", "_encoding"),
            Home("_fastapi/plan.py", "_planned"),
            Home("_client/plan.py", "_encoded"),
        ),
    ),
    DecisionHome(
        decision=(
            "which keywords of a schema hold subschemas, the shape of each value (a map of named schemas, a list, one, "
            "or one or a list) and the roles each keyword plays (applicator, in place, conditional, child of an array "
            "or an object, unmatched, unevaluated, held as a `JsonSchemaObject` field, definitions); a walker that "
            "visits another set or order than a role gives keeps it as a named view of the table; the keywords that "
            "hold schemas only for references (`DEFINITION_KEYS`) and those whose values are instance data; the "
            "subschemas one keyword holds, read by its shape, and the walk over every value of a loaded JSON value"
        ),
        homes=(
            Home("_json_schema.py", "SCHEMA_POSITIONS"),
            Home("_json_schema.py", "SCHEMA_SHAPES"),
            Home("_json_schema.py", "schema_positions"),
            Home("_json_schema.py", "DEFINITION_KEYS"),
            Home("_json_schema.py", "INSTANCE_DATA_KEYWORDS"),
            Home("_json_schema.py", "subschema_entries"),
            Home("_json_schema.py", "json_nodes"),
            Home("_json_schema.py", "SCHEMA_MAP_KEYS"),
            Home("_json_schema.py", "SCHEMA_VALUE_KEYS"),
            Home("_json_schema.py", "COMBINATOR_POSITIONS"),
            Home("_json_schema.py", "NAMED_SCHEMA_MAPS"),
            Home("_json_schema.py", "MODELED_SCHEMA_MAPS"),
            Home("_json_schema.py", "ITEM_POSITIONS"),
            Home("_json_schema.py", "NAMESPACE_DEFINITION_KEYS"),
            Home("_json_schema.py", "MODEL_VALUE_POSITIONS"),
            Home("_json_schema.py", "REF_PRE_PASS_FIELDS"),
            Home("_json_schema.py", "REF_PRE_PASS_POSITIONS"),
        ),
        core=(
            "`_json_schema.py`: `SCHEMA_POSITIONS`, `SCHEMA_SHAPES`, `schema_positions`, `DEFINITION_KEYS`, "
            "`INSTANCE_DATA_KEYWORDS`, `subschema_entries`, `json_nodes`; views `SCHEMA_MAP_KEYS`, "
            "`SCHEMA_VALUE_KEYS`, `COMBINATOR_POSITIONS`, `NAMED_SCHEMA_MAPS`, `MODELED_SCHEMA_MAPS`, "
            "`ITEM_POSITIONS`, `NAMESPACE_DEFINITION_KEYS`, `MODEL_VALUE_POSITIONS`, `REF_PRE_PASS_FIELDS`, "
            "`REF_PRE_PASS_POSITIONS`"
        ),
        adapters=(
            "the walkers of `parser/jsonschema.py` (`_walk_schema_objects`, `_version_checked_subschemas`), "
            "`parser/_schema_refs.py:walk_ref_cycle` and `parser/_schema_documents.py` "
            "(`iter_schema_resource_children`, and `_searched` of row `legacy-external-anchor-search`, through "
            "`subschema_entries`), `parser/_schema_graph.py:walk_schema_resources` (the schemas it passes over, "
            "through `subschema_entries`), `parser/_schema_reading.py:_DOCUMENT_KEYWORDS` (the other keywords of a "
            "document object by what their values hold, read through the views `_NON_SCHEMA_KEYWORDS` of the read of a "
            "whole document, `parser/jsonschema.py:JsonSchemaParser._named_entry_maps` of its named entry maps and "
            "`_REF_EDGE_KEYWORDS` of `$ref` edges) and their keyword tables; through `json_nodes`: "
            "`parser/xmlschema.py:_schema_dicts`, `_input_model_transport.py`, `input_model_result.py`, "
            "`_client/protocols.py:_literal_problem`, `_recommend/_metrics.py:_collect_words`; "
            "`parser/_schema_object.py:_locate_dump`, `parser/_intersection.py`, "
            "`_fastapi/source_document.py:SourceDocument.schema_member`, `input_model.py`, `_recommend/_signals.py`, "
            "`_recommend/_metrics.py`, `_generation.py:is_schema`"
        ),
        sites=(
            Home("parser/_schema_refs.py", "walk_ref_cycle"),
            Home("parser/_schema_documents.py", "iter_schema_resource_children"),
            Home("parser/_schema_documents.py", "_searched"),
            Home("parser/_schema_graph.py", "walk_schema_resources"),
            Home("parser/xmlschema.py", "_schema_dicts"),
            Home("_client/protocols.py", "_literal_problem"),
            Home("_recommend/_metrics.py", "_collect_words"),
            Home("parser/_schema_object.py", "_locate_dump"),
            Home("_fastapi/source_document.py", "SourceDocument.schema_member"),
            Home("_generation.py", "is_schema"),
            Home("parser/jsonschema.py", "JsonSchemaParser._walk_schema_objects"),
            Home("parser/jsonschema.py", "JsonSchemaParser._version_checked_subschemas"),
            Home("_input_model_transport.py"),
            Home("input_model_result.py"),
            Home("parser/_intersection.py"),
            Home("input_model.py"),
            Home("_recommend/_signals.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a block runs with the generator's own warnings ignored (a retry parse, a recommendation trial, a "
            "probe of a type: the run the user sees reports them): the warning filters saved, one category (every "
            "warning by default) ignored while the block runs, and the filters restored after it"
        ),
        homes=(Home("_warning_control.py", "quiet"),),
        core="`_warning_control.py:quiet`",
        adapters=(
            "`parser/base.py:Parser._run_with_suppressed_warnings` (the hook the settle passes call for a retry "
            "parse), `_generation.py:_parse_again` (the category its caller names, or none), "
            "`_recommend/_trial.py:_run_candidate`, `parser/jsonschema.py:JsonSchemaParser._typed_model_children` (the "
            "extra items type of a key model)"
        ),
        sites=(
            Home("parser/base.py", "Parser._run_with_suppressed_warnings"),
            Home("_generation.py", "_parse_again"),
            Home("_recommend/_trial.py", "_run_candidate"),
            Home("parser/jsonschema.py", "JsonSchemaParser._typed_model_children"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether a warning about a schema is shown: once per parse for each key it reports (the schema's location, "
            "with the keywords or patterns it names), recorded in that warning's set of the parser once the warning is "
            "shown, so a block whose warnings the filters ignore (`quiet`, as the inherited property map and the "
            "request/response walk read schemas) leaves the key to the parse that shows it"
        ),
        homes=(Home("_warning_control.py", "first_report"),),
        core="`_warning_control.py:first_report`",
        adapters=(
            "`parser/jsonschema.py`: `JsonSchemaParser._warn_unsatisfiable`, `_warn_untaken_patterns`, "
            "`_warn_closed_pattern_properties`, `_warn_inexact_object_map`, `_enum_positions_outside_const`"
        ),
        sites=(
            Home("parser/jsonschema.py", "JsonSchemaParser._warn_unsatisfiable"),
            Home("parser/jsonschema.py", "JsonSchemaParser._warn_untaken_patterns"),
            Home("parser/jsonschema.py", "JsonSchemaParser._warn_closed_pattern_properties"),
            Home("parser/jsonschema.py", "JsonSchemaParser._warn_inexact_object_map"),
            Home("parser/jsonschema.py", "JsonSchemaParser._enum_positions_outside_const"),
        ),
    ),
    DecisionHome(
        decision=(
            "which frames a warning's `stacklevel` and the caller of `Parser.parse()` skip: the wrappers that "
            "`--debug` puts around the functions it traces"
        ),
        homes=(Home("_warning_control.py", "is_tracer_frame"),),
        core="`_warning_control.py:is_tracer_frame`",
        adapters="`parser/_pipeline.py:parse_caller`, `parser/_passes/_warnings.py:warn_as_module`",
        sites=(
            Home("parser/_pipeline.py", "parse_caller"),
            Home("parser/_passes/_warnings.py", "warn_as_module"),
        ),
    ),
    DecisionHome(
        decision=(
            "which module the filters of a warning match, for code that moved out of a public module: the module "
            "it came from, and the package for a frame in the generation core"
        ),
        homes=(Home("_warning_control.py", "warn_at_frame"),),
        core="`_warning_control.py:warn_at_frame`",
        adapters=(
            "`_warning_control.py:warn_as_public`, `parser/_pipeline.py:warn_at_parse_caller`, "
            "`parser/_passes/_warnings.py:warn_as_module`"
        ),
        sites=(
            Home("_warning_control.py", "warn_as_public"),
            Home("parser/_pipeline.py", "warn_at_parse_caller"),
            Home("parser/_passes/_warnings.py", "warn_as_module"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a message lists items in English: the conjunction before the last item, also after a serial comma "
            "from three items on, or between every two items; and the first items of a longer list followed by how "
            "many more there are"
        ),
        homes=(
            Home("_message_text.py", "english_list"),
            Home("_message_text.py", "listed_with_more"),
        ),
        core="`_message_text.py:english_list`, `_message_text.py:listed_with_more`",
        adapters=(
            "the client helper checks (`_client/pagination.py`, `polling.py`, `streams.py`, `protocols.py`, "
            "`codec_plan.py`), the target option checks (`base_config.py`, `__main__.py`, `_api_generation.py`, "
            "`_target_render.py`), the client README (`_client/render.py`), the prompt (`prompt.py`), the Pydantic "
            "floor and Decimal default warnings (`_requirements.py:pydantic_floor_message`, "
            "`parser/_passes/imports.py:warn_about_decimal_defaults`), and `parser/jsonschema.py` (the shifted enum "
            "entries, the inexact object maps)"
        ),
        sites=(
            Home("_requirements.py", "pydantic_floor_message"),
            Home("parser/_passes/imports.py", "warn_about_decimal_defaults"),
            Home("_client/pagination.py"),
            Home("_client/protocols.py"),
            Home("_client/codec_plan.py"),
            Home("base_config.py"),
            Home("__main__.py"),
            Home("_api_generation.py"),
            Home("_target_render.py"),
            Home("_client/render.py"),
            Home("prompt.py"),
            Home("parser/jsonschema.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "which form a word of a message takes for a count: the one form for exactly one, else the other (by "
            "default the word with `s`, or `es` after an `s`), also with the count written before it"
        ),
        homes=(
            Home("_message_text.py", "for_count"),
            Home("_message_text.py", "counted"),
        ),
        core="`_message_text.py:for_count`, `_message_text.py:counted`",
        adapters=(
            "the target option checks (`__main__.py`, `_client/protocol_plan.py`), the recommender "
            "(`_recommend/__init__.py`, `_recommend/_report.py`), the dropped constraint warnings (`model/base.py`, "
            "`model/pydantic_v2/base_model.py`), `parser/_passes/imports.py:warn_about_decimal_defaults`, "
            "`parser/_passes/settle.py:warn_about_unmatched_model_name_keys`, `parser/jsonschema.py` (the shifted enum "
            "entries)"
        ),
        sites=(
            Home("parser/_passes/imports.py", "warn_about_decimal_defaults"),
            Home("parser/_passes/settle.py", "warn_about_unmatched_model_name_keys"),
            Home("__main__.py"),
            Home("_client/protocol_plan.py"),
            Home("_recommend/__init__.py"),
            Home("_recommend/_report.py"),
            Home("model/base.py"),
            Home("model/pydantic_v2/base_model.py"),
            Home("parser/jsonschema.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a message, a help text or a registry entry names an extra of the generator: its requirement "
            "(`datamodel-code-generator[x]`) and the pip command that installs it, written as a shell runs it (no "
            "prompt character), as a code span or as plain text"
        ),
        homes=(
            Home("_message_text.py", "extra_requirement"),
            Home("_message_text.py", "install_command"),
        ),
        core="`_message_text.py:extra_requirement`, `_message_text.py:install_command`",
        adapters=(
            "the missing-extra errors and warnings (`_debug.py:enable_debug_message`, `http.py:_get_http_stack`, "
            "`watch.py:_get_watchfiles`, `format.py:CodeFormatter._find_ruff_path`, "
            "`parser/openapi.py:OpenAPIParser.parse_raw` (`--validation`), `parser/protobuf.py`, `parser/graphql.py`), "
            "the help of `--url`, `--formatters` and `--debug` (`_option_table.py`), the `extra.httpx2` entry of "
            "`experimental.py`"
        ),
        sites=(
            Home("_debug.py", "enable_debug_message"),
            Home("http.py", "_get_http_stack"),
            Home("watch.py", "_get_watchfiles"),
            Home("format.py", "CodeFormatter._find_ruff_path"),
            Home("parser/openapi.py", "OpenAPIParser.parse_raw"),
            Home("parser/protobuf.py"),
            Home("parser/graphql.py"),
            Home("_option_table.py"),
            Home("experimental.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a block runs with context variables set (the scopes of an invocation, a parse or a rendering): each "
            "variable set to its value in order before the block, and reset by its token in reverse order after it, "
            "also when the block raises"
        ),
        homes=(Home("_context_scope.py", "context_values"),),
        core="`_context_scope.py:context_values`",
        adapters=(
            "the scopes `deprecations.py:cli_migration_warning_scope`, `_naming_hint.py:naming_hint_scope`, "
            "`reference.py:unmatched_model_name_keys_unreported`, `parser/base.py:dropped_constraints_recorded`, "
            "`parser/_passes/_state.py:pydantic_floors_recorded`, `_parse_memo.py:rendering_memo` and "
            "`watch_dependencies.py:WatchDependencies.generation`; `parser/base.py`: `Parser.parse` (the schema graph, "
            "the render plans, the memo and the caller frame of a parse) and `Parser._finalize_modules` (those of them "
            "no parse has set); `model/base.py` (the constructor-ordering flags)"
        ),
        sites=(
            Home("deprecations.py", "cli_migration_warning_scope"),
            Home("_naming_hint.py", "naming_hint_scope"),
            Home("reference.py", "unmatched_model_name_keys_unreported"),
            Home("parser/base.py", "dropped_constraints_recorded"),
            Home("parser/_passes/_state.py", "pydantic_floors_recorded"),
            Home("_parse_memo.py", "rendering_memo"),
            Home("watch_dependencies.py", "WatchDependencies.generation"),
            Home("parser/base.py", "Parser.parse"),
            Home("parser/base.py", "Parser._finalize_modules"),
            Home("model/base.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the reason (`Why`) a field's render plan records for an item that more than one output backend writes "
            "(the plain or `Annotated` annotation; the default as given, written alone after the annotation, "
            "Pydantic's `MISSING`, or none for a required field; the factory of a mutable default or of an optional "
            "nested model; a forced assignment; an explicit alias; a constraint the type does not take): its rule, "
            "keyword and option, one value each; a reason only one backend writes stays in that backend's module"
        ),
        homes=(
            Home("model/_render_plan.py", "PLAIN_ANNOTATION"),
            Home("model/_render_plan.py", "UNTAKEN_BY_TYPE"),
            Home("model/_render_plan.py", "Why"),
            Home("model/_render_plan.py", "RuleId"),
        ),
        core="`model/_render_plan.py` (`PLAIN_ANNOTATION` to `UNTAKEN_BY_TYPE`, beside `Why` and `RuleId`)",
        adapters=(
            "none: the field plans of `model/base.py`, `model/dataclass.py`, `model/msgspec.py`, "
            "`model/pydantic_base.py`, `model/pydantic_v2/base_model.py` and `model/pydantic_v2/dataclass.py` import "
            "them"
        ),
        sites=(
            Home("model/base.py"),
            Home("model/dataclass.py"),
            Home("model/msgspec.py"),
            Home("model/pydantic_base.py"),
            Home("model/pydantic_v2/base_model.py"),
            Home("model/pydantic_v2/dataclass.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a message, a warning or the model metadata names the schema object it is about: its location as "
            "`document#pointer` with the document URL redacted (`redact_reference`), or the caller's text when it has "
            "none; the parse path of a `SchemaParseError`, which redacts each part when it is printed; a message about "
            "a text that may hold a lone surrogate names the location, else that of a model, else the model's path, "
            "with each lone surrogate written as its escape, which no UTF-8 stream prints"
        ),
        homes=(
            Home("_url_redaction.py", "named_location"),
            Home("parser/_schema_object.py", "_named_path"),
            Home("reference.py", "named_schema_location"),
            Home("_message_text.py", "printable_text"),
        ),
        core=(
            "`_url_redaction.py:named_location`, `parser/_schema_object.py:_named_path`, "
            "`reference.py:named_schema_location`, `_message_text.py:printable_text`"
        ),
        adapters=(
            "`parser/_passes/settle.py:_named_model_path`, `parser/base.py:Parser._model_metadata`, the lone surrogate "
            "refusals (`model/msgspec.py:Struct._validate_renamed_field_names`, "
            "`parser/_passes/imports.py:reject_lone_surrogates_pydantic_reads`)"
        ),
        sites=(
            Home("parser/_passes/settle.py", "_named_model_path"),
            Home("parser/base.py", "Parser._model_metadata"),
            Home("model/msgspec.py", "Struct._validate_renamed_field_names"),
            Home("parser/_passes/imports.py", "reject_lone_surrogates_pydantic_reads"),
        ),
    ),
    DecisionHome(
        decision=(
            "how the parse of the input sources ends: after the parse in its block, the targets the parsed documents "
            "leave (row `resolve`: the targets the sources reserve, `_resolve_unparsed_json_pointer`, for JSON Schema, "
            "Avro, XML Schema and Protocol Buffers; OpenAPI's `_resolve_specification_targets`, which parses the "
            "models specialized for a dynamic scope first and the models that parameter models use last; AsyncAPI's "
            "`_resolve_asyncapi_unparsed_json_pointer`, which reads each target in the document of its context), then "
            "`_end_schema_parsing`, and on every path the release of the inherited-field state "
            "(`_clear_inherited_field_caches`) and of the local source cache"
        ),
        homes=(Home("parser/jsonschema.py", "JsonSchemaParser._parsing_session"),),
        core="`parser/jsonschema.py:JsonSchemaParser._parsing_session`",
        adapters=(
            "the parsers' `parse_raw`: JSON Schema, Avro and XML Schema "
            "(`parser/_schema_documents.py:parse_converted_sources`), OpenAPI and its `api` scope, Protocol Buffers, "
            "AsyncAPI"
        ),
        sites=(Home("parser/_schema_documents.py", "parse_converted_sources"),),
    ),
    DecisionHome(
        decision=(
            "how a cache of a run or of the process bounds what it keeps: a map of the most recently used keys, where "
            "a read of a key it holds makes that key the most recently used (unless the reader refuses the value, as a "
            "stale XML Schema entry) and a write drops the least recently used keys past the bound the caller gives, "
            "returning what it dropped; a cache that keeps a value only from the second sighting of its key marks the "
            "keys it saw in such a map"
        ),
        homes=(
            Home("_caches.py", "BoundedLru"),
            Home("_caches.py", "BoundedLru.recent"),
            Home("_caches.py", "BoundedLru.put"),
            Home("_caches.py", "SeenKeys.mark"),
        ),
        core="`_caches.py`: `BoundedLru` (`recent`, `put`), `SeenKeys.mark`",
        adapters=(
            "`_source.py`: `_load_parser_source_data_from_path_bytes`, `_load_cached_parser_source_data`, "
            "`_store_parser_source_data`; `parser/xmlschema.py:_load_xml_schema_data_from_path`; "
            "`http.py:_HTTPFetchSession`: `get_ips_from_host`, `get_response` (which closes the clients it drops); "
            "`model/base.py:_refresh_custom_template_paths` (which forgets the dependencies of the directories it "
            "drops); left out: `_runtime/protocols/cache_stores.py:_Entries`, which generated packages copy and which "
            "never imports the generator"
        ),
        sites=(
            Home("_source.py", "_load_parser_source_data_from_path_bytes"),
            Home("_source.py", "_load_cached_parser_source_data"),
            Home("_source.py", "_store_parser_source_data"),
            Home("parser/xmlschema.py", "_load_xml_schema_data_from_path"),
            Home("http.py", "_HTTPFetchSession"),
            Home("http.py", "_HTTPFetchSession.get_ips_from_host"),
            Home("http.py", "_HTTPFetchSession.get_response"),
            Home("model/base.py", "_refresh_custom_template_paths"),
            Home("_runtime/protocols/cache_stores.py", "_Entries"),
        ),
    ),
    DecisionHome(
        decision=(
            "which nodes a walk from some starts reaches, and in which order: a stack walk that takes the node pushed "
            "last first (the last start first), takes a node unless it took one of the same key before (the node "
            "itself, or its key, such as a model's identity or a name), and pushes the nodes the visit of the node "
            "returns, in their order; a walk that needs only the set of nodes reads the same walk"
        ),
        homes=(Home("_graph.py", "reach"),),
        core="`_graph.py:reach`",
        adapters=(
            "`_reference_graph.py`: `ReferenceGraph.reached`, `ReferenceGraph.preorder`, `walk_graph`; "
            "`_recommend/_signals.py`: `_reachable`, `_walk_components`; "
            "`parser/_passes/plans.py:flatten_conflicting_base_layouts`; "
            "`parser/graphql.py:GraphQLParser._resolve_typename_collisions`; "
            "`_client/target.py:_HelperDigests.contract`; `input_model.py:_native_generic_field_paths`; "
            "`model/pydantic_v2/base_model.py:has_lookaround_pattern` (the models whose patterns a Pydantic dataclass "
            "reads, row `legacy-dataclass-follows-every-reference`)"
        ),
        sites=(
            Home("_reference_graph.py", "ReferenceGraph.reached"),
            Home("_reference_graph.py", "ReferenceGraph.preorder"),
            Home("_reference_graph.py", "walk_graph"),
            Home("_recommend/_signals.py", "_reachable"),
            Home("_recommend/_signals.py", "_walk_components"),
            Home("parser/_passes/plans.py", "flatten_conflicting_base_layouts"),
            Home("parser/graphql.py", "GraphQLParser._resolve_typename_collisions"),
            Home("_client/target.py", "_HelperDigests.contract"),
            Home("input_model.py", "_native_generic_field_paths"),
            Home("model/pydantic_v2/base_model.py", "has_lookaround_pattern"),
        ),
    ),
    DecisionHome(
        decision=(
            "which nodes reach some seeds: the seeds and every node from which an edge, or a chain of edges, leads to "
            "one of them; each user gives the edges (the types of a model's fields and its bases, or its bases alone)"
        ),
        homes=(Home("_graph.py", "reaching"),),
        core="`_graph.py:reaching`",
        adapters=(
            "`model/pydantic_v2/base_model.py:models_reaching_string_pattern_aliases` (the models that reach a type "
            "alias keeping a string pattern); `_openapi_codec_plan.py:extras_symbols` (the models whose values reach a "
            "Pydantic dataclass that allows extra members); "
            "`parser/base.py:Parser.__get_resolve_reference_action_parts` (the models a rebuild call names: those with "
            "a forward reference and the models below them)"
        ),
        sites=(
            Home("model/pydantic_v2/base_model.py", "models_reaching_string_pattern_aliases"),
            Home("_openapi_codec_plan.py", "extras_symbols"),
            Home("parser/base.py", "Parser.__get_resolve_reference_action_parts"),
        ),
    ),
    DecisionHome(
        decision=(
            "which loaded document the file part of a reference key names: the document at that file (`_get_ref_body`, "
            "which a parser may extend, as OpenAPI indexes the discriminators of each document it loads), or the "
            "document being parsed when the part is empty"
        ),
        homes=(Home("parser/_schema_refs.py", "ref_file_document"),),
        core="`parser/_schema_refs.py:ref_file_document`",
        adapters=(
            "`parser/_schema_refs.py:read_ref_value` (the value a reference key names, the row below); "
            "`parser/jsonschema.py:JsonSchemaParser._ref_document_context`; "
            "`parser/openapi_scope.py:ApiOpenAPIParser._reference_target`"
        ),
        sites=(
            Home("parser/_schema_refs.py", "read_ref_value"),
            Home("parser/jsonschema.py", "JsonSchemaParser._ref_document_context"),
            Home("parser/openapi_scope.py", "ApiOpenAPIParser._reference_target"),
        ),
    ),
    DecisionHome(
        decision=(
            "the value a reference key names: the document its file part names (`ref_file_document`), or a tree the "
            "reader gives, its fragment split by the parser's reading (`SlashNames`) or by the legacy row of the "
            "reader (`PointerReading`), and walked by row `refusing`; the record of a prepared tree keeps it by "
            "fragment and reading (`parser/_schema_graph.py:SchemaGraphBuilder.pointer_value`), so every reader of one "
            "tree shares it and the walks take a key's node from it"
        ),
        homes=(
            Home("parser/_schema_refs.py", "read_ref_value"),
            Home("parser/_schema_documents.py", "ref_value_in"),
            Home("parser/_schema_reading.py", "ref_fragment_tokens"),
        ),
        core=(
            "`parser/_schema_refs.py:read_ref_value` (in the loaded document; with `as_schema`, as a schema); "
            "`parser/_schema_documents.py:ref_value_in` (in a given tree); "
            "`parser/_schema_reading.py:ref_fragment_tokens`"
        ),
        adapters=(
            "`parser/_schema_refs.py`: `key_node` and `parser/jsonschema.py:JsonSchemaParser._get_ref_raw_schema` (as "
            "a schema), `ref_schema_exists`, `ref_cycle_document` (row `legacy-cycle-through-scalar`); "
            "`parser/openapi.py:OpenAPIParser.get_ref_model` (row `legacy-split-pointer-tokens`); "
            "`parser/asyncapi.py:AsyncAPIParser._resolve_asyncapi_ref` (row `legacy-drop-empty-tokens`, in the "
            "document of its context, through `ref_fragment_tokens`: it parses at the tokens too); the readers of a "
            "given tree: `parser/jsonschema.py:JsonSchemaParser._parse_all_of_single_ref` (row "
            "`legacy-all-of-ref-in-parsed-document`, without `--read-single-allof-refs-in-their-documents`; with it, "
            "`read_ref_value`), `parser/_schema_refs.py`: `dynamic_ref_edges`, `parse_dynamic_specialization` (the "
            "prepared tree), `parser/_schema_documents.py`: `lenient_pointer_exists` (the lenient tree), "
            "`reserved_targets` (the prepared tree); left out: the readers of the parts of a key's pointer, split by "
            "the parser's reading (`parser/_schema_documents.py:ref_pointer_parts`, the lookup and the reference parts "
            "of `_split_json_pointer`): `parser/jsonschema.py:JsonSchemaParser`: `_parse_pointer_ref`, "
            "`_parse_base_stand_in` and `resolve_ref` (for `_parse_file`), which parse at them, and "
            "`parser/_schema_refs.py:recursive_ref_target` (tokens only); the `api` scope (`parser/openapi_scope.py`, "
            "canonical tokens)"
        ),
        sites=(
            Home("parser/_schema_refs.py", "key_node"),
            Home("parser/jsonschema.py", "JsonSchemaParser._get_ref_raw_schema"),
            Home("parser/_schema_refs.py", "ref_schema_exists"),
            Home("parser/_schema_refs.py", "ref_cycle_document"),
            Home("parser/openapi.py", "OpenAPIParser.get_ref_model"),
            Home("parser/asyncapi.py", "AsyncAPIParser._resolve_asyncapi_ref"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_all_of_single_ref"),
            Home("parser/_schema_refs.py", "read_ref_value"),
            Home("parser/_schema_refs.py", "dynamic_ref_edges"),
            Home("parser/_schema_refs.py", "parse_dynamic_specialization"),
            Home("parser/_schema_documents.py", "lenient_pointer_exists"),
            Home("parser/_schema_documents.py", "reserved_targets"),
            Home("parser/_schema_documents.py", "ref_pointer_parts"),
            Home("parser/jsonschema.py", "JsonSchemaParser"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_pointer_ref"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_base_stand_in"),
            Home("parser/jsonschema.py", "JsonSchemaParser.resolve_ref"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_file"),
            Home("parser/_schema_refs.py", "recursive_ref_target"),
            Home("parser/openapi_scope.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the directory references from a loaded document resolve from: the document's directory, read from the "
            "file system once per parse, or none for a URL document; and the current base path a context sets again or "
            "the resolver's base path enters, set without reading the file system again (the resolver's base path "
            "joined to itself, read once per parse)"
        ),
        homes=(
            Home("parser/_schema_refs.py", "schema_document_directory"),
            Home("parser/_schema_documents.py", "base_path_context"),
        ),
        core="`parser/_schema_refs.py:schema_document_directory`, `parser/_schema_documents.py:base_path_context`",
        adapters=(
            "`parser/_schema_refs.py:schema_document_context` (`ModelResolver.document_context`; "
            "`parser/jsonschema.py:JsonSchemaParser._ref_document_context`, `_ref_file_context`, `resolve_ref`, "
            "`_home_context`), `parser/_schema_documents.py:source_context` (the input sources), "
            "`parser/asyncapi.py:AsyncAPIParser._resolve_asyncapi_ref`, "
            "`parser/openapi.py:OpenAPIParser._process_path_items`; `base_path_context`: "
            "`parser/_schema_refs.py:parse_ref_sites`, `AsyncAPIParser._asyncapi_context`, "
            "`JsonSchemaParser._parse_object_common_part`, `OpenAPIParser._get_discriminator_union_type`, "
            "`OpenAPIParser._load_references_of_parameter_models`; not this rule: the public "
            "`ModelResolver.current_base_path_context`, which resolves its argument on every call"
        ),
        sites=(
            Home("parser/_schema_refs.py", "schema_document_context"),
            Home("parser/jsonschema.py", "JsonSchemaParser._ref_document_context"),
            Home("parser/jsonschema.py", "JsonSchemaParser._ref_file_context"),
            Home("parser/jsonschema.py", "JsonSchemaParser.resolve_ref"),
            Home("parser/jsonschema.py", "JsonSchemaParser._home_context"),
            Home("parser/_schema_documents.py", "source_context"),
            Home("parser/asyncapi.py", "AsyncAPIParser._resolve_asyncapi_ref"),
            Home("parser/openapi.py", "OpenAPIParser._process_path_items"),
            Home("parser/_schema_documents.py", "base_path_context"),
            Home("parser/_schema_refs.py", "parse_ref_sites"),
            Home("parser/asyncapi.py", "AsyncAPIParser._asyncapi_context"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_object_common_part"),
            Home("parser/openapi.py", "OpenAPIParser._get_discriminator_union_type"),
            Home("parser/openapi.py", "OpenAPIParser._load_references_of_parameter_models"),
        ),
    ),
    DecisionHome(
        decision=(
            "how a schema refers to one of its definitions by name: `#/<key>/<name>`, with the key `definitions` or "
            "`$defs` and the name as the reference writes it (the converters write `definitions`, the schemas of input "
            "models and the rewritten local references `$defs`); a reader that tells such a reference by its start "
            "reads the same text without a name"
        ),
        homes=(Home("_json_schema.py", "definition_ref"),),
        core="`_json_schema.py:definition_ref`",
        adapters=(
            "`parser/avro.py:_AvroSchemaConverter._ref`, "
            "`parser/protobuf.py:_ProtobufDescriptorConverter._reference_schema`, "
            "`parser/xmlschema.py:_XMLSchemaConverter` (`_ref`, `_inherited_content_restriction`), "
            "`parser/asyncapi.py:_move_json_schema_definitions_to_components`; `_json_schema.py`: "
            "`_local_definition_ref_name`, `_rewrite_local_definition_ref`; `input_model.py`: "
            "`_transform_single_model_to_inheritance`, `_InputModelDefinitions.normalize`, `_load_model_schema`; "
            "`parser/jsonschema.py:JsonSchemaParser._resolve_type_import_from_defs`"
        ),
        sites=(
            Home("parser/avro.py", "_AvroSchemaConverter._ref"),
            Home("parser/protobuf.py", "_ProtobufDescriptorConverter._reference_schema"),
            Home("parser/xmlschema.py", "_XMLSchemaConverter"),
            Home("parser/xmlschema.py", "_XMLSchemaConverter._ref"),
            Home("parser/xmlschema.py", "_XMLSchemaConverter._inherited_content_restriction"),
            Home("parser/asyncapi.py", "_move_json_schema_definitions_to_components"),
            Home("_json_schema.py", "_local_definition_ref_name"),
            Home("_json_schema.py", "_rewrite_local_definition_ref"),
            Home("input_model.py", "_transform_single_model_to_inheritance"),
            Home("input_model.py", "_InputModelDefinitions.normalize"),
            Home("input_model.py", "_load_model_schema"),
            Home("parser/jsonschema.py", "JsonSchemaParser._resolve_type_import_from_defs"),
        ),
    ),
    DecisionHome(
        decision=(
            "which JSON types the types a `type` keyword lists admit: the ones it lists, and `integer` where it lists "
            "`number`; the types two lists both admit, in the order of the first (a `number` of the first gives "
            "`integer` where the second admits only integers), and those of them that no other of them admits"
        ),
        homes=(Home("_json_schema.py", "admitted_types"),),
        core="`_json_schema.py:admitted_types`",
        adapters=(
            "`parser/_schema_reading.py:_numeric_type_domain`; "
            "`parser/_inherited_schemas.py:is_inherited_type_narrowing`; "
            "`parser/_schema_validators.py:_get_conditional_property_values` (which also reads an integral float as an "
            "integer); `parser/_intersection.py`: `intersect_types`, `common_types`; `_client/pagination.py:_fits`; "
            "left out: `_runtime/model_codecs/media.py`, which generated packages copy and which never imports the "
            "generator"
        ),
        sites=(
            Home("parser/_schema_reading.py", "_numeric_type_domain"),
            Home("parser/_inherited_schemas.py", "is_inherited_type_narrowing"),
            Home("parser/_schema_validators.py", "_get_conditional_property_values"),
            Home("parser/_intersection.py", "intersect_types"),
            Home("parser/_intersection.py", "common_types"),
            Home("_client/pagination.py", "_fits"),
            Home("_runtime/model_codecs/media.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "which types a `type` list holds other than `null`, in their order: each one it lists (a type listed twice "
            "counts twice where `has_multiple_types` counts them) or, with `distinct`, one of each (the types a "
            "heterogeneous root union under `--field-constraints` counts, and the one type beside `null` whose "
            "constraints a schema takes)"
        ),
        homes=(Home("_json_schema.py", "non_null_types"),),
        core="`_json_schema.py:non_null_types`",
        adapters=(
            "`parser/_schema_object.py:JsonSchemaObject.has_multiple_types`; `parser/jsonschema.py`: `_get_data_type`, "
            "`_build_constrained_type_union`, `_get_array_union_non_array_types`, "
            "`_should_skip_root_field_constraints_for_multiple_types`, `_iter_non_object_union_types`; "
            "`parser/_all_of_values.py`: `_non_null_types`, `handle_allof_root_model_with_constraints`, "
            "`all_of_site_schema`; `parser/_inherited_schemas.py:merge_inherited_type_shape_dict`; "
            "`parser/_intersection.py:_Reconciler._type`; `parser/_value_keywords.py`: "
            "`extract_const_enum_from_combined`, `has_effective_constraints`; left out: the tests of every listed type "
            "against a set that holds `null` (`parser/jsonschema.py`: `_has_shared_numeric_union_constraints`, "
            "`_get_type_array_data_type`; `parser/_schema_reading.py:_object_map_other_types`) and the set of a "
            "parent's types (`parser/_inherited_schemas.py`), which read no order; `_client/pagination.py`, which "
            "keeps `null` where the value writes it"
        ),
        sites=(
            Home("parser/_schema_object.py", "JsonSchemaObject.has_multiple_types"),
            Home("parser/jsonschema.py", "JsonSchemaParser._get_data_type"),
            Home("parser/jsonschema.py", "JsonSchemaParser._build_constrained_type_union"),
            Home("parser/jsonschema.py", "JsonSchemaParser._get_array_union_non_array_types"),
            Home("parser/jsonschema.py", "JsonSchemaParser._should_skip_root_field_constraints_for_multiple_types"),
            Home("parser/jsonschema.py", "JsonSchemaParser._iter_non_object_union_types"),
            Home("parser/_all_of_values.py", "_non_null_types"),
            Home("parser/_all_of_values.py", "handle_allof_root_model_with_constraints"),
            Home("parser/_all_of_values.py", "all_of_site_schema"),
            Home("parser/_inherited_schemas.py", "merge_inherited_type_shape_dict"),
            Home("parser/_intersection.py", "_Reconciler._type"),
            Home("parser/_value_keywords.py", "extract_const_enum_from_combined"),
            Home("parser/_value_keywords.py", "has_effective_constraints"),
            Home("parser/jsonschema.py", "JsonSchemaParser._has_shared_numeric_union_constraints"),
            Home("parser/jsonschema.py", "JsonSchemaParser._get_type_array_data_type"),
            Home("parser/_schema_reading.py", "_object_map_other_types"),
            Home("_client/pagination.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the type of a member of a `type` list other than `null` that is not the object of its own model: where "
            "the constraints of the schema are localized into the members, the annotated alias that takes those of "
            "them that apply to the member's type (where none apply, the type it takes otherwise); otherwise the map "
            "of an object member whose map values the run types, and any other member's type and format without "
            "constraints"
        ),
        homes=(Home("parser/jsonschema.py", "JsonSchemaParser._type_list_branch"),),
        core="`parser/jsonschema.py:JsonSchemaParser._type_list_branch`",
        adapters=(
            "`parser/jsonschema.py`: `_iter_non_object_union_types` (the members beside an object model or map), "
            "`_add_array_union_non_array_types` (the members beside an array), `_build_constrained_type_union` (the "
            "members of a type list that take annotated aliases, but an object with `propertyNames` or "
            "`patternProperties`); left out: the members beside an object map that keep their constraints "
            "(`_get_constrained_data_type`, as `get_data_type` maps the members of a type list)"
        ),
        sites=(
            Home("parser/jsonschema.py", "JsonSchemaParser._iter_non_object_union_types"),
            Home("parser/jsonschema.py", "JsonSchemaParser._add_array_union_non_array_types"),
            Home("parser/jsonschema.py", "JsonSchemaParser._build_constrained_type_union"),
            Home("parser/jsonschema.py", "JsonSchemaParser._get_constrained_data_type"),
            Home("parser/jsonschema.py", "JsonSchemaParser.get_data_type"),
        ),
    ),
    DecisionHome(
        decision=(
            "which models the passes leave out as unused, by identity: the passes between the module loop and "
            "unused-model removal leave out every unused model of the run (`run_unused_models`), the render "
            "preparation of a module the unused models it added, and the nested-default-factory decision and "
            "unused-model removal the unused models whose module writes the list they leave (`unused_model_lists`, "
            "which says whether it does)"
        ),
        homes=(
            Home("parser/_passes/_shared.py", "UnusedModels"),
            Home("parser/_passes/_shared.py", "run_unused_models"),
            Home("parser/_passes/_shared.py", "unused_model_lists"),
        ),
        core="`parser/_passes/_shared.py`: `UnusedModels`, `run_unused_models`, `unused_model_lists`",
        adapters=(
            "`parser/_passes/decide.py:decide` (the facts of the decision phase); `parser/_passes/modules.py`: "
            "`name_inner_models_in_rules`, `write_rule_aliases_as_classes`; `parser/_passes/render_prep.py`: "
            "`collect_live_models`, `prepare_type_alias_fields`, `decide_nested_model_default_factories`; "
            "`parser/_passes/imports.py:remove_unused_models`"
        ),
        sites=(
            Home("parser/_passes/decide.py", "decide"),
            Home("parser/_passes/modules.py", "name_inner_models_in_rules"),
            Home("parser/_passes/modules.py", "write_rule_aliases_as_classes"),
            Home("parser/_passes/render_prep.py", "collect_live_models"),
            Home("parser/_passes/render_prep.py", "prepare_type_alias_fields"),
            Home("parser/_passes/render_prep.py", "decide_nested_model_default_factories"),
            Home("parser/_passes/imports.py", "remove_unused_models"),
        ),
    ),
    DecisionHome(
        decision=(
            "how the client runtime reads received text: the bytes in the charset their raw content type names, UTF-8 "
            "when it names none or an unknown one, a byte that charset cannot decode raising `UnicodeDecodeError` for "
            "each reader to turn into its own failure"
        ),
        homes=(Home("_runtime/client/media.py", "charset_text"),),
        core="`_runtime/client/media.py:charset_text` (copied into every client package)",
        adapters=(
            "`_runtime/client/operations.py:_text` (a text response), `_runtime/client/raw.py:_Raw._text` (a raw "
            "response's text), `_runtime/client/multipart_responses.py:_typed_text` (a form-data part read as text)"
        ),
        sites=(
            Home("_runtime/client/operations.py", "_text"),
            Home("_runtime/client/raw.py", "_Raw._text"),
            Home("_runtime/client/multipart_responses.py", "_typed_text"),
        ),
    ),
    DecisionHome(
        decision=(
            "how the generator fetches the text of a URL input or of a remote reference: with the HTTP options of the "
            "config or the parser that asks for it (the headers each request sends, TLS verification, the query "
            "parameters of the first request, its timeout or the default one, whether a private network host may be "
            "named, the encoding the body is read in, and its HTTP backend unless a parser's session brings its own "
            "client stack and response observer)"
        ),
        homes=(Home("http.py", "_get_configured_body"),),
        core="`http.py:_get_configured_body`",
        adapters=(
            "`_generation.py:_prepare_generation_input` (a `--url` input, through the remote text cache), "
            "`_recommend/__init__.py:_read_input` (the `--url` input of `--recommend-options`), "
            "`parser/base.py:Parser._get_text_from_url` (a remote reference, through the parser's session and the "
            "remote text cache)"
        ),
        sites=(
            Home("_generation.py", "_prepare_generation_input"),
            Home("_recommend/__init__.py", "_read_input"),
            Home("parser/base.py", "Parser._get_text_from_url"),
        ),
    ),
    DecisionHome(
        decision=(
            "how an operation that replaced an entry of a process-wide mapping puts it back: the entry saved before "
            "(`mapping.get(key, ABSENT)`), or no entry when the mapping held none"
        ),
        homes=(
            Home("_process_state.py", "ABSENT"),
            Home("_process_state.py", "restore_entry"),
        ),
        core="`_process_state.py`: `ABSENT`, `restore_entry`",
        adapters=(
            "the modules a dynamic multi-module run registered (`dynamic.py:_execute_multi_module`), an input model "
            "module loaded from a path (`input_model.py:_restore_path_module`) and the importer cache entry of the "
            "working directory (`input_model.py:_cwd_import_scope`); left out: a single dynamic module, registered "
            "only under a name no module holds, which its run removes (`dynamic.py:_execute_single_module`), and the "
            "parent package attribute a failed formatter refresh puts back with `setattr` "
            "(`format.py:_restore_watch_package`)"
        ),
        sites=(
            Home("dynamic.py", "_execute_multi_module"),
            Home("input_model.py", "_restore_path_module"),
            Home("input_model.py", "_cwd_import_scope"),
            Home("dynamic.py", "_execute_single_module"),
            Home("format.py", "_restore_watch_package"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether an annotation of an input model names a class itself: no generic alias or other special form "
            "(`get_origin` gives none) and a `type`"
        ),
        homes=(Home("input_model.py", "_plain_class"),),
        core="`input_model.py:_plain_class`",
        adapters=(
            "the walks of `input_model.py` over annotations: `_reached_classes`, `_imported_python_type_expr`, "
            "`_pydantic_generic_metadata`, `_native_generic_field_paths` and `_exported_runtime_symbol`; not routed: "
            "the checks that take any class (`isinstance(tp, type)` alone, for an enum, a model or a TypedDict)"
        ),
        sites=(
            Home("input_model.py", "_reached_classes"),
            Home("input_model.py", "_imported_python_type_expr"),
            Home("input_model.py", "_pydantic_generic_metadata"),
            Home("input_model.py", "_native_generic_field_paths"),
            Home("input_model.py", "_exported_runtime_symbol"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether an object is a Pydantic model class: a `type` that subclasses `BaseModel`, `BaseModel` itself "
            "included"
        ),
        homes=(Home("input_model.py", "_model_class"),),
        core="`input_model.py:_model_class`",
        adapters=(
            "the models a walk of the classes an annotation reaches follows (`_reached_classes`), the type family of a "
            "model (`_get_type_family`), the model bases of a model (`_get_base_model_parents`, which leaves "
            "`BaseModel` itself out) and the input models a run loads (`load_model_schema`, "
            "`_load_single_model_schema`); not routed: the models of a generated module (`dynamic.py`), which are "
            "classes already"
        ),
        sites=(Home("dynamic.py"),),
    ),
    DecisionHome(
        decision=(
            "the family of a class of an input model: an enum, a Pydantic model, a class with typed fields that is no "
            "Pydantic model (a dataclass, of the Pydantic family where it is one of Pydantic; a TypedDict; a msgspec "
            "Struct), or another"
        ),
        homes=(
            Home("input_model.py", "_get_type_family"),
            Home("input_model.py", "_typed_fields_family"),
        ),
        core="`input_model.py`: `_get_type_family`, `_typed_fields_family` (the classes with typed fields)",
        adapters=(
            "the definitions a reference strategy reuses (`_filter_defs_by_strategy`) and those the merged roots name "
            "(`_InputModelDefinitions.normalize`), the classes a walk meets (`_reached_classes`, which reads the "
            "family once for each and yields it: `_collect_nested_models` records every family but the last, and the "
            "walk follows the classes with typed fields under `--reuse-every-reached-input-model-type`), and the "
            "dataclass or TypedDict a single `--input-model` loads (`_load_single_model_schema`, which refuses a "
            "msgspec Struct, for which Pydantic has no schema, and reads the classes with typed fields, so that an "
            "enum with a dataclass mixin loads as its dataclass does)"
        ),
        sites=(),
    ),
    DecisionHome(
        decision=(
            "the typed fields of a class of an input model: the fields Pydantic holds for it (the `model_fields` of a "
            "Pydantic model, asked of `_model_class`: an attribute of that name on another class is a field of its "
            "own; under `--read-input-model-dataclass-fields-by-alias` also those of a Pydantic dataclass) and the "
            "annotation of each field: of those fields for a Pydantic model, else the type hints of the class; a "
            "native field path reads the raw annotations of the class instead (its own only, `Annotated` kept, strings "
            "under `from __future__ import annotations`), as the generated code that evaluates the path does"
        ),
        homes=(
            Home("input_model.py", "_pydantic_fields"),
            Home("input_model.py", "_model_field_annotations"),
        ),
        core="`input_model.py`: `_pydantic_fields`, `_model_field_annotations`",
        adapters=(
            "the walks that follow the fields of a class (`_reached_classes`, `_collect_nested_models`, "
            "`_native_generic_field_paths`), the unserializable properties of a model "
            "(`_add_python_type_for_unserializable`) and the Python types of the properties of a definition "
            "(`_add_python_type_to_model_properties`, which pairs the Pydantic fields by wire name and reads a class "
            "without them by its type hints, keyed by field name); not routed: the fields of the models whose "
            "inheritance a load rewrites (`_partition_inherited_fields`, `_transform_single_model_to_inheritance`), "
            "which are Pydantic models already"
        ),
        sites=(),
    ),
    DecisionHome(
        decision=(
            "how an annotation of an input model is descended: the annotation, then each argument below it "
            "(`get_args`) in written order, with the argument indexes that lead to it; the metadata of an `Annotated` "
            "is an argument like any other"
        ),
        homes=(Home("input_model.py", "_annotation_nodes"),),
        core="`input_model.py:_annotation_nodes`",
        adapters=(
            "the classes an annotation names and those the fields of the models among them reach, each model followed "
            "where the walk first meets it (`_reached_classes`: the nested types of a model by name, of which a later "
            "class of a name replaces an earlier one, `_collect_nested_models`; the core identities of Pydantic 2.0, "
            "`_collect_core_type_identities`), the classes a Python type imports (`_imported_python_type_expr`), the "
            "arguments a reused generic expression respells, the first written of two that are spelled alike "
            "(`_reused_python_type_expression`), and the native field paths (`_native_generic_field_paths`, which "
            "follows the classes through `_graph.py:reach` and reads a field from its last argument to its first, row "
            "`legacy-native-path-last-argument-first`); not this rule: the walks through the arguments of a Pydantic "
            "specialization (`_has_annotated_generic_argument`, `_exported_generic_expression`) and the type "
            "expressions built over an annotation (`_preserved_python_type_expr`)"
        ),
        sites=(Home("_graph.py", "reach"),),
    ),
    DecisionHome(
        decision=(
            "the local path a file URI names (RFC 8089): the path of the URI, percent-decoded, a host other than "
            "`localhost` making it a UNC share, its query and fragment left out"
        ),
        homes=(Home("_paths.py", "file_uri_path"),),
        core="`_paths.py:file_uri_path`",
        adapters=(
            "the identity of a document a file URI names (`document_identity`) and the file a message names for a "
            "reference (`named_document`); the file the parser reads for a `file:` reference "
            "(`parser/jsonschema.py:JsonSchemaParser._get_ref_body_from_url`) and the path of a schema resource's file "
            "URI (`parser/_schema_documents.py:resolve_schema_resource_ref`)"
        ),
        sites=(
            Home("parser/jsonschema.py", "JsonSchemaParser._get_ref_body_from_url"),
            Home("parser/_schema_documents.py", "resolve_schema_resource_ref"),
        ),
    ),
    DecisionHome(
        decision=(
            "the exit code of each outcome of the command line: 0 done, 1 a difference found (a check or an input "
            "diff), 2 an error, 3 interrupted"
        ),
        homes=(Home("enums.py", "_ExitCode"),),
        core="`enums.py`: `_ExitCode` (`OK`, `DIFF`, `ERROR`, `INTERRUPTED`)",
        adapters=(
            "the members of `__main__.Exit`, and the ints a target run returns (`_target_cli.py`: `run_target`, "
            "`_run`), an Agent Skill installation returns (`_agent_skill_cli.py:install_agent_skill_command`, also on "
            "its fast path) and a JSON Schema printed on a fast path returns (`__main__.py:_print_json_schema`); not "
            "routed: the `sys.exit(0)` of the module-level fast paths of `__main__.py` (`--version`, `--help`, the "
            "prompt), which run before its import block"
        ),
        sites=(
            Home("_target_cli.py", "run_target"),
            Home("_target_cli.py", "_run"),
            Home("_agent_skill_cli.py", "install_agent_skill_command"),
            Home("__main__.py", "_print_json_schema"),
        ),
    ),
    DecisionHome(
        decision=(
            "the schema default the property a field is built from declares, as the field keeps it for default value "
            "overrides: its value, none declared (`_RAW_SCHEMA_DEFAULT_UNDEFINED`) or no record (a run without "
            "overrides, a field no property builds)"
        ),
        homes=(
            Home("parser/_passes/_keys.py", "raw_schema_default"),
            Home("parser/_passes/_keys.py", "declares_raw_schema_default"),
        ),
        core="`parser/_passes/_keys.py:raw_schema_default`, `declares_raw_schema_default`",
        adapters=(
            "the default an inherited field takes in the derived class scope "
            "(`parser/_passes/_shared.py:apply_inherited_field_default`), the field whose default an override of an "
            "inherited field takes (`inherited_default_source`); not routed: the writes of the record "
            "(`parser/jsonschema.py:JsonSchemaParser.get_object_field`, "
            "`parser/_field_copies.py:_copy_resolved_inherited_field`)"
        ),
        sites=(
            Home("parser/_passes/_shared.py", "apply_inherited_field_default"),
            Home("parser/_passes/_shared.py", "inherited_default_source"),
            Home("parser/jsonschema.py", "JsonSchemaParser.get_object_field"),
            Home("parser/_field_copies.py", "_copy_resolved_inherited_field"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether the schema at a key splits under `--read-only-write-only-model-type request-response`, as the "
            "parser caches it: one answer for both variants, `Request` and `Response`, which split together"
        ),
        homes=(Home("parser/_rw_split.py", "_cache_request_response_split"),),
        core="`parser/_rw_split.py:_cache_request_response_split`",
        adapters=(
            "the keys a reachability walk reaches with and without a variant "
            "(`request_response_ref_schema_generates_variant`) and a model that splits "
            "(`create_request_response_models`); not routed: a root model's split "
            "(`parser/jsonschema.py:JsonSchemaParser._register_root_model_as`), which records each variant after "
            "preparing it"
        ),
        sites=(Home("parser/jsonschema.py", "JsonSchemaParser._register_root_model_as"),),
    ),
    DecisionHome(
        decision=(
            "which JSON types a `type` keyword lists, in order: the one a string names, or the string items of a list, "
            "and none for any other value"
        ),
        homes=(Home("_json_schema.py", "declared_types"),),
        core="`_json_schema.py:declared_types`",
        adapters=(
            "the parser's reads of a schema's `type` (`parser/_intersection.py`, `_all_of_values.py`, "
            "`_inherited_schemas.py`, `_schema_validators.py`, `_value_keywords.py`, `jsonschema.py`), whose values "
            "the schema object has validated as strings; the types a schema declares to the recommender "
            "(`_recommend/_signals.py:_types`)"
        ),
        sites=(
            Home("_recommend/_signals.py", "_types"),
            Home("parser/_intersection.py"),
            Home("parser/_all_of_values.py"),
            Home("parser/_inherited_schemas.py"),
            Home("parser/_schema_validators.py"),
            Home("parser/_value_keywords.py"),
            Home("parser/jsonschema.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether a generation class is one of the built-ins: a model, field or type class that the model package "
            "defines (its module is under `datamodel_code_generator.model.`), not a custom one"
        ),
        homes=(Home("model/base.py", "_model_package_class"),),
        core="`model/base.py:_model_package_class`",
        adapters=(
            "whether every configured generation class of a parser is a built-in (`parser/base.py:Parser.__init__`, "
            "`_configured_generation_types_are_builtin`) and whether a template is rendered with its compiled renderer "
            "(`model/base.py:_builtin_renderer`); not this rule: whether a class is one of the package's at all "
            "(`model/_render_plan.py:_package_class`), a built-in Pydantic v2 model (`model/pydantic_v2/version.py`) "
            "and the backend modules the inheritance check names (`parser/_inheritance_check.py`)"
        ),
        sites=(
            Home("parser/base.py", "Parser.__init__"),
            Home("model/base.py", "_builtin_renderer"),
            Home("model/_render_plan.py", "_package_class"),
            Home("model/pydantic_v2/version.py"),
            Home("parser/_inheritance_check.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether a built-in template is rendered with its compiled renderer, whose output equals the Jinja output: "
            "the class is one of the built-ins and no custom template directory is set; a custom class or a custom "
            "template directory renders it with Jinja, and so does a model rendered with positional arguments"
        ),
        homes=(Home("model/base.py", "_builtin_renderer"),),
        core="`model/base.py:_builtin_renderer`",
        adapters=(
            "`DataModel._render_template` (the template of a model, whose custom template directory is its own) and "
            "`pydantic_v2/base_model.py:BaseModel._render_core_schema_runtime_validation_helpers` (the shared helpers "
            "of a module, whose custom template directory is the first one among its models); left out: the Jinja "
            "template a custom directory selects (`DataModel.template`)"
        ),
        sites=(Home("model/pydantic_v2/base_model.py", "BaseModel._render_core_schema_runtime_validation_helpers"),),
    ),
    DecisionHome(
        decision=(
            "the type a Python type expression spells: the expression bound for the target "
            "(`parser/_type_overrides.py:bind_python_type`: its names and the imports they take), written as the bound "
            "expression rendered, with the bound type as its `python_type`"
        ),
        homes=(Home("parser/_type_overrides.py", "python_type_data_type"),),
        core="`parser/_type_overrides.py:python_type_data_type`",
        adapters=(
            "`x-python-type` that the schema type does not take "
            "(`parser/jsonschema.py:JsonSchemaParser._get_python_type_override`, a union of any spelling under "
            "`--use-x-python-type-as-written`), an `x-python-import` that names a runtime symbol "
            "(`JsonSchemaParser.get_ref_data_type`) and a `--type-overrides` annotation (brackets, a union, or a bare "
            "builtin or registry name), which every parser applies (`parser/_type_overrides.py:override_annotation`, "
            "optional where the field it replaces is); not this rule: the import aliases a bound type takes later "
            "(`parser/_passes/_import_aliases.py:_alias_data_type_structured_imports` rewrites the type through the "
            "store)"
        ),
        sites=(
            Home("parser/jsonschema.py", "JsonSchemaParser._get_python_type_override"),
            Home("parser/jsonschema.py", "JsonSchemaParser.get_ref_data_type"),
            Home("parser/_type_overrides.py", "override_annotation"),
            Home("parser/_passes/_import_aliases.py", "_alias_data_type_structured_imports"),
        ),
    ),
    DecisionHome(
        decision=(
            "the arguments a code formatter of generated modules takes from the model settings: the target Python "
            "version, string wrapping, quotes (`skip_string_normalization` unless `--use-double-quotes`), the custom "
            "formatters and their arguments, the encoding, the formatters, the built-in line length, and whether "
            "imports used only for type checking move into `TYPE_CHECKING` blocks (unset, the model class and whether "
            "the output has several modules decide)"
        ),
        homes=(Home("format.py", "_model_formatter_arguments"),),
        core="`format.py:_model_formatter_arguments`",
        adapters=(
            "the formatter of a parse (`parser/base.py:Parser._build_code_formatter`, which a parser subclass may "
            "override), the deferred Ruff run over a written package (`_generation.py:_format_deferred_output`) and "
            "the "
            "formatter of a client or server package (`_api_generation.py:_Planner.finish`, a `TargetCodeFormatter`); "
            "each constructs the formatter itself and adds the settings path, the third-party packages and deferred "
            "formatting, and the formatter of a parse and of a client or server package also the formatter directory"
        ),
        sites=(
            Home("parser/base.py", "Parser._build_code_formatter"),
            Home("_generation.py", "_format_deferred_output"),
            Home("_api_generation.py", "_Planner.finish"),
        ),
    ),
    DecisionHome(
        decision=(
            "whether a name can name the shared schema validator base class (`--schema-validator-base-class-name`): a "
            "name the target Python binds (row `target` of `is_python_name`), or none given; the message when it cannot"
        ),
        homes=(Home("base_config.py", "_schema_validator_base_class_name_error"),),
        core="`base_config.py:_schema_validator_base_class_name_error`",
        adapters=(
            "for the newest target, the command line "
            "(`_cli_config.py:Config.validate_schema_validator_base_class_name`, which raises `Error`) and "
            "`generate()` and the parser config (`config.py:validate_schema_validator_base_class_name`, which raises "
            "`ValueError`); for the run's target, `Parser.__init__`, which raises `Error`"
        ),
        sites=(Home("config.py", "validate_schema_validator_base_class_name"),),
    ),
    DecisionHome(
        decision=(
            "whether a reference or an input names a URL, not a path: it starts with `https://`, `http://` or "
            "`file://`, the schemes the generator fetches"
        ),
        homes=(Home("_url_redaction.py", "is_url"),),
        core="`_url_redaction.py:is_url` (also `reference.is_url`)",
        adapters=(
            "the `--url` value the command line accepts (`_cli_config.py:Config.validate_url`) and the `--watch` input "
            "it refuses (`__main__.py`), the references the resolver fetches (`reference.py:ModelResolver`) and the "
            "parsers' remote references (`parser/_schema_documents.py`, `_schema_object.py`, `_schema_refs.py`, "
            "`jsonschema.py`, `openapi.py`, `asyncapi.py`)"
        ),
        sites=(
            Home("reference.py", "ModelResolver"),
            Home("__main__.py"),
            Home("parser/_schema_documents.py"),
            Home("parser/_schema_object.py"),
            Home("parser/_schema_refs.py"),
            Home("parser/jsonschema.py"),
            Home("parser/asyncapi.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the field whose default an override of an inherited field takes where its class defers the inherited "
            "default: the inherited field where `--allof-merge-mode` inherits `default` "
            "(`parser/_intersection.py:inherits_keyword`) and the override declares no default, else the override"
        ),
        homes=(Home("parser/_passes/_shared.py", "inherited_default_source"),),
        core="`parser/_passes/_shared.py:inherited_default_source`",
        adapters=(
            "the deferred overrides of a class that defers the inherited default "
            "(`parser/_rw_split.py:merge_inherited_field_overrides`), the overrides of an object's inherited fields "
            "its parse resolves (`parser/jsonschema.py:JsonSchemaParser._parse_object_common_part`, with default value "
            "overrides) and the inherited placeholders a pass replaces "
            "(`parser/_passes/_shared.py:override_required_field`); not this rule: an override whose own field defers "
            "the inherited default, which takes the inherited field's"
        ),
        sites=(
            Home("parser/_rw_split.py", "merge_inherited_field_overrides"),
            Home("parser/jsonschema.py", "JsonSchemaParser._parse_object_common_part"),
            Home("parser/_passes/_shared.py", "override_required_field"),
        ),
    ),
    DecisionHome(
        decision=(
            "the schema version a document is read by and its features (whether the keywords beside a `$ref` apply, "
            "`ref_sibling_keywords`: every draft but draft 4, 6 and 7, OpenAPI 3.1 and later, AsyncAPI 3, and `auto`): "
            "the configured version unless `auto`, else the one the document declares (`$schema`, `openapi`, "
            "`asyncapi`), else that of the document that loads it, else the document's own content; a JSON Schema "
            "document a specification loads keeps the specification's own fields"
        ),
        homes=(
            Home("parser/schema_version.py", "document_dialect"),
            Home("parser/schema_version.py", "DocumentDialect"),
            Home("parser/schema_version.py", "_DRAFT_FLAG_CHANGES"),
            Home("parser/schema_version.py", "configured_version"),
        ),
        core=(
            "`parser/schema_version.py:document_dialect` (`DocumentDialect`, the draft flags of `_DRAFT_FLAG_CHANGES`; "
            "`configured_version`, the configured version unless `auto`, which the Protocol Buffers and XML Schema "
            "parsers read too)"
        ),
        adapters=(
            "each loaded document's version, recorded in L0 (`parser/_schema_documents.py:record_document_dialect`); "
            "the readings of the parser (`parser/jsonschema.py:JsonSchemaParser._read_dialect`: `schema_features`, "
            "`_ref_sibling_keywords_enabled`, `_ref_siblings_are_parts`, read through `_features_at`, "
            "`_ref_siblings_apply_to` and `_document_schema_paths`, which under `--read-schema-version-per-document` "
            "read the version of the document a schema object is in (`_dialect_at`), and keep row "
            "`legacy-first-read-dialect` without it); under `--ignore-ref-siblings-by-schema-version`, a `$ref` in a "
            "version that ignores the keywords beside it read as its target alone where its document object is "
            "validated (`parser/_schema_object.py:JsonSchemaObject.read_ref_alone_where_its_siblings_are_ignored`, "
            "through `JsonSchemaParser._ignores_ref_siblings_at` in the validation context), except in a schema "
            "converted from Avro, XML Schema or Protocol Buffers (`SchemaGraphBuilder.is_converted`, recorded in L0 "
            "where the converted document or payload enters the graph); the one the recommender reads "
            "(`_recommend/_signals.py:_dialect`); the version the AsyncAPI client reads channels by and the OpenAPI "
            "client's root version (`_target_binding.py:_recorded_version`)"
        ),
        sites=(
            Home("parser/_schema_documents.py", "record_document_dialect"),
            Home("parser/jsonschema.py", "JsonSchemaParser._read_dialect"),
            Home("parser/jsonschema.py", "JsonSchemaParser.schema_features"),
            Home("parser/jsonschema.py", "JsonSchemaParser._ref_sibling_keywords_enabled"),
            Home("parser/jsonschema.py", "JsonSchemaParser._ref_siblings_are_parts"),
            Home("parser/jsonschema.py", "JsonSchemaParser._features_at"),
            Home("parser/jsonschema.py", "JsonSchemaParser._ref_siblings_apply_to"),
            Home("parser/jsonschema.py", "JsonSchemaParser._dialect_at"),
            Home("parser/_schema_object.py", "JsonSchemaObject.read_ref_alone_where_its_siblings_are_ignored"),
            Home("parser/jsonschema.py", "JsonSchemaParser._ignores_ref_siblings_at"),
            Home("_recommend/_signals.py", "_dialect"),
            Home("_target_binding.py", "_recorded_version"),
        ),
    ),
    DecisionHome(
        decision="which fields of an AsyncAPI 2 channel hold an operation, in order: `publish`, then `subscribe`",
        homes=(Home("_document_reader.py", "CHANNEL_OPERATIONS"),),
        core="`_document_reader.py:CHANNEL_OPERATIONS`",
        adapters=(
            "the channel walk of generation (`parser/asyncapi.py:AsyncAPIParser._iter_channel_schemas`, through the "
            "public `OPERATION_NAMES`) and the recommender's message schemas and operation count "
            "(`_recommend/_signals.py`: `_message_schemas`, `_count_operations`), and the AsyncAPI client's reading of "
            "a channel's messages (`_asyncapi_connections.py:_Reader.v2_messages`); not this rule: the AsyncAPI 3 "
            "`operations` map, which the recommender counts by its keys"
        ),
        sites=(
            Home("parser/asyncapi.py", "AsyncAPIParser._iter_channel_schemas"),
            Home("_recommend/_signals.py", "_message_schemas"),
            Home("_recommend/_signals.py", "_count_operations"),
            Home("_asyncapi_connections.py", "_Reader.v2_messages"),
        ),
    ),
    DecisionHome(
        decision=(
            'what a read of nested schemas ends in when it exceeds the Python recursion limit: the error "Schema '
            'nesting is too deep to generate", for a document whose schemas nest too deep and for one that holds a '
            "schema in itself through a YAML alias"
        ),
        homes=(Home("_document_reader.py", "nesting_limit"),),
        core="`_document_reader.py:nesting_limit`",
        adapters=(
            "the parse of generation (`parser/_passes/settle.py:parse_raw`) and the signals of the recommender "
            "(`_recommend/__init__.py:_measure`)"
        ),
        sites=(
            Home("parser/_passes/settle.py", "parse_raw"),
            Home("_recommend/__init__.py", "_measure"),
        ),
    ),
    DecisionHome(
        decision=(
            "which schema a media type object holds, and whether it is its `itemSchema`: the `schema`, else the "
            "`itemSchema` of OpenAPI 3.2; a key set to null holds nothing, so `schema: null` reads the `itemSchema` as "
            "an absent `schema` does"
        ),
        homes=(Home("parser/openapi.py", "OpenAPIParser._pick_media_schema"),),
        core="`parser/openapi.py:OpenAPIParser._pick_media_schema`",
        adapters=(
            "the validated media reader (`OpenAPIParser._get_media_schema`: the request bodies and responses of an "
            "operation, a content parameter) and the raw media reader (`OpenAPIParser._get_raw_media_schema`: the "
            "request-bodies scope and the API walk of `parser/openapi_scope.py`)"
        ),
        sites=(Home("parser/openapi_scope.py"),),
    ),
    DecisionHome(
        decision=(
            "whether an AsyncAPI value is a Multi Format Schema Object, the wrapper of a schema in its `schema`: a "
            "mapping with a `schemaFormat`, or one whose keys other than `x-` extensions and keys that are no string "
            "are `schema` alone; an OpenAPI or JSON Schema document has no such wrapper, so a `schema` key there is an "
            "unknown keyword"
        ),
        homes=(Home("_document_reader.py", "is_multi_format_schema_object"),),
        core="`_document_reader.py:is_multi_format_schema_object`",
        adapters="the schema and format a wrapper holds (`_document_reader.py:held_schema`)",
        sites=(Home("_document_reader.py", "held_schema"),),
    ),
    DecisionHome(
        decision=(
            "the schema an AsyncAPI Multi Format Schema Object holds and the `schemaFormat` it is read in: its "
            "`schema`, in its own `schemaFormat`, else in the one it inherits (a message's, for its payload), so an "
            "empty or null `schemaFormat` inherits too"
        ),
        homes=(Home("_document_reader.py", "held_schema"),),
        core="`_document_reader.py:held_schema`",
        adapters=(
            "the schemas generation reads (`parser/asyncapi.py:AsyncAPIParser._iter_schema_format_schemas`, which "
            "refuses a wrapper without a `schema` or with a `schemaFormat` that is no string, "
            "`_check_multi_format_schema_object`), the schemas the recommender measures "
            "(`_recommend/_signals.py:_unwrap`: the component schemas of an AsyncAPI document, and the payloads and "
            "headers of its messages), and the `schemaFormat` of the payload a client codes "
            "(`_asyncapi_connections.py:_Reader.message`; where its schema is, the walk records)"
        ),
        sites=(
            Home("parser/asyncapi.py", "AsyncAPIParser._iter_schema_format_schemas"),
            Home("parser/asyncapi.py", "_check_multi_format_schema_object"),
            Home("_recommend/_signals.py", "_unwrap"),
            Home("_asyncapi_connections.py", "_Reader.message"),
        ),
    ),
    DecisionHome(
        decision=(
            "the order an AsyncAPI operation or message and its traits apply in, the strongest last: the traits in "
            "their order, then the object, whose own fields win; under AsyncAPI 2, which merges each trait into the "
            "object by JSON Merge Patch, the object first, so each trait's fields win (row `legacy-own-headers-win-v2` "
            "keeps the message's own headers winning in the models under both versions)"
        ),
        homes=(Home("_document_reader.py", "trait_layers"),),
        core="`_document_reader.py:trait_layers`",
        adapters=(
            "the connection reader's layers of an operation or a message (`_asyncapi_connections.py:_layers`, read for "
            "the HTTP bindings by `_bound` and for the merged fields by `_traited`) and the merged message headers of "
            "`--merge-message-trait-headers` (`parser/asyncapi.py:AsyncAPIParser._message_headers`)"
        ),
        sites=(
            Home("_asyncapi_connections.py", "_layers"),
            Home("_asyncapi_connections.py", "_bound"),
            Home("_asyncapi_connections.py", "_traited"),
            Home("parser/asyncapi.py", "AsyncAPIParser._message_headers"),
        ),
    ),
    DecisionHome(
        decision=(
            "the media type an AsyncAPI `schemaFormat` or `contentType` names: its text before any `;` parameter, "
            "stripped and lowercased, so `application/vnd.apache.avro+json;version=1.9.0` names "
            "`application/vnd.apache.avro+json`"
        ),
        homes=(Home("_document_reader.py", "schema_format_media_type"),),
        core="`_document_reader.py:schema_format_media_type`",
        adapters=(
            "the kind of a `schemaFormat` (`parser/asyncapi.py:schema_format_kind`) and the essence of a client "
            "message's `contentType` (`_asyncapi_connections.py:_media`, `_streams`)"
        ),
        sites=(
            Home("parser/asyncapi.py", "schema_format_kind"),
            Home("_asyncapi_connections.py", "_media"),
            Home("_asyncapi_connections.py", "_streams"),
        ),
    ),
    DecisionHome(
        decision=(
            "the kind of schema an AsyncAPI `schemaFormat` names: the kind `SCHEMA_FORMAT_KIND_BY_MEDIA_TYPE` gives "
            "its media type (`asyncapi`, `jsonschema`, `openapi`, `avro`, `protobuf` or `xmlschema`), `asyncapi` where "
            "none is given, and none for a media type the table does not list"
        ),
        homes=(Home("parser/asyncapi.py", "schema_format_kind"),),
        core="`parser/asyncapi.py:schema_format_kind`",
        adapters=(
            "the format generation reads an embedded schema in, which refuses a format of no kind "
            "(`parser/asyncapi.py:AsyncAPIParser._iter_schema_format_schemas`), the Avro schemas the recommender "
            "converts (`_recommend/_signals.py:_unwrap`), and for a client the format of a message's payload, whose "
            "kind is coded only where generation reads its schema in place (`_asyncapi_connections.py:_CODED_FORMATS`)"
        ),
        sites=(
            Home("parser/asyncapi.py", "AsyncAPIParser._iter_schema_format_schemas"),
            Home("_recommend/_signals.py", "_unwrap"),
        ),
    ),
    DecisionHome(
        decision=(
            "the array schema OpenAPI 3.2 `itemSchema` stands for: an array whose items are the item schema, for the "
            "media type object a document is read from as a validated object and as a raw one"
        ),
        homes=(Home("parser/openapi.py", "_item_schema_array"),),
        core="`parser/openapi.py:_item_schema_array`",
        adapters=(
            "the validated media reader (`parser/openapi.py:OpenAPIParser._array_schema_from_media_item_schema`, for "
            "`_get_media_schema`) and the raw media reader (`OpenAPIParser._get_raw_media_schema`, which the "
            "request-bodies scope and the API walk of `parser/openapi_scope.py` read); not routed: "
            "`_acquire_item_schema` of `parser/openapi_scope.py` and `_target_binding.py`, which project the stream "
            "array over a generated item declaration"
        ),
        sites=(
            Home("parser/openapi.py", "OpenAPIParser._array_schema_from_media_item_schema"),
            Home("parser/openapi.py", "OpenAPIParser._get_media_schema"),
            Home("parser/openapi.py", "OpenAPIParser._get_raw_media_schema"),
            Home("parser/openapi_scope.py"),
            Home("_target_binding.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "the data type a media type object's schema parses into: the schema `_get_media_schema` reads (its "
            "`schema`, or the array `itemSchema` stands for), parsed at the path of the media type, the special path "
            "of `itemSchema` for that array, with the location of a boolean schema; none for a media type with no "
            "schema"
        ),
        homes=(Home("parser/openapi.py", "OpenAPIParser._parse_media_schema"),),
        core="`parser/openapi.py:OpenAPIParser._parse_media_schema`",
        adapters=(
            "the request bodies (`OpenAPIParser.parse_request_body`) and the responses "
            "(`OpenAPIParser.parse_responses`) of an operation; not routed: the content of a parameter "
            "(`OpenAPIParser.parse_all_parameters`), which takes the schema as the field's own and parses it as an item"
        ),
        sites=(),
    ),
    DecisionHome(
        decision=(
            "how the walk reads a declared schema: as the value it declares (`value`) or as the array of the items of "
            "a stream (`item_stream_array`, the `itemSchema` of a media type)"
        ),
        homes=(Home("_site_facts.py", "Projection"),),
        core="`_site_facts.py:Projection`",
        adapters=(
            "`parser/openapi_scope.py`: `ApiDeclarationFrame.projection`, `ApiOpenAPIParser._schema_frame`; "
            "`_target_contract.py:TypeUseId.projection`; `_target_binding.py` (the acquisitions and schema uses by "
            "location)"
        ),
        sites=(
            Home("parser/openapi_scope.py", "ApiOpenAPIParser._schema_frame"),
            Home("_target_binding.py"),
        ),
    ),
    DecisionHome(
        decision=(
            "where the walk finds a declared schema: among the schemas of a document (`schema`), in a parameter "
            "(`parameter`), in a request or a response body (`request_body`, `response_body`), in a response header "
            "(`response_header`) or in a header of the encoding of a request or a response body "
            "(`request_encoding_header`, `response_encoding_header`)"
        ),
        homes=(Home("_site_facts.py", "TypeUseRole"),),
        core="`_site_facts.py:TypeUseRole`",
        adapters=(
            "`parser/openapi_scope.py`: `ApiDeclarationFrame.role`, `ApiOpenAPIParser._schema_frame` and the walk "
            "methods that give a role (`SchemaRole`, the name the module keeps for it); "
            "`_target_contract.py:TypeUseId.role`; `_target_binding.py` (the record of an acquired schema, "
            "`TargetApiOpenAPIParser._schema_record`, and the role of each use by location); not this rule: "
            "`_json_schema.py:SchemaRole`, what a subschema keyword does"
        ),
        sites=(
            Home("parser/openapi_scope.py", "ApiOpenAPIParser._schema_frame"),
            Home("_target_binding.py", "TargetApiOpenAPIParser._schema_record"),
        ),
    ),
    DecisionHome(
        decision=(
            "the reference a discriminator mapping value names: a value with a `/` or a `#` is a reference or a path "
            "and stays as it is, any other value is a schema name of `#/components/schemas/` (a bare file name is a "
            "schema name too)"
        ),
        homes=(Home("_document_reader.py", "discriminator_mapping_ref"),),
        core="`_document_reader.py:discriminator_mapping_ref`",
        adapters=(
            "the discriminator mappings the parser reads (`parser/openapi.py:OpenAPIParser._normalize_discriminator`, "
            "`_discriminator_mapping_ref`; AsyncAPI inherits them) and the document a FastAPI server serves "
            "(`_fastapi/source_document.py:SourceDocument.discriminator_target`, which keeps the name of the component "
            "a schema name takes there)"
        ),
        sites=(
            Home("parser/openapi.py", "OpenAPIParser._normalize_discriminator"),
            Home("parser/openapi.py", "OpenAPIParser._discriminator_mapping_ref"),
            Home("_fastapi/source_document.py", "SourceDocument.discriminator_target"),
        ),
    ),
    DecisionHome(
        decision=(
            "the directory the documents that a JSON option value names resolve against: the directory of the JSON "
            "file it names (a relative file joined to the given directory, the directory of a link kept as given), "
            "else the directory its settings come from (the working directory for options, the pyproject.toml "
            "directory for its keys)"
        ),
        homes=(Home("json_config.py", "document_base"),),
        core="`json_config.py:document_base`",
        adapters=(
            "the target settings of the command line (`_target_cli.py:_base`, read by "
            "`_target_selection.py:target_of`) and the bases `generate()` takes from a loaded project configuration "
            "(`_project_config.py:load_pyproject_config`, kept for `_target_selection.py:generate_selected_target`); "
            "not this rule: a config given to `generate()` without a project file, whose documents resolve against the "
            "caller's working directory"
        ),
        sites=(
            Home("_target_cli.py", "_base"),
            Home("_target_selection.py", "target_of"),
            Home("_project_config.py", "load_pyproject_config"),
            Home("_target_selection.py", "generate_selected_target"),
        ),
    ),
    DecisionHome(
        decision=(
            "what both JSON reports of a target record of it: its package, the runtime dependencies of the generated "
            "project and the settings the run implied"
        ),
        homes=(Home("_target_cli.py", "_payload_fields"),),
        core="`_target_cli.py:_payload_fields`",
        adapters=(
            "the report of `--check` and `--diff-against` (`_target_payload`, a `TargetPayload`) and the generation "
            "report (`_target_json`, a `GeneratedTargetPayload`, which adds the README, the entrypoint, the "
            "implementations and the usage)"
        ),
        sites=(),
    ),
    DecisionHome(
        decision=(
            "which `generate()` field takes the value of a command-line option whose field it names differently: "
            "`use_default` is `apply_default_values_for_required_fields`, `force_optional` is "
            "`force_optional_for_required_fields` and `default_values` is `default_value_overrides`"
        ),
        homes=(Home("_option_names.py", "GENERATE_FIELD_NAMES"),),
        core="`_option_names.py:GENERATE_FIELD_NAMES`",
        adapters=(
            "the project keys and the set fields of a loaded project configuration "
            "(`_project_config.py:load_pyproject_config`) and the config a command-line run generates with "
            "(`__main__.py:_generation_config`, where the run's own `default_value_overrides` replaces the one the "
            "option gives), and the `generate()` field the playground names for an option "
            "(`scripts/build_playground_assets.py`: `config_dest`)"
        ),
        sites=(
            Home("_project_config.py", "load_pyproject_config"),
            Home("__main__.py", "_generation_config"),
        ),
    ),
    DecisionHome(
        decision="whether a `--url` input allows the remote references it names: an unset `--allow-remote-refs` is on",
        homes=(Home("__main__.py", "_imply_remote_refs"),),
        core="`__main__.py:_imply_remote_refs`",
        adapters=(
            "the command-line run (`__main__.py:_run_config`) and the trial configurations of `--recommend-options` "
            "(`__main__.py:_recommend_generation_config`)"
        ),
        sites=(
            Home("__main__.py", "_run_config"),
            Home("__main__.py", "_recommend_generation_config"),
        ),
    ),
    DecisionHome(
        decision=(
            "the words of a recommendation that its JSON report spells as the recommender does: the mode (`quick` or "
            "`full`), where the input was read from (`file`, `url`, `stdin`, `directory` or `input-model`), a config "
            "value as pyproject.toml spells it (a bool, a string or a list of strings) and why a configured value "
            "stays although a rule suggests another (`explicit`, `preset` or `newer-preset`)"
        ),
        homes=(
            Home("_recommend_vocabulary.py", "RecommendMode"),
            Home("_recommend_vocabulary.py", "InputSource"),
            Home("_recommend_vocabulary.py", "OptionValue"),
            Home("_recommend_vocabulary.py", "DisagreementKind"),
        ),
        core="`_recommend_vocabulary.py`: `RecommendMode`, `InputSource`, `OptionValue`, `DisagreementKind`",
        adapters=(
            "the recommender (`_recommend/__init__.py`, `_recommend/_rules.py`, `_recommend/_trial.py`) and the "
            "payloads of its JSON report (`_structured_output.py`: `RecommendInputPayload`, `RecommendOptionsPayload`, "
            "`RecommendCandidatePayload`, `RecommendOptionPayload`, `RecommendDisagreementPayload`); the leaf stays "
            "outside `_recommend/`, as importing the report would run the recommender's modules (13% of printing the "
            "JSON Schema of the structured output); left out: the modes the argument parser accepts "
            "(`_option_table.py:_recommend_mode`, which keeps its own copy)"
        ),
        sites=(
            Home("_structured_output.py", "RecommendInputPayload"),
            Home("_structured_output.py", "RecommendOptionsPayload"),
            Home("_structured_output.py", "RecommendCandidatePayload"),
            Home("_structured_output.py", "RecommendOptionPayload"),
            Home("_structured_output.py", "RecommendDisagreementPayload"),
            Home("_option_table.py", "_recommend_mode"),
            Home("_recommend/__init__.py"),
            Home("_recommend/_rules.py"),
            Home("_recommend/_trial.py"),
        ),
    ),
)

BUILDER_HOMES: Final[tuple[BuilderHome, ...]] = (
    BuilderHome(
        output="the parse of a definition, a root, a reserved target, an AsyncAPI payload or the `api` scope",
        prose=(
            "`_DEFINITION_TABLE` in `_parse_schema_obj` (definitions, roots, reserved targets, AsyncAPI payloads and "
            "the `api` scope)"
        ),
        builder=Home("parser/jsonschema.py", "JsonSchemaParser._parse_schema_obj"),
        table=Home("parser/jsonschema.py", "JsonSchemaParser._DEFINITION_TABLE"),
    ),
    BuilderHome(
        output="the parse of a field, an item or a map value",
        prose="`_FIELD_TABLE` in `parse_item` (fields, items and map values)",
        builder=Home("parser/jsonschema.py", "JsonSchemaParser.parse_item"),
        table=Home("parser/jsonschema.py", "JsonSchemaParser._FIELD_TABLE"),
    ),
    BuilderHome(
        output="the parse of a body, a response or a parameter of the legacy OpenAPI scopes",
        prose=(
            "`_OPENAPI_OPERATION_TABLE` in `OpenAPIParser.parse_schema` (the bodies, responses and parameters of the "
            "legacy scopes)"
        ),
        builder=Home("parser/openapi.py", "OpenAPIParser.parse_schema"),
        table=Home("parser/openapi.py", "OpenAPIParser._OPENAPI_OPERATION_TABLE"),
    ),
    BuilderHome(
        output="the parse of an object-kind schema",
        prose=(
            "the rows of an object-kind schema (`_OBJECT_KIND_ROWS`) are shared by the definition table and the "
            "object-kind table that `_parse_object_kind_schema` chooses from"
        ),
        builder=Home("parser/jsonschema.py", "JsonSchemaParser._parse_object_kind_schema"),
        table=Home("parser/jsonschema.py", "JsonSchemaParser._OBJECT_KIND_TABLE"),
    ),
    BuilderHome(
        output="the dict of an object map",
        prose="is built by one builder at every position (`_parse_object_map`)",
        builder=Home("parser/jsonschema.py", "JsonSchemaParser._parse_object_map"),
        marks=(Mark(keyword="is_dict", value=True), Mark(callee="_container_flags", argument=(1, "dict"))),
        parts=(
            Home("parser/_object_maps.py", "parse_additional_properties_map"),
            Home("parser/_object_maps.py", "parse_pattern_map"),
            Home("parser/_object_maps.py", "pattern_map_type"),
            Home("parser/jsonschema.py", "JsonSchemaParser.parse_property_names"),
            Home("parser/_object_maps.py", "parse_property_names"),
        ),
        within="parser/",
    ),
    BuilderHome(
        output="the reference that names a model",
        prose="Names come from `ModelResolver.add`.",
        builder=Home("reference.py", "ModelResolver.add"),
        marks=(
            Mark(callee="datamodel_code_generator.reference.Reference"),
            Mark(callee="datamodel_code_generator.reference.Reference.model_construct"),
            Mark(callee="datamodel_code_generator.reference.Reference.model_validate"),
        ),
        parts=(Home("reference.py", "ModelResolver._register_reference"),),
    ),
)
"""The builder homes of the L1 prose, guarded by ``tests/test_decision_homes.py``."""

PLACEMENTS: Final[tuple[Placement, ...]] = (
    Placement(
        reads="raw keywords of one schema object (`const`, `enum`, `nullable`, `uniqueItems`, extension keys)",
        facts=frozenset(),
        place="L1 at that object, or record them on the field for a later pass",
    ),
    Placement(
        reads="names settled by `--model-name-map` or yielding names",
        facts=frozenset({RenderFact.CLASS_NAMES}),
        place="after L2 `settle_yielding_names`",
        after=("settle_yielding_names",),
    ),
    Placement(
        reads="the names of the models once every model has claimed one (the naming plan)",
        facts=frozenset({RenderFact.CLASS_NAMES, RenderFact.PLANS}),
        place="after L2 `name_models`",
        after=("name_models",),
    ),
    Placement(
        reads="field set, required or nullable of a model in the same module, without discriminator tags",
        facts=frozenset({Fact.FIELD_SET, Fact.REQUIRED, Fact.NULLABLE}),
        place="after that module's `set_one_literal_on_default`",
        after=("set_one_literal_on_default",),
    ),
    Placement(
        reads="a fact of a model in another module that the module loop can still change",
        facts=frozenset(),
        place="L6, after the module loop (facts that settle earlier, such as mapped names, follow their own row)",
    ),
    Placement(
        reads="discriminator tags or msgspec ClassVars",
        facts=frozenset({Fact.DISCRIMINATOR_TAGS}),
        place="L6, after `tag_fields_of_tagged_bases`",
        after=("tag_fields_of_tagged_bases",),
    ),
    Placement(
        reads="type overrides",
        facts=frozenset({Fact.TYPE_OVERRIDES, Fact.FIELD_TYPES, Fact.BASES}),
        place=(
            "field types of a model in the same module: after that module's `apply_field_type_overrides`; of a model "
            "in another module: after the module loop; base classes: after the model's "
            "`apply_base_class_type_overrides`"
        ),
        after=("apply_field_type_overrides", "apply_base_class_type_overrides"),
    ),
    Placement(
        reads="bases including the generic base class",
        facts=frozenset({Fact.BASES}),
        place="L6, after `apply_generic_base_class`",
        after=("apply_generic_base_class",),
    ),
    Placement(
        reads="class names",
        facts=frozenset({RenderFact.CLASS_NAMES}),
        place="L6, after `change_imported_model_name`",
        after=("change_imported_model_name",),
    ),
    Placement(
        reads="where a model comes from in its document",
        facts=frozenset(),
        place="after L1, through `location_of(model.reference)`, never from `Reference.path` or a parse path",
    ),
    Placement(
        reads="imports or names written in annotations",
        facts=frozenset({RenderFact.MODULE_IMPORTS, RenderFact.IMPORT_ALIASES, RenderFact.CLASS_NAMES}),
        place="end of `_finalize_modules`, or at render",
    ),
    Placement(
        reads="the target Python or Pydantic version a generated construct needs",
        facts=frozenset(),
        place=(
            "any pass: read its row in `_requirements.py` (`python_target_meets`, `pydantic_target_meets`, or "
            "`model_target_meets` for the target recorded on a Pydantic model); a new construct gets a row instead "
            "of a version comparison"
        ),
    ),
)
"""The rows of the placement table of the Placement Guide, in its order."""
