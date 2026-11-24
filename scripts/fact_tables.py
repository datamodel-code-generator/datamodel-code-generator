"""Which attribute of the generation graph holds which fact, and when that fact becomes final.

``ATTRIBUTE_FACTS`` has one row per owner class and attribute, ``__dict__`` key, dict entry or cached property.
``scripts/check_generation_store_usage.py`` reads this module with ``ast`` (it never imports the package), so every
row is a literal ``AttributeFact(...)`` call whose arguments are strings, tuples, ``None`` or members of the enums
below and of ``Phase``, which the generation store enters and ``parser/_facts.py`` defines. Later readers: heki's
contracts (``scripts/build_heki_contracts.py``, among them the render facts no decision reads), the write guards of the
finality check (which attributes to guard per class), the finality fingerprints (the ``fact`` rows) and the Fact
Finality table of ``docs/architecture.md``.

Columns of a row:

* ``owner``: the class that declares the attribute (subclasses share the row).
* ``attribute``: the attribute name, the ``__dict__`` key constant, ``name["key"]`` for one entry of a dict
  attribute, or the cached property name; ``access`` says which.
* ``kind``: ``fact`` (semantic, one writer pass, frozen at its freeze point), ``presentation`` (how a fact is written
  out), ``cache`` (derived and recomputable; banned on mutable objects, so these rows only shrink) or
  ``bookkeeping`` (identity, holder edges and pending markers; writes no ``Fact`` and no ``RenderFact``).
* ``facts``: the ``Fact`` and ``RenderFact`` members a write of the attribute changes; empty for bookkeeping rows,
  for caches that hold no fact of their own, and for construction-time option copies.
* ``final_after``: the last phase that writes the attribute in today's pipeline.
* ``frozen_at``: the latest freeze point among the row's facts (every ``Fact`` freezes at B1, every ``RenderFact`` at
  its ``RENDER_FACT_FREEZE_POINTS`` entry; B1 for a row without facts), after which no pass may write the attribute
  in the target pipeline. The finality checks still check each ``Fact`` at B1 unless a pass declares an exception.
  ``None`` for caches and bookkeeping.
* ``store_ops``: the ``GenerationStore`` operations allowed to write it (``scripts/build_heki_contracts.py`` makes them
  the writers of a heki contract); every other write is recorded in ``scripts/heki_baseline.toml`` until a later
  change routes it. Bookkeeping rows name none: holder edges are enforced separately.

The checker enforces the kind rules: a fact row names a ``Fact``, a presentation row names only ``RenderFact``
members, a cache row names no ``Fact``. Three exceptions are allowed: plan rows (``Parser`` snapshots that later
passes read) are fact rows that name only ``RenderFact.PLANS``; rows holding rendered nullability (``is_optional``
and the record of where a type's None comes from) name ``Fact.NULLABLE`` and freeze at B2: render preparation writes
rendered nullability once, and only the null a msgspec field strips from its copied types is written at L7; the
schema-validator plan cache names ``RenderFact.MODULE_CODE``, the code it holds.

``DECISION_ACCESSORS`` lists the methods and properties of the graph classes that a decision may call although they read
render facts, each with what it reads, all of it final at B1; ``scripts/build_heki_contracts.py`` lets the decisions
reach their reads.

``IMPORTS_MUTATORS`` lists the methods that change an ``Imports`` (``imports.py``), which only the import ledger
(``parser/_imports_plan.py:ModuleImportLedger``), the ``imports`` module itself and the writers ``LEDGER_WRITERS`` names
call; ``scripts/build_heki_contracts.py`` makes them one heki contract.

``STORE_MEMBERS`` lists the members of the graph that only the generation store, the member's own class (a method)
or module (an attribute) and the modules a row names may use: the methods that write the graph for the store
operations that run them (the hooks a store operation runs and the reference mutators: ``MemberUse.CALL``), and the
bookkeeping attributes of a reference that its own module keeps (``MemberUse.READ``, ``MemberUse.WRITE``).
``scripts/build_heki_contracts.py`` makes each row a heki contract and lets the methods write what the store module
may write.
"""

from __future__ import annotations

from enum import Enum, unique

from typing_extensions import final

from datamodel_code_generator._records import record  # noqa: PLC2701
from datamodel_code_generator.parser._facts import Phase  # noqa: PLC2701

__all__ = [
    "ATTRIBUTE_FACTS",
    "DECISION_ACCESSORS",
    "IMPORTS_MUTATORS",
    "LEDGER_WRITERS",
    "RENDER_FACT_FREEZE_POINTS",
    "STORE_MEMBERS",
    "Access",
    "AttributeFact",
    "DecisionAccessor",
    "Fact",
    "FactKind",
    "FreezePoint",
    "LedgerWriter",
    "MemberUse",
    "RenderFact",
    "StoreMember",
    "freeze_point",
    "holds_render_facts",
]


@unique
class FactKind(Enum):
    """What an attribute holds."""

    FACT = "fact"
    PRESENTATION = "presentation"
    CACHE = "cache"
    BOOKKEEPING = "bookkeeping"


@unique
class Fact(Enum):
    """Semantic facts of the generated models that are final at B1."""

    MODELS_EXIST = "models_exist"
    MODEL_KIND = "model_kind"
    BASES = "bases"
    FIELD_SET = "field_set"
    FIELD_NAMES = "field_names"
    FIELD_TYPES = "field_types"
    CONTAINERS = "containers"
    REQUIRED = "required"
    NULLABLE = "nullable"
    DEFAULTS = "defaults"
    CONSTRAINTS = "constraints"
    DISCRIMINATOR_TAGS = "discriminator_tags"
    TYPE_OVERRIDES = "type_overrides"
    FIELD_ALIASES = "field_aliases"
    METADATA = "metadata"


@unique
class RenderFact(Enum):
    """What render preparation, final naming and import collection write."""

    FIELD_ORDER = "field_order"
    KEYWORD_ONLY = "keyword_only"
    FORCED_ASSIGNMENT = "forced_assignment"
    ASSIGNMENT_FORM = "assignment_form"
    QUOTING = "quoting"
    UNBOUND_FIELDS = "unbound_fields"
    DEFAULT_VALIDATION = "default_validation"
    DEFAULT_EXPRESSION = "default_expression"
    IMPORT_ALIASES = "import_aliases"
    TYPE_ALIAS_BODY = "type_alias_body"
    SET_ITEM_HASH = "set_item_hash"
    CLASS_NAMES = "class_names"
    MODULE_MEMBERSHIP = "module_membership"
    MODEL_ORDER = "model_order"
    MODULE_IMPORTS = "module_imports"
    BACKEND_CONFIG = "backend_config"
    MODULE_CODE = "module_code"
    PLANS = "plans"
    STDERR = "stderr"
    """The warnings on stderr, in order: no attribute holds it, so that two passes that warn are ordered."""


