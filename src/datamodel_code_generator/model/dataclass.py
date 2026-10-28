"""Python dataclass model generator.

Generates Python dataclasses using the @dataclass decorator.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar, Optional, cast

from typing_extensions import final, override

from datamodel_code_generator._format_types import (
    DateClassType,
    DatetimeClassType,
    PythonVersion,
    PythonVersionMin,
)
from datamodel_code_generator._records import record
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot, graph_answer, walk_graph
from datamodel_code_generator.model import DataModel, DataModelFieldBase, _rebuild_model_with_datamodel_namespace
from datamodel_code_generator.model._constraints import Constraints  # noqa: TC001 # needed for pydantic
from datamodel_code_generator.model._decisions.assignment import (
    DataclassItemInputs,
    DataclassWritten,
    dataclass_field_items,
    participates_in_constructor,
    written_data,
)
from datamodel_code_generator.model._render_plan import (
    CallArguments,
    CallPlan,
    FieldDefault,
    FieldRenderPlan,
    RuleId,
    Why,
    field_call_text,
    written_argument_names,
    written_text,
)
from datamodel_code_generator.model.base import (
    UNDEFINED,
    _classify_field_assignment,
    _default_factory_type_name,
    _has_field_assignment,
    _model_key_of_model,
    _nested_model_factory,
    _NestedModelFactory,
    _ValueFactory,
    get_effective_fields,
)
from datamodel_code_generator.model.imports import IMPORT_DATACLASS, IMPORT_FIELD
from datamodel_code_generator.model.types import DataTypeManager as _DataTypeManager
from datamodel_code_generator.python_literal import represent_python_value
from datamodel_code_generator.types import DataType, StrictTypes, chain_as_tuple

if TYPE_CHECKING:
    from collections import defaultdict
    from collections.abc import Collection, Iterator, Sequence
    from pathlib import Path

    from datamodel_code_generator.enums import DataclassArguments
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.model._decisions.assignment import AssignmentRow, DataclassItems, WrittenData
    from datamodel_code_generator.model._render_plan import Omitted
    from datamodel_code_generator.reference import Reference


def has_field_assignment(field: DataModelFieldBase) -> bool:
    """Check if a dataclass field renders with an assignment or default value."""
    return _has_field_assignment(field)


def get_field_default_info(field: DataModelFieldBase) -> tuple[bool, bool]:
    """Return Python dataclass constructor-default semantics."""
    return field._get_constructor_default_info()  # noqa: SLF001  # output-owned field policy hook


def field_participates_in_constructor(field: DataModelFieldBase) -> bool:
    """Return whether a Python dataclass field participates in __init__."""
    return participates_in_constructor(
        "dataclass", init_excluded=field.extras.get("init") is False, class_var=field.is_class_var
    )


def _construction_order_key(field: DataModelFieldBase) -> bool:
    """Return whether the class body writes an assignment for the field, as ``has_field_assignment`` answers.

    The field's backend answers from the ``field()`` arguments it decides to write, without building its plan.
    """
    return _classify_field_assignment(
        field,
        "model",
        field._construction_written_assignment(),  # noqa: SLF001
    ).assigned


def _has_constructor_default(field: DataModelFieldBase) -> bool:
    """Classify a constructor default without recursively rendering structured values."""
    if field.required and not field.use_default_with_required:
        return False
    if field.default is not UNDEFINED and field.default is not None:
        return True
    return get_field_default_info(field)[0]


def _nested_dataclass_sources(data_type: DataType) -> tuple[DataClass, ...]:
    """Return dataclass models referenced by one non-mapping annotation branch."""
    sources: list[DataClass] = []
    for nested_data_type in data_type.data_types or (data_type,):
        if (
            nested_data_type.reference
            and isinstance(source := nested_data_type.reference.source, DataClass)
            and all(source is not existing_source for existing_source in sources)
        ):
            sources.append(source)
    return tuple(sources)


def _iter_nested_mapping_default_sources(model: DataClass) -> Iterator[DataClass]:
    """Yield dataclass models that the mapping defaults of model's constructor fields materialize."""
    effective_fields = model.fields if not model.base_classes else get_effective_fields(model)
    for field in effective_fields:
        if not field_participates_in_constructor(field) or not isinstance(field.default, dict):
            continue
        data_types = field.data_type.data_types or (field.data_type,)
        if (
            field.data_type.is_dict
            or field.data_type.is_mapping
            or any(data_type.is_dict or data_type.is_mapping for data_type in data_types)
        ):
            continue
        yield from _nested_dataclass_sources(field.data_type)


