"""Which attribute of the generation graph holds which fact, and when that fact becomes final.

``ATTRIBUTE_FACTS`` has one row per owner class and attribute, ``__dict__`` key, dict entry or cached property.
``scripts/check_generation_store_usage.py`` reads this module with ``ast`` (it never imports the package), so every
row is a literal ``AttributeFact(...)`` call whose arguments are strings, tuples, ``None`` or members of the enums
below. Later readers: the runtime write guard (which attributes to guard per class and phase), the finality
fingerprints (the ``fact`` rows), the decision phase (what ``FinalFacts`` exposes) and the Fact Finality table of
``docs/architecture.md``.

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
* ``store_ops``: the ``GenerationStore`` operations allowed to write it; every other write is listed in
  ``scripts/generation_store_allowlist.toml`` until a later change routes it. Bookkeeping rows name none: holder
  edges are enforced separately.

The checker enforces the kind rules: a fact row names a ``Fact``, a presentation row names only ``RenderFact``
members, a cache row names no ``Fact``. Three exceptions are allowed: plan rows (``Parser`` snapshots that later
passes read) are fact rows that name only ``RenderFact.PLANS``; rows holding rendered nullability (``is_optional``
and its ``__dict__`` markers) name ``Fact.NULLABLE`` and freeze at B2: render preparation writes rendered nullability
once, and only the null a msgspec field strips from its copied types is written at L7; the schema-validator plan
cache names ``RenderFact.MODULE_CODE``, the code it holds.
"""

from __future__ import annotations

from enum import Enum, unique

from typing_extensions import final

from datamodel_code_generator._records import record

__all__ = [
    "ATTRIBUTE_FACTS",
    "RENDER_FACT_FREEZE_POINTS",
    "Access",
    "AttributeFact",
    "Fact",
    "FactKind",
    "FreezePoint",
    "Phase",
    "RenderFact",
]


@unique
class Phase(Enum):
    """Pipeline phases in execution order.

    ``PARSE`` is L0 and L1, ``SETTLE`` L2, ``STRUCTURE`` L3, ``PLAN`` L4, ``MODULES`` L5, ``CROSS_MODULE`` the L6 passes
    up to the generic base class, ``DECIDE`` the decision phase after B1, ``RENDER_PREPARATION`` the passes that
    prepare rendering, ``IMPORTS`` final names and module imports, ``RENDER`` L7.
    """

    PARSE = 0
    SETTLE = 1
    STRUCTURE = 2
    PLAN = 3
    MODULES = 4
    CROSS_MODULE = 5
    DECIDE = 6
    RENDER_PREPARATION = 7
    IMPORTS = 8
    RENDER = 9


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
        (Fact.FIELD_TYPES,),
        Phase.CROSS_MODULE,
        FreezePoint.B1,
        access=Access.CONTAINER,
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
        "_REFERENCE_NULL_DATA_TYPE_KEY",
        FactKind.PRESENTATION,
        (Fact.NULLABLE,),
        Phase.RENDER_PREPARATION,
        FreezePoint.B2,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataType",
        "_NULL_STRIPPED_DATA_TYPE_KEY",
        FactKind.PRESENTATION,
        (Fact.NULLABLE,),
        Phase.RENDER,
        FreezePoint.B2,
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
        "DataModelFieldBase",
        "name",
        FactKind.FACT,
        (Fact.FIELD_NAMES,),
        Phase.MODULES,
        FreezePoint.B1,
        store_ops=("rename_field",),
    ),
    AttributeFact(
        "DataModelFieldBase",
        "default",
        FactKind.FACT,
        (Fact.DEFAULTS, RenderFact.DEFAULT_EXPRESSION),
        Phase.IMPORTS,
        FreezePoint.B2,
        store_ops=("set_field_default", "apply_literal_default"),
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
        store_ops=("rename_field",),
    ),
    AttributeFact(
        "DataModelFieldBase",
        "validation_aliases",
        FactKind.FACT,
        (Fact.FIELD_ALIASES,),
        Phase.MODULES,
        FreezePoint.B1,
        access=Access.CONTAINER,
    ),
    AttributeFact(
        "DataModelFieldBase", "serialization_alias", FactKind.FACT, (Fact.FIELD_ALIASES,), Phase.MODULES, FreezePoint.B1
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
        store_ops=("apply_literal_default",),
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
        store_ops=("drop_field_discriminator", "set_field_extra", "set_field_extras"),
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
        "DataModelFieldBase", "original_name", FactKind.FACT, (Fact.FIELD_NAMES,), Phase.MODULES, FreezePoint.B1
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
        "DataModelFieldBase", "use_serialization_alias", FactKind.PRESENTATION, (), Phase.MODULES, FreezePoint.B1
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
        "_UNCONSTRAINED_UNION_MEMBERS_KEY",
        FactKind.FACT,
        (Fact.CONSTRAINTS,),
        Phase.MODULES,
        FreezePoint.B1,
        access=Access.DICT_KEY,
    ),
    AttributeFact(
        "DataModelFieldBase",
        "_RAW_SCHEMA_DEFAULT_KEY",
        FactKind.FACT,
        (Fact.DEFAULTS,),
        Phase.MODULES,
        FreezePoint.B1,
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
            "remove_field",
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
        store_ops=("move_model", "rename_model", "update_model_reference"),
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
    AttributeFact(
        "DataModel",
        "_field_name_bindings",
        FactKind.PRESENTATION,
        (RenderFact.UNBOUND_FIELDS,),
        Phase.IMPORTS,
        FreezePoint.RENDER,
    ),
    AttributeFact(
        "DataModel", "discriminator_tag", FactKind.FACT, (Fact.DISCRIMINATOR_TAGS,), Phase.CROSS_MODULE, FreezePoint.B1
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
        store_ops=("move_model", "rename_model", "update_model_reference"),
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
        "Reference", "SCHEMA_OWNER_FRAMES_KEY", FactKind.BOOKKEEPING, (), Phase.PARSE, None, access=Access.DICT_KEY
    ),
    AttributeFact(
        "Reference", "_DERIVED_FROM_KEY", FactKind.BOOKKEEPING, (), Phase.PARSE, None, access=Access.DICT_KEY
    ),
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
    AttributeFact("Parser", "_reuse_union_tags", FactKind.FACT, (RenderFact.PLANS,), Phase.STRUCTURE, FreezePoint.B1),
    AttributeFact("Parser", "_recursion_facts", FactKind.FACT, (RenderFact.PLANS,), Phase.MODULES, FreezePoint.B1),
    AttributeFact(
        "ModuleContext",
        "models",
        FactKind.PRESENTATION,
        (RenderFact.MODEL_ORDER, RenderFact.MODULE_MEMBERSHIP),
        Phase.IMPORTS,
        FreezePoint.RENDER,
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
