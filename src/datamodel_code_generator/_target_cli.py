"""Publish or check the selected generation target with model CLI reporting."""

from __future__ import annotations

import os
import sys
import traceback
from functools import partial
from itertools import starmap
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Final, cast

from datamodel_code_generator import Error, InvalidClassNameError
from datamodel_code_generator._target_documents import shown

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Iterable, Iterator, Sequence

    from datamodel_code_generator.__main__ import OutputComparison
    from datamodel_code_generator._api_generation import PlannedTarget
    from datamodel_code_generator._api_types import GeneratedArtifact, GeneratedProject, NextSteps
    from datamodel_code_generator._publication import StagedFile
    from datamodel_code_generator._structured_output import CheckDifferencePayload, ImpliedPayload, TargetPayload
    from datamodel_code_generator._target_config import TargetConfig

_OK: Final = 0
_DIFF: Final = 1
_ERROR: Final = 2


def run_target(  # noqa: PLR0913, PLR0917
    args: Sequence[str],
    namespace: Namespace,
    config: Any,
    pyproject_path: Path | None,
    batch: list[tuple[str, PlannedTarget, str | None]] | None = None,
    lock: Any = None,
    job: str = "",
    implied: dict[str, Any] | None = None,
    announced: dict[str, Any] | None = None,
) -> int:
    """Generate or check the selected target from the finalized CLI config, reporting errors like model runs.

    A model setting the target needs but the config lacks is refused like a model option conflict, before the run. A
    batch `job` appends the planned target to the targets its `batch` has planned instead of publishing it, and its
    models record into the batch's remote `lock`; a job that checks its target appends it without a notice, so that
    later jobs are refused the server packages it plans as when they write them. A single run publishes its target as
    a batch of one, so a failed publication is reported as for the model runs that publish through the same journal.

    The config already holds the settings the target `implied`, which no option, key, or preset set; the JSON payloads
    name them under `target.implied`. After a run that succeeds, stderr shows how to set the `announced` ones: the
    command as run with their options appended, or the lines of the `pyproject.toml` table that held the settings.
    """
    from datamodel_code_generator._api_generation import model_requirement, python_problem  # noqa: PLC0415

    implied = implied or {}
    if config.preset is not None and (problem := python_problem(config, _kind(config))) is not None:
        print(f"Error: --target-python-version: {problem}", file=sys.stderr)  # noqa: T201
        return _ERROR
    if requirement := model_requirement(config.openapi_scopes, _selector(config)):
        print(f"Error: {requirement}", file=sys.stderr)  # noqa: T201
        return _ERROR
    try:
        return _run(args, namespace, config, pyproject_path, batch, lock, job, implied, announced or {})
    except Exception as error:  # noqa: BLE001
        from datamodel_code_generator._target_selection import keyed  # noqa: PLC0415

        _failure(keyed(error, config), encoding=config.encoding)
        return _ERROR


def _implied_option(name: str, value: object) -> tuple[str, Any]:
    """Return the key of an implied setting and its JSON value, as `pyproject.toml` spells them."""
    key = name.replace("_", "-")
    match value:
        case list():
            return key, [item.value for item in cast("list[Any]", value)]
        case bool():
            return key, value
    return key, cast("Any", value).value


def _print_notice(notice: str | None) -> None:
    if notice is not None:
        print(notice, file=sys.stderr)  # noqa: T201


