"""Shared media syntax and OpenAPI encoding applicability decisions."""

from __future__ import annotations

from typing import Literal, TypeAlias

from typing_extensions import final

from datamodel_code_generator._records import record

MediaOwner: TypeAlias = Literal["request_body", "response", "parameter", "header"]
IgnoredEncodingReason: TypeAlias = Literal["oas_encoding_request_body_only", "oas_encoding_media_not_applicable"]

_ERRORS = ("Invalid media type", "Invalid media type parameter")


@final
@record
class MediaType:
    """Retain normalized type tokens and ordered, unescaped parameter values."""

    type: str
    subtype: str
    parameters: tuple[tuple[str, str], ...]


def parse_media_type(value: str) -> MediaType:
    """Parse HTTP media syntax once for generation and shared wire decisions."""
    from datamodel_code_generator._runtime.model_codecs.media import (  # ruff: ignore[import-outside-top-level]
        media_parts,
        unquoted,
    )

    kind, subtype, parameters = media_parts(value, _ERRORS)
    return MediaType(
        kind,
        subtype,
        tuple((name, unquoted(text)) for name, text in parameters),
    )


def encoding_applies(owner: MediaOwner, *, openapi_32: bool) -> bool:
    """Return whether an encoding under ``owner`` applies in the OpenAPI version, before its media type is read."""
    return openapi_32 or owner == "request_body"


def encoding_media(value: str, owner: MediaOwner, *, openapi_32: bool) -> MediaType | IgnoredEncodingReason:
    """Apply version/location before media, without inspecting ignored children."""
    if not encoding_applies(owner, openapi_32=openapi_32):
        return "oas_encoding_request_body_only"
    media = parse_media_type(value)
    if media.type != "multipart" and (media.type, media.subtype) != ("application", "x-www-form-urlencoded"):
        return "oas_encoding_media_not_applicable"
    return media
