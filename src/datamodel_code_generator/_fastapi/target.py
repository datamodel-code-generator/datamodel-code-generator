"""The FastAPI server target: plan, bind, and render one server package behind the single-target coordinator."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._api_generation import TargetBinding, TargetRender
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, OperationRef
from datamodel_code_generator._fastapi.callbacks import CallbackIndex, flattened
from datamodel_code_generator._fastapi.config import FastAPIConfig
from datamodel_code_generator._fastapi.documentation import Documentation
from datamodel_code_generator._fastapi.hooks import Extensions, HookRunner, extended
from datamodel_code_generator._fastapi.plan import PlanError, Planner, Revision
from datamodel_code_generator._fastapi.render import ServerRenderer
from datamodel_code_generator._fastapi.templates import TemplateSet
from datamodel_code_generator._fastapi.views import ContextBuilder
from datamodel_code_generator._openapi_codec_plan import plan_model_codecs
from datamodel_code_generator._openapi_wire_plan import operation_uses, plan_wire
from datamodel_code_generator._target_render import PATTERNS, model_dependencies, patterned
from datamodel_code_generator.enums import DataModelType

if TYPE_CHECKING:
    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import TargetKind
    from datamodel_code_generator._fastapi.context import FastAPIContext
    from datamodel_code_generator._fastapi.plan import ServerPlan
    from datamodel_code_generator._openapi_codec_plan import CodecPlan, PydanticBackend
    from datamodel_code_generator._openapi_wire_plan import CodecDiagnostic, WirePlan
    from datamodel_code_generator._target_contract import ModelArtifact, TypeUseId

DEPENDENCIES: Final = (
    "fastapi>=0.141.1",
    "starlette>=1.0.0",
    "pydantic>=2.13.5",
    "jsonschema[format-nongpl]>=4.26",
    "referencing>=0.37",
    "typing-extensions>=4.16",
)
FORMS: Final = "python-multipart>=0.0.32"
_BACKENDS: Final[dict[DataModelType, PydanticBackend]] = {
    DataModelType.PydanticV2BaseModel: "pydantic_v2.BaseModel",
    DataModelType.PydanticV2Dataclass: "pydantic_v2.dataclass",
}


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
        docs = _docs(plan, request, stage.wire)
        renderer = ServerRenderer(
            config=config,
            package=request.layout.package,
            backend=_BACKENDS[request.model_config.output_model_type],
            plan=plan,
            batch=request.batch,
            wire=stage.wire,
            codecs=codecs,
            templates=templates,
            context=context,
            docs=docs,
        )
        return TargetRender(
            files=renderer.files(),
            target_data={},
            dependencies=_dependencies(plan, stage.wire, request.models),
            bindings=_bindings(codecs, _BACKENDS[request.model_config.output_model_type]),
            diagnostics=tuple(docs.problems),
            persistent_diagnostics=excluded,
        )


class _Stage:
    """Plan the server and bind its codecs under a hook revision, keeping the latest plan."""

    def __init__(self, request: TargetRequest, config: FastAPIConfig) -> None:
        """Plan the wire of the selected operations once."""
        self.request = request
        self.config = config
        self.wire = plan_wire(
            request.batch,
            request.lease,
            [use for operation in request.operations for use in operation_uses(operation)],
            operations=frozenset(operation.id for operation in request.operations),
            documents=request.documents.pointers,
        )
        self.latest: tuple[Revision, ServerPlan, CodecPlan] | None = None
        self.view: tuple[Revision, FastAPIContext] | None = None

    def planned(self, revision: Revision) -> tuple[ServerPlan, CodecPlan]:
        """Return the plan and codecs of a revision, planning them unless the latest revision was the same."""
        if (latest := self.latest) is not None and latest[0] == revision:
            return latest[1], latest[2]
        request = self.request
        plan = Planner(request, self.config, self.wire, revision).plan()
        uses = _codec_uses(plan)
        codecs = plan_model_codecs(
            request.batch,
            replace(self.wire, schema_ids=tuple(item for item in self.wire.schema_ids if item[0] in uses)),
            _BACKENDS[request.model_config.output_model_type],
        )
        selected = {operation.contract.id for operation in plan.operations}
        if problems := [item for item in codecs.diagnostics if item.operation in {None, *selected}]:
            raise APIGenerationError(tuple(_diagnostic(item, request) for item in problems))
        self.latest = (revision, plan, codecs)
        return plan, codecs

    def context(self, revision: Revision, extensions: Extensions) -> FastAPIContext:
        """Return the context of a revision's plan, with the hooks' extras and imports.

        The context of the latest revision is built once, however many hooks only add extras.
        """
        if (view := self.view) is None or view[0] != revision:
            plan, codecs = self.planned(revision)
            renderer = ServerRenderer(
                config=self.config,
                package=self.request.layout.package,
                backend=_BACKENDS[self.request.model_config.output_model_type],
                plan=plan,
                batch=self.request.batch,
                wire=self.wire,
                codecs=codecs,
            )
            view = self.view = (revision, ContextBuilder(renderer, self.request).context())
        return extended(view[1], extensions)


def _docs(plan: ServerPlan, request: TargetRequest, wire: WirePlan) -> Documentation:
    """Return the documentation builder, planning the schemas of callbacks, which no route reads, apart."""
    index = CallbackIndex(request.batch)
    callbacks = list(
        {
            node.operation.id: node.operation
            for spec in plan.operations
            for node in flattened(index.nodes(spec.contract, spec.key))
        }.values()
    )
    wires: tuple[WirePlan, ...] = (wire,)
    if callbacks:
        wires = (
            wire,
            plan_wire(
                request.batch,
                request.lease,
                [use for operation in callbacks for use in operation_uses(operation)],
                operations=frozenset(operation.id for operation in callbacks),
                documents=request.documents.pointers,
            ),
        )
    return Documentation(plan, request, wires)


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


def _codec_uses(plan: ServerPlan) -> frozenset[TypeUseId]:
    uses: set[TypeUseId] = set()
    for spec in plan.operations:
        for response in spec.responses:
            uses.update(media.use.id for media in response.media if media.use is not None and media.kind != "binary")
            uses.update(
                header.use.id for header in response.headers if header.use is not None and header.plan is not None
            )
    return frozenset(uses)


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


def _dependencies(plan: ServerPlan, wire: WirePlan, models: tuple[ModelArtifact, ...]) -> tuple[str, ...]:
    forms = any(
        (body := spec.body) is not None
        and (
            body.form
            or (body.decision.transport == "codec_adapter" and any(item.kind == "multipart" for item in body.media))
        )
        for spec in plan.operations
    )
    return (
        *DEPENDENCIES,
        *((FORMS,) if forms else ()),
        *((PATTERNS,) if patterned(wire) else ()),
        *model_dependencies(models),
    )


def _bindings(codecs: CodecPlan, backend: str) -> tuple[TargetBinding, ...]:
    return tuple(
        TargetBinding(
            use=use,
            backend=backend,
            strategy=binding.projection_mode,
            converter_strategy=binding.converter_strategy,
        )
        for use, binding in codecs.bindings
    )
