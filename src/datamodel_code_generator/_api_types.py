"""Shared records, diagnostics, and errors of the single-target API generators.

generate() and the command line reach the targets through these records; failures reach callers as ordinary
`datamodel_code_generator.Error`s.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path  # ruff: ignore[typing-only-standard-library-import] - Public annotations support get_type_hints().
from typing import Literal, TypeAlias

from datamodel_code_generator import DocumentationAnnotationWarning, Error

__all__ = [
    "APIGenerationError",
    "ArtifactAction",
    "ArtifactKind",
    "Diagnostic",
    "DiagnosticSeverity",
    "DiagnosticStage",
    "DocumentationAnnotationWarning",
    "GeneratedArtifact",
    "GeneratedProject",
    "Implementation",
    "NextSteps",
    "OperationRef",
    "OperationSelector",
    "SchemaRef",
    "TargetKind",
]

TargetKind: TypeAlias = Literal["fastapi", "client"]
DiagnosticSeverity: TypeAlias = Literal["error", "warning", "info"]
DiagnosticStage: TypeAlias = Literal["config", "input", "model", "binding", "target", "format", "publication"]
ArtifactKind: TypeAlias = Literal["model", "model_metadata", "remote_lock", "target", "implementation"]
ArtifactAction: TypeAlias = Literal["write", "unchanged", "keep"]


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationRef:
    """Select a root operation use site by RFC 6901 pointer, in the root document unless another is named."""

    pointer: str
    document: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaRef:
    """Select a processed schema occurrence by RFC 6901 pointer, in the root document unless another is named."""

    pointer: str
    document: str | None = None


OperationSelector: TypeAlias = OperationRef | str


@dataclass(frozen=True, slots=True, kw_only=True)
class Diagnostic:
    """Describe one ordered generation finding without secrets or live objects."""

    code: str
    severity: DiagnosticSeverity
    stage: DiagnosticStage
    message: str
    source_pointer: str | None = None
    operation: OperationRef | None = None
    option_path: str | None = None
    artifact_path: str | None = None


class APIGenerationError(Error):
    """Report target failures as ordinary dcg errors, retaining internal findings in phase order."""

    def __init__(self, diagnostics: tuple[Diagnostic, ...], *, option_prefix: str | None = None) -> None:
        """Keep the internal findings and describe errors without diagnostic codes or stages."""
        self.diagnostics = diagnostics
        super().__init__("; ".join(_error_message(item, option_prefix) for item in diagnostics))


def _error_message(item: Diagnostic, option_prefix: str | None) -> str:
    message = item.message
    location = item.source_pointer or item.artifact_path
    if (path := item.option_path) is not None:
        if path.startswith("model_config."):
            name = path.removeprefix("model_config.")
            prefix = ""
        else:
            name = path
            prefix = None if option_prefix is None else f"{option_prefix}-"
        field = name.split(".", 1)[0].split("[", 1)[0]
        option = name if prefix is None else f"--{prefix}{field.replace('_', '-')}{name[len(field) :]}"
        holder, _, member = name.rpartition(".")
        if path in message:
            message = message.replace(path, option, 1)
        elif holder and member.isidentifier() and holder in message:
            message = message.replace(holder, option.removesuffix(f".{member}"), 1)
        else:
            location = option
    return message if location is None else f"{location}: {message}"


@dataclass(frozen=True, slots=True, kw_only=True)
class GeneratedArtifact:
    """Carry one generated file: its bytes, and whether writing them changes the file at its path.

    An `implementation` is a module of the user's that generation writes with stubs only while it is missing; one that
    exists is kept, without content, but for the initializer of a `services` package another server package left,
    whose docstring generation rewrites to name the server package that takes the package over, as the project's
    `next_steps.taken_over` says.
    """

    path: Path
    kind: ArtifactKind
    action: ArtifactAction
    content: bytes


@dataclass(frozen=True, slots=True, kw_only=True)
class Implementation:
    """A module of the user's that a generated entrypoint imports, and the class or function it imports from it.

    `created` says whether the generation writes the module with stubs, which it does only while the file is missing.
    """

    module: str
    name: str
    path: Path
    created: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class NextSteps:
    """What to do with a generated package: its README, and a server's entrypoint and implementations or a client's use.

    `app` is the import string of a server's ASGI application, defined in the `entrypoint` module, and `usage` is code
    that calls a client. `taken_over` names the server package whose `services` package a server takes over: the run
    rewrites the docstring of the package's initializer, an `implementation` it writes although the file exists.
    """

    readme: Path
    app: str | None = None
    entrypoint: Path | None = None
    implementations: tuple[Implementation, ...] = ()
    usage: str | None = None
    taken_over: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class GeneratedProject:
    """Return every generated file of one target without writing it.

    dependencies are the requirement specifiers the generated package needs at run time, and next_steps says what to
    do with the package.
    """

    target: TargetKind
    artifacts: tuple[GeneratedArtifact, ...]
    next_steps: NextSteps
    dependencies: tuple[str, ...] = ()
    schema_version: Literal[1] = 1
