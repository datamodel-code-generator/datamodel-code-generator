"""Plan the keyword arguments that stand for the fields of eligible request bodies, as body_arguments 'both' asks."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.naming import HELPER_ARGUMENTS, RESERVED_ARGUMENTS
from datamodel_code_generator._client.plan import FieldArgument, FieldBranch
from datamodel_code_generator._runtime.model_codecs.media import normalize_media_type
from datamodel_code_generator._target_naming import NameScope, explicit_name

if TYPE_CHECKING:
    from collections.abc import Container

    from datamodel_code_generator._client.model_facts import ModelFacts, ModelField
    from datamodel_code_generator._client.plan import ClientPlan, MediaSpec, OperationSpec
    from datamodel_code_generator._target_contract import OperationId, TypeUseId
    from datamodel_code_generator._target_naming import TargetNames

_KINDS: Final = frozenset({"json", "form"})


def _problem(code: str, message: str, operation: OperationSpec, option_path: str | None = None) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity="error",
        stage="config" if code == "E_CONFIG_VALUE" else "target",
        message=message,
        source_pointer=operation.contract.id.use_site.pointer,
        option_path=option_path,
    )


def _label(operation: OperationSpec) -> str:
    return f"{operation.contract.method.upper()} {operation.contract.path}"


class _Fields:
    """Plan the field branches of every operation whose body arguments are 'both'."""

    def __init__(
        self, facts: ModelFacts, codecs: Container[TypeUseId], names: TargetNames, helpers: Container[OperationId]
    ) -> None:
        self.facts = facts
        self.codecs = codecs
        self.names = names
        self.helpers = helpers
        self.problems: list[Diagnostic] = []

    def model(self, media: MediaSpec) -> tuple[ModelField, ...] | str:
        """Return the writable fields of the object model a body with a codec stands for, or why it has none.

        A native projection constructs every field its direction does not exclude, so a call gives every field but the
        read-only ones, and must give those the model's constructor requires.
        """
        use = media.use
        if media.kind not in _KINDS or media.members is not None:
            return "only JSON and URL-encoded form bodies have field arguments"
        if (
            use is None
            or (value := use.type) is None
            or use.id not in self.codecs
            or (model := self.facts.model(value)) is None
        ):
            return "its schema is not an object model"
        return (
            tuple(item for item in self.facts.fields(model.id) if not item.read_only)
            or "its model has no writable field"
        )

    def operation(self, spec: OperationSpec) -> OperationSpec:
        """Plan an operation's field branches and why other media have none, refusing names that name no field.

        Explicit names, configured or `--aliases` entries, must be new beside the method's other arguments. Each other
        field takes its model field's name, or for a key that is no identifier the name a field would take, suffixed
        apart from the other arguments; a field of several media takes one name.
        """
        if spec.body is None or spec.body_arguments != "both":
            return spec
        names = {(normalize_media_type(item.media_type), item.name): item.python_name for item in spec.body_field_names}
        declared: list[tuple[str, tuple[ModelField, ...]]] = []
        body_only: list[tuple[str, str]] = []
        for media in spec.body.media:
            if isinstance(fields := self.model(media), str):
                body_only.append((media.media_type, fields))
            else:
                declared.append((media.media_type, fields))
        explicit = {
            (media_type, item.wire_name): given
            for media_type, fields in declared
            for item in fields
            if (given := names.pop((media_type, item.wire_name), None) or self.names.alias(item.wire_name)) is not None
        }
        self.problems.extend(
            _problem(
                "E_CONFIG_VALUE",
                f"The body field name of the {media_type} property {name!r} of {_label(spec)} names no field argument",
                spec,
                "operations",
            )
            for media_type, name in names
        )
        helpers = HELPER_ARGUMENTS if spec.contract.id in self.helpers else ()
        scope = NameScope((*RESERVED_ARGUMENTS, *helpers, *(parameter.python_name for parameter in spec.parameters)))
        for media_type, fields in declared:
            given = [explicit[key] for item in fields if (key := (media_type, item.wire_name)) in explicit]
            counts = Counter(given)
            if taken := sorted({name for name in given if name in scope or counts[name] > 1}):
                self.problems.append(
                    _problem(
                        "E_NAME_COLLISION",
                        f"The {media_type} body fields of {_label(spec)} cannot take the argument names "
                        f"{', '.join(map(repr, taken))}, which other arguments take",
                        spec,
                    )
                )
        for name in explicit.values():
            scope.take(name)
        derived: dict[str, str] = {}
        branches = tuple(
            FieldBranch(
                media_type=media_type,
                fields=tuple(
                    FieldArgument(
                        python_name=explicit.get((media_type, item.wire_name))
                        or derived.get(item.wire_name)
                        or derived.setdefault(item.wire_name, scope.claim(self.base(item))),
                        wire_name=item.wire_name,
                        required=item.required,
                        type=item.type,
                    )
                    for item in fields
                ),
            )
            for media_type, fields in declared
        )
        return replace(spec, fields=branches, body_only=tuple(body_only))

    def base(self, field: ModelField) -> str:
        """Return the name a field's argument derives from: its model field's name, or for a key, a field's name."""
        return field.name if explicit_name(field.name) else self.names.argument(field.wire_name)


def plan_fields(
    plan: ClientPlan,
    facts: ModelFacts,
    codecs: Container[TypeUseId],
    names: TargetNames,
    helpers: Container[OperationId] = (),
) -> tuple[ClientPlan, tuple[Diagnostic, ...]]:
    """Return the plan with each operation's field branches, and the problems of naming them."""
    fields = _Fields(facts, codecs, names, helpers)
    operations = tuple(fields.operation(spec) for spec in plan.operations)
    resources = tuple(
        replace(resource, operations=tuple(operations[spec.index] for spec in resource.operations))
        for resource in plan.resources
    )
    return replace(plan, operations=operations, resources=resources), tuple(fields.problems)
