"""Publish or check the selected generation target with model CLI reporting."""

from __future__ import annotations

import re
import sys
import traceback
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast

from datamodel_code_generator import Error, InvalidClassNameError
from datamodel_code_generator._api_manifest import document_identity, shown
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, OperationRef

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Iterable, Sequence

    from datamodel_code_generator.__main__ import OutputComparison
    from datamodel_code_generator._api_generation import PlannedTarget
    from datamodel_code_generator._api_types import GeneratedProject, OperationSelector
    from datamodel_code_generator._client.config import ClientGenerationConfig
    from datamodel_code_generator._client.target import ClientTarget
    from datamodel_code_generator._fastapi.config import FastAPIConfig
    from datamodel_code_generator._fastapi.target import FastAPITarget
    from datamodel_code_generator._publication import StagedFile
    from datamodel_code_generator._structured_output import CheckDifferencePayload
    from datamodel_code_generator._target_config import TargetConfig

_OK: Final = 0
_DIFF: Final = 1
_ERROR: Final = 2
_CONFLICTS: Final = (("watch", "--watch"), ("diff_against", "--diff-against"), ("input_model", "--input-model"))
_SERVER_SETTINGS: Final = ("layout", "handler_mode", "include_request", "body_mode", "router_names")
_OPERATION_SETTINGS: Final = ("handler_modes", "body_modes", "operation_names", "parameter_names")
_INDEXED: Final = re.compile(r"(operations|resource_names)\[(\d+)\](?:\.(parameter_names|body_field_names)\[(\d+)\])?")
_CLIENT_SETTINGS: Final = ("signature_style", "body_arguments", "default_base_url", "server_base_url")


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
    models record into the batch's remote `lock`. A single run publishes its target as a batch of one, so a failed
    publication is reported as for the model runs that publish through the same journal.
    """
    from datamodel_code_generator._api_generation import model_requirement  # noqa: PLC0415

    if requirement := model_requirement(config.openapi_scopes, _selector(config)):
        print(f"Error: {requirement}", file=sys.stderr)  # noqa: T201
        return _ERROR
    try:
        return _run(args, namespace, config, pyproject_path, batch, lock, job)
    except Exception as error:  # noqa: BLE001
        _failure(_keyed(error, config), encoding=config.encoding)
        return _ERROR


def _selector(config: Any) -> str:
    """Return the option that selects the run's target."""
    return "--generate-server" if config.generate_client is None else "--generate-client"


def _keyed(error: Exception, config: Any) -> Exception:
    """Name the operation and resource client settings in an error by the keys they were given under.

    A parameter name is named by its location and name, and a body field name by its media type and property.
    """
    if not isinstance(error, APIGenerationError) or config.generate_client is None:
        return error
    keys = {
        "operations": list(config.client_operations or ()),
        "resource_names": list(config.client_resource_names or ()),
    }

    def keyed(item: Diagnostic) -> Diagnostic:
        if (path := item.option_path) is None or (found := _INDEXED.match(path)) is None:
            return item
        named = f"{found[1]}[{(key := keys[found[1]][int(found[2])])!r}]"
        if (member := found[3]) is not None:
            names = config.client_operations[key][member]
            spelled = (
                [f"[{name!r}]" for name in names]
                if member == "parameter_names"
                else [f"[{media!r}][{name!r}]" for media, fields in names.items() for name in fields]
            )
            named += f".{member}{spelled[int(found[4])]}"
        return replace(item, option_path=f"{named}{path[found.end() :]}")

    if (diagnostics := tuple(map(keyed, error.diagnostics))) == error.diagnostics:
        return error
    from datamodel_code_generator._client.config import OPTION_PREFIX  # noqa: PLC0415

    return APIGenerationError(diagnostics, option_prefix=OPTION_PREFIX)


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
        _publish_or_error,  # pyright: ignore[reportPrivateUsage]
        _single_job_plan,  # pyright: ignore[reportPrivateUsage]
        _stage_job_plans,  # pyright: ignore[reportPrivateUsage]
        _target_lockfile,  # pyright: ignore[reportPrivateUsage, reportUnknownVariableType]
        _target_settings,  # pyright: ignore[reportPrivateUsage, reportUnknownVariableType]
        _write_comparison_output,  # pyright: ignore[reportPrivateUsage]
    )
    from datamodel_code_generator._api_generation import plan_target, prepare_target, render_target  # noqa: PLC0415

    lockfile = _target_lockfile(config, pyproject_path)
    if flags := [flag for name, flag in _CONFLICTS if getattr(config, name)]:
        raise _refused(flags, _selector(config))
    generator, target = (_server if config.generate_client is None else _client)(config, namespace, pyproject_path)
    effective = _target_settings(config, args, lockfile)
    if batch is not None:
        effective.resolve_remote_lock(lock)
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
            print(_target_json(planned.project, config.output, config.encoding))  # noqa: T201
        batch.append((job, planned, _next_step(target, planned.project.dependencies)))
        return _OK
    planned = plan_target(source, model_config=effective, config=target, generator=generator, publish=True)
    project = planned.project
    output = _target_json(project, config.output, config.encoding) if json_output else None
    (staged,) = _stage_job_plans((_single_job_plan(config, pyproject_path),))
    cast("list[Any]", staged.targets).append((job, planned, _next_step(target, project.dependencies)))
    if (failure := _publish_or_error((staged,))) is not None:
        return int(failure)
    if output is not None:
        print(output)  # noqa: T201
    return _OK


