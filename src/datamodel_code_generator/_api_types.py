"""Shared records, diagnostics, and errors of the single-target API generators.

The target entry points will expose these records through `datamodel_code_generator.api_types`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path  # noqa: TC003 - Public annotations support get_type_hints().
from typing import Literal, TypeAlias

from datamodel_code_generator._codec_declarations import OperationRef
from datamodel_code_generator._publication import PublicationRollbackError

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
    "OperationSelection",
    "OperationSelector",
    "PublicationRollbackError",
    "TargetKind",
]

TargetKind: TypeAlias = Literal["fastapi", "client"]
DiagnosticSeverity: TypeAlias = Literal["error", "warning", "info"]
DiagnosticStage: TypeAlias = Literal[
    "config", "input", "model", "binding", "selection", "hook", "target", "verify", "ownership", "format", "publication"
]
ArtifactKind: TypeAlias = Literal[
    "model", "model_metadata", "remote_lock", "target", "model_inventory", "target_manifest"
]
ArtifactAction: TypeAlias = Literal["write", "delete", "unchanged"]
OperationSelector: TypeAlias = OperationRef | str


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


class APIGenerationError(Exception):
    """Report configuration, binding, target, or ownership failures in phase order."""

    def __init__(self, diagnostics: tuple[Diagnostic, ...]) -> None:
        """Keep the ordered diagnostics and summarize them in the message."""
        self.diagnostics = diagnostics
        super().__init__("; ".join(f"{item.code}: {item.message}" for item in diagnostics))


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationSelection:
    """Select root path operations by reference or tag, then subtract exclusions."""

    include_operations: tuple[OperationSelector, ...] = ()
    include_tags: tuple[str, ...] = ()
    exclude_operations: tuple[OperationSelector, ...] = ()
    exclude_tags: tuple[str, ...] = ()
    reason: str | None = None


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
    """Summarize one publishing generation of a single target."""

    target: TargetKind
    written_files: tuple[ArtifactRecord, ...]
    unchanged_files: tuple[ArtifactRecord, ...]
    deleted_files: tuple[ArtifactRecord, ...]
    diagnostics: tuple[Diagnostic, ...]
    generator_version: str
    runtime_revision: str
    schema_version: Literal[1] = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class GeneratedProject:
    """Return every publication candidate of one target without writing it."""

    target: TargetKind
    artifacts: tuple[GeneratedArtifact, ...]
    diagnostics: tuple[Diagnostic, ...]
    generator_version: str
    runtime_revision: str
    schema_version: Literal[1] = 1
