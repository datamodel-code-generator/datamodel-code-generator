"""Public records, errors, selections, and codec registrations of the generation targets.

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
    OperationSelection,
    PublicationRollbackError,
)
from datamodel_code_generator._codec_declarations import (
    CodecAdapterRegistration,
    OperationRef,
    SchemaDirectionalUse,
    SchemaRef,
    TypeUseRef,
)
from datamodel_code_generator._runtime.model_codecs.capabilities import (
    CodecCapabilities,
    ParameterCodecCapabilities,
    SchemaCodecCapabilities,
)

__all__ = [
    "APIGenerationError",
    "ArtifactRecord",
    "CodecAdapterRegistration",
    "CodecCapabilities",
    "Diagnostic",
    "GeneratedArtifact",
    "GeneratedProject",
    "GenerationReport",
    "OperationRef",
    "OperationSelection",
    "ParameterCodecCapabilities",
    "PublicationRollbackError",
    "SchemaCodecCapabilities",
    "SchemaDirectionalUse",
    "SchemaRef",
    "TypeUseRef",
]
