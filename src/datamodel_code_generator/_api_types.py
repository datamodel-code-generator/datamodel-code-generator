"""Shared records, diagnostics, and errors of the single-target API generators.

The target entry points expose these records through `datamodel_code_generator.api_types`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path  # ruff: ignore[typing-only-standard-library-import] - Public annotations support get_type_hints().
from typing import Literal, TypeAlias

from datamodel_code_generator import Error

__all__ = [
    "APIGenerationError",
    "ArtifactAction",
    "ArtifactKind",
    "ArtifactRecord",
    "Diagnostic",
    "DiagnosticSeverity",
    "DiagnosticStage",
    "GeneratedArtifact",
    "GeneratedProject",
    "GenerationReport",
    "OperationRef",
    "OperationSelector",
    "PublicationRollbackError",
    "SchemaRef",
    "TargetKind",
]

TargetKind: TypeAlias = Literal["fastapi", "client"]
DiagnosticSeverity: TypeAlias = Literal["error", "warning", "info"]
DiagnosticStage: TypeAlias = Literal[
    "config", "input", "model", "binding", "target", "ownership", "format", "publication"
]
ArtifactKind: TypeAlias = Literal["model", "model_metadata", "remote_lock", "target", "target_manifest"]
ArtifactAction: TypeAlias = Literal["write", "delete", "unchanged"]


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


class PublicationRollbackError(OSError):
    """Report a failed publication whose rollback could not restore every destination.

    The original failure is the ``__cause__``. Backups of destinations that were not restored stay in place.
    """

    def __init__(self, unrestored: tuple[Path, ...], backups: tuple[Path, ...]) -> None:
        """Keep the destinations that were not restored and the backups left for recovery."""
        self.unrestored = unrestored
        self.backups = backups
        super().__init__(f"Publication rollback failed for {', '.join(path.as_posix() for path in unrestored)}")


@dataclass(frozen=True, slots=True, kw_only=True)
class Diagnostic:
    """Describe one ordered generation finding without secrets or live objects."""

    code: str
    severity: DiagnosticSeverity
    stage: DiagnosticStage
    message: str
    source_uri: str | None = None
    source_pointer: str | None = None
    operation: OperationRef | None = None
    option_path: str | None = None
    artifact_path: str | None = None
    target_id: str | None = None


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
        if path in message:
            message = message.replace(path, option, 1)
        else:
            location = option
    return message if location is None else f"{location}: {message}"


@dataclass(frozen=True, slots=True, kw_only=True)
class ArtifactRecord:
    """Describe one published or compared file without its content."""

    path: Path
    kind: ArtifactKind
    sha256: str
    size: int
    target_id: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class GeneratedArtifact:
    """Carry one publication candidate; deletions carry no content."""

    path: Path
    kind: ArtifactKind
    action: ArtifactAction
    content: bytes | None
    sha256: str | None
    target_id: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerationReport:
    """Summarize one publishing generation of a single target.

    dependencies are the requirement specifiers the generated package needs at run time.
    """

    target: TargetKind
    written_files: tuple[ArtifactRecord, ...]
    unchanged_files: tuple[ArtifactRecord, ...]
    deleted_files: tuple[ArtifactRecord, ...]
    generator_version: str
    runtime_revision: str
    dependencies: tuple[str, ...] = ()
    schema_version: Literal[1] = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class GeneratedProject:
    """Return every publication candidate of one target without writing it.

    dependencies are the requirement specifiers the generated package needs at run time.
    """

    target: TargetKind
    artifacts: tuple[GeneratedArtifact, ...]
    generator_version: str
    runtime_revision: str
    dependencies: tuple[str, ...] = ()
    schema_version: Literal[1] = 1