@unique
class FreezePoint(Enum):
    """Where an attribute stops changing in the target pipeline."""

    B1 = "facts-final"
    B2 = "render-ready"
    RENDER = "render"


@unique
class Access(Enum):
    """How an attribute is stored and which writes reach it.

    ``ATTRIBUTE``: assignment, deletion and ``setattr``. ``CONTAINER``: also item writes and list, dict or set mutators.
    ``ITEM``: one key of a dict attribute. ``DICT_KEY``: an entry of the object's ``__dict__`` under a module constant
    or literal key. ``CACHED_PROPERTY``: a ``functools.cached_property``, which stores its value in ``__dict__``.
    """

    ATTRIBUTE = "attribute"
    CONTAINER = "container"
    ITEM = "item"
    DICT_KEY = "dict_key"
    CACHED_PROPERTY = "cached_property"


RENDER_FACT_FREEZE_POINTS: dict[RenderFact, FreezePoint] = {
    RenderFact.FIELD_ORDER: FreezePoint.B2,
    RenderFact.KEYWORD_ONLY: FreezePoint.B2,
    RenderFact.FORCED_ASSIGNMENT: FreezePoint.B2,
    RenderFact.ASSIGNMENT_FORM: FreezePoint.B2,
    RenderFact.QUOTING: FreezePoint.RENDER,
    RenderFact.UNBOUND_FIELDS: FreezePoint.RENDER,
    RenderFact.DEFAULT_VALIDATION: FreezePoint.B2,
    RenderFact.DEFAULT_EXPRESSION: FreezePoint.B2,
    RenderFact.IMPORT_ALIASES: FreezePoint.RENDER,
    RenderFact.TYPE_ALIAS_BODY: FreezePoint.B2,
    RenderFact.SET_ITEM_HASH: FreezePoint.B2,
    RenderFact.CLASS_NAMES: FreezePoint.RENDER,
    RenderFact.MODULE_MEMBERSHIP: FreezePoint.RENDER,
    RenderFact.MODEL_ORDER: FreezePoint.B2,
    RenderFact.MODULE_IMPORTS: FreezePoint.RENDER,
    RenderFact.BACKEND_CONFIG: FreezePoint.B2,
    RenderFact.MODULE_CODE: FreezePoint.RENDER,
    RenderFact.PLANS: FreezePoint.B1,
    RenderFact.STDERR: FreezePoint.RENDER,
}
"""Where each render fact freezes: render preparation ends at B2; names, imports, quoting and module code at render."""


@final
@record
class AttributeFact:
    """One row of the attribute-to-fact table."""

    owner: str
    attribute: str
    kind: FactKind
    facts: tuple[Fact | RenderFact, ...]
    final_after: Phase
    frozen_at: FreezePoint | None
    store_ops: tuple[str, ...] = ()
    access: Access = Access.ATTRIBUTE


