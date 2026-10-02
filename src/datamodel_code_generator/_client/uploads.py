"""Plan the resumable upload helpers of a client target and the checks they must pass.

An upload helper of the offset profile creates an upload, probes the server's offset, appends chunks as binary bodies,
and completes by length or with an operation. Each selector is checked against the responses it reads, and each target
against the operation it writes to; the parts profile, remote abort, and session URLs are not supported yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.pagination import (
    _label,
    _overlaps,
    _page_use,
    _Pages,
    _problem,
    _target_key,
    credential_place,
)
from datamodel_code_generator._client.polling import _Polls, _Source

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._client.plan import ClientPlan, OperationSpec, ParameterSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper
    from datamodel_code_generator._generation_contract import TypeUseBinding
    from datamodel_code_generator._openapi_codec_plan import CodecPlan
    from datamodel_code_generator._openapi_wire_plan import WirePlan

__all__ = ("UploadSpec", "plan_uploads")

_PARAMETERS: Final = frozenset({"path", "query", "header"})
_INTEGER: Final = frozenset({"integer"})


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadSpec:
    """An upload helper ready to render: its operations, the create parameter the size fills, and its result.

    `create_uses` are the uses of the create operation's success media. A helper completing with an operation returns
    that operation's response, whose use is `completion_use`; one completing by length returns None.
    """

    helper: Helper
    operation: OperationSpec
    probe: OperationSpec
    append: OperationSpec
    create_uses: tuple[TypeUseBinding, ...] = ()
    size: ParameterSpec | None = None
    completion: OperationSpec | None = None
    completion_use: TypeUseBinding | None = None
    schemas: tuple[Mapping[str, str], ...] = ()


class _Checks(_Polls):
    """The polling checks of reads, bindings, and targets, leaving required arguments to the upload checks."""

    @staticmethod
    def required(
        helper: Helper, spec: OperationSpec, written: list[tuple[str, ...]], at: str, *, complete: bool = True
    ) -> Iterator[Diagnostic]:
        """Check nothing here; an upload's required arguments count its offset, length, and size writes too."""
        del helper, spec, written, at, complete
        return iter(())


def _parameter(spec: OperationSpec, target: Mapping[str, Any]) -> ParameterSpec:
    """Return the parameter of an operation that a parameter target names."""
    key = _target_key(target)
    return next(item for item in spec.parameters if _target_key({"in": item.location, "name": item.wire_name}) == key)


