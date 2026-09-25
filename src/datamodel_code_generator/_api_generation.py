"""Coordinate one target: validate settings, generate models once in staging, select operations, and plan files."""

from __future__ import annotations

import os
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass, field, fields
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias
from urllib.parse import ParseResult

from datamodel_code_generator._api_manifest import (
    GENERATOR_NAME,
    INVENTORY_PATH,
    MANIFEST_NAME,
    ROOT_POINTER,
    ROOT_URN,
    DocumentTable,
    RootInput,
    canonical_bytes,
    canonical_document,
    config_error,
    document_identity,
    json_object,
    manifest_files,
    model_record,
    plan_files,
    portable,
    read_target_state,
    relative_uri,
    runtime_revision,
    sha256,
    target_identity,
)
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, GeneratedArtifact, GeneratedProject
from datamodel_code_generator._codec_declarations import OperationRef, SchemaRef

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator import _GenerationInput  # pyright: ignore[reportPrivateUsage]
    from datamodel_code_generator._api_manifest import FilePlan, JSONObject, PlannedFile, TargetState
    from datamodel_code_generator._api_types import (
        ArtifactAction,
        ArtifactKind,
        OperationSelection,
        OperationSelector,
        TargetKind,
    )
    from datamodel_code_generator._generation_contract import (
        GeneratedTypeContractBatch,
        OperationContract,
        OperationId,
        TypeUseId,
    )
    from datamodel_code_generator._openapi_artifacts import ModelArtifact
    from datamodel_code_generator._openapi_generation import ModelGenerationProduct, SourceLease
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue
    from datamodel_code_generator._target_config import TargetConfig
    from datamodel_code_generator.config import GenerateConfig
    from datamodel_code_generator.enums import DataModelType
    from datamodel_code_generator.remote_lock import RemoteReferenceLock

Strategy: TypeAlias = Literal["native", "envelope", "adapter"]
ConverterStrategy: TypeAlias = Literal[
    "pydantic_type_adapter",
    "dataclass_structural",
    "typeddict_structural",
    "msgspec_convert",
    "msgspec_structural",
    "registered_adapter",
]
Exclusion: TypeAlias = "tuple[OperationContract, str]"