ATTRIBUTE_FACTS: tuple[AttributeFact, ...] = (
    AttributeFact(
        "DataType",
        "type",
        FactKind.FACT,
        (Fact.FIELD_TYPES, RenderFact.IMPORT_ALIASES),
        Phase.IMPORTS,
        FreezePoint.RENDER,
        store_ops=("set_type_expressions",),
    ),
    AttributeFact(
        "DataType",
        "reference",
        FactKind.FACT,
        (Fact.FIELD_TYPES,),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=(
            "collapse_root_data_type",
            "detach_data_type_ref",
            "detach_model_data_type_refs",
            "redirect_model_reference_users",
            "redirect_reference_users",
            "replace_data_type_ref",
        ),
    ),
    AttributeFact(
        "DataType",
        "data_types",
        FactKind.FACT,
        (Fact.FIELD_TYPES, RenderFact.TYPE_ALIAS_BODY),
        Phase.RENDER_PREPARATION,
        FreezePoint.B2,
        store_ops=(
            "drop_union_member_constraints",
            "replace_nested_data_type",
            "set_nested_data_types",
            "swap_data_type",
        ),
        access=Access.CONTAINER,
    ),
    AttributeFact(
        "DataType",
        "dict_key",
        FactKind.FACT,
        (Fact.FIELD_TYPES,),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=("replace_nested_data_type",),
    ),
    AttributeFact("DataType", "is_func", FactKind.FACT, (Fact.FIELD_TYPES,), Phase.PARSE, FreezePoint.B1),
    AttributeFact(
        "DataType",
        "kwargs",
        FactKind.FACT,
        (Fact.CONSTRAINTS, RenderFact.IMPORT_ALIASES),
        Phase.IMPORTS,
        FreezePoint.RENDER,
        store_ops=("set_type_expressions",),
        access=Access.CONTAINER,
    ),
    AttributeFact(
        "DataType",
        "import_",
        FactKind.FACT,
        (Fact.FIELD_TYPES, RenderFact.IMPORT_ALIASES),
        Phase.RENDER,
        FreezePoint.RENDER,
        store_ops=("override_type_import", "set_type_expressions"),
    ),
    AttributeFact(
        "DataType",
        "python_type",
        FactKind.FACT,
        (Fact.FIELD_TYPES, RenderFact.IMPORT_ALIASES),
        Phase.IMPORTS,
        FreezePoint.RENDER,
        store_ops=("set_type_expressions",),
    ),
    AttributeFact("DataType", "python_version", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1),
    AttributeFact(
        "DataType",
        "is_optional",
        FactKind.FACT,
        (Fact.NULLABLE,),
        Phase.RENDER_PREPARATION,
        FreezePoint.B2,
        store_ops=("drop_held_null", "write_rendered_null"),
    ),
    AttributeFact("DataType", "is_dict", FactKind.FACT, (Fact.CONTAINERS,), Phase.MODULES, FreezePoint.B1),
    AttributeFact("DataType", "is_list", FactKind.FACT, (Fact.CONTAINERS,), Phase.MODULES, FreezePoint.B1),
    AttributeFact("DataType", "is_set", FactKind.FACT, (Fact.CONTAINERS,), Phase.MODULES, FreezePoint.B1),
    AttributeFact("DataType", "is_frozen_set", FactKind.FACT, (Fact.CONTAINERS,), Phase.MODULES, FreezePoint.B1),
    AttributeFact("DataType", "is_mapping", FactKind.FACT, (Fact.CONTAINERS,), Phase.MODULES, FreezePoint.B1),
    AttributeFact("DataType", "is_sequence", FactKind.FACT, (Fact.CONTAINERS,), Phase.MODULES, FreezePoint.B1),
    AttributeFact("DataType", "is_tuple", FactKind.FACT, (Fact.CONTAINERS,), Phase.MODULES, FreezePoint.B1),
    AttributeFact("DataType", "tuple_item_count", FactKind.FACT, (Fact.CONTAINERS,), Phase.MODULES, FreezePoint.B1),
    AttributeFact("DataType", "given_container", FactKind.FACT, (Fact.CONTAINERS,), Phase.PARSE, FreezePoint.B1),
    AttributeFact("DataType", "is_custom_type", FactKind.FACT, (Fact.FIELD_TYPES,), Phase.PARSE, FreezePoint.B1),
    AttributeFact(
        "DataType",
        "literals",
        FactKind.FACT,
        (Fact.FIELD_TYPES,),
        Phase.MODULES,
        FreezePoint.B1,
        access=Access.CONTAINER,
    ),
    AttributeFact(
        "DataType",
        "enum_member_literals",
        FactKind.FACT,
        (Fact.FIELD_TYPES, RenderFact.IMPORT_ALIASES),
        Phase.IMPORTS,
        FreezePoint.RENDER,
        store_ops=("name_enum_member_literal_class",),
        access=Access.CONTAINER,
    ),
    AttributeFact(
        "DataType",
        "enum_member_tag_values",
        FactKind.FACT,
        (Fact.FIELD_TYPES,),
        Phase.MODULES,
        FreezePoint.B1,
    ),
    AttributeFact(
        "DataType",
        "use_standard_collections",
        FactKind.PRESENTATION,
        (RenderFact.QUOTING,),
        Phase.MODULES,
        FreezePoint.RENDER,
        store_ops=("use_typing_generics",),
    ),
    AttributeFact(
        "Member",
        "alias",
        FactKind.PRESENTATION,
        (RenderFact.IMPORT_ALIASES,),
        Phase.IMPORTS,
        FreezePoint.RENDER,
        store_ops=("name_enum_member",),
    ),
    AttributeFact("DataType", "use_generic_container", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1),
    AttributeFact(
        "DataType",
        "use_union_operator",
        FactKind.PRESENTATION,
        (RenderFact.QUOTING,),
        Phase.MODULES,
        FreezePoint.RENDER,
        store_ops=("drop_union_operator",),
    ),
    AttributeFact("DataType", "preserve_union_member_order", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1),
    AttributeFact(
        "DataType",
        "alias",
        FactKind.PRESENTATION,
        (RenderFact.IMPORT_ALIASES, RenderFact.QUOTING),
        Phase.IMPORTS,
        FreezePoint.RENDER,
        store_ops=("name_reference",),
    ),
    AttributeFact(
        "DataType",
        "parent",
        FactKind.BOOKKEEPING,
        (),
        Phase.RENDER_PREPARATION,
        None,
    ),
    AttributeFact("DataType", "children", FactKind.BOOKKEEPING, (), Phase.PARSE, None, access=Access.CONTAINER),
    AttributeFact("DataType", "strict", FactKind.FACT, (Fact.FIELD_TYPES,), Phase.PARSE, FreezePoint.B1),
    AttributeFact("DataType", "treat_dot_as_module", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1),
    AttributeFact("DataType", "use_serialize_as_any", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1),
    AttributeFact(
        "DataType",
        "discriminator",
        FactKind.FACT,
        (Fact.DISCRIMINATOR_TAGS,),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=("drop_field_discriminator", "set_type_discriminator"),
    ),
    AttributeFact(
        "DataType",
        "_RUNTIME_EXPRESSION_IMPORTS_DATA_TYPE_KEY",
        FactKind.PRESENTATION,
        (RenderFact.MODULE_IMPORTS,),
        Phase.IMPORTS,
        FreezePoint.RENDER,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataType",
        "_LITERAL_IMPORT_DATA_TYPE_KEY",
        FactKind.PRESENTATION,
        (RenderFact.IMPORT_ALIASES,),
        Phase.MODULES,
        FreezePoint.RENDER,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataType",
        "_FORWARD_REFERENCE_DATA_TYPE_KEY",
        FactKind.PRESENTATION,
        (RenderFact.QUOTING,),
        Phase.MODULES,
        FreezePoint.RENDER,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataType",
        "_ENUM_MEMBER_LITERAL_REFERENCE_DATA_TYPE_KEY",
        FactKind.FACT,
        (Fact.FIELD_TYPES,),
        Phase.MODULES,
        FreezePoint.B1,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataType",
        "_SITE_CONSTRAINTS_KEY",
        FactKind.FACT,
        (Fact.CONSTRAINTS,),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=("record_site_constraints",),
        access=Access.DICT_KEY,
    ),
    AttributeFact("DataType", "TYPE_ORIGIN_KEY", FactKind.BOOKKEEPING, (), Phase.PARSE, None, access=Access.DICT_KEY),
    AttributeFact(
        "DataType", "BINARY_CONTENT_KEY", FactKind.BOOKKEEPING, (), Phase.PARSE, None, access=Access.DICT_KEY
    ),
    AttributeFact("DataType", "_JSON_NUMBER_KEY", FactKind.BOOKKEEPING, (), Phase.PARSE, None, access=Access.DICT_KEY),
    AttributeFact("DataType", "UNIQUE_ITEMS_KEY", FactKind.BOOKKEEPING, (), Phase.PARSE, None, access=Access.DICT_KEY),
    AttributeFact(
        "DataType", "_UNIQUE_ITEMS_SET_KEY", FactKind.BOOKKEEPING, (), Phase.MODULES, None, access=Access.DICT_KEY
    ),
    AttributeFact(
        "DataType", "_INLINED_SITE_KEY", FactKind.BOOKKEEPING, (), Phase.MODULES, None, access=Access.DICT_KEY
    ),
    AttributeFact("DataType", "ENUM_LITERAL_KEY", FactKind.BOOKKEEPING, (), Phase.PARSE, None, access=Access.DICT_KEY),
    AttributeFact(
        "DataType",
        "_UNTYPED_VALUE_DATA_TYPE_KEY",
        FactKind.BOOKKEEPING,
        (),
        Phase.PARSE,
        None,
        access=Access.DICT_KEY,
    ),
    AttributeFact("DataType", "SUBTYPE_REF_KEY", FactKind.BOOKKEEPING, (), Phase.PARSE, None, access=Access.DICT_KEY),
    AttributeFact(
        "DataType",
        "_TYPE_NULL_DATA_TYPE_KEY",
        FactKind.PRESENTATION,
        (Fact.NULLABLE,),
        Phase.RENDER,
        FreezePoint.B2,
        store_ops=(
            "clear_target_null",
            "drop_held_null",
            "mark_target_null",
            "redirect_model_reference_users",
            "redirect_reference_users",
            "write_rendered_null",
        ),
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "AnnotatedConstraintDataType",
        "constrained_base",
        FactKind.FACT,
        (Fact.FIELD_TYPES,),
        Phase.PARSE,
        FreezePoint.B1,
    ),
    AttributeFact(
        "_MetaAnnotatedType",
        "meta",
        FactKind.FACT,
        (Fact.CONSTRAINTS,),
        Phase.PARSE,
        FreezePoint.B1,
    ),
    AttributeFact(
        "_MetaType",
        "meta_keywords",
        FactKind.FACT,
        (Fact.CONSTRAINTS,),
        Phase.PARSE,
        FreezePoint.B1,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "name",
        FactKind.FACT,
        (Fact.FIELD_NAMES,),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=("rename_field", "set_field_identity"),
    ),
    AttributeFact(
        "DataModelFieldBase",
        "default",
        FactKind.FACT,
        (Fact.DEFAULTS, RenderFact.DEFAULT_EXPRESSION),
        Phase.IMPORTS,
        FreezePoint.B2,
        store_ops=("set_field_default", "set_field_runtime_default", "apply_literal_default", "omit_field_default"),
    ),
    AttributeFact(
        "DataModelFieldBase",
        "required",
        FactKind.FACT,
        (Fact.REQUIRED,),
        Phase.CROSS_MODULE,
        FreezePoint.B1,
        store_ops=("require_field", "apply_literal_default"),
    ),
    AttributeFact(
        "DataModelFieldBase",
        "alias",
        FactKind.FACT,
        (Fact.FIELD_ALIASES,),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=("rename_field", "set_field_identity"),
    ),
    AttributeFact(
        "DataModelFieldBase",
        "validation_aliases",
        FactKind.FACT,
        (Fact.FIELD_ALIASES,),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=("set_field_identity",),
        access=Access.CONTAINER,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "serialization_alias",
        FactKind.FACT,
        (Fact.FIELD_ALIASES,),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=("set_field_identity",),
    ),
    AttributeFact(
        "DataModelFieldBase",
        "data_type",
        FactKind.FACT,
        (Fact.FIELD_TYPES, RenderFact.TYPE_ALIAS_BODY),
        Phase.RENDER_PREPARATION,
        FreezePoint.B2,
        store_ops=("apply_const", "replace_field_type", "swap_data_type"),
    ),
    AttributeFact(
        "DataModelFieldBase",
        "constraints",
        FactKind.FACT,
        (Fact.CONSTRAINTS,),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=("set_field_constraints",),
    ),
    AttributeFact("DataModelFieldBase", "strip_default_none", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1),
    AttributeFact(
        "DataModelFieldBase",
        "order_constructor_fields_by_written_defaults",
        FactKind.PRESENTATION,
        (),
        Phase.PARSE,
        FreezePoint.B1,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "nullable",
        FactKind.FACT,
        (Fact.NULLABLE,),
        Phase.CROSS_MODULE,
        FreezePoint.B1,
        store_ops=("apply_literal_default", "omit_field_default"),
    ),
    AttributeFact(
        "DataModelFieldBase",
        "parent",
        FactKind.BOOKKEEPING,
        (),
        Phase.MODULES,
        None,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "extras",
        FactKind.FACT,
        (Fact.METADATA, RenderFact.IMPORT_ALIASES),
        Phase.IMPORTS,
        FreezePoint.RENDER,
        store_ops=("drop_field_discriminator", "drop_field_extra", "set_field_extra", "set_field_extras"),
        access=Access.CONTAINER,
    ),
    AttributeFact(
        "DataModelFieldBase",
        'extras["discriminator"]',
        FactKind.FACT,
        (Fact.DISCRIMINATOR_TAGS,),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=("drop_field_discriminator", "set_field_extra"),
        access=Access.ITEM,
    ),
    AttributeFact(
        "DataModelFieldBase",
        'extras["default_factory"]',
        FactKind.FACT,
        (Fact.DEFAULTS,),
        Phase.MODULES,
        FreezePoint.B1,
        access=Access.ITEM,
    ),
    AttributeFact(
        "DataModelFieldBase",
        'extras["init"]',
        FactKind.FACT,
        (Fact.REQUIRED,),
        Phase.MODULES,
        FreezePoint.B1,
        access=Access.ITEM,
    ),
    AttributeFact(
        "DataModelFieldBase",
        'extras["is_classvar"]',
        FactKind.FACT,
        (Fact.FIELD_SET, Fact.DISCRIMINATOR_TAGS),
        Phase.CROSS_MODULE,
        FreezePoint.B1,
        store_ops=("drop_field_extra", "set_field_extra"),
        access=Access.ITEM,
    ),
    AttributeFact(
        "DataModelFieldBase",
        'extras["_compiled_python_pattern"]',
        FactKind.PRESENTATION,
        (RenderFact.IMPORT_ALIASES,),
        Phase.IMPORTS,
        FreezePoint.RENDER,
        store_ops=("set_field_extra",),
        access=Access.ITEM,
    ),
    AttributeFact(
        "DataModelFieldBase",
        'extras["kw_only"]',
        FactKind.PRESENTATION,
        (RenderFact.KEYWORD_ONLY,),
        Phase.MODULES,
        FreezePoint.B2,
        access=Access.ITEM,
    ),
    AttributeFact(
        "DataModelFieldBase",
        'extras["validate_default"]',
        FactKind.PRESENTATION,
        (RenderFact.DEFAULT_VALIDATION,),
        Phase.MODULES,
        FreezePoint.B2,
        access=Access.ITEM,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "use_annotated",
        FactKind.PRESENTATION,
        (RenderFact.ASSIGNMENT_FORM,),
        Phase.MODULES,
        FreezePoint.B2,
        store_ops=("write_field_call_in_annotated",),
    ),
    AttributeFact("DataModelFieldBase", "use_serialize_as_any", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1),
    AttributeFact("DataModelFieldBase", "has_default", FactKind.FACT, (Fact.DEFAULTS,), Phase.MODULES, FreezePoint.B1),
    AttributeFact(
        "DataModelFieldBase", "use_field_description", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1
    ),
    AttributeFact(
        "DataModelFieldBase", "use_field_description_example", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1
    ),
    AttributeFact(
        "DataModelFieldBase", "use_inline_field_description", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1
    ),
    AttributeFact("DataModelFieldBase", "const", FactKind.FACT, (Fact.FIELD_TYPES,), Phase.MODULES, FreezePoint.B1),
    AttributeFact(
        "DataModelFieldBase",
        "original_name",
        FactKind.FACT,
        (Fact.FIELD_NAMES,),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=("set_field_identity",),
    ),
    AttributeFact("DataModelFieldBase", "use_default_kwarg", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1),
    AttributeFact("DataModelFieldBase", "use_missing_sentinel", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1),
    AttributeFact(
        "DataModelFieldBase", "use_one_literal_as_default", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1
    ),
    AttributeFact(
        "DataModelFieldBase", "target_python_version", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1
    ),
    AttributeFact(
        "DataModelFieldBase", "type_has_null", FactKind.FACT, (Fact.NULLABLE,), Phase.MODULES, FreezePoint.B1
    ),
    AttributeFact("DataModelFieldBase", "read_only", FactKind.FACT, (Fact.METADATA,), Phase.MODULES, FreezePoint.B1),
    AttributeFact("DataModelFieldBase", "write_only", FactKind.FACT, (Fact.METADATA,), Phase.MODULES, FreezePoint.B1),
    AttributeFact("DataModelFieldBase", "use_frozen_field", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1),
    AttributeFact(
        "DataModelFieldBase",
        "use_serialization_alias",
        FactKind.PRESENTATION,
        (),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=("set_field_identity",),
    ),
    AttributeFact(
        "DataModelFieldBase",
        "use_default_factory_for_optional_nested_models",
        FactKind.PRESENTATION,
        (),
        Phase.PARSE,
        FreezePoint.B1,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "use_default_with_required",
        FactKind.FACT,
        (Fact.REQUIRED,),
        Phase.CROSS_MODULE,
        FreezePoint.B1,
        store_ops=("require_field",),
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_UNTAKEN_CONSTRAINTS_KEY",
        FactKind.FACT,
        (Fact.CONSTRAINTS,),
        Phase.MODULES,
        FreezePoint.B1,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_MEMBER_UNTAKEN_CONSTRAINTS_KEY",
        FactKind.FACT,
        (Fact.CONSTRAINTS,),
        Phase.MODULES,
        FreezePoint.B1,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_OVERRIDDEN_CONSTRAINTS_KEY",
        FactKind.FACT,
        (Fact.CONSTRAINTS,),
        Phase.MODULES,
        FreezePoint.B1,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_TYPE_DEPENDENT_CONSTRAINTS_KEY",
        FactKind.FACT,
        (Fact.CONSTRAINTS,),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=("set_field_constraints",),
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "UNION_SIBLING_CONSTRAINTS_KEY",
        FactKind.FACT,
        (Fact.CONSTRAINTS,),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=("set_field_constraints",),
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_UNCONSTRAINED_UNION_MEMBERS_KEY",
        FactKind.FACT,
        (Fact.CONSTRAINTS,),
        Phase.MODULES,
        FreezePoint.B1,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "NULLABLE_WITHOUT_DEFAULT_KEY",
        FactKind.FACT,
        (Fact.NULLABLE,),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=("record_nullable_without_default", "apply_literal_default"),
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_RAW_SCHEMA_DEFAULT_KEY",
        FactKind.FACT,
        (Fact.DEFAULTS,),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=("record_raw_schema_default",),
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_RAW_SCHEMA_EXPLICIT_FIELD_EXTRAS_KEY",
        FactKind.FACT,
        (Fact.METADATA,),
        Phase.MODULES,
        FreezePoint.B1,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_ALIAS_RESOLUTION_CLASS_NAME_KEY",
        FactKind.FACT,
        (Fact.FIELD_ALIASES,),
        Phase.MODULES,
        FreezePoint.B1,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_RUNTIME_EXPRESSION_IMPORTS_FIELD_KEY",
        FactKind.PRESENTATION,
        (RenderFact.MODULE_IMPORTS,),
        Phase.IMPORTS,
        FreezePoint.RENDER,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_forced_field_assignment",
        FactKind.PRESENTATION,
        (RenderFact.FORCED_ASSIGNMENT,),
        Phase.MODULES,
        FreezePoint.B2,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_UNBOUND_FIELD_KEY",
        FactKind.PRESENTATION,
        (RenderFact.UNBOUND_FIELDS,),
        Phase.MODULES,
        FreezePoint.RENDER,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_DEFERRED_INHERITED_FIELD_KEY",
        FactKind.BOOKKEEPING,
        (),
        Phase.MODULES,
        None,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_DEFERRED_INHERITED_CLASS_KEY",
        FactKind.BOOKKEEPING,
        (),
        Phase.MODULES,
        None,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_DEFERRED_INHERITED_TYPE_KEY",
        FactKind.BOOKKEEPING,
        (),
        Phase.MODULES,
        None,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_REQUIRED_INHERITED_DEFAULT_FACTORY_KEY",
        FactKind.BOOKKEEPING,
        (),
        Phase.MODULES,
        None,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_REQUIRED_INHERITED_INIT_KEY",
        FactKind.BOOKKEEPING,
        (),
        Phase.MODULES,
        None,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase", "FIELD_ORIGIN_KEY", FactKind.BOOKKEEPING, (), Phase.MODULES, None, access=Access.DICT_KEY
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_TYPE_LIST_UNIQUE_ITEMS_KEY",
        FactKind.BOOKKEEPING,
        (),
        Phase.PARSE,
        None,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "ConstraintsBase", "has_constraints", FactKind.CACHE, (), Phase.RENDER, None, access=Access.CACHED_PROPERTY
    ),
    AttributeFact(
        "ConstraintsBase", "_exclude_unset_dump", FactKind.CACHE, (), Phase.RENDER, None, access=Access.CACHED_PROPERTY
    ),
    AttributeFact(
        "_RenderedDataModelField", "_field", FactKind.BOOKKEEPING, (), Phase.RENDER, None, access=Access.DICT_KEY
    ),
    AttributeFact(
        "_RenderedDataModelField", "docstring", FactKind.CACHE, (), Phase.RENDER, None, access=Access.DICT_KEY
    ),
    AttributeFact("_RenderedDataModelField", "field", FactKind.CACHE, (), Phase.RENDER, None, access=Access.DICT_KEY),
    AttributeFact(
        "_RenderedDataModelField", "annotated", FactKind.CACHE, (), Phase.RENDER, None, access=Access.DICT_KEY
    ),
    AttributeFact(
        "DataModel",
        "fields",
        FactKind.FACT,
        (Fact.FIELD_SET, RenderFact.FIELD_ORDER),
        Phase.RENDER_PREPARATION,
        FreezePoint.B2,
        store_ops=(
            "append_field",
            "insert_field",
            "set_fields",
            "use_assignment_for_forward_references",
        ),
        access=Access.CONTAINER,
    ),
    AttributeFact(
        "DataModel",
        "base_classes",
        FactKind.FACT,
        (Fact.BASES,),
        Phase.CROSS_MODULE,
        FreezePoint.B1,
        store_ops=("reset_base_classes", "set_base_classes"),
        access=Access.CONTAINER,
    ),
    AttributeFact("DataModel", "reference", FactKind.BOOKKEEPING, (), Phase.MODULES, None),
    AttributeFact(
        "DataModel",
        "class_name",
        FactKind.PRESENTATION,
        (RenderFact.CLASS_NAMES,),
        Phase.IMPORTS,
        FreezePoint.RENDER,
        store_ops=("rename_model",),
    ),
    AttributeFact(
        "DataModel", "keyword_only", FactKind.PRESENTATION, (RenderFact.KEYWORD_ONLY,), Phase.PARSE, FreezePoint.B2
    ),
    AttributeFact(
        "DataModel", "frozen", FactKind.PRESENTATION, (RenderFact.BACKEND_CONFIG,), Phase.PARSE, FreezePoint.B2
    ),
    AttributeFact(
        "DataModel",
        "dataclass_arguments",
        FactKind.PRESENTATION,
        (RenderFact.BACKEND_CONFIG, RenderFact.KEYWORD_ONLY),
        Phase.PARSE,
        FreezePoint.B2,
        access=Access.CONTAINER,
    ),
    AttributeFact(
        "DataModel",
        "decorators",
        FactKind.PRESENTATION,
        (RenderFact.MODULE_CODE,),
        Phase.PARSE,
        FreezePoint.RENDER,
        access=Access.CONTAINER,
    ),
    AttributeFact(
        "DataModel",
        "_additional_imports",
        FactKind.PRESENTATION,
        (RenderFact.MODULE_IMPORTS,),
        Phase.RENDER,
        FreezePoint.RENDER,
        store_ops=("add_model_import",),
        access=Access.CONTAINER,
    ),
    AttributeFact("DataModel", "custom_base_class", FactKind.FACT, (Fact.BASES,), Phase.PARSE, FreezePoint.B1),
    AttributeFact(
        "DataModel",
        "file_path",
        FactKind.PRESENTATION,
        (RenderFact.MODULE_MEMBERSHIP,),
        Phase.CROSS_MODULE,
        FreezePoint.RENDER,
        store_ops=("rename_model", "update_model_reference"),
    ),
    AttributeFact(
        "DataModel",
        "extra_template_data",
        FactKind.PRESENTATION,
        (RenderFact.BACKEND_CONFIG,),
        Phase.RENDER,
        FreezePoint.B2,
        access=Access.CONTAINER,
    ),
    AttributeFact(
        "DataModel",
        "_internal_template_data",
        FactKind.PRESENTATION,
        (RenderFact.BACKEND_CONFIG, RenderFact.MODULE_CODE, RenderFact.SET_ITEM_HASH),
        Phase.RENDER,
        FreezePoint.RENDER,
        access=Access.CONTAINER,
    ),
    AttributeFact(
        "DataModel",
        "methods",
        FactKind.PRESENTATION,
        (RenderFact.MODULE_CODE,),
        Phase.PARSE,
        FreezePoint.RENDER,
        access=Access.CONTAINER,
    ),
    AttributeFact("DataModel", "description", FactKind.FACT, (Fact.METADATA,), Phase.PARSE, FreezePoint.B1),
    AttributeFact("DataModel", "default", FactKind.FACT, (Fact.DEFAULTS,), Phase.PARSE, FreezePoint.B1),
    AttributeFact("DataModel", "_nullable", FactKind.FACT, (Fact.NULLABLE,), Phase.PARSE, FreezePoint.B1),
    AttributeFact("DataModel", "_custom_template_dir", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1),
    AttributeFact("DataModel", "_treat_dot_as_module", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1),
    AttributeFact("DataModel", "target_python_version", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1),
    AttributeFact(
        "DataModel",
        "_field_name_bindings",
        FactKind.PRESENTATION,
        (RenderFact.UNBOUND_FIELDS,),
        Phase.IMPORTS,
        FreezePoint.RENDER,
    ),
    AttributeFact(
        "DataModel",
        "_builtin_name_bindings",
        FactKind.PRESENTATION,
        (RenderFact.IMPORT_ALIASES,),
        Phase.IMPORTS,
        FreezePoint.RENDER,
        store_ops=("bind_builtin_names",),
    ),
    AttributeFact(
        "DataModel", "discriminator_tag", FactKind.FACT, (Fact.DISCRIMINATOR_TAGS,), Phase.CROSS_MODULE, FreezePoint.B1
    ),
    AttributeFact(
        "DataModel",
        "serialized_as_any",
        FactKind.FACT,
        (Fact.FIELD_TYPES,),
        Phase.DECIDE,
        FreezePoint.B1,
        store_ops=("mark_serialized_as_any",),
    ),
    AttributeFact(
        "DataModel",
        "has_forward_reference",
        FactKind.PRESENTATION,
        (RenderFact.QUOTING,),
        Phase.MODULES,
        FreezePoint.RENDER,
        store_ops=("mark_forward_references",),
    ),
    AttributeFact("DataModel", "reused_reference", FactKind.FACT, (Fact.MODELS_EXIST,), Phase.MODULES, FreezePoint.B1),
    AttributeFact(
        "DataModel",
        "_PATH_PARTNER_KEY",
        FactKind.BOOKKEEPING,
        (),
        Phase.MODULES,
        None,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModel",
        "_NESTED_MODEL_DEFAULT_FACTORY_ORDER_KEY",
        FactKind.PRESENTATION,
        (RenderFact.DEFAULT_EXPRESSION,),
        Phase.IMPORTS,
        FreezePoint.B2,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModel",
        "_NESTED_MODEL_DEFAULT_FACTORY_RECURSIVE_KEYS_KEY",
        FactKind.PRESENTATION,
        (RenderFact.DEFAULT_EXPRESSION,),
        Phase.IMPORTS,
        FreezePoint.B2,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModel",
        "_SCHEMA_RUNTIME_VALIDATION_MODULE_PLAN_CACHE_KEY",
        FactKind.CACHE,
        (RenderFact.MODULE_CODE,),
        Phase.RENDER,
        None,
        access=Access.DICT_KEY,
    ),
    AttributeFact("DataModel", "path", FactKind.CACHE, (), Phase.RENDER, None, access=Access.CACHED_PROPERTY),
    AttributeFact(
        "DataModel", "_uses_custom_root_template", FactKind.CACHE, (), Phase.RENDER, None, access=Access.CACHED_PROPERTY
    ),
    AttributeFact(
        "TemplateBase", "template_file_path", FactKind.CACHE, (), Phase.RENDER, None, access=Access.CACHED_PROPERTY
    ),
    AttributeFact("TemplateBase", "template", FactKind.CACHE, (), Phase.RENDER, None, access=Access.CACHED_PROPERTY),
    AttributeFact("DataClass", "_lookaround_regex_engine_checked", FactKind.CACHE, (), Phase.RENDER, None),
    AttributeFact(
        "RootModel",
        "_requires_literal_validation",
        FactKind.PRESENTATION,
        (RenderFact.MODULE_CODE,),
        Phase.PARSE,
        FreezePoint.RENDER,
    ),
    AttributeFact("RootModel", "_literal_values", FactKind.FACT, (Fact.CONSTRAINTS,), Phase.PARSE, FreezePoint.B1),
    AttributeFact(
        "BaseModel",
        "_validator_bindings",
        FactKind.PRESENTATION,
        (RenderFact.MODULE_CODE,),
        Phase.PARSE,
        FreezePoint.RENDER,
    ),
    AttributeFact(
        "TypedDict", "use_total_false_for_typed_dict", FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1
    ),
    AttributeFact(
        "Reference",
        "path",
        FactKind.PRESENTATION,
        (RenderFact.MODULE_MEMBERSHIP,),
        Phase.CROSS_MODULE,
        FreezePoint.RENDER,
        store_ops=("rename_model", "update_model_reference"),
    ),
    AttributeFact(
        "Reference", "original_name", FactKind.PRESENTATION, (RenderFact.CLASS_NAMES,), Phase.PARSE, FreezePoint.RENDER
    ),
    AttributeFact(
        "Reference",
        "name",
        FactKind.PRESENTATION,
        (RenderFact.CLASS_NAMES,),
        Phase.IMPORTS,
        FreezePoint.RENDER,
        store_ops=("rename_model",),
    ),
    AttributeFact(
        "Reference",
        "duplicate_name",
        FactKind.PRESENTATION,
        (RenderFact.CLASS_NAMES,),
        Phase.IMPORTS,
        FreezePoint.RENDER,
        store_ops=("rename_model",),
    ),
    AttributeFact("Reference", "loaded", FactKind.BOOKKEEPING, (), Phase.PARSE, None),
    AttributeFact("Reference", "source", FactKind.BOOKKEEPING, (), Phase.MODULES, None),
    AttributeFact("Reference", "children", FactKind.BOOKKEEPING, (), Phase.MODULES, None, access=Access.CONTAINER),
    AttributeFact(
        "Reference", "SCHEMA_LOCATION_KEY", FactKind.BOOKKEEPING, (), Phase.PARSE, None, access=Access.DICT_KEY
    ),
    AttributeFact(
        "Reference", "SCHEMA_LOCATION_CONFLICT_KEY", FactKind.BOOKKEEPING, (), Phase.PARSE, None, access=Access.DICT_KEY
    ),
    AttributeFact(
        "Reference", "_DERIVED_FROM_KEY", FactKind.BOOKKEEPING, (), Phase.PARSE, None, access=Access.DICT_KEY
    ),
    AttributeFact(
        "Reference", "FIELDLESS_SITE_KEY", FactKind.BOOKKEEPING, (), Phase.PARSE, None, access=Access.DICT_KEY
    ),
    AttributeFact(
        "Reference", "REF_BRANCH_SCHEMA_KEY", FactKind.BOOKKEEPING, (), Phase.PARSE, None, access=Access.DICT_KEY
    ),
    AttributeFact(
        "Reference", "ITEM_COUNTS_SITE_KEY", FactKind.BOOKKEEPING, (), Phase.PARSE, None, access=Access.DICT_KEY
    ),
    AttributeFact(
        "Reference", "SCHEMA_PATH_KEY", FactKind.BOOKKEEPING, (), Phase.MODULES, None, access=Access.DICT_KEY
    ),
    AttributeFact("Reference", "BUILD_RECORD_KEY", FactKind.BOOKKEEPING, (), Phase.PARSE, None, access=Access.DICT_KEY),
    AttributeFact(
        "Parser",
        "results",
        FactKind.FACT,
        (Fact.MODELS_EXIST, RenderFact.MODEL_ORDER),
        Phase.SETTLE,
        FreezePoint.B2,
        store_ops=("register_model",),
        access=Access.CONTAINER,
    ),
    AttributeFact("Parser", "_tag_plan", FactKind.FACT, (RenderFact.PLANS,), Phase.CROSS_MODULE, FreezePoint.B1),
    AttributeFact("Parser", "_recursion_facts", FactKind.FACT, (RenderFact.PLANS,), Phase.MODULES, FreezePoint.B1),
    AttributeFact("Parser", "_reuse_groups", FactKind.FACT, (RenderFact.PLANS,), Phase.SETTLE, FreezePoint.B1),
    AttributeFact("Parser", "_naming_plan", FactKind.FACT, (RenderFact.PLANS,), Phase.SETTLE, FreezePoint.B1),
    AttributeFact(
        "ModuleContext",
        "models",
        FactKind.PRESENTATION,
        (RenderFact.MODEL_ORDER, RenderFact.MODULE_MEMBERSHIP),
        Phase.IMPORTS,
        FreezePoint.RENDER,
        store_ops=("set_module_models",),
        access=Access.CONTAINER,
    ),
    AttributeFact(
        "ModuleContext",
        "imports",
        FactKind.PRESENTATION,
        (RenderFact.MODULE_IMPORTS,),
        Phase.RENDER,
        FreezePoint.RENDER,
        access=Access.CONTAINER,
    ),
)


