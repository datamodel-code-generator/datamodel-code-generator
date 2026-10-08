"""Public records, errors, and warnings of the generation targets.

The FastAPI server target re-exports the same objects from `datamodel_code_generator.fastapi`.
"""

from __future__ import annotations

from datamodel_code_generator._api_types import (
    APIGenerationError,
    Diagnostic,
    DocumentationAnnotationWarning,
    GeneratedArtifact,
    GeneratedProject,
    OperationRef,
    PublicationRollbackError,
    SchemaRef,
    TargetEditWarning,
    TargetStateWarning,
)

__all__ = [
    "APIGenerationError",
    "Diagnostic",
    "DocumentationAnnotationWarning",
    "GeneratedArtifact",
    "GeneratedProject",
    "OperationRef",
    "PublicationRollbackError",
    "SchemaRef",
    "TargetEditWarning",
    "TargetStateWarning",
]
