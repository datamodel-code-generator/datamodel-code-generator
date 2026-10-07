"""Coordinate one target: validate settings, generate models once in staging, select operations, and plan files."""

from __future__ import annotations

import ast
import codecs
import json
import os
import sys
import tempfile
import unicodedata
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias
from urllib.parse import ParseResult

from datamodel_code_generator._api_manifest import (
    GENERATOR_NAME,
    MANIFEST_NAME,
    ROOT_URN,
    DocumentTable,
    PlannedFile,
    RootInput,
    canonical_document,
    config_error,
    document_identity,
    hand_edits,
    manifest_files,
    model_record,
    observe,
    observe_file,
    plan_files,
    read_target_state,
    relative_uri,
    runtime_revision,
    sha256,
    target_identity,
)
from datamodel_code_generator._api_types import (
    APIGenerationError,
    Diagnostic,
    GeneratedArtifact,
    GeneratedProject,
    OperationRef,
    attach_diagnostic,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from datamodel_code_generator import _GenerationInput  # pyright: ignore[reportPrivateUsage]
    from datamodel_code_generator._api_manifest import FilePlan, JSONObject, Observed, TargetState
    from datamodel_code_generator._api_types import (
        ArtifactAction,
        ArtifactKind,
        DiagnosticStage,
        GenerationReport,
        OperationSelection,
        OperationSelector,
        TargetKind,
    )
    from datamodel_code_generator._openapi_generation import ModelGenerationProduct, SourceLease
    from datamodel_code_generator._target_config import TargetConfig
    from datamodel_code_generator._target_contract import (
        GeneratedTypeContractBatch,
        ModelArtifact,
        OperationContract,
        OperationId,
    )
    from datamodel_code_generator.config import GenerateConfig
    from datamodel_code_generator.enums import DataModelType, OpenAPIScope
    from datamodel_code_generator.format import CodeFormatter
    from datamodel_code_generator.remote_lock import RemoteReferenceLock

Exclusion: TypeAlias = "tuple[OperationContract, str]"

_PYTHON_MINIMUM = (3, 11)
_PYTHON_MINIMUM_TEXT = f"{_PYTHON_MINIMUM[0]}.{_PYTHON_MINIMUM[1]}"
_README = PurePosixPath("README.md")


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetLayout:
    """Where the Python package lives below the target root, and whether that root is a distribution."""

    package: PurePosixPath
    distribution: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderedFile:
    """One text file a target rendered; the coordinator formats, heads, and encodes it.

    A verbatim file copies one of this package's own sources, such as a runtime module, that is valid for every
    supported target Python, so the coordinator heads and encodes it without formatting or checking it again.
    """

    path: PurePosixPath
    kind: str
    text: str
    verbatim: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetRequest:
    """Everything a target renders from: settings, the accepted model contracts, the selection, and the cwd."""

    config: TargetConfig
    layout: TargetLayout
    model_config: GenerateConfig
    target_id: str
    batch: GeneratedTypeContractBatch
    lease: SourceLease
    models: tuple[ModelArtifact, ...]
    operations: tuple[OperationContract, ...]
    excluded: tuple[Exclusion, ...]
    documents: DocumentTable
    resolve: Callable[[OperationRef], OperationContract | None]
    cwd: Path


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetRender:
    """A target's planned files and manifest data; run warnings and information are reported but never persisted.

    A target raises `APIGenerationError` for its failures instead of returning error diagnostics.
    """

    files: tuple[RenderedFile, ...]
    dependencies: tuple[str, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


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

    @property
    def selector(self) -> str:
        """Return the command-line option that selects the target, which its requirement errors name."""

    def render(self, request: TargetRequest) -> TargetRender:
        """Render the target's files from one accepted model generation."""


@dataclass(frozen=True, slots=True, kw_only=True)
class _Models:
    product: ModelGenerationProduct
    artifacts: tuple[ModelArtifact, ...]
    single: bool
    metadata: tuple[Path, bytes] | None
    lock: RemoteReferenceLock | None
    lock_state: Observed
    source: RootInput
    cwd: Path


def prepare_target(
    input_: _GenerationInput, model_config: GenerateConfig, generator: TargetGenerator
) -> GenerateConfig:
    """Validate target settings in the contract order and return the effective model settings.

    The model settings stay as given, so a target writes the models a model-only run writes; a setting the target
    needs but the settings lack is an `Error` that names the missing option.
    """
    from datamodel_code_generator import (  # noqa: PLC0415
        Error,
        _prepare_generate_facade_config,  # pyright: ignore[reportPrivateUsage]
    )
    from datamodel_code_generator.enums import DataModelType  # noqa: PLC0415

    if sys.version_info < _PYTHON_MINIMUM:
        raise config_error(
            code="E_PYTHON_UNSUPPORTED",
            option_path=None,
            message=f"The {generator.kind} target needs Python {_PYTHON_MINIMUM_TEXT} or later to run",
        )
    if model_config.output is None:
        raise config_error(
            code="E_CONFIG_VALUE",
            option_path="model_config.output",
            message=f"The {generator.kind} target needs model_config.output",
        )
    try:
        effective = _prepare_generate_facade_config(model_config)
    except Error as error:
        attach_diagnostic(
            error, Diagnostic(code="E_MODEL_CONFIG", severity="error", stage="config", message=str(error))
        )
        raise
    if (requirement := model_requirement(effective.openapi_scopes, generator.selector)) is not None:
        error = Error(requirement)
        attach_diagnostic(
            error,
            Diagnostic(
                code="E_MODEL_CONFIG",
                severity="error",
                stage="config",
                message=requirement,
                option_path="model_config.openapi_scopes",
            ),
        )
        raise error
    if (backend := effective.output_model_type) not in generator.backends:
        allowed = " or ".join(repr(item.value) for item in DataModelType if item in generator.backends)
        raise config_error(
            code=generator.unsupported_backend,
            option_path="model_config.output_model_type",
            message=f"The {generator.kind} target does not support {backend.value!r}; use {allowed}",
        )
    if effective.target_python_version.version_key < _PYTHON_MINIMUM:
        raise config_error(
            code="E_CONFIG_VALUE",
            option_path="model_config.target_python_version",
            message=f"The {generator.kind} target needs a target Python version of {_PYTHON_MINIMUM_TEXT} or later",
        )
    if (problem := _root_problem(input_, effective)) is None:
        return effective
    option_path, message = problem
    raise APIGenerationError((
        Diagnostic(code="E_INPUT_ROOT", severity="error", stage="input", message=message, option_path=option_path),
    ))


def model_requirement(scopes: Iterable[OpenAPIScope] | None, selector: str) -> str | None:
    """Return the "X requires Y" message for the api scope a target needs, or None while the scopes include it."""
    from datamodel_code_generator.enums import OpenAPIScope  # noqa: PLC0415

    if scopes is not None and OpenAPIScope.Api in scopes:
        return None
    return f"{selector} requires --openapi-scopes to include api"


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
            return RootInput(path.as_uri(), path.parent)
        case ParseResult():
            return RootInput(input_.geturl(), cwd)
    return RootInput(ROOT_URN, cwd)


def target_layout(config: TargetConfig) -> TargetLayout:
    """Return the package root below the target root: the root itself, or `src/<package>` in a distribution."""
    if config.package_mode == "standalone":
        return TargetLayout(package=PurePosixPath("src", *config.package.split(".")), distribution=True)
    return TargetLayout(package=PurePosixPath(), distribution=False)


def _bundled_models(root: Path, models: Path, model_package: str) -> PurePosixPath | None:
    package = root.joinpath("src", *model_package.split("."))
    for candidate in (package, package.with_name(f"{package.name}.py")):
        if models == candidate:
            return PurePosixPath(*candidate.relative_to(root).parts)
    return None


def _check_layout(config: TargetConfig, output: Path, cwd: Path) -> None:
    root, models = (cwd / config.output.expanduser()).resolve(), (cwd / output.expanduser()).resolve()
    package = root.joinpath(*target_layout(config).package.parts)
    if package == models or package in models.parents or models in package.parents:
        raise config_error(
            code="E_PATH_COLLISION",
            option_path="output",
            message="The target package and the model output must not contain each other",
        )
    if config.package_mode != "standalone":
        return
    match _bundled_models(root, models, config.model_package), config.model_dependency:
        case PurePosixPath(), str():
            raise config_error(
                code="E_CONFIG_CONFLICT",
                option_path="model_dependency",
                message="A distribution that bundles its models declares no model_dependency",
            )
        case None, None:
            raise config_error(
                code="E_CONFIG_VALUE",
                option_path="model_dependency",
                message="A distribution needs its models under src/ or an explicit model_dependency",
            )
        case _:
            pass


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
    from datamodel_code_generator._target_contract import ModelArtifact  # noqa: PLC0415

    if staged.is_file():
        return (ModelArtifact((output.name,), staged.read_bytes(), encoding),)
    files = sorted((path.relative_to(staged).parts, path) for path in staged.rglob("*") if path.is_file())
    return tuple(ModelArtifact(parts, path.read_bytes(), encoding) for parts, path in files)


def _generate_models(
    input_: _GenerationInput, config: GenerateConfig, target: TargetConfig, cwd: Path, *, use_output_cwd: bool
) -> _Models:
    from datamodel_code_generator import _run_generation  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]
    from datamodel_code_generator._openapi_generation import TargetGenerationSession  # noqa: PLC0415
    from datamodel_code_generator._target_binding import MetadataCycleError  # noqa: PLC0415

    source = _root_input(input_, cwd)
    output = config.output
    assert output is not None
    _check_layout(target, output, cwd)
    prepared, lock = _remote_lock(input_, config, cwd)
    lock_state = None if lock is None else observe_file(lock.path)
    output = prepared.output
    assert output is not None
    with ExitStack() as stack:
        staged_output = _staging(stack, output, cwd) / (output.name or "output")
        if (cwd / output).is_dir():
            staged_output.mkdir()
        updates: dict[str, Any] = {"output": staged_output}
        if (metadata := config.emit_model_metadata) is not None:
            updates["emit_model_metadata"] = _staging(stack, metadata, cwd) / (metadata.name or "model-metadata.json")
        staged = prepared.model_copy(update=updates)
        staged._logical_output = output  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        staged._logical_model_metadata = prepared.emit_model_metadata  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        if lock is not None or not staged.remote_lock_resolved:
            staged.resolve_remote_lock(lock)
        session = TargetGenerationSession(
            output=output, model_package=target.model_package, root_selector_document=source.identity
        )
        try:
            _run_generation(input_, staged, cwd, use_output_cwd=use_output_cwd, capture=session)
            artifacts = _staged_models(staged_output, output, prepared.encoding)
            metadata_file = None if metadata is None else (metadata, updates["emit_model_metadata"].read_bytes())
            product = session.take_product(artifacts, allow_empty_api=True)
        except MetadataCycleError as error:
            from datamodel_code_generator._api_manifest import persistent_uri  # noqa: PLC0415

            raise APIGenerationError((
                Diagnostic(
                    code="E_INPUT_CYCLE",
                    severity="error",
                    stage="input",
                    message=str(error),
                    source_uri=persistent_uri(
                        document_identity(error.document, source.base),
                        (cwd / target.output.expanduser()).resolve(),
                        "input",
                    ),
                    source_pointer=error.pointer,
                ),
            )) from error
        finally:
            session.close()
        return _Models(
            product=product,
            artifacts=artifacts,
            single=staged_output.is_file(),
            metadata=metadata_file,
            lock=lock,
            lock_state=lock_state,
            source=source,
            cwd=cwd,
        )


def _is_python(path: PurePosixPath) -> bool:
    return path.suffix in {".py", ".pyi"}


def _normalized(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    return f"{text}\n" if text else ""


def _toml_array(values: Iterable[str]) -> str:
    return f"[{', '.join(json.dumps(value) for value in values)}]"


def _dependencies(rendered: TargetRender, model_dependency: str | None) -> tuple[str, ...]:
    """Return what a generated package needs at run time: the target's dependencies and any external models."""
    return rendered.dependencies if model_dependency is None else (*rendered.dependencies, model_dependency)


def _tags(operation: OperationContract) -> tuple[str, ...]:
    from datamodel_code_generator._target_contract import LiteralScalar, LiteralSequence  # noqa: PLC0415

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
        self.documents = DocumentTable(models.product.batch, models.source, self.root)
        self.operations: dict[str, OperationContract] = {}
        self.observed: dict[Path, Observed] = {}
        self.version, self.revision = get_version(), runtime_revision()
        self.layout = target_layout(config)

    def locate(self, path: Path, *, option_path: str) -> str:
        return relative_uri(self.cwd / path.expanduser(), self.root, option_path)

    def operation(self, reference: OperationRef) -> OperationContract | None:
        document = reference.document
        if document is None or document_identity(document, self.cwd) == self.models.source.identity:
            return self.operations.get(reference.pointer)
        return None

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
        planned: tuple[ArtifactAction, Observed] | None = None,
    ) -> GeneratedArtifact:
        location = self.cwd / path
        if planned is None:
            current = location.read_bytes() if location.is_file() else None
            planned = ("unchanged" if current == content else "write", observe(current))
        action, self.observed[location] = planned
        return GeneratedArtifact(
            path=path,
            kind=kind,
            action=action,
            content=content,
            sha256=None if content is None else sha256(content),
            target_id=target_id,
        )

    def lock_artifacts(self) -> tuple[GeneratedArtifact, ...]:
        if (lock := self.models.lock) is None:
            return ()
        with tempfile.TemporaryDirectory(prefix=".datamodel-codegen-") as directory:
            try:
                content = staged.read_bytes() if isinstance(staged := lock.stage(Path(directory)), Path) else b""
            finally:
                lock.discard_stage()
        current = lock.path.read_bytes() if lock.path.is_file() else None
        action: ArtifactAction = "unchanged" if current == content else "write"
        return (self.artifact(lock.path, "remote_lock", content, None, (action, self.models.lock_state)),)

    def check_state(self, state: TargetState, manifest: GeneratedArtifact) -> None:
        if self.observed[self.cwd / manifest.path] == state.snapshot:
            return
        raise APIGenerationError((
            Diagnostic(
                code="E_STATE_CHANGED",
                severity="error",
                stage="ownership",
                message="The manifest changed while the target was planned",
                artifact_path=manifest.path.as_posix(),
                target_id=self.target_id,
            ),
        ))

    def check_collisions(self, artifacts: tuple[GeneratedArtifact, ...]) -> None:
        seen: set[str] = set()
        problems: list[Diagnostic] = []
        for artifact in artifacts:
            location = self.cwd / artifact.path
            key = unicodedata.normalize("NFC", str(location.parent.resolve() / location.name)).casefold()
            if key in seen:
                problems.append(
                    Diagnostic(
                        code="E_PATH_COLLISION",
                        severity="error",
                        stage="ownership",
                        message="Two generated files resolve to the same path",
                        artifact_path=artifact.path.as_posix(),
                        target_id=artifact.target_id,
                    )
                )
            seen.add(key)
        if problems:
            raise APIGenerationError(tuple(problems))

    def manifest(self, plans: tuple[FilePlan, ...], model: JSONObject) -> JSONObject:
        return {
            "format": 1,
            "generator": {"name": GENERATOR_NAME, "version": self.version},
            "target": {"kind": self.generator.kind, "package": self.config.package},
            "model": model,
            "files": manifest_files(plans),
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
                layout=self.layout,
                model_config=self.effective,
                target_id=self.target_id,
                batch=models.product.batch,
                lease=models.product.source_lease,
                models=models.artifacts,
                operations=selected,
                excluded=excluded,
                documents=self.documents,
                resolve=self.operation,
                cwd=self.cwd,
            )
        )
        if verifying := config.model_mode == "verify":
            self.verify()
        finished = _Finisher(self).finish(rendered)
        plans = plan_files(self.root, state, finished, self.target_id)
        output = self.effective.output
        assert output is not None
        model = model_record(output=self.locate(output, option_path="model_config.output"), artifacts=models.artifacts)
        exclusions = self.exclusions(excluded)
        manifest = self.manifest(plans, model)
        artifacts = (
            *(
                self.artifact(
                    self.model_path(artifact),
                    "model",
                    artifact.content,
                    None,
                    ("unchanged", observe(artifact.content)) if verifying else None,
                )
                for artifact in models.artifacts
            ),
            *(
                self.artifact(
                    config.output.joinpath(*plan.path.parts),
                    "target",
                    plan.content,
                    self.target_id,
                    (plan.action, plan.observed),
                )
                for plan in plans
            ),
            *(
                ()
                if verifying or models.metadata is None
                else (self.artifact(models.metadata[0], "model_metadata", models.metadata[1], None),)
            ),
            *self.lock_artifacts(),
            manifest_artifact := self.artifact(
                config.output / MANIFEST_NAME, "target_manifest", canonical_document(manifest), self.target_id
            ),
        )
        self.check_state(state, manifest_artifact)
        self.check_collisions(artifacts)
        return GeneratedProject(
            target=generator.kind,
            artifacts=artifacts,
            diagnostics=(
                *state.diagnostics,
                *exclusions,
                *rendered.diagnostics,
                *hand_edits(state, plans, self.target_id),
            ),
            generator_version=self.version,
            runtime_revision=self.revision,
            dependencies=_dependencies(rendered, config.model_dependency),
        )