def freeze_point(row: AttributeFact, fact: Fact | RenderFact) -> FreezePoint:
    """Return where ``fact`` of ``row`` is final: a render fact at its point, a fact at B1 or its row's later point."""
    if isinstance(fact, RenderFact):
        return RENDER_FACT_FREEZE_POINTS[fact]
    if row.frozen_at is not None and all(isinstance(item, Fact) for item in row.facts):
        return row.frozen_at
    return FreezePoint.B1


def holds_render_facts(row: AttributeFact) -> bool:
    """Return whether ``row`` holds facts and none of them is a ``Fact`` final at B1: what no decision may read."""
    return bool(row.facts) and not any(
        isinstance(fact, Fact) and freeze_point(row, fact) is FreezePoint.B1 for fact in row.facts
    )


@unique
class MemberUse(Enum):
    """What only the generation store, the member's class or module and the modules its row names may do with it."""

    CALL = "call"
    """Call the method, or hand it on to be called; overrides of it in subclasses are the same member."""
    READ = "read"
    WRITE = "write"
    """Write the attribute; the row names a bookkeeping row of ``ATTRIBUTE_FACTS``."""


@final
@record
class StoreMember:
    """One row of ``STORE_MEMBERS``: a member of a class of the graph, by the package-relative path of its module."""

    path: str
    owner: str
    name: str
    use: MemberUse
    homes: tuple[str, ...] = ()
    """The package-relative paths of the modules besides the store that may use the member.

    A method is also used by its own class, an attribute by the module of its class.
    """


