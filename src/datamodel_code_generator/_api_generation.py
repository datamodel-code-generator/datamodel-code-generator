"""Coordinate one target: validate settings, generate models once in staging, and plan files."""

from __future__ import annotations

import ast
import os
import sys
import tempfile
import unicodedata
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol
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
        TargetKind,
    )
    from datamodel_code_generator._openapi_generation import ModelGenerationProduct, SourceLease
    from datamodel_code_generator._target_config import TargetConfig
    from datamodel_code_generator._target_contract import (
        GeneratedTypeContractBatch,
        ModelArtifact,
        OperationContract,
    )
    from datamodel_code_generator._target_format import TargetCodeFormatter
    from datamodel_code_generator.config import GenerateConfig
    from datamodel_code_generator.enums import DataModelType, OpenAPIScope
    from datamodel_code_generator.remote_lock import RemoteReferenceLock

_PYTHON_MINIMUM = (3, 11)
_PYTHON_MINIMUM_TEXT = f"{_PYTHON_MINIMUM[0]}.{_PYTHON_MINIMUM[1]}"


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
    """Everything a target renders from: settings, the accepted model contracts, the root operations, and the cwd."""

    config: TargetConfig
    model_config: GenerateConfig
    target_id: str
    batch: GeneratedTypeContractBatch
    lease: SourceLease
    models: tuple[ModelArtifact, ...]
    operations: tuple[OperationContract, ...]
    documents: DocumentTable
    resolve: Callable[[OperationRef], OperationContract | None]
    cwd: Path

    @property
    def unresolved(self) -> str:
        """Say why an operation reference resolved to nothing, naming the include filter when one is set."""
        if self.model_config.openapi_include_paths:
            return "selects no root path operation that --openapi-include-paths keeps"
        return "selects no root path operation"


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetRender:
    """A target's planned files and manifest data; run warnings and information are reported but never persisted.

    A target raises `APIGenerationError` for its failures instead of returning error diagnostics.
    """

    files: tuple[RenderedFile, ...]
    dependencies: tuple[str, ...] = ()


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
    filename: str
    settings_path: Path
    formatter_cwd: Path
    custom_header: str | None


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
    effective = _prepare_generate_facade_config(model_config)
    if (requirement := model_requirement(effective.openapi_scopes, generator.selector)) is not None:
        raise Error(requirement)
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


def _check_layout(config: TargetConfig, output: Path, cwd: Path) -> None:
    package, models = (cwd / config.output.expanduser()).resolve(), (cwd / output.expanduser()).resolve()
    if package == models or package in models.parents or models in package.parents:
        raise config_error(
            code="E_PATH_COLLISION",
            option_path="output",
            message="The target package and the model output must not contain each other",
        )


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
    from datamodel_code_generator import (  # noqa: PLC0415
        _absolute_generation_path,  # pyright: ignore[reportPrivateUsage]
        _default_input_filename,  # pyright: ignore[reportPrivateUsage]
        _output_context_path,  # pyright: ignore[reportPrivateUsage]
        _read_custom_file_header,  # pyright: ignore[reportPrivateUsage]
        _run_generation,  # pyright: ignore[reportPrivateUsage]
        _settings_path_from,  # pyright: ignore[reportPrivateUsage]
    )
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
    formatter_cwd = _output_context_path(_absolute_generation_path(output, cwd), cwd)
    settings_path = _settings_path_from(formatter_cwd, prepared.settings_path)
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
            filename=prepared.input_filename or _default_input_filename(input_),
            settings_path=settings_path,
            formatter_cwd=formatter_cwd,
            custom_header=_read_custom_file_header(
                prepared.custom_file_header,
                _absolute_generation_path(prepared.custom_file_header_path, cwd),
                prepared.encoding,
            ),
        )


def _is_python(path: PurePosixPath) -> bool:
    return path.suffix in {".py", ".pyi"}


