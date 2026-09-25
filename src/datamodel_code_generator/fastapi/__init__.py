"""Generate a FastAPI server package and its models from one OpenAPI document.

The server target is experimental: its entry points, settings, and generated packages may change.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeAlias
from urllib.parse import ParseResult

from datamodel_code_generator._fastapi.config import FastAPIConfig, ResponseChoice
from datamodel_code_generator._fastapi.context import HookReference
from datamodel_code_generator.api_types import (
    APIGenerationError,
    ArtifactRecord,
    BuiltinCodecCompatibility,
    ClientMediaCodecCapabilities,
    CodecAdapterRegistration,
    CodecCapabilities,
    Diagnostic,
    GeneratedArtifact,
    GeneratedProject,
    GenerationReport,
    ModelExportBinding,
    OperationRef,
    OperationSelection,
    ParameterCodecCapabilities,
    PublicationRollbackError,
    SchemaCodecCapabilities,
    SchemaDirectionalUse,
    SchemaRef,
    ServerMediaCodecCapabilities,
    TypeUseRef,
)
from datamodel_code_generator.config import GenerateConfig  # noqa: TC001 - Public annotations support get_type_hints().

GenerationInput: TypeAlias = Path | str | ParseResult | Mapping[str, Any]


def generate_fastapi(
    input_: GenerationInput, *, model_config: GenerateConfig, config: FastAPIConfig
) -> GenerationReport:
    """Generate the models and the server package once, then publish every change together."""
    from datamodel_code_generator._api_generation import generate_target  # noqa: PLC0415
    from datamodel_code_generator._fastapi.target import FastAPITarget  # noqa: PLC0415

    return generate_target(input_, model_config=model_config, config=config, generator=FastAPITarget())


def render_fastapi(input_: GenerationInput, *, model_config: GenerateConfig, config: FastAPIConfig) -> GeneratedProject:
    """Render the models and the server package once, returning every artifact without writing it."""
    from datamodel_code_generator._api_generation import render_target  # noqa: PLC0415
    from datamodel_code_generator._fastapi.target import FastAPITarget  # noqa: PLC0415

    return render_target(input_, model_config=model_config, config=config, generator=FastAPITarget())


__all__ = [
    "APIGenerationError",
    "ArtifactRecord",
    "BuiltinCodecCompatibility",
    "ClientMediaCodecCapabilities",
    "CodecAdapterRegistration",
    "CodecCapabilities",
    "Diagnostic",
    "FastAPIConfig",
    "GeneratedArtifact",
    "GeneratedProject",
    "GenerationInput",
    "GenerationReport",
    "HookReference",
    "ModelExportBinding",
    "OperationRef",
    "OperationSelection",
    "ParameterCodecCapabilities",
    "PublicationRollbackError",
    "ResponseChoice",
    "SchemaCodecCapabilities",
    "SchemaDirectionalUse",
    "SchemaRef",
    "ServerMediaCodecCapabilities",
    "TypeUseRef",
    "generate_fastapi",
    "render_fastapi",
]
