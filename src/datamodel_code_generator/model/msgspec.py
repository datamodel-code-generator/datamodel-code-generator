"""msgspec.Struct model generator.

Generates Python models using msgspec.Struct for high-performance serialization.
"""

from __future__ import annotations

import keyword
from ast import literal_eval
from fractions import Fraction
from functools import wraps
from math import ceil, floor, isfinite
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Final, NamedTuple, Optional, TypeVar, cast

from typing_extensions import Sentinel, final, override

from datamodel_code_generator import Error
from datamodel_code_generator import _constraint_applicability as _applicability
from datamodel_code_generator._records import record
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot, graph_answer, walk_graph
from datamodel_code_generator._target_unicode import is_identifier
from datamodel_code_generator.imports import IMPORT_UNION, Import
from datamodel_code_generator.model import DataModel, DataModelFieldBase, _rebuild_model_with_datamodel_namespace
from datamodel_code_generator.model._constraints import PatternConstraints as _Constraints
from datamodel_code_generator.model._decisions.assignment import (
    MsgspecWritten,
    struct_assigned,
    struct_construction_assigned,
    written_data,
)
from datamodel_code_generator.model._decisions.literal_default import decide_literal_default
from datamodel_code_generator.model._decisions.tag_value import (
    struct_discriminator_tag,
    struct_tag_options,
    struct_tag_refused,
)
from datamodel_code_generator.model._render_plan import (
    CallArguments,
    CallPlan,
    FieldDefault,
    FieldRenderPlan,
    Kwarg,
    Omitted,
    RuleId,
    Why,
    field_call_text,
    written_argument_names,
    written_text,
)
from datamodel_code_generator.model.base import (
    UNDEFINED,
    BaseClassDataType,
    BaseLayoutView,
    _apply_literal_default,
    _default_factory_type_name,
    _keeps_stripped_place,
    _model_key_of_model,
    _ModelKeys,
    _nested_model_factory,
    _NestedModelFactory,
    _ValueFactory,
    linearize_data_models,
)
from datamodel_code_generator.model.field_name import MsgspecFieldNameResolver
from datamodel_code_generator.model.imports import (
    IMPORT_MSGSPEC_CONVERT,
    IMPORT_MSGSPEC_FIELD,
    IMPORT_MSGSPEC_META,
    IMPORT_MSGSPEC_STRUCT,
    IMPORT_MSGSPEC_UNSET,
    IMPORT_MSGSPEC_UNSETTYPE,
)
from datamodel_code_generator.model.type_alias import TypeAliasBase
from datamodel_code_generator.model.types import DataTypeManager as _DataTypeManager
from datamodel_code_generator.python_literal import (
    _normalize_string,
    represent_python_value,
    represent_untrusted_python_value,
)
from datamodel_code_generator.reference import FieldNameResolver, ModelType
from datamodel_code_generator.types import (
    NONE,
    DataType,
    _create_context_data_type,
    _is_value_literal,
    _qualified_type_path,
    build_union,
    chain_as_tuple,
    merge_normalized_constraint,
    normalize_integer_constraint,
    union_members,
)


class _UNSET:
    @override
    def __str__(self) -> str:
        return "UNSET"

    __repr__ = __str__


UNSET = _UNSET()


_ANNOTATED_CONSTRAINTS_CONTEXT: object = Sentinel("_ANNOTATED_CONSTRAINTS_CONTEXT")
_UNCONSTRAINED_UNION_MEMBERS_KEY = "_unconstrained_union_members"


def _integer_bounds(constraints: tuple[tuple[str, Any], ...]) -> tuple[int | None, int | None]:
    """Return the least and the greatest integer that numeric constraints admit, None where they set no bound."""
    lowers: list[int] = []
    uppers: list[int] = []
    for name, value in constraints:
        match name:
            case "ge":
                lowers.append(ceil(value))
            case "gt":
                lowers.append(floor(value) + 1)
            case "le":
                uppers.append(floor(value))
            case "lt":
                uppers.append(ceil(value) - 1)
    return max(lowers, default=None), min(uppers, default=None)


def _admits_integers_of(constraints: tuple[tuple[str, Any], ...], other: tuple[tuple[str, Any], ...]) -> bool:
    """Return whether numeric constraints admit every integer that the other numeric constraints admit.

    The integer multiples of a divisor p/q in lowest terms are the multiples of p.
    """
    lower, upper = _integer_bounds(constraints)
    other_lower, other_upper = _integer_bounds(other)
    if other_lower is not None and other_upper is not None and other_lower > other_upper:
        return True
    if lower is not None and (other_lower is None or other_lower < lower):
        return False
    if upper is not None and (other_upper is None or other_upper > upper):
        return False
    other_steps = [Fraction(str(value)).numerator for name, value in other if name == "multiple_of"]
    return all(
        step == 1 or any(not other_step % step for other_step in other_steps)
        for step in (Fraction(str(value)).numerator for name, value in constraints if name == "multiple_of")
    )


def _is_non_finite(value: float) -> bool:
    """Return whether a number has no finite float: an infinity, NaN, or an int beyond the float range.

    msgspec.Meta takes finite numbers only: an int beyond the float range fails when the module is imported.
    """
    try:
        return not isfinite(value)
    except OverflowError:
        return True


def _is_exact_float_divisor(value: Any) -> bool:
    """Return whether a float represents a divisor exactly as a dyadic rational, so msgspec checks multiples exactly.

    msgspec checks `multiple_of` on a float by its floating-point remainder, which rejects multiples of a divisor
    such as 0.01 that a float only approximates.
    """
    divisor = Fraction(str(value))
    return not divisor.denominator & (divisor.denominator - 1) and Fraction(float(value)) == divisor


_PLAIN_ANNOTATION = Why(RuleId("D11.annotation.plain"))
_ANNOTATED = Why(RuleId("D11.annotation.annotated"), option="--use-annotated")
_CLASS_VAR = Why(RuleId("D7.tag.class-var"), keyword="discriminator")
"""A tag field of a tagged Struct: msgspec writes the tag itself, and the built-in template skips the field."""
_FIELD_CALL = Why(RuleId("D11.msgspec.field-call"))
"""The field writes ``field(...)``: a name, a default factory, or a default beside them needs one."""
_META_CALL = Why(RuleId("D11.msgspec.meta"), option="--use-annotated")
_FIELD_ARGUMENT = RuleId("D11.msgspec.field-argument")
_META_ARGUMENT = RuleId("D11.msgspec.meta-argument")
_ALIAS = Why(RuleId("D9.alias.explicit"), keyword="alias")
_DEFAULT = Why(RuleId("D4.default.value"), keyword="default")
_BARE_DEFAULT = Why(RuleId("D4.default.bare"), keyword="default")
"""The field writes its default alone after the annotation."""
_UNSET_DEFAULT = Why(RuleId("D4.default.msgspec-unset"), keyword="required")
"""An optional field without a default writes ``UNSET``, or ``None`` when it is nullable: a Struct always writes one."""
_NONE_DEFAULT = Why(RuleId("D4.default.msgspec-none"), keyword="nullable")
_REQUIRED = Why(RuleId("D4.default.required"), keyword="required")
_STRUCT_CONVERSION = Why(RuleId("D4.default.msgspec-convert"), keyword="default")
"""A default holding Structs is written as a factory that converts it with ``msgspec.convert``."""
_MUTABLE_FACTORY = Why(RuleId("D4.default.mutable-factory"), keyword="default")
_NESTED_MODEL_FACTORY = Why(
    RuleId("D4.default.nested-model-factory"), option="--use-default-factory-for-optional-nested-models"
)
_CONSTRAINT = RuleId("D2.msgspec.constraint")
_NON_FINITE = RuleId("D2.msgspec.non-finite-bound")
_MULTIPLE_OF_ONE = RuleId("D2.msgspec.integer-multiple-of-one")
_STRONGER_BOUND = RuleId("D2.msgspec.stronger-bound")
_ITEMS_AS_LENGTH = RuleId("D2.msgspec.items-as-length")
_UNTAKEN = RuleId("D2.untaken-by-type")
_LITERAL_DECIDED = RuleId("D2.msgspec.literal-decided")
"""The values of the field's ``Literal`` decide the constraint (``_applicability.literal_check``)."""
_INEXACT = RuleId("D2.msgspec.inexact-float-divisor")
_INT64_LIMIT = RuleId("D2.msgspec.int64-limit")
"""msgspec takes the number constraints of an int only within the 64-bit range (``_INT_META_LIMITS``)."""
_INT_META_LIMITS: Final = MappingProxyType({
    "ge": (-(2**63), 2**63 - 1),
    "le": (-(2**63), 2**63 - 1),
    "gt": (-(2**63), 2**63 - 2),
    "lt": (-(2**63) + 1, 2**63 - 1),
    "multiple_of": (-(2**63), 2**63 - 1),
})
"""The values msgspec.Meta takes on an int: 64-bit ones, a ``gt`` or ``lt`` whose ``ge`` or ``le`` is one too."""
_NONE_VALUE = Why(RuleId("D11.msgspec.none-unset"))
_NEEDS_ANNOTATED = Why(RuleId("D11.msgspec.meta-needs-annotated"), option="--use-annotated")


@final
@record(eq=False)
class _StructConversion:
    """The default factory that converts a default holding Structs: ``lambda: convert({...},  type=Model)``.

    ``of_list`` converts to a list of ``target``; ``container`` is the list type whose hint names ``target``. Its text
    names the models as the module finally names them.
    """

    method: str
    default: object
    target: DataType
    container: DataType | None = None
    of_list: bool = False

    @override
    def __str__(self) -> str:
        """Return the factory as the field's call writes it."""
        name = _default_factory_type_name(self.target)
        if self.of_list:
            type_text = f"list[{name}]"
        elif self.container is not None:
            type_text = self.container.type_hint.replace(self.target.alias or name, name)
        else:
            type_text = name
        return f"lambda: {self.method}({represent_python_value(self.default)},  type={type_text})"