class _Uploads:
    """Check and plan every enabled upload helper of a client target."""

    def __init__(self, checks: _Checks) -> None:
        """Keep the shared polling and pagination checks."""
        self.checks = checks
        self.pages = checks.pages

    def helper(self, helper: Helper) -> tuple[UploadSpec | None, list[Diagnostic]]:
        """Check one enabled helper against its operations, and plan it when every check passes."""
        tree = helper.tree
        create, probe, append = (self.checks.spec(tree[name]["operation"]) for name in ("create", "probe", "append"))
        problems = list(self.unsupported(helper, create))
        created = self.created(helper, create, problems)
        sources = {"initial": created, "previous": []}
        size = self.size(helper, create, problems)
        problems.extend(self.expiry(helper, created))
        problems.extend(self.probe(helper, probe, sources))
        problems.extend(self.append(helper, append, sources))
        completion = self.completion(helper, sources, problems)
        if problems:
            return None, problems
        completed, use, schema = completion or (None, None, None)
        return UploadSpec(
            helper=helper,
            operation=create,
            probe=probe,
            append=append,
            create_uses=tuple(
                media.use for response in create.responses if response.success for media in response.media if media.use
            ),
            size=size,
            completion=completed,
            completion_use=use,
            schemas=() if schema is None else (schema,),
        ), problems

    @staticmethod
    def unsupported(helper: Helper, create: OperationSpec) -> Iterator[Diagnostic]:
        """Refuse remote abort and a session URL, which later upload helpers add."""
        tree = helper.tree
        for where, present, what in (
            (f"{helper.at}.abort", "abort" in tree, "remote abort"),
            (f"{helper.at}.create.session_url", "session_url" in tree["create"], "a session URL"),
        ):
            if present:
                message = f"The resumable_upload helper {helper.name!r} declares {what}, which is not supported yet"
                yield _problem("E_CLIENT_UNSUPPORTED", "target", where, message, create)

    def created(self, helper: Helper, create: OperationSpec, problems: list[Diagnostic]) -> list[_Source]:
        """Return the create operation's success responses, refusing an operation without one."""
        sources = [
            _Source(create, response, self.checks.model(response)) for response in create.responses if response.success
        ]
        if not sources:
            message = f"{_label(create)} must declare a success response for the upload helper {helper.name!r}"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{helper.at}.create.operation", message, create))
        return sources

    def count(
        self, helper: Helper, spec: OperationSpec, target: Mapping[str, Any], at: str, what: str
    ) -> Iterator[Diagnostic]:
        """Check that a count goes to a path, query, or header parameter accepting integers, never to credentials."""
        name, pages = helper.name, self.pages
        if (place := credential_place(target, pages.secret_headers, pages.secret_queries, spec)) is not None:
            message = f"The {what} of {name!r} writes {place}, which carries credentials no helper writes"
            yield _problem("E_CONFIG_VALUE", "config", at, message, spec)
        elif target["in"] not in _PARAMETERS:
            message = (
                f"The {what} of {name!r} writes {target['in']!r}, where only a path, query, or header parameter "
                "takes it"
            )
            yield _problem("E_CONFIG_VALUE", "config", at, message, spec)
        else:
            yield from pages.fits(helper, spec, target, _INTEGER, at, what, null=False)

    def size(self, helper: Helper, create: OperationSpec, problems: list[Diagnostic]) -> ParameterSpec | None:
        """Check where the create operation writes the content's size, returning the parameter it fills."""
        if (target := helper.tree["create"].get("size")) is None:
            return None
        found = list(self.count(helper, create, target, f"{helper.at}.create.size", "size"))
        problems.extend(found)
        return None if found else _parameter(create, target)

    def typed(  # noqa: PLR0913, PLR0917
        self, helper: Helper, sources: list[_Source], read: Mapping[str, Any], at: str, what: str, wanted: str
    ) -> Iterator[Diagnostic]:
        """Check that a selector reads values of one JSON type, or header text, from each response, never the status."""
        if not sources:
            return
        spec = sources[0].spec
        if read["from"] == "status":
            message = f"The {what} of {helper.name!r} reads the status, which gives no {what}"
            yield _problem("E_CONFIG_VALUE", "config", at, message, spec)
            return
        types = self.checks.read(helper, sources, read, at, what)
        if isinstance(types, Diagnostic):
            yield types
            return
        allowed = {wanted, *(("string",) if read["from"] == "header" else ())}
        if types is not None and (others := sorted(types - allowed)):
            message = f"The {what} of {helper.name!r} reads {', '.join(others)} values, where only {wanted} values fit"
            yield _problem("E_CONFIG_VALUE", "config", at, message, spec)

    def expiry(self, helper: Helper, created: list[_Source]) -> Iterator[Diagnostic]:
        """Check that a server expiry reads strings from every create response."""
        if (read := helper.tree["create"].get("expires_at")) is not None:
            yield from self.typed(helper, created, read, f"{helper.at}.create.expires_at", "server expiry", "string")

    def bindings(
        self, helper: Helper, spec: OperationSpec, part: str, sources: Mapping[str, list[_Source]]
    ) -> Iterator[Diagnostic]:
        """Check one call's bindings: each reads the create response or is a literal, and fits its target.

        An append's body is its chunk, so no binding of it writes the body.
        """
        at = f"{helper.at}.{part}.bindings"
        bindings = helper.tree[part]["bindings"]
        refused = False
        for index, item in enumerate(bindings):
            where = f"{at}[{index}]"
            if item["value"].get("source") == "previous":
                message = (
                    f"The {part} binding {index} of {helper.name!r} reads the previous response, which is not "
                    "supported yet"
                )
                yield _problem("E_CLIENT_UNSUPPORTED", "target", f"{where}.value.source", message, spec)
            if part == "append" and item["target"]["in"] == "body":
                refused = True
                message = f"The append binding {index} of {helper.name!r} writes the request body, which is the chunk"
                yield _problem("E_CONFIG_VALUE", "config", f"{where}.target", message, spec)
        if not refused:
            yield from self.checks.bindings(helper, spec, bindings, at, sources, f"{part} binding")

    @staticmethod
    def required(
        helper: Helper, spec: OperationSpec, part: str, written: list[tuple[str, ...]], *, body: bool = True
    ) -> Iterator[Diagnostic]:
        """Refuse a required parameter or body of an operation that nothing the helper writes fills.

        A body a binding writes must be JSON with a default media type; an append's body is its chunk instead.
        """
        at = f"{helper.at}.{part}.bindings"
        label = _label(spec)
        locations = {key[0] for key in written}
        for parameter in spec.parameters:
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
        if (request := spec.body) is None or not body:
            return
        if "body" in locations and any(media.kind != "json" for media in request.media):
            message = (
                f"The upload helper {helper.name!r} writes a request body of {label} other than JSON, which is not "
                "supported yet"
            )
            yield _problem("E_CLIENT_UNSUPPORTED", "target", at, message, spec)
        elif "body" in locations and request.default is None:
            message = (
                f"The upload helper {helper.name!r} writes a request body of {label}, which has no default media "
                "type; set the operation's request_media_type"
            )
            yield _problem("E_CONFIG_VALUE", "config", at, message, spec)
        elif request.required and "body" not in locations:
            message = f"{label} requires a request body, which no binding of {helper.name!r} writes"
            yield _problem("E_CONFIG_VALUE", "config", at, message, spec)

    def probe(self, helper: Helper, probe: OperationSpec, sources: Mapping[str, list[_Source]]) -> Iterator[Diagnostic]:
        """Check the probe: its bindings, what it requires, and a success response giving the remote offset."""
        at = f"{helper.at}.probe"
        yield from self.bindings(helper, probe, "probe", sources)
        yield from self.required(helper, probe, "probe", self.keys(helper.tree["probe"]["bindings"]))
        offered = [
            _Source(probe, response, self.checks.model(response)) for response in probe.responses if response.success
        ]
        if not offered:
            message = f"{_label(probe)} must declare a success response for the probe of {helper.name!r}"
            yield _problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, probe)
            return
        read = helper.tree["probe"]["remote_offset"]
        yield from self.typed(helper, offered, read, f"{at}.remote_offset", "remote offset", "integer")

    @staticmethod
    def keys(bindings: list[Mapping[str, Any]]) -> list[tuple[str, ...]]:
        """Return the keys of the targets bindings write."""
        return [_target_key(item["target"]) for item in bindings]

    def append(
        self, helper: Helper, append: OperationSpec, sources: Mapping[str, list[_Source]]
    ) -> Iterator[Diagnostic]:
        """Check the append: one binary request media, its offset and length counts, and its bindings."""
        tree, at, name = helper.tree["append"], f"{helper.at}.append", helper.name
        body = append.body
        if body is None or len(body.media) != 1 or body.media[0].kind != "binary":
            message = f"{_label(append)} must take exactly one binary request media for the chunks of {name!r}"
            yield _problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, append)
        if not any(response.success for response in append.responses):
            message = f"{_label(append)} must declare a success response for the appends of {name!r}"
            yield _problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, append)
        written = self.keys(tree["bindings"])
        for what in ("offset", "length"):
            if (target := tree.get(what)) is None:
                continue
            key, where = _target_key(target), f"{at}.{what}"
            if (
                clash := next((index for index, other in enumerate(written) if _overlaps(key, other)), None)
            ) is not None:
                owner = f"its append binding {clash}" if clash < len(tree["bindings"]) else "its chunk offset"
                message = f"The chunk {what} of {name!r} writes the same target as {owner}"
                yield _problem("E_CONFIG_CONFLICT", "config", where, message, append)
                continue
            written.append(key)
            yield from self.count(helper, append, target, where, f"chunk {what}")
        yield from self.bindings(helper, append, "append", sources)
        yield from self.required(helper, append, "append", written, body=False)

    def completion(
        self, helper: Helper, sources: Mapping[str, list[_Source]], problems: list[Diagnostic]
    ) -> tuple[OperationSpec, TypeUseBinding, Mapping[str, str]] | None:
        """Check a completion operation: one JSON success response of the result schema, and its bindings."""
        completion, at, name = helper.tree["completion"], f"{helper.at}.completion", helper.name
        if completion["kind"] != "operation":
            return None
        spec = self.checks.spec(completion["operation"])
        successes = [response for response in spec.responses if response.success]
        if (use := _page_use(successes)) is None or self.checks.model(successes[0]) is None:
            message = f"{_label(spec)} must declare exactly one JSON success response for the completion of {name!r}"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, spec))
            return None
        reference = completion["result_schema"]
        pages = self.pages
        if (expected := pages.item_schema(reference)) is None:
            message = f"The result schema {reference.pointer!r} of {name!r} does not exist in its document"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.result_schema", message, spec))
            return None
        if use.schema is None or pages.wire.schema(pages.nonnull(use.schema))[0] != expected:
            message = (
                f"The result schema {reference.pointer!r} of {name!r} is not the schema of the {_label(spec)} response"
            )
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.result_schema", message, spec))
            return None
        problems.extend(self.bindings(helper, spec, "completion", sources))
        problems.extend(self.required(helper, spec, "completion", self.keys(completion["bindings"])))
        schema = {"document": self.checks.protocols.documents[reference], "pointer": reference.pointer}
        return spec, use, schema


def plan_uploads(
    protocols: Protocols | None, plan: ClientPlan, codecs: CodecPlan, wire: WirePlan, request: TargetRequest
) -> tuple[tuple[UploadSpec, ...], dict[str, list[Diagnostic]]]:
    """Plan every enabled upload helper, returning them and each checked helper's problems."""
    if protocols is None:
        return (), {}
    operations = {spec.contract.id: spec for spec in plan.operations}
    uploads = _Uploads(_Checks(_Pages(protocols, plan, codecs, wire, request), operations, codecs, protocols))
    specs: list[UploadSpec] = []
    problems: dict[str, list[Diagnostic]] = {}
    for helper in protocols.helpers:
        if not helper.enabled or helper.kind != "resumable_upload":
            continue
        planned, problems[helper.name] = uploads.helper(helper)
        if planned is not None:
            specs.append(planned)
    return tuple(specs), problems