def _walks_to_repeated_mapping_default_path(model: DataClass, active_paths: frozenset[str]) -> bool:
    """Walk every mapping-default edge path from model; exact when distinct models share a path."""
    if model.path in active_paths:
        return True
    active_paths |= {model.path}
    return any(
        _walks_to_repeated_mapping_default_path(source, active_paths)
        for source in _iter_nested_mapping_default_sources(model)
    )


def _has_recursive_nested_mapping_default(model: DataClass) -> bool:
    """Return whether materializing model defaults would recurse through mapping factories.

    True when a mapping-default edge path from model reaches a model path already on it: when a walk through the
    snapshot of the models that mapping defaults materialize meets two models of one path, or one model twice.
    """
    walk = walk_graph(
        GraphSnapshot.MAPPING_DEFAULTS,
        EdgeKind.MAPPING_DEFAULT,
        (model,),
        _model_key_of_model,
        _iter_nested_mapping_default_sources,
    )
    return graph_answer(
        GraphSnapshot.MAPPING_DEFAULTS,
        walk.graph.meets_a_group_twice(walk.node_of, lambda key: walk.node_of[key].path, (EdgeKind.MAPPING_DEFAULT,)),
        lambda: _walks_to_recursive_nested_mapping_default(model),
    )


def _walks_to_recursive_nested_mapping_default(model: DataClass) -> bool:
    """Return whether a mapping-default edge path from model reaches a model path already on it, by walking the paths.

    One depth-first search expands each model path once. A path shared by two models falls back to walking every edge
    path.
    """
    owners = {model.path: model}
    active_paths = {model.path}
    finished_paths: set[str] = set()
    stack = [(model.path, _iter_nested_mapping_default_sources(model))]
    while stack:
        path, sources = stack[-1]
        if (source := next(sources, None)) is None:
            stack.pop()
            active_paths.remove(path)
            finished_paths.add(path)
        elif (source_path := source.path) in active_paths:
            return True
        elif owners.setdefault(source_path, source) is not source:
            return _walks_to_repeated_mapping_default_path(model, frozenset())
        elif source_path not in finished_paths:
            active_paths.add(source_path)
            stack.append((source_path, _iter_nested_mapping_default_sources(source)))
    return False


@final
@record(eq=False)
class _NestedDataclassFactory:
    """The default factory that builds a nested dataclass from a mapping default: ``lambda: Model(a=1)``.

    Its text names the model as the module finally names it.
    """

    data_type: DataType
    arguments: tuple[str, ...]

    @override
    def __str__(self) -> str:
        """Return the factory as the field's call writes it."""
        return f"lambda: {_default_factory_type_name(self.data_type)}({', '.join(self.arguments)})"


def _build_nested_dataclass_default_factory(
    data_type: DataType, default: dict[Any, Any]
) -> _NestedDataclassFactory | None:
    """Build one valid dataclass constructor factory candidate."""
    if not (data_type.reference and isinstance(source := data_type.reference.source, DataClass)):
        return None
    effective_fields = source.fields if not source.base_classes else get_effective_fields(source)
    field_names: dict[str, str] = {}
    original_names: dict[str, str] | None = None
    required_names: set[str] = set()
    has_nested_mapping_default = False
    for model_field in effective_fields:
        if model_field.name is None or not field_participates_in_constructor(model_field):
            continue
        field_names[model_field.name] = model_field.name
        if model_field.original_name is not None:
            if original_names is None:
                original_names = {}
            original_names[model_field.original_name] = model_field.name
        if not _has_constructor_default(model_field):
            required_names.add(model_field.name)
        has_nested_mapping_default = has_nested_mapping_default or (
            isinstance(model_field.default, dict) and bool(_nested_dataclass_sources(model_field.data_type))
        )
    arguments: list[str] = []
    used_field_names: set[str] | None = set() if len(default) > 1 else None
    for name, value in default.items():
        field_name = original_names.get(name) if original_names is not None else None
        if field_name is None and (field_name := field_names.get(name)) is None:
            return None
        if used_field_names is not None:
            if field_name in used_field_names:
                return None
            used_field_names.add(field_name)
        required_names.discard(field_name)
        arguments.append(f"{field_name}={represent_python_value(value)}")
    if required_names:
        return None
    if has_nested_mapping_default and _has_recursive_nested_mapping_default(source):
        return None
    return _NestedDataclassFactory(data_type, tuple(arguments))


