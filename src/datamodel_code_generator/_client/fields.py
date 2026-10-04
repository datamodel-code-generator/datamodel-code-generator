"""Plan the keyword arguments that stand for the fields of eligible request bodies, as body_arguments 'both' asks."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.naming import RESERVED_ARGUMENTS, identifier, snake
from datamodel_code_generator._client.plan import FieldArgument, FieldBranch
from datamodel_code_generator._runtime.model_codecs.media import normalize_media_type
from datamodel_code_generator._target_contract import SourceLocation

if TYPE_CHECKING:
    from collections.abc import Container, Mapping

    from datamodel_code_generator._client.model_facts import ModelFacts, ModelField
    from datamodel_code_generator._client.plan import ClientPlan, MediaSpec, OperationSpec
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._target_contract import TypeUseId

_KINDS: Final = frozenset({"json", "form"})
_MEMBERS: Final = ("allOf", "anyOf", "oneOf")


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


def _required(wire: WirePlan, site: SourceLocation, seen: set[SourceLocation]) -> set[str]:
    """Return the names an object schema requires, following references and its members.

    A body bound to one model requires what its allOf members require, and what its object alternative to null does;
    a schema its own members reach again adds nothing more.
    """
    location, schema = wire.schema(site)
    if location in seen:
        return set()
    seen.add(location)
    required = schema.get("required")
    names: set[str] = {name for name in required if isinstance(name, str)} if isinstance(required, tuple) else set()
    for keyword in _MEMBERS:
        members = schema.get(keyword)
        for index in range(len(members) if isinstance(members, tuple) else 0):
            member = SourceLocation(location.document, f"{location.pointer}/{keyword}/{index}", "schema")
            names |= _required(wire, member, seen)
    return names


class _Fields:
    """Plan the field branches of every operation whose body arguments are 'both'."""

    def __init__(self, facts: ModelFacts, codecs: Container[TypeUseId], wire: WirePlan) -> None:
        self.facts = facts
        self.codecs = codecs
        self.wire = wire
        self.problems: list[Diagnostic] = []

    def model(self, media: MediaSpec) -> tuple[tuple[ModelField, ...], set[str]] | None:
        """Return the fields of the object model a media's body with a codec stands for, and the names it requires.

        Any other body is None, as is one whose schema requires a name that only extra properties could hold.
        """
        use = media.use
        if (
            media.kind not in _KINDS
            or media.members is not None
            or use is None
            or (value := use.type) is None
            or use.id not in self.codecs
        ):
            return None
        model = self.facts.model(value)
        if model is None or use.schema is None:
            return None
        fields = self.facts.fields(model.id)
        if not (required := _required(self.wire, use.schema, set())) <= {field.wire_name for field in fields}:
            return None
        return fields, required

    def branch(self, media: MediaSpec, names: Mapping[str, str]) -> FieldBranch | None:
        """Return the field branch of one media type, or None when its body cannot be given as fields.

        A native projection constructs every field its direction does not exclude, and no required field is excluded,
        so a call gives every field but the read-only ones, those the body's schema requires first of all, whatever
        requiredness the model gives them.
        """
        if (found := self.model(media)) is None:
            return None
        declared, required = found
        fields = [
            FieldArgument(
                python_name=names.get(item.wire_name) or snake(item.wire_name),
                wire_name=item.wire_name,
                required=item.wire_name in required,
                type=item.type,
            )
            for item in declared
            if not item.read_only
        ]
        return FieldBranch(media_type=media.media_type, fields=tuple(fields)) if fields else None

    def operation(self, spec: OperationSpec) -> tuple[FieldBranch, ...]:
        """Plan an operation's field branches, and refuse names that are invalid, taken, or name no field."""
        if spec.body is None or spec.body_arguments != "both":
            return ()
        names = {(normalize_media_type(item.media_type), item.name): item.python_name for item in spec.body_field_names}
        branches: list[FieldBranch] = []
        for media in spec.body.media:
            mapped = {name: python for (media_type, name), python in names.items() if media_type == media.media_type}
            if (branch := self.branch(media, mapped)) is not None:
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
                        f"{', '.join(map(repr, taken))}; name them with body_field_names",
                        spec,
                    )
                )
        return tuple(branches)


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
    plan: ClientPlan, facts: ModelFacts, codecs: Container[TypeUseId], wire: WirePlan
) -> tuple[ClientPlan, tuple[Diagnostic, ...]]:
    """Return the plan with each operation's field branches, and the problems of naming them."""
    fields = _Fields(facts, codecs, wire)
    operations = tuple(replace(spec, fields=fields.operation(spec)) for spec in plan.operations)
    resources = tuple(
        replace(resource, operations=tuple(operations[spec.index] for spec in resource.operations))
        for resource in plan.resources
    )
    return replace(plan, operations=operations, resources=resources), tuple(fields.problems)