def publish_targets(files: Iterable[StagedFile], targets: Sequence[tuple[str, PlannedTarget, str]]) -> None:
    """Publish the staged files of a batch and the targets its jobs planned through one journal.

    Each target's dependency notice follows the publication on stderr, as after a single run.
    """
    from datamodel_code_generator._api_generation import publish_planned  # noqa: PLC0415

    publish_planned(files, [(job, planned) for job, planned, _ in targets])
    for _, _, notice in targets:
        print(notice, file=sys.stderr)  # noqa: T201


def _shown(path: Path) -> str:
    """Return a path relative to the working directory when it lies inside it."""
    return shown(path, Path.cwd()).as_posix()


def _payload_base(project: GeneratedProject, models: Path) -> Path:
    """Return the directory payload paths are relative to, as in the model payload: the model output or its parent."""
    rendered = [artifact.path for artifact in project.artifacts if artifact.kind == "model"]
    return models.parent if models in rendered or (not rendered and models.suffix) else models


def _target_json(project: GeneratedProject, models: Path, encoding: str) -> str:
    """Emit rendered model and target text, read like their published files, with the existing generation payload.

    As in the model payload, a path is relative to the model output directory; a file outside it has an absolute path.
    """
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
    base = _payload_base(project, models)
    files = [
        GeneratedFilePayload(
            path=shown(path, base).as_posix(),
            content=TextIOWrapper(BytesIO(content), encoding=codec).read(),
        )
        for path, content, codec in sorted(artifacts, key=lambda artifact: artifact[0].parts)
    ]
    return generation_output_json(files, output=models.as_posix())


def _nested(models: Path, target: Path) -> dict[str, tuple[Path, Path]]:
    """Return the output root that lies inside the other one, by artifact kind, with its path inside that root.

    A comparison takes that root as part of the other one, so each file is reported once and neither root lists
    the files of the other as extra.
    """
    model_root, target_root = models.resolve(), target.resolve()
    if model_root.is_relative_to(target_root):
        return {"model": (models, model_root.relative_to(target_root))}
    if target_root.is_relative_to(model_root):
        return {"target": (target, target_root.relative_to(model_root))}
    return {}


def _compare_target(project: GeneratedProject, models: Path, target: Path, encoding: str) -> OutputComparison:
    """Compare rendered text and Python output roots through the model comparison path.

    Labels stay relative to the working directory; each difference names its file like the generation payload.
    """
    from tempfile import TemporaryDirectory  # noqa: PLC0415

    from datamodel_code_generator.__main__ import (  # noqa: PLC0415
        OutputComparison,
        OutputComparisonOptions,
        _compare_generated_outputs,  # pyright: ignore[reportPrivateUsage]
    )

    nested = _nested(models, target)
    differences: list[CheckDifferencePayload] = []
    contents: list[str] = []
    base, cwd = _payload_base(project, models), Path.cwd()
    with TemporaryDirectory(prefix="datamodel-codegen-check-") as directory:
        staging = Path(directory)
        for kind, output, is_directory in (("model", models, base == models), ("target", target, True)):
            if not is_directory and not any(artifact.kind == kind for artifact in project.artifacts):
                continue
            if kind in nested:
                continue
            staged_root = staging / kind
            staged_root.mkdir()
            non_python: list[tuple[Path, Path, str]] = []
            for artifact in project.artifacts:
                path = artifact.path
                if artifact.kind == kind:
                    staged = staged_root / (path.relative_to(output) if is_directory else path.name)
                elif (inner := nested.get(artifact.kind)) is not None:
                    staged = staged_root / inner[1] / path.relative_to(inner[0])
                else:
                    continue
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(artifact.content)
                if is_directory and path.suffix != ".py":
                    non_python.append((staged, path, encoding if artifact.kind == "model" else "utf-8"))
            comparisons = [(staged_root if is_directory else staged_root / output.name, output, is_directory, encoding)]
            comparisons.extend((staged, path, False, codec) for staged, path, codec in non_python)
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
                differences.extend(
                    difference.model_copy(update={"path": shown(cwd / difference.path, base).as_posix()})
                    for difference in compared.differences
                )
                if content := compared.content:
                    contents.append(content if content.endswith("\n") else content + "\n")
    return OutputComparison(differences=differences, content="".join(contents))