class _Finisher:
    def __init__(self, planner: _Planner) -> None:
        self.config = planner.config
        self.effective = planner.effective
        self.kind = planner.generator.kind
        self.root = planner.root
        self.cwd = planner.cwd
        self.layout = planner.layout
        self.target_id = planner.target_id
        self.timestamp = (
            datetime.now(timezone.utc).isoformat(timespec="seconds") if self.config.include_timestamp else None
        )

    def header(self) -> str:
        config = self.config
        lines = [] if codecs.lookup(config.encoding).name == "utf-8" else [f"-*- coding: {config.encoding} -*-"]
        lines += (
            [f"Generated by datamodel-code-generator; target={self.kind}; schema_version=1"]
            if config.header is None
            else config.header.splitlines()
        )
        if self.timestamp is not None:
            lines.append(f"timestamp: {self.timestamp}")
        return "".join(f"# {line}\n" if line else "#\n" for line in lines)

    def pyproject(self, rendered: TargetRender) -> str:
        config, output = self.config, self.effective.output
        assert output is not None
        bundled = _bundled_models(self.root, (self.cwd / output.expanduser()).resolve(), config.model_package)
        included = (self.layout.package.as_posix(), *(() if bundled is None else (bundled.as_posix(),)))
        readme = ("README.md",) if any(file.path == _README for file in rendered.files) else ()
        documentation = tuple(file.path.as_posix() for file in rendered.files if file.kind == "documentation")
        return "\n".join((
            "[build-system]",
            'requires = ["hatchling>=1.27"]',
            'build-backend = "hatchling.build"',
            "",
            "[project]",
            f"name = {json.dumps(config.distribution_name)}",
            f"version = {json.dumps(config.package_version)}",
            f'requires-python = ">={self.effective.target_python_version.value}"',
            *(f'readme = "{path}"' for path in readme),
            f"dependencies = {_toml_array(_dependencies(rendered, config.model_dependency))}",
            "",
            "[tool.hatch.build.targets.wheel]",
            f"only-include = {_toml_array(included)}",
            'sources = ["src"]',
            "",
            "[tool.hatch.build.targets.sdist]",
            f"only-include = {_toml_array((*included, *readme, *documentation, 'pyproject.toml'))}",
        ))

    def layout_files(self, rendered: TargetRender) -> tuple[RenderedFile, ...]:
        typed = RenderedFile(path=self.layout.package / "py.typed", kind="typing", text="")
        if not self.layout.distribution:
            return (typed,)
        return typed, RenderedFile(
            path=PurePosixPath("pyproject.toml"), kind="pyproject", text=self.pyproject(rendered)
        )

    def check_sources(self, files: Iterable[tuple[PurePosixPath, str]], stage: DiagnosticStage) -> None:
        version = self.effective.target_python_version.version_key
        if problems := tuple(
            Diagnostic(
                code="E_TARGET_SOURCE",
                severity="error",
                stage=stage,
                message=f"The generated Python source is invalid: {error}",
                artifact_path=path.as_posix(),
                target_id=self.target_id,
            )
            for path, text in files
            if _is_python(path) and (error := _syntax_error(path.as_posix(), text, version)) is not None
        ):
            raise APIGenerationError(problems)

    def finish(self, rendered: TargetRender) -> tuple[PlannedFile, ...]:
        from datamodel_code_generator.format import CodeFormatter  # noqa: PLC0415

        config = self.config
        files = (*rendered.files, *self.layout_files(rendered))
        self.check_sources(((file.path, file.text) for file in files if not file.verbatim), "target")
        settings = config.formatter_settings
        formatter = CodeFormatter(
            self.effective.target_python_version,
            self.cwd if settings is None else self.cwd / settings.expanduser(),
            None,
            skip_string_normalization=True,
            known_third_party=None,
            custom_formatters=list(config.custom_formatters),
            custom_formatters_kwargs=dict(config.custom_formatter_kwargs),
            encoding=config.encoding,
            formatters=list(config.formatters),
            builtin_format_line_length=None,
            use_type_checking_imports=False,
            defer_formatting=False,
            formatter_cwd=self.cwd,
        )
        header = self.header()
        texts = [
            (
                file,
                _normalized(header + _formatted(file, formatter) if _is_python(file.path) else file.text),
            )
            for file in files
        ]
        self.check_sources(((file.path, text) for file, text in texts if not file.verbatim), "format")
        return tuple(
            PlannedFile(
                path=file.path,
                content=text.encode(config.encoding if _is_python(file.path) else "utf-8"),
            )
            for file, text in texts
        )