def _normalized(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    return f"{text}\n" if text else ""


def _root_operations(batch: GeneratedTypeContractBatch) -> tuple[OperationContract, ...]:
    """Return the operations of the root document's `paths`, in document order.

    A component path item that no included path references is walked on its own; it has no URL, so it is left out.
    """
    root = batch.documents[0].id
    return tuple(
        operation
        for operation in batch.operations
        if operation.id.parent is None
        and operation.id.kind == "path"
        and operation.id.use_site.document == root
        and operation.id.use_site.pointer.startswith("/paths/")
    )


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

    def locate(self, path: Path, *, option_path: str) -> str:
        return relative_uri(self.cwd / path.expanduser(), self.root, option_path)

    def operation(self, reference: OperationRef) -> OperationContract | None:
        document = reference.document
        if document is None or document_identity(document, self.cwd) == self.models.source.identity:
            return self.operations.get(reference.pointer)
        return None

    def model_path(self, artifact: ModelArtifact) -> Path:
        output = self.effective.output
        assert output is not None
        return output if self.models.single else output.joinpath(*artifact.path)

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
        operations = _root_operations(models.product.batch)
        self.operations = {operation.id.use_site.pointer: operation for operation in operations}
        state = read_target_state(self.root, generator.kind, config.package)
        rendered = generator.render(
            TargetRequest(
                config=config,
                model_config=self.effective,
                target_id=self.target_id,
                batch=models.product.batch,
                lease=models.product.source_lease,
                models=models.artifacts,
                operations=operations,
                documents=self.documents,
                resolve=self.operation,
                cwd=self.cwd,
            )
        )
        finished = _Finisher(self).finish(rendered)
        plans = plan_files(self.root, state, finished, self.target_id)
        output = self.effective.output
        assert output is not None
        model = model_record(output=self.locate(output, option_path="model_config.output"), artifacts=models.artifacts)
        manifest = self.manifest(plans, model)
        artifacts = (
            *(
                self.artifact(self.model_path(artifact), "model", artifact.content, None)
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
                if models.metadata is None
                else (self.artifact(models.metadata[0], "model_metadata", models.metadata[1], None),)
            ),
            *self.lock_artifacts(),
            manifest_artifact := self.artifact(
                config.output / MANIFEST_NAME, "target_manifest", canonical_document(manifest), self.target_id
            ),
        )
        self.check_state(state, manifest_artifact)
        self.check_collisions(artifacts)
        hand_edits(state, plans)
        return GeneratedProject(
            target=generator.kind,
            artifacts=artifacts,
            generator_version=self.version,
            runtime_revision=self.revision,
            dependencies=rendered.dependencies,
        )


class _Finisher:
    def __init__(self, planner: _Planner) -> None:
        self.config = planner.config
        self.effective = planner.effective
        self.cwd = planner.cwd
        self.target_id = planner.target_id
        self.models = planner.models

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
        """Format, head, and encode every Python file like a model file, from the model output settings."""
        from datamodel_code_generator import (  # noqa: PLC0415
            _build_file_header_parts,  # pyright: ignore[reportPrivateUsage]
            _build_module_content,  # pyright: ignore[reportPrivateUsage]
            _format_file_header,  # pyright: ignore[reportPrivateUsage]
        )
        from datamodel_code_generator._target_format import TargetCodeFormatter  # noqa: PLC0415

        effective, models = self.effective, self.models
        files = (*rendered.files, RenderedFile(path=PurePosixPath("py.typed"), kind="typing", text=""))
        self.check_sources(((file.path, file.text) for file in files if not file.verbatim), "target")
        formatter = TargetCodeFormatter(
            effective.target_python_version,
            models.settings_path,
            effective.wrap_string_literal,
            skip_string_normalization=not effective.use_double_quotes,
            model_package=self.config.model_package,
            custom_formatters=effective.custom_formatters,
            custom_formatters_kwargs=effective.custom_formatters_kwargs,
            encoding=effective.encoding,
            formatters=effective.formatters,
            builtin_format_line_length=effective.builtin_format_line_length,
            use_type_checking_imports=False,
            defer_formatting=True,
            formatter_cwd=models.formatter_cwd,
        )
        custom_header = models.custom_header
        header = _format_file_header(*_build_file_header_parts(custom_header, effective), models.filename)
        formatted = {file.path for file in files if _is_python(file.path) and not file.verbatim}
        texts: dict[PurePosixPath, str] = {}
        for file in files:
            body = formatter.format_code(file.text) if file.path in formatted else file.text
            texts[file.path] = (
                _build_module_content(body, header, has_custom_file_header=bool(custom_header))
                if _is_python(file.path)
                else body
            )
        self.defer(formatter, texts, formatted)
        texts = {path: _normalized(text) for path, text in texts.items()}
        self.check_sources(((file.path, texts[file.path]) for file in files if not file.verbatim), "format")
        return tuple(
            PlannedFile(path=path, content=text.encode(effective.encoding if _is_python(path) else "utf-8"))
            for path, text in texts.items()
        )

    def defer(self, formatter: TargetCodeFormatter, texts: dict[PurePosixPath, str], paths: set[PurePosixPath]) -> None:
        """Run the Ruff formatters once over the formatted files, staged beside the target like a model directory."""
        from datamodel_code_generator._format_types import Formatter  # noqa: PLC0415

        if not paths or not {Formatter.RUFF_CHECK, Formatter.RUFF_FORMAT}.intersection(formatter.formatters):
            return
        encoding = self.effective.encoding
        with ExitStack() as stack:
            staged = _staging(stack, self.config.output, self.cwd)
            for path in paths:
                (location := staged.joinpath(*path.parts)).parent.mkdir(parents=True, exist_ok=True)
                location.write_text(texts[path], encoding=encoding)
            formatter.format_directory(staged)
            texts.update((path, staged.joinpath(*path.parts).read_text(encoding=encoding)) for path in paths)


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
    effective = prepare_target(input_, model_config, generator)
    if not effective.disable_timestamp and effective._generation_timestamp is None:  # noqa: SLF001
        effective = effective.model_copy()
        effective._generation_timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()  # noqa: SLF001
    models = _run_models(input_, effective, config)
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
