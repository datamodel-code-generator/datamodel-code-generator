"""What each pass of ``Parser.parse()`` reads and writes, in today's execution order.

One ``PassDecl`` per pass: the methods and named steps of ``parser/base.py`` that the pipeline tables of ``Parser``
run. ``reads`` and ``writes`` use the facts of ``parser/_facts.py``. The pipeline runs every pass inside
``fact_pass(DECL)`` (``parser/_finality.py``), where ``DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY`` checks the
writes against the declaration and a pass trace records the pass. The writes of ``fact`` rows are checked at run time;
reads and the writes of presentation rows are declared from the code; ``after`` edges order the passes whose facts
conflict (``parser/_pipeline.py:check_order``). ``PASS_DECLS`` lists every declaration in execution order and is the
source of the generated pass table in ``docs/architecture.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import NamedTuple

from typing_extensions import final

from datamodel_code_generator.parser._facts import Fact, Phase, RenderFact

__all__ = [
    "ASSIGN_FORWARD_REFERENCE_FIELDS",
    "COLLECT_LIVE_MODELS",
    "FINALIZE_RENDERED_NULLABILITY",
    "FIX_CONSTRUCTOR_FIELD_ORDERING",
    "FIX_CONSTRUCTOR_FIELD_ORDERING_EARLY",
    "FIX_TAG_OPT_OUT_FIELD_ORDERING",
    "PASS_DECLS",
    "SET_VALIDATE_DEFAULT_ON_FIELDS",
    "UNBIND_HIDDEN_TYPE_FIELDS",
    "UPDATE_TYPE_ALIASES",
    "WARN_REQUIRED_ASSIGNMENTS_AFTER_DEFAULTS",
    "After",
    "PassDecl",
    "PassKind",
    "Scope",
]


@unique
class Scope(Enum):
    """Whether a pass runs once over every model or once per output module over the models of that module."""

    GLOBAL = "global"
    MODULE = "module"


@unique
class PassKind(Enum):
    """What a pass does with the model graph."""

    TRANSFORM = "transform"
    """Changes model facts."""
    PLAN = "plan"
    """Writes a plan or snapshot that later passes read."""
    REPAIR = "repair"
    """Fixes results built from facts that changed later; ``removed_by`` names the change that removes it."""
    CHECK = "check"
    """Reads only: raises errors and emits warnings."""
    SETUP = "setup"
    """Prepares run configuration and per-run state; writes no fact."""
    BOOKKEEPING = "bookkeeping"
    """Keeps caches and local snapshots; writes no fact."""


class After(NamedTuple):
    """An explicit order edge: the pass runs after ``pass_name`` for ``reason``."""

    pass_name: str
    reason: str


@final
@dataclass(frozen=True, slots=True)
class PassDecl:
    """The declaration of one pass: its phase and scope, and the facts it reads and writes."""

    name: str
    phase: Phase
    scope: Scope
    kind: PassKind
    reads: frozenset[Fact | RenderFact] = frozenset()
    writes: frozenset[Fact | RenderFact] = frozenset()
    after: tuple[After, ...] = ()
    removed_by: str | None = None
    target: Phase | None = None
    """The phase the pass moves to once the change named in its summary lands."""
    summary: str = ""
    condition: str = ""
    """When the pipeline runs the pass; empty when it always does."""


def _facts(*members: Fact | RenderFact) -> frozenset[Fact | RenderFact]:
    return frozenset(members)


REFRESH_CUSTOM_TEMPLATE_PATHS = PassDecl(
    "refresh_custom_template_paths",
    Phase.PARSE,
    Scope.GLOBAL,
    PassKind.SETUP,
    summary="Clears the template lookups of the custom template directory.",
    condition="custom_template_dir",
)
SET_TYPED_EXTRA_ANNOTATION_MODE = PassDecl(
    "set_typed_extra_annotation_mode",
    Phase.PARSE,
    Scope.GLOBAL,
    PassKind.SETUP,
    summary="Chooses how typed extra items are annotated.",
)
PARSE_RAW = PassDecl(
    "parse_raw",
    Phase.PARSE,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    writes=_facts(*Fact, RenderFact.MODEL_ORDER, RenderFact.CLASS_NAMES),
    summary="L0 and L1, the parser subclass hook: builds and registers every model.",
)
INDEX_CONST_NARROWED_VALUES = PassDecl(
    "index_const_narrowed_values",
    Phase.PARSE,
    Scope.GLOBAL,
    PassKind.BOOKKEEPING,
    reads=_facts(Fact.FIELD_TYPES),
    summary="Indexes the data types whose value a const narrows, by data type.",
)

SETTLE_YIELDING_NAMES = PassDecl(
    "settle_yielding_names",
    Phase.SETTLE,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(RenderFact.CLASS_NAMES),
    writes=_facts(RenderFact.CLASS_NAMES),
    summary="Gives the names that yield to --model-name-map their final form.",
)
REPORT_PARSE_DIAGNOSTICS = PassDecl(
    "report_parse_diagnostics",
    Phase.SETTLE,
    Scope.GLOBAL,
    PassKind.CHECK,
    writes=_facts(RenderFact.STDERR),
    summary="Reports dangling references and other parse diagnostics (parser hook).",
)
WARN_ABOUT_UNMATCHED_MODEL_NAME_KEYS = PassDecl(
    "warn_about_unmatched_model_name_keys",
    Phase.SETTLE,
    Scope.GLOBAL,
    PassKind.CHECK,
    writes=_facts(RenderFact.STDERR),
    reads=_facts(RenderFact.CLASS_NAMES),
    after=(After("report_parse_diagnostics", "parse diagnostics are reported first"),),
    condition="not a retry that suppresses parse warnings",
)
PREPARE_PARSE_CONFIG = PassDecl(
    "prepare_parse_config",
    Phase.SETTLE,
    Scope.GLOBAL,
    PassKind.SETUP,
)
SORT_DATA_MODELS = PassDecl(
    "sort_data_models",
    Phase.SETTLE,
    Scope.GLOBAL,
    PassKind.PLAN,
    reads=_facts(Fact.BASES, Fact.FIELD_TYPES, RenderFact.MODEL_ORDER),
    writes=_facts(RenderFact.MODEL_ORDER),
    summary="Orders models by dependency and lists those that need forward-reference updates.",
)
RECORD_SOURCE_REFERENCE_PATHS = PassDecl(
    "record_source_reference_paths",
    Phase.SETTLE,
    Scope.GLOBAL,
    PassKind.BOOKKEEPING,
    reads=_facts(RenderFact.MODEL_ORDER),
    condition="collect_model_metadata",
)
SORT_BASE_CLASSES_FOR_MRO = PassDecl(
    "sort_base_classes_for_mro",
    Phase.SETTLE,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.BASES, RenderFact.MODEL_ORDER),
    writes=_facts(Fact.BASES),
    after=(After("sort_data_models", "the model order reads the bases as parsed; the MRO order of bases follows it"),),
)

GROUP_MODELS_BY_MODULE = PassDecl(
    "group_models_by_module",
    Phase.STRUCTURE,
    Scope.GLOBAL,
    PassKind.PLAN,
    reads=_facts(RenderFact.MODEL_ORDER),
    writes=_facts(RenderFact.MODULE_MEMBERSHIP),
)
RESET_COLLAPSED_REUSE_STATE = PassDecl(
    "reset_collapsed_reuse_state",
    Phase.STRUCTURE,
    Scope.GLOBAL,
    PassKind.BOOKKEEPING,
)
SNAPSHOT_UNION_TAGS = PassDecl(
    "snapshot_union_tags",
    Phase.STRUCTURE,
    Scope.GLOBAL,
    PassKind.PLAN,
    reads=_facts(Fact.DISCRIMINATOR_TAGS),
    writes=_facts(RenderFact.PLANS),
    summary="Records the tags of discriminated unions before any model is merged.",
    condition="reuse_model",
)
DELETE_DUPLICATE_MODELS = PassDecl(
    "delete_duplicate_models",
    Phase.STRUCTURE,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(*Fact, RenderFact.FIELD_ORDER, RenderFact.CLASS_NAMES, RenderFact.MODULE_MEMBERSHIP),
    writes=_facts(Fact.MODELS_EXIST, Fact.BASES, Fact.FIELD_TYPES, Fact.NULLABLE, RenderFact.CLASS_NAMES),
    summary="Removes models of the module that render the same as another and redirects their users.",
)
REPLACE_DUPLICATE_NAME_IN_MODULE = PassDecl(
    "replace_duplicate_name_in_module",
    Phase.STRUCTURE,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(RenderFact.CLASS_NAMES, RenderFact.MODULE_IMPORTS),
    writes=_facts(Fact.NULLABLE, RenderFact.CLASS_NAMES),
    after=(After("delete_duplicate_models", "renames the duplicates that deletion keeps"),),
    summary="Makes class names unique within a module; row legacy-l3-import-nullability writes nullability first.",
)
APPLY_FORCED_INVALID_DOTTED_STDOUT_REPAIR = PassDecl(
    "apply_forced_invalid_dotted_stdout_repair",
    Phase.STRUCTURE,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(RenderFact.MODULE_MEMBERSHIP, RenderFact.CLASS_NAMES),
    writes=_facts(RenderFact.MODULE_MEMBERSHIP, RenderFact.CLASS_NAMES),
    after=(After("replace_duplicate_name_in_module", "flattens invalid roots under the names deduplication gave"),),
    summary="Flattens the modules of dotted stdout output that a retry found invalid, then regroups.",
)
REUSE_MODEL_TREE_SCOPE = PassDecl(
    "reuse_model_tree_scope",
    Phase.STRUCTURE,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(*Fact, RenderFact.FIELD_ORDER, RenderFact.MODULE_MEMBERSHIP, RenderFact.PLANS),
    writes=_facts(
        Fact.MODELS_EXIST, Fact.BASES, Fact.FIELD_SET, Fact.FIELD_TYPES, Fact.NULLABLE, RenderFact.MODULE_MEMBERSHIP
    ),
    after=(After("apply_forced_invalid_dotted_stdout_repair", "shares the duplicates of the repaired module layout"),),
    summary="Moves models duplicated across modules into a shared module.",
    condition="reuse_model with the tree scope",
)
RESOLVE_CIRCULAR_IMPORTS = PassDecl(
    "resolve_circular_imports",
    Phase.STRUCTURE,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_TYPES, Fact.BASES, RenderFact.MODULE_MEMBERSHIP),
    writes=_facts(RenderFact.MODULE_MEMBERSHIP, RenderFact.CLASS_NAMES),
    after=(
        After(
            "reuse_model_tree_scope",
            "breaks the cycles of the modules tree-scope reuse leaves, its shared module included",
        ),
    ),
    summary="Merges modules that import each other into _internal modules.",
)
CONFIGURE_CODE_FORMATTER = PassDecl(
    "configure_code_formatter",
    Phase.STRUCTURE,
    Scope.GLOBAL,
    PassKind.SETUP,
    reads=_facts(RenderFact.MODULE_MEMBERSHIP),
    condition="format_",
)
DETECT_STANDARD_GENERATION_TEMPLATES = PassDecl(
    "detect_standard_generation_templates",
    Phase.STRUCTURE,
    Scope.GLOBAL,
    PassKind.SETUP,
)

RESET_PER_RUN_STATE = PassDecl(
    "reset_per_run_state",
    Phase.PLAN,
    Scope.GLOBAL,
    PassKind.BOOKKEEPING,
)
FLATTEN_CONFLICTING_BASE_LAYOUTS = PassDecl(
    "flatten_conflicting_base_layouts",
    Phase.PLAN,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.BASES, Fact.FIELD_SET, Fact.DISCRIMINATOR_TAGS, Fact.FIELD_TYPES),
    writes=_facts(Fact.BASES, Fact.FIELD_SET, RenderFact.FIELD_ORDER),
    summary="Copies base fields into msgspec Structs whose generated bases have conflicting layouts.",
    condition="msgspec",
)
UNCONSTRAIN_UNION_MEMBERS = PassDecl(
    "unconstrain_union_members",
    Phase.PLAN,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_TYPES),
    writes=_facts(Fact.FIELD_TYPES, Fact.CONSTRAINTS),
    after=(After("flatten_conflicting_base_layouts", "layouts are flattened with the union members as parsed"),),
    condition="msgspec",
)
SET_CIRCULAR_ROOT_MODEL_PATHS = PassDecl(
    "set_circular_root_model_paths",
    Phase.PLAN,
    Scope.GLOBAL,
    PassKind.PLAN,
    reads=_facts(Fact.FIELD_TYPES, RenderFact.MODULE_MEMBERSHIP),
    writes=_facts(RenderFact.PLANS),
    condition="collapse_root_models",
)
RECORD_EMITTING_MODULES = PassDecl(
    "record_emitting_modules",
    Phase.PLAN,
    Scope.GLOBAL,
    PassKind.BOOKKEEPING,
    reads=_facts(RenderFact.MODULE_MEMBERSHIP),
    summary="Records the module that writes each model, for the names --collapse-root-models-name-strategy gives.",
)

RECORD_DECIMAL_ALIAS_MODELS = PassDecl(
    "record_decimal_alias_models",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.BOOKKEEPING,
    reads=_facts(Fact.FIELD_NAMES),
    condition="a field of the module is named Decimal",
)
ALIAS_SHADOWED_IMPORTS = PassDecl(
    "alias_shadowed_imports",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_NAMES, RenderFact.MODULE_IMPORTS),
    writes=_facts(RenderFact.IMPORT_ALIASES, RenderFact.DEFAULT_EXPRESSION),
    summary="Aliases imports that field names shadow, in types and in runtime expressions of defaults.",
)
OVERRIDE_REQUIRED_FIELD = PassDecl(
    "override_required_field",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.BASES, Fact.FIELD_SET, Fact.REQUIRED, Fact.DEFAULTS),
    writes=_facts(Fact.FIELD_SET, Fact.REQUIRED, Fact.DEFAULTS),
    summary="Replaces inherited placeholders by copies of the inherited fields.",
)
REPLACE_UNIQUE_LIST_TO_SET = PassDecl(
    "replace_unique_list_to_set",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.CONSTRAINTS, Fact.DEFAULTS, Fact.FIELD_TYPES),
    writes=_facts(Fact.CONTAINERS, Fact.FIELD_TYPES, Fact.DEFAULTS),
    after=(After("override_required_field", "converts the defaults that inherited required fields bring"),),
)
CHANGE_FROM_IMPORT = PassDecl(
    "change_from_import",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_TYPES, RenderFact.MODULE_MEMBERSHIP, RenderFact.CLASS_NAMES),
    writes=_facts(Fact.NULLABLE, RenderFact.MODULE_IMPORTS, RenderFact.IMPORT_ALIASES),
    after=(After("alias_shadowed_imports", "imports referenced models after shadowed imports took their aliases"),),
)
EXTRACT_INHERITED_ENUM = PassDecl(
    "extract_inherited_enum",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.BASES, Fact.FIELD_SET, Fact.MODEL_KIND, RenderFact.MODULE_IMPORTS),
    writes=_facts(Fact.MODEL_KIND, Fact.MODELS_EXIST),
)
MARK_UNUSED_MODELS_START = PassDecl(
    "mark_unused_models_start",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.BOOKKEEPING,
)
SET_REFERENCE_DEFAULT_VALUE_TO_FIELD = PassDecl(
    "set_reference_default_value_to_field",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.DEFAULTS, Fact.FIELD_TYPES),
    writes=_facts(Fact.DEFAULTS),
    after=(After("replace_unique_list_to_set", "turns defaults into references after unique lists became sets"),),
)
REUSE_MODEL = PassDecl(
    "reuse_model",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(*Fact, RenderFact.FIELD_ORDER, RenderFact.PLANS),
    writes=_facts(Fact.MODELS_EXIST, Fact.BASES, Fact.FIELD_SET, Fact.FIELD_TYPES, Fact.NULLABLE),
    after=(
        After("set_reference_default_value_to_field", "compares models with their final defaults"),
        After("extract_inherited_enum", "compares models after inherited enums became models of their own"),
    ),
    summary="Replaces duplicates of the module by empty subclasses, or removes them and redirects their users.",
)
COLLAPSE_ROOT_MODELS = PassDecl(
    "collapse_root_models",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_TYPES, Fact.CONSTRAINTS, Fact.MODEL_KIND, RenderFact.PLANS),
    writes=_facts(
        Fact.FIELD_TYPES,
        Fact.CONSTRAINTS,
        Fact.NULLABLE,
        Fact.REQUIRED,
        Fact.DEFAULTS,
        Fact.MODELS_EXIST,
        Fact.DISCRIMINATOR_TAGS,
        Fact.METADATA,
        RenderFact.CLASS_NAMES,
        RenderFact.MODULE_IMPORTS,
    ),
    after=(After("reuse_model", "inlines the root models that reuse keeps"),),
    summary="Inlines the root models the module references.",
    condition="collapse_root_models",
)
SET_DEFAULT_ENUM_MEMBER = PassDecl(
    "set_default_enum_member",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.DEFAULTS, Fact.FIELD_TYPES),
    writes=_facts(Fact.DEFAULTS),
    after=(After("collapse_root_models", "writes enum members as defaults of the types collapse inlined"),),
)
SORT_MODELS = PassDecl(
    "sort_models",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_TYPES, Fact.BASES, RenderFact.MODEL_ORDER),
    writes=_facts(RenderFact.MODEL_ORDER),
)
CHANGE_FIELD_NAME = PassDecl(
    "change_field_name",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_NAMES, RenderFact.CLASS_NAMES),
    writes=_facts(Fact.FIELD_NAMES, Fact.FIELD_ALIASES, RenderFact.CLASS_NAMES),
    after=(
        After("collapse_root_models", "renames fields against the class names collapse settled"),
        After("record_decimal_alias_models", "the Decimal check reads the field names as parsed"),
    ),
)
RESTORE_INHERITED_FIELD_NAMES_FOR_UNIQUE_ALIASES = PassDecl(
    "restore_inherited_field_names_for_unique_aliases",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_NAMES, Fact.FIELD_ALIASES, Fact.BASES),
    writes=_facts(Fact.FIELD_NAMES, Fact.FIELD_ALIASES),
    after=(After("change_field_name", "restores the inherited names that renaming changed"),),
)
APPLY_DISCRIMINATOR_TYPE = PassDecl(
    "apply_discriminator_type",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.DISCRIMINATOR_TAGS, Fact.FIELD_SET, Fact.FIELD_TYPES),
    writes=_facts(
        Fact.FIELD_SET,
        Fact.FIELD_TYPES,
        Fact.REQUIRED,
        Fact.NULLABLE,
        Fact.DEFAULTS,
        Fact.CONSTRAINTS,
        Fact.DISCRIMINATOR_TAGS,
        RenderFact.MODULE_IMPORTS,
    ),
    after=(
        After("sort_models", "models are ordered by their types before tags rewrite the variant types"),
        After("set_default_enum_member", "enum defaults read the types before tags rewrite them"),
    ),
    summary="Writes the tag of each discriminated union into its variants, in any module.",
)
TYPE_CONST_NARROWED_VALUES = PassDecl(
    "type_const_narrowed_values",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_TYPES),
    writes=_facts(Fact.FIELD_TYPES, RenderFact.MODULE_IMPORTS),
    after=(After("apply_discriminator_type", "narrows the types that tags wrote"),),
    summary="Types each value by the const that narrows the enum it names.",
)
SET_ONE_LITERAL_ON_DEFAULT = PassDecl(
    "set_one_literal_on_default",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.REQUIRED, Fact.FIELD_TYPES),
    writes=_facts(Fact.DEFAULTS, Fact.REQUIRED, Fact.NULLABLE),
    condition="use_one_literal_as_default",
)
REFRESH_FIELD_CONFIG = PassDecl(
    "refresh_field_config",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.REQUIRED, Fact.DEFAULTS, Fact.NULLABLE, Fact.FIELD_ALIASES, Fact.CONSTRAINTS),
    writes=_facts(RenderFact.BACKEND_CONFIG),
    summary="Lets each model refresh its backend field configuration (model hook).",
)
RENAME_FIELDS_HIDING_TYPES = PassDecl(
    "rename_fields_hiding_types",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(
        Fact.FIELD_NAMES,
        Fact.BASES,
        Fact.FIELD_SET,
        Fact.REQUIRED,
        Fact.DEFAULTS,
        Fact.NULLABLE,
        RenderFact.CLASS_NAMES,
        RenderFact.KEYWORD_ONLY,
        RenderFact.FORCED_ASSIGNMENT,
    ),
    writes=_facts(Fact.FIELD_NAMES, Fact.FIELD_ALIASES, RenderFact.CLASS_NAMES),
    after=(After("refresh_field_config", "the field config is refreshed with the aliases before renaming"),),
)
REMOVE_OVERRIDDEN_MODELS = PassDecl(
    "remove_overridden_models",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.TYPE_OVERRIDES, RenderFact.CLASS_NAMES),
    writes=_facts(Fact.MODELS_EXIST),
    summary="Keeps the models that a model-level --type-overrides replaces out of the module.",
)
APPLY_FIELD_TYPE_OVERRIDES = PassDecl(
    "apply_field_type_overrides",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.TYPE_OVERRIDES, Fact.FIELD_NAMES),
    writes=_facts(Fact.FIELD_TYPES, Fact.CONSTRAINTS, Fact.TYPE_OVERRIDES, RenderFact.MODULE_IMPORTS),
    after=(
        After("remove_overridden_models", "overrides apply to the models kept after model-level overrides (#4958)"),
    ),
)
TYPE_DICT_KEYS_BY_ROOT_TYPES = PassDecl(
    "type_dict_keys_by_root_types",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_TYPES, Fact.CONSTRAINTS, Fact.MODEL_KIND, Fact.METADATA, RenderFact.MODULE_MEMBERSHIP),
    writes=_facts(Fact.FIELD_TYPES, RenderFact.MODULE_IMPORTS, RenderFact.IMPORT_ALIASES, RenderFact.BACKEND_CONFIG),
    after=(After("apply_field_type_overrides", "types dict keys after field types are overridden"),),
    summary="Types each dict key that names a root model whose instances cannot be dict keys by its root type.",
)
FIX_CONSTRUCTOR_FIELD_ORDERING_EARLY = PassDecl(
    "fix_constructor_field_ordering_early",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(
        Fact.FIELD_SET,
        Fact.FIELD_NAMES,
        Fact.FIELD_TYPES,
        Fact.REQUIRED,
        Fact.NULLABLE,
        Fact.DEFAULTS,
        RenderFact.FIELD_ORDER,
        RenderFact.KEYWORD_ONLY,
        RenderFact.FORCED_ASSIGNMENT,
        RenderFact.BACKEND_CONFIG,
    ),
    writes=_facts(Fact.REQUIRED, RenderFact.FIELD_ORDER, RenderFact.KEYWORD_ONLY, RenderFact.FORCED_ASSIGNMENT),
    summary="Orders constructor fields while the modules are processed, before every field has its final name.",
    condition="without --order-constructor-fields-by final-names",
)
SNAPSHOT_MODULE_UNUSED_MODELS = PassDecl(
    "snapshot_module_unused_models",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.BOOKKEEPING,
)
APPLY_BASE_CLASS_TYPE_OVERRIDES = PassDecl(
    "apply_base_class_type_overrides",
    Phase.MODULES,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.TYPE_OVERRIDES),
    writes=_facts(Fact.BASES, Fact.TYPE_OVERRIDES, RenderFact.MODULE_IMPORTS),
    after=(
        After(
            "type_dict_keys_by_root_types", "both add module imports; base overrides come after field types are final"
        ),
    ),
)

WRITE_RULE_ALIASES_AS_CLASSES = PassDecl(
    "write_rule_aliases_as_classes",
    Phase.MODULES,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.MODELS_EXIST, Fact.MODEL_KIND, RenderFact.MODULE_MEMBERSHIP, RenderFact.CLASS_NAMES),
    writes=_facts(Fact.MODEL_KIND, RenderFact.MODULE_IMPORTS, RenderFact.IMPORT_ALIASES),
    summary=(
        "After every module is processed: writes each type alias that holds schema validator rules and that "
        "`collapse_root_models` left written as the RootModel class, under its model key, and imports the models of "
        "other modules that its rules name."
    ),
    condition="--generate-schema-validators",
    after=(
        After(
            "type_dict_keys_by_root_types",
            "the aliases written as classes are object maps, which no dict key names",
        ),
        After(
            "apply_base_class_type_overrides",
            "imports what the rules of the new classes name once every module has written its imports",
        ),
    ),
)
NAME_INNER_MODELS_IN_RULES = PassDecl(
    "name_inner_models_in_rules",
    Phase.MODULES,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.MODELS_EXIST, RenderFact.MODULE_MEMBERSHIP, RenderFact.CLASS_NAMES),
    writes=_facts(Fact.FIELD_TYPES, RenderFact.MODULE_IMPORTS, RenderFact.IMPORT_ALIASES),
    summary=(
        "After every module is processed: makes the schema validator rules that name a root model the `parent` "
        "strategy of `collapse_root_models` replaced name its inner model, and imports it in their modules."
    ),
    condition="--generate-schema-validators --collapse-root-models-name-strategy parent",
    after=(
        After(
            "write_rule_aliases_as_classes",
            "the aliases written as classes import what their rules name, which this pass then redirects",
        ),
        After("fix_constructor_field_ordering_early", "the types of rules are no constructor fields"),
    ),
)
APPLY_BASE_STAND_IN_IMPORTS = PassDecl(
    "apply_base_stand_in_imports",
    Phase.MODULES,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.BASES),
    writes=_facts(Fact.BASES, RenderFact.MODULE_IMPORTS),
    summary=(
        "After every module is processed: replaces the bases that name a stand-in (a schema that a base names and the "
        "output types by an import) by that import, so that the passes of every module read the stand-in as a "
        "generated base whatever the module order."
    ),
    condition="a base names a stand-in",
    after=(
        After(
            "apply_base_class_type_overrides",
            "every module reads the stand-ins as the bases of models of other modules before imports replace them",
        ),
        After("name_inner_models_in_rules", "both add module imports once every module is processed"),
    ),
)
SETTLE_DISCRIMINATED_UNIONS = PassDecl(
    "settle_discriminated_unions",
    Phase.MODULES,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(
        Fact.DEFAULTS, Fact.DISCRIMINATOR_TAGS, Fact.FIELD_SET, Fact.FIELD_TYPES, Fact.NULLABLE, Fact.REQUIRED
    ),
    writes=_facts(Fact.DISCRIMINATOR_TAGS),
    summary=(
        "After every module is processed: writes no `discriminator=` for the union of a lone `$ref` variant that has "
        "no tag, which a union of any module may still write into it, nor for a lone variant that is no model (a "
        "scalar, `Any` or None, as a collapsed alias or an inline schema leaves it), nor for a union that takes a "
        "root model, or a type alias, with a default as a variant, also where `--collapse-root-models` inlines the "
        "union into the items of a list."
    ),
    condition="the output fields write `discriminator=`",
)
TAG_FIELDS_OF_TAGGED_BASES = PassDecl(
    "tag_fields_of_tagged_bases",
    Phase.CROSS_MODULE,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(
        Fact.BASES, Fact.DISCRIMINATOR_TAGS, Fact.FIELD_SET, RenderFact.MODULE_MEMBERSHIP, RenderFact.MODULE_IMPORTS
    ),
    writes=_facts(
        Fact.BASES,
        Fact.DISCRIMINATOR_TAGS,
        Fact.FIELD_SET,
        Fact.FIELD_TYPES,
        RenderFact.MODULE_IMPORTS,
        RenderFact.IMPORT_ALIASES,
    ),
    summary=(
        "Tags the structs that declare the tag field of a tagged base, or opts them out of the tag, and opts out "
        "the structs whose tag field a base keeps as a field, typing their tag field by its const again; a struct "
        "that reuse made an empty subclass of that base declares the fields of its base instead and keeps its tag, "
        "importing the models of other modules that those fields name."
    ),
    condition="msgspec",
)
WRITE_ENUM_MEMBER_TAGS_AS_VALUES = PassDecl(
    "write_enum_member_tags_as_values",
    Phase.CROSS_MODULE,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_TYPES, Fact.DISCRIMINATOR_TAGS),
    writes=_facts(Fact.FIELD_TYPES),
    after=(
        After(
            "tag_fields_of_tagged_bases", "the tags read a member literal as no literal until the structs are tagged"
        ),
    ),
    summary="Writes the values of the enum members that tag a variant, where the output's Literal holds no member.",
    condition="an output whose Literal holds no enum member (msgspec)",
)
WARN_ENUMS_ADMITTING_OTHER_VALUES = PassDecl(
    "warn_enums_admitting_other_values",
    Phase.CROSS_MODULE,
    Scope.GLOBAL,
    PassKind.CHECK,
    writes=_facts(RenderFact.STDERR),
    reads=_facts(Fact.FIELD_TYPES, Fact.DISCRIMINATOR_TAGS),
)
IMPORT_REMOVED_REUSE_CANONICALS = PassDecl(
    "import_removed_reuse_canonicals",
    Phase.CROSS_MODULE,
    Scope.GLOBAL,
    PassKind.REPAIR,
    reads=_facts(Fact.MODELS_EXIST, RenderFact.MODULE_MEMBERSHIP, RenderFact.MODULE_IMPORTS),
    writes=_facts(RenderFact.MODULE_IMPORTS, RenderFact.IMPORT_ALIASES),
    after=(
        After(
            "tag_fields_of_tagged_bases",
            "repairs the imports of every module once the fields reused structs copy from their bases import theirs",
        ),
    ),
    removed_by="RP15b",
)
IMPORT_RENAMED_TYPES = PassDecl(
    "import_renamed_types",
    Phase.CROSS_MODULE,
    Scope.GLOBAL,
    PassKind.REPAIR,
    reads=_facts(RenderFact.CLASS_NAMES, RenderFact.MODULE_IMPORTS),
    writes=_facts(RenderFact.MODULE_IMPORTS, RenderFact.IMPORT_ALIASES),
    after=(
        After(
            "tag_fields_of_tagged_bases",
            "imports renamed types once the fields reused structs copy from their bases import theirs",
        ),
        After(
            "import_removed_reuse_canonicals",
            "imports renamed types next to the canonicals of removed reuse duplicates",
        ),
    ),
    removed_by="RP15b",
)
IMPORT_ENUM_MEMBER_LITERAL_CLASSES = PassDecl(
    "import_enum_member_literal_classes",
    Phase.CROSS_MODULE,
    Scope.GLOBAL,
    PassKind.REPAIR,
    reads=_facts(Fact.FIELD_TYPES, Fact.DEFAULTS, RenderFact.MODULE_IMPORTS),
    writes=_facts(Fact.FIELD_TYPES, RenderFact.MODULE_IMPORTS, RenderFact.IMPORT_ALIASES),
    after=(
        After("import_renamed_types", "imports literal classes next to the renamed types"),
        After(
            "warn_enums_admitting_other_values", "the warning reads the enum types before literal classes replace them"
        ),
    ),
    removed_by="RP15b",
)
APPLY_GENERIC_BASE_CLASS = PassDecl(
    "apply_generic_base_class",
    Phase.CROSS_MODULE,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.BASES),
    writes=_facts(Fact.BASES, Fact.MODELS_EXIST),
    summary="Inserts the generated base model and rewrites the base classes; B1 follows.",
)

ASSIGN_FORWARD_REFERENCE_FIELDS = PassDecl(
    "assign_forward_reference_fields",
    Phase.RENDER_PREPARATION,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_TYPES, Fact.DEFAULTS, RenderFact.MODEL_ORDER, RenderFact.FIELD_ORDER),
    writes=_facts(RenderFact.ASSIGNMENT_FORM, RenderFact.FIELD_ORDER),
    condition="use_annotated with deferred annotations",
)
FIX_CONSTRUCTOR_FIELD_ORDERING = PassDecl(
    "fix_constructor_field_ordering",
    Phase.RENDER_PREPARATION,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(
        Fact.FIELD_SET,
        Fact.FIELD_NAMES,
        Fact.FIELD_TYPES,
        Fact.REQUIRED,
        Fact.NULLABLE,
        Fact.DEFAULTS,
        RenderFact.FIELD_ORDER,
        RenderFact.KEYWORD_ONLY,
        RenderFact.FORCED_ASSIGNMENT,
        RenderFact.ASSIGNMENT_FORM,
        RenderFact.BACKEND_CONFIG,
        RenderFact.CLASS_NAMES,
        RenderFact.MODULE_IMPORTS,
    ),
    writes=_facts(
        Fact.REQUIRED,
        Fact.FIELD_SET,
        RenderFact.FIELD_ORDER,
        RenderFact.KEYWORD_ONLY,
        RenderFact.FORCED_ASSIGNMENT,
        RenderFact.MODULE_IMPORTS,
        RenderFact.IMPORT_ALIASES,
    ),
    after=(
        After(
            "assign_forward_reference_fields",
            "a field written by assignment is a dataclass default, which ordering reads from the bases of every module",
        ),
    ),
    summary=(
        "Orders constructor fields so that fields without a default come first, reading the fields of bases, each "
        "model after its bases; an inherited field that bases put after a default gets a keyword-only copy."
    ),
)
UNBIND_HIDDEN_TYPE_FIELDS = PassDecl(
    "unbind_hidden_type_fields",
    Phase.RENDER_PREPARATION,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_NAMES, RenderFact.CLASS_NAMES),
    writes=_facts(RenderFact.UNBOUND_FIELDS),
    summary="Leaves fields unbound that hide a type their class names (model hook).",
)
UPDATE_TYPE_ALIASES = PassDecl(
    "update_type_aliases",
    Phase.RENDER_PREPARATION,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(
        Fact.BASES,
        Fact.FIELD_SET,
        Fact.FIELD_NAMES,
        Fact.FIELD_TYPES,
        RenderFact.MODEL_ORDER,
        RenderFact.CLASS_NAMES,
        RenderFact.UNBOUND_FIELDS,
    ),
    writes=_facts(Fact.FIELD_TYPES, Fact.NULLABLE, RenderFact.QUOTING, RenderFact.TYPE_ALIAS_BODY),
    after=(
        After("fix_constructor_field_ordering", "ordering reads the field types before aliases quote them"),
        After("assign_forward_reference_fields", "the forward-reference assignment reads the field types unquoted"),
    ),
    summary="Quotes forward references and defers recursive containers.",
    removed_by="RP16b",
)
COLLECT_LIVE_MODELS = PassDecl(
    "collect_live_models",
    Phase.RENDER_PREPARATION,
    Scope.MODULE,
    PassKind.BOOKKEEPING,
)
SET_VALIDATE_DEFAULT_ON_FIELDS = PassDecl(
    "set_validate_default_on_fields",
    Phase.RENDER_PREPARATION,
    Scope.MODULE,
    PassKind.TRANSFORM,
    reads=_facts(Fact.DEFAULTS, Fact.FIELD_TYPES, Fact.MODELS_EXIST),
    writes=_facts(
        Fact.DEFAULTS,
        Fact.CONSTRAINTS,
        RenderFact.DEFAULT_VALIDATION,
        RenderFact.DEFAULT_EXPRESSION,
        RenderFact.IMPORT_ALIASES,
    ),
    after=(
        After(
            "assign_forward_reference_fields",
            "the forward-reference assignment reads which fields have a default, which deserializing it keeps",
        ),
    ),
)
WARN_REQUIRED_ASSIGNMENTS_AFTER_DEFAULTS = PassDecl(
    "warn_required_assignments_after_defaults",
    Phase.RENDER_PREPARATION,
    Scope.MODULE,
    PassKind.CHECK,
    writes=_facts(RenderFact.STDERR),
    reads=_facts(
        Fact.REQUIRED,
        Fact.DEFAULTS,
        RenderFact.FIELD_ORDER,
        RenderFact.FORCED_ASSIGNMENT,
        RenderFact.KEYWORD_ONLY,
    ),
)
FIX_TAG_OPT_OUT_FIELD_ORDERING = PassDecl(
    "fix_tag_opt_out_field_ordering",
    Phase.RENDER_PREPARATION,
    Scope.GLOBAL,
    PassKind.REPAIR,
    reads=_facts(Fact.BASES, Fact.FIELD_SET, Fact.REQUIRED, Fact.DEFAULTS, RenderFact.KEYWORD_ONLY),
    writes=_facts(RenderFact.KEYWORD_ONLY),
    after=(
        After(
            "warn_required_assignments_after_defaults",
            "the pydantic warning and this msgspec repair never read or write the same models",
        ),
    ),
    removed_by="G08",
    condition="msgspec",
)
FINALIZE_RENDERED_NULLABILITY = PassDecl(
    "finalize_rendered_nullability",
    Phase.RENDER_PREPARATION,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_TYPES, Fact.NULLABLE, RenderFact.QUOTING),
    writes=_facts(Fact.NULLABLE, Fact.FIELD_TYPES),
    summary=(
        "Writes the None each type hint renders, once, after the module bodies set union operators and quotes;"
        " rendering and import collection write none. Rendering the members' hints writes the import of the Any an"
        " empty union renders as."
    ),
)
COLLECT_ALL_MODELS = PassDecl(
    "collect_all_models",
    Phase.RENDER_PREPARATION,
    Scope.GLOBAL,
    PassKind.BOOKKEEPING,
)
PREPARE_TYPE_ALIAS_FIELDS = PassDecl(
    "prepare_type_alias_fields",
    Phase.RENDER_PREPARATION,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_TYPES, Fact.MODELS_EXIST, Fact.MODEL_KIND),
    writes=_facts(Fact.METADATA, RenderFact.TYPE_ALIAS_BODY, RenderFact.MODULE_IMPORTS),
    summary="Rewrites the bodies of type aliases (alias bodies are not part of FIELD_TYPES) and compiles patterns.",
)
FINALIZE_TYPE_ALIAS_USERS = PassDecl(
    "finalize_type_alias_users",
    Phase.RENDER_PREPARATION,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.MODEL_KIND, Fact.FIELD_TYPES),
    writes=_facts(RenderFact.BACKEND_CONFIG),
    summary="Lets the backend configure the models that use type aliases (model hook).",
)
MARK_SET_ITEM_MODELS_HASHABLE = PassDecl(
    "mark_set_item_models_hashable",
    Phase.RENDER_PREPARATION,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.CONTAINERS, Fact.FIELD_TYPES),
    writes=_facts(RenderFact.SET_ITEM_HASH),
)
FINALIZE_STRUCTURED_IMPORTS = PassDecl(
    "finalize_structured_imports",
    Phase.RENDER_PREPARATION,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.DEFAULTS, Fact.CONSTRAINTS, Fact.FIELD_NAMES, RenderFact.MODULE_IMPORTS),
    writes=_facts(RenderFact.DEFAULT_EXPRESSION, RenderFact.IMPORT_ALIASES, RenderFact.MODULE_IMPORTS),
    after=(After("prepare_type_alias_fields", "aliases imports after alias bodies dropped their con* imports"),),
    summary="Aliases imports that runtime default expressions shadow (Avro also aligns shared defaults).",
    target=Phase.IMPORTS,
)
FINALIZE_SEQUENCE_INTERFACE = PassDecl(
    "finalize_sequence_interface",
    Phase.RENDER_PREPARATION,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.MODEL_KIND, Fact.FIELD_TYPES, RenderFact.CLASS_NAMES),
    writes=_facts(RenderFact.BACKEND_CONFIG),
    after=(After("finalize_type_alias_users", "both write backend config; the sequence interface is decided last"),),
    condition="use_root_model_sequence_interface",
)
DECIDE_NESTED_MODEL_DEFAULT_FACTORIES = PassDecl(
    "decide_nested_model_default_factories",
    Phase.RENDER_PREPARATION,
    Scope.GLOBAL,
    PassKind.PLAN,
    reads=_facts(Fact.DEFAULTS, Fact.FIELD_TYPES, Fact.MODELS_EXIST, RenderFact.MODEL_ORDER),
    condition="use_default_factory_for_optional_nested_models",
    summary=(
        "Decides, from the final model order and reference cycles, the default factories of optional nested models"
        " that the render plans read (row legacy.L-NF)."
    ),
)
ORDER_CONSTRUCTOR_FIELDS_BY_DECIDED_NESTED_FACTORIES = PassDecl(
    "order_constructor_fields_by_decided_nested_factories",
    Phase.RENDER_PREPARATION,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(
        Fact.FIELD_SET,
        Fact.FIELD_NAMES,
        Fact.FIELD_TYPES,
        Fact.REQUIRED,
        Fact.NULLABLE,
        Fact.DEFAULTS,
        RenderFact.FIELD_ORDER,
        RenderFact.KEYWORD_ONLY,
        RenderFact.FORCED_ASSIGNMENT,
        RenderFact.BACKEND_CONFIG,
    ),
    writes=_facts(Fact.REQUIRED, RenderFact.FIELD_ORDER, RenderFact.KEYWORD_ONLY, RenderFact.FORCED_ASSIGNMENT),
    condition="use_default_factory_for_optional_nested_models and order_constructor_fields_by_written_defaults",
    summary=(
        "Orders constructor fields again by the decided nested factories, which the orderings before leave out"
        " under --order-constructor-fields-by written-defaults."
    ),
)
FREEZE_FIELD_RENDER_PLANS = PassDecl(
    "freeze_field_render_plans",
    Phase.RENDER_PREPARATION,
    Scope.GLOBAL,
    PassKind.PLAN,
    reads=_facts(
        *Fact,
        RenderFact.FIELD_ORDER,
        RenderFact.KEYWORD_ONLY,
        RenderFact.FORCED_ASSIGNMENT,
        RenderFact.DEFAULT_VALIDATION,
        RenderFact.DEFAULT_EXPRESSION,
        RenderFact.BACKEND_CONFIG,
    ),
    summary="Freezes one render plan per field of the built-in output models, which rendering reads; B2 follows.",
)

SNAPSHOT_MODEL_IMPORTS = PassDecl(
    "snapshot_model_imports",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.PLAN,
    reads=_facts(
        *Fact,
        RenderFact.IMPORT_ALIASES,
        RenderFact.QUOTING,
        RenderFact.TYPE_ALIAS_BODY,
        RenderFact.DEFAULT_EXPRESSION,
        RenderFact.BACKEND_CONFIG,
    ),
    summary="Collects the imports of every model once; B2 precedes it.",
)
APPEND_MODEL_IMPORTS = PassDecl(
    "append_model_imports",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    writes=_facts(RenderFact.MODULE_IMPORTS),
)
REMOVE_UNUSED_MODELS = PassDecl(
    "remove_unused_models",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.MODELS_EXIST),
    writes=_facts(Fact.MODELS_EXIST, RenderFact.MODULE_IMPORTS, RenderFact.MODULE_MEMBERSHIP),
    after=(
        After("append_model_imports", "removes the imports of unused models after every model added its imports"),
        After("snapshot_model_imports", "the snapshot holds the imports of unused models, which leave with them"),
    ),
    summary="Removes the models that collapse left unused from their modules.",
)
VALIDATE_EXPLICIT_FIELD_ALIASES = PassDecl(
    "validate_explicit_field_aliases",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.CHECK,
    reads=_facts(Fact.FIELD_ALIASES, Fact.FIELD_NAMES),
    condition="aliases",
)
PREPARE_SCHEMA_RUNTIME_VALIDATION_MODULE_CODE = PassDecl(
    "prepare_schema_runtime_validation_module_code",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_NAMES, RenderFact.CLASS_NAMES),
    writes=_facts(RenderFact.MODULE_CODE, RenderFact.MODULE_IMPORTS),
    after=(After("remove_unused_models", "plans validator helpers for the kept models"),),
    condition="generate_schema_validators",
)
SYNC_SCHEMA_RUNTIME_VALIDATION_MODULE_IMPORTS = PassDecl(
    "sync_schema_runtime_validation_module_imports",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(RenderFact.MODULE_CODE),
    writes=_facts(RenderFact.MODULE_IMPORTS),
    after=(After("prepare_schema_runtime_validation_module_code", "adds the imports of the planned helpers"),),
    condition="generate_schema_validators",
)
MERGE_RUNTIME_EXPRESSION_IMPORTS = PassDecl(
    "merge_runtime_expression_imports",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(RenderFact.DEFAULT_EXPRESSION),
    writes=_facts(RenderFact.MODULE_IMPORTS),
    after=(
        After(
            "sync_schema_runtime_validation_module_imports",
            "both add module imports; runtime expressions come after validator helpers",
        ),
    ),
    condition="runtime expressions",
)
REMOVE_UNUSED_IMPORTS = PassDecl(
    "remove_unused_imports",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.REPAIR,
    reads=_facts(Fact.FIELD_TYPES, RenderFact.MODULE_IMPORTS, RenderFact.CLASS_NAMES, RenderFact.MODULE_CODE),
    writes=_facts(RenderFact.MODULE_IMPORTS),
    removed_by="RP15b",
    after=(After("merge_runtime_expression_imports", "removes unused imports once every producer added its imports"),),
    summary="Drops the imports no rendered name uses; becomes a check.",
)
RESOLVE_MODULE_IMPORT_CONFLICTS = PassDecl(
    "resolve_module_import_conflicts",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(Fact.FIELD_NAMES, RenderFact.MODULE_IMPORTS),
    writes=_facts(RenderFact.MODULE_IMPORTS, RenderFact.IMPORT_ALIASES),
    after=(After("remove_unused_imports", "resolves the conflicts of the imports that are used"),),
    summary="Lets the backend alias imports that clash with names of the module (model hook).",
)
BIND_MODULE_FIELD_NAMES = PassDecl(
    "bind_module_field_names",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.REPAIR,
    reads=_facts(Fact.FIELD_NAMES, RenderFact.MODULE_IMPORTS),
    writes=_facts(RenderFact.IMPORT_ALIASES, RenderFact.UNBOUND_FIELDS),
    after=(After("resolve_module_import_conflicts", "binds field names against the final import names"),),
    removed_by="RP15b",
)
CHANGE_IMPORTED_MODEL_NAME = PassDecl(
    "change_imported_model_name",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(RenderFact.MODULE_IMPORTS, RenderFact.CLASS_NAMES),
    writes=_facts(RenderFact.CLASS_NAMES),
    summary="Renames models that collide with imports, the last class-name change.",
)
NAME_FINAL_CLASSES_OF_ADDITIONAL_PROPERTIES = PassDecl(
    "name_final_classes_of_additional_properties",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(RenderFact.CLASS_NAMES, RenderFact.IMPORT_ALIASES),
    writes=_facts(RenderFact.BACKEND_CONFIG),
    condition="additional properties reference classes",
    summary="Renders backend settings that name the classes of additional properties (TypedDict extra_items) "
    "from their final names, a class of another module through its import (model hook).",
)
REFRESH_SEQUENCE_INTERFACE_AFTER_RENAMES = PassDecl(
    "refresh_sequence_interface_after_renames",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.REPAIR,
    reads=_facts(RenderFact.CLASS_NAMES),
    writes=_facts(RenderFact.BACKEND_CONFIG),
    removed_by="RP15b",
    after=(
        After(
            "name_final_classes_of_additional_properties",
            "both write backend config; the sequence interface is decided last",
        ),
    ),
    condition="use_root_model_sequence_interface and a model was renamed",
)
REPLAN_SCHEMA_VALIDATORS_AFTER_RENAMES = PassDecl(
    "replan_schema_validators_after_renames",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.REPAIR,
    reads=_facts(RenderFact.CLASS_NAMES),
    writes=_facts(RenderFact.MODULE_CODE, RenderFact.MODULE_IMPORTS),
    removed_by="RP15b",
    after=(
        After("change_imported_model_name", "plans the validator helpers again for the renamed models"),
        After("bind_module_field_names", "binding reads the module imports before the helpers are planned again"),
    ),
    condition="generate_schema_validators and a model was renamed",
)
DECIDE_CONFIG_ITEMS = PassDecl(
    "decide_config_items",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.PLAN,
    reads=_facts(Fact.CONSTRAINTS, RenderFact.BACKEND_CONFIG, RenderFact.MODULE_CODE),
    writes=_facts(RenderFact.BACKEND_CONFIG, RenderFact.MODULE_CODE),
    after=(
        After(
            "name_final_classes_of_additional_properties", "both write backend config; config items are decided last"
        ),
        After("refresh_sequence_interface_after_renames", "both write backend config; config items are decided last"),
        After(
            "replan_schema_validators_after_renames",
            "reads the neutral extra config that planning the validator helpers sets on root models",
        ),
    ),
    summary="Decides the config items each model's class writes (model hook).",
)
REMAP_IMPORT_OVERRIDES = PassDecl(
    "remap_import_overrides",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(RenderFact.MODULE_IMPORTS),
    writes=_facts(RenderFact.MODULE_IMPORTS),
    after=(After("replan_schema_validators_after_renames", "import overrides move the final imports"),),
    condition="import_overrides",
)
REJECT_UNAVAILABLE_IMPORTS = PassDecl(
    "reject_unavailable_imports",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.CHECK,
    reads=_facts(RenderFact.MODULE_IMPORTS),
    condition="a configured type is unavailable on the target Python",
)

RECORD_GENERATED_CLASS_NAMES = PassDecl(
    "record_generated_class_names",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.BOOKKEEPING,
    reads=_facts(RenderFact.CLASS_NAMES),
    condition="class names are collected",
)
RECORD_MODEL_PLACEMENT = PassDecl(
    "record_model_placement",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.BOOKKEEPING,
    reads=_facts(RenderFact.CLASS_NAMES, RenderFact.MODULE_MEMBERSHIP),
    condition="typed or untyped nullable maps",
)
CHECK_MODEL_INHERITANCE = PassDecl(
    "check_model_inheritance",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.CHECK,
    reads=_facts(Fact.BASES, Fact.FIELD_SET, RenderFact.MODULE_MEMBERSHIP),
)
REJECT_ALIAS_CYCLES_WITHOUT_SCHEMA = PassDecl(
    "reject_alias_cycles_without_schema",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.CHECK,
    reads=_facts(
        Fact.MODELS_EXIST,
        Fact.MODEL_KIND,
        Fact.FIELD_TYPES,
        Fact.CONSTRAINTS,
        Fact.NULLABLE,
        RenderFact.TYPE_ALIAS_BODY,
        RenderFact.MODULE_MEMBERSHIP,
    ),
    condition="output that Pydantic builds or msgspec decodes",
)
WARN_ABOUT_DROPPED_CONSTRAINTS = PassDecl(
    "warn_about_dropped_constraints",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.CHECK,
    writes=_facts(RenderFact.STDERR),
    reads=_facts(Fact.CONSTRAINTS),
)
WARN_ABOUT_DECIMAL_DEFAULTS = PassDecl(
    "warn_about_decimal_defaults",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.CHECK,
    writes=_facts(RenderFact.STDERR),
    reads=_facts(Fact.DEFAULTS),
    after=(After("warn_about_dropped_constraints", "warnings keep their order"),),
)
WARN_ABOUT_VARIANT_DISCRIMINATOR_TAGS = PassDecl(
    "warn_about_variant_discriminator_tags",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.CHECK,
    writes=_facts(RenderFact.STDERR),
    reads=_facts(Fact.DISCRIMINATOR_TAGS),
    after=(After("warn_about_decimal_defaults", "warnings keep their order"),),
)
WARN_ABOUT_MSGSPEC_DECODING = PassDecl(
    "warn_about_msgspec_decoding",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.CHECK,
    writes=_facts(RenderFact.STDERR),
    reads=_facts(Fact.FIELD_TYPES),
    after=(After("warn_about_variant_discriminator_tags", "warnings keep their order"),),
)
SET_NESTED_MODEL_DEFAULT_FACTORY_METADATA = PassDecl(
    "set_nested_model_default_factory_metadata",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    writes=_facts(RenderFact.DEFAULT_EXPRESSION),
    condition="use_default_factory_for_optional_nested_models",
    summary=(
        "Publishes the nested-factory decision on the models, for the models that render without frozen plans"
        " (GraphQL unions, custom output models; row legacy.L-NF)."
    ),
)
RECORD_PYDANTIC_FLOOR = PassDecl(
    "record_pydantic_floor",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.CHECK,
    reads=_facts(
        Fact.MODEL_KIND,
        Fact.FIELD_TYPES,
        Fact.CONSTRAINTS,
        Fact.FIELD_ALIASES,
        RenderFact.TYPE_ALIAS_BODY,
        RenderFact.BACKEND_CONFIG,
    ),
    condition="an explicit target Pydantic version",
)
ADD_ROOT_INIT_RESULT = PassDecl(
    "add_root_init_result",
    Phase.IMPORTS,
    Scope.GLOBAL,
    PassKind.BOOKKEEPING,
    reads=_facts(RenderFact.MODULE_MEMBERSHIP),
)

RENDER_MODULES = PassDecl(
    "render_modules",
    Phase.RENDER,
    Scope.GLOBAL,
    PassKind.TRANSFORM,
    reads=_facts(*Fact, *RenderFact),
    writes=_facts(Fact.FIELD_TYPES),
    summary="L7: renders every module; writes the import of the Any an empty union renders as.",
)

PASS_DECLS: tuple[PassDecl, ...] = tuple(value for value in tuple(globals().values()) if isinstance(value, PassDecl))
"""Every declaration, in execution order."""
