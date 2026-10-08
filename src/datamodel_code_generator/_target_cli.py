"""Run the generation target a command line selects: publish or check it, and report its diagnostics."""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NoReturn

from typing_extensions import TypeIs

from datamodel_code_generator import Error, InvalidClassNameError
from datamodel_code_generator._api_manifest import document_identity, shown
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, OperationRef

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Iterable, Sequence

    from datamodel_code_generator.__main__ import OutputComparison
    from datamodel_code_generator._api_types import GeneratedProject, OperationSelector
    from datamodel_code_generator._fastapi.config import FastAPIConfig
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue
    from datamodel_code_generator._structured_output import CheckDifferencePayload

_OK: Final = 0
_DIFF: Final = 1
_ERROR: Final = 2
_JOBS: Final = (("job", "--job"), ("all_jobs", "--all-jobs"))
_CONFLICTS: Final = (("watch", "--watch"), ("diff_against", "--diff-against"), ("input_model", "--input-model"))
_TARGET: Final = "fastapi"
_SERVER_SETTINGS: Final = ("layout", "handler_mode", "include_request", "body_mode", "router_names")
_OPERATION_SETTINGS: Final = ("handler_modes", "body_modes", "operation_names", "parameter_names")
_REPORT: Final = frozenset({"schema_version", "target", "diagnostics"})
_FIELDS: Final = frozenset({
    "code",
    "severity",
    "stage",
    "message",
    "source_uri",
    "source_pointer",
    "operation",
    "option_path",
    "artifact_path",
    "target_id",
})


def run_target(args: Sequence[str], namespace: Namespace, config: Any, pyproject_path: Path | None) -> int:
    """Generate or check the selected target from the finalized CLI config, reporting every diagnostic.

    A model setting the target needs but the config lacks is refused like a model option conflict, before the run.
    """
    from datamodel_code_generator._api_generation import model_requirement  # noqa: PLC0415
    from datamodel_code_generator._fastapi.target import FastAPITarget  # noqa: PLC0415

    if config is not None and (requirement := model_requirement(config.openapi_scopes, FastAPITarget.selector)):
        print(f"Error: {requirement}", file=sys.stderr)  # noqa: T201
        return _ERROR
    report = _Report(vars(namespace).get("diagnostics_json"))
    try:
        code = (
            _jobs(namespace, pyproject_path, report)
            if config is None
            else _run(args, namespace, config, pyproject_path, report)
        )
    except Exception as error:  # noqa: BLE001
        report.failure(error, encoding="utf-8" if config is None else config.encoding)
        code = _ERROR
    return code if report.write() else _ERROR


def _run(args: Sequence[str], namespace: Namespace, config: Any, pyproject_path: Path | None, report: _Report) -> int:
    from datamodel_code_generator.__main__ import (  # noqa: PLC0415
        _target_lockfile,  # pyright: ignore[reportPrivateUsage, reportUnknownVariableType]
        _target_settings,  # pyright: ignore[reportPrivateUsage, reportUnknownVariableType]
        _write_comparison_output,  # pyright: ignore[reportPrivateUsage]
    )
    from datamodel_code_generator._api_generation import generate_target, prepare_target, render_target  # noqa: PLC0415
    from datamodel_code_generator._fastapi.target import FastAPITarget  # noqa: PLC0415

    lockfile = _target_lockfile(config, pyproject_path)
    report.guard(
        (pyproject_path, config.input, config.output, config.emit_model_metadata, lockfile),
        (config.server_output, config.output),
    )
    if flags := _flags(namespace, config, _CONFLICTS, json_supported=config.check):
        raise _refused(flags)
    if namespace.output_format == "json" and report.destination == "-":
        raise APIGenerationError((_conflict("--output-format json cannot be used with --diagnostics-json -"),))
    if (form := vars(namespace).get("dependency_format")) is not None and report.destination == "-":
        raise APIGenerationError((_conflict("--dependency-format cannot be used with --diagnostics-json -"),))
    target = _server_config(config, namespace, pyproject_path)
    effective = _target_settings(config, args, lockfile)
    generator = FastAPITarget()
    if (source := config.url or config.input) is None:
        prepare_target("", effective, generator)
        source = sys.stdin.read()
    if config.check:
        project = render_target(source, model_config=effective, config=target, generator=generator, warn_edits=False)
        comparison = _compare_target(project, config.output, target.output, config.encoding)
        _write_comparison_output(comparison, namespace.output_format)
        return _DIFF if comparison.differences else _OK
    generated = generate_target(source, model_config=effective, config=target, generator=generator)
    if report.destination != "-":
        print(_next_step(target, generated.dependencies, form))  # noqa: T201
    return _OK