STORE_MEMBERS: tuple[StoreMember, ...] = (
    StoreMember("model/base.py", "DataModel", "_write_additional_properties_type", MemberUse.CALL),
    StoreMember("model/base.py", "DataModel", "adopt_base_options", MemberUse.CALL),
    StoreMember("model/base.py", "DataModel", "assign_forward_reference_fields", MemberUse.CALL),
    StoreMember("model/base.py", "DataModel", "replace_children_in_models", MemberUse.CALL),
    StoreMember("model/base.py", "DataModel", "set_base_class", MemberUse.CALL),
    StoreMember("model/base.py", "DataModel", "set_reference_path", MemberUse.CALL),
    StoreMember("model/base.py", "DataModelFieldBase", "_process_const_as_literal", MemberUse.CALL),
    StoreMember("model/base.py", "DataModelFieldBase", "process_const", MemberUse.CALL),
    StoreMember("model/base.py", "DataModelFieldBase", "replace_data_type", MemberUse.CALL),
    StoreMember("model/base.py", "DataModelFieldBase", "unconstrain_union_members", MemberUse.CALL),
    StoreMember("reference.py", "Reference", "replace_children_references", MemberUse.CALL),
    StoreMember("types.py", "DataType", "_update_type_null", MemberUse.CALL),
    StoreMember("types.py", "DataType", "remove_reference", MemberUse.CALL),
    StoreMember("types.py", "DataType", "replace_reference", MemberUse.CALL),
    StoreMember("types.py", "DataType", "swap_with", MemberUse.CALL),
    StoreMember("reference.py", "Reference", "children", MemberUse.READ, homes=("_graph_links.py",)),
    StoreMember("reference.py", "Reference", "children", MemberUse.WRITE, homes=("_graph_links.py",)),
    StoreMember("reference.py", "Reference", "loaded", MemberUse.WRITE),
    StoreMember("reference.py", "Reference", "source", MemberUse.WRITE),
)
"""The members only the generation store, their own class or module and the modules a row names may use.

``CALL`` rows are the methods that write the graph for the store operations that run them: the hooks those operations
run (``process_const``, ``set_base_class``, ...) and the reference mutators. They may call each other, as they run only
inside a store operation. The ``READ`` and ``WRITE`` rows keep the reverse edges and the loading state of a reference
in its module; the parser asks the generation index for the users of a reference instead.
"""