_SECRET_MODEL_OPTIONS = frozenset({"http_headers", "http_query_parameters"})


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetBinding:
    """How the target converts one type use, as recorded in the manifest."""

    use: TypeUseId
    backend: str
    strategy: Strategy
    converter_strategy: ConverterStrategy
    adapter_identity: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetRequest:
    """Everything a target renders from: settings, the accepted model contracts, and the selection."""

    config: TargetConfig
    model_config: GenerateConfig
    target_id: str
    batch: GeneratedTypeContractBatch
    lease: SourceLease
    models: tuple[ModelArtifact, ...]
    operations: tuple[OperationContract, ...]
    excluded: tuple[Exclusion, ...]
    documents: DocumentTable
    state: TargetState


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetRender:
    """A target's planned files and manifest data; run diagnostics are reported but never persisted."""

    files: tuple[PlannedFile, ...]
    target_data: JSONObject
    bindings: tuple[TargetBinding, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    persistent_diagnostics: tuple[Diagnostic, ...] = ()
    runtime_defaults: JSONObject = field(default_factory=json_object)
    protocol_metadata: JSONObject = field(default_factory=json_object)


class TargetGenerator(Protocol):
    """Render one target kind from the coordinator's request."""

    @property
    def kind(self) -> TargetKind:
        """Return the target kind recorded in manifests and reports."""

    @property
    def backends(self) -> frozenset[DataModelType]:
        """Return the model backends the target accepts."""

    @property
    def unsupported_backend(self) -> str:
        """Return the diagnostic code for a backend the target does not accept."""

    def render(self, request: TargetRequest) -> TargetRender:
        """Render the target's files from one accepted model generation."""


@dataclass(frozen=True, slots=True, kw_only=True)
class _Models:
    product: ModelGenerationProduct
    artifacts: tuple[ModelArtifact, ...]
    single: bool
    metadata: tuple[Path, bytes] | None
    lock: tuple[Path, bytes] | None
    source: RootInput
    cwd: Path


def prepare_target(
    input_: _GenerationInput, model_config: GenerateConfig, generator: TargetGenerator
) -> GenerateConfig:
    """Validate target settings in the contract order and return the effective model settings."""
    from datamodel_code_generator import (  # noqa: PLC0415
        _prepare_generate_facade_config,  # pyright: ignore[reportPrivateUsage]
    )
    from datamodel_code_generator.enums import DataModelType  # noqa: PLC0415

    if model_config.output is None:
        raise config_error(
            code="E_CONFIG_VALUE",
            option_path="model_config.output",
            message=f"The {generator.kind} target needs model_config.output",
        )
    effective = _prepare_generate_facade_config(model_config)
    if (backend := effective.output_model_type) not in generator.backends:
        allowed = " or ".join(repr(item.value) for item in DataModelType if item in generator.backends)
        raise config_error(
            code=generator.unsupported_backend,
            option_path="model_config.output_model_type",
            message=f"The {generator.kind} target does not support {backend.value!r}; use {allowed}",
        )
    if (problem := _root_problem(input_, effective)) is None:
        return effective
    option_path, message = problem
    raise APIGenerationError((
        Diagnostic(code="E_INPUT_ROOT", severity="error", stage="input", message=message, option_path=option_path),
    ))


def _root_problem(input_: _GenerationInput, effective: GenerateConfig) -> tuple[str, str] | None:
    from datamodel_code_generator.enums import InputFileType  # noqa: PLC0415

    match input_:
        case list():
            return "input", "Target generation reads one root document, not a list"
        case Path() if input_.is_dir():
            return "input", "Target generation reads one root document, not a directory"
        case _ if effective.input_file_type not in {InputFileType.Auto, InputFileType.OpenAPI}:
            return "model_config.input_file_type", "Target generation reads an OpenAPI document"
    return None


def _root_input(input_: _GenerationInput, cwd: Path) -> RootInput:
    match input_:
        case Path():
            path = (cwd / input_.expanduser()).resolve()
            return RootInput("file", path.as_uri(), path.parent)
        case ParseResult():
            return RootInput("url", input_.geturl(), cwd)
        case str():
            return RootInput("text", ROOT_URN, cwd)
    return RootInput("mapping", ROOT_URN, cwd)


def _remote_lock(
    input_: _GenerationInput, config: GenerateConfig, cwd: Path
) -> tuple[GenerateConfig, RemoteReferenceLock | None]:
    from datamodel_code_generator import (  # noqa: PLC0415
        _prepare_atomic_generation_remote_lock,  # pyright: ignore[reportPrivateUsage]
        _resolve_generation_remote_lock,  # pyright: ignore[reportPrivateUsage]
    )

    if config.remote_lock_resolved and getattr(config.remote_lock, "update", False):
        raise config_error(
            code="E_CONFIG_CONFLICT",
            option_path="model_config.update_lock",
            message="A target run cannot publish a remote lock update that another caller owns",
        )
    if config.update_lock and not config.remote_lock_resolved:
        config, _, lock = _prepare_atomic_generation_remote_lock(input_, config, cwd)
        return config, lock
    return _resolve_generation_remote_lock(input_, config, cwd), None


def _staging(stack: ExitStack, destination: Path, cwd: Path) -> Path:
    parent = Path(os.path.abspath(cwd / destination.expanduser())).parent  # noqa: PTH100
    while not parent.exists():
        parent = parent.parent
    return Path(stack.enter_context(tempfile.TemporaryDirectory(prefix=".datamodel-codegen-", dir=parent)))


def _staged_models(staged: Path, output: Path, encoding: str) -> tuple[ModelArtifact, ...]:
    from datamodel_code_generator._openapi_artifacts import ModelArtifact  # noqa: PLC0415

    if staged.is_file():
        return (ModelArtifact((output.name,), staged.read_bytes(), encoding),)
    files = sorted((path.relative_to(staged).parts, path) for path in staged.rglob("*") if path.is_file())
    return tuple(ModelArtifact(parts, path.read_bytes(), encoding) for parts, path in files)


def _generate_models(
    input_: _GenerationInput, config: GenerateConfig, target: TargetConfig, cwd: Path, *, use_output_cwd: bool
) -> _Models:
    from datamodel_code_generator import _run_generation  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]
    from datamodel_code_generator._openapi_generation import OpenAPIGenerationSession  # noqa: PLC0415
    from datamodel_code_generator.enums import OpenAPIScope  # noqa: PLC0415

    source = _root_input(input_, cwd)
    prepared, lock = _remote_lock(input_, config, cwd)
    output = prepared.output
    assert output is not None
    with ExitStack() as stack:
        staged_output = (staging := _staging(stack, output, cwd)) / (output.name or "output")
        if (cwd / output).is_dir():
            staged_output.mkdir()
        updates = {"output": staged_output}
        if (metadata := config.emit_model_metadata) is not None:
            updates["emit_model_metadata"] = _staging(stack, metadata, cwd) / (metadata.name or "model-metadata.json")
        staged = prepared.model_copy(update=updates)
        staged._logical_output = output  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        staged._logical_model_metadata = prepared.emit_model_metadata  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        if lock is not None or not staged.remote_lock_resolved:
            staged.resolve_remote_lock(lock)
        session = OpenAPIGenerationSession(
            output=output, model_package=target.model_package, root_selector_document=source.identity
        )
        try:
            _run_generation(input_, staged, cwd, use_output_cwd=use_output_cwd, capture=session)
            artifacts = _staged_models(staged_output, output, prepared.encoding)
            product = session.take_product(
                artifacts, allow_empty_api=OpenAPIScope.Api in (prepared.openapi_scopes or ())
            )
        finally:
            session.close()
        try:
            return _Models(
                product=product,
                artifacts=artifacts,
                single=staged_output.is_file(),
                metadata=None if metadata is None else (metadata, updates["emit_model_metadata"].read_bytes()),
                lock=None
                if lock is None or not isinstance(staged_lock := lock.stage(staging), Path)
                else (lock.path, staged_lock.read_bytes()),
                source=source,
                cwd=cwd,
            )
        except BaseException:
            product.close()
            raise
        finally:
            if lock is not None:
                lock.discard_stage()


