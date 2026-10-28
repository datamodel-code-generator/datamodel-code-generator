"""Shared base classes for Pydantic model implementations.

Provides Constraints, DataModelField, and BaseModelBase used by Pydantic v2 models.
"""

from __future__ import annotations

from abc import ABC
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, Optional, cast

from pydantic import Field as _Field
from typing_extensions import override

from datamodel_code_generator import cached_path_exists
from datamodel_code_generator._constraint_applicability import DROPPED_MULTIPLE_OF_KEY
from datamodel_code_generator.imports import IMPORT_DECIMAL
from datamodel_code_generator.model import (
    UNDEFINED,
    DataModel,
    DataModelFieldBase,
    _rebuild_model_with_datamodel_namespace,
)
from datamodel_code_generator.model import ConstraintsBase as _ConstraintsBase
from datamodel_code_generator.model._constraints import Constraints
from datamodel_code_generator.model._constraints import PatternConstraints as _PatternConstraints
from datamodel_code_generator.model._pydantic_imports import IMPORT_ANYURL, IMPORT_FIELD
from datamodel_code_generator.model._render_plan import CallArguments as _FieldArguments
from datamodel_code_generator.model._render_plan import (
    CallPlan,
    FieldDefault,
    FieldRenderPlan,
    Kwarg,
    Omitted,
    Positional,
    RuleId,
    Why,
    frozen_render_plan,
)
from datamodel_code_generator.model.base import (
    _nested_model_factory,
    _NestedModelFactory,
    _remember_custom_template_dependency,
)
from datamodel_code_generator.python_literal import represent_python_value
from datamodel_code_generator.types import UnionIntFloat as _UnionIntFloat
from datamodel_code_generator.types import (
    _float_constraint,
    _non_finite_as_written,
    chain_as_tuple,
    merge_normalized_constraint,
    normalize_integer_constraint,
)

if TYPE_CHECKING:
    from collections import defaultdict

    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

# Preserve names that were historically importable from this module.
Field = _Field
ConstraintsBase = _ConstraintsBase
PatternConstraints = _PatternConstraints
UnionIntFloat = _UnionIntFloat


def _constraint_arguments(constraints: type[_ConstraintsBase]) -> dict[str, str]:
    """Return the ``Field()`` argument that a constraints model writes each schema keyword as."""
    return {info.alias: name for name, info in constraints.model_fields.items() if info.alias is not None}


_FIELD_CONSTRAINT_ARGUMENTS: dict[str, str] = _constraint_arguments(Constraints)

_FIELD_CALL = Why(RuleId("D11.pydantic.field-call"))
"""The field writes a ``Field()`` call: an argument, a default factory or a required ``None`` needs one."""
_PLAIN_ANNOTATION = Why(RuleId("D11.annotation.plain"))
_ANNOTATED = Why(RuleId("D11.annotation.annotated"), option="--use-annotated")
_CLASS_VAR = Why(RuleId("D11.class-var"), keyword="x-is-classvar")
_DEFAULT = Why(RuleId("D4.default.value"), keyword="default")
_MISSING_DEFAULT = Why(RuleId("D4.default.missing-sentinel"), option="--use-missing-sentinel")
_DEFAULT_ARGUMENT = Why(RuleId("D4.default.field-argument"), keyword="default")
_REQUIRED = Why(RuleId("D4.default.required"), keyword="required")
_REQUIRED_NULLABLE = Why(RuleId("D3.render.required-nullable"), keyword="nullable")
_NONE_VALUE = Why(RuleId("D11.pydantic.none-unset"))
_ALIAS = Why(RuleId("D9.alias.explicit"), keyword="alias")
_STRICT_DECIMAL = Why(RuleId("D2.pydantic.strict-decimal"), keyword="strict")
_USE_FIELD_DESCRIPTION = Why(
    RuleId("D11.description.docstring"), keyword="description", option="--use-field-description"
)
_DECLARED_FACTORY = Why(RuleId("D4.default.declared-factory"), keyword="default_factory")
_NESTED_MODEL_FACTORY = Why(
    RuleId("D4.default.nested-model-factory"), option="--use-default-factory-for-optional-nested-models"
)
_FACTORY_NOT_APPLICABLE = Why(RuleId("D4.default.factory-of-required-or-valued"), keyword="default_factory")
_UNTAKEN = RuleId("D2.untaken-by-type")
_OVERRIDE = Why(RuleId("D2.type-override"), option="--type-overrides")
_ANYURL = RuleId("legacy-anyurl-keeps-string-keywords")
_CONSTRAINT = RuleId("D2.pydantic.constraint")
_NONE_TYPE = RuleId("D2.pydantic.none-type-number")
_MULTIPLE_OF_ONE = RuleId("D2.pydantic.integer-multiple-of-one")
_NON_FINITE_MULTIPLE_OF = RuleId("D2.pydantic.non-finite-multiple-of")
"""Pydantic takes ``multiple_of`` only as a finite number (``_non_finite_as_written``)."""
_EXTRA = RuleId("D11.pydantic.field-extra")
_DISCRIMINATOR = RuleId("D7.tag.discriminator")


