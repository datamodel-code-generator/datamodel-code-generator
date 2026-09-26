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
)
from datamodel_code_generator._codec_declarations import (
    BuiltinCodecCompatibility,
    CodecAdapterRegistration,
    ModelExportBinding,
    OperationRef,
    SchemaDirectionalUse,
    SchemaRef,
    TypeUseRef,
)
from datamodel_code_generator._publication import PublicationRollbackError
from datamodel_code_generator._runtime.model_codecs.capabilities import (
    ClientMediaCodecCapabilities,
    CodecCapabilities,
    ParameterCodecCapabilities,
    SchemaCodecCapabilities,
    ServerMediaCodecCapabilities,
)

__all__ = [
    "APIGenerationError",
    "ArtifactRecord",
    "BuiltinCodecCompatibility",
    "ClientMediaCodecCapabilities",
    "CodecAdapterRegistration",
    "CodecCapabilities",
    "Diagnostic",
    "GeneratedArtifact",
    "GeneratedProject",
    "GenerationReport",
    "ModelExportBinding",
    "OperationRef",
    "OperationSelection",
    "ParameterCodecCapabilities",
    "PublicationRollbackError",
    "SchemaCodecCapabilities",
    "SchemaDirectionalUse",
    "SchemaRef",
    "ServerMediaCodecCapabilities",
    "TypeUseRef",
]