@final
@record
class DecisionAccessor:
    """One row of ``DECISION_ACCESSORS``: a method or property of a graph class, by the package path of its module."""

    path: str
    owner: str
    name: str
    reads: str
    """What it reads, each final at B1."""


DECISION_ACCESSORS: tuple[DecisionAccessor, ...] = (
    DecisionAccessor(
        "types.py",
        "DataType",
        "admits_none",
        "rendered nullability, which is final at B1 (model/_decisions/nullability.py)",
    ),
    DecisionAccessor(
        "types.py",
        "DataType",
        "_renders_none",
        "the type, the name a type override gives it, the reference, the literals and the container kind of the type"
        " and of its one member, final at B1: the names import binding and forward references give later go to types"
        " that name a model",
    ),
    DecisionAccessor(
        "types.py",
        "DataType",
        "_writes_nothing_of_its_own",
        "the type, the name a type override gives it, the reference and the container kind, final at B1: the names"
        " import binding and forward references give later go to types that name a model",
    ),
    DecisionAccessor(
        "model/base.py",
        "DataModelFieldBase",
        "nullable_without_default",
        "the nullability the parse records without the default (NULLABLE_WITHOUT_DEFAULT_KEY), else NULLABLE, final"
        " at B1",
    ),
    DecisionAccessor(
        "model/base.py",
        "DataModelFieldBase",
        "has_default_factory",
        "whether the field names a default factory (the default_factory entry of extras, DEFAULTS), final at B1",
    ),
    DecisionAccessor(
        "model/base.py",
        "DataModelFieldBase",
        "union_sibling_constraints",
        "the constraints written beside a union (UNION_SIBLING_CONSTRAINTS_KEY)",
    ),
    DecisionAccessor(
        "model/base.py",
        "DataModelFieldBase",
        "written_constraints",
        "the CONSTRAINTS its call writes for its field type, final at B1",
    ),
    DecisionAccessor(
        "model/base.py",
        "DataModel",
        "effective_fields",
        "the fields of the model and of its bases in C3 order (BASES, FIELD_SET), final at B1",
    ),
    DecisionAccessor(
        "model/base.py",
        "DataModel",
        "additional_properties_type",
        "the type of the additional properties that the parser stores for the settings of the class (TypedDict"
        " extra_items), whose nodes are field types, final at B1",
    ),
    DecisionAccessor("model/base.py", "DataModel", "form", "the model kind, final at B1"),
    DecisionAccessor(
        "model/base.py",
        "DataModel",
        "has_length",
        "the sequence interface a root model takes where it is parsed (L1) and the container kind of its root type,"
        " final at B1",
    ),
    DecisionAccessor(
        "model/base.py",
        "DataModel",
        "holds_null",
        "the model kind, and the nullability of its root type or its enum members, final at B1",
    ),
    DecisionAccessor(
        "model/base.py",
        "DataModel",
        "set_item_hash",
        "the model kind, the dataclass decorator, the struct options of its chain and the class and field types of a"
        " frozen Pydantic class, final at B1",
    ),
    DecisionAccessor(
        "model/base.py", "DataModel", "value_base", "the model kind and the base classes of an enum, final at B1"
    ),
)
"""The members of the graph classes a decision may call although they read render facts: each reads only facts final
at B1. ``scripts/build_heki_contracts.py`` makes them, with their overrides, the allowed callees of the decisions'
boundary, so the reads inside count as theirs, not the decision's."""