def _formatted(file: RenderedFile, formatter: CodeFormatter) -> str:
    return file.text if file.verbatim else formatter.format_code(file.text)


def _syntax_error(filename: str, text: str, version: tuple[int, int]) -> str | None:
    """Return why a source is invalid for the target grammar, reading PEP 695 aliases older hosts cannot parse."""
    try:
        compile(ast.parse(text, filename, feature_version=version), filename, "exec", dont_inherit=True)
    except SyntaxError as error:
        if version >= (3, 12) > sys.version_info[:2]:
            from datamodel_code_generator._builtin_formatter import (  # noqa: PLC0415
                _replace_pep695_type_aliases_with_placeholders,
            )

            if (replaced := _replace_pep695_type_aliases_with_placeholders(text)) != text:
                return _syntax_error(filename, replaced, version)
        return error.msg
    return None


def _run_models(input_: _GenerationInput, effective: GenerateConfig, config: TargetConfig) -> _Models:
    from datamodel_code_generator import _uses_legacy_process_state  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]
    from datamodel_code_generator._process_state import PROCESS_STATE_LOCK  # noqa: PLC0415

    if _uses_legacy_process_state(effective):
        with PROCESS_STATE_LOCK:
            return _generate_models(input_, effective, config, Path.cwd(), use_output_cwd=True)
    with PROCESS_STATE_LOCK:
        cwd = Path.cwd()
    return _generate_models(input_, effective, config, cwd, use_output_cwd=False)