def _tags(operation: OperationContract) -> tuple[str, ...]:
    from datamodel_code_generator._generation_contract import LiteralScalar, LiteralSequence  # noqa: PLC0415

    if not isinstance(tags := dict(operation.facts).get("tags"), LiteralSequence):
        return ()
    return tuple(item.value for item in tags.items if isinstance(item, LiteralScalar) and isinstance(item.value, str))


class _Selector:
    def __init__(self, batch: GeneratedTypeContractBatch, source: RootInput, cwd: Path) -> None:
        root = batch.documents[0].id
        self.operations = {
            operation.id.use_site.pointer: operation
            for operation in batch.operations
            if operation.id.parent is None and operation.id.use_site.document == root
        }
        self.endpoints = [operation for operation in self.operations.values() if operation.id.kind == "path"]
        self.source = source
        self.cwd = cwd
        self.problems: list[Diagnostic] = []

    def report(self, code: str, option_path: str, message: str, pointer: str | None = None) -> None:
        self.problems.append(
            Diagnostic(
                code=code,
                severity="error",
                stage="selection",
                message=message,
                source_pointer=pointer,
                option_path=option_path,
            )
        )

    def resolve(self, name: str, selectors: tuple[OperationSelector, ...]) -> set[OperationId]:
        resolved: set[OperationId] = set()
        for index, selector in enumerate(selectors):
            pointer, document = (selector, None) if isinstance(selector, str) else (selector.pointer, selector.document)
            if document is not None and document_identity(document, self.cwd) != self.source.identity:
                message = "The operation reference names a document other than the root input"
            elif (operation := self.operations.get(pointer)) is None:
                message = "The operation reference selects no root operation"
            elif operation.id.kind != "path":
                message = "The operation reference selects a webhook, not a path operation"
            elif operation.id in resolved:
                self.report("E_SELECTION", f"selection.{name}[{index}]", "The selection repeats an operation", pointer)
                continue
            else:
                resolved.add(operation.id)
                continue
            self.report("E_OPERATION_REF", f"selection.{name}[{index}]", message, pointer)
        return resolved

    def known(self, name: str, values: tuple[str, ...]) -> frozenset[str]:
        present = {tag for operation in self.endpoints for tag in _tags(operation)}
        for index, tag in enumerate(values):
            if tag not in present:
                self.report("E_SELECTION", f"selection.{name}[{index}]", f"No path operation has the tag {tag!r}")
        return frozenset(values)


