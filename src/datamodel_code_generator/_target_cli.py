"""Run the generation target a command line selects: publish or check it, and report its diagnostics."""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, attached_diagnostic

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Iterable, Sequence

    from datamodel_code_generator._fastapi.config import FastAPIConfig
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue

_OK: Final = 0
_DIFF: Final = 1
_ERROR: Final = 2
_JOBS: Final = (("job", "--job"), ("all_jobs", "--all-jobs"))
_CONFLICTS: Final = (("watch", "--watch"), ("diff_against", "--diff-against"), ("input_model", "--input-model"))
_TARGET: Final = "fastapi"


def run_target(args: Sequence[str], namespace: Namespace, config: Any, pyproject_path: Path | None) -> int:
    """Generate or check the selected target from the finalized CLI config, reporting every diagnostic."""
    report = _Report(vars(namespace).get("diagnostics_json"))
    try:
        code = _run(args, namespace, config, pyproject_path, report)
    except APIGenerationError as error:
        report.extend(error.diagnostics)
        code = _ERROR
    except Exception as error:  # noqa: BLE001
        report.failure(error)
        code = _ERROR
    report.write()
    return code


def _run(args: Sequence[str], namespace: Namespace, config: Any, pyproject_path: Path | None, report: _Report) -> int:
    from datamodel_code_generator.__main__ import (  # noqa: PLC0415
        _target_settings,  # pyright: ignore[reportPrivateUsage, reportUnknownVariableType]
    )
    from datamodel_code_generator._api_generation import generate_target, prepare_target, render_target  # noqa: PLC0415
    from datamodel_code_generator._fastapi.target import FastAPITarget  # noqa: PLC0415

    if conflicts := _conflicts(namespace, config):
        raise APIGenerationError(
            tuple(_conflict(f"--generate-server cannot be used with {flag}") for flag in conflicts)
        )
    target = _target_config(namespace.target_config, vars(namespace).get("target_output"))
    effective, lockfile = _target_settings(config, args, pyproject_path)
    if (destination := report.destination) not in {None, "-"} and _collides(
        Path(str(destination)), config, target, namespace.target_config, lockfile
    ):
        report.destination = None
        raise APIGenerationError((_conflict("--diagnostics-json names a file the generation reads or writes"),))
    generator = FastAPITarget()
    if (source := config.url or config.input) is None:
        prepare_target("", effective, generator)
        source = sys.stdin.read()
    if config.check:
        project = render_target(source, model_config=effective, config=target, generator=generator)
        report.extend(project.diagnostics)
        changes = [artifact for artifact in project.artifacts if artifact.action != "unchanged"]
        cwd = Path.cwd()
        for artifact in changes:
            shown = artifact.path.relative_to(cwd) if artifact.path.is_relative_to(cwd) else artifact.path
            print(f"{artifact.action} {shown.as_posix()}", file=sys.stderr)  # noqa: T201
        return _DIFF if changes else _OK
    report.extend(generate_target(source, model_config=effective, config=target, generator=generator).diagnostics)
    return _OK


def _conflicts(namespace: Namespace, config: Any) -> list[str]:
    selected = vars(namespace)
    flags = [flag for name, flag in _JOBS if selected[name]]
    if config is not None:
        flags.extend(flag for name, flag in _CONFLICTS if getattr(config, name))
    if namespace.output_format == "json":
        flags.append("--output-format json")
    return flags


def _target_config(path: Path, output: Path | None) -> FastAPIConfig:
    from datamodel_code_generator._fastapi.config import FastAPIConfig  # noqa: PLC0415
    from datamodel_code_generator._target_config import load_target_config  # noqa: PLC0415

    try:
        return load_target_config(path, FastAPIConfig, output=output)
    except OSError as error:
        message = f"The target file cannot be read: {error.strerror}"
        raise APIGenerationError((
            Diagnostic(code="E_CONFIG_VALUE", severity="error", stage="config", message=message),
        )) from None


def _collides(destination: Path, config: Any, target: FastAPIConfig, target_config: Path, lockfile: Path) -> bool:
    written = destination.resolve()
    files = [config.input, config.output, config.emit_model_metadata, target_config, lockfile]
    roots = [target.output, config.output]
    return any(path is not None and written == Path(str(path)).resolve() for path in files) or any(
        written.is_relative_to(root.resolve()) for root in roots if root is not None
    )


def _conflict(message: str) -> Diagnostic:
    return Diagnostic(code="E_CONFIG_CONFLICT", severity="error", stage="config", message=message)


class _Report:
    """Print diagnostics to stderr as they arrive and write them as JSON when asked to."""

    def __init__(self, destination: str | None) -> None:
        self.destination = destination
        self.diagnostics: list[Diagnostic] = []

    def extend(self, diagnostics: Iterable[Diagnostic]) -> None:
        for diagnostic in diagnostics:
            self.diagnostics.append(diagnostic)
            location = diagnostic.option_path or diagnostic.source_pointer or diagnostic.artifact_path
            where = "" if location is None else f" {location}"
            print(  # noqa: T201
                f"{diagnostic.code} {diagnostic.severity} {diagnostic.stage}{where}: {diagnostic.message}",
                file=sys.stderr,
            )

    def failure(self, error: Exception) -> None:
        if (diagnostic := attached_diagnostic(error)) is None:
            traceback.print_exception(error, file=sys.stderr)
            message = f"{type(error).__name__}: {error}"
            diagnostic = Diagnostic(code="E_GENERATION_FAILURE", severity="error", stage="target", message=message)
        self.extend((diagnostic,))

    def write(self) -> None:
        if (destination := self.destination) is None:
            return
        document: JSONValue = {
            "schema_version": 1,
            "target": _TARGET,
            "diagnostics": [_json(diagnostic) for diagnostic in self.diagnostics],
        }
        text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        if destination == "-":
            sys.stdout.write(text)
        else:
            Path(destination).write_text(text, encoding="utf-8")


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