def _implied_notice(  # noqa: PLR0913, PLR0917
    implied: dict[str, Any], args: Sequence[str], config: Any, namespace: Namespace, pyproject: Path | None, job: str
) -> str | None:
    """Return how to set the implied settings: the command with their options, or the lines of the table to add.

    A job adds them to its table, and a run whose target the command line selected adds them to its command. Any
    other run that read `pyproject.toml` adds them to the table it read, its profile's when it selected one. A run
    that implied nothing has no notice.
    """
    if not implied:
        return None
    import json  # noqa: PLC0415
    import shlex  # noqa: PLC0415

    options = dict(starmap(_implied_option, implied.items()))
    lead = f"{_selector(config)} set the options it needs that you left unset; to set them yourself"
    selected = any(getattr(namespace, name, None) is not None for name in ("generate_server", "generate_client"))
    if pyproject is not None and (job or not selected):
        named = ("jobs", job) if job else ("profiles", profile) if (profile := namespace.profile) else ()
        table = ".".join(("tool.datamodel-codegen", *(_toml_key(part) for part in named)))
        lines = (f"  {key} = {json.dumps(data)}" for key, data in options.items())
        where = os.path.relpath(pyproject, Path.cwd())
        return "\n".join((f"{lead}, add to [{table}] of {Path(where).as_posix()}:", *lines))
    flags = [
        token
        for key, data in options.items()
        for token in (f"--{key}", *(data if isinstance(data, list) else () if data is True else (data,)))
    ]
    stdin = "" if config.input or config.url else " (with the same input on stdin)"
    return f"{lead}, run{stdin}:\n  {shlex.join(('datamodel-codegen', *args, *flags))}"


def _toml_key(key: str) -> str:
    """Return a key of a TOML table header: bare when TOML allows it, or quoted."""
    import json  # noqa: PLC0415
    import re  # noqa: PLC0415

    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)


def _kind(config: Any) -> str:
    """Return the kind of the run's target, as its messages name it."""
    return "fastapi" if config.generate_client is None else "client"


def _selector(config: Any) -> str:
    """Return the option that selects the run's target."""
    return "--generate-server" if config.generate_client is None else "--generate-client"


def _run(  # noqa: PLR0913, PLR0917
    args: Sequence[str],
    namespace: Namespace,
    config: Any,
    pyproject_path: Path | None,
    batch: list[tuple[str, PlannedTarget, str | None]] | None,
    lock: Any,
    job: str,
    implied: dict[str, Any],
    announced: dict[str, Any],
) -> int:
    from datamodel_code_generator.__main__ import (  # noqa: PLC0415
        _publish_or_error,  # pyright: ignore[reportPrivateUsage]
        _single_job_plan,  # pyright: ignore[reportPrivateUsage]
        _stage_job_plans,  # pyright: ignore[reportPrivateUsage]
        _target_lockfile,  # pyright: ignore[reportPrivateUsage, reportUnknownVariableType]
        _target_settings,  # pyright: ignore[reportPrivateUsage, reportUnknownVariableType]
        _write_comparison_output,  # pyright: ignore[reportPrivateUsage]
    )
    from datamodel_code_generator._api_generation import (  # noqa: PLC0415
        implied_conflict,
        plan_target,
        prepare_target,
    )
    from datamodel_code_generator._target_selection import target_of  # noqa: PLC0415

    lockfile = _target_lockfile(config, pyproject_path)
    generator, target = target_of(config, partial(_base, config, namespace, pyproject_path))
    effective = _target_settings(config, args, lockfile)
    if batch is not None:
        effective.resolve_remote_lock(lock)
    if (source := config.url or config.input) is None:
        prepare_target("", effective, generator)
        source = sys.stdin.read()
    implied_conflict(config, implied, generator)
    notice = _implied_notice(announced, args, config, namespace, pyproject_path, job)
    if config.check:
        planned = plan_target(source, model_config=effective, config=target, generator=generator, job=job)
        if batch is not None:
            _join(batch, job, planned, None)
        project = planned.project
        comparison = _compare_target(project, config.output, target.output, config.encoding)
        _write_comparison_output(comparison, namespace.output_format, target=_target_payload(target, project, implied))
        _print_notice(notice)
        return _DIFF if comparison.differences else _OK
    if config.diff_against is not None:
        effective = effective.model_copy(update={"input_filename": "<input>"})
        baseline = plan_target(config.diff_against, model_config=effective, config=target, generator=generator)
        current = plan_target(
            source, model_config=effective, config=target, generator=generator, timestamp=baseline.timestamp
        )
        comparison = _compare_target(
            current.project, config.output, target.output, config.encoding, baseline=baseline.project
        )
        _write_comparison_output(
            comparison,
            namespace.output_format,
            kind="input-diff",
            target=_target_payload(target, current.project, implied),
        )
        _print_notice(notice)
        return _DIFF if comparison.differences else _OK
    json_output = namespace.output_format == "json"
    if batch is not None:
        timestamp = next((earlier.timestamp for _, earlier, _ in batch if earlier.timestamp is not None), None)
        planned = plan_target(
            source, model_config=effective, config=target, generator=generator, timestamp=timestamp, job=job
        )
        _join(batch, job, planned, _next_step(target, planned.project, notice))
        if json_output:
            print(_target_json(planned.project, config.output, config.encoding, target, implied))  # noqa: T201
        return _OK
    planned = plan_target(source, model_config=effective, config=target, generator=generator, publish=True)
    project = planned.project
    output = _target_json(project, config.output, config.encoding, target, implied) if json_output else None
    (staged,) = _stage_job_plans((_single_job_plan(config, pyproject_path),))
    cast("list[Any]", staged.targets).append((job, planned, _next_step(target, project, notice)))
    if (failure := _publish_or_error((staged,))) is not None:
        return int(failure)
    if output is not None:
        print(output)  # noqa: T201
    return _OK


