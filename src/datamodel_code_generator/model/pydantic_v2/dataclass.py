"""Pydantic v2 dataclass model generator.

Generates pydantic.dataclasses.dataclass decorated classes with validation support.
"""

from __future__ import annotations

import keyword
from dataclasses import replace
from typing import TYPE_CHECKING, Any, ClassVar

from typing_extensions import override

from datamodel_code_generator._target_unicode import is_identifier
from datamodel_code_generator.imports import IMPORT_ANNOTATED
from datamodel_code_generator.model import DataModel, DataModelFieldBase, _rebuild_model_with_datamodel_namespace
from datamodel_code_generator.model import dataclass as _dataclass_module
from datamodel_code_generator.model._decisions.assignment import (
    PydanticDataclassWritten,
    participates_in_constructor,
)
from datamodel_code_generator.model._render_plan import CallPlan, Kwarg, Positional, RuleId, Why
from datamodel_code_generator.model.base import (
    UNDEFINED,
    _classify_field_assignment,
    _find_base_classes,
    _nested_model_factory,
    _NestedModelFactory,
    _reference_names_model,
)
from datamodel_code_generator.model.dataclass import _DataclassReuseMixin
from datamodel_code_generator.model.pydantic_v2._config import (
    ConfigAttribute,
    build_base_config_parameters,
)
from datamodel_code_generator.model.pydantic_v2._output_context import (
    ANNOTATED_CONSTRAINTS_CONTEXT as _ANNOTATED_CONSTRAINTS_CONTEXT,
)
from datamodel_code_generator.model.pydantic_v2.base_model import (
    _ALIAS_GENERATOR_TEMPLATE_DATA_KEY,
    _config_constructs,
    _config_dict_items,
    _config_keys_without_requirement,
    _import_rendered_alias_generator,
    _model_references,
    _template_config_items,
    assigns_forward_reference_field,
    has_lookaround_pattern,
    models_reaching_string_pattern_aliases,
)
from datamodel_code_generator.model.pydantic_v2.base_model import (
    Constraints as _Constraints,
)
from datamodel_code_generator.model.pydantic_v2.base_model import (
    DataModelField as DataModelFieldV2,
)
from datamodel_code_generator.model.pydantic_v2.imports import (
    IMPORT_CONFIG_DICT,
    IMPORT_PYDANTIC_DATACLASS,
)
from datamodel_code_generator.model.pydantic_v2.version import _includes_dict_key_reference_classes, model_target_meets
from datamodel_code_generator.python_literal import represent_python_value

has_field_assignment = _dataclass_module.has_field_assignment
_MUTABLE_DEFAULT_FACTORY = Why(RuleId("D4.default.dataclass-mutable-factory"), keyword="default")
_FORCED_ASSIGNMENT = Why(RuleId("D4.default.forced-required-assignment"))
"""A required field that a dataclass base's defaults would follow writes ``Field(...)``, so it stays required."""
_VISIBLE_METADATA = Why(RuleId("D11.pydantic-dataclass.visible-metadata"), option="--use-annotated")
"""The dataclass must see the call's metadata: the call follows a plain annotation instead of going into it."""
_SAFE_CONFIG_ITEMS_TEMPLATE_DATA_KEY = "_safe_config_items"


def _has_pydantic_dataclass_field_assignment(field: DataModelFieldBase) -> bool:
    """Return whether ``dataclass.jinja2`` writes an assignment for the field.

    A ``None`` default that ``--strip-default-none`` strips stays when the rendered annotation admits None.
    """
    return _classify_field_assignment(field, "pydantic-dataclass").assigned


def _construction_order_key(field: DataModelFieldBase) -> bool:
    """Return whether the class body gives the field a default whatever its rendered type turns out to be.

    A ``None`` default that ``--strip-default-none`` keeps only for a type that admits None counts as no default
    here, as the type is final later; constructor ordering then puts such a field after the fields without one.
    """
    return _classify_field_assignment(field, "pydantic-dataclass").construction_assigned


def _get_pydantic_dataclass_field_default_info(field: DataModelFieldBase) -> tuple[bool, bool]:
    """Return Pydantic dataclass constructor-default semantics."""
    return field._get_constructor_default_info()  # noqa: SLF001  # output-owned field policy hook


def _pydantic_dataclass_field_participates_in_constructor(field: DataModelFieldBase) -> bool:
    """Return whether a Pydantic dataclass field participates in __init__."""
    return participates_in_constructor(
        "pydantic-dataclass", init_excluded=field.extras.get("init") is False, class_var=field.is_class_var
    )