_PLAIN_ANNOTATION = Why(RuleId("D11.annotation.plain"))
_FIELD_CALL = Why(RuleId("D11.dataclass.field-call"))
"""The field writes ``field(...)``: an argument, a default factory, or a forced assignment needs one."""
_FORCED_FIELD_CALL = Why(RuleId("D4.default.forced-required-assignment"))
"""A required field that a dataclass base's defaults would follow writes ``field()``, so it stays required."""
_FIELD_ARGUMENT = RuleId("D11.dataclass.field-argument")
_DEFAULT = Why(RuleId("D4.default.value"), keyword="default")
_BARE_DEFAULT = Why(RuleId("D4.default.bare"), keyword="default")
"""The field writes its default alone after the annotation."""
_KEPT_NONE_DEFAULT = Why(RuleId("D4.default.none-beside-arguments"), keyword="default")
"""A ``None`` default goes into a ``field(...)`` other arguments need, unless ``--strip-default-none`` strips it."""
_REQUIRED = Why(RuleId("D4.default.required"), keyword="required")
_NESTED_MODEL_FACTORY = Why(
    RuleId("D4.default.nested-model-factory"), option="--use-default-factory-for-optional-nested-models"
)
_NESTED_MAPPING_FACTORY = Why(RuleId("D4.default.nested-dataclass-from-mapping"), keyword="default")
_MUTABLE_FACTORY = Why(RuleId("D4.default.mutable-factory"), keyword="default")
_UNBOUND_FACTORY = Why(RuleId("D4.default.unbound-field-factory"))
"""A field that hides a type its class names writes its default through a factory, so ``dataclasses`` unbinds it."""
_REQUIRED_INHERITED_INIT_KEY = "_required_inherited_init"
_UNBOUND_FIELD_KEY = "_unbound_field"
_PSEUDO_FIELD_TYPE = re.compile(r"(?:\w+\.)*(?:ClassVar|InitVar)\b")


class _DataclassReuseMixin:
    def has_keyword_only_definition(self) -> bool:
        """Return whether a dataclass declaration enables keyword-only fields."""
        return bool(cast("DataModel", self).dataclass_arguments.get("kw_only"))

    def _derived_options(self) -> dict[str, Any]:
        """Return the constructor options a model built from this one carries over, frozen and its arguments too."""
        model = cast("DataModel", self)
        return {
            **DataModel._derived_options(model),  # noqa: SLF001
            "frozen": model.frozen,
            "dataclass_arguments": model.dataclass_arguments,
        }