def _next_step(target: TargetConfig, dependencies: tuple[str, ...]) -> str:
    """Return the uv command that adds the generated package's runtime dependencies."""
    arguments = " ".join(f'"{dependency}"' for dependency in dependencies)
    return f"Add the runtime dependencies of {target.package} to your project:\n  uv add {arguments}"


def _refused(flags: list[str], selector: str) -> APIGenerationError:
    return APIGenerationError(tuple(_conflict(f"{selector} cannot be used with {flag}") for flag in flags))


def _base(config: Any, namespace: Namespace, pyproject_path: Path | None, field: str) -> Path:
    """Return the directory that the documents a setting names resolve against.

    A setting read from a JSON file resolves against that file's directory, and against a link's directory when
    the file is given through one. Inline JSON and tables resolve against the pyproject.toml directory for its keys,
    and against the working directory for options.
    """
    if (source := config._json_sources.get(field)) is not None:  # noqa: SLF001
        from datamodel_code_generator.json_config import _json_file  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]

        if (file := _json_file(source)) is not None:
            return (Path.cwd() / file).parent
    return Path.cwd() if pyproject_path is None or getattr(namespace, field) is not None else pyproject_path.parent


def _server(config: Any, namespace: Namespace, pyproject_path: Path | None) -> tuple[FastAPITarget, FastAPIConfig]:
    """Map the --server-* settings onto the server configuration, leaving unset ones at their defaults.

    Documents named in operation references resolve against the JSON file that holds the setting, else against the
    pyproject.toml directory for settings read from it and the working directory for settings given as options.
    """
    from datamodel_code_generator._fastapi.config import FastAPIConfig, ResponseChoice  # noqa: PLC0415
    from datamodel_code_generator._fastapi.target import FastAPITarget  # noqa: PLC0415

    values: dict[str, Any] = {
        name: value for name in _SERVER_SETTINGS if (value := getattr(config, f"server_{name}")) is not None
    }
    for name in _OPERATION_SETTINGS:
        if (entries := getattr(config, field := f"server_{name}")) is not None:
            root = _base(config, namespace, pyproject_path, field)
            values[name] = {_operation(key, root): value for key, value in entries.items()}
    if (responses := config.server_primary_responses) is not None:
        root = _base(config, namespace, pyproject_path, "server_primary_responses")
        values["primary_responses"] = {
            _operation(key, root): ResponseChoice(status_code=choice.status_code, media_type=choice.media_type)
            for key, choice in responses.items()
        }
    return FastAPITarget(), FastAPIConfig(
        output=config.server_output, package=config.server_package, model_package=config.server_model_package, **values
    )


def _client(
    config: Any, namespace: Namespace, pyproject_path: Path | None
) -> tuple[ClientTarget, ClientGenerationConfig]:
    """Map the --client-* settings onto the client configuration, leaving unset ones at their defaults.

    Operation references and the documents helpers name resolve like the operation references of the server settings.
    """
    from datamodel_code_generator._client.config import (  # noqa: PLC0415
        ClientGenerationConfig,
        ResourceName,
        operation_configs,
    )
    from datamodel_code_generator._client.target import ClientTarget  # noqa: PLC0415

    values: dict[str, Any] = {
        name: value for name in _CLIENT_SETTINGS if (value := getattr(config, f"client_{name}")) is not None
    }
    if (names := config.client_resource_names) is not None:
        values["resource_names"] = tuple(ResourceName(tag=tag, namespace=name) for tag, name in names.items())
    if (entries := config.client_operations) is not None:
        root = _base(config, namespace, pyproject_path, "client_operations")
        values["operations"] = operation_configs(entries, partial(_operation, base=root))
    return ClientTarget(_base(config, namespace, pyproject_path, "client_protocols")), ClientGenerationConfig(
        output=config.client_output,
        package=config.client_package,
        model_package=config.client_model_package,
        transport=config.generate_client,
        protocols=config.client_protocols,
        **values,
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
            print("".join(traceback.format_exception(error)), file=sys.stderr)  # noqa: T201
            return
    print(f"Error: {message}", file=sys.stderr)  # noqa: T201
