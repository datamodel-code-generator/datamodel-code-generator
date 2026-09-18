"""Shared media syntax and OpenAPI encoding applicability decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, TypeAlias

MediaOwner: TypeAlias = Literal["request_body", "response", "parameter", "header"]
IgnoredEncodingReason: TypeAlias = Literal["oas_encoding_request_body_only", "oas_encoding_media_not_applicable"]

_HTTP_WORD = r"[!#$%&'*+.^_`|~0-9a-zA-Z-]+"
_MEDIA = re.compile(rf"[ \t]*(?P<type>{_HTTP_WORD})/(?P<subtype>{_HTTP_WORD})[ \t]*")
_PARAMETER = re.compile(
    rf";[ \t]*(?P<name>{_HTTP_WORD})=(?P<value>{_HTTP_WORD}|"
    r'"(?:[\t !#-\[\]-~\x80-\xff]|\\[\t -~\x80-\xff])*")[ \t]*'
)


@dataclass(frozen=True, slots=True)
class MediaType:
    """Retain normalized type tokens and ordered, unescaped parameter values."""

    type: str
    subtype: str
    parameters: tuple[tuple[str, str], ...]


def parse_media_type(value: str) -> MediaType:
    """Parse HTTP media syntax once for generation and shared wire decisions."""
    if (match := _MEDIA.match(value)) is None:
        msg = "Invalid media type"
        raise ValueError(msg)
    position = match.end()
    parameters: list[tuple[str, str]] = []
    while position < len(value):
        if (parameter := _PARAMETER.match(value, position)) is None:
            msg = "Invalid media type parameter"
            raise ValueError(msg)
        raw_value = parameter["value"]
        decoded = re.sub(r"\\(.)", r"\1", raw_value[1:-1]) if raw_value.startswith('"') else raw_value
        parameters.append((parameter["name"].lower(), decoded))
        position = parameter.end()
    return MediaType(match["type"].lower(), match["subtype"].lower(), tuple(parameters))


def encoding_media(value: str, owner: MediaOwner, *, openapi_32: bool) -> MediaType | IgnoredEncodingReason:
    """Apply version/location before media, without inspecting ignored children."""
    if not openapi_32 and owner != "request_body":
        return "oas_encoding_request_body_only"
    media = parse_media_type(value)
    if media.type != "multipart" and (media.type, media.subtype) != ("application", "x-www-form-urlencoded"):
        return "oas_encoding_media_not_applicable"
    return media