class _PydanticFieldRenderPlan(NamedTuple):
    """Text of a field's ``Field()`` call, written from its render plan, for the views that read text."""

    rendered: str
    assignment: str | None
    arguments: tuple[str, ...]
    default_factory: Any


_EMPTY_FIELD_TEXT = _PydanticFieldRenderPlan("", None, (), None)


def _put_discriminator_last(args: _FieldArguments) -> None:
    """Write the discriminator as its property name, after the other arguments."""
    if (discriminator := args.take("discriminator")) is None:
        return
    why = Why(_DISCRIMINATOR, "discriminator")
    match discriminator.value:
        case str() as property_name:
            args.put("discriminator", property_name, why, discriminator.items)
        case {"propertyName": property_name}:
            args.put("discriminator", property_name, why, discriminator.items)
        case _:
            args.omit_entry(discriminator, "not-a-property-name", why)


class DataModelField(DataModelFieldBase):
    """Field implementation for Pydantic models."""

    SUPPORTS_FIELD_CONSTRAINTS: ClassVar[bool] = True
    _EXCLUDE_FIELD_KEYS: ClassVar[set[str]] = {
        "alias",
        "default",
        "const",
        "gt",
        "ge",
        "lt",
        "le",
        "multiple_of",
        "min_items",
        "max_items",
        "min_length",
        "max_length",
        "regex",
    }
    _COMPARE_EXPRESSIONS: ClassVar[set[str]] = {"gt", "ge", "lt", "le"}
    _INTEGER_CONSTRAINTS: ClassVar[set[str]] = _COMPARE_EXPRESSIONS | {"multiple_of"}
    _CONSTRAINT_ARGUMENTS: ClassVar[dict[str, str]] = _FIELD_CONSTRAINT_ARGUMENTS
    """The ``Field()`` argument that the constraints model of the field writes each schema keyword as."""
    constraints: Optional[Constraints] = None  # noqa: UP045

    @property
    def has_default_factory_in_field(self) -> bool:
        """Whether the field's call writes the default factory its own facts give it."""
        if (plan := frozen_render_plan(self)) is not None:
            return plan.has_default_factory_in_field
        return self._get_effective_default_factory() is not None

    @property
    @override
    def dropped_constraints_warning(self) -> str | None:
        """Return a warning naming the constraints this field is generated without, if any.

        They are the omitted items of the field's render plan that warn: the declared constraints Pydantic does not
        take on the type, the constraints of a field whose type holds ``AnyUrl`` outside a container, which takes no
        ``pattern``, and the arguments of the ``con*`` type that ``--type-overrides`` replaces.
        """
        omitted = self._render_plan().omitted
        reasons: list[str] = []
        count = 0
        if dropped := [o.why.keyword or o.item for o in omitted if o.reason == "anyurl" and o.warns]:
            if "pattern" in dropped:
                reasons.append(
                    f"Pydantic does not take {self._CONSTRAINT_ARGUMENTS['pattern']} on {self.data_type.type_hint}"
                )
            if others := sorted(self._CONSTRAINT_ARGUMENTS[key] for key in dropped if key != "pattern"):
                reasons.append(f"fields holding AnyUrl are written without {', '.join(others)}")
            count += len(dropped)
        groups: dict[str | None, list[str]] = {}
        for untaken in omitted:
            if untaken.reason == "untaken-by-type":
                groups.setdefault(untaken.detail, []).append(untaken.why.keyword or untaken.item)
        for minimum, keys in groups.items():
            arguments = ", ".join(sorted(self._CONSTRAINT_ARGUMENTS[key] for key in keys))
            reasons.append(
                f"Pydantic does not take {arguments} on {self.data_type.type_hint}"
                if minimum is None
                else f"Pydantic checks {arguments} on {self.data_type.type_hint} only from {minimum} on, which "
                f"an explicit --target-pydantic-version {minimum} or later selects"
            )
            count += len(keys)
        for overridden in omitted:
            if overridden.reason == "override-type-does-not-take":
                reasons.append(f"--type-overrides writes {self.data_type.type_hint} in place of {overridden.detail}")
                count += cast("int", overridden.value)
        if non_finite := [
            *(o.value for o in omitted if o.why.rule == _NON_FINITE_MULTIPLE_OF),
            *(
                dropped
                for data_type in self.data_type.all_data_types
                if (dropped := data_type.__dict__.get(DROPPED_MULTIPLE_OF_KEY)) is not None
            ),
        ]:
            reasons.append(
                "Pydantic takes multiple_of only as a finite number, not "
                + ", ".join(f"multiple_of={represent_python_value(value)}" for value in dict.fromkeys(non_finite))
            )
            count += len(non_finite)
        return self._generated_without_constraints("; ".join(reasons), count) if reasons else None

    @property
    @override
    def method(self) -> str | None:
        """Get the validation method name."""
        return self.validator

    @property
    def validator(self) -> str | None:
        """Get the validator name."""
        return None
        # TODO refactor this method for other validation logic

    @property
    @override
    def field(self) -> str | None:
        """For backwards compatibility."""
        return self._get_field_render_plan().assignment

    def _rendered_field_values(self) -> tuple[str | None, str | None]:
        """Render field and annotated values from one structured plan."""
        return self._rendered_field_values_from_plan(self._get_field_render_plan())

    def _rendered_field_values_from_plan(
        self,
        plan: _PydanticFieldRenderPlan,
    ) -> tuple[str | None, str | None]:
        """Render compatibility values from an already computed plan."""
        if self.is_class_var:
            return None, None
        if not self.use_annotated or not plan.rendered:
            return plan.assignment, None
        return plan.assignment, f"Annotated[{self.type_hint}, {plan.rendered}]"

    def _has_field_statement(self) -> bool:
        """Return whether rendering this field will require a Field() call."""
        return self._render_plan().call is not None

    def _may_have_field_statement(self) -> bool:
        """Return whether field metadata may require a Field() call."""
        has_field_metadata = (
            self.extras
            or self.alias is not None
            or self.const
            or self.constraints is not None
            or (self.use_frozen_field and self.read_only)
            or self.validation_aliases
            or self.serialization_alias is not None
            or self.use_serialization_alias
        )
        needs_required_nullable_field = self.nullable and self.required and not self.use_default_with_required
        needs_optional_nested_factory = (
            self.use_default_factory_for_optional_nested_models
            and not self.required
            and (self.default is None or self.default is UNDEFINED)
        )
        return bool(has_field_metadata or needs_required_nullable_field or needs_optional_nested_factory)

    def _has_numeric_data_type(self, type_name: str, strict_import_part: str) -> bool:
        """Return whether any field data type is the given builtin or strict numeric type."""
        return any(
            data_type.type == type_name
            or (data_type.strict and data_type.import_ and strict_import_part in data_type.import_.import_)
            for data_type in self.data_type.all_data_types
        )

    def _get_strict_field_constraint(
        self, constraint: str, value: Any, *, is_float_type: bool, is_int_type: bool
    ) -> tuple[str, Any] | None:
        if value is None or constraint not in self._INTEGER_CONSTRAINTS:
            return constraint, value
        if is_float_type:
            return constraint, _float_constraint(value)
        if is_int_type:
            return normalize_integer_constraint(constraint, value)
        if isinstance(value, float) and value.is_integer():
            return constraint, int(value)
        return constraint, value

    def _normalized_constraint_arguments(self, omitted: list[Omitted]) -> dict[str, Kwarg]:
        """Return the constraint arguments with integer-safe values, merging colliding bounds into one argument.

        A constraint the type expresses, or a number constraint of a ``None`` type, goes to ``omitted``.
        """
        assert self.constraints is not None
        dumped = self.constraints._exclude_unset_dump  # noqa: SLF001
        if (
            self.data_type.type == "None"
            and not self.data_type.is_custom_type
            and not ((parent := self.parent) and (parent.IS_ALIAS or parent._uses_custom_root_template))  # noqa: SLF001
        ):
            omitted.extend(
                Omitted(f"constraint:{key}", value, "none-type", Why(_NONE_TYPE, key))
                for key, value in dumped.items()
                if key in self._INTEGER_CONSTRAINTS
            )
            dumped = {key: value for key, value in dumped.items() if key not in self._INTEGER_CONSTRAINTS}
        has_integer_constraints = bool(self._INTEGER_CONSTRAINTS & dumped.keys())
        is_float_type = has_integer_constraints and self._has_numeric_data_type("float", "Float")
        is_int_type = has_integer_constraints and not is_float_type and self._has_numeric_data_type("int", "Int")
        values: dict[str, Any] = {}
        sources: dict[str, tuple[str, ...]] = {}
        for k, v in dumped.items():
            item = f"constraint:{k}"
            if k == "multiple_of" and (written := _non_finite_as_written(v, as_float=is_float_type)) is not None:
                omitted.append(
                    Omitted(item, written, "backend-cannot-express", Why(_NON_FINITE_MULTIPLE_OF, k), warns=True)
                )
                continue
            if (
                normalized := self._get_strict_field_constraint(
                    k, v, is_float_type=is_float_type, is_int_type=is_int_type
                )
            ) is None:
                omitted.append(Omitted(item, v, "consumed-by-type", Why(_MULTIPLE_OF_ONE, k)))
                continue
            key, value = normalized
            merge_normalized_constraint(values, key, value)
            sources[key] = (*sources.get(key, ()), item)
        arguments = {
            key: Kwarg(key, value, Why(_CONSTRAINT, sources[key][0].removeprefix("constraint:")), sources[key])
            for key, value in values.items()
        }
        if self._has_strict_decimal():
            arguments["strict"] = Kwarg("strict", True, _STRICT_DECIMAL)  # noqa: FBT003
        return arguments

    def _has_strict_decimal(self) -> bool:
        """Return whether the field's type, or a member of the union it is, is a strict ``Decimal``, aliased or not.

        Pydantic has no strict ``Decimal`` class, so the field writes the strictness among its ``Field()`` arguments.
        """
        return any(
            data_type.strict
            and (import_ := data_type.import_) is not None
            and (import_.from_, import_.import_) == (IMPORT_DECIMAL.from_, IMPORT_DECIMAL.import_)
            for data_type in (self.data_type, *self.data_type.data_types)
        )

    def _has_anyurl_outside_container(self) -> bool:
        def has_anyurl(data_type: DataType, *, inside_container: bool) -> bool:
            is_container = inside_container or (
                data_type.is_dict
                or data_type.is_list
                or data_type.is_set
                or data_type.is_frozen_set
                or data_type.is_mapping
                or data_type.is_sequence
                or data_type.is_tuple
            )
            if data_type.import_ == IMPORT_ANYURL:
                return not is_container
            return any(has_anyurl(child, inside_container=is_container) for child in data_type.data_types) or (
                data_type.dict_key is not None and has_anyurl(data_type.dict_key, inside_container=is_container)
            )

        return has_anyurl(self.data_type, inside_container=False)

    def _get_default_factory_for_optional_nested_model(self) -> _NestedModelFactory | None:
        """Get default_factory for optional nested Pydantic model fields.

        Returns the factory of the nested model if the field type references a BaseModel, otherwise None.
        """
        return _nested_model_factory(self, BaseModelBase)

    @override
    def _has_default_for_nested_model_factory(self) -> bool:
        """Preserve optional None defaults retained by Pydantic model templates."""
        if self.default is None and self.data_type.is_optional:
            return True
        return super()._has_default_for_nested_model_factory()

    @override
    def enable_structured_default_validation(self) -> bool:
        """Return whether the Pydantic validation of the structured default is still to write (``validate_default``)."""
        return self.extras.get("validate_default") is not True

    def _process_field_arguments(self, args: _FieldArguments) -> None:
        """Adjust the ``Field()`` arguments to the Pydantic version of the output model."""

    def _effective_default_factory(self, configured: Kwarg | None, args: _FieldArguments | None = None) -> Kwarg | None:
        """Return the default factory the field's facts give it: the declared one, or a nested model's.

        A declared factory of a required field, or a ``None`` one, goes to ``args`` as omitted.
        """
        if (self.required and not self.use_default_with_required) or (
            self.default is not UNDEFINED and self.default is not None and configured is None
        ):
            if configured is not None and args is not None:
                args.omit_entry(configured, "factory-not-applicable", _FACTORY_NOT_APPLICABLE)
            return None
        if configured is not None and configured.value is not None:
            return configured
        if configured is not None and args is not None:
            args.omit_entry(configured, "none-value", _NONE_VALUE)
        if (
            self.use_default_factory_for_optional_nested_models
            and not self.required
            and (self.default is None or self.default is UNDEFINED)
            and (nested := self._get_default_factory_for_optional_nested_model()) is not None
        ):
            return Kwarg("default_factory", nested, _NESTED_MODEL_FACTORY)
        return None

    def _get_effective_default_factory(self) -> Any:
        """Return the current effective factory without depending on a previous render."""
        if self.is_class_var:
            return None
        configured = (
            Kwarg("default_factory", self.extras["default_factory"], _DECLARED_FACTORY, ("extra:default_factory",))
            if "default_factory" in self.extras
            else None
        )
        return None if (factory := self._effective_default_factory(configured)) is None else factory.value

    def _warned_omissions(self) -> tuple[list[Omitted], bool]:
        """Return the declared constraints the field is generated without and warns about, and whether AnyUrl drops.

        They are the constraints of a field whose type holds ``AnyUrl`` outside a container, those its type does not
        take (``untaken_constraints``) and the arguments ``--type-overrides`` replaced.
        """
        omitted: list[Omitted] = []
        constraints = self.constraints
        anyurl = constraints is not None and self._can_apply_constraints and self._has_anyurl_outside_container()
        if constraints is not None and anyurl:
            fields = type(constraints).model_fields
            omitted.extend(
                Omitted(
                    f"constraint:{name}",
                    value,
                    "anyurl",
                    Why(_ANYURL, fields[name].alias or name),
                    warns=value is not None,
                )
                for name, value in constraints._exclude_unset_dump.items()  # noqa: SLF001
            )
        untaken, minimums = self.untaken_constraints
        omitted.extend(
            Omitted(
                f"untaken:{key}", value, "untaken-by-type", Why(_UNTAKEN, key), warns=True, detail=minimums.get(key)
            )
            for key, value in untaken.items()
        )
        if overridden := self.overridden_constraints:
            replaced, count = overridden
            omitted.append(
                Omitted("override", count, "override-type-does-not-take", _OVERRIDE, warns=True, detail=replaced)
            )
        return omitted, anyurl

    @override
    def _build_frozen_render_plan(self) -> FieldRenderPlan:
        return self._render_plan()

    def _render_plan_text(self, plan: FieldRenderPlan) -> tuple[object, ...]:
        """Return what the field writes from ``plan``, to compare a frozen plan with a rebuilt one."""
        return (
            *self._plan_text(plan)[:3],
            plan.annotation_form,
            plan.has_default_factory_in_field,
            plan.default.missing_sentinel,
            tuple((omitted.item, omitted.reason, omitted.warns, omitted.detail) for omitted in plan.omitted),
        )

    def _declared_render_items(self) -> frozenset[str]:
        """Return the items the field declares that its render plan must write or omit."""
        items = {f"extra:{key}" for key in self.extras if key not in self._EXCLUDE_FIELD_KEYS}
        if self.alias is not None:
            items.add("alias")
        if self.constraints is not None:
            items.update(f"constraint:{key}" for key in self.constraints._exclude_unset_dump)  # noqa: SLF001
        items.update(f"untaken:{key}" for key in self.untaken_constraints[0])
        if self.overridden_constraints:
            items.add("override")
        if self.validation_aliases:
            items.add("validation_aliases")
        if self.serialization_alias is not None:
            items.add("serialization_alias")
        if self.use_frozen_field and self.read_only:
            items.add("read_only")
        return frozenset(items)

    def _field_arguments(self, omitted: list[Omitted], *, anyurl: bool) -> _FieldArguments:
        """Build the ``Field()`` arguments and the default factory of a field that is no class variable."""
        args = _FieldArguments(omitted)
        for key, value in {k: v for k, v in self.extras.items() if k not in self._EXCLUDE_FIELD_KEYS}.items():
            args.put(key, value, Why(_EXTRA, key), (f"extra:{key}",))
        if self.alias is not None:
            args.put("alias", self.alias, _ALIAS, ("alias",))
        if self.constraints is not None and not anyurl:
            if (blocked := self._constraints_blocked_by()) is not None:
                args.omitted.extend(
                    Omitted(f"constraint:{key}", value, blocked, Why(_CONSTRAINT, key))
                    for key, value in self.constraints._exclude_unset_dump.items()  # noqa: SLF001
                )
            else:
                for name, argument in self._normalized_constraint_arguments(args.omitted).items():
                    args.put(name, argument.value, argument.why, argument.items)

        if self.use_field_description:
            args.pop("description", "use-field-description", _USE_FIELD_DESCRIPTION)

        self._process_field_arguments(args)
        _put_discriminator_last(args)
        args.factory = self._effective_default_factory(args.take("default_factory"), args)
        return args

    def _call_default_factory(self, args: _FieldArguments) -> Kwarg | None:  # noqa: PLR6301
        """Return the default factory the call writes: the one the field's facts give it."""
        return args.factory

    def _get_field_data_and_default_factory(self) -> tuple[dict[str, Any], Any]:
        """Build Field() keyword data and the effective default_factory."""
        if self.is_class_var:
            return {}, None
        omitted, anyurl = self._warned_omissions()
        args = self._field_arguments(omitted, anyurl=anyurl)
        return args.data(), None if (factory := self._call_default_factory(args)) is None else factory.value

    @override
    def _build_render_plan(self) -> FieldRenderPlan:
        """Build the field's render plan from its facts as they are now."""
        omitted, anyurl = self._warned_omissions()
        if self.is_class_var:
            accounted = {omission.item for omission in omitted}
            omitted.extend(
                Omitted(item, None, "class-var", _CLASS_VAR)
                for item in sorted(self._declared_render_items() - accounted)
            )
            return FieldRenderPlan(
                annotation_form="classvar",
                call=None,
                default=FieldDefault(self.default, missing_sentinel=False, why=_DEFAULT),
                has_default_factory_in_field=False,
                omitted=tuple(omitted),
                symbol_imports=(),
                why=_CLASS_VAR,
            )
        args = self._field_arguments(omitted, anyurl=anyurl)
        kwargs: list[Kwarg] = []
        for argument in args.kwargs():
            if argument.value is None:
                args.omit_entry(argument, "none-value", _NONE_VALUE)
            else:
                kwargs.append(argument)
        factory = self._call_default_factory(args)
        missing = self._uses_missing_sentinel(factory_present=args.factory is not None)
        default = FieldDefault(self.default, missing, _MISSING_DEFAULT if missing else _DEFAULT)
        call: CallPlan | None = None
        if kwargs or factory is not None:
            positional: Positional | None = None
            if self.use_annotated:
                pass
            elif (
                self.required
                and not self.use_default_with_required
                and factory is None
                and not self.extras.get("validate_default")
            ):
                positional = Positional("required", _REQUIRED)
            elif (
                factory is None and self.represented_default and not self.should_strip_default_none(keep_optional=True)
            ):
                positional = Positional("default", _DEFAULT_ARGUMENT)
            call = CallPlan(
                positional,
                factory,
                tuple(kwargs),
                default_keyword=self.use_default_kwarg and positional is not None and positional.kind == "default",
                why=_FIELD_CALL,
            )
        elif self.nullable and self.required and not self.use_default_with_required:
            call = CallPlan(
                Positional("required", _REQUIRED_NULLABLE), None, (), default_keyword=False, why=_FIELD_CALL
            )
        return self._field_render_plan(
            call, default, has_default_factory_in_field=args.factory is not None, omitted=tuple(args.omitted)
        )

    def _field_render_plan(
        self,
        call: CallPlan | None,
        default: FieldDefault,
        *,
        has_default_factory_in_field: bool,
        omitted: tuple[Omitted, ...],
    ) -> FieldRenderPlan:
        """Return the plan of a field that is no class variable, writing ``call``."""
        annotated = call is not None and self.use_annotated
        return FieldRenderPlan(
            "annotated" if annotated else "plain",
            call,
            default,
            has_default_factory_in_field,
            omitted,
            () if call is None else ((IMPORT_FIELD, call.why),),
            _ANNOTATED if annotated else _PLAIN_ANNOTATION,
        )

    def _uses_missing_sentinel(self, *, factory_present: bool) -> bool:  # noqa: ARG002, PLR6301
        """Return whether the field writes Pydantic's ``MISSING`` sentinel as its default; only Pydantic 2 does."""
        return False

    def _call_arguments(self, call: CallPlan) -> tuple[str, ...]:
        """Return the arguments of ``call`` as written: positional, default factory, then the rest sorted."""
        arguments = sorted(f"{argument.name}={represent_python_value(argument.value)}" for argument in call.kwargs)
        if call.factory is not None:
            arguments = [f"default_factory={call.factory.value}", *arguments]
        match call.positional:
            case Positional(kind="required"):
                arguments = ["...", *arguments]
            case Positional(kind="none"):
                arguments = ["None", *arguments]
            case Positional(kind="default"):
                arguments = [self.represented_default, *arguments]
        return tuple(arguments)

    def _plan_text(self, plan: FieldRenderPlan) -> _PydanticFieldRenderPlan:
        """Write the text of the field's call from ``plan``."""
        if plan.annotation_form == "classvar":
            default = plan.default.value
            return _PydanticFieldRenderPlan(
                "" if default is UNDEFINED else represent_python_value(default), None, (), None
            )
        if (call := plan.call) is None:
            return _EMPTY_FIELD_TEXT
        arguments = self._call_arguments(call)
        rendered = self._render_field_call(arguments)
        return _PydanticFieldRenderPlan(
            rendered=rendered,
            assignment=f"Field(default={', '.join(arguments)})" if call.default_keyword else rendered,
            arguments=arguments,
            default_factory=None if call.factory is None else call.factory.value,
        )

    @staticmethod
    def _render_field_call(arguments: tuple[str, ...]) -> str:
        return f"Field({', '.join(arguments)})"

    def _get_field_render_plan(self) -> _PydanticFieldRenderPlan:
        """Write all Field() views from the field's render plan."""
        return self._plan_text(self._render_plan())

    @override
    def __str__(self) -> str:
        """Return Field() call with all constraints and metadata."""
        return self._get_field_render_plan().rendered

    @property
    @override
    def is_class_var(self) -> bool:
        """Check if this field is a ClassVar."""
        return self.extras.get("x-is-classvar") is True

    @property
    @override
    def type_hint(self) -> str:
        """Get the type hint including ClassVar if applicable."""
        if (plan := frozen_render_plan(self)) is not None:
            is_class_var = plan.annotation_form == "classvar"
        else:
            is_class_var = self.is_class_var
        if is_class_var:
            return f"ClassVar[{super().type_hint}]"
        return super().type_hint

    @property
    @override
    def annotated(self) -> str | None:
        """Get the Annotated type hint if use_annotated is enabled."""
        if not self.use_annotated or self.is_class_var:
            return None
        if not (field := str(self)):
            return None
        return f"Annotated[{self.type_hint}, {field}]"

    @override
    def _import_state_data_type(self) -> DataType | None:
        return self._import_state_target(
            needs_annotated=self.use_annotated and not self.is_class_var and self._has_field_statement(),
            data_type=self.data_type,
        )

    @property
    @override
    def imports(self) -> tuple[Import, ...]:
        """Get all required imports including Field if needed.

        Computes _has_field_statement() once and derives needs_annotated from it:
        str(self) is non-empty exactly when _has_field_statement() is true (both
        come from the same Field() data), so this matches bool(self.annotated)
        without rebuilding the type hint and Field() string.
        """
        has_field_statement = self._has_field_statement()
        base_imports = self._collect_field_imports(
            needs_annotated=self.use_annotated and not self.is_class_var and has_field_statement
        )
        if has_field_statement:
            return chain_as_tuple(base_imports, (IMPORT_FIELD,))
        return base_imports


