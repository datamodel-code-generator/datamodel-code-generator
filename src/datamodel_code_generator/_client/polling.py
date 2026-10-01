"""Plan the polling helpers of a client target and the checks they must pass.

A helper's state, binding values, and result are read from a response's wire value, a header, or the status; each is
checked against the operations it creates, polls, and fetches the result with, and the request targets it writes.
Typed accessors the package generates read a result through the fields of its response model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, cast

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.pagination import (
    ItemStep,
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
    credential_place,
)
from datamodel_code_generator._codec_type_source import Namespace, TypeSource
from datamodel_code_generator._generation_contract import AnnotatedType, NoneType, UnionType

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._client.pagination import _Types
    from datamodel_code_generator._client.plan import ClientPlan, OperationSpec, ResponseSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper
    from datamodel_code_generator._codec_declarations import OperationRef
    from datamodel_code_generator._generation_contract import FinalPythonType, OperationId, TypeUseBinding
    from datamodel_code_generator._openapi_codec_plan import CodecPlan
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.bindings import UseBinding

STATES: Final = ("pending", "succeeded", "failed", "cancelled")
_KINDS: Final = {"polling": "polling", "sse": "SSE", "ndjson": "NDJSON"}
_MIN_SUCCESS: Final = 200
_MAX_SUCCESS: Final = 299


@dataclass(frozen=True, slots=True, kw_only=True)
class PollingSpec:
    """A polling helper ready to render: its operations, poll and result types, and result accessors.

    `create_uses` are the uses of the create operation's success media. An inline result is read from the final poll
    by `steps` and has the type `value`; a fetched one is the response of `fetch`, whose use is `fetch_use`; a helper
    without a result gives None. `immediate` reads the result from a create response with an immediate status.
    `cancel` is the operation of a declared remote cancellation, whose success media have the uses `cancel_uses`.
    """

    helper: Helper
    operation: OperationSpec
    poll: OperationSpec
    poll_use: TypeUseBinding
    create_uses: tuple[TypeUseBinding, ...] = ()
    value: FinalPythonType | None = None
    steps: tuple[ItemStep, ...] = ()
    fetch: OperationSpec | None = None
    fetch_use: TypeUseBinding | None = None
    immediate: tuple[ItemStep, ...] | None = None
    schemas: tuple[Mapping[str, str], ...] = ()
    cancel: OperationSpec | None = None
    cancel_uses: tuple[TypeUseBinding, ...] = ()

    @property
    def result(self) -> Literal["inline", "operation", "none"]:
        """Return how the helper gives its result."""
        return self.helper.tree["result"]["kind"]


@dataclass(frozen=True, slots=True)
class _Source:
    """A response a selector reads: its operation, its declaration, and the model binding of its JSON body, if any."""

    spec: OperationSpec
    response: ResponseSpec
    binding: UseBinding | None


@dataclass(frozen=True, slots=True)
class _Result:
    """A checked result: its type, how its accessor reads it, and its schema; or the fetch's operation and use."""

    value: FinalPythonType | None = None
    steps: tuple[ItemStep, ...] = ()
    schema: Mapping[str, str] | None = None
    fetch: OperationSpec | None = None
    fetch_use: TypeUseBinding | None = None


def _response(spec: OperationSpec, status: int) -> ResponseSpec | None:
    """Return the success response a status selects, by its exact key, its hundreds range, or default, or None."""
    if not (_MIN_SUCCESS <= status <= _MAX_SUCCESS or status in spec.success_statuses):
        return None
    keys = {response.status: response for response in spec.responses if response.success}
    return keys.get(str(status)) or keys.get(f"{status // 100}XX") or keys.get("default")


def _nonnull(value: FinalPythonType) -> FinalPythonType:
    """Return a type without None, through its metadata, as a result that reads null is refused at runtime."""
    if not isinstance(base := value.base if isinstance(value, AnnotatedType) else value, UnionType):
        return value
    members = tuple(member for member in base.members if not isinstance(member, NoneType))
    return members[0] if len(members) == 1 else UnionType(members, base.preserve_order)


