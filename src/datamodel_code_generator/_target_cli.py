"""Run the generation target a command line selects: publish or check it, and report its diagnostics."""

from __future__ import annotations

import json
import re
import sys
import traceback
from collections.abc import Mapping
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NoReturn

from typing_extensions import TypeIs

from datamodel_code_generator import Error, InvalidClassNameError
from datamodel_code_generator._api_manifest import document_identity
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, OperationRef

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Iterable, Sequence

    from datamodel_code_generator._api_types import OperationSelector
    from datamodel_code_generator._client.config import ClientGenerationConfig
    from datamodel_code_generator._client.target import ClientTarget
    from datamodel_code_generator._fastapi.config import FastAPIConfig
    from datamodel_code_generator._fastapi.target import FastAPITarget
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue
    from datamodel_code_generator._target_config import TargetConfig

_OK: Final = 0
_DIFF: Final = 1
_ERROR: Final = 2
_JOBS: Final = (("job", "--job"), ("all_jobs", "--all-jobs"))
_CONFLICTS: Final = (("watch", "--watch"), ("diff_against", "--diff-against"), ("input_model", "--input-model"))
_SERVER_SETTINGS: Final = ("layout", "handler_mode", "include_request", "body_mode", "router_names")
_OPERATION_SETTINGS: Final = ("handler_modes", "body_modes", "operation_names", "parameter_names")
_INDEXED: Final = re.compile(r"(operations|resource_names)\[(\d+)\](?:\.(parameter_names|body_field_names)\[(\d+)\])?")
_CLIENT_SETTINGS: Final = ("signature_style", "body_arguments", "default_base_url", "server_base_url")
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


def run_target(
    args: Sequence[str], namespace: Namespace, config: Any, pyproject_path: Path | None, *, job: bool
) -> int:
    """Generate or check the target the finalized CLI config selects, reporting every diagnostic.

    A model setting the target needs but the config lacks is refused like a model option conflict, before the run.
    A job cannot select a target until the job runner stages target packages, so a job that does is refused.
    """
    from datamodel_code_generator._api_generation import model_requirement  # noqa: PLC0415

    selector, name = _selected(config)
    if not job and (requirement := model_requirement(config.openapi_scopes, selector)):
        print(f"Error: {requirement}", file=sys.stderr)  # noqa: T201
        return _ERROR
    report = _Report(vars(namespace).get("diagnostics_json"), name)
    try:
        code = (
            _jobs(namespace, pyproject_path, report, selector)
            if job
            else _run(args, namespace, config, pyproject_path, report)
        )
    except Exception as error:  # noqa: BLE001
        report.failure(_keyed(error, config), encoding=config.encoding)
        code = _ERROR
    return code if report.write() else _ERROR


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


def _selected(config: Any) -> tuple[str, str]:
    """Return the option that selects the run's target and the target's name."""
    if config.generate_client is None:
        return "--generate-server", config.generate_server
    return "--generate-client", config.generate_client


def _run(args: Sequence[str], namespace: Namespace, config: Any, pyproject_path: Path | None, report: _Report) -> int:
    from datamodel_code_generator.__main__ import (  # noqa: PLC0415
        _target_lockfile,  # pyright: ignore[reportPrivateUsage, reportUnknownVariableType]
        _target_settings,  # pyright: ignore[reportPrivateUsage, reportUnknownVariableType]
    )
    from datamodel_code_generator._api_generation import generate_target, prepare_target, render_target  # noqa: PLC0415

    server = config.generate_client is None
    lockfile = _target_lockfile(config, pyproject_path)
    report.guard(
        (pyproject_path, config.input, config.output, config.emit_model_metadata, lockfile),
        (config.server_output if server else config.client_output, config.output),
    )
    if flags := _flags(namespace, config, _CONFLICTS):
        raise _refused(flags, _selected(config)[0])
    if (form := vars(namespace).get("dependency_format")) is not None and report.destination == "-":
        raise APIGenerationError((_conflict("--dependency-format cannot be used with --diagnostics-json -"),))
    generator, target = (_server if server else _client)(config, namespace, pyproject_path)
    effective = _target_settings(config, args, lockfile)
    if (source := config.url or config.input) is None:
        prepare_target("", effective, generator)
        source = sys.stdin.read()
    if config.check:
        project = render_target(source, model_config=effective, config=target, generator=generator)
        report.extend(project.diagnostics)
        changes = [artifact for artifact in project.artifacts if artifact.action != "unchanged"]
        for artifact in changes:
            print(f"{artifact.action} {_shown(artifact.path)}", file=sys.stderr)  # noqa: T201
        return _DIFF if changes else _OK
    generated = generate_target(source, model_config=effective, config=target, generator=generator)
    report.extend(generated.diagnostics)
    if report.destination != "-":
        print(_next_step(target, generated.dependencies, form))  # noqa: T201
    return _OK


