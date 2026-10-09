"""Generate an HTTPX2 client package and its models from one OpenAPI document.

The client target is experimental: its entry points, settings, and generated packages may change. The records of
`ProtocolConfiguration` load the first time one of them is used.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from pathlib import Path
from typing import Any, Final, TypeAlias
from urllib.parse import ParseResult

from datamodel_code_generator._client.config import (
    BodyFieldName,
    ClientGenerationConfig,
    ClientOperationConfig,
    IdempotencyMetadata,
    ParameterName,
    ResourceName,
    RuntimeOperationMetadata,
)
from datamodel_code_generator.api_types import (
    APIGenerationError,
    Diagnostic,
    DocumentationAnnotationWarning,
    GeneratedArtifact,
    GeneratedProject,
    OperationRef,
    SchemaRef,
)
from datamodel_code_generator.config import GenerateConfig  # noqa: TC001 - Public annotations support get_type_hints().

GenerationInput: TypeAlias = Path | str | ParseResult | Mapping[str, Any]

_PROTOCOL_RECORDS: Final = (
    "AdapterSignature",
    "Binding",
    "BodyHmacSignature",
    "CacheHelper",
    "CountContinuation",
    "CursorContinuation",
    "EndCondition",
    "EventDiscriminator",
    "EventMapping",
    "ImmediateResult",
    "InlineResult",
    "LengthCompletion",
    "LinkContinuation",
    "LiteralValue",
    "NextUrlContinuation",
    "NoResult",
    "NoSignature",
    "OperationCompletion",
    "OperationResult",
    "PaginationHelper",
    "PollInterval",
    "PollingHelper",
    "ProtocolConfiguration",
    "PublicKeySignature",
    "RemoteCancel",
    "ResumableUploadHelper",
    "SourceValue",
    "StandardWebhooksSignature",
    "StreamCompletion",
    "StreamHelper",
    "StreamResume",
    "StripeStyleSignature",
    "UploadAbort",
    "UploadAppend",
    "UploadChecksum",
    "UploadCreate",
    "UploadProbe",
    "WebSocketHelper",
    "WebSocketMessage",
    "WebhookHelper",
)
_REQUEST_RECORDS: Final = (
    "BodySelector",
    "BodyTarget",
    "HeaderSelector",
    "ParameterTarget",
    "QuerystringTarget",
    "StatusSelector",
)
_LAZY: Final = {
    **dict.fromkeys(_PROTOCOL_RECORDS, "datamodel_code_generator._client.protocols"),
    **dict.fromkeys(_REQUEST_RECORDS, "datamodel_code_generator._runtime.protocols.records"),
}


def generate_client(input_: GenerationInput, *, model_config: GenerateConfig, config: ClientGenerationConfig) -> None:
    """Generate the models and the client package once, then publish every change together."""
    from datamodel_code_generator._api_generation import generate_target  # noqa: PLC0415
    from datamodel_code_generator._client.target import ClientTarget  # noqa: PLC0415

    generate_target(input_, model_config=model_config, config=config, generator=ClientTarget())


def render_client(
    input_: GenerationInput, *, model_config: GenerateConfig, config: ClientGenerationConfig
) -> GeneratedProject:
    """Render the models and the client package once, returning every artifact without writing it."""
    from datamodel_code_generator._api_generation import render_target  # noqa: PLC0415
    from datamodel_code_generator._client.target import ClientTarget  # noqa: PLC0415

    return render_target(input_, model_config=model_config, config=config, generator=ClientTarget())


def __getattr__(name: str) -> Any:
    """Load a helper or request record the first time it is used."""
    if (module := _LAZY.get(name)) is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    return getattr(import_module(module), name)


__all__ = [  # noqa: PLE0604
    "APIGenerationError",
    "BodyFieldName",
    "ClientGenerationConfig",
    "ClientOperationConfig",
    "Diagnostic",
    "DocumentationAnnotationWarning",
    "GeneratedArtifact",
    "GeneratedProject",
    "GenerationInput",
    "IdempotencyMetadata",
    "OperationRef",
    "ParameterName",
    "ResourceName",
    "RuntimeOperationMetadata",
    "SchemaRef",
    "generate_client",
    "render_client",
    *_LAZY,
]