def _join(
    batch: list[tuple[str, PlannedTarget, str | None]], job: str, planned: PlannedTarget, notice: str | None
) -> None:
    """Append a job's planned target to its batch, refusing a server package that shares an earlier job's services."""
    from datamodel_code_generator._api_generation import check_shared_services  # noqa: PLC0415

    check_shared_services(job, planned, ((earlier, target) for earlier, target, _ in batch))
    batch.append((job, planned, notice))


def publish_targets(files: Iterable[StagedFile], targets: Sequence[tuple[str, PlannedTarget, str | None]]) -> None:
    """Publish the staged files of a batch and the targets its jobs planned through one journal.

    Each target's dependency notice follows the publication on stderr, as after a single run. A target that a job
    only checked has no notice and is not published.
    """
    from datamodel_code_generator._api_generation import publish_planned  # noqa: PLC0415

    written = [(job, planned, notice) for job, planned, notice in targets if notice is not None]
    publish_planned(files, [(job, planned) for job, planned, _ in written])
    for _, _, notice in written:
        print(notice, file=sys.stderr)  # noqa: T201


def _shown(path: Path) -> str:
    """Return a path relative to the working directory when it lies inside it."""
    return shown(path, Path.cwd()).as_posix()


def _payload_base(project: GeneratedProject, models: Path) -> Path:
    """Return the directory payload paths are relative to, as in the model payload: the model output or its parent."""
    rendered = [artifact.path for artifact in project.artifacts if artifact.kind == "model"]
    return models.parent if models in rendered or (not rendered and models.suffix) else models


def _target_payload(target: TargetConfig, project: GeneratedProject, implied: dict[str, Any]) -> TargetPayload:
    """Name the generated package and the requirements it needs at run time, as the uv add notice does.

    The settings the target implied are keyed as `pyproject.toml` keys them, or left out when it implied none.
    """
    from datamodel_code_generator._structured_output import TargetPayload  # noqa: PLC0415

    return TargetPayload(
        package=target.package, dependencies=list(project.dependencies), implied=_implied_payload(implied)
    )


def _implied_payload(implied: dict[str, Any]) -> ImpliedPayload | None:
    """Return the settings the target implied as the payload records them, or None when it implied none."""
    if not implied:
        return None
    from datamodel_code_generator._structured_output import ImpliedPayload  # noqa: PLC0415

    return ImpliedPayload(**{name: _implied_option(name, value)[1] for name, value in implied.items()})