def select_operations(
    batch: GeneratedTypeContractBatch, selection: OperationSelection, source: RootInput, cwd: Path
) -> tuple[tuple[OperationContract, ...], tuple[Exclusion, ...]]:
    """Resolve selectors against root use sites, then include by OR and subtract exclusions, keeping order."""
    selector = _Selector(batch, source, cwd)
    include_operations = selector.resolve("include_operations", selection.include_operations)
    include_tags = selector.known("include_tags", selection.include_tags)
    exclude_operations = selector.resolve("exclude_operations", selection.exclude_operations)
    exclude_tags = selector.known("exclude_tags", selection.exclude_tags)
    if selector.problems:
        raise APIGenerationError(tuple(selector.problems))
    everything = not (include_operations or include_tags)
    selected: list[OperationContract] = []
    excluded: list[Exclusion] = []
    for operation in selector.endpoints:
        found = _tags(operation)
        included = ["include_operations"] if operation.id in include_operations else []
        included += [f"include_tags {tag!r}" for tag in found if tag in include_tags]
        dropped = ["exclude_operations"] if operation.id in exclude_operations else []
        dropped += [f"exclude_tags {tag!r}" for tag in found if tag in exclude_tags]
        match bool(included), bool(dropped):
            case True, True:
                excluded.append((operation, f"included by {', '.join(included)} but excluded by {', '.join(dropped)}"))
            case _, True:
                excluded.append((operation, f"excluded by {', '.join(dropped)}"))
            case False, _ if not everything:
                excluded.append((operation, "matched by no include rule"))
            case _:
                selected.append(operation)
    return tuple(selected), tuple(excluded)


def _identity(value: object) -> str:
    owner = value if callable(value) and hasattr(value, "__qualname__") else type(value)
    return f"{owner.__module__}.{owner.__qualname__}"


def _portable_diagnostic(diagnostic: Diagnostic) -> JSONObject:
    return {
        "code": diagnostic.code,
        "severity": diagnostic.severity,
        "stage": diagnostic.stage,
        "message": diagnostic.message,
        "source_uri": diagnostic.source_uri,
        "source_pointer": diagnostic.source_pointer,
        "operation": None
        if diagnostic.operation is None
        else {"document": ROOT_POINTER, "pointer": diagnostic.operation.pointer},
        "option_path": diagnostic.option_path,
        "artifact_path": diagnostic.artifact_path,
        "target_id": diagnostic.target_id,
    }


