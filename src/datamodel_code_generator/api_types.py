"""Public records and errors of the generation targets.

The FastAPI server target re-exports the same objects from `datamodel_code_generator.fastapi`.
"""

from __future__ import annotations

from datamodel_code_generator._api_types import (
    APIGenerationError,
    ArtifactRecord,
    Diagnostic,
    GeneratedArtifact,
    GeneratedProject,
    GenerationReport,
    OperationRef,
    PublicationRollbackError,
    SchemaRef,
)

__all__ = [
    "APIGenerationError",
    "ArtifactRecord",
    "Diagnostic",
    "GeneratedArtifact",
    "GeneratedProject",
    "GenerationReport",
    "OperationRef",
    "PublicationRollbackError",
    "SchemaRef",
]
