"""Plan the cache helpers of a client target and the checks they must pass.

A cache helper fetches a GET operation without a body whose cacheable statuses are JSON successes, and its tags and its
mutations' tags fill their braces with required scalar path or query parameters of the operation each renders for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypeAlias

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.pagination import _Pages
from datamodel_code_generator._codec_declarations import OperationRef

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import DiagnosticStage
    from datamodel_code_generator._client.plan import ClientPlan, OperationSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper
    from datamodel_code_generator._openapi_codec_plan import CodecPlan
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._target_contract import TypeUseBinding

Tag: TypeAlias = tuple[str | int, ...]

_PLACEHOLDER: Final = re.compile(r"\{([^{}]+)\}")
_UNSAFE: Final = frozenset({"post", "put", "patch", "delete"})
_SCALARS: Final = frozenset({"string", "integer", "number", "boolean"})
_TAGGED: Final = frozenset({"path", "query"})
_MIN_SUCCESS: Final = 200
_MAX_SUCCESS: Final = 299


@dataclass(frozen=True, slots=True, kw_only=True)
class MutationSpec:
    """A mutation of a cache helper ready to render: its method name, operation, and invalidated tags."""

    name: str
    operation: OperationSpec
    tags: tuple[Tag, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheSpec:
    """A cache helper ready to render: its operation, the use of its cacheable response, its tags, and mutations.

    A tag is literal text and the positions of the parameters whose wire values fill it in.
    """

    helper: Helper
    operation: OperationSpec
    response: TypeUseBinding
    tags: tuple[Tag, ...]
    mutations: tuple[MutationSpec, ...]


def _label(spec: OperationSpec) -> str:
    return f"{spec.contract.method.upper()} {spec.contract.path}"


def _problem(code: str, stage: DiagnosticStage, at: str, message: str, spec: OperationSpec) -> Diagnostic:
    pointer = spec.contract.id.use_site.pointer
    return Diagnostic(
        code=code,
        severity="error",
        stage=stage,
        message=message,
        source_pointer=pointer,
        operation=OperationRef(pointer=pointer),
        option_path=at,
    )


class _Caches:
    """Check and plan every enabled cache helper of a client target."""

    def __init__(
        self, protocols: Protocols, codecs: CodecPlan, wire: WirePlan, request: TargetRequest, plan: ClientPlan
    ) -> None:
        """Index the use bindings, the schema reader that types parameters, and the headers credentials travel in."""
        self.pages = _Pages(protocols, plan, codecs, wire, request)
        self.bindings = dict(codecs.bindings)
        self.credential_headers = self.pages.secret_headers

    def helper(
        self, helper: Helper, spec: OperationSpec, mutations: Mapping[str, OperationSpec]
    ) -> tuple[CacheSpec | None, list[Diagnostic]]:
        """Check one enabled helper against its operation and its mutations', and plan it when every check passes."""
        at, name, label, tree = helper.at, helper.name, _label(spec), helper.tree
        problems: list[Diagnostic] = []
        method = spec.contract.method
        if method == "head":
            message = f"The cache helper {name!r} fetches {label}, and HEAD is not supported yet"
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", f"{at}.operation", message, spec))
        elif method != "get":
            message = f"The cache helper {name!r} fetches {label}, which changes state; only GET is cached"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, spec))
        if spec.body is not None:
            message = f"The cache helper {name!r} fetches {label}, which takes a request body"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, spec))
        response = self.responses(helper, spec, problems)
        security = spec.security
        if not tree["authenticated"] and security is not None and security.alternatives and all(security.alternatives):
            message = f"The cache helper {name!r} is declared anonymous, but {label} requires credentials"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.authenticated", message, spec))
        for index, header in enumerate(tree["vary_allowlist"]):
            if header.lower() in self.credential_headers:
                message = (
                    f"The cache helper {name!r} allows a Vary on {header!r}, which credentials travel in; the auth "
                    "adds it after the cache looks a request up"
                )
                problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.vary_allowlist[{index}]", message, spec))
        tags = tuple(self.tags(helper, spec, tree["tags"], f"{at}.tags", problems))
        planned: list[MutationSpec] = []
        for key, mutation in tree["mutations"].items():
            where, target = f"{at}.mutations[{key!r}]", mutations[key]
            if target.contract.method not in _UNSAFE:
                message = (
                    f"The mutation {key!r} of {name!r} calls {_label(target)}; only POST, PUT, PATCH, and DELETE "
                    "invalidate"
                )
                problems.append(_problem("E_CONFIG_VALUE", "config", f"{where}.operation", message, target))
            invalidated = self.tags(helper, target, mutation["invalidate_tags"], f"{where}.invalidate_tags", problems)
            planned.append(MutationSpec(name=key, operation=target, tags=tuple(invalidated)))
        if response is None or problems:
            return None, problems
        return CacheSpec(
            helper=helper, operation=spec, response=response, tags=tags, mutations=tuple(planned)
        ), problems

    def responses(self, helper: Helper, spec: OperationSpec, problems: list[Diagnostic]) -> TypeUseBinding | None:
        """Check that each cacheable status is a declared success with one natively decoded JSON body.

        Return the use of the first one's body.
        """
        at, name, label = f"{helper.at}.statuses", helper.name, _label(spec)
        found: list[TypeUseBinding] = []
        for index, status in enumerate(helper.tree["statuses"]):
            response = next((item for item in spec.responses if item.status == str(status)), None)
            where = f"{at}[{index}]"
            if response is None or not response.success or not _MIN_SUCCESS <= status <= _MAX_SUCCESS:
                message = f"The cacheable status {status} of {name!r} is no declared 2xx success of {label}"
                problems.append(_problem("E_CONFIG_VALUE", "config", where, message, spec))
                continue
            media = response.media
            use = media[0].use if len(media) == 1 and media[0].kind == "json" else None
            binding = None if use is None else self.bindings.get(use.id)
            if (
                use is None
                or binding is None
                or binding.projection_mode != "native"
                or binding.converter_strategy == "registered_adapter"
            ):
                message = (
                    f"The cacheable status {status} of {name!r} has a response other than one natively decoded JSON "
                    "body, which is not supported yet"
                )
                problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", where, message, spec))
                continue
            found.append(use)
        return found[0] if found else None

    def tags(
        self, helper: Helper, spec: OperationSpec, templates: list[str], at: str, problems: list[Diagnostic]
    ) -> Iterator[Tag]:
        """Compile each tag template into literal text and parameter positions, reporting unusable placeholders.

        A placeholder names exactly one required path or query parameter by wire name, typed only as scalars.
        """
        parameters = spec.parameters
        for index, template in enumerate(templates):
            where, parts, position = f"{at}[{index}]", [], 0
            for match in _PLACEHOLDER.finditer(template):
                parts.extend((template[position : match.start()],) if match.start() > position else ())
                position = match.end()
                found = [
                    place
                    for place, item in enumerate(parameters)
                    if item.location in _TAGGED and item.wire_name == match[1]
                ]
                wanted, label = match[1], _label(spec)
                problem = None
                if len(found) != 1:
                    many = "more than one" if found else "no"
                    problem = f"names {many} path or query parameter {wanted!r} of {label}"
                elif not (parameter := parameters[found[0]]).required:
                    problem = f"names the optional parameter {wanted!r} of {label}"
                elif (
                    (use := parameter.use) is None
                    or use.schema is None
                    or not _SCALARS.issuperset(self.pages.types(use.schema) or {"object"})
                ):
                    problem = (
                        f"names the parameter {wanted!r} of {label}, which is not always a string, number, integer, or "
                        "boolean"
                    )
                if problem is not None:
                    message = f"The tag {template!r} of {helper.name!r} {problem}"
                    problems.append(_problem("E_CONFIG_VALUE", "config", where, message, spec))
                    continue
                parts.append(found[0])
            parts.extend((template[position:],) if position < len(template) else ())
            yield tuple(parts)


def plan_caches(
    protocols: Protocols | None, plan: ClientPlan, codecs: CodecPlan, wire: WirePlan, request: TargetRequest
) -> tuple[tuple[CacheSpec, ...], dict[str, list[Diagnostic]]]:
    """Plan every enabled cache helper, returning the planned ones and each checked helper's problems."""
    if protocols is None:
        return (), {}
    caches = _Caches(protocols, codecs, wire, request, plan)
    operations = {spec.contract.id: spec for spec in plan.operations}
    specs: list[CacheSpec] = []
    problems: dict[str, list[Diagnostic]] = {}
    for helper in protocols.helpers:
        if not helper.enabled or helper.kind != "cache":
            continue
        entry, *others = helper.links
        spec = operations[protocols.operations[entry.ref].id]
        mutations = {
            name: operations[protocols.operations[link.ref].id]
            for name, link in zip(helper.tree["mutations"], others, strict=True)
        }
        planned, problems[helper.name] = caches.helper(helper, spec, mutations)
        if planned is not None:
            specs.append(planned)
    return tuple(specs), problems