def _beyond_int_limits(keyword: str, value: object) -> bool:
    """Return whether msgspec.Meta refuses a number constraint of an int: one beyond the 64-bit range."""
    return (
        isinstance(value, int)
        and (limits := _INT_META_LIMITS.get(keyword)) is not None
        and not limits[0] <= value <= limits[1]
    )


def _meta_call_text(call: CallPlan) -> str:
    """Write a ``Meta(...)`` call: its arguments sorted by their text."""
    return f"Meta({', '.join(sorted(f'{k.name}={represent_python_value(k.value)}' for k in call.kwargs))})"


class _MetaAnnotatedType(DataType):
    """The ``Annotated`` type of a msgspec field: a copy of the field's type, its one member, with its ``Meta()`` call.

    It writes its text when it is read, so a field's render plan holds it with the names inside it written as they are
    then; import collection reads the typing names of that text.
    """

    meta: Any = None

    @property
    @override
    def type_hint(self) -> str:
        """Write ``Annotated`` over the hint of the copied type, ``None`` when it writes nothing, and the ``Meta()``."""
        return f"Annotated[{self.data_types[0].type_hint or NONE}, {_meta_call_text(self.meta)}]"

    @property
    @override
    def _annotation_texts(self) -> tuple[str | None, ...]:
        return (self.type_hint,)


class _UnionLeaf(NamedTuple):
    """A union member msgspec decodes as one type: the alias members spread to reach it, its type and constraints."""

    spread: tuple[tuple[DataType, DataModelField], ...]
    member: DataType
    kind: str
    base: DataType
    constraints: tuple[tuple[str, Any], ...]

    @property
    def keywords(self) -> frozenset[str]:
        """Return the Meta keywords of the member."""
        return frozenset(name for name, _ in self.constraints)


if TYPE_CHECKING:
    from collections import defaultdict
    from collections.abc import Callable, Collection, Iterator, Mapping
    from pathlib import Path

    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.model._decisions.assignment import AssignmentRow
    from datamodel_code_generator.model._render_plan import OmitReason
    from datamodel_code_generator.reference import Reference


def _is_instance_field(field: DataModelFieldBase) -> bool:
    """Return whether a field takes a slot of its struct, unlike the class variable a msgspec tag field becomes."""
    return not field.is_class_var


def _same_model(model: DataModel) -> DataModel:
    """Return the model itself as the model whose layout it has."""
    return model


def _is_never_opaque(model: DataModel) -> bool:  # noqa: ARG001
    """Return that only a configured base, which the layout check finds itself, hides the slots of a model."""
    return False


_FINAL_LAYOUT_VIEW = BaseLayoutView(_is_instance_field, _same_model, _is_never_opaque)


def _user_struct_options(model: DataModel) -> dict[str, Any]:
    """Return the struct options that template data gives a model, which builtin templates read only from a dict."""
    return options if isinstance(options := model.extra_template_data.get("base_class_kwargs"), dict) else {}


def has_field_assignment(field: DataModelFieldBase) -> bool:
    """Return whether a msgspec field renders with a default assignment.

    A Struct writes a default for every optional field, ``UNSET`` or ``None``, with ``--strip-default-none`` too.
    """
    return struct_assigned(required=field.required and not field.use_default_with_required)


def _construction_order_key(field: DataModelFieldBase) -> bool:
    """Return whether a Struct is built with the field among the fields with a default.

    Under ``--strip-default-none`` a field whose ``None`` default is stripped keeps its schema place among the fields
    without one, unless ``--order-constructor-fields-by written-defaults`` is given.
    """
    return struct_construction_assigned(
        required=field.required and not field.use_default_with_required,
        keeps_stripped_place=_keeps_stripped_place(field),
    )


def get_field_default_info(field: DataModelFieldBase) -> tuple[bool, bool]:
    """Return msgspec constructor-default semantics."""
    return field._get_constructor_default_info()  # noqa: SLF001  # output-owned field policy hook


DataModelFieldBaseT = TypeVar("DataModelFieldBaseT", bound=DataModelFieldBase)


def import_extender(cls: type[DataModelFieldBaseT]) -> type[DataModelFieldBaseT]:
    """Extend imports property with msgspec-specific imports."""
    original_getter: Callable[[DataModelFieldBaseT], tuple[Import, ...]] = cast("Any", cls.imports.fget)

    @wraps(original_getter)
    def new_imports(self: DataModelFieldBaseT) -> tuple[Import, ...]:
        if self.is_class_var:
            return ()
        extra_imports: tuple[Import, ...] = ()
        if isinstance(self, DataModelField):
            extra_imports = tuple(import_ for import_, _ in self._render_plan().symbol_imports)
        imports = original_getter(self)
        return chain_as_tuple(imports, extra_imports)

    cls.imports = property(new_imports)  # ty: ignore[invalid-assignment]
    return cls


