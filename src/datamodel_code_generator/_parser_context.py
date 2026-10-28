"""Neutral parser construction contexts shared with entry points."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from datamodel_code_generator.enums import HTTPBackend

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from datamodel_code_generator._shared_types import DefaultPutDict
    from datamodel_code_generator._source import DirectoryInputFilter


@dataclass(frozen=True, slots=True)
class ParserSourceContext:
    """Immutable source and reference policy shared by parser entry points."""

    base_path: Path | None = None
    encoding: str = "utf-8"
    directory_input_filter: DirectoryInputFilter | None = None
    remote_text_cache: DefaultPutDict[str, str] | None = None
    allow_remote_refs: bool | None = None
    strict_refs: bool = False
    allow_private_network: bool = False
    http_backend: HTTPBackend = HTTPBackend.AUTO
    http_headers: Sequence[tuple[str, str]] | None = None
    http_local_ref_path: Path | None = None
    http_ignore_tls: bool = False
    http_query_parameters: Sequence[tuple[str, str]] | None = None
    http_timeout: float | None = None
    external_ref_mapping: Mapping[str, str] | None = None
    remote_response_observer: (
        Callable[[str, Sequence[tuple[str, str]] | None, Sequence[tuple[str, str]] | None, bytes], None] | None
    ) = None


__all__ = ["ParserSourceContext"]