def _shown(path: Path) -> str:
    """Return a path relative to the working directory when it lies inside it."""
    return shown(path, Path.cwd()).as_posix()


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


def _next_step(target: FastAPIConfig, dependencies: tuple[str, ...], form: str | None) -> str:
    """Return what adds the runtime dependencies of a generated package to a project: a uv command or requirements."""
    if form == "requirements":
        return "\n".join(dependencies)
    arguments = " ".join(f'"{dependency}"' for dependency in dependencies)
    return f"Add the runtime dependencies of {target.package} to your project:\n  uv add {arguments}"


def _jobs(namespace: Namespace, pyproject_path: Path | None, report: _Report) -> NoReturn:
    report.guard((pyproject_path,), ())
    raise _refused(_flags(namespace, namespace, _JOBS))


def _flags(
    namespace: Namespace, source: object, options: tuple[tuple[str, str], ...], *, json_supported: bool = False
) -> list[str]:
    flags = [flag for name, flag in options if getattr(source, name)]
    return [*flags, "--output-format json"] if namespace.output_format == "json" and not json_supported else flags


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


def _is_report(path: Path) -> bool:
    try:
        document: object = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return False
    return (
        _is_mapping(document)
        and frozenset(document) == _REPORT
        and document["schema_version"] == 1
        and document["target"] == _TARGET
        and _is_list(entries := document["diagnostics"])
        and all(_is_mapping(entry) and frozenset(entry) == _FIELDS for entry in entries)
    )


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_list(value: object) -> TypeIs[list[object]]:
    return isinstance(value, list)


def _conflict(message: str) -> Diagnostic:
    return Diagnostic(code="E_CONFIG_CONFLICT", severity="error", stage="config", message=message)


def _unwritable(reason: str) -> Diagnostic:
    message = f"--diagnostics-json cannot be written: {reason}"
    return Diagnostic(code="E_CONFIG_VALUE", severity="error", stage="config", message=message)


class _Report:
    """Collect the diagnostics of a failed run and write them as JSON when asked to."""

    def __init__(self, destination: str | None) -> None:
        self.destination = destination
        self.diagnostics: list[Diagnostic] = []

    def guard(self, files: Iterable[Path | None], roots: Iterable[Path | None]) -> None:
        """Refuse a diagnostics file the generation reads or writes, before anything could overwrite it."""
        if (destination := self.destination) is None or destination == "-":
            return
        written = Path(destination).resolve()
        if any(path is not None and written == path.resolve() for path in files) or any(
            root is not None and written.is_relative_to(root.resolve()) for root in roots
        ):
            self.destination = None
            raise APIGenerationError((_conflict("--diagnostics-json names a file the generation reads or writes"),))
        if written.is_dir() or not written.parent.is_dir():
            self.destination = None
            raise APIGenerationError((_unwritable("it is not a file in an existing directory"),))
        if written.is_file() and not _is_report(written):
            self.destination = None
            raise APIGenerationError((_conflict("--diagnostics-json names an existing file that is not a report"),))

    def failure(self, error: Exception, *, encoding: str = "utf-8") -> None:
        """Print ordinary target errors and preserve the model CLI's hints and unexpected-error traceback."""
        if isinstance(error, APIGenerationError):
            self.diagnostics.extend(error.diagnostics)
        else:
            self.diagnostics.append(
                Diagnostic(
                    code="E_GENERATION_FAILURE",
                    severity="error",
                    stage="target",
                    message=f"{type(error).__name__}: {error}",
                )
            )
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

    def write(self) -> bool:
        if (destination := self.destination) is None:
            return True
        document: JSONValue = {
            "schema_version": 1,
            "target": _TARGET,
            "diagnostics": [_json(diagnostic) for diagnostic in self.diagnostics],
        }
        text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        if destination == "-":
            sys.stdout.write(text)
            return True
        try:
            Path(destination).write_text(text, encoding="utf-8")
        except OSError as error:
            self.failure(APIGenerationError((_unwritable(str(error.strerror)),)))
            return False
        return True


def _json(diagnostic: Diagnostic) -> JSONValue:
    operation = diagnostic.operation
    return {
        "code": diagnostic.code,
        "severity": diagnostic.severity,
        "stage": diagnostic.stage,
        "message": diagnostic.message,
        "source_uri": diagnostic.source_uri,
        "source_pointer": diagnostic.source_pointer,
        "operation": None if operation is None else {"pointer": operation.pointer, "document": operation.document},
        "option_path": diagnostic.option_path,
        "artifact_path": diagnostic.artifact_path,
        "target_id": diagnostic.target_id,
    }