class Struct(DataModel):
    """DataModel implementation for msgspec.Struct."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "msgspec.jinja2"
    _FREEZES_FIELD_RENDER_PLANS: ClassVar[bool] = True
    RESOLVES_STRING_ANNOTATIONS_FROM_MODULE: ClassVar[bool] = True
    WRITES_QUOTED_NAMES_IN_TYPING_GENERICS: ClassVar[bool] = True
    BASE_CLASS: ClassVar[str] = "msgspec.Struct"
    BASE_CLASS_NAME: ClassVar[str] = "Struct"
    BASE_CLASS_ALIAS: ClassVar[str] = "_Struct"
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = ()
    FIELD_ASSIGNMENT_CHECKER = staticmethod(has_field_assignment)
    FIELD_CONSTRUCTION_ORDER_KEY = staticmethod(_construction_order_key)
    FIELD_DEFAULT_CLASSIFIER = staticmethod(get_field_default_info)
    _ASSIGNMENT_ROW: ClassVar[AssignmentRow | None] = "msgspec"
    FIELD_NAME_MODEL_TYPE: ClassVar[ModelType] = ModelType.MSGSPEC
    FIELD_NAME_RESOLVER_CLASS: ClassVar[type[FieldNameResolver]] = MsgspecFieldNameResolver
    SUPPORTS_DISCRIMINATOR: ClassVar[bool] = True
    SUPPORTS_INHERITED_DISCRIMINATOR_ENUM: ClassVar[bool] = True
    SUPPORTS_KW_ONLY: ClassVar[bool] = True
    REQUIRES_MODEL_LEVEL_KW_ONLY: ClassVar[bool] = True
    SUPPORTS_BOOLEAN_LITERAL: ClassVar[bool] = False
    SUPPORTS_NULL_ENUM_MEMBER: ClassVar[bool] = False
    _DECODES_MIXED_ENUM_VALUES: ClassVar[bool] = False
    _DECODES_VALUE_UNIONS: ClassVar[bool] = False
    _LITERAL_HOLDS_ENUM_MEMBERS: ClassVar[bool] = False
    _LAYERS_REPEATED_CONSTRAINTS: ClassVar[bool] = False
    _CHECKS_CONSTRAINTS: ClassVar[bool] = True
    _READ_BY_MSGSPEC: ClassVar[bool] = True
    REQUIRES_TAGGED_UNION_DISCRIMINATOR: ClassVar[bool] = True
    REQUIRES_UNIQUE_FIELD_ALIASES: ClassVar[bool] = True
    FLATTENS_CONFLICTING_BASE_LAYOUTS: ClassVar[bool] = True
    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = True
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = _ANNOTATED_CONSTRAINTS_CONTEXT
    REQUIRES_EXPLICIT_DEFERRED_ANNOTATIONS_FOR_FORWARD_REFS: ClassVar[bool] = True
    CONFIG_MAPPING: ClassVar[dict[tuple[str, Any], tuple[str, Any] | None]] = {
        ("allow_mutation", False): ("frozen", True),
        ("extra_fields", "forbid"): ("forbid_unknown_fields", True),
        ("extra_fields", "allow"): None,
        ("extra_fields", "ignore"): None,
        ("allow_extra_fields", True): None,
        ("allow_population_by_field_name", True): None,
        ("use_attribute_docstrings", True): None,
    }

    def __init__(  # noqa: PLR0913
        self,
        *,
        reference: Reference,
        fields: list[DataModelFieldBase],
        decorators: list[str] | None = None,
        base_classes: list[Reference] | None = None,
        custom_base_class: str | list[str] | None = None,
        custom_template_dir: Path | None = None,
        extra_template_data: defaultdict[str, dict[str, Any]] | None = None,
        methods: list[str] | None = None,
        path: Path | None = None,
        description: str | None = None,
        default: Any = UNDEFINED,
        nullable: bool = False,
        keyword_only: bool = False,
        treat_dot_as_module: bool | None = None,
    ) -> None:
        """Initialize msgspec Struct with fields sorted by field assignment requirement."""
        super().__init__(
            reference=reference,
            fields=self.order_fields_for_construction(fields),
            decorators=decorators,
            base_classes=base_classes,
            custom_base_class=custom_base_class,
            custom_template_dir=custom_template_dir,
            extra_template_data=extra_template_data,
            methods=methods,
            path=path,
            description=description,
            default=default,
            nullable=nullable,
            keyword_only=keyword_only,
            treat_dot_as_module=treat_dot_as_module,
        )
        self.extra_template_data.setdefault("base_class_kwargs", {})
        self._set_internal_template_data("base_class_kwargs", {})
        if self.keyword_only:
            self.add_base_class_kwarg("kw_only", "True")

    @classmethod
    @override
    def render_module_code(cls, models: list[DataModel]) -> str:
        """Reject incompatible generated Struct layouts before rendering a module."""
        for model in models:
            if isinstance(model, cls) and len(model.base_classes) > 1:
                cls._validate_base_layouts(model)
        return ""

    @staticmethod
    def _has_opaque_layout_base(model: DataModel) -> bool:
        """Identify effective configured bases whose inherited slots are unknown."""
        return (
            (custom_base_class := model.custom_base_class) is not None
            and custom_base_class not in (Struct.BASE_CLASS, [Struct.BASE_CLASS])
            and any(not base.reference for base in model.base_classes)
        )

    @staticmethod
    def _layout_parents(model: DataModel, view: BaseLayoutView) -> list[DataModel]:
        """Return the generated Struct bases of a model as the view of the layouts sees them."""
        return [
            view.layout_model(parent.reference.source)
            for parent in model.base_classes
            if parent.reference and isinstance(parent.reference.source, Struct)
        ]

    @staticmethod
    def _has_unknown_layout(model: DataModel, view: BaseLayoutView) -> bool:
        """Return whether a configured base or the view hides the slots a model has."""
        return Struct._has_opaque_layout_base(model) or view.is_opaque(model)

    @staticmethod
    def _find_layout_conflict(
        bases: list[DataModel], view: BaseLayoutView = _FINAL_LAYOUT_VIEW
    ) -> tuple[DataModel, DataModel] | None:
        """Find independent slot owners with linear storage for compatible graphs.

        Field introduction depths distinguish inherited slots from extensions.
        Encounter ranks retain the original diagnostic order independently of
        the parent-first traversal needed to establish each model's layout.
        The view tells the fields that take slots and the model whose layout a model has. Models are told apart by
        model key.
        """
        pending = [(view.layout_model(base), False) for base in bases]
        keys = _ModelKeys("struct layout")
        visited: dict[ModelKey, int] = {}
        owner_depths: dict[ModelKey, int] = {}
        field_depths: dict[str | None, int] = {}
        owners: list[DataModel] = []
        while pending:
            base, complete = pending.pop()
            if not complete and keys.of(base) in visited:
                continue
            parents = Struct._layout_parents(base, view)
            if not complete:
                visited[keys.of(base)] = len(visited)
                if Struct._has_unknown_layout(base, view):
                    return None
                pending.append((base, True))
                pending.extend((parent, False) for parent in reversed(parents))
                continue
            parent_depth = max((owner_depths.get(keys.of(parent), 0) for parent in parents), default=0)
            if new_names := [
                field.name
                for field in base.fields
                if view.is_slot(field) and field_depths.get(field.name, len(owners) + 1) > parent_depth
            ]:
                if parent_depth != len(owners):
                    other = min(owners[parent_depth:], key=lambda owner: visited[keys.of(owner)])
                    return Struct._select_layout_conflict(
                        (other, base), pending, owners, owner_depths, visited, view, keys
                    )
                owners.append(base)
                for name in new_names:
                    field_depths[name] = len(owners)
                parent_depth = len(owners)
            owner_depths[keys.of(base)] = parent_depth
        return None

    @staticmethod
    def _select_layout_conflict(  # noqa: PLR0913, PLR0917
        conflict: tuple[DataModel, DataModel],
        pending: list[tuple[DataModel, bool]],
        owners: list[DataModel],
        owner_depths: dict[ModelKey, int],
        encounter_ranks: dict[ModelKey, int],
        view: BaseLayoutView,
        keys: _ModelKeys,
    ) -> tuple[DataModel, DataModel] | None:
        """Prefer an earlier active slot owner without repeating the layout proof.

        The compact witness establishes incompatibility. An active descendant
        may occur earlier in the emitted bases' preorder, or inherit an opaque
        layout that prevents diagnosing the generated class at all.
        """
        for base, complete in reversed(pending):
            if not complete:
                continue
            parents = Struct._layout_parents(base, view)
            ancestor_keys: set[ModelKey] = set()
            inherited_names: set[str | None] = set()
            parent_depth = 0
            while parents:
                parent = parents.pop()
                if (parent_key := keys.of(parent)) in ancestor_keys:
                    continue
                if Struct._has_unknown_layout(parent, view):
                    return None
                ancestor_keys.add(parent_key)
                parent_depth = max(parent_depth, owner_depths.get(parent_key, 0))
                inherited_names.update(field.name for field in parent.fields if view.is_slot(field))
                parents.extend(Struct._layout_parents(parent, view))
            if parent_depth < len(owners) and any(
                field.name not in inherited_names and view.is_slot(field) for field in base.fields
            ):
                other = min(owners[parent_depth:], key=lambda owner: encounter_ranks[keys.of(owner)])
                conflict = other, base
        return conflict

    def _generated_layout_bases(self) -> list[DataModel]:
        """Return the generated Struct bases in the order the layout of the bases is diagnosed in."""
        return [
            base.reference.source
            for base in reversed(self.base_classes)
            if base.reference and isinstance(base.reference.source, Struct)
        ]

    @override
    def flatten_conflicting_base_layouts(self, view: BaseLayoutView, flatten: Callable[[DataModel], None]) -> None:
        """Flatten the struct where msgspec cannot combine the layouts of its generated bases, as the view sees them."""
        if not self._uses_custom_root_template and self._find_layout_conflict(self._generated_layout_bases(), view):
            flatten(self)

    @override
    def flatten_base_layouts(
        self, adopt_field: Callable[[DataModelFieldBase, bool], DataModelFieldBase]
    ) -> list[DataModelFieldBase]:
        """Return the fields in the order the constructor needs, and make the struct options of the bases its own.

        msgspec makes a struct take each option from the first class in its method resolution order that sets it.
        """
        inherited_options: dict[str, Any] = {}
        for model in reversed(linearize_data_models([self])[1:]):
            inherited_options.update(_user_struct_options(model))
        options = _user_struct_options(self)
        for name, value in inherited_options.items():
            options.setdefault(name, value)
        return self.order_fields_for_construction(super().flatten_base_layouts(adopt_field))

    def _validate_base_layouts(self) -> None:
        """Allow empty mixins and shared layouts, but reject independent slot extensions."""
        if self._uses_custom_root_template:
            return
        if conflict := self._find_layout_conflict(self._generated_layout_bases()):
            other, base = conflict
            msg = (
                f"msgspec.Struct model {self.class_name!r} has incompatible layouts from generated bases "
                f"{other.class_name!r} and {base.class_name!r}."
            )
            raise Error(msg)

    def add_base_class_kwarg(self, name: str, value: str) -> None:
        """Add keyword argument to base class constructor."""
        if isinstance((base_class_kwargs := self.extra_template_data.get("base_class_kwargs")), dict):
            base_class_kwargs[name] = value
        self._internal_template_data["base_class_kwargs"][name] = value

    @override
    def _builtin_template_data(self) -> dict[str, Any]:
        """Serialize user msgspec options while retaining generator-owned syntax."""
        template_data = super()._builtin_template_data()
        raw_base_class_kwargs = self.extra_template_data.get("base_class_kwargs")
        base_class_kwargs: dict[str, str] = {}
        if isinstance(raw_base_class_kwargs, dict):
            for key, value in raw_base_class_kwargs.items():
                if not isinstance(key, str):
                    continue
                normalized_key = _normalize_string(key)
                if not normalized_key.isidentifier() or keyword.iskeyword(normalized_key):
                    continue
                base_class_kwargs[normalized_key] = represent_untrusted_python_value(value)
        base_class_kwargs.update(self._internal_template_data["base_class_kwargs"])
        return {**template_data, "base_class_kwargs": base_class_kwargs}

    @override
    def _custom_template_data(self) -> dict[str, Any]:
        """Preserve legacy raw msgspec options for trusted custom templates."""
        template_data = super()._custom_template_data()
        raw_base_class_kwargs = self.extra_template_data.get("base_class_kwargs")
        internal_base_class_kwargs = self._internal_template_data["base_class_kwargs"]
        if not isinstance(raw_base_class_kwargs, dict):
            return {**template_data, "base_class_kwargs": raw_base_class_kwargs}
        return {
            **template_data,
            "base_class_kwargs": {**raw_base_class_kwargs, **internal_base_class_kwargs},
        }

    @override
    def apply_discriminator_tag(self, field: DataModelFieldBase, field_name: str, value: Any) -> bool:
        """Configure msgspec's tag; the discriminator leaves the instance fields.

        A tag msgspec refuses (``struct_tag_refused``) is left out, and the discriminator stays a field.
        """
        if struct_tag_refused(value):
            return False
        self.add_base_class_kwarg("tag_field", repr(field_name))
        self.add_base_class_kwarg("tag", repr(value))
        return True

    @override
    def opt_out_of_inherited_tag(self) -> None:
        """Clear the tag that msgspec makes the struct inherit, or that it has."""
        self.add_base_class_kwarg("tag", "False")

    def _own_tag_options(self, bases: list[tuple[Any, Any]]) -> tuple[Any, Any]:
        """Return the tag and tag_field options of the struct, given those of its generated bases."""
        options = self.extra_template_data.get("base_class_kwargs")
        options = {
            **(options if isinstance(options, dict) else {}),
            **{
                key: literal_eval(value)
                for key, value in self._internal_template_data["base_class_kwargs"].items()
                if key in {"tag", "tag_field"}
            },
        }
        return struct_tag_options(own_tag=options.get("tag"), own_tag_field=options.get("tag_field"), bases=bases)

    def _tag_options(self) -> tuple[Any, Any]:
        """Return the tag and tag_field options of the struct and its generated bases (``struct_tag_options``).

        Each struct is answered after its bases, from the snapshot of the structs reached through base classes, a
        component of structs that inherit from one another in a cycle as one unit: the bases a struct takes options
        from are those outside its component. The generated code cannot define such a cycle, which L6 reports.
        """
        walk = walk_graph(GraphSnapshot.STRUCT_TAGS, EdgeKind.BASE, (self,), _model_key_of_model, _struct_bases)
        graph = walk.graph
        options: dict[ModelKey, tuple[Any, Any]] = {}
        for component in graph.components((EdgeKind.BASE,)):
            for key in component:
                options[key] = walk.node_of[key]._own_tag_options([  # noqa: SLF001
                    options[base] for base in graph.ordered_targets(key, EdgeKind.BASE) if base not in component
                ])
        return graph_answer(
            GraphSnapshot.STRUCT_TAGS,
            options[walk.starts[0]],
            lambda: (
                options[walk.starts[0]] if graph.acyclic_order((EdgeKind.BASE,)) is None else self._walked_tag_options()
            ),
        )

    def _walked_tag_options(self) -> tuple[Any, Any]:
        """Return the tag and tag_field options of the struct by walking its generated bases."""
        return self._own_tag_options([base._walked_tag_options() for base in _struct_bases(self)])  # noqa: SLF001

    @property
    @override
    def discriminator_tag(self) -> tuple[Any, Any] | None:  # ty: ignore[invalid-property-type-override]
        """Return the tag field and the tag msgspec tells the struct apart by (``struct_discriminator_tag``)."""
        return struct_discriminator_tag(*self._tag_options(), class_name=self.class_name)

    @override
    def has_keyword_only_definition(self) -> bool:
        """Return whether msgspec's class declaration already enables keyword-only fields."""
        if self._internal_template_data["base_class_kwargs"].get("kw_only") == "True":
            return True
        base_class_kwargs = self.extra_template_data.get("base_class_kwargs")
        return isinstance(base_class_kwargs, dict) and base_class_kwargs.get("kw_only") in {True, "True"}

    @override
    def enable_model_keyword_only(self) -> None:
        """Enable msgspec's class-level keyword-only option."""
        self.add_base_class_kwarg("kw_only", "True")

    @classmethod
    @override
    def create_base_class_model(
        cls,
        config: dict[str, Any],
        reference: Reference,
        custom_template_dir: Path | None = None,
        keyword_only: bool = False,
        treat_dot_as_module: bool | None = None,
    ) -> Struct | None:
        """Create a shared base class model for DRY configuration.

        Creates a Struct that inherits from msgspec.Struct (aliased as _Struct)
        with the specified configuration. Updates the reference path and name in place.
        """
        reference.path = f"#/{cls.BASE_CLASS_NAME}"
        reference.name = cls.BASE_CLASS_NAME

        base_model = cls(
            reference=reference,
            fields=[],
            custom_template_dir=custom_template_dir,
            keyword_only=keyword_only,
            treat_dot_as_module=treat_dot_as_module,
        )

        base_model.base_classes = [BaseClassDataType(type=cls.BASE_CLASS_ALIAS)]

        for key, value in config.items():
            mapping_result = cls.CONFIG_MAPPING.get((key, value))
            if mapping_result is None:
                continue
            mapped_key, mapped_value = mapping_result
            base_model.add_base_class_kwarg(mapped_key, str(mapped_value))

        base_model._additional_imports.append(
            Import(from_=IMPORT_MSGSPEC_STRUCT.from_, import_=IMPORT_MSGSPEC_STRUCT.import_, alias=cls.BASE_CLASS_ALIAS)
        )

        return base_model


