"""Publish or check the selected generation target with model CLI reporting."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from datamodel_code_generator import Error, InvalidClassNameError
from datamodel_code_generator._api_manifest import document_identity, shown
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, OperationRef

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Iterable, Sequence

    from datamodel_code_generator.__main__ import OutputComparison
    from datamodel_code_generator._api_generation import PlannedTarget
    from datamodel_code_generator._api_types import GeneratedProject, OperationSelector
    from datamodel_code_generator._fastapi.config import FastAPIConfig
    from datamodel_code_generator._publication import StagedFile
    from datamodel_code_generator._structured_output import CheckDifferencePayload

_OK: Final = 0
_DIFF: Final = 1
_ERROR: Final = 2
_CONFLICTS: Final = (("watch", "--watch"), ("diff_against", "--diff-against"), ("input_model", "--input-model"))
_SERVER_SETTINGS: Final = ("layout", "handler_mode", "include_request", "body_mode", "router_names")
_OPERATION_SETTINGS: Final = ("handler_modes", "body_modes", "operation_names", "parameter_names")


def run_target(  # noqa: PLR0913, PLR0917
    args: Sequence[str],
    namespace: Namespace,
    config: Any,
    pyproject_path: Path | None,
    batch: list[tuple[str, PlannedTarget, str]] | None = None,
    lock: Any = None,
    job: str = "",
) -> int:
    """Generate or check the selected target from the finalized CLI config, reporting errors like model runs.

    A model setting the target needs but the config lacks is refused like a model option conflict, before the run. A
    batch `job` appends the planned target to the targets its `batch` has planned instead of publishing it, and its
    models record into the batch's remote `lock`.
    """
    from datamodel_code_generator._api_generation import model_requirement  # noqa: PLC0415
    from datamodel_code_generator._fastapi.target import FastAPITarget  # noqa: PLC0415

    if requirement := model_requirement(config.openapi_scopes, FastAPITarget.selector):
        print(f"Error: {requirement}", file=sys.stderr)  # noqa: T201
        return _ERROR
    try:
        return _run(args, namespace, config, pyproject_path, batch, lock, job)
    except Exception as error:  # noqa: BLE001
        _failure(error, encoding=config.encoding)
        return _ERROR


def _run(  # noqa: PLR0913, PLR0917
    args: Sequence[str],
    namespace: Namespace,
    config: Any,
    pyproject_path: Path | None,
    batch: list[tuple[str, PlannedTarget, str]] | None,
    lock: Any,
    job: str,
) -> int:
    from datamodel_code_generator.__main__ import (  # noqa: PLC0415
        _target_lockfile,  # pyright: ignore[reportPrivateUsage, reportUnknownVariableType]
        _target_settings,  # pyright: ignore[reportPrivateUsage, reportUnknownVariableType]
        _write_comparison_output,  # pyright: ignore[reportPrivateUsage]
    )
    from datamodel_code_generator._api_generation import (  # noqa: PLC0415
        generate_target,
        plan_target,
        prepare_target,
        publish_target,
        render_target,
    )
    from datamodel_code_generator._fastapi.target import FastAPITarget  # noqa: PLC0415

    lockfile = _target_lockfile(config, pyproject_path)
    if flags := [flag for name, flag in _CONFLICTS if getattr(config, name)]:
        raise _refused(flags)
    target = _server_config(config, namespace, pyproject_path)
    effective = _target_settings(config, args, lockfile)
    if batch is not None:
        effective.resolve_remote_lock(lock)
    generator = FastAPITarget()
    if (source := config.url or config.input) is None:
        prepare_target("", effective, generator)
        source = sys.stdin.read()
    if config.check:
        project = render_target(source, model_config=effective, config=target, generator=generator)
        comparison = _compare_target(project, config.output, target.output, config.encoding)
        _write_comparison_output(comparison, namespace.output_format)
        return _DIFF if comparison.differences else _OK
    json_output = namespace.output_format == "json"
    if batch is not None:
        timestamp = next((earlier.timestamp for _, earlier, _ in batch if earlier.timestamp is not None), None)
        planned = plan_target(source, model_config=effective, config=target, generator=generator, timestamp=timestamp)
        if json_output:
            print(_target_json(planned.project, config.output, target.output, config.encoding))  # noqa: T201
        batch.append((job, planned, _next_step(target, planned.project.dependencies)))
        return _OK
    if json_output:
        planned = plan_target(source, model_config=effective, config=target, generator=generator, publish=True)
        project = planned.project
        output = _target_json(project, config.output, target.output, config.encoding)
        publish_target(planned)
        print(output)  # noqa: T201
        dependencies = project.dependencies
    else:
        dependencies = generate_target(source, model_config=effective, config=target, generator=generator).dependencies
    print(_next_step(target, dependencies), file=sys.stderr)  # noqa: T201
    return _OK


def publish_targets(files: Iterable[StagedFile], targets: Sequence[tuple[str, PlannedTarget, str]]) -> None:
    """Publish the staged files of a batch and the targets its server jobs planned through one journal.

    Each target's dependency notice follows the publication on stderr, as after a single run.
    """
    from datamodel_code_generator._api_generation import publish_planned  # noqa: PLC0415

    publish_planned(files, [(job, planned) for job, planned, _ in targets])
    for _, _, notice in targets:
        print(notice, file=sys.stderr)  # noqa: T201


def _shown(path: Path) -> str:
    """Return a path relative to the working directory when it lies inside it."""
    return shown(path, Path.cwd()).as_posix()


def _target_json(project: GeneratedProject, models: Path, target: Path, encoding: str) -> str:
    """Emit rendered model and target text, read like their published files, with the existing generation payload."""
    import os  # noqa: PLC0415
    from io import BytesIO, TextIOWrapper  # noqa: PLC0415

    from datamodel_code_generator._structured_output import GeneratedFilePayload, generation_output_json  # noqa: PLC0415

    artifacts = [
        (
            artifact.path,
            content,
            encoding if artifact.kind == "model" or artifact.path.suffix in {".py", ".pyi"} else "utf-8",
        )
        for artifact in project.artifacts
        if artifact.kind in {"model", "target"} and (content := artifact.content) is not None
    ]
    try:
        parent = Path(os.path.commonpath((models.parent, target.parent)))
    except ValueError:
        parent = None
    files = [
        GeneratedFilePayload(
            path=(path.absolute() if parent is None else path.relative_to(parent)).as_posix(),
            content=TextIOWrapper(BytesIO(content), encoding=codec).read(),
        )
        for path, content, codec in sorted(artifacts, key=lambda artifact: artifact[0].parts)
    ]
    return generation_output_json(files, output=models.as_posix())


def _compare_target(project: GeneratedProject, models: Path, target: Path, encoding: str) -> OutputComparison:
    """Compare rendered text and Python output roots through the model comparison path."""
    from tempfile import TemporaryDirectory  # noqa: PLC0415

    from datamodel_code_generator.__main__ import (  # noqa: PLC0415
        OutputComparison,
        OutputComparisonOptions,
        _compare_generated_outputs,  # pyright: ignore[reportPrivateUsage]
    )

    differences: list[CheckDifferencePayload] = []
    contents: list[str] = []
    with TemporaryDirectory(prefix="datamodel-codegen-check-") as directory:
        staging = Path(directory)
        for kind, output, is_directory in (("model", models, not models.suffix), ("target", target, True)):
            if not is_directory and not any(artifact.kind == kind for artifact in project.artifacts):
                continue
            staged_root = staging / kind
            staged_root.mkdir()
            non_python: list[tuple[Path, Path]] = []
            for artifact in project.artifacts:
                if artifact.kind != kind or artifact.content is None:
                    continue
                path = artifact.path
                staged = staged_root / (path.relative_to(output) if is_directory else path.name)
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(artifact.content)
                if is_directory and path.suffix != ".py":
                    non_python.append((staged, path))
            comparisons = [(staged_root if is_directory else staged_root / output.name, output, is_directory, encoding)]
            comparisons.extend((staged, path, False, "utf-8") for staged, path in non_python)
            for generated, actual, directory_output, codec in comparisons:
                try:
                    compared = _compare_generated_outputs(
                        generated,
                        actual,
                        codec,
                        OutputComparisonOptions(
                            is_directory_output=directory_output,
                            single_file_display_path=_shown(actual),
                            directory_display_path=_shown(actual),
                        ),
                    )
                except UnicodeError as error:
                    message = f"{_shown(actual)}: Output is not text in encoding {codec!r}: {error}"
                    raise Error(message) from error
                differences.extend(compared.differences)
                if content := compared.content:
                    contents.append(content if content.endswith("\n") else content + "\n")
    return OutputComparison(differences=differences, content="".join(contents))


def _next_step(target: FastAPIConfig, dependencies: tuple[str, ...]) -> str:
    """Return the uv command that adds the generated package's runtime dependencies."""
    arguments = " ".join(f'"{dependency}"' for dependency in dependencies)
    return f"Add the runtime dependencies of {target.package} to your project:\n  uv add {arguments}"


