"""The ``legacy-*`` rows that ``src/`` names: what each keeps, what fixes it, and the run that keeps it.

A ``legacy-*`` row names an exception that keeps the output of an earlier release. One ``LegacyRow`` per row that
``src/datamodel_code_generator`` names, in the order of the "Legacy rows" table of ``docs/architecture.md``, which
``scripts/build_architecture_docs.py`` writes from ``LEGACY_ROWS``. ``tests/test_legacy_rows.py`` checks that the rows
here are the rows ``src/`` names, no more and no fewer.
"""

from __future__ import annotations

from typing import Final, Literal, NamedTuple, TypeAlias

__all__ = ["KEEP", "LEGACY_ROWS", "UNTESTED", "Fix", "LegacyRow"]

KEEP: Final = "keep"
"""The fix of a row whose output stays the default for good."""
Fix: TypeAlias = int | Literal["keep"]
"""What deletes a row: the number of the issue whose fix does, or ``KEEP``."""


class LegacyRow(NamedTuple):
    """One ``legacy-*`` row: its name, what it keeps, its fix and the run whose output keeps it."""

    name: str
    keeps: str
    """What the row keeps, as the Markdown of its cell in the page table."""
    fix: Fix
    witness: tuple[str, ...]
    """The ``datamodel-codegen`` arguments, without ``--output``, of the smallest input under ``tests/data`` whose
    generation reaches the row, with the row's option off."""
    option: str | None = None
    """The option that writes the new form instead, or None where none does."""