class BaseModelBase(DataModel, ABC):
    """Abstract base class for Pydantic BaseModel implementations."""

    REQUIRES_RUNTIME_IMPORTS_WITH_RUFF_CHECK: ClassVar[bool] = True
    _FREEZES_FIELD_RENDER_PLANS: ClassVar[bool] = True

    def __init__(  # noqa: PLR0913
        self,
        *,
        reference: Reference,
        fields: list[DataModelFieldBase],
        decorators: list[str] | None = None,
        base_classes: list[Reference] | None = None,
        custom_base_class: str | list[str] | None = None,
        custom_template_dir: Path | None = None,
        extra_template_data: defaultdict[str, Any] | None = None,
        path: Path | None = None,
        description: str | None = None,
        default: Any = UNDEFINED,
        nullable: bool = False,
        keyword_only: bool = False,
        treat_dot_as_module: bool | None = None,
    ) -> None:
        """Initialize the BaseModel with fields and configuration."""
        methods: list[str] = [field.method for field in fields if field.method]

        super().__init__(
            fields=fields,
            reference=reference,
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

    @cached_property
    @override
    def template_file_path(self) -> Path:
        """Get the template file path with backward compatibility support."""
        # This property is for Backward compatibility
        # Current version supports '{custom_template_dir}/BaseModel.jinja'
        # But, Future version will support only '{custom_template_dir}/pydantic/BaseModel.jinja'
        if self._custom_template_dir is not None:
            custom_template_file_path = self._custom_template_dir / Path(self.TEMPLATE_FILE_PATH).name
            _remember_custom_template_dependency(self._custom_template_dir, custom_template_file_path)
            if cached_path_exists(custom_template_file_path):
                return custom_template_file_path.resolve()
        return super().template_file_path


_rebuild_model_with_datamodel_namespace(DataModelField)