def _target_json(
    project: GeneratedProject, models: Path, encoding: str, target: TargetConfig, implied: dict[str, Any]
) -> str:
    """Emit rendered model and target text, read like their published files, with the existing generation payload.

    The files include the initializer of a `services` package a server takes over, whose docstring the run rewrites.
    As in the model payload, a path is relative to the model output directory; a file outside it has an absolute path.
    The payload's target names the package, its runtime dependencies, and the next steps with paths of the same base.
    """
    from io import BytesIO, TextIOWrapper  # noqa: PLC0415

    from datamodel_code_generator._structured_output import (  # noqa: PLC0415
        EntrypointPayload,
        GeneratedFilePayload,
        GeneratedTargetPayload,
        ImplementationPayload,
        generation_output_json,
    )

    taken = _taken_over(project)
    artifacts = [
        (
            artifact.path,
            content,
            encoding if artifact.kind == "model" or artifact.path.suffix in {".py", ".pyi"} else "utf-8",
        )
        for artifact in project.artifacts
        if (artifact.kind in {"model", "target"} or artifact is taken) and (content := artifact.content) is not None
    ]
    base = _payload_base(project, models)
    files = [
        GeneratedFilePayload(
            path=shown(path, base).as_posix(),
            content=TextIOWrapper(BytesIO(content), encoding=codec).read(),
        )
        for path, content, codec in sorted(artifacts, key=lambda artifact: artifact[0].parts)
    ]
    steps = project.next_steps
    entrypoint = implementation = None
    if (app := steps.app) is not None:
        entrypoint = EntrypointPayload(app=app, path=shown(cast("Path", steps.entrypoint), base).as_posix())
        implementation = [
            ImplementationPayload(
                module=item.module, name=item.name, path=shown(item.path, base).as_posix(), created=item.created
            )
            for item in steps.implementations
        ]
    payload = GeneratedTargetPayload(
        package=target.package,
        dependencies=list(project.dependencies),
        readme=shown(steps.readme, base).as_posix(),
        entrypoint=entrypoint,
        implementation=implementation,
        usage=steps.usage,
        implied=_implied_payload(implied),
    )
    return generation_output_json(files, output=models.as_posix(), target=payload)


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


