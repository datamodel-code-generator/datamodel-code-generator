"""Plan the cache helpers of a client target and the checks they must pass.

A cache helper fetches a GET operation without a body whose cacheable statuses are JSON successes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.pagination import _Pages
from datamodel_code_generator._codec_declarations import OperationRef

if TYPE_CHECKING:
    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import DiagnosticStage
    from datamodel_code_generator._client.plan import ClientPlan, OperationSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper
    from datamodel_code_generator._openapi_codec_plan import CodecPlan
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._target_contract import TypeUseBinding

_MIN_SUCCESS: Final = 200
_MAX_SUCCESS: Final = 299


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheSpec:
    """A cache helper ready to render, with its operation and cacheable response use."""

    helper: Helper
    operation: OperationSpec
    response: TypeUseBinding


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
        """Index the response bindings and the headers credentials travel in."""
        self.pages = _Pages(protocols, plan, codecs, wire, request)
        self.bindings = dict(codecs.bindings)
        self.credential_headers = self.pages.secret_headers

    def helper(self, helper: Helper, spec: OperationSpec) -> tuple[CacheSpec | None, list[Diagnostic]]:
        """Check one enabled helper against its operation, and plan it when every check passes."""
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
        if response is None or problems:
            return None, problems
        return CacheSpec(helper=helper, operation=spec, response=response), problems

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
        entry = helper.links[0]
        spec = operations[protocols.operations[entry.ref].id]
        planned, problems[helper.name] = caches.helper(helper, spec)
        if planned is not None:
            specs.append(planned)
    return tuple(specs), problems