class DataClass(_DataclassReuseMixin, DataModel):
    """DataModel implementation for Python dataclasses."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "dataclass.jinja2"
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_DATACLASS,)
    RESOLVES_STRING_ANNOTATIONS_FROM_MODULE: ClassVar[bool] = True
    FIELD_DEFAULT_CLASSIFIER = staticmethod(get_field_default_info)
    FIELD_PARTICIPATES_IN_CONSTRUCTOR = staticmethod(field_participates_in_constructor)
    FIELD_CONSTRUCTION_ORDER_KEY = staticmethod(_construction_order_key)
    _ASSIGNMENT_ROW: ClassVar[AssignmentRow | None] = "dataclass"
    _FREEZES_FIELD_RENDER_PLANS: ClassVar[bool] = True
    SUPPORTS_TREE_SCOPE_REUSE_MODEL_INHERITANCE: ClassVar[bool] = True
    USES_DATACLASS_ARGUMENTS: ClassVar[bool] = True
    SUPPORTS_REQUIRED_INHERITED_FIELD_ASSIGNMENT: ClassVar[bool] = True
    SUPPORTS_DISCRIMINATOR: ClassVar[bool] = True
    SUPPORTS_INHERITED_DISCRIMINATOR_ENUM: ClassVar[bool] = True
    SUPPORTS_KW_ONLY: ClassVar[bool] = True

    @override
    def field_name_hides_types(self, field: DataModelFieldBase) -> bool:
        """Return whether the class body binds the field's name, which its annotation is evaluated after."""
        return self.field_binds_class_attribute(field)

    @override
    def unbind_fields(self, names: Collection[str | None]) -> bool:
        """Write the defaults of the fields of these names through ``default_factory``.

        ``dataclasses`` then removes their class attributes, so a resolver that looks the class up before the module,
        like Pydantic or a native deferred annotation, finds the type of that name instead of the default. A
        ``ClassVar`` or ``InitVar`` takes no default factory and stays bound.
        """
        fields = [
            field
            for field in self.fields
            if field.name in names
            and not any(_PSEUDO_FIELD_TYPE.match(data_type.type or "") for data_type in field.data_type.all_data_types)
        ]
        for field in fields:
            field.__dict__[_UNBOUND_FIELD_KEY] = True
        return bool(fields)

    @override
    def resolves_hidden_types_from_strings(self) -> bool:
        """Return whether the class needs string annotations to resolve a type that a field hides.

        ``dataclasses`` evaluates native deferred annotations while the class still binds the field's name. A class
        with slots is built anew once the name is unbound, and a string would find the slot of that name instead.
        """
        return not self.dataclass_arguments.get("slots")

    @classmethod
    @override
    def prepare_required_inherited_field(
        cls,
        field: DataModelFieldBase,
        inherited_field: DataModelFieldBase,
        *,
        explicit_extras: Collection[str] = (),
    ) -> None:
        """Preserve inherited init=False until requiredness is final."""
        super().prepare_required_inherited_field(
            field,
            inherited_field,
            explicit_extras=explicit_extras,
        )
        if inherited_field.extras.get("init") is False and "init" not in explicit_extras:
            field.__dict__[_REQUIRED_INHERITED_INIT_KEY] = True

    @classmethod
    @override
    def restore_required_inherited_field_state(cls, field: DataModelFieldBase) -> bool:
        """Restore a required field excluded from the inherited constructor."""
        if not (field.required and field.__dict__.pop(_REQUIRED_INHERITED_INIT_KEY, False)):
            return False
        return field.extras.pop("init", None) is False

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
        frozen: bool = False,
        treat_dot_as_module: bool | None = None,
        dataclass_arguments: DataclassArguments | None = None,
    ) -> None:
        """Initialize dataclass with fields sorted by field assignment requirement."""
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
            frozen=frozen,
            treat_dot_as_module=treat_dot_as_module,
        )
        self.dataclass_arguments = self.resolve_dataclass_arguments(
            dataclass_arguments, frozen=frozen, keyword_only=keyword_only
        )
        self._set_deprecated_decorator()


