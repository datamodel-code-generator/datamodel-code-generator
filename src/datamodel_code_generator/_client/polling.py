"""Plan the polling helpers of a client target and the checks they must pass.

A helper's state, binding values, and result are read from a response's wire value, a header, or the status; each is
checked against the operations it creates, polls, and fetches the result with, and the request targets it writes.
Typed accessors the package generates read a result through the fields of its response model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, cast

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.naming import HELPER_KINDS
from datamodel_code_generator._client.pagination import (
    _dot_literals,
    _fits,
    _json_type,
    _label,
    _listed,
    _overlaps,
    _page_use,
    _Pages,
    _problem,
    _target_key,
)
from datamodel_code_generator._client.plan import success_response
from datamodel_code_generator._runtime.model_codecs.parameters import parameter_key
from datamodel_code_generator._target_module import TargetModule, TypeNames
from datamodel_code_generator._target_types import without_none

if TYPE_CHECKING:
    from collections.abc import Container, Iterator, Mapping

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._client.model_facts import ItemStep, ModelFacts
    from datamodel_code_generator._client.pagination import _Types
    from datamodel_code_generator._client.plan import ClientPlan, OperationSpec, ResponseSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper
    from datamodel_code_generator._target_contract import TypeUseBinding, TypeUseId, TypeView

STATES: Final = ("pending", "succeeded", "failed", "cancelled")


@dataclass(frozen=True, slots=True, kw_only=True)
class PollingSpec:
    """A polling helper ready to render: its operations, poll and result types, and result accessors.

    An inline result is read from the final poll by `steps` and has the type `value`; a fetched one is the response of
    `fetch`, whose use is `fetch_use`; a helper without a result gives None. `immediate` reads the result from a create
    response with an immediate status. `cancel` is the operation of a declared remote cancellation.
    """

    helper: Helper
    operation: OperationSpec
    poll: OperationSpec
    poll_use: TypeUseBinding
    value: TypeView | None = None
    steps: tuple[ItemStep, ...] = ()
    fetch: OperationSpec | None = None
    fetch_use: TypeUseBinding | None = None
    immediate: tuple[ItemStep, ...] | None = None
    cancel: OperationSpec | None = None

    @property
    def result(self) -> Literal["inline", "operation", "none"]:
        """Return how the helper gives its result."""
        return self.helper.tree["result"]["kind"]


@dataclass(frozen=True, slots=True)
class _Source:
    """A response a selector reads: its operation, its declaration, and the type of its JSON body with a codec."""

    spec: OperationSpec
    response: ResponseSpec
    body: TypeView | None


@dataclass(frozen=True, slots=True)
class _Result:
    """A checked result: its type and how its accessor reads it, or the fetch's operation and use."""

    value: TypeView | None = None
    steps: tuple[ItemStep, ...] = ()
    fetch: OperationSpec | None = None
    fetch_use: TypeUseBinding | None = None