class _Polls:
    """Check and plan every enabled polling helper of a client target."""

    def __init__(
        self, pages: _Pages, operations: Mapping[OperationId, OperationSpec], codecs: CodecPlan, protocols: Protocols
    ) -> None:
        """Keep the shared pagination checks, the selected operations by identity, and a static type speller."""
        self.pages = pages
        self.operations = operations
        self.protocols = protocols
        self.spelling = TypeSource(Namespace(()), dict(codecs.imports), lambda module, name: f"{module}.{name}")

    def spec(self, reference: OperationRef) -> OperationSpec:
        """Return the selected operation a helper reference names."""
        return self.operations[self.protocols.operations[reference].id]

    def model(self, response: ResponseSpec) -> UseBinding | None:
        """Return the native model binding of a response's one JSON media, or None for any other response."""
        if (use := _page_use([response])) is None or (binding := self.pages.bindings.get(use.id)) is None:
            return None
        native = binding.projection_mode == "native" and binding.converter_strategy != "registered_adapter"
        return binding if native else None

    def helper(self, helper: Helper) -> tuple[PollingSpec | None, list[Diagnostic]]:
        """Check one enabled helper against its operations, and plan it when every check passes."""
        tree = helper.tree
        at = helper.at
        create, poll = self.spec(tree["create"]), self.spec(tree["poll"])
        problems: list[Diagnostic] = []
        created = self.created(helper, create, problems)
        problems.extend(self.expiry(helper, created))
        successes = [response for response in poll.responses if response.success]
        page = _page_use(successes)
        if page is None or (binding := self.model(successes[0])) is None:
            message = (
                f"{_label(poll)} must declare exactly one JSON success response for the polling helper {helper.name!r}"
            )
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.poll", message, poll))
            return None, problems
        polled = _Source(poll, successes[0], binding)
        problems.extend(self.states(helper, polled))
        sources = {"initial": created, "previous": [*created, polled]}
        problems.extend(self.bindings(helper, poll, tree["bindings"], f"{at}.bindings", sources, "binding"))
        cancel = self.cancel(helper, sources, problems)
        if (result := self.result(helper, polled, created, problems)) is None:
            return None, problems
        immediate = self.immediate(helper, create, result, problems)
        if problems:
            return None, problems
        schemas = tuple(item for item in (result.schema, immediate and immediate[1]) if item is not None)
        return PollingSpec(
            helper=helper,
            operation=create,
            poll=poll,
            poll_use=page,
            create_uses=tuple(
                use
                for response in create.responses
                if response.success
                for media in response.media
                if (use := media.use) is not None
            ),
            value=result.value,
            steps=result.steps,
            fetch=result.fetch,
            fetch_use=result.fetch_use,
            immediate=None if immediate is None else immediate[0],
            schemas=schemas,
            cancel=cancel,
            cancel_uses=()
            if cancel is None
            else tuple(
                use
                for response in cancel.responses
                if response.success
                for media in response.media
                if (use := media.use) is not None
            ),
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
        at, spec = f"{helper.at}.remote_cancel", self.spec(declared["operation"])
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
            if (response := _response(create, status)) is None:
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
            if selector["from"] == "body" and source.binding is None:
                message = (
                    f"The {what} of {helper.name!r} reads the body of the {response.status} response of "
                    f"{_label(spec)}, which is no JSON model"
                )
                return _problem("E_CONFIG_VALUE", "config", at, message, spec)
            binding = cast("UseBinding", source.binding)
            types = self.pages.read(helper, spec, binding, response.headers, selector, at, what)
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
    ) -> Iterator[Diagnostic]:
        """Check each binding's value against its target, that no two writes overlap, and what the operation requires.

        A cookie or another credential position is never written, a body written to must be JSON only, and literals
        that make a path segment a dot segment are no path values. `written` are the targets the helper writes
        besides, which no binding overlaps, and only a `complete` request, one the bindings and those writes alone
        make, must write every required one.
        """
        name, label = helper.name, _label(spec)
        writes = list(written)
        dotted = _dot_literals(spec, bindings)
        for index, item in enumerate(bindings):
            where, owner, value, target = f"{at}[{index}]", f"{what} {index}", item["value"], item["target"]
            pages = self.pages
            if (place := credential_place(target, pages.secret_headers, pages.secret_queries)) is not None:
                message = f"The {owner} of {name!r} writes {place}, which carries credentials no helper writes"
                yield _problem("E_CONFIG_VALUE", "config", f"{where}.target", message, spec)
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
        yield from self.required(helper, spec, [key for key, _ in writes], at, complete=complete)

    @staticmethod
    def required(
        helper: Helper, spec: OperationSpec, written: list[tuple[str, ...]], at: str, *, complete: bool = True
    ) -> Iterator[Diagnostic]:
        """Refuse a required parameter or body of a complete request no binding writes, and a body other than JSON.

        A written body must have a default media type, since a request the helper builds names none.
        """
        label = _label(spec)
        locations = {key[0] for key in written}
        for parameter in spec.parameters if complete else ():
            location = parameter.location
            unwritten = (
                location not in locations
                if location == "querystring"
                else _target_key({"in": location, "name": parameter.wire_name}) not in written
            )
            if parameter.required and unwritten:
                message = (
                    f"{label} requires the {location} parameter {parameter.wire_name!r}, which no binding of "
                    f"{helper.name!r} writes"
                )
                yield _problem("E_CONFIG_VALUE", "config", at, message, spec)
        if (body := spec.body) is None:
            return
        if "body" in locations and any(media.kind != "json" for media in body.media):
            message = (
                f"The {_KINDS[helper.kind]} helper {helper.name!r} writes a request body of {label} other than JSON, "
                "which is not supported yet"
            )
            yield _problem("E_CLIENT_UNSUPPORTED", "target", at, message, spec)
        elif "body" in locations and body.default is None:
            message = (
                f"The {_KINDS[helper.kind]} helper {helper.name!r} writes a request body of {label}, which has no "
                "default media type; set the operation's request_media_type"
            )
            yield _problem("E_CONFIG_VALUE", "config", at, message, spec)
        elif complete and body.required and "body" not in locations:
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
        assert source.binding is not None
        pointer = selector["pointer"]
        if (reached := self.pages.walk(source.binding, pointer)) == "unsupported":
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
        if (expected := self.pages.item_schema(reference)) is None:
            message = f"The {what} schema {reference.pointer!r} of {name!r} does not exist in its document"
            return _problem("E_CONFIG_VALUE", "config", f"{at}.schema", message, spec)
        if self.pages.wire.schema(self.pages.nonnull(member.schema))[0] != expected:
            message = f"The {what} schema {reference.pointer!r} of {name!r} is not the schema its pointer reads"
            return _problem("E_CONFIG_VALUE", "config", f"{at}.schema", message, spec)
        schema = {"document": self.protocols.documents[reference], "pointer": reference.pointer}
        return _Result(value=_nonnull(member.model_facts.type), steps=reached.steps, schema=schema)

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
                fetch = self.spec(result["operation"])
                successes = [response for response in fetch.responses if response.success]
                if (use := _page_use(successes)) is None or self.model(successes[0]) is None:
                    message = (
                        f"{_label(fetch)} must declare exactly one JSON success response for the result of "
                        f"{helper.name!r}"
                    )
                    checked = _problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, fetch)
                else:
                    sources = {"initial": created, "previous": [polled]}
                    problems.extend(
                        self.bindings(helper, fetch, result["bindings"], f"{at}.bindings", sources, "result binding")
                    )
                    checked = _Result(fetch=fetch, fetch_use=use)
        if isinstance(checked, Diagnostic):
            problems.append(checked)
            return None
        return checked

    def immediate(
        self, helper: Helper, create: OperationSpec, result: _Result, problems: list[Diagnostic]
    ) -> tuple[tuple[ItemStep, ...], Mapping[str, str]] | None:
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
            if _response(create, status) is None:
                message = f"The immediate status {status} of {name!r} selects no success response of {label}"
                problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.statuses[{index}]", message, create))
        successes = [response for response in create.responses if response.success]
        uses = [_page_use([response]) for response in successes]
        spelled = {self.spelling.static(use.type) for use in uses if use is not None and use.type is not None}
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
        assert reached.schema is not None
        if (given := self.spelling.static(reached.value)) != (wanted := self.spelling.static(expected)):
            message = f"The immediate result of {name!r} reads {given}, which is not its result type {wanted}"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.selector", message, create))
            return None
        return reached.steps, reached.schema


def plan_polling(
    protocols: Protocols | None, plan: ClientPlan, codecs: CodecPlan, wire: WirePlan, request: TargetRequest
) -> tuple[tuple[PollingSpec, ...], dict[str, list[Diagnostic]]]:
    """Plan every enabled polling helper, returning them and each checked helper's problems."""
    if protocols is None:
        return (), {}
    operations = {spec.contract.id: spec for spec in plan.operations}
    polls = _Polls(_Pages(protocols, plan, codecs, wire, request), operations, codecs, protocols)
    specs: list[PollingSpec] = []
    problems: dict[str, list[Diagnostic]] = {}
    for helper in protocols.helpers:
        if not helper.enabled or helper.kind != "polling":
            continue
        planned, problems[helper.name] = polls.helper(helper)
        if planned is not None:
            specs.append(planned)
    return tuple(specs), problems