class _Planner:
    def __init__(
        self, models: _Models, effective: GenerateConfig, config: TargetConfig, generator: TargetGenerator
    ) -> None:
        from datamodel_code_generator import get_version  # noqa: PLC0415

        self.models = models
        self.effective = effective
        self.config = config
        self.generator = generator
        self.cwd = models.cwd
        self.root = (self.cwd / config.output.expanduser()).resolve()
        self.target_id = target_identity(generator.kind, config.package)
        self.documents = DocumentTable(models.product.batch, models.product.source_lease, models.source, self.root)
        self.operations: dict[str, OperationContract] = {}
        self.version, self.revision = get_version(), runtime_revision()

    def locate(self, path: Path, *, option_path: str) -> str:
        return relative_uri(self.cwd / path.expanduser(), self.root, option_path)

    def operation(self, reference: OperationRef) -> OperationContract | None:
        document = reference.document
        if document is None or document_identity(document, self.cwd) == self.models.source.identity:
            return self.operations.get(reference.pointer)
        return None

    def refer(self, reference: OperationRef | SchemaRef, *, option_path: str) -> JSONValue:
        match reference:
            case OperationRef() if (operation := self.operation(reference)) is not None:
                return self.documents.operation(operation.id)
            case SchemaRef() if (located := self.documents.pointer(reference.document, self.cwd)) is not None:
                return {"document": located, "pointer": reference.pointer}
        raise config_error(
            code="E_OPERATION_REF" if isinstance(reference, OperationRef) else "E_CONFIG_VALUE",
            option_path=option_path,
            message="The reference names no operation or document of the accepted input",
        )

    def portable(self, value: object, option_path: str) -> JSONValue:
        return portable(
            value, partial(self.locate, option_path=option_path), partial(self.refer, option_path=option_path)
        )

    def provenance(self) -> Iterator[JSONValue]:
        config = self.effective
        for name, info in type(config).model_fields.items():
            if name == "output" or (value := getattr(config, name)) == info.get_default(call_default_factory=True):
                continue
            option_path = f"model_config.{name}"
            if name in _SECRET_MODEL_OPTIONS:
                yield {"option_path": option_path, "identity": None, "digest": None, "opaque": True}
                continue
            try:
                digest = sha256(canonical_bytes(self.portable(value, option_path)))
            except TypeError:
                yield {"option_path": option_path, "identity": _identity(value), "digest": None, "opaque": True}
            else:
                yield {"option_path": option_path, "identity": None, "digest": digest, "opaque": False}

    def target_config(self, rendered: TargetRender) -> JSONObject:
        public: JSONObject = {}
        opaque: list[JSONValue] = []
        for item in fields(self.config):
            if item.name in type(self.config).manifest_exclusions:
                continue
            value = getattr(self.config, item.name)
            try:
                public[item.name] = self.portable(value, item.name)
            except TypeError:
                opaque.append({"option_path": item.name, "identity": _identity(value)})
        return {
            "public_options": public,
            "runtime_defaults": rendered.runtime_defaults,
            "protocol_metadata": rendered.protocol_metadata,
            "opaque_options": opaque,
        }

    def formatters(self) -> list[JSONValue]:
        config = self.config
        entries: list[JSONValue] = []
        if (settings := config.formatter_settings) is not None:
            uri = self.locate(settings, option_path="formatter_settings")
            entries.append({"identity": None, "uri": uri, "digest": None, "opaque": True})
        entries.extend(
            {"identity": name, "uri": None, "digest": None, "opaque": False} for name in config.custom_formatters
        )
        return entries

    def selection(self, selected: tuple[OperationContract, ...], excluded: tuple[Exclusion, ...]) -> JSONObject:
        selection = self.config.selection

        def references(selectors: tuple[OperationSelector, ...]) -> list[JSONValue]:
            return [
                self.portable(OperationRef(pointer=item) if isinstance(item, str) else item, "selection")
                for item in selectors
            ]

        return {
            "rules": {
                "include_operations": references(selection.include_operations),
                "include_tags": list(selection.include_tags),
                "exclude_operations": references(selection.exclude_operations),
                "exclude_tags": list(selection.exclude_tags),
                "reason": selection.reason,
            },
            "reason": selection.reason,
            "selected_operations": [self.documents.operation(operation.id) for operation in selected],
            "excluded_operations": [
                {"operation": self.documents.operation(operation.id), "reason": selection.reason}
                for operation, _ in excluded
            ],
        }

    def exclusions(self, excluded: tuple[Exclusion, ...]) -> tuple[Diagnostic, ...]:
        reason = self.config.selection.reason
        return tuple(
            Diagnostic(
                code="S_OPERATION_EXCLUDED",
                severity="info",
                stage="selection",
                message=f"{operation.method.upper()} {operation.path} is {grounds}: {reason}",
                source_uri=self.documents.root_uri,
                source_pointer=operation.id.use_site.pointer,
                operation=OperationRef(pointer=operation.id.use_site.pointer),
                target_id=self.target_id,
            )
            for operation, grounds in excluded
        )

    def model_path(self, artifact: ModelArtifact) -> Path:
        output = self.effective.output
        assert output is not None
        return output if self.models.single else output.joinpath(*artifact.path)

    def verify(self) -> None:
        problems = [
            Diagnostic(
                code="E_MODEL_MISMATCH",
                severity="error",
                stage="verify",
                message="The model file differs from the verified candidate"
                if present
                else "The verified candidate has no model file",
                artifact_path=path.as_posix(),
            )
            for artifact in self.models.artifacts
            if not (present := (location := self.cwd / (path := self.model_path(artifact))).is_file())
            or location.read_bytes() != artifact.content
        ]
        if problems:
            raise APIGenerationError(tuple(problems))

    def artifact(
        self,
        path: Path,
        kind: ArtifactKind,
        content: bytes | None,
        target_id: str | None,
        action: ArtifactAction | None = None,
    ) -> GeneratedArtifact:
        if action is None:
            location = self.cwd / path
            action = "unchanged" if location.is_file() and location.read_bytes() == content else "write"
        return GeneratedArtifact(
            path=path,
            kind=kind,
            action=action,
            content=content,
            sha256=None if content is None else sha256(content),
            target_id=target_id,
        )

    def manifest(
        self,
        rendered: TargetRender,
        plans: tuple[FilePlan, ...],
        model: JSONObject,
        chosen: tuple[tuple[OperationContract, ...], tuple[Exclusion, ...]],
        diagnostics: tuple[Diagnostic, ...],
    ) -> JSONObject:
        generator, config = self.generator, self.config
        return {
            "schema_version": 1,
            "target": {"id": self.target_id, "kind": generator.kind, "package": config.package, "root_uri": "."},
            "generator": {"name": GENERATOR_NAME, "version": self.version, "runtime_revision": self.revision},
            "inputs": {
                "root": self.documents.root,
                "documents": self.documents.documents,
                "provenance": list(self.provenance()),
                "target_config": self.target_config(rendered),
            },
            "model": model,
            "files": manifest_files(plans),
            "selection": self.selection(*chosen),
            "diagnostics": [_portable_diagnostic(item) for item in (*diagnostics, *rendered.persistent_diagnostics)],
            "extensions": {"hooks": [], "templates": [], "formatters": self.formatters()},
            "bindings": [
                {
                    "use_id": self.documents.use(binding.use),
                    "backend": binding.backend,
                    "strategy": binding.strategy,
                    "converter_strategy": binding.converter_strategy,
                    "adapter_identity": binding.adapter_identity,
                }
                for binding in rendered.bindings
            ],
            "target_data": {generator.kind: rendered.target_data},
        }

    def project(self) -> GeneratedProject:
        models, config, generator = self.models, self.config, self.generator
        selected, excluded = select_operations(models.product.batch, config.selection, models.source, self.cwd)
        self.operations = {
            operation.id.use_site.pointer: operation for operation in (*selected, *(item for item, _ in excluded))
        }
        state = read_target_state(self.root, generator.kind, config.package)
        rendered = generator.render(
            TargetRequest(
                config=config,
                model_config=self.effective,
                target_id=self.target_id,
                batch=models.product.batch,
                lease=models.product.source_lease,
                models=models.artifacts,
                operations=selected,
                excluded=excluded,
                documents=self.documents,
                state=state,
            )
        )
        if any(item.severity == "error" for item in rendered.diagnostics):
            raise APIGenerationError(rendered.diagnostics)
        if verifying := config.model_mode == "verify":
            self.verify()
        plans = plan_files(self.root, state, rendered.files, self.target_id)
        output = self.effective.output
        assert output is not None
        model = model_record(
            output_uri=self.locate(output, option_path="model_config.output"),
            package=config.model_package,
            mode=config.model_mode,
            artifacts=models.artifacts,
        )
        exclusions = self.exclusions(excluded)
        manifest = self.manifest(rendered, plans, model, (selected, excluded), exclusions)
        model_action: ArtifactAction | None = "unchanged" if verifying else None
        return GeneratedProject(
            target=generator.kind,
            artifacts=(
                *(
                    self.artifact(self.model_path(artifact), "model", artifact.content, None, model_action)
                    for artifact in models.artifacts
                ),
                *(
                    self.artifact(
                        config.output.joinpath(*plan.path.parts), "target", plan.content, self.target_id, plan.action
                    )
                    for plan in plans
                ),
                *(
                    ()
                    if verifying or models.metadata is None
                    else (self.artifact(models.metadata[0], "model_metadata", models.metadata[1], None),)
                ),
                *(() if models.lock is None else (self.artifact(models.lock[0], "remote_lock", models.lock[1], None),)),
                self.artifact(
                    config.output.joinpath(*INVENTORY_PATH.parts),
                    "model_inventory",
                    canonical_document({"schema_version": 1, "model": model}),
                    self.target_id,
                ),
                self.artifact(
                    config.output / MANIFEST_NAME, "target_manifest", canonical_document(manifest), self.target_id
                ),
            ),
            diagnostics=(*exclusions, *rendered.diagnostics, *rendered.persistent_diagnostics),
            generator_version=self.version,
            runtime_revision=self.revision,
        )


def render_target(
    input_: _GenerationInput, *, model_config: GenerateConfig, config: TargetConfig, generator: TargetGenerator
) -> GeneratedProject:
    """Render one target and its models once, returning every publication candidate without writing it."""
    from datamodel_code_generator import _uses_legacy_process_state  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]
    from datamodel_code_generator._process_state import PROCESS_STATE_LOCK  # noqa: PLC0415

    effective = prepare_target(input_, model_config, generator)
    if _uses_legacy_process_state(effective):
        with PROCESS_STATE_LOCK:
            models = _generate_models(input_, effective, config, Path.cwd(), use_output_cwd=True)
    else:
        with PROCESS_STATE_LOCK:
            cwd = Path.cwd()
        models = _generate_models(input_, effective, config, cwd, use_output_cwd=False)
    try:
        return _Planner(models, effective, config, generator).project()
    finally:
        models.product.close()
