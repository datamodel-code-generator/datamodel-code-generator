"""Public records, errors, and warnings of the generation targets.

The FastAPI server and HTTPX2 client targets re-export the same objects from `datamodel_code_generator.fastapi`
and `datamodel_code_generator.client`.
"""

from __future__ import annotations

from datamodel_code_generator._api_types import (
    APIGenerationError,
    Diagnostic,
    DocumentationAnnotationWarning,
    GeneratedArtifact,
    GeneratedProject,
    OperationRef,
    SchemaRef,
)

__all__ = [
    "APIGenerationError",
    "Diagnostic",
    "DocumentationAnnotationWarning",
    "GeneratedArtifact",
    "GeneratedProject",
    "OperationRef",
    "SchemaRef",
]
