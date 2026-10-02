"""Plan the batch helpers of a client target and the checks they must pass.

A helper writes the items of one request into a JSON array of the operation's request body, the whole body or one of
its properties, and reads one result per item from an array of the success response. Each result item carries a
success member or an error member, whose typed accessors the package generates, and is matched with its input item
by position or by a declared ID.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final, Literal

from datamodel_code_generator._client.pagination import (
    _SEQUENCES,
    ItemStep,
    _child,
    _label,
    _page_use,
    _Pages,
    _problem,
    _tokens,
    _unwrapped,
)
from datamodel_code_generator._client.polling import _nonnull, _Polls
from datamodel_code_generator._generation_contract import TypeUseBinding
from datamodel_code_generator._runtime.model_codecs.bindings import ArrayNode, ModelNode

if TYPE_CHECKING:
    from collections.abc import Mapping

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import Diagnostic
    from datamodel_code_generator._client.plan import ClientPlan, OperationSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper
    from datamodel_code_generator._generation_contract import FinalPythonType, SourceLocation
    from datamodel_code_generator._openapi_codec_plan import CodecPlan
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.bindings import TypeNode, UseBinding

_IDS: Final[Mapping[frozenset[str], Literal["str", "int"]]] = {
    frozenset({"string"}): "str",
    frozenset({"integer"}): "int",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class BatchMemberSpec:
    """A result member a helper reads: its type and the steps of its accessor from a result item."""

    value: FinalPythonType
    steps: tuple[ItemStep, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class BatchSpec:
    """A batch helper ready to render: its operation, input item and result types, and result accessors.

    `items_member` is the wire name of the request body property the items are written to, or None when the body is
    the array itself; `results` reads the result array of a decoded
    response, and `success` and `error` read the members of one result item. `item_id` is the Python type of a
    declared correlation ID, or None for positions.
    """

    helper: Helper
    operation: OperationSpec
    item: FinalPythonType
    item_use: TypeUseBinding
    items_member: str | None
    page: TypeUseBinding
    result: FinalPythonType
    results: tuple[ItemStep, ...]
    success: BatchMemberSpec
    error: BatchMemberSpec
    item_id: Literal["str", "int"] | None
    schemas: tuple[Mapping[str, str], ...]


class _Batches:
    """Check and plan every enabled batch helper of a client target."""

    def __init__(self, polls: _Polls) -> None:
        """Keep the shared pagination and polling checks."""
        self.polls = polls
        self.pages: _Pages = polls.pages

    def schema(self, reference: Any) -> Mapping[str, str]:
        """Return a schema reference as the manifest names it."""
        return {"document": self.polls.protocols.documents[reference], "pointer": reference.pointer}

    def helper(self, helper: Helper) -> tuple[BatchSpec | None, list[Diagnostic]]:  # noqa: PLR0914
        """Check one enabled helper against its operation, and plan it when every check passes."""
        tree, at = helper.tree, helper.at
        spec = self.polls.spec(tree["operation"])
        problems: list[Diagnostic] = []
        if tree["retry_failed_subset"]:
            message = f"The batch helper {helper.name!r} retries failed subsets, which is not supported yet"
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", f"{at}.retry_failed_subset", message, spec))
        items = self.items(helper, spec, problems)
        results = self.results(helper, spec, problems)
        if items is None or results is None:
            return None, problems
        item, member, item_schema, item_use = items
        page, result, steps, node, binding = results
        success = self.member(helper, spec, binding, node, "success", problems)
        error = self.member(helper, spec, binding, node, "error", problems)
        item_id = self.correlation(helper, spec, item_schema, binding, node, problems)
        if success is None or error is None or problems:
            return None, problems
        return BatchSpec(
            helper=helper,
            operation=spec,
            item=item,
            item_use=item_use,
            items_member=member,
            page=page,
            result=result,
            results=steps,
            success=success,
            error=error,
            item_id=item_id,
            schemas=(
                self.schema(tree["item_schema"]),
                self.schema(tree["success"]["schema"]),
                self.schema(tree["error"]["schema"]),
            ),
        ), problems

    def items(  # noqa: PLR0911
        self, helper: Helper, spec: OperationSpec, problems: list[Diagnostic]
    ) -> tuple[FinalPythonType, str | None, SourceLocation, TypeUseBinding] | None:
        """Check the request body the items are written to: return the item type, property, schema, and root model.

        A body that is a root model of the array, rather than the array, is built from the items by its type.

        The body has one JSON media sent by default, with a native model binding; the items pointer names the body or
        one of its properties, an array of the item schema, and every other property must be optional, since the
        helper sends no other member.
        """
        at, name, label = f"{helper.at}.request_items", helper.name, _label(spec)
        body = spec.body
        assert body is not None
        if len(body.media) != 1 or (media := body.media[0]).kind != "json" or media.use is None or body.default is None:
            message = f"The batch helper {name!r} needs one JSON request media of {label}, which is not supported yet"
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", at, message, spec))
            return None
        use = media.use
        if (binding := self.pages.bindings.get(use.id)) is None or (
            binding.projection_mode != "native" or binding.converter_strategy == "registered_adapter"
        ):
            message = f"The batch helper {name!r} sends an envelope-projected request body, which is not supported yet"
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", at, message, spec))
            return None
        pointer = helper.tree["request_items"]["pointer"]
        if len(tokens := _tokens(pointer)) > 1:
            message = f"The items pointer {pointer!r} of {name!r} names a nested member, which is not supported yet"
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", at, message, spec))
            return None
        if (reached := self.pages.walk(binding, pointer)) == "absent":
            message = f"The items pointer {pointer!r} of {name!r} names no property of the {label} request body"
            problems.append(_problem("E_CONFIG_VALUE", "config", at, message, spec))
            return None
        if reached == "unsupported" or any(step.kind == "root" for step in reached.steps[0 if tokens else 1 :]):
            message = (
                f"The items pointer {pointer!r} of {name!r} reads through a union, a map, or a root model property, "
                "which is not supported yet"
            )
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", at, message, spec))
            return None
        if not isinstance(reached.node, ArrayNode) or reached.node.container not in _SEQUENCES:
            message = f"The items pointer {pointer!r} of {name!r} selects no JSON array of the {label} request body"
            problems.append(_problem("E_CONFIG_VALUE", "config", at, message, spec))
            return None
        member = reached.member
        array = use.schema if member is None else member.schema
        reference = helper.tree["item_schema"]
        if (expected := self.pages.item_schema(reference)) is None:
            message = f"The item_schema {reference.pointer!r} of {name!r} does not exist in its document"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{helper.at}.item_schema", message, spec))
            return None
        if array is None or self.pages.wire.schema(_child(self.pages.nonnull(array), "items"))[0] != expected:
            message = (
                f"The item_schema {reference.pointer!r} of {name!r} is not the item schema of the array its items "
                "pointer selects"
            )
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{helper.at}.item_schema", message, spec))
            return None
        models = {model.symbol: model for model in binding.models}
        if tokens and (
            others := [
                field.wire_name
                for field in models[_model(binding.type, models).symbol].fields
                if field.required and field.wire_name != tokens[0]
            ]
        ):
            message = (
                f"The batch helper {name!r} sends only the items of the {label} request body, which also requires "
                f"{', '.join(map(repr, others))}"
            )
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", at, message, spec))
            return None
        facts = None if member is None else member.model_facts
        item = self.pages.element(use.type if facts is None else facts.type)
        item_use = TypeUseBinding(
            id=replace(use.id, use_site=_child(array, "items"), schema_site=expected, name=helper.name),
            state="bound",
            type=item,
            reason=None,
            schema=expected,
        )
        return (
            item,
            tokens[0] if tokens else None,
            expected,
            item_use,
        )

    def results(
        self, helper: Helper, spec: OperationSpec, problems: list[Diagnostic]
    ) -> tuple[TypeUseBinding, FinalPythonType, tuple[ItemStep, ...], TypeNode, UseBinding] | None:
        """Check the result array: one JSON success response whose pointer names an array of models.

        Return the response use, the result item type, the accessor steps, the item's node, and the model binding.
        """
        at, name, label = f"{helper.at}.results", helper.name, _label(spec)
        successes = [response for response in spec.responses if response.success]
        if (page := _page_use(successes)) is None or (binding := self.polls.model(successes[0])) is None:
            message = f"{label} must declare exactly one JSON success response for the batch helper {name!r}"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{helper.at}.operation", message, spec))
            return None
        pointer = helper.tree["results"]["pointer"]
        reached = self.pages.walk(binding, pointer)
        if reached == "unsupported":
            message = (
                f"The results pointer {pointer!r} of {name!r} reads through a union or map, which is not supported yet"
            )
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", at, message, spec))
            return None
        if reached == "absent":
            message = f"The results pointer {pointer!r} of {name!r} names no property of the {label} response"
            problems.append(_problem("E_CONFIG_VALUE", "config", at, message, spec))
            return None
        models = {model.symbol: model for model in binding.models}
        if (
            not isinstance(array := reached.node, ArrayNode)
            or array.container not in _SEQUENCES
            or not isinstance(_unwrapped(array.item, models, []), ModelNode)
        ):
            message = f"The results pointer {pointer!r} of {name!r} selects no JSON array of objects of {label}"
            problems.append(_problem("E_CONFIG_VALUE", "config", at, message, spec))
            return None
        member = reached.member
        result = self.pages.element(
            page.type if member is None or member.model_facts is None else member.model_facts.type
        )
        return page, _nonnull(result), reached.steps, array.item, binding

    def member(  # noqa: PLR0913, PLR0917
        self,
        helper: Helper,
        spec: OperationSpec,
        binding: UseBinding,
        node: TypeNode,
        key: Literal["success", "error"],
        problems: list[Diagnostic],
    ) -> BatchMemberSpec | None:
        """Check that a result member names a property of the result item whose schema is the declared one."""
        at, name = f"{helper.at}.{key}", helper.name
        declared = helper.tree[key]
        pointer = declared["pointer"]
        reached = self.pages.walk(binding, pointer, node)
        if reached == "unsupported":
            message = (
                f"The {key} pointer {pointer!r} of {name!r} reads through a union or map, which is not supported yet"
            )
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", f"{at}.pointer", message, spec))
            return None
        if (
            isinstance(reached, str)
            or (member := reached.member) is None
            or member.schema is None
            or (member.model_facts is None)
        ):
            message = f"The {key} pointer {pointer!r} of {name!r} names no property of a result item"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.pointer", message, spec))
            return None
        reference = declared["schema"]
        if (expected := self.pages.item_schema(reference)) is None:
            message = f"The {key} schema {reference.pointer!r} of {name!r} does not exist in its document"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.schema", message, spec))
            return None
        if self.pages.wire.schema(self.pages.nonnull(member.schema))[0] != expected:
            message = f"The {key} schema {reference.pointer!r} of {name!r} is not the schema its pointer reads"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.schema", message, spec))
            return None
        return BatchMemberSpec(value=_nonnull(member.model_facts.type), steps=reached.steps)

    def correlation(  # noqa: PLR0913, PLR0917
        self,
        helper: Helper,
        spec: OperationSpec,
        item_schema: SourceLocation,
        binding: UseBinding,
        node: TypeNode,
        problems: list[Diagnostic],
    ) -> Literal["str", "int"] | None:
        """Check declared IDs: a string or integer property of the item schema and the same type in a result item."""
        correlation, name = helper.tree["correlation"], helper.name
        if correlation["kind"] == "position":
            return None
        at = f"{helper.at}.correlation"
        found = self.pages.declared(item_schema, correlation["input"])
        kind = None if found is None else _IDS.get(self.pages.types(self.pages.nonnull(found)) or frozenset())
        if found is None or kind is None:
            message = (
                f"The input ID pointer {correlation['input']!r} of {name!r} names no string or integer property of "
                "the item schema"
            )
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.input", message, spec))
            return None
        pointer = correlation["result"]
        if owner := next((key for key in ("success", "error") if _inside(pointer, helper.tree[key]["pointer"])), None):
            message = (
                f"The result ID pointer {pointer!r} of {name!r} reads the {owner} member, which a result of the other "
                "outcome does not carry"
            )
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.result", message, spec))
            return None
        reached = self.pages.walk(binding, pointer, node)
        member = None if isinstance(reached, str) else reached.member
        types = None if member is None or member.schema is None else self.pages.types(self.pages.nonnull(member.schema))
        if types is None or _IDS.get(types) != kind:
            message = (
                f"The result ID pointer {correlation['result']!r} of {name!r} names no property of a result item of "
                f"the input ID's type, {'string' if kind == 'str' else 'integer'}"
            )
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.result", message, spec))
            return None
        return kind


def _inside(pointer: str, member: str) -> bool:
    """Return whether a pointer names a member, or a member inside it."""
    return f"{pointer}/".startswith(f"{member}/")


def _model(node: TypeNode, models: Mapping[str, Any]) -> ModelNode:
    """Return the model of an object body, through a nullable single member."""
    found = _unwrapped(node, models, [])
    assert isinstance(found, ModelNode)
    return found


def plan_batches(
    protocols: Protocols | None, plan: ClientPlan, codecs: CodecPlan, wire: WirePlan, request: TargetRequest
) -> tuple[tuple[BatchSpec, ...], dict[str, list[Diagnostic]]]:
    """Plan every enabled batch helper, returning them and each checked helper's problems."""
    if protocols is None or not any(helper.enabled and helper.kind == "batch" for helper in protocols.helpers):
        return (), {}
    operations = {spec.contract.id: spec for spec in plan.operations}
    batches = _Batches(_Polls(_Pages(protocols, plan, codecs, wire, request), operations, codecs, protocols))
    specs: list[BatchSpec] = []
    problems: dict[str, list[Diagnostic]] = {}
    for helper in protocols.helpers:
        if not helper.enabled or helper.kind != "batch":
            continue
        planned, problems[helper.name] = batches.helper(helper)
        if planned is not None:
            specs.append(planned)
    return tuple(specs), problems