def _compare_target(
    project: GeneratedProject,
    models: Path,
    target: Path,
    encoding: str,
    *,
    baseline: GeneratedProject | None = None,
) -> OutputComparison:
    """Compare rendered text and Python output roots through the model comparison path.

    Labels stay relative to the working directory; each difference names its file like the generation payload. The
    initializer of a `services` package a server takes over is compared at its path, as the docstring the run rewrites.
    With a `baseline` rendered from another input, the comparison is an input diff against its files instead of the
    output.
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
        baseline_root = None if baseline is None else staging / "baseline"
        for artifact in () if baseline is None else _generated(baseline):
            (staged := _existing(artifact.path, baseline_root)).parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(artifact.content)
        rendered = [*project.artifacts, *(() if baseline is None else baseline.artifacts)]
        comparisons: list[tuple[Path, Path, bool, str]] = []
        for kind, output, is_directory in (("model", models, base == models), ("target", target, True)):
            if not is_directory and not any(artifact.kind == kind for artifact in rendered):
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
            _stage_implementations(project, output if is_directory and baseline is None else None, staged_root)
            comparisons.append((
                staged_root if is_directory else staged_root / output.name,
                output,
                is_directory,
                encoding,
            ))
            comparisons.extend((staged, path, False, codec) for staged, path, codec in non_python)
        comparisons.extend(() if baseline is not None else _staged_take_over(project, staging / "services", encoding))
        for generated, actual, directory_output, codec in comparisons:
            try:
                compared = _compare_generated_outputs(
                    generated,
                    _existing(actual, baseline_root),
                    codec,
                    OutputComparisonOptions(
                        is_directory_output=directory_output,
                        input_diff=baseline is not None,
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


def _staged_take_over(project: GeneratedProject, staging: Path, encoding: str) -> list[tuple[Path, Path, bool, str]]:
    """Stage the initializer of a `services` package a server takes over, to compare it with the file at its path."""
    if (taken := _taken_over(project)) is None:
        return []
    staging.mkdir()
    (staged := staging / taken.path.name).write_bytes(taken.content)
    return [(staged, taken.path, False, encoding)]


def _taken_over(project: GeneratedProject) -> GeneratedArtifact | None:
    """Return the initializer of the `services` package a server takes over, which the run writes although it exists.

    Every other implementation the run writes is a module that is missing.
    """
    if project.next_steps.taken_over is None:
        return None
    return next(
        artifact
        for artifact in project.artifacts
        if artifact.kind == "implementation" and artifact.action == "write" and artifact.path.name == "__init__.py"
    )


def _generated(project: GeneratedProject) -> Iterator[GeneratedArtifact]:
    """Yield the artifacts of a project that generation owns, leaving out the user modules it writes only once."""
    return (artifact for artifact in project.artifacts if artifact.kind != "implementation")


def _stage_implementations(project: GeneratedProject, output: Path | None, staged_root: Path) -> None:
    """Stage the `services` package inside a compared output directory as it is, so it never lists its files as extra.

    The user's package beside a server package lies in the models directory that holds the server package, with the
    modules generation writes and any others. Without an output directory to compare with the files at their paths,
    nothing is staged.
    """
    if output is None:
        return
    from datamodel_code_generator._leftover_files import python_files  # noqa: PLC0415

    root = output.resolve()
    services = {artifact.path.parent.resolve() for artifact in project.artifacts if artifact.kind == "implementation"}
    for directory in services:
        if not directory.is_relative_to(root):
            continue
        for path in python_files(directory):
            (staged := staged_root / directory.relative_to(root) / path).parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes((directory / path).read_bytes())


def _existing(path: Path, baseline_root: Path | None) -> Path:
    """Return the file an output path is compared with: itself, or the baseline file staged for it.

    A baseline root mirrors the filesystem, so every output path, inside the working directory or not, has its place.
    """
    return path if baseline_root is None else baseline_root.joinpath(*Path.cwd().joinpath(path).parts[1:])


def _next_step(target: TargetConfig, project: GeneratedProject, implied: str | None) -> str:
    """Return the uv command that adds the generated package's runtime dependencies, then what to do next.

    The notice of the settings the target implied, if any, comes first. A server that takes a `services` package over
    says so, since the modules of the package still import the server package it took it from. A server names the
    modules to implement, marking those the run creates, and how to run its entrypoint; a client shows a call of its
    first operation. Both name their README.
    """
    arguments = " ".join(f'"{dependency}"' for dependency in project.dependencies)
    lines = [f"Add the runtime dependencies of {target.package} to your project:", f"  uv add {arguments}"]
    if (taken := _taken_over(project)) is not None:
        old, new = project.next_steps.taken_over, target.package
        lines.append(
            f"{_shown(taken.path)} now names {new} instead of {old}: "
            f"update the imports of the services modules from {old} to {new}."
        )
    return "\n".join((*((implied,) if implied else ()), *lines, *_steps(project.next_steps)))


def _steps(steps: NextSteps) -> list[str]:
    readme = _shown(steps.readme)
    if steps.usage is not None:
        return [
            "Next steps: call the API with the client:",
            *(f"  {line}".rstrip() for line in steps.usage.splitlines()),
            f"See {readme}.",
        ]
    created = ", ".join(_shown(item.path) for item in steps.implementations if item.created)
    kept = ", ".join(_shown(item.path) for item in steps.implementations if not item.created)
    implement = (
        f"implement the stubs in {created}{f' (your services: {kept})' if kept else ''}, then " if created else ""
    )
    yours = f" with your services in {kept}" if kept and not created else ""
    app, run = _run_commands(cast("str", steps.app), _shown(cast("Path", steps.entrypoint)))
    return [
        f"Next steps: {implement}run the server{yours}:",
        f"  uv add uvicorn && uv run uvicorn {app} --reload",
        f'or: uv add "fastapi[standard]" && uv run {run}',
        f"See {readme}.",
    ]


def _run_commands(app: str, entrypoint: str) -> tuple[str, str]:
    """Return the uvicorn arguments and the `fastapi dev` command that import a server's application.

    The import root is the package directory without the package's dotted parts, relative to the working directory;
    uvicorn adds it with `--app-dir`. `fastapi dev` imports a top-level package from the path of `main.py`, and a
    nested one, whose parent may lack an initializer, by its import string from the import root. Paths are quoted for
    the shell where they need it.
    """
    from shlex import quote  # noqa: PLC0415

    parts = PurePosixPath(entrypoint).parent.parts
    depth = app.partition(":")[0].count(".")
    root = quote(str(PurePosixPath(*parts[: max(len(parts) - depth, 0)], *[".."] * (depth - len(parts)))))
    app_dir, directory = (f"--app-dir {root} ", f"--directory {root} ") if root != "." else ("", "")
    run = f"fastapi dev {quote(entrypoint)}" if depth == 1 else f"{directory}fastapi dev --entrypoint {app}"
    return f"{app_dir}{app}", run


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
