"""Plan the keyword arguments that stand for the fields of eligible request bodies, as body_arguments 'both' asks."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.naming import RESERVED_ARGUMENTS, identifier, snake
from datamodel_code_generator._client.plan import FieldArgument, FieldBranch
from datamodel_code_generator._runtime.model_codecs.media import normalize_media_type

if TYPE_CHECKING:
    from collections.abc import Container, Mapping

    from datamodel_code_generator._client.model_facts import ModelFacts, ModelField
    from datamodel_code_generator._client.plan import ClientPlan, MediaSpec, OperationSpec
    from datamodel_code_generator._target_contract import TypeUseId

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

    def __init__(self, facts: ModelFacts, codecs: Container[TypeUseId]) -> None:
        self.facts = facts
        self.codecs = codecs
        self.problems: list[Diagnostic] = []

    def model(self, media: MediaSpec) -> tuple[ModelField, ...] | str:
        """Return the fields of the object model a body with a codec stands for, or why it has no field arguments."""
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
        return self.facts.fields(model.id)

    def branch(self, media: MediaSpec, names: Mapping[str, str]) -> FieldBranch | str:
        """Return the field branch of one media type, or why its body cannot be given as fields.

        A native projection constructs every field its direction does not exclude, so a call gives every field but the
        read-only ones, and must give those the model's constructor requires.
        """
        if isinstance(declared := self.model(media), str):
            return declared
        fields = [
            FieldArgument(
                python_name=names.get(item.wire_name) or snake(item.wire_name),
                wire_name=item.wire_name,
                required=item.required,
                type=item.type,
            )
            for item in declared
            if not item.read_only
        ]
        return (
            FieldBranch(media_type=media.media_type, fields=tuple(fields))
            if fields
            else "its model has no writable field"
        )

    def operation(self, spec: OperationSpec) -> OperationSpec:
        """Plan an operation's field branches and why other media have none, refusing names that name no field.

        Names that are invalid or taken are refused too.
        """
        if spec.body is None or spec.body_arguments != "both":
            return spec
        names = {(normalize_media_type(item.media_type), item.name): item.python_name for item in spec.body_field_names}
        branches: list[FieldBranch] = []
        body_only: list[tuple[str, str]] = []
        for media in spec.body.media:
            mapped = {name: python for (media_type, name), python in names.items() if media_type == media.media_type}
            if isinstance(branch := self.branch(media, mapped), str):
                body_only.append((media.media_type, branch))
            else:
                branches.append(branch)
                for field in branch.fields:
                    names.pop((media.media_type, field.wire_name), None)
        self.problems.extend(
            _problem(
                "E_CONFIG_VALUE",
                f"The body field name of the {media_type} property {name!r} of {_label(spec)} names no field argument",
                spec,
                "operations",
            )
            for media_type, name in names
        )
        for branch in branches:
            if taken := _taken(spec, branch):
                self.problems.append(
                    _problem(
                        "E_NAME_COLLISION",
                        f"The {branch.media_type} body fields of {_label(spec)} cannot take the argument names "
                        f"{', '.join(map(repr, taken))}; name them with the operation's body_field_names in "
                        "--client-operations",
                        spec,
                    )
                )
        return replace(spec, fields=tuple(branches), body_only=tuple(body_only))


def _taken(spec: OperationSpec, branch: FieldBranch) -> list[str]:
    """Return the field argument names that are no identifier, are reserved, or another argument already takes."""
    parameters = {parameter.python_name for parameter in spec.parameters}
    counts = Counter(field.python_name for field in branch.fields)
    return sorted({
        name
        for field in branch.fields
        if not identifier(name := field.python_name)
        or name in RESERVED_ARGUMENTS
        or name in parameters
        or counts[name] > 1
    })


def plan_fields(
    plan: ClientPlan, facts: ModelFacts, codecs: Container[TypeUseId]
) -> tuple[ClientPlan, tuple[Diagnostic, ...]]:
    """Return the plan with each operation's field branches, and the problems of naming them."""
    fields = _Fields(facts, codecs)
    operations = tuple(fields.operation(spec) for spec in plan.operations)
    resources = tuple(
        replace(resource, operations=tuple(operations[spec.index] for spec in resource.operations))
        for resource in plan.resources
    )
    return replace(plan, operations=operations, resources=resources), tuple(fields.problems)