def _refused(flags: list[str]) -> APIGenerationError:
    return APIGenerationError(tuple(_conflict(f"--generate-server cannot be used with {flag}") for flag in flags))


def _server_config(config: Any, namespace: Namespace, pyproject_path: Path | None) -> FastAPIConfig:
    """Map the --server-* settings onto the server configuration, leaving unset ones at their defaults.

    Documents named in operation references resolve against the pyproject.toml directory for settings read from it,
    and against the working directory for settings given on the command line.
    """
    from datamodel_code_generator._fastapi.config import FastAPIConfig, ResponseChoice  # noqa: PLC0415

    def base(field: str) -> Path:
        return Path.cwd() if pyproject_path is None or getattr(namespace, field) is not None else pyproject_path.parent

    values: dict[str, Any] = {
        name: value for name in _SERVER_SETTINGS if (value := getattr(config, f"server_{name}")) is not None
    }
    for name in _OPERATION_SETTINGS:
        if (entries := getattr(config, field := f"server_{name}")) is not None:
            root = base(field)
            values[name] = {_operation(key, root): value for key, value in entries.items()}
    if (responses := config.server_primary_responses) is not None:
        root = base("server_primary_responses")
        values["primary_responses"] = {
            _operation(key, root): ResponseChoice(status_code=choice.status_code, media_type=choice.media_type)
            for key, choice in responses.items()
        }
    return FastAPIConfig(
        output=config.server_output, package=config.server_package, model_package=config.server_model_package, **values
    )


def _operation(key: str, base: Path) -> OperationSelector:
    """Read an operation reference: a pointer into the root document, or a document and a pointer joined by #."""
    document, separator, pointer = key.partition("#")
    if not separator:
        return key
    return OperationRef(pointer=pointer, document=document_identity(document, base) if document else None)


def _conflict(message: str) -> Diagnostic:
    return Diagnostic(code="E_CONFIG_CONFLICT", severity="error", stage="config", message=message)


def _failure(error: Exception, *, encoding: str = "utf-8") -> None:
    """Preserve model CLI error hints and unexpected-error tracebacks."""
    message = str(error)
    if isinstance(error, InvalidClassNameError):
        message = f"{error} You have to set `--class-name` option"
    elif isinstance(error, UnicodeDecodeError):
        message = f"Unable to decode input using encoding {encoding!r}: {error}"
    elif not isinstance(error, (Error, OSError)):
        from datamodel_code_generator.remote_lock import RemoteLockError  # noqa: PLC0415

        if not isinstance(error, RemoteLockError):
            traceback.print_exception(error, file=sys.stderr)
            return
    print(f"Error: {message}", file=sys.stderr)  # noqa: T201