LEGACY_ROWS: Final[tuple[LegacyRow, ...]] = (
    LegacyRow(
        "legacy-exact-ints-as-floats",
        "the schema models read an int constraint as a float where one equals it and is written as it",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/root_alias_constraints/null_numeric.json",
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--use-root-model-type-alias",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-default-values-skip-true-properties",
        (
            "without `--apply-default-values-to-true-properties`, a property whose schema is `true` takes no default "
            "from `--default-values`, in the class that declares it and in a child that restates an inherited "
            "property as `true` (`parser/jsonschema.py:JsonSchemaParser._true_property_default_state`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/default_values_true_properties/settings.json",
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--default-values",
            '{"Settings.retry-count": 3}',
            "--formatters",
            "builtin",
        ),
        option="--apply-default-values-to-true-properties",
    ),
    LegacyRow(
        "legacy-generic-base-drops-configured-bases",
        (
            "without `--keep-bases-beside-generic-base-class`, the generated base class of `--use-generic-base-class` "
            "replaces the bases of a model none of whose bases is a generated model, so a class that "
            "`--base-class-map`, `customBasePath` or `--base-class` names, or that an `--external-ref-mapping` or "
            "`x-python-import` schema the model extends stands for, is not inherited and its import stays unused "
            "(`parser/_passes/cross_module.py:_bases_beside_generic`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/generic_base_class_bases/api.json",
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--use-generic-base-class",
            "--extra-fields",
            "forbid",
            "--base-class-map",
            '{"Tag": "mypkg.base.TagBase"}',
            "--formatters",
            "builtin",
        ),
        option="--keep-bases-beside-generic-base-class",
    ),
    LegacyRow(
        "legacy-standard-primitive-types-without-decoder",
        (
            "without `--use-str-for-formats-msgspec-cannot-decode`, `--use-standard-primitive-types` writes "
            "`IPv4Address`, `IPv6Address`, `IPv4Network`, `IPv6Network` and `Path` for the formats `ipv4`, `ipv6`, "
            "`ipv4-network`, `ipv6-network` and `path` of msgspec output, which msgspec decodes only with a `dec_hook` "
            "(`types.py:DataTypeManager.use_str_for_formats_msgspec_cannot_decode`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/use_standard_primitive_types.json",
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--output-model-type",
            "msgspec.Struct",
            "--use-standard-primitive-types",
            "--formatters",
            "builtin",
        ),
        option="--use-str-for-formats-msgspec-cannot-decode",
    ),
    LegacyRow(
        "legacy-msgspec-float-bounds",
        (
            "without `--read-msgspec-union-bounds-exactly`, the check of a msgspec union reads the bound of a float "
            "member as the float it is, so an integral bound past `2**53` is the integer of its binary value "
            "(`int(1e300)`), where the schema models read the decimal that is written (`10**300`), and the int member "
            "of a union can keep a constraint that rejects an integer the float member accepts"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/msgspec_number_limits/union_integer_bounds.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--output-model-type",
            "msgspec.Struct",
        ),
        option="--read-msgspec-union-bounds-exactly",
    ),
    LegacyRow(
        "legacy-mapped-tags-only",
        (
            "without `--use-unmapped-variant-tag-values`, a discriminated variant a mapping key names takes only its "
            "mapping keys, not the other values its tag field admits that no key and no other variant claim "
            "(`model/_decisions/tag_value.py:variant_tags`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/discriminator_mapped_variant_several_values.json",
            "--input-file-type",
            "jsonschema",
            "--preset",
            "standard-py312-20261113",
            "--disable-timestamp",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-validate-default-for-root-model-type-aliases",
            "--read-ref-siblings-in-closed-allof-members",
            "--formatters",
            "builtin",
        ),
        option="--use-unmapped-variant-tag-values",
    ),
    LegacyRow(
        "legacy-tag-type-from-earlier-union",
        (
            "without `--read-mapping-keys-by-schema-tag-type`, a variant's tags are read against the tag field it "
            "declares, which an earlier union may have written as its `Literal`, else the nearest one it inherits "
            "from an ancestor whose tag field no union wrote, so a mapping key reads as text or as an integer by the "
            "order of the unions, and as text where the field names a root model or a type alias of an integer "
            "(`parser/_tag_plan.py:_declared_type`, `declared_tag_type`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/variant_tag_values/mapping_key_after_union.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--read-mapping-keys-by-schema-tag-type",
    ),
    LegacyRow(
        "legacy-nested-enum-members-untagged",
        (
            "under `--use-enum-values-in-discriminator`, a variant that a union lists through a nested discriminated "
            "union and whose tag field holds the members of an Enum takes no tag, so that union stays a plain union, "
            "as in 0.97.0 (`model/_decisions/tag_value.py:variant_tags`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/variant_tag_values/nested_union_of_several_values.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
    ),
    LegacyRow(
        "legacy-branch-own-tags",
        (
            "without `--infer-union-discriminators`, msgspec output reads the tag of each branch of a union only from "
            "the branch or the schema its local `$ref` names, never from its `allOf` members, and counts a null "
            "branch, so such a union stays untagged and warns that msgspec cannot decode it "
            "(`parser/_union_tags.py:UnionTagReading`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/serde_tagged_draft7.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "msgspec.Struct",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--infer-union-discriminators",
    ),
    LegacyRow(
        "legacy-rw-parsed-facts",
        (
            "without `--dedupe-request-response-variants`, the read/write facts and graph node of a key come from a "
            "temporary parse of its fields, the walk reads every reference of a key, and every model collects its "
            "inherited fields to decide its split"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/read_only_write_only_external_root_inline_object/main.json",
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "request-response",
            "--formatters",
            "builtin",
        ),
        option="--dedupe-request-response-variants",
    ),
    LegacyRow(
        "legacy-rw-unowned-parent-fields",
        (
            "without `--dedupe-request-response-variants`, the inline models of an allOf parent's own fields are "
            "parsed at no owner path"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/read_only_write_only_external_root_inline_object/main.json",
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "request-response",
            "--formatters",
            "builtin",
        ),
        option="--dedupe-request-response-variants",
    ),
    LegacyRow(
        "legacy-rw-base-reads-imported-fields",
        (
            "without `--use-x-python-import-for-every-ref`, under request-response an allOf base splits the model that "
            "inherits it where the base reaches a readOnly or writeOnly property through a field that an import types: "
            "the walk reads a base, and the base readers of the split read it, with every field naming its schema's "
            "model (`_rw_split.imported_field_reading`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/python_import_inherited_request_response.yaml",
            "--input-file-type",
            "openapi",
            "--read-only-write-only-model-type",
            "request-response",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--use-x-python-import-for-every-ref",
    ),
    LegacyRow(
        "legacy-python-import-modelled-targets",
        (
            "without `--use-x-python-import-for-every-ref`, the import types the sites that name a schema with "
            "`x-python-import` as their one class only where the schema got no model, read as that class by the "
            "definitions of its document; a schema that a JSON pointer or a whole-document `$ref` parsed into a model "
            "keeps it at those sites"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/python_import_inherited_request_response.yaml",
            "--input-file-type",
            "openapi",
            "--read-only-write-only-model-type",
            "request-response",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--use-x-python-import-for-every-ref",
    ),
    LegacyRow(
        "legacy-rw-allof-before-custom-type",
        (
            "the request/response walk reads a root model whose schema holds an allOf beside a `$ref`, a "
            "`customTypePath` or a type list whose branches take annotated aliases as built from its allOf (the models "
            "of the allOf's lightweight type, not its members), where the parser builds its root type from the `$ref`, "
            "the custom type or the type list (`_root_type_row`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/legacy_rows/request_response_all_of_ref.yaml",
            "--input-file-type",
            "openapi",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--read-only-write-only-model-type",
            "request-response",
            "--use-annotated",
        ),
    ),
    LegacyRow(
        "legacy-rw-extra-items-as-written",
        (
            "the request/response walk reads the TypedDict extra_items type of additionalProperties as written, not "
            "with its false allOf values read as `set_additional_properties` reads them"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/official_schema_pass/map.avsc",
            "--input-file-type",
            "avro",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-merge-row",
        (
            "a `$ref` with siblings and an inherited property override are the earlier merge (`concatenating_row` / "
            "`inherited_override_row`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/schema_intersection/enum_refs_null.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--intersect-allof-keywords",
    ),
    LegacyRow(
        "legacy-document-order",
        (
            "without `--read-allof-parents-in-mro-order`, the C3 merge of an inherited schema reads the allOf parents "
            "of each schema in document order (`inherited_schema_parents`), so with `allOf: [A, B]` and `B = allOf: "
            "[A, ...]` it takes the farther base `A` first and a property both declare takes `A`'s declaration, not "
            "the nearer base's as the class does"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/allof_inherited_property_nearer_base.json",
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--read-allof-parents-in-mro-order",
    ),
    LegacyRow(
        "legacy-dfs-inline-first",
        (
            "without `--read-inherited-property-schemas-in-mro-order`, whether some ancestor declares a property that "
            "a model only requires (`get_inherited_field_schema`, a row of `walk_inherited_schemas`) is read from the "
            "members of each allOf as written, depth first from each base: a schema, its inline members with their own "
            "references, then the schemas its `$ref` members name. It is no order of its own: it answers as row `c3` "
            "does wherever `all_of_members` reads the members as written. Under "
            "`--intersect-combined-schema-keywords`, where the property map follows an alias member to the schema its "
            "`$ref` names (`alias_target`) and reads the keywords beside a `$ref` member, it does neither, so a model "
            "written before its parents writes such a field without its description or constraints, or as `Any`, and "
            "not as it does when written after them. Where the base that leads to the declaration is dropped as a root "
            "model (row `legacy-ref-member-union-dropped`, without `--distribute-allof-over-unions`), a declaration "
            "that is found defers to a field no base has and the field is not written (#5305), so under "
            "`--intersect-combined-schema-keywords` the field of a declaration behind an alias member, which this row "
            "writes with the type `_get_inherited_field_type` finds, is not written when row `c3` is asked. The option "
            "asks row `c3`; once that is the only reading and row `legacy-ref-member-union-dropped` is gone, "
            "`_walk_inline_first` and `JsonSchemaParser._inherited_declaration_row` go"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/inherited_property_schemas/sibling.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--intersect-combined-schema-keywords",
        ),
        option="--read-inherited-property-schemas-in-mro-order",
    ),
    LegacyRow(
        "legacy-direct-then-grandparents",
        (
            "the type of a property a model only requires and `get_inherited_field_schema` finds no declaration of "
            "(`_get_inherited_field_type`, a row of `walk_inherited_schemas`) is read from the generated fields of the "
            "bases, then of the parents of the first base whose allOf names parents (registering their references), "
            "one level per call: so a model written before its parent takes the type of a field that only the model of "
            "a parent's parent holds, such as one that a `$ref` beside keywords or a conditional schema gives it, and "
            "only along the first base that has parents. On the way the property each base declares itself, as written "
            "without its inline members, is typed too: without `--read-inherited-property-schemas-in-mro-order` that "
            "is the type, without its description, of a declaration `legacy-dfs-inline-first` missed; under the option "
            "row `c3` has found every such declaration"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/inherited_property_schemas/sibling.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--intersect-combined-schema-keywords",
        ),
        option="--read-inherited-property-schemas-in-mro-order",
    ),
    LegacyRow(
        "legacy-ref-sibling-parent-read-alone",
        (
            "without `--read-allof-parents-as-their-models`, the readers of the schemas a model inherits "
            "(`model_schema_at`) read a parent that is a `$ref` with sibling keywords by its sibling keywords alone, "
            "while its model is built from their merge with the schema its `$ref` names: a model written before such "
            "a parent types a property it only requires `Any`, a property it narrows `Any`, and leaves optional a "
            "property the `$ref` target requires (#6868)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/allof_parents_as_models/ref_sibling_parents.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
        ),
        option="--read-allof-parents-as-their-models",
    ),
    LegacyRow(
        "legacy-inherited-required-names-unwritten",
        (
            "without `--read-allof-parents-as-their-models`, a model writes no field for a name that only the schemas "
            "it inherits require (`JsonSchemaParser._optional_inherited_required_names`), so the class takes the field "
            "from a base as the output collects fields, optional where that base declares it optional: `allOf: "
            "[PetBase, Named]` with only `Named` requiring `name` accepts a payload without `name` (#7331)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/allof_parents_as_models/required_names.yaml",
            "--input-file-type",
            "openapi",
            "--formatters",
            "builtin",
        ),
        option="--read-allof-parents-as-their-models",
    ),
    LegacyRow(
        "legacy-root-site-members-as-written",
        (
            "without `--read-allof-parents-as-their-models`, the allOf root model row "
            "(`handle_allof_root_model_with_constraints`) reads the members as written, not as `all_of_members` reads "
            "them, so under `--intersect-combined-schema-keywords` an allOf of a `$ref` alias of an object model and "
            "a member that adds only a `type` or `required` names is a root model of a dict, without the fields of "
            "the schema the alias names (#7309)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/allof_parents_as_models/alias_parent.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--intersect-combined-schema-keywords",
        ),
        option="--read-allof-parents-as-their-models",
    ),
    LegacyRow(
        "legacy-inherited-reads-at-first-ask",
        (
            "the ancestors of an inherited schema (`get_inherited_schema_ancestor_paths`), the C3 order of a list of "
            "bases (`linearize_inherited_schema_refs`) and the names that order requires "
            "(`get_inherited_required_names`) keep the answer of their first ask until L1 ends, whatever document asks "
            "later. Where an option makes `all_of_members` read the version of the document being parsed, the document "
            "that asks first decides them for all: under `--intersect-combined-schema-keywords`, with a base whose "
            "allOf holds a `$ref` beside `required` and `properties`, the model of a draft-07 document that inherits "
            "the base requires those names (the property the draft-07 document redeclares is required) when a 2020-12 "
            "document inheriting it is parsed before, and not when the draft-07 document is parsed first"
        ),
        fix=7197,
        witness=(
            "--input",
            "tests/data/legacy_rows/inherited_reads_at_first_ask",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--intersect-combined-schema-keywords",
        ),
    ),
    LegacyRow(
        "legacy-lookahead-patterns",
        "patterns are joined by lookaheads with no warning; an allOf member merge keeps the later pattern",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/schema_intersection/ref_siblings_oas30.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--intersect-allof-keywords",
    ),
    LegacyRow(
        "legacy-all-of-site-form",
        (
            "allOf value sites keep their earlier form with no conflict warning (a model without fields only where "
            "their parts admit an object), the root field of a site whose parts some value meets writing their "
            "keywords (`_all_of_values.legacy_site_keywords`); a `$ref` member's sibling `enum`/`const` does not "
            "narrow its literals"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/schema_intersection/enum_refs_null.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--intersect-allof-keywords",
    ),
    LegacyRow(
        "legacy-all-of-null-as-written",
        (
            "null is read as written where allOf parts meet (null members beside a `type` without null, an allOf map "
            "member's `type` keeps its null, a `None` merged enum value fits)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/schema_intersection/enum_refs_null.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--intersect-allof-keywords",
    ),
    LegacyRow(
        "legacy-combined-branch-row",
        (
            "without `--intersect-combined-schema-keywords`, a branch of an `anyOf` or `oneOf` is its merge with the "
            "keywords beside it, the branch's value of a keyword winning (`all_of_member_row` without bounds)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/inherited_required_names/items.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--intersect-combined-schema-keywords",
    ),
    LegacyRow(
        "legacy-enum-outside-type-kept",
        (
            "without `--intersect-combined-schema-keywords`, an `enum` value that the merged `type` rejects stays "
            "where parts meet"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/inherited_required_names/alias.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
        ),
        option="--intersect-combined-schema-keywords",
    ),
    LegacyRow(
        "legacy-schema-maps-merged-as-schemas",
        (
            "without `--intersect-combined-schema-keywords`, an inherited property override (and an allOf of maps) "
            "merges a `properties` or `patternProperties` map as a schema, so names spelled like a keyword the merge "
            "mode does not inherit, or `nullable`, are dropped"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/inherited_property_overrides/binary_values.yaml",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--intersect-combined-schema-keywords",
    ),
    LegacyRow(
        "legacy-type-narrowing-override-deferred",
        (
            "without `--intersect-combined-schema-keywords`, a partial override without constraints is a copy of the "
            "inherited field, which keeps the inherited type and drops at most its null, also where the override "
            "narrows the type further"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/inherited_required_names/deep.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--intersect-combined-schema-keywords",
    ),
    LegacyRow(
        "legacy-type-shape-length-from-max-items",
        (
            "without `--intersect-combined-schema-keywords`, the type shape of `--allof-merge-mode none` keeps the "
            "length of a tuple only from an explicit `maxItems` equal to `minItems`"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/schema_intersection/inherited_tuple_shape_oas31.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
            "--allof-merge-mode",
            "none",
        ),
        option="--intersect-combined-schema-keywords",
    ),
    LegacyRow(
        "legacy-all-of-ref-siblings-ignored",
        (
            "without `--intersect-combined-schema-keywords`, an allOf member that is a `$ref` with sibling keywords is "
            "read as the `$ref` alone, also where the dialect reads the siblings as parts"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/inherited_required_names/alias.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
        ),
        option="--intersect-combined-schema-keywords",
    ),
    LegacyRow(
        "legacy-all-of-alias-member-base",
        (
            "without `--intersect-combined-schema-keywords`, an allOf member naming a schema that is only a `$ref` "
            "takes that alias as its base, which later passes drop as a root model"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/inherited_required_names/alias.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
        ),
        option="--intersect-combined-schema-keywords",
    ),
    LegacyRow(
        "legacy-all-of-union-null-dropped",
        (
            "without `--intersect-combined-schema-keywords`, the union of the variants an allOf member's `anyOf` or "
            "`oneOf` gives admits no null, also where every part admits it"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/schema_intersection/combined_option_edges_draft04.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "dataclasses.dataclass",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--intersect-combined-schema-keywords",
    ),
    LegacyRow(
        "legacy-all-of-required-names-dropped",
        (
            "without `--intersect-combined-schema-keywords`, a field, an item or a branch whose allOf names one model "
            "and adds only `required` names to it, through a member or beside the allOf, is that model, which does not "
            "require them (`JsonSchemaParser._adds_required_names`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/schema_intersection/ref_siblings_oas30.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--intersect-combined-schema-keywords",
    ),
    LegacyRow(
        "legacy-ref-branch-without-site-parts",
        (
            "without `--distribute-allof-over-unions`, a `$ref` branch of an `anyOf` or `oneOf` takes the `properties` "
            "and `required` beside it, not the allOf members (which an inline branch takes) nor the value keywords "
            "beside it, and the subtypes of a discriminated `$ref` branch take none of the allOf members"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/allof_union_through_ref_files/loop.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--distribute-allof-over-unions",
    ),
    LegacyRow(
        "legacy-operation-all-of-before-union",
        (
            "without `--distribute-allof-over-unions`, an OpenAPI request or response body with an allOf beside an "
            "`anyOf` or `oneOf` is parsed by its allOf and the union is dropped"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/schema_intersection/allof_beside_union_oas31.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
            "--openapi-scopes",
            "schemas",
            "paths",
        ),
        option="--distribute-allof-over-unions",
    ),
    LegacyRow(
        "legacy-union-parts-joined",
        (
            "without `--distribute-allof-over-unions`, every `anyOf` and `oneOf` of an allOf is parsed at the site's "
            "path and their branches join one union, each with the other parts; a discriminated `$ref` branch of them "
            "is left out"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/allof_union_through_ref_files/loop.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--distribute-allof-over-unions",
    ),
    LegacyRow(
        "legacy-ref-member-union-dropped",
        (
            "without `--distribute-allof-over-unions`, an allOf member naming an allOf that holds an `anyOf` or "
            "`oneOf` is a base, which later passes drop as a root model"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/allof_union_through_ref_files/loop.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--distribute-allof-over-unions",
    ),
    LegacyRow(
        "legacy-value-union-members-unread",
        (
            "without `--distribute-allof-over-unions`, the value branches of an allOf's `anyOf` and `oneOf` are read "
            "only where every combination admits no object and the site writes a model: a field writes `Any`, a field "
            "of one `$ref` member that type, and an allOf of object branches leaves them out"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/allof_union_through_ref_files/loop.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--distribute-allof-over-unions",
    ),
    LegacyRow(
        "legacy-union-branch-of-union-as-one",
        (
            "without `--distribute-allof-over-object-branches`, a branch of an allOf's `anyOf` or `oneOf` that is "
            "itself a union, by a `$ref` or written there, or an alias of another schema, is one model "
            "(`JsonSchemaParser._branch_models`): a root model that later passes drop from the variant's bases, so the "
            "variant checks none of its fields, or no variant at all for an inline union (#6869); it warns"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/allof_union_object_branches/other_document/model.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
        ),
        option="--distribute-allof-over-object-branches",
    ),
    LegacyRow(
        "legacy-union-variant-required-names-inherited",
        (
            "without `--distribute-allof-over-object-branches`, a union variant of an allOf takes a name that another "
            "part requires as its bases hold it (`JsonSchemaParser._union_model_restated_names`): optional where its "
            "branch declares it optional, and `Any` where the class of the other members holds it first (#6867)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/allof_union_object_branches/required_names.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
        ),
        option="--distribute-allof-over-object-branches",
    ),
    LegacyRow(
        "legacy-all-of-object-merge-row",
        (
            "without `--intersect-merged-allof-members`, allOf members merged into one object are their merge row: of "
            "a property several of them declare, the later `type`, `nullable` and `multipleOf` win, two type lists "
            "join and a later `true` replaces the schema, and the allOf members of a member are dropped"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/allof_merged_member_values.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--intersect-merged-allof-members",
    ),
    LegacyRow(
        "legacy-all-of-root-first-typed-member",
        (
            "without `--intersect-merged-allof-members`, a named allOf of one `$ref` to a value schema and value "
            "members is the first typed of them with the constraints of all, the `enum` of another member dropped"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/allof_enum_member_of_constrained_ref.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--intersect-merged-allof-members",
    ),
    LegacyRow(
        "legacy-all-of-enum-root-self-alias",
        (
            "without `--intersect-merged-allof-members`, a named allOf whose merged root is an enum written as a class "
            "names itself where no output reads the annotations (standard dataclasses), which later passes type `Any`"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/allof_enum_member_of_constrained_ref.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "dataclasses.dataclass",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--intersect-merged-allof-members",
    ),
    LegacyRow(
        "legacy-all-of-map-counts-dropped",
        (
            "without `--intersect-merged-allof-members`, an allOf of a `$ref` to a map and members that add only "
            "property counts or `propertyNames` is a model without fields at a definition, and the reference elsewhere"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/allof_map_member_counts.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--intersect-merged-allof-members",
    ),
    LegacyRow(
        "legacy-all-of-base64-reference-layered",
        (
            "without `--intersect-merged-allof-members`, a length or `pattern` an allOf site adds to a `$ref` to "
            "base64 text is written beside the reference, checked on the decoded value"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/allof_base64_member_constraints.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--field-constraints",
            "--use-encoded-str-for-constrained-base64",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--intersect-merged-allof-members",
    ),
    LegacyRow(
        "legacy-map-union-of-dicts",
        (
            "without `--use-single-dict-for-object-maps`, patternProperties give a union of dicts, one per rendered "
            "value type, with no inexact-map warning (msgspec, which cannot decode it, gets the one dict)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/schema_intersection/allof_4716.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--use-single-dict-for-object-maps",
    ),
    LegacyRow(
        "legacy-map-pattern-keys-only",
        (
            "without `--use-single-dict-for-object-maps`, an additionalProperties schema beside patternProperties "
            "types no key; the dict admits the keys of its patterns only"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/schema_intersection/allof_4716.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--use-single-dict-for-object-maps",
    ),
    LegacyRow(
        "legacy-map-null-left-to-field",
        (
            "without `--use-single-dict-for-object-maps`, the dict of an object map takes neither the null of its type "
            "array or `nullable` nor its property counts, which stay on the field (a type array of object and null is "
            "a union holding the dict); only the typed values of `--use-typed-nullable-map-values` make one dict, "
            "optional where its type array lists null, joined with the other types"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/type_union_constraints/keys_root.json",
            "--input-file-type",
            "jsonschema",
            "--custom-file-header",
            "# Type-specific union constraints",
            "--formatters",
            "builtin",
        ),
        option="--use-single-dict-for-object-maps",
    ),
    LegacyRow(
        "legacy-validated-pattern-maps",
        (
            "without `--check-pattern-maps-strictly`, the root dict of a map the patternProperties validator checks "
            "has `str` keys and counts no pattern, a declared key whose simple schema a pattern rejects witnesses the "
            "check, and the model of an object without properties joins no other type of its type array"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/additional_pattern_intersections/boolean_integer.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
            "--use-single-dict-for-object-maps",
            "--generate-schema-validators",
        ),
        option="--check-pattern-maps-strictly",
    ),
    LegacyRow(
        "legacy-nullable-as-type-list",
        (
            "OpenAPI `nullable` is read as a type list only after the first `_get_data_type` of a schema (a list item "
            "is nullable under a root model and not elsewhere)"
        ),
        fix=5120,
        witness=(
            "--input",
            "tests/data/legacy_rows/nullable_root.yaml",
            "--input-file-type",
            "openapi",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--strict-nullable",
        ),
    ),
    LegacyRow(
        "legacy-merged-reference-null",
        (
            "a reference whose schema admits null takes the null of the model it names after a merge, so it loses the "
            "None when that model rejects null, unless `--keep-reference-null-on-merged-models` keeps the null of its "
            "schema"
        ),
        fix=4816,
        witness=(
            "--input",
            "tests/data/jsonschema/keep_reference_null_on_merged_models.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--strict-nullable",
            "--reuse-model",
            "--target-python-version",
            "3.10",
            "--formatters",
            "builtin",
        ),
        option="--keep-reference-null-on-merged-models",
    ),
    LegacyRow(
        "legacy-root-model-type-only",
        "a root model ignores `nullable` and reads only its `type`",
        fix=5426,
        witness=(
            "--input",
            "tests/data/legacy_rows/nullable_root.yaml",
            "--input-file-type",
            "openapi",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
    ),
    LegacyRow(
        "legacy-ref-target-null-type-only",
        "a reference reads only a `type` of null (or strict `nullable`) of its target, not a type list",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/legacy_rows/jsonschema_witnesses/null_target.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
    ),
    LegacyRow(
        "legacy-map-copied-nullable",
        "a `nullable` copied into a combined branch leaves its map without null (the owner kept it)",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/nullable_object_maps.yaml",
            "--input-file-type",
            "openapi",
            "--formatters",
            "builtin",
            "--output-model-type",
            "msgspec.Struct",
            "--strict-nullable",
            "--use-single-dict-for-object-maps",
        ),
    ),
    LegacyRow(
        "legacy-item-null-by-form",
        (
            "without `--keep-null-of-nullable-items`, under `--strict-nullable` an array item, a tuple item or a map "
            "value that is a scalar or an object keeps the null its `nullable` declares, and one "
            "that is an array, a map or an `allOf` of one `$ref` rejects null"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/nullable_item_forms.yaml",
            "--input-file-type",
            "openapi",
            "--formatters",
            "builtin",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--strict-nullable",
            "--disable-timestamp",
        ),
        option="--keep-null-of-nullable-items",
    ),
    LegacyRow(
        "legacy-mixed-union-key-root-models",
        (
            "without `--use-root-types-for-mixed-union-map-keys`, a map key that is a union with a member that is "
            "no RootModel (`str`, a `constr` call, a `Literal`, `Any`, an enum) keeps the RootModel class of its "
            "other members, which Pydantic cannot hash, so a key only the RootModel matches raises `TypeError`, and "
            "a key both match is read by the other member (`parser/_passes/modules.py:type_dict_keys_by_root_types`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/property_names_root_model_mixed_union_keys.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--use-root-types-for-mixed-union-map-keys",
    ),
    LegacyRow(
        "legacy-redeclared-hiding-field-keeps-name",
        (
            "without `--rename-redeclared-fields-hiding-types`, a field that a subclass redeclares keeps its name "
            "where the field of its base is renamed because it is named like its type (`Pet_1` with `alias='Pet'`), "
            "so the subclass holds two fields that read the key "
            "(`parser/_passes/modules.py:follow_hiding_field_renames`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/field_named_like_type_redeclared.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--rename-redeclared-fields-hiding-types",
    ),
    LegacyRow(
        "legacy-field-hides-type-from-checkers",
        (
            "without `--rename-fields-hiding-types-from-type-checkers`, a msgspec or dataclass field that the class "
            "body assigns keeps its name and its type's name where they are equal, which a type checker reads as the "
            "field (`parser/_passes/modules.py:renames_hiding_fields`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/field_named_like_type_assigned.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "dataclasses.dataclass",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--rename-fields-hiding-types-from-type-checkers",
    ),
    LegacyRow(
        "legacy-root-type-nullable-ungated",
        "a root field is nullable by `nullable` without `--strict-nullable`",
        fix=5054,
        witness=(
            "--input",
            "tests/data/openapi/redundant_reference_null_inline.yaml",
            "--input-file-type",
            "openapi",
            "--formatters",
            "builtin",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-annotated",
            "--field-constraints",
        ),
        option="--strict-nullable",
    ),
    LegacyRow(
        "legacy-multiple-types-nullable-not-recorded",
        (
            "a type list with an object records only the type list's null; `--read-nullable-beside-type-list` reads "
            "`nullable` too"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/schema_intersection/null_multiple_types_oas30.yaml",
            "--input-file-type",
            "openapi",
            "--disable-timestamp",
            "--formatters",
            "builtin",
            "--use-missing-sentinel",
        ),
        option="--read-nullable-beside-type-list",
    ),
    LegacyRow(
        "legacy-array-nullable-ungated",
        "an array field is nullable by `nullable` without `--strict-nullable`",
        fix=5054,
        witness=(
            "--input",
            "tests/data/openapi/nullable.yaml",
            "--input-file-type",
            "openapi",
            "--formatters",
            "builtin",
        ),
        option="--strict-nullable",
    ),
    LegacyRow(
        "legacy-array-enum-class-only",
        (
            "the enum beside a `type` list holding `array` is written as an Enum class without asking "
            "`should_parse_enum_as_literal` (`JsonSchemaParser._enum_site_form`), so `--enum-field-as-literal all` "
            "leaves it a class"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/string_enum.json",
            "--input-file-type",
            "jsonschema",
            "--no-use-specialized-enum",
        ),
    ),
    LegacyRow(
        "legacy-title-alias-literal-unread-as-const",
        (
            "under `--use-title-as-name` a titled enum is written as a type alias where its `Literal` would be, "
            "without reading it as the const beside it first (`JsonSchemaParser._enum_site_form`), so an untyped "
            "titled enum that `--use-single-value-enum-as-const` reads as its const still gets an alias"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/string_enum.json",
            "--input-file-type",
            "jsonschema",
            "--no-use-specialized-enum",
        ),
    ),
    LegacyRow(
        "legacy-enum-literal-value-only",
        "a `Literal` enum admits null by its null value alone",
        fix=5054,
        witness=(
            "--input",
            "tests/data/jsonschema/nullable_enum_literal_length.json",
            "--input-file-type",
            "jsonschema",
            "--field-constraints",
            "--filter-literal-values",
            "--enum-field-as-literal",
            "all",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--target-python-version",
            "3.10",
        ),
    ),
    LegacyRow(
        "legacy-msgspec-top-level-null-as-written",
        "msgspec reads `is_optional` for the top-level None before render preparation writes rendered nullability",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/boolean_property.json",
            "--input-file-type",
            "jsonschema",
            "--target-python-version",
            "3.10",
            "--output-model-type",
            "msgspec.Struct",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-member-null-from-hint-text",
        "a union takes the None of a member by rendering the member's hint",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-l3-import-nullability",
        "L3 deduplication renders write nullability that overrides and constructor ordering read",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-consumed-at-object-property",
        (
            "a zero-bound class consumes its keyword only at an object property; `--omit-redundant-constraints` omits "
            "it everywhere"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/redundant_constraints.json",
            "--input-file-type",
            "jsonschema",
            "--use-non-positive-negative-number-constrained-types",
            "--use-single-value-enum-as-const",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--target-python-version",
            "3.10",
        ),
        option="--omit-redundant-constraints",
    ),
    LegacyRow(
        "legacy-root-tuple-lengths",
        (
            "under `--field-constraints` a root field keeps the lengths its schema declares for a fixed tuple, and "
            "takes no item counts for it, where an object property and the array builder leave them out; msgspec then "
            "warns that `Meta` takes no length on a tuple"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-property-counts-beside-models",
        (
            "an object property whose type is a model keeps the item counts that `contains` and boolean `items` imply "
            "for the written type to decide (a type alias of an array and a collapsed array take them; a model whose "
            "value takes none, such as a root model, leaves them out with a warning, also in the Pydantic outputs "
            "since #6035); a root field writes none there"
        ),
        fix=6051,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-root-records-by-option",
        (
            "a root field records the constraints it leaves out (and their warning) and those its type decides only "
            "under `--field-constraints`, where an object property records them once it takes any"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-all-of-root-format-kinds",
        (
            "without `--keep-string-keywords-beside-formats`, an allOf root keeps, silently, the keywords of the kinds "
            "its format names, so a date or UUID format drops the string keywords of the root also where the output "
            "writes it as `str`"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/format_string_keywords_all_of.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "msgspec.Struct",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--keep-string-keywords-beside-formats",
    ),
    LegacyRow(
        "legacy-undeclared-keywords-off-literal",
        "the `Literal` of a const or enum takes no keyword of an undeclared kind",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/oneof_const_enum_infer_type.yaml",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-override-keeps-string-keywords",
        "`--type-overrides` keeps the string keywords the replaced type took",
        fix=5987,
        witness=(
            "--input",
            "tests/data/jsonschema/type_overrides_replace_whole_type.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--type-overrides",
            (
                '{"Inventory.counts": "builtins.dict", "Inventory.sized": "builtins.dict", '
                '"Inventory.amounts": "typing.Any", "Inventory.keyed": "collections.abc.Mapping", '
                '"Inventory.labels": "dict[str, str]", "Inventory.tags": "builtins.tuple", '
                '"Inventory.notes": "builtins.str", "Inventory.code": "builtins.bytes"}'
            ),
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--target-python-version",
            "3.10",
        ),
        option="--type-overrides-replace-whole-type",
    ),
    LegacyRow(
        "legacy-class-override-keeps-container",
        (
            "without `--type-overrides-replace-whole-type`, a class path of `--type-overrides` keeps the container of "
            "the field's own type node, so an object map is written as a dict of the class (`dict[str, dict]` for "
            "`builtins.dict`), and every override keeps the item or property counts of the array or map it replaces "
            "whatever its type counts"
        ),
        fix=5987,
        witness=(
            "--input",
            "tests/data/jsonschema/type_overrides_replace_whole_type.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--type-overrides",
            (
                '{"Inventory.counts": "builtins.dict", "Inventory.sized": "builtins.dict", '
                '"Inventory.amounts": "typing.Any", "Inventory.keyed": "collections.abc.Mapping", '
                '"Inventory.labels": "dict[str, str]", "Inventory.tags": "builtins.tuple", '
                '"Inventory.notes": "builtins.str", "Inventory.code": "builtins.bytes"}'
            ),
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--target-python-version",
            "3.10",
        ),
        option="--type-overrides-replace-whole-type",
    ),
    LegacyRow(
        "legacy-content-media-type-string-is-text",
        (
            "without `--use-bytes-for-content-media-type`, a string of an OpenAPI document that has a "
            "`contentMediaType` and no `contentEncoding`, raw binary content the generated clients and servers send as "
            "a file, is typed `str` (`JsonSchemaParser._get_schema_type`), where a `format: binary` string is `bytes`"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/content_media_type_bytes.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--target-python-version",
            "3.10",
        ),
        option="--use-bytes-for-content-media-type",
    ),
    LegacyRow(
        "legacy-float-const-unbounded",
        (
            "without `--use-bounds-for-float-const`, a finite float `const`, which no `Literal` holds, is typed "
            "`float` (`JsonSchemaParser._get_const_data_type`), which admits every other number, with a warning, where "
            "Pydantic and msgspec output could fix it by an equal `minimum` and `maximum`"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/float_const_decimal_multiple_of.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--use-decimal-for-multiple-of",
            "--field-constraints",
        ),
        option="--use-bounds-for-float-const",
    ),
    LegacyRow(
        "legacy-spelled-union-takes-schema-type",
        (
            "without `--use-x-python-type-as-written`, an `x-python-type` union spelled `Optional[...]` or "
            "`Union[...]` counts as compatible with every type the schema declares "
            "(`JsonSchemaParser._is_compatible_python_type`), so the field takes the schema's type, where the same "
            "union spelled `X \\| Y` types it"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/x_python_type_unions.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--target-python-version",
            "3.10",
        ),
        option="--use-x-python-type-as-written",
    ),
    LegacyRow(
        "legacy-x-python-type-container-takes-slot-class",
        (
            "without `--use-x-python-type-as-written`, an `x-python-type` that names a container class compatible with "
            "the schema gives the node only its slot (`JsonSchemaParser._get_python_type_flags`), so the slot's class "
            "is written: `list` for a `tuple`, `Sequence` for a `MutableSequence`, `set` for a `MutableSet`, "
            "`frozenset` for an `AbstractSet` and `Mapping` for a `MutableMapping`, which "
            "`--use-generic-container-types` and `--use-unique-items-as-set` rewrite as they rewrite any other"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/x_python_type_unions.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--target-python-version",
            "3.10",
        ),
        option="--use-x-python-type-as-written",
    ),
    LegacyRow(
        "legacy-anyurl-keeps-string-keywords",
        (
            "without `--keep-string-keywords-beside-formats`, `AnyUrl` takes every string keyword and Pydantic then "
            "drops them all at render"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/legacy_rows/jsonschema_witnesses/anyurl_keywords.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--field-constraints",
        ),
        option="--keep-string-keywords-beside-formats",
    ),
    LegacyRow(
        "legacy-format-types-drop-string-keywords",
        (
            "without `--keep-string-keywords-beside-formats`, a Pydantic string whose format is written as `EmailStr`, "
            "`Base64Str` or `AnyUrl` is written without its `minLength`, `maxLength` and `pattern`, "
            "silently, unless `--field-constraints` writes them"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/format_string_keywords.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--keep-string-keywords-beside-formats",
    ),
    LegacyRow(
        "legacy-length-keywords-across-kinds",
        (
            "without `--read-length-keywords-by-type`, a declared array holds `minLength`/`maxLength` and a declared "
            "string `minItems`/`maxItems`, written as length bounds, also where the field forms write the array branch "
            "of a union with a string beside `maxLength` as an item count"
        ),
        fix=5821,
        witness=(
            "--input",
            "tests/data/jsonschema/length_keywords_by_type.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--target-python-version",
            "3.11",
        ),
        option="--read-length-keywords-by-type",
    ),
    LegacyRow(
        "legacy-lenient-base64-str",
        (
            "without `--use-strict-str-for-base64`, `--strict-types str` leaves a `format: byte` string `Base64Str`, "
            "or the `str` that `EncodedStr` decodes under `--use-encoded-str-for-constrained-base64` "
            "(`_constraint_applicability.strict_encoded_str`), which accept bytes where a strict string refuses them"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/encoded_str_for_constrained_base64.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--strict-types",
            "str",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--target-python-version",
            "3.11",
        ),
        option="--use-strict-str-for-base64",
    ),
    LegacyRow(
        "legacy-reference-takes-every-keyword",
        (
            "a type that names a model takes every keyword where the L1 field assembly and msgspec read it, as the "
            "model's own type decides them, where the decision phase and a Pydantic field when it is written read the "
            "model (a root model, an object model or a plain enum takes none)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/malformed/dangling_local_ref.json",
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-value-base-changing-a-member",
        (
            "the 0.86.0 `IntEnum` or `(T, Enum)` base that changes a member value (`2.5` becomes `2`); "
            "`--keep-enum-member-values` keeps the values"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/enum_value_base_kinds.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
        ),
        option="--keep-enum-member-values",
    ),
    LegacyRow(
        "legacy-msgspec-decodable-value-base",
        "msgspec keeps the 0.86.0 value base so that the class decodes its members",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/enum_value_base_kinds.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "msgspec.Struct",
            "--keep-enum-member-values",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-inherited-first-base-class",
        "an enum merged from allOf takes the class of the first enum",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/single_allof_refs/main.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-inherited-specialized-without-member-check",
        (
            "a merged enum keeps the `IntEnum` or `StrEnum` of the first and, since 0.87.0, leaves out the members it "
            "cannot hold"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/single_allof_refs/main.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-merged-enum-specialized-only",
        (
            "merging the enums that a model inherits reads the value type of the first from its class alone "
            "(`StrEnum`, `IntEnum`), so where the first is a `(T, Enum)` the merged enum keeps the members `T` cannot "
            "hold"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/msgspec_enum_diagnostics/float_integer_alias.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "msgspec.Struct",
            "--target-python-version",
            "3.10",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-const-drops-null",
        "a required or defaulted const drops the null its schema admits",
        fix=4873,
        witness=(
            "--input",
            "tests/data/jsonschema/const.json",
            "--output-model-type",
            "typing.TypedDict",
            "--target-python-version",
            "3.10",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-const-without-literal-not-null",
        "an optional const that no `Literal` holds is not nullable but defaults to None",
        fix=4804,
        witness=(
            "--input",
            "tests/data/jsonschema/float_const_decimal_multiple_of.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--use-decimal-for-multiple-of",
            "--field-constraints",
            "--disable-timestamp",
        ),
    ),
    LegacyRow(
        "legacy-msgspec-const-not-null",
        "a msgspec const is never nullable and takes no default",
        fix=5054,
        witness=(
            "--input",
            "tests/data/jsonschema/const.json",
            "--output-model-type",
            "msgspec.Struct",
            "--target-python-version",
            "3.10",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
    ),
    LegacyRow(
        "legacy-one-literal-drops-type-null",
        "a one-literal default drops the null of a type list that a tag keeps",
        fix=5054,
        witness=(
            "--input",
            "tests/data/jsonschema/generic_base_class_literal_default_order.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "msgspec.Struct",
            "--use-generic-base-class",
            "--allow-population-by-field-name",
            "--use-one-literal-as-default",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
    ),
    LegacyRow(
        "legacy-non-array-first",
        "the branches beside `array` of a type array go before it",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/yaml/root_list.yaml",
            "--input-file-type",
            "yaml",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-replacement-last",
        "the member that collapse replaces goes last in its union",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/schema_intersection/allof_4716.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "msgspec.Struct",
            "--disable-timestamp",
            "--formatters",
            "builtin",
            "--collapse-root-models",
        ),
    ),
    LegacyRow(
        "legacy-graphql-template",
        "the GraphQL union template writes `Union[...]` whatever the union operator",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/graphql/union.graphql",
            "--input-file-type",
            "graphql",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.10",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--use-union-operator",
        ),
    ),
    LegacyRow(
        "legacy-graphql-field",
        (
            "a GraphQL field takes neither `--use-frozen-field` nor `--use-default-factory-for-optional-nested-models` "
            "(an optional nested model defaults to `None`); `--apply-field-options-to-every-field` gives it every "
            "option"
        ),
        fix=5588,
        witness=(
            "--input",
            "tests/data/graphql/field_options_every_field.graphql",
            "--input-file-type",
            "graphql",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.12",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--target-python-version",
            "3.10",
            "--use-default-factory-for-optional-nested-models",
        ),
        option="--apply-field-options-to-every-field",
    ),
    LegacyRow(
        "legacy-boolean-property",
        (
            "a property whose schema is `true` or `false` takes neither `--use-serialize-as-any`, "
            "`--use-default-kwarg` (it writes `Field(None, ...)`), `--use-frozen-field` nor "
            "`--use-default-factory-for-optional-nested-models`; `--apply-field-options-to-every-field` gives it every "
            "option"
        ),
        fix=5588,
        witness=(
            "--input",
            "tests/data/openapi/field_options_every_field.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.12",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--use-default-kwarg",
        ),
        option="--apply-field-options-to-every-field",
    ),
    LegacyRow(
        "legacy-alias-option-only",
        (
            "a required name without a property or an inherited field, and the tag field the discriminator pass "
            "appends, take `--use-serialization-alias` only of the field options (no `Annotated` under "
            "`--use-annotated`); `--apply-field-options-to-every-field` gives them every option"
        ),
        fix=5588,
        witness=(
            "--input",
            "tests/data/openapi/field_options_every_field.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.12",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--use-annotated",
        ),
        option="--apply-field-options-to-every-field",
    ),
    LegacyRow(
        "legacy-dedup-before-strip",
        "rendering compares a member's text before stripping its None",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/official_schema_pass/union.avsc",
            "--input-file-type",
            "avro",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-missing-appended",
        "Pydantic writes `MISSING` after the hint and keeps a nested union",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/missing_sentinel_nullable.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-missing-sentinel",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-any-key",
        "the msgspec layout prediction of L4 matches mapping paths by all four key kinds",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/discriminator_mapping_unlisted_schema/main.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-hash-order",
        "the redeclared tag field is picked in string-hash order when both its names are tag fields",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/discriminator_enum_const_outside_enum.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "msgspec.Struct",
            "--use-enum-values-in-discriminator",
            "--use-subclass-enum",
            "--formatters",
            "builtin",
            "--disable-warnings",
        ),
    ),
    LegacyRow(
        "legacy-literal-only",
        "L6 reads a redeclared tag field only as a one-value `Literal`",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/discriminator_mapping_other_document/a/openapi.yaml",
            "--input-file-type",
            "openapi",
            "--allow-remote-refs",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-nested-root-model-tag",
        (
            "L5 reads the tag field of a variant a nested choice gives a union, and of an inline variant, without the "
            "root model or type alias its type names, so such a union stays plain; reading it there would make "
            "Pydantic reject a payload of a variant whose tag is optional"
        ),
        fix=6044,
        witness=(
            "--input",
            "tests/data/openapi/discriminator_mapping_other_document/a/openapi.yaml",
            "--input-file-type",
            "openapi",
            "--allow-remote-refs",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-literal-property-replaces-declared",
        "the L1 tag property replaces a declared one",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/nullable_object_narrowed_in_oneof.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--output-model-type",
            "msgspec.Struct",
        ),
    ),
    LegacyRow(
        "legacy-python-equality",
        "`1 == True` share a tag at L5 and L6 but not at L1",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/discriminator_mapping_other_document/a/openapi.yaml",
            "--input-file-type",
            "openapi",
            "--allow-remote-refs",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-exact-type-key",
        (
            "an enum member lookup keys a value outside the numbers under its exact type "
            "(`_json_schema.py:json_value_key`, `exact_types`), so a `str` subclass is another value than the equal "
            "`str`: under `--set-default-enum-member`, a schema built in Python whose `enum` lists the members of a "
            "`StrEnum` keeps the default `'red'`, where JSON equality writes the member `E.red`"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/graphql/field-default-enum.graphql",
            "--input-file-type",
            "graphql",
            "--set-default-enum-member",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-python-equality-fallback",
        (
            "an enum member lookup compares the items of an array or object as Python does "
            '(`_json_schema.py:json_value_key`, `python_items`): under `--set-default-enum-member`, `enum: [{"a": '
            '1}, {"a": 2}]` with the default `{"a": true}` writes `E.value_0`, where JSON equality keeps `{\'a\': '
            "True}`, and a YAML `enum: [[.nan], [2]]` with the items default `[[.nan]]` keeps `[[float('nan')]]`, "
            "where JSON equality writes `[E.list__nan_]`"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/graphql/field-default-enum.graphql",
            "--input-file-type",
            "graphql",
            "--set-default-enum-member",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-python-name",
        "msgspec `tag_field` by Python name without `--use-msgspec-tagged-unions` or `--tag-msgspec-by-payload-name`",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/discriminator_msgspec_wire_name.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "msgspec.Struct",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--tag-msgspec-by-payload-name",
    ),
    LegacyRow(
        "legacy-same-name-dedup",
        "same-name deduplication leaves union tags out and renders models under their own class name",
        fix=4816,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-collapse-reuse",
        "collapse-reuse skips root models, renders each round anew and tells bases apart by reference",
        fix=4834,
        witness=(
            "--input",
            "tests/data/openapi/yaml_1_1_numbers/ratio.yaml",
            "--input-file-type",
            "openapi",
            "--preset",
            "practical-py312-20261113",
            "--disable-timestamp",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-tree-scope",
        "tree-scope reuse compares models by its own key",
        fix=4834,
        witness=(
            "--input",
            "tests/data/jsonschema/reuse_scope_tree_no_dup",
            "--input-file-type",
            "jsonschema",
            "--reuse-model",
            "--reuse-scope",
            "tree",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-reuse-model",
        "`--reuse-model` compares models by its own key",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/yaml_1_1_numbers/ratio.yaml",
            "--input-file-type",
            "openapi",
            "--preset",
            "practical-py312-20261113",
            "--disable-timestamp",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-stale-dedup-keys",
        "users compare a dedup key memoized by an earlier pass",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/official_schema_pass/fixed.avsc",
            "--input-file-type",
            "avro",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-graphql-affix-only",
        "GraphQL names get affixes only: no uniqueness, map, strategy or singularization",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/graphql/alias_generator_empty_union.graphql",
            "--input-file-type",
            "graphql",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--alias-generator",
            "to_camel",
            "--graphql-no-typename",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-recursion-follows-path",
        "recursion facts follow paths through `_internal` relocation",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/collapse_root_models_parent_class_keys.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--collapse-root-models",
            "--collapse-root-models-name-strategy",
            "parent",
            "--base-class-map",
            '{"Wrap": "base.Base"}',
            "--aliases",
            '{"Wrap.name": "label"}',
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-keyless-reference-by-path",
        "a reference that no model was built with names the model at its path",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-module-follows-path",
        "module entries follow paths",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/dotted_module_exports_cycle_collision",
            "--input-file-type",
            "jsonschema",
            "--target-python-version",
            "3.10",
            "--treat-dot-as-module",
            "--all-exports-scope",
            "recursive",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-exact-imports",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-keep-order-short-name",
        "a false dependency on a same-named model of another module",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/keep_model_order_same_short_name.yaml",
            "--input-file-type",
            "openapi",
            "--keep-model-order",
            "--disable-future-imports",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-forward-by-name",
        (
            "the quoted forward references of render preparation and the update actions of a module find a model's "
            "position by its short name, so the update actions also count a same-named model of another module"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/malformed/dangling_local_ref.json",
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-replaced-root-shares-path",
        "a collapsed root and its inner model name each other",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/graphql/field_type_collision.graphql",
            "--input-file-type",
            "graphql",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--field-type-collision-strategy",
            "rename-type",
            "--base-class-map",
            '{"Pet_1": "base.Base"}',
            "--extra-template-data",
            '{"Pet_1": {"config": {"frozen": true}}}',
            "--validators",
            '{"Pet_1": {"validators": [{"field": "name", "function": "base.check"}]}}',
            "--aliases",
            '{"Pet_1.name": "label"}',
            "--serialization-aliases",
            '{"Pet_1.name": "wire_name"}',
            "--default-values",
            '{"Pet_1.name": "x"}',
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-nested-factory-at-read",
        "the assignment is read with the nested factories it has then, before L6 records the recursive paths",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/person.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.dataclass",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-nested-factory-order-before-nullability",
        "a Pydantic dataclass under `--strip-default-none` keeps the required-fields answer of the first read",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-warning-reads-parsed-bases",
        (
            "the Pydantic 2.13 signature warning and the keyword-only fix of the msgspec structs that opted out of a "
            "tag read a model's bases as listed after `--type-overrides` replaced some, so a replaced base counts no "
            "fields, where constructor ordering reads the generated model it stood for (`constructor_base_models`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/csv/backend_matrix.csv",
            "--input-file-type",
            "csv",
            "--target-python-version",
            "3.10",
            "--output-model-type",
            "dataclasses.dataclass",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-reference-only",
        (
            "the field renames of `change_field_name` read the references a field's annotation names, not the enum of "
            "member literals, so a discriminator field that the union of a module processed earlier typed "
            "`Literal[Kind.cat]` keeps the name `Kind` of that enum, where the other readers of the names a model "
            "writes (`_written_type_names`) see the enum"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy.L-NF",
        "the import snapshot reads fields as if the nested factory were written",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-stdlib-annotated",
        "a stdlib dataclass `field(...)` under `--use-annotated` counts as unassigned",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/dataclass_inheritance_field_ordering.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "dataclasses.dataclass",
            "--target-python-version",
            "3.10",
            "--use-annotated",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
    ),
    LegacyRow(
        "legacy-collapse-internal-module-import",
        (
            "a model of an `_internal` module that a type inlined into a module names (collapsed root types, dict keys "
            "typed by root types) is named through the `_internal` module, which the module imports "
            "(`field_internal.Pet`), where the module's own types import the model from it"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/graphql/field-default-enum.graphql",
            "--input-file-type",
            "graphql",
            "--output-model-type",
            "typing.TypedDict",
            "--collapse-root-models",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy.alias-order",
        "import names in the order that L5 and L6 repairs asked for them",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/decimal_strict_aliased_import.json",
            "--input-file-type",
            "jsonschema",
            "--use-decimal-for-multiple-of",
            "--use-decimal-in-field-forms",
            "--strict-types",
            "float",
            "--field-constraints",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--target-python-version",
            "3.11",
        ),
    ),
    LegacyRow(
        "legacy.enum-member-import-bindings",
        "an enum's `Field` import is bound but not written",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/string_enum.json",
            "--input-file-type",
            "jsonschema",
            "--no-use-specialized-enum",
        ),
    ),
    LegacyRow(
        "legacy.pdc-annotated-strip",
        (
            "a Pydantic dataclass drops the `Annotated` import whenever it must see the field's metadata, also when "
            "the hint writes `Annotated`"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/boolean_property.json",
            "--input-file-type",
            "jsonschema",
            "--target-python-version",
            "3.10",
            "--output-model-type",
            "pydantic_v2.dataclass",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-run-alias-generator-import",
        (
            "a BaseModel keeps importing the run's `--alias-generator` where it does not render it, so that class "
            "naming is unchanged"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/graphql/alias_generator_empty_union.graphql",
            "--input-file-type",
            "graphql",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--alias-generator",
            "to_camel",
            "--graphql-no-typename",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-reparse-for-final-names",
        (
            "the L1 rows read the name a model is created under; when a yielding name changed and an L1 key names the "
            "created or the final name, the whole input is parsed again with the final names"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-number-before-case",
        (
            "under `--capitalise-enum-members` a member whose capitalized name is taken is numbered without its casing "
            "(`a_1` beside `A`, `FieldNameResolver.get_valid_name`), unless `--capitalise-numbered-enum-members` cases "
            "it too (`A_1`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/capitalise_numbered_enum_members.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--capitalise-enum-members",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--capitalise-numbered-enum-members",
    ),
    LegacyRow(
        "legacy-first-character-of-path-key",
        (
            "a paths key loses its first character when its operation models are named, a leading slash or not (`pets` "
            "names `EtsGetResponse`, `OpenAPIParser._process_path_items`), unless "
            "`--keep-first-character-of-path-keys` strips only a leading slash, as for webhook names"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/paths_key_without_leading_slash.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--openapi-scopes",
            "schemas",
            "paths",
            "parameters",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--keep-first-character-of-path-keys",
    ),
    LegacyRow(
        "legacy-content-parameter-copy",
        (
            "a parameter whose `content` media schema is a Reference Object reads a copy of the schema it names: an "
            "object gets a model of its own (`Page1Request` beside `PageRequest` under "
            "`--read-only-write-only-model-type request-response`), a scalar its inline type with the default and "
            "description of the target (`OpenAPIParser._content_parameter_schema`), unless "
            "`--keep-content-parameter-refs` keeps the `$ref`, as a parameter's own schema does"
        ),
        fix=6322,
        witness=(
            "--input",
            "tests/data/openapi/content_parameter_refs.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--openapi-scopes",
            "schemas",
            "paths",
            "parameters",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--keep-content-parameter-refs",
    ),
    LegacyRow(
        "legacy-converter-placeholder-title",
        (
            "an AsyncAPI payload in Avro or XML Schema that names no type of its own takes the converter's title "
            "`Model` (`convert_avro_schema_data`, `_convert_xml_schema_data`), which its field writes and "
            "`--use-title-as-name` names its class by, unless `--name-converted-payloads-by-message` drops it, so the "
            "payload is named after its message as a JSON Schema payload is"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/asyncapi/converted_payload_names.yaml",
            "--input-file-type",
            "asyncapi",
            "--target-python-version",
            "3.10",
            "--formatters",
            "builtin",
        ),
        option="--name-converted-payloads-by-message",
    ),
    LegacyRow(
        "legacy-string-occurs-tests",
        (
            "the XSD converter tells whether a particle may occur, is optional or repeats by comparing its `maxOccurs` "
            'and `minOccurs` with the strings "0" and "1" (`parser/xmlschema.py:_occurs`), while the item counts '
            'read the integers (`_safe_int`), so an element with `minOccurs="00"` is required and gets no `minItems`'
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/xmlschema/field_name_collisions/global_elements.xsd",
            "--input-file-type",
            "xmlschema",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-one-inline-trait-headers",
        (
            "an AsyncAPI message's headers are its own, else those of the one trait that is no reference and defines "
            "them, else those of a referenced trait, which keep their component model; a second inline trait with "
            "headers is an error (`AsyncAPIParser._iter_trait_schemas`), unless `--merge-message-trait-headers` merges "
            "the headers of every trait in order and then the message's own, as JSON Merge Patches (`_merge_patch`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/asyncapi_invalid/multiple-trait-headers.yaml",
            "--input-file-type",
            "asyncapi",
            "--target-python-version",
            "3.10",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
        ),
        option="--merge-message-trait-headers",
    ),
    LegacyRow(
        "legacy-own-headers-win-v2",
        (
            "under `--merge-message-trait-headers` the headers of an AsyncAPI 2 message's own win over those of its "
            "traits, as under AsyncAPI 3 (`AsyncAPIParser._message_headers` passes `own_wins=True` to "
            "`_document_reader.py:trait_layers`), where AsyncAPI 2 merges each trait into the message by JSON Merge "
            "Patch and the connection reader lets the trait win (`_asyncapi_connections.py:_layers`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/asyncapi_invalid/multiple-trait-headers.yaml",
            "--input-file-type",
            "asyncapi",
            "--target-python-version",
            "3.10",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--merge-message-trait-headers",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-whole-name-singular",
        (
            "the item model of an array is named by singularizing its whole name as one word (`actions_caches` names "
            "`ActionsCach`, `ModelResolver._singular_name`), unless `--singularize-last-word-of-item-names` "
            "singularizes only its last word (`ActionsCache`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/singularize_last_word_of_item_names.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--singularize-last-word-of-item-names",
    ),
    LegacyRow(
        "legacy-module-suffix-model",
        (
            "the `module` scheme of L3 `replace_duplicate_name_in_module` gives a taken name the suffix `Model` "
            "without reading the `--duplicate-name-suffix` map, unless "
            "`--use-duplicate-name-suffix-for-every-collision` reads it (row `map-or-model`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/duplicate_name_suffix_every_collision.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--duplicate-name-suffix",
            '{"model": "Schema", "enum": "Kind"}',
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--use-duplicate-name-suffix-for-every-collision",
    ),
    LegacyRow(
        "legacy-variant-takes-parent-name",
        (
            "a branch of a union that no tag, title or `--infer-union-variant-names` names takes the union's name "
            "(`composed_name`, role `variant-parent`), so its model is numbered after the union's, or prefixed with it "
            "under `--naming-strategy full-path` (`StreamEventStreamEvent`)"
        ),
        fix=4826,
        witness=(
            "--input",
            "tests/data/avro/official_schema_pass/union.avsc",
            "--input-file-type",
            "avro",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-first-come",
        (
            "the first model to ask the L1 resolver for a name takes it and a later one is numbered, in the order L1 "
            "reaches them, whatever each is (a definition, an inline model, a variant), so a definition can be "
            "numbered after an inline model of its name (`ModelResolver.add`)"
        ),
        fix=4825,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-external-only-primary-first",
        (
            "under `--naming-strategy primary-first` only a reference of another file gives its name to a primary "
            "definition; an inline model of the same file that took the name first keeps it, and the definition is "
            "numbered (`Package1`, `ModelResolver._rename_external_ref_with_same_name`)"
        ),
        fix=4825,
        witness=(
            "--input",
            "tests/data/openapi/typed_dict_extra_items_final_name/renamed.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "typing.TypedDict",
            "--formatters",
            "builtin",
            "--naming-strategy",
            "primary-first",
        ),
    ),
    LegacyRow(
        "legacy-ref-drops-duplicate-name",
        (
            "a name `add_ref` generates keeps no duplicate name, so under `--naming-strategy primary-first` a "
            "reference of another file that is numbered when a `$ref` names it (`other.Pet1`, where a definition of "
            "the main file already holds `Pet`) is not given its name back where its module leaves it free, while one "
            "that took the name first and that the definition renames later is (`other.Pet`), so the name follows the "
            "order of the main file (`ModelResolver._add_ref_at`)"
        ),
        fix=6855,
        witness=(
            "--input",
            "tests/data/malformed/dangling_local_ref.json",
            "--input-file-type",
            "jsonschema",
        ),
    ),
    LegacyRow(
        "legacy-closest-registered-parent",
        (
            "the parent whose name `--naming-strategy full-path` and `parent-prefixed` put before a model's is the "
            "closest path a reference holds when the model is named, not the model it is built for, so a definition "
            "that no `$ref` named first takes the root model's name (`ModelCustomer`, "
            "`ModelResolver._find_parent_reference`)"
        ),
        fix=4855,
        witness=(
            "--input",
            "tests/data/jsonschema/dynamic_ref_generic_specialization/probe.schema.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-title-as-name",
            "--strict-types",
            "str",
            "bytes",
            "int",
            "float",
            "bool",
            "--naming-strategy",
            "full-path",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-fixed-definition-patterns",
        (
            "the resolver tells a primary definition by the text of its path, a direct child of `#/definitions/`, "
            "`#/components/schemas/` or `#/$defs/` (`ModelResolver._is_primary_definition`), not by the definition "
            "containers the parser reads (`JsonSchemaParser._is_named_schema_definition_path`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-allof-members-under-holder",
        (
            "the union members of an allOf beside a `oneOf` or `anyOf` are named after the allOf's holder and numbered "
            "after it (`Event1`, `Event2`), not after their branches (`JsonSchemaParser._parse_all_of_union_model`)"
        ),
        fix=4847,
        witness=(
            "--input",
            "tests/data/jsonschema/allof_union_through_ref_files/loop.json",
            "--input-file-type",
            "jsonschema",
            "--distribute-allof-over-unions",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
    ),
    LegacyRow(
        "legacy-module-restore",
        (
            "once L3 has made the classes of a module unique, a model the resolver numbered takes back the name it "
            "asked for where the module leaves it free, the first such model first "
            "(`replace_duplicate_name_in_module`, `NamingPlan.base_of`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-both-prefixes",
        (
            "under `--parent-scoped-naming` with `--naming-strategy full-path` an inline model takes the parent's name "
            "twice, the `full-path` prefix before the `parent-prefixed` one (`ModelResolver._parent_prefixed`); the "
            "names of generated API code take both too (`ModelResolver._naming_rows`)"
        ),
        fix=5666,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-predicted-rename",
        (
            "the later holders of a root model that the `parent` strategy collapsed keep naming it where the run "
            "renames colliding types and both models are written in one module, until `settle_kept_parent_roots` reads "
            "the final class names"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/circular_ref_ref_with_schema_keywords.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--collapse-root-models",
            "--collapse-root-models-name-strategy",
            "parent",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-decide-in-union-module",
        "a discriminated union is tagged when its module is processed, not after the module loop",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/discriminator_msgspec_tagged_unions_modules_variants_first.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "msgspec.Struct",
            "--use-msgspec-tagged-unions",
            "--target-python-version",
            "3.10",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-tag-from-earlier-union",
        (
            "a variant keeps the tags of the first union that tags it, unless `--merge-shared-variant-tags` gives it "
            "every tag the unions give it"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/discriminator_merged_collapsed_variant_tags.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--reuse-model",
            "--collapse-reuse-models",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--merge-shared-variant-tags",
    ),
    LegacyRow(
        "legacy-inline-variants-at-first-ask",
        "the inline variants that take the tag they inherit are decided at the first collapse or apply that asks",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/discriminator_mapping_other_document/a/openapi.yaml",
            "--input-file-type",
            "openapi",
            "--allow-remote-refs",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-predicted-tag-names",
        "L4 layout flattening reads a superset of the msgspec tag fields (`possible_tag_names`)",
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/msgspec_inheritance/graphql_independent.graphql",
            "--input-file-type",
            "graphql",
            "--output-model-type",
            "msgspec.Struct",
            "--target-python-version",
            "3.10",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-collapse-python-name-mode",
        (
            "the union that `--collapse-root-models` inlines as list items is checked by the Python names of its "
            "variants' tag fields, where L5 checks one that writes `discriminator=` by the schema's names; it writes "
            "`discriminator=` by the Python name the property takes in any class, which a union whose choices do not "
            "all carry that tag replaces, once every module wrote its tags, by the decision of its site "
            "(`parser/_tag_plan.py:iter_unbuildable_inlined_unions`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/schema_intersection/allof_4716.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
            "--collapse-root-models",
        ),
    ),
    LegacyRow(
        "legacy-collapse-any-discriminator",
        (
            "the union that `--collapse-root-models` inlines as list items keeps `discriminator=` where its "
            "discriminator is written as a string or names an empty property "
            "(`parser/_tag_plan.py:collapsed_item_site`), which L5 reads as no union site (`union_site`) and never tags"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/collapse_root_models_lookaround_root.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--collapse-root-models",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-inherited-enum-by-default-name",
        (
            "the check of a discriminated union finds the Enum typing the tag field a variant inherits by the Python "
            "name the property takes in any class (`parser/_tag_plan.py:_find_tag_enum`), where L5 writes the tag by "
            "the name the variants share (`shared_site_names`): a base whose field is named otherwise, as a property "
            "named like its Enum is, types no tag in the check; the union such a base gives variants with one tag is "
            "now written without `discriminator=` (`settle_site`, `dropped-shared-tag`), so its module imports, and "
            "its variants keep the tag field with every member"
        ),
        fix=6865,
        witness=(
            "--input",
            "tests/data/openapi/discriminator_enum_single_value_anyof.yaml",
            "--input-file-type",
            "openapi",
            "--target-python-version",
            "3.10",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-enum-values-in-discriminator",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-root-most-first",
        (
            "the tag fields a variant inherits are read from the root-most ancestor down "
            "(`parser/_tag_plan.py:_inherited_models`, the order of `DataModel.iter_all_fields`) for the Python name "
            "the variants of a union share, the tag and tag field of a variant a nested choice gives a union, the tag "
            "field of the one variant a union names and the tags an inline variant inherits (read before its own), "
            "where the generated class has the field of the nearest ancestor and the other readers take that one "
            "(`nearest-first`); a union whose nested variant thus keeps an optional tag in the field its class has is "
            "now written without `discriminator=` (`settle_site`, `dropped-nested-untagged`), so its module imports, "
            "and the variant keeps its tag fields (#6875)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/discriminator_mapping_other_document/a/openapi.yaml",
            "--input-file-type",
            "openapi",
            "--allow-remote-refs",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-direct-bases-first",
        (
            "the Enum typing the tag field a variant inherits is read from its direct bases in their order, then from "
            "the other ancestors nearest first (`parser/_tag_plan.py:_inherited_tag_enum`), so a later direct base "
            "comes before the ancestors of an earlier one, which the generated class resolves first"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/discriminator_mapping_other_document/a/openapi.yaml",
            "--input-file-type",
            "openapi",
            "--allow-remote-refs",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-struct-hash-last-base-first",
        (
            "under `--use-unique-items-as-set`, whether a msgspec struct hashes "
            "(`model/msgspec.py:Struct._class_hash`) reads `frozen` and `eq` from the chain that takes the last base "
            'first (`_struct_chain(order="graph")`), where msgspec takes each from the first base first (the '
            "`listed` chain, as `array_like` does): where two bases set `frozen` differently, a set may be written of "
            "structs msgspec cannot hash. This is today's order, kept: the two orders differ only in a configuration "
            "nobody writes (opposite `frozen` on two parents of one struct)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/msgspec_number_limits/union_integer_bounds.json",
            "--input-file-type",
            "jsonschema",
            "--preset",
            "practical-py312-20261104",
            "--disable-timestamp",
            "--output-model-type",
            "msgspec.Struct",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-unique-sets-level-order",
        (
            "under `--use-unique-items-as-set-by-value`, the fields a set item model has with its bases are read "
            "nearest first, each name from the first model that declares it "
            "(`parser/_unique_sets.py:_ByValue.fields`, `ReferenceGraph.level_order`), also beside the fields of its "
            "C3 order, which the generated class takes and `--read-set-item-fields-in-class-order` reads too: a name "
            "a base two levels up and a later direct base both declare is read from the later base as well, so where "
            "only that field does not hash the items stay a list (the class's field hashes)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/yaml_1_1_numbers/ratio.yaml",
            "--input-file-type",
            "openapi",
            "--preset",
            "practical-py312-20261113",
            "--disable-timestamp",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-unique-sets-class-fields-unread",
        (
            "without `--read-set-item-fields-in-class-order`, whether a set item model hashes by value under "
            "`--use-unique-items-as-set-by-value` reads only the fields its bases declare nearest first "
            "(`parser/_unique_sets.py:_ByValue.fields`), not those of its C3 order, which the generated class takes: "
            "where a base two levels up and a later direct base both declare a field and the class takes the one that "
            "does not hash, the uniqueItems array is a set its items cannot be added to (#6997)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/unique_items_by_value_inherited_fields.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "dataclasses.dataclass",
            "--frozen-dataclasses",
            "--use-unique-items-as-set",
            "--use-unique-items-as-set-by-value",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--read-set-item-fields-in-class-order",
    ),
    LegacyRow(
        "legacy-cycle-through-scalar",
        (
            "the check for a `$ref` cycle (`parser/_schema_refs.py:is_ref_circular`) counts a pointer that walks "
            "through a scalar as a target that cannot be loaded, so a `$ref` to it keeps its siblings unmerged, where "
            "the other readers of a pointer find nothing there"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/schema_intersection/allof_4716.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-cycle-walk-reads-values-whole",
        (
            "the check for a `$ref` cycle (`parser/_schema_refs.py:walk_ref_cycle`, "
            "`_json_schema.py:subschema_entries` with `whole`) reads the value of a subschema keyword other than a map "
            "whole when it is an object or an array, whatever shape the keyword gives it, and the arrays in an array: "
            'under `not: [{"$ref": "#/definitions/A"}]` in `A`, a `$ref` to `A` with siblings keeps them unmerged, '
            "where the other walkers read no subschema there"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-read-document-every-key",
        (
            "the read of a whole document (`parser/_schema_reading.py:_document_child_entries`, under "
            "`--use-single-value-enum-as-const`, `--read-format-as-annotation` or `--read-kubernetes-extensions`) "
            "reads every key of a schema object but the data keywords and extensions, also one `_DOCUMENT_KEYWORDS` "
            "does not list: a schema under an unknown keyword that a `$ref` names (`#/definitions/Settings/meta/mode`) "
            "is read, where `$ref` edges read the listed keywords only"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/json/simple.json",
            "--input-file-type",
            "json",
            "--snake-case-field",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-split-pointer-tokens",
        (
            "the OpenAPI object a reference names (`OpenAPIParser.get_ref_model`: a path item, parameter, request body "
            "or response) is read by a pointer split at every `/`, so a name that a pointer holding `~1` or `://` "
            "writes with an unescaped `/` names nothing and a fragment that no `/` opens names the whole document, "
            "where the other readers of a pointer join such a name into the key the object holds "
            "(`parser/_schema_reading.py:ref_fragment_tokens`); the lone `$ref` of an allOf is split the same way (row "
            "`legacy-all-of-ref-in-parsed-document`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/json_pointer.yaml",
            "--input-file-type",
            "openapi",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
    ),
    LegacyRow(
        "legacy-all-of-ref-in-parsed-document",
        (
            "without `--read-single-allof-refs-in-their-documents`, the target of the lone `$ref` of an allOf "
            "(`JsonSchemaParser._parse_all_of_single_ref`: where it holds an enum, the schema's type is the referenced "
            "model; where it is `true`, a root model; where it is `false`, the schema is refused) is read in the "
            "document being parsed (`raw_obj`), from the reference as it is spelled after its first two characters, "
            "split at every `/` (row `legacy-split-pointer-tokens`), so the `$ref` of a schema of another document "
            "reads the input document at that pointer (where that holds no enum, the referenced enum is written as a "
            "copy, where one file gives a root model over it) and a plain name (`#code`, an `$anchor`) reads the "
            "root's key `ode`, where the other readers read the document and the pointer of the reference's key; with "
            "the option it is read at that key (`read_ref_value`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/single_allof_refs/main.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
        ),
        option="--read-single-allof-refs-in-their-documents",
    ),
    LegacyRow(
        "legacy-drop-empty-tokens",
        (
            "an AsyncAPI reference (`AsyncAPIParser._resolve_asyncapi_ref`) and the check for a `$ref` cycle (row "
            "`legacy-cycle-through-scalar`) are read by a pointer split at every `/` without its empty tokens, so "
            "`#/a//b` names `#/a/b` and a name that a pointer holding `~1` or `://` writes with an unescaped `/` names "
            "nothing; the target of a reference into another document (`JsonSchemaParser._parse_file`, split by the "
            "parser's reading in `resolve_ref`) also drops its empty tokens, so `other.json#/definitions//Item` parses "
            "`#/definitions/Item` under a key no reference holds; the other readers of a pointer keep the empty tokens "
            "and join such a name into the key the object holds (`parser/_schema_reading.py:ref_fragment_tokens`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/asyncapi/external-message-ref.yaml",
            "--input-file-type",
            "asyncapi",
            "--target-python-version",
            "3.10",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
    ),
    LegacyRow(
        "legacy-compare-before-float",
        (
            "the duration limits of the client options (`_runtime/client/timing.py:checked_seconds`) and an OAuth "
            "token response's `expires_in` (`_runtime/client/oauth.py:_expiry`) compare the value with the largest "
            "float before reading it, so an integer just past it, which rounds to it, is refused where the other "
            "durations read it as the largest float"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/responses_extensions.yaml",
            "--input-file-type",
            "openapi",
            "--openapi-scopes",
            "schemas",
            "api",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--generate-client",
            "httpx2",
            "--client-output",
            "client",
            "--client-package",
            "client",
            "--client-model-package",
            "models",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-dataclass-overwrites-engine",
        (
            "a `pydantic_v2.dataclass` that needs Python's `re` writes `python-re` over a `regex_engine` its config "
            "names (`--extra-template-data`), which a `pydantic_v2.BaseModel` keeps"
        ),
        fix=5637,
        witness=(
            "--input",
            "tests/data/jsonschema/root_alias_constraints/lookaround.json",
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-dataclass-follows-every-reference",
        (
            "a `pydantic_v2.dataclass` takes Python's `re` for a pattern of any model its fields reach through "
            "references (`_own_patterns_select_python`, `_FOLLOWS_PATTERN_REFERENCES`): a model or dataclass with a "
            "config of its own and a type alias whose patterns the target compiles count too, where the models that "
            "use a type alias read only the string patterns of the aliases they reach "
            "(`models_reaching_string_pattern_aliases`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-flat-bare-loads",
        (
            "the check of module imports named like a builtin counts every name node of a rendered model as a bare "
            "load of it: stores, and names that a function, lambda or comprehension of a custom template binds itself"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/module_import_named_like_builtin_type_alias.json",
            "--input-file-type",
            "jsonschema",
            "--use-type-alias",
            "--target-python-version",
            "3.12",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
    ),
    LegacyRow(
        "legacy-abstract-containers-as-unions",
        (
            "the keywords a field's type consumes or enforces itself read a `Sequence` or `Mapping` through to its "
            "members, like a union, where every other container is its own leaf"
        ),
        fix=5821,
        witness=(
            "--input",
            "tests/data/yaml/root_list.yaml",
            "--input-file-type",
            "yaml",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-object-extension-paths",
        (
            "the paths scope's walk (`OpenAPIParser._process_path_items`) reads a `parameters` or `x-` key of `paths` "
            "that holds an object as a path, and so does the recommender (`_recommend/_signals.py`: `_path_operations` "
            "for the paths and webhooks scopes, `_count_operations` for the size note of the other scopes), while the "
            "api scope's walk and the recommender's reading of it skip it; under `--skip-path-extension-keys` both "
            "read row `legacy-object-parameters-path` instead, which skips every `x-` key as the api scope does and "
            "reads only a `parameters` key that holds an object as a path"
        ),
        fix=5746,
        witness=(
            "--input",
            "tests/data/malformed/invalid_operation.yaml",
            "--input-file-type",
            "openapi",
            "--openapi-scopes",
            "paths",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-object-parameters-path",
        (
            "under `--skip-path-extension-keys`, the paths scope's walk (`OpenAPIParser._process_path_items`) and the "
            "recommender's reading of it skip every `x-` key of `paths`, as the api scope does, but still read a "
            "`parameters` key of `paths` that holds an object as a path, as 0.89.0 did, where the api scope skips it"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/malformed/invalid_operation.yaml",
            "--input-file-type",
            "openapi",
            "--openapi-scopes",
            "paths",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-object-extension-responses",
        (
            "a `x-` key of an operation's Responses Object that holds an object is read as a response by the paths "
            "scope's walk (the `Operation` model) and the API walk (`_walk_api_operation`), as they always have, so "
            "the models of its content are generated, while the key of any other value is skipped, which no response "
            "can be"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/anchor_object_documents/held_by_cyclic_path_item/api.yaml",
            "--input-file-type",
            "openapi",
            "--openapi-scopes",
            "schemas",
            "paths",
            "parameters",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
    ),
    LegacyRow(
        "legacy-ascii-table-keys",
        (
            "the notice of the settings a target implied quotes a table key that is no bare key (a job or profile name "
            "such as `ジョブ`) with `\\u` escapes, where `--generate-pyproject-config` and the recommender write its "
            "characters as they are"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/person.json",
            "--input-file-type",
            "jsonschema",
            "--recommend-options",
            "quick",
        ),
    ),
    LegacyRow(
        "legacy-keyword-directory-kept",
        (
            "a directory of the input named like a keyword (`class`) keeps its name as one named by an identifier "
            "does, so a module that imports it is refused, where a directory that is no identifier is written as a "
            "file's name is"
        ),
        fix=6054,
        witness=(
            "--input",
            "tests/data/jsonschema/collapsed_unimportable_directory_reference",
            "--input-file-type",
            "jsonschema",
            "--treat-dot-as-module",
            "--collapse-root-models",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-class-body-without-alias-values",
        (
            "the order of the models whose class body evaluates others (`_sort_models_by_class_body_references`, the "
            "unresolved models of `sort_data_models`) reads the fields of a root model only, not the value of a type "
            "alias, the fields of the functional TypedDict or annotations the module evaluates"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-keep-order-type-statement",
        (
            "`--keep-model-order` reads the value of a `type` statement, which Python defers, and the fields of the "
            "functional TypedDict only where it reads the annotations of a class, which are deferred only by `from "
            "__future__ import annotations`, not by the target"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-evaluated-without-root-model",
        (
            "the check of the root models that name themselves reads a Pydantic root model as a class, the own fields "
            "of the functional TypedDict where it reads the annotations of a class, and the bases of a class only "
            "then; like `--keep-model-order`, it reads them unless `from __future__ import annotations` defers them, "
            "whatever the target defers"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/avro/root_string.avsc",
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-ref-spelling-keys",
        (
            "without `--read-escaped-ref-pointers`, a reference is keyed by its spelling and a model by its parse path "
            "joined as written, so a pointer that escapes a character names a copy of the declaration it reads, named "
            "after the escaped text, and a pointer that writes `/` unescaped inside a name names the declaration whose "
            "path joins to the same text"
        ),
        fix=4952,
        witness=(
            "--input",
            "tests/data/jsonschema/escaped_ref_pointer_split_name.json",
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--read-escaped-ref-pointers",
    ),
    LegacyRow(
        "legacy-merged-site-per-path",
        (
            "without `--use-one-model-per-ref-site`, an object `$ref` with sibling keywords is parsed as its merged "
            "target at each path that reaches it, so the copies of such a `$ref` that a merged target holds are parsed "
            "again below it, one nested parse per hop"
        ),
        fix=5079,
        witness=(
            "--input",
            "tests/data/jsonschema/ref_site_changed_copy.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--use-one-model-per-ref-site",
    ),
    LegacyRow(
        "legacy-dynamic-anchor-parse-index",
        (
            "without `--read-dynamic-anchors-in-document-order`, a plain-name `$dynamicRef` that its dynamic scope "
            "does not bind names the first `$dynamicAnchor` of its name that the parse has registered so far, else "
            "resolves through its edge, so the answer depends on the order the parse meets the schemas in"
        ),
        fix=5131,
        witness=(
            "--input",
            "tests/data/openapi/dynamic_anchor_held_document/api.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--openapi-scopes",
            "schemas",
            "paths",
            "parameters",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--read-dynamic-anchors-in-document-order",
    ),
    LegacyRow(
        "legacy-anchor-parse-index",
        (
            "a plain name the walk of the definitions registered with the model resolver (`parse_id`: an `$id` or "
            "`$anchor` at the positions of the reference pre-pass) names the schema registered last there, also where "
            "the registry names another one, such as the last declaration in document order of a name several schemas "
            "of a document declare"
        ),
        fix=5131,
        witness=(
            "--input",
            "tests/data/jsonschema/missing_anchor.json",
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-external-anchor-search",
        (
            "a plain name in a reference to another document (`parser/_schema_documents.py:normalize_external_ref`) "
            "that no duplicate-name reading answers names the `$anchor` of that name, as written, that the released "
            "search of the document reached (`legacy_external_anchor_search`, over the declarations and the "
            "passed-over `$anchor`s of `DocumentRecord.resources` in the document's prepared tree): through the "
            "subschemas each keyword holds from the root, a namespace of definitions and what it holds outside its "
            "definitions included, not into the members of a version namespace of definitions or an implicit resource "
            "and not past a schema below the root with a `$id` member, the first on its stack (`stack_position`: an "
            "ancestor before what it holds, else the member written later); a `$dynamicAnchor` or an identifier that "
            "is a fragment names nothing, where the registry names them"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/external_anchor_declarations/root.json",
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--read-refs-in-their-resources",
    ),
    LegacyRow(
        "legacy-namespace-declarations-passed",
        (
            "without `--read-refs-in-their-resources`, the walk of a document's schema resources "
            "(`iter_schema_resource_children`, L0) reads a member of a definitions container that has no schema "
            "keywords and holds definitions as a namespace, for its entries alone: its `$id`, `$anchor` and "
            "`$dynamicAnchor` and those of its other subschemas declare nothing (only `passed_anchors` lists its "
            "`$anchor`s for row `legacy-external-anchor-search`), so a pointer inside a bundled `$defs` member with "
            "its own `$id` is read against the enclosing resource (#6946); with the option every member is a schema "
            "the walk visits"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/embedded_resource_definitions.json",
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--read-refs-in-their-resources",
    ),
    LegacyRow(
        "legacy-component-schema-resources",
        (
            "without `--read-refs-in-their-resources`, the implicit schema resources of an OpenAPI document "
            "(`implicit_schema_resources`, L0) are the schemas under `components/schemas` and those of a document a "
            "reference names as an object, so a schema an operation, a webhook or another component kind holds inline "
            "is no resource: its `$id` and anchors declare nothing and a pointer below its `$id` is read against the "
            "document (#7179); with the option every Schema Object the document holds (`held_schemas` of its root)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/operation_schema_resources.yaml",
            "--input-file-type",
            "openapi",
            "--openapi-scopes",
            "schemas",
            "paths",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--read-refs-in-their-resources",
    ),
    LegacyRow(
        "legacy-held-resource-fetched",
        (
            "without `--read-refs-in-their-resources`, a reference whose key is a URL that another document of the run "
            "declares (`held_resource_location`) loads the document at that URL wherever that load finds one "
            "(`loads_document`), and a lenient (OpenAPI) document keeps such a reference for the load with a warning "
            "(`resolve_lenient_resource_ref`), as in 0.100.0; where the load finds nothing the held schema is read "
            "(#7159); with the option the held schema is read without a request"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/component_embedded_ids/api.yaml",
            "--input-file-type",
            "openapi",
            "--openapi-scopes",
            "schemas",
            "paths",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--read-refs-in-their-resources",
    ),
    LegacyRow(
        "legacy-inputs-prepared-as-parsed",
        (
            "without `--read-refs-in-their-resources`, each document of a directory or list input is prepared (L0) "
            "when it is parsed (`JsonSchemaParser._prepare_input_documents`), so a reference of one input to a URL "
            "another input declares finds it only where that input was parsed before; with the option every input is "
            "prepared before the first is parsed"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/held_root_ids/after",
            "--input-file-type",
            "jsonschema",
            "--no-allow-remote-refs",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--read-refs-in-their-resources",
    ),
    LegacyRow(
        "legacy-first-read-dialect",
        (
            "without `--read-schema-version-per-document`, the readings of a schema version are the run's: the "
            "`$ref`-sibling gate of the allOf, literal-narrowing and validator readers and the schema features "
            "(`schema_features`) are those of the document parsed at each one's first read, the gate detected as JSON "
            "Schema for every parser class (an OpenAPI or AsyncAPI document reads as draft 7 unless it holds `$defs`); "
            "whether siblings merge as parts is that of the input document being parsed, also for the schemas of a "
            "document a reference loads"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/recursive_anchor_external_defs/root.json",
            "--input-file-type",
            "jsonschema",
            "--schema-version-mode",
            "strict",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--read-schema-version-per-document",
    ),
    LegacyRow(
        "legacy-count-ref-member-as-written",
        (
            "without `--read-schema-version-per-document`, the property-count validator reads "
            "`minProperties`/`maxProperties` beside an allOf `$ref` member in every schema version, where the other "
            "validator walkers read them only in a version that applies the keywords beside a `$ref`"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/recursive_anchor_external_defs/root.json",
            "--input-file-type",
            "jsonschema",
            "--schema-version-mode",
            "strict",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        option="--read-schema-version-per-document",
    ),
    LegacyRow(
        "legacy-closing-ref-siblings-with-applicators",
        (
            "without `--read-ref-siblings-in-closed-allof-members`, the check whether an `unevaluatedProperties: "
            "false` allOf member closes the object (`parser/_all_of_values.py:_closes_unevaluated_properties`) reads "
            "the keywords beside an allOf `$ref` of the schemas the member evaluates only where that `$ref` member "
            "holds an applicator (`allOf`, `anyOf`, `oneOf`, `if`), `patternProperties`, `additionalProperties` or "
            "`unevaluatedProperties`, so a `dependentSchemas` or `$dynamicRef` alone beside it does not keep the "
            "member from closing the object; the validator walkers, and the check with the option, read every such "
            "member that has a keyword beside its `$ref`"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/allof_unevaluated_closed_member_ref_siblings.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--target-python-version",
            "3.10",
        ),
        option="--read-ref-siblings-in-closed-allof-members",
    ),
    LegacyRow(
        "legacy-dedup-keys-without-unique-items",
        (
            "without `--use-unique-items-as-set-by-value`, the dedup and reuse keys leave out which arrays declare "
            "`uniqueItems`, which they compare as the lists they still are"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/collapse_constrained_roots_in_containers.json",
            "--input-file-type",
            "jsonschema",
            "--preset",
            "practical-py312-20261027",
            "--disable-timestamp",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--collapse-root-models",
            "--field-constraints",
            "--merge-shared-variant-tags",
            "--use-encoded-str-for-constrained-base64",
            "--read-length-keywords-by-type",
            "--read-msgspec-union-bounds-exactly",
            "--naming-strategy",
            "definitions-first",
            "--name-allof-union-members-after-branches",
            "--use-duplicate-name-suffix-for-every-collision",
            "--capitalise-numbered-enum-members",
            "--keep-first-character-of-path-keys",
            "--name-converted-payloads-by-message",
            "--singularize-last-word-of-item-names",
            "--read-schema-version-per-document",
            "--ignore-ref-siblings-by-schema-version",
            "--intersect-combined-schema-keywords",
            "--distribute-allof-over-unions",
            "--keep-reference-null-on-merged-models",
            "--use-msgspec-meta-for-map-keys",
            "--rename-submodules-hidden-by-classes",
            "--use-serialize-as-any-for-every-subclass",
            "--read-escaped-ref-pointers",
            "--read-dynamic-anchors-in-document-order",
            "--use-one-model-per-ref-site",
            "--keep-constrained-root-models-in-every-container",
            "--keep-enum-literal-aliases",
            "--read-input-model-dataclass-fields-by-alias",
            "--use-plain-alias-for-recursive-models",
            "--type-overrides-replace-whole-type",
            "--formatters",
            "builtin",
        ),
        option="--use-unique-items-as-set-by-value",
    ),
    LegacyRow(
        "legacy-unique-items-decided-before-collapse",
        (
            "without `--use-unique-items-as-set-by-value`, `--use-unique-items-as-set` writes its sets in L5, before "
            "root models collapse, type overrides apply and the other modules are processed, and a set item model "
            "hashes by identity; only the class of the models the items name is read after collapse "
            "(`replace_unhashable_unique_set_to_list`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/keep_false_dataclass_arguments.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "dataclasses.dataclass",
            "--dataclass-arguments",
            '{"eq": false, "frozen": true}',
            "--use-unique-items-as-set",
            "--formatters",
            "builtin",
            "--keep-false-dataclass-arguments",
        ),
        option="--use-unique-items-as-set-by-value",
    ),
    LegacyRow(
        "legacy-collapse-enum-literal-aliases",
        (
            "without `--keep-enum-literal-aliases`, collapse inlines the `Literal` type alias of an enum definition at "
            "its uses"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/collapse_enum_literal_aliases.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "typing.TypedDict",
            "--collapse-root-models",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--keep-enum-literal-aliases",
    ),
    LegacyRow(
        "legacy-collapse-beside-lists-and-dicts",
        (
            "under `--field-constraints` without `--keep-constrained-root-models-in-every-container`, collapse keeps a "
            "constrained root model beside a list, a dict or a union only, and inlines it into a `Sequence`, `Mapping` "
            "or set"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/collapse_constrained_roots_in_containers.json",
            "--input-file-type",
            "jsonschema",
            "--collapse-root-models",
            "--field-constraints",
            "--use-unique-items-as-set",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--keep-constrained-root-models-in-every-container",
    ),
    LegacyRow(
        "legacy-self-reference-array-untyped",
        (
            "without `--keep-self-referencing-array-items` (and always in msgspec output, which cannot decode a type "
            "alias that names itself through no Struct), a plain array whose items name the array itself is rewritten "
            "like a type list that lists the array beside other types, so its items are lost (`RootModel[list]`) and "
            "the rewritten root field is built without `--use-serialize-as-any`"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/self_referencing_array_items.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ),
        option="--keep-self-referencing-array-items",
    ),
    LegacyRow(
        "legacy-native-path-last-argument-first",
        (
            "the native field path of a generic specialization that one field of an input model names more than once "
            "is the path of its last argument (`tuple[Box[P], Box[P]]` gives `.__args__[1]`), and of the classes one "
            "field names the first is followed first: `input_model.py:_native_generic_field_paths` reads each field "
            "from its last argument to its first, where the annotation walk (`_annotation_nodes`) is in written order"
        ),
        fix=KEEP,
        witness=(
            "--input-model",
            "tests.data.python.input_model.generic_reuse:AnnotatedAliasRoot",
            "--disable-timestamp",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--input-model-ref-strategy",
            "reuse-all",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-str-subclass-host-repr",
        (
            "`represent_python_value` writes a `str` subclass that keeps the `repr()` of `str` (a `$ref` value that "
            "`--field-include-all-keys` writes into `json_schema_extra`) by the running interpreter's `repr()`, which "
            "escapes the characters its Unicode version does not print, where `stable_repr` writes them as the newest "
            "target prints them: on a host Python older than 3.15 (Unicode below 17; the target version does not "
            "select the interpreter) a `$ref` to a definition whose name holds U+A7CE, a letter of Unicode 17, is "
            "written with the escape `\\ua7ce` beside the definition's key, which prints the letter"
        ),
        fix=6906,
        witness=(
            "--input",
            "tests/data/legacy_rows/jsonschema_witnesses/host_repr_reference.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--field-include-all-keys",
        ),
    ),
    LegacyRow(
        "legacy-host-identifier-runtime-names",
        (
            "a name of a runtime class (`_python_type_runtime.py:_runtime_symbol`) with a part the running interpreter "
            "does not read as an identifier is opaque text, where the nodes take the names of the newest target: on "
            "Python 3.14 an input model field typed by a class created at run time with U+A7CE, a letter of Unicode "
            "17, in its name is written as its dotted path, without its import"
        ),
        fix=6907,
        witness=(
            "--input-model",
            "tests.data.python.input_model.pydantic_models:ModelWithPythonTypes",
        ),
    ),
    LegacyRow(
        "legacy-wire-null-unknown-type",
        (
            "the JSON types that a helper selector of a generated client reads of a property without a decided "
            "`nullable` hold the null its own type accepts as `_Models.preexisting_null` reads it: no null value of an "
            "enum (`enum: [a, null]`) and none of a type it cannot read (a custom type, an `x-python-type`, a "
            "configured import, `Any`, a reference that binds no symbol), where `TypeTables.takes_null` reads both"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/responses_extensions.yaml",
            "--input-file-type",
            "openapi",
            "--openapi-scopes",
            "schemas",
            "api",
            "--target-python-version",
            "3.11",
            "--disable-timestamp",
            "--generate-client",
            "httpx2",
            "--client-output",
            "client",
            "--client-package",
            "client",
            "--client-model-package",
            "models",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-imported-container-text",
        (
            "the schema and lexical vocabularies of `TypeTables.json_kinds` read a container class imported from a "
            "module (an abstract class or a `typing` alias) that a type names alone, as `--type-overrides` with "
            "`--type-overrides-replace-whole-type` does (`collections.abc.Mapping`), as a string, where the codec "
            "reads its slot"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/openapi/single_value_enum_as_const/api.yaml",
            "--input-file-type",
            "openapi",
            "--openapi-scopes",
            "schemas",
            "api",
            "--output-model-type",
            "dataclasses.dataclass",
            "--disable-timestamp",
            "--generate-client",
            "httpx2",
            "--client-output",
            "client",
            "--client-package",
            "client",
            "--client-model-package",
            "models",
            "--formatters",
            "builtin",
        ),
    ),
    LegacyRow(
        "legacy-running-packages-load",
        (
            "the refusal of a class beside a module of its name in its package reads an import of the module by an "
            "ancestor package, which has started before the package's `__init__.py`, as a load of that `__init__.py`, "
            "and writes the output where the classes do not evaluate their annotations as they are defined (output "
            "other than Pydantic, with deferred annotations) (`parser/_passes/_import_edges.py:init_loads`, "
            "`parser/_passes/imports.py:_class_named_like_submodule`)"
        ),
        fix=KEEP,
        witness=(
            "--input",
            "tests/data/jsonschema/submodule_named_like_package_class_root_import.json",
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "dataclasses.dataclass",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
    ),
)
"""Every ``legacy-*`` row of ``src/``, in the order of the page table."""

UNTESTED: Final[frozenset[str]] = frozenset({
    "legacy-all-of-ref-in-parsed-document",
    "legacy-all-of-root-format-kinds",
    "legacy-allof-members-under-holder",
    "legacy-anchor-parse-index",
    "legacy-anyurl-keeps-string-keywords",
    "legacy-both-prefixes",
    "legacy-class-override-keeps-container",
    "legacy-closest-registered-parent",
    "legacy-closing-ref-siblings-with-applicators",
    "legacy-collapse-any-discriminator",
    "legacy-collapse-enum-literal-aliases",
    "legacy-collapse-reuse",
    "legacy-const-drops-null",
    "legacy-const-without-literal-not-null",
    "legacy-consumed-at-object-property",
    "legacy-content-media-type-string-is-text",
    "legacy-content-parameter-copy",
    "legacy-converter-placeholder-title",
    "legacy-count-ref-member-as-written",
    "legacy-cycle-walk-reads-values-whole",
    "legacy-dataclass-follows-every-reference",
    "legacy-dataclass-overwrites-engine",
    "legacy-decide-in-union-module",
    "legacy-dedup-before-strip",
    "legacy-dedup-keys-without-unique-items",
    "legacy-dynamic-anchor-parse-index",
    "legacy-exact-ints-as-floats",
    "legacy-exact-type-key",
    "legacy-external-only-primary-first",
    "legacy-first-character-of-path-key",
    "legacy-first-come",
    "legacy-first-read-dialect",
    "legacy-fixed-definition-patterns",
    "legacy-float-const-unbounded",
    "legacy-graphql-affix-only",
    "legacy-hash-order",
    "legacy-host-identifier-runtime-names",
    "legacy-imported-container-text",
    "legacy-inherited-enum-by-default-name",
    "legacy-inherited-first-base-class",
    "legacy-inherited-reads-at-first-ask",
    "legacy-inherited-specialized-without-member-check",
    "legacy-inline-variants-at-first-ask",
    "legacy-keep-order-short-name",
    "legacy-keyless-reference-by-path",
    "legacy-l3-import-nullability",
    "legacy-length-keywords-across-kinds",
    "legacy-lenient-base64-str",
    "legacy-literal-property-replaces-declared",
    "legacy-mapped-tags-only",
    "legacy-member-null-from-hint-text",
    "legacy-merged-reference-null",
    "legacy-merged-site-per-path",
    "legacy-missing-appended",
    "legacy-module-follows-path",
    "legacy-module-restore",
    "legacy-module-suffix-model",
    "legacy-msgspec-const-not-null",
    "legacy-msgspec-decodable-value-base",
    "legacy-msgspec-float-bounds",
    "legacy-msgspec-top-level-null-as-written",
    "legacy-multiple-types-nullable-not-recorded",
    "legacy-native-path-last-argument-first",
    "legacy-nested-enum-members-untagged",
    "legacy-nested-factory-at-read",
    "legacy-nested-factory-order-before-nullability",
    "legacy-number-before-case",
    "legacy-object-extension-responses",
    "legacy-object-parameters-path",
    "legacy-one-inline-trait-headers",
    "legacy-one-literal-drops-type-null",
    "legacy-override-keeps-string-keywords",
    "legacy-own-headers-win-v2",
    "legacy-predicted-rename",
    "legacy-predicted-tag-names",
    "legacy-property-counts-beside-models",
    "legacy-python-equality-fallback",
    "legacy-python-name",
    "legacy-read-document-every-key",
    "legacy-recursion-follows-path",
    "legacy-ref-drops-duplicate-name",
    "legacy-ref-spelling-keys",
    "legacy-reference-takes-every-keyword",
    "legacy-reparse-for-final-names",
    "legacy-replaced-root-shares-path",
    "legacy-reuse-model",
    "legacy-root-records-by-option",
    "legacy-root-tuple-lengths",
    "legacy-run-alias-generator-import",
    "legacy-rw-extra-items-as-written",
    "legacy-same-name-dedup",
    "legacy-self-reference-array-untyped",
    "legacy-spelled-union-takes-schema-type",
    "legacy-stale-dedup-keys",
    "legacy-stdlib-annotated",
    "legacy-str-subclass-host-repr",
    "legacy-string-occurs-tests",
    "legacy-struct-hash-last-base-first",
    "legacy-tag-from-earlier-union",
    "legacy-tree-scope",
    "legacy-undeclared-keywords-off-literal",
    "legacy-unique-items-decided-before-collapse",
    "legacy-unique-sets-level-order",
    "legacy-value-base-changing-a-member",
    "legacy-variant-takes-parent-name",
    "legacy-warning-reads-parsed-bases",
    "legacy-whole-name-singular",
    "legacy-wire-null-unknown-type",
    "legacy-x-python-type-container-takes-slot-class",
    "legacy.L-NF",
    "legacy.alias-order",
    "legacy.enum-member-import-bindings",
    "legacy.pdc-annotated-strip",
})
"""The rows no line of ``src/`` tests: a function that names one in its docstring, or a constant or value that
names it, is read where no line decides its branch (``scripts/legacy_row_reads.py``). The set may only shrink: a new
row is tested where its branch is decided, and a row given such a line leaves the set (``tests/test_legacy_rows.py``).
"""