if TYPE_CHECKING:
    from collections import defaultdict
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from datamodel_code_generator import DataclassArguments
    from datamodel_code_generator._requirements import PydanticConstruct
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.model._decisions.assignment import AssignmentRow
    from datamodel_code_generator.model._render_plan import FieldRenderPlan
    from datamodel_code_generator.model.pydantic_base import _FieldArguments, _PydanticFieldRenderPlan
    from datamodel_code_generator.reference import Reference

Constraints = _Constraints


class DataClass(_DataclassReuseMixin, DataModel):
    """DataModel implementation for Pydantic v2 dataclasses."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "pydantic_v2/dataclass.jinja2"
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_PYDANTIC_DATACLASS,)
    SUPPORTS_FIELD_RENAMING: ClassVar[bool] = True
    SUPPORTS_FIELD_TYPE_RENAMING: ClassVar[bool] = True
    FIELD_ASSIGNMENT_CHECKER = staticmethod(_has_pydantic_dataclass_field_assignment)
    FIELD_DEFAULT_CLASSIFIER = staticmethod(_get_pydantic_dataclass_field_default_info)
    FIELD_PARTICIPATES_IN_CONSTRUCTOR = staticmethod(_pydantic_dataclass_field_participates_in_constructor)
    FIELD_CONSTRUCTION_ORDER_KEY = staticmethod(_construction_order_key)
    _ASSIGNMENT_ROW: ClassVar[AssignmentRow | None] = "pydantic-dataclass"
    _FREEZES_FIELD_RENDER_PLANS: ClassVar[bool] = True
    USES_DATACLASS_ARGUMENTS: ClassVar[bool] = True
    SUPPORTS_REQUIRED_INHERITED_FIELD_ASSIGNMENT: ClassVar[bool] = True
    REQUIRES_EXPLICIT_INHERITED_FACTORY_OVERRIDE: ClassVar[bool] = True
    REQUIRED_ASSIGNMENT_COUNTS_AS_CONSTRUCTOR_DEFAULT: ClassVar[bool] = True
    REQUIRES_RUNTIME_IMPORTS_WITH_RUFF_CHECK: ClassVar[bool] = True
    SUPPORTS_DISCRIMINATOR: ClassVar[bool] = True
    SUPPORTS_KW_ONLY: ClassVar[bool] = True
    _READ_BY_PYDANTIC: ClassVar[bool] = True
    _CHECKS_CONSTRAINTS: ClassVar[bool] = True
    _CONFIG_KEYS_WITHOUT_REQUIREMENT = staticmethod(_config_keys_without_requirement)
    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = True
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = _ANNOTATED_CONSTRAINTS_CONTEXT
    _INCLUDE_DICT_KEY_REFERENCE_CLASSES = staticmethod(_includes_dict_key_reference_classes)
    # frozen/allow_mutation are handled as dataclass decorator arguments, not ConfigDict
    _CONFIG_ATTRIBUTES_V2: ClassVar[list[ConfigAttribute]] = [
        ConfigAttribute("allow_population_by_field_name", "populate_by_name", False),  # noqa: FBT003
        ConfigAttribute("populate_by_name", "populate_by_name", False),  # noqa: FBT003
        ConfigAttribute("use_attribute_docstrings", "use_attribute_docstrings", False),  # noqa: FBT003
    ]
    _CONFIG_ATTRIBUTES_V2_11: ClassVar[list[ConfigAttribute]] = [
        ConfigAttribute("allow_population_by_field_name", "validate_by_name", False),  # noqa: FBT003
        ConfigAttribute("populate_by_name", "validate_by_name", False),  # noqa: FBT003
        ConfigAttribute("use_attribute_docstrings", "use_attribute_docstrings", False),  # noqa: FBT003
    ]

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
        """Initialize pydantic v2 dataclass with sorted fields and ConfigDict support."""
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

        config_parameters = build_base_config_parameters(
            extra_template_data=self.extra_template_data,
            all_data_types=self.all_data_types,
            config_attributes_v2=self._CONFIG_ATTRIBUTES_V2,
            config_attributes_v2_11=self._CONFIG_ATTRIBUTES_V2_11,
        )
        if config_items := _config_dict_items(self.extra_template_data.get("config")):
            config_parameters.update(config_items)

        if config_parameters:
            self._additional_imports.append(IMPORT_CONFIG_DICT)
            self.extra_template_data["config"] = config_parameters
            self._set_internal_template_data(
                _SAFE_CONFIG_ITEMS_TEMPLATE_DATA_KEY, config_items := self._rendered_config_items()
            )
            _import_rendered_alias_generator(self, config_items)

        self._lookaround_regex_engine_checked = False

    @override
    def orders_required_assignments_before_defaults(self) -> bool:
        """Order required ``= Field(...)`` fields first only for an explicit target that needs it.

        Pydantic 2.13+ rejects a required field after a default in ``inspect.signature``. An unset or older
        target keeps the schema order, which positional construction follows on every release.
        """
        return model_target_meets(self, "pydantic.dataclass_signature_order")

    @override
    def keeps_assignment_with_required_fields(self, field: DataModelFieldBase) -> bool:
        """Keep a required field that only its alias makes an assignment before the fields with defaults.

        Targets before Pydantic 2.4 write a non-identifier alias of an ``Annotated`` field as an assignment,
        which the newer targets leave in ``Annotated`` among the required fields.
        """
        return getattr(field, "assigned_only_for_alias", False)

    @override
    def class_attribute_hides_named_type(
        self, field: DataModelFieldBase, *, assigned: bool, resolved_late: bool, as_string: bool
    ) -> bool:
        """Return whether a class attribute named like the field hides the type that its annotation names.

        From a string Pydantic 2.10 resolves the name of the class itself before its attributes, and a field of
        a class with slots stays an attribute once the class exists.
        """
        if (
            as_string
            and any(
                (reference := data_type.reference) is not None
                and _reference_names_model(reference, self, "dataclass own name")
                for data_type in field.data_type.all_data_types
            )
            and model_target_meets(self, "pydantic.dataclass_own_name")
        ):
            return False
        return super().class_attribute_hides_named_type(
            field, assigned=assigned, resolved_late=resolved_late, as_string=as_string
        ) or (resolved_late and bool(self.dataclass_arguments.get("slots")))

    @override
    def assigns_forward_reference_field(
        self, field: DataModelFieldBase, positions: Mapping[str, int], index: int
    ) -> bool:
        """Leave ``Annotated`` for a field that names a later model when the target predates Pydantic 2.11."""
        return assigns_forward_reference_field(self, field, positions, index)

    @override
    def assign_forward_reference_fields(self, positions: Mapping[str, int], index: int) -> bool:
        """Write fields that name a later model by assignment, keeping required fields before defaults."""
        if not super().assign_forward_reference_fields(positions, index):
            return False
        self.fields[:] = self.order_fields_for_construction(self.fields)
        return True

    def _apply_lookaround_regex_engine(self) -> None:
        """Force the python-re regex engine when a lookaround pattern is reachable.

        Runs lazily because referenced patterns are only linked after ``__init__``; the
        result is memoized to keep ``imports`` cheap.
        """
        if self._lookaround_regex_engine_checked:
            return
        self._lookaround_regex_engine_checked = True
        if has_lookaround_pattern(self.fields, follow_references=True):
            self._select_python_regex_engine()

    @classmethod
    @override
    def finalize_type_alias_users(cls, models: list[DataModel]) -> None:
        """Select Python's regex engine in every dataclass that reaches a string pattern type alias.

        A dataclass inherits the config of its first base only and builds the fields of its other bases
        with its own, so the references of its fields and of those bases count.
        """
        if not (reached := models_reaching_string_pattern_aliases(models)):
            return
        for model in models:
            if isinstance(model, cls) and any(
                id(reference.source) in reached for reference in _model_references(model, model.base_classes[1:])
            ):
                model._select_python_regex_engine()  # noqa: SLF001

    def _rendered_config_items(self) -> list[tuple[str, str]]:
        """Return the ``ConfigDict`` items the built-in dataclass template writes; imports and aliases read them."""
        return _template_config_items(self.extra_template_data)

    def _applied_config_items(self, visiting: frozenset[int] = frozenset()) -> dict[str, str]:
        """Return the config items Pydantic applies: the dataclass's own, else all of its first dataclass base's.

        A base on an inheritance cycle, which generation rejects later, adds nothing.
        """
        if own := self._rendered_config_items():
            return dict(own)
        visiting |= {id(self)}
        return next(
            (
                base._applied_config_items(visiting)  # noqa: SLF001
                for base in _find_base_classes(self)
                if isinstance(base, DataClass) and id(base) not in visiting
            ),
            {},
        )

    @override
    def _pydantic_constructs(self) -> Iterator[tuple[PydanticConstruct, str | None]]:
        """Also yield the rows of the keys that the rendered dataclass config writes."""
        yield from super()._pydantic_constructs()
        yield from _config_constructs(self._rendered_config_items())

    def _select_python_regex_engine(self) -> None:
        """Set ``regex_engine`` in the dataclass config."""
        # Merge into any config from __init__; a duplicate ConfigDict import is deduped on render.
        config = self.extra_template_data.setdefault("config", {})
        config["regex_engine"] = '"python-re"'
        self._set_internal_template_data(_SAFE_CONFIG_ITEMS_TEMPLATE_DATA_KEY, self._rendered_config_items())
        self._additional_imports.append(IMPORT_CONFIG_DICT)

    @property
    @override
    def imports(self) -> tuple[Import, ...]:
        """Return model imports, ensuring the lookaround regex engine config is applied."""
        self._apply_lookaround_regex_engine()
        return super().imports

    @override
    def render(self, *, class_name: str | None = None) -> str:
        """Render the dataclass, ensuring the lookaround regex engine config is applied."""
        self._apply_lookaround_regex_engine()
        return super().render(class_name=class_name)


class _PydanticDataclassField(DataModelFieldV2):
    """Adapt Annotated fields to the existing dataclass template contract."""

    _FIELD_ASSIGNMENT_ROW: ClassVar[AssignmentRow] = "pydantic-dataclass"

    _DATACLASS_ASSIGNMENT_KEYS: ClassVar[frozenset[str]] = frozenset({
        "default_factory",
        "init",
        "init_var",
        "kw_only",
        "repr",
    })
    _ALIASES_REQUIRE_ASSIGNMENT: ClassVar[bool] = False

    @override
    def _parent_config_items(self) -> dict[str, str]:
        """Return the config items that Pydantic applies to a parent dataclass, else those of a parent model."""
        if isinstance(parent := self.parent, DataClass):
            return parent._applied_config_items()  # noqa: SLF001
        return super()._parent_config_items()

    @override
    def _alias_generator_name_from_parent(self) -> str | None:
        """Follow only a generator that the dataclass config applies: the template writes no template-data one."""
        if self._pins_aliases() and _ALIAS_GENERATOR_TEMPLATE_DATA_KEY not in self._parent_config_items():
            return None
        return super()._alias_generator_name_from_parent()

    @override
    def _alias_generator_renames(self, name: str) -> bool:
        """Return False without ``--pin-aliases-with-alias-generator``: the dataclass template renders no generator."""
        return self._pins_aliases() and super()._alias_generator_renames(name)

    @override
    def _call_default_factory(self, args: _FieldArguments) -> Kwarg | None:
        """Use dataclass-safe factories for mutable schema defaults."""
        if (factory := super()._call_default_factory(args)) is not None or (
            self.required and not self.use_default_with_required
        ):
            return factory
        kwarg = None
        match self.default:
            case list() | dict() | set() as default:
                kwarg = Kwarg(
                    "default_factory",
                    f"lambda: {represent_python_value(default)}" if default else type(default).__name__,
                    _MUTABLE_DEFAULT_FACTORY,
                )
            case _:
                pass
        return kwarg

    @override
    def _written_assignment(self) -> PydanticDataclassWritten:
        """Return the call the field renders and whether its ``Field()`` writes a default factory."""
        return PydanticDataclassWritten(
            text=bool(self.field), factory=bool(self._get_field_render_plan().default_factory)
        )

    @override
    def _build_render_plan(self) -> FieldRenderPlan:
        """Add the ``Field(...)`` of a forced assignment, and the plain annotation of a call the dataclass must see."""
        plan = super()._build_render_plan()
        if plan.annotation_form == "classvar":
            return plan
        if (call := plan.call) is None:
            if not self._has_forced_field_assignment:
                return plan
            call = CallPlan(
                Positional("required", _FORCED_ASSIGNMENT), None, (), default_keyword=False, why=_FORCED_ASSIGNMENT
            )
            plan = self._field_render_plan(
                call,
                plan.default,
                has_default_factory_in_field=plan.has_default_factory_in_field,
                omitted=plan.omitted,
            )
        if plan.annotation_form == "annotated" and self._requires_dataclass_field_assignment(
            None if call.factory is None else call.factory.value
        ):
            return replace(plan, annotation_form="plain", why=_VISIBLE_METADATA)
        return plan

    def _writes_visible_metadata(self, plan: FieldRenderPlan) -> bool:
        """Return whether ``plan`` writes the call after a plain annotation for the dataclass to see it."""
        return plan.call is not None and plan.annotation_form == "plain" and self.use_annotated

    @property
    def requires_dataclass_field_assignment(self) -> bool:
        """Check whether Annotated metadata must also be visible to dataclasses."""
        return self._requires_dataclass_field_assignment(self._get_field_render_plan().default_factory)

    def _requires_dataclass_field_assignment(self, default_factory: object) -> bool:
        """Return whether the dataclass must see the call that writes ``default_factory`` (None for no factory)."""
        return self._alias_requires_assignment or self._metadata_requires_assignment(default_factory)

    @property
    def _alias_requires_assignment(self) -> bool:
        return self._ALIASES_REQUIRE_ASSIGNMENT and bool(self.validation_aliases or self.serialization_alias)

    def _metadata_requires_assignment(self, default_factory: object) -> bool:
        return bool(
            self._has_forced_field_assignment
            or not self._DATACLASS_ASSIGNMENT_KEYS.isdisjoint(self.extras)
            or default_factory
        )

    @property
    def assigned_only_for_alias(self) -> bool:
        """Return whether a required ``Annotated`` field is an assignment only because of its alias."""
        return (
            self.use_annotated
            and self.required
            and not self.use_default_with_required
            and self._alias_requires_assignment
            and not self._metadata_requires_assignment(self._get_field_render_plan().default_factory)
        )

    @property
    def dataclass_field(self) -> str | None:
        """Render a Field() assignment that preserves Python dataclass defaults."""
        return self._render_dataclass_field(self._get_field_render_plan())

    def _render_dataclass_field(self, plan: _PydanticFieldRenderPlan) -> str | None:
        """Render a dataclass assignment from an already computed plan."""
        if not plan.rendered:
            return None
        if (
            plan.default_factory
            or (self.required and not self.use_default_with_required)
            or self.should_strip_default_none(keep_optional=True)
        ):
            return plan.rendered
        if not self.use_annotated:
            return plan.assignment
        if self.default is UNDEFINED and not self.use_missing_sentinel_default:
            return plan.rendered
        default_argument = f"default={self.represented_default}" if self.use_default_kwarg else self.represented_default
        return self._render_field_call((default_argument, *plan.arguments))

    @property
    @override
    def annotated(self) -> str | None:
        """Keep assignments visible to legacy dataclass templates."""
        if self._render_plan().annotation_form != "annotated":
            return None
        return super().annotated

    @property
    @override
    def field(self) -> str | None:
        """Render dataclass-visible metadata on the assignment when required."""
        if self._writes_visible_metadata(plan := self._render_plan()):
            return self._render_dataclass_field(self._plan_text(plan))
        return super().field

    @override
    def _rendered_field_values(self) -> tuple[str | None, str | None]:
        """Keep built-in rendering consistent with the public field properties."""
        plan = self._render_plan()
        text = self._plan_text(plan)
        if self._writes_visible_metadata(plan):
            return self._render_dataclass_field(text), None
        return self._rendered_field_values_from_plan(text)

    @override
    def _get_default_factory_for_optional_nested_model(self) -> _NestedModelFactory | None:
        """Get a factory for an optional nested Pydantic dataclass."""
        return _nested_model_factory(self, DataClass)

    @property
    @override
    def imports(self) -> tuple[Import, ...]:
        """Drop Annotated when dataclass metadata moves to the assignment.

        Row ``legacy.pdc-annotated-strip``: the import goes whenever the dataclass must see the field's metadata, also
        when the type hint itself writes ``Annotated`` (``NameError`` at import); the module import plan (RP15) keeps
        the imports the rendered text uses and deletes the row.
        """
        imports = super().imports
        if not (
            self.use_annotated
            and self._requires_dataclass_field_assignment(self._get_field_render_plan().default_factory)
        ):
            return imports
        return tuple(import_ for import_ in imports if import_ != IMPORT_ANNOTATED)


class DataModelField(_PydanticDataclassField):
    """Field implementation for Pydantic v2 dataclass models.

    Inherits pydantic v2 Field() constraint handling from DataModelFieldV2.
    """


class DataModelFieldBackport(DataModelField):
    """Dataclass field for targets before Pydantic 2.4, which rejects non-identifier dataclass aliases."""

    _ALIASES_REQUIRE_ASSIGNMENT: ClassVar[bool] = True

    def __init__(self, **data: Any) -> None:
        """Initialize and make non-identifier aliases safe for dataclass signatures."""
        super().__init__(**data)
        if self.alias is None or (
            is_identifier(self.alias, self.data_type.python_version) and not keyword.iskeyword(self.alias)
        ):
            return

        validation_aliases = list(self.validation_aliases or ())
        if self.alias not in validation_aliases:
            validation_aliases.insert(0, self.alias)
        if self.serialization_alias is None:
            self.serialization_alias = self.alias
        self.validation_aliases = validation_aliases
        self.alias = None


_rebuild_model_with_datamodel_namespace(DataModelField)
_rebuild_model_with_datamodel_namespace(DataModelFieldBackport)