def _shown(path: Path) -> str:
    """Return a path relative to the working directory when it lies inside it."""
    cwd = Path.cwd()
    return (path.relative_to(cwd) if path.is_relative_to(cwd) else path).as_posix()


def _next_step(target: TargetConfig, dependencies: tuple[str, ...], form: str | None) -> str:
    """Return what adds the runtime dependencies of a generated package to a project: a uv command or requirements."""
    if form == "requirements":
        return "\n".join(dependencies)
    arguments = " ".join(f'"{dependency}"' for dependency in dependencies)
    return f"Add the runtime dependencies of {target.package} to your project:\n  uv add {arguments}"


def _jobs(namespace: Namespace, pyproject_path: Path | None, report: _Report, selector: str) -> NoReturn:
    report.guard((pyproject_path,), ())
    raise _refused(_flags(namespace, namespace, _JOBS), selector)


def _flags(namespace: Namespace, source: object, options: tuple[tuple[str, str], ...]) -> list[str]:
    flags = [flag for name, flag in options if getattr(source, name)]
    return [*flags, "--output-format json"] if namespace.output_format == "json" else flags


def _refused(flags: list[str], selector: str) -> APIGenerationError:
    return APIGenerationError(tuple(_conflict(f"{selector} cannot be used with {flag}") for flag in flags))


def _base(namespace: Namespace, pyproject_path: Path | None, field: str) -> Path:
    """Return the directory a setting's documents resolve against: pyproject.toml's for its keys, else the cwd."""
    return Path.cwd() if pyproject_path is None or getattr(namespace, field) is not None else pyproject_path.parent


def _server(config: Any, namespace: Namespace, pyproject_path: Path | None) -> tuple[FastAPITarget, FastAPIConfig]:
    """Map the --server-* settings onto the server configuration, leaving unset ones at their defaults.

    Documents named in operation references resolve against the pyproject.toml directory for settings read from it,
    and against the working directory for settings given on the command line.
    """
    from datamodel_code_generator._fastapi.config import FastAPIConfig, ResponseChoice  # noqa: PLC0415
    from datamodel_code_generator._fastapi.target import FastAPITarget  # noqa: PLC0415

    values: dict[str, Any] = {
        name: value for name in _SERVER_SETTINGS if (value := getattr(config, f"server_{name}")) is not None
    }
    for name in _OPERATION_SETTINGS:
        if (entries := getattr(config, field := f"server_{name}")) is not None:
            root = _base(namespace, pyproject_path, field)
            values[name] = {_operation(key, root): value for key, value in entries.items()}
    if (responses := config.server_primary_responses) is not None:
        root = _base(namespace, pyproject_path, "server_primary_responses")
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
        root = _base(namespace, pyproject_path, "client_operations")
        values["operations"] = operation_configs(entries, partial(_operation, base=root))
    return ClientTarget(_base(namespace, pyproject_path, "client_protocols")), ClientGenerationConfig(
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


def _is_report(path: Path, target: str) -> bool:
    try:
        document: object = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return False
    return (
        _is_mapping(document)
        and frozenset(document) == _REPORT
        and document["schema_version"] == 1
        and document["target"] == target
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
    """Print diagnostics to stderr as they arrive and write them as JSON when asked to."""

    def __init__(self, destination: str | None, target: str) -> None:
        self.destination = destination
        self.target = target
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
        if written.is_file() and not _is_report(written, self.target):
            self.destination = None
            raise APIGenerationError((_conflict("--diagnostics-json names an existing file that is not a report"),))

    def extend(self, diagnostics: Iterable[Diagnostic]) -> None:
        for diagnostic in diagnostics:
            self.diagnostics.append(diagnostic)
            location = diagnostic.option_path or diagnostic.source_pointer or diagnostic.artifact_path
            where = "" if location is None else f" {location}"
            print(  # noqa: T201
                f"{diagnostic.code} {diagnostic.severity} {diagnostic.stage}{where}: {diagnostic.message}",
                file=sys.stderr,
            )

    def failure(self, error: Exception, *, encoding: str = "utf-8") -> None:
        """Print ordinary target errors and preserve the model CLI's hints and unexpected-error traceback."""
        if isinstance(error, APIGenerationError):
            for diagnostic in error.diagnostics:
                if diagnostic.severity == "error":
                    self.diagnostics.append(diagnostic)
                else:
                    self.extend((diagnostic,))
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
            "target": self.target,
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