class DataModelField(DataModelFieldBase):
    """Field implementation for dataclass models."""

    _FIELD_ASSIGNMENT_ROW: ClassVar[AssignmentRow] = "dataclass"

    _FIELD_KEYS: ClassVar[set[str]] = {
        "default_factory",
        "init",
        "repr",
        "hash",
        "compare",
        "metadata",
        "kw_only",
    }
    constraints: Optional[Constraints] = None  # noqa: UP045

    @override
    def process_const(self) -> None:
        """Process const field constraint using literal type."""
        self._process_const_as_literal()

    @property
    @override
    def imports(self) -> tuple[Import, ...]:
        """Get imports including field() when the field writes ``field(...)``, as its ``field`` property gives it."""
        if (field := self.field) and field.startswith("field("):
            return chain_as_tuple(super().imports, (IMPORT_FIELD,))
        return super().imports

    @property
    @override
    def field(self) -> str | None:
        """For backwards compatibility."""
        result = str(self)
        if not result:
            return None
        return result

    def _get_default_factory_for_nested_model(self) -> _NestedModelFactory | None:
        """Get default_factory for nested dataclass model fields.

        Returns the factory of the nested model if the field type references a DataClass, otherwise None.
        """
        return _nested_model_factory(self, DataClass)

    def _get_default_factory_for_nested_value(self, default: dict[Any, Any]) -> _NestedDataclassFactory | None:
        """Return the factory that builds a nested dataclass from a mapping default, if exactly one can."""
        if self.data_type.is_dict or self.data_type.is_mapping:
            return None

        data_types = self.data_type.data_types or (self.data_type,)
        if any(data_type.is_dict or data_type.is_mapping for data_type in data_types):
            return None

        default_factory: _NestedDataclassFactory | None = None
        for data_type in data_types:
            if (factory := _build_nested_dataclass_default_factory(data_type, default)) is None:
                continue
            if default_factory is not None:
                return None
            default_factory = factory
        return default_factory

    def _nested_model_factory_argument(self) -> _NestedModelFactory | None:
        """Return the nested model's factory the field writes: optional, no declared factory, a factory found."""
        if self._takes_nested_model_factory() and "default_factory" not in self.extras:
            return self._get_default_factory_for_nested_model()
        return None

    def _field_items(self, nested: _NestedModelFactory | None) -> DataclassItems:
        """Return which ``field()`` arguments the field writes (``dataclass_field_items``) with ``nested`` found."""
        default = self.default
        return dataclass_field_items(
            DataclassItemInputs(
                extra_keys=tuple(key for key in self.extras if key in self._FIELD_KEYS),
                init_excluded=self.extras.get("init") is False,
                nested_model_factory=nested is not None,
                default="none" if default is None else "value" if default != UNDEFINED else "undefined",
                container_default=isinstance(default, (list, dict, set)),
                strip_default_none=self.strip_default_none,
                required=self.required and not self.use_default_with_required,
                unbound=bool(self.__dict__.get(_UNBOUND_FIELD_KEY)),
            )
        )

    def _field_arguments(self, omitted: list[Omitted]) -> CallArguments:
        """Build the ``field(...)`` arguments the field writes (``_field_items``), in order, with their values.

        The item left after the others is the factory of a field that ``dataclasses`` unbinds.
        """
        nested = self._nested_model_factory_argument()
        items = self._field_items(nested)
        args = CallArguments(omitted)
        for key in items.dropped:
            args.omit(f"extra:{key}", self.extras[key], "factory-not-applicable", _REQUIRED)
        for name, item in items.written:
            match item:
                case "extra":
                    args.put(name, self.extras[name], Why(_FIELD_ARGUMENT, name), (f"extra:{name}",))
                case "nested-model-factory":
                    args.put(name, nested, _NESTED_MODEL_FACTORY)
                case "kept-none-default":
                    args.put(name, None, _KEPT_NONE_DEFAULT)
                case "default":
                    args.put(name, self.default, _DEFAULT)
                case "value-factory":
                    if isinstance(self.default, dict) and (
                        nested_factory := self._get_default_factory_for_nested_value(self.default)
                    ):
                        args.put(name, nested_factory, _NESTED_MAPPING_FACTORY)
                    else:
                        args.put(name, _ValueFactory(self.default), _MUTABLE_FACTORY)
                case _:
                    args.put(name, _ValueFactory(self.default, always_lambda=True), _UNBOUND_FACTORY)
        return args

    @override
    def _construction_written_assignment(self) -> DataclassWritten:
        """Return the ``field()`` arguments the field decides to write, without building its plan, and a forced call.

        A plan frozen at B2 writes the same arguments while the facts it read hold, as ``RenderReady`` rebuilds it.
        """
        return self._written(written_data(self._field_items(self._nested_model_factory_argument()).names()))

    @override
    def _build_frozen_render_plan(self) -> FieldRenderPlan:
        return self._render_plan()

    @override
    def _build_render_plan(self) -> FieldRenderPlan:
        """Build the plan: ``field(...)`` with its arguments, the default alone, or nothing after the annotation."""
        args = self._field_arguments([])
        kwargs = args.kwargs()
        call: CallPlan | None = None
        default = FieldDefault(self.default, missing_sentinel=False, why=_DEFAULT)
        bare_default = False
        if not kwargs:
            if self._has_forced_field_assignment:
                call = CallPlan(None, None, (), default_keyword=False, why=_FORCED_FIELD_CALL, callee="field")
        elif len(kwargs) == 1 and kwargs[0].name == "default":
            default = FieldDefault(kwargs[0].value, missing_sentinel=False, why=_BARE_DEFAULT, items=kwargs[0].items)
            bare_default = True
        else:
            call = CallPlan(None, None, kwargs, default_keyword=False, why=_FIELD_CALL, callee="field")
        return FieldRenderPlan(
            annotation_form="plain",
            call=call,
            default=default,
            has_default_factory_in_field="default_factory" in args,
            omitted=tuple(args.omitted),
            symbol_imports=() if call is None else ((IMPORT_FIELD, call.why),),
            why=_PLAIN_ANNOTATION,
            bare_default=bare_default,
        )

    def _plan_text(self, plan: FieldRenderPlan) -> str:  # noqa: PLR6301
        """Write what follows the annotation from ``plan``: ``field(...)``, the default alone, or nothing."""
        if (call := plan.call) is not None:
            return field_call_text(call)
        return represent_python_value(plan.default.value) if plan.bare_default else ""

    def _render_plan_text(self, plan: FieldRenderPlan) -> tuple[object, ...]:
        """Return what the field writes from ``plan``, to compare a frozen plan with a rebuilt one."""
        return (
            self._plan_text(plan),
            tuple(import_ for import_, _ in plan.symbol_imports),
            tuple((omitted.item, omitted.reason) for omitted in plan.omitted),
        )

    def _declared_render_items(self) -> frozenset[str]:
        """Return the items the field declares that its plan must write or omit: its ``field()`` arguments."""
        return frozenset(f"extra:{key}" for key in self.extras if key in self._FIELD_KEYS)

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
    def _written_assignment(self) -> DataclassWritten:
        """Return the ``field()`` arguments the field writes and whether it forces a ``field()``."""
        return self._written(written_data(written_argument_names(self._render_plan())))

    def _written(self, data: WrittenData) -> DataclassWritten:
        """Return the written assignment of the ``field()`` arguments ``data``, with what the field's call renders."""
        forced = self._has_forced_field_assignment
        return DataclassWritten(data=data, forced=forced, text=written_text(self, writes=data.any or forced))

    @override
    def __str__(self) -> str:
        """Generate field() call or default value representation."""
        return self._plan_text(self._render_plan())


