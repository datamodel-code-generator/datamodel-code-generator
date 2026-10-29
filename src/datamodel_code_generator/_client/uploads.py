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
    _Pages,
    _parameter_key,
    _problem,
    _target_key,
)
from datamodel_code_generator._client.polling import _Polls, _Source

if TYPE_CHECKING:
    from collections.abc import Container, Iterator, Mapping

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._client.model_facts import ModelFacts
    from datamodel_code_generator._client.plan import ClientPlan, OperationSpec, ParameterSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper
    from datamodel_code_generator._target_contract import TypeUseId

__all__ = ("UploadSpec", "plan_uploads")

_PARAMETERS: Final = frozenset({"path", "query", "header"})
_INTEGER: Final = frozenset({"integer"})
_STRING: Final = frozenset({"string"})


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadSpec:
    """An upload helper ready to render: its operations, the create parameter the size fills, and its result.

    A helper completing with an operation returns that operation's response; one completing by length returns None.
    """

    helper: Helper
    operation: OperationSpec
    probe: OperationSpec
    append: OperationSpec
    size: ParameterSpec | None = None
    completion: OperationSpec | None = None


def _parameter(spec: OperationSpec, target: Mapping[str, Any]) -> ParameterSpec:
    """Return the parameter of an operation that a parameter target names."""
    key = _target_key(target)
    return next(item for item in spec.parameters if _parameter_key(item.location, item.wire_name) == key)


class _Uploads:
    """Check and plan every enabled upload helper of a client target."""

    def __init__(self, checks: _Polls) -> None:
        """Keep the shared polling and pagination checks."""
        self.checks = checks
        self.pages = checks.pages

    def helper(self, helper: Helper, create: OperationSpec) -> tuple[UploadSpec | None, list[Diagnostic]]:
        """Check one enabled helper against its operations, and plan it when every check passes."""
        tree = helper.tree
        probe, append = (self.pages.spec(tree[name]["operation"]) for name in ("probe", "append"))
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
        return UploadSpec(
            helper=helper, operation=create, probe=probe, append=append, size=size, completion=completion
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

    def successes(self, spec: OperationSpec) -> list[_Source]:
        """Return an operation's success responses, each with the type of its JSON body with a codec."""
        return [_Source(spec, response, self.checks.model(response)) for response in spec.responses if response.success]

    def created(self, helper: Helper, create: OperationSpec, problems: list[Diagnostic]) -> list[_Source]:
        """Return the create operation's success responses, refusing an operation without one."""
        if not (sources := self.successes(create)):
            message = f"{_label(create)} must declare a success response for the upload helper {helper.name!r}"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{helper.at}.create.operation", message, create))
        return sources

    def count(  # noqa: PLR0913, PLR0917
        self,
        helper: Helper,
        spec: OperationSpec,
        target: Mapping[str, Any],
        at: str,
        what: str,
        types: frozenset[str] = _INTEGER,
    ) -> Iterator[Diagnostic]:
        """Check that a count or checksum goes to a path, query, or header parameter of its types, never credentials."""
        if (problem := self.pages.credential(helper, spec, target, at, what)) is not None:
            yield problem
        elif target["in"] not in _PARAMETERS:
            message = (
                f"The {what} of {helper.name!r} writes {target['in']!r}, where only a path, query, or header parameter "
                "takes it"
            )
            yield _problem("E_CONFIG_VALUE", "config", at, message, spec)
        else:
            yield from self.pages.fits(helper, spec, target, types, at, what, null=False)

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
            yield from self.checks.bindings(helper, spec, bindings, at, sources, f"{part} binding", required=False)

    @staticmethod
    def required(
        helper: Helper, spec: OperationSpec, part: str, written: list[tuple[str, ...]], *, body: bool = True
    ) -> Iterator[Diagnostic]:
        """Refuse a required parameter or body of one call that nothing the helper writes fills."""
        return _Polls.required(helper, spec, written, f"{helper.at}.{part}.bindings", body=body)

    def probe(self, helper: Helper, probe: OperationSpec, sources: Mapping[str, list[_Source]]) -> Iterator[Diagnostic]:
        """Check the probe: its bindings, what it requires, and a success response giving the remote offset."""
        at = f"{helper.at}.probe"
        yield from self.bindings(helper, probe, "probe", sources)
        yield from self.required(helper, probe, "probe", self.keys(helper.tree["probe"]["bindings"]))
        if not (offered := self.successes(probe)):
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
        """Check the append: one binary request media, its offset and length counts, its checksum, and bindings."""
        tree, at, name = helper.tree["append"], f"{helper.at}.append", helper.name
        body = append.body
        if body is None or len(body.media) != 1 or body.media[0].kind != "binary":
            message = f"{_label(append)} must take exactly one binary request media for the chunks of {name!r}"
            yield _problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, append)
        if not any(response.success for response in append.responses):
            message = f"{_label(append)} must declare a success response for the appends of {name!r}"
            yield _problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, append)
        written = self.keys(tree["bindings"])
        owners = [f"its append binding {index}" for index in range(len(written))]
        checksum = tree.get("checksum")
        for what, target, where, types in (
            ("offset", tree["offset"], f"{at}.offset", _INTEGER),
            ("length", tree.get("length"), f"{at}.length", _INTEGER),
            ("checksum", None if checksum is None else checksum["target"], f"{at}.checksum.target", _STRING),
        ):
            if target is None:
                continue
            key = _target_key(target)
            if (
                clash := next((index for index, other in enumerate(written) if _overlaps(key, other)), None)
            ) is not None:
                message = f"The chunk {what} of {name!r} writes the same target as {owners[clash]}"
                yield _problem("E_CONFIG_CONFLICT", "config", where, message, append)
                continue
            written.append(key)
            owners.append(f"its chunk {what}")
            yield from self.count(helper, append, target, where, f"chunk {what}", types)
        yield from self.bindings(helper, append, "append", sources)
        yield from self.required(helper, append, "append", written, body=False)

    def completion(
        self, helper: Helper, sources: Mapping[str, list[_Source]], problems: list[Diagnostic]
    ) -> OperationSpec | None:
        """Check a completion operation: one JSON success response of the result schema, and its bindings."""
        completion, at, pages = helper.tree["completion"], f"{helper.at}.completion", self.pages
        if completion["kind"] != "operation":
            return None
        spec = pages.spec(completion["operation"])
        if isinstance(single := pages.single(spec, f"{at}.operation", f"completion of {helper.name!r}"), Diagnostic):
            problems.append(single)
            return None
        use, reference, mismatch = single[1], completion["result_schema"], f"the schema of the {_label(spec)} response"
        where = f"{at}.result_schema"
        if found := pages.schema(helper, spec, reference, use.type, use.id.direction, where, "result schema", mismatch):
            problems.append(found)
            return None
        problems.extend(self.bindings(helper, spec, "completion", sources))
        problems.extend(self.required(helper, spec, "completion", self.keys(completion["bindings"])))
        return spec


def plan_uploads(
    protocols: Protocols | None,
    plan: ClientPlan,
    facts: ModelFacts,
    codecs: Container[TypeUseId],
    request: TargetRequest,
) -> tuple[tuple[UploadSpec, ...], dict[str, list[Diagnostic]]]:
    """Plan every enabled upload helper, returning them and each checked helper's problems."""
    if protocols is None:
        return (), {}
    uploads = _Uploads(_Polls(_Pages(protocols, plan, facts, codecs, request)))
    return uploads.pages.each(("resumable_upload",), uploads.helper)
