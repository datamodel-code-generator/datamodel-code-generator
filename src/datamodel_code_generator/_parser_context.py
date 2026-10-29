"""Neutral parser construction contexts shared with entry points."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from datamodel_code_generator.enums import HTTPBackend

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from datamodel_code_generator._shared_types import DefaultPutDict, ResponseObserver
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
    remote_response_observer: ResponseObserver | None = None

    @classmethod
    def of(
        cls,
        options: Mapping[str, Any] | object,
        remote_lock: Any,
        directory_input_filter: DirectoryInputFilter | None = None,
    ) -> ParserSourceContext:
        """Build the policy from a mapping or a config of option values; an option a mapping lacks keeps its default.

        A remote lock records every response the parser fetches.
        """
        values = (
            options if isinstance(options, Mapping) else {name: getattr(options, name) for name in _SOURCE_OPTION_NAMES}
        )
        return cls(
            **{name: values[name] for name in _SOURCE_OPTION_NAMES if name in values},
            directory_input_filter=directory_input_filter,
            remote_response_observer=remote_lock.record_response if remote_lock is not None else None,
        )


_SOURCE_OPTION_NAMES = (
    "base_path",
    "encoding",
    "remote_text_cache",
    "allow_remote_refs",
    "strict_refs",
    "allow_private_network",
    "http_backend",
    "http_headers",
    "http_local_ref_path",
    "http_ignore_tls",
    "http_query_parameters",
    "http_timeout",
    "external_ref_mapping",
)


__all__ = ["ParserSourceContext"]