IMPORTS_MUTATORS: tuple[str, ...] = (
    "append",
    "apply_alias",
    "extract_future",
    "remap_modules",
    "remove",
    "remove_referenced_imports",
    "remove_unused",
)
"""The methods of ``imports.py:Imports`` that change it: the import ledger (``parser/_imports_plan.py``), the
``imports`` module and the ``LEDGER_WRITERS`` alone call them, so the live imports of a module are what its ledger
entries hold."""


@final
@record
class LedgerWriter:
    """One row of ``LEDGER_WRITERS``: a def besides the import ledger that changes an ``Imports``, and why it may."""

    path: str
    name: str
    reason: str


LEDGER_WRITERS: tuple[LedgerWriter, ...] = (
    LedgerWriter(
        "model/pydantic_v2/base_model.py",
        "BaseModel.resolve_module_import_conflicts",
        "the output model's hook gets the live imports inside ModuleImportLedger.external, which records its changes",
    ),
    LedgerWriter(
        "parser/_init_exports.py",
        "build_all_exports_code",
        "the re-exports of a package __init__ module, built and written at once: no module imports",
    ),
    LedgerWriter(
        "parser/base.py",
        "Parser.__generate_forwarder_content",
        "the imports of a forwarder module, built and written at once: no module imports",
    ),
)
"""The defs besides the import ledger that call the ``IMPORTS_MUTATORS``."""
