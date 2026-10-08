"""The FastAPI server target: plan, bind, and render one server package behind the single-target coordinator."""

from __future__ import annotations

import warnings
from dataclasses import replace
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._api_generation import TargetRender
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic
from datamodel_code_generator._fastapi.callbacks import CallbackIndex, flattened
from datamodel_code_generator._fastapi.config import FastAPIConfig
from datamodel_code_generator._fastapi.documentation import Documentation
from datamodel_code_generator._fastapi.plan import PlanError, Planner
from datamodel_code_generator._fastapi.render import ServerRenderer
from datamodel_code_generator._fastapi.templates import FastAPITemplates
from datamodel_code_generator._openapi_wire_plan import operation_uses, plan_wire
from datamodel_code_generator._target_render import model_dependencies
from datamodel_code_generator.enums import DataModelType

if TYPE_CHECKING:
    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import TargetKind
    from datamodel_code_generator._fastapi.plan import ServerPlan
    from datamodel_code_generator._openapi_codec_plan import PydanticBackend
    from datamodel_code_generator._openapi_wire_plan import CodecDiagnostic, WirePlan
    from datamodel_code_generator._target_contract import ModelArtifact

DEPENDENCIES: Final = ("fastapi>=0.141.1", "pydantic>=2.13.5")
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
    selector: str = "--generate-server"

    def render(self, request: TargetRequest) -> TargetRender:  # noqa: PLR6301
        """Plan the selected operations and render the package, with the custom template directory's overrides."""
        config = request.config
        assert isinstance(config, FastAPIConfig)
        wire = plan_wire(
            request.batch,
            request.lease,
            [use for operation in request.operations for use in operation_uses(operation)],
            operations=frozenset(operation.id for operation in request.operations),
            documents=request.documents.pointers,
        )
        try:
            plan = Planner(request, config, wire).plan()
        except PlanError as error:
            raise APIGenerationError(
                tuple(replace(item, target_id=request.target_id) for item in error.diagnostics),
                option_prefix=FastAPIConfig._option_prefix,  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            ) from None
        selected = {operation.contract.id for operation in plan.operations}
        if problems := [item for item in wire.diagnostics if item.operation in {None, *selected}]:
            raise APIGenerationError(tuple(_diagnostic(item, request) for item in problems))
        docs = _docs(plan, request, wire)
        renderer = ServerRenderer(
            config=config,
            backend=_BACKENDS[request.model_config.output_model_type],
            plan=plan,
            batch=request.batch,
            wire=wire,
            templates=FastAPITemplates.custom(request.model_config, request.target_id, request.cwd),
            docs=docs,
        )
        files = renderer.files()
        for problem in docs.problems:
            warnings.warn(problem, stacklevel=2)
        return TargetRender(files=files, dependencies=_dependencies(plan, request.models))


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


def _dependencies(plan: ServerPlan, models: tuple[ModelArtifact, ...]) -> tuple[str, ...]:
    forms = any(
        (body := spec.body) is not None
        and (
            body.form or (body.decision.transport == "adapter" and any(item.kind == "multipart" for item in body.media))
        )
        for spec in plan.operations
    )
    return (
        *DEPENDENCIES,
        *((FORMS,) if forms else ()),
        *model_dependencies(models),
    )