class DataTypeManager(_DataTypeManager):
    """Type manager for dataclass models."""

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        python_version: PythonVersion = PythonVersionMin,
        use_standard_collections: bool = False,  # noqa: FBT001, FBT002
        use_generic_container_types: bool = False,  # noqa: FBT001, FBT002
        strict_types: Sequence[StrictTypes] | None = None,
        use_non_positive_negative_number_constrained_types: bool = False,  # noqa: FBT001, FBT002
        use_decimal_for_multiple_of: bool = False,  # noqa: FBT001, FBT002
        use_union_operator: bool = False,  # noqa: FBT001, FBT002
        use_pendulum: bool = False,  # noqa: FBT001, FBT002
        use_standard_primitive_types: bool = False,  # noqa: FBT001, FBT002
        use_object_type: bool = False,  # noqa: FBT001, FBT002
        target_datetime_class: DatetimeClassType = DatetimeClassType.Datetime,
        target_date_class: DateClassType | None = None,
        treat_dot_as_module: bool | None = None,  # noqa: FBT001
        use_serialize_as_any: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        """Initialize type manager with dataclass datetime defaults."""
        super().__init__(
            python_version=python_version,
            use_standard_collections=use_standard_collections,
            use_generic_container_types=use_generic_container_types,
            strict_types=strict_types,
            use_non_positive_negative_number_constrained_types=use_non_positive_negative_number_constrained_types,
            use_decimal_for_multiple_of=use_decimal_for_multiple_of,
            use_union_operator=use_union_operator,
            use_pendulum=use_pendulum,
            use_standard_primitive_types=use_standard_primitive_types,
            use_object_type=use_object_type,
            target_datetime_class=target_datetime_class,
            target_date_class=target_date_class,
            treat_dot_as_module=treat_dot_as_module,
            use_serialize_as_any=use_serialize_as_any,
        )


_rebuild_model_with_datamodel_namespace(DataModelField)
