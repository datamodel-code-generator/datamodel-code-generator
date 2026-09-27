"""Plan the keyword arguments that stand for the fields of eligible request bodies, as body_arguments 'both' asks."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.naming import RESERVED_ARGUMENTS, identifier, snake
from datamodel_code_generator._client.plan import FieldArgument, FieldBranch
from datamodel_code_generator._generation_contract import SourceLocation, SymbolId
from datamodel_code_generator._runtime.model_codecs.bindings import ModelNode, UnionNode
from datamodel_code_generator._runtime.model_codecs.media import normalize_media_type

if TYPE_CHECKING:
    from collections.abc import Mapping

    from datamodel_code_generator._client.plan import ClientPlan, MediaSpec, OperationSpec
    from datamodel_code_generator._generation_contract import FieldUseBinding, GeneratedTypeContractBatch, TypeUseId
    from datamodel_code_generator._openapi_codec_plan import CodecPlan
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.bindings import ModelBinding, UseBinding

_KINDS: Final = frozenset({"json", "form"})
_NATIVE_KINDS: Final = frozenset({"model", "dataclass", "typed_dict", "struct"})


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


def _model(binding: UseBinding) -> ModelBinding | None:
    """Return the object model a use binds natively, alone or with null, or None when its body is anything else.

    A root model whose root is such a model, as a Pydantic body of an object or null is, stands for that model.
    """
    models = {item.symbol: item for item in binding.models}
    node = binding.type
    if isinstance(node, ModelNode) and (root := models[node.symbol].root) is not None:
        node = root
    if isinstance(node, UnionNode) and len(node.members) == 1:
        node = node.members[0]
    if not isinstance(node, ModelNode) or binding.projection_mode != "native":
        return None
    return model if (model := models[node.symbol]).native_kind in _NATIVE_KINDS else None


def _required(wire: WirePlan, site: SourceLocation, seen: set[SourceLocation]) -> set[str]:
    """Return the names an object schema requires, with those its allOf members require, following references.

    A schema its own allOf members reach again adds nothing more.
    """
    location, schema = wire.schema(site)
    if location in seen:
        return set()
    seen.add(location)
    required = schema.get("required")
    names: set[str] = {name for name in required if isinstance(name, str)} if isinstance(required, tuple) else set()
    members = schema.get("allOf")
    for index in range(len(members) if isinstance(members, tuple) else 0):
        member = SourceLocation(location.document, f"{location.pointer}/allOf/{index}", "schema")
        names |= _required(wire, member, seen)
    return names


class _Fields:
    """Plan the field branches of every operation whose body arguments are 'both'."""

    def __init__(self, codecs: CodecPlan, batch: GeneratedTypeContractBatch, wire: WirePlan) -> None:
        self.wire = wire
        self.bindings: Mapping[TypeUseId, UseBinding] = dict(codecs.bindings)
        self.symbols = {name: symbol for symbol, name in codecs.imports}
        self.adapted = {item.use for item in codecs.adapters}
        self.members: dict[SymbolId, list[FieldUseBinding]] = {}
        for member in batch.fields:
            self.members.setdefault(member.consumer, []).append(member)
        self.problems: list[Diagnostic] = []

    def model(self, media: MediaSpec) -> tuple[ModelBinding, SymbolId] | None:
        """Return the object model a media's body binds natively with its symbol, or None for any other body.

        Every name the body's schema requires must be a field of the model, not a key only extra properties hold.
        """
        use = media.use
        if media.kind not in _KINDS or media.members is not None or use is None or use.id in self.adapted:
            return None
        binding = self.bindings.get(use.id)
        model = None if binding is None else _model(binding)
        if (
            model is None
            or use.schema is None
            or not _required(self.wire, use.schema, set()) <= {field.wire_name for field in model.fields}
        ):
            return None
        return model, SymbolId(self.symbols[model.symbol])

    def branch(self, media: MediaSpec, names: Mapping[str, str]) -> FieldBranch | None:
        """Return the field branch of one media type, or None when its body cannot be given as fields.

        A native projection constructs every field its direction does not exclude, and no required field is excluded,
        so a call gives every field but the read-only ones.
        """
        if (found := self.model(media)) is None:
            return None
        model, symbol = found
        declared = {member.wire_name: member for member in self.members.get(symbol, []) if member.wire_name}
        fields: list[FieldArgument] = []
        for item in model.fields:
            if item.read_only:
                continue
            facts = declared[item.wire_name].model_facts
            assert facts is not None
            python_name = names.get(item.wire_name) or snake(item.wire_name)
            fields.append(
                FieldArgument(
                    python_name=python_name, wire_name=item.wire_name, required=facts.required, type=facts.type
                )
            )
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
    plan: ClientPlan, codecs: CodecPlan, batch: GeneratedTypeContractBatch, wire: WirePlan
) -> tuple[ClientPlan, tuple[Diagnostic, ...]]:
    """Return the plan with each operation's field branches, and the problems of naming them."""
    fields = _Fields(codecs, batch, wire)
    operations = tuple(replace(spec, fields=fields.operation(spec)) for spec in plan.operations)
    resources = tuple(
        replace(resource, operations=tuple(operations[spec.index] for spec in resource.operations))
        for resource in plan.resources
    )
    return replace(plan, operations=operations, resources=resources), tuple(fields.problems)
