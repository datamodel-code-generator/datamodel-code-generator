"""The FastAPI server target: plan, bind, and render one server package behind the single-target coordinator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._api_generation import TargetBinding, TargetRender
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic
from datamodel_code_generator._codec_declarations import CodecDeclarations, OperationRef
from datamodel_code_generator._fastapi.config import FastAPIConfig
from datamodel_code_generator._fastapi.hooks import Extensions, HookRunner, extended
from datamodel_code_generator._fastapi.plan import PlanError, Planner, Revision
from datamodel_code_generator._fastapi.render import ServerRenderer
from datamodel_code_generator._fastapi.templates import TemplateSet
from datamodel_code_generator._fastapi.views import ContextBuilder
from datamodel_code_generator._openapi_codec_adapters import select_adapters
from datamodel_code_generator._openapi_codec_plan import artifact_module, plan_model_codecs
from datamodel_code_generator._openapi_wire_plan import plan_wire
from datamodel_code_generator.enums import DataModelType

if TYPE_CHECKING:
    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_manifest import JSONObject
    from datamodel_code_generator._api_types import TargetKind
    from datamodel_code_generator._fastapi.context import FastAPIContext
    from datamodel_code_generator._fastapi.plan import OperationSpec, ServerPlan
    from datamodel_code_generator._generation_contract import OperationContract, OperationId, TypeUseId
    from datamodel_code_generator._openapi_codec_plan import CodecPlan, PydanticBackend
    from datamodel_code_generator._openapi_wire_plan import CodecDiagnostic, WirePlan
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue, WireValue

DEPENDENCIES: Final = (
    "fastapi>=0.141.1,<0.142",
    "starlette>=1.0.0,<2",
    "pydantic>=2.13.5,<3",
    "jsonschema[format-nongpl]>=4.26,<5",
    "referencing>=0.37,<1",
    "typing-extensions>=4.16,<5",
)
FORMS: Final = "python-multipart>=0.0.32,<0.1"
PATTERNS: Final = "google-re2>=1.1.20251105,<2"
_BACKENDS: Final[dict[DataModelType, PydanticBackend]] = {
    DataModelType.PydanticV2BaseModel: "pydantic_v2.BaseModel",
    DataModelType.PydanticV2Dataclass: "pydantic_v2.dataclass",
}
_PATTERN_KEYWORDS: Final = frozenset({"pattern", "patternProperties"})

class FastAPITarget:
    """Render a FastAPI server package for the two Pydantic v2 backends."""

    kind: TargetKind = "fastapi"
    backends: frozenset[DataModelType] = frozenset(_BACKENDS)
    unsupported_backend: str = "E_FASTAPI_BACKEND_UNSUPPORTED"

    def render(self, request: TargetRequest) -> TargetRender:  # noqa: PLR6301
        """Plan the selected operations, let the hooks revise the plan, bind codecs, and render the package."""
        config = request.config
        assert isinstance(config, FastAPIConfig)
        stage = _Stage(request, config)
        try:
            plan, codecs = stage.planned(Revision())
        except PlanError as error:
            raise APIGenerationError(
                tuple(replace(item, target_id=request.target_id) for item in error.diagnostics)
            ) from None
        templates = None if config.templates is None else TemplateSet(config.templates, request.target_id)
        excluded: tuple[Diagnostic, ...] = ()
        context = None
        if config.hooks:
            revision, _, context = HookRunner(config.hooks, request.target_id).run(stage)
            plan, codecs = stage.planned(revision)
            excluded = _excluded(plan, request)
        elif templates is not None:
            context = stage.context(Revision(), Extensions())
        renderer = ServerRenderer(
            config=config,
            package=request.layout.package,
            plan=plan,
            batch=request.batch,
            wire=stage.wire,
            codecs=codecs,
            templates=templates,
            context=context,
        )
        return TargetRender(
            files=renderer.files(),
            target_data=_target_data(plan, config, request),
            dependencies=_dependencies(plan, stage.wire),
            bindings=_bindings(codecs, _BACKENDS[request.model_config.output_model_type]),
            persistent_diagnostics=excluded,
        )


class _Stage:
    """Plan the server and bind its codecs under a hook revision, keeping the latest plan."""

    def __init__(self, request: TargetRequest, config: FastAPIConfig) -> None:
        """Plan the wire of the selected operations and choose their codec adapters once."""
        self.request = request
        self.config = config
        self.wire = plan_wire(
            request.batch,
            request.lease,
            [use for operation in request.operations for use in _uses(operation)],
            operations=frozenset(operation.id for operation in request.operations),
            documents=request.documents.pointers,
        )
        self.declarations = CodecDeclarations(
            compatibility=config.builtin_codec_compatibility,
            exports=config.export_bindings,
            adapters=config.codec_adapters,
        )
        self.adapters = select_adapters(request.batch, self.wire, self.declarations, "server")
        self.latest: tuple[Revision, ServerPlan, CodecPlan] | None = None

    def planned(self, revision: Revision) -> tuple[ServerPlan, CodecPlan]:
        """Return the plan and codecs of a revision, planning them unless the latest revision was the same."""
        if (latest := self.latest) is not None and latest[0] == revision:
            return latest[1], latest[2]
        request = self.request
        plan = Planner(request, self.config, self.wire, self.adapters, revision).plan()
        uses = _codec_uses(plan)
        codecs = plan_model_codecs(
            request.batch,
            replace(self.wire, schema_ids=tuple(item for item in self.wire.schema_ids if item[0] in uses)),
            _BACKENDS[request.model_config.output_model_type],
            declarations=self.declarations,
            surface="server",
            lease=request.lease,
            sources=_sources(request),
        )
        selected = {operation.contract.id for operation in plan.operations}
        if problems := [item for item in codecs.diagnostics if item.operation in {None, *selected}]:
            raise APIGenerationError(tuple(_diagnostic(item, request) for item in problems))
        self.latest = (revision, plan, codecs)
        return plan, codecs

    def context(self, revision: Revision, extensions: Extensions) -> FastAPIContext:
        """Return the context of a revision's plan, with the hooks' extras and imports."""
        plan, codecs = self.planned(revision)
        renderer = ServerRenderer(
            config=self.config,
            package=self.request.layout.package,
            plan=plan,
            batch=self.request.batch,
            wire=self.wire,
            codecs=codecs,
        )
        return extended(ContextBuilder(renderer, self.request).context(), extensions)


def _excluded(plan: ServerPlan, request: TargetRequest) -> tuple[Diagnostic, ...]:
    kept = {spec.key for spec in plan.operations}
    return tuple(
        Diagnostic(
            code="S_OPERATION_EXCLUDED",
            severity="info",
            stage="hook",
            message=f"{operation.method.upper()} {operation.path} is removed by a hook",
            source_uri=request.documents.root_uri,
            source_pointer=key,
            operation=OperationRef(pointer=key),
            target_id=request.target_id,
        )
        for operation in request.operations
        if (key := operation.id.use_site.pointer) not in kept
    )


def _uses(operation: OperationContract) -> tuple[TypeUseId, ...]:
    pending = [*operation.parameters, *operation.responses]
    if operation.request_body is not None:
        pending.append(operation.request_body)
    found: list[TypeUseId] = []
    while pending:
        declaration = pending.pop(0)
        found.extend(declaration.schemas)
        pending.extend(child for child in declaration.children if child.kind != "encoding")
    return tuple(found)


def _codec_uses(plan: ServerPlan) -> frozenset[TypeUseId]:
    uses: set[TypeUseId] = set()
    for spec in plan.operations:
        uses.update(
            parameter.use.id for parameter in spec.parameters if parameter.native is None and parameter.use is not None
        )
        if spec.body is not None and spec.body.decision.transport == "codec_adapter":
            uses.update(media.use.id for media in spec.body.media if media.use is not None and media.kind != "binary")
        for response in spec.responses:
            uses.update(media.use.id for media in response.media if media.use is not None and media.kind != "binary")
            uses.update(
                header.use.id for header in response.headers if header.use is not None and header.plan is not None
            )
    return frozenset(uses)


def _sources(request: TargetRequest) -> dict[str, str]:
    contents = {artifact.path: artifact.content.decode(artifact.encoding) for artifact in request.models}
    return {
        artifact_module(address): contents[address.relative_path]
        for address in request.batch.artifacts
        if address.relative_path in contents
    }


def _diagnostic(item: CodecDiagnostic, request: TargetRequest) -> Diagnostic:
    return Diagnostic(
        code=item.code,
        severity="error",
        stage="binding",
        message=item.message,
        source_uri=request.documents.root_uri,
        source_pointer=item.source.pointer,
        target_id=request.target_id,
    )


def _dependencies(plan: ServerPlan, wire: WirePlan) -> tuple[str, ...]:
    forms = any(spec.body is not None and spec.body.fields for spec in plan.operations)
    patterns = any(_patterned(resource.contents) for resource in wire.resources)
    return (*DEPENDENCIES, *((FORMS,) if forms else ()), *((PATTERNS,) if patterns else ()))


def _patterned(value: WireValue) -> bool:
    if isinstance(value, tuple):
        return any(_patterned(item) for item in value)
    if isinstance(value, Mapping):
        return any(key in _PATTERN_KEYWORDS or _patterned(item) for key, item in value.items())
    return False


def _bindings(codecs: CodecPlan, backend: str) -> tuple[TargetBinding, ...]:
    return tuple(
        TargetBinding(
            use=use,
            backend=backend,
            strategy="adapter" if binding.converter_strategy == "registered_adapter" else binding.projection_mode,
            converter_strategy=binding.converter_strategy,
        )
        for use, binding in codecs.bindings
    )


def _target_data(plan: ServerPlan, config: FastAPIConfig, request: TargetRequest) -> JSONObject:
    selected = {operation.id: index for index, operation in enumerate(request.operations)}
    indexes = {spec.key: index for index, spec in enumerate(plan.operations)}
    return {
        "context_version": 1,
        "layout": config.layout,
        "operations": [_operation_data(spec, selected, config, request) for spec in plan.operations],
        "groups": [
            {
                "key": group.key,
                "file_stem": group.stem,
                "primary_tag": group.primary_tag,
                "service": group.service,
                "operations": [f"/target_data/fastapi/operations/{indexes[spec.key]}" for spec in group.operations],
            }
            for group in plan.groups
        ],
    }


def _operation_data(
    spec: OperationSpec, selected: dict[OperationId, int], config: FastAPIConfig, request: TargetRequest
) -> JSONValue:
    documents = request.documents
    primary = spec.primary
    return {
        "operation": f"/selection/selected_operations/{selected[spec.contract.id]}",
        "python_name": spec.python_name,
        "method": spec.contract.method,
        "path": spec.contract.path,
        "route_path": spec.route.route_path,
        "group_key": spec.group,
        "handler_mode": spec.mode,
        "body_mode": "request" if spec.body is not None and spec.body.decision.transport == "raw_request" else "typed",
        "primary_response": None
        if primary is None
        else {"status_code": primary.status, "media_type": None if primary.media is None else primary.media.media_type},
        "registration_status": spec.registration_status,
        "response_payload_alias": f"{spec.pascal}ResponsePayload",
        "response_codecs_name": f"{spec.pascal}ResponseCodecs",
        "projections": [
            {
                "use_ids": [documents.use(use) for use in decision.uses],
                "site": decision.site,
                "transport": decision.transport,
                "reason": decision.reason,
                "source": None if decision.source is None else documents.source(decision.source),
            }
            for decision in spec.decisions()
        ],
        "path_slots": [
            {"wire_name": slot.wire_name, "slot": slot.slot, "occurrence": slot.occurrence} for slot in spec.route.slots
        ],
    }