class _Polls:
    """Check and plan every enabled polling helper of a client target."""

    def __init__(self, pages: _Pages) -> None:
        """Keep the shared pagination checks and a static type speller."""
        self.pages = pages
        self.spelling = TargetModule(TypeNames(pages.request.batch), qualified=True)

    def model(self, response: ResponseSpec) -> TypeView | None:
        """Return the type of a response's one JSON media with a codec, or None for any other response."""
        return None if (use := _page_use([response])) is None or use.id not in self.pages.codecs else use.type

    def helper(self, helper: Helper, create: OperationSpec) -> tuple[PollingSpec | None, list[Diagnostic]]:
        """Check one enabled helper against its operations, and plan it when every check passes."""
        tree = helper.tree
        at = helper.at
        poll = self.pages.spec(tree["poll"])
        problems: list[Diagnostic] = []
        created = self.created(helper, create, problems)
        problems.extend(self.expiry(helper, created))
        if isinstance(single := self.pages.single(poll, f"{at}.poll", f"polling helper {helper.name!r}"), Diagnostic):
            problems.append(single)
            return None, problems
        response, page, body = single
        polled = _Source(poll, response, body)
        problems.extend(self.states(helper, polled))
        sources = {"initial": created, "previous": [*created, polled]}
        problems.extend(self.bindings(helper, poll, tree["bindings"], f"{at}.bindings", sources, "binding"))
        cancel = self.cancel(helper, sources, problems)
        if (result := self.result(helper, polled, created, problems)) is None:
            return None, problems
        immediate = self.immediate(helper, create, result, problems)
        if problems:
            return None, problems
        return PollingSpec(
            helper=helper,
            operation=create,
            poll=poll,
            poll_use=page,
            value=result.value,
            steps=result.steps,
            fetch=result.fetch,
            fetch_use=result.fetch_use,
            immediate=immediate,
            cancel=cancel,
        ), problems

    def expiry(self, helper: Helper, created: list[_Source]) -> Iterator[Diagnostic]:
        """Check that a declared server expiry reads strings from every accepted create response."""
        if (read := helper.tree.get("expires_at")) is None or not created:
            return
        at = f"{helper.at}.expires_at"
        if isinstance(types := self.read(helper, created, read, at, "expiry"), Diagnostic):
            yield types
        elif not _fits("string", types):
            spec = created[0].spec
            message = (
                f"The expiry of {helper.name!r} reads {_listed(sorted(cast('frozenset[str]', types)))} values, where "
                "only a string gives a date and time"
            )
            yield _problem("E_CONFIG_VALUE", "config", at, message, spec)

    def cancel(
        self, helper: Helper, sources: Mapping[str, list[_Source]], problems: list[Diagnostic]
    ) -> OperationSpec | None:
        """Check a declared remote cancellation: a success response its receipt holds, and its bindings."""
        if (declared := helper.tree.get("remote_cancel")) is None:
            return None
        at, spec = f"{helper.at}.remote_cancel", self.pages.spec(declared["operation"])
        if not any(response.success for response in spec.responses):
            message = f"{_label(spec)} must declare a success response for the remote cancel of {helper.name!r}"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, spec))
            return None
        found = list(self.bindings(helper, spec, declared["bindings"], f"{at}.bindings", sources, "cancel binding"))
        problems.extend(found)
        return None if found else spec

    def created(self, helper: Helper, create: OperationSpec, problems: list[Diagnostic]) -> list[_Source]:
        """Return the create responses the accepted statuses select, refusing a status no success response declares."""
        sources: list[_Source] = []
        for index, status in enumerate(helper.tree["accepted_statuses"]):
            if (response := success_response(create, status)) is None:
                message = (
                    f"The accepted status {status} of {helper.name!r} selects no success response of {_label(create)}"
                )
                where = f"{helper.at}.accepted_statuses[{index}]"
                problems.append(_problem("E_CONFIG_VALUE", "config", where, message, create))
                continue
            sources.append(_Source(create, response, self.model(response)))
        return sources

    def read(
        self, helper: Helper, sources: list[_Source], selector: Mapping[str, Any], at: str, what: str
    ) -> _Types | Diagnostic:
        """Return the JSON types a selector reads from every response it may read, or why one cannot give a value.

        A body pointer needs a JSON model body in each response.
        """
        found: set[str] | None = set()
        for source in sources:
            spec, response = source.spec, source.response
            if selector["from"] == "body" and source.body is None:
                message = (
                    f"The {what} of {helper.name!r} reads the body of the {response.status} response of "
                    f"{_label(spec)}, which is no JSON model"
                )
                return _problem("E_CONFIG_VALUE", "config", at, message, spec)
            body = cast("TypeView", source.body)
            types = self.pages.read(helper, spec, body, response.headers, selector, at, what)
            if isinstance(types, Diagnostic):
                return types
            found = None if types is None or found is None else found | types
        return None if found is None else frozenset(found)

    def states(self, helper: Helper, polled: _Source) -> Iterator[Diagnostic]:
        """Check that the state is read from the poll response and that each declared state has a type it reads."""
        tree = helper.tree
        types = self.read(helper, [polled], tree["state"], f"{helper.at}.state", "state")
        if isinstance(types, Diagnostic):
            yield types
            return
        for name in STATES:
            for index, value in enumerate(tree[name]):
                if not _fits(kind := _json_type(value), types):
                    message = f"The state {value!r} of {helper.name!r} is {kind}, which its state never reads"
                    yield _problem("E_CONFIG_VALUE", "config", f"{helper.at}.{name}[{index}]", message, polled.spec)

    def bindings(  # noqa: PLR0913, PLR0917
        self,
        helper: Helper,
        spec: OperationSpec,
        bindings: list[Mapping[str, Any]],
        at: str,
        sources: Mapping[str, list[_Source]],
        what: str,
        *,
        written: tuple[tuple[tuple[str, ...], str], ...] = (),
        complete: bool = True,
        required: bool = True,
    ) -> Iterator[Diagnostic]:
        """Check each binding's value against its target, that no two writes overlap, and what the operation requires.

        A cookie or another credential position is never written, a body written to must be JSON only, and literals
        that make a path segment a dot segment are no path values. `written` are the targets the helper writes
        besides, which no binding overlaps, and only a `complete` request, one the bindings and those writes alone
        make, must write every required one; an upload checks what its calls require itself, without `required`.
        """
        name, label = helper.name, _label(spec)
        writes = list(written)
        dotted = _dot_literals(spec, bindings)
        for index, item in enumerate(bindings):
            where, owner, value, target = f"{at}[{index}]", f"{what} {index}", item["value"], item["target"]
            if (problem := self.pages.credential(helper, spec, target, f"{where}.target", owner)) is not None:
                yield problem
                continue
            key = _target_key(target)
            if (
                clash := next(((other, holder) for other, holder in writes if _overlaps(key, other)), None)
            ) is not None:
                relation = "the same target as" if clash[0] == key else "a target overlapping that of"
                message = f"The {owner} of {name!r} writes {relation} its {clash[1]}"
                yield _problem("E_CONFIG_CONFLICT", "config", f"{where}.target", message, spec)
                continue
            writes.append((key, owner))
            if value.get("source") == "input":
                message = f"The {owner} of {name!r} reads the helper's input, which is not supported yet"
                yield _problem("E_CLIENT_UNSUPPORTED", "target", f"{where}.value.source", message, spec)
                continue
            if "literal" in value:
                if index in dotted:
                    message = (
                        f"The {owner} of {name!r} gives {value['literal']!r}, which makes the segment of the path "
                        f"parameter {target['name']!r} of {label} a dot segment"
                    )
                    yield _problem("E_CONFIG_VALUE", "config", f"{where}.value.literal", message, spec)
                    continue
                types: _Types | Diagnostic = frozenset({_json_type(value["literal"])})
            else:
                types = self.read(helper, sources[value["source"]], value["selector"], f"{where}.value.selector", owner)
            if isinstance(types, Diagnostic):
                yield types
                continue
            yield from self.pages.fits(helper, spec, target, types, f"{where}.target", owner, null=True)
        if required:
            yield from self.required(helper, spec, [key for key, _ in writes], at, complete=complete)

    @staticmethod
    def required(  # noqa: PLR0913
        helper: Helper,
        spec: OperationSpec,
        written: list[tuple[str, ...]],
        at: str,
        *,
        complete: bool = True,
        body: bool = True,
    ) -> Iterator[Diagnostic]:
        """Refuse a required parameter or body of a complete request nothing writes, and a body other than JSON.

        A written body must have a default media type, since a poll, a result fetch, a remote cancel, or an upload call
        names none; an upload's append, whose body is its chunk, checks no `body`.
        """
        label = _label(spec)
        locations = {key[0] for key in written}
        for parameter in spec.parameters if complete else ():
            location = parameter.location
            unwritten = (
                location not in locations
                if location == "querystring"
                else parameter_key(location, parameter.wire_name) not in written
            )
            if parameter.required and unwritten:
                message = (
                    f"{label} requires the {location} parameter {parameter.wire_name!r}, which no binding of "
                    f"{helper.name!r} writes"
                )
                yield _problem("E_CONFIG_VALUE", "config", at, message, spec)
        if (request := spec.body) is None or not body:
            return
        kind = HELPER_KINDS[helper.kind].label
        if "body" in locations and any(media.kind != "json" for media in request.media):
            message = (
                f"The {kind} helper {helper.name!r} writes a request body of {label} other than JSON, "
                "which is not supported yet"
            )
            yield _problem("E_CLIENT_UNSUPPORTED", "target", at, message, spec)
        elif "body" in locations and request.default is None:
            message = (
                f"The {kind} helper {helper.name!r} writes a request body of {label}, which has no "
                "default media type; set the operation's request_media_type"
            )
            yield _problem("E_CONFIG_VALUE", "config", at, message, spec)
        elif complete and request.required and "body" not in locations:
            message = f"{label} requires a request body, which no binding of {helper.name!r} writes"
            yield _problem("E_CONFIG_VALUE", "config", at, message, spec)

    def reached(  # noqa: PLR0913, PLR0917
        self, helper: Helper, source: _Source, selector: Mapping[str, Any], reference: Any, at: str, what: str
    ) -> _Result | Diagnostic:
        """Return the type and accessor of the property a body pointer names, checked against the declared schema."""
        name, spec = helper.name, source.spec
        if selector["from"] != "body":
            message = f"The {what} of {name!r} reads a {selector['from']}, where only a body pointer reads a result"
            return _problem("E_CONFIG_VALUE", "config", f"{at}.selector", message, spec)
        assert source.body is not None
        pointer = selector["pointer"]
        if (reached := self.pages.walk(source.body, pointer)) == "unsupported":
            message = (
                f"The {what} pointer {pointer!r} of {name!r} reads through a union or map, which is not supported yet"
            )
            return _problem("E_CLIENT_UNSUPPORTED", "target", f"{at}.selector", message, spec)
        if (
            isinstance(reached, str)
            or (member := reached.member) is None
            or member.schema is None
            or member.model_facts is None
        ):
            message = f"The {what} pointer {pointer!r} of {name!r} names no property of the {_label(spec)} response"
            return _problem("E_CONFIG_VALUE", "config", f"{at}.selector", message, spec)
        value, noun, mismatch = member.model_facts.type, f"{what} schema", "the schema its pointer reads"
        if found := self.pages.schema(helper, spec, reference, value, "response", f"{at}.schema", noun, mismatch):
            return found
        return _Result(value=without_none(value), steps=reached.steps)

    def result(
        self, helper: Helper, polled: _Source, created: list[_Source], problems: list[Diagnostic]
    ) -> _Result | None:
        """Check the result: an inline one in the final poll, a fetch and its bindings, or none."""
        result, at = helper.tree["result"], f"{helper.at}.result"
        checked: _Result | Diagnostic = _Result()
        match result["kind"]:
            case "inline":
                checked = self.reached(helper, polled, result["selector"], result["schema"], at, "result")
            case "operation":
                fetch = self.pages.spec(result["operation"])
                single = self.pages.single(fetch, f"{at}.operation", f"result of {helper.name!r}")
                if isinstance(single, Diagnostic):
                    checked = single
                else:
                    sources = {"initial": created, "previous": [polled]}
                    problems.extend(
                        self.bindings(helper, fetch, result["bindings"], f"{at}.bindings", sources, "result binding")
                    )
                    checked = _Result(fetch=fetch, fetch_use=single[1])
        if isinstance(checked, Diagnostic):
            problems.append(checked)
            return None
        return checked

    def immediate(
        self, helper: Helper, create: OperationSpec, result: _Result, problems: list[Diagnostic]
    ) -> tuple[ItemStep, ...] | None:
        """Check an immediate result: a result kind that gives one, JSON success responses of one model, and its type.

        Its accessor reads the decoded create response, so every success response of the create operation must be the
        same JSON model, and the property it reads must have the helper's result type.
        """
        if (immediate := helper.tree.get("immediate_result")) is None:
            return None
        at, name, label = f"{helper.at}.immediate_result", helper.name, _label(create)
        if helper.tree["result"]["kind"] == "none":
            message = f"The immediate result of {name!r} gives a result, which a helper without a result never has"
            problems.append(_problem("E_CONFIG_VALUE", "config", at, message, create))
            return None
        for index, status in enumerate(immediate["statuses"]):
            if success_response(create, status) is None:
                message = f"The immediate status {status} of {name!r} selects no success response of {label}"
                problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.statuses[{index}]", message, create))
        successes = [response for response in create.responses if response.success]
        uses = [_page_use([response]) for response in successes]
        spelled = {self.spelling.hint(use.type) for use in uses if use is not None and use.type is not None}
        model = self.model(successes[0]) if successes else None
        if model is None or None in uses or len(spelled) != 1:
            message = (
                f"The immediate result of {name!r} reads {label}, whose success responses are not all one JSON model, "
                "which is not supported yet"
            )
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", at, message, create))
            return None
        source = _Source(create, successes[0], model)
        reached = self.reached(helper, source, immediate["selector"], immediate["schema"], at, "immediate result")
        if isinstance(reached, Diagnostic):
            problems.append(reached)
            return None
        expected = result.value if result.fetch_use is None else result.fetch_use.type
        assert expected is not None
        assert reached.value is not None
        if (given := self.spelling.hint(reached.value)) != (wanted := self.spelling.hint(expected)):
            message = f"The immediate result of {name!r} reads {given}, which is not its result type {wanted}"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.selector", message, create))
            return None
        return reached.steps


def plan_polling(
    protocols: Protocols | None,
    plan: ClientPlan,
    facts: ModelFacts,
    codecs: Container[TypeUseId],
    request: TargetRequest,
) -> tuple[tuple[PollingSpec, ...], dict[str, list[Diagnostic]]]:
    """Plan every enabled polling helper, returning them and each checked helper's problems."""
    if protocols is None:
        return (), {}
    polls = _Polls(_Pages(protocols, plan, facts, codecs, request))
    return polls.pages.each(("polling",), polls.helper)