def _struct_bases(struct: Struct) -> list[Struct]:
    """Return the generated structs that ``struct`` inherits from, in the order its base classes list them."""
    return [
        base
        for base_class in struct.base_classes
        if base_class.reference and isinstance(base := base_class.reference.source, Struct)
    ]


class Constraints(_Constraints):
    """Constraint model for msgspec fields."""


@import_extender
class DataModelField(DataModelFieldBase):
    """Field implementation for msgspec Struct models."""

    _FIELD_ASSIGNMENT_ROW: ClassVar[AssignmentRow] = "msgspec"

    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = True
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = _ANNOTATED_CONSTRAINTS_CONTEXT
    _FIELD_KEYS: ClassVar[set[str]] = {
        "default",
        "default_factory",
    }
    _META_FIELD_KEYS: ClassVar[set[str]] = {
        "title",
        "description",
        "gt",
        "ge",
        "lt",
        "le",
        "multiple_of",
        "min_items",
        "max_items",
        "min_length",
        "max_length",
        "pattern",
        "examples",
        # 'unique_items', # not supported by msgspec
    }
    _PARSE_METHOD = "convert"
    _COMPARE_EXPRESSIONS: ClassVar[set[str]] = {"gt", "ge", "lt", "le", "multiple_of"}
    constraints: Optional[Constraints] = None  # noqa: UP045

    @override
    def process_const(self) -> None:
        """Process const field constraint; its default and nullability are those of ``decide_literal_default``."""
        if "const" not in self.extras:
            return
        self.const = True
        const = self.extras["const"]
        _apply_literal_default(self, decide_literal_default("msgspec-const", const, required=self.required))
        if self.data_type.type == "str" and isinstance(const, str):  # pragma: no cover
            self.replace_data_type(self.data_type.__class__(literals=[const]), clear_old_parent=False)

    def _unset_type_data_type(self) -> DataType:
        return self.data_type.__class__.from_import(IMPORT_MSGSPEC_UNSETTYPE)

    def _ordered_union_data_type(self, members: list[DataType], *, null: bool, unset: bool) -> DataType:
        """Return the union a field hint writes: ``members``, then None and the ``UNSET`` sentinel (row ``ordered``)."""
        data_type_class = self.data_type.__class__
        none_members = [data_type_class(type=NONE)] if null else []
        unset_members = [self._unset_type_data_type()] if unset else []
        return build_union(
            data_type_class,
            [*members, *none_members, *unset_members],
            "ordered",
            roles=(*("member",) * len(members), *("null",) * len(none_members), *("sentinel",) * len(unset_members)),
            use_union_operator=self._use_union_operator,
        )

    def _data_type_has_top_level_none(self, data_type: DataType) -> bool:
        """Return whether the type writes None at the top; row ``legacy-msgspec-top-level-null-as-written``.

        It reads ``is_optional`` as written before render preparation, without the None render preparation wrote.
        """
        if data_type._optional_before_render_preparation() or self._data_type_renders_none(data_type):  # noqa: SLF001
            return True
        if not data_type.is_union:
            return False
        return any(self._data_type_renders_none(child) for child in data_type.data_types)

    def _field_has_top_level_none(self) -> bool:
        return any((
            self.nullable is True,
            self.type_has_null is True,
            self._data_type_has_top_level_none(self.data_type),
        ))

    @staticmethod
    def _data_type_has_container(data_type: DataType) -> bool:
        return any((
            data_type.is_dict,
            data_type.is_list,
            data_type.is_set,
            data_type.is_frozen_set,
            data_type.is_mapping,
            data_type.is_sequence,
            data_type.is_tuple,
        ))

    @staticmethod
    def _data_type_is_plain_union(data_type: DataType) -> bool:
        return bool(union_members(data_type))

    def _data_type_spreads_members(self, data_type: DataType) -> bool:
        """Return whether a field's unset union writes the members of its union type one by one.

        A union none of whose members writes anything renders ``Any`` whole, as every other hint of it does.
        """
        return any(self._data_type_writes_hint(member) for member in union_members(data_type))

    def _copy_data_type(self, data_type: DataType) -> DataType:
        copied = data_type.model_copy()
        copied.parent = None
        copied.children = []
        copied.data_types = [self._copy_data_type(child) for child in data_type.data_types]
        copied.literals = list(data_type.literals)
        copied.enum_member_literals = list(data_type.enum_member_literals)
        if data_type.dict_key:
            copied.dict_key = self._copy_data_type(data_type.dict_key)
        if data_type.kwargs:
            copied.kwargs = dict(data_type.kwargs)
        return copied

    def _copy_data_type_without_top_level_none(self, data_type: DataType) -> DataType:
        """Copy a data type without its top-level None, for a hint that writes None itself.

        The None is the type's own when only the nullable keyword of the field declares it.
        """
        data_type = self._copy_data_type(data_type)
        data_type._strip_null(own=self.nullable is True and not self.type_has_null)  # noqa: SLF001
        if data_type.is_union:
            data_type.data_types = [child for child in data_type.data_types if not self._data_type_renders_none(child)]
        return data_type

    def _unset_union_data_type(self) -> DataType:
        if self._data_type_renders_none(self.data_type):
            return self._ordered_union_data_type([], null=True, unset=True)

        data_types = []
        has_none = self._field_has_top_level_none()
        match (has_none, self._data_type_spreads_members(self.data_type)):
            case (False, True):
                data_types.extend(self._copy_data_type(child) for child in self.data_type.data_types)
            case (True, _):
                if self._data_type_has_renderable_structure(
                    base_data_type := self._copy_data_type_without_top_level_none(self.data_type)
                ) and not self._data_type_renders_none(base_data_type):
                    data_types.append(base_data_type)
            case _:
                if self._data_type_has_renderable_structure(
                    base_data_type := self._copy_data_type(self.data_type)
                ) and not self._data_type_renders_none(base_data_type):
                    data_types.append(base_data_type)
        return self._ordered_union_data_type(data_types, null=has_none, unset=True)

    def _annotated_base_data_type(self) -> DataType:
        if not self._field_has_top_level_none():
            return self._copy_data_type(self.data_type)
        return self._copy_data_type_without_top_level_none(self.data_type)

    def _annotated_union_data_type(self, meta: CallPlan, *, include_unset: bool) -> DataType:
        """Return the ``Annotated`` field type, over None where the type writes nothing, as the field hint does."""
        data_type = self.data_type
        annotated = _MetaAnnotatedType(
            meta=meta,
            data_types=[self._annotated_base_data_type()],
            python_version=data_type.python_version,
            use_standard_collections=data_type.use_standard_collections,
            use_generic_container=data_type.use_generic_container,
            use_union_operator=data_type.use_union_operator,
            treat_dot_as_module=data_type.treat_dot_as_module,
            use_serialize_as_any=data_type.use_serialize_as_any,
        )
        return self._ordered_union_data_type([annotated], null=self._field_has_top_level_none(), unset=include_unset)

    def _written_type(self, meta: CallPlan | None) -> DataType | None:
        """Return the copy of the field's type that its annotation writes, or None when it writes the type itself.

        It is the ``Annotated`` type with ``meta``, with ``UnsetType`` unless the field is required or nullable, or
        the union with ``UnsetType`` of a field its struct may leave unset. Like the copies it holds, it keeps no
        parents, so render plans compare by value.
        """
        if meta is not None:
            written = self._annotated_union_data_type(meta, include_unset=not (self.required or self.nullable))
        elif self._not_required and not self.nullable:
            written = self._unset_union_data_type()
        else:
            return None
        for node in written.all_data_types:
            node.parent = None
        return written

    def _get_simple_unset_base_type(self) -> str | None:  # noqa: PLR0911
        """Return the existing leaf hint when direct unset rendering is equivalent."""
        parent = self.parent
        data_type = self.data_type
        if type(self) is not DataModelField or type(parent) is not Struct:
            return None
        if self.required or self.nullable or self.use_annotated:
            return None
        if data_type.data_types or data_type.dict_key or data_type.reference or data_type.python_type:
            return None
        if (type_hint := data_type.type) is None or not is_identifier(type_hint, data_type.python_version):
            return None
        if data_type.literals or data_type.enum_member_literals or data_type.kwargs or data_type.alias:
            return None
        if data_type.is_dict or data_type.is_list or data_type.is_set or data_type.is_frozen_set:
            return None
        if data_type.is_mapping or data_type.is_sequence or data_type.is_tuple:
            return None
        data_type_class = type(data_type)
        if data_type_class is not DataType and data_type_class is not _create_context_data_type(
            "ContextDataType",
            DataType,
            data_type.python_version,
            data_type.use_standard_collections,
            data_type.use_generic_container,
            data_type.use_union_operator,
            data_type.treat_dot_as_module,
            data_type.use_serialize_as_any,
        ):
            return None
        if (
            data_type.is_custom_type
            or data_type.discriminator is not None
            or data_type.is_optional
            or data_type.is_func
        ):
            return None
        if self._has_explicit_typing_import_requirements(data_type) or self.type_has_null is True:
            return None
        if type_hint == NONE:
            return None
        if type_hint == "UnsetType":
            return None
        return type_hint

    def _get_simple_unset_type_hint(self) -> str | None:
        """Render a standard unset union without copying its data type graph."""
        if (type_hint := self._get_simple_unset_base_type()) is None:
            return None

        match self._use_union_operator:
            case True:
                return f"{type_hint} | UnsetType"
            case _:
                pass
        return f"Union[{type_hint}, UnsetType]"

    def _imports_data_type(self, plan: FieldRenderPlan) -> DataType:
        """Return the type imports collect from: the one the annotation of ``plan`` writes, else the field's own.

        The ``Annotated`` type of a field neither required, nullable nor in a struct is not.
        """
        if plan.annotated_call is not None and not (self.required or self.nullable or self._not_required):
            return self.data_type
        return cast("DataType | None", plan.written_type) or self.data_type

    def _has_numeric_data_type(self, type_name: str) -> bool:
        """Return whether any field data type is the given numeric type."""
        return any(data_type.type == type_name for data_type in self.data_type.all_data_types)

    def _get_strict_field_constraint(
        self, constraint: str, value: Any, *, is_float_type: bool, is_int_type: bool
    ) -> tuple[str, Any] | None:
        """Return a constraint normalized for the field's numeric type.

        Non-finite bounds are dropped because msgspec Meta only accepts finite values (``_is_non_finite``).
        """
        if value is None or constraint not in self._COMPARE_EXPRESSIONS:
            return constraint, value
        if _is_non_finite(value):
            return None
        if is_float_type:
            return constraint, float(value)
        if is_int_type:
            return normalize_integer_constraint(constraint, value)
        if isinstance(value, float) and value.is_integer():
            return constraint, int(value)
        return constraint, value

    @property
    @override
    def field(self) -> str | None:
        """For backwards compatibility."""
        return self._plan_text(self._render_plan()) or None

    def _field_call_arguments(self, omitted: list[Omitted]) -> tuple[CallArguments, bool]:
        """Build the ``field(...)`` arguments in the order the call writes them, and whether one converts Structs."""
        args = CallArguments(omitted)
        for key, value in self.extras.items():
            if key in self._FIELD_KEYS:
                args.put(key, value, Why(_FIELD_ARGUMENT, key), (f"extra:{key}",))
        converts = False
        if self.alias is not None:
            args.put("name", self.alias, _ALIAS, ("alias",))

        if self.default is not UNDEFINED and self.default is not None:
            args.put("default", self.default, _DEFAULT)
        elif self._not_required and "default_factory" not in args:
            args.put("default", None if self.nullable else UNSET, _NONE_DEFAULT if self.nullable else _UNSET_DEFAULT)

        if self.required and not self.use_default_with_required:
            args.pop("default", "factory-not-applicable", _REQUIRED)
            args.pop("default_factory", "factory-not-applicable", _REQUIRED)
        elif (
            self.default is not UNDEFINED
            and self.default is not None
            and "default_factory" not in args
            and (conversion := self._get_default_as_struct_model()) is not None
        ):
            default = cast("Kwarg", args.take("default"))
            args.put("default_factory", conversion, _STRUCT_CONVERSION, default.items)
            converts = True

        if (
            (entry := args.entry("default")) is not None
            and isinstance(entry.value, (list, dict, set))
            and "default_factory" not in args
        ):
            args.take("default")
            args.put("default_factory", _ValueFactory(entry.value), _MUTABLE_FACTORY, entry.items)

        if (
            self._takes_nested_model_factory()
            and "default_factory" not in args
            and (nested := self._get_default_factory_for_optional_nested_model()) is not None
        ):
            args.put("default_factory", nested, _NESTED_MODEL_FACTORY)
            args.pop("default", "superseded", _NESTED_MODEL_FACTORY)
        return args, converts

    @override
    def _has_default_for_nested_model_factory(self) -> bool:
        """Preserve the UNSET default emitted for optional Struct fields."""
        return not self.required or super()._has_default_for_nested_model_factory()

    @override
    def _written_assignment(self) -> MsgspecWritten:
        """Return the ``field()`` arguments the field's render plan writes and whether its call renders."""
        data = written_data(written_argument_names(self._render_plan()))
        return MsgspecWritten(data=data, text=written_text(self, writes=data.any))

    @override
    def _build_frozen_render_plan(self) -> FieldRenderPlan:
        return self._render_plan()

    @override
    def _build_render_plan(self) -> FieldRenderPlan:
        """Build the plan: ``Meta(...)`` inside ``Annotated``, then ``field(...)``, the default alone, or nothing.

        A tag field (a class variable) keeps both for templates that read them; the built-in template skips it.
        """
        omitted: list[Omitted] = []
        args, converts = self._field_call_arguments(omitted)
        meta = self._build_meta_call(omitted)
        kwargs = args.kwargs()
        call: CallPlan | None = None
        default = FieldDefault(self.default, missing_sentinel=False, why=_DEFAULT)
        bare_default = False
        if len(kwargs) == 1 and kwargs[0].name == "default":
            default = FieldDefault(kwargs[0].value, missing_sentinel=False, why=_BARE_DEFAULT, items=kwargs[0].items)
            bare_default = True
        elif kwargs:
            call = CallPlan(None, None, kwargs, default_keyword=False, why=_FIELD_CALL, callee="field")
        symbol_imports: list[tuple[Import, Why]] = []
        if not self.is_class_var:
            if call is not None:
                symbol_imports.append((IMPORT_MSGSPEC_FIELD, call.why))
            if converts:
                symbol_imports.append((IMPORT_MSGSPEC_CONVERT, _STRUCT_CONVERSION))
            if meta is not None:
                symbol_imports.append((IMPORT_MSGSPEC_META, meta.why))
            if args.get("default") is UNSET:
                symbol_imports.append((IMPORT_MSGSPEC_UNSET, _UNSET_DEFAULT))
        form, why = (
            ("classvar", _CLASS_VAR)
            if self.is_class_var
            else ("plain", _PLAIN_ANNOTATION)
            if meta is None
            else ("annotated", _ANNOTATED)
        )
        return FieldRenderPlan(
            annotation_form=form,
            call=call,
            default=default,
            has_default_factory_in_field="default_factory" in args,
            omitted=tuple(omitted),
            symbol_imports=tuple(symbol_imports),
            why=why,
            annotated_call=meta,
            bare_default=bare_default,
            written_type=self._written_type(meta),
        )

    @staticmethod
    def _plan_text(plan: FieldRenderPlan) -> str:
        """Write what follows the annotation from ``plan``: ``field(...)``, the default alone, or nothing."""
        if (call := plan.call) is not None:
            return field_call_text(call)
        return represent_python_value(plan.default.value) if plan.bare_default else ""

    def _render_plan_text(self, plan: FieldRenderPlan) -> tuple[object, ...]:
        """Return what the field writes from ``plan``, to compare a frozen plan with a rebuilt one."""
        return (
            self._plan_text(plan),
            None if plan.annotated_call is None else _meta_call_text(plan.annotated_call),
            plan.annotation_form,
            tuple(import_ for import_, _ in plan.symbol_imports),
            tuple((omitted.item, omitted.reason, omitted.warns) for omitted in plan.omitted),
        )

    def _declared_render_items(self) -> frozenset[str]:
        """Return the items the field declares that its plan must write or omit.

        They are its ``field()`` and ``Meta()`` metadata, its alias and its constraints.
        """
        items = {f"extra:{key}" for key in self.extras if key in self._FIELD_KEYS or key in self._META_FIELD_KEYS}
        if self.alias is not None:
            items.add("alias")
        if self.constraints is not None:
            items.update(
                f"constraint:{key}" for key, value in self.constraints.model_dump().items() if value is not None
            )
        return frozenset(items)

    def _get_field_data(self) -> dict[str, Any]:
        """Return the ``field()`` arguments the field writes, its default factory as text."""
        plan = self._render_plan()
        if plan.bare_default:
            return {"default": plan.default.value}
        if (call := plan.call) is None:
            return {}
        return {
            argument.name: str(argument.value) if argument.name == "default_factory" else argument.value
            for argument in call.kwargs
        }

    @override
    def __str__(self) -> str:
        """Generate field() call or default value representation."""
        return self._plan_text(self._render_plan())

    @property
    @override
    def type_hint(self) -> str:
        """Return the type hint, using UnsetType for non-required non-nullable fields."""
        if (simple_unset_type_hint := self._get_simple_unset_type_hint()) is not None:
            return simple_unset_type_hint
        if self._not_required and not self.nullable:
            plan = self._render_plan()
            written = plan.written_type if plan.annotated_call is None else self._unset_union_data_type()
            return cast("DataType", written).type_hint
        return super().type_hint

    def _hint_meta(self) -> tuple[bool, str | None]:
        """Return whether the hint renders the field's own type, and the ``Meta()`` it annotates the type with.

        An unset union, and an ``Annotated`` type of a required or nullable field, render a copy of the type.
        """
        meta = self._get_meta_string() if self.use_annotated else None
        copied = (self._not_required and not self.nullable) or (meta is not None and (self.required or self.nullable))
        return not copied, meta

    @override
    def _hint_renders_own_type(self) -> bool:
        return self._hint_meta()[0]

    @override
    def _import_state_data_type(self) -> DataType | None:
        """Return the field's own data type, or None when ``imports`` collects from a copy of it."""
        own, meta = self._hint_meta()
        return self._import_state_target(needs_annotated=meta is not None, data_type=self.data_type) if own else None

    @property
    @override
    def imports(self) -> tuple[Import, ...]:
        """Get imports from the structurally rendered msgspec annotation."""
        if self._get_simple_unset_base_type() is not None:
            imports = self._collect_field_imports(needs_annotated=False, data_type=self.data_type)
            if IMPORT_MSGSPEC_UNSETTYPE not in imports:
                imports = (*imports, IMPORT_MSGSPEC_UNSETTYPE)
            if not self._use_union_operator and IMPORT_UNION not in imports:
                imports = (*imports, IMPORT_UNION)
            return imports
        plan = self._render_plan()
        return self._collect_field_imports(
            needs_annotated=plan.annotated_call is not None,
            data_type=self._imports_data_type(plan),
        )

    @property
    def _not_required(self) -> bool:
        return not self.required and isinstance(self.parent, Struct)

    @property
    @override
    def fall_back_to_nullable(self) -> bool:
        """Return whether to fall back to nullable type instead of UnsetType."""
        return not self._not_required

    def _meta_keywords(self, data_type: DataType) -> frozenset[str]:
        """Return the constraint keywords msgspec.Meta takes on a type.

        Numbers take bounds, sized types take lengths and str takes a pattern, as the msgspec type table lists;
        a mapping takes lengths, a fixed tuple and a union take none, and an unclassified type keeps them all. A
        referenced type alias takes what its type takes (``_meta_type``); any other referenced model, such as an enum
        or a Struct, takes none.
        """
        if (target := self._meta_type(data_type)) is None:
            return _applicability.NO_KEYWORDS
        if (keywords := self._shape_meta_keywords(target)) is not None:
            return keywords
        return _applicability.meta_keywords(
            DataTypeManager.constraint_keywords(target, self.target_version_key(target))
        )

    def _shape_meta_keywords(self, data_type: DataType) -> frozenset[str] | None:
        """Return the keywords a mapping, a fixed tuple or a union takes, or None for a type of another shape."""
        return _applicability.shape_meta_keywords(
            mapping=data_type.is_dict or data_type.is_mapping,
            unconstrained=data_type.is_tuple or self._data_type_is_plain_union(data_type),
        )

    def _meta_type(self, data_type: DataType, seen: frozenset[int] = frozenset()) -> DataType | None:
        """Return the type msgspec checks the ``Meta`` of a type on, or None where that type takes no constraint.

        A referenced type alias stands for its type, and takes none when that type writes None; any other referenced
        model takes none. A chain of aliases that comes back to an alias of its own, and an alias without a field,
        such as a GraphQL scalar, stand for themselves, as unclassified types.
        """
        if self._shape_meta_keywords(data_type) is not None:
            return data_type
        if (
            (reference := data_type.reference) is not None
            and (source := reference.source) is not None
            and id(source) not in seen
            and (source.fields or not isinstance(source, TypeAliasBase))
        ):
            alias_field = source.fields[0] if isinstance(source, TypeAliasBase) else None
            if alias_field is None or any((
                alias_field.nullable is True,
                alias_field.type_has_null is True,
                self._data_type_has_top_level_none(alias_field.data_type),
            )):
                return None
            return self._meta_type(alias_field.data_type, seen | {id(source)})
        if len(data_type.data_types) == 1 and not (
            data_type.type or data_type.reference or data_type.python_type or self._data_type_has_container(data_type)
        ):
            return self._meta_type(data_type.data_types[0], seen)
        return data_type

    def _get_meta_data(self) -> tuple[dict[str, Any], frozenset[str]]:
        """Return the Meta(...) keyword data that msgspec takes on the type, and the constraints it leaves out."""
        args, dropped = self._meta_call_arguments([])
        return args.data(), dropped

    def _meta_call_arguments(self, omitted: list[Omitted]) -> tuple[CallArguments, frozenset[str]]:
        """Return the ``Meta(...)`` arguments msgspec takes on the type, and the constraints it leaves out.

        The constraints it does not take go to ``omitted``, warning unless the values of the field's ``Literal`` decide
        them; so does a float ``multiple_of`` that msgspec checks inexactly.
        """
        args = self._declared_meta_arguments(omitted)
        data = args.data()
        untaken, inexact = self._dropped_meta_keywords(data)
        dropped = (untaken | {"multiple_of"}) if inexact else untaken
        decided = self._literal_decided_meta_keywords(data) if untaken else frozenset()
        for name in args.names():
            if name not in dropped:
                continue
            entry = cast("Kwarg", args.take(name))
            if name in untaken:
                literal = name in decided
                reason: OmitReason = "consumed-by-type" if literal else "untaken-by-type"
                why = Why(_LITERAL_DECIDED if literal else _UNTAKEN, name)
                warns = not literal
            else:
                reason, why, warns = "inexact-float-divisor", Why(_INEXACT, name), True
            omitted.extend(Omitted(item, entry.value, reason, why, warns=warns) for item in entry.items)
        return args, dropped

    def _declared_meta_arguments(self, omitted: list[Omitted]) -> CallArguments:
        """Return the ``Meta(...)`` arguments of the field's metadata and constraints, before the type decides them.

        Number bounds are normalized for the field's numeric type, the weaker of two bounds of one side goes, and
        item counts are written as lengths.
        """
        args = CallArguments(omitted)
        for key, value in self.extras.items():
            if key in self._META_FIELD_KEYS:
                args.put(key, value, Why(_META_ARGUMENT, key), (f"extra:{key}",))
        unset = [cast("Kwarg", args.take(entry.name)) for entry in args.kwargs() if entry.value is None]
        omitted.extend(Omitted(item, None, "none-value", _NONE_VALUE) for entry in unset for item in entry.items)
        if self.constraints is not None:
            declared = {k: v for k, v in self.constraints.model_dump().items() if v is not None}
            if (blocked := self._constraints_blocked_by()) is not None:
                omitted.extend(Omitted(f"constraint:{k}", v, blocked, Why(_CONSTRAINT, k)) for k, v in declared.items())
            else:
                for key, argument in self._normalized_meta_constraints(declared, omitted).items():
                    args.put(key, argument.value, argument.why, argument.items)

        if (gt := args.get("gt")) is not None and (ge := args.get("ge")) is not None:
            args.pop("ge" if gt >= ge else "gt", "superseded", Why(_STRONGER_BOUND, "gt"))
        if (lt := args.get("lt")) is not None and (le := args.get("le")) is not None:
            args.pop("le" if lt <= le else "lt", "superseded", Why(_STRONGER_BOUND, "lt"))

        for items_key, length_key in (("min_items", "min_length"), ("max_items", "max_length")):
            if (entry := args.take(items_key)) is not None:
                args.put(length_key, entry.value, Why(_ITEMS_AS_LENGTH, items_key), entry.items)
        return args

    def _normalized_meta_constraints(self, declared: dict[str, Any], omitted: list[Omitted]) -> dict[str, Kwarg]:
        """Return the constraints ``Meta(...)`` writes, normalized for the field's numeric type, merging collisions.

        A constraint ``Meta`` has no argument for, a non-finite bound, an integer ``multiple_of`` of one and, on an
        int (``_names_int``), a number beyond the 64-bit range go to ``omitted``; that last one warns, also where the
        number has no finite float.
        """
        has_integer_constraints = any(key in self._COMPARE_EXPRESSIONS for key in declared)
        is_float_type = has_integer_constraints and self._has_numeric_data_type("float")
        is_int_type = has_integer_constraints and not is_float_type and self._has_numeric_data_type("int")
        annotates_int = (
            has_integer_constraints
            and self.use_annotated
            and (target := self._meta_type(self._annotated_base_data_type())) is not None
            and self._names_int(target)
        )
        values: dict[str, Any] = {}
        sources: dict[str, tuple[str, ...]] = {}
        for k, v in declared.items():
            item = f"constraint:{k}"
            if k not in self._META_FIELD_KEYS:
                omitted.append(Omitted(item, v, "backend-cannot-express", Why(_CONSTRAINT, k)))
                continue
            normalized = self._get_strict_field_constraint(k, v, is_float_type=is_float_type, is_int_type=is_int_type)
            if annotates_int and _beyond_int_limits(*(constraint := normalized or (k, v))):
                omitted.append(
                    Omitted(item, constraint[1], "backend-cannot-express", Why(_INT64_LIMIT, constraint[0]), warns=True)
                )
                continue
            if normalized is None:
                if _is_non_finite(v):
                    omitted.append(Omitted(item, v, "backend-cannot-express", Why(_NON_FINITE, k)))
                else:
                    omitted.append(Omitted(item, v, "consumed-by-type", Why(_MULTIPLE_OF_ONE, k)))
                continue
            merge_normalized_constraint(values, normalized[0], normalized[1])
            sources[normalized[0]] = (*sources.get(normalized[0], ()), item)
        return {
            key: Kwarg(key, value, Why(_CONSTRAINT, sources[key][0].removeprefix("constraint:")), sources[key])
            for key, value in values.items()
        }

    def _names_int(self, data_type: DataType) -> bool:
        """Return whether a type is int, also where an annotation names the int that Annotated or Final qualifies."""
        if (python_type := data_type.python_type) is None:
            return data_type.type == "int"
        return _qualified_type_path(python_type.expression, self.target_version_key(data_type)) == "int"

    def _dropped_meta_keywords(self, data: dict[str, Any]) -> tuple[frozenset[str], bool]:
        """Return the constraints msgspec.Meta does not take on the type, and whether a float multiple_of is inexact.

        msgspec checks `multiple_of` on a float by its floating-point remainder, so a divisor a float only
        approximates, such as 0.01, rejects its own multiples.
        """
        if not (
            constrained := {k for k, v in data.items() if v is not None and k in _applicability.META_SCHEMA_KEYWORDS}
        ):
            return frozenset(), False
        untaken = frozenset(constrained - self._meta_keywords(self._annotated_base_data_type()))
        inexact = (
            "multiple_of" in constrained - untaken
            and self._has_numeric_data_type("float")
            and not _is_exact_float_divisor(data["multiple_of"])
        )
        return untaken, inexact

    def _literal_decided_meta_keywords(self, data: dict[str, Any]) -> frozenset[str]:
        """Return the constraints the values of a const's or an enum's ``Literal`` decide, which it leaves out silently.

        The parser keeps only the values that meet them (``_applicability.literal_check``), or warns that none does. A
        constraint that only some of the values meet stays reported: the values that fail it are kept, as without
        --filter-literal-values, and the ``Meta`` keyword would have rejected them.
        """
        if not _is_value_literal(base := self._annotated_base_data_type()):
            return frozenset()
        checks = {
            meta: _applicability.literal_check(base.literals, {schema: data[meta]})
            for meta, schema in _applicability.META_SCHEMA_KEYWORDS.items()
            if schema in _applicability.LITERAL_DECIDABLE_KEYWORDS and data.get(meta) is not None
        }
        return frozenset(meta for meta, (decided, met) in checks.items() if decided and (all(met) or not any(met)))

    def _union_leaves(
        self,
        member: DataType,
        spread: tuple[tuple[DataType, DataModelField], ...] = (),
        constraints: tuple[tuple[str, Any], ...] = (),
        seen: frozenset[int] = frozenset(),
    ) -> Iterator[_UnionLeaf]:
        """Yield the types msgspec decodes a union member as, with their types without aliases and Meta constraints.

        A member is an alias chain ending in a scalar or a Literal of one type; msgspec spreads an alias of a union,
        or of a type and None, into the union that names it, so the members of such an alias count as its own.
        Other members yield nothing.
        """
        data_type = member
        while (reference := data_type.reference) is not None:
            source = reference.source
            if (
                data_type.is_optional
                or not isinstance(source, TypeAliasBase)
                or len(source.fields) != 1
                or id(source) in seen
                or not isinstance(alias_field := source.fields[0], DataModelField)
            ):
                return
            seen |= {id(source)}
            if alias_field.use_annotated:
                meta = alias_field._get_meta_data()[0]  # noqa: SLF001
                constraints += tuple(
                    (k, v) for k, v in meta.items() if v is not None and k in _applicability.META_SCHEMA_KEYWORDS
                )
            if alias_field._field_has_top_level_none() or self._data_type_is_plain_union(alias_field.data_type):  # noqa: SLF001
                members = self._non_none_members(alias_field.data_type)
                for inner in members:
                    yield from self._union_leaves(
                        inner,
                        (*spread, (member, alias_field)),
                        constraints if len(members) == 1 else (),
                        seen,
                    )
                return
            data_type = alias_field.data_type
        if (
            data_type.data_types
            or data_type.is_optional
            or data_type.enum_member_literals
            or self._data_type_has_container(data_type)
        ):
            return
        kinds = {type(literal).__name__ for literal in data_type.literals} or {data_type.type}
        yield from (
            _UnionLeaf(spread, member, kind, data_type, constraints) for kind in kinds if kind and len(kinds) == 1
        )

    def _non_none_members(self, data_type: DataType) -> list[DataType]:
        """Return the members of a type other than None, spreading the unions and wrappers that render as them."""
        if (
            data_type.type is None
            and data_type.reference is None
            and data_type.alias is None
            and data_type.data_types
            and not self._data_type_has_container(data_type)
        ):
            return [member for child in data_type.data_types for member in self._non_none_members(child)]
        return [] if self._data_type_renders_none(data_type) else [data_type]

    def _union_members(self, data_type: DataType) -> Iterator[tuple[DataType, int, DataType]]:
        """Yield each member of a union with its owner and index, spreading member unions that render as one."""
        for index, member in enumerate(data_type.data_types):
            if member.type is None and member.reference is None and self._data_type_is_plain_union(member):
                yield from self._union_members(member)
            else:
                yield data_type, index, member

    def _unconstrained_members(
        self,
        member: DataType,
        dropped: Mapping[int, _UnionLeaf],
        spread: Mapping[int, DataModelField],
    ) -> list[DataType]:
        """Return a union member written with the dropped members as their types without Meta.

        An alias spread to reach a dropped member is written as its members, so the alias itself keeps its Meta.
        """
        if (leaf := dropped.get(id(member))) is not None:
            return [self._copy_data_type(leaf.base)]
        if (alias_field := spread.get(id(member))) is None:
            copied = self._copy_data_type(member)
            for nested in copied.all_data_types:
                nested.register_reference()
                for child in nested.data_types:
                    child.parent = nested
            return [copied]
        members = [
            unconstrained
            for inner in self._non_none_members(alias_field.data_type)
            for unconstrained in self._unconstrained_members(inner, dropped, spread)
        ]
        if alias_field._field_has_top_level_none():  # noqa: SLF001
            members.append(self.data_type.__class__(type=NONE))
        return members

    @staticmethod
    def _conflicting_leaves(leaves: Mapping[str, list[_UnionLeaf]]) -> tuple[dict[int, _UnionLeaf], set[int]]:
        """Return the constrained members of a union that would reject values another member accepts, by identity.

        Each such member comes with the constraints it loses. A member of one type whose constraints are the ones
        every member of that type shares keeps them, since msgspec checks them on every member; the other members
        lose all of theirs. Also return the int members that would reject an integer a float member accepts, since
        msgspec decodes every integer as the int member; constraints that admit every such integer stay.
        """
        dropped: dict[int, _UnionLeaf] = {}
        for same_kind in leaves.values():
            if len(same_kind) < 2:  # noqa: PLR2004
                continue
            constraint_sets = [frozenset(leaf.constraints) for leaf in same_kind]
            shared = frozenset.intersection(*constraint_sets)
            kept = shared if shared in constraint_sets else frozenset()
            dropped.update(
                (id(leaf.member), leaf._replace(constraints=tuple(c for c in leaf.constraints if c not in kept)))
                for leaf, constraints in zip(same_kind, constraint_sets, strict=True)
                if constraints != kept
            )
        floats = [
            () if id(leaf.member) in dropped else leaf.constraints
            for leaf in leaves.get("float", ())
            if not leaf.base.literals
        ]
        integers = [
            leaf
            for leaf in leaves.get("int", ())
            if leaf.constraints
            and id(leaf.member) not in dropped
            and not all(_admits_integers_of(leaf.constraints, other) for other in floats)
        ]
        dropped.update((id(leaf.member), leaf) for leaf in integers)
        return dropped, {id(leaf.member) for leaf in integers}

    @override
    def unconstrain_union_members(self) -> bool:
        """Write the members of a union that msgspec decodes as one type without their Meta constraints.

        msgspec checks every member of a type in a union against the constraints of all of them, and decodes every
        integer as the int member when the union has a float member too, so a constraint of such a member would reject
        values another member accepts; the union accepts the values of every member. Return whether the field changed.
        """
        changed = False
        kinds: set[str] = set()
        keywords: set[str] = set()
        integer_keywords: set[str] = set()
        for data_type in tuple(self.data_type.all_data_types):
            if data_type.is_tuple or len(data_type.data_types) < 2:  # noqa: PLR2004
                continue
            slots = list(self._union_members(data_type))
            leaves: dict[str, list[_UnionLeaf]] = {}
            for _, _, member in slots:
                for leaf in self._union_leaves(member):
                    leaves.setdefault(leaf.kind, []).append(leaf)
            dropped, integers = self._conflicting_leaves(leaves)
            for member_id, leaf in dropped.items():
                if member_id in integers:
                    integer_keywords.update(leaf.keywords)
                else:
                    kinds.add(leaf.kind)
                    keywords.update(leaf.keywords)
            spread = {id(member): alias_field for leaf in dropped.values() for member, alias_field in leaf.spread}
            for owner, index, member in reversed(slots):
                if id(member) in dropped or id(member) in spread:
                    changed = True
                    member.unregister_reference()
                    owner.data_types[index : index + 1] = members = self._unconstrained_members(member, dropped, spread)
                    for unconstrained in members:
                        unconstrained.parent = owner
        self._record_unconstrained_union_members(kinds, keywords, integer_keywords)
        return changed or bool(kinds or integer_keywords)

    @override
    def inherit_unconstrained_union_members(self, source: DataModelFieldBase) -> None:
        """Take over the union members written without constraints from a field whose type this field inlines.

        Collapsing a root model copies its field's type into this field, so this field warns about its members.
        """
        if (recorded := source.__dict__.get(_UNCONSTRAINED_UNION_MEMBERS_KEY)) is not None:
            self._record_unconstrained_union_members(*recorded)

    def _record_unconstrained_union_members(
        self, kinds: Collection[str], keywords: Collection[str], integer_keywords: Collection[str]
    ) -> None:
        """Record the kinds and the Meta keywords of the union members this field is written without."""
        if not kinds and not integer_keywords:
            return
        recorded_kinds, recorded_keywords, recorded_integer_keywords = self.__dict__.get(
            _UNCONSTRAINED_UNION_MEMBERS_KEY, (frozenset(), frozenset(), frozenset())
        )
        self.__dict__[_UNCONSTRAINED_UNION_MEMBERS_KEY] = (
            recorded_kinds | frozenset(kinds),
            recorded_keywords | frozenset(keywords),
            recorded_integer_keywords | frozenset(integer_keywords),
        )
        self.invalidate_semantic_caches()

    @property
    @override
    def dropped_constraints_warning(self) -> str | None:
        """Return a warning naming the constraints left out because msgspec.Meta does not take or check them exactly.

        Those of the field's own ``Meta(...)`` are the omitted items of its render plan that warn.
        """
        omitted = self._render_plan().omitted
        reasons: list[str] = []
        untaken = frozenset(cast("str", o.why.keyword) for o in omitted if o.reason == "untaken-by-type" and o.warns)
        inexact = next((o for o in omitted if o.reason == "inexact-float-divisor" and o.warns), None)
        beyond = {cast("str", o.why.keyword): o.value for o in omitted if o.why.rule == _INT64_LIMIT}
        if untaken:
            reasons.append(
                f"msgspec.Meta does not take {', '.join(sorted(untaken))} on "
                f"{self._annotated_base_data_type().type_hint}"
            )
        if beyond:
            reasons.append(
                "msgspec.Meta takes the number constraints of an int only within the 64-bit range, not "
                + ", ".join(f"{name}={represent_python_value(value)}" for name, value in sorted(beyond.items()))
            )
        if inexact is not None:
            reasons.append(
                "msgspec checks multiple_of on float by a floating-point remainder, which rejects multiples of "
                f"{represent_python_value(inexact.value)} that floats only approximate"
            )
        kinds, union_keywords, integer_keywords = self.__dict__.get(
            _UNCONSTRAINED_UNION_MEMBERS_KEY, (frozenset(), frozenset(), frozenset())
        )
        if kinds:
            reasons.append(
                f"msgspec checks all {', '.join(sorted(kinds))} members of a union against the constraints of each "
                f"of them, so {', '.join(sorted(union_keywords))} of one member would reject values another "
                "member accepts"
            )
        if integer_keywords:
            reasons.append(
                "msgspec decodes every integer as the int member of a union that has a float member too, so "
                f"{', '.join(sorted(integer_keywords))} of the int member would reject integers the float member "
                "accepts"
            )
        if not reasons:
            return None
        return self._generated_without_constraints(
            "; ".join(reasons),
            len(untaken | frozenset(beyond) | union_keywords | integer_keywords) + (inexact is not None),
        )

    def _get_meta_string(self) -> str | None:
        """Return the ``Meta(...)`` the field writes inside ``Annotated``, or None (always without --use-annotated)."""
        return None if (meta := self._meta_call()) is None else _meta_call_text(meta)

    def _meta_call(self) -> CallPlan | None:
        """Return the ``Meta(...)`` call of the field's render plan, the frozen one or before B2 the live one."""
        return self._render_plan().annotated_call

    def _build_meta_call(self, omitted: list[Omitted]) -> CallPlan | None:
        """Build the ``Meta(...)`` call written inside ``Annotated``: its arguments, under --use-annotated."""
        if not self.use_annotated:
            args = self._declared_meta_arguments(omitted)
            for argument in args.kwargs():
                args.omit_entry(argument, "meta-needs-annotated", _NEEDS_ANNOTATED)
            return None
        if not (kwargs := self._meta_call_arguments(omitted)[0].kwargs()):
            return None
        return CallPlan(None, None, kwargs, default_keyword=False, why=_META_CALL, callee="Meta")

    @property
    @override
    def annotated(self) -> str | None:
        """Get Annotated type hint with Meta constraints.

        For ClassVar fields (discriminator tag_field), ClassVar is required
        regardless of use_annotated setting.
        """
        if self.extras.get("is_classvar"):  # pragma: no cover
            meta = self._get_meta_string()
            if self.use_annotated and meta:
                return f"ClassVar[Annotated[{self.type_hint}, {meta}]]"
            return f"ClassVar[{self.type_hint}]"

        if not self.use_annotated or (plan := self._render_plan()).annotated_call is None:
            return None
        return cast("DataType", plan.written_type).type_hint

    @property
    @override
    def needs_annotated_import(self) -> bool:
        """Check if this field requires the Annotated import.

        ClassVar fields with Meta need Annotated only when use_annotated is True.
        ClassVar fields without Meta don't need Annotated.
        """
        return self._meta_call() is not None

    def _get_default_as_struct_model(self) -> _StructConversion | None:
        """Convert default value to Struct model using msgspec convert."""
        if self._uses_empty_builtin_container_factory():
            return None

        data_types = self.data_type.data_types or (self.data_type,)
        if self.data_type.is_list and (item_type := data_types[0]).is_list:
            while item_type.is_list and len(item_type.data_types) == 1:
                item_type = item_type.data_types[0]
            if item_type.reference and isinstance(item_type.reference.source, Struct):
                data_types = (item_type,)

        for data_type in data_types:
            # TODO: Check nested data_types
            if data_type.is_dict:
                # TODO: Parse dict model for default
                continue
            if data_type.is_list and len(data_type.data_types) == 1:
                data_type_child = data_type.data_types[0]
                if (
                    data_type_child.reference
                    and (isinstance(data_type_child.reference.source, (Struct, TypeAliasBase)))
                    and isinstance(self.default, list)
                ):
                    return _StructConversion(self._PARSE_METHOD, self.default, data_type_child, of_list=True)
            elif data_type.reference and (
                isinstance(data_type.reference.source, Struct)
                or (
                    isinstance(data_type.reference.source, TypeAliasBase)
                    and self._type_alias_needs_struct_conversion(data_type.reference.source)
                )
            ):
                if self.data_type.is_union:
                    if not isinstance(self.default, (dict, list)):
                        continue
                    if isinstance(self.default, dict) and any(dt.is_dict for dt in self.data_type.data_types):
                        continue
                return _StructConversion(
                    self._PARSE_METHOD, self.default, data_type, self.data_type if self.data_type.is_list else None
                )
        return None

    def _type_alias_needs_struct_conversion(self, source: TypeAliasBase) -> bool:
        """Follow alias targets without changing direct defaults or empty factories."""
        if (
            not source.fields
            or not isinstance(self.default, (dict, list))
            or (isinstance(self.default, list) and not self.default)
        ):
            return False

        pending = [source.fields[0].data_type]
        visited = {id(source)}
        has_struct = False
        while pending:
            data_type = pending.pop()
            if data_type.is_dict:
                # Mapping defaults remain outside the existing Struct conversion support.
                return False
            if data_type.reference:
                referenced_model = data_type.reference.source
                if isinstance(referenced_model, Struct):
                    has_struct = True
                elif (
                    isinstance(referenced_model, TypeAliasBase)
                    and referenced_model.fields
                    and id(referenced_model) not in visited
                ):
                    visited.add(id(referenced_model))
                    pending.append(referenced_model.fields[0].data_type)
            else:
                pending.extend(data_type.data_types)
        return has_struct

    def _uses_empty_builtin_container_factory(self) -> bool:
        """Return whether an empty collection can use its zero-cost builtin factory."""
        if self.default:
            return False

        match self.default:
            case list():
                container_attribute = "is_list"
            case dict():
                container_attribute = "is_dict"
            case set():
                container_attribute = "is_set"
            case _:
                return False

        if getattr(self.data_type, container_attribute):
            return True
        return any(getattr(data_type, container_attribute) for data_type in self.data_type.data_types)

    def _get_default_factory_for_optional_nested_model(self) -> _NestedModelFactory | None:
        """Get default_factory for optional nested Struct model fields.

        Returns the factory of the nested model if the field type references a Struct, otherwise None.
        """
        return _nested_model_factory(self, Struct)


class DataTypeManager(_DataTypeManager):
    """Type manager for msgspec Struct models."""

    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = True
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = _ANNOTATED_CONSTRAINTS_CONTEXT
    CONSTRAINT_KEYWORDS: ClassVar[Mapping[str, frozenset[str]]] = _applicability.MSGSPEC_OUTPUT.type_keywords
    ANNOTATION_UNIONS_TAKE_CONSTRAINTS: ClassVar[bool] = _applicability.MSGSPEC_OUTPUT.annotation_unions_take
    FIXED_TUPLES_TAKE_LENGTH: ClassVar[bool] = _applicability.MSGSPEC_OUTPUT.fixed_tuples_take_length
    LITERALS_TAKE_CONSTRAINTS: ClassVar[bool] = _applicability.MSGSPEC_OUTPUT.literals_take


_rebuild_model_with_datamodel_namespace(Constraints)
_rebuild_model_with_datamodel_namespace(DataModelField)