def _plan(
    input_: _GenerationInput, model_config: GenerateConfig, config: TargetConfig, generator: TargetGenerator
) -> tuple[_Planner, GeneratedProject]:
    from datamodel_code_generator import Error  # noqa: PLC0415

    effective = prepare_target(input_, model_config, generator)
    try:
        models = _run_models(input_, effective, config)
    except Error as error:
        attach_diagnostic(error, Diagnostic(code="E_MODEL_PARSE", severity="error", stage="model", message=str(error)))
        raise
    try:
        planner = _Planner(models, effective, config, generator)
        return planner, planner.project()
    finally:
        models.product.close()


def render_target(
    input_: _GenerationInput, *, model_config: GenerateConfig, config: TargetConfig, generator: TargetGenerator
) -> GeneratedProject:
    """Render one target and its models once, returning every publication candidate without writing it."""
    return _plan(input_, model_config, config, generator)[1]


def generate_target(
    input_: _GenerationInput, *, model_config: GenerateConfig, config: TargetConfig, generator: TargetGenerator
) -> GenerationReport:
    """Render one target and its models once, then publish every change together through one journal."""
    planner, project = _plan(input_, model_config, config, generator)
    from datamodel_code_generator._api_publication import publish_project  # noqa: PLC0415

    return publish_project(project, planner.observed, cwd=planner.models.cwd, lock=planner.models.lock)
